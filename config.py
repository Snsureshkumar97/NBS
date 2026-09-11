"""
config.py
----------
Central place for instrument definitions and settings. Edit strike step /
lot sizes here if the exchange changes them.
"""

import os


def _read_dotenv_file(path: str) -> None:
    """Load one .env file into os.environ. Does NOT override a variable
    you've already set for real in your shell (a manual `export` always
    wins), and never overrides a value an earlier file already supplied."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # An EMPTY variable must not count as "already set". A stray
                # `export KITE_API_KEY=` in a shell profile — or any launcher
                # that passes blanks through — would otherwise silently shadow
                # the saved credentials, and the tool would insist it had no
                # API key while the key sat right there in the file.
                if key and not os.environ.get(key):
                    os.environ[key] = value
    except OSError:
        pass


def home_config_dir() -> str:
    """A folder that survives every future download of this tool.

    Storing .env beside the code was a mistake. You unzip a new copy into a
    new folder each time the tool is updated, which strands the old .env in
    the previous folder — so the tool asks for your API key again, and again,
    and it looks like it never remembers anything.

    Anything kept here is found by every copy of the tool, forever, no matter
    where you unzip it or which directory you launch from. Same reasoning as
    the trade log, and for the same reason: the things you'd hate to lose must
    not live next to code you replace.
    """
    # On a host like Render the home directory is wiped on every deploy, so
    # accounts and broker tokens have to live on a mounted disk instead.
    # TRADING_TOOL_HOME points at it. Unset — which is every desktop install —
    # behaves exactly as before.
    override = os.environ.get("TRADING_TOOL_HOME", "").strip()
    if override:
        try:
            os.makedirs(override, exist_ok=True)
            try:
                os.chmod(override, 0o700)      # it holds credentials
            except OSError:
                pass
            return override
        except OSError:
            pass
    try:
        d = os.path.join(os.path.expanduser("~"), ".trading-tool")
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)      # it holds credentials
        except OSError:
            pass
        return d
    except OSError:
        return os.path.dirname(os.path.abspath(__file__))


def dotenv_search_paths() -> list:
    """Every location we look for a .env file, in priority order.

    The home folder is checked LAST so an explicit .env you drop beside the
    code (or in the folder you launched from) still wins — useful for running
    two configurations — while the home copy is the one that quietly keeps
    working across upgrades.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.getcwd(), ".env"),          # where you launched from
        os.path.join(here, ".env"),                 # next to the code itself
        os.path.join(home_config_dir(), ".env"),    # survives every re-download
    ]
    seen, out = set(), []
    for p in candidates:
        rp = os.path.realpath(p)
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def _load_dotenv() -> None:
    """Tiny built-in .env loader (no extra dependency) — so the access
    token that kite_login_helper.py saves gets picked up automatically the
    next time you run gui.py/main.py, even in a brand-new terminal window
    where you haven't re-typed `export ...`."""
    for path in dotenv_search_paths():
        if os.path.exists(path):
            _read_dotenv_file(path)


_load_dotenv()

# ---------------------------------------------------------------------------
# Instrument definitions
# ---------------------------------------------------------------------------
INSTRUMENTS = {
    "NIFTY": {
        "yahoo_ticker": "^NSEI",
        "nse_symbol": "NIFTY",          # used for NSE option-chain-indices API
        "kite_exchange": "NSE",
        "kite_tradingsymbol": "NIFTY 50",
        "market": "nse_index",
        "strike_step": 50,
        "lot_size": 75,                 # verify current lot size on Zerodha before trading
        "has_free_option_chain": True,
    },
    "BANKNIFTY": {
        "yahoo_ticker": "^NSEBANK",
        "nse_symbol": "BANKNIFTY",
        "kite_exchange": "NSE",
        "kite_tradingsymbol": "NIFTY BANK",
        "market": "nse_index",
        "strike_step": 100,
        "lot_size": 30,                  # verify current lot size on Zerodha before trading
        "has_free_option_chain": True,
    },
    "SENSEX": {
        "yahoo_ticker": "^BSESN",
        "nse_symbol": None,              # no free public BSE option-chain API
        "kite_exchange": "BSE",
        "kite_tradingsymbol": "SENSEX",
        "market": "nse_index",
        "strike_step": 100,
        "lot_size": 20,                   # verify current lot size on Zerodha before trading
        "has_free_option_chain": False,
    },
    # ---- crypto -----------------------------------------------------------
    # Priced and charted off Deribit, which carries the perpetual for candles,
    # a published index for spot, and the only BTC/ETH option chain with enough
    # open interest to compute a PCR from. Binance would have been the obvious
    # source for candles and is geo-blocked from here, which is why this is one
    # venue rather than two.
    #
    # lot_size is 1 because a Deribit option contract IS one coin, so "lots" and
    # "contracts" are the same number - unlike an index, where a lot is 75.
    "BTC": {
        "yahoo_ticker": "BTC-USD",
        "nse_symbol": None,
        "kite_exchange": None,
        "kite_tradingsymbol": None,
        "market": "crypto",
        "provider": "deribit",
        "deribit_instrument": "BTC-PERPETUAL",
        "deribit_index": "btc_usd",
        "deribit_currency": "BTC",
        "quote_ccy": "USD",
        "strike_step": 1000,
        "lot_size": 1,
        "has_free_option_chain": False,
    },
}

