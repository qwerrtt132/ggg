"""
tjr_complete_all_in_one_v13.py
==============================
Trading bot with ML integration, self-correction, and backtesting capabilities.
VERSION: v13.0
"""

# Fix 15: VERSION constant updated to v13.0
VERSION = "v13.0"

import heapq
import threading
import time
import os
import json
import logging
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
LEARNED_CONFIG_PATH = "learned_config.json"


def save_learned_config(config: "BotConfig") -> None:
    """Persist learned configuration to disk."""
    try:
        data = {
            "pattern_stats": config.pattern_stats,
            "symbol_stats": config.symbol_stats,
            "hour_stats": config.hour_stats,
        }
        with open(LEARNED_CONFIG_PATH, "w") as fh:
            json.dump(data, fh, indent=2)
    except OSError as exc:
        logger.warning("save_learned_config failed: %s", exc)


def load_learned_config(config: "BotConfig") -> None:
    """Load persisted configuration from disk."""
    if not os.path.exists(LEARNED_CONFIG_PATH):
        return
    try:
        with open(LEARNED_CONFIG_PATH) as fh:
            data = json.load(fh)
        config.pattern_stats.update(data.get("pattern_stats", {}))
        config.symbol_stats.update(data.get("symbol_stats", {}))
        config.hour_stats.update(data.get("hour_stats", {}))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("load_learned_config failed: %s", exc)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BotConfig:
    """Central configuration for the trading bot."""
    symbols: List[str] = field(default_factory=lambda: ["SPY", "QQQ", "AAPL"])
    risk_pct: float = 1.0
    max_open_trades: int = 3
    backtest_mode: bool = False
    backtest_step: int = 20
    require_ob_or_fvg: bool = True
    require_volume_confirmation: bool = True

    # Fix 7: regime override flag prevents SelfCorrector toggle loop
    _regime_vol_override: bool = False

    # Learned / persisted data
    pattern_stats: Dict[str, Any] = field(default_factory=dict)
    symbol_stats: Dict[str, Any] = field(default_factory=dict)
    hour_stats: Dict[str, Any] = field(default_factory=dict)

    # Regime
    current_regime: str = "trending"


# ---------------------------------------------------------------------------
# Trade dataclass
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    """Represents a single trade."""
    symbol: str
    direction: str          # "long" or "short"
    entry_price: float
    stop_loss: float
    take_profit1: float
    take_profit2: float
    size: float
    entry_time: float = field(default_factory=time.time)
    close_price: float = 0.0
    close_time: float = 0.0
    pnl: float = 0.0
    outcome: str = "open"   # "win", "loss", "open"
    bars_held: int = 0      # Fix 3: canonical field name (no underscore)
    confluence_score: float = 0.0
    fingerprint: str = ""
    # Fix 4: serialisable opposing_signal_count
    opposing_signal_count: int = 0
    ob_used: bool = False
    fvg_used: bool = False
    volume_confirmed: bool = False
    hour: int = 0
    pattern_key: str = ""


# ---------------------------------------------------------------------------
# DataHandler
# ---------------------------------------------------------------------------

class DataHandler:
    """
    Handles market data retrieval and caching.
    Supports both live (yfinance) and backtest modes.
    """

    def __init__(self, backtest_mode: bool = False) -> None:
        self.backtest_mode = backtest_mode
        self.lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        # Fix 14: full_cache protected by self.lock
        self.full_cache: Dict[str, Any] = {}
        self._backtest_cursor: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _download(self, symbol: str, period: str, interval: str) -> "pd.DataFrame":
        """Download data from yfinance, returning an empty DataFrame on error."""
        try:
            import yfinance as yf  # type: ignore
            df = yf.download(symbol, period=period, interval=interval, progress=False)
            return df
        except Exception as exc:  # noqa: BLE001
            logger.warning("Download failed %s/%s/%s: %s", symbol, period, interval, exc)
            try:
                import pandas as pd
                return pd.DataFrame()
            except ImportError:
                return None  # type: ignore

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, symbol: str, interval: str, period: str) -> Any:
        """Return OHLCV data, using cache when available."""
        key = f"{symbol}_{interval}_{period}"
        if key in self._cache:
            return self._cache[key]

        df = self._download(symbol, period, interval)
        self._cache[key] = df

        with self.lock:  # Fix 14: lock the write
            self.full_cache[key] = df

        return df

    def get_future_bars(self, symbol: str, interval: str, period: str,
                        from_index: int, count: int) -> Any:
        """Return a slice of cached data for backtesting."""
        key = f"{symbol}_{interval}_{period}"
        with self.lock:  # Fix 14: lock the read
            df = self.full_cache.get(key)
        if df is None:
            df = self.get(symbol, interval, period)
        try:
            return df.iloc[from_index: from_index + count]
        except Exception:
            return df


