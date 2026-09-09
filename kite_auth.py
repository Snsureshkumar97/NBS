"""
kite_auth.py — one-click daily login to Zerodha
================================================================================
Zerodha flushes every access token each morning (somewhere between roughly
05:00 and 07:30 IST), and there is no long-lived alternative. So a token has
to be obtained once per trading day, every trading day. That part is Zerodha's
design and nothing here can change it.

What CAN change is how much work it costs you. The old flow was:

    run a script -> copy a URL -> log in -> find request_token in the address
    bar -> copy it -> paste it back -> copy the access token -> paste it into
    .env

Seven steps, most of them copy-paste, all of them at 10pm your time.

This module reduces it to: click a button, log in to Zerodha the normal way,
done. It works by listening on a local port that Zerodha redirects back to
after login, so the request_token arrives by itself instead of being copied
out of the address bar by hand.

*** NO PASSWORD IS EVER STORED OR READ BY THIS CODE. ***
You type your Zerodha credentials into Zerodha's own page in your own
browser, exactly as you do when logging into Kite normally. This module never
sees them — it only receives the one-time request_token that Zerodha hands
back afterwards, and trades it for the day's access token.

ONE-TIME SETUP
    Your Kite Connect app's "Redirect URL" must point at the local port this
    listens on. Open https://developers.kite.trade/apps, edit your app, and
    set Redirect URL to exactly:

        http://127.0.0.1:5055/

    (or whatever KITE_REDIRECT_PORT is set to in config.py). That is the only
    manual configuration, and it is done once, not daily.
"""

import http.server
import os
import socket
import stat
import sys
import threading
import time
import urllib.parse
import webbrowser

import config

DEFAULT_TIMEOUT = 240


