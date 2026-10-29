"""Image order and missing audio remain explicit across the upload/UI boundary."""
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from clipmind import render, server
from clipmind.asr import Transcript
from clipmind.fetch import Media
from clipmind.jobs import JobStore
from clipmind.media import Frame
from tests.test_result_reading import APP, HARNESS


class ImageRenderTests(unittest.TestCase):
    def test_metadata_preserves_image_order_without_invented_clock_times(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            image = root / "visual_states" / "all" / "image-00003.jpg"
            image.parent.mkdir(parents=True)
            frame = Frame(2, 0.0, image, source_image_index=2)
            item = Media(image, {
                "id": "gallery", "_clipmind_media_type": "image_collection",
                "_clipmind_audio_status": "not_acquired", "_clipmind_time_basis": "image_order",
            })
            result = render.build_metadata(
                root, item, Transcript([]), visual_states=[frame], visual_preview=[frame],
                evidence_manifest={
                    "schema": {"version": "1.3.0"},
                    "completeness": {"transcript": "unavailable"},
                    "diagnostics": {"audio_status": "not_acquired", "time_basis": "image_order"},
                },
            )
            self.assertEqual(result["media_type"], "image_collection")
            self.assertEqual(result["audio_status"], "not_acquired")
            self.assertEqual(result["time_basis"], "image_order")
            for row in (result["visual_states"][0], result["visual_preview"][0]):
                self.assertEqual(row["source_image_index"], 2)
                self.assertFalse(row["timestamp_available"])
                self.assertEqual(row["clock"], "第 3 张")


class ImageUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_upload_enters_the_normal_durable_queue(self):
        data = io.BytesIO()
        Image.new("RGB", (12, 8), "white").save(data, format="PNG")
        payload = data.getvalue()

        class Request:
            async def stream(self):
                yield payload[:20]
                yield payload[20:]

        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            with patch.object(server, "store", test_store), patch.object(test_store, "_schedule"):
                uploaded = await server.upload(Request(), filename="../../Diagram.PNG")
            source = Path(uploaded["url"])
            self.assertTrue(source.is_relative_to(test_store.storage.root / ".uploads"))
            self.assertEqual(source.read_bytes(), payload)
            self.assertEqual(uploaded["options"]["uploaded_filename"], "Diagram.PNG")
            self.assertEqual(uploaded["status"], "queued")

    async def test_tiff_view_is_browser_readable_without_rewriting_saved_evidence(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "chart.tiff"
            Image.new("RGB", (12, 8), "blue").save(path)
            original = path.read_bytes()
            response = await server._image_response(path)
            self.assertEqual(response.media_type, "image/png")
            with Image.open(io.BytesIO(response.body)) as displayed:
                self.assertEqual(displayed.size, (12, 8))
                self.assertEqual(displayed.getpixel((0, 0)), (0, 0, 255))
            self.assertEqual(path.read_bytes(), original)


@unittest.skipUnless(shutil.which("node"), "Node required")
class ImageUITests(unittest.TestCase):
    def run_app(self, body):
        result = subprocess.run(
            ["node", "-e", HARNESS.replace("BODY", body), str(APP)],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_image_navigation_uses_order_even_when_all_timestamps_are_zero(self):
        self.run_app(r"""
  run(`state.detail = {
    job: { id: 'gallery', result: { media_type: 'image_collection', audio_status: 'not_acquired' },
      transcript: [{ start: 0, end: 3, text: 'unrelated speech must stay out' }] },
    modernFrames: true, states: [],
    frames: [0, 1, 2].map((index) => ({ timestamp: 0, source_image_index: index,
      file: 'visual_states/preview/image-' + index + '.jpg', text: 'Page ' + (index + 1) })),
  }; renderDetailFrames(); openFrameContext(1);`);
  assert.equal(element('frame-dialog-time').textContent, '第 2 张');
  assert.match(element('frame-dialog-image').src, /image-1\.jpg$/);
  assert.match(element('frame-dialog-ocr').innerHTML, /Page 2/);
  assert.match(element('frame-dialog-speech').innerHTML, /未提取配音/);
  assert.doesNotMatch(element('frame-dialog-speech').innerHTML, /unrelated speech/);
  assert.equal(element('frame-dialog-transcript').hidden, true);
  const frames = element('pane-frames').innerHTML;
  assert.match(frames, /第 1 张/); assert.match(frames, /第 3 张/);
  assert.doesNotMatch(frames, /00:00/);
  const sequence = run('timeline(state.detail.job, state.detail.frames, true)');
  assert.match(sequence, /第 2 张/);
  assert.doesNotMatch(sequence, /alt="00:00/);
""")

    def test_gallery_result_discloses_unfetched_audio_and_has_no_fake_duration(self):
        self.run_app(r"""
  const job = { id: 'gallery', title: 'Diagram collection', transcript: [], result: {
    media_type: 'image_collection', audio_status: 'not_acquired', time_basis: 'image_order', duration: 0,
    canonical_visual_state_count: 2,
    evidence_pack: { schema: { version: '1.3.0' }, completeness: { ocr: 'complete', transcript: 'unavailable' } },
    visual_states: [], visual_preview: [0, 1].map((index) => ({ timestamp: 0, source_image_index: index,
      file: 'visual_states/preview/image-' + index + '.jpg', text: 'Page ' + (index + 1) })),
  } };
  context.fetch = async () => ({ ok: true, json: async () => job });
  await run(`openDetail('gallery')`);
  assert.match(element('pane-summary').innerHTML, /图集：已提取图片和文字；未提取配音/);
  assert.match(element('pane-summary').innerHTML, /下载画面资料（图片与文字）/);
  assert.match(element('pane-transcript').innerHTML, /未提取配音/);
  assert.doesNotMatch(element('d-meta').innerHTML, /00:00/);
  const card = run(`libraryCard([${JSON.stringify(job)}])`);
  assert.match(card, /2 张图片/); assert.doesNotMatch(card, /00:00/);
""")

    def test_absent_audio_and_asr_failure_are_not_the_same_message(self):
        self.run_app(r"""
  assert.match(run(`emptyTranscriptMessage({ audio_status: 'absent' })`), /没有音轨/);
  assert.match(run(`emptyTranscriptMessage({ asr_error: 'no audio track' })`), /没有音轨/);
  assert.match(run(`emptyTranscriptMessage({ media_type: 'image_collection', audio_status: 'not_applicable' })`), /本地图片不包含配音/);
  const failure = run(`emptyTranscriptMessage({ audio_status: 'present', asr_error: 'CUDA unavailable' })`);
  assert.match(failure, /语音转写未完成/); assert.match(failure, /CUDA unavailable/);
  assert.doesNotMatch(failure, /没有可转写的语音/);
""")

    def test_image_search_uses_the_canonical_reference_to_jump_to_its_page(self):
        self.run_app(r"""
  run(`state.jobs.set('gallery', { result: { media_type: 'image_collection',
    visual_states: [0, 1].map((index) => ({ timestamp: 0, source_image_index: index })) } });`);
  assert.equal(run(`searchHitPosition('gallery', { kind: 'ocr', ref: 'visual-00002', timestamp: 0 })`), 1);
  assert.equal(run(`searchHitLabel('gallery', { kind: 'ocr', ref: 'visual-00002', timestamp: 0 })`), '第 2 张');
  assert.equal(run(`searchHitLabel('gallery', { kind: 'ocr', ref: 'missing', timestamp: 0 })`), '图片文字');
  assert.equal(run(`searchHitPosition('gallery', { kind: 'ocr', ref: 'missing', timestamp: 0 })`), '');
""")

    def test_audio_only_result_does_not_warn_about_missing_ocr_or_offer_nonexistent_frames(self):
        self.run_app(r"""
  const job = { id: 'audio', title: 'Audio recording', transcript: [{start:0,end:1,text:'hello'}], result: {
    media_type: 'audio', audio_status: 'present', speech_status: 'detected', visual_primary: false,
    visual_states: [], visual_preview: [], evidence_pack: { schema: { version:'1.3.0' },
      completeness:{ocr:'unavailable', transcript:'complete', visual_states:'complete'},
      diagnostics:{visual_status:'not_applicable', audio_status:'present'} },
  }};
  context.fetch = async () => ({ok:true, json:async()=>job});
  await run(`openDetail('audio')`);
  const summary = element('pane-summary').innerHTML;
  assert.match(summary, /此素材仅有音频，无需截图或 OCR/);
  assert.match(summary, /下载文字资料/);
  assert.doesNotMatch(summary, /画面文字未完整识别|请先重启到最新版本|OCR 修复工具/);
  assert.doesNotMatch(element('pane-frames').innerHTML, /原始采样画面仍可查看/);
  assert.equal(run(`isAudioOnly({evidence_pack:{diagnostics:{visual_status:'not_applicable'}}})`), true);
  assert.match(run(`emptyTranscriptMessage({media_type:'audio',speech_status:'not_detected'})`), /未检测到讲话/);
  assert.match(run(`emptyTranscriptMessage({media_type:'audio',asr_error:'CUDA unavailable'})`), /语音转写未完成/);
""")


if __name__ == "__main__":
    unittest.main()