# ---------------------------------------------------------------------------
# Signal engine settings
# ---------------------------------------------------------------------------
EMA_FAST = 20
EMA_SLOW = 50
RSI_LENGTH = 14
RSI_BULL_MIN = 50
RSI_BEAR_MAX = 50
RSI_OVERBOUGHT = 75
RSI_OVERSOLD = 25
ATR_LENGTH = 14

# MACD (momentum) — replaces Supertrend as a scoring vote. Supertrend and
# the EMA20/50 trend check are both fundamentally "is price above/below a
# moving reference," so they were nearly always agreeing with each other —
# counting that agreement as "2 independent votes" overstated confidence.
# MACD's histogram measures whether momentum is accelerating/decelerating,
# which is a genuinely different signal and can diverge from raw trend
# near turning points.
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ADX — trend-STRENGTH gate (not a vote). Below this, the market is
# considered too choppy/directionless to trade even if every scoring
# indicator happens to agree on a side — that "agreement" is much more
# likely to whipsaw when ADX is low. Common convention: <20 weak/no trend,
# 20-25 developing, 25+ trending. Kept at the more permissive end (20) so
# this filters out only clearly range-bound conditions, not every quiet day.
ADX_LENGTH = 14
ADX_TREND_THRESHOLD = 20

# ---------------------------------------------------------------------------
# "STRONG DOWNTREND" while the day is FLAT — the displacement check
# ---------------------------------------------------------------------------
# The market-trend headline used to be built from ADX (strength) and the EMA
# stack (direction) alone. Neither of those asks the obvious question: has price
# actually GONE anywhere?
#
# It can't, and that produced a genuinely silly reading, seen live: BankNifty
# labelled STRONG DOWNTREND with a day move of -7.55 points, or -0.01%. ADX is
# a lagging 14-bar average — a real move can finish, price can flatten, and ADX
# stays elevated for hours afterwards. The panel was even printing "momentum
# fading" beside it, which is the same fact said quietly.
#
# So the label now also measures how far price has NET travelled over the ADX
# window, in ATR units so it means the same thing on Nifty and BankNifty. Under
# TREND_MIN_DISPLACEMENT_ATR the market gets called what it is — going nowhere —
# no matter what ADX says. Between that and the STRONG figure it can be a trend,
# but not a strong one.
TREND_MIN_DISPLACEMENT_ATR = 1.0     # below this over 14 bars: not a trend at all
TREND_STRONG_DISPLACEMENT_ATR = 2.0  # below this: at most MODERATE

# 3-tier index-level targets, in units of ATR. SL is single-tier.
# Still used as the FALLBACK model (see SWING_LOOKBACK below) for when no
# clear recent swing point exists to anchor a structural stop to.
TARGET_ATR_MULTS = [0.75, 1.5, 2.5]   # T1, T2, T3
SL_ATR_MULT = 0.75

# Swing-based stop-loss: the PRIMARY model. Stop-loss is placed just beyond
# the lowest low / highest high of the last SWING_LOOKBACK candles (a
# simple, robust proxy for "the nearest real support/resistance"), with a
# small ATR buffer so price doesn't get stopped out by sitting exactly on
# the pivot. Falls back to the ATR-multiple model above only if that recent
# swing point is on the wrong side of the current spot (e.g. right after a
# sharp move, when there's no usable recent low/high yet).
# ---------------------------------------------------------------------------
# TREND-DAY RANGE EXPANSION
# ---------------------------------------------------------------------------
# "How much of a normal day's range is left" is one of the three limits on how
# far the market can still travel. Measured plainly — average prior daily range
# minus what today has used — it is a decent estimate on an average day and a
# systematic UNDER-estimate on a trending one, because a trend day is by
# definition a range-expansion day.
#
# That failure mode is the worst possible one: the tool goes blindest on
# exactly the days worth trading, vetoing unanimous signals for "no room" while
# the market runs. Seen live on BankNifty with ADX 30.9 and a -5 of 5 score.
#
# So the typical range is scaled up according to how strong the trend is.
# Read as: at ADX >= 35, expect a normal day's range to be exceeded by 2x.
# Run `python3 backtest.py --measure-expansion` to check these against real
# history for your indices rather than taking them on faith.
TREND_RANGE_EXPANSION = True
RANGE_EXPANSION_BY_ADX = [(35, 2.0), (25, 1.6), (20, 1.25), (0, 1.0)]


def range_expansion(adx):
    """Multiplier on the typical daily range, given current trend strength."""
    if not TREND_RANGE_EXPANSION or adx is None:
        return 1.0
    for threshold, mult in RANGE_EXPANSION_BY_ADX:
        if adx >= threshold:
            return mult
    return 1.0


# ---------------------------------------------------------------------------
# How WIDE the stop is allowed to get
# ---------------------------------------------------------------------------
# The stop is anchored to the last swing high/low, which is the right idea:
# it puts the stop where the chart actually turned. But in a fast move that
# swing can be a very long way off — 480 points on BankNifty, seen live — and
# there was no upper bound at all.
#
# That matters twice over. A stop 4x ATR away is not a stop anyone would
# actually take intraday, AND it is the denominator of the reward:risk test,
# so an over-wide stop can veto a perfectly good trade on its own.
#
# Beyond this multiple of ATR the swing is treated as unusable and the stop is
# placed here instead. MIN_RISK_ATR_MULT below is the same idea at the other
# end — too close is as bad as too far.
MAX_RISK_ATR_MULT = 2.0

