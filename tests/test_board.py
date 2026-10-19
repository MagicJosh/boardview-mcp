"""board.py queries against the real board (criterion 1a-1h) and a synthetic board for connection_path (1e)."""
import re

import pytest

from boardview_mcp import board as bd
from boardview_mcp.board import Board, BoardError, Part, Pin


# 1a
@pytest.mark.parametrize("refdes, pin, net", [
    ("UBIOS1", "6", "R_SPI_CLK"),
    ("UBIOS1", "7", "R_SPI_DAT3_HOLD#"),
    ("UBIOS1", "8", "+1.8V_SPI_+1.8V"),
    ("R123", "1", "APU_SPI_DAT3_HOLD#"),
    ("R123", "2", "R_SPI_DAT3_HOLD#"),
    ("R115", "1", "FCH_SPI_CLK"),
    ("R115", "2", "+1.8V_SPI_+1.8V"),
])
def test_pin_nets(board, refdes, pin, net):
    assert bd.pin_net(board, refdes, pin)["net"] == net


def test_get_part_pin_list_and_cap(board):
    nets = {r["pin"]: r["net"] for r in bd.get_part(board, "UBIOS1")["pins"]}
    assert (nets["6"], nets["7"], nets["8"]) == ("R_SPI_CLK", "R_SPI_DAT3_HOLD#", "+1.8V_SPI_+1.8V")
    capped = bd.get_part(board, "UBIOS1", limit=3)
    assert len(capped["pins"]) == 3 and capped["truncated"] is True and capped["total"] == len(nets)


def test_lookups_are_case_insensitive_and_suggest_close_matches(board):
    assert bd.pin_net(board, "ubios1", "7")["net"] == "R_SPI_DAT3_HOLD#"
    with pytest.raises(BoardError, match="UBIOS1"):  # did you mean
        bd.get_part(board, "UBIOS01")
    with pytest.raises(BoardError, match="unknown net"):
        bd.get_net(board, "NO_SUCH_NET_XYZ")
    with pytest.raises(BoardError, match="no pin"):
        bd.pin_net(board, "UBIOS1", "99")


# 1b
def test_counts_and_ubios1_pin1(board):
    info = bd.board_info(board)
    assert (info["parts"], info["pins"], info["nets"]) == (5160, 17226, 2816)
    assert info["parts_top"] + info["parts_bottom"] == 5160
    pin1 = next(r for r in bd.get_part(board, "UBIOS1")["pins"] if r["pin"] == "1")
    assert (pin1["x"], pin1["y"]) == pytest.approx((-72.803, -161.173), abs=0.001)


# 1c
@pytest.mark.parametrize("refdes", ["PC720", "PC3014", "MC1", "UTPM1", "U2000"])
def test_dnp_true(board, refdes):
    part = bd.get_part(board, refdes)
    assert (part["dnp_likely"], part["dnp_reason"]) == (True, "empty BOM description")


@pytest.mark.parametrize("refdes", ["PR713", "PC3010", "PC3011", "PC3013", "PC3015", "R115", "R123", "C685", "UBIOS1"])
def test_dnp_false(board, refdes):
    assert bd.get_part(board, refdes)["dnp_likely"] is False


@pytest.mark.parametrize("refdes", ["ATP1", "RTCVCC_TP1"])
def test_dnp_null_for_pads(board, refdes):
    part = bd.get_part(board, refdes)
    assert (part["dnp_likely"], part["dnp_reason"]) == (None, "1-pin pad")


def test_dnp_flagged_matches_true_flags(board):
    flagged = sum(bd.get_part(board, ref)["dnp_likely"] is True for ref in board.parts)
    assert bd.board_info(board)["dnp_flagged"] == flagged


# 1d
def test_every_part_has_a_bom_row(board):
    assert [r for r, p in board.parts.items() if p.description is None] == []
    assert bd.get_part(board, "CN_SSD1")["description"].startswith("NGFF 5P")
    assert bd.get_part(board, "CN_HDMI1")["description"].startswith("CONN DIP HDMI")


# 1e, real board
@pytest.mark.parametrize("args, bridge, dc, connected", [
    (("UBIOS1", "7", "R123", "1"), "R123", True, True),
    (("UBIOS1", "6", "R115", "1"), "R119", True, True),
    (("C685", "1", "UBIOS1", "4"), "C685", False, False),
])
def test_connection_path_real(board, args, bridge, dc, connected):
    result = bd.connection_path(board, *args)
    assert result["direct"] is False and result["connected"] is connected
    [row] = result["bridges"]
    assert row["refdes"] == bridge and row["dc_path"] is dc
    assert row["dc_reason"] is None and row["dnp_likely"] is False and row["dnp_reason"] is None


def test_connection_path_same_net_is_direct(board):
    result = bd.connection_path(board, "UBIOS1", "7", "R123", "2")
    assert (result["direct"], result["connected"], result["bridges"]) == (True, True, [])


# 1e, synthetic board
def two_pin(refdes: str, description: str | None, net1: str = "N1", net2: str = "N2") -> Part:
    pins = [Pin(refdes, "1", "1", net1, 0.0, 0.0, "top"), Pin(refdes, "2", "2", net2, 1.0, 0.0, "top")]
    return Part(refdes, "top", pins, description)


def synthetic(*bridges: Part) -> Board:
    """X1 on N1 and X2 on N2 (1-pin parts, never bridges) plus the given 2-pin parts."""
    ends = [Part(r, "top", [Pin(r, "1", "1", n, 0.0, 0.0, "top")], "TEST POINT") for r, n in (("X1", "N1"), ("X2", "N2"))]
    return Board("synthetic.fz", "none", {p.refdes: p for p in [*ends, *bridges]}, [])


