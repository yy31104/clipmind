const $ = (id) => document.getElementById(id);
const state = { jobs: new Map(), view: "home", current: null, kbInbox: false,
                showFailed: false };

const clock = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
// A job that never got past ingestion still carries its URL as a title.
const urlish = (t) => /^\/?(?:https?:\/\/)?(?:www\.|v\.)?douyin\.com\//i.test(t || "")
  || /^\/?https?:\/\//i.test(t || "");

const dateLabel = (seconds) => {
  if (!seconds) return "";
  const at = new Date(seconds * 1000);
  return at.toDateString() === new Date().toDateString()
    ? at.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : at.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
};

const esc = (s) => (s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const STAGE_LABEL = {
  queued: "排队中", fetching: "获取视频", sampling: "抽帧",
  analysing: "语音转写 + 画面识别", summarising: "生成笔记", writing: "写入",
  done: "完成", error: "失败", interrupted: "已中断",
};

/* ── minimal markdown renderer for the note format we generate ── */
function markdown(src) {
  const inline = (t) =>
    esc(t)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[(\d{2}:\d{2})\]/g, '<span class="ts">$1</span>');

  const out = [];
  let list = null, fence = null;
  const closeList = () => { if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; } };

  for (const raw of (src || "").split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (line.startsWith("```")) {
      if (fence === null) { closeList(); fence = []; }
      else { out.push(`<pre>${esc(fence.join("\n"))}</pre>`); fence = null; }
      continue;
    }
    if (fence !== null) { fence.push(raw); continue; }

    const img = line.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
    if (img) { closeList(); out.push(`<img src="${esc(img[2])}" alt="${esc(img[1])}">`); continue; }
    if (/^#{1,6}\s/.test(line)) {
      closeList();
      const level = Math.min(line.match(/^#+/)[0].length + 1, 4);
      out.push(`<h${level}>${inline(line.replace(/^#+\s*/, ""))}</h${level}>`);
      continue;
    }
    if (/^[-*]\s+/.test(line)) { (list ||= []).push(`<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`); continue; }
    if (/^>\s?/.test(line)) { closeList(); out.push(`<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`); continue; }
    if (!line.trim()) { closeList(); continue; }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  if (fence !== null) out.push(`<pre>${esc(fence.join("\n"))}</pre>`);
  return out.join("\n");
}

/* ── rendering ── */
function render() {
  const jobs = [...state.jobs.values()].sort((a, b) => b.created_at - a.created_at);
  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const done = jobs.filter((j) => j.status === "done");
  const failed = jobs.filter((j) => j.status === "error" || j.status === "interrupted");

  $("active-label").hidden = active.length === 0;
  $("active").innerHTML = active.map(jobCard).join("");

  const packs = groupPacks(done);
  $("library-label").hidden = packs.length === 0;
  $("library").innerHTML = packs.map(libraryCard).join("");

  const toggle = $("failed-toggle");
  toggle.hidden = failed.length === 0;
  toggle.textContent = `${failed.length} 个链接获取失败`;
  toggle.setAttribute("aria-expanded", String(state.showFailed));
  toggle.classList.toggle("open", state.showFailed);
  $("failed").hidden = failed.length === 0 || !state.showFailed;
  $("failed").innerHTML = failed.map(jobCard).join("");

  $("empty").hidden = jobs.length > 0;

  for (const el of document.querySelectorAll("[data-open]")) {
    el.onclick = () => openDetail(el.dataset.open);
  }
  for (const el of document.querySelectorAll("[data-reprocess]")) {
    el.onclick = () => reprocess(el.dataset.reprocess);
  }
}

/* One video reprocessed three times is one entry, not three. Packs arrive
   newest-first, so the head of each group is the current one. */
function groupPacks(done) {
  const groups = new Map();
  for (const job of done) {
    const key = job.result?.id || job.id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(job);
  }
  return [...groups.values()];
}

function jobCard(j) {
  const pct = Math.round((j.progress || 0) * 100);
  const failed = j.status === "error" || j.status === "interrupted";
  const title = failed && urlish(j.title) ? "链接无法获取" : j.title;
  const source = failed && urlish(j.title)
    ? `<div class="job-source">${esc(j.title.replace(/^\/+/, ""))}</div>` : "";
  return `<div class="job ${failed ? "error" : j.status}">
    <div class="job-head">
      <div class="job-title">${esc(title)}</div>
      <div class="job-time">${failed ? STAGE_LABEL[j.status] : `${pct}% · ${j.elapsed}s`}</div>
    </div>
    ${source}
    <div class="job-note">${esc(STAGE_LABEL[j.stage] || j.stage)}${j.note ? " · " + esc(j.note) : ""}</div>
    ${failed
      ? `<div class="job-error">${esc(j.error || "")}</div>
         ${j.error_action ? `<div class="job-action">${esc(j.error_action)}</div>` : ""}
         <button class="download" data-reprocess="${j.id}">重新处理</button>`
      : `<div class="bar"><i style="width:${pct}%"></i></div>`}
  </div>`;
}

function libraryCard(group) {
  const [current, ...superseded] = group;
  const modern = Array.isArray(current.result?.visual_preview);
  const frames = modern ? current.result.visual_preview : (current.result?.keyframes || []);
  const cover = frames.length
    ? `<img class="thumb" loading="lazy" src="${frameUrl(current.id, frames[Math.floor(frames.length / 2)], modern)}" alt="">`
    : `<div class="thumb"></div>`;
  const meta = [
    clock(current.result?.duration || 0),
    `${frames.length} 个画面`,
    dateLabel(current.finished_at || current.created_at),
  ].filter(Boolean).join(" · ");
  return `<button class="card" data-open="${current.id}">
    ${cover}
    <div class="card-body">
      <div class="card-title">${esc(current.title)}</div>
      <div class="card-meta">${esc(meta)}</div>
      ${superseded.length ? `<div class="card-older">+${superseded.length} 个旧版本</div>` : ""}
    </div>
  </button>`;
}

function frameUrl(jobId, frame, modern) {
  const name = frame.file.split("/").pop();
  const collection = modern ? "visual_states/preview" : "keyframes";
  return `/api/jobs/${jobId}/${collection}/${encodeURIComponent(name)}`;
}

/* Text the speech never carried: this frame is the only place it exists.
   A count, not a verdict - the reader decides whether it matters. */
function unspokenBadge(frame) {
  const novel = frame.transcript_novelty_char_count;
  if (!Number.isFinite(novel) || novel < 40) return "";
  return `<span class="unspoken">${novel} 字未在语音中</span>`;
}

/* ── detail view ── */
async function openDetail(id) {
  const res = await fetch(`/api/jobs/${id}`);
  if (!res.ok) return;
  const job = await res.json();
  state.current = job;
  show("detail");

  const meta = job.result || {};
  const modernFrames = Array.isArray(meta.visual_preview);
  const frames = modernFrames ? meta.visual_preview : (meta.keyframes || []);
  $("d-title").textContent = job.title;
  $("d-meta").innerHTML = [
    meta.uploader ? esc(meta.uploader) : null,
    meta.duration ? clock(meta.duration) : null,
    `${frames.length} 预览画面`,
    meta.url ? `<a href="${esc(meta.url)}" target="_blank" rel="noopener">原视频 ↗</a>` : null,
    meta.summary_engine ? esc(meta.summary_engine) : null,
  ].filter(Boolean).join("<span>·</span>");

  if (meta.evidence_pack) {
    const complete = meta.evidence_pack.completeness || {};
    $("pane-summary").innerHTML = `<h2>Evidence Pack ${esc(meta.evidence_pack.schema?.version || "")}</h2>
      <p>完整时间戳转写、OCR、视觉时间线和 canonical 画面已经按稳定文件契约落盘。</p>
      <ul>
        <li>转写：${esc(complete.transcript || "unknown")}</li>
        <li>OCR：${esc(complete.ocr || "unknown")}</li>
        <li>视觉状态：${esc(complete.visual_states || "unknown")}</li>
      </ul>
      <a class="download" href="/api/jobs/${job.id}/evidence.md" download>下载 Evidence Markdown</a>
      <a class="download" href="/api/jobs/${job.id}/evidence.zip" download>下载完整 ZIP</a>
      <button class="download" id="reprocess">重新处理</button>
      ${state.kbInbox ? `<button class="download" id="send-kb">发送到知识库 Inbox</button><p id="handoff-status"></p>` : ""}`;
    $("reprocess").onclick = () => reprocess(job.id);
    if (state.kbInbox) $("send-kb").onclick = () => sendToKnowledgeBase(job.id);
  } else {
    $("pane-summary").innerHTML =
      markdown((job.note_markdown || "").split("## 关键帧")[0].replace(/^# .*$/m, "").replace(/^- (来源|作者|时长|获取方式|总结模型):.*$/gm, "")) +
      `<a class="download" href="/api/jobs/${job.id}/note.md" download>下载旧版 Markdown</a>`;
  }

  $("pane-frames").innerHTML = frames.map((f) => `
    <div class="frame">
      <div class="frame-cap"><span class="ts">${f.clock}</span>${unspokenBadge(f)}</div>
      <img loading="lazy" src="${frameUrl(job.id, f, modernFrames)}" alt="${f.clock}">
      ${f.text ? `<div class="frame-ocr">${esc(f.text)}</div>` : ""}
    </div>`).join("") || `<p class="hint">没有提取到可预览的画面证据。</p>`;

  $("pane-transcript").innerHTML = (job.transcript || []).length
    ? job.transcript.map((s) => `<div class="line"><span class="ts">${clock(s.start)}</span><span>${esc(s.text)}</span></div>`).join("")
    : `<p class="hint">这个视频没有可转写的语音。${meta.asr_error ? esc(" (" + meta.asr_error + ")") : ""}</p>`;

  selectTab("summary");
}

async function sendToKnowledgeBase(id) {
  const button = $("send-kb");
  const status = $("handoff-status");
  button.disabled = true;
  status.textContent = "正在复制…";
  try {
    const response = await fetch(`/api/jobs/${id}/handoff`, { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "发送失败");
    status.textContent = result.status === "already_present" ? "知识库中已存在。" : "已发送到知识库 Inbox。";
  } catch (error) {
    status.textContent = error.message;
    button.disabled = false;
  }
}

async function reprocess(id) {
  try {
    const response = await fetch(`/api/jobs/${id}/reprocess`, { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "无法重新处理");
    state.jobs.set(result.id, result);
    show("home");
    render();
  } catch (error) {
    $("error").textContent = error.message;
    $("error").hidden = false;
    show("home");
  }
}

function selectTab(name) {
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.tab === name);
  for (const p of ["summary", "frames", "transcript"]) $(`pane-${p}`).hidden = p !== name;
}

function show(view) {
  state.view = view;
  $("view-home").hidden = view !== "home";
  $("view-detail").hidden = view !== "detail";
  window.scrollTo(0, 0);
}

/* ── wiring ── */
$("tabs").onclick = (e) => { if (e.target.dataset.tab) selectTab(e.target.dataset.tab); };
$("failed-toggle").onclick = () => {
  state.showFailed = !state.showFailed;
  render();
};
$("back").onclick = () => show("home");
$("home-btn").onclick = () => show("home");

const URL_RE = /https?:\/\/(?:v\.douyin\.com\/[\w-]+|(?:www\.)?douyin\.com\/(?:video|note)\/\d+)/g;
$("input").addEventListener("input", () => {
  const n = new Set($("input").value.match(URL_RE) || []).size;
  const hint = $("detected");
  hint.textContent = n ? `识别到 ${n} 个链接` : "等待粘贴";
  hint.classList.toggle("ready", n > 0);
});

async function analyze() {
  const text = $("input").value.trim();
  if (!text) return;
  $("analyze").disabled = true;
  $("error").hidden = true;
  try {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "提交失败");
    for (const j of data.jobs) state.jobs.set(j.id, j);
    $("input").value = "";
    $("detected").textContent = data.reused
      ? `已复用 ${data.reused} 个 Evidence Pack${data.skipped ? `，跳过 ${data.skipped} 个处理中任务` : ""}`
      : data.skipped ? `跳过 ${data.skipped} 个处理中任务` : "等待粘贴";
    $("detected").classList.remove("ready");
    render();
  } catch (err) {
    $("error").textContent = err.message;
    $("error").hidden = false;
  } finally {
    $("analyze").disabled = false;
  }
}
$("analyze").onclick = analyze;
$("input").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") analyze();
});

/* ── live updates ── */
async function refreshJobs(attempt = 0) {
  // A resync that fails silently puts the page back in the stuck state the
  // resync exists to escape, so retry with backoff before giving up.
  try {
    const response = await fetch("/api/jobs");
    if (!response.ok) throw new Error(`snapshot failed: ${response.status}`);
    const data = await response.json();
    state.jobs.clear();
    for (const job of data.jobs) state.jobs.set(job.id, job);
    if (state.view === "home") render();
  } catch (error) {
    if (attempt >= 3) {
      $("error").textContent = "无法同步任务状态，请刷新页面。";
      $("error").hidden = false;
      return;
    }
    setTimeout(() => refreshJobs(attempt + 1), 500 * 2 ** attempt);
  }
}

const events = new EventSource("/api/events");
events.onmessage = async (e) => {
  const job = JSON.parse(e.data);
  if (job.type === "hello" || job.type === "resync") {
    await refreshJobs();
    return;
  }
  if (!job.id) return;
  state.jobs.set(job.id, job);
  if (state.view === "home") render();
};

(async () => {
  const [jobs, health] = await Promise.all([
    fetch("/api/jobs").then((r) => r.json()),
    fetch("/api/health").then((r) => r.json()),
  ]);
  for (const j of jobs.jobs) state.jobs.set(j.id, j);
  state.kbInbox = Boolean(health.knowledge_base_inbox);
  render();
  $("health").innerHTML = [
    ["yt-dlp", health.yt_dlp], ["ffmpeg", health.ffmpeg],
    ["OCR", health.ocr], ["语音", health.asr],
    [health.llm ? "AI 总结" : "AI 总结未配置", health.llm],
  ].map(([label, on]) => `<span class="chip ${on ? "on" : "off"}">${label}</span>`).join("");
})();
