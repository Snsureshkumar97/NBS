#!/usr/bin/env python3
"""The Market Bot: what it sends, what it never sends, its limits, and its wiring.

Fakes only - no real Anthropic call, no socket, a temporary log folder. What
matters: the snapshot is an allow-list (never the raw state, never an email or
a broker ID), an open ticket's frozen levels ride along, the rules block
reflects config, history stays sane even when the client sends it garbage, the
cooldown and daily cap actually bite, and every exception from the SDK becomes
a message a user could read rather than a stack trace.
"""
import csv
import datetime as dt
import json
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import anthropic

import config
import market_bot as mb

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


IST = mb.IST


def fake_response(text="Hold. Price has not reached T2 or the stop.", stop_reason="end_turn",
                  extra_blocks=()):
    Block = type("Block", (), {})
    blocks = list(extra_blocks)
    if text is not None:
        b = Block(); b.type = "text"; b.text = text
        blocks.append(b)
    usage = type("Usage", (), {"input_tokens": 1200, "output_tokens": 140,
                               "cache_read_input_tokens": 900})()
    resp = type("Resp", (), {})()
    resp.content, resp.stop_reason, resp.usage = blocks, stop_reason, usage
    resp.model, resp._request_id = mb.MODEL, "req_test123"
    return resp


class FakeMessages:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.resp


class FakeClient:
    def __init__(self, resp=None, exc=None):
        self.messages = FakeMessages(resp, exc)


def fake_httpx_response(status=429):
    return type("R", (), {"status_code": status, "request": object(),
                          "headers": {}})()


print("1. THE KEY")
old = os.environ.pop("ANTHROPIC_API_KEY", None)
try:
    check("no key: not present", mb.key_present() is False)
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-123"
    check("a key in the environment: present", mb.key_present() is True)
finally:
    os.environ.pop("ANTHROPIC_API_KEY", None)
    if old is not None:
        os.environ["ANTHROPIC_API_KEY"] = old

print("2. THE COOLDOWN AND THE DAILY CAP")
mb._usage.clear()
t0 = 1_800_000_000.0
ok, msg = mb.allow("a@example.invalid", now=t0)
check("the first question of the day is allowed", ok, msg)
ok, msg = mb.allow("a@example.invalid", now=t0 + 1)
check("a second question inside the cooldown is refused", not ok and "again" in msg, msg)
ok, _ = mb.allow("a@example.invalid", now=t0 + mb.MIN_GAP_S + 0.1)
check("past the cooldown, it is allowed again", ok)
check("a different account has its own cooldown, unaffected",
      mb.allow("b@example.invalid", now=t0 + 1)[0] is True)
mb._usage["a@example.invalid"]["count"] = mb.DAILY_CAP
ok, msg = mb.allow("a@example.invalid", now=t0 + 999)
check("the daily cap refuses further questions and says so",
      not ok and str(mb.DAILY_CAP) in msg, msg)
next_day = t0 + 86400
ok, _ = mb.allow("a@example.invalid", now=next_day)
check("a new day resets the count", ok)
mb._usage.clear()

print("3. SCRUBBING")
check("an email address is removed", "[removed]" in mb.scrub("contact me at trader@example.com please")
      and "example.com" not in mb.scrub("trader@example.com"))
check("a broker-ID-shaped token is removed", mb.scrub("client XYZ001 logged in") == "client [removed] logged in")
check("an index name is left alone (no digits, does not match the pattern)",
      mb.scrub("NIFTY is bullish") == "NIFTY is bullish")
check("plain numbers are left alone", mb.scrub("spot is 23217.60") == "spot is 23217.60")

