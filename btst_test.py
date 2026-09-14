"""BTST: the live reading must be the backtest's reading, bar for bar."""
import os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import btst

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

H = os.path.expanduser("~/trading-tool-logs/history")
for key in ("NIFTY", "SENSEX"):
    print(key)
    df = pd.read_csv(os.path.join(H, f"{key}_15m_3y.csv"), index_col=0, parse_dates=True)
    table = btst.day_table(df)
    daily = df.resample("1D").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    # use the full table's own complete sessions as the daily closes, as the study did
    daily = daily[[d.date() in set(table.index) for d in daily.index]]
    days = list(table.index[-60:-1])            # sixty recent closed sessions
    mismatch, sig_mismatch = 0, 0
    for d in days:
        cut = df[df.index.date <= d]
        row = btst.session_row(cut, daily[daily.index.date <= d])
        ref = table.loc[d]
        for f in ("close", "high", "low", "prev_high", "prev_low", "ret", "pos", "last_hour"):
            if abs(row[f] - ref[f]) > 1e-9:
                mismatch += 1
        if abs(row["ema20"] - ref["ema20"]) > 1e-6:
            mismatch += 1
        for rule in btst.RULES:
            if row["signals"][rule] != btst.signal(rule, ref):
                sig_mismatch += 1
        if not row["final"]:
            mismatch += 1
    check("every field identical to the backtest's row, 59 sessions", mismatch == 0, f"{mismatch} differences")
    check("every rule gives the backtest's signal", sig_mismatch == 0, f"{sig_mismatch} differences")

    d = days[-1]
    part = df[(df.index.date < d) | ((df.index.date == d) & (df.index.hour * 60 + df.index.minute <= 14 * 60 + 45))]
    row = btst.session_row(part, daily[daily.index.date < d])
    check("mid-afternoon: provisional, not final", row and row["final"] is False and row["as_of"] == "15:00", row and row["as_of"])
    part = df[(df.index.date < d) | ((df.index.date == d) & (df.index.hour * 60 + df.index.minute <= 13 * 60))]
    row = btst.session_row(part, None)
    check("before 14:30: no last-hour figure yet, no crash", row is not None and row["last_hour"] is None)
    check("empty input: None", btst.session_row(df.iloc[0:0]) is None)

    # a feed that adds a 15:30 bar for the closing print (Yahoo does)
    cut = df[df.index.date <= d]
    extra = cut.iloc[[-1]].copy()
    extra.index = extra.index + pd.Timedelta(minutes=15)
    extra[["Open", "High", "Low", "Close"]] = extra[["Close"]].values * [1, 1.01, 0.99, 1.005]
    row = btst.session_row(pd.concat([cut, extra]), daily[daily.index.date <= d])
    ref = table.loc[d]
    check("an extra 15:30 bar is ignored: still final, same close and range as the backtest",
          row["final"] and abs(row["close"] - ref["close"]) < 1e-9 and abs(row["high"] - ref["high"]) < 1e-9,
          (row["final"], row["as_of"], row["close"], ref["close"]))
    import datetime as _dt
    ist = cut.index[-1].tzinfo
    at_1520 = _dt.datetime(d.year, d.month, d.day, 15, 20, tzinfo=ist)
    at_1531 = _dt.datetime(d.year, d.month, d.day, 15, 31, tzinfo=ist)
    check("15:15 bar before 15:30: provisional", btst.session_row(cut, None, now=at_1520)["final"] is False)
    check("after 15:30: final", btst.session_row(cut, None, now=at_1531)["final"] is True)
    check("next morning, reading yesterday's close: final",
          btst.session_row(cut, None, now=at_1531 + _dt.timedelta(hours=17))["final"] is True)

print()
print("BTST TEST PASSED" if not fails else f"BTST TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
