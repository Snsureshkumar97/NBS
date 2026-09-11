#!/usr/bin/env python3
"""
main.py — Nifty / BankNifty / Sensex CE-PE signal tool
=========================================================

  python main.py --index NIFTY --mode free
  python main.py --index BANKNIFTY --mode kite --live --refresh 60
  python main.py --index SENSEX --mode free --interval 1d

Run `python main.py --help` for all options.

*** THIS TOOL DOES NOT PLACE ANY ORDERS. It only prints/saves a suggestion. ***
*** NOT SEBI-registered investment advice. See README.md for full disclaimer. ***
"""

import argparse
import os
import sys
import time
import datetime as dt

import config
from data_providers import FreeDataProvider, KiteDataProvider
from signal_engine import (
    compute_technical_signal,
    compute_option_chain_signal,
    compute_market_trend,
    compute_reachability,
    build_recommendation,
)

# The session lives in config.py — see the note there on why 15:40 and not
# 15:30. Re-exported under the old names so nothing that imports them breaks.
MARKET_OPEN_TIME = config.MARKET_OPEN_TIME
MARKET_CLOSE_TIME = config.MARKET_CLOSE_TIME
SESSION_REFRESH_EVERY = 20    # re-warm the NSE session every N live-mode cycles
MAX_FETCH_RETRIES = 2         # retries within a single cycle before giving up on that cycle

# ---------------------------------------------------------------------------
# IMPORTANT: NSE/BSE trade on India Standard Time (IST), no matter where you
# are physically running this tool from. If you're in the US, UK, etc., your
# computer's own clock/timezone is NOT IST — so we must always explicitly
# convert to IST rather than trusting the machine's local time. now_ist()
# below is used everywhere instead of dt.datetime.now().
# ---------------------------------------------------------------------------
try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:
    # Some minimal Windows Python installs lack the IANA timezone database
    # (the 'tzdata' package) that zoneinfo needs. Fall back to a manual
    # fixed UTC+5:30 offset, which is always correct for IST (India has no
    # daylight saving time, so this fallback never drifts).
    _IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def now_ist() -> dt.datetime:
    """Current date/time in India Standard Time — correct no matter what
    timezone your own computer is set to."""
    return dt.datetime.now(_IST)


# ---------------------------------------------------------------------------
# NSE/BSE TRADING HOLIDAYS
# Sourced from NSE's published 2026 equity/derivatives holiday calendar
# (cross-checked against two independent listings, both agreeing on these
# 16 dates). This does NOT auto-update — someone needs to add next year's
# dates here every December, or this silently goes stale and the tool will
# think a holiday is a normal trading day again. Muhurat Trading (a special
# one-hour Diwali session, Nov 08 2026) is deliberately NOT listed as a
# holiday since the exchange is technically open that evening.
# ---------------------------------------------------------------------------
NSE_HOLIDAYS_BY_YEAR = {
    2026: {
        dt.date(2026, 1, 15),   # Maharashtra Municipal Corporation Election
        dt.date(2026, 1, 26),   # Republic Day
        dt.date(2026, 3, 3),    # Holi
        dt.date(2026, 3, 26),   # Ram Navami
        dt.date(2026, 3, 31),   # Mahavir Jayanti
        dt.date(2026, 4, 3),    # Good Friday
        dt.date(2026, 4, 14),   # Dr. Ambedkar Jayanti
        dt.date(2026, 5, 1),    # Maharashtra Day
        dt.date(2026, 5, 28),   # Bakri Id (Eid ul-Adha)
        dt.date(2026, 6, 26),   # Muharram
        dt.date(2026, 9, 14),   # Ganesh Chaturthi
        dt.date(2026, 10, 2),   # Gandhi Jayanti
        dt.date(2026, 10, 20),  # Dussehra
        dt.date(2026, 11, 10),  # Diwali Balipratipada
        dt.date(2026, 11, 24),  # Guru Nanak Jayanti
        dt.date(2026, 12, 25),  # Christmas
    },
}