SWING_LOOKBACK = 12
SL_BUFFER_ATR_MULT = 0.15

# If the nearest swing point happens to sit unrealistically close to entry,
# the resulting risk (R) is tiny and the R-multiple targets below stretch
# out to unrealistic distances. Floor the risk at this many ATRs so a
# too-close swing point falls back to the ATR-based stop instead (same
# fallback path used when there's no usable swing point at all).
MIN_RISK_ATR_MULT = 0.5

# Once the stop-loss is set (swing-based or ATR-fallback), targets are
# defined as multiples of the ACTUAL risk (entry-to-stop distance) rather
# than a fixed ATR distance independent of where the stop is. This keeps
# reward:risk coherent — T2 is always exactly a 2R target, whatever the
# stop distance worked out to be that trade.
RR_MULTS = [1.0, 2.0, 3.0]   # T1, T2, T3 as multiples of risk (R)

# ---------------------------------------------------------------------------
# How targets are chosen
# ---------------------------------------------------------------------------
#   "market" (default) — targets are built from how far the market can
#       REALISTICALLY travel before the session ends, measured three ways:
#       the option market's own expected move (the ATM straddle price), the
#       heaviest open-interest strikes acting as walls, and how much of a
#       normal day's range is still unused. The tightest limit wins, and the
#       three targets are placed inside that reachable distance. If there
#       isn't enough room to make the trade worthwhile, it says so instead
#       of printing targets the market has no chance of reaching.
#   "risk" — the older model: targets are fixed multiples of the stop
#       distance (RR_MULTS above). Simple and always produces numbers, but
#       those numbers are not connected to anything the market is doing.
TARGET_MODE = "market"

# Where the three targets sit inside the reachable distance. T3 sits at the
# realistic limit; T1 and T2 are partial exits on the way there.
REACH_FRACTIONS = [0.4, 0.7, 1.0]

# The room-to-run check needs option-chain data (the ATM straddle for the
# expected move, and the OI walls). When the chain is missing — NSE hiccups,
# a Kite session that has not warmed up, an index with no chain published yet
# — that check simply cannot run.
#
# The old behaviour was to fall through and issue the ticket anyway, with
# targets built from a multiple of the stop instead. That is exactly the
# trade nobody checked: of six stop-outs in the reviewed log, five came from
# tickets issued while the chain was unavailable. A target chosen without
# knowing whether the market can reach it is a guess wearing a number.
#
# True  = no chain, no ticket. Say why, and wait for the chain to come back.
# False = old behaviour (issue anyway on risk-multiple targets).
REQUIRE_REACHABILITY = True

# The rupee figures on the ticket are quoted for this many lots. Purely a
# DISPLAY setting: this tool never places an order, it only tells you what a
# move is worth, so changing it changes the arithmetic on screen and nothing
# about the signal, the targets or the stop.
#
# Whatever is selected when a ticket is issued is frozen INTO that ticket, so
# switching to 5 lots at lunchtime cannot retroactively rewrite what the
# morning's trade made.
# Which palette the window starts in: "dark" or "light".
# Both were validated the same way — see theme.py and validate_palette.py.
# Switch it live with the half-moon icon at the bottom of the left rail.
THEME = "dark"

DEFAULT_LOTS = 1
MAX_LOTS = 5



# ---------------------------------------------------------------------------
# DAILY LIMITS — the two rules that stop a good day being handed back
# ---------------------------------------------------------------------------
#
# Stop taking NEW trades once this many have run all the way to T3 today.
# Signals keep being shown and explained; the tool just stops issuing tickets.
#
# The reasoning: the edge here is thin (measured at roughly break-even after
# costs), so results are dominated by variance. Two full winners is a good day
# pulled out of a thin edge, and continuing to trade after it has no positive
# expectancy to draw on — it just gives the day's profit more chances to leave.
# Anything already OPEN is untouched; this only blocks new entries.
#
# ---- the master switch -----------------------------------------------------
# False = no daily caps at all. Every qualifying signal becomes a ticket,
#         however many that turns out to be. The quality gates below (chain
#         data, room-to-run, the opening window) still apply — those decide
#         whether a trade is WORTH taking, which is a different question from
#         how many you have already had.
# True  = the two caps underneath are enforced.
#
# Flip it on screen with the "Daily limits" tick box; this is only the value
# it starts with.
DAILY_LIMITS_ON = False

# 0 disables the limit.
DAILY_TARGET_WINS = 2

# A hard ceiling on tickets issued per day, whatever the outcomes. This is the
# over-trading brake proper: the win limit only engages once you are winning,
# and a run of small losses can otherwise churn all session.
#
# 0 disables the limit.
MAX_TRADES_PER_DAY = 4

