# ClipMind v1 技术审计与质量门槛

审计对象：1760 行，13 个模块，零测试。三个真实抖音链接跑通。
本文不含代码改动，只定义 v1 的验收标准与实施顺序。

---

## A. 架构评估

### A.1 应当保留的结构

现有分层是对的，不要在 v1 重构掉：

```
分享文字 → URL 提取 → yt-dlp(已登录会话) → 临时媒体
                                              │
                              ┌───────────────┴───────────────┐
                              │ ASR (MLX Whisper)             │ 视觉 (抽帧 + OCR)
                              └───────────────┬───────────────┘
                                              ↓
                                        timeline fusion
                                              ↓
                                         summarizer
                                              ↓
                                       不可变笔记 + 清理
```

三个正确的决策：

1. **按资源类型分池，而不是一个全局并发数。** `fetch=4 / asr=1 / ocr=2 / llm=4`
   反映了各阶段争抢的是不同资源（网络 / GPU / CPU）。单一信号量会让网络等待
   占住 GPU 名额。
2. **产品产出是笔记，源媒体是实现细节。** 处理完即删。这个定位让整个系统
   不必去解决"下载器"要解决的问题。
3. **ASR 和 OCR 都已做到失败降级而非杀任务。** 这是失败隔离的正确方向，
   但覆盖不完整（见 B.5）。

### A.2 结构性缺陷

**缺陷 1：没有持久化层，内存与磁盘两套状态不对账。**

`JobStore.jobs` 是纯内存字典（jobs.py:43），启动时不扫描 `out/`。后果：
笔记在磁盘上好好的，重启后笔记库显示为空；重启时正在跑的任务无法恢复，
其临时文件成为孤儿。这是最大的单点结构问题。

**缺陷 2：timeline fusion 不是一个阶段，而是一个字符串拼接函数。**

架构图里 fusion 是独立环节，代码里它是 `summarize.build_context()`——
一个把转写和 OCR 拼成 prompt 的辅助函数。这带来两个问题：融合逻辑无法
独立测试；换 summarizer 就得重写融合。**融合必须先于摘要独立出来**，产出
一个确定性的、可断言的 `Timeline` 对象。

**缺陷 3：没有平台适配器边界。**

抖音的知识散落在两处：`links.py` 的正则、`fetch.py` 的 yt-dlp 调用。
没有 `DouyinAdapter` 接口。v1 只做抖音是对的，但边界要画出来——否则
抖音页面一变，改动会渗进核心。

**缺陷 4：`summarize.py` 同时承担融合、provider 选择、API 调用、降级。**

四件事挤在一个模块里，是 provider 抽象缺失的直接症状。

---

## B. 风险清单

### P0 — 阻断 v1

**P0-1 免费路径没有真正的语义摘要**

当前无 key 时走 `_fallback()`：把转写截断 400 字 + OCR 文字按 novelty 排序
拼接。这是抽取式拼接，不是归纳。本机实测 `ollama` 未安装、`mlx_lm` 未安装，
所以"免费也能用"目前只是"免费也能跑完"。

这是明确的硬约束，必须在 v1 解决。

**P0-2 重启丢失整个笔记库**

证据：`jobs.py:43` 仅 `self.jobs = {}`，全仓库无任何 `out/` 扫描逻辑。
`out/` 下已有 3 个完整笔记目录，但重启后 `GET /api/jobs` 返回空。
运行中的任务在重启后既不恢复也不标记失败。

**P0-3 零测试**

1760 行代码，`find` 无任何 `test_*`。这一条单独就能让仓库在面试中失分。

**P0-4 无幂等，重复 URL 全量重算**

`server.py:26` 的去重集合只包含 `queued|running`。已完成的 URL 再次提交会
重新下载、重新转写、重新 OCR。评测集里"同一 URL 提交两次"这一项目前必然
浪费一整轮算力。

**P0-5 临时文件生命周期只覆盖成功路径**

`pipeline.py:90-94` 的 `rmtree(samples)` 和 `unlink(source)` 是直线代码，
不在 `try/finally` 里。下载完成之后、`write_all` 之前的任何异常都会留下
`source.mp4` 加最多 1200 张候选 JPEG。

说明：本次实测的失败用例（无效链接）在下载前就失败了，目录是空的，
所以**这条是代码路径推断，尚未实测复现**。评测集必须包含"中途失败"用例来证实。

### P1 — 影响可信度

**P1-6 SSE 断线后永久丢事件**

服务端不发 event id，客户端 `EventSource` 自动重连但 `app.js` 无 `onerror`、
重连后不重新拉 `/api/jobs`。断线期间的所有状态变化永久丢失，界面卡在旧状态。

**P1-7 关键帧预算不随时长伸缩**

实测：

| 视频 | 时长 | 2fps 候选帧 | 最终关键帧 |
|---|---|---|---|
| VibeCoding 个人主页 | 227s | 454 | 10 |
| 面试问底层 | 64s | 128 | 10 |
| AI 产品经理面试 | 52s | 104 | 10 |

227 秒的视频和 52 秒的视频拿到同样的信息预算。`keyframe_information_coverage`
这个指标目前必然随时长单调下降。预算应当是时长与信息密度的函数。

