"""Shared helpers for the AI Video Editing Toolkit.

Everything here is plain Python + numpy + soundfile on top of the ffmpeg/ffprobe
command-line tools. No absolute paths, no project-specific values.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

try:
    import soundfile as sf
except ImportError:  # pragma: no cover - reported by check_env.py
    sf = None


# --------------------------------------------------------------------------- #
# Console / environment
# --------------------------------------------------------------------------- #
def utf8_console() -> None:
    """Make print() safe for non-ASCII text on Windows consoles (cp1251/cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def require_ffmpeg() -> tuple[str, str]:
    """Return (ffmpeg, ffprobe) executables or exit with a helpful message."""
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ff or not fp:
        sys.exit(
            "ffmpeg/ffprobe were not found on PATH.\n"
            "Windows 11:  winget install Gyan.FFmpeg   (then open a new terminal)\n"
            "See README.md -> Installation."
        )
    return ff, fp


def run(cmd: list, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command; raise with stderr attached when it fails."""
    cmd = [str(c) for c in cmd]
    if capture:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    else:
        r = subprocess.run(cmd)
    if check and r.returncode != 0:
        err = r.stderr if capture else ""
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(cmd)}\n{err}")
    return r


def ffmpeg_stderr(args: list) -> str:
    """Run `ffmpeg <args> -f null -` and return its stderr (used for analysis filters)."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", *map(str, args), "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.stderr


def ffprobe_json(path) -> dict:
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], capture=True)
    return json.loads(r.stdout)


def media_summary(path) -> dict:
    """Compact description of the first video and audio stream."""
    info = ffprobe_json(path)
    out = {"file": str(path), "duration": float(info["format"].get("duration", 0))}
    for s in info["streams"]:
        if s["codec_type"] == "video" and "video" not in out:
            num, den = s.get("avg_frame_rate", "0/1").split("/")
            out["video"] = {
                "codec": s.get("codec_name"), "profile": s.get("profile"), "width": s.get("width"),
                "height": s.get("height"), "fps": round(int(num) / int(den), 3) if int(den) else None,
                "pix_fmt": s.get("pix_fmt"), "frames": s.get("nb_frames"),
            }
        elif s["codec_type"] == "audio" and "audio" not in out:
            out["audio"] = {
                "codec": s.get("codec_name"), "sample_rate": int(s.get("sample_rate", 0)),
                "channels": s.get("channels"), "bit_rate": s.get("bit_rate"),
            }
    return out


# --------------------------------------------------------------------------- #
# Paths / config
# --------------------------------------------------------------------------- #
def load_json(path) -> dict | list:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def resolve(path, root) -> Path:
    """Resolve a config path: absolute stays, relative is taken from `root`."""
    p = Path(path)
    return p if p.is_absolute() else Path(root) / p


def snap(t: float, fps: float) -> float:
    """Snap a time to the frame grid so video and audio lengths agree exactly."""
    return round(round(t * fps) / fps, 6)


