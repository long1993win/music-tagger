# -*- coding: utf-8 -*-
"""
MusicTagger 核心逻辑
识别文件名 -> 多源搜索(准确率打分) -> 下载封面 -> 写回标签
无 GUI 依赖，可独立测试 / 命令行使用。
"""
import io
import os
import re
import time
import difflib
import urllib.parse

import requests
from mutagen import File as MFile
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPE2, TRCK, APIC, USLT
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

UA = {"User-Agent": "MusicTagger/1.0 (personal use)"}
MB_HEADERS = {"User-Agent": "MusicTagger/1.0 (personal-use; contact: user@example.com)"}

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".wma", ".aac", ".wav", ".ape", ".wv"}

# ---------------------------------------------------------------- 文件名解析
def parse_filename(path: str) -> dict:
    """从文件名里尽量拆出 艺术家 / 标题 / 版本 / 音轨号。"""
    base = os.path.splitext(os.path.basename(path))[0].strip()
    base = base.replace("_", " ").replace(".", " ").strip()
    # 去掉方括号 / 书名号装饰，如 [Official Audio]、【高清修复】
    base = re.sub(r"\[[^\]]*\]", " ", base)
    base = re.sub(r"【[^】]*】", " ", base)
    base = re.sub(r"\s+", " ", base).strip()

    track = None
    m = re.match(r"^(\d{1,3})[\s._\-]+", base)
    if m:
        track = int(m.group(1))
        base = base[m.end():].strip()

    # Artist - Title (支持各种连接符)
    artist = title = None
    m = re.match(r"^(.+?)\s+(?:-|–|—|~|:)\s+(.+)$", base)
    if m:
        artist = m.group(1).strip().strip("-–—~:")
        title = m.group(2).strip().strip("-–—~:")
    else:
        # 只有标题
        title = base

    # 分离版本标签 (xxx) 保留在标题里，对 DJ 混音很重要
    return {
        "track": track,
        "artist": artist or None,
        "title": title or None,
        "raw": base,
    }


