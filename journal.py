"""
journal.py - a trading journal: your own trades, the tool's tickets, a note a day
================================================================================
The tool's tickets are what the rule set said; they are not necessarily what you
did. A journal worth keeping holds both - the trades you actually took, entered
by hand, beside the tickets the tool issued - and a line a day on why, because
the reason a trade was taken is the one thing no log can reconstruct later.

Per account AND per market, like the trade log it sits beside: rupee trades on
Nifty and dollar trades on Bitcoin in one file would add two currencies into a
total that describes nothing.

Money is before costs unless it says otherwise. For index options each trade
also carries Zerodha's charges - the figure you typed, or an estimate from the
same model the backtests were judged on (regime_study.net_rupees) - so the
journal can show what was actually kept.
"""
import datetime as dt
import json
import math
import random
import os
import re
import threading
import uuid

import config
import trade_log

JOURNAL_NAME = "journal.json"
MAX_TEXT = 2000
MAX_TRADES = 5000
SIDES = ("CE", "PE", "FUT")
_lock = threading.Lock()
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^\d{2}:\d{2}$")
_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def today_ist():
    return dt.datetime.now(_IST).date()


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
def _path(email, market):
    log = trade_log.user_log_path(email, market)
    # user_log_path falls back to the shared desktop log when it cannot make
    # the private folder. A journal must never land in a shared file.
    if os.sep + "users" + os.sep not in log:
        raise RuntimeError("no private folder for this account")
    return os.path.join(os.path.dirname(log), JOURNAL_NAME)


def load(email, market):
    try:
        with open(_path(email, market)) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("trades", [])
    data.setdefault("notes", {})
    return data


def _save(email, market, data):
    path = _path(email, market)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def instruments(market):
    return [k for k, v in config.INSTRUMENTS.items() if v.get("market") == market]


def _num(value, name, lo, hi, required=True):
    if value in (None, ""):
        if required:
            raise ValueError(f"{name} is needed.")
        return None
    try:
        x = float(str(value).replace(",", "").strip())
    except ValueError:
        raise ValueError(f"{name} has to be a number.")
    if x != x or not lo <= x <= hi:
        raise ValueError(f"{name} is out of range.")
    return x