def is_nse_holiday(d: dt.date) -> bool:
    """True if `d` is a known NSE/BSE trading holiday. Returns False (not
    "unknown") for any year not in NSE_HOLIDAYS_BY_YEAR, so an un-updated
    calendar degrades to the old weekend-only behavior rather than
    guessing — check the comment above NSE_HOLIDAYS_BY_YEAR if this list
    needs a new year added."""
    return d in NSE_HOLIDAYS_BY_YEAR.get(d.year, set())


DISCLAIMER = """
------------------------------------------------------------------------------
DISCLAIMER: This is a rule-based technical/OI heuristic tool for educational
and informational purposes only. It is NOT SEBI-registered investment advice
and CANNOT guarantee accuracy. Options trading carries a high risk of rapid
capital loss (theta decay, volatility, gap moves). Before acting on any
signal:
  1. Cross-check on your own TradingView chart.
  2. Paper-trade / backtest this logic on your timeframe first.
  3. Never risk more than 1-2% of capital on a single trade.
  4. Premium target/SL figures here are ROUGH ESTIMATES (assume ATM delta
     ~0.5) — actual option premium moves depend on real Delta/Theta/Vega/IV
     and will differ, especially close to expiry.
------------------------------------------------------------------------------
"""


def is_market_open(now: dt.datetime, index_key: str = None) -> bool:
    """`now` must be IST (use now_ist()) — comparing any other timezone's
    wall-clock time against the session here would give wrong results.

    `index_key` names the instrument whose market is being asked about. Omitted,
    it answers for an NSE index, which is what every caller written before there
    was more than one kind of market means by the question.
    """
    m = config.market_for(index_key)
    if m["always_open"]:
        # Nothing to check. A 24/7 market has no weekend, no holiday list and
        # no open or close to compare against, and running the checks below
        # would answer "shut" every Saturday for a market that is not.
        return True
    if not m["weekends"] and now.weekday() >= 5:  # Sat/Sun
        return False
    if m["holidays"] and is_nse_holiday(now.date()):
        return False
    open_t = now.replace(hour=m["open"][0], minute=m["open"][1], second=0, microsecond=0)
    close_t = now.replace(hour=m["close"][0], minute=m["close"][1], second=0, microsecond=0)
    return open_t <= now <= close_t
    # NOTE: weekends AND the known NSE holidays in NSE_HOLIDAYS_BY_YEAR are
    # both handled. If that dict hasn't been updated for the current year,
    # this silently falls back to weekend-only detection — see the comment
    # above NSE_HOLIDAYS_BY_YEAR.


def next_market_open_str(now: dt.datetime) -> str:
    candidate = now.replace(hour=MARKET_OPEN_TIME[0], minute=MARKET_OPEN_TIME[1], second=0, microsecond=0)
    if now >= candidate:
        candidate += dt.timedelta(days=1)
    while candidate.weekday() >= 5 or is_nse_holiday(candidate.date()):
        candidate += dt.timedelta(days=1)
    return candidate.strftime("%A %Y-%m-%d %H:%M IST")


class MissingKiteCredentials(RuntimeError):
    """Raised when 'kite' mode is selected but no API key / access token is
    available. Carries a full, self-explanatory message so the GUI can show
    it in a dialog instead of printing to a terminal nobody is watching."""


def kite_credentials_help() -> str:
    """Explains exactly what's missing and how to fix it — including the
    single most common cause, which is a .env file left behind in an older
    copy of the tool's folder after unzipping a new one."""
    have_key = bool(config.KITE_API_KEY)
    have_token = bool(config.KITE_ACCESS_TOKEN)
    missing = []
    if not have_key:
        missing.append("KITE_API_KEY")
    if not have_token:
        missing.append("KITE_ACCESS_TOKEN")

    searched = "\n".join(f"    {p}" for p in config.dotenv_search_paths())
    found = [p for p in config.dotenv_search_paths() if os.path.exists(p)]

    lines = [
        f"Kite mode needs {' and '.join(missing)}, which {'is' if len(missing) == 1 else 'are'} not set.",
        "",
        "Looked for a .env file here:",
        searched,
        "",
        ("Found a .env at: " + found[0]) if found
        else "No .env file was found in either location.",
        "",
        "Most likely cause:",
    ]
    if not found:
        lines += [
            "  You unzipped a fresh copy of the tool into a NEW folder. Your saved",
            "  token is still sitting in the OLD folder's .env file, which this copy",
            "  can't see.",
            "",
            "  Fix — either copy the old .env across:",
            "      cp '/path/to/old/trading-tool/.env' .",
            "  or just generate a fresh token (takes a minute):",
            "      python3 kite_login_helper.py",
        ]
    else:
        lines += [
            "  A .env was found, so the token in it has most likely EXPIRED.",
            "  Kite access tokens die at about 6:00 am IST every morning, so you",
            "  need a new one each trading day:",
            "      python3 kite_login_helper.py",
        ]
    lines += [
        "",
        "Or switch Mode to 'free' to run without Zerodha (delayed data, and no",
        "live option premiums for Sensex).",
    ]
    return "\n".join(lines)