# --------------------------------------------------------------------------- #
# Audio I/O and DSP (numpy)
# --------------------------------------------------------------------------- #
def decode_audio(path, sr: int, start: float | None = None, end: float | None = None,
                 extra_filters: str | None = None) -> np.ndarray:
    """Decode any media file to float64 stereo samples at `sr` using ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn"]
    af = []
    if start is not None or end is not None:
        af.append(f"atrim=start={start or 0}" + (f":end={end}" if end is not None else "") + ",asetpts=PTS-STARTPTS")
    if extra_filters:
        af.append(extra_filters)
    if af:
        cmd += ["-af", ",".join(af)]
    cmd += ["-f", "f32le", "-ac", "2", "-ar", str(sr), "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace"))
    data = np.frombuffer(r.stdout, dtype=np.float32).astype(np.float64)
    return data.reshape(-1, 2)


def write_wav(path, data: np.ndarray, sr: int) -> None:
    if sf is None:
        sys.exit("python package 'soundfile' is missing: pip install -r requirements.txt")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, sr, subtype="PCM_24")


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-12))


def peak_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.max(np.abs(x)) + 1e-12))


def fade(x: np.ndarray, sr: int, fade_in: float = 0.0, fade_out: float = 0.0) -> np.ndarray:
    """Squared ramp in, raised-cosine out (gentle, natural sounding)."""
    x = x.copy()
    a, b = int(fade_in * sr), int(fade_out * sr)
    if a:
        x[:a] *= (np.linspace(0, 1, a) ** 2)[:, None]
    if b:
        x[len(x) - b:] *= (0.5 * (1 + np.cos(np.pi * np.linspace(0, 1, b))))[:, None]
    return x


def fit_length(x: np.ndarray, n: int) -> np.ndarray:
    """Trim or zero-pad stereo samples to exactly n frames."""
    if len(x) >= n:
        return x[:n]
    return np.vstack([x, np.zeros((n - len(x), x.shape[1]))])


def crossfade_concat(parts: list[np.ndarray], sr: int, xf: float) -> np.ndarray:
    """Join audio parts with a short equal-gain (triangular) crossfade of `xf` seconds."""
    nx = int(xf * sr)
    out = parts[0]
    ramp = np.linspace(0, 1, nx)[:, None]
    for nxt in parts[1:]:
        if nx == 0 or len(out) < nx or len(nxt) < nx:
            out = np.vstack([out, nxt])
            continue
        out = np.vstack([out[:-nx], out[-nx:] * (1 - ramp) + nxt[:nx] * ramp, nxt[nx:]])
    return out


def cut_bed(segments: list[tuple[np.ndarray, int]], sr: int, xf: float) -> np.ndarray:
    """Assemble a dialogue bed from (samples, keep_frames) pairs.

    Each item carries `keep_frames` of audio that belong to the picture plus
    `xf` seconds of tail that is only used to fade across the next cut. Segment
    i+1 therefore starts exactly at the video cut; no audio is time-shifted.
    """
    nx = int(xf * sr)
    total = sum(n for _, n in segments)
    bed = np.zeros((total, 2))
    ramp = np.linspace(0, 1, nx, endpoint=False)[:, None] if nx else None
    pos = 0
    prev_tail = None
    for i, (d, n) in enumerate(segments):
        if i == 0 or nx == 0 or prev_tail is None or len(prev_tail) < nx:
            bed[pos:pos + n] = d[:n]
        else:
            bed[pos:pos + nx] = prev_tail * (1 - ramp) + d[:nx] * ramp
            bed[pos + nx:pos + n] = d[nx:n]
        prev_tail = d[n:n + nx] if nx else None
        pos += n
    return bed


def room_ir(sr: int, rt60: float = 1.0, length: float = 1.8, predelay_ms: float = 14.0,
            seed: int = 7, early: list | None = None) -> np.ndarray:
    """Synthetic room impulse response: early reflections + decaying, darkening noise tail.

    Good enough to place a dry sound effect "inside" a hall; not a measured IR.
    """
    rng = np.random.default_rng(seed)
    n = int(length * sr)
    t = np.arange(n) / sr
    tail = rng.standard_normal((n, 2)) * (10 ** (-3 * t / rt60))[:, None]
    # one-pole low-pass whose cutoff falls from 5 kHz to 1.5 kHz along the tail
    a = np.exp(-2 * np.pi * np.interp(t, [0, length], [5000, 1500]) / sr)
    y = np.zeros_like(tail)
    for ch in range(2):
        acc = 0.0
        col, o = tail[:, ch], y[:, ch]
        for i in range(n):
            acc = a[i] * acc + (1 - a[i]) * col[i]
            o[i] = acc
    k20 = int(0.02 * sr)
    y[:k20] *= np.linspace(0, 1, k20)[:, None]
    pre = int(predelay_ms / 1000 * sr)
    ir = np.zeros((pre + n, 2))
    ir[pre:] = y * 0.35
    early = early or [(9, .55, -.3), (17, .45, .4), (26, .38, -.6), (33, .32, .2), (44, .26, .7), (57, .2, -.2)]
    for ms, g, p in early:
        k = int(pre + ms * sr / 1000)
        if k < len(ir):
            ir[k, 0] += g * (1 - p) / 2 + g / 2
            ir[k, 1] += g * (1 + p) / 2 + g / 2
    return ir / np.sqrt((ir ** 2).sum())


def fft_convolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    n = len(x) + len(h) - 1
    big = 1 << (n - 1).bit_length()
    y = np.zeros((n, 2))
    for ch in range(2):
        y[:, ch] = np.fft.irfft(np.fft.rfft(x[:, ch], big) * np.fft.rfft(h[:, ch], big), big)[:n]
    return y


def pan_gains(pan: float) -> tuple[float, float]:
    """Constant-power pan, pan in [-1 (left) .. +1 (right)]."""
    th = (pan + 1) * np.pi / 4
    return float(np.cos(th) * np.sqrt(2)), float(np.sin(th) * np.sqrt(2))
