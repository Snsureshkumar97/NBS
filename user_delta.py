"""
user_delta.py — each account's Delta Exchange India API keys
================================================================================
The crypto twin of user_kite.py, asked for on 20 Sep 2026 so the Bitcoin market
can place real orders on Delta Exchange India. Two differences from Zerodha
shape everything here:

  * Delta's prices, candles and option chain are PUBLIC. No key is needed to
    see the market, so an account with no Delta keys still gets every signal;
    the keys are only for live orders.
  * Delta keys are static (an API key and a secret the user creates once on
    delta.exchange), not a token renewed each morning. They live in the same
    user file as the Kite token, readable only by the server's own user, are
    never shown in a page or written to a log, and go with the account.

Every signed request is HMAC-SHA256 over method + timestamp + path + query +
body with the secret, sent as api-key / timestamp / signature headers; Delta
refuses a signature more than five seconds old, and refuses trading calls from
an IP not whitelisted on the key.
"""
import datetime as dt
import hashlib
import hmac
import json
import threading
import time
import urllib.parse

import requests

import accounts

BASE = "https://api.india.delta.exchange"
TESTNET = "https://cdn-ind.testnet.deltaex.org"
_CHECK_TTL = 300
_lock = threading.Lock()
_checks = {}                # email -> (when, state, detail)
_wallets = {}               # email -> (when, [{asset, available, balance}])
WALLET_TTL = 60

FIELDS = ("delta_key", "delta_secret", "delta_user_id", "delta_connected_at")


def sign(secret, method, timestamp, path, query="", body=""):
    """Delta's signature: hex HMAC-SHA256 of method + timestamp + path + query
    (with its leading "?") + body."""
    msg = f"{method}{timestamp}{path}{query}{body}"
    return hmac.new(str(secret).encode(), msg.encode(), hashlib.sha256).hexdigest()


def headers(key, secret, method, path, query="", body="", now=None):
    ts = str(int(now if now is not None else time.time()))
    return {"api-key": key, "timestamp": ts, "signature": sign(secret, method, ts, path, query, body),
            "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "TradePicker"}


def request(key, secret, method, path, params=None, body=None, timeout=15, session=None, base=BASE):
    """One signed call. Returns the decoded JSON body; raises DeltaError with
    Delta's own error code (and the client IP it saw, when it says) on
    failure, so a refused key or an unregistered IP reads as itself."""
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    data = json.dumps(body, separators=(",", ":")) if body is not None else ""
    h = headers(key, secret, method.upper(), path, query, data)
    r = (session or requests).request(method.upper(), base + path + query, headers=h,
                                      data=data or None, timeout=timeout)
    try:
        out = r.json()
    except ValueError:
        out = {}
    if r.status_code != 200 or not out.get("success", True):
        err = (out.get("error") or {}) if isinstance(out, dict) else {}
        raise DeltaError(err.get("code") or f"http {r.status_code}", (err.get("context") or {}).get("client_ip"))
    return out.get("result", out)


class DeltaError(Exception):
    def __init__(self, code, client_ip=None):
        super().__init__(code)
        self.code, self.client_ip = code, client_ip

    def __str__(self):
        c = self.code
        text = {"invalid_api_key": "Delta does not recognise that API key.",
                "invalid_signature": "The API secret does not match that key.",
                "ip_not_whitelisted_for_api_key": "This server's IP is not whitelisted on that API key.",
                "unauthorized": "Delta refused the key - check it has trading permission.",
                "expired_signature": "Delta rejected the request as too old - this server's clock is off.",
                }.get(c, f"Delta refused the request ({c}).")
        if self.client_ip:
            text += f" Delta saw the request come from {self.client_ip} - whitelist that IP on the key."
        return text


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


def app_ready():
    """Delta needs nothing configured on the server: every account brings its
    own keys. Kept for symmetry with user_kite.app_ready()."""
    return True, ""


def _clean(v):
    return str(v or "").strip()


def connect(email, key, secret, session=None, base=BASE):
    """Check the keys against Delta and keep them. (ok, message)."""
    key, secret = _clean(key), _clean(secret)
    if not key or not secret:
        return False, "Both the API key and the API secret are needed."
    if any(c.isspace() for c in key + secret):
        return False, "A key or secret cannot contain spaces."
    try:
        profile = request(key, secret, "GET", "/v2/profile", session=session, base=base) or {}
    except DeltaError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"Could not reach Delta Exchange: {type(exc).__name__}"
    who = str((profile or {}).get("id") or (profile or {}).get("user_id") or "")
    ok, msg = accounts.update_user(email, {
        "delta_key": key, "delta_secret": secret, "delta_user_id": who,
        "delta_connected_at": _now(),
    })
    if not ok:
        return False, msg
    with _lock:
        _checks.pop(email, None)
    return True, (f"Connected to Delta Exchange India as user {who}." if who else "Connected to Delta Exchange India.")


def disconnect(email):
    with _lock:
        _checks.pop(email, None)
    return accounts.update_user(email, {k: None for k in FIELDS})


def keys_for(email):
    """(key, secret) or None. The only place the secret is read."""
    user = accounts.get_user(email) or {}
    if user.get("delta_key") and user.get("delta_secret"):
        return user["delta_key"], user["delta_secret"]
    return None


def status(email, force=False, session=None, base=BASE):
    """("ok" | "missing" | "invalid" | "unknown", detail), checked against
    Delta at most once every five minutes."""
    keys = keys_for(email)
    if not keys:
        return "missing", "No Delta Exchange keys yet - needed only for live orders; prices need none."
    now = time.time()
    if not force:
        with _lock:
            cached = _checks.get(email)
        if cached and now - cached[0] < _CHECK_TTL:
            return cached[1], cached[2]
    try:
        request(keys[0], keys[1], "GET", "/v2/profile", session=session, base=base)
        state, detail = "ok", "Delta Exchange India keys accepted."
    except DeltaError as exc:
        state, detail = "invalid", str(exc)
    except Exception as exc:
        state, detail = "unknown", f"Couldn't check the Delta keys: {type(exc).__name__}"
    with _lock:
        _checks[email] = (now, state, detail)
    return state, detail


def wallet(email, force=False, session=None, base=BASE):
    """The account's balances on Delta, [{asset, available, balance}], read at
    most once a minute - for the user's own pages. The bot is never handed
    this; it gets yes/no from the executor's funds check."""
    keys = keys_for(email)
    if not keys:
        return None
    now = time.time()
    if not force:
        with _lock:
            cached = _wallets.get(email)
        if cached and now - cached[0] < WALLET_TTL:
            return cached[1]
    try:
        rows = request(keys[0], keys[1], "GET", "/v2/wallet/balances", session=session, base=base) or []
    except Exception:
        return None
    out = []
    for b in rows if isinstance(rows, list) else []:
        try:
            out.append({"asset": b.get("asset_symbol"), "available": round(float(b.get("available_balance") or 0), 2),
                        "balance": round(float(b.get("balance") or 0), 2)})
        except (TypeError, ValueError):
            continue
    with _lock:
        _wallets[email] = (now, out)
    return out


def summary(email):
    """Everything a page needs to describe the connection - and no key."""
    user = accounts.get_user(email) or {}
    state, detail = status(email)
    return {"state": state, "detail": detail, "connected": state == "ok",
            "user_id": user.get("delta_user_id") or "",
            "since": user.get("delta_connected_at") or "",
            "wallet": wallet(email) if state == "ok" else None}