def clean_trade(form, market):
    """A trade typed into the form, checked. Raises ValueError with a message
    fit to show the person who typed it."""
    date = (form.get("date") or "").strip()
    if not _DATE.match(date):
        raise ValueError("Pick the date of the trade.")
    try:
        day = dt.date.fromisoformat(date)
    except ValueError:
        raise ValueError("That is not a real date.")
    if day > today_ist():
        raise ValueError("That date is in the future.")
    tm = (form.get("time") or "").strip() or None
    if tm and not _TIME.match(tm):
        raise ValueError("Time is hours and minutes, like 10:45.")
    inst = (form.get("instrument") or "").strip().upper()
    if inst not in instruments(market) + ["OTHER"]:
        raise ValueError("Pick one of this market's instruments, or Other.")
    side = (form.get("side") or "").strip().upper()
    if side not in SIDES:
        raise ValueError("Side is CE, PE or FUT.")
    direction = (form.get("dir") or "buy").strip().lower()
    if direction not in ("buy", "sell"):
        raise ValueError("Direction is buy or sell.")
    meta = config.INSTRUMENTS.get(inst) or {}
    lot_size = _num(form.get("lot_size"), "Lot size", 0.0001, 100000, required=False)
    if lot_size is None:
        lot_size = float(meta.get("lot_size") or 0) or None
    if not lot_size:
        raise ValueError("Lot size is needed for this instrument.")
    notes = (form.get("notes") or "").strip()
    if len(notes) > MAX_TEXT:
        raise ValueError(f"Notes are limited to {MAX_TEXT} characters.")
    return {
        "date": date, "time": tm, "instrument": inst, "side": side, "dir": direction,
        "strike": _num(form.get("strike"), "Strike", 0, 1e7, required=False),
        "lots": _num(form.get("lots"), "Lots", 0.0001, 100000),
        "lot_size": lot_size,
        "entry": _num(form.get("entry"), "Entry price", 0, 1e7),
        "exit": _num(form.get("exit"), "Exit price", 0, 1e7),
        "charges": _num(form.get("charges"), "Charges", 0, 1e7, required=False),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# changes
# ---------------------------------------------------------------------------
def add_trade(email, market, form):
    t = clean_trade(form, market)
    with _lock:
        data = load(email, market)
        if len(data["trades"]) >= MAX_TRADES:
            raise ValueError("The journal is full.")
        t["id"] = uuid.uuid4().hex[:12]
        data["trades"].append(t)
        _save(email, market, data)
    return t["id"]


def update_trade(email, market, trade_id, form):
    t = clean_trade(form, market)
    with _lock:
        data = load(email, market)
        for i, old in enumerate(data["trades"]):
            if old.get("id") == trade_id:
                t["id"] = trade_id
                data["trades"][i] = t
                _save(email, market, data)
                return True
    raise ValueError("That trade is not in your journal.")


def delete_trade(email, market, trade_id):
    with _lock:
        data = load(email, market)
        kept = [t for t in data["trades"] if t.get("id") != trade_id]
        if len(kept) == len(data["trades"]):
            raise ValueError("That trade is not in your journal.")
        data["trades"] = kept
        _save(email, market, data)
    return True


def set_note(email, market, date, text):
    if not _DATE.match(date or ""):
        raise ValueError("Pick a day for the note.")
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise ValueError("That is not a real date.")
    text = (text or "").strip()
    if len(text) > MAX_TEXT:
        raise ValueError(f"A note is limited to {MAX_TEXT} characters.")
    with _lock:
        data = load(email, market)
        if text:
            data["notes"][date] = text
        else:
            data["notes"].pop(date, None)
        _save(email, market, data)
    return True


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def estimate_charges(instrument, entry, exit_price, qty, direction="buy"):
    """Zerodha's charges on one round trip of an index option, or None where the
    model does not apply (crypto, anything not an NSE/BSE index option)."""
    exch = (config.INSTRUMENTS.get(instrument) or {}).get("kite_exchange")
    if exch not in ("NSE", "BSE") or not qty or entry is None or exit_price is None:
        return None
    import regime_study as rs
    buy, sell = (entry, exit_price) if direction == "buy" else (exit_price, entry)
    gross = (sell - buy) * qty
    return round(gross - rs.net_rupees(buy, sell, qty, exch, 0.0), 2)


def entries(email, market, source="all"):
    """Every journal line, oldest first: yours (source "mine") and the tool's
    closed tickets (source "tool")."""
    out = []
    if source in ("all", "mine"):
        for t in load(email, market)["trades"]:
            qty = (t.get("lots") or 0) * (t.get("lot_size") or 0)
            sign = 1 if t.get("dir", "buy") == "buy" else -1
            gross = round((t["exit"] - t["entry"]) * qty * sign, 2)
            charges, estimated = t.get("charges"), False
            if charges is None and t.get("side") in ("CE", "PE"):
                charges = estimate_charges(t["instrument"], t["entry"], t["exit"], qty, t.get("dir", "buy"))
                estimated = charges is not None
            out.append(dict(t, source="mine", gross=gross, charges=charges,
                            charges_estimated=estimated,
                            net=None if charges is None else round(gross - charges, 2),
                            status=None))
    if source in ("all", "tool"):
        try:
            rows = trade_log._read_rows(trade_log.user_log_path(email, market))
        except Exception:
            rows = []
        for r in rows:
            if r.get("event") != "CLOSE" or "the tool stopped" in (r.get("status") or ""):
                continue
            pnl = _f(r.get("pnl"))
            if pnl is None:
                continue           # a ticket tracked on the index has no money figure
            entry, exit_price = _f(r.get("entry")), _f(r.get("exit"))
            lots, lot_size = _f(r.get("lots")) or 1.0, _f(r.get("lot_size")) or 1.0
            charges = estimate_charges(r.get("index"), entry, exit_price, lots * lot_size)
            out.append({
                "id": r.get("trade_id") or "", "source": "tool", "date": r.get("date") or "",
                "time": (r.get("time_ist") or "")[:5] or None, "instrument": r.get("index"),
                "side": r.get("option_type"), "dir": "buy", "strike": _f(r.get("strike")),
                "lots": lots, "lot_size": lot_size, "entry": entry, "exit": exit_price,
                "gross": round(pnl, 2), "charges": charges,
                "charges_estimated": charges is not None,
                "net": None if charges is None else round(pnl - charges, 2),
                "status": r.get("status"), "notes": ""})
    out = [e for e in out if _DATE.match(e.get("date") or "")]
    out.sort(key=lambda e: (e["date"], e.get("time") or ""))
    return out


def summarize(lines):
    """Daily totals and the statistics a journal is read for."""
    days = {}
    for e in lines:
        d = days.setdefault(e["date"], {"gross": 0.0, "net": 0.0, "trades": 0,
                                        "wins": 0, "losses": 0, "net_complete": True})
        d["gross"] += e["gross"]
        d["trades"] += 1
        if e.get("net") is None:
            d["net_complete"] = False
        else:
            d["net"] += e["net"]
        if e["gross"] > 0:
            d["wins"] += 1
        elif e["gross"] < 0:
            d["losses"] += 1
    for d in days.values():
        d["gross"] = round(d["gross"], 2)
        d["net"] = round(d["net"], 2) if d["net_complete"] else None

    pnls = [e["gross"] for e in lines]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    eq = peak = dd = 0.0
    cur_w = cur_l = best_w = best_l = 0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        if p > 0:
            cur_w, cur_l = cur_w + 1, 0
        elif p < 0:
            cur_l, cur_w = cur_l + 1, 0
        else:
            cur_w = cur_l = 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    by_day = sorted(days.items())
    best = max(by_day, key=lambda kv: kv[1]["gross"]) if by_day else None
    worst = min(by_day, key=lambda kv: kv[1]["gross"]) if by_day else None
    nets = [e["net"] for e in lines if e.get("net") is not None]
    by_inst = {}
    for e in lines:
        b = by_inst.setdefault(e.get("instrument") or "?", {"trades": 0, "gross": 0.0, "wins": 0})
        b["trades"] += 1
        b["gross"] = round(b["gross"] + e["gross"], 2)
        b["wins"] += 1 if e["gross"] > 0 else 0
    stats = {
        "trades": n,
        "gross": round(sum(pnls), 2) if n else 0.0,
        "net": round(sum(nets), 2) if n and len(nets) == n else None,
        "charges": round(sum(e["charges"] for e in lines if e.get("charges") is not None), 2),
        "charges_known_for": len(nets),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / n, 1) if n else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "expectancy": round(sum(pnls) / n, 2) if n else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else None,
        "max_drawdown": round(dd, 2),
        "longest_win_streak": best_w, "longest_loss_streak": best_l,
        "green_days": sum(1 for _, d in by_day if d["gross"] > 0),
        "red_days": sum(1 for _, d in by_day if d["gross"] < 0),
        "best_day": {"date": best[0], "gross": best[1]["gross"]} if best else None,
        "worst_day": {"date": worst[0], "gross": worst[1]["gross"]} if worst else None,
        "by_instrument": by_inst,
        "first": by_day[0][0] if by_day else None, "last": by_day[-1][0] if by_day else None,
    }
    return {"days": days, "stats": stats, "risk": risk(lines)}


