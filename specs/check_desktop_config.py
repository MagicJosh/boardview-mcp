"""Add the approved boardview entry to claude_desktop_config.json, or verify it (--check).

Prints key names and booleans only, never values (other servers hold tokens).
"""
import codecs
import copy
import json
import os
import shutil
import sys

CFG = os.path.join(os.environ["APPDATA"], "Claude", "claude_desktop_config.json")
BAK = CFG + ".bak-20261019"
HOME = os.path.expanduser("~")
BIOS = os.path.join(HOME, "Desktop", "Asus BIOS")
ENTRY = {
    "command": os.path.join(HOME, "boardview-mcp", ".venv", "Scripts", "python.exe"),
    "args": [
        "-m", "boardview_mcp",
        "--board", os.path.join(BIOS, "ASUS GA605WI 2.1 60NR0JA0-MB4000 GA605WV MB._32G_AI R9HX-370__R2.1(V8G)(TFT)(REG).fz"),
        "--obv-exe", os.path.join(BIOS, "openboardview-10.0.0", "openboardview.exe"),
    ],
}


def read(path):
    with open(path, "rb") as f:
        return f.read()


def write_entry():
    if os.path.exists(BAK):
        sys.exit("backup already exists; refusing to overwrite it")
    raw = read(CFG)
    data = json.loads(raw.decode("utf-8-sig"))
    if "boardview" in data.get("mcpServers", {}):
        sys.exit("boardview entry already present; nothing written")
    shutil.copy2(CFG, BAK)
    # Keep the file's own format: BOM, line endings, indent width, trailing newline.
    bom = raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    crlf = "\r\n" in text
    lines = text.splitlines()
    indented = next((l for l in lines if l[:1] in (" ", "\t")), "  ")
    indent = "\t" if indented[0] == "\t" else len(indented) - len(indented.lstrip(" "))
    data["mcpServers"]["boardview"] = ENTRY
    out = json.dumps(data, indent=indent, ensure_ascii=False)
    if text.endswith(("\n", "\r\n")):
        out += "\n"
    if crlf:
        out = out.replace("\n", "\r\n")
    with open(CFG, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
        f.write(out)
    print(f"written (indent={indent!r}, crlf={crlf}, bom={bom})")


def check():
    try:
        new = json.loads(read(CFG).decode("utf-8-sig"))
        valid = True
    except ValueError:
        print("valid json: False")
        return
    old = json.loads(read(BAK).decode("utf-8-sig"))
    servers = list(new.get("mcpServers", {}))
    rest = copy.deepcopy(new)
    entry = rest["mcpServers"].pop("boardview", None)
    print("valid json:", valid)
    print("servers:", ", ".join(servers))
    print("old entries unchanged:", rest == old and list(rest) == list(old))
    print("boardview entry matches approved:", entry == ENTRY)
    print("bak exists:", os.path.isfile(BAK))


if __name__ == "__main__":
    if "--check" not in sys.argv:
        write_entry()
    check()
