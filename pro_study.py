#!/usr/bin/env python3
"""
pro_study.py — what an experienced trader would add, tested before it is trusted
================================================================================
Each idea is one pre-declared variant, layered on the rules the tool runs NOW
(opening-range break on). Pre-declared because trying ten settings of each and
keeping the best is how a backtest learns the past instead of the market.

A variant is kept only if it improves the pooled result in-sample AND on the
held-out final year, after Zerodha's costs, priced as the option you would buy.

What is new against regime_study.py:

  * REAL EXPIRIES. Days to expiry used to be a fixed guess, and it moved the
    result more than any filter did. Each trade is now priced against the
    contract the tool would actually have suggested - the nearest expiry on the
    exchange calendar in force that day, stepped back over holidays using the
    days the index actually traded:
        NIFTY      weekly Thursday; Tuesday from 2 Sep 2025
        SENSEX     weekly Friday; Tuesday from 7 Jan 2025; Thursday from 4 Sep 2025
        BANKNIFTY  weekly Wednesday until 13 Nov 2024, then monthly only -
                   last Thursday, last Tuesday from Sep 2025

  * EXITS, re-simulated on the same entries. The entries do not depend on how
    a trade is exited, so every exit policy is measured on identical trades.

  * A DAILY LOSS BRAKE, applied across the three indices on a merged timeline:
    a loss only counts once it has closed, as it would live.

Limits carried over: IV constant through a trade (IV crush not modelled);
today's lot sizes; VIX-scaled volatility; slippage 0.25% a side.

WHAT IT FOUND (11 Sep 2026, history to 14 Aug 2026)
  * Most of the profit sits on the contract's own expiry day - the one day a
    constant-volatility price is least like the real premium. The edge is not
    proven by this model; the tool's own live premium record is what can.
  * Adopted: T3 at least 1x the stop distance (MIN_REWARD_RISK_T3), and Bank
    Nifty watch-only (WATCH_ONLY_INDICES). Together: profit factor 1.14 -> 1.21
    in-sample and 1.05 -> 1.17 held-out; worst drawdown 115k -> 86k and
    179k -> 110k.
  * Dropped: skipping expiry day, the next expiry on expiry day, VIX > 20,
    gaps > 1%, breakeven after T1, half off at T1, a two-hour time stop.
  * The daily loss brake helped the held-out year and hurt in-sample; it is
    kept as a limit on the downside (DAILY_LOSS_LIMIT_R), not as an edge.

WHICH TARGET THIS EXITS AT
    The same one the live ticket engine uses - config.EXIT_AT_TARGET, today T2.
    It used to be hard-coded to T3 here while tickets closed at T2, so every
    number this file produced described an exit the tool does not perform. The
    setting is read now, so the two cannot disagree again without someone
    changing the setting itself.

    python3 pro_study.py
"""
import datetime as dt
import math

import numpy as np
import pandas as pd

import screener

import backtest_intraday as bt
import config
import regime_study as rs

IST = "Asia/Kolkata"
INDICES = rs.INDICES
SPLIT = rs.SPLIT
SLIP = rs.SLIP


# ---------------------------------------------------------------- expiries
def _last_weekday(year, month, weekday):
    d = dt.date(year + (month == 12), (month % 12) + 1, 1) - dt.timedelta(days=1)
    while d.weekday() != weekday:
        d -= dt.timedelta(days=1)
    return d


def _scheduled(key, d):
    """The nearest scheduled expiry on or after d, before holiday adjustment."""
    MON, TUE, WED, THU, FRI = range(5)
    if key == "NIFTY":
        wd = TUE if d >= dt.date(2025, 9, 1) else THU
    elif key == "SENSEX":
        wd = (THU if d >= dt.date(2025, 9, 1) else
              TUE if d >= dt.date(2025, 1, 4) else FRI)
    else:  # BANKNIFTY
        if d <= dt.date(2024, 11, 13):
            wd = WED if d >= dt.date(2023, 9, 6) else THU
        else:
            for k in range(0, 3):
                y, m = d.year + (d.month + k - 1) // 12, (d.month + k - 1) % 12 + 1
                e = _last_weekday(y, m, TUE if dt.date(y, m, 1) >= dt.date(2025, 9, 1) else THU)
                if e >= d:
                    return e
    delta = (wd - d.weekday()) % 7
    return d + dt.timedelta(days=delta)


