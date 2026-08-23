import unittest

from blacklist_state import (
    BlacklistEntry,
    BlacklistState,
    format_remaining,
    render_blocked_message,
)


class BlacklistStateTest(unittest.TestCase):
    def test_records_are_isolated_by_conversation(self) -> None:
        state = BlacklistState()
        state.add("bot:GroupMessage:100", BlacklistEntry("1", "甲", 200.0))
        state.add("bot:GroupMessage:200", BlacklistEntry("1", "甲", 300.0))

        first = state.get("bot:GroupMessage:100", "1", 100.0)
        second = state.get("bot:GroupMessage:200", "1", 100.0)

        self.assertEqual(first.expires_at if first else None, 200.0)
        self.assertEqual(second.expires_at if second else None, 300.0)

    def test_readding_user_replaces_the_old_expiry(self) -> None:
        state = BlacklistState()
        state.add("dialog", BlacklistEntry("1", "甲", 200.0))
        state.add("dialog", BlacklistEntry("1", "甲", 500.0))

        entry = state.get("dialog", "1", 100.0)

        self.assertEqual(entry.expires_at if entry else None, 500.0)
        self.assertEqual(len(state.entries_for("dialog", 100.0)), 1)

    def test_remove_only_releases_user_from_selected_conversation(self) -> None:
        state = BlacklistState()
        state.add("dialog-a", BlacklistEntry("openid_1", "甲", 200.0))
        state.add("dialog-b", BlacklistEntry("openid_1", "甲", 300.0))

        removed = state.remove("dialog-a", "openid_1")

        self.assertEqual(removed.user_id if removed else None, "openid_1")
        self.assertIsNone(state.get("dialog-a", "openid_1", 100.0))
        self.assertIsNotNone(state.get("dialog-b", "openid_1", 100.0))

    def test_remove_unknown_user_does_not_change_state(self) -> None:
        state = BlacklistState()
        state.add("dialog", BlacklistEntry("1", "甲", 200.0))

        removed = state.remove("dialog", "2")

        self.assertIsNone(removed)
        self.assertIsNotNone(state.get("dialog", "1", 100.0))

    def test_loading_discards_expired_and_invalid_records(self) -> None:
        raw = {
            "dialog": {
                "active": {"user_name": "甲", "expires_at": 200.0},
                "expired": {"user_name": "乙", "expires_at": 99.0},
                "broken": {"user_name": "丙", "expires_at": "nope"},
            }
        }

        state = BlacklistState.from_dict(raw, now=100.0)

        self.assertIsNotNone(state.get("dialog", "active", 100.0))
        self.assertIsNone(state.get("dialog", "expired", 100.0))
        self.assertIsNone(state.get("dialog", "broken", 100.0))
        self.assertEqual(len(state.entries_for("dialog", 100.0)), 1)

    def test_remove_expired_removes_empty_conversations(self) -> None:
        state = BlacklistState()
        state.add("expired-dialog", BlacklistEntry("1", "甲", 100.0))
        state.add("active-dialog", BlacklistEntry("2", "乙", 200.0))

        changed = state.remove_expired(100.0)

        self.assertTrue(changed)
        self.assertNotIn("expired-dialog", state.to_dict())
        self.assertIn("active-dialog", state.to_dict())

    def test_serialized_state_round_trips(self) -> None:
        state = BlacklistState()
        state.add("dialog", BlacklistEntry("1", "甲", 200.0))

        restored = BlacklistState.from_dict(state.to_dict(), now=100.0)

        self.assertEqual(restored.to_dict(), state.to_dict())

    def test_remaining_time_is_rounded_up(self) -> None:
        self.assertEqual(format_remaining(0.1), "1秒")
        self.assertEqual(format_remaining(3661), "1小时1分钟1秒")
        self.assertEqual(format_remaining(90060), "1天1小时1分钟")

    def test_blocked_message_replaces_remaining_time_placeholder(self) -> None:
        message = render_blocked_message(
            "你还要等 {remaining_time}，{unknown} 保持原样。",
            61,
        )

        self.assertEqual(message, "你还要等 1分钟1秒，{unknown} 保持原样。")


if __name__ == "__main__":
    unittest.main()
