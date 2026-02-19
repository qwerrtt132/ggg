#!/usr/bin/env python3
"""
TJR Trading Bot – Ultimate Implementation
==========================================
Complete 575-rule knowledge base with intelligent loss analysis,
pattern detection, and self-correction.

Features
--------
* Daily bias engine  (Weekly → Daily → 4H alignment)
* Liquidity sweep detection
* Break of Structure (BOS) confirmation
* Order Block (OB) detection
* Fair Value Gap (FVG) detection
* Equilibrium zone calculation
* Confluence scoring (minimum 3 required)
* Paper trading with 1% risk, partial TPs, breakeven at TP1
* Loss analysis referencing specific rule numbers
* Pattern detection over last N trades
* Self-correction with user approval
* Adaptive learning – tracks win rates by confluence type

Usage
-----
    python tjr_bot.py

Requirements
------------
    pip install yfinance pandas numpy pytz
"""

from __future__ import annotations

import json
import os
import sys
import time
import warnings
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed.  Run: pip install yfinance pandas numpy pytz")
    sys.exit(1)

try:
    import pytz
    NY_TZ = pytz.timezone("America/New_York")
except ImportError:
    NY_TZ = None  # type: ignore

from rules_database import RulesEngine, RULES, SYMPTOM_KEYS

# ─────────────────────────────────────────────────────────────────────────────
# SELF-CORRECTION THRESHOLDS (extracted constants for maintainability)
# ─────────────────────────────────────────────────────────────────────────────
# Maximum confluence count allowed by the self-corrector (Rule 256 – may miss trades)
MAX_CONFLUENCES: int = 5
# Win-rate below this % causes the corrector to recommend disabling a pattern
MIN_CONFLUENCE_WIN_RATE: float = 30.0

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "tjr_config.json")
JOURNAL_FILE = os.path.join(BASE_DIR, "tjr_journal.json")

# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG: Dict[str, Any] = {
    # Market
    "symbol":                       "GC=F",    # Gold futures (GBPJPY=X, GBPUSD=X, SPY …)
    "account_balance":              10_000.0,
    # Risk (Rules 37-44)
    "risk_percent":                 1.0,
    "max_trades_per_day":           1,
    "max_consecutive_losses":       2,
    # Confluence requirements (Rules 183-190, 498-499)
    "min_confluences":              3,
    "require_ob_or_fvg":            True,
    # R:R (Rules 400, 408)
    "min_rr":                       1.5,
    # Position management (Rules 393-416)
    "partial_close_tp1_pct":        0.50,
    "partial_close_tp2_pct":        0.30,
    "breakeven_at_tp1":             True,
    # Swing detection
    "swing_lookback":               5,
    # Session (Rules 169-170)
    "session":                      "NY",
    # Adaptive learning
    "disabled_confluence_patterns": [],
    "preferred_confluence_patterns": [],
    # Misc
    "scan_interval_seconds":        60,
    "paper_trading":                True,
}

# ─────────────────────────────────────────────────────────────────────────────
# COLOUR HELPERS
# ─────────────────────────────────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    MAGENTA= "\033[95m"
    BLUE   = "\033[94m"

def cprint(msg: str, colour: str = C.WHITE) -> None:
    print(f"{colour}{msg}{C.RESET}")

# ─────────────────────────────────────────────────────────────────────────────
# TRADE DATACLASS
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    id:               str
    symbol:           str
    direction:        str          # "long" | "short"
    entry_price:      float
    stop_loss:        float
    take_profits:     List[float]  # [tp1, tp2, tp3, ...]
    position_size:    float        # units
    risk_amount:      float        # $ risked
    confluences:      List[str]
    confluence_count: int
    has_ob:           bool
    has_fvg:          bool
    has_eq:           bool
    daily_bias:       str          # "bullish" | "bearish" | "neutral"
    weekly_trend:     str
    four_h_trend:     str
    entry_time:       str
    # ── filled on close ───────────────────────────────────────────────
    close_time:       Optional[str]  = None
    close_price:      Optional[float]= None
    status:           str           = "open"  # open|closed_win|closed_loss|closed_be
    pnl_pct:          float          = 0.0     # % P&L
    pnl_usd:          float          = 0.0
    closed_tps:       List[int]      = field(default_factory=list)
    breakeven_set:    bool           = False
    remaining_size:   float          = 0.0    # units still open
    # ── analysis ──────────────────────────────────────────────────────
    symptoms:         List[str]      = field(default_factory=list)
    violated_rules:   List[int]      = field(default_factory=list)
    notes:            str            = ""


# ─────────────────────────────────────────────────────────────────────────────
# SETUP DATACLASS
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Setup:
    symbol:           str
    direction:        str
    entry:            float
    stop:             float
    take_profits:     List[float]
    confluences:      List[str]
    confluence_count: int
    has_ob:           bool
    has_fvg:          bool
    has_eq:           bool
    ob_zone:          Optional[Tuple[float, float]]  # (top, bottom)
    fvg_zone:         Optional[Tuple[float, float]]
    eq_level:         Optional[float]
    swept_level:      float
    bos_level:        float
    daily_bias:       str
    weekly_trend:     str
    four_h_trend:     str
    rr:               float
    description:      str


# ─────────────────────────────────────────────────────────────────────────────
# TRADE JOURNAL (persistent JSON store)
# ─────────────────────────────────────────────────────────────────────────────
class TradeJournal:
    def __init__(self, path: str = JOURNAL_FILE) -> None:
        self.path = path
        self.trades: List[Trade] = []
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path) as fh:
                    raw = json.load(fh)
                self.trades = [Trade(**t) for t in raw]
            except Exception:
                self.trades = []

    def save(self) -> None:
        with open(self.path, "w") as fh:
            json.dump([asdict(t) for t in self.trades], fh, indent=2)

    def add(self, trade: Trade) -> None:
        self.trades.append(trade)
        self.save()

    def update(self, trade: Trade) -> None:
        for i, t in enumerate(self.trades):
            if t.id == trade.id:
                self.trades[i] = trade
                break
        self.save()

    def open_trades(self) -> List[Trade]:
        return [t for t in self.trades if t.status == "open"]

    def closed_trades(self) -> List[Trade]:
        return [t for t in self.trades if t.status != "open"]

    def losses(self) -> List[Trade]:
        return [t for t in self.trades if t.status == "closed_loss"]

    def wins(self) -> List[Trade]:
        return [t for t in self.trades
                if t.status in ("closed_win", "closed_be")]

    def today_trades(self) -> List[Trade]:
        today = datetime.now().strftime("%Y-%m-%d")
        return [t for t in self.trades if t.entry_time.startswith(today)]

    def last_n(self, n: int) -> List[Trade]:
        return self.trades[-n:]

    def consecutive_losses(self) -> int:
        count = 0
        for t in reversed(self.trades):
            if t.status == "closed_loss":
                count += 1
            elif t.status in ("closed_win", "closed_be"):
                break
        return count


