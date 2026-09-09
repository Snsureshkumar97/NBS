# Setting the tool up on someone else's computer

Give them this page and the zip. Nothing in the code is tied to your account —
no key, no client ID, no licence check — so a second install is just a second
install. They need their own Kite Connect app and they log in with their own
Zerodha ID.

Everything below happens on **their** computer.

---

## Before they start

- A Zerodha trading account (their own).
- ₹500/month for Kite Connect, billed to that account. Historical data is
  included in that price — there is no separate data add-on to buy.
- Python 3.9 or newer. On a Mac it's already there. Check with `python3 --version`.

---

## Step 1 — Create his Kite Connect app

1. Go to **https://developers.kite.trade/apps** and sign in with his Zerodha ID.
2. Click **Create new app**.
3. Fill it in:
   - **Type:** Connect
   - **App name:** anything, e.g. `my-signals`
   - **Zerodha client ID:** his own
   - **Redirect URL:** exactly

         http://127.0.0.1:5055/

     Including the `http://`, including the trailing slash. If this is wrong by
     one character the login will hang and then time out — it is the single most
     common mistake.
   - **Postback URL:** leave blank.
   - **Description:** anything.
4. Pay the ₹500. The app goes live within a few minutes.
5. Open the app. Copy the **API key** off the page, then click to reveal and
   copy the **API secret**.

Keep both somewhere private for the next five minutes. The secret is the
password to the app — it should never be pasted into a chat, a screenshot,
or a message.

---

## Step 2 — Unzip the tool

Unzip it wherever he likes, e.g. `Documents/trading-tool`. Then in Terminal:

```
cd ~/Documents/trading-tool
```

(Drag the folder onto the Terminal window after typing `cd ` if he doesn't
want to type the path.)

---

## Step 3 — Install the two libraries

```
pip3 install kiteconnect pandas
```

If that errors with "externally managed environment", use:

```
pip3 install --break-system-packages kiteconnect pandas
```

---

## Step 4 — Start it and log in

```
python3 gui.py
```

The window opens. Click **Login to Zerodha** (top bar).

The first time only, it asks two questions:

- **API key** → paste the key from Step 1
- **API secret** → paste the secret from Step 1

It saves both to `~/.trading-tool/.env`, owner-readable only. He will never be
asked for them again — not tomorrow, not after re-downloading the tool.

Then his browser opens **Zerodha's own login page**. He logs in with his client
ID, password and TOTP as normal. The browser bounces back to a local page that
says it worked, and the tool picks the token up by itself.

Top bar should now read **Kite connected**.

---

## Step 5 — Every day after that

Zerodha wipes access tokens every morning around 6–7:30 AM IST. So each day:

```
python3 gui.py
```

then click **Login to Zerodha** once. Two clicks, no copy-pasting. The key and
secret are already saved; only the daily token is refreshed.

---

## If something goes wrong

Run:

```
python3 check_setup.py
```

It prints where it looked for the settings, what it found, and what's missing.
Every secret is masked, so that output is safe to send to you.

Common ones:

| What he sees | What it means |
|---|---|
| Login opens, then times out after 90s | Redirect URL in the Kite app isn't exactly `http://127.0.0.1:5055/` |
| "Port 5055 is already in use" | Something else has the port. `check_setup.py` suggests a free one — set `KITE_REDIRECT_PORT` in `~/.trading-tool/.env` **and** change the Redirect URL in the Kite app to match. Both, or it breaks. |
| It keeps asking for the API key | A blank `KITE_API_KEY` is exported in his shell profile and shadows the saved one. `check_setup.py` flags this explicitly. |
| "market closed" | Correct outside 9:15–15:40 IST. Not an error. |

---

## Appendix — installing Python 3

The tool needs Python 3.9 or newer, **with Tk** (that's what draws the window).
Most trouble on this step is the Tk part, not Python itself.

### Check first — it may already be there

**Mac**, open Terminal (Cmd+Space, type "terminal"):

```
python3 --version
```

**Windows**, open PowerShell (Start menu, type "powershell"):

```
python --version
```

If either prints `Python 3.9.x` or higher, skip ahead — but still run the Tk
check at the bottom of this appendix.

### Mac

macOS ships a `python3`, but the Tk that comes with it is old and the window can
look wrong or fail to open. Use the official installer instead:

1. Go to **https://www.python.org/downloads/macos/**
2. Download the latest **macOS 64-bit universal2 installer** (a `.pkg`).
3. Open it and click through. It bundles a current Tk, so nothing extra to do.
4. Close Terminal and open a new one, then check:

```
python3 --version
```

*If you prefer Homebrew:* `brew install python python-tk` — the second package
is not optional, Homebrew ships Tk separately and without it `gui.py` dies with
`ModuleNotFoundError: No module named 'tkinter'`.

### Windows

1. Go to **https://www.python.org/downloads/windows/**
2. Download the latest **Windows installer (64-bit)**.
3. Run it. On the very first screen, **tick "Add python.exe to PATH"** at the
   bottom before clicking Install Now. This one checkbox causes most of the
   "python is not recognized" problems later.
4. Leave "tcl/tk and IDLE" ticked in the optional features — that is Tk.
5. Close PowerShell, open a new one, then check:

```
python --version
pip --version
```

**On Windows the command is `python`, not `python3`.** So everywhere in this
guide, run:

```
python gui.py
python check_setup.py
pip install kiteconnect pandas
```

*Microsoft Store version:* also fine, and it registers `python3` as well. Avoid
mixing the two — pick one.

### Confirm Tk works, on either OS

Mac:

```
python3 -m tkinter
```

Windows:

```
python -m tkinter
```

A small grey window titled "tk" should pop up with a Quit button. If it does,
`gui.py` will open. Close it and carry on. If instead you get
`No module named 'tkinter'`, install the Tk package for your Python — on
Homebrew that's `brew install python-tk`, on Windows re-run the installer and
tick tcl/tk under Optional Features.

---

## Two things to tell him honestly

**The subscription buys independence, not better data.** The candles and option
chain the tool reads are identical for every Kite user. His own app means his
own token on his own machine, which is the right way to do it — but he is not
getting anything you don't have.

**The measured result is negative.** Backtested on 8,837 real signals over three
years of 15-minute candles, the average trade was about +0.02R gross and roughly
**−0.13R after brokerage, spread and theta**. Give him that number before he
pays the ₹500, not after.

This tool never places an order. It shows a suggestion; every order is placed by
hand in Zerodha. It is not SEBI-registered investment advice.
