# Nifty / BankNifty / Sensex CE-PE Signal Tool

A rule-based decision-support tool for index options trading: a **TradingView
Pine Script indicator** for on-chart signals, and a **Python script** that
combines trend + momentum + option-chain OI into a BUY CE / BUY PE / WAIT
suggestion with a suggested strike, 3-tier targets (T1/T2/T3), a stop-loss,
and — when option-chain/live data is available — the real live option
premium (LTP) for that strike with its own 3 premium-based targets.


## The window

The screen is drawn by `skin.py` on a single canvas — no native buttons, tabs or
checkboxes anywhere in the main view. That is deliberate. Tk's own widgets look
different on macOS, Windows and Linux and cannot do gradients or rounded
corners, so the layout would have drifted apart on each machine. Painting it
means one appearance everywhere, and it means the whole screen can be rendered
to a PNG without a display, which is how the layout is checked.

Three files:

| File | Does |
|---|---|
| `theme.py` | every colour and font, with the validator's verdict recorded beside the ones that carry meaning |
| `ui_kit.py` | gradients, rounded cards, pill tabs, the ring gauge, the sparkline, the rail icons |
| `skin.py` | the layout itself, plus hit-testing for clicks |
| `skin_bridge.py` | connects `gui.py`'s existing render code to the painted screen |

`gui.py` was not rewritten. It still calls `.config(text=…)` on about forty
labels; `skin_bridge.py` hands it stand-ins that file each value under a name
and repaint. All the trading logic — the worker thread, the ticket lock, the
trade tracking — is untouched.

### The left rail

| Icon | Page |
|---|---|
| pulse | the signal board (Signal and Chart tabs) |
| grid | Market Map — the sector heat map, full width |
| bars | Your trades — the summary from `trades.csv` |
| bell | switches the popup alerts on and off |
| gear | Settings — token state, where credentials live, which files are written |

The bell is a switch rather than a destination. The icons are spaced by solving
for the room between the logo and the theme toggle rather than by a fixed
pitch, so the rail stays clear of the bottom controls at every window size the
app allows.

The Market Map used to open in its own window. It is a page on the rail now,
which gives it the whole area instead of a third of it.

### When a signal is shown but not taken

A live signal and an issued ticket are different things. Five rules sit between
them, and the ticket now names the one that is holding it rather than asserting
a reason:

| Badge | What is actually happening |
|---|---|
| `CONFIRMING` | the direction has to hold for `SIGNAL_CONFIRM_SECONDS` before anything is issued |
| `COOLDOWN` | `MIN_MINUTES_BETWEEN_TICKETS` since the last ticket has not elapsed |
| `POSITION OPEN` | a ticket is already running on this index and is left alone |
| `ALREADY TAKEN` | this index was ticketed in this direction today, and re-entry is off |
| `DAY LIMIT` | the daily brake in `entry_block()` |

The screen used to print "a ticket is issued when the direction changes"
whatever the reason. That sentence is true for exactly one of those five rows,
and it is the least common one — so a 100%-confidence signal waiting out a
cooldown read as though the tool disagreed with a screen full of agreement.

`MIN_MINUTES_BETWEEN_TICKETS` is **0** — the twenty-minute hold was removed by
request, so `COOLDOWN` no longer appears for that reason. Put a number of
minutes back in `config.py` to restore it; `TICKET_GAP_PER_INDEX` then decides
whether that gap is shared across the three indices or counted separately.

Auto re-arm keeps its own floor, `REARM_MIN_SECONDS = 60`, which is not a churn
brake and is not meant to be tuned. Re-arm skips the confirmation window by
design, so with the gap at 0 and the daily limits switch off, nothing else
stands between a close and the next open: a ticket whose stop is already
breached closes on the next evaluation and re-arms immediately, four times a
second, until the option chain refreshes. That was thirteen tickets in twelve
seconds when it happened. A genuine re-entry a minute later is still taken.

### One target, not three

The three tiers were never partial exits. The comment in `config.py` said
"scale out at each tier" — that is how a person would trade them, but the tool
has never done it: it holds the whole position and closes on **one** exit. T1
and T2 tick, timestamp and tell you the move is working. They book nothing.

So this is already a single-target system, and `EXIT_AT_TARGET` is that target:

```python
PREMIUM_TARGET_PCTS = [20, 40, 75]   # where T1, T2, T3 sit
EXIT_AT_TARGET      = "T3"           # which one ends the trade
```

Nearer target = higher win rate, smaller wins. Further = the reverse. Measured
on the 162 logged trades, counting only the ones the market actually decided:

| Exit at | Win rate | Avg reward:risk | Break-even needs | R per trade |
|---|---|---|---|---|
| T1 +20% | 75.0% | 0.54 : 1 | 64.8% | +0.12R |
| T2 +40% | 61.2% | 0.95 : 1 | 51.2% | +0.09R |
| T3 +75% | 48.9% | 1.36 : 1 | 42.4% | −0.05R |

Read that with care. Between 98 and 117 of the 162 were closed early by a
signal flip before the market answered, and the ones that DID resolve are the
ones that moved decisively — which flatters every row. `target_study.py` prints
this table for any `trades.csv` and shows the size of the unknown column.

The setting is frozen into each ticket at entry, like the lots are, so changing
it at lunchtime cannot move where a running trade was supposed to get out.

### Colours that mean something

Direction is green up, red down, everywhere — the trend card, the vote list and
the chart all agree. The three targets are one hue at three brightnesses rather
than three different colours, because T1/T2/T3 are one thing at increasing
distances, not three categories. Four unrelated hues were tried first and the
colour-vision validator rejected them: blue against violet separates by ΔE 1.9
under protanopia and by 9.8 even with normal colour vision.


## Read this first — what this tool is and isn't

- **It is**: a systematic way to combine a few well-known technical signals
  (EMA trend, Supertrend, RSI, VWAP) and, where available, option-chain OI/PCR
  into one score, so you're not eyeballing five indicators separately.
- **It is not**: a guarantee of accuracy. No tool can predict Nifty/BankNifty/
  Sensex direction reliably — options are especially unforgiving because of
  time decay and volatility. Treat every signal as one input, confirm it
  yourself on your TradingView chart, and never skip your own risk management.
- **It does not place trades.** It only prints/suggests. You place every
  order yourself in Zerodha (Kite).
- **Not SEBI-registered investment advice.** This is a personal-use technical
  tool, for your own analysis and education.
- **Run this on your own computer**, not in a locked-down sandbox — it needs
  normal internet access to reach Yahoo Finance / NSE / Zerodha.

---

## Part 1 — TradingView Pine Script indicators (two of them, used together)

