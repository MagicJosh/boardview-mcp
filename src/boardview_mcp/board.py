"""In-memory board model and the read-only queries behind the MCP tools.

All positions are mm with the board file's origin, so they match OpenBoardView's
"Show position" readout. Every query returns a JSON-ready dict.
"""
import collections
import difflib
import fnmatch
import math
import re
from dataclasses import dataclass, field

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000
NET_SUMMARY_OVER = 50
MAX_PATTERN = 200
DC_DESCRIPTIONS = ("RES", "THERMISTOR", "IND", "BEAD", "FERRITE", "FUSE", "JUMP")
DC_PREFIXES = {"R", "PR", "L", "PL", "FB", "F", "PF", "PJ"}
OBV_NOTE = "OpenBoardView shows one more part and one more net: its dummy part and its UNCONNECTED net."


class BoardError(Exception):
    """A query the board cannot answer, such as an unknown refdes. Safe to show to the model."""


@dataclass
class Pin:
    part: str
    pin: str
    name: str
    net: str
    x: float  # mm
    y: float  # mm
    side: str


@dataclass
class Part:
    refdes: str
    side: str
    pins: list[Pin] = field(default_factory=list)
    description: str | None = None  # None: no BOM row; "": BOM row with an empty description

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        if not self.pins:
            return None
        xs = [p.x for p in self.pins]
        ys = [p.y for p in self.pins]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def center(self) -> tuple[float, float] | None:
        box = self.bbox
        return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if box else None

    def dnp(self) -> tuple[bool | None, str | None]:
        """(dnp_likely, dnp_reason). Blank BOM description = not fitted, except on pads."""
        if self.description:
            return False, None
        if not self.pins:
            return None, "no pins"
        if len(self.pins) == 1:
            return None, "1-pin pad"
        return True, "empty BOM description" if self.description == "" else "no BOM row"