print("4. A CLOSED TICKET'S ENTRY AND EXIT DON'T JUST VANISH")
# 17 Sep 2026: the live ticket is cleared to None the instant it closes (tickets.py
# sets book.trade = None), so a question about a trade that already closed had
# nothing in open_ticket AND nothing in last_ticket_if_closed - the bot said it
# had no entry/exit for a trade the user had just watched close. Fixed by reading
# the same trade log the Journal and Record read.
TL_HOME = tempfile.mkdtemp()
_old_home = os.environ.get("TRADING_TOOL_HOME")
os.environ["TRADING_TOOL_HOME"] = TL_HOME
import importlib
import trade_log
importlib.reload(trade_log)
LOG_USER = "trader@example.invalid"
log_path = trade_log.user_log_path(LOG_USER, "nse_index")
with open(log_path, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["trade_id", "event", "date", "time_ist", "index", "strike", "option_type",
               "entry", "exit", "t1_hit", "t2_hit", "t3_hit", "sl_hit", "status", "pnl",
               "lot_size", "lots", "risk_points", "reward_risk", "score", "confidence", "adx",
               "rsi", "macd_hist", "vwap_gap"])
    # NIFTY-OLD: dated before NIFTY-1/NIFTY-2 and written first, matching how a real
    # append-only log is actually ordered - _recent_trades() relies on file order to
    # get newest-first right, so an older trade belongs earlier in the file, not later.
    # No rsi/macd_hist/vwap_gap columns exist in this row at all (those joined the log
    # on 17 Sep 2026), proving the None-not-a-crash path for a genuinely pre-dated trade.
    w.writerow(["NIFTY-OLD", "OPEN", "2026-09-10", "11:10:00", "NIFTY", "23450", "PE",
               "111.1", "", "", "", "", "", "OPEN", "", "75", "1.0",
               "56.95", "1.59", "-4", "High", "32.3"])
    w.writerow(["NIFTY-OLD", "CLOSE", "2026-09-10", "11:10:00", "NIFTY", "23450", "PE",
               "111.1", "", "", "", "", "",
               "CLOSED — the tool stopped while this was open, so there is no exit price for it",
               "", "75", "1.0", "56.95", "1.59", "-4", "High", "32.3"])
    # NIFTY-1: ADX was 22.7 and confidence Medium AT ENTRY; by the time it closed the
    # signal had strengthened to 25.6 / High. Only the entry reading should surface -
    # same for RSI/MACD/VWAP, added to the log 17 Sep 2026.
    w.writerow(["NIFTY-1", "OPEN", "2026-09-17", "09:59:44", "NIFTY", "23300", "CE",
               "130.35", "", "", "", "", "", "OPEN", "", "75", "1.0",
               "77.72", "1.44", "3", "Medium", "22.7", "58.3", "1.7", "-4.2"])
    w.writerow(["NIFTY-1", "CLOSE", "2026-09-17", "12:22:47", "NIFTY", "23300", "CE",
               "130.35", "171.0", "True", "True", "False", "False",
               "CLOSED — T2 hit (full target reached)", "3048.75", "75", "1.0",
               "77.51", "1.22", "4", "High", "25.6", "71.2", "2.9", "8.0"])
    w.writerow(["NIFTY-2", "OPEN", "2026-09-17", "12:22:59", "NIFTY", "23350", "CE",
               "134.6", "", "", "", "", "", "OPEN", "", "75", "1.0",
               "43.11", "1.08", "3", "Medium", "24.9", "62.0", "0.8", "3.5"])
    w.writerow(["NIFTY-2", "CLOSE", "2026-09-17", "13:27:12", "NIFTY", "23350", "CE",
               "134.6", "95.15", "False", "False", "False", "True",
               "CLOSED — stop-loss hit", "-2958.75", "75", "1.0",
               "39.45", "0.95", "2", "Low", "17.3", "48.5", "-1.2", "-6.0"])
    w.writerow(["BANKNIFTY-1", "OPEN", "2026-09-17", "10:00:00", "BANKNIFTY", "56000", "CE",
               "200.0", "", "", "", "", "", "OPEN", "", "30", "1.0",
               "60.0", "1.3", "4", "High", "26.0", "65.0", "2.1", "5.0"])
    w.writerow(["BANKNIFTY-1", "CLOSE", "2026-09-17", "11:00:00", "BANKNIFTY", "56000", "CE",
               "200.0", "220.0", "True", "False", "False", "False",
               "CLOSED — T1 hit", "600.0", "30", "1.0",
               "58.0", "1.35", "4", "High", "27.0", "73.0", "3.0", "10.0"])
