# EPerf V2T

Turns phone screen recordings of scrolling articles into PDFs: OCR'd, reflowed text, or the screenshots
themselves. Two front ends on the `video2pdf.py` engine:

- **Web converter** at https://v2t.eperflabs.com: upload a recording and get one PDF (the text, then the
  screenshots) plus a `.txt`. `webapp.py` (Flask) runs on eperf4; uploads are deleted once converted, and results after an hour.
- **Windows app** (Tkinter, EPerf UI), downloadable from the same page. It runs fully offline, and Tesseract OCR is bundled into the exe.


