# SPDX-License-Identifier: AGPL-3.0-only
# Derived from Aer-Light-Work/astrbot_mcgetter_enhanced (薄暝, Aer and contributors).
# Modified for Nekro MC Status in 2026; see NOTICE.md.
"""服务器状态图片的字体回退、文本绘制与布局实现。"""

import base64
import io
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
import logging
logger = logging.getLogger(__name__)

from ..query import (
    TextSegment, MotdLine, parse_custom_note_text, parse_section_sign_text, MC_COLOR_CODES
)

# 自定义字体文件路径（可通过 set_custom_font_path 设置，默认为空使用系统加载逻辑）
_custom_font_path: Optional[str] = None
_custom_bold_font_path: Optional[str] = None

# 内置字体优先：移植时随包带入 Sarasa UI SC（拉丁 + 简体中文）。
# 背景：容器内原本没有任何 CJK 字体，只能退化为"符号字体画 ASCII + UniFont 画中文"，
# 两者字距与基线不同，表现为数字间距过大、整体下陷（2026-10-11 实测）。
_RESOURCE_DIR = Path(__file__).resolve().parent.parent / "resources"
_BUNDLED_FONT_CANDIDATES = (
    _RESOURCE_DIR / "SarasaUiSC-Regular.ttf",
    _RESOURCE_DIR / "SarasaUiSC-SemiBold.ttf",
)

# 系统字体兜底（三平台常见 CJK 路径）
_SYSTEM_NOTO_FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/Deng.ttf",
    "C:/Windows/Fonts/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/NotoSansCJK-Regular.ttc",
    "/Library/Fonts/NotoSans-Regular.ttf",
)
_SYMBOL_FONT_PATH = Path(__file__).resolve().parent.parent / "resources" / "NotoSansSymbols2-Regular.ttf"