try:
    check("with no open ticket, the closed trade is not just absent",
          mb._recent_trades(LOG_USER, "nse_index", "NIFTY") != [])
    recent = mb._recent_trades(LOG_USER, "nse_index", "NIFTY")
    check("newest first", recent[0]["status"] == "CLOSED — stop-loss hit"
          and recent[1]["status"].startswith("CLOSED — T2 hit"), recent)
    check("entry, exit and the actual P&L are all there",
          recent[1]["entry"] == "130.35" and recent[1]["exit"] == "171.0" and recent[1]["pnl"] == 3048.75)
    check("which target was hit travels with it", recent[1]["t1_hit"] is True and recent[1]["t2_hit"] is True
          and recent[1]["t3_hit"] is False)
    check("scoped to the index asked about - Bank Nifty's trade does not show up under NIFTY",
          all(t.get("strike") != "56000" for t in recent))
    check("the ENTRY-time reading, not the close-time one that overwrote it in the same row",
          recent[1]["entry_adx"] == 22.7 and recent[1]["entry_confidence"] == "Medium"
          and recent[1]["entry_score"] == 3.0 and recent[1]["entry_reward_risk"] == 1.44
          and recent[1]["entry_risk_points"] == 77.72, recent[1])
    check("...specifically NOT the CLOSE row's own re-stamped values (25.6 / High / 4 / 1.22)",
          recent[1]["entry_adx"] != 25.6 and recent[1]["entry_confidence"] != "High")
    check("the second trade's own entry reading is its own, not the first trade's",
          recent[0]["entry_adx"] == 24.9 and recent[0]["entry_confidence"] == "Medium")
    check("RSI, MACD histogram and VWAP gap at entry ride along the same way, from the OPEN row",
          recent[1]["entry_rsi"] == 58.3 and recent[1]["entry_macd_hist"] == 1.7
          and recent[1]["entry_vwap_gap"] == -4.2, recent[1])
    check("...not the CLOSE row's own later reading (71.2 / 2.9 / 8.0)",
          recent[1]["entry_rsi"] != 71.2 and recent[1]["entry_vwap_gap"] != 8.0)
    old = [t for t in recent if t["strike"] == "23450"][0]
    check("a trade logged before 17 Sep 2026 (no such columns in that row at all) gives None, not a crash",
          old["entry_rsi"] is None and old["entry_macd_hist"] is None and old["entry_vwap_gap"] is None
          and old["entry_adx"] == 32.3, old)
    check("no user, no lookup - never touches disk for an anonymous call",
          mb._recent_trades(None, "nse_index", "NIFTY") == [])
    check("capped", len(mb._recent_trades(LOG_USER, "nse_index", "NIFTY")) <= mb.RECENT_TRADES_MAX)

    ctx_closed = json.loads(mb.build_context({"indices": {}, "tickets": {}}, "nse_index", "NIFTY", user=LOG_USER))
    check("build_context carries it through, even with no live ticket at all",
          ctx_closed["open_ticket"] is None
          and ctx_closed["recent_trades_this_index"][0]["pnl"] == -2958.75, ctx_closed["recent_trades_this_index"])
    check("without a user, the field is an empty list, not missing",
          json.loads(mb.build_context({"indices": {}, "tickets": {}}, "nse_index", "NIFTY"))
          ["recent_trades_this_index"] == [])
finally:
    if _old_home is None:
        os.environ.pop("TRADING_TOOL_HOME", None)
    else:
        os.environ["TRADING_TOOL_HOME"] = _old_home
    importlib.reload(trade_log)

