# ClipMind

本地优先的抖音视频证据提取器。你只需要复制分享链接。

```
粘贴分享文字  →  自动取回视频  →  完整语音转写 ┐
                                            ├→  时间线  →  结构化证据
                               画面变化 + OCR ┘
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
uv pip install -r clipmind/requirements.txt
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

## 当前产出

当前实现仍使用下面的兼容格式；它是 Evidence Pack 的前身，不是最终 v1 schema：

```
out/<job-id>/
├── job.json           # 可恢复的任务状态
├── note.md            # 当前的人类可读证据视图
├── metadata.json
├── transcript.json
└── keyframes/
    ├── 00-08.jpg
    └── 00-23.jpg
```

源视频和音频在处理完会自动删除（`CLIPMIND_KEEP_VIDEO=1` 可保留）。

v1 的 canonical artifact 将是确定性的 **Evidence Pack**：完整时间戳转写、OCR、
视觉时间线、所有实质不同的稳定画面、来源信息和 schema/manifest。ClipMind 只负责
提取和组织证据；判断重点、关联已有知识和决定长期保留内容，交给下游知识库 agent。

## 免费路径

完整的 canonical extraction pipeline 不需要任何付费 API key：

| 环节 | 用什么 | 成本 |
|---|---|---|
| 取回视频 | yt-dlp | 免费 |
| 音频/抽帧 | ffmpeg | 免费 |
| 语音转写 | MLX Whisper（Apple GPU） | 免费 |
| 画面文字 | macOS Vision OCR | 免费，无需下载模型 |
| 视觉状态检测 | 图像哈希 + OCR 变化 | 免费 |
| 时间线与 Evidence Pack | 确定性本地代码 | 免费 |

当前代码保留了一个可选的 Claude 摘要兼容路径，但它不是 v1 的必需能力，也不会
成为 Evidence Pack 的依赖。没有 `ANTHROPIC_API_KEY` 时，核心提取能力仍应完整成立。

## 视觉提取状态

当前实现使用下面的漏斗，并固定最多保留 10 张：

```
2 fps 采样  →  dHash 去重  →  OCR  →  按"新增文字量"排序  →  取前 10
```

这是已知的 v1 缺口，不是最终契约。v1 禁止固定每个视频的数量：应按内容保留所有
实质不同、稳定且可读的视觉状态；纯口播可能只有少数几张，白板/PPT 按实际页面数，
密集代码或 UI 演示则可能更多。紧凑 preview 可以存在，但不能截断完整证据集。

## 并发

不同环节用独立的并发池，避免某一环把机器占死：

| 池 | 默认 | 原因 |
|---|---|---|
| `CLIPMIND_MAX_VIDEOS` | 4 | 同时处理的视频数 |
| `CLIPMIND_MAX_FETCH` | 4 | 网络 IO，可以多开 |
| `CLIPMIND_MAX_ASR` | 1 | 只有一块 GPU |
| `CLIPMIND_MAX_OCR` | 2 | CPU |

同一个视频内部，语音转写和画面 OCR 也是并行的，只在生成时间线时汇合。

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

只做抖音 + macOS。ClipMind 提取证据；下游知识库 agent 负责解释、总结、去重、
关联已有知识和决定保留内容。语义摘要、多平台和复杂知识库集成都不是 v1 核心。
