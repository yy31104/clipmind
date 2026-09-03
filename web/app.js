const $ = (id) => document.getElementById(id);
const state = {
  jobs: new Map(),
  view: "home",
  mode: "inbox",
  current: null,
  health: null,
  kbInbox: false,
  showFailed: false,
  searchQuery: "",
  searchResults: [],
};

const clock = (seconds) => {
  const safe = Number(seconds) || 0;
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = Math.floor(safe % 60);
  return hours
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
};

const urlish = (value) => /^(?:\/?https?:\/\/|\/|file:\/\/)/i.test(value || "");
const esc = (value) => String(value ?? "").replace(
  /[&<>"]/g,
  (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[character]),
);

const dateLabel = (seconds) => {
  if (!seconds) return "";
  const at = new Date(seconds * 1000);
  return at.toDateString() === new Date().toDateString()
    ? at.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : at.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
};

const STAGE_LABEL = {
  queued: "排队中",
  fetching: "获取媒体",
  sampling: "低成本抽帧",
  preflight: "成本预检",
  analysing: "语音与画面识别",
  writing: "写入证据包",
  done: "完成",
  error: "失败",
  interrupted: "已中断",
};

const SOURCE_LABEL = {
  douyin: "抖音",
  youtube: "YouTube",
  local: "本地文件",
};
const SOURCE_ORDER = ["youtube", "douyin", "local"];

function supportedSourceLabels(items = []) {
  return [...items]
    .sort((left, right) => {
      const leftRank = SOURCE_ORDER.indexOf(left.platform);
      const rightRank = SOURCE_ORDER.indexOf(right.platform);
      return (leftRank < 0 ? 99 : leftRank) - (rightRank < 0 ? 99 : rightRank);
    })
    .map((item) => SOURCE_LABEL[item.platform] || item.platform)
    .filter(Boolean);
}

function markdown(source) {
  const inline = (text) => esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  const output = [];
  let list = null;
  let fence = null;
  const closeList = () => {
    if (list) output.push(`<ul>${list.join("")}</ul>`);
    list = null;
  };
  for (const raw of (source || "").split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (line.startsWith("```")) {
      if (fence === null) {
        closeList();
        fence = [];
      } else {
        output.push(`<pre>${esc(fence.join("\n"))}</pre>`);
        fence = null;
      }
      continue;
    }
    if (fence !== null) {
      fence.push(raw);
      continue;
    }
    if (/^#{1,6}\s/.test(line)) {
      closeList();
      const level = Math.min(line.match(/^#+/)[0].length + 1, 4);
      output.push(`<h${level}>${inline(line.replace(/^#+\s*/, ""))}</h${level}>`);
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      (list ||= []).push(`<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`);
      continue;
    }
    if (/^>\s?/.test(line)) {
      closeList();
      output.push(`<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`);
      continue;
    }
    if (!line.trim()) {
      closeList();
      continue;
    }
    closeList();
    output.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  if (fence !== null) output.push(`<pre>${esc(fence.join("\n"))}</pre>`);
  return output.join("\n");
}

function groupPacks(done) {
  const groups = new Map();
  for (const job of done) {
    const key = `${job.result?.platform || "source"}:${job.result?.id || job.id}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(job);
  }
  return [...groups.values()];
}

function render() {
  const jobs = [...state.jobs.values()].sort((a, b) => b.created_at - a.created_at);
  const active = jobs.filter((job) => ["queued", "running"].includes(job.status));
  const done = jobs.filter((job) => job.status === "done");
  const failed = jobs.filter((job) => ["error", "interrupted"].includes(job.status));
  const libraryMode = state.mode === "library";

  $("hero").hidden = libraryMode;
  $("active-section").hidden = libraryMode;
  $("failed-section").hidden = libraryMode;
  $("active-label").hidden = active.length === 0;
  $("active").innerHTML = active.map(jobCard).join("");

  const packs = groupPacks(done);
  $("library-label").hidden = false;
  $("library-label").textContent = libraryMode ? "Library" : "最近的 Evidence Packs";
  $("library").innerHTML = packs.map(libraryCard).join("");
  $("search-field").classList.toggle("prominent", libraryMode);

  const toggle = $("failed-toggle");
  toggle.hidden = failed.length === 0;
  toggle.textContent = `${failed.length} 个未完成任务`;
  toggle.setAttribute("aria-expanded", String(state.showFailed));
  toggle.classList.toggle("open", state.showFailed);
  $("failed").hidden = failed.length === 0 || !state.showFailed;
  $("failed").innerHTML = failed.map(jobCard).join("");

  $("loading").hidden = true;
  $("empty").hidden = jobs.length > 0;
  wireDynamicActions();
}

function wireDynamicActions() {
  for (const element of document.querySelectorAll("[data-open]")) {
    element.onclick = () => openDetail(element.dataset.open);
  }
  for (const element of document.querySelectorAll("[data-reprocess]")) {
    element.onclick = () => reprocess(element.dataset.reprocess, false);
  }
  for (const element of document.querySelectorAll("[data-force]")) {
    element.onclick = () => reprocess(element.dataset.force, true);
  }
}

function costEstimate(job) {
  const value = job.error_details;
  if (job.error_code !== "cost_limit_exceeded" || !value) return "";
  return `<dl class="cost-estimate">
    <div><dt>时长</dt><dd>${clock(value.duration_seconds)}</dd></div>
    <div><dt>视觉状态</dt><dd>约 ${value.estimated_canonical_states}</dd></div>
    <div><dt>OCR</dt><dd>约 ${value.estimated_ocr_seconds} 秒</dd></div>
    <div><dt>Evidence Pack</dt><dd>约 ${value.estimated_pack_mb} MB</dd></div>
  </dl>`;
}

function jobCard(job) {
  const percent = Math.round((job.progress || 0) * 100);
  const failed = ["error", "interrupted"].includes(job.status);
  const title = failed && urlish(job.title) ? "媒体无法处理" : job.title;
  const source = failed && urlish(job.title)
    ? `<div class="job-source">${esc(job.title.replace(/^\/+/, ""))}</div>`
    : "";
  const retry = job.error_code === "cost_limit_exceeded"
    ? `<button class="primary compact" data-force="${job.id}">仍然完整处理</button>`
    : `<button class="secondary compact" data-reprocess="${job.id}">重新处理</button>`;
  return `<article class="job ${failed ? "error" : job.status}">
    <div class="job-head">
      <div class="job-title">${esc(title)}</div>
      <div class="job-time">${failed ? esc(STAGE_LABEL[job.status]) : `${percent}% / ${job.elapsed}s`}</div>
    </div>
    ${source}
    <div class="job-note">${esc(STAGE_LABEL[job.stage] || job.stage)}${job.note ? ` / ${esc(job.note)}` : ""}</div>
    ${failed
      ? `<div class="job-error">${esc(job.error || "")}</div>
         ${costEstimate(job)}
         ${job.error_action ? `<div class="job-action">${esc(job.error_action)}</div>` : ""}
         ${retry}`
      : `<div class="bar" role="progressbar" aria-label="处理进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><i style="width:${percent}%"></i></div>`}
  </article>`;
}

function libraryCard(group) {
  const [current, ...superseded] = group;
  const modern = Array.isArray(current.result?.visual_preview);
  const frames = modern ? current.result.visual_preview : (current.result?.keyframes || []);
  const cover = frames.length
    ? `<img class="thumb" loading="lazy" src="${frameUrl(current.id, frames[Math.floor(frames.length / 2)], modern)}" alt="">`
    : `<div class="thumb empty-thumb">${esc((current.result?.platform || "video").toUpperCase())}</div>`;
  const metadata = [
    clock(current.result?.duration || 0),
    `${frames.length} 个预览画面`,
    dateLabel(current.finished_at || current.created_at),
  ].filter(Boolean).join(" / ");
  return `<button class="card" data-open="${current.id}">
    ${cover}
    <span class="card-body">
      <span class="card-title">${esc(current.title)}</span>
      <span class="card-meta">${esc(metadata)}</span>
      ${superseded.length ? `<span class="card-older">${superseded.length} 个旧版本</span>` : ""}
    </span>
  </button>`;
}

function frameUrl(jobId, frame, modern = true, collection = null) {
  const name = String(frame.file || "").split("/").pop();
  const directory = collection || (modern ? "visual_states/preview" : "keyframes");
  return `/api/jobs/${jobId}/${directory}/${encodeURIComponent(name)}`;
}

function unspokenBadge(frame) {
  const novel = frame.transcript_novelty_char_count;
  if (!Number.isFinite(novel) || novel < 40) return "";
  return `<span class="unspoken">${novel} 字仅在画面中</span>`;
}

async function openDetail(id) {
  const response = await fetch(`/api/jobs/${id}`);
  if (!response.ok) return;
  const job = await response.json();
  state.current = job;
  show("detail");

  const metadata = job.result || {};
  const modernFrames = Array.isArray(metadata.visual_preview);
  const frames = modernFrames ? metadata.visual_preview : (metadata.keyframes || []);
  $("d-title").textContent = job.title;
  $("d-meta").innerHTML = [
    metadata.platform ? esc(metadata.platform) : null,
    metadata.uploader ? esc(metadata.uploader) : null,
    metadata.duration ? clock(metadata.duration) : null,
    `${frames.length} 个预览画面`,
    metadata.url ? `<a href="${esc(metadata.url)}" target="_blank" rel="noopener">打开原视频</a>` : null,
  ].filter(Boolean).join("<span>/</span>");

  if (metadata.evidence_pack) {
    const complete = metadata.evidence_pack.completeness || {};
    const preflight = metadata.preflight || {};
    $("pane-summary").innerHTML = `<div class="pack-overview">
      <div><span>Schema</span><strong>${esc(metadata.evidence_pack.schema?.version || "")}</strong></div>
      <div><span>转写</span><strong>${esc(complete.transcript || "unknown")}</strong></div>
      <div><span>OCR</span><strong>${esc(complete.ocr || "unknown")}</strong></div>
      <div><span>视觉状态</span><strong>${esc(complete.visual_states || "unknown")}</strong></div>
    </div>
    <p>完整时间戳转写、OCR、视觉时间线和 canonical 画面已经写入稳定文件契约。</p>
    ${preflight.estimated_canonical_states !== undefined
      ? `<p class="hint">预检估算 ${preflight.estimated_canonical_states} 个状态，${preflight.estimated_pack_mb} MB。</p>`
      : ""}
    <div class="actions">
      <a class="secondary action" href="/api/jobs/${job.id}/evidence.md" download>下载 Markdown</a>
      <a class="secondary action" href="/api/jobs/${job.id}/evidence.zip" download>导出完整 ZIP</a>
      <button class="secondary action" id="copy-transcript">复制转写</button>
      <button class="secondary action" id="reprocess">重新处理</button>
      ${state.kbInbox ? `<button class="secondary action" id="send-kb">发送到知识库</button>` : ""}
    </div>
    <p id="handoff-status" class="hint"></p>`;
    $("reprocess").onclick = () => reprocess(job.id, false);
    $("copy-transcript").onclick = () => copyTranscript(job.transcript || []);
    if (state.kbInbox) $("send-kb").onclick = () => sendToKnowledgeBase(job.id);
  } else {
    $("pane-summary").innerHTML = markdown(job.note_markdown || "")
      + `<a class="secondary action" href="/api/jobs/${job.id}/note.md" download>下载旧版 Markdown</a>`;
  }

  $("pane-frames").innerHTML = frames.map((frame) => `
    <article class="frame">
      <div class="frame-cap"><span class="ts">${esc(frame.clock || clock(frame.timestamp))}</span>${unspokenBadge(frame)}</div>
      <img loading="lazy" src="${frameUrl(job.id, frame, modernFrames)}" alt="${esc(frame.clock || clock(frame.timestamp))} 的视觉证据">
      ${frame.text ? `<div class="frame-ocr">${esc(frame.text)}</div>` : ""}
    </article>`).join("") || `<p class="hint">没有提取到可预览的画面证据。</p>`;

  $("pane-transcript").innerHTML = (job.transcript || []).length
    ? job.transcript.map((segment) => `<div class="line"><span class="ts">${clock(segment.start)}</span><span>${esc(segment.text)}</span></div>`).join("")
    : `<p class="hint">这个媒体没有可转写的语音。${metadata.asr_error ? esc(` (${metadata.asr_error})`) : ""}</p>`;

  $("pane-timeline").innerHTML = timeline(job, frames, modernFrames);
  selectTab("summary");
}

function timeline(job, frames, modernFrames) {
  const visual = frames.map((frame) => ({
    kind: "visual",
    at: Number(frame.timestamp) || 0,
    frame,
  }));
  const speech = (job.transcript || []).map((segment) => ({
    kind: "speech",
    at: Number(segment.start) || 0,
    segment,
  }));
  const events = [...visual, ...speech].sort((a, b) => a.at - b.at || (a.kind === "visual" ? -1 : 1));
  if (!events.length) return `<p class="hint">没有时间线事件。</p>`;
  return `<div class="timeline">${events.map((event) => {
    if (event.kind === "speech") {
      return `<article class="timeline-row speech-row">
        <time>${clock(event.at)}</time><div><span class="kind">语音</span><p>${esc(event.segment.text)}</p></div>
      </article>`;
    }
    const frame = event.frame;
    return `<article class="timeline-row visual-row">
      <time>${clock(event.at)}</time><div>
        <span class="kind">画面</span>${unspokenBadge(frame)}
        <img loading="lazy" src="${frameUrl(job.id, frame, modernFrames)}" alt="${clock(event.at)} 的视觉证据">
        ${frame.text ? `<p>${esc(frame.text)}</p>` : ""}
      </div>
    </article>`;
  }).join("")}</div>`;
}

async function copyTranscript(segments) {
  const text = segments.map((segment) => `[${clock(segment.start)}] ${segment.text}`).join("\n");
  try {
    await navigator.clipboard.writeText(text);
    $("copy-transcript").textContent = "已复制";
  } catch (_error) {
    $("handoff-status").textContent = "浏览器没有授予剪贴板权限。";
  }
}

async function sendToKnowledgeBase(id) {
  const button = $("send-kb");
  const status = $("handoff-status");
  button.disabled = true;
  status.textContent = "正在复制";
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

async function reprocess(id, force) {
  try {
    const response = await fetch(`/api/jobs/${id}/reprocess`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: Boolean(force) }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "无法重新处理");
    state.jobs.set(result.id, result);
    setMode("inbox");
    render();
  } catch (error) {
    showError(error.message);
    setMode("inbox");
  }
}

function selectTab(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    const selected = tab.dataset.tab === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  }
  for (const pane of ["summary", "timeline", "frames", "transcript"]) {
    $(`pane-${pane}`).hidden = pane !== name;
  }
}

function show(view) {
  state.view = view;
  $("view-home").hidden = view !== "home";
  $("view-settings").hidden = view !== "settings";
  $("view-detail").hidden = view !== "detail";
  window.scrollTo(0, 0);
}

function setMode(mode) {
  state.mode = mode;
  for (const item of document.querySelectorAll("[data-mode]")) {
    const selected = item.dataset.mode === mode;
    item.classList.toggle("active", selected);
    item.setAttribute("aria-current", selected ? "page" : "false");
  }
  if (mode === "settings") {
    show("settings");
    renderSettings();
  } else {
    show("home");
    render();
    if (mode === "library") $("library-search").focus();
  }
}

function renderSettings() {
  const health = state.health || {};
  const checks = [
    ["yt-dlp", health.yt_dlp, "获取链接媒体"],
    ["FFmpeg", health.ffmpeg, "提取音频和画面"],
    [health.asr_provider || "ASR", health.asr, "转写语音"],
    [health.ocr_provider || "OCR", health.ocr, "识别画面文字"],
  ];
  $("settings-grid").innerHTML = checks.map(([name, ready, purpose]) => `
    <article class="setting-row">
      <div><strong>${esc(name)}</strong><span>${esc(purpose)}</span></div>
      <span class="status ${ready ? "ready" : "missing"}">${ready ? "就绪" : "不可用"}</span>
    </article>`).join("") + `
    <article class="setting-row wide">
      <div><strong>支持的来源</strong><span>${esc(supportedSourceLabels(health.supported_sources).join(", "))}</span></div>
    </article>
    <article class="setting-row wide">
      <div><strong>上传上限</strong><span>${esc(health.max_upload_mb || 0)} MB，每个文件</span></div>
    </article>`;
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = false;
}

async function analyze() {
  const text = $("input").value.trim();
  if (!text) return;
  $("analyze").disabled = true;
  $("error").hidden = true;
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "提交失败");
    for (const job of data.jobs) state.jobs.set(job.id, job);
    $("input").value = "";
    $("detected").textContent = data.reused
      ? `复用了 ${data.reused} 个 Evidence Pack`
      : data.skipped ? `跳过 ${data.skipped} 个处理中任务` : "已加入队列";
    $("detected").classList.remove("ready");
    render();
  } catch (error) {
    showError(error.message);
  } finally {
    $("analyze").disabled = false;
  }
}

async function uploadFiles(files) {
  if (!files.length) return;
  $("error").hidden = true;
  $("choose-file").disabled = true;
  let completed = 0;
  try {
    for (const file of files) {
      $("detected").textContent = `正在添加 ${file.name}`;
      const response = await fetch(`/api/uploads?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `无法添加 ${file.name}`);
      state.jobs.set(result.id, result);
      completed += 1;
    }
    $("detected").textContent = `已添加 ${completed} 个本地文件`;
    setMode("inbox");
  } catch (error) {
    showError(error.message);
  } finally {
    $("choose-file").disabled = false;
    $("file-input").value = "";
    render();
  }
}

let searchTimer = null;
async function searchEvidence() {
  const query = $("library-search").value.trim();
  state.searchQuery = query;
  if (!query) {
    state.searchResults = [];
    $("search-results").hidden = true;
    $("library").hidden = false;
    return;
  }
  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "搜索失败");
    if (query !== state.searchQuery) return;
    state.searchResults = data.results;
    renderSearchResults();
  } catch (error) {
    showError(error.message);
  }
}

