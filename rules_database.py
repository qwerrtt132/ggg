"""
TJR Trading Strategy – Complete 575-Rule Knowledge Base
Extracted from 40+ days of TJR's trading bootcamp.

Each rule carries:
  number   – original rule number from the document
  text     – exact text of the rule
  category – logical grouping
  symptoms – symptom keys that trigger this rule during loss analysis
  is_fix   – True when this rule describes a corrective action
  fix_type – machine-readable tag used by SelfCorrector
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional

# ---------------------------------------------------------------------------
# SYMPTOM KEYS  (must match what LossAnalyzer.extract_symptoms() emits)
# ---------------------------------------------------------------------------
SYMPTOM_KEYS: Dict[str, str] = {
    "low_confluence":             "Fewer than 3 confluences used",
    "only_sweep_bos":             "Only Sweep + BOS, no OB or FVG",
    "no_ob":                      "No Order Block present in setup",
    "no_fvg":                     "No Fair Value Gap present in setup",
    "no_eq":                      "No Equilibrium zone check performed",
    "stopped_early":              "Stopped out before reaching TP1",
    "continued_after_stop":       "Price continued in original direction after stop",
    "wrong_bias":                 "Traded against daily / weekly bias",
    "daily_4h_conflict":          "Daily and 4H timeframes not aligned",
    "timeframe_conflict":         "Multiple timeframe conflict detected",
    "news_event":                 "Traded during or near high-impact news",
    "no_bos":                     "No Break of Structure confirmed",
    "no_sweep":                   "No liquidity sweep detected before entry",
    "wrong_direction":            "Trading against the dominant trend",
    "overtraded":                 "Exceeded max-trades-per-day limit",
    "consecutive_losses":         "2+ consecutive losses before this trade",
    "revenge_trade":              "Trade taken too quickly after a loss",
    "poor_rr":                    "Risk:reward ratio below minimum",
    "premium_buy":                "Bought in premium zone (above 50 % EQ)",
    "discount_sell":              "Sold in discount zone (below 50 % EQ)",
    "emotional":                  "Emotional trading indicators detected",
    "chop":                       "Market in consolidation / chop",
    "forming_candle":             "Entry on a still-forming candle",
    "no_daily_bias":              "Daily bias not established before entry",
    "stop_too_tight":             "Stop not placed at true invalidation point",
    "stop_too_wide":              "Stop too wide, killing R:R",
    "wick_bos":                   "BOS confirmed by wick only, not close",
    "prominent_level_missed":     "Setup not at a prominent liquidity level",
    "session_violation":          "Traded outside designated session",
    "bank_holiday":               "Traded on bank holiday / reduced volume",
}

# ---------------------------------------------------------------------------
# FULL 575-RULE DATABASE
# ---------------------------------------------------------------------------
_R: List[Dict[str, Any]] = []

def _rule(number: int, text: str, category: str,
          symptoms: Optional[List[str]] = None,
          is_fix: bool = False,
          fix_type: str = "") -> None:
    _R.append({
        "number":   number,
        "text":     text,
        "category": category,
        "symptoms": symptoms or [],
        "is_fix":   is_fix,
        "fix_type": fix_type,
    })

# ── CANDLESTICK PATTERNS (Rules 1-7) ──────────────────────────────────────
_rule(1,  "Only single-candle patterns (doji, hammer, dragonfly).",
      "Candlestick Patterns")
_rule(2,  "Never trade forming candles – wait for close.",
      "Candlestick Patterns", ["forming_candle"])
_rule(3,  "Wicks show rejection at levels.",
      "Candlestick Patterns")
_rule(4,  "Patterns need confluence – never trade alone.",
      "Candlestick Patterns", ["low_confluence"])
_rule(5,  "Doji = indecision, not entry signal.",
      "Candlestick Patterns")
_rule(6,  "Hammer = potential reversal, need confirmation.",
      "Candlestick Patterns")
_rule(7,  "Dragonfly doji = strong rejection from below.",
      "Candlestick Patterns")

# ── TREND IDENTIFICATION (Rules 8-17) ─────────────────────────────────────
_rule(8,  "Three trend states: up, down, consolidation.",
      "Trend Identification")
_rule(9,  "Uptrend = higher highs + higher lows.",
      "Trend Identification")
_rule(10, "Downtrend = lower highs + lower lows.",
      "Trend Identification")
_rule(11, "Higher timeframes override lower timeframes.",
      "Trend Identification", ["timeframe_conflict"])
_rule(12, "Timeframe hierarchy: Weekly→Daily→4H→30min→5min.",
      "Trend Identification", ["timeframe_conflict", "daily_4h_conflict"])
_rule(13, "Must check all timeframes before entry.",
      "Trend Identification", ["timeframe_conflict", "no_daily_bias"])
_rule(14, "Consolidation = range-bound, no clear direction.",
      "Trend Identification", ["chop"])
_rule(15, "Don't trade consolidation (chop).",
      "Trend Identification", ["chop"])
_rule(16, "Trend is your friend – trade with it.",
      "Trend Identification", ["wrong_direction"])
_rule(17, "Consolidation = NO TRADE (critical rule).",
      "Trend Identification", ["chop"])

# ── BREAK OF STRUCTURE (Rules 18-24) ──────────────────────────────────────
_rule(18, "High = price goes up then down (2 candles minimum).",
      "Break of Structure")
_rule(19, "Low = price goes down then up (2 candles minimum).",
      "Break of Structure")
_rule(20, "BOS requires CLOSE beyond level, not just wick.",
      "Break of Structure", ["wick_bos", "no_bos"])
_rule(21, "Only most recent high/low matters for BOS.",
      "Break of Structure", ["no_bos"])
_rule(22, "Wick through doesn't count as BOS.",
      "Break of Structure", ["wick_bos", "no_bos"])
_rule(23, "Must close beyond structure for confirmation.",
      "Break of Structure", ["no_bos", "wick_bos"])
_rule(24, "Consolidation = no valid BOS possible.",
      "Break of Structure", ["chop", "no_bos"])

# ── LIQUIDITY (Rules 25-36) ───────────────────────────────────────────────
_rule(25, "Liquidity = resting orders above highs / below lows.",
      "Liquidity")
_rule(26, "Banks drive price to liquidity to fill orders.",
      "Liquidity")
_rule(27, "Liquidity sweep = wick through high/low, close back.",
      "Liquidity", ["no_sweep"])
_rule(28, "Sweep alone is NOT a signal.",
      "Liquidity", ["no_bos", "only_sweep_bos"])
_rule(29, "Need BOS confirmation after sweep.",
      "Liquidity", ["no_bos", "only_sweep_bos"])
_rule(30, "Prominent highs/lows matter most.",
      "Liquidity", ["prominent_level_missed"])
_rule(31, "Prominent = stands out visually on chart.",
      "Liquidity", ["prominent_level_missed"])
_rule(32, "Equal highs/lows = double liquidity.",
      "Liquidity")
_rule(33, "Liquidity is used for take profit targets.",
      "Liquidity")
_rule(34, "Target liquidity in direction of trade.",
      "Liquidity")
_rule(35, "Don't target liquidity against your bias.",
      "Liquidity", ["wrong_bias"])
_rule(36, "Never enter AT liquidity – enter after sweep + BOS.",
      "Liquidity", ["no_sweep", "no_bos"])

# ── RISK MANAGEMENT (Rules 37-44) ─────────────────────────────────────────
_rule(37, "Risk 1-3 % per trade maximum.",
      "Risk Management")
_rule(38, "Risk 1-3 % per day total (not per trade if multiple).",
      "Risk Management", ["overtraded"])
_rule(39, "Max 1-3 trades per day.",
      "Risk Management", ["overtraded"])
_rule(40, "Stop trading after 2 consecutive losses.",
      "Risk Management", ["consecutive_losses"])
_rule(41, "Demo trade until consistently profitable.",
      "Risk Management")
_rule(42, "Never risk money you need for bills.",
      "Risk Management")
_rule(43, "Use position size calculator.",
      "Risk Management")
_rule(44, "Know your risk BEFORE entering.",
      "Risk Management")

# ── PSYCHOLOGY (Rules 45-56) ──────────────────────────────────────────────
_rule(45, "Losses are part of trading – accept them.",
      "Psychology")
_rule(46, "Never revenge trade after a loss.",
      "Psychology", ["revenge_trade"])
_rule(47, "Be robotic and emotionless.",
      "Psychology", ["emotional"])
_rule(48, "Think year-over-year, not day-to-day.",
      "Psychology")
_rule(49, "Every loss is YOUR fault, not market's.",
      "Psychology")
_rule(50, "Build trading habits (takes 21 days).",
      "Psychology")
_rule(51, "Take 1 day off per week minimum.",
      "Psychology")
_rule(52, "Don't trade when emotional.",
      "Psychology", ["emotional"])
_rule(53, "Don't trade money you can't afford to lose.",
      "Psychology")
_rule(54, "Patience is the most important skill.",
      "Psychology")
_rule(55, "Default mindset: 'No trade' until proven.",
      "Psychology")
_rule(56, "Trading should be boring, not exciting.",
      "Psychology", ["emotional"])

# ── FAIR VALUE GAPS (Rules 57-68) ─────────────────────────────────────────
_rule(57, "FVG = 3-candle pattern with gap between candle 1 and 3.",
      "Fair Value Gap")
_rule(58, "FVG is retracement tool, not reversal signal.",
      "Fair Value Gap")
_rule(59, "Measure wick to wick (candle 1 to candle 3).",
      "Fair Value Gap")
_rule(60, "Wicks of candle 1 and 3 cannot overlap body of candle 2.",
      "Fair Value Gap")
_rule(61, "If wicks overlap middle candle body = not valid FVG.",
      "Fair Value Gap")
_rule(62, "Wait for price to fill FVG before entering.",
      "Fair Value Gap")
_rule(63, "Can enter when FVG is touched.",
      "Fair Value Gap")
_rule(64, "Or wait for BOS within FVG for confirmation.",
      "Fair Value Gap")
_rule(65, "Scale to lower timeframe for precise entry in FVG.",
      "Fair Value Gap")
_rule(66, "FVG shows lack of liquidity = price wants to return.",
      "Fair Value Gap")
_rule(67, "FVG is 3rd priority retracement tool (after OB, before EQ).",
      "Fair Value Gap", ["no_fvg"])
_rule(68, "Multiple FVGs = choose one closest to current price.",
      "Fair Value Gap")

# ── PATIENCE (Rules 69-76) ────────────────────────────────────────────────
_rule(69, "Have checklist and follow exactly.",
      "Patience")
_rule(70, "Default to 'no trade' mindset.",
      "Patience")
_rule(71, "Wait for market to prove setup.",
      "Patience")
_rule(72, "Don't force trades.",
      "Patience")
_rule(73, "Trading should be boring not exciting.",
      "Patience", ["emotional"])
_rule(74, "Takes 2+ years minimum to become profitable.",
      "Patience")
_rule(75, "Most traders fail due to lack of patience.",
      "Patience")
_rule(76, "If in doubt, don't trade.",
      "Patience")

# ── NEWS TRADING (Rules 77-89) ────────────────────────────────────────────
_rule(77, "Never trade CPI/PPI/FOMC/NFP (blackout events).",
      "News Trading", ["news_event"])
_rule(78, "Use Forex Factory calendar daily.",
      "News Trading", ["news_event"])
_rule(79, "Red folder news = wait 15-30 min after release.",
      "News Trading", ["news_event"])
_rule(80, "Orange folder news = reduce risk by 50%.",
      "News Trading", ["news_event"])
_rule(81, "Check news calendar every morning before trading.",
      "News Trading", ["news_event"])
_rule(82, "Price action overrides news bias.",
      "News Trading")
_rule(83, "Don't take directional bias from news.",
      "News Trading")
_rule(84, "News causes volatility = higher risk.",
      "News Trading", ["news_event"])
_rule(85, "Better to miss trade than trade during news.",
      "News Trading", ["news_event"])
_rule(86, "Bank holidays = reduced volume, avoid trading.",
      "News Trading", ["bank_holiday"])
_rule(87, "News spike = stop trading for the day.",
      "News Trading", ["news_event"])
_rule(88, "High impact news = major market movers.",
      "News Trading", ["news_event"])
_rule(89, "If news against your trade, exit immediately.",
      "News Trading", ["news_event"])

# ── ORDER BLOCKS (Rules 90-103) ───────────────────────────────────────────
_rule(90,  "Order Block = the move that caused liquidity sweep.",
       "Order Blocks", ["no_ob"])
_rule(91,  "Only ONE order block per trend/move.",
       "Order Blocks")
_rule(92,  "Order Block is #1 priority for entries.",
       "Order Blocks", ["no_ob"])
_rule(93,  "Price reacts at OB because orders were filled there.",
       "Order Blocks")
_rule(94,  "Use wick-to-body marking method.",
       "Order Blocks")
_rule(95,  "Mark from wick of first candle to body of last candle.",
       "Order Blocks")
_rule(96,  "Price reacts at BODY of OB, not wick.",
       "Order Blocks")
_rule(97,  "OB requires confirmation on lower timeframe.",
       "Order Blocks")
_rule(98,  "Scale down to see BOS within OB.",
       "Order Blocks")
_rule(99,  "Stop placement depends on OB timeframe.",
       "Order Blocks")
_rule(100, "Higher timeframe OB = wider stop.",
       "Order Blocks")
_rule(101, "Lower timeframe OB = tighter stop.",
       "Order Blocks")
_rule(102, "OB is where smart money entered.",
       "Order Blocks")
_rule(103, "Multiple OBs = use most recent one.",
       "Order Blocks")

# ── WINNING TRADES PSYCHOLOGY (Rules 104-116) ─────────────────────────────
_rule(104, "Treat wins same as losses emotionally.",
       "Winning Trades")
_rule(105, "Don't get overconfident after wins.",
       "Winning Trades", ["emotional"])
_rule(106, "Never increase risk after winning.",
       "Winning Trades")
_rule(107, "Analyze why you won, not just celebrate.",
       "Winning Trades")
_rule(108, "Don't watch account balance obsessively.",
       "Winning Trades")
_rule(109, "Check performance monthly/yearly not daily.",
       "Winning Trades")
_rule(110, "Winning streak doesn't mean you're invincible.",
       "Winning Trades")
_rule(111, "One win doesn't validate bad strategy.",
       "Winning Trades")
_rule(112, "Stay humble after wins.",
       "Winning Trades")
_rule(113, "Don't change strategy after one win.",
       "Winning Trades")
_rule(114, "Wins can make you sloppy – stay disciplined.",
       "Winning Trades", ["emotional"])
_rule(115, "Journal wins just like losses.",
       "Winning Trades")
_rule(116, "Understand WHY trade worked.",
       "Winning Trades")

# ── EQUILIBRIUM (Rules 117-126) ───────────────────────────────────────────
_rule(117, "Equilibrium = 50% of swing (high to low).",
       "Equilibrium")
_rule(118, "Above 50% = premium (expensive).",
       "Equilibrium", ["premium_buy"])
_rule(119, "Below 50% = discount (cheap).",
       "Equilibrium", ["discount_sell"])
_rule(120, "In uptrend: buy at discount (below 50%).",
       "Equilibrium", ["premium_buy"])
_rule(121, "In downtrend: sell at premium (above 50%).",
       "Equilibrium", ["discount_sell"])
_rule(122, "Smart money buys discount, not premium.",
       "Equilibrium", ["premium_buy"])
_rule(123, "Use Gann box or Fibonacci for equilibrium.",
       "Equilibrium")
_rule(124, "EQ is 3rd priority retracement tool (after OB, FVG).",
       "Equilibrium", ["no_eq"])
_rule(125, "EQ + OB = high probability setup.",
       "Equilibrium")
_rule(126, "EQ + FVG = good probability setup.",
       "Equilibrium")

# ── OVERCONFIDENCE & FEAR (Rules 127-138) ─────────────────────────────────
_rule(127, "Overconfidence as dangerous as fear.",
       "Psychology", ["emotional"])
_rule(128, "Expect losses after win streaks.",
       "Psychology")
_rule(129, "Fear = trading money you can't lose.",
       "Psychology", ["emotional"])
_rule(130, "Demo path: 3mo profit→tiny live→demo→funded→real.",
       "Psychology")
_rule(131, "TJR took 2 years to become profitable.",
       "Psychology")
_rule(132, "Don't skip demo trading phase.",
       "Psychology")
_rule(133, "Fear causes hesitation and missed trades.",
       "Psychology", ["emotional"])
_rule(134, "Overconfidence causes revenge trading.",
       "Psychology", ["revenge_trade", "emotional"])
_rule(135, "Both emotions destroy accounts.",
       "Psychology", ["emotional"])
_rule(136, "Stay neutral emotionally.",
       "Psychology", ["emotional"])
_rule(137, "Follow plan regardless of recent results.",
       "Psychology", ["revenge_trade"])
_rule(138, "Emotions are the enemy.",
       "Psychology", ["emotional"])

# ── EQUILIBRIUM PART 2 (Rules 139-158) ────────────────────────────────────
_rule(139, "Equilibrium measured from swing low to swing high.",
       "Equilibrium")
_rule(140, "Above 50% = PREMIUM (expensive, don't buy).",
       "Equilibrium", ["premium_buy"])
_rule(141, "Below 50% = DISCOUNT (cheap, smart money buys here).",
       "Equilibrium", ["discount_sell"])
_rule(142, "In uptrend: look for discount entries.",
       "Equilibrium", ["premium_buy"])
_rule(143, "In downtrend: look for premium shorts.",
       "Equilibrium", ["discount_sell"])
_rule(144, "Equilibrium is NOT entry by itself.",
       "Equilibrium", ["no_eq"])
_rule(145, "Equilibrium is ZONE to watch for confirmation.",
       "Equilibrium")
_rule(146, "Works on EVERY timeframe.",
       "Equilibrium")
_rule(147, "When price at equilibrium, scale down for BOS.",
       "Equilibrium")
_rule(148, "Equilibrium + FVG = high probability.",
       "Equilibrium")
_rule(149, "Drag from most recent low to most recent high.",
       "Equilibrium")
_rule(150, "When new high/low forms, redraw equilibrium.",
       "Equilibrium")
_rule(151, "Smart money thinks like shopping – wants discount.",
       "Equilibrium", ["premium_buy"])
_rule(152, "Use Gann Box or Fib tool for equilibrium.",
       "Equilibrium")
_rule(153, "Settings: Only show 0, 0.5, and 1 levels.",
       "Equilibrium")
_rule(154, "Price levels: 0 (bottom), 0.5 (middle), 1 (top).",
       "Equilibrium")
_rule(155, "Equilibrium + Sweep + BOS = valid entry.",
       "Equilibrium")
_rule(156, "If FVG at equilibrium discount = extremely high probability.",
       "Equilibrium")
_rule(157, "If OB at equilibrium discount = highest probability.",
       "Equilibrium")
_rule(158, "Target previous liquidity after entering from equilibrium.",
       "Equilibrium")

# ── TRADING PLAN (Rules 159-193) ──────────────────────────────────────────
_rule(159, "Have step-by-step plan written down.",
       "Trading Plan")
_rule(160, "Trading plan must have 4 sections.",
       "Trading Plan")
_rule(161, "Pre-designate risk before every trading day.",
       "Trading Plan")
_rule(162, "Choose: Set lot size OR Calculated lot size.",
       "Trading Plan")
_rule(163, "TJR uses set lot sizes (3 predetermined).",
       "Trading Plan")
_rule(164, "Write down exact monetary value risking per day.",
       "Trading Plan")
_rule(165, "Write down exact number of trades per day.",
       "Trading Plan", ["overtraded"])
_rule(166, "If taking 2 trades: reduce risk so total = normal risk.",
       "Trading Plan", ["overtraded"])
_rule(167, "Never say '1-2 trades' – commit to ONE.",
       "Trading Plan", ["overtraded"])
_rule(168, "Designate specific trading times.",
       "Trading Plan", ["session_violation"])
_rule(169, "Choose ONE session: Asian, London, NY AM, NY PM.",
       "Trading Plan", ["session_violation"])
_rule(170, "TJR trades NY open (first 1.5 hours only).",
       "Trading Plan", ["session_violation"])
_rule(171, "If can't find trade in designated time = NO TRADE.",
       "Trading Plan")
_rule(172, "Decide: Trade after news or avoid news.",
       "Trading Plan", ["news_event"])
_rule(173, "If avoiding news: define your rule.",
       "Trading Plan")
_rule(174, "Example: '5-point spike in 5 min = done for day'.",
       "Trading Plan")
_rule(175, "Check news calendar EVERY morning.",
       "Trading Plan", ["news_event"])
_rule(176, "Choose ONE pair/asset and master it.",
       "Trading Plan")
_rule(177, "Most traders: trade ONLY ONE pair until profitable.",
       "Trading Plan")
_rule(178, "Delete watchlist except ONE pair.",
       "Trading Plan")
_rule(179, "Each market moves differently.",
       "Trading Plan")
_rule(180, "TJR traded ONLY GBP/JPY first 2 years.",
       "Trading Plan")
_rule(181, "Adding extra pairs = adding risk.",
       "Trading Plan")
_rule(182, "Only add second pair after profitable on first.",
       "Trading Plan")
_rule(183, "Write down exact confluence requirements.",
       "Trading Plan", ["low_confluence"])
_rule(184, "May take backtesting to refine.",
       "Trading Plan")
_rule(185, "Test confluence combinations for 2 weeks each.",
       "Trading Plan")
_rule(186, "Most high-probability: Sweep+BOS+FVG/OB+Daily bias+Overall trend.",
       "Trading Plan", ["low_confluence", "no_ob", "no_fvg"])
_rule(187, "Write: 'I only take trades with [X] confluences'.",
       "Trading Plan", ["low_confluence"])
_rule(188, "Don't take trades on FOMO.",
       "Trading Plan", ["emotional"])
_rule(189, "Trading plan turns you into ROBOT.",
       "Trading Plan")
_rule(190, "If any step not met = NO TRADE.",
       "Trading Plan", ["low_confluence"])
_rule(191, "Review plan every morning.",
       "Trading Plan")
_rule(192, "Follow EVERY step before pressing button.",
       "Trading Plan")
_rule(193, "This is foolproof.",
       "Trading Plan")

# ── WORK ETHIC (Rules 194-234) ────────────────────────────────────────────
for n, t in [
    (194, "To reach top, do 4X work top person is doing."),
    (195, "Minimum effort = minimum results."),
    (196, "Most fail because not enough work."),
    (197, "TJR watched 8+ hours trading videos daily."),
    (198, "TJR read 10+ day trading books, 10+ psychology books."),
    (199, "Do 4 hours per day MINIMUM study/backtest."),
    (200, "Check screen time – replace with studying."),
    (201, "Make success UNDENIABLE through work volume."),
    (202, "Work so hard success becomes INEVITABLE."),
    (203, "Kobe practiced 3X per day vs teammates 1-2X."),
    (204, "Greatness = genetics + work ethic (80% work)."),
    (205, "If not seeing progress, not doing enough."),
    (206, "Look at other traders – do MORE."),
    (207, "Be competitor – outwork everyone."),
    (208, "Fail 1,000 times if needed – learn from each."),
    (209, "1 success out of 1,000 = ahead of start."),
    (210, "10 successes out of 1,000 = great track."),
    (211, "Learn from every mistake immediately."),
    (212, "Journal why you lost."),
    (213, "Work so hard people have no choice but to recognize."),
    (214, "Practice so hard they have NO CHOICE but to play you."),
    (215, "Don't roll out of bed and trade half-asleep."),
    (216, "Wake early, study, prepare BEFORE market."),
    (217, "TJR woke 4 hours early to film videos."),
    (218, "Get 4 hours work done before noon."),
    (219, "Combine activities (study on bike doing cardio)."),
    (220, "If no improvement = not working hard enough."),
    (221, "Track metrics: win rate, entry precision, risk:reward."),
    (222, "If none improving = change something."),
    (223, "Re-watch Day 1 when motivation drops."),
    (224, "Check goals – made progress on ANY?"),
    (225, "Work so much you see results, get addicted."),
    (226, "Like gym – newbie gains hook you."),
    (227, "TJR addicted to seeing numbers rise."),
    (228, "No one in trading puts out as much content as TJR."),
    (229, "If not willing to work = admit you'll never reach goals."),
    (230, "Don't dream of cars/houses if not willing to work."),
    (231, "Role models STILL working harder than you."),
    (232, "TJR could quit at 21 but refuses."),
    (233, "Be proud of progress BUT NEVER SATISFIED."),
    (234, "Always a better version of yourself to become."),
]:
    _rule(n, t, "Work Ethic")

# ── BUILDING BLOCKS – PUTTING IT TOGETHER (Rules 235-311) ─────────────────
_rule(235, "Five building blocks total.",
       "Building Blocks")
_rule(236, "Liquidity Sweep = potential for orders to fill.",
       "Building Blocks", ["no_sweep"])
_rule(237, "BOS = CONFIRMATION orders were filled + structure shift.",
       "Building Blocks", ["no_bos"])
_rule(238, "Order Blocks = price range WHERE orders filled.",
       "Building Blocks", ["no_ob"])
_rule(239, "FVGs = price range with LACK of liquidity.",
       "Building Blocks", ["no_fvg"])
_rule(240, "Equilibrium = finding discounted price between swings.",
       "Building Blocks", ["no_eq"])
_rule(241, "Entry Method 1 (RISKIEST): Sweep + BOS only.",
       "Entry Methods", ["only_sweep_bos", "low_confluence"])
_rule(242, "Method 1: Enter immediately on BOS close.",
       "Entry Methods", ["only_sweep_bos"])
_rule(243, "Method 1: Lowest confluence, catches full move.",
       "Entry Methods", ["only_sweep_bos", "low_confluence"])
_rule(244, "Method 1: Stop beyond swept liquidity.",
       "Entry Methods")
_rule(245, "Entry Method 2 (SAFER): Sweep + BOS + OB.",
       "Entry Methods")
_rule(246, "Method 2: Wait for OB retracement.",
       "Entry Methods")
_rule(247, "Method 2: Better risk:reward than Method 1.",
       "Entry Methods")
_rule(248, "Method 2: Stop just above/below OB.",
       "Entry Methods")
_rule(249, "Entry Method 3 (SAFER): Sweep + BOS + FVG.",
       "Entry Methods")
_rule(250, "Method 3: Wait for FVG fill.",
       "Entry Methods")
_rule(251, "Method 3: Can miss OB, still catch FVG.",
       "Entry Methods")
_rule(252, "Method 3: Stop beyond FVG boundary.",
       "Entry Methods")
_rule(253, "Entry Method 4 (SAFEST): Sweep + BOS + (FVG OR OB) + Equilibrium.",
       "Entry Methods")
_rule(254, "Method 4: FOOLPROOF for beginners.",
       "Entry Methods", ["low_confluence", "no_ob", "no_fvg", "no_eq"])
_rule(255, "Method 4: Wait for all confluences.",
       "Entry Methods", ["low_confluence"])
_rule(256, "Method 4: Highest probability.",
       "Entry Methods")
_rule(257, "Method 4: May miss some trades.",
       "Entry Methods")
_rule(258, "Choose ONE entry method, be consistent.",
       "Entry Methods")
_rule(259, "Don't mix methods randomly.",
       "Entry Methods")
_rule(260, "Entry method IS your execution section.",
       "Entry Methods")
_rule(261, "Must understand WHY each block works.",
       "Entry Methods")
_rule(262, "This is NOT complete strategy yet.",
       "Entry Methods")
_rule(263, "Building blocks are price magnets.",
       "Entry Methods")
_rule(264, "Write down all blocks and what they tell you.",
       "Entry Methods")
_rule(265, "Choose which entry method you'll use.",
       "Entry Methods")
_rule(266, "Add entry method to trading plan.",
       "Entry Methods")
_rule(267, "Re-watch building block videos if unclear.",
       "Entry Methods")
_rule(268, "This is BARE BONES strategy.",
       "Building Blocks")
_rule(269, "Daily bias, stops, TPs covered later.",
       "Building Blocks")
_rule(270, "Focus on EXECUTION practice, not live trading.",
       "Building Blocks")
_rule(271, "Find 10 examples of Sweep + BOS.",
       "Building Blocks")
_rule(272, "Mark what happened AFTER setup.",
       "Building Blocks")
_rule(273, "This builds confidence patterns work.",
       "Building Blocks")
_rule(274, "Do on ANY pair/timeframe.",
       "Building Blocks")
_rule(275, "Goal: patterns are 'clockwork'.",
       "Building Blocks")
_rule(276, "Find 10 examples Sweep + BOS + OB.",
       "Building Blocks")
_rule(277, "Mark leg that caused orders to fill.",
       "Building Blocks")
_rule(278, "Mark where price entered OB zone.",
       "Building Blocks")
_rule(279, "Mark what happened after.",
       "Building Blocks")
_rule(280, "Should be 'brainless' after enough practice.",
       "Building Blocks")
_rule(281, "Practice until finding setups is 'robotic'.",
       "Building Blocks")
_rule(282, "Scan chart, instantly see: Sweep→BOS→OB→Rally.",
       "Building Blocks")
_rule(283, "Goal: become like 'aimbot' – instant spotting.",
       "Building Blocks")
_rule(284, "TJR does this brainlessly from thousands of reps.",
       "Building Blocks")
_rule(285, "Speed comes from repetition – 10+ examples minimum.",
       "Building Blocks")
_rule(286, "If can't spot patterns quickly, not ready for bias/TPs.",
       "Building Blocks")
_rule(287, "Examples on: GBP/JPY, GBP/USD, EUR/USD, Gold, SPY.",
       "Building Blocks")
_rule(288, "Same patterns all timeframes: 5min-Weekly.",
       "Building Blocks")
_rule(289, "Every example: Liquidity→Sweep→BOS→Entry→Reaction.",
       "Building Blocks")
_rule(290, "Sweep + BOS works every asset class.",
       "Building Blocks")
_rule(291, "OB entries provide better risk:reward.",
       "Building Blocks", ["no_ob"])
_rule(292, "Price ALWAYS reacts at OBs.",
       "Building Blocks")
_rule(293, "Can switch to 5min for precise OB entry after 4H setup.",
       "Building Blocks")
_rule(294, "Multiple examples often on same chart.",
       "Building Blocks")
_rule(295, "Building blocks stack – more = higher probability.",
       "Building Blocks", ["low_confluence"])
_rule(296, "Don't get excited and place 'million trades'.",
       "Building Blocks", ["overtraded"])
_rule(297, "Don't take trades on forming candles.",
       "Building Blocks", ["forming_candle"])
_rule(298, "Don't force trades if criteria not met.",
       "Building Blocks", ["low_confluence"])
_rule(299, "Don't move to daily bias until patterns instant.",
       "Building Blocks")
_rule(300, "Don't trade live until 50-100 practice examples.",
       "Building Blocks")
_rule(301, "Master Sweep + BOS first.",
       "Building Blocks")
_rule(302, "Then master Sweep + BOS + OB.",
       "Building Blocks")
_rule(303, "Then add FVG recognition.",
       "Building Blocks")
_rule(304, "Then add Equilibrium.",
       "Building Blocks")
_rule(305, "Then learn daily bias.",
       "Building Blocks")
_rule(306, "Then learn stop loss placement.",
       "Building Blocks")
_rule(307, "Then learn take profit targeting.",
       "Building Blocks")
_rule(308, "Psychology covered – if lack discipline, re-watch Day 1.",
       "Building Blocks")
_rule(309, "Videos now daily – focus on execution.",
       "Building Blocks")
_rule(310, "Next: FVG entries, then FVG + OB + EQ entries.",
       "Building Blocks")
_rule(311, "Building block by block – be patient.",
       "Building Blocks")
_rule(312, "Next step: Learn daily bias to complete the full TJR strategy.",
       "Building Blocks")

# ── DAILY BIAS PART 1 (Rules 313-345) ─────────────────────────────────────
_rule(313, "Daily bias = where you think price goes FOR THE DAY.",
       "Daily Bias", ["no_daily_bias"])
_rule(314, "Trade opposite bias = likely stopped or short-lived.",
       "Daily Bias", ["wrong_bias"])
_rule(315, "Daily bias is MOST IMPORTANT (more than execution).",
       "Daily Bias", ["no_daily_bias", "wrong_bias"])
_rule(316, "Start weekly timeframe – where is price going for WEEK.",
       "Daily Bias", ["no_daily_bias"])
_rule(317, "Use line chart on weekly – easiest direction view.",
       "Daily Bias")
_rule(318, "Weekly tells overall direction: up = bullish, down = bearish.",
       "Daily Bias", ["no_daily_bias"])
_rule(319, "Weekly gives 'long-term bias' not direct trading bias.",
       "Daily Bias")
_rule(320, "Daily timeframe = where you find actual daily bias.",
       "Daily Bias", ["no_daily_bias"])
_rule(321, "Look at daily market structure – BOS up or down?",
       "Daily Bias", ["no_daily_bias"])
_rule(322, "Daily BOS to downside = bearish daily bias.",
       "Daily Bias")
_rule(323, "Daily BOS to upside = bullish daily bias.",
       "Daily Bias")
_rule(324, "Majority of daily moves happen during YOUR session.",
       "Daily Bias")
_rule(325, "Everything else (Asian, London for SPY) is chop.",
       "Daily Bias", ["chop"])
_rule(326, "Check 4H trend – does it align with daily?",
       "Daily Bias", ["daily_4h_conflict"])
_rule(327, "If 4H and daily don't align = tricky, be cautious.",
       "Daily Bias", ["daily_4h_conflict"])
_rule(328, "If 4H opposite daily = daily retracing, wait for 4H flip.",
       "Daily Bias", ["daily_4h_conflict"])
_rule(329, "Until 4H flips to match daily, be tentative with TPs.",
       "Daily Bias", ["daily_4h_conflict"])
_rule(330, "Use hourly/15min for EXECUTION points within bias.",
       "Daily Bias")
_rule(331, "Look for building blocks on execution timeframe.",
       "Daily Bias")
_rule(332, "Scale down to lower TF AFTER higher TF confluences met.",
       "Daily Bias")
_rule(333, "Imagine taking swing trade on 4H/Daily.",
       "Daily Bias")
_rule(334, "Find where YOU would enter that swing trade.",
       "Daily Bias")
_rule(335, "Then scale to 15m/5m for precise entries.",
       "Daily Bias")
_rule(336, "This gives: Daily/4H trade + 15m/5m execution = best R:R.",
       "Daily Bias")
_rule(337, "Can't target long-term TPs if higher TF not confirmed.",
       "Daily Bias", ["daily_4h_conflict"])
_rule(338, "If daily down but 4H up = only target 4H retracement.",
       "Daily Bias", ["daily_4h_conflict"])
_rule(339, "When daily and 4H align = can target bigger TPs.",
       "Daily Bias")
_rule(340, "If wrong about bias and price proves it = good loss.",
       "Daily Bias")
_rule(341, "When proved wrong, market shifted – adapt and move on.",
       "Daily Bias")
_rule(342, "Daily BOS opposite weekly = WEEKLY RETRACEMENT.",
       "Daily Bias")
_rule(343, "Weekly retracement means daily won't go forever.",
       "Daily Bias")
_rule(344, "Look for retracement tools on weekly during daily moves.",
       "Daily Bias")
_rule(345, "When daily breaks back in line with weekly = retracement over.",
       "Daily Bias")

# ── DAILY BIAS PART 2 (Rules 346-366) ─────────────────────────────────────
_rule(346, "Do daily bias BEFORE market opens.",
       "Daily Bias", ["no_daily_bias"])
_rule(347, "Record bias – don't change mid-session.",
       "Daily Bias")
_rule(348, "Don't let personal bias override price action.",
       "Daily Bias", ["wrong_bias"])
_rule(349, "Mark higher TF liquidity draws even if far.",
       "Daily Bias")
_rule(350, "Daily broke structure = establish bias from that.",
       "Daily Bias")
_rule(351, "'Can't force your bias on the market'.",
       "Daily Bias", ["wrong_bias"])
_rule(352, "Take trades based on what market GIVES, not WANT.",
       "Daily Bias", ["wrong_bias", "emotional"])
_rule(353, "Weekly bullish + daily bearish = daily in weekly retracement.",
       "Daily Bias")
_rule(354, "Mark weekly OB where sweep occurred.",
       "Daily Bias")
_rule(355, "Until daily flips bullish, look for shorts (but retracement).",
       "Daily Bias")
_rule(356, "In retracement: use building blocks for entry zones.",
       "Daily Bias")
_rule(357, "FVG + OB + Sweep = high confluence zone.",
       "Daily Bias")
_rule(358, "Need 15min BOS minimum when 4H/hourly oppose daily.",
       "Daily Bias", ["daily_4h_conflict"])
_rule(359, "Weekly down + daily down = looking for shorts.",
       "Daily Bias")
_rule(360, "Currently retracing on daily (4H broke up).",
       "Daily Bias")
_rule(361, "Prime time to catch shorts when both align bearish.",
       "Daily Bias")
_rule(362, "Mark all building blocks in direction of bias.",
       "Daily Bias")
_rule(363, "When price hits zones, scale to lower TF for entry.",
       "Daily Bias")
_rule(364, "TJR takes trades in first 1.5 hours NY session.",
       "Daily Bias", ["session_violation"])
_rule(365, "If can't find trade in designated time = NO TRADE.",
       "Daily Bias")
_rule(366, "Trade recap shows how daily bias played out.",
       "Daily Bias")

# ── DAILY BIAS PART 3 (Rules 367-392) ─────────────────────────────────────
_rule(367, "Daily breaks down in weekly uptrend = weekly retracement.",
       "Daily Bias")
_rule(368, "Weekly retracement means will reverse eventually.",
       "Daily Bias")
_rule(369, "Can trade shorts during weekly retracement.",
       "Daily Bias")
_rule(370, "Use weekly retracement tools: EQ, FVG, OB.",
       "Daily Bias")
_rule(371, "15min BOS in 1H uptrend = just 1H retracement.",
       "Daily Bias", ["timeframe_conflict"])
_rule(372, "1min BOS could just be 5min retracement.",
       "Daily Bias", ["timeframe_conflict"])
_rule(373, "Don't get 'skewed by low timeframe bullshit moves'.",
       "Daily Bias", ["timeframe_conflict"])
_rule(374, "One minute BOS doesn't mean whole chart reversing.",
       "Daily Bias", ["timeframe_conflict"])
_rule(375, "Keep bias on HIGH timeframe, ignore low TF noise.",
       "Daily Bias", ["timeframe_conflict"])
_rule(376, "Small leg vs big leg: ALWAYS take big leg (extension).",
       "Daily Bias")
_rule(377, "No clear confluences on 4H = didn't trade that day.",
       "Daily Bias", ["low_confluence"])
_rule(378, "If price comes to confluences but NO REACTION = ignore, move on.",
       "Daily Bias")
_rule(379, "Daily down + weekly up = take shorts knowing it's retracement.",
       "Daily Bias")
_rule(380, "Not in weekly discount yet = shorts won't go all way.",
       "Daily Bias")
_rule(381, "First target = weekly discount zone.",
       "Daily Bias")
_rule(382, "Daily OB = highest priority TP for daily move.",
       "Daily Bias")
_rule(383, "Can hold 3-4 days if targeting daily levels.",
       "Daily Bias")
_rule(384, "Weekly down + daily down = highest probability shorts.",
       "Daily Bias")
_rule(385, "Currently in daily discount = good reversal area.",
       "Daily Bias")
_rule(386, "4H broke up = daily retracement happening.",
       "Daily Bias")
_rule(387, "In 4H retracement during daily down = PRIME setup.",
       "Daily Bias")
_rule(388, "Weekly trend→Daily structure→4H confirm→Hourly blocks→5m execution.",
       "Daily Bias", ["no_daily_bias", "timeframe_conflict"])
_rule(389, "If all align same direction = highest probability.",
       "Daily Bias", ["daily_4h_conflict"])
_rule(390, "Journal every loss to find patterns.",
       "Daily Bias")
_rule(391, "Discipline and risk should be 'robotic' by now.",
       "Daily Bias")
_rule(392, "If still emotional about demo = can't go live.",
       "Daily Bias", ["emotional"])

# ── TAKE PROFITS (Rules 393-416) ──────────────────────────────────────────
_rule(393, "Take profits AT your building blocks.",
       "Take Profits")
_rule(394, "Building blocks = price magnets where price reacts.",
       "Take Profits")
_rule(395, "Use blocks on EXECUTION timeframe for TPs.",
       "Take Profits")
_rule(396, "Use ONE HIGHER timeframe for first TP.",
       "Take Profits")
_rule(397, "Entering on 5min → look at 15min for TP1.",
       "Take Profits")
_rule(398, "Entering on 15min → look at 1H for TP1.",
       "Take Profits")
_rule(399, "Entering on 1H → look at 4H for TP1.",
       "Take Profits")
_rule(400, "Minimum first TP = 1:1 risk:reward.",
       "Take Profits", ["poor_rr"])
_rule(401, "Liquidity pools (untested highs/lows) for TPs.",
       "Take Profits")
_rule(402, "Order blocks in direction of trade.",
       "Take Profits")
_rule(403, "Fair value gaps.",
       "Take Profits")
_rule(404, "Top/bottom of imbalances.",
       "Take Profits")
_rule(405, "Stack multiple TPs at different blocks.",
       "Take Profits")
_rule(406, "Enter 5min BOS→TP1 at 5min OB→TP2 at 15min FVG→TP3 at 1H liquidity.",
       "Take Profits")
_rule(407, "Typical: 3-4 take profits.",
       "Take Profits")
_rule(408, "First TP: 1:1 to 2:1 usually.",
       "Take Profits", ["poor_rr"])
_rule(409, "Don't need absurd R:R (10:1) on first TP.",
       "Take Profits")
_rule(410, "Most important: Daily bias.",
       "Take Profits", ["no_daily_bias"])
_rule(411, "Second: Execution.",
       "Take Profits")
_rule(412, "Third: Stop loss.",
       "Take Profits")
_rule(413, "Fourth: Take profits (least worry).",
       "Take Profits")
_rule(414, "If execution good, price goes in your favor.",
       "Take Profits")
_rule(415, "Real trade example TJR showed in video.",
       "Take Profits")
_rule(416, "Mark building blocks in direction, set TPs at each.",
       "Take Profits")

# ── STOP LOSSES (Rules 417-443) ───────────────────────────────────────────
_rule(417, "Stop loss = your INVALIDATION point.",
       "Stop Losses", ["stop_too_tight"])
_rule(418, "Default: Stop ABOVE liquidity sweep (shorts) or BELOW (longs).",
       "Stop Losses", ["stop_too_tight"])
_rule(419, "If price back above swept liquidity = setup invalidated.",
       "Stop Losses")
_rule(420, "Sweep is where orders supposedly filled.",
       "Stop Losses")
_rule(421, "If price returns, orders weren't filled.",
       "Stop Losses")
_rule(422, "Sweep + BOS + OB = stop above sweep.",
       "Stop Losses", ["stop_too_tight"])
_rule(423, "Don't put stop just above OB – put above sweep.",
       "Stop Losses", ["stop_too_tight"])
_rule(424, "Stop should be 0.5 points from actual price (spread buffer).",
       "Stop Losses", ["stop_too_tight"])
_rule(425, "Never put stop EXACTLY on price – stopped by spread.",
       "Stop Losses", ["stop_too_tight"])
_rule(426, "Check broker's spread for each pair.",
       "Stop Losses")
_rule(427, "Add buffer beyond exact level.",
       "Stop Losses", ["stop_too_tight"])
_rule(428, "Ask/bid difference stops you if not accounting for spread.",
       "Stop Losses", ["stop_too_tight"])
_rule(429, "If sweep SO FAR that R:R terrible → adjust.",
       "Stop Losses", ["stop_too_wide", "poor_rr"])
_rule(430, "Exception: Put stop above OB instead of sweep.",
       "Stop Losses")
_rule(431, "TJR doesn't like exception – invalidation still at liquidity.",
       "Stop Losses")
_rule(432, "Price could take you out then go in bias.",
       "Stop Losses")
_rule(433, "For 15min sweep + BOS = can use lower TF confluence for closer stop.",
       "Stop Losses")
_rule(434, "Scale one TF lower for closer stop level.",
       "Stop Losses")
_rule(435, "Still keep risk management in mind.",
       "Stop Losses")
_rule(436, "Stop purely based on RISK TOLERANCE + INVALIDATION.",
       "Stop Losses")
_rule(437, "Stop not based on PIPS/POINTS – based on invalidation.",
       "Stop Losses", ["stop_too_tight"])
_rule(438, "Your invalidation = where trade idea no longer makes sense.",
       "Stop Losses")
_rule(439, "TJR's real example: stop below 5min sweep (his invalidation).",
       "Stop Losses")
_rule(440, "Got stopped, but price went in original bias after = OK, followed plan.",
       "Stop Losses")
_rule(441, "Find invalidation + understand tolerance = proper stop.",
       "Stop Losses")
_rule(442, "If stopped but bias correct = good loss (followed rules).",
       "Stop Losses")
_rule(443, "Better planned loss than random loss.",
       "Stop Losses")

# ── POSITION SIZING (Rules 444-485) ───────────────────────────────────────
_rule(444, "Three methods: Set lot size, Calculate each time, Full port (NEVER).",
       "Position Sizing")
_rule(445, "Full port = stupid, will blow account.",
       "Position Sizing")
_rule(446, "Method 1: SET LOT SIZE (TJR's method).",
       "Position Sizing")
_rule(447, "Use same lot for every trade (or range of 3).",
       "Position Sizing")
_rule(448, "Calculate once based on average stop.",
       "Position Sizing")
_rule(449, "Don't calculate every trade – saves time.",
       "Position Sizing")
_rule(450, "Works when consistent stop sizes.",
       "Position Sizing")
_rule(451, "Method 2: CALCULATE EVERY TIME.",
       "Position Sizing")
_rule(452, "Use lot calculator each trade.",
       "Position Sizing")
_rule(453, "Input: balance, risk %, stop in pips.",
       "Position Sizing")
_rule(454, "Tools: Stenu app ($14/year) or online calculators.",
       "Position Sizing")
_rule(455, "More precise but takes time.",
       "Position Sizing")
_rule(456, "Use Stenu app or MyFxBook calculator.",
       "Position Sizing")
_rule(457, "Formula: Balance + Risk % + Stop pips = Lot size.",
       "Position Sizing")
_rule(458, "Example: $1000, 1%, 30 pips = 0.03 lots.",
       "Position Sizing")
_rule(459, "For SPX: Check broker 'contract size per lot'.",
       "Position Sizing")
_rule(460, "Most offshore: 100 units per lot.",
       "Position Sizing")
_rule(461, "Prop firms: Usually 10 units per lot.",
       "Position Sizing")
_rule(462, "Hanko (TJR's): 10 units per lot for SPX.",
       "Position Sizing")
_rule(463, "Use short position tool on TV to see stop in pips.",
       "Position Sizing")
_rule(464, "Drag from entry to stop = shows pips.",
       "Position Sizing")
_rule(465, "That number = stop in pips for calculator.",
       "Position Sizing")
_rule(466, "Determine MINIMUM usual stop size.",
       "Position Sizing")
_rule(467, "Calculate lot for that minimum stop.",
       "Position Sizing")
_rule(468, "If actual stop 2x minimum = risk 2x normal.",
       "Position Sizing")
_rule(469, "If actual stop 3x minimum = risk 3x normal.",
       "Position Sizing")
_rule(470, "Example: Normal 4 points for 1% = 8 points = 2%.",
       "Position Sizing")
_rule(471, "TJR's 3-Tier System: NORMAL RISK.",
       "Position Sizing")
_rule(472, "Use on normal days, no news.",
       "Position Sizing")
_rule(473, "Based on minimum usual stop.",
       "Position Sizing")
_rule(474, "Default 90% of time.",
       "Position Sizing")
_rule(475, "DE-RISK LOT SIZE: 50% of normal.",
       "Position Sizing", ["news_event", "bank_holiday"])
_rule(476, "Use when: High impact news, bank holidays, choppy expected.",
       "Position Sizing", ["news_event", "bank_holiday", "chop"])
_rule(477, "TJR used during 4th July week.",
       "Position Sizing")
_rule(478, "CONFIDENT LOT SIZE: 2x normal (DON'T USE if unprofitable).",
       "Position Sizing")
_rule(479, "Only when: No news + LOVE setup + all biases align.",
       "Position Sizing")
_rule(480, "Beginners should NEVER use confident.",
       "Position Sizing")
_rule(481, "Only after proven profitability track record.",
       "Position Sizing")
_rule(482, "Beginners: Calculated lot or ONE set lot only.",
       "Position Sizing")
_rule(483, "Don't use confident until consistently profitable.",
       "Position Sizing")
_rule(484, "Different pairs need different lot calculations.",
       "Position Sizing")
_rule(485, "Each pair different volatility and pip values.",
       "Position Sizing")

# ── HANDLING LOSING STREAKS (Rules 486-531) ───────────────────────────────
_rule(486, "DON'T change entire strategy during streak.",
       "Losing Streaks")
_rule(487, "Strategy worked before – still works, needs tweaking.",
       "Losing Streaks")
_rule(488, "Make SMALL tweaks, not drastic changes.",
       "Losing Streaks")
_rule(489, "Journal EVERY trade to find patterns.",
       "Losing Streaks")
_rule(490, "Ask: 'Is there pattern in what I'm doing wrong?'",
       "Losing Streaks")
_rule(491, "Look for same mistake repeated.",
       "Losing Streaks")
_rule(492, "Tweak 1: CHANGE PAIRS – pair not moving well.",
       "Losing Streaks", is_fix=True, fix_type="change_symbol")
_rule(493, "TJR example: SPX choppy → Forex → 100K week.",
       "Losing Streaks", ["chop"])
_rule(494, "Small tweak, big reward.",
       "Losing Streaks")
_rule(495, "Tweak 2: ADD CONFLUENCE – TJR entering just Sweep + BOS.",
       "Losing Streaks",
       ["only_sweep_bos", "stopped_early", "continued_after_stop", "low_confluence"])
_rule(496, "Getting stopped, price going in direction after.",
       "Losing Streaks",
       ["stopped_early", "continued_after_stop", "only_sweep_bos"])
_rule(497, "Solution: Add OB or FVG requirement.",
       "Losing Streaks",
       ["only_sweep_bos", "no_ob", "no_fvg"],
       is_fix=True, fix_type="require_ob_or_fvg")
_rule(498, "Now requires: Sweep + BOS + (OB OR FVG) minimum.",
       "Losing Streaks",
       ["only_sweep_bos", "low_confluence"],
       is_fix=True, fix_type="require_ob_or_fvg")
_rule(499, "Small tweak: Just one more confluence.",
       "Losing Streaks",
       ["low_confluence"],
       is_fix=True, fix_type="increase_min_confluences")
_rule(500, "Don't try to 'make back' losses immediately.",
       "Losing Streaks", ["revenge_trade"])
_rule(501, "Don't force trades to recover money.",
       "Losing Streaks", ["revenge_trade"])
_rule(502, "Don't increase risk after losses.",
       "Losing Streaks")
_rule(503, "Don't trade more frequently to recover.",
       "Losing Streaks", ["overtraded", "revenge_trade"])
_rule(504, "Worst thing: Trying to make money back = more losses.",
       "Losing Streaks", ["revenge_trade"])
_rule(505, "Know money will come back next week/month/year.",
       "Losing Streaks")
_rule(506, "If proven profitable, WILL make it back.",
       "Losing Streaks")
_rule(507, "Be patient – don't force it.",
       "Losing Streaks", ["revenge_trade"])
_rule(508, "Two losing days ≠ losing streak (TJR view).",
       "Losing Streaks")
_rule(509, "Two losing WEEKS = losing streak to TJR.",
       "Losing Streaks")
_rule(510, "Win or lose – analyze what could be better.",
       "Losing Streaks")
_rule(511, "On wins: How get better R:R?",
       "Losing Streaks")
_rule(512, "On wins: How more precise entry?",
       "Losing Streaks")
_rule(513, "On losses: What went wrong? How improve?",
       "Losing Streaks")
_rule(514, "Never blame market – ALWAYS your fault.",
       "Losing Streaks")
_rule(515, "Market does what it wants regardless.",
       "Losing Streaks")
_rule(516, "Market doesn't owe you anything.",
       "Losing Streaks")
_rule(517, "YOU pressed button – YOU analyzed – YOU responsible.",
       "Losing Streaks")
_rule(518, "If can't accept this, trading not for you.",
       "Losing Streaks")
_rule(519, "TJR recent example: Lost 2 days, bias correct both.",
       "Losing Streaks")
_rule(520, "Stopped out, price went in direction after.",
       "Losing Streaks",
       ["stopped_early", "continued_after_stop"])
_rule(521, "Problem: Entry execution needed more confluence.",
       "Losing Streaks",
       ["low_confluence", "only_sweep_bos"])
_rule(522, "Solution: Wait for OB/FVG, not just Sweep + BOS.",
       "Losing Streaks",
       ["only_sweep_bos", "no_ob", "no_fvg"],
       is_fix=True, fix_type="require_ob_or_fvg")
_rule(523, "Every bias was correct = execution was issue.",
       "Losing Streaks",
       ["only_sweep_bos", "low_confluence"])
_rule(524, "If losing consistently and NOT learning = won't make it.",
       "Losing Streaks")
_rule(525, "Must journal losses to find patterns.",
       "Losing Streaks")
_rule(526, "Spend hours watching charts to understand market.",
       "Losing Streaks")
_rule(527, "If not willing to put in hours = 'get lost'.",
       "Losing Streaks")
_rule(528, "Genuinely won't make it without work.",
       "Losing Streaks")
_rule(529, "Even winning days – find what could be better.",
       "Losing Streaks")
_rule(530, "Every single day has room for improvement.",
       "Losing Streaks")
_rule(531, "Never be satisfied – always improve.",
       "Losing Streaks")

# ── BACKTESTING (Rules 532-575) ───────────────────────────────────────────
_rule(532, "Bar replay NOT best way to backtest.",
       "Backtesting")
_rule(533, "Best: Scroll back on full chart and analyze.",
       "Backtesting")
_rule(534, "Problem 1: Bar replay locks to ONE timeframe.",
       "Backtesting")
_rule(535, "If 15min bar replay, daily shows FULL daily candle.",
       "Backtesting")
_rule(536, "Can't see daily bias develop properly.",
       "Backtesting")
_rule(537, "4H/1H also show full candles, not bar-by-bar.",
       "Backtesting")
_rule(538, "Defeats purpose since daily bias most important.",
       "Backtesting")
_rule(539, "Problem 2: No emotion simulation.",
       "Backtesting")
_rule(540, "Bar replay: bar, next bar, next bar (click click).",
       "Backtesting")
_rule(541, "Real trading: candles form with wicks = EMOTION.",
       "Backtesting")
_rule(542, "Bar replay doesn't simulate emotional aspect.",
       "Backtesting")
_rule(543, "Emotions from forming candles don't show.",
       "Backtesting")
_rule(544, "Problem 3: Can't adapt bias.",
       "Backtesting")
_rule(545, "In bar replay, start with bias from one point.",
       "Backtesting")
_rule(546, "Then watch 15min bars one by one.",
       "Backtesting")
_rule(547, "Miss higher TF BOS happening.",
       "Backtesting")
_rule(548, "Might look for shorts when hourly broke up.",
       "Backtesting")
_rule(549, "Can't adapt bias – stuck on one TF.",
       "Backtesting")
_rule(550, "RECOMMENDED METHOD: SCROLL BACK.",
       "Backtesting")
_rule(551, "Start random point, do complete top-down.",
       "Backtesting")
_rule(552, "Weekly→Daily→4H→1H→15m→5m.",
       "Backtesting")
_rule(553, "'AIMBOT PRACTICE'.",
       "Backtesting")
_rule(554, "Practice instantly spotting building blocks.",
       "Backtesting")
_rule(555, "Boom – sweep, boom – BOS, boom – OB.",
       "Backtesting")
_rule(556, "Repeat until 'robotic'.",
       "Backtesting")
_rule(557, "Goal: Spot as fast as aimbot clicks targets.",
       "Backtesting")
_rule(558, "REPLAY PREVIOUS WEEK.",
       "Backtesting")
_rule(559, "Every weekend: Review previous week.",
       "Backtesting")
_rule(560, "Each day: Find 1-2 good trades possible.",
       "Backtesting")
_rule(561, "Mark all building blocks on those days.",
       "Backtesting")
_rule(562, "Practice: Liquidity→Sweep→BOS→Entry→Result.",
       "Backtesting")
_rule(563, "LIVE DEMO TRADING.",
       "Backtesting")
_rule(564, "Best backtesting = demo trade LIVE.",
       "Backtesting")
_rule(565, "Live market moves with real emotions.",
       "Backtesting")
_rule(566, "Judge only on LIVE markets, not bar replay.",
       "Backtesting")
_rule(567, "Win rate on bar replay worse than live (no proper bias).",
       "Backtesting")
_rule(568, "TARGET PRACTICE: Spot blocks repeatedly.",
       "Backtesting")
_rule(569, "Do homework from building block videos.",
       "Backtesting")
_rule(570, "Find 10 examples of each block type.",
       "Backtesting")
_rule(571, "Repeat until spotting is 'brainless'.",
       "Backtesting")
_rule(572, "Backtesting can't replicate live emotions.",
       "Backtesting")
_rule(573, "Forming candles create fear/excitement.",
       "Backtesting", ["forming_candle"])
_rule(574, "Only way train emotions = live or demo.",
       "Backtesting")
_rule(575, "This why demo better than bar replay.",
       "Backtesting")

# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------
RULES: List[Dict[str, Any]] = _R


class RulesEngine:
    """Search and retrieve rules from the 575-rule knowledge base."""

    def __init__(self) -> None:
        self._rules = RULES
        # Build index: symptom → list of rules
        self._symptom_index: Dict[str, List[Dict]] = {}
        for rule in self._rules:
            for sym in rule["symptoms"]:
                self._symptom_index.setdefault(sym, []).append(rule)

    # ------------------------------------------------------------------ #
    def find_by_number(self, number: int) -> Optional[Dict]:
        for r in self._rules:
            if r["number"] == number:
                return r
        return None

    def find_by_symptoms(self, symptoms: List[str]) -> List[Dict]:
        """Return rules that match ANY of the given symptom keys, deduped."""
        seen: set = set()
        results: List[Dict] = []
        for sym in symptoms:
            for rule in self._symptom_index.get(sym, []):
                if rule["number"] not in seen:
                    seen.add(rule["number"])
                    results.append(rule)
        results.sort(key=lambda r: r["number"])
        return results

    def find_fixes(self, symptoms: List[str]) -> List[Dict]:
        """Return only rules that describe a corrective action."""
        return [r for r in self.find_by_symptoms(symptoms) if r["is_fix"]]

    def get_by_category(self, category: str) -> List[Dict]:
        return [r for r in self._rules if r["category"] == category]

    def search_text(self, keyword: str) -> List[Dict]:
        kw = keyword.lower()
        return [r for r in self._rules if kw in r["text"].lower()]

    def total_rules(self) -> int:
        return len(self._rules)

    def categories(self) -> List[str]:
        seen: set = set()
        order: List[str] = []
        for r in self._rules:
            cat = r["category"]
            if cat not in seen:
                seen.add(cat)
                order.append(cat)
        return order
