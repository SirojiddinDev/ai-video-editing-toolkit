# audio/

`sfx/` is where `mix_sfx.py` looks for sound-effect files (`sfx_root` in the config).
It is **git-ignored on purpose**: third-party sounds are not redistributed with this
repository. Download them yourself and keep the license notes below.

## Sounds used by the sample project (`config/sud_latifasi_laughter.json`)

All three are from Pixabay and are free to use, including commercially, without
attribution, under the [Pixabay Content License](https://pixabay.com/service/license-summary/).
The same license forbids redistributing the files **on their own**, which is why
they are not in this repo. Save them into `audio/sfx/` with these file names:

| File name expected | Title on Pixabay | Author | Page |
|---|---|---|---|
| `walla_82403.mp3` | Crowd Laughing (Walla) | freesound_community | https://pixabay.com/sound-effects/people-crowd-laughing-walla-82403/ |
| `male_laughter_92404.mp3` | Male Laughter | freesound_community | https://pixabay.com/sound-effects/people-male-laughter-92404/ |
| `men_laughing_03_242216.mp3` | Men Laughing 03 | Universfield | https://pixabay.com/sound-effects/people-men-laughing-03-242216/ |

Why these: the walla recording is a real room full of people with a natural build
and a natural decay (both parts are used); the single and small-group laughs let the
reaction start with one or two people before the hall joins. "Sudden-onset" crowd
laughs and TV laugh tracks were rejected as unnatural for a courtroom.

## Room acoustics

No impulse-response files are needed: `common.room_ir()` synthesizes a hall
response (early reflections + decaying, darkening noise tail; RT60 configurable).
If you have a measured IR you prefer, load it in `mix_sfx.py` instead of `room_ir()`.
