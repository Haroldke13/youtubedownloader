import glob
import logging
import os
import re
import shutil
import threading
import uuid

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
    flash,
)
import yt_dlp

app = Flask(__name__)
# Local administrator authentication; account data stays outside source.
import importlib.util as _local_auth_importlib
from pathlib import Path as _LocalAuthPath
_local_auth_spec = _local_auth_importlib.spec_from_file_location(
    "_local_admin_auth", _LocalAuthPath(__file__).with_name("local_admin_auth.py")
)
_local_auth_module = _local_auth_importlib.module_from_spec(_local_auth_spec)
_local_auth_spec.loader.exec_module(_local_auth_module)
_local_auth_module.protect_app(app, __file__)
app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError(
        "SECRET_KEY is not set. Copy .env.example to .env and provide a value "
        "before starting the app."
    )

import bpm_engine

# Allow user to set download directory via environment variable

# Better Android detection (check several common mount points)
ANDROID_DOWNLOAD_PATHS = [
    "/storage/emulated/0/Download",
    "/sdcard/Download",
    "/storage/sdcard0/Download",
]
IS_ANDROID = any(os.path.exists(p) for p in ANDROID_DOWNLOAD_PATHS)

# Set download folder depending on platform or env var
_env_dl = os.environ.get('DOWNLOAD_DIR')
if _env_dl:
    BASE_DOWNLOAD_DIR = os.path.abspath(_env_dl)
else:
    BASE_DOWNLOAD_DIR = "/storage/emulated/0/Download/YTDownloads" if IS_ANDROID else os.path.abspath(os.path.expanduser('~/Downloads/YTDownloads'))

if not os.path.exists(BASE_DOWNLOAD_DIR):
    os.makedirs(BASE_DOWNLOAD_DIR, exist_ok=True)

# Temporary folder for single-file downloads (Method 2)
TEMP_DIR = os.path.join(BASE_DOWNLOAD_DIR, "temp_downloads")
os.makedirs(TEMP_DIR, exist_ok=True)

# YouTube refuses media URLs without a signed-in session (HTTP 403) and warns
# when no JS runtime is available to solve its player challenges. Both apply to
# the built-in downloader too, so resolve them the same way dlallbpm does.
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
COOKIES_BROWSER = os.environ.get("COOKIES_BROWSER", "chrome")
JS_RUNTIME_CANDIDATES = (
    ("deno", ("deno",)),
    ("node", ("node",)),
    ("quickjs", ("quickjs", "qjs")),
    ("bun", ("bun",)),
)


