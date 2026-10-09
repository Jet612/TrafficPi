# Reaching TrafficPi from outside your network

By default TrafficPi answers on your own network only (`http://<the Pi>:8000`). This page is for when you want to open the dashboard from somewhere else, or let other people do it. There are three ways, and the first is the simplest.

| | Who can use it | What you need | Live video |
|---|---|---|---|
| **Your own network only** (the default) | Anyone on it who has the password | Nothing | Yes |
| **A VPN** (Tailscale, WireGuard) | Devices you put on the VPN | The VPN's app on the Pi and on each device | Yes |
| **Cloudflare Tunnel with Access** | Anyone you name by email address, in any browser, with nothing to install | A Cloudflare account and a domain that uses Cloudflare's DNS | Held back unless you allow it (below) |

**Do not forward a port on your router to the Pi.** Neither of the last two needs it: the Pi calls out to the VPN or to Cloudflare, and nothing calls in.

> **How much of this has been tried.** The VPN way needs nothing from TrafficPi and works as on the LAN. The Cloudflare way is written from Cloudflare's own documentation and from what TrafficPi does when a request arrives with Cloudflare's headers, which the tests and the browser test check with the headers faked. **It has not been tried against a real tunnel**: that needs a Cloudflare account and a domain. If you set one up, the steps that did not match are worth telling the maintainer.

## What TrafficPi does for people who come through Cloudflare

Everything works for them as it does at home (the numbers, the charts, the History and Data pages, the pictures and clips of stop-sign passes, the zone editor, the boxes in the live data) with one exception: **the continuous live video is held back**. In its place the Live page shows a still picture, renewed every few seconds, with the lines and zones over it, and a line of text saying why. People on your own network and on a VPN are not affected.

The reason is Cloudflare's terms. When this was written (2026-10-09), the CDN section of Cloudflare's *Service-Specific Terms* (the page was dated 2026-09-28) said that Cloudflare may limit use of its CDN by a customer without a paid plan who delivers "video or a disproportionate percentage of pictures, audio files, or other large files". The *Zero Trust* terms of the same date, which cover Tunnel and Access, did not mention Tunnel traffic or video, and neither page mentions Tunnel and video together, so **whether a tunnelled hostname counts as use of the CDN is not stated**. A ten-frames-a-second stream is the one thing here that is video in the plainest sense, so it is off until you say otherwise. A still picture every three seconds is the same size as the pictures the site has always shown. (Those pages were read through a tool that summarises them: read them yourself before you switch the video on. Cloudflare changes its terms, and this is not legal advice.)

- The switch is **Setup → Settings → Viewers who come through Cloudflare → Allow live video…**. It is off.
- A request counts as coming through Cloudflare if it carries Cloudflare's `CF-Ray` or `CF-Connecting-IP` header. That holds back bandwidth; it is not a login. Someone on your LAN who writes that header only gives themselves the still picture.
- The short clips of stop-sign passes (nine seconds, 640 by 360, about 50 KB) are shown to remote viewers like any picture. If you would rather hold them back as well, say so: it is one condition in `trafficpi/api/app.py` (plan.md Q16).

## Using a VPN instead (Tailscale)

If the only person who needs to look from outside is you, this is the easier way and avoids the questions above.

1. Install Tailscale on the Pi (`curl -fsSL https://tailscale.com/install.sh | sh`, or from their package repository; this Pi has it already) and run `sudo tailscale up`.
2. Install Tailscale on your phone or laptop and sign in to the same account.
3. Open `http://<the Pi's Tailscale name or 100.x.y.z address>:8000`. The traffic between your device and the Pi is encrypted by the VPN even though the page itself is plain HTTP.

TrafficPi needs nothing for this: it already listens on every address of the Pi. Live video works as at home.

## Cloudflare Tunnel with Access, step by step

