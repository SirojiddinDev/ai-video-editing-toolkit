#!/usr/bin/env python
"""Optional: word-level timestamps with faster-whisper, to double-check that cut
points do not clip words.

Honest note from the sud-latifasi project: Whisper "medium" produced unusable
output for Uzbek dialogue (two clips returned nothing, the rest were garbled), so
the cut points were verified with analyze_clips.py (RMS envelope + spectrograms)
instead. For well-supported languages this script is a useful second opinion.

Requires:  pip install faster-whisper   (CPU is fine; first run downloads the model)

Usage:
  python scripts/transcribe.py clip1.mp4 clip2.mp4 --language uz --model medium --out transcript.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import utf8_console


def main() -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media", nargs="+")
    ap.add_argument("--language", default=None, help="ISO code, e.g. en, ru, uz (default: auto)")
    ap.add_argument("--model", default="small", help="tiny/base/small/medium/large-v3")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute", default="int8", help="int8 (CPU) / float16 (GPU)")
    ap.add_argument("--out", help="write the transcript here as well")
    args = ap.parse_args()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper is not installed:  pip install faster-whisper")
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute)
    lines = []
    for m in args.media:
        lines.append(f"=== {Path(m).name}")
        segs, info = model.transcribe(m, language=args.language, word_timestamps=True,
                                      beam_size=5, condition_on_previous_text=False)
        lines.append(f"(language {info.language}, p={info.language_probability:.2f})")
        for s in segs:
            lines.append(f"[{s.start:7.2f} - {s.end:7.2f}] {s.text.strip()}")
            for w in s.words or []:
                lines.append(f"      {w.start:7.2f}-{w.end:7.2f}  {w.word.strip()}  (p={w.probability:.2f})")
        print("\n".join(lines[-1:]))
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
