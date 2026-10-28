"""
fills.py — what a stop actually fills at in a candle walk
================================================================================
Found 28 Oct 2026 (votes_alone_study.py controls): the walks filled every stop AT the stop's own price. With a stop
that MOVES - stepped to T1 after a candle touched it, trailed to a Supertrend line that jumps to the far side of the
price when it flips - the next candle often opens already past it, and was still sold at the stop. That is a free exit
at T1 for any trade whose wick reached it: a coin flip for the direction made +7.3M over six years on the rules' gates
and exits. A live stop fills at the price there is: the candle's open when it opens past the stop, the stop itself
when the price runs into it during the candle.
"""


def stop_fill(A, j, stop, ce):
    """The fill of a stop hit in candle j of the arrays A ("hi", "lo", "cl", optionally "op"): the stop, or the
    candle's open when the candle opens already past it. Without "op" the previous close stands in for the open (a
    15-minute candle opens where the last one closed, to within a tick or two inside the day)."""
    op = A["op"][j] if "op" in A else A["cl"][j - 1]
    return min(stop, op) if ce else max(stop, op)
