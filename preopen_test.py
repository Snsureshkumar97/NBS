"""Pre-open auction bars must never reach an indicator."""
import sys, datetime as dt
sys.path.insert(0, "/tmp/tkstub"); sys.path.insert(0, "/home/claude/trading-tool")
import pandas as pd, numpy as np
import config, main as M, signal_engine as SE

# A normal session, plus one wild 09:00 pre-open auction bar per day.
rows, idx = [], []
rs = np.random.RandomState(4)
px = 24000.0
for d in range(8):
    day = dt.datetime(2026, 8, 10 + d, 9, 0)
    if day.weekday() >= 5:
        continue
    # the pre-open print: a 300-point spike on almost no volume
    idx.append(day)
    rows.append((px, px + 300, px - 40, px + 280, 120))
    t = day.replace(hour=9, minute=15)
    for _ in range(25):
        step = rs.randn() * 8
        o = px; px = px + step
        idx.append(t)
        rows.append((o, max(o, px) + 5, min(o, px) - 5, px, 90000))
        t += dt.timedelta(minutes=15)
df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"],
                  index=pd.DatetimeIndex(idx))

pre = df[(df.index.hour * 60 + df.index.minute) < 9 * 60 + 15]
print("bars total:", len(df), "| pre-open bars:", len(pre))
assert len(pre) == 6

notes = []
clean = M.drop_preopen(df, notes)
assert len(clean) == len(df) - len(pre), (len(clean), len(df))
assert not ((clean.index.hour * 60 + clean.index.minute) < 9 * 60 + 15).any()
print("after filter:", len(clean), "|", notes[0])

# the auction bar really does move the numbers it feeds
a = SE.compute_technical_signal(df)
b = SE.compute_technical_signal(clean)
print(f"\n           with auction bar   without")
for k in ("last_close", "last_atr", "adx", "rsi", "total_score"):
    va, vb = a.get(k), b.get(k)
    if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
        print(f"  {k:11s} {va:>12.2f} {vb:>10.2f}   {'DIFFERS' if abs(va-vb) > 0.01 else 'same'}")
assert abs(a["adx"] - b["adx"]) > 1.0, "the auction bar was not actually distorting anything"
print(f"\n-> ADX reads {a['adx']:.1f} with the auction bars and {b['adx']:.1f} without.")
print("   ADX is the gate that decides a trend is strong enough to trade — the")
print("   auction spike was walking straight through it every morning.")

# and it is wired into the real fetch path
class P:
    def get_ohlc(self, *a, **k): return df
    def get_option_chain(self, *a, **k): raise RuntimeError("no chain")
config.REQUIRE_REACHABILITY = False       # so we reach the end of the path
rec, notes2 = M.fetch_recommendation(P(), "NIFTY", "15m", None, quiet=False)
assert any("pre-open" in n for n in notes2), notes2
assert not ((rec["candles"].index.hour * 60 + rec["candles"].index.minute)
            < 9 * 60 + 15).any(), "pre-open bars reached the recommendation"
config.REQUIRE_REACHABILITY = True
print("-> fetch_recommendation drops them and says so:", [n for n in notes2 if "pre-open" in n][0])

# switching it off restores the old behaviour
config.DROP_PREOPEN_CANDLES = False
assert len(M.drop_preopen(df, [])) == len(df)
config.DROP_PREOPEN_CANDLES = True
print("-> DROP_PREOPEN_CANDLES = False restores the old behaviour")

print("\nPRE-OPEN TEST PASSED")
