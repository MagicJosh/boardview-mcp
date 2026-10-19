"""render.py (criterion 1i): a valid PNG, the TOP/BOTTOM caption, and the bottom-side X mirror."""
import io

import pytest
from PIL import Image

from boardview_mcp import render
from boardview_mcp.board import BoardError


def test_render_top_part(board):
    png, caption = render.render_area(board, "UBIOS1", 5)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    img = Image.open(io.BytesIO(png))
    img.load()
    assert (img.format, img.size) == ("PNG", (1024, 1024))
    assert caption["side"] == "TOP" and caption["refdes"] == "UBIOS1" and caption["scale_bar_mm"] > 0


def test_render_bottom_part_and_overrides(board):
    bottom = next(r for r, p in board.parts.items() if p.side == "bottom" and len(p.pins) >= 2)
    assert render.render_area(board, bottom, 5)[1]["side"] == "BOTTOM"
    assert render.render_area(board, "UBIOS1", 5, side="bottom")[1]["side"] == "BOTTOM"
    assert render.render_area(board, "UBIOS1", 5, net="r_spi_clk")[1]["net"] == "R_SPI_CLK"


def test_bottom_transform_mirrors_x():
    right_top = render.to_px(1.0, 0.0, (0.0, 0.0), 10.0, "top")
    right_bottom = render.to_px(1.0, 0.0, (0.0, 0.0), 10.0, "bottom")
    assert right_top[0] > render.SIZE / 2 > right_bottom[0]
    assert right_top[1] == right_bottom[1]


def test_render_rejects_bad_arguments(board):
    for args in (("UBIOS1", 0), ("UBIOS1", 101), ("UBIOS01", 5), ("UBIOS1", 5, "left"), ("UBIOS1", 5, None, "NO_SUCH_NET")):
        with pytest.raises(BoardError):
            render.render_area(board, *args)
