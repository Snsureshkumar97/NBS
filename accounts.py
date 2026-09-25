"""
accounts.py — user accounts for the website
================================================================================
Signup, login, sessions and password storage, in the standard library only.

WHY THIS IS WRITTEN CAREFULLY
    A password file is the one part of a hobby project that can hurt people who
    are not you. People reuse passwords; a plaintext or weakly-hashed store
    hands an attacker credentials that open their email and their broker. So:

      * PBKDF2-HMAC-SHA256, 600,000 iterations, 16-byte random salt per user.
        Slow on purpose — that is the whole point of a password hash.
      * hmac.compare_digest everywhere a secret is compared, so a timing
        difference can't be used to guess a byte at a time.
      * Login says the same thing for "no such account" and "wrong password".
        Different messages let anyone harvest a list of your real users.
      * Failed logins are rate-limited per email and per IP.
      * Session tokens are 32 random bytes, stored hashed, and expire.

WHAT THIS DELIBERATELY DOES NOT DO
    No password reset by email — that needs a mail service, and a half-built
    reset flow is a way in, not a feature. Until you add one, resets are manual
    from the admin page.

    No payments, no plans, no billing. See the SEBI section of the README before
    money enters this picture.
"""

import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import threading
import time

import config

ITERATIONS = 600_000          # OWASP's floor for PBKDF2-SHA256 at time of writing
SALT_BYTES = 16
SESSION_TTL = 14 * 24 * 3600  # two weeks
MIN_PASSWORD = 10             # length beats complexity rules; see comment below
MAX_FAILS = 8                 # per email and per IP, within the window
FAIL_WINDOW = 15 * 60

_lock = threading.RLock()
_fails = {}                   # key -> [timestamps]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
def _path():
    return os.path.join(config.home_config_dir(), "users.json")


def _load():
    try:
        with open(_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"users": {}, "sessions": {}}
    data.setdefault("users", {})
    data.setdefault("sessions", {})
    return data


def _save(data):
    path = _path()
    tmp = path + ".tmp"
    # Written to a temp file and renamed, so a crash mid-write can't leave you
    # with a truncated user database and nobody able to log in.
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------
def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    """Constant-time, and never raises on a malformed record."""
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def password_problem(password):
    """Length is the only rule worth enforcing.

    Complexity rules ("one capital, one symbol") push people toward Password1!
    and are worse than a longer passphrase. NIST dropped them years ago.
    """
    if not password or len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if password.lower() in ("password12", "1234567890", "qwertyuiop", "passwordpassword"):
        return "That password is one of the first an attacker tries. Pick another."
    return None


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------
def _note_failure(*keys):
    now = time.time()
    with _lock:
        for k in keys:
            hits = [t for t in _fails.get(k, []) if now - t < FAIL_WINDOW]
            hits.append(now)
            _fails[k] = hits


def _is_locked(*keys):
    now = time.time()
    with _lock:
        for k in keys:
            hits = [t for t in _fails.get(k, []) if now - t < FAIL_WINDOW]
            _fails[k] = hits
            if len(hits) >= MAX_FAILS:
                return True
    return False


def _clear_failures(*keys):
    with _lock:
        for k in keys:
            _fails.pop(k, None)


# ---------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------
def user_count():
    with _lock:
        return len(_load()["users"])


def list_users():
    with _lock:
        data = _load()
    return sorted(
        ({"email": e, "created": u.get("created"), "last_login": u.get("last_login"),
          "disabled": bool(u.get("disabled"))} for e, u in data["users"].items()),
        key=lambda u: u["created"] or "")


def always_on_users():
    """Emails whose feed the tool holds open the whole session, page open or not.

    Every live account: running all session is automatic now (the user, 25 Sep
    2026: "it should be running all session, make it automatic"), where it used to
    be a switch on the Signal page that somebody had to remember. The old
    `always_on` flag on an account is ignored - it can neither turn this off nor
    be needed to turn it on.

    Read on a timer by the feed supervisor, so it is deliberately cheap and
    deliberately quiet: a disabled or expired account is skipped here rather than
    being started and then rejected somewhere further in, because a feed that
    exists for a disabled user is a feed calling Zerodha under a token that
    account should no longer be using. (An account with no Zerodha token today is
    skipped by the supervisor, not started.)
    """
    with _lock:
        data = _load()
    return sorted(e for e, u in data["users"].items()
                  if not u.get("disabled") and not _expired(u))


