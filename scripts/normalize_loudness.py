#!/usr/bin/env python
"""Two-pass EBU R128 loudness normalization (ffmpeg loudnorm, linear mode).

Linear mode applies a single gain so dialogue dynamics are untouched; it refuses to
exceed the true-peak ceiling, so a very dynamic mix may land below the target.
Pass --limit to add a transparent peak limiter before measuring (changes dynamics
slightly - only do this when a platform target must be hit exactly).

NOTE: this tool was NOT used for the sud-latifasi delivery (its shout peaks at -0.3
dBTP; the master was left at -19 LUFS). It is included for platform deliveries.

Usage:
  python scripts/normalize_loudness.py --in cut.mp4 --out cut_-16.mp4            # -16 LUFS / -1.5 dBTP
  python scripts/normalize_loudness.py --in cut.mp4 --out ... --lufs -14 --tp -1 --limit
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from common import ffmpeg_stderr, media_summary, require_ffmpeg, run, utf8_console


def measure(path, lufs, tp, lra, pre):
    err = ffmpeg_stderr(["-i", path, "-af", f"{pre}loudnorm=I={lufs}:TP={tp}:LRA={lra}:print_format=json"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        raise RuntimeError("could not parse loudnorm measurement:\n" + err[-2000:])
    return json.loads(m.group(0))


def main() -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lufs", type=float, default=-16.0)
    ap.add_argument("--tp", type=float, default=-1.5)
    ap.add_argument("--lra", type=float, default=11.0)
    ap.add_argument("--limit", action="store_true", help="apply alimiter before normalizing")
    ap.add_argument("--bitrate", default="192k")
    args = ap.parse_args()
    require_ffmpeg()
    pre = f"alimiter=limit={10 ** (args.tp / 20):.4f}:attack=5:release=50," if args.limit else ""
    m = measure(args.inp, args.lufs, args.tp, args.lra, pre)
    print("measured:", {k: m[k] for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")})
    af = (f"{pre}loudnorm=I={args.lufs}:TP={args.tp}:LRA={args.lra}:linear=true:"
          f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
          f"measured_thresh={m['input_thresh']}:offset={m['target_offset']},aresample=48000")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    has_video = "video" in media_summary(args.inp)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", args.inp, "-af", af]
    if has_video:
        cmd += ["-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", args.bitrate, "-movflags", "+faststart"]
    cmd += [out]
    run(cmd)
    after = ffmpeg_stderr(["-i", out, "-af", "ebur128=peak=true"])
    summ = after[after.rfind("Summary:"):]
    vals = {k: (re.search(k + r":\s*(-?[0-9.]+)", summ) or [None, "?"])[1] for k in ("I", "LRA", "Peak")}
    print(f"result: integrated {vals['I']} LUFS, LRA {vals['LRA']} LU, true peak {vals['Peak']} dBTP")
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