def cookie_opts(use_cookies):
    """A real cookies.txt when one is present, otherwise the browser session."""
    if not use_cookies:
        return {}
    if os.path.isfile(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0:
        return {"cookiefile": COOKIES_FILE}
    return {"cookiesfrombrowser": (COOKIES_BROWSER,)}


def js_runtime_opts():
    runtimes = {}
    for name, binaries in JS_RUNTIME_CANDIDATES:
        for binary in binaries:
            found = shutil.which(binary)
            if found:
                runtimes[name] = {"path": found}
                break
    if not runtimes:
        return {}
    return {"js_runtimes": runtimes, "remote_components": {"ejs:github"}}


# Simple global progress state for the built-in yt-dlp downloader
download_status = {
    "running": False,
    "message": "",
    "path": "",
}


def download_playlist(url, storage_path, is_playlist=False, use_cookies=False):
    global download_status
    download_status["running"] = True
    download_status["message"] = "Starting download..."
    download_status["path"] = storage_path

    ydl_opts = {
        "outtmpl": os.path.join(
            storage_path,
             "%(playlist_title)s/%(playlist_index)s - %(title)s.%(ext)s" if is_playlist else "%(title)s.%(ext)s"
        ),
        "format": "bestvideo[height<=480]+bestaudio/best[height<=480]/best[height<=480]",
        "merge_output_format": "mp4",
        "ignoreerrors": True,
        "noplaylist": not is_playlist,
        "download_archive": "downloaded.txt",
        "progress_hooks": [progress_hook],
        **cookie_opts(use_cookies),
        **js_runtime_opts(),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        download_status["message"] = "Download completed"
    except Exception as e:
        download_status["message"] = f"Error: {e}"

    download_status["running"] = False


def progress_hook(d):
    if d["status"] == "downloading":
        download_status["message"] = f"Downloading: {d.get('_percent_str', '')}"
    elif d["status"] == "finished":
        download_status["message"] = "Processing video..."


def _classic_storage_path(form):
    """Resolve the destination folder for the built-in downloader."""
    custom_folder = (form.get("custom_folder") or "").strip()
    if custom_folder and os.path.isabs(custom_folder):
        # Absolute paths are only honoured on Android device storage.
        allowed_android_roots = [
            "/storage/emulated/0",
            "/sdcard",
            "/storage/sdcard0",
            "/storage/self/primary",
            "/mnt/sdcard",
            "/mnt/media_rw",
            "/mnt/shell/emulated",
        ]
        mounts_present = any(os.path.exists(root) for root in allowed_android_roots)
        if mounts_present and (
            custom_folder.startswith("/storage")
            or any(custom_folder.startswith(root) for root in allowed_android_roots)
        ):
            return os.path.abspath(custom_folder)
        raise ValueError(
            "Absolute paths are only allowed when running on Android and must "
            "be under device storage mounts."
        )

    raw_name = custom_folder or form.get("storage") or "general"
    folder_name = re.sub(r'[^A-Za-z0-9 _-]', '', raw_name).strip() or "general"
    folder_name = os.path.basename(folder_name)
    return os.path.join(BASE_DOWNLOAD_DIR, folder_name)


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        engine = (request.form.get("engine") or "bpm").strip().lower()

        # --- dlallbpm.py: BPM-scanned downloads -------------------------
        if engine == "bpm":
            if bpm_engine.job.running:
                flash("A BPM download is already running.")
                return redirect(url_for("progress"))
            try:
                bpm_engine.job.start(request.form)
            except FileNotFoundError as exc:
                flash(str(exc))
                return redirect("/")
            except (ValueError, RuntimeError) as exc:
                flash(str(exc))
                return redirect("/")
            except Exception as exc:  # importing dlallbpm can fail loudly
                flash(f"Could not start dlallbpm: {exc}")
                return redirect("/")
            return redirect(url_for("progress"))

        # --- built-in yt-dlp downloader ---------------------------------
        url = request.form.get("url")
        if not url:
            flash("Please enter a URL")
            return redirect("/")

        try:
            storage_path = _classic_storage_path(request.form)
        except ValueError as exc:
            flash(str(exc))
            return redirect("/")

        os.makedirs(storage_path, exist_ok=True)
        is_playlist = "playlist" in url.lower()

        if not download_status["running"]:
            thread = threading.Thread(
                target=download_playlist,
                args=(url, storage_path, is_playlist),
                kwargs={"use_cookies": bool(request.form.get("use_cookies"))},
                daemon=True
            )
            thread.start()
            return redirect("/progress")

        flash("A download is already running")

    return render_template(
        "index.html",
        downloads_folder=BASE_DOWNLOAD_DIR,
        bpm_folder=str(bpm_engine.BPM_DOWNLOAD_DIR),
        bpm_available=bpm_engine.available(),
        bpm_script=str(bpm_engine.DLALLBPM_PATH),
        status=bpm_engine.job.snapshot(),
    )


@app.route("/download", methods=["POST"])
def download():
    # Accept JSON or form-encoded requests
    data = request.get_json(silent=True)
    if not data:
        data = request.form or request.values

    url = None
    if isinstance(data, dict):
        url = data.get("url")
    else:
        url = data.get("url") if data else None

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        file_path = download_video(url, use_cookies=True)
    except Exception as e:
        return jsonify({"error": f"Download failed: {e}"}), 500

    # Serve the file as attachment
    return send_file(
        file_path,
        as_attachment=True,
        download_name=os.path.basename(file_path)
    )


def download_video(url, use_cookies=True):
    """Download a single video to a unique temp file and return the filepath.

    Uses `TEMP_DIR` inside the app's base downloads folder so Android devices
    will store files under the allowed storage area.
    """
    uid = str(uuid.uuid4())
    base_path = os.path.join(TEMP_DIR, uid)
    outtmpl = base_path + ".%(ext)s"

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "noplaylist": True,
        **cookie_opts(use_cookies),
        **js_runtime_opts(),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # Find the created file (yt-dlp will pick the final extension)
    matches = glob.glob(base_path + ".*")
    if not matches:
        raise RuntimeError("yt-dlp did not produce an output file")

    return matches[0]


@app.route("/progress")
def progress():
    return render_template(
        "progress.html",
        status=download_status,
        bpm_status=bpm_engine.job.snapshot(),
    )


@app.route("/api/progress")
def api_progress():
    return jsonify({
        "classic": download_status,
        "bpm": bpm_engine.job.snapshot(),
    })


@app.route("/cancel", methods=["POST"])
def cancel():
    stopped = bpm_engine.job.cancel()
    if request.accept_mimetypes.best == "application/json" or request.is_json:
        return jsonify({"stopped": stopped, "bpm": bpm_engine.job.snapshot()})
    flash("Stopping the BPM download…" if stopped else "No BPM download is running.")
    return redirect(url_for("progress"))


def _library():
    entries = bpm_engine.scan_library(BASE_DOWNLOAD_DIR)
    roots = bpm_engine.roots(BASE_DOWNLOAD_DIR)
    scanned = sum(1 for item in entries if item["bpm"] is not None)
    return {
        "entries": entries,
        "roots": roots,
        "stats": {
            "count": len(entries),
            "bytes": sum(item["size"] for item in entries),
            "audio": sum(1 for item in entries if item["kind"] == "audio"),
            "video": sum(1 for item in entries if item["kind"] == "video"),
            "scanned": scanned,
        },
    }


@app.route("/files")
def files():
    library = _library()
    return render_template(
        "files.html",
        library=library,
        status=bpm_engine.job.snapshot(),
        bpm_folder=str(bpm_engine.BPM_DOWNLOAD_DIR),
    )


@app.route("/api/files")
def api_files():
    library = _library()
    library["bpm"] = bpm_engine.job.snapshot()
    return jsonify(library)


@app.route("/media/<root>/<path:rel>")
def media(root, rel):
    """Stream a library file; `?dl=1` downloads it instead of playing it."""
    path = bpm_engine.resolve_media(BASE_DOWNLOAD_DIR, root, rel)
    if path is None:
        abort(404)
    as_attachment = request.args.get("dl") in {"1", "true", "yes"}
    return send_file(
        path,
        as_attachment=as_attachment,
        download_name=path.name,
        conditional=True,
        max_age=0,
    )


if __name__ == "__main__":
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))
    try:
        app.run(debug=debug, host=host, port=5247)
    except Exception as e:
        logging.error(f"Error starting server: {e}")
