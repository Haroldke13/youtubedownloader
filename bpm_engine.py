"""Bridge between this Flask app and the standalone dlallbpm.py downloader.

dlallbpm.py is a terminal program: it asks its questions on stdin and reports
progress by printing to stdout. Nothing in here edits it. The module is loaded
from its own directory by path, its module-level ``progress_hook`` is swapped
for one that feeds a status dictionary, and stdout written by the worker thread
is tee'd into a ring buffer that the web UI polls. Both stay reversible, so the
same file keeps working from the command line and from post.py.
"""

import importlib.util
import os
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

# ---------------------------------------------------------------------------
# Locating dlallbpm.py
# ---------------------------------------------------------------------------
DEFAULT_DLALLBPM_PATH = Path(
    "/home/onyango/Desktop/PROJECT/personal/youtube-playlist-suite/"
    "YOUTUBE PLAYLISTS/dlallbpm.py"
)
DLALLBPM_PATH = Path(os.environ.get("DLALLBPM_PATH") or DEFAULT_DLALLBPM_PATH)

# dlallbpm.py derives this the same way; mirrored here so the files browser can
# list the library without paying for the (yt_dlp-heavy) import.
_ANDROID_DOWNLOAD_PATHS = (
    "/storage/emulated/0/Download",
    "/sdcard/Download",
    "/storage/sdcard0/Download",
)
_IS_ANDROID = any(os.path.exists(p) for p in _ANDROID_DOWNLOAD_PATHS)
BPM_DOWNLOAD_DIR = Path(
    os.environ.get("BPM_DOWNLOAD_DIR")
    or (
        "/storage/emulated/0/Download/Universal_Downloads"
        if _IS_ANDROID
        else os.path.expanduser("~/Downloads/Universal_Downloads")
    )
).resolve()

_module = None
_module_lock = threading.Lock()


def available():
    return DLALLBPM_PATH.is_file()


def load():
    """Import dlallbpm.py by path. Cached; safe to call from any thread."""
    global _module, BPM_DOWNLOAD_DIR
    if _module is not None:
        return _module
    with _module_lock:
        if _module is not None:
            return _module
        if not DLALLBPM_PATH.is_file():
            raise FileNotFoundError(f"dlallbpm.py not found at {DLALLBPM_PATH}")
        spec = importlib.util.spec_from_file_location("dlallbpm", DLALLBPM_PATH)
        module = importlib.util.module_from_spec(spec)
        # It sits beside helpers of its own suite; let those resolve.
        sys.path.insert(0, str(DLALLBPM_PATH.parent))
        try:
            sys.modules["dlallbpm"] = module
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop("dlallbpm", None)
            raise
        finally:
            try:
                sys.path.remove(str(DLALLBPM_PATH.parent))
            except ValueError:
                pass
        # Trust the module over the mirrored guess once it is loaded.
        BPM_DOWNLOAD_DIR = Path(module.BASE_DOWNLOAD_DIR).resolve()
        _module = module
        return _module


# ---------------------------------------------------------------------------
# Library scanning
# ---------------------------------------------------------------------------
# dlallbpm's own set plus the containers the classic downloader and other tools
# leave in ~/Downloads. Listing is cosmetic, so being generous here is free.
MEDIA_EXTENSIONS = {
    ".aac", ".avi", ".flac", ".flv", ".m4a", ".m4b", ".m4v", ".mka", ".mkv",
    ".mov", ".mp3", ".mp4", ".ogg", ".opus", ".ts", ".wav", ".webm", ".wma",
    ".3gp",
}
AUDIO_EXTENSIONS = {
    ".aac", ".flac", ".m4a", ".m4b", ".mka", ".mp3", ".ogg", ".opus", ".wav",
    ".wma",
}
SKIP_DIR_NAMES = {"temp_downloads", "__pycache__", ".git"}
SKIP_DIR_PREFIXES = ("bpm_scan_", ".")

# dlallbpm renames kept files to "<bpm>_<original name>".
BPM_PREFIX = re.compile(r"^(\d{2,3})_(?=\S)")


