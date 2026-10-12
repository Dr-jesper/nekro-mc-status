"""Native SHOUMC cards; themes and avatars are drawn once by display order."""

import base64
import copy
import io
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

from ..query import TextSegment
from .wzbcmc import _helpers, _wrap, _fit, _width, _limited_lines, _draw_vanilla_brand

ASSET_DIR = Path(__file__).resolve().parents[1] / "resources" / "shoumc"
DEFAULT_PRESET = {
    "name": "SHOUMC 米白酒红", "style": "shoumc",
    "title": "SHOUMC · 服务器状态",
    "subtitle": "上海海洋大学 · 潮涌核心社",
    "colors": {
        "primary": [116, 49, 62], "dark": [93, 69, 62],
        "paper": [253, 249, 243], "light": [249, 239, 228],
        "text_dark": [28, 28, 24], "muted": [83, 67, 68],
        "on_dark": [253, 249, 243], "muted_on_dark": [216, 193, 195],
        "border": [216, 193, 195], "motd_background": [93, 38, 50],
    },
    "display": {
        "show_icon": True, "show_version": True, "show_address": True,
        "show_latency": True, "show_players": True, "show_motd": True,
        "show_notes": False, "show_query_time": True,
    },
    "fonts": {"title_size": 28, "text_size": 22, "small_size": 18},
    "layout": {
        "width": 940, "padding": 20, "gap": 12, "icon_size": 56,
        "header_height": 88, "footer_height": 32,
        "max_player_rows": 1, "max_motd_lines": 2,
    },
}

def _config(preset=None):
    cfg = copy.deepcopy(DEFAULT_PRESET)
    for key, value in (preset or {}).items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


def _pack_runs(rows):
    return [[{"text": seg.text, "color": seg.color, "bold": seg.bold,
              "italic": seg.italic, "underline": seg.underline,
              "strikethrough": seg.strikethrough} for seg in row] for row in rows]


def _unpack_runs(rows):
    return [[TextSegment(**{**seg, "color": tuple(seg["color"]) if seg["color"] is not None else None})
             for seg in row] for row in rows]


