#!/usr/bin/env python3
"""
web_server.py — the same tool, served as a website
================================================================================
    python3 web_server.py                    # http://127.0.0.1:8080, just you
    python3 web_server.py --host 0.0.0.0     # reachable from your network
    python3 web_server.py --port 80          # if you're putting it behind a domain

Standard library only. No Flask, no npm, no build step — it starts instantly and
runs unchanged on a Mac, a Linux VPS, or Termux on a phone.

WHAT IT SHARES WITH THE DESKTOP APP
    Everything that matters. It calls the same signal_engine, the same
    explain.py, and it draws the chart with the same draw_chart() the Tk window
    uses — rendered to SVG instead of pixels. There is no second implementation
    of the strategy to drift out of sync with the first.

THINGS TO KNOW BEFORE PUTTING THIS ON THE PUBLIC INTERNET
    * There is no login and no rate limiting. Anyone who can reach the address
      sees everything on it. Do not expose it with real credentials on the box
      unless you understand that.
    * http.server is not a hardened production server. Behind nginx or Caddy on
      a small VPS is fine. Directly on port 80 facing the world is not advisable.
    * Your .env — API key, secret, access token — lives on whatever machine runs
      this. Nothing here ever sends it to a browser, but the machine itself
      becomes worth protecting.
    * India regulates investment advice. Publishing buy/sell calls to the public
      can fall under SEBI's Research Analyst / Investment Adviser rules,
      especially once money changes hands anywhere in the picture. The page
      carries a disclaimer, but a disclaimer is not a licence. See README.

*** THIS TOOL PLACES NO ORDERS. IT ONLY DISPLAYS A SUGGESTION. ***
*** NOT SEBI-REGISTERED INVESTMENT ADVICE. ***
"""

import argparse
import datetime as dt
import http.server
import json
import os
import socket
import socketserver
import threading
import time
import traceback

import hmac
import secrets
import urllib.parse

import accounts
import config
import explain
import kite_auth
import signal_engine
import trade_log
from chart_panel import chart_svg
from main import (
    fetch_recommendation, get_provider, is_market_open, now_ist,
    MissingKiteCredentials,
)

# ---------------------------------------------------------------------------
# SHARED STATE — written by the worker thread, read by every request
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state = {
    "indices": {},          # key -> {"rec": ..., "why": ..., "df": ...}
    "started": None,
    "last_error": None,
    "mode": "free",
    "market_open": False,
    "updated": None,
    "feed": "unknown",      # "ok" | "missing" | "expired" — Zerodha token health
}
_stop = threading.Event()

# Set when the operator finishes a login, so the worker retries AT ONCE instead
# of sitting out the rest of its back-off. Without this you log in successfully
# and the page keeps showing the old error for another half minute, which reads
# exactly like the login having failed.
_wake = threading.Event()


def _sleep(seconds):
    """Wait, but come back early if we're stopping or something woke us."""
    if _stop.wait(0):
        return
    if _wake.wait(seconds):
        _wake.clear()

# One-time nonces for operator logins. A login that comes back without a nonce
# this server issued is somebody else's login, and is refused — otherwise a
# stranger could complete the Zerodha flow with their own account and leave the
# server running on their credentials.
_nonces = {}
NONCE_TTL = 600


def _new_nonce():
    n = secrets.token_urlsafe(24)
    now = time.time()
    for k, t in list(_nonces.items()):        # sweep expired ones
        if now - t > NONCE_TTL:
            _nonces.pop(k, None)
    _nonces[n] = now
    return n


def _burn_nonce(n):
    """Valid at most once. Returns True only the first time."""
    t = _nonces.pop(n, None)
    return t is not None and (time.time() - t) <= NONCE_TTL


