#!/usr/bin/env python3
"""
crypto_checklist_study.py — the user's own pasted BTC checklist votes, on the
tool's OWN BTC trades
================================================================================
The user, 2 Oct 2026: "what about the votes for the trades i gave you for btc".
crypto_strategy_study.py tested the two pasted checklists only on a fresh
momentum-breakout entry. This puts each pasted vote on top of the tool's own
live engine (today's Trend/MACD/RSI/VWAP vote, ADX 20, crypto reward:risk 2.0)
as an extra "must say YES" - applied INSIDE backtest_intraday.run()'s bar loop,
so a refused signal frees the slot for the next one exactly as live.

THE VOTES (as written in the two pastes; pre-declared):
    room60    paste 1: the day (UTC, Delta's 05:30 IST reset) has used < 60% of
              the 14-day daily ATR (previous days only)
    room50    paste 2: < 50%
    vol2x     paste 1: the entry candle's volume >= 2x the previous 10 candles'
    funding   paste 1: no long while 8h funding > +0.03%, no short while < -0.03%
    us        paste 2: enter only 18:30-23:30 IST
    no_asia   paste 2: no entries 09:00-14:00 IST
    no_aftn   paste 2's theta vote: no entries 13:00-17:30 IST (the same-day
              option's last hours)
    paste1    room60 + vol2x + funding (its 1:2 reward:risk is already live)
    paste2    room50 + us
    NOT TESTABLE: open interest (no free history), BTC alignment (meaningless when
    trading BTC itself), leverage (an option buyer's loss is capped at premium).

Everything else is the live system, priced as crypto_strategy_study.py prices it
(options premium-tracked as live on the next-day contract, perp taker/maker,
gross points). Adopted only if it beats today in BOTH periods and is profitable
after costs in both.

    python3 crypto_checklist_study.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

KEY = "BTC"
FILTERS = {
    "room60 (paste 1)": ("room60",),
    "room50 (paste 2)": ("room50",),
    "vol2x (paste 1)": ("vol2x",),
    "funding (paste 1)": ("funding",),
    "US session only (paste 2)": ("us",),
    "no Asia 09-14 (paste 2)": ("no_asia",),
    "no 13:00-17:30 (paste 2)": ("no_aftn",),
    "paste 1 full checklist": ("room60", "vol2x", "funding"),
    "paste 2 full checklist": ("room50", "us"),
}


def _arrays(df, fund):
    import crypto_strategy_study as cs
    BAR = cs.BAR
    close_t = df.index + BAR
    vol = df["Volume"].to_numpy()
    vavg10 = df["Volume"].rolling(10).mean().shift(1).to_numpy()
    return {"room": cs.utc_day_room(df), "vol": vol, "vavg10": vavg10,
            "fund": fund.to_numpy(), "mins": (close_t.hour * 60 + close_t.minute).to_numpy()}


def votes_ok(a, i, side, filters):
    """True if every named pasted vote says YES at bar i for this side."""
    room, mins = a["room"][i], a["mins"][i]
    for f in filters:
        if f == "room60" and room == room and room >= 0.60:
            return False
        if f == "room50" and room == room and room >= 0.50:
            return False
        if f == "vol2x" and not (a["vavg10"][i] > 0 and a["vol"][i] >= 2 * a["vavg10"][i]):
            return False
        if f == "funding":
            fr = a["fund"][i]
            if fr == fr and ((side == "CE" and fr > 0.0003) or (side == "PE" and fr < -0.0003)):
                return False
        if f == "us" and not (18 * 60 + 30 <= mins <= 23 * 60 + 30):
            return False
        if f == "no_asia" and 9 * 60 <= mins < 14 * 60:
            return False
        if f == "no_aftn" and 13 * 60 <= mins < 17 * 60 + 30:
            return False
    return True


def _entries(name):
    import pickle
    import backtest_intraday as bt
    import crypto_strategy_study as cs
    import tickets

    df, iv, fund = cs.load()
    tag = "".join(ch for ch in name if ch.isalnum())[:24]
    path = os.path.join(os.path.dirname(bt._cache_path(KEY, 3)), f"btc_checklist_{tag}_{len(df)}.pkl")
    if os.path.exists(path):
        return name, path
    a = _arrays(df, fund)
    flt = FILTERS[name]

    def gate(i, rec):
        return (tickets.TicketBook._reward_hold(cs._SELF, KEY, rec) is None
                and tickets.TicketBook._spread_hold(cs._SELF, rec) is None
                and votes_ok(a, i, rec.get("option_type"), flt))

    entries = bt.run(KEY, df, gate=gate)["trades"]
    with open(path, "wb") as fh:
        pickle.dump(entries, fh)
    return name, path


def main():
    import pickle
    import backtest_intraday as bt
    import config
    import crypto_strategy_study as cs
    import crypto_vote_study as cvs
    import indicators as ind

    df, iv, fund = cs.load()
    # Today's entries and the per-bar signal for the reversal exit: the vote study's
    # own cached V0 run (these filters do not change the vote, only who is let in).
    _, v0_path = cvs._entries_and_bias("V0  today (Trend,MACD,RSI,VWAP 3/1)")
    with open(v0_path, "rb") as fh:
        v0 = pickle.load(fh)
    opp = v0["opp"]

    workers = max(1, min(len(FILTERS), (os.cpu_count() or 2) - 1))
    print(f"Replaying the live engine with each pasted vote on {workers} processes...", flush=True)
    paths = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for name, path in ex.map(_entries, FILTERS):
            paths[name] = path
            print(f"  done: {name}", flush=True)

    pre = bt.precompute(df, KEY)
    adx = pre["adx"].to_numpy()
    st_line = ind.supertrend(df, *config.supertrend_params(KEY))[0].to_numpy()
    a = _arrays(df, fund)
    pos = {t: n for n, t in enumerate(df.index)}

    sets = {"today (no pasted vote)": v0["entries"]}
    for name in FILTERS:
        with open(paths[name], "rb") as fh:
            sets[name] = pickle.load(fh)
        # Invariant: every entry the gate let through really does pass its votes.
        bad = [tr for tr in sets[name] if not votes_ok(a, pos[tr["when"]], tr["side"], FILTERS[name])]
        assert not bad, f"{name}: {len(bad)} entries violate their own votes"

    R = {}
    for name, ents in sets.items():
        idx = cs.price_all(cs.run_live(df, iv, ents, opp, st_line, adx), df, iv)
        R[name] = {"gross": idx, "perp": idx, "perp_maker": idx,
                   "opt_next_day": cs.run_live_premium(df, iv, ents, opp, st_line, adx, "next_day")}

    def cell(rows, key):
        s = cs.stats(rows, key)
        return f"{s['n']:>4} {s['total']:>+9,.0f} PF{s['pf']:>5.2f}"

    print("\n" + "=" * 150)
    print(" BTC - the tool's own entries + each pasted vote. $ per 1 BTC (x0.25 for 250 lots), after Delta fees"
          " + 18% GST (+2% option spread). n, total, PF.")
    print("=" * 150)
    for key, label in (("gross", "GROSS POINTS - no costs"),
                       ("opt_next_day", "OPTIONS, next-day, premium-tracked as live (what the tool trades)"),
                       ("perp", "PERPETUAL, taker"),
                       ("perp_maker", "PERPETUAL, maker")):
        print(f"\n {label}")
        print(f" {'entry rule':34s} {'IN-SAMPLE':>26s} {'HELD-OUT':>26s} {'LIVE WINDOW':>26s}")
        for name in sets:
            p = cs.periods(R[name][key])
            print(f" {name:34s} {cell(p['is'], key):>26s} {cell(p['oos'], key):>26s} {cell(p['live'], key):>26s}")

    print("\n VERDICT - vs today, both periods; adopted only if also profitable after costs in both")
    for key in ("opt_next_day", "perp", "perp_maker"):
        base = cs.periods(R["today (no pasted vote)"][key])
        b_is, b_oos = cs.stats(base["is"], key)["total"], cs.stats(base["oos"], key)["total"]
        for name in FILTERS:
            p = cs.periods(R[name][key])
            a_is, a_oos = cs.stats(p["is"], key)["total"], cs.stats(p["oos"], key)["total"]
            better = a_is > b_is and a_oos > b_oos
            verdict = "ADOPT" if (better and a_is > 0 and a_oos > 0) else ("loses less, still loses" if better else "worse")
            print(f"   {key:12s} {name:30s} in-sample {a_is - b_is:>+9,.0f}  held-out {a_oos - b_oos:>+9,.0f}  -> {verdict}")
    return R


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
