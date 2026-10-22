"""ic0_real_check.py - the expiry-day iron condor on REAL recorded premiums, every expiry day since 10 Sep 2026, against
high_win_study's model for the same days (22 Oct 2026). Run on the server with high_win_study.py beside it in /tmp/sc
(results: ic0_real_check_results.txt): real won 18% / 36% (k 1.5 / 2.0) and lost Rs 19,950 / 13,321 over 11 expiry
days; the model, same days, lost Rs 5,879 / 1,602 - it is about Rs 1,000-2,000 a trade too kind, more than the
strategy's whole three-year average profit."""
import glob, math, os, sys
sys.path.insert(0, "/tmp/sc"); sys.path.insert(1, os.path.expanduser("~/nbs"))
import numpy as np, pandas as pd
import config, high_win_study as h
H = os.path.expanduser("~/trading-tool-logs/option_history")
files = sorted(glob.glob(os.path.join(H, "*.csv.gz")))
prev_vix = {}
data = {}
for p in files:
    day = os.path.basename(p)[:10]
    d = pd.read_csv(p, parse_dates=["ts"]); d["ts"] = d["ts"].dt.tz_localize(None)
    data[day] = d
days = sorted(data)
for n, day in enumerate(days):
    if n:
        v = data[days[n - 1]]; v = v[v.kind == "VIX"]
        if len(v): prev_vix[day] = float(v.sort_values("ts").close.iloc[-1])
SL = lambda p: max(p * h.SLIP_PCT, h.SLIP_MIN)
rows = []
for day in days:
    d = data[day]
    if day not in prev_vix: continue
    vix = prev_vix[day]
    for key in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"):
        o = d[(d.kind == "OPT") & (d["index"] == key) & (d.expiry == day)]
        if o.empty: continue                                        # not this index's expiry day
        idx = d[(d.kind == "IDX") & (d["index"] == key)].set_index("ts").sort_index()
        step = config.INSTRUMENTS[key]["strike_step"]; lot = config.INSTRUMENTS[key]["lot_size"]
        t_in = pd.Timestamp(f"{day} 09:30")
        S = float(idx[idx.index < t_in].close.iloc[-1])            # the 09:29 minute's close
        T = h.years_to(day, t_in.tz_localize("Asia/Kolkata"))
        rec = sorted(o.strike.unique())
        def px(k, opt, t):
            x = o[(o.strike == k) & (o.opt == opt) & (o.ts < t)]
            return float(x.sort_values("ts").close.iloc[-1]) if len(x) else None
        for kk in (1.5, 2.0):
            em = vix / 100 * math.sqrt(T)
            sp = math.floor(S * math.exp(-kk * em) / step) * step; sc = math.ceil(S * math.exp(kk * em) / step) * step
            w = max(step, round(0.01 * S / step) * step)
            lp = max([x for x in rec if x <= sp - w] or [None] if any(x <= sp - w for x in rec) else [min(rec)])
            lc = min([x for x in rec if x >= sc + w] or [None] if any(x >= sc + w for x in rec) else [max(rec)])
            legs = {"sp": (sp, "PE"), "sc": (sc, "CE"), "lp": (lp, "PE"), "lc": (lc, "CE")}
            p_in = {n_: px(K, op, t_in) for n_, (K, op) in legs.items()}
            if any(v is None for v in p_in.values()):
                continue
            credit = (p_in["sp"] - SL(p_in["sp"]) + p_in["sc"] - SL(p_in["sc"])) - (p_in["lp"] + SL(p_in["lp"]) + p_in["lc"] + SL(p_in["lc"]))
            # 15-minute bars of the index after entry
            bars = idx[(idx.index >= t_in) & (idx.index < pd.Timestamp(f"{day} 15:15"))].resample("15min", label="left", closed="left").agg({"high": "max", "low": "min", "close": "last"}).dropna()
            pay, sells_out, buys_out, orders = 0.0, 0.0, 0.0, 4
            open_ = {"put": True, "call": True}
            for t, b in bars.iterrows():
                tc = t + pd.Timedelta(minutes=15)
                for side, sn, ln, op in (("put", "sp", "lp", "PE"), ("call", "sc", "lc", "CE")):
                    if not open_[side]: continue
                    hit = b.low <= legs[sn][0] if side == "put" else b.high >= legs[sn][0]
                    if hit:
                        s_, l_ = px(legs[sn][0], op, tc), px(legs[ln][0], op, tc)
                        if s_ is None or l_ is None: continue
                        pay += (s_ + SL(s_)) - max(l_ - SL(l_), 0); buys_out += s_ + SL(s_); sells_out += max(l_ - SL(l_), 0); orders += 2
                        open_[side] = False
            t_out = pd.Timestamp(f"{day} 15:15")
            for side, sn, ln, op in (("put", "sp", "lp", "PE"), ("call", "sc", "lc", "CE")):
                if open_[side]:
                    s_, l_ = px(legs[sn][0], op, t_out), px(legs[ln][0], op, t_out)
                    s_ = s_ or 0.0; l_ = l_ or 0.0
                    if s_ > 0.05: pay += s_ + SL(s_); buys_out += s_ + SL(s_); orders += 1
                    if l_ > 0.05: pay -= max(l_ - SL(l_), 0); sells_out += max(l_ - SL(l_), 0); orders += 1
            sells_in = p_in["sp"] - SL(p_in["sp"]) + p_in["sc"] - SL(p_in["sc"]); buys_in = p_in["lp"] + SL(p_in["lp"]) + p_in["lc"] + SL(p_in["lc"])
            net = (credit - pay) * lot - h.charges(key, (sells_in + sells_out) * lot, (buys_in + buys_out) * lot, orders)
            # the model for the same day, entry and rule (index candles from the recording)
            df = idx.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"}).resample("15min", label="left", closed="left").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
            df.index = df.index.tz_localize("Asia/Kolkata")
            h.CAL = True
            m = h.condor(key, df, pd.Timestamp(day).date(), t_in.tz_localize("Asia/Kolkata"), S, vix, pd.Timestamp(day).date(), kk, False, intraday=True)
            rows.append({"day": day, "index": key, "k": kk, "real_credit": round(credit, 2), "real_net": round(net), "model_net": round(m[0]) if m else None,
                         "wing_note": "" if (lp == sp - w and lc == sc + w) else "narrower wing (strike not recorded)"})
R = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(R.to_string(index=False))
for kk in (1.5, 2.0):
    g = R[R.k == kk]
    print(f"\n k={kk}: {len(g)} expiry days | REAL won {(g.real_net > 0).mean():.0%} total ₹{g.real_net.sum():+,} | MODEL won {(g.model_net > 0).mean():.0%} total ₹{g.model_net.sum():+,}")