# ---------------------------------------------------------------------------
# INTRADAY VOLUME PROFILE — what VWAP is weighted by on an index
# ---------------------------------------------------------------------------
# An index has no volume of its own, so the VWAP vote used to be computed on
# nothing (see indicators.vwap). This is the average share of a session's
# volume each 15-minute slot carries in the Nifty near-month future, 52 full
# sessions from 1 Jul to 10 Sep 2026, via Zerodha's historical API; mean = 1.
# Bank Nifty's and Sensex's futures show the same U-shape (correlation 0.98
# and 0.91), so one profile serves all three. Live, the near-month future's
# actual volume is attached to the index candles and this only fills the bar
# that is still forming. Re-measure it once a year or so - the shape is
# structural (heavy open, quiet lunch, closing pick-up) and moves slowly.
INTRADAY_VOLUME_PROFILE = {
    "09:15": 3.68, "09:30": 1.74, "09:45": 1.28, "10:00": 1.11, "10:15": 1.10,
    "10:30": 0.94, "10:45": 0.74, "11:00": 0.86, "11:15": 0.68, "11:30": 0.67,
    "11:45": 0.64, "12:00": 0.79, "12:15": 0.68, "12:30": 0.83, "12:45": 0.75,
    "13:00": 0.65, "13:15": 0.57, "13:30": 0.69, "13:45": 0.69, "14:00": 0.65,
    "14:15": 0.81, "14:30": 0.75, "14:45": 0.88, "15:00": 1.21, "15:15": 1.65,
    "15:30": 0.97,
}

# ---------------------------------------------------------------------------
# ACCOUNT RISK — sizing from the account, not from a lots dropdown
# ---------------------------------------------------------------------------
# The first thing a professional asks of any setup is "what does one trade
# risk, as a share of the account?" The answer used to be nowhere on screen:
# lots were picked from a dropdown, so the same signal risked 0.5% of one
# account and 6% of another without either person being told.
#
# Enter your trading capital on the signal card and the tool shows, for every
# signal and every open ticket, the money between entry and stop, what share
# of the account that is, and how many lots fit inside RISK_PER_TRADE_PCT.
# Nothing is enforced on the lots you pick - this tool never places an order -
# but the number is in front of you before you do.
#
# The daily loss limit IS enforced on tickets: once today's closed trades have
# lost DAILY_LOSS_LIMIT_R full-risk trades' worth of capital (3 x 1% = 3% at
# the default), no new tickets that day. Only active
# once a capital figure is entered, since a percentage of nothing is nothing.
#
# Why it is not tied to DAILY_LIMITS_ON: those caps shape how much you trade;
# this one bounds how much a bad day can cost, and has no business being off
# by default once you have told the tool how big the account is.
#
# pro_study.py tested a "stop after two losing trades" rule on real expiries:
# it lifted the held-out year by about ₹62k per lot and cut its worst drawdown
# by ₹27k, but cost ₹95k in-sample - so it is kept as a limit on the downside,
# not sold as an edge. Expressed in R so it scales with the risk you choose.
RISK_PER_TRADE_PCT = 1.0
RISK_PCT_CHOICES = (0.5, 1.0, 1.5, 2.0)
DAILY_LOSS_LIMIT_R = 3

# No NEW tickets before this time, whatever the signal says. 09:15-09:20 is
# the opening auction settling: spreads are wide, the first 15m candle barely
# exists, and every indicator is reading a bar with almost nothing in it.
# Signals are still shown during this window — only entry is held.
NO_NEW_TRADES_BEFORE = (9, 20)

# ---------------------------------------------------------------------------
# THE TRADING SESSION
# ---------------------------------------------------------------------------
# Defined here, once, because it used to be defined twice — main.py and
# signal_engine.py each carried their own copy, and when NSE moved the close
# only one of them would have been found and fixed. The other decides how much
# trading time is left today, which feeds reachability and therefore the
# reward-to-risk gate, so a stale copy there is not cosmetic.
#
# 15:40, not 15:30: NSE extended EQUITY DERIVATIVES to 15:40 on 3 August 2026,
# so derivatives traders can react to the Closing Auction Session (15:15-15:35)
# that now sets the cash market's closing price. The cash market still ends at
# 15:30. This tool trades index options, so 15:40 is the number that applies.
MARKET_OPEN_TIME = (9, 15)
MARKET_CLOSE_TIME = (15, 40)

# When the index stops being a live price.
#
# NSE's Closing Auction Session, in force since 3 August 2026, pulls every
# F&O-eligible stock out of continuous trading at 15:15. All fifty Nifty
# constituents are in the auction from that moment, so no trade prints and
# the index simply stops: it holds one value to the paise until the auction
# matches and the closing prices publish around 15:35.
#
# The options carry on trading until 15:40, and this is the trap. For those
# twenty-five minutes the premium moves while the index behind it is a
# photograph, so every reading taken from the index - RSI, MACD, ADX, VWAP,
# trend, the market map - is frozen too, and nothing about the screen says so.
# A signal computed then is built on a dead input and priced on a live one.
CAS_START_TIME = (15, 15)


# ---------------------------------------------------------------------------
# Market profiles
#
# Everything above describes one market: an NSE index, 09:15 to 15:40, shut at
# weekends and on a published holiday list, with a closing auction near the end.
# All of that was global, which was fine while every instrument was an NSE
# index and wrong the moment one is not. A market is a property of the
# instrument now, and the session rules are looked up per instrument rather
# than assumed.
# ---------------------------------------------------------------------------
MARKETS = {
    "nse_index": {
        "label": "NSE / BSE index",
        "market_provider": "kite",
        "always_open": False,
        "weekends": False,
        "holidays": True,          # consult the NSE calendar in main.py
        "open": MARKET_OPEN_TIME,
        "close": MARKET_CLOSE_TIME,
        "cas": CAS_START_TIME,
    },
    "crypto": {
        # No open, no close, no weekend, no holiday and no closing auction.
        # Every session rule below simply does not apply, which is the whole
        # reason a market had to stop being a global.
        "label": "crypto, 24/7",
        "market_provider": "deribit",
        "always_open": True,
        "weekends": True,
        "holidays": False,
        "open": None,
        "close": None,
        "cas": None,
    },
}