**P1-8 降级链路有一级是死的**

实测 Safari rung 返回：

```
Operation not permitted: .../com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies
```

macOS TCC 保护 Safari 容器，除非终端被授予完全磁盘访问权限，这一级永远失败。
它只是在每次彻底失败时多加一次无用尝试和一条误导性报错。

**P1-9 失败原因不可行动**

无效短链的报错是 `Unsupported URL: https://www.douyin.com/`——短链重定向到了
裸域名。用户无法从中知道是链接失效、视频被删、需要登录，还是被限流。
需要一层失败分类。

**P1-10 cookie 授权粒度过粗**

`--cookies-from-browser chrome` 把**整个 Chrome cookie jar**（所有站点）交给
yt-dlp，而不只是 douyin.com。对一个本地工具可以接受，但必须在 README 里
明说，并提供只导出 douyin cookie 的路径。这是放在 GitHub 上会被问到的问题。

**P1-11 无任务取消，jobs 字典无界增长**

**P1-12 无可观测性**

无结构化日志，各阶段耗时不落盘。任务失败后无法事后诊断——只有一个
`job.error` 字符串。

### P2 — 打磨

- `media.dedupe` 逐帧 `except Exception: continue`，静默丢帧
- `job_id` 未校验格式，依赖 `resolve()+startswith` 兜底
- `fetch._winning_source` 是模块级可变全局
- `Settings` 在 import 时从环境变量固化，测试需 monkeypatch 环境
- README 的基准数字是手抄的，不可复现
- UI 暴露 `fallback (no API key)` 这种开发者措辞

---

## C. v1 验收标准

逐条可检验。全部满足才叫 v1。

### C.1 免费性（硬约束）

- [ ] 全新机器、**未设置任何 API key**，能得到真正的语义摘要而非拼接
- [ ] `ANTHROPIC_API_KEY` 不出现在任何必需路径；缺失它不产生任何警告
- [ ] 三级 summarizer 均可用且可显式选择：`extractive` / `local` / `api`

### C.2 正确性与失败隔离

- [ ] 任一单阶段失败（OCR / ASR / summarizer）不导致任务失败，产出标注降级原因
- [ ] 采集失败按类型分类上报，而非透传 yt-dlp 原始报错
- [ ] 任意阶段抛异常，临时媒体与候选帧 100% 被清理

### C.3 状态与重启

- [ ] 重启后笔记库完整恢复（从 `out/` 重建索引）
- [ ] 重启时 `running` 的任务被标记为 `interrupted`，其临时文件被回收
- [ ] 同一 URL 重复提交命中缓存，不重复下载与推理

### C.4 质量（不以 DONE 为准）

- [ ] `duplicate_keyframe_rate` < 10%（关键帧两两 dHash 距离全部 > 阈值）
- [ ] `keyframe_information_coverage` 不随时长显著下降
- [ ] 无语音幻灯片类视频仍产出有意义笔记（OCR 独立成立）

### C.5 工程

- [ ] 单元测试覆盖：URL 提取、dedupe、collapse_builds、评分、fusion、失败分级
- [ ] `make bench` 一条命令产出可复现基准，README 数字由它生成
- [ ] `make eval` 跑评测集并输出指标表
- [ ] 每个任务落盘结构化 stage timing，失败可事后诊断

### C.6 可解释性（面试目标）

- [ ] `ARCHITECTURE.md` 回答：每个阶段为何存在、失败时会怎样、什么并发、
      关键帧算法为何选中某一帧——**不看源码即可解释**
- [ ] UI 高级面板暴露实际使用的 provider，而非硬编码文案

---

## D. 评测矩阵

评测集固定 URL 列表 + 期望行为，纳入版本控制。

### D.1 内容维度

| # | 类型 | 主要考察 | 期望 |
|---|---|---|---|
| 1 | 纯口播 | ASR 主导 | 转写完整，关键帧少而不空 |
| 2 | 无语音幻灯片 | OCR 独立成立 | ASR 为空不算失败，笔记仍有内容 |
| 3 | 录屏 / 编程教学 | 小字号 OCR | 代码文本可读，帧不塌缩成一张 |
| 4 | 大量烧录中文字幕 | ASR/OCR 冗余 | fusion 不产生重复内容 |
| 5 | 画面文字极少 | 视觉信号弱 | 不因 novelty=0 而返回零帧 |
| 6 | 30s / 3min / 10min+ | 时长伸缩 | 关键帧预算随时长增长；耗时线性 |

### D.2 故障维度

| # | 注入 | 期望 |
|---|---|---|
| 7 | 同一 URL 提交两次 | 第二次命中缓存，不重复推理 |
| 8 | 一次提交 5–10 个 URL | 并发受限于 `max_videos`，无 OOM |
| 9 | 失效 / 已删 / 私密链接 | 分类报错，非原始 yt-dlp 文本 |
| 10 | Chrome cookie 不可用 | 明确提示需要什么，非静默失败 |
| 11 | OCR 强制失败 | 任务成功，笔记标注视觉降级 |
| 12 | ASR 强制失败 | 任务成功，笔记仅含视觉信息 |
| 13 | yt-dlp 中途失败 | **临时文件被清理**（验证 P0-5） |
| 14 | 运行中重启 | 任务标记 interrupted，临时文件回收 |

