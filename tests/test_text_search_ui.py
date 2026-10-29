"""Omitted OCR search hits open readable text rather than an unrelated image."""
import shutil
import subprocess
import unittest

from tests.test_result_reading import APP, HARNESS


@unittest.skipUnless(shutil.which("node"), "Node required")
class TextSearchUITests(unittest.TestCase):
    def run_app(self, body):
        result = subprocess.run(
            ["node", "-e", HARNESS.replace("BODY", body), str(APP)],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_omitted_images_never_use_source_page_numbers_as_existing_frame_positions(self):
        self.run_app(r"""
  run(`state.jobs.set('reading', {result:{media_type:'image_collection', visual_states:[
    {timestamp:0, source_image_index:3, file:'visual_states/all/retained.png'}]}});`);
  const omitted = {kind:'ocr', ref:null, timestamp:null, visual_available:false,
    omitted_visual:true, source_visual_ref:'visual-00004', source_image_index:3};
  context.omitted = omitted;
  assert.equal(run(`searchHitPosition('reading', omitted)`), '');
  assert.equal(run(`searchHitLabel('reading', omitted)`), '第 4 张（仅文字）');
  assert.equal(run(`searchHitLabel('reading', {kind:'ocr',visual_available:false,timestamp:null})`), '画面文字（仅文字）');
  assert.equal(run(`searchHitPosition('reading', {kind:'ocr',timestamp:0,source_image_index:3,visual_available:true})`), 3);
  assert.equal(run(`searchHitPosition('video', {kind:'transcript',timestamp:12})`), 12);
""")

    def test_clicking_an_omitted_hit_opens_its_full_escaped_text_without_fake_context(self):
        self.run_app(r"""
  const fullText = 'Heading\n' + 'A long paragraph. '.repeat(150) + '\n<script>unsafe()</script>\nEND OF OCR';
  const job = {id:'reading',title:'Compact source',transcript:[{start:0,end:2,text:'unrelated speech'}],result:{
    media_type:'image_collection', audio_status:'not_acquired',
    evidence_pack:{schema:{version:'1.3.0'},completeness:{ocr:'complete'}},
    visual_states:[{timestamp:0,source_image_index:0,file:'visual_states/all/retained.png',text:'different page'}],
    visual_preview:[],
  }};
  context.fetch = async () => ({ok:true,json:async()=>job});
  context.fullText = fullText;
  run(`state.searchQuery='paragraph'; state.searchResults=[{job_id:'reading',title:'Compact source',platform:'douyin',hits:[{
    kind:'ocr',ref:null,timestamp:null,visual_available:false,omitted_visual:true,
    source_visual_ref:'visual-00004',source_image_index:3,text:fullText,
  }]}];`);
  const hitButton = {dataset:{jump:'reading',kind:'ocr',at:'',resultIndex:'0',hitIndex:'0'}};
  rows['[data-jump]'] = [hitButton];
  element('frame-dialog-body').style = {};
  element('frame-dialog-image').src = 'stale-image.png';
  element('frame-dialog-image').removeAttribute = function(name) { delete this[name]; };
  run('renderSearchResults()');
  assert.match(element('search-results').innerHTML, /data-result-index="0" data-hit-index="0"/);
  assert.match(element('search-results').innerHTML, /data-at=""/);
  await hitButton.onclick();
  assert.equal(element('frame-dialog').open, true);
  assert.equal(element('frame-dialog-time').textContent, '第 4 张（仅文字）');
  assert.equal(element('frame-dialog-image').hidden, true);
  assert.equal(element('frame-dialog-image').src, undefined);
  const text = element('frame-dialog-ocr').innerHTML;
  assert.match(text, /此精简包未附此页图片/);
  assert.ok(text.includes('A long paragraph. '.repeat(150)));
  assert.match(text, /END OF OCR/);
  assert.match(text, /&lt;script&gt;unsafe\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(text, /<script>/);
  assert.equal(element('frame-dialog-speech-section').hidden, true);
  assert.equal(element('frame-dialog-speech').innerHTML, '');
  assert.equal(element('frame-dialog-transcript').hidden, true);
  assert.equal(element('frame-dialog-body').style.gridTemplateColumns, 'minmax(0, 1fr)');
""")

    def test_missing_image_without_page_number_has_no_invented_timestamp(self):
        self.run_app(r"""
  const job = {id:'text',title:'Text only',transcript:[],result:{visual_states:[],visual_preview:[],
    evidence_pack:{schema:{version:'1.3.0'},completeness:{ocr:'complete'}}}};
  context.fetch = async () => ({ok:true,json:async()=>job});
  await run(`openDetail('text',{kind:'ocr',textOnly:{kind:'ocr',visual_available:false,
    omitted_visual:true,ref:null,timestamp:null,source_visual_ref:'visual-00012',text:'complete omitted text'}})`);
  assert.equal(element('frame-dialog-time').textContent, '画面文字（仅文字）');
  assert.doesNotMatch(element('frame-dialog-time').textContent, /00:00/);
  assert.match(element('frame-dialog-ocr').innerHTML, /complete omitted text/);
  assert.equal(element('frame-dialog-image').hidden, true);
""")

    def test_a_real_screenshot_restores_the_image_and_its_own_nearby_speech(self):
        self.run_app(r"""
  element('frame-dialog-body').style = {};
  run(`state.detail={job:{id:'video',result:{},transcript:[{start:0,end:2,text:'correct nearby speech'}]},
    frames:[{timestamp:0,file:'visual_states/preview/00-00.jpg',text:'actual screen text'}],
    states:[],modernFrames:true};
    openOmittedVisualText({kind:'ocr',visual_available:false,text:'omitted old text'});
    openFrameContext(0);`);
  assert.equal(element('frame-dialog-image').hidden, false);
  assert.equal(element('frame-dialog-image').src, '/api/jobs/video/visual_states/preview/00-00.jpg');
  assert.equal(element('frame-dialog-speech-section').hidden, false);
  assert.equal(element('frame-dialog-transcript').hidden, false);
  assert.equal(element('frame-dialog-body').style.gridTemplateColumns, '');
  assert.match(element('frame-dialog-speech').innerHTML, /correct nearby speech/);
  assert.match(element('frame-dialog-ocr').innerHTML, /actual screen text/);
  assert.doesNotMatch(element('frame-dialog-ocr').innerHTML, /omitted old text/);
""")


if __name__ == "__main__":
    unittest.main()