class UniFontHex:
    """GNU UniFont .hex 的按需位图字形读取器。"""

    def __init__(self, path: Path):
        self.path = path
        self._offsets: Dict[int, int] = {}
        self._glyphs: Dict[int, Tuple[int, int, bytes]] = {}
        self._indexed = False

    def _ensure_index(self) -> None:
        if self._indexed:
            return
        try:
            with self.path.open("rb") as source:
                while True:
                    offset = source.tell()
                    line = source.readline()
                    if not line:
                        break
                    codepoint, separator, _ = line.partition(b":")
                    if separator:
                        try:
                            self._offsets[int(codepoint, 16)] = offset
                        except ValueError:
                            pass
        except OSError as error:
            logger.warning(f"UniFont 索引加载失败: {self.path}: {error}")
        self._indexed = True

    def glyph(self, char: str) -> Optional[Tuple[int, int, bytes]]:
        codepoint = ord(char)
        self._ensure_index()
        if codepoint not in self._offsets:
            return None
        if codepoint not in self._glyphs:
            try:
                with self.path.open("rb") as source:
                    source.seek(self._offsets[codepoint])
                    _, hex_data = source.readline().decode("ascii").strip().split(":", 1)
                data = bytes.fromhex(hex_data)
                # UniFont .hex 的每行固定 16 行，高度为 16；每行字节数决定宽度。
                if len(data) % 16:
                    return None
                self._glyphs[codepoint] = (len(data) // 16 * 8, 16, data)
            except (OSError, UnicodeDecodeError, ValueError):
                return None
        return self._glyphs[codepoint]


_unifont = UniFontHex(Path(__file__).resolve().parent.parent / "resources" / "unifont_all-17.0.05.hex")


class RenderFont:
    """一个字号下的主字体、符号字体与 UniFont 回退集合。"""

    def __init__(
        self,
        regular: ImageFont.ImageFont,
        bold: Optional[ImageFont.ImageFont],
        size: int,
        use_unifont: bool = False,
        symbol: Optional[ImageFont.ImageFont] = None,
    ):
        self.regular = regular
        self.bold = bold
        self.size = size
        self.use_unifont = use_unifont
        self.symbol = symbol

    @staticmethod
    def _has_glyph(font: ImageFont.ImageFont, char: str) -> bool:
        if char.isspace():
            return True
        try:
            # Pillow 对未覆盖字符会给出与 U+FFFF 相同的 .notdef mask。
            mask = font.getmask(char)
            missing_mask = font.getmask("\uffff")
            return (mask.size, bytes(mask)) != (missing_mask.size, bytes(missing_mask))
        except Exception:
            return False

    def source_for(self, char: str, bold: bool = False) -> str:
        font = self.bold if bold and self.bold is not None else self.regular
        if not self.use_unifont and self._has_glyph(font, char):
            return "ttf-bold" if bold and self.bold is not None else "ttf"
        # 符号字体只用于非 ASCII 字符：ASCII 若落到符号字体上，字距（实测 '1' 宽 35px）与
        # 基线和 UniFont 完全不同，混排即"数字间距过大 + 整体下陷"。
        if ord(char) > 0x7F and self.symbol is not None and self._has_glyph(self.symbol, char):
            return "symbol"
        return "unifont" if _unifont.glyph(char) else "ttf"

    def advance(self, char: str, bold: bool = False) -> int:
        source = self.source_for(char, bold)
        if source == "unifont":
            glyph = _unifont.glyph(char)
            return max(1, round(glyph[0] * self.size / 16)) if glyph else 0
        font = self.symbol if source == "symbol" else self.bold if source == "ttf-bold" else self.regular
        width = int(round(font.getlength(char)))
        # 没有真实粗体文件时，draw_segments 会在右侧复制一个整数像素。
        if bold and (source == "symbol" or self.bold is None):
            width += 1
        return width

    def ascent(self) -> int:
        try:
            return self.regular.getmetrics()[0]
        except Exception:
            return self.size


def set_custom_font_path(
    font_path: Optional[str], bold_font_path: Optional[str] = None, heavier_font_weight: bool = False,
) -> None:
    """
    设置自定义字体路径，None/空字符串 则恢复系统默认加载逻辑
    bold_font_path: 粗体字体文件路径（可选）。未指定时自动在同目录下查找 SemiBold/Bold 变体
    heavier_font_weight: 开启时以 SemiBold 作为常规字体、Bold 作为粗体字体
    """
    global _custom_font_path, _custom_bold_font_path
    if font_path:
        original_path = str(font_path)
        _custom_font_path = (
            _find_font_variant(original_path, ("SemiBold",)) if heavier_font_weight else None
        ) or original_path
        if bold_font_path:
            _custom_bold_font_path = str(bold_font_path)
        elif heavier_font_weight:
            _custom_bold_font_path = _find_font_variant(original_path, ("Bold",))
        else:
            _custom_bold_font_path = _guess_bold_font_path(original_path)
    else:
        _custom_font_path = None
        _custom_bold_font_path = None


def _find_font_variant(regular_path: str, variants: Tuple[str, ...]) -> Optional[str]:
    """从同一字体族中查找指定字重的非斜体字体文件。"""
    try:
        base = Path(regular_path)
        directory = base.parent
        if not directory.exists():
            return None
        prefix = base.stem.replace("-Regular", "").replace("Regular", "").strip("-")
        candidates = []
        for variant in variants:
            candidates.extend(directory.glob(f"*{prefix}*{variant}*"))
        for candidate in sorted(set(candidates)):
            if candidate.suffix.lower() not in {".ttf", ".ttc", ".otf"}:
                continue
            if "italic" not in candidate.stem.lower():
                return str(candidate)
    except Exception:
        pass
    return None


def _guess_bold_font_path(regular_path: str) -> Optional[str]:
    """从常规字体路径猜测同目录下的粗体变体（SemiBold / Bold）"""
    return _find_font_variant(regular_path, ("SemiBold", "Bold"))


def _load_system_noto_font(font_size: int) -> Optional[ImageFont.ImageFont]:
    """加载系统安装的 Noto Sans；未安装时返回 ``None``。"""
    for path in _SYSTEM_NOTO_FONT_PATHS:
        try:
            return ImageFont.truetype(path, font_size)
        except OSError:
            continue
    return None


def _load_bundled_font(font_size: int, bold: bool = False) -> Optional[ImageFont.ImageFont]:
    """加载随包字体（含中文）。粗体优先取同族 Bold/SemiBold 变体。"""
    candidates = list(_BUNDLED_FONT_CANDIDATES)
    if bold and _BUNDLED_FONT_CANDIDATES:
        variant = _find_font_variant(str(_BUNDLED_FONT_CANDIDATES[0]), ("Bold", "SemiBold"))
        if variant:
            candidates.insert(0, Path(variant))
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), font_size)
        except (OSError, ValueError):
            continue
    return None


