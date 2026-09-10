#!/bin/zsh
set -e
UID_N=$(id -u)
for L in com.nbs.morningcheck com.nbs.signaltool com.nbs.tailscaled; do
  launchctl bootout "gui/$UID_N/$L" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$L.plist"
  echo "removed $L"
done
