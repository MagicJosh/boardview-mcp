"""Read ASUS .fz boardview files: find the key, decrypt, split, inflate and parse.

Port of OpenBoardView 10.0.0 src/openboardview/FileFormats/FZFile.cpp. Read-only: the
board file is only ever opened in "rb" mode, and the key is never stored or returned.
"""
import array
import os
import re
import struct
import zlib

from .board import Board, Part, Pin

MILS_TO_MM = 0.0254
MAX_FILE_BYTES = 50 * 1024 * 1024
# FZFile::getKeyParity(): bit i is 1 when key word i has an even number of set bits.
KEY_PARITY = bytes([0, 1, 1, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0, 1, 0, 0,
                    0, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 1, 0, 1])
_M32 = 0xFFFFFFFF


class FzError(Exception):
    """A board that cannot be opened. Messages never contain key or file bytes."""


def _parity(word: int) -> int:
    return 1 - (word.bit_count() & 1)


def parse_key(text: str) -> tuple[int, ...] | None:
    """44 hex words as written in obv.conf ("0x1234abcd, 0x..."), or None if invalid."""
    words = tuple(int(h, 16) & _M32 for h in re.findall(r"0x[0-9a-fA-F]+", text))
    if len(words) != 44 or bytes(_parity(w) for w in words) != KEY_PARITY:
        return None
    return words


def _key_from_obv_conf(path: str | None) -> tuple[int, ...] | None:
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            name, sep, value = line.partition("=")
            if sep and name.strip() == "FZKey":
                return parse_key(value)
    return None


