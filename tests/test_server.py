"""server.py: tools in-process, open_board (1j), read-only proof (1k), key-leak scan (1l), stdio smoke test (1n)."""
import asyncio
import json
import os
import sys

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from boardview_mcp import fz, server
from conftest import FZ_PATH, GU605_FZ, OBV_CONF, OBV_EXE, file_fingerprint

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = {"board_info", "get_part", "find_parts", "get_net", "find_nets", "parts_near", "pin_net",
         "connection_path", "open_board", "render_area"}


def point_server_at(mp: pytest.MonkeyPatch, board) -> None:
    mp.setattr(server, "_board", board)
    mp.setattr(server._cfg, "board", FZ_PATH)
    mp.setattr(server._cfg, "obv_conf", OBV_CONF)
    mp.setattr(server._cfg, "obv_exe", OBV_EXE)


@pytest.fixture
def served(monkeypatch, board):
    point_server_at(monkeypatch, board)


def run_all_tools() -> tuple[list[str], list[str]]:
    """Call every tool once, plus a few bad calls. Returns (text outputs, error messages)."""
    texts, errors = [], []
    calls = [
        (server.board_info, ()), (server.get_part, ("UBIOS1",)), (server.find_parts, ("UBIOS*",)),
        (server.get_net, ("GND",)), (server.get_net, ("GND", True, 10)), (server.get_net, ("R_SPI_DAT3_HOLD#",)),
        (server.find_nets, ("*SPI*",)), (server.parts_near, ("UBIOS1",)), (server.pin_net, ("UBIOS1", "7")),
        (server.connection_path, ("UBIOS1", "7", "R123", "1")), (server.render_area, ("UBIOS1",)),
        (server.open_board, (FZ_PATH,)),
    ]
    for fn, args in calls:
        out = fn(*args)
        texts.extend(x for x in (out if isinstance(out, list) else [out]) if isinstance(x, str))
    bad = [
        (server.get_part, ("UBIOS01",)), (server.pin_net, ("UBIOS1", "99")), (server.find_parts, ("re:(",)),
        (server.get_net, ("NO_SUCH_NET",)), (server.open_board, ("no_such_dir/board.txt",)),
        (server.open_board, (os.path.dirname(FZ_PATH),)), (server.render_area, ("UBIOS1", 500.0)),
    ]
    for fn, args in bad:
        with pytest.raises(ToolError) as err:
            fn(*args)
        errors.append(str(err.value))
    return texts, errors


@pytest.fixture(scope="module")
def tool_run(board):
    with pytest.MonkeyPatch.context() as mp:
        point_server_at(mp, board)
        return run_all_tools()


def leaked_indices(key, text: str) -> list[int]:
    text = text.lower()
    return [i for i, w in enumerate(key) if f"{w:08x}" in text or str(w) in text]


def test_tools_return_compact_json(served):
    out = server.board_info()
    info = json.loads(out)
    assert (info["parts"], info["pins"], info["nets"]) == (5160, 17226, 2816)
    assert out == json.dumps(info, separators=(",", ":"))
    assert json.loads(server.pin_net("UBIOS1", "7"))["net"] == "R_SPI_DAT3_HOLD#"
    assert json.loads(server.connection_path("UBIOS1", "7", "R123", "1"))["connected"] is True


def test_errors_become_tool_errors(served):
    with pytest.raises(ToolError, match="UBIOS1"):
        server.get_part("UBIOS01")
    with pytest.raises(ToolError, match="bad regex"):
        server.find_nets("re:(")
    with pytest.raises(ToolError, match="side must be"):
        server.parts_near("UBIOS1", side="left")


def test_no_board_configured(monkeypatch):
    monkeypatch.setattr(server, "_board", None)
    monkeypatch.setattr(server._cfg, "board", None)
    with pytest.raises(ToolError, match="open_board"):
        server.board_info()