def lan_address():
    """This machine's address on the local network, for opening the site on a
    phone. Found by asking the OS which interface it would use to reach the
    outside world — no packet is actually sent, and it needs no internet.
    Beats making you run ipconfig and read off the right line."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        return ip if ip and not ip.startswith("127.") else None
    except OSError:
        return None
    finally:
        s.close()


def _admin_ok(qs):
    """Constant-time comparison, and disabled outright when no key is set."""
    key = config.WEB_ADMIN_KEY
    if not key:
        return False
    given = (qs.get("key") or [""])[0]
    return hmac.compare_digest(given, key)


def _public(rec):
    """The parts of a recommendation a browser needs. Deliberately explicit —
    dumping the whole dict would ship the candle DataFrame and the raw option
    chain to every visitor on every poll."""
    if not rec:
        return None
    tech = rec.get("technical") or {}
    trend = rec.get("trend") or {}
    return {
        "index": rec.get("index"),
        "bias": rec.get("bias"),
        "action": rec.get("action"),
        "confidence": rec.get("confidence"),
        "spot": rec.get("spot"),
        "strike": rec.get("suggested_strike"),
        "option_type": rec.get("option_type"),
        "targets": rec.get("index_targets"),
        "stop": rec.get("index_stop_loss"),
        "risk_points": rec.get("risk_points"),
        "reach_points": rec.get("reach_points"),
        "reach_to_risk": rec.get("reach_to_risk"),
        "reach_reason": rec.get("reach_reason"),
        "not_worth_it": rec.get("not_worth_it"),
        "adx_blocked": rec.get("adx_blocked"),
        "blockers": rec.get("blockers") or [],
        "votes": rec.get("votes") or {},
        "agree": rec.get("agree"), "dissent": rec.get("dissent"),
        "adx": tech.get("adx"), "adx_ok": tech.get("adx_ok"),
        "rsi": round(tech["last_rsi"], 1) if tech.get("last_rsi") is not None else None,
        "macd_hist": tech.get("macd_hist"),
        "vwap_gap": tech.get("vwap_gap"),
        "trend": {
            "label": trend.get("label"), "adx": trend.get("adx"),
            "momentum": trend.get("momentum"), "direction": trend.get("direction"),
            "displacement_atr": trend.get("displacement_atr"),
            "stalled": trend.get("stalled"),
            "day_change": trend.get("day_change"), "day_change_pct": trend.get("day_change_pct"),
            "day_high": trend.get("day_high"), "day_low": trend.get("day_low"),
            "range_pos_pct": trend.get("range_pos_pct"),
        },
    }


def worker(mode, interval, expiry=None):
    """Fetch and analyse all three indices on a loop. One thread, shared by
    every visitor — the analysis is identical for everyone, so computing it
    per-request would just be the same numbers at N times the cost (and N
    times the load on Zerodha)."""
    provider = None
    while not _stop.is_set():
        try:
            if provider is None:
                provider = get_provider(mode)
            open_now = is_market_open()
            for key in config.INSTRUMENTS:
                if _stop.is_set():
                    break
                try:
                    rec, notes = fetch_recommendation(provider, key, "15m", None,
                                                       quiet=True, expiry=expiry)
                    with _lock:
                        _state["indices"][key] = {
                            "rec": rec,
                            "public": _public(rec),
                            "why": explain.explain(rec),
                            "df": rec.get("candles"),
                            "notes": notes,
                            "at": now_ist().strftime("%H:%M:%S"),
                        }
                        _state["last_error"] = None
                except Exception as exc:
                    with _lock:
                        _state["last_error"] = f"{key}: {exc}"
                _stop.wait(1.0)
            feed = "ok"
            if mode == "kite":
                try:
                    feed, _ = kite_auth.token_status()
                except Exception:
                    feed = "unknown"
            with _lock:
                _state["market_open"] = open_now
                _state["updated"] = now_ist().strftime("%H:%M:%S")
                _state["feed"] = feed
        except MissingKiteCredentials as exc:
            with _lock:
                _state["last_error"] = str(exc)
                _state["feed"] = "missing"
            provider = None
            _sleep(30)
            continue          # retry immediately once woken, don't also sleep `interval`
        except Exception:
            with _lock:
                _state["last_error"] = traceback.format_exc(limit=2)
            provider = None
            _sleep(10)
            continue
        _sleep(interval)


# ---------------------------------------------------------------------------
# TRACK RECORD — the honest bit
# ---------------------------------------------------------------------------
def track_record():
    """Every closed trade this tool has ever logged, summarised without
    flattering. A signal site that shows only its current call and never its
    history is asking to be believed rather than checked."""
    try:
        rows = [r for r in trade_log._read_rows() if r.get("event") == "CLOSE"]
    except Exception:
        rows = []
    if not rows:
        return {"n": 0}
    def f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    pnls = [f(r.get("pnl")) for r in rows]
    priced = [p for p in pnls if p is not None]
    hit = lambda k: sum(1 for r in rows if str(r.get(k, "")).lower() == "true")
    return {
        "n": len(rows),
        "t1": round(100 * hit("t1_hit") / len(rows), 1),
        "t2": round(100 * hit("t2_hit") / len(rows), 1),
        "t3": round(100 * hit("t3_hit") / len(rows), 1),
        "sl": round(100 * hit("sl_hit") / len(rows), 1),
        "net": round(sum(priced), 0) if priced else None,
        "wins": sum(1 for p in priced if p > 0),
        "losses": sum(1 for p in priced if p < 0),
        "first": min(r.get("date", "") for r in rows),
        "last": max(r.get("date", "") for r in rows),
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "SignalTool"

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Nothing here loads anything external, so lock that down.
        self.send_header("Content-Security-Policy",
                         "default-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in getattr(self, "_extra_headers", []):
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------- accounts
    SESSION_COOKIE = "sd_session"
    OPEN_PATHS = ("/login", "/signup", "/logout", "/healthz", "/admin", "/kite/callback")

    def _cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _is_https(self):
        """Behind Caddy/nginx the connection to us is plain http, so trust the
        proxy's header for whether the USER's connection was encrypted."""
        return (self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                or (config.WEB_PUBLIC_URL or "").startswith("https://"))

    def _set_session(self, token, clear=False):
        bits = [f"{self.SESSION_COOKIE}={'' if clear else token}",
                "Path=/", "HttpOnly", "SameSite=Lax"]
        # HttpOnly: JavaScript can't read it, so an injected script can't steal
        # the session. SameSite=Lax blocks it being sent from another site.
        bits.append("Max-Age=0" if clear else f"Max-Age={accounts.SESSION_TTL}")
        if self._is_https():
            bits.append("Secure")
        self._extra_headers.append(("Set-Cookie", "; ".join(bits)))

    def _current_user(self):
        return accounts.session_user(self._cookie(self.SESSION_COOKIE))

    def _client_ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() if fwd else self.client_address[0])

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        for k, v in self._extra_headers:
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_form(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0 or n > 64_000:          # a login form is never this big
            return {}
        body = self.rfile.read(n).decode("utf-8", "replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}

    def do_POST(self):
        self._extra_headers = []
        path = self.path.split("?")[0].rstrip("/") or "/"
        form = self._read_form()
        try:
            if path == "/login":
                return self._do_login(form)
            if path == "/signup":
                return self._do_signup(form)
            if path == "/admin":
                return self._do_admin_action(form)
            return self._send("<h1>404</h1>", code=404)
        except Exception:
            return self._send(f"<pre>{traceback.format_exc(limit=3)}</pre>", code=500)

    def _do_login(self, form):
        token, err = accounts.authenticate(form.get("email"), form.get("password"),
                                           ip=self._client_ip())
        if err:
            return self._send(auth_page("login", error=err, email=form.get("email", "")))
        self._set_session(token)
        return self._redirect("/")

    def _do_signup(self, form):
        if not config.WEB_ALLOW_SIGNUP:
            return self._send("<h1>404</h1>", code=404)
        if not form.get("understood"):
            return self._send(auth_page("signup", email=form.get("email", ""),
                error="Please tick the box confirming you've read what this is."))
        ok, msg = accounts.create_user(form.get("email"), form.get("password"))
        if not ok:
            return self._send(auth_page("signup", error=msg, email=form.get("email", "")))
        token, err = accounts.authenticate(form.get("email"), form.get("password"),
                                          ip=self._client_ip())
        if err:
            return self._send(auth_page("login", error="Account created — please log in."))
        self._set_session(token)
        return self._redirect("/")

    def _do_admin_action(self, form):
        if not _admin_ok({"key": [form.get("key", "")]}):
            return self._send("<h1>404</h1>", code=404)
        action, email = form.get("action"), form.get("email")
        if action == "disable":
            accounts.set_disabled(email, True)
        elif action == "enable":
            accounts.set_disabled(email, False)
        elif action == "delete":
            accounts.delete_user(email)
        elif action == "password":
            accounts.set_password(email, form.get("password", ""))
        return self._redirect(f"/admin?key={urllib.parse.quote(form.get('key',''))}")

    def do_GET(self):
        self._extra_headers = []
        path, _, query = self.path.partition("?")
        path = path.rstrip("/") or "/"
        try:
            import urllib.parse
            qs = urllib.parse.parse_qs(query)
        except Exception:
            qs = {}
        user = self._current_user()
        if (config.WEB_REQUIRE_LOGIN and not user
                and path not in self.OPEN_PATHS and not path.startswith("/chart/")):
            return self._redirect("/login")
        # The chart is data too — gate it, or the signals leak via the image.
        if (config.WEB_REQUIRE_LOGIN and not user and path.startswith("/chart/")):
            return self._send("", "image/svg+xml", code=403)

        try:
            if path == "/login":
                if user:
                    return self._redirect("/")
                return self._send(auth_page("login"))
            if path == "/signup":
                if not config.WEB_ALLOW_SIGNUP:
                    return self._send("<h1>404</h1>", code=404)
                if user:
                    return self._redirect("/")
                return self._send(auth_page("signup"))
            if path == "/logout":
                accounts.logout(self._cookie(self.SESSION_COOKIE))
                self._set_session(None, clear=True)
                return self._redirect("/login")
            if path == "/":
                # Zerodha lets an app have exactly ONE redirect URL. Rather than
                # forcing a choice between the desktop button and the website,
                # the home page also answers to a login coming back — so an
                # existing "http://127.0.0.1:5055/" setting keeps working for
                # both, as long as the web server runs on that port.
                if qs.get("request_token") or qs.get("status"):
                    return self._callback(qs)
                return self._send(PAGE)
            if path == "/api/state":
                with _lock:
                    payload = {
                        "market_open": _state["market_open"],
                        "updated": _state["updated"],
                        "mode": _state["mode"],
                        "user": user,
                        "feed": _state["feed"],
                        "stale": _state["feed"] in ("missing", "expired"),
                        "error": _state["last_error"],
                        "indices": {k: v["public"] for k, v in _state["indices"].items()},
                        "why": {k: v["why"] for k, v in _state["indices"].items()},
                        "record": track_record(),
                        "order": list(config.INSTRUMENTS.keys()),
                    }
                return self._send(json.dumps(payload), "application/json")
            if path.startswith("/chart/") and path.endswith(".svg"):
                key = path[len("/chart/"):-len(".svg")]
                with _lock:
                    entry = _state["indices"].get(key)
                    df = entry["df"] if entry else None
                    rec = entry["rec"] if entry else None
                # The browser tells us how wide the chart will actually be, so
                # it can be drawn at that size instead of scaled (and squashed)
                # to fit afterwards. Clamped, because the width arrives from
                # the client and nothing from a client is trusted.
                try:
                    w = int(float((qs.get("w") or ["900"])[0]))
                except (TypeError, ValueError):
                    w = 900
                w = max(320, min(1600, w))
                if df is None or len(df) < 2:
                    return self._send(
                        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} 300" '
                        f'width="{w}" height="300" style="width:100%;height:auto">'
                        f'<rect width="{w}" height="300" fill="#141924"/>'
                        f'<text x="{w//2}" y="150" fill="#6b7280" font-size="14" '
                        f'text-anchor="middle" font-family="sans-serif">waiting for candles…'
                        f'</text></svg>', "image/svg+xml")
                return self._send(chart_svg(df, rec, None, width=w), "image/svg+xml")
            if path == "/healthz":
                return self._send("ok", "text/plain")
            if path == "/admin":
                return self._admin(qs)
            if path == "/kite/callback":
                return self._callback(qs)
            return self._send("<h1>404</h1>", code=404)
        except Exception:
            return self._send(f"<pre>{traceback.format_exc(limit=3)}</pre>", code=500)


    # ------------------------------------------------------------- operator
    def _admin(self, qs):
        """The operator's page: feed health, and a link that starts a login.

        Deliberately ugly and unlinked from anywhere. It is not a feature for
        visitors; it exists so the person running the server can refresh the
        Zerodha token each morning without SSH-ing in.
        """
        if not _admin_ok(qs):
            # Same response whether the key is wrong or unset — no hints.
            return self._send("<h1>404</h1>", code=404)

        state, detail = kite_auth.token_status()
        cb = config.web_callback_url()
        rows = [
            ("Data feed", {"ok": "live", "missing": "NOT LOGGED IN",
                           "expired": "TOKEN EXPIRED"}.get(state, state)),
            ("Detail", detail),
            ("Callback URL", cb or "WEB_PUBLIC_URL is not set in .env"),
            ("Mode", _state["mode"]),
            ("Last update", _state["updated"] or "never"),
            ("Last error", (_state["last_error"] or "none").split("\n")[0]),
        ]
        rows.append(("Accounts", f"{accounts.user_count()} · signup "
                     + ("OPEN to anyone" if config.WEB_ALLOW_SIGNUP else "closed")
                     + " · login " + ("required" if config.WEB_REQUIRE_LOGIN else "NOT required")))
        body = "".join(f"<tr><td><b>{k}</b></td><td>{self._esc(str(v))}</td></tr>"
                       for k, v in rows)

        key_q = self._esc((qs.get("key") or [""])[0])
        users = accounts.list_users()
        if users:
            ulist = "".join(
                f"<tr><td>{self._esc(u['email'])}</td>"
                f"<td class=mut>{self._esc(u['created'] or '')}</td>"
                f"<td class=mut>{self._esc(u['last_login'] or 'never')}</td>"
                f"<td>{'DISABLED' if u['disabled'] else 'active'}</td>"
                f"<td><form method=post action=/admin style='display:flex;gap:5px'>"
                f"<input type=hidden name=key value='{key_q}'>"
                f"<input type=hidden name=email value='{self._esc(u['email'])}'>"
                f"<button name=action value='{'enable' if u['disabled'] else 'disable'}'>"
                f"{'Enable' if u['disabled'] else 'Disable'}</button>"
                f"<button name=action value=delete>Delete</button></form></td></tr>"
                for u in users)
            body += ("</table><h3>Accounts</h3><table><tr><th>Email</th><th>Created</th>"
                     "<th>Last login</th><th>State</th><th></th></tr>" + ulist)
        else:
            body += "</table><h3>Accounts</h3><table><tr><td class=mut>Nobody has signed up yet.</td></tr>"

        body += ("<tr><td colspan=5><form method=post action=/admin "
                 "style='display:flex;gap:6px;margin-top:8px;flex-wrap:wrap'>"
                 f"<input type=hidden name=key value='{key_q}'>"
                 "<input name=email placeholder='email' required>"
                 "<input name=password type=password placeholder='new password' required>"
                 "<button name=action value=password>Set password</button>"
                 "</form><p class=mut>There is no email-based reset, so this is how a "
                 "locked-out user gets back in. It signs out all their sessions.</p>"
                 "</td></tr>")

        if not cb:
            action = ("<p style='color:#f59e0b'>Set <code>WEB_PUBLIC_URL</code> in "
                      ".env to this server's public address, and set the same value "
                      "+ <code>/kite/callback</code> as the Redirect URL on your Kite "
                      "app. Then reload this page.</p>")
        elif not config.KITE_API_KEY or not config.KITE_API_SECRET:
            action = ("<p style='color:#ef4444'>KITE_API_KEY / KITE_API_SECRET are "
                      "missing from this server's .env.</p>")
        else:
            nonce = _new_nonce()
            params = urllib.parse.urlencode({"n": nonce})
            try:
                url = kite_auth._kite(config.KITE_API_KEY).login_url()
                url += "&redirect_params=" + urllib.parse.quote(params)
                action = (f"<p><a class=btn href='{self._esc(url)}'>Log in to Zerodha"
                          f"</a></p><p class=mut>Valid for 10 minutes, single use.</p>")
            except Exception as exc:
                action = f"<p style='color:#ef4444'>{self._esc(str(exc))}</p>"

        return self._send(f"""<!doctype html><meta charset=utf-8>
