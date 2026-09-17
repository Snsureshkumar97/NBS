#!/usr/bin/env python3
"""RSI, MACD histogram and VWAP gap, added to the trade log 17 Sep 2026.

The trade log is append-only and lives forever in a user's home directory - a
new column has to land at the END of FIELDS (_upgrade_header only ever appends
to an old header, never reorders it) or every row written under the new schema
misaligns against a file still carrying the old header. This is the one file
where getting that wrong corrupts real trading history, not just a test run.
"""
import csv
import datetime as dt
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import trade_log

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


NOW = dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))


def trade(trade_id="T1", use_premium=True):
    return {"trade_id": trade_id, "index": "NIFTY", "strike": 23300, "option_type": "CE",
            "use_premium": use_premium, "entry_ltp": 130.35, "entry_spot": 23286.35,
            "premium_targets": [150.0, 170.0, 190.0], "premium_sl": 90.0,
            "index_targets": [23330.0, 23360.0, 23390.0], "index_sl": 23200.0,
            "lot_size": 75, "lots": 1.0, "cooldown_skipped": False,
            "hit": {"T1": True, "T2": True, "T3": False}, "sl_hit": False,
            "status": "CLOSED — T2 hit (full target reached)"}


def rec(rsi=58.3, macd_hist=1.7, vwap_gap=-4.2, adx=22.7, confidence="Medium"):
    return {"risk_points": 77.72, "reach_points": 112.0, "reach_to_risk": 1.44,
            "score": 3, "confidence": confidence, "strictness": "strict",
            "technical": {"adx": adx, "last_rsi": rsi, "macd_hist": macd_hist, "vwap_gap": vwap_gap}}


print("1. THE COLUMNS ARE APPENDED, NOT INSERTED")
i = trade_log.FIELDS.index("cooldown_skipped")
check("cooldown_skipped is still exactly where it always was",
      trade_log.FIELDS[:i + 1] == ["trade_id", "event", "date", "time_ist", "index", "strike",
                                   "option_type", "entry", "exit", "t1", "t2", "t3", "stop",
                                   "t1_hit", "t2_hit", "t3_hit", "sl_hit", "status", "pnl",
                                   "lot_size", "lots", "tracked_on", "entry_spot", "risk_points",
                                   "reach_points", "reward_risk", "score", "confidence", "adx",
                                   "strictness", "cooldown_skipped"])
check("the three new columns come strictly after it, in this order",
      trade_log.FIELDS[i + 1:] == ["rsi", "macd_hist", "vwap_gap"])

print("2. A FRESH ROW CARRIES THE READING AT THAT CALL")
row = trade_log._base_row(trade(), rec(), NOW)
check("rsi is rounded from last_rsi, the same way the live card rounds it", row["rsi"] == 58.3)
check("macd_hist and vwap_gap pass through as already rounded", row["macd_hist"] == 1.7 and row["vwap_gap"] == -4.2)
check("adx is unaffected by the refactor that introduced the shared tech lookup", row["adx"] == 22.7)

print("3. NO rec, NO technical, NO CRASH")
row_none = trade_log._base_row(trade(), None, NOW)
check("a None rec gives None for all three, not an exception",
      row_none["rsi"] is None and row_none["macd_hist"] is None and row_none["vwap_gap"] is None)
row_empty = trade_log._base_row(trade(), {"confidence": "Low"}, NOW)
check("a rec with no technical key at all does the same",
      row_empty["rsi"] is None and row_empty["macd_hist"] is None and row_empty["vwap_gap"] is None)

print("4. THE ACTUAL WRITE PATH: OPEN, THEN CLOSE, THEN READ BACK")
path = os.path.join(tempfile.mkdtemp(), "trades.csv")
trade_log.log_open(trade(), rec(rsi=58.3, macd_hist=1.7, vwap_gap=-4.2, adx=22.7, confidence="Medium"), NOW, path=path)
close_time = NOW + dt.timedelta(hours=2, minutes=23)
t = trade()
trade_log.log_close(t, rec(rsi=71.2, macd_hist=2.9, vwap_gap=8.0, adx=25.6, confidence="High"),
                    close_time, 171.0, 3048.75, path=path)
rows = trade_log._read_rows(path)
check("two rows written", len(rows) == 2, rows)
opened, closed = rows[0], rows[1]
check("the OPEN row keeps the entry-time reading", opened["rsi"] == "58.3" and opened["adx"] == "22.7")
check("the CLOSE row keeps its OWN (close-time) reading, same as adx already did",
      closed["rsi"] == "71.2" and closed["adx"] == "25.6")
check("nothing after the new columns got shifted - pnl still reads as the real number",
      float(closed["pnl"]) == 3048.75)

print("5. AN OLD-FORMAT FILE UPGRADES CLEANLY - NOTHING SHIFTS, NOTHING IS LOST")
old_fields = trade_log.FIELDS[:trade_log.FIELDS.index("cooldown_skipped") + 1]   # the pre-17-Sep header
old_path = os.path.join(tempfile.mkdtemp(), "trades.csv")
with open(old_path, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=old_fields)
    w.writeheader()
    w.writerow({k: "" for k in old_fields} | {
        "trade_id": "OLD-1", "event": "OPEN", "date": "2026-09-10", "time_ist": "09:15:00",
        "index": "NIFTY", "strike": "23200", "option_type": "PE", "entry": "111.1",
        "adx": "32.3", "confidence": "High"})
    w.writerow({k: "" for k in old_fields} | {
        "trade_id": "OLD-1", "event": "CLOSE", "date": "2026-09-10", "time_ist": "11:10:00",
        "index": "NIFTY", "strike": "23200", "option_type": "PE", "entry": "111.1",
        "status": "CLOSED — the tool stopped while this was open", "adx": "32.3", "confidence": "High"})
before = trade_log._read_rows(old_path)
check("before touching it, the old file has none of the new columns at all",
      "rsi" not in before[0], list(before[0]))

trade_log.log_open(trade("NEW-1"), rec(rsi=44.0, macd_hist=-0.5, vwap_gap=2.0), NOW, path=old_path)
after = trade_log._read_rows(old_path)
check("still three rows, in order", len(after) == 3 and [r["trade_id"] for r in after] == ["OLD-1", "OLD-1", "NEW-1"])
check("the old rows are untouched - same index, entry, adx, confidence as before",
      after[0]["index"] == "NIFTY" and after[0]["entry"] == "111.1" and after[0]["adx"] == "32.3"
      and after[0]["confidence"] == "High")
check("an old row simply has nothing for the new columns - blank, not misaligned data",
      after[0].get("rsi", "") == "" and after[1].get("macd_hist", "") == "")
check("the new row's own reading lands in the right column, not shifted into an old one",
      after[2]["rsi"] == "44.0" and after[2]["macd_hist"] == "-0.5" and after[2]["index"] == "NIFTY")
check("critically: nothing after adx/confidence in the OLD rows got pushed into the new columns",
      after[0]["strictness"] == "" and after[1]["strictness"] == "")

print("TRADE LOG ENTRY SIGNAL TEST PASSED" if not fails else f"TRADE LOG ENTRY SIGNAL TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
