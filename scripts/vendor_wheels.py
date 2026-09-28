"""Regenerate vendor/wheels so the Docker build needs no network.

Maintainers run this (it needs the internet) after changing requirements.txt:

    python scripts/vendor_wheels.py

It downloads binary wheels for CPython 3.12 on Linux x86_64 and aarch64 (the
python:3.12-slim image on Intel/AMD and ARM laptops), then the Dockerfile
installs them with `pip install --no-index`. Standard library only.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "vendor" / "wheels"
ARCHES = ("x86_64", "aarch64")
TAGS = ("manylinux2014", "manylinux_2_17", "manylinux_2_28", "manylinux_2_34")


def main() -> int:
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    for arch in ARCHES:
        command = [
            sys.executable, "-m", "pip", "download", "-q",
            "-r", str(ROOT / "requirements.txt"),
            "--only-binary=:all:", "--python-version", "3.12", "--implementation", "cp",
            "--abi", "cp312", "--abi", "abi3", "--abi", "none",
            "-d", str(TARGET),
        ]
        for tag in TAGS:
            command += ["--platform", f"{tag}_{arch}"]
        subprocess.run(command, check=True)
    wheels = sorted(path.name for path in TARGET.glob("*.whl"))
    print(f"{len(wheels)} wheels in {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
