"""Exercise the shipped JS removal handler with a minimal DOM/network boundary."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node required')
class RemovalUITests(unittest.TestCase):
    def test_confirmation_selection_and_partial_success(self):
        app = Path(__file__).resolve().parents[1] / 'clipmind/web/app.js'
        result = subprocess.run(['node', '-e', r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    value: '', hidden: false, classList: {add(){}, remove(){}, toggle(){}},
    addEventListener(name, fn){this[name] = fn}, showModal(){this.open = true},
    setAttribute(){},
  });
  return elements.get(id);
};
const context = vm.createContext({document: {getElementById: element, querySelectorAll: () => []},
  EventSource: class {}, fetch: () => new Promise(()=>{}), setTimeout, clearTimeout, console});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
// Stub rendering only; selection and removal are the actual production functions.
vm.runInContext('render = () => {}; refreshJobs = async () => {};', context);
const run = code => vm.runInContext(code, context);
(async () => {
  run(`state.selected.failed.add('one'); state.selected.failed.add('two');
       state.jobs.set('one', {id:'one'}); state.jobs.set('two', {id:'two'});`);
  let requests = 0;
  context.fetch = async (_url, options) => {
    requests++;
    assert.deepStrictEqual(JSON.parse(options.body).ids, ['one', 'two']);
    return {ok: true, json: async () => ({deleted:['one'], failed:[{id:'two',message:'busy'}]})};
  };
  let pending = run(`removeSelected('failed')`);
  assert.equal(requests, 0, 'must not delete before confirmation');
  element('remove-dialog').returnValue = 'cancel'; element('remove-dialog').close();
  await pending;
  assert.equal(requests, 0, 'cancel must not send request');
  pending = run(`removeSelected('failed')`);
  element('remove-dialog').returnValue = 'remove'; element('remove-dialog').close();
  await pending;
  assert.equal(requests, 1);
  assert.equal(run(`state.jobs.has('one')`), false);
  assert.equal(run(`state.jobs.has('two')`), true);
  assert.equal(run(`state.selected.failed.has('two')`), true);
  assert.match(element('removal-notice').textContent, /1 项未删除/);
  assert.equal(run('state.removing'), false);
  run(`state.selected.library.add('visible'); state.selected.library.add('hidden');
       managementBar('library', [{id:'visible'}]);`);
  assert.equal(run(`state.selected.library.has('hidden')`), false);
})().catch(error => {console.error(error); process.exitCode = 1;});
''', str(app)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
