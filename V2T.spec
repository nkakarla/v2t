# PyInstaller spec for V2T.exe - one self-contained file with bundled Tesseract and a
# loading splash (shown while the exe unpacks, closed by v2t_app once the window is up).
# Build with build_exe.bat (runs vendor_tesseract.py first).

a = Analysis(
    ["v2t_app.py"],
    datas=[("assets/eperf_icon.png", "assets"), ("vendor/tesseract", "tesseract")],
    excludes=["matplotlib", "sklearn", "skimage", "scipy", "pandas", "torch"],
)
pyz = PYZ(a.pure)

splash = Splash(
    "assets/splash.png",
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(32, 272),          # live "Unpacking ..." status line above the blue bar
    text_size=9,
    text_color="#6ea8fe",
    text_default="Starting…",
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    name="V2T",
    icon="assets/eperf_icon.ico",
    console=False,
    upx=True,
)
