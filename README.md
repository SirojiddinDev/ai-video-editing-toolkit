# AI Video Editing Toolkit

Script-driven, frame-accurate editing of short AI-generated videos with Python and FFmpeg:
analyze clips by their audio, cut them from a JSON edit decision list with click-free
audio joins, layer sound effects with room acoustics, and verify the result with numbers.

Built while finishing a 36-second comedy short assembled from five AI-generated clips
(Uzbek dialogue, 9:16, 720x1280 @ 24 fps). Every decision in that edit was made from
measurements, not from a timeline UI, and the scripts that made it are what this
repository packages.

## Features

* **Audio-first clip analysis** (`analyze_clips.py`): silence map, RMS envelope, sound
  regions, shot-change detection, EBU R128 loudness, contact sheets and log-frequency
  spectrograms. Tells you where words end, where music hides and where a cut is invisible.
* **EDL-based assembly** (`build_cut.py`): hard cuts from a JSON list; cut points snapped
  to the frame grid; audio joined with 50 ms equal-gain crossfades placed exactly at the
  picture cuts (room tone never drops out, words never overlap); per-segment gain; the
  picture of one clip can carry the room tone of another; output conformed to one
  resolution / frame rate / codec (H.264 + AAC).
* **Sound-effect layering** (`mix_sfx.py`): time-placed layers with band-limiting,
  RMS-targeted levels, constant-power panning, splicing of a sample's natural build and
  decay, a synthetic room impulse response (configurable RT60), and a bit-for-bit copy of
  the video stream when muxing.
* **Loudness normalization** (`normalize_loudness.py`): two-pass linear `loudnorm` with
  an optional limiter for platform targets.
* **Lossless audio replacement** (`mux_audio.py`) and an optional **ASR cross-check**
  (`transcribe.py`, faster-whisper).
* **Environment check** (`check_env.py`) for ffmpeg/ffprobe, encoders and Python packages.

### Why numpy for the audio joins?

The obvious one-liner, a chain of ffmpeg `acrossfade` filters, silently truncated the
audio to a few seconds on ffmpeg 7.1 once the chain grew past a handful of inputs.
Assembling the dialogue bed in numpy (per-segment WAVs decoded by ffmpeg) is
deterministic, sample-exact and trivial to inspect, so that is what the toolkit does.
FFmpeg still does everything it is best at: decoding, filtering, trimming, concatenating
and encoding the picture.

## Tech stack

Python 3.9+, numpy, soundfile, FFmpeg/FFprobe (libx264, aac, `silencedetect`, `astats`,
`ebur128`, `showspectrumpic`, `select=scene`), optional faster-whisper.
No GUI, no cloud, no API keys.

## Installation (Windows 11)

```powershell
# 1. FFmpeg (adds ffmpeg + ffprobe to PATH; open a new terminal afterwards)
winget install Gyan.FFmpeg

# 2. Python 3.11+ from python.org or the Microsoft Store, then:
cd path\to\toolkit
python -m pip install -r requirements.txt

# 3. verify
python scripts\check_env.py
```

Linux/macOS: `sudo apt install ffmpeg` / `brew install ffmpeg`, then the same pip step.
All scripts are plain files; run them with `python scripts/<name>.py --help`.

## Usage

```powershell
# What is in these clips? (writes analysis.json / analysis.md / PNGs)
python scripts\analyze_clips.py clips\*.mp4 --out analysis --images

# Cut them (EDL: see config/ and examples/example_edl.json)
python scripts\build_cut.py --edl my_edl.json --root clips --out out\cut.mp4 --save-bed

# Add sound effects on top, with room acoustics; picture copied untouched
python scripts\mix_sfx.py --config my_sfx.json --root .

# Swap an audio track losslessly / hit a platform loudness target
python scripts\mux_audio.py --video out\cut.mp4 --audio out\mix.wav --out out\final.mp4
python scripts\normalize_loudness.py --in out\final.mp4 --out out\final_-16lufs.mp4 --lufs -16 --tp -1.5
```

