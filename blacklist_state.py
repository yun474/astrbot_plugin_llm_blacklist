from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

OFFICIAL_MENTION_PATTERN = re.compile(r"<@!?([^<>\s]+)>")


@dataclass(frozen=True, slots=True)
class BlacklistEntry:
    """一条会话内拉黑记录，expires_at 为 None 表示永久拉黑。"""

    user_id: str
    user_name: str
    expires_at: float | None

    @property
    def permanent(self) -> bool:
        return self.expires_at is None

    def is_active(self, now: float) -> bool:
        return self.expires_at is None or self.expires_at > now


class BlacklistState:
    """按 AstrBot 统一会话标识隔离的临时黑名单。"""

    def __init__(self) -> None:
        self._conversations: dict[str, dict[str, BlacklistEntry]] = {}

    def copy(self) -> BlacklistState:
        state = BlacklistState()
        state._conversations = {
            conversation_id: users.copy()
            for conversation_id, users in self._conversations.items()
        }
        return state

    @classmethod
    def from_dict(cls, raw: Any, now: float) -> BlacklistState:
        state = cls()
        if not isinstance(raw, dict):
            return state

        for conversation_id, users in raw.items():
            if not isinstance(conversation_id, str) or not isinstance(users, dict):
                continue
            for user_id, payload in users.items():
                if not isinstance(user_id, str) or not isinstance(payload, dict):
                    continue
                if "expires_at" not in payload:
                    continue
                expires_at = payload["expires_at"]
                # 显式存储的 null 表示永久拉黑
                if expires_at is not None:
                    try:
                        expires_at = float(expires_at)
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(expires_at) or expires_at <= now:
                        continue

                user_name = str(payload.get("user_name") or user_id)
                state.add(
                    conversation_id,
                    BlacklistEntry(
                        user_id=user_id,
                        user_name=user_name,
                        expires_at=expires_at,
                    ),
                )
        return state

    def to_dict(self) -> dict[str, dict[str, dict[str, str | float | None]]]:
        return {
            conversation_id: {
                user_id: {
                    "user_name": entry.user_name,
                    "expires_at": entry.expires_at,
                }
                for user_id, entry in users.items()
            }
            for conversation_id, users in self._conversations.items()
        }

    def add(self, conversation_id: str, entry: BlacklistEntry) -> None:
        users = self._conversations.setdefault(conversation_id, {})
        users[entry.user_id] = entry

    def remove(self, conversation_id: str, user_id: str) -> BlacklistEntry | None:
        users = self._conversations.get(conversation_id)
        if not users:
            return None

        entry = users.pop(user_id, None)
        if not users:
            del self._conversations[conversation_id]
        return entry

    def get(
        self, conversation_id: str, user_id: str, now: float
    ) -> BlacklistEntry | None:
        entry = self._conversations.get(conversation_id, {}).get(user_id)
        if entry and entry.is_active(now):
            return entry
        return None

    def entries_for(self, conversation_id: str, now: float) -> list[BlacklistEntry]:
        entries = [
            entry
            for entry in self._conversations.get(conversation_id, {}).values()
            if entry.is_active(now)
        ]
        # 临时记录按解禁时间排序，永久记录统一排在最后
        return sorted(
            entries,
            key=lambda entry: (
                entry.permanent,
                entry.expires_at or 0.0,
                entry.user_id,
            ),
        )

    def all_entries(self, now: float) -> dict[str, list[BlacklistEntry]]:
        return {
            conversation_id: entries
            for conversation_id in sorted(self._conversations)
            if (entries := self.entries_for(conversation_id, now))
        }

    def remove_expired(self, now: float) -> bool:
        changed = False
        for conversation_id in list(self._conversations):
            users = self._conversations[conversation_id]
            for user_id in list(users):
                if not users[user_id].is_active(now):
                    del users[user_id]
                    changed = True
            if not users:
                del self._conversations[conversation_id]
        return changed


def format_remaining(seconds: float) -> str:
    """将剩余秒数格式化为紧凑的中文时长。"""

    total_seconds = max(0, math.ceil(seconds))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if seconds or not parts:
        parts.append(f"{seconds}秒")
    return "".join(parts)


def format_end_time(timestamp: float) -> str:
    """按 AstrBot 所在机器的本地时区格式化解禁时间。"""

    return (
        datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    )


def render_blocked_message(template: str, remaining_seconds: float) -> str:
    """替换拉黑提示模板中的剩余时间占位符。"""

    return template.replace(
        "{remaining_time}",
        format_remaining(remaining_seconds),
    )


def extract_official_mention_ids(message: str) -> list[str]:
    """提取官方机器人文本格式中的艾特用户 ID。"""

    return OFFICIAL_MENTION_PATTERN.findall(message)
