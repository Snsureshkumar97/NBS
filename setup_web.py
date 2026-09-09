#!/usr/bin/env python3
"""
setup_web.py — one command, then the website is ready
================================================================================
    python3 setup_web.py

Asks you three short questions, writes the .env file, invents the admin password
for you, and prints the exact two things to paste into Zerodha's developer
console. Nothing is sent anywhere — this runs entirely on your own machine and
only writes one local file.

Run it once. After that, `python3 web_server.py` prints the link you open each
morning, so there is nothing left to remember.
"""

import os
import secrets
import sys

import config
import kite_auth

LINE = "=" * 72


def ask(prompt, default=None, secret=False):
    hint = f" [{default}]" if default else ""
    while True:
        try:
            val = input(f"{prompt}{hint}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            sys.exit(1)
        if not val and default is not None:
            return default
        if val:
            return val
        print("  (needed)")


def main():
    print(LINE)
    print(" SET UP THE WEBSITE")
    print(LINE)
    print(" Three questions. Everything stays on this computer.\n")

    moved = kite_auth.migrate_env_to_home()
    if moved:
        print(f" Moved your existing settings into {moved}")
        print(" (so future downloads of the tool find them automatically)\n")

    # ---- 1. credentials ---------------------------------------------------
    have_key = config.KITE_API_KEY
    have_secret = config.KITE_API_SECRET
    if have_key and have_secret:
        print(f" Found your API key already saved ({have_key[:4]}…). Keeping it.")
        api_key, api_secret = have_key, have_secret
    else:
        print(" Your API key and secret come from https://developers.kite.trade/apps")
        print(" (open your app — the key is on the page, the secret is behind a")
        print("  'show' link). These do NOT expire.\n")
        api_key = ask(" API key")
        api_secret = ask(" API secret")

    # ---- 2. where it will live -------------------------------------------
    # Defaults to the port the desktop login already uses, so testing on this
    # machine needs NO change in the Kite console at all.
    local_default = f"http://127.0.0.1:{config.KITE_REDIRECT_PORT}"
    print("\n What address will the website be reached at?")
    print(f"   - just testing on this computer  ->  press Enter ({local_default})")
    print("   - a real site                    ->  e.g. https://mysignals.com")
    print("   - a plain server, no domain      ->  e.g. http://203.0.113.9:8080")
    print("\n No domain needed to test. Press Enter and it runs on this machine.")
    public = ask(" Website address", default=local_default)
    public = public.rstrip("/")
    is_local = "127.0.0.1" in public or "localhost" in public

    # ---- 3. accounts ------------------------------------------------------
    print("\n Should visitors need an account to see the signals?")
    print("   n  - no, anyone with the address can see it        (default)")
    print("   y  - yes, and let people register themselves")
    print("   p  - yes, but only accounts YOU create")
    accounts_mode = ask(" Require login? (n/y/p)", default="n").strip().lower()[:1]
    require_login = accounts_mode in ("y", "p")
    allow_signup = accounts_mode == "y"
    if allow_signup:
        print("\n Open signup means strangers act on these signals. The signup page")
        print(" states what this rule set actually measured, above the form, and asks")
        print(" them to confirm they read it. Please leave that in.")

    # ---- 4. admin password — invented for you ----------------------------
    admin = config.WEB_ADMIN_KEY or secrets.token_urlsafe(24)
    reused = bool(config.WEB_ADMIN_KEY)

    # ---- write ------------------------------------------------------------
    try:
        path = kite_auth.save_env({
            "KITE_API_KEY": api_key,
            "KITE_API_SECRET": api_secret,
            "WEB_PUBLIC_URL": public,
            "WEB_ADMIN_KEY": admin,
            "WEB_REQUIRE_LOGIN": "1" if require_login else "0",
            "WEB_ALLOW_SIGNUP": "1" if allow_signup else "0",
        })
    except RuntimeError as exc:
        print(f"\n Could not save: {exc}")
        sys.exit(1)

    callback = f"{public}/kite/callback"
    admin_url = f"{public}/admin?key={admin}"

    print("\n" + LINE)
    print(" SAVED")
    print(LINE)
    print(f" Written to: {path}")
    print(" That folder is in your home directory, NOT next to the code — so every")
    print(" future version of the tool finds it and never asks for your key again.")
    print(f" Admin password: {'kept the existing one' if reused else 'generated for you'}")

    already_ok = is_local and public == f"http://127.0.0.1:{config.KITE_REDIRECT_PORT}"

    print("\n" + LINE)
    if already_ok:
        print(" NOTHING LEFT TO DO — your Kite settings already work")
        print(LINE)
        print(f" Your app's Redirect URL is already  http://127.0.0.1:"
              f"{config.KITE_REDIRECT_PORT}/  for the desktop login, and the")
        print(" website answers on that same address. No change needed in the")
        print(" Kite console at all.")
        print("\n (When you later buy a domain, run this again and it will tell you")
        print("  the one line to update.)")
    else:
        print(" ONE THING LEFT — do this now, it takes 30 seconds")
        print(LINE)
        print(" 1. Open  https://developers.kite.trade/apps")
        print(" 2. Click your app")
        print(" 3. Set 'Redirect URL' to EXACTLY this line, then Save:\n")
        print(f"       {callback}\n")
        print(" If that doesn't match, the daily login will time out. It is the")
        print(" single most common thing to get wrong.")
        print("\n NOTE: Zerodha allows only ONE redirect URL per app, so changing it")
        print(" here means the desktop window's 'Login to Zerodha' button will stop")
        print(" working until you change it back. The website's own login replaces it.")

    print("\n" + LINE)
    print(" YOUR DAILY LINK — bookmark it, keep it private")
    print(LINE)
    print(f"\n   {admin_url}\n")
    print(" Open that once each morning (after about 07:30 IST / 10pm US Eastern),")
    print(" click 'Log in to Zerodha', done. The site is live for the rest of the day.")
    print("\n Anyone with that link can take over your data feed, so treat it like a")
    print(" password. It is not needed by visitors — only by you.")

    if require_login:
        print("\n" + LINE)
        print(" ACCOUNTS")
        print(LINE)
        if allow_signup:
            print(f" Anyone can register at   {public}/signup")
            print(f" Everyone logs in at      {public}/login")
        else:
            print(f" People log in at         {public}/login")
            print(" Nobody can register themselves. Create accounts from the admin")
            print(" page, or run once with --allow-signup to make the first one.")
        if not public.startswith("https://"):
            print("\n NOT on HTTPS. Fine while you test on this machine. On the open")
            print(" internet it means passwords cross the network readable — get a")
            print(" domain and put Caddy in front first. See DEPLOY.md.")

    print("\n" + LINE)
    print(" IF ANYTHING GOES WRONG")
    print(LINE)
    print("   python3 check_setup.py    — shows where it looked, what it found,")
    print("                               and what to do. Secrets are masked, so")
    print("                               the output is safe to share.")

    print("\n" + LINE)
    print(" START THE WEBSITE")
    print(LINE)
    print("   python3 web_server.py                  (this computer only)")
    print("   python3 web_server.py --host 0.0.0.0   (reachable from outside)")
    print(LINE + "\n")


if __name__ == "__main__":
    main()
