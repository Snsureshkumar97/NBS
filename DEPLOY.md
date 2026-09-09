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

---

# Hosting the tool on Render

The website has two halves and they need different hosts. The eight public
pages are static and belong on a CDN — see `export_site.py` for Vercel. The
tool itself is not request-shaped: it holds a Zerodha WebSocket open, keeps
ticket state in memory between polls, and writes accounts and trade history to
disk. That needs a process that stays running.

## Before you start: the plan matters

Render's **free** web services will not work for this, for two specific
reasons rather than as a general caution:

* **They spin down after ~15 minutes without a request.** Spun down, the
  WebSocket dies and ticket state is lost. The next visitor also waits about
  fifty seconds for a cold start.
* **They have no persistent disk.** `users.json` and `trades.csv` would be
  erased on every deploy and every restart — accounts and a month of trade
  history, gone.

The **Starter** plan removes the spin-down and allows the 1GB disk in
`render.yaml`. If you would rather not pay for it, running the tool on your own
machine works properly and costs nothing; only the public pages need hosting.

## Steps

1. **Push the repo.** Render deploys from GitHub, so the branch has to be
   there first.

2. **Render → New → Blueprint**, point it at the repo. It reads
   `render.yaml` and creates the service, the disk, and a generated
   `WEB_ADMIN_KEY`. It will ask you for the three values marked
   `sync: false`.

3. **Set `WEB_PUBLIC_URL`** to the hostname Render assigns, e.g.
   `https://nbs-signal-tool.onrender.com` — no trailing slash. Zerodha
   redirects here after a login, so it has to be exact.

4. **Set `KITE_API_KEY` and `KITE_API_SECRET`** from your Kite Connect app.

5. **Set the Redirect URL on the Kite app** to that same address plus
   `/kite/callback`. Read the warning below first — this is the step with a
   consequence.

6. **Create your account.** A fresh disk has no accounts, so nobody can log
   in yet. Open `https://<service>.onrender.com/admin?key=<WEB_ADMIN_KEY>`
   (read the key out of Render's dashboard) and create it there. Keep that
   link private; it is the only thing standing in front of the account list.

7. **Point the public site at it.** Re-export with the app's address so every
   Log in button goes to the real thing instead of the placeholder:

       python3 export_site.py --app-url https://<service>.onrender.com \
                              --base-url https://<your>.vercel.app
       npx vercel deploy --prod dist

## The one real trade-off: Kite allows ONE redirect URL per app

A Kite Connect app has exactly one Redirect URL, and this is not a setting you
can have both ways:

* Point it at Render, and the **website** can connect Zerodha — but the
  desktop app's one-click login breaks, because it waits for the token on
  `http://127.0.0.1:5055/` and Zerodha will now deliver it to Render instead.
* Leave it on `http://127.0.0.1:5055/` and the **desktop** keeps working,
  but nobody can connect Zerodha through the website.

There is no configuration that avoids this. The three honest ways out:

1. **A second Kite Connect app** for the website, with its own key, secret
   and redirect URL. Zerodha bills per app, so check their current pricing.
2. **Make the website primary** and accept that the desktop app loses its
   one-click login. Everything else in the desktop app still works.
3. **Keep the desktop primary** and run the website locally alongside it,
   with only the public pages hosted.

## Notes

* `TRADING_TOOL_HOME=/var/data` is what moves the durable files onto the
  mounted disk. Without it they would be written to a home directory that
  does not survive a deploy.
* `$PORT` is assigned by Render and wins over the port in `WEB_PUBLIC_URL` —
  the public address is https on 443 while the process is handed something
  like 10000 to bind to.
* `--host 0.0.0.0` is required, or Render's proxy cannot reach the process
  and every request returns 502.
* The health check is `/healthz`, which answers without touching Zerodha.
* Region is `singapore`, the closest Render offers to NSE.
