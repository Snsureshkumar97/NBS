#!/bin/zsh
# Install the NBS Signal Tool as two launchd agents, so it runs without a
# terminal, a Claude session, or anything else being left open.
#
#   com.nbs.tailscaled  - the userspace Tailscale daemon that publishes
#                         https://nbs-signal-tool.tail7b2558.ts.net
#   com.nbs.signaltool  - the tool itself, on 127.0.0.1:5055
#   com.nbs.morningcheck- watches the open and writes a report
#
# Both are RunAtLoad and KeepAlive: they start when you log in and are
# restarted within seconds if they crash or are killed.
set -e
UID_N=$(id -u)
HERE=${0:A:h}
for L in com.nbs.tailscaled com.nbs.signaltool com.nbs.morningcheck; do
  cp "$HERE/$L.plist" "$HOME/Library/LaunchAgents/$L.plist"
  plutil -lint "$HOME/Library/LaunchAgents/$L.plist" > /dev/null
  launchctl bootout "gui/$UID_N/$L" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_N" "$HOME/Library/LaunchAgents/$L.plist"
  echo "loaded $L"
done
echo
echo "Status:"
launchctl list | grep com.nbs || true
echo
echo "Logs: ~/Library/Logs/nbs-signal-tool.log, ~/Library/Logs/nbs-tailscaled.log"