# ---------------------------------------------------------------------------
# risk, from your own trades
# ---------------------------------------------------------------------------
# What a run of ordinary bad luck looks like on YOUR record: how rough a day
# gets, and - by re-drawing the next hundred trades at random from the ones you
# have already taken - the range the next stretch could plausibly land in. It
# assumes the future resembles your past trades, which is the most it can
# honestly assume, and it says so. Too few trades and it declines to guess.
RISK_MIN_TRADES = 20
MC_TRADES = 100
MC_RUNS = 2000


def _pct(sorted_vals, q):
    """Linear-interpolated percentile of an already-sorted list, q in [0, 1]."""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def risk(lines, seed=7):
    pnls = [e["gross"] for e in lines]
    n = len(pnls)
    out = {"trades": n, "enough": n >= RISK_MIN_TRADES, "min_trades": RISK_MIN_TRADES}
    if not out["enough"]:
        return out
    days = {}
    for e in lines:
        days[e["date"]] = days.get(e["date"], 0.0) + e["gross"]
    daily = sorted(days.values())
    m = sum(daily) / len(daily)
    sd = math.sqrt(sum((x - m) ** 2 for x in daily) / (len(daily) - 1)) if len(daily) > 1 else 0.0
    downside = [min(0.0, x) for x in daily]
    dsd = math.sqrt(sum(x * x for x in downside) / len(downside)) if downside else 0.0
    tail = daily[:max(1, int(math.ceil(len(daily) * 0.05)))]
    out.update({
        "days": len(daily),
        "sharpe": round(m / sd * math.sqrt(252), 2) if sd > 0 else None,
        "sortino": round(m / dsd * math.sqrt(252), 2) if dsd > 0 else None,
        "var95_day": round(_pct(daily, 0.05), 2),
        "es95_day": round(sum(tail) / len(tail), 2),
    })
    rnd = random.Random(seed)
    totals, dds = [], []
    for _ in range(MC_RUNS):
        eq = peak = dd = 0.0
        for _ in range(MC_TRADES):
            eq += pnls[rnd.randrange(n)]
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        totals.append(eq)
        dds.append(dd)
    totals.sort()
    dds.sort()
    out["monte_carlo"] = {
        "trades": MC_TRADES, "runs": MC_RUNS,
        "total_p5": round(_pct(totals, 0.05), 2), "total_median": round(_pct(totals, 0.5), 2),
        "total_p95": round(_pct(totals, 0.95), 2),
        "chance_down": round(100.0 * sum(1 for t in totals if t < 0) / MC_RUNS, 1),
        "drawdown_median": round(_pct(dds, 0.5), 2), "drawdown_p95": round(_pct(dds, 0.95), 2),
    }
    return out
