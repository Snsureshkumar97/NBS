"""
review_page.py — is the setup doing in real life what the backtest said it would?
================================================================================
The question a professional asks every week, answered from your own ticket log
instead of from memory: how many trades, how many won, what one is worth on
average, where the money came from and where it went - each set beside the
backtest's figure for the same thing, so a live result can be read as "on
track" or "not" rather than as a number with nothing to compare it to.

Everything is per lot (per contract on crypto). The lots you picked scale the
money, not the quality of the signal; per lot is the only way two weeks at
different sizes, or live against backtest, can be compared honestly.

And the sample size is stated every time, with a range. Twenty trades cannot
tell a 1.17 profit factor from a 0.9 one, and a page that shows a number
without saying so is how people talk themselves into or out of a setup a
fortnight too early.
"""
import datetime as dt
import html
import math

import config
import trade_log

# The backtest this is measured against: rule_review.py, the rules live since
# 11 Sep 2026 (opening-range break, room to run >= 1x stop, exit at T2, Bank
# Nifty watch-only), real expiries, after Zerodha's costs, per lot, on the
# held-out year (15 Aug 2025 - 14 Aug 2026). Index options only - there is no
# comparable crypto backtest priced as options, so crypto shows its own record.
BENCHMARK = {
    "all":    {"n": 1043, "win": 43.0, "avg": 223, "pf": 1.25},
    "NIFTY":  {"n": 534,  "win": 42.1, "avg": 201, "pf": 1.21},
    "SENSEX": {"n": 509,  "win": 44.0, "avg": 245, "pf": 1.31},
    "expiry": {"n": 204,  "win": 37.3, "avg": 562, "pf": 1.66},
    "other":  {"n": 839,  "win": 44.5, "avg": 140, "pf": 1.16},
}
MIN_SAMPLE = 30


# ---------------------------------------------------------------- expiries
def _last_weekday(y, m, wd):
    d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    while d.weekday() != wd:
        d -= dt.timedelta(days=1)
    return d


def is_expiry_day(index, d):
    """By the exchange calendar in force since September 2025. Holiday shifts
    are not known here, so a moved expiry is counted on its scheduled day -
    a handful of days a year, labelled as such on the page."""
    if index == "NIFTY":
        return d.weekday() == 1
    if index == "SENSEX":
        return d.weekday() == 3
    if index == "BANKNIFTY":
        return d == _last_weekday(d.year, d.month, 1)
    return False


# ---------------------------------------------------------------- trades
def closed_trades(path):
    """One dict per finished ticket that has a P&L, oldest first."""
    rows = trade_log._read_rows(path)
    opens, out = {}, []
    for r in rows:
        tid = r.get("trade_id") or ""
        if (r.get("event") or "").upper() == "OPEN":
            opens[tid] = r
    for r in rows:
        if (r.get("event") or "").upper() != "CLOSE":
            continue
        pnl = trade_log._f(r.get("pnl"))
        if pnl is None:
            continue                     # "the tool stopped" - no honest exit price
        o = opens.get(r.get("trade_id"), {})
        lots = trade_log._f(r.get("lots")) or trade_log._f(o.get("lots")) or 1.0
        lot_size = trade_log._f(r.get("lot_size")) or trade_log._f(o.get("lot_size")) or 1.0
        entry = trade_log._f(o.get("entry")) or trade_log._f(r.get("entry"))
        stop = trade_log._f(o.get("stop")) or trade_log._f(r.get("stop"))
        risk = (entry - stop) * lot_size if (entry and stop and entry > stop) else None
        per_lot = pnl / lots if lots else pnl
        try:
            d = dt.date.fromisoformat(o.get("date") or r.get("date"))
        except (TypeError, ValueError):
            continue
        t = (o.get("time_ist") or "")[:5]
        status = r.get("status") or ""
        out.append({
            "id": r.get("trade_id"), "index": r.get("index"), "date": d, "time": t,
            "pnl": per_lot, "r": (per_lot / risk) if risk else None,
            "exit": _exit_kind(status), "expiry": is_expiry_day(r.get("index"), d),
        })
    out.sort(key=lambda x: (x["date"], x["time"]))
    return out


def _exit_kind(status):
    s = status.lower()
    if "stop-loss" in s:
        return "Stop"
    if trade_log.is_target_close(status):
        return "Target"
    if "cleared" in s:
        return "Cleared by you"
    if "signal changed" in s:
        return "Signal flipped"
    if "bell" in s or "close" in s or "square" in s:
        return "Closed at the bell"
    return "Other"


