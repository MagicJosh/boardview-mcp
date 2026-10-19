"""fz.py: key lookup order, the unencrypted synthetic file (1m), the load guards, and the real board's facts."""
import os
import struct

import pytest

from boardview_mcp import fz
from boardview_mcp.board import board_info
from conftest import FZ_PATH

CONTENT = "\n".join([
    "A!REFDES!COMP_INSERTION_CODE!SYM_NAME!SYM_MIRROR!SYM_ROTATE!",
    "S!R1!!R0201!NO!0!",
    "S!C1!!C0201!YES!0!",
    "S!CN_SSD1!!CONN!NO!0!",
    "S!TP1!!TP!NO!0!",
    "A!NET_NAME!REFDES!PIN_NUMBER!PIN_NAME!PIN_X!PIN_Y!TEST_POINT!RADIUS!",
    "S!NET_A!R1!1!1!1000!2000!!6!",
    "S!NET_B!R1!2!2!1100,5!2000!!6!",  # comma as the decimal separator
    "S!NET_A!C1!1!1!1000!2100!!6!",
    "S!NET_B!C1!2!2!1100!2100!!6!",
    "S!NET_C!CN_SSD1!0!A1!2000!3000!!6!",  # pin number 0: the pin name is the identifier
    "S!NET_D!CN_SSD1!!B2!2100!3000!!6!",  # empty pin number: same rule
    "S!NET_C!TP1!1!1!2000!3100!!6!",
    "A!GRAPHIC_DATA_NAME!GRAPHIC_DATA_NUMBER!RECORD_TAG!GRAPHIC_DATA_1!GRAPHIC_DATA_2!GRAPHIC_DATA_3!GRAPHIC_DATA_4!",
    "S!OUTLINE!1!LINE!0!0!4000!0!!!",
    "S!OUTLINE!2!LINE!4000!0!4000!2000!!!",
    "A!LOGOInfo",
    "S!LOGO_SENTINEL!nobody@example.invalid!",
    "A!UnDrawSym",
])
BOM = "\r".join([  # CR line endings, two header lines, tab columns
    "TINY TEST BOARD",
    "PARTNUMBER\tDESCRIPTION\tQTY\tLOCATION\tPARTNUMBER2",
    "10-001\tRES 0 OHM 1/20W\t1\tR1\t",
    "10-002\tMLCC 1UF/6.3V\t1\tC1\t",
    "10-003\tNGFF 5P TEST CONN\t1\tCN SSD1\t",  # the BOM writes a space where the board has "_"
    "10-005\t\t1\tTP1\t",
    "s-004\tMUST BE SKIPPED\t1\tTP1\t",  # part numbers starting with "s" are unused
])


def fake_key(odd: int, even: int) -> tuple[int, ...]:
    """A parity-valid key: word i has an even number of set bits iff KEY_PARITY[i] == 1."""
    return tuple(even if bit else odd for bit in fz.KEY_PARITY)


def key_text(key: tuple[int, ...]) -> str:
    return ", ".join(f"0x{w:08x}" for w in key)


# KEY_EXE has bits in the high half-word, so a 2-byte misalignment changes the parity pattern.
KEY_ENV, KEY_CONF, KEY_EXE = fake_key(1, 3), fake_key(7, 5), fake_key(0x00010000, 0x00030000)


def test_fake_keys_are_valid():
    for key in (KEY_ENV, KEY_CONF, KEY_EXE):
        assert fz.parse_key(key_text(key)) == key


def test_synthetic_unencrypted_file_parses_without_a_key(make_fz, monkeypatch):
    monkeypatch.delenv("BOARDVIEW_FZKEY", raising=False)
    path = make_fz(CONTENT, BOM)
    with open(path, "rb") as f:
        assert f.read()[4:6] == b"\x78\x9c"
    b = fz.load(path, None, None)
    assert "not encrypted" in b.key_source
    assert b.units_in_file == "mils"
    assert sorted(b.parts) == ["C1", "CN_SSD1", "R1", "TP1"]
    assert (b.parts["R1"].side, b.parts["C1"].side) == ("top", "bottom")
    r1_1, r1_2 = b.parts["R1"].pins
    assert (r1_1.x, r1_1.y) == pytest.approx((25.4, 50.8))  # mils x 0.0254
    assert r1_2.x == pytest.approx(1100.5 * 0.0254)
    assert [p.pin for p in b.parts["CN_SSD1"].pins] == ["A1", "B2"]
    info = board_info(b)
    assert (info["parts"], info["pins"], info["nets"]) == (4, 7, 4)
    assert info["board_size_mm"] == pytest.approx([101.6, 50.8])


