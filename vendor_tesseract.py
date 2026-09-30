#!/usr/bin/env python3
"""
Copy the parts of a local Tesseract install that V2T needs into vendor/tesseract,
so build_exe.bat can bundle OCR into V2T.exe (no separate install on the target PC).

Keeps tesseract.exe, only the DLLs it actually loads (walked from its PE imports),
and English language data + the configs pytesseract uses. Training tools are skipped.
"""
import os
import shutil
import sys

import pefile

SRC_CANDIDATES = [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR"),
                  r"C:\Program Files\Tesseract-OCR",
                  r"C:\Program Files (x86)\Tesseract-OCR"]
DST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "tesseract")
TESSDATA = ["eng.traineddata", "osd.traineddata", "configs"]


def local_deps(src, exe):
    """DLLs imported (recursively) by exe that ship in the Tesseract folder; system DLLs are left out."""
    shipped = {f.lower(): f for f in os.listdir(src) if f.lower().endswith(".dll")}
    need, todo = set(), [exe]
    while todo:
        pe = pefile.PE(os.path.join(src, todo.pop()), fast_load=True)
        pe.parse_data_directories([pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        for imp in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
            name = imp.dll.decode().lower()
            if name in shipped and name not in need:
                need.add(name)
                todo.append(shipped[name])
        pe.close()
    return sorted(shipped[n] for n in need)


def main():
    src = next((s for s in SRC_CANDIDATES if os.path.isfile(os.path.join(s, "tesseract.exe"))), None)
    if not src:
        sys.exit("Tesseract not found. Install it from https://github.com/UB-Mannheim/tesseract/wiki")
    shutil.rmtree(DST, ignore_errors=True)
    os.makedirs(os.path.join(DST, "tessdata"))

    files = ["tesseract.exe"] + local_deps(src, "tesseract.exe")
    for f in files:
        shutil.copy2(os.path.join(src, f), DST)
    for t in TESSDATA:
        s, d = os.path.join(src, "tessdata", t), os.path.join(DST, "tessdata", t)
        (shutil.copytree if os.path.isdir(s) else shutil.copy2)(s, d)

    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(DST) for f in fs)
    print(f"Vendored Tesseract from {src}: {len(files)} files, {size / 1e6:.0f} MB -> {DST}")


if __name__ == "__main__":
    main()
