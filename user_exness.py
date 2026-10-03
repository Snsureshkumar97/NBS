"""
user_exness.py — each user's OWN Exness accounts, through their OWN MetaApi
================================================================================
The user, 3 Oct 2026: "how the users can connect their real account and it does show me
the balance real account and demo account should show the balance" - and, asked how:
"Their own MetaApi" (each user adds their Exness MT5 account to their own MetaApi account
and pastes a MetaApi token and the account IDs here - this server never sees an Exness
password), and the server's shared demo account (the one feeding Bitcoin and gold prices to
everyone, METAAPI_* in .env) shown to the admin only.

What is kept, per user, in the same user file as the Kite token and the Delta keys (readable
only by the server's own user, never shown in a page or a log, removed with the account):
the MetaApi token, and up to MAX_ACCOUNTS Exness accounts - each one's MetaApi id, region,
Exness server, and whether it is a DEMO or a REAL account (MetaApi's own answer, read when
it is added). Balances are read live, at most once every INFO_TTL seconds per user.

An account connected in MetaApi with the INVESTOR password is read-only: its balance shows,
but it can never take a live order (exness_orders.py refuses it and says why).

Endpoints - MetaApi's own API description (mt-client-api-v1.<region>.agiliumtrade.ai
/api-docs.json) and its provisioning API:
    GET  https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai/users/current/accounts/<id>
         -> region, state (DEPLOYED), connectionStatus (CONNECTED), server
    GET  https://mt-client-api-v1.<region>.agiliumtrade.ai/users/current/accounts/<id>/account-information
         -> type (ACCOUNT_TRADE_MODE_DEMO / _REAL / _CONTEST), currency, balance, equity,
            margin, freeMargin, leverage, broker, investorMode
"""
import datetime as dt
import re
import threading
import time

import requests

import accounts

PROVISIONING = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai"
CLIENT = "https://mt-client-api-v1.{region}.agiliumtrade.ai"
FIELDS = ("exness_token", "exness_accounts", "exness_connected_at")
MAX_ACCOUNTS = 3
INFO_TTL = 30
_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_lock = threading.Lock()
_info = {}                 # (email or "", account id) -> (when, info dict)


class ExnessError(RuntimeError):
    pass


def _now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M IST")


def kind_of(mt_type):
    """MetaApi's account type -> "demo" / "real" / "contest"."""
    t = str(mt_type or "").upper()
    return "demo" if t.endswith("DEMO") else "real" if t.endswith("REAL") else "contest" if t.endswith("CONTEST") else "unknown"


def _get(token, url, session=None, timeout=20):
    r = (session or requests).get(url, headers={"auth-token": token, "Accept": "application/json"}, timeout=timeout)
    if r.status_code == 401:
        raise ExnessError("MetaApi refused that token (401). Copy it again from MetaApi > API Access.")
    if r.status_code == 404:
        raise ExnessError("MetaApi has no account with that ID on this token (404). Copy the ID from MetaApi > "
                          "MT Accounts.")
    if r.status_code == 504:
        raise ExnessError("That account is not connected to Exness right now (504). In MetaApi it must show "
                          "Deployed and Connected.")
    if r.status_code >= 400:
        raise ExnessError(f"MetaApi answered HTTP {r.status_code}.")
    return r.json()


def parse_ids(text):
    """Account IDs pasted one per line, or separated by commas or spaces."""
    ids = [x for x in re.split(r"[\s,;]+", str(text or "").strip()) if x]
    out = []
    for x in ids:
        if x not in out:
            out.append(x)
    return out


def check_account(token, account_id, session=None):
    """MetaApi's own facts about one account, or raises ExnessError with a readable reason."""
    if not _ID.match(account_id):
        raise ExnessError(f"'{account_id[:20]}' does not look like a MetaApi account ID.")
    prov = _get(token, f"{PROVISIONING}/users/current/accounts/{account_id}", session) or {}
    server = str(prov.get("server") or "")
    if not server.lower().startswith("exness"):
        raise ExnessError(f"That account is on '{server or 'an unknown server'}', not Exness - this tool reads "
                          "Exness accounts only.")
    if prov.get("state") != "DEPLOYED" or prov.get("connectionStatus") != "CONNECTED":
        raise ExnessError(f"That account is {str(prov.get('state') or '?').lower()} / "
                          f"{str(prov.get('connectionStatus') or '?').lower()} in MetaApi - deploy it and wait for "
                          "Connected, then add it here.")
    region = str(prov.get("region") or "new-york")
    info = _get(token, f"{CLIENT.format(region=region)}/users/current/accounts/{account_id}/account-information",
                session) or {}
    return {"id": account_id, "region": region, "server": server, "kind": kind_of(info.get("type")),
            "currency": info.get("currency") or "USD", "investor": bool(info.get("investorMode"))}


