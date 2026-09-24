#!/usr/bin/env python3
"""The public pages' background scene is built once per history file, not once per page view.

Found 24 Sep 2026 while chasing "the tool feels slower": /healthz answered in 1 ms but
/login, /privacy and /terms took 0.25-0.4 s on the server, every time, because each one
re-read a compressed price-history file with pandas to draw 14 candles behind the page.
The candles only change when the daily history file does.

What must hold: the same candles as before (the builder is untouched), built again when
the file changes or a newer one appears, never built twice for the same file, and a
missing or unreadable file still gives the stand-in shape rather than an error.
"""
import gzip
import os
import sys
import tempfile
import time

HOME = tempfile.mkdtemp()
os.environ["HOME"] = HOME
os.environ["TRADING_TOOL_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nbs_site

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


HIST = os.path.join(HOME, "trading-tool-logs", "option_history")
builds = []
real = nbs_site._scene_candles_build
nbs_site._scene_candles_build = lambda files: (builds.append(list(files)) or real(files))


def write_history(name, base, minutes=15 * 20, mtime=None):
    """A day's index rows, one a minute, in the file's real columns."""
    os.makedirs(HIST, exist_ok=True)
    import datetime as dt
    rows = ["kind,index,ts,open,high,low,close"]
    t0 = dt.datetime(2026, 9, 24, 9, 15)
    for m in range(minutes):
        p = base + (m % 37) * 0.5 + m * 0.1
        rows.append(f"IDX,NIFTY,{(t0 + dt.timedelta(minutes=m)).isoformat()},{p},{p + 2},{p - 2},{p + 1}")
        rows.append(f"IDX,BANKNIFTY,{(t0 + dt.timedelta(minutes=m)).isoformat()},9{p},9{p},9{p},9{p}")
    path = os.path.join(HIST, name)
    with gzip.open(path, "wt") as fh:
        fh.write("\n".join(rows) + "\n")
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


print("1. NO HISTORY FILE: THE STAND-IN SHAPE, BUILT ONCE")
a = nbs_site._scene_candles()
b = nbs_site._scene_candles()
check("a scene is produced with 14 candles and no file", a.count('class="cb ') == 14, a.count('class="cb '))
check("the second view does not build it again", len(builds) == 1 and a == b, len(builds))

print("2. A HISTORY FILE APPEARS")
p1 = write_history("2026-09-23.csv.gz", 24000)
c = nbs_site._scene_candles()
check("the file's arrival is noticed: built again, from that file", len(builds) == 2 and builds[-1] == [p1] and c != a, builds[-1:])
check("14 candles from the real bars", c.count('class="cb ') == 14)
d = nbs_site._scene_candles()
check("and then left alone: the same object, no more builds however often a page is viewed", d is c and len(builds) == 2)
for _ in range(50):
    nbs_site._scene_candles()
check("fifty more page views: still two builds in all", len(builds) == 2)

print("3. THE FILE CHANGES, OR A NEWER ONE ARRIVES")
time.sleep(0.02)
write_history("2026-09-23.csv.gz", 24500, minutes=15 * 22)
e = nbs_site._scene_candles()
check("the same file rewritten (new size and time): built again, and differently", len(builds) == 3 and e != c)
p2 = write_history("2026-09-24.csv.gz", 25000)
f = nbs_site._scene_candles()
check("a newer day's file: built from IT", len(builds) == 4 and builds[-1] == [p1, p2], builds[-1:])
check("...and differs from yesterday's", f != e)
nbs_site._scene_candles()
check("then stays built", len(builds) == 4)
os.utime(p2, (time.time() + 60, time.time() + 60))               # same bytes, only the modification time moved
nbs_site._scene_candles()
check("a file touched without its size changing is still noticed (the time is part of the key)", len(builds) == 5)
orig_stat = os.stat
def flaky(path, *a, **k):
    if str(path).endswith(".csv.gz"):
        raise OSError("gone between listing and reading")
    return orig_stat(path, *a, **k)
os.stat = flaky
try:
    nbs_site._scene_candles()
    ok = True
except OSError:
    ok = False
finally:
    os.stat = orig_stat
check("a file that vanishes between the listing and the stat does not break the page", ok)
builds.clear()
nbs_site._scene_candles()
builds.clear()

print("4. A FILE THAT CANNOT BE READ")
with open(os.path.join(HIST, "2026-09-25.csv.gz"), "wb") as fh:
    fh.write(b"this is not a gzip file")
g = nbs_site._scene_candles()
check("a broken file gives the stand-in shape, not an error", g.count('class="cb ') == 14 and g == a, g[:60])
n = len(builds)
nbs_site._scene_candles()
check("and it is not retried on every view", len(builds) == n)
os.remove(os.path.join(HIST, "2026-09-25.csv.gz"))
h = nbs_site._scene_candles()
check("once it is gone the last good file's scene is back", h == f and len(builds) == n + 1)

print("5. THE PAGES USE IT")
builds.clear()
p = [nbs_site.login_page() for _ in range(5)]
check("five login pages, no rebuild of an unchanged scene", not builds and all(x == p[0] for x in p))
check("the page carries the scene", 'class="c3r"' in p[0] and f in p[0])

print("SCENE CACHE TEST PASSED" if not fails else f"SCENE CACHE TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
