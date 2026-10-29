#!/usr/bin/env python
"""Layer sound effects (e.g. audience laughter) onto a finished cut, with room
acoustics, without touching the dialogue or the picture.

Pipeline
  bed  = audio of the video (or a WAV)                      -> untouched
  each layer: ffmpeg band-limit -> trim parts -> splice -> level -> fades -> pan -> place at `at`
  all layers -> synthetic room reverb (dry + wet) -> optional overall trim
  out  = bed + layers, final fade, WAV (+ MP4 with the video stream copied bit-for-bit)

Config (JSON):
{
  "bed": "out/cut.mp4",                      # video or wav; audio is decoded from it
  "video": "out/cut.mp4",                    # optional: picture to mux when bed is a WAV
  "sample_rate": 48000,
  "sfx_root": "audio/sfx",                   # where layer files live (relative to config)
  "layers": [
    {"file": "male_laughter.mp3", "parts": [[0.15, 2.45]], "at": 32.75,
     "rms_db": -27, "pan": -0.45, "fade_in": 0.03, "fade_out": 0.5,
     "highpass": 140, "lowpass": 5500},
    {"file": "crowd_walla.mp3", "parts": [[2.05, 3.45], [10.1, 12.1]], "splice_xf": 0.25,
     "match_splice": true, "splice_drop_db": 1.5, "at": 33.35,
     "rms_db": -26, "level_window": [0.6, 1.4], "fade_in": 0.6, "fade_out": 0.35}
  ],
  "reverb": {"rt60": 1.0, "length": 1.8, "predelay_ms": 14, "wet_db": -7, "seed": 7},
  "layers_trim_db": -2,
  "fade_out": 0.4,
  "out_wav": "out/mix.wav",
  "out_video": "out/cut_with_sfx.mp4"        # optional: mux with the bed video (-c:v copy)
}
Times are seconds in the final timeline. `rms_db` is the target RMS level (dBFS) of
the layer measured over `level_window` (relative to the layer start; default: all).

Usage:
  python scripts/mix_sfx.py --config config/laughter.json
  python scripts/mix_sfx.py --config ... --root project/   # resolve relative paths from here
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np

from common import (crossfade_concat, decode_audio, fade, fft_convolve, load_json, media_summary,
                    pan_gains, peak_db, require_ffmpeg, resolve, rms_db, room_ir, run, utf8_console,
                    write_wav)


def prepare_layer(cfg: dict, root: Path, sfx_root: Path, sr: int) -> np.ndarray:
    src = resolve(cfg["file"], sfx_root if not Path(cfg["file"]).is_absolute() else root)
    filters = []
    if cfg.get("highpass"):
        filters.append(f"highpass=f={cfg['highpass']}")
    if cfg.get("lowpass"):
        filters.append(f"lowpass=f={cfg['lowpass']}")
    full = decode_audio(src, sr, extra_filters=",".join(filters) or None)
    if cfg.get("mono_source"):
        full = np.repeat(full.mean(1, keepdims=True), 2, axis=1)
    parts_t = cfg.get("parts") or [[0, len(full) / sr]]
    parts = [full[int(a * sr):int(b * sr)] for a, b in parts_t]
    if cfg.get("match_splice") and len(parts) > 1:
        drop = float(cfg.get("splice_drop_db", 0.0))
        w = int(0.3 * sr)
        for i in range(1, len(parts)):
            target = rms_db(parts[i - 1][-w:]) - drop
            parts[i] = parts[i] * 10 ** ((target - rms_db(parts[i][:w])) / 20)
    layer = crossfade_concat(parts, sr, float(cfg.get("splice_xf", 0.25)))
    # level
    lw = cfg.get("level_window")
    ref = layer[int(lw[0] * sr):int(lw[1] * sr)] if lw else layer
    if "rms_db" in cfg:
        layer = layer * 10 ** ((float(cfg["rms_db"]) - rms_db(ref)) / 20)
    layer = layer * 10 ** (float(cfg.get("gain_db", 0)) / 20)
    layer = fade(layer, sr, float(cfg.get("fade_in", 0)), float(cfg.get("fade_out", 0)))
    pan = float(cfg.get("pan", 0))
    if pan:
        gl, gr = pan_gains(pan)
        mono = layer.mean(1)
        layer = np.stack([mono * gl, mono * gr], 1)
    return layer


def main() -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--root", help="base folder for relative paths (default: config folder)")
    ap.add_argument("--print-envelope", type=float, default=0.25,
                    help="print bed/layers/mix RMS every N seconds around the layers (0 = off)")
    args = ap.parse_args()
    require_ffmpeg()
    cfg_path = Path(args.config)
    root = Path(args.root) if args.root else cfg_path.parent
    cfg = load_json(cfg_path)
    sr = int(cfg.get("sample_rate", 48000))
    sfx_root = resolve(cfg.get("sfx_root", "."), root)

    bed_src = resolve(cfg["bed"], root)
    bed = decode_audio(bed_src, sr)
    T = len(bed) / sr
    print(f"bed: {bed_src.name}, {T:.3f} s")

    layers = np.zeros_like(bed)
    first, last = T, 0.0
    for lc in cfg["layers"]:
        x = prepare_layer(lc, root, sfx_root, sr)
        s = int(round(float(lc["at"]) * sr))
        e = min(s + len(x), len(layers))
        if s >= len(layers):
            print(f"  skip {lc['file']}: starts after the end of the bed")
            continue
        layers[s:e] += x[:e - s]
        first, last = min(first, s / sr), max(last, e / sr)
        print(f"  layer {lc['file']:32s} at {s / sr:7.3f}  len {len(x) / sr:6.3f} s  rms {rms_db(x):6.1f} dBFS")

    rv = cfg.get("reverb")
    if rv:
        ir = room_ir(sr, float(rv.get("rt60", 1.0)), float(rv.get("length", 1.8)),
                     float(rv.get("predelay_ms", 14)), int(rv.get("seed", 7)))
        wet = fft_convolve(layers, ir)[:len(layers)]
        layers = layers + wet * 10 ** (float(rv.get("wet_db", -7)) / 20)
    layers *= 10 ** (float(cfg.get("layers_trim_db", 0)) / 20)

    mix = bed + layers
    fo = float(cfg.get("fade_out", 0))
    if fo:
        mix = fade(mix, sr, 0, fo)
    pk = peak_db(mix)
    print(f"mix peak {pk:.2f} dBFS")
    if pk > -0.1:
        print("  !! clipping risk: lower layers_trim_db or rms_db targets")

    if args.print_envelope and last > first:
        step = args.print_envelope
        print(f"{'t':>6s} {'bed':>7s} {'layers':>7s} {'mix':>7s}   (dBFS RMS per {step}s)")
        t = max(0.0, first - 0.5)
        while t < min(T, last + 0.5):
            a, b = int(t * sr), int((t + step) * sr)
            print(f"{t:6.2f} {rms_db(bed[a:b]):7.1f} {rms_db(layers[a:b]):7.1f} {rms_db(mix[a:b]):7.1f}")
            t += step

    out_wav = resolve(cfg.get("out_wav", "mix_sfx.wav"), root)
    write_wav(out_wav, mix, sr)
    print(f"written {out_wav}")
    if cfg.get("out_video"):
        video_src = resolve(cfg.get("video", cfg["bed"]), root)   # picture source (bed may be a WAV)
        out_video = resolve(cfg["out_video"], root)
        out_video.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video_src, "-i", out_wav,
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
             "-b:a", cfg.get("audio_bitrate", "192k"), "-ar", str(sr), "-ac", "2",
             "-movflags", "+faststart", "-shortest", out_video])
        info = media_summary(out_video)
        print(f"written {out_video}: {info['duration']:.3f} s (video stream copied, audio replaced)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
