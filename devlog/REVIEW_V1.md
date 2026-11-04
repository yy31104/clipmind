# ClipMind v1 技术审计与质量门槛

审计起点：1760 行，13 个模块，零测试，三个真实抖音链接跑通。
当前状态：v1 实现与发布工程已完成，69 项自动化测试和三条真实视频评测通过。
本文保留最初审计、恢复契约和验收标准；当前架构、评测与已知边界分别见
`docs/ARCHITECTURE.md`、`docs/REAL_WORLD_EVAL.md` 与 `docs/LIMITATIONS.md`。

---

## 0. ClipMind v1 产品契约（范围更正）

### Canonical purpose

ClipMind 是一个 local-first 的短视频**证据提取系统**。它负责忠实提取、对齐和
结构化来源证据；不负责判断用户应该学什么、什么最重要、或什么应进入长期知识库。
解释和知识管理决策属于下游 knowledge-base agent。

### Canonical free path

完整 canonical pipeline 必须在没有任何付费 API key 时成立：

```text
抖音分享文字 / URL
→ 本地认证媒体获取
→ 本地 ASR
→ 视觉状态提取
→ 本地 OCR
→ 确定性时间线对齐
→ Evidence Pack
```

这条路径不需要 Claude、OpenAI、Gemini 或其他付费模型。

### Semantic summarization

语义摘要**不是 v1 要求**。未来可以增加可选 summarizer，但它不得成为提取依赖；
缺失或失败不得降低证据完整性。canonical artifact 是 Evidence Pack，不是 AI 摘要。

### Canonical Evidence Pack

每个成功处理的视频至少应产生：

- source metadata、source URL 与可用的稳定来源身份；
- 完整、带时间戳的 transcript；
- 与时间戳/视觉状态关联的 OCR；
- visual timeline；
- 所有 materially distinct、stable 的视觉状态；
- manifest 与 schema version；
- 从结构化产物确定性生成的人类可读 evidence view。

源视频只是临时处理输入；成功提取后无需保留，除非显式 debug/user 选项要求。

### Visual extraction contract

canonical extraction 禁止固定每个视频的关键帧数量。保留数量必须由内容决定：

- 无实质画面变化的纯口播可以只有很少视觉状态；
- 四页白板或幻灯片应大致对应四个稳定状态；
- 密集代码或 UI 演示可以产生更多状态。

选择必须考虑 scene cut、结构变化、OCR/文字变化、转场与构建动画、画面稳定性和
可读性、重复/近重复状态。逐步构建中后来消失或被替换的信息不得丢弃；近重复构建
状态可以组成 build group。完整 evidence set 保存所有有用状态，单独的 compact
preview 可以选代表帧，但不得替代或截断 canonical evidence set。

### Responsibility boundary

ClipMind 负责本地获取、转写、视觉证据提取、OCR、时间对齐和可靠打包。
下游 knowledge-base agent 负责解释、按需摘要、与已有知识比较、判断相关性、知识
去重、创建学习任务/笔记，以及决定长期保留内容。

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
                                   deterministic timeline
                                              ↓
                                         Evidence Pack
                                              ↓
                                  下游知识库 agent（独立阶段）