# ─────────────────────────────────────────────────────────────────────────────
# MARKET ANALYSER
# ─────────────────────────────────────────────────────────────────────────────
class MarketAnalyzer:
    """Fetch market data and detect TJR building blocks."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.cfg = config

    # ── Data fetching ──────────────────────────────────────────────────────
    def get_data(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                return pd.DataFrame()
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.dropna(inplace=True)
            return df
        except Exception as e:
            return pd.DataFrame()

    def get_4h_data(self, symbol: str) -> pd.DataFrame:
        df = self.get_data(symbol, "1h", "60d")
        if df.empty:
            return df
        df.index = pd.to_datetime(df.index)
        df4 = df.resample("4h").agg(
            Open=("Open", "first"),
            High=("High", "max"),
            Low=("Low", "min"),
            Close=("Close", "last"),
            Volume=("Volume", "sum"),
        ).dropna()
        return df4

    # ── Swing point detection (Rules 18-24) ───────────────────────────────
    def find_swings(self, df: pd.DataFrame,
                    lookback: int = 5) -> List[Dict]:
        swings: List[Dict] = []
        n = len(df)
        for i in range(lookback, n - lookback):
            hi  = df["High"].iloc[i]
            lo  = df["Low"].iloc[i]
            win_hi = df["High"].iloc[i - lookback: i + lookback + 1]
            win_lo = df["Low"].iloc[i - lookback: i + lookback + 1]
            if hi == win_hi.max():
                swings.append({"idx": i, "price": hi, "type": "high",
                                "time": df.index[i]})
            elif lo == win_lo.min():
                swings.append({"idx": i, "price": lo, "type": "low",
                                "time": df.index[i]})
        return swings

    def get_trend(self, swings: List[Dict]) -> str:
        """Classify trend from swing sequence (Rules 9-10)."""
        highs = [s["price"] for s in swings if s["type"] == "high"]
        lows  = [s["price"] for s in swings if s["type"] == "low"]
        if len(highs) < 2 or len(lows) < 2:
            return "neutral"
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            return "bullish"
        if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            return "bearish"
        return "consolidation"

    # ── Daily bias (Rules 313-392) ─────────────────────────────────────────
    def get_daily_bias(self, symbol: str) -> Tuple[str, str, str]:
        """
        Returns (daily_bias, weekly_trend, four_h_trend).
        Each is: "bullish" | "bearish" | "neutral" | "consolidation"
        """
        # Weekly
        df_wk = self.get_data(symbol, "1wk", "2y")
        weekly_trend = "neutral"
        if not df_wk.empty:
            swings_wk = self.find_swings(df_wk, lookback=3)
            weekly_trend = self.get_trend(swings_wk)

        # Daily
        df_d = self.get_data(symbol, "1d", "1y")
        daily_bias = "neutral"
        if not df_d.empty and len(df_d) >= 10:
            swings_d = self.find_swings(df_d, lookback=3)
            daily_bias = self.get_trend(swings_d)
            # Last close vs recent swings for confirmation
            last_close = df_d["Close"].iloc[-1]
            recent_highs = [s for s in swings_d if s["type"] == "high"]
            recent_lows  = [s for s in swings_d if s["type"] == "low"]
            if recent_highs and recent_lows:
                last_hi = recent_highs[-1]["price"]
                last_lo = recent_lows[-1]["price"]
                if last_close > last_hi:
                    daily_bias = "bullish"
                elif last_close < last_lo:
                    daily_bias = "bearish"

        # 4H
        df_4h = self.get_4h_data(symbol)
        four_h_trend = "neutral"
        if not df_4h.empty and len(df_4h) >= 10:
            swings_4h = self.find_swings(df_4h, lookback=3)
            four_h_trend = self.get_trend(swings_4h)

        return daily_bias, weekly_trend, four_h_trend

    # ── Liquidity sweep detection (Rules 25-36) ────────────────────────────
    def detect_sweep(self, df: pd.DataFrame,
                     swings: List[Dict]) -> Optional[Dict]:
        """
        Scan the last 5 candles for a sweep of any prominent swing.
        Returns dict with sweep info or None.
        """
        if len(df) < 10:
            return None
        recent = df.iloc[-5:]
        for swing in reversed(swings):
            # Skip swings that are too recent (within last 3 candles)
            if swing["idx"] >= len(df) - 3:
                continue
            sp = swing["price"]
            stype = swing["type"]
            for i in range(len(recent)):
                c = recent.iloc[i]
                if stype == "high":
                    if c["High"] > sp and c["Close"] < sp:
                        return {"direction": "bearish", "swept_price": sp,
                                "sweep_idx": len(df) - len(recent) + i,
                                "swing_type": "high"}
                else:
                    if c["Low"] < sp and c["Close"] > sp:
                        return {"direction": "bullish", "swept_price": sp,
                                "sweep_idx": len(df) - len(recent) + i,
                                "swing_type": "low"}
        return None

    # ── Break of Structure (Rules 20-24) ──────────────────────────────────
    def detect_bos(self, df: pd.DataFrame, sweep: Dict,
                   swings: List[Dict]) -> Optional[Dict]:
        """
        After a sweep, look for BOS (close beyond opposite structure).
        Rule 20: BOS = CLOSE beyond level (not just wick).
        """
        sweep_idx = sweep["sweep_idx"]
        direction = sweep["direction"]
        post = df.iloc[sweep_idx:]
        if len(post) < 2:
            return None

        if direction == "bearish":
            # Need close BELOW a prior swing LOW
            prior_lows = [s for s in swings
                          if s["type"] == "low" and s["idx"] < sweep_idx]
            if not prior_lows:
                return None
            target = prior_lows[-1]["price"]
            for i in range(1, len(post)):
                if post.iloc[i]["Close"] < target:
                    return {"bos_level": target, "bos_idx": sweep_idx + i,
                            "direction": "bearish"}
        else:
            # Need close ABOVE a prior swing HIGH
            prior_highs = [s for s in swings
                           if s["type"] == "high" and s["idx"] < sweep_idx]
            if not prior_highs:
                return None
            target = prior_highs[-1]["price"]
            for i in range(1, len(post)):
                if post.iloc[i]["Close"] > target:
                    return {"bos_level": target, "bos_idx": sweep_idx + i,
                            "direction": "bullish"}
        return None

    # ── Order Block detection (Rules 90-103) ──────────────────────────────
    def detect_order_block(self, df: pd.DataFrame,
                           sweep_idx: int,
                           direction: str) -> Optional[Dict]:
        """
        OB = last opposing-colour candle before the sweep move.
        Mark: wick of first candle to body of last candle (Rule 94-95).
        """
        start = max(0, sweep_idx - 20)
        if direction == "bearish":
            # Last BULLISH candle before sweep
            for i in range(sweep_idx - 1, start, -1):
                c = df.iloc[i]
                if c["Close"] > c["Open"]:
                    return {"top": c["High"], "bottom": c["Open"],
                            "idx": i, "setup": "bearish_short"}
        else:
            # Last BEARISH candle before sweep
            for i in range(sweep_idx - 1, start, -1):
                c = df.iloc[i]
                if c["Close"] < c["Open"]:
                    return {"top": c["Open"], "bottom": c["Low"],
                            "idx": i, "setup": "bullish_long"}
        return None

    # ── Fair Value Gap detection (Rules 57-68) ────────────────────────────
    def detect_fvg(self, df: pd.DataFrame,
                   direction: str,
                   lookback: int = 30) -> Optional[Dict]:
        """
        Bullish FVG : c[0].high < c[2].low  (gap = discount zone)
        Bearish FVG : c[0].low  > c[2].high (gap = premium zone)
        Rule 59: measure wick to wick.
        """
        start = max(0, len(df) - lookback)
        best = None
        for i in range(start, len(df) - 2):
            c0 = df.iloc[i]
            c2 = df.iloc[i + 2]
            if direction == "bullish":
                if c0["High"] < c2["Low"]:
                    gap = c2["Low"] - c0["High"]
                    if best is None or gap > best.get("gap", 0):
                        best = {"top": c2["Low"], "bottom": c0["High"],
                                "mid": (c2["Low"] + c0["High"]) / 2,
                                "gap": gap, "idx": i, "direction": "bullish"}
            else:
                if c0["Low"] > c2["High"]:
                    gap = c0["Low"] - c2["High"]
                    if best is None or gap > best.get("gap", 0):
                        best = {"top": c0["Low"], "bottom": c2["High"],
                                "mid": (c0["Low"] + c2["High"]) / 2,
                                "gap": gap, "idx": i, "direction": "bearish"}
        return best

    # ── Equilibrium (Rules 117-158) ────────────────────────────────────────
    def calculate_equilibrium(self, df: pd.DataFrame,
                               swings: List[Dict]) -> Optional[Dict]:
        highs = [s["price"] for s in swings if s["type"] == "high"]
        lows  = [s["price"] for s in swings if s["type"] == "low"]
        if not highs or not lows:
            return None
        sh = highs[-1]
        sl = lows[-1]
        if sh <= sl:
            return None
        eq  = (sh + sl) / 2.0
        cur = df["Close"].iloc[-1]
        return {
            "swing_high":  sh,
            "swing_low":   sl,
            "eq":          eq,
            "current":     cur,
            "zone":        "premium" if cur > eq else "discount",
            "pct_from_eq": (cur - eq) / (sh - sl) * 100,
        }

    # ── Full setup scan ────────────────────────────────────────────────────
    def scan(self, symbol: str, config: Dict[str, Any]) -> Optional[Setup]:
        """
        Full top-down scan for TJR setup.
        Returns Setup if valid, else None.
        """
        # 1. Daily bias (Rules 313-392)
        daily_bias, weekly_trend, four_h_trend = self.get_daily_bias(symbol)

        # Require bias alignment (Rule 327 – be cautious if 4H conflicts)
        # We allow trades when at least daily is clear
        if daily_bias in ("neutral", "consolidation"):
            return None  # Rule 17: No trade in consolidation

        # Execution timeframe: 5-minute
        df5 = self.get_data(symbol, "5m", "5d")
        if df5.empty or len(df5) < 30:
            return None

        lookback = config.get("swing_lookback", 5)
        swings = self.find_swings(df5, lookback=lookback)
        if not swings:
            return None

        # 2. Liquidity sweep (Rules 25-36)
        sweep = self.detect_sweep(df5, swings)
        if sweep is None:
            return None

        # Validate direction matches bias
        sweep_dir = sweep["direction"]
        if sweep_dir != daily_bias:
            # Only allow if bias says we go in sweep direction
            return None

        # 3. BOS confirmation (Rules 20-24)
        bos = self.detect_bos(df5, sweep, swings)
        if bos is None:
            return None

        # 4. Order Block (Rules 90-103)
        ob = self.detect_order_block(df5, sweep["sweep_idx"], sweep_dir)

        # 5. FVG (Rules 57-68)
        fvg = self.detect_fvg(df5, sweep_dir)

        # 6. Equilibrium (Rules 117-158)
        eq_info = self.calculate_equilibrium(df5, swings)

        # 7. Confluence count (Rules 183-190)
        confluences: List[str] = ["sweep", "bos"]
        if daily_bias in ("bullish", "bearish"):
            confluences.append("daily_bias")
        if four_h_trend == daily_bias:
            confluences.append("4h_align")
        if weekly_trend == daily_bias:
            confluences.append("weekly_align")
        has_ob = ob is not None
        has_fvg = fvg is not None
        has_eq  = False

        if has_ob:
            confluences.append("ob")
        if has_fvg:
            confluences.append("fvg")
        if eq_info:
            # Premium/discount check (Rules 120-122, 140-143)
            correct_zone = (
                (sweep_dir == "bullish" and eq_info["zone"] == "discount") or
                (sweep_dir == "bearish" and eq_info["zone"] == "premium")
            )
            if correct_zone:
                confluences.append("equilibrium")
                has_eq = True

        # Check disabled patterns (from self-corrector)
        disabled = config.get("disabled_confluence_patterns", [])
        conf_key = "-".join(sorted(confluences))
        if conf_key in disabled:
            return None

        confluence_count = len(confluences)

        # 8. Gate: minimum confluences (Rule 190)
        if confluence_count < config.get("min_confluences", 3):
            return None

        # 9. Gate: require OB or FVG (Rule 498)
        if config.get("require_ob_or_fvg", True) and not (has_ob or has_fvg):
            return None

        # 10. Calculate entry, stop, TPs ─────────────────────────────────
        current_price = df5["Close"].iloc[-1]

        # Entry: at OB mid-point > FVG mid > current price
        if has_ob:
            entry = (ob["top"] + ob["bottom"]) / 2  # type: ignore[index]
        elif has_fvg:
            entry = fvg["mid"]                       # type: ignore[index]
        else:
            entry = current_price

        # Stop: beyond swept liquidity (Rules 417-443)
        buffer = abs(entry) * 0.001  # 0.1% buffer (Rule 424)
        swept  = sweep["swept_price"]
        if sweep_dir == "bearish":
            stop = swept + buffer
        else:
            stop = swept - buffer

        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return None

        # TPs: 1:1, 1.5:1, 2:1 on next liquidity levels
        high_swings  = [s["price"] for s in swings if s["type"] == "high"]
        low_swings   = [s["price"] for s in swings if s["type"] == "low"]

        tps: List[float] = []
        if sweep_dir == "bearish":
            candidates = sorted([p for p in low_swings if p < entry], reverse=True)
            for p in candidates[:3]:
                rr = (entry - p) / stop_dist
                if rr >= config.get("min_rr", 1.5):
                    tps.append(round(p, 5))
            if not tps:
                tps = [
                    round(entry - stop_dist * 1.5, 5),
                    round(entry - stop_dist * 2.0, 5),
                    round(entry - stop_dist * 3.0, 5),
                ]
        else:
            candidates = sorted([p for p in high_swings if p > entry])
            for p in candidates[:3]:
                rr = (p - entry) / stop_dist
                if rr >= config.get("min_rr", 1.5):
                    tps.append(round(p, 5))
            if not tps:
                tps = [
                    round(entry + stop_dist * 1.5, 5),
                    round(entry + stop_dist * 2.0, 5),
                    round(entry + stop_dist * 3.0, 5),
                ]

        rr = abs(tps[0] - entry) / stop_dist if tps else 0.0

        # Describe setup
        conf_str = " + ".join(c.upper() for c in confluences)
        desc = (f"{sweep_dir.upper()} – {conf_str} "
                f"| R:R={rr:.1f} | bias={daily_bias}")

        return Setup(
            symbol=symbol, direction=sweep_dir,
            entry=round(entry, 5), stop=round(stop, 5),
            take_profits=tps,
            confluences=confluences, confluence_count=confluence_count,
            has_ob=has_ob, has_fvg=has_fvg, has_eq=has_eq,
            ob_zone=(ob["top"], ob["bottom"]) if has_ob else None,  # type: ignore[index]
            fvg_zone=(fvg["top"], fvg["bottom"]) if has_fvg else None,  # type: ignore[index]
            eq_level=eq_info["eq"] if eq_info else None,
            swept_level=swept, bos_level=bos["bos_level"],
            daily_bias=daily_bias, weekly_trend=weekly_trend,
            four_h_trend=four_h_trend, rr=round(rr, 2),
            description=desc,
        )


# ─────────────────────────────────────────────────────────────────────────────
# POSITION MANAGER
# ─────────────────────────────────────────────────────────────────────────────
class PositionManager:
    """Handle sizing, partial closes and breakeven (Rules 393-485)."""

    @staticmethod
    def calc_size(balance: float, risk_pct: float,
                  entry: float, stop: float) -> float:
        """
        Units = risk_$ / stop_distance
        Rule 457: Balance × Risk% / stop_distance = position size
        """
        risk_usd  = balance * (risk_pct / 100.0)
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return 0.0
        return round(risk_usd / stop_dist, 4)

    @staticmethod
    def update_trade(trade: Trade, current_price: float,
                     config: Dict[str, Any]) -> Trade:
        """
        Check if SL / TPs hit.  Handles partial closes and breakeven.
        Returns updated Trade object.
        """
        if trade.status != "open":
            return trade

        d = trade.direction

        def hit_sl() -> bool:
            if d == "long":
                return current_price <= trade.stop_loss
            return current_price >= trade.stop_loss

        def hit_tp(tp: float) -> bool:
            if d == "long":
                return current_price >= tp
            return current_price <= tp

        # SL hit?
        if hit_sl():
            pnl_pts = (trade.stop_loss - trade.entry_price
                       if d == "long" else
                       trade.entry_price - trade.stop_loss)
            trade.close_price = trade.stop_loss
            trade.close_time  = datetime.now().isoformat()
            if trade.breakeven_set:
                trade.status  = "closed_be"
                trade.pnl_pct = 0.0
                trade.pnl_usd = 0.0
            else:
                trade.status  = "closed_loss"
                trade.pnl_pct = pnl_pts / trade.entry_price * 100
                trade.pnl_usd = pnl_pts * trade.remaining_size
            return trade

        # TPs
        for tp_idx, tp in enumerate(trade.take_profits):
            if tp_idx in trade.closed_tps:
                continue
            if hit_tp(tp):
                trade.closed_tps.append(tp_idx)
                # Partial close
                if tp_idx == 0:
                    close_pct = config.get("partial_close_tp1_pct", 0.5)
                    # Move stop to breakeven at TP1 (Rule: "Move to BE at TP1").
                    # Guard: closed_tps check above ensures this runs only once.
                    if config.get("breakeven_at_tp1", True) and not trade.breakeven_set:
                        trade.stop_loss     = trade.entry_price
                        trade.breakeven_set = True
                elif tp_idx == 1:
                    close_pct = config.get("partial_close_tp2_pct", 0.3)
                else:
                    close_pct = 1.0  # full close at last TP

                closed_units = trade.remaining_size * close_pct
                trade.remaining_size -= closed_units
                pnl_pts = abs(tp - trade.entry_price)
                trade.pnl_usd += pnl_pts * closed_units
                trade.pnl_pct  = (trade.pnl_usd /
                                   (trade.risk_amount or 1)) * 100

                if trade.remaining_size <= 0 or tp_idx >= len(trade.take_profits) - 1:
                    trade.close_price = tp
                    trade.close_time  = datetime.now().isoformat()
                    trade.status      = "closed_win"
                    trade.remaining_size = 0.0

        return trade


# ─────────────────────────────────────────────────────────────────────────────
# PAPER TRADER
# ─────────────────────────────────────────────────────────────────────────────
class PaperTrader:
    """Execute and track paper trades."""

    def __init__(self, journal: TradeJournal, config: Dict[str, Any]) -> None:
        self.journal = journal
        self.cfg     = config
        self.pm      = PositionManager()

    def execute(self, setup: Setup) -> Trade:
        size = PositionManager.calc_size(
            self.cfg["account_balance"],
            self.cfg["risk_percent"],
            setup.entry, setup.stop,
        )
        risk_usd = (self.cfg["account_balance"]
                    * self.cfg["risk_percent"] / 100.0)
        trade_id = f"TJR-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        trade = Trade(
            id=trade_id,
            symbol=setup.symbol,
            direction=setup.direction,
            entry_price=setup.entry,
            stop_loss=setup.stop,
            take_profits=setup.take_profits,
            position_size=size,
            risk_amount=risk_usd,
            confluences=setup.confluences,
            confluence_count=setup.confluence_count,
            has_ob=setup.has_ob,
            has_fvg=setup.has_fvg,
            has_eq=setup.has_eq,
            daily_bias=setup.daily_bias,
            weekly_trend=setup.weekly_trend,
            four_h_trend=setup.four_h_trend,
            entry_time=datetime.now().isoformat(),
            remaining_size=size,
        )
        self.journal.add(trade)
        return trade

    def refresh_open_trades(self, symbol: str) -> None:
        open_t = self.journal.open_trades()
        if not open_t:
            return
        analyzer = MarketAnalyzer(self.cfg)
        df = analyzer.get_data(symbol, "5m", "1d")
        if df.empty:
            return
        cur = df["Close"].iloc[-1]
        for trade in open_t:
            if trade.symbol != symbol:
                continue
            updated = PositionManager.update_trade(trade, cur, self.cfg)
            self.journal.update(updated)


# ─────────────────────────────────────────────────────────────────────────────
# LOSS ANALYSER (uses 575-rule knowledge base)
# ─────────────────────────────────────────────────────────────────────────────
class LossAnalyzer:
    """Diagnose a loss by matching trade symptoms to violated rules."""

    def __init__(self) -> None:
        self.engine = RulesEngine()

    def extract_symptoms(self, trade: Trade,
                         cfg: Dict[str, Any]) -> List[str]:
        symptoms: List[str] = []
        min_conf = cfg.get("min_confluences", 3)

        # Confluence checks
        if trade.confluence_count < min_conf:
            symptoms.append("low_confluence")
        if (not trade.has_ob and not trade.has_fvg
                and "sweep" in trade.confluences
                and "bos" in trade.confluences
                and trade.confluence_count <= 2):
            symptoms.append("only_sweep_bos")
        if not trade.has_ob:
            symptoms.append("no_ob")
        if not trade.has_fvg:
            symptoms.append("no_fvg")
        if not trade.has_eq:
            symptoms.append("no_eq")

        # Direction
        d = trade.direction
        if d == "long" and trade.daily_bias in ("bearish",):
            symptoms.append("wrong_bias")
        if d == "short" and trade.daily_bias in ("bullish",):
            symptoms.append("wrong_bias")
        if trade.four_h_trend not in (trade.daily_bias, "neutral"):
            symptoms.append("daily_4h_conflict")
        if trade.weekly_trend not in (trade.daily_bias, "neutral"):
            symptoms.append("timeframe_conflict")

        # Price zone check (EQ premium/discount)
        if d == "long" and trade.has_eq:
            # We check entry vs eq later; flag as premium_buy for now
            pass  # determined at analysis time

        # Consecutive losses (Rule 40)
        if trade.status == "closed_loss":
            journal_idx = None
            # We flag this separately; added during pattern analysis
            pass

        # No daily bias
        if trade.daily_bias in ("neutral", "consolidation"):
            symptoms.append("no_daily_bias")

        # Stop placement check
        if trade.stop_loss == trade.entry_price:
            symptoms.append("stop_too_tight")

        # Poor R:R
        if trade.take_profits:
            stop_dist = abs(trade.entry_price - trade.stop_loss)
            tp1_dist  = abs(trade.take_profits[0] - trade.entry_price)
            if stop_dist > 0 and (tp1_dist / stop_dist) < 1.0:
                symptoms.append("poor_rr")

        # Continued after stop → classic Rule 495/496 pattern
        if trade.status == "closed_loss":
            symptoms.append("stopped_early")
            symptoms.append("continued_after_stop")

        return list(dict.fromkeys(symptoms))  # dedup, preserve order

    def analyze(self, trade: Trade,
                cfg: Dict[str, Any]) -> Dict[str, Any]:
        symptoms = self.extract_symptoms(trade, cfg)
        rules    = self.engine.find_by_symptoms(symptoms)
        fixes    = self.engine.find_fixes(symptoms)

        # Tag trade
        trade.symptoms      = symptoms
        trade.violated_rules = [r["number"] for r in rules]

        return {
            "trade":    trade,
            "symptoms": symptoms,
            "rules":    rules,
            "fixes":    fixes,
        }

    def format_report(self, analysis: Dict[str, Any]) -> str:
        trade   = analysis["trade"]
        symptoms= analysis["symptoms"]
        rules   = analysis["rules"]
        fixes   = analysis["fixes"]

        lines: List[str] = []
        bar = "═" * 62
        lines.append(bar)
        lines.append(f"  LOSS ANALYSIS – Trade {trade.id}")
        lines.append(f"  {trade.symbol}  {trade.direction.upper()}")
        lines.append(bar)

        # Trade details
        lines.append("")
        lines.append(f"  Entry  : {trade.entry_price:.5f}")
        lines.append(f"  Stop   : {trade.stop_loss:.5f}")
        lines.append(f"  Status : {trade.status.upper()}")
        lines.append(f"  P&L    : ${trade.pnl_usd:+.2f}")
        lines.append(f"  Confluences ({trade.confluence_count}): "
                     f"{', '.join(trade.confluences)}")
        lines.append(f"  Daily Bias : {trade.daily_bias}  "
                     f"4H: {trade.four_h_trend}  Weekly: {trade.weekly_trend}")
        lines.append(f"  OB: {'✓' if trade.has_ob else '✗'}  "
                     f"FVG: {'✓' if trade.has_fvg else '✗'}  "
                     f"EQ: {'✓' if trade.has_eq else '✗'}")

        # Symptoms
        lines.append("")
        lines.append("SYMPTOMS DETECTED:")
        for s in symptoms:
            lines.append(f"  ⚠  {SYMPTOM_KEYS.get(s, s)}")

        # Rules
        lines.append("")
        lines.append(f"SEARCHING 575 RULES … {len(rules)} VIOLATIONS FOUND:")
        lines.append("")
        shown = rules[:10]  # show top 10 most relevant
        for r in shown:
            lines.append(f"  Rule #{r['number']:>4}: {r['text']}")
        if len(rules) > 10:
            lines.append(f"  … and {len(rules) - 10} more (see full DB)")

        # Fixes
        if fixes:
            lines.append("")
            lines.append("RECOMMENDED FIXES (from rule database):")
            for f_ in fixes:
                lines.append(f"  ► Rule #{f_['number']}: {f_['text']}")

        lines.append(bar)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# PATTERN DETECTOR
# ─────────────────────────────────────────────────────────────────────────────
class PatternDetector:
    """Detect repeated mistakes and calculate win rates by pattern."""

    def analyze(self, trades: List[Trade],
                window: int = 10) -> Dict[str, Any]:
        closed = [t for t in trades if t.status != "open"]
        recent = closed[-window:]

        # ── Symptom frequency in losses ───────────────────────────────────
        symptom_counts: Dict[str, int] = {}
        for t in [r for r in recent if r.status == "closed_loss"]:
            for s in t.symptoms:
                symptom_counts[s] = symptom_counts.get(s, 0) + 1

        # ── Win rate by confluence count ───────────────────────────────────
        by_count: Dict[int, Dict] = {}
        for t in closed:
            c = t.confluence_count
            if c not in by_count:
                by_count[c] = {"wins": 0, "losses": 0}
            if t.status in ("closed_win", "closed_be"):
                by_count[c]["wins"] += 1
            else:
                by_count[c]["losses"] += 1

        conf_wr: Dict[int, float] = {}
        for c, d in by_count.items():
            total = d["wins"] + d["losses"]
            conf_wr[c] = (d["wins"] / total * 100) if total else 0.0

        # ── Win rate by confluence type ────────────────────────────────────
        by_type: Dict[str, Dict] = {}
        for t in closed:
            for conf in t.confluences:
                if conf not in by_type:
                    by_type[conf] = {"wins": 0, "losses": 0}
                if t.status in ("closed_win", "closed_be"):
                    by_type[conf]["wins"] += 1
                else:
                    by_type[conf]["losses"] += 1

        type_wr: Dict[str, float] = {}
        for conf, d in by_type.items():
            total = d["wins"] + d["losses"]
            type_wr[conf] = (d["wins"] / total * 100) if total else 0.0

        # ── Repeated patterns ─────────────────────────────────────────────
        repeated: List[Dict] = []
        for sym, cnt in symptom_counts.items():
            if cnt >= 3:
                repeated.append({
                    "symptom":     sym,
                    "description": SYMPTOM_KEYS.get(sym, sym),
                    "count":       cnt,
                })

        # ── Overall stats ─────────────────────────────────────────────────
        total   = len(closed)
        n_wins  = sum(1 for t in closed if t.status in ("closed_win", "closed_be"))
        win_rate= (n_wins / total * 100) if total else 0.0

        return {
            "total_closed":  total,
            "win_rate":      win_rate,
            "symptom_freq":  symptom_counts,
            "conf_win_rate": conf_wr,
            "type_win_rate": type_wr,
            "repeated":      repeated,
            "recent_window": window,
        }

    def format_report(self, analysis: Dict[str, Any]) -> str:
        bar = "═" * 62
        lines: List[str] = []
        lines.append(bar)
        lines.append("  PATTERN ANALYSIS")
        lines.append(bar)
        lines.append(f"  Total closed trades : {analysis['total_closed']}")
        lines.append(f"  Overall win rate    : {analysis['win_rate']:.1f}%")
        lines.append(f"  Analysis window     : last {analysis['recent_window']} trades")

        # Win rate by confluence count
        lines.append("")
        lines.append("WIN RATE BY CONFLUENCE COUNT:")
        for c in sorted(analysis["conf_win_rate"]):
            wr = analysis["conf_win_rate"][c]
            bar_len = int(wr / 5)
            bar_viz  = "█" * bar_len
            lines.append(f"  {c} confluences : {wr:5.1f}%  {bar_viz}")

        # Win rate by type
        lines.append("")
        lines.append("WIN RATE BY CONFLUENCE TYPE:")
        for conf in sorted(analysis["type_win_rate"],
                           key=lambda x: analysis["type_win_rate"][x],
                           reverse=True):
            wr = analysis["type_win_rate"][conf]
            lines.append(f"  {conf:<18s}: {wr:5.1f}%")

        # Repeated mistakes
        if analysis["repeated"]:
            lines.append("")
            lines.append("REPEATED MISTAKES (3+ times in last window):")
            for r in analysis["repeated"]:
                lines.append(f"  ⚠  '{r['description']}' × {r['count']}")

        lines.append(bar)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SELF CORRECTOR
# ─────────────────────────────────────────────────────────────────────────────
class SelfCorrector:
    """
    Generate parameter-level recommendations from pattern data.
    Ask user permission.  Apply approved corrections (Rules 486-531).
    """

    def __init__(self) -> None:
        self.engine = RulesEngine()

    def generate_recommendations(
            self, patterns: Dict[str, Any],
            config: Dict[str, Any]) -> List[Dict]:
        recs: List[Dict] = []
        repeated = patterns.get("repeated", [])
        conf_wr  = patterns.get("conf_win_rate", {})
        type_wr  = patterns.get("type_win_rate", {})
        symptom_freq = patterns.get("symptom_freq", {})

        # ── Recommendation: increase min confluences ──────────────────────
        lc_count = symptom_freq.get("low_confluence", 0)
        osb_count = symptom_freq.get("only_sweep_bos", 0)
        current_min = config.get("min_confluences", 3)

        if (lc_count >= 2 or osb_count >= 2) and current_min < MAX_CONFLUENCES:
            new_min = current_min + 1
            violated = self.engine.find_by_symptoms(
                ["low_confluence", "only_sweep_bos"])
            fix_rules = self.engine.find_fixes(
                ["low_confluence", "only_sweep_bos"])
            wr_now  = conf_wr.get(current_min, 0)
            wr_next = conf_wr.get(new_min, 0)
            recs.append({
                "title":       f"Increase min_confluences from {current_min} → {new_min}",
                "param":       "min_confluences",
                "old_value":   current_min,
                "new_value":   new_min,
                "reason":      (f"'Low confluence' appeared {lc_count}× in losses; "
                                f"win rate: {current_min}c={wr_now:.0f}% vs "
                                f"{new_min}c={wr_next:.0f}%"),
                "rule_numbers":[r["number"] for r in fix_rules[:5]],
                "violated":    [r["number"] for r in violated[:5]],
            })

        # ── Recommendation: require OB or FVG ─────────────────────────────
        no_ob_count  = symptom_freq.get("no_ob", 0)
        no_fvg_count = symptom_freq.get("no_fvg", 0)
        if (no_ob_count >= 2 or no_fvg_count >= 2) and \
                not config.get("require_ob_or_fvg", True):
            fix_rules = self.engine.find_fixes(["no_ob", "no_fvg"])
            recs.append({
                "title":       "Enable require_ob_or_fvg",
                "param":       "require_ob_or_fvg",
                "old_value":   False,
                "new_value":   True,
                "reason":      (f"Setup had no OB {no_ob_count}× and no FVG "
                                f"{no_fvg_count}× among recent losses."),
                "rule_numbers":[r["number"] for r in fix_rules[:5]],
                "violated":    [497, 498, 522],
            })

        # ── Recommendation: disable low-performing confluence pattern ──────
        poor_types = [k for k, wr in type_wr.items() if wr < MIN_CONFLUENCE_WIN_RATE]
        for pt in poor_types:
            # Build the pattern string that scan() would produce
            recs.append({
                "title":       f"Disable low-performing confluence: '{pt}'",
                "param":       "disabled_confluence_patterns",
                "old_value":   config.get("disabled_confluence_patterns", []),
                "new_value":   config.get("disabled_confluence_patterns", []) + [pt],
                "reason":      f"Confluence type '{pt}' has win rate {type_wr[pt]:.1f}%",
                "rule_numbers":[492, 493, 494],
                "violated":    [],
            })

        return recs

    def format_recommendations(self, recs: List[Dict]) -> str:
        bar = "═" * 62
        lines: List[str] = [bar, "  SELF-CORRECTION RECOMMENDATIONS", bar]
        for i, r in enumerate(recs, 1):
            lines.append(f"\n[{i}] {r['title']}")
            lines.append(f"    Reason : {r['reason']}")
            if r.get("rule_numbers"):
                lines.append(f"    Fix rules: {r['rule_numbers']}")
            if r.get("violated"):
                lines.append(f"    Violated : {r['violated']}")
        if not recs:
            lines.append("  No recommendations – strategy looks optimal.")
        lines.append(bar)
        return "\n".join(lines)

    def apply(self, rec: Dict, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a single recommendation and return updated config."""
        param = rec["param"]
        config[param] = rec["new_value"]
        return config


