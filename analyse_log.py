#!/usr/bin/env python3
"""
analyse_log.py — what the trade log actually says

    python3 analyse_log.py                 # ~/trading-tool-logs/trades.csv
    python3 analyse_log.py path/to/trades.csv

Everything here is measured from CLOSED trades only. An OPEN row with no
matching CLOSE is a position the tool never finished tracking, and counting it
as anything would be inventing a result.
"""
import collections
import csv
import sys
import trade_log as _tl


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bucket(status):
    s = (status or "").lower()
    if "signal changed" in s:
        return "closed early (signal flipped)"
    if _tl.is_target_close(s):
        return "target reached"
    if "stop-loss" in s:
        return "stopped out"
    if "cleared" in s:
        return "cleared by hand"
    if "restart" in s or "market closed" in s:
        return "closed by the tool"
    return "other"


def r_of(row):
    """Result in R — how many times the risk was made or lost.

    Rupees alone cannot be compared across trades: a 30-lot BANKNIFTY and a
    20-lot SENSEX risk different amounts, so +2,000 from one is not the same
    achievement as +2,000 from the other. R divides that out.
    """
    entry, stop, exit_ = f(row.get("entry")), f(row.get("stop")), f(row.get("exit"))
    if None in (entry, stop, exit_) or entry == stop:
        return None
    risk = abs(entry - stop)
    return (exit_ - entry) / risk          # premium always rises when right


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        import trade_log
        path = trade_log._log_path()
    rows = list(csv.DictReader(open(path)))
    closes = [r for r in rows if r.get("event") == "CLOSE"]
    opens = [r for r in rows if r.get("event") == "OPEN"]
    if not closes:
        print("No closed trades in the log.")
        return 0

    days = sorted({r["date"] for r in rows})
    print(f"\n{len(closes)} closed trades over {len(days)} sessions "
          f"({days[0]} to {days[-1]})")
    unfinished = len(opens) - len(closes)
    if unfinished > 0:
        print(f"{unfinished} tickets were opened and never closed — excluded "
              f"from everything below.")

    # ---- how they ended -------------------------------------------------
    print("\nHOW THEY ENDED")
    counts = collections.Counter(bucket(r.get("status")) for r in closes)
    pnl = collections.defaultdict(float)
    for r in closes:
        pnl[bucket(r.get("status"))] += f(r.get("pnl")) or 0.0
    n = len(closes)
    print(f"  {'':34s}{'count':>6s}{'share':>8s}{'P&L':>12s}")
    for k, c in counts.most_common():
        print(f"  {k:34s}{c:6d}{100*c/n:7.0f}%{pnl[k]:12,.0f}")
    print(f"  {'TOTAL':34s}{n:6d}{'':8s}{sum(pnl.values()):12,.0f}")

    # ---- expectancy -----------------------------------------------------
    rs = [(r, r_of(r)) for r in closes]
    rs = [(r, x) for r, x in rs if x is not None]
    if rs:
        vals = [x for _, x in rs]
        wins = [x for x in vals if x > 0]
        print("\nEXPECTANCY  (R = multiples of the risk taken on that trade)")
        print(f"  trades scored        {len(vals)}")
        print(f"  win rate             {100*len(wins)/len(vals):.1f}%")
        print(f"  average              {sum(vals)/len(vals):+.3f}R")
        if wins:
            print(f"  average win          {sum(wins)/len(wins):+.2f}R")
        losses = [x for x in vals if x <= 0]
        if losses:
            print(f"  average loss         {sum(losses)/len(losses):+.2f}R")
        print(f"  total                {sum(vals):+.1f}R")
        avg = sum(vals) / len(vals)
        print(f"\n  At 0.10R round-trip costs: {avg-0.10:+.3f}R per trade")
        print("  " + ("This clears its costs." if avg - 0.10 > 0.02 else
                      "This does NOT clear its costs." if avg - 0.10 < -0.02 else
                      "This is within noise of break-even."))

    # ---- where the targets actually got to ------------------------------
    print("\nHOW FAR THEY GOT")
    for key, label in (("t1_hit", "reached T1"), ("t2_hit", "reached T2"),
                       ("t3_hit", "reached T3"), ("sl_hit", "hit the stop")):
        hit = sum(1 for r in closes if str(r.get(key, "")).strip().lower()
                  in ("true", "1", "yes"))
        print(f"  {label:16s} {hit:4d}  {100*hit/n:5.1f}%")

    # ---- the slices that might hold an edge -----------------------------
    def slice_by(name, keyfn):
        groups = collections.defaultdict(list)
        for r, x in rs:
            k = keyfn(r)
            if k is not None:
                groups[k].append(x)
        if not groups:
            return
        print(f"\nBY {name.upper()}")
        for k in sorted(groups, key=lambda k: -len(groups[k])):
            v = groups[k]
            if len(v) < 3:
                continue
            w = 100 * len([x for x in v if x > 0]) / len(v)
            print(f"  {str(k):22s} {len(v):4d} trades   win {w:5.1f}%   "
                  f"avg {sum(v)/len(v):+.3f}R")

    slice_by("index", lambda r: r.get("index"))
    slice_by("hour", lambda r: (r.get("time_ist") or "")[:2] + ":00")
    slice_by("confidence", lambda r: r.get("confidence"))
    slice_by("reward:risk at entry",
             lambda r: (lambda v: None if v is None else
                        "under 1.0" if v < 1.0 else
                        "1.0 - 1.5" if v < 1.5 else "1.5 and above")(f(r.get("reward_risk"))))
    slice_by("ADX band",
             lambda r: (lambda v: None if v is None else
                        "under 20" if v < 20 else
                        "20 - 30" if v < 30 else "30 and above")(f(r.get("adx"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