def get_provider(mode: str):
    if mode == "free":
        return FreeDataProvider()
    elif mode == "kite":
        if not config.KITE_API_KEY or not config.KITE_ACCESS_TOKEN:
            raise MissingKiteCredentials(kite_credentials_help())
        return KiteDataProvider(config.KITE_API_KEY, config.KITE_ACCESS_TOKEN)
    else:
        raise ValueError(f"Unknown mode: {mode}")


def drop_preopen(df, notes=None, index_key=None):
    """Remove bars stamped before the 09:15 open.

    Those belong to the pre-open auction, where indicative prices swing on
    almost no volume. An EMA or an ATR fed one of them is measuring an auction
    rather than a market — and because the auction bar is the FIRST of the day,
    it is still the newest completed bar for the whole of 09:15-09:30, which is
    exactly when the bad signals were firing.

    Bars with no usable timestamp are left alone: dropping data because the
    index is an unexpected type would be worse than the problem being fixed.
    """
    if df is None or not getattr(config, "DROP_PREOPEN_CANDLES", True):
        return df
    # A 24/7 market has no pre-open auction, so there is nothing here to
    # protect against - and applying the rule anyway silently deleted every
    # bar before 09:15 IST. Measured on BTC: 186 of 481 bars, 39% of the
    # history, thrown away for belonging to an auction that does not exist.
    if config.market_for(index_key)["always_open"]:
        return df
    try:
        idx = df.index
        if not hasattr(idx, "hour"):
            return df
        open_h, open_m = MARKET_OPEN_TIME
        minutes = idx.hour * 60 + idx.minute
        keep = minutes >= (open_h * 60 + open_m)
        dropped = int((~keep).sum())
        if dropped and keep.any():
            if notes is not None:
                notes.append(f"(Dropped {dropped} pre-open bar(s) before "
                             f"{open_h:02d}:{open_m:02d}.)")
            return df[keep]
    except Exception:
        pass
    return df


def fetch_recommendation(provider, index_key: str, interval: str, lookback_days, quiet: bool = False, expiry: str = None):
    """Fetch + compute one recommendation. Retries transient failures a
    couple of times (NSE/Yahoo occasionally hiccup) before giving up.
    `expiry` — optional, one of the strings from provider.get_expiry_dates();
    None means "nearest expiry" (the default).
    Returns (rec, notes) — notes is a list of warning strings instead of
    printing directly, so this can be reused by both the terminal tool and
    the GUI without duplicating logic."""
    meta = config.INSTRUMENTS[index_key]
    notes = []

    last_err = None
    df = None
    for attempt in range(1, MAX_FETCH_RETRIES + 2):
        try:
            df = provider.get_ohlc(index_key, interval=interval, lookback_days=lookback_days)
            break
        except Exception as e:
            last_err = e
            if not quiet:
                notes.append(f"(Price fetch attempt {attempt} failed: {e})")
            if attempt <= MAX_FETCH_RETRIES:
                time.sleep(2)
    if df is None:
        raise RuntimeError(f"Could not fetch price data after {MAX_FETCH_RETRIES + 1} attempts: {last_err}")

    df = drop_preopen(df, notes, index_key)

    if len(df) < max(config.EMA_SLOW, config.ATR_LENGTH, config.MACD_SLOW, config.ADX_LENGTH,
                      config.SWING_LOOKBACK) + 5:
        notes.append(f"WARNING: only {len(df)} candles fetched — indicators may be unreliable "
                      f"until more history is available (try a longer lookback or daily interval).")

    tech = compute_technical_signal(df)

    chain = None
    for attempt in range(1, MAX_FETCH_RETRIES + 2):
        try:
            chain = provider.get_option_chain(index_key, expiry=expiry)
            break
        except Exception as e:
            if not quiet:
                notes.append(f"(Option chain fetch attempt {attempt} failed: {e})")
            if attempt <= MAX_FETCH_RETRIES:
                time.sleep(2)

    oi = compute_option_chain_signal(chain)

    # How far can the market realistically travel from here? Measured from
    # the option market's expected move, the OI walls, and the day's
    # remaining range — this is what targets get built from.
    try:
        reach = compute_reachability(tech["last_close"], oi, df, now_ist(),
                                      adx=tech.get("adx"), index_key=index_key)
    except Exception as e:
        reach = None
        notes.append(f"(Reachability check unavailable, using risk-multiple targets: {e})")

    rec = build_recommendation(index_key, tech, oi, meta["strike_step"], reach=reach)
    from signal_engine import opening_range
    rec["opening_range"] = opening_range(df)
    # Carry the candles along so the GUI's live loop can append the
    # in-progress bar to them and recompute indicators between fetches.
    rec["candles"] = df
    # Overall market condition — independent of whether a CE/PE signal fired,
    # so you always know what the market is doing even while it says WAIT.
    try:
        rec["trend"] = compute_market_trend(df)
    except Exception as e:
        rec["trend"] = None
        notes.append(f"(Market-trend read unavailable: {e})")
    return rec, notes


