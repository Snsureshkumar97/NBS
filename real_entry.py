"""The price a live order actually filled at, shown as the ticket's Entry.

The user (25 Sep 2026): "when its a live order let the tool give the entry ltp - whatever Zerodha or Delta have entered,
the same ltp should be shown in the tool". A ticket's entry is the premium the tool saw when it issued the ticket; a live
order is a limit order priced a little away from that and fills at whatever the exchange gives (Bitcoin on 25 Sep: signal
588.3 limit, filled 574.0). Until now the page showed the tool's own number in its Entry cell and worked the result off it,
while the broker's average price sat in a status line beside it - two entries for one trade.

Once an executor has a fill for the ticket's position, the ticket the PAGE is sent carries that price as `entry`, keeps the
tool's own as `entry_signal`, and its result is re-worked from the fill. Only the public copy changes: the ticket in the
book, its frozen levels (targets and stop are absolute prices), the trade log and the fills log are untouched, so nothing
that decides or records a trade reads a different number.
"""


def apply(executor, t):
    """Overlay the executor's fill onto a public open ticket `t` (a dict, changed in place). True when it did."""
    if executor is None or not t or not t.get("open") or t.get("tracked_on") != "premium" or not t.get("trade_id"):
        return False
    try:
        got = executor.real_entry(t["trade_id"])
    except Exception:
        return False
    if not got or t.get("entry") is None:
        return False
    fill, venue = got
    signal = t["entry"]
    t["entry_signal"] = signal
    t["entry"] = fill
    t["entry_real"] = True
    t["entry_venue"] = venue
    if t.get("pnl") is not None and t.get("lot_size"):
        t["pnl"] = round(t["pnl"] - (fill - signal) * t["lot_size"] * t.get("lots", 1), 2)
    return True