def clean_artist(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\s*[\(\[]?(feat\.?|ft\.?|featuring)\s+.*$", "", s, flags=re.I)
    s = re.sub(r"\s*[\(\[](remix|extended|club mix|edit|radio edit|original mix|instrumental|acoustic)[^)\]]*$", "", s, flags=re.I)
    return s.strip()


def clean_title(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"^(official|lyrics|audio)\s*(\s-)?", "", s, flags=re.I)
    return s.strip()


def norm(s) -> str:
    """归一化，用于相似度比较。"""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", s)
    return s


def sim(a, b) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


# ---------------------------------------------------------------- 多源搜索
def search_itunes(artist=None, title=None, limit=8):
    term = " ".join(x for x in (clean_artist(artist), title) if x).strip()
    if not term:
        return []
    try:
        r = requests.get("https://itunes.apple.com/search",
                         params={"term": term, "media": "music", "entity": "song", "limit": limit},
                         timeout=15, headers=UA)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception:
        return []


def search_deezer(artist=None, title=None, limit=8):
    q = " ".join(x for x in (clean_artist(artist), title) if x).strip()
    if not q:
        return []
    try:
        r = requests.get("https://api.deezer.com/search",
                         params={"q": q, "limit": limit}, timeout=15, headers=UA)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception:
        return []


def search_musicbrainz(artist=None, title=None, limit=8):
    query = " AND ".join(f"recording:{p}" for p in (title,))
    if artist:
        query += f" AND artist:{artist}"
    try:
        r = requests.get("https://musicbrainz.org/ws/2/recording/",
                         params={"query": query, "fmt": "json", "limit": limit},
                         timeout=20, headers=MB_HEADERS)
        r.raise_for_status()
        return r.json().get("recordings", [])
    except Exception:
        return []


def _cover_art_archive(mbid):
    if not mbid:
        return None
    for size in ("1200", "500", "250"):
        try:
            r = requests.get(f"https://coverartarchive.org/release/{mbid}/front-{size}",
                             timeout=15, headers=UA)
            if r.status_code == 200:
                return r.content
        except Exception:
            continue
    return None


def _itunes_artwork_url(url) -> str:
    """iTunes 100x100 换成 600x600。"""
    if not url:
        return url
    return re.sub(r"100x100(bb)?\.jpg$", "600x600bb.jpg", url)


# ---------------------------------------------------------------- 识别主流程
def build_candidates(artist, title, album=None):
    """汇总各源候选，统一格式 + 打分。"""
    cands = []

    for it in search_itunes(artist, title):
        cands.append({
            "source": "iTunes",
            "title": it.get("trackName"),
            "artist": it.get("artistName"),
            "album": it.get("collectionName"),
            "album_artist": it.get("artistName"),
            "cover_url": _itunes_artwork_url(it.get("artworkUrl100")),
            "mbid": None,
            "score": 0,
        })

    for dz in search_deezer(artist, title):
        pic = dz.get("album", {}).get("cover_xl") or dz.get("album", {}).get("cover_big")
        cands.append({
            "source": "Deezer",
            "title": dz.get("title"),
            "artist": dz.get("artist", {}).get("name"),
            "album": dz.get("album", {}).get("title"),
            "album_artist": None,
            "cover_url": pic,
            "mbid": None,
            "score": 0,
        })

    for mb in search_musicbrainz(artist, title):
        rel = (mb.get("releases") or [None])[0]
        cands.append({
            "source": "MusicBrainz",
            "title": mb.get("title"),
            "artist": (mb.get("artist-credit") or [{}])[0].get("name"),
            "album": rel.get("title") if rel else None,
            "album_artist": None,
            "cover_url": None,
            "mbid": rel.get("id") if rel else None,
            "score": 0,
        })

    # 打分：标题相似度最重要，其次艺术家、专辑
    for c in cands:
        s = sim(title, c["title"]) * 5
        if artist:
            s += sim(clean_artist(artist), clean_artist(c["artist"] or "")) * 3
        if album:
            s += sim(album, c["album"] or "") * 2
        c["score"] = round(s, 3)

    cands.sort(key=lambda x: x["score"], reverse=True)
    return cands


def read_existing_tags(path: str) -> dict:
    """尽量读出已有标签，作为搜索种子。"""
    try:
        f = MFile(path, easy=False)
    except Exception:
        return {}
    if f is None:
        return {}
    out = {}
    try:
        out["title"] = f.get("TIT2", f.get("\xa9nam", [None]))[0] if hasattr(f, "get") else None
    except Exception:
        pass
    return out


def identify(path: str, prefer_existing=True) -> dict:
    """识别单文件，返回 {parsed, best, candidates}"""
    parsed = parse_filename(path)
    artist, title, album = parsed["artist"], parsed["title"], None

    cands = build_candidates(artist, title, album)
    if not cands:
        return {"parsed": parsed, "best": None, "candidates": []}

    best = cands[0]
    return {"parsed": parsed, "best": best, "candidates": cands}


# ---------------------------------------------------------------- 封面
def fetch_cover(url_or_bytes, max_size=1000) -> bytes:
    """按 url 抓封面，PIL 压缩到 max_size 内，返回 JPEG bytes。"""
    data = url_or_bytes if isinstance(url_or_bytes, bytes) else None
    if data is None and url_or_bytes:
        try:
            r = requests.get(url_or_bytes, timeout=20, headers=UA)
            r.raise_for_status()
            data = r.content
        except Exception:
            return b""
    if not data:
        return b""
    if HAS_PIL:
        try:
            im = Image.open(io.BytesIO(data))
            im.thumbnail((max_size, max_size))
            if im.mode != "RGB":
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
            return buf.getvalue()
        except Exception:
            pass
    return data


def resolve_cover(best, candidates=None) -> bytes:
    """
    按优先级拿封面：best 的图 -> 其他候选的封面兜底 -> Cover Art Archive。
    保证即使 best 是 MusicBrainz 候选(无封面)，也能从 iTunes/Deezer 候选拿到封面。
    """
    candidates = candidates or []

    # 1) best 自己的封面
    if best and best.get("cover_url"):
        data = fetch_cover(best["cover_url"])
        if data:
            return data

    # 2) 其他候选的封面（按 score 排序，跳过无封面的）
    for c in candidates:
        if c is best:
            continue
        if c.get("cover_url"):
            data = fetch_cover(c["cover_url"])
            if data:
                return data

    # 3) Cover Art Archive（需要 MBID）
    for c in [best] + candidates:
        if c and c.get("mbid"):
            raw = _cover_art_archive(c["mbid"])
            if raw:
                return raw
    return b""


# ---------------------------------------------------------------- 歌词
LYRIC_LANG = "zh"  # USLT 语言字段

def fetch_lyrics(artist=None, title=None, album=None) -> str:
    """从 lrclib.net 获取歌词。优先带时间戳的 LRC，否则纯文本。"""
    params = {}
    if artist:
        params["artist_name"] = clean_artist(artist)
    if title:
        params["track_name"] = title
    if not params:
        return ""
    try:
        url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(params)
        r = requests.get(url, timeout=15, headers=UA)
        if r.status_code != 200:
            return ""
        d = r.json()
        return (d.get("syncedLyrics") or d.get("plainLyrics") or "")
    except Exception:
        return ""


# ---------------------------------------------------------------- 写标签
def write_tags(path: str, meta: dict, cover: bytes = b"", lyrics: str = "") -> bool:
    """
    meta: {title, artist, album, album_artist, track}
    返回 True 表示成功（无覆盖则返回 True 表示不需要处理）。
    """
    if not os.path.exists(path):
        return False
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":
            audio = ID3(path)
        else:
            audio = MFile(path, easy=False)
        if audio is None:
            return False
    except Exception:
        # 新文件没有标签
        if ext == ".mp3":
            audio = ID3()
        else:
            audio = MFile(path, easy=False)
        if audio is None:
            return False

    if ext == ".mp3":
        if meta.get("title"):
            audio.add(TIT2(encoding=3, text=meta["title"]))
        if meta.get("artist"):
            audio.add(TPE1(encoding=3, text=meta["artist"]))
        if meta.get("album"):
            audio.add(TALB(encoding=3, text=meta["album"]))
        if meta.get("album_artist"):
            audio.add(TPE2(encoding=3, text=meta["album_artist"]))
        if meta.get("track"):
            audio.add(TRCK(encoding=3, text=str(meta["track"])))
        if cover:
            # 覆盖旧封面
            for k in list(audio.keys()):
                if k.startswith("APIC"):
                    del audio[k]
            audio.add(APIC(encoding=3, mime="image/jpeg", type=3,
                           desc="Cover", data=cover))
        if lyrics:
            for k in list(audio.keys()):
                if k.startswith("USLT"):
                    del audio[k]
            audio.add(USLT(encoding=3, lang=LYRIC_LANG, desc="", text=lyrics))
        # 强制 v2.3，兼容 Windows 资源管理器 / WMP / 老播放器
        try:
            audio.update_to_v24 = False
        except Exception:
            pass
        audio.save(path)

    elif ext == ".flac":
        audio = FLAC(path)
        if meta.get("title"):
            audio["title"] = meta["title"]
        if meta.get("artist"):
            audio["artist"] = meta["artist"]
        if meta.get("album"):
            audio["album"] = meta["album"]
        if meta.get("album_artist"):
            audio["albumartist"] = meta["album_artist"]
        if meta.get("track"):
            audio["tracknumber"] = str(meta["track"])
        if cover:
            audio.clear_pictures()
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.data = cover
            audio.add_picture(pic)
        if lyrics:
            audio["lyrics"] = lyrics
        audio.save()

    elif ext in (".m4a", ".mp4"):
        audio = MP4(path)
        if meta.get("title"):
            audio["\xa9nam"] = meta["title"]
        if meta.get("artist"):
            audio["\xa9ART"] = meta["artist"]
        if meta.get("album"):
            audio["\xa9alb"] = meta["album"]
        if meta.get("album_artist"):
            audio["aART"] = meta["album_artist"]
        if meta.get("track"):
            try:
                audio["trkn"] = [(int(meta["track"]), 0)]
            except Exception:
                pass
        if cover:
            audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        if lyrics:
            audio["\xa9lyr"] = lyrics
        audio.save()

    elif ext in (".ogg", ".opus"):
        audio = OggVorbis(path)
        if meta.get("title"):
            audio["title"] = meta["title"]
        if meta.get("artist"):
            audio["artist"] = meta["artist"]
        if meta.get("album"):
            audio["album"] = meta["album"]
        if meta.get("album_artist"):
            audio["albumartist"] = meta["album_artist"]
        if meta.get("track"):
            audio["tracknumber"] = str(meta["track"])
        if cover:
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.data = cover
            audio["metadata_block_picture"] = [pic.write().decode("ascii")]
        if lyrics:
            audio["lyrics"] = lyrics
        audio.save()

    else:
        # WAV/APE/WMA 等不支持内嵌封面 → 旁挂 folder.jpg（多数播放器认这个）
        if cover:
            try:
                folder = os.path.dirname(path)
                with open(os.path.join(folder, "folder.jpg"), "wb") as f:
                    f.write(cover)
            except Exception:
                pass
        return True

    return True


# ---------------------------------------------------------------- 扫描
def scan_folder(folder: str):
    files = []
    for root, _, names in os.walk(folder):
        for n in sorted(names):
            if os.path.splitext(n)[1].lower() in AUDIO_EXTS:
                files.append(os.path.join(root, n))
    return files


# ---------------------------------------------------------------- CLI 测试入口
if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="MusicTagger CLI")
    ap.add_argument("path", nargs="+", help="文件或文件夹")
    ap.add_argument("--write", action="store_true", help="写回标签")
    ap.add_argument("--cover", action="store_true", help="下载封面")
    args = ap.parse_args()

    files = []
    for p in args.path:
        if os.path.isdir(p):
            files += scan_folder(p)
        elif os.path.isfile(p):
            files.append(p)

    for fp in files:
        res = identify(fp)
        best = res["best"]
        print(f"\n== {os.path.basename(fp)}")
        print(f"  解析: {res['parsed']}")
        if not best:
            print("  ✗ 未识别到")
            continue
        print(f"  ✓ [{best['source']}] 标题={best['title']!r} 艺术家={best['artist']!r} "
              f"专辑={best['album']!r} (score={best['score']})")
        if args.cover or args.write:
            cover = resolve_cover(best, res["candidates"])
            print(f"  封面: {'✓ ' + str(len(cover)) + 'B' if cover else '✗ 未找到'}")
        lyrics = ""
        if args.write:
            lyrics = fetch_lyrics(best.get("artist"), best.get("title"))
            print(f"  歌词: {'✓ ' + str(len(lyrics)) + '字符' if lyrics else '✗ 未找到'}")
        if args.write:
            ok = write_tags(fp, {
                "title": best["title"], "artist": best["artist"],
                "album": best["album"], "album_artist": best.get("album_artist"),
                "track": res["parsed"]["track"],
            }, cover if args.cover else b"", lyrics)
            print(f"  写入: {'✓' if ok else '✗'}")
