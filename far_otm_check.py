"""far_otm_check.py - real far-out-of-the-money option prices against high_win_study's model (22 Oct 2026). Read-only
Zerodha history of the live expiries' far strikes (1.5-7% from the price), 15-minute closes over the last trading days.
Run on the server with high_win_study.py beside it in /tmp/sc (results: far_otm_check_results.txt). It found the model
charging 2-3x too much at 2-3 expected moves on Nifty, Sensex and Bank Nifty - the source of high_win_study's REAL_CAL."""
import datetime as dt, math, os, sys, time
sys.path.insert(0, "/tmp/sc"); sys.path.insert(1, os.path.expanduser("~/nbs"))
import pandas as pd, numpy as np
import config, record_options as ro
import high_win_study as h
k = ro._kite()
dumps = {ex: k.instruments(ex) for ex in ("NSE", "BSE", "NFO", "BFO")}
today = dt.date.today()
frm = dt.datetime.combine(today - dt.timedelta(days=10), dt.time(9, 0)); to = dt.datetime.combine(today, dt.time(15, 45))
def hist(tok, interval="15minute"):
    for a in range(3):
        try:
            r = pd.DataFrame(k.historical_data(tok, frm, to, interval)); time.sleep(0.36); return r
        except Exception as e:
            if "Too many" in str(e): time.sleep(2); continue
            return pd.DataFrame()
    return pd.DataFrame()
vixi = [i for i in dumps["NSE"] if i["tradingsymbol"] == "INDIA VIX"][0]
vd = pd.DataFrame(k.historical_data(vixi["instrument_token"], frm - dt.timedelta(days=5), to, "day"))
vd["d"] = pd.to_datetime(vd["date"]).dt.date
vclose = dict(zip(vd.d, vd.close))
rows = []
for key in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"):
    meta = config.INSTRUMENTS[key]; ex = meta["kite_exchange"]; dex = "NFO" if ex == "NSE" else "BFO"
    name = meta.get("nse_symbol") or key; step = meta["strike_step"]
    idx = [i for i in dumps[ex] if i["tradingsymbol"] == meta["kite_tradingsymbol"] and i["segment"] in ("INDICES", "BSE-INDICES")][0]
    sp = hist(idx["instrument_token"]); sp["ts"] = pd.to_datetime(sp["date"])
    opts = [i for i in dumps[dex] if i.get("name") == name and i["instrument_type"] in ("CE", "PE") and i["expiry"] >= today]
    exps = sorted({i["expiry"] for i in opts})[:2]
    S0 = float(sp["close"].iloc[-1])
    for e in exps:
        for i in opts:
            if i["expiry"] != e: continue
            K = i["strike"]; o = i["instrument_type"]
            if (o == "PE" and not (S0 * 0.93 <= K <= S0 * 0.985)) or (o == "CE" and not (S0 * 1.015 <= K <= S0 * 1.07)): continue
            if (K / step) % 2: continue                       # every other strike, to keep the requests down
            px = hist(i["instrument_token"])
            if px.empty: continue
            px["ts"] = pd.to_datetime(px["date"])
            for _, b in px.iterrows():
                t = b["ts"]
                if t.strftime("%H:%M") not in ("09:15", "11:45", "14:45"): continue    # bars closing 09:30, 12:00, 15:00
                d = t.date()
                prior = [x for x in sorted(vclose) if x < d]
                if not prior: continue
                vix = float(vclose[prior[-1]])
                s = sp[sp.ts == t]
                if s.empty: continue
                S = float(s["close"].iloc[0]); tc = t + pd.Timedelta(minutes=15)
                T = h.years_to(e, tc.tz_convert("Asia/Kolkata") if tc.tzinfo else tc.tz_localize("Asia/Kolkata"))
                model = h.price(S, K, T, vix, o, e == d, False)
                m = math.log(K / S) / (vix / 100 * math.sqrt(T))
                rows.append({"index": key, "day": str(d), "hm": tc.strftime("%H:%M"), "expiry": str(e), "dte": (e - d).days,
                             "opt": o, "K": K, "S": S, "m": m, "real": float(b["close"]), "model": model, "vol": float(b.get("volume", 0))})
A = pd.DataFrame(rows)
A.to_csv("/tmp/sc/far_otm.csv", index=False)
A["mb"] = A.m.abs().round()
A = A[(A.real >= 0.5) | (A.model >= 0.5)]
A["ratio"] = A.real / A.model.clip(lower=0.05)
print("prices compared:", len(A), "days", A.day.nunique(), "| real / model, median by distance (expected moves), side, and whether it was expiry day")
print(A.groupby([A.dte == 0, "opt", "mb"]).ratio.agg(["count", "median"]).round(2).unstack("mb").to_string())
print("\nby index (all distances 1.5-3.5 EM, not expiry day):")
B = A[(A.dte > 0) & A.m.abs().between(1.5, 3.5)]
print(B.groupby(["index", "opt"]).ratio.agg(["count", "median"]).round(2).to_string())
