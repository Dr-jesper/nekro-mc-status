"""Nekro public commands and deterministic natural-language interception."""
from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path
from typing import Any

from nekro_agent.api import message as message_api
from nekro_agent.api.plugin import CmdCtl, CommandExecutionContext, CommandPermission
from nekro_agent.api.schemas import AgentCtx
from nekro_agent.core.logger import get_sub_logger
from nekro_agent.core.os_env import USER_UPLOAD_DIR
from nekro_agent.schemas.chat_message import ChatMessage
from nekro_agent.schemas.signal import MsgSignal
from nekro_agent.services.command.schemas import CommandOutputSegment, CommandResponse
from nekro_agent.tools.path_convertor import (
    convert_filename_to_sandbox_upload_path,
    sanitize_chat_key_for_path,
)

from .intent import QueryIntent, parse_intent
from .plugin import mc_config, plugin
from .service import PreparedReply, StatusService

logger = get_sub_logger("plugin.nekro_mc_status")
_service: StatusService | None = None


def _get_service() -> StatusService:
    global _service
    if _service is None:
        _service = StatusService(mc_config, plugin.get_plugin_data_dir)
    return _service


def _scope_allowed(chat_key: str) -> bool:
    denied = getattr(mc_config, "DENY_GROUPS", []) or []
    allowed = getattr(mc_config, "ALLOW_GROUPS", []) or []
    return chat_key not in denied and (not allowed or chat_key in allowed)


