# youtubedownloader

Flask web UI over yt-dlp for downloading YouTube playlists and single videos to a server-side folder.

## What it does

One file, `app.py` (265 lines), serves four routes:

| Route | Method | Purpose |
| --- | --- | --- |
| `/` | GET, POST | Form to submit a URL; POST starts a background download and redirects to `/progress` |
| `/progress` | GET | Renders the current global download status |
| `/files` | GET | Lists every file found under the download directory (`os.walk`) |
| `/download` | POST | Separate JSON/form endpoint: downloads one video and returns it as an attachment |

Two different download paths exist:

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
| `DOWNLOAD_DIR` | Base download directory | Android path if detected, else `~/Downloads/YTDownloads` |
| `HOST` | Bind address | `0.0.0.0` |
| `PORT` | Bind port | `5000` |
| `DEBUG` | `true` enables Flask debug mode | `False` |
| `ELECTRON_APP` | Read in `/files` but both branches are identical, so it currently does nothing | unset |

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
- **`cookies.txt` is never used.** `ydl_opts` contains `"cookies.txt": "cookies.txt"`,
  but yt-dlp's option is `cookiefile`; the unknown key is carried in the params
  dict and ignored, so `cookiefile` stays `None`. Confirmed by inspecting
  `YoutubeDL.params` directly. The committed `cookies.txt` is also 0 bytes.
  Age-restricted or sign-in-required videos will therefore fail.
- **The submit button fires two conflicting actions.** In `templates/index.html`
  the button is both `onclick="download()"` (JSON `fetch` to `/download`) and
  `type="submit"` (normal form POST to `/`), so a click triggers the single-video
  endpoint and the playlist endpoint at once.
- **The folder picker UI is missing from the live template.** `index.html`'s
  inline script looks up `#custom_folder` and `#use-default-btn`, but those
  elements exist only in the unused `index copy.html`. As shipped, the form
  posts only `url`, so every download lands in the `general` subfolder and the
  whole `storage`/`custom_folder` branch in `app.py` is unreachable.
- **The startup error handler would itself crash.** `app.py` does
  `from flask import ... logging ...` and then calls `logging.error(...)` — but
  `flask.logging` has no `error` attribute, so if `app.run()` ever raised, the
  handler would raise `AttributeError` instead of logging. Confirmed by
  inspecting the module.
- The `POST /download` temp files under `temp_downloads/` accumulate forever;
  nothing cleans them up.
- No authentication on any route, and `/files` walks and lists the entire
  download tree.
- **Downloaded media is committed to the repository.** `downloads/` holds 41 MB
  of copyrighted commercial music videos (`.mp4`, `.webm.part`) pulled from
  YouTube, plus `downloaded.txt`, the yt-dlp archive of the video IDs that were
  fetched. `__pycache__/app.cpython-312.pyc` is also tracked. None of this
  belongs in a public repository; `downloads/.keep` suggests the intent was to
  track the empty folder only.

Not verified here:

- An actual download against YouTube. TODO: verify the playlist path, the 480p
  cap, and the ffmpeg merge end to end.

LICENSE: GNU General Public License v3.