def run_once(provider, index_key: str, interval: str, lookback_days, quiet_retries: bool = False, expiry: str = None):
    """Terminal convenience wrapper: fetch, print notes + full report, return rec."""
    rec, notes = fetch_recommendation(provider, index_key, interval, lookback_days, quiet=quiet_retries, expiry=expiry)
    for n in notes:
        print(n)
    print(format_report(rec))
    return rec


def format_report(rec: dict) -> str:
    """Build the full human-readable report as a single string. Shared by
    the terminal tool (main()) and the GUI (gui.py)."""
    lines = []
    now = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    lines.append("=" * 78)
    lines.append(f" {rec['index']}  |  {now} IST")
    lines.append("=" * 78)
    lines.append(f"Spot price            : {rec['spot']}")

    tr = rec.get("trend")
    if tr:
        arrow = {"UP": "^", "DOWN": "v"}.get(tr["direction"], "-")
        lines.append("-" * 78)
        lines.append(f"MARKET TREND            : {arrow} {tr['label']}")
        if tr["day_change"] is not None:
            sign = "+" if tr["day_change"] >= 0 else ""
            lines.append(f"  Day move              : {sign}{tr['day_change']} pts "
                          f"({sign}{tr['day_change_pct']}%) from open {tr['day_open']}")
        if tr["day_high"] is not None:
            pos = f"{tr['range_pos_pct']}% of range" if tr["range_pos_pct"] is not None else "n/a"
            lines.append(f"  Day range             : {tr['day_low']} — {tr['day_high']}   (now at {pos})")
        htf = tr.get("htf_direction") or "n/a"
        lines.append(f"  This timeframe (15m)  : {tr['direction']}      1-hour: {htf}")
        lines.append(f"  ADX {tr['adx']} ({tr['strength'].lower()}) · momentum {tr['momentum']} · "
                      f"price {tr['vs_vwap']} VWAP ({tr['vwap']})")
        lines.append("-" * 78)

    lines.append(f"Technical score        : {rec['technical']['total_score']} / {rec['technical']['max_score']}"
                  f"  (trend={rec['technical']['trend_score']}, macd={rec['technical']['macd_score']}, "
                  f"rsi={rec['technical']['rsi_score']} [RSI={round(rec['technical']['last_rsi'],1)}], "
                  f"vwap={rec['technical']['vwap_score']})")
    lines.append(f"Trend strength (ADX)   : {rec['technical']['adx']}"
                  f"  ({'OK, trending' if rec['technical']['adx_ok'] else f'WEAK — below {config.ADX_TREND_THRESHOLD}, chop risk'})")
    if rec.get("adx_blocked"):
        lines.append(f"NOTE: score alone said {rec['raw_bias']}, but this signal was BLOCKED by the ADX "
                      f"trend-strength filter — see below.")
    if rec["option_chain"]["available"]:
        lines.append(f"Option-chain (OI) score : {rec['option_chain']['oi_score']}  -> {rec['option_chain']['notes']}")
        lines.append(f"Nearest expiry          : {rec['option_chain'].get('expiry', 'n/a')}")
    else:
        lines.append(f"Option-chain (OI) score : n/a -> {rec['option_chain']['notes']}")
    lines.append("-" * 78)
    lines.append(f"COMBINED SCORE          : {rec['score']} / {rec['max_score']}   Confidence: {rec['confidence']}")

    rch = rec.get("reach")
    if rch and rch.get("available"):
        lines.append("-" * 78)
        lines.append("HOW FAR CAN IT REALISTICALLY GO? (this is what targets are built from)")
        if rch.get("expected_move_remaining") is not None:
            lines.append(f"    Option market expects : +/-{rch['expected_move_remaining']} pts in the "
                          f"{rch['hours_left_today']}h left today")
            lines.append(f"      (ATM straddle = {rch['expected_move_expiry']} pts by expiry {rch.get('expiry')})")
        if rch.get("resistance") or rch.get("support"):
            lines.append(f"    OI walls              : resistance {rch.get('resistance') or 'n/a'} | "
                          f"support {rch.get('support') or 'n/a'}")
        if rch.get("room_left_today") is not None:
            lines.append(f"    Day's range           : typical {rch['typical_daily_range']} pts, "
                          f"used {rch['used_today']} so far -> ~{rch['room_left_today']} pts left")
        lines.append(f"    => Realistic reach    : UP {rch.get('reach_up')} pts | DOWN {rch.get('reach_down')} pts")

    lines.append(f"BIAS                    : {rec['bias']}")
    lines.append(f"SUGGESTED ACTION        : {rec['action']}")

    if rec.get("not_worth_it"):
        lines.append("")
        lines.append("  *** NO TARGETS SHOWN — the market cannot realistically reach a worthwhile")
        lines.append(f"      one from here. Reachable {rec.get('reach_points')} pts vs {rec.get('risk_points')} pts")
        lines.append(f"      of risk (reward:risk {rec.get('reach_to_risk')}). Limited by {rec.get('reach_reason')}.")

    # Explain the silence. Without this, "no signal" looks identical to
    # "tool is broken", and there's no way to tell how close it came.
    if rec["bias"] == "NEUTRAL" and rec.get("blockers"):
        lines.append("")
        lines.append("WHY THERE'S NO TRADE RIGHT NOW:")
        for b in rec["blockers"]:
            lines.append(f"  - {b}")
        if rec.get("votes"):
            arrows = {1: "UP", -1: "DOWN", 0: "--"}
            vote_str = "  ".join(f"{k}:{arrows.get(v, '?')}" for k, v in rec["votes"].items())
            lines.append(f"  Current votes: {vote_str}")
        lines.append(f"  (Strictness is '{rec.get('strictness')}' — see SIGNAL_STRICTNESS in")
        lines.append("   config.py if you want more signals at lower average quality.)")
    if rec["bias"] != "NEUTRAL":
        lines.append(f"Suggested strike        : {rec['suggested_strike']} {rec['option_type']}  (ATM, expiry = nearest weekly)")
        lines.append("")
        sl_basis_label = {
            "swing": f"recent {config.SWING_LOOKBACK}-candle swing "
                     f"{'low' if rec['option_type'] == 'CE' else 'high'} + buffer",
            "atr_fallback": "ATR fallback (no usable recent swing point)",
        }.get(rec.get("sl_basis"), "n/a")
        lines.append("  INDEX-LEVEL LEVELS (underlying price)")
        t1, t2, t3 = rec["index_targets"]
        lines.append(f"    Entry (spot)   : {rec['spot']}")
        if rec.get("target_basis") == "market_reach":
            f1, f2, f3 = config.REACH_FRACTIONS
            lines.append(f"    Target 1       : {t1}   ({int(f1*100)}% of the realistic move)")
            lines.append(f"    Target 2       : {t2}   ({int(f2*100)}% of the realistic move)")
            lines.append(f"    Target 3       : {t3}   (the full realistic move — capped by")
            lines.append(f"                            {rec.get('reach_reason')})")
        else:
            lines.append(f"    Target 1 (1R)  : {t1}")
            lines.append(f"    Target 2 (2R)  : {t2}")
            lines.append(f"    Target 3 (3R)  : {t3}")
        lines.append(f"    Stop-loss      : {rec['index_stop_loss']}   [{sl_basis_label}]")
        lines.append(f"    Risk (1R)      : {rec.get('risk_points')} pts")
        if rec.get("reach_to_risk") is not None:
            lines.append(f"    Reward:risk    : {rec['reach_to_risk']} : 1   "
                          f"({rec.get('reach_points')} pts reachable vs {rec.get('risk_points')} pts risked)")
        lines.append("")
        if rec["premium_source"] == "live":
            pt1, pt2, pt3 = rec["premium_targets"]
            lines.append(f"  LIVE OPTION PREMIUM ({rec['option_type']} {rec['suggested_strike']}) — real LTP from option chain")
            lines.append(f"    Current LTP    : {rec['live_ltp']}")
            if rec.get("target_basis") == "market_reach":
                # Premiums derived from the index distance above, via delta.
                lines.append(f"    Target 1       : {pt1}")
                lines.append(f"    Target 2       : {pt2}")
                lines.append(f"    Target 3       : {pt3}")
                lines.append(f"    Stop-loss      : {rec['premium_stop_loss']}")
                lines.append(f"    (Converted from the index targets above at ~{config.APPROX_ATM_DELTA} delta.")
                lines.append("     Time decay is NOT included — a slow move will land below these.)")
            else:
                lines.append(f"    Target 1 (+{config.PREMIUM_TARGET_PCTS[0]}%): {pt1}")
                lines.append(f"    Target 2 (+{config.PREMIUM_TARGET_PCTS[1]}%): {pt2}")
                lines.append(f"    Target 3 (+{config.PREMIUM_TARGET_PCTS[2]}%): {pt3}")
                lines.append(f"    Stop-loss (-{config.PREMIUM_SL_PCT}%): {rec['premium_stop_loss']}")
            lines.append("    (Common approach: exit 1/3 quantity at each target rather than all at once.)")
        elif rec["premium_source"] == "approx_move":
            pt1, pt2, pt3 = rec["premium_targets"]
            lines.append("  APPROX PREMIUM MOVE (no live option chain available for this index/mode)")
            lines.append(f"    Rough target moves (delta~0.5 estimate): T1 +{pt1} | T2 +{pt2} | T3 +{pt3} pts")
            lines.append(f"    Rough stop-loss move: -{rec['premium_stop_loss']} pts")
            lines.append("    Use --mode kite, or check the live premium yourself on Zerodha/TradingView,")
            lines.append("    for real numbers before trading.")
    lines.append("-" * 78)
    lines.append(f"Last updated: {now_ist().strftime('%H:%M:%S')} IST")
    lines.append("=" * 78)
    return "\n".join(lines)