def stats(trades):
    n = len(trades)
    if not n:
        return None
    p = [t["pnl"] for t in trades]
    wins = [x for x in p if x > 0]
    gross_w, gross_l = sum(wins), -sum(x for x in p if x < 0)
    mean = sum(p) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in p) / (n - 1)) if n > 1 else 0.0
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in p:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    rs = [t["r"] for t in trades if t["r"] is not None]
    return {
        "n": n, "win": 100.0 * len(wins) / n, "avg": mean, "total": sum(p),
        "pf": (gross_w / gross_l) if gross_l else None,
        "lo": mean - 1.96 * sd / math.sqrt(n) if n > 1 else None,
        "hi": mean + 1.96 * sd / math.sqrt(n) if n > 1 else None,
        "dd": dd, "avg_r": (sum(rs) / len(rs)) if rs else None,
        "best": max(p), "worst": min(p),
        "days": len({t["date"] for t in trades}),
    }


def build(email, market):
    path = trade_log.user_log_path(email, market)
    trades = closed_trades(path)
    groups = {}

    def group(title, key):
        g = {}
        for t in trades:
            g.setdefault(key(t), []).append(t)
        groups[title] = [(k, stats(v)) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))]

    group("By index", lambda t: t["index"])
    group("By how it ended", lambda t: t["exit"])

    def slot(t):
        if not t["time"]:
            return "?"
        if config.market_for(t["index"])["always_open"]:
            h = int(t["time"][:2])
            return f"{h // 6 * 6:02d}:00-{h // 6 * 6 + 6:02d}:00 IST"
        return ("09:15-11:00" if t["time"] < "11:00" else
                "11:00-13:00" if t["time"] < "13:00" else "13:00-15:40")
    group("By entry time", slot)
    if not config.MARKETS[market]["always_open"]:
        group("Expiry day or not", lambda t: "expiry" if t["expiry"] else "other")
    return {"market": market, "all": stats(trades), "groups": groups,
            "recent": trades[-15:][::-1], "since": trades[0]["date"] if trades else None,
            "ccy": config.MARKETS[market].get("currency", "INR")}


# ---------------------------------------------------------------- page
def _money(v, ccy, signed=True):
    if v is None:
        return "—"
    sym = "$" if ccy == "USD" else "₹"
    sign = ("+" if v >= 0 else "−") if signed else ""
    return f"{sign}{sym}{abs(v):,.0f}"


def _verdict(s, bench):
    if not s:
        return ("", "No finished trades yet.")
    if s["n"] < MIN_SAMPLE:
        return ("warn", f"{s['n']} trades - too few to judge. Anything under about "
                        f"{MIN_SAMPLE} is mostly luck either way; keep size small and "
                        f"let the sample grow.")
    if bench is None:
        return ("", f"{s['n']} trades.")
    lo, hi = s["lo"], s["hi"]
    if lo is not None and lo > 0:
        return ("ok", "Profitable, and the range excludes zero - the edge is showing up live.")
    if hi is not None and hi < bench["avg"] * 0.25:
        return ("bad", "Running well below the backtest, past what luck usually explains. "
                       "Cut size and look at where the losses come from below.")
    return ("warn", "Within the range the backtest allows, but not yet proven either way.")


