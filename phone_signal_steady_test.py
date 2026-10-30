#!/usr/bin/env python3
"""The Signal tab on a phone stays put through a bad connection (the user, 30 Oct 2026: "when i open it in mobile and as
soon as i go in signal section after some time i just go down it just start to reload so i coundt do anything").

A real browser (Playwright's Chromium, 390x844, touch) against the real page, served by web_server.py from a throwaway
home with a throwaway account; /api/state, /api/tick and /api/chain answered here, each a second late, from
phone_signal_fixture.json plus a chain and its sellers built by feeds._sellers_ahead. Then the connection goes bad:
for a few seconds a chain fetch comes back empty and the readings come without their sellers and CPR.
  - the chain drawing (about 1,100px on a phone) and the CPR stay on screen through the gap and the page does not shrink
    under the reader; they clear only once the data has really been gone for longer than the hold
  - each poll waits for its last answer: never two price or state requests in flight at once (they piled up)
  - no pull-to-refresh: a swipe down at the top no longer reloads the page
The holds are shortened for the test (the page's CHAIN_HOLD_MS / LIVE_HOLD_MS and its 20 s chain refetch rewritten).
Run with an interpreter that has Playwright (/usr/local/bin/python3 here). No network.
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SERVER = os.environ.get("PHONE_TEST_SERVER", os.path.join(HERE, "web_server.py"))   # another copy, to see it fail

fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)


try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Playwright is not installed for this interpreter - run with /usr/local/bin/python3")
    sys.exit(1)
import feeds

FX = json.load(open(os.path.join(HERE, "phone_signal_fixture.json")))
SPOT = float(FX["state"]["indices"]["NIFTY"]["spot"])
ATM = round(SPOT / 50) * 50
L = 1e5
OI = {-4: (5, 66.6), -3: (10, 50.4), -2: (30, 45), -1: (40, 100.7), 0: (35, 40), 1: (67.4, 30), 2: (30, 10), 3: (79.9, 8),
      4: (65.9, 5)}
CHAIN = {"strikes": [{"strike": ATM + 50 * k, "call_oi": c * L, "put_oi": p * L} for k, (c, p) in OI.items()]}
SELLERS = feeds._sellers_ahead(CHAIN, SPOT)
ROWS = [{"strike": s["strike"], "ce": {"oi": s["call_oi"], "ltp": 100.0}, "pe": {"oi": s["put_oi"], "ltp": 100.0}}
        for s in sorted(CHAIN["strikes"], key=lambda s: s["strike"])]
CPR = {"bc": ATM - 40, "tc": ATM + 30, "pivot": ATM - 5, "kind": "normal", "width": 70, "avg_width": 75,
       "where": "inside", "for": "today"}
REWRITE = [("const CHAIN_HOLD_MS = 90000, LIVE_HOLD_MS = 20000;", "const CHAIN_HOLD_MS = 8000, LIVE_HOLD_MS = 5000;"),
           ("Date.now() - SIGCHAIN.at > 20000", "Date.now() - SIGCHAIN.at > 1500")]
DELAY = 1.0


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main():
    home = tempfile.mkdtemp(prefix="phone-signal-")
    env = dict(os.environ, HOME=home, TRADING_TOOL_HOME=home, WEB_ALLOW_SIGNUP="0", KITE_API_KEY="",
               KITE_API_SECRET="", KITE_ACCESS_TOKEN="", ENABLE_CRYPTO="0")
    pw = "t-" + os.urandom(9).hex()
    made = subprocess.run([sys.executable, "-c", "import accounts, sys; accounts.create_user(sys.argv[1], sys.argv[2]); "
                           "t, e = accounts.authenticate(sys.argv[1], sys.argv[2]); print(t or e)",
                           "phone@example.test", pw], cwd=HERE, env=env, capture_output=True, text=True, timeout=60)
    token = made.stdout.strip().splitlines()[-1] if made.stdout.strip() else ""
    port = free_port()
    env["WEB_PUBLIC_URL"] = f"http://127.0.0.1:{port}"
    srv = subprocess.Popen([sys.executable, SERVER, "--port", str(port), "--mode", "free"], cwd=HERE, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    mode = {"live": True, "chain": True}
    flight = {"/api/tick": [0, 0], "/api/state": [0, 0]}          # now, most
    errors, rewrote = [], []
    try:
        for _ in range(120):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(0.5)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                            device_scale_factor=2)
            await ctx.add_cookies([{"name": "sd_session", "value": token, "url": base}])
            page = await ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))

            async def app(route):
                r = await route.fetch()
                body = await r.text()
                for a, b in REWRITE:
                    if a in body:
                        body = body.replace(a, b)
                        rewrote.append(a)
                await route.fulfill(response=r, body=body)

            def reading(rec):
                rec = dict(rec)
                rec["sellers"] = SELLERS if mode["live"] else None
                rec["cpr"] = CPR if mode["live"] else None
                return rec

            async def slow(route, path, make):
                f = flight[path]
                f[0] += 1
                f[1] = max(f[1], f[0])
                try:
                    await asyncio.sleep(DELAY)
                    await route.fulfill(status=200, content_type="application/json", body=json.dumps(make()))
                finally:
                    f[0] -= 1

            def state():
                s = json.loads(json.dumps(FX["state"]))
                s["indices"]["NIFTY"] = reading(s["indices"]["NIFTY"])
                return s

            def tick():
                t = json.loads(json.dumps(FX["tick"]))
                rd = t.get("reading") or {}
                if rd.get("index") == "NIFTY" and rd.get("public"):
                    rd["public"] = reading(rd["public"])
                return t

            async def chain(route):
                body = {"index": "NIFTY", "spot": SPOT, "rows": ROWS} if mode["chain"] \
                    else {"index": "NIFTY", "rows": [], "error": "no chain right now"}
                await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

            await page.route("**/app", app)
            await page.route("**/api/state*", lambda r: slow(r, "/api/state", state))
            await page.route("**/api/tick*", lambda r: slow(r, "/api/tick", tick))
            await page.route("**/api/chain*", chain)
            await page.goto(base + "/app#signal")
            for _ in range(80):
                if await page.evaluate("!!document.querySelector('#sigsellers svg')"):
                    break
                await asyncio.sleep(0.25)
            print("1. THE PAGE")
            check("the Signal tab's chain drawing is up", await page.evaluate("!!document.querySelector('#sigsellers svg')"))
            check("the test's shorter holds were written into the page", len(set(rewrote)) == len(REWRITE),
                  sorted(set(rewrote)))
            check("no pull-to-refresh: the page's overscroll is none",
                  await page.evaluate("getComputedStyle(document.documentElement).overscrollBehaviorY") == "none")
            await page.evaluate("document.querySelector('#sigsellers').scrollIntoView({block: 'center'})")
            await asyncio.sleep(1.5)

            async def sample():
                return await page.evaluate("""() => { const e = document.querySelector('#sigsellers');
                    return [!!(e && e.querySelector('svg')), e ? e.offsetHeight : 0, document.documentElement.scrollHeight,
                            Math.round(scrollY), !!(document.querySelector('#sigcpr') || {}).innerHTML]; }""")
            h0 = await sample()
            print("2. A BAD FEW SECONDS (empty chain fetches, readings without sellers or CPR)")
            seen = []
            for gap in range(2):
                mode.update(live=False, chain=False)
                for _ in range(30):                                    # 3 s - under both holds
                    seen.append(await sample())
                    await asyncio.sleep(0.1)
                mode.update(live=True, chain=True)
                for _ in range(20):
                    seen.append(await sample())
                    await asyncio.sleep(0.1)
            check("the drawing stayed on screen in every sample", all(s[0] for s in seen),
                  f"{sum(not s[0] for s in seen)} of {len(seen)} without it")
            check("...and the CPR", all(s[4] for s in seen), f"{sum(not s[4] for s in seen)} of {len(seen)} without it")
            drop = h0[2] - min(s[2] for s in seen)
            check("the page never shrank under the reader", drop < 100, f"shrank {drop}px (drawing {h0[1]}px)")
            moved = max(abs(s[3] - h0[3]) for s in seen)
            check("...nor moved them", moved < 100, f"{moved}px")
            print("3. REALLY GONE")
            mode.update(live=False, chain=False)
            await asyncio.sleep(12)                                    # past both shortened holds
            gone = await sample()
            check("once the data has been gone past the hold, the drawing and CPR clear (no frozen picture)",
                  not gone[0] and not gone[4], gone)
            print("4. ONE REQUEST AT A TIME")
            check("never two price requests in flight", flight["/api/tick"][1] <= 1, f"most at once {flight['/api/tick'][1]}")
            check("never two state requests in flight", flight["/api/state"][1] <= 1, f"most at once {flight['/api/state'][1]}")
            check("no script errors", not errors, errors[:3])
            await browser.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()
        shutil.rmtree(home, ignore_errors=True)


asyncio.run(main())
print()
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
