import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pydantic_ai.workspaces import LocalWorkspaceBackend
from vespera_agent.bridge import Bridge


class BundledToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_finder_path_reaches_bundled_tools_in_child_commands(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "bin"
            tools.mkdir()
            executable = tools / "rg"
            executable.write_text('#!/bin/sh\nprintf "bundled-rg"\n')
            executable.chmod(0o755)
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}), \
                 patch("vespera_agent.bridge.sysconfig.get_path", return_value=str(root)), \
                 patch("vespera_agent.bridge.AgentCore"), \
                 patch("vespera_agent.bridge.Thread"), \
                 patch("vespera_agent.bridge.load_dotenv"):
                bridge = Bridge(root, root)
                try:
                    backend = LocalWorkspaceBackend(root)
                    result = await backend.run(["/bin/sh", "-c", "rg --version"])
                    self.assertEqual(result.exit_code, 0, result.stderr)
                    self.assertEqual(result.stdout, "bundled-rg")
                    self.assertEqual(os.environ["PATH"], f"{tools}:/usr/bin:/bin")
                finally:
                    bridge.loop.close()