def _kind(suffix):
    return "audio" if suffix in AUDIO_EXTENSIONS else "video"


def _skip_dir(name):
    return name in SKIP_DIR_NAMES or name.startswith(SKIP_DIR_PREFIXES)


def scan_root(root, root_key, exclude=(), limit=4000):
    """Walk one library root and return plain dicts the templates can render."""
    root = Path(root)
    if not root.is_dir():
        return []
    excluded = {Path(p).resolve() for p in exclude}
    entries = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if not _skip_dir(d) and (here / d).resolve() not in excluded
        ]
        dirnames.sort()
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            suffix = Path(filename).suffix.lower()
            if suffix not in MEDIA_EXTENSIONS:
                continue
            path = here / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            rel = path.relative_to(root).as_posix()
            match = BPM_PREFIX.match(filename)
            bpm = int(match.group(1)) if match else None
            title = filename[match.end():] if match else filename
            entries.append({
                "root": root_key,
                "rel": rel,
                "name": filename,
                "title": Path(title).stem,
                "ext": suffix.lstrip("."),
                "kind": _kind(suffix),
                "bpm": bpm,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "folder": str(Path(rel).parent) if "/" in rel else "",
            })
            if len(entries) >= limit:
                return entries
    return entries


def roots(app_download_dir):
    """The two libraries the browser shows: dlallbpm's and this app's own."""
    app_root = Path(app_download_dir).resolve()
    listing = [{
        "key": "bpm",
        "label": "BPM library",
        "hint": "dlallbpm.py — BPM-scanned, prefixed with detected tempo",
        "path": str(BPM_DOWNLOAD_DIR),
    }]
    if app_root != BPM_DOWNLOAD_DIR:
        listing.append({
            "key": "app",
            "label": "Downloads",
            "hint": "this app's own yt-dlp downloads",
            "path": str(app_root),
        })
    return listing


def scan_library(app_download_dir):
    entries = []
    for root in roots(app_download_dir):
        exclude = [BPM_DOWNLOAD_DIR] if root["key"] == "app" else []
        entries.extend(scan_root(root["path"], root["key"], exclude=exclude))
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    return entries


def resolve_media(app_download_dir, root_key, rel):
    """Map (root, relative path) to a real file, refusing anything outside."""
    for root in roots(app_download_dir):
        if root["key"] != root_key:
            continue
        base = Path(root["path"]).resolve()
        candidate = (base / rel).resolve()
        if candidate == base or base not in candidate.parents:
            return None
        if not candidate.is_file():
            return None
        if candidate.suffix.lower() not in MEDIA_EXTENSIONS:
            return None
        return candidate
    return None


# ---------------------------------------------------------------------------
# Running a download
# ---------------------------------------------------------------------------
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROGRESS_GLYPH = "⬇"  # dlallbpm prints this on every progress tick
YTDLP_TICK = "[download] "


def _is_tick(line):
    """True for the progress lines that repeat many times a second."""
    return line.startswith(PROGRESS_GLYPH) or (
        line.startswith(YTDLP_TICK) and "%" in line
    )


class _TeeBuffer:
    """The binary half of a tee.

    yt-dlp does not call ``stream.write``; its ``write_string`` encodes and
    calls ``stream.buffer.write``. Without this proxy every yt-dlp line -- the
    404s and 403s most worth showing -- would slip past the log.
    """

    def __init__(self, original, tee):
        self._original = original
        self._tee = tee

    def write(self, data):
        if threading.get_ident() == self._tee.ident:
            try:
                self._tee.sink(bytes(data).decode(self._tee.encoding, "replace"))
            except Exception:
                pass
        return self._original.write(data)

    def flush(self):
        self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


class _ThreadTee:
    """Route output written by one thread into a sink, pass the rest through."""

    def __init__(self, original, ident, sink):
        self._original = original
        self.ident = ident
        self.sink = sink
        self.encoding = getattr(original, "encoding", None) or "utf-8"
        original_buffer = getattr(original, "buffer", None)
        if original_buffer is not None:
            self.buffer = _TeeBuffer(original_buffer, self)

    def write(self, text):
        if threading.get_ident() == self.ident:
            try:
                self.sink(text)
            except Exception:
                pass
        return self._original.write(text)

    def flush(self):
        self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


