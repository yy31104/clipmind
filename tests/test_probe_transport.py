"""Read budgets at the actual HTTP boundary, not at the metadata serializer."""
import asyncio
from contextlib import chdir
import gzip
import io
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from clipmind import fetch
from clipmind.config import Settings
from clipmind.probe_worker import BoundedHTTP, LimitedBody, NetworkBudget, ProbeRefused
from tests.test_probe import CountingMediaServer, has_yt_dlp


class BudgetIOTests(unittest.TestCase):
    def test_readinto_and_line_iteration_cannot_bypass_read_budget(self):
        for mode in ('read', 'readinto', 'lines'):
            with self.subTest(mode=mode):
                budget = NetworkBudget(16, 2)
                body = LimitedBody(io.BytesIO(b'x\n' * 100), budget)
                with self.assertRaises(ProbeRefused):
                    if mode == 'read':
                        body.read()
                    elif mode == 'readinto':
                        body.readinto(bytearray(100))
                    else:
                        list(body)
                self.assertEqual(budget.bytes_read, 16)
                self.assertEqual(budget.refusal, 'probe_budget_exceeded')

    def test_two_responses_share_one_body_budget(self):
        budget = NetworkBudget(16, 2)
        first = LimitedBody(io.BytesIO(b'a' * 10), budget)
        self.assertEqual(first.read(), b'a' * 10)
        second = LimitedBody(io.BytesIO(b'b' * 10), budget)
        with self.assertRaises(ProbeRefused):
            second.read()
        self.assertEqual(budget.bytes_read, 16)


class FixtureServer:
    def __init__(self, tls=None):
        self.paths = []
        self.headers = []
        self.chunked_bytes = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.paths.append(self.path)
                owner.headers.append(dict(self.headers))
                try:
                    if self.path == '/escaped':
                        self.send_response(302)
                        self.send_header('Location', '/media file%20caf\xe9.mp4')
                        self.end_headers()
                    elif self.path.startswith('/redirect'):
                        self.send_response(302)
                        self.send_header('Location', '/redirect' + str(len(owner.paths)))
                        self.send_header('Content-Length', '100000000')
                        self.end_headers()
                        # Never send or wait to drain a redirect body.
                    elif self.path == '/cross':
                        self.send_response(302)
                        self.send_header('Location', 'http://localhost:' + str(self.server.server_port) + '/media.mp4')
                        self.end_headers()
                    elif self.path in {'/chunked', '/error'}:
                        self.send_response(500 if self.path == '/error' else 200)
                        self.send_header('Content-Type', 'text/html')
                        self.send_header('Transfer-Encoding', 'chunked')
                        self.end_headers()
                        for index in range(4096):
                            chunk = (b'<html><body>' + b' ' * (4096 - 12)) if index == 0 else b' ' * 4096
                            self.wfile.write(b'1000\r\n' + chunk + b'\r\n')
                            self.wfile.flush()
                            owner.chunked_bytes += 4096
                        self.wfile.write(b'0\r\n\r\n')
                    elif self.path == '/gzip':
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/html')
                        self.send_header('Content-Encoding', 'gzip')
                        self.end_headers()
                        self.wfile.write(gzip.compress(b'x' * (8 * 1024 * 1024)))
                    else:
                        payload = (b'<html><head><title>Fixture video</title></head><body>'
                                   b'<video src="/media.mp4"></video></body></html>') if self.path == '/page' else b'\0' * 1024
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/html' if self.path == '/page' else 'video/mp4')
                        self.send_header('Content-Length', str(len(payload)))
                        self.send_header('Set-Cookie', 'new=server; Path=/')
                        self.end_headers()
                        self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        if tls:
            self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = ('https' if tls else 'http') + f'://127.0.0.1:{self.server.server_port}'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@unittest.skipUnless(has_yt_dlp, 'integration requires yt-dlp')
class TransportIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = Settings(cookie_sources=('-',), probe_max_bytes=64*1024,
                               probe_max_requests=3, probe_timeout=15)

    async def test_small_html_is_still_parsed_by_ytdlp(self):
        with FixtureServer() as server:
            result = await fetch.probe(server.url + '/page', config=self.config)
        self.assertEqual(result.status, 'reachable')
        self.assertIn('Fixture video', result.title)
        self.assertGreater(result.network_bytes, 0)

    async def test_legacy_runtime_or_direct_socket_cannot_bypass_transport(self):
        # Both operations run inside a real worker, at the extractor boundary.
        # Runtime helper code need not use ydl.urlopen (e.g. legacy PhantomJS).
        worker_file = Path(fetch.__file__).with_name('probe_worker.py')
        for operation in (
            "subprocess.run([sys.executable, '-c', 'print(123)'], check=True)",
            "socket.create_connection(('127.0.0.1', 9), timeout=1)",
        ):
            program = f'''
import importlib.util,sys,subprocess,socket,json
spec=importlib.util.spec_from_file_location('probe_worker', {str(worker_file)!r})
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
import yt_dlp
def extractor(self, *args, **kwargs):
    {operation}
    return {{'id':'should-not-succeed'}}
yt_dlp.YoutubeDL.extract_info=extractor
print(json.dumps(module.run(1024, 2, ['--ignore-config', '--no-config-locations', 'https://example.com/v'])))
'''
            with tempfile.TemporaryDirectory() as temp:
                code, out, err = await fetch._run_budgeted(
                    [os.sys.executable, '-c', program], 10, output_limit=1024*1024, cwd=Path(temp))
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out.splitlines()[-1])['failure_code'], 'probe_transport_unsupported')

    async def test_chunked_response_without_length_is_capped(self):
        with FixtureServer() as server:
            result = await fetch.probe(server.url + '/chunked', config=self.config)
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(result.failure_code, 'probe_budget_exceeded')
        self.assertEqual(result.network_bytes, self.config.probe_max_bytes)
        self.assertLess(server.chunked_bytes, 4096 * 4096)

    async def test_http_error_bodies_have_the_same_budget(self):
        # The error body is a real HTTP response, not a synthetic byte string.
        with FixtureServer() as server:
            budget = NetworkBudget(1024, 1)
            transport = BoundedHTTP(budget, 2)
            try:
                raw, body = transport.open(server.url + '/error')
                self.assertEqual(raw.code, 500)
                with self.assertRaises(ProbeRefused):
                    body.read()
            finally:
                transport.close()
        self.assertEqual(budget.bytes_read, 1024)

    async def test_redirect_hops_exhaust_shared_request_budget_without_body_reads(self):
        with FixtureServer() as server:
            result = await fetch.probe(server.url + '/redirect', config=self.config)
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(result.failure_code, 'probe_budget_exceeded')
        self.assertEqual(result.network_requests, 3)
        self.assertEqual(len(server.paths), 3)
        self.assertEqual(result.network_bytes, 0)

    async def test_compressed_responses_are_refused_before_decompression(self):
        with FixtureServer() as server:
            result = await fetch.probe(server.url + '/gzip', config=self.config)
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(result.failure_code, 'probe_transport_unsupported')
        self.assertEqual(result.network_bytes, 0)
        self.assertEqual(server.headers[0]['Accept-Encoding'], 'identity')

    async def test_cookie_file_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            cookie = Path(temp) / 'cookies.txt'
            original = b'# Netscape HTTP Cookie File\n127.0.0.1\tFALSE\t/\tFALSE\t0\texisting\tvalue\n'
            cookie.write_bytes(original)
            before = cookie.stat().st_mtime_ns
            with FixtureServer() as server:
                result = await fetch.probe(server.url + '/media.mp4', config=replace(
                    self.config, cookie_sources=(), cookie_file=str(cookie)))
            self.assertEqual(result.status, 'reachable')
            self.assertEqual(cookie.read_bytes(), original)
            self.assertEqual(cookie.stat().st_mtime_ns, before)
            self.assertIn('existing=value', server.headers[0].get('Cookie', ''))

    async def test_relative_workdir_and_cookie_paths_survive_child_cwd(self):
        with tempfile.TemporaryDirectory() as temp, chdir(temp):
            cookie = Path('cookies.txt')
            cookie.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
            with CountingMediaServer(b'\0' * 4096) as server:
                config = replace(self.config, cookie_sources=(), cookie_file=str(cookie))
                result = await fetch.probe(server.url, config=config)
                acquired = await fetch.fetch(server.url, Path('job'), config=config)
            self.assertEqual(result.status, 'reachable')
            self.assertEqual(acquired.media_path, Path('job/acquisition/source.mp4').resolve())
            self.assertTrue(acquired.media_path.is_file())
            self.assertFalse(Path('job/acquisition/job').exists())

    async def test_acquisition_config_cannot_write_absolute_sidecars(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_dir = root / 'config'
            (config_dir / 'yt-dlp').mkdir(parents=True)
            outside = root / 'outside'
            outside.mkdir()
            (config_dir / 'yt-dlp/config').write_text(
                '--write-info-json\n-o "infojson:' + str(outside / 'sidecar') + '"\n', encoding='utf-8')
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_dir)}):
                with CountingMediaServer(b'\0' * 4096) as server:
                    acquired = await fetch.fetch(server.url, root / 'job', config=self.config)
            self.assertTrue(acquired.media_path.is_file())
            self.assertEqual(list(outside.iterdir()), [])

    async def test_redirect_does_not_forward_origin_credentials(self):
        with FixtureServer() as server:
            transport = BoundedHTTP(NetworkBudget(1024, 3), 2)
            try:
                transport.open(server.url + '/cross', headers={'Authorization': 'secret', 'Cookie': 'secret=value'})
            finally:
                transport.close()
        self.assertEqual(server.headers[0]['Authorization'], 'secret')
        self.assertNotIn('Authorization', server.headers[1])
        self.assertNotIn('Cookie', server.headers[1])

    async def test_redirect_spaces_nonascii_and_existing_escapes_are_preserved(self):
        with FixtureServer() as server:
            result = await fetch.probe(server.url + '/escaped', config=self.config)
        self.assertEqual(result.status, 'reachable')
        self.assertIn('/media%20file%20caf%E9.mp4', server.paths)

    @unittest.skipUnless(shutil.which('openssl'), 'TLS fixture needs openssl')
    async def test_https_uses_the_same_boundary_and_verifies_certificates(self):
        with tempfile.TemporaryDirectory() as temp:
            cert, key = Path(temp) / 'cert.pem', Path(temp) / 'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', str(key), '-out', str(cert), '-days', '1',
                            '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            with FixtureServer(tls=context) as server:
                untrusted = await fetch.probe(server.url + '/page', config=self.config)
                self.assertEqual(untrusted.status, 'unknown')
                with patch.dict(os.environ, {'SSL_CERT_FILE': str(cert)}):
                    trusted = await fetch.probe(server.url + '/page', config=self.config)
                    too_large = await fetch.probe(server.url + '/chunked', config=self.config)
            self.assertEqual(trusted.status, 'reachable')
            self.assertEqual(too_large.failure_code, 'probe_budget_exceeded')


class SharedWorkerBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_cookie_rungs_cannot_reset_network_budget(self):
        calls = []
        async def worker(args, *a, **kw):
            calls.append((int(args[2]), int(args[3])))
            return 0, json.dumps({'metadata': None, 'error': 'connection reset',
                                  'network_bytes': 40, 'network_requests': 2}), ''
        with patch.object(fetch, '_run_budgeted', worker), patch.object(fetch.shutil, 'which', return_value='yt-dlp'):
            result = await fetch.probe('https://example.com/v', config=Settings(
                cookie_sources=('chrome', '-'), probe_max_bytes=100, probe_max_requests=4))
        self.assertEqual(calls, [(100, 4), (60, 2)])
        self.assertEqual(result.network_bytes, 80)
        self.assertEqual(result.network_requests, 4)

    async def test_crashed_worker_cannot_reset_unreported_usage_and_retry(self):
        calls = []
        async def worker(*args, **kwargs):
            calls.append(1)
            return 1, '', 'worker failed after reading'
        with patch.object(fetch, '_run_budgeted', worker), patch.object(fetch.shutil, 'which', return_value='yt-dlp'):
            result = await fetch.probe('https://example.com/v', config=Settings())
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(result.failure_code, 'probe_invalid_response')
        self.assertEqual(len(calls), 1)
