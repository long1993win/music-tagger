# -*- coding: utf-8 -*-
"""
MusicTagger GUI — Windows 图形界面
选择文件夹 -> 自动识别 -> 预览/编辑 -> 一键写入标签+封面
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from core import (
    scan_folder, identify, resolve_cover, write_tags,
    AUDIO_EXTS, fetch_cover,
)

APP_TITLE = "🎵 MusicTagger — 音乐自动补全"
COLS = ["#", "文件名", "标题", "艺术家", "专辑", "封面", "来源", "分数"]
COL_WIDTHS = [40, 200, 180, 150, 150, 50, 80, 60]


class MusicTaggerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1100x620")
        root.minsize(800, 400)

        # 状态
        self.files = []          # [filepath]
        self.results = {}        # filepath -> {parsed, best, candidates, cover}
        self.processing = False

        self._build_ui()

    # ---- UI ----
    def _build_ui(self):
        # 顶部工具栏
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="📁 选择文件夹", command=self.choose_folder) \
            .pack(side="left")
        ttk.Button(top, text="🔍 开始识别", command=self.start_identify) \
            .pack(side="left", padx=4)
        ttk.Button(top, text="💾 全部写入", command=self.write_all) \
            .pack(side="left", padx=4)
        ttk.Button(top, text="✏️ 写入选中", command=self.write_selected) \
            .pack(side="left", padx=4)

        self.path_var = tk.StringVar(value="未选择")
        ttk.Label(top, textvariable=self.path_var, foreground="gray") \
            .pack(side="left", padx=8)

        # 选项
        opt = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        opt.pack(fill="x")
        self.opt_title = tk.BooleanVar(value=True)
        self.opt_artist = tk.BooleanVar(value=True)
        self.opt_album = tk.BooleanVar(value=True)
        self.opt_cover = tk.BooleanVar(value=True)
        for txt, var in [("补全标题", self.opt_title), ("补全艺术家", self.opt_artist),
                         ("补全专辑", self.opt_album), ("下载封面", self.opt_cover)]:
            ttk.Checkbutton(opt, text=txt, variable=var).pack(side="left", padx=6)

        # 文件列表
        mid = ttk.Frame(self.root, padding=(8, 4))
        mid.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(mid, columns=COLS, show="headings", selectmode="extended")
        for i, (c, w) in enumerate(zip(COLS, COL_WIDTHS)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, minwidth=30)
        self.tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        # 底部状态栏 + 进度
        bot = ttk.Frame(self.root, padding=(8, 4, 8, 8))
        bot.pack(fill="x")
        self.progress = ttk.Progressbar(bot, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bot, textvariable=self.status_var, width=40) \
            .pack(side="left")

    # ---- 逻辑 ----
    def choose_folder(self):
        folder = filedialog.askdirectory(title="选择音乐文件夹")
        if not folder:
            return
        self.path_var.set(folder)
        self.files = scan_folder(folder)
        self.results.clear()
        self._refresh_tree()
        self._set_status(f"扫描到 {len(self.files)} 个音乐文件")

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, fp in enumerate(self.files):
            r = self.results.get(fp)
            if r and r.get("best"):
                b = r["best"]
                cover = "✓" if r.get("cover") else "—"
                vals = (i + 1, os.path.basename(fp), b.get("title", ""),
                        b.get("artist", ""), b.get("album", ""), cover,
                        b.get("source", ""), f"{b.get('score', 0):.1f}")
            else:
                vals = (i + 1, os.path.basename(fp), "", "", "", "", "", "")
            self.tree.insert("", "end", iid=str(i), values=vals)

    def start_identify(self):
        if not self.files:
            messagebox.showinfo("提示", "请先选择文件夹")
            return
        if self.processing:
            return
        self.processing = True
        threading.Thread(target=self._identify_all, daemon=True).start()

    def _identify_all(self):
        n = len(self.files)
        self.progress["maximum"] = n
        for i, fp in enumerate(self.files):
            self._set_status(f"识别中 ({i + 1}/{n}): {os.path.basename(fp)}")
            try:
                res = identify(fp)
                best = res.get("best")
                cover = b""
                if best and self.opt_cover.get():
                    cover = resolve_cover(best, res.get("candidates", []))
                self.results[fp] = {
                    "parsed": res["parsed"],
                    "best": best,
                    "candidates": res.get("candidates", []),
                    "cover": cover,
                }
            except Exception as e:
                self.results[fp] = {"parsed": None, "best": None, "candidates": [], "cover": b"", "error": str(e)}
            self.progress["value"] = i + 1
            self.root.after(0, self._refresh_tree)
        self.processing = False
        ok = sum(1 for r in self.results.values() if r.get("best"))
        self._set_status(f"完成: {ok}/{n} 识别成功")

    def _get_selected_files(self):
        sel = self.tree.selection()
        return [self.files[int(s)] for s in sel]

    def write_selected(self):
        files = self._get_selected_files()
        if not files:
            messagebox.showinfo("提示", "请先在列表中选中行")
            return
        self._write_files(files)

    def write_all(self):
        if not self.files:
            messagebox.showinfo("提示", "请先选择文件夹")
            return
        self._write_files(self.files)

    def _write_files(self, files):
        def _do():
            ok = 0
            for fp in files:
                r = self.results.get(fp)
                if not r or not r.get("best"):
                    continue
                b = r["best"]
                meta = {}
                if self.opt_title.get():
                    meta["title"] = b.get("title")
                if self.opt_artist.get():
                    meta["artist"] = b.get("artist")
                if self.opt_album.get():
                    meta["album"] = b.get("album")
                meta["album_artist"] = b.get("album_artist")
                if r.get("parsed"):
                    meta["track"] = r["parsed"].get("track")
                cover = r.get("cover", b"") if self.opt_cover.get() else b""
                try:
                    write_tags(fp, meta, cover)
                    ok += 1
                except Exception as e:
                    print(f"写入失败 {fp}: {e}")
            self._set_status(f"写入完成: {ok}/{len(files)}")
            self.root.after(0, lambda: messagebox.showinfo("完成", f"成功写入 {ok}/{len(files)} 个文件"))

        threading.Thread(target=_do, daemon=True).start()

    def _set_status(self, msg):
        self.status_var.set(msg)
        self.root.update_idletasks()


def main():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.3)
    except Exception:
        pass
    MusicTaggerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