def _key_from_exe(path: str | None) -> tuple[int, ...] | None:
    """OBV release builds embed the key as a 4-byte aligned u32[44]; find it by its parity."""
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        blob = f.read()
    words = array.array("I", blob[: len(blob) // 4 * 4])  # native order, little-endian on Windows
    at = bytes(_parity(w) for w in words).find(KEY_PARITY)
    return tuple(words[at:at + 44]) if at >= 0 else None


def find_key(obv_conf: str | None, obv_exe: str | None) -> tuple[tuple[int, ...], str]:
    """Key and where it came from: BOARDVIEW_FZKEY, then obv.conf FZKey, then the OBV exe."""
    env = os.environ.get("BOARDVIEW_FZKEY")
    if env:
        key = parse_key(env)
        if key is None:
            raise FzError("BOARDVIEW_FZKEY is set but is not a valid 44-word FZ key")
        return key, "env BOARDVIEW_FZKEY"
    key = _key_from_obv_conf(obv_conf)
    if key:
        return key, "obv.conf"
    key = _key_from_exe(obv_exe)
    if key:
        return key, "openboardview.exe"
    raise FzError("no FZ key found: set FZKey in obv.conf, set BOARDVIEW_FZKEY, or pass --obv-exe")


def _rotl(a: int, b: int) -> int:
    b &= 31
    return ((a << b) | (a >> (32 - b))) & _M32 if b else a


def decrypt(key: tuple[int, ...], data: bytes) -> bytes:
    """FZFile::decode: RC6 rounds as a stream cipher keyed on the previous 16 cipher bytes."""
    # shortcut: pure Python, about 7 s for a 270 kB board; upgrade to numpy when load time matters.
    a = b = c = d = 0
    window = bytearray(16)
    out = bytearray(len(data))
    for pos, cipher in enumerate(data):
        b = (b + key[0]) & _M32
        d = (d + key[1]) & _M32
        for i in range(1, 21):
            t = _rotl((b * (2 * b + 1)) & _M32, 5)
            u = _rotl((d * (2 * d + 1)) & _M32, 5)
            a = (_rotl(a ^ t, u) + key[2 * i]) & _M32
            c = (_rotl(c ^ u, t) + key[2 * i + 1]) & _M32
            a, b, c, d = b, c, d, a
        a = (a + key[42]) & _M32
        out[pos] = cipher ^ (a & 0xFF)
        window = window[1:] + bytes([cipher])
        a, b, c, d = struct.unpack("<4I", window)
    return bytes(out)


def _inflate(data: bytes, what: str) -> str:
    z = zlib.decompressobj()
    try:
        raw = z.decompress(data)
    except zlib.error:
        raise FzError(f"could not inflate the {what} section: wrong key or not an ASUS .fz file") from None
    if not z.eof:
        raise FzError(f"the {what} section is truncated")
    return raw.decode("utf-8", errors="replace")


def _split(buf: bytes) -> tuple[str, str]:
    """[4 bytes][zlib content][zlib BOM][u32 LE BOM size]; FZFile::split."""
    if len(buf) < 12:
        raise FzError("file is too small to be an .fz board")
    bom_size = struct.unpack_from("<I", buf, len(buf) - 4)[0]
    if bom_size > len(buf):
        raise FzError("could not split the file: wrong key or not an ASUS .fz file")
    content_end = len(buf) - bom_size + 4
    return _inflate(buf[4:content_end], "board"), _inflate(buf[content_end:], "BOM")


def _num(field: str) -> float:
    try:
        return float(field.replace(",", "."))
    except ValueError:
        raise FzError("malformed number in the board data") from None


def _parse(path: str, content: str, bom: str, key_source: str) -> Board:
    scale = MILS_TO_MM
    parts: dict[str, Part] = {}
    outline: list[tuple[float, float, float, float]] = []
    block = None
    for line in content.splitlines():
        line = line.strip()
        if line == "UNIT:millimeters":
            scale = 1.0
        if line.startswith("A!"):
            block = line[2:].split("!", 1)[0]
            continue
        if not line.startswith("S!"):
            continue
        f = line[2:].split("!")
        if block == "REFDES" and len(f) >= 4:
            parts[f[0]] = Part(f[0], "bottom" if f[3] == "YES" else "top")
        elif block == "NET_NAME" and len(f) >= 6 and f[1] in parts:
            part = parts[f[1]]
            # FZFile::parse: an empty or "0" pin number means the pin name is the identifier.
            pin_id = f[3] if f[2] in ("", "0") else f[2]
            part.pins.append(Pin(part.refdes, pin_id, f[3], f[0],
                                 _num(f[4]) * scale, _num(f[5]) * scale, part.side))
        elif block == "GRAPHIC_DATA_NAME" and "OUTLINE" in f and len(f) >= 7:
            # shortcut: an ARC is drawn as the chord between its end points.
            outline.append(tuple(_num(v) * scale for v in f[3:7]))
        # LOGOInfo holds the converter's contact details and is never parsed.

    for line in bom.splitlines()[2:]:
        cols = line.split("\t")
        if len(cols) < 4 or line.lstrip().startswith("s"):
            continue
        locations = cols[3].split()
        joined = "_".join(locations)
        if joined in parts and not all(loc in parts for loc in locations):
            locations = [joined]  # BOM writes "CN SSD1" for the board's "CN_SSD1"
        for loc in locations:
            if loc in parts:
                parts[loc].description = cols[1].strip()

    if not parts:
        raise FzError("no parts found: wrong key or not an ASUS .fz file")
    return Board(os.path.basename(path), key_source, parts, outline,
                 "millimeters" if scale == 1.0 else "mils")


def load(path: str, obv_conf: str | None = None, obv_exe: str | None = None) -> Board:
    if not path.lower().endswith(".fz") or not os.path.isfile(path):
        raise FzError("open_board only reads existing .fz files")
    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise FzError("file is larger than 50 MB")
    with open(path, "rb") as f:
        raw = f.read()
    if raw[4:5] == b"\x78" and raw[5:6] in (b"\x9c", b"\xda"):
        return _parse(path, *_split(raw), "none (file is not encrypted)")
    key, source = find_key(obv_conf, obv_exe)
    return _parse(path, *_split(decrypt(key, raw)), source)
