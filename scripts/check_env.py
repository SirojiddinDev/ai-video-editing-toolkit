#!/usr/bin/env python
"""Check that everything the toolkit needs is available.

Usage:  python scripts/check_env.py
Exit code 0 = ready, 1 = something required is missing.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys

from common import utf8_console

REQUIRED_PY = ["numpy", "soundfile"]
OPTIONAL_PY = ["scipy", "faster_whisper"]


def version_of(exe: str) -> str | None:
    path = shutil.which(exe)
    if not path:
        return None
    out = subprocess.run([path, "-version"], capture_output=True, text=True, errors="replace").stdout
    return out.splitlines()[0] if out else path


def main() -> int:
    utf8_console()
    ok = True
    print(f"Python        : {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 9):
        print("  !! Python 3.9+ required")
        ok = False
    for exe in ("ffmpeg", "ffprobe"):
        v = version_of(exe)
        print(f"{exe:<14}: {v or 'NOT FOUND'}")
        ok &= v is not None
    for mod in REQUIRED_PY:
        try:
            m = importlib.import_module(mod)
            print(f"{mod:<14}: {getattr(m, '__version__', 'ok')}")
        except ImportError:
            print(f"{mod:<14}: MISSING  -> pip install -r requirements.txt")
            ok = False
    for mod in OPTIONAL_PY:
        try:
            m = importlib.import_module(mod)
            print(f"{mod:<14}: {getattr(m, '__version__', 'ok')}  (optional)")
        except ImportError:
            print(f"{mod:<14}: not installed (optional, only for transcribe.py)")
    # libx264 / aac encoders present?
    if shutil.which("ffmpeg"):
        enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, errors="replace").stdout
        for name in ("libx264", "aac"):
            has = f" {name} " in enc
            print(f"encoder {name:<6}: {'ok' if has else 'MISSING'}")
            ok &= has
    print("\nRESULT:", "READY" if ok else "NOT READY")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
