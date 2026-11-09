"""Exercise plugin handlers with lightweight AstrBot API doubles."""

import asyncio
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


def load_plugin():
    root = Path(__file__).resolve().parents[1]
    package = types.ModuleType("blacklist_test_plugin")
    package.__path__ = [str(root)]
    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    api.logger = Mock()
    event_api = types.ModuleType("astrbot.api.event")
    event_api.AstrMessageEvent = object
    event_api.filter = Mock()

    def decorator(*args, **kwargs):
        return lambda handler: handler

    for name in ("llm_tool", "on_llm_request", "command", "permission_type"):
        setattr(event_api.filter, name, decorator)
    components = types.ModuleType("astrbot.api.message_components")
    components.At = type("At", (), {})
    provider = types.ModuleType("astrbot.api.provider")
    provider.ProviderRequest = object
    star = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context):
            self.context = context

    star.Star = Star
    star.Context = object
    star.register = decorator
    modules = {
        "blacklist_test_plugin": package,
        "astrbot": types.ModuleType("astrbot"),
        "astrbot.api": api,
        "astrbot.api.event": event_api,
        "astrbot.api.message_components": components,
        "astrbot.api.provider": provider,
        "astrbot.api.star": star,
    }
    spec = importlib.util.spec_from_file_location(
        "blacklist_test_plugin.main", root / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


plugin_module = load_plugin()


class PluginToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = Mock()
        self.context.get_config.return_value = {"admins_id": ["admin"]}
        self.plugin = plugin_module.LLMBlacklistPlugin(self.context, {})
        self.plugin.get_kv_data = AsyncMock(return_value={})
        self.plugin.put_kv_data = AsyncMock()
        self.event = self.make_event()
        self.clock = patch.object(
            plugin_module.time, "time", return_value=1_700_000_000
        )
        self.clock.start()
        self.addCleanup(self.clock.stop)

    @staticmethod
    def make_event(
        user_id="sender", conversation="bot:GroupMessage:group", admin=False
    ):
        event = Mock()
        event.unified_msg_origin = conversation
        event.get_sender_id.return_value = user_id
        event.get_sender_name.return_value = "发送者昵称"
        event.get_self_id.return_value = "bot"
        event.is_admin.return_value = admin
        event.send = AsyncMock()
        return event

    async def test_omitted_openid_preserves_sender_behavior(self):
        result = await self.plugin.add_dialog_temporary_blacklist(self.event, 30)
        saved = self.plugin.put_kv_data.call_args.args[1]
        entry = saved[self.event.unified_msg_origin]["sender"]
        self.assertEqual(entry["user_name"], "发送者昵称")
        self.assertEqual(entry["expires_at"], 1_700_001_800)
        self.assertIn("30分钟", result)

    async def test_explicit_openid_blocks_target_only_in_this_conversation(self):
        result = await self.plugin.add_dialog_temporary_blacklist(
            self.event, 30, " target "
        )
        saved = self.plugin.put_kv_data.call_args.args[1]
        self.assertEqual(set(saved[self.event.unified_msg_origin]), {"target"})
        self.assertEqual(
            saved[self.event.unified_msg_origin]["target"]["user_name"], "target"
        )
        self.assertNotIn("发送者昵称", result)
        target = self.make_event("target")
        await self.plugin.block_blacklisted_request(target, None)
        target.stop_event.assert_called_once()
        target.send.assert_awaited_once()
        for event in (self.event, self.make_event("target", "bot:GroupMessage:other")):
            await self.plugin.block_blacklisted_request(event, None)
            event.stop_event.assert_not_called()

    async def test_admin_sender_can_target_non_admin(self):
        event = self.make_event("admin", admin=True)
        result = await self.plugin.add_dialog_temporary_blacklist(event, 1, "target")
        self.assertIn("已将用户", result)
        self.plugin.put_kv_data.assert_awaited_once()
        self.context.get_config.assert_called_with(event.unified_msg_origin)

    async def test_explicit_target_survives_reload_and_can_be_released(self):
        await self.plugin.add_dialog_temporary_blacklist(self.event, 30, "target")
        saved = self.plugin.put_kv_data.call_args.args[1]
        restored = plugin_module.LLMBlacklistPlugin(self.context, {})
        restored.get_kv_data = AsyncMock(return_value=saved)
        restored.put_kv_data = AsyncMock()
        target = self.make_event("target")
        await restored.block_blacklisted_request(target, None)
        target.stop_event.assert_called_once()

        admin = self.make_event("admin", admin=True)
        mention = plugin_module.At()
        mention.qq = "target"
        admin.get_messages.return_value = [mention]
        results = [result async for result in restored.release_user(admin)]
        self.assertEqual(len(results), 1)
        target.stop_event.reset_mock()
        await restored.block_blacklisted_request(target, None)
        target.stop_event.assert_not_called()
        self.assertEqual(restored.put_kv_data.call_args.args[1], {})

    async def test_admin_is_not_blocked_by_existing_record(self):
        await self.plugin.add_dialog_temporary_blacklist(self.event, 30, "target")
        target = self.make_event("target", admin=True)
        await self.plugin.block_blacklisted_request(target, None)
        target.stop_event.assert_not_called()

    async def test_failed_add_preserves_previous_expiry(self):
        await self.plugin.add_dialog_temporary_blacklist(self.event, 30, "target")
        before = self.plugin._state.to_dict()
        self.plugin.put_kv_data.side_effect = OSError("storage unavailable")
        with self.assertRaises(OSError):
            await self.plugin.add_dialog_temporary_blacklist(self.event, 1, "target")
        self.assertEqual(self.plugin._state.to_dict(), before)

    async def test_cancelled_save_does_not_add_target(self):
        self.plugin.put_kv_data.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.plugin.add_dialog_temporary_blacklist(self.event, 30, "target")
        self.assertEqual(self.plugin._state.to_dict(), {})

    async def test_failed_release_keeps_target_blocked(self):
        await self.plugin.add_dialog_temporary_blacklist(self.event, 30, "target")
        self.plugin.put_kv_data.side_effect = OSError("storage unavailable")
        admin = self.make_event("admin", admin=True)
        mention = plugin_module.At()
        mention.qq = "target"
        admin.get_messages.return_value = [mention]
        with self.assertRaises(OSError):
            _ = [result async for result in self.plugin.release_user(admin)]
        target = self.make_event("target")
        await self.plugin.block_blacklisted_request(target, None)
        target.stop_event.assert_called_once()

    async def test_failed_initial_normalization_can_retry(self):
        self.plugin.get_kv_data.return_value = {"expired": {}}
        self.plugin.put_kv_data.side_effect = OSError("storage unavailable")
        with self.assertRaises(OSError):
            await self.plugin.initialize()
        self.assertFalse(self.plugin._state_loaded)
        self.plugin.put_kv_data.side_effect = None
        await self.plugin.initialize()
        self.assertTrue(self.plugin._state_loaded)
        self.assertEqual(self.plugin.get_kv_data.await_count, 2)

    async def test_large_lists_are_truncated_without_removing_records(self):
        self.plugin._state_loaded = True
        for index in range(100):
            self.plugin._state.add(
                self.event.unified_msg_origin,
                plugin_module.BlacklistEntry(str(index), "昵称" * 32, 1_700_001_800),
            )
        self.plugin._state.add(
            "bot:GroupMessage:other",
            plugin_module.BlacklistEntry("other", "其他人", 1_700_001_800),
        )
        before = self.plugin._state.to_dict()
        self.event.plain_result.side_effect = lambda message: message
        for handler, total in (
            (self.plugin.list_dialog_blacklist, 100),
            (self.plugin.list_global_blacklist, 101),
        ):
            with self.subTest(handler=handler.__name__):
                results = [result async for result in handler(self.event)]
                self.assertEqual(len(results), 1)
                message = results[0]
                self.assertLessEqual(len(message), plugin_module.MAX_LIST_LENGTH)
                shown = len(re.findall(r"^\d+\. ", message, re.MULTILINE))
                self.assertGreater(shown, 0)
                self.assertIn(f"另有 {total - shown} 条未展示", message)
        self.assertEqual(self.plugin._state.to_dict(), before)
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_short_list_is_not_truncated(self):
        await self.plugin.add_dialog_temporary_blacklist(self.event, 30, "target")
        self.event.plain_result.side_effect = lambda message: message
        results = [
            result async for result in self.plugin.list_dialog_blacklist(self.event)
        ]
        self.assertIn("1. target（target）", results[0])
        self.assertNotIn("截断", results[0])

    async def test_admin_targets_are_protected(self):
        for event, target in (
            (self.event, "admin"),
            (self.make_event("admin", admin=True), None),
            (self.make_event("role-admin", admin=True), "role-admin"),
        ):
            with self.subTest(target=target):
                result = await self.plugin.add_dialog_temporary_blacklist(
                    event, 1, target
                )
                self.assertIn("管理员", result)
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_invalid_targets_do_not_fall_back_to_sender(self):
        for target in ("", " ", 123, True, "<@target>", "@target", "a b", "all", "bot"):
            with self.subTest(target=target):
                result = await self.plugin.add_dialog_temporary_blacklist(
                    self.event, 1, target
                )
                self.assertIn("拉黑失败", result)
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_invalid_durations_do_not_write_records(self):
        for duration in (
            None,
            "bad",
            True,
            0,
            -1,
            float("nan"),
            float("inf"),
            1e300,
            1e20,
        ):
            with self.subTest(duration=duration):
                result = await self.plugin.add_dialog_temporary_blacklist(
                    self.event, duration, "target"
                )
                self.assertIn("拉黑失败", result)
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_permanent_ban_ignores_duration_and_survives_reload(self):
        result = await self.plugin.add_dialog_temporary_blacklist(
            self.event, None, "target", True
        )
        self.assertIn("永久拉黑", result)
        saved = self.plugin.put_kv_data.call_args.args[1]
        self.assertIsNone(saved[self.event.unified_msg_origin]["target"]["expires_at"])

        restored = plugin_module.LLMBlacklistPlugin(self.context, {})
        restored.get_kv_data = AsyncMock(return_value=saved)
        restored.put_kv_data = AsyncMock()
        target = self.make_event("target")
        target.plain_result.side_effect = lambda message: message
        with patch.object(plugin_module.time, "time", return_value=10**12):
            await restored.block_blacklisted_request(target, None)
        target.stop_event.assert_called_once()
        target.send.assert_awaited_once_with(
            plugin_module.DEFAULT_PERMANENT_BLOCKED_MESSAGE
        )

        self.event.plain_result.side_effect = lambda message: message
        results = [
            result async for result in restored.list_dialog_blacklist(self.event)
        ]
        self.assertIn("1. target（target）\n   永久拉黑", results[0])

        admin = self.make_event("admin", admin=True)
        mention = plugin_module.At()
        mention.qq = "target"
        admin.get_messages.return_value = [mention]
        _ = [result async for result in restored.release_user(admin)]
        target.stop_event.reset_mock()
        await restored.block_blacklisted_request(target, None)
        target.stop_event.assert_not_called()

    async def test_permanent_message_uses_its_own_config(self):
        self.plugin.config = {"permanent_blocked_message": "永久禁言"}
        await self.plugin.add_dialog_temporary_blacklist(self.event, permanent=True)
        self.event.plain_result.side_effect = lambda message: message
        await self.plugin.block_blacklisted_request(self.event, None)
        self.event.send.assert_awaited_once_with("永久禁言")

    async def test_false_permanent_keeps_temporary_behavior(self):
        result = await self.plugin.add_dialog_temporary_blacklist(
            self.event, 30, "target", False
        )
        self.assertIn("30分钟", result)
        saved = self.plugin.put_kv_data.call_args.args[1]
        self.assertEqual(
            saved[self.event.unified_msg_origin]["target"]["expires_at"], 1_700_001_800
        )

    async def test_non_boolean_permanent_is_rejected(self):
        for permanent in ("true", "false", 1, 0):
            with self.subTest(permanent=permanent):
                result = await self.plugin.add_dialog_temporary_blacklist(
                    self.event, 30, "target", permanent
                )
                self.assertIn("拉黑失败", result)
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_permanent_ban_still_protects_admins(self):
        result = await self.plugin.add_dialog_temporary_blacklist(
            self.event, None, "admin", True
        )
        self.assertIn("管理员", result)
        self.plugin.put_kv_data.assert_not_awaited()

    async def test_explicit_target_does_not_require_sender_id(self):
        event = self.make_event("")
        event.message_obj.sender.user_id = ""
        result = await self.plugin.add_dialog_temporary_blacklist(event, 1, "target")
        self.assertIn("已将用户", result)


if __name__ == "__main__":
    unittest.main()
