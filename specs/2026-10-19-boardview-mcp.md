# Spec: boardview-mcp, a read-only MCP server for the GA605 .fz boardview

## Goal
Let Claude (Claude Code and the cloud session via the desktop app) query parts, pins, nets and positions in `ASUS GA605WI ... (REG).fz` directly, read-only, instead of screenshots from OpenBoardView.

## Decisions
1. Port OBV 10.0.0's `FZFile` parser to Python and query an in-memory model; rejected: driving the OBV GUI or building OBV's C++ code. Hard to undo: no.
2. FZ key from `BOARDVIEW_FZKEY` env override, then `FZKey` in `obv.conf`, then a parity scan of `openboardview.exe`; memory only. Rejected: DPAPI, `.env`, hardcoding. Hard to undo: no.
3. Register in Claude Code (user scope) and in `claude_desktop_config.json` (that is how the cloud session reaches this PC); rejected: Claude Code only. Hard to undo: no (remove entry or restore `.bak`).

## Verified format facts (from OBV 10.0.0 source + the real file)
- Encryption: OBV's RC6-variant stream cipher (`FZFile::decode`, 20 rounds, 44 u32 key words with a fixed parity pattern). Skip decryption when bytes 4-5 are `78 9C` or `78 DA`.
- `obv.conf` has `FZKey` empty; the shipped exe embeds the key (one parity match). The public source ships it empty.
- Decrypted layout: `[4 bytes][zlib content][zlib BOM][u32 LE BOM size]`, `content_size = len - bom_size + 4`.
- Content: `!`-separated lines. `A!REFDES` (SYM_MIRROR `YES` = bottom), `A!NET_NAME` (net, refdes, pin number, pin name, x, y, test point, radius), `A!GRAPHIC_DATA_NAME` (board OUTLINE lines/arcs), `A!LOGOInfo` (ignored), `A!UnDrawSym` (empty). Units are mils unless a `UNIT:millimeters` line exists. Commas in numbers become dots.
- BOM: CR-only line endings, tab-separated `PARTNUMBER DESCRIPTION QTY LOCATION PARTNUMBER2`, two header lines. LOCATION may use a space where the refdes uses `_` (`CN SSD1` vs `CN_SSD1`).
- DNP signal: an empty DESCRIPTION field (1650 of 5160 parts; 511 of those are 1-pin pads). QTY is always 1, no markers, no DNP section.
- Pin name rule (OBV): if the pin number is empty or `0`, the pin name is the identifier.
- OBV shows 5161 parts / 2817 nets because it adds a dummy part and an `UNCONNECTED` net. The real counts are 5160 / 17226 pins / 2816.

## Tool contract
All tools return compact JSON. Lists default to 50 rows (`limit` up to 1000) with `total` and `truncated`. An unknown refdes or net returns an error with up to 5 close matches. Positions are mm, with the same origin as OBV's "Show position" (file mils x 0.0254).

| Tool | Returns |
|---|---|
| `board_info()` | file, format, key_source (env / obv.conf / openboardview.exe), file units, output units, counts, board size from outline, parts per side, `dnp_flagged`, `dnp_unknown`, note on OBV's +1 counts |
| `get_part(refdes)` | side, center, bbox, description, `dnp_likely` (true / false / null), `dnp_reason`, pins with net and x/y |
| `find_parts(pattern)` | glob on refdes or description (`re:` prefix = regex) |
| `get_net(net, full=false)` | 50 pins or fewer: pin list. More: count + summary (parts, per side, refdes prefixes, sample). `full=true` gives the capped list |
| `find_nets(pattern)` | net names with pin counts |
| `parts_near(refdes or x,y, radius_mm, side=both)` | parts with any pin inside the radius, closest-pin distance, side tag, sorted by distance |
| `pin_net(refdes, pin)` | net and position of one pin (pin number or name) |
| `connection_path(a, pin_a, b, pin_b)` | both nets, `direct`, `connected` (true / "possible" / false), and every 2-pin bridge part with description, `dc_path` (true / false / null), `dc_reason`, `dnp_likely`, `dnp_reason`. Fitted DC bridges first, likely-unfitted bridges last |
| `open_board(path)` | switches to another `.fz`, returns its `board_info` |
| `render_area(refdes, radius_mm, side, net?)` | PNG: one side, OBV orientation (bottom mirrored in X), pin-bbox outlines, refdes labels, target highlighted, DNP grey dashed, optional net highlight, pin net names at radius 5 mm or less, mm scale bar, TOP/BOTTOM label |

## Proposed additions
| Detail | Proposal | Why it matters |
|---|---|---|
| Typos | Unknown refdes/net returns 5 close matches (stdlib `difflib`) | Claude recovers from `UBIOS01` without guessing |
| `open_board` guard | Existing regular `.fz` files only, 50 MB max, errors never echo file bytes | The model picks the path |
| `LOGOInfo` block | Never parsed or returned | Holds the converter's phone number and email |
| Key-leak test | Scan every tool output for the key words | Key never reaches the cloud session or a commit |
| Read-only test | sha256 + mtime of the `.fz` unchanged after every tool | Proves "never edit" instead of trusting it |
| Synthetic test file | Tiny unencrypted `.fz` built inside the test | Parser tests run on any laptop without the key |

