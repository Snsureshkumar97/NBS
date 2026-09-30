#!/usr/bin/env python3
"""config.home_config_dir() stopped touching the filesystem on every call.

30 Sep 2026: with several accounts' crypto feeds ticking, py-spy on the live
server showed 210 of 226 threads queued inside accounts.py's session_user()/
session_market()/get_user()/account_summary() - all of which call _load(),
which calls _path(), which calls home_config_dir(). The lock holder was
stuck INSIDE home_config_dir() itself: it did a real os.makedirs() +
os.chmod() on every single call, even though the directory never moves
while TRADING_TOOL_HOME stays the same - and it did this while holding
accounts._lock, the one global lock every request needs. Same shape of
slowdown as the 29 Sep session-lock incident (mtime-cached _load(), commit
c9b4d67) - this time a syscall under the lock rather than a JSON re-parse.

Fixed by memoizing the resolved directory, keyed by the TRADING_TOOL_HOME
override in effect (not one unconditional slot) - market_bot_test.py
switches that env var mid-run and switches it back, and a process-wide
single-value cache would have handed back a stale answer for a directory
that was never actually verified for the new value.
"""
import os
import sys
import tempfile

os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()      # nothing touches the real logs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


print("1. THE FIRST CALL FOR A GIVEN OVERRIDE STILL DOES THE REAL WORK")
config._home_config_dir_cache.clear()
d1 = tempfile.mkdtemp()
os.environ["TRADING_TOOL_HOME"] = d1
made, chmodded = [], []
real_makedirs, real_chmod = os.makedirs, os.chmod
os.makedirs = lambda p, exist_ok=False: (made.append(p), real_makedirs(p, exist_ok=exist_ok))[-1]
os.chmod = lambda p, m: (chmodded.append((p, m)), real_chmod(p, m))[-1]
try:
    got = config.home_config_dir()
    check("returns the override directory itself", got == d1, got)
    check("a fresh directory is actually created/verified and chmod'd the first time",
          made == [d1] and chmodded == [(d1, 0o700)], (made, chmodded))

    print("2. EVERY SUBSEQUENT CALL FOR THE SAME OVERRIDE SKIPS THE FILESYSTEM ENTIRELY")
    made.clear(); chmodded.clear()
    for _ in range(50):
        got = config.home_config_dir()
    check("the same directory is still returned", got == d1, got)
    check("...but os.makedirs/os.chmod were never called again - the whole reason for the fix: "
          "this used to run on EVERY request while holding accounts._lock",
          made == [] and chmodded == [], (made, chmodded))
finally:
    os.makedirs, os.chmod = real_makedirs, real_chmod

print("3. A DIFFERENT OVERRIDE (market_bot_test.py's mid-run switch) IS NOT SERVED THE FIRST ONE'S STALE ANSWER")
d2 = tempfile.mkdtemp()
os.environ["TRADING_TOOL_HOME"] = d2
got = config.home_config_dir()
check("switching TRADING_TOOL_HOME to a genuinely different directory returns THAT directory, "
      "not the previous one's cached path", got == d2 and got != d1, (got, d1))
check("...and it is a real, freshly-verified directory - not just trusted blindly", os.path.isdir(got), got)

print("4. SWITCHING BACK RETURNS THE FIRST DIRECTORY AGAIN, CORRECTLY - THE CACHE ENTRY WAS KEPT, NOT DROPPED")
os.environ["TRADING_TOOL_HOME"] = d1
got = config.home_config_dir()
check("back to the first override: back to the first directory", got == d1, got)

print("5. NO OVERRIDE AT ALL (the production case - every desktop install, and the live server) "
      "IS ITS OWN CACHE KEY, SEPARATE FROM ANY OVERRIDE A TEST SET")
os.environ.pop("TRADING_TOOL_HOME", None)
config._home_config_dir_cache.clear()
real_expanduser = os.path.expanduser
os.path.expanduser = lambda p: tempfile.mkdtemp() if p == "~" else real_expanduser(p)
try:
    made.clear()
    os.makedirs = lambda p, exist_ok=False: (made.append(p), real_makedirs(p, exist_ok=exist_ok))[-1]
    first = config.home_config_dir()
    check("resolves under the (faked) home directory the first time", first.endswith(".trading-tool"), first)
    check("touches the filesystem on that first call", made == [first], made)
    made.clear()
    second = config.home_config_dir()
    check("the second call with no override returns the same answer without touching the filesystem again",
          second == first and made == [], (second, made))
finally:
    os.path.expanduser = real_expanduser
    os.makedirs, os.chmod = real_makedirs, real_chmod

print("HOME CONFIG DIR CACHE TEST PASSED" if not fails else f"HOME CONFIG DIR CACHE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
