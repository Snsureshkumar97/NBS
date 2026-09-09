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
import feeds
import kite_auth
import nbs_site
import trade_log
import user_kite
from chart_panel import chart_svg
from main import (
    fetch_recommendation, get_provider, is_market_open, now_ist,
    MissingKiteCredentials,
)

# ---------------------------------------------------------------------------
# SHARED STATE
# ---------------------------------------------------------------------------
# The analysis loops live in feeds.py now — one per user who is actually
# watching, each running under that person's own Zerodha token. What is left
# here belongs to the server as a whole rather than to anybody's session.
_lock = threading.Lock()
_state = {
    "started": None,
    "mode": "kite",
}

# One-time nonces for Zerodha logins, each remembering WHOSE login it is.
#
# A login that comes back without a nonce this server issued is somebody
# else's and is refused. Without the email alongside it, a stranger could
# complete Zerodha's flow with their own account and have the token filed
# against another user here — so the nonce is not just proof that we started
# a login, it is the record of who we started it for.
_nonces = {}                  # nonce -> (issued_at, email; None = the operator)
NONCE_TTL = 600


def _new_nonce(email=None):
    n = secrets.token_urlsafe(24)
    now = time.time()
    for k, (t, _owner) in list(_nonces.items()):      # sweep expired ones
        if now - t > NONCE_TTL:
            _nonces.pop(k, None)
    _nonces[n] = (now, email)
    return n


