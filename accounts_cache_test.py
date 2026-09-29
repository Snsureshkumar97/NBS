"""Accounts: _load() caches users.json instead of re-reading it on every call.

29 Sep 2026 slowdown: session_user() runs on every single request and used to
open+parse users.json under the global lock every time. With enough concurrent
polling, threads piled up waiting on that lock (up to 133 of ~220 at once on
the live server). This tests that _load() now only touches disk when the file
has actually changed, that _save() keeps the cache in sync with what it wrote,
that an out-of-band edit is still picked up, and that none of this breaks
correctness under concurrent access.

Never touches the real user file - accounts._path is pointed at a temp file."""
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accounts

tmp = os.path.join(tempfile.mkdtemp(), "users.json")
accounts._path = lambda: tmp
PW = "correct horse battery"

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


# Count real disk reads by wrapping the built-in open() the way _load() calls
# it - json.load(f) after open(path). Easiest reliable hook: wrap json.load.
_reads = [0]
_real_json_load = json.load
def _counting_load(f):
    _reads[0] += 1
    return _real_json_load(f)
json.load = _counting_load


print("1. REPEATED _load() WITH NO CHANGE HITS DISK ONCE")
accounts.create_user("a@example.com", PW)          # first write, primes the cache
before = _reads[0]
for _ in range(20):
    accounts._load()
check("20 calls with nothing changed did not re-parse the file",
      _reads[0] == before, f"{_reads[0] - before} extra reads")

print("2. _save() KEEPS THE CACHE CURRENT WITHOUT A RE-READ")
before = _reads[0]
accounts.create_user("b@example.com", PW)
d = accounts._load()
check("the new user is visible immediately", "b@example.com" in d["users"])
check("no disk read was needed to see it", _reads[0] == before, f"{_reads[0] - before} extra reads")

print("3. AN OUT-OF-BAND EDIT IS STILL PICKED UP")
with open(tmp) as f:
    raw = _real_json_load(f)
raw["users"]["c@example.com"] = {"password": accounts.hash_password(PW), "created": "x",
                                  "last_login": None, "disabled": False}
# Force the mtime forward - some filesystems only offer 1s resolution and the
# edit above can land in the same tick as the last write.
os.utime(tmp, ns=(time.time_ns() + 2_000_000_000,) * 2)
with open(tmp, "w") as f:
    json.dump(raw, f)
os.utime(tmp, ns=(time.time_ns() + 2_000_000_000,) * 2)
d = accounts._load()
check("the hand-edited user shows up", "c@example.com" in d["users"])
tok, err = accounts.authenticate("c@example.com", PW)
check("and can actually log in", bool(tok) and not err, err or "")

print("4. FUNCTIONAL CORRECTNESS IS UNCHANGED WITH CACHING ON")
tok_a, err = accounts.authenticate("a@example.com", PW)
check("login still works", bool(tok_a) and not err, err or "")
check("session_user resolves it", accounts.session_user(tok_a) == "a@example.com")
accounts.logout(tok_a)
check("logout clears it", accounts.session_user(tok_a) is None)
ok, m = accounts.set_password("a@example.com", "another good password")
check("password change works", ok, m)
tok_a2, err = accounts.authenticate("a@example.com", "another good password")
check("new password works", bool(tok_a2) and not err, err or "")

print("5. CONCURRENT READERS AND WRITERS DON'T CORRUPT THE STORE")
stop_at = time.time() + 1.0
errors = []
def hammer_reads():
    while time.time() < stop_at:
        try:
            accounts.session_user(tok_a2)
            accounts._load()
        except Exception as e:
            errors.append(repr(e))

def hammer_writes(n):
    email = f"w{n}@example.com"
    try:
        accounts.create_user(email, PW)
        for _ in range(5):
            accounts.set_session_market(tok_a2, "nse_index")
    except Exception as e:
        errors.append(repr(e))

threads = [threading.Thread(target=hammer_reads) for _ in range(6)]
threads += [threading.Thread(target=hammer_writes, args=(n,)) for n in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join(timeout=5)
check("no exceptions under concurrent read/write", not errors, errors[:3])
d = accounts._load()
check("every concurrently-created account actually landed",
      all(f"w{n}@example.com" in d["users"] for n in range(6)),
      sorted(d["users"]))

json.load = _real_json_load

print()
print("ACCOUNTS CACHE TEST PASSED" if not fails else f"ACCOUNTS CACHE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
