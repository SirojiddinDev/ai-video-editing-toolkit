#!/usr/bin/env python
"""Assemble a cut from an EDL (edit decision list) with frame-accurate hard cuts
and click-free audio joins.

Why not a single ffmpeg filter graph?  On ffmpeg 7.x a long chain of `acrossfade`
filters silently truncated the audio in our tests, so the audio is assembled in
numpy (50 ms equal-gain crossfades placed exactly at the picture cuts) and the
video is trimmed/concatenated by ffmpeg. Cut points are snapped to the frame grid
so the video and audio lengths match to the sample.

EDL format (JSON) - either a bare list of segments or an object:
{
  "settings": {"fps": 24, "width": 720, "height": 1280, "crf": 18, "preset": "slow",
               "crossfade": 0.05, "fade_out": 0.5, "audio_bitrate": "192k"},
  "segments": [
    {"v": "clip1.mp4", "vs": 0.58, "ve": 5.54},                      # picture + its own audio
    {"v": "clip1.mp4", "vs": 6.54, "ve": 8.29, "gain_db": 2},        # louder
    {"v": "clip5.mp4", "vs": 2.92, "ve": 5.96,
     "a": "clip2.mp4", "as": 4.35}                                   # picture with room tone from elsewhere
  ]
}
Paths inside the EDL are relative to --root (default: the EDL's folder).

Usage:
  python scripts/build_cut.py --edl config/my_edl.json --root path/to/clips --out out/cut.mp4
  python scripts/build_cut.py --edl ... --out ... --save-bed      # also keep the mixed dialogue WAV
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

from common import (cut_bed, decode_audio, fade, fit_length, load_json, media_summary,
                    peak_db, require_ffmpeg, resolve, run, snap, utf8_console, write_wav)

DEFAULTS = {"fps": 24, "width": 720, "height": 1280, "crf": 18, "preset": "slow",
            "crossfade": 0.05, "fade_out": 0.5, "audio_bitrate": "192k", "sample_rate": 48000,
            "normalize_video": True}


def load_edl(path):
    data = load_json(path)
    if isinstance(data, list):
        return dict(DEFAULTS), data
    settings = {**DEFAULTS, **data.get("settings", {})}
    return settings, data["segments"]


def build(edl_path: Path, root: Path, out: Path, save_bed: bool, tmp_dir: Path | None, overrides: dict) -> dict:
    require_ffmpeg()
    settings, segments = load_edl(edl_path)
    settings.update({k: v for k, v in overrides.items() if v is not None})
    fps, sr, xf = settings["fps"], settings["sample_rate"], settings["crossfade"]
    W, H = settings["width"], settings["height"]

    # 1. snap cut points to the frame grid and report
    print(f"{'source':30s} {'in':>8s} {'out':>8s} {'len':>7s}   audio")
    for seg in segments:
        seg["vs"], seg["ve"] = snap(seg["vs"], fps), snap(seg["ve"], fps)
        if seg["ve"] <= seg["vs"]:
            raise ValueError(f"empty segment: {seg}")
        a = f"{seg['a']} @{seg['as']}" if "a" in seg else "(own)"
        if "gain_db" in seg:
            a += f"  gain {seg['gain_db']:+} dB"
        print(f"{seg['v']:30s} {seg['vs']:8.3f} {seg['ve']:8.3f} {seg['ve'] - seg['vs']:7.3f}   {a}")

    # 2. audio: decode each segment (+xf tail) and assemble the bed in numpy
    parts = []
    for i, seg in enumerate(segments):
        ln = seg["ve"] - seg["vs"]
        extra = 0.0 if i == len(segments) - 1 else xf
        a_src = resolve(seg.get("a", seg["v"]), root)
        a_start = float(seg.get("as", seg["vs"]))
        gain = 10 ** (float(seg.get("gain_db", 0)) / 20)
        d = decode_audio(a_src, sr, a_start, a_start + ln + extra) * gain
        parts.append((fit_length(d, int(round((ln + extra) * sr))), int(round(ln * sr))))
    bed = cut_bed(parts, sr, xf)
    total = len(bed) / sr
    bed = fade(bed, sr, 0.0, float(settings["fade_out"]))
    pk = peak_db(bed)
    print(f"bed: {total:.3f} s, peak {pk:.2f} dBFS")
    if pk > -0.1:
        print("  !! audio peaks near 0 dBFS - consider lowering gain_db on loud segments")

    work = Path(tmp_dir) if tmp_dir else Path(tempfile.mkdtemp(prefix="build_cut_"))
    work.mkdir(parents=True, exist_ok=True)
    bed_wav = work / "bed.wav"
    write_wav(bed_wav, bed, sr)

    # 3. video: trim + (optional) conform + concat, mux with the bed
    srcs: list[Path] = []
    def idx(p: Path) -> int:
        if p not in srcs:
            srcs.append(p)
        return srcs.index(p)
    conform = (f",scale={W}:{H}:force_original_aspect_ratio=decrease,"
               f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}" if settings["normalize_video"] else "")
    fc, labels = [], []
    for i, seg in enumerate(segments):
        vi = idx(resolve(seg["v"], root))
        fc.append(f"[{vi}:v]trim=start={seg['vs']}:end={seg['ve']},setpts=PTS-STARTPTS{conform}[v{i}]")
        labels.append(f"[v{i}]")
    fc.append("".join(labels) + f"concat=n={len(segments)}:v=1:a=0[vout]")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats", "-y"]
    for s in srcs:
        cmd += ["-i", s]
    cmd += ["-i", bed_wav, "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", f"{len(srcs)}:a",
            "-r", str(fps), "-c:v", "libx264", "-preset", settings["preset"], "-crf", str(settings["crf"]),
            "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-c:a", "aac", "-b:a", settings["audio_bitrate"], "-ar", str(sr), "-ac", "2",
            "-movflags", "+faststart", "-shortest", out]
    out.parent.mkdir(parents=True, exist_ok=True)
    run(cmd)

    if save_bed:
        shutil.copyfile(bed_wav, out.with_suffix(".bed.wav"))
    if not tmp_dir:
        shutil.rmtree(work, ignore_errors=True)

    info = media_summary(out)
    print(f"written {out}: {info['duration']:.3f} s, {info['video']['width']}x{info['video']['height']} "
          f"{info['video']['fps']} fps, audio {info['audio']['sample_rate']} Hz")
    if abs(info["duration"] - total) > 1.0 / fps:
        print(f"  !! duration mismatch: expected {total:.3f}")
    return info


def main() -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edl", required=True, help="EDL JSON file")
    ap.add_argument("--root", help="folder the EDL's media paths are relative to (default: EDL folder)")
    ap.add_argument("--out", required=True, help="output .mp4")
    ap.add_argument("--save-bed", action="store_true", help="also write <out>.bed.wav (dialogue mix)")
    ap.add_argument("--tmp", help="keep intermediate files in this folder")
    ap.add_argument("--fps", type=int)
    ap.add_argument("--size", help="WIDTHxHEIGHT, e.g. 720x1280")
    ap.add_argument("--crf", type=int)
    ap.add_argument("--preset")
    ap.add_argument("--crossfade", type=float, help="audio crossfade at cuts, seconds")
    ap.add_argument("--fade-out", type=float)
    args = ap.parse_args()
    edl = Path(args.edl)
    root = Path(args.root) if args.root else edl.parent
    overrides = {"fps": args.fps, "crf": args.crf, "preset": args.preset,
                 "crossfade": args.crossfade, "fade_out": args.fade_out}
    if args.size:
        w, h = args.size.lower().split("x")
        overrides.update(width=int(w), height=int(h))
    build(edl, root, Path(args.out), args.save_bed, Path(args.tmp) if args.tmp else None, overrides)
    return 0


if __name__ == "__main__":
    sys.exit(main())
