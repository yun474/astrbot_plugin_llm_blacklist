from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class BlacklistEntry:
    """一条会话内临时拉黑记录。"""

    user_id: str
    user_name: str
    expires_at: float


class BlacklistState:
    """按 AstrBot 统一会话标识隔离的临时黑名单。"""

    def __init__(self) -> None:
        self._conversations: dict[str, dict[str, BlacklistEntry]] = {}

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
                try:
                    expires_at = float(payload["expires_at"])
                except (KeyError, TypeError, ValueError):
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

    def to_dict(self) -> dict[str, dict[str, dict[str, str | float]]]:
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

    def get(
        self, conversation_id: str, user_id: str, now: float
    ) -> BlacklistEntry | None:
        entry = self._conversations.get(conversation_id, {}).get(user_id)
        if entry and entry.expires_at > now:
            return entry
        return None

    def entries_for(self, conversation_id: str, now: float) -> list[BlacklistEntry]:
        entries = [
            entry
            for entry in self._conversations.get(conversation_id, {}).values()
            if entry.expires_at > now
        ]
        return sorted(entries, key=lambda entry: (entry.expires_at, entry.user_id))

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
                if users[user_id].expires_at <= now:
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