def connect(email, token, ids_text, session=None):
    """Check the token and every account against MetaApi, then keep them. (ok, message)."""
    token = str(token or "").strip()
    if not token or any(c.isspace() for c in token):
        return False, "Paste the MetaApi token (MetaApi > API Access) - one line, no spaces."
    ids = parse_ids(ids_text)
    if not ids:
        return False, "Add at least one MetaApi account ID (MetaApi > MT Accounts, the ID on the card)."
    if len(ids) > MAX_ACCOUNTS:
        return False, f"Up to {MAX_ACCOUNTS} accounts - for example one demo and one real."
    kept = []
    for a in ids:
        try:
            kept.append(check_account(token, a, session))
        except ExnessError as exc:
            return False, f"{a[:8]}...: {exc}"
        except Exception as exc:
            return False, f"Could not reach MetaApi: {type(exc).__name__}"
    ok, msg = accounts.update_user(email, {"exness_token": token, "exness_accounts": kept,
                                           "exness_connected_at": _now()})
    if not ok:
        return False, msg
    with _lock:
        for k in [k for k in _info if k[0] == email]:
            _info.pop(k, None)
    names = ", ".join(f"{a['kind'].upper()} ({a['server']}{', read-only' if a['investor'] else ''})" for a in kept)
    return True, f"Connected: {names}."


def disconnect(email):
    with _lock:
        for k in [k for k in _info if k[0] == email]:
            _info.pop(k, None)
    return accounts.update_user(email, {k: None for k in FIELDS})


def token_for(email):
    """The MetaApi token, or None. The only place it is read."""
    return (accounts.get_user(email) or {}).get("exness_token") or None


def stored(email):
    """The accounts as kept - no token."""
    return list((accounts.get_user(email) or {}).get("exness_accounts") or [])


def account(email, account_id):
    return next((a for a in stored(email) if a.get("id") == account_id), None)


def live_info(token, acct, key, force=False, session=None):
    """Balance, equity, free margin, leverage... for one account, cached INFO_TTL seconds."""
    now = time.time()
    if not force:
        with _lock:
            hit = _info.get(key)
        if hit and now - hit[0] < INFO_TTL:
            return hit[1]
    out = {k: acct.get(k) for k in ("id", "kind", "server", "currency", "investor")}
    try:
        i = _get(token, f"{CLIENT.format(region=acct.get('region') or 'new-york')}/users/current/accounts/"
                        f"{acct['id']}/account-information", session) or {}
        out.update(ok=True, detail="", kind=kind_of(i.get("type")) if i.get("type") else out.get("kind"),
                   currency=i.get("currency") or out.get("currency"), investor=bool(i.get("investorMode")),
                   **{k: i.get(k) for k in ("balance", "equity", "margin", "freeMargin", "leverage")})
    except ExnessError as exc:
        out.update(ok=False, detail=str(exc))
    except Exception as exc:
        out.update(ok=False, detail=f"Could not reach MetaApi: {type(exc).__name__}")
    with _lock:
        _info[key] = (now, out)
    return out


def balances(email, force=False, session=None):
    """Every connected account with its live balance - for this user's own pages only."""
    token = token_for(email)
    if not token:
        return []
    return [live_info(token, a, (email, a["id"]), force, session) for a in stored(email)]


def server_account(force=False, session=None):
    """The server's shared demo account (the price feed), for the admin's own page only."""
    import exness_provider
    token, acct_id, region = exness_provider.creds()
    if not (token and acct_id):
        return None
    out = live_info(token, {"id": acct_id, "region": region, "kind": "demo", "server": "", "currency": "USD"},
                    ("", acct_id), force, session)
    return dict(out, shared=True)


def summary(email):
    """Everything a page needs - and no token."""
    user = accounts.get_user(email) or {}
    accts = balances(email) if user.get("exness_token") else []
    return {"connected": bool(accts), "since": user.get("exness_connected_at") or "", "accounts": accts}
