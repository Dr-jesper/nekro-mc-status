# SPDX-License-Identifier: AGPL-3.0-only
# Derived from Aer-Light-Work/astrbot_mcgetter_enhanced (薄暝, Aer and contributors).
# Modified for Nekro MC Status in 2026; see NOTICE.md.
"""Bounded Java status query and faithful Minecraft MOTD parsing."""
import asyncio
import re
import weakref
from dataclasses import replace
from typing import Any, Optional
from mcstatus import JavaServer
from mcstatus.motd import Motd
from mcstatus.motd.components import JavaFormatting, JavaMinecraftColor, WebColor
from .models import TextSegment, MotdLine, PlayerInfo, ServerSnapshot

# Minecraft 颜色代码映射
MC_COLOR_CODES = {
    "0": (0, 0, 0),
    "1": (0, 0, 170),
    "2": (0, 170, 0),
    "3": (0, 170, 170),
    "4": (170, 0, 0),
    "5": (170, 0, 170),
    "6": (255, 170, 0),
    "7": (170, 170, 170),
    "8": (85, 85, 85),
    "9": (85, 85, 255),
    "a": (85, 255, 85),
    "b": (85, 255, 255),
    "c": (255, 85, 85),
    "d": (255, 85, 255),
    "e": (255, 255, 85),
    "f": (255, 255, 255),
}

MC_COLOR_NAMES = {
    "black": (0, 0, 0),
    "dark_blue": (0, 0, 170),
    "dark_green": (0, 170, 0),
    "dark_aqua": (0, 170, 170),
    "dark_red": (170, 0, 0),
    "dark_purple": (170, 0, 170),
    "gold": (255, 170, 0),
    "gray": (170, 170, 170),
    "grey": (170, 170, 170),
    "dark_gray": (85, 85, 85),
    "dark_grey": (85, 85, 85),
    "blue": (85, 85, 255),
    "green": (85, 255, 85),
    "aqua": (85, 255, 255),
    "red": (255, 85, 85),
    "light_purple": (255, 85, 255),
    "yellow": (255, 255, 85),
    "white": (255, 255, 255),
    "reset": None,
}

def parse_mc_color(color_str: str) -> Optional[tuple[int, int, int]]:
    """
    解析 Minecraft 颜色字符串
    支持: 单字符代码(§a), hex(#FF0000), 颜色名称(red)
    """
    if not color_str:
        return None
    normalized = str(color_str).lower()
    if len(normalized) == 1:
        return MC_COLOR_CODES.get(normalized)
    if normalized.startswith("#") and len(normalized) == 7:
        try:
            return (
                int(normalized[1:3], 16),
                int(normalized[3:5], 16),
                int(normalized[5:7], 16),
            )
        except ValueError:
            return None
    return MC_COLOR_NAMES.get(normalized)


_HEX_DIGITS = frozenset("0123456789abcdef")