# ─────────────────────────────────────────────────────────────────────────────
# LEARNING TRACKER
# ─────────────────────────────────────────────────────────────────────────────
class LearningTracker:
    """Track win rates over time and identify what works."""

    def get_stats(self, journal: TradeJournal,
                  config: Dict[str, Any]) -> Dict[str, Any]:
        trades  = journal.trades
        closed  = journal.closed_trades()
        losses  = journal.losses()
        wins    = journal.wins()

        total     = len(closed)
        win_count = len(wins)
        win_rate  = (win_count / total * 100) if total else 0.0

        # Average R:R on wins
        win_rr_list = []
        for t in wins:
            if t.take_profits and t.entry_price and t.stop_loss:
                sd = abs(t.entry_price - t.stop_loss)
                if sd > 0:
                    tp1d = abs(t.take_profits[0] - t.entry_price)
                    win_rr_list.append(tp1d / sd)

        avg_rr = (sum(win_rr_list) / len(win_rr_list)) if win_rr_list else 0.0

        # Total P&L
        total_pnl = sum(t.pnl_usd for t in closed)

        return {
            "total_trades":   len(trades),
            "open_trades":    len(journal.open_trades()),
            "closed_trades":  total,
            "wins":           win_count,
            "losses":         len(losses),
            "win_rate":       win_rate,
            "avg_rr":         avg_rr,
            "total_pnl_usd":  total_pnl,
            "consecutive_losses": journal.consecutive_losses(),
            "trades_today":   len(journal.today_trades()),
            "balance":        config["account_balance"] + total_pnl,
        }

    def format_stats(self, stats: Dict[str, Any]) -> str:
        bar = "═" * 62
        lines: List[str] = [bar, "  PERFORMANCE STATISTICS", bar]
        lines.append(f"  Account balance   : ${stats['balance']:,.2f}")
        lines.append(f"  Total P&L         : ${stats['total_pnl_usd']:+,.2f}")
        lines.append(f"  Total trades      : {stats['total_trades']}")
        lines.append(f"  Open trades       : {stats['open_trades']}")
        lines.append(f"  Closed trades     : {stats['closed_trades']}")
        lines.append(f"  Wins / Losses     : {stats['wins']} / {stats['losses']}")
        lines.append(f"  Win rate          : {stats['win_rate']:.1f}%")
        lines.append(f"  Average R:R (TP1) : {stats['avg_rr']:.2f}:1")
        lines.append(f"  Consecutive losses: {stats['consecutive_losses']}")
        lines.append(f"  Trades today      : {stats['trades_today']}")
        lines.append(bar)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN BOT ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────