class DownloadJob:
    """One dlallbpm download at a time, with a pollable status."""

    def __init__(self):
        self._lock = threading.Lock()
        self._log = deque(maxlen=400)
        self._thread = None
        self._cancel = threading.Event()
        self._state = self._idle_state()

    @staticmethod
    def _idle_state():
        return {
            "running": False,
            "phase": "idle",
            "message": "No download running",
            "percent": None,
            "speed": "",
            "eta": "",
            "current_file": "",
            "folder": "",
            "output_dir": "",
            "options": {},
            "started_at": None,
            "finished_at": None,
            "summary": None,
            "error": None,
            "cancelled": False,
        }

    # -- status -----------------------------------------------------------
    def snapshot(self):
        with self._lock:
            state = dict(self._state)
            state["log"] = list(self._log)
        if state["started_at"]:
            end = state["finished_at"] or time.time()
            state["elapsed"] = int(end - state["started_at"])
        else:
            state["elapsed"] = 0
        return state

    def _set(self, **fields):
        with self._lock:
            self._state.update(fields)

    def _append_log(self, text):
        for chunk in re.split(r"[\r\n]+", _ANSI.sub("", text)):
            line = chunk.rstrip()
            if not line:
                continue
            with self._lock:
                # Progress ticks overwrite each other instead of flooding.
                if _is_tick(line) and self._log and _is_tick(self._log[-1]):
                    self._log[-1] = line
                else:
                    self._log.append(line)

    # -- control ----------------------------------------------------------
    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def cancel(self):
        if not self.running:
            return False
        self._cancel.set()
        self._set(message="Stopping — keeping whatever finished downloading…")
        return True

    def start(self, options):
        """Validate, then run download_page() on a worker thread."""
        if self.running:
            raise RuntimeError("A download is already running")
        module = load()
        call = build_call(module, options)
        self._cancel.clear()
        with self._lock:
            self._log.clear()
            self._state = self._idle_state()
            self._state.update({
                "running": True,
                "phase": "starting",
                "message": "Starting dlallbpm…",
                "folder": call["folder"],
                "output_dir": str(Path(module.BASE_DOWNLOAD_DIR) / call["folder"]),
                "options": describe(call),
                "started_at": time.time(),
            })
        self._thread = threading.Thread(
            target=self._run, args=(module, call), daemon=True
        )
        self._thread.start()
        return self.snapshot()

    # -- worker -----------------------------------------------------------
    def _progress_hook(self, data):
        if self._cancel.is_set():
            raise KeyboardInterrupt
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            done = data.get("downloaded_bytes") or 0
            percent = round(done * 100.0 / total, 1) if total else None
            self._set(
                phase="downloading",
                percent=percent,
                speed=_ANSI.sub("", (data.get("_speed_str") or "")).strip(),
                eta=_ANSI.sub("", (data.get("_eta_str") or "")).strip(),
                current_file=Path(data.get("filename") or "").name,
                message="Downloading",
            )
        elif status == "finished":
            self._set(
                phase="processing",
                percent=100.0,
                message="Merging / converting…",
                speed="",
                eta="",
            )

    def _run(self, module, call):
        original_stdout, original_stderr = sys.stdout, sys.stderr
        original_hook = module.progress_hook
        ident = threading.get_ident()
        # yt-dlp reports errors on stderr and dlallbpm talks on stdout; the web
        # log wants both.
        sys.stdout = _ThreadTee(original_stdout, ident, self._append_log)
        sys.stderr = _ThreadTee(original_stderr, ident, self._append_log)
        module.progress_hook = self._progress_hook
        try:
            result = module.download_page(**call)
            summary = result.get("summary") or {}
            if result.get("error"):
                self._set(phase="error", message=f"Failed: {result['error']}",
                          error=result["error"])
            elif result.get("interrupted") or self._cancel.is_set():
                self._set(
                    phase="cancelled", cancelled=True, summary=summary,
                    message="Stopped — kept {} scanned file(s)".format(
                        summary.get("kept", 0)),
                )
            else:
                self._set(
                    phase="done", summary=summary, percent=100.0,
                    message="Finished — {} kept, {} dropped, {} without BPM".format(
                        summary.get("kept", 0), summary.get("dropped", 0),
                        summary.get("failed", 0)),
                )
            if result.get("output_dir"):
                self._set(output_dir=str(result["output_dir"]))
        except KeyboardInterrupt:
            self._set(phase="cancelled", cancelled=True, message="Stopped")
        except BaseException as exc:  # a CLI script raising SystemExit, too
            self._set(phase="error", message=f"Failed: {exc}", error=str(exc))
            self._append_log(f"⚠️ {type(exc).__name__}: {exc}")
        finally:
            module.progress_hook = original_hook
            sys.stdout, sys.stderr = original_stdout, original_stderr
            self._set(running=False, finished_at=time.time(), speed="", eta="")


