"""Bounded status query lifecycle, coalescing and temporary image ownership."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class PreparedReply:
    text: str = ""
    image_path: Path | None = None
    duplicate: bool = False
    fallback_text: str = ""


class StatusService:
    def __init__(self, config: Any, data_dir: Callable[[], Path], *, query=None, render=None):
        self.config = config
        self.data_dir = data_dir
        self.query = query
        self.render = render
        self._seen: dict[tuple[str, str], float] = {}
        self._last: dict[str, float] = {}
        self._hint_last: dict[str, float] = {}
        self._notice_last: dict[str, float] = {}
        self._busy: set[str] = set()
        self._active = 0
        self._cache: dict[str, tuple[float, Any]] = {}
        self._queries: dict[str, asyncio.Task] = {}
        self._query_active = 0
        self._query_condition = asyncio.Condition()
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._build_tasks: set[asyncio.Task] = set()
        self._closed = False

    def _prune(self, now: float) -> None:
        self._seen = {key: ts for key, ts in self._seen.items() if now - ts < 120}
        self._last = {key: ts for key, ts in self._last.items() if now - ts < 600}
        self._hint_last = {key: ts for key, ts in self._hint_last.items() if now - ts < 600}
        self._notice_last = {key: ts for key, ts in self._notice_last.items() if now - ts < 600}
        self._cache = {key: value for key, value in self._cache.items() if now - value[0] < 60}
        for mapping in (self._seen, self._last, self._hint_last, self._notice_last, self._cache):
            while len(mapping) > 4096:
                mapping.pop(next(iter(mapping)))

    def _notice(self, chat_key: str, message_key: str, text: str) -> PreparedReply:
        # Prevent the cooldown explanation itself becoming a flood. Native
        # commands have no message key and need an ordinary command response.
        now = time.monotonic()
        if message_key and now - self._notice_last.get(chat_key, float("-inf")) < 3:
            return PreparedReply(duplicate=True)
        self._notice_last[chat_key] = now
        return PreparedReply(text=text)

    def admit(self, chat_key: str, message_key: str, *, explicit: bool, selection_hint: bool = False) -> PreparedReply | None:
        """Atomic until the first await; duplicates include failed sends."""
        now = time.monotonic()
        self._prune(now)
        if message_key:
            key = (chat_key, message_key)
            if key in self._seen:
                return PreparedReply(duplicate=True)
            self._seen[key] = now
        cooldown = float(getattr(self.config, "COMMAND_COOLDOWN_SECONDS" if explicit else "NATURAL_COOLDOWN_SECONDS", 3 if explicit else 20))
        if chat_key in self._busy and not selection_hint:
            return self._notice(chat_key, message_key, "正在查询这个频道的服务器状态，请稍等。") if explicit else PreparedReply(duplicate=True)
        timestamps = self._hint_last if selection_hint else self._last
        if now - timestamps.get(chat_key, float("-inf")) < cooldown:
            if selection_hint or not explicit:
                return PreparedReply(duplicate=True)
            return self._notice(chat_key, message_key, "刚刚查询过，请稍后再查。")
        if selection_hint:
            timestamps[chat_key] = now
        return None

    async def prepare(self, profile: str | None, chat_key: str, message_key: str = "", *, explicit: bool = False) -> PreparedReply:
        if self._closed:
            return PreparedReply(duplicate=True)
        refusal = self.admit(chat_key, message_key, explicit=explicit, selection_hint=profile not in {"wzbc", "shou"})
        if refusal is not None:
            return refusal
        if profile not in {"wzbc", "shou"}:
            return PreparedReply(text="这个群还没有绑定服务器。用 /wzbc 查询温商，或用 /shou 查询水专。")
        if self._active >= int(getattr(self.config, "MAX_CONCURRENT_REQUESTS", 2)):
            return self._notice(chat_key, message_key, "服务器查询正忙，请稍后再试。") if explicit else PreparedReply(duplicate=True)
        self._last[chat_key] = time.monotonic()
        self._busy.add(chat_key)
        self._active += 1
        task = asyncio.create_task(self._build(profile))
        self._build_tasks.add(task)
        task.add_done_callback(self._build_tasks.discard)
        try:
            return await asyncio.wait_for(task, timeout=float(getattr(self.config, "REQUEST_TIMEOUT_SECONDS", 20)))
        except asyncio.CancelledError:
            if self._closed:
                return PreparedReply(duplicate=True)
            raise
        except asyncio.TimeoutError:
            return PreparedReply(text="服务器状态查询超时，暂时无法确认在线情况，请稍后再试。")
        except Exception:
            return PreparedReply(text="服务器状态查询或图片生成失败，暂时无法提供状态，请稍后再试。")
        finally:
            self._active -= 1
            self._busy.discard(chat_key)

    async def _query_one(self, host: str):
        now = time.monotonic()
        cached = self._cache.get(host)
        if cached and now - cached[0] < float(getattr(self.config, "CACHE_SECONDS", 10)):
            return cached[1]
        task = self._queries.get(host)
        if task is None:
            from .query import query_server
            from .models import ServerSnapshot

            async def execute():
                admitted = False
                try:
                    timeout = float(getattr(self.config, "QUERY_TIMEOUT_SECONDS", 5))
                    async with self._query_condition:
                        await self._query_condition.wait_for(lambda: self._query_active < int(getattr(self.config, "MAX_CONCURRENT_REQUESTS", 2)))
                        self._query_active += 1
                        admitted = True
                    snapshot = await asyncio.wait_for((self.query or query_server)(host, timeout=timeout), timeout=timeout + 0.25)
                except asyncio.TimeoutError:
                    snapshot = ServerSnapshot(host=host, error="查询超时，无法确认状态")
                except Exception:
                    snapshot = ServerSnapshot(host=host, error="查询失败，无法确认状态")
                finally:
                    if admitted:
                        async with self._query_condition:
                            self._query_active -= 1
                            self._query_condition.notify_all()
                self._cache[host] = (time.monotonic(), snapshot)
                return snapshot

            task = asyncio.create_task(execute())
            self._queries[host] = task
            task.add_done_callback(lambda done: self._queries.pop(host, None) if self._queries.get(host) is done else None)
        # A single caller's request deadline must not cancel another caller's
        # shared in-flight query. Each query has its own bounded timeout.
        return await asyncio.shield(task)

    async def _build(self, profile: str) -> PreparedReply:
        from .rendering import render_profile

        style = getattr(self.config, "WZBC_STYLE" if profile == "wzbc" else "SHOU_STYLE", "wzbcmc" if profile == "wzbc" else "shoumc")
        if style not in ({"wzbcmc", "rich", "simple"} if profile == "wzbc" else {"shoumc", "rich", "simple"}):
            return PreparedReply(text="图片风格配置无效，请管理员选择对应学校预设、rich 或 simple。")
        try:
            timezone = ZoneInfo(str(getattr(self.config, "DISPLAY_TIMEZONE", "Asia/Shanghai")))
        except (ZoneInfoNotFoundError, ValueError):
            return PreparedReply(text="查询时间时区配置无效，请管理员填写有效的 IANA 时区名称。")
        servers = getattr(self.config, "WZBC_SERVERS" if profile == "wzbc" else "SHOU_SERVERS", [])
        if not isinstance(servers, list) or not servers or len(servers) > 8:
            return PreparedReply(text="服务器配置无效：请管理员配置 1 至 8 台服务器。")
        entries = []
        for row in servers:
            if not isinstance(row, dict):
                return PreparedReply(text="服务器配置无效：每台服务器需要 name 和 host。")
            name, host = str(row.get("name", "")).strip(), str(row.get("host", "")).strip()
            if not name or not host or len(host) > 255 or any(ch.isspace() for ch in host):
                return PreparedReply(text="服务器配置无效：每台服务器需要有效的 name 和 host。")
            entries.append((name[:100], host))
        snapshots = await asyncio.gather(*(self._query_one(host) for _, host in entries))
        snapshots = [replace(snapshot, server_name=name) for (name, _), snapshot in zip(entries, snapshots)]
        failures = [snapshot for snapshot in snapshots if snapshot.error]
        # Preserve real counts and failure semantics even if graphics fail.
        title = "温商" if profile == "wzbc" else "水专"
        lines = [f"{title} Minecraft 服务器状态："]
        for snapshot in snapshots:
            if snapshot.error:
                lines.append(f"{snapshot.server_name}：查询失败，无法确认在线情况（{snapshot.error}）")
            else:
                count = str(snapshot.online) if snapshot.online is not None else "未知"
                lines.append(f"{snapshot.server_name}：在线 {count}/{snapshot.max if snapshot.max is not None else '未知'}")
        fallback = "\n".join(lines)
        if len(failures) == len(snapshots):
            return PreparedReply(text=fallback)
        base_dir = Path(self.data_dir()).resolve()
        output_dir = (base_dir / "status-images").resolve()
        if not output_dir.is_relative_to(base_dir):
            raise ValueError("status image directory escaped plugin data directory")
        output_dir.mkdir(parents=True, exist_ok=True)
        self._sweep(output_dir)
        output_path = output_dir / f"mc-status-{uuid4().hex}.png"
        self._schedule_cleanup(output_path)
        try:
            rendered = Path(await (self.render or render_profile)(
                style, snapshots, datetime.now(timezone), output_path,
                font_path=str(getattr(self.config, "FONT_PATH", "") or "") or None,
                show_avatars=bool(getattr(self.config, "SHOW_AVATARS", True)),
            )).resolve()
            if rendered != output_path or not rendered.is_file():
                raise ValueError("renderer did not return owned output file")
        except asyncio.CancelledError:
            raise
        except Exception:
            return PreparedReply(text=fallback + "\n图片生成失败，以上为实际查询结果。")
        return PreparedReply(image_path=rendered, fallback_text=fallback)

    def _sweep(self, directory: Path) -> None:
        cutoff = time.time() - int(getattr(self.config, "TEMP_RETENTION_SECONDS", 600))
        files = sorted(directory.glob("mc-status-*.png"), key=lambda path: path.stat().st_mtime)
        for index, path in enumerate(files):
            if path.stat().st_mtime < cutoff or index < len(files) - 128:
                path.unlink(missing_ok=True)

    def _schedule_cleanup(self, path: Path) -> None:
        async def cleanup():
            await asyncio.sleep(int(getattr(self.config, "TEMP_RETENTION_SECONDS", 600)))
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # Lazy sweep retries on the next query.
        task = asyncio.create_task(cleanup())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._cleanup_tasks) + list(self._queries.values()) + list(self._build_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._queries.clear()
        self._cache.clear()
