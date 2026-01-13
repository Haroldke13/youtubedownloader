import os
import threading
from flask import Flask, logging, render_template, request, redirect, url_for, flash
import yt_dlp

app = Flask(__name__)
app.secret_key = "dkjfhkjhfd28yr98fdh9s8y9y23897duegwd7893ye892u3d2ueu9328py998dh9p8whdui9a8hdy98ey7w8e8fy74328edh7823r2ged8khdih9y3249887r2y49877df2t87e87td78wt78t87ct8d7if7y278yd7fy97d8ype298y89yp39yrq4983gf7er8fgcpuid;bgcjkzxehuiywiuehiuri"

# Allow user to set download directory via environment variable
import platform, re, sys 
import pathlib

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

# Simple global progress state 
download_status = {
    "running": False,
    "message": "",
    "path": "",
}

def download_playlist(url, storage_path, is_playlist=False):
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
        "cookies.txt": "cookies.txt",
        "ignoreerrors": True,
        "noplaylist": not is_playlist,
        "download_archive": "downloaded.txt",
        "progress_hooks": [progress_hook],
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




"""@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        url = request.form.get("url")
        folder = request.form.get("storage") or "general"

        if not url:
            flash("Please enter a URL")
            return redirect("/")

        storage_path = os.path.join(BASE_DOWNLOAD_DIR, folder)
        os.makedirs(storage_path, exist_ok=True)

        is_playlist = "playlist" in url.lower()

        if not download_status["running"]:
            thread = threading.Thread(
                target=download_playlist,
                args=(url, storage_path, is_playlist),
                daemon=True
            )
            thread.start()
            return redirect("/progress")

        flash("A download is already running")

    return render_template("index.html", downloads_folder=BASE_DOWNLOAD_DIR)
"""


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        url = request.form.get("url")
        # allow either select (storage) or a custom folder input (custom_folder)
        storage_select = request.form.get("storage")
        custom_folder = (request.form.get("custom_folder") or "").strip()

        # Determine storage_path safely
        if custom_folder:
            # If user provided an absolute path, only allow it on Android and only under known mounts
            if os.path.isabs(custom_folder):
                allowed_android_roots = ["/storage/emulated/0", "/sdcard", "/storage/sdcard0"]
                if IS_ANDROID and any(custom_folder.startswith(root) for root in allowed_android_roots):
                    storage_path = os.path.abspath(custom_folder)
                else:
                    flash("Absolute paths are only allowed when running on Android and must be under the device storage mount.")
                    return redirect("/")
            else:
                # treat as relative folder name under BASE_DOWNLOAD_DIR
                folder_name = re.sub(r'[^A-Za-z0-9 _-]', '', custom_folder).strip() or "general"
                folder_name = os.path.basename(folder_name)
                storage_path = os.path.join(BASE_DOWNLOAD_DIR, folder_name)
        else:
            # use selected category (safe relative name)
            folder_name = storage_select or "general"
            folder_name = re.sub(r'[^A-Za-z0-9 _-]', '', folder_name).strip() or "general"
            folder_name = os.path.basename(folder_name)
            storage_path = os.path.join(BASE_DOWNLOAD_DIR, folder_name)

        if not url:
            flash("Please enter a URL")
            return redirect("/")

        os.makedirs(storage_path, exist_ok=True)

        is_playlist = "playlist" in url.lower()

        if not download_status["running"]:
            thread = threading.Thread(
                target=download_playlist,
                args=(url, storage_path, is_playlist),
                daemon=True
            )
            thread.start()
            return redirect("/progress")

        flash("A download is already running")

    return render_template("index.html", downloads_folder=BASE_DOWNLOAD_DIR)



@app.route("/progress")
def progress():
    return render_template("progress.html", status=download_status)


@app.route("/files")
def files():
    file_list = []
    is_electron = os.environ.get('ELECTRON_APP') == 'true'
    
    if is_electron:
        # In Electron, also show files from any absolute paths that were used
        # This is a simplified approach - in a real app, you'd track used paths
        search_dirs = [BASE_DOWNLOAD_DIR]
    else:
        search_dirs = [BASE_DOWNLOAD_DIR]

    for search_dir in search_dirs:
        if os.path.exists(search_dir):
            for root, dirs, files in os.walk(search_dir):
                for f in files:
                    file_list.append(os.path.relpath(os.path.join(root, f), search_dir))

    return render_template("files.html", files=file_list)


if __name__ == "__main__":
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))
    try:
        app.run(debug=debug, host=host, port=port)
    except Exception as e:
        logging.error(f"Error starting server: {e}")
