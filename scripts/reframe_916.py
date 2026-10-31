#!/usr/bin/env python3
"""
reframe_916.py - smart reframing of a 16:9 clip to 9:16 (or any target aspect)
without stretching the picture.

The clip is cut into "shots" (time ranges). Every shot has its own crop window
policy:
  * "static"  : one fixed crop centre
  * "keys"    : crop centre interpolated between keyframes (smoothstep easing)
  * "track"   : crop centre follows the motion centroid inside a region of
                interest (x range); the raw track is median + gaussian smoothed,
                the pan speed is limited and the result is clamped to the frame.
Shots are hard cuts inside the rendered file, so the output has the same
duration and the same audio as the source and can be used in an EDL like the
original (build_cut.py) - only the picture is 9:16.

Two render modes:
  * crop   : the vertical crop window is scaled (lanczos) to the output size.
  * blurbg : the whole 16:9 picture (optionally narrowed to --fg-aspect around the
             same crop centre) is laid over a blurred, zoomed copy of itself.

Examples
  python reframe_916.py --in src/03.mp4 --out work/03_916.mp4 --shots shots.json
  python reframe_916.py --in src/03.mp4 --out work/03_blur.mp4 --shots shots.json --mode blurbg --fg-aspect 1.33
  python reframe_916.py --in src/03.mp4 --track-only --roi 560 1120   # print the motion track

shots.json
{
  "shots": [
    {"t0": 0.0, "t1": 3.3,  "mode": "static", "cx": 930},
    {"t0": 3.3, "t1": 8.4,  "mode": "track",  "roi": [560, 1120], "cx_min": 600, "cx_max": 1000,
                             "smooth": 12, "max_speed": 120, "lead": 0.0},
    {"t0": 8.4, "t1": 10.1, "mode": "keys",   "keys": [[8.4, 300], [10.1, 300]]}
  ]
}
Times are seconds in the source; "cx" values are the crop centre in source pixels.
"""
import argparse, json, os, subprocess, sys
import numpy as np

try:
    from scipy.ndimage import gaussian_filter1d, median_filter
except Exception:  # scipy optional
    gaussian_filter1d = median_filter = None


