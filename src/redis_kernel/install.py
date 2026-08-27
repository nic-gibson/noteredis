"""Install the kernelspec, pinned to the interpreter running this module.

    python -m redis_kernel.install --user

``kernelspec/kernel.json`` in this package is a *template*, not an installable
spec: nothing knows the interpreter path until install time. Its ``argv[0]`` is
:data:`PLACEHOLDER`, which fails immediately and says why if the directory is
ever installed directly -- better than the ``python`` it used to hold, which
resolved against whatever ``PATH`` the Jupyter *server* happened to have and so
worked in a single-environment setup and failed with a puzzling
``No module named redis_kernel`` anywhere else.

The wheel deliberately ships no ``share/jupyter/kernels`` data for the same
reason, so installing the kernel is this one explicit step.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from jupyter_client.kernelspec import KernelSpecManager

__all__ = ["KERNEL_NAME", "PLACEHOLDER", "install", "kernel_json", "main"]

KERNEL_NAME = "redis"
SPEC_DIR = Path(__file__).parent / "kernelspec"

#: What the template carries in ``argv[0]`` until this module replaces it.
PLACEHOLDER = "PYTHON-SET-BY-redis-kernel-install"


def kernel_json(executable: str | None = None) -> dict[str, object]:
    """The kernelspec, with ``argv[0]`` pinned to a real interpreter path."""
    spec = json.loads((SPEC_DIR / "kernel.json").read_text())
    argv = list(spec["argv"])
    if argv[0] != PLACEHOLDER:
        # The template changed without this module keeping up. Refusing beats
        # writing a spec that names an interpreter nobody chose.
        raise RuntimeError(f"kernelspec template argv[0] is {argv[0]!r}, expected {PLACEHOLDER!r}")
    argv[0] = executable or sys.executable
    spec["argv"] = argv
    return dict(spec)


def install(
    user: bool = True,
    prefix: str | None = None,
    executable: str | None = None,
) -> str:
    """Write the kernelspec and return the directory it landed in.

    Logos and anything else beside ``kernel.json`` are copied along with it, so
    the kernel gets its icon in the launcher.
    """
    with tempfile.TemporaryDirectory() as staging:
        staged = Path(staging) / KERNEL_NAME
        shutil.copytree(SPEC_DIR, staged)
        (staged / "kernel.json").write_text(json.dumps(kernel_json(executable), indent=2) + "\n")
        return str(
            KernelSpecManager().install_kernel_spec(
                str(staged),
                kernel_name=KERNEL_NAME,
                user=user and prefix is None,
                prefix=prefix,
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--user", action="store_true", help="install for the current user (the default)"
    )
    target.add_argument(
        "--sys-prefix",
        action="store_true",
        help=f"install into the current environment ({sys.prefix})",
    )
    target.add_argument("--prefix", help="install into a prefix of your choosing")
    args = parser.parse_args(argv)

    prefix = sys.prefix if args.sys_prefix else args.prefix
    where = install(user=not prefix, prefix=prefix)
    print(f"installed the '{KERNEL_NAME}' kernelspec to {where}")
    print(f"it will run: {sys.executable} -m redis_kernel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
