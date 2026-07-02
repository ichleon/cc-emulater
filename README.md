# CraftOS-Py

A small Python/Tkinter emulator for a ComputerCraft-style Lua computer:
a character-grid terminal display, a virtual filesystem, a virtual
printer peripheral, and the core `term` / `fs` / `os` / `peripheral`
Lua APIs from CC:Tweaked, close enough to run simple computer/turtle
scripts written against that API.

## How it works

- **`main.py`** is the "host" (like CC's Java side): it implements the
  native primitives — a term character grid, real file I/O sandboxed
  to a `computer/` folder, timers, and a virtual printer — as plain
  Python functions and hands them to a Lua runtime via the
  [`lupa`](https://pypi.org/project/lupa/) binding.
- **`bios.lua`** is the "operating system" (like CC's own `bios.lua`):
  `os.pullEvent`, `print`/`read`, the `colors`/`colours` and `keys`
  tables, `textutils`, `dofile`/`run`, and the interactive `lua>`
  prompt are all plain Lua sitting on top of those native primitives.
- The whole of `bios.lua` runs inside **one Lua coroutine**. Whenever
  Lua code calls `os.pullEvent`, it internally yields the coroutine.
  Python waits for a real GUI event (keypress, timer, etc.) and
  resumes the coroutine with it — the same trick CC itself uses.

## Install & run

```bash
pip install lupa
# Linux only, if tkinter isn't already installed:
sudo apt install python3-tk

python3 main.py
```

A window opens with a 51x19 terminal (left) and a printer-output panel
(right). It boots straight to a `lua>` prompt (after auto-running
`computer/startup.lua`, if present).

## What's implemented

- `term.*` — write, blit, clear, clearLine, scroll, cursor
  position/blink, colours, getSize (51x19)
- `colors` / `colours` — full 16-colour table, toBlit/fromBlit,
  combine/subtract/test, packRGB/unpackRGB
- `keys` — a keys table + `keys.getName` (letters, digits, arrows,
  enter/backspace/tab/escape, F1-F12, modifiers, punctuation).
  Note: the numeric codes are **our own numbering**, not
  bit-for-bit identical to real CC/LWJGL codes — `keys.enter`,
  `keys.up`, etc. all work correctly, but hard-coded numeric
  literals from real CC scripts won't match.
- `os.*` — pullEvent/pullEventRaw, sleep, queueEvent, startTimer/
  cancelTimer, time/day/epoch/clock (via real Lua `os`), run, reboot,
  shutdown
- `fs.*` — open (r/w/a, text or binary), exists, isDir, list, makeDir,
  delete, move, copy, combine, getName, getDir, getSize. The virtual
  filesystem is sandboxed to the `computer/` folder next to `main.py`,
  so files persist between runs.
- `peripheral.*` — one fixed "printer" peripheral on side `"back"`
  (`find`/`wrap`/`isPresent`/`getType`/`getNames`)
- printer — `newPage`/`write`/`setCursorPos`/`endPage`/`getPageSize`;
  finished pages show up in the "Printer output" side panel
- `textutils.serialize`/`unserialize`, `urlEncode`, `formatTime`
- `print`/`printError`/`write`/`read`, `dofile`, `run("file.lua")`,
  `ls()`

## Known limitations

- Only one "computer" (no multiple monitors/turtles/wireless modems).
- `term.write` treats a literal `\n` as a newline for convenience;
  real CC's raw `term.write` does not do this (only `print` wraps).
- A Lua script with a *tight loop that never calls `os.pullEvent` /
  `sleep`* will freeze the GUI (there's no CC-style "too long without
  yielding" watchdog). Normal event-driven scripts are unaffected.
- `keys` numeric codes are emulator-specific (see above).
- No `shell.lua`/`multishell`/`edit` program — you get a direct Lua
  REPL instead of CC's full shell, plus `run("file.lua")`/`ls()`
  helpers.

## Files

- `main.py` — GUI + native APIs (run this)
- `bios.lua` — the Lua-side "OS" layer
- `computer/` — the virtual filesystem (persists across runs);
  `computer/startup.lua` runs automatically at boot
