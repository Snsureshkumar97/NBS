#!/usr/bin/env python3
"""
check_setup.py — why isn't it finding my key?
================================================================================
    python3 check_setup.py

Prints exactly where the tool looked for your settings, what it found in each
place, and what it ended up using. Every secret is masked, so the output is safe
to paste back into a chat.

If the tool keeps asking for your API key, run this. It will say which file it
expected to find and whether that file exists.
"""

import os
import sys

# Snapshot the shell environment BEFORE importing config — importing it loads
# the .env file into os.environ, which overwrites exactly the thing this script
# is trying to report on. Without this, "you have a blank KITE_API_KEY exported
# in your shell" is invisible, and that is one of the failure modes worth
# catching.
_SHELL_ENV = {k: os.environ.get(k) for k in
              ("KITE_API_KEY", "KITE_API_SECRET", "KITE_ACCESS_TOKEN",
               "WEB_PUBLIC_URL", "WEB_ADMIN_KEY", "KITE_REDIRECT_PORT")}

import config

LINE = "=" * 72
SECRET_KEYS = ("KITE_API_SECRET", "KITE_ACCESS_TOKEN", "WEB_ADMIN_KEY", "KITE_API_KEY")


def mask(key, value):
    """Never print a credential in full — this output is meant to be shareable."""
    if not value:
        return "(empty)"
    if key in SECRET_KEYS:
        return f"{value[:4]}…{value[-2:]}  ({len(value)} chars)" if len(value) > 8 else "(set, short)"
    return value


def read_keys(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError as exc:
        out["__error__"] = str(exc)
    return out


def main():
    print(LINE)
    print(" SETUP CHECK")
    print(LINE)
    print(f" Running from : {os.getcwd()}")
    print(f" Code lives in: {os.path.dirname(os.path.abspath(config.__file__))}")

    print("\n" + LINE)
    print(" WHERE IT LOOKED FOR YOUR SETTINGS")
    print(LINE)
    print(" (first match wins for each individual setting)\n")

    found_any = False
    for i, path in enumerate(config.dotenv_search_paths(), 1):
        exists = os.path.exists(path)
        print(f" {i}. {path}")
        if not exists:
            print("      -> not there")
            continue
        found_any = True
        keys = read_keys(path)
        if "__error__" in keys:
            print(f"      -> EXISTS but could not be read: {keys['__error__']}")
            continue
        if not keys:
            print("      -> EXISTS but is empty")
            continue
        print(f"      -> EXISTS, contains {len(keys)} setting(s):")
        for k, v in keys.items():
            print(f"           {k} = {mask(k, v)}")

    if not found_any:
        print("\n No settings file exists anywhere. That is why it keeps asking.")
        print(" Run:  python3 setup_web.py   — it will create one.")

    print("\n" + LINE)
    print(" WHAT THE TOOL IS ACTUALLY USING")
    print(LINE)
    rows = [
        ("KITE_API_KEY", config.KITE_API_KEY),
        ("KITE_API_SECRET", config.KITE_API_SECRET),
        ("KITE_ACCESS_TOKEN", config.KITE_ACCESS_TOKEN),
        ("WEB_PUBLIC_URL", config.WEB_PUBLIC_URL),
        ("WEB_ADMIN_KEY", config.WEB_ADMIN_KEY),
        ("KITE_REDIRECT_PORT", str(config.KITE_REDIRECT_PORT)),
    ]
    for k, v in rows:
        flag = "" if v else "   <-- MISSING"
        print(f" {k:<20}{mask(k, v)}{flag}")

    # A variable exported in the shell overrides the file, and a BLANK one used
    # to shadow it silently. Worth showing, because it is invisible otherwise.
    print("\n" + LINE)
    print(" SHELL ENVIRONMENT (these override the file)")
    print(LINE)
    shell_set = False
    for k, raw in _SHELL_ENV.items():
        if raw is not None:
            shell_set = True
            note = ""
            if raw == "":
                note = "   <-- blank. Harmless now, but remove it from your shell profile."
            print(f" {k:<20}{mask(k, raw) if raw else '(exported but EMPTY)'}{note}")
    if not shell_set:
        print(" nothing exported — the file is in charge, which is normal")

    print("\n" + LINE)
    print(" TOKEN")
    print(LINE)
    try:
        import kite_auth
        state, detail = kite_auth.token_status()
        print(f" {state.upper()} — {detail}")
        if state in ("missing", "expired"):
            print("\n Zerodha clears access tokens every morning; this is normal.")
            print(" Start the website and open the daily login link it prints.")
    except Exception as exc:
        print(f" could not check: {exc}")

    # ---- trade log --------------------------------------------------------
    print("\n" + LINE)
    print(" TRADE LOG")
    print(LINE)
    try:
        import trade_log
        d = trade_log.log_dir()
        csv_path = trade_log._log_path()
        print(f" Folder : {d}")
        print(f"          {'exists' if os.path.isdir(d) else 'DOES NOT EXIST'}")
        print(f" File   : {csv_path}")
        if not os.path.exists(csv_path):
            print("          NOT THERE YET")
            print("\n This file is created the moment a ticket is ISSUED — not when the")
            print(" tool starts, and not when you save a summary. So if it is missing,")
            print(" no signal has ever fired. That is the tool working, not failing:")
            print(" a read showing PREVIEW or NOT WORTH IT has not issued anything.")
        else:
            rows = trade_log._read_rows()
            opens = [r for r in rows if r.get("event") == "OPEN"]
            closes = [r for r in rows if r.get("event") == "CLOSE"]
            print(f"          {len(rows)} row(s) — {len(opens)} opened, {len(closes)} closed")
            if rows:
                print(f"          first {min(r.get('date','') for r in rows)}"
                      f"  ·  last {max(r.get('date','') for r in rows)}")
            if opens and not closes:
                print("\n Tickets were issued but none has closed yet — the summary only")
                print(" counts CLOSED trades, so it will read empty until one finishes.")
        summaries = sorted(f for f in os.listdir(d) if f.startswith("summary_")) \
            if os.path.isdir(d) else []
        print(f" Summaries: {len(summaries)}" + (f"  (latest {summaries[-1]})" if summaries else ""))
        if summaries and not os.path.exists(csv_path):
            print("          A saved summary with no trades.csv means the summary was")
            print("          written but had nothing to report.")
    except Exception as exc:
        print(f" could not check: {exc}")

    print("\n" + LINE)
    print(" WHAT TO DO")
    print(LINE)
    if not config.KITE_API_KEY or not config.KITE_API_SECRET:
        print(" Your API key and/or secret are not saved yet. Run:")
        print("     python3 setup_web.py")
        print(f" It will save them to {os.path.join(config.home_config_dir(), '.env')}")
        print(" which every future copy of the tool reads, so you enter them once.")
    elif not config.KITE_ACCESS_TOKEN:
        print(" Key and secret are saved. You just need today's login:")
        print("     python3 web_server.py     then open the admin link it prints")
    else:
        print(" Everything is saved. Start it with:")
        print("     python3 web_server.py")
    print(LINE + "\n")
    print(" This output is masked and safe to share.\n")


if __name__ == "__main__":
    main()