def expiry_on_or_after(key, d, trading_days):
    """Scheduled expiry, moved to the previous trading day if the exchange was
    shut - and if that lands before d, the next cycle's."""
    probe = d
    for _ in range(6):
        e = _scheduled(key, probe)
        adj = e
        while adj not in trading_days and adj > d - dt.timedelta(days=7):
            adj -= dt.timedelta(days=1)
        if adj >= d and adj in trading_days:
            return adj
        probe = e + dt.timedelta(days=1)
    return _scheduled(key, d)


def years_to(expiry_date, when):
    exp = pd.Timestamp(dt.datetime.combine(expiry_date, dt.time(15, 30)), tz=IST)
    return max((exp - when).total_seconds() / (365 * 86400), 1e-6)


# ---------------------------------------------------------------- exits
def simulate(A, i, tr, hold_bars=26, square_off=True,
             be_after_t1=False, half_at_t1=False, time_stop=None, target=None):
    """Legs of one trade under an exit policy: [(exit_spot, bar, weight), ...].

    Stop is tested before targets inside a bar - the bar does not say which came
    first, and the pessimistic order is the only defensible one."""
    if target is None:
        target = str(getattr(config, "EXIT_AT_TARGET", "T3") or "T3").lower()
        if target not in ("t1", "t2", "t3"):
            target = "t3"
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    stop, legs, rem, t1_done = tr["stop"], [], 1.0, False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            legs.append((stop, j, rem)); return legs
        if not t1_done and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
            t1_done = True
            if half_at_t1:
                legs.append((tr["t1"], j, 0.5)); rem = 0.5
            if be_after_t1 or half_at_t1:
                stop = tr["entry"]
        tgt = tr.get(target) if hasattr(tr, "get") else tr[target]
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            legs.append((tgt, j, rem)); return legs
        if time_stop and not t1_done and (j - i) >= time_stop:
            legs.append((cl[j], j, rem)); return legs
        if square_off and end[j]:
            legs.append((cl[j], j, rem)); return legs
    last = min(i + hold_bars, n - 1)
    legs.append((cl[last], last, rem))
    return legs


def price(key, df, A, i, tr, legs, sigma, next_expiry_on_expiry_day=False):
    """Net rupees per lot for one trade and its legs, against the real contract."""
    meta = config.INSTRUMENTS[key]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    when = df.index[i] + pd.Timedelta(minutes=15)             # entry at the bar's close
    exp = expiry_on_or_after(key, when.date(), A["days"])
    if next_expiry_on_expiry_day and exp == when.date():
        exp = expiry_on_or_after(key, when.date() + dt.timedelta(days=1), A["days"])
    call = tr["side"] == "CE"
    K = round(tr["entry"] / step) * step
    p0 = rs.bs(tr["entry"], K, years_to(exp, when), sigma, call)
    if p0 <= 0.5:
        return None
    buy = p0 * (1 + SLIP) * qty
    sells, orders = 0.0, 1
    for spot, j, w in legs:
        t_exit = df.index[j] + pd.Timedelta(minutes=15)
        p1 = rs.bs(spot, K, years_to(exp, t_exit), sigma, call)
        sells += max(p1 * (1 - SLIP), 0.0) * qty * w
        orders += 1
    brokerage = rs.BROKERAGE_PER_ORDER * orders
    stt = rs.STT_SELL * sells
    txn = rs.TXN[exch] * (buy + sells)
    sebi = rs.SEBI_PER_RUPEE * (buy + sells)
    stamp = rs.STAMP_BUY * buy
    gst = rs.GST * (brokerage + txn + sebi)
    net = sells - buy - (brokerage + stt + txn + sebi + stamp + gst)
    exit_bar = max(j for _, j, _ in legs)
    return {"when": when, "exit_time": df.index[exit_bar] + pd.Timedelta(minutes=15),
            "net": net, "expiry_day": exp == when.date(), "index": key}


# ---------------------------------------------------------------- features
def _side(r):
    """CE or PE for a recommendation, whichever field carries it."""
    side = r.get("option_type")
    if side in ("CE", "PE"):
        return side
    return {"BULLISH": "CE", "BEARISH": "PE"}.get(r.get("bias"))