DEFAULT_MARKET = "nse_index"


# Crypto is off unless asked for. It is a different venue, a different
# currency and a different session, and an operator who wants three Indian
# indices should not silently acquire two more instruments and a dependency on
# a foreign exchange's uptime because the code gained the ability.
ENABLE_CRYPTO = os.environ.get("ENABLE_CRYPTO", "").lower() in ("1", "true", "yes", "on")


def instruments_in(market):
    """The instrument keys trading in one market, in declaration order."""
    return [k for k, v in INSTRUMENTS.items()
            if v.get("market", DEFAULT_MARKET) == market
            and (market != "crypto" or ENABLE_CRYPTO)]


def active_instruments():
    """Every instrument the server should actually run, grouped market first.

    Kept separate rather than merged: the two markets quote in different
    currencies, keep different hours and come from different venues, so a
    single flat list is the wrong shape for almost every caller.
    """
    out = []
    for m in MARKETS:
        out.extend(instruments_in(m))
    return out


def market_for(index_key=None):
    """The market profile an instrument trades in.

    Defaults to the NSE index profile, so every existing caller that does not
    name an instrument keeps the behaviour it had.
    """
    if not index_key:
        return MARKETS[DEFAULT_MARKET]
    meta = INSTRUMENTS.get(index_key) or {}
    return MARKETS.get(meta.get("market", DEFAULT_MARKET), MARKETS[DEFAULT_MARKET])


def in_closing_auction(now, index_key=None):
    """True when the index has stopped updating but options still trade.

    `now` must be IST. Says nothing about whether the market is open - it is,
    and an open position still needs managing. It says the index is no longer
    a live price, which is a different question with a different answer.
    """
    m = market_for(index_key)
    if m["always_open"] or not m["cas"]:
        return False
    if now.weekday() >= 5:
        return False
    cas = (m["cas"][0], m["cas"][1])
    close = (m["close"][0], m["close"][1])
    return cas <= (now.hour, now.minute) <= close


def session_hours(index_key=None):
    """Length of the trading day in hours, derived rather than written down.

    A 24/7 market has no trading day, so it answers 24 - which is what the
    expected-move scaling needs: "how much of the move is left before expiry"
    has to count every hour, not six and a half of them.
    """
    m = market_for(index_key)
    if m["always_open"]:
        return 24.0
    o = m["open"][0] * 60 + m["open"][1]
    c = m["close"][0] * 60 + m["close"][1]
    return (c - o) / 60.0

# Bars stamped before the open belong to the PRE-OPEN auction (09:00-09:15),
# where indicative prices swing wildly on tiny volume. Feeding them to an EMA
# or an ATR produces a reading of an auction, not of a market. Dropped before
# any indicator sees them.
DROP_PREOPEN_CANDLES = True

# If the realistic reach is smaller than this multiple of the risk, the
# trade is rejected outright — you'd be risking materially more than the
# market is likely to hand back.
#
# Deliberately well BELOW 1.0. The reach estimate is conservative by
# construction: it only counts the move left in TODAY's session, and caps
# at OI walls that price trades through regularly. Measured reward:risk
# therefore understates the true figure, so vetoing at 1.0 rejected large
# numbers of perfectly ordinary trades. The reward:risk is always displayed
# either way, so a thin-but-tradeable setup is shown to you with its real
# numbers rather than silently withheld.
MIN_REACH_TO_RISK = 0.6

# ---------------------------------------------------------------------------
# How picky the signal is
# ---------------------------------------------------------------------------
# Three separate filters have to pass before a trade fires: the indicator
# score, the ADX trend-strength gate, and the reachability check. Stacked
# together they can go quiet for days at a time — which is correct behaviour
# if the market genuinely has nothing to offer, but frustrating if you want
# to see the tool work.
#
# The vote requirement is stated directly as counts — "at least N agree, at
# most M disagree" — rather than as a score threshold.
#
# A score threshold was subtly broken. Score = agreeing - disagreeing, so ONE
# dissenter moves the score by TWO (it removes a +1 and adds a -1). A margin
# of 1 was meant to tolerate a single dissenter but actually demanded
# unanimity, because the threshold fell on a score that parity made
# unreachable: with 5 indicators voting, the only possible scores are
# -5,-3,-1,1,3,5, and the requirement of 4 could never be met by anything
# except a clean sweep. With only 2 indicators voting it was impossible to
# fire at all. Counting agreement directly removes the whole class of bug.
#
#   "strict"   — >=3 agree, <=1 disagrees, ADX >= 20.
#   "balanced" — >=3 agree, <=2 disagree, ADX >= 18.
#   "loose"    — >=2 agree, <=2 disagree, ADX >= 15. Use this to watch the
#                machinery work, NOT to trade real money.
#
# Honest note: relaxing this does not find more good trades, it lowers the
# bar for what counts as one. Expect the win rate to fall as you loosen it.
SIGNAL_STRICTNESS = "strict"

