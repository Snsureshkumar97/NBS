#!/usr/bin/env python3
"""
live_vs_backtest.py — the TradingView rating's live (paper) trades against its own honest backtest, trade by trade
================================================================================
The user, 28 Oct 2026: "do the live vs backtest comparison now" - over 13-28 Oct the rating's live tickets lost
-20,461 a lot (25 trades, Nifty and Bank Nifty) while honest_retest.py's backtest of the same days made money.

Every figure GROSS per lot (no charges, no slippage) - the journal books a paper trade that way - three ways:
  LIVE          the journal's own P&L (entry and exit premiums as the ticket booked them)
  BT REAL       the backtest's trade priced with the RECORDED option prices (record_options.py's minute candles): the
                at-the-money strike of the nearest expiry at the entry minute, sold at the minute the index reached the
                backtest's exit level (its stop / target: the first minute of the exit candle that touched it; a
                close-type exit: the candle's last minute)
  BT MODEL      the same trade as the backtest prices it (Black-Scholes on the day's sigma, pro_study's expiry)
Signals are matched on index, side and the 15-minute candle the ticket entered after. LIVE - BT REAL on matched
trades is execution (the ticket's premium-tracked stop and targets, its timing); BT REAL - BT MODEL is the pricing
model; unmatched trades are trade selection.

    python3 live_vs_backtest.py LIVE_CSV OPTION_HISTORY_DIR [UNTIL]     (HOME at the six-year history)
    python3 live_vs_backtest.py model OPTION_HISTORY_DIR SINCE UNTIL  (every backtest trade the recorder can price)
LIVE_CSV: the OPEN / CLOSE rows of every account's trades.csv, one per (trade_id, system, event).
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
IST = "Asia/Kolkata"
KEYS = ("NIFTY", "BANKNIFTY")
SINCE = pd.Timestamp("2026-10-13", tz=IST)


def load_live(path, until):
    d = pd.read_csv(path)
    d = d[(d["system"] == "tv_rating") & d["index"].isin(KEYS)]
    o = d[d["event"] == "OPEN"].drop_duplicates("trade_id").set_index("trade_id")
    c = d[d["event"] == "CLOSE"].drop_duplicates("trade_id").set_index("trade_id")
    rows = []
    for tid in o.index.intersection(c.index):
        a, b = o.loc[tid], c.loc[tid]
        t_open = pd.Timestamp(f"{a['date']} {a['time_ist']}", tz=IST)
        if not (SINCE <= t_open < until):
            continue
        lots = float(b["lots"] or 1) or 1
        rows.append({"id": tid, "index": a["index"], "side": a["option_type"], "t_open": t_open,
                     "t_close": pd.Timestamp(f"{b['date']} {b['time_ist']}", tz=IST), "strike": float(a["strike"]),
                     "entry": float(a["entry"]), "exit": float(b["exit"]), "status": str(b["status"]),
                     "gross": float(b["pnl"]) / lots, "lot_size": float(b["lot_size"]),
                     "candle": t_open.floor("15min")})
    return rows


class Prices:
    """The recorded minute candles: options by (index, expiry, strike, CE/PE), and the index itself."""
    def __init__(self, folder):
        self.folder, self.days = folder, {}

    def day(self, d):
        if d not in self.days:
            p = os.path.join(self.folder, f"{d}.csv.gz")
            if not os.path.exists(p):
                self.days[d] = None
            else:
                x = pd.read_csv(p)
                ts = pd.to_datetime(x["ts"])                 # files before mid-October carry no time zone
                x["ts"] = ts.dt.tz_localize(IST) if ts.dt.tz is None else ts.dt.tz_convert(IST)
                self.days[d] = x
        return self.days[d]

    def option(self, k, d, expiry, strike, opt):
        x = self.day(d)
        if x is None:
            return None
        s = x[(x["kind"] == "OPT") & (x["index"] == k) & (x["expiry"] == expiry) & (x["strike"] == strike)
              & (x["opt"] == opt)]
        return s.set_index("ts").sort_index() if len(s) else None

    def expiries(self, k, d):
        x = self.day(d)
        return [] if x is None else sorted(x[(x["kind"] == "OPT") & (x["index"] == k)]["expiry"].dropna().unique())

    def index(self, k, d):
        x = self.day(d)
        return None if x is None else x[(x["kind"] == "IDX") & (x["index"] == k)].set_index("ts").sort_index()


def at_open(s, t):
    """The option's price at minute t (that minute's open; else the last close before it)."""
    if s is None:
        return None
    if t in s.index:
        return float(s.loc[t, "open"])
    before = s[s.index < t]
    return float(before["close"].iloc[-1]) if len(before) else None


def at_close(s, t):
    if s is None:
        return None
    upto = s[s.index <= t]
    return float(upto["close"].iloc[-1]) if len(upto) else None


def bt_real(row, P):
    """The backtest trade priced with recorded option prices. -> (gross per lot, expiry, strike) or None."""
    import config
    k, d = row["index"], row["when"].date().isoformat()
    meta = config.INSTRUMENTS[k]
    K = round(row["entry_spot"] / meta["strike_step"]) * meta["strike_step"]
    exps = [e for e in P.expiries(k, d) if e >= d]
    if not exps:
        return None
    opt = P.option(k, d, exps[0], K, row["side"])
    p0 = at_open(opt, row["when"])
    if p0 is None:
        return None
    ce = row["side"] == "CE"
    t_out, px, via = row["exit_time"], row["exit_spot"], row["closed_via"]
    exit_t = t_out - pd.Timedelta(minutes=1)
    if via in ("stop", "target"):
        idx = P.index(k, d)
        win = idx[(idx.index >= t_out - pd.Timedelta(minutes=15)) & (idx.index < t_out)] if idx is not None else []
        down = (via == "stop") == ce                       # the level lies below: a call's stop, a put's target
        hit = win[(win["low"] <= px)] if down else win[(win["high"] >= px)] if len(win) else win
        if len(hit):
            exit_t = hit.index[0]
    p1 = at_close(opt, exit_t)
    if p1 is None:
        return None
    return (p1 - p0) * meta["lot_size"], exps[0], K


def bt_model(row, expiry):
    import config
    import pro_study as ps
    import regime_study as rs
    k = row["index"]
    meta = config.INSTRUMENTS[k]
    K = round(row["entry_spot"] / meta["strike_step"]) * meta["strike_step"]
    exp = pd.Timestamp(expiry).date()
    call = row["side"] == "CE"
    p0 = rs.bs(row["entry_spot"], K, ps.years_to(exp, row["when"]), row["sigma"], call)
    p1 = rs.bs(row["exit_spot"], K, ps.years_to(exp, row["exit_time"]), row["sigma"], call)
    return (p1 - p0) * meta["lot_size"]


def _tv(k):
    import honest_retest as h
    return k, h._job((k, "tv", None))[1]["TV_RATING"]


def main():
    import multiprocessing as mp
    live_csv, folder = sys.argv[1], sys.argv[2]
    until = pd.Timestamp(sys.argv[3] if len(sys.argv) > 3 else "2026-10-27", tz=IST)
    P = Prices(folder)
    live = load_live(live_csv, until)
    with mp.get_context("spawn").Pool(processes=2) as pool:
        bt = {k: rows for k, rows in pool.map(_tv, KEYS)}
    bts = [r for k in KEYS for r in bt[k] if SINCE <= pd.Timestamp(r["when"]) < until]
    for r in bts:
        real = bt_real(r, P)
        r["real"], r["expiry"] = (real[0], real[1]) if real else (None, None)
        r["model"] = bt_model(r, r["expiry"]) if r["expiry"] else None
    by_key = {(r["index"], r["side"], pd.Timestamp(r["when"])): r for r in bts}
    pairs, live_only = [], []
    for L in live:
        b = by_key.pop((L["index"], L["side"], L["candle"]), None)
        (pairs.append((L, b)) if b else live_only.append(L))
    bt_only = list(by_key.values())

    f = lambda v: "      -" if v is None else f"{v:+8,.0f}"
    print(f"TradingView rating, {SINCE.date()} to {(until - pd.Timedelta(days=1)).date()}, Nifty + Bank Nifty, GROSS per lot")
    print(f"{'':9s} {'index':10s} {'side':4s} {'entered':16s} | {'LIVE out':8s} {'how':28s} {'LIVE':>8s} | "
          f"{'BT out':6s} {'how':16s} {'REAL':>8s} {'MODEL':>8s}")
    for L, b in sorted(pairs, key=lambda x: x[0]["t_open"]):
        print(f"{'matched':9s} {L['index']:10s} {L['side']:4s} {L['t_open'].strftime('%d %b %H:%M'):16s} | "
              f"{L['t_close'].strftime('%H:%M'):8s} {L['status'].replace('CLOSED — ', '')[:28]:28s} {f(L['gross'])} | "
              f"{pd.Timestamp(b['exit_time']).strftime('%H:%M'):6s} {b['closed_via']:16s} {f(b['real'])} {f(b['model'])}")
    for L in sorted(live_only, key=lambda x: x["t_open"]):
        print(f"{'live only':9s} {L['index']:10s} {L['side']:4s} {L['t_open'].strftime('%d %b %H:%M'):16s} | "
              f"{L['t_close'].strftime('%H:%M'):8s} {L['status'].replace('CLOSED — ', '')[:28]:28s} {f(L['gross'])} |")
    for b in sorted(bt_only, key=lambda x: pd.Timestamp(x["when"])):
        print(f"{'bt only':9s} {b['index']:10s} {b['side']:4s} {pd.Timestamp(b['when']).strftime('%d %b %H:%M'):16s} | "
              f"{'':8s} {'':28s} {'':>8s} | {pd.Timestamp(b['exit_time']).strftime('%H:%M'):6s} "
              f"{b['closed_via']:16s} {f(b['real'])} {f(b['model'])}")
    s = lambda xs: sum(x for x in xs if x is not None)
    print()
    print(f"matched   {len(pairs):3d}:  LIVE {s(L['gross'] for L, _ in pairs):+10,.0f}   BT REAL "
          f"{s(b['real'] for _, b in pairs):+10,.0f}   BT MODEL {s(b['model'] for _, b in pairs):+10,.0f}")
    print(f"live only {len(live_only):3d}:  LIVE {s(L['gross'] for L in live_only):+10,.0f}")
    print(f"bt only   {len(bt_only):3d}:                    BT REAL {s(b['real'] for b in bt_only):+10,.0f}   "
          f"BT MODEL {s(b['model'] for b in bt_only):+10,.0f}")
    print(f"ALL:          LIVE {s(L['gross'] for L in live):+10,.0f}   BT REAL {s(b['real'] for b in bts):+10,.0f}   "
          f"BT MODEL {s(b['model'] for b in bts):+10,.0f}   (backtest trades with no recorded price: "
          f"{sum(b['real'] is None for b in bts)})")


def _tv4(k):
    import honest_retest as h
    return k, h._job((k, "tv", None))[1]["TV_RATING"]


def model_check():
    """Every TradingView-rating backtest trade on the days the recorder has, all four indices: real prices vs the
    model, gross per lot. python3 live_vs_backtest.py model OPTION_HISTORY_DIR SINCE UNTIL"""
    import multiprocessing as mp
    import numpy as np
    folder = sys.argv[2]
    since, until = (pd.Timestamp(x, tz=IST) for x in sys.argv[3:5])
    P = Prices(folder)
    keys = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
    with mp.get_context("spawn").Pool(processes=4) as pool:
        bt = dict(pool.map(_tv4, keys))
    print(f"TradingView rating backtest trades {since.date()} to {(until - pd.Timedelta(days=1)).date()}: "
          f"GROSS per lot, real recorded prices vs the model")
    print(f"{'index':11s} {'trades':>6s} {'priced':>6s} {'REAL':>10s} {'MODEL':>10s} {'diff/trade':>10s} {'se':>6s}")
    allr = []
    for k in keys + ("ALL",):
        if k == "ALL":
            rows = allr
        else:
            rows = [r for r in bt[k] if since <= pd.Timestamp(r["when"]) < until]
            for r in rows:
                real = bt_real(r, P)
                r["real"], r["expiry"] = (real[0], real[1]) if real else (None, None)
                r["model"] = bt_model(r, r["expiry"]) if r["expiry"] else None
            allr += rows
        ok = [r for r in rows if r["real"] is not None]
        d = np.array([r["real"] - r["model"] for r in ok]) if ok else np.array([0.0])
        print(f"{k:11s} {len(rows):6d} {len(ok):6d} {sum(r['real'] for r in ok):+10,.0f} "
              f"{sum(r['model'] for r in ok):+10,.0f} {d.mean():+10,.0f} {d.std() / max(len(d), 1) ** 0.5:6,.0f}")
    print("by how the trade ended (all four):")
    for via in sorted({r["closed_via"] for r in allr}):
        ok = [r for r in allr if r["closed_via"] == via and r["real"] is not None]
        if ok:
            print(f"   {via:16s} {len(ok):4d}  REAL {sum(r['real'] for r in ok):+9,.0f}  MODEL "
                  f"{sum(r['model'] for r in ok):+9,.0f}  diff/trade {np.mean([r['real'] - r['model'] for r in ok]):+6,.0f}")


if __name__ == "__main__":
    model_check() if sys.argv[1:2] == ["model"] else main()
