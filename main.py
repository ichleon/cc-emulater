#!/usr/bin/env python3
"""
CraftOS-Py - a small Python/Tkinter emulator for a ComputerCraft-style
Lua computer: a character-grid terminal display, a virtual filesystem,
a virtual printer peripheral, and the core os/term/fs/peripheral Lua
APIs, close enough to CC:Tweaked to run simple mining-turtle / computer
scripts written against that API.

Architecture (mirrors real ComputerCraft):
  * main.py (this file) is the "Java host": it implements the native,
    low-level primitives (term cell buffer, real file I/O, timers,
    the printer peripheral) as plain Python functions and hands them
    to a Lua runtime (via `lupa`).
  * bios.lua is the "operating system": os.pullEvent, print/read,
    colors/keys tables, textutils, dofile/run and the interactive
    "lua>" prompt are all written in Lua on top of those primitives,
    exactly like CC's own bios.lua.
  * The whole of bios.lua runs inside a single Lua coroutine. Whenever
    Lua code calls os.pullEvent, it internally calls coroutine.yield()
    and control comes back to Python. Python waits for a real GUI
    event (keypress, timer, etc.), then resumes the coroutine with it.

Requirements:
    pip install lupa
    Tkinter (bundled with Python on Windows/macOS; on Linux you may
    need `sudo apt install python3-tk`).

Run:
    python3 main.py
"""

import os
import sys
import time
import queue
import tkinter as tk
from tkinter import ttk

import lupa

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TERM_COLS = 51
TERM_ROWS = 19
CELL_W = 14
CELL_H = 22
FONT = ("Consolas", 13, "normal")
FONT_FAMILY_FALLBACK = ("Courier New", 13, "normal")

PAGE_COLS = 25
PAGE_ROWS = 21

FS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "computer")
BIOS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bios.lua")

# CC-style default 16 colour palette (value -> hex order 1,2,4,8,...32768)
COLOUR_ORDER = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768]
PALETTE = {
    1: "#F0F0F0",      # white
    2: "#F2B233",      # orange
    4: "#E57FD8",      # magenta
    8: "#99B2F2",      # lightBlue
    16: "#DEDE6C",     # yellow
    32: "#7FCC19",     # lime
    64: "#F2B2CC",     # pink
    128: "#4C4C4C",    # gray
    256: "#999999",    # lightGray
    512: "#4C99B2",    # cyan
    1024: "#B266E5",   # purple
    2048: "#3366CC",   # blue
    4096: "#7F664C",   # brown
    8192: "#57A64E",   # green
    16384: "#CC4C4C",  # red
    32768: "#191919",  # black
}
BLIT_CHARS = "0123456789abcdef"


def colour_to_hex(c):
    try:
        return PALETTE.get(int(c), PALETTE[32768])
    except (TypeError, ValueError):
        return PALETTE[32768]


def blit_char_to_colour(ch):
    idx = BLIT_CHARS.find(ch.lower())
    if idx < 0:
        return 1
    return COLOUR_ORDER[idx]


# ---------------------------------------------------------------------------
# Terminal buffer (native "graphics card")
# ---------------------------------------------------------------------------

