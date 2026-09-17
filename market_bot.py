"""
market_bot.py — the Market Bot: the market, the signal and an open ticket, explained
================================================================================
Asked for by the user on 17 Sep 2026: a section where an assistant explains what
the selected market is doing, why the signal says what it says, and whether an
open ticket should be held or exited - per market, changing with the market.

HOW IT ANSWERS
    Claude (claude-opus-5) is given a fresh snapshot of THIS market for every
    question - the selected index's signal, what is holding it, its levels, the
    other indices beside it, and the open ticket with its frozen levels - plus
    the tool's rules. Hold-or-exit is answered from those rules, never from a
    view of its own: a ticket closes at its exit target or its stop, and the
    answer says where price is between them and what the rules would do.

WHAT IS SENT, AND WHAT IS NOT
    Sent to Anthropic: the market snapshot above and your question. Never sent:
    your name, email, broker ID, account details or capital. The snapshot is
    built from an allow-list of fields, and scrub() removes anything that looks
    like an email or a Zerodha client ID even so.

THE KEY AND THE COST
    Your own ANTHROPIC_API_KEY, billed to your Anthropic account, read from the
    environment or ~/.trading-tool/.env (added after the server started is fine:
    it is looked for again on the next question). The stable instructions are
    prompt-cached. A per-account cooldown and daily cap stop a stuck page from
    running up a bill.

*** NOT FINANCIAL ADVICE. The bot explains a mechanical rule set and its data.
*** It never places, changes or cancels an order.
"""
import datetime as dt
import json
import os
import re
import threading
import time

import config

MODEL = "claude-opus-5"
MAX_TOKENS = 16000           # thinking counts toward this; answers themselves are short
EFFORT = "medium"            # a chat explanation, not a hard reasoning task
MAX_QUESTION = 800
MAX_TURNS = 6                # earlier exchanges kept for context (bounds the POST body size)
MIN_GAP_S = 4.0
DAILY_CAP = 150              # questions per account per day
TIMEOUT_S = 120.0
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

SYSTEM = """You are the Market Bot inside TradePicker, a rule-based options signal tool. \
It covers the Indian index options Nifty, Bank Nifty and Sensex (traded through Zerodha, in rupees, \
09:15 to 15:40 IST) and Bitcoin options on Deribit (in dollars, around the clock). \
The person asking is the tool's user, looking at one market and one index at a time.

Every question arrives with a <market_snapshot> of that market at the moment of asking. \
It is the only source of facts you have. Use its numbers; never invent prices, levels, news or data \
that are not in it. If something the question needs is missing, say so plainly.

A ticket that has already closed is NOT in open_ticket - the moment it closes the tool clears its \
live state - so entry, exit and result for a past trade come only from recent_trades_this_index \
(newest first, capped, this index only). If the question is about a trade that closed, look there \
before saying you have no information; if it is genuinely not in that list either, say so rather \
than guessing.

session_today.per_index gives each traded index's own net result for today, in this market - so the \
session-wide tallies (issued, wins, stops, net) can be reconciled against the selected index's own \
trades without inventing another index's strike, entry or exit, which you were not given and must \
not guess at. If the numbers do not add up to the selected index alone, that is normal and expected \
on a day when more than one index traded - say which other index accounts for the difference and its \
net for the day, not as a gap in what you were given but as something outside this index's own detail.

What you are asked for, and how to answer:
- The market: what the selected index is doing now - direction, trend strength (ADX), where price sits \
against VWAP and the day's range, and how the other indices in the same market compare.
- The signal: why it says what it says (the indicator votes, agreement and dissent, confidence), \
what is holding it if a ticket is not being issued (the hold reason and blockers), and what would have \
to change for that to clear.
- Hold or exit an open ticket: answer strictly from the tool's rules, not from a view of your own. \
A ticket closes at its exit target (exit_at, normally T2) or at its stop, whichever comes first, and \
its levels are frozen at entry. Say where price is now between entry, the stop and the exit target \
(how far to each, and the live P&L), whether any target has already been hit, and what the rules say \
to do from here - which is to hold until the exit target or the stop unless the snapshot shows the \
ticket has already closed. Name the risks in the snapshot that could matter (a weakening trend, an \
approaching stop, a wide spread, expiry day, the closing auction). Do not tell the user to override the \
rules, move the stop, or add to a position.
- If there is no open ticket, say so rather than answering as if there were.

How much to trust a signal, when asked: the tool's own three-year backtests found the Indian index \
rules modestly profitable after costs in both test periods, with most of the profit on expiry days and \
large drawdowns; Bank Nifty lost money in the most recent year. Bitcoin signals did not clear estimated \
costs in either test period. Past results do not predict future ones.

Style: plain English, short paragraphs, at most a few bullet lines starting with "•". No headings, \
tables or markdown. Use the market's currency (₹ for Indian indices, $ for Bitcoin) and index points \
where the snapshot uses them. Keep answers under about 220 words unless the question needs more. \
You are explaining a mechanical rule set, not giving financial advice: when a question asks what to do, \
answer with what the rules say and make clear the decision is the user's."""


