#!/usr/bin/env python3
"""
V2T - EPerf desktop app: turn phone screen recordings of scrolling text into PDFs.

Wraps the video2pdf.py engine in a Tkinter UI styled to the EPerf Labs look
(dark #111/#222 panels, #0d6efd brand blue, Segoe UI).

Run:   py v2t_app.py [videos...]
Build: build_exe.bat  ->  dist\\V2T.exe
"""
try:  # loading splash from the PyInstaller exe (absent when run from source)
    import pyi_splash
    pyi_splash.update_text("Loading video and OCR libraries…")
except ImportError:
    pyi_splash = None

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import video2pdf as v2p

try:  # drag-and-drop of files from Explorer
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND = True
except ImportError:
    DND = False

APP_NAME = "V2T"
VIDEO_EXTS = (".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm")

# ---------- EPerf palette ----------
BG = "#111111"
PANEL = "#222222"
CARD = "#1c1c1c"
INPUT = "#141414"
BORDER = "#333333"
BORDER_STRONG = "#444444"
FG = "#ffffff"
MUTED = "#9a9a9a"
BRAND = "#0d6efd"
BRAND_HI = "#2a7eff"
BRAND_DARK = "#003cba"
POS = "#3fb950"
NEG = "#e66767"
WARN = "#e3b341"

FONT = "Segoe UI"
F_BRAND = (FONT, 20, "bold")
F_SUB = (FONT, 10)
F_H2 = (FONT, 9, "bold")
F_BODY = (FONT, 10)
F_BTN = (FONT, 10, "bold")
F_MONO = ("Consolas", 10)


