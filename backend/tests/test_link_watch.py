"""scripts/link_watch.sh on fake commands and a clock the test moves: what it does at each minute
of an outage, and what it says."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "link_watch.sh"
MINUTE = 60
HOUR = 3600

WIFI_UP = "3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP mode DORMANT"
WIFI_LOST = "3: wlan0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 state DOWN mode DORMANT"
ROUTE_WIFI = "default via 192.168.1.1 dev wlan0 proto dhcp src 192.168.1.50 metric 600"
ROUTE_CABLE = "default via 192.168.1.1 dev eth0 proto dhcp src 192.168.1.51 metric 100"

FAKE_IP = """#!/bin/sh
case "$*" in
  "-o link show wlan0") cat "$FAKE/link" ;;
  "route show default") cat "$FAKE/route" ;;
esac
"""
FAKE_LOGGED = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE/calls"
"""


class Pi:
    """The script's world: a link, a clock, and a log of what it ran."""

    def __init__(self, folder: Path) -> None:
        self.fake = folder / "fake"
        self.bin = folder / "bin"
        self.run_dir = folder / "run"
        self.state_dir = folder / "state"
        for path in (self.fake, self.bin, self.run_dir, self.state_dir):
            path.mkdir()
        self.command("ip", FAKE_IP)
        self.command("date", '#!/bin/sh\ncat "$FAKE/clock"\n')
        self.command("sleep", "#!/bin/sh\n")
        for name in ("nmcli", "modprobe", "systemctl"):
            self.command(name, FAKE_LOGGED)
        self.now = 1_800_000_000
        self.wifi(up=True)

    def command(self, name: str, text: str) -> None:
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def wifi(self, *, up: bool) -> None:
        (self.fake / "link").write_text((WIFI_UP if up else WIFI_LOST) + "\n")
        (self.fake / "route").write_text(ROUTE_WIFI + "\n" if up else "")

    def calls(self) -> list[str]:
        path = self.fake / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def look(self) -> str:
        """One run of the timer. Says what the script said."""
        (self.fake / "clock").write_text(f"{self.now}\n")
        done = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "FAKE": str(self.fake),
                "RUNTIME_DIRECTORY": str(self.run_dir),
                "STATE_DIRECTORY": str(self.state_dir),
            },
        )
        return done.stdout + done.stderr

    def minutes(self, count: int) -> str:
        """That many looks, a minute apart. Says what they said."""
        said = ""
        for _ in range(count):
            self.now += MINUTE
            said += self.look()
        return said

    def boot(self) -> None:
        """/run is empty after a boot, and /var/lib is not."""
        shutil.rmtree(self.run_dir)
        self.run_dir.mkdir()
        self.now += MINUTE


@pytest.fixture
def pi(tmp_path: Path) -> Pi:
    return Pi(tmp_path)


def steps(pi: Pi) -> list[str]:
    """The name of each command that was run, in order."""
    return [call.split()[0] for call in pi.calls()]


def test_a_healthy_link_runs_nothing_and_says_nothing(pi: Pi) -> None:
    assert pi.minutes(60) == ""
    assert pi.calls() == []


def test_a_short_loss_is_noted_and_nothing_is_done_about_it(pi: Pi) -> None:
    pi.wifi(up=False)
    assert "no Wi-Fi link" in pi.look()
    assert pi.minutes(8) == ""  # said once, not every minute
    pi.wifi(up=True)
    assert "is back after 9 minutes, by itself" in pi.minutes(1)
    assert pi.calls() == []


def test_the_radio_is_switched_after_ten_minutes_and_the_network_asked_to_connect(pi: Pi) -> None:
    pi.wifi(up=False)
    pi.look()
    assert pi.minutes(9) == ""
    said = pi.minutes(1)
    assert "no Wi-Fi link for 10 minutes: switching the radio off and on" in said
    assert pi.calls() == [
        "nmcli radio wifi off",
        "nmcli radio wifi on",
        "nmcli --wait 20 device connect wlan0",
    ]