def create_user(email, password, now=None, expires=None):
    """Returns (ok, message). `expires` is an optional YYYY-MM-DD - the last
    day the account can sign in."""
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return False, "That doesn't look like an email address."
    problem = password_problem(password)
    if problem:
        return False, problem
    expiry, bad = _clean_expiry(expires, allow_past=False)
    if bad:
        return False, bad
    stamp = now or time.strftime("%Y-%m-%d %H:%M")
    with _lock:
        data = _load()
        if email in data["users"]:
            # Not "already registered" — that would confirm to a stranger which
            # addresses have accounts here.
            return False, "Could not create that account. Try logging in instead."
        data["users"][email] = {
            "password": hash_password(password),
            "created": stamp,
            "last_login": None,
            "disabled": False,
        }
        if expiry:
            data["users"][email]["expires"] = expiry
        _save(data)
    return True, ("Account created, with access until " + _fmt_day(expiry) + "."
                  if expiry else "Account created, with no expiry.")


def set_password(email, password):
    problem = password_problem(password)
    if problem:
        return False, problem
    email = (email or "").strip().lower()
    with _lock:
        data = _load()
        if email not in data["users"]:
            return False, "No such account."
        data["users"][email]["password"] = hash_password(password)
        # Every existing session for that account dies with the old password.
        data["sessions"] = {t: s for t, s in data["sessions"].items()
                            if s.get("email") != email}
        _save(data)
    return True, "Password changed, and all their sessions were signed out."


def set_disabled(email, disabled=True):
    email = (email or "").strip().lower()
    with _lock:
        data = _load()
        if email not in data["users"]:
            return False, "No such account."
        data["users"][email]["disabled"] = bool(disabled)
        if disabled:
            data["sessions"] = {t: s for t, s in data["sessions"].items()
                                if s.get("email") != email}
        _save(data)
    return True, ("Account disabled and signed out." if disabled else "Account enabled.")


def delete_user(email):
    email = (email or "").strip().lower()
    with _lock:
        data = _load()
        if email not in data["users"]:
            return False, "No such account."
        del data["users"][email]
        data["sessions"] = {t: s for t, s in data["sessions"].items()
                            if s.get("email") != email}
        _save(data)
    return True, "Account deleted."


def authenticate(email, password, ip=""):
    """Returns (session_token, error). Deliberately vague on failure."""
    email = (email or "").strip().lower()
    ekey, ikey = "e:" + email, "i:" + (ip or "?")
    if _is_locked(ekey, ikey):
        return None, ("Too many failed attempts. Wait fifteen minutes and try again.")

    with _lock:
        data = _load()
        user = data["users"].get(email)
        stored = user["password"] if user else None

    # Hash even when the account doesn't exist, so a missing account and a wrong
    # password take the same time — otherwise the response speed alone reveals
    # which addresses are registered.
    ok = verify_password(password or "", stored) if stored else (
        verify_password(password or "", hash_password("dummy")) and False)

    if not ok or (user and user.get("disabled")):
        _note_failure(ekey, ikey)
        return None, "Wrong email or password."

    if user and _expired(user):
        # Only reachable with the RIGHT password, so naming the reason tells a
        # stranger nothing they did not already hold. It does tell the person
        # locked out why, and who to ask - "wrong password" would send them
        # round in circles resetting a password that was never the problem.
        _clear_failures(ekey, ikey)
        return None, (f"This account's access ended on {_fmt_day(user.get('expires'))}. "
                      f"Ask the administrator to renew it.")

    _clear_failures(ekey, ikey)
    token = secrets.token_urlsafe(32)
    with _lock:
        data = _load()
        data["sessions"][_token_key(token)] = {
            "email": email, "expires": time.time() + SESSION_TTL}
        data["users"][email]["last_login"] = time.strftime("%Y-%m-%d %H:%M")
        _prune(data)
        _save(data)
    return token, None


