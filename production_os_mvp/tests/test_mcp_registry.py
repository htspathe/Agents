import json
import tempfile
import unittest
from pathlib import Path

import mcp_registry


class MCPRegistryTests(unittest.TestCase):
    def test_reads_server_names_without_exposing_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({
                "mcpServers": {
                    "agent-desktop": {
                        "command": "agent-desktop",
                        "args": ["--token", "do-not-print-this"],
                        "env": {"API_KEY": "do-not-print-this"},
                    },
                    "blender": {"url": "http://127.0.0.1:9876/mcp", "headers": {"Authorization": "secret"}},
                }
            }), encoding="utf-8")
            result = mcp_registry.scan([path])
            rendered = json.dumps(result)
            self.assertEqual(result["server_count"], 2)
            self.assertIn("agent-desktop", result["names"])
            self.assertIn("blender", result["names"])
            self.assertNotIn("do-not-print-this", rendered)
            self.assertNotIn("secret", rendered)
            self.assertNotIn("/mcp", rendered)


if __name__ == "__main__":
    unittest.main()
