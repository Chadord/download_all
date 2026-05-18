import os
import io
import re
import sys
import subprocess
import zipfile
import threading
import uuid
import time
from flask import Flask, render_template, request, jsonify, send_file

# Дозволені хости для завантаження (SSRF-захист)
_ALLOWED_HOSTS_RE = re.compile(
    r'^https?://'
    r'(?:(?:www\.)?youtube\.com|youtu\.be'
    r'|(?:www\.)?tiktok\.com'
    r'|(?:www\.)?instagram\.com'
    r'|(?:(?:[a-z]{2}|www)\.)?pinterest\.com'
    r'|(?:www\.)?twitter\.com|(?:www\.)?x\.com'
    r'|(?:www\.)?vimeo\.com'
    r'|(?:www\.)?twitch\.tv'
    r')',
    re.IGNORECASE
)

MAX_URLS_PER_REQUEST = 20


def _is_allowed_url(url: str) -> bool:
    return bool(_ALLOWED_HOSTS_RE.match(url))

app = Flask(__name__)

DOWNLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "downloads")
SERVE_FOLDER    = os.path.join(DOWNLOAD_FOLDER, ".serve")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(SERVE_FOLDER, exist_ok=True)

_jobs: dict = {}
_jobs_lock = threading.Lock()
_zips: dict = {}   # zip_id -> filepath on disk

FILE_TTL = 2 * 3600  # файли живуть 2 години

def _cleanup_loop():
    """Фонове видалення старих файлів кожні 30 хвилин."""
    while True:
        time.sleep(1800)
        cutoff = time.time() - FILE_TTL
        deleted = 0
        try:
            for fname in os.listdir(SERVE_FOLDER):
                if fname.startswith("."):
                    continue
                fpath = os.path.join(SERVE_FOLDER, fname)
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    deleted += 1
        except Exception as e:
            print(f"[CLEANUP] Помилка: {e}", flush=True)
        if deleted:
            print(f"[CLEANUP] Видалено {deleted} старих файл(ів)", flush=True)

threading.Thread(target=_cleanup_loop, daemon=True).start()

QUALITY_FORMATS: dict[str, tuple[str, str, bool]] = {
    "best":  ("bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio[ext=m4a]/bestvideo+bestaudio/best",              "mp4",  False),
    "2160":  ("bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio[ext=m4a]/best",        "mp4",  False),
    "1080":  ("bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio[ext=m4a]/best",        "mp4",  False),
    "720":   ("bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio[ext=m4a]/best",          "mp4",  False),
    "480":   ("bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio[ext=m4a]/best",          "mp4",  False),
    "360":   ("bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=360]+bestaudio[ext=m4a]/best",          "mp4",  False),
    "audio": ("bestaudio[ext=m4a]/bestaudio/best",                                                                         "mp3",  True),
}
DEFAULT_QUALITY = "720"


def _run_download(job_id: str, url: str, quality: str = DEFAULT_QUALITY, index: int = 1):
    """Запускає yt-dlp як subprocess — SEGV в C-бібліотеці не вбиває Flask."""
    fmt, merge_fmt, audio_only = QUALITY_FORMATS.get(quality, QUALITY_FORMATS[DEFAULT_QUALITY])
    folder  = SERVE_FOLDER
    outtmpl = os.path.join(folder, f"{index} - %(title)s.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--format", fmt,
        "--output", outtmpl,
        "--no-playlist",
        "--newline",
        "--format-sort", "vcodec:h264,acodec:aac",
    ]
    if audio_only:
        cmd += ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "320K"]
    else:
        cmd += ["--merge-output-format", merge_fmt]
    cmd.append(url)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:
            m = re.search(r'\[download\]\s+([\d.]+)%', line)
            if m:
                with _jobs_lock:
                    _jobs[job_id]["progress"] = f"{float(m.group(1)):.0f}%"
        proc.wait()

        if proc.returncode != 0:
            with _jobs_lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"]  = f"yt-dlp завершився з кодом {proc.returncode}"
            return

        filepath = _find_file(folder, index)
        title = url
        if filepath:
            base = os.path.splitext(os.path.basename(filepath))[0]
            prefix = f"{index} - "
            title = base[len(prefix):] if base.startswith(prefix) else base

        with _jobs_lock:
            _jobs[job_id]["status"]   = "done"
            _jobs[job_id]["title"]    = title
            _jobs[job_id]["progress"] = "100%"
            _jobs[job_id]["filepath"] = filepath

        # Авто-видалення файлу через 1 годину після завантаження
        if filepath:
            def _delete_after_ttl(path=filepath, jid=job_id):
                time.sleep(3600)
                try:
                    os.remove(path)
                except Exception:
                    pass
                with _jobs_lock:
                    _jobs.pop(jid, None)
            threading.Thread(target=_delete_after_ttl, daemon=True).start()

    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"]  = str(exc)


def _find_file(folder: str, index: int) -> str:
    prefix = f"{index} - "
    candidates = []
    try:
        for f in os.listdir(folder):
            if (f.startswith(prefix) and not f.startswith(".") and
                    not f.endswith(".part") and not f.endswith(".ytdl")):
                full = os.path.join(folder, f)
                if os.path.isfile(full):
                    candidates.append(full)
    except Exception:
        return ""
    if not candidates:
        return ""
    return max(candidates, key=os.path.getmtime)