### D.3 指标

每次 `make eval` 产出：

```
ingestion_success_rate          成功采集 / 尝试
asr_realtime_factor             ASR 耗时 / 视频时长
ocr_runtime_per_frame           OCR 总耗时 / 帧数
end_to_end_latency              提交到笔记落盘
keyframe_count                  最终帧数
duplicate_keyframe_rate         两两 dHash 距离 <= 阈值的比例
keyframe_information_coverage   关键帧 OCR 字符并集 / 全部候选帧字符并集
batch_wall_clock_speedup        串行耗时和 / 实际墙钟
failure_recovery_rate           注入故障后仍产出可用笔记的比例
disk_cleanup_success            任务结束后残留字节数（应为仅笔记）
```

`keyframe_information_coverage` 是这套指标里最关键的一个——它是唯一能证明
"选帧算法真的在选信息"而不只是"选出了 10 张图"的量化依据。

---

## E. 实施顺序

依赖关系决定顺序，不要并行做 1 和 2。

**第 1 步：summarizer provider 抽象（解 P0-1）**

```
Timeline ──→ SummarizerProvider (Protocol)
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
  Extractive     Local        API
  无依赖         mlx_lm       anthropic
  永远可用       默认         可选
```

三级而非两级：`extractive` 必须保留为永远成立的地板，`local` 是默认，
`api` 是可选增强。选择逻辑：能加载本地模型就用 local，否则 extractive；
显式配置了 API 才用 api。

本地 provider 选 **mlx_lm** 而不是 Ollama：本机已通过 mlx-whisper 引入
MLX，同一运行时、无后台守护进程、无额外服务管理。模型选 4-bit Qwen 系
instruct（中文质量足够，显存可控）——具体仓库 tag 在实现时验证可用性再定。

**第 2 步：抽出 Timeline fusion（解缺陷 2，是第 1 步的前置产物）**

`fuse(transcript, frames) -> Timeline`，纯函数，确定性，可断言。
所有 provider 消费同一个 `Timeline`。

**第 3 步：持久化与恢复（解 P0-2 / P0-4 / C.3）**

`out/<content_hash>/` 内容寻址，启动时扫描重建索引，`running` 标记为
`interrupted` 并回收临时文件。内容寻址同时解决幂等。

**第 4 步：临时文件生命周期（解 P0-5）**

`try/finally` 或上下文管理器包裹整个 workdir，异常路径必清理。

**第 5 步：测试与评测（解 P0-3 / D）**

先单元测试纯函数（links / dedupe / collapse_builds / score / fuse），
再评测集，最后基准命令。

**第 6 步：失败分类与可观测性（解 P1-9 / P1-12）**

**第 7 步：UI 措辞与高级面板（解 P2）**

```
Summary mode: Local          ← 默认
Summary mode: AI             ← 配置了 API
Summary mode: Extractive     ← 无模型可用

高级详情：
  Ingestion   yt-dlp + Chrome session
  ASR         mlx-whisper (large-v3-turbo)
  OCR         macOS Vision
  Summarizer  mlx_lm / anthropic / none
```

**第 8 步：`ARCHITECTURE.md`（解 C.6）**

---

## F. 交给 Codex 的任务

适合交出去的是边界清晰、可测试、不需要产品判断的部分：

| 任务 | 依据 |
|---|---|
| Timeline fusion 纯函数 + 单元测试 | 输入输出明确，无歧义 |
| SummarizerProvider 协议 + 三个实现 | 接口定死后是机械工作 |
| 内容寻址存储与启动恢复 | 规格清楚 |
| workdir 上下文管理器 | 小而独立 |
| 纯函数单元测试套件 | 最适合 |
| `make bench` / `make eval` 与指标计算 | 公式已在 D.3 定义 |
| 失败分类映射表 | 需先人工枚举 yt-dlp 错误样本 |
| UI 措辞与高级面板 | 文案已给定 |

**不要交给 Codex：**

- 关键帧算法调参——需要人看图判断选得对不对
- 评测集 URL 选取——需要人判断内容类型是否有代表性
- `ARCHITECTURE.md`——这是你面试时要讲的东西，必须自己写

---

## G. 明确推迟

不进 v1，也不要讨论：

- TikTok / 小红书 / B站
- 账号、团队、权限
- 向量检索、语义搜索、知识图谱
- 分布式队列（Celery / Redis）——本地单机应用，asyncio 足够
- Docker / K8s
- 移动端
- 浏览器扩展与 tabCapture——已被 yt-dlp 路径证明不必要
- 视频理解模型（VLM 看关键帧）——先把 OCR + ASR 的融合做扎实

---

## 一句话总结

现在的代码是一个**结构正确但只在顺境验证过**的原型。v1 的全部工作是：
把免费路径补成真正可用（P0-1）、把状态补成可恢复（P0-2/4）、把清理补成
无条件（P0-5）、把质量补成可测量（P0-3 + D）。功能一个都不用加。