```

三个正确的决策：

1. **按资源类型分池，而不是一个全局并发数。** `fetch=4 / asr=1 / ocr=2`
   反映了各阶段争抢的是不同资源（网络 / GPU / CPU）。单一信号量会让网络等待
   占住 GPU 名额。
2. **产品产出是 Evidence Pack，源媒体是实现细节。** 处理完即删。这个定位让
   系统不必去解决"下载器"要解决的问题。
3. **ASR 和 OCR 都已做到失败降级而非杀任务。** 这是失败隔离的正确方向，
   但 Evidence Pack 仍需显式记录证据缺失，不能把降级结果冒充完整输出。

### A.2 结构性缺陷

**已解决：持久化层与重启恢复（P0-B）。**

`out/<job-id>/job.json` 现在通过 write-through 持久化任务；启动恢复逐状态遵循
C.3 契约，异常路径清理已由自动化测试保护。

**已解决：确定性 timeline fusion。**

`visual_timeline.jsonl` 现在以稳定 ID 确定性关联 transcript、OCR 和视觉区间，
不依赖 summarizer；schema 与对齐行为均有自动化测试。

**缺陷 3：没有平台适配器边界。**

抖音的知识散落在两处：`links.py` 的正则、`fetch.py` 的 yt-dlp 调用。
没有 `DouyinAdapter` 接口。v1 只做抖音是对的，但边界要画出来——否则
抖音页面一变，改动会渗进核心。

**兼容层：当前 `summarize.py` 仍承担旧的人类可读输出。**

它可以在迁移期保留，但不再是 v1 canonical path，也不是 P0。Evidence Pack 不得
依赖该模块或任何 summarizer provider。

---

## B. 风险清单

### P0 — 阻断 v1

**✓ P0-A：行为回归安全网**

69 项自动化测试现已覆盖 URL、job lifecycle、并发、管线降级、SSE、重启恢复、
异常清理及 `dhash / hamming / dedupe / collapse_builds / score / select` 的
characterization contract。

**✓ P0-B：持久化、恢复与异常安全清理**

任务状态已 write-through 到 `out/<job-id>/job.json`；queued/running/terminal 恢复
遵循 C.3，已完成笔记可恢复，所有下载后异常都清理临时媒体且保留 final artifacts。

**✓ Extraction fidelity**

canonical visual states 已取消固定 10 张限制；1280px 证据/OCR、内容驱动 preview、
progressive build 分组和 2fps/4fps 覆盖探针均已完成并有真实视频报告。

**✓ Evidence Pack contract 与知识库交接**

versioned manifest、结构化 transcript/OCR/visual timeline、完整视觉状态集和
确定性 evidence view 已冻结；ZIP 与 manifest-last Inbox 复制提供单向交接。

**✓ P0-C hardening**

重复 URL 与已知 source ID 会复用完整 Pack，Reprocess 显式创建新任务；超过视频
并发上限的任务保持 queued，8-job benchmark 与测试都验证了 backpressure。

### P1 — 影响可信度

**✓ P1-6 SSE 断线后重新同步**

客户端收到首次连接或重连的 `hello` 后重新拉取 `/api/jobs`，因此断线期间的状态
由持久化索引补齐，不依赖事件逐条重放。

**已提升到 v1 core：固定关键帧预算不符合产品契约**

实测：

| 视频 | 时长 | 2fps 候选帧 | 最终关键帧 |
|---|---|---|---|
| VibeCoding 个人主页 | 227s | 454 | 10 |
| 面试问底层 | 64s | 128 | 10 |
| AI 产品经理面试 | 52s | 104 | 10 |

227 秒的视频和 52 秒的视频拿到同样的信息预算。修复方向不是按时长增加固定预算，
而是移除 canonical hard cap，按 materially distinct、stable 的内容状态决定数量。

**✓ P1-8 移除默认 Safari 降级**

实测 Safari rung 返回：

```
Operation not permitted: .../com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies
```

macOS TCC 保护 Safari 容器，除非终端被授予完全磁盘访问权限，这一级永远失败。
默认 cookie ladder 已改为 `chrome,-`，不再建议 Full Disk Access。

**✓ P1-9 失败原因可行动**

采集层现在区分失效/已删、私密、登录、cookie 不可用/过期和一般媒体失败，页面只
显示短消息与恢复动作；原始 yt-dlp 文本不进入 API 响应。

**✓ P1-10 cookie 授权边界已文档化并提供 cookie-file 路径**

`--cookies-from-browser chrome` 把**整个 Chrome cookie jar**（所有站点）交给
yt-dlp，而不只是 douyin.com。对一个本地工具可以接受，但必须在 README 里
明说。`docs/PRIVACY.md` 已记录边界，并提供只导出 Douyin cookie 的配置路径。

**P1-11 无任务取消，jobs 字典无界增长**

**✓ P1-12 关键阶段耗时进入 manifest/job result**

新 Pack 在 manifest/job result 中记录 acquisition、sampling、ASR、OCR、preview
和可选 summary 的墙钟耗时；失败记录包含稳定 error code 与恢复动作。

### P2 — 打磨

- `media.dedupe` 已改为失败保留并附安全诊断
- artifact route 只接受索引中的 job ID，并用 `Path.is_relative_to` 限定根目录
- `fetch._winning_source` 是模块级可变全局
- `Settings` 在 import 时从环境变量固化，测试需 monkeypatch 环境
- README 基准数字来自 `make eval` / `make bench` 的机器可读报告
- UI 主路径已使用 Evidence Pack / visual states；旧 note/keyframes 仅作兼容

---

## C. v1 验收标准

逐条可检验。全部满足才叫 v1。

### C.1 免费性（硬约束）

- [x] 当前媒体获取、ASR、OCR 与视觉处理不要求付费 API key
- [ ] 全新机器、**未设置任何 API key**，能生成完整 canonical Evidence Pack
- [x] 可选 summarizer 缺失或失败时，Evidence Pack 的完整性不受影响
- [x] canonical path 不发起任何 Claude/OpenAI/Gemini 等付费模型请求

### C.2 正确性与失败隔离

- [x] ASR / OCR 单阶段失败被明确记录，输出不得静默冒充完整证据
- [x] 采集失败按类型分类上报，而非透传 yt-dlp 原始报错
- [x] 任意下载后阶段抛异常，临时媒体与候选帧被清理且 final artifacts 被保留

### C.3 状态与重启

- [x] 重启后已有结果从 `out/` 重建索引
- [x] 重启时 `running` 的任务被标记为 `interrupted`，其临时文件被回收
- [x] 同一 URL 重复提交命中缓存，不重复下载与推理
- [x] 恢复行为逐状态符合下方 Restart recovery contract
- [x] 持久化顺序不变式成立：先写 `running`，再产生任何外部副作用

#### Restart recovery contract

这是 P0-B 已实现并由测试保护的契约。`interrupted` 是第五种状态；当前状态机和
UI 已覆盖 `queued / running / done / error / interrupted`。

**Persistence ordering invariant**

A job MUST be durably transitioned to `running` before ingestion begins or
before any temporary media or other processing side effects are created.

这条是整份契约成立的前提。若实际顺序是"先开始下载、再写 `running`"，那么
进程在两者之间崩溃时，磁盘状态是 `queued` 而副作用已经产生，
`queued → requeue` 就不再安全。

**`queued`**

- Represents work that has not begun.
- On application restart, restore the job and enqueue it exactly once.
- No temporary artifacts are expected for a valid queued job.
- This behavior does not depend on URL-level idempotency because the prior
  execution has not begun.

**`running`**

- A persisted running job found during startup is considered interrupted.
- It MUST NOT automatically resume or restart.
- Recover it as `interrupted`.
- Clean any temporary processing artifacts owned by that job.
- Preserve final/user-facing artifacts if any exist.
- The UI may offer an explicit manual retry later.

**`done`**

- Restore as a terminal successful job.
- Restore its persisted result/note into the library/index.
- Never automatically re-enter processing.

**`error`**

- Restore as a terminal failed job.
- Preserve its diagnostic metadata.
- Do not automatically retry.
- A later retry must be an explicit user action.

**Terminal-state invariant**

`done`, `error`, and `interrupted` jobs MUST NOT automatically transition back
into `queued` or `running` during startup recovery.

### C.4 Extraction fidelity（不以 DONE 为准）

- [x] transcript 完整保留时间戳，不因摘要或 preview 截断
- [x] canonical extraction 不使用固定 per-video frame cap
- [x] 评测范围内 materially distinct、stable、可读的视觉状态进入完整 evidence set
      （任意视频的普适保证仍属于 `docs/LIMITATIONS.md` 的明确边界）
- [x] progressive build 中后来消失或被替换的信息不丢失
- [x] preview 与完整 evidence set 分离，preview 不得截断 canonical artifacts
- [x] OCR 与 visual state/timestamp 可确定性关联
- [x] `duplicate_visual_state_rate` < 10%（真实评测集合计 3.0%）
- [x] 无语音幻灯片仍可依靠 OCR/视觉状态形成可用 Evidence Pack
      （生成式四页夹具真实运行 FFmpeg + Vision OCR，4/4 canonical 与 preview）

### C.5 工程

- [x] 修改视觉算法前，characterization tests 覆盖 `dhash / hamming / dedupe / collapse_builds / score / select`
- [x] 单元测试覆盖确定性 timeline alignment 与 Evidence Pack schema
- [x] `make bench` 一条命令产出可复现基准，README 数字由它生成
- [x] `make eval` 跑评测集并输出指标表
- [x] 每个任务落盘结构化 stage timing，失败可事后诊断

### C.6 可解释性（面试目标）

- [x] `ARCHITECTURE.md` 回答：每个阶段为何存在、失败时会怎样、什么并发、
      visual-state 算法为何保留某个状态——**不看源码即可解释**
- [x] Evidence Pack schema 与 ClipMind / 下游 agent 的责任边界可独立阅读

---

## D. 评测矩阵

评测集固定 URL 列表 + 期望行为，纳入版本控制。

### D.1 内容维度

| # | 类型 | 主要考察 | 期望 |
|---|---|---|---|
| 1 | 纯口播 | ASR 主导 | 转写完整；视觉状态可很少但不强凑数量 |
| 2 | 无语音幻灯片 | OCR 独立成立 | ASR 为空不算失败，Evidence Pack 仍有内容 |
| 3 | 录屏 / 编程教学 | 小字号 OCR、滚动状态 | 代码可读，信息消失前的状态被保存 |
| 4 | 大量烧录中文字幕 | ASR/OCR 冗余 | timeline 对齐但不复制冗余证据 |
| 5 | 画面文字极少 | 视觉信号弱 | 实质 scene/structure change 仍可形成状态 |
| 6 | 30s / 3min / 10min+ | 内容驱动伸缩 | 状态数由内容变化决定；耗时与候选量可解释 |
| 7 | 白板/PPT progressive build | 构建分组 | 被替换信息不丢；近重复状态可分组 |

### D.2 故障维度

| # | 注入 | 期望 |
|---|---|---|
| 8 | 同一 URL 提交两次 | 第二次命中缓存，不重复推理 |
| 9 | 一次提交 5–10 个 URL | 并发受限于资源预算，无 OOM |
| 10 | 失效 / 已删 / 私密链接 | 分类报错，非原始 yt-dlp 文本 |
| 11 | Chrome cookie 不可用 | 明确提示需要什么，非静默失败 |
| 12 | OCR 强制失败 | Evidence Pack 明确标注视觉证据不完整 |
| 13 | ASR 强制失败 | Evidence Pack 明确标注转写缺失，保留视觉证据 |
| 14 | yt-dlp 中途失败 | 临时文件被清理 |
| 15 | 运行中重启 | 任务标记 interrupted，临时文件回收 |

### D.3 指标

每次 `make eval` 产出：

```
ingestion_success_rate          成功采集 / 尝试
asr_realtime_factor             ASR 耗时 / 视频时长
ocr_runtime_per_frame           OCR 总耗时 / 帧数
end_to_end_latency              提交到 Evidence Pack 落盘
visual_state_count              canonical visual state 数量
preview_frame_count             UI preview 数量（不得限制 canonical set）
duplicate_visual_state_rate     近重复 visual state 的比例
visual_information_coverage     保留状态信息并集 / 全部有意义候选状态信息并集
batch_wall_clock_speedup        串行耗时和 / 实际墙钟
failure_recovery_rate           注入故障后的正确终态/降级比例
disk_cleanup_success            任务结束后仅保留 canonical/final artifacts
```

`visual_information_coverage` 是核心指标：它证明算法保留了来源信息，而不只是
“选出若干张图”。OCR 耗时也必须随状态数单独监控，尤其是长视频与密集 UI 演示。

---

## E. 实施顺序

P0-A 与 P0-B 已完成。后续严格按依赖顺序推进：

**第 1 步：视觉算法 characterization tests（下一刀）**

在改变行为前，为 `dhash / hamming / dedupe / collapse_builds / score / select`
建立安全网，明确当前对重复画面、渐进构建、无文字画面和 opening context 的行为。

**第 2 步：content-driven visual states**

移除 canonical 固定 10 张上限；检测 materially distinct、stable、可读状态，保存
完整集并为 progressive builds 建组。preview 是派生产物，不限制完整集。

**第 3 步：Evidence Pack 与 deterministic timeline**

定义 versioned manifest、source metadata、timestamped transcript、OCR、visual
timeline、`visual_states/all`、`visual_states/preview` 和 human-readable evidence view。

**第 4 步：知识库交接边界**

先采用确定性目录/文件契约或监听目录；不在 v1 引入复杂双向 API、MCP 或 RAG。

**第 5 步：P0-C idempotency 与资源控制**

稳定来源身份命中已完成 Evidence Pack，避免重复 ASR/OCR；验证 5–10 URL 批量提交
的并发上限、GPU/CPU/OCR 资源占用和失败隔离。

**第 6 步：真实视频 eval 与可复现 benchmark**

覆盖 D 的内容/故障矩阵，生成 extraction-quality、性能和清理指标，而不是手抄数字。

**第 7 步：失败分类、可观测性、UI 术语与 `ARCHITECTURE.md`**

UI 从“摘要/关键帧”迁移到 Evidence Pack / visual states，并完整解释各阶段、并发、
失败语义、状态选择原因和下游责任边界。

---

## F. 交给 Codex 的任务

适合交出去的是边界清晰、可测试、不需要产品判断的部分：

| 任务 | 依据 |
|---|---|
| 视觉纯函数 characterization tests | 当前输入输出可直接断言，不改变算法 |
| 已定义规则后的 visual-state implementation | 行为边界由测试和产品契约共同约束 |
| Timeline / Evidence Pack schema 与序列化 | 输入输出明确、适合契约测试 |
| 知识库目录交接与 schema 验证 | 边界窄且可在临时目录测试 |
| idempotency 与资源限制 | Evidence Pack 身份稳定后规格清楚 |
| `make bench` / `make eval` 与指标计算 | 公式已在 D.3 定义 |
| 失败分类映射表 | 需先人工枚举 yt-dlp 错误样本 |
| UI 术语迁移 | Evidence Pack 文案与字段确定后是机械工作 |

**不要交给 Codex：**

- 没有代表性 fixture/eval 的视觉阈值调参——需要人看图判断
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
- 必需的本地/付费 semantic summarizer——不属于 canonical v1 path
- 视频理解模型（VLM）——先把 ASR、OCR 与视觉状态的确定性证据做扎实
- 复杂双向知识库 API / MCP / RAG——v1 先用稳定文件契约交接

---

## 一句话总结

ClipMind 已完成可靠任务底座；v1 剩余主线是把“固定数量的摘要式关键帧输出”升级为
**免费、完整、可验证的 Evidence Pack**，再通过稳定文件契约交给下游知识库 agent。