def path(b: Board) -> dict:
    return bd.connection_path(b, "X1", "1", "X2", "1")


def test_blank_resistor_alone_is_possible():
    result = path(synthetic(two_pin("R9", "")))
    [row] = result["bridges"]
    assert row["dc_path"] is None and row["dnp_likely"] is True
    assert row["dc_reason"] == "unknown, probably not fitted; would conduct if fitted (R prefix)"
    assert result["connected"] == "possible"


def test_blank_capacitor_alone_is_not_connected():
    result = path(synthetic(two_pin("C9", None)))  # no BOM row counts as blank too
    [row] = result["bridges"]
    assert row["dc_path"] is None and row["dnp_likely"] is True
    assert row["dc_reason"] == "unknown, probably not fitted; would not conduct if fitted (C prefix)"
    assert result["connected"] is False


def test_fitted_resistor_beats_blank_resistor_in_parallel():
    result = path(synthetic(two_pin("R9", ""), two_pin("R8", "RES 0 OHM")))
    assert [r["refdes"] for r in result["bridges"]] == ["R8", "R9"]
    assert result["bridges"][0]["dc_path"] is True
    assert result["connected"] is True


def test_bridge_order_fitted_dc_then_fitted_non_dc_then_unfitted():
    result = path(synthetic(two_pin("PR9", ""), two_pin("C8", "MLCC 1UF/6.3V"), two_pin("L8", "IND 1UH")))
    assert [(r["refdes"], r["dc_path"]) for r in result["bridges"]] == [("L8", True), ("C8", False), ("PR9", None)]
    assert "(PR prefix)" in result["bridges"][2]["dc_reason"]


def test_fitted_capacitor_alone_is_not_connected():
    assert path(synthetic(two_pin("C8", "MLCC 1UF/6.3V")))["connected"] is False
    assert path(synthetic())["connected"] is False


# 1f
def test_parts_near_ubios1(board):
    near = bd.parts_near(board, "UBIOS1", radius_mm=8, limit=1000)
    rows = {r["refdes"]: r for r in near["parts"]}
    assert not near["truncated"] and "UBIOS1" not in rows
    for refdes, dist in (("R123", 6.29), ("R115", 6.39), ("C685", 7.17)):
        assert rows[refdes]["distance_mm"] == pytest.approx(dist, abs=0.02)
        assert rows[refdes]["side"] == "top"
    distances = [r["distance_mm"] for r in near["parts"]]
    assert distances == sorted(distances)

    bottom = bd.parts_near(board, "UBIOS1", radius_mm=8, side="bottom", limit=1000)
    assert {"R123", "R115", "C685"}.isdisjoint(r["refdes"] for r in bottom["parts"])
    assert all(r["side"] == "bottom" for r in bottom["parts"])


def test_parts_near_xy_form(board):
    cx, cy = bd.get_part(board, "UBIOS1")["center"]
    by_refdes = {r["refdes"] for r in bd.parts_near(board, "UBIOS1", radius_mm=8, limit=1000)["parts"]}
    by_xy = {r["refdes"] for r in bd.parts_near(board, x=cx, y=cy, radius_mm=8, limit=1000)["parts"]}
    # Known, accepted deviation: with x,y there is no reference part to exclude, so UBIOS1 itself is listed.
    assert "UBIOS1" in by_xy
    assert by_xy - {"UBIOS1"} == by_refdes


def test_parts_near_rejects_bad_arguments(board):
    for kwargs in ({"refdes": "UBIOS1", "side": "left"}, {"refdes": "UBIOS1", "radius_mm": 0}, {}, {"x": 1.0}):
        with pytest.raises(BoardError):
            bd.parts_near(board, **kwargs)


# 1g
def test_get_net_gnd_summary_and_full(board):
    summary = bd.get_net(board, "GND")
    assert summary["summary"] is True and "pins" not in summary
    full = bd.get_net(board, "GND", full=True, limit=10)
    assert len(full["pins"]) == 10 and full["truncated"] is True and full["total"] == summary["pin_count"]


def test_get_net_lists_small_net(board):
    pins = {(r["refdes"], r["pin"]) for r in bd.get_net(board, "R_SPI_DAT3_HOLD#")["pins"]}
    assert {("UBIOS1", "7"), ("R123", "2")} <= pins


# 1h
def test_find_parts(board):
    assert "UBIOS1" in [r["refdes"] for r in bd.find_parts(board, "UBIOS*")["parts"]]
    assert "UBIOS1" in [r["refdes"] for r in bd.find_parts(board, "*W25R256*")["parts"]]
    assert "UBIOS1" in [r["refdes"] for r in bd.find_parts(board, "ubios*")["parts"]]  # case-insensitive
    regex = [r["refdes"] for r in bd.find_parts(board, "re:^R12[0-9]$")["parts"]]
    assert "R123" in regex and all(re.fullmatch(r"R12[0-9]", r) for r in regex)


def test_find_nets(board):
    names = [r["net"] for r in bd.find_nets(board, "*SPI*")["nets"]]
    assert "R_SPI_CLK" in names and "FCH_SPI_CLK" in names


def test_limit_and_pattern_guards(board):
    everything = bd.find_parts(board, "*", limit=5000)
    assert len(everything["parts"]) == bd.MAX_LIMIT and everything["total"] == 5160 and everything["truncated"]
    assert len(bd.find_parts(board, "*")["parts"]) == bd.DEFAULT_LIMIT
    with pytest.raises(BoardError, match="bad regex"):
        bd.find_parts(board, "re:(")
    with pytest.raises(BoardError, match="longer than"):
        bd.find_nets(board, "*" * 201)