There are now **two** Pine scripts, for two different jobs:

| File | Apply it on | What it tells you |
|---|---|---|
| `nifty_banknifty_sensex_signal.pine` | The **index chart** (Nifty 50 / Bank Nifty / Sensex) | Direction (CE or PE) and which ATM strike to look at |
| `option_premium_signal.pine` | The **option contract's own chart** (e.g. search `NSE:NIFTY25081424000CE`) | Real live entry/target/SL, since it runs directly on the premium's own candles |

This split exists because Pine Script can't reliably auto-link "today's ATM
option" into the index chart (see the note in Part 2 below) — but if you
just open the option's own chart directly, its `close` price already *is*
the live premium, no linking needed at all. That's the simplest, most
reliable way to get genuinely live premium numbers.

**Workflow:** check the index chart's indicator for direction + strike →
search that exact option contract on TradingView → open its chart → add
`option_premium_signal.pine` there → get your real entry/T1/T2/T3/SL in
premium terms.

### 1a — Index chart indicator

File: `nifty_banknifty_sensex_signal.pine`

This runs directly on any TradingView chart (Nifty 50, Bank Nifty, or Sensex,
any timeframe) — no external data needed, since TradingView already has the
price feed.

**Setup:**
1. Open TradingView → open a Nifty/BankNifty/Sensex chart.
2. Pine Editor (bottom panel) → New blank script → delete the default code →
   paste the contents of `nifty_banknifty_sensex_signal.pine`.
3. Click **Add to Chart**.
4. You'll see: EMA20/EMA50, Supertrend, VWAP, a small score dashboard
   (top-right table), and green/red triangles with labels showing suggested
   entry and **3 targets (T1/T2/T3) + stop-loss** whenever the combined
   score crosses the threshold.
5. **Set alerts**: right-click the chart → Add Alert → Condition → pick this
   indicator → choose "CE Buy Signal" or "PE Buy Signal" → set notification
   (App/Email/Webhook). You'll get pinged the moment a signal fires, without
   staring at the screen.

Tune the inputs (gear icon) per index — e.g. Bank Nifty is more volatile, so
you may want a larger ATR multiplier for target/SL than Nifty.

**Optional — show the real option premium (LTP) on the chart too:**

TradingView can't automatically figure out "the ATM Nifty option for today"
— option contracts are separate instruments with their own symbols that
change every expiry, and Pine Script has no built-in way to look that up. So
this is a one-time-per-trade manual step:

1. In TradingView's top search bar, search the index (e.g. "NIFTY"), then
   filter to **Options**, pick the nearest weekly expiry and the ATM strike
   (the strike the indicator's dashboard/labels suggest), and open that
   specific Call (or Put) contract's chart to see its exact symbol.
2. Open this indicator's **Settings → "LIVE Option Premium"** group, and
   paste that symbol into the **Call (CE) option symbol** field (or **Put**,
   depending on which side you're trading).
3. Tick **"Enable live option premium"**. The dashboard's "Call LTP" / "Put
   LTP" row will now show the real live premium, and any new signal's label
   will include 3 premium-based targets (+20% / +40% / +75% by default —
   editable) and a premium stop-loss (-25% by default) calculated off that
   real price.
4. You'll need to update this symbol yourself when the expiry rolls or the
   ATM strike moves meaningfully — the indicator does not auto-track it.
   Live updates also require TradingView's NSE F&O real-time data
   entitlement; without it the premium will be delayed.

This linking approach works, but it's the more fragile of the two options
below — if you just want simple, always-reliable live premium numbers,
use 1b instead.

### 1b — Option-chart indicator (simpler, always reliable)

File: `option_premium_signal.pine`

Instead of linking a symbol into the index chart, apply this indicator
**directly on the option contract's own chart**:

1. In TradingView's search bar, search the index (e.g. "NIFTY"), filter to
   **Options**, and open the specific Call or Put contract's own chart (the
   one the index-chart indicator suggested as ATM).
2. Pine Editor → paste in `option_premium_signal.pine` → Add to Chart.
3. Because you're now on the option's own chart, `close` already *is* the
   live premium — no linking, no symbol-paste step, nothing that can throw
   an "invalid symbol" error. The dashboard, signal labels, and T1/T2/T3/SL
   are all directly in real premium terms, live.
4. Set alerts the same way as before (Add Alert → this indicator → "Buy
   Option Signal" / "Exit/Avoid Option Signal").

The tradeoff: you have to re-open a new option contract's chart (and
re-add this indicator, or just switch symbol on the same chart — the
indicator adapts automatically) whenever you switch strikes or the expiry
rolls. But there's nothing to configure or keep in sync — it just works off
whatever chart it's on.

---

## Part 2 — Python signal tool (technical + option-chain OI combined)

Files: `main.py`, `signal_engine.py`, `indicators.py`, `data_providers.py`,
`config.py`, `kite_login_helper.py`, `requirements.txt`

### Install

```bash
cd trading-tool
python3 -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

### Two modes

| Mode | Data source | Cost | What you get |
|---|---|---|---|
| `free` (default) | Yahoo Finance (price) + NSE public site (option chain) | Free | Delayed-ish price, Nifty/BankNifty OI+PCR. **Sensex has no free public option-chain source**, so Sensex runs technical-only in this mode. NSE occasionally rate-limits/blocks scripted requests — the tool retries gracefully but isn't 100% reliable. |
| `kite` | Your Zerodha Kite Connect subscription | **₹500/month** — the "Connect" plan (Zerodha developer console). Order/portfolio APIs are free now, but you specifically need market data, which is what the ₹500/month plan is for. | Real-time ticks, real OI for **all three indices including Sensex**, more reliable. |

### Run in free mode (no setup needed beyond `pip install`)

```bash
python main.py --index NIFTY
python main.py --index BANKNIFTY --interval 5m
python main.py --index SENSEX --interval 1d
```

### Run in live/loop mode (auto-refreshes during market hours)

```bash
python main.py --index NIFTY --live --refresh 60
```

This keeps running continuously while you trade, and is built to stay
reliable over a multi-hour session:

- **Market hours aware, using real India time — not your computer's own
  timezone**: only fetches/refreshes Mon-Fri 09:15-15:30 **IST**. This is
  computed by explicitly converting to Asia/Kolkata (via `now_ist()` in
  `main.py`), so it works correctly even if you're running this from the US,
  UK, or anywhere else — it does NOT just read your machine's local clock.
  Outside market hours it prints a clear **"MARKET IS CLOSED"** banner
  (with the time shown in IST) and the next session's open time, and polls
  much less frequently (no point hammering the network while closed).
  Weekends AND known NSE/BSE trading holidays are both detected (see
  `NSE_HOLIDAYS_BY_YEAR` in `main.py`) — currently populated for 2026.
  **This list needs a manual update every December** for the following
  year, or it silently falls back to weekend-only detection.
- **Auto-retries** transient fetch failures (NSE/Yahoo occasionally hiccup)
  a couple of times before giving up on that cycle, so one bad request
  doesn't stop the loop.
- **Refreshes its NSE session periodically** (every 20 cycles by default)
  since cookies can go stale over a long-running session.
- **Flags when the signal changes** — prints a `>>> SIGNAL CHANGED <<<`
  line whenever the bias/strike differs from the previous update, so you
  don't have to re-read the full report every single cycle to notice.
- Press `Ctrl+C` to stop it cleanly at any time.

### Switching to Kite Connect (real-time) once you have the API key

1. Go to the [Zerodha developer console](https://developers.kite.trade/) and subscribe to the **Connect** plan — currently **₹500/month**, billed monthly, separate from your normal Zerodha trading/brokerage account. (Zerodha made basic order-placement APIs free in 2024, but that free "Personal" tier does **not** include market data — you need the paid Connect plan specifically for the live price/option-chain data this tool uses.)
2. Create an app there → note down the **API key** and **API secret**, and set
   the app's **Redirect URL** to exactly:

   ```
   http://127.0.0.1:5055/
   ```

   This one matters. That address is where the tool listens for Zerodha to
   hand the login back, which is what removes the daily copy-paste. If it
   points anywhere else, the login will time out with a message telling you
   to come back and fix it.
3. Click **Login to Zerodha** in the tool. First time only, it asks for your
   API key and secret and saves them to `.env` — those don't expire.

That's the whole setup. See below for what happens daily.

### The daily login — one click

Zerodha clears every access token each morning (somewhere between roughly
05:00 and 07:30 IST) and offers nothing longer-lived. So a token has to be
fetched once per trading day, every trading day. That part is Zerodha's
design and no tool can avoid it.

What the tool does avoid is the *work*. The old flow was seven steps, most of
them copy-paste. Now:

1. Press **Login to Zerodha** (top bar, next to the mode dropdown).
2. Your browser opens on Zerodha's own login page. Log in the way you always
   do — user ID, password, TOTP.
3. Done. The tool catches the redirect, exchanges it for the day's token,
   writes it to `.env`, and switches Mode to `kite`.

The badge beside the button tells you where you stand before you touch
anything: **● logged in**, **● token expired**, or **● not logged in**. It's
checked against Zerodha at launch, not guessed from the clock — a token can
be revoked early, and one generated before the morning flush dies at the
flush no matter how recently you made it.

If you press **Start** in kite mode with a dead token, you get an offer to log
in right there instead of an error.

**No password is stored anywhere.** You type your Zerodha credentials into
Zerodha's own page in your own browser. The tool only ever sees the one-time
`request_token` that Zerodha hands back afterwards. The only things in `.env`
are your API key, API secret, and the day's access token — and that file is
written owner-only (`chmod 600`).

**Timing, if you're outside India.** The token can be generated any time from
about 07:30 IST onwards and lasts the rest of that trading day. In US Eastern
that's from roughly **10pm the night before**, and the market opens at 11:45pm
ET. So logging in once before bed covers the whole session you'll sleep
through.

**No browser (Termux, headless)?** Run `python3 kite_auth.py` instead — same
flow, prints the URL for you to open on the phone's browser.

**Why 5055 and not 5000?** Because macOS gives 5000 and 5001 to AirPlay
Receiver, so anything defaulting to 5000 fails on a stock Mac with a confusing
"port in use" error. 5055 isn't claimed by anything common.

**If 5055 is taken too**, the tool checks which ports are actually free, names
one, and prints the exact two lines to change. Put the line it gives you in
`.env`:

```
KITE_REDIRECT_PORT=5056
```

and set your app's Redirect URL to `http://127.0.0.1:5056/` to match. The two
numbers just have to agree.

Then, if you prefer the terminal tool to the window:

```bash
python main.py --index SENSEX --mode kite --live --refresh 30
```

It reads the same `.env`, so no `export` needed.

### How the combined score works

Each of these contributes **+1 (bullish) / -1 (bearish) / 0 (neutral)**:

- Price vs EMA20/EMA50 trend
- MACD histogram (momentum — accelerating up or down)
- RSI vs 50 (avoiding overbought/oversold extremes)
- Price vs VWAP
- Option-chain PCR (Put-Call Ratio) — skipped if unavailable

(Earlier versions used Supertrend instead of MACD here. Supertrend and the
EMA trend check are both fundamentally "is price above/below a moving
reference," so they were almost always agreeing with each other — a "4/4
agree" reading often really reflected one underlying trend signal counted
twice, not four independent confirmations. MACD's histogram measures
momentum instead, so it's a genuinely different vote.)

Scores are summed. A combined score of **+3 or more** → `BUY CE`; **-3 or
less** → `BUY PE`; otherwise → `WAIT`.

**Indicators that abstain don't count against you.** Each indicator can vote
bullish (+1), bearish (−1), or *undecided* (0) — PCR in particular sits
undecided whenever the put-call ratio is in its balanced 0.8–1.2 band, which
is most of the time. The requirement is based on how many indicators
actually **had an opinion**, not how many exist. (This was a bug until
recently: an abstaining PCR still pushed the requirement from 3 up to 4, so
three agreeing indicators fired a signal *without* option-chain data but
stayed silent *with* it — more information made the tool less willing to
trade. Votes *against* still count in full; abstaining is neutral,
disagreeing is not.)

**Trend-strength gate (ADX):** even when the score qualifies, the signal is
blocked if ADX (a measure of trend strength, not direction) is below
`ADX_TREND_THRESHOLD` (default 20) — a low ADX means the market is choppy/
directionless, and "indicator agreement" there is much more likely to be
noise. The report shows `ADX=... < 20 — trend too weak` when this happens,
so you can see it was a real signal that got vetoed, not just silence.

The tool then suggests the ATM strike (spot rounded to the nearest strike
step) and levels:

**Stop-loss** is anchored to the nearest real recent swing low/high (the
lowest low / highest high of the last `SWING_LOOKBACK` candles, default 12),
with a small ATR buffer — a structural level, not an arbitrary distance. If
no usable swing point exists yet (e.g. right after a sharp move) or it sits
too close to entry, it falls back to a fixed ATR multiple (`SL_ATR_MULT`).

### Targets are built from how far the market can actually go

This is the important part. A target that's simply "2× my risk" says nothing
about whether the index can realistically travel that far before the session
ends. So instead, the tool measures the realistic distance three ways, all
from live market data:

1. **The option market's own expected move.** The at-the-money call price
   plus the at-the-money put price (the *straddle*) is what the options
   market is collectively pricing as the move by expiry. That's real money
   from real traders, not a formula. It's scaled down by the square root of
   the time actually remaining, giving the expected move for the rest of
   today.
2. **Open-interest walls.** The strike carrying the heaviest call OI tends
   to cap rallies; the heaviest put OI tends to floor declines. A target
   sitting beyond the wall has to break through it first.
3. **The day's remaining range.** Measured, not assumed: the tool averages
   the high-low range of the previous days in the fetched data, compares it
   to how much today has already covered, and takes the difference.

**The tightest of the three wins.** T3 is placed at that limit, with T1 and
T2 at 40% and 70% of the way there (`REACH_FRACTIONS`) as partial exits. The
report names which limit was binding, e.g. *"capped by the call-OI wall at
24500"*.

**If there isn't enough room, no targets are shown at all.** When the
reachable distance falls below `MIN_REACH_TO_RISK` × the stop distance
(default 0.6), the trade is rejected — you'd be risking materially more than
the market is likely to hand back. The ticket shows `NO ROOM / NOT WORTH IT`
and explains why, instead of printing targets that can't be reached.

That threshold sits well below 1.0 on purpose. The reach estimate is
conservative by construction — it counts only the move left in *today's*
session and caps at OI walls that price trades through regularly — so the
measured reward:risk understates the real one. Two limits are also **floored
at half the expected move** so they can't veto a trade on their own:

- *The day's remaining range.* A trending day routinely blows through its
  typical range; that's what a trend is. Treating it as a hard ceiling made
  the tool blindest on exactly the days worth trading, and produced an
  absurd cliff where 93% of the range used rejected a trade while 100% used
  allowed it.
- *The OI walls.* They're resistance, not brick walls. Capping T3 at the
  wall is sensible; letting a wall 150 points away veto every signal all day
  was not.

The **reward:risk** ratio is shown on every signal, so you can see the trade
quality before entering rather than guessing.

**Option-premium targets** are converted from those index-level targets
using ~0.5 delta for an ATM option — so they inherit the same grounding.
Note this ignores **time decay**: a slow move will land below these numbers
even if the index does reach the target.

Set `TARGET_MODE = "risk"` in `config.py` to go back to the old fixed
1R/2R/3R model. The market-based model needs an option chain for the
expected-move and wall components; without one (e.g. Sensex in free mode) it
falls back automatically, using whatever limits it can still measure.

### Trend days, and why "no room" used to fire on the best setups

Two calibration faults were found live, on a BankNifty day reading STRONG
DOWNTREND, ADX 30.9, and a **unanimous −5 of 5 score** — a textbook signal,
rejected as NOT WORTH IT with a reward:risk of 0.3:1.

**1. The day-range limit didn't know it was a trend day.** "How much of a
normal day's range is left" was computed as *average prior daily range minus
what today has used*. That is a fair estimate on an average day and a
systematic under-estimate on a trending one — a trend day is by definition a
range-expansion day. The effect was perverse: the tool went blindest on
exactly the days worth trading. The typical range is now scaled by trend
strength (`RANGE_EXPANSION_BY_ADX` in `config.py`).

Don't take those multipliers on trust — measure them against real history:

```bash
python3 backtest.py --measure-expansion
```

That prints, per index and pooled, how much further the market actually
travelled on high-ADX days. If the measured numbers are lower than what's in
`config.py`, turn the setting down.

**2. Nothing capped how wide the stop could get.** The stop anchors to the
last swing high/low, which is right in principle. But in a fast move that
swing can be 480 points away on BankNifty — nearly 4x ATR, not a stop anyone
would actually take intraday. Worse, that distance is the denominator of the
reward:risk test, so an over-wide stop could veto a good trade by itself.
Beyond `MAX_RISK_ATR_MULT` (2.0x ATR) the swing is now treated as unusable and
the stop is placed at the cap, labelled `swing_capped` in the trade log and
explained as such on the Chart tab.

On the case above, the two together move reward:risk from 0.29 to 4.14 — and
note *how*: the stop got tighter and the reach estimate got honest.
`MIN_REACH_TO_RISK` was not touched. No threshold was loosened.

**The tradeoff, stated plainly.** A tighter stop gets hit more often. This
buys more signals and a better reward:risk on paper, at the cost of a lower
win rate. Whether that nets out positive is a question for the backtest, not
for intuition — run `python3 backtest.py` and read the OLD vs NEW comparison
before trusting it with money. Both changes can be reverted independently:
set `TREND_RANGE_EXPANSION = False` or raise `MAX_RISK_ATR_MULT`.

### "I'm not getting any signals"

Three separate filters have to pass before a trade fires — the indicator
score, the ADX trend-strength gate, and the reachability check. That's
deliberate, but stacked together they can stay quiet for a long time.

**The tool now tells you which one is blocking.** Look for the
`WHY THERE'S NO TRADE RIGHT NOW` block in the report (or the "Waiting
because:" text on the ticket in the window). It shows how many points short
of the threshold you are, which indicators are voting against, and the
current ADX. So you can see whether it's nearly there or nowhere close.

Before changing anything, check the obvious ones:

- **Is the market actually open?** NSE trades 09:15–15:30 **IST**. If you're
  outside India that may be the middle of your night — from US Eastern, for
  example, that's roughly 11:45pm to 6:00am. The tool shows a
  `MARKET IS CLOSED` banner when it's shut, and cannot produce signals then.
- **A signal fires on a direction CHANGE**, not continuously. If it fired
  once this morning and the direction hasn't changed since, it stays quiet —
  that's correct, not broken.

If you want more signals, set `SIGNAL_STRICTNESS` in `config.py`:

| Setting | Agreement needed | ADX | Min reward:risk |
|---|---|---|---|
| `"strict"` (default) | ≥3 agree, ≤1 disagrees | 20 | 0.6 |
| `"balanced"` | ≥3 agree, ≤2 disagree | 18 | 0.45 |
| `"loose"` | ≥2 agree, ≤2 disagree | 15 | 0.3 |

The requirement is stated as **counts of agreement**, not as a score
threshold. That's deliberate: score = agreeing − disagreeing, so a single
dissenter moves the score by *two*, and a threshold meant to tolerate one
dissenter silently demanded unanimity instead. With five indicators voting
the only reachable scores are −5, −3, −1, 1, 3, 5 — a requirement of 4 could
never be met by anything short of a clean sweep, and with only two
indicators voting nothing could fire at all. Counting agreement directly
eliminates that whole class of bug.

Be clear-eyed about this: loosening does **not** find more good trades, it
lowers the bar for what counts as one. Expect the win rate to fall. `loose`
is for watching the machinery work, not for trading real money.

### Adjusting the strategy

Edit `config.py`:
- `EMA_FAST` / `EMA_SLOW`, `RSI_LENGTH`, `MACD_FAST` / `MACD_SLOW` /
  `MACD_SIGNAL` — indicator settings.
- `ADX_LENGTH` / `ADX_TREND_THRESHOLD` — the trend-strength gate. Raise the
  threshold for fewer, more conservative signals (only trades clearly
  trending markets); lower it to allow more signals through in choppier
  conditions.
- `SWING_LOOKBACK` / `SL_BUFFER_ATR_MULT` / `MIN_RISK_ATR_MULT` — how the
  structural stop-loss is found. `RR_MULTS` — the R-multiples used for
  T1/T2/T3. `TARGET_ATR_MULTS` / `SL_ATR_MULT` — the ATR-based fallback,
  used only when there's no usable swing point.
- `INSTRUMENTS[...]["lot_size"]` / `["strike_step"]` — double-check these
  against Zerodha's current contract specs before trading; exchanges change
  lot sizes periodically.

**Before trusting a strategy change (including the one above):** run
`python backtest.py` — it now backtests the OLD and NEW strategy side by
side on the same 5 years of data and prints a comparison table, so you can
see for yourself whether a change actually helped instead of taking
anyone's word for it. See the disclaimer at the top of `backtest.py` for
what this can and can't tell you.

### Prefer a window instead of the terminal?

File: `gui.py`

Same tool, same logic, but in a small native desktop window instead of a
scrolling terminal — better for glancing at while you're actually trading.

```bash
python3 gui.py
```

Pick your mode and hit **Start**. Hit **Stop** anytime, or just close the
window.

The window **scrolls** — Tkinter doesn't do that on its own, so the whole
body sits in a scrollable canvas with a real scrollbar and mouse-wheel
support (Windows, macOS and Linux wheel conventions all handled). Resize it
however you like; nothing gets stranded below the bottom edge.

### All three indices at once

There is no index dropdown any more. **One Start monitors Nifty, BankNifty
and Sensex simultaneously**, and the tabs across the top switch which one is
displayed — instantly, because every index is already being analysed. No
stopping and restarting to check another index.

Each tab carries a coloured dot showing that index's state at a glance:

| Dot | Meaning |
|---|---|
| green `BUY CE` / red `BUY PE` | a signal is live on that index |
| green/red `CE open` / `PE open` | a ticket is running there |
| amber `no room` | real setup, rejected for poor reward:risk |
| grey + the spot price | nothing doing |

So a BankNifty signal is visible while you're looking at Nifty. Critically,
**a ticket on a background index keeps being tracked** — its targets and stop
are checked against live price whether or not you're looking at it, and it
will alert you when one is hit.

### The Chart tab — seeing the reason, not just the verdict

Under the index tabs there is a second row: **Signal** and **Chart**.

*Signal* is the ticket view you already know. *Chart* draws the same candles
the indicators are computed from, so the numbers on the ticket have a visible
position rather than being asserted:

- **Candles** — the last 70 bars, with the in-progress one outlined in amber
  and labelled `forming`, because a half-built candle looks exactly like a
  finished one and its close is still going to change.
- **EMA20 (blue), EMA50 (purple), VWAP (amber dashed)** — the three lines the
  Trend and VWAP votes are actually decided on.
- **Your levels** — once a ticket is issued, entry, T1, T2, T3 and the stop
  are drawn as labelled lines. Before a ticket exists, the levels the *current*
  signal would use are drawn faintly as dotted lines, so you can see what is
  brewing without mistaking it for a live trade.
- **A marker on the candle the signal fired on**, so "why here?" has an
  answer you can point at.

Anything that would fall a long way outside the visible price range is left
at the edge rather than allowed to squash the candles flat.

Underneath sits the **WHY** panel — the same decision, in sentences that quote
the actual readings and thresholds:

> ▲ **Trend** — Price 24,963 is above the 50-EMA (24,779), and the 20-EMA
> (24,863) is above it too. Both conditions have to hold, so the averages are
> stacked in an uptrend.
>
> – **RSI** — RSI is 92, above 75 — overbought. The move up is real but too
> stretched to call a fresh entry, so RSI abstains rather than voting bullish
> into a likely pullback.
>
> ✓ **Gate** — ADX is 61.7, at or above the 20 threshold — there is a genuine
> trend in progress.
>
> ▸ **Levels** — STOP 24,818, placed just below the lowest low of the last 12
> candles (24,822) plus a 0.15x ATR buffer...

**The reasoning is frozen when the ticket is issued.** Opening the Chart tab
an hour later shows why it *fired*, not what the market looks like now — those
are different questions, and the second one is useless for judging a trade
after the fact. The `This ticket` / `Right now` toggle switches between them.

When no ticket is open the panel simply explains the live read, including why
there *isn't* a signal.

### Live prices (kite mode)

There is no "refresh rate" setting, because in kite mode prices don't get
polled — they **stream**. The tool opens a WebSocket to Zerodha (the same
mechanism the Kite app itself uses) and the exchange pushes every tick as it
happens. The spot price, your open trade's premium, its rupee P&L and its
target/stop checks all move continuously. A **◉ LIVE** badge sits in the top
bar; it changes to `stale Ns` if ticks stop arriving and `reconnecting` if
the socket drops, so a feed that has quietly died can't keep pretending.

Because targets are checked on every tick rather than on a timer, a target
registers the *instant* price touches it instead of up to a refresh-interval
later.

**The indicators are live too.** The streamer assembles the *in-progress*
15-minute candle from the ticks themselves — open, high, low and running
close — and that candle is appended to the completed ones before EMA, MACD,
RSI, ADX and VWAP are recalculated. So the indicator votes, the confidence,
the ADX gate, the market-trend strip and the room-to-run figure all move
continuously, exactly the way a charting platform redraws an indicator
before the bar has closed. The whole recompute measures about 15ms, and runs
once a second (`LIVE_ANALYSIS_MS`).

**Ticket issuance is deliberately debounced.** Intra-candle readings really
do flip back and forth, and freezing a trade's entry, targets and stop off a
reading that vanishes three seconds later would be worse than useless. So a
direction has to survive `SIGNAL_CONFIRM_TICKS` consecutive passes (default
4, i.e. hold for ~4 seconds) before a ticket locks. Once locked, its levels
never move again.

The one thing that genuinely doesn't stream is **open interest** — PCR and
the OI walls. That's an exchange snapshot figure that doesn't change
meaningfully second to second, so it refreshes on the slower
`ANALYSIS_INTERVAL_SEC` cycle (default 30s) along with the REST candle
fetch. Asking more often would burn API quota for identical answers.

Free mode has no streaming source — Yahoo and NSE are request-response only —
so it refreshes on `ANALYSIS_INTERVAL_FREE_SEC` (default 60s) and the badge
shows the interval rather than `LIVE`.

**The market-trend strip** runs across the top, above the ticket. This is
deliberately *separate* from the CE/PE signal: the signal only fires when
nearly every indicator agrees, so it says `WAIT` most of the day and tells
you nothing about what the market is actually doing. The trend strip always
answers the simpler question — is it going up, down, or just chopping:

- **Verdict** — `STRONG UPTREND` / `MODERATE DOWNTREND` / `RANGE-BOUND —
  CHOPPY` / `NOT ENOUGH DATA YET`, colour-coded, with the ADX reading and
  whether momentum is *building* or *fading* underneath. Direction comes
  from EMA20/EMA50; strength comes from ADX alone, so a directionless
  market is labelled chop no matter which way the averages happen to lean.
- **Day move** — points and % from today's open, plus today's high/low, and
  a bar showing where price currently sits inside that range (marker hard
  right = pressing the day's highs, hard left = sitting on the lows).
- **Timeframe agreement** — ▲/▼ for the 15-minute trend, the 1-hour trend,
  and price vs VWAP. The 1-hour read is derived by rolling up the same
  candles already fetched, so it costs no extra network call. All three
  arrows pointing the same way is a much stronger backdrop than one alone.

The same information is printed in the terminal report under a
`MARKET TREND` heading, so `main.py` users get it too.

**The signal ticket.** The centrepiece is a boarding-pass-style ticket, torn
down the middle by a perforated edge:

- **Left stub — the trade.** A status badge (`WAITING` / `PREVIEW` / `OPEN` /
  `TARGET HIT` / `STOPPED OUT`), the action in large type (`BUY CE` /
  `BUY PE`), the contract, then Entry / Now / Spot / rupee P&L, and finally
  four level boxes: T1, T2, T3 and STOP. **Each box's border lights up green
  the moment that target is actually reached** (stamped with the time it
  happened), or red if the stop is hit — so "did it work?" is answered at a
  glance instead of by reading numbers.
- **Right stub — the reasoning.** Confidence, then a row per indicator
  (Trend, MACD, RSI, VWAP, PCR) showing **the actual reading and the vote it
  produced**: ▲ bullish, ▼ bearish, or **–** undecided. A dash means that
  indicator has no opinion right now — PCR sits undecided whenever the
  put-call ratio is inside its balanced 0.80–1.20 band, which is most of the
  time. An undecided indicator does *not* count against you. Below that sits
  the ADX gate (`PASS` / `WEAK` / `BLOCKED`) and how far the market can still
  realistically travel. This side always reflects the *latest* live read,
  even while an older ticket is still being tracked on the left.

Before any trade is locked in, the ticket shows the current live signal
greyed out and badged `PREVIEW`, so you can see what's brewing. Once a real
signal fires, the ticket is **issued**: its entry and T1/T2/T3/SL are frozen
at that instant and never recalculated, giving you one stable set of numbers
to actually trade against.

### Your trades are saved permanently

Every ticket is written to **`~/trading-tool-logs/trades.csv`** — one row
when it opens, another when it closes, with entry, targets, stop, what was
hit, the outcome and the P&L.

It lives in your **home folder, not next to the code**, on purpose. You
unzip a fresh copy of the tool each time it's updated, and a log stored
beside the code would be stranded in the old folder — a month of evidence
scattered across `trading-tool 3`, `trading-tool 7` and `trading-tool 11` is
worth almost nothing. In `~/` every copy of the tool appends to the same
file, forever.

The OPEN row is written immediately rather than on close, so even a crash
mid-trade leaves proof the signal happened. Filter to `event = CLOSE` rows
for analysis.

**Session summary.** A `Save summary` button writes a readable report to the
same folder — trades, win/loss, T1/T2/T3 hit rates, net P&L, broken down by
index. It also generates **automatically the moment the market closes**, so
an overnight run leaves something to read in the morning without you being
awake at 15:30 IST to capture it.

This matters more than it sounds: the SESSION strip in the window is memory
only and dies when you close it. If you're outside India, the market runs
while you're asleep — the CSV and the summary are the only things that turn
"I ran it for a few days" into evidence you can actually judge.

### Market Map

A **Market Map** button opens a live sector heat map of the index's
constituent stocks — HDFCBANK, RELIANCE, INFY and so on — each tile sized by
index weight and coloured by its move today, deep green through deep red.
It answers a question the signal engine cannot: is the whole market moving,
or is one heavyweight dragging the index while everything else goes the
other way? The header shows how many stocks are up versus down and the
weighted average move, which is a useful cross-check on the index price
itself.

**It is live.** It redraws once a second from the same WebSocket everything
else uses (subscribed in quote mode, so each tick carries the day's % change
directly), so it needs **kite mode with the feed running** — press Start
first.

And it tells you when it *isn't* live. If the feed disconnects, goes quiet
for more than 20 seconds, or you press Stop, the tiles flatten to grey, the
title changes to `(not live)` and the status line says exactly which — e.g.
`FEED DISCONNECTED — frozen at the last tick received`. A frozen heat map
that still looked live would be worse than no heat map at all, since you'd
read stale prices as current ones.

The constituent lists live at the top of `market_map.py` and **need
occasional manual updating**, since index membership and weights change when
the exchange rebalances. The weights only control how big each tile is
drawn — a stale weight makes a box slightly the wrong size, it cannot make a
price wrong.

Below the ticket, a **SESSION** strip logs every ticket that closes, with a
running net P&L on the right. The full text report sits underneath, and a
"MARKET IS CLOSED" banner takes over outside trading hours.

Two more controls in the second row:
- **Expiry**: click **"Load expiries"** to fetch the actual list of
  available expiry dates for the selected index (live from NSE/Kite), then
  pick one from the dropdown before hitting Start — or leave it on
  "(nearest)" for the default (nearest weekly) behavior.
- **"Pop up on new CE/PE signal"** (on by default): whenever the tool has an
  actionable BUY CE or BUY PE call — either the first one after you hit
  Start, or a fresh one after the bias changes — a popup window appears with
  the strike, index-level T1/T2/T3/SL, and the live premium targets if
  available, so you don't have to be staring at the window to catch it.
  Uncheck this if you'd rather just glance at the main window instead.

This uses Tkinter, which normally ships with Python already — if you get
`No module named tkinter` when running it: on macOS with Homebrew Python,
run `brew install python-tk`; with the python.org installer, Tkinter should
already be bundled (try reinstalling from python.org if it's still missing).

---

## Part 2b — running it as a website

```bash
python3 web_server.py                 # http://127.0.0.1:8080 — just you
python3 web_server.py --host 0.0.0.0  # reachable from your phone on the same wifi
python3 web_server.py --mode free     # no Zerodha login needed
```

Standard library only — no Flask, no npm, no build step. It starts instantly and
runs unchanged on a Mac, a Linux VPS, or Termux.

**The chart is the same code as the desktop app.** `draw_chart()` was factored
out of the Tk canvas so it can draw onto anything that speaks the canvas
dialect: the window passes a real `tk.Canvas`, the web server passes an
`SvgCanvas` that writes SVG. Reimplementing candles and EMAs in JavaScript would
have guaranteed the two eventually disagreed about what your chart looks like,
with no way to tell which one was wrong. Same for the reasoning — the WHY panel
on the page is `explain.py`, not a copy of it.

The browser sends its own width so the chart is drawn at the size it will be
shown at, rather than stretched to fit afterwards (which squashed the price axis
into an unreadable smear on a phone).

### Where the live data comes from — and who needs a Kite login

**The server holds the credentials. Visitors need nothing.** No key, no login,
no Zerodha account. One worker thread fetches from Kite, computes the analysis
once, and every visitor sees that same result. This is why one ₹500/month app
covers any number of visitors: they aren't each calling Zerodha, the server is.

Which means the daily token is now a *server* problem. If it isn't renewed, the
whole site goes stale for everyone at once.

The desktop one-click login **cannot do this for a hosted server**: it redirects
to `http://127.0.0.1:5055/`, which from your browser means *your own laptop*,
not the server. So the web server has its own login route.

**Keeping it running** — see `DEPLOY.md`. Short version: a ~₹400/month cloud
server with a systemd unit is the only option that gives you a site others can
reach; your Mac with `caffeinate` is fine for yourself; Termux on a phone runs
but Android will kill it and your phone has no reachable address. And you likely
don't need 24/7 — the market is only open 11:45pm–6am your time.

**Where your credentials live.** `~/.trading-tool/.env`, in your home directory,
**not** beside the code. That's deliberate: this tool gets re-downloaded into a
new folder each time it changes, and anything stored next to the code is stranded
in the old folder — which is why it used to keep asking for your API key. Enter
it once and every future copy finds it. An old `.env` beside the code is migrated
across automatically the first time you run `setup_web.py` or `web_server.py`.

**Testing it without buying a domain.** You don't need one. Run
`python3 setup_web.py`, press Enter at the address question, and it runs on your
own machine at `http://127.0.0.1:5055` — the same address the desktop login
already uses, so **nothing in the Kite console needs changing at all**. The home
page doubles as the login callback precisely so one Redirect URL serves both the
window and the website. Buy a domain later, re-run the setup, change one line.

**Setting it up — one command:**

```bash
python3 setup_web.py
```

It asks three short questions, writes `.env`, **invents the admin password for
you**, and prints the exact line to paste into the Kite developer console plus
your daily login link. Nothing leaves your machine.

If you'd rather do it by hand, the two settings are `WEB_PUBLIC_URL` and
`WEB_ADMIN_KEY` in `.env`, and the Kite Redirect URL must be
`<WEB_PUBLIC_URL>/kite/callback`.

**Each morning (after ~07:30 IST / 10pm US Eastern):** open the admin link,
click *Log in to Zerodha*, done. `web_server.py` prints that link every time it
starts, so there's nothing to remember or type out. The page also shows feed
health, last update, and last error.

**Why the admin key matters.** The access token belongs to whoever completed the
login. Leave that route open and a stranger could point their own Zerodha
account at your server and have it run on their credentials. So: the admin
routes are **disabled entirely** unless `WEB_ADMIN_KEY` is set, the key is
compared in constant time, a wrong key returns a plain 404 with no hint that the
route exists, and every login carries a single-use nonce that expires in ten
minutes — a callback the server didn't start is refused.

**When the feed dies**, the page shows an amber banner saying so, because stale
data looks exactly like a quiet market and that is the most dangerous way for a
signal page to fail.

### Before putting it on the public internet

- **No login, no rate limiting.** Anyone who can reach the address sees
  everything. `--host 0.0.0.0` on a laptop means everyone on that wifi.
- **`http.server` is not a hardened production server.** Behind nginx or Caddy
  on a small VPS is fine. Directly on port 80 facing the world is not.
- **Your `.env` lives on whatever machine runs this.** Nothing on the page ever
  contains it — there's a test asserting the API key, secret and access token
  never appear in any response — but that machine now holds live trading
  credentials and is worth protecting.
- **India regulates investment advice.** Publishing buy/sell calls publicly can
  fall under SEBI's Research Analyst / Investment Adviser rules, and much more
  clearly so once money is involved anywhere in the picture. The page carries a
  disclaimer; a disclaimer is not a licence. Read the SEBI section before
  pointing a domain at this.

### What the page shows, deliberately

Alongside the signal it shows the **blockers** when there's no trade, the full
plain-English reasoning, and a **track record built from your actual trade log —
wins and losses both**. It also states, at the top where nobody can miss it,
that a three-year backtest measured this rule set as break-even before costs and
negative after them.

That last part is not decoration. A signal page that shows only its current call
and never its history is asking to be believed rather than checked. If you keep
one thing when you customise this, keep that.

## Part 3a — the intraday backtest (the one that matters)

```bash
python3 backtest_intraday.py --compare
```

**This is the backtest to trust.** Your Kite Connect subscription includes
historical data at no extra cost, and 15-minute candles go back to 2015 — so
this tests the real strategy on the real timeframe, instead of a swing-trading
cousin of it on daily bars.

It calls `signal_engine.build_recommendation()` and
`signal_engine.compute_reachability()` — the same functions the window calls —
rather than a paraphrase. Before printing anything it re-derives the indicators
for 25 sampled bars using the genuine `compute_technical_signal()` and refuses
to report numbers if its fast path disagrees by even 0.02.

Options:

```bash
python3 backtest_intraday.py                  # 3 years, all three indices
python3 backtest_intraday.py --years 5
python3 backtest_intraday.py --index BANKNIFTY
python3 backtest_intraday.py --compare        # with and without the fixes
python3 backtest_intraday.py --no-cache       # force a re-download
```

Candles are cached under `~/trading-tool-logs/history/`, so only the first run
is slow.

**How it's checked for lookahead.** The test suite runs it against pure random
walks, where a strategy that cannot see the future must score zero. It returns
+0.002, −0.079 and −0.088 R across three seeds. On a synthetic market given a
genuine intraday trend, the same code returns +1.6 R. It finds structure when
structure exists and nothing when it doesn't — which is the property that makes
the real numbers worth reading.

**What it still cannot tell you:**

- **Index points, not rupees.** No option premiums, so no theta. A winning
  index move can still be a losing option trade if it takes too long.
- **PCR abstains throughout.** Expired option contracts drop out of Kite's
  instrument list, so no historical option chain can be rebuilt. Every signal
  here was decided by four indicators, not five.
- **Expected move and OI walls are absent** for the same reason. Of the three
  limits on reachability, only the day-range one is live — which is the one the
  trend-expansion fix changes, so that fix *is* tested here.
- **No costs.** No brokerage, STT, slippage or bid-ask.

A good result here is necessary but not sufficient. A bad result is decisive.

## Part 3 — 5-year backtest (checking historical accuracy)

File: `backtest.py`

Run this to see how the technical strategy (EMA + Supertrend + RSI, daily
bars) would have performed on the last 5 years of Nifty, Bank Nifty, and
Sensex data:

```bash
python backtest.py          # defaults to 5 years
python backtest.py 3        # or pass a different number of years
```

It prints, per index: total signals, win rate (target hit before stop,
within a 15-trading-day window), average R-multiple, and max consecutive
losses — plus a combined accuracy figure across all three indices.

**Important caveats about what this number means:**
- It measures whether the **index itself** reached the target level before
  the stop-loss level — it does **not** simulate actual option premiums
  (theta decay, IV changes, spreads, brokerage). Real option P&L is
  typically worse than this index-level number, especially for slow-moving
  signals, because time decay eats the premium while you wait.
- It uses **daily candles**, since 5 years of free intraday data doesn't
  exist anywhere — so it's testing a swing-style variant, not the exact
  5m/15m intraday version in `main.py`. VWAP and option-chain OI/PCR are
  left out (both need data that isn't available historically for free).
- 5 years of history includes very different market regimes (2021 recovery
  rally, 2022 correction, 2024-25 chop) — a decent overall number doesn't
  mean the strategy will keep working in the next regime.
- This is a research tool for *your own* due diligence, not a guarantee.

Run it, and if you want, paste the full output back to me — I'll turn it
into a proper written report with per-index breakdowns and what it does/
doesn't tell you about live trading performance.

---

## Risk management checklist (please actually use this)

1. **Paper trade first.** Run the tool for a few days/weeks and just log what
   it would have suggested vs what actually happened, before risking money.
2. **Position size**: risk only 1-2% of your trading capital on any single
   trade (i.e. size your quantity so that a stop-loss hit costs you that
   much, not more).
3. **Always place a real stop-loss order** in Zerodha the moment you enter —
   don't rely on watching the screen.
4. **Expiry day is dangerous** for index options — premiums can move
   violently on very small index moves. Consider avoiding fresh option buys
   in the last hour before expiry unless you fully understand the risk.
5. **This tool has no memory of your capital, existing positions, or overall
   portfolio risk** — it only looks at one index's chart at a time. You are
   responsible for aggregate risk across your positions.

## Known limitations

- Free-mode NSE option-chain requests can occasionally get blocked/rate
  limited by NSE's anti-scraping checks — if `get_option_chain` fails, the
  tool falls back to technical-only and tells you.
- Yahoo Finance's free intraday data can lag the live market by a few
  seconds to a couple of minutes — fine for swing/positional signals, not
  meant for sub-minute scalping.
- Sensex has no free public option-chain source — use `--mode kite` for a
  full Sensex signal including OI.
- The max-pain / PCR logic is a widely used heuristic, not a proven
  predictive model on its own.

### User accounts (optional, off by default)

```
WEB_REQUIRE_LOGIN=1     # nobody sees the signals without an account
WEB_ALLOW_SIGNUP=1      # strangers can create their own accounts
```

Both default to **off**, and that default is deliberate rather than lazy.

**How the password store is built.** PBKDF2-HMAC-SHA256, 600,000 iterations, a
random 16-byte salt per user, in `~/.trading-tool/users.json` at mode 0600.
Session tokens are 32 random bytes and are *stored hashed*, so reading that file
does not let anyone impersonate a logged-in user. Failed logins are rate-limited
per email and per IP. Wrong password and unknown account return the identical
message, so nobody can harvest a list of who has an account. Changing or
disabling an account signs out every session it has.

A password file is the one part of a hobby project that can hurt people who
aren't you — people reuse passwords, and a weak store hands an attacker their
email and their broker. Forty tests cover exactly this, including the timing and
enumeration cases.

**What is gated.** The page, `/api/state`, *and* the chart image. Gating only the
page would leak every signal through the SVG.

**What isn't built.** No password reset by email — that needs a mail service, and
a half-finished reset flow is a way in, not a feature. Until then, resets are
manual from `/admin`. No payments, no plans: read the SEBI section first.

**The signup page states the measured result** — negative after costs, 8,837
signals, targets reached about a third of the time — above the email field, with
a checkbox to confirm it was read. Someone signing up cannot run your backtest.
That page is the only place they will ever learn what they're looking at, so
please leave it there.

**HTTPS is not optional once strangers use this.** Passwords over plain `http://`
on the open internet is negligent. That means a domain and Caddy, i.e. the small
cloud server in `DEPLOY.md`, not your Mac. The login page says so itself when it
detects it isn't on HTTPS.

### Reviewing a week of real trades

```bash
python3 review.py             # everything ever logged
python3 review.py --days 7    # just the last week
python3 review.py --index NIFTY
python3 review.py --cost 0.0  # see it before costs
```

Reads `~/trading-tool-logs/trades.csv` — the file the tool writes itself — and
reports hit rates, average R, and net-of-costs R, then breaks it down by index,
side, day, hour, ADX at entry, and reward:risk at entry.

Results are in **R**, not rupees, because R is the only unit that compares a
Nifty trade to a BankNifty one, or a tight stop to a wide one. A big win on a big
stop can be a worse trade than a small win on a small stop; rupees hide that.

If the file doesn't exist, it says so plainly and explains why — that file is
created when a *ticket is issued*, not when the tool starts and not when you save
a summary, so a missing file means no signal has fired.

**The breakdowns are hints, not conclusions.** Any slice with under 20 trades is
labelled as too few to mean anything, and the verdict says outright that under 30
trades the number is noise. Cut a small sample enough ways and one slice always
looks brilliant by luck. A pattern is only worth acting on if it holds up on
trades you found it *after*.
