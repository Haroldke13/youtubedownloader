# youtubedownloader

Flask web UI over yt-dlp for downloading YouTube playlists and single videos to a server-side folder, plus a front end for the standalone `dlallbpm.py` BPM downloader.

## What it does

`app.py` serves the web routes, `bpm_engine.py` bridges to `dlallbpm.py`:

| Route | Method | Purpose |
| --- | --- | --- |
| `/` | GET, POST | Two-tab form: BPM downloader (`dlallbpm.py`) or the built-in quick downloader. POST starts a background job and redirects to `/progress` |
| `/progress` | GET | Live status and console output for both downloaders |
| `/files` | GET | Media library browser: search, filter by source/type/BPM, sort, play in-page, download |
| `/media/<root>/<path>` | GET | Streams one library file (range requests supported); `?dl=1` forces a download |
| `/api/files` | GET | JSON library listing, used by the browser's rescan |
| `/api/progress` | GET | JSON status of both downloaders, polled by `/files` and `/progress` |
| `/cancel` | POST | Asks a running BPM download to stop; whatever already downloaded is still BPM-scanned and kept |
| `/download` | POST | Separate JSON/form endpoint: downloads one video and returns it as an attachment |

Three download paths exist:

- **BPM path** (`bpm_engine.job`, used by `POST /` with `engine=bpm`).
  Loads `dlallbpm.py` by path and calls its `download_page()` on a daemon
  thread. `dlallbpm` stages downloads in a temp directory, detects the tempo of
  every media file (aubio if installed, an ffmpeg energy/autocorrelation
  fallback otherwise), drops anything outside the requested BPM range and moves
  the keepers to `<BPM library>/<folder>/<uploader>/<playlist>/<bpm>_<title>.<ext>`.
  Nothing in `dlallbpm.py` is modified: the bridge swaps its module-level
  `progress_hook` for one that feeds a status dict, tees the worker thread's
  stdout and stderr into a ring buffer for the web console, and restores both
  when the job ends. Cancelling raises `KeyboardInterrupt` inside the hook,
  which `dlallbpm` already handles by scanning and keeping what it has.

- **Playlist / background path** (`download_playlist`, used by `POST /`).
  Runs `yt_dlp.YoutubeDL` on a daemon thread with
  `format="bestvideo[height<=480]+bestaudio/best[height<=480]/best[height<=480]"`
  and `merge_output_format="mp4"` — so it deliberately caps quality at 480p.
  `ignoreerrors` is on, and `download_archive="downloaded.txt"` (relative to the
  process working directory) means already-downloaded video IDs are skipped on
  later runs. If the submitted URL contains the substring `playlist`, output
  goes to `<playlist_title>/<index> - <title>.<ext>`, otherwise to
  `<title>.<ext>`. A yt-dlp `progress_hook` writes percentage strings into a
  single module-level `download_status` dict, so **only one download can run at
  a time** and there is no per-job tracking.
- **Single-video path** (`download_video`, used by `POST /download`).
  Downloads `best[ext=mp4]/best` to a UUID-named file under
  `<base>/temp_downloads/`, then `send_file`s it back to the browser as an
  attachment. The temp file is never deleted.

Destination folder resolution:

- `DOWNLOAD_DIR` env var wins if set.
- Otherwise, if any of `/storage/emulated/0/Download`, `/sdcard/Download` or
  `/storage/sdcard0/Download` exists, the app assumes Android and uses
  `/storage/emulated/0/Download/YTDownloads`.
- Otherwise `~/Downloads/YTDownloads`.

Within that base, the POST handler picks a subfolder from a `storage` select or
a `custom_folder` text field, stripping everything outside `[A-Za-z0-9 _-]` and
taking `os.path.basename`. Absolute custom paths are accepted only when an
Android storage mount is present and the path is under one of the known mount
roots; otherwise the request is rejected.

## Tech stack

Python 3, Flask, yt-dlp. `requirements.txt` is three unpinned lines:

```
Flask
yt-dlp
gunicorn
```

`gunicorn` is listed but nothing in the repository invokes it — there is no
Procfile, Dockerfile, start script, or CI config.

Templates are Jinja with inline CSS/JS: `base.html` (nav + styling),
`index.html`, `progress.html`, `files.html`. There is also `index copy.html`, an
unused older copy of the form. `static/img/` holds one "harold IT SOLUTIONS"
logo used as the favicon.

ffmpeg is required in practice: the playlist path requests separate video and
audio streams and sets `merge_output_format="mp4"`, which yt-dlp can only do by
shelling out to ffmpeg.

## Setup and running

```bash
cd youtubedownloader
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
sudo apt install ffmpeg        # needed to merge video+audio
python app.py                  # http://0.0.0.0:5000
```

Environment variables:

| Variable | Effect | Default |
| --- | --- | --- |
| `DOWNLOAD_DIR` | Base directory for the built-in downloader, and the second source in the Files browser. Keep it dedicated — everything under it is listed | Android path if detected, else `~/Downloads/YTDownloads` |
| `HOST` | Bind address | `0.0.0.0` |
| `PORT` | Bind port | `5000` |
| `DEBUG` | `true` enables Flask debug mode | `False` |
| `DLALLBPM_PATH` | Path to `dlallbpm.py` | `~/Desktop/PROJECT/personal/youtube-playlist-suite/YOUTUBE PLAYLISTS/dlallbpm.py` |
| `BPM_DOWNLOAD_DIR` | Where the BPM library lives; must match what `dlallbpm.py` writes to | Android path if detected, else `~/Downloads/Universal_Downloads` |
| `COOKIES_BROWSER` | Browser whose cookies yt-dlp reuses when `cookies.txt` is empty | `chrome` |

The BPM downloader additionally needs `ffmpeg` (always) and benefits from
`aubio` + `numpy` (better tempo detection) and a JS runtime such as `node`
(YouTube extraction). `dlallbpm.py` detects the JS runtime itself.

Because `gunicorn` is in the requirements, presumably the intent was:

```bash
gunicorn --workers 1 --bind 0.0.0.0:5000 app:app
```

Keep it at one worker — download state is a module-level dict. TODO: verify that
this is how the author deploys it.

## Status

Working prototype with several loose ends. Single commit, 2026-01-13.

Verified here:

- The app imports cleanly against installed Flask and yt-dlp, registers all four
  routes, and `/`, `/progress` and `/files` all return 200. `POST /download`
  with no URL correctly returns `{"error": "No URL provided"}` with a 400.
- The committed `downloads/` folder and `downloaded.txt` are evidence that
  playlist downloads have really run at some point.

Confirmed bugs and dead code:

- **The previous README described a different application.** It documented an
  Electron desktop app with `npm install`, `npm run dev`, `npm run build` and an
  "Electron Main Process" with native folder dialogs. There is no `package.json`,
  no Node source, and no Electron dependency anywhere in the repository. The only
  remaining trace is the inert `ELECTRON_APP` check in `/files`. This README
  replaces those claims.
- ~~**`cookies.txt` is never used.**~~ Fixed: the bogus `"cookies.txt"` key is
  gone. `cookie_opts()` now sets `cookiefile` when the repo's `cookies.txt` is
  non-empty and falls back to `cookiesfrombrowser` (`COOKIES_BROWSER`, default
  `chrome`) otherwise, and `js_runtime_opts()` passes a detected JS runtime.
  Both download paths use them. The committed `cookies.txt` is still 0 bytes, so
  in practice the browser session is what gets used.
- ~~**The submit button fires two conflicting actions.**~~ Fixed: the form is a
  plain POST to `/`, and the `fetch`-to-`/download` script is gone. `/download`
  itself still exists as an API endpoint.
- ~~**The folder picker UI is missing from the live template.**~~ Fixed: the
  quick-download tab now posts `storage` and `custom_folder`, so that branch in
  `app.py` is reachable.
- ~~**The startup error handler would itself crash.**~~ Fixed: `app.py` imports
  the standard library `logging` instead of `flask.logging`.
- The `POST /download` temp files under `temp_downloads/` accumulate forever;
  nothing cleans them up.
- Every route is behind `local_admin_auth.protect_app`, a WSGI login gate whose
  account data lives outside the repository. `/files` lists only media files,
  from the BPM library and the app's own download directory.
- **Downloaded media is committed to the repository.** `downloads/` holds 41 MB
  of copyrighted commercial music videos (`.mp4`, `.webm.part`) pulled from
  YouTube, plus `downloaded.txt`, the yt-dlp archive of the video IDs that were
  fetched. `__pycache__/app.cpython-312.pyc` is also tracked. None of this
  belongs in a public repository; `downloads/.keep` suggests the intent was to
  track the empty folder only.

Verified for the BPM integration (2026-09-20):

- `dlallbpm.py` imports cleanly by path, and a real download through
  `bpm_engine.job` against a locally served 120 BPM click track produced
  `122_click track.mp3` in the BPM library — download, aubio tempo detection,
  BPM-range filtering, rename and library listing all end to end.
- Stdout *and* stderr from the worker thread reach the web console, including
  yt-dlp's own `ERROR:` lines (yt-dlp writes through `stream.buffer`, so the tee
  proxies the binary buffer as well as `write`).
- Cancel, double-start refusal, and hook/stream restoration after failures are
  covered by the stub-module test in the scratchpad.
- `/media` refuses path traversal, unknown roots and non-media files, and
  answers range requests with `206`.

- **A real YouTube download, with and without cookies.** Without a session,
  YouTube serves the manifest but refuses the media URLs (`HTTP Error 403`,
  surfacing as `ffmpeg exited with code 8`). With `cookiesfrombrowser=("chrome",)`
  plus a JS runtime, the same video downloads and muxes fine. That is why the
  cookie switch is on by default in both tabs, and why `dlallbpm.py`'s own
  `configure_cookie_auth()` fallback to the Chrome session matters.

LICENSE: Proprietary. Copyright © 2026 Joel Harold Onyango, all rights reserved.
