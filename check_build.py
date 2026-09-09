#!/usr/bin/env python3
"""
check_build.py — which version of the tool is in THIS folder?

Run it from the folder you actually launch the app from:

    python3 check_build.py

It reads the source files beside it and reports which features are present.
If something says MISSING, the folder you are running from is not the folder
you unzipped into — which is the usual reason a new build "changes nothing".
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

CHECKS = [
    ("Price ladder (levels at true distance)", "skin.py", "def _ladder("),
    ("Reward:risk on the ticket", "skin.py", "REWARD : RISK"),
    ("Target-hit tick + timestamp", "skin_bridge.py", "def _levels("),
    ("Progress bars on unhit levels", "gui.py", "level_progress"),
    ("Lots selector (1-5)", "skin_bridge.py", "lots_var"),
    ("Combined daily P&L strip", "gui.py", "def portfolio("),
    ("Daily limits switch", "gui.py", "def entry_block("),
    ("No entries before 09:20", "config.py", "NO_NEW_TRADES_BEFORE"),
    ("Pre-open auction bars dropped", "main.py", "def drop_preopen("),
    ("No ticket without chain data", "config.py", "REQUIRE_REACHABILITY"),
    ("Last data shown when market shut", "gui.py", "_closed_snapshot_done"),
    ("Hover no longer repaints everything", "skin.py", "def _redraw_zone("),
    ("Demo mode (--demo)", "gui.py", "def _load_demo("),
    ("Light / dark theme switch", "theme.py", "PALETTES"),
    ("Confirmation measured in seconds", "config.py", "SIGNAL_CONFIRM_SECONDS"),
    ("Ticket gap knob present (now 0)", "config.py", "MIN_MINUTES_BETWEEN_TICKETS"),
    ("A flip no longer closes a trade", "config.py", "CLOSE_ON_SIGNAL_FLIP"),
    ("Auto re-arm obeys the daily gates", "gui.py", "def _rearm("),
    ("Open tickets squared at the bell", "gui.py", "def _close_open_at_bell("),
    ("Queue pump survives an error", "gui.py", "def _pump_queue("),
    ("Unknown interval now raises", "data_providers.py", "unknown interval"),
    ("Ticket says WHY it is waiting", "gui.py", "def hold(code, short, why)"),
    ("Auto re-arm has an anti-runaway floor", "gui.py", "REARM_MIN_SECONDS"),
    ("Single exit target is a setting", "config.py", "EXIT_AT_TARGET"),
    ("One-target study on your own log", "target_study.py", "IF THERE HAD BEEN ONE TARGET"),
    ("Strength bars on the vote list", "skin_bridge.py", "def _strength("),
]


def main():
    print(f"\nReading the files in:\n  {HERE}\n")
    missing = 0
    for label, fname, marker in CHECKS:
        path = os.path.join(HERE, fname)
        try:
            with open(path, encoding="utf-8") as f:
                ok = marker in f.read()
        except OSError:
            ok = False
        if not ok:
            missing += 1
        print(f"  [{'ok' if ok else '  '}] {label}"
              f"{'' if ok else '   <- MISSING'}")

    print()
    if missing == 0:
        print("This folder has the latest build.")
        print("The ladder only draws when a ticket EXISTS - with 'NO SIGNAL YET'")
        print("there are no levels to plot, so you still see the empty boxes.")
        print("To look at it right now, market open or not:")
        print("\n    python3 gui.py --demo\n")
    else:
        print(f"{missing} feature(s) missing - this is an OLDER copy.")
        print("You are running from a different folder than the one you")
        print("unzipped into. Find where the app actually starts from:")
        print("\n    python3 -c \"import gui; print(gui.__file__)\"\n")
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
