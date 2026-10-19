"""MCP stdio server: read-only boardview tools over one lazily loaded .fz board.

Nothing here may print to stdout (it carries the MCP protocol) or write any file.
"""
import argparse
import functools
import inspect
import json
import os
import threading
from typing import Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import board, fz, render
from .board import BoardError
from .fz import FzError

mcp = MCPServer(
    "boardview",
    instructions=(
        "Read-only queries on one ASUS .fz boardview (parts, pins, nets, positions). Positions are mm with the "
        "origin of OpenBoardView's 'Show position'. The board loads on the first call (about 10 s). "
        "Lists show 50 rows unless you pass limit (max 1000); check total and truncated. "
        "Patterns are case-insensitive globs, or regex with a 're:' prefix. "
        "dnp_likely true = empty BOM description, probably not fitted; null = 1-pin pad, unknown."
    ),
)
_cfg = argparse.Namespace(
    board=None,
    obv_conf=os.path.join(os.environ.get("APPDATA", ""), "OpenBoardView", "obv.conf"),
    obv_exe=None,
)
_board: board.Board | None = None
_lock = threading.Lock()  # sync tools run in worker threads


def _get() -> board.Board:
    """The current board; the first call loads _cfg.board. Held under the lock so it loads once."""
    global _board
    with _lock:
        if _board is None:
            if not _cfg.board:
                raise ToolError("no board is loaded; call open_board with the path of a .fz file")
            _board = fz.load(_cfg.board, _cfg.obv_conf, _cfg.obv_exe)
        return _board


def _dump(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _query(fn, *args) -> str:
    return _dump(fn(_get(), *args))


def _tool(fn):
    """Register fn as a read-only tool. BoardError and FzError messages are safe for the model."""

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (BoardError, FzError) as e:
            raise ToolError(str(e)) from None

    return mcp.tool(description=inspect.getdoc(fn), annotations=ToolAnnotations(readOnlyHint=True),
                    structured_output=False)(run)


@_tool
def board_info() -> str:
    """Summary of the loaded board: file, key source, counts of parts, pins and nets, board size (mm),
    parts per side, dnp_flagged. Call it first to see what is loaded."""
    return _query(board.board_info)


@_tool
def get_part(refdes: str, limit: int = board.DEFAULT_LIMIT) -> str:
    """One part: side, center and bbox (mm), BOM description, dnp_likely and dnp_reason, and its pins with
    net and x/y. Pins are capped at `limit` (default 50, max 1000); see total and truncated."""
    return _query(board.get_part, refdes, limit)


@_tool
def find_parts(pattern: str, limit: int = board.DEFAULT_LIMIT) -> str:
    """Parts whose refdes or BOM description matches a glob (UBIOS*, *W25R256*) or a regex with the 're:'
    prefix (re:^R12[0-9]$). Returns refdes, side, description, dnp_likely. Default 50 rows, `limit` up to 1000."""
    return _query(board.find_parts, pattern, limit)


@_tool
def get_net(net: str, full: bool = False, limit: int = board.DEFAULT_LIMIT) -> str:
    """Pins on a net. Up to 50 pins give the pin list. A bigger net (GND, a rail) gives only a summary
    (counts, sides, refdes prefixes) unless full=true, which lists up to `limit` pins (default 50, max 1000)."""
    return _query(board.get_net, net, full, limit)


@_tool
def find_nets(pattern: str, limit: int = board.DEFAULT_LIMIT) -> str:
    """Nets whose name matches a glob (*SPI*) or a regex with the 're:' prefix, with pin counts.
    Default 50 rows, `limit` up to 1000."""
    return _query(board.find_nets, pattern, limit)


@_tool
def parts_near(refdes: str | None = None, x: float | None = None, y: float | None = None,
               radius_mm: float = 5.0, side: Literal["both", "top", "bottom"] = "both",
               limit: int = board.DEFAULT_LIMIT) -> str:
    """Parts with any pin within radius_mm of a part's center (give refdes) or of a point (give x and y, mm).
    Returns the closest-pin distance (mm), side, description and dnp_likely, nearest first. The refdes form
    leaves out the part itself; the x,y form does not. Default 50 rows, `limit` up to 1000."""
    return _query(board.parts_near, refdes, x, y, radius_mm, side, limit)


@_tool
def pin_net(refdes: str, pin: str) -> str:
    """Net and position (mm) of one pin. `pin` is the pin number or the pin name."""
    return _query(board.pin_net, refdes, pin)


@_tool
def connection_path(refdes_a: str, pin_a: str, refdes_b: str, pin_b: str, limit: int = board.DEFAULT_LIMIT) -> str:
    """Whether two pins connect: on the same net (direct), or through one 2-pin part (bridges).
    connected is true for the same net or a fitted bridge with dc_path true (resistor, 0 ohm, inductor, bead,
    fuse, jumper); "possible" when only a likely-unfitted bridge (dnp_likely true) would conduct if fitted;
    false otherwise. Bridges are listed fitted DC first, then fitted non-DC, then likely unfitted.
    Checks one hop only."""
    return _query(board.connection_path, refdes_a, pin_a, refdes_b, pin_b, limit)


@_tool
def open_board(path: str) -> str:
    """Switch to another .fz file (an existing regular .fz of at most 50 MB; loading takes about 10 s) and
    return its board_info. Later calls use the new board."""
    global _board
    new = fz.load(path, _cfg.obv_conf, _cfg.obv_exe)  # slow, so not under the lock
    with _lock:
        _board, _cfg.board = new, path
    return _dump(board.board_info(new))


@_tool
def render_area(refdes: str, radius_mm: float = 5.0, side: Literal["top", "bottom"] | None = None,
                net: str | None = None) -> list[Image | str]:
    """PNG of one board side in a square of +-radius_mm (at most 100) around a part: pin boxes, refdes
    labels, the target highlighted, likely-unfitted parts dashed grey, optional `net` highlighted, pin net
    names when radius_mm is 5 or less, a mm scale bar. `side` defaults to the part's side; bottom is
    mirrored in X like OpenBoardView. Returns the image plus a JSON caption."""
    png, caption = render.render_area(_get(), refdes, radius_mm, side, net)
    return [Image(data=png, format="png"), _dump(caption)]


def main() -> None:
    parser = argparse.ArgumentParser(prog="boardview-mcp", description=__doc__.splitlines()[0])
    parser.add_argument("--board", help=".fz file to load on the first tool call")
    parser.add_argument("--obv-exe", help="openboardview.exe, scanned for the FZ key")
    parser.add_argument("--obv-conf", default=_cfg.obv_conf, help="obv.conf with FZKey (default: %(default)s)")
    parser.parse_args(namespace=_cfg)
    mcp.run()
