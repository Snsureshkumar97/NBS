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
import market_ticker
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
_stop_ticker = threading.Event()

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


def _available_markets():
    """Markets this server actually has instruments for, in display order."""
    return [m for m in config.MARKETS if config.instruments_in(m)]


def track_record(user=None, market=None):
    """One account's closed trades, summarised without flattering.

    Per user, because the trades are. This read the shared desktop log while
    tickets were being written to per-account files, so the card showed a
    stale history belonging to nobody on the site — one trade from the
    desktop app while the account it was displayed to had thirty-eight.

    `user` of None means no record rather than everybody's. The public pages
    call it that way on purpose: aggregating strangers' trades into a public
    "track record" would mix different people's lot sizes and discipline into
    a number that describes none of them, and would publish their activity
    besides. The public pages state the fixed backtest instead, which is a
    measurement rather than a scoreboard.
    """
    if not user:
        return {"n": 0}
    try:
        path = trade_log.user_log_path(user, market)
        rows = [r for r in trade_log._read_rows(path) if r.get("event") == "CLOSE"]
    except Exception:
        rows = []

    # Tickets the tool never saw the end of — it was restarted or stopped while
    # they were open — are counted but kept OUT of the hit rates. They have no
    # outcome, and folding them in would report "T1 reached 8.8%" for a set
    # that is mostly trades nobody watched. A hit rate has to be over trades
    # that actually finished, or it is not a hit rate.
    abandoned = [r for r in rows if "the tool stopped" in (r.get("status") or "")]
    rows = [r for r in rows if r not in abandoned]
    if not rows:
        return {"n": 0, "abandoned": len(abandoned)}
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
        "abandoned": len(abandoned),
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
    GATED = ("/app", "/api/state", "/api/tick", "/api/map/", "/chart/",
             "/connect")

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

    def _current_market(self):
        """The market this login chose at the door, or None if it has not.

        None is not a default to paper over: the tool cannot show a sensible
        screen without knowing which market it is in, so callers send the user
        to the chooser instead of guessing.
        """
        return accounts.session_market(self._cookie(self.SESSION_COOKIE))

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
            if path == "/api/alwayson":
                return self._do_always_on(form)
            if path == "/market":
                return self._do_market(form)
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
        # To the chooser rather than the tool. The two markets are separate all
        # the way down - separate feed, ticket book, totals and trade log - so
        # which one you are in has to be settled before there is a screen to
        # show. With crypto switched off there is only one answer and the
        # chooser records it and moves on without asking.
        markets = _available_markets()
        if len(markets) == 1:
            accounts.set_session_market(token, markets[0])
            return self._redirect("/app")
        return self._redirect("/market")

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
                # No record on the public pages — see track_record().
                return self._send(page(user=user, record=None))

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
                # A market has to be settled before there is a screen to draw:
                # it decides the instruments, the feed, the ticket book and the
                # currency everything is quoted in. A session that predates the
                # chooser has none, so it is asked once and carries on.
                if not self._current_market():
                    markets = _available_markets()
                    if len(markets) == 1:
                        accounts.set_session_market(
                            self._cookie(self.SESSION_COOKIE), markets[0])
                    else:
                        return self._redirect("/market")
                return self._send(PAGE)
            if path == "/market":
                return self._market_page()
            if path == "/review":
                # Your own record against the backtest, for the market this
                # login is in. Read-only; built from the ticket log on disk.
                market = self._current_market()
                if not market:
                    return self._redirect("/market")
                import review_page
                return self._send(review_page.page(review_page.build(user, market)))
            if path == "/api/state":
                return self._api_state(user)
            if path == "/api/tick":
                return self._api_tick(user)
            if path == "/api/chain":
                return self._api_chain(user, qs)
            if path == "/api/markets":
                # Public on purpose: it is world index levels off a free feed,
                # not anybody's data, and the strip is drawn before login on
                # the marketing pages too.
                return self._send(json.dumps({"rows": market_ticker.rows()}),
                                  "application/json")
            if path.startswith("/chart/") and path.endswith(".svg"):
                return self._chart(user, path[len("/chart/"):-len(".svg")], qs)
            if path.startswith("/api/candles/"):
                return self._candles(user, path[len("/api/candles/"):])
            if path.startswith("/api/map/"):
                return self._heat_map(user, path[len("/api/map/"):], qs)

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
        # A hashed name addresses one exact set of bytes, so it can be held
        # forever. A bare slug is a moving target and gets a short life -
        # promising immutable for a URL whose content can change is what left
        # replaced screenshots invisible in browsers that had seen the old ones.
        hashed = "." in slug
        self.send_header("Cache-Control",
                         "public, max-age=31536000, immutable" if hashed
                         else "public, max-age=300")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------------------------------------------------------------- state
    def _api_state(self, user):
        """This user's own feed, started by the act of asking for it."""
        market = self._current_market()
        feed = feeds.for_user(user, market)
        snap = feed.snapshot()
        kite = user_kite.summary(user) if _state["mode"] != "free" else {
            "state": "ok", "detail": "", "connected": True, "user_id": "", "since": ""}
        payload = {
            "market_open": snap["market_open"],
            "closing_auction": snap.get("closing_auction", False),
            "always_on": bool((accounts.get_user(user) or {}).get("always_on"))
                         if user else False,
            "market": market,
            "market_label": (config.MARKETS.get(market) or {}).get("label", ""),
            # The page must not guess this. Rendering a dollar premium behind a
            # rupee sign is a wrong number that looks like a right one.
            "currency": "USD" if market == "crypto" else "INR",
            "markets": _available_markets(),
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
            "record": track_record(user, market),
            # Only this market's instruments. Returning all of them put NIFTY
            # cards on a crypto screen with no data behind them, because the
            # feed - correctly - was not analysing them.
            "order": config.instruments_in(market) if market
                     else config.active_instruments(),
        }
        return self._send(json.dumps(payload), "application/json")

    def _api_chain(self, user, qs):
        """The option chain the signal was computed from, for one instrument.

        Strikes around the money with both sides' price, bid, ask and open
        interest - the same table a terminal puts on screen, built from data
        the tool already has rather than from a second source.
        """
        market = self._current_market()
        names = config.instruments_in(market)
        name = (qs.get("index") or [""])[0].upper()
        if name not in names:
            name = names[0] if names else ""
        feed = feeds.for_user(user, market)
        chain = feed.chain(name) if name else None
        if not chain or not chain.get("strikes"):
            return self._send(json.dumps({"index": name, "rows": [],
                                          "error": "no chain right now"}),
                              "application/json")
        meta = config.INSTRUMENTS.get(name) or {}
        step = meta.get("strike_step") or 50
        spot = chain.get("spot")
        strikes = sorted(chain["strikes"], key=lambda s: s["strike"])
        atm = min((s["strike"] for s in strikes),
                  key=lambda k: abs(k - (spot or 0))) if spot else None
        # A window either side of the money: the whole chain is hundreds of
        # rows, and nobody reads the 20% out-of-the-money wing on a screen.
        span = 12
        if atm is not None:
            strikes = [s for s in strikes if abs(s["strike"] - atm) <= span * step]

        def side(s, kind):
            ltp, bid, ask = s.get(f"{kind}_ltp"), s.get(f"{kind}_bid"), s.get(f"{kind}_ask")
            pct = None
            if bid and ask and ask >= bid:
                pct = round((ask - bid) / ((ask + bid) / 2) * 100, 2)
            return {"ltp": ltp, "bid": bid, "ask": ask,
                    "oi": s.get(f"{kind}_oi"), "spread": pct}

        rows = [{"strike": s["strike"], "ce": side(s, "call"), "pe": side(s, "put")}
                for s in strikes]
        rec = ((feed.snapshot().get("indices") or {}).get(name) or {})
        return self._send(json.dumps({
            "index": name, "expiry": chain.get("expiry"), "spot": spot, "atm": atm,
            "pcr": chain.get("pcr"), "max_pain": chain.get("max_pain"),
            "call_wall": chain.get("top_call_oi_strike"),
            "put_wall": chain.get("top_put_oi_strike"),
            "suggested": {"strike": rec.get("strike"), "type": rec.get("option_type")},
            "currency": "USD" if market == "crypto" else "INR",
            "rows": rows}), "application/json")

    def _api_tick(self, user):
        """Prices only, read straight out of the tick socket's memory.

        Separate from /api/state on purpose. State is a heavy object — three
        recommendations, their reasoning, the ticket book, the day's totals —
        and it changes when the analysis runs, which is every thirty seconds.
        Prices change several times a second, so they get their own endpoint
        that touches no network and can be asked for at that rate.
        """
        market = self._current_market()
        feed = feeds.for_user(user, market)
        payload = feed.ticks()
        payload["tickets"] = {
            k: (feed.tickets.public(k) or {}).get("ticket")
            for k in (config.instruments_in(market) if market
                      else config.active_instruments())
        }
        return self._send(json.dumps(payload), "application/json")

    def _heat_map(self, user, key, qs):
        """The index's constituents as a treemap, laid out at the size the
        page asked for.

        Streamed prices, so it moves with everything else. It answers the one
        question the signal engine cannot: is the whole index moving, or is
        one heavyweight dragging it while the rest goes the other way?
        """
        def num(name, default, lo, hi):
            try:
                v = int(float((qs.get(name) or [str(default)])[0]))
            except (TypeError, ValueError):
                v = default
            return max(lo, min(hi, v))

        feed = feeds.for_user(user, self._current_market())
        payload = feed.heat_map(key, width=num("w", 900, 240, 2000),
                                     height=num("h", 460, 160, 1200))
        payload["index"] = key
        return self._send(json.dumps(payload), "application/json")

    def _chart(self, user, key, qs):
        feed = feeds.for_user(user, self._current_market())
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
                f'<rect width="{w}" height="300" fill="#0f0f12"/>'
                f'<text x="{w//2}" y="150" fill="#6f6f7b" font-size="14" '
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
        feed = feeds.for_user(user, self._current_market())
        book = feed.tickets

        def as_bool(name):
            v = form.get(name)
            return None if v is None else v not in ("0", "false", "False", "")

        action = form.get("action")
        if action == "clear":
            book.clear(form.get("index") or "")
        else:
            try:
                lots = float(form["lots"]) if "lots" in form else None
            except (TypeError, ValueError):
                lots = None

            def as_num(name):
                if name not in form:
                    return None
                try:
                    v = float(str(form[name]).replace(",", "").strip() or 0)
                except (TypeError, ValueError):
                    return None
                return v if v == v and 0 <= v < 1e12 else None

            book.configure(lots=lots, reentry=as_bool("reentry"),
                           auto_rearm=as_bool("auto_rearm"),
                           limits=as_bool("limits"),
                           capital=as_num("capital"), risk_pct=as_num("risk_pct"))
        return self._send(json.dumps({"ok": True, "session": book.session()}),
                          "application/json")

    def _market_page(self, error=None):
        user = self._current_user()
        if not user:
            return self._redirect("/login")
        return self._send(nbs_site.market_page(
            user=user, markets=_available_markets(), error=error))

    def _do_market(self, form):
        user = self._current_user()
        if not user:
            return self._redirect("/login")
        want = (form.get("market") or "").strip()
        if want not in _available_markets():
            return self._market_page(error="That is not a market this server runs.")
        accounts.set_session_market(self._cookie(self.SESSION_COOKIE), want)
        return self._redirect("/app")

    def _do_always_on(self, form):
        """Turn unattended running on or off for this account.

        Stored on the account rather than in the session, because the whole
        point is that it outlives the browser: the supervisor in feeds.py reads
        it on a timer and holds the feed open from just before the open until
        the close, whether or not anyone has the page up.
        """
        user = self._current_user()
        if not user:
            return self._redirect("/login")
        want = form.get("on")
        on = want not in ("0", "false", "False", "", None)
        ok, msg = accounts.update_user(user, {"always_on": True} if on
                                       else {"always_on": None})
        if on and feeds._resident_window(now_ist()):
            # Start it now rather than waiting up to twenty seconds for the
            # supervisor, so switching it on during the session does something
            # visible immediately. Outside the session the flag is simply
            # saved: spinning a feed up at ten at night to reap it four minutes
            # later fetches a day of candles nobody asked for.
            feeds.for_user(user, self._current_market())
        return self._send(json.dumps({"ok": bool(ok), "always_on": on,
                                      "error": None if ok else msg}),
                          "application/json")

    def _candles(self, user, key):
        """The bars themselves, as JSON, for the interactive chart.

        The SVG endpoint stays: it is what a browser with no JavaScript, and
        the desktop app's own renderer, both use. This one exists because a
        picture cannot be hovered — a crosshair that reads out the bar under
        the pointer has to have the bars in the browser.
        """
        import indicators as ind

        feed = feeds.for_user(user, self._current_market())
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
<meta name="color-scheme" content="dark">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<title>NBS Signal Tool — Nifty · Bank Nifty · Sensex</title>
<style>
/* Kite's palette, so this screen and kite.zerodha.com can sit in adjacent
   tabs without the eye having to re-calibrate between them. The up/down pair
   is #4caf50 / #ff5722, which is what Kite uses and what the chart hues were
   re-checked against for colour-blind separation — the notes in
   chart_panel.py record what the previous pair failed on. */
