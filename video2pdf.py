#!/usr/bin/env python3
"""
video2pdf.py - turn a screen video of scrolling text into a readable PDF.

Pipeline:
  1. Track the scroll offset frame-to-frame (phase correlation), so both
     continuous flick-scrolls and scroll-pause-scroll recordings work.
  2. Keep a frame each time ~60% of a screen of new content has scrolled in
     (consecutive kept frames overlap by --overlap), plus page changes.
  3. mode=images -> one PDF page per unique frame (fast, exact look).
     mode=text   -> OCR each kept frame, merge the overlapping lines between
                    consecutive frames, and reflow into a clean text PDF.

Install:
  pip install opencv-python pillow pytesseract reportlab
  brew install tesseract        # macOS  (apt install tesseract-ocr on Linux)

Usage:
  python video2pdf.py input.mov out.pdf --mode text
  python video2pdf.py input.mov out.pdf --mode images --crop-top 0 --crop-bottom 0
  (crop defaults trim the iPhone status bar and Safari toolbar)
"""
import argparse
import difflib

import cv2
import numpy as np
from PIL import Image


# ---------- frame selection ----------
SMALL_W = 240  # tracking width; height follows the frame's aspect ratio


def _small_gray(frame):
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    return cv2.resize(g, (SMALL_W, int(h * SMALL_W / w)), interpolation=cv2.INTER_AREA).astype(np.float32)


def _scroll_dy(prev, cur, win, change):
    """Vertical content shift prev -> cur in small-frame pixels (+ = scrolled down).

    Returns None when cur isn't prev scrolled, i.e. the rows that should line up
    after undoing the shift still differ by more than `change` on average.
    """
    (_, dy), resp = cv2.phaseCorrelate(prev, cur, win)
    s, h = int(round(-dy)), prev.shape[0]
    if resp < 0.05 or abs(s) > 0.8 * h:
        return None
    a, b = (prev[s:], cur[:h - s]) if s >= 0 else (prev[:h + s], cur[-s:])
    return -dy if np.mean(np.abs(a - b)) <= change else None


def extract_keyframes(path, fps=30.0, change=18.0, overlap=0.4, crop_top=0.0, crop_bottom=0.0, progress=None):
    """Keep a frame each time ~(1-overlap) of a screen of new content has scrolled in.

    Scroll offset is tracked with phase correlation, so continuous flick-scrolls
    work (screen recordings have no motion blur). Content that changes without
    scrolling (page switch) is caught when the shift-compensated diff exceeds `change`.
    progress(frames_read, total_frames) is called every second of video.
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(round(src_fps / fps)))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    kept, prev, win = [], None, None
    pos = kept_pos = 0.0   # current / last-kept scroll offset (small-frame px)
    pending = None         # latest frame scrolled past the last kept one
    settling = False       # page just (re)appeared: keep refreshing kept[-1] until scrolling starts
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            h = frame.shape[0]
            frame = frame[int(h * crop_top): h - int(h * crop_bottom)]
            sg = _small_gray(frame)
            H = sg.shape[0]
            if win is None:
                win = cv2.createHanningWindow(sg.shape[::-1], cv2.CV_32F)
            dy = None if prev is None else _scroll_dy(prev, sg, win, change)
            if dy is None:
                # first frame, or content jumped with no trackable scroll -> new page.
                # A jump right after the start is iOS Control Center closing, so
                # whatever was captured before it isn't content.
                if idx < 2 * src_fps:
                    kept = []
                kept.append(frame); kept_pos = pos
                pending, settling = None, True
            else:
                pos += dy
                if settling and pos - kept_pos < 0.05 * H:
                    kept[-1] = frame     # still transitioning in; later frame is cleaner
                else:
                    settling = False
                    if pos - kept_pos >= (1 - overlap) * H:
                        kept.append(frame); kept_pos = pos
                        pending = None
                    elif pos - kept_pos > 0.1 * H:
                        pending = frame
            prev = sg
        idx += 1
        if progress and idx % max(1, int(src_fps)) == 0:
            progress(idx, max(total, idx))
    cap.release()
    if pending is not None:  # tail of the scroll that never reached a full step
        kept.append(pending)
    return kept


# ---------- image PDF ----------
def frames_to_image_pdf(frames, out):
    imgs = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames]
    imgs[0].save(out, save_all=True, append_images=imgs[1:], resolution=150)


# ---------- OCR + merge ----------
def ocr_lines(frame, min_conf=60):
    """OCR a frame into lines, dropping low-confidence ones (images, tables, cut-off text)."""
    import pytesseract
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if np.mean(g) < 110:          # dark mode -> invert for Tesseract
        g = 255 - g
    g = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    d = pytesseract.image_to_data(g, config="--psm 6", output_type=pytesseract.Output.DICT)
    rows = {}
    for i, word in enumerate(d["text"]):
        if word.strip():
            key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            rows.setdefault(key, []).append((word, float(d["conf"][i])))
    lines = []
    for key in sorted(rows):
        words = rows[key]
        if np.mean([c for _, c in words]) >= min_conf:
            lines.append(" ".join(w for w, _ in words))
    return lines


def _same(a, b, thr=0.85):
    return difflib.SequenceMatcher(None, a, b).ratio() >= thr


def merge_overlap(acc, new, max_overlap=80):
    """Append `new` lines to `acc`, skipping the part that overlaps acc's tail."""
    if not acc:
        return new[:]
    best = 0
    for k in range(min(len(acc), len(new), max_overlap), 0, -1):
        tail, head = acc[-k:], new[:k]
        hits = sum(_same(a, b) for a, b in zip(tail, head))
        if hits >= max(1, int(0.8 * k)):
            best = k
            break
    return acc + new[best:]


