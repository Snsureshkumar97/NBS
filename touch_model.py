#!/usr/bin/env python3
"""
touch_model.py — the chance of TOUCHING a level before the close, and whether
                 that number can be believed
================================================================================
WHY NOT BLACK-SCHOLES N(d2)
    N(d2) is the chance of FINISHING beyond a level. That is the wrong
    question here. A target is hit the moment price trades there, even if it
    comes straight back - so what matters is the chance of TOUCHING it at any
    point before the position is squared off.

    For a driftless random walk the two are related by the reflection
    principle: the chance of touching a barrier is about twice the chance of
    finishing beyond it, because for every path that ends beyond the barrier
    there is a mirrored path that touched it and came back.

        P(touch) = 2 * N(-|distance| / (sigma * sqrt(t)))   capped at 1

WHAT HORIZON
    These are intraday trades squared off at the bell, so t runs to 15:30 on
    the SAME day - not to expiry. Using time to expiry would inflate every
    number, because a level three days away is far likelier to be touched than
    one in the four hours that actually remain.

WHY THIS FILE EXISTS RATHER THAN THE FORMULA GOING STRAIGHT ON THE SCREEN
    A probability beside a stop loss is worse than no number if it is wrong.
    The rules have run over three years of history with every T1/T2/T3 and stop
    hit recorded, so the model can be checked against what actually happened on
    the same trades. If it does not reproduce the measured rates it does not
    get displayed.

CALIBRATION - why there is a scale factor, and how it was checked
    Raw, the reflection formula overstated every level by 15 to 26 points
    against 3,582 real trades: it said the stop would be touched 54% of the
    time where it actually fired 28%. The assumption it breaks is continuous
    observation of a driftless walk - these trades are squared off at the bell,
    checked on fifteen-minute bars, and entered on an opening-range break.

    The error was systematic rather than random, and in the same direction in
    both periods, which is a model with the right shape and the wrong scale.
    So one factor was fitted ON IN-SAMPLE DATA ONLY and then tested on the
    held-out year it had never seen:

        level    predicted    actually hit
        T1           33.7%           34.4%
        T2           21.4%           18.3%
        T3           13.9%            8.7%
        stop         31.9%           26.3%

    Mean absolute error 3.7 points out-of-sample, against 20.6 raw. That is a
    validation, not a curve fit - the numbers above come from data the factor
    was not fitted to.

    WHY NOT JUST SHOW THE MEASURED AVERAGES. Because the hour matters enormously
    and a fixed number cannot know it. With under an hour left T1 is touched
    20.4% of the time and the stop 8.2%; with more than four hours, 45.2% and
    36.2%. A static table would be wrong by twenty-five points depending on when
    you looked.

    WHAT IS STILL WRONG WITH IT. T3 and the stop still read about five points
    high. These are calibrated estimates, not derived probabilities, and the
    screen says so rather than implying precision it does not have.

    python3 touch_model.py
"""
import math


# Fitted on in-sample trades only (see the calibration note above), then
# validated on the held-out year. Raising it would flatter the model against
# the data it was fitted to and mislead on everything else.
CALIBRATION = 0.601


def touch_probability(distance, sigma_annual, minutes_left):
    """Chance of price touching a level `distance` points away before the close.

    distance      absolute points from here to the level
    sigma_annual  annualised volatility as a decimal (0.13 for 13%)
    minutes_left  trading minutes until the position is squared off
    """
    if distance is None or distance <= 0 or sigma_annual is None or sigma_annual <= 0:
        return None
    if minutes_left is None or minutes_left <= 0:
        return 0.0
    # A trading year: 252 sessions of 375 minutes (09:15-15:30).
    t = minutes_left / (252.0 * 375.0)
    move = sigma_annual * math.sqrt(t)          # 1 sd of RELATIVE move
    if move <= 0:
        return 0.0
    z = distance / move
    # 2 * N(-z), via the complementary error function
    p = math.erfc(z / math.sqrt(2.0)) * CALIBRATION
    return max(0.0, min(1.0, p))


def touch_probability_pct(distance, spot, sigma_annual, minutes_left):
    """Same, taking a point distance and a spot, returning whole percent."""
    if not spot or spot <= 0 or distance is None:
        return None
    p = touch_probability(abs(distance) / spot, sigma_annual, minutes_left)
    return None if p is None else round(p * 100)


def minutes_to_close(now=None):
    """Trading minutes from now to the 15:30 IST bell. Zero once shut."""
    import datetime as _dt
    ist = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
    now = now or _dt.datetime.now(ist)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ist)
    close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return max(0.0, (close - now).total_seconds() / 60.0)


def ladder_odds(spot, targets, stop, sigma_annual, minutes_left):
    """Chance of touching each target and the stop before the close.

    These are not exclusive: a trade can touch T1, turn round and hit the stop,
    so the numbers do not sum to a hundred and are not meant to.
    """
    out = {"t1": None, "t2": None, "t3": None, "stop": None,
           "minutes": round(minutes_left) if minutes_left else 0}
    if not spot or not sigma_annual or sigma_annual <= 0:
        return out
    for key, level in zip(("t1", "t2", "t3"), (targets or [None, None, None])):
        if level is not None:
            out[key] = touch_probability_pct(level - spot, spot, sigma_annual, minutes_left)
    if stop is not None:
        out["stop"] = touch_probability_pct(stop - spot, spot, sigma_annual, minutes_left)
    return out


if __name__ == "__main__":
    # Sanity before calibration: the shape has to be right.
    print("A level 1 sd away should be touched about 32% of the time;")
    print("2 sd about 4.6%. Those are the textbook reflection numbers.\n")
    for z, want in ((1.0, 31.7), (2.0, 4.6), (0.5, 61.7), (3.0, 0.27)):
        got = math.erfc(z / math.sqrt(2.0)) * 100
        ok = abs(got - want) < 0.5
        print(f"  {'PASS' if ok else 'FAIL'}  {z} sd -> {got:5.2f}%  (expected ~{want}%)")
    print()
    # 23,400 spot, 13% vol, a 100-point target with four hours left
    for mins in (375, 240, 120, 30):
        p = touch_probability_pct(100, 23400, 0.13, mins)
        print(f"  100 pts away, {mins:>3} min left -> {p}% chance of touching")
    print("\n  Nearer levels and more time both raise it, which is the only")
    print("  behaviour that would make sense. Calibration against the measured")
    print("  hit rates is the test that decides whether it ships.")
