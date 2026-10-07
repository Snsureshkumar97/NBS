#!/usr/bin/env python3
"""
loss_limit_study.py — the Indian-index daily loss limit on 3 years (7 Oct 2026)
================================================================================
The user, after 7 Oct's three stops tripped their limit at 10:18 (Rs 2,00,000 at 2% = Rs 12,000, 3 lots = Rs 4,000 a
lot): "does indian market have loss limit remove it because if there is room no recover the loss it can". Removed
(config.DAILY_LOSS_LIMIT_OFF). This measures what the limit did: today's live trades (stop_day_study's replay), in
time order across the three indices; a new trade is skipped once the day's CLOSED trades are down the limit. A
post-filter on the finished list (a skipped trade does not free its index for a later one), per lot, after costs.

    python3 loss_limit_study.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import stop_day_study as sds
_, raw = sds._replay(("live",))
base = sds.per_index(raw)
def with_limit(rows, limit):
    """Today's trades in time order; a new trade is skipped once the day's CLOSED trades are down `limit` per lot."""
    kept, by_day = [], {}
    for r in sorted(rows, key=lambda r: r["when"]):
        d = r["when"].date()
        booked = sum(o["net"] for o in by_day.get(d, []) if o["exit_time"] <= r["when"])
        if booked <= -limit:
            continue
        kept.append(r)
        by_day.setdefault(d, []).append(r)
    return kept
print("THE DAILY LOSS LIMIT ON 3 YEARS (per lot; your account's Rs 12,000 at 3 lots = Rs 4,000 a lot)")
print(sds.line("no limit", base))
for lim in (2000, 4000, 6000, 8000):
    rows = with_limit(base, lim)
    print(sds.line(f"limit Rs {lim:,} a lot" + ("  <- yours" if lim == 4000 else ""), rows))
    print(sds.verdict(f"limit Rs {lim:,} vs no limit", rows, base))
n_all = pd.Series([r["when"].date() for r in base]).value_counts()
n_kept = pd.Series([r["when"].date() for r in with_limit(base, 4000)]).value_counts().reindex(n_all.index, fill_value=0)
print(f"days the Rs 4,000 limit skipped a trade: {int((n_kept < n_all).sum())} of {len(n_all)} trading days")
