#!/usr/bin/env python3
"""
kite_login_helper.py
----------------------
THE MANUAL FALLBACK. Use kite_auth.py instead — it does all of this with one
click and no copy-pasting:

    python3 kite_auth.py          (or the "Login to Zerodha" button in gui.py)

This file is kept because the automatic flow depends on your Kite app's
Redirect URL pointing at http://127.0.0.1:5000/, and if that isn't set (or a
firewall blocks the local port) you still need a way in. Everything below
works exactly as it always did.

One-time-per-day helper to generate a Kite Connect access_token.

Zerodha's Kite Connect API does NOT support username/password login from a
script (by design, for security) — you must complete the login in a real
browser once per day and paste back a "request_token". This script automates
everything except that one manual browser step.

Usage:
    export KITE_API_KEY=your_api_key
    export KITE_API_SECRET=your_api_secret
    python kite_login_helper.py

It will:
  1. Print a login URL.
  2. You open it, log in with your Zerodha credentials + 2FA in the browser.
  3. After login, Zerodha redirects to your app's redirect URL with a
     `request_token=...` query parameter — copy that token.
  4. Paste it back into this script.
  5. It exchanges it for an access_token and prints an `export` line you can
     paste into your shell (or it can write a .env file for you).

The access_token is valid until ~6am IST the next day, so you need to redo
this once every trading day (Zerodha does not issue longer-lived tokens).
"""

import os
import sys

try:
    from kiteconnect import KiteConnect
except ImportError:
    print("kiteconnect not installed. Run: pip install kiteconnect")
    sys.exit(1)


def main():
    api_key = os.environ.get("KITE_API_KEY", "").strip()
    api_secret = os.environ.get("KITE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        api_key = input("Enter your Kite Connect API key: ").strip()
        api_secret = input("Enter your Kite Connect API secret: ").strip()

    kite = KiteConnect(api_key=api_key)

    print("\nStep 1: Open this URL in your browser and log in to Zerodha:\n")
    print(kite.login_url())
    print(
        "\nStep 2: After login, you'll be redirected to your app's redirect URL. "
        "Copy the 'request_token' value from that URL's query string.\n"
    )

    request_token = input("Paste the request_token here: ").strip()

    try:
        data = kite.generate_session(request_token, api_secret=api_secret)
    except Exception as e:
        print(f"\nFailed to generate session: {e}")
        sys.exit(1)

    access_token = data["access_token"]
    print("\nSuccess! Your access token (valid until ~6am IST tomorrow):\n")
    print(f"  {access_token}\n")
    print("Set it for this terminal session with:\n")
    print(f'  export KITE_API_KEY="{api_key}"')
    print(f'  export KITE_ACCESS_TOKEN="{access_token}"\n')

    write_env = input("Save these to a .env file so the tool picks them up automatically? (Y/n): ").strip().lower()
    if write_env in ("", "y", "yes"):
        # Write it NEXT TO THE CODE, not into whatever directory you happen
        # to be standing in — config.py looks here, so saving it beside the
        # scripts means gui.py/main.py find the token no matter where you
        # launch them from.
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        with open(env_path, "w") as f:
            f.write(f"KITE_API_KEY={api_key}\n")
            f.write(f"KITE_API_SECRET={api_secret}\n")
            f.write(f"KITE_ACCESS_TOKEN={access_token}\n")
        print(f"\nSaved to: {env_path}")
        print("gui.py and main.py will now pick this up automatically — no `export` needed.")
        print("Re-run this helper each trading morning (the token expires ~6am IST).")
        print("\nNOTE: if you later unzip a NEW copy of the tool into a different folder,")
        print("copy this .env across too, or just re-run this helper there.")


if __name__ == "__main__":
    main()