function renderSearchResults() {
  const container = $("search-results");
  $("library").hidden = Boolean(state.searchQuery);
  container.hidden = !state.searchQuery;
  if (!state.searchResults.length) {
    container.innerHTML = `<div class="empty compact-empty"><strong>没有匹配的证据</strong><p>尝试标题、说过的话或画面中的文字。</p></div>`;
    return;
  }
  container.innerHTML = state.searchResults.map((result) => `
    <button class="search-result" data-open="${result.job_id}">
      <span class="search-title">${esc(result.title)}</span>
      <span class="search-platform">${esc(result.platform)}</span>
      ${result.hits.map((hit) => `<span class="search-hit"><time>${clock(hit.timestamp)}</time><span>${esc(hit.text)}</span></span>`).join("")}
    </button>`).join("");
  wireDynamicActions();
}

async function refreshJobs(attempt = 0) {
  try {
    const response = await fetch("/api/jobs");
    if (!response.ok) throw new Error(`snapshot failed: ${response.status}`);
    const data = await response.json();
    state.jobs.clear();
    for (const job of data.jobs) state.jobs.set(job.id, job);
    if (state.view === "home") render();
  } catch (_error) {
    if (attempt >= 3) {
      showError("无法同步任务状态，请刷新页面。");
      return;
    }
    setTimeout(() => refreshJobs(attempt + 1), 500 * 2 ** attempt);
  }
}

