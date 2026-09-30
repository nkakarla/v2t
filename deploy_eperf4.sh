#!/usr/bin/env bash
# Publish V2T: push source to GitHub, pull it on eperf4, upload dist/V2T.exe + latest.json.
# Run from Git Bash after build_exe.bat. First-time server setup: see deploy/README-deploy.md.
set -euo pipefail
cd "$(dirname "$0")"

HOST=nash-mudi@eperf4
APP_DIR=Documents/projects/v2t
EXE=dist/V2T.exe
VERSION=$(tr -d '[:space:]' < VERSION)

[ -f "$EXE" ] || { echo "No $EXE - run build_exe.bat first"; exit 1; }

git push origin main
ssh "$HOST" "if [ -d $APP_DIR/.git ]; then git -C $APP_DIR pull --ff-only; else git clone git@github.com:nkakarla/v2t.git $APP_DIR; fi"

SHA=$(sha256sum "$EXE" | cut -d' ' -f1)
SIZE=$(stat -c %s "$EXE")
printf '{"version":"%s","size":%s,"sha256":"%s","date":"%s"}\n' "$VERSION" "$SIZE" "$SHA" "$(date +%Y-%m-%d)" > dist/latest.json

# upload under a temp name, then swap, so a half-uploaded exe is never served
scp "$EXE" "$HOST:$APP_DIR/web/downloads/V2T.exe.part"
scp dist/latest.json "$HOST:$APP_DIR/web/downloads/latest.json"
ssh "$HOST" "cd $APP_DIR/web/downloads && echo '$SHA  V2T.exe.part' | sha256sum -c --quiet && mv -f V2T.exe.part V2T.exe"
echo "Published V2T $VERSION ($SHA)"
