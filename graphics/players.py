"""Measure faces and text together before drawing player rows."""
from PIL import Image
from ..models import TextSegment
from ..query import parse_section_sign_text

FACE_SIZE = 24
FACE_GAP = 6
ITEM_GAP = 14
DISPLAY_LIMIT = 8


def prepare_rows(draw, names, online, font, width, max_rows=2, faces=None):
    from . import get_img as tools
    from .wzbcmc import _fit, _width
    if not names:
        text = "暂无玩家在线" if online == 0 else "未公开玩家名单" if online is not None else "玩家信息未知"
        segments = [TextSegment(text="在线玩家：" + text)]
        return [{"items": [{"x": 0, "segments": segments, "face": None}], "width": _width(draw, segments, font)}]
    max_rows = max(1, min(3, max_rows))
    prefix = [TextSegment(text="在线玩家：")]
    prefix_width = _width(draw, prefix, font)
    rows, consumed = [], 0
    names = list(names)
    for row_index in range(max_rows):
        items = [{"x": 0, "segments": prefix, "face": None}] if row_index == 0 else []
        cursor = prefix_width if row_index == 0 else 0
        while consumed < min(len(names), DISPLAY_LIMIT):
            raw_name = names[consumed]
            # Native callers key by sample index so duplicate display names
            # cannot overwrite one another's UUID-derived face.
            face = faces.get(consumed, faces.get(raw_name)) if faces is not None else None
            avatar_width = FACE_SIZE + FACE_GAP if face is not None else 0
            gap = ITEM_GAP if consumed and cursor else 0
            remaining = len(names) - consumed - 1
            reaches_limit = consumed + 1 == DISPLAY_LIMIT
            tail = [TextSegment(text=f" · +{remaining}")] if remaining and (row_index == max_rows - 1 or reaches_limit) else []
            tail_width = _width(draw, tail, font)
            # First player on a row can truncate, so even a very long name has
            # a measured face and text rather than falling outside the card.
            room = width - cursor - gap - avatar_width - tail_width
            if room < font.size:
                break
            name = _fit(draw, parse_section_sign_text(" ".join(str(raw_name).split())),
                        font, min(180, room))
            name_width = _width(draw, name, font)
            if cursor + gap + avatar_width + name_width + tail_width > width:
                break
            items.append({"x": cursor + gap, "segments": name, "face": face})
            cursor += gap + avatar_width + name_width
            consumed += 1
        if row_index == max_rows - 1 and consumed < len(names):
            tail = [TextSegment(text=f" · +{len(names) - consumed}")]
            items.append({"x": cursor, "segments": tail, "face": None})
            cursor += _width(draw, tail, font)
        rows.append({"items": items, "width": cursor})
        if consumed >= min(len(names), DISPLAY_LIMIT):
            if consumed < len(names) and row_index < max_rows - 1:
                tail = [TextSegment(text=f" · +{len(names) - consumed}")]
                items.append({"x": cursor, "segments": tail, "face": None})
                cursor += _width(draw, tail, font)
                rows[-1]["width"] = cursor
            break
    return rows


def draw_rows(image, draw, x, y, rows, font, color):
    from . import get_img as tools
    line_height = max(font.size + 6, FACE_SIZE + 4)
    for row in rows:
        for item in row["items"]:
            item_x = x + item["x"]
            if item["face"] is not None:
                face = item["face"].resize((FACE_SIZE, FACE_SIZE), Image.Resampling.NEAREST)
                image.paste(face, (int(item_x), int(y)), face)
                item_x += FACE_SIZE + FACE_GAP
            tools.draw_segments(draw, item_x, y + max(0, (FACE_SIZE - font.size) // 2 - 2),
                                item["segments"], font, color)
        y += line_height
    return y