def _burn_nonce(n):
    """Valid at most once. Returns (ok, email)."""
    entry = _nonces.pop(n, None)
    if not entry:
        return False, None
    issued, email = entry
    return (time.time() - issued) <= NONCE_TTL, email


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

    # Paths that need somebody logged in. Everything else is public on purpose:
    # the marketing pages are the reason the site is reachable at all, and
    # gating them would leave a visitor staring at a login form with nothing
    # anywhere telling them what they would be logging in to.
    GATED = ("/app", "/api/state", "/chart/", "/connect")

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
            if path == "/connect":
                return self._do_connect(form)
            if path == "/api/ticket":
                return self._do_ticket(form)
            return self._send(nbs_site.result_page(
                "Not found", "There is no page at that address.", ok=False,
                back="/", back_label="Go to the home page"), code=404)
        except Exception:
            return self._send(f"<pre>{traceback.format_exc(limit=3)}</pre>", code=500)

    def _do_login(self, form):
        token, err = accounts.authenticate(form.get("email"), form.get("password"),
                                           ip=self._client_ip())
        if err:
            return self._send(nbs_site.login_page(error=err,
                                                  email=form.get("email", "")))
        self._set_session(token)
        # Straight to the tool rather than the home page. Somebody who just
        # typed a password was not looking for the marketing copy.
        return self._redirect("/app")

    def _do_signup(self, form):
        if not config.WEB_ALLOW_SIGNUP:
            return self._send("<h1>404</h1>", code=404)
        if not form.get("understood"):
            return self._send(nbs_site.signup_page(
                email=form.get("email", ""),
                error="Please tick the box confirming you've read what this is."))
        ok, msg = accounts.create_user(form.get("email"), form.get("password"))
        if not ok:
            return self._send(nbs_site.signup_page(error=msg, email=form.get("email", "")))
        token, err = accounts.authenticate(form.get("email"), form.get("password"),
                                          ip=self._client_ip())
        if err:
            return self._send(nbs_site.login_page(
                notice="Account created — please log in."))
        self._set_session(token)
        return self._redirect("/app")

    def _do_admin_action(self, form):
        if not _admin_ok({"key": [form.get("key", "")]}):
            return self._send("<h1>404</h1>", code=404)
        action, email = form.get("action"), form.get("email")
        note = ""
        if action == "create":
            # The missing half of this page until now: accounts could be
            # disabled, deleted and reset here but only ever created by the
            # signup form — so closing signup, which is the sensible default,
            # left no way to make one at all.
            ok, note = accounts.create_user(email, form.get("password", ""))
            if ok:
                note = f"Created {email}. Give them that password directly."
        elif action == "disable":
            _, note = accounts.set_disabled(email, True)
        elif action == "enable":
            _, note = accounts.set_disabled(email, False)
        elif action == "delete":
            _, note = accounts.delete_user(email)
        elif action == "password":
            _, note = accounts.set_password(email, form.get("password", ""))
        elif action == "unlink":
            # Kills the stored broker token without touching the account, for
            # when a user asks you to cut it and cannot reach the site.
            _, note = user_kite.disconnect(email)
            note = f"Disconnected {email} from Zerodha."
        key = urllib.parse.quote(form.get("key", ""))
        return self._redirect(f"/admin?key={key}&note={urllib.parse.quote(note or '')}")

    def do_GET(self):
        self._extra_headers = []
        path, _, query = self.path.partition("?")
        path = path.rstrip("/") or "/"
        try:
            qs = urllib.parse.parse_qs(query)
        except Exception:
            qs = {}
        user = self._current_user()

        # One gate, checked once, instead of a condition repeated per route.
        if not user and any(path == g or path.startswith(g) for g in self.GATED):
            # The chart is data too. Gating the page but not the image it pulls
            # would leak the signals through the picture.
            if path.startswith("/chart/"):
                return self._send("", "image/svg+xml", code=403)
            return self._redirect("/login")

        try:
            # ---- the public site -----------------------------------------
            if path == "/":
                # Zerodha allows an app exactly ONE redirect URL. Rather than
                # forcing a choice between the desktop button and this site,
                # the home page also answers to a login coming back — so an
                # existing "http://127.0.0.1:5055/" setting keeps working for
                # both, as long as the web server runs on that port.
                if qs.get("request_token") or qs.get("status"):
                    return self._callback(qs)
            page = nbs_site.PAGES.get(path)
            if page:
                return self._send(page(user=user, record=track_record()))

            if path.startswith("/shot/") and path.endswith(".png"):
                return self._shot(path[len("/shot/"):-len(".png")])
            if path in ("/favicon.svg", "/favicon.ico"):
                return self._send(nbs_site.FAVICON, "image/svg+xml")
            if path == "/robots.txt":
                return self._send(nbs_site.robots_txt(self._base()), "text/plain")
            if path == "/sitemap.xml":
                return self._send(nbs_site.sitemap_xml(self._base()),
                                  "application/xml")

            # ---- accounts -------------------------------------------------
            if path == "/login":
                if user:
                    return self._redirect("/app")
                return self._send(nbs_site.login_page())
            if path == "/signup":
                if not config.WEB_ALLOW_SIGNUP:
                    return self._not_found()
                if user:
                    return self._redirect("/app")
                return self._send(nbs_site.signup_page())
            if path == "/logout":
                accounts.logout(self._cookie(self.SESSION_COOKIE))
                self._set_session(None, clear=True)
                return self._redirect("/")
            if path == "/connect":
                return self._connect(user)

            # ---- the tool itself ------------------------------------------
            if path == "/app":
                return self._send(PAGE)
            if path == "/api/state":
                return self._api_state(user)
            if path.startswith("/chart/") and path.endswith(".svg"):
                return self._chart(user, path[len("/chart/"):-len(".svg")], qs)
            if path.startswith("/api/candles/"):
                return self._candles(user, path[len("/api/candles/"):])

            # ---- plumbing --------------------------------------------------
            if path == "/healthz":
                return self._send("ok", "text/plain")
            if path == "/admin":
                return self._admin(qs)
            if path == "/kite/callback":
                return self._callback(qs)
            return self._not_found()
        except Exception:
            return self._send(f"<pre>{traceback.format_exc(limit=3)}</pre>", code=500)

    def _base(self):
        """This server's public address, for the absolute URLs a sitemap needs.

        WEB_PUBLIC_URL when it is set, because behind a proxy the Host header
        is the only other thing we have and it is supplied by the client.
        """
        if config.WEB_PUBLIC_URL:
            return config.WEB_PUBLIC_URL
        host = self.headers.get("Host", "")
        return f"{'https' if self._is_https() else 'http'}://{host}" if host else ""

    def _not_found(self):
        return self._send(nbs_site.result_page(
            "Not found", "There is no page at that address.", ok=False,
            back="/", back_label="Go to the home page"), code=404)

    # --------------------------------------------------------------- images
    def _shot(self, slug):
        """Serve one of the app screenshots.

        From a whitelist in nbs_site rather than by joining the slug onto a
        directory: "serve any png from the app folder" and "serve any file from
        the app folder" are one path-traversal bug apart, and the app folder is
        where .env used to live.
        """
        path = nbs_site.shot_path(slug)
        if not path:
            return self._send("", "image/png", code=404)
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._send("", "image/png", code=404)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        # These change only when the app is re-shot, so let a visitor's browser
        # keep them rather than re-fetching a megabyte of PNG on every page.
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------------------------------------------------------------- state
    def _api_state(self, user):
        """This user's own feed, started by the act of asking for it."""
        feed = feeds.for_user(user)
        snap = feed.snapshot()
        kite = user_kite.summary(user) if _state["mode"] != "free" else {
            "state": "ok", "detail": "", "connected": True, "user_id": "", "since": ""}
        payload = {
            "market_open": snap["market_open"],
            "updated": snap["updated"],
            "mode": _state["mode"],
            "user": user,
            "feed": snap["feed"],
            "stale": snap["feed"] in ("missing", "stale", "expired"),
            "error": snap["error"],
            "kite": kite,
            "needs_connect": not kite["connected"],
            "indices": snap["indices"],
            "why": snap["why"],
            "tickets": snap.get("tickets") or {},
            "session": snap.get("session") or {},
            "events": snap.get("events") or [],
            "record": track_record(),
            "order": list(config.INSTRUMENTS.keys()),
        }
        return self._send(json.dumps(payload), "application/json")

    def _chart(self, user, key, qs):
        feed = feeds.for_user(user)
        df, rec = feed.candles(key)
        # The browser reports how wide the chart will actually be, so it can be
        # drawn at that size instead of scaled — and squashed — to fit
        # afterwards. Clamped, because the width arrives from a client and
        # nothing from a client is trusted.
        try:
            w = int(float((qs.get("w") or ["900"])[0]))
        except (TypeError, ValueError):
            w = 900
        w = max(320, min(1600, w))
        if df is None or len(df) < 2:
            return self._send(
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} 300" '
                f'width="{w}" height="300" style="width:100%;height:auto">'
                f'<rect width="{w}" height="300" fill="#fcfcfc"/>'
                f'<text x="{w//2}" y="150" fill="#9b9b9b" font-size="14" '
                f'text-anchor="middle" font-family="sans-serif">waiting for candles…'
                f'</text></svg>', "image/svg+xml")
        return self._send(chart_svg(df, rec, None, width=w), "image/svg+xml")

    def _do_ticket(self, form):
        """The settings that used to be checkboxes on the desktop window, and
        the one manual action: clearing a ticket.

        POST rather than GET because each of these changes something. Nothing
        here places or cancels an order — there is no order to cancel. Clearing
        marks a ticket closed and logs it, which is a statement about the
        record, not about a position.
        """
        user = self._current_user()
        if not user:
            return self._redirect("/login")
        feed = feeds.for_user(user)
        book = feed.tickets

        def as_bool(name):
            v = form.get(name)
            return None if v is None else v not in ("0", "false", "False", "")

        action = form.get("action")
        if action == "clear":
            book.clear(form.get("index") or "")
        else:
            try:
                lots = int(form["lots"]) if "lots" in form else None
            except (TypeError, ValueError):
                lots = None
            book.configure(lots=lots, reentry=as_bool("reentry"),
                           auto_rearm=as_bool("auto_rearm"),
                           limits=as_bool("limits"))
        return self._send(json.dumps({"ok": True, "session": book.session()}),
                          "application/json")

    def _candles(self, user, key):
        """The bars themselves, as JSON, for the interactive chart.

        The SVG endpoint stays: it is what a browser with no JavaScript, and
        the desktop app's own renderer, both use. This one exists because a
        picture cannot be hovered — a crosshair that reads out the bar under
        the pointer has to have the bars in the browser.
        """
        import indicators as ind

        feed = feeds.for_user(user)
        df, rec = feed.candles(key)
        if df is None or len(df) < 2:
            return self._send(json.dumps({"candles": [], "index": key}),
                              "application/json")

        def col(name):
            try:
                return df[name]
            except KeyError:
                return None

        def clean(series):
            """NaN is not JSON. Indicator series begin with a warm-up window of
            them, so they are sent as nulls and the chart simply starts the
            line where the data does."""
            if series is None:
                return None
            out = []
            for v in series:
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    out.append(None)
                    continue
                out.append(None if (f != f or f in (float("inf"), float("-inf")))
                           else round(f, 2))
            return out

        try:
            ema_fast = clean(ind.ema(df["Close"], config.EMA_FAST))
            ema_slow = clean(ind.ema(df["Close"], config.EMA_SLOW))
        except Exception:
            ema_fast = ema_slow = None
        try:
            vwap = clean(ind.vwap(df))
        except Exception:
            vwap = None

        # Epoch seconds, so the browser can format them in the viewer's own
        # locale instead of us shipping pre-formatted strings we would then
        # have to keep consistent with the axis.
        times = []
        for ts in df.index:
            try:
                times.append(int(ts.timestamp()))
            except Exception:
                times.append(None)

        o, h, l, c = (clean(col("Open")), clean(col("High")),
                      clean(col("Low")), clean(col("Close")))
        v = clean(col("Volume"))
        bars = [[times[i], o[i], h[i], l[i], c[i], (v[i] if v else None)]
                for i in range(len(df))
                if None not in (times[i], o[i], h[i], l[i], c[i])]

        pub = feeds._public(rec, key) or {}
        tg = pub.get("targets") or []
        payload = {
            "index": key,
            "interval": "15m",
            "candles": bars,
            "ema_fast": ema_fast, "ema_slow": ema_slow, "vwap": vwap,
            "ema_fast_len": config.EMA_FAST, "ema_slow_len": config.EMA_SLOW,
            "levels": {
                "t1": tg[0] if len(tg) > 0 else None,
                "t2": tg[1] if len(tg) > 1 else None,
                "t3": tg[2] if len(tg) > 2 else None,
                "stop": pub.get("stop"),
                "spot": pub.get("spot"),
            },
            "action": pub.get("action"),
            "bias": pub.get("bias"),
            "strike": pub.get("strike"),
            "option_type": pub.get("option_type"),
        }
        return self._send(json.dumps(payload), "application/json")

    # -------------------------------------------------------- kite, per user
    def _connect(self, user, error=None, notice=None):
        app_ok, app_why = user_kite.app_ready()
        info = user_kite.summary(user)
        return self._send(nbs_site.connect_page(
            user, info["state"], info["detail"], user_id=info["user_id"],
            since=info["since"], app_ok=app_ok, app_why=app_why,
            error=error, notice=notice))

    def _do_connect(self, form):
        user = self._current_user()
        if not user:
            return self._redirect("/login")
        action = form.get("action")
        if action == "disconnect":
            user_kite.disconnect(user)
            feeds.wake(user)
            return self._connect(user, notice="Disconnected from Zerodha.")
        if action != "start":
            return self._connect(user)

        app_ok, app_why = user_kite.app_ready()
        if not app_ok:
            return self._connect(user, error=app_why)
        try:
            url = user_kite.login_url(_new_nonce(user))
        except Exception as exc:
            return self._connect(user, error=str(exc))
        return self._redirect(url)

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

        cb = config.web_callback_url()
        note = (qs.get("note") or [""])[0]
        key_q = self._esc((qs.get("key") or [""])[0])

        rows = [
            ("Mode", _state["mode"]),
            ("Started", _state["started"] or "just now"),
            ("Live feeds", str(len(feeds.active())) + " (one per user watching)"),
            ("Callback URL", cb or "WEB_PUBLIC_URL is not set in .env"),
            ("Kite app", "configured" if (config.KITE_API_KEY and config.KITE_API_SECRET)
                         else "KITE_API_KEY / KITE_API_SECRET MISSING"),
            ("Accounts", f"{accounts.user_count()} · signup "
                         + ("OPEN to anyone" if config.WEB_ALLOW_SIGNUP else "closed")),
        ]
        body = "".join(f"<tr><td><b>{k}</b></td><td>{self._esc(str(v))}</td></tr>"
                       for k, v in rows)
        body += "</table>"

        # Create an account. This is the only way one is made when signup is
        # closed, which is the default and the point.
        body += (f"<h3>Create an account</h3>"
                 f"<form method=post action=/admin class=row>"
                 f"<input type=hidden name=key value='{key_q}'>"
                 f"<input name=email type=email placeholder='email' required>"
                 f"<input name=password type=text placeholder='password "
                 f"({accounts.MIN_PASSWORD}+ characters)' required "
                 f"minlength={accounts.MIN_PASSWORD} size=34>"
                 f"<button name=action value=create>Create</button></form>"
                 f"<p class=mut>The password box is plain text on purpose — you "
                 f"have to read it back to them, and there is no email to send "
                 f"it in. Pick a passphrase, hand it over, and tell them to "
                 f"treat it as temporary.</p>")

        users = accounts.list_users()
        if users:
            urows = ""
            for u in users:
                info = accounts.get_user(u["email"]) or {}
                kite = ("connected " + self._esc(info.get("kite_user_id") or "")
                        if info.get("kite_token") else "—")
                urows += (
                    f"<tr><td>{self._esc(u['email'])}</td>"
                    f"<td class=mut>{self._esc(u['created'] or '')}</td>"
                    f"<td class=mut>{self._esc(u['last_login'] or 'never')}</td>"
                    f"<td class=mut>{kite}</td>"
                    f"<td>{'DISABLED' if u['disabled'] else 'active'}</td>"
                    f"<td><form method=post action=/admin class=row>"
                    f"<input type=hidden name=key value='{key_q}'>"
                    f"<input type=hidden name=email value='{self._esc(u['email'])}'>"
                    f"<button name=action value='{'enable' if u['disabled'] else 'disable'}'>"
                    f"{'Enable' if u['disabled'] else 'Disable'}</button>"
                    f"<button name=action value=unlink>Unlink Kite</button>"
                    f"<button name=action value=delete>Delete</button></form></td></tr>")
            body += ("<h3>Accounts</h3><table><tr><th>Email</th><th>Created</th>"
                     "<th>Last login</th><th>Zerodha</th><th>State</th><th></th></tr>"
                     + urows + "</table>")
        else:
            body += "<h3>Accounts</h3><p class=mut>No accounts yet.</p>"

        body += (f"<h3>Reset a password</h3>"
                 f"<form method=post action=/admin class=row>"
                 f"<input type=hidden name=key value='{key_q}'>"
                 f"<input name=email type=email placeholder='email' required>"
                 f"<input name=password type=text placeholder='new password' required "
                 f"minlength={accounts.MIN_PASSWORD} size=28>"
                 f"<button name=action value=password>Set password</button></form>"
                 f"<p class=mut>There is no email-based reset, so this is how a "
                 f"locked-out user gets back in. It signs out all their sessions.</p>")

        warn = ""
        if not cb:
            warn = ("<p class=warn>Set <code>WEB_PUBLIC_URL</code> in .env to this "
                    "server's public address, and set the same value + "
                    "<code>/kite/callback</code> as the Redirect URL on your Kite "
                    "Connect app. Until then nobody can connect Zerodha.</p>")
        elif not config.KITE_API_KEY or not config.KITE_API_SECRET:
            warn = ("<p class=warn>KITE_API_KEY / KITE_API_SECRET are missing from "
                    "this server's .env. Nobody can connect Zerodha without "
                    "them.</p>")

        return self._send(f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=robots content=noindex><title>operator</title>
<style>body{{background:#0b0d12;color:#eef1f6;font:14px/1.6 -apple-system,sans-serif;
padding:28px;max-width:900px;margin:0 auto}}table{{border-collapse:collapse;width:100%}}
td,th{{padding:7px 10px;border-bottom:1px solid #2a3141;vertical-align:middle;text-align:left}}
th{{font-size:11px;color:#657189;text-transform:uppercase;letter-spacing:.5px}}
button,input{{background:#1c2330;color:#eef1f6;border:1px solid #2a3141;border-radius:6px;
padding:6px 10px;font-size:12px;cursor:pointer;font-family:inherit}}
input{{cursor:text}} button:hover{{border-color:#387ed1}}
.row{{display:flex;gap:6px;flex-wrap:wrap;align-items:center}}
h2{{margin:0 0 4px}} h3{{font-size:14px;margin:26px 0 8px}}
.mut{{color:#6b7280;font-size:12px;margin:8px 0 0}}
.warn{{color:#fab219;background:#1a1610;border:1px solid #3d3218;border-radius:8px;
padding:10px 13px}}
.note{{color:#4ecf9c;background:#0f2119;border:1px solid #1d4a37;border-radius:8px;
padding:10px 13px;margin:0 0 16px}}
code{{background:#1c2330;padding:1px 5px;border-radius:4px}}
a{{color:#387ed1}}
</style>
<h2>Operator</h2>
<p class=mut style="margin:0 0 18px">{self._esc(nbs_site.BRAND)} · users connect
their own Zerodha accounts from <a href=/connect>/connect</a>, so there is no
daily login for you to do here.</p>
{f'<p class=note>{self._esc(note)}</p>' if note else ''}
{warn}
<table>{body}""")

    def _callback(self, qs):
        """Zerodha redirects a browser here after a login.

        The nonce says whose login it was. That is the whole security of this
        route: without it, anyone who arrived holding a request token would
        have it exchanged and filed against whichever account happened to be
        asking, including somebody else's.
        """
        nonce = (qs.get("n") or [""])[0]
        token = (qs.get("request_token") or [""])[0]
        user = self._current_user()

        def done(title, message, ok=True):
            return self._send(nbs_site.result_page(
                title, message, ok=ok, user=user,
                back="/connect" if user else "/",
                back_label="Back to the connect page" if user
                           else "Go to the home page"), code=200 if ok else 400)

        if not token:
            return done("Login failed", (qs.get("message") or
                        ["Zerodha didn't return a request token."])[0], ok=False)

        ok, owner = _burn_nonce(nonce)
        if not ok:
            return done("Refused",
                        "This login wasn't started from this server, or it has "
                        "already been used. Start again from the connect page.",
                        ok=False)

        if owner:
            # A user connecting their own account. Their session has to still
            # be the one that started it — a token exchanged into an account
            # nobody is signed in to would be a token nobody asked for.
            if owner != user:
                return done("Refused",
                            "That login was started by a different account on "
                            "this server. Log in as that account and try again.",
                            ok=False)
            good, msg = user_kite.connect(owner, token)
            if not good:
                return done("Connection failed", msg, ok=False)
            feeds.wake(owner)      # pick the token up now, not after the back-off
            return done("Connected", msg + " The feed is live for the rest of "
                        "today's session — Zerodha clears it again tomorrow "
                        "morning.")

        # No owner on the nonce means the operator's own server-level login,
        # kept for the desktop app and for running the site in shared mode.
        try:
            access = kite_auth.exchange(config.KITE_API_KEY, config.KITE_API_SECRET,
                                        token)
            kite_auth.save_env({"KITE_API_KEY": config.KITE_API_KEY,
                                "KITE_API_SECRET": config.KITE_API_SECRET,
                                "KITE_ACCESS_TOKEN": access})
            config.apply_credentials(access_token=access)
            return done("Logged in", "The server-level token has been saved. "
                        "Website users still connect their own accounts.")
        except Exception as exc:
            return done("Exchange failed", str(exc), ok=False)

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
<meta name="color-scheme" content="light">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<title>NBS Signal Tool — Nifty · Bank Nifty · Sensex</title>
<style>
/* Kite's palette, so this screen and kite.zerodha.com can sit in adjacent
   tabs without the eye having to re-calibrate between them. The up/down pair
   is #4caf50 / #ff5722, which is what Kite uses and what the chart hues were
   re-checked against for colour-blind separation — the notes in
   chart_panel.py record what the previous pair failed on. */
:root{
  --bg:#ffffff; --surface:#ffffff; --raised:#f5f5f5; --sunken:#fcfcfc;
  --bd:#e9e9e9; --bd-soft:#f0f0f0;
  --ink:#3c3c3c; --ink-2:#6c6c6c; --ink-3:#9b9b9b;
  --up:#4caf50; --down:#ff5722; --warn:#f6a500; --accent:#387ed1;
  --ema-fast:#387ed1; --ema-slow:#f6a500; --vwap:#9b59b6;
  --r:3px; --r-sm:3px;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px 64px}

/* ---------- header ---------- */
header{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.92);
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
.beat.live{background:var(--up);box-shadow:0 0 0 0 rgba(76,175,80,.55);
  animation:beat 2.4s infinite}
@keyframes beat{0%{box-shadow:0 0 0 0 rgba(76,175,80,.45)}
  70%{box-shadow:0 0 0 7px rgba(76,175,80,0)}100%{box-shadow:0 0 0 0 rgba(76,175,80,0)}}

/* ---------- notices ---------- */
.notice{border-radius:var(--r);padding:14px 16px;margin:16px 0 0;font-size:13px;
  line-height:1.65;display:flex;gap:11px;align-items:flex-start}
.notice svg{flex:none;margin-top:2px}
.notice.risk{background:#fff2ee;border:1px solid #ffc7b4;color:#8a4a33}
.notice.risk b{color:#d84315}
.notice.stale{background:#fffaf0;border:1px solid #f3e2c0;color:#7a6a48}
.notice.stale b{color:#b07d15}

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
.tag.up{background:rgba(76,175,80,.12);color:#3d8b40;border-color:rgba(76,175,80,.35)}
.tag.down{background:rgba(255,87,34,.11);color:#d84315;border-color:rgba(255,87,34,.3)}
.tag.flat{background:var(--raised);color:var(--ink-2);border-color:var(--bd)}
.tag.warn{background:rgba(246,165,0,.12);color:#b07d15;border-color:rgba(246,165,0,.3)}

/* ---------- stat tiles ---------- */
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}
.tile{background:var(--sunken);border:1px solid var(--bd-soft);border-radius:var(--r-sm);
  padding:11px 13px}
.tile .l{font-size:10.5px;color:var(--ink-3);font-weight:600;letter-spacing:.3px}
.tile .v{font-size:18px;font-weight:650;letter-spacing:-.4px;margin-top:2px}
.tile .d{font-size:11px;color:var(--ink-3);margin-top:1px}

/* ---------- levels ---------- */
/* The index/premium switch above the ladder. Two buttons rather than a
   select, because the whole point is that both readings exist and one of them
   is the number you actually pay — a collapsed dropdown hides that. */
.lswitch{display:flex;gap:6px;margin-top:16px}
.lbtn{background:var(--raised);border:1px solid var(--bd);color:var(--ink-2);
  border-radius:999px;padding:5px 13px;font-size:12px;font-weight:650;
  cursor:pointer;font-family:inherit}
.lbtn.on{background:var(--accent);border-color:var(--accent);color:#fff}
.lbtn:disabled{opacity:.45;cursor:not-allowed}
.lnote{color:var(--ink-3);font-size:12px;margin-top:10px;line-height:1.6}
.lnote b{color:var(--ink-2)}

/* The lots selector. Nothing here places an order, so this only scales the
   rupee column — it is a "what would that be worth to me" dial, not a size. */
.lots{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--ink-3)}
.lots select{background:var(--bg);color:var(--ink);border:1px solid var(--bd);
  border-radius:var(--r-sm);padding:4px 7px;font-size:12px;font-family:inherit}

/* The indicator panel — the same readings the desktop app shows down its
   right-hand side, as a bar each side of centre so agreement and dissent are
   the same shape in both directions. */
.gauges{margin-top:16px;border-top:1px solid var(--bd-soft);padding-top:6px}
.gauge{display:grid;grid-template-columns:88px 1fr 96px;gap:12px;
  align-items:center;padding:8px 0;border-bottom:1px solid var(--bd-soft)}
.gauge:last-child{border-bottom:0}
.gauge .gn{font-size:12.5px;color:var(--ink-2);font-weight:600}
.gauge .gt{height:5px;border-radius:3px;background:var(--bd-soft);
  position:relative;overflow:hidden}
.gauge .gt i{position:absolute;top:0;height:100%;border-radius:3px;
  transition:left .35s ease,width .35s ease,background .25s ease}
.gauge .gt u{position:absolute;top:-2px;bottom:-2px;left:50%;width:1px;
  background:var(--bd);transform:translateX(-.5px)}
.gauge .gv{text-align:right;font-size:12.5px;font-weight:650;
  font-variant-numeric:tabular-nums}
.gnote{color:var(--ink-3);font-size:11.5px;margin-top:9px}

/* The three boxes across the top: where the market is, what it has done
   today, and how much of the rule set agrees. */
.top3{display:grid;grid-template-columns:1.15fr 1.35fr .8fr;gap:14px;margin-top:14px}
@media(max-width:900px){.top3{grid-template-columns:1fr}}
.spark{width:100%;height:76px;display:block;margin-top:8px}
.sparkrange{display:flex;justify-content:space-between;font-size:11px;
  color:var(--ink-3);font-variant-numeric:tabular-nums;margin-top:2px}
.daymove{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
.daymove .big{font-size:25px;font-weight:700;letter-spacing:-.6px;
  font-variant-numeric:tabular-nums}
.daymove .pct{font-size:14px;font-weight:600}
.ring{display:flex;flex-direction:column;align-items:center;gap:4px;padding-top:4px}
.ring svg{display:block}
.ring .lab{font-size:12px;color:var(--ink-3)}
.ring .sub2{font-size:11.5px;color:var(--ink-3);text-align:center}
.ringtxt{font-size:22px;font-weight:700;fill:var(--ink);
  font-variant-numeric:tabular-nums}
/* The arc grows into place rather than snapping, so a change of confidence
   between polls reads as a movement instead of a different number. */
.ringarc{transition:stroke-dashoffset .5s ease, stroke .3s ease}

/* ---------- the ticket ---------- */
.thead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:2px}
.thead .eyebrow{margin:0}
.badge{font-size:10.5px;font-weight:800;letter-spacing:.7px;padding:4px 10px;
  border-radius:999px;border:1px solid transparent;white-space:nowrap}
.badge.open{background:#f1f8f2;border-color:#b5dcb7;color:#3d8b40}
.badge.hold{background:#fffaf0;border-color:#f3e2c0;color:#8a6410}
.badge.prev{background:var(--raised);border-color:var(--bd);color:var(--ink-3)}
.tclear{margin-left:auto}
.contract{font-size:13px;color:var(--ink-2);margin-top:4px}
.contract b{color:var(--ink)}
.issued{font-size:12px;color:var(--ink-3);margin-top:3px}
.tstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));
  gap:14px;margin-top:14px;padding:13px 0;border-top:1px solid var(--bd-soft);
  border-bottom:1px solid var(--bd-soft)}
.tstat .l{font-size:10.5px;letter-spacing:.6px;text-transform:uppercase;
  color:var(--ink-3);font-weight:700}
.tstat .v{font-size:19px;font-weight:700;letter-spacing:-.4px;margin-top:3px;
  font-variant-numeric:tabular-nums}
.whyhold{font-size:13px;color:var(--ink-2);margin-top:12px;line-height:1.65;
  background:var(--surface);border:1px solid var(--bd-soft);
  border-radius:var(--r-sm);padding:11px 13px}
.rung .k .tick{color:var(--up);font-weight:700}
.rung.done .k{color:var(--up)}

/* ---------- the session strip ---------- */
.session{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
  margin-top:14px;padding:14px 16px;background:var(--surface);
  border:1px solid var(--bd);border-radius:var(--r)}
.session .lbl{font-size:10.5px;letter-spacing:.7px;text-transform:uppercase;
  color:var(--ink-3);font-weight:700;flex:none}
.chips{display:flex;gap:7px;flex-wrap:wrap;flex:1}
.chip2{font-size:12px;font-weight:650;padding:4px 11px;border-radius:999px;
  border:1px solid var(--bd);background:var(--bg);
  font-variant-numeric:tabular-nums;
  /* The chips change as prices move; a hard cut between colours reads as a
     flicker, so the change is eased instead. */
  transition:color .3s ease,border-color .3s ease}
.today{text-align:right;flex:none}
.today .n{font-size:22px;font-weight:700;letter-spacing:-.5px;
  font-variant-numeric:tabular-nums}
.today .d{font-size:11px;color:var(--ink-3);margin-top:1px}
.feedline{font-size:12px;color:var(--ink-3);margin-top:9px}
.feedline span{margin-right:14px;white-space:nowrap}
@media(max-width:640px){.session{flex-direction:column;align-items:stretch}
  .today{text-align:left}}

.ladder{margin-top:12px;border-top:1px solid var(--bd-soft);padding-top:14px}
.rung{display:flex;align-items:center;gap:12px;padding:7px 0;
  border-bottom:1px solid var(--bd-soft)}
.rung:last-child{border-bottom:0}
.rung .k{width:44px;font-size:11px;font-weight:700;letter-spacing:.5px;color:var(--ink-3)}
.rung .bar{flex:1;height:4px;border-radius:2px;background:var(--bd-soft);overflow:hidden}
.rung .bar i{display:block;height:100%;border-radius:2px}
.rung .n{width:92px;text-align:right;font-size:14.5px;font-weight:650;
  font-variant-numeric:tabular-nums}
.rung .rs{width:104px;text-align:right;font-size:12px;color:var(--ink-3);
  font-variant-numeric:tabular-nums}
@media(max-width:560px){.rung .rs{display:none}}

/* ---------- chart ---------- */
.chartwrap{background:var(--bg);border:1px solid var(--bd);
  border-radius:var(--r-sm);margin-top:4px;overflow:hidden}
.chartbar{display:flex;justify-content:space-between;align-items:center;gap:10px;
  padding:7px 10px;border-bottom:1px solid var(--bd-soft);flex-wrap:wrap}
.chartlegend{display:flex;gap:13px;font-size:11.5px;color:var(--ink-3);
  flex-wrap:wrap;font-variant-numeric:tabular-nums;align-items:center}
.chartlegend b{color:var(--ink);font-weight:650}
.chartlegend .o{color:var(--ink-2)}
.chartctl{display:flex;gap:5px;flex:none}
.chartctl .lbtn{padding:3px 10px;font-size:12px;line-height:1.5}
#cv{display:block;width:100%;height:430px;cursor:crosshair;touch-action:none}
@media(max-width:640px){#cv{height:330px}}
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
  <a class="brand" href="/" style="color:inherit;text-decoration:none">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#f5f5f5" stroke="#e9e9e9"/>
      <path d="M5 16.5l3.6-4.2 2.9 2.6 3-4.4 4.5 3.4" stroke="#387ed1"
            stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="19" cy="13.9" r="2" fill="#4caf50"/>
    </svg>
    <div>NBS Signal Tool<small>Nifty · Bank Nifty · Sensex</small></div>
  </a>
  <div class="row" style="display:flex;gap:8px;align-items:center">
    <span class="pill"><span class="beat" id="beat"></span><span id="mkt">connecting</span></span>
    <span class="pill" id="upd">—</span>
    <a class="pill" id="kite" href="/connect" style="text-decoration:none">Zerodha</a>
    <a class="pill" id="signout" href="/logout" style="display:none;text-decoration:none">Sign out</a>
  </div>
</div></header>

<div class="wrap">

 <div class="notice risk">
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
   <circle cx="8" cy="8" r="7" stroke="#d84315" stroke-width="1.5"/>
   <path d="M8 4.6v4.2M8 11.2v.6" stroke="#d84315" stroke-width="1.7" stroke-linecap="round"/>
  </svg>
  <div><b>Read before acting on anything here.</b> This is the output of a mechanical
   rule set — not advice, and not from a SEBI-registered research analyst or investment
   adviser. No orders are placed for you. <span id="honest"></span></div>
 </div>

 <div class="notice stale" id="connect" style="display:none">
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
   <path d="M6.4 9.6L2.8 13.2M9.6 6.4l3.6-3.6" stroke="#b07d15" stroke-width="1.6"
         stroke-linecap="round"/>
   <path d="M4.6 6.2a2.6 2.6 0 013.7 0l1.5 1.5a2.6 2.6 0 010 3.7"
         stroke="#b07d15" stroke-width="1.6" stroke-linecap="round"/>
  </svg>
  <div><b>Your Zerodha account is not connected.</b> <span id="connectmsg"></span>
   The signals below are computed under your own broker session, so there is
   nothing to show until you connect it.
   <a href="/connect" style="color:#b07d15;font-weight:700">Connect now &rarr;</a></div>
 </div>

 <div class="notice stale" id="stale" style="display:none">
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
   <path d="M8 1.8l6.4 11.4H1.6L8 1.8z" stroke="#b07d15" stroke-width="1.5" stroke-linejoin="round"/>
   <path d="M8 6.4v3M8 11.4v.6" stroke="#b07d15" stroke-width="1.6" stroke-linecap="round"/>
  </svg>
  <div><b>Live data feed is down.</b> <span id="stalemsg"></span>
   Everything below is the last reading before it stopped — not the current market.</div>
 </div>

 <div class="markets" id="markets" role="tablist"></div>

 <div class="session" id="session" style="display:none">
  <span class="lbl">Session</span>
  <div class="chips" id="schips"></div>
  <div class="today">
   <div class="n" id="snet">—</div>
   <div class="d" id="sdetail"></div>
  </div>
 </div>
 <div class="feedline" id="sfeed"></div>

 <div class="top3">
  <div class="card">
   <p class="eyebrow">Market trend</p>
   <div class="hero"><div class="v" id="trend"
        style="font-size:23px;letter-spacing:-.5px">—</div></div>
   <div class="sub" id="trendsub" style="margin-top:5px"></div>
  </div>
  <div class="card">
   <p class="eyebrow">Day move</p>
   <div class="daymove">
    <span class="big" id="dmv">—</span><span class="pct" id="dmp"></span>
   </div>
   <canvas class="spark" id="spark"></canvas>
   <div class="sparkrange"><span id="dmlo"></span><span id="dmhi"></span></div>
  </div>
  <div class="card">
   <p class="eyebrow">Confidence</p>
   <div class="ring" id="ring"></div>
  </div>
 </div>

 <div class="card" style="margin-top:14px">
  <div class="thead">
   <p class="eyebrow" id="teyebrow">Signal</p>
   <span class="badge prev" id="tbadge" style="display:none"></span>
   <button class="lbtn tclear" id="tclear" type="button"
           style="display:none">Clear ticket</button>
  </div>
  <div class="hero">
   <div class="v" id="bias">—</div>
   <span class="tag flat" id="conftag" style="display:none"></span>
  </div>
  <div class="contract" id="tcontract" style="display:none"></div>
  <div class="issued" id="tissued" style="display:none"></div>
  <div class="tstats" id="tstats" style="display:none"></div>
  <div class="whyhold" id="twhy" style="display:none"></div>
  <div class="sub" id="reason"></div>
  <div class="tiles" id="tiles"></div>
  <div class="lswitch" id="lswitch">
   <button class="lbtn on" id="lb-index" type="button">Index points</button>
   <button class="lbtn" id="lb-premium" type="button">Option premium (LTP)</button>
   <div class="lots" id="lotswrap" style="margin-left:auto">
    <label for="lots">Lots</label>
    <select id="lots"></select>
   </div>
  </div>
  <div class="ladder" id="ladder"></div>
  <div class="lnote" id="lnote"></div>
  <div class="gauges" id="gauges"></div>
  <div class="gnote" id="gnote"></div>
 </div>

 <div class="grid">
  <div class="card">
   <p class="eyebrow">Price · 15-minute candles</p>
   <div class="chartwrap">
    <div class="chartbar">
     <div class="chartlegend" id="cvlegend"></div>
     <div class="chartctl">
      <button class="lbtn" id="cvout" type="button" title="Zoom out">&minus;</button>
      <button class="lbtn" id="cvin" type="button" title="Zoom in">+</button>
      <button class="lbtn" id="cvreset" type="button">Reset</button>
     </div>
    </div>
    <canvas id="cv" aria-label="Candlestick chart. Drag to scroll back through
     earlier candles, scroll to zoom."></canvas>
   </div>
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
    <p class="eyebrow">Today's range</p>
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
  $("lswitch").style.display="none"; $("lnote").innerHTML="";
  $("gauges").innerHTML=""; $("gnote").textContent="";
  $("ring").innerHTML=""; $("dmv").textContent="—"; $("dmp").textContent="";
  $("dmlo").textContent=""; $("dmhi").textContent="";
  $("tbadge").style.display="none"; $("tclear").style.display="none";
  $("tcontract").style.display="none"; $("tissued").style.display="none";
  $("tstats").style.display="none"; $("twhy").style.display="none";
  $("trend").textContent="—"; $("trend").style.color="var(--ink-3)";
  $("trendsub").textContent=""; $("trendtiles").innerHTML="";
  $("why").innerHTML=""; CH.data=null; CH.key=null; chartDraw();
}

// ---------------------------------------------------------------- ladder
// Two readings of the same trade. The index ladder is where the INDEX has to
// travel; the premium ladder is what the OPTION is expected to be worth when
// it gets there, starting from the live LTP of the suggested strike. The
// second one is the money, so it is not hidden behind anything.
let LMODE = "index";
let LOTS = 1;
let LOTS_SYNCED = false;

function ladder(r, tk){
  // A ticket outranks the live reading. Once one is issued its levels are
  // frozen, and showing the recalculated ones beside an open position would
  // be showing numbers that trade is not being measured against.
  if(tk && tk.open){
    const prem = tk.tracked_on === "premium";
    const base = tk.entry, tg = tk.targets || [null,null,null];
    const dp = prem ? 2 : 0;
    const per = (prem && tk.lot_size) ? tk.lot_size * (tk.lots||1) : 0;
    $("lswitch").style.display = "none";
    const rungs=[["T1",tg[0],"var(--up)"],["T2",tg[1],"var(--up)"],
                 ["T3",tg[2],"var(--up)"],["Stop",tk.stop,"var(--down)"]];
    const spread=Math.max(...rungs.map(x=>x[1]==null?0:Math.abs(x[1]-(base||0))))||1;
    $("ladder").innerHTML = rungs.map(([k,v,c])=>{
      const done = k==="Stop" ? tk.sl_hit : (tk.hit||{})[k];
      const when = k==="Stop" ? tk.sl_hit_time : (tk.hit_time||{})[k];
      const pct=v==null?0:Math.min(100,Math.abs(v-(base||0))/spread*100);
      let rs = "";
      if(per && v!=null && base!=null){
        const amt=(v-base)*per;
        rs = (amt>=0?"+":"−")+"₹"+Math.abs(Math.round(amt)).toLocaleString("en-IN");
      }
      return `<div class="rung${done?" done":""}"><div class="k">${k}${
          done?` <span class="tick">✓ ${esc(when||"")}</span>`:""}</div>
        <div class="bar"><i style="width:${done?100:pct}%;background:${v==null?"transparent":c}"></i></div>
        <div class="n" style="color:${v==null?"var(--ink-3)":c}">${v==null?"—":num(v,dp)}</div>
        <div class="rs" style="color:${rs.startsWith("+")?"var(--up)":rs?"var(--down)":"var(--ink-3)"}">${rs}</div></div>`;
    }).join("");
    $("lnote").innerHTML = prem
      ? `Tracked on the live premium of <b>${esc(String(tk.strike))} ${esc(tk.option_type)}</b>, `
        + `frozen at entry. Closes on <b>${esc(tk.exit_at||"T3")}</b> or the stop.`
      : `No live chain for this strike at entry, so the ticket is tracked on the `
        + `index itself. Closes on <b>${esc(tk.exit_at||"T3")}</b> or the stop.`;
    return;
  }

  const havePrem = r.ltp != null && (r.premium_targets||[]).some(v => v != null);
  const pb = $("lb-premium");
  pb.disabled = !havePrem;
  pb.title = havePrem ? "" : "No live option chain for this strike right now.";
  if(!havePrem && LMODE === "premium") LMODE = "index";
  $("lb-index").classList.toggle("on", LMODE === "index");
  pb.classList.toggle("on", LMODE === "premium");
  $("lswitch").style.display = (r.targets||[]).some(v => v != null) ? "flex" : "none";

  const prem = LMODE === "premium";
  const tg   = (prem ? r.premium_targets : r.targets) || [null,null,null];
  const stop = prem ? r.premium_stop : r.stop;
  const base = prem ? r.ltp : r.spot;
  const dp   = prem ? 2 : 0;      // premiums are paise; index points are not

  const rungs=[["T1",tg[0],"var(--up)"],["T2",tg[1],"var(--up)"],
               ["T3",tg[2],"var(--up)"],["Stop",stop,"var(--down)"]];
  const spread=Math.max(...rungs.map(x=>x[1]==null?0:Math.abs(x[1]-(base||0))))||1;

  // What each level is worth in rupees, at the lot size Zerodha uses for this
  // index and the number of lots chosen above. Shown only on the premium
  // ladder, because a rupee figure against an index point is meaningless.
  const per = (prem && r.lot_size) ? r.lot_size * LOTS : 0;
  $("lotswrap").style.display = per ? "flex" : "none";

  $("ladder").innerHTML = rungs.map(([k,v,c])=>{
    const pct=v==null?0:Math.min(100,Math.abs(v-(base||0))/spread*100);
    let rs = "";
    if(per && v!=null && base!=null){
      const amt = (v - base) * per;
      rs = (amt>=0?"+":"−") + "₹" + Math.abs(Math.round(amt)).toLocaleString("en-IN");
    }
    return `<div class="rung"><div class="k">${k}</div>
      <div class="bar"><i style="width:${pct}%;background:${v==null?"transparent":c}"></i></div>
      <div class="n" style="color:${v==null?"var(--ink-3)":c}">${v==null?"—":num(v,dp)}</div>
      <div class="rs" style="color:${rs.startsWith("+")?"var(--up)":rs?"var(--down)":"var(--ink-3)"}">${rs}</div></div>`;
  }).join("");

  // Lot choices come from the server's own MAX_LOTS rather than a hard-coded
  // list, so raising the cap in config raises it here too.
  const maxL = r.max_lots || 5;
  const sel = $("lots");
  if(!LOTS_SYNCED && LAST && LAST.session && LAST.session.lots){
    LOTS = LAST.session.lots; LOTS_SYNCED = true;
  }
  if(sel.options.length !== maxL){
    sel.innerHTML = "";
    for(let i=1;i<=maxL;i++) sel.add(new Option(String(i), String(i)));
  }
  sel.value = String(Math.min(LOTS, maxL));
  if(r.lot_size) $("lotswrap").title = r.lot_size + " per lot";

  // The note carries the caveat rather than a tooltip, because the premium
  // numbers are a delta approximation and saying so quietly would be worse
  // than not showing them.
  let note = "";
  if(prem){
    note = `From a live premium of <b>${num(r.ltp,2)}</b> on `
         + `<b>${esc(String(r.strike||""))} ${esc(r.option_type||"")}</b>`
         + (r.expiry ? ` (expiry ${esc(String(r.expiry))})` : "") + ". ";
    note += r.premium_source === "live"
      ? "Each level is the index target converted at roughly 0.5 delta — an "
      + "approximation that ignores time decay, so treat it as a guide rather "
      + "than a quote."
      : "Estimated from the point move; no live chain price was available for "
      + "this strike.";
  } else if(havePrem){
    note = `The suggested strike is trading at <b>${num(r.ltp,2)}</b>. `
         + "Switch to option premium to see these levels as prices.";
  } else if((r.targets||[]).some(v=>v!=null)){
    note = "No live option chain for this strike right now, so only the index "
         + "levels are available.";
  }
  $("lnote").innerHTML = note;
}

$("lb-index").onclick   = () => { LMODE="index";   if(LAST) render(LAST); };
$("lb-premium").onclick = () => { LMODE="premium"; if(LAST) render(LAST); };
$("lots").onchange = e => {
  LOTS = parseInt(e.target.value,10)||1;
  if(LAST) render(LAST);
  // Sent to the server too: the lots a ticket is issued for are frozen with
  // it, so the number has to be known there before the next one fires, not
  // only in this tab.
  fetch("/api/ticket", {method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body:new URLSearchParams({lots:String(LOTS)})}).catch(()=>{});
};

$("tclear").onclick = () => {
  if(!confirm("Clear this ticket?\n\nIt is marked closed and written to your "
            + "trade log at the current price — nothing is cancelled with your "
            + "broker, because nothing was ever placed there.")) return;
  fetch("/api/ticket", {method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body:new URLSearchParams({action:"clear", index:CUR})})
    .then(() => tick()).catch(()=>{});
};

// --------------------------------------------------------------- ticket
// A live signal and an issued ticket are different things, and the badge names
// the rule that is holding one rather than asserting a generic reason. The
// desktop learned this the hard way: it used to print "a ticket is issued when
// the direction changes" whichever of the five gates was actually holding,
// which is true for exactly one of them — the least common one.
function ticketBox(r, state){
  const tk = state && state.ticket, wait = state && state.wait;
  const open = !!(tk && tk.open);

  $("teyebrow").textContent = open ? "Signal ticket" : "Signal";
  const badge = $("tbadge");
  if(open){
    badge.style.display = ""; badge.className = "badge open";
    badge.textContent = "OPEN";
  } else if(wait && wait.badge && wait.code !== "neutral"){
    badge.style.display = ""; badge.className = "badge hold";
    badge.textContent = wait.badge;
  } else if(r.action && r.bias && r.bias !== "NEUTRAL"){
    badge.style.display = ""; badge.className = "badge prev";
    badge.textContent = "PREVIEW";
  } else badge.style.display = "none";

  $("tclear").style.display = open ? "" : "none";

  const c = $("tcontract");
  if(open){
    c.style.display = "";
    c.innerHTML = `<b>${esc(tk.index)} ${esc(String(tk.strike))} ${esc(tk.option_type)}</b>`
                + ` · tracked on ${tk.tracked_on === "premium" ? "live premium" : "the index"}`;
    $("tissued").style.display = "";
    $("tissued").textContent = `Issued ${tk.entry_time} IST · levels frozen at entry`;
  } else {
    c.style.display = "none"; $("tissued").style.display = "none";
  }

  // The stats row. Entry and Now are the two numbers a held position is
  // actually about; the rupee figure is what the difference is worth at the
  // lots this ticket was issued for — frozen with it, not with the selector.
  const st = $("tstats");
  if(open){
    st.style.display = "";
    const dp = tk.tracked_on === "premium" ? 2 : 0;
    const pnl = tk.pnl;
    const pc = pnl == null ? "var(--ink-3)" : pnl > 0 ? "var(--up)"
             : pnl < 0 ? "var(--down)" : "var(--ink-2)";
    const cell = (l,v,col) => `<div class="tstat"><div class="l">${esc(l)}</div>`
               + `<div class="v"${col?` style="color:${col}"`:""}>${v}</div></div>`;
    st.innerHTML =
        cell("Reward : risk", r.reach_to_risk==null?"—":r.reach_to_risk+" : 1")
      + cell("Entry", num(tk.entry,dp))
      + cell("Now", num(tk.now,dp))
      + cell("Spot", num(r.spot,0))
      + cell(`${tk.lots} lot${tk.lots!==1?"s":""}`,
             pnl==null ? "—"
               : (pnl>=0?"+":"−")+"₹"+Math.abs(Math.round(pnl)).toLocaleString("en-IN"),
             pc);
  } else st.style.display = "none";

  // Why nothing was issued. Shown only when there is a real rule holding it,
  // not for the ordinary case of the indicators simply disagreeing.
  const wh = $("twhy");
  if(!open && wait && wait.why && wait.code !== "neutral"){
    wh.style.display = ""; wh.textContent = wait.why;
  } else wh.style.display = "none";
}

// -------------------------------------------------------------- session
function sessionStrip(sess, order){
  if(!sess || !sess.per_index){ $("session").style.display="none";
                                $("sfeed").textContent=""; return; }
  const per = sess.per_index, keys = (order||[]).filter(k => per[k] != null);
  const money = v => (v>=0?"+":"−") + "₹"
                   + Math.abs(Math.round(v)).toLocaleString("en-IN");
  // Always shown, even at zero. The desktop keeps its session strip on screen
  // all day saying "nothing yet", and a total that appears only once you are
  // up or down is a total you cannot trust to be complete.
  $("session").style.display = "flex";

  $("schips").innerHTML = keys.length
    ? keys.map(k => {
        const v = per[k], col = v>0?"var(--up)":v<0?"var(--down)":"var(--ink-2)";
        return `<span class="chip2" style="color:${col};border-color:${
          v>0?"#b5dcb7":v<0?"#ffc7b4":"var(--bd)"}">${esc(k)} ${money(v)}</span>`;
      }).join("")
    : `<span class="chip2" style="color:var(--ink-3)">Nothing closed yet today</span>`;

  const net = sess.net || 0;
  $("snet").textContent = money(net);
  $("snet").style.color = net>0?"var(--up)":net<0?"var(--down)":"var(--ink-2)";
  $("sdetail").textContent = `booked ${money(sess.booked||0)} · open ${money(sess.open||0)}`;

  // The day's budget, stated whether or not the brake is switched on — the
  // count is worth seeing even when nothing is capping it.
  const bits = [];
  if(sess.issued != null){
    bits.push(`<span>${sess.issued} ticket${sess.issued===1?"":"s"} today`
      + (sess.limits && sess.max_trades ? ` of ${sess.max_trades}` : "") + `</span>`);
  }
  if(sess.wins != null) bits.push(`<span>${sess.wins} ran to target</span>`);
  if(sess.stops != null) bits.push(`<span>${sess.stops} stopped out</span>`);
  if(!sess.limits) bits.push(`<span>daily limits off</span>`);
  $("sfeed").innerHTML = bits.join("");
}

// ----------------------------------------------------------- confidence
// The engine reports confidence as a word, because that is how the decision
// is actually made — from the split between inputs that agreed and inputs
// that dissented. The ring shows that split as the fraction it is, and the
// word underneath, so the number cannot be read as a probability of profit.
// It is not one, and nothing here should be mistaken for one.
function ringBox(r){
  const agree = r.agree, dissent = r.dissent;
  const label = r.confidence || "—";
  // On a neutral bias the engine reports confidence as N/A, because there is
  // no call for anything to be confident about. Showing a percentage beside
  // that would be a number contradicting the word next to it, so the ring is
  // emptied instead — the vote split still gets said underneath.
  const rated = label !== "N/A" && label !== "—";
  const known = rated && agree != null && dissent != null && (agree + dissent) > 0;
  const pct = known ? Math.round(agree / (agree + dissent) * 100) : null;
  const colour = label === "High" ? "var(--up)"
               : label === "Medium" ? "var(--warn)"
               : label === "Low" ? "var(--down)" : "var(--ink-3)";
  const R = 34, C = 2 * Math.PI * R;
  const off = pct == null ? C : C * (1 - pct/100);
  $("ring").innerHTML = `
    <svg width="92" height="92" viewBox="0 0 92 92" role="img"
         aria-label="Confidence ${pct==null?"unavailable":pct+" percent"}, ${esc(label)}">
      <circle cx="46" cy="46" r="${R}" fill="none" stroke="var(--bd-soft)" stroke-width="9"/>
      <circle class="ringarc" cx="46" cy="46" r="${R}" fill="none" stroke="${colour}"
              stroke-width="9" stroke-linecap="round"
              stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"
              transform="rotate(-90 46 46)"/>
      <text class="ringtxt" x="46" y="46" text-anchor="middle" dominant-baseline="central"
        >${pct==null?"—":pct+"%"}</text>
    </svg>
    <div class="lab" style="color:${colour};font-weight:650">${esc(label)}</div>
    <div class="sub2">${(agree != null && dissent != null && (agree+dissent) > 0)
        ? agree+" of "+(agree+dissent)+" inputs agree"
        : "no vote yet"}</div>`;
}

// -------------------------------------------------------------- sparkline
// Today's closes only. A sparkline that quietly ran across yesterday too
// would make an opening gap look like a move that happened during the day.
function sparkline(){
  const el = $("spark"), g = el.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = el.clientWidth, h = el.clientHeight;
  if(el.width !== Math.round(w*dpr) || el.height !== Math.round(h*dpr)){
    el.width = Math.round(w*dpr); el.height = Math.round(h*dpr);
  }
  g.setTransform(dpr,0,0,dpr,0,0);
  g.clearRect(0,0,w,h);

  const bars = ((CH.data||{}).candles) || [];
  const blank = () => { $("dmv").textContent="—"; $("dmp").textContent="";
                        $("dmlo").textContent=""; $("dmhi").textContent=""; };
  if(!bars.length){ blank(); return; }
  const lastDay = new Date(bars[bars.length-1][0]*1000).toDateString();
  const today = bars.filter(b => new Date(b[0]*1000).toDateString() === lastDay);
  if(today.length < 2){ blank(); return; }

  const open = today[0][1], close = today[today.length-1][4];
  const chg = close - open, pct = open ? chg/open*100 : 0;
  const upC = css("--up"), downC = css("--down"), col = chg >= 0 ? upC : downC;
  let lo = Infinity, hi = -Infinity;
  for(const b of today){ if(b[3]<lo) lo=b[3]; if(b[2]>hi) hi=b[2]; }
  const span = (hi-lo) || 1;
  const X = i => (i/(today.length-1)) * (w-2) + 1;
  const Y = v => h - 4 - (v-lo)/span * (h-8);

  // A filled area under the line, faded out, so the direction of the day is
  // legible at a glance rather than only from the sign of the number.
  const grad = g.createLinearGradient(0,0,0,h);
  grad.addColorStop(0, col + "33"); grad.addColorStop(1, col + "00");
  g.beginPath(); g.moveTo(X(0), Y(today[0][4]));
  today.forEach((b,i) => g.lineTo(X(i), Y(b[4])));
  g.lineTo(X(today.length-1), h); g.lineTo(X(0), h); g.closePath();
  g.fillStyle = grad; g.fill();

  g.beginPath(); g.moveTo(X(0), Y(today[0][4]));
  today.forEach((b,i) => g.lineTo(X(i), Y(b[4])));
  g.strokeStyle = col; g.lineWidth = 1.6; g.lineJoin = "round"; g.stroke();

  // the open, as the line everything today is measured against
  g.save(); g.setLineDash([3,3]); g.strokeStyle = css("--ink-3"); g.lineWidth = 1;
  g.beginPath(); g.moveTo(0, Math.round(Y(open))+0.5);
  g.lineTo(w, Math.round(Y(open))+0.5); g.stroke(); g.restore();

  const f = v => v.toLocaleString("en-IN",{maximumFractionDigits:2});
  $("dmv").textContent = (chg>=0?"+":"−") + f(Math.abs(chg));
  $("dmv").style.color = col;
  $("dmp").textContent = "(" + (chg>=0?"+":"−") + Math.abs(pct).toFixed(2) + "%)";
  $("dmp").style.color = col;
  $("dmlo").textContent = "low " + f(lo);
  $("dmhi").textContent = "high " + f(hi);
}

// --------------------------------------------------------------- gauges
// Each reading as a bar either side of a centre line: right and green for
// agreement with the call, left and red against it, nothing at all for an
// input that abstained. A dash is not a neutral vote — the engine ignores it
// entirely, and the note under the panel says so rather than leaving a reader
// to assume a blank bar was counted as zero.
function gauges(r, why){
  const rows = [];
  (why && why.votes || []).forEach(v => {
    rows.push([v.name, v.vote, v.reading || "", v.vote===null?"var(--ink-3)"
               : v.vote>0?"var(--up)":v.vote<0?"var(--down)":"var(--ink-3)"]);
  });
  if(why && why.gate){
    const ok = why.gate.ok;
    rows.push(["ADX Gate", ok===true?1:ok===false?-1:null,
               (r.adx==null?"—":r.adx + (ok===true?" PASS":ok===false?" WEAK":"")),
               ok===true?"var(--up)":ok===false?"var(--down)":"var(--ink-3)"]);
  }
  if(r.reach_points != null){
    rows.push(["Room to Run", r.reach_to_risk!=null && r.reach_to_risk>=1 ? 1
               : r.reach_to_risk!=null ? -1 : null,
               num(r.reach_points,0) + " pts",
               r.reach_to_risk==null?"var(--ink-3)"
               : r.reach_to_risk>=1?"var(--up)":"var(--warn)"]);
  }
  if(!rows.length){ $("gauges").innerHTML=""; $("gnote").textContent=""; return; }

  $("gauges").innerHTML = rows.map(([name,vote,reading,colour]) => {
    // Magnitude is capped: these votes are small integers, and a bar that
    // grew without limit would say more about the scale than the reading.
    const mag = vote==null ? 0 : Math.min(1, Math.abs(vote)/2);
    const w = mag*50;
    const left = vote>0 ? 50 : 50-w;
    return `<div class="gauge">
      <div class="gn">${esc(name)}</div>
      <div class="gt"><u></u><i style="left:${left}%;width:${w}%;background:${colour}"></i></div>
      <div class="gv" style="color:${colour}">${esc(reading)||"—"}</div>
    </div>`;
  }).join("");
  $("gnote").textContent = "A dash is an input that abstained — it is ignored, not counted as neutral.";
}

// =====================================================================
// THE CHART
// =====================================================================
// Drawn here rather than fetched as a picture, because a picture cannot be
// scrolled and cannot tell you what the bar under your pointer was. Hand-rolled
// on a canvas rather than pulled from a charting library: the page's content
// security policy allows no third-party script, and the rest of this tool has
// no build step to bundle one into.
//
// The server keeps a deep window of bars (see feeds.py), so dragging left
// really does walk back through earlier sessions rather than running out after
// the indicator warm-up.
const CH = {
  key:null, data:null, at:0,
  i0:0, n:130,          // first visible bar, and how many are visible
  pinned:true,          // stuck to the right edge until the user drags away
  hover:null, drag:null, pinch:null,
};
const CH_MIN_BARS = 20, CH_MAX_BARS = 600;
const PAD = {l:0, r:64, t:10, b:24};

const cv = $("cv"), cx = cv.getContext("2d");

function chartWant(key){
  const stale = Date.now() - CH.at > 30000;
  if(key === CH.key && !stale){ chartDraw(); return; }
  const first = key !== CH.key;
  CH.key = key;
  CH.at = Date.now();
  fetch("/api/candles/" + encodeURIComponent(key), {cache:"no-store"})
    .then(r => r.json())
    .then(d => {
      if(CH.key !== key) return;          // the user switched index mid-flight
      CH.data = d;
      const len = (d.candles||[]).length;
      if(first || CH.pinned){
        CH.n = Math.min(CH.n, Math.max(CH_MIN_BARS, len));
        CH.i0 = Math.max(0, len - CH.n);  // newest bars, which is where you look
        if(first) CH.pinned = true;
      }
      chartDraw();
      sparkline();
    })
    .catch(() => {});
}

function chartSize(){
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if(cv.width !== Math.round(w*dpr) || cv.height !== Math.round(h*dpr)){
    cv.width = Math.round(w*dpr); cv.height = Math.round(h*dpr);
  }
  cx.setTransform(dpr,0,0,dpr,0,0);
  return {w, h};
}

// A gridline step that lands on a number a person would have chosen: 1, 2 or
// 5 times a power of ten, never 1.37.
function niceStep(range, want){
  const raw = range / Math.max(1, want);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  return (norm > 5 ? 10 : norm > 2 ? 5 : norm > 1 ? 2 : 1) * mag;
}

function css(v){ return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }

function chartDraw(){
  const {w,h} = chartSize();
  const C = {
    ink: css("--ink"), ink2: css("--ink-2"), ink3: css("--ink-3"),
    bd: css("--bd"), bdSoft: css("--bd-soft"), bg: css("--bg"),
    up: css("--up"), down: css("--down"), warn: css("--warn"),
    accent: css("--accent"), fast: css("--ema-fast"), slow: css("--ema-slow"),
    vwap: css("--vwap"),
  };
  cx.clearRect(0,0,w,h);
  cx.fillStyle = C.bg; cx.fillRect(0,0,w,h);

  const d = CH.data, bars = (d && d.candles) || [];
  if(!bars.length){
    cx.fillStyle = C.ink3; cx.font = "13px -apple-system,sans-serif";
    cx.textAlign = "center";
    cx.fillText("waiting for candles…", w/2, h/2);
    $("cvlegend").textContent = "";
    return;
  }

  CH.n  = Math.max(CH_MIN_BARS, Math.min(CH_MAX_BARS, Math.min(CH.n, bars.length)));
  CH.i0 = Math.max(0, Math.min(bars.length - CH.n, CH.i0));
  const i0 = CH.i0, i1 = Math.min(bars.length, i0 + CH.n);
  const view = bars.slice(i0, i1);

  const plotW = w - PAD.l - PAD.r, plotH = h - PAD.t - PAD.b;
  const bw = plotW / view.length;

  // ---- price range over what is actually on screen -------------------
  let lo = Infinity, hi = -Infinity;
  for(const b of view){ if(b[3] < lo) lo = b[3]; if(b[2] > hi) hi = b[2]; }
  const overlays = [d.ema_fast, d.ema_slow, d.vwap];
  for(const arr of overlays){
    if(!arr) continue;
    for(let i=i0;i<i1;i++){ const v=arr[i];
      if(v!=null){ if(v<lo) lo=v; if(v>hi) hi=v; } }
  }
  // Levels are drawn, so they are included — but only when they are near
  // enough not to squash the candles into a band. An ATR-derived target
  // always is; a stale one from another session might not be.
  const L = (d.levels)||{};
  const span0 = (hi - lo) || 1;
  for(const v of [L.t1,L.t2,L.t3,L.stop]){
    if(v==null) continue;
    if(v > hi && v - hi > span0*0.9) continue;
    if(v < lo && lo - v > span0*0.9) continue;
    if(v<lo) lo=v; if(v>hi) hi=v;
  }
  const pad = (hi-lo||1) * 0.08; lo -= pad; hi += pad;
  const span = hi - lo || 1;
  const Y = v => PAD.t + (hi - v) / span * plotH;
  const X = i => PAD.l + (i - i0 + 0.5) * bw;

  // ---- horizontal grid + price axis ----------------------------------
  cx.font = "11px -apple-system,sans-serif";
  cx.textBaseline = "middle";
  const step = niceStep(span, Math.max(3, Math.round(plotH/62)));
  cx.lineWidth = 1;
  for(let v = Math.ceil(lo/step)*step; v <= hi; v += step){
    const y = Math.round(Y(v)) + 0.5;
    cx.strokeStyle = C.bdSoft;
    cx.beginPath(); cx.moveTo(PAD.l, y); cx.lineTo(w - PAD.r, y); cx.stroke();
    cx.fillStyle = C.ink3; cx.textAlign = "left";
    cx.fillText(v.toLocaleString("en-IN",{maximumFractionDigits: step<1?2:0}),
                w - PAD.r + 7, y);
  }

  // ---- vertical grid + time axis --------------------------------------
  const fmtT = t => new Date(t*1000).toLocaleTimeString("en-IN",
                     {hour:"2-digit", minute:"2-digit", hour12:false});
  const fmtD = t => new Date(t*1000).toLocaleDateString("en-IN",
                     {day:"2-digit", month:"short"});
  const everyN = Math.max(1, Math.round(view.length / Math.max(2, Math.floor(plotW/86))));
  cx.textAlign = "center"; cx.textBaseline = "top";
  let lastDay = null;
  for(let k=0;k<view.length;k++){
    const t = view[k][0];
    const day = new Date(t*1000).toDateString();
    const newDay = lastDay !== null && day !== lastDay;
    lastDay = day;
    if(k % everyN !== 0 && !newDay) continue;
    const x = Math.round(X(i0+k)) + 0.5;
    cx.strokeStyle = newDay ? C.bd : C.bdSoft;
    cx.beginPath(); cx.moveTo(x, PAD.t); cx.lineTo(x, PAD.t+plotH); cx.stroke();
    cx.fillStyle = C.ink3;
    cx.fillText(newDay ? fmtD(t) : fmtT(t), x, PAD.t+plotH+6);
  }

  // ---- overlays --------------------------------------------------------
  function line(arr, colour, dash){
    if(!arr) return;
    cx.save(); cx.strokeStyle = colour; cx.lineWidth = 1.4;
    cx.setLineDash(dash||[]); cx.beginPath();
    let started = false;
    for(let i=i0;i<i1;i++){
      const v = arr[i]; if(v == null){ started = false; continue; }
      const x = X(i), y = Y(v);
      if(!started){ cx.moveTo(x,y); started = true; } else cx.lineTo(x,y);
    }
    cx.stroke(); cx.restore();
  }
  line(d.vwap, C.vwap, [4,3]);
  line(d.ema_slow, C.slow);
  line(d.ema_fast, C.fast);

  // ---- candles ---------------------------------------------------------
  const body = Math.max(1, Math.min(bw*0.68, 14));
  for(let k=0;k<view.length;k++){
    const [t,o,hg,lw,c] = view[k];
    const up = c >= o, colour = up ? C.up : C.down;
    const x = X(i0+k);
    cx.strokeStyle = colour; cx.fillStyle = colour; cx.lineWidth = 1;
    cx.beginPath();
    cx.moveTo(Math.round(x)+0.5, Y(hg)); cx.lineTo(Math.round(x)+0.5, Y(lw));
    cx.stroke();
    const yo = Y(o), yc = Y(c);
    const top = Math.min(yo,yc), tall = Math.max(1, Math.abs(yc-yo));
    if(bw < 2.2) { cx.fillRect(Math.round(x), top, 1, tall); }
    else if(up) { // hollow up candles, the way Kite draws them
      cx.fillStyle = C.bg;
      cx.fillRect(x-body/2, top, body, tall);
      cx.strokeRect(Math.round(x-body/2)+0.5, Math.round(top)+0.5,
                    Math.round(body), Math.round(tall));
    } else {
      cx.fillRect(x-body/2, top, body, tall);
    }
  }

  // ---- levels ----------------------------------------------------------
  function level(v, label, colour){
    if(v == null) return;
    const y = Y(v); if(y < PAD.t-1 || y > PAD.t+plotH+1) return;
    cx.save();
    cx.strokeStyle = colour; cx.lineWidth = 1; cx.setLineDash([5,4]);
    cx.beginPath(); cx.moveTo(PAD.l, Math.round(y)+0.5);
    cx.lineTo(w-PAD.r, Math.round(y)+0.5); cx.stroke();
    cx.setLineDash([]);
    cx.fillStyle = colour;
    cx.fillRect(w-PAD.r, y-8, PAD.r, 16);
    cx.fillStyle = "#fff"; cx.font = "10px -apple-system,sans-serif";
    cx.textAlign = "left"; cx.textBaseline = "middle";
    cx.fillText(label + " " + Math.round(v).toLocaleString("en-IN"),
                w-PAD.r+4, y);
    cx.restore();
  }
  level(L.t1, "T1", C.up); level(L.t2, "T2", C.up); level(L.t3, "T3", C.up);
  level(L.stop, "SL", C.down);

  // ---- last price ------------------------------------------------------
  const last = bars[bars.length-1];
  if(i1 >= bars.length){
    const y = Y(last[4]);
    cx.save();
    cx.strokeStyle = C.ink3; cx.setLineDash([2,3]); cx.lineWidth = 1;
    cx.beginPath(); cx.moveTo(PAD.l, Math.round(y)+0.5);
    cx.lineTo(w-PAD.r, Math.round(y)+0.5); cx.stroke();
    cx.setLineDash([]);
    cx.fillStyle = last[4] >= last[1] ? C.up : C.down;
    cx.fillRect(w-PAD.r, y-9, PAD.r, 18);
    cx.fillStyle = "#fff"; cx.font = "600 11px -apple-system,sans-serif";
    cx.textAlign = "left"; cx.textBaseline = "middle";
    cx.fillText(last[4].toLocaleString("en-IN",{maximumFractionDigits:2}),
                w-PAD.r+5, y);
    cx.restore();
  }

  // ---- crosshair -------------------------------------------------------
  let readout = last, hoverIdx = bars.length-1;
  if(CH.hover){
    const k = Math.max(0, Math.min(view.length-1,
                Math.floor((CH.hover.x - PAD.l) / bw)));
    hoverIdx = i0 + k; readout = bars[hoverIdx];
    const x = Math.round(X(hoverIdx)) + 0.5;
    const y = Math.max(PAD.t, Math.min(PAD.t+plotH, CH.hover.y));
    cx.save();
    cx.strokeStyle = C.ink3; cx.setLineDash([3,3]); cx.lineWidth = 1;
    cx.beginPath(); cx.moveTo(x, PAD.t); cx.lineTo(x, PAD.t+plotH); cx.stroke();
    cx.beginPath(); cx.moveTo(PAD.l, Math.round(y)+0.5);
    cx.lineTo(w-PAD.r, Math.round(y)+0.5); cx.stroke();
    cx.setLineDash([]);
    // price under the pointer, on the axis
    const pv = hi - (y - PAD.t) / plotH * span;
    cx.fillStyle = C.ink; cx.fillRect(w-PAD.r, y-9, PAD.r, 18);
    cx.fillStyle = "#fff"; cx.font = "11px -apple-system,sans-serif";
    cx.textAlign = "left"; cx.textBaseline = "middle";
    cx.fillText(pv.toLocaleString("en-IN",{maximumFractionDigits:2}), w-PAD.r+5, y);
    // time under the pointer, on the bottom axis
    const lbl = fmtD(readout[0]) + " " + fmtT(readout[0]);
    cx.font = "11px -apple-system,sans-serif"; cx.textAlign = "center";
    const tw = cx.measureText(lbl).width + 12;
    cx.fillStyle = C.ink;
    cx.fillRect(Math.min(w-PAD.r-tw/2, Math.max(tw/2, x))-tw/2, PAD.t+plotH+2, tw, 17);
    cx.fillStyle = "#fff"; cx.textBaseline = "top";
    cx.fillText(lbl, Math.min(w-PAD.r-tw/2, Math.max(tw/2, x)), PAD.t+plotH+6);
    cx.restore();
  }

  // ---- the OHLC readout, in the bar above the canvas -------------------
  const f = v => v==null ? "—" : v.toLocaleString("en-IN",{maximumFractionDigits:2});
  const chg = readout[4] - readout[1];
  const pc  = readout[1] ? (chg/readout[1]*100) : 0;
  const cc  = chg >= 0 ? C.up : C.down;
  $("cvlegend").innerHTML =
    `<span class="o">${esc(d.index||"")} · 15m</span>`
  + `<span>O <b>${f(readout[1])}</b></span>`
  + `<span>H <b>${f(readout[2])}</b></span>`
  + `<span>L <b>${f(readout[3])}</b></span>`
  + `<span>C <b>${f(readout[4])}</b></span>`
  + `<span style="color:${cc}">${chg>=0?"+":""}${f(chg)} (${chg>=0?"+":""}${pc.toFixed(2)}%)</span>`
  + `<span class="o">EMA ${d.ema_fast_len||20}<i class="key" style="display:inline-block;`
  + `margin-left:5px;background:${C.fast}"></i></span>`
  + `<span class="o">EMA ${d.ema_slow_len||50}<i class="key" style="display:inline-block;`
  + `margin-left:5px;background:${C.slow}"></i></span>`
  + `<span class="o">VWAP<i class="key dash" style="display:inline-block;margin-left:5px"></i></span>`
  + (CH.pinned ? "" : `<span class="o">scrolled back — press Reset</span>`);
}

// ---- interaction --------------------------------------------------------
function chartZoom(factor, anchorX){
  const bars = ((CH.data||{}).candles)||[];
  if(!bars.length) return;
  const plotW = cv.clientWidth - PAD.l - PAD.r;
  const frac = anchorX == null ? 1 : Math.max(0, Math.min(1, (anchorX-PAD.l)/plotW));
  const at = CH.i0 + frac * CH.n;                 // keep this bar under the cursor
  const next = Math.max(CH_MIN_BARS, Math.min(CH_MAX_BARS,
                 Math.min(bars.length, Math.round(CH.n * factor))));
  CH.i0 = Math.round(at - frac * next);
  CH.n = next;
  CH.i0 = Math.max(0, Math.min(bars.length - CH.n, CH.i0));
  CH.pinned = (CH.i0 + CH.n >= bars.length);
  chartDraw();
}

cv.addEventListener("wheel", e => {
  e.preventDefault();
  const r = cv.getBoundingClientRect();
  chartZoom(e.deltaY > 0 ? 1.15 : 1/1.15, e.clientX - r.left);
}, {passive:false});

cv.addEventListener("pointerdown", e => {
  cv.setPointerCapture(e.pointerId);
  CH.drag = {x:e.clientX, i0:CH.i0};
  cv.style.cursor = "grabbing";
});
cv.addEventListener("pointermove", e => {
  const r = cv.getBoundingClientRect();
  CH.hover = {x: e.clientX - r.left, y: e.clientY - r.top};
  if(CH.drag){
    const bars = ((CH.data||{}).candles)||[];
    const bw = (cv.clientWidth - PAD.l - PAD.r) / Math.max(1, CH.n);
    const moved = Math.round((e.clientX - CH.drag.x) / bw);
    CH.i0 = Math.max(0, Math.min(Math.max(0, bars.length - CH.n),
                                 CH.drag.i0 - moved));
    CH.pinned = (CH.i0 + CH.n >= bars.length);
  }
  chartDraw();
});
function endDrag(){ CH.drag = null; cv.style.cursor = "crosshair"; }
cv.addEventListener("pointerup", endDrag);
cv.addEventListener("pointercancel", endDrag);
cv.addEventListener("pointerleave", () => { endDrag(); CH.hover = null; chartDraw(); });
cv.addEventListener("dblclick", () => chartReset());

function chartReset(){
  const bars = ((CH.data||{}).candles)||[];
  CH.n = Math.min(130, Math.max(CH_MIN_BARS, bars.length || 130));
  CH.i0 = Math.max(0, bars.length - CH.n);
  CH.pinned = true;
  chartDraw();
}
$("cvin").onclick    = () => chartZoom(1/1.3, null);
$("cvout").onclick   = () => chartZoom(1.3, null);
$("cvreset").onclick = () => chartReset();
addEventListener("resize", () => { chartDraw(); sparkline(); });

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
  // Needing to connect and having a broken feed are different problems with
  // different fixes, so they are different notices — and only ever one of
  // them, because "connect your account" also explains the missing data.
  const needs = !!s.needs_connect;
  $("connect").style.display = needs ? "flex" : "none";
  $("connectmsg").textContent = (s.kite && s.kite.detail) || "";
  $("kite").textContent = needs ? "Connect Zerodha" : "Zerodha";
  $("kite").style.color = needs ? "#b07d15" : "";
  $("stale").style.display = (!needs && s.stale) ? "flex" : "none";
  $("stalemsg").textContent = s.feed==="expired"
    ? "Zerodha clears access tokens every morning and today's has not been renewed."
    : "The connection to Zerodha is not returning data.";

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

  const tstate = (s.tickets||{})[CUR] || null;
  ticketBox(r, tstate);
  ladder(r, tstate && tstate.ticket);
  sessionStrip(s.session, s.order);

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
  gauges(r, w);
  ringBox(r);
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

  chartWant(CUR);

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
                    help="accepted and ignored — the tool is always behind a login")
    ap.add_argument("--allow-signup", action="store_true",
                    help="let visitors create their own accounts instead of only the owner")
    args = ap.parse_args()

    # Flags win over .env, so you can try accounts for one run without
    # committing to them — editing a hidden file to see whether you like a
    # feature is a bad trade.
    if args.allow_signup:
        config.WEB_ALLOW_SIGNUP = True
    # WEB_REQUIRE_LOGIN is gone as a switch. The tool is always behind a login
    # now and the marketing pages are always public, because with a per-user
    # broker connection there is no longer a coherent "everyone sees the same
    # unauthenticated screen" mode to turn back on.
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

    # No worker thread is started here any more. In kite mode each user's feed
    # runs under their own Zerodha token, so it cannot exist before they do —
    # feeds.py starts one when somebody actually opens the tool and reaps it a
    # few minutes after they close it. In free mode they all share one, started
    # the same way and on the same terms.
    feeds.configure(args.mode, interval)

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
    if not config.WEB_ADMIN_KEY:
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
        print(f"\n  OPERATOR PAGE — create accounts here, keep this link private:")
        print(f"    {base}/admin?key={config.WEB_ADMIN_KEY}")
        if not config.WEB_PUBLIC_URL:
            print("    (WEB_PUBLIC_URL isn't set, so that link assumes this machine.")
            print("     Run  python3 setup_web.py  if you're hosting it somewhere else.)")
    else:
        print("\n  No admin key is set, so /admin is closed. Set WEB_ADMIN_KEY "
              "in .env to create accounts.")
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
    count = accounts.user_count()
    print(f"\n  ACCOUNTS: {count} · signup "
          + ("OPEN to anyone" if config.WEB_ALLOW_SIGNUP else "closed — you make them"))
    if config.WEB_ALLOW_SIGNUP:
        print(f"    Anyone can register at {base}/signup")
    elif count == 0 and config.WEB_ADMIN_KEY:
        print("    Nobody can log in yet. Create the first account on the")
        print("    operator page above.")
    if not (config.WEB_PUBLIC_URL or "").startswith("https://"):
        print("    NOT on HTTPS — fine on your own machine, negligent on the")
        print("    open internet. Passwords would cross the network in the clear.")

    print(f"\n  Mode: {args.mode}   ·   refresh every {interval}s   ·   Ctrl+C to stop")
    print("=" * 70)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        feeds.stop_all()
        srv.server_close()


if __name__ == "__main__":
    main()