def test_synthetic_bom_matching(make_fz):
    b = fz.load(make_fz(CONTENT, BOM), None, None)
    assert b.parts["R1"].description == "RES 0 OHM 1/20W"
    assert b.parts["CN_SSD1"].description.startswith("NGFF 5P")  # "CN SSD1" in the BOM
    assert b.parts["TP1"].description == ""  # the "s-004" row did not overwrite it
    assert b.parts["TP1"].dnp() == (None, "1-pin pad")
    assert b.parts["R1"].dnp() == (False, None)


def test_synthetic_millimeters_and_logo_block(make_fz):
    mm = CONTENT.replace("A!REFDES", "UNIT:millimeters\nA!REFDES", 1)
    b = fz.load(make_fz(mm, BOM), None, None)
    assert b.units_in_file == "millimeters"
    assert b.parts["R1"].pins[0].x == pytest.approx(1000.0)
    assert "SENTINEL" not in repr(b) and "example.invalid" not in repr(b)  # LOGOInfo is never parsed


def test_load_guards(tmp_path, make_fz, monkeypatch):
    monkeypatch.delenv("BOARDVIEW_FZKEY", raising=False)
    txt = tmp_path / "board.txt"
    txt.write_text("not a board")
    (tmp_path / "dir.fz").mkdir()
    for bad in (str(txt), str(tmp_path), str(tmp_path / "dir.fz"), str(tmp_path / "missing.fz")):
        with pytest.raises(fz.FzError, match="only reads existing .fz"):
            fz.load(bad, None, None)
    encrypted_looking = tmp_path / "enc.fz"
    encrypted_looking.write_bytes(bytes(range(64)))
    with pytest.raises(fz.FzError, match="no FZ key found"):
        fz.load(str(encrypted_looking), None, None)
    path = make_fz(CONTENT, BOM)
    monkeypatch.setattr(fz, "MAX_FILE_BYTES", 10)
    with pytest.raises(fz.FzError, match="larger than 50 MB"):
        fz.load(path, None, None)


def test_key_order_env_then_conf_then_exe(tmp_path, monkeypatch):
    conf = tmp_path / "obv.conf"
    conf.write_text(f"[General]\nFZKey = {key_text(KEY_CONF)}\n")
    exe = tmp_path / "openboardview.exe"
    exe.write_bytes(b"MZ" + b"\0" * 62 + struct.pack("<44I", *KEY_EXE) + b"tail" * 3)

    monkeypatch.setenv("BOARDVIEW_FZKEY", key_text(KEY_ENV))
    assert fz.find_key(str(conf), str(exe)) == (KEY_ENV, "env BOARDVIEW_FZKEY")

    monkeypatch.delenv("BOARDVIEW_FZKEY")
    assert fz.find_key(str(conf), str(exe)) == (KEY_CONF, "obv.conf")

    conf.write_text("[General]\nFZKey = \n")  # empty, like the real obv.conf
    assert fz.find_key(str(conf), str(exe)) == (KEY_EXE, "openboardview.exe")


def test_key_not_found_and_invalid(tmp_path, monkeypatch):
    monkeypatch.delenv("BOARDVIEW_FZKEY", raising=False)
    unaligned = tmp_path / "unaligned.exe"
    unaligned.write_bytes(b"MZ" + b"\0" * 62 + b"\0\0" + struct.pack("<44I", *KEY_EXE))
    for conf, exe in [(None, None), (str(tmp_path / "nope.conf"), str(unaligned))]:
        with pytest.raises(fz.FzError, match="no FZ key found"):
            fz.find_key(conf, exe)
    monkeypatch.setenv("BOARDVIEW_FZKEY", "0x1, 0x2")
    with pytest.raises(fz.FzError, match="BOARDVIEW_FZKEY"):
        fz.find_key(None, None)


def test_real_board_key_source(board):
    assert board.key_source in ("env BOARDVIEW_FZKEY", "obv.conf", "openboardview.exe")
    assert board.file == os.path.basename(FZ_PATH)
