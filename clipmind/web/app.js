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
  selected: { library: new Set(), failed: new Set() },
  removing: false,
  detail: null,
};

function selectionControl(job, scope, label = "选择") {
  return `<label class="select-job"><input type="checkbox" data-select="${esc(job.id)}" data-scope="${scope}"
    ${state.selected[scope].has(job.id) ? "checked" : ""} ${state.removing ? "disabled" : ""}>
    ${esc(label)}</label>`;
}

function managementBar(scope, jobs) {
  const allowed = new Set(jobs.map((job) => job.id));
  for (const id of state.selected[scope]) {
    if (!allowed.has(id)) state.selected[scope].delete(id);
  }
  const count = state.selected[scope].size;
  return `<label class="select-job"><input type="checkbox" data-select-all="${scope}"
    ${jobs.length && count === jobs.length ? "checked" : ""} ${!jobs.length || state.removing ? "disabled" : ""}>
    全选${scope === "library" ? "当前列表（含旧版本）" : "未完成任务"}</label>
    <span>已选 ${count} 项</span>
    <button class="secondary compact danger" data-remove="${scope}" ${!count || state.removing ? "disabled" : ""}>删除所选</button>`;
}

function visiblePackGroups() {
  const groups = groupPacks([...state.jobs.values()].filter((job) => job.status === "done")
    .sort((a, b) => b.created_at - a.created_at));
  if (!state.searchQuery) return groups;
  const hits = new Set(state.searchResults.map((result) => result.job_id));
  return groups.filter((group) => group.some((job) => hits.has(job.id)));
}

async function removeSelected(scope) {
  const ids = [...state.selected[scope]];
  if (!ids.length || state.removing) return;
  const description = scope === "library" ? "证据包版本" : "未完成任务";
  const dialog = $("remove-dialog");
  $("remove-description").textContent = `将移出所选 ${ids.length} 个${description}，未勾选的项目不受影响。`;
  dialog.returnValue = "cancel";
  const confirmed = new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "remove"), { once: true }));
  dialog.showModal();
  if (!await confirmed) return;
  state.removing = true;
  render();
  const notice = $("removal-notice");
  try {
    const result = { deleted: [], failed: [] };
    for (let offset = 0; offset < ids.length; offset += 500) {
      const response = await fetch("/api/jobs/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: ids.slice(offset, offset + 500) }),
      });
      if (!response.ok) throw new Error("删除请求失败，部分项目可能已移出；请刷新确认当前状态。");
      const batch = await response.json();
      result.deleted.push(...batch.deleted);
      result.failed.push(...batch.failed);
    }
    for (const id of result.deleted) {
      state.jobs.delete(id);
      state.selected[scope].delete(id);
    }
    state.searchResults = state.searchResults.filter((hit) => !result.deleted.includes(hit.job_id));
    notice.textContent = `已移出 ${result.deleted.length} 项（文件保留在库目录 .trash 中）。`
      + (result.failed.length ? ` ${result.failed.length} 项未删除：${result.failed[0].message}` : "");
    await refreshJobs();
    if (state.searchQuery) await searchEvidence();
  } catch (error) {
    notice.textContent = error.message;
  } finally {
    notice.hidden = false;
    state.removing = false;
    render();
  }
}

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
  const failed = jobs.filter((job) => ["error", "interrupted"].includes(job.status));
  const libraryMode = state.mode === "library";

  $("hero").hidden = libraryMode;
  $("active-section").hidden = libraryMode;
  $("failed-section").hidden = libraryMode;
  $("active-label").hidden = active.length === 0;
  $("active").innerHTML = active.map(jobCard).join("");

  const packs = visiblePackGroups();
  $("library-label").hidden = false;
  $("library-label").textContent = libraryMode ? "Library" : "最近的 Evidence Packs";
  $("library").innerHTML = packs.map(libraryCard).join("");
  $("library-manage").innerHTML = managementBar("library", packs.flat());
  $("library").hidden = false;
  $("search-results").hidden = true;
  $("search-field").classList.toggle("prominent", libraryMode);

  const toggle = $("failed-toggle");
  toggle.hidden = failed.length === 0;
  toggle.textContent = `${failed.length} 个未完成任务`;
  toggle.setAttribute("aria-expanded", String(state.showFailed));
  toggle.classList.toggle("open", state.showFailed);
  $("failed").hidden = failed.length === 0 || !state.showFailed;
  $("failed").innerHTML = failed.map(jobCard).join("");
  $("failed-manage").hidden = !state.showFailed || !failed.length;
  $("failed-manage").innerHTML = managementBar("failed", failed);

  $("loading").hidden = true;
  $("empty").hidden = jobs.length > 0 || Boolean(state.searchQuery);
  if (state.searchQuery) renderSearchResults();
  markClampedTitles();
  wireDynamicActions();
}