print("5. THE SNAPSHOT: AN ALLOW-LIST, NOT THE RAW STATE")
SNAP_OPEN = {
    "indices": {
        "NIFTY": {"action": "BUY CE (Call)", "bias": "BULLISH", "spot": 23282.1, "adx": 26.2,
                  "reach_to_risk": 1.68, "risk_points": 73.0, "targets": [23331.0, 23364.0, 23398.0],
                  "stop": 23209.0, "confidence": "High", "not_a_real_field": "should not appear"},
        "BANKNIFTY": {"action": "BUY CE (Call)", "bias": "BULLISH", "spot": 56398.0, "adx": 23.9,
                     "reach_to_risk": 0.82, "risk_points": 222.0, "confidence": "Medium"},
    },
    "why": {"NIFTY": {"summary": "3 of 4 vote bullish"}},
    "tickets": {
        "NIFTY": {"ticket": {"index": "NIFTY", "strike": 23300, "option_type": "CE", "open": True,
                             "status": "OPEN", "entry": 130.35, "now": 98.9, "pnl": -2358.75,
                             "tracked_on": "premium", "lots": 1.0, "lot_size": 75,
                             "targets": [152.75, 169.55, 186.36], "stop": 91.49,
                             "index_targets": [23331.0, 23364.0, 23398.0], "index_stop": 23209.0,
                             "entry_spot": 23286.35, "hit": {"T1": False, "T2": False, "T3": False},
                             "exit_at": "T2"},
                   "wait": None},
        "BANKNIFTY": {"ticket": None, "wait": {"code": "neutral", "badge": "NO SIGNAL",
                                               "why": "The indicators do not agree yet."}},
    },
    "session": {"issued": 3, "closed_today": 1, "wins": 1, "stops": 0, "net": 210.5,
               "per_index": {"NIFTY": 90.0, "BANKNIFTY": 120.5},
               "max_trades": 4, "limits": False, "capital": 200000, "risk_pct": 2.0},
    "market_open": True, "closing_auction": False, "feed": "ok",
    "user": "me@example.invalid", "account": {"expires": "2027-01-01"},
    "kite": {"user_id": "XYZ001", "connected": True},
}

ctx = json.loads(mb.build_context(SNAP_OPEN, "nse_index", "NIFTY"))
check("only allow-listed fields on the selected index",
      set(ctx["selected"]) <= set(mb.INDEX_FIELDS), set(ctx["selected"]) - set(mb.INDEX_FIELDS))
check("a field not on the allow-list never appears", "not_a_real_field" not in ctx["selected"])
check("the open ticket rides along, with its frozen index levels",
      ctx["open_ticket"]["index_targets"] == [23331.0, 23364.0, 23398.0]
      and ctx["open_ticket"]["entry_spot"] == 23286.35)
check("a closed ticket does not also appear as open", ctx["last_ticket_if_closed"] is None)
check("the sibling index is peer-only - no risk_points, no confidence leaking through",
      set(ctx["other_indices_in_this_market"]["BANKNIFTY"]) <= set(mb.PEER_FIELDS))
check("the account, the session's identity and the broker fields never appear anywhere in the snapshot",
      "user" not in ctx and "account" not in ctx and "kite" not in ctx
      and "me@example.invalid" not in mb.build_context(SNAP_OPEN, "nse_index", "NIFTY")
      and "XYZ001" not in mb.build_context(SNAP_OPEN, "nse_index", "NIFTY"))
check("the rules block matches config", ctx["rules"]["exit_target"] == config.EXIT_AT_TARGET
      and ctx["rules"]["trend_gate_adx"] == config.strictness()["adx"])
check("the trend measure is reported per index (fast on NSE, since 17 Sep 2026)",
      "3 candles" in ctx["rules"]["trend_measure"])
# 17 Sep 2026: session_today had no per-index breakdown, so a day when more than
# one index traded left the bot unable to reconcile its own session tally against
# the selected index's own trades - it correctly said so rather than guessing,
# but the gap was real. per_index is numbers only (each index's own net for
# today), never another index's strike, entry or exit.
check("each traded index's own net for today is visible, not just the market-wide total",
      ctx["session_today"]["per_index"] == {"NIFTY": 90.0, "BANKNIFTY": 120.5})