def _publish_upload_copy(chat_key: str, image_path: str | Path, profile: str) -> Path:
    """把插件私有目录里的成图复制到本频道的 uploads 目录，返回宿主机路径。

    发送链路只认两类路径：宿主机路径（命令输出段）与沙盒 shared/uploads 路径
    （message API）。plugin_data 两者都不是，直接发送会被 path_convertor 拒绝；
    shared 路径又要求上下文带 container_key，而消息钩子的 ctx 并没有该字段
    （实测会落到 sandbox_<chat_key> 兜底），因此统一走 uploads：
    宿主机 <USER_UPLOAD_DIR>/<chat_key>/<name>，沙盒 /app/uploads/<name>。
    文件名带内容摘要，同一张图重复查询不会重复占盘，也避免并发覆盖。
    """
    source = Path(image_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    dest = Path(USER_UPLOAD_DIR) / sanitize_chat_key_for_path(chat_key) / f"mc-{profile}-{digest}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file() or dest.stat().st_size != source.stat().st_size:
        shutil.copyfile(source, dest)
    return dest


def _intent_for(ctx: Any, message: Any) -> QueryIntent | None:
    if not getattr(plugin, "is_enabled", True) or not getattr(mc_config, "ENABLED", True):
        return None
    chat_key = str(getattr(message, "chat_key", "") or getattr(ctx, "chat_key", "") or "")
    if not chat_key or not _scope_allowed(chat_key):
        return None
    # Bind full keys only. This prevents a platform's group number leaking to a
    # same-number channel on another adapter.
    binding = (getattr(mc_config, "GROUP_PROFILES", {}) or {}).get(chat_key)
    intent = parse_intent(message, binding)
    if intent is None:
        return None
    if not intent.explicit:
        channel = getattr(ctx, "db_chat_channel", None)
        chat_type = getattr(message, "chat_type", None) or getattr(channel, "chat_type", None)
        chat_type = str(getattr(chat_type, "value", chat_type)).lower()
        if (not getattr(mc_config, "NATURAL_LANGUAGE_ENABLED", True)
                or chat_type != "group"
                or bool(getattr(channel, "observe_mode", False))
                or getattr(channel, "is_active", True) is False):
            return None
    return intent


def claims_message(ctx: Any, message: Any) -> bool:
    """Optional loaded-plugin handshake for reply judge; pure and order-free.

    Includes deduplicated, busy, cooldown and unbound selection replies because
    these requests still belong to this plugin and must not start a model reply.
    """
    return _intent_for(ctx, message) is not None


async def _native_query(context: CommandExecutionContext, profile: str) -> CommandResponse:
    if not getattr(plugin, "is_enabled", True) or not getattr(mc_config, "ENABLED", True):
        return CmdCtl.failed("Minecraft 状态查询已关闭。")
    if not _scope_allowed(context.chat_key):
        return CmdCtl.failed("这个频道未开启 Minecraft 状态查询。")
    service = _get_service()
    reply = await service.prepare(profile, context.chat_key, explicit=True)
    if service is not _service or not getattr(plugin, "is_enabled", True) or not getattr(mc_config, "ENABLED", True):
        return CmdCtl.failed("Minecraft 状态查询已关闭。")
    segments = []
    if reply.text:
        segments.append(CommandOutputSegment(type="text", text=reply.text))
    if reply.image_path:
        # 命令输出段按 SDK 约定携带宿主机路径（materialize / 合并转发 / 适配器都按
        # 宿主机路径读文件），因此先物化一份到 uploads，避免依赖插件临时目录的清理时机。
        try:
            host_copy = _publish_upload_copy(context.chat_key, reply.image_path, profile)
        except Exception:
            logger.warning("[mc_status] 命令输出图片物化失败，降级为文本结果")
            segments.append(CommandOutputSegment(type="text", text=reply.fallback_text or "图片发送准备失败，请稍后再试。"))
        else:
            segments.append(CommandOutputSegment(type="image", file_path=str(host_copy)))
    return CmdCtl.success(segments)


@plugin.mount_command(name="wzbc", description="查询温商 Minecraft 两服的在线状态", permission=CommandPermission.PUBLIC, usage="wzbc")
async def wzbc_command(context: CommandExecutionContext) -> CommandResponse:
    return await _native_query(context, "wzbc")


@plugin.mount_command(name="shou", description="查询水专 Minecraft 三服的在线状态", permission=CommandPermission.PUBLIC, usage="shou")
async def shou_command(context: CommandExecutionContext) -> CommandResponse:
    return await _native_query(context, "shou")


async def _send_reply(ctx: AgentCtx, message: ChatMessage, reply: PreparedReply, profile: str) -> None:
    if reply.duplicate:
        return
    reference = str(getattr(message, "message_id", "") or "") or None
    if reply.image_path:
        # 走 uploads 沙盒路径：UniversalChatService 会按 chat_key 还原成宿主机
        # uploads 文件。此前用 ctx.fs.mixed_forward_file 得到 /app/shared/...，
        # 消息钩子的 ctx 没有 container_key，转换直接抛错并把错误文本回给群里。
        try:
            host_copy = _publish_upload_copy(message.chat_key, reply.image_path, profile)
        except Exception:
            if reply.fallback_text:
                await message_api.send_text(message.chat_key, reply.fallback_text, ctx, ref_msg_id=reference)
                return
            raise
        sandbox_path = convert_filename_to_sandbox_upload_path(host_copy.name)
        await message_api.send_image(message.chat_key, str(sandbox_path), ctx, ref_msg_id=reference)
    elif reply.text:
        await message_api.send_text(message.chat_key, reply.text, ctx, ref_msg_id=reference)


@plugin.mount_on_user_message()
async def on_user_message(ctx: AgentCtx, message: ChatMessage) -> MsgSignal:
    intent = _intent_for(ctx, message)
    if intent is None:
        return MsgSignal.CONTINUE
    try:
        deadline = asyncio.get_running_loop().time() + float(getattr(mc_config, "REQUEST_TIMEOUT_SECONDS", 20))
        message_key = str(getattr(message, "message_id", "") or "")
        if not message_key and getattr(message, "send_timestamp", None):
            material = f"{getattr(message, 'sender_id', '')}|{message.send_timestamp}|{getattr(message, 'content_text', '')}"
            message_key = "fallback-" + hashlib.sha256(material.encode()).hexdigest()
        service = _get_service()
        reply = await service.prepare(
            intent.profile, str(message.chat_key), message_key,
            explicit=intent.explicit,
        )
        if service is not _service or _intent_for(ctx, message) is None:
            return MsgSignal.BLOCK_TRIGGER
        await asyncio.wait_for(
            _send_reply(ctx, message, reply, intent.profile or ""),
            timeout=max(0.1, deadline - asyncio.get_running_loop().time()),
        )
    except Exception as exc:
        # A failed send is still a claimed deterministic request. Returning
        # CONTINUE here would make a second model response contradict the result.
        logger.warning(f"[mc_status] 查询或发送失败: {type(exc).__name__}")
    return MsgSignal.BLOCK_TRIGGER


@plugin.on_disabled()
async def on_disabled() -> None:
    global _service
    if _service is not None:
        await _service.close()
        _service = None
