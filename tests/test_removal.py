import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from clipmind import server
from clipmind.jobs import Job, JobStore
from clipmind.sdk import PackLibrary
from tests.pack_fixture import make_complete_pack


class RemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'out'
        self.store = JobStore(self.root)

    def job(self, name, status='done'):
        if status == 'done':
            make_complete_pack(self.root, name=name, source_id=name)
        job = Job(name, '/original/video.mp4', 'Synthetic', status=status, result={'id': name})
        self.store.jobs[name] = job
        self.store.storage.save(name, job.record())
        if status == 'done':
            self.store.index.sync(name, self.store.workdir(name))
        return job

    def test_remove_is_recoverable_and_not_resurrected_or_searchable(self):
        self.job('first')
        self.job('second')
        self.assertIn('first', [hit['job_id'] for hit in self.store.search('vector')])
        before = {p.relative_to(self.root/'first'):p.read_bytes() for p in (self.root/'first').rglob('*') if p.is_file()}
        queue = self.store.subscribe()
        queue.put_nowait({'id':'first','status':'done'})
        self.store.remove('first')
        moved = next((self.root/'.trash').glob('*/first'))
        self.assertEqual(before, {p.relative_to(moved):p.read_bytes() for p in moved.rglob('*') if p.is_file()})
        self.assertEqual(queue.get_nowait(), {'type':'resync'})
        self.assertTrue(queue.empty())
        self.assertEqual(set(JobStore(self.root).jobs), {'second'})
        self.assertEqual([p.id for p in PackLibrary(self.root).list()], ['second'])
        self.assertNotIn('first', [hit['job_id'] for hit in self.store.search('vector')])
        self.assertTrue((self.root/'second'/'manifest.json').exists())
        moved.rename(self.root/'first')
        recovered = JobStore(self.root)
        self.assertEqual(set(recovered.jobs), {'first', 'second'})
        self.assertIn('first', [hit['job_id'] for hit in recovered.search('vector')])

    def test_failed_job_and_original_media(self):
        original = Path(self.temp.name)/'original.mp4'
        original.write_bytes(b'original')
        job = self.job('failed', 'error')
        job.url = str(original)
        self.store.remove('failed')
        self.assertEqual(original.read_bytes(), b'original')
        self.assertNotIn('failed', JobStore(self.root).jobs)

    def test_active_jobs_are_rejected(self):
        for status in ('running', 'queued'):
            self.job(status, status)
            with self.assertRaises(ValueError): self.store.remove(status)
            self.assertTrue(self.store.workdir(status).exists())

    def test_failed_move_keeps_job_and_can_be_retried(self):
        self.job('first')
        with patch.object(Path, 'rename', side_effect=PermissionError):
            with self.assertRaises(PermissionError): self.store.remove('first')
        self.assertIn('first', self.store.jobs)
        self.assertTrue(self.store.workdir('first').exists())
        self.assertIn('first', [hit['job_id'] for hit in self.store.index.search('vector')])
        self.assertTrue(self.store.search('vector'))
        self.store.remove('first')
        self.assertNotIn('first', JobStore(self.root).jobs)

    def test_paths_and_symlinks_are_rejected(self):
        for name in ('.', '..', '.uploads', '../outside', 'a/b', 'a\\b'):
            with self.assertRaises(ValueError): self.store.storage.move_to_trash(name)
        self.job('first')
        external = Path(self.temp.name)/'external'
        external.mkdir()
        try: (self.root/'.trash').symlink_to(external, target_is_directory=True)
        except OSError: self.skipTest('symlink unavailable')
        with self.assertRaises(ValueError): self.store.remove('first')
        self.assertTrue(self.store.workdir('first').exists())
        self.assertEqual(list(external.iterdir()), [])

    def test_bulk_api_partial_success_and_origin_guard(self):
        self.job('first', 'error')
        self.job('busy', 'running')
        def request(origin):
            return Request({'type':'http','scheme':'http','server':('localhost',8420),'path':'/api/jobs/delete','headers':[(b'host',b'localhost:8420'),(b'origin',origin.encode())]})
        with patch.object(server, 'store', self.store):
            with self.assertRaises(HTTPException):
                asyncio.run(server.delete_jobs(server.DeleteJobsBody(ids=['first']),request('https://other.example')))
            self.assertIn('first',self.store.jobs)
            result=asyncio.run(server.delete_jobs(server.DeleteJobsBody(ids=['first','first','busy','missing']),request('http://localhost:8420')))
        self.assertEqual(result['deleted'],['first'])
        self.assertEqual({r['id'] for r in result['failed']},{'busy','missing'})


if __name__ == '__main__': unittest.main()
