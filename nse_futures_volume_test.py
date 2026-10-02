#!/usr/bin/env python3
"""nse_futures_volume.py's parsing and front-month selection - hand-traced,
fake CSV text only, no network. Both real column layouts (confirmed by
actually downloading and inspecting a day from each archive, 2 Oct 2026)
are exercised verbatim, trimmed to a handful of rows each."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import nse_futures_volume as nfv

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


OLD_CSV = (
    "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,"
    "CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,\n"
    "FUTIDX,BANKNIFTY,25-Jan-2024,0,XX,48375,48489.95,47877.45,47946.65,47946.65,"
    "178046,1286113.08,2283615,231420,02-JAN-2024,\n"
    "FUTIDX,BANKNIFTY,29-Feb-2024,0,XX,48799.95,48819.95,48242.2,48306.2,48306.2,"
    "8244,59933.31,180825,8970,02-JAN-2024,\n"
    "FUTIDX,NIFTY,25-Jan-2024,0,XX,21700,21750,21650,21700,21700,"
    "60000,500000,9000000,100000,02-JAN-2024,\n"
    # an option row (same INSTRUMENT family prefix, must NOT be mistaken for the future)
    "OPTIDX,NIFTY,25-Jan-2024,21700,CE,100,110,95,105,105,"
    "50000,40000,800000,5000,02-JAN-2024,\n"
    # a stock future - must not be swept in just because INSTRUMENT is FUTIDX... wait,
    # stock futures are FUTSTK, not FUTIDX, but a wrong SYMBOL alone must also be excluded
    "FUTIDX,FINNIFTY,25-Jan-2024,0,XX,100,110,95,105,105,"
    "1000,900,20000,500,02-JAN-2024,\n"
)

NEW_CSV = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
    "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
    "2026-09-01,2026-09-01,FO,NSE,IDF,68407,,NIFTY,,2026-09-29,2026-09-29,,,"
    "NIFTY26SEPFUT,24200.00,24296.10,24064.00,24090.30,24081.00,24251.40,24055.80,"
    "24090.30,16051815,323310,57171,89746405775.00,39620,F1,65,,,,,\n"
    "2026-09-01,2026-09-01,FO,NSE,IDF,61471,,NIFTY,,2026-11-23,2026-11-23,,,"
    "NIFTY26NOVFUT,24447.70,24514.80,24287.60,24312.80,24314.10,24487.60,24055.80,"
    "24312.80,334100,52845,2193,3472863361.50,1524,F1,65,,,,,\n"
    # an index OPTION row (IDO, not IDF) - must not be swept in
    "2026-09-01,2026-09-01,FO,NSE,IDO,47298,,NIFTY,,2026-09-15,2026-09-15,23500.00,PE,"
    "NIFTY2691523500PE,27.40,45.10,20.10,39.40,39.45,26.80,24055.80,39.40,1498965,"
    "638300,35894,54903977888.50,19111,F1,65,,,,,\n"
)

print("1. OLD FORMAT: PICKS UP ONLY FUTIDX ROWS FOR NIFTY/BANKNIFTY, NOTHING ELSE")
rows = nfv._parse(OLD_CSV)
check("exactly 3 rows - 2 BANKNIFTY contracts + 1 NIFTY, the option/FINNIFTY rows excluded",
      len(rows) == 3, rows)
check("every row is NIFTY or BANKNIFTY", all(r["symbol"] in ("NIFTY", "BANKNIFTY") for r in rows))
nifty_row = next(r for r in rows if r["symbol"] == "NIFTY")
check("NIFTY's volume (CONTRACTS) and OI read correctly",
      nifty_row["volume"] == 60000.0 and nifty_row["oi"] == 9000000.0, nifty_row)
check("NIFTY's own OPEN/CLOSE read correctly - the future's day candle, not the index's",
      nifty_row["open"] == 21700.0 and nifty_row["close"] == 21700.0, nifty_row)
check("the expiry date parses to ISO form", nifty_row["expiry"] == "2024-01-25", nifty_row)

print("2. NEW FORMAT: PICKS UP ONLY IDF ROWS FOR NIFTY/BANKNIFTY, NOT IDO (OPTIONS)")
rows = nfv._parse(NEW_CSV)
check("exactly 2 rows - the two NIFTY futures contracts, the option row excluded",
      len(rows) == 2, rows)
sep_row = next(r for r in rows if r["expiry"] == "2026-09-29")
check("volume (TtlTradgVol) and OI (OpnIntrst) read correctly",
      sep_row["volume"] == 57171.0 and sep_row["oi"] == 16051815.0, sep_row)
check("OpnPric/ClsPric read correctly as this contract's own open/close",
      sep_row["open"] == 24200.00 and sep_row["close"] == 24090.30, sep_row)

print("3. front_month_series: PICKS THE NEAREST EXPIRY THAT HAS NOT YET PASSED, PER DAY")
raw = pd.DataFrame([
    {"date": "2024-01-02", "symbol": "NIFTY", "expiry": "2024-01-25", "volume": 60000.0, "oi": 9e6,
     "open": 21650.0, "close": 21700.0},
    {"date": "2024-01-02", "symbol": "NIFTY", "expiry": "2024-02-29", "volume": 5000.0, "oi": 1e6,
     "open": 21700.0, "close": 21750.0},
    {"date": "2024-01-26", "symbol": "NIFTY", "expiry": "2024-01-25", "volume": 1.0, "oi": 1.0,
     "open": 1.0, "close": 1.0},
    {"date": "2024-01-26", "symbol": "NIFTY", "expiry": "2024-02-29", "volume": 40000.0, "oi": 8e6,
     "open": 21800.0, "close": 21750.0},
])
fm = nfv.front_month_series(raw, "NIFTY")
check("2 Jan: the Jan-25 contract (soonest, not yet expired) is the front month",
      fm.loc[pd.Timestamp("2024-01-02"), "volume"] == 60000.0, fm)
check("...and its own open/close come along with it, not the other contract's",
      fm.loc[pd.Timestamp("2024-01-02"), "open"] == 21650.0, fm)
check("26 Jan: the Jan-25 contract has ALREADY expired by then and is excluded, "
      "even though a stray row for it exists that day - the Feb-29 contract is used instead",
      fm.loc[pd.Timestamp("2024-01-26"), "volume"] == 40000.0, fm)
check("only 2 days in the result - one row per day, not one per contract", len(fm) == 2, fm)

print("4. front_month_series: AN UNKNOWN SYMBOL RETURNS EMPTY, NOT AN ERROR")
fm = nfv.front_month_series(raw, "SENSEX")
check("empty, not a crash - SENSEX was never fetched from NSE at all", len(fm) == 0, fm)

print("NSE FUTURES VOLUME TEST PASSED" if not fails else f"NSE FUTURES VOLUME TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