# ---------------------------------------------------------------------------
# Live feed vs analysis cadence
# ---------------------------------------------------------------------------
# In kite mode prices arrive over a WebSocket — pushed by the exchange as
# they happen, exactly like the Kite app — so the spot price, your open
# trade's premium, P&L and target hits update continuously with no polling.
#
# Indicators are a different matter: EMA/MACD/RSI/ADX/VWAP are computed from
# 15-MINUTE candles, and the option chain (PCR, OI walls) comes from a REST
# call. Those cannot change faster than their underlying data no matter how
# often you ask, so they're recomputed on this slower timer instead. Asking
# more often just burns API quota for identical answers.
ANALYSIS_INTERVAL_SEC = 30       # kite mode (prices stream separately)
ANALYSIS_INTERVAL_FREE_SEC = 60  # free mode has no stream — this is the whole refresh

# How often the window reads the streamed prices out of memory. This is a
# local dict lookup, not a network call, so it costs essentially nothing.
LIVE_UI_MS = 250

# How often the FULL read is recomputed on the live candle — indicators,
# trend, reachability, targets. Measured at ~15 ms a pass, so once a second
# is a rounding error of CPU.
LIVE_ANALYSIS_MS = 1000

# Intra-candle readings genuinely flip back and forth. A direction has to
# survive this many consecutive live passes before a ticket is locked in, so
# a trade's levels are never frozen off a reading that vanishes seconds
# later. At LIVE_ANALYSIS_MS=1000 this is simply "hold for N seconds".
SIGNAL_CONFIRM_TICKS = 4

# ---------------------------------------------------------------------------
# CHURN BRAKES — the three reasons a day ran to 77 tickets
# ---------------------------------------------------------------------------
#
# 1. CONFIRMATION MEASURED IN SECONDS, NOT TICKS.
#    SIGNAL_CONFIRM_TICKS above counts live evaluations, and those arrive every
#    LIVE_UI_MS (250ms). Four of them is ONE SECOND. That is not a confirmation
#    window, it is a formality — the count was written for a slower cadence and
#    never revisited when the tick feed arrived. A direction now has to survive
#    this many seconds of live evaluation before it can become a ticket.
SIGNAL_CONFIRM_SECONDS = 120

# 2. A FLOOR UNDER THE GAP BETWEEN TICKETS, IN ANY DIRECTION.
#    REENTRY_COOLDOWN_MIN only ever governed re-entering the SAME direction. A
#    flip to the opposite side was free, so an index oscillating CE/PE could
#    issue tickets all session and nothing stopped it. This applies to every
#    new ticket, whichever way it points.
#
#    0 = OFF. Set here by request: a signal is never held back by the clock.
#    Restore it by putting 20 (or any number of minutes) back.
#
#    What this means in practice: the tool ran at 20 for the sessions after the
#    77-ticket day. With it at 0, the remaining brakes on ticket COUNT are the
#    120-second confirmation, one position at a time per index, and the daily
#    limits switch — which is currently off. Nothing else caps the day.
#
#    The measured expectancy on the logged trades is -0.090R after costs, so
#    more tickets is a faster loss rather than a bigger opportunity unless the
#    churn fixes have actually changed that. Worth re-running churn_report.py
#    after a couple of weeks to see which way it went.
MIN_MINUTES_BETWEEN_TICKETS = 0

# A separate, much smaller floor that ONLY governs auto re-arm, in seconds.
#
# This is not a churn brake and is not meant to be tuned. Auto re-arm skips the
# confirmation window by design — the direction was already confirmed when the
# ticket that just closed was issued — so with MIN_MINUTES_BETWEEN_TICKETS at 0
# and the daily limits switch off, nothing at all stands between a close and
# the next open. A ticket whose stop is already breached then closes on the
# next evaluation and re-arms immediately, four times a second, until the
# option chain refreshes: thirteen tickets in twelve seconds, measured.
#
# Sixty seconds is enough for the chain to move on and cannot hide a real
# setup, because a genuine re-entry a minute later is still taken.
REARM_MIN_SECONDS = 60

# 3. A FLIP NO LONGER KILLS A RUNNING TRADE.
#    The old behaviour closed the open position at market the moment the signal
#    changed its mind, and opened the opposite one. That is two trades and two
#    lots of costs for one change of opinion, and it happened dozens of times a
#    day. The levels are frozen at entry precisely so the trade can be left to
#    reach its own target or its own stop.
#
#    True restores the old behaviour.
CLOSE_ON_SIGNAL_FLIP = False

_STRICTNESS = {
    "strict":   {"min_agree": 3, "max_dissent": 1, "adx": 20, "min_rr": 0.6},
    "balanced": {"min_agree": 3, "max_dissent": 2, "adx": 18, "min_rr": 0.45},
    "loose":    {"min_agree": 2, "max_dissent": 2, "adx": 15, "min_rr": 0.3},
}


def strictness() -> dict:
    return _STRICTNESS.get(SIGNAL_STRICTNESS, _STRICTNESS["strict"])

# Approx delta used to translate an index-point move into an expected ATM
# option premium move. This is a rough rule of thumb, NOT a real Greeks
# calculation, and is only used as a FALLBACK when no live option LTP could
# be fetched (e.g. Sensex in free mode). ATM options have delta ~0.5.
APPROX_ATM_DELTA = 0.5

# 3-tier PREMIUM targets, as a % gain from the live entry LTP (when
# available). This mirrors how many option buyers actually manage a
# position: scale out partial quantity at each tier rather than betting the
# whole position on a single exit.
PREMIUM_TARGET_PCTS = [20, 40, 75]   # T1, T2, T3 (% gain on premium)
PREMIUM_SL_PCT = 25                   # % loss on premium