class TermBuffer:
    def __init__(self, cols, rows):
        self.cols = cols
        self.rows = rows
        self.fg = 1        # white
        self.bg = 32768     # black
        self.cursor_x = 1
        self.cursor_y = 1
        self.cursor_blink = True
        self.dirty = True
        self._clear_grid()

    def _clear_grid(self):
        self.grid = [
            [[" ", self.fg, self.bg] for _ in range(self.cols)]
            for _ in range(self.rows)
        ]

    def _put(self, x, y, ch, fg, bg):
        if 1 <= x <= self.cols and 1 <= y <= self.rows:
            self.grid[y - 1][x - 1][0] = ch
            self.grid[y - 1][x - 1][1] = fg
            self.grid[y - 1][x - 1][2] = bg

    def _advance_line(self):
        self.cursor_x = 1
        self.cursor_y += 1
        if self.cursor_y > self.rows:
            self.scroll(self.cursor_y - self.rows)
            self.cursor_y = self.rows

    # -- native term.* implementations -------------------------------------

    def write(self, text):
        text = "" if text is None else str(text)
        for ch in text:
            if ch == "\n":
                self._advance_line()
                continue
            self._put(self.cursor_x, self.cursor_y, ch, self.fg, self.bg)
            self.cursor_x += 1
            if self.cursor_x > self.cols:
                self._advance_line()
        self.dirty = True

    def blit(self, text, fg_blit, bg_blit):
        text = str(text)
        fg_blit = str(fg_blit)
        bg_blit = str(bg_blit)
        for i, ch in enumerate(text):
            fg = blit_char_to_colour(fg_blit[i]) if i < len(fg_blit) else self.fg
            bg = blit_char_to_colour(bg_blit[i]) if i < len(bg_blit) else self.bg
            self._put(self.cursor_x, self.cursor_y, ch, fg, bg)
            self.cursor_x += 1
            if self.cursor_x > self.cols:
                self._advance_line()
        self.dirty = True

    def clear(self):
        for row in self.grid:
            for cell in row:
                cell[0] = " "
                cell[1] = self.fg
                cell[2] = self.bg
        self.dirty = True

    def clear_line(self):
        if 1 <= self.cursor_y <= self.rows:
            for cell in self.grid[self.cursor_y - 1]:
                cell[0] = " "
                cell[1] = self.fg
                cell[2] = self.bg
        self.dirty = True

    def scroll(self, n):
        n = int(n)
        if n > 0:
            self.grid = self.grid[n:] + [
                [[" ", self.fg, self.bg] for _ in range(self.cols)] for _ in range(min(n, self.rows))
            ]
        elif n < 0:
            n = -n
            self.grid = [
                [[" ", self.fg, self.bg] for _ in range(self.cols)] for _ in range(min(n, self.rows))
            ] + self.grid[: self.rows - n]
        self.grid = self.grid[: self.rows]
        self.dirty = True

    def set_cursor_pos(self, x, y):
        self.cursor_x = int(x)
        self.cursor_y = int(y)

    def get_cursor_pos(self):
        return self.cursor_x, self.cursor_y

    def set_cursor_blink(self, b):
        self.cursor_blink = bool(b)

    def get_cursor_blink(self):
        return self.cursor_blink

    def get_size(self):
        return self.cols, self.rows

    def set_text_colour(self, c):
        self.fg = int(c)

    def set_background_colour(self, c):
        self.bg = int(c)

    def get_text_colour(self):
        return self.fg

    def get_background_colour(self):
        return self.bg


# ---------------------------------------------------------------------------
# Virtual printer peripheral
# ---------------------------------------------------------------------------

class Printer:
    def __init__(self, on_page_printed):
        self.pages = []          # list[list[str]] finished pages
        self._page = None        # current page buffer (list[str]) or None
        self._cx = 1
        self._cy = 1
        self._on_page_printed = on_page_printed

    def newPage(self):
        if self._page is not None:
            return False
        self._page = [" " * PAGE_COLS for _ in range(PAGE_ROWS)]
        self._cx, self._cy = 1, 1
        return True

    def write(self, text):
        if self._page is None:
            return False
        text = str(text)
        for ch in text:
            if ch == "\n":
                self._cx = 1
                self._cy += 1
                continue
            if 1 <= self._cy <= PAGE_ROWS and 1 <= self._cx <= PAGE_COLS:
                row = self._page[self._cy - 1]
                self._page[self._cy - 1] = row[: self._cx - 1] + ch + row[self._cx:]
            self._cx += 1
            if self._cx > PAGE_COLS:
                self._cx = 1
                self._cy += 1
        return True

    def setCursorPos(self, x, y):
        self._cx, self._cy = int(x), int(y)

    def getCursorPos(self):
        return self._cx, self._cy

    def getPageSize(self):
        return PAGE_COLS, PAGE_ROWS

    def endPage(self):
        if self._page is None:
            return False
        self.pages.append(self._page)
        self._page = None
        self._on_page_printed()
        return True

    def isPrinting(self):
        return self._page is not None

    def getInkLevel(self):
        return 100

    def getPaperLevel(self):
        return 100

    def getName(self):
        return "printer"