# ---------------------------------------------------------------------------
# Fix 1: Module-level _get_vix function — accessible from any class
# ---------------------------------------------------------------------------

def _get_vix(data: DataHandler) -> float:
    """
    Return the current VIX level.
    Falls back to 15.0 if data cannot be fetched.
    """
    try:
        df = data.get("^VIX", "1d", "5d")
        if df is not None and not getattr(df, "empty", True) and len(df) > 0:
            close_col = "Close"
            if hasattr(df, "columns") and close_col in df.columns:
                return float(df[close_col].iloc[-1])
    except Exception as exc:  # noqa: BLE001
        logger.debug("_get_vix fallback: %s", exc)
    return 15.0


# ---------------------------------------------------------------------------
# SignalFilter
# ---------------------------------------------------------------------------

class SignalFilter:
    """Deduplicates trade signals using fingerprints with LRU-style eviction."""

    MAX_SIZE = 5000
    EVICT_PCT = 0.20

    def __init__(self) -> None:
        self._seen: Dict[str, float] = {}  # fingerprint -> timestamp

    def is_new(self, fingerprint: str) -> bool:
        if fingerprint in self._seen:
            return False
        if len(self._seen) >= self.MAX_SIZE:
            # Evict oldest 20%
            n_evict = int(self.MAX_SIZE * self.EVICT_PCT)
            oldest = heapq.nsmallest(n_evict, self._seen, key=lambda k: self._seen[k])
            for k in oldest:
                del self._seen[k]
        self._seen[fingerprint] = time.time()
        return True


# ---------------------------------------------------------------------------
# MLBrain (simplified RL/ML integration)
# ---------------------------------------------------------------------------

class MLBrain:
    """Minimal ML brain for trade outcome learning."""

    def __init__(self) -> None:
        self._results: List[Dict] = []
        self._model_ready = False

    def add_trade_result(self, trade: Trade, rl_reward: float = 0.0) -> None:
        self._results.append({"trade": asdict(trade), "rl_reward": rl_reward})
        if len(self._results) >= 10:
            self._maybe_train()

    def compute_rl_reward(self, pnl: float, bars_held: int,
                          win: bool, confluence: float) -> float:
        base = pnl / max(1, bars_held)
        if win:
            base *= 1.0 + (confluence * 0.1)
        return float(base)

    def predict_score(self, features: Dict) -> float:
        """Returns a score in [0, 1]."""
        if not self._model_ready:
            return 0.5
        # Placeholder: real implementation would use a trained model
        return min(1.0, max(0.0, features.get("confluence", 0.5)))

    def _maybe_train(self) -> None:
        self._model_ready = True
        logger.debug("MLBrain: trained on %d samples", len(self._results))


# Global ML brain singleton
global_ml_brain = MLBrain()


# ---------------------------------------------------------------------------
# FastExecutor
# ---------------------------------------------------------------------------