$("tabs").onclick = (event) => {
  if (event.target.dataset.tab) selectTab(event.target.dataset.tab);
};
$("failed-toggle").onclick = () => {
  state.showFailed = !state.showFailed;
  render();
};
$("back").onclick = () => setMode("library");
$("home-btn").onclick = () => setMode("inbox");
for (const item of document.querySelectorAll("[data-mode]")) {
  item.onclick = () => setMode(item.dataset.mode);
}

const URL_RE = /https?:\/\/[^\s<>"']+/g;
$("input").addEventListener("input", () => {
  const count = new Set($("input").value.match(URL_RE) || []).size;
  $("detected").textContent = count ? `识别到 ${count} 个链接` : "等待链接或文件";
  $("detected").classList.toggle("ready", count > 0);
});
$("analyze").onclick = analyze;
$("input").addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") analyze();
});
$("choose-file").onclick = () => $("file-input").click();
$("file-input").onchange = () => uploadFiles([...$("file-input").files]);

for (const eventName of ["dragenter", "dragover"]) {
  $("composer").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("composer").classList.add("dragging");
    $("drop-message").hidden = false;
  });
}
for (const eventName of ["dragleave", "drop"]) {
  $("composer").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("composer").classList.remove("dragging");
    $("drop-message").hidden = true;
  });
}
$("composer").addEventListener("drop", (event) => uploadFiles([...event.dataTransfer.files]));
$("library-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(searchEvidence, 180);
});

const events = new EventSource("/api/events");
events.onmessage = async (event) => {
  const job = JSON.parse(event.data);
  if (job.type === "hello" || job.type === "resync") {
    await refreshJobs();
    return;
  }
  if (!job.id) return;
  state.jobs.set(job.id, job);
  if (state.view === "home") render();
};

(async () => {
  try {
    const [jobsResponse, healthResponse] = await Promise.all([
      fetch("/api/jobs"),
      fetch("/api/health"),
    ]);
    if (!jobsResponse.ok || !healthResponse.ok) throw new Error("initial state unavailable");
    const jobs = await jobsResponse.json();
    state.health = await healthResponse.json();
    const sourceLabels = supportedSourceLabels(state.health.supported_sources);
    if (sourceLabels.length) $("source-list").textContent = sourceLabels.join(", ");
    for (const job of jobs.jobs) state.jobs.set(job.id, job);
    state.kbInbox = Boolean(state.health.knowledge_base_inbox);
    render();
  } catch (_error) {
    $("loading").hidden = true;
    showError("ClipMind 服务尚未准备好，请稍后刷新页面。");
  }
})();