def test_the_steps_come_in_order_each_once_at_their_own_minute(pi: Pi) -> None:
    pi.wifi(up=False)
    pi.look()
    pi.minutes(14)
    assert "modprobe" not in steps(pi)
    assert "reloading the driver" in pi.minutes(1)  # 15 minutes
    assert steps(pi).count("modprobe") == 2
    assert pi.calls()[-2].startswith("modprobe -r ") and pi.calls()[-2].endswith(" brcmfmac")
    assert pi.calls()[-1] == "modprobe brcmfmac"
    pi.minutes(9)
    assert "systemctl" not in steps(pi)
    assert "no Wi-Fi link for 25 minutes: rebooting the Pi" in pi.minutes(1)
    assert pi.calls()[-1] == "systemctl reboot"
    assert steps(pi).count("nmcli") == 3 and steps(pi).count("modprobe") == 2


@pytest.mark.parametrize(
    ("minutes", "how"),
    [
        (12, "after the radio was switched off and on"),
        (18, "after the driver was reloaded"),
    ],
)
def test_it_says_which_step_worked_and_the_next_loss_starts_over(
    pi: Pi, minutes: int, how: str
) -> None:
    pi.wifi(up=False)
    pi.look()
    pi.minutes(minutes)
    pi.wifi(up=True)
    assert f"is back after {minutes + 1} minutes, {how}" in pi.minutes(1)
    assert pi.minutes(5) == ""  # said once
    before = len(pi.calls())
    pi.wifi(up=False)
    pi.look()
    pi.minutes(9)
    assert len(pi.calls()) == before  # a new loss gets its ten minutes again


def test_no_step_comes_in_the_first_ten_minutes_after_a_boot(pi: Pi) -> None:
    pi.wifi(up=False)
    pi.look()
    pi.minutes(9)
    pi.boot()
    pi.look()
    pi.minutes(9)
    assert pi.calls() == []


def test_after_a_reboot_it_says_that_the_reboot_worked(pi: Pi) -> None:
    pi.wifi(up=False)
    pi.look()
    pi.minutes(25)
    pi.boot()
    pi.wifi(up=True)
    assert "is back after the reboot" in pi.look()
    assert pi.minutes(5) == ""


def test_a_second_reboot_waits_six_hours_and_says_so_once(pi: Pi) -> None:
    pi.wifi(up=False)
    pi.look()
    pi.minutes(25)
    pi.boot()
    pi.look()
    said = pi.minutes(60)
    assert said.count("less than 6 hours ago") == 1
    assert steps(pi).count("systemctl") == 1
    said = pi.minutes(5 * 60)
    assert said.count("rebooting the Pi") == 1
    assert steps(pi).count("systemctl") == 2


def test_a_cable_with_a_default_route_is_a_link(pi: Pi) -> None:
    pi.wifi(up=False)
    (pi.fake / "route").write_text(ROUTE_CABLE + "\n")
    assert pi.minutes(30) == ""
    assert pi.calls() == []


def test_no_wifi_device_at_all_counts_as_no_link(pi: Pi) -> None:
    """A driver that does not come back from its reload leaves no wlan0: only a reboot helps."""
    pi.wifi(up=False)
    (pi.fake / "link").write_text("")
    pi.look()
    pi.minutes(25)
    assert pi.calls()[-1] == "systemctl reboot"


def test_a_step_that_fails_does_not_keep_the_next_from_coming(pi: Pi) -> None:
    pi.command("modprobe", FAKE_LOGGED + "exit 1\n")
    pi.wifi(up=False)
    pi.look()
    pi.minutes(25)
    assert pi.calls()[-1] == "systemctl reboot"


def test_a_step_that_hangs_and_is_killed_is_not_tried_again(pi: Pi) -> None:
    """The unit's time limit ends a hung driver unload; the next look must go on to the reboot."""
    pi.command("modprobe", FAKE_LOGGED + "kill -9 $PPID\n")
    pi.wifi(up=False)
    pi.look()
    pi.minutes(25)
    assert steps(pi).count("modprobe") == 1
    assert pi.calls()[-1] == "systemctl reboot"


def test_a_long_answer_from_ip_is_still_a_link(pi: Pi) -> None:
    """Reading only the start of what a command prints would end it with a broken pipe, which
    `pipefail` turns into no link, and a healthy Pi would be rebooted."""
    (pi.fake / "link").write_text(WIFI_UP + "\n" + "more\n" * 500_000)  # more than a pipe holds
    assert pi.minutes(2) == ""


def test_it_refuses_to_run_outside_its_unit() -> None:
    done = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert done.returncode != 0 and "trafficpi-link-watch.service" in done.stderr
