"""Search results lead to the evidence, and a screenshot is read in context.

Runs the shipped app.js in Node with a minimal DOM boundary, like the removal
UI tests: the helpers, rendering and navigation below are production code.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "clipmind/web/app.js"

HARNESS = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const classes = () => {
  const names = new Set();
  return {
    add: (name) => names.add(name), remove: (name) => names.delete(name),
    toggle: (name, on) => ((on ?? !names.has(name)) ? names.add(name) : names.delete(name)),
    contains: (name) => names.has(name),
  };
};
const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) elements.set(id, {
    id, value: '', hidden: false, innerHTML: '', textContent: '', dataset: {}, classList: classes(),
    addEventListener(name, fn) { this[name] = fn; }, showModal() { this.open = true; },
    close() { this.open = false; }, setAttribute() {}, focus() {},
  });
  return elements.get(id);
};
const rows = {};
const row = (at) => ({ dataset: { at: String(at) }, classList: classes(), scrollIntoView() { this.scrolled = true; } });
const context = vm.createContext({
  document: { getElementById: element, querySelectorAll: (selector) => rows[selector] || [] },
  window: { scrollY: 0, scrollTo(options) { this.scrolledTo = options; }, addEventListener() {}, matchMedia: () => ({ matches: false }) },
  navigator: {}, EventSource: class {},
  fetch: () => new Promise(() => {}), setTimeout, clearTimeout, console,
});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
vm.runInContext('render = () => {};', context);
const run = (code) => vm.runInContext(code, context);
const DETAIL = `{
  job: { id: 'job1', transcript: [
    { start: 200, end: 214, text: 'before' },
    { start: 220, end: 226, text: 'during' },
    { start: 240, end: 245, text: 'after' },
  ] },
  frames: [{ timestamp: 218, clock: '03:38', file: 'visual_states/preview/03-38.jpg', text: 'RAG' }],
  modernFrames: true,
  states: [
    { timestamp: 100, clock: '01:40', file: 'visual_states/all/01-40.jpg', text: 'old slide' },
    { timestamp: 218, clock: '03:38', file: 'visual_states/all/03-38.jpg', text: 'RAG\\nVector Database' },
  ],
}`;
(async () => {
BODY
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "Node required")
class ResultReadingTests(unittest.TestCase):
    def run_app(self, body: str) -> None:
        result = subprocess.run(
            ["node", "-e", HARNESS.replace("BODY", body), str(APP)],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_moment_helpers(self) -> None:
        self.run_app(r"""
  assert.equal(run('coveringIndex([0, 5, 10], 7)'), 1);
  assert.equal(run('coveringIndex([0, 5, 10], 10)'), 2);
  assert.equal(run('coveringIndex(["3", "5"], 1)'), 0, 'before everything: the first item');
  assert.equal(run('coveringIndex([], 1)'), -1);
  const near = run(`nearbySpeech([
    { start: 0, end: 4, text: 'a' }, { start: 20, end: 24, text: 'b' }, { start: 31, end: 33, text: 'c' },
  ], 25)`);
  assert.deepStrictEqual(Array.from(near, (segment) => segment.text), ['b', 'c']);
""")

    def test_each_search_hit_leads_to_its_moment(self) -> None:
        self.run_app(r"""
  run(`state.searchQuery = 'vector'; state.searchResults = [{ job_id: 'job1', title: 'Talk', platform: 'youtube', hits: [
    { kind: 'ocr', ref: 'visual-00003', timestamp: 218, text: 'Vector Database' },
    { kind: 'transcript', ref: 'transcript-00002', timestamp: null, text: 'vector search' },
  ] }];`);
  run('renderSearchResults()');
  const html = element('search-results').innerHTML;
  assert.match(html, /data-jump="job1" data-kind="ocr" data-at="218"/);
  assert.match(html, /data-jump="job1" data-kind="transcript" data-at=""/);
  const title = html.match(/<button class="search-result"[\s\S]*?<\/button>/)[0];
  assert.doesNotMatch(title, /search-hit/, 'a hit must not be nested inside the title button');