def resource(name):
    """Path to a bundled file, both from source and inside a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ---------- widgets ----------
def _round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class PillButton(tk.Canvas):
    """Rounded EPerf button. kind: 'primary' (brand blue) or 'secondary' (dark outline)."""

    STYLES = {
        "primary":   dict(fill=BRAND, hover=BRAND_HI, outline=BRAND, fg=FG),
        "secondary": dict(fill="#2a2a2a", hover="#353535", outline=BORDER_STRONG, fg=FG),
    }

    def __init__(self, parent, text, command, kind="secondary", padx=16, pady=7):
        self.bg_parent = parent["bg"]
        f = tkfont.Font(font=F_BTN)
        w, h = f.measure(text) + 2 * padx, f.metrics("linespace") + 2 * pady
        super().__init__(parent, width=w + 2, height=h + 2, bg=self.bg_parent,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command, self.s, self.enabled = command, self.STYLES[kind], True
        self.shape = _round_rect(self, 1, 1, w, h, 10, fill=self.s["fill"], outline=self.s["outline"])
        self.label = self.create_text(w / 2 + 1, h / 2 + 1, text=text, fill=self.s["fg"], font=F_BTN)
        self.bind("<Enter>", lambda e: self._paint(self.s["hover"]))
        self.bind("<Leave>", lambda e: self._paint(self.s["fill"]))
        self.bind("<ButtonRelease-1>", self._click)

    def _paint(self, fill):
        if self.enabled:
            self.itemconfig(self.shape, fill=fill)

    def _click(self, _):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, on):
        self.enabled = on
        self.itemconfig(self.shape, fill=self.s["fill"] if on else "#2a2a2a",
                        outline=self.s["outline"] if on else BORDER)
        self.itemconfig(self.label, fill=self.s["fg"] if on else "#666666")
        self.config(cursor="hand2" if on else "arrow")


class Segmented(tk.Frame):
    """Row of mutually exclusive options; the selected one is filled with brand blue."""

    def __init__(self, parent, variable, options):
        super().__init__(parent, bg=BORDER_STRONG, padx=1, pady=1)
        self.var, self.items = variable, {}
        for value, text in options:
            lbl = tk.Label(self, text=text, font=F_BODY, padx=14, pady=6, cursor="hand2")
            lbl.pack(side="left", padx=(0 if not self.items else 1, 0))
            lbl.bind("<Button-1>", lambda e, v=value: self._pick(v))
            self.items[value] = lbl
        self.disabled = set()
        variable.trace_add("write", lambda *_: self._refresh())
        self._refresh()

    def _pick(self, value):
        if value not in self.disabled:
            self.var.set(value)

    def disable(self, value):
        self.disabled.add(value)
        self._refresh()

    def _refresh(self):
        for value, lbl in self.items.items():
            if value in self.disabled:
                lbl.config(bg=INPUT, fg="#555555", cursor="arrow")
            elif value == self.var.get():
                lbl.config(bg=BRAND, fg=FG)
            else:
                lbl.config(bg=INPUT, fg=MUTED)


def card(parent, title=None, **pack):
    """Dark EPerf card with an uppercase muted section heading. Returns the inner frame."""
    outer = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_STRONG)
    outer.pack(**pack)
    inner = tk.Frame(outer, bg=PANEL, padx=16, pady=14)
    inner.pack(fill="both", expand=True)
    if title:
        tk.Label(inner, text=title.upper(), font=F_H2, fg=MUTED, bg=PANEL).pack(anchor="w", pady=(0, 10))
    return inner


def dark_entry(parent, var, width=None):
    kw = dict(width=width) if width else {}
    return tk.Entry(parent, textvariable=var, font=F_BODY, fg=FG, bg=INPUT, insertbackground=FG,
                    relief="flat", highlightthickness=1, highlightbackground=BORDER,
                    highlightcolor=BRAND, disabledbackground=INPUT, disabledforeground="#555555", **kw)


def dark_spin(parent, var, lo, hi, step):
    return tk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=step, width=5,
                      font=F_BODY, fg=FG, bg=INPUT, buttonbackground="#2a2a2a", insertbackground=FG,
                      relief="flat", highlightthickness=1, highlightbackground=BORDER,
                      highlightcolor=BRAND, justify="center")


# ---------- app ----------
class V2TApp(TkinterDnD.Tk if DND else tk.Tk):
    def __init__(self, initial=()):
        super().__init__()
        self.title("EPerf | V2T - Video to PDF")
        self.geometry("1100x800")
        self.minsize(980, 740)
        self.configure(bg=BG)
        self._set_icon()
        self._style_ttk()
        self.update_idletasks()
        self._dark_title_bar()

        self.mode = tk.StringVar(value="text")
        self.combine = tk.BooleanVar(value=False)
        self.doc_title = tk.StringVar(value="")
        self.crop_top = tk.StringVar(value="7")
        self.crop_bottom = tk.StringVar(value="9")
        self.overlap = tk.StringVar(value="40")
        self.out_dir = tk.StringVar(value="")
        self.msgs = queue.Queue()
        self.busy = False
        self.last_out = None

        self.tesseract = v2p.find_tesseract()
        self._build()
        self._enable_drop()
        self.add_paths(initial)
        self.after(100, self._drain)

    # ----- setup -----
    def _dark_title_bar(self):
        if sys.platform != "win32":
            return
        try:  # Windows 10 20H1+ / 11: DWMWA_USE_IMMERSIVE_DARK_MODE
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            on = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
        except Exception:
            pass

    def _set_icon(self):
        try:
            self._icon = tk.PhotoImage(file=resource("assets/eperf_icon.png"))
            self.iconphoto(True, self._icon)
        except tk.TclError:
            self._icon = None

    def _style_ttk(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("V2T.Treeview", background=INPUT, fieldbackground=INPUT, foreground=FG,
                     rowheight=30, borderwidth=0, font=F_BODY,
                     bordercolor=INPUT, lightcolor=INPUT, darkcolor=INPUT)
        st.layout("V2T.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("V2T.Treeview.Heading", background=CARD, foreground=MUTED, relief="flat",
                     font=F_H2, borderwidth=0, padding=(8, 6))
        st.map("V2T.Treeview", background=[("selected", BRAND)], foreground=[("selected", FG)])
        st.map("V2T.Treeview.Heading", background=[("active", CARD)])
        st.configure("V2T.Horizontal.TProgressbar", troughcolor=INPUT, background=BRAND,
                     bordercolor=BORDER, lightcolor=BRAND, darkcolor=BRAND_DARK, thickness=8)
        for orient in ("Vertical", "Horizontal"):
            st.configure(f"{orient}.TScrollbar", background="#2a2a2a", troughcolor=INPUT,
                         bordercolor=INPUT, arrowcolor=MUTED, lightcolor="#2a2a2a", darkcolor="#2a2a2a",
                         gripcount=0)
            st.map(f"{orient}.TScrollbar", background=[("active", "#3a3a3a")])

    def _build(self):
        wrap = tk.Frame(self, bg=BG, padx=18, pady=16)
        wrap.pack(fill="both", expand=True)
        self._build_header(wrap)
        self._build_footer(wrap)

        cols = tk.Frame(wrap, bg=BG)
        cols.pack(fill="both", expand=True)
        cols.grid_columnconfigure(0, weight=3, uniform="c")
        cols.grid_columnconfigure(1, weight=2, uniform="c")
        cols.grid_rowconfigure(0, weight=1)

        left = tk.Frame(cols, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = tk.Frame(cols, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        bottom = tk.Frame(left, bg=BG)
        bottom.pack(side="bottom", fill="x", pady=(16, 0))
        self._build_log(bottom)
        self._build_videos(left)
        self._build_options(right)

    def _build_header(self, parent):
        outer = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_STRONG)
        outer.pack(fill="x", pady=(0, 16))
        head = tk.Frame(outer, bg=PANEL, padx=18, pady=12)
        head.pack(fill="x")
        tk.Frame(outer, bg=BRAND, height=3).pack(fill="x")

        if self._icon:
            self._logo = self._icon.subsample(max(1, self._icon.width() // 44))
            tk.Label(head, image=self._logo, bg=PANEL).pack(side="left", padx=(0, 12))
        text = tk.Frame(head, bg=PANEL)
        text.pack(side="left")
        tk.Label(text, text="EPerf V2T", font=F_BRAND, fg=FG, bg=PANEL).pack(anchor="w")
        tk.Label(text, text="Screen recording  →  readable PDF", font=F_SUB, fg=MUTED, bg=PANEL).pack(anchor="w")

        badge = tk.Canvas(head, width=104, height=28, bg=PANEL, highlightthickness=0)
        _round_rect(badge, 1, 1, 103, 27, 13, fill=BRAND, outline=BRAND)
        badge.create_text(52, 14, text="EPerf Labs", fill=FG, font=(FONT, 9, "bold"))
        badge.pack(side="right")

        ok = bool(self.tesseract)
        color = POS if ok else WARN
        pill = tk.Label(head, text="●  OCR ready" if ok else "●  OCR unavailable", font=(FONT, 9, "bold"),
                        fg=color, bg=PANEL, padx=10, pady=3, highlightthickness=1, highlightbackground=color)
        pill.pack(side="right", padx=(0, 12))

    def _build_footer(self, parent):
        foot = tk.Frame(parent, bg=BG)
        foot.pack(side="bottom", fill="x", pady=(12, 0))
        tk.Frame(foot, bg=BORDER, height=1).pack(fill="x")
        tk.Label(foot, text=f"© {time.localtime().tm_year} EPerf Labs · {APP_NAME}", font=(FONT, 9),
                 fg="#888888", bg=BG).pack(pady=(8, 0))

    def _build_videos(self, parent):
        box = card(parent, "Videos", fill="both", expand=True)

        # buttons first, pinned to the bottom, so a tall list can never push them out of view
        row = tk.Frame(box, bg=PANEL)
        row.pack(side="bottom", fill="x", pady=(12, 0))
        self.btn_add = PillButton(row, "Add videos…", self.pick_files, kind="primary")
        self.btn_add.pack(side="left")
        self.btn_folder = PillButton(row, "Add folder…", self.pick_folder)
        self.btn_folder.pack(side="left", padx=(8, 0))
        self.btn_remove = PillButton(row, "Remove", self.remove_selected)
        self.btn_remove.pack(side="right")
        self.btn_clear = PillButton(row, "Clear", self.clear_videos)
        self.btn_clear.pack(side="right", padx=(0, 8))

        self.tv_wrap = tv_wrap = tk.Frame(box, bg=INPUT, highlightthickness=1, highlightbackground=BORDER)
        tv_wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tv_wrap, style="V2T.Treeview", columns=("status",), show="tree headings",
                                 selectmode="extended", height=5)
        self.tree.heading("#0", text="FILE", anchor="w")
        self.tree.heading("status", text="STATUS", anchor="w")
        self.tree.column("#0", stretch=True, minwidth=200)
        self.tree.column("status", width=150, stretch=False)
        for tag, color in (("queued", MUTED), ("work", WARN), ("done", POS), ("error", NEG)):
            self.tree.tag_configure(tag, foreground=color)
        sb = ttk.Scrollbar(tv_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.paths = {}

        hint = "Drop screen recordings here\nor click to choose files" if DND \
            else "Click here to choose screen recordings"
        self.empty_hint = tk.Label(self.tree, text=hint, font=(FONT, 11), fg="#777777", bg=INPUT,
                                   cursor="hand2", justify="center")
        self.empty_hint.bind("<Button-1>", lambda e: self.pick_files())
        # double-clicking empty space in the list also opens the picker
        self.tree.bind("<Double-1>", lambda e: self.pick_files() if not self.tree.identify_row(e.y) else None)
        self._update_hint()

    def _build_options(self, parent):
        box = card(parent, "Output", fill="both", expand=True)

        tk.Label(box, text="PDF type", font=F_BODY, fg=FG, bg=PANEL).pack(anchor="w")
        self.seg = Segmented(box, self.mode, [("text", "Text (OCR)"), ("images", "Screenshots"), ("both", "Both")])
        self.seg.pack(anchor="w", pady=(6, 4))
        tk.Label(box, text="Text reflows into a clean, searchable document.\nScreenshots keep the exact look.",
                 font=(FONT, 9), fg=MUTED, bg=PANEL, justify="left").pack(anchor="w", pady=(0, 12))
        if not self.tesseract:
            self.seg.disable("text"); self.seg.disable("both"); self.mode.set("images")

        tk.Checkbutton(box, text="Combine all videos into one PDF", variable=self.combine,
                       command=self._toggle_combine, font=F_BODY, fg=FG, bg=PANEL, activebackground=PANEL,
                       activeforeground=FG, selectcolor=INPUT, highlightthickness=0, bd=0,
                       cursor="hand2").pack(anchor="w")
        trow = tk.Frame(box, bg=PANEL)
        trow.pack(fill="x", pady=(6, 12))
        tk.Label(trow, text="Title", font=F_BODY, fg=MUTED, bg=PANEL, width=6, anchor="w").pack(side="left")
        self.title_entry = dark_entry(trow, self.doc_title)
        self.title_entry.pack(side="left", fill="x", expand=True, ipady=5, ipadx=6)
        self._toggle_combine()

        crow = tk.Frame(box, bg=PANEL)
        crow.pack(fill="x", pady=(0, 4))
        for label, var, hi in (("Trim top %", self.crop_top, 40), ("Trim bottom %", self.crop_bottom, 40),
                               ("Overlap %", self.overlap, 80)):
            cell = tk.Frame(crow, bg=PANEL)
            cell.pack(side="left", padx=(0, 16))
            tk.Label(cell, text=label, font=(FONT, 9), fg=MUTED, bg=PANEL).pack(anchor="w")
            dark_spin(cell, var, 0, hi, 1).pack(anchor="w", pady=(3, 0), ipady=3)
        tk.Label(box, text="Trim removes the phone status bar and browser toolbar.",
                 font=(FONT, 9), fg=MUTED, bg=PANEL).pack(anchor="w", pady=(2, 12))

        tk.Label(box, text="Save to", font=F_BODY, fg=FG, bg=PANEL).pack(anchor="w")
        orow = tk.Frame(box, bg=PANEL)
        orow.pack(fill="x", pady=(6, 0))
        self.out_entry = dark_entry(orow, self.out_dir)
        self.out_entry.pack(side="left", fill="x", expand=True, ipady=5, ipadx=6)
        PillButton(orow, "Browse…", self.pick_out_dir, padx=12, pady=5).pack(side="left", padx=(8, 0))
        tk.Label(box, text="Leave empty to use an “out” folder next to the videos.",
                 font=(FONT, 9), fg=MUTED, bg=PANEL).pack(anchor="w", pady=(4, 0))

    def _build_log(self, parent):
        box = card(parent, "Progress", fill="both", expand=True)
        prow = tk.Frame(box, bg=PANEL)
        prow.pack(fill="x", pady=(0, 12))
        self.btn_run = PillButton(prow, "Convert to PDF", self.start, kind="primary", padx=22, pady=9)
        self.btn_run.pack(side="left")
        self.btn_open = PillButton(prow, "Open output", self.open_output, padx=14, pady=9)
        self.btn_open.pack(side="left", padx=(8, 0))
        self.btn_open.set_enabled(False)
        self.pct = tk.Label(prow, text="", font=F_BODY, fg=MUTED, bg=PANEL, width=5, anchor="e")
        self.pct.pack(side="right", padx=(10, 0))
        self.progress = ttk.Progressbar(prow, style="V2T.Horizontal.TProgressbar", maximum=1.0)
        self.progress.pack(side="right", fill="x", expand=True, padx=(18, 0))

        lwrap = tk.Frame(box, bg=INPUT, highlightthickness=1, highlightbackground=BORDER)
        lwrap.pack(fill="both", expand=True)
        self.log = tk.Text(lwrap, wrap="word", font=F_MONO, fg="#d0d0d0", bg=INPUT, relief="flat",
                           padx=10, pady=8, height=7, insertbackground=FG, highlightthickness=0,
                           state="disabled")
        sb = ttk.Scrollbar(lwrap, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for tag, color in (("muted", MUTED), ("pos", POS), ("neg", NEG), ("warn", WARN), ("brand", BRAND_HI)):
            self.log.tag_configure(tag, foreground=color)

        if self.tesseract:
            where = "built-in" if getattr(sys, "_MEIPASS", None) and self.tesseract.startswith(sys._MEIPASS) \
                else self.tesseract
            self._log(f"OCR engine: Tesseract ({where})", "muted")
        else:
            self._log("Tesseract OCR not found - only Screenshots mode is available. "
                      "Install it from https://github.com/UB-Mannheim/tesseract/wiki", "warn")
        self._log("Ready. Add videos and click Convert to PDF.", "muted")

    # ----- video list -----
    def add_paths(self, paths):
        for p in paths:
            p = os.path.abspath(p)
            if os.path.isdir(p):
                self.add_paths(sorted(str(f) for f in Path(p).iterdir() if f.suffix.lower() in VIDEO_EXTS))
            elif p.lower().endswith(VIDEO_EXTS) and p not in self.paths.values():
                iid = self.tree.insert("", "end", text="  " + os.path.basename(p),
                                       values=("Queued",), tags=("queued",))
                self.paths[iid] = p
        self._update_hint()

    def _enable_drop(self):
        """Accept files/folders dropped anywhere on the window; the list glows blue while dragging."""
        if not DND:
            return
        for w in (self, self.tv_wrap, self.tree, self.empty_hint):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<DropEnter>>", lambda e: self._drop_glow(True) or e.action)
            w.dnd_bind("<<DropLeave>>", lambda e: self._drop_glow(False) or e.action)
            w.dnd_bind("<<Drop>>", self._on_drop)

    def _drop_glow(self, on):
        self.tv_wrap.config(highlightbackground=BRAND if on else BORDER, highlightthickness=2 if on else 1)

    def _on_drop(self, event):
        self._drop_glow(False)
        if self.busy:
            return event.action
        dropped = self.tk.splitlist(event.data)
        before = len(self.paths)
        self.add_paths(dropped)
        added = len(self.paths) - before
        skipped = [os.path.basename(p) for p in dropped
                   if not os.path.isdir(p) and not p.lower().endswith(VIDEO_EXTS)]
        if added:
            self._log(f"Added {added} video(s).", "muted")
        if skipped:
            self._log(f"Skipped (not a video): {', '.join(skipped)}", "warn")
        return event.action

    def pick_files(self):
        files = filedialog.askopenfilenames(
            title="Choose screen recordings",
            filetypes=[("Videos", " ".join("*" + e for e in VIDEO_EXTS)), ("All files", "*.*")])
        self.add_paths(files)

    def pick_folder(self):
        d = filedialog.askdirectory(title="Choose a folder of screen recordings")
        if d:
            self.add_paths([d])

    def pick_out_dir(self):
        d = filedialog.askdirectory(title="Save PDFs to")
        if d:
            self.out_dir.set(d)

    def remove_selected(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.paths.pop(iid, None)
        self._update_hint()

    def clear_videos(self):
        self.tree.delete(*self.tree.get_children())
        self.paths.clear()
        self._update_hint()

    def _update_hint(self):
        if self.paths:
            self.empty_hint.place_forget()
        else:
            self.empty_hint.place(relx=0.5, rely=0.5, anchor="center")

    def _toggle_combine(self):
        self.title_entry.config(state="normal" if self.combine.get() else "disabled")

    # ----- run -----
    def _read_opts(self):
        try:
            top, bottom, ov = (float(v.get()) / 100 for v in (self.crop_top, self.crop_bottom, self.overlap))
        except ValueError:
            raise ValueError("Trim and overlap must be numbers.")
        if not (0 <= top < 0.5 and 0 <= bottom < 0.5 and 0 <= ov < 0.9):
            raise ValueError("Trim must be under 50% and overlap under 90%.")
        first = next(iter(self.paths.values()))
        out = self.out_dir.get().strip() or os.path.join(os.path.dirname(first), "out")
        return dict(mode=self.mode.get(), combine=self.combine.get(), title=self.doc_title.get().strip(),
                    crop_top=top, crop_bottom=bottom, overlap=ov, out=out)

    def start(self):
        if self.busy:
            return
        if not self.paths:
            messagebox.showinfo(APP_NAME, "Add at least one video first.")
            return
        try:
            opts = self._read_opts()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        jobs = [(iid, self.paths[iid]) for iid in self.tree.get_children()]
        for iid, _ in jobs:
            self.tree.item(iid, values=("Queued",), tags=("queued",))
        self._set_busy(True)
        self.progress["value"] = 0
        self._log("")
        threading.Thread(target=self._worker, args=(jobs, opts), daemon=True).start()

    def _set_busy(self, on):
        self.busy = on
        for b in (self.btn_run, self.btn_add, self.btn_folder, self.btn_remove, self.btn_clear):
            b.set_enabled(not on)
        if not on:
            self.btn_open.set_enabled(bool(self.last_out))

    def _worker(self, jobs, o):
        post = self.msgs.put
        t0 = time.time()
        os.makedirs(o["out"], exist_ok=True)
        want_text, want_img = o["mode"] in ("text", "both"), o["mode"] in ("images", "both")
        sections, all_frames, written, failed = [], [], [], 0
        n = len(jobs)

        for k, (iid, path) in enumerate(jobs):
            name, stem = os.path.basename(path), Path(path).stem
            post(("status", iid, "Finding frames…", "work"))
            post(("log", f"▶ {name}", "brand"))
            try:
                frames = v2p.extract_keyframes(path, overlap=o["overlap"],
                                               crop_top=o["crop_top"], crop_bottom=o["crop_bottom"])
                if not frames:
                    raise RuntimeError("no frames could be read")
                post(("log", f"   {len(frames)} frames kept", "muted"))
                post(("progress", (k + 0.3) / n))

                if want_img:
                    if o["combine"]:
                        all_frames += frames
                    else:
                        dst = os.path.join(o["out"], f"{stem}-images.pdf")
                        v2p.frames_to_image_pdf(frames, dst)
                        written.append(dst)
                if want_text:
                    post(("status", iid, "Reading text…", "work"))

                    paras = v2p.frames_to_paragraphs(
                        frames, log=lambda _m: None,
                        progress=lambda i, m, _k=k: post(("progress", (_k + 0.3 + 0.7 * i / m) / n)))
                    post(("log", f"   {len(paras)} paragraphs, {sum(len(p.split()) for p in paras)} words", "muted"))
                    if o["combine"]:
                        sections.append((None, paras))
                    else:
                        dst = os.path.join(o["out"], f"{stem}-text.pdf")
                        v2p.text_to_pdf([(None, paras)], dst)
                        written.append(dst)
                post(("status", iid, "Done", "done"))
            except Exception as e:
                failed += 1
                post(("status", iid, "Error", "error"))
                post(("log", f"   ✖ {e}", "neg"))
            post(("progress", (k + 1) / n))

        base = os.path.join(o["out"], o["title"] or "V2T combined")
        try:
            if all_frames:
                v2p.frames_to_image_pdf(all_frames, base + "-images.pdf")
                written.append(base + "-images.pdf")
            if sections:
                if o["title"]:
                    sections[0] = (o["title"], sections[0][1])
                v2p.text_to_pdf(sections, base + "-text.pdf")
                written.append(base + "-text.pdf")
        except Exception as e:
            failed += 1
            post(("log", f"✖ Could not write combined PDF: {e}", "neg"))

        if written:
            post(("log", f"Saved to {o['out']}", "muted"))
        for w in written:
            post(("log", f"✔ {os.path.basename(w)}", "pos"))
        summary = f"Finished in {time.time() - t0:.0f}s - {len(written)} PDF(s) written"
        post(("log", summary + (f", {failed} error(s)" if failed else ""), "neg" if failed else "pos"))
        post(("done", o["out"] if written else None))

    def _drain(self):
        try:
            while True:
                msg = self.msgs.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log(msg[1], msg[2])
                elif kind == "status":
                    _, iid, text, tag = msg
                    if self.tree.exists(iid):
                        self.tree.item(iid, values=(text,), tags=(tag,))
                elif kind == "progress":
                    self.progress["value"] = msg[1]
                    self.pct.config(text=f"{msg[1] * 100:.0f}%")
                elif kind == "done":
                    self.last_out = msg[1]
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _log(self, text, tag=None):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n", (tag,) if tag else ())
        self.log.see("end")
        self.log.config(state="disabled")

    def open_output(self):
        if self.last_out and os.path.isdir(self.last_out):
            if sys.platform == "win32":
                os.startfile(self.last_out)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", self.last_out])


def main():
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    if pyi_splash:
        pyi_splash.update_text("Opening window…")
    app = V2TApp(sys.argv[1:])
    if pyi_splash:
        app.after(300, pyi_splash.close)  # once the main window has drawn
    app.mainloop()


if __name__ == "__main__":
    main()
