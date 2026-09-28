"""Carry VERDICT's two base images to a machine with no network.

`docker compose up` builds VERDICT with the network off (every Python package
is vendored in vendor/wheels and the build runs with `network: none`). The only
outside inputs are the two standard base images below. If they are not already
in the local Docker cache of an air-gapped machine, save them on any machine
that has them and load them on the offline one:

    python scripts/offline_images.py save   # writes verdict-base-images.tar
    python scripts/offline_images.py load   # on the offline machine
    python scripts/offline_images.py check  # reports whether both are present

Standard library only.
"""

import subprocess
import sys
from pathlib import Path

IMAGES = ("python:3.12-slim", "postgres:16-alpine")
ARCHIVE = Path(__file__).resolve().parent.parent / "verdict-base-images.tar"


def present(image: str) -> bool:
    result = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    return result.returncode == 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "check"
    if command == "check":
        missing = [image for image in IMAGES if not present(image)]
        for image in IMAGES:
            print(f"{image:24} {'present' if image not in missing else 'MISSING'}")
        return 1 if missing else 0
    if command == "save":
        missing = [image for image in IMAGES if not present(image)]
        if missing:
            print("Pull these first on a connected machine: " + ", ".join(missing))
            return 1
        subprocess.run(["docker", "save", "-o", str(ARCHIVE), *IMAGES], check=True)
        print(f"saved {ARCHIVE} ({ARCHIVE.stat().st_size // (1024 * 1024)} MB)")
        return 0
    if command == "load":
        if not ARCHIVE.exists():
            print(f"{ARCHIVE} not found; copy it next to the repository root first")
            return 1
        subprocess.run(["docker", "load", "-i", str(ARCHIVE)], check=True)
        return main([argv[0], "check"])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
