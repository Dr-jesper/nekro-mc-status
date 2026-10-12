"""Framework-free Pillow rendering; CPU work runs outside the bot event loop."""
import asyncio
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from .avatars import AvatarClient, default_client
from .graphics import get_img as tools
from .graphics import shoumc, wzbcmc
from .graphics.players import DISPLAY_LIMIT, FACE_SIZE, prepare_rows, draw_rows
from .models import ServerSnapshot, TextSegment
from .query import parse_custom_note_text

# Upstream font helpers have mutable font selection. Serialize only the CPU
# render section, so concurrent chats cannot leak their configured font.
_render_lock = threading.Lock()


def _legacy_card(snapshot, profile, faces):
    """Preserve original rich/simple palette, straight icons and column layouts."""
    simple = profile == "simple"
    background = (34, 34, 34) if simple else (15, 15, 15)
    foreground = (255, 255, 255) if simple else (220, 220, 220)
    accent, muted = (85, 255, 85), (170, 170, 170)
    padding, width, icon_size = (20, 940, 64) if simple else (30, 940, 100)
    title_font = tools.load_render_font_sync(30 if simple else 26)
    text_font = tools.load_render_font_sync(20 if simple else 22)
    small_font = tools.load_render_font_sync(18)
    title_h, text_h = title_font.size + 8, text_font.size + 8
    draw = ImageDraw.Draw(Image.new("RGB", (width, 1)))
    icon = asyncio.run(tools.fetch_icon(snapshot.icon))
    name_x = padding + icon_size + 15 if icon else padding
    right = width - padding
    count = f"{snapshot.online if snapshot.online is not None else '?'} / {snapshot.max if snapshot.max is not None else '?'}"
    version = snapshot.version or "版本未知"
    latency = f"{snapshot.latency:g} ms" if snapshot.latency is not None else "延迟未知"
    right_width = max(tools.measure_text(draw, value, text_font, bold=True)
                      for value in (count, version, latency))
    available = max(100, right - right_width - 20 - name_x)
    name_rows = wzbcmc._wrap(draw, [TextSegment(snapshot.server_name or snapshot.host, bold=True)], title_font, available)
    motd_rows = []
    if simple:
        motd_rows = wzbcmc._wrap(draw, [TextSegment("版本：" + version + "  地址：" + snapshot.host)], text_font,
                                right - name_x)
    else:
        for line in snapshot.motd:
            motd_rows.extend(wzbcmc._wrap(draw, line.segments, text_font, available))
        motd_rows = wzbcmc._limited_lines(draw, motd_rows, text_font, available, 4)
    header_h = max(icon_size, len(name_rows) * title_h + max(len(motd_rows), 2) * text_h)
    note = snapshot.error or snapshot.note
    notes = (wzbcmc._wrap(draw, [TextSegment("查询失败：" if snapshot.error else "备注：")]
                         + parse_custom_note_text(note), small_font, width - padding * 2)
             if note else [])
    rows = [] if notes else prepare_rows(draw, [p.name for p in snapshot.players], snapshot.online,
                                         small_font, width - padding * 2, 3 if not simple else 2, faces)
    bottom_h = len(notes) * 26 if notes else len(rows) * max(26, FACE_SIZE + 4)
    height = padding + header_h + 16 + (text_h if simple else 0) + bottom_h + padding
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    if icon:
        resized = icon.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
        image.paste(resized, (padding, padding), resized)
    y = padding
    for row in name_rows:
        tools.draw_segments(draw, name_x, y, row, title_font, accent if simple else (255, 255, 255))
        y += title_h
    for row in motd_rows:
        tools.draw_segments(draw, name_x, y, row, text_font, foreground)
        y += text_h
    metric_y = padding if not simple else padding + header_h + 6
    tools.draw_segments_right_aligned(draw, right, metric_y, [TextSegment(count, bold=True)], text_font, accent)
    if not simple:
        tools.draw_segments_right_aligned(draw, right, metric_y + title_h, [TextSegment(version, bold=True)], text_font, muted)
    latency_color = (85, 255, 85) if snapshot.latency is not None and snapshot.latency < 100 else (255, 170, 0)
    if not simple:
        tools.draw_segments_right_aligned(draw, right, metric_y + title_h + text_h,
                                         [TextSegment(latency, bold=True)], text_font, latency_color)
    else:
        tools.draw_segments(draw, name_x, metric_y, [TextSegment("在线玩家")], text_font, accent)
        tools.draw_segments(draw, padding, metric_y, [TextSegment(latency)], text_font, latency_color)
    y = padding + header_h + 16 + (text_h if simple else 0)
    if notes:
        for row in notes:
            tools.draw_segments(draw, padding, y, row, small_font, muted)
            y += 26
    else:
        draw_rows(image, draw, padding, y, rows, small_font, foreground)
    if simple:
        draw.rounded_rectangle((10, 10, width - 10, height - 10), radius=10, outline=accent, width=2)
    return image