check("account-level session fields still never appear",
      "capital" not in ctx["session_today"] and "risk_pct" not in ctx["session_today"])

SNAP_CLOSED = json.loads(json.dumps(SNAP_OPEN))
SNAP_CLOSED["tickets"]["NIFTY"]["ticket"]["open"] = False
SNAP_CLOSED["tickets"]["NIFTY"]["ticket"]["status"] = "CLOSED — T2 hit"
ctx2 = json.loads(mb.build_context(SNAP_CLOSED, "nse_index", "NIFTY"))
check("a closed ticket appears as the LAST ticket, not as an open one",
      ctx2["open_ticket"] is None and ctx2["last_ticket_if_closed"]["status"].startswith("CLOSED"))

big = dict(SNAP_OPEN)
big["why"] = {"NIFTY": "x" * 100000}
ctx3 = json.loads(mb.build_context(big, "nse_index", "NIFTY"))
check("a runaway reasoning blob is dropped rather than sent whole",
      ctx3["reasoning"] == "(too long to include)" and len(mb.build_context(big, "nse_index", "NIFTY")) < 45000)

print("6. CONVERSATION HISTORY")
check("empty or missing history is fine", mb.clean_history(None) == [] and mb.clean_history("") == [])
check("garbage JSON becomes an empty history", mb.clean_history("{not json") == [])
check("a JSON object (not a list) becomes an empty history", mb.clean_history(json.dumps({"a": 1})) == [])
good = json.dumps([{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}])
check("a clean pair round-trips", mb.clean_history(good) == [{"role": "user", "content": "hi"},
                                                              {"role": "assistant", "content": "hello"}])
dup = json.dumps([{"role": "user", "text": "a"}, {"role": "user", "text": "b"},
                  {"role": "assistant", "text": "c"}])
check("two same-role turns in a row: the second is dropped, alternation kept",
      mb.clean_history(dup) == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "c"}])
lead = json.dumps([{"role": "assistant", "text": "orphan"}, {"role": "user", "text": "hi"},
                   {"role": "assistant", "text": "hello"}])
