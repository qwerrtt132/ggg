# 🎬 "How Algorithmic Trading Works" — 30-Second TikTok

Vertical 9:16 · 1080×1920 · 30fps · ~30s. Final file: `algorithmic-trading.mp4`.

The video is **silent with on-screen captions** (works muted on the For You feed).
Read the **voiceover** below over it if you want narration, and drop a trending
upbeat/lo-fi track underneath at low volume.

---

## Scene-by-scene (voiceover + on-screen text)

| Time | On screen | Voiceover (record this) |
|------|-----------|--------------------------|
| 0–4s | 🤖📈 "How do **computers** trade stocks by themselves?" | "How do computers trade stocks all by themselves? Here's algo trading in 30 seconds." |
| 4–9s | "**Algorithmic trading** = code that buys & sells using **rules**, not feelings." | "Algorithmic trading is just code that buys and sells using rules — no fear, no greed, just math." |
| 9–14s | **① It reads the data** + live candlestick chart | "Step one: it reads the data — live prices, volume, news and patterns, thousands of times a second." |
| 14–19s | **② It follows a strategy** · `IF price dips 2% AND volume spikes → BUY` | "Step two: it follows a strategy. Like — IF price dips two percent AND volume spikes, then buy. Tested on years of history first." |
| 19–24s | **③ It executes in milliseconds** · counter ticks to 10,000 | "Step three: it executes in milliseconds — thousands of trades before you can blink." |
| 24–28s | "Algorithms drive most of the market" · ⚡~70% volume · 🧠 zero emotion · 🌍 24/7 | "And this runs the market — around seventy percent of volume, zero emotion, all day, every day." |
| 28–30s | 📊 "Now you get **algo trading.**" · Follow ⬇️ | "Now you get algo trading. Follow for more." |

---

## Post it

- **Handle:** edit `@yourhandle` in `scenes.html` (search `.handle`) before re-rendering.
- **Caption idea:** `Algorithmic trading explained in 30s 🤖📈 #algotrading #stocks #trading #fintech #investing101`
- **Hashtags:** `#algotrading #trading #stocks #fintech #quant #investing #stockmarket #fyp`
- **Hook retention tip:** the first 3s (the 🤖📈 question) is the scroll-stopper — keep it.

## ⚠️ Disclaimer
Educational content only — not financial advice. The "~70% of volume" figure is a
widely-cited industry estimate for US equity markets; phrase as an estimate.

---

## Re-render it yourself
```bash
cd video
pip install imageio-ffmpeg          # full H.264 ffmpeg (one-time)
NODE_PATH=/opt/node22/lib/node_modules \
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
node render.js                      # -> algorithmic-trading.mp4
```
Edit copy, colors, timing, or your handle in `scenes.html`, then re-run.
`scenes.html` also opens directly in any browser as a live preview.