# WHICH target actually closes the trade: "T1", "T2" or "T3".
#
# Worth being clear about what the three tiers do and do not do. The comment
# above says "scale out partial quantity at each tier" — that is how a person
# would trade them, but the TOOL has never done it. It holds the whole position
# and closes on one exit. T1 and T2 are markers: they tick, they timestamp, they
# tell you the move is working. They book nothing.
#
# So this is already a single-target system, and this setting is the single
# target. Moving it nearer raises the win rate and lowers what each win pays;
# moving it further does the reverse. On the 162 logged trades, counting only
# the ones the market actually decided:
#
#     exit at   win rate   avg reward:risk   break-even needs   R per trade
#     T1 +20%     75.0%        0.54 : 1           64.8%            +0.12R
#     T2 +40%     61.2%        0.95 : 1           51.2%            +0.09R
#     T3 +75%     48.9%        1.36 : 1           42.4%            -0.05R
#
# Read that with care: 98-117 of the 162 were closed early by a signal flip
# before the market answered, and the ones that DID resolve are the ones that
# moved decisively — which flatters every row. Treat it as a direction to lean,
# not a measurement.
EXIT_AT_TARGET = "T3"

# ---------------------------------------------------------------------------
# Kite Connect credentials (only needed if --mode kite)
# Prefer setting these as environment variables rather than editing this file:
#   export KITE_API_KEY=xxxx
#   export KITE_API_SECRET=xxxx
#   export KITE_ACCESS_TOKEN=xxxx   (generated fresh each trading day - see
#                                     kite_login_helper.py)
# ---------------------------------------------------------------------------
KITE_API_KEY = os.environ.get("KITE_API_KEY", "")
KITE_API_SECRET = os.environ.get("KITE_API_SECRET", "")
KITE_ACCESS_TOKEN = os.environ.get("KITE_ACCESS_TOKEN", "")

# The local address Zerodha redirects your browser back to after login, so
# kite_auth.py can catch the request_token instead of you copying it out of
# the address bar. This must MATCH the "Redirect URL" set on your app at
# https://developers.kite.trade/apps — set it there to exactly:
#
#     http://127.0.0.1:5055/
#
# Why 5055 and not the more obvious 5000: macOS gives 5000 and 5001 to AirPlay
# Receiver by default, so a tool defaulting to 5000 fails on a stock Mac with a
# confusing "port in use" error before it has done anything. 5055 is not
# claimed by anything common.
#
# Change it here only if 5055 is taken on your machine too — and if you do,
# change the Redirect URL in the Kite developer console to match. The two just
# have to agree.
KITE_REDIRECT_PORT = int(os.environ.get("KITE_REDIRECT_PORT", "5055"))

# ---------------------------------------------------------------------------
# WEB SERVER — operator login
# ---------------------------------------------------------------------------
# When the tool runs as a website on a machine that ISN'T the one you're sitting
# at, the desktop login flow can't work: it redirects to 127.0.0.1, which from
# your browser means YOUR computer, not the server. So the server exposes its
# own callback route instead.
#
# That route must be protected. The access token it produces belongs to whoever
# completed the login — leave it open and a stranger could point their own
# Zerodha account at your server and have it run on their credentials.
#
# Set this to a long random string in .env before exposing the site:
#     WEB_ADMIN_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
# Empty means the admin routes are DISABLED entirely, which is the safe default
# for anyone who never reads this far.
WEB_ADMIN_KEY = os.environ.get("WEB_ADMIN_KEY", "")

# The public address Zerodha should redirect back to after an operator login,
# e.g. https://yourdomain.com  or  http://203.0.113.9:8080
# Whatever you put here + /kite/callback must be EXACTLY the Redirect URL set on
# your app at https://developers.kite.trade/apps.
WEB_PUBLIC_URL = os.environ.get("WEB_PUBLIC_URL", "").rstrip("/")


def web_callback_url():
    return f"{WEB_PUBLIC_URL}/kite/callback" if WEB_PUBLIC_URL else ""


def apply_credentials(api_key=None, api_secret=None, access_token=None):
    """Update the live credentials in place, after a fresh login.

    These are read into module-level names at import, so a token written to
    .env mid-session would otherwise not be seen until the next restart —
    which would make the whole point of a login button (log in, then press
    Start, in one sitting) not work.
    """
    global KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN
    for name, value in (("KITE_API_KEY", api_key),
                        ("KITE_API_SECRET", api_secret),
                        ("KITE_ACCESS_TOKEN", access_token)):
        if value:
            os.environ[name] = value
            globals()[name] = value
    return KITE_ACCESS_TOKEN


# ---------------------------------------------------------------------------
# WEBSITE ACCOUNTS
# ---------------------------------------------------------------------------
# Require a login before anyone can see the signals.
WEB_REQUIRE_LOGIN = os.environ.get("WEB_REQUIRE_LOGIN", "0") not in ("0", "", "false", "False")

# Let strangers create their own accounts. OFF by default, and that default is
# deliberate: the moment people you don't know start acting on these signals,
# two things become true that weren't before. Their money is at risk on a rule
# set measured at roughly break-even before costs and negative after them; and
# publishing buy/sell calls to the public moves you toward SEBI's Research
# Analyst rules. Neither is a reason you can't do it — both are reasons it
# should be a decision rather than a default.
WEB_ALLOW_SIGNUP = os.environ.get("WEB_ALLOW_SIGNUP", "0") not in ("0", "", "false", "False")