check("history cannot start on assistant - a leading orphan is stripped",
      mb.clean_history(lead) == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
lead_only = json.dumps([{"role": "assistant", "text": "orphan"}, {"role": "user", "text": "hi"}])
check("a leading orphan with nothing usable left after it is simply dropped - not replayed as if answered",
      mb.clean_history(lead_only) == [])
trail = json.dumps([{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"},
                    {"role": "user", "text": "c"}])
check("history cannot end on user - the new question is appended after it",
      mb.clean_history(trail) == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
long_hist = json.dumps([{"role": "user" if i % 2 == 0 else "assistant", "text": f"t{i}"} for i in range(40)])
check(f"history is capped at {2 * mb.MAX_TURNS} entries", len(mb.clean_history(long_hist)) == 2 * mb.MAX_TURNS)
long_msg = json.dumps([{"role": "user", "text": "y" * 9000}, {"role": "assistant", "text": "ok"}])
check("one message is capped at 2000 characters", len(mb.clean_history(long_msg)[0]["content"]) == 2000)
leaky = json.dumps([{"role": "user", "text": "my email is trader@example.com"}, {"role": "assistant", "text": "ok"}])
check("history is scrubbed the same way a fresh question is",
      "[removed]" in mb.clean_history(leaky)[0]["content"])

print("7. ASKING - THE HAPPY PATH")
client = FakeClient(resp=fake_response("Hold until T2 or the stop; nothing has been hit yet."))
answer, meta = mb.ask("Should I hold?", [], ctx, client=client)
check("the answer text comes back", "Hold" in answer, answer)
check("usage is reported", meta["input_tokens"] == 1200 and meta["cache_read_tokens"] == 900, meta)
call = client.messages.calls[0]
check("the model and effort match the design", call["model"] == mb.MODEL
      and call["output_config"]["effort"] == mb.EFFORT)
check("the system prompt is cached", call["system"][0]["cache_control"] == {"type": "ephemeral"})
check("server-side fallback is requested (a refusal should not just dead-end)",
      call["extra_body"].get("fallbacks") == "default"
      and "server-side-fallback-2026-07-01" in call["extra_headers"].get("anthropic-beta", ""))
check("the snapshot travels inside <market_snapshot> tags with the question after it",
      call["messages"][-1]["content"].startswith("<market_snapshot>")
      and call["messages"][-1]["content"].strip().endswith("Should I hold?"))

print("8. INPUT VALIDATION - NEVER CALLS THE MODEL")
c = FakeClient(resp=fake_response())
try:
    mb.ask("   ", [], ctx, client=c); raised = False
except mb.BotError as exc:
    raised, code = True, exc.code
check("an empty question is refused before any API call", raised and code == 400 and not c.messages.calls)
c = FakeClient(resp=fake_response())
try:
    mb.ask("x" * (mb.MAX_QUESTION + 1), [], ctx, client=c); raised = False
except mb.BotError as exc:
    raised, code = True, exc.code
check("an over-long question is refused before any API call", raised and code == 400 and not c.messages.calls)

print("9. WHAT THE MODEL CAN DO WRONG, AND HOW IT SURFACES")
c = FakeClient(resp=fake_response(text=None, stop_reason="refusal"))
try:
    mb.ask("q", [], ctx, client=c); raised = False
except mb.BotError as exc:
    raised, code = True, exc.code
check("a refusal is reported plainly, not as a crash", raised and code == 422)

Thinking = type("Thinking", (), {})
th = Thinking(); th.type = "thinking"
c = FakeClient(resp=fake_response(text=None, extra_blocks=[th]))
try:
    mb.ask("q", [], ctx, client=c); raised = False
except mb.BotError as exc:
    raised = True
check("no text block at all: a message, not an empty answer or a KeyError", raised)

c = FakeClient(resp=fake_response(text="partial thought", stop_reason="max_tokens"))
answer, _ = mb.ask("q", [], ctx, client=c)
check("hitting max_tokens is disclosed in the answer itself", answer.endswith("cut short.)"), answer)

for exc_obj, want_code, note in (
    (anthropic.AuthenticationError("bad key", response=fake_httpx_response(401), body=None), 502, "bad key"),
    (anthropic.PermissionDeniedError("no access", response=fake_httpx_response(403), body=None), 502, "permission"),
    (anthropic.RateLimitError("slow down", response=fake_httpx_response(429), body=None), 429, "rate limit"),
    (anthropic.BadRequestError("bad request", response=fake_httpx_response(400), body=None), 502, "bad request"),
    (anthropic.APIStatusError("server exploded", response=fake_httpx_response(500), body=None), 502, "5xx"),
    (anthropic.APIConnectionError(request=object()), 502, "no network"),
):
    c = FakeClient(exc=exc_obj)
    try:
        mb.ask("q", [], ctx, client=c); raised, code = False, None
    except mb.BotError as e:
        raised, code = True, e.code
    check(f"{note}: turned into a BotError({want_code})", raised and code == want_code, code)

print("10. WIRED INTO THE PAGE")
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")).read()
check("GET and POST routes exist", SRC.count('if path == "/api/marketbot":') == 2)
check("the handlers are defined", "def _api_marketbot(self, user):" in SRC and "def _do_marketbot(self, form):" in SRC)
check("a sidebar entry and its own pane", 'data-tab="marketbot"' in SRC and 'data-pane="marketbot"' in SRC)
check("in the master tab list, with a readable label",
      '"watchlist", "marketbot", "market"' in SRC and 'marketbot:"Market Bot"' in SRC)
check("showing the tab checks status and focuses the box", "if(name === \"marketbot\") botOnShow();" in SRC)
check("switching index clears the conversation - one market's ticket must not bleed into another's answer",
      "function botOnIndexChange()" in SRC and "BOT.history = [];" in SRC)
check("the one place CUR changes now has a hook for it",
      "function selectIndex(k){" in SRC and 'el.addEventListener("click",()=>{ selectIndex(k); });' in SRC)
check("Enter sends, Shift+Enter does not", 'e.key === "Enter" && !e.shiftKey' in SRC)
check("the disclosure is on the page: not advice, never places an order",
      "Never places, changes or cancels an order." in SRC)

print("11. THE ENDPOINT ITSELF")
import feeds
import web_server

_real_for_user = feeds.for_user
ME = "me@example.invalid"


class FakeFeed:
    def __init__(self, snap):
        self._snap = snap
    def snapshot(self):
        return self._snap


def handler(user=ME, same_origin=True, market="nse_index", snap=SNAP_OPEN):
    feeds.for_user = lambda email, mkt=None, start=True: FakeFeed(snap)
    h = object.__new__(web_server.Handler)
    out = {}
    h._send = lambda body, ctype="text/html", code=200: out.update(body=body, code=code)
    h._current_market = lambda: market
    h._current_user = lambda: user
    h._same_origin = lambda: same_origin
    return h, out


_real_key_present, _real_allow, _real_ask = mb.key_present, mb.allow, mb.ask

try:
    mb.key_present = lambda: False
    h, out = handler()
    h._api_marketbot(ME)
    d = json.loads(out["body"])
    check("status when no key: unavailable, with a reason naming the .env file",
          d["available"] is False and ".env" in (d["reason"] or ""))
    h, out = handler(user=None)
    h._api_marketbot(None)
    check("status when signed out: unavailable, no crash", json.loads(out["body"])["available"] is False)

    h, out = handler()
    h._do_marketbot({"action": "ask", "index": "NIFTY", "question": "hi"})
    check("asking with no key configured is refused (503), never silently answered",
          out["code"] == 503, out["code"])

    mb.key_present = lambda: True
    h, out = handler(same_origin=False)
    h._do_marketbot({"action": "ask", "index": "NIFTY", "question": "hi"})
    check("a cross-site request is refused (403)", out["code"] == 403)

    h, out = handler(user=None)
    h._do_marketbot({"action": "ask", "index": "NIFTY", "question": "hi"})
    check("signed out is refused (401)", out["code"] == 401)

    h, out = handler()
    h._do_marketbot({"action": "ask", "index": "BTC", "question": "hi"})
    check("an index not traded in this market is refused (400)",
          out["code"] == 400 and json.loads(out["body"])["ok"] is False)

    mb.allow = lambda email, now=None: (False, "One question at a time - try again in a few seconds.")
    h, out = handler()
    h._do_marketbot({"action": "ask", "index": "NIFTY", "question": "hi"})
    check("the cooldown is enforced at the endpoint, not just in the library (429)", out["code"] == 429)

    mb.allow = lambda email, now=None: (True, "")
    mb.ask = lambda q, hist, context, client=None, tools_ctx=None: ("Hold - T2 or the stop have not been hit.", {"input_tokens": 1})
    h, out = handler()
    h._do_marketbot({"action": "ask", "index": "NIFTY",
                     "question": "Should I hold my NIFTY ticket?", "history": "[]"})
    d = json.loads(out["body"])
    check("a real question round-trips end to end", d["ok"] is True and "Hold" in d["answer"], d)

    def boom(q, hist, context, client=None, tools_ctx=None):
        raise mb.BotError("Anthropic is rate-limiting this key right now - try again in a minute.", 429)
    mb.ask = boom
    h, out = handler()
    h._do_marketbot({"action": "ask", "index": "NIFTY", "question": "hi"})
    check("a BotError from ask() is passed through with its own code", out["code"] == 429)
finally:
    mb.key_present, mb.allow, mb.ask = _real_key_present, _real_allow, _real_ask
    feeds.for_user = _real_for_user

print("MARKET BOT TEST PASSED" if not fails else f"MARKET BOT TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
