#!/usr/bin/env python3
"""
set_order_proxy.py — give one account's Zerodha ORDER calls their own static IP
================================================================================
The operator's tool, run on the server. Zerodha takes an API order only from an IP on the app owner's
developer account and shares one only with immediate family; the server's IP is the site owner's, so an
account with its OWN Kite Connect app (user_kite.app_for) sends its orders through an order proxy with
its own static address (user_kite.order_proxy_for - nbs-order-proxy, set up 5 Oct 2026).

    .venv/bin/python set_order_proxy.py <account email> <proxy url> <public ip>
    .venv/bin/python set_order_proxy.py <account email> --clear

Prints what the account now has - never a key, a secret or a token.
"""
import ipaddress
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accounts


def main(argv):
    if len(argv) == 2 and argv[1] == "--clear":
        email, patch = argv[0], {"kite_order_proxy": None, "kite_order_ip": None}
    elif len(argv) == 3:
        email, url, ip = argv
        if not url.startswith("http://") or " " in url:
            print("The proxy URL is the proxy's internal address, like http://10.160.0.3:8888")
            return 2
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            print("The public IP must be an IPv4 address, like 34.180.7.83")
            return 2
        patch = {"kite_order_proxy": url, "kite_order_ip": ip}
    else:
        print(__doc__.strip().split("\n\n")[1])
        return 2
    if not accounts.get_user(email):
        print(f"No account {email} on this server.")
        return 1
    ok, msg = accounts.update_user(email, patch)
    if not ok:
        print(f"Not saved: {msg}")
        return 1
    u = accounts.get_user(email) or {}
    own = bool(u.get("kite_api_key") and u.get("kite_api_secret"))
    print(f"{email}: order proxy {u.get('kite_order_proxy') or 'none'}, order IP {u.get('kite_order_ip') or 'none'}, "
          f"own Kite app saved: {'yes' if own else 'not yet - the proxy is used only once it is'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
