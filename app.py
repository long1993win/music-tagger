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
    fetch_lyrics, process_many, read_existing_tags, read_cover,
    AUDIO_EXTS, fetch_cover,
)

APP_TITLE = "🎵 MusicTagger — 音乐自动补全"
COLS = ["#", "文件名", "标题", "艺术家", "专辑", "封面", "歌词", "来源", "分数"]
COL_WIDTHS = [35, 180, 160, 130, 130, 70, 70, 70, 50]

# 并发线程数（公开无 key API 限流较宽松，4-6 安全；过高易被限流）
WORKERS = 4


class MusicTaggerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1100x620")
        root.minsize(800, 400)

        # 状态
        self.files = []          # [filepath]
        self.results = {}        # filepath -> {parsed, best, candidates, cover, lyrics}
        self.originals = {}      # filepath -> 文件原有标签 (识别前)
        self.processing = False

        self._build_ui()

        # 右键菜单
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="📄 查看详细信息", command=self.show_details)
        self.menu.add_command(label="🎵 查看歌词", command=self.show_lyrics)
        self.menu.add_command(label="🖼 查看封面", command=self.show_cover)
        self.tree.bind("<Button-3>", self._on_right_click)

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
        self.opt_lyrics = tk.BooleanVar(value=True)
        for txt, var in [("补全标题", self.opt_title), ("补全艺术家", self.opt_artist),
                         ("补全专辑", self.opt_album), ("下载封面", self.opt_cover),
                         ("下载歌词", self.opt_lyrics)]:
            ttk.Checkbutton(opt, text=txt, variable=var).pack(side="left", padx=6)

        ttk.Label(opt, text="线程:").pack(side="left", padx=(16, 2))
        self.worker_var = tk.IntVar(value=WORKERS)
        ttk.Spinbox(opt, from_=1, to=12, width=4, textvariable=self.worker_var) \
            .pack(side="left")

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
        self.originals.clear()
        self._refresh_tree()
        # 后台读取每个文件原有标签，随读随刷新
        threading.Thread(target=self._load_originals, daemon=True).start()
        self._set_status(f"扫描到 {len(self.files)} 个音乐文件，正在读取原有标签…")

    def _load_originals(self):
        n = len(self.files)
        for i, fp in enumerate(self.files):
            try:
                self.originals[fp] = read_existing_tags(fp)
            except Exception:
                self.originals[fp] = {}
            if i % 10 == 0 or i == n - 1:
                self.root.after(0, self._refresh_tree)
                self._set_status(f"读取原有标签 {i + 1}/{n}")
        self.root.after(0, self._refresh_tree)
        self._set_status(f"扫描+读取完成: {n} 个文件")

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, fp in enumerate(self.files):
            vals = self._row_values(i, fp)
            self.tree.insert("", "end", iid=str(i), values=vals)

    def _row_values(self, i, fp):
        """生成一行显示值：识别结果优先，否则显示文件原有标签。"""
        r = self.results.get(fp)
        b = r.get("best") if r else None
        orig = self.originals.get(fp) or {}

        if b:
            cover = r.get("cover", b"")
            if cover:
                cover_txt = f"✓ {len(cover) // 1024}KB"
            elif r.get("cover_fail"):
                cover_txt = "✗ 无图"
            else:
                cover_txt = "—"
            lyrics = r.get("lyrics", "")
            if lyrics:
                lyrics_txt = f"✓ {len(lyrics)}字"
            elif r.get("lyrics_fail"):
                lyrics_txt = "✗ 无词"
            else:
                lyrics_txt = "—"
            source = b.get("source", "")
            score = f"{b.get('score', 0):.1f}"
            return (i + 1, os.path.basename(fp), b.get("title", ""),
                    b.get("artist", ""), b.get("album", ""), cover_txt,
                    lyrics_txt, source, score)

        # 原始标签（识别前）
        cover_txt = "✓ 有" if orig.get("has_cover") else "—"
        lyrics_txt = "✓ 有" if orig.get("has_lyrics") else "—"
        return (i + 1, os.path.basename(fp), orig.get("title") or "",
                orig.get("artist") or "", orig.get("album") or "",
                cover_txt, lyrics_txt, "", "")

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
        want_cover = self.opt_cover.get()
        want_lyrics = self.opt_lyrics.get()
        workers = max(1, min(12, int(self.worker_var.get())))

        # 进度回调必须在后台线程安全地调用：只通过 after 排队到主线程
        last = [0]
        def _progress(done, total):
            # 节流：每完成 1 个或每 5 个刷新一次，避免 after 堆积
            if done - last[0] >= 1:
                last[0] = done
                try:
                    self.root.after(0, lambda d=done, t=total: self._update_progress(d, t))
                except Exception:
                    pass

        try:
            results = process_many(
                self.files,
                want_cover=want_cover,
                want_lyrics=want_lyrics,
                workers=workers,
                progress=_progress,
            )
        except Exception as e:
            self._set_status(f"识别出错: {e}")
            self.processing = False
            return

        # 关键修复：process_many 返回的 key 可能因异常中断而不完整，
        # 但正常情况应为全部文件。这里直接替换而非 update，避免残留旧数据。
        self.results.update(results)
        self.processing = False
        got = len(results)
        ok = sum(1 for r in results.values() if r.get("best"))
        self.root.after(0, self._refresh_tree)
        self._set_status(f"完成: 识别 {got}/{n} 个, 匹配成功 {ok} 个")

    def _update_progress(self, done, total):
        """主线程：更新进度和表格。"""
        self.progress["value"] = done
        self.status_var.set(f"识别中 ({done}/{total}) — 并行")
        self._refresh_tree()

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
            cover_ok = 0
            lyrics_ok = 0
            skipped = 0
            errors = []   # (filename, reason)
            for fp in files:
                r = self.results.get(fp)
                if not r or not r.get("best"):
                    skipped += 1
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
                lyrics = r.get("lyrics", "") if self.opt_lyrics.get() else ""
                try:
                    write_tags(fp, meta, cover, lyrics)
                    ok += 1
                    if cover:
                        cover_ok += 1
                    if lyrics:
                        lyrics_ok += 1
                except Exception as e:
                    errors.append((os.path.basename(fp), str(e)))

            # 汇总（线程安全地更新 UI）
            def _done():
                lines = [f"成功写入 {ok}/{len(files)} 个文件",
                         f"封面 {cover_ok} 个, 歌词 {lyrics_ok} 个"]
                if skipped:
                    lines.append(f"跳过 {skipped} 个（未识别到）")
                if errors:
                    lines.append(f"失败 {len(errors)} 个:")
                    for fn, why in errors[:6]:
                        lines.append(f"  • {fn}: {why}")
                    if len(errors) > 6:
                        lines.append(f"  … 共 {len(errors)} 个，详见 music-tagger.log")
                self._set_status(f"写入完成: {ok} 成功, {len(errors)} 失败, {skipped} 跳过")
                messagebox.showinfo("完成", "\n".join(lines))

            # 写日志
            try:
                logpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music-tagger.log")
                with open(logpath, "a", encoding="utf-8") as f:
                    f.write(f"=== 写入会话 ===\n成功 {ok} 失败 {len(errors)} 跳过 {skipped}\n")
                    for fn, why in errors:
                        f.write(f"  [失败] {fn}: {why}\n")
            except Exception:
                pass

            self.root.after(0, _done)

        threading.Thread(target=_do, daemon=True).start()

    def _set_status(self, msg):
        # 线程安全：可能从后台线程调用，统一通过 after 排队到主线程
        try:
            self.root.after(0, lambda: self.status_var.set(msg))
        except Exception:
            try:
                self.status_var.set(msg)
            except Exception:
                pass

    # ---- 右键查看 ----
    def _on_right_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.tree.focus(row)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _selected_file(self):
        sel = self.tree.selection()
        if not sel:
            return None
        idx = int(sel[0])
        if 0 <= idx < len(self.files):
            return self.files[idx]
        return None

    def show_details(self):
        fp = self._selected_file()
        if not fp:
            return
        orig = self.originals.get(fp) or {}
        r = self.results.get(fp)
        b = r.get("best") if r else None

        def fmt(x, none="—"):
            return str(x) if x not in (None, "") else none

        lines = []
        lines.append(f"文件名: {os.path.basename(fp)}")
        lines.append(f"完整路径: {fp}")
        lines.append(f"格式: {fmt(orig.get('format'))}   文件大小: {fmt(_fmt_size(orig.get('size', 0)))}")
        lines.append(f"时长: {fmt(orig.get('duration'), '—')} 秒")
        lines.append("")
        lines.append("【文件原有标签】")
        lines.append(f"  标题: {fmt(orig.get('title'))}")
        lines.append(f"  艺术家: {fmt(orig.get('artist'))}")
        lines.append(f"  专辑: {fmt(orig.get('album'))}")
        lines.append(f"  专辑艺术家: {fmt(orig.get('album_artist'))}")
        lines.append(f"  音轨号: {fmt(orig.get('track'))}")
        lines.append(f"  封面: {'有' if orig.get('has_cover') else '无'}")
        lines.append(f"  歌词: {'有 (' + str(len(orig.get('lyrics', ''))) + '字)' if orig.get('has_lyrics') else '无'}")
        lines.append("")
        lines.append("【识别结果】")
        if b:
            lines.append(f"  标题: {fmt(b.get('title'))}")
            lines.append(f"  艺术家: {fmt(b.get('artist'))}")
            lines.append(f"  专辑: {fmt(b.get('album'))}")
            lines.append(f"  来源: {fmt(b.get('source'))}   匹配分数: {b.get('score', 0):.1f}")
            lines.append(f"  新封面: {'有 (' + str(len(r.get('cover', b'')) // 1024) + 'KB)' if r.get('cover') else '无'}")
            lines.append(f"  新歌词: {'有 (' + str(len(r.get('lyrics', ''))) + '字)' if r.get('lyrics') else '无'}")
        else:
            lines.append("  尚未识别")

        self._show_text_window(f"详细信息 - {os.path.basename(fp)}", "\n".join(lines))

    def show_lyrics(self):
        fp = self._selected_file()
        if not fp:
            return
        # 优先识别后的新歌词，否则用原有歌词
        r = self.results.get(fp)
        lyric = (r or {}).get("lyrics", "") if r and r.get("lyrics") else ""
        src = "识别结果"
        if not lyric:
            lyric = (self.originals.get(fp) or {}).get("lyrics", "")
            src = "文件原有"
        if not lyric:
            messagebox.showinfo("歌词", "该文件没有歌词")
            return
        self._show_text_window(f"歌词 ({src}) - {os.path.basename(fp)}", lyric)

    def show_cover(self):
        fp = self._selected_file()
        if not fp:
            return
        r = self.results.get(fp)
        cover = (r or {}).get("cover", b"")
        src = "新封面"
        if not cover:
            cover = read_cover(fp)
            src = "原有封面"
        if not cover:
            messagebox.showinfo("封面", "该文件没有封面")
            return
        self._show_image_window(f"封面 ({src}) - {os.path.basename(fp)}", cover)

    def _show_text_window(self, title, text):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("600x500")
        t = tk.Text(win, wrap="word", font=("Consolas", 10))
        t.insert("1.0", text)
        t.configure(state="disabled")
        sb = ttk.Scrollbar(win, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _show_image_window(self, title, cover_bytes):
        try:
            from PIL import Image, ImageTk
            import io as _io
            im = Image.open(_io.BytesIO(cover_bytes))
            im.thumbnail((500, 500))
            if im.mode != "RGB":
                im = im.convert("RGB")
            win = tk.Toplevel(self.root)
            win.title(title)
            photo = ImageTk.PhotoImage(im)
            lbl = tk.Label(win, image=photo)
            lbl.image = photo
            lbl.pack(padx=10, pady=10)
        except Exception as e:
            messagebox.showerror("封面", f"无法显示封面: {e}")


def _fmt_size(n):
    try:
        n = int(n)
    except Exception:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n}{unit}"
        n //= 1024
    return f"{n}TB"


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