def page(data):
    e = html.escape
    ccy, s = data["ccy"], data["all"]
    crypto = config.MARKETS[data["market"]]["always_open"]
    bench_all = None if crypto else BENCHMARK["all"]
    unit = "contract" if crypto else "lot"
    cls, verdict = _verdict(s, bench_all)

    def cell(label, val, sub=""):
        return (f'<div class="k"><div class="l">{e(label)}</div><div class="v">{val}</div>'
                f'<div class="s">{sub}</div></div>')

    def pf(v):
        return "no losses" if v is None else f"{v:.2f}"

    head = ""
    if s:
        rng = (f"{_money(s['lo'], ccy)} to {_money(s['hi'], ccy)}"
               if s["lo"] is not None else "—")
        b = bench_all or {}
        head = (
            cell("Trades", f"{s['n']}", f"over {s['days']} day{'s' if s['days'] != 1 else ''}")
            + cell("Win rate", f"{s['win']:.0f}%", f"backtest {b['win']:.0f}%" if b else "")
            + cell(f"Average per {unit}", _money(s["avg"], ccy),
                   f"backtest {_money(b['avg'], ccy)}" if b else "")
            + cell("Profit factor", pf(s["pf"]), f"backtest {b['pf']:.2f}" if b else "")
            + cell(f"Total per {unit}", _money(s["total"], ccy),
                   f"worst drawdown {_money(s['dd'], ccy, False)}")
            + cell("Average R", "—" if s["avg_r"] is None else f"{s['avg_r']:+.2f}R",
                   "result ÷ money at risk")
            + f'<div class="k wide"><div class="l">95% range for the true average per {unit}</div>'
              f'<div class="v sm">{rng}</div><div class="s">narrows as trades accumulate</div></div>')

    def table(title, rows):
        if not rows:
            return ""
        body = []
        for k, st in rows:
            if not st:
                continue
            b = None if crypto else BENCHMARK.get(k)
            label = {"expiry": "Expiry day", "other": "Other days"}.get(k, k)
            col = "up" if st["avg"] > 0 else "down" if st["avg"] < 0 else ""
            bt = (f"{b['win']:.0f}% · {_money(b['avg'], ccy)} · PF {b['pf']:.2f}" if b else "")
            body.append(
                f"<tr><td>{e(str(label))}</td><td>{st['n']}</td><td>{st['win']:.0f}%</td>"
                f"<td class='{col}'>{_money(st['avg'], ccy)}</td><td>{pf(st['pf'])}</td>"
                f"<td class='{col}'>{_money(st['total'], ccy)}</td>"
                f"<td class='mut'>{bt}</td></tr>")
        if not body:
            return ""
        return (f"<h2>{e(title)}</h2><div class='tw'><table><tr><th></th><th>Trades</th><th>Win</th>"
                f"<th>Avg / {unit}</th><th>PF</th><th>Total / {unit}</th><th>Backtest</th></tr>"
                + "".join(body) + "</table></div>")

    groups = "".join(table(t, rows) for t, rows in data["groups"].items())
    def rrow(t):
        r = "—" if t["r"] is None else f"{t['r']:+.2f}R"
        col = "up" if t["pnl"] > 0 else "down"
        tag = " · expiry" if t["expiry"] else ""
        return (f"<tr><td>{t['date']:%d %b}</td><td>{e(t['time'])}</td>"
                f"<td>{e(t['index'] or '')}</td><td>{e(t['exit'])}{tag}</td>"
                f"<td class='{col}'>{_money(t['pnl'], ccy)}</td><td>{r}</td></tr>")
    recent = "".join(rrow(t) for t in data["recent"])
    recent = (f"<h2>Latest trades</h2><div class='tw'><table><tr><th>Date</th><th>Entry</th>"
              f"<th>Index</th><th>Ended</th><th>Per {unit}</th><th>R</th></tr>{recent}</table></div>"
              if recent else "")
    since = f" since {data['since']:%d %b %Y}" if data["since"] else ""
    label = config.MARKETS[data["market"]].get("label", data["market"])
    bench_note = ("" if crypto else
                  "<p class='note'>Backtest = rule_review.py on the rules running now, the held-out "
                  "year to 14 Aug 2026, real expiries, after Zerodha's costs, per lot. Its premiums "
                  "are modelled, so treat it as a guide; the daily option-price recorder is building "
                  "the real-price version. Expiry days are by the current exchange calendar - a "
                  "holiday-shifted expiry counts on its scheduled day.</p>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review · NBS Signal Tool</title>
<style>
:root{{--bg:#0a0d14;--card:#121724;--bd:#232b3d;--ink:#e8ecf4;--ink2:#aab3c5;--ink3:#76809a;
  --up:#4caf50;--down:#ff5722;--warn:#f6a500;--accent:#4d94e8}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:22px 18px 60px}}
a{{color:var(--accent)}}
.top{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
h1{{font-size:22px;margin:0}} h1 small{{color:var(--ink3);font-weight:500;font-size:14px;margin-left:8px}}
h2{{font-size:15px;margin:28px 0 10px;color:var(--ink2)}}
.verdict{{margin:16px 0;padding:12px 14px;border-radius:12px;border:1px solid var(--bd);background:var(--card)}}
.verdict.ok{{border-color:#1f4a2c}} .verdict.warn{{border-color:#5c4a10}} .verdict.bad{{border-color:#5c2a18}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.k{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px}}
.k.wide{{grid-column:span 2}}
.k .l{{font-size:11.5px;color:var(--ink3);text-transform:uppercase;letter-spacing:.4px}}
.k .v{{font-size:22px;font-weight:700;margin-top:2px}} .k .v.sm{{font-size:17px}}
.k .s{{font-size:12px;color:var(--ink3)}}
.tw{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--bd);border-radius:12px;overflow:hidden}}
th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid var(--bd);white-space:nowrap}}
th{{font-size:11.5px;color:var(--ink3);font-weight:600;text-transform:uppercase;letter-spacing:.4px}}
.up{{color:var(--up)}} .down{{color:var(--down)}} .mut{{color:var(--ink3);font-size:12.5px}}
.note{{color:var(--ink3);font-size:12.5px;margin-top:18px}}
</style></head><body><div class="wrap">
<div class="top"><h1>Review<small>{e(label)}{since}</small></h1>
<a href="/app">← Back to the tool</a></div>
<div class="verdict {cls}">{e(verdict)}</div>
<div class="grid">{head}</div>
{groups}
{recent}
{bench_note}
<p class="note">Per {unit} throughout: each trade's result divided by the lots it was issued for.
Trades the tool could not price (it was stopped while they were open) are left out.</p>
</div></body></html>"""
