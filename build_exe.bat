@echo off
REM Build dist\V2T.exe - one self-contained file, Tesseract OCR and loading splash included.
cd /d "%~dp0"
py -m pip install -r requirements.txt pyinstaller pefile
py vendor_tesseract.py || goto :fail
py -m PyInstaller --noconfirm --clean --workpath "%TEMP%\v2t_build" --distpath "%~dp0dist" V2T.spec || goto :fail
echo.
echo Built dist\V2T.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