Two layers protect the dashboard. **Access** (Cloudflare's) decides who may reach the site at all, by email address, before a single request gets to your Pi. **TrafficPi's own login** is the second layer: a request that gets past Access still needs the dashboard's password.

### 1. Install `cloudflared` on the Pi

`cloudflared` is Cloudflare's connector. It comes from Cloudflare's own package repository, which means trusting Cloudflare's software on your Pi, so it is not installed by TrafficPi's installer. Cloudflare's page for Debian is the authority (<https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/>); at the time of writing it said:

```
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-public-v2.gpg | sudo tee /usr/share/keyrings/cloudflare-public-v2.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-public-v2.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install cloudflared
```

The key's file name has changed once already; if the page and this copy differ, follow the page.

### 2. Make the tunnel

In Cloudflare's dashboard, under **Zero Trust → Networks → Tunnels** (the menus move now and then; look for Tunnels), create a tunnel of the *Cloudflared* kind, give it a name, and choose **Debian** and **arm64**. The page shows a command that begins `sudo cloudflared service install` and ends in a long token. Run it on the Pi. It installs `cloudflared.service` and starts it; `systemctl status cloudflared` should say *active*, and the tunnel in the dashboard should turn *Healthy*. The token is a secret and sits in that unit file, which every user of the Pi can read; on a Pi that only you use that is fine.

(If you prefer a file to a token, `deploy/cloudflared/config.example.yml` is a configuration for a tunnel you create with `cloudflared tunnel create`. The result is the same.)

### 3. Point a name at the dashboard

Still in the tunnel's settings, add a **public hostname**: a name under your domain (for example `traffic.example.com`), service type **HTTP**, URL **`localhost:8000`**. Cloudflare creates the DNS record by itself.

### 4. Tell TrafficPi that it is behind a tunnel

Everything that arrives through the tunnel comes from the Pi itself (from `cloudflared`), so TrafficPi would see one address for every visitor, and its limit on failed logins (eight in ten minutes, per address and per name) would treat all of them as one. This makes it believe the real address that Cloudflare passes on, and only for connections that come from this Pi:

```
cd ~/TrafficPi        # wherever the checkout is
scripts/install_services.sh --trust-proxy
sudo systemctl restart trafficpi-api
```

(`--trust-proxy-off` undoes it.)

### 5. Protect the name with Access

In **Zero Trust → Access → Applications**, add a **self-hosted** application for the hostname from step 3. Add one policy, of the kind **Allow**, that includes **only the email addresses of the people you mean** (or one address pattern you control, never "everyone"). Choose a login method that needs no setup, **One-time PIN**, which emails a code. Pick a session length you can live with. Save.

Two things go wrong here and are worth checking before you rely on it: open the hostname in a private browser window and see that you are asked for an email address *before* you see the TrafficPi login; and try an address that is not on the list and see that no code arrives.

### 6. Turn off what rewrites pages

TrafficPi tells the browser to run only the scripts that are in the page it sent, by their hashes. Anything that changes the page on its way through Cloudflare breaks that, and the dashboard comes up blank. In the Cloudflare settings for the domain, leave **Rocket Loader**, **Auto Minify** (HTML) and **Email Address Obfuscation** off, and do not turn on automatic **Web Analytics** for this hostname (its script is blocked, which is harmless, and the rewriting is not).

### 7. Try it

Open the hostname on a phone that is **not** on your Wi-Fi. Expect: Cloudflare asks for your email address and a code; the TrafficPi login follows; the Live page shows *a still picture* with the line "Live video is off for people who come through Cloudflare…"; **Setup → Settings** says "You are looking at this page through Cloudflare now."; the History and Data pages work.

### Taking it down

`sudo systemctl disable --now cloudflared`, delete the tunnel and the Access application in Cloudflare's dashboard, and `scripts/install_services.sh --trust-proxy-off`. Nothing else on the Pi changes.

## When something is wrong

- **Cloudflare shows error 502 or 1033.** The tunnel is up but the dashboard is not answering, or the tunnel is down. `systemctl status trafficpi-api cloudflared`, and `curl http://localhost:8000/api/health` on the Pi.
- **The page loads and the live data never arrives** (the header says the connection was lost). The live data uses a WebSocket. In the domain's Cloudflare settings, **Network → WebSockets** must be on (it is by default).
- **A blank page after a Cloudflare setting was changed.** See step 6.
- **"Too many failed logins" for everyone at once.** The proxy headers are not believed: step 4 was skipped or the API was not restarted. In the log, `journalctl -u trafficpi-api` names the address that failed, and it should be the visitor's, not `127.0.0.1`.
- **Signed in on the phone, signed out again at once.** Cloudflare must pass `X-Forwarded-Proto` (it does by default); TrafficPi then marks its cookie *Secure*, which a browser keeps only on an HTTPS address. Opening the site through a plain `http://` address does that.
- **You want the live video.** Read Cloudflare's terms (the links are above), then the switch in Setup. Consider the VPN instead.

## What is and is not protected

[security.md](security.md) says who can do what with each of these ways, and what stays open (the dashboard is plain HTTP on your own network, so a password typed there crosses it in clear).