# ---------------------------------------------------------------- probing / io
def ffprobe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
                          "-show_entries", "format=duration", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    s = j["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    return int(s["width"]), int(s["height"]), fps, float(j["format"]["duration"])


def decode_gray(path, w, h):
    """Decode the clip downscaled to (w,h) 8-bit gray -> ndarray [n,h,w]."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={w}:{h}",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.uint8).reshape(-1, h, w)


# ---------------------------------------------------------------- tracking
def motion_track(gray, src_w, roi, thresh=12, accumulate=3):
    """Per-frame motion centroid (source px) inside roi=[x0,x1] (source px).
    Returns (cx[n], weight[n]); cx is nan where there is no motion."""
    n, h, w = gray.shape
    scale = src_w / w
    x0, x1 = int(roi[0] / scale), int(np.ceil(roi[1] / scale))
    xs = np.arange(x0, x1)
    g = gray.astype(np.int16)
    cx = np.full(n, np.nan)
    wt = np.zeros(n)
    for i in range(1, n):
        m = np.zeros((h, x1 - x0), bool)
        for k in range(accumulate):
            a, b = i - k, i - k - 1
            if b < 0:
                break
            m |= np.abs(g[a, :, x0:x1] - g[b, :, x0:x1]) > thresh
        cols = m.sum(0)
        tot = cols.sum()
        if tot > 15:
            cx[i] = (cols * xs).sum() / tot * scale
            wt[i] = tot
    return cx, wt


def smooth_track(cx, fps, smooth=12, max_speed=120, cx_min=None, cx_max=None):
    """Fill gaps, median+gaussian smooth, limit pan speed (px/s), clamp."""
    x = cx.copy()
    idx = np.arange(len(x))
    ok = ~np.isnan(x)
    if ok.sum() == 0:
        raise SystemExit("no motion found in ROI")
    x = np.interp(idx, idx[ok], x[ok])
    if median_filter is not None:
        x = median_filter(x, size=9, mode="nearest")
        x = gaussian_filter1d(x, sigma=smooth, mode="nearest")
    else:  # poor man's smoothing
        k = np.ones(2 * smooth + 1) / (2 * smooth + 1)
        x = np.convolve(np.pad(x, smooth, mode="edge"), k, mode="valid")
    # speed limit
    step = max_speed / fps
    y = x.copy()
    for i in range(1, len(y)):
        d = y[i] - y[i - 1]
        if abs(d) > step:
            y[i] = y[i - 1] + np.sign(d) * step
    if cx_min is not None:
        y = np.maximum(y, cx_min)
    if cx_max is not None:
        y = np.minimum(y, cx_max)
    return y


def smoothstep(a, b, t):
    t = np.clip(t, 0, 1)
    return a + (b - a) * (t * t * (3 - 2 * t))


# ---------------------------------------------------------------- centre curve
def build_centre_curve(shots, n, fps, src_w, gray=None):
    """Return cx[n] (source px) and a list of (frame, shot_index) cut points."""
    cx = np.full(n, src_w / 2.0)
    cuts = []
    for si, sh in enumerate(shots):
        f0 = int(round(sh["t0"] * fps))
        f1 = min(n, int(round(sh["t1"] * fps)))
        if f1 <= f0:
            continue
        cuts.append((f0, si))
        mode = sh.get("mode", "static")
        if mode == "static":
            cx[f0:f1] = sh["cx"]
        elif mode == "keys":
            keys = sorted(sh["keys"])
            kt = np.array([k[0] for k in keys]) * fps
            kx = np.array([k[1] for k in keys], float)
            for f in range(f0, f1):
                if f <= kt[0]:
                    cx[f] = kx[0]
                elif f >= kt[-1]:
                    cx[f] = kx[-1]
                else:
                    j = np.searchsorted(kt, f) - 1
                    cx[f] = smoothstep(kx[j], kx[j + 1], (f - kt[j]) / (kt[j + 1] - kt[j]))
        elif mode == "track":
            if gray is None:
                raise SystemExit("track mode needs decoded frames")
            raw, _ = motion_track(gray[f0:f1], src_w, sh.get("roi", [0, src_w]))
            sm = smooth_track(raw, fps, sh.get("smooth", 12), sh.get("max_speed", 120),
                              sh.get("cx_min"), sh.get("cx_max"))
            lead = sh.get("lead", 0.0)  # look-ahead in seconds (pan slightly ahead of motion)
            if lead:
                k = int(lead * fps)
                sm = np.concatenate([sm[k:], np.repeat(sm[-1], k)])[: len(sm)]
            if "start_cx" in sh:  # ease in from a given centre over ease_s seconds
                e = int(sh.get("ease_s", 0.5) * fps)
                w = np.clip(np.arange(f1 - f0) / max(e, 1), 0, 1)
                sm = smoothstep(sh["start_cx"], sm, w)
            cx[f0:f1] = sm
            sh["_track"] = {"raw": raw.tolist(), "smooth": sm.tolist()}
        else:
            raise SystemExit(f"unknown shot mode {mode}")
    return cx, cuts


# ---------------------------------------------------------------- rendering
def render(src, dst, cx, src_w, src_h, fps, out_w, out_h, mode, fg_aspect, crf, blur, cut_frames):
    crop_h = src_h
    if mode == "crop":
        crop_w = int(round(src_h * out_w / out_h))
    else:
        crop_w = int(round(src_h * fg_aspect)) if fg_aspect else src_w
        crop_w = min(crop_w, src_w)
    xs = np.clip(np.round(cx - crop_w / 2), 0, src_w - crop_w).astype(int)

    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    if mode == "crop":
        vf = f"scale={out_w}:{out_h}:flags=lanczos,format=yuv420p"
    else:
        fg_h = int(round(out_w * crop_h / crop_w)) // 2 * 2
        vf = (f"split[a][b];"
              f"[a]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},"
              f"gblur=sigma={blur},eq=brightness=-0.08:saturation=0.85[bg];"
              f"[b]scale={out_w}:{fg_h}:flags=lanczos[fg];"
              f"[bg][fg]overlay=0:(H-h)/2,format=yuv420p")
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{crop_w}x{crop_h}", "-r", f"{fps}", "-i", "-",
                            "-i", src, "-map", "0:v", "-map", "1:a?",
                            "-vf", vf, "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
                            "-r", f"{fps}", "-c:a", "copy", "-movflags", "+faststart", dst], stdin=subprocess.PIPE)
    frame_bytes = src_w * src_h * 3
    i = 0
    while True:
        buf = dec.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(src_h, src_w, 3)
        x = xs[min(i, len(xs) - 1)]
        enc.stdin.write(np.ascontiguousarray(fr[:, x:x + crop_w, :]).tobytes())
        i += 1
    enc.stdin.close()
    dec.wait()
    enc.wait()
    if enc.returncode != 0:
        raise SystemExit("ffmpeg encode failed")
    return i, crop_w, xs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst")
    ap.add_argument("--shots", help="shots JSON (see docstring)")
    ap.add_argument("--mode", choices=["crop", "blurbg"], default="crop")
    ap.add_argument("--fg-aspect", type=float, default=0.0, help="blurbg: narrow the foreground to this w/h (0 = full width)")
    ap.add_argument("--blur", type=float, default=28.0)
    ap.add_argument("--out-w", type=int, default=720)
    ap.add_argument("--out-h", type=int, default=1280)
    ap.add_argument("--crf", type=int, default=16, help="intermediate quality; the final EDL encode comes later")
    ap.add_argument("--track-only", action="store_true", help="print the motion track for --roi and exit")
    ap.add_argument("--roi", type=int, nargs=2, default=None)
    ap.add_argument("--save-curve", help="write the per-frame crop centre/x as JSON")
    a = ap.parse_args()

    src_w, src_h, fps, dur = ffprobe(a.src)
    gray = decode_gray(a.src, 320, int(round(320 * src_h / src_w)))
    n = len(gray)
    print(f"source {src_w}x{src_h} @ {fps:g} fps, {n} frames, {dur:.3f} s")

    if a.track_only:
        raw, wt = motion_track(gray, src_w, a.roi or [0, src_w])
        sm = smooth_track(raw, fps)
        for i in range(0, n, max(1, int(fps / 4))):
            print(f"t={i / fps:6.2f}  raw={raw[i]:7.1f}  smooth={sm[i]:7.1f}  w={int(wt[i])}")
        return

    shots = json.load(open(a.shots))["shots"] if a.shots else [{"t0": 0, "t1": dur + 1, "mode": "static", "cx": src_w / 2}]
    cx, cuts = build_centre_curve(shots, n, fps, src_w, gray)
    frames, crop_w, xs = render(a.src, a.dst, cx, src_w, src_h, fps, a.out_w, a.out_h, a.mode, a.fg_aspect, a.crf, a.blur, cuts)
    print(f"wrote {a.dst}: {frames} frames, crop {crop_w}x{src_h} -> {a.out_w}x{a.out_h}, mode={a.mode}")
    for f, si in cuts:
        print(f"  shot {si}: from frame {f} (t={f / fps:.3f}) mode={shots[si].get('mode')}  cx_start={cx[f]:.0f} cx_end={cx[min(n, int(round(shots[si]['t1'] * fps))) - 1]:.0f}")
    if a.save_curve:
        json.dump({"fps": fps, "crop_w": crop_w, "cx": cx.tolist(), "x": xs.tolist(),
                   "shots": [{k: v for k, v in s.items() if k != "_track"} for s in shots],
                   "tracks": [s.get("_track") for s in shots]}, open(a.save_curve, "w"), indent=1)


if __name__ == "__main__":
    main()