class FastExecutor:
    """
    Analyses market data and generates trade signals.
    Executes trades via the paper trader in backtest or live mode.
    """

    def __init__(self, config: BotConfig, data: DataHandler,
                 trader: "PaperTrader") -> None:
        self.config = config
        self.data = data
        self.trader = trader

    # ------------------------------------------------------------------
    # Signal detection helpers
    # ------------------------------------------------------------------

    def _detect_ob(self, df: Any, direction: str) -> Optional[Tuple[float, float]]:
        """Return (low, high) of the most recent order block, or None."""
        try:
            if df is None or getattr(df, "empty", True) or len(df) < 3:
                return None
            # Simplified OB detection
            closes = df["Close"].values
            opens = df["Open"].values
            highs = df["High"].values
            lows = df["Low"].values
            for i in range(len(df) - 2, max(0, len(df) - 10), -1):
                if direction == "long" and closes[i] < opens[i]:
                    return (lows[i], highs[i])
                if direction == "short" and closes[i] > opens[i]:
                    return (lows[i], highs[i])
        except Exception:
            pass
        return None

    def _detect_fvg(self, df: Any, direction: str) -> Optional[Tuple[float, float]]:
        """Return (low, high) of most recent Fair Value Gap, or None."""
        try:
            if df is None or getattr(df, "empty", True) or len(df) < 4:
                return None
            highs = df["High"].values
            lows = df["Low"].values
            for i in range(len(df) - 2, max(1, len(df) - 10), -1):
                if direction == "long" and lows[i] > highs[i - 2]:
                    mid = (lows[i] + highs[i - 2]) / 2
                    return (highs[i - 2], lows[i])
                if direction == "short" and highs[i] < lows[i - 2]:
                    return (highs[i], lows[i - 2])
        except Exception:
            pass
        return None

    def _compute_confluence(self, ob: Optional[Tuple], fvg: Optional[Tuple],
                            volume_ok: bool) -> float:
        score = 0.3
        if ob:
            score += 0.3
        if fvg:
            score += 0.25
        if volume_ok:
            score += 0.15
        return min(1.0, score)

    # ------------------------------------------------------------------
    # Main analysis method
    # ------------------------------------------------------------------

    def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyse symbol and return a signal dict, or None if no trade found.
        """
        config = self.config

        # Fix 5: use longer periods in backtest mode
        if config.backtest_mode:
            period_1h = "2y"
            period_1d = "5y"
            period_1w = "10y"
        else:
            period_1h = "60d"
            period_1d = "90d"
            period_1w = "1y"

        try:
            weekly = self.data.get(symbol, "1wk", period_1w)
            daily = self.data.get(symbol, "1d", period_1d)
            hourly = self.data.get(symbol, "1h", period_1h)
            five_min = self.data.get(symbol, "5m", "5d")
        except Exception as exc:
            logger.warning("analyze: data fetch failed for %s: %s", symbol, exc)
            return None

        # Strip forming candles from weekly, daily, hourly
        try:
            if weekly is not None and not getattr(weekly, "empty", True) and len(weekly) > 1:
                weekly = weekly.iloc[:-1]
            if daily is not None and not getattr(daily, "empty", True) and len(daily) > 1:
                daily = daily.iloc[:-1]
            if hourly is not None and not getattr(hourly, "empty", True) and len(hourly) > 1:
                hourly = hourly.iloc[:-1]

            # Fix 13: strip forming 5m candle
            if five_min is not None and not getattr(five_min, "empty", True) and len(five_min) > 10:
                five_min = five_min.iloc[:-1]
        except Exception:
            pass

        # Determine current price
        try:
            current_price = float(hourly["Close"].iloc[-1])
        except Exception:
            return None

        # Simple trend detection
        try:
            ema20 = float(hourly["Close"].ewm(span=20).mean().iloc[-1])
            direction = "long" if current_price > ema20 else "short"
        except Exception:
            direction = "long"

        # Fix 1: use module-level _get_vix
        vix = _get_vix(self.data)
        if vix > 40:
            logger.info("analyze: VIX=%.1f too high, skipping %s", vix, symbol)
            return None

        # Volume confirmation
        volume_ok = True
        if config.require_volume_confirmation:
            try:
                avg_vol = float(hourly["Volume"].rolling(20).mean().iloc[-1])
                cur_vol = float(hourly["Volume"].iloc[-1])
                volume_ok = cur_vol >= avg_vol * 0.8
            except Exception:
                volume_ok = False

        ob = self._detect_ob(hourly, direction)
        fvg = self._detect_fvg(hourly, direction)

        if config.require_ob_or_fvg and not ob and not fvg:
            return None

        # Entry price: midpoint of OB or FVG
        structure = ob or fvg
        if structure:
            entry = (structure[0] + structure[1]) / 2.0
        else:
            entry = current_price

        # Fix 8: snap entry to current_price in backtest when behind the market
        if config.backtest_mode:
            if direction == "long" and entry < current_price:
                entry = current_price
            elif direction == "short" and entry > current_price:
                entry = current_price

        # Risk / reward
        risk_pct = 0.015
        if direction == "long":
            stop_loss = entry * (1 - risk_pct)
            tp1 = entry * (1 + risk_pct * 1.5)
            tp2 = entry * (1 + risk_pct * 3.0)
        else:
            stop_loss = entry * (1 + risk_pct)
            tp1 = entry * (1 - risk_pct * 1.5)
            tp2 = entry * (1 - risk_pct * 3.0)

        confluence = self._compute_confluence(ob, fvg, volume_ok)
        ml_score = global_ml_brain.predict_score({"confluence": confluence, "vix": vix})

        fingerprint = f"{symbol}_{direction}_{round(entry, 2)}_{int(time.time() // 3600)}"

        return {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "confluence": confluence,
            "ml_score": ml_score,
            "fingerprint": fingerprint,
            "ob": ob is not None,
            "fvg": fvg is not None,
            "volume_ok": volume_ok,
            "current_price": current_price,
        }

    def run_continuous(self, interval_seconds: int = 300) -> None:
        """Run analyse loop continuously."""
        while True:
            for symbol in self.config.symbols:
                signal = self.analyze(symbol)
                if signal and signal["ml_score"] > 0.5:
                    # Fix 1: use module-level _get_vix (not self._get_vix)
                    vix_now = _get_vix(self.data)
                    logger.info(
                        "Signal: %s %s @ %.2f  VIX=%.1f",
                        signal["direction"], symbol, signal["entry"], vix_now
                    )
                    self.trader.open_trade_from_signal(signal)
            time.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# PaperTrader
# ---------------------------------------------------------------------------

class PaperTrader:
    """
    Simulates trade execution, tracking open/closed trades and P&L.
    """

    # Fix 12: bounded _seen_fingerprints dict (max 5000, evict oldest 20%)
    _MAX_FP = 5000
    _EVICT_PCT = 0.20

    def __init__(self, config: BotConfig, initial_capital: float = 100_000.0) -> None:
        self.config = config
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.trades: List[Trade] = []
        self.open_trades: List[Trade] = []
        # Fix 12: Dict[str, float] with max size instead of unbounded Set
        self._seen_fingerprints: Dict[str, float] = {}
        # Fix 1: _data will be set by UltimateBot.__init__
        self._data: Optional[DataHandler] = None

    # ------------------------------------------------------------------
    # Fingerprint deduplication (Fix 12)
    # ------------------------------------------------------------------

    def _is_new_fingerprint(self, fp: str) -> bool:
        if fp in self._seen_fingerprints:
            return False
        if len(self._seen_fingerprints) >= self._MAX_FP:
            n_evict = int(self._MAX_FP * self._EVICT_PCT)
            oldest = heapq.nsmallest(n_evict, self._seen_fingerprints,
                                     key=lambda k: self._seen_fingerprints[k])
            for k in oldest:
                del self._seen_fingerprints[k]
        self._seen_fingerprints[fp] = time.time()
        return True

    # ------------------------------------------------------------------
    # Risk sizing
    # ------------------------------------------------------------------

    def smart_risk_size(self, entry: float, stop_loss: float,
                        confluence: float) -> float:
        """
        Return the number of shares/contracts to trade.
        Fix 1: uses module-level _get_vix with self._data (set by UltimateBot).
        """
        # Fix 1: call module-level function, fall back to 15.0 when _data not set
        vix = _get_vix(self._data) if self._data is not None else 15.0
        vix_mult = max(0.5, 1.5 - (vix / 40.0))

        risk_amount = self.capital * (self.config.risk_pct / 100.0) * vix_mult
        risk_per_unit = abs(entry - stop_loss)
        if risk_per_unit < 1e-9:
            return 0.0
        confluence_mult = 0.5 + confluence
        return (risk_amount / risk_per_unit) * confluence_mult

    # ------------------------------------------------------------------
    # Trade lifecycle
    # ------------------------------------------------------------------

    def open_trade_from_signal(self, signal: Dict) -> Optional[Trade]:
        if len(self.open_trades) >= self.config.max_open_trades:
            return None
        if not self._is_new_fingerprint(signal.get("fingerprint", "")):
            return None

        size = self.smart_risk_size(
            signal["entry"], signal["stop_loss"], signal["confluence"]
        )
        if size <= 0:
            return None

        trade = Trade(
            symbol=signal["symbol"],
            direction=signal["direction"],
            entry_price=signal["entry"],
            stop_loss=signal["stop_loss"],
            take_profit1=signal["tp1"],
            take_profit2=signal["tp2"],
            size=size,
            confluence_score=signal["confluence"],
            fingerprint=signal.get("fingerprint", ""),
            ob_used=signal.get("ob", False),
            fvg_used=signal.get("fvg", False),
            volume_confirmed=signal.get("volume_ok", False),
            hour=time.localtime().tm_hour,
            pattern_key=f"{signal['direction']}_{signal.get('ob', 0)}_{signal.get('fvg', 0)}",
        )
        self.open_trades.append(trade)
        logger.info("Opened: %s %s @ %.2f  size=%.2f",
                    trade.direction, trade.symbol, trade.entry_price, trade.size)
        return trade

    def close_trade(self, trade: Trade, close_price: float,
                    outcome: str = "win") -> None:
        """
        Close a trade, compute P&L, and feed the result to the ML brain.
        Fix 2: only ONE add_trade_result call (in the [v13] ML Integration block).
        Fix 3: use trade.bars_held (not _bars_held).
        """
        if trade in self.open_trades:
            self.open_trades.remove(trade)

        trade.close_price = close_price
        trade.close_time = time.time()
        trade.outcome = outcome

        if trade.direction == "long":
            trade.pnl = (close_price - trade.entry_price) * trade.size
        else:
            trade.pnl = (trade.entry_price - close_price) * trade.size

        self.capital += trade.pnl
        self.trades.append(trade)

        # Fix 3: use trade.bars_held directly; max(1, ...) avoids division-by-zero
        bars = max(1, trade.bars_held)

        # [v13] ML Integration — single add_trade_result call (Fix 2)
        rl_reward = global_ml_brain.compute_rl_reward(
            pnl=trade.pnl,
            bars_held=bars,
            win=(outcome == "win"),
            confluence=trade.confluence_score,
        )
        global_ml_brain.add_trade_result(trade, rl_reward=rl_reward)

        self._update_symbol_stats(trade)
        self._update_pattern_stats(trade)
        self._update_hour_stats(trade)

        logger.info(
            "Closed: %s %s @ %.2f  pnl=%.2f  bars=%d  outcome=%s",
            trade.direction, trade.symbol, close_price, trade.pnl, bars, outcome,
        )

    def check_open_positions(self, symbol: str, current_price: float,
                              bar_number: int) -> None:
        """
        Check open trades for stop-loss / take-profit hits and update bars_held.
        Fix 4: opposing_signal_count is now a proper dataclass field.
        """
        for trade in list(self.open_trades):
            if trade.symbol != symbol:
                continue

            trade.bars_held = bar_number

            if trade.direction == "long":
                if current_price <= trade.stop_loss:
                    self.close_trade(trade, current_price, "loss")
                elif current_price >= trade.take_profit2:
                    self.close_trade(trade, current_price, "win")
            else:
                if current_price >= trade.stop_loss:
                    self.close_trade(trade, current_price, "loss")
                elif current_price <= trade.take_profit2:
                    self.close_trade(trade, current_price, "win")

    # ------------------------------------------------------------------
    # Stats helpers — Fix 9: no save_learned_config here
    # ------------------------------------------------------------------

    def _update_symbol_stats(self, trade: Trade) -> None:
        stats = self.config.symbol_stats.setdefault(trade.symbol, {"wins": 0, "losses": 0})
        if trade.outcome == "win":
            stats["wins"] += 1
        else:
            stats["losses"] += 1
        # Fix 9: removed save_learned_config() call

    def _update_pattern_stats(self, trade: Trade) -> None:
        stats = self.config.pattern_stats.setdefault(
            trade.pattern_key, {"wins": 0, "losses": 0, "total_pnl": 0.0}
        )
        if trade.outcome == "win":
            stats["wins"] += 1
        else:
            stats["losses"] += 1
        stats["total_pnl"] += trade.pnl
        # Fix 9: removed save_learned_config() call

    def _update_hour_stats(self, trade: Trade) -> None:
        stats = self.config.hour_stats.setdefault(
            str(trade.hour), {"wins": 0, "losses": 0}
        )
        if trade.outcome == "win":
            stats["wins"] += 1
        else:
            stats["losses"] += 1

    # ------------------------------------------------------------------
    # Backtest simulation
    # ------------------------------------------------------------------

    def simulate_trade_outcome(self, trade: Trade, future_bars: Any) -> str:
        """Simulate whether a pending trade would trigger and its outcome."""
        try:
            for bar_idx in range(len(future_bars)):
                row = future_bars.iloc[bar_idx]
                low = float(row["Low"])
                high = float(row["High"])

                # Check stop and take-profit
                if trade.direction == "long":
                    if low <= trade.stop_loss:
                        self.close_trade(trade, trade.stop_loss, "loss")
                        return "loss"
                    if high >= trade.take_profit2:
                        self.close_trade(trade, trade.take_profit2, "win")
                        return "win"
                else:
                    if high >= trade.stop_loss:
                        self.close_trade(trade, trade.stop_loss, "loss")
                        return "loss"
                    if low <= trade.take_profit2:
                        self.close_trade(trade, trade.take_profit2, "win")
                        return "win"
            # No resolution within future bars — determine outcome from unrealised PnL
            last_close = float(future_bars["Close"].iloc[-1])
            if trade.direction == "long":
                unrealised_pnl = (last_close - trade.entry_price) * trade.size
            else:
                unrealised_pnl = (trade.entry_price - last_close) * trade.size
            outcome = "win" if unrealised_pnl >= 0 else "loss"
            self.close_trade(trade, last_close, outcome)
            return outcome
        except Exception as exc:
            logger.warning("simulate_trade_outcome: %s", exc)
            return "unknown"

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_metrics(self) -> Dict:
        wins = sum(1 for t in self.trades if t.outcome == "win")
        total = len(self.trades)
        win_rate = wins / total if total else 0.0
        total_pnl = sum(t.pnl for t in self.trades)
        return {
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "capital": self.capital,
            "return_pct": (self.capital - self.initial_capital) / self.initial_capital * 100,
        }


# ---------------------------------------------------------------------------
# Regime Detection
# ---------------------------------------------------------------------------

def _detect_regime(config: BotConfig, hourly_df: Any) -> str:
    """
    Classify market regime and update config settings.
    Fix 7: sets _regime_vol_override when relaxing volume confirmation.
    """
    try:
        close = hourly_df["Close"]
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        # Simplified ATR for choppiness
        highs = hourly_df["High"]
        lows = hourly_df["Low"]
        atr = (highs - lows).rolling(14).mean().iloc[-1]
        price = close.iloc[-1]
        atr_pct = float(atr / price) if price else 0.01

        trend_up = float(ema20.iloc[-1]) > float(ema50.iloc[-1])
        if atr_pct < 0.005:
            regime = "slow_chop"
        elif trend_up:
            regime = "trending"
        else:
            regime = "volatile"

        config.current_regime = regime

        if regime == "slow_chop":
            # Relax volume requirement to get signals in slow markets
            config.require_volume_confirmation = False
            # Fix 7: mark that regime is managing this override
            config._regime_vol_override = True
        else:
            config._regime_vol_override = False

        return regime
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# SelfCorrector
# ---------------------------------------------------------------------------

class SelfCorrector:
    """
    Analyses recent performance and applies configuration corrections.
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._recent: deque = deque(maxlen=20)

    def record(self, trade: Trade) -> None:
        self._recent.append(trade)

    def apply_fix(self) -> None:
        """
        Apply corrections based on recent trade performance.
        Fix 7: skip volume correction when regime override is active.
        """
        if len(self._recent) < 5:
            return
        recent_list = list(self._recent)
        win_rate = sum(1 for t in recent_list if t.outcome == "win") / len(recent_list)

        if win_rate < 0.40:
            # Only re-enable volume confirmation if regime hasn't overridden it
            # Fix 7: check _regime_vol_override before toggling
            if not self.config._regime_vol_override:
                self.config.require_volume_confirmation = True
                logger.info("SelfCorrector: re-enabled volume confirmation (win_rate=%.2f)", win_rate)
            else:
                logger.debug(
                    "SelfCorrector: skipping volume fix — regime override active (win_rate=%.2f)",
                    win_rate,
                )

    def _learn_from_winners(self, trades: List[Trade]) -> None:
        """
        Fix 10: store winning confluence patterns in pattern_stats (persisted).
        Previously toggled require_ob_or_fvg which was not persisted.
        """
        winners = [t for t in trades if t.outcome == "win"]
        for trade in winners:
            key = f"winner_{trade.pattern_key}"
            stats = self.config.pattern_stats.setdefault(
                key, {"count": 0, "total_pnl": 0.0, "avg_confluence": 0.0}
            )
            stats["count"] += 1
            stats["total_pnl"] += trade.pnl
            # Running average of confluence
            n = stats["count"]
            stats["avg_confluence"] = (
                stats["avg_confluence"] * (n - 1) + trade.confluence_score
            ) / n