function markClampedTitles() {
  // Only a title that is really cut off fades; a short one ends cleanly.
  for (const title of document.querySelectorAll(".card-title")) {
    title.classList.toggle("is-clamped", title.scrollHeight > title.clientHeight + 1);
  }
}

// Transcripts and libraries get long; the way back up should not be a scroll.
const BACK_TO_TOP_AFTER_PX = 600;

function updateBackToTop() {
  $("back-to-top").hidden = window.scrollY < BACK_TO_TOP_AFTER_PX;
}

function wireDynamicActions() {
  for (const element of document.querySelectorAll("[data-select]")) {
    element.onchange = () => {
      const selected = state.selected[element.dataset.scope];
      if (element.checked) selected.add(element.dataset.select);
      else selected.delete(element.dataset.select);
      render();
    };
  }
  for (const element of document.querySelectorAll("[data-select-all]")) {
    element.onchange = () => {
      const scope = element.dataset.selectAll;
      const jobs = scope === "library" ? visiblePackGroups().flat()
        : [...state.jobs.values()].filter((job) => ["error", "interrupted"].includes(job.status));
      state.selected[scope].clear();
      if (element.checked) for (const job of jobs) state.selected[scope].add(job.id);
      render();
    };
  }
  for (const element of document.querySelectorAll("[data-remove]")) {
    element.onclick = () => removeSelected(element.dataset.remove);
  }
  for (const element of document.querySelectorAll("[data-open]")) {
    element.onclick = () => openDetail(element.dataset.open);
  }
  for (const element of document.querySelectorAll("[data-jump]")) {
    const at = element.dataset.at === "" ? NaN : Number(element.dataset.at);
    element.onclick = () => openDetail(element.dataset.jump, { kind: element.dataset.kind, at });
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
    ${failed ? selectionControl(job, "failed", "选择此任务") : ""}
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
  return `<article class="pack-entry"><button class="card" data-open="${current.id}">
    ${cover}
    <span class="card-body">
      <span class="card-title">${esc(current.title)}</span>
      <span class="card-meta">${esc(metadata)}</span>
      ${superseded.length ? `<span class="card-older">${superseded.length} 个旧版本</span>` : ""}
    </span>
  </button><div class="pack-selection">${group.map((job, index) => selectionControl(job, "library",
    `${index ? "旧版本" : "当前版本"} · ${dateLabel(job.finished_at || job.created_at)}`)).join("")}</div></article>`;
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

function contentBadge(frame) {
  const labels = {
    caption: "字幕",
    code_ui: "代码 / UI",
    document_slide: "文档 / 幻灯片",
    textual: "画面文字",
  };
  const label = labels[frame.content_hint];
  return label ? `<span class="content-hint">${label}</span>` : "";
}

function frameBadges(frame) {
  return `${contentBadge(frame)}${unspokenBadge(frame)}`;
}

// Speech this close to a screenshot is shown beside it: long enough to cover
// what is being said while a slide is up, short enough to stay about it.
const SPEECH_WINDOW_SECONDS = 10;

function coveringIndex(times, at) {
  // What is on screen, or being said, at `at`: the last item started by then.
  if (!times.length) return -1;
  let found = 0;
  times.forEach((time, index) => {
    if (Number(time) <= at + 1e-6) found = index;
  });
  return found;
}

function nearbySpeech(segments, at, window = SPEECH_WINDOW_SECONDS) {
  return segments.filter((segment) => {
    const start = Number(segment.start) || 0;
    const end = Number(segment.end ?? segment.start) || start;
    return start <= at + window && end >= at - window;
  });
}

function stateAt(detail, at) {
  // Canonical visual states cover the whole video; previews are only a subset.
  const canonical = detail.states.length > 0;
  const items = canonical ? detail.states : detail.frames;
  const index = coveringIndex(items.map((item) => item.timestamp), at);
  return index < 0 ? null : { item: items[index], collection: canonical ? "visual_states/all" : null };
}

function focusRow(selector, at) {
  for (const row of document.querySelectorAll(".focus")) row.classList.remove("focus");
  const rows = [...document.querySelectorAll(selector)];
  const index = coveringIndex(rows.map((row) => row.dataset.at), at);
  if (index < 0) return null;
  rows[index].classList.add("focus");
  rows[index].scrollIntoView?.({ block: "center" });
  return rows[index];
}

function openFrameContext(at) {
  const detail = state.detail;
  const found = detail && stateAt(detail, at);
  if (!found) return;
  const { item, collection } = found;
  const label = item.clock || clock(item.timestamp);
  $("frame-dialog-time").textContent = label;
  $("frame-dialog-image").src = frameUrl(detail.job.id, item, detail.modernFrames, collection);
  $("frame-dialog-image").alt = `${label} 的画面`;
  $("frame-dialog-ocr").innerHTML = item.text
    ? `<p class="frame-dialog-text">${esc(item.text)}</p>`
    : `<p class="hint">这个画面没有识别到文字。</p>`;
  const speech = nearbySpeech(detail.job.transcript || [], at);
  const current = speech.findIndex((segment) =>
    Number(segment.start) <= at && at <= Number(segment.end ?? segment.start));
  $("frame-dialog-speech").innerHTML = speech.length
    ? speech.map((segment, index) => `<div class="line${index === current ? " current" : ""}"><span class="ts">${clock(segment.start)}</span><span>${esc(segment.text)}</span></div>`).join("")
    : `<p class="hint">前后 ${SPEECH_WINDOW_SECONDS} 秒内没有讲话。</p>`;
  const dialog = $("frame-dialog");
  $("frame-dialog-transcript").onclick = () => {
    dialog.close();
    selectTab("transcript");
    focusRow("#pane-transcript .line[data-at]", at);
  };
  if (!dialog.open) dialog.showModal();
}

function showEvidenceAt(kind, at) {
  if (kind === "ocr") {
    selectTab("frames");
    if (state.detail?.states.length) {
      state.detail.showAllFrames = true;
      renderDetailFrames();
    }
    focusRow("#pane-frames .frame[data-at]", at);
  } else {
    selectTab("transcript");
    focusRow("#pane-transcript .line[data-at]", at);
  }
  openFrameContext(at);
}

function renderDetailFrames() {
  const detail = state.detail;
  if (!detail) return;
  const all = detail.showAllFrames && detail.states.length > 0;
  const frames = all ? detail.states : detail.frames;
  const collection = all ? "visual_states/all" : null;
  const description = detail.states.length
    ? `当前显示 ${all ? `全部 ${detail.states.length}` : `精选 ${detail.frames.length}`} 个画面。`
    : "当前结果包没有独立的完整画面列表。";
  $("pane-frames").innerHTML = `<div class="frames-toolbar"><span>${description}</span>
    ${detail.states.length ? `<button class="secondary compact" id="toggle-all-frames">${all ? "只看精选" : `查看全部 ${detail.states.length} 个画面`}</button>` : ""}</div>`
    + (frames.map((frame) => `
    <article class="frame" data-at="${Number(frame.timestamp) || 0}">
      <div class="frame-cap"><span class="ts">${esc(frame.clock || clock(frame.timestamp))}</span>${frameBadges(frame)}</div>
      <button class="frame-open" data-context-at="${Number(frame.timestamp) || 0}" aria-label="查看 ${esc(frame.clock || clock(frame.timestamp))} 的画面文字和附近讲话">
        <img loading="lazy" src="${frameUrl(detail.job.id, frame, detail.modernFrames, collection)}" alt="${esc(frame.clock || clock(frame.timestamp))} 的视觉证据">
      </button>
      ${frame.text ? `<div class="frame-ocr">${esc(frame.text)}</div>` : ""}
    </article>`).join("") || `<p class="hint">没有提取到可查看的画面证据。</p>`);
  if ($("toggle-all-frames")) {
    $("toggle-all-frames").onclick = () => {
      detail.showAllFrames = !detail.showAllFrames;
      renderDetailFrames();
    };
  }
  for (const element of document.querySelectorAll("#pane-frames [data-context-at]")) {
    element.onclick = () => openFrameContext(Number(element.dataset.contextAt));
  }
}

async function openDetail(id, focus = null) {
  const response = await fetch(`/api/jobs/${id}`);
  if (!response.ok) return;
  const job = await response.json();
  state.current = job;
  show("detail");

  const metadata = job.result || {};
  const modernFrames = Array.isArray(metadata.visual_preview);
  const frames = modernFrames ? metadata.visual_preview : (metadata.keyframes || []);
  state.detail = {
    job,
    frames,
    modernFrames,
    states: modernFrames && Array.isArray(metadata.visual_states)
      ? metadata.visual_states.filter((item) => item && item.file)
      : [],
    showAllFrames: false,
  };
  const sourceUrl = /^https?:\/\//i.test(metadata.url || "") ? metadata.url : "";
  $("d-title").textContent = job.title;
  $("d-meta").innerHTML = [
    metadata.platform ? esc(metadata.platform) : null,
    metadata.uploader ? esc(metadata.uploader) : null,
    metadata.duration ? clock(metadata.duration) : null,
    `${frames.length} 个预览画面`,
    sourceUrl ? `<a href="${esc(sourceUrl)}" target="_blank" rel="noopener">打开原视频</a>` : null,
  ].filter(Boolean).join("<span>/</span>");

  if (metadata.evidence_pack) {
    const complete = metadata.evidence_pack.completeness || {};
    const preflight = metadata.preflight || {};
    const ocrWarning = complete.ocr && complete.ocr !== "complete"
      ? `<aside class="quality-warning"><strong>画面文字未完整识别</strong><p>截图仍然完整保留，但搜索、笔记和精选画面可能遗漏屏幕文字。请先重启到最新版本，再重新处理；也可以使用本地 OCR 修复工具，只重新识别已有截图。</p></aside>`
      : "";
    $("pane-summary").innerHTML = `${ocrWarning}<p class="summary-lead">已保存 ${(job.transcript || []).length} 段讲话、${state.detail.states.length || frames.length} 个完整画面和 ${frames.length} 张精选预览。点开截图，可以同时看到画面文字和附近的讲话。</p>
    <div class="actions">
      <button class="primary action" id="copy-transcript">复制全文转写</button>
      <a class="secondary action" href="/api/jobs/${job.id}/evidence.md" download>导出笔记（Markdown）</a>
      <a class="secondary action" href="/api/jobs/${job.id}/evidence.zip" download>下载全部资料（含截图）</a>
      ${sourceUrl ? `<a class="secondary action" href="${esc(sourceUrl)}" target="_blank" rel="noopener">打开原视频</a>` : ""}
    </div>
    <div class="actions quiet-actions">
      <button class="secondary action" id="reprocess">重新处理</button>
      ${state.kbInbox ? `<button class="secondary action" id="send-kb">发送到知识库</button>` : ""}
    </div>
    <p id="handoff-status" class="hint"></p>
    <details class="developer">
      <summary>开发者信息</summary>
      <div class="pack-overview">
        <div><span>Schema</span><strong>${esc(metadata.evidence_pack.schema?.version || "")}</strong></div>
        <div><span>转写</span><strong>${esc(complete.transcript || "unknown")}</strong></div>
        <div><span>OCR</span><strong>${esc(complete.ocr || "unknown")}</strong></div>
        <div><span>视觉状态</span><strong>${esc(complete.visual_states || "unknown")}</strong></div>
      </div>
      ${preflight.estimated_canonical_states !== undefined
        ? `<p class="hint">预检估算 ${preflight.estimated_canonical_states} 个状态，${preflight.estimated_pack_mb} MB。</p>`
        : ""}
      <ul class="developer-links">
        <li><a href="/api/packs/${job.id}" target="_blank" rel="noopener">Evidence Pack 摘要（JSON）</a></li>
        <li><a href="/api/packs/${job.id}/transcript" target="_blank" rel="noopener">转写（JSON）</a></li>
        <li><a href="/api/packs/${job.id}/ocr" target="_blank" rel="noopener">画面文字（JSON）</a></li>
        <li><a href="/api/packs/${job.id}/timeline" target="_blank" rel="noopener">视觉时间线（JSON）</a></li>
      </ul>
      <p class="hint">Agent 可以运行 <code>clipmind mcp</code>，通过 stdio MCP 读取整个本地库。</p>
    </details>`;
    $("reprocess").onclick = () => reprocess(job.id, false);
    $("copy-transcript").onclick = () => copyTranscript(job.transcript || []);
    if (state.kbInbox) $("send-kb").onclick = () => sendToKnowledgeBase(job.id);
  } else {
    $("pane-summary").innerHTML = markdown(job.note_markdown || "")
      + `<a class="secondary action" href="/api/jobs/${job.id}/note.md" download>下载旧版 Markdown</a>`;
  }

  renderDetailFrames();

  $("pane-transcript").innerHTML = (job.transcript || []).length
    ? job.transcript.map((segment) => `<div class="line" data-at="${Number(segment.start) || 0}"><span class="ts">${clock(segment.start)}</span><span>${esc(segment.text)}</span></div>`).join("")
    : `<p class="hint">这个媒体没有可转写的语音。${metadata.asr_error ? esc(` (${metadata.asr_error})`) : ""}</p>`;

  $("pane-timeline").innerHTML = timeline(job, frames, modernFrames);
  for (const element of document.querySelectorAll("#pane-timeline [data-context-at]")) {
    element.onclick = () => openFrameContext(Number(element.dataset.contextAt));
  }
  if (focus && Number.isFinite(focus.at)) showEvidenceAt(focus.kind, focus.at);
  else selectTab("summary");
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
        <span class="kind">画面</span>${frameBadges(frame)}
        <button class="frame-open" data-context-at="${event.at}" aria-label="查看 ${clock(event.at)} 的画面文字和附近讲话">
          <img loading="lazy" src="${frameUrl(job.id, frame, modernFrames)}" alt="${clock(event.at)} 的视觉证据">
        </button>
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
    [health.diarization_provider || "说话人分离", health.diarization, "可选的说话人标签"],
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
    render();
    return;
  }
  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "搜索失败");
    if (query !== state.searchQuery) return;
    state.searchResults = data.results;
    render();
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
  // Each hit is its own button: it leads to that moment, not just the video.
  container.innerHTML = state.searchResults.map((result) => `<article class="search-card">
    <button class="search-result" data-open="${esc(result.job_id)}">
      <span class="search-title">${esc(result.title)}</span>
      <span class="search-platform">${esc(result.platform)}</span>
    </button>
    ${result.hits.map((hit) => {
      const at = hit.timestamp === null || hit.timestamp === undefined ? "" : Number(hit.timestamp);
      return `<button class="search-hit" data-jump="${esc(result.job_id)}" data-kind="${esc(hit.kind)}" data-at="${at}">
        <time>${clock(hit.timestamp)}</time><span>${esc(hit.text)}</span><span class="hit-kind">${hit.kind === "ocr" ? "画面文字" : "讲话"}</span>
      </button>`;
    }).join("")}<div class="pack-selection">${(visiblePackGroups().find((group) => group.some((job) => job.id === result.job_id)) || [])
      .map((job, index) => selectionControl(job, "library", `${index ? "旧版本" : "当前版本"} · ${dateLabel(job.finished_at || job.created_at)}`)).join("")}</div></article>`).join("");
  wireDynamicActions();
}

async function refreshJobs(attempt = 0) {
  try {
    const response = await fetch("/api/jobs");
    if (!response.ok) throw new Error(`snapshot failed: ${response.status}`);
    const data = await response.json();
    state.jobs.clear();
    for (const job of data.jobs) state.jobs.set(job.id, job);
    state.searchResults = state.searchResults.filter((hit) => state.jobs.has(hit.job_id));
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
$("back-to-top").onclick = () => {
  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
};
window.addEventListener("scroll", updateBackToTop, { passive: true });
window.addEventListener("resize", markClampedTitles);
document.fonts?.ready?.then(markClampedTitles);
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