## Trade-offs
Top 3 characteristics: correctness of pin/net data (a wrong net sends the iron to the wrong part), security of the key and the desktop config tokens, simplicity (one maintainer). One process, one quantum.

| Option | Cost | Verdict |
|---|---|---|
| A. Python port, in-memory model, MCP stdio | About 150-line parser port; pure-Python decrypt about 7 s per load | Pick: simplest and fully testable |
| B. Automate the OBV GUI | Brittle, still screenshots | Rejected by you |
| C. Build OBV's C++ parser into a CLI | C++ toolchain + zlib, two languages, still needs the key | Rejected |
| D. Parse once into a SQLite cache | Writes decrypted data to disk, adds a data store | Rejected: 9 s once is fine |

Given up: a sub-second first call (marked `shortcut:` in code; upgrade to numpy when load time matters).

## Security
| # | Risk | Evidence / scope | Likelihood | Impact | Severity | Mitigation | Status |
|---|---|---|---|---|---|---|---|
| S1 | FZ key leaks into code, repo, logs, errors or tool output | OBV deliberately ships the key empty in source | Medium | Medium | Medium | Memory only; no key in repr or errors; key-leak test (criterion 1l) | Open, tested |
| S2 | Desktop config tokens printed or file corrupted | File holds other servers' API tokens (never printed) | Low | High | Medium | `.bak-20261019` copy first, in-process JSON merge, print key names only, verify old entries unchanged in-process | Open |
| S3 | Board file modified | Your read-only rule | Low | High | Medium | Open `rb` only; criteria 1k and 2 | Open, tested |
| S4 | `open_board` reads arbitrary paths | Path comes from the model | Medium | Low | Low | `.fz` regular files, 50 MB cap, no bytes in errors | Open, tested |
| S5 | Third-party PII | `LOGOInfo` has the converter's contacts | High | Low | Low | Never parsed | Open |
| S6 | Egress | Tool results go to Anthropic via Claude Code / desktop relay | n/a | Low | Low | Authorized by you; board data only, no key, no PII | Accepted |
| S7 | Dependencies | `mcp`, `pillow`, `pytest` from PyPI | Low | Medium | Low | Venv only, exact version pins | Open |
| S8 | Regex DoS via `re:` | Model-supplied pattern | Low | Low | Low | Pattern max 200 chars, local only | Accepted |
| S9 | Env override puts the key in MCP config plaintext | Only if you set it | Low | Medium | Low | Not set by default | Accepted |

Verdict: CONDITIONAL. Proceed with S1-S5 and S7 implemented and tested.

## Acceptance criteria
Run from the project root.

1. `.venv/Scripts/python -m pytest -q` passes with 0 skipped on this PC, covering:
   - a. UBIOS1 pins 6/7/8 = R_SPI_CLK / R_SPI_DAT3_HOLD# / +1.8V_SPI_+1.8V; R123 pins 1/2 = APU_SPI_DAT3_HOLD# / R_SPI_DAT3_HOLD#; R115 pins 1/2 = FCH_SPI_CLK / +1.8V_SPI_+1.8V; `pin_net("UBIOS1","7")` = R_SPI_DAT3_HOLD#.
   - b. Counts 5160 / 17226 / 2816; UBIOS1 pin 1 at (-72.803, -161.173) mm.
   - c. DNP: PC720, PC3014, MC1, UTPM1, U2000 are `true` with "empty BOM description"; PR713, PC3010, PC3011, PC3013, PC3015, R115, R123, C685, UBIOS1 are `false`; ATP1 and RTCVCC_TP1 are `null` with "1-pin pad"; `board_info.dnp_flagged` equals the number of `true` flags.
   - d. Every part matches a BOM row; CN_SSD1 description starts "NGFF 5P", CN_HDMI1 starts "CONN DIP HDMI".
   - e. `connection_path`: (UBIOS1,7,R123,1) bridge R123 `dc_path` true, `dnp_likely` false, `connected` true; (UBIOS1,6,R115,1) bridge R119 `dc_path` true, `connected` true; (C685,1,UBIOS1,4) bridges include C685 `dc_path` false, `connected` false; (UBIOS1,7,R123,2) `direct` true, `connected` true. Synthetic board (built from the dataclasses): a blank-description R between two nets gives `dc_path` null, `dnp_likely` true, a `dc_reason` naming the R prefix, `connected` "possible"; a blank-description C alone gives `connected` false; a "RES 0 OHM" R in parallel with the blank R gives `connected` true and lists the fitted R first.
   - f. `parts_near("UBIOS1", 8)` includes R123 about 6.29, R115 about 6.39, C685 about 7.17 mm (tolerance 0.02), each side-tagged; `side="bottom"` drops them; the x,y form at UBIOS1's center returns the same set.
   - g. `get_net("GND")` returns a summary and no pin list; `full=true, limit=10` returns 10 rows with `truncated` true; `get_net("R_SPI_DAT3_HOLD#")` lists UBIOS1.7 and R123.2.
   - h. `find_parts("UBIOS*")` and `find_parts("*W25R256*")` return UBIOS1; `find_parts("re:^R12[0-9]$")` returns only matching refdes, including R123; `find_nets("*SPI*")` includes R_SPI_CLK and FCH_SPI_CLK.
   - i. `render_area("UBIOS1", 5)` returns a valid PNG whose caption has side TOP and the scale bar length; the bottom transform mirrors X.
   - j. `open_board` rejects a `.txt` file and a directory, and loads the GU605MI `.fz` (skipped if absent).
   - k. The `.fz` sha256 and mtime are unchanged after loading and calling every tool.
   - l. No key word (8-digit hex in any case, or decimal) appears in any tool output, error or `repr` of the model.
   - m. A synthetic unencrypted `.fz` built in the test parses with no key available.
   - n. Over a real stdio MCP client, the server lists exactly the 10 tools and `board_info` returns the counts.