def test_lazy_load_uses_configured_board(monkeypatch, make_fz):
    path = make_fz("A!REFDES\nS!R1!!R!NO!0!\nA!NET_NAME\nS!N!R1!1!1!10!10!!6!\n", "T\rH\r")
    monkeypatch.setattr(server, "_board", None)
    monkeypatch.setattr(server._cfg, "board", path)
    assert json.loads(server.board_info())["parts"] == 1
    assert server._board is not None


def test_render_area_tool_returns_image_and_caption(served):
    image, caption = server.render_area("UBIOS1", 5)
    assert isinstance(image, Image) and image.data.startswith(b"\x89PNG")
    assert json.loads(caption)["side"] == "TOP"


def test_in_process_client_lists_tools_and_flags_errors(served):
    async def go():
        async with Client(server.mcp) as c:
            tools = (await c.list_tools()).tools
            bad = await c.call_tool("get_part", {"refdes": "UBIOS01"})
            png = await c.call_tool("render_area", {"refdes": "UBIOS1", "radius_mm": 5})
            return tools, bad, png

    tools, bad, png = asyncio.run(go())
    assert {t.name for t in tools} == TOOLS
    assert all(t.annotations.read_only_hint is True for t in tools)
    assert bad.is_error is True and "UBIOS1" in bad.content[0].text
    assert [c.type for c in png.content] == ["image", "text"] and png.content[0].mime_type == "image/png"


# 1j
def test_open_board_rejects_bad_files(served, tmp_path):
    (tmp_path / "notes.txt").write_text("not a board")
    (tmp_path / "folder.fz").mkdir()
    for bad in (tmp_path / "notes.txt", tmp_path, tmp_path / "folder.fz", tmp_path / "missing.fz"):
        with pytest.raises(ToolError, match="only reads existing .fz"):
            server.open_board(str(bad))
    assert server._board is not None and server._cfg.board == FZ_PATH  # a failed open keeps the old board


@pytest.mark.skipif(not os.path.isfile(GU605_FZ), reason="GU605MI board not on this machine")
def test_open_board_loads_the_gu605mi(served):
    info = json.loads(server.open_board(GU605_FZ))
    assert info["file"].startswith("Asus ROG Zephyrus G16 (2024) GU605MI") and info["parts"] > 1000
    assert server._cfg.board == GU605_FZ
    assert json.loads(server.board_info()) == info  # later calls use the new board


# 1k
def test_board_file_unchanged_after_every_tool(tool_run, fingerprint):
    texts, errors = tool_run
    assert len(texts) >= 12 and len(errors) == 7  # the tools really ran
    assert file_fingerprint(FZ_PATH) == fingerprint


# 1l
def test_leak_scan_detects_a_planted_key_word():
    fake = tuple(range(0x1000A000, 0x1000A000 + 44))
    assert leaked_indices(fake, "clean text 12345") == []
    assert leaked_indices(fake, f"x {fake[5]:08X} x") == [5]  # hex, any case
    assert leaked_indices(fake, f"x {fake[7]} x") == [7]  # decimal


def test_no_key_word_in_outputs_errors_or_repr(tool_run, board):
    texts, errors = tool_run
    key, _ = fz.find_key(OBV_CONF, OBV_EXE)  # in-process only; failures show indices, never values
    leaked = leaked_indices(key, "\n".join([*texts, *errors, repr(board)]))
    assert leaked == []


# 1n
def test_stdio_server_lists_ten_tools_and_board_info():
    params = StdioServerParameters(
        command=sys.executable, cwd=ROOT,
        args=["-m", "boardview_mcp", "--board", FZ_PATH, "--obv-exe", OBV_EXE, "--obv-conf", OBV_CONF])

    async def go():
        with anyio.fail_after(180):  # the first call loads the board (about 7 s)
            async with Client(params) as c:
                names = [t.name for t in (await c.list_tools()).tools]
                return names, await c.call_tool("board_info", {})

    names, result = asyncio.run(go())
    assert sorted(names) == sorted(TOOLS) and len(names) == 10
    assert result.is_error is False
    info = json.loads(result.content[0].text)
    assert (info["parts"], info["pins"], info["nets"]) == (5160, 17226, 2816)
