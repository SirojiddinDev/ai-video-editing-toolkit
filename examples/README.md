# Examples

## 1. Full walkthrough: the "Sud latifasi" short (5 AI-generated clips -> 36.5 s vertical video)

The project folder next to the toolkit (`../sud-latifasi/`) contains the source
clips, the delivered masters and the editing report. `run_sud_latifasi.ps1`
re-creates the delivery from scratch:

```powershell
cd D:\AI-Video-Projects
.\toolkit\examples\run_sud_latifasi.ps1
```

What happens, step by step:

| Step | Script | Result |
|---|---|---|
| analyze | `analyze_clips.py` | per-clip silence map, RMS envelope, shot changes, loudness, contact sheets, spectrograms |
| cut v1 | `build_cut.py` + `config/sud_latifasi_edl.json` | 34.25 s master, 9 segments, music tails cut, room tone continuity |
| cut v2 | `build_cut.py --save-bed` + `config/sud_latifasi_edl_v2.json` | 36.5 s picture + lossless dialogue bed WAV |
| laughter | `mix_sfx.py` + `config/sud_latifasi_laughter.json` | three laughter layers (1 person -> 3 people -> hall), courtroom reverb, muxed with the untouched picture |

The decisions behind every cut are documented in
`../sud-latifasi/reports/MONTAJ_HISOBOTI.txt` (Uzbek) and summarized in the main README.

## 2. Your own project

```powershell
# look before you cut
python toolkit\scripts\analyze_clips.py my_clips\*.mp4 --out my_project\analysis --images

# write an EDL (start from examples\example_edl.json), then
python toolkit\scripts\build_cut.py --edl my_project\edl.json --root my_clips --out my_project\cut.mp4 --save-bed

# optional: sound effects with room acoustics (start from examples\example_sfx.json)
python toolkit\scripts\mix_sfx.py --config my_project\sfx.json --root .

# optional: platform loudness
python toolkit\scripts\normalize_loudness.py --in my_project\cut.mp4 --out my_project\cut_-16lufs.mp4
```

## 3. Reading the analysis

* `analysis.md` lists, per clip, the **sound regions** (where dialogue is), the
  **silences** (where you can cut) and the **shot changes** (where a cut is invisible).
* Open `<clip>_spec.png`: speech shows as stacked harmonics; a music pad shows as a
  broad, steady band. In the sample project the pad only appeared *after* the last
  word of each clip, which is why it could be removed by simply cutting earlier and
  replacing the tail with room tone from another clip (`"a"` / `"as"` in the EDL).
