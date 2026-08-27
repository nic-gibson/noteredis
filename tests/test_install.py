"""Installing the kernelspec.

The one thing that must hold: the installed spec names a real interpreter, not
whatever ``python`` resolves to when the Jupyter server launches it. The wheel
ships no kernelspec of its own precisely so this is the only way one appears.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from redis_kernel.install import KERNEL_NAME, PLACEHOLDER, SPEC_DIR, install, kernel_json, main

PROJECT = Path(__file__).resolve().parents[1]

# The manifest checks read pyproject.toml, and tomllib arrived in 3.11 while
# this package supports 3.10. They are the same on every version, so running
# them on the newer ones in CI is enough.
if sys.version_info >= (3, 11):
    import tomllib

    def manifest() -> dict[str, Any]:
        return dict(tomllib.loads((PROJECT / "pyproject.toml").read_text()))

else:  # pragma: no cover - exercised on the 3.10 CI job

    def manifest() -> dict[str, Any]:
        pytest.skip("reading pyproject.toml needs tomllib (Python 3.11+)")


# --------------------------------------------------------------------------- #
# The template
# --------------------------------------------------------------------------- #


def test_the_template_carries_the_placeholder() -> None:
    """It must not carry a usable-looking interpreter like "python".

    A spec that half-works -- right in one environment, wrong in the next -- is
    worse than one that fails immediately and says what to run.
    """
    template = json.loads((SPEC_DIR / "kernel.json").read_text())
    assert template["argv"][0] == PLACEHOLDER
    assert template["argv"][1:3] == ["-m", "redis_kernel"]


def test_the_wheel_ships_no_kernelspec_data() -> None:
    """No shared-data entry: installing the template verbatim is the bug."""
    wheel = manifest()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "shared-data" not in wheel


def test_the_install_entry_point_is_declared() -> None:
    assert manifest()["project"]["scripts"] == {"redis-kernel-install": "redis_kernel.install:main"}


# --------------------------------------------------------------------------- #
# Building the spec
# --------------------------------------------------------------------------- #


def test_kernel_json_pins_the_running_interpreter() -> None:
    spec = kernel_json()
    argv = spec["argv"]
    assert isinstance(argv, list)
    assert argv[0] == sys.executable
    assert Path(argv[0]).is_absolute()
    assert PLACEHOLDER not in argv


def test_kernel_json_accepts_an_explicit_interpreter() -> None:
    assert kernel_json("/opt/python/bin/python3")["argv"][0] == "/opt/python/bin/python3"  # type: ignore[index]


def test_kernel_json_keeps_the_rest_of_the_spec() -> None:
    spec = kernel_json()
    assert spec["display_name"] == "Redis"
    assert spec["language"] == "redis"
    # Message-based interrupt, so a frontend does not have to signal the process.
    assert spec["interrupt_mode"] == "message"


def test_a_changed_template_is_refused(monkeypatch: Any, tmp_path: Path) -> None:
    """If someone puts "python" back, say so rather than shipping it."""
    (tmp_path / "kernel.json").write_text(json.dumps({"argv": ["python", "-m", "redis_kernel"]}))
    monkeypatch.setattr("redis_kernel.install.SPEC_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="expected"):
        kernel_json()


# --------------------------------------------------------------------------- #
# Writing it out
# --------------------------------------------------------------------------- #


def test_install_writes_a_usable_spec(tmp_path: Path) -> None:
    where = Path(install(prefix=str(tmp_path)))
    assert where.name == KERNEL_NAME

    spec = json.loads((where / "kernel.json").read_text())
    assert spec["argv"][0] == sys.executable

    # The logos travel with it, or the kernel has no icon in the launcher.
    assert {"logo-32x32.png", "logo-64x64.png"} <= {p.name for p in where.iterdir()}


def test_install_lands_under_the_given_prefix(tmp_path: Path) -> None:
    where = Path(install(prefix=str(tmp_path)))
    assert tmp_path in where.parents
    assert where.parent.name == "kernels"


def test_install_can_be_pointed_at_another_interpreter(tmp_path: Path) -> None:
    where = Path(install(prefix=str(tmp_path), executable="/usr/bin/python3"))
    spec = json.loads((where / "kernel.json").read_text())
    assert spec["argv"][0] == "/usr/bin/python3"


def test_installing_twice_is_fine(tmp_path: Path) -> None:
    """Re-running the installer after an upgrade must not need a manual clean."""
    first = install(prefix=str(tmp_path))
    second = install(prefix=str(tmp_path))
    assert first == second


def test_the_command_line_reports_where_it_went(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("JUPYTER_DATA_DIR", str(tmp_path))
    assert main(["--user"]) == 0
    printed = capsys.readouterr().out
    assert str(tmp_path) in printed
    assert sys.executable in printed


def test_sys_prefix_and_user_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        main(["--user", "--sys-prefix"])
