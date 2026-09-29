#!/usr/bin/env python3
"""Headless smoke test for BUBBLE, run against the built ROM.

WHY THIS EXISTS: a screenshot only tells you the screen is not blank. This boots
the ROM in PyBoy, starts a game and fires shots, then asserts the board actually
changed -- which is the difference between "main.c runs" and "main.c plays".

    make test            # or: tools/smoke.py bubble.gb

It reads the BG TILEMAP rather than the game's own variables on purpose: tile
ids are a stable interface (0 blank, 1 wall, 2-17 bubbles) and it needs no
symbol map, so it does not break every time a global is added or reordered.

Input convention matters: ordinary input needs a press -> tick(1) -> release PER
FRAME. Pressing once and then ticking many frames does not reach the game.

This is a DEV TOOL. Nothing in the build depends on it.
"""
import os
import re
import sys

from pyboy import PyBoy

MAP = 0x9800            # BG map base (LCDC bit 3 clear): the playfield
WIN_MAP = 0x9C00        # window map base (LCDC bit 6 set): the launcher strip
SCY_REG = 0xFF42        # scroll Y: a ceiling drop slides the board with this
LCDC_REG = 0xFF40
WALL_TILES = 18 * 4     # both side walls, full height

# The title screen's arch and text. Must match main.c.
TITLE_COUNT = [8, 7, 6, 5, 4, 3]
TITLE_START = [0, 0, 1, 1, 2, 2]
T_FONT = 18                              # first font tile id
FONT_ORDER = " ABCEGIKLMNOPRSTUVYZ0123456789"   # glyph order, matches FONT_ORDER in main.c
TITLE_TEXT = [("PUZZLE BALLOON", 3, 12),  # string, tile column, tile row
              ("PRESS START", 4, 14)]
MSG_ROW = 12                              # end-of-board messages, screen tile row
MSG_OVER, MSG_OVER_COL = "GAME OVER", 5   # must match MSG_OVER / MSG_OVER_COL in main.c
SCORE_LABEL = "SCORE"                     # label row, then the digits under it
SCORE_COL, SCORE_ROW, SCORE_DIGITS = 12, 0, 5   # rows are window rows, not screen
LEVEL_LABEL = "LV"                        # the level readout, in the other free run
LEVEL_COL, LEVEL_ROW, LEVEL_DIGITS = 5, 0, 2
LEVEL_NUM_COL = 7                         # LEVEL_COL + the two tiles of "LV"

HERE = os.path.dirname(os.path.abspath(__file__))


def check_border_data(path=None):
    """The SGB border data must be the shape set_sgb_border() can send.

    PyBoy is not a Super Game Boy, so the CHR_TRN/PCT_TRN upload never runs here
    -- it is gated on sgb_check().  What is checkable is the data that upload
    would carry: those sizes come out of png2asset, and regenerating the border
    with the wrong flags (no -pack_mode sgb, more than 4 palettes) breaks the SGB
    quietly, with a border that is the wrong size or the wrong colour depth.
    """
    path = path or os.path.join(HERE, os.pardir, "border_data.h")
    src = open(path).read()

    def num(name):
        m = re.search(r"#define\s+%s\s+(\d+)" % name, src)
        assert m, "border_data.h has no %s -- did `make border` run?" % name
        return int(m.group(1))

    def array(name):
        m = re.search(r"%s\[(\d+)\]" % name, src)
        assert m, "border_data.h has no %s[]" % name
        return int(m.group(1))

    tiles, pals, cpp = (num("border_data_TILE_COUNT"),
                        num("border_data_PALETTE_COUNT"),
                        num("border_data_COLORS_PER_PALETTE"))
    # One CHR_TRN pair is the whole border: 256 4bpp tiles of 32 bytes.
    assert 0 < tiles <= 256, "%d border tiles, the SGB holds 256" % tiles
    assert array("border_data_tiles") == tiles * 32, \
        "border tile data is not 32 bytes per tile (bpp wrong?)"
    # 256x224 in 8x8 cells, two bytes each: tile index plus the palette attribute.
    assert array("border_data_map") == 1792, "border map is not 32x28 cells"
    assert 0 < pals <= 4 and cpp == 16, "%d palettes of %d colours" % (pals, cpp)
    # palette_color_t is uint16_t, so the count is in elements, not bytes.
    assert array("border_data_palettes") == pals * cpp, "not 16 colours per palette"
    return "%dt/%dp" % (tiles, pals)


def check_sgb_header(rom):
    """The cartridge header has to claim SGB support, or there is no border at all.

    mGBA picks the handheld model from 0x0146 (paired with the old-licensee byte
    0x014B), so without it mGBA emulates a plain DMG, sgb_check() is false, the
    border is never uploaded -- and nothing else looks wrong, which is exactly how
    a missing -Wm-ys hides. A real SGB BIOS reads the same two bytes.
    """
    head = open(rom, "rb").read(0x150)
    assert head[0x146] == 0x03, \
        "header 0x0146 is 0x%02X, not 0x03: no SGB flag (missing -Wm-ys?)" % head[0x146]
    assert head[0x14B] == 0x33, \
        "header 0x014B is 0x%02X, not 0x33: the SGB flag is ignored without it" % head[0x14B]
    return "0x%02X" % head[0x146]