def extra_features(df, vix):
    day = pd.Series(df.index.date, index=df.index)
    dc = rs.daily_close(df)
    first_open = df["Open"].groupby(df.index.date).first()
    gap = (first_open / dc.shift(1) - 1.0).abs()
    vprev = pd.Series(vix).shift(1)
    # Realised volatility over twenty sessions, annualised, shifted a day so a
    # bar only ever sees finished history. Paired with VIX it gives the
    # variance premium - what the market charges against what the index has
    # actually been doing. The absolute VIX filter tested here before said
    # nothing about that: 20 is cheap in a wild month and dear in a quiet one.
    rv = (np.log(dc).diff().rolling(20).std() * math.sqrt(252) * 100).shift(1)
    # Bollinger (20, 2) and Stochastic %K (14, smoothed 3) on the 15-minute
    # bars, read at the bar that makes the entry - the close the entry is at.
    cl, hi, lo = df["Close"], df["High"], df["Low"]
    mid, sdv = cl.rolling(20).mean(), cl.rolling(20).std()
    ll, hh = lo.rolling(14).min(), hi.rolling(14).max()
    stoch = (100 * (cl - ll) / (hh - ll)).rolling(3).mean()
    # CCI (20), Williams %R (14) and Parabolic SAR (0.02, 0.2), the Screener's
    # definitions, on the same bars and read at the same entry bar.
    tp = (hi + lo + cl) / 3.0
    mad = tp.rolling(20).apply(lambda x: float(np.mean(np.abs(x - x.mean()))), raw=True)
    cci = (tp - tp.rolling(20).mean()) / (0.015 * mad.replace(0, np.nan))
    wr = -100.0 * (hh - cl) / (hh - ll).replace(0, np.nan)
    sar = screener._psar(hi, lo, 0.02, 0.2)
    return {"gap": day.map(gap).to_numpy(), "vix": day.map(vprev).to_numpy(),
            "rv": day.map(rv).to_numpy(), "cl": cl.to_numpy(),
            "bb_up": (mid + 2 * sdv).to_numpy(), "bb_lo": (mid - 2 * sdv).to_numpy(),
            "stoch": stoch.to_numpy(), "cci": cci.to_numpy(), "wr": wr.to_numpy(),
            "sar": sar.to_numpy()}


# ---------------------------------------------------------------- scoring
def stats(rows):
    if not rows:
        return None
    net = np.array([r["net"] for r in rows])
    eq = np.cumsum(net)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    w, l = net[net > 0].sum(), -net[net < 0].sum()
    return {"n": len(net), "win": float((net > 0).mean() * 100), "avg": float(net.mean()),
            "total": float(net.sum()), "pf": float(w / l) if l else float("inf"), "dd": dd}


