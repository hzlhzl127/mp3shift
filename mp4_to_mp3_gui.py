#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MP4 → MP3 桌面转换器（tkinter GUI）

- 拖放视频/文件夹到窗口，或点「添加文件」选择
- 批量队列，逐文件百分比进度
- 可选音质（VBR / 128k / 192k / 320k）、音轨、输出目录、覆盖
- 转换在后台线程里跑，界面不卡；取消会在当前文件结束后生效

运行：python mp4_to_mp3_gui.py（或双击 桌面版.bat）
依赖：mp4_to_mp3.py、同目录 ffmpeg\bin\ffmpeg.exe、tkinterdnd2（可选，拖放用）
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import mp4_to_mp3 as core

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAS_DND = True
except Exception:  # 没装 tkinterdnd2 时退化为「添加文件」按钮
    HAS_DND = False
    DND_FILES = None

# ---------------------------------------------------------------- 外观常量

FONT = "Microsoft YaHei UI"
BG = "#f4f5f7"
CARD = "#ffffff"
FG = "#1f2329"
MUTED = "#6b7280"
ACCENT = "#2563eb"
DROP_BG = "#eef2ff"
DROP_BORDER = "#c7d2fe"
DROP_BORDER_HOVER = "#6366f1"
OK_GREEN = "#15803d"
ERR_RED = "#b91c1c"

QUALITY_CHOICES = [
    ("VBR 标准 q=2", None, 2),
    ("VBR 高 q=0", None, 0),
    ("128 kbps", "128k", 2),
    ("192 kbps", "192k", 2),
    ("320 kbps", "320k", 2),
]


def create_root() -> tk.Tk:
    if HAS_DND:
        try:
            return TkinterDnD.Tk()
        except Exception:
            pass
    return tk.Tk()


class ConverterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.files: list[Path] = []
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()
        self.running = False
        self.failures: list[str] = []
        self.last_dir: Path | None = None

        self.ffmpeg: Path | None = None
        try:
            self.ffmpeg = core.find_ffmpeg()
        except SystemExit:
            pass

        self._init_style()
        self._build()
        self._poll()

    # ------------------------------------------------------------ 样式

    def _init_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        style.configure(".", font=(FONT, 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", foreground=MUTED, font=(FONT, 9))
        style.configure("Title.TLabel", font=(FONT, 16, "bold"), foreground=FG)
        style.configure("TLabelframe", background=BG, bordercolor="#e5e7eb")
        style.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                        font=(FONT, 9, "bold"))
        style.configure("Treeview", background=CARD, fieldbackground=CARD,
                        rowheight=27, borderwidth=0)
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"),
                        background="#f0f1f3", relief="flat")
        style.map("Treeview", background=[("selected", "#dbeafe")],
                  foreground=[("selected", FG)])
        style.configure("TEntry", fieldbackground=CARD)
        style.configure("Horizontal.TProgressbar", troughcolor="#e5e7eb",
                        background=ACCENT, bordercolor="#e5e7eb",
                        lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure("TCheckbutton", background=BG)
        style.configure("Empty.TLabel", background=CARD, foreground="#9ca3af",
                        font=(FONT, 10), justify="center")

    # ------------------------------------------------------------ 布局

    def _build(self) -> None:
        root = self.root
        root.title("MP4 → MP3 转换器")
        root.geometry("900x640")
        root.minsize(760, 540)
        root.configure(bg=BG)

        main = ttk.Frame(root, padding=(16, 14, 16, 14))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)

        # 标题行
        head = ttk.Frame(main)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="MP4 → MP3", style="Title.TLabel").grid(
            row=0, column=0, sticky="w")
        self.lbl_count = ttk.Label(head, text="尚未添加文件", style="Muted.TLabel")
        self.lbl_count.grid(row=0, column=1, sticky="e")

        # 拖放区
        self.drop = tk.Frame(main, bg=DROP_BG, highlightthickness=2,
                             highlightbackground=DROP_BORDER, cursor="hand2")
        self.drop.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.drop.columnconfigure(0, weight=1)
        self.lbl_drop = tk.Label(
            self.drop, text="把视频拖到这里", bg=DROP_BG, fg="#3730a3",
            font=(FONT, 13, "bold"))
        self.lbl_drop.grid(row=0, column=0, pady=(16, 0))
        hint = "或点击此处选择文件 · 支持批量、文件夹" if HAS_DND else \
               "点击此处选择文件 · 支持批量、文件夹（拖放需安装 tkinterdnd2）"
        self.lbl_drop_hint = tk.Label(
            self.drop, text=hint, bg=DROP_BG, fg="#6366f1", font=(FONT, 9))
        self.lbl_drop_hint.grid(row=1, column=0, pady=(2, 16))

        for w in (self.drop, self.lbl_drop, self.lbl_drop_hint):
            w.bind("<Button-1>", lambda _e: self.add_files())
            w.bind("<Enter>", lambda _e: self.drop.configure(
                highlightbackground=DROP_BORDER_HOVER))
            w.bind("<Leave>", lambda _e: self.drop.configure(
                highlightbackground=DROP_BORDER))

        # 工具栏
        bar = ttk.Frame(main)
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.btn_add = ttk.Button(bar, text="添加文件…", command=self.add_files)
        self.btn_add.pack(side="left")
        self.btn_add_dir = ttk.Button(bar, text="添加文件夹…",
                                      command=self.add_folder)
        self.btn_add_dir.pack(side="left", padx=(6, 0))
        self.btn_remove = ttk.Button(bar, text="移除所选", command=self.remove_selected)
        self.btn_remove.pack(side="left", padx=(6, 0))
        self.btn_clear = ttk.Button(bar, text="清空", command=self.clear_files)
        self.btn_clear.pack(side="left", padx=(6, 0))

        # 文件列表
        tree_wrap = ttk.Frame(main)
        tree_wrap.grid(row=3, column=0, sticky="nsew")
        tree_wrap.columnconfigure(0, weight=1)
        tree_wrap.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tree_wrap, columns=("status", "progress"),
            show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="文件", anchor="w")
        self.tree.heading("status", text="状态", anchor="center")
        self.tree.heading("progress", text="进度", anchor="center")
        self.tree.column("#0", width=480, anchor="w")
        self.tree.column("status", width=96, anchor="center", stretch=False)
        self.tree.column("progress", width=90, anchor="center", stretch=False)
        self.tree.tag_configure("ok", foreground=OK_GREEN)
        self.tree.tag_configure("fail", foreground=ERR_RED)
        self.tree.tag_configure("skip", foreground=MUTED)
        self.tree.tag_configure("run", foreground=ACCENT)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())

        self.lbl_empty = ttk.Label(
            tree_wrap,
            text="还没有文件\n\n把视频拖进来，或点上方「添加文件」",
            style="Empty.TLabel")
        self.lbl_empty.place(relx=0.5, rely=0.45, anchor="center")

        # 设置区
        box = ttk.LabelFrame(main, text=" 转换设置 ", padding=(12, 10, 12, 10))
        box.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        box.columnconfigure(0, weight=1)

        opt1 = ttk.Frame(box)
        opt1.grid(row=0, column=0, sticky="ew")
        ttk.Label(opt1, text="音质").pack(side="left")
        self.cmb_quality = ttk.Combobox(
            opt1, values=[q[0] for q in QUALITY_CHOICES], state="readonly",
            width=16)
        self.cmb_quality.current(0)
        self.cmb_quality.pack(side="left", padx=(8, 26))
        ttk.Label(opt1, text="音轨").pack(side="left")
        self.spin_track = ttk.Spinbox(opt1, from_=0, to=9, width=4,
                                      justify="center")
        self.spin_track.set("0")
        self.spin_track.pack(side="left", padx=(8, 0))
        ttk.Label(opt1, text="（0 = 第一条音轨）",
                  style="Muted.TLabel").pack(side="left", padx=(10, 0))

        opt2 = ttk.Frame(box)
        opt2.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        opt2.columnconfigure(1, weight=1)
        ttk.Label(opt2, text="输出目录").grid(row=0, column=0, sticky="w")
        self.ent_out = ttk.Entry(opt2)
        self.ent_out.grid(row=0, column=1, sticky="ew", padx=(8, 6))
        self.btn_browse = ttk.Button(opt2, text="浏览…", command=self.choose_out_dir)
        self.btn_browse.grid(row=0, column=2, sticky="w")
        ttk.Label(opt2, text="留空 = 原目录",
                  style="Muted.TLabel").grid(row=0, column=3, sticky="w",
                                             padx=(10, 0))

        self.var_overwrite = tk.BooleanVar(value=False)
        self.chk_overwrite = ttk.Checkbutton(
            box, text="覆盖已存在的同名 MP3", variable=self.var_overwrite)
        self.chk_overwrite.grid(row=2, column=0, sticky="w", pady=(8, 0))

        # 底部
        foot = ttk.Frame(main)
        foot.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        foot.columnconfigure(0, weight=1)

        self.pb = ttk.Progressbar(foot, maximum=100, mode="determinate")
        self.pb.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        self.lbl_status = ttk.Label(foot, text=self._ready_text(), style="Muted.TLabel")
        self.lbl_status.grid(row=1, column=0, sticky="w")

        self.btn_open = ttk.Button(foot, text="打开输出文件夹",
                                   command=self.open_output, state="disabled")
        self.btn_open.grid(row=1, column=1, padx=(0, 6))

        self.btn_cancel = ttk.Button(foot, text="取消", command=self.cancel_run,
                                     state="disabled")
        self.btn_cancel.grid(row=1, column=2, padx=(0, 6))

        self.btn_start = tk.Button(
            foot, text="开始转换", command=self.start,
            bg=ACCENT, fg="white", activebackground="#1d4ed8",
            activeforeground="white", relief="flat", bd=0,
            highlightthickness=0, padx=20, pady=5, cursor="hand2",
            font=(FONT, 10, "bold"), disabledforeground="#dbeafe")
        self.btn_start.grid(row=1, column=3)

        if HAS_DND:
            for w in (self.drop, self.tree, self.lbl_empty):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
            self.drop.dnd_bind("<<DropEnter>>", lambda _e: self._drop_hover(True))
            self.drop.dnd_bind("<<DropLeave>>", lambda _e: self._drop_hover(False))

        self._refresh_count()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _drop_hover(self, on: bool) -> None:
        self.drop.configure(
            highlightbackground=DROP_BORDER_HOVER if on else DROP_BORDER)

    def _ready_text(self) -> str:
        if self.ffmpeg is None:
            return "✗ 未找到 ffmpeg，无法转换"
        return "就绪"

    # ------------------------------------------------------------ 文件管理

    def _on_drop(self, event) -> None:
        if self.running:
            self._status("转换中，暂时不能添加文件")
            return
        paths = self.root.tk.splitlist(event.data)
        self.add_paths(paths)

    def add_files(self) -> None:
        if self.running:
            return
        paths = filedialog.askopenfilenames(
            title="选择视频文件",
            filetypes=[
                ("视频文件", " ".join("*" + e for e in sorted(core.VIDEO_EXTS))),
                ("所有文件", "*.*"),
            ])
        if paths:
            self.add_paths(list(paths))

    def add_folder(self) -> None:
        if self.running:
            return
        folder = filedialog.askdirectory(title="选择包含视频的文件夹")
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths: list[str]) -> None:
        if self.running:
            return
        found: list[Path] = []
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                found.extend(sorted(
                    f for f in p.rglob("*")
                    if f.is_file() and f.suffix.lower() in core.VIDEO_EXTS))
            elif p.is_file():
                if p.suffix.lower() in core.VIDEO_EXTS:
                    found.append(p)

        known = {str(f) for f in self.files}
        added = 0
        for f in found:
            key = str(f.resolve())
            if key in known:
                continue
            known.add(key)
            self.files.append(f)
            self.tree.insert("", "end", iid=key, text=f"  {f.name}",
                             values=("等待中", "—"))
            added += 1

        if added:
            self._refresh_count()
            self._status(f"已添加 {added} 个文件")
        elif found:
            self._status("文件已在列表中")
        else:
            self._status("没有找到可转换的视频文件")

    def remove_selected(self) -> None:
        if self.running:
            return
        selected = set(self.tree.selection())
        if not selected:
            return
        for iid in selected:
            self.tree.delete(iid)
        self.files = [f for f in self.files if str(f.resolve()) not in selected]
        self._refresh_count()

    def clear_files(self) -> None:
        if self.running:
            return
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.files.clear()
        self._refresh_count()
        self._status("列表已清空")

    def _refresh_count(self) -> None:
        n = len(self.files)
        self.lbl_count.config(text=f"共 {n} 个文件" if n else "尚未添加文件")
        if n:
            self.lbl_empty.place_forget()
        else:
            self.lbl_empty.place(relx=0.5, rely=0.45, anchor="center")

    # ------------------------------------------------------------ 转换流程

    def start(self) -> None:
        if self.running:
            return
        if self.ffmpeg is None:
            messagebox.showerror("缺少 ffmpeg",
                                 "没有找到 ffmpeg.exe，请确认 ffmpeg 文件夹与脚本在同一目录。")
            return
        if not self.files:
            messagebox.showinfo("还没有文件", "请先拖入或添加要转换的视频。")
            return

        label, bitrate, quality = QUALITY_CHOICES[self.cmb_quality.current()]
        try:
            track = int(self.spin_track.get())
        except ValueError:
            messagebox.showerror("参数错误", "音轨序号需要是整数。")
            return

        out_dir = self.ent_out.get().strip()
        out_path = Path(out_dir).expanduser() if out_dir else None
        if out_path:
            try:
                out_path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror("输出目录不可用", str(exc))
                return

        settings = {
            "bitrate": bitrate,
            "quality": quality,
            "track": track,
            "out_dir": out_path,
            "overwrite": self.var_overwrite.get(),
        }

        self.failures.clear()
        self.running = True
        self.cancel.clear()
        self.pb["value"] = 0
        for iid in self.tree.get_children():
            self.tree.item(iid, tags=())
            self.tree.set(iid, "status", "等待中")
            self.tree.set(iid, "progress", "—")

        self._set_busy(True)
        self._status(f"开始转换，共 {len(self.files)} 个文件…")
        self.worker = threading.Thread(target=self._work,
                                       args=(list(self.files), settings),
                                       daemon=True)
        self.worker.start()

    def _work(self, files: list[Path], s: dict) -> None:
        total = len(files)
        ok = skipped = failed = 0
        last_dir: Path | None = None
        try:
            for i, src in enumerate(files, 1):
                if self.cancel.is_set():
                    break
                self.queue.put(("begin", i, total, src))
                duration, tracks = core.probe_media(self.ffmpeg, src)
                if not tracks:
                    self.queue.put(("skip", i, src, "没有音轨"))
                    skipped += 1
                    continue
                if s["track"] >= len(tracks):
                    self.queue.put(("skip", i, src, f"只有 {len(tracks)} 条音轨"))
                    skipped += 1
                    continue

                dst = (s["out_dir"] or src.parent) / (src.stem + ".mp3")
                if dst.exists() and not s["overwrite"]:
                    self.queue.put(("skip", i, src, "同名 MP3 已存在"))
                    skipped += 1
                    continue

                def on_progress(pct, cur, tot, _i=i, _src=src):
                    self.queue.put(("progress", _i, total, _src, pct))

                good, err = core.convert_file(
                    self.ffmpeg, src, dst, duration=duration,
                    track=s["track"], bitrate=s["bitrate"],
                    quality=s["quality"], overwrite=s["overwrite"],
                    on_progress=on_progress)
                if good:
                    ok += 1
                    last_dir = dst.parent
                    self.queue.put(("ok", i, src, dst))
                else:
                    failed += 1
                    self.queue.put(("fail", i, src, err))
        except Exception as exc:  # 兜底，避免线程静默死掉
            self.queue.put(("fatal", f"{type(exc).__name__}: {exc}"))
        finally:
            self.queue.put(("done", ok, skipped, failed,
                            self.cancel.is_set(), last_dir))

    def _poll(self) -> None:
        try:
            while True:
                msg = self.queue.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _handle(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "begin":
            _, i, total, src = msg
            iid = str(src.resolve())
            if self.tree.exists(iid):
                self.tree.see(iid)
                self.tree.selection_set(iid)
                self.tree.item(iid, tags=("run",))
                self.tree.set(iid, "status", "转换中")
                self.tree.set(iid, "progress", "0%")
            self.pb["value"] = (i - 1) / total * 100
            self._status(f"正在转换 {i}/{total}：{src.name}")

        elif kind == "progress":
            _, i, total, src, pct = msg
            iid = str(src.resolve())
            if self.tree.exists(iid) and pct is not None:
                self.tree.set(iid, "progress", f"{pct * 100:.0f}%")
                self.pb["value"] = ((i - 1) + pct) / total * 100
                self._status(f"正在转换 {i}/{total}：{src.name} {pct * 100:.0f}%")

        elif kind == "ok":
            _, i, src, dst = msg
            iid = str(src.resolve())
            if self.tree.exists(iid):
                self.tree.item(iid, tags=("ok",))
                self.tree.set(iid, "status", "完成")
                self.tree.set(iid, "progress", "100%")
            self.last_dir = dst.parent

        elif kind == "skip":
            _, i, src, reason = msg
            iid = str(src.resolve())
            if self.tree.exists(iid):
                self.tree.item(iid, tags=("skip",))
                self.tree.set(iid, "status", f"跳过")
                self.tree.set(iid, "progress", "—")
            self._status(f"跳过 {src.name}：{reason}")

        elif kind == "fail":
            _, i, src, err = msg
            iid = str(src.resolve())
            if self.tree.exists(iid):
                self.tree.item(iid, tags=("fail",))
                self.tree.set(iid, "status", "失败")
                self.tree.set(iid, "progress", "—")
            self.failures.append(f"{src.name}：{err}")
            self._status(f"失败 {src.name}：{err}")

        elif kind == "fatal":
            self.failures.append(str(msg[1]))
            self._status(f"出错：{msg[1]}")

        elif kind == "done":
            _, ok, skipped, failed, cancelled, last_dir = msg
            self.running = False
            if cancelled:
                self._status(f"已取消（完成 {ok}，跳过 {skipped}，失败 {failed}）")
            else:
                self.pb["value"] = 100
                self._status(f"完成：成功 {ok}，跳过 {skipped}，失败 {failed}")
            if last_dir:
                self.last_dir = last_dir
            self.tree.selection_remove(self.tree.selection())
            self._set_busy(False)
            if self.failures and not cancelled:
                detail = "\n".join(self.failures[:6])
                if len(self.failures) > 6:
                    detail += f"\n…… 另有 {len(self.failures) - 6} 个失败"
                messagebox.showwarning("部分文件失败", detail)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for w in (self.btn_add, self.btn_add_dir, self.btn_remove,
                  self.btn_clear, self.btn_start,
                  self.spin_track, self.ent_out, self.btn_browse,
                  self.chk_overwrite):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        self.cmb_quality.configure(state="disabled" if busy else "readonly")
        self.btn_cancel.configure(state="normal" if busy else "disabled",
                                  text="取消")
        self.btn_start.configure(
            state="disabled" if busy else "normal",
            text="转换中…" if busy else "开始转换",
            bg="#8fb0f0" if busy else ACCENT)
        self.btn_open.configure(
            state="normal" if (not busy and self.last_dir) else "disabled")

    def cancel_run(self) -> None:
        if not self.running or self.cancel.is_set():
            return
        self.cancel.set()
        self.btn_cancel.configure(state="disabled", text="正在取消…")
        self._status("正在取消：当前文件转换完成后停止…")

    def open_output(self) -> None:
        if self.last_dir and self.last_dir.is_dir():
            try:
                os.startfile(str(self.last_dir))  # type: ignore[attr-defined]
            except OSError as exc:
                messagebox.showerror("无法打开目录", str(exc))

    def choose_out_dir(self) -> None:
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            self.ent_out.delete(0, "end")
            self.ent_out.insert(0, folder)

    def _status(self, text: str) -> None:
        self.lbl_status.config(text=text)

    def on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno("正在转换", "转换还没结束，确定退出吗？"):
                return
            self.cancel.set()
        self.root.destroy()


def main() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = create_root()
    try:
        tkfont.nametofont("TkDefaultFont").configure(family=FONT, size=10)
        tkfont.nametofont("TkTextFont").configure(family=FONT, size=10)
    except Exception:
        pass

    app = ConverterApp(root)

    # 支持把视频拖到 exe 图标上启动：自动加入队列；再加 --start 就直接开转
    cli_files = [a for a in sys.argv[1:] if not a.startswith("--")]
    if cli_files:
        app.add_paths(cli_files)
        if "--start" in sys.argv[1:]:
            root.after(400, app.start)

    root.mainloop()


if __name__ == "__main__":
    main()
