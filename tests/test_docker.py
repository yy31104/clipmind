"""The container image must not become a way to expose the unauthenticated server.

Inside the container the server listens on 0.0.0.0, which is what lets a
published port reach it at all. Whether that reaches only this machine or the
whole network is decided by how the port is published, so every documented
`docker run` must publish on the host's loopback address.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# A command may continue over several lines ending in a backslash.
DOCKER_RUN = re.compile(r"docker run(?:[^\n]*\\\n)*[^\n]*")
PUBLISH = re.compile(r"(?:-p|--publish)[ =](\S+)")


class DockerDocumentationTests(unittest.TestCase):
    def test_documented_docker_runs_publish_on_loopback_only(self) -> None:
        for document in ("README.md", "docs/INSTALL.md"):
            text = (ROOT / document).read_text(encoding="utf-8")
            commands = DOCKER_RUN.findall(text)
            self.assertTrue(commands, f"{document} no longer documents docker run")
            for command in commands:
                ports = PUBLISH.findall(command)
                self.assertTrue(ports, f"{document}: docker run publishes no port")
                for port in ports:
                    with self.subTest(document=document, port=port):
                        self.assertTrue(
                            port.startswith("127.0.0.1:"),
                            f"{document} publishes {port} beyond the host's loopback",
                        )


if __name__ == "__main__":
    unittest.main()
