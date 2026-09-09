"""
user_kite.py — each user's own Zerodha connection
================================================================================
The website used to have one broker session: yours. You logged in from /admin
each morning, the server fetched candles with your token, and everybody who
could reach the page saw what your account could see.

This replaces that with one connection per user. Your Kite Connect *app* — the
API key and secret — still belongs to the server, because that is what an app
is. What changes is the access token: each person completes Zerodha's login
themselves, and the token that comes back is stored against their account and
used only for their own data.

WHY IT IS WORTH THE EXTRA MOVING PARTS
    * Zerodha's terms are between Zerodha and the person whose account it is.
      Serving three strangers from your session makes their data your problem
      and your rate limit their bottleneck.
    * A user can revoke you without anybody else losing the feed.
    * Nobody's access outlives their account: delete the account and the token
      goes with it, because it lives in the same record.

WHAT A TOKEN IS AND ISN'T
    A Kite access token can read positions and place orders. THIS TOOL PLACES
    NO ORDERS — but the token stored here could, so it is treated as a
    credential: written only to users.json (mode 0600, same file the password
    hashes live in), never rendered into a page, never logged, and never sent
    to a browser.

    It also expires every morning. Zerodha clears every access token around
    07:30 IST regardless of when it was issued, so "connected" is a statement
    about today and nothing more. That is their rule, not this tool's, and it
    is why the connect screen says "each morning" rather than "once".
"""

import datetime as dt
import threading
import time
import urllib.parse

import accounts
import config
import kite_auth

# Asking Zerodha "is this token alive?" is a network round trip. The signal
# page polls every few seconds, so an uncached check would mean a profile()
# call per poll per user — enough to get rate-limited for no information, since
# the answer changes about once a day.
_CHECK_TTL = 120
_checks = {}                  # email -> (checked_at, state, detail)
_lock = threading.RLock()

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _today_ist():
    return dt.datetime.now(IST).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# the app itself
# ---------------------------------------------------------------------------
def app_ready():
    """Can anyone connect at all? Returns (ok, reason).

    Separated from a user's own state because the failure reads completely
    differently: a missing API key is the operator's job, and telling a user
    to "try connecting again" when the server has no credentials would send
    them round a loop they cannot exit.
    """
    if not config.KITE_API_KEY or not config.KITE_API_SECRET:
        return False, ("This server has no Kite Connect app configured. "
                       "The site owner needs to set KITE_API_KEY and "
                       "KITE_API_SECRET before anyone can connect.")
    if not config.web_callback_url():
        return False, ("This server does not know its own public address, so "
                       "Zerodha has nowhere to send you back to. The site "
                       "owner needs to set WEB_PUBLIC_URL.")
    return True, ""


def login_url(nonce):
    """Zerodha's login page, carrying a one-time nonce back to the callback.

    The nonce is how the callback knows which of its users started this login.
    Without it the callback would have to trust whoever arrives holding a
    request token, and a stranger could hand us a token from their own account
    and have it filed under somebody else's email.
    """
    url = kite_auth._kite(config.KITE_API_KEY).login_url()
    return url + "&redirect_params=" + urllib.parse.quote(
        urllib.parse.urlencode({"n": nonce}))


# ---------------------------------------------------------------------------
# a user's connection
# ---------------------------------------------------------------------------
def connect(email, request_token):
    """Exchange a request token for this user's access token. (ok, message)."""
    ok, why = app_ready()
    if not ok:
        return False, why
    try:
        access = kite_auth.exchange(config.KITE_API_KEY, config.KITE_API_SECRET,
                                    request_token)
    except Exception as exc:
        return False, f"Zerodha refused the login: {exc}"

    # Ask who it was, so the account page can say "connected as ZX1234" rather
    # than just "connected". A user with two Zerodha logins needs to be able to
    # tell which one this is.
    who = ""
    try:
        profile = kite_auth._kite(config.KITE_API_KEY, access).profile() or {}
        who = profile.get("user_id") or profile.get("user_name") or ""
    except Exception:
        pass

    ok, msg = accounts.update_user(email, {
        "kite_token": access,
        "kite_user_id": who,
        "kite_connected": _today_ist(),
        "kite_connected_at": time.strftime("%Y-%m-%d %H:%M"),
    })
    if not ok:
        return False, msg
    with _lock:
        _checks.pop(email, None)
    return True, (f"Connected as {who}." if who else "Connected.")


def disconnect(email):
    with _lock:
        _checks.pop(email, None)
    return accounts.update_user(email, {
        "kite_token": None, "kite_user_id": None,
        "kite_connected": None, "kite_connected_at": None,
    })


def token_for(email):
    """The user's access token, or None. The only place it is read."""
    user = accounts.get_user(email)
    return (user or {}).get("kite_token") or None


def status(email, force=False):
    """("ok" | "missing" | "stale" | "expired" | "unknown", detail).

    "stale" means the token was issued on an earlier day, which Zerodha
    guarantees is dead — answered from the stored date without a network call,
    because there is no point spending a request to be told something we can
    work out from the calendar.
    """
    user = accounts.get_user(email) or {}
    token = user.get("kite_token")
    if not token:
        return "missing", "Not connected to Zerodha yet."
    if user.get("kite_connected") != _today_ist():
        return "stale", ("Connected on " + str(user.get("kite_connected"))
                         + ". Zerodha clears every token each morning, so it "
                           "needs doing again today.")
    now = time.time()
    if not force:
        with _lock:
            cached = _checks.get(email)
        if cached and now - cached[0] < _CHECK_TTL:
            return cached[1], cached[2]
    try:
        state, detail = kite_auth.token_status(config.KITE_API_KEY, token)
    except Exception as exc:
        state, detail = "unknown", f"Couldn't check the connection: {exc}"
    with _lock:
        _checks[email] = (now, state, detail)
    return state, detail


def summary(email):
    """Everything a page needs to describe the connection — and no token."""
    user = accounts.get_user(email) or {}
    state, detail = status(email)
    return {
        "state": state,
        "detail": detail,
        "connected": state == "ok",
        "user_id": user.get("kite_user_id") or "",
        "since": user.get("kite_connected_at") or "",
    }