def _load_pillow_default_font(font_size: int) -> ImageFont.ImageFont:
    """提供仅用于 UniFont 缺字时的 Pillow 最小回退字体。"""
    try:
        return ImageFont.load_default(size=font_size)
    except TypeError:
        return ImageFont.load_default()


def _load_symbol_font(font_size: int) -> Optional[ImageFont.ImageFont]:
    """加载随插件携带的 Noto Sans Symbols 2 矢量符号字体。"""
    try:
        return ImageFont.truetype(_SYMBOL_FONT_PATH, font_size)
    except OSError as error:
        logger.warning(f"符号字体加载失败: {_SYMBOL_FONT_PATH}: {error}")
        return None


def _load_font_with_source(
    font_size: int,
    bold: bool = False,
) -> tuple[ImageFont.ImageFont, bool]:
    """返回字体及是否应由 UniFont 作为默认渲染来源。"""
    # 粗体：优先使用粗体字体文件
    if bold and _custom_bold_font_path:
        try:
            return ImageFont.truetype(_custom_bold_font_path, font_size), False
        except OSError as e:
            logger.warning(f"粗体字体加载失败: {_custom_bold_font_path}: {e}，回退到常规字体")

    # 用户自定义字体路径优先
    if _custom_font_path:
        try:
            return ImageFont.truetype(_custom_font_path, font_size), False
        except OSError as e:
            logger.warning(f"自定义字体加载失败: {_custom_font_path}: {e}，回退到系统 Noto Sans")

    bundled_font = _load_bundled_font(font_size, bold=bold)
    if bundled_font is not None:
        return bundled_font, False

    system_font = _load_system_noto_font(font_size)
    if system_font is not None:
        return system_font, False

    logger.debug("未找到系统 Noto Sans，使用内置 UniFont 渲染文本")
    return _load_pillow_default_font(font_size), True


async def load_font(font_size: int, bold: bool = False) -> ImageFont.ImageFont:
    """加载主字体；缺少 Noto Sans 时返回 UniFont 的最小 Pillow 配套字体。"""
    return _load_font_with_source(font_size, bold)[0]


def load_render_font_sync(font_size: int) -> RenderFont:
    """同步加载渲染字体，供柱状图等同步绘制器复用同一回退策略。"""
    regular, use_unifont = _load_font_with_source(font_size)
    bold, bold_uses_unifont = _load_font_with_source(font_size, bold=True)
    if use_unifont or bold_uses_unifont:
        bold = None
        use_unifont = True
    elif bold is regular or getattr(bold, "path", None) == getattr(regular, "path", None):
        bold = None
    return RenderFont(
        regular, bold, font_size,
        use_unifont=use_unifont,
        symbol=_load_symbol_font(font_size),
    )


async def load_render_font(font_size: int) -> RenderFont:
    """加载一组用于实际绘制、测量与 UniFont 回退的字体。"""
    return load_render_font_sync(font_size)


async def fetch_icon(icon_base64: Optional[str] = None) -> Optional[Image.Image]:
    """处理Base64编码的服务器图标，无图标时返回默认图标占位"""
    if not icon_base64:
        return load_default_icon()

    try:
        # 去除可能的Base64前缀
        if "," in icon_base64:
            icon_base64 = icon_base64.split(",", 1)[1]
        if len(icon_base64) > 350_000:
            raise ValueError("Server icon is too large")
        icon_data = base64.b64decode(icon_base64, validate=True)
        with Image.open(io.BytesIO(icon_data)) as source:
            if source.width > 512 or source.height > 512:
                raise ValueError("Server icon dimensions are too large")
            return source.convert("RGBA")
    except Exception as e:
        logger.warning(f"Base64 图标解码失败: {e}")
        return load_default_icon()


