// Records scenes.html into a 1080x1920, 30s webm using Playwright's Chromium,
// then transcodes to an MP4 with Playwright's bundled ffmpeg.
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const PAGE = 'file://' + path.join(HERE, 'scenes.html');
const OUTDIR = path.join(HERE, 'out');
const DURATION_MS = 30500; // a touch over 30s so the CTA lands

(async () => {
  fs.rmSync(OUTDIR, { recursive: true, force: true });
  fs.mkdirSync(OUTDIR, { recursive: true });

  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const context = await browser.newContext({
    viewport: { width: 1080, height: 1920 },
    deviceScaleFactor: 1,
    recordVideo: { dir: OUTDIR, size: { width: 1080, height: 1920 } },
  });
  const page = await context.newPage();
  await page.goto(PAGE, { waitUntil: 'load' });
  await page.waitForTimeout(DURATION_MS);

  const video = page.video();
  await context.close(); // finalizes the webm
  await browser.close();

  const webm = await video.path();
  const mp4 = path.join(HERE, 'algorithmic-trading.mp4');

  // Playwright's bundled ffmpeg is VP8-only; we need an H.264 build for TikTok.
  // Resolve a full ffmpeg via imageio-ffmpeg (pip install imageio-ffmpeg) or PATH.
  let ffmpegBin = 'ffmpeg';
  try {
    ffmpegBin = execFileSync('python3', ['-c',
      'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())']).toString().trim();
  } catch (_) { /* fall back to ffmpeg on PATH */ }

  execFileSync(ffmpegBin, [
    '-y', '-i', webm,
    '-vf', 'scale=1080:1920,fps=30,format=yuv420p',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '20',
    '-movflags', '+faststart',
    mp4,
  ], { stdio: 'inherit' });

  console.log('WEBM:', webm);
  console.log('MP4 :', mp4);
})().catch(e => { console.error(e); process.exit(1); });