def _prepare(players_list, latency, server_name, plays_max, plays_online,
             server_version, icon, host_address, cfg, motd_lines, note_text, status, player_faces=None):
    """Measure once and keep only the runs and icon actually shown on the card."""
    from ..query import parse_custom_note_text

    from .players import prepare_rows, FACE_SIZE
    tools = _helpers()
    layout, display, sizes = (cfg[key] for key in ("layout", "display", "fonts"))
    width = max(400, min(2400, int(layout["width"])))
    padding = max(12, min(int(layout["padding"]), width // 8))
    x, right = padding + 18, width - padding - 18
    available = right - x
    fonts = [max(10, min(64, int(sizes[key]))) for key in ("title_size", "text_size", "small_size")]
    title_font, text_font, small_font = [tools.load_render_font_sync(size) for size in fonts]
    title_h, text_h, small_h = [size + 6 for size in fonts]
    measure = ImageDraw.Draw(Image.new("RGB", (width, 1)))
    icon_size = min(80, max(32, int(layout["icon_size"]))) if icon else 0
    icon_data = None
    if icon:
        icon = icon.copy()
        icon.thumbnail((icon_size, icon_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        icon.save(buffer, format="PNG")
        icon_data = base64.b64encode(buffer.getvalue()).decode("ascii")
    name_x = x + icon_size + 14 if icon else x
    if status is None:
        status = "online" if all(value is not None for value in (latency, plays_online, plays_max)) else "unknown"
    elif status not in ("online", "offline", "unknown"):
        status = "unknown"
    state = {"online": "运行中", "offline": "离线"}.get(status, "状态未知")
    badge_width = int(tools.measure_text(measure, state, small_font) + 34)
    name_width = max(title_font.size, right - badge_width - 16 - name_x)
    name = _wrap(measure, [TextSegment(text=str(server_name or "未命名服务器"), bold=True)], title_font, name_width)
    address = _wrap(measure, [TextSegment(text=f"地址：{host_address or '未知'}")], small_font, right - name_x) if display["show_address"] else []
    top_height = max(icon_size, len(name) * title_h + len(address) * small_h)
    metrics = [("在线人数", f"{plays_online if plays_online is not None else '?'} / {plays_max if plays_max is not None else '?'}")]
    if display["show_version"]:
        metrics.append(("版本", str(server_version or "未知")))
    if display["show_latency"]:
        metrics.append(("延迟", f"{latency} ms" if latency is not None else "未知"))
    column_width = (available - 20 * (len(metrics) - 1)) // len(metrics)
    # Labels receive the theme's muted colour when drawn; explicit MOTD and
    # nickname colours stay in their original TextSegment runs.
    metric_rows = [_wrap(measure, [TextSegment(text=label + " "), TextSegment(text=value, bold=True)], text_font, column_width)
                   for label, value in metrics]
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
    sparse = not notes and not any(display[key] for key in (
        "show_icon", "show_address", "show_version", "show_latency", "show_players", "show_motd"))
    height = max(130 if sparse else 190, 16 + top_height + 12 + metrics_height
                 + (len(motd) * text_h + 12 if motd else 0)
                 + (len(bottom) * small_h + 10 if bottom else
                    len(avatar_rows) * max(small_h, FACE_SIZE + 4) + 10 if avatar_rows else 0) + 18)
    return {"width": width, "height": height, "padding": padding, "fonts": fonts,
            "icon_size": icon_size, "icon": icon_data, "name_x": name_x,
            "top_height": top_height, "badge_width": badge_width, "status": status,
            "column_width": column_width, "metrics_height": metrics_height,
            "name": _pack_runs(name), "address": _pack_runs(address),
            "metrics": [_pack_runs(rows) for rows in metric_rows],
            "motd": _pack_runs(motd), "bottom": _pack_runs(bottom), "avatar_rows": avatar_rows}


def _draw_card(model, cfg, light=False):
    tools, colors = _helpers(), cfg["colors"]
    width, height, padding = model["width"], model["height"], model["padding"]
    x, right = padding + 18, width - padding - 18
    fonts = [tools.load_render_font_sync(size) for size in model["fonts"]]
    title_font, text_font, small_font = fonts
    title_h, text_h, small_h = [font.size + 6 for font in fonts]
    foreground = tuple(colors["text_dark"] if light else colors["on_dark"])
    muted = tuple(colors["muted"] if light else colors["muted_on_dark"])
    image = Image.new("RGB", (width, height), tuple(colors["paper"]))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((padding, 2, width - padding, height - 2), radius=18,
                           fill=tuple(colors["light"] if light else colors["dark"]),
                           outline=tuple(colors["border"]), width=1)
    y = 16
    if model["icon"]:
        with Image.open(io.BytesIO(base64.b64decode(model["icon"], validate=True))) as source:
            icon = source.convert("RGBA")
        image.paste(icon, (x + (model["icon_size"] - icon.width) // 2,
                           y + (model["icon_size"] - icon.height) // 2), icon)
    for row in _unpack_runs(model["name"]):
        tools.draw_segments(draw, model["name_x"], y, row, title_font, foreground)
        y += title_h
    for row in _unpack_runs(model["address"]):
        tools.draw_segments(draw, model["name_x"], y, row, small_font, muted)
        y += small_h
    state, state_color = {
        "online": ("运行中", (178, 220, 181)),
        "offline": ("离线", (255, 182, 180)),
    }.get(model["status"], ("状态未知", (235, 218, 203)))
    badge_x = right - model["badge_width"]
    draw.rounded_rectangle((badge_x, 20, right, 46), radius=13, fill=tuple(colors["primary"]))
    draw.ellipse((badge_x + 10, 29, badge_x + 16, 35), fill=state_color)
    tools.draw_segments(draw, badge_x + 23, 21, [TextSegment(text=state)], small_font, state_color)
    y = 16 + model["top_height"] + 12
    for index, rows in enumerate(model["metrics"]):
        metric_x = x + index * (model["column_width"] + 20)
        for line_index, row in enumerate(_unpack_runs(rows)):
            for seg in row:
                if not seg.bold:
                    seg.color = muted
            tools.draw_segments(draw, metric_x, y + line_index * text_h, row, text_font, foreground)
    y += model["metrics_height"]
    motd = _unpack_runs(model["motd"])
    if motd:
        y += 10
        # A dark inset on either palette keeps white Minecraft text legible.
        # draw_segments uses each segment's RGB when explicitly supplied.
        draw.rounded_rectangle((x, y - 2, right, y + len(motd) * text_h + 2), radius=8, fill=tuple(colors["motd_background"]))
        for row in motd:
            tools.draw_segments(draw, x + 12, y, row, text_font, tuple(colors["on_dark"]))
            y += text_h
        y += 2
    if model["bottom"]:
        y += 10
        for row in _unpack_runs(model["bottom"]):
            tools.draw_segments(draw, x, y, row, small_font, muted)
            y += small_h
    if model.get("avatar_rows"):
        from .players import draw_rows
        draw_rows(image, draw, x, y + 10, model["avatar_rows"], small_font, muted)
    return image


async def render_card(players_list: list, latency: int, server_name: str,
                      plays_max: int, plays_online: int, server_version: str,
                      icon_base64: Optional[str] = None, host_address: Optional[str] = None,
                      preset: Optional[dict] = None, motd_lines: Optional[list] = None,
                      note_text: Optional[str] = None, status: Optional[str] = None,
                      player_faces: Optional[dict] = None, light: bool = False) -> Image.Image:
    cfg = _config(preset)
    icon = await _helpers().fetch_icon(icon_base64) if cfg["display"]["show_icon"] else None
    model = _prepare(players_list, latency, server_name, plays_max, plays_online,
                     server_version, icon, host_address, cfg, motd_lines, note_text, status, player_faces)
    return _draw_card(model, cfg, light=light)


def compose_cards(cards: list[Image.Image], query_time: datetime, preset: Optional[dict] = None) -> Image.Image:
    if not cards:
        raise ValueError("至少需要一张服务器状态图才能合并")
    cfg, bodies = _config(preset), cards
    tools, layout, colors = _helpers(), cfg["layout"], cfg["colors"]
    width, padding = max(card.width for card in bodies), int(layout["padding"])
    header, footer = max(80, int(layout["header_height"])), max(24, int(layout["footer_height"]))
    gap = max(0, int(layout["gap"]))
    height = header + sum(card.height for card in bodies) + gap * (len(bodies) - 1) + footer
    image = Image.new("RGB", (width, height), tuple(colors["paper"]))
    draw = ImageDraw.Draw(image)
    x = padding
    try:
        with Image.open(ASSET_DIR / "Conduit_JE1_BE1.png") as source:
            logo = source.convert("RGBA")
        bounds = logo.getchannel("A").getbbox()
        if bounds:
            logo = logo.crop(bounds)
        logo.thumbnail((52, 52), Image.Resampling.NEAREST)
        image.paste(logo, (x + (52 - logo.width) // 2, (header - logo.height) // 2), logo)
        x += 64
    except OSError:
        pass
    title_font, small_font = tools.load_render_font_sync(28), tools.load_render_font_sync(int(cfg["fonts"]["small_size"]))
    title = str(cfg.get("title") or DEFAULT_PRESET["title"])
    title_segments = [TextSegment(text=title, bold=True)]
    title_y = 12
    if title == DEFAULT_PRESET["title"]:
        try:
            brand_width = _draw_vanilla_brand(image, x, 52, tuple(colors["primary"]),
                                            int(cfg["fonts"]["title_size"]) + 4,
                                            width - padding - x - 10, text="SHOUMC")
            title_segments = [TextSegment(text="· 服务器状态", bold=True)]
            title_x = x + brand_width + 10
        except (OSError, ValueError, KeyError, IndexError):
            title_x = x
    else:
        title_x = x
    tools.draw_segments(draw, title_x, title_y, _fit(draw, title_segments, title_font, width - padding - title_x), title_font, tuple(colors["primary"]))
    subtitle = [TextSegment(text=str(cfg.get("subtitle") or DEFAULT_PRESET["subtitle"]))]
    tools.draw_segments(draw, x, 52, _fit(draw, subtitle, small_font, width - padding - x), small_font, tuple(colors["muted"]))
    y = header
    for card in bodies:
        image.paste(card, ((width - card.width) // 2, y))
        y += card.height + gap
    footer_y = height - footer + (footer - small_font.size) // 2 - 2
    time_segments = [TextSegment(text="查询时间 " + query_time.strftime("%Y-%m-%d %H:%M:%S"))] if cfg["display"]["show_query_time"] else []
    time_width = _width(draw, time_segments, small_font)
    tools.draw_segments(draw, padding, footer_y, _fit(draw, [TextSegment(text="shoumc.com")], small_font, max(20, width - padding * 2 - time_width - 20)), small_font, tuple(colors["muted"]))
    if time_segments:
        tools.draw_segments(draw, width - padding - time_width, footer_y, time_segments, small_font, tuple(colors["muted"]))
    image.info.update(shoumc_layout="v1", shoumc_body_top=str(header), shoumc_body_bottom=str(height - footer))
    return image