def _token_key(token):
    """Sessions are stored hashed. Someone who reads users.json still can't
    impersonate a logged-in user with it."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _prune(data):
    now = time.time()
    data["sessions"] = {t: s for t, s in data["sessions"].items()
                        if s.get("expires", 0) > now}


def session_user(token):
    if not token:
        return None
    with _lock:
        data = _load()
        s = data["sessions"].get(_token_key(token))
        if not s or s.get("expires", 0) <= time.time():
            return None
        user = data["users"].get(s["email"])
        if not user or user.get("disabled") or _expired(user):
            # An expired account's open sessions stop here. Refusing only new
            # logins would leave an expired user signed in for up to the full
            # two-week session on the cookie they already hold.
            return None
        return s["email"]


def session_market(token):
    """Which market this login chose, or None if it has not chosen yet.

    Kept on the session rather than in a cookie so the answer cannot be edited
    by the browser, and so it dies with the login: signing in again asks again,
    which is the point of asking at the door.
    """
    if not token:
        return None
    with _lock:
        s = _load()["sessions"].get(_token_key(token))
    if not s or s.get("expires", 0) <= time.time():
        return None
    return s.get("market")


def set_session_market(token, market):
    """Record the market this login is working in. Returns True if it stuck."""
    if not token or not market:
        return False
    with _lock:
        data = _load()
        key = _token_key(token)
        s = data["sessions"].get(key)
        if not s or s.get("expires", 0) <= time.time():
            return False
        s["market"] = market
        _save(data)
    return True


def logout(token):
    if not token:
        return
    with _lock:
        data = _load()
        if data["sessions"].pop(_token_key(token), None) is not None:
            _save(data)


# ---------------------------------------------------------------------------
# per-user extras
# ---------------------------------------------------------------------------
# Everything above this line is about proving who someone is. What follows is
# about remembering things *for* them — currently just their Zerodha
# connection, kept here rather than in a second file because a user and their
# broker token have to be deleted in the same breath. A separate store would
# eventually be left holding a live token for an account that no longer exists.

def get_user(email):
    """A user record with the password hash removed.

    The hash never leaves this module. Callers want the extras — when they
    joined, whether they've connected a broker — and handing them the hash as
    well only creates places for it to be logged or rendered by accident.
    """
    email = (email or "").strip().lower()
    with _lock:
        user = _load()["users"].get(email)
    if not user:
        return None
    out = {k: v for k, v in user.items() if k != "password"}
    out["email"] = email
    return out


def update_user(email, patch):
    """Merge fields into a user record. Returns (ok, message).

    `password` and `disabled` are refused: both have their own function that
    also invalidates sessions, and a caller reaching them through here would
    silently skip that step, leaving a signed-out user still signed in.
    """
    email = (email or "").strip().lower()
    # role and expires join them: both decide who may sign in, so each has a
    # dedicated function with its own rules and session handling.
    reserved = {"password", "disabled", "role", "expires"}
    bad = reserved & set(patch or {})
    if bad:
        return False, f"Use the dedicated function for: {', '.join(sorted(bad))}."
    with _lock:
        data = _load()
        if email not in data["users"]:
            return False, "No such account."
        for k, v in (patch or {}).items():
            if v is None:
                data["users"][email].pop(k, None)
            else:
                data["users"][email][k] = v
        _save(data)
    return True, "Saved."


# ---------------------------------------------------------------------------
# roles and expiry
# ---------------------------------------------------------------------------
# An admin is an account with role "admin", set with set_role() on the server.
# There is deliberately no way to promote anyone from the admin tab, so a
# session that is not already an admin cannot become one by tampering with a
# request.
#
# Expiry is a date - the last day an account can sign in - stored YYYY-MM-DD and
# judged in India time, the clock this tool runs on. An expired account is
# refused at login AND its open sessions stop working, exactly like a disabled
# one. Admin accounts never expire: an admin whose access lapsed would have no
# way back in to renew anything, including their own access.

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def today_ist():
    return _dt.datetime.now(_IST).date()


def _clean_expiry(expires, allow_past=True):
    """(YYYY-MM-DD or None, error). Empty means no expiry."""
    if expires in (None, ""):
        return None, None
    try:
        day = _dt.date.fromisoformat(str(expires).strip())
    except ValueError:
        return None, "That expiry is not a date - use the calendar picker."
    if not allow_past and day < today_ist():
        return None, "That expiry date has already passed."
    return day.isoformat(), None


def _expired(user, today=None):
    """True once the expiry date is behind us. Valid THROUGH the expiry day."""
    if not user or user.get("role") == "admin":
        return False
    exp = user.get("expires")
    if not exp:
        return False
    try:
        return _dt.date.fromisoformat(exp) < (today or today_ist())
    except ValueError:
        return True          # an unreadable expiry fails closed, not open


def _days_left(exp, today=None):
    if not exp:
        return None
    try:
        return (_dt.date.fromisoformat(exp) - (today or today_ist())).days
    except ValueError:
        return None


def _fmt_day(exp):
    try:
        d = _dt.date.fromisoformat(exp)
        return f"{d.day} {d.strftime('%b %Y')}"
    except (TypeError, ValueError):
        return str(exp or "")


def is_admin(email):
    email = (email or "").strip().lower()
    with _lock:
        user = _load()["users"].get(email)
    return bool(user and user.get("role") == "admin" and not user.get("disabled"))


def set_role(email, role):
    """Make an account admin (role="admin") or ordinary (role=None)."""
    if role not in ("admin", None):
        return False, "Unknown role."
    email = (email or "").strip().lower()
    with _lock:
        data = _load()
        user = data["users"].get(email)
        if not user:
            return False, "No such account."
        if role:
            user["role"] = role
            user.pop("expires", None)       # an admin never expires
        else:
            user.pop("role", None)
        _save(data)
    return True, (f"{email} is now an admin." if role else f"{email} is no longer an admin.")


def set_expiry(email, expires):
    """Set, move or clear an account's expiry. A date already passed closes the
    account on the spot and signs it out; that is how access is cut."""
    email = (email or "").strip().lower()
    day, bad = _clean_expiry(expires, allow_past=True)
    if bad:
        return False, bad
    with _lock:
        data = _load()
        user = data["users"].get(email)
        if not user:
            return False, "No such account."
        if user.get("role") == "admin" and day:
            return False, ("An admin account does not expire - a lapsed admin "
                           "could not sign in to renew anything.")
        if day:
            user["expires"] = day
        else:
            user.pop("expires", None)
        ended = _expired(user)
        if ended:
            data["sessions"] = {t: s for t, s in data["sessions"].items()
                                if s.get("email") != email}
        _save(data)
    if not day:
        return True, f"{email} no longer expires."
    if ended:
        return True, (f"{email}'s access ended on {_fmt_day(day)} - the account "
                      f"is closed and signed out.")
    return True, f"{email} now has access until {_fmt_day(day)}."


def revoke_sessions(email):
    """Sign an account out everywhere without touching the account itself."""
    email = (email or "").strip().lower()
    with _lock:
        data = _load()
        if email not in data["users"]:
            return False, "No such account."
        before = len(data["sessions"])
        data["sessions"] = {t: s for t, s in data["sessions"].items()
                            if s.get("email") != email}
        n = before - len(data["sessions"])
        _save(data)
    return True, f"Signed {email} out of {n} session{'s' if n != 1 else ''}."


def account_summary(email):
    """What a signed-in user is told about their own access."""
    email = (email or "").strip().lower()
    with _lock:
        user = _load()["users"].get(email)
    if not user:
        return None
    admin = user.get("role") == "admin"
    exp = None if admin else user.get("expires")
    return {"email": email, "admin": admin, "expires": exp,
            "expires_label": _fmt_day(exp) if exp else None,
            "days_left": _days_left(exp), "expired": _expired(user)}


def admin_view():
    """Every account, for the admin tab - with an explicit list of fields.

    Not get_user(): that strips the password hash but keeps kite_token, the
    live Zerodha access token, and anything built on it would carry that
    token into the browser. What goes out is named here, one field at a time.
    """
    now, today = time.time(), today_ist()
    with _lock:
        data = _load()
    live = {}
    for s in data["sessions"].values():
        if s.get("expires", 0) > now:
            live[s.get("email")] = live.get(s.get("email"), 0) + 1
    out = []
    for email, u in data["users"].items():
        admin = u.get("role") == "admin"
        exp = None if admin else u.get("expires")
        status = ("disabled" if u.get("disabled")
                  else "expired" if _expired(u, today) else "active")
        out.append({
            "email": email, "admin": admin, "status": status,
            "expires": exp, "expires_label": _fmt_day(exp) if exp else None,
            "days_left": _days_left(exp, today),
            "created": u.get("created"), "last_login": u.get("last_login"),
            "zerodha": bool(u.get("kite_token")),
            "always_on": not u.get("disabled") and status != "expired",
            "sessions": live.get(email, 0)})
    out.sort(key=lambda r: (not r["admin"], r["created"] or ""))
    return out
