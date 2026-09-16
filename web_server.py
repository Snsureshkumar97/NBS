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
    server_version = "TradePicker"

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
    # Everything under /api/ is gated, with the public exceptions named. This
    # used to list API routes one by one, and every endpoint added after the
    # list was written - analytics, greeks, oiclock, screen, spikes, chain,
    # news, candles - answered anyone on the internet without a login, some of
    # them spending the owner's Zerodha session to do it. A new route is now
    # private unless someone deliberately makes it public.
    GATED = ("/app", "/api/", "/chart/", "/connect")
    # World index levels from a free feed: no user data, no broker call.
    PUBLIC_API = ("/api/markets",)

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
            if path == "/api/admin":
                return self._do_admin_api(form)
            if path == "/api/journal":
                return self._do_journal(form)
            if path == "/api/customscreen":
                return self._do_customscreen(form)
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
        if (not user and path not in self.PUBLIC_API
                and any(path == g or path.startswith(g) for g in self.GATED)):
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
                # The journal replaced this page: the same comparison with the
                # backtest lives inside it, beside your own trades.
                return self._redirect("/app#journal")
            if path == "/api/state":
                return self._api_state(user)
            if path == "/api/tick":
                return self._api_tick(user)
            if path == "/api/chain":
                return self._api_chain(user, qs)
            if path == "/api/news":
                return self._api_news(user)
            if path == "/api/screen":
                return self._api_screen(user, qs)
            if path == "/api/spikes":
                return self._api_spikes(user, qs)
            if path == "/api/analytics":
                return self._api_analytics(user, qs)
            if path == "/api/journal":
                return self._api_journal(user, qs)
            if path == "/api/customscreen":
                return self._api_customscreen(user)
            if path == "/api/greeks":
                return self._api_greeks(user, qs)
            if path == "/api/admin/users":
                return self._api_admin_users(user)
            if path == "/api/oiclock":
                return self._api_oiclock(user, qs)
            if path == "/api/markets":
                # Public on purpose: it is world index levels off a free feed,
                # not anybody's data, and the strip is drawn before login on
                # the marketing pages too.
                # The strip is Indian indices and sectors. On the crypto
                # screen that is not context, it is a different market's
                # numbers, so that screen asks for the world block instead -
                # Nikkei through BTC/USD, which is real context for a coin.
                grp = (qs.get("group") or [""])[0]
                _rows = market_ticker.rows()
                if grp:
                    _rows = [r for r in _rows if r.get("group") == grp]
                return self._send(json.dumps({"rows": _rows}),
                                  "application/json")
            if path.startswith("/chart/") and path.endswith(".svg"):
                return self._chart(user, path[len("/chart/"):-len(".svg")], qs)
            if path.startswith("/api/candles/"):
                return self._candles(user, path[len("/api/candles/"):], qs)
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
            "posgreeks": self._position_greeks(user, market,
                                               snap.get("tickets") or {}),
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
            # Who this is and until when - the renewal date a user needs to see,
            # and whether the admin tab should exist at all on their screen.
            "account": accounts.account_summary(user) if user else None,
            "feed": snap["feed"],
            "stale": snap["feed"] in ("missing", "stale", "expired"),
            "error": snap["error"],
            # Why prices are not streaming, when they are not. The analysis
            # keeps refreshing over REST regardless, so without this a dead
            # tick socket looks like a working page.
            "stream_error": snap.get("stream_error"),
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

    def _api_news(self, user):
        """Headlines for this market, fetched and de-duplicated by the server.

        Nothing about the user goes out with the request, and the browser
        never touches the publishers - see news.py.
        """
        import news
        try:
            items, note = news.headlines(self._current_market() or "nse_index")
        except Exception:
            items, note = [], "Headlines are unavailable right now."
        return self._send(json.dumps({"items": items, "note": note}),
                          "application/json")

    def _api_screen(self, user, qs):
        """The constituent screeners: breakouts, volume and the day's move.

        Covers the index constituents this tool knows - 56 symbols across
        Nifty, Bank Nifty and Sensex, not the whole exchange - and says so in
        the payload, because a screener that implies a wider net than it casts
        is worse than no screener. Daily candles are cached for fifteen
        minutes: a break of a fifty-session high does not change by the minute.
        """
        market = self._current_market()
        feed = feeds.for_user(user, market)
        # A market with one instrument has no constituents to screen. It does
        # have a live option chain, and where open interest sits across that
        # chain is the honest answer to "what is the market doing" here.
        if market != "nse_index":
            return self._send(json.dumps(dict(feed.crypto_pulse(
                (config.instruments_in(market) or ["BTC"])[0]),
                scope="the live option chain", kind="crypto")),
                "application/json")
        try:
            hist = feed.constituent_history("day", days=120)
        except Exception as exc:
            return self._send(json.dumps({"rows": [], "covered": 0,
                                          "error": str(exc)[:200]}),
                              "application/json")
        if not hist:
            return self._send(json.dumps({"rows": [], "covered": 0,
                                          "note": "Constituent history is not "
                                                  "loaded yet."}),
                              "application/json")
        rows, short = [], 0
        for sym, df in hist.items():
            d = df.sort_values("ts")
            if len(d) < 51:
                short += 1
                continue
            close = float(d["close"].iloc[-1])
            prev = float(d["close"].iloc[-2])
            vol = float(d["volume"].iloc[-1])
            av20 = float(d["volume"].iloc[-21:-1].mean() or 0)
            hi10 = float(d["high"].iloc[-11:-1].max())
            lo10 = float(d["low"].iloc[-11:-1].min())
            hi50 = float(d["high"].iloc[-51:-1].max())
            lo50 = float(d["low"].iloc[-51:-1].min())
            rows.append({
                "sym": sym, "close": round(close, 2),
                "pct": round((close / prev - 1) * 100, 2) if prev else None,
                "bo10": "high" if close > hi10 else ("low" if close < lo10 else ""),
                "bo50": "high" if close > hi50 else ("low" if close < lo50 else ""),
                "vx": round(vol / av20, 2) if av20 else None,
                "hi10": round(hi10, 2), "lo10": round(lo10, 2),
                "hi50": round(hi50, 2), "lo50": round(lo50, 2)})
        rows.sort(key=lambda r: -(r["pct"] or 0))
        return self._send(json.dumps({
            "rows": rows, "covered": len(rows), "universe": len(hist),
            "short_history": short,
            "scope": "index constituents (Nifty, Bank Nifty, Sensex)",
            "missing": getattr(feed, "_hist_missing", 0)}), "application/json")

    def _position_greeks(self, user, market, tickets):
        """What an open position is actually exposed to, in rupees.

        A ticket shows entry, price and P&L. It does not show that the position
        bleeds a known amount every day it is held, or what a hundred-point
        move is worth - and those are the two numbers that decide whether
        holding overnight is sensible. Priced with the same model as the chain,
        scaled by the real lot size, so the answer is money rather than a
        textbook sensitivity.
        """
        if market != "nse_index":
            return {}
        import datetime as _dt
        import greeks as gk
        ist = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
        now = _dt.datetime.now(ist)
        feed = feeds.for_user(user, market)
        import btst
        study = _btst_study()
        out = {}
        for name, blob in (tickets or {}).items():
            t = (blob or {}).get("ticket") if isinstance(blob, dict) else None
            if not t or not t.get("open"):
                continue
            strike, kind = t.get("strike"), t.get("option_type")
            prem, expiry = t.get("now"), str(t.get("expiry") or "")
            lot = t.get("lot_size") or 0
            lots = t.get("lots") or 1
            rec = ((feed.snapshot().get("indices") or {}).get(name) or {})
            spot = rec.get("spot") or rec.get("ltp_spot")
            if not (strike and kind and prem and spot and expiry):
                continue
            try:
                y, mo, dd = (int(x) for x in expiry.split("-")[:3])
            except Exception:
                continue
            close = _dt.datetime(y, mo, dd, 15, 30, tzinfo=ist)
            t_yr = gk.years_to_expiry((close - now).total_seconds() / 60.0)
            iv = gk.implied_vol(prem, spot, strike, t_yr, kind)
            if not iv:
                out[name] = {"note": "No volatility fits this price, so the "
                                     "sensitivities are not shown.",
                             "overnight": btst.overnight(now, name, kind, expiry, None, study)}
                continue
            g_ = gk.greeks(spot, strike, t_yr, iv, kind)
            qty = (lot or 0) * lots
            out[name] = {
                "iv": round(iv * 100, 2),
                "delta": round(g_["delta"], 4),
                "gamma": round(g_["gamma"], 7),
                "theta_day": round(g_["theta"] * qty, 2) if qty else None,
                "per_100": round(g_["delta"] * 100 * qty, 2) if qty else None,
                "vega_pt": round(g_["vega"] * qty, 2) if qty else None,
                "whatif": gk.scenarios(spot, strike, t_yr, iv, kind, prem, qty),
                "days": round((close - now).total_seconds() / 86400.0, 2),
                "qty": qty,
                # What carrying it past the close would cost - shown on the
                # ticket in the last hour of the session.
                "overnight": btst.overnight(now, name, kind, expiry,
                                            g_["theta"] * qty if qty else None, study)}
        return out

    def _same_origin(self):
        """True when a state-changing request came from this site.

        The session cookie is SameSite=Lax, which already stops another site's
        form posting here with it. This is a second lock on the same door. A
        request with no Origin at all is a non-browser client presenting a
        real admin cookie, which is the credential, so it is let through.
        """
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not origin:
            return True
        net = urllib.parse.urlparse(origin).netloc.lower()
        allowed = {(self.headers.get("Host") or "").lower(),
                   (self.headers.get("X-Forwarded-Host") or "").lower()}
        public = urllib.parse.urlparse(config.WEB_PUBLIC_URL or "").netloc.lower()
        if public:
            allowed.add(public)
        allowed.discard("")
        return net in allowed

    def _api_admin_users(self, user):
        """Every account, for the admin tab. Admin only - checked HERE, not just
        by hiding the tab: a hidden button is not a permission."""
        if not accounts.is_admin(user):
            return self._send(json.dumps({"error": "admin only"}),
                              "application/json", code=403)
        rows = accounts.admin_view()
        try:
            import subprocess as _sp
            commit = _sp.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=os.path.dirname(os.path.abspath(__file__)),
                             capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            commit = ""
        server = {"commit": commit, "started": _state.get("started"),
                  "mode": _state.get("mode"), "feeds": len(feeds.active()),
                  "accounts": len(rows),
                  "sessions": sum(r["sessions"] for r in rows),
                  "signup": bool(config.WEB_ALLOW_SIGNUP),
                  # The tick engine, and every feed whose socket is not live.
                  # Neither shows anywhere else, and a dead engine is fixed
                  # only by a restart - which is the operator's call to make.
                  "tick_engine": feeds.tick_engine_problem(),
                  "streams": feeds.stream_problems(),
                  # Years the NSE holiday calendar covers. A year missing from
                  # it makes every holiday look like a trading day.
                  "holiday_years": sorted(__import__("main").NSE_HOLIDAYS_BY_YEAR)}
        return self._send(json.dumps({"users": rows, "server": server, "me": user,
                                      "today": accounts.today_ist().isoformat()}),
                          "application/json")

    def _do_admin_api(self, form):
        """Account management from inside the tool, for the admin only.

        Three checks, and none of them is the tab being hidden: a signed-in
        session, that session belonging to an admin, and the request coming
        from this site. Admin accounts - including the one making the request -
        cannot be expired, disabled, deleted, signed out or have their password
        reset from here, so the admin cannot lock themselves out by mistake.
        The key-based /admin page remains as the way back in regardless.
        """
        def reply(ok, message, code=200):
            return self._send(json.dumps({"ok": bool(ok), "message": message}),
                              "application/json", code=code)
        user = self._current_user()
        if not user:
            return reply(False, "Sign in first.", 401)
        if not accounts.is_admin(user):
            return reply(False, "Admin only.", 403)
        if not self._same_origin():
            return reply(False, "Refused: that request did not come from this site.", 403)
        action = (form.get("action") or "").strip()
        email = (form.get("email") or "").strip().lower()
        if action == "create":
            ok, msg = accounts.create_user(email, form.get("password") or "",
                                           expires=(form.get("expires") or "").strip() or None)
            return reply(ok, msg)
        target = accounts.get_user(email)
        if not target:
            return reply(False, "No such account.")
        if (email == user or target.get("role") == "admin") and action in (
                "expiry", "disable", "delete", "password", "signout", "unlink"):
            return reply(False, "That is an admin account. It cannot be changed from "
                                "here, so an admin cannot be locked out by accident.")
        if action == "expiry":
            ok, msg = accounts.set_expiry(email, (form.get("expires") or "").strip() or None)
        elif action == "disable":
            ok, msg = accounts.set_disabled(email, True)
        elif action == "enable":
            ok, msg = accounts.set_disabled(email, False)
        elif action == "password":
            ok, msg = accounts.set_password(email, form.get("password") or "")
        elif action == "signout":
            ok, msg = accounts.revoke_sessions(email)
        elif action == "unlink":
            user_kite.disconnect(email)
            ok, msg = True, f"Disconnected {email} from Zerodha."
        elif action == "delete":
            ok, msg = accounts.delete_user(email)
        else:
            return reply(False, "Unknown action.", 400)
        return reply(ok, msg)

    def _api_greeks(self, user, qs):
        """The chain restated as volatility and sensitivities.

        A premium on its own says little: 120 rupees two days from expiry and
        120 a fortnight out are not the same bet. Implied volatility is that
        price expressed as the movement it implies, which compares across
        strikes and days; the greeks say how it will change.

        Validated against the live chain before it was wired here: ATM implied
        volatility solved to 13.9 / 13.7 / 14.5% against India VIX at 12.3,
        with the downside puts bid over the upside calls on Nifty and Sensex -
        the put skew an equity index always carries.

        Crypto is refused rather than guessed at. Deribit's BTC options are
        inverse and quoted in the coin, so rupee-style Black-Scholes would
        produce confident nonsense.
        """
        import datetime as _dt
        import greeks as gk

        market = self._current_market()
        if market != "nse_index":
            return self._send(json.dumps({
                "note": "These are European cash-settled index options priced "
                        "with Black-Scholes. Deribit's contracts are inverse "
                        "and quoted in the coin, so the same model does not "
                        "apply and is not pretended to."}), "application/json")

        names = config.instruments_in(market)
        name = (qs.get("index") or [""])[0].upper()
        if name not in names:
            name = (self._current_index(user) if hasattr(self, "_current_index")
                    else None) or (names[0] if names else "")
        feed = feeds.for_user(user, market)
        chain = feed.chain(name) if name else None
        if not chain or not chain.get("strikes"):
            return self._send(json.dumps({"index": name, "rows": [],
                                          "note": "No chain from the broker "
                                                  "right now."}),
                              "application/json")

        spot = chain.get("spot")
        expiry = str(chain.get("expiry") or "")
        try:
            y, mo, dd = (int(x) for x in expiry.split("-"))
        except Exception:
            return self._send(json.dumps({"index": name, "rows": [],
                                          "note": "No expiry on this chain."}),
                              "application/json")
        ist = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
        # To the close on expiry day, not to midnight: on the day itself that
        # difference is most of what is left of the option's life.
        close = _dt.datetime(y, mo, dd, 15, 30, tzinfo=ist)
        minutes = (close - _dt.datetime.now(ist)).total_seconds() / 60.0
        t = gk.years_to_expiry(minutes)

        strikes = sorted(chain["strikes"], key=lambda s_: s_["strike"])
        atm = min((s_["strike"] for s_ in strikes),
                  key=lambda k: abs(k - (spot or 0))) if spot else None
        meta = config.INSTRUMENTS.get(name) or {}
        step = meta.get("strike_step") or 50
        if atm is not None:
            strikes = [s_ for s_ in strikes if abs(s_["strike"] - atm) <= 12 * step]

        rows, solved, quotes = [], 0, 0
        for s_ in strikes:
            k = s_["strike"]
            row = {"strike": k, "atm": (k == atm)}
            for side, tag in (("call", "ce"), ("put", "pe")):
                px = s_.get(f"{side}_ltp")
                oi = s_.get(f"{side}_oi")
                kind = "CE" if side == "call" else "PE"
                quotes += 1
                iv = gk.implied_vol(px, spot, k, t, kind)
                if iv:
                    solved += 1
                    g_ = gk.greeks(spot, k, t, iv, kind)
                    row[tag] = {"ltp": px, "oi": oi,
                                "iv": round(iv * 100, 2),
                                "delta": round(g_["delta"], 4),
                                "gamma": round(g_["gamma"], 7),
                                "theta": round(g_["theta"], 2),
                                "vega": round(g_["vega"], 2),
                                "gxoi": round(g_["gamma"] * (oi or 0), 1)}
                else:
                    row[tag] = {"ltp": px, "oi": oi, "iv": None}
            rows.append(row)

        def _avg(vals):
            vals = [v for v in vals if v is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        atm_iv = _avg([ (r.get("ce") or {}).get("iv") for r in rows if r["atm"] ]
                    + [ (r.get("pe") or {}).get("iv") for r in rows if r["atm"] ])
        put_wing = _avg([(r.get("pe") or {}).get("iv") for r in rows
                         if spot and r["strike"] < spot * 0.99])
        call_wing = _avg([(r.get("ce") or {}).get("iv") for r in rows
                          if spot and r["strike"] > spot * 1.01])
        # OI-weighted gamma by strike. Called a concentration, not dealer
        # positioning: who is short which side is not knowable from here.
        conc = []
        for r in rows:
            v = ((r.get("ce") or {}).get("gxoi") or 0) - ((r.get("pe") or {}).get("gxoi") or 0)
            if v:
                conc.append({"strike": r["strike"], "v": round(v, 1)})
        conc.sort(key=lambda c: -abs(c["v"]))

        return self._send(json.dumps({
            "index": name, "spot": spot, "expiry": expiry, "atm": atm,
            "minutes": round(minutes), "days": round(minutes / 1440, 2),
            "t": round(t, 6), "rate": gk.RATE * 100,
            "atm_iv": atm_iv, "put_wing": put_wing, "call_wing": call_wing,
            "skew": (round(put_wing - call_wing, 2)
                     if (put_wing is not None and call_wing is not None) else None),
            "solved": solved, "quotes": quotes,
            "concentration": conc[:8], "rows": rows,
            "note": "Implied volatility is solved from the last traded price: "
                    "this feed carries no bid or ask, so an illiquid strike can "
                    "hold a stale reading. Time runs to the 15:30 close on "
                    "expiry day."}), "application/json")

    def _api_customscreen(self, user):
        """What the screen builder can use: presets, your saved screens, and the
        indicators, timeframes and comparisons it accepts."""
        import screener
        if self._current_market() != "nse_index":
            return self._send(json.dumps({
                "note": "The screener runs on the Indian index member stocks - Nifty, Bank "
                        "Nifty and Sensex. A market of one instrument has nothing to screen."}),
                "application/json")
        try:
            saved = screener.saved(user)
        except Exception:
            saved = []
        return self._send(json.dumps({
            "presets": screener.PRESETS, "saved": saved,
            "indicators": [{"name": k, "label": v[0],
                            "params": [{"key": p[0], "default": p[1], "lo": p[2], "hi": p[3]} for p in v[1]]}
                           for k, v in screener.INDICATORS.items()],
            "timeframes": list(screener.TIMEFRAMES), "operators": list(screener.OPERATORS),
            "max_conditions": screener.MAX_CONDITIONS, "max_offset": screener.MAX_OFFSET}),
            "application/json")

    def _do_customscreen(self, form):
        """Run a screen over the index member stocks, or save or delete one of
        your own. The screen is data checked against fixed lists - never code."""
        def reply(ok, message, code=200, **extra):
            return self._send(json.dumps(dict({"ok": bool(ok), "message": message}, **extra)),
                              "application/json", code=code)
        user = self._current_user()
        if not user:
            return reply(False, "Sign in first.", 401)
        if not self._same_origin():
            return reply(False, "Refused: that request did not come from this site.", 403)
        if self._current_market() != "nse_index":
            return reply(False, "The screener runs on the Indian index member stocks.", 400)
        import screener
        action = (form.get("action") or "").strip()
        strategy = None
        if action in ("run", "save"):
            try:
                strategy = json.loads(form.get("strategy") or "")
            except ValueError:
                return reply(False, "That screen could not be read.", 400)
        try:
            if action == "run":
                hist = feeds.for_user(user, "nse_index").constituent_history("day", days=400)
                out = screener.run(hist, strategy, screener.members())
                return reply(True, f"{len(out['matches'])} of {out['checked']} stocks match.",
                             result=out, at=now_ist().strftime("%H:%M"))
            if action == "save":
                s = screener.save(user, strategy)
                return reply(True, f"Saved \u201c{s['name']}\u201d.", saved=screener.saved(user), name=s["name"])
            if action == "delete":
                screener.delete(user, form.get("name") or "")
                return reply(True, "Deleted.", saved=screener.saved(user))
        except ValueError as exc:
            return reply(False, str(exc), 400)
        except RuntimeError as exc:
            text = str(exc)
            # Zerodha clears every token early each morning. Its own wording for
            # that ("TokenException: Incorrect api_key or access_token") reads
            # like a fault in the tool; the fix is a reconnect, so say that.
            if ("TokenException" in text or "access_token" in text
                    or "no Zerodha token" in text):
                return reply(False, "Your Zerodha connection has expired for the day - Zerodha "
                                    "clears it every morning. Reconnect on the Zerodha page, then "
                                    "run the screen again.", 503, reconnect=True)
            return reply(False, f"The stock data could not be read: {text[:160]}", 503)
        except Exception:
            return reply(False, "The screen could not be run just now.", 500)
        return reply(False, "Unknown action.", 400)

    def _api_journal(self, user, qs):
        """The trading journal: your own trades and the tool's tickets by day,
        the statistics, the day notes, and the comparison with the backtest
        that the Review page used to make."""
        import journal
        import review_page
        market = self._current_market()
        if not market:
            return self._send(json.dumps({"error": "Pick a market first."}),
                              "application/json", code=400)
        source = (qs.get("source") or ["all"])[0]
        if source not in ("all", "mine", "tool"):
            source = "all"
        try:
            lines = journal.entries(user, market, source)
            summary = journal.summarize(lines)
            notes = journal.load(user, market)["notes"]
        except Exception:
            return self._send(json.dumps({"error": "The journal could not be read just now."}),
                              "application/json", code=500)
        review = None
        try:
            data = review_page.build(user, market)
            bench = None if config.MARKETS[market]["always_open"] else review_page.BENCHMARK
            kind, text = review_page._verdict(data["all"], (bench or {}).get("all"))
            review = {"all": data["all"], "bench": bench, "min_sample": review_page.MIN_SAMPLE,
                      "verdict": {"kind": kind, "text": text},
                      "groups": {title: [[str(k), v] for k, v in rows]
                                 for title, rows in data["groups"].items()}}
        except Exception:
            review = None
        insts = journal.instruments(market)
        return self._send(json.dumps({
            "market": market, "currency": config.MARKETS[market].get("currency", "INR"),
            "source": source, "entries": lines, "days": summary["days"],
            "stats": summary["stats"], "risk": summary.get("risk"), "notes": notes, "instruments": insts,
            "lot_sizes": {k: (config.INSTRUMENTS.get(k) or {}).get("lot_size") for k in insts},
            "today": journal.today_ist().isoformat(), "review": review}, default=str),
            "application/json")

    def _do_journal(self, form):
        """Add, change or delete one of your own journal trades, or save a day's
        note. Only ever your own journal: the account comes from the session."""
        def reply(ok, message, code=200, **extra):
            return self._send(json.dumps(dict({"ok": bool(ok), "message": message}, **extra)),
                              "application/json", code=code)
        user = self._current_user()
        if not user:
            return reply(False, "Sign in first.", 401)
        if not self._same_origin():
            return reply(False, "Refused: that request did not come from this site.", 403)
        market = self._current_market()
        if not market:
            return reply(False, "Pick a market first.", 400)
        import journal
        action = (form.get("action") or "").strip()
        try:
            if action == "add":
                return reply(True, "Added to your journal.", id=journal.add_trade(user, market, form))
            if action == "update":
                journal.update_trade(user, market, (form.get("id") or "").strip(), form)
                return reply(True, "Trade updated.")
            if action == "delete":
                journal.delete_trade(user, market, (form.get("id") or "").strip())
                return reply(True, "Trade deleted.")
            if action == "note":
                journal.set_note(user, market, (form.get("date") or "").strip(), form.get("text") or "")
                return reply(True, "Note saved.")
        except ValueError as exc:
            return reply(False, str(exc), 400)
        except Exception:
            return reply(False, "The journal could not be saved just now.", 500)
        return reply(False, "Unknown action.", 400)

    def _api_analytics(self, user, qs):
        """The analyst's numbers: volatility, levels, internals, strength,
        seasonality.

        Every block carries the sample it was computed from. A statistic
        without its n is a claim, not a measurement, and this file has already
        shipped one verdict drawn from an empty dataset.

        All of it is NSE-sourced - India VIX, index candles, the constituent
        list - so the crypto session gets an honest refusal rather than
        another market's numbers.
        """
        import math
        market = self._current_market()
        if market != "nse_index":
            return self._send(json.dumps({
                "market": market,
                "note": "These are Indian-market analytics - India VIX, the "
                        "index candles and the constituent list. None of it "
                        "exists for this market, so none of it is shown."}),
                "application/json")

        feed = feeds.for_user(user, market)
        out = {"market": market}
        try:
            provider, state, _ = feed._provider()
        except Exception:
            provider = None
        if provider is None:
            return self._send(json.dumps({"error": "no data provider right now"}),
                              "application/json")

        def series(key, interval, days):
            try:
                return provider.get_ohlc(key, interval=interval, lookback_days=days)
            except Exception:
                return None

        # ---------------------------------------------------------- volatility
        try:
            px = series("NIFTY", "1d", 400)
            vix = provider.candles_for_token(264969, "day", days=400)
            closes = [float(x) for x in px["Close"].tolist()]
            rets = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
            def rvol(n):
                w = rets[-n:]
                if len(w) < 5: return None
                m = sum(w) / len(w)
                sd = (sum((x - m) ** 2 for x in w) / (len(w) - 1)) ** .5
                return round(sd * (252 ** .5) * 100, 2)
            ivnow = round(float(vix["close"].iloc[-1]), 2)
            year = [float(x) for x in vix["close"].tolist()][-252:]
            pctile = round(sum(1 for v in year if v < ivnow) / len(year) * 100, 1)
            spot = closes[-1]
            moves = {}
            for label, d in (("day", 1), ("week", 5), ("month", 21)):
                em = spot * (ivnow / 100) * ((d / 252) ** .5)
                moves[label] = {"pts": round(em), "lo": round(spot - em, 2),
                                "hi": round(spot + em, 2)}
            # How often the day's move stayed inside what implied vol priced.
            # Both sides aligned on plain dates: a half-normalised join gave
            # NaN and read as 0%, which is impossible and was my error.
            import pandas as _pd
            pxd = px.copy()
            pxd.index = _pd.to_datetime(px.index).tz_localize(None).normalize()
            vx = vix.copy()
            vx["d"] = _pd.to_datetime(vx["ts"]).dt.tz_localize(None).dt.normalize()
            vs = vx.set_index("d")["close"].reindex(pxd.index).ffill().shift(1)
            exp1 = pxd["Close"].shift(1) * (vs / 100) * (1 / 252) ** .5
            act = (pxd["Close"] - pxd["Close"].shift(1)).abs()
            both = _pd.concat([act, exp1], axis=1).dropna()
            both.columns = ["a", "e"]
            inside = (both["a"] <= both["e"])
            rv20 = rvol(20)
            out["vol"] = {
                "vix": ivnow, "pctile": pctile,
                "rv10": rvol(10), "rv20": rv20, "rv60": rvol(60), "rv250": rvol(250),
                "premium": round(ivnow - rv20, 2) if rv20 else None,
                "spot": round(spot, 2), "moves": moves,
                "inside": round(float(inside.mean()) * 100),
                "inside_n": int(len(inside)),
                "inside_60": round(float(inside.tail(60).mean()) * 100),
                "vix_lo": round(min(year), 2), "vix_hi": round(max(year), 2)}
        except Exception as exc:
            out["vol"] = {"error": str(exc)[:140]}

        # --------------------------------------------------------------- cone
        # Realised volatility at several horizons against its OWN year, so
        # "quiet" and "wild" are judged against this index's history rather
        # than a number someone remembers. Short windows swing; long ones
        # anchor, which is why every horizon is shown instead of one.
        try:
            import statistics as _st
            cl = [float(x) for x in px["Close"].tolist()]
            lr = [math.log(cl[i] / cl[i-1]) for i in range(1, len(cl))]
            cone = []
            for w in (5, 10, 20, 30, 60, 90):
                if len(lr) < w + 5:
                    continue
                vals = []
                for i in range(w, len(lr) + 1):
                    win = lr[i-w:i]
                    m = sum(win) / w
                    sd = (sum((x - m) ** 2 for x in win) / (w - 1)) ** .5
                    vals.append(sd * (252 ** .5) * 100)
                now = vals[-1]
                srt = sorted(vals)
                def pc(q):
                    return srt[min(len(srt) - 1, int(q * (len(srt) - 1)))]
                cone.append({
                    "window": w, "now": round(now, 2),
                    "min": round(srt[0], 2), "p25": round(pc(.25), 2),
                    "median": round(pc(.5), 2), "p75": round(pc(.75), 2),
                    "max": round(srt[-1], 2),
                    "rank": round(sum(1 for v in vals if v < now) / len(vals) * 100),
                    "n": len(vals)})
            out["cone"] = {"rows": cone, "iv": out.get("vol", {}).get("vix"),
                           "sessions": len(cl)}
        except Exception as exc:
            out["cone"] = {"error": str(exc)[:140]}

        # -------------------------------------------------------------- levels
        levels = []
        from indicators import fib_retracements
        for key in config.instruments_in(market):
            try:
                d = series(key, "1d", 60)
                if d is None or len(d) < 15: continue
                H, L, C = (float(d["High"].iloc[-1]), float(d["Low"].iloc[-1]),
                           float(d["Close"].iloc[-1]))
                pivot = (H + L + C) / 3
                trs = []
                for i in range(1, len(d)):
                    hi, lo = float(d["High"].iloc[i]), float(d["Low"].iloc[i])
                    pc = float(d["Close"].iloc[i-1])
                    trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
                atr = sum(trs[-14:]) / 14
                row = {"index": key, "close": round(C, 2),
                       "prev_high": round(H, 2), "prev_low": round(L, 2),
                       "range": round(H - L, 2),
                       "pivot": round(pivot, 2),
                       "r1": round(2*pivot - L, 2), "s1": round(2*pivot - H, 2),
                       "r2": round(pivot + (H - L), 2), "s2": round(pivot - (H - L), 2),
                       "atr": round(atr), "atr_lo": round(C - atr, 2),
                       "atr_hi": round(C + atr, 2), "session": str(d.index[-1].date()),
                       **{f"fib{k}": v for k, v in fib_retracements(H, L).items()}}
                m = series(key, "5m", 5)
                if m is not None and len(m) > 3:
                    day = m.index[-1].date()
                    sess = m[[ix.date() == day for ix in m.index]]
                    if len(sess) >= 3:
                        orb = sess.iloc[:3]
                        oh, ol = float(orb["High"].max()), float(orb["Low"].min())
                        cl = float(sess["Close"].iloc[-1])
                        row.update({"or_hi": round(oh, 2), "or_lo": round(ol, 2),
                                    "or_close": "above" if cl > oh else
                                                "below" if cl < ol else "inside"})
                levels.append(row)
            except Exception:
                continue
        out["levels"] = levels

        # ------------------------------------- internals + relative strength
        try:
            hist = feed.constituent_history("day", days=400)
            closes = {s_: [float(x) for x in d["close"].tolist()]
                      for s_, d in hist.items() if len(d) > 60}
            n = min(len(v) for v in closes.values())
            trimmed = {k: v[-n:] for k, v in closes.items()}
            def ma(v, w): return sum(v[-w:]) / w
            a20 = sum(1 for v in trimmed.values() if v[-1] > ma(v, 20))
            a50 = sum(1 for v in trimmed.values() if v[-1] > ma(v, 50))
            adv = sum(1 for v in trimmed.values() if v[-1] > v[-2])
            dec = sum(1 for v in trimmed.values() if v[-1] < v[-2])
            hi = sum(1 for v in trimmed.values() if v[-1] >= max(v))
            lo = sum(1 for v in trimmed.values() if v[-1] <= min(v))
            adline = []
            run = 0
            for i in range(max(1, n - 20), n):
                up = sum(1 for v in trimmed.values() if v[i] > v[i-1])
                dn = sum(1 for v in trimmed.values() if v[i] < v[i-1])
                run += up - dn
                adline.append(run)
            total = len(trimmed)
            out["internals"] = {
                "n": total, "lookback": n,
                "above20": round(a20 / total * 100, 1),
                "above50": round(a50 / total * 100, 1),
                "adv": adv, "dec": dec, "at_high": hi, "at_low": lo,
                "adline": adline}
            rs = sorted(((k, (v[-1] / v[-21] - 1) * 100) for k, v in trimmed.items()
                         if len(v) > 21), key=lambda kv: -kv[1])
            out["strength"] = {
                "window": 20,
                "leaders": [{"sym": k, "pct": round(v, 2)} for k, v in rs[:8]],
                "laggards": [{"sym": k, "pct": round(v, 2)} for k, v in rs[-8:]]}
        except Exception as exc:
            out["internals"] = {"error": str(exc)[:140]}

        # --------------------------------------------------------- seasonality
        try:
            d = series("NIFTY", "1d", 400)
            rows, gaps = {}, []
            prev_close = None
            for ix, r in zip(d.index, d.itertuples()):
                if prev_close:
                    rows.setdefault(ix.weekday(), []).append(
                        (float(r.Close) / prev_close - 1) * 100)
                    gaps.append((float(r.Open) / prev_close - 1) * 100)
                prev_close = float(r.Close)
            names = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}
            dow = []
            for k in sorted(rows):
                if k not in names: continue
                v = rows[k]
                m = sum(v) / len(v)
                sd = (sum((x - m) ** 2 for x in v) / max(1, len(v) - 1)) ** .5
                dow.append({"day": names[k], "avg": round(m, 3), "sd": round(sd, 2),
                            "n": len(v), "up": round(sum(1 for x in v if x > 0) / len(v) * 100)})
            out["season"] = {
                "dow": dow, "n": len(gaps),
                "gap_up": sum(1 for g in gaps if g > 0.1),
                "gap_flat": sum(1 for g in gaps if abs(g) <= 0.1),
                "gap_down": sum(1 for g in gaps if g < -0.1),
                "gap_avg": round(sum(abs(g) for g in gaps) / len(gaps), 2)}
        except Exception as exc:
            out["season"] = {"error": str(exc)[:140]}

        return self._send(json.dumps(out), "application/json")

    def _api_spikes(self, user, qs):
        """What moved hardest in the last five and ten minutes of the session.

        Volume is compared with that symbol's OWN typical five-minute volume
        that session, never with other symbols: a bank trading ten times a
        smallcap's shares is not a spike, it is a bank.

        TradeFinder calls this screen "Insider Strategy". It is nothing of the
        kind - it is short-window momentum - and naming it that here would
        imply information this tool does not have. It is called what it
        measures.

        When the market is shut this is the LAST session that traded, and the
        payload names the day rather than letting a stale screen pass for a
        live one.
        """
        market = self._current_market()
        feed = feeds.for_user(user, market)
        if market != "nse_index":
            return self._send(json.dumps(dict(feed.crypto_moves(
                (config.instruments_in(market) or ["BTC"])[0]),
                scope="the instrument itself", kind="crypto", live=True)),
                "application/json")
        try:
            hist = feed.constituent_history("5minute", days=5)
        except Exception as exc:
            return self._send(json.dumps({"rows": [], "covered": 0,
                                          "error": str(exc)[:200]}),
                              "application/json")
        if not hist:
            return self._send(json.dumps({"rows": [], "covered": 0,
                                          "note": "Intraday candles are not "
                                                  "loaded yet."}),
                              "application/json")
        rows, session = [], ""
        for sym, df in hist.items():
            d = df.sort_values("ts")
            if not len(d):
                continue
            day = str(d["ts"].iloc[-1])[:10]
            session = max(session, day)
            d = d[d["ts"].astype(str).str[:10] == day]
            if len(d) < 25:
                continue
            c, v = d["close"], d["volume"]
            av = float(v.iloc[:-2].tail(20).mean() or 0)
            if not av:
                continue
            last = float(c.iloc[-1])
            rows.append({
                "sym": sym, "close": round(last, 2),
                "m5": round((last / float(c.iloc[-2]) - 1) * 100, 2),
                "m10": round((last / float(c.iloc[-3]) - 1) * 100, 2),
                "v5": round(float(v.iloc[-1]) / av, 2),
                "v10": round(float(v.iloc[-2:].sum()) / (2 * av), 2)})
        # There is no _market_open_now() on this class - the snapshot is where
        # that fact lives. A hasattr() guard round a method that does not exist
        # would have reported None for ever while looking like a live field.
        try:
            live = bool((feed.snapshot() or {}).get("market_open"))
        except Exception:
            live = False
        return self._send(json.dumps({
            "rows": rows, "covered": len(rows), "session": session, "live": live,
            "scope": "index constituents (Nifty, Bank Nifty, Sensex)",
            "note": "Volume is against each symbol's own average five-minute "
                    "volume this session."}), "application/json")

    def _api_oiclock(self, user, qs):
        """Open interest added or closed across a window of the session.

        Read from the recorder's own files, so it can look at any minute of
        any day it has kept - which is what the live chain endpoint cannot do,
        since that one only knows what it has seen since it started.

        The figure is the change WITHIN the window asked for. It is not "OI
        added today": OI is a level that falls as well as rises, so a morning
        build and an afternoon unwind are both real and do not cancel into
        the day's net.
        """
        # The recorder keeps Nifty, Bank Nifty and Sensex. Asked from any
        # other market this defaulted to NIFTY and served Indian open interest
        # to a crypto session - the same leak fixed elsewhere on 12 Sep.
        if self._current_market() != "nse_index":
            return self._send(json.dumps({
                "rows": [], "days": [],
                "note": "The option recorder keeps Nifty, Bank Nifty and "
                        "Sensex. There is no recorded open interest for this "
                        "market, so there is nothing to show."}),
                "application/json")
        import glob as _glob, csv as _csv, gzip as _gzip, os as _os
        d = _os.path.join(_os.path.expanduser("~"), "trading-tool-logs",
                          "option_history")
        files = sorted(_glob.glob(_os.path.join(d, "*.csv.gz")))
        if not files:
            return self._send(json.dumps({"rows": [], "days": [],
                                          "note": "The option recorder has not "
                                                  "written a day yet."}),
                              "application/json")
        days = [_os.path.basename(f)[:10] for f in files]
        day = (qs.get("day") or [days[-1]])[0]
        if day not in days:
            day = days[-1]
        index = (qs.get("index") or ["NIFTY"])[0].upper()
        frm = (qs.get("from") or ["09:15"])[0]
        to = (qs.get("to") or ["15:39"])[0]
        path = _os.path.join(d, f"{day}.csv.gz")

        want_exp = (qs.get("expiry") or [""])[0]
        # Which expiries this day holds, before anything is summed. Two
        # expiries share every strike, so a ladder that does not pin one
        # counts both contracts under the same key and the total is nonsense.
        expiries = set()
        try:
            with _gzip.open(path, "rt", newline="") as fh:
                for r in _csv.DictReader(fh):
                    if r.get("index") == index and r.get("kind") == "OPT" and r.get("expiry"):
                        expiries.add(r["expiry"])
        except OSError as exc:
            return self._send(json.dumps({"rows": [], "days": days,
                                          "error": str(exc)[:160]}),
                              "application/json")
        exp_sorted = sorted(expiries)
        use_exp = want_exp if want_exp in expiries else (exp_sorted[0] if exp_sorted else "")

        first, last, spot = {}, {}, None
        try:
            with _gzip.open(path, "rt", newline="") as fh:
                for r in _csv.DictReader(fh):
                    if r.get("index") != index:
                        continue
                    hm = (r.get("ts") or "")[11:16]
                    if r.get("kind") == "IDX" and frm <= hm <= to:
                        try: spot = float(r["close"])
                        except (TypeError, ValueError): pass
                        continue
                    if r.get("kind") != "OPT" or (r.get("expiry") or "") != use_exp:
                        continue
                    if not (frm <= hm <= to):
                        continue
                    try:
                        oi = float(r["oi"]); strike = float(r["strike"])
                    except (TypeError, ValueError):
                        continue
                    k = (strike, r.get("opt"))
                    if k not in first:
                        first[k] = oi
                    last[k] = oi
        except OSError as exc:
            return self._send(json.dumps({"rows": [], "days": days,
                                          "error": str(exc)[:160]}),
                              "application/json")

        exp_list = exp_sorted
        by = {}
        for (strike, opt), f0 in first.items():
            by.setdefault(strike, {})[opt] = round(last[(strike, opt)] - f0)
        rows = [{"strike": k, "ce": v.get("CE"), "pe": v.get("PE")}
                for k, v in sorted(by.items())]
        if not rows:
            return self._send(json.dumps({
                "rows": [], "day": day, "days": days, "index": index,
                "expiry": use_exp, "expiries": exp_list, "from": frm, "to": to,
                "note": f"The option recorder has nothing for {index} on {day}. "
                        f"It keeps Nifty, Bank Nifty and Sensex; this screen "
                        f"has no history for any other market."}),
                "application/json")
        ce = sum(r["ce"] or 0 for r in rows)
        pe = sum(r["pe"] or 0 for r in rows)
        top_ce = max(rows, key=lambda r: r["ce"] or 0, default=None)
        top_pe = max(rows, key=lambda r: r["pe"] or 0, default=None)
        return self._send(json.dumps({
            "rows": rows, "day": day, "days": days, "index": index,
            "expiry": use_exp, "expiries": exp_list,
            "from": frm, "to": to, "spot": spot,
            "net_ce": ce, "net_pe": pe,
            "resistance": top_ce["strike"] if top_ce and (top_ce["ce"] or 0) > 0 else None,
            "support": top_pe["strike"] if top_pe and (top_pe["pe"] or 0) > 0 else None,
            "reading": ("puts written, support building" if pe > ce
                        else "calls written, resistance building"),
            "window_note": "Change within this window, not OI added today."},
            default=float), "application/json")

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

        # Where open interest stood when the tool first saw this chain today.
        # OI is a LEVEL - contracts outstanding right now - and it falls as
        # positions are closed as readily as it rises: measured on the
        # recorder's files, 176 of 384 minutes moved down. So the total on its
        # own says little intraday; what has been added or closed since the
        # session started is the number that means something. Kept in memory:
        # it is a fact about today, and a restart honestly loses it rather
        # than inventing a baseline.
        import datetime as _dt
        day = _dt.datetime.now().strftime("%Y-%m-%d")
        bkey = (market, name, str(chain.get("expiry")), day)
        base = _OI_BASE.get(bkey)
        if base is None:
            base = _OI_BASE[bkey] = {s["strike"]: (s.get("call_oi"), s.get("put_oi"))
                                     for s in chain["strikes"]}
            _OI_BASE_AT[bkey] = time.time()
            for k in [k for k in _OI_BASE if k[3] != day]:
                _OI_BASE.pop(k, None); _OI_BASE_AT.pop(k, None)

        # Strikes on the live feed with a recent tick: their price, book and
        # open interest replace the snapshot's, which is only as fresh as the
        # last analysis pass. PCR, max pain and the walls stay the snapshot's.
        live = feed.chain_live(name) if hasattr(feed, "chain_live") else {}

        def side(s, kind):
            ltp, bid, ask = s.get(f"{kind}_ltp"), s.get(f"{kind}_bid"), s.get(f"{kind}_ask")
            now = s.get(f"{kind}_oi")
            b = live.get((float(s["strike"]), "CE" if kind == "call" else "PE"))
            if b:
                if b.get("ltp") is not None:
                    ltp = b["ltp"]
                if b.get("bid") is not None or b.get("ask") is not None:
                    bid, ask = b.get("bid"), b.get("ask")
                if b.get("oi") is not None:
                    now = b["oi"]
            pct = None
            if bid and ask and ask >= bid:
                pct = round((ask - bid) / ((ask + bid) / 2) * 100, 2)
            was = (base.get(s["strike"]) or (None, None))[0 if kind == "call" else 1]
            chg = (now - was) if (was is not None and now is not None) else None
            return {"ltp": ltp, "bid": bid, "ask": ask,
                    "oi": now, "oi_chg": chg, "spread": pct, "live": bool(b)}

        rows = [{"strike": s["strike"], "ce": side(s, "call"), "pe": side(s, "put")}
                for s in strikes]
        rec = ((feed.snapshot().get("indices") or {}).get(name) or {})
        return self._send(json.dumps({
            "index": name, "expiry": chain.get("expiry"), "spot": spot, "atm": atm,
            "pcr": chain.get("pcr"), "max_pain": chain.get("max_pain"),
            "call_wall": chain.get("top_call_oi_strike"),
            "put_wall": chain.get("top_put_oi_strike"),
            "suggested": {"strike": rec.get("strike"), "type": rec.get("option_type")},
            "since": time.strftime("%H:%M", time.localtime(_OI_BASE_AT.get(bkey, time.time()))),
            "currency": "USD" if market == "crypto" else "INR",
            "live": sum(1 for r in rows for k in ("ce", "pe") if r[k].get("live")),
            "live_at": (_dt.datetime.fromtimestamp(
                            max(b.get("at", 0) for b in live.values()),
                            _dt.timezone(_dt.timedelta(hours=5, minutes=30))).strftime("%H:%M:%S")
                        if live else None),
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
        elif action == "skip_cooldown":
            # Lifts only the clock on one index, once. The ticket itself is
            # still issued by the rules on the next reading, or not at all.
            ok, msg = book.skip_cooldown(form.get("index") or "")
            return self._send(json.dumps({"ok": ok, "message": msg,
                                          "session": book.session()}),
                              "application/json")
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

    _TIMEFRAMES = ("5m", "15m", "1d")

    def _candles(self, user, key, qs=None):
        """The bars themselves, as JSON, for the interactive chart.

        The SVG endpoint stays: it is what a browser with no JavaScript, and
        the desktop app's own renderer, both use. This one exists because a
        picture cannot be hovered — a crosshair that reads out the bar under
        the pointer has to have the bars in the browser.
        """
        import indicators as ind

        feed = feeds.for_user(user, self._current_market())
        df, rec = feed.candles(key)
        # The levels and the reasoning always come from the 15-minute series
        # the signal is computed on; only the bars change with the timeframe,
        # and a timeframe that will not load falls back rather than emptying
        # the chart.
        tf = ((qs or {}).get("tf") or ["15m"])[0]
        if tf not in self._TIMEFRAMES:
            tf = "15m"
        if tf != "15m":
            try:
                alt = feed.ohlc(key, tf)
            except Exception:
                alt = None
            if alt is not None and len(alt) > 2:
                df = alt
            else:
                tf = "15m"
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
            "interval": tf,
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
# Open interest as the tool first saw it today, per market/index/expiry, so the
# chain can show what has been added since rather than only the running total.
_OI_BASE = {}
_OI_BASE_AT = {}

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<meta name="color-scheme" content="dark">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<title>TradePicker — Nifty · Bank Nifty · Sensex · Bitcoin</title>
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
.hd{max-width:none;margin:0;padding:11px 18px;display:flex;
  align-items:center;gap:10px;justify-content:space-between;flex-wrap:wrap;
  overflow:hidden}
@media(max-width:1100px){.status .st-clock{display:none}}
@media(max-width:820px){.status .feedtag{display:none}}
.hd .row{flex:0 0 auto;justify-content:flex-end}
.hd .status{flex:0 0 auto}
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
.swatch{display:inline-block;width:7px;height:7px;border-radius:2px;flex:none}

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
/* Not .rr - that name already styles the rows of another table as a
   five-column grid, and sharing it turned this panel into one. */
.overnight{margin-top:12px;border:1px solid rgba(242,163,61,.42);background:rgba(242,163,61,.08);
  border-radius:12px;padding:10px 13px;font-size:12.5px;line-height:1.6;color:var(--ink-2)}
.overnight b{color:var(--ink)}
.scbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px}
.scbar select,.scbar input,.sccond select,.sccond input{background:var(--raised);border:1px solid var(--bd);
  color:var(--ink);border-radius:8px;padding:6px 8px;font:inherit;font-size:12.5px;color-scheme:dark;min-width:0}
.scbar input{flex:1 1 200px}
.scbar select{max-width:100%}
.sccond{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:9px 0;border-top:1px solid var(--bd-soft)}
.sccond input.num{width:66px}
.sccond .sep{font-size:11.5px;color:var(--ink-3)}
.sccond select.op{font-weight:700}
.sccond .scx{margin-left:auto}
#scres td:nth-child(-n+3),#scres th:nth-child(-n+3){text-align:left}
#scres td.scv{font-size:11.5px;color:var(--ink-2)}
#scres td.scv b{color:var(--up)}
.jbar{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;margin:0 0 12px}
.jsrc,.jscope{display:flex;flex-wrap:wrap;gap:6px}
.jscope{margin:2px 0 10px}
.jform select{background:var(--raised);border:1px solid var(--bd);color:var(--ink);border-radius:8px;
  padding:7px 8px;font:inherit;font-size:13px;color-scheme:dark}
.jform input[type=time]{color-scheme:dark}
.jheatwrap{overflow-x:auto;-webkit-overflow-scrolling:touch;padding:2px 0 6px}
.jheat{display:inline-flex;flex-direction:column;gap:4px;min-width:max-content}
.jhmonths{display:flex;gap:3px;margin-left:34px;height:14px;font-size:10.5px;color:var(--ink-3)}
.jhmonths span{flex:none;white-space:nowrap;overflow:hidden}
.jhgrid{display:flex;gap:3px}
.jhdays{display:flex;flex-direction:column;gap:3px;width:31px;flex:none;font-size:10px;color:var(--ink-3)}
.jhdays span{height:13px;line-height:13px}
.jhcol{display:flex;flex-direction:column;gap:3px}
.jhcol i{display:block;width:13px;height:13px;border-radius:3px;cursor:pointer}
.jhcol i.fut{background:transparent!important;cursor:default}
.jhcol i.sel{outline:2px solid var(--accent);outline-offset:1px}
.jlegend{display:flex;align-items:center;justify-content:flex-end;gap:4px;flex-wrap:wrap;font-size:11px;color:var(--ink-3);margin-top:8px}
.jlegend i{display:inline-block;width:12px;height:12px;border-radius:3px}
.jcalhead{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}
.jmonth{margin:0;font-size:15px;font-weight:700;color:var(--ink)}
.jcal{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:5px}
.jcal .wd{font-size:10.5px;color:var(--ink-3);text-align:center;padding:2px 0}
.jcal .jd{min-height:62px;min-width:0;border-radius:9px;border:1px solid var(--bd-soft);padding:5px 6px;
  display:flex;flex-direction:column;justify-content:space-between;cursor:pointer;background:rgba(255,255,255,.02)}
.jcal .jd.empty{border:0;background:transparent;cursor:default}
.jcal .jd .n{font-size:11px;color:var(--ink-3)}
.jcal .jd .v{font-size:11.5px;font-weight:700;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.jcal .jd .c{font-size:10px;color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.jcal .jd.today .n{color:var(--accent);font-weight:700}
.jcal .jd.sel{outline:2px solid var(--accent);outline-offset:1px}
@media(max-width:600px){.jcal{gap:3px}.jcal .jd{min-height:46px;padding:3px 4px}.jcal .jd .c{display:none}.jcal .jd .v{font-size:9.5px}}
.jnotes{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--ink-3);margin-top:12px}
.jnotes textarea{background:var(--raised);border:1px solid var(--bd);border-radius:10px;color:var(--ink);
  padding:8px 10px;font:inherit;font-size:13px;resize:vertical;min-height:44px}
.jactions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px}
.jmsg{font-size:12.5px}
.jmuted{color:var(--ink-3);font-size:13px;margin:0}
.jbadge{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;
  border:1px solid var(--bd);border-radius:999px;padding:1px 7px;color:var(--ink-3)}
.jbadge.mine{color:var(--accent);border-color:rgba(77,148,232,.45)}
#jdaytbl td:nth-child(-n+3),#jdaytbl th:nth-child(-n+3),#jdaytbl td:nth-last-child(2),#jdaytbl th:nth-last-child(2){text-align:left}
#jdaytbl td.jnote{white-space:normal;min-width:180px;max-width:320px;color:var(--ink-2)}
.rrcard{margin-top:12px;border:1px solid var(--bd);border-radius:12px;padding:12px 14px}
.rrcard:empty{display:none}
.rrsum{font-size:12.5px;color:var(--ink-2);line-height:1.65;margin:4px 0 10px}
.rrsum b{color:var(--ink)}
.rrtbl td,.rrtbl th{white-space:nowrap}
.rrtbl tr.exit td{background:rgba(255,255,255,.035)}
.wiftbl td:first-child{white-space:normal;min-width:150px}
@media (max-width:560px){
  /* Premium and rupees are what matter on a phone; the per-unit change is the
     difference of the two and would push the rupee column off the card. */
  .wiftbl td:first-child{min-width:0}
  .wiftbl th:nth-child(3),.wiftbl td:nth-child(3){display:none}
}
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
.tskip{color:var(--warn);border-color:rgba(242,163,61,.45)}
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
.feedline{font-size:12px;color:var(--ink-3);margin-top:9px;
  display:flex;flex-wrap:wrap;gap:0 14px}
.feedline span{white-space:nowrap}
@media(max-width:640px){.session{flex-direction:column;align-items:stretch}
  .today{text-align:left}}

.ladder{margin-top:12px;border-top:1px solid var(--bd-soft);padding-top:14px}
.rung{display:flex;align-items:center;gap:12px;padding:7px 0;
  border-bottom:1px solid var(--bd-soft)}
.rung:last-child{border-bottom:0}
.rung .k{width:88px;font-size:11px;font-weight:700;letter-spacing:.5px;color:var(--ink-3)}
.rung .bar{flex:1;height:4px;border-radius:2px;background:var(--bd-soft);overflow:hidden}
.rung .bar i{display:block;height:100%;border-radius:2px;
  transition:width .45s ease-out}
.rung .n{width:92px;text-align:right;font-size:14.5px;font-weight:650;
  font-variant-numeric:tabular-nums}
.rung .rs{width:104px;text-align:right;font-size:12px;color:var(--ink-3);
  font-variant-numeric:tabular-nums}
.rung .od{width:52px;text-align:right;font-size:12px;opacity:.9;
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

/* ---------- screener, sectors, recap ---------- */
.scrctl{display:flex;gap:8px;align-items:center;margin-bottom:8px;font-size:12px;color:var(--ink-3)}
.scrctl input,.scrctl select{background:var(--raised);color:var(--ink);border:1px solid var(--bd);
  border-radius:8px;padding:4px 8px;font:inherit;font-size:12px}
.scrctl input{width:110px}
.scrwrap{max-height:360px;overflow:auto;-webkit-overflow-scrolling:touch;
  border:1px solid var(--bd);border-radius:12px;
  background:rgba(6,8,12,.55)}
table.scr{width:100%;border-collapse:collapse;font-size:12.5px;
  font-variant-numeric:tabular-nums}
table.scr th{position:sticky;top:0;background:rgba(10,12,18,.97);font-size:10px;
  letter-spacing:.5px;text-transform:uppercase;color:var(--ink-3);font-weight:700;
  padding:7px 8px;text-align:right;cursor:pointer;white-space:nowrap}
table.scr th:first-child,table.scr td:first-child{text-align:left}
table.scr th.on{color:var(--ink-2)}
table.scr td{padding:6px 8px;text-align:right;border-top:1px solid var(--bd-soft);
  color:var(--ink-2);white-space:nowrap}
table.scr td.sym{color:var(--ink);font-weight:650}
table.scr td.sec{color:var(--ink-3);font-size:11.5px}
.sect{display:grid;gap:7px}
.sectrow{display:grid;grid-template-columns:104px 1fr 62px;gap:10px;align-items:center;
  font-size:12.5px;color:var(--ink-2)}
.sectbar{height:8px;border-radius:5px;background:linear-gradient(180deg,#0a0b0f,#16181f);
  box-shadow:inset 0 2px 3px rgba(0,0,0,.55);position:relative;overflow:hidden}
.sectbar i{position:absolute;top:0;height:100%;border-radius:5px}
.sectbar u{position:absolute;top:-2px;bottom:-2px;left:50%;width:1px;background:var(--bd)}
.sectval{text-align:right;font-variant-numeric:tabular-nums;font-weight:650}
.recap{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));gap:10px}
.recap .r{background:rgba(255,255,255,.03);border:1px solid var(--bd);border-radius:12px;padding:10px 12px}
.recap .r .l{font-size:10.5px;color:var(--ink-3);text-transform:uppercase;letter-spacing:.4px}
.recap .r .v{font-size:19px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums}
.recaplist{margin-top:10px;font-size:12.5px;color:var(--ink-3)}
.recaplist div{padding:5px 0;border-top:1px solid var(--bd-soft)}

/* ---------- headlines ---------- */
.news{display:grid;gap:0}
.news a{display:block;padding:9px 2px;border-bottom:1px solid var(--bd-soft);
  color:var(--ink-2);font-size:13.5px;line-height:1.45;text-decoration:none}
.news a:last-child{border-bottom:0}
.news a:hover{color:var(--ink)}
.news .m{display:flex;gap:8px;align-items:center;margin-top:3px;
  font-size:11px;color:var(--ink-3)}
.news .src{border:1px solid var(--bd);border-radius:999px;padding:1px 7px}
.newsnote{color:var(--ink-3);font-size:11.5px;margin-top:9px}
.newsnote:empty{display:none}

/* ---------- sections ----------
   The signal stays above these; everything else lives in a pane and only one
   pane is on screen at a time. Panes are hidden, never torn down, so the
   chart keeps its scroll and the chain keeps its place in the strikes. */
/* The menu moved into the sidebar, so the sections area is just the panes. */
.sections{display:block;margin-top:16px}
.tabs-legacy{display:none}

/* ---------- home ---------- */
.hsec{margin:0 0 26px}
.htitle{font-size:clamp(26px,3.4vw,38px);line-height:1.1;margin:0 0 8px;letter-spacing:-.8px;
  font-weight:800}
.hsub{color:var(--ink-2);font-size:14.5px;max-width:680px;margin:0 0 16px}
.gmk{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:10px}
.gmk .q{background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.015)),
  rgba(9,11,17,.72);border:1px solid var(--bd);border-radius:14px;padding:12px 14px}