def check_font_order(path=None):
    """FONT_ORDER and FONT_GLYPHS must list the same characters in the same order.

    Nothing else catches this. The game turns a character into a tile by
    searching FONT_ORDER, and everything here compares tile IDS -- so a glyph row
    inserted in the wrong place leaves every id still lining up and the screen
    quietly renders the wrong letter. That is not hypothetical: adding I, K and Y
    to the font put them before E, and the border read "PUZZLI BALLOON" with this
    test green. The glyph comments name each row, so the order is checkable from
    the source.
    """
    path = path or os.path.join(HERE, os.pardir, "main.c")
    src = open(path).read()
    order = re.search(r'FONT_ORDER\[\]\s*=\s*"([^"]*)"', src).group(1)
    body = re.search(r'FONT_GLYPHS\[.*?\]\s*=\s*\{(.*?)\n\};', src, re.S).group(1)
    names = re.findall(r"/\*\s*([A-Za-z0-9]+)\s*\*/", body)
    want = ["space" if ch == " " else ch for ch in order]
    assert names == want, \
        "FONT_GLYPHS is %r but FONT_ORDER is %r: the screen would draw the wrong letters" \
        % (names, want)

    # Every character the game prints must have a glyph. A missing one is not a
    # crash: font_tile() falls back to the space tile, so the message quietly
    # loses letters. "STAGE CLEAR" needs this most -- nothing headless ever
    # clears a board, so that string is only ever checked here, in the source.
    for name in ("MSG_CLEAR", "MSG_OVER"):
        msg = re.search(r'%s\s+"([^"]*)"' % name, src).group(1)
        missing = sorted(set(msg) - set(order))
        assert not missing, "%s %r has no glyph for %r" % (name, msg, missing)
    return len(order)




def c_ternary_to_python(src):
    """Rewrite a C expression's `?:` as Python conditional expressions.

    The difficulty check below reads main.c's own expressions instead of repeating
    the numbers, so it has to evaluate C. `?:` is the only thing Python spells
    differently, which makes the translation a balanced-paren scan rather than a
    parser: take the first `?`, run back to the start of its group for the
    condition, take its `:` and the end of that group for the two arms, and rewrite
    the three as `a if cond else b`. Innermost first, so nesting works out. The
    casts go first -- the arithmetic in here is all int8/uint8 and dropping
    `(uint8_t)` changes nothing about its value.
    """
    src = re.sub(r"\((?:u?int(?:8|16|32)_t|unsigned char)\)", "", src)
    while "?" in src:
        i = src.index("?")
        j, depth = i - 1, 0                # back to the start of the condition
        while j >= 0:
            if src[j] == ")":
                depth += 1
            elif src[j] == "(":
                if not depth:
                    break
                depth -= 1
            j -= 1
        k, depth = i + 1, 0                # forward to the ternary's `:`
        while k < len(src):
            if src[k] == "(":
                depth += 1
            elif src[k] == ")":
                if not depth:
                    break
                depth -= 1
            elif src[k] == ":" and not depth:
                break
            k += 1
        assert k < len(src) and src[k] == ":", \
            "cannot read the C expression %r: update this check" % src
        e, depth = k + 1, 0                # and on to the end of the else arm
        while e < len(src):
            if src[e] == "(":
                depth += 1
            elif src[e] == ")":
                if not depth:
                    break
                depth -= 1
            e += 1
        src = "%s((%s) if %s else (%s))%s" % (src[:j + 1], src[i + 1:k].strip(),
                                              src[j + 1:i].strip(),
                                              src[k + 1:e].strip(), src[e:])
    return src


def level_expr(src, pattern, name):
    """One of main.c's level-dependent expressions, as a function of the level.

    The eval() is on a snippet of this repo's own main.c, with builtins removed and
    bound to a single name (`level`), so the worst a bad expression can do is fail
    to compile.
    """
    m = re.search(pattern, src)
    assert m, "main.c has no %s matching %r: update this check" % (name, pattern)
    code = compile(c_ternary_to_python(m.group(1)), name, "eval")
    return lambda lv: eval(code, {"__builtins__": {}}, {"level": lv})


def check_difficulty(path=None):
    """The ramp has to leave room to play, at every level.

    Two properties, both read out of main.c's own expressions -- the numbers are
    the check, so they are evaluated rather than repeated here:

      * at least two free rows above LOSE_ROW. `4 + (level > 2 ? 2 : level)` starts
        the pile 6 rows deep from level 2, leaving exactly one free row, which is
        not a board that can be played.
      * the ceiling drop never comes round more often than every 4 shots. A drop
        adds a full row (~7.5 bubbles) and three shots take 9 off at best, so a
        3-shot interval loses ground every cycle.

    This is a SOURCE check, not a play check: `level` only rises on a cleared board
    and nothing headless clears one, so a smoke run never gets past level 1 and the
    rest of the ramp is unreachable from the emulator.
    """
    src = open(path or os.path.join(HERE, os.pardir, "main.c")).read()
    rows = level_expr(src, r"init_board\(\(uint8_t\)\s*\((.*?)\)\);", "init_board")
    every = level_expr(src, r"drop_every\s*=\s*(.*?);", "drop_every")
    # LOSE_ROW comes out of main.c as well. Hardcoding 7 here coupled the check to
    # THIS file's grid: shrink GRID_ROWS in main.c and the losing row moves up, but
    # a board one row too deep still passed, because 7 never moved.
    grid = re.search(r"#define\s+GRID_ROWS\s+(\d+)", src)
    assert grid, "main.c has no `#define GRID_ROWS`: update this check"
    lose_row = int(grid.group(1)) - 1

    depths = [rows(lv) for lv in range(64)]
    gaps = [every(lv) for lv in range(64)]
    for lv, n in enumerate(depths):
        assert 1 <= n <= lose_row - 2, \
            "level %d starts the pile %d rows deep: %d free row(s) above LOSE_ROW " \
            "(%d), and at least 2 are needed to play in" % (
                lv, n, lose_row - n, lose_row)
    for lv, d in enumerate(gaps):
        assert d >= 4, \
            "level %d drops the ceiling every %d shots: a drop adds ~7.5 bubbles " \
            "and three shots clear ~9, so the pile wins every cycle" % (lv, d)
    # A ramp, not a constant, and one that only ever gets harder with the level.
    assert len(set(depths)) > 1, "the starting depth never changes: no ramp (%s)" % depths[0]
    assert len(set(gaps)) > 1, "the drop interval never changes: no ramp (%s)" % gaps[0]
    assert all(gaps[lv] >= gaps[lv + 1] for lv in range(63)), \
        "the drop interval goes back up with the level: %s" % gaps
    # Shape as well as range: `(level > 1) ? 1 : ...` kept every depth inside the
    # bound while making level 2 onward start with a single bubble, and passed.
    assert all(depths[lv] <= depths[lv + 1] for lv in range(63)), \
        "the starting pile gets shallower as the level rises: %s" % depths
    return "rows<=%d, drop every %d..%d shots" % (
        max(depths), min(gaps), max(gaps))


