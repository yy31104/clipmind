from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipmind import cli
from clipmind.jobs import JobStore


class CLITests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_reuses_a_complete_pack_unless_reprocess_is_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            stdout = StringIO()
            with (
                patch.object(cli, "JobStore", return_value=test_store),
                patch.object(cli, "OUT_DIR", Path(tempdir) / "out"),
                patch.object(
                    test_store,
                    "reusable",
                    return_value=SimpleNamespace(id="cached-job"),
                ),
                patch.object(test_store, "submit") as submit,
                redirect_stdout(stdout),
            ):
                status = await cli.main("https://v.douyin.com/example")

        self.assertEqual(status, 0)
        submit.assert_not_called()
        self.assertIn("reused", stdout.getvalue())

    async def test_cli_uses_durable_job_store_and_reports_evidence_pack(self) -> None:
        async def fake_process(url, workdir, pools, report, **kwargs):
            report("writing", 0.9, "writing evidence")
            (workdir / "manifest.json").write_text(
                '{"schema":{"name":"clipmind-evidence-pack","version":"1.0.0"}}',
                encoding="utf-8",
            )
            (workdir / "evidence.md").write_text("# Evidence", encoding="utf-8")
            return {"title": "Done", "evidence_pack": {"manifest": "manifest.json"}}

        with tempfile.TemporaryDirectory() as tempdir:
            test_store = JobStore(Path(tempdir) / "out")
            stdout = StringIO()
            with (
                patch.object(cli, "JobStore", return_value=test_store),
                patch.object(cli, "OUT_DIR", Path(tempdir) / "out"),
                patch("clipmind.jobs.process", new=fake_process),
                redirect_stdout(stdout),
            ):
                status = await cli.main("https://v.douyin.com/example")

            job = next(iter(test_store.jobs.values()))
            record = json.loads(
                (test_store.workdir(job.id) / "job.json").read_text(encoding="utf-8")
            )["job"]

        self.assertEqual(status, 0)
        self.assertEqual(record["status"], "done")
        self.assertIn("evidence.md", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