class BotError(Exception):
    def __init__(self, message, code=502):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- the key
def key_present():
    """Whether a key is available. A key added to the .env after the server
    started is picked up here - no restart needed."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        try:
            config._load_dotenv()
        except Exception:
            pass
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------- limits
_lock = threading.Lock()
_usage = {}            # email -> {"last": ts, "day": "YYYY-MM-DD", "count": n}


def allow(email, now=None):
    """(ok, message). A cooldown between questions and a daily cap per account."""
    now = time.time() if now is None else now
    day = dt.datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d")
    with _lock:
        u = _usage.setdefault(email, {"last": 0.0, "day": day, "count": 0})
        if u["day"] != day:
            u.update(day=day, count=0)
        if now - u["last"] < MIN_GAP_S:
            return False, "One question at a time - try again in a few seconds."
        if u["count"] >= DAILY_CAP:
            return False, f"That is today's {DAILY_CAP} questions for this account. The cap resets at midnight IST."
        u["last"] = now
        u["count"] += 1
    return True, ""


# ---------------------------------------------------------------- the snapshot
INDEX_FIELDS = ("action", "bias", "confidence", "spot", "strike", "option_type", "expiry",
                "expiry_today", "ltp", "targets", "stop", "premium_targets", "premium_stop",
                "risk_points", "reach_points", "reach_to_risk", "reach_reason", "room", "adx",
                "adx_ok", "rsi", "macd_hist", "vwap_gap", "trend", "votes", "agree", "dissent",
                "blockers", "odds", "spread", "max_spread", "exit_at", "opening_range",
                "lot_size", "not_worth_it")
PEER_FIELDS = ("action", "bias", "confidence", "spot", "adx", "reach_to_risk")
TICKET_FIELDS = ("index", "strike", "expiry", "option_type", "status", "open", "entry_time",
                 "entry", "now", "pnl", "tracked_on", "lots", "lot_size", "targets", "stop",
                 "hit", "hit_time", "sl_hit", "sl_hit_time", "exit_at", "index_targets",
                 "index_stop", "entry_spot")
SESSION_FIELDS = ("issued", "closed_today", "wins", "stops", "net", "per_index", "max_trades",
                  "limits", "loss_limit_pct", "open")
RECENT_TRADES_MAX = 5
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_CLIENT_ID = re.compile(r"\b[A-Z]{2,4}\d{3,6}\b")


def _recent_trades(user, market, index):
    """The last few CLOSED tickets the tool itself issued on this index, newest
    first - entry, exit, result. A closed ticket is cleared from the live state
    the moment it closes, so this is the only place a past trade's numbers
    still exist. Read-only: the same trade log the Journal and Record read."""
    if not user:
        return []
    import trade_log
    try:
        rows = trade_log._read_rows(trade_log.user_log_path(user, market))
    except Exception:
        return []
    out = []
    for r in rows:
        if r.get("event") != "CLOSE" or r.get("index") != index:
            continue
        pnl = r.get("pnl")
        try:
            pnl = round(float(pnl), 2) if pnl not in (None, "") else None
        except ValueError:
            pnl = None
        out.append({
            "date": r.get("date"), "time": (r.get("time_ist") or "")[:5] or None,
            "strike": r.get("strike"), "option_type": r.get("option_type"),
            "entry": r.get("entry"), "exit": r.get("exit"), "pnl": pnl,
            "status": r.get("status"),
            "t1_hit": r.get("t1_hit") == "True", "t2_hit": r.get("t2_hit") == "True",
            "t3_hit": r.get("t3_hit") == "True", "sl_hit": r.get("sl_hit") == "True",
        })
    out.reverse()          # rows are oldest-first; the model reads newest-first
    return out[:RECENT_TRADES_MAX]


def _pick(d, fields):
    return {k: d[k] for k in fields if isinstance(d, dict) and d.get(k) is not None}


def scrub(text):
    """Belt and braces: nothing that looks like an email or a broker client ID
    leaves the machine, whatever a field happened to contain."""
    text = _EMAIL.sub("[removed]", text)
    return _CLIENT_ID.sub(lambda m: m.group(0) if m.group(0) in config.INSTRUMENTS else "[removed]", text)


def build_context(snap, market, index, now=None, user=None):
    """The snapshot sent with a question - an allow-list, never the raw state."""
    now = now or dt.datetime.now(IST)
    indices = snap.get("indices") or {}
    tickets = snap.get("tickets") or {}
    m = config.MARKETS.get(market) or {}
    why = (snap.get("why") or {}).get(index)
    tk = (tickets.get(index) or {})
    ticket = _pick(tk.get("ticket") or {}, TICKET_FIELDS) or None
    ctx = {
        "as_of_ist": now.strftime("%a %d %b %Y %H:%M:%S"),
        "market": m.get("label", market),
        "currency": "USD" if market == "crypto" else "INR",
        "market_open": snap.get("market_open"),
        "closing_auction": snap.get("closing_auction", False),
        "data_feed": snap.get("feed"),
        "selected_index": index,
        "selected": _pick(indices.get(index) or {}, INDEX_FIELDS),
        "reasoning": why,
        "hold_reason": tk.get("wait"),
        "open_ticket": ticket if ticket and ticket.get("open") else None,
        "last_ticket_if_closed": ticket if ticket and not ticket.get("open") else None,
        "recent_trades_this_index": _recent_trades(user, market, index),
        "other_indices_in_this_market": {k: _pick(v or {}, PEER_FIELDS)
                                         for k, v in indices.items() if k != index},
        "session_today": _pick(snap.get("session") or {}, SESSION_FIELDS),
        "rules": {
            "exit_target": getattr(config, "EXIT_AT_TARGET", "T2"),
            "min_reward_to_risk_T3": getattr(config, "MIN_REWARD_RISK_T3", None),
            "trend_gate_adx": config.strictness().get("adx"),
            "trend_measure": ("ADX with its trend reading averaged over 3 candles"
                              if config.adx_dx_smoothing(index) else "ADX over 14 candles"),
            "opening_range_wait": bool(getattr(config, "REGIME_OR_BREAK", False)),
            "max_tickets_per_day": getattr(config, "MAX_TRADES_PER_DAY", None),
            "trend_day_room": bool(getattr(config, "TREND_DAY_ROOM", False)),
            "session_ist": None if m.get("always_open") else "09:15-15:40",
        },
    }
    text = json.dumps(ctx, sort_keys=True, default=str, separators=(",", ":"))
    if len(text) > 40000:                       # a runaway reasoning blob, never a normal snapshot
        ctx["reasoning"] = "(too long to include)"
        text = json.dumps(ctx, sort_keys=True, default=str, separators=(",", ":"))
    return scrub(text)


def clean_history(raw):
    """Earlier turns from the page: alternating user/assistant plain text, capped."""
    try:
        items = json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
    except ValueError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        role, text = it.get("role"), it.get("text")
        if role not in ("user", "assistant") or not isinstance(text, str) or not text.strip():
            continue
        if out and out[-1]["role"] == role:
            continue                            # keep strict alternation
        out.append({"role": role, "content": scrub(text.strip()[:2000])})
    out = out[-2 * MAX_TURNS:]
    while out and out[0]["role"] != "user":
        out.pop(0)
    if out and out[-1]["role"] != "assistant":
        out.pop()                               # the new question is appended after this
    return out


# ---------------------------------------------------------------- asking
def ask(question, history, context, client=None):
    """(answer, meta). Raises BotError with a message fit to show the user."""
    import anthropic
    q = (question or "").strip()
    if not q:
        raise BotError("Ask a question first.", 400)
    if len(q) > MAX_QUESTION:
        raise BotError(f"Keep the question under {MAX_QUESTION} characters.", 400)
    if client is None:
        client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=2)
    messages = list(history) + [{
        "role": "user",
        "content": f"<market_snapshot>\n{context}\n</market_snapshot>\n\n{scrub(q)}",
    }]
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": EFFORT},
            messages=messages,
            # A declined request is re-run server-side on Anthropic's recommended
            # fallback model instead of coming back empty.
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        )
    except anthropic.AuthenticationError:
        raise BotError("Anthropic did not accept the API key. Check ANTHROPIC_API_KEY in ~/.trading-tool/.env.", 502)
    except anthropic.PermissionDeniedError:
        raise BotError("That API key is not allowed to use this model.", 502)
    except anthropic.RateLimitError:
        raise BotError("Anthropic is rate-limiting this key right now - try again in a minute.", 429)
    except anthropic.BadRequestError as exc:
        raise BotError(f"Anthropic rejected the request: {getattr(exc, 'message', exc)}", 502)
    except anthropic.APIStatusError as exc:
        raise BotError(f"Anthropic had a problem ({exc.status_code}) - try again shortly.", 502)
    except anthropic.APIConnectionError:
        raise BotError("Could not reach Anthropic - check this machine's internet connection.", 502)

    if resp.stop_reason == "refusal":
        raise BotError("The model declined to answer that one. Try asking about the market or the ticket directly.", 422)
    text = "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise BotError("No answer came back - try again.", 502)
    if resp.stop_reason == "max_tokens":
        text += "\n\n(The answer was cut short.)"
    u = getattr(resp, "usage", None)
    meta = {"model": getattr(resp, "model", MODEL),
            "input_tokens": getattr(u, "input_tokens", None),
            "output_tokens": getattr(u, "output_tokens", None),
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", None),
            "request_id": getattr(resp, "_request_id", None)}
    return text, meta
