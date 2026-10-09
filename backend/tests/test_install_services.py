"""scripts/install_services.sh, as far as it can be tried without changing the machine: its
checks of what it is given, and what a dry run says it would do (plan.md D30)."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install_services.sh"
VENV = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "trafficpi-vision"


def systemd_can_check_units() -> bool:
    """Whether `systemd-analyze verify` works here at all (a container may have the program and
    not the means), tried on a unit that is certainly fine."""
    if shutil.which("systemd-analyze") is None:
        return False
    with tempfile.TemporaryDirectory() as folder:
        unit = Path(folder) / "probe.service"
        unit.write_text("[Service]\nExecStart=/bin/true\n")
        done = subprocess.run(
            ["systemd-analyze", "verify", str(unit)], capture_output=True, check=False
        )
    return done.returncode == 0


pytestmark = pytest.mark.skipif(
    not VENV.exists() or not systemd_can_check_units(),
    reason="needs the project's virtualenv and a systemd-analyze that can check units",
)


def run(*args: str, expect: int = 0) -> str:
    done = subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == expect, done.stdout + done.stderr
    return done.stdout + done.stderr


def test_help_prints_the_header_of_the_script_and_nothing_is_run() -> None:
    said = run("--help")
    assert "--backup-at" in said and "--dry-run" in said and "--persistent-journal" in said
    assert "set -euo" not in said  # only the comment block, not the code


def test_a_dry_run_says_what_it_would_do_and_does_not_do_it() -> None:
    said = run("--dry-run", "--enable", "--restart", "--backup-at", "03:30")
    assert "would install trafficpi-vision.service trafficpi-api.service" in said
    assert "would install trafficpi-backup.service trafficpi-backup.timer" in said
    assert "would enable and start" in said
    # Every command that changes the system is shown with a "+" and not run.
    commands = [line for line in said.splitlines() if "sudo " in line]
    assert commands and all(line.startswith("+ sudo ") for line in commands), commands
    assert "+ sudo systemctl enable --now trafficpi-vision.service trafficpi-api.service" in said
    assert "+ sudo systemctl restart trafficpi-vision.service trafficpi-api.service" in said
    assert "+ sudo systemctl enable trafficpi-backup.timer" in said
    assert "installed trafficpi" not in said.replace("would install", "")


def test_a_dry_run_shows_a_new_unit_in_full_with_this_checkouts_paths_filled_in() -> None:
    said = run("--dry-run", "--backup-at", "03:30")
    if "trafficpi-backup.service: new:" not in said:  # already installed on this machine
        assert "trafficpi-backup.service:" in said
        return
    assert "ExecStart=" in said and "trafficpi-admin backup --keep 7" in said
    assert "@" not in said.split("trafficpi-backup.service: new:")[1].split("+ sudo")[0]


def test_the_backup_folder_goes_into_the_command_and_the_sandbox(tmp_path: Path) -> None:
    said = run(
        "--dry-run", "--backup-at", "04:15", "--backup-keep", "3", "--backup-to", str(tmp_path)
    )
    if "trafficpi-backup.service: new:" in said:
        assert f"backup --keep 3 --to {tmp_path}" in said
        unit = said.split("trafficpi-backup.service: new:")[1].split("+ sudo")[0]
        assert str(tmp_path) in unit.split("ReadWritePaths=")[1].split("\n")[0]
    assert "every day at 04:15" in said and "the newest 3 kept" in said


@pytest.mark.parametrize(
    ("args", "complaint"),
    [
        (["--backup-at", "25:00"], "--backup-at wants a time of day"),
        (["--backup-at", "3:30"], "--backup-at wants a time of day"),
        (["--reboot-at", "noon"], "--reboot-at wants a time of day"),
        (["--backup-keep", "0"], "--backup-keep wants a whole number"),
        (["--backup-keep", "many"], "--backup-keep wants a whole number"),
        (["--backup-to", "/no/such/folder"], "--backup-to wants an existing folder"),
        (["--no-such-option"], "unknown option"),
    ],
)
def test_what_it_is_given_is_checked_before_anything_happens(
    args: list[str], complaint: str
) -> None:
    said = run("--dry-run", *args, expect=2)
    assert complaint in said and "+ sudo" not in said


def test_removing_is_also_only_said_in_a_dry_run() -> None:
    said = run("--dry-run", "--remove")
    assert "would remove" in said
    commands = [line for line in said.splitlines() if "sudo " in line]
    assert all(line.startswith("+ sudo ") for line in commands), commands


def test_the_extras_can_be_taken_out_one_by_one_in_a_dry_run() -> None:
    assert "would remove trafficpi-backup.service" in run("--dry-run", "--backup-off")
    assert "would remove trafficpi-reboot.service" in run("--dry-run", "--reboot-off")