### EDL in one minute

```json
{
  "settings": {"fps": 24, "width": 720, "height": 1280, "crf": 18, "crossfade": 0.05, "fade_out": 0.5},
  "segments": [
    {"v": "clip1.mp4", "vs": 0.58, "ve": 5.54},
    {"v": "clip1.mp4", "vs": 6.54, "ve": 8.29, "gain_db": 2},
    {"v": "clip5.mp4", "vs": 2.92, "ve": 5.96, "a": "clip2.mp4", "as": 4.35}
  ]
}
```

`v`/`vs`/`ve` = picture source and in/out (seconds); `a`/`as` = take the audio from
another file/time (room tone instead of a music tail); `gain_db` = per-segment level.

## How it works

1. **Analyze**: ffmpeg filters are run once per clip and parsed into JSON. Cut candidates
   are the silences between sound regions and the detected shot boundaries.
2. **Cut**: every in/out point is rounded to a frame. For each segment the audio is
   decoded (plus a 50 ms tail), the bed is assembled with triangular crossfades so that
   segment *i+1* starts exactly at the picture cut, and ffmpeg trims, conforms and
   concatenates the video, then muxes it with the bed.
3. **Sweeten**: each SFX layer is band-limited, trimmed to the parts you want (a
   sample's natural rise and its natural decay can be spliced), leveled to an RMS target,
   faded, panned and placed; the sum goes through a synthetic hall IR and is added to the
   untouched dialogue bed. The video stream is copied, never re-encoded.
4. **Verify**: ffprobe (duration, codec, frame count), `ebur128` (integrated loudness,
   true peak), scene-cut positions, and RMS envelopes printed around the edits.

## Sample project: "Sud latifasi" (courtroom joke)

Five 10-second AI clips -> one 36.5 s vertical short. Highlights of the edit:

* 15.8 s of dead air removed; no word cut; all cuts in pauses (>= 0.25 s margin) or on
  shot boundaries found by scene detection.
* The "tragic" music pad in three clips started only *after* the last word, so it was
  removed by cutting earlier and carrying room tone from another clip under the reaction
  shots. No source separation needed, dialogue untouched.
* Punchline raised +2 dB; audience laughter starts on the exact frame the defendant
  begins to smile (1 person -> 3 people -> the hall), in a synthetic courtroom, decaying
  over an added wide shot.
* Verified: video stream of v2 identical to v1 (MD5) up to the extension; dialogue audio
  identical sample-for-sample; integrated -19 LUFS, -0.3 dBTP.

Configs: `config/sud_latifasi_edl.json`, `config/sud_latifasi_edl_v2.json`,
`config/sud_latifasi_laughter.json`. Full Uzbek report with every cut: the project folder's
`reports/MONTAJ_HISOBOTI.txt`. Media files are not part of this repository.

## Repository layout

```
toolkit/
  scripts/        check_env, analyze_clips, build_cut, mix_sfx, mux_audio, normalize_loudness, transcribe, common
  config/         EDLs and SFX config of the sample project
  examples/       templates, walkthrough, PowerShell runner
  audio/          sfx/ (git-ignored third-party sounds) + sources and licenses
  requirements.txt, LICENSE (MIT), .gitignore
```

## Limitations

* ASR (Whisper) was not reliable for Uzbek; cut verification relies on the audio
  analysis. For well-supported languages `transcribe.py` adds word timestamps.
* The room reverb is synthetic; swap in a measured IR for critical work.
* `build_cut.py` re-encodes the picture once (libx264 CRF 18 by default); stream-copy
  cutting is not attempted because it is not frame-accurate.
* No subtitle tooling yet.

## License

Code: MIT (see `LICENSE`). Media processed with the toolkit and third-party sound
effects keep their own licenses; the ones used by the sample project are listed with
links in `audio/README.md`.
