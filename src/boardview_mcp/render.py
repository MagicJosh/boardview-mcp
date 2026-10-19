"""render_area: a PNG of one board side around a part, oriented like OpenBoardView."""
import io

from PIL import Image, ImageDraw, ImageFont

from .board import Board, BoardError, _lookup, _part, _r

SIZE = 1024  # px, square
MAX_RADIUS_MM = 100.0
NET_LABELS_UP_TO_MM = 5.0
PAD_MM = 0.15  # part outline margin around its pins
BG, EDGE, PART, DNP, TARGET = (18, 18, 22), (90, 90, 105), (175, 175, 175), (105, 105, 105), (255, 200, 0)
PIN, GND, NET_HL, TEXT, NET_TEXT = (205, 125, 45), (70, 145, 70), (0, 210, 255), (235, 235, 235), (140, 180, 225)


def to_px(x: float, y: float, center: tuple[float, float], px_per_mm: float, side: str) -> tuple[float, float]:
    """Board mm to image px. Y points up; the bottom side is mirrored in X, like OBV's flip."""
    mirror = -1 if side == "bottom" else 1
    return SIZE / 2 + mirror * (x - center[0]) * px_per_mm, SIZE / 2 - (y - center[1]) * px_per_mm


def _scale_bar_mm(radius_mm: float) -> float:
    """Largest round length that fits in a fifth of the image width."""
    fits = [s for s in (0.5, 1, 2, 5, 10, 20, 50) if s <= radius_mm * 2 / 5]
    return fits[-1] if fits else 0.5


def _dashed_rect(d: ImageDraw.ImageDraw, box: tuple[float, float, float, float], color, dash: int = 5) -> None:
    x0, y0, x1, y1 = box
    for x in range(int(x0), int(x1), 2 * dash):
        end = min(x + dash, x1)
        d.line([(x, y0), (end, y0)], fill=color)
        d.line([(x, y1), (end, y1)], fill=color)
    for y in range(int(y0), int(y1), 2 * dash):
        end = min(y + dash, y1)
        d.line([(x0, y), (x0, end)], fill=color)
        d.line([(x1, y), (x1, end)], fill=color)


def render_area(b: Board, refdes: str, radius_mm: float = 5.0, side: str | None = None,
                net: str | None = None) -> tuple[bytes, dict]:
    """PNG bytes and a caption for the square of +-radius_mm around a part's center."""
    if not 0 < radius_mm <= MAX_RADIUS_MM:
        raise BoardError(f"radius_mm must be greater than 0 and at most {MAX_RADIUS_MM:g}")
    target = _part(b, refdes)
    if target.center is None:
        raise BoardError(f"{target.refdes} has no pins, so it has no position")
    side = side or target.side
    if side not in ("top", "bottom"):
        raise BoardError("side must be 'top' or 'bottom'")
    net_name = _lookup("net", net, b.nets, b._netnames) if net else None

    cx, cy = center = target.center
    s = SIZE / (2 * radius_mm)
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    label_font = ImageFont.load_default(size=max(9, min(16, int(s * 0.8))))
    net_font = ImageFont.load_default(size=11)

    for x1, y1, x2, y2 in b.outline:
        d.line([to_px(x1, y1, center, s, side), to_px(x2, y2, center, s, side)], fill=EDGE, width=2)

    drawn = 0
    for part in b.parts.values():
        box = part.bbox
        if part.side != side or box is None:
            continue
        x0, y0, x1, y1 = box[0] - PAD_MM, box[1] - PAD_MM, box[2] + PAD_MM, box[3] + PAD_MM
        if x1 < cx - radius_mm or x0 > cx + radius_mm or y1 < cy - radius_mm or y0 > cy + radius_mm:
            continue
        drawn += 1
        (ax, ay), (bx, by) = to_px(x0, y0, center, s, side), to_px(x1, y1, center, s, side)
        rect = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
        if part is target:
            d.rectangle(rect, outline=TARGET, width=3)
        elif part.dnp()[0]:
            _dashed_rect(d, rect, DNP)
        else:
            d.rectangle(rect, outline=PART, width=1)
        pin_r = max(2.0, 0.12 * s)
        for p in part.pins:
            px, py = to_px(p.x, p.y, center, s, side)
            color = NET_HL if p.net == net_name else GND if p.net == "GND" else PIN
            d.ellipse([px - pin_r, py - pin_r, px + pin_r, py + pin_r], fill=color)
            if radius_mm <= NET_LABELS_UP_TO_MM:
                d.text((px + pin_r + 2, py), p.net, fill=NET_TEXT, font=net_font, anchor="lm")
        d.text(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), part.refdes,
               fill=TARGET if part is target else TEXT, font=label_font, anchor="mm")

    title = "TOP" if side == "top" else "BOTTOM (mirrored, as in OBV)"
    d.rectangle((0, 0, SIZE, 62), fill=BG)
    d.text((14, 8), title, fill=TEXT, font=ImageFont.load_default(size=26))
    subtitle = f"{target.refdes}   radius {radius_mm:g} mm" + (f"   net {net_name}" if net_name else "")
    d.text((14, 40), subtitle, fill=NET_TEXT, font=ImageFont.load_default(size=15))

    bar_mm = _scale_bar_mm(radius_mm)
    bx0, by0, bx1 = 20, SIZE - 28, 20 + bar_mm * s
    d.rectangle((0, SIZE - 60, bx1 + 90, SIZE), fill=BG)
    d.line([(bx0, by0), (bx1, by0)], fill=TEXT, width=4)
    for tick in (bx0, bx1):
        d.line([(tick, by0 - 8), (tick, by0 + 8)], fill=TEXT, width=2)
    d.text((bx1 + 10, by0), f"{bar_mm:g} mm", fill=TEXT, font=ImageFont.load_default(size=16), anchor="lm")

    out = io.BytesIO()
    img.save(out, "PNG")
    caption = {"refdes": target.refdes, "side": side.upper(), "center": [_r(cx), _r(cy)], "radius_mm": radius_mm,
               "scale_bar_mm": bar_mm, "px_per_mm": _r(s), "parts_drawn": drawn, "net": net_name}
    return out.getvalue(), caption