def expected_title_map():
    """The whole BG map the title screen should produce, as tile ids.

    Comparing the entire map, rather than counting tiles, is what makes this a
    real check: it pins the shape, the colours, and the per-colour tile order
    (LT, LB, RT, RB -- see draw_cell), and it still catches the DMG boot ROM's
    Nintendo logo surviving, since that would leave tiles that do not match.
    """
    m = [[0] * 20 for _ in range(18)]
    for r in range(18):
        for c in (0, 1, 18, 19):                # walls, full height
            m[r][c] = 1
    for r in range(6):
        end = min(TITLE_START[r] + TITLE_COUNT[r], 8 - (r & 1))
        for c in range(TITLE_START[r], end):
            base = 2 + c * 2 + (r & 1)
            b = 2 + ((r + c) & 3) * 4
            m[r * 2][base] = b                  # left top
            m[r * 2][base + 1] = b + 2          # right top
            m[r * 2 + 1][base] = b + 1          # left bottom
            m[r * 2 + 1][base + 1] = b + 3      # right bottom
    for text, col, row in TITLE_TEXT:
        for i, ch in enumerate(text):           # .index() raises on a missing glyph
            m[row][col + i] = T_FONT + FONT_ORDER.index(ch)
    return m


def tile(py, col, row):
    """The tile at a SCREEN cell, following the scroll.

    A ceiling drop slides the whole board down by scrolling SCY rather than
    rewriting the map, so screen row r shows map row (SCY/8 + r) mod 32. Reading
    the map row for row means this keeps working at any point in the walk. (It is
    only exact when SCY is a multiple of 8 -- mid-slide the view is between map
    rows -- so checks that care are made once a drop has settled.)
    """
    return py.memory[MAP + (((py.memory[SCY_REG] >> 3) + row) & 31) * 32 + col]


def win_tile(py, col, row):
    """The tile at a screen cell of the window layer -- the launcher strip.

    The window is not scrolled, so this is a plain map read: window rows 0-1 sit
    on screen rows 128-143.
    """
    return py.memory[WIN_MAP + row * 32 + col]


T_ZERO = T_FONT + FONT_ORDER.index("0")


def score(py):
    """The score as an int, or None if the field is not label + five digits.

    "SCORE" sits on the row above the number, zero-padded and right-aligned in the
    launcher strip, so a wrong width, a missing label or a stray tile shows up here
    rather than as a silently wrong number. The strip is the window layer, so this
    is also the check that set_win_tiles() really addressed the window map: if it
    wrote the BG one instead, the strip would read blank here.
    """
    want = [T_FONT + FONT_ORDER.index(ch) for ch in SCORE_LABEL]
    if [win_tile(py, c, SCORE_ROW) for c in range(SCORE_COL, SCORE_COL + len(SCORE_LABEL))] != want:
        return None
    n = 0
    for c in range(SCORE_COL, SCORE_COL + SCORE_DIGITS):
        t = win_tile(py, c, SCORE_ROW + 1)
        if not T_ZERO <= t <= T_ZERO + 9:
            return None
        n = n * 10 + (t - T_ZERO)
    return n


def strip_clear_of_sprites(py, col, width):
    """True when no visible strip sprite covers the tile span col .. col+width-1.

    The launcher and next bubbles are sprites, and sprites draw OVER the window
    layer, so a strip field can be perfectly correct in the tilemap and still be
    half hidden. That is not hypothetical: the level field's digits first went at
    cols 8-9 and the "1" sat behind the launcher bubble, with every tile id right
    and `level()` reading it happily. OAM x is offset by 8, so a 16px bubble at
    x32 covers cols 3-4 and one at x80 covers cols 9-10 -- not the columns an
    earlier note guessed from the x values alone.
    """
    left, right = col * 8, (col + width) * 8 - 1
    for s in range(4):                      # launcher (0-1) then next (2-3)
        x = py.memory[0xFE01 + s * 4]
        if x and x - 8 <= right and x - 8 + 7 >= left:
            return False
    return True


def level(py):
    """The level as an int, or None if the field is not "LV" + two digits.

    Same shape as score(), and the same job: a wrong width, a missing label or a
    stray tile reads as None here rather than as a silently wrong number. The
    game shows level + 1 (its `level` is 0-based), so the first board must read 1.
    """
    want = [T_FONT + FONT_ORDER.index(ch) for ch in LEVEL_LABEL]
    if [win_tile(py, LEVEL_COL + i, LEVEL_ROW) for i in range(len(LEVEL_LABEL))] != want:
        return None
    n = 0
    for c in range(LEVEL_NUM_COL, LEVEL_NUM_COL + LEVEL_DIGITS):
        t = win_tile(py, c, LEVEL_ROW + 1)
        if not T_ZERO <= t <= T_ZERO + 9:
            return None
        n = n * 10 + (t - T_ZERO)
    return n


def msg_on_screen(py, text, col, row):
    """True when `text` is drawn on screen at (col, row).

    Read through tile(), which follows SCY: the game draws the message by SCREEN
    row (see draw_text() in main.c), so a drop's slide must not move it -- and if
    it did, this is what would catch it, since a loss is nearly always preceded by
    one. .index() raises on a glyph that is not in FONT_ORDER.
    """
    return [tile(py, col + i, row) for i, ch in enumerate(text)] == \
           [T_FONT + FONT_ORDER.index(ch) for ch in text]


def bubble_tiles(py):
    """Bubble tiles (a bubble is 4 tiles) in the playfield."""
    return sum(1 for r in range(16) for c in range(2, 18)
               if 2 <= tile(py, c, r) <= 17)