@dataclass
class Board:
    file: str
    key_source: str
    parts: dict[str, Part]
    outline: list[tuple[float, float, float, float]]  # x1, y1, x2, y2 segments, mm
    units_in_file: str = "mils"
    nets: dict[str, list[Pin]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.nets = {}
        for part in self.parts.values():
            for pin in part.pins:
                self.nets.setdefault(pin.net, []).append(pin)
        self._refdes = {r.upper(): r for r in self.parts}
        self._netnames = {n.upper(): n for n in self.nets}


def _r(v: float) -> float:
    return round(v, 3)


def _capped(key: str, rows: list, limit: int) -> dict:
    limit = max(1, min(int(limit), MAX_LIMIT))
    return {key: rows[:limit], "total": len(rows), "truncated": len(rows) > limit}


def _lookup(kind: str, name: str, exact: dict, index: dict[str, str]) -> str:
    """Exact name first, then case-insensitive, else an error with close matches."""
    name = str(name).strip()
    if name in exact:
        return name
    key = name.upper()
    if key in index:
        return index[key]
    close = difflib.get_close_matches(key, index, n=5)
    hint = f"; did you mean: {', '.join(index[c] for c in close)}" if close else ""
    raise BoardError(f"unknown {kind} '{name}'{hint}")


def _part(b: Board, refdes: str) -> Part:
    return b.parts[_lookup("refdes", refdes, b.parts, b._refdes)]


def _pin(part: Part, pin: str) -> Pin:
    key = str(pin).strip().upper()
    for attr in ("pin", "name"):
        for p in part.pins:
            if getattr(p, attr).upper() == key:
                return p
    shown = ", ".join(p.pin for p in part.pins[:20]) + (", ..." if len(part.pins) > 20 else "")
    raise BoardError(f"{part.refdes} has no pin '{pin}' (pins: {shown})")


def _matcher(pattern: str):
    """Case-insensitive glob, or a regex search when the pattern starts with 're:'."""
    if len(pattern) > MAX_PATTERN:
        raise BoardError(f"pattern is longer than {MAX_PATTERN} characters")
    if pattern.startswith("re:"):
        try:
            rx = re.compile(pattern[3:], re.IGNORECASE)
        except re.error as e:
            raise BoardError(f"bad regex: {e}") from None
        return lambda s: rx.search(s) is not None
    glob = pattern.upper()
    return lambda s: fnmatch.fnmatchcase(s.upper(), glob)


def _prefix(refdes: str) -> str:
    return re.match(r"[A-Z]*", refdes.upper()).group() or refdes


def dc_path(part: Part) -> tuple[bool | None, str | None]:
    """(dc_path, dc_reason) from the BOM description (resistor, 0 ohm, inductor, bead, fuse, jumper).

    A blank description gives (None, hint): the refdes prefix says what the part would do if
    fitted, but it is only a hint and never makes dc_path true.
    """
    desc = (part.description or "").upper()
    if desc:
        return desc.startswith(DC_DESCRIPTIONS), None
    prefix = _prefix(part.refdes)
    verdict = "would conduct" if prefix in DC_PREFIXES else "would not conduct"
    return None, f"unknown, probably not fitted; {verdict} if fitted ({prefix} prefix)"


def _pin_row(p: Pin) -> dict:
    row = {"pin": p.pin, "net": p.net, "x": _r(p.x), "y": _r(p.y)}
    if p.name != p.pin:
        row["name"] = p.name
    return row


def board_info(b: Board) -> dict:
    pins = [p for part in b.parts.values() for p in part.pins]
    xs = [v for s in b.outline for v in (s[0], s[2])] or [p.x for p in pins] or [0.0]
    ys = [v for s in b.outline for v in (s[1], s[3])] or [p.y for p in pins] or [0.0]
    flags = [part.dnp()[0] for part in b.parts.values()]
    return {
        "file": b.file,
        "format": "ASUS .fz (RC6 stream cipher + zlib)",
        "key_source": b.key_source,
        "units_in_file": b.units_in_file,
        "units": "mm",
        "coordinates": "same origin as OpenBoardView 'Show position' (mm)",
        "parts": len(b.parts),
        "pins": len(pins),
        "nets": len(b.nets),
        "parts_top": sum(p.side == "top" for p in b.parts.values()),
        "parts_bottom": sum(p.side == "bottom" for p in b.parts.values()),
        "board_size_mm": [_r(max(xs) - min(xs)), _r(max(ys) - min(ys))],
        "board_bbox_mm": [_r(min(xs)), _r(min(ys)), _r(max(xs)), _r(max(ys))],
        "dnp_flagged": sum(f is True for f in flags),
        "dnp_unknown": sum(f is None for f in flags),
        "dnp_rule": "dnp_likely is true for an empty BOM description on a part with 2+ pins, null for 1-pin pads",
        "note": OBV_NOTE,
    }


def get_part(b: Board, refdes: str, limit: int = DEFAULT_LIMIT) -> dict:
    part = _part(b, refdes)
    dnp, reason = part.dnp()
    center, box = part.center, part.bbox
    return {
        "refdes": part.refdes,
        "side": part.side,
        "center": [_r(v) for v in center] if center else None,
        "bbox": [_r(v) for v in box] if box else None,
        "description": part.description,
        "dnp_likely": dnp,
        "dnp_reason": reason,
        **_capped("pins", [_pin_row(p) for p in part.pins], limit),
    }


def find_parts(b: Board, pattern: str, limit: int = DEFAULT_LIMIT) -> dict:
    match = _matcher(pattern)
    rows = [{"refdes": p.refdes, "side": p.side, "description": p.description, "dnp_likely": p.dnp()[0]}
            for p in b.parts.values() if match(p.refdes) or (p.description and match(p.description))]
    return {"pattern": pattern, **_capped("parts", rows, limit)}


def get_net(b: Board, net: str, full: bool = False, limit: int = DEFAULT_LIMIT) -> dict:
    name = _lookup("net", net, b.nets, b._netnames)
    pins = b.nets[name]
    per_part = collections.Counter(p.part for p in pins)
    head = {"net": name, "pin_count": len(pins), "part_count": len(per_part)}
    if len(pins) > NET_SUMMARY_OVER and not full:
        return {
            **head,
            "summary": True,
            "pins_top": sum(p.side == "top" for p in pins),
            "pins_bottom": sum(p.side == "bottom" for p in pins),
            "parts_by_prefix": dict(collections.Counter(_prefix(r) for r in per_part).most_common(10)),
            "most_pins_on_net": per_part.most_common(10),
            "hint": "pass full=true for the pin list",
        }
    rows = [{"refdes": p.part, "pin": p.pin, "side": p.side, "x": _r(p.x), "y": _r(p.y)} for p in pins]
    return {**head, **_capped("pins", rows, limit)}


def find_nets(b: Board, pattern: str, limit: int = DEFAULT_LIMIT) -> dict:
    match = _matcher(pattern)
    rows = [{"net": n, "pin_count": len(ps)} for n, ps in b.nets.items() if match(n)]
    return {"pattern": pattern, **_capped("nets", rows, limit)}


def pin_net(b: Board, refdes: str, pin: str) -> dict:
    p = _pin(_part(b, refdes), pin)
    return {"refdes": p.part, "side": p.side, **_pin_row(p)}


def parts_near(b: Board, refdes: str | None = None, x: float | None = None, y: float | None = None,
               radius_mm: float = 5.0, side: str = "both", limit: int = DEFAULT_LIMIT) -> dict:
    """Parts with any pin within radius_mm of the center (a part's center, or x,y in mm)."""
    if side not in ("both", "top", "bottom"):
        raise BoardError("side must be 'both', 'top' or 'bottom'")
    if not radius_mm > 0:
        raise BoardError("radius_mm must be greater than 0")
    ref = None
    if refdes:
        ref = _part(b, refdes)
        if ref.center is None:
            raise BoardError(f"{ref.refdes} has no pins, so it has no position")
        cx, cy = ref.center
    elif x is not None and y is not None:
        cx, cy = x, y
    else:
        raise BoardError("give a refdes, or both x and y in mm")
    rows = []
    for part in b.parts.values():
        if part is ref or not part.pins or side not in ("both", part.side):
            continue
        d = min(math.hypot(p.x - cx, p.y - cy) for p in part.pins)
        if d <= radius_mm:
            rows.append({"refdes": part.refdes, "side": part.side, "distance_mm": _r(d),
                         "description": part.description, "dnp_likely": part.dnp()[0]})
    rows.sort(key=lambda r: r["distance_mm"])
    return {"center": [_r(cx), _r(cy)], "radius_mm": radius_mm, "side": side, **_capped("parts", rows, limit)}


def connection_path(b: Board, refdes_a: str, pin_a: str, refdes_b: str, pin_b: str,
                    limit: int = DEFAULT_LIMIT) -> dict:
    """Same net, or one hop through a 2-pin part. Bridges: fitted DC, fitted non-DC, likely unfitted."""
    pa = _pin(_part(b, refdes_a), pin_a)
    pb = _pin(_part(b, refdes_b), pin_b)
    bridges = []
    if pa.net != pb.net:
        for p in b.nets[pa.net]:
            part = b.parts[p.part]
            if len(part.pins) != 2:
                continue
            other = part.pins[1] if part.pins[0] is p else part.pins[0]
            if other.net == pb.net:
                dc, dc_reason = dc_path(part)
                dnp, dnp_reason = part.dnp()
                bridges.append({"refdes": part.refdes, "description": part.description, "dc_path": dc,
                                "dc_reason": dc_reason, "dnp_likely": dnp, "dnp_reason": dnp_reason,
                                "pins": [[p.pin, p.net], [other.pin, other.net]]})
        bridges.sort(key=lambda r: (r["dnp_likely"] is True, r["dc_path"] is not True))
    if pa.net == pb.net or any(r["dc_path"] for r in bridges):
        connected = True
    elif any(r["dnp_likely"] and _prefix(r["refdes"]) in DC_PREFIXES for r in bridges):
        connected = "possible"  # only a likely-unfitted part could close the path
    else:
        connected = False
    return {
        "a": {"refdes": pa.part, "pin": pa.pin, "net": pa.net},
        "b": {"refdes": pb.part, "pin": pb.pin, "net": pb.net},
        "direct": pa.net == pb.net,
        "connected": connected,
        **_capped("bridges", bridges, limit),
    }