@app.route("/")
def index():
    return render_template("downloader.html")


@app.route("/start", methods=["POST"])
def start_downloads():
    data    = request.get_json(force=True)
    urls    = [u.strip() for u in (data.get("urls") or []) if u.strip()]
    quality = data.get("quality", DEFAULT_QUALITY)

    if quality not in QUALITY_FORMATS:
        quality = DEFAULT_QUALITY
    if not urls:
        return jsonify({"error": "Немає посилань"}), 400
    if len(urls) > MAX_URLS_PER_REQUEST:
        return jsonify({"error": f"Максимум {MAX_URLS_PER_REQUEST} посилань за раз"}), 400
    invalid = [u for u in urls if not _is_allowed_url(u)]
    if invalid:
        return jsonify({"error": f"Непідтримуване джерело: {invalid[0]}"}), 400

    job_ids = []
    for idx, url in enumerate(urls, start=1):
        job_id = str(uuid.uuid4())
        with _jobs_lock:
            _jobs[job_id] = {
                "url": url, "status": "downloading", "title": url,
                "progress": "0%", "error": None, "quality": quality,
                "index": idx, "filepath": None,
            }
        threading.Thread(target=_run_download,
                         args=(job_id, url, quality, idx),
                         daemon=True).start()
        job_ids.append(job_id)

    return jsonify({"job_ids": job_ids})


@app.route("/status")
def get_status():
    ids = request.args.getlist("ids")
    with _jobs_lock:
        result = {jid: _jobs[jid] for jid in ids if jid in _jobs}
    return jsonify(result)


@app.route("/retry", methods=["POST"])
def retry():
    data   = request.get_json(force=True)
    job_id = data.get("job_id")
    with _jobs_lock:
        if job_id not in _jobs:
            return jsonify({"error": "Не знайдено"}), 404
        url     = _jobs[job_id]["url"]
        quality = _jobs[job_id].get("quality", DEFAULT_QUALITY)
        index   = _jobs[job_id].get("index",   1)
        _jobs[job_id] = {
            "url": url, "status": "downloading", "title": url,
            "progress": "0%", "error": None, "quality": quality,
            "index": index, "filepath": None,
        }
    threading.Thread(target=_run_download,
                     args=(job_id, url, quality, index),
                     daemon=True).start()
    return jsonify({"ok": True})


@app.route("/zip", methods=["POST"])
def download_zip():
    data    = request.get_json(force=True)
    job_ids = data.get("job_ids") or []
    files   = []
    with _jobs_lock:
        for jid in job_ids:
            job = _jobs.get(jid)
            if job and job.get("status") == "done":
                fp = job.get("filepath") or ""
                if fp and os.path.isfile(fp):
                    files.append(fp)
    if not files:
        return "Немає готових файлів", 404
    total_mb = sum(os.path.getsize(f) for f in files) / 1024 / 1024
    print(f"[ZIP] Починаємо пакування {len(files)} файл(ів), ~{total_mb:.1f} МБ", flush=True)
    zip_id   = str(uuid.uuid4())
    zip_path = os.path.join(SERVE_FOLDER, f"zip-{zip_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for i, fp in enumerate(files, 1):
            name = os.path.basename(fp)
            size_mb = os.path.getsize(fp) / 1024 / 1024
            print(f"[ZIP] {i}/{len(files)} додаємо: {name} ({size_mb:.1f} МБ)…", flush=True)
            zf.write(fp, name)
            print(f"[ZIP] {i}/{len(files)} готово", flush=True)
    zip_mb = os.path.getsize(zip_path) / 1024 / 1024
    print(f"[ZIP] Архів готовий: {zip_mb:.1f} МБ → /zip-file/{zip_id}", flush=True)
    _zips[zip_id] = zip_path
    return jsonify({"zip_id": zip_id})


@app.route("/zip-file/<zip_id>")
def serve_zip(zip_id: str):
    path = _zips.get(zip_id)
    if not path or not os.path.isfile(path):
        return "ZIP не знайдено або вже видалено", 404
    # Видаляємо через 5 хвилин після того як файл забрали
    def _cleanup():
        time.sleep(300)
        try:
            os.remove(path)
        except Exception:
            pass
        _zips.pop(zip_id, None)
    threading.Thread(target=_cleanup, daemon=True).start()
    return send_file(path, as_attachment=True,
                     download_name="videos.zip",
                     mimetype="application/zip")



@app.route("/file/<job_id>")
def serve_file(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job or job.get("status") != "done":
        return "Файл не готовий", 404
    filepath = job.get("filepath")
    if not filepath or not os.path.isfile(filepath):
        return "Файл не знайдено на сервері", 404
    return send_file(filepath, as_attachment=True,
                     download_name=os.path.basename(filepath),
                     mimetype="application/octet-stream")


if __name__ == "__main__":
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f"Локально:           http://127.0.0.1:5050")
    print(f"З телефона (Wi-Fi): http://{local_ip}:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
