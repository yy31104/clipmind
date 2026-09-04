from __future__ import annotations

import unittest
from unittest.mock import patch

from clipmind import desktop, server


class DesktopLauncherTests(unittest.TestCase):
    def test_launcher_passes_the_asgi_app_object_to_uvicorn(self) -> None:
        with patch("uvicorn.run") as run:
            desktop.entrypoint(["--no-browser", "--host", "127.0.0.1", "--port", "9444"])

        run.assert_called_once_with(server.app, host="127.0.0.1", port=9444)

    def test_macos_launcher_makes_homebrew_dependencies_visible(self) -> None:
        with (
            patch.object(desktop.sys, "platform", "darwin"),
            patch.dict(desktop.os.environ, {"PATH": "/usr/bin:/bin"}, clear=True),
        ):
            desktop._prepare_dependency_path()
            entries = desktop.os.environ["PATH"].split(desktop.os.pathsep)

        self.assertEqual(entries[:2], ["/opt/homebrew/bin", "/usr/local/bin"])
        self.assertEqual(entries.count("/usr/bin"), 1)


if __name__ == "__main__":
    unittest.main()