def load_default_icon() -> Optional[Image.Image]:
    """加载默认占位图标"""
    try:
        path = Path(__file__).resolve().parent.parent / 'resources' / 'default_icon.png'
        return Image.open(path).convert("RGBA")
    except Exception as e:
        logger.warning(f"默认图标加载失败: {e}")
        return None


def _as_render_font(font: ImageFont.ImageFont | RenderFont) -> RenderFont:
    return font if isinstance(font, RenderFont) else RenderFont(font, None, getattr(font, "size", 16))


def measure_text(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont | RenderFont, bold: bool = False,
) -> int:
    """以最终实际使用的字体（含 UniFont 回退）测量文本宽度。"""
    render_font = _as_render_font(font)
    return sum(render_font.advance(char, bold) for char in text)


def _seg_width(draw: ImageDraw.ImageDraw, seg: TextSegment, font: ImageFont.ImageFont | RenderFont) -> int:
    """按最终粗体/回退字体测量单个文本段。"""
    return measure_text(draw, seg.text, font, seg.bold)


def _font_runs(text: str, font: RenderFont, bold: bool) -> Iterable[Tuple[str, str]]:
    """按实际字体来源合并连续字符，避免逐字符调用 Pillow。"""
    current_source: Optional[str] = None
    current_text = ""
    for char in text:
        source = font.source_for(char, bold)
        if current_source is not None and source != current_source:
            yield current_source, current_text
            current_text = ""
        current_source = source
        current_text += char
    if current_source is not None:
        yield current_source, current_text


def _draw_unifont_char(
    image: Image.Image, x: int, y: int, char: str, font: RenderFont, color: Tuple[int, int, int], bold: bool,
) -> int:
    glyph = _unifont.glyph(char)
    if glyph is None:
        return 0
    width, height, data = glyph
    bitmap = Image.frombytes("1", (width, height), data)
    scaled_width = max(1, round(width * font.size / 16))
    scaled_height = max(1, round(height * font.size / 16))
    # UniFont 是 16px 位图；先转灰度掩膜并高质量缩放，避免 ✦ 等 Unicode
    # 符号在非原始字号下被最近邻放大成明显锯齿。
    bitmap = bitmap.convert("L").resize(
        (scaled_width, scaled_height), Image.Resampling.LANCZOS,
    )
    # UniFont 的 16px 字高以基线为参考，与当前 TrueType 字体基线对齐。
    glyph_y = y + font.ascent() - scaled_height
    colored = Image.new("RGB", bitmap.size, color)
    image.paste(colored, (x, glyph_y), bitmap)
    if bold:
        image.paste(colored, (x + 1, glyph_y), bitmap.convert("L"))
    return scaled_width


def draw_segments(
    draw: ImageDraw.ImageDraw,
    x: int, y: int,
    segments: List[TextSegment],
    font: ImageFont.ImageFont | RenderFont,
    default_color: Tuple[int, int, int],
    bold_font: Optional[ImageFont.ImageFont] = None,
) -> int:
    """
    绘制带格式的文本段，返回绘制后的 x 坐标
    粗体优先使用粗体字体文件（bold_font），否则回退描边模拟；下划线/删除线用线条绘制
    """
    render_font = _as_render_font(font)
    if bold_font is not None:
        render_font.bold = bold_font
    image = draw._image
    current_x = x
    for seg in segments:
        if not seg.text:
            continue

        color = seg.color if seg.color else default_color
        segment_x = current_x

        for source, run in _font_runs(seg.text, render_font, seg.bold):
            if source == "unifont":
                for char in run:
                    current_x += _draw_unifont_char(image, current_x, y, char, render_font, color, seg.bold)
                continue
            active_font = (
                render_font.symbol if source == "symbol"
                else render_font.bold if source == "ttf-bold"
                else render_font.regular
            )
            if seg.bold and (source == "symbol" or (source == "ttf" and render_font.bold is None)):
                # 不使用 stroke_width：描边会在字形四周制造抗锯齿光晕。
                # 仅向右复制一个整数像素，模拟增加字重并保持边缘收敛。
                draw.text((current_x, y), run, font=active_font, fill=color)
                draw.text((current_x + 1, y), run, font=active_font, fill=color)
            else:
                draw.text((current_x, y), run, font=active_font, fill=color)
            current_x += measure_text(draw, run, render_font, seg.bold)

        text_width = _seg_width(draw, seg, render_font)

        # 下划线
        if seg.underline:
            baseline_y = y + render_font.size - 2
            draw.line(
                [(segment_x, baseline_y), (segment_x + text_width, baseline_y)],
                fill=color, width=1
            )

        # 删除线
        if seg.strikethrough:
            mid_y = y + render_font.size // 2
            draw.line(
                [(segment_x, mid_y), (segment_x + text_width, mid_y)],
                fill=color, width=1
            )

    return current_x