def daily_brake(rows, max_losses=2):
    """Across all three indices: after `max_losses` closed losing trades in a
    day, no new trade that day. A loss counts only once it has closed."""
    out = []
    for d, grp in pd.DataFrame(rows).groupby(lambda k: rows[k]["when"].date()):
        closed_losses = []
        for _, r in grp.sort_values("when").iterrows():
            n_lost = sum(1 for (x, t) in closed_losses if t <= r["when"])
            if n_lost >= max_losses:
                continue
            out.append(r.to_dict())
            if r["net"] < 0:
                closed_losses.append((r["net"], r["exit_time"]))
    return out


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    X = {k: extra_features(hists[k], vix) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}

    # Entries: today's live rules (opening-range break), plus entry-only variants.
    base_gate = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}
    entry_variants = {
        "LIVE RULES (OR break)": lambda k: base_gate[k],
        "skip expiry day": lambda k: (lambda i, r, k=k: base_gate[k](i, r) and not
                                      (expiry_on_or_after(k, (hists[k].index[i]).date(), A[k]["days"])
                                       == hists[k].index[i].date())),
        "skip VIX > 20": lambda k: (lambda i, r, k=k: base_gate[k](i, r) and not
                                    (X[k]["vix"][i] == X[k]["vix"][i] and X[k]["vix"][i] > 20)),
        # Reward to the FINAL target at least equal to the risk to the stop.
        # Pre-declared at 1.0, the level every risk manager starts from - not
        # swept. Live tickets on 11 Sep had T3 at 0.66x the stop distance.
        "min reward:risk 1 (T3)": lambda k: (lambda i, r, k=k: base_gate[k](i, r) and
                                    r.get("risk_points") and r["index_targets"][2] is not None and
                                    abs(r["index_targets"][2] - r["spot"]) >= r["risk_points"]),
        "R:R 1 + Bank Nifty watch-only": lambda k: (lambda i, r, k=k: k != "BANKNIFTY" and
                                    entry_variants["min reward:risk 1 (T3)"](k)(i, r)),
        "skip gap > 1%": lambda k: (lambda i, r, k=k: base_gate[k](i, r) and not
                                    (X[k]["gap"][i] == X[k]["gap"][i] and X[k]["gap"][i] > 0.01)),
        # PRE-DECLARED 13 Sep 2026, one threshold, not swept: skip the entry
        # when implied volatility is at least half again the realised. See the
        # note in this file's docstring - if it fails either period it is
        # reported failed and nothing changes.
        "skip premium >= 1.5x realised": lambda k: (lambda i, r, k=k: base_gate[k](i, r) and not
                                    (X[k]["vix"][i] == X[k]["vix"][i] and
                                     X[k]["rv"][i] == X[k]["rv"][i] and X[k]["rv"][i] > 0 and
                                     X[k]["vix"][i] >= 1.5 * X[k]["rv"][i])),
        # PRE-DECLARED 15 Sep 2026, from reviewing the Jinni project: two textbook
        # filters it uses that TradePicker does not, at their standard settings,
        # not swept. Layered on the rules live today. Kept only if better than
        # those in BOTH periods; otherwise reported failed and nothing changes.
        # RESULT, history to 11 Sep 2026, per lot after costs, against the live rules
        # (1,260 trades +333,421 PF 1.30 DD 57,950 | 939 +221,110 PF 1.25 DD 106,038):
        #   Bollinger  1,094 +268,512 PF 1.28 DD 36,437 | 822 +234,414 PF 1.31 DD 73,326
        #   Stochastic   424  +48,555 PF 1.12 DD 46,831 | 269  +60,812 PF 1.27 DD 45,898
        # Bollinger made less in-sample (-64,909) though more held-out and with a
        # shallower drawdown in both; Stochastic made far less in both. Neither is
        # better in both periods, so neither is used. Not re-tuned after the fact.
        "live + Bollinger, don't chase": lambda k: (lambda i, r, k=k:
            entry_variants["R:R 1 + Bank Nifty watch-only"](k)(i, r) and not (
                X[k]["bb_up"][i] == X[k]["bb_up"][i] and (
                    (_side(r) == "CE" and X[k]["cl"][i] > X[k]["bb_up"][i]) or
                    (_side(r) == "PE" and X[k]["cl"][i] < X[k]["bb_lo"][i])))),
        "live + Stochastic, not exhausted": lambda k: (lambda i, r, k=k:
            entry_variants["R:R 1 + Bank Nifty watch-only"](k)(i, r) and not (
                X[k]["stoch"][i] == X[k]["stoch"][i] and (
                    (_side(r) == "CE" and X[k]["stoch"][i] > 80) or
                    (_side(r) == "PE" and X[k]["stoch"][i] < 20)))),
        # PRE-DECLARED 15 Sep 2026, before any result, from the indicators added to
        # the Screener after reviewing lshariprasad/Stock-Trading-Agent. One
        # textbook rule each at standard settings, not swept, layered on the rules
        # live today, kept only if better in BOTH periods:
        #   CCI(20)          calls only above +100, puts only below -100 (Lambert)
        #   Williams %R(14)  no call above -20, no put below -80 (not exhausted)
        #   Parabolic SAR    calls only above the SAR, puts only below it
        # A bar with no reading yet is not held against the trade.
        # RESULT, history to 11 Sep 2026, per lot after costs, against the live rules
        # (1,260 trades +333,421 PF 1.30 DD 57,950 | 939 +221,110 PF 1.25 DD 106,038):
        #   CCI         878 +206,538 PF 1.25 DD 41,594 | 661  +74,813 PF 1.11 DD 140,126
        #   Williams    426  +23,322 PF 1.06 DD 62,262 | 279  +90,031 PF 1.44 DD  26,181
        #   SAR       1,199 +310,822 PF 1.29 DD 46,689 | 906 +217,947 PF 1.25 DD 104,918
        # All three made less in both periods. The SAR agreed with 95% of the live
        # entries already - the momentum rule does its job - and the 94 it removed
        # were worth money. Williams %R's held-out PF rests on 279 trades after a
        # PF 1.06 in-sample year. None is used. Not re-tuned after the fact.
        "live + CCI beyond 100": lambda k: (lambda i, r, k=k:
            entry_variants["R:R 1 + Bank Nifty watch-only"](k)(i, r) and not (
                X[k]["cci"][i] == X[k]["cci"][i] and (
                    (_side(r) == "CE" and X[k]["cci"][i] <= 100) or
                    (_side(r) == "PE" and X[k]["cci"][i] >= -100)))),
        "live + Williams %R, not exhausted": lambda k: (lambda i, r, k=k:
            entry_variants["R:R 1 + Bank Nifty watch-only"](k)(i, r) and not (
                X[k]["wr"][i] == X[k]["wr"][i] and (
                    (_side(r) == "CE" and X[k]["wr"][i] > -20) or
                    (_side(r) == "PE" and X[k]["wr"][i] < -80)))),
        "live + SAR agrees": lambda k: (lambda i, r, k=k:
            entry_variants["R:R 1 + Bank Nifty watch-only"](k)(i, r) and not (
                X[k]["sar"][i] == X[k]["sar"][i] and (
                    (_side(r) == "CE" and X[k]["cl"][i] <= X[k]["sar"][i]) or
                    (_side(r) == "PE" and X[k]["cl"][i] >= X[k]["sar"][i])))),
        # LIVE FROM 15 Sep 2026, the user's choices after seeing each cost: wait
        # for the opening range with no break (REGIME_OR_REQUIRE_BREAK False),
        # T3 at least 1x the stop, and Bank Nifty traded. History to 11 Sep 2026:
        #   3,305 trades +339,871 PF 1.10 DD 132,176 | 2,436 +5,524 PF 1.00 DD 364,906
        # By index, held-out: Nifty +110,219, Sensex +112,963, Bank Nifty -217,658.
        # Later that day the RSI-divergence entry filter was added (skills_study.py,
        # variant D, better in both periods). With it, the live totals are
        #   3,233 trades +359,660 PF 1.10 DD 111,305 | 2,380 +34,528 PF 1.01 DD 343,799
        # This row stays as it was measured, without the filter.
        "LIVE 15 Sep: range wait + R:R 1, all three": lambda k: (lambda i, r, k=k:
            bool(F[k]["or_ready"].iloc[i]) and bool(r.get("risk_points"))
            and r["index_targets"][2] is not None
            and abs(r["index_targets"][2] - r["spot"]) >= r["risk_points"]),
    }
    exit_variants = {
        "hold to T3/stop (live)": {},
        "breakeven after T1": {"be_after_t1": True},
        "half at T1, BE rest": {"half_at_t1": True},
        "time stop 2h": {"time_stop": 8},
    }

    # Sanity: the default exit policy must reproduce the backtest's own exits.
    res = bt.run("NIFTY", hists["NIFTY"], gate=base_gate["NIFTY"])
    pos = {t: n for n, t in enumerate(hists["NIFTY"].index)}
    mism = 0
    for tr in res["trades"]:
        legs = simulate(A["NIFTY"], pos[tr["when"]], tr)
        if abs(legs[-1][0] - tr["exit"]) > 1e-6:
            mism += 1
    print(f"Exit simulator vs backtest, {len(res['trades'])} NIFTY trades: {mism} exits differ\n")

    def evaluate(entry_name, exit_kw, next_exp=False, brake=None):
        rows = []
        for k in INDICES:
            df = hists[k]
            res = bt.run(k, df, gate=entry_variants[entry_name](k))
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in res["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = simulate(A[k], i, tr, **exit_kw)
                p = price(k, df, A[k], i, tr, legs, s, next_expiry_on_expiry_day=next_exp)
                if p:
                    rows.append(p)
        if brake:
            rows = daily_brake(rows, brake)
        return ({"is": stats([r for r in rows if r["when"] < SPLIT]),
                 "oos": stats([r for r in rows if r["when"] >= SPLIT])}, rows)

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:30s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    hdr = (f" {'variant':30s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':54s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print("=" * 128); print(" All per lot, pooled across NIFTY / BANKNIFTY / SENSEX, real expiries, after costs")
    print("=" * 128); print(hdr); print("-" * 128)

    base, base_rows = evaluate("LIVE RULES (OR break)", {})
    print(line("LIVE RULES (OR break)", base))
    exp_share = np.mean([r["expiry_day"] for r in base_rows]) * 100
    print(f"   ({exp_share:.0f}% of these trades were on the contract's own expiry day)")
    # Where the money comes from. If it is concentrated on expiry day, that is
    # the finding - because expiry day is exactly where a constant-volatility
    # Black-Scholes price is least like the real premium.
    bd = pd.DataFrame(base_rows)
    bd["oos"] = bd["when"] >= SPLIT
    piv = bd.groupby(["index", "expiry_day", "oos"])["net"].agg(["count", "sum"])
    print("   by index and day type (per lot):   in-sample            | held-out")
    for k in INDICES:
        for e in (True, False):
            a = piv.loc[(k, e, False)] if (k, e, False) in piv.index else None
            b = piv.loc[(k, e, True)] if (k, e, True) in piv.index else None
            fa = f"{int(a['count']):>4} ₹{a['sum']:>+9,.0f}" if a is not None else "   —"
            fb = f"{int(b['count']):>4} ₹{b['sum']:>+9,.0f}" if b is not None else "   —"
            print(f"     {k:9s} {'expiry day' if e else 'other days':11s}   {fa}   | {fb}")
    bn = bd[(bd["index"] == "BANKNIFTY")]
    for lab, lo, hi in (("weekly era (to 13 Nov 2024)", None, "2024-11-14"),
                        ("monthly-only, in-sample", "2024-11-14", SPLIT),
                        ("monthly-only, held-out", SPLIT, None)):
        m = bn
        if lo is not None:
            m = m[m["when"] >= (lo if isinstance(lo, pd.Timestamp) else pd.Timestamp(lo, tz=IST))]
        if hi is not None:
            m = m[m["when"] < (hi if isinstance(hi, pd.Timestamp) else pd.Timestamp(hi, tz=IST))]
        print(f"     BANKNIFTY {lab:30s} {len(m):>4} trades  ₹{m['net'].sum():>+9,.0f}")
    print("-" * 128)
    out = {"LIVE RULES (OR break)": base}
    r, _ = evaluate("LIVE RULES (OR break)", {}, next_exp=True); out["next expiry on expiry day"] = r
    print(line("next expiry on expiry day", r))
    for name in ("skip expiry day", "skip VIX > 20", "skip gap > 1%",
                 "skip premium >= 1.5x realised", "min reward:risk 1 (T3)",
                 "R:R 1 + Bank Nifty watch-only",
                 "live + Bollinger, don't chase", "live + Stochastic, not exhausted",
                 "live + CCI beyond 100", "live + Williams %R, not exhausted", "live + SAR agrees",
                 "LIVE 15 Sep: range wait + R:R 1, all three"):
        r, _ = evaluate(name, {}); out[name] = r; print(line(name, r))
    for name, kw in list(exit_variants.items())[1:]:
        r, _ = evaluate("LIVE RULES (OR break)", kw); out[name] = r; print(line(name, r))
    r, _ = evaluate("LIVE RULES (OR break)", {}, brake=2); out["daily brake: 2 losses"] = r
    print(line("daily brake: 2 losses", r))

    print("\n VERDICT — kept only if better than LIVE RULES both in-sample and out-of-sample")
    b = out["LIVE RULES (OR break)"]
    for name, r in out.items():
        if name.startswith("LIVE"):
            continue
        di = r["is"]["total"] - b["is"]["total"]; do = r["oos"]["total"] - b["oos"]["total"]
        dd = r["oos"]["dd"] - b["oos"]["dd"]
        keep = di > 0 and do > 0
        print(f"   {'KEEP ' if keep else 'drop '} {name:30s} in-sample {di:>+10,.0f}   out-of-sample {do:>+10,.0f}"
              f"   held-out drawdown {dd:>+9,.0f}")
    return out


if __name__ == "__main__":
    main()
