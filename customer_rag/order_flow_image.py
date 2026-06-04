from __future__ import annotations

import hashlib
import re
from pathlib import Path


def create_order_flow_image(
    product_info: str,
    order_flow: str,
    other_note: str = "",
    *,
    source_path: Path,
    row_number: int,
    output_root: Path,
) -> str | None:
    cells = [
        ("产品信息", _clean_cell_text(product_info)),
        ("下单流程", _clean_cell_text(order_flow)),
        ("其他说明", _clean_cell_text(other_note)),
    ]
    if not any(value for _, value in cells):
        return None

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ModuleNotFoundError:
        return None

    font_path = _font_path()
    header_font = ImageFont.truetype(font_path, 22) if font_path else ImageFont.load_default()
    font = ImageFont.truetype(font_path, 21) if font_path else ImageFont.load_default()

    width = 1320
    column_widths = [360, 610, 350]
    header_height = 46
    padding_x = 18
    padding_y = 16
    line_height = 30
    line_gap = 6
    border = (210, 218, 230)
    header_fill = (242, 245, 249)
    header_text = (65, 72, 86)

    wrapped_cells = [
        (header, _wrap_preserving_lines(value or " ", max_visual=max(8, int((column_widths[index] - padding_x * 2) / 11))))
        for index, (header, value) in enumerate(cells)
    ]
    content_height = max(
        72,
        max(len(lines) * (line_height + line_gap) - line_gap + padding_y * 2 for _, lines in wrapped_cells),
    )
    height = header_height + content_height

    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width - 1, height - 1), outline=border, width=1)
    draw.rectangle((0, 0, width - 1, header_height), fill=header_fill, outline=border, width=1)

    x = 0
    for index, (header, lines) in enumerate(wrapped_cells):
        column_width = column_widths[index]
        right = x + column_width
        if index > 0:
            draw.line((x, 0, x, height), fill=border, width=1)
        draw.text((x + padding_x, 12), header, font=header_font, fill=header_text)

        y = header_height + padding_y
        for line in lines:
            draw.text((x + padding_x, y), line, font=font, fill=_line_color(line))
            y += line_height + line_gap
        x = right

    digest_text = "|".join(value for _, value in cells)
    digest = hashlib.sha1(f"{source_path}:{row_number}:{digest_text}".encode("utf-8")).hexdigest()[:16]
    safe_stem = _safe_name(source_path.stem)
    target_dir = output_root / safe_stem
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"row-{row_number}-{digest}.png"
    image.save(target)
    return str(target)


def _clean_cell_text(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _wrap_preserving_lines(text: str, *, max_visual: int) -> list[str]:
    wrapped: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            wrapped.append("")
            continue
        wrapped.extend(_wrap_line(line, max_visual=max_visual))
    return wrapped


def _wrap_line(text: str, *, max_visual: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        if _visual_len(current + char) > max_visual:
            if current:
                lines.append(current)
            current = char
        else:
            current += char
    if current:
        lines.append(current)
    return lines


def _visual_len(text: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in text)


def _line_color(line: str) -> tuple[int, int, int]:
    if "确认收货后" in line:
        return (198, 55, 55)
    if "权益" in line or "返" in line or "券" in line:
        return (29, 128, 78)
    if "http://" in line or "https://" in line:
        return (42, 112, 216)
    if "下单价" in line or "付款不高于" in line:
        return (204, 72, 45)
    return (44, 49, 60)


def _font_path() -> str | None:
    candidates = (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def _safe_name(value: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" .")
    return safe[:80] or "order-flow"
