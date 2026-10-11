# Security policy

## Reporting a vulnerability

Please report a security problem **privately**, not as a public issue or a pull request. Use GitHub's private vulnerability reporting: on the repository's **Security** tab, choose **Report a vulnerability**.

Say what you found, how to reproduce it (the commands, the page or the request), the version (`git log -1 --oneline`) and what you think it allows. Leave out real secrets: if a log or a recording shows a password, a session key, a tunnel token or a mail key, cut it out first.

TrafficPi has one maintainer, so there is no guaranteed time to a reply. A report is answered as soon as it is seen.

## What is in scope

[docs/security.md](docs/security.md) says what the dashboard and the API protect, from whom, and what they do not: the dashboard is plain HTTP on a local network, for one, and there is one password for everyone. A way round a protection that page lists is a vulnerability. A weakness that page already lists as not done is known.

In scope: the API, the login and session handling, the way the dashboard's files are served, the installer and the systemd units, and what leaves the machine (the optional daily e-mail).

## Supported versions

There is no tagged release yet. Only the `main` branch is maintained.
