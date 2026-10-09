# Security: what is protected, from whom

TrafficPi is meant to run on a home or workplace network. This page says what it does to keep the dashboard, the pictures and the data from people who should not have them, what it does not do, and what to do about the rest. The design decisions are plan.md D10, D11, D28 and D31; the risks are R6, R11 and R12.

## The short version

- Everything but the login page needs the dashboard's password: the numbers, the pictures, the live video, the clips, the settings. The only things served without it are the login itself, `/api/health`, `/api/auth/state` (whether a password has been set) and the dashboard's own files (code and styling, no data).
- **On your own network the dashboard is plain HTTP.** Anyone who can watch the traffic on that network can read the password as you type it and the session cookie after. That is the usual trade for a device on a home network. For anything beyond one, use a VPN or the Cloudflare Tunnel (both encrypt), see [remote-access.md](remote-access.md).
- There is one user. The password is set from the command line (`trafficpi-admin set-password`), not through a web page that anyone could reach first.
- No number plates are read and no faces are recognised; the evidence pictures are 640 by 360 pixels and are kept for a limited time (the Settings card).

## What is in place

| Threat | What TrafficPi does |
|---|---|
| Guessing the password | Passwords are stored as Argon2id hashes (64 MiB, three passes) and need at least 10 characters. Eight failed logins in ten minutes lock an address, and eight lock a user name, for the rest of the ten minutes; failures and lockouts are written to the journal. At most two passwords are checked at a time, and a third login is turned away with "busy" rather than waiting, so a flood of logins cannot use up the Pi's memory. |
| Stealing a session | The cookie is `HttpOnly` (a script on the page cannot read it), `SameSite=Strict` (it is not sent with requests that other sites cause) and `Secure` when the page came by HTTPS. It is signed with the key in `secret.key` and lasts seven days. It carries a fingerprint of the password hash, so **setting a new password ends every session**. There is no list of sessions on the server, so one session cannot be ended alone; change the password to end them all. |
| Another site making your browser do things | The cookie's `SameSite=Strict`, and a check that the `Origin` of every change and of the live socket is this site. |
| Code injected into a page | The Content-Security-Policy lets a page run only the scripts that are in the file the server sent, by their hashes, and nothing from elsewhere; no other site may frame the dashboard or read what it serves (`X-Frame-Options`, `frame-ancestors`, `Cross-Origin-Resource-Policy`); the browser is told not to guess content types. |
| Huge or endless requests | A request body is limited to 16 KB (1 MB where a camera profile is sent) and is cut off as it streams. |
| Someone else on the Pi or its network reading the data | The data directory is readable by its owner only (mode 700), and so are backups. The vision service, which owns the camera, listens on `127.0.0.1` only. |
| The web service being taken over | It runs as an ordinary user with no new privileges, a read-only view of the file system, a system-call filter, no devices and a memory limit that the kernel enforces where it can (`systemd-analyze security` gives it 1.5 of 10, 10 being worst). The vision service has the same without the filter and the device limits, which libcamera and the Hailo runtime would not survive (6.5). |
| A service that hangs | systemd's watchdog restarts either service that stops answering, and the Pi's own hardware watchdog (on by default in Raspberry Pi OS) resets the board if the whole system hangs. |
| Losing the data | `trafficpi-admin backup` and `restore` (see [operations.md](operations.md)). |
| The email provider's key leaking | It is kept in a file of its own (`backend/data/mail.key`, mode 600), never in the database, an API answer, a log line or an error text (what a server or a network error says is cleaned, and the key taken out of it, before it is kept), and it is typed into a write-only field: the dashboard cannot show it again. A refused key is not repeated back. The key is sent to Resend only, over HTTPS, and a redirect is never followed, so it cannot be sent on to another host. |
| Cloudflare's terms (remote access) | Live video is held back from viewers who come through Cloudflare unless you allow it ([remote-access.md](remote-access.md)). |

Passwords are never written to the log: a failed login is logged with the user name only if that user exists, because a name that does not may well be a password typed in the wrong box.

## What is not done, and what to do about it

- **No encryption on the local network.** See the short version. If the Pi is on a network with people you do not trust (a shared house, an office), use the VPN way even at home, and set the Pi's firewall (`ufw`) to allow port 8000 only from it.
- **One password for everyone.** There are no roles: whoever has the password can change the camera's lines and settings, delete data and see the video. Anyone you give it to gets all of it. (Cloudflare Access can limit *who may reach* the site by email address, which is a good reason to use it for people other than yourself.)
- **The vision service's local interface has no password.** It listens on `127.0.0.1:8001` and gives the camera's picture and state to any program running on the Pi. Programs on the Pi are trusted; do not run untrusted ones there.
- **A web page could try passwords from a visitor's browser (DNS rebinding).** Such a page can make requests to the Pi under its own name, which the Origin check accepts because it matches. It cannot read the session cookie (it belongs to the Pi's real name) and so reaches only the public pages and the login, which is limited as above. Closing this fully would mean naming the host names the Pi answers to; nothing is done about it now.
- **The session key is the root of trust.** Anyone with `secret.key` can make a valid session cookie. It is readable by your user only; a backup holds a copy. If it may have leaked, stop the services, delete `backend/data/secret.key`, start them (a new one is made and every session ends) and set a new password.
- **The daily email goes to a third party.** If you switch it on (it is off until you do), the recipients' addresses and the numbers in the report (counts, shares, the busiest hour) go to Resend, which delivers them. No picture, clip or path is sent, and the HTML mail loads nothing when opened. A key kept in `mail.key` is in the backups, which are private too; if it may have leaked, delete it in Resend and paste a new one. Details in [operations.md](operations.md).
- **Evidence shows the street.** The pictures and clips show the houses and gardens opposite as well as the vehicles. They are kept for the time set in Settings and then deleted, and only a logged-in user can open them.
- **Updates.** TrafficPi's own dependencies are fixed by lock files; `scripts/audit_deps.sh` looks for known weaknesses in them (and checks their licences), a weekly workflow on GitHub runs it, and you can run it yourself at any time. The Pi's own software is yours to keep up to date (`sudo apt update && sudo apt full-upgrade`); a kernel update is first run by the next reboot, and the Hailo driver is rebuilt for it (`scripts/check_boot.sh` afterwards).

## If you find a weakness

Please tell the maintainer privately before making it public. (A proper address and policy come with the release, plan.md M9.)