def parse_section_sign_text(
    text: str,
    default_color: Optional[tuple[int, int, int]] = None,
) -> list[TextSegment]:
    """
    解析包含 § 格式代码的文本
    §0-§f: 旧版固定颜色代码
    §#RRGGBB: 本插件为自定义备注保留的扩展颜色语法，不是 Minecraft
        1.16+ 原版的十六进制颜色表示法
    §k: 随机字符(obfuscated, 忽略)
    §l: 粗体
    §m: 删除线
    §n: 下划线
    §o: 斜体
    §r: 重置
    """
    segments: list[TextSegment] = []
    current_text: list[str] = []
    current_color = default_color
    current_bold = False
    current_italic = False
    current_underline = False
    current_strikethrough = False

    def flush() -> None:
        nonlocal current_text
        if current_text:
            segments.append(TextSegment(
                text="".join(current_text),
                color=current_color,
                bold=current_bold,
                italic=current_italic,
                underline=current_underline,
                strikethrough=current_strikethrough,
            ))
            current_text = []

    i = 0
    while i < len(text):
        if text[i] == "§" and i + 1 < len(text):
            flush()
            code = text[i + 1].lower()
            if code == "#" and i + 7 < len(text):
                # 插件扩展：§#RRGGBB，例如 §#ffcd1a → (255,205,26)。
                hex_raw = text[i + 2:i + 8]
                if all(ch.lower() in _HEX_DIGITS for ch in hex_raw):
                    current_color = parse_mc_color(f"#{hex_raw}")
                    current_bold = False
                    current_italic = False
                    current_underline = False
                    current_strikethrough = False
                    i += 8
                    continue
                # 解析失败按普通字符处理 §#
                i += 2
            elif code in MC_COLOR_CODES:
                current_color = MC_COLOR_CODES[code]
                # Java 版颜色代码会清除之前启用的样式。
                current_bold = False
                current_italic = False
                current_underline = False
                current_strikethrough = False
                i += 2
            elif code == "l":
                current_bold = True
                i += 2
            elif code == "m":
                current_strikethrough = True
                i += 2
            elif code == "n":
                current_underline = True
                i += 2
            elif code == "o":
                current_italic = True
                i += 2
            elif code == "r":
                current_color = default_color
                current_bold = False
                current_italic = False
                current_underline = False
                current_strikethrough = False
                i += 2
            else:
                # §k (obfuscated) 及其他未识别代码忽略
                i += 2
        else:
            current_text.append(text[i])
            i += 1

    flush()
    return segments


def _split_motd_lines(segments: list[TextSegment]) -> list[MotdLine]:
    """按换行拆分文本段，并复制样式到被切开的片段。"""
    lines: list[MotdLine] = []
    current: list[TextSegment] = []

    for segment in segments:
        parts = segment.text.split("\n")
        for index, part in enumerate(parts):
            if part:
                current.append(TextSegment(
                    text=part,
                    color=segment.color,
                    bold=segment.bold,
                    italic=segment.italic,
                    underline=segment.underline,
                    strikethrough=segment.strikethrough,
                ))
            if index < len(parts) - 1:
                lines.append(MotdLine(segments=current))
                current = []

    lines.append(MotdLine(segments=current))
    return lines


def _parse_component_motd(raw: list[Any] | dict[str, Any]) -> list[TextSegment]:
    """遍历原始 JSON 组件，保留父样式并隔离兄弟组件。

    mcstatus 的扁平化 / simplify 会丢失空 text 父节点的格式和组件边界。
    JSON 颜色与 Legacy 颜色代码的格式重置语义也不同，因此不经过该路径。
    """
    segments: list[TextSegment] = []
    style_fields = {
        "bold": "bold", "italic": "italic", "underlined": "underline",
        "strikethrough": "strikethrough",
    }
    legacy_fields = {
        JavaFormatting.BOLD: "bold", JavaFormatting.ITALIC: "italic",
        JavaFormatting.UNDERLINED: "underline", JavaFormatting.STRIKETHROUGH: "strikethrough",
    }

    def append_text(text: str, style: TextSegment) -> None:
        # JSON text may still contain Legacy § / & codes. Parse only this flat
        # string, without simplify, keeping its changes local to this content.
        current = style
        for token in Motd.parse(text).parsed:
            if isinstance(token, str):
                if token:
                    segments.append(replace(current, text=token))
            elif isinstance(token, JavaMinecraftColor):
                current = TextSegment(text="", color=MC_COLOR_CODES[token.value])
            elif token is JavaFormatting.RESET:
                current = style
            elif isinstance(token, JavaFormatting) and token in legacy_fields:
                current = replace(current, **{legacy_fields[token]: True})

    def visit(component: Any, parent: TextSegment) -> TextSegment:
        if isinstance(component, str):
            append_text(component, parent)
            return parent
        if isinstance(component, list):
            if component:
                # A Java component array uses its first element as the parent
                # of the remaining elements; this is distinct from extra[].
                first_style = visit(component[0], parent)
                for child in component[1:]:
                    visit(child, first_style)
                return first_style
            return parent
        if not isinstance(component, dict):
            return parent
        overrides = {
            field_name: component[key] for key, field_name in style_fields.items()
            if isinstance(component.get(key), bool)
        }
        if component.get("color") is not None:
            color = component["color"]
            overrides["color"] = (WebColor.from_hex(color).rgb if str(color).startswith("#")
                                  else parse_mc_color(color))
        style = replace(parent, **overrides)
        if isinstance(component.get("text"), str):
            append_text(component["text"], style)
        # Translation-only content stays ignored, matching the previous renderer.
        for child in component.get("extra", []):
            visit(child, style)
        return style

    visit(raw, TextSegment(text=""))
    return segments


