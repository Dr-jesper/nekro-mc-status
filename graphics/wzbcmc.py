"""Compact WZBC MC cards using the existing font, icon and Minecraft text tools.

``render_card`` returns a headerless Pillow card. ``compose_cards`` owns one
brand bar and footer. Imports of the pure font helpers are lazy.
"""

import copy
import json
from dataclasses import replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

from ..query import TextSegment, parse_custom_note_text, parse_section_sign_text

ASSET_DIR = Path(__file__).resolve().parents[1] / "resources" / "wzbcmc"
DEFAULT_PRESET = {
    "name": "WZBC MC 紧凑紫色",
    "style": "wzbcmc",
    "title": "WZBC MC · 服务器状态",
    "colors": {
        "primary": [96, 25, 134], "dark": [48, 17, 63],
        "paper": [247, 245, 240], "lavender": [200, 175, 213],
        "text_dark": [33, 28, 36], "muted": [101, 93, 105],
    },
    "display": {
        "show_icon": True, "show_version": True, "show_address": True,
        "show_latency": True, "show_players": True, "show_motd": True,
        "show_notes": False, "show_query_time": True,
    },
    "fonts": {"title_size": 28, "text_size": 22, "small_size": 18},
    "layout": {
        "width": 940, "padding": 20, "gap": 12, "icon_size": 56,
        "header_height": 80, "footer_height": 32,
        "max_player_rows": 1, "max_motd_lines": 2,
    },
}


def _helpers():
    from . import get_img
    return get_img