# ---------------------------------------------------------------------------
# Virtual filesystem (sandboxed onto a real directory on disk)
# ---------------------------------------------------------------------------

class VFS:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _resolve(self, path):
        path = str(path).replace("\\", "/").lstrip("/")
        real = os.path.normpath(os.path.join(self.root, path))
        if real != self.root and not real.startswith(self.root + os.sep):
            raise ValueError("Path escapes computer root: " + path)
        return real

    def exists(self, path):
        try:
            return os.path.exists(self._resolve(path))
        except ValueError:
            return False

    def isDir(self, path):
        try:
            return os.path.isdir(self._resolve(path))
        except ValueError:
            return False

    def isReadOnly(self, path):
        return False

    def getSize(self, path):
        try:
            real = self._resolve(path)
            return os.path.getsize(real) if os.path.isfile(real) else 0
        except (ValueError, OSError):
            return 0

    def list(self, path="/"):
        try:
            real = self._resolve(path)
            if not os.path.isdir(real):
                return None
            return sorted(os.listdir(real))
        except (ValueError, OSError):
            return None

    def makeDir(self, path):
        try:
            os.makedirs(self._resolve(path), exist_ok=True)
            return True
        except (ValueError, OSError):
            return False

    def delete(self, path):
        try:
            real = self._resolve(path)
            if os.path.isdir(real):
                import shutil
                shutil.rmtree(real, ignore_errors=True)
            elif os.path.isfile(real):
                os.remove(real)
            return True
        except (ValueError, OSError):
            return False

    def move(self, src, dst):
        try:
            os.replace(self._resolve(src), self._resolve(dst))
            return True
        except (ValueError, OSError):
            return False

    def copy(self, src, dst):
        try:
            import shutil
            shutil.copy2(self._resolve(src), self._resolve(dst))
            return True
        except (ValueError, OSError):
            return False

    def combine(self, base, sub):
        combined = os.path.normpath("/" + str(base) + "/" + str(sub)).replace("\\", "/")
        return combined if combined != "/" else "/"

    def getName(self, path):
        return os.path.basename(str(path).rstrip("/")) or "/"

    def getDir(self, path):
        d = os.path.dirname(str(path).rstrip("/"))
        return d if d else "/"

    def getFreeSpace(self, path):
        return 10 ** 9

    def getDrive(self, path):
        return "hdd"

    def open(self, path, mode):
        mode = str(mode)
        try:
            real = self._resolve(path)
        except ValueError as e:
            return None, str(e)

        try:
            if mode in ("r", "rb"):
                if not os.path.isfile(real):
                    return None, "/" + str(path) + ": No such file"
                pymode = "rb" if mode == "rb" else "r"
                fh = open(real, pymode, encoding=None if mode == "rb" else "utf-8", newline="" if mode == "r" else None)
                lines_iter = {"pos": 0}

                def readAll():
                    data = fh.read()
                    return data if data else (b"" if mode == "rb" else "")

                def readLine():
                    line = fh.readline()
                    if not line:
                        return None
                    if isinstance(line, bytes):
                        return line.rstrip(b"\n").rstrip(b"\r")
                    return line.rstrip("\n").rstrip("\r")

                def close():
                    fh.close()

                handle = {"readAll": readAll, "readLine": readLine, "close": close}
                return handle, None

            elif mode in ("w", "a", "wb", "ab"):
                os.makedirs(os.path.dirname(real), exist_ok=True)
                pymode = mode if "b" in mode else mode
                fh = open(real, pymode, encoding=None if "b" in mode else "utf-8")

                def write(s):
                    fh.write(s if "b" in mode else str(s))
                    return True

                def writeLine(s):
                    fh.write((s if "b" in mode else str(s)))
                    fh.write(b"\n" if "b" in mode else "\n")
                    return True

                def close():
                    fh.close()

                def flush():
                    fh.flush()

                handle = {"write": write, "writeLine": writeLine, "close": close, "flush": flush}
                return handle, None
            else:
                return None, "Unsupported mode: " + mode
        except OSError as e:
            return None, str(e)


# ---------------------------------------------------------------------------
# The emulator engine: wires native Python APIs into a Lua runtime and
# drives the coroutine with GUI events.
# ---------------------------------------------------------------------------

