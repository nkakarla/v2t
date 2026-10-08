# EPerf V2T

Turns phone screen recordings of scrolling articles into PDFs: OCR'd, reflowed text, or the screenshots
themselves. Two front ends on the `video2pdf.py` engine:

- **Web converter** at https://v2t.eperflabs.com: upload a recording and get one PDF (the text, then the
  screenshots) plus a `.txt`. `webapp.py` (Flask) runs on eperf4; uploads are deleted once converted, and results after an hour.
- **Windows app** (Tkinter, EPerf UI), downloadable from the same page. It runs fully offline, and Tesseract OCR is bundled into the exe.

## Run from source
```
py -m pip install -r requirements.txt
py vendor_tesseract.py      # optional: copies a local Tesseract install into vendor/
V2T.bat                     # or: py v2t_app.py [videos...]
py video2pdf.py in.mov out.pdf --mode both   # command line: text + screenshots PDF, plus out.txt
```

## Build
`build_exe.bat` → `dist\V2T.exe`: one file, including Tesseract, the loading splash and drag-and-drop support.
The exe is unsigned, so users will see SmartScreen warnings. The download page explains how to get past them.

## Publish
Bump `VERSION`, build, commit, then from Git Bash run `./deploy_eperf4.sh`. It pushes to GitHub, pulls on eperf4,
updates and restarts the web converter (asks for the sudo password), and uploads the exe + `latest.json`
(version, size, SHA-256, shown on the page) if the exe changed. The exe is over GitHub's 100 MB limit, so it's
uploaded over SSH instead of committed.

### One-time server setup (eperf4)
```
sudo apt install -y tesseract-ocr
cd ~/Documents/projects/v2t && python3 -m venv .venv && .venv/bin/pip install -r requirements-server.txt
sudo cp deploy/v2t.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now v2t
sudo cp deploy/v2t.eperflabs.com.conf /etc/apache2/sites-available/
sudo a2enmod headers proxy proxy_http && sudo a2ensite v2t.eperflabs.com && sudo systemctl reload apache2
# after the v2t CNAME exists at SiteGround:
sudo certbot --apache -d v2t.eperflabs.com --redirect
# certbot copies the vhost into v2t.eperflabs.com-le-ssl.conf, including the Include of deploy/v2t-api.conf
```
