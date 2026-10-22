"""smile_calib.py - the option smile against India VIX on the recorded chains (for high_win_study.py, 22 Oct 2026):
implied volatility / VIX by distance from the price in expected moves, ordinary days and expiry day. Run on the server
with gex_study.py beside it in /tmp/sc:  python3 smile_calib.py  (results: smile_calib_results.txt). Covers only the
recorded strikes, about 1-3% either side of the price - far_otm_check.py shows it over-prices strikes further out."""
import glob, math, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, "/tmp/sc")
import gex_study as g
H = os.path.expanduser("~/trading-tool-logs/option_history")
out = []
for path in sorted(glob.glob(os.path.join(H, "*.csv.gz"))):
    day = os.path.basename(path)[:10]
    d = pd.read_csv(path, parse_dates=["ts"]); d["ts"] = d["ts"].dt.tz_localize(None)
    vix = d[d.kind == "VIX"].set_index("ts")["close"].sort_index()
    for key in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"):
        idx = d[(d.kind == "IDX") & (d["index"] == key)].set_index("ts")["close"].sort_index()
        o = d[(d.kind == "OPT") & (d["index"] == key)]
        exps = sorted(e for e in o.expiry.dropna().unique() if e >= day)
        if idx.empty or not exps or vix.empty:
            continue
        for exp in exps[:2]:
            oo = o[o.expiry == exp]
            for hm in ("09:30", "12:00", "15:15"):
                t = pd.Timestamp(f"{day} {hm}")
                s = idx[idx.index <= t]; v = vix[vix.index <= t]
                if s.empty or v.empty:
                    continue
                S, V = float(s.iloc[-1]), float(v.iloc[-1]) / 100
                T = g.years_to(exp, t)
                last = oo[oo.ts <= t].sort_values("ts").groupby(["strike", "opt"])["close"].last()
                for (k, opt), px in last.items():
                    m = math.log(k / S) / (V * math.sqrt(T))
                    if abs(m) > 3.5:
                        continue
                    iv = g.iv(float(px), S, float(k), T, opt == "CE")
                    if iv:
                        out.append({"index": key, "day": day, "hm": hm, "dte_days": (pd.Timestamp(exp) - pd.Timestamp(day)).days,
                                    "opt": opt, "m": m, "ratio": iv / V})
A = pd.DataFrame(out)
A["mb"] = (A.m * 2).round() / 2
print("rows", len(A), "days", A.day.nunique())
for opt in ("PE", "CE"):
    sub = A[(A.opt == opt) & (((A.m <= 0.25) & (opt == "PE")) | ((A.m >= -0.25) & (opt == "CE")))]
    t = sub.groupby(["index", "mb"]).ratio.median().unstack("mb").round(2)
    print(f"\n{opt}: median IV / VIX by moneyness (expected-move units, - = below spot), OTM side")
    print(t.to_string())
sub = A[((A.opt == "PE") & (A.m <= 0)) | ((A.opt == "CE") & (A.m >= 0))]
sub["exp_day"] = sub.dte_days == 0
print("\nOTM median IV/VIX: expiry day vs other days")
print(sub.groupby(["exp_day", "opt", "mb"]).ratio.median().unstack("mb").round(2).to_string())
A.to_csv("/tmp/sc/smile.csv", index=False)
