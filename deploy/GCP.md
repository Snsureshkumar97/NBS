# Moving the tool to a Google Cloud VM in Mumbai

Decided on 20 Sep 2026. The reason: Zerodha (SEBI rule, since 1 Apr 2026) and
Delta Exchange India both refuse API orders from any IP that is not registered
on the account, and this Mac's public address changes with the network it is
on (Comcast one day, a T‑Mobile gateway the next). A small always‑on VM with a
reserved static IP in `asia-south1` (Mumbai) gives the tool one fixed address
that never moves, keeps running when the Mac sleeps or roams, and puts the
server in India, which Zerodha and SEBI prefer for retail algos.

What moves: the server (`web_server.py`), the two timed jobs (the morning check
at 08:00 IST, the option recorder at 16:00 IST), the Tailscale node named
`nbs-signal-tool` that publishes **https://nbs-signal-tool.tail7b2558.ts.net**,
and the data — `~/.trading-tool/.env` (your keys), the users file, the trade
logs and the 15‑minute history cache. The Mac stays the development machine.

Cost: an `e2-small` (2 shared vCPU, 2 GB) is about ₹1,500–2,000 a month, the
reserved static IP about ₹250, the 20 GB disk about ₹150. New Google Cloud
accounts get free credit for the first 90 days.

---

## Part A — yours (account and money; I never do these)

1. **Account and project.** Sign in at https://console.cloud.google.com with
   your Google account, accept the terms, and create a project — call it
   `nbs-signal-tool`. Google gives it an ID like `nbs-signal-tool-123456`;
   note that ID.
2. **Billing.** Attach a billing account to the project (Billing → Link a
   billing account). Nothing is charged until a VM exists.
3. **Sign the CLI in on the Mac** (a browser window opens; it is your Google
   login, never typed into the tool):

   ```bash
   /opt/homebrew/share/google-cloud-sdk/bin/gcloud auth login
   ```

4. **Point the CLI at the project and enable Compute Engine** (enabling an
   API is a billing decision, so it is yours to run):

   ```bash
   /opt/homebrew/share/google-cloud-sdk/bin/gcloud config set project YOUR_PROJECT_ID
   ```

   ```bash
   /opt/homebrew/share/google-cloud-sdk/bin/gcloud services enable compute.googleapis.com --project=YOUR_PROJECT_ID --quiet
   ```

Tell me when these four are done. Everything below I do with `gcloud` from
this Mac, one command at a time, each shown before it runs.

## Part B — mine (the VM)

1. Reserve the static IP in Mumbai:
   `gcloud compute addresses create nbs-signal-tool-ip --region=asia-south1 --project=…`
   and read it back — **this is the address you will register at Zerodha and Delta.**
2. Create the VM with that address:
   `gcloud compute instances create nbs-signal-tool --zone=asia-south1-a
   --machine-type=e2-small --image-family=debian-12 --image-project=debian-cloud
   --boot-disk-size=20GB --address=nbs-signal-tool-ip --project=…`
3. Install the tool on it: `gcloud compute ssh` in, run `deploy/gcp/setup.sh`
   (packages, the checkout from GitHub, a virtualenv, the systemd service, the
   two IST timers, Tailscale).
4. Copy the data from the Mac with `gcloud compute scp` — `.env`, the users
   file, the trade logs, the history cache — and set `.env` to mode 600.

## Part C — the cut‑over (a few minutes; outside market hours; nothing open)

1. On the Mac: stop the tool, the recorder, the morning check and the
   `nbs` Tailscale node, and **log that node out** so the name `nbs-signal-tool`
   is free (`tailscale --socket ~/.tailscale-nbs/tailscaled.sock logout`).
2. On the VM: `sudo tailscale up --hostname=nbs-signal-tool` (a login link to
   approve in your Tailscale account), then `sudo tailscale funnel --bg 5055`.
   The public address stays exactly **https://nbs-signal-tool.tail7b2558.ts.net**,
   so nothing changes for you, and the Kite app's redirect URL stays valid.

   *What happened on 20 Sep 2026:* the VM registered while the Mac's old
   device still held the name and came up as `nbs-signal-tool-1`. Deleting the
   old device and renaming in the console did **not** move the node's MagicDNS
   name, and neither did `tailscale set --hostname` or a plain logout/login
   (same device, same name). What worked was a fresh identity:
   `sudo systemctl stop tailscaled && sudo rm /var/lib/tailscale/tailscaled.state
   && sudo systemctl start tailscaled && sudo tailscale up --hostname=nbs-signal-tool`
   (one more approval link), then `sudo tailscale funnel --bg 5055`. So: remove
   the old device **before** the VM's first `tailscale up`, and the plain name
   comes first time.
3. On the VM: `sudo systemctl restart nbs-signal-tool`; I check the page,
   the feeds and the tickets from the outside.
4. **You register the VM's static IP** on developers.kite.trade (your app →
   IP whitelist) and on your Delta Exchange API key's whitelist. Both venues
   see the tool come from that one address from then on. If adding the
   whitelist on Delta creates or rotates the key, re-enter the new key and
   secret on the tool's Delta page - it checks them from the VM's own address.
5. You log in to Zerodha on the new server the next morning as usual (the
   token does not move — it is cleared every morning anyway).

## Rollback

The Mac's launchd jobs are left installed but unloaded. Reloading them
(`launchctl load ~/Library/LaunchAgents/com.nbs.*.plist`) and logging the
VM's Tailscale node out puts everything back within a minute; the data on the
Mac is a copy as of the cut‑over.

## After the move

- Updates: `cd ~/nbs && git pull && sudo systemctl restart nbs-signal-tool`
  on the VM (over `gcloud compute ssh`), still only after 15:40 IST with no
  ticket open — the same practice as on the Mac.
- Logs: `~/trading-tool-logs/nbs-signal-tool.log` on the VM.
- The Mac's `python3` now resolves to Homebrew's copy (installed with the
  Google Cloud CLI); the tool's own interpreter is
  `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`.
