#!/data/data/com.termux/files/usr/bin/bash
#
# termux_setup.sh — one-shot installer for running this tool on Android/Termux
# ============================================================================
#
#   bash termux_setup.sh
#
# WHAT THIS DOES
#   Installs everything main.py needs on Termux. numpy and pandas are NOT
#   available as ready-made Termux packages — they have to be COMPILED from
#   source on your phone. That is slow (typically 20-60 minutes, sometimes
#   longer on older devices) and needs about 2 GB of free space. Plug your
#   phone in, keep Termux in the foreground, and let it run.
#
#   You only do this once. After that, starting the tool takes seconds.
#
# WHAT WILL NOT WORK ON TERMUX
#   gui.py — the desktop window needs Tkinter, which plain Termux has no
#   working display for. Use main.py (the terminal version) instead; it runs
#   the identical strategy and prints the same report.
#
set -u

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
warn() { printf '\n\033[1;33m!!  %s\033[0m\n' "$1"; }
die() { printf '\n\033[1;31mXX  %s\033[0m\n' "$1"; exit 1; }

command -v pkg >/dev/null 2>&1 || die "This does not look like Termux (no 'pkg' command)."

say "Step 1/5 — updating Termux packages"
yes | pkg upgrade -y || warn "pkg upgrade had problems; continuing anyway."

say "Step 2/5 — installing build tools (needed to compile numpy/pandas)"
pkg install -y python build-essential cmake ninja libopenblas \
    libandroid-execinfo patchelf binutils-is-llvm || die "Package install failed."

PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
say "Detected Python $PYVER"

# NOTE: never run `pip install --upgrade pip` on Termux — it ships a patched
# pip, and replacing it breaks these builds.
say "Step 3/5 — installing build helpers"
pip3 install --no-cache-dir setuptools wheel packaging pyproject_metadata \
    cython meson-python versioneer || die "Build helper install failed."

if python3 -c 'import numpy' 2>/dev/null; then
    say "Step 4/5 — numpy already installed, skipping"
else
    say "Step 4/5 — compiling numpy (SLOW: grab a coffee)"
    MATHLIB=m LDFLAGS="-lpython${PYVER}" pip3 install \
        --no-build-isolation --no-cache-dir numpy || die "numpy build failed."
fi

if python3 -c 'import pandas' 2>/dev/null; then
    say "Step 5/5 — pandas already installed, skipping"
else
    say "Step 5/5 — compiling pandas (SLOWEST step; can take 30+ minutes)"
    LDFLAGS="-lpython${PYVER}" pip3 install \
        --no-build-isolation --no-cache-dir pandas || die "pandas build failed."
fi

say "Installing requests + kiteconnect"
pip3 install --no-cache-dir requests || warn "requests install failed."
pip3 install --no-cache-dir kiteconnect || \
    warn "kiteconnect failed — that's OK unless you want --mode kite."

say "Verifying"
python3 - <<'PYEOF'
mods = ["pandas", "numpy", "requests"]
ok = True
for m in mods:
    try:
        mod = __import__(m)
        print(f"  OK   {m} {getattr(mod, '__version__', '')}")
    except Exception as e:
        ok = False
        print(f"  FAIL {m}: {e}")
try:
    import kiteconnect
    print("  OK   kiteconnect (kite mode available)")
except Exception:
    print("  --   kiteconnect missing (free mode only)")
print()
print("READY" if ok else "SOMETHING IS MISSING — see FAIL lines above")
PYEOF

cat <<'DONE'

--------------------------------------------------------------------
Setup finished. To run the tool:

    python3 main.py --index NIFTY --mode free --live --refresh 60

Use main.py, NOT gui.py — the window version cannot display in Termux.
See TERMUX.md for the full guide.
--------------------------------------------------------------------
DONE