""")

    def test_a_screenshot_opens_with_its_text_and_nearby_speech(self) -> None:
        self.run_app(r"""
  run(`state.detail = ${DETAIL}; openFrameContext(222);`);
  assert.equal(element('frame-dialog').open, true);
  assert.equal(element('frame-dialog-time').textContent, '03:38');
  assert.equal(element('frame-dialog-image').src, '/api/jobs/job1/visual_states/all/03-38.jpg');
  assert.match(element('frame-dialog-ocr').innerHTML, /Vector Database/);
  const speech = element('frame-dialog-speech').innerHTML;
  assert.match(speech, /before/);
  assert.match(speech, /class="line current"><span class="ts">[^<]*<\/span><span>during/);
  assert.doesNotMatch(speech, /after/, 'speech outside the window stays out');
""")

    def test_a_text_hit_selects_the_screenshot_and_a_speech_hit_the_line(self) -> None:
        self.run_app(r"""
  run(`state.detail = ${DETAIL};`);
  const frames = [row(100), row(218)];
  const lines = [row(200), row(220), row(240)];
  rows['#pane-frames .frame[data-at]'] = frames;
  rows['#pane-transcript .line[data-at]'] = lines;

  run(`showEvidenceAt('ocr', 218)`);
  assert.equal(element('pane-frames').hidden, false);
  assert.equal(element('pane-transcript').hidden, true);
  assert.ok(frames[1].classList.contains('focus') && frames[1].scrolled);
  assert.ok(!frames[0].classList.contains('focus'));

  element('frame-dialog').open = false;
  run(`showEvidenceAt('transcript', 222)`);
  assert.equal(element('pane-transcript').hidden, false);
  assert.ok(lines[1].classList.contains('focus'));
  assert.equal(element('frame-dialog').open, true, 'the moment opens with its screenshot');
""")

    def test_opening_a_result_at_a_moment_and_its_actions(self) -> None:
        self.run_app(r"""
  const job = {
    id: 'job1', title: 'Talk', transcript: [{ start: 220, end: 226, text: 'during' }],
    result: {
      url: 'local:///talk.mp4', evidence_pack: { completeness: {}, schema: { version: '1.3.0' } },
      visual_preview: [{ timestamp: 218, clock: '03:38', file: 'visual_states/preview/03-38.jpg', text: 'RAG' }],
      visual_states: [{ timestamp: 218, clock: '03:38', file: 'visual_states/all/03-38.jpg', text: 'RAG' }],
    },
  };
  context.fetch = async () => ({ ok: true, json: async () => job });
  rows['#pane-transcript .line[data-at]'] = [row(220)];
  await run(`openDetail('job1', { kind: 'transcript', at: 221 })`);
  assert.match(element('pane-transcript').innerHTML, /class="line" data-at="220"/);
  assert.match(element('pane-frames').innerHTML, /data-context-at="218"/);
  assert.equal(element('frame-dialog').open, true);
  assert.equal(element('pane-summary').hidden, true);
  const summary = element('pane-summary').innerHTML;
  for (const label of ['复制全文转写', '导出笔记（Markdown）', '下载全部资料（含截图）', '开发者信息']) {
    assert.ok(summary.includes(label), label);
  }
  assert.ok(!summary.includes('打开原视频') && !element('d-meta').innerHTML.includes('打开原视频'),
    'a local file has no source page to open');
""")

    def test_only_titles_that_overflow_fade(self) -> None:
        self.run_app(r"""
  const short = { scrollHeight: 38, clientHeight: 38, classList: classes() };
  const long = { scrollHeight: 76, clientHeight: 38, classList: classes() };
  rows['.card-title'] = [short, long];
  run('markClampedTitles()');
  assert.ok(!short.classList.contains('is-clamped'), 'a title that fits must not fade');
  assert.ok(long.classList.contains('is-clamped'));
""")

    def test_back_to_top_appears_once_scrolled_and_returns_to_the_top(self) -> None:
        self.run_app(r"""
  context.window.scrollY = 0;
  run('updateBackToTop()');
  assert.equal(element('back-to-top').hidden, true);
  context.window.scrollY = 900;
  run('updateBackToTop()');
  assert.equal(element('back-to-top').hidden, false);
  element('back-to-top').onclick();
  assert.equal(context.window.scrolledTo.top, 0);
""")


if __name__ == "__main__":
    unittest.main()