:root{
  /* Deep slate rather than flat black: a faint blue in the base lets the
     accent, the green and the orange all sit on it without any one of
     them looking pasted on. Ink and signal colours are unchanged. */
  --bg:#0a0d14; --surface:#10141d; --raised:#161b26; --sunken:#0c1018;
  --bd:#222938; --bd-soft:#1a2030;
  --ink:#e8e8ec; --ink-2:#a2a2ac; --ink-3:#6f6f7b;
  --up:#4caf50; --down:#ff5722; --warn:#f6a500; --accent:#4d94e8;
  --ema-fast:#4d94e8; --ema-slow:#f6a500; --vwap:#b07ad4;
  --r:3px; --r-sm:3px;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  -webkit-font-smoothing:antialiased}
/* Wider than the reading pages on purpose. This is a dashboard: the chart,
   the ladder and the map all want room, and a 1120px column on a 27in monitor
   wastes half of it. */
.wrap{max-width:1320px;margin:0 auto;padding:0 20px 64px}

/* ---------- background ----------
   Three soft glows fixed to the viewport - blue from the top left, green from
   the top right, a little violet from below - over a faint chart grid that
   fades out down the page. Cards stay solid on top of it, so it is felt in the
   gutters and never behind a number you have to read. */
body{background-color:var(--bg);
  background-image:
    radial-gradient(1100px 620px at 12% -8%, rgba(77,148,232,.14), transparent 62%),
    radial-gradient(900px 520px at 100% 0%, rgba(76,175,80,.08), transparent 58%),
    radial-gradient(1000px 700px at 50% 115%, rgba(176,122,212,.07), transparent 60%);
  background-attachment:fixed}
