# Running this tool on Android (Termux)

Two things to know before you start:

**1. The window version (`gui.py`) will not work.** It needs Tkinter, which
plain Termux has no working display for. Getting it running would mean
installing a whole X11 desktop server on your phone — not worth it. Use
`main.py` instead: it's the same strategy, the same signals, the same
targets, just printed as text instead of drawn in a window.

**2. Setup is slow the first time.** `pandas` and `numpy` are not available
as ready-made Termux packages, so your phone has to compile them from
source. Budget **20–60 minutes** and about **2 GB of free space**. Plug the
phone in and keep Termux on screen. You only do this once — after that,
starting the tool takes seconds.

---

## Step 1 — install Termux (not from Play Store)

The Play Store version is outdated and abandoned. Install from
[F-Droid](https://f-droid.org/packages/com.termux/) or the
[GitHub releases](https://github.com/termux/termux-app/releases).

## Step 2 — get the files onto your phone

In Termux, allow it to see your phone's storage:

```bash
termux-setup-storage
```

Tap **Allow** on the popup. Now copy the zip you downloaded, and unzip it:

```bash
cd ~
cp ~/storage/downloads/trading-tool.zip .
pkg install -y unzip
unzip trading-tool.zip
cd trading-tool
```

If your file landed somewhere else, find it with:

```bash
ls ~/storage/downloads
```

## Step 3 — run the installer

```bash
bash termux_setup.sh
```

This installs Python, the build tools, then compiles numpy and pandas. It
prints progress as it goes and verifies everything at the end. **This is the
slow part.** If your screen turns off mid-build, Android may kill it — keep
the phone awake, or run `termux-wake-lock` first.

If it fails partway, just run it again — it skips anything already built.

## Step 4 — run the tool

Free mode (no Zerodha account needed, delayed data):

```bash
python3 main.py --index NIFTY --mode free --live --refresh 60
```

Press `Ctrl+C` to stop.

Other options:

```bash
# one-off reading instead of a live loop
python3 main.py --index BANKNIFTY --mode free

# Bank Nifty, refreshing every 30 seconds
python3 main.py --index BANKNIFTY --mode free --live --refresh 30

# see which expiry dates are available
python3 main.py --index NIFTY --mode free --list-expiries
```

## Step 5 — Zerodha (kite) mode, if you want live prices

Kite mode gives real-time prices and live option premiums, which is what
makes the targets most accurate. You need a fresh token each trading day:

```bash
python3 kite_auth.py
```

It prints a login link — open it in your phone's browser and log in to
Zerodha normally. Because the redirect comes back to `http://127.0.0.1:5055/`,
which is Termux itself, the token is captured automatically and saved to
`.env` — no copying anything out of the address bar.

For this to work your Kite app's **Redirect URL** must be set to exactly
`http://127.0.0.1:5055/` at https://developers.kite.trade/apps. If it isn't,
the script times out and tells you so; `python3 kite_login_helper.py` is the
manual fallback that still works with any redirect URL.

Your Zerodha password is never stored or read — you type it into Zerodha's
own page in the browser, same as always.

Then:

```bash
python3 main.py --index NIFTY --mode kite --live --refresh 30
```

The token expires around **6 am IST** each morning, so repeat the login step
once per trading day.

---

## Reading the output

Every refresh prints the full report — the market trend block, how far the
market can realistically travel, the bias, and (when a signal fires) your
entry, targets, and stop-loss.

The one thing you lose versus the desktop version is the **automatic trade
tracker** — the window version watches the live price and tells you the
moment a target is hit. In Termux you'll need to compare the printed numbers
against the market yourself, or keep an eye on your Zerodha app.

## If something goes wrong

**"No module named pandas"** — the build didn't finish. Re-run
`bash termux_setup.sh`.

**Build fails with a compiler error** — you may be low on space. Check with
`df -h ~` and clear some room, then retry.

**Never run `pip install --upgrade pip` on Termux.** Termux ships a patched
pip, and replacing it breaks these builds.

**NSE blocks the request / option chain fails** — free mode scrapes NSE's
public site, which sometimes rate-limits. The tool retries automatically;
if it keeps failing, wait a few minutes or use kite mode.

---

Sources for the Termux build steps:
[termux-packages discussion #19126](https://github.com/termux/termux-packages/discussions/19126),
[termux/x11-packages issue #172 (Tkinter)](https://github.com/termux/x11-packages/issues/172)