def lines_to_paragraphs(lines):
    """Rejoin wrapped lines into paragraphs (break on short sentence-ending lines)."""
    if not lines:
        return []
    short = 0.8 * float(np.median([len(ln) for ln in lines]))
    paras, cur = [], []
    for ln in lines:
        cur.append(ln)
        if ln.endswith((".", "!", "?", ":", '"', "”")) and len(ln) < short:
            paras.append(" ".join(cur)); cur = []
    if cur:
        paras.append(" ".join(cur))
    return paras


def frames_to_paragraphs(frames, log=print, progress=None):
    """OCR frames, merge their overlaps and reflow into paragraphs. progress(i, n) after each frame."""
    acc = []
    for i, f in enumerate(frames, 1):
        lines = ocr_lines(f)
        # frame edges usually slice a line in half; the overlap covers those lines anyway
        if i > 1:
            lines = lines[1:]
        if i < len(frames):
            lines = lines[:-1]
        acc = merge_overlap(acc, lines)
        log(f"OCR {i}/{len(frames)} -> {len(acc)} lines")
        if progress:
            progress(i, len(frames))
    return lines_to_paragraphs(acc)


def _screenshot_grid(frames, width, max_h=580, gap=12):
    """Screenshots two to a row (phone screens are tall and narrow), as JPEGs to keep the PDF small."""
    import io
    from reportlab.platypus import Image as RLImage, Table, TableStyle
    cw = (width - gap) / 2
    cells = []
    for f in frames:
        h, w = f.shape[:2]
        scale = min(cw / w, max_h / h)
        ok, jpg = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 82])
        cells.append(RLImage(io.BytesIO(jpg.tobytes()), w * scale, h * scale))
    if len(cells) % 2:
        cells.append("")
    rows = [cells[i:i + 2] for i in range(0, len(cells), 2)]
    t = Table(rows, colWidths=[cw + gap / 2] * 2)
    t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), gap)]))
    return t


def text_to_pdf(sections, out):
    """sections: list of (title or None, paragraphs[, frames]); each section after the first starts
    a new page. Frames, when given, follow the section's text as a "Screenshots" appendix."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from xml.sax.saxutils import escape

    st = getSampleStyleSheet()
    body = st["BodyText"]; body.fontSize = 12; body.leading = 17
    doc = SimpleDocTemplate(out, pagesize=letter, leftMargin=60, rightMargin=60)
    story = []
    for n, (title, paras, *rest) in enumerate(sections):
        if n:
            story.append(PageBreak())
        if title:
            story += [Paragraph(escape(title), st["Title"]), Spacer(1, 12)]
        for p in paras:
            story += [Paragraph(escape(p), body), Spacer(1, 8)]
        if rest and rest[0]:
            story += [PageBreak(), Paragraph("Screenshots", st["Heading2"]), Spacer(1, 6),
                      _screenshot_grid(rest[0], doc.width)]
    doc.build(story)


def paragraphs_to_text(title, paras):
    """Plain-text version of a section: optional title, then paragraphs separated by blank lines."""
    return "\n\n".join(([title] if title else []) + list(paras)) + "\n"


def find_tesseract():
    """Point pytesseract at Tesseract: the copy bundled in V2T.exe (or vendor/ from
    vendor_tesseract.py), else PATH or the usual Windows locations. Returns path or None."""
    import os, shutil, sys
    import pytesseract
    base = getattr(sys, "_MEIPASS", None)
    bundled = os.path.join(base, "tesseract") if base else \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "tesseract")
    if os.path.isfile(os.path.join(bundled, "tesseract.exe")):
        os.environ["TESSDATA_PREFIX"] = os.path.join(bundled, "tessdata")
        pytesseract.pytesseract.tesseract_cmd = os.path.join(bundled, "tesseract.exe")
        return pytesseract.pytesseract.tesseract_cmd
    candidates = [shutil.which("tesseract"),
                  os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
                  r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]
    for c in candidates:
        if c and os.path.isfile(c):
            pytesseract.pytesseract.tesseract_cmd = c
            return c
    return None


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("out")
    ap.add_argument("--mode", choices=["text", "images", "both"], default="text",
                    help="both = text followed by the screenshots in one PDF, plus a .txt next to it")
    ap.add_argument("--fps", type=float, default=30.0, help="frames/sec analysed for scroll tracking")
    ap.add_argument("--change", type=float, default=18.0, help="diff left after undoing the scroll that counts as a page change")
    ap.add_argument("--overlap", type=float, default=0.4, help="screen fraction shared by consecutive kept frames")
    ap.add_argument("--crop-top", type=float, default=0.07, help="fraction to trim (status bar)")
    ap.add_argument("--crop-bottom", type=float, default=0.09, help="fraction to trim (browser/home bar)")
    ap.add_argument("--title")
    a = ap.parse_args()

    frames = extract_keyframes(a.video, a.fps, a.change, a.overlap, a.crop_top, a.crop_bottom)
    print(f"{len(frames)} unique frames")
    if not frames:
        raise SystemExit("No frames extracted")

    if a.mode == "images":
        frames_to_image_pdf(frames, a.out)
    else:
        find_tesseract()
        paras = frames_to_paragraphs(frames)
        text_to_pdf([(a.title, paras, frames if a.mode == "both" else None)], a.out)
        if a.mode == "both":
            import os
            with open(os.path.splitext(a.out)[0] + ".txt", "w", encoding="utf-8") as fh:
                fh.write(paragraphs_to_text(a.title, paras))
    print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
