from __future__ import annotations

import asyncio
import math
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

from .blacklist_state import (
    BlacklistEntry,
    BlacklistState,
    extract_official_mention_ids,
    format_end_time,
    format_remaining,
    render_blocked_message,
)

PLUGIN_NAME = "astrbot_plugin_llm_blacklist"
STATE_KEY = "temporary_blacklist"
MAX_LIST_LENGTH = 1800
DEFAULT_BLOCKED_MESSAGE_TEMPLATE = (
    "你在当前对话中仍处于临时拉黑状态，剩余 {remaining_time}。发送 /拉黑 可查看详情。"
)


@register(
    PLUGIN_NAME,
    "yun474",
    "由 LLM 自主决定时长的会话临时黑名单",
    "0.1.2",
)
class LLMBlacklistPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._state = BlacklistState()
        self._state_loaded = False
        self._state_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._state_lock:
            await self._ensure_state_loaded()
        logger.info("LLM 会话临时黑名单插件已加载。")

    async def _ensure_state_loaded(self) -> None:
        if self._state_loaded:
            return

        raw = await self.get_kv_data(STATE_KEY, {})
        now = time.time()
        state = BlacklistState.from_dict(raw, now)

        normalized = state.to_dict()
        if raw != normalized:
            await self.put_kv_data(STATE_KEY, normalized)
        self._state = state
        self._state_loaded = True

    async def _save_state(self, state: BlacklistState) -> None:
        await self.put_kv_data(STATE_KEY, state.to_dict())
        self._state = state

    @staticmethod
    def _sender_id(event: AstrMessageEvent) -> str:
        user_id = event.get_sender_id()
        if user_id:
            return user_id
        sender = getattr(event.message_obj, "sender", None)
        return str(getattr(sender, "user_id", "") or "")

    @staticmethod
    def _sender_name(event: AstrMessageEvent, user_id: str) -> str:
        name = " ".join(event.get_sender_name().split())
        return name[:64] or user_id

    @staticmethod
    def _mentioned_user_id(event: AstrMessageEvent) -> str:
        bot_id = str(event.get_self_id() or "")
        for component in event.get_messages():
            if not isinstance(component, At):
                continue
            user_id = str(component.qq).strip()
            if user_id and user_id != "all" and user_id != bot_id:
                return user_id

        for user_id in extract_official_mention_ids(event.get_message_str()):
            if user_id != bot_id:
                return user_id
        return ""

    @staticmethod
    def _format_entry(index: int, entry: BlacklistEntry, now: float) -> str:
        remaining = format_remaining(entry.expires_at - now)
        end_time = format_end_time(entry.expires_at)
        return (
            f"{index}. {entry.user_name}（{entry.user_id}）\n"
            f"   剩余 {remaining}，解禁时间 {end_time}"
        )

    def _format_list(
        self, header: str, conversations: dict[str, list[BlacklistEntry]], now: float
    ) -> str:
        total = sum(len(entries) for entries in conversations.values())
        suffix = "\n…名单过长，已截断，另有 {} 条未展示。"
        budget = MAX_LIST_LENGTH - len(suffix.format(total))
        message = header
        shown = 0
        for conversation_id, entries in conversations.items():
            for index, entry in enumerate(entries, start=1):
                section = (
                    f"\n\n[{conversation_id}]" if conversation_id and index == 1 else ""
                )
                part = section + "\n" + self._format_entry(index, entry, now)
                if len(message) + len(part) > budget:
                    return message + suffix.format(total - shown)
                message += part
                shown += 1
        return message

    @filter.on_llm_request()
    async def block_blacklisted_request(
        self, event: AstrMessageEvent, _request: ProviderRequest
    ) -> None:
        """在请求 LLM 前拦截当前会话内仍处于拉黑期的用户。"""

        if event.is_admin():
            return

        user_id = self._sender_id(event)
        if not user_id:
            return

        now = time.time()
        conversation_id = event.unified_msg_origin
        async with self._state_lock:
            await self._ensure_state_loaded()
            state = self._state.copy()
            changed = state.remove_expired(now)
            entry = state.get(conversation_id, user_id, now)
            if changed:
                await self._save_state(state)

        if entry is None:
            return

        event.stop_event()
        if self.config.get("show_blocked_message", True):
            template = str(
                self.config.get(
                    "blocked_message_template",
                    DEFAULT_BLOCKED_MESSAGE_TEMPLATE,
                )
            )
            message = render_blocked_message(template, entry.expires_at - now)
            if message:
                await event.send(event.plain_result(message))
        logger.info(
            "已拦截用户 %s 在会话 %s 的 LLM 请求，解禁时间 %s。",
            user_id,
            conversation_id,
            format_end_time(entry.expires_at),
        )

    @filter.llm_tool(name="add_dialog_temporary_blacklist")
    async def add_dialog_temporary_blacklist(
        self,
        event: AstrMessageEvent,
        duration_minutes: float | None = None,
        openid: str | None = None,
    ) -> str:
        """将指定用户加入当前对话的临时黑名单。管理员不会被拉黑；拉黑时长由你自主决定。

        Args:
            duration_minutes(number): 拉黑分钟数，必须大于 0，具体数值由你自主决定。
            openid(string): 目标用户的真实 openid 或用户 ID。
                必须来自可确认的身份信息，不要猜测。省略时使用当前消息发送者；
                只填原始 ID，不要带 @ 或 <@...>。
        """

        sender_id = self._sender_id(event)
        if openid is None:
            user_id = sender_id
        elif not isinstance(openid, str) or not openid.strip():
            return "拉黑失败：openid 必须是非空字符串。"
        else:
            user_id = openid.strip()
            if any(char.isspace() or char in "<>@" for char in user_id):
                return "拉黑失败：openid 只填写原始用户 ID，不要带艾特格式或空白。"
        if not user_id:
            return "拉黑失败：无法取得当前消息发送者 ID，请传入 openid。"
        if user_id == "all" or user_id == str(event.get_self_id()):
            return "拉黑失败：不能拉黑机器人自身或全体成员。"

        admins = self.context.get_config(event.unified_msg_origin).get("admins_id", [])
        if (user_id == sender_id and event.is_admin()) or user_id in map(str, admins):
            return "拉黑失败：目标用户是 AstrBot 管理员。"

        try:
            duration = float(duration_minutes)
        except (TypeError, ValueError, OverflowError):
            return "拉黑失败：duration_minutes 必须是数字。"
        if (
            isinstance(duration_minutes, bool)
            or not math.isfinite(duration)
            or duration <= 0
        ):
            return "拉黑失败：duration_minutes 必须是大于 0 的有限数字。"

        now = time.time()
        expires_at = now + duration * 60
        if not math.isfinite(expires_at):
            return "拉黑失败：拉黑时长过大。"
        try:
            end_time = format_end_time(expires_at)
        except (OverflowError, OSError, ValueError):
            return "拉黑失败：拉黑时长超出日期支持范围。"

        conversation_id = event.unified_msg_origin
        entry = BlacklistEntry(
            user_id=user_id,
            user_name=self._sender_name(event, user_id)
            if user_id == sender_id
            else user_id,
            expires_at=expires_at,
        )
        async with self._state_lock:
            await self._ensure_state_loaded()
            state = self._state.copy()
            state.remove_expired(now)
            state.add(conversation_id, entry)
            await self._save_state(state)

        remaining = format_remaining(expires_at - now)
        logger.info(
            "LLM 已将用户 %s 加入会话 %s 的临时黑名单，时长 %s。",
            user_id,
            conversation_id,
            remaining,
        )
        return (
            f"已将用户 {entry.user_name}（{user_id}）在当前对话拉黑 {remaining}，"
            f"解禁时间 {end_time}。"
        )

    @filter.command("拉黑")
    async def list_dialog_blacklist(self, event: AstrMessageEvent):
        """查看当前对话的临时黑名单与剩余时间。"""

        now = time.time()
        async with self._state_lock:
            await self._ensure_state_loaded()
            state = self._state.copy()
            changed = state.remove_expired(now)
            entries = state.entries_for(event.unified_msg_origin, now)
            if changed:
                await self._save_state(state)

        if not entries:
            yield event.plain_result("当前对话没有仍在生效的临时拉黑记录。")
            return

        message = self._format_list(
            f"当前对话临时黑名单（{len(entries)} 人）", {"": entries}, now
        )
        yield event.plain_result(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("全局拉黑")
    async def list_global_blacklist(self, event: AstrMessageEvent):
        """查看所有对话的临时黑名单，仅限 AstrBot 管理员。"""

        now = time.time()
        async with self._state_lock:
            await self._ensure_state_loaded()
            state = self._state.copy()
            changed = state.remove_expired(now)
            conversations = state.all_entries(now)
            if changed:
                await self._save_state(state)

        if not conversations:
            yield event.plain_result("全部对话中都没有仍在生效的临时拉黑记录。")
            return

        total = sum(len(entries) for entries in conversations.values())
        message = self._format_list(
            f"全局临时黑名单（{len(conversations)} 个对话，共 {total} 人）",
            conversations,
            now,
        )
        yield event.plain_result(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("出狱")
    async def release_user(self, event: AstrMessageEvent):
        """解除被艾特用户在当前对话中的临时拉黑，仅限 AstrBot 管理员。"""

        user_id = self._mentioned_user_id(event)
        if not user_id:
            yield event.plain_result("请艾特需要解除拉黑的用户，例如：/出狱 @用户")
            return

        now = time.time()
        conversation_id = event.unified_msg_origin
        async with self._state_lock:
            await self._ensure_state_loaded()
            state = self._state.copy()
            changed = state.remove_expired(now)
            entry = state.remove(conversation_id, user_id)
            if changed or entry is not None:
                await self._save_state(state)

        if entry is None:
            yield event.plain_result(f"用户 {user_id} 当前不在这个对话的临时黑名单中。")
            return

        logger.info(
            "管理员已将用户 %s 移出会话 %s 的临时黑名单。",
            user_id,
            conversation_id,
        )
        yield event.plain_result(
            f"已解除 {entry.user_name}（{entry.user_id}）在当前对话中的拉黑状态。"
        )

    async def terminate(self) -> None:
        logger.info("LLM 会话临时黑名单插件已卸载。")
