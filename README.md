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

每个成功任务都会生成 versioned Evidence Pack；`manifest.json` 最后写入，是可以复用
这个结果的完成标记：

```
out/<job-id>/
├── manifest.json      # clipmind-evidence-pack@1.0.0
├── source.json
├── job.json           # 可恢复的任务状态
├── transcript.jsonl   # 完整、带时间戳的 ASR segments
├── transcript.md
├── ocr.jsonl          # 每个 canonical visual state 一条记录
├── visual_timeline.jsonl
├── evidence.md        # 确定性的完整人类可读证据视图
├── visual_states/
│   ├── all/            # 当前采样与近重复过滤后保留的完整 canonical 集合
│   └── preview/        # 内容驱动预览；渐进构建只展示完成态，无固定张数上限
├── metadata.json      # 以下为迁移期兼容产物
├── transcript.json
├── note.md
└── keyframes/
```

源视频和音频在处理完会自动删除（`CLIPMIND_KEEP_VIDEO=1` 可保留）。

Evidence Pack 的字段、ID、完整性状态和责任边界见
[`docs/EVIDENCE_PACK.md`](docs/EVIDENCE_PACK.md)，manifest 的机器可读定义见
[`schemas/evidence-pack-v1.schema.json`](schemas/evidence-pack-v1.schema.json)。ClipMind
只负责提取和组织证据；判断重点、关联已有知识和决定长期保留内容，交给下游知识库
agent。

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

当前实现先无固定数量上限地保留 canonical 集合，再非破坏性地标注渐进构建并派生预览：

```
2 fps 采样  →  dHash 近重复过滤  →  visual_states/all/（无固定上限）
                          ├→ OCR + progressive build 分组
                          │  └→ visual_states/preview/（无固定上限，每组取完成态）
                          └→ collapse/score/select
                             └→ keyframes/（旧笔记格式的最多 10 张兼容产物）
```

build group 只合并相邻、OCR 内容单调增加的状态；文字被替换或消失就立即断组，因此
前面的信息仍会进入 preview。这里的 canonical 仍表示“保留所有通过当前采样与安全
近重复过滤的候选状态”，还没有完整解决转场、稳定性和信息价值。最终 v1 仍应按
内容识别所有实质不同、稳定且可读的视觉状态；preview 不会截断完整证据集。

变化检测使用 640px 候选帧；最终 canonical 图片和 OCR 默认使用 1280px。这个选择来自
同一条 227 秒代码/UI 视频的对照实验：640px OCR 识别 559 个不同字符，1280px 识别
1034 个；OCR 总耗时从 24.7 秒增至 36.6 秒，最终图片约从 6.4MB 增至 19.5MB。
原始结果见 `docs/ocr-resolution-experiment.json`，可用
`scripts/compare_ocr_resolution.py` 在本地视频上复跑。

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