class Emulator:
    def __init__(self, app):
        self.app = app
        self.term = TermBuffer(TERM_COLS, TERM_ROWS)
        self.vfs = VFS(FS_ROOT)
        self.printer = Printer(on_page_printed=app.on_page_printed)
        self.pending_events = queue.Queue()
        self.co = None
        self.alive = False
        self._timer_id_counter = 0
        self._pending_after_ids = {}

        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self._lock_down_stdlib()
        self._install_native_apis()

    # -- sandboxing ----------------------------------------------------

    def _lock_down_stdlib(self):
        g = self.lua.globals()
        # Programs should use fs.* (virtual FS) and our os.* API, not the
        # real filesystem / process control that stock Lua exposes.
        g.io = None
        g.loadfile = None
        g.dofile = None  # bios.lua defines its own virtual-fs dofile()
        real_os = g.os
        for dangerous in ("execute", "remove", "rename", "tmpname", "exit", "getenv"):
            try:
                real_os[dangerous] = None
            except Exception:
                pass

    # -- native API registration ---------------------------------------

    def _install_native_apis(self):
        g = self.lua.globals()
        new_table = self.lua.table

        # term ------------------------------------------------------------
        term_t = new_table()
        t = self.term
        term_t["write"] = lambda text=None: t.write(text)
        term_t["blit"] = lambda text, fg, bg: t.blit(text, fg, bg)
        term_t["clear"] = lambda: t.clear()
        term_t["clearLine"] = lambda: t.clear_line()
        term_t["scroll"] = lambda n=1: t.scroll(n)
        term_t["setCursorPos"] = lambda x, y: t.set_cursor_pos(x, y)
        term_t["getCursorPos"] = lambda: t.get_cursor_pos()
        term_t["setCursorBlink"] = lambda b: t.set_cursor_blink(b)
        term_t["getCursorBlink"] = lambda: t.get_cursor_blink()
        term_t["getSize"] = lambda: t.get_size()
        term_t["isColor"] = lambda: True
        term_t["isColour"] = lambda: True
        term_t["setTextColor"] = lambda c: t.set_text_colour(c)
        term_t["setTextColour"] = lambda c: t.set_text_colour(c)
        term_t["setBackgroundColor"] = lambda c: t.set_background_colour(c)
        term_t["setBackgroundColour"] = lambda c: t.set_background_colour(c)
        term_t["getTextColor"] = lambda: t.get_text_colour()
        term_t["getTextColour"] = lambda: t.get_text_colour()
        term_t["getBackgroundColor"] = lambda: t.get_background_colour()
        term_t["getBackgroundColour"] = lambda: t.get_background_colour()
        g["term"] = term_t

        # fs ----------------------------------------------------------------
        fs_t = new_table()
        vfs = self.vfs

        def fs_open(path, mode):
            handle, err = vfs.open(path, mode)
            if handle is None:
                return None, err
            ht = new_table()
            for k, v in handle.items():
                ht[k] = v
            return ht

        fs_t["open"] = fs_open
        fs_t["exists"] = lambda p: vfs.exists(p)
        fs_t["isDir"] = lambda p: vfs.isDir(p)
        fs_t["isReadOnly"] = lambda p: vfs.isReadOnly(p)
        fs_t["getSize"] = lambda p: vfs.getSize(p)
        fs_t["makeDir"] = lambda p: vfs.makeDir(p)
        fs_t["delete"] = lambda p: vfs.delete(p)
        fs_t["move"] = lambda a, b: vfs.move(a, b)
        fs_t["copy"] = lambda a, b: vfs.copy(a, b)
        fs_t["combine"] = lambda a, b: vfs.combine(a, b)
        fs_t["getName"] = lambda p: vfs.getName(p)
        fs_t["getDir"] = lambda p: vfs.getDir(p)
        fs_t["getFreeSpace"] = lambda p: vfs.getFreeSpace(p)
        fs_t["getDrive"] = lambda p: vfs.getDrive(p)

        def fs_list(p="/"):
            items = vfs.list(p)
            if items is None:
                return None
            lt = new_table()
            for i, name in enumerate(items, start=1):
                lt[i] = name
            return lt

        fs_t["list"] = fs_list
        g["fs"] = fs_t

        # os (augment the existing table bios.lua/Lua stdlib provides) -----
        os_t = g["os"]

        def os_start_timer(t_sec):
            self._timer_id_counter += 1
            tid = self._timer_id_counter
            ms = max(0, int(float(t_sec) * 1000))
            after_id = self.app.root.after(ms, lambda: self._queue_event(("timer", tid)))
            self._pending_after_ids[tid] = after_id
            return tid

        def os_cancel_timer(tid):
            after_id = self._pending_after_ids.pop(tid, None)
            if after_id is not None:
                try:
                    self.app.root.after_cancel(after_id)
                except Exception:
                    pass

        os_t["startTimer"] = os_start_timer
        os_t["cancelTimer"] = os_cancel_timer
        os_t["epoch"] = lambda kind="ingame": int(time.time() * 1000)
        os_t["day"] = lambda: int(time.time() // 86400)
        os_t["about"] = lambda: "CraftOS-Py 1.0"
        os_t["computerID"] = lambda: 0
        os_t["getComputerID"] = lambda: 0
        os_t["computerLabel"] = lambda: "Computer"
        os_t["getComputerLabel"] = lambda: "Computer"
        os_t["shutdown"] = lambda: self.app.request_shutdown()
        os_t["reboot"] = lambda: self.app.request_reboot()
        g["os"] = os_t

        # peripheral ----------------------------------------------------
        periph_t = new_table()
        printer_t = new_table()
        p = self.printer
        printer_t["newPage"] = lambda: p.newPage()
        printer_t["write"] = lambda s: p.write(s)
        printer_t["setCursorPos"] = lambda x, y: p.setCursorPos(x, y)
        printer_t["getCursorPos"] = lambda: p.getCursorPos()
        printer_t["getPageSize"] = lambda: p.getPageSize()
        printer_t["endPage"] = lambda: p.endPage()
        printer_t["isPrinting"] = lambda: p.isPrinting()
        printer_t["getInkLevel"] = lambda: p.getInkLevel()
        printer_t["getPaperLevel"] = lambda: p.getPaperLevel()

        PERIPH_SIDE = "back"

        def periph_is_present(side):
            return side == PERIPH_SIDE

        def periph_get_type(side):
            return "printer" if side == PERIPH_SIDE else None

        def periph_wrap(side):
            return printer_t if side == PERIPH_SIDE else None

        def periph_find(ptype, *_filter):
            if ptype == "printer":
                return printer_t
            return None

        def periph_get_names():
            nt = new_table()
            nt[1] = PERIPH_SIDE
            return nt

        periph_t["isPresent"] = periph_is_present
        periph_t["getType"] = periph_get_type
        periph_t["wrap"] = periph_wrap
        periph_t["find"] = periph_find
        periph_t["getNames"] = periph_get_names
        g["peripheral"] = periph_t

    # -- event queue -----------------------------------------------------

    def _queue_event(self, event_tuple):
        self.pending_events.put(event_tuple)

    def queue_event_external(self, event_tuple):
        """Called from Tk callbacks (keyboard, buttons)."""
        self._queue_event(event_tuple)

    # -- boot / run --------------------------------------------------------

    def boot(self):
        with open(BIOS_PATH, "r", encoding="utf-8") as f:
            bios_src = f.read()
        main_fn = self.lua.eval("assert(load(...))", bios_src)
        # assert(load(src)) returns the compiled chunk as a Lua function
        # object we can wrap into a coroutine.
        self.co = main_fn.coroutine()
        self.alive = True
        self._resume(None)

    def _resume(self, value):
        if not self.alive:
            return
        try:
            self.co.send(value)
            status = self.lua.eval("coroutine.status")(self.co)
            if status == "dead":
                self.alive = False
        except lupa.LuaError as e:
            self.alive = False
            self.term.set_text_colour(16384)
            self.term.write("\n[Lua error] " + str(e) + "\n")
        except StopIteration:
            self.alive = False
        self.term.dirty = True

    def pump(self):
        """Called periodically from the Tk mainloop."""
        if self.alive:
            try:
                ev = self.pending_events.get_nowait()
            except queue.Empty:
                ev = None
            if ev is not None:
                self._resume(ev)


# ---------------------------------------------------------------------------
# Key mapping: Tk keysym -> our `keys.*` numeric code (see bios.lua)
# ---------------------------------------------------------------------------

KEY_CODE = {}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    KEY_CODE[c] = i + 1
for i in range(10):
    KEY_CODE[str(i)] = 27 + i
KEY_CODE.update({
    "space": 37, "return": 38, "kp_enter": 38, "backspace": 39, "tab": 40, "escape": 41,
    "up": 42, "down": 43, "left": 44, "right": 45,
    "shift_l": 46, "shift_r": 47, "control_l": 48, "control_r": 49, "alt_l": 50, "alt_r": 51,
    "home": 52, "end": 53, "prior": 54, "next": 55, "delete": 56, "insert": 57,
    "f1": 58, "f2": 59, "f3": 60, "f4": 61, "f5": 62, "f6": 63, "f7": 64, "f8": 65,
    "f9": 66, "f10": 67, "f11": 68, "f12": 69,
    "minus": 70, "equal": 71, "bracketleft": 72, "bracketright": 73, "semicolon": 74,
    "apostrophe": 75, "comma": 76, "period": 77, "slash": 78, "backslash": 79, "grave": 80,
})


# ---------------------------------------------------------------------------
# Tkinter application
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("CraftOS-Py - ComputerCraft-style Lua computer emulator")
        self.root.resizable(False, False)

        self._build_ui()

        self._blink_state = True
        self.emulator = Emulator(self)
        self._prev_render = None
        self.emulator.boot()
        self.render()

        self.root.after(15, self._tick)
        self.root.after(500, self._blink_tick)

    # -- UI construction --------------------------------------------------

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=6)
        outer.grid(row=0, column=0, sticky="nsew")

        # Terminal canvas
        term_frame = ttk.Frame(outer)
        term_frame.grid(row=0, column=0, sticky="n")
        canvas_w = TERM_COLS * CELL_W
        canvas_h = TERM_ROWS * CELL_H
        self.canvas = tk.Canvas(term_frame, width=canvas_w, height=canvas_h,
                                 bg=PALETTE[32768], highlightthickness=1,
                                 highlightbackground="#555555")
        self.canvas.pack()

        self.canvas.font = FONT

        # pre-create cell rects + text items for fast diff-based redraws
        self._rects = [[None] * TERM_COLS for _ in range(TERM_ROWS)]
        self._texts = [[None] * TERM_COLS for _ in range(TERM_ROWS)]
        for row in range(TERM_ROWS):
            for col in range(TERM_COLS):
                x0, y0 = col * CELL_W, row * CELL_H
                rect = self.canvas.create_rectangle(
                    x0, y0, x0 + CELL_W, y0 + CELL_H, fill=PALETTE[32768], width=0)
                text = self.canvas.create_text(
                    x0 + CELL_W / 2, y0 + CELL_H / 2, text="", fill=PALETTE[1],
                    font=self.canvas.font)
                self._rects[row][col] = rect
                self._texts[row][col] = text
        self._cursor_rect = self.canvas.create_rectangle(0, 0, 0, 0, fill=PALETTE[1],
                                                           width=0, state="hidden")

        # Controls under the terminal
        controls = ttk.Frame(term_frame)
        controls.pack(fill="x", pady=(6, 0))
        ttk.Button(controls, text="Reboot", command=self.request_reboot).pack(side="left")
        ttk.Button(controls, text="Terminate (Ctrl+T)", command=self.request_terminate).pack(
            side="left", padx=(6, 0))
        self.status_var = tk.StringVar(value="Running")
        ttk.Label(controls, textvariable=self.status_var).pack(side="right")

        # Side panel: printer output
        side = ttk.Frame(outer, padding=(10, 0, 0, 0))
        side.grid(row=0, column=1, sticky="ns")
        ttk.Label(side, text="Printer output", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        list_frame = ttk.Frame(side)
        list_frame.pack(fill="both", expand=True, pady=(4, 4))
        self.page_list = tk.Listbox(list_frame, width=16, height=10, exportselection=False)
        self.page_list.pack(side="left", fill="y")
        self.page_list.bind("<<ListboxSelect>>", self._on_page_select)
        self.page_view = tk.Text(side, width=27, height=22, font=("Consolas", 10),
                                  bg="#fdfdf0", state="disabled")
        self.page_view.pack(fill="both", expand=True)

        # keyboard bindings on the whole window
        self.root.bind("<KeyPress>", self._on_key_press)
        self.root.bind("<KeyRelease>", self._on_key_release)
        self.root.bind("<Control-t>", lambda e: self.request_terminate())
        self.canvas.focus_set()
        self.canvas.bind("<Button-1>", lambda e: self.canvas.focus_set())

    # -- rendering ----------------------------------------------------------

    def render(self):
        term = self.emulator.term
        if not term.dirty and self._prev_render is not None:
            self._update_cursor(term)
            return
        term.dirty = False
        for row in range(TERM_ROWS):
            for col in range(TERM_COLS):
                ch, fg, bg = term.grid[row][col]
                prev = self._prev_render[row][col] if self._prev_render else None
                if prev == (ch, fg, bg):
                    continue
                bg_hex = colour_to_hex(bg)
                fg_hex = colour_to_hex(fg)
                self.canvas.itemconfigure(self._rects[row][col], fill=bg_hex)
                self.canvas.itemconfigure(self._texts[row][col],
                                           text=ch if ch != " " else "",
                                           fill=fg_hex)
        self._prev_render = [[tuple(cell) for cell in row] for row in term.grid]
        self._update_cursor(term)

    def _update_cursor(self, term):
        x, y = term.get_cursor_pos()
        visible = term.get_cursor_blink() and 1 <= x <= TERM_COLS and 1 <= y <= TERM_ROWS
        if visible and self._blink_state and self.emulator.alive:
            x0 = (x - 1) * CELL_W
            y0 = (y - 1) * CELL_H + CELL_H - 3
            self.canvas.coords(self._cursor_rect, x0, y0, x0 + CELL_W, y0 + CELL_H)
            self.canvas.itemconfigure(self._cursor_rect, state="normal")
        else:
            self.canvas.itemconfigure(self._cursor_rect, state="hidden")

    def _blink_tick(self):
        self._blink_state = not self._blink_state
        self._update_cursor(self.emulator.term)
        self.root.after(500, self._blink_tick)

    # -- keyboard -------------------------------------------------------

    def _on_key_press(self, event):
        keysym = event.keysym.lower()
        ctrl_held = bool(event.state & 0x4)
        code = KEY_CODE.get(keysym)
        if code is not None:
            self.emulator.queue_event_external(("key", code, False))
        if (not ctrl_held) and event.char and event.char.isprintable() and len(event.char) == 1:
            self.emulator.queue_event_external(("char", event.char))

    def _on_key_release(self, event):
        keysym = event.keysym.lower()
        code = KEY_CODE.get(keysym)
        if code is not None:
            self.emulator.queue_event_external(("key_up", code))

    # -- controls -------------------------------------------------------

    def request_terminate(self):
        self.emulator.queue_event_external(("terminate",))

    def request_reboot(self):
        self.status_var.set("Rebooting...")
        self.emulator = Emulator(self)
        self._prev_render = None
        self.emulator.boot()
        self.render()
        self.status_var.set("Running")

    def request_shutdown(self):
        self.emulator.alive = False
        self.status_var.set("Shut down")

    def on_page_printed(self):
        n = len(self.emulator.printer.pages)
        self.page_list.insert("end", "Page %d" % n)

    def _on_page_select(self, _event):
        sel = self.page_list.curselection()
        if not sel:
            return
        page = self.emulator.printer.pages[sel[0]]
        self.page_view.configure(state="normal")
        self.page_view.delete("1.0", "end")
        self.page_view.insert("1.0", "\n".join(page))
        self.page_view.configure(state="disabled")

    # -- main loop --------------------------------------------------------

    def _tick(self):
        self.emulator.pump()
        self.render()
        self.status_var.set("Running" if self.emulator.alive else "Idle")
        self.root.after(15, self._tick)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