def _render_sync(profile, snapshots, query_time, output_path, font_path, faces_by_server):
    with _render_lock:
        tools.set_custom_font_path(font_path)
        try:
            cards = []
            for index, snapshot in enumerate(snapshots):
                if profile in ("wzbcmc", "shoumc"):
                    renderer = wzbcmc if profile == "wzbcmc" else shoumc
                    cfg = {"display": {"show_notes": bool(snapshot.note or snapshot.error)},
                           "layout": {"max_player_rows": 2}}
                    extra = {"light": index % 2 == 1} if profile == "shoumc" else {}
                    card = asyncio.run(renderer.render_card(
                        [p.name for p in snapshot.players], snapshot.latency,
                        snapshot.server_name or snapshot.host, snapshot.max, snapshot.online,
                        snapshot.version, icon_base64=snapshot.icon, host_address=snapshot.host,
                        preset=cfg, motd_lines=snapshot.motd,
                        note_text=snapshot.error or snapshot.note,
                        status="unknown" if snapshot.error or any(value is None for value in
                               (snapshot.online, snapshot.max, snapshot.latency)) else "online",
                        player_faces=faces_by_server[index], **extra))
                else:
                    card = _legacy_card(snapshot, profile, faces_by_server[index])
                cards.append(card)
            if profile in ("wzbcmc", "shoumc"):
                renderer = wzbcmc if profile == "wzbcmc" else shoumc
                image = renderer.compose_cards(cards, query_time)
            else:
                background = (34, 34, 34) if profile == "simple" else (15, 15, 15)
                header, footer, gap = (0, 30, 10) if profile == "simple" else (70, 30, 0)
                image = Image.new("RGB", (940, header + sum(c.height for c in cards) +
                                          gap * (len(cards) - 1) + footer), background)
                draw = ImageDraw.Draw(image)
                if header:
                    font = tools.load_render_font_sync(44)
                    tools.draw_segments_centered(draw, 470, 16, [TextSegment("Minecraft Server Status", bold=True)], font, (255, 255, 255))
                y = header
                for card in cards:
                    image.paste(card, (0, y))
                    y += card.height + gap
                tools.draw_segments_right_aligned(draw, 930, image.height - 24,
                    [TextSegment(query_time.strftime("%Y-%m-%d %H:%M:%S"), bold=True)],
                    tools.load_render_font_sync(18), (120, 120, 120))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path, format="PNG")
            return output_path
        finally:
            tools.set_custom_font_path(None)


async def render_profile(profile: str, snapshots: list[ServerSnapshot], query_time: datetime,
                         output_path: Path, *, font_path: str | None = None,
                         show_avatars: bool = True, avatar_client: AvatarClient | None = None) -> Path:
    profile = {"wzbc": "wzbcmc", "shou": "shoumc"}.get(profile, profile)
    if profile not in {"wzbcmc", "shoumc", "rich", "simple"}:
        raise ValueError("Unknown render profile")
    if not snapshots:
        raise ValueError("At least one server snapshot is required")
    faces_by_server = []
    client = avatar_client or default_client
    async def server_faces(snapshot):
        if not show_avatars or snapshot.error or snapshot.note:
            return None
        players = snapshot.players[:DISPLAY_LIMIT]
        faces = await asyncio.gather(*(client.face(player) for player in players))
        return {index: face for index, face in enumerate(faces)}
    # Skin resolution is capped at eight supplied names per server; it never
    # invents identities from online counts. HTTP failures yield grey defaults.
    faces_by_server = await asyncio.gather(*(server_faces(s) for s in snapshots))
    return await asyncio.to_thread(_render_sync, profile, snapshots, query_time,
                                   Path(output_path), font_path, faces_by_server)