.gmk .q .n{font-size:11px;letter-spacing:.5px;text-transform:uppercase;color:var(--ink-3);
  font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gmk .q .p{font-size:19px;font-weight:700;margin-top:3px;font-variant-numeric:tabular-nums}
.gmk .q .c{font-size:12px;font-weight:650;margin-top:2px;font-variant-numeric:tabular-nums}
.dgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
.dcard{display:block;text-align:left;background:linear-gradient(180deg,rgba(255,255,255,.05),
  rgba(255,255,255,.015)),rgba(9,11,17,.72);border:1px solid var(--bd);border-radius:16px;
  padding:16px 18px;cursor:pointer;font:inherit;color:inherit;text-decoration:none;
  transition:transform .2s,border-color .2s,box-shadow .2s}
.dcard:hover{transform:translateY(-2px);border-color:rgba(43,224,138,.35);
  box-shadow:0 20px 50px -25px var(--glow-up);text-decoration:none}
.dcard .i{font-size:20px;line-height:1}
.dcard b{display:block;margin:9px 0 4px;font-size:15px;color:var(--ink)}
.dcard span{font-size:12.5px;color:var(--ink-3);line-height:1.5;display:block}

/* ---------- index mover, pulse, option clock, calculator ---------- */
.mover,.pulse,.clock2{display:grid;gap:7px}
.mvrow{display:grid;grid-template-columns:96px 1fr 74px;gap:10px;align-items:center;font-size:12.5px}
.mvrow .s{color:var(--ink-2);font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mvbar{height:8px;border-radius:5px;background:linear-gradient(180deg,#0a0b0f,#16181f);
  position:relative;overflow:hidden;box-shadow:inset 0 2px 3px rgba(0,0,0,.55)}
.mvbar i{position:absolute;top:0;height:100%;border-radius:5px}
.mvbar u{position:absolute;top:-2px;bottom:-2px;left:50%;width:1px;background:var(--bd)}
.mvval{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.pulse .ph{font-size:10.5px;letter-spacing:.6px;text-transform:uppercase;color:var(--ink-3);
  font-weight:700;margin-top:6px}
.pulse .pr{display:flex;justify-content:space-between;gap:10px;font-size:12.5px;
  color:var(--ink-2);padding:3px 0;font-variant-numeric:tabular-nums}
.calcgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.calcgrid label{display:flex;flex-direction:column;gap:5px;font-size:11.5px;color:var(--ink-3)}
.calcgrid input{background:var(--raised);border:1px solid var(--bd);border-radius:9px;
  padding:8px 10px;color:var(--ink);font:inherit;font-size:13.5px}
.calcout{margin-top:12px;font-size:13px;color:var(--ink-2);line-height:1.7}
.calcout b{color:var(--ink)}

/* ---------- the status strip ---------- */
.status{display:inline-flex;align-items:center;gap:9px;background:rgba(255,255,255,.05);
  border:1px solid var(--bd);border-radius:999px;padding:6px 14px;font-size:12.5px;
  font-weight:650;color:var(--ink-2);white-space:nowrap}
.status .st-sep{width:1px;height:14px;background:var(--bd)}
.status .st-mkt{color:var(--ink)}
.status .st-clock{font-variant-numeric:tabular-nums;color:var(--ink-3);font-weight:600}
.chip{display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.04);
  border:1px solid var(--bd);border-radius:999px;padding:6px 13px;font-size:12.5px;
  font-weight:650;color:var(--ink-2);text-decoration:none;white-space:nowrap}
.chip:hover{color:var(--ink);background:rgba(255,255,255,.08);text-decoration:none}
.acct{display:inline-flex;align-items:center;gap:8px;background:rgba(77,148,232,.12);
  border:1px solid rgba(77,148,232,.35);border-radius:999px;padding:4px 12px 4px 4px;
  font-size:12.5px;font-weight:650;color:var(--ink)}
.acct span{max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.acct i{width:24px;height:24px;border-radius:50%;background:linear-gradient(180deg,#5aa2ee,#3a7fd0);
  color:#fff;display:flex;align-items:center;justify-content:center;font-style:normal;
  font-size:12px;text-transform:uppercase}
@media(max-width:900px){.acct span{display:none}}

.admmsg{font-size:12.5px;min-height:18px;margin:2px 0 8px}
.admbadge{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.4px;
  text-transform:uppercase;border:1px solid;border-radius:999px;padding:1px 8px}
.admyou{font-size:10.5px;color:var(--ink-3);margin-left:7px}
.admbox{display:flex;flex-direction:column;gap:9px;padding:8px 2px;text-align:left}
.admline{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.admline span{font-size:11.5px;color:var(--ink-3);min-width:98px}
.admline input{background:var(--raised);border:1px solid var(--bd);border-radius:9px;
  color:var(--ink);padding:5px 9px;font:inherit;font-size:12.5px;color-scheme:dark}
.admquick{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.calcgrid input[type=date]{color-scheme:dark}
.lbtn.admdanger{color:var(--down);border-color:rgba(239,85,112,.45)}
.admtbl td{white-space:nowrap}
.admtbl tr.admdetail td{white-space:normal}
/* The manage panel sits inside a table that scrolls sideways on a phone. Pin it
   to the visible width so its buttons wrap in view instead of running off under
   the scroll, where Delete could not be seen at all. */
@media (max-width:760px){
  .admbox{position:sticky;left:0;width:calc(100vw - 92px);max-width:calc(100vw - 92px)}
  .admline span{min-width:0;width:100%}
}
.adm [hidden],.menu .tab[hidden]{display:none!important}
.sidefoot .su small{font-size:10.5px;color:var(--ink-3);margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px}
.adwrap{margin-top:4px}
.adwrap canvas{display:block;width:100%;max-width:100%}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
  gap:14px;margin-top:14px;align-items:start}
.spkbar{font-size:12.5px;color:var(--ink-2);margin-bottom:8px}
.spkbar .lv{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.5px;
  text-transform:uppercase;border:1px solid var(--bd);border-radius:999px;
  padding:2px 8px;margin-right:6px;color:var(--ink-3)}
.spkbar .lv.on{color:var(--up);border-color:rgba(43,224,138,.45)}
.clockbar{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:2px 0 8px}
.clockbar select{background:var(--sunken);color:var(--ink-2);border:1px solid var(--bd);
  border-radius:8px;padding:4px 8px;font:inherit;font-size:12px;font-weight:650;
  max-width:100%}
.clockbar .to{color:var(--ink-3);font-size:12px}
.clockhead{font-size:12.5px;color:var(--ink-2);margin-bottom:8px;line-height:1.5}

/* ---------- the sidebar ---------- */
.side{position:fixed;left:0;top:0;bottom:0;width:236px;z-index:40;display:flex;
  flex-direction:column;gap:2px;padding:16px 12px 12px;overflow-y:auto;
  background:rgba(8,10,16,.82);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  border-right:1px solid var(--bd)}
.main{margin-left:236px;min-width:0}
.sbrand{display:flex;align-items:center;gap:10px;font-weight:700;letter-spacing:-.2px;
  padding:6px 8px 16px}
.sbrand small{display:block;font-weight:500;font-size:10px;color:var(--ink-3);
  letter-spacing:.3px;text-transform:uppercase;margin-top:2px}
.menu{display:flex;flex-direction:column;gap:2px}
.mgroup{font-size:10.5px;font-weight:700;letter-spacing:.9px;text-transform:uppercase;
  color:var(--ink-3);margin:14px 0 6px;padding-left:10px}
.menu .tab{display:flex;align-items:center;gap:10px;background:transparent;
  border:1px solid transparent;color:var(--ink-2);border-radius:10px;padding:9px 11px;
  font-size:13.5px;font-weight:600;cursor:pointer;font-family:inherit;text-align:left;
  text-decoration:none;transition:background .15s,color .15s,border-color .15s}
.menu .tab i{font-style:normal;font-size:14px;width:18px;text-align:center;flex:none;opacity:.9}
.menu .tab:hover{color:var(--ink);background:rgba(255,255,255,.05);text-decoration:none}
.menu .tab.on{background:linear-gradient(90deg,rgba(77,148,232,.22),rgba(77,148,232,.05));
  border-color:rgba(77,148,232,.45);color:var(--ink)}
.sidefoot{margin-top:auto;display:flex;align-items:center;gap:8px;padding:10px 10px 4px;
  border-top:1px solid var(--bd-soft);font-size:12px;color:var(--ink-3)}
.sidefoot .su{display:flex;flex-direction:column;line-height:1.25;min-width:0}
.sidefoot b{color:var(--ink-2);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;max-width:150px}
.sout{margin-left:auto;color:var(--ink-3);font-size:15px;text-decoration:none;
  border:1px solid var(--bd);border-radius:8px;padding:2px 8px}
.sout:hover{color:var(--down);border-color:rgba(239,85,112,.5);text-decoration:none}
/* On a phone the sidebar used to become every section as a wrapped pill
   across the top - twenty of them, a full screen of menu before any content.
   It is a drawer instead, opened from the header, with the four sections used
   most in a bar along the bottom within reach of a thumb. */
.navbtn,.botnav,.navscrim{display:none}
@media(max-width:900px){
  .side{width:min(300px,86vw);transform:translateX(-104%);visibility:hidden;z-index:70;
    transition:transform .25s cubic-bezier(.2,.8,.2,1),visibility .25s;
    background:rgba(8,10,16,.97);padding:14px 12px calc(12px + env(safe-area-inset-bottom))}
  body.navopen .side{transform:none;visibility:visible;box-shadow:18px 0 48px rgba(0,0,0,.55)}
  .navscrim{display:block;position:fixed;inset:0;z-index:65;background:rgba(0,0,0,.5);
    opacity:0;pointer-events:none;transition:opacity .2s}
  body.navopen .navscrim{opacity:1;pointer-events:auto}
  body.navopen{overflow:hidden}
  .main{margin-left:0;width:100%;max-width:100%}
  body{overflow-x:hidden;padding-bottom:calc(66px + env(safe-area-inset-bottom))}
  .wrap{padding-left:14px;padding-right:14px}
  .navbtn{display:inline-flex;align-items:center;gap:7px;flex:0 1 auto;min-width:0;
    background:rgba(255,255,255,.05);border:1px solid var(--bd);color:var(--ink);
    border-radius:999px;padding:6px 12px 6px 10px;font:inherit;font-size:13px;font-weight:650;cursor:pointer}
  .navbtn i{font-style:normal;font-size:15px;line-height:1}
  .navbtn span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:34vw}
  /* under the .pal overlay (60), over the page */
  .botnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:55;gap:2px;
    padding:6px 6px calc(6px + env(safe-area-inset-bottom));
    background:rgba(8,10,16,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
    border-top:1px solid var(--bd)}
  .botnav button{flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:center;gap:3px;
    background:transparent;border:0;color:var(--ink-3);font:inherit;font-size:10.5px;font-weight:650;
    padding:6px 2px;border-radius:10px;cursor:pointer}
  .botnav button i{font-style:normal;font-size:18px;line-height:1}
  .botnav button span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
  .botnav button.on{color:var(--ink);background:rgba(77,148,232,.16)}
}
/* A small phone: the menu button, the market state and Switch market have to
   share one line - at 360px "Switch market" dropped onto a second. The divider
   goes and the market state gives way with an ellipsis before anything wraps. */
@media(max-width:420px){
  .hd{flex-wrap:nowrap;gap:6px;padding:10px}
  .hd .status{flex:0 1 auto;min-width:0;padding:6px 10px;gap:7px}
  .status .st-sep{display:none}
  .status .st-mkt{min-width:0;overflow:hidden;text-overflow:ellipsis}
  .hd .row{gap:6px!important;flex-wrap:nowrap!important}
  .hd .chip{padding:6px 10px}
  .navbtn{padding:6px 10px 6px 9px}
  .navbtn span{max-width:24vw}
}
.panes{min-width:0}
.pane{display:none}
.pane.on{display:block}
/* Under a laptop width the rail costs more than it gives, so it becomes a
   strip across the top that you can push with a thumb. */


/* ---------- a workspace you can arrange ----------
   The drag handle appears on hover, top-right of a movable panel. Only the
   two side-by-side stacks take part: the signal, the trend row and the
   reasoning are full-width and their order on the page is the argument the
   screen is making, so they stay where they are. */
[data-panel]{position:relative}
.grip{position:absolute;top:9px;right:9px;z-index:4;width:24px;height:22px;
  border-radius:7px;border:1px solid var(--bd);background:rgba(255,255,255,.06);
  color:var(--ink-3);font-size:11px;line-height:1;display:flex;align-items:center;
  justify-content:center;cursor:grab;opacity:0;transition:opacity .15s}
[data-panel]:hover > .grip,.grip:focus{opacity:1}
.grip:active{cursor:grabbing}
[data-panel].dragging{opacity:.45}
[data-panel].over{outline:2px dashed rgba(77,148,232,.65);outline-offset:3px}

/* A hidden panel stays hidden: several of these are grid or flex containers
   whose own display would otherwise win against the hidden attribute. */
[hidden]{display:none !important}

/* ---------- option chain ----------
   Calls on the left, puts on the right, strikes down the middle - the way a
   chain is read everywhere. Built from the same snapshot the signal was
   computed from, so the two can never disagree. */
.chainwrap{max-height:420px;overflow:auto;-webkit-overflow-scrolling:touch;
  border:1px solid var(--bd);
  border-radius:12px;background:rgba(6,8,12,.55)}
/* Ten columns of tabular figures do not fit a phone, so .chainwrap scrolls
   them. No min-width here: the wrapper clips and scrolls on its own, and the
   min-width added earlier was answering a broken check, not a broken page. */
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
/* On a phone the index cards were one full-width row each - a third of the
   first screen spent on three numbers. One row of equal columns instead,
   however many indices the market has (three here, one for Bitcoin). */
@media(max-width:900px){
  .markets{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);gap:8px}
  .mkt{display:block;min-width:0}
}
@media(max-width:600px){
  .mkt{padding:10px 10px 9px}
  .mkt .nm{font-size:10px;letter-spacing:.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .mkt .px{font-size:17px;letter-spacing:-.3px;margin-top:2px}
  .mkt .ex{font-size:10px;overflow:hidden;text-overflow:ellipsis}
  .mkt .st{display:flex;align-items:flex-start;font-size:10.5px;line-height:1.25;margin-top:4px}
  .mkt .st .swatch{margin-top:3px}
}

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

<aside class="side" id="side">
 <a class="sbrand" href="/" style="color:inherit;text-decoration:none">
  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="1" y="1" width="22" height="22" rx="6" fill="#1b1b20" stroke="#2a2a31"/><rect x="4.6" y="11.5" width="2.2" height="6" rx="1" fill="#3a4050"/><rect x="8.3" y="9.5" width="2.2" height="8" rx="1" fill="#3a4050"/><rect x="12" y="6.5" width="2.6" height="11" rx="1.1" fill="#4d94e8"/><rect x="16.4" y="12.5" width="2.2" height="5" rx="1" fill="#3a4050"/><circle cx="13.3" cy="4.4" r="2.1" fill="#4caf50"/><path d="M12.4 4.4l.7.7 1.3-1.4" stroke="#0d1117" stroke-width="1" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
  <div>TradePicker<small id="sidesub">Nifty · Bank Nifty · Sensex · Bitcoin</small></div>
 </a>
 <nav class="menu" id="tabs" role="tablist" aria-label="Sections">
  <button class="tab on" data-tab="home" role="tab" type="button"><i>&#127968;</i>Home</button>
  <p class="mgroup">Desk</p>
  <button class="tab" data-tab="signal" role="tab" type="button"><i>&#127919;</i>Signal</button>
  <button class="tab" data-tab="journal" role="tab" type="button"><i>&#128211;</i>Journal</button>
  <p class="mgroup">Market</p>
  <button class="tab" data-tab="chart" role="tab" type="button"><i>&#128200;</i>Chart</button>
  <button class="tab" data-tab="chain" role="tab" type="button"><i>&#9939;</i>Option chain</button>
  <button class="tab" data-tab="market" role="tab" type="button"><i>&#128506;</i>Market</button>
  <button class="tab" data-tab="pulse" role="tab" type="button"><i>&#128200;</i>Market pulse</button>
  <button class="tab" data-tab="screener" role="tab" type="button"><i>&#128269;</i>Screener</button>
  <button class="tab" data-tab="sector" role="tab" type="button"><i>&#129518;</i>Sector scope</button>
  <button class="tab" data-tab="spikes" role="tab" type="button"><i>&#9889;</i>Momentum spikes</button>
  <p class="mgroup">Analysis</p>
  <button class="tab" data-tab="vol" role="tab" type="button"><i>&#127786;</i>Volatility</button>
  <button class="tab" data-tab="greeks" role="tab" type="button"><i>&#120491;</i>Greeks &amp; IV</button>
  <button class="tab" data-tab="levels" role="tab" type="button"><i>&#128207;</i>Levels</button>
  <button class="tab" data-tab="internals" role="tab" type="button"><i>&#128202;</i>Internals</button>
  <button class="tab" data-tab="strength" role="tab" type="button"><i>&#127947;</i>Relative strength</button>
  <button class="tab" data-tab="season" role="tab" type="button"><i>&#128197;</i>Seasonality</button>
  <p class="mgroup">Research</p>
  <button class="tab" data-tab="news" role="tab" type="button"><i>&#128240;</i>News</button>
  <button class="tab" data-tab="record" role="tab" type="button"><i>&#128188;</i>Record</button>
  <p class="mgroup">Account</p>
  <a class="tab" href="/connect"><i>&#128279;</i>Zerodha</a>
  <a class="tab" href="/how-it-works"><i>&#10067;</i>How it works</a>
  <button class="tab" data-tab="admin" role="tab" type="button" hidden><i>&#128737;</i>Admin</button>
 </nav>
 <div class="sidefoot">
  <div class="su">Signed in<b id="sideuser">&mdash;</b><small id="siderenew"></small></div>
  <a class="sout" href="/logout" title="Sign out">&#9211;</a>
 </div>
</aside>
<div class="navscrim" id="navscrim"></div>
<nav class="botnav" id="botnav" aria-label="Main sections">
 <button class="tab" data-tab="home" type="button"><i>&#127968;</i><span>Home</span></button>
 <button class="tab" data-tab="signal" type="button"><i>&#127919;</i><span>Signal</span></button>
 <button class="tab" data-tab="chart" type="button"><i>&#128200;</i><span>Chart</span></button>
 <button class="tab" data-tab="chain" type="button"><i>&#9939;</i><span>Chain</span></button>
 <button id="bnmore" type="button" aria-controls="side" aria-expanded="false"><i>&#9776;</i><span>More</span></button>
</nav>

<div class="main">
<header><div class="hd">
  <button class="navbtn" id="navbtn" type="button" aria-controls="side"
    aria-expanded="false" aria-label="Open the menu"><i>&#9776;</i><span id="navtitle">Home</span></button>
  <!-- The brand sits in the sidebar now; printing it again here was the same
       words twice across the top of the screen. -->
  <!-- One status strip rather than three pills that each said a different
       thing in a different shape: whether the market is trading, whether the
       feed is alive, and the clock it was last true at. -->
  <div class="status">
   <span class="beat" id="beat"></span><span class="st-mkt" id="mkt">connecting</span>
   <span class="st-sep"></span>
   <span class="feedtag" id="feed">&mdash;</span>
   <span class="st-clock" id="upd">&mdash;</span>
  </div>
  <div class="row" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <a class="chip" id="mktsw" href="/market" style="display:none"
       title="Switch market">&mdash;</a>
    <!-- Who you are signed in as, and signing out, are in the sidebar footer.
         They were printed here as well, and the duplicate is what collided
         with the market chip in the top corner. Zerodha stays: it changes to
         "Connect Zerodha" when the broker session needs attention, which is
         worth a place at the top of the screen. -->
    <a class="chip" id="kite" href="/connect" style="display:none">Connect Zerodha</a>
  </div>
</div></header>

<div class="ticker" aria-label="World market levels"><div class="tk-track" id="tkt"></div></div>

<div class="wrap">

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

 <div class="notice stale" id="renewnote" style="display:none">
  <div><b>Renewal due.</b> <span id="renewmsg"></span> Ask the administrator to
   renew it before then.</div>
 </div>

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

 <div class="sections">
 <nav class="tabs-legacy" hidden aria-hidden="true">
  <button class="tab on" data-tab="chart" role="tab" type="button">Chart</button>
  <button class="tab" data-tab="chain" role="tab" type="button">Option chain</button>
  <button class="tab" data-tab="market" role="tab" type="button">Market</button>
  <button class="tab" data-tab="news" role="tab" type="button">News</button>
  <button class="tab" data-tab="record" role="tab" type="button">Record</button>
 </nav>
 <div class="panes">

 <section class="pane on" data-pane="home">
  <div class="hsec">
   <h2 class="htitle">Global markets</h2>
   <p class="hsub">Where the wider market is sitting, before you look at a single
    strike. These are the same levels that scroll across the top.</p>
   <div class="gmk" id="gmk"></div>
  </div>
  <div class="welcome">
  <div>
  <p class="eyebrow">Overview</p>
  <h1>Welcome, <span class="who" id="who">—</span></h1>
  <p class="said" id="said">Nifty, Bank Nifty and Sensex — one screen for the session.</p>
  <p class="said" id="renewline"></p>
  </div>
  <div class="acts">
  <a class="lbtn" href="/how-it-works">How it works</a>
  <a class="lbtn" href="/connect">Zerodha</a>
  <a class="lbtn" href="/results">Results</a>
  </div>
  </div>
  <div class="hsec">
   <p class="eyebrow">Today</p>
   <div class="recap" id="htoday"></div>
  </div>
  <div class="hsec">
   <p class="eyebrow">Your desk</p>
   <div class="dgrid" id="dgrid"></div>
  </div>
 </section>

 <section class="pane" data-pane="signal">
  <div class="session" id="session" style="display:none">
  <span class="lbl">Session</span>
  <div class="chips" id="schips"></div>
  <div class="today">
  <div class="n" id="snet">—</div>
  <div class="d" id="sdetail"></div>
  </div>
  </div>
  <div class="feedline" id="sfeed"></div>

  <div class="card" data-panel="posgk" id="posgkcard" hidden style="margin-top:14px">
   <p class="eyebrow">This position &middot; what it is exposed to</p>
   <div class="pulse" id="posgk"></div>
   <div class="gnote" id="posgknote"></div>
  </div>
  <div class="card herocard" id="sigcard" data-panel="signal" style="margin-top:14px">
  <div class="thead">
  <p class="eyebrow" id="teyebrow">Signal</p>
  <span class="badge prev" id="tbadge" style="display:none"></span>
  <button class="lbtn tclear" id="tclear" type="button"
  style="display:none">Clear ticket</button>
  <button class="lbtn tclear tskip" id="tskip" type="button"
  style="display:none">Skip cooldown</button>
  </div>
  <div class="hero">
  <div class="v" id="bias">—</div>
  <span class="tag flat" id="conftag" style="display:none"></span>
  <span class="tag flat" id="exptag" style="display:none"></span>
  </div>
  <div class="contract" id="tcontract" style="display:none"></div>
  <div class="issued" id="tissued" style="display:none"></div>
  <div class="tstats" id="tstats" style="display:none"></div>
  <div class="overnight" id="tovernight" style="display:none"></div>
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
  <div class="gnote" id="laddernote"></div>
  <div class="lnote" id="lnote"></div>
  <div class="rrcard" id="rr"></div>
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

  <!-- The sections. One screen used to be one long scroll; the signal, the
  index cards and the session stay pinned above this, and everything else
  lives behind a tab. Panes are switched by hiding, never by rebuilding:
  the chart keeps its scroll position, the chain keeps its place in the
  strikes, and nothing is re-fetched just because you looked away. -->
 </section>

 <section class="pane" data-pane="chart">
  <div class="grid">
   <div id="colL">
    <div class="card" data-panel="chart">
    <p class="eyebrow">Price &middot; <span id="tflabel">15-minute candles</span></p>
    <div class="chartwrap">
    <div class="chartbar">
    <div class="chartlegend" id="cvlegend"></div>
    <div class="chartctl">
    <button class="lbtn tf" data-tf="5m" type="button">5m</button>
    <button class="lbtn tf on" data-tf="15m" type="button">15m</button>
    <button class="lbtn tf" data-tf="1d" type="button">1D</button>
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
    <span><i class="swatch" style="background:var(--up)"></i>Up candle</span>
    <span><i class="swatch" style="background:var(--down)"></i>Down candle</span>
    </div>
    </div>
   </div>
   <div id="colR">
    <div class="card" data-panel="range">
    <p class="eyebrow">Today's range</p>
    <div class="tiles" style="grid-template-columns:1fr" id="trendtiles"></div>
    </div>
    
   </div>
  </div>
 </section>

 <section class="pane" data-pane="chain">
  <div class="card" data-panel="clock" id="clockcard">
   <p class="eyebrow">Option clock &middot; open interest change in the window</p>
   <div class="clockbar">
    <select id="oiday" aria-label="Day"></select>
    <select id="oiexp" aria-label="Expiry"></select>
    <select id="oifrom" aria-label="Window start"></select>
    <span class="to">to</span>
    <select id="oito" aria-label="Window end"></select>
   </div>
   <div class="clockhead" id="oihead"></div>
   <div class="clock2" id="clock2"></div>
   <div class="gnote" id="clocknote"></div>
  </div>
  <div class="card" data-panel="chain" id="chaincard">
  <p class="eyebrow">Option chain &middot; <span id="chainhead">&mdash;</span></p>
  <div class="chainwrap"><table class="chain" id="chain"></table></div>
  <div class="chainbar" id="chainbar"></div>
  </div>
 </section>

 <section class="pane" data-pane="market">
  <div id="colM2">
   <div class="card" data-panel="mover" id="movercard">
    <p class="eyebrow">Index mover &middot; who is pushing <span id="movidx">&mdash;</span></p>
    <div class="mover" id="mover"></div>
    <div class="gnote" id="movernote"></div>
   </div>
   <div class="card" data-panel="screen" id="screencard">
    <p class="eyebrow">Constituents &middot; <span id="scrcount">&mdash;</span></p>
    <div class="scrctl">
     <input id="scrq" placeholder="filter" autocomplete="off" spellcheck="false">
     <select id="scrsec"><option value="">all sectors</option></select>
     <span id="scrnote" style="margin-left:auto"></span>
    </div>
    <div class="scrwrap"><table class="scr" id="scr"></table></div>
   </div>
  </div>
 </section>

 <!-- Market pulse: breadth, then the screeners the daily candles support.
      The panels here are fed by /api/screen, which covers the index
      constituents this tool knows - not the whole exchange, and it says so. -->
 <section class="pane" data-pane="pulse">
  <div class="card" data-panel="pulse" id="pulsecard">
   <p class="eyebrow">Market pulse &middot; up against down</p>
   <div class="pulse" id="pulse"></div>
  </div>
  <div class="grid2">
   <div class="card" data-panel="bo10" id="bo10card">
    <p class="eyebrow">Breakout beacon &middot; 10-day high or low</p>
    <div class="scrwrap"><table class="scr" id="bo10"></table></div>
   </div>
   <div class="card" data-panel="bo50" id="bo50card">
    <p class="eyebrow">Swing spectrum &middot; 50-day high or low</p>
    <div class="scrwrap"><table class="scr" id="bo50"></table></div>
   </div>
   <div class="card" data-panel="boost" id="boostcard">
    <p class="eyebrow">Intraday boost &middot; volume against its 20-session average</p>
    <div class="scrwrap"><table class="scr" id="boost"></table></div>
   </div>
   <div class="card" data-panel="levels" id="levelscard">
    <p class="eyebrow">Top and low level &middot; nearest its 50-day extreme</p>
    <div class="scrwrap"><table class="scr" id="levels"></table></div>
   </div>
  </div>
  <div class="gnote" id="pulsenote"></div>
 </section>

 <!-- Sector scope: the treemap and the sector strengths, moved here from the
      Market tab rather than drawn a second time. -->
 <section class="pane" data-pane="sector">
  <div class="card" data-panel="map" id="mapcard">
   <p class="eyebrow">Market map &middot; <span id="mapidx">&mdash;</span> constituents</p>
   <div class="mapwrap" id="mapwrap"></div>
   <div class="mapbar">
    <span id="mapbreadth">loading&hellip;</span>
    <div class="maplegend"><span>&minus;2%</span><i class="sw"></i><span>+2%</span></div>
   </div>
  </div>
  <div class="card" data-panel="sectors" id="sectorcard">
   <p class="eyebrow">Sectors &middot; weighted move today</p>
   <div class="sect" id="sectors"></div>
  </div>
 </section>

 <!-- Short-window momentum. TradeFinder calls this "Insider Strategy"; it is
      not insider anything, and it is not called that here. -->
 <section class="pane" data-pane="spikes">
  <div class="card" data-panel="spk5" id="spk5card">
   <p class="eyebrow">Momentum spikes &middot; <span id="spkhead">last five minutes</span></p>
   <div class="spkbar" id="spkbar"></div>
   <div class="scrwrap"><table class="scr" id="spk5"></table></div>
  </div>
  <div class="card" data-panel="spk10" id="spk10card">
   <p class="eyebrow">Over the last ten minutes</p>
   <div class="scrwrap"><table class="scr" id="spk10"></table></div>
  </div>
  <div class="gnote" id="spknote"></div>
 </section>

 <section class="pane" data-pane="vol">
  <div class="card" data-panel="volhead" id="volheadcard">
   <p class="eyebrow">Volatility &middot; what the market is paying for movement</p>
   <div class="pulse" id="volstats"></div>
   <div class="gnote" id="volnote"></div>
  </div>
  <div class="grid2">
   <div class="card" data-panel="volem" id="volemcard">
    <p class="eyebrow">Expected move &middot; from implied volatility</p>
    <div class="scrwrap"><table class="scr" id="volem"></table></div>
    <div class="gnote" id="volemnote"></div>
   </div>
   <div class="card" data-panel="volrv" id="volrvcard">
    <p class="eyebrow">Realised volatility &middot; what it actually did</p>
    <div class="scrwrap"><table class="scr" id="volrv"></table></div>
   </div>
  </div>
  <div class="card" data-panel="volcone" id="volconecard">
   <p class="eyebrow">Volatility cone &middot; today against its own year</p>
   <div class="scrwrap"><table class="scr" id="volcone"></table></div>
   <div class="gnote" id="volconenote"></div>
  </div>
 </section>

 <section class="pane" data-pane="greeks">
  <div class="card" data-panel="gkhead" id="gkheadcard">
   <p class="eyebrow">Implied volatility &middot; <span id="gkhead">&mdash;</span></p>
   <div class="pulse" id="gkstats"></div>
   <div class="gnote" id="gknote"></div>
  </div>
  <div class="card" data-panel="gkchain" id="gkchaincard">
   <p class="eyebrow">The chain, priced &middot; implied volatility and sensitivities</p>
   <div class="scrwrap"><table class="scr" id="gkchain"></table></div>
   <div class="gnote" id="gkchainnote"></div>
  </div>
  <div class="card" data-panel="gkconc" id="gkconccard">
   <p class="eyebrow">Gamma concentration &middot; open interest weighted</p>
   <div class="scrwrap"><table class="scr" id="gkconc"></table></div>
   <div class="gnote" id="gkconcnote"></div>
  </div>
 </section>

 <section class="pane" data-pane="levels">
  <div class="card" data-panel="lvl" id="lvlcard">
   <p class="eyebrow">Levels &middot; <span id="lvlsess">&mdash;</span></p>
   <div class="scrwrap"><table class="scr" id="lvl"></table></div>
   <div class="gnote" id="lvlnote"></div>
  </div>
  <div class="card" data-panel="lvlor" id="lvlorcard">
   <p class="eyebrow">Opening range &middot; first fifteen minutes</p>
   <div class="scrwrap"><table class="scr" id="lvlor"></table></div>
  </div>
  <div class="card" data-panel="lvlfib" id="lvlfibcard">
   <p class="eyebrow">Fibonacci &middot; the previous session&rsquo;s range</p>
   <div class="scrwrap"><table class="scr" id="lvlfib"></table></div>
   <div class="gnote" id="lvlfibnote"></div>
  </div>
 </section>

 <section class="pane" data-pane="internals">
  <div class="card" data-panel="intn" id="intncard">
   <p class="eyebrow">Market internals &middot; what the members are doing</p>
   <div class="pulse" id="intstats"></div>
   <div class="gnote" id="intnote"></div>
  </div>
  <div class="card" data-panel="intad" id="intadcard">
   <p class="eyebrow">Advance/decline line &middot; last 20 sessions</p>
   <div class="adwrap"><canvas id="adline" height="150"></canvas></div>
   <div class="gnote" id="adnote"></div>
  </div>
 </section>

 <section class="pane" data-pane="strength">
  <div class="grid2">
   <div class="card" data-panel="rsl" id="rslcard">
    <p class="eyebrow">Leaders &middot; <span id="rswin">20</span>-day return</p>
    <div class="scrwrap"><table class="scr" id="rslead"></table></div>
   </div>
   <div class="card" data-panel="rsg" id="rsgcard">
    <p class="eyebrow">Laggards</p>
    <div class="scrwrap"><table class="scr" id="rslag"></table></div>
   </div>
  </div>
  <div class="gnote" id="rsnote"></div>
 </section>

 <section class="pane" data-pane="season">
  <div class="card" data-panel="sdow" id="sdowcard">
   <p class="eyebrow">By weekday &middot; <span id="seasn">&mdash;</span> sessions</p>
   <div class="scrwrap"><table class="scr" id="sdow"></table></div>
   <div class="gnote" id="sdownote"></div>
  </div>
  <div class="card" data-panel="sgap" id="sgapcard">
   <p class="eyebrow">Opening gaps</p>
   <div class="pulse" id="sgap"></div>
  </div>
 </section>

 <section class="pane" data-pane="screener">
  <div class="card" data-panel="scbuild" id="scbuildcard">
   <p class="eyebrow">Build a screen &middot; the Nifty, Bank Nifty and Sensex member stocks</p>
   <div class="scbar">
    <select id="scpick" aria-label="Presets and saved screens"></select>
    <button class="lbtn" type="button" id="scload">Load</button>
    <input id="scname" maxlength="60" autocomplete="off" aria-label="Screen name">
   </div>
   <div id="scconds"></div>
   <div class="jactions">
    <button class="lbtn" type="button" id="scadd">+ Add a condition</button>
    <button class="lbtn on" type="button" id="scrun">Run screen</button>
    <button class="lbtn" type="button" id="scsave">Save</button>
    <button class="lbtn" type="button" id="scdel" style="display:none">Delete this saved screen</button>
    <span class="jmsg" id="scmsg"></span>
   </div>
  </div>
  <div class="card" data-panel="scres" id="screscard">
   <p class="eyebrow" id="screshead">Results &middot; run a screen to see them</p>
   <div class="scrwrap"><table class="scr" id="scres"></table></div>
   <div class="gnote">Daily candles for about the last thirteen months; weekly and monthly are
    built from them. A screen shows what matches now &mdash; it does not say whether trading it
    makes money. A stock without enough history for a condition is left out rather than
    guessed. The condition format follows the open-source Indian-Stock-Market-Screener
    project, and its strategy files use the same fields.</div>
  </div>
 </section>

 <section class="pane" data-pane="journal">
  <div class="jbar">
   <div class="jsrc" id="jsrc" role="group" aria-label="Which trades">
    <button class="lbtn on" type="button" data-src="all">Everything</button>
    <button class="lbtn" type="button" data-src="mine">My trades</button>
    <button class="lbtn" type="button" data-src="tool">Tool tickets</button>
   </div>
   <button class="lbtn on" type="button" id="jaddbtn">+ Add a trade</button>
  </div>
  <div class="card" data-panel="jadd" id="jaddcard" style="display:none">
   <p class="eyebrow" id="jaddtitle">Add a trade</p>
   <div class="calcgrid jform">
    <label>Date<input id="jf_date" type="date"></label>
    <label>Time<input id="jf_time" type="time"></label>
    <label>Instrument<select id="jf_inst"></select></label>
    <label>Side<select id="jf_side"><option>CE</option><option>PE</option><option>FUT</option></select></label>
    <label>Buy or sell<select id="jf_dir"><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
    <label>Strike<input id="jf_strike" inputmode="decimal" autocomplete="off"></label>
    <label>Lots<input id="jf_lots" inputmode="decimal" autocomplete="off" value="1"></label>
    <label>Lot size<input id="jf_lot" inputmode="decimal" autocomplete="off"></label>
    <label>Entry price<input id="jf_entry" inputmode="decimal" autocomplete="off"></label>
    <label>Exit price<input id="jf_exit" inputmode="decimal" autocomplete="off"></label>
    <label>Charges<input id="jf_charges" inputmode="decimal" autocomplete="off" placeholder="blank = estimate"></label>
   </div>
   <label class="jnotes">Why you took it, and how it went
    <textarea id="jf_notes" rows="2" maxlength="2000"></textarea></label>
   <div class="jactions">
    <button class="lbtn on" type="button" id="jf_save">Save trade</button>
    <button class="lbtn" type="button" id="jf_cancel">Cancel</button>
    <span class="jmsg" id="jf_msg"></span>
   </div>
  </div>
  <div class="card" data-panel="jbook" id="jbookcard">
   <p class="eyebrow">Tradebook &middot; <span id="jbookrange">the last twelve months</span></p>
   <div class="jheatwrap"><div class="jheat" id="jheat"></div></div>
   <div class="jlegend" id="jlegend"></div>
  </div>
  <div class="grid2">
   <div class="card" data-panel="jcal" id="jcalcard">
    <div class="jcalhead">
     <button class="lbtn" type="button" id="jprev" aria-label="Previous month">&lsaquo;</button>
     <p class="jmonth" id="jmonth">&mdash;</p>
     <button class="lbtn" type="button" id="jnext" aria-label="Next month">&rsaquo;</button>
    </div>
    <div class="jcal" id="jcal"></div>
   </div>
   <div class="card" data-panel="jstats" id="jstatscard">
    <p class="eyebrow">Statistics &middot; <span id="jstatscope">this month</span></p>
    <div class="jscope" id="jscope" role="group" aria-label="Period">
     <button class="lbtn on" type="button" data-scope="month">This month</button>
     <button class="lbtn" type="button" data-scope="all">All time</button>
    </div>
    <div class="pulse" id="jstats"></div>
   </div>
  </div>
  <div class="card" data-panel="jday" id="jdaycard" style="display:none">
   <p class="eyebrow" id="jdaytitle">&mdash;</p>
   <div class="scrwrap"><table class="scr" id="jdaytbl"></table></div>
   <label class="jnotes">Note for the day
    <textarea id="jdaynote" rows="3" maxlength="2000"
     placeholder="What you saw, what you did, what you would do differently."></textarea></label>
   <div class="jactions">
    <button class="lbtn on" type="button" id="jnotesave">Save note</button>
    <span class="jmsg" id="jnotemsg"></span>
   </div>
  </div>
  <div class="card" data-panel="jrisk" id="jriskcard">
   <p class="eyebrow">Risk &middot; from your own trades</p>
   <div class="pulse" id="jrisk"></div>
   <div class="gnote" id="jrisknote"></div>
  </div>
  <div class="card" data-panel="jreview" id="jreviewcard">
   <p class="eyebrow">Against the backtest &middot; the tool&rsquo;s tickets, per lot</p>
   <div id="jreview"></div>
  </div>
  <div class="gnote">Money is before costs unless a column says otherwise. Charges on
   index options are the figure you typed, or an estimate at Zerodha&rsquo;s published
   rates; slippage is not included. Tool tickets are the trades the rule set issued,
   which are not necessarily the ones you took.</div>
 </section>

 <section class="pane" data-pane="admin">
  <div class="card adm" data-panel="admusers" id="admuserscard">
   <p class="eyebrow">Accounts &middot; <span id="admcount">&mdash;</span></p>
   <div class="admmsg" id="admmsg"></div>
   <div class="scrwrap"><table class="scr admtbl" id="admusers"></table></div>
   <div class="gnote">Access runs through the end of the expiry date, India time.
    An expired account cannot sign in, and anyone already signed in is signed
    out. Admin accounts never expire and cannot be changed from here, so you
    cannot lock yourself out.</div>
  </div>
  <div class="grid2">
   <div class="card adm" data-panel="admcreate" id="admcreatecard">
    <p class="eyebrow">Create an account</p>
    <div class="calcgrid">
     <label>Email<input id="ac_email" type="email" autocomplete="off" spellcheck="false"></label>
     <label>Password<input id="ac_pass" type="text" autocomplete="off" spellcheck="false"></label>
     <label>Access until<input id="ac_exp" type="date"></label>
    </div>
    <div class="admquick">
     <button class="lbtn" type="button" data-cq="30">30 days</button>
     <button class="lbtn" type="button" data-cq="90">90 days</button>
     <button class="lbtn" type="button" data-cq="365">1 year</button>
     <button class="lbtn" type="button" data-cq="never">No expiry</button>
     <button class="lbtn on" type="button" id="ac_go">Create account</button>
    </div>
    <div class="gnote">The password shows as you type on purpose: there is no email
     to send it in, so you read it back to them. At least 10 characters - tell them
     to treat it as temporary.</div>
   </div>
   <div class="card adm" data-panel="admserver" id="admservercard">
    <p class="eyebrow">Server</p>
    <div class="pulse" id="admserver"></div>
   </div>
  </div>
 </section>

 <section class="pane" data-pane="news">
  <div class="card" data-panel="news" id="newscard">
  <p class="eyebrow">Headlines &middot; <span id="newshead">market news</span></p>
  <div class="news" id="news"></div>
  <div class="newsnote" id="newsnote"></div>
  </div>
 </section>

 <section class="pane" data-pane="record">
  <div class="card" data-panel="calc" id="calccard">
   <p class="eyebrow">Position calculator</p>
   <div class="calcgrid">
    <label>Capital<input id="c_cap" inputmode="decimal" autocomplete="off"></label>
    <label>Risk %<input id="c_risk" inputmode="decimal" autocomplete="off"></label>
    <label>Entry premium<input id="c_entry" inputmode="decimal" autocomplete="off"></label>
    <label>Stop premium<input id="c_stop" inputmode="decimal" autocomplete="off"></label>
    <label>Lot size<input id="c_lot" inputmode="decimal" autocomplete="off"></label>
   </div>
   <div class="calcout" id="calcout"></div>
  </div>
  <div class="card" data-panel="recap" id="recapcard">
  <p class="eyebrow">Session recap</p>
  <div class="recap" id="recap"></div>
  <div class="recaplist" id="recaplist"></div>
  </div>
  <div class="card" id="reccard" data-panel="record">
  <p class="eyebrow">Track record &middot; wins and losses</p>
  <div id="record"><p style="color:var(--ink-3);font-size:13px;margin:0">
  No completed trades recorded yet.</p></div>
  </div>
  <div class="card" data-panel="why" style="margin-top:14px">
  <p class="eyebrow">Why — every input, in full</p>
  <div class="why" id="why"></div>
  </div>
 </section>
 </div>
 </div>

 <footer>
  <!-- Said per kind of figure, because the tabs do not all count the same way:
       "all figures exclude costs" stopped being true once the Risk and reward
       panel took Zerodha's charges off and the overnight warning priced decay. -->
  Levels and signals are in index points unless shown as an option premium. Ticket
  P&amp;L, the session total and the Record are the premium move times the lot size,
  <b>before</b> brokerage, STT, exchange charges, GST and slippage, all of which come off
  what you actually keep. Figures marked &ldquo;after charges&rdquo; include Zerodha&rsquo;s
  charges but not slippage. Crypto figures are in dollars, before Deribit&rsquo;s fees.
  Past behaviour of a rule set does not predict its future behaviour. Options can lose
  their entire value. Verify every number with your own broker before risking money.
 </footer>
</div>
</div>

<script>
let CUR=null, LAST=null;
// These are declared here, with the other page globals, because the code in
// this block runs BEFORE the blocks that define the sections, the map and the
// home screen - and a const or let reached before its own declaration throws
// rather than reading as undefined, which silently killed everything after it.
let MKT_ROWS = null;      // the strip's levels, reused by Home
let MAPDATA = null;       // the constituent payload the map fetched
let TAB = "home";         // the section on screen
let GATED_FOR = null;     // which market the tabs were last gated for
const DESK = [
  ["signal", "&#127919;", "Signal", "The call, its strike, the ladder and what is holding it back."],
  ["chart", "&#128200;", "Chart", "Candles with both EMAs and VWAP, at 5m, 15m or daily."],
  ["chain", "&#9939;", "Option chain", "Calls and puts around the money, with the spread you would pay."],
  ["market", "&#128506;", "Market", "The map, sector strength, the constituents and who is moving the index."],
  ["news", "&#128240;", "News", "Headlines from several sources, de-duplicated."],
  ["record", "&#128188;", "Record", "This session, your track record, and the full reasoning."],
];
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
      else if(r.macd_blocked){col="var(--ink-3)";label="Momentum against";}
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
      <div class="st" style="color:${col}"><i class="swatch" style="background:${col}"></i>${esc(label)}</div>`;
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
  $("tiles").innerHTML=""; $("ladder").innerHTML=""; $("rr").innerHTML="";
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
    // The chance of reaching each of THIS ticket's frozen levels from where the
    // index is now. The live ladder had it; this branch returned before it, so
    // the percentages vanished exactly when a position was open.
    const tod = tk.odds || {};
    $("ladder").innerHTML = rungs.map(([k,v,c])=>{
      const done = k==="Stop" ? tk.sl_hit : (tk.hit||{})[k];
      const ch = tod[k === "Stop" ? "stop" : k.toLowerCase()];
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
        <div class="od" style="color:${ch==null?"var(--ink-3)":c}" title="chance of reaching this level">${ch==null?"":ch+"%"}</div>
        <div class="rs" style="color:${rs.startsWith("+")?"var(--up)":rs?"var(--down)":"var(--ink-3)"}">${rs}</div></div>`;
    }).join("");
    const tln = $("laddernote");
    if(tln){
      const crypto = tod.year_minutes === 525600;
      tln.textContent = tod.t1 == null
        ? (tod.minutes === 0
             ? "Chances appear while the market is open - with no time left to the bell there is nothing to compute."
             : tod.iv == null ? "No option chain right now, so the chances cannot be worked out." : "")
        : `Chance of the index reaching each of this ticket's levels ${tod.horizon || "before the close"}, `
          + `from where it is now, with implied volatility at ${tod.iv}%. A level already reached reads 100%. `
          + `They do not add up to 100: a trade can touch a target, turn round and still hit the stop. `
          + (crypto ? "On BTC these are rough - the calibration was fitted on Nifty trades and has not been checked on Bitcoin."
                    : "Calibrated estimates - against 3,582 past trades the model lands within about four points.");
    }
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

  const exitAt = (r.exit_at || "T2").toUpperCase();
  const lbl = k => k === exitAt ? k + " · exit"
                 : k === "T3"   ? "T3 · room check" : k;
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
    // The chance of TOUCHING this level before the bell, from live implied
    // volatility and the minutes actually left. Not exclusive: a trade can
    // touch T1, turn round and still hit the stop, so these do not sum to 100.
    const od = r.odds || {};
    const ch = od[k === "Stop" ? "stop" : k.toLowerCase()];
    return `<div class="rung"><div class="k" title="${k === exitAt
        ? "the trade closes here" : k === "T3"
        ? "not an exit - the room-to-run check that decides whether a ticket is issued at all"
        : ""}">${lbl(k)}</div>
      <div class="bar"><i style="width:${pct}%;background:${v==null?"transparent":c};--c:${c}"></i></div>
      <div class="n" style="color:${v==null?"var(--ink-3)":c}">${v==null?"—":num(v,dp)}</div>
      <div class="od" style="color:${ch==null?"var(--ink-3)":c}" title="chance of touching this level before the close">${ch==null?"":ch+"%"}</div>
      <div class="rs" style="color:${rs.startsWith("+")?"var(--up)":rs?"var(--down)":"var(--ink-3)"}">${rs}</div></div>`;
  }).join("");

  // What these percentages are, and what they are not - said here rather than
  // leaving the reader to assume precision the number does not have.
  const ladderNote = $("laddernote");
  if(ladderNote){
    const o = r.odds || {};
    const noLevels = !(r.targets && r.targets[0] != null);
    ladderNote.textContent = (o.t1 == null)
      ? (o.minutes === 0
           ? "Chances appear while the market is open - with no time left to the bell there is nothing to compute."
           : o.iv == null
               ? "No option chain right now, so the chances cannot be worked out."
               : noLevels
                   ? "No active signal, so there are no levels to price. The chain is fine - implied volatility is reading "
                     + o.iv + "%."
                   : "The levels could not be priced just now.")
      : `Chance of touching each level ${o.horizon || "before the 15:30 close"}, with ${o.minutes} `
        + `minutes left and implied volatility at ${o.iv}%. They do not add up to `
        + `100: a trade can touch a target, turn round and still hit the stop. `
        + `These are calibrated estimates - against 3,582 past trades the model `
        + `lands within about four points on data it was not fitted to, and it `
        + `reads a few points high on T3 and the stop. The calibration was fitted `
        + `on a VIX-derived volatility and is fed the chain's implied volatility here.`
        + (o.year_minutes === 525600 ? " On BTC none of that validation applies - it was fitted on Nifty, so read these as rough." : "");
  }

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
  let perLot = null, lots = LOTS, what = "this signal", chg = null;
  if(tk && tk.open){
    if(tk.tracked_on === "premium" && tk.entry != null && tk.stop != null && tk.lot_size){
      perLot = (tk.entry - tk.stop) * tk.lot_size;
    }
    lots = tk.lots || 1; what = "this ticket"; chg = tk.charges || null;
  } else if(r.ltp != null && r.premium_stop != null && r.lot_size
            && r.bias && r.bias !== "NEUTRAL"){
    perLot = (r.ltp - r.premium_stop) * r.lot_size;
    chg = r.charges || null;
  }
  // What a stop-out really costs: the premium lost plus the charges on both
  // orders - one flat part per trade, the rest per lot - the same figure the
  // Risk and reward panel shows. Sizing on the premium alone let the suggested
  // lots quietly exceed the risk budget by the charges.
  const flat = (chg && chg.flat != null) ? chg.flat : 0;
  const lotCost = (chg && chg.per_lot && chg.per_lot.stop != null) ? chg.per_lot.stop : 0;
  if(perLot != null && perLot > 0){
    const total = (perLot + lotCost) * lots + flat;
    let s = `Risk on ${what}: <b>${money(total,false)}</b> for ${lots} ${unit}${lots!==1?"s":""}`
          + ` (${money(perLot,false)} per ${unit}, entry to stop${chg ? ", plus charges" : ""})`;
    if(cap){
      const pct = total / cap * 100;
      const cls = pct <= rp * 1.05 ? "ok" : pct <= rp * 2 ? "warn" : "bad";
      s += ` = <b class="${cls}">${pct.toFixed(2)}% of capital</b>.`;
      if(!(tk && tk.open)){
        // In the market's own step: whole lots, or tenths of a BTC contract.
        const ch = sess.lot_choices || [1];
        const step = ch[0] < 1 ? ch[0] : 1;
        const raw = (cap * rp / 100 - flat) / (perLot + lotCost);
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
// ============================================================== journal
// Your own trades beside the tool's tickets: a year as a heatmap, a month as a
// calendar, the statistics a journal is read for, a note a day, and the
// comparison with the backtest the Review page used to make.
let JN = null, JN_SRC = "all", JN_MONTH = null, JN_DAY = null, JN_SCOPE = "month", JN_EDIT = null;
const JN_M = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const JN_MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
const jdate = iso => { const d = new Date(iso + "T00:00:00Z"); return `${d.getUTCDate()} ${JN_M[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const jiso = d => d.toISOString().slice(0, 10);
const jcol = v => v > 0 ? "var(--up)" : v < 0 ? "var(--down)" : "var(--ink-2)";
function jshort(v, bare){   // bare: no symbol - a phone calendar cell is ~45px
  const a = Math.abs(v), sg = v >= 0 ? "+" : "−";
  const body = a >= 1e5 && CCY !== "USD" ? (a / 1e5).toFixed(a >= 1e6 ? 0 : 1) + "L"
             : a >= 1e3 ? (a / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k" : String(Math.round(a));
  return sg + (bare ? "" : ccySym()) + body;
}
function jshade(v, maxAbs){
  if(!v) return "rgba(255,255,255,.06)";
  const k = 0.28 + 0.72 * Math.min(1, Math.abs(v) / (maxAbs || 1));
  return v > 0 ? `rgba(43,224,138,${k.toFixed(2)})` : `rgba(239,85,112,${k.toFixed(2)})`;
}
function jstats(lines){
  const sum = a => a.reduce((t, x) => t + x, 0);
  const n = lines.length, p = lines.map(e => e.gross);
  const wins = p.filter(x => x > 0), losses = p.filter(x => x < 0);
  let eq = 0, peak = 0, dd = 0, cw = 0, cl = 0, bw = 0, bl = 0;
  p.forEach(x => {
    eq += x; peak = Math.max(peak, eq); dd = Math.max(dd, peak - eq);
    if(x > 0){ cw++; cl = 0; } else if(x < 0){ cl++; cw = 0; } else { cw = cl = 0; }
    bw = Math.max(bw, cw); bl = Math.max(bl, cl);
  });
  const days = {};
  lines.forEach(e => { days[e.date] = (days[e.date] || 0) + e.gross; });
  const dv = Object.entries(days).sort();
  const nets = lines.filter(e => e.net != null);
  return {n, gross: sum(p), net: n && nets.length === n ? sum(nets.map(e => e.net)) : null,
    netKnown: nets.length, wins: wins.length, losses: losses.length,
    winRate: n ? 100 * wins.length / n : null,
    avgWin: wins.length ? sum(wins) / wins.length : null,
    avgLoss: losses.length ? sum(losses) / losses.length : null,
    exp: n ? sum(p) / n : null, pf: losses.length ? sum(wins) / -sum(losses) : null,
    dd, bw, bl, green: dv.filter(([, v]) => v > 0).length, red: dv.filter(([, v]) => v < 0).length,
    best: dv.length ? dv.reduce((a, b) => b[1] > a[1] ? b : a) : null,
    worst: dv.length ? dv.reduce((a, b) => b[1] < a[1] ? b : a) : null};
}
async function journalFetch(){
  try{
    const r = await fetch("/api/journal?source=" + encodeURIComponent(JN_SRC), {cache: "no-store"});
    JN = await r.json();
  }catch(e){ JN = {error: "The journal could not be read just now."}; }
  journalPaint();
}
async function jpost(fields){
  try{
    const r = await fetch("/api/journal", {method: "POST", cache: "no-store",
      headers: {"Content-Type": "application/x-www-form-urlencoded"}, body: new URLSearchParams(fields)});
    return await r.json();
  }catch(e){ return {ok: false, message: "That could not be sent."}; }
}
function journalPaint(){
  const d = JN || {};
  if(d.error){ $("jheat").innerHTML = `<p class="jmuted">${esc(d.error)}</p>`; return; }
  if(d.currency) CCY = d.currency;
  const today = d.today || jiso(new Date());
  if(!JN_MONTH) JN_MONTH = today.slice(0, 7);
  document.querySelectorAll("#jsrc [data-src]").forEach(b => b.classList.toggle("on", b.dataset.src === JN_SRC));
  document.querySelectorAll("#jscope [data-scope]").forEach(b => b.classList.toggle("on", b.dataset.scope === JN_SCOPE));
  jheatPaint(d, today); jcalPaint(d, today); jstatsPaint(d); jdayPaint(d); jriskPaint(d.risk); jreviewPaint(d.review);
}
function jheatPaint(d, today){
  const days = d.days || {}, crypto = d.market === "crypto";
  const maxAbs = Math.max(1, ...Object.values(days).map(x => Math.abs(x.gross)));
  const rows = crypto ? [0, 1, 2, 3, 4, 5, 6] : [0, 1, 2, 3, 4];
  const end = new Date(today + "T00:00:00Z"), endDow = (end.getUTCDay() + 6) % 7;
  const start = new Date(end); start.setUTCDate(end.getUTCDate() - endDow - 52 * 7);
  let cols = "";
  const spans = [];                       // [label, weeks] - a label spans its month's columns
  for(let w = 0; w < 53; w++){
    const monday = new Date(start); monday.setUTCDate(start.getUTCDate() + w * 7);
    const mo = monday.getUTCMonth();
    if(!spans.length || spans[spans.length - 1][2] !== mo) spans.push([JN_M[mo], 1, mo]);
    else spans[spans.length - 1][1]++;
    cols += `<div class="jhcol">` + rows.map(r => {
      const day = new Date(monday); day.setUTCDate(monday.getUTCDate() + r);
      const iso = jiso(day);
      if(iso > today) return `<i class="fut"></i>`;
      const v = days[iso];
      const tip = v ? `${jdate(iso)} · ${v.trades} trade${v.trades === 1 ? "" : "s"} · ${money(v.gross)}` : `${jdate(iso)} · no trades`;
      return `<i data-day="${iso}" title="${esc(tip)}" class="${iso === JN_DAY ? "sel" : ""}" style="background:${jshade(v && v.gross, maxAbs)}"></i>`;
    }).join("") + `</div>`;
  }
  const dayNames = crypto ? ["Mon", "", "Wed", "", "Fri", "", "Sun"] : ["Mon", "", "Wed", "", "Fri"];
  const months = spans.map(([label, weeks]) =>
    `<span style="width:${weeks * 16 - 3}px">${weeks >= 3 ? label : ""}</span>`).join("");
  $("jheat").innerHTML = `<div class="jhmonths">${months}</div><div class="jhgrid"><div class="jhdays">`
    + dayNames.map(n => `<span>${n}</span>`).join("") + `</div>${cols}</div>`;
  // On a narrow screen the year scrolls sideways; open it on the latest weeks.
  const wrap = document.querySelector("#jbookcard .jheatwrap");
  if(wrap) wrap.scrollLeft = wrap.scrollWidth;
  const vals = Object.values(days).map(x => x.gross);
  const lo = vals.length ? Math.min(0, ...vals) : 0, hi = vals.length ? Math.max(0, ...vals) : 0;
  const sw = v => `<i style="background:${jshade(v, maxAbs)}"></i>`;
  $("jlegend").innerHTML = `<span>${lo < 0 ? "Worst day " + money(lo) : "Loss"}</span>`
    + [-1, -0.66, -0.33].map(k => sw(k * maxAbs)).join("") + sw(0)
    + [0.33, 0.66, 1].map(k => sw(k * maxAbs)).join("")
    + `<span>${hi > 0 ? "Best day " + money(hi) : "Profit"}</span>`;
  $("jbookrange").textContent = Object.keys(days).length
    ? `${Object.keys(days).length} trading day${Object.keys(days).length === 1 ? "" : "s"} with trades` : "no trades yet";
}
function jcalPaint(d, today){
  const days = d.days || {};
  const [y, m] = JN_MONTH.split("-").map(Number);
  $("jmonth").textContent = `${JN_MONTHS[m - 1]} ${y}`;
  $("jnext").disabled = JN_MONTH >= today.slice(0, 7);
  const first = new Date(Date.UTC(y, m - 1, 1)), lead = (first.getUTCDay() + 6) % 7;
  const count = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const inMonth = Object.entries(days).filter(([k]) => k.startsWith(JN_MONTH));
  const maxAbs = Math.max(1, ...inMonth.map(([, v]) => Math.abs(v.gross)));
  let html = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(w => `<div class="wd">${w}</div>`).join("");
  for(let i = 0; i < lead; i++) html += `<div class="jd empty"></div>`;
  for(let dd = 1; dd <= count; dd++){
    const iso = `${JN_MONTH}-${String(dd).padStart(2, "0")}`, v = days[iso];
    const cls = ["jd", iso === today ? "today" : "", iso === JN_DAY ? "sel" : ""].join(" ");
    const note = d.notes && d.notes[iso] ? " ✎" : "";
    html += `<div class="${cls}" data-day="${iso}" style="${v ? `background:${jshade(v.gross, maxAbs)}` : ""}">`
      + `<span class="n">${dd}${note}</span>`
      + (v ? `<span class="v">${jshort(v.gross, innerWidth <= 600)}</span><span class="c">${v.trades} trade${v.trades === 1 ? "" : "s"}</span>` : "")
      + `</div>`;
  }
  $("jcal").innerHTML = html;
}
function jstatsPaint(d){
  const all = d.entries || [];
  const lines = JN_SCOPE === "month" ? all.filter(e => e.date.startsWith(JN_MONTH)) : all;
  const [y, m] = JN_MONTH.split("-").map(Number);
  $("jstatscope").textContent = JN_SCOPE === "month" ? `${JN_MONTHS[m - 1]} ${y}` : "all time";
  const s = jstats(lines), box = $("jstats");
  if(!s.n){ box.innerHTML = `<p class="jmuted">No trades ${JN_SCOPE === "month" ? "this month" : "yet"}. Add one, or they appear here as the tool's tickets close.</p>`; return; }
  const pct = v => v == null ? "—" : v.toFixed(0) + "%";
  box.innerHTML =
      statRow("Trades", `${s.n} &middot; ${s.wins} won, ${s.losses} lost`)
    + statRow("P&L before costs", money(s.gross), jcol(s.gross))
    + statRow("After charges", s.net != null ? money(s.net)
        : s.netKnown ? `charges known for ${s.netKnown} of ${s.n}` : "—", s.net != null ? jcol(s.net) : "")
    + statRow("Win rate", pct(s.winRate))
    + statRow("Profit factor", s.pf == null ? (s.wins ? "no losses" : "—") : s.pf.toFixed(2))
    + statRow("Average win / loss", `${s.avgWin == null ? "—" : money(s.avgWin)} / ${s.avgLoss == null ? "—" : money(s.avgLoss)}`)
    + statRow("Per trade", money(s.exp), jcol(s.exp))
    + statRow("Best day", s.best ? `${money(s.best[1])} &middot; ${jdate(s.best[0])}` : "—", s.best ? jcol(s.best[1]) : "")
    + statRow("Worst day", s.worst ? `${money(s.worst[1])} &middot; ${jdate(s.worst[0])}` : "—", s.worst ? jcol(s.worst[1]) : "")
    + statRow("Green / red days", `${s.green} / ${s.red}`)
    + statRow("Deepest drawdown", s.dd ? money(-s.dd) : "none", s.dd ? "var(--down)" : "")
    + statRow("Longest streak", `${s.bw} won &middot; ${s.bl} lost`);
}
function jdayPaint(d){
  const card = $("jdaycard");
  if(!JN_DAY){ card.style.display = "none"; return; }
  card.style.display = "";
  const lines = (d.entries || []).filter(e => e.date === JN_DAY);
  const tot = lines.reduce((t, e) => t + e.gross, 0);
  $("jdaytitle").textContent = `${jdate(JN_DAY)} · ` + (lines.length ? `${lines.length} trade${lines.length === 1 ? "" : "s"} · ${money(tot)}` : "no trades");
  const dp = v => v == null ? "—" : num(v, 2);
  $("jdaytbl").innerHTML = lines.length
    ? `<thead><tr><th>Source</th><th>Time</th><th>Contract</th><th>Lots</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Charges</th><th>After</th><th>Note</th><th></th></tr></thead><tbody>`
      + lines.map(e => `<tr><td>${e.source === "mine" ? '<span class="jbadge mine">You</span>' : '<span class="jbadge">Tool</span>'}</td>`
        + `<td>${esc(e.time || "")}</td>`
        + `<td class="sym">${esc(e.instrument || "")} ${e.strike != null ? esc(String(e.strike)) : ""} ${esc(e.side || "")}${e.dir === "sell" ? " sold" : ""}</td>`
        + `<td>${num(e.lots, e.lots % 1 ? 2 : 0)}</td><td>${dp(e.entry)}</td><td>${dp(e.exit)}</td>`
        + `<td style="color:${jcol(e.gross)}">${money(e.gross)}</td>`
        + `<td>${e.charges == null ? "—" : money(e.charges, false) + (e.charges_estimated ? " est." : "")}</td>`
        + `<td style="color:${e.net == null ? "" : jcol(e.net)}">${e.net == null ? "—" : money(e.net)}</td>`
        + `<td class="jnote">${esc(e.source === "mine" ? (e.notes || "") : (e.status || "").replace(/^CLOSED\s*[—-]\s*/, ""))}</td>`
        + `<td>${e.source === "mine" ? `<button class="lbtn" type="button" data-jedit="${esc(e.id)}">Edit</button> <button class="lbtn" type="button" data-jdel="${esc(e.id)}">Delete</button>` : ""}</td></tr>`).join("")
      + `</tbody>`
    : `<tbody><tr><td class="jmuted" style="text-align:left">Nothing traded this day.</td></tr></tbody>`;
  const note = $("jdaynote");
  if(document.activeElement !== note) note.value = (d.notes || {})[JN_DAY] || "";
}
// How rough ordinary bad luck gets on this record: the bad day, and the next
// hundred trades re-drawn from the ones already taken.
function jriskPaint(r){
  const box = $("jrisk"), note = $("jrisknote");
  if(!box) return;
  if(!r || !r.enough){
    box.innerHTML = `<p class="jmuted">Needs at least ${(r && r.min_trades) || 20} trades to estimate `
      + `anything - ${(r && r.trades) || 0} so far. A risk figure from a handful of trades is noise.</p>`;
    note.textContent = ""; return;
  }
  const mc = r.monte_carlo || {};
  const ratio = v => v == null ? "\u2014" : v.toFixed(2);
  box.innerHTML =
      statRow("Sharpe ratio", ratio(r.sharpe), r.sharpe == null ? "" : jcol(r.sharpe))
    + statRow("Sortino ratio", ratio(r.sortino), r.sortino == null ? "" : jcol(r.sortino))
    + statRow("1 day in 20 loses at least", money(r.var95_day), jcol(r.var95_day))
    + statRow("Those worst days average", money(r.es95_day), jcol(r.es95_day))
    + statRow(`Next ${mc.trades} trades, likely range`, `${money(mc.total_p5)} to ${money(mc.total_p95)}`)
    + statRow("Middle outcome", money(mc.total_median), jcol(mc.total_median))
    + statRow("Chance of ending down", `${mc.chance_down}%`, mc.chance_down > 50 ? "var(--down)" : "")
    + statRow("Drawdown along the way", `${money(-mc.drawdown_median)} typical &middot; ${money(-mc.drawdown_p95)} bad case`, "var(--down)");
  note.textContent = `From ${r.trades} trades over ${r.days} trading days, before costs. Sharpe and Sortino are the `
    + `average day against its swings (Sortino counts only the losing swings), scaled to a year. The range re-draws `
    + `your next ${mc.trades} trades at random from the ${r.trades} you have taken, ${mc.runs} times; it assumes the `
    + `future looks like your past, which is the most it can assume. Size so that the bad-case drawdown is one you `
    + `would keep trading through.`;
}

function jreviewPaint(rv){
  const box = $("jreview");
  if(!box) return;
  if(!rv || !rv.all){
    box.innerHTML = `<p class="jmuted">No finished tool tickets with a money figure yet, so there is nothing to set against the backtest.</p>`;
    return;
  }
  const s = rv.all, b = rv.bench && rv.bench.all;
  const pf = v => v == null ? "—" : Number(v).toFixed(2);
  const vc = {ok: "var(--up)", warn: "var(--warn)", bad: "var(--down)"}[rv.verdict && rv.verdict.kind] || "var(--ink-2)";
  let html = `<div class="rrsum" style="color:${vc}">${esc((rv.verdict || {}).text || "")}</div>`
    + `<div class="scrwrap"><table class="scr"><thead><tr><th></th><th>Trades</th><th>Won</th><th>Per lot</th><th>Profit factor</th></tr></thead><tbody>`
    + `<tr><td class="sym">The tool&rsquo;s tickets</td><td>${s.n}</td><td>${Math.round(s.win)}%</td><td style="color:${jcol(s.avg)}">${money(s.avg)}</td><td>${pf(s.pf)}</td></tr>`
    + (b ? `<tr><td class="sym">Backtest, held-out year</td><td>${b.n}</td><td>${Math.round(b.win)}%</td><td>${money(b.avg)}</td><td>${pf(b.pf)}</td></tr>` : "")
    + `</tbody></table></div>`;
  if(s.lo != null && s.hi != null){
    html += `<div class="gnote">With ${s.n} trades the true average per lot could plausibly sit anywhere from ${money(s.lo)} to ${money(s.hi)}. Under ${rv.min_sample} trades any result is mostly luck.</div>`;
  }
  for(const [title, rows] of Object.entries(rv.groups || {})){
    const good = rows.filter(r => r[1]);
    if(!good.length) continue;
    html += `<p class="eyebrow" style="margin-top:14px">${esc(title)}</p><div class="scrwrap"><table class="scr"><thead><tr><th></th><th>Trades</th><th>Won</th><th>Per lot</th><th>Total</th></tr></thead><tbody>`
      + good.map(([k, v]) => `<tr><td class="sym">${esc(k)}</td><td>${v.n}</td><td>${Math.round(v.win)}%</td><td style="color:${jcol(v.avg)}">${money(v.avg)}</td><td style="color:${jcol(v.total)}">${money(v.total)}</td></tr>`).join("")
      + `</tbody></table></div>`;
  }
  box.innerHTML = html;
}
function jformOpen(trade){
  const d = JN || {}, card = $("jaddcard");
  JN_EDIT = trade ? trade.id : null;
  $("jaddtitle").textContent = trade ? "Edit a trade" : "Add a trade";
  const insts = (d.instruments || []).concat(["OTHER"]);
  $("jf_inst").innerHTML = insts.map(i => `<option value="${esc(i)}">${esc(i === "OTHER" ? "Other" : i)}</option>`).join("");
  const t = trade || {date: JN_DAY || d.today, time: "", instrument: insts[0], side: "CE", dir: "buy", lots: 1};
  const set = (id, v) => { $(id).value = v == null ? "" : v; };
  set("jf_date", t.date); set("jf_time", t.time); set("jf_inst", t.instrument); set("jf_side", t.side);
  set("jf_dir", t.dir || "buy"); set("jf_strike", t.strike); set("jf_lots", t.lots);
  set("jf_lot", trade ? t.lot_size : ""); set("jf_entry", t.entry); set("jf_exit", t.exit);
  set("jf_charges", trade && !t.charges_estimated ? t.charges : ""); set("jf_notes", t.notes);
  $("jf_lot").placeholder = String((d.lot_sizes || {})[$("jf_inst").value] || "");
  $("jf_date").max = d.today || "";
  $("jf_msg").textContent = "";
  card.style.display = "";
  card.scrollIntoView({block: "nearest", behavior: "smooth"});
}
document.addEventListener("change", e => {
  if(e.target.id === "jf_inst") $("jf_lot").placeholder = String(((JN || {}).lot_sizes || {})[e.target.value] || "");
});
document.addEventListener("click", async e => {
  if(!e.target.closest('[data-pane="journal"]')) return;
  const src = e.target.closest("#jsrc [data-src]");
  if(src){ JN_SRC = src.dataset.src; journalFetch(); return; }
  const sc = e.target.closest("#jscope [data-scope]");
  if(sc){ JN_SCOPE = sc.dataset.scope; journalPaint(); return; }
  const day = e.target.closest("[data-day]");
  if(day){ JN_DAY = day.dataset.day; JN_MONTH = JN_DAY.slice(0, 7); journalPaint();
           $("jdaycard").scrollIntoView({block: "nearest", behavior: "smooth"}); return; }
  if(e.target.closest("#jprev") || e.target.closest("#jnext")){
    const [y, m] = JN_MONTH.split("-").map(Number);
    const t = new Date(Date.UTC(y, m - 1 + (e.target.closest("#jnext") ? 1 : -1), 1));
    JN_MONTH = jiso(t).slice(0, 7); journalPaint(); return;
  }
  if(e.target.closest("#jaddbtn")){ jformOpen(null); return; }
  if(e.target.closest("#jf_cancel")){ $("jaddcard").style.display = "none"; JN_EDIT = null; return; }
  const ed = e.target.closest("[data-jedit]");
  if(ed){ jformOpen(((JN || {}).entries || []).find(x => x.source === "mine" && x.id === ed.dataset.jedit)); return; }
  const del = e.target.closest("[data-jdel]");
  if(del){
    if(!confirm("Delete this trade from your journal? This cannot be undone.")) return;
    const r = await jpost({action: "delete", id: del.dataset.jdel});
    $("jnotemsg").textContent = r.message || ""; $("jnotemsg").style.color = r.ok ? "var(--up)" : "var(--down)";
    if(r.ok) journalFetch();
    return;
  }
  if(e.target.closest("#jf_save")){
    const f = {action: JN_EDIT ? "update" : "add"};
    if(JN_EDIT) f.id = JN_EDIT;
    [["date","jf_date"],["time","jf_time"],["instrument","jf_inst"],["side","jf_side"],["dir","jf_dir"],["strike","jf_strike"],
     ["lots","jf_lots"],["lot_size","jf_lot"],["entry","jf_entry"],["exit","jf_exit"],["charges","jf_charges"],["notes","jf_notes"]]
      .forEach(([k, id]) => { f[k] = $(id).value; });
    const r = await jpost(f);
    $("jf_msg").textContent = r.message || ""; $("jf_msg").style.color = r.ok ? "var(--up)" : "var(--down)";
    if(r.ok){
      JN_DAY = f.date; JN_MONTH = f.date.slice(0, 7); JN_EDIT = null;
      setTimeout(() => { $("jaddcard").style.display = "none"; }, 900);
      journalFetch();
    }
    return;
  }
  if(e.target.closest("#jnotesave") && JN_DAY){
    const r = await jpost({action: "note", date: JN_DAY, text: $("jdaynote").value});
    $("jnotemsg").textContent = r.message || ""; $("jnotemsg").style.color = r.ok ? "var(--up)" : "var(--down)";
    if(r.ok){ if(JN){ JN.notes = JN.notes || {}; const v = $("jdaynote").value.trim(); if(v) JN.notes[JN_DAY] = v; else delete JN.notes[JN_DAY]; } jcalPaint(JN, JN.today); }
  }
});

// ============================================================= screener
// A screen is conditions over the index member stocks, built from fixed lists
// the server hands out; the server checks every field again before running it.
let SC = null, SC_ROWS = [];
const SC_TF = {daily: "Daily", weekly: "Weekly", monthly: "Monthly"};
const scInd = name => ((SC && SC.indicators) || []).find(x => x.name === name) || {label: name, params: []};
function scDefault(name, rhs){
  const params = {};
  scInd(name).params.forEach(p => { params[p.key] = p.default; });
  const o = {type: "indicator", name, tf: "daily", params, offset: 0};
  if(rhs){ o.mult = 1; o.add = 0; }
  return o;
}
async function scFetch(){
  if(SC && !SC.note) return;
  try{ SC = await (await fetch("/api/customscreen", {cache: "no-store"})).json(); }
  catch(e){ SC = {note: "The screener could not be loaded just now."}; }
  if(SC.note){ $("scconds").innerHTML = `<p class="jmuted">${esc(SC.note)}</p>`; return; }
  scPickPaint(); scLoad(SC.presets[0], false);
}
function scPickPaint(){
  $("scpick").innerHTML = `<optgroup label="Presets">` + SC.presets.map((s, i) => `<option value="p${i}">${esc(s.name)}</option>`).join("") + `</optgroup>`
    + (SC.saved.length ? `<optgroup label="Your saved screens">` + SC.saved.map((s, i) => `<option value="s${i}">${esc(s.name)}</option>`).join("") + `</optgroup>` : "");
}
function scLoad(s, isSaved){
  SC_ROWS = JSON.parse(JSON.stringify(s.conditions));
  $("scname").value = isSaved ? s.name : "";
  $("scname").placeholder = isSaved ? "Name this screen" : `${s.name} - name it to save your own`;
  $("scdel").style.display = isSaved ? "" : "none";
  $("scdel").dataset.name = isSaved ? s.name : "";
  scRowsPaint();
}
function scOperand(o, i, side){
  const tf = `<select data-f="${side}.tf" data-i="${i}" aria-label="Timeframe">` + SC.timeframes.map(t => `<option value="${t}"${o.tf === t ? " selected" : ""}>${SC_TF[t]}</option>`).join("") + `</select>`;
  const nm = `<select data-f="${side}.name" data-i="${i}" aria-label="Indicator">` + SC.indicators.map(x => `<option value="${x.name}"${o.name === x.name ? " selected" : ""}>${esc(x.label)}</option>`).join("") + `</select>`;
  const ps = scInd(o.name).params.map(p => `<input class="num" type="number" step="any" min="${p.lo}" max="${p.hi}" title="${esc(p.key)}" aria-label="${esc(p.key)}" data-f="${side}.params.${p.key}" data-i="${i}" value="${o.params[p.key] ?? p.default}">`).join("");
  const off = `<span class="sep">bars ago</span><input class="num" type="number" min="0" max="${SC.max_offset}" aria-label="Bars ago" data-f="${side}.offset" data-i="${i}" value="${o.offset || 0}">`;
  const extra = side === "rhs" ? `<span class="sep">&times;</span><input class="num" type="number" step="any" aria-label="Multiplier" data-f="rhs.mult" data-i="${i}" value="${o.mult ?? 1}"><span class="sep">+</span><input class="num" type="number" step="any" aria-label="Added amount" data-f="rhs.add" data-i="${i}" value="${o.add ?? 0}">` : "";
  return tf + nm + (ps ? `<span class="sep">(</span>${ps}<span class="sep">)</span>` : "") + off + extra;
}
function scRowsPaint(){
  $("scconds").innerHTML = SC_ROWS.length ? SC_ROWS.map((c, i) => {
    const isVal = c.rhs.type === "value";
    return `<div class="sccond">${scOperand(c.lhs, i, "lhs")}`
      + `<select class="op" data-f="op" data-i="${i}" aria-label="Comparison">` + SC.operators.map(o => `<option${c.op === o ? " selected" : ""}>${o}</option>`).join("") + `</select>`
      + `<select data-f="rhs.type" data-i="${i}" aria-label="Compare with"><option value="value"${isVal ? " selected" : ""}>a number</option><option value="indicator"${isVal ? "" : " selected"}>an indicator</option></select>`
      + (isVal ? `<input class="num" type="number" step="any" aria-label="Number" data-f="rhs.value" data-i="${i}" value="${c.rhs.value}">` : scOperand(c.rhs, i, "rhs"))
      + `<button class="lbtn scx" type="button" data-scx="${i}" aria-label="Remove this condition">&times;</button></div>`;
  }).join("") : `<p class="jmuted">No conditions yet - add one.</p>`;
  $("scadd").disabled = SC_ROWS.length >= SC.max_conditions;
}
function scText(o){
  if(o.type === "value") return num(o.value, 2);
  const m = scInd(o.name), ps = m.params.map(p => o.params[p.key]).join(",");
  let t = (o.tf !== "daily" ? SC_TF[o.tf].toLowerCase() + " " : "") + m.label + (ps ? `(${ps})` : "");
  if(o.offset) t += ` ${o.offset} bar${o.offset === 1 ? "" : "s"} ago`;
  if(o.mult != null && Number(o.mult) !== 1) t += ` × ${o.mult}`;
  if(o.add) t += ` ${o.add > 0 ? "+" : "−"} ${Math.abs(o.add)}`;
  return t;
}
async function scPost(fields){
  try{
    const r = await fetch("/api/customscreen", {method: "POST", cache: "no-store",
      headers: {"Content-Type": "application/x-www-form-urlencoded"}, body: new URLSearchParams(fields)});
    return await r.json();
  }catch(e){ return {ok: false, message: "That could not be sent."}; }
}
function scMsg(text, ok){ $("scmsg").textContent = text || ""; $("scmsg").style.color = ok ? "var(--up)" : "var(--down)"; }
function scStrategy(){
  const typed = $("scname").value.trim();
  return {name: typed || $("scname").placeholder.replace(/ - name it to save your own$/, ""), conditions: SC_ROWS};
}
// Volumes run to lakhs; two decimals on them are noise.
const scNum = v => v == null ? "\u2014" : num(v, Math.abs(v) >= 1000 ? 0 : 2);
function scResults(r, at){
  const st = r.strategy, rows = r.matches;
  $("screshead").textContent = `${st.name} · ${rows.length} of ${r.checked} stocks match · ${at} IST`;
  const heads = st.conditions.map(c => `<th title="${esc(scText(c.lhs) + " " + c.op + " " + scText(c.rhs))}">${esc(scText(c.lhs))} ${esc(c.op)} ${esc(scText(c.rhs))}</th>`).join("");
  $("scres").innerHTML = rows.length
    ? `<thead><tr><th>Stock</th><th>Index</th><th>Sector</th><th>Close</th><th>Day</th>${heads}</tr></thead><tbody>`
      + rows.map(m => `<tr><td class="sym">${esc(m.sym)}</td><td>${esc((m.indices || []).join(", "))}</td><td>${esc(m.sector || "")}</td>`
        + `<td>${num(m.close, 2)}</td><td style="color:${jcol(m.pct || 0)}">${m.pct == null ? "—" : (m.pct > 0 ? "+" : "") + m.pct.toFixed(2) + "%"}</td>`
        + m.values.map(v => `<td class="scv"><b>${scNum(v.lhs)}</b> vs ${scNum(v.rhs)}</td>`).join("") + `</tr>`).join("") + `</tbody>`
    : `<tbody><tr><td class="jmuted" style="text-align:left">No stock matches every condition right now.</td></tr></tbody>`;
  const nh = (r.not_enough_history || []).length;
  if(nh) $("scmsg").textContent += ` ${nh} had too little history for a condition and were left out.`;
}
document.addEventListener("change", e => {
  const el = e.target.closest("#scconds [data-f]");
  if(!el) return;
  const c = SC_ROWS[Number(el.dataset.i)], path = el.dataset.f.split(".");
  if(path[0] === "op"){ c.op = el.value; return; }
  if(path[0] === "rhs" && path[1] === "type"){ c.rhs = el.value === "value" ? {type: "value", value: 0} : scDefault("close", true); scRowsPaint(); return; }
  const side = c[path[0]];
  if(path[1] === "name"){
    const fresh = scDefault(el.value, path[0] === "rhs");
    fresh.tf = side.tf; fresh.offset = side.offset;
    if(path[0] === "rhs"){ fresh.mult = side.mult ?? 1; fresh.add = side.add ?? 0; }
    c[path[0]] = fresh; scRowsPaint(); return;
  }
  if(path[1] === "params"){ side.params[path[2]] = Number(el.value); return; }
  side[path[1]] = path[1] === "tf" ? el.value : Number(el.value);
});
document.addEventListener("click", async e => {
  if(!e.target.closest('[data-pane="screener"]') || !SC || SC.note) return;
  if(e.target.closest("#scload")){
    const v = $("scpick").value, list = v[0] === "p" ? SC.presets : SC.saved, s = list[Number(v.slice(1))];
    if(s){ scLoad(s, v[0] === "s"); scMsg(""); }
    return;
  }
  if(e.target.closest("#scadd")){
    SC_ROWS.push({lhs: scDefault("rsi", false), op: ">", rhs: {type: "value", value: 50}});
    scRowsPaint(); return;
  }
  const x = e.target.closest("[data-scx]");
  if(x){ SC_ROWS.splice(Number(x.dataset.scx), 1); scRowsPaint(); return; }
  if(e.target.closest("#scrun")){
    const b = $("scrun"); b.disabled = true; scMsg("Running over the member stocks…", true);
    const r = await scPost({action: "run", strategy: JSON.stringify(scStrategy())});
    b.disabled = false; scMsg(r.message, r.ok);
    if(r.ok) scResults(r.result, r.at);
    return;
  }
  if(e.target.closest("#scsave")){
    if(!$("scname").value.trim()){ scMsg("Give the screen a name first.", false); $("scname").focus(); return; }
    const r = await scPost({action: "save", strategy: JSON.stringify(scStrategy())});
    scMsg(r.message, r.ok);
    if(r.ok){ SC.saved = r.saved; scPickPaint(); const i = SC.saved.findIndex(s => s.name === r.name);
      if(i >= 0){ $("scpick").value = "s" + i; scLoad(SC.saved[i], true); scMsg(r.message, true); } }
    return;
  }
  if(e.target.closest("#scdel")){
    const name = $("scdel").dataset.name;
    if(!name || !confirm(`Delete the saved screen “${name}”?`)) return;
    const r = await scPost({action: "delete", name});
    scMsg(r.message, r.ok);
    if(r.ok){ SC.saved = r.saved; scPickPaint(); scLoad(SC.presets[0], false); scMsg(r.message, true); }
  }
});

// --------------------------------------------------------- risk & reward
// How the trade on screen actually pays: what the stop costs, what each target
// makes, how many times the risk that is, and the win rate it needs to break
// even - in rupees after Zerodha's charges when there is a live premium, in
// index points when there is not. The open ticket's frozen levels when one is
// running, the live signal's otherwise.
function rrBox(r, tk){
  const el = $("rr");
  if(!el) return;
  const open = !!(tk && tk.open);
  const live = !open && !!r && !!r.bias && r.bias !== "NEUTRAL";
  if(!open && !live){ el.innerHTML = ""; return; }
  let prem, entry, tg, stop, lotSize, lots, exitAt, odds, ch;
  if(open){
    prem = tk.tracked_on === "premium";
    entry = tk.entry; tg = tk.targets || []; stop = tk.stop;
    lotSize = tk.lot_size; lots = tk.lots || 1; exitAt = tk.exit_at;
    odds = tk.odds || {}; ch = tk.charges || null;
  } else {
    prem = r.ltp != null && r.premium_stop != null && (r.premium_targets || []).some(v => v != null);
    entry = prem ? r.ltp : r.spot;
    tg = (prem ? r.premium_targets : r.targets) || [];
    stop = prem ? r.premium_stop : r.stop;
    lotSize = r.lot_size; lots = LOTS; exitAt = r.exit_at;
    odds = r.odds || {}; ch = r.charges || null;
  }
  exitAt = String(exitAt || "T2").toUpperCase();
  const risk = (entry == null || stop == null) ? 0 : Math.abs(entry - stop);
  if(!(risk > 0)){ el.innerHTML = ""; return; }

  const dp = prem ? 2 : 0, pts = prem ? "" : " pts";
  const unit = (lotSize || 1) > 1 ? "lot" : "contract";
  const size = `${lots} ${unit}${lots !== 1 ? "s" : ""}`;
  const qty = (prem && lotSize) ? lotSize * lots : 0;          // money only on the premium
  const cost = key => (ch && ch.per_lot && ch.per_lot[key] != null) ? ch.flat + ch.per_lot[key] * lots : 0;
  const loss = qty ? risk * qty + cost("stop") : null;
  const rows = [["T1", tg[0]], ["T2", tg[1]], ["T3", tg[2]]].filter(([, v]) => v != null).map(([k, v]) => {
    const move = Math.abs(v - entry), R = move / risk;
    const gain = qty ? move * qty - cost(k.toLowerCase()) : null;
    const be = qty ? (gain > 0 ? loss / (loss + gain) * 100 : null) : 100 / (1 + R);
    return {k, v, move, R, gain, be, od: odds[k.toLowerCase()]};
  });
  const pc = v => v == null ? "" : `${v}%`;
  const lbl = k => k === exitAt ? `${k} · exit` : k === "T3" ? "T3 · room check" : k;

  const head = `<thead><tr><th>Level</th><th>Price</th><th>From entry</th><th>× risk</th>`
    + (qty ? `<th>${esc(size)}${ch ? ", after charges" : ", before fees"}</th>` : "")
    + `<th>Break-even win rate</th><th>Chance</th></tr></thead>`;
  const body = rows.map(x => `<tr${x.k === exitAt ? ' class="exit"' : ""}><td class="sym">${lbl(x.k)}</td>`
      + `<td>${num(x.v, dp)}</td><td style="color:var(--up)">+${num(x.move, dp)}${pts}</td>`
      + `<td>${x.R.toFixed(2)}R</td>`
      + (qty ? `<td style="color:${x.gain > 0 ? "var(--up)" : "var(--down)"}">${money(x.gain)}</td>` : "")
      + `<td>${x.be == null ? "never - charges exceed it" : x.be.toFixed(0) + "%"}</td>`
      + `<td>${pc(x.od)}</td></tr>`).join("")
    + `<tr><td class="sym">Stop</td><td>${num(stop, dp)}</td>`
    + `<td style="color:var(--down)">&minus;${num(risk, dp)}${pts}</td><td>&minus;1.00R</td>`
    + (qty ? `<td style="color:var(--down)">${money(-loss)}</td>` : "")
    + `<td>&mdash;</td><td>${pc(odds.stop)}</td></tr>`;

  const x = rows.find(v => v.k === exitAt);
  const who = open ? "This ticket" : "This trade";
  let sum = "";
  if(x && qty && !(x.gain > 0)){
    sum = `${who} exits at <b>${x.k}</b>, but after charges the move from ${num(entry, dp)} to `
        + `${num(x.v, dp)} does not pay for itself at ${esc(size)}: it loses <b>${money(x.gain, false)}</b> even when it works.`;
  } else if(x){
    const ratio = qty ? x.gain / loss : x.R;
    const riskTxt = qty ? money(loss, false) : num(risk, dp) + pts;
    const gainTxt = qty ? money(x.gain, false) : num(x.move, dp) + pts;
    sum = `${who} exits at <b>${x.k}</b>. You risk <b>${riskTxt}</b> - from ${num(entry, dp)} down to the stop at `
        + `${num(stop, dp)}${ch ? ", charges included" : ""} - to make <b>${gainTxt}</b> at ${num(x.v, dp)}. `
        + `That is <b>${ratio.toFixed(2)} to 1</b>: each loss takes ${(1 / ratio).toFixed(2)} wins to earn back, `
        + `so it comes out ahead only if it wins more than <b>${x.be.toFixed(0)}%</b> of the time.`;
    if(x.od != null && odds.stop != null){
      sum += ` The model gives ${x.k} a ${x.od}% chance of being reached ${esc(odds.horizon || "")} and the stop ${odds.stop}%. `
           + `Those overlap - a trade can touch both - so set them loosely against the ${x.be.toFixed(0)}% it needs; they are not a win rate.`;
    }
  }
  const notes = [];
  if(!prem) notes.push("In index points - there is no live option price for this strike, so no rupee figures.");
  else if(ch) notes.push(`After Zerodha's charges at ${esc(size)}: brokerage of ₹20 an order, STT on the sell side, `
                       + `exchange, SEBI and stamp charges, and GST. Slippage is not included - a wide spread costs more.`);
  else if(CCY === "USD") notes.push("Deribit's trading fees are not included.");
  else notes.push("Charges could not be worked out, so these are before costs.");
  notes.push(`"× risk" is how far each level is from entry compared with the stop. The Reward : risk tile above is `
           + `a different number: how far the market has room to run against the stop - the check that decides `
           + `whether a trade is issued at all - not what the exit pays.`);

  el.innerHTML = `<p class="eyebrow">Risk and reward on this ${open ? "ticket" : "trade"}</p>`
    + (sum ? `<div class="rrsum">${sum}</div>` : "")
    + `<div class="scrwrap"><table class="scr rrtbl">${head}<tbody>${body}</tbody></table></div>`
    + `<div class="gnote">${notes.join(" ")}</div>`;
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

$("tskip").onclick = () => {
  if(!confirm(`Skip the cooldown on ${CUR}?\n\n`
            + "Only the waiting time is lifted, once. The ticket is still issued by "
            + "the rules on the next reading - confirmed direction, room to the "
            + "targets, reward to risk, spread, one position at a time - so it "
            + "may still not come if one of those is not met.\n\n"
            + "The cooldown is there so a stop-out is not bought straight back. "
            + "A ticket issued this way is marked in your trade log.")) return;
  const b = $("tskip"); b.disabled = true;
  fetch("/api/ticket", {method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body:new URLSearchParams({action:"skip_cooldown", index:CUR})})
    .then(x => x.json())
    .then(j => {
      if(j && !j.ok && j.message){ const w = $("twhy"); w.style.display = ""; w.textContent = j.message; }
      return tick();
    })
    .catch(()=>{})
    .finally(() => { b.disabled = false; });
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
  // Counted in India time, where the contract expires. In the browser's own
  // zone a laptop or phone set elsewhere read "1 day left" beside an
  // "Expires today" note on the morning of expiry.
  const ist = new Date(Date.now() + 330 * 60000);
  const today = new Date(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate());
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
  // Offered only while a cooldown is what is holding the ticket - not for a
  // missing room, a spread or the daily brake, which it could not lift anyway.
  const cooling = !open && !!wait && (wait.code === "reentry_cooldown" || wait.code === "ticket_gap");
  $("tskip").style.display = cooling ? "" : "none";

  const c = $("tcontract");
  if(open){
    c.style.display = "";
    const ex = expiryText(tk.expiry);
    c.innerHTML = `<b>${esc(tk.index)} ${esc(String(tk.strike))} ${esc(tk.option_type)}</b>`
                + (ex ? ` · expiry <b>${esc(ex)}</b>` : "")
                + ` · tracked on ${tk.tracked_on === "premium" ? "live premium" : "the index"}`;
    $("tissued").style.display = "";
    $("tissued").textContent = `Issued ${tk.entry_time} IST · levels frozen at entry`
      + (tk.cooldown_skipped ? " · cooldown skipped by you" : "");
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
  // The line needs the chart's candles, which load when the Chart tab is
  // opened - so on the Signal tab the card read "—" all day. The number does
  // not need them: the server already sends the day's change with every
  // reading. Shown without the line until the candles arrive, and called the
  // last session once the market has shut, because that is what it is.
  const fromTrend = () => {
    const tr = ((LAST && LAST.indices && LAST.indices[CUR]) || {}).trend || {};
    if(tr.day_change == null){ blank(); return; }
    const up = tr.day_change >= 0, col = up ? css("--up") : css("--down");
    const f = v => Math.abs(v).toLocaleString("en-IN", {maximumFractionDigits: 2});
    $("dmv").textContent = (up ? "+" : "−") + f(tr.day_change); $("dmv").style.color = col;
    $("dmp").textContent = tr.day_change_pct == null ? ""
      : "(" + (up ? "+" : "−") + Math.abs(tr.day_change_pct).toFixed(2) + "%)";
    $("dmp").style.color = col;
    $("dmlo").textContent = (LAST && LAST.market_open) ? "" : "last session";
    $("dmhi").textContent = "";
  };
  if(!bars.length){ fromTrend(); return; }
  const lastDay = new Date(bars[bars.length-1][0]*1000).toDateString();
  let today = bars.filter(b => new Date(b[0]*1000).toDateString() === lastDay);
  if(today.length < 2){ fromTrend(); return; }

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
try{ CH.tf = localStorage.getItem("nbs.tf.v1") || "15m"; }catch(e){ CH.tf = "15m"; }
const PAD = {l:0, r:64, t:10, b:24};

const cv = $("cv"), cx = cv.getContext("2d");

// The signal is always computed on the 15-minute series; these are views of
// the same market, and the levels drawn on them are the signal's own.
const TF_LABEL = {"5m": "5-minute candles", "15m": "15-minute candles", "1d": "daily candles"};
function chartTF(tf){
  if(!TF_LABEL[tf] || tf === CH.tf) return;
  CH.tf = tf;
  try{ localStorage.setItem("nbs.tf.v1", tf); }catch(e){}
  document.querySelectorAll(".lbtn.tf").forEach(b => b.classList.toggle("on", b.dataset.tf === tf));
  $("tflabel").textContent = TF_LABEL[tf];
  CH.key = null;                       // force a refetch at the new timeframe
  CH.pinned = true;
  chartWant(CUR);
}
function chartWant(key){
  const stale = Date.now() - CH.at > 30000;
  if(key === CH.key && !stale){ chartDraw(); return; }
  const first = key !== CH.key;
  CH.key = key;
  CH.at = Date.now();
  fetch("/api/candles/" + encodeURIComponent(key) + "?tf=" + encodeURIComponent(CH.tf || "15m"),
        {cache:"no-store"})
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

// Text that can be read on whatever the tag behind it is painted with: black or
// white, whichever contrasts more (WCAG relative luminance). The crosshair tag
// is painted with --ink, which is nearly white, so the white price on it was
// invisible until 16 Sep 2026; the bright mint --up of the dark theme was close
// behind. Anything unparseable falls back to white, as before.
function onColour(bg){
  let r, g, b;
  const s = (bg || "").trim();
  if(s[0] === "#"){
    const hx = s.length === 4 ? s.slice(1).split("").map(c => c + c).join("") : s.slice(1, 7);
    if(hx.length < 6) return "#fff";
    r = parseInt(hx.slice(0,2),16); g = parseInt(hx.slice(2,4),16); b = parseInt(hx.slice(4,6),16);
  } else {
    const m = s.match(/[\d.]+/g);
    if(!m || m.length < 3) return "#fff";
    r = +m[0]; g = +m[1]; b = +m[2];
  }
  if([r,g,b].some(v => !(v >= 0 && v <= 255))) return "#fff";
  const lin = v => { v /= 255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); };
  const L = 0.2126*lin(r) + 0.7152*lin(g) + 0.0722*lin(b);
  const onWhite = 1.05 / (L + 0.05), onBlack = (L + 0.05) / 0.05;
  return onBlack >= onWhite ? "#0a0d14" : "#fff";
}

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
    cx.fillStyle = onColour(a.colour); cx.font = "10px -apple-system,sans-serif";
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
    const lastBg = last[4] >= last[1] ? C.up : C.down;
    cx.fillStyle = lastBg;
    cx.fillRect(w-PAD.r, y-9, PAD.r, 18);
    cx.fillStyle = onColour(lastBg); cx.font = "600 11px -apple-system,sans-serif";
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
    cx.fillStyle = onColour(C.ink); cx.font = "11px -apple-system,sans-serif";
    cx.textAlign = "left"; cx.textBaseline = "middle";
    cx.fillText(pv.toLocaleString("en-IN",{maximumFractionDigits:2}), w-PAD.r+5, y);
    // time under the pointer, on the bottom axis
    const lbl = fmtD(readout[0]) + " " + fmtT(readout[0]);
    cx.font = "11px -apple-system,sans-serif"; cx.textAlign = "center";
    const tw = cx.measureText(lbl).width + 12;
    cx.fillStyle = C.ink;
    cx.fillRect(Math.min(w-PAD.r-tw/2, Math.max(tw/2, x))-tw/2, PAD.t+plotH+2, tw, 17);
    cx.fillStyle = onColour(C.ink); cx.textBaseline = "top";
    cx.fillText(lbl, Math.min(w-PAD.r-tw/2, Math.max(tw/2, x)), PAD.t+plotH+6);
    cx.restore();
  }

  // ---- the OHLC readout, in the bar above the canvas -------------------
  const f = v => v==null ? "—" : v.toLocaleString("en-IN",{maximumFractionDigits:2});
  const chg = readout[4] - readout[1];
  const pc  = readout[1] ? (chg/readout[1]*100) : 0;
  const cc  = chg >= 0 ? C.up : C.down;
  $("cvlegend").innerHTML =
    `<span class="o">${esc(d.index||"")} · ${esc(d.interval || "15m")}</span>`
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
  try{ gateTabs(); }catch(e){}
  try{ posGreeks(s); }catch(e){}
  try{ overnightWarn(s); }catch(e){}
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
      sw.textContent = "Switch market";
      sw.style.display = "inline-flex";
      sw.title = (s.markets && s.markets.length > 1)
        ? "Switch market" : "This server runs one market";
    } else sw.style.display = "none";
  }
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
  rrBox(r, tstate && tstate.ticket);
  sessionStrip(s.session, s.order);
  if(TAB === "chain") chainFetch();
  if(TAB === "news") newsFetch();
  recapDraw(s);
  if(TAB === "home") homeDraw(s);

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
  if(!force && CUR === MAPKEY && Date.now() - MAPAT < 2000) return;
  MAPKEY = CUR; MAPAT = Date.now();
  $("mapidx").textContent = CUR || "—";
  let d;
  // The screener and the sector panel read the same constituents, so the
  // fetch happens even when the map itself is put away - with a nominal box,
  // since nothing is being laid out.
  const blind = box.hidden || w < 40;
  try{
    d = await (await fetch(`/api/map/${encodeURIComponent(CUR)}?w=${blind?600:w}`
                           + `&h=${blind?400:h}`, {cache:"no-store"})).json();
  }catch(e){ return; }
  if(d.index !== CUR) return;              // the user switched mid-flight
  MAPDATA = d;
  screenDraw(); sectorDraw(); moverDraw(); pulseDraw();
  if(blind) return;

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
  const crypto = !!(LAST && LAST.market === "crypto");
  // The strip stays hidden on the crypto screen, but Home asks for the world
  // block rather than nothing: hiding the ticker used to leave MKT_ROWS full
  // of Indian indices from an earlier fetch, and Home printed NIFTY 50 at the
  // top of a Bitcoin desk.
  if(strip) strip.style.display = crypto ? "none" : "";
  try{
    const d = await (await fetch("/api/markets" + (crypto ? "?group=world" : ""),
                                 {cache:"no-store"})).json();
    MKT_ROWS = d.rows;
    if(!crypto) renderTicker(d.rows);
    if(TAB === "home") homeDraw(LAST);
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
  const su = $("sideuser");
  if(su){ su.textContent = email.split("@")[0]; su.title = email; }
  try{ accountDraw(s); }catch(e){}
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
  // The chain's strikes stream as well. render() asks for the chain only when
  // the index itself moved, so while its tab is open it is asked here too.
  if(TAB === "chain") chainFetch();

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
$("honest").innerHTML="A three-year backtest of the current rules, priced as options after "+
  "Zerodha's charges and slippage, came out slightly positive on Nifty and Sensex - on "+
  "modelled prices rather than real fills, with most of the profit on expiry days and a "+
  "worst drawdown of about \u20b91 lakh per lot in the latest year. It is published so it "+
  "can be checked, not because it is known to work. "+
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
                ["map","Market map"], ["news","Headlines"], ["sectors","Sectors"],
                ["mover","Index mover"], ["pulse","Market pulse"],
                ["clock","Option clock"], ["calc","Position calculator"],
                ["screen","Constituents"], ["recap","Session recap"],
                ["why","Why - every input"]];
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
  if(!HIDDEN.has("news")) newsFetch(true);
  if(!HIDDEN.has("screen") || !HIDDEN.has("sectors") || !HIDDEN.has("map")) heatMap(true);
}
applyPanels();


// ============================================================ layout
// The useful half of OpenTerminal's widget grid: the two side stacks can be
// re-ordered, and a panel can be dragged from one stack to the other. Saved
// per browser, restored on the next visit. The full-width panels are left
// alone - the signal above the reasoning is the argument the page is making.
const LKEY = "nbs.layout.v1";
const STACKS = ["colL", "colR", "colM2"];
// A panel is movable inside its own stack, and a panel that sits straight in
// a section is movable within that section. Keyed by container, so a layout
// saved for one section can never reorder another.
const DRAG_SEL = STACKS.map(id => `#${id} > [data-panel]`).join(", ")
               + ", .pane > [data-panel]";
let LAYOUT = {};
try{ LAYOUT = JSON.parse(localStorage.getItem(LKEY) || "{}"); }catch(e){}
const stackOf = el => {
  const p = el && el.parentElement;
  if(!p) return null;
  if(STACKS.includes(p.id)) return p.id;
  return p.classList && p.classList.contains("pane") ? "pane:" + p.dataset.pane : null;
};
function saveLayout(){
  const out = {};
  const boxes = [...STACKS.map(id => document.getElementById(id)),
                 ...document.querySelectorAll(".pane")].filter(Boolean);
  boxes.forEach(box => {
    const key = box.id || ("pane:" + box.dataset.pane);
    const kids = [...box.querySelectorAll(":scope > [data-panel]")].map(e => e.dataset.panel);
    if(kids.length) out[key] = kids;
  });
  LAYOUT = out;
  try{ localStorage.setItem(LKEY, JSON.stringify(out)); }catch(e){}
}
function applyLayout(){
  Object.keys(LAYOUT).forEach(id => {
    const box = id.startsWith("pane:")
      ? document.querySelector(`.pane[data-pane="${id.slice(5)}"]`)
      : document.getElementById(id);
    const names = LAYOUT[id];
    if(!box || !Array.isArray(names)) return;
    names.forEach(n => {
      const el = document.querySelector(`[data-panel="${n}"]`);
      // Only panels that already live in one of the two stacks, so a layout
      // saved by an older version cannot pull the signal card into a column.
      if(el && stackOf(el) === id) box.appendChild(el);
    });
  });
}
function resetLayout(){
  LAYOUT = {};
  try{ localStorage.removeItem(LKEY); }catch(e){}
  location.reload();
}
function wireDrag(){
  document.querySelectorAll(DRAG_SEL).forEach(el => {
    if(el.querySelector(":scope > .grip")) return;
    const g = document.createElement("button");
    g.className = "grip"; g.type = "button"; g.title = "Drag to move this panel";
    g.setAttribute("aria-label", "Move panel"); g.textContent = "∷";
    g.addEventListener("mousedown", () => { el.draggable = true; });
    g.addEventListener("mouseup", () => { el.draggable = false; });
    el.appendChild(g);
    el.addEventListener("dragstart", e => {
      e.dataTransfer.setData("text/plain", el.dataset.panel);
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("dragging");
    });
    el.addEventListener("dragend", () => {
      el.draggable = false; el.classList.remove("dragging");
      document.querySelectorAll(".over").forEach(x => x.classList.remove("over"));
    });
    el.addEventListener("dragover", e => {
      if(!document.querySelector(".dragging")) return;
      e.preventDefault(); el.classList.add("over");
    });
    el.addEventListener("dragleave", () => el.classList.remove("over"));
    el.addEventListener("drop", e => {
      e.preventDefault(); el.classList.remove("over");
      const name = e.dataTransfer.getData("text/plain");
      const src = document.querySelector(`[data-panel="${name}"]`);
      if(!src || src === el || src.parentElement !== el.parentElement) return;
      const r = el.getBoundingClientRect();
      el.parentElement.insertBefore(src, e.clientY < r.top + r.height / 2 ? el : el.nextSibling);
      saveLayout();
      try{ chartDraw(); }catch(err){}
    });
  });
}
applyLayout(); wireDrag();

// ============================================================ option chain
// The chain the signal was computed from: calls left, puts right, strikes
// down the middle, the money highlighted and the suggested contract ringed.
// Nothing new is fetched from anywhere - the server already had this.
let CHAIN_AT = 0, CHAIN_FOR = null, CHAIN_SCROLLED = null, CHAIN_LIVE = false, CHAIN_BUSY = false;
const oiFmt = v => v == null ? "—" :
  new Intl.NumberFormat("en-IN", {notation:"compact", maximumFractionDigits:1}).format(v);
async function chainFetch(force){
  const card = $("chaincard");
  if(!card || card.hidden || !CUR) return;
  // Once a second while its strikes stream - a memory read on the server - and
  // the old twenty seconds when they do not, since a snapshot moves no faster.
  if(!force && CHAIN_FOR === CUR && Date.now() - CHAIN_AT < (CHAIN_LIVE ? 1000 : 20000)) return;
  if(CHAIN_BUSY && !force) return;
  const want = CUR;
  CHAIN_AT = Date.now(); CHAIN_FOR = want; CHAIN_BUSY = true;
  try{
    const d = await (await fetch("/api/chain?index=" + encodeURIComponent(want),
                                 {cache:"no-store"})).json();
    if(want !== CUR) return;                  // the index changed while it loaded
    CHAIN_LIVE = !!(d && d.live);
    chainDraw(d);
  }catch(e){}
  finally{ CHAIN_BUSY = false; }
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
    + `over 3% and the tool holds the ticket.</span>`
    + (d.live ? `<span><b style="color:var(--up)">Live</b> - LTP, bid, ask and OI stream on `
        + `${d.live} contracts` + (d.live_at ? `, last tick ${esc(d.live_at)} IST` : "")
        + `. PCR, max pain and the walls move with each chain snapshot, about every 40 s.</span>`
      : "");
  // Centre the money once per index, not on every refresh - otherwise the
  // table yanks itself back while you are reading a far strike.
  //
  // By moving the BOX's own scrollTop, never scrollIntoView: that scrolls
  // every ancestor as well, so loading the page threw you down to the option
  // chain instead of leaving you at the top of the screen.
  if(CHAIN_SCROLLED !== d.index){
    CHAIN_SCROLLED = d.index;
    const row = t.querySelector("tr.atm"), box = t.closest(".chainwrap");
    if(row && box){
      const rb = row.getBoundingClientRect(), bb = box.getBoundingClientRect();
      box.scrollTop += (rb.top - bb.top) - (bb.height / 2 - rb.height / 2);
    }
  }
}



// ============================================================ sections
// One screen, five sections. The pane is switched by class, so nothing is
// rebuilt and nothing is re-fetched for a section you already opened; what a
// pane needs on first sight (a chart to size itself, a map to lay out) is
// drawn when it becomes visible, because an element with no box cannot.
const TABS = ["home", "signal", "chart", "chain", "market", "pulse", "sector",
              "spikes", "vol", "greeks", "levels", "internals", "strength",
              "season", "news", "record", "admin", "journal", "screener"];
const TAB_LABEL = {home:"Home", signal:"Signal", chart:"Chart", chain:"Option chain",
                   market:"Market", pulse:"Market pulse", sector:"Sector scope",
                   spikes:"Momentum spikes", vol:"Volatility", greeks:"Greeks & IV",
                   levels:"Levels",
                   internals:"Internals", strength:"Relative strength",
                   season:"Seasonality", news:"News", record:"Record", admin:"Admin", journal:"Journal", screener:"Screener"};
// The phone menu. A drawer rather than a strip of pills, closed by picking a
// section, tapping outside it, or Escape.
function navOpen(){
  document.body.classList.add("navopen");
  ["navbtn", "bnmore"].forEach(id => { const b = $(id); if(b) b.setAttribute("aria-expanded", "true"); });
}
function navClose(){
  if(!document.body.classList.contains("navopen")) return;
  document.body.classList.remove("navopen");
  ["navbtn", "bnmore"].forEach(id => { const b = $(id); if(b) b.setAttribute("aria-expanded", "false"); });
}

function showTab(name, push){
  if(!TABS.includes(name)) name = "home";
  TAB = name;
  document.querySelectorAll(".pane").forEach(p => p.classList.toggle("on", p.dataset.pane === name));
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  { const nt = $("navtitle"); if(nt) nt.textContent = TAB_LABEL[name] || "Menu"; }
  { const bm = $("bnmore"); if(bm) bm.classList.toggle("on", !["home", "signal", "chart", "chain"].includes(name)); }
  navClose();
  try{ localStorage.setItem("nbs.tab.v1", name); }catch(e){}
  if(push !== false && location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
  // Anything that measures itself has to be measured now that it has a size.
  // The handles are added per panel, and a pane that was hidden at load had
  // none: they are wired again on the way in, which is a no-op for any panel
  // that already has one.
  try{ wireDrag(); }catch(e){}
  if(name === "chart"){ try{ chartDraw(); sparkline(); }catch(e){} }
  if(name === "market") heatMap(true);
  if(name === "sector") heatMap(true);
  if(name === "pulse"){ pulseDraw(); screenFetch(); }
  if(name === "spikes") spikeFetch();
  if(["vol","levels","internals","strength","season"].includes(name)) anaFetch();
  if(name === "greeks") gkFetch();
  if(name === "admin") adminFetch();
  if(name === "journal") journalFetch();
  if(name === "screener") scFetch();
  if(name === "record"){
    const ses = (LAST && LAST.session) || {}, cap = $("c_cap");
    if(cap && !cap.value){
      cap.value = ses.capital ? Math.round(ses.capital) : "";
      $("c_risk").value = ses.risk_pct || "";
      const tk = ((LAST && LAST.tickets && LAST.tickets[CUR]) || {}).ticket;
      const r = (LAST && LAST.indices && LAST.indices[CUR]) || {};
      if($("c_lot") && !$("c_lot").value) $("c_lot").value = r.lot_size || "";
      if($("c_entry") && !$("c_entry").value && r.ltp) $("c_entry").value = r.ltp.toFixed(2);
      if($("c_stop") && !$("c_stop").value && r.premium_stop) $("c_stop").value = r.premium_stop.toFixed(2);
      calcDraw();
    }
  }
  if(name === "chain"){ chainFetch(true); oiFetch(); }
  if(name === "news") newsFetch();
  if(name === "home"){ homeDraw(LAST); markets_(); }
  gateTabs();
}
document.querySelectorAll(".tab").forEach(b =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));
$("navbtn").addEventListener("click", navOpen);
$("bnmore").addEventListener("click", () =>
  document.body.classList.contains("navopen") ? navClose() : navOpen());
$("navscrim").addEventListener("click", navClose);
addEventListener("keydown", e => { if(e.key === "Escape") navClose(); });
addEventListener("hashchange", () => showTab(location.hash.slice(1), false));
(() => {
  let start = location.hash.slice(1);
  if(!TABS.includes(start)){
    try{ start = localStorage.getItem("nbs.tab.v1") || "home"; }catch(e){ start = "home"; }
  }
  showTab(start, false);
})();


// ============================================================ home
// The landing screen: where the wider market is, what today has done, and a
// way into every section. Every number here is one the page already has -
// the strip's own levels and the session the record is kept in.
function homeDraw(s){
  const g = $("gmk");
  if(g && MKT_ROWS){
    g.innerHTML = MKT_ROWS.slice(0, 12).map(r => {
      const up = r.pct == null ? 0 : r.pct;
      const col = up > 0 ? "var(--up)" : up < 0 ? "var(--down)" : "var(--ink-3)";
      const chg = r.change == null ? "—"
        : `${r.change >= 0 ? "+" : "−"}${Math.abs(r.change).toLocaleString("en-IN")}`
          + (r.pct == null ? "" : ` (${r.pct >= 0 ? "+" : "−"}${Math.abs(r.pct).toFixed(2)}%)`);
      return `<div class="q"><div class="n">${esc(r.label)}</div>`
           + `<div class="p">${r.price == null ? "—" : num(r.price, r.dp == null ? 2 : r.dp)}</div>`
           + `<div class="c" style="color:${col}">${chg}</div></div>`;
    }).join("");
  }
  const t = $("htoday"), ses = (s && s.session) || {};
  if(t){
    const cell = (l, v, col) => `<div class="r"><div class="l">${esc(l)}</div>`
      + `<div class="v"${col ? ` style="color:${col}"` : ""}>${v}</div></div>`;
    const n = ses.net;
    t.innerHTML = cell("Market", s && s.market_open ? "Open" : "Closed",
                       s && s.market_open ? "var(--up)" : "var(--ink-3)")
      + cell("Tickets today", ses.issued == null ? "—" : ses.issued)
      + cell("Net", n == null ? "—" : money(n),
             (n || 0) > 0 ? "var(--up)" : (n || 0) < 0 ? "var(--down)" : "")
      + cell("Watching", (s && (s.order || []).length) || "—");
  }
  const d = $("dgrid");
  if(d && DESK.length && !d.dataset.built){
    d.dataset.built = "1";
    d.innerHTML = DESK.map(([tab, icon, title, desc]) =>
      `<button class="dcard" type="button" data-go="${tab}"><span class="i">${icon}</span>`
      + `<b>${esc(title)}</b><span>${esc(desc)}</span></button>`).join("");
    d.addEventListener("click", e => {
      const c = e.target.closest("[data-go]");
      if(c) showTab(c.dataset.go);
    });
  }
}

// ============================================================ index mover
// Which members are carrying the index and which are holding it back, in
// index points: the level times the member's weight times its move. The
// screener says what each stock did; this says what it did TO the index.
function moverDraw(){
  const box = $("mover");
  if(!box || !MAPDATA) return;
  const spot = ((LAST && LAST.indices && LAST.indices[CUR]) || {}).spot;
  $("movidx").textContent = CUR || "—";
  const rows = (MAPDATA.tiles || [])
    .filter(r => r.pct != null && r.weight)
    .map(r => ({sym: r.sym, pts: (spot || 0) * (r.weight / 100) * (r.pct / 100), pct: r.pct}))
    .sort((a, b) => b.pts - a.pts);
  if(!rows.length || !spot){
    box.innerHTML = `<p style="color:var(--ink-3);font-size:13px;margin:0">Waiting for the constituents.</p>`;
    $("movernote").textContent = "";
    return;
  }
  const top = rows.slice(0, 5), bottom = rows.slice(-5).reverse();
  const max = Math.max(...rows.map(r => Math.abs(r.pts)), 1);
  const line = r => {
    const up = r.pts >= 0, half = Math.min(50, Math.abs(r.pts) / max * 50);
    return `<div class="mvrow"><span class="s">${esc(r.sym)}</span>`
      + `<span class="mvbar"><u></u><i style="${up ? "left:50%" : "right:50%"};width:${half}%;`
      + `background:${up ? "var(--up)" : "var(--down)"}"></i></span>`
      + `<span class="mvval" style="color:${up ? "var(--up)" : "var(--down)"}">`
      + `${up ? "+" : "−"}${Math.abs(r.pts).toFixed(1)}</span></div>`;
  };
  box.innerHTML = top.map(line).join("") + bottom.map(line).join("");
  const net = rows.reduce((a, r) => a + r.pts, 0);
  $("movernote").textContent =
    `Index points contributed. The five pushing hardest and the five dragging most; `
    + `everything listed adds to ${net >= 0 ? "+" : "−"}${Math.abs(net).toFixed(0)} points.`;
}

// ============================================================ market pulse
function pulseDraw(){
  const box = $("pulse");
  if(!box || !MAPDATA) return;
  // Breadth across index members. A one-instrument market has none, and this
  // ran after the crypto paint and replaced it with "Waiting for the
  // constituents" - a message about something that does not exist there.
  if(LAST && LAST.market === "crypto") return;
  const known = (MAPDATA.tiles || []).filter(r => r.pct != null);
  if(!known.length){
    box.innerHTML = `<p style="color:var(--ink-3);font-size:13px;margin:0">Waiting for the constituents.</p>`;
    return;
  }
  const by = [...known].sort((a, b) => b.pct - a.pct);
  const row = r => `<div class="pr"><span>${esc(r.sym)}</span>`
    + `<span style="color:${r.pct >= 0 ? "var(--up)" : "var(--down)"}">`
    + `${r.pct >= 0 ? "+" : "−"}${Math.abs(r.pct).toFixed(2)}%</span></div>`;
  const up = known.filter(r => r.pct > 0).length, down = known.filter(r => r.pct < 0).length;
  const b = MAPDATA.breadth || {};
  box.innerHTML =
      `<div class="pr"><span><b>${up}</b> up &middot; <b>${down}</b> down</span>`
    + `<span style="color:${(b.weighted||0) >= 0 ? "var(--up)" : "var(--down)"}">`
    + `weighted ${(b.weighted||0) >= 0 ? "+" : "−"}${Math.abs(b.weighted||0).toFixed(2)}%</span></div>`
    + `<div class="ph">Leading</div>` + by.slice(0, 4).map(row).join("")
    + `<div class="ph">Lagging</div>` + by.slice(-4).reverse().map(row).join("");
}

// ============================================================ option clock
// Open interest is a running total, so the number that matters intraday is
// what has been ADDED since the session started. Writers building at a strike
// is where the market is defending; unwinding is where it has given up.
const OI = {day:"", exp:"", from:"09:15", to:"15:39", busy:false};
function oiSlots(){
  const hm = m => String(Math.floor(m/60)).padStart(2,"0") + ":" + String(m%60).padStart(2,"0");
  const out = [];
  for(let m = 9*60+15; m <= 15*60+35; m += 5) out.push(hm(m));
  out.push("15:39");          // the session's real last minute, and the value
  return out;                 // the endpoint defaults to - without it here,
}                             // setting the select silently left it empty.
function oiFill(sel, vals, cur){
  const el = $(sel);
  if(!el) return;
  const want = vals.join("|");
  if(el.dataset.vals !== want){
    el.dataset.vals = want;
    el.innerHTML = vals.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  }
  if(cur && el.value !== cur){
    el.value = cur;
    // A <select> given a value it has no option for goes to "" and says
    // nothing. That empty string is then sent as the query and the panel
    // shows a different window than the one on screen.
    if(el.value !== cur && vals.length) el.value = vals[vals.length - 1];
  }
}
async function oiFetch(){
  const card = $("clockcard");
  if(!card || card.hidden || OI.busy) return;
  OI.busy = true;
  try{
    const q = new URLSearchParams({index: CUR || "NIFTY", from: OI.from, to: OI.to});
    if(OI.day) q.set("day", OI.day);
    if(OI.exp) q.set("expiry", OI.exp);
    const d = await (await fetch("/api/oiclock?" + q, {cache:"no-store"})).json();
    clockDraw(d);
  }catch(e){
    // An endpoint that cannot answer and a session with no build-up are
    // different things and must not look the same on screen.
    $("clock2").innerHTML = `<p style="color:var(--warn);font-size:13px;margin:0">`
      + `The option clock could not be read just now.</p>`;
  }finally{ OI.busy = false; }
}
["oiday","oiexp","oifrom","oito"].forEach(id => {
  const el = $(id);
  if(el) el.addEventListener("change", () => {
    OI.day = $("oiday").value; OI.exp = $("oiexp").value;
    OI.from = $("oifrom").value; OI.to = $("oito").value;
    if(OI.from > OI.to){ const t = OI.from; OI.from = OI.to; OI.to = t;
                         $("oifrom").value = OI.from; $("oito").value = OI.to; }
    oiFetch();
  });
});
// Sector scope and the constituents screener are index-member screens. A
// market with one instrument has no members, so those tabs are not shown
// there at all - a hidden tab beats a blank panel that looks broken.
function gateTabs(){
  const crypto = !!(LAST && LAST.market === "crypto");
  [["sector", crypto], ["market", crypto], ["vol", crypto], ["levels", crypto],
   ["internals", crypto], ["strength", crypto], ["season", crypto],
   ["greeks", crypto], ["screener", crypto]].forEach(([name, hide]) => {
    const btn = document.querySelector(`.menu .tab[data-tab="${name}"]`);
    if(btn) btn.hidden = hide;
    if(hide && TAB === name) showTab("home");
  });
  // The admin tab exists only on an admin's screen. The server refuses every
  // admin request from anyone else regardless; hiding it is courtesy, not security.
  const admBtn = document.querySelector('.menu .tab[data-tab="admin"]');
  const isAdmin = !!(LAST && LAST.account && LAST.account.admin);
  if(admBtn) admBtn.hidden = !isAdmin;
  if(!isAdmin && TAB === "admin") showTab("home");
  const sc = document.querySelector(`.pane[data-pane="sector"]`);
  if(sc) sc.dataset.na = crypto ? "1" : "";
  // The first render happens before /api/state has answered, so the market is
  // unknown then and nothing can be gated on it. When it does arrive - or
  // changes - the strip has to be asked for again, because the rows fetched
  // under the previous assumption are the wrong market's.
  const key = crypto ? "crypto" : "other";
  if(GATED_FOR !== key){
    GATED_FOR = key;
    try{ markets_(); }catch(e){}
  }
}

// What an open position is exposed to, in money rather than textbook units.
// Hidden entirely when nothing is open: an empty table of dashes reads as
// broken, and there is nothing to say when there is no position.
// The one moment the BTST study matters: a ticket still open in the last hour
// of the session. The tool closes its own ticket at the bell; this is about
// carrying the real position past it.
function overnightWarn(s){
  const el = $("tovernight");
  if(!el) return;
  const tk = (((s && s.tickets) || {})[CUR] || {}).ticket;
  const o = ((((s && s.posgreeks) || {})[CUR]) || {}).overnight;
  if(!tk || !tk.open || !o || !o.show){ el.style.display = "none"; return; }
  const n = o.calendar_nights;
  const span = n > 1 ? `${n} nights - the market is shut until ${esc(o.next_open)}`
                     : `the night, until ${esc(o.next_open)}`;
  const idxName = {NIFTY: "Nifty", BANKNIFTY: "Bank Nifty", SENSEX: "Sensex"}[tk.index] || tk.index;
  const parts = [];
  if(o.expires_today){
    parts.push(`<b>This contract expires today at 15:30.</b> It cannot be held overnight - it settles at the close.`);
  } else {
    parts.push(`<b>Thinking of holding this overnight?</b> `
      + (o.decay != null
          ? `Time decay would take about <b>${money(o.decay, false)}</b> off this position over ${span}, even if the index does not move - more if volatility eases by morning.`
          : `It loses value to time decay over ${span}, even if the index does not move.`));
    parts.push(`A stop cannot protect it while the market is shut: it opens where it opens, and the gap decides the loss.`);
    if(o.baseline){
      parts.push(`In the three-year test, buying a ${esc(tk.option_type)} into the close on ${esc(idxName)} lost `
        + `${money(Math.abs(o.baseline.avg), false)} a trade per lot on average in the last year, over ${o.baseline.n} trades.`);
    }
  }
  parts.push(`The tool closes this ticket at the bell either way.`);
  el.innerHTML = parts.join(" ");
  el.style.display = "";
}

function posGreeks(s){
  const card = $("posgkcard"), box = $("posgk"), note = $("posgknote");
  if(!card || !box) return;
  const all = (s && s.posgreeks) || {};
  const mine = all[CUR];
  if(!mine){ card.hidden = true; return; }
  card.hidden = false;
  if(mine.note){
    box.innerHTML = `<p style="color:var(--ink-3);font-size:13px;margin:0">`
      + `${esc(mine.note)}</p>`;
    if(note) note.textContent = "";
    return;
  }
  const theta = mine.theta_day, per100 = mine.per_100;
  box.innerHTML =
    statRow("Implied volatility", mine.iv.toFixed(2) + "%")
  + statRow("Delta", mine.delta.toFixed(3))
  + statRow("A 100-point move is worth",
            per100 == null ? "—" : money(Math.abs(per100), false),
            per100 >= 0 ? "var(--up)" : "var(--down)")
  + statRow("Time decay, a day",
            theta == null ? "—" : "−" + money(Math.abs(theta), false), "var(--down)")
  + statRow("Per point of volatility",
            mine.vega_pt == null ? "—" : money(Math.abs(mine.vega_pt), false));
  const wif = mine.whatif || [];
  if(wif.length){
    const signed = (v, fmt) => v == null ? "—" : (v >= 0 ? "+" : "−") + fmt(Math.abs(v));
    const hue = v => v == null ? "" : ` style="color:${v >= 0 ? "var(--up)" : "var(--down)"}"`;
    box.innerHTML += `<p class="eyebrow" style="margin:14px 0 4px">What if</p>`
      + `<div class="scrwrap"><table class="scr rrtbl wiftbl"><thead><tr><th>If</th><th>Premium</th>`
      + `<th>Change</th><th>${mine.qty} units</th></tr></thead><tbody>`
      + wif.map(w => `<tr${w.key === "worst" ? ` class="exit"` : ""}><td>${esc(w.label)}</td>`
          + `<td>${w.premium.toFixed(2)}</td><td${hue(w.change)}>${signed(w.change, x => x.toFixed(2))}</td>`
          + `<td${hue(w.rupees)}>${signed(w.rupees, x => money(x, false))}</td></tr>`).join("")
      + `</tbody></table></div>`;
  }
  if(note) note.textContent =
    `On ${mine.qty} units, ${mine.days} days to expiry. Time decay is what `
    + `holding costs if nothing moves - it is charged every day, weekends `
    + `included, and it accelerates as expiry approaches. Delta is the move `
    + `per point of index, so the hundred-point figure is what the position `
    + `gains or loses on a move that size, before volatility changes.`
    + (wif.length ? ` What if: the premium repriced in full with the same model - a move `
      + `landing at once, the volatility drop that often follows an event, and a day of `
      + `time decay alone; the last row puts all three against the position together. `
      + `A model estimate, not a quote.` : "");
}

// ======================================================= account + admin
// Your own renewal date, everywhere it can be seen: the sidebar on a desktop,
// the Home screen on a phone (the sidebar footer is hidden there), and a notice
// across every section once a week or less is left.
function accountDraw(s){
  const a = (s && s.account) || null;
  const side = $("siderenew"), line = $("renewline"), note = $("renewnote");
  const soon = !!(a && !a.admin && a.days_left != null && a.days_left <= 7);
  const left = a && a.days_left != null
    ? (a.days_left <= 0 ? "ends today" : `${a.days_left} day${a.days_left === 1 ? "" : "s"} left`) : "";
  if(side){
    side.textContent = !a ? "" : a.admin ? "Admin" : a.expires ? `Access until ${a.expires_label}` : "No expiry set";
    side.style.color = soon ? "var(--warn)" : "";
  }
  if(line){
    line.textContent = (a && !a.admin && a.expires) ? `Your access runs until ${a.expires_label} - ${left}.` : "";
    line.style.color = soon ? "var(--warn)" : "";
  }
  if(note){
    note.style.display = soon ? "flex" : "none";
    if(soon) $("renewmsg").textContent = a.days_left <= 0
      ? `Your access ends today, ${a.expires_label}.`
      : `Your access renews on ${a.expires_label} - ${left}.`;
  }
}

let ADM = null;
function admStatus(msg, ok){
  const el = $("admmsg");
  if(el){ el.textContent = msg || ""; el.style.color = ok ? "var(--up)" : "var(--down)"; }
}
function admAddDays(base, n){
  const [y, m, d] = String(base).split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
}
function admDays(r){
  if(r.admin) return "never";
  if(!r.expires) return "no expiry";
  if(r.days_left == null) return "—";
  if(r.days_left < 0) return `ended ${-r.days_left}d ago`;
  if(r.days_left === 0) return "ends today";
  return `${r.days_left} day${r.days_left === 1 ? "" : "s"}`;
}
async function adminFetch(){
  const t = $("admusers");
  if(!t) return;
  try{
    const r = await fetch("/api/admin/users", {cache:"no-store"});
    if(r.status === 403){ t.innerHTML = `<tbody><tr><td style="color:var(--ink-3);padding:8px">Admin only.</td></tr></tbody>`; return; }
    ADM = await r.json();
  }catch(e){ admStatus("The account list could not be read.", false); return; }
  const ex = $("ac_exp");
  if(ex && !ex.value && ADM.today) ex.value = admAddDays(ADM.today, 30);
  adminPaint();
}
async function adminAction(fields){
  try{
    const r = await fetch("/api/admin", {method:"POST", cache:"no-store",
      headers:{"Content-Type":"application/x-www-form-urlencoded"},
      body:new URLSearchParams(fields)});
    const d = await r.json();
    admStatus(d.message || (d.ok ? "Done." : "That did not work."), !!d.ok);
    if(d.ok) adminFetch();
    return d;
  }catch(e){
    admStatus("That could not be sent.", false);
    return {ok:false};
  }
}
function adminPaint(){
  const d = ADM || {}, t = $("admusers");
  if(!t) return;
  const rows = d.users || [];
  $("admcount").textContent = `${rows.length} account${rows.length === 1 ? "" : "s"}`;
  const badge = r => {
    const c = r.admin ? "var(--accent)" : r.status === "active" ? "var(--up)"
            : r.status === "expired" ? "var(--down)" : "var(--ink-3)";
    return `<span class="admbadge" style="color:${c};border-color:${c}">${r.admin ? "admin" : r.status}</span>`;
  };
  const btn = (act, i, label, extra) =>
    `<button class="lbtn${extra ? " " + extra : ""}" type="button" data-act="${act}" data-i="${i}">${label}</button>`;
  t.innerHTML = `<thead><tr><th>Account</th><th>Status</th><th>Access until</th><th>Left</th>`
    + `<th>Last login</th><th>Zerodha</th><th></th></tr></thead><tbody>`
    + rows.map((r, i) => {
        const soon = !r.admin && r.days_left != null && r.days_left <= 7;
        const main = `<tr><td class="sym">${esc(r.email)}${r.email === d.me ? '<span class="admyou">you</span>' : ""}</td>`
          + `<td>${badge(r)}</td>`
          + `<td>${r.admin ? "—" : esc(r.expires_label || "no expiry")}</td>`
          + `<td style="color:${soon ? "var(--warn)" : "var(--ink-2)"}">${admDays(r)}</td>`
          + `<td>${esc(r.last_login || "never")}</td>`
          + `<td>${r.zerodha ? "connected" : "—"}</td>`
          + `<td>${r.admin ? "" : btn("edit", i, "Manage")}</td></tr>`;
        if(r.admin) return main;
        return main + `<tr class="admdetail" data-for="${i}" hidden><td colspan="7"><div class="admbox">`
          + `<div class="admline"><span>Access until</span>`
          + `<input type="date" class="admdate" value="${esc(r.expires || "")}">`
          + btn("exp-save", i, "Save date") + btn("exp-add", i, "+30 days", "d30")
          + btn("exp-add", i, "+90 days", "d90") + btn("exp-add", i, "+1 year", "d365")
          + btn("exp-never", i, "No expiry") + `</div>`
          + `<div class="admline"><span>New password</span>`
          + `<input type="text" class="admpass" autocomplete="off" spellcheck="false">`
          + btn("password", i, "Reset password") + `</div>`
          + `<div class="admline"><span>Access</span>`
          + btn(r.status === "disabled" ? "enable" : "disable", i, r.status === "disabled" ? "Enable" : "Disable")
          + btn("signout", i, `Sign out everywhere (${r.sessions})`)
          + (r.zerodha ? btn("unlink", i, "Disconnect Zerodha") : "")
          + btn("delete", i, "Delete account", "admdanger") + `</div>`
          + `</div></td></tr>`;
      }).join("") + `</tbody>`;
  const sv = d.server || {}, sb = $("admserver");
  if(sb) sb.innerHTML =
      statRow("Accounts", sv.accounts == null ? "—" : sv.accounts)
    + statRow("Signed-in sessions", sv.sessions == null ? "—" : sv.sessions)
    + statRow("Live data feeds", sv.feeds == null ? "—" : sv.feeds)
    + statRow("Signup", sv.signup ? "open to anyone" : "closed - accounts are made here")
    + statRow("Zerodha tick engine", sv.tick_engine
        ? esc(`${sv.tick_engine} - restart the server to get live prices back (a restart closes open tickets)`)
        : "no fault seen", sv.tick_engine ? "var(--warn)" : "")
    + (sv.streams || []).map(s => statRow(`Tick socket · ${esc(s.market)}`,
        esc(`${s.email}: ${s.error}`), "var(--warn)")).join("")
    + (() => {
        // Warn once the calendar runs out: the year itself missing, or from
        // mid-November the next year missing (NSE publishes it in December).
        const years = sv.holiday_years || [], now = new Date(), y = now.getFullYear();
        const due = !years.includes(y) ? y
                  : (now.getMonth() === 11 || (now.getMonth() === 10 && now.getDate() >= 15)) && !years.includes(y + 1) ? y + 1 : null;
        const have = years.length ? years.join(", ") : "none";
        return statRow("NSE holiday list", due
          ? `${have} - add ${due} to NSE_HOLIDAYS_BY_YEAR in main.py once NSE publishes it`
          : `covers ${have}`, due ? "var(--warn)" : "");
      })()
    + statRow("Running since", esc(sv.started || "—"))
    + statRow("Build", esc(sv.commit || "—"));
}
document.addEventListener("click", e => {
  const b = e.target.closest("#admusers [data-act]");
  if(b){
    const i = Number(b.dataset.i), r = ((ADM && ADM.users) || [])[i];
    if(!r) return;
    const act = b.dataset.act;
    const row = document.querySelector(`#admusers tr.admdetail[data-for="${i}"]`);
    if(act === "edit"){ if(row) row.hidden = !row.hidden; return; }
    const scope = row || document;
    const today = (ADM && ADM.today) || new Date().toISOString().slice(0, 10);
    if(act === "exp-save"){
      const v = (scope.querySelector(".admdate") || {}).value || "";
      if(!v){ admStatus("Pick a date first, or choose No expiry.", false); return; }
      adminAction({action:"expiry", email:r.email, expires:v}); return;
    }
    if(act === "exp-add"){
      // Renewal counts on from the current expiry, not from today, so renewing
      // early does not cost the user the days they already had.
      const base = (r.expires && r.expires > today) ? r.expires : today;
      const n = b.classList.contains("d365") ? 365 : b.classList.contains("d90") ? 90 : 30;
      adminAction({action:"expiry", email:r.email, expires:admAddDays(base, n)}); return;
    }
    if(act === "exp-never"){ adminAction({action:"expiry", email:r.email, expires:""}); return; }
    if(act === "password"){
      adminAction({action:"password", email:r.email,
                   password:(scope.querySelector(".admpass") || {}).value || ""}); return;
    }
    if(act === "delete"){
      if(!confirm(`Delete ${r.email}? This removes the account and its Zerodha connection, and cannot be undone.`)) return;
    }
    adminAction({action:act, email:r.email});
    return;
  }
  const q = e.target.closest("#admcreatecard [data-cq]");
  if(q){
    const today = (ADM && ADM.today) || new Date().toISOString().slice(0, 10);
    $("ac_exp").value = q.dataset.cq === "never" ? "" : admAddDays(today, Number(q.dataset.cq));
    return;
  }
  if(e.target.closest("#ac_go")){
    adminAction({action:"create", email:$("ac_email").value.trim(),
                 password:$("ac_pass").value, expires:$("ac_exp").value})
      .then(d => { if(d && d.ok){ $("ac_email").value = ""; $("ac_pass").value = ""; } });
  }
});

// ======================================================= greeks & IV
// The chain restated: what each price implies about movement, and how it will
// change. Validated against the live chain before it was wired - ATM implied
// volatility solved to within a tenth of a point of the offline run, with the
// put skew an equity index always carries.
let GK = null, GK_AT = 0, GK_FOR = null;
async function gkFetch(force){
  const want = CUR || "";
  if(!force && GK && GK_FOR === want && Date.now() - GK_AT < 45000){ gkPaint(); return; }
  try{
    GK = await (await fetch("/api/greeks?index=" + encodeURIComponent(want),
                            {cache:"no-store"})).json();
    GK_AT = Date.now(); GK_FOR = want;
  }catch(e){ GK = {error:"could not be read"}; }
  gkPaint();
}
function gkPaint(){
  const d = GK || {};
  const head = $("gkhead"), stats = $("gkstats");
  if(d.note && !(d.rows || []).length){
    if(head) head.textContent = "—";
    if(stats) stats.innerHTML = `<p style="color:var(--ink-3);font-size:13px;margin:0">`
      + `${esc(d.note)}</p>`;
    ["gkchain","gkconc"].forEach(id => { const t = $(id); if(t) t.innerHTML = ""; });
    if($("gknote")) $("gknote").textContent = "";
    return;
  }
  if(head) head.textContent = `${esc(d.index || "")} · expiry ${esc(d.expiry || "—")}`
    + ` · ${d.days} days left`;
  if(stats){
    const skewUp = (d.skew || 0) > 0;
    stats.innerHTML =
      statRow("At the money", d.atm_iv == null ? "—" : d.atm_iv.toFixed(2) + "%")
    + statRow("Downside puts", d.put_wing == null ? "—" : d.put_wing.toFixed(2) + "%")
    + statRow("Upside calls", d.call_wing == null ? "—" : d.call_wing.toFixed(2) + "%")
    + statRow("Skew (puts − calls)", d.skew == null ? "—"
              : (d.skew >= 0 ? "+" : "−") + Math.abs(d.skew).toFixed(2) + " pts",
              skewUp ? "var(--down)" : "var(--up)")
    + statRow("Strikes that solved", `${d.solved} of ${d.quotes}`);
  }
  if($("gknote")) $("gknote").textContent =
    (d.skew > 0
      ? "Downside puts carry more implied volatility than upside calls - the "
        + "usual shape for an index, where protection costs more than upside. "
      : "Upside calls carry as much implied volatility as the puts, which is "
        + "unusual for an index and worth a second look. ")
    + `Priced at a ${d.rate}% rate, ${d.minutes} minutes to the close on expiry `
    + `day. ${d.note || ""}`;

  const cell = (o, k, dp) => `<td>${!o || o[k] == null ? "—" : num(o[k], dp)}</td>`;
  scrTable("gkchain", d.rows || [],
    [["Strike", r => `<td class="sym"${r.atm ? ' style="color:var(--ink)"' : ""}>`
        + `${num(r.strike,0)}${r.atm ? " ·" : ""}</td>`],
     ["Call IV", r => `<td>${(r.ce||{}).iv == null ? "—" : r.ce.iv.toFixed(2)+"%"}</td>`],
     ["Δ", r => cell(r.ce, "delta", 3)],
     ["Θ/day", r => cell(r.ce, "theta", 1)],
     ["Put IV", r => `<td>${(r.pe||{}).iv == null ? "—" : r.pe.iv.toFixed(2)+"%"}</td>`],
     ["Δ ", r => cell(r.pe, "delta", 3)],
     ["Θ/day ", r => cell(r.pe, "theta", 1)],
     ["Γ", r => cell(r.ce, "gamma", 6)],
     ["Vega", r => cell(r.ce, "vega", 1)]],
    "No chain right now.");
  if($("gkchainnote")) $("gkchainnote").textContent =
    "Delta is the move per point of index; gamma how fast delta itself moves; "
    + "theta what a day of waiting costs; vega the change per point of implied "
    + "volatility. Gamma and vega are the same for a call and a put at the same "
    + "strike, so they are shown once. A dash means no volatility fits that "
    + "price - usually a stale quote on a far strike, and refusing is the "
    + "honest answer.";

  scrTable("gkconc", d.concentration || [],
    [["Strike", r => `<td class="sym">${num(r.strike,0)}</td>`],
     ["Gamma × OI", r => {
        const up = r.v >= 0;
        return `<td style="color:${up ? "var(--up)" : "var(--down)"}">`
             + `${up ? "+" : "−"}${num(Math.abs(r.v),0)}</td>`; }],
     ["Distance from spot", r => `<td>${d.spot == null ? "—"
        : (r.strike >= d.spot ? "+" : "−") + num(Math.abs(r.strike - d.spot), 0)}</td>`]],
    "No open interest to weight.");
  if($("gkconcnote")) $("gkconcnote").textContent =
    "Gamma weighted by open interest, calls counted positive and puts negative. "
    + "This is where the chain's gamma sits, not a claim about who is hedging "
    + "it: which side a dealer is short is not visible from outside the "
    + "exchange, so it is not asserted here.";
}

// ======================================================= analysis
// One fetch behind five panes. Every panel prints the sample it was computed
// from: a statistic without its n is a claim, not a measurement.
let ANA = null, ANA_AT = 0;
async function anaFetch(force){
  if(!force && ANA && Date.now() - ANA_AT < 600000){ anaPaint(); return; }
  // The first read after a restart fetches candles for every index member,
  // about twenty seconds - said while it happens, rather than empty cards.
  if(!ANA){
    const msg = `<p style="color:var(--ink-3);font-size:13px;margin:0">Reading candles for every `
      + `index member - the first read after a restart can take about twenty seconds.</p>`;
    ["volstats", "intstats", "sgap"].forEach(id => { const el = $(id); if(el && !el.innerHTML.trim()) el.innerHTML = msg; });
  }
  try{
    ANA = await (await fetch("/api/analytics", {cache:"no-store"})).json();
    ANA_AT = Date.now();
  }catch(e){ ANA = {error: "could not be read"}; }
  anaPaint();
}
const statRow = (label, value, colour) =>
  `<div class="pr"><span>${esc(label)}</span>`
  + `<b${colour ? ` style="color:${colour}"` : ""}>${value}</b></div>`;

function anaPaint(){
  const d = ANA || {};
  if(d.note){                       // a market these do not apply to
    ["volstats","intstats","sgap"].forEach(id => {
      const el = $(id);
      if(el) el.innerHTML = `<p style="color:var(--ink-3);font-size:13px;margin:0">`
        + `${esc(d.note)}</p>`; });
    ["volem","volrv","lvl","lvlor","rslead","rslag","sdow"].forEach(id => {
      const t = $(id); if(t) t.innerHTML = ""; });
    return;
  }
  const v = d.vol || {};
  if($("volstats")){
    if(v.error || v.vix == null){
      $("volstats").innerHTML = `<p style="color:var(--ink-3);font-size:13px;margin:0">`
        + `Volatility data is not available right now.</p>`;
    }else{
      const rich = v.premium > 0;
      $("volstats").innerHTML =
        statRow("India VIX (implied)", v.vix.toFixed(2) + "%")
      + statRow("Realised, 20 sessions", v.rv20 == null ? "—" : v.rv20.toFixed(2) + "%")
      + statRow("Premium over realised", (v.premium >= 0 ? "+" : "−")
                + Math.abs(v.premium).toFixed(2) + " pts",
                rich ? "var(--warn)" : "var(--up)")
      + statRow("VIX percentile, past year", v.pctile + "%")
      + statRow("VIX range this year", v.vix_lo.toFixed(1) + " – " + v.vix_hi.toFixed(1));
      $("volnote").textContent = rich
        ? `Implied volatility sits ${v.premium.toFixed(2)} points above what the `
          + `index has actually done over 20 sessions, so option premium is `
          + `expensive relative to recent movement. That favours the seller and `
          + `penalises the buyer - it does not predict direction.`
        : `Implied volatility is below realised movement: premium is cheap `
          + `relative to how much the index has been moving.`;
    }
  }
  if(v.moves){
    scrTable("volem", [["a day", v.moves.day], ["this week", v.moves.week],
                       ["this month", v.moves.month]].map(([k, m]) =>
              ({window: k, pts: m.pts, lo: m.lo, hi: m.hi})),
      [["Over", r => `<td class="sym">${esc(r.window)}</td>`],
       ["Expected move", r => `<td>±${num(r.pts,0)} pts</td>`],
       ["Low", r => `<td>${num(r.lo,2)}</td>`],
       ["High", r => `<td>${num(r.hi,2)}</td>`]],
      "No implied volatility reading.");
    if($("volemnote")) $("volemnote").textContent =
      `One standard deviation from ${num(v.spot,2)} at today's implied `
      + `volatility. Over the last ${v.inside_n} sessions the index stayed `
      + `inside its one-day expected move ${v.inside}% of the time (${v.inside_60}% `
      + `over the last 60). A fair one-sigma band would hold about 68%, so the `
      + `market has been pricing more movement than it delivered.`;
  }
  const cone = (d.cone || {});
  scrTable("volcone", cone.rows || [],
    [["Window", r => `<td class="sym">${r.window} days</td>`],
     ["Now", r => `<td style="color:var(--ink)">${r.now.toFixed(2)}%</td>`],
     ["Quietest", r => `<td>${r.min.toFixed(2)}%</td>`],
     ["Median", r => `<td>${r.median.toFixed(2)}%</td>`],
     ["Wildest", r => `<td>${r.max.toFixed(2)}%</td>`],
     ["Percentile", r => {
        const col = r.rank < 20 ? "var(--up)" : r.rank > 80 ? "var(--down)" : "var(--ink-2)";
        return `<td style="color:${col}">${r.rank}%</td>`; }],
     ["Windows", r => `<td>${r.n}</td>`]],
    "Not enough daily history for a cone.");
  if($("volconenote")){
    const quiet = (cone.rows || []).filter(r => r.rank < 20).map(r => r.window + "d");
    $("volconenote").textContent =
      `Realised volatility over each window, ranked against every other window `
      + `of the same length in ${cone.sessions || "—"} sessions. Short windows `
      + `swing and long ones anchor, which is why all six are here rather than `
      + `one number. `
      + (quiet.length
          ? `At ${quiet.join(", ")} the index is in the quietest fifth of its `
            + `year while options are priced at ${cone.iv}% - the same premium `
            + `the panel above measures, seen by horizon.`
          : `Nothing is at an extreme of its own range today.`);
  }
  scrTable("volrv", [["10 sessions", v.rv10], ["20 sessions", v.rv20],
                     ["60 sessions", v.rv60], ["250 sessions", v.rv250]]
             .filter(r => r[1] != null).map(([k, x]) => ({w: k, v: x})),
    [["Window", r => `<td class="sym">${esc(r.w)}</td>`],
     ["Annualised", r => `<td>${r.v.toFixed(2)}%</td>`]],
    "No candles yet.");

  const L = d.levels || [];
  if(L.length && $("lvlsess")) $("lvlsess").textContent = "from " + L[0].session;
  scrTable("lvl", L,
    [["Index", r => `<td class="sym">${esc(r.index)}</td>`],
     ["Close", r => `<td>${num(r.close,2)}</td>`],
     ["S2", r => `<td>${num(r.s2,2)}</td>`], ["S1", r => `<td>${num(r.s1,2)}</td>`],
     ["Pivot", r => `<td style="color:var(--ink)">${num(r.pivot,2)}</td>`],
     ["R1", r => `<td>${num(r.r1,2)}</td>`], ["R2", r => `<td>${num(r.r2,2)}</td>`],
     ["ATR(14)", r => `<td>${num(r.atr,0)}</td>`]],
    "No levels yet.");
  if($("lvlnote")) $("lvlnote").textContent =
    "Floor pivots from the previous session's high, low and close. ATR(14) is "
    + "the average true range over fourteen sessions - the distance this index "
    + "typically covers in a day, which is what a stop has to survive.";
  scrTable("lvlor", L.filter(r => r.or_hi != null),
    [["Index", r => `<td class="sym">${esc(r.index)}</td>`],
     ["Range low", r => `<td>${num(r.or_lo,2)}</td>`],
     ["Range high", r => `<td>${num(r.or_hi,2)}</td>`],
     ["Width", r => `<td>${num(r.or_hi - r.or_lo,0)} pts</td>`],
     ["Closed", r => `<td style="color:${r.or_close === "above" ? "var(--up)"
        : r.or_close === "below" ? "var(--down)" : "var(--ink-2)"}">${esc(r.or_close)}</td>`]],
    "No intraday candles for the opening range.");
  scrTable("lvlfib", L.filter(r => r.fib50 != null),
    [["Index", r => `<td class="sym">${esc(r.index)}</td>`],
     ["Low", r => `<td>${num(r.prev_low,2)}</td>`],
     ["38.2%", r => `<td>${num(r.fib382,2)}</td>`],
     ["50%", r => `<td style="color:var(--ink)">${num(r.fib50,2)}</td>`],
     ["61.8%", r => `<td>${num(r.fib618,2)}</td>`],
     ["High", r => `<td>${num(r.prev_high,2)}</td>`]],
    "No levels yet.");
  if($("lvlfibnote")) $("lvlfibnote").textContent =
    "The previous session's range cut at 38.2%, 50% and 61.8%, measured up from its low - "
    + "the same three prices as measured down from its high. Levels many traders watch for "
    + "a pullback to stall; they are not part of the signal and this tool has not tested them.";

  const intern = d.internals || {};
  if($("intstats")){
    if(intern.error || intern.n == null){
      $("intstats").innerHTML = `<p style="color:var(--ink-3);font-size:13px;margin:0">`
        + `Constituent history is not loaded.</p>`;
    }else{
      const weak = intern.above20 < 30;
      $("intstats").innerHTML =
        statRow("Above their 20-day average", intern.above20 + "%",
                weak ? "var(--down)" : "var(--up)")
      + statRow("Above their 50-day average", intern.above50 + "%")
      + statRow("Advancing / declining", intern.adv + " / " + intern.dec,
                intern.adv > intern.dec ? "var(--up)" : "var(--down)")
      + statRow(`At a ${intern.lookback}-session high`, intern.at_high)
      + statRow(`At a ${intern.lookback}-session low`, intern.at_low);
      $("intnote").textContent =
        `${intern.n} index members over ${intern.lookback} sessions. Breadth says whether `
        + `a move is the whole market or a few heavyweights: an index can rise `
        + `while most of its members fall.`;
    }
  }
  adDraw(intern.adline || []);

  const strong = d.strength || {};
  if($("rswin")) $("rswin").textContent = strong.window || 20;
  const rsCols = [["Symbol", r => `<td class="sym">${esc(r.sym)}</td>`],
                  ["Return", r => pctCell(r.pct)]];
  scrTable("rslead", strong.leaders || [], rsCols, "No history yet.");
  scrTable("rslag", (strong.laggards || []).slice().reverse(), rsCols, "No history yet.");
  if($("rsnote")) $("rsnote").textContent =
    `Return over the last ${strong.window || 20} sessions, across the index members `
    + `this tool covers. Relative strength is about ranking, not direction: in a `
    + `falling market the leader may still be down.`;

  const se = d.season || {};
  if($("seasn")) $("seasn").textContent = se.n || "—";
  scrTable("sdow", se.dow || [],
    [["Day", r => `<td class="sym">${esc(r.day)}</td>`],
     ["Average", r => pctCell(r.avg)],
     ["Up days", r => `<td>${r.up}%</td>`],
     ["Spread", r => `<td>${r.sd.toFixed(2)}</td>`],
     ["Sessions", r => `<td>${r.n}</td>`]],
    "No daily history.");
  if($("sdownote")) $("sdownote").textContent =
    "Average close-to-close move by weekday. With about fifty samples a day "
    + "these are tendencies, not rules - the spread column is wider than every "
    + "average in the table, which is the point.";
  if($("sgap") && se.n){
    $("sgap").innerHTML =
      statRow("Gapped up at the open", se.gap_up + " sessions", "var(--up)")
    + statRow("Opened flat", se.gap_flat + " sessions")
    + statRow("Gapped down", se.gap_down + " sessions", "var(--down)")
    + statRow("Average gap, either way", se.gap_avg + "%");
  }
}

// The A/D line: a running total of advances minus declines. Drawn rather than
// tabulated because its shape is the information.
function adDraw(vals){
  const c = $("adline");
  if(!c) return;
  const box = c.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(240, Math.floor(box.width)), h = 150;
  c.width = w * dpr; c.height = h * dpr;
  c.style.width = w + "px"; c.style.height = h + "px";
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  if(!vals.length){
    g.fillStyle = "#6b7282"; g.font = "13px system-ui";
    g.fillText("Waiting for the constituents.", 10, 24);
    return;
  }
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const span = (hi - lo) || 1, pad = 10;
  const x = i => pad + i * (w - 2 * pad) / Math.max(1, vals.length - 1);
  const y = v => h - pad - (v - lo) * (h - 2 * pad) / span;
  g.strokeStyle = "rgba(255,255,255,.10)"; g.beginPath();
  g.moveTo(pad, y(0)); g.lineTo(w - pad, y(0)); g.stroke();
  const last = vals[vals.length - 1];
  g.strokeStyle = last >= 0 ? "#2be08a" : "#ef5570";
  g.lineWidth = 2; g.beginPath();
  vals.forEach((v, i) => i ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v)));
  g.stroke();
  g.fillStyle = last >= 0 ? "#2be08a" : "#ef5570";
  g.beginPath(); g.arc(x(vals.length - 1), y(last), 3, 0, 7); g.fill();
  const note = $("adnote");
  if(note) note.textContent = `Cumulative advances minus declines over the last `
    + `${vals.length} sessions, currently ${last >= 0 ? "+" : "−"}${Math.abs(last)}. `
    + `A falling line while the index holds up means fewer and fewer names are `
    + `carrying it.`;
}

// ======================================================= pulse screeners
// One fetch of /api/screen feeds four tables. Each says what it covers: these
// are the index constituents this tool knows, not the whole exchange, and a
// screener that implies a wider net than it casts is worse than none.
let SCREEN = null, SCREEN_AT = 0;
function scrTable(el, rows, cols, empty){
  const t = $(el);
  if(!t) return;
  if(!rows.length){
    t.innerHTML = `<tbody><tr><td style="color:var(--ink-3);padding:8px">`
                + `${esc(empty)}</td></tr></tbody>`;
    return;
  }
  t.innerHTML = `<thead><tr>${cols.map(c => `<th>${esc(c[0])}</th>`).join("")}</tr></thead>`
    + `<tbody>${rows.map(r => `<tr>${cols.map(c => c[1](r)).join("")}</tr>`).join("")}</tbody>`;
}
const pctCell = v => {
  const col = v == null ? "var(--ink-3)" : v > 0 ? "var(--up)" : v < 0 ? "var(--down)" : "var(--ink-2)";
  return `<td style="color:${col}">${v == null ? "—"
    : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2) + "%"}</td>`;
};
async function screenFetch(force){
  if(!force && SCREEN && Date.now() - SCREEN_AT < 300000){ screenPaint(); return; }
  const note = $("pulsenote");
  if(note) note.textContent = "Loading the constituents…";
  try{
    SCREEN = await (await fetch("/api/screen", {cache:"no-store"})).json();
    SCREEN_AT = Date.now();
    screenPaint();
  }catch(e){
    if(note) note.textContent = "The screener could not be read just now.";
  }
}
function screenPaint(){
  const d = SCREEN || {}, rows = d.rows || [], note = $("pulsenote");
  if(d.error){
    if(note) note.textContent = "Screener error: " + d.error;
    return;
  }
  if(d.kind === "crypto"){ cryptoPulsePaint(d); return; }
  const sym = r => `<td class="sym">${esc(r.sym)}</td>`;
  const px = r => `<td>${num(r.close, 2)}</td>`;
  scrTable("bo10", rows.filter(r => r.bo10),
    [["Symbol", sym], ["Price", px], ["Change", r => pctCell(r.pct)],
     ["Broke", r => `<td style="color:${r.bo10 === "high" ? "var(--up)" : "var(--down)"}">`
                  + `10-day ${r.bo10}</td>`]],
    "Nothing broke its 10-day range in this session.");
  scrTable("bo50", rows.filter(r => r.bo50),
    [["Symbol", sym], ["Price", px], ["Change", r => pctCell(r.pct)],
     ["Broke", r => `<td style="color:${r.bo50 === "high" ? "var(--up)" : "var(--down)"}">`
                  + `50-day ${r.bo50}</td>`]],
    "Nothing broke its 50-day range in this session.");
  scrTable("boost", rows.filter(r => r.vx != null).sort((a,b) => b.vx - a.vx).slice(0, 12),
    [["Symbol", sym], ["Price", px], ["Change", r => pctCell(r.pct)],
     ["Volume", r => `<td>${r.vx.toFixed(2)}×</td>`]],
    "No volume reading yet.");
  const near = rows.filter(r => r.hi50 && r.lo50).map(r => {
    const span = r.hi50 - r.lo50;
    return Object.assign({}, r, {pos: span ? (r.close - r.lo50) / span * 100 : null});
  }).filter(r => r.pos != null).sort((a,b) => Math.abs(b.pos - 50) - Math.abs(a.pos - 50));
  scrTable("levels", near.slice(0, 12),
    [["Symbol", sym], ["Price", px],
     ["50-day low", r => `<td>${num(r.lo50, 2)}</td>`],
     ["50-day high", r => `<td>${num(r.hi50, 2)}</td>`],
     ["In range", r => `<td style="color:${r.pos > 80 ? "var(--up)" : r.pos < 20 ? "var(--down)" : "var(--ink-2)"}">`
                     + `${r.pos.toFixed(0)}%</td>`]],
    "No range reading yet.");
  if(note) note.textContent = `${d.covered || 0} of ${d.universe || 0} `
    + `${d.scope || "constituents"} — daily candles, cached for fifteen minutes. `
    + `This is not the whole exchange.`;
}

// The crypto shapes of those two screens. Same panels, different question:
// with one instrument there is no breadth, so the chain's own open interest
// is the distribution worth showing.
function cryptoPulsePaint(d){
  const note = $("pulsenote");
  const pulseEyebrow = document.querySelector("#pulsecard .eyebrow");
  if(pulseEyebrow) pulseEyebrow.innerHTML = "Market pulse &middot; the option chain";
  ["bo50","boost","levels"].forEach(id => { const t = $(id); if(t) t.innerHTML = ""; });
  ["bo50card","boostcard","levelscard"].forEach(id => {
    const c = $(id); if(c) c.hidden = true; });
  const p = $("pulse");
  if(p){
    if(d.pcr == null){
      p.innerHTML = `<p style="color:var(--ink-3);font-size:13px;margin:0">`
        + `Waiting for the option chain.</p>`;
    }else{
      const bull = d.pcr > 1;
      p.innerHTML = `<div class="pr"><span>Put/call open interest</span>`
        + `<b style="color:${bull ? "var(--up)" : "var(--down)"}">${d.pcr.toFixed(2)}</b></div>`
        + `<div class="pr"><span>Calls open</span><b>${oiFmt(d.call_oi)}</b></div>`
        + `<div class="pr"><span>Puts open</span><b>${oiFmt(d.put_oi)}</b></div>`
        + `<div class="pr"><span>Call wall</span><b>${num(d.call_wall,0)}</b></div>`
        + `<div class="pr"><span>Put wall</span><b>${num(d.put_wall,0)}</b></div>`
        + `<div class="pr"><span>Spot</span><b>${num(d.spot,2)}</b></div>`;
    }
  }
  const head = $("bo10card");
  if(head){
    head.hidden = false;
    const t = head.querySelector(".eyebrow");
    if(t) t.innerHTML = "Open interest around the money &middot; expiry "
                      + esc(d.expiry || "—");
  }
  scrTable("bo10", d.strikes || [],
    [["Strike", r => `<td class="sym">${num(r.strike,0)}</td>`],
     ["Calls open", r => `<td>${oiFmt(r.ce)}</td>`],
     ["Puts open", r => `<td>${oiFmt(r.pe)}</td>`],
     ["Leaning", r => { const b = r.pe > r.ce;
       return `<td style="color:${b ? "var(--up)" : "var(--down)"}">`
            + `${b ? "puts" : "calls"}</td>`; }]],
    "No chain right now.");
  if(note) note.textContent = "Open interest across the live option chain, in "
    + "contracts. One instrument has no breadth to measure, so this is where "
    + "the positions actually sit.";
}
function cryptoMovesPaint(d){
  const note = $("spknote"), bar = $("spkbar"), head = $("spkhead");
  const c10 = $("spk10card");
  if(c10) c10.hidden = true;
  if(bar) bar.innerHTML = `<span class="lv on">live</span> `
    + `${esc(d.index || "BTC")} &middot; ${num(d.close,2)}`
    + (d.vx != null ? ` &middot; volume ${d.vx.toFixed(2)}× its average` : "");
  if(head) head.textContent = "how far it has moved";
  scrTable("spk5", d.rows || [],
    [["Window", r => `<td class="sym">${esc(r.window)}</td>`],
     ["Move", r => pctCell(r.move)]],
    d.note || "Not enough candles yet.");
  if(note) note.textContent = "This market trades one instrument, so there is "
    + "no board of movers: this is what that instrument has done, on its own "
    + "volume. Day range " + num(d.low,2) + " – " + num(d.high,2) + ".";
}

// ======================================================= momentum spikes
let SPK = null, SPK_AT = 0;
async function spikeFetch(force){
  if(!force && SPK && Date.now() - SPK_AT < 120000){ spikePaint(); return; }
  const note = $("spknote");
  if(note) note.textContent = "Loading five-minute candles…";
  try{
    SPK = await (await fetch("/api/spikes", {cache:"no-store"})).json();
    SPK_AT = Date.now();
    spikePaint();
  }catch(e){
    if(note) note.textContent = "The spike screen could not be read just now.";
  }
}
function spikePaint(){
  const d = SPK || {}, rows = d.rows || [], note = $("spknote");
  if(d.error){ if(note) note.textContent = "Spike error: " + d.error; return; }
  if(d.kind === "crypto"){ cryptoMovesPaint(d); return; }
  const bar = $("spkbar");
  if(bar){
    bar.innerHTML = d.live
      ? `<span class="lv on">live</span> this session`
      : `<span class="lv">last session</span> ${esc(d.session || "—")} — the market is shut, `
        + `so this is the last session that traded, not live movement.`;
  }
  const head = $("spkhead");
  if(head) head.textContent = d.live ? "last five minutes" : "final five minutes of " + (d.session || "");
  const sym = r => `<td class="sym">${esc(r.sym)}</td>`;
  const px = r => `<td>${num(r.close, 2)}</td>`;
  // Above its own average, ranked by the size of the move either way: a spike
  // is a move ON volume, so both halves have to be there.
  const five = rows.filter(r => r.v5 > 1.2).sort((a,b) => Math.abs(b.m5) - Math.abs(a.m5)).slice(0, 12);
  const ten  = rows.filter(r => r.v10 > 1.2).sort((a,b) => Math.abs(b.m10) - Math.abs(a.m10)).slice(0, 12);
  scrTable("spk5", five,
    [["Symbol", sym], ["Price", px], ["5-min move", r => pctCell(r.m5)],
     ["Volume", r => `<td>${r.v5.toFixed(2)}×</td>`]],
    "Nothing moved on volume in the final five minutes.");
  scrTable("spk10", ten,
    [["Symbol", sym], ["Price", px], ["10-min move", r => pctCell(r.m10)],
     ["Volume", r => `<td>${r.v10.toFixed(2)}×</td>`]],
    "Nothing moved on volume in the final ten minutes.");
  if(note) note.textContent = `${d.covered || 0} ${d.scope || "constituents"}. `
    + `${d.note || ""}`;
}

function clockDraw(d){
  const box = $("clock2");
  if(!box) return;
  const oiBar = document.querySelector("#clockcard .clockbar");
  if(oiBar) oiBar.hidden = !(d && d.days && d.days.length);
  if(d && d.days) oiFill("oiday", d.days, d.day);
  if(d && d.expiries && d.expiries.length) oiFill("oiexp", d.expiries, d.expiry);
  const slots = oiSlots();
  oiFill("oifrom", slots, d && d.from);
  oiFill("oito", slots, d && d.to);
  const head = $("oihead");
  if(head){
    if(d && d.note){ head.textContent = d.note; }
    else if(d && (d.net_ce != null)){
      const bull = (d.net_pe || 0) > (d.net_ce || 0);
      head.innerHTML = `<b style="color:${bull ? "var(--up)" : "var(--down)"}">`
        + `${esc(d.reading || "")}</b> &middot; calls ${d.net_ce >= 0 ? "+" : "−"}`
        + `${oiFmt(Math.abs(d.net_ce))} &middot; puts ${d.net_pe >= 0 ? "+" : "−"}`
        + `${oiFmt(Math.abs(d.net_pe))}`
        + (d.support ? ` &middot; support ${num(d.support,0)}` : "")
        + (d.resistance ? ` &middot; resistance ${num(d.resistance,0)}` : "");
    } else head.textContent = "";
  }
  const rows = (d && d.rows) || [];
  const adds = [];
  rows.forEach(r => {
    if(r.ce != null) adds.push({k: r.strike, side: "CE", v: r.ce});
    if(r.pe != null) adds.push({k: r.strike, side: "PE", v: r.pe});
  });
  const moved = adds.filter(a => Math.abs(a.v) > 0);
  if(!moved.length){
    box.innerHTML = `<p style="color:var(--ink-3);font-size:13px;margin:0">`
      + `${d && d.note ? esc(d.note) : "No open-interest change in this window."}</p>`;
    $("clocknote").textContent = "";
    return;
  }
  moved.sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
  const max = Math.max(...moved.map(a => Math.abs(a.v)), 1);
  box.innerHTML = moved.slice(0, 8).map(a => {
    const up = a.v >= 0, half = Math.min(50, Math.abs(a.v) / max * 50);
    return `<div class="mvrow"><span class="s">${num(a.k,0)} ${a.side}</span>`
      + `<span class="mvbar"><u></u><i style="${up ? "left:50%" : "right:50%"};width:${half}%;`
      + `background:${a.side === "CE" ? "var(--up)" : "var(--down)"};opacity:${up ? 1 : .55}"></i></span>`
      + `<span class="mvval" style="color:${up ? "var(--ink-2)" : "var(--ink-3)"}">`
      + `${up ? "+" : "−"}${oiFmt(Math.abs(a.v))}</span></div>`;
  }).join("");
  $("clocknote").textContent = (d && d.window_note ? d.window_note + " " : "")
    + "Open interest is a level: it falls as positions close as readily as it "
    + "rises, so a morning build and an afternoon unwind are both real and do "
    + "not cancel into one number. Read from the recorder's own files.";
}

// ============================================================ calculator
// The same arithmetic the risk box does on a live signal, for a trade you are
// sizing by hand. It places nothing and stores nothing.
function calcDraw(){
  const g = id => parseFloat(($(id).value || "").replace(/[^0-9.]/g, ""));
  const cap = g("c_cap"), risk = g("c_risk"), entry = g("c_entry"),
        stop = g("c_stop"), lot = g("c_lot");
  const out = $("calcout");
  if(!out) return;
  if(!(entry > 0) || !(stop >= 0) || !(lot > 0) || !(entry > stop)){
    out.innerHTML = "Enter an entry above the stop, and the lot size, to size a trade.";
    return;
  }
  const perLot = (entry - stop) * lot;
  let txt = `One lot risks <b>${money(perLot, false)}</b> `
          + `(${num(entry - stop, 2)} of premium × ${num(lot, 0)}).`;
  if(cap > 0 && risk > 0){
    const budget = cap * risk / 100, fit = Math.floor(budget / perLot);
    txt += ` At ${num(risk, 2)}% of ${money(cap, false)} you can risk `
        + `<b>${money(budget, false)}</b>, which is `
        + (fit >= 1 ? `<b>${fit} lot${fit !== 1 ? "s" : ""}</b> `
                    + `(${money(perLot * fit, false)}, ${num(perLot * fit / cap * 100, 2)}% of capital).`
                    : `<b>less than one lot</b> - one lot alone is `
                      + `${num(perLot / cap * 100, 2)}% of capital.`);
  }
  out.innerHTML = txt;
}
["c_cap","c_risk","c_entry","c_stop","c_lot"].forEach(id => {
  const el = $(id);
  if(el) el.addEventListener("input", calcDraw);
});

// ============================================================ screener
// OpenTerminal screens the whole US market; an index has a fixed, published
// membership, so the useful version here is the index's own constituents:
// sort them, filter them, and see which sectors are carrying the move. Drawn
// from the payload the market map already fetched - no second call, and the
// two panels can never disagree with the map.
const SCR = {by: "pct", dir: -1, q: "", sec: ""};
function scrRows(){
  const t = (MAPDATA && MAPDATA.tiles) || [];
  const q = SCR.q.toLowerCase();
  const rows = t.filter(r => (!SCR.sec || r.sector === SCR.sec)
                          && (!q || (r.sym + " " + r.sector).toLowerCase().includes(q)));
  const key = SCR.by;
  return rows.sort((a, b) => {
    const av = key === "sym" ? a.sym : key === "sector" ? a.sector : a[key],
          bv = key === "sym" ? b.sym : key === "sector" ? b.sector : b[key];
    if(av == null) return 1;
    if(bv == null) return -1;
    if(typeof av === "string") return SCR.dir * av.localeCompare(bv);
    return SCR.dir * (av - bv);
  });
}
function screenDraw(){
  const t = $("scr");
  if(!t || !MAPDATA) return;
  const all = MAPDATA.tiles || [];
  const sel = $("scrsec");
  const sectors = [...new Set(all.map(r => r.sector).filter(Boolean))].sort();
  if(sel.options.length !== sectors.length + 1){
    sel.innerHTML = `<option value="">all sectors</option>`
      + sectors.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    sel.value = SCR.sec;
  }
  const rows = scrRows();
  $("scrcount").textContent = rows.length === all.length
    ? `${all.length} in ${CUR}` : `${rows.length} of ${all.length}`;
  const head = (k, label) =>
    `<th class="${SCR.by === k ? "on" : ""}" data-k="${k}">${label}`
    + (SCR.by === k ? (SCR.dir < 0 ? " ▾" : " ▴") : "") + `</th>`;
  t.innerHTML = `<thead><tr>${head("sym","Symbol")}${head("sector","Sector")}`
    + `${head("weight","Weight")}${head("pct","Change")}</tr></thead><tbody>`
    + rows.map(r => {
        const col = r.pct == null ? "var(--ink-3)" : r.pct > 0 ? "var(--up)"
                  : r.pct < 0 ? "var(--down)" : "var(--ink-2)";
        const pct = r.pct == null ? "—"
                  : (r.pct >= 0 ? "+" : "−") + Math.abs(r.pct).toFixed(2) + "%";
        return `<tr><td class="sym">${esc(r.sym)}</td><td class="sec">${esc(r.sector||"")}</td>`
             + `<td>${r.weight == null ? "—" : r.weight.toFixed(2) + "%"}</td>`
             + `<td style="color:${col}">${pct}</td></tr>`;
      }).join("") + `</tbody>`;
  const known = all.filter(r => r.pct != null).length;
  $("scrnote").textContent = known < all.length ? `${known}/${all.length} streaming` : "";
}
document.querySelectorAll(".lbtn.tf").forEach(b =>
  b.addEventListener("click", () => chartTF(b.dataset.tf)));
(() => {                                   // restore the saved timeframe
  const tf = CH.tf || "15m";
  document.querySelectorAll(".lbtn.tf").forEach(b => b.classList.toggle("on", b.dataset.tf === tf));
  const lab = $("tflabel"); if(lab) lab.textContent = TF_LABEL[tf] || TF_LABEL["15m"];
})();

$("scr").addEventListener("click", e => {
  const th = e.target.closest("th[data-k]");
  if(!th) return;
  const k = th.dataset.k;
  if(SCR.by === k) SCR.dir = -SCR.dir; else { SCR.by = k; SCR.dir = (k === "sym" || k === "sector") ? 1 : -1; }
  screenDraw();
});
$("scrq").addEventListener("input", e => { SCR.q = e.target.value; screenDraw(); });
$("scrsec").addEventListener("change", e => { SCR.sec = e.target.value; screenDraw(); });

// ============================================================ sectors
// Each sector's move, weighted by what it is worth in the index rather than
// averaged flat - a 12% bank moving 1% is not the same event as a 0.4% one.
function sectorDraw(){
  const box = $("sectors");
  if(!box || !MAPDATA) return;
  const by = {};
  (MAPDATA.tiles || []).forEach(r => {
    if(r.pct == null || !r.weight) return;
    const s = by[r.sector] = by[r.sector] || {w: 0, wp: 0};
    s.w += r.weight; s.wp += r.weight * r.pct;
  });
  const rows = Object.entries(by).map(([name, v]) => ({name, w: v.w, pct: v.wp / v.w}))
                     .sort((a, b) => b.pct - a.pct);
  if(!rows.length){ box.innerHTML =
    `<p style="color:var(--ink-3);font-size:13px;margin:0">Waiting for the constituents.</p>`;
    return; }
  const max = Math.max(0.35, ...rows.map(r => Math.abs(r.pct)));
  box.innerHTML = rows.map(r => {
    const half = Math.min(50, Math.abs(r.pct) / max * 50);
    const up = r.pct >= 0;
    return `<div class="sectrow"><span>${esc(r.name)}</span>`
      + `<span class="sectbar"><u></u><i style="${up ? "left:50%" : `right:50%`};`
      + `width:${half}%;background:${up ? "var(--up)" : "var(--down)"}"></i></span>`
      + `<span class="sectval" style="color:${up ? "var(--up)" : "var(--down)"}">`
      + `${up ? "+" : "−"}${Math.abs(r.pct).toFixed(2)}%</span></div>`;
  }).join("");
}

// ============================================================ recap
// What this session has actually done, in one box: what the rules issued,
// what came of it, and where the index finished. Read from the state the
// page already has - the log on disk is the source for all of it.
function recapDraw(s){
  const box = $("recap");
  if(!box) return;
  const ses = s.session || {}, r = (s.indices || {})[CUR] || {}, tr = r.trend || {};
  const cell = (l, v, col) => `<div class="r"><div class="l">${esc(l)}</div>`
    + `<div class="v"${col ? ` style="color:${col}"` : ""}>${v}</div></div>`;
  const net = ses.net == null ? null : ses.net;
  box.innerHTML =
      cell("Tickets today", ses.issued == null ? "—" : ses.issued)
    + cell("Ran to target", ses.wins == null ? "—" : ses.wins, "var(--up)")
    + cell("Stopped out", ses.stops == null ? "—" : ses.stops, "var(--down)")
    + cell("Booked", ses.booked == null ? "—" : money(ses.booked),
           (ses.booked || 0) > 0 ? "var(--up)" : (ses.booked || 0) < 0 ? "var(--down)" : "")
    + cell("Open", ses.open == null ? "—" : money(ses.open),
           (ses.open || 0) > 0 ? "var(--up)" : (ses.open || 0) < 0 ? "var(--down)" : "")
    + cell("Net", net == null ? "—" : money(net),
           (net || 0) > 0 ? "var(--up)" : (net || 0) < 0 ? "var(--down)" : "")
    + cell(CUR + " today", tr.day_change == null ? "—"
           : (tr.day_change > 0 ? "+" : "") + num(tr.day_change, 0),
           tr.day_change > 0 ? "var(--up)" : tr.day_change < 0 ? "var(--down)" : "");
  const recent = (ses.recent || []).slice(0, 4);
  $("recaplist").innerHTML = recent.length
    ? recent.map(t => `<div>${esc(t.index)} ${esc(String(t.strike || ""))} `
        + `${esc(t.option_type || "")} &middot; ${esc(t.exit_time || "")} &middot; `
        + `<span style="color:${(t.pnl||0) >= 0 ? "var(--up)" : "var(--down)"}">`
        + `${t.pnl == null ? "no price" : money(t.pnl)}</span></div>`).join("")
    : `<div>Nothing has closed yet today.</div>`;
}

// ============================================================ headlines
// Fetched by the server from public RSS, several sources de-duplicated into
// one list. Opened in a new tab, and never trusted: the title is escaped and
// only http(s) links are kept (news.py does that check too).
let NEWS_AT = 0;
const ago = ts => {
  if(!ts) return "";
  const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  return m < 1 ? "just now" : m < 60 ? m + "m ago"
       : m < 1440 ? Math.round(m / 60) + "h ago" : Math.round(m / 1440) + "d ago";
};
async function newsFetch(force){
  const card = $("newscard");
  if(!card || card.hidden) return;
  if(!force && Date.now() - NEWS_AT < 300000) return;
  NEWS_AT = Date.now();
  try{
    const d = await (await fetch("/api/news", {cache:"no-store"})).json();
    const box = $("news");
    box.innerHTML = (d.items || []).slice(0, 12).map(it =>
      `<a href="${esc(it.link)}" target="_blank" rel="noopener noreferrer">`
      + `${esc(it.title)}<span class="m"><span class="src">${esc(it.source)}</span>`
      + `<span>${esc(ago(it.ts))}</span></span></a>`).join("")
      || `<p style="color:var(--ink-3);font-size:13px;margin:0">No headlines right now.</p>`;
    $("newsnote").textContent = d.note || "";
  }catch(e){}
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
  PANELS.forEach(([k, label], i) => out.push(
    {t:"Panel", label:(HIDDEN.has(k) ? "Show " : "Hide ") + label,
     sub:(i < 9 ? "⌥" + (i + 1) + " · " : "") + (HIDDEN.has(k) ? "hidden" : "showing"),
     run:() => togglePanel(k)}));
  TABS.forEach(t => out.push(
    {t:"Section", label:"Go to " + TAB_LABEL[t], sub:TAB === t ? "showing" : "",
     run:() => showTab(t)}));
  Object.entries(TF_LABEL).forEach(([tf, label]) => out.push(
    {t:"Chart", label:"Show " + label, sub:CH.tf === tf ? "showing" : "",
     run:() => chartTF(tf)}));
  out.push({t:"Go", label:"Journal - your trades and results", run:() => showTab("journal")});
  out.push({t:"Go", label:"How it works", run:() => location.href="/how-it-works"});
  out.push({t:"Go", label:"Zerodha connection", run:() => location.href="/connect"});
  out.push({t:"Go", label:"Results", run:() => location.href="/results"});
  out.push({t:"Do", label:"Reset the panel layout", sub:"order and visibility",
            run:() => { HIDDEN = new Set(); try{ localStorage.removeItem(PKEY); }catch(e){} resetLayout(); }});
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
  // Option/Alt + 1-9 shows or hides a panel, 0 brings them all back. Read from
  // e.code, because Alt+1 on a Mac types a character, not a digit.
  if(e.altKey && !e.metaKey && !e.ctrlKey && /^Digit[0-9]$/.test(e.code || "")){
    const n = +e.code.slice(5);
    e.preventDefault();
    if(n === 0){
      HIDDEN = new Set();
      try{ localStorage.setItem(PKEY, "[]"); }catch(err){}
      applyPanels(); try{ chartDraw(); }catch(err){} chainFetch(true);
    } else if(PANELS[n - 1]) togglePanel(PANELS[n - 1][0]);
    return;
  }
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
_BTST_CACHE = {"mtime": None, "data": None}


def _btst_study():
    """btst_study.py's saved results, re-read only when the file changes."""
    path = os.path.join(trade_log.log_dir(), "btst_study.json")
    try:
        mtime = os.path.getmtime(path)
        if _BTST_CACHE["mtime"] != mtime:
            with open(path) as fh:
                _BTST_CACHE.update(mtime=mtime, data=json.load(fh))
    except (OSError, ValueError):
        return None
    return _BTST_CACHE["data"]


def main():
    # Under launchd stdout is a file, not a terminal, so Python holds output
    # back in a block buffer - the log sat unchanged for a day across several
    # restarts. Line buffering writes each message as it happens.
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except Exception:
            pass
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
        # The key itself is NOT printed. This banner goes to a log file that
        # is read, copied and pasted around, and the link it used to print in
        # full opens account creation on whatever host this is published at.
        # The fingerprint is enough to tell which key is loaded.
        _k = config.WEB_ADMIN_KEY
        print(f"\n  OPERATOR PAGE — create accounts here, keep this link private:")
        print(f"    {base}/admin?key=<your WEB_ADMIN_KEY>")
        print(f"    (key loaded: {_k[:4]}…{len(_k)} chars — the value is in .env, not this log)")
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
