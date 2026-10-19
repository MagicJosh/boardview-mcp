"""Shared fixtures. The real board is loaded once per session (about 7 s, pure-Python decrypt)."""
import hashlib
import os
import struct
import zlib

import pytest

from boardview_mcp import fz

_DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
FZ_PATH = os.environ.get("BOARDVIEW_TEST_FZ") or os.path.join(
    _DESKTOP, "Asus BIOS",
    "ASUS GA605WI 2.1 60NR0JA0-MB4000 GA605WV MB._32G_AI R9HX-370__R2.1(V8G)(TFT)(REG).fz")
OBV_EXE = os.environ.get("BOARDVIEW_TEST_EXE") or os.path.join(
    _DESKTOP, "Asus BIOS", "openboardview-10.0.0", "openboardview.exe")
OBV_CONF = os.environ.get("BOARDVIEW_TEST_CONF") or os.path.join(
    os.environ.get("APPDATA", ""), "OpenBoardView", "obv.conf")
GU605_FZ = os.environ.get("BOARDVIEW_TEST_FZ2") or os.path.join(
    _DESKTOP, "ASUS_ROG_Zephyrus_G16_2024_GU605MI_60NR0IS0_MB5520 BOARDVIEW(BDV)(.FZ)",
    "Asus ROG Zephyrus G16 (2024) GU605MI 60NR0IS0-MB5520 Quanta NJU DA0NJUMBCF0 "
    "(GU605MI MB._32G_U9-185H_E1.3(V8G)(RGB)(OLED)) Rev F BoardView.fz")


def file_fingerprint(path: str) -> tuple[str, int]:
    """(sha256, mtime in ns) of a file."""
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return digest, os.stat(path).st_mtime_ns


@pytest.fixture(scope="session")
def fingerprint() -> tuple[str, int]:
    return file_fingerprint(FZ_PATH)


@pytest.fixture(scope="session")
def board(fingerprint):  # depends on fingerprint so the hash is taken before the first load
    return fz.load(FZ_PATH, OBV_CONF, OBV_EXE)


@pytest.fixture
def make_fz(tmp_path):
    """Build an unencrypted .fz: [4 bytes][zlib content][zlib BOM][u32 LE = len(zbom) + 8]."""

    def build(content: str, bom: str, name: str = "tiny.fz") -> str:
        zbom = zlib.compress(bom.encode())
        path = tmp_path / name
        path.write_bytes(b"\0\0\0\0" + zlib.compress(content.encode()) + zbom + struct.pack("<I", len(zbom) + 8))
        return str(path)

    return build
