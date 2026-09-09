# Reaching it from another device, and keeping it up

## Opening the site on your phone

**Same wifi as the computer running it** — the easy case:

```bash
python3 web_server.py --host 0.0.0.0
```

It now prints the exact address to type:

```
ON YOUR PHONE (same wifi):  http://192.168.1.24:5055
```

Type that into your phone's browser. macOS may show a firewall prompt the first
time — click **Allow**.

Two things that trip people up:

- **The daily Zerodha login still has to be done on the computer itself.** Your
  admin link points at `127.0.0.1`, which *from the phone* means the phone. Log
  in on the computer, then browse from the phone. (Or re-run `setup_web.py`,
  give it the `192.168.x.x` address, and update the Kite Redirect URL to match —
  then the phone can do the login too.)
- **The computer must stay awake:** `caffeinate -i python3 web_server.py --host 0.0.0.0`

**Away from home — different wifi, or mobile data.** Your home address isn't
reachable from outside; the router blocks it. Three ways round that, easiest
first:

| Option | What you get | Cost |
|---|---|---|
| **Tailscale** | A private network joining your own devices. The site stays invisible to everyone else and works from anywhere. Install on the Mac and the phone, then use the Tailscale address. | Free |
| **Cloudflare Tunnel** | A real public `https://` URL, no router changes, no ports opened: `cloudflared tunnel --url http://localhost:5055` | Free |
| **A small cloud server** | The proper answer if this is to be a real site — see below. | ~Rs.400/mo |

**Tailscale is what I'd use** for reaching your own tool from your own phone.
Private by default, and nothing about your home network is exposed. A Cloudflare
tunnel gives a *public* URL — anyone with the link can open it, so treat that as
publishing, with everything the SEBI section of the README says about it.

Port-forwarding on your router is a fourth option and I'd avoid it: it puts a
development web server straight onto the open internet.

---

# Keeping the website up

Yes — something has to keep running. The website is a program; close it and the
site is gone. The only question is *what* keeps it running.

**But first, the useful bit:** you almost certainly don't need 24/7. The market
is open **09:15–15:30 IST**, which is **11:45pm–6:00am US Eastern**. Outside
those hours the site has nothing new to show. A schedule that runs it overnight
and stops in the morning is less to go wrong than something that never stops.

---

## Your options, honestly ranked

### 1. A small cloud server (best, if this matters to you)

About ₹300–600/month for the smallest instance anywhere — DigitalOcean, Hetzner,
Vultr, AWS Lightsail, or an Indian host if you'd rather the machine sat closer
to Zerodha. Always on, fixed address, survives your laptop closing.

This is the only option that gives you a real website other people can reach.

```bash
# on the server, once
sudo nano /etc/systemd/system/signal-desk.service
```

```ini
[Unit]
Description=Signal Desk
After=network-online.target

[Service]
Type=simple
User=YOURUSER
WorkingDirectory=/home/YOURUSER/trading-tool
ExecStart=/usr/bin/python3 web_server.py --host 0.0.0.0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now signal-desk
sudo systemctl status signal-desk      # check it
journalctl -u signal-desk -f           # watch the log
```

`Restart=always` means it comes back after a crash, and `enable` means it comes
back after a reboot. Neither of those is true of anything you start by hand.

Put Caddy in front for HTTPS — it gets a certificate on its own:

```
yourdomain.com {
    reverse_proxy 127.0.0.1:8080
}
```

### 2. Your Mac (fine for yourself)

Works, but the laptop has to stay awake and online. macOS sleeps aggressively.

```bash
caffeinate -i python3 web_server.py
```

`caffeinate -i` stops the machine idle-sleeping while it runs. Closing the lid
still sleeps it unless the Mac is on power with the display attached. Good for
testing and for watching it yourself overnight; not a website other people can
rely on.

To have it start itself each evening and stop each morning, `crontab -e`:

```cron
40 23 * * 1-5 cd ~/trading-tool && caffeinate -i python3 web_server.py >> ~/signal.log 2>&1
35 6  * * 1-6 pkill -f web_server.py
```

That covers the Indian session in US Eastern time. Adjust if your timezone
differs, and remember it only makes sense on weekdays.

### 3. Termux on your phone (works, but don't rely on it)

It runs. It will also let you down, and the ways it does are not obvious:

- **Android kills background processes** to save battery. `termux-wake-lock`
  helps and does not fix it — aggressive OEM battery managers (Xiaomi, Samsung,
  OnePlus in particular) will still reap it.
- **Your phone has no fixed address.** On mobile data you are behind carrier NAT;
  nothing on the internet can reach you. On wifi your IP changes. So it can only
  ever serve *you*, on your own network.
- **Wifi drops when the screen sleeps** on many devices, which breaks the Zerodha
  feed mid-session.
- **It is drawing on your battery all night** to poll a market API.

If you want it anyway:

```bash
termux-wake-lock
python3 web_server.py --host 0.0.0.0
```

Keep it plugged in, and exempt Termux from battery optimisation in Android
settings. Treat any data it shows after a long gap as suspect — the stale-feed
banner is there for exactly this.

**Termux is a good way to run `main.py` for a quick reading. It is a poor way to
host a website.**

---

## Whichever you pick

- The **daily Zerodha login still has to happen** — around 07:30 IST, from the
  `/admin` link. Nothing here automates that away; Zerodha's tokens expire and
  the login needs a human at a browser.
- If the token isn't renewed, the site shows an amber **feed down** banner rather
  than quietly serving yesterday's numbers. Check for it before trusting a price.
- `~/.trading-tool/.env` holds live credentials. On a shared or cloud machine
  that file is worth as much as your Zerodha password.