# ---------------------------------------------------------------------------
# UltimateBot — top-level orchestrator
# ---------------------------------------------------------------------------

class UltimateBot:
    """
    Orchestrates the entire trading system.
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.data = DataHandler(backtest_mode=config.backtest_mode)
        self.trader = PaperTrader(config)
        self.executor = FastExecutor(config, self.data, self.trader)
        self.corrector = SelfCorrector(config)

        # Fix 1: wire DataHandler into PaperTrader so _get_vix can be called
        self.trader._data = self.data

        load_learned_config(config)

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def run_cycle(self, cycle: int = 0) -> None:
        """
        Run one analysis cycle over all configured symbols.
        Fix 9: batch-save learned config every 10 trades.
        Fix 11: in backtest mode, execute signals immediately (no pending orders).
        """
        for symbol in self.config.symbols:
            # Fix 5: prefetch with mode-appropriate periods
            if self.config.backtest_mode:
                for interval, period in [("1h", "2y"), ("1d", "5y"), ("1wk", "10y")]:
                    self.data.get(symbol, interval, period)
            else:
                for interval, period in [("1h", "60d"), ("1d", "90d"), ("1wk", "1y")]:
                    self.data.get(symbol, interval, period)

            signal = self.executor.analyze(symbol)
            if signal is None:
                continue

            ml_ok = signal["ml_score"] >= 0.5
            if not ml_ok:
                continue

            if self.config.backtest_mode:
                # Fix 11: skip pending-order system in backtest — fill immediately
                signal["entry"] = signal["current_price"]
                trade = self.trader.open_trade_from_signal(signal)
                if trade:
                    # Simulate outcome with available future bars
                    future = self.data.get_future_bars(
                        symbol, "1h",
                        "2y" if self.config.backtest_mode else "60d",
                        cycle * self.config.backtest_step,
                        self.config.backtest_step,
                    )
                    self.trader.simulate_trade_outcome(trade, future)
            else:
                self.trader.open_trade_from_signal(signal)

        # Fix 9: batch-save every 10 trades instead of per-trade
        if len(self.trader.trades) > 0 and len(self.trader.trades) % 10 == 0:
            save_learned_config(self.config)

        # Update self-corrector
        for trade in self.trader.trades[-5:]:
            self.corrector.record(trade)
        self.corrector.apply_fix()

    def run_backtest(self, cycles: int = 50) -> Dict:
        """Run a backtesting marathon."""
        logger.info("Starting backtest: %d cycles", cycles)
        for cycle in range(cycles):
            self.run_cycle(cycle=cycle)
        save_learned_config(self.config)
        metrics = self.trader.get_metrics()
        logger.info("Backtest complete: %s", metrics)
        return metrics

    def run_live(self, interval_seconds: int = 300) -> None:
        """Run the bot continuously in live paper-trading mode."""
        cycle = 0
        while True:
            self.run_cycle(cycle=cycle)
            cycle += 1
            time.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# Dashboard (Streamlit embedded script)
# Fix 6: added 'import time as time_module' inside the script string
# ---------------------------------------------------------------------------

_DASHBOARD_SCRIPT = """\
import streamlit as st
import json
import os
import time as time_module  # Fix 6: was missing, caused NameError on time_module.time()

