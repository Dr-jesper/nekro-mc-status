"""Conservative, deterministic query intent; no model calls or mutable state."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QueryIntent:
    profile: str | None
    explicit: bool = False


def message_text(message: Any) -> str | None:
    """Use actual text segments; do not query quoted/forwarded/media messages."""
    if getattr(message, "is_recalled", False):
        return None
    # OneBot reply segments are stored in PlatformMessageExt.ref_msg_id, then
    # removed from content_data by the 2.4.0 converter. Check both real platform
    # metadata and segment forms instead of relying on a reference segment.
    ext_data = getattr(message, "ext_data", None)
    if isinstance(ext_data, dict) and ext_data.get("ref_msg_id"):
        return None
    segments = getattr(message, "content_data", None) or []
    if not segments:
        return str(getattr(message, "content_text", "") or "").strip()
    texts = []
    for segment in segments:
        kind = getattr(segment, "type", "")
        kind = str(getattr(kind, "value", kind)).lower()
        if kind == "text":
            texts.append(str(getattr(segment, "text", "") or ""))
        elif kind != "at":
            return None
    return "".join(texts).strip()


_SCHOOL = {
    "wzbc": re.compile(r"温商|温州商学院|wzbc", re.I),
    "shou": re.compile(r"水专|上海海洋大学|shou(?:mc)?", re.I),
}
_NEGATIVE = re.compile(r"[\"'“”‘’「」『』`]|(?:他说|她说|他们说|朋友说|有人说|说过|问过|有人问|别人问|有人在问|提到|讨论|原话|引用|转述|这句话|不要|不用|别查|别问|无需|不需要|不是问|没问|不想|不打算|不查询|不要查询)")


def parse_intent(message: Any, group_profile: str | None = None) -> QueryIntent | None:
    text = message_text(message)
    if not text:
        return None
    command = re.fullmatch(r"/(wzbc|shou)", text, re.I)
    if command:
        return QueryIntent(command.group(1).lower(), explicit=True)
    if text.startswith(("/", "!")) or len(text) > 100 or _NEGATIVE.search(text):
        return None
    # Current presence/status requests only. Historical outages, policy and general
    # discussion are intentionally outside this plugin's claim.
    if re.search(r"昨天|前天|上周|以前|历史|当时|曾经|上次|为什么|为何|怎么回事|原因|教程|设置|介绍", text):
        return None
    schools = [key for key, pattern in _SCHOOL.items() if pattern.search(text)]
    if len(schools) > 1:
        return None
    if not schools and re.search(r"[\u4e00-\u9fff]{2,12}(?:大学|学院)|(?:其他|别的|另一个).{0,4}(?:服务器|学校|大学|服)", text):
        return None
    target = bool(schools) or bool(re.search(r"服务器|服里|服内|MC服|mc服|游戏服", text))
    request = bool(re.search(
        r"(?:有|还|现在|目前|在线|多少|几)(?:.{0,5})(?:人|玩家|在线)|"
        r"(?:谁|哪些人)(?:.{0,5})(?:在|在线)|"
        r"(?:查|看看|看下|看一下)(?:.{0,12})(?:状态|在线|人|玩家)|"
        r"(?:状态|在线人数)(?:.{0,5})(?:如何|怎么样|怎样|多少|吗|呢|查)|"
        r"(?:在线|离线|开着|开了|开服|能进|能连|正常|活着)(?:.{0,3})(?:吗|没|了吗|么)", text,
    ))
    # Unrelated statements such as “服务器里有人在讨论宕机历史” do not ask.
    asking = bool(re.search(r"吗|么|没|呢|？|\?|多少|几(?:个)?人|谁|哪些人|查|看看|看下|看一下|如何|怎么样|怎样", text))
    if not (target and request and asking):
        return None
    profile = schools[0] if schools else group_profile
    return QueryIntent(profile if profile in {"wzbc", "shou"} else None)
