import json
import unittest
from pathlib import Path


class PluginConfigTest(unittest.TestCase):
    def test_blocked_message_defaults_include_remaining_time(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertIs(schema["show_blocked_message"]["default"], True)
        self.assertIn(
            "{remaining_time}",
            schema["blocked_message_template"]["default"],
        )


if __name__ == "__main__":
    unittest.main()
