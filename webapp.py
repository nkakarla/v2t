#!/usr/bin/env python3
"""
webapp.py - V2T in the browser: upload a screen recording, get back one PDF (text, then the
screenshots) and a plain .txt of the text.

Runs on eperf4 behind Apache, which serves web/ and proxies /api/ here (deploy/v2t-common.conf).
Jobs live in memory and are converted one at a time on a background thread, so gunicorn must
run a single worker. Uploads are deleted as soon as they're converted, and results after an hour.

  POST /api/jobs                 multipart: video, title?, trim=1|0  -> 202 job
  GET  /api/jobs/<id>            -> job (state queued|working|done|error, stage, progress, position)
  GET  /api/jobs/<id>/pdf|txt    -> the result as a download
"""
import logging
import os
import queue
import re
import shutil
import threading
import time
import uuid

import cv2
from flask import Flask, abort, jsonify, request, send_file

import video2pdf as v2p

DATA = os.environ.get("V2T_DATA", os.path.expanduser("~/.v2t"))
JOBS_DIR = os.path.join(DATA, "jobs")
MAX_MB = 300
MAX_MINUTES = 15
KEEP_SECONDS = 3600
MAX_WAITING = 10          # jobs queued or working, across everyone
MAX_PER_CLIENT = 2        # ... and per IP
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}
CROP_TOP, CROP_BOTTOM = 0.07, 0.09   # iPhone status bar, Safari toolbar

log = logging.getLogger("v2t")
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

jobs = {}                 # id -> job dict; every read/write under `lock`
lock = threading.Lock()
todo = queue.Queue()
ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _err(msg, code):
    return jsonify(error=msg), code


def _client():
    # Apache appends the real client to X-Forwarded-For; earlier entries are client-supplied
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[-1].strip() or request.remote_addr


def _view(job):
    v = {k: job[k] for k in ("id", "state", "stage", "progress", "name")}
    if job["state"] == "queued":
        v["position"] = 1 + sum(j["state"] == "queued" and j["seq"] < job["seq"] for j in jobs.values())
    if job["state"] == "done":
        v.update(screens=job["screens"], paragraphs=job["paragraphs"],
                 files={k: f"api/jobs/{job['id']}/{k}" for k in ("pdf", "txt")})
    if job["state"] == "error":
        v["error"] = job["error"]
    return v


def _update(job, **kw):
    with lock:
        job.update(kw)


def _convert(job):
    src, out = job["src"], os.path.join(job["dir"], job["name"])
    try:
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise ValueError("Couldn't read this video. Try the original .mov or .mp4 screen recording.")
        fps, n = cap.get(cv2.CAP_PROP_FPS) or 30, cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        if n > 0 and n / fps > MAX_MINUTES * 60:
            raise ValueError(f"This video is {n / fps / 60:.0f} minutes long; the limit is {MAX_MINUTES}.")

        _update(job, state="working", stage="Finding each screen of text", progress=2)
        crop = (CROP_TOP, CROP_BOTTOM) if job["trim"] else (0.0, 0.0)
        frames = v2p.extract_keyframes(src, crop_top=crop[0], crop_bottom=crop[1],
                                       progress=lambda i, t: _update(job, progress=2 + int(33 * i / t)))
        if not frames:
            raise ValueError("No screens of text were found in this video.")

        def ocr_progress(i, t):
            _update(job, stage=f"Reading text: screen {i} of {t}", progress=35 + int(55 * i / t))
        _update(job, stage=f"Reading text: screen 1 of {len(frames)}", progress=35)
        paras = v2p.frames_to_paragraphs(frames, log=lambda m: None, progress=ocr_progress)

        _update(job, stage="Writing the PDF and text file", progress=92)
        v2p.text_to_pdf([(job["title"], paras, frames)], out + ".pdf")
        with open(out + ".txt", "w", encoding="utf-8") as fh:
            fh.write(v2p.paragraphs_to_text(job["title"], paras))
        _update(job, state="done", stage="Done", progress=100, screens=len(frames), paragraphs=len(paras))
    except ValueError as e:
        _update(job, state="error", error=str(e))
    except Exception:
        log.exception("job %s failed", job["id"])
        _update(job, state="error", error="Something went wrong converting this video. Try again, or try another recording.")
    finally:
        try:
            os.remove(src)
        except OSError:
            pass
        _update(job, finished=time.time())