def draw_segments_centered(
    draw: ImageDraw.ImageDraw,
    center_x: int, y: int,
    segments: List[TextSegment],
    font: ImageFont.ImageFont | RenderFont,
    default_color: Tuple[int, int, int],
    bold_font: Optional[ImageFont.ImageFont] = None,
) -> int:
    """居中绘制文本段"""
    total_width = sum(_seg_width(draw, seg, font) for seg in segments if seg.text)
    start_x = center_x - total_width // 2
    return draw_segments(draw, start_x, y, segments, font, default_color, bold_font)


def draw_segments_right_aligned(
    draw: ImageDraw.ImageDraw,
    right_x: int, y: int,
    segments: List[TextSegment],
    font: ImageFont.ImageFont | RenderFont,
    default_color: Tuple[int, int, int],
    bold_font: Optional[ImageFont.ImageFont] = None,
) -> int:
    """右对齐绘制文本段"""
    total_width = sum(_seg_width(draw, seg, font) for seg in segments if seg.text)
    start_x = right_x - total_width
    return draw_segments(draw, start_x, y, segments, font, default_color, bold_font)


def wrap_segments_to_lines(
    draw: ImageDraw.ImageDraw,
    segments: List[TextSegment],
    font: ImageFont.ImageFont | RenderFont,
    max_width: int,
) -> List[List[TextSegment]]:
    """将文本段按最大宽度折行"""
    lines: List[List[TextSegment]] = []
    current_line: List[TextSegment] = []
    current_width = 0

    for seg in segments:
        if not seg.text:
            continue
        seg_width = _seg_width(draw, seg, font)

        if current_width + seg_width <= max_width:
            current_line.append(seg)
            current_width += seg_width
        else:
            if current_line:
                lines.append(current_line)
            # 如果单个段就超过宽度，按字符折行
            if seg_width > max_width:
                remaining = seg.text
                while remaining:
                    chunk = ""
                    chunk_w = 0
                    for ch in remaining:
                        ch_w = measure_text(draw, ch, font, seg.bold)
                        if chunk_w + ch_w <= max_width:
                            chunk += ch
                            chunk_w += ch_w
                        else:
                            break
                    if not chunk:
                        chunk = remaining[0]
                    lines.append([TextSegment(
                        text=chunk, color=seg.color, bold=seg.bold,
                        italic=seg.italic, underline=seg.underline,
                        strikethrough=seg.strikethrough,
                    )])
                    remaining = remaining[len(chunk):]
                current_line = []
                current_width = 0
            else:
                current_line = [seg]
                current_width = seg_width

    if current_line:
        lines.append(current_line)

    return lines


def split_segments_by_newline(segments: List[TextSegment]) -> List[List[TextSegment]]:
    """将带 \\n 的 segments 按换行符拆分为多行，保留各段颜色/格式"""
    lines: List[List[TextSegment]] = []
    current: List[TextSegment] = []

    for seg in segments:
        if "\n" in seg.text:
            parts = seg.text.split("\n")
            for i, part in enumerate(parts):
                if part:
                    current.append(TextSegment(
                        text=part,
                        color=seg.color,
                        bold=seg.bold,
                        italic=seg.italic,
                        underline=seg.underline,
                        strikethrough=seg.strikethrough,
                    ))
                if i < len(parts) - 1:
                    if current:
                        lines.append(current)
                        current = []
        else:
            current.append(seg)

    if current:
        lines.append(current)

    return lines
