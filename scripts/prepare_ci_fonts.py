"""Fetch an OFL Japanese font for CI using immutable URLs and SHA-256 checks.

No system installation / admin privileges are needed. Only synthetic CI checks
use PRIZE_FONT_DIR; the portable application normally uses Windows system fonts.
"""
import hashlib
import os
from pathlib import Path
from urllib.request import urlopen

ROOT = "https://raw.githubusercontent.com/google/fonts/fbe81817505e8aeadc53cc7a38a2b76fb57c333e/ofl/notosansjp/"
FILES = {
    "NotoSansJP.ttf": ("NotoSansJP%5Bwght%5D.ttf", "c2f3b4d463500a2ddcd3849cded1fceeb9fd6d1c32e6cbecd568453ba50fc68f"),
    "OFL.txt": ("OFL.txt", "1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9"),
}


def prepare(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for name, (remote, expected) in FILES.items():
        path = directory / name
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            continue
        with urlopen(ROOT + remote, timeout=60) as response:
            content = response.read(16 * 1024 * 1024)
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"Font checksum mismatch: {name}")
        path.write_bytes(content)
    return directory


if __name__ == "__main__":
    import sys
    directory = prepare(sys.argv[1] if len(sys.argv) > 1 else "build-fonts")
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as handle:
            handle.write(f"PRIZE_FONT_DIR={directory}\nQT_QPA_FONTDIR={directory}\n")
    print(f"Verified Noto Sans JP (OFL): {directory}")