# ---------------------------------------------------------------------------
# RE-ENTRY IN THE SAME DIRECTION
# ---------------------------------------------------------------------------
# By default a ticket is only issued when the direction CHANGES. That exists for
# a good reason: the read recalculates about once a second, so without it a
# market that stayed bearish all afternoon would produce a new identical ticket
# every second.
#
# But it has a real cost. If the tool ticketed a PE at 10am and the downtrend
# runs all day, it will not offer you another PE — however good the next setup
# looks — until the read flips bullish and back again. Perfectly tradeable
# continuation entries are simply never shown.
#
# Turning this on lets a second ticket be issued in the SAME direction, provided
# all of these hold:
#     * no ticket is currently open (one position at a time)
#     * at least REENTRY_COOLDOWN_MIN minutes since the last one
#     * the setup passes every normal check — ADX gate, agreement, reward:risk
#
# The cooldown is the part that matters. Without it this is just "issue a ticket
# every second", which is how you turn a thin edge into a brokerage bill.
#
# Worth being plain about: this INCREASES the number of trades, and the measured
# expectancy of this rule set is already negative after costs. More trades at a
# negative expectancy is a faster loss, not a bigger opportunity. Turn it on to
# see continuation entries you're currently blind to — not because more signals
# is better.
ALLOW_SAME_DIRECTION_REENTRY = True
REENTRY_COOLDOWN_MIN = 20

# A second ticket in a direction already taken today must have real room left.
# The targets are placed INSIDE the room - T1/T2/T3 at 40/70/100% of it - so
# "can it hit the targets" comes down to whether the room is big enough to be
# worth the stop. A first entry needs room of 0.6x the risk; a re-entry needs
# the room to be at least as far as the stop, because the move has already had
# one leg and a second ticket on a tired trend is how a good morning becomes a
# bad one. 1.0 means T3 sits at least as far away as the stop does.
REENTRY_MIN_RR = 1.0

# Opening-range confirmation, Indian indices only. A CE is taken only once price
# is above the high of 09:15-09:45, a PE only below its low; nothing before the
# range is complete. From regime_study.py, three years, priced as ATM options
# after Zerodha's charges: it was the only filter tested that improved the
# in-sample result under every cost and expiry assumption and did not collapse
# on the held-out year (pooled, default assumptions: -164k -> +253k in-sample,
# +20k -> +66k out-of-sample, per lot). It takes about 45% fewer trades; in the
# most favourable assumptions the unfiltered rules make more, in the
# unfavourable ones they lose far more. Robustness was preferred to upside.
REGIME_OR_BREAK = True

# ---------------------------------------------------------------------------
# REWARD TO THE FINAL TARGET — at least what the stop risks
# ---------------------------------------------------------------------------
# A ticket closes on T3 or the stop, so T3 is the reward and the stop is the
# risk. On 11 Sep 2026 all three index tickets had T3 CLOSER than the stop -
# Nifty's 23250 PE risked 44 of premium to make 29 - because the targets are
# capped by how far the market can plausibly run while the stop sits behind
# structure. A trade like that has to win well over half the time just to
# stand still, and it is the first thing a risk manager strikes off.
#
# pro_study.py, pre-declared at 1.0 (not swept), real expiries, after costs,
# per lot, on top of the opening-range rule:
#                   profit factor     worst drawdown      total
#   in-sample       1.14 -> 1.15      115k -> 106k        426k -> 402k
#   held-out year   1.05 -> 1.10      179k -> 142k         98k -> 157k
# Better per trade and shallower in both periods; the in-sample total is lower
# only because 11% fewer trades are taken. 0 switches it off.
MIN_REWARD_RISK_T3 = 1.0

# ---------------------------------------------------------------------------
# WATCH-ONLY INDICES — shown, explained, charted, never ticketed
# ---------------------------------------------------------------------------
# Bank Nifty lost its weekly expiry on 13 Nov 2024, and its results went with
# it. Same rules, same costs, per lot (pro_study.py):
#   weekly-expiry era            587 trades   +91k
#   monthly-only, in-sample      404 trades   +24k
#   monthly-only, held-out year  553 trades   -62k
# The only index negative on the held-out year in every configuration tested.
# The signal stays on screen so you can watch it, and so it is obvious the day
# it starts working again; it just no longer issues tickets. Remove the name
# from this tuple to trade it again.
WATCH_ONLY_INDICES = ("BANKNIFTY",)

# Is MIN_MINUTES_BETWEEN_TICKETS counted per index, or across all three?
# Moot while that setting is 0 — kept because it matters the moment it is not.
#
# False (the default it shipped with) means one gap shared by everything: a
# NIFTY ticket at 09:31 holds SENSEX back until 09:51, even though SENSEX is a
# different instrument with its own trend. That is a real brake on total
# trades, and it is also why a screen can show a 100%-confidence signal sitting
# in PREVIEW with nothing obviously wrong.
#
# True counts the gap separately for each index, so each one is only held back
# by its own last ticket. Expect MORE tickets — up to three times as many in
# the worst case. Given the measured expectancy is negative after costs, more
# tickets is a faster loss, not a bigger opportunity. Change it because you
# have decided the indices should be independent, not to see more signals.
TICKET_GAP_PER_INDEX = False