def parse_motd(description: Motd | str | list[Any] | dict[str, Any]) -> list[MotdLine]:
    """将服务端 Java MOTD 转换为图片渲染模型。

    Legacy 字符串使用 mcstatus 规范化后生成的原版 ``§`` 代码交给 Legacy
    解析器；JSON component 递归读取原始结构，直接写入 RGB 和继承样式。
    两条路径只在逐行渲染模型上汇合，避免把 WebColor 伪装为分节符文本。
    """
    raw = description.raw if isinstance(description, Motd) else description
    if isinstance(raw, str):
        parsed = description if isinstance(description, Motd) else Motd.parse(raw)
        segments = parse_section_sign_text(parsed.simplify().to_minecraft())
    else:
        segments = _parse_component_motd(raw)
    return _split_motd_lines(segments)


def parse_custom_note_text(text: str) -> list[TextSegment]:
    """
    解析自定义备注文本，支持:
    1. § 分节符格式代码
    2. <color:#hex>...</color> 标签语法
    """
    # 先解析 <color:#hex> 标签
    result_segments: list[TextSegment] = []
    pattern = re.compile(r"<color:([^>]+)>(.*?)</color>", re.DOTALL)

    last_end = 0
    for match in pattern.finditer(text):
        # 标签前的文本
        if match.start() > last_end:
            prefix = text[last_end:match.start()]
            result_segments.extend(parse_section_sign_text(prefix))

        color_str = match.group(1).strip()
        inner_text = match.group(2)
        color = parse_mc_color(color_str)

        # 内部文本可能还包含 § 代码
        inner_segments = parse_section_sign_text(inner_text)
        for seg in inner_segments:
            if seg.color is None:
                seg.color = color
            result_segments.append(seg)

        last_end = match.end()

    # 剩余文本
    if last_end < len(text):
        remaining = text[last_end:]
        result_segments.extend(parse_section_sign_text(remaining))

    return result_segments


_query_limiters: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


async def query_server(host: str, timeout: float = 5) -> ServerSnapshot:
    """SRV lookup and status share one deadline; failures never imply offline.

    A per-event-loop limiter also bounds concurrent calls from separate chats.
    mcstatus keeps the raw component MOTD and the real sample UUIDs intact.
    """
    loop = asyncio.get_running_loop()
    limiter = _query_limiters.setdefault(loop, asyncio.Semaphore(4))
    try:
        async with asyncio.timeout(max(0.05, float(timeout))):
            async with limiter:
                server = await JavaServer.async_lookup(host, timeout=float(timeout))
                status = await server.async_status(version=767)
                sample = status.players.sample or []
                return ServerSnapshot(
                    host=host,
                    players=[PlayerInfo(str(player.name), str(player.id) if getattr(player, "id", None) else None)
                             for player in sample],
                    online=int(status.players.online), max=int(status.players.max),
                    version=str(status.version.name), latency=round(float(status.latency), 1),
                    icon=status.icon, motd=parse_motd(status.motd),
                )
    except TimeoutError:
        return ServerSnapshot(host=host, error="查询超时")
    except Exception as error:
        # No connection success was observed, so show unknown rather than offline.
        return ServerSnapshot(host=host, error=f"查询失败（{type(error).__name__}）")
