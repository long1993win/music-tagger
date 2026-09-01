# 🎵 MusicTagger — 音乐自动补全工具

自动识别文件夹中的音乐文件，从 iTunes / Deezer / MusicBrainz 多源搜索匹配，补全**标题、艺术家、唱片集**，并下载最合适的**封面图片**嵌入文件。

## ✨ 特性

- **自动识别**：解析文件名（支持 `Artist - Title`、`01. Artist - Title` 等格式），无需手动输入
- **多源搜索**：iTunes + Deezer + MusicBrainz 三源交叉比对，按相似度打分取最优
- **智能封面**：优先取最佳匹配的封面；若最佳候选无封面，自动从其他源兜底（覆盖 DJ 变种 / Remix 等冷门曲目）
- **批量写入**：支持 MP3 / FLAC / M4A / OGG 格式，标题/艺术家/专辑/封面一次写入
- **图形界面**：选文件夹 → 自动识别 → 预览 → 一键写入

## 📦 打包为 EXE

在 Windows 上，双击 `build.bat` 即可。生成的 `dist\MusicTagger.exe` 可独立运行，无需安装 Python。

前置条件：安装 [Python 3.10+](https://python.org)（勾选 "Add to PATH"）。

## 🚀 直接运行（开发模式）

```bat
pip install -r requirements.txt
python app.py
```

## 📋 使用流程

1. 点 **📁 选择文件夹** → 扫描所有音乐文件
2. 点 **🔍 开始识别** → 自动从三个源搜索匹配
3. 预览列表中的识别结果（标题/艺术家/专辑/封面/来源/分数）
4. 勾选要补全的项目 → 点 **💾 全部写入** 或 **✏️ 写入选中**

## 🔧 技术细节

| 项目 | 说明 |
|------|------|
| 识别方式 | 文件名解析 + 多源 API 搜索 + 相似度打分 |
| 数据源 | iTunes Search API / Deezer API / MusicBrainz |
| 封面兜底 | best 候选无封面 → 遍历其他候选 → Cover Art Archive |
| 标签写入 | mutagen（MP3=ID3v2, FLAC=VorbisComment+Picture, M4A=MP4 atoms） |
| 封面处理 | PIL 压缩至 1000px 内，JPEG 90% 质量 |

## 📁 文件结构

```
music-tagger/
├── app.py            # GUI 入口
├── core.py           # 核心逻辑（识别/搜索/封面/写入）
├── requirements.txt
├── build.bat         # Windows 一键打包
└── README.md
```
