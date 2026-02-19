# TJR Trading Bot

Ultimate self-correcting paper trading bot based on **575 rules** extracted from 40+ days of TJR's trading bootcamp.

## Features

| Feature | Details |
|---|---|
| **Daily Bias Engine** | Weekly → Daily → 4H alignment (Rules 313–392) |
| **Liquidity Sweep Detection** | Wick through high/low, close back (Rule 27) |
| **Break of Structure** | Close-confirmed BOS, not just wicks (Rule 20–24) |
| **Order Block Detection** | Last opposing candle before BOS move (Rule 90–103) |
| **Fair Value Gap Detection** | 3-candle gap pattern (Rule 57–68) |
| **Equilibrium Zones** | 50 % of swing – discount/premium check (Rule 117–158) |
| **Confluence Scoring** | Minimum 3 required; OB or FVG mandatory (Rule 498) |
| **Paper Trading** | 1 % risk, partial TPs, breakeven at TP1 (Rule 37, 393–416) |
| **Loss Analyser** | Matches symptoms → specific rule numbers (Rules 495–523) |
| **Pattern Detector** | Win-rate by confluence count/type over last N trades |
| **Self-Corrector** | Recommendations with rule references; user approval required |
| **Adaptive Learning** | Disable losing patterns, prefer high-performing setups |

## Quick Start

```bash
pip install -r requirements.txt
python tjr_bot.py
```

## Files

| File | Description |
|---|---|
| `tjr_bot.py` | Main bot – all trading logic, CLI, position management |
| `rules_database.py` | Complete 575-rule knowledge base with symptom/fix metadata |
| `requirements.txt` | Python dependencies |
| `tjr_config.json` | Auto-generated config (ignored by git) |
| `tjr_journal.json` | Auto-generated trade journal (ignored by git) |

## Menu

```
[1] Scan for Trading Setup          – runs full top-down analysis
[2] View Open Trades                – shows live paper positions
[3] Trade History & Journal         – closed trade log
[4] Analyze Recent Losses           – 575-rule diagnosis per loss
[5] Pattern Analysis                – win rates, repeated mistakes
[6] Self-Correction (AI)            – recommendations + user approval
[7] Performance Statistics          – balance, win rate, R:R
[8] Configure Settings              – change any parameter live
[9] Run Live Scanner (auto)         – continuous scanning loop
[0] Exit
```

## Intelligent Loss Analysis Example

```
══════════════════════════════════════════════════════════════
  LOSS ANALYSIS – Trade TJR-20250115143022
  GBPUSD=X  SHORT
══════════════════════════════════════════════════════════════

  Confluences (2): sweep, bos
  OB: ✗  FVG: ✗  EQ: ✗

SYMPTOMS DETECTED:
  ⚠  Fewer than 3 confluences used
  ⚠  Only Sweep + BOS, no OB or FVG
  ⚠  Stopped out before reaching TP1

SEARCHING 575 RULES … 23 VIOLATIONS FOUND:

  Rule # 241: Entry Method 1 (RISKIEST): Sweep + BOS only.
  Rule # 495: Tweak 2: ADD CONFLUENCE – TJR entering just Sweep + BOS.
  Rule # 496: Getting stopped, price going in direction after.
  Rule # 497: Solution: Add OB or FVG requirement.
  Rule # 498: Now requires: Sweep + BOS + (OB OR FVG) minimum.
  Rule # 522: Solution: Wait for OB/FVG, not just Sweep + BOS.
  …

RECOMMENDED FIXES:
  ► Rule #497: Solution: Add OB or FVG requirement.
  ► Rule #498: Now requires: Sweep + BOS + (OB OR FVG) minimum.
```

## Self-Correction Example

```
  Apply correction [1] 'Increase min_confluences from 2 → 3' ? [y/n]: y
  ✓ Applied: min_confluences = 3
    Based on Rules: [495, 498, 499, 522, 254]
```

## Supported Symbols

Any symbol supported by `yfinance`:
- `GC=F` – Gold futures (default)
- `GBPJPY=X` – GBP/JPY forex
- `GBPUSD=X` – GBP/USD forex
- `EURUSD=X` – EUR/USD forex
- `SPY` – S&P 500 ETF
- `ES=F` – S&P 500 futures

Change via menu `[8] Configure Settings → symbol=GBPJPY=X`

## Risk Management (Rules 37–44)

- **1 % risk** per trade (configurable)
- **Stop** at invalidation point – above/below swept liquidity
- **TP1** at 1.5:1 R:R minimum – partial close 50 %
- **TP2** – partial close 30 %
- **TP3** – full close
- **Breakeven** moved to entry at TP1
- **Max 1 trade/day**, stop after 2 consecutive losses
