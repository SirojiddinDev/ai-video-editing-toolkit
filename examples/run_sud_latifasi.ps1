# Rebuild the "Sud latifasi" short end-to-end with the toolkit (Windows PowerShell).
# Run from the AI-Video-Projects folder:   .\toolkit\examples\run_sud_latifasi.ps1
# Needs: ffmpeg on PATH, pip install -r toolkit\requirements.txt,
#        the three Pixabay laughter files in toolkit\audio\sfx\ (see toolkit\audio\README.md).
$ErrorActionPreference = "Stop"
$py = "python"

& $py toolkit\scripts\check_env.py
if ($LASTEXITCODE -ne 0) { throw "environment not ready" }

# 1. look at the clips (writes sud-latifasi\reports\analysis\*)
& $py toolkit\scripts\analyze_clips.py (Get-ChildItem sud-latifasi\source\0*.mp4 | ForEach-Object FullName) `
     --out sud-latifasi\reports\analysis --images

# 2. v1 master: hard cuts, 50 ms audio crossfades, music tails replaced by room tone
& $py toolkit\scripts\build_cut.py --edl toolkit\config\sud_latifasi_edl.json `
     --root sud-latifasi\source --out sud-latifasi\output\cut_v1.mp4

# 3. v2 picture (extended ending) + lossless dialogue bed
& $py toolkit\scripts\build_cut.py --edl toolkit\config\sud_latifasi_edl_v2.json `
     --root sud-latifasi\source --out sud-latifasi\output\cut_v2.mp4 --save-bed

# 4. courtroom laughter layered on top, video stream copied untouched
& $py toolkit\scripts\mix_sfx.py --config toolkit\config\sud_latifasi_laughter.json --root .

Write-Host "done: sud-latifasi\output\final_sud_latifasi_v2_rebuilt.mp4"