class TJRBot:
    def __init__(self) -> None:
        self.config   = self._load_config()
        self.journal  = TradeJournal()
        self.analyzer = MarketAnalyzer(self.config)
        self.trader   = PaperTrader(self.journal, self.config)
        self.loss_an  = LossAnalyzer()
        self.patterns = PatternDetector()
        self.corrector= SelfCorrector()
        self.tracker  = LearningTracker()

    # ── Config persistence ─────────────────────────────────────────────────
    def _load_config(self) -> Dict[str, Any]:
        cfg = DEFAULT_CONFIG.copy()
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as fh:
                    stored = json.load(fh)
                cfg.update(stored)
            except Exception:
                pass
        return cfg

    def _save_config(self) -> None:
        with open(CONFIG_FILE, "w") as fh:
            json.dump(self.config, fh, indent=2)

    # ── Banner ─────────────────────────────────────────────────────────────
    def _banner(self) -> None:
        stats = self.tracker.get_stats(self.journal, self.config)
        cprint("\n╔══════════════════════════════════════════════════════════╗",
               C.CYAN)
        cprint("║      TJR TRADING BOT v1.0 – PAPER TRADING MODE          ║",
               C.CYAN)
        cprint("║        Based on 575-Rule Knowledge Base                  ║",
               C.CYAN)
        cprint("╚══════════════════════════════════════════════════════════╝\n",
               C.CYAN)
        cprint(f"  Symbol  : {C.BOLD}{self.config['symbol']}{C.RESET}"
               f"  Balance : ${stats['balance']:,.2f}  "
               f"Open : {stats['open_trades']}",
               C.WHITE)
        cprint(f"  Trades today : {stats['trades_today']}/{self.config['max_trades_per_day']}"
               f"  Win Rate : {stats['win_rate']:.1f}%"
               f"  Streak : {stats['consecutive_losses']} losses\n",
               C.WHITE)

    # ── Menu ───────────────────────────────────────────────────────────────
    def _menu(self) -> None:
        cprint("  [1] Scan for Trading Setup", C.WHITE)
        cprint("  [2] View Open Trades", C.WHITE)
        cprint("  [3] Trade History & Journal", C.WHITE)
        cprint("  [4] Analyze Recent Losses", C.WHITE)
        cprint("  [5] Pattern Analysis", C.WHITE)
        cprint("  [6] Self-Correction (AI)", C.WHITE)
        cprint("  [7] Performance Statistics", C.WHITE)
        cprint("  [8] Configure Settings", C.WHITE)
        cprint("  [9] Run Live Scanner (auto)", C.WHITE)
        cprint("  [0] Exit\n", C.WHITE)

    # ── [1] Scan ───────────────────────────────────────────────────────────
    def cmd_scan(self) -> None:
        cprint(f"\nScanning {self.config['symbol']} for setups …", C.YELLOW)

        # Refresh open trades first
        self.trader.refresh_open_trades(self.config["symbol"])

        # Risk gates
        stats = self.tracker.get_stats(self.journal, self.config)
        if stats["trades_today"] >= self.config["max_trades_per_day"]:
            cprint(f"  ✗ Max trades today ({self.config['max_trades_per_day']}) reached. "
                   "No trade.  (Rule 39)", C.RED)
            return
        if stats["consecutive_losses"] >= self.config["max_consecutive_losses"]:
            cprint(f"  ✗ {stats['consecutive_losses']} consecutive losses. "
                   "Stop trading today.  (Rule 40)", C.RED)
            return

        setup = self.analyzer.scan(self.config["symbol"], self.config)

        if setup is None:
            cprint("  ✗ No valid setup found.", C.YELLOW)
            cprint("  Criteria: Sweep + BOS + (OB or FVG) + "
                   f"≥{self.config['min_confluences']} confluences\n", C.WHITE)
            return

        # Show setup
        cprint("\n  ✓ SETUP FOUND!", C.GREEN + C.BOLD)
        cprint(f"  {setup.description}", C.GREEN)
        cprint(f"\n  Symbol    : {setup.symbol}", C.WHITE)
        cprint(f"  Direction : {setup.direction.upper()}", C.WHITE)
        cprint(f"  Entry     : {setup.entry:.5f}", C.WHITE)
        cprint(f"  Stop      : {setup.stop:.5f}", C.WHITE)
        for i, tp in enumerate(setup.take_profits, 1):
            cprint(f"  TP{i}       : {tp:.5f}", C.WHITE)
        cprint(f"  R:R       : {setup.rr:.2f}:1", C.WHITE)
        cprint(f"\n  Confluences ({setup.confluence_count}): "
               f"{', '.join(setup.confluences)}", C.WHITE)
        cprint(f"  Daily bias: {setup.daily_bias}  "
               f"4H: {setup.four_h_trend}  Weekly: {setup.weekly_trend}", C.WHITE)
        cprint(f"  OB: {'✓' if setup.has_ob else '✗'}  "
               f"FVG: {'✓' if setup.has_fvg else '✗'}  "
               f"EQ: {'✓' if setup.has_eq else '✗'}\n", C.WHITE)

        confirm = input("  Execute paper trade? [y/n]: ").strip().lower()
        if confirm == "y":
            trade = self.trader.execute(setup)
            cprint(f"\n  ✓ Trade {trade.id} opened. "
                   f"Size: {trade.position_size} units  "
                   f"Risk: ${trade.risk_amount:.2f}", C.GREEN)
        else:
            cprint("  Trade skipped.", C.YELLOW)

    # ── [2] View open trades ───────────────────────────────────────────────
    def cmd_view_open(self) -> None:
        self.trader.refresh_open_trades(self.config["symbol"])
        opens = self.journal.open_trades()
        if not opens:
            cprint("\n  No open trades.\n", C.YELLOW)
            return
        cprint(f"\n  OPEN TRADES ({len(opens)})\n", C.CYAN)
        for t in opens:
            cprint(f"  [{t.id}] {t.symbol} {t.direction.upper()}", C.WHITE)
            cprint(f"    Entry: {t.entry_price:.5f}  "
                   f"Stop: {t.stop_loss:.5f}  "
                   f"{'BE ✓' if t.breakeven_set else ''}", C.WHITE)
            for i, tp in enumerate(t.take_profits, 1):
                hit = "✓" if (i - 1) in t.closed_tps else "○"
                cprint(f"    TP{i}: {tp:.5f} {hit}", C.WHITE)
            cprint(f"    Confluences: {', '.join(t.confluences)}\n", C.WHITE)

    # ── [3] Trade history ──────────────────────────────────────────────────
    def cmd_history(self) -> None:
        closed = self.journal.closed_trades()
        if not closed:
            cprint("\n  No closed trades yet.\n", C.YELLOW)
            return
        cprint(f"\n  TRADE HISTORY (last 20)\n", C.CYAN)
        for t in closed[-20:]:
            colour = C.GREEN if t.status in ("closed_win", "closed_be") else C.RED
            icon   = "✓" if t.status in ("closed_win", "closed_be") else "✗"
            cprint(
                f"  {icon} {t.id} | {t.symbol} {t.direction.upper()} | "
                f"{t.status.upper()} | P&L: ${t.pnl_usd:+.2f} | "
                f"{t.confluence_count}c | {t.entry_time[:10]}",
                colour,
            )

    # ── [4] Analyze losses ─────────────────────────────────────────────────
    def cmd_analyze_losses(self) -> None:
        losses = self.journal.losses()
        if not losses:
            cprint("\n  No losses to analyze.\n", C.YELLOW)
            return

        cprint(f"\n  Analyzing {len(losses)} loss(es) …\n", C.YELLOW)
        for trade in losses[-5:]:          # show last 5 losses
            analysis = self.loss_an.analyze(trade, self.config)
            self.journal.update(trade)
            report = self.loss_an.format_report(analysis)
            cprint(report, C.WHITE)

    # ── [5] Pattern analysis ───────────────────────────────────────────────
    def cmd_patterns(self) -> None:
        trades = self.journal.trades
        if not trades:
            cprint("\n  Not enough data for pattern analysis.\n", C.YELLOW)
            return
        result = self.patterns.analyze(trades, window=10)
        report = self.patterns.format_report(result)
        cprint(report, C.WHITE)

    # ── [6] Self-correction ────────────────────────────────────────────────
    def cmd_self_correct(self) -> None:
        trades = self.journal.trades
        if not trades:
            cprint("\n  Not enough data for self-correction.\n", C.YELLOW)
            return

        patterns_data = self.patterns.analyze(trades)
        recs = self.corrector.generate_recommendations(patterns_data, self.config)
        report = self.corrector.format_recommendations(recs)
        cprint(report, C.CYAN)

        if not recs:
            return

        for i, rec in enumerate(recs, 1):
            ans = input(
                f"\n  Apply correction [{i}] '{rec['title']}' ? [y/n]: "
            ).strip().lower()
            if ans == "y":
                self.config = self.corrector.apply(rec, self.config)
                self._save_config()
                cprint(f"  ✓ Applied: {rec['param']} = {rec['new_value']}",
                       C.GREEN)
                # Show which rules validated this fix
                if rec.get("rule_numbers"):
                    rn = rec["rule_numbers"]
                    cprint(f"    Based on Rules: {rn}", C.WHITE)
            else:
                cprint("  Skipped.", C.YELLOW)

    # ── [7] Performance ────────────────────────────────────────────────────
    def cmd_performance(self) -> None:
        stats = self.tracker.get_stats(self.journal, self.config)
        report = self.tracker.format_stats(stats)
        cprint(report, C.WHITE)

    # ── [8] Configure ──────────────────────────────────────────────────────
    def cmd_configure(self) -> None:
        cprint("\n  CURRENT CONFIGURATION\n", C.CYAN)
        for k, v in self.config.items():
            cprint(f"    {k:<40s}: {v}", C.WHITE)

        cprint("\n  Enter 'key=value' to change a setting (or ENTER to skip):", C.YELLOW)
        while True:
            inp = input("  > ").strip()
            if not inp:
                break
            if "=" not in inp:
                cprint("  Format: key=value", C.RED)
                continue
            key, val_str = inp.split("=", 1)
            key = key.strip()
            if key not in self.config:
                cprint(f"  Unknown key: {key}", C.RED)
                continue
            # Type coercion
            old = self.config[key]
            try:
                if isinstance(old, bool):
                    self.config[key] = val_str.strip().lower() in ("true", "1", "yes")
                elif isinstance(old, int):
                    self.config[key] = int(val_str)
                elif isinstance(old, float):
                    self.config[key] = float(val_str)
                else:
                    self.config[key] = val_str.strip()
                self._save_config()
                cprint(f"  ✓ {key} = {self.config[key]}", C.GREEN)
            except ValueError as e:
                cprint(f"  ✗ {e}", C.RED)

    # ── [9] Live scanner ────────────────────────────────────────────────────
    def cmd_live_scan(self) -> None:
        interval = self.config.get("scan_interval_seconds", 60)
        cprint(f"\n  Live scanner – scanning every {interval}s."
               "  Press Ctrl+C to stop.\n", C.CYAN)
        try:
            while True:
                self.trader.refresh_open_trades(self.config["symbol"])
                stats = self.tracker.get_stats(self.journal, self.config)
                # Auto risk gates
                if stats["consecutive_losses"] >= self.config["max_consecutive_losses"]:
                    cprint("  ✗ Max consecutive losses reached. Stopped. (Rule 40)",
                           C.RED)
                    break
                if stats["trades_today"] >= self.config["max_trades_per_day"]:
                    cprint("  ✗ Max daily trades reached.", C.YELLOW)
                    time.sleep(interval)
                    continue
                setup = self.analyzer.scan(self.config["symbol"], self.config)
                now = datetime.now().strftime("%H:%M:%S")
                if setup:
                    cprint(f"\n  [{now}] ⚡ SETUP FOUND: {setup.description}", C.GREEN)
                    cprint(f"    Entry={setup.entry:.5f}  "
                           f"Stop={setup.stop:.5f}  "
                           f"TP1={setup.take_profits[0]:.5f}", C.GREEN)
                    ans = input("  Execute? [y/n]: ").strip().lower()
                    if ans == "y":
                        t = self.trader.execute(setup)
                        cprint(f"  ✓ Trade {t.id} opened.", C.GREEN)
                else:
                    cprint(f"  [{now}] No setup … scanning again in {interval}s",
                           C.WHITE)
                time.sleep(interval)
        except KeyboardInterrupt:
            cprint("\n  Live scanner stopped.\n", C.YELLOW)

    # ── Main loop ──────────────────────────────────────────────────────────
    def run(self) -> None:
        os.system("cls" if os.name == "nt" else "clear")
        self._banner()
        self._menu()
        while True:
            try:
                choice = input(f"{C.CYAN}  Choice: {C.RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                cprint("\n  Goodbye!\n", C.CYAN)
                break
            if choice == "1":
                self.cmd_scan()
            elif choice == "2":
                self.cmd_view_open()
            elif choice == "3":
                self.cmd_history()
            elif choice == "4":
                self.cmd_analyze_losses()
            elif choice == "5":
                self.cmd_patterns()
            elif choice == "6":
                self.cmd_self_correct()
            elif choice == "7":
                self.cmd_performance()
            elif choice == "8":
                self.cmd_configure()
            elif choice == "9":
                self.cmd_live_scan()
            elif choice == "0":
                cprint("\n  Goodbye!\n", C.CYAN)
                break
            else:
                cprint("  Invalid choice.", C.RED)
            # Re-display menu after each action
            print()
            self._banner()
            self._menu()


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot = TJRBot()
    bot.run()