def _config(preset=None):
    result = copy.deepcopy(DEFAULT_PRESET)
    for key, value in (preset or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(value)
        else:
            result[key] = value
    return result


@lru_cache(maxsize=16)
def _vanilla_brand_mask(text="WZBC MC"):
    """Read Java's real ASCII provider; retain its advance and pixel shapes."""
    font_dir = ASSET_DIR / "vanilla-font"
    definition = json.loads((font_dir / "default.json").read_text(encoding="utf-8"))
    provider = next((item for item in definition["providers"]
                     if item.get("type") == "bitmap"
                     and item.get("file") == "minecraft:font/ascii.png"), None)
    if provider is None:
        raise ValueError("Minecraft ASCII bitmap provider is missing")
    rows = provider["chars"]
    if not rows or not rows[0]:
        raise ValueError("Minecraft ASCII bitmap grid is empty")
    columns = len(rows[0])
    with Image.open(font_dir / "ascii.png") as source:
        alpha = source.convert("RGBA").getchannel("A")
    cell_width, cell_height = alpha.width // columns, alpha.height // len(rows)
    font_height = int(provider.get("height", 8))
    if (any(len(row) != columns for row in rows)
            or alpha.width % columns or alpha.height % len(rows)
            or cell_height != font_height):
        raise ValueError("Minecraft ASCII bitmap grid does not match its provider")
    positions = {char: (column, row_index) for row_index, row in enumerate(rows)
                 for column, char in enumerate(row) if char != "\0"}
    glyphs = []
    advance = 0
    for char in text:
        if char == " ":
            advance += 4  # Java default's space provider at the 8px base size.
            continue
        column, row = positions[char]
        glyph = alpha.crop((column * cell_width, row * cell_height,
                            (column + 1) * cell_width, (row + 1) * cell_height))
        bounds = glyph.getbbox()
        if bounds is None:
            raise ValueError(f"Minecraft brand glyph {char!r} is blank")
        # Java scans from the rightmost opaque column. Keep any left bearing,
        # then add its one-pixel tracking instead of using a monospaced cell.
        glyph_width = bounds[2]
        glyphs.append((advance, glyph.crop((0, 0, glyph_width, cell_height))))
        advance += glyph_width + 1
    mask = Image.new("L", (advance, cell_height))
    for x, glyph in glyphs:
        mask.paste(glyph, (x, 0))
    bounds = mask.getbbox()
    # Centre visible strokes vertically while retaining horizontal advances.
    return mask.crop((0, bounds[1], mask.width, bounds[3])), font_height


def _draw_vanilla_brand(image, x, header, color, font_size, max_width, text="WZBC MC"):
    mask, base_height = _vanilla_brand_mask(text)
    requested_scale = max(1, (font_size + base_height // 2) // base_height)
    scale = max(1, min(requested_scale, max(1, (header - 16) // base_height),
                       max(1, max_width // mask.width)))
    mask = mask.resize((mask.width * scale, mask.height * scale), Image.Resampling.NEAREST)
    image.paste(color, (x, (header - mask.height) // 2), mask)
    return mask.width


def _wrap(draw, segments, font, width):
    """Use the existing wrapper with character runs for fluid coloured text."""
    tools = _helpers()
    lines = []
    for source_line in tools.split_segments_by_newline(segments):
        chars = [replace(seg, text=char) for seg in source_line for char in seg.text]
        for row in tools.wrap_segments_to_lines(draw, chars, font, max(1, width)):
            compact = []
            for seg in row:
                if compact and replace(compact[-1], text="") == replace(seg, text=""):
                    compact[-1] = replace(compact[-1], text=compact[-1].text + seg.text)
                else:
                    compact.append(seg)
            lines.append(compact)
    return lines


def _width(draw, segments, font):
    return sum(_helpers().measure_text(draw, seg.text, font, seg.bold) for seg in segments)


def _fit(draw, segments, font, width, suffix="…"):
    result = [replace(seg) for seg in segments]
    if _width(draw, result, font) <= width:
        return result
    ending = TextSegment(text=suffix)
    while result and _width(draw, result + [ending], font) > width:
        result[-1] = replace(result[-1], text=result[-1].text[:-1])
        if not result[-1].text:
            result.pop()
    return result + [ending]


def _limited_lines(draw, lines, font, width, limit):
    if len(lines) <= limit:
        return lines
    selected = lines[:limit]
    selected[-1] = _fit(draw, selected[-1] + [TextSegment(text="…")], font, width)
    return selected


def _player_lines(draw, players, online, font, width, max_rows):
    """+N counts only real names omitted from the supplied sample."""
    if not players:
        text = "暂无玩家在线" if online == 0 else "未提供玩家名单" if online is not None else "玩家信息未知"
        return [[TextSegment(text=f"在线玩家：{text}")]]
    # Server samples may contain control whitespace; one nickname must stay in
    # one measured row. Normalising whitespace keeps Minecraft style codes.
    names = [
        _fit(draw, parse_section_sign_text(" ".join(str(name).split())), font, min(180, width // 2))
        for name in players
    ]
    rows = []
    consumed = 0
    for row_index in range(max_rows):
        row = [TextSegment(text="在线玩家：" if row_index == 0 else "")]
        in_row = 0
        while consumed < len(names):
            separator = [TextSegment(text=" · ")] if in_row else []
            candidate = row + separator + names[consumed]
            remaining = len(names) - consumed - 1
            tail = [TextSegment(text=f" · +{remaining}")] if remaining and row_index == max_rows - 1 else []
            if _width(draw, candidate + tail, font) > width:
                break
            row = candidate
            in_row += 1
            consumed += 1
        if row_index == max_rows - 1 and consumed < len(names):
            row += [TextSegment(text=f" · +{len(names) - consumed}")]
        rows.append(row)
        if consumed == len(names):
            break
    return rows


async def render_card(
    players_list: list, latency: int, server_name: str, plays_max: int,
    plays_online: int, server_version: str, icon_base64: Optional[str] = None,
    host_address: Optional[str] = None, preset: Optional[dict] = None,
    motd_lines: Optional[list] = None, note_text: Optional[str] = None,
    status: Optional[str] = None, player_faces: Optional[dict] = None,
) -> Image.Image:
    """Render supplied data; successful query callers provide populated values.

    The native adapter supplies status='unknown' on failed queries; absent
    measurements never imply offline.
    """
    from .players import prepare_rows, draw_rows, FACE_SIZE
    tools = _helpers()
    cfg = _config(preset)
    layout, display, sizes, colors = (cfg[key] for key in ("layout", "display", "fonts", "colors"))
    width = max(400, int(layout["width"]))
    padding = max(12, min(int(layout["padding"]), width // 8))
    x, right = padding + 18, width - padding - 18
    available = right - x
    title_font = tools.load_render_font_sync(int(sizes["title_size"]))
    text_font = tools.load_render_font_sync(int(sizes["text_size"]))
    small_font = tools.load_render_font_sync(int(sizes["small_size"]))
    title_h, text_h, small_h = title_font.size + 6, text_font.size + 6, small_font.size + 6
    measure = ImageDraw.Draw(Image.new("RGB", (width, 1)))
    white, muted = tuple(colors["paper"]), tuple(colors["lavender"])
    icon = await tools.fetch_icon(icon_base64) if display["show_icon"] else None
    icon_size = min(80, max(32, int(layout["icon_size"]))) if icon else 0
    name_x = x + icon_size + 14 if icon else x
    if status is None:
        status = "online" if all(value is not None for value in (latency, plays_online, plays_max)) else "unknown"
    state, state_color = {
        "online": ("运行中", (153, 229, 175)),
        "offline": ("离线", (255, 161, 164)),
    }.get(status, ("状态未知", (222, 205, 231)))
    badge_width = tools.measure_text(measure, state, small_font) + 34
    name_width = max(title_font.size, right - badge_width - 16 - name_x)
    name_lines = _wrap(measure, [TextSegment(text=str(server_name or "未命名服务器"), bold=True)], title_font, name_width)
    address_lines = []
    if display["show_address"]:
        address_lines = _wrap(measure, [TextSegment(text=f"地址：{host_address or '未知'}")], small_font, right - name_x)
    top_height = max(icon_size, len(name_lines) * title_h + len(address_lines) * small_h)

    metrics = [("在线人数", f"{plays_online if plays_online is not None else '?'} / {plays_max if plays_max is not None else '?'}")]
    if display["show_version"]:
        metrics.append(("版本", str(server_version or "未知")))
    if display["show_latency"]:
        metrics.append(("延迟", f"{latency} ms" if latency is not None else "未知"))
    column_width = (available - 20 * (len(metrics) - 1)) // len(metrics)
    metric_rows = [
        _wrap(measure, [TextSegment(text=label + " ", color=muted), TextSegment(text=value, bold=True)], text_font, column_width)
        for label, value in metrics
    ]
    metrics_height = max(len(rows) for rows in metric_rows) * text_h

    motd = []
    if display["show_motd"]:
        for line in motd_lines or []:
            motd.extend(_wrap(measure, line.segments, text_font, available - 24))
        motd = _limited_lines(measure, motd, text_font, available - 24, max(1, int(layout["max_motd_lines"])))
    notes = parse_custom_note_text(note_text) if note_text and display["show_notes"] else []
    avatar_rows = []
    if notes:
        bottom = _wrap(measure, [TextSegment(text="备注：")] + notes, small_font, available)
    elif display["show_players"]:
        avatar_rows = prepare_rows(measure, players_list or [], plays_online, small_font, available, max(1, min(2, int(layout["max_player_rows"]))), player_faces)
        bottom = []
    else:
        bottom = []
    motd_height = len(motd) * text_h + 12 if motd else 0
    bottom_height = (len(bottom) * small_h + 10 if bottom else
                     len(avatar_rows) * max(small_h, FACE_SIZE + 4) + 10 if avatar_rows else 0)
    sparse = not notes and not any(display[key] for key in (
        "show_icon", "show_address", "show_version", "show_latency", "show_players", "show_motd",
    ))
    min_height = 130 if sparse else 190
    height = max(min_height, 16 + top_height + 12 + metrics_height + motd_height + bottom_height + 18)
    image = Image.new("RGB", (width, height), tuple(colors["paper"]))
    draw = ImageDraw.Draw(image)
    # Subtle edge highlight; all decoration stays inside the compact card.
    draw.rounded_rectangle((padding, 2, width - padding, height - 2), radius=18, fill=tuple(colors["dark"]), outline=tuple(colors["lavender"]), width=1)
    draw.line((padding + 18, 3, width - padding - 18, 3), fill=tuple(colors["primary"]), width=2)
    y = 16
    if icon:
        resized = icon.copy()
        resized.thumbnail((icon_size, icon_size), Image.Resampling.LANCZOS)
        image.paste(resized, (x + (icon_size - resized.width) // 2, y + (icon_size - resized.height) // 2), resized)
    for row in name_lines:
        tools.draw_segments(draw, name_x, y, row, title_font, white)
        y += title_h
    for row in address_lines:
        tools.draw_segments(draw, name_x, y, row, small_font, muted)
        y += small_h
    badge_x = right - badge_width
    draw.rounded_rectangle((badge_x, 20, right, 46), radius=13, fill=(74, 43, 87))
    draw.ellipse((badge_x + 10, 29, badge_x + 16, 35), fill=state_color)
    tools.draw_segments(draw, badge_x + 23, 21, [TextSegment(text=state)], small_font, state_color)
    y = 16 + top_height + 12
    for index, rows in enumerate(metric_rows):
        metric_x = x + index * (column_width + 20)
        for line_index, row in enumerate(rows):
            tools.draw_segments(draw, metric_x, y + line_index * text_h, row, text_font, white)
    y += metrics_height
    if motd:
        y += 10
        draw.rounded_rectangle((x, y - 2, right, y + len(motd) * text_h + 2), radius=8, fill=(41, 14, 54))
        for row in motd:
            tools.draw_segments(draw, x + 12, y, row, text_font, white)
            y += text_h
        y += 2
    if bottom:
        y += 10
        for row in bottom:
            tools.draw_segments(draw, x, y, row, small_font, muted)
            y += small_h
    if avatar_rows:
        draw_rows(image, draw, x, y + 10, avatar_rows, small_font, muted)
    return image


def compose_cards(cards: list[Image.Image], query_time: datetime, preset: Optional[dict] = None) -> Image.Image:
    """Compose headerless cards, owning one brand bar and one footer."""
    if not cards:
        raise ValueError("至少需要一张服务器状态图才能合并")
    # Metadata allows callers to merge either bare child cards or standalone
    # PNGs without repeating branding. The ordinary /mc path uses bare cards.
    bodies = []
    for card in cards:
        top, bottom = 0, card.height
        if card.info.get("wzbcmc_layout") == "v1":
            try:
                top = int(card.info["wzbcmc_body_top"])
                bottom = int(card.info["wzbcmc_body_bottom"])
            except (KeyError, TypeError, ValueError):
                top, bottom = 0, card.height
        if 0 <= top < bottom <= card.height:
            bodies.append(card.crop((0, top, card.width, bottom)))
        else:
            bodies.append(card)
    cards = bodies
    tools = _helpers()
    cfg = _config(preset)
    layout, colors = cfg["layout"], cfg["colors"]
    width = max(card.width for card in cards)
    padding = int(layout["padding"])
    header = max(70, int(layout["header_height"]))
    footer = max(24, int(layout["footer_height"]))
    gap = max(0, int(layout["gap"]))
    height = header + sum(card.height for card in cards) + gap * (len(cards) - 1) + footer
    image = Image.new("RGB", (width, height), tuple(colors["paper"]))
    draw = ImageDraw.Draw(image)
    x = padding
    try:
        with Image.open(ASSET_DIR / "logo-symbol.png") as source:
            logo = source.convert("RGBA")
        logo.thumbnail((52, 52), Image.Resampling.LANCZOS)
        image.paste(logo, (x + (52 - logo.width) // 2, (header - logo.height) // 2), logo)
        x += 64
    except OSError:
        pass  # Missing branding never substitutes a redrawn logo.
    title_font = tools.load_render_font_sync(28)
    title = str(cfg.get("title") or DEFAULT_PRESET["title"])
    title_segments = [TextSegment(text=title, bold=True)]
    # Only the known Latin brand uses Java's bitmap. Chinese keeps full fallback.
    if title == DEFAULT_PRESET["title"]:
        try:
            brand_width = _draw_vanilla_brand(
                image, x, header, tuple(colors["primary"]), int(cfg["fonts"]["title_size"]) + 4,
                width - padding - x - 10 - tools.measure_text(draw, "…", title_font),
            )
            x += brand_width + 10
            title_segments = [TextSegment(text="· 服务器状态", bold=True)]
        except (OSError, ValueError, KeyError, IndexError):
            pass
    title_segments = _fit(draw, title_segments, title_font, width - padding - x)
    tools.draw_segments(draw, x, (header - title_font.size) // 2 - 2, title_segments, title_font, tuple(colors["primary"]))
    y = header
    for card in cards:
        image.paste(card, ((width - card.width) // 2, y))
        y += card.height + gap
    small_font = tools.load_render_font_sync(int(cfg["fonts"]["small_size"]))
    footer_y = height - footer + (footer - small_font.size) // 2 - 2
    time_segments = []
    if cfg["display"]["show_query_time"]:
        time_segments = [TextSegment(text="查询时间 " + query_time.strftime("%Y-%m-%d %H:%M:%S"))]
    time_width = _width(draw, time_segments, small_font)
    brand = _fit(draw, [TextSegment(text="wzbcmc.cn · 温州商学院 Minecraft 社团")], small_font, max(20, width - padding * 2 - time_width - 20))
    tools.draw_segments(draw, padding, footer_y, brand, small_font, tuple(colors["muted"]))
    if time_segments:
        tools.draw_segments(draw, width - padding - time_width, footer_y, time_segments, small_font, tuple(colors["muted"]))
    image.info["wzbcmc_layout"] = "v1"
    image.info["wzbcmc_body_top"] = str(header)
    image.info["wzbcmc_body_bottom"] = str(height - footer)
    return image