<meta name=robots content=noindex><title>operator</title>
<style>body{{background:#0b0d12;color:#eef1f6;font:14px/1.6 -apple-system,sans-serif;
padding:28px;max-width:760px;margin:0 auto}}table{{border-collapse:collapse;width:100%}}
td,th{{padding:7px 10px;border-bottom:1px solid #2a3141;vertical-align:top;text-align:left}}
th{{font-size:11px;color:#657189;text-transform:uppercase;letter-spacing:.5px}}
button,input{{background:#1c2330;color:#eef1f6;border:1px solid #2a3141;border-radius:6px;
padding:6px 10px;font-size:12px;cursor:pointer}}
h3{{font-size:14px;margin:22px 0 8px}}
.btn{{display:inline-block;background:#3b82f6;color:#fff;padding:10px 18px;
border-radius:8px;text-decoration:none;font-weight:700}}
.mut{{color:#6b7280;font-size:12px}}code{{background:#1c2330;padding:1px 5px;border-radius:4px}}
</style><h2>Operator</h2><table>{body}</table>{action}
<p class=mut>The daily token is Zerodha's rule, not this tool's — they clear every
access token each morning. Log in once after about 07:30 IST and the feed stays
up for the rest of the session.</p>""")

    def _callback(self, qs):
        """Zerodha redirects the operator's browser here after login."""
        nonce = (qs.get("n") or [""])[0]
        token = (qs.get("request_token") or [""])[0]

        def page(title, colour, msg):
            return self._send(
                f"<!doctype html><meta charset=utf-8><title>{title}</title>"
                f"<body style='background:#0b0d12;color:#eef1f6;"
                f"font:15px/1.6 -apple-system,sans-serif;padding:40px;max-width:640px;"
                f"margin:0 auto'><h2 style='color:{colour}'>{title}</h2>"
                f"<p>{self._esc(msg)}</p>")

        if not token:
            return page("Login failed", "#ef4444",
                        (qs.get("message") or ["Zerodha didn't return a request token."])[0])
        if not _burn_nonce(nonce):
            # Either a replay, an expired attempt, or somebody else's login.
            return page("Refused", "#ef4444",
                        "This login wasn't started from this server's operator page, "
                        "or it has already been used. Start again from /admin.")
        try:
            access = kite_auth.exchange(config.KITE_API_KEY, config.KITE_API_SECRET, token)
            kite_auth.save_env({"KITE_API_KEY": config.KITE_API_KEY,
                                "KITE_API_SECRET": config.KITE_API_SECRET,
                                "KITE_ACCESS_TOKEN": access})
            config.apply_credentials(access_token=access)
            with _lock:
                _state["last_error"] = None
                _state["feed"] = "ok"
            _wake.set()          # the worker picks the new token up now, not in 30s
            return page("Logged in", "#2ecc71",
                        "The data feed is live again for the rest of today's session. "
                        "You can close this tab.")
        except Exception as exc:
            return page("Exchange failed", "#ef4444", str(exc))

    @staticmethod
    def _esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# THE PAGE
# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<meta name="color-scheme" content="dark">
<title>Signal Desk — Nifty · Bank Nifty · Sensex</title>
<style>
/* Colours below are not chosen by eye. The chart hues were run through a
   colour-blind separation validator; the notes in chart_panel.py say what
   failed and why these replaced it. */
:root{
  --bg:#0b0d12; --surface:#111621; --raised:#161d2b; --sunken:#0d111a;
  --bd:#222c3e; --bd-soft:#1a2333;
  --ink:#eef1f6; --ink-2:#9aa6bd; --ink-3:#657189;
  --up:#199e70; --down:#ef4444; --warn:#fab219; --accent:#9085e9;
  --ema-fast:#3987e5; --ema-slow:#c98500; --vwap:#d55181;
  --r:14px; --r-sm:10px;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px 64px}

/* ---------- header ---------- */
header{position:sticky;top:0;z-index:20;background:rgba(11,13,18,.86);
  backdrop-filter:saturate(160%) blur(12px);border-bottom:1px solid var(--bd-soft)}
.hd{max-width:1120px;margin:0 auto;padding:13px 20px;display:flex;
  align-items:center;gap:14px;justify-content:space-between}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;letter-spacing:-.2px}
.brand svg{display:block}
.brand small{display:block;font-weight:500;font-size:11px;color:var(--ink-3);
  letter-spacing:.3px;text-transform:uppercase}
.pill{display:inline-flex;align-items:center;gap:7px;background:var(--raised);
  border:1px solid var(--bd);border-radius:999px;padding:5px 12px;
  font-size:12px;font-weight:600;color:var(--ink-2);white-space:nowrap}
.beat{width:7px;height:7px;border-radius:50%;background:var(--ink-3);flex:none}
.beat.live{background:var(--up);box-shadow:0 0 0 0 rgba(25,158,112,.6);
  animation:beat 2.4s infinite}
@keyframes beat{0%{box-shadow:0 0 0 0 rgba(25,158,112,.5)}
  70%{box-shadow:0 0 0 7px rgba(25,158,112,0)}100%{box-shadow:0 0 0 0 rgba(25,158,112,0)}}

/* ---------- notices ---------- */
.notice{border-radius:var(--r);padding:14px 16px;margin:16px 0 0;font-size:13px;
  line-height:1.65;display:flex;gap:11px;align-items:flex-start}
.notice svg{flex:none;margin-top:2px}
.notice.risk{background:#1b1416;border:1px solid #3d2126;color:#f0c9cd}
.notice.risk b{color:#ff9ba3}
.notice.stale{background:#1a1610;border:1px solid #3d3218;color:#f6dfae}
.notice.stale b{color:var(--warn)}

/* ---------- index switcher ---------- */
.markets{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0 0}
.mkt{background:var(--surface);border:1px solid var(--bd);border-radius:var(--r);
  padding:13px 15px;cursor:pointer;text-align:left;color:inherit;font:inherit;
  transition:border-color .15s,background .15s;position:relative;overflow:hidden}
.mkt:hover{background:var(--raised)}
.mkt[aria-selected="true"]{border-color:var(--accent);background:var(--raised)}
.mkt[aria-selected="true"]::before{content:"";position:absolute;left:0;top:0;bottom:0;
  width:3px;background:var(--accent)}
.mkt .nm{font-size:11px;font-weight:700;letter-spacing:.8px;color:var(--ink-3);
  text-transform:uppercase}
.mkt .px{font-size:20px;font-weight:650;letter-spacing:-.4px;margin-top:3px}
.mkt .st{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;
  font-weight:650;margin-top:3px}
.chip{display:inline-block;width:7px;height:7px;border-radius:2px;flex:none}

/* ---------- layout ---------- */
.grid{display:grid;grid-template-columns:1.5fr 1fr;gap:14px;margin-top:14px;
  align-items:start}
/* The reasoning is far taller than anything beside it, so it gets its own
   full-width band and splits into two columns of self-contained rows rather
   than leaving half the page empty next to a short chart. */
/* one column, but a capped measure — see .wrow .t */
.card{background:var(--surface);border:1px solid var(--bd);border-radius:var(--r);
  padding:18px 20px}
.card + .card{margin-top:14px}
.eyebrow{font-size:10.5px;font-weight:700;letter-spacing:.9px;color:var(--ink-3);
  text-transform:uppercase;margin:0 0 10px}

/* ---------- hero ---------- */
.hero{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.hero .v{font-size:46px;font-weight:700;letter-spacing:-1.6px;line-height:1.05}
.hero .sub{color:var(--ink-2);font-size:13.5px;margin-top:9px;line-height:1.6}
.tag{display:inline-flex;align-items:center;gap:6px;border-radius:999px;
  padding:4px 11px;font-size:11.5px;font-weight:700;letter-spacing:.3px;
  border:1px solid transparent}
.tag.up{background:rgba(25,158,112,.14);color:#4fd6a3;border-color:rgba(25,158,112,.35)}
.tag.down{background:rgba(239,68,68,.13);color:#ff8f8f;border-color:rgba(239,68,68,.32)}
.tag.flat{background:var(--raised);color:var(--ink-2);border-color:var(--bd)}
.tag.warn{background:rgba(250,178,25,.12);color:#f3c869;border-color:rgba(250,178,25,.3)}

/* ---------- stat tiles ---------- */
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}
.tile{background:var(--sunken);border:1px solid var(--bd-soft);border-radius:var(--r-sm);
  padding:11px 13px}
.tile .l{font-size:10.5px;color:var(--ink-3);font-weight:600;letter-spacing:.3px}
.tile .v{font-size:18px;font-weight:650;letter-spacing:-.4px;margin-top:2px}
.tile .d{font-size:11px;color:var(--ink-3);margin-top:1px}

/* ---------- levels ---------- */
.ladder{margin-top:16px;border-top:1px solid var(--bd-soft);padding-top:14px}
.rung{display:flex;align-items:center;gap:12px;padding:7px 0;
  border-bottom:1px solid var(--bd-soft)}
.rung:last-child{border-bottom:0}
.rung .k{width:44px;font-size:11px;font-weight:700;letter-spacing:.5px;color:var(--ink-3)}
.rung .bar{flex:1;height:4px;border-radius:2px;background:var(--bd-soft);overflow:hidden}
.rung .bar i{display:block;height:100%;border-radius:2px}
.rung .n{width:96px;text-align:right;font-size:14.5px;font-weight:650;
  font-variant-numeric:tabular-nums}

/* ---------- chart ---------- */
.chartwrap{background:var(--sunken);border:1px solid var(--bd-soft);
  border-radius:var(--r-sm);padding:6px;margin-top:4px}
img.chart{width:100%;height:auto;display:block;border-radius:7px}
.legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:11px;font-size:11.5px;
  color:var(--ink-2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.key{width:15px;height:2.5px;border-radius:2px;flex:none}
.key.dash{background:repeating-linear-gradient(90deg,var(--vwap) 0 4px,transparent 4px 7px)}

/* ---------- why ---------- */
.why{display:flex;flex-direction:column;gap:0}
.wrow{display:grid;grid-template-columns:22px 62px 1fr;gap:10px;
  padding:11px 0;border-bottom:1px solid var(--bd-soft);font-size:13.5px;
  line-height:1.6;align-items:start}
.wrow:last-child{border-bottom:0}
.wrow .g{font-weight:800;font-size:13px;text-align:center;line-height:1.5}
.wrow .n{font-weight:700;color:var(--ink-2);font-size:12px;letter-spacing:.2px;
  padding-top:1px}
.wrow .t{color:var(--ink-2);max-width:78ch}
.wrow.verdict .t{color:var(--ink)}

/* ---------- record ---------- */
.rec{display:grid;grid-template-columns:repeat(auto-fit,minmax(92px,1fr));gap:9px}
.rec .tile .v{font-size:16px}

footer{color:var(--ink-3);font-size:12px;line-height:1.75;margin-top:22px;
  border-top:1px solid var(--bd-soft);padding-top:16px}

@media(max-width:900px){
  .grid{grid-template-columns:1fr}
  .markets{grid-template-columns:1fr}
  .mkt{display:flex;align-items:center;justify-content:space-between;gap:12px}
  .mkt .px{margin:0}
  .tiles{grid-template-columns:repeat(2,1fr)}
  .hero .v{font-size:36px;letter-spacing:-1.1px}
  .wrap{padding:0 14px 48px}
  .hd{padding:11px 14px;gap:10px}
  /* The strapline wrapped onto two lines and shoved the logo off-centre. */
  .brand small{display:none}
  .pill{padding:5px 10px;font-size:11.5px}
}
</style></head><body>

<header><div class="hd">
  <div class="brand">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#161d2b" stroke="#222c3e"/>
      <path d="M5 16.5l3.6-4.2 2.9 2.6 3-4.4 4.5 3.4" stroke="#9085e9"
            stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="19" cy="13.9" r="2" fill="#199e70"/>
    </svg>
    <div>Signal Desk<small>Nifty · Bank Nifty · Sensex</small></div>
  </div>
  <div class="row" style="display:flex;gap:8px;align-items:center">
    <span class="pill"><span class="beat" id="beat"></span><span id="mkt">connecting</span></span>
    <span class="pill" id="upd">—</span>
    <a class="pill" id="signout" href="/logout" style="display:none;text-decoration:none">Sign out</a>
  </div>
</div></header>

<div class="wrap">

 <div class="notice risk">
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
   <circle cx="8" cy="8" r="7" stroke="#ff9ba3" stroke-width="1.5"/>
   <path d="M8 4.6v4.2M8 11.2v.6" stroke="#ff9ba3" stroke-width="1.7" stroke-linecap="round"/>
  </svg>
  <div><b>Read before acting on anything here.</b> This is the output of a mechanical
   rule set — not advice, and not from a SEBI-registered research analyst or investment
   adviser. No orders are placed for you. <span id="honest"></span></div>
 </div>

 <div class="notice stale" id="stale" style="display:none">
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
   <path d="M8 1.8l6.4 11.4H1.6L8 1.8z" stroke="#fab219" stroke-width="1.5" stroke-linejoin="round"/>
   <path d="M8 6.4v3M8 11.4v.6" stroke="#fab219" stroke-width="1.6" stroke-linecap="round"/>
  </svg>
  <div><b>Live data feed is down.</b> <span id="stalemsg"></span>
   Everything below is the last reading before it stopped — not the current market.</div>
 </div>

 <div class="markets" id="markets" role="tablist"></div>

 <div class="card" style="margin-top:14px">
  <p class="eyebrow">Signal</p>
  <div class="hero">
   <div class="v" id="bias">—</div>
   <span class="tag flat" id="conftag" style="display:none"></span>
  </div>
  <div class="sub" id="reason"></div>
  <div class="tiles" id="tiles"></div>
  <div class="ladder" id="ladder"></div>
 </div>

 <div class="grid">
  <div class="card">
   <p class="eyebrow">Price · 15-minute candles</p>
   <div class="chartwrap"><img class="chart" id="chart" alt="Candlestick chart with moving averages and trade levels"></div>
   <div class="legend">
    <span><i class="key" style="background:var(--ema-fast)"></i>EMA 20</span>
    <span><i class="key" style="background:var(--ema-slow)"></i>EMA 50</span>
    <span><i class="key dash"></i>VWAP</span>
    <span><i class="chip" style="background:var(--up)"></i>Up candle</span>
    <span><i class="chip" style="background:var(--down)"></i>Down candle</span>
   </div>
  </div>

  <div>
   <div class="card">
    <p class="eyebrow">Market trend</p>
    <div class="hero"><div class="v" id="trend" style="font-size:25px;letter-spacing:-.7px">—</div></div>
    <div class="sub" id="trendsub" style="margin-top:5px"></div>
    <div class="tiles" style="grid-template-columns:1fr" id="trendtiles"></div>
   </div>
   <div class="card">
    <p class="eyebrow">Track record · wins and losses</p>
    <div id="record"><p style="color:var(--ink-3);font-size:13px;margin:0">
      No completed trades recorded yet.</p></div>
   </div>
  </div>
 </div>

 <div class="card" style="margin-top:14px">
  <p class="eyebrow">Why — every input, in full</p>
  <div class="why" id="why"></div>
 </div>

 <footer>
  All figures are index points and exclude brokerage, STT, slippage and option time
  decay — every one of which works against you. Past behaviour of a rule set does not
  predict its future behaviour. Options can lose their entire value. Verify every
  number with your own broker before risking money.
 </footer>
</div>

<script>
let CUR=null, LAST=null;
const $=id=>document.getElementById(id);
const num=(v,d=2)=>v===null||v===undefined||isNaN(v)?"—":
  Number(v).toLocaleString("en-IN",{minimumFractionDigits:d,maximumFractionDigits:d});
const esc=s=>String(s==null?"":s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

function markets(s){
  const w=$("markets"); const order=s.order||[];
  if(w.children.length!==order.length) w.innerHTML="";
  order.forEach((k,i)=>{
    const r=s.indices[k];
    let col="var(--ink-3)", label="waiting", px="—", cls="flat";
    if(r){
      px = r.spot!=null ? num(r.spot,0) : "—";
      if(r.bias==="BULLISH"){col="var(--up)";label="Buy CE";cls="up";}
      else if(r.bias==="BEARISH"){col="var(--down)";label="Buy PE";cls="down";}
      else if(r.not_worth_it){col="var(--warn)";label="No room";cls="warn";}
      else if(r.adx_blocked){col="var(--ink-3)";label="Trend too weak";}
      else {label="No trade";}
    }
    let el=w.children[i];
    if(!el){ el=document.createElement("button"); el.className="mkt"; el.type="button";
             el.setAttribute("role","tab"); w.appendChild(el);
             el.addEventListener("click",()=>{CUR=k;render(LAST);}); }
    el.setAttribute("aria-selected", k===CUR?"true":"false");
    el.innerHTML=`<div><div class="nm">${esc(k)}</div><div class="px">${px}</div></div>
      <div class="st" style="color:${col}"><i class="chip" style="background:${col}"></i>${esc(label)}</div>`;
  });
}

function tile(l,v,d,cls){
  return `<div class="tile"><div class="l">${esc(l)}</div>
    <div class="v${cls?" "+cls:""}" ${cls?`style="color:${cls}"`:""}>${v}</div>
    <div class="d">${esc(d||"")}</div></div>`;
}

function blank(msg,detail){
  $("bias").textContent=msg; $("bias").style.color="var(--ink-3)";
  $("conftag").style.display="none";
  $("reason").textContent=detail||"";
  $("tiles").innerHTML=""; $("ladder").innerHTML="";
  $("trend").textContent="—"; $("trend").style.color="var(--ink-3)";
  $("trendsub").textContent=""; $("trendtiles").innerHTML="";
  $("why").innerHTML=""; $("chart").removeAttribute("src");
}

function render(s){
  if(!s) return;
  // Reset ONLY when CUR isn't a real index. Resetting because an index hasn't
  // loaded yet used to bounce the tab back and — far worse — show one index's
  // signal under another index's name.
  if(!CUR || !(s.order||[]).includes(CUR)) CUR=(s.order||[])[0];
  markets(s);

  $("beat").className = "beat" + (s.market_open && !s.stale ? " live" : "");
  $("mkt").textContent = s.stale ? "feed down" : (s.market_open?"Market open":"Market closed");
  $("upd").textContent = s.updated ? s.updated+" IST" : "—";
  const so = $("signout");
  if(s.user){ so.style.display="inline-flex"; so.title = s.user; }
  else so.style.display="none";
  $("stale").style.display = s.stale ? "flex" : "none";
  $("stalemsg").textContent = s.feed==="expired"
    ? "Zerodha clears access tokens every morning and today's has not been renewed."
    : "The server is not logged in to Zerodha.";

  const r=s.indices[CUR];
  if(!r){ blank("No data yet for "+CUR,
      s.error ? "Last error — "+s.error.split("\n")[0]
              : "Still loading this index. Each is fetched in turn, so this can take a few seconds after startup."); return; }

  const bull=r.bias==="BULLISH", bear=r.bias==="BEARISH";
  $("bias").textContent = bull?"Buy CE":bear?"Buy PE":"No trade";
  $("bias").style.color = bull?"var(--up)":bear?"var(--down)":"var(--ink-3)";
  if(r.confidence && r.confidence!=="N/A"){
    $("conftag").style.display="inline-flex";
    $("conftag").className="tag "+(bull?"up":bear?"down":"flat");
    $("conftag").textContent=(bull||bear? r.strike+" "+(r.option_type==="CE"?"Call":"Put")+" · ":"")+r.confidence+" confidence";
  } else $("conftag").style.display="none";

  $("reason").innerHTML = (bull||bear)
    ? `Risking <b>${num(r.risk_points,0)}</b> points to a stop at <b>${num(r.stop,0)}</b>.`
    : ((r.blockers&&r.blockers.length) ? esc(r.blockers.join("  ")) : esc(r.action||""));

  const tr=r.trend||{}, dc=tr.day_change, dp=tr.day_change_pct;
  $("tiles").innerHTML =
    tile("Spot", num(r.spot), CUR) +
    tile("Day move", (dc==null?"—":(dc>0?"+":"")+num(dc,0)), dp==null?"":(dp>0?"+":"")+dp+"% since open",
         dc>0?"var(--up)":dc<0?"var(--down)":"") +
    tile("Trend strength", r.adx==null?"—":r.adx, r.adx==null?"":(r.adx_ok?"above the 20 gate":"below the 20 gate"),
         r.adx==null?"":(r.adx_ok?"var(--up)":"var(--warn)")) +
    tile("Reward : risk", r.reach_to_risk==null?"—":r.reach_to_risk+":1",
         r.reach_points==null?"":num(r.reach_points,0)+" pts of room",
         r.reach_to_risk==null?"":(r.reach_to_risk>=2?"var(--up)":r.reach_to_risk<0.6?"var(--down)":"var(--warn)"));

  const tg=r.targets||[null,null,null];
  const rungs=[["T1",tg[0],"var(--up)"],["T2",tg[1],"var(--up)"],["T3",tg[2],"var(--up)"],["Stop",r.stop,"var(--down)"]];
  const spread=Math.max(...rungs.map(x=>x[1]==null?0:Math.abs(x[1]-(r.spot||0))))||1;
  $("ladder").innerHTML = rungs.map(([k,v,c])=>{
    const pct=v==null?0:Math.min(100,Math.abs(v-(r.spot||0))/spread*100);
    return `<div class="rung"><div class="k">${k}</div>
      <div class="bar"><i style="width:${pct}%;background:${v==null?"transparent":c}"></i></div>
      <div class="n" style="color:${v==null?"var(--ink-3)":c}">${v==null?"—":num(v,0)}</div></div>`;
  }).join("");

  $("trend").textContent = tr.label||"—";
  $("trend").style.color = tr.direction==="UP"?"var(--up)":tr.direction==="DOWN"?"var(--down)":"var(--ink-2)";
  $("trendsub").textContent = [
      tr.adx!=null ? "ADX "+tr.adx : null,
      tr.momentum ? "momentum "+tr.momentum : null,
      tr.displacement_atr!=null ? "moved "+tr.displacement_atr+" ATR in the last 14 bars" : null
    ].filter(Boolean).join(" · ");
  if(tr.stalled) $("trend").style.color = "var(--warn)";
  $("trendtiles").innerHTML =
    tile("Day range", tr.day_low==null?"—":num(tr.day_low,0)+" – "+num(tr.day_high,0),
         tr.range_pos_pct==null?"":"now "+tr.range_pos_pct+"% up the range") +
    tile("Versus VWAP", r.vwap_gap==null?"—":(r.vwap_gap>0?"+":"")+num(r.vwap_gap,0),
         "points from today's average price",
         r.vwap_gap>0?"var(--up)":r.vwap_gap<0?"var(--down)":"");

  const w=(s.why||{})[CUR];
  if(w){
    // Glyph + name + sentence: identity never rests on colour alone.
    let rows=(w.votes||[]).map(v=>{
      const m=v.vote===null?["–","var(--ink-3)"]:v.vote>0?["▲","var(--up)"]:v.vote<0?["▼","var(--down)"]:["–","var(--ink-3)"];
      return `<div class="wrow"><div class="g" style="color:${m[1]}">${m[0]}</div>
        <div class="n">${esc(v.name)}</div><div class="t">${esc(v.text)}</div></div>`;
    }).join("");
    if(w.gate&&w.gate.text){
      const ok=w.gate.ok===true;
      rows+=`<div class="wrow"><div class="g" style="color:${ok?"var(--up)":"var(--down)"}">${ok?"✓":"✕"}</div>
        <div class="n">Gate</div><div class="t">${esc(w.gate.text)}</div></div>`;
    }
    rows+=`<div class="wrow verdict"><div class="g" style="color:var(--accent)">=</div>
      <div class="n">Verdict</div><div class="t">${esc(w.verdict||"")}</div></div>`;
    (w.levels||[]).forEach((l,i)=>{
      rows+=`<div class="wrow"><div class="g" style="color:var(--warn)">${i?"·":"▸"}</div>
        <div class="n">${i?"":"Levels"}</div><div class="t">${esc(l)}</div></div>`;
    });
    $("why").innerHTML=rows;
  }

  const cw=Math.round($("chart").parentElement.clientWidth||900);
  $("chart").src="/chart/"+encodeURIComponent(CUR)+".svg?w="+cw+"&t="+Date.now();

  const rec=s.record||{};
  $("record").innerHTML = rec.n
    ? `<div class="rec">
        ${tile("Trades", rec.n, rec.first+" → "+rec.last)}
        ${tile("Reached T1", rec.t1+"%","")}
        ${tile("Reached T2", rec.t2+"%","")}
        ${tile("Stopped out", rec.sl+"%","")}
        ${rec.net!=null?tile("Net","₹"+Math.abs(rec.net).toLocaleString("en-IN"),
            rec.wins+" wins / "+rec.losses+" losses",
            rec.net>=0?"var(--up)":"var(--down)"):""}
       </div>`
    : `<p style="color:var(--ink-3);font-size:13px;margin:0">No completed trades recorded yet.
       This fills in as signals close, and it shows losses as well as wins.</p>`;
}

async function tick(){
  try{ LAST=await (await fetch("/api/state",{cache:"no-store"})).json(); render(LAST); }
  catch(e){ $("mkt").textContent="connection lost"; $("beat").className="beat"; }
}
$("honest").textContent="A three-year backtest of this rule set on 15-minute candles "+
  "measured roughly break-even before costs and negative after them. It is published "+
  "so it can be checked, not because it is known to work.";
tick(); setInterval(tick,3000);
addEventListener("resize",()=>{clearTimeout(window._rz);
  window._rz=setTimeout(()=>render(LAST),260)});
</script>
</body></html>
"""



# ---------------------------------------------------------------------------
# LOGIN / SIGNUP PAGES
# ---------------------------------------------------------------------------
def auth_page(kind, error=None, email=""):
    """The signup form states the measured result of this rule set, above the
    fields, before anyone can create an account.

    That is not decoration and it is not legal cover. Someone signing up cannot
    run the backtest — this page is the only place they will ever learn what
    they are agreeing to look at. Leaving it out would mean strangers acting on
    numbers whose expectancy I have measured and they haven't.
    """
    signup = kind == "signup"
    esc = lambda t: (str(t or "").replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))
    disclosure = """
     <div class="disc">
      <b>Before you create an account, please read this.</b>
      <p>This site shows the output of a mechanical rule set applied to Nifty,
      Bank Nifty and Sensex. It is <b>not advice</b>, and it does not come from a
      SEBI-registered research analyst or investment adviser.</p>
      <p><b>It has been tested, and the result was not good.</b> Across three
      years and 8,837 signals on 15-minute candles, it measured roughly
      break-even before costs and <b>negative after</b> brokerage, the option
      bid-ask and time decay. Its targets are reached about a third of the time.</p>
      <p>It is published so it can be checked, not because it is known to work.
      Treat everything here as something to examine, never as something to act
      on. Options can lose their entire value.</p>
     </div>
     <label class="ack"><input type="checkbox" name="understood" value="1" required>
      I have read the above and understand this rule set tested negative after costs.</label>
    """ if signup else ""

    other = ('<a href="/login">Already have an account? Log in</a>' if signup
             else ('<a href="/signup">Create an account</a>'
                   if config.WEB_ALLOW_SIGNUP else ''))
    warn = ""
    if signup or kind == "login":
        warn = ("" if (config.WEB_PUBLIC_URL or "").startswith("https://")
                else '<p class="insecure">This page is not using HTTPS. Do not use a '
                     'password here that you use anywhere else.</p>')

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{'Create account' if signup else 'Log in'} — Signal Desk</title>
<style>
 :root{{--bg:#0b0d12;--surface:#111621;--bd:#222c3e;--ink:#eef1f6;--ink-2:#9aa6bd;
  --ink-3:#657189;--accent:#9085e9;--down:#ef4444;--warn:#fab219}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:var(--bg);color:var(--ink);display:flex;min-height:100vh;
  align-items:center;justify-content:center;padding:24px;
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
 .box{{width:100%;max-width:460px}}
 .brand{{display:flex;align-items:center;gap:10px;font-weight:700;margin-bottom:18px}}
 .brand small{{display:block;font-weight:500;font-size:11px;color:var(--ink-3);
  letter-spacing:.3px;text-transform:uppercase}}
 form{{background:var(--surface);border:1px solid var(--bd);border-radius:14px;padding:22px}}
 h1{{font-size:19px;margin:0 0 16px}}
 label.f{{display:block;font-size:12px;color:var(--ink-2);font-weight:600;margin:12px 0 5px}}
 input[type=email],input[type=password]{{width:100%;background:#0d111a;color:var(--ink);
  border:1px solid var(--bd);border-radius:9px;padding:11px 12px;font-size:15px}}
 input:focus{{outline:2px solid var(--accent);outline-offset:1px}}
 button{{width:100%;margin-top:18px;background:var(--accent);color:#fff;border:0;
  border-radius:9px;padding:12px;font-size:15px;font-weight:700;cursor:pointer}}
 .err{{background:#2a1616;border:1px solid #5c2020;color:#ffb4b4;border-radius:9px;
  padding:10px 12px;font-size:13px;margin-bottom:14px}}
 .disc{{background:#1b1416;border:1px solid #3d2126;border-radius:10px;padding:13px 15px;
  font-size:12.5px;line-height:1.6;color:#f0c9cd;margin-bottom:14px}}
 .disc b{{color:#ff9ba3}} .disc p{{margin:8px 0 0}}
 .ack{{display:flex;gap:9px;font-size:12.5px;color:var(--ink-2);align-items:flex-start;
  margin-top:12px}}
 .ack input{{margin-top:3px;flex:none;width:16px;height:16px;accent-color:var(--accent)}}
 .insecure{{color:var(--warn);font-size:12px;margin:12px 0 0}}
 .alt{{text-align:center;margin-top:14px;font-size:13px}}
 a{{color:var(--accent);text-decoration:none}}
 .hint{{color:var(--ink-3);font-size:11.5px;margin-top:6px}}
</style></head><body><div class="box">
 <div class="brand">
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
   <rect x="1" y="1" width="22" height="22" rx="6" fill="#161d2b" stroke="#222c3e"/>
   <path d="M5 16.5l3.6-4.2 2.9 2.6 3-4.4 4.5 3.4" stroke="#9085e9" stroke-width="1.9"
         stroke-linecap="round" stroke-linejoin="round"/>
   <circle cx="19" cy="13.9" r="2" fill="#199e70"/></svg>
  <div>Signal Desk<small>Nifty · Bank Nifty · Sensex</small></div>
 </div>
 <form method="post" action="/{'signup' if signup else 'login'}">
  <h1>{'Create an account' if signup else 'Log in'}</h1>
  {f'<div class="err">{esc(error)}</div>' if error else ''}
  {disclosure}
  <label class="f" for="email">Email</label>
  <input id="email" type="email" name="email" value="{esc(email)}" required autocomplete="email">
  <label class="f" for="password">Password</label>
  <input id="password" type="password" name="password" required
         autocomplete="{'new-password' if signup else 'current-password'}"
         minlength="{accounts.MIN_PASSWORD if signup else 1}">
  {f'<p class="hint">At least {accounts.MIN_PASSWORD} characters. A short phrase beats a short scramble.</p>' if signup else ''}
  {warn}
  <button type="submit">{'Create account' if signup else 'Log in'}</button>
  <div class="alt">{other}</div>
  {'<div class="alt hint">Forgotten passwords are reset by the site owner — there is no email reset yet.</div>' if not signup else ''}
 </form>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="Serve the signal tool as a website.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 = this machine only (default). 0.0.0.0 = reachable from outside.")
    ap.add_argument("--port", type=int, default=None,
                    help="defaults to the port in WEB_PUBLIC_URL, else 8080")
    ap.add_argument("--mode", choices=["free", "kite"], default="kite")
    ap.add_argument("--interval", type=int, default=None,
                    help="seconds between full refreshes")
    ap.add_argument("--require-login", action="store_true",
                    help="nobody sees the signals without an account")
    ap.add_argument("--allow-signup", action="store_true",
                    help="let visitors create their own accounts (implies --require-login)")
    args = ap.parse_args()

    # Flags win over .env, so you can try accounts for one run without
    # committing to them — editing a hidden file to see whether you like a
    # feature is a bad trade.
    if args.allow_signup:
        config.WEB_ALLOW_SIGNUP = True
        config.WEB_REQUIRE_LOGIN = True      # signup with no gate is pointless
    if args.require_login:
        config.WEB_REQUIRE_LOGIN = True

    # If WEB_PUBLIC_URL says a port, serve on that port. Otherwise the address
    # you told setup_web about and the address the server actually listens on
    # can silently disagree, and the login times out with nothing to explain it.
    port = args.port
    if port is None:
        try:
            parsed = urllib.parse.urlparse(config.WEB_PUBLIC_URL or "")
            port = parsed.port or 8080
        except Exception:
            port = 8080
    args.port = port

    interval = args.interval or (config.ANALYSIS_INTERVAL_SEC if args.mode == "kite"
                                 else config.ANALYSIS_INTERVAL_FREE_SEC)
    with _lock:
        _state["mode"] = args.mode
        _state["started"] = now_ist().isoformat()

    t = threading.Thread(target=worker, args=(args.mode, interval), daemon=True)
    t.start()

    srv = Server((args.host, args.port), Handler)
    where = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}"

    # Pull any .env stranded beside an older copy of the code into the home
    # folder, so upgrading stops costing you your credentials.
    try:
        kite_auth.migrate_env_to_home()
    except Exception:
        pass

    # If there's no admin password yet, invent one and save it rather than
    # making the operator think one up, get it wrong, or skip the step and
    # wonder later why /admin is a 404. Generated once and reused after that.
    if not config.WEB_ADMIN_KEY and args.mode == "kite":
        try:
            key = secrets.token_urlsafe(24)
            kite_auth.save_env({"WEB_ADMIN_KEY": key})
            config.WEB_ADMIN_KEY = key
            os.environ["WEB_ADMIN_KEY"] = key
            print("\n  (No admin password was set, so one was generated and saved to .env.)")
        except Exception:
            pass

    base = config.WEB_PUBLIC_URL or where
    print("=" * 70)
    print(f"  Website:  {where}")
    if config.WEB_ADMIN_KEY:
        print(f"\n  DAILY LOGIN LINK — open this each morning, keep it private:")
        print(f"    {base}/admin?key={config.WEB_ADMIN_KEY}")
        if not config.WEB_PUBLIC_URL:
            print("    (WEB_PUBLIC_URL isn't set, so that link assumes this machine.")
            print("     Run  python3 setup_web.py  if you're hosting it somewhere else.)")
    else:
        print("\n  No admin login available in free mode — none is needed.")
    if args.host == "0.0.0.0":
        lan = lan_address()
        if lan:
            print(f"\n  ON YOUR PHONE (same wifi):  http://{lan}:{args.port}")
        else:
            print("\n  Couldn't work out this machine's network address — on a Mac,")
            print("  run:  ipconfig getifaddr en0")
        print("\n  Bound to 0.0.0.0 — anyone who can reach this machine can see the site.")
        print("  Visitors need nothing; the credentials stay on this machine. But put")
        print("  it behind nginx or Caddy before pointing a real domain at it.")
    if config.WEB_REQUIRE_LOGIN:
        n = accounts.user_count()
        print(f"\n  ACCOUNTS: login required · {n} account(s) · signup "
              + ("OPEN to anyone" if config.WEB_ALLOW_SIGNUP else "closed"))
        if config.WEB_ALLOW_SIGNUP:
            print(f"    Anyone can register at {base}/signup")
        elif n == 0:
            print("    Nobody can log in yet. Create the first account with:")
            print("      python3 web_server.py --allow-signup")
        if not (config.WEB_PUBLIC_URL or "").startswith("https://"):
            print("    NOT on HTTPS — fine on your own machine, negligent on the")
            print("    open internet. Passwords would cross the network in the clear.")
    else:
        print("\n  ACCOUNTS: off — anyone who can reach the address sees everything.")
        print("    Try them with:  python3 web_server.py --allow-signup")

    print(f"\n  Mode: {args.mode}   ·   refresh every {interval}s   ·   Ctrl+C to stop")
    print("=" * 70)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        _stop.set()
        srv.server_close()


if __name__ == "__main__":
    main()