# ---------------------------------------------------------------------------
# The page Zerodha lands on after login
# ---------------------------------------------------------------------------
_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title><style>
 body{{background:#0b0d12;color:#eef1f6;font-family:-apple-system,Segoe UI,Roboto,sans-serif;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
 .c{{text-align:center;max-width:460px;padding:36px;background:#141924;border:1px solid #2a3141;border-radius:12px}}
 h1{{font-size:20px;margin:0 0 10px;color:{colour}}}
 p{{color:#9aa4b8;font-size:14px;line-height:1.6;margin:0}}
</style></head><body><div class="c"><h1>{title}</h1><p>{body}</p></div></body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        token = (params.get("request_token") or [None])[0]
        status = (params.get("status") or [None])[0]

        if token:
            self.server.result["token"] = token
            self._reply(200, "Logged in", "#2ecc71",
                        "You can close this tab and go back to the signal tool — "
                        "your token for today has been saved.")
            return
        if status == "error":
            msg = (params.get("message") or ["Zerodha reported a login error."])[0]
            self.server.result["error"] = msg
            self._reply(200, "Login failed", "#ef4444", msg)
            return

        # Browsers fetch /favicon.ico and similar on their own — those must not
        # be mistaken for the redirect, or the wait would end on the wrong one.
        self._reply(404, "Waiting for Zerodha", "#9aa4b8",
                    "This page is the signal tool's login listener. "
                    "Nothing to do here.")

    def _reply(self, code, title, colour, body):
        page = _PAGE.format(title=title, colour=colour, body=body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        try:
            self.wfile.write(page)
        except OSError:
            pass

    def log_message(self, *args):
        pass          # don't spray the terminal with request lines


def redirect_url(port=None):
    return f"http://127.0.0.1:{port or config.KITE_REDIRECT_PORT}/"


def port_is_free(port=None):
    port = port or config.KITE_REDIRECT_PORT
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# Ports that are unlikely to collide with anything. 5000 is the documented
# default but macOS hands it to AirPlay Receiver, and 5001 to AirPlay's
# companion — so both are skipped here rather than offered as a "fix" that
# fails the same way.
_FALLBACK_PORTS = (5055, 5056, 5057, 8765, 8766, 9099)


def suggest_free_port(exclude=None):
    """A port that is actually free right now, so the fix can name a number
    instead of telling you to go find one."""
    exclude = exclude or set()
    for p in _FALLBACK_PORTS:
        if p not in exclude and port_is_free(p):
            return p
    return None


def busy_port_help(port):
    """Everything needed to get unstuck, in one message: what's wrong, the
    exact port to switch to, and both places it has to be changed."""
    alt = suggest_free_port({port})
    lines = [
        f"Port {port} is already in use, so the login can't be caught.",
        "",
    ]
    if port in (5000, 5001) and sys.platform == "darwin":
        lines += [
            "On a Mac this is almost always AirPlay Receiver, which takes ports "
            "5000 and 5001. You can either turn it off in System Settings > "
            "General > AirDrop & Handoff, or just use a different port:",
            "",
        ]
    if alt:
        lines += [
            f"Use port {alt} instead — it's free right now. Two changes, and they",
            "must match each other:",
            "",
            f"  1. Add this line to your .env file (next to gui.py):",
            f"         KITE_REDIRECT_PORT={alt}",
            "",
            f"  2. At https://developers.kite.trade/apps, set your app's",
            f"     Redirect URL to exactly:",
            f"         http://127.0.0.1:{alt}/",
            "",
            "Then restart the tool and click Login again.",
        ]
    else:
        lines += [
            "Set KITE_REDIRECT_PORT in your .env file to a free port, and set your",
            "app's Redirect URL at https://developers.kite.trade/apps to",
            "http://127.0.0.1:<that port>/ to match.",
        ]
    return "\n".join(lines)


def capture_request_token(port=None, timeout=DEFAULT_TIMEOUT, should_cancel=None):
    """Serve the redirect URL until Zerodha bounces the browser back to it.

    Returns (request_token, error). Exactly one of the two is set.
    """
    port = port or config.KITE_REDIRECT_PORT
    try:
        server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    except OSError:
        return None, busy_port_help(port)

    server.result = {"token": None, "error": None}
    # A short poll interval rather than one blocking accept, so Cancel and the
    # timeout both stay responsive instead of hanging until a request lands.
    server.timeout = 0.5
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if should_cancel and should_cancel():
                return None, "Cancelled."
            server.handle_request()
            if server.result["token"] or server.result["error"]:
                break
    finally:
        server.server_close()

    if server.result["token"]:
        return server.result["token"], None
    if server.result["error"]:
        return None, server.result["error"]
    return None, (
        f"Timed out after {timeout}s waiting for Zerodha to redirect back.\n\n"
        f"The usual cause is that your Kite app's Redirect URL isn't pointing "
        f"here. Open https://developers.kite.trade/apps, edit your app, and set "
        f"Redirect URL to exactly:\n\n    {redirect_url(port)}\n\n"
        f"Then try again.")


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------
def env_path():
    """Where credentials get SAVED — a stable folder in your home directory,
    deliberately NOT next to the code.

    Writing beside the code meant every new download of the tool started blank
    and asked for the API key again, because the old .env was stranded in the
    previous folder. Saving to ~/.trading-tool/ means you enter it once, ever.

    An existing .env sitting beside the code still takes priority when READING
    (see config.dotenv_search_paths), so nobody's current setup breaks — but
    new writes go somewhere that lasts.
    """
    return os.path.join(config.home_config_dir(), ".env")


def migrate_env_to_home():
    """Move a legacy .env from beside the code into the home folder, once.

    Silent and idempotent: it only copies keys that aren't already saved, so
    running it can never clobber a newer token with an older one.
    """
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    target = env_path()
    if not os.path.exists(legacy) or os.path.realpath(legacy) == os.path.realpath(target):
        return None
    try:
        existing = {}
        if os.path.exists(target):
            with open(target) as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        existing[line.split("=", 1)[0].strip()] = True
        moved = {}
        with open(legacy) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k and k not in existing:
                    moved[k] = v.strip().strip('"').strip("'")
        if moved:
            save_env(moved, path=target)
            return target
    except OSError:
        pass
    return None


def save_env(values, path=None):
    """Update just these keys, leaving anything else in the file untouched —
    overwriting the whole file would silently discard settings you'd added."""
    path = path or env_path()
    lines, seen = [], set()
    if os.path.exists(path):
        try:
            with open(path) as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []

    out = []
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in values and not line.strip().startswith("#"):
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in values.items():
        if key not in seen:
            out.append(f"{key}={val}")

    try:
        with open(path, "w") as f:
            f.write("\n".join(out).rstrip() + "\n")
        # This file holds a live trading token. Owner-only, on any OS that
        # honours it.
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return path
    except OSError as exc:
        raise RuntimeError(f"Couldn't write {path}: {exc}")


# ---------------------------------------------------------------------------
# Token checks and exchange
# ---------------------------------------------------------------------------
def _kite(api_key, access_token=None):
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        raise RuntimeError("kiteconnect isn't installed. Run: pip install kiteconnect")
    k = KiteConnect(api_key=api_key)
    if access_token:
        k.set_access_token(access_token)
    return k


def token_status(api_key=None, access_token=None):
    """Is today's token actually alive?

    Returns (state, detail) where state is "ok" | "missing" | "expired" |
    "unknown". Checked by asking Zerodha rather than by looking at the clock:
    a token can be revoked early, and a token generated before the morning
    flush dies at the flush regardless of when you made it.
    """
    api_key = api_key or config.KITE_API_KEY
    access_token = access_token if access_token is not None else config.KITE_ACCESS_TOKEN
    if not api_key:
        return "missing", "No KITE_API_KEY set."
    if not access_token:
        return "missing", "No access token yet for today."
    try:
        profile = _kite(api_key, access_token).profile()
        name = (profile or {}).get("user_name") or (profile or {}).get("user_id") or ""
        return "ok", f"Logged in as {name}." if name else "Token is valid."
    except Exception as exc:
        text = str(exc).lower()
        if "token" in text or "session" in text or "expired" in text or "403" in text:
            return "expired", "Today's token has expired — Zerodha clears them each morning."
        return "unknown", f"Couldn't verify the token: {exc}"


def exchange(api_key, api_secret, request_token):
    data = _kite(api_key).generate_session(request_token, api_secret=api_secret)
    return data["access_token"]


# ---------------------------------------------------------------------------
# The whole flow
# ---------------------------------------------------------------------------
def one_click_login(api_key=None, api_secret=None, port=None, timeout=DEFAULT_TIMEOUT,
                    open_browser=True, on_status=None, should_cancel=None):
    """Log in and save the day's token.

    Returns (access_token, error). Exactly one is set. `on_status` is called
    with short progress strings so a GUI can narrate the wait instead of
    looking frozen.
    """
    def say(msg):
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    api_key = (api_key or config.KITE_API_KEY or "").strip()
    api_secret = (api_secret or config.KITE_API_SECRET or "").strip()
    port = port or config.KITE_REDIRECT_PORT

    if not api_key or not api_secret:
        return None, ("Your API key and secret are missing. Both come from your app at "
                      "https://developers.kite.trade/apps and only need to be entered once "
                      "— they don't expire daily, unlike the access token.")

    try:
        url = _kite(api_key).login_url()
    except RuntimeError as exc:
        return None, str(exc)

    if not port_is_free(port):
        return None, busy_port_help(port)

    # The listener has to be up BEFORE the browser opens — Zerodha can bounce
    # back fast enough that a race here would drop the token.
    result = {}
    say("Starting the local listener…")
    thread = threading.Thread(
        target=lambda: result.update(
            zip(("token", "error"), capture_request_token(port, timeout, should_cancel))),
        daemon=True)
    thread.start()
    time.sleep(0.25)

    say("Opening Zerodha in your browser — log in there as normal.")
    opened = False
    if open_browser:
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
    if not opened:
        say("Couldn't open a browser automatically. Open this URL yourself:\n" + url)

    thread.join(timeout + 5)
    token, error = result.get("token"), result.get("error")
    if error:
        return None, error
    if not token:
        return None, "Login didn't complete."

    say("Got the login back from Zerodha — exchanging it for today's token…")
    try:
        access_token = exchange(api_key, api_secret, token)
    except Exception as exc:
        return None, (f"Zerodha rejected the exchange: {exc}\n\n"
                      f"The usual cause is a wrong API secret, or a request_token that was "
                      f"already used (they're single-use — just log in again).")

    try:
        path = save_env({"KITE_API_KEY": api_key, "KITE_API_SECRET": api_secret,
                         "KITE_ACCESS_TOKEN": access_token})
    except RuntimeError as exc:
        return None, str(exc)

    config.apply_credentials(api_key, api_secret, access_token)
    say(f"Saved to {path}. You're logged in for the rest of today's session.")
    return access_token, None


# ---------------------------------------------------------------------------
# Command line — for Termux, or any time you'd rather not open the window
# ---------------------------------------------------------------------------
def main():
    print("Zerodha one-click login")
    print("=" * 60)
    api_key = config.KITE_API_KEY or input("API key: ").strip()
    api_secret = config.KITE_API_SECRET or input("API secret: ").strip()

    state, detail = token_status(api_key, config.KITE_ACCESS_TOKEN)
    print(f"\nCurrent token: {state} — {detail}")
    if state == "ok":
        if input("\nAlready valid. Log in again anyway? (y/N): ").strip().lower() not in ("y", "yes"):
            return

    print(f"\nMake sure your app's Redirect URL is exactly:  {redirect_url()}")
    print("(https://developers.kite.trade/apps -> your app -> Redirect URL)\n")

    token, error = one_click_login(api_key, api_secret, on_status=lambda m: print("  " + m))
    if error:
        print("\nFAILED\n" + error)
        raise SystemExit(1)
    print("\nDone. gui.py and main.py will pick this up automatically.")


if __name__ == "__main__":
    main()
