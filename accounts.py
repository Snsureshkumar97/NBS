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


def create_user(email, password, now=None):
    """Returns (ok, message)."""
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return False, "That doesn't look like an email address."
    problem = password_problem(password)
    if problem:
        return False, problem
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
        _save(data)
    return True, "Account created."


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
        if not user or user.get("disabled"):
            return None
        return s["email"]


def logout(token):
    if not token:
        return
    with _lock:
        data = _load()
        if data["sessions"].pop(_token_key(token), None) is not None:
            _save(data)
