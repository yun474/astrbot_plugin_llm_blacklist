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

    def test_permanent_message_default_matches_code(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
        source = (root / "main.py").read_text(encoding="utf-8")

        default = schema["permanent_blocked_message"]["default"]
        self.assertNotIn("{remaining_time}", default)
        self.assertIn(f'DEFAULT_PERMANENT_BLOCKED_MESSAGE = "{default}"', source)


if __name__ == "__main__":
    unittest.main()
