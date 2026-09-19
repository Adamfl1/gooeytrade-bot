# GooeyTrade Bot — Setup Guide

## How It Works

GitHub Actions runs the bot every 5 minutes, Monday-Friday:

```
Every 5 min (Mon-Fri):
  ├─ Scrape your TradingView chart
  │
  ├─ Phase B: Found "SL xxxx" + "TP xxxx" labels?
  │   → Yes + pending trade? → Edit position on GooeyTrade (add SL/TP)
  │   → No? → Skip
  │
  └─ Phase A: Found "BUY xxxx" or "SELL xxxx" label?
      → Yes? → Open market order on GooeyTrade (no SL/TP yet)
      → No? → Skip
```

Your PC can be OFF. Everything runs in the cloud.

---

## Step 1: Capture Sessions (PC ON — one time only)

### TradingView session
```bash
pip install playwright
playwright install chromium
python capture_tv_session.py
```
A browser opens → log in to TradingView → press Enter → `tv_session.json` is saved.

### GooeyTrade session
```bash
python capture_session.py
```
A browser opens → log in to GooeyTrade → press Enter → `session.json` is saved.

---

## Step 2: Push to GitHub

```bash
git init
git add .
git commit -m "initial"
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

---

## Step 3: Add GitHub Secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret

| Secret Name | Value |
|---|---|
| `TV_SESSION_BASE64` | base64 of `tv_session.json` |
| `GOOEYTRADE_SESSION_BASE64` | base64 of `session.json` |
| `TV_CHART_URL` | `https://www.tradingview.com/chart/k6hILD7L/?symbol=OANDA%3AXAUUSD` |

To get the base64 values:
```powershell
# Windows PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("tv_session.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("session.json"))
```

---

## Step 4: Enable the Workflow

1. Go to your repo → Actions → GooeyTrade Bot
2. Click "Enable workflow"
3. That's it. The cron `*/5 * * * 1-5` handles scheduling automatically.

**Cron runs are LIVE by default.** No extra setup needed.

---

## When Sessions Expire (~30 days)

You'll see errors like "Trade page did not load" or "No TV signal".

To fix:
1. Turn PC ON
2. Run `python capture_tv_session.py` and `python capture_session.py`
3. Re-encode and update GitHub secrets:
   ```powershell
   $tv = [Convert]::ToBase64String([IO.File]::ReadAllBytes("tv_session.json"))
   $gt = [Convert]::ToBase64String([IO.File]::ReadAllBytes("session.json"))
   # Then update secrets in GitHub UI
   ```
4. Push the new session files:
   ```bash
   git add tv_session.json session.json
   git commit -m "refresh sessions"
   git push
   ```

---

## Files

| File | Purpose |
|---|---|
| `run.py` | Main bot — two-phase flow |
| `config.py` | All settings |
| `state.py` | Trade history + pending TP/SL |
| `tv_scraper.py` | Reads TradingView labels |
| `execution.py` | Places trades on GooeyTrade |
| `signal_detector.py` | Local signal detection (fallback) |
| `data_pipeline.py` | yfinance candle data |
| `indicators.py` | EMA, Supertrend, ATR math |
| `capture_session.py` | Save GooeyTrade login |
| `capture_tv_session.py` | Save TradingView login |
