# Keeping the tool up on this Mac

## What has to be running

Three things, in this order:

1. **`tailscaled`** — publishes `https://nbs-signal-tool.tail7b2558.ts.net`
2. **`web_server.py`** — the tool, on `127.0.0.1:5055`
3. **This Mac, awake and online**

The public site at `nbstradingtool.vercel.app` is static and always up, but
`/login` on it is a redirect to the Tailscale address. When the Mac is off,
asleep, or the server is down, the redirect lands nowhere and login fails.
That is the whole of "login doesn't work when I close Claude".

## What you no longer have to keep open

Nothing. Both processes are launchd agents now:

```
~/Library/LaunchAgents/com.nbs.tailscaled.plist
~/Library/LaunchAgents/com.nbs.signaltool.plist
```

`RunAtLoad` starts them when you log in. `KeepAlive` restarts them within a
couple of seconds if they crash or are killed. No terminal, no Claude session,
no window.

Install or reinstall:

```
./deploy/macos/install.sh
```

Check, restart, read the logs:

```
launchctl list | grep com.nbs
launchctl kickstart -k gui/$(id -u)/com.nbs.signaltool
tail -f ~/Library/Logs/nbs-signal-tool.log
```

## The two things launchd cannot fix

**Sleep.** A sleeping Mac is an offline server. `pmset -g custom` currently
reports `sleep 1` on both AC and battery, so it drops off a minute after you
stop touching it. To keep it reachable while plugged in:

```
sudo pmset -c sleep 0 disablesleep 0
```

That needs your password, so run it yourself. On battery it should still
sleep — leave that alone.

**The Zerodha token.** Zerodha clears access tokens every morning around
07:30 IST. That is not a hosting problem and no amount of uptime prevents it:
you have to reconnect the Zerodha account each trading day.

## When this stops being enough

A laptop that sleeps, moves and runs on battery is not a server. If other
people need the tool reliably, or you want it up while the Mac is shut, it
belongs on a host that stays on — see `deploy/ORACLE.md`, which is already
written and unused.
