"""Install the kernelspec, pinned to the interpreter running this module.

    python -m redis_kernel.install --user

The static ``kernelspec/kernel.json`` in this package cannot name an
interpreter: nothing knows the path until install time. It says ``python``,
which is resolved from whatever ``PATH`` the Jupyter *server* happens to have --
fine when the server and the kernel live in one environment, and a confusing
``No module named redis_kernel`` when they do not.

This writes a spec with :data:`sys.executable` instead, so the kernel starts
under the interpreter it was installed into whatever launches it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from jupyter_client.kernelspec import KernelSpecManager

__all__ = ["KERNEL_NAME", "install", "kernel_json", "main"]

KERNEL_NAME = "redis"
SPEC_DIR = Path(__file__).parent / "kernelspec"


def kernel_json(executable: str | None = None) -> dict[str, object]:
    """The kernelspec, with ``argv[0]`` pinned to a real interpreter path."""
    spec = json.loads((SPEC_DIR / "kernel.json").read_text())
    argv = list(spec["argv"])
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