def _sweep():
    now = time.time()
    with lock:
        old = [j for j in jobs.values() if j.get("finished") and now - j["finished"] > KEEP_SECONDS]
        for j in old:
            del jobs[j["id"]]
    for j in old:
        shutil.rmtree(j["dir"], ignore_errors=True)


def _worker():
    while True:
        try:
            job = todo.get(timeout=300)
        except queue.Empty:
            job = None
        if job:
            _convert(job)
        _sweep()


@app.post("/api/jobs")
def create_job():
    f = request.files.get("video")
    if not f or not f.filename:
        return _err("Choose a video file first.", 400)
    stem, ext = os.path.splitext(os.path.basename(f.filename))
    if ext.lower() not in VIDEO_EXT:
        return _err("That doesn't look like a video. Upload a screen recording (.mov or .mp4).", 415)
    client = _client()
    with lock:
        active = [j for j in jobs.values() if j["state"] in ("queued", "working")]
        if len(active) >= MAX_WAITING:
            return _err("V2T is busy right now. Please try again in a few minutes.", 503)
        if sum(j["client"] == client for j in active) >= MAX_PER_CLIENT:
            return _err("You already have videos converting. Wait for them to finish, then add more.", 429)

    jid = uuid.uuid4().hex
    d = os.path.join(JOBS_DIR, jid)
    os.makedirs(d)
    src = os.path.join(d, "input" + ext.lower())
    f.save(src)
    name = re.sub(r"[^\w\- ]+", "", stem).strip()[:80] or "V2T"
    title = (request.form.get("title") or "").strip()[:150] or None
    job = dict(id=jid, dir=d, src=src, name=name, title=title, trim=request.form.get("trim", "1") != "0",
               client=client, seq=time.time(), state="queued", stage="Waiting in line", progress=0)
    with lock:
        jobs[jid] = job
        view = _view(job)
    todo.put(job)
    log.info("job %s queued: %s (%.1f MB)", jid, f.filename, os.path.getsize(src) / 1e6)
    return jsonify(view), 202


def _job(jid):
    with lock:
        job = jobs.get(jid) if ID_RE.match(jid) else None
        if not job:
            abort(404)
        return job


@app.get("/api/jobs/<jid>")
def get_job(jid):
    job = _job(jid)
    with lock:
        return jsonify(_view(job))


@app.get("/api/jobs/<jid>/<kind>")
def get_file(jid, kind):
    job = _job(jid)
    if kind not in ("pdf", "txt") or job["state"] != "done":
        abort(404)
    path = os.path.join(job["dir"], f"{job['name']}.{kind}")
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/pdf" if kind == "pdf" else "text/plain; charset=utf-8")


@app.get("/api/health")
def health():
    with lock:
        busy = sum(j["state"] in ("queued", "working") for j in jobs.values())
    return jsonify(ok=True, ocr=bool(v2p.find_tesseract()), busy=busy, max_mb=MAX_MB)


@app.errorhandler(404)
def not_found(e):
    return _err("Not found. Results are kept for one hour after converting.", 404)


@app.errorhandler(413)
def too_large(e):
    return _err(f"That file is too big. The limit is {MAX_MB} MB.", 413)


# results from before a restart can't be served (jobs are in memory), so start clean
shutil.rmtree(JOBS_DIR, ignore_errors=True)
os.makedirs(JOBS_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
v2p.find_tesseract()
threading.Thread(target=_worker, daemon=True, name="v2t-worker").start()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8850)