2. `git grep -nE "open\(.*['\"][wax]|write_(text|bytes)|\.save\(" -- src` matches only the PNG save into a `BytesIO` in `render.py`.
3. `claude mcp list` shows `boardview` as connected.
4. Config check script prints `valid json: True`, the existing servers plus `boardview`, and `old entries unchanged: True` (compared to the `.bak` in-process); the `.bak` file exists.
5. Manual (you): after restarting the Claude desktop app, the cloud session calls `board_info` (as `mcp__remote-devices__boardview__board_info`) and gets 5160 / 17226 / 2816.

## Non-goals
- No writing to board files, OBV's install or OBV's config, ever.
- No other formats (.brd, .bdv, .cae, ...) and no OBV GUI automation.
- No decrypted data or key cached to disk.
- No HTTP transport, no numpy speed-up, no README.
- No commits or pushes until you ask.
- No ground-truth test for a flagged resistor near UBIOS1 yet. It comes after your physical check (candidates within 8 mm: RTPM8, RTPM7, both top).

## Blast radius
- New project `~\boardview-mcp\`: `pyproject.toml`, `.gitignore`, `specs/current.md`, `src/boardview_mcp/{__init__,__main__,fz,board,render,server}.py`, `tests/{conftest,test_fz,test_board,test_render,test_server}.py`, `.venv/` (ignored).
- Modified outside the project: `%APPDATA%\Claude\claude_desktop_config.json` (one key added) plus a new `claude_desktop_config.json.bak-20261019`; `~\.claude.json` (via `claude mcp add --scope user`).
- Read only: both `.fz` boards, `%APPDATA%\OpenBoardView\obv.conf`, `openboardview.exe`.
- Callers: none yet. Consumers are Claude Code and Claude Desktop (relayed to the cloud session).

## Assumptions
- Env override name `BOARDVIEW_FZKEY`, same format as the `obv.conf` line. `obv.conf` default path `%APPDATA%\OpenBoardView\obv.conf`, override with `--obv-conf`.
- Python 3.12 venv, pins `mcp==2.3.0`, `pillow==13.0.0`, `pytest==9.1.1`. `mcp` 2.x is a major version past what I know, so I read its docs before writing `server.py`.
- The board loads on the first tool call, not at startup. Pure-Python decrypt, marked `shortcut:`.
- Part center = pin bounding-box center. Board size = outline bounding box.
- `get_part` pin lists use the same cap (the APU has hundreds of pins).
- `dc_path` comes from the BOM description only: true when it starts with RES, THERMISTOR, IND, BEAD, FERRITE, FUSE or JUMP, false for anything else (MLCC, CAP, ...). Blank description: `dc_path` null and `dc_reason` "unknown, probably not fitted; would conduct if fitted (R prefix)" for refdes prefixes R, PR, L, PL, FB, F, PF, PJ, or "... would not conduct if fitted (C prefix)" otherwise. The prefix is a hint only, never true.
- `connected`: true when the pins share a net or a fitted bridge has `dc_path` true; "possible" when the only DC candidates are likely-unfitted parts whose prefix would conduct; false otherwise (so a blank C alone is false, not "possible").
- Bridge order: fitted DC, fitted non-DC, then likely-unfitted.
- Pin ids and patterns are case-insensitive.
- Render: 1024 px wide, side defaults to the target part's side, returned as MCP image content.
- `git init` with `*.fz`, `.venv`, `__pycache__`, `*.bak*` ignored.
- The desktop config is written only after criteria 1-3 pass, using exactly the entry you approved.