st.set_page_config(page_title="Trading Bot Dashboard", layout="wide")
st.title("Trading Bot Dashboard v13.0")

LEARNED_CONFIG_PATH = "learned_config.json"

@st.cache_data(ttl=10)
def load_data():
    if not os.path.exists(LEARNED_CONFIG_PATH):
        return {}
    with open(LEARNED_CONFIG_PATH) as fh:
        return json.load(fh)

data = load_data()

st.sidebar.header("Status")
st.sidebar.write(f"Last refresh: {time_module.strftime('%H:%M:%S', time_module.localtime(time_module.time()))}")

pattern_stats = data.get("pattern_stats", {})
symbol_stats = data.get("symbol_stats", {})
hour_stats = data.get("hour_stats", {})

col1, col2 = st.columns(2)
with col1:
    st.subheader("Pattern Stats")
    if pattern_stats:
        import pandas as pd
        df = pd.DataFrame(pattern_stats).T
        st.dataframe(df)
    else:
        st.info("No pattern stats yet.")

with col2:
    st.subheader("Symbol Stats")
    if symbol_stats:
        import pandas as pd
        df = pd.DataFrame(symbol_stats).T
        st.dataframe(df)
    else:
        st.info("No symbol stats yet.")

st.subheader("Hour-of-Day Stats")
if hour_stats:
    import pandas as pd
    df = pd.DataFrame(hour_stats).T
    st.dataframe(df)
