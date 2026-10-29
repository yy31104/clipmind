"""Fetching has no whole-job percentage; later stages keep measured progress."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from tests.test_result_reading import APP, HARNESS


@unittest.skipUnless(shutil.which('node'), 'Node required')
class FetchProgressUITests(unittest.TestCase):
    def run_app(self, body):
        result = subprocess.run(
            ['node', '-e', HARNESS.replace('BODY', body), str(APP)],
            capture_output=True, text=True, encoding='utf-8', timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_storage_warning_distinguishes_completed_evidence_from_unstarted_work(self):
        self.run_app(r'''
  const failed = run(`persistenceNotice({status: 'error', persistence_warning: {code: 'storage'}})`);
  assert.match(failed, /处理尚未完成/);
  assert.doesNotMatch(failed, /已生成的资料/);
  const done = run(`persistenceNotice({status: 'done', result: {}, persistence_warning: {code: 'storage'}})`);
  assert.match(done, /请先导出备份/);
''')

    def test_fetching_displays_real_transfer_note_without_fake_total_percentage(self):
        self.run_app(r'''
  for (const progress of [0, 0.1, 0.2]) {
    const html = run(`jobCard({id: 'waiting', status: 'running', stage: 'fetching',
      title: 'A long video', progress: ${progress}, elapsed: 164.3,
      note: '正在接收临时媒体：125.4 / 369.3 MB · 3.2 MB/s'})`);
    assert.match(html.replace(/<[^>]+>/g, ''), /获取中 \/ 164\.3s/);
    assert.match(html, /125\.4 \/ 369\.3 MB · 3\.2 MB\/s/);
    assert.match(html, /自动临时获取，处理后清理，无需手动下载/);
    assert.match(html, /role="progressbar"/);
    assert.match(html, /整体进度尚未知/);
    assert.doesNotMatch(html, /aria-valuenow=|style="width:|\d+%/);
  }
''')

    def test_later_stage_progress_remains_numeric_without_transfer_hint(self):
        self.run_app(r'''
  const html = run(`jobCard({id: 'ready', status: 'running', stage: 'sampling',
    title: 'Video', progress: 0.2, elapsed: 239, note: 'Sampling frames'})`);
  assert.match(html.replace(/<[^>]+>/g, ''), /20% \/ 239s/);
  assert.match(html, /aria-valuenow="20"/);
  assert.match(html, /style="width:20%"/);
  assert.doesNotMatch(html, /indeterminate|job-transfer-hint|获取中/);
''')

    def test_stored_fetch_failure_is_not_shown_as_still_transferring_and_notes_are_escaped(self):
        self.run_app(r'''
  const html = run(`jobCard({id: 'failed', status: 'error', stage: 'fetching',
    title: '<script>bad</script>', progress: 0, elapsed: 60,
    note: '<img src=x onerror=bad()>', error: 'Transfer failed'})`);
  assert.match(html, /失败/);
  assert.match(html, /Transfer failed/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;img src=x onerror=bad\(\)&gt;/);
  assert.doesNotMatch(html, /<script>|<img |progressbar|job-transfer-hint|获取中/);
''')

    def test_active_clock_ticks_without_events_requests_or_rebuilding_other_ui(self):
        self.run_app(r'''
  assert.equal(intervals.length, 1);
  assert.equal(intervals[0].delay, 1000);
  run(`let nowMs = 1700000020000; Date.now = () => nowMs;
    state.jobs.set('long-asr', {id: 'long-asr', status: 'running', stage: 'analysing',
      progress: 0.32, started_at: 1700000000, elapsed: 5});
    render = () => { throw new Error('Timer must not rebuild the page'); };
    fetch = () => { throw new Error('Timer must not request a snapshot'); };`);
  const label = {dataset: {activeJobTime: 'long-asr'}, textContent: ''};
  rows['[data-active-job-time]'] = [label];
  element('library').innerHTML = 'The existing library';
  element('input').value = 'An unfinished input';
  intervals[0].callback();
  assert.equal(label.textContent, '20s');
  run('nowMs += 1000');
  intervals[0].callback();
  assert.equal(label.textContent, '21s');
  assert.equal(run("state.jobs.get('long-asr').progress"), 0.32);
  assert.equal(run("state.jobs.get('long-asr').elapsed"), 5, 'Server snapshot stays untouched');
  assert.equal(element('library').innerHTML, 'The existing library');
  assert.equal(element('input').value, 'An unfinished input');
''')

    def test_terminal_elapsed_uses_server_value_and_no_longer_ticks(self):
        self.run_app(r'''
  run(`let nowMs = 1700000020000; Date.now = () => nowMs;
    state.jobs.set('finished', {id: 'finished', status: 'done', stage: 'done',
      title: 'Finished', started_at: 1700000000, elapsed: 12.7, progress: 1});`);
  const stale = {dataset: {activeJobTime: 'finished'}, textContent: '12.7s'};
  rows['[data-active-job-time]'] = [stale];
  const initial = run("jobCard(state.jobs.get('finished'))");
  assert.match(initial.replace(/<[^>]+>/g, ''), /100% \/ 12\.7s/);
  assert.doesNotMatch(initial, /data-active-job-time/);
  run('nowMs += 120000');
  intervals[0].callback();
  assert.equal(stale.textContent, '12.7s');
  assert.equal(run("jobElapsedSeconds(state.jobs.get('finished'))"), 12.7);
  for (const status of ['queued', 'error', 'interrupted']) {
    assert.equal(run(`jobElapsedSeconds({status: '${status}', started_at: 1700000000, elapsed: 12.7})`), 12.7);
  }
''')

    def test_missing_or_invalid_start_times_keep_snapshot_elapsed_compatibility(self):
        self.run_app(r'''
  const values = run(`[undefined, null, '1700000000', true, NaN, Infinity, -1, 0]
    .map((started_at) => jobElapsedSeconds({status: 'running', started_at, elapsed: 164.3}, 1700000020))`);
  assert.deepStrictEqual(Array.from(values), Array(8).fill(164.3));
  assert.equal(run("jobElapsedSeconds({status: 'running', elapsed: 164.3}, NaN)"), 164.3);
  assert.equal(run("jobElapsedSeconds({status: 'running', started_at: 1700000030}, 1700000020)"), 0);
  assert.equal(run("jobElapsedSeconds({status: 'running', elapsed: Infinity})"), 0);
''')


if __name__ == '__main__':
    unittest.main()