body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background-image:
    linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px);
  background-size:40px 40px;
  -webkit-mask-image:radial-gradient(ellipse 120% 90% at 50% 0%, #000 35%, transparent 80%);
          mask-image:radial-gradient(ellipse 120% 90% at 50% 0%, #000 35%, transparent 80%)}

/* ---------- header ---------- */
header{position:sticky;top:0;z-index:20;background:rgba(10,13,20,.80);
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
/* The feed's own state, kept apart from the market's. They answer different
   questions - the market can be open while the feed is dead - and sharing one
   pill meant two timers overwriting each other four times a second, which read
   as a flicker between the clock and the word "live". */
.lbtn.ao{font-size:11px;padding:2px 9px;border-radius:999px;font-weight:600}
.lbtn.ao.on{color:var(--up);border-color:rgba(76,175,80,.4);
  background:rgba(76,175,80,.12)}
.room{margin-top:14px;border-top:1px solid var(--bd-soft);padding-top:12px}
.rr-h{font-size:10.5px;font-weight:700;letter-spacing:.9px;color:var(--ink-3);
  text-transform:uppercase;margin:0 0 6px}
.rr{display:grid;grid-template-columns:78px 86px 118px 1fr auto;gap:10px;
  align-items:center;padding:7px 10px;border-radius:6px;font-size:13px;
  border:1px solid transparent}
.rr.mine{background:var(--raised);border-color:var(--bd)}
.rr-d{font-weight:700}
.rr-p{font-weight:650;color:var(--ink)}
.rr-to{color:var(--ink-2)}
.rr-to b{color:var(--ink);font-weight:650}
.rr-c{color:var(--ink-3);font-size:12px}
.rr-tag{font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;
  color:var(--accent);border:1px solid rgba(77,148,232,.4);border-radius:999px;
  padding:1px 8px}
@media(max-width:640px){.rr{grid-template-columns:72px 1fr 1fr}
  .rr-c,.rr-tag{grid-column:1/-1}}
.feedtag{font-size:10px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  color:var(--ink-3);background:var(--sunken);border:1px solid var(--bd-soft);
  border-radius:3px;padding:1px 5px;flex:none}
.feedtag.on{color:var(--up);background:rgba(76,175,80,.12);
  border-color:rgba(76,175,80,.35)}
.feedtag.off{color:var(--warn);background:rgba(246,165,0,.12);
  border-color:rgba(246,165,0,.35)}
.beat.live{background:var(--up);box-shadow:0 0 0 0 rgba(76,175,80,.55);
  animation:beat 2.4s infinite}
@keyframes beat{0%{box-shadow:0 0 0 0 rgba(76,175,80,.45)}
  70%{box-shadow:0 0 0 7px rgba(76,175,80,0)}100%{box-shadow:0 0 0 0 rgba(76,175,80,0)}}

/* ---------- notices ---------- */
.notice{border-radius:var(--r);padding:14px 16px;margin:16px 0 0;font-size:13px;
  line-height:1.65;display:flex;gap:11px;align-items:flex-start}
.notice svg{flex:none;margin-top:2px}
.notice.risk{background:#2a1610;border:1px solid #5c2a18;color:#e0b0a0;
  display:block;padding:11px 15px}
.notice.risk summary{cursor:pointer;list-style:none;font-size:13px;
  line-height:1.6}
.notice.risk summary::-webkit-details-marker{display:none}
.notice.risk .more{color:#ff8a65;font-weight:650;white-space:nowrap}
.notice.risk[open] .more{display:none}
.notice.risk #honest{font-size:13px;line-height:1.65;margin-top:9px;
  padding-top:9px;border-top:1px solid #5c2a18}
.notice.risk b{color:#ff8a65}
.notice.stale{background:#1c1710;border:1px solid #3a2f18;color:#d8c9a8}
.notice.stale b{color:#f0bf55}

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
.mkt .ex{font-size:11px;color:var(--ink-3);margin-top:3px;white-space:nowrap}
.mkt .ex.today{color:var(--warn);font-weight:650}
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

/* Account risk. The question a professional asks before any other - what
   does this trade put at stake, as a share of the account - answered on the
   card rather than left to mental arithmetic. */
.risk{margin-top:12px;border:1px solid var(--bd);border-radius:12px;
  background:var(--sunken);padding:10px 12px}
.riskctl{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:12px;color:var(--ink-3)}
.riskctl input,.riskctl select{background:var(--raised);color:var(--ink);
  border:1px solid var(--bd);border-radius:8px;padding:4px 8px;font:inherit;font-size:12px}
.riskctl input{width:130px}
.riskline{font-size:12px;color:var(--ink-3);line-height:1.6;margin-top:8px}
.riskline:empty{display:none}
.riskline b{color:var(--ink-2)}
.riskline .ok{color:var(--up)} .riskline .warn{color:var(--warn)} .riskline .bad{color:var(--down)}
.xday{display:inline-block;margin-top:8px;font-size:11.5px;font-weight:650;color:var(--warn);
  border:1px solid color-mix(in srgb,var(--warn) 45%,transparent);border-radius:999px;padding:3px 10px}

/* The lots selector. Nothing here places an order, so this only scales the
   rupee column — it is a "what would that be worth to me" dial, not a size. */
.lots{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--ink-3)}
.lots select{background:var(--sunken);color:var(--ink);border:1px solid var(--bd);
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
/* ---------- the market map ---------- */
/* A treemap: each constituent's box sized by its index weight and coloured by
   its move today. Absolutely-positioned divs rather than a canvas, because
   these need to be hoverable, selectable and readable by a screen reader —
   a canvas would be a picture of a table. */
.mapwrap{position:relative;background:var(--sunken);border:1px solid var(--bd);
  border-radius:var(--r-sm);overflow:hidden;height:460px;margin-top:4px}
@media(max-width:640px){.mapwrap{height:340px}}
.mtile{position:absolute;overflow:hidden;border:1px solid rgba(0,0,0,.35);
  display:flex;flex-direction:column;justify-content:center;align-items:center;
  padding:2px;transition:background-color .4s ease}
.mtile b{font-size:11px;font-weight:700;line-height:1.15;letter-spacing:-.2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.mtile i{font-size:10px;font-style:normal;font-variant-numeric:tabular-nums;
  line-height:1.2;opacity:.92}
.mtile.tiny b{font-size:9px} .mtile.tiny i{display:none}
.mtile.mini b{display:none} .mtile.mini i{display:none}
.mapbar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  font-size:12px;color:var(--ink-3);margin-top:10px}
.mapbar b{color:var(--ink-2);font-weight:650}
.mapbar .w{font-variant-numeric:tabular-nums;font-weight:700}
.maplegend{display:flex;align-items:center;gap:6px;margin-left:auto}
.maplegend span{font-size:10.5px}
.maplegend .sw{width:52px;height:8px;border-radius:2px;
  background:linear-gradient(90deg,#ef5570,#242429,#2be08a)}

/* ---------- the world markets strip ---------- */
/* Two identical copies of the row slide left together; when the first has
   fully passed, the animation restarts and the second is exactly where the
   first began, so the seam never shows. CSS rather than a scroll timer,
   which keeps it on the compositor and off the main thread — this must not
   compete with the tick loop for frames. */
.ticker{position:relative;overflow:hidden;border-bottom:1px solid var(--bd);
  background:var(--surface);height:38px}
.ticker:hover .tk-track{animation-play-state:paused}
.tk-track{display:flex;width:max-content;align-items:center;height:38px;
  /* Duration scales with how much is in the strip — a fixed time would make
     a long list sprint and a short one crawl. Set from JS as --tkdur. */
  animation:tkslide var(--tkdur,150s) linear infinite}
@keyframes tkslide{from{transform:translateX(0)}to{transform:translateX(-50%)}}
@media(prefers-reduced-motion:reduce){
  /* Motion someone did not ask for, in their peripheral vision, all day. */
  .tk-track{animation:none}
  .ticker{overflow-x:auto}
}
.tk{display:inline-flex;align-items:baseline;gap:7px;padding:0 18px;
  font-size:12.5px;white-space:nowrap;border-right:1px solid var(--bd-soft)}
.tk .n{color:var(--ink-2);font-weight:650;letter-spacing:.2px}
.tk .p{color:var(--ink);font-variant-numeric:tabular-nums;font-weight:600}
.tk .c{font-size:11.5px;font-variant-numeric:tabular-nums;font-weight:600}
.tk .dot{width:5px;height:5px;border-radius:50%;flex:none;align-self:center}
/* Marks where the Indian block ends and the world block begins, so the strip
   reads as two lists rather than one long undifferentiated one. */
.tk-sep{display:inline-flex;align-items:center;padding:0 16px;font-size:10px;
  font-weight:800;letter-spacing:1.2px;color:var(--ink-3);white-space:nowrap;
  border-right:1px solid var(--bd-soft)}

/* ---------- the welcome bar ---------- */
.welcome{display:flex;align-items:flex-end;justify-content:space-between;
  gap:18px;flex-wrap:wrap;margin-top:16px;margin-bottom:2px}
.welcome .eyebrow{margin:0 0 6px}
.welcome h1{font-size:clamp(21px,2.6vw,28px);line-height:1.15;margin:0;
  letter-spacing:-.5px;font-weight:700}
.welcome .who{color:var(--accent)}
.welcome .said{color:var(--ink-2);font-size:13.5px;margin:5px 0 0}
.welcome .acts{display:flex;gap:8px;flex-wrap:wrap;flex:none}

.top3{display:grid;grid-template-columns:1.15fr 1.35fr .8fr;gap:14px;
  margin-top:14px}
.top3 .card{padding:16px 18px 18px}
@media(max-width:900px){.top3{grid-template-columns:1fr}}
.spark{width:100%;height:64px;display:block;margin-top:8px}
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
.badge.open{background:#122017;border-color:#1f4a2c;color:#7ed492}
.badge.hold{background:#1c1710;border-color:#3a2f18;color:#e0a93a}
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
  border:1px solid var(--bd);background:var(--raised);
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
.rung .bar i{display:block;height:100%;border-radius:2px;
  transition:width .45s ease-out}
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

/* A hidden panel stays hidden: several of these are grid or flex containers
   whose own display would otherwise win against the hidden attribute. */
[hidden]{display:none !important}

/* ---------- option chain ----------
   Calls on the left, puts on the right, strikes down the middle - the way a
   chain is read everywhere. Built from the same snapshot the signal was
   computed from, so the two can never disagree. */
.chainwrap{max-height:420px;overflow:auto;border:1px solid var(--bd);
  border-radius:12px;background:rgba(6,8,12,.55)}
table.chain{width:100%;border-collapse:collapse;font-size:12px;
  font-variant-numeric:tabular-nums;font-family:"SF Mono",Consolas,monospace}
table.chain th{position:sticky;top:0;z-index:1;background:rgba(10,12,18,.97);
  font-size:10px;letter-spacing:.5px;text-transform:uppercase;color:var(--ink-3);
  font-weight:700;padding:7px 6px;text-align:right}
table.chain th.k,table.chain td.k{text-align:center;color:var(--ink-2);font-weight:700}
table.chain th.ce{color:var(--up)} table.chain th.pe{color:var(--down)}
table.chain td{padding:5px 6px;text-align:right;color:var(--ink-3);
  border-top:1px solid var(--bd-soft);white-space:nowrap}
table.chain td.px{color:var(--ink-2)}
table.chain tr.atm{background:rgba(255,255,255,.06)}
table.chain tr.atm td{color:var(--ink-2)} table.chain tr.atm td.k{color:var(--ink)}
table.chain td.mine{outline:1px solid rgba(77,148,232,.55);border-radius:4px;color:var(--ink)}
table.chain .wall{color:var(--warn);font-weight:700}
table.chain .wide{color:var(--down)}
.chainbar{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--ink-3);margin-top:10px}
.chainbar b{color:var(--ink-2)}

/* ---------- the command palette ---------- */
.pal{position:fixed;inset:0;z-index:60;background:rgba(3,4,7,.6);
  backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);
  display:flex;align-items:flex-start;justify-content:center;padding-top:12vh}
.palbox{width:min(620px,92vw);background:rgba(12,15,22,.97);border:1px solid var(--bd);
  border-radius:16px;box-shadow:0 40px 90px -30px rgba(0,0,0,.9);overflow:hidden}
.palbox input{width:100%;background:transparent;border:0;border-bottom:1px solid var(--bd);
  color:var(--ink);font:inherit;font-size:16px;padding:15px 18px;outline:none}
.pallist{max-height:52vh;overflow:auto;padding:6px}
.palrow{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:10px;
  cursor:pointer;font-size:14px;color:var(--ink-2)}
.palrow .t{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--ink-3);
  border:1px solid var(--bd);border-radius:999px;padding:1px 8px;flex:none}
.palrow .s{color:var(--ink-3);font-size:12px;margin-left:auto;white-space:nowrap}
.palrow.on{background:rgba(77,148,232,.18);color:var(--ink)}
.palhint{border-top:1px solid var(--bd);padding:8px 14px;font-size:11.5px;color:var(--ink-3);
  display:flex;gap:14px}
.palhint kbd{font:inherit;font-size:11px;background:rgba(255,255,255,.07);
  border:1px solid var(--bd);border-radius:5px;padding:1px 5px}

/* =====================================================================
   THE 3D LAYER - nbs-signal-3d.html, applied to the live screen.
   A particle field, two glows and a slowly turning candlestick chart made
   of the selected index's REAL last candles, drawn on a canvas behind the
   page; glass cards over it; a signal card that glows in the colour of the
   signal and sways a degree or two; target bars with depth; cards that tilt
   under the cursor. Pure canvas and CSS - no Three.js, because the page's
   security policy loads nothing from outside, and a page holding a broker
   session should keep it that way.
   Numbers stay flat and still: the chart, the map and the inputs never tilt,
   and the sway stops the moment the pointer is over the signal card.
   ===================================================================== */
:root{
  --bg:#05060a; --surface:rgba(255,255,255,.035); --raised:rgba(255,255,255,.065);
  --sunken:rgba(6,8,12,.62); --bd:rgba(255,255,255,.09); --bd-soft:rgba(255,255,255,.06);
  --ink:#f0f2f6; --ink-2:#a3aabb; --ink-3:#6b7282;
  --up:#2be08a; --down:#ef5570; --warn:#f2a33d;
  --glow-up:rgba(43,224,138,.45); --glow-down:rgba(239,85,112,.40); --glow-warn:rgba(242,163,61,.35);
  --r:16px; --r-sm:12px;
}
body{background:#05060a;
  background-image:radial-gradient(900px 600px at 80% 10%, rgba(43,224,138,.08), transparent 60%),
                   radial-gradient(800px 600px at 10% 90%, rgba(242,163,61,.06), transparent 60%);
  background-attachment:fixed}
body::before{display:none}
#bg3d{position:fixed;inset:0;width:100vw;height:100vh;z-index:0;pointer-events:none;display:block}
.wrap,footer{position:relative;z-index:1}
.wrap{perspective:1400px}
header{background:rgba(5,6,10,.62);border-bottom:1px solid var(--bd-soft)}
.ticker{background:rgba(10,12,18,.55);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);
  border-bottom:1px solid var(--bd-soft)}

/* glass */
.card,.mkt,.session,.notice.stale{
  /* Glass over a dark base: the scene shows through as colour and movement,
     never as shapes behind a number you have to read. */
  background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.015)),rgba(9,11,17,.72);
  border:1px solid var(--bd);border-radius:16px;
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  box-shadow:0 20px 40px -25px rgba(0,0,0,.7)}
.notice.risk{background:rgba(240,84,106,.08);border:1px solid rgba(240,84,106,.25);color:#f3a9b3;
  border-radius:12px;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.notice.risk b,.notice.risk .more{color:#fff}
.notice.risk #honest{border-top-color:rgba(240,84,106,.25)}
.tile,.risk,.tstat{background:rgba(255,255,255,.03);border:1px solid var(--bd);border-radius:12px}
.mapwrap{border-radius:12px}
.lbtn.on{background:linear-gradient(180deg,#5aa2ee,#3a7fd0);border-color:#5aa2ee}

/* index cards: tilt, and a glow in the colour of the signal they carry */
.mkt{transform-style:preserve-3d;transition:transform .15s ease-out,box-shadow .3s ease,border-color .3s}
.mkt:hover{background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.02)),rgba(9,11,17,.72)}
.mkt.bull{border-color:rgba(43,224,138,.5);
  box-shadow:0 0 0 1px rgba(43,224,138,.2),0 25px 60px -20px var(--glow-up)}
.mkt.bear{border-color:rgba(239,85,112,.5);
  box-shadow:0 0 0 1px rgba(239,85,112,.2),0 25px 60px -20px var(--glow-down)}
.mkt[aria-selected="true"]{background:linear-gradient(180deg,rgba(77,148,232,.12),rgba(255,255,255,.02)),rgba(9,11,17,.72)}
.mkt[aria-selected="true"]::before{background:linear-gradient(180deg,#5aa2ee,#2be08a);width:3px}
.mkt .px{font-size:24px;font-weight:600}

/* the signal card */
.herocard{border-radius:20px;padding:26px 28px 28px;transform-style:preserve-3d;
  animation:mount 7s ease-in-out infinite}
.herocard:hover,.herocard:focus-within{animation-play-state:paused}
@keyframes mount{
  0%,100%{transform:perspective(1200px) rotateX(.8deg) rotateY(-.9deg)}
  50%{transform:perspective(1200px) rotateX(-.6deg) rotateY(1deg)}}
.herocard[data-bias="up"]{border-color:rgba(43,224,138,.32);
  box-shadow:0 40px 100px -30px rgba(0,0,0,.75),0 0 90px -20px var(--glow-up)}
.herocard[data-bias="down"]{border-color:rgba(239,85,112,.32);
  box-shadow:0 40px 100px -30px rgba(0,0,0,.75),0 0 90px -20px var(--glow-down)}
.herocard[data-bias="up"] #bias{text-shadow:0 0 40px var(--glow-up)}
.herocard[data-bias="down"] #bias{text-shadow:0 0 40px var(--glow-down)}
.herocard .hero .v{font-size:46px;letter-spacing:-.02em}

.tag.up{background:rgba(43,224,138,.12);color:var(--up);border-color:rgba(43,224,138,.35)}
.tag.down{background:rgba(239,85,112,.12);color:var(--down);border-color:rgba(239,85,112,.35)}
.tag.warn{background:rgba(242,163,61,.14);color:#facc7a;border-color:rgba(242,163,61,.35)}
.tag.flat{background:rgba(255,255,255,.05);color:var(--ink-2);border-color:var(--bd)}
.badge.hold{background:rgba(242,163,61,.14);border-color:rgba(242,163,61,.35);color:#facc7a}
.badge.open{background:rgba(43,224,138,.12);border-color:rgba(43,224,138,.4);color:var(--up)}
.badge.prev{background:rgba(255,255,255,.05);border-color:var(--bd);color:var(--ink-2)}

/* target bars with depth */
.rung{padding:9px 0}
.rung .bar{height:14px;border-radius:8px;background:linear-gradient(180deg,#0a0b0f,#16181f);
  box-shadow:inset 0 2px 4px rgba(0,0,0,.6),inset 0 -1px 0 rgba(255,255,255,.03)}
.rung .bar i{border-radius:8px 0 0 8px;transition:width 1.2s cubic-bezier(.2,.8,.2,1);
  background-image:linear-gradient(180deg,rgba(255,255,255,.38),rgba(255,255,255,0) 45%,rgba(0,0,0,.35)) !important;
  box-shadow:inset 0 2px 3px rgba(255,255,255,.35),inset 0 -3px 5px rgba(0,0,0,.35),0 0 16px -2px var(--c,transparent)}
.rung .n{font-family:"SF Mono",Consolas,monospace;font-size:14px}
.gauge .gt{height:8px;border-radius:5px;background:linear-gradient(180deg,#0a0b0f,#16181f);
  box-shadow:inset 0 2px 3px rgba(0,0,0,.55)}
.gauge .gt i{border-radius:5px;
  background-image:linear-gradient(180deg,rgba(255,255,255,.3),rgba(0,0,0,.25)) !important}
.ringarc{filter:drop-shadow(0 0 10px rgba(242,163,61,.55))}
.ring svg{filter:drop-shadow(0 6px 14px rgba(0,0,0,.5))}

/* the three boxes and the stat tiles tilt too */
.top3 .card,.tiles .tile{transform-style:preserve-3d;transition:transform .15s ease-out}

/* entrance */
@keyframes fadeUp{from{opacity:0;transform:translateY(14px) rotateX(6deg)}
  to{opacity:1;transform:none}}
.wrap > *{animation:fadeUp .6s cubic-bezier(.2,.8,.2,1) both}
.wrap > *:nth-child(2){animation-delay:.05s} .wrap > *:nth-child(3){animation-delay:.1s}
.wrap > *:nth-child(4){animation-delay:.15s} .wrap > *:nth-child(5){animation-delay:.2s}
.wrap > *:nth-child(6){animation-delay:.25s} .wrap > *:nth-child(7){animation-delay:.3s}
.wrap > *:nth-child(n+8){animation-delay:.35s}
/* The signal card enters like the rest, then starts its sway - both in one
   list, or the entrance rule (which comes later) silently replaced it. */
.wrap > .herocard{animation:fadeUp .6s cubic-bezier(.2,.8,.2,1) .2s both,
                             mount 7s ease-in-out .8s infinite}
.wrap > .herocard:hover,.wrap > .herocard:focus-within{animation-play-state:paused}

@media (prefers-reduced-motion: reduce){
  .herocard,.wrap > *,.wrap > .herocard{animation:none}
  .mkt,.top3 .card,.tiles .tile{transition:none}
}
@media (max-width:720px){
  .card,.mkt,.session{backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
  .herocard,.wrap > .herocard{animation:none;padding:20px}
  .herocard .hero .v{font-size:34px}
}
</style></head><body>
<canvas id="bg3d" aria-hidden="true"></canvas>
<div class="pal" id="pal" hidden>
 <div class="palbox" role="dialog" aria-label="Command palette">
  <input id="palq" placeholder="Jump to an index, a panel or a page&hellip;"
         autocomplete="off" spellcheck="false">
  <div class="pallist" id="pallist"></div>
  <div class="palhint"><span><kbd>&uarr;</kbd><kbd>&darr;</kbd> move</span>
   <span><kbd>&crarr;</kbd> run</span><span><kbd>esc</kbd> close</span></div>
 </div>
</div>

<header><div class="hd">
  <a class="brand" href="/" style="color:inherit;text-decoration:none">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="22" height="22" rx="6" fill="#1b1b20" stroke="#2a2a31"/>
      <path d="M5 16.5l3.6-4.2 2.9 2.6 3-4.4 4.5 3.4" stroke="#4d94e8"
            stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="19" cy="13.9" r="2" fill="#4caf50"/>
    </svg>
    <div>NBS Signal Tool<small id="brandsub">Nifty · Bank Nifty · Sensex</small></div>
  </a>
  <div class="row" style="display:flex;gap:8px;align-items:center">
    <span class="pill"><span class="beat" id="beat"></span><span id="mkt">connecting</span></span>
    <span class="pill"><span class="feedtag" id="feed">&mdash;</span><span id="upd">&mdash;</span></span>
    <a class="pill" id="mktsw" href="/market" style="text-decoration:none;display:none"
       title="Switch market">&mdash;</a>
    <a class="pill" href="/review" style="text-decoration:none"
       title="Your results so far, against the backtest">Review</a>
    <a class="pill" id="kite" href="/connect" style="text-decoration:none">Zerodha</a>
    <a class="pill" id="signout" href="/logout" style="display:none;text-decoration:none">Sign out</a>
  </div>
</div></header>

<div class="ticker" aria-label="World market levels"><div class="tk-track" id="tkt"></div></div>

<div class="wrap">

 <div class="welcome">
  <div>
   <p class="eyebrow">Overview</p>
   <h1>Welcome, <span class="who" id="who">—</span></h1>
   <p class="said" id="said">Nifty, Bank Nifty and Sensex — one screen for the session.</p>
  </div>
  <div class="acts">
   <a class="lbtn" href="/how-it-works">How it works</a>
   <a class="lbtn" href="/connect">Zerodha</a>
   <a class="lbtn" href="/results">Results</a>
  </div>
 </div>

 <!-- One line by default, the whole thing on demand. It is not dismissible
      and there is no "don't show again": the point of it is that it is always
      there. But six lines of it above the fold on every single load taught
      people to scroll past the top of the page, which is worse for the
      warning than making it compact. -->
 <details class="notice risk" id="riskbox">
  <summary><b>Not advice.</b> A mechanical rule set, not a SEBI-registered
   analyst. No orders are placed for you. <span class="more">What was
   measured &rsaquo;</span></summary>
  <div id="honest"></div>
 </details>

 <div class="notice stale" id="connect" style="display:none">
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
   <path d="M6.4 9.6L2.8 13.2M9.6 6.4l3.6-3.6" stroke="#f0bf55" stroke-width="1.6"
         stroke-linecap="round"/>
   <path d="M4.6 6.2a2.6 2.6 0 013.7 0l1.5 1.5a2.6 2.6 0 010 3.7"
         stroke="#f0bf55" stroke-width="1.6" stroke-linecap="round"/>
  </svg>
  <div><b>Your Zerodha account is not connected.</b> <span id="connectmsg"></span>
   The signals below are computed under your own broker session, so there is
   nothing to show until you connect it.
   <a href="/connect" style="color:#f0bf55;font-weight:700">Connect now &rarr;</a></div>
 </div>

 <div class="notice stale" id="stale" style="display:none">
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
   <path d="M8 1.8l6.4 11.4H1.6L8 1.8z" stroke="#f0bf55" stroke-width="1.5" stroke-linejoin="round"/>
   <path d="M8 6.4v3M8 11.4v.6" stroke="#f0bf55" stroke-width="1.6" stroke-linecap="round"/>
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

 <div class="card herocard" id="sigcard" data-panel="signal" style="margin-top:14px">
  <div class="thead">
   <p class="eyebrow" id="teyebrow">Signal</p>
   <span class="badge prev" id="tbadge" style="display:none"></span>
   <button class="lbtn tclear" id="tclear" type="button"
           style="display:none">Clear ticket</button>
  </div>
  <div class="hero">
   <div class="v" id="bias">—</div>
   <span class="tag flat" id="conftag" style="display:none"></span>
   <span class="tag flat" id="exptag" style="display:none"></span>
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
    <label for="lots" id="lotslabel">Lots</label>
    <select id="lots"></select>
   </div>
  </div>
  <div class="ladder" id="ladder"></div>
  <div class="lnote" id="lnote"></div>
  <div class="risk" id="risk">
   <div class="riskctl">
    <label for="capital">Capital</label>
    <input id="capital" type="text" inputmode="numeric" autocomplete="off"
           placeholder="enter to size trades">
    <label for="riskpct">Risk / trade</label>
    <select id="riskpct"></select>
   </div>
   <div class="riskline" id="riskline"></div>
  </div>
  <div class="gauges" id="gauges"></div>
  <div class="room" id="room"></div>
  <div class="gnote" id="gnote"></div>
 </div>

 <div class="top3" data-panel="trend">
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

 <div class="grid">
  <!-- Left column. The track record sits under the chart rather than at the
       bottom of the right-hand stack: the chart is a fixed 430px while the
       range and the map together run past 600, so the left column used to
       simply stop and leave the rest of its height empty. Down here the card
       is also twice as wide, which lets .rec's auto-fit put all five tiles in
       one row instead of four and an orphan. -->
  <div>
   <div class="card" data-panel="chart">
    <p class="eyebrow">Price &middot; 15-minute candles</p>
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
   <div class="card" id="reccard" data-panel="record">
    <p class="eyebrow">Track record &middot; wins and losses</p>
    <div id="record"><p style="color:var(--ink-3);font-size:13px;margin:0">
      No completed trades recorded yet.</p></div>
   </div>
  </div>

  <div>
   <div class="card" data-panel="range">
    <p class="eyebrow">Today's range</p>
    <div class="tiles" style="grid-template-columns:1fr" id="trendtiles"></div>
   </div>
   <div class="card" data-panel="chain" id="chaincard">
    <p class="eyebrow">Option chain &middot; <span id="chainhead">&mdash;</span></p>
    <div class="chainwrap"><table class="chain" id="chain"></table></div>
    <div class="chainbar" id="chainbar"></div>
   </div>
   <div class="card" data-panel="map">
    <p class="eyebrow">Market map &middot; <span id="mapidx">&mdash;</span> constituents</p>
    <div class="mapwrap" id="mapwrap"></div>
    <div class="mapbar">
     <span id="mapbreadth">loading&hellip;</span>
     <div class="maplegend"><span>&minus;2%</span><i class="sw"></i><span>+2%</span></div>
    </div>
   </div>
  </div>
 </div>

 <div class="card" data-panel="why" style="margin-top:14px">
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
var SCENE_BIAS = "";      // the 3D background's glow colour; read by the scene script
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
    el.classList.toggle("bull", !!(r && r.bias==="BULLISH"));
    el.classList.toggle("bear", !!(r && r.bias==="BEARISH"));
    // The expiry on every card, all the time: the open ticket's own contract
    // when one is running on this index, otherwise the one being suggested.
    const tk = ((s.tickets||{})[k]||{}).ticket;
    const iso = (tk && tk.open && tk.expiry) ? tk.expiry : (r && r.expiry);
    const ex = expiryText(iso, true);
    el.innerHTML=`<div><div class="nm">${esc(k)}</div>
      <div class="px" id="px-${esc(k)}">${px}</div>
      ${ex ? `<div class="ex${ex.includes("today") ? " today" : ""}">Exp ${esc(ex)}</div>` : ""}</div>
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
  $("conftag").style.display="none"; $("exptag").style.display="none";
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
// "auto" until the reader picks a side. The desktop tracks a ticket on the
// live premium whenever there is one, and the premium is the number actually
// paid — so defaulting to index points meant the website quietly answered a
// different question than the app did, using the same word for it.
let LMODE = "auto";
let LOTS = 1;
let LOTS_SYNCED = false;

// A Deribit contract IS one coin, so "5 lots of 1" is a unit that does not
// exist; index options are genuinely sold in lots of 75 or 30. Set here rather
// than inside the no-ticket branch, because ladder() returns early once a
// ticket is open - which is exactly when you are most likely to be reading it.
function unitLabel(r){
  const perLot = (r.lot_size || 1) > 1;
  const ll = $("lotslabel");
  if(ll) ll.textContent = perLot ? "Lots" : "Contracts";
  const w = $("lotswrap");
  if(w) w.title = perLot ? (r.lot_size + " per lot")
                         : ("one contract is one " + String(CUR||"").toUpperCase());
}

function ladder(r, tk){
  unitLabel(r);
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
    $("ladder").innerHTML = rungs.map(([k,v,c])=>{
      const done = k==="Stop" ? tk.sl_hit : (tk.hit||{})[k];
      const when = k==="Stop" ? tk.sl_hit_time : (tk.hit_time||{})[k];
      // How far price has actually travelled from entry toward this level —
      // the desktop's "38% of the way". The old bar drew the level's distance
      // from entry instead, which is fixed the moment the ticket is issued and
      // therefore never moved at all.
      let pct = 0;
      if(v != null && base != null && tk.now != null && v !== base){
        pct = Math.max(0, Math.min(100, (tk.now - base) / (v - base) * 100));
      }
      let rs = "";
      if(per && v!=null && base!=null){
        const amt=(v-base)*per;
        rs = money(amt);
      }
      return `<div class="rung${done?" done":""}"><div class="k">${k}${
          done?` <span class="tick">✓ ${esc(when||"")}</span>`:""}</div>
        <div class="bar" title="${done?"reached":Math.round(pct)+"% of the way"}"
          ><i style="width:${done?100:pct}%;background:${v==null?"transparent":c};--c:${c}"></i></div>
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
  // Resolve "auto" every render rather than once, so a strike that only gets
  // a live price mid-session still lands on the premium view when it does.
  let mode = LMODE;
  if(mode === "auto") mode = havePrem ? "premium" : "index";
  if(!havePrem && mode === "premium") mode = "index";
  $("lb-index").classList.toggle("on", mode === "index");
  pb.classList.toggle("on", mode === "premium");
  $("lswitch").style.display = (r.targets||[]).some(v => v != null) ? "flex" : "none";

  const prem = mode === "premium";
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
      rs = money(amt);
    }
    return `<div class="rung"><div class="k">${k}</div>
      <div class="bar"><i style="width:${pct}%;background:${v==null?"transparent":c};--c:${c}"></i></div>
      <div class="n" style="color:${v==null?"var(--ink-3)":c}">${v==null?"—":num(v,dp)}</div>
      <div class="rs" style="color:${rs.startsWith("+")?"var(--up)":rs?"var(--down)":"var(--ink-3)"}">${rs}</div></div>`;
  }).join("");

  // Lot choices come from the server's own MAX_LOTS rather than a hard-coded
  // list, so raising the cap in config raises it here too.
  // Choices come from the server: whole lots for index options, 0.1 steps
  // for BTC, whose smallest order on Deribit is a tenth of a contract.
  const sess = (LAST && LAST.session) || {};
  const choices = sess.lot_choices || [1,2,3,4,5];
  const sel = $("lots");
  if(!LOTS_SYNCED && sess.lots){ LOTS = sess.lots; LOTS_SYNCED = true; }
  const sig = choices.join(",");
  if(sel.dataset.sig !== sig){
    sel.innerHTML = "";
    choices.forEach(v => sel.add(new Option(String(v), String(v))));
    sel.dataset.sig = sig;
  }
  if(!choices.includes(LOTS)){
    LOTS = choices.reduce((a,b) => Math.abs(b-LOTS) < Math.abs(a-LOTS) ? b : a, choices[0]);
  }
  sel.value = String(LOTS);

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

// -------------------------------------------------------------- risk
// Money between entry and stop, for the signal on screen or the ticket that
// is open, against the capital entered. The lots selector stays yours - this
// tool places nothing - but the share of the account each choice puts at
// stake is no longer something you have to work out in your head.
let CAPFOCUS = false;
function riskBox(r, tk, sess){
  const box = $("risk"), line = $("riskline");
  if(!box) return;
  sess = sess || {};
  const cap = sess.capital || null, rp = sess.risk_pct || 1;
  const inp = $("capital"), sel = $("riskpct");
  if(!CAPFOCUS) inp.value = cap ? Math.round(cap).toLocaleString(ccyLocale()) : "";
  inp.placeholder = "e.g. " + (CCY === "USD" ? "10,000" : "2,00,000");
  const ch = sess.risk_choices || [0.5,1,1.5,2];
  if(sel.options.length !== ch.length){
    sel.innerHTML = "";
    ch.forEach(v => sel.add(new Option(v + "%", String(v))));
  }
  sel.value = String(rp);

  const unit = (r.lot_size || 1) > 1 ? "lot" : "contract";
  const parts = [];
  let perLot = null, lots = LOTS, what = "this signal";
  if(tk && tk.open){
    if(tk.tracked_on === "premium" && tk.entry != null && tk.stop != null && tk.lot_size){
      perLot = (tk.entry - tk.stop) * tk.lot_size;
    }
    lots = tk.lots || 1; what = "this ticket";
  } else if(r.ltp != null && r.premium_stop != null && r.lot_size
            && r.bias && r.bias !== "NEUTRAL"){
    perLot = (r.ltp - r.premium_stop) * r.lot_size;
  }
  if(perLot != null && perLot > 0){
    const total = perLot * lots;
    let s = `Risk on ${what}: <b>${money(total,false)}</b> for ${lots} ${unit}${lots!==1?"s":""}`
          + ` (${money(perLot,false)} per ${unit}, entry to stop)`;
    if(cap){
      const pct = total / cap * 100;
      const cls = pct <= rp * 1.05 ? "ok" : pct <= rp * 2 ? "warn" : "bad";
      s += ` = <b class="${cls}">${pct.toFixed(2)}% of capital</b>.`;
      if(!(tk && tk.open)){
        // In the market's own step: whole lots, or tenths of a BTC contract.
        const ch = sess.lot_choices || [1];
        const step = ch[0] < 1 ? ch[0] : 1;
        const raw = cap * rp / 100 / perLot;
        const fit = Math.round(Math.floor(raw / step + 1e-9) * step * 100) / 100;
        s += fit >= step
          ? ` At ${rp}% risk the account carries <b>${fit} ${unit}${fit!==1?"s":""}</b>.`
          : ` <span class="bad">${step < 1 ? "The smallest size ("+step+" "+unit+")" : "One "+unit}`
            + ` is more than ${rp}% of the account</span>`
            + ` (${money(cap*rp/100,false)}) - skip it, or know you are sizing up.`;
      }
    } else {
      s += ". Enter your capital to see it as a share of the account.";
    }
    parts.push(s);
  } else if(!(tk && tk.open) && r.bias && r.bias !== "NEUTRAL"){
    // Only when there IS a trade to size; on "No trade" there is nothing to say.
    parts.push(cap ? "No live premium stop for this signal, so its risk in money cannot be worked out yet."
                   : "");
  }
  if(cap && sess.loss_limit){
    const booked = sess.booked || 0;
    const left = sess.loss_limit + Math.min(booked, 0);
    parts.push(`Daily loss limit <b>${money(sess.loss_limit,false)}</b> (${sess.loss_limit_pct}% of capital)`
      + (booked < 0 ? ` - today's closed trades ${money(booked)}, `
                    + (left > 0 ? `${money(left,false)} left before new tickets stop.`
                                : `<span class="bad">limit reached, no new tickets today.</span>`)
                    : " - no closed losses today."));
  }
  const sp = r.spread;
  if(sp && sp.pct != null && !(tk && tk.open) && r.bias && r.bias !== "NEUTRAL"){
    const lim = r.max_spread || 0;
    const cls = !lim ? "" : sp.pct <= lim / 3 ? "ok" : sp.pct <= lim ? "warn" : "bad";
    parts.push(`Spread <b class="${cls}">${sp.pct.toFixed(2)}%</b> of the price`
      + ` (${num(sp.bid,2)} bid / ${num(sp.ask,2)} ask)`
      + (lim && sp.pct > lim ? ` - over the ${lim}% limit, so no ticket; a market order`
           + ` would lose that much on entry and exit before the price moved.`
         : " - use a limit order near the middle."));
  }
  if(r.expiry_today){
    parts.push(`<span class="xday">Expires today</span> This contract settles at 15:30. `
      + "The premium moves several times faster than the ladder's 0.5-delta estimate, "
      + "both ways - and in the backtest, most of the profit came from days like this, "
      + "which is exactly where its option model is least trustworthy. Size for it.");
  }
  line.innerHTML = parts.filter(Boolean).join("<br>");
}
function postRisk(fields){
  fetch("/api/ticket", {method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body:new URLSearchParams(fields)})
    .then(x => x.json()).then(j => {
      if(j && j.session && LAST){ LAST.session = Object.assign(LAST.session||{}, j.session); render(LAST); }
    }).catch(()=>{});
}
$("capital").onfocus = () => { CAPFOCUS = true; };
$("capital").onblur  = e => {
  CAPFOCUS = false;
  const v = String(e.target.value || "").replace(/[^0-9.]/g, "");
  postRisk({capital: v || "0"});
};
$("capital").onkeydown = e => { if(e.key === "Enter") e.target.blur(); };
$("riskpct").onchange = e => postRisk({risk_pct: e.target.value});

$("lb-index").onclick   = () => { LMODE="index";   if(LAST) render(LAST); };
$("lb-premium").onclick = () => { LMODE="premium"; if(LAST) render(LAST); };
$("lots").onchange = e => {
  LOTS = parseFloat(e.target.value)||1;
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
// "25 Sep 2026 · Fri · 14 days left" from an ISO date. Calendar days to the
// expiry date; "expires today" on the day itself, because that is the day the
// premium behaves differently.
function expiryText(iso, short){
  if(!iso) return "";
  const d = new Date(String(iso).slice(0,10) + "T00:00:00");
  if(isNaN(d)) return "";
  const today = new Date(); today.setHours(0,0,0,0);
  const days = Math.round((d - today) / 86400000);
  const M = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const when = `${d.getDate()} ${M[d.getMonth()]} ${d.getFullYear()}`;
  const wd = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][d.getDay()];
  if(short){
    // For the index cards: "15 Sep · Tue · 4d", or "15 Sep · today".
    const dm = `${d.getDate()} ${M[d.getMonth()]}`;
    return days <= 0 ? `${dm} · today` : `${dm} · ${wd} · ${days}d`;
  }
  const left = days <= 0 ? "expires today" : days === 1 ? "1 day left" : days + " days left";
  return `${when} · ${wd} · ${left}`;
}

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
    const ex = expiryText(tk.expiry);
    c.innerHTML = `<b>${esc(tk.index)} ${esc(String(tk.strike))} ${esc(tk.option_type)}</b>`
                + (ex ? ` · expiry <b>${esc(ex)}</b>` : "")
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
               : money(pnl),
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
  // money() is global now, so the session strip, the ladder and the record
  // cannot disagree about the currency.
  // Always shown, even at zero. The desktop keeps its session strip on screen
  // all day saying "nothing yet", and a total that appears only once you are
  // up or down is a total you cannot trust to be complete.
  $("session").style.display = "flex";

  $("schips").innerHTML = keys.length
    ? keys.map(k => {
        const v = per[k], col = v>0?"var(--up)":v<0?"var(--down)":"var(--ink-2)";
        return `<span class="chip2" style="color:${col};border-color:${
          v>0?"#1f4a2c":v<0?"#5c2a18":"var(--bd)"}">${esc(k)} ${money(v)}</span>`;
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
  // Unattended running. It belongs on this line because it is the same kind of
  // fact as the ones beside it - how the tool is set to behave today - and
  // because this is the line you read when you wonder why nothing was logged.
  const ao = LAST && LAST.always_on;
  bits.push(`<span><button class="lbtn ao${ao?" on":""}" id="aotog" type="button"`
    + ` title="${ao
        ? "The tool runs from 09:10 to 15:40 whether or not this page is open."
        : "The tool only runs while this page is open. Nothing is analysed or "
          + "logged after you close the tab."}">`
    + `${ao ? "runs all session" : "runs only while open"}</button></span>`);
  $("sfeed").innerHTML = bits.join("");
  const tog = $("aotog");
  if(tog) tog.onclick = async () => {
    tog.disabled = true;
    try{
      const r = await fetch("/api/alwayson", {
        method:"POST",
        headers:{"Content-Type":"application/x-www-form-urlencoded"},
        body:"on=" + (LAST && LAST.always_on ? "0" : "1")});
      const j = await r.json();
      if(LAST) LAST.always_on = !!j.always_on;
      render(LAST);
      tick();
    }catch(e){ tog.disabled = false; }
  };
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
  let today = bars.filter(b => new Date(b[0]*1000).toDateString() === lastDay);
  if(today.length < 2){ blank(); return; }

  // The newest candle is up to fifteen minutes old and its close only moves
  // when the analysis refreshes. The streamed spot is where the market is
  // now, so the line is drawn out to it.
  const liveSpot = (LIVE && LIVE.spots && LIVE.spots[CUR] != null)
                   ? LIVE.spots[CUR] : null;
  if(liveSpot != null){
    today = today.slice();
    const last = today[today.length-1].slice();
    last[4] = liveSpot;
    last[2] = Math.max(last[2], liveSpot);
    last[3] = Math.min(last[3], liveSpot);
    today[today.length-1] = last;
  }
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
// ---------------------------------------------------------- room to run
// Which way there is room, how far, to what price, and what is stopping it.
// The engine has measured both sides all along; the page showed one bare
// number, so "606 pts of room" never said up or down, or up to where.
const ARROW = {up: "\u25B2", down: "\u25BC"};
function roomReading(r){
  const rm = r.room || {}, side = rm.side;
  if(side && rm[side] != null)
    return ARROW[side] + " " + num(rm[side],0) + " \u2192 " + num(rm[side + "_to"],0);
  return num(r.reach_points,0) + " pts";
}
function roomSub(r){
  const rm = r.room || {}, side = rm.side;
  if(side && rm[side] != null)
    return ARROW[side] + " " + num(rm[side],0) + " pts, to " + num(rm[side + "_to"],0);
  return r.reach_points == null ? "" : num(r.reach_points,0) + " pts of room";
}
function roomRun(r){
  const el = $("room");
  if(!el) return;
  const rm = (r && r.room) || {};
  if(rm.up == null && rm.down == null){ el.innerHTML = ""; return; }
  // An open ticket decides which side is "this trade", not the current
  // signal: with a PE running and the signal gone quiet, the room that matters
  // is still the room below.
  const tk = ((LAST && LAST.tickets) || {})[CUR];
  const open = tk && tk.ticket && tk.ticket.open !== false && tk.ticket.option_type;
  const side = open ? (tk.ticket.option_type === "CE" ? "up" : "down") : rm.side;
  const line = dir => {
    const pts = rm[dir], to = rm[dir + "_to"], cap = rm[dir + "_cap"];
    const mine = side === dir;
    const col = dir === "up" ? "var(--up)" : "var(--down)";
    return `<div class="rr${mine ? " mine" : ""}">`
      + `<span class="rr-d" style="color:${col}">${ARROW[dir]} ${dir === "up" ? "Up" : "Down"}</span>`
      + `<span class="rr-p">${pts == null ? "\u2014" : num(pts,0) + " pts"}</span>`
      + `<span class="rr-to">${to == null ? "" : "to <b>" + num(to,0) + "</b>"}</span>`
      + `<span class="rr-c">${cap ? "limited by " + esc(cap) : ""}</span>`
      + (mine ? `<span class="rr-tag">${open ? "open trade" : "this trade"}</span>` : "")
      + `</div>`;
  };
  el.innerHTML = `<div class="rr-h">Room to run</div>` + line("up") + line("down");
}

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
               roomReading(r),
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
  const ticks = [];
  for(let k=0;k<view.length;k++){
    const t = view[k][0];
    const day = new Date(t*1000).toDateString();
    const newDay = lastDay !== null && day !== lastDay;
    lastDay = day;
    if(k % everyN !== 0 && !newDay) continue;
    const x = Math.round(X(i0+k)) + 0.5;
    cx.strokeStyle = newDay ? C.bd : C.bdSoft;
    cx.beginPath(); cx.moveTo(x, PAD.t); cx.lineTo(x, PAD.t+plotH); cx.stroke();
    ticks.push({x, day: newDay, text: newDay ? fmtD(t) : fmtT(t)});
  }
  // Labels only where they fit. A day boundary lands wherever the session
  // starts, usually a bar or two from a regular tick, and the two used to be
  // printed on top of each other ("09 Sept" over "09:45"). Day labels are
  // placed first; a time label that would touch any placed label is skipped.
  cx.fillStyle = C.ink3;
  const placed = [];
  const fits = (x, wd) => placed.every(p => x + wd/2 + 8 < p[0] || x - wd/2 - 8 > p[1]);
  for(const pass of [true, false]){
    for(const tk of ticks){
      if(tk.day !== pass) continue;
      const wd = cx.measureText(tk.text).width;
      const x = Math.min(Math.max(tk.x, PAD.l + wd/2), w - PAD.r - wd/2);
      if(!fits(x, wd)) continue;
      placed.push([x - wd/2, x + wd/2]);
      cx.fillText(tk.text, x, PAD.t+plotH+6);
    }
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
  // Lines at their true prices; the tags on the right are spread at least a
  // tag's height apart, because T1, T2 and T3 are often a few points from
  // each other and their tags used to print one over the next.
  const lv = [[L.t1, "T1", C.up], [L.t2, "T2", C.up], [L.t3, "T3", C.up], [L.stop, "SL", C.down]]
    .filter(a => a[0] != null)
    .map(a => ({v: a[0], label: a[1], colour: a[2], y: Y(a[0])}))
    .filter(a => a.y >= PAD.t-1 && a.y <= PAD.t+plotH+1);
  lv.forEach(a => {
    cx.save(); cx.strokeStyle = a.colour; cx.lineWidth = 1; cx.setLineDash([5,4]);
    cx.beginPath(); cx.moveTo(PAD.l, Math.round(a.y)+0.5);
    cx.lineTo(w-PAD.r, Math.round(a.y)+0.5); cx.stroke(); cx.restore();
    a.ty = a.y;
  });
  // The current-price tag is drawn after these at its own height and never
  // moves, so it takes part as a fixed slot - otherwise it simply covered
  // whichever target sat nearest the price, which is usually T1.
  const lastBar = bars[bars.length-1];
  const slots = lv.slice();
  if(i1 >= bars.length && lastBar){
    const py = Y(lastBar[4]);
    if(py >= PAD.t && py <= PAD.t+plotH) slots.push({y: py, ty: py, fixed: true});
  }
  const tagLo = PAD.t + 8, tagHi = PAD.t + plotH - 8, GAP = 17;
  for(let pass = 0; pass < 30; pass++){
    slots.sort((a, b) => a.ty - b.ty);
    let moved = false;
    for(let i = 1; i < slots.length; i++){
      const a = slots[i-1], b = slots[i], d = b.ty - a.ty;
      if(d >= GAP) continue;
      const push = GAP - d; moved = true;
      if(a.fixed){ b.ty += push; }
      else if(b.fixed){ a.ty -= push; }
      else { a.ty -= push / 2; b.ty += push / 2; }
    }
    slots.forEach(t => { if(!t.fixed) t.ty = Math.min(tagHi, Math.max(tagLo, t.ty)); });
    if(!moved) break;
  }
  lv.forEach(a => {
    cx.save();
    cx.fillStyle = a.colour;
    cx.fillRect(w-PAD.r, a.ty-8, PAD.r, 16);
    cx.fillStyle = "#fff"; cx.font = "10px -apple-system,sans-serif";
    cx.textAlign = "left"; cx.textBaseline = "middle";
    cx.fillText(a.label + " " + Math.round(a.v).toLocaleString("en-IN"), w-PAD.r+4, a.ty);
    cx.restore();
  });

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
addEventListener("resize", () => { chartDraw(); sparkline(); heatMap(true); });

function render(s){
  if(!s) return;
  // Reset ONLY when CUR isn't a real index. Resetting because an index hasn't
  // loaded yet used to bounce the tab back and — far worse — show one index's
  // signal under another index's name.
  if(!CUR || !(s.order||[]).includes(CUR)) CUR=(s.order||[])[0];
  markets(s);
  greet(s);
  if(s.market === "crypto"){
    const strip = document.querySelector(".ticker");
    if(strip) strip.style.display = "none";
  }

  // The closing auction is its own state, not a shade of "open". Saying
  // "Market open" over an index that has held one value since 15:15 is the
  // one reading that sends you looking for a bug in the tool.
  CCY = s.currency || "INR";
  const cas = !!s.closing_auction && !s.stale;
  $("beat").className = "beat" + (s.market_open && !s.stale && !cas ? " live" : "");
  $("mkt").textContent = s.stale ? "feed down"
    : (cas ? "Closing auction" : (s.market_open?"Market open":"Market closed"));
  $("mkt").title = cas
    ? "From 15:15 every constituent is in NSE's closing auction, so the index "
      + "holds one value until the closing prices publish around 15:35. "
      + "Options trade until 15:40, so an open ticket is still tracked."
    : "";
  $("upd").textContent = s.updated ? s.updated+" IST" : "—";
  // Which market this session is in. Shown always, not only when there is a
  // choice: on a screen where every number is a currency, "which market am I
  // looking at" should never be something you infer from the ticker names.
  const sw = $("mktsw");
  if(sw){
    if(s.market_label){
      sw.textContent = s.market_label;
      sw.style.display = "inline-flex";
      sw.title = (s.markets && s.markets.length > 1)
        ? "Switch market" : "This server runs one market";
    } else sw.style.display = "none";
  }
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
  // The signal card glows in the signal's colour, and so does the 3D scene.
  { const sc = $("sigcard"); if(sc) sc.dataset.bias = bull ? "up" : bear ? "down" : ""; }
  SCENE_BIAS = bull ? "up" : bear ? "down" : "";
  $("bias").textContent = bull?"Buy CE":bear?"Buy PE":"No trade";
  $("bias").style.color = bull?"var(--up)":bear?"var(--down)":"var(--ink-3)";
  if(r.confidence && r.confidence!=="N/A"){
    $("conftag").style.display="inline-flex";
    $("conftag").className="tag "+(bull?"up":bear?"down":"flat");
    $("conftag").textContent=(bull||bear? r.strike+" "+(r.option_type==="CE"?"Call":"Put")+" · ":"")+r.confidence+" confidence";
  } else $("conftag").style.display="none";

  // The expiry of the contract in play - the open ticket's own when one is
  // running, since that is the contract actually being tracked.
  {
    const tkOpen = ((s.tickets||{})[CUR]||{}).ticket;
    const running = !!(tkOpen && tkOpen.open);
    const ex = expiryText(running && tkOpen.expiry ? tkOpen.expiry : r.expiry);
    const et = $("exptag");
    // Always shown when known - with no signal it is the nearest expiry the
    // tool would use, which is still worth knowing before one arrives.
    if(ex){
      et.style.display = "inline-flex";
      et.className = "tag " + (ex.includes("today") ? "warn" : "flat");
      et.textContent = (bull || bear || running ? "Expiry " : "Nearest expiry ")
                     + ex + (running ? " · your ticket" : "");
    } else et.style.display = "none";
  }

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
         roomSub(r),
         r.reach_to_risk==null?"":(r.reach_to_risk>=2?"var(--up)":r.reach_to_risk<0.6?"var(--down)":"var(--warn)"));

  const tstate = (s.tickets||{})[CUR] || null;
  ticketBox(r, tstate);
  ladder(r, tstate && tstate.ticket);
  riskBox(r, tstate && tstate.ticket, s.session);
  sessionStrip(s.session, s.order);
  chainFetch();

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
  roomRun(r);
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
  sparkline();
  heatMap();

  const rec=s.record||{};
  $("record").innerHTML = rec.n
    ? `<div class="rec">
        ${tile("Trades", rec.n, rec.first+" → "+rec.last)}
        ${tile("Reached T1", rec.t1+"%","")}
        ${tile("Reached T2", rec.t2+"%","")}
        ${tile("Stopped out", rec.sl+"%","")}
        ${rec.net!=null?tile("Net",money(rec.net,false),
            rec.wins+" wins / "+rec.losses+" losses",
            rec.net>=0?"var(--up)":"var(--down)"):""}
       </div>`
    : `<p style="color:var(--ink-3);font-size:13px;margin:0">No completed trades recorded yet.
       This fills in as signals close, and it shows losses as well as wins.</p>`;
}

// ------------------------------------------------------------ market map
// Each constituent sized by its index weight and coloured by its move today.
// The layout arrives already computed — squarify() lives in market_map.py and
// is the same code the desktop window uses, so there is one implementation of
// that arithmetic rather than two that drift apart.
//
// The colour ramp is built here rather than server-side because it belongs to
// this page's palette: market_map.heat_colour() answers in the desktop app's
// dark theme, and a dark tile on a white card would look like a bug.
function heat(pct){
  if(pct == null) return "#1b1b20";
  const p = Math.max(-2, Math.min(2, pct)) / 2;
  // Toward white at zero, so "barely moved" reads as barely coloured.
  const mix = (a, b, t) => Math.round(a + (b - a) * t);
  const [r0,g0,b0] = [30,30,36];
  // The SAME up/down the rest of the screen uses — var(--up) #4caf50 and
  // var(--down) #ff5722 — as literals, because a canvas-free gradient cannot
  // read a CSS variable. They were left as the old light-theme pair, so a
  // falling stock in the map was a different red from a falling number six
  // inches above it. If the theme's up/down ever change, change these too.
  const [r1,g1,b1] = p >= 0 ? [43,224,138] : [239,85,112];
  const t = Math.abs(p);
  return `rgb(${mix(r0,r1,t)},${mix(g0,g1,t)},${mix(b0,b1,t)})`;
}

let MAPKEY = null, MAPAT = 0;

async function heatMap(force){
  const box = $("mapwrap");
  if(!box) return;
  const w = Math.round(box.clientWidth), h = Math.round(box.clientHeight);
  if(w < 40) return;
  if(!force && CUR === MAPKEY && Date.now() - MAPAT < 2000) return;
  MAPKEY = CUR; MAPAT = Date.now();
  $("mapidx").textContent = CUR || "—";
  let d;
  try{
    d = await (await fetch(`/api/map/${encodeURIComponent(CUR)}?w=${w}&h=${h}`,
                           {cache:"no-store"})).json();
  }catch(e){ return; }
  if(d.index !== CUR) return;              // the user switched mid-flight

  if(!d.tiles || !d.tiles.length){
    box.innerHTML = `<div style="display:flex;height:100%;align-items:center;
      justify-content:center;color:var(--ink-3);font-size:13px;padding:20px;
      text-align:center">${d.ready
        ? "Waiting for the first tick on the constituents."
        : "Connect Zerodha to stream the constituents."}</div>`;
    $("mapbreadth").textContent = "";
    return;
  }

  box.innerHTML = d.tiles.map(t => {
    const bg = heat(t.pct);
    // Dark text on pale tiles, white on saturated ones, so the label stays
    // readable at both ends of the ramp instead of only in the middle.
    const strong = t.pct != null && Math.abs(t.pct) > 0.8;
    const size = t.w < 34 || t.h < 20 ? "mini" : (t.w < 62 || t.h < 32 ? "tiny" : "");
    const pct = t.pct == null ? "—"
              : (t.pct >= 0 ? "+" : "\u2212") + Math.abs(t.pct).toFixed(2) + "%";
    return `<div class="mtile ${size}" title="${esc(t.sym)} \u00b7 ${esc(t.sector)}`
         + ` \u00b7 ${t.weight}% of the index \u00b7 ${pct}"`
         + ` style="left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;`
         + `background:${bg};color:${strong ? "#fff" : "var(--ink-2)"}">`
         + `<b>${esc(t.sym)}</b><i>${pct}</i></div>`;
  }).join("");

  const b = d.breadth;
  $("mapbreadth").innerHTML = b
    ? `<b>${b.up}</b> up \u00b7 <b>${b.down}</b> down`
      + (b.flat ? ` \u00b7 <b>${b.flat}</b> flat` : "")
      + ` &nbsp;|&nbsp; weighted <span class="w" style="color:${
          b.weighted>0?"var(--up)":b.weighted<0?"var(--down)":"var(--ink-2)"}">`
      + `${b.weighted>=0?"+":"\u2212"}${Math.abs(b.weighted).toFixed(2)}%</span>`
      + ` &nbsp;|&nbsp; ${b.known} of ${b.total} streaming`
    : "Waiting for ticks.";
}

// -------------------------------------------------------- world markets
// Rendered twice into the same track. The animation slides it exactly half
// its width, so the copy lands where the original started and the loop has
// no visible seam. Refreshed on the server's own minute, not the page's.
function renderTicker(rows){
  if(!rows || !rows.length) return;
  // Roughly six seconds of travel per item, so adding markets makes the strip
  // longer rather than faster.
  $("tkt").style.setProperty("--tkdur", (rows.length * 6) + "s");
  let group = null;
  const one = rows.map(r => {
    let sep = "";
    if(r.group && r.group !== group){
      // No divider before the first block — it would read as a stray label.
      if(group !== null) sep = `<span class="tk-sep">WORLD</span>`;
      group = r.group;
    }
    const up = r.pct == null ? 0 : r.pct;
    const col = up > 0 ? "var(--up)" : up < 0 ? "var(--down)" : "var(--ink-3)";
    const chg = r.change == null ? ""
      : `<span class="c" style="color:${col}">${r.change>=0?"+":"\u2212"}`
        + `${Math.abs(r.change).toLocaleString("en-IN")}`
        + (r.pct==null?"":` (${r.pct>=0?"+":"\u2212"}${Math.abs(r.pct).toFixed(2)}%)`)
        + `</span>`;
    return sep + `<span class="tk"><i class="dot" style="background:${col}"></i>`
         + `<span class="n">${esc(r.label)}</span>`
         + `<span class="p">${r.price.toLocaleString("en-IN",
              {minimumFractionDigits:r.dp,maximumFractionDigits:r.dp})}</span>`
         + `${chg}</span>`;
  }).join("");
  $("tkt").innerHTML = one + one;      // the seamless half
}

async function markets_(){
  // The strip is Indian indices, sectors and India VIX - context for the
  // Indian screen and noise on the crypto one, so it is not shown or fetched there.
  const strip = document.querySelector(".ticker");
  if(LAST && LAST.market === "crypto"){
    if(strip) strip.style.display = "none";
    return;
  }
  if(strip) strip.style.display = "";
  try{
    const d = await (await fetch("/api/markets",{cache:"no-store"})).json();
    renderTicker(d.rows);
  }catch(e){}
}

// The name in the greeting. Accounts here are email addresses and nobody has
// given us a display name, so the local part is the closest thing to one —
// and it is what the person typed, which beats inventing a formatting rule
// for somebody else's name.
function greet(s){
  const who = $("who");
  const email = s && s.user;
  if(!email){ who.textContent = "—"; return; }
  who.textContent = email.split("@")[0];
  who.title = email;
  const k = s.kite || {};
  // Crypto needs no broker and trades none of the three indices, so the
  // Zerodha line and the index names would both be describing the wrong screen.
  if(s.market === "crypto"){
    $("said").textContent = "Bitcoin options on Deribit, priced live in dollars - "
                          + "one screen, around the clock.";
    const bs = $("brandsub"); if(bs) bs.textContent = "BTC · Deribit · 24/7";
    return;
  }
  $("said").textContent = k.connected
    ? (k.user_id ? `Connected to Zerodha as ${k.user_id}. Nifty, Bank Nifty and `
                 + `Sensex — one screen for the session.`
                 : "Connected to Zerodha. Nifty, Bank Nifty and Sensex — one "
                 + "screen for the session.")
    : "Connect your Zerodha account to see live signals.";
}

async function tick(){
  try{ LAST=await (await fetch("/api/state",{cache:"no-store"})).json(); render(LAST); }
  catch(e){ $("mkt").textContent="connection lost"; $("beat").className="beat"; }
}

// ---------------------------------------------------------------- ticks
// The prices, on their own timer. The full state is three recommendations and
// their reasoning and only changes when the analysis runs; the prices change
// several times a second. Asking for them separately is what makes the spot
// and the premium move here the way they move in the desktop app, instead of
// stepping once every poll.
async function priceTick(){
  if(document.hidden) return;              // a background tab is not watching
  let t;
  // A failed poll is itself news. Returning quietly left the tag reading
  // "Live" over prices that had stopped arriving the moment the server or the
  // network went away - the exact claim the tag exists to avoid making.
  try{ t = await (await fetch("/api/tick",{cache:"no-store"})).json(); }
  catch(e){ feedTag(false, null); return; }
  LIVE = t;
  if(!LAST || !LAST.indices) return;

  // Merge the streamed prices into the state the page renders from, then
  // render normally. Patching individual cells was quicker but it only moved
  // the two numbers it knew about — the ladder, the tiles and the progress
  // bars all still stepped once every thirty seconds, which is exactly what
  // "the rest of it isn't live" meant.
  let changed = false;
  for(const [k, px] of Object.entries(t.spots||{})){
    const r = LAST.indices[k];
    if(r && px != null && r.spot !== px){ r.spot = px; changed = true; }
  }
  // Two sources of a premium, and the ticket's wins: an open ticket is tracked
  // on its own frozen strike, while `ltp` is whatever strike is being suggested
  // right now. Applying the suggestion over a live ticket would quote a
  // different contract under the ticket's own numbers.
  for(const [k, px] of Object.entries(t.ltp||{})){
    const r = LAST.indices[k];
    if(r && px != null && r.ltp !== px){ r.ltp = px; changed = true; }
  }
  for(const [k, px] of Object.entries(t.premium||{})){
    const r = LAST.indices[k];
    if(r && px != null && r.ltp !== px){ r.ltp = px; changed = true; }
  }
  for(const [k, tk] of Object.entries(t.tickets||{})){
    if(!tk || !LAST.tickets || !LAST.tickets[k]) continue;
    const cur = LAST.tickets[k].ticket;
    if(cur){
      if(cur.now !== tk.now || cur.pnl !== tk.pnl){ changed = true; }
      cur.now = tk.now; cur.pnl = tk.pnl;
      cur.hit = tk.hit; cur.hit_time = tk.hit_time;
      cur.sl_hit = tk.sl_hit; cur.sl_hit_time = tk.sl_hit_time;
      cur.status = tk.status; cur.open = tk.open;
    }
  }
  // The candle currently being built. Without this the chart's right-hand
  // edge froze for fifteen minutes at a time while every number around it
  // moved — the bar was only ever redrawn when a finished one was fetched.
  const bar = (t.bar||{})[CH.key];
  if(bar && CH.data && CH.data.candles && CH.data.candles.length){
    const bars = CH.data.candles, last = bars[bars.length-1];
    if(last[0] === bar.t){                       // same bar, still forming
      last[1]=bar.o; last[2]=bar.h; last[3]=bar.l; last[4]=bar.c;
    } else if(bar.t > last[0]){                  // a new bar has opened
      bars.push([bar.t, bar.o, bar.h, bar.l, bar.c, null]);
      if(CH.pinned) CH.i0 = Math.max(0, bars.length - CH.n);
    }
    chartDraw();
  }
  // One writer per element. This used to set #upd and #beat, both of which
  // render() also sets from the market's state - so every 250ms poll and every
  // 3s render overwrote each other and the corner flickered between the clock
  // and the word "live". The feed gets its own tag and nothing else touches it.
  feedTag(t.live, t.age);

  if(changed) render(LAST);

  // A target reached on a tick changes more than a price — the ticket may
  // have closed and the day's totals moved with it, and only the full state
  // knows that.
  const tk = (t.tickets||{})[CUR];
  if(tk){
    const sig = JSON.stringify([tk.hit, tk.sl_hit, tk.open]);
    if(LASTHIT !== null && sig !== LASTHIT) tick();
    LASTHIT = sig;
  }
}
let LIVE = null, LASTHIT = null;
// The screen counts one currency and must never guess which. Crypto premiums
// are dollars per contract; index premiums are rupees per lot. Printing one
// behind the other's sign is a wrong number that looks like a right one, and
// nothing on the page would give it away.
let CCY = "INR";
function ccySym(){ return CCY === "USD" ? "$" : "\u20b9"; }
function ccyLocale(){ return CCY === "USD" ? "en-US" : "en-IN"; }
function money(v, signed){
  const n = Math.abs(Math.round(v)).toLocaleString(ccyLocale());
  const sign = signed === false ? "" : (v >= 0 ? "+" : "\u2212");
  return sign + ccySym() + n;
}
// Written on every poll, so it only touches the DOM when something actually
// changed - otherwise this is four needless mutations a second.
let FEEDSTATE = null;
function feedTag(live, age){
  const el = $("feed");
  if(!el) return;
  // Silence is only a fault while the market is trading. After the close the
  // ticks stop because there is nothing to send, and "Stalled" in amber next
  // to "Market closed" reads as a broken tool rather than an ended day. The
  // auction counts as trading here: options are still printing.
  const trading = !!(LAST && LAST.market_open);
  const label = live ? "Live" : (age != null && trading ? "Stalled" : "Idle");
  const cls = "feedtag" + (live ? " on" : (age != null && trading ? " off" : ""));
  // The age only enters the signature while stalled, where it is the whole
  // point of the tooltip; when live it would rewrite this four times a second.
  const sig = label + "|" + cls + "|" + (live || age == null ? "" : Math.round(age));
  if(sig === FEEDSTATE) return;
  FEEDSTATE = sig;
  el.textContent = label;
  el.className = cls;
  el.title = live ? "Prices are streaming from Zerodha's tick socket."
    : (age == null ? "No tick socket yet."
       : !trading ? "The session is over, so there is nothing left to stream. "
                    + "These are the closing numbers."
       : "No tick for " + Math.round(age) + "s \u2014 the socket is open but "
         + "nothing is arriving. The numbers on screen are the last ones sent.");
}
$("honest").innerHTML="A three-year backtest of this rule set on 15-minute candles "+
  "measured roughly break-even before costs and negative after them, and its targets "+
  "are reached about a third of the time. It is published so it can be checked, not "+
  "because it is known to work. "+
  '<a href="/results" style="color:inherit;text-decoration:underline">The figures.</a>';
tick(); setInterval(tick,3000);
// Prices, four times a second. The server reads them straight out of the
// tick socket and answers in ~20ms, so the poll interval was the only
// thing left standing between the exchange and the screen.
priceTick(); setInterval(priceTick,250);
markets_(); setInterval(markets_,60000);
addEventListener("resize",()=>{clearTimeout(window._rz);
  window._rz=setTimeout(()=>render(LAST),260)});
</script>
<script>
// ============================================================ workspace
// Borrowed from OpenTerminal: panels you can put away, and one keystroke that
// gets you anywhere. Not its drag-and-resize grid - this screen has an order
// that was chosen (signal first, reasoning last) and a layout engine would
// mostly be a way to break it - but the useful half of the idea is here:
// hide what you do not use, and it stays hidden on your next visit.
const PANELS = [["signal","Signal card"], ["trend","Trend, day move, confidence"],
                ["chart","Price chart"], ["record","Track record"],
                ["range","Today's range"], ["chain","Option chain"],
                ["map","Market map"], ["why","Why - every input"]];
const PKEY = "nbs.panels.v1";
let HIDDEN = new Set();
try{ HIDDEN = new Set(JSON.parse(localStorage.getItem(PKEY) || "[]")); }catch(e){}
function applyPanels(){
  document.querySelectorAll("[data-panel]").forEach(el => {
    el.hidden = HIDDEN.has(el.dataset.panel);
  });
}
function togglePanel(k){
  if(HIDDEN.has(k)) HIDDEN.delete(k); else HIDDEN.add(k);
  try{ localStorage.setItem(PKEY, JSON.stringify([...HIDDEN])); }catch(e){}
  applyPanels();
  if(!HIDDEN.has("chart")) { try{ chartDraw(); }catch(e){} }
  if(!HIDDEN.has("chain")) chainFetch(true);
}
applyPanels();

// ============================================================ option chain
// The chain the signal was computed from: calls left, puts right, strikes
// down the middle, the money highlighted and the suggested contract ringed.
// Nothing new is fetched from anywhere - the server already had this.
let CHAIN_AT = 0, CHAIN_FOR = null, CHAIN_SCROLLED = null;
const oiFmt = v => v == null ? "—" :
  new Intl.NumberFormat("en-IN", {notation:"compact", maximumFractionDigits:1}).format(v);
async function chainFetch(force){
  const card = $("chaincard");
  if(!card || card.hidden || !CUR) return;
  if(!force && CHAIN_FOR === CUR && Date.now() - CHAIN_AT < 20000) return;
  CHAIN_AT = Date.now(); CHAIN_FOR = CUR;
  try{
    const d = await (await fetch("/api/chain?index=" + encodeURIComponent(CUR),
                                 {cache:"no-store"})).json();
    chainDraw(d);
  }catch(e){}
}
function chainDraw(d){
  const t = $("chain"), bar = $("chainbar");
  if(!t) return;
  if(!d || !d.rows || !d.rows.length){
    t.innerHTML = "";
    $("chainhead").textContent = "—";
    bar.innerHTML = "No chain from the broker right now.";
    return;
  }
  $("chainhead").textContent = (d.index || "") + " · expiry " + (d.expiry || "—");
  const cell = (o, kind, strike) => {
    const mine = d.suggested && d.suggested.strike === strike
              && (d.suggested.type === (kind === "ce" ? "CE" : "PE"));
    const wall = (kind === "ce" ? d.call_wall : d.put_wall) === strike;
    const wide = o.spread != null && o.spread > 3;
    return `<td class="px${mine?" mine":""}">${o.ltp == null ? "—" : num(o.ltp,2)}</td>`
         + `<td>${o.bid == null ? "—" : num(o.bid,2)}</td>`
         + `<td>${o.ask == null ? "—" : num(o.ask,2)}</td>`
         + `<td class="${wide?"wide":""}">${o.spread == null ? "—" : o.spread.toFixed(1)+"%"}</td>`
         + `<td class="${wall?"wall":""}">${oiFmt(o.oi)}</td>`;
  };
  t.innerHTML =
    `<thead><tr><th colspan="5" class="ce" style="text-align:center">Calls</th>`
    + `<th class="k">Strike</th>`
    + `<th colspan="5" class="pe" style="text-align:center">Puts</th></tr>`
    + `<tr><th>LTP</th><th>Bid</th><th>Ask</th><th>Spr</th><th>OI</th><th class="k"></th>`
    + `<th>LTP</th><th>Bid</th><th>Ask</th><th>Spr</th><th>OI</th></tr></thead><tbody>`
    + d.rows.map(r => {
        const atm = r.strike === d.atm;
        return `<tr class="${atm?"atm":""}" data-k="${r.strike}">`
             + cell(r.ce, "ce", r.strike)
             + `<td class="k">${num(r.strike,0)}</td>`
             + cell(r.pe, "pe", r.strike) + `</tr>`;
      }).join("") + `</tbody>`;
  const sym = d.currency === "USD" ? "$" : "₹";
  bar.innerHTML =
    `<span>Spot <b>${d.spot == null ? "—" : num(d.spot,2)}</b></span>`
    + `<span>PCR <b>${d.pcr == null ? "—" : d.pcr}</b></span>`
    + (d.max_pain != null ? `<span>Max pain <b>${num(d.max_pain,0)}</b></span>` : "")
    + (d.call_wall != null ? `<span>Call wall <b>${num(d.call_wall,0)}</b></span>` : "")
    + (d.put_wall != null ? `<span>Put wall <b>${num(d.put_wall,0)}</b></span>` : "")
    + `<span>Prices in ${sym}, per unit of the contract. Spr = the bid-ask gap; `
    + `over 3% and the tool holds the ticket.</span>`;
  // Centre the money once per index, not on every refresh - otherwise the
  // table yanks itself back while you are reading a far strike.
  if(CHAIN_SCROLLED !== d.index){
    CHAIN_SCROLLED = d.index;
    const row = t.querySelector("tr.atm");
    if(row && row.scrollIntoView) row.scrollIntoView({block:"center"});
  }
}

// ============================================================ palette
const PAL = {items: [], sel: 0};
function palItems(){
  const out = [];
  ((LAST && LAST.order) || []).forEach(k => out.push(
    {t:"Index", label:k, sub:"show this index", run:() => { CUR = k; render(LAST); chainFetch(true); }}));
  if(((LAST && LAST.markets) || []).length > 1)
    out.push({t:"Market", label:"Switch market", sub:"Indian indices / crypto",
              run:() => location.href = "/market"});
  PANELS.forEach(([k, label]) => out.push(
    {t:"Panel", label:(HIDDEN.has(k) ? "Show " : "Hide ") + label,
     sub:HIDDEN.has(k) ? "hidden" : "showing", run:() => togglePanel(k)}));
  out.push({t:"Go", label:"Review - your results so far", run:() => location.href="/review"});
  out.push({t:"Go", label:"How it works", run:() => location.href="/how-it-works"});
  out.push({t:"Go", label:"Zerodha connection", run:() => location.href="/connect"});
  out.push({t:"Go", label:"Results", run:() => location.href="/results"});
  out.push({t:"Do", label:"Set capital and risk per trade",
            run:() => { const c = $("capital"); if(c){ c.scrollIntoView({block:"center"}); c.focus(); } }});
  out.push({t:"Do", label:"Clear the open ticket",
            run:() => { const b = $("tclear"); if(b && b.style.display !== "none") b.click(); }});
  out.push({t:"Do", label:"Log out", run:() => location.href="/logout"});
  return out;
}
function palRender(){
  const q = $("palq").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  PAL.items = palItems().filter(it => {
    const hay = (it.t + " " + it.label + " " + (it.sub || "")).toLowerCase();
    return q.every(w => hay.includes(w));
  });
  if(PAL.sel >= PAL.items.length) PAL.sel = Math.max(0, PAL.items.length - 1);
  $("pallist").innerHTML = PAL.items.map((it, i) =>
    `<div class="palrow${i === PAL.sel ? " on" : ""}" data-i="${i}">`
    + `<span class="t">${esc(it.t)}</span><span>${esc(it.label)}</span>`
    + (it.sub ? `<span class="s">${esc(it.sub)}</span>` : "") + `</div>`).join("")
    || `<div class="palrow"><span class="s">nothing matches</span></div>`;
}
function palOpen(){
  $("pal").hidden = false; $("palq").value = ""; PAL.sel = 0; palRender(); $("palq").focus();
}
function palClose(){ $("pal").hidden = true; $("palq").blur(); }
function palRun(){
  const it = PAL.items[PAL.sel];
  palClose();
  if(it && it.run) it.run();
}
$("palq").addEventListener("input", () => { PAL.sel = 0; palRender(); });
$("pallist").addEventListener("click", e => {
  const row = e.target.closest(".palrow");
  if(!row || row.dataset.i === undefined) return;
  PAL.sel = +row.dataset.i; palRun();
});
$("pal").addEventListener("mousedown", e => { if(e.target === $("pal")) palClose(); });
document.addEventListener("keydown", e => {
  const open = !$("pal").hidden;
  if((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k"){
    e.preventDefault(); open ? palClose() : palOpen(); return;
  }
  if(!open) return;
  if(e.key === "Escape"){ e.preventDefault(); palClose(); }
  else if(e.key === "ArrowDown"){ e.preventDefault(); PAL.sel = Math.min(PAL.sel + 1, PAL.items.length - 1); palRender(); }
  else if(e.key === "ArrowUp"){ e.preventDefault(); PAL.sel = Math.max(PAL.sel - 1, 0); palRender(); }
  else if(e.key === "Enter"){ e.preventDefault(); palRun(); }
});
</script>
<script>
// ------------------------------------------------------------ the 3D scene
// nbs-signal-3d.html's background, drawn with a 2D canvas and a hand-rolled
// perspective projection instead of Three.js - the page's security policy
// loads no outside script, and this is a few hundred lines lighter besides.
// Two drifting particle fields, a glow that takes the colour of the current
// signal, an amber one opposite, and a slowly turning 3D candlestick chart
// built from the selected index's own last candles. Capped at ~30 fps, paused
// with the tab, drawn once and left still for anyone who prefers less motion.
(function(){
  const cv = document.getElementById("bg3d");
  if(!cv || !cv.getContext) return;
  const ctx = cv.getContext("2d");
  const still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  let W = 0, H = 0;
  function field(n, sx, sy, sz, oz){
    const a = new Float32Array(n * 3);
    for(let i = 0; i < n; i++){
      a[i*3] = (Math.random() - .5) * sx; a[i*3+1] = (Math.random() - .5) * sy;
      a[i*3+2] = (Math.random() - .5) * sz + oz;
    }
    return a;
  }
  const N = window.innerWidth < 720 ? 260 : 700;
  const F1 = field(N, 1600, 1000, 800, -200), F2 = field(N, 1800, 1100, 900, -400);
  const cam = {x: 0, y: 0, z: 420};
  let mx = 0, my = 0;
  window.addEventListener("mousemove", e => {
    mx = e.clientX / (W || 1) - .5; my = e.clientY / (H || 1) - .5;
  }, {passive: true});
  const TAN = Math.tan(30 * Math.PI / 180);
  function proj(x, y, z){
    const d = cam.z - z;
    if(d <= 1) return null;
    const f = (H / 2) / TAN / d;
    return [W / 2 + (x - cam.x) * f, H / 2 - (y - cam.y) * f, f];
  }
  function points(F, ry, rx, rgb, size, alpha){
    const cy = Math.cos(ry), sy = Math.sin(ry), cx = Math.cos(rx), sx = Math.sin(rx);
    ctx.fillStyle = `rgba(${rgb},${alpha})`;
    for(let i = 0; i < F.length; i += 3){
      const x = F[i], y = F[i+1], z = F[i+2];
      const x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
      const y1 = y * cx - z1 * sx, z2 = y * sx + z1 * cx;
      const p = proj(x1, y1, z2);
      if(!p) continue;
      if(p[0] < -4 || p[0] > W + 4 || p[1] < -4 || p[1] > H + 4) continue;
      // Three.js's own point attenuation: size x (half the height / depth).
      const r = Math.min(4, Math.max(.4, size * (H / 2) / (cam.z - z2)));
      ctx.fillRect(p[0] - r / 2, p[1] - r / 2, r, r);
    }
  }
  function glow(x, y, z, radius, rgb, a){
    const p = proj(x, y, z);
    if(!p) return;
    const R = radius * p[2];
    const g = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], R);
    g.addColorStop(0, `rgba(${rgb},${.66 * a})`);
    g.addColorStop(.4, `rgba(${rgb},${.26 * a})`);
    g.addColorStop(1, `rgba(${rgb},0)`);
    ctx.fillStyle = g;
    ctx.fillRect(p[0] - R, p[1] - R, R * 2, R * 2);
  }
  // The candles: the selected index's last 14, or a gentle stand-in until the
  // chart has loaded. Scaled to the same height whatever the index's price.
  function candles(){
    const d = (typeof CH !== "undefined" && CH.data && CH.data.candles) || [];
    let bars = d.slice(-14).map(b => ({o: +b[1], h: +b[2], l: +b[3], c: +b[4]}))
                .filter(b => isFinite(b.o) && isFinite(b.c) && isFinite(b.h) && isFinite(b.l));
    if(bars.length < 6){
      bars = []; let p = 100;
      for(let i = 0; i < 14; i++){ const o = p; p += Math.sin(i * 1.7) * 3 - .6;
        bars.push({o, c: p, h: Math.max(o, p) + 1.6, l: Math.min(o, p) - 1.6}); }
    }
    const hi = Math.max(...bars.map(b => b.h)), lo = Math.min(...bars.map(b => b.l));
    const k = 200 / Math.max(hi - lo, 1e-9), mid = (hi + lo) / 2;
    return bars.map(b => ({o: (b.o - mid) * k, c: (b.c - mid) * k,
                           h: (b.h - mid) * k, l: (b.l - mid) * k}));
  }
  let UP = "#2be08a", DN = "#ef5570";
  function candleChart(t){
    const bars = candles();
    const ry = -0.35 + Math.sin(t * .12) * .06, rx = -0.15;
    const oy = -80 + Math.sin(t * .25) * 10, ox = 180, oz = -520;
    const cy = Math.cos(ry), sy = Math.sin(ry), cx = Math.cos(rx), sx = Math.sin(rx);
    const T = (x, y, z) => {
      const x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
      const y1 = y * cx - z1 * sx, z2 = y * sx + z1 * cx;
      return proj(x1 + ox, y1 + oy, z2 + oz);
    };
    bars.forEach((b, i) => {
      const x = (i - bars.length / 2) * 42, top = Math.max(b.o, b.c), bot = Math.min(b.o, b.c);
      const h = Math.max(top - bot, 1.2), w = 9, dp = 3;
      const col = b.c >= b.o ? UP : DN;
      const w1 = T(x, b.l, 0), w2 = T(x, b.h, 0);
      if(w1 && w2){
        ctx.globalAlpha = 1; ctx.strokeStyle = "rgba(139,147,163,.35)"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(w1[0], w1[1]); ctx.lineTo(w2[0], w2[1]); ctx.stroke();
      }
      const face = (pts, a) => {
        const q = pts.map(p => T(p[0], p[1], p[2]));
        if(q.some(p => !p)) return;
        ctx.globalAlpha = a; ctx.fillStyle = col;
        ctx.beginPath(); ctx.moveTo(q[0][0], q[0][1]);
        for(let j = 1; j < q.length; j++) ctx.lineTo(q[j][0], q[j][1]);
        ctx.closePath(); ctx.fill();
      };
      face([[x+w, bot, -dp], [x+w, bot, dp], [x+w, bot+h, dp], [x+w, bot+h, -dp]], .2);
      face([[x-w, bot+h, dp], [x+w, bot+h, dp], [x+w, bot+h, -dp], [x-w, bot+h, -dp]], .26);
      face([[x-w, bot, dp], [x+w, bot, dp], [x+w, bot+h, dp], [x-w, bot+h, dp]], .38);
    });
    ctx.globalAlpha = 1;
  }
  let last = 0;
  const t0 = performance.now();
  function frame(now, once){
    if(!once && now - last < 33){ requestAnimationFrame(frame); return; }
    last = now;
    const t = (now - t0) / 1000;
    cam.x += (mx * 60 - cam.x) * .02; cam.y += (-my * 40 - cam.y) * .02;
    ctx.clearRect(0, 0, W, H);
    ctx.globalCompositeOperation = "lighter";
    const sig = SCENE_BIAS === "up" ? "43,224,138" : SCENE_BIAS === "down" ? "239,85,112" : "77,148,232";
    glow(220, 120 + Math.sin(t * .4) * 25, -150, 350, sig, SCENE_BIAS ? .85 + Math.sin(t * .8) * .1 : .45);
    glow(-260 + Math.cos(t * .3) * 30, -180, -250, 250, "242,163,61", .7);
    points(F1, t * .015, t * .006, "43,224,138", 2.2, .55);
    points(F2, -t * .01, 0, "242,163,61", 1.6, .3);
    ctx.globalCompositeOperation = "source-over";
    // On a phone the chart would sit behind the index cards themselves.
    if(W >= 720) candleChart(t);
    if(!once && !still) requestAnimationFrame(frame);
  }
  function size(){
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    W = window.innerWidth; H = window.innerHeight;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cs = getComputedStyle(document.documentElement);
    UP = cs.getPropertyValue("--up").trim() || UP; DN = cs.getPropertyValue("--down").trim() || DN;
    if(still) frame(performance.now(), true);
  }
  window.addEventListener("resize", size);
  size();
  if(!still) requestAnimationFrame(frame);
  else setInterval(() => frame(performance.now(), true), 30000);   // candles still update
})();

// ------------------------------------------------------------ card tilt
// Cards lean toward the cursor, a few degrees at most. Delegated from the
// document because the tiles are rebuilt on every refresh. Not on touch, not
// for reduced motion, and never on the chart, the map or anything with inputs.
(function(){
  if(window.matchMedia && (matchMedia("(prefers-reduced-motion: reduce)").matches
     || matchMedia("(hover: none)").matches)) return;
  const SEL = ".mkt, .top3 .card, .tiles .tile";
  let cur = null;
  function reset(el){ if(el) el.style.transform = ""; }
  document.addEventListener("mousemove", e => {
    const el = e.target.closest ? e.target.closest(SEL) : null;
    if(el !== cur){ reset(cur); cur = el; }
    if(!el) return;
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - .5, py = (e.clientY - r.top) / r.height - .5;
    el.style.transform = `perspective(900px) rotateX(${(-py * 6).toFixed(2)}deg) `
                       + `rotateY(${(px * 8).toFixed(2)}deg) translateZ(4px)`;
  }, {passive: true});
  document.addEventListener("mouseleave", () => { reset(cur); cur = null; });
})();
</script>
</body></html>
"""



# ---------------------------------------------------------------------------
# LOGIN / SIGNUP PAGES
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Serve the signal tool as a website.")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
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
        # A platform that assigns the port tells you so through $PORT, and it
        # wins over WEB_PUBLIC_URL: the public address is https on 443 while
        # the process is handed something like 10000 to bind to, so reading the
        # port off the public URL would bind to the wrong one.
        env_port = (os.environ.get("PORT") or os.environ.get("WEB_PORT") or "").strip()
        if env_port.isdigit():
            port = int(env_port)
        else:
            try:
                parsed = urllib.parse.urlparse(config.WEB_PUBLIC_URL or "")
                # Only when the public URL names a port explicitly. Behind a
                # tunnel or a proxy the public address is https on 443 while
                # the process binds something else entirely, and inferring 443
                # from the scheme would bind the wrong port — or, as happened
                # here, fall through to 8080 while the tunnel forwarded 5055.
                # Set WEB_PORT for that case.
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

    # Kept warm in the background so no page load ever waits on Yahoo.
    market_ticker.start_background(_stop_ticker)

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
        _stop_ticker.set()
        feeds.stop_all()
        srv.server_close()


if __name__ == "__main__":
    main()
