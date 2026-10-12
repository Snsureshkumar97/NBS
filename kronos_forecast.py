#!/usr/bin/env python3
"""
kronos_forecast.py — Kronos's next-hour forecast at every 15-minute candle, for kronos_study.py
================================================================================
The user, 12 Oct 2026: "yes test kronos on our indian data". Kronos (Shi et al., arXiv 2508.02739, AAAI 2026) is an
open-source foundation model for candlestick data. This runs the published Kronos-small model with its
Kronos-Tokenizer-base over each index's 3-year 15-minute history and writes what it forecast at every candle.

RUNS OUTSIDE THE TOOL, in its own environment (it needs PyTorch, which the tool does not): the Kronos model code
(github.com/shiyu-coder/Kronos, model/ only) and the weights (Hugging Face NeoQuasar/Kronos-small and
NeoQuasar/Kronos-Tokenizer-base, MIT) live in ~/kronos-test, never in this repo.

    ~/kronos-test/venv/bin/python -I kronos_forecast.py --index NIFTY

FIXED BEFORE ANY RESULT WAS SEEN (12 Oct 2026):
    context   the last 400 candles up to and INCLUDING candle i (closed) - nothing after it
    forecast  the next 4 candles (one hour); the candles' own future timestamps (the exchange's schedule, no price)
    sampling  temperature 1.0, top-p 0.9, 5 samples averaged (the model's own predict_batch), seed = candle number
    volume    none (an index has no volume of its own) - the model's own default, zeros
    candles   (decided 12 Oct 2026, for run time - ~2.9 candles/s on this Mac's GPU, ~1.9 on its CPU, so every candle of
              four indices would take most of a day) only the candles where the tool's engine signals and the live
              entry checks pass: ~6,200 per index, a third of all - every candle a trade could be taken on, with or
              without a Kronos filter (kronos_study.signal_bars writes them; --bars)
Output: ~/kronos-test/out/<INDEX>.csv, one row per candle i: its time, its close, and the forecast closes 1-4 candles
ahead. Re-running resumes where it stopped.
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd
import torch

HOME = os.path.expanduser("~/kronos-test")
HISTORY = os.path.expanduser("~/trading-tool-logs/history")
CONTEXT, PRED_LEN, SAMPLES, TEMP, TOP_P = 400, 4, 5, 1.0, 0.9


def load(key):
    df = pd.read_csv(os.path.join(HISTORY, f"{key}_15m_3y.csv"))
    ts = pd.to_datetime(df["Date"])
    px = df[["Open", "High", "Low", "Close"]].rename(columns=str.lower).astype(float)
    return ts, px


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--limit", type=int, default=0, help="stop after this many new candles (for timing)")
    ap.add_argument("--bars", default="", help="a JSON {index: [candle numbers]} to forecast only those")
    ap.add_argument("--device", default=None, help="mps / cpu (default: the model's own choice)")
    ap.add_argument("--threads", type=int, default=0)
    a = ap.parse_args()
    if a.threads:
        torch.set_num_threads(a.threads)

    sys.path.insert(0, os.path.join(HOME, "src"))
    from model import Kronos, KronosTokenizer, KronosPredictor
    tok = KronosTokenizer.from_pretrained(os.path.join(HOME, "weights", "Kronos-Tokenizer-base"))
    mdl = Kronos.from_pretrained(os.path.join(HOME, "weights", "Kronos-small"))
    tok.eval(); mdl.eval()
    pred = KronosPredictor(mdl, tok, device=a.device, max_context=512)
    print(f"device {pred.device}", flush=True)

    ts, px = load(a.index)
    n = len(px)
    os.makedirs(os.path.join(HOME, "out"), exist_ok=True)
    out = os.path.join(HOME, "out", f"{a.index}.csv")
    done = set(pd.read_csv(out)["i"]) if os.path.exists(out) else set()
    want = None
    if a.bars:
        import json
        want = set(json.load(open(a.bars))[a.index])
    todo = [i for i in range(CONTEXT - 1, n - PRED_LEN) if i not in done and (want is None or i in want)]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{a.index}: {len(todo)} candles to forecast ({len(done)} already done)", flush=True)
    close = px["close"].to_numpy()
    t0, made = time.time(), 0
    for s in range(0, len(todo), a.batch):
        chunk = todo[s:s + a.batch]
        dfs = [px.iloc[i - CONTEXT + 1:i + 1].reset_index(drop=True) for i in chunk]
        xts = [ts.iloc[i - CONTEXT + 1:i + 1].reset_index(drop=True) for i in chunk]
        yts = [ts.iloc[i + 1:i + 1 + PRED_LEN].reset_index(drop=True) for i in chunk]
        torch.manual_seed(chunk[0])
        res = pred.predict_batch(dfs, xts, yts, pred_len=PRED_LEN, T=TEMP, top_p=TOP_P, sample_count=SAMPLES,
                                 verbose=False)
        rows = pd.DataFrame({
            "i": chunk, "time": [ts.iloc[i].isoformat() for i in chunk], "close": [close[i] for i in chunk],
            **{f"f{h}": [float(r["close"].iloc[h - 1]) for r in res] for h in range(1, PRED_LEN + 1)}})
        rows.to_csv(out, mode="a", header=not os.path.exists(out), index=False)
        made += len(chunk)
        if (s // a.batch) % 10 == 0 or s + a.batch >= len(todo):
            rate = made / (time.time() - t0)
            print(f"  {made}/{len(todo)}  {rate:.1f} candles/s  ~{(len(todo) - made) / max(rate, 1e-9) / 60:.0f} min left",
                  flush=True)


if __name__ == "__main__":
    main()
