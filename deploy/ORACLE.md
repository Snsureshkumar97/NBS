# Running the tool on Oracle Cloud Always Free

The point of this is to stop the site depending on a laptop being awake.
Oracle's Always Free tier is a real VM with a real disk that runs permanently
at no cost, so the code does not change at all — unlike the serverless route,
which would mean giving up the WebSocket and tick-checked targets.

`deploy/oracle-setup.sh` does everything on the machine. What is left for you
is the account, the VM, and pasting one command.

---

## 1. The account

[cloud.oracle.com](https://cloud.oracle.com) → Start for free.

It asks for a **credit card**. Always Free resources do not charge it — the
card is identity verification, and the account stays on the free tier unless
you deliberately upgrade. Pick the **home region** carefully: it cannot be
changed afterwards, and it decides where the free capacity you are competing
for lives. For India, Mumbai or Hyderabad.

## 2. The VM

Compute → Instances → **Create instance**.

| Field | Value |
|---|---|
| Image | **Ubuntu 22.04** or 24.04 |
| Shape | **VM.Standard.A1.Flex** — 1 OCPU, 6 GB |
| SSH keys | Generate a key pair and **download the private key** |

### The one thing that will probably go wrong

**"Out of host capacity."** The free ARM (A1) shapes are heavily
oversubscribed and popular regions refuse new ones for days at a time. This is
the single most common reason people give up on Oracle's free tier, and it is
not something you have done wrong.

Two ways through it:

* **Try a different availability domain** in the same region, and try again at
  a quiet hour. Capacity is released continuously.
* **Take `VM.Standard.E2.1.Micro` instead** — AMD, always available, but
  **1 GB of RAM**. That is tight for pandas and numpy. It works, but expect a
  slow first start, and add swap before anything else:

      sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
      sudo mkswap /swapfile && sudo swapon /swapfile
      echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

  Without swap, `pip install pandas` on a 1 GB micro is likely to be killed by
  the kernel partway through, which looks like a hang rather than an error.

**Networking:** leave it alone. Nothing needs an inbound port, because
Tailscale Funnel works over an outbound connection. Do not open 80/443 — on
Oracle's Ubuntu images that also requires editing iptables, and it is a
detour to a place you do not need to go.

## 3. Install

    chmod 400 ~/Downloads/your-key.key
    ssh -i ~/Downloads/your-key.key ubuntu@<the instance's public IP>

Then:

    curl -fsSL https://raw.githubusercontent.com/Snsureshkumar97/NBS/main/deploy/oracle-setup.sh | bash

It installs Python, clones the repo, builds a virtualenv, writes an env file
with a generated admin key, installs a systemd service that starts on boot and
restarts on failure, and installs Tailscale. On ARM the pandas/numpy install
takes a few minutes. It is safe to re-run.

## 4. Finish

The script prints these; they are here so you can read ahead.

    sudo tailscale up --hostname=nbs-tool          # sign in via the printed link
    tailscale status --json | grep -m1 DNSName     # your permanent address

    nano ~/.trading-tool/.env                      # WEB_PUBLIC_URL, KITE_API_KEY, KITE_API_SECRET

    sudo systemctl restart nbs-signal-tool
    curl -s localhost:5055/healthz                 # expect: ok

    sudo tailscale funnel --bg 5055

Then set your **Kite Redirect URL** to `<WEB_PUBLIC_URL>/kite/callback` and
re-export the public site so its buttons point at the VM instead of the Mac:

    python3 export_site.py --app-url https://nbs-tool.<tailnet>.ts.net \
                           --base-url https://nbstradingtool.vercel.app
    git add dist && git commit -m "Point the site at the VM" && git push

## Notes worth having in advance

* **Kite allows one Redirect URL.** Moving to the VM takes it away from the
  Mac's address, so the Mac's copy stops being able to connect Zerodha. That
  is the intended outcome — the VM is the one serving people now.
* **The machine name has to differ from the Mac's** (`nbs-signal-tool`) or
  Tailscale appends a suffix and the address is not what you expected. The
  script uses `nbs-tool`.
* **Oracle reclaims idle Always Free compute.** Their stated policy targets
  instances that are genuinely idle over a week; a service holding a WebSocket
  and serving requests is not, but it is worth knowing the policy exists.
* **Back up the accounts file.** It is the one thing here that cannot be
  regenerated:

      scp -i key ubuntu@<ip>:~/.trading-tool/users.json ./users-backup.json

* **Logs:** `journalctl -u nbs-signal-tool -f`
* **Update:** `cd ~/nbs && git pull && sudo systemctl restart nbs-signal-tool`