def build_call(module, options):
    """Turn form values into download_page() keyword arguments.

    Raises ValueError with a message meant for the user; dlallbpm's own
    parsers do the validating wherever it has one.
    """
    def text(name):
        return (options.get(name) or "").strip()

    def flag(name):
        return str(options.get(name) or "").lower() in {"1", "true", "on", "yes", "y"}

    url = text("url")
    if not url:
        raise ValueError("Paste at least one URL, or a path to a file of links")

    folder = re.sub(r"[^A-Za-z0-9 _-]", "", text("folder")).strip() or "downloads"
    call = {
        "url": url,
        "folder": folder,
        "audio_only": flag("audio_only"),
        "use_cookies": flag("use_cookies"),
        "bpm_range": module.parse_bpm_range(text("bpm_range")),
        "most_viewed_count": module.parse_most_viewed_count(text("top_count")),
        "new_music_only": flag("new_music_only"),
        "deduplicate_songs": flag("dedupe"),
    }

    cookie_file = text("cookie_file")
    if cookie_file:
        path = Path(cookie_file).expanduser()
        if not path.is_file():
            raise ValueError(f"Cookie file not found: {path}")
        call["cookie_file"] = str(path)
        call["use_cookies"] = True

    # Free-text filters dlallbpm parses itself inside download_page().
    for form_name, kwarg in (
        ("date_filter", "date_filter"),
        ("duration_filter", "duration_filter"),
        ("artist_filters", "artist_filters"),
        ("genre_filters", "genre_filters"),
        ("mood_filters", "mood_filters"),
        ("country_filters", "country_filters"),
    ):
        value = text(form_name)
        if value:
            call[kwarg] = value

    if call["most_viewed_count"] is not None and flag("top_per_link"):
        call["top_per_link"] = True
    return call


def describe(call):
    """Short, human-readable echo of what a job was asked to do."""
    bits = {"Folder": call["folder"]}
    bpm_range = call.get("bpm_range")
    bits["BPM"] = (
        f"{bpm_range[0]:g}–{bpm_range[1]:g}" if bpm_range else "tag all, drop none"
    )
    bits["Mode"] = "audio (mp3)" if call.get("audio_only") else "video (mp4)"
    if call.get("use_cookies"):
        bits["Cookies"] = call.get("cookie_file") or "from browser"
    if call.get("most_viewed_count"):
        bits["Top"] = str(call["most_viewed_count"]) + (
            " per link" if call.get("top_per_link") else " overall"
        )
    for label, key in (
        ("Released", "date_filter"), ("Duration", "duration_filter"),
        ("Artist", "artist_filters"), ("Genre", "genre_filters"),
        ("Mood", "mood_filters"), ("Country", "country_filters"),
    ):
        if call.get(key):
            bits[label] = str(call[key])
    if call.get("new_music_only"):
        bits["New releases only"] = "yes"
    if call.get("deduplicate_songs"):
        bits["Deduplicate"] = "yes"
    return bits


job = DownloadJob()
