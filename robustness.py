"""
robustness.py — two checks every study's verdict now carries: walk-forward and a Monte Carlo drawdown
================================================================================
The user, 20 Oct 2026, after the backtesting-frameworks guide on skills.sh was read: "yes add those two checks". Until
now a change was KEPT on one rule: more profit than today's in BOTH the first two years and the held-out year. Two
blocks can hide a result that rests on one lucky stretch, and a single worst drop is one draw of luck. So:

  WALK-FORWARD  the three years cut into half-years (Jan-Jun, Jul-Dec). A change should beat today's in most of them,
                not just in two big blocks: STEADY needs at least 60% of the half-years both had trades in.
                walk_forward_pick() asks the harder question for a sweep: had the setting been re-chosen every
                half-year as whichever did best over the year before, would that have beaten keeping today's? If not,
                choosing a setting from history is not something to trust.
  MONTE CARLO   the trading DAYS' results (a day, not a trade: trades on one day move together) redrawn at random,
                with replacement, 5,000 times. The worst drop in 95 of 100 redraws is a realistic bad stretch -
                the one actual worst drop is a single draw. Also the chance the period ends in a loss: how sure the
                profit is.
A KEEP that is not STEADY, or whose 95% worst drop is more than 25% deeper than today's in either period, is printed
"KEEP, FRAGILE": it passed the two-period rule, but read it as weak. The two-period rule itself is unchanged.

stop_day_study.verdict() adds this line under every verdict; crypto studies call verdict_line() themselves.
"""
import numpy as np
import pandas as pd

SIMS = 5000
SEED = 7
STEADY_SHARE = 0.60
DD_WORSE = 1.25


def _ts(r):
    t = pd.Timestamp(r["when"])
    return t.tz_convert("Asia/Kolkata") if t.tzinfo is not None else t


def daily(rows, key="net"):
    """Each trading day's total result, in date order."""
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series([r[key] for r in rows], index=[_ts(r).date() for r in rows])
    return s.groupby(level=0).sum().sort_index()


def half_year(r):
    t = _ts(r)
    return f"{t.year}-H{1 if t.month <= 6 else 2}"


def by_half(rows, key="net"):
    """Total per half-year."""
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series([r[key] for r in rows], index=[half_year(r) for r in rows]).groupby(level=0).sum().sort_index()


def monte_carlo(rows, key="net", sims=SIMS, seed=SEED):
    """The days redrawn with replacement: {"dd_median", "dd_p95", "loss_chance", "days"} (dd as a positive number)."""
    d = daily(rows, key).to_numpy(float)
    if len(d) < 5:
        return {"dd_median": 0.0, "dd_p95": 0.0, "loss_chance": float("nan"), "days": len(d)}
    rng = np.random.default_rng(seed)
    draws = d[rng.integers(0, len(d), size=(sims, len(d)))]
    eq = np.cumsum(draws, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((sims, 1)), eq], axis=1), axis=1)[:, 1:]
    dd = (peak - eq).max(axis=1)
    return {"dd_median": float(np.median(dd)), "dd_p95": float(np.percentile(dd, 95)),
            "loss_chance": float((eq[:, -1] < 0).mean()), "days": len(d)}


def walk_forward(rows, base, key="net"):
    """Half-year by half-year against today's: {"wins", "of", "diffs": {half: diff}}. A half-year counts only when
    both had trades in it."""
    a, b = by_half(rows, key), by_half(base, key)
    halves = sorted(set(a.index) & set(b.index))
    diffs = {h: float(a.get(h, 0.0) - b.get(h, 0.0)) for h in halves}
    return {"wins": sum(1 for v in diffs.values() if v > 0), "of": len(halves), "diffs": diffs}


def walk_forward_pick(variants, key="net", train_halves=2):
    """Re-choose every half-year: the variant with the most profit over the previous `train_halves` half-years, held
    for the next. -> {"picked": total, "fixed": {name: total over the same half-years}, "choices": [(half, name)]}."""
    tables = {n: by_half(rs, key) for n, rs in variants.items()}
    halves = sorted(set().union(*[set(t.index) for t in tables.values()]))
    picked, choices = 0.0, []
    fixed = {n: 0.0 for n in variants}
    for k in range(train_halves, len(halves)):
        past, now = halves[k - train_halves:k], halves[k]
        best = max(variants, key=lambda n: sum(tables[n].get(h, 0.0) for h in past))
        choices.append((now, best))
        picked += tables[best].get(now, 0.0)
        for n in variants:
            fixed[n] += tables[n].get(now, 0.0)
    return {"picked": picked, "fixed": fixed, "choices": choices}


def verdict_line(rows, base, split, money="₹", key="net", passed=None):
    """One line under a verdict: walk-forward half-years and the Monte Carlo 95% worst drop, each period."""
    wf = walk_forward(rows, base, key)
    parts, worse = [], False
    for name, sel in (("first two years", lambda r: _ts(r) < split), ("held-out", lambda r: _ts(r) >= split)):
        mc_r = monte_carlo([r for r in rows if sel(r)], key)
        mc_b = monte_carlo([r for r in base if sel(r)], key)
        if mc_b["dd_p95"] > 0 and mc_r["dd_p95"] > DD_WORSE * mc_b["dd_p95"]:
            worse = True
        lc = mc_r["loss_chance"]
        parts.append(f"{name}: 95% worst drop {money}{mc_r['dd_p95']:,.0f} (today's {money}{mc_b['dd_p95']:,.0f}), "
                     f"chance of a loss {lc:.0%}" if lc == lc else f"{name}: too few days")
    steady = wf["of"] > 0 and wf["wins"] >= STEADY_SHARE * wf["of"]
    flag = ""
    if passed:
        flag = "  -> STEADY" if steady and not worse else "  -> KEEP, FRAGILE" + (
            " (wins too few half-years)" if not steady else "") + (" (deeper 95% drop)" if worse else "")
    return (f"        walk-forward: beats today's in {wf['wins']} of {wf['of']} half-years;  "
            + ";  ".join(parts) + flag)


def risk_line(rows, split, money="₹", key="net"):
    """A system on its own: the Monte Carlo 95% worst drop and the chance of a loss, each period and all of it."""
    out = []
    for name, sel in (("first two years", lambda r: _ts(r) < split), ("held-out", lambda r: _ts(r) >= split),
                      ("all three years", lambda r: True)):
        mc = monte_carlo([r for r in rows if sel(r)], key)
        lc = mc["loss_chance"]
        out.append(f"{name}: worst drop {money}{mc['dd_median']:,.0f} typical / {money}{mc['dd_p95']:,.0f} in 95 of "
                   f"100, chance of a loss {lc:.0%}" if lc == lc else f"{name}: too few days")
    return "        " + ";  ".join(out)