def format_market_closed_banner(now: dt.datetime) -> str:
    lines = [
        "=" * 78,
        " MARKET IS CLOSED",
        f" Now (India time): {now.strftime('%A %Y-%m-%d %H:%M:%S')} IST",
        f" Next session opens: {next_market_open_str(now)}",
        " (Weekends and known NSE/BSE trading holidays are both detected —",
        "  see NSE_HOLIDAYS_BY_YEAR in main.py if this list ever needs updating.)",
        "=" * 78,
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Nifty/BankNifty/Sensex CE-PE signal tool")
    parser.add_argument("--index", choices=list(config.INSTRUMENTS.keys()), default="NIFTY")
    parser.add_argument("--mode", choices=["free", "kite"], default="free",
                         help="'free' = Yahoo/NSE public data (delayed). "
                              "'kite' = your Zerodha Kite Connect subscription (real-time).")
    parser.add_argument("--interval", choices=["5m", "15m", "1d"], default="15m")
    parser.add_argument("--lookback-days", type=int, default=None,
                         help="Override default history window.")
    parser.add_argument("--live", action="store_true",
                         help="Keep running, re-checking every --refresh seconds "
                              "(only during market hours 9:15-15:40 IST).")
    parser.add_argument("--refresh", type=int, default=60, help="Seconds between refreshes in --live mode.")
    parser.add_argument("--expiry", type=str, default=None,
                         help="Specific expiry date string (as shown by --list-expiries). "
                              "Default: nearest expiry.")
    parser.add_argument("--list-expiries", action="store_true",
                         help="Print available expiry dates for --index and exit.")
    args = parser.parse_args()

    print(DISCLAIMER)

    if args.index == "SENSEX" and args.mode == "free":
        print("NOTE: Free mode has no public BSE option-chain source for SENSEX. "
              "Signal will be technical-only (no OI/PCR component). Use --mode kite for full signal.\n")

    try:
        provider = get_provider(args.mode)
    except MissingKiteCredentials as e:
        print("=" * 78)
        print(" CANNOT START IN KITE MODE")
        print("=" * 78)
        print(e)
        print("=" * 78)
        sys.exit(1)

    if args.list_expiries:
        expiries = provider.get_expiry_dates(args.index)
        if not expiries:
            print(f"No expiry dates available for {args.index} (option chain unavailable in this mode).")
        else:
            print(f"Available expiries for {args.index}:")
            for e in expiries:
                print(f"  {e}")
        return

    if not args.live:
        run_once(provider, args.index, args.interval, args.lookback_days, expiry=args.expiry)
        return

    print(f"Live mode: refreshing every {args.refresh}s during market hours "
          f"(Mon-Fri {MARKET_OPEN_TIME[0]:02d}:{MARKET_OPEN_TIME[1]:02d}-"
          f"{MARKET_CLOSE_TIME[0]:02d}:{MARKET_CLOSE_TIME[1]:02d} IST — India Standard Time, "
          f"converted automatically from your computer's own clock/timezone). Press Ctrl+C to stop.\n")

    last_signature = None        # (bias, option_type, suggested_strike) — full, for change detection
    last_bias_signature = None   # (bias, option_type) only — whether the DIRECTION actually changed
    cycle_count = 0
    was_open_last_check = None

    try:
        while True:
            now = now_ist()
            market_open_now = is_market_open(now)

            if not market_open_now:
                if was_open_last_check is not False:
                    # Just transitioned to closed (or this is the first check) — print the banner clearly.
                    print(format_market_closed_banner(now))
                else:
                    print(f"[{now.strftime('%H:%M:%S')} IST] Market still closed. Waiting...")
                was_open_last_check = False
                last_signature = None  # reset so the first signal after reopen always announces itself
                last_bias_signature = None
                time.sleep(min(args.refresh, 300))  # no need to poll every second while closed
                continue

            was_open_last_check = True
            cycle_count += 1

            # Periodically force a fresh NSE session — long-running loops can
            # end up with stale cookies otherwise.
            if cycle_count % SESSION_REFRESH_EVERY == 0 and hasattr(provider, "refresh_session"):
                provider.refresh_session()

            try:
                rec = run_once(provider, args.index, args.interval, args.lookback_days, quiet_retries=True, expiry=args.expiry)
                signature = (rec["bias"], rec["option_type"], rec["suggested_strike"])
                bias_signature = (rec["bias"], rec["option_type"])

                if last_bias_signature is not None and bias_signature != last_bias_signature:
                    print(">>> SIGNAL CHANGED (direction) since last update <<<")
                    print(f">>> Was: {last_bias_signature}  ->  Now: {bias_signature}")
                elif (
                    last_signature is not None
                    and bias_signature == last_bias_signature
                    and rec["suggested_strike"] != last_signature[2]
                ):
                    # Same direction, just a different ATM strike because spot
                    # crossed a rounding boundary — a DIFFERENT contract's LTP,
                    # not the same one moving. Flagged distinctly so it isn't
                    # mistaken for the tool suddenly showing a wrong price.
                    print(f">>> Suggested strike shifted {last_signature[2]} -> {rec['suggested_strike']} "
                          f"(spot crossed a rounding boundary) — direction unchanged <<<")

                last_bias_signature = bias_signature
                last_signature = signature
            except Exception as e:
                print(f"[{now.strftime('%H:%M:%S')}] Error during refresh (will retry next cycle): {e}")

            time.sleep(args.refresh)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
