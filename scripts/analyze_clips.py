#!/usr/bin/env python
"""Analyze source clips before cutting: format, silence, loudness envelope, shot changes.

This is the "listen with numbers" step that replaces a speech transcript when the
language is not well supported by ASR. It answers: where does dialogue start and
stop, where are the shot boundaries, how loud is each clip, and is there music
under the dialogue (look at the spectrogram PNGs).

Usage:
  python scripts/analyze_clips.py clip1.mp4 clip2.mp4 ... --out analysis/
  python scripts/analyze_clips.py src/*.mp4 --out analysis/ --images --rms-window 0.25

Outputs (in --out):
  analysis.json           machine-readable results for all clips
  analysis.md             human-readable summary
  <clip>_sheet.png        contact sheet (2 frames per second)      [--images]
  <clip>_spec.png         log-frequency spectrogram                [--images]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from common import ffmpeg_stderr, media_summary, require_ffmpeg, run, utf8_console


def silence(path, noise_db: float, min_dur: float) -> list[dict]:
    err = ffmpeg_stderr(["-i", path, "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}"])
    starts = [float(m) for m in re.findall(r"silence_start: ([0-9.]+)", err)]
    ends = [float(m) for m in re.findall(r"silence_end: ([0-9.]+)", err)]
    return [{"start": s, "end": e} for s, e in zip(starts, ends)]


def rms_envelope(path, window: float, sr: int = 48000) -> list[list[float]]:
    n = int(window * sr)
    err = ffmpeg_stderr([
        "-i", path, "-af",
        f"aresample={sr},asetnsamples={n},astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
    ])
    # ametadata prints to stdout when file=-; fall back to parsing stderr as well
    text = err
    out, t = [], None
    for line in text.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            t = float(m.group(1))
        m = re.search(r"RMS_level=(-?[0-9.]+|-inf)", line)
        if m and t is not None:
            v = m.group(1)
            out.append([round(t, 3), -120.0 if v == "-inf" else round(float(v), 1)])
    return out


def rms_envelope_stdout(path, window: float, sr: int = 48000) -> list[list[float]]:
    """Same as rms_envelope but reads the metadata printer from stdout (ffmpeg >= 5)."""
    n = int(window * sr)
    r = run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
        f"aresample={sr},asetnsamples={n},astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
        "-f", "null", "-",
    ], capture=True, check=False)
    out, t = [], None
    for line in r.stdout.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            t = float(m.group(1))
        m = re.search(r"RMS_level=(-?[0-9.]+|-inf)", line)
        if m and t is not None:
            v = m.group(1)
            out.append([round(t, 3), -120.0 if v == "-inf" else round(float(v), 1)])
    return out


def scene_cuts(path, threshold: float) -> list[float]:
    err = ffmpeg_stderr(["-i", path, "-vf", f"select='gt(scene,{threshold})',showinfo", "-vsync", "vfr"])
    return [round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", err)]


def loudness(path) -> dict:
    err = ffmpeg_stderr(["-i", path, "-af", "ebur128=peak=true"])
    summary = err[err.rfind("Summary:"):] if "Summary:" in err else ""
    def grab(key):
        m = re.search(key + r":\s*(-?[0-9.]+)", summary)
        return float(m.group(1)) if m else None
    return {"integrated_lufs": grab("I"), "lra_lu": grab("LRA"), "true_peak_dbtp": grab("Peak")}


def speech_regions(env: list[list[float]], floor_db: float, gap: float) -> list[list[float]]:
    """Merge RMS windows above `floor_db` into regions separated by >= gap seconds."""
    regions = []
    for t, v in env:
        if v > floor_db:
            if regions and t - regions[-1][1] <= gap:
                regions[-1][1] = t
            else:
                regions.append([t, t])
    return [[round(a, 2), round(b, 2)] for a, b in regions]


def images(path, out_dir: Path, stem: str, sheet_fps: float, cols: int) -> None:
    dur = media_summary(path)["duration"]
    rows = max(1, int(dur * sheet_fps / cols + 0.999))
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
         "-vf", f"fps={sheet_fps},scale=180:-1,tile={cols}x{rows}", "-frames:v", "1",
         out_dir / f"{stem}_sheet.png"])
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
         "-lavfi", "showspectrumpic=s=1200x320:legend=1:scale=log:fscale=log:start=50:stop=8000",
         out_dir / f"{stem}_spec.png"])


def main() -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+", help="media files to analyze")
    ap.add_argument("--out", default="analysis", help="output directory")
    ap.add_argument("--silence-db", type=float, default=-35.0, help="silencedetect threshold (dBFS)")
    ap.add_argument("--silence-min", type=float, default=0.25, help="minimum silence length (s)")
    ap.add_argument("--rms-window", type=float, default=0.25, help="RMS envelope window (s)")
    ap.add_argument("--speech-floor", type=float, default=-32.0, help="RMS above this counts as speech/sound (dBFS)")
    ap.add_argument("--scene", type=float, default=0.25, help="scene-change threshold 0..1")
    ap.add_argument("--images", action="store_true", help="also write contact sheet + spectrogram PNGs")
    ap.add_argument("--sheet-fps", type=float, default=2.0)
    ap.add_argument("--sheet-cols", type=int, default=10)
    args = ap.parse_args()
    require_ffmpeg()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    md = ["# Clip analysis", ""]
    for clip in args.clips:
        clip = Path(clip)
        if not clip.exists():
            print(f"skip (missing): {clip}")
            continue
        print(f"analyzing {clip.name} ...")
        info = media_summary(clip)
        env = rms_envelope_stdout(clip, args.rms_window)
        if not env:
            env = rms_envelope(clip, args.rms_window)
        res = {
            **info,
            "silence": silence(clip, args.silence_db, args.silence_min),
            "scene_cuts": scene_cuts(clip, args.scene),
            "loudness": loudness(clip),
            "rms_envelope": env,
            "sound_regions": speech_regions(env, args.speech_floor, args.rms_window * 1.5),
        }
        if args.images:
            images(clip, out_dir, clip.stem, args.sheet_fps, args.sheet_cols)
        results.append(res)
        v, a = res.get("video", {}), res.get("audio", {})
        md += [f"## {clip.name}",
               f"- duration {res['duration']:.3f} s; video {v.get('width')}x{v.get('height')} {v.get('fps')} fps "
               f"{v.get('codec')}; audio {a.get('codec')} {a.get('sample_rate')} Hz {a.get('channels')} ch",
               f"- loudness: {res['loudness']}",
               f"- shot changes (s): {res['scene_cuts'] or 'none detected'}",
               f"- silences (<{args.silence_db} dB, >= {args.silence_min}s): "
               + (", ".join(f"{s['start']:.2f}-{s['end']:.2f}" for s in res["silence"]) or "none"),
               f"- sound regions (RMS > {args.speech_floor} dB): "
               + (", ".join(f"{a_:.2f}-{b_:.2f}" for a_, b_ in res["sound_regions"]) or "none"),
               ""]
    with open(out_dir / "analysis.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1, ensure_ascii=False)
    with open(out_dir / "analysis.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
    print("\n".join(md))
    print(f"written: {out_dir / 'analysis.json'}, {out_dir / 'analysis.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
