# ClipMind

把抖音短视频变成结构化笔记。你只需要复制分享链接。

```
粘贴分享文字  →  自动取回视频  →  语音转写 ┐
                                        ├→  融合  →  笔记 + 关键帧
                                 画面 OCR ┘
```

不需要手动下载 MP4，不需要录屏，不需要浏览器插件，不需要填账号密码。

---

## 为什么不用录屏

最初的设想是"浏览器能播放就录下来"。实测下来没必要：`yt-dlp` 直接支持抖音，
配合 `--cookies-from-browser chrome` 借用你已登录的会话，就能拿到原始媒体流。

这带来一个关键差别：

| | 录屏方案 | 现在的方案 |
|---|---|---|
| 3:47 的视频取回耗时 | ≥ 3:47（必须播放完） | 几秒 |
| 需要 Chrome 扩展 | 是 | 否 |
| 视频窗口需保持前台 | 是 | 否 |

所以处理时间由转写和 OCR 决定，而不是由视频时长决定。

## 安装

```bash
brew install ffmpeg yt-dlp
uv venv --python 3.12
uv pip install -r requirements.txt
```

macOS 会在首次读取 Chrome cookie 时弹一次钥匙串授权。首次运行还会下载
Whisper 模型（约 1.6 GB），之后都走本地缓存。

## 使用

```bash
python run.py
```

打开 http://127.0.0.1:8420，把分享文字整段粘进去 —— 不用自己把 URL 抠出来，
一次粘多个也可以，它们会并行处理。

命令行同样可用：

```bash
python cli.py "4.66 g@b.nQ ULJ:/ 标题 https://v.douyin.com/xxxx/ 复制此链接…"
```

## 测试

测试全部使用本地 fake，不访问抖音，也不下载真实媒体：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 产出

```
out/<job-id>/
├── note.md            # 摘要 + 要点 + 关键帧 + 全文转写
├── metadata.json
├── transcript.json
└── keyframes/
    ├── 00-08.jpg
    └── 00-23.jpg
```

源视频和音频在处理完会自动删除（`CLIPMIND_KEEP_VIDEO=1` 可保留）。

## 成本

除了最后一步的 AI 总结，全部免费且在本机运行：

| 环节 | 用什么 | 成本 |
|---|---|---|
| 取回视频 | yt-dlp | 免费 |
| 音频/抽帧 | ffmpeg | 免费 |
| 语音转写 | MLX Whisper（Apple GPU） | 免费 |
| 画面文字 | macOS Vision OCR | 免费，无需下载模型 |
| 关键帧筛选 | dHash + OCR 新增字判定 | 免费 |
| 笔记归纳 | Claude API | 可选，约几分钱一条 |

**没有配 `ANTHROPIC_API_KEY` 也能跑完整流程**，只是最后一步不做模型归纳，
改为把转写和 OCR 结构化拼接输出。配好 key 重跑即可。

## 关键帧是怎么选的

"每 5 秒截一张"会得到大量重复图。这里是一个漏斗：

```
2 fps 采样  →  dHash 去重  →  OCR  →  按"新增文字量"排序  →  取前 10
```

一个 52 秒的视频大约从 100 张候选降到 10 张，留下的都是画面真正变化的时刻。

## 并发

不同环节用独立的并发池，避免某一环把机器占死：

| 池 | 默认 | 原因 |
|---|---|---|
| `CLIPMIND_MAX_VIDEOS` | 4 | 同时处理的视频数 |
| `CLIPMIND_MAX_FETCH` | 4 | 网络 IO，可以多开 |
| `CLIPMIND_MAX_ASR` | 1 | 只有一块 GPU |
| `CLIPMIND_MAX_OCR` | 2 | CPU |

同一个视频内部，语音转写和画面 OCR 也是并行的，只在生成笔记时汇合。

## 采集失败时

`fetch` 按顺序尝试：Chrome cookie → 无 cookie → Safari cookie → 指定的
cookies.txt。全部失败会把每一层的真实报错显示在界面上，而不是只说"失败"。

不想让程序读浏览器 cookie 的话，在 `.env` 里改成：

```
CLIPMIND_COOKIE_SOURCES=-
CLIPMIND_COOKIE_FILE=/path/to/cookies.txt
```

## 配置

见 `.env.example`。

## 范围

只做抖音 + macOS。把一条链路做稳，比支持一堆平台但每个都时灵时不灵有用。