else:
    st.info("No hour stats yet.")
"""


def launch_dashboard() -> None:
    """Write and launch the Streamlit dashboard."""
    dashboard_path = "/tmp/dashboard_v13.py"
    with open(dashboard_path, "w") as fh:
        fh.write(_DASHBOARD_SCRIPT)
    import subprocess
    subprocess.Popen(["streamlit", "run", dashboard_path])
    logger.info("Dashboard launched: %s", dashboard_path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=f"Trading Bot {VERSION}")
    parser.add_argument("--backtest", action="store_true", help="Run backtest marathon")
    parser.add_argument("--cycles", type=int, default=50, help="Backtest cycles")
    parser.add_argument("--dashboard", action="store_true", help="Launch Streamlit dashboard")
    parser.add_argument(
        "--symbols", nargs="+", default=["SPY", "QQQ"],
        help="Symbols to trade",
    )
    args = parser.parse_args()

    config = BotConfig(
        symbols=args.symbols,
        backtest_mode=args.backtest,
    )

    if args.dashboard:
        launch_dashboard()
        return

    bot = UltimateBot(config)

    if args.backtest:
        metrics = bot.run_backtest(cycles=args.cycles)
        print(json.dumps(metrics, indent=2))
    else:
        bot.run_live()


if __name__ == "__main__":
    main()