def board_address(py):
    """Locate main.c's `board[8][8]` in WRAM by matching it against the screen.

    The linker map lists no statics, so the only handle on the board is that its
    bytes ARE what is drawn: 0 for an empty cell, colour + 1 otherwise, and
    column 7 always empty in the shifted rows. The board's SCREEN position is
    invariant (that is what the drop's SCY walk preserves), so this matches at any
    settled moment, whatever map_y0 has become.

    Which rows are shifted depends on `parity`, which is not readable from here
    (SHIFTED(r) is (r ^ parity) & 1 and every drop toggles it), so both are tried
    and the byte match decides.
    """
    def drawn(parity):
        want = []
        for r in range(8):
            shifted = (r ^ parity) & 1
            for c in range(8):
                if shifted and c == 7:
                    want.append(0)               # shifted rows never use col 7
                    continue
                t = tile(py, 2 + c * 2 + shifted, r * 2)
                want.append((t - 2) // 4 + 1 if 2 <= t <= 17 else 0)
        return want

    hits = [a for parity in (0, 1) for a in range(0xC000, 0xE000 - 64)
            if list(py.memory[a:a + 64]) == drawn(parity)]
    assert len(hits) == 1, \
        "found %d WRAM windows matching the drawn board, expected exactly 1" % len(hits)
    return hits[0]


def mask_from_ram(py, addr):
    """The colours present in the board, read from RAM rather than the screen."""
    m = 0
    for a in range(64):
        v = py.memory[addr + a]
        if v:
            m |= 1 << (v - 1)
    return m


def check_drop_keeps_colours(py, frames):
    """A ceiling drop must not bring back a colour cleared off the board.

    This is the bug the game shipped with: ceiling_drop() filled the new row with
    `rand() & 3` regardless of what was left, while pick_colour() -- which feeds
    the bubbles the player fires -- drew from the board only. A board narrowed to
    two colours had all four handed back every eight shots, so a stage clear
    could not be reached. Measured by forcing that state: blind play never gets
    there in a smoke run, so a check that relies on it would never fail on the
    broken build.

    The board is rewritten in RAM (the screen keeps showing the old one, which is
    why everything below the forced state is read from RAM), and the two colours
    used are the two already picked, so nothing stale is compared.

    This starts its own game, and leaves the board in a state that would CLEAR,
    so it runs last: run before the game-over checks it turns the loss they need
    into a stage clear.
    """
    for _ in range(150):
        py.button_press("start")
        py.tick(1, True)
    py.button_release("start")
    for _ in range(300):
        py.tick(1, True)
        if all(py.memory[0xFE01 + s * 4] for s in (4, 5, 6)):
            break
    addr = board_address(py)
    cur, nxt = py.memory[0xFE02] >> 2, py.memory[0xFE02 + 2 * 4] >> 2
    for a in range(64):
        py.memory[addr + a] = 0
    py.memory[addr + 0 * 8 + 0] = cur + 1        # col 0 is valid in either parity
    py.memory[addr + 1 * 8 + 0] = nxt + 1
    start_mask = mask_from_ram(py, addr)
    assert bin(start_mask).count("1") == 2, "forcing the two-colour board failed"

    slid, prev = False, None
    for shot in range(30):
        frames(4, "left" if shot % 2 else "right")
        frames(3, "a")
        for _ in range(60):
            py.tick(1, True)
            scy = py.memory[SCY_REG]
            if scy % 8:
                slid = True
            elif slid and scy and scy == prev:
                now = mask_from_ram(py, addr)
                assert not (now & ~start_mask), \
                    "a ceiling drop brought back colour(s) %s that were cleared " \
                    "off the board (mask %s -> %s)" % (
                        format(now & ~start_mask, "04b"),
                        format(start_mask, "04b"), format(now, "04b"))
                return
            prev = scy
        # Harmless during play; restarts the game if the board filled up.
        frames(1, "start")
    raise AssertionError("no ceiling drop within 30 shots of the forced board")


def is_title(py):
    """True when every one of the 20x18 tiles matches the title screen."""
    want = expected_title_map()
    if not all(tile(py, c, r) == want[r][c] for r in range(18) for c in range(20)):
        return False
    # ...and when the launcher strip is empty. The title leaves it blank except
    # for the walls, which the window layer has to draw itself, and a strip that
    # was not cleared is exactly what a game over used to leave behind.
    return all(win_tile(py, c, r) == (1 if c in (0, 1, 18, 19) else 0)
               for r in range(2) for c in range(20))


def game_over_to_title(py, frames, shot_budget=60, skip=False):
    """Play until the game is lost; return frames from then to the title screen.

    Game over is the frame every used sprite goes hidden -- main() calls
    hide_all_sprites() as play() returns. Returns None if no game over happened
    within the budget, and -1 if one did but the title never came back. Shots are
    fired blind, which loses reliably; clearing a board just moves on a level.

    With skip=True, tap A once the game is over, which should cut the hold short
    instead of waiting it out.
    """
    if is_title(py):                    # START is what leaves the title screen
        frames(3, "start")
        frames(60)

    over = None
    n = 0
    for _ in range(shot_budget):
        frames(5, "left")
        frames(3, "a")
        for _ in range(400):
            py.tick(1, True)
            n += 1
            if is_title(py):
                return -1 if over is None else n - over
            if over is None and all(py.memory[0xFE00 + s * 4] == 0 for s in range(7)):
                over = n                # launcher, next bubble and aim dots all gone
                # ...and that is main() drawing "GAME OVER" over the board. This is
                # the only place the message is checked on screen: it is drawn and
                # never cleared, so it is still up all through the hold.
                assert msg_on_screen(py, MSG_OVER, MSG_OVER_COL, MSG_ROW), \
                    "GAME OVER is not on the board at row %d cols %d-%d (tiles %s)" % (
                        MSG_ROW, MSG_OVER_COL, MSG_OVER_COL + len(MSG_OVER) - 1,
                        [tile(py, MSG_OVER_COL + i, MSG_ROW) for i in range(len(MSG_OVER))])
            if skip and over is not None and n - over == 130:
                # ~1.5s in: past the 96-frame game-over flash, which does not read
                # the pad, and well inside the hold that follows. A tap, not a hold,
                # so it reads as a fresh press.
                frames(1, "a")
                py.tick(1, True)
                n += 1
    return -1 if over is not None else None


def wall_count(py):
    return sum(1 for r in range(18) for c in (0, 1, 18, 19)
               if tile(py, c, r) == 1)


def aim_up(py, frames):
    """Fire with no steering and report whether the shot went exactly vertical.

    The default angle has to be dead up. A 15 + 10k sweep never lands on 90 deg,
    so before ANG_MID existed the nearest shots were 85 and 95 deg and drifted
    ~12px over the playfield's height -- more than half a bubble, enough to miss
    anything directly above the launcher. Both halves are checked: the preview
    dots (all one x) and the flight itself, since only the flight proves fdx is
    really zero rather than merely small.
    """
    dots = set(py.memory[0xFE01 + s * 4] for s in (4, 5, 6))   # aim dots, x bytes
    if len(dots) != 1 or 0 in dots:
        return False
    frames(1, "a")
    prev, x, seen = 0, None, False
    for _ in range(60):
        py.tick(1, True)
        oy, ox = py.memory[0xFE00], py.memory[0xFE01]
        if not oy or oy >= 144:             # hidden, or still sitting in the launcher
            if seen:
                break
            continue
        if prev and oy >= prev:             # stopped rising: it has landed
            break
        seen = True
        if x is None:
            x = ox
        elif ox != x:
            return False                    # drifted sideways in flight
        prev = oy
    return seen


def first_press_fires(py, frames):
    """Leave the title, and fire on the new board's first press -- no release first.

    The aim loop breaks on `(keys & A|B) && !(prev & A|B)`. With prev starting at
    0xFF that test cannot be true for a first press: it needs a RELEASE to have been
    seen, so the press that should have fired the shot is swallowed and nothing
    happens until the player presses again. Nothing checked this before -- aim_up()
    is the only other check that reads OAM across a shot, and it presses A on three
    frames in a row, which is exactly the shape that hides it.

    A has to be down on the aim loop's FIRST frame or the old code fires anyway (by
    the second frame prev is 0 and an ordinary press works), and the only moment
    that is provably before play() starts and observably so is the redraw, which
    runs with the LCD off. So: leave the title, wait for LCDC bit 7 to go clear,
    then press A and keep it down. That is not a synthetic state -- it is what
    mashing A through a stage clear looks like (waitpad(J_START), waitpadup(),
    redraw_all(), play()). LCDC reads off at the end of a frame only if DISPLAY_ON
    has not run yet, so anything pressed then is down before play() is reached.

    The shot shows up as the launcher sprite leaving its parked row. The parked row
    is MEASURED, not hardcoded: the first version watched `0 < OAM y < 144`, which
    held only because LAUNCH_Y parks the launcher at 144, so raising LAUNCH_Y by a
    tile put the parked sprite inside the watched window and the check passed on a
    build with the bug put back.

    Measuring it needs the board to have finished drawing first. The LCD is off when
    we press, so the playfield count climbs 0 -> 112 -> 120 over the next two frames;
    take the parked row before that settles and "the launcher moved" just means the
    redraw finished, which is true of every build.
    """
    assert is_title(py), "not at the title screen: this would press into a game"
    frames(1, "start")                      # the title's own press, then released
    for _ in range(200):                    # ...so waitpadup() lets the redraw run
        py.tick(1, True)
        if not py.memory[LCDC_REG] & 0x80:
            break
    else:
        raise AssertionError("the board redraw never turned the LCD off")
    py.button_press("a")                    # held from here, never released
    was = 0
    for _ in range(60):                     # wait out the redraw, see the docstring
        py.tick(1, True)
        n = bubble_tiles(py)
        if n and n == was:
            break
        was = n
    else:
        raise AssertionError("the board never finished drawing")
    rest = py.memory[0xFE00]                # the launcher's row while aiming
    for _ in range(240):
        py.tick(1, True)
        if py.memory[0xFE00] != rest:       # it left: a shot is in flight
            py.button_release("a")
            return True
    py.button_release("a")
    return False


# The fall animation's sprite slots: 2 sprites per floater, from SPR_FALL up.
# Slots 0-7 are the launcher, the next bubble and the aim dots. Must match main.c.
FALL_SLOTS = list(range(8, 8 + 2 * 12))


def fall_watch(py, frames):
    """Fire one shot; report whether a floater sprite was seen moving DOWN.

    A hidden sprite reads Y=0, so any slot in FALL_SLOTS with a non-zero Y is a
    floater on screen, and a fall is that Y increasing frame to frame.
    """
    frames(6, "left")
    frames(3, "a")
    prev = [py.memory[0xFE00 + s * 4] for s in FALL_SLOTS]
    for _ in range(150):
        py.tick(1, True)
        cur = [py.memory[0xFE00 + s * 4] for s in FALL_SLOTS]
        if any(c and p and c > p for c, p in zip(cur, prev)):
            return True
        prev = cur
    # Tap START after a dry shot: harmless during play, and it restarts the game
    # if the board filled up and main() is sitting on the title screen again.
    py.button_press("start")
    py.tick(1, True)
    py.button_release("start")
    return False


def drop_watch(py, frames, shots=30):
    """Fire shots until a ceiling drop has settled; return the (SCY, LCDC) samples.

    The ceiling drop slides the board down by scrolling SCY instead of rewriting
    the map with the LCD off, which is what used to make it flash. Two things
    follow, and both are visible from the two registers alone:

      * SCY does not stay 0. The board walks up the 32-row map, so after a drop
        SCY is map_y0 * 8 and stays there -- nonzero until the walk wraps.
      * On the way it is NOT a multiple of 8, because the slide moves one pixel
        a frame, and the LCD is still on for every one of those frames.

    Putting the redraw back would leave SCY at 0 and the LCD briefly off, so both
    halves of this fail. Frames are sampled one at a time: the slide is 16 long
    and a 60-frame window after each shot covers it. Returning as soon as SCY has
    stopped moving means the caller sees the board exactly as the drop left it --
    which is when the new top row can be checked where it should be. ("Stopped"
    and not "a multiple of 8": a 16px slide passes over a tile boundary on the
    way, so one of its frames is tile-aligned too.)

    The budget is shots, not frames, and generous: a drop only comes every
    drop_every shots of ONE game, and losing mid-window restarts the count.
    """
    seen, slid, prev = [], False, None
    for _ in range(shots):
        frames(6, "left")
        frames(3, "a")
        for _ in range(60):
            py.tick(1, True)
            scy, lcdc = py.memory[SCY_REG], py.memory[LCDC_REG]
            seen.append((scy, lcdc))
            if scy % 8:
                slid = True
            elif slid and scy and scy == prev:
                return seen
            prev = scy
        # Harmless during play; restarts the game if the board filled up.
        frames(1, "start")
    return seen


def strip_visible(py):
    """Pixels where the score's digits are, i.e. is the window layer showing?

    The digits are only in the window map -- the BG rows behind the strip are
    blank in the middle -- so ink at the digit cells proves the window is both
    enabled and sitting where the strip should be (WY 128, WX 7). Tiles alone
    cannot show that: set_win_tiles() would happily fill a map that is never
    displayed. The digits are window row 1, i.e. screen rows 136-143, x 96-135;
    that is clear of the launcher sprite at x 80-95. Blank is white (255 on every
    channel), and the font is written in colour 3, the darkest shade.
    """
    screen = py.screen.ndarray
    return bool((screen[136:144, 96:136, 0] < 200).any())


# --- audio ---
NR50_REG, NR51_REG, NR52_REG = 0xFF24, 0xFF25, 0xFF26
CH_FIRE, CH_BOUNCE, CH_WARN, CH_POP = 0x01, 0x02, 0x04, 0x08   # NR52 bits 0-3
# audio_init()'s signature: AUDIO_MASTER and AUDIO_PAN, from audio.c. Nothing else
# in the ROM writes either one to these values.
AUDIO_MASTER, AUDIO_PAN = 0x77, 0xFF


def check_audio(rom):
    """The four SFX have to actually reach the APU, on the right channels.

    This needs its own PyBoy with sound_emulated=True: every other check here runs
    with sound_emulated=False, where PyBoy returns 0 from every sound register and
    DISCARDS every write, so audio is simply not observable in that instance --
    measured, not assumed. Sound emulation also needs no audio device, which is why
    this works headlessly.

    Two rules, both measured on this toolchain:

      * Assert on NR52's per-channel bits (0-3 = CH1-4 running). NRx3/NRx4 are
        write-only on a DMG and read back 0xFF, so they cannot be asserted on.
      * Gate on something THIS GAME wrote, never on "a channel is on". The DMG boot
        ROM drives CH1/CH2 itself for the first ~65 frames, so a check that waits
        for any channel to be active passes before main() has run a line of game
        code -- the same shape of mistake as the title-screen arch that fooled
        check_drop_keeps_colours(). NR50 == 0x77 alone is NOT enough either: the
        boot ROM sets exactly that for its jingle. NR51 is what separates them, as
        the boot ROM writes 0xF3 there and audio_init() writes 0xFF.

    Teeth come from what the checks would miss: a shot with no sfx_fire() leaves
    CH1 silent, a wall bounce with no sfx_bounce() leaves CH2 silent, a pop with no
    sfx_pop() leaves CH4 silent, and a drop warned at the instant it lands -- the
    off-by-one in sfx_warn()'s call site -- leaves no gap between the CH3 burst and
    the slide, which is what the gap assertion below measures.
    """
    py = PyBoy(rom, window="null", sound_emulated=True)

    def tick(n, key=None):
        for _ in range(n):
            if key:
                py.button_press(key)
            py.tick(1, True)
            if key:
                py.button_release(key)

    def channels():
        return py.memory[NR52_REG] & 0x0F

    for _ in range(400):
        py.tick(1, True)
        if py.memory[NR50_REG] == AUDIO_MASTER and py.memory[NR51_REG] == AUDIO_PAN:
            break
    else:
        raise AssertionError(
            "the game never initialised the APU: NR50/NR51 never took audio_init()'s "
            "values after 400 frames (NR50=0x%02X NR51=0x%02X, want 0x%02X/0x%02X)"
            % (py.memory[NR50_REG], py.memory[NR51_REG], AUDIO_MASTER, AUDIO_PAN))

    # Past the boot ROM's own jingle, at the title. is_title() is the exact signal
    # and worth waiting for: main() runs audio_init() and then draws the title, and
    # a START pressed into the gap between the two is simply not read.
    for _ in range(600):
        py.tick(1, True)
        if is_title(py):
            break
    else:
        raise AssertionError("the title screen never came up: cannot reach a board")
    tick(3, "start")
    for _ in range(300):
        py.tick(1, True)
        if all(py.memory[0xFE01 + s * 4] for s in (4, 5, 6)):
            break
    else:
        raise AssertionError("the aim dots never appeared: no board was started")

    # Nothing fires while the player is only aiming. audio_init() leaves a powered
    # APU with every channel idle, and nothing in the aim loop triggers one.
    assert not channels(), \
        "channel(s) %s are running while the player is only aiming: something " \
        "triggers an SFX that should not" % format(channels(), "04b")

    # Steer the sweep to its far right stop -- 15 degrees, nearly horizontal -- so
    # the shot reaches the right wall within ~15 frames, before it can hit anything.
    # The default aim is dead vertical and would never touch a wall at all. The sweep
    # does not wrap (LEFT/RIGHT clamp at the ends), so holding it is free: the aim
    # key repeat is one step per 3 frames, and 60 of them is more than the 8 steps
    # between the vertical and the stop.
    tick(60, "right")
    tick(1, "a")
    fire, bounce = False, False
    for _ in range(40):
        py.tick(1, True)
        n = channels()
        fire |= bool(n & CH_FIRE)
        bounce |= bool(n & CH_BOUNCE)
    assert fire, "firing a shot did not start CH1: sfx_fire() is not wired to the aim break"
    assert bounce, "the shot bounced off a wall without starting CH2: sfx_bounce() is " \
                   "not wired to the wall reflection"

    # The ceiling drop's telegraph. It needs a shot count to build up (a drop comes
    # every drop_every shots -- 8 at level 0), so this is blind play until one has
    # slid the board and settled.
    #
    # `warned` deliberately OUTLIVES the shot it was heard in: the whole point of the
    # warning is that it belongs to the shot BEFORE the drop, so it is always in the
    # previous shot's window. The gap is measured from the burst to the drop's FIRST
    # slid frame, which is what makes the off-by-one sign-flip: warning on the drop's
    # own frame means the CH3 burst is still sounding when the slide starts and the
    # gap comes out negative. Measured to the settle instead it would come out merely
    # small -- the burst and the 16-frame slide overlap either way -- and a warn-now
    # implementation would pass it.
    warned, gaps, frame = None, [], 0
    for _ in range(24):
        tick(6, "left")
        tick(3, "a")
        slide, slid, prev, n = None, False, None, 0
        while n < 160:
            py.tick(1, True)
            frame += 1
            n += 1
            if channels() & CH_WARN:
                warned = frame
            scy = py.memory[SCY_REG]
            if scy % 8:
                slid = True            # mid-slide: the drop is happening now
                if slide is None:
                    slide = frame
            elif slid and scy and scy == prev:
                assert warned is not None, \
                    "a ceiling drop at frame %d was never warned: sfx_warn() is not " \
                    "wired to the shot before the drop" % slide
                gaps.append(slide - warned)
                break
            prev = scy
        if gaps:
            break

    assert gaps, "no ceiling drop within 24 shots: the drop warning is untested"
    assert gaps[0] >= 10, \
        "the drop warning sounded %d frames before the drop started (%s): it has to " \
        "be a whole shot earlier, not at the instant the player is punished" % (
            gaps[0], "still sounding when the slide began" if gaps[0] <= 0 else "too close")

    # A pop. Which shot lands a match is not predictable, so fire until one does --
    # the same shape as the score loop in main().
    pop = False
    for _ in range(30):
        if pop:
            break
        tick(6, "left")
        tick(3, "a")
        for _ in range(90):
            py.tick(1, True)
            if channels() & CH_POP:
                pop = True
        # Harmless during play; restarts the game if the board filled up.
        tick(1, "start")
    assert pop, "no pop within 30 shots started CH4: sfx_pop() is not wired to resolve()"

    py.stop(save=False)
    return "fire/bounce/pop/warn(+%df)" % gaps[0]


def main():
    rom = sys.argv[1] if len(sys.argv) > 1 else "bubble.gb"
    border = check_border_data()
    check_sgb_header(rom)
    check_font_order()
    ramp = check_difficulty()
    # Its own console, and so its own PyBoy: the one below runs with
    # sound_emulated=False, where every sound register reads 0 and every write is
    # thrown away. Run before that instance exists rather than alongside it.
    audio = check_audio(rom)
    py = PyBoy(rom, window="null", sound_emulated=False)

    def frames(n, key=None):
        for _ in range(n):
            if key:
                py.button_press(key)
            py.tick(1, True)
            if key:
                py.button_release(key)

    # The title screen, checked tile for tile. This doubles as the check that the
    # DMG boot ROM's Nintendo logo does not survive: main() blanks the map before
    # DISPLAY_ON, and the title's redraw covers all 20x18 tiles, so the logo (or,
    # on hardware that does not zero VRAM the way an emulator does, garbage) would
    # show up here as tiles that do not match.
    frames(150)
    actual = [[tile(py, c, r) for c in range(20)] for r in range(18)]
    want = expected_title_map()
    diff = [(r, c, actual[r][c], want[r][c])
            for r in range(18) for c in range(20) if actual[r][c] != want[r][c]]
    assert not diff, "title screen wrong in %d tiles, e.g. %s" % (len(diff), diff[:4])

    # The title screen is not just tiles: it also shows the launcher and the next
    # bubble as sprites, so the picture reads as ready to play.
    assert py.memory[0xFE00] != 0, "title screen shows no launcher sprite"

    # The first press on a new board. This also leaves the title screen, which the
    # title being up and checked above makes safe: main()'s boot wait is over, so
    # one press/release is enough to get past `while (!(joypad() & (START|A)))` and
    # the waitpadup() behind it. See first_press_fires().
    assert first_press_fires(py, frames), \
        "the first A press on a new board was swallowed: no shot fired with the " \
        "button held down from before the board was drawn"

    # Wait for the walls, then let the redraw finish. draw_board lays the walls
    # down first and the bubbles after, so a snapshot taken the moment the walls
    # count goes green can still catch the playfield half-drawn.
    for _ in range(300):
        py.tick(1, True)
        if wall_count(py) == WALL_TILES:
            break

    # ...then for the aim loop to put its dots up, which is the real "a board is
    # being played" signal. The walls are NOT it: the title screen draws the same
    # walls, so that condition is already true while the title is still up, and
    # this used to be a fixed frames(5) that only worked because the redraw and
    # play() happened to land exactly there. Adding the level readout moved it by
    # a frame and the shot fired before the dots existed. The title never places
    # dots, so waiting for them is exact.
    for _ in range(60):
        py.tick(1, True)
        if all(py.memory[0xFE01 + s * 4] for s in (4, 5, 6)):
            break

    walls = wall_count(py)
    assert walls == WALL_TILES, "walls not drawn: %d/%d tiles" % (walls, WALL_TILES)

    # Named for what it is: first_press_fires() has already fired a shot, so this
    # is the board AFTER it landed, not the starting board. Nothing asserts the
    # starting depth here -- check_difficulty() owns that, and it is a source check.
    after_shot = bubble_tiles(py)
    assert after_shot >= 24, "board is empty after the first shot (%d tiles)" % after_shot

    # The level readout, drawn by draw_level() at the start of every board. This is
    # the one point a headless run sees it from: nothing here clears a board, so the
    # level never rises. Reading 1 rather than 0 is the point -- the game's `level`
    # is 0-based and the field adds 1.
    assert level(py) == 1, "level field is not LV + two digits reading 01 (tiles %s / %s)" % (
        [win_tile(py, c, LEVEL_ROW) for c in range(LEVEL_COL, LEVEL_NUM_COL + LEVEL_DIGITS)],
        [win_tile(py, c, LEVEL_ROW + 1) for c in range(LEVEL_COL, LEVEL_NUM_COL + LEVEL_DIGITS)])

    # ...and that the launcher and next-bubble sprites are not sitting on it. The
    # field fills the only gap between them, so the two have to agree.
    assert strip_clear_of_sprites(py, LEVEL_COL, LEVEL_DIGITS + len(LEVEL_LABEL)), \
        "a strip sprite covers the level field at cols %d-%d" % (
            LEVEL_COL, LEVEL_COL + LEVEL_DIGITS + len(LEVEL_LABEL) - 1)

    # The un-steered shot must be exactly vertical -- see aim_up().
    assert aim_up(py, frames), "the default aim is not straight up"

    # Fire a few shots; each should stick somewhere, so the board must change.
    seen = {after_shot}
    for _ in range(3):
        frames(6, "left")
        frames(3, "a")
        frames(70)
        seen.add(bubble_tiles(py))
    assert len(seen) > 1, \
        "board never changed after 3 shots (stuck at %d tiles)" % after_shot

    # Floaters. A pop that strands bubbles must drop them: each floater leaves the
    # board as a sprite (FALL_SLOTS below) that travels DOWN the screen. The failure
    # this catches is a sprite parked at its old cell, or one that blinks out.
    # Which shot strands bubbles is not predictable, so fire until one does.
    assert any(fall_watch(py, frames) for _ in range(40)), \
        "no floater was seen falling after 40 shots"

    # The score. It shows from the start of the level, so it must already read as
    # five digits; it must then move when bubbles pop. Which shot lands a match is
    # not predictable, so keep firing until one does.
    assert score(py) is not None, "score field is not SCORE + five digits (tiles %s / %s)" % \
        ([win_tile(py, c, SCORE_ROW) for c in range(SCORE_COL, SCORE_COL + SCORE_DIGITS)],
         [win_tile(py, c, SCORE_ROW + 1) for c in range(SCORE_COL, SCORE_COL + SCORE_DIGITS)])

    for _ in range(25):
        if score(py):
            break
        frames(6, "left")
        frames(3, "a")
        frames(90)
        # Harmless during play; restarts the game if the board filled up.
        py.button_press("start")
        py.tick(1, True)
        py.button_release("start")
    assert score(py), "score never moved"
    final_score = score(py)            # the strip is blank again once the title is back

    # The strip is the window layer, and the digits are in the window map only, so
    # ink where the digits are is what proves the window is on screen at all.
    assert strip_visible(py), "the score strip is not showing (window layer off screen?)"

    # The ceiling drop. It slides the board down by scrolling SCY instead of
    # rewriting the map under DISPLAY_OFF, which is what used to flash black. See
    # drop_watch(): SCY must leave 0, because the board walks up the map rather
    # than being rewritten, and be caught mid-slide at a non-multiple of 8 -- one
    # pixel a frame -- with the LCD still on for every one of those frames.
    drops = drop_watch(py, frames)
    mid = [lcdc for scy, lcdc in drops if scy % 8]
    seq = [scy for scy, _ in drops]
    # Settled = a value held over two frames in a row. Mid-slide SCY changes every
    # frame, so nothing else can produce that -- and one frame of a 16px slide does
    # fall on a tile boundary, so "tile aligned" on its own would not do.
    settled = {scy for i, scy in enumerate(seq) if i and scy == seq[i - 1]}
    assert any(seq), "SCY never left 0: a ceiling drop redrew the map instead of sliding it"
    assert mid, "SCY was never caught mid-slide: the drop did not slide"
    assert all(lcdc & 0x80 for lcdc in mid), \
        "the LCD was off mid-slide: that is the flash the slide is meant to replace"
    # A settled SCY is map_y0 * 8, and map_y0 is always even -- a board row is two
    # map rows tall, so an odd map_y0 would put half a bubble across the map wrap.
    assert all(scy % 16 == 0 for scy in settled), \
        "a settled SCY is not a multiple of 16: map_y0 is no longer even"
    # The drop has just added a row at the top of the board, and it must be drawn
    # at the top of the SCREEN. The flight and the board live in different
    # coordinate spaces -- pixels vs map_y0 plus SCY -- and reading the new row
    # through tile(), which follows the scroll, is what says the two agree.
    assert any(2 <= tile(py, c, r) <= 17 for r in (0, 1) for c in range(2, 18)), \
        "no bubbles at the top of the screen right after a drop: board and scroll disagree"

    # Game over must hand back to the title screen by itself, and must not leave
    # the finished game's score hanging over it (is_title() covers the strip).
    gap = game_over_to_title(py, frames)
    assert gap is not None, "no game over within 60 shots, so the title return is untested"
    assert gap > 0, "the game ended but the title screen never came back"
    assert 280 <= gap <= 380, \
        "title came back %d frames after game over, expected ~300 (5s)" % gap

    # A or B during the hold goes straight to the title instead of waiting it out.
    skip = game_over_to_title(py, frames, skip=True)
    assert skip is not None and 0 < skip < 280, \
        "A did not cut the game-over hold short (%s frames)" % skip

    # ...and that a ceiling drop does not hand back colours the player has cleared
    # off the board. Last, because it leaves the board in a clearable state. See
    # check_drop_keeps_colours(): blind play never narrows the board far enough for
    # this to happen on its own, so the state is forced.
    check_drop_keeps_colours(py, frames)

    print("ok: sgb_border=%s walls=%d ramp=%s audio=%s after_shot=%d counts=%s score=%d "
          "drop_scy=%d mid=%d game_over->title=%d frames (A: %d)"
          % (border, walls, ramp, audio, after_shot, sorted(seen), final_score, max(settled),
             len(mid), gap, skip))
    py.stop(save=False)


if __name__ == "__main__":
    main()
