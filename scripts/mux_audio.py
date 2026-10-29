#!/usr/bin/env python
"""Replace the audio track of a video with a WAV/other audio file, copying the
video stream bit-for-bit (no re-encode, no quality loss).

Usage:
  python scripts/mux_audio.py --video cut.mp4 --audio mix.wav --out cut_final.mp4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import media_summary, require_ffmpeg, run, utf8_console


def main() -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bitrate", default="192k", help="AAC bitrate")
    args = ap.parse_args()
    require_ffmpeg()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", args.video, "-i", args.audio,
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", args.bitrate,
         "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-shortest", out])
    v, a = media_summary(args.video), media_summary(out)
    print(f"written {out}: video {v['duration']:.3f} s -> output {a['duration']:.3f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
