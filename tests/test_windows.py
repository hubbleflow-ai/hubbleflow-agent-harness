"""Windows behaviour, exercised from any platform.

The Windows branches can't be integration-tested on a Mac runner, so what is
pinned here is the decision-making: which shell gets invoked, how its output is
parsed back, and which commands are safe enough to skip the approval prompt.
The genuinely platform-bound checks are skipped off-Windows and marked as such.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hubbleflow.permissions import PermissionPolicy, is_read_only
from hubbleflow.tools import shell as shell_module
from hubbleflow.tools.shell import ShellSession, posix_command, windows_argv

on_windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")


# -- which shell gets invoked ----------------------------------------------

def test_windows_uses_powershell_not_cmd():
    """`create_subprocess_shell` would pick cmd.exe, which is not good enough."""
    argv = windows_argv("Get-ChildItem")
    assert argv[0] == "powershell"
    assert "-NoProfile" in argv and "-NonInteractive" in argv
    assert argv[-1].startswith("Get-ChildItem")


def test_each_platform_appends_its_own_cwd_probe():
    assert "$PWD" in posix_command("ls")
    assert "printf" in posix_command("ls")

    windows = windows_argv("dir")[-1]
    assert "Write-Output" in windows
    assert "$($PWD.Path)" in windows
    assert "printf" not in windows


def test_the_marker_is_the_same_on_both_platforms():
    """The parser is shared, so the two shells must agree on the marker."""
    marker = shell_module._CWD_MARKER
    assert marker in posix_command("ls")
    assert marker in windows_argv("dir")[-1]


# -- parsing what comes back -----------------------------------------------

def test_a_windows_style_path_is_absorbed_from_output(tmp_path, monkeypatch):
    """The cwd probe has to survive a drive-letter path and CRLF line endings."""
    session = ShellSession(tmp_path)
    target = tmp_path / "sub"
    target.mkdir()

    output = f"some output\r\n{shell_module._CWD_MARKER}{target}"
    cleaned = session._absorb_cwd(output)

    assert session.cwd == target
    assert shell_module._CWD_MARKER not in cleaned
    assert "some output" in cleaned


def test_a_cwd_that_no_longer_exists_is_ignored(tmp_path):
    """A deleted directory shouldn't strand the session pointing at nothing."""
    session = ShellSession(tmp_path)
    session._absorb_cwd(f"out\n{shell_module._CWD_MARKER}{tmp_path / 'gone'}")
    assert session.cwd == tmp_path


# -- approvals -------------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    ["dir", "Get-ChildItem", "Get-Content notes.txt", "type notes.txt", "Test-Path x", "gci"],
)
def test_powershell_read_only_commands_do_not_prompt(command):
    assert is_read_only(command), f"{command} should run unattended"


@pytest.mark.parametrize(
    "command",
    ["Remove-Item x", "Set-Content a.txt b", "New-Item f", "Get-Content a > b", "rm -rf x"],
)
def test_powershell_mutating_commands_still_prompt(command):
    assert not is_read_only(command)


def test_cmdlet_case_does_not_change_the_verdict():
    """PowerShell is case-insensitive; the allowlist has to be too."""
    for spelling in ("get-childitem", "Get-ChildItem", "GET-CHILDITEM"):
        assert is_read_only(spelling)


def test_allowlisting_a_cmdlet_covers_later_calls():
    policy = PermissionPolicy()
    call = {"command": "Set-Content notes.txt hello"}
    assert policy.needs_review("bash", call)

    policy.allow_always("bash", call)
    assert not policy.needs_review("bash", {"command": "Set-Content other.txt bye"})


# -- genuinely platform-bound ----------------------------------------------

@on_windows
async def test_powershell_actually_runs_and_reports_its_cwd(tmp_path):
    session = ShellSession(tmp_path)
    (tmp_path / "sub").mkdir()

    assert "hello" in await session.run("Write-Output hello")
    await session.run("Set-Location sub")
    assert session.cwd == tmp_path / "sub"


@on_windows
def test_the_home_config_directory_resolves(tmp_path):
    from hubbleflow.config import CONFIG_DIR

    assert CONFIG_DIR.is_absolute()
    assert CONFIG_DIR.name == ".hubbleflow"
