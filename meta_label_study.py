#!/usr/bin/env python3
"""
meta_label_study.py — can a second model learn which of the tool's signals will fail? (López de Prado's meta-labelling)
================================================================================
The user, 22 Oct 2026: "think you are a senior researcher and you have to solve this problem" (avoiding the trades the
market reverses on). reversal_study.py found no single condition at entry that marks a losing trade; gamma_regime_study
(time to expiry, 3 years) and gex_study (the chains' own gamma, 27 days) did not decide continuation either. Meta-
labelling (Advances in Financial Machine Learning, ch. 3) asks the question with every condition at once: the tool's
signal stays the primary model; a classifier learns, from its past trades, which ones fail; the ones it rates worst
are skipped. The published evidence is thin (toy examples, unreplicated figures) - so this is held to a strict test.

FIXED BEFORE ANY RESULT WAS SEEN (22 Oct 2026). Every trade, each index on its system (the rules on Nifty, Sensex and
Midcap; the TradingView rating on Nifty and Bank Nifty), with reversal_study's 15 entry features plus trading days to
expiry, G (1/sqrt(years to expiry)), weekday, the system, the index and the side. Label: the trade made money (per lot
after costs). Two models, both trained ONLY on the first two years: gradient-boosted trees (HistGradientBoosting,
depth 3, 200 rounds, learning rate 0.05) and a plain logistic regression (standardised features). The skip threshold
is the 20th percentile of the in-sample predictions made out-of-fold (five folds in time order, each predicted by a
model trained on the folds before it), so nothing is tuned on the held-out year.
  On the held-out year: the AUC (how well it ranks winners above losers; 0.5 = a coin), the skipped trades' total, and
  what skipping them does to each system's total. It WORKS only if a model's held-out AUC > 0.55 AND skipping raises
  the held-out total for BOTH systems. Then - and only then - it would go on to a test inside the backtest.

RESULT (22 Oct 2026; meta_label_study_results.txt) - NO MODEL PASSED. Held-out AUC 0.536 (trees) and 0.569 (logistic,
which mostly learned that the rating's trades win less often). The trades they would skip still made money: +48,151 /
+70,323 (trees), +5,309 / +77,449 (logistic) held-out on the rules / the rating - skipping costs. Win or loss can be
ranked a little; profit cannot, because a few big winners sit among the trades that look worst.

    /usr/bin/python3 meta_label_study.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gamma_regime_study as grs
import reversal_study as rv

NUM = list(rv.FEATURES) + ["dte", "G", "weekday"]


def frame(base):
    import stop_day_study as sds
    out = []
    for (system, key), rows in base.items():
        T = rv.feature_table(key)
        f_exp, restore = grs.expiry_calc(key)
        per_day = {}
        for r in sorted(rows, key=lambda x: x["when"]):
            i = rv.bar_of(T, r["when"])
            if i < 20:
                continue
            d = T["days"][i]
            per_day[d] = per_day.get(d, 0) + 1
            f = rv.feats(T, i, r["side"] == "CE")
            f["trade_no"] = per_day[d]
            n, yrs = f_exp(r["when"])
            f["dte"] = n if n is not None else np.nan
            f["G"] = 1 / math.sqrt(yrs) if yrs else np.nan
            f["weekday"] = pd.Timestamp(r["when"]).weekday()
            out.append({"system": system, "index": key, "side": r["side"], "when": r["when"], "net": r["net"],
                        "is": r["when"] < sds.SPLIT, **f})
        restore()
    A = pd.DataFrame(out).sort_values("when").reset_index(drop=True)
    for k in ("NIFTY", "SENSEX", "MIDCPNIFTY", "BANKNIFTY"):
        A["ix_" + k] = (A["index"] == k).astype(float)
    A["sys_tv"] = (A.system == "tv").astype(float)
    A["side_ce"] = (A.side == "CE").astype(float)
    return A


def cols(A):
    return NUM + [c for c in A.columns if c.startswith("ix_")] + ["sys_tv", "side_ce"]


def models():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {"boosted trees": lambda: HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05,
                                                                     random_state=7),
            "logistic": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                              LogisticRegression(C=0.5, max_iter=2000))}


def main():
    import multiprocessing as mp
    from sklearn.metrics import roc_auc_score
    import order_block_study as ob
    import stop_day_study as sds
    with mp.get_context("spawn").Pool(processes=5, maxtasksperchild=1) as pool:
        base = rv.collect(pool)
    for k, figs in ob.B_FIGS.items():
        s = sds.split(base[("rules", k)])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} rules must be room_fix_study's B {figs}"
    A = frame(base)
    X, y = A[cols(A)].to_numpy(float), (A.net > 0).to_numpy(int)
    ins, oos = A["is"].to_numpy(), ~A["is"].to_numpy()
    W = 150
    print("=" * W)
    print(f" META-LABELLING - {len(A)} trades ({ins.sum()} first two years for training, {oos.sum()} held-out for judging),"
          f" {len(cols(A))} features, label = the trade made money")
    print("=" * W)
    ok_any = False
    for name, make in models().items():
        # out-of-fold in-sample predictions, folds in time order, for the threshold
        idx_in = np.where(ins)[0]
        folds = np.array_split(idx_in, 5)
        oof = np.full(len(A), np.nan)
        for n in range(1, 5):
            tr = np.concatenate(folds[:n])
            m = make().fit(X[tr], y[tr])
            oof[folds[n]] = m.predict_proba(X[folds[n]])[:, 1]
        thr = float(np.nanpercentile(oof[np.concatenate(folds[1:])], 20))
        m = make().fit(X[ins], y[ins])
        p = m.predict_proba(X[oos])[:, 1]
        auc_in = roc_auc_score(y[np.concatenate(folds[1:])], oof[np.concatenate(folds[1:])])
        auc = roc_auc_score(y[oos], p)
        H = A[oos].copy()
        H["p"] = p
        print(f" {name}: AUC in-sample out-of-fold {auc_in:.3f}, HELD-OUT {auc:.3f}   (skip threshold {thr:.3f})")
        works = auc > 0.55
        for system in ("rules", "tv"):
            g = H[H.system == system]
            skip = g[g.p < thr]
            keep = g[g.p >= thr]
            change = -skip.net.sum()
            works = works and change > 0
            print(f"   {('the rules' if system == 'rules' else 'the TradingView rating'):24s} held-out {len(g):>5} trades "
                  f"₹{g.net.sum():>+10,.0f}; skipped {len(skip):>4} ({len(skip) / max(1, len(g)):4.0%}) won {(skip.net > 0).mean() if len(skip) else 0:4.0%} "
                  f"total ₹{skip.net.sum():>+9,.0f}; kept won {(keep.net > 0).mean() if len(keep) else 0:4.0%} -> skipping changes the"
                  f" total by ₹{change:>+9,.0f}")
        # by predicted fifth, held-out: does a higher score mean better trades?
        q = pd.qcut(H.p.rank(method="first"), 5, labels=False)
        print("   held-out by the model's score, lowest fifth to highest: "
              + "  ".join(f"Q{k + 1} won {(H.net[q == k] > 0).mean():4.0%} avg ₹{H.net[q == k].mean():+5,.0f}" for k in range(5)))
        print(f"   {'WORKS' if works else 'does not work'} by the rule fixed above")
        ok_any = ok_any or works
    print(f"\n VERDICT: {'a model passed - it goes on to the backtest test' if ok_any else 'NO MODEL PASSED - the failing trades cannot be told apart in advance from these features'}")
    return A


if __name__ == "__main__":
    main()
