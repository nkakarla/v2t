# EPerf V2T

Turns phone screen recordings of scrolling articles into PDFs: OCR'd, reflowed text, or the screenshots
themselves. Windows desktop app (Tkinter, EPerf UI) built on the `video2pdf.py` engine; Tesseract OCR is
bundled into the exe.

Download page: https://v2t.eperflabs.com

## Run from source
```
py -m pip install -r requirements.txt
py vendor_tesseract.py      # optional: copies a local Tesseract install into vendor/
V2T.bat                     # or: py v2t_app.py [videos...]
py video2pdf.py in.mov out.pdf --mode text   # command-line engine
```

## Build
`build_exe.bat` → `dist\V2T.exe`: one file, including Tesseract, the loading splash and drag-and-drop support.
The exe is unsigned, so users will see SmartScreen warnings. The download page explains how to get past them.

## Publish
Bump `VERSION`, build, commit, then from Git Bash run `./deploy_eperf4.sh`. It pushes to GitHub, pulls on eperf4,
and uploads the exe + `latest.json` (version, size, SHA-256, shown on the page). The exe is over GitHub's 100 MB
limit, so it's uploaded over SSH instead of committed.

### One-time server setup (eperf4)
```
sudo cp ~/Documents/projects/v2t/deploy/v2t.eperflabs.com.conf /etc/apache2/sites-available/
sudo a2enmod headers && sudo a2ensite v2t.eperflabs.com && sudo systemctl reload apache2
# after the v2t CNAME exists at SiteGround:
sudo certbot --apache -d v2t.eperflabs.com --redirect
```
