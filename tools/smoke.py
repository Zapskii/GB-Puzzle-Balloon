#!/usr/bin/env python3
"""Headless smoke test for Puzzle Balloon, run against the built ROM.

WHY THIS EXISTS: a screenshot only tells you the screen is not blank. This boots
the ROM in PyBoy, starts a game and fires shots, then asserts the board actually
changed -- which is the difference between "main.c runs" and "main.c plays".

    make test            # or: tools/smoke.py Puzzle-Balloon.gb

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


def board_match(py):
    """Locate main.c's `board[8][8]` in WRAM and the row parity, by matching the screen.

    The linker map lists no statics, so the only handle on the board is that its
    bytes ARE what is drawn: 0 for an empty cell, colour + 1 otherwise, and
    column 7 always empty in the shifted rows. The board's SCREEN position is
    invariant (that is what the drop's SCY walk preserves), so this matches at any
    settled moment, whatever map_y0 has become.

    Which rows are shifted depends on `parity`, which is not readable from here
    (SHIFTED(r) is (r ^ parity) & 1 and every drop toggles it), so both are tried
    and the byte match decides. The parity comes back with the address because a
    caller that rewrites the board in RAM has to know which rows take 7 cells.
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

    hits = [(a, parity) for parity in (0, 1) for a in range(0xC000, 0xE000 - 64)
            if list(py.memory[a:a + 64]) == drawn(parity)]
    assert len(hits) == 1, \
        "found %d WRAM windows matching the drawn board, expected exactly 1" % len(hits)
    return hits[0]


def board_address(py):
    """main.c's `board[8][8]` address in WRAM -- see board_match()."""
    return board_match(py)[0]


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


# --- aim preview ---
DOT_SLOTS = (4, 5, 6)           # the aim dots; sprite slot, x/y at 0xFE00 + 4*slot
MARK_SLOTS = (32, 33)           # the landing marker: a bubble, so two slots


def oam(py, slot):
    """A sprite's OAM x,y. y == 0 means hidden."""
    return py.memory[0xFE01 + slot * 4], py.memory[0xFE00 + slot * 4]


def aim_tables(path=None):
    """LAUNCH_X/LAUNCH_Y and the angle table, out of main.c.

    Read rather than repeated: the ray this check measures the dots against IS the
    ANG_DX/ANG_DY pair, so hardcoding a copy here would let the check drift away
    from the thing it is checking.
    """
    src = open(path or os.path.join(HERE, os.pardir, "main.c")).read()

    def num(name):
        m = re.search(r"#define\s+%s\s+(\d+)" % name, src)
        assert m, "main.c has no `#define %s`: update this check" % name
        return int(m.group(1))

    def table(name):
        m = re.search(r"%s\[NUM_ANGLES\]\s*=\s*\{(.*?)\};" % name, src, re.S)
        assert m, "main.c has no %s[NUM_ANGLES]" % name
        return [int(v) for v in re.findall(r"-?\d+", m.group(1))]

    return num("LAUNCH_X"), num("LAUNCH_Y"), table("ANG_DX"), table("ANG_DY")


def check_aim_preview(py, frames, path=None):
    """The aim preview has to follow the REAL, wall-bouncing path.

    Before this, the dots were three points along a straight ray from the launcher
    (`LAUNCH + ANG_D * n`, n = (i+1)*4 fixed-point units), so at every angle whose
    shot bounces the preview pointed somewhere the shot never goes. The launcher is
    64px from either wall and the fastest a shot closes that is 3.9px a frame -- the
    15-degree aim, which is also the one that reaches a wall soonest -- so no shot
    can have bounced within its first ~16 frames: dots at the first three frames,
    where the old preview put them, cannot show a bounce at all. That is why they
    moved to a quarter/half/three quarters of the path, and it is why this check has
    to read the outermost dot rather than the nearest one.

    Two claims, and both are about the preview agreeing with the shot:

      * At the shallowest aim (the far right stop, 15 degrees) the outermost dot is
        nowhere near the straight ray. Measured as the perpendicular distance from
        the ray, NOT as a distance from where the old formula put its dot: the old
        formula drew dots one, two and three frames out, so a build spreading dots
        along a straight ray would differ from it too, and a position-only check
        would pass on a preview that still ignores bounces. The perpendicular
        distance is zero for any point on the ray, however it is spaced.

      * The marker is the cell the shot actually lands in. That is the check that
        the preview and the shot did not drift apart: the game calls the same
        snap(), so the marker has to be within one cell (16px) of where the flight
        stopped, and the cell it points at has to be empty on the board.

    Plus, both must be off screen through the flight and back afterwards: the marker
    is a bubble's worth of tiles, so one left standing through the flight or the pop
    would read as a real bubble on the board, and one that never comes back would
    mean the preview is simply gone.
    """
    lx, ly, adx, ady = aim_tables(path)
    # The shallowest rightward aim: the largest ANG_DX, which is the one that
    # reaches the right wall soonest and so the one a straight ray lies about most.
    ang = max(range(len(adx)), key=lambda i: adx[i])
    assert adx[ang] > 0, "no rightward aim in ANG_DX: update this check"
    parked = ly + 8                      # the launcher's OAM y while it is parked

    # Steer to that stop. The key repeat needs 3 frames an aim step and the sweep
    # does not wrap, so holding it is free -- and the preview then needs a few more
    # frames to finish walking the path (it is deliberately sliced per frame, see
    # preview_step() in main.c). Waiting for the marker is how this knows the walk
    # has stopped and the dots are final; a build with no marker walks nothing, so
    # it just times out here and the dot assertion below still gets read -- which is
    # the one that has to fire on a build that draws the straight ray.
    frames(60, "right")
    for _ in range(150):
        py.tick(1, True)
        if any(py.memory[0xFE00 + s * 4] for s in MARK_SLOTS):
            break
    ox, oy = oam(py, DOT_SLOTS[-1])
    assert ox and oy, "the aim dots are not on screen at the far right stop"

    # OAM to screen pixels: the dot sprite is centred on the point it marks (the
    # +4/+12 in the aim loop), so px = x-4, py = y-12.
    px, py_ = ox - 4, oy - 12
    dx, dy = adx[ang] / 16.0, ady[ang] / 16.0       # px per flight frame
    # Perpendicular distance from the straight ray through the launcher.
    cross = dx * (py_ - ly) - dy * (px - lx)
    off = abs(cross) / (dx * dx + dy * dy) ** 0.5
    assert off > 8, \
        "the outermost aim dot is %.1fpx off the straight ray from the launcher " \
        "(dot at %d,%d; launcher %d,%d; ray %.2f,%.2f px/frame): the preview is " \
        "drawing the straight line and ignoring wall bounces" \
        % (off, px, py_, lx, ly, dx, dy)

    # ...and it is not merely somewhere else on the ray: the shot at this angle
    # crosses the right wall, so the dot has to be back inside the playfield.
    assert 16 <= px <= 144, \
        "the outermost aim dot is at x=%d, outside the playfield: the preview is " \
        "not reflecting off the walls at all" % px

    # The marker: on a cell centre, on a cell the board says is empty. A cell centre
    # is a multiple of 8 in x (the two row parities sit 8px apart) and of 16 in y,
    # plus 8 because place_bubble_sprite writes the centre y through OAM's own +8.
    mx, my = oam(py, MARK_SLOTS[0])
    assert mx and my, "no landing marker at the far right stop: nothing shows where " \
                      "the shot will rest"
    assert mx % 8 == 0 and my % 16 == 0, \
        "the landing marker is at %d,%d: not the centre of a grid cell" % (mx, my)
    col, row = (mx - 8) // 8, (my - 16) // 8
    assert tile(py, col, row) == 0, \
        "the landing marker sits on a bubble (tile %d at %d,%d): its cell has to " \
        "be one the shot can land in" % (tile(py, col, row), col, row)

    # Fire, and watch the flight. The watch does not stop at the landing frame: it
    # runs until the dots are back, which is the aim loop resuming, so a dot left
    # standing through a pop or the drop's slide is caught too.
    frames(1, "a")
    flying, back, last, unhidden = False, False, None, []
    for _ in range(400):
        py.tick(1, True)
        fx, fy = oam(py, 0)
        dots_up = any(oam(py, s) for s in DOT_SLOTS)
        if fy and fy != parked:                     # off the launcher: in flight
            flying = True
            last = (fx, fy)
            up = [s for s in DOT_SLOTS + MARK_SLOTS if any(oam(py, s))]
            if up:
                unhidden.append((up, fy))
        elif flying and dots_up:                    # ...and the aim loop is back
            back = True
            break
    assert flying, "the shot never left the launcher"
    assert not unhidden, \
        "the aim dots / landing marker were still on screen on %d frame(s) of the " \
        "flight (slots %s, at launcher y=%d): the marker would read as a bubble " \
        "sitting on the board" % (len(unhidden), unhidden[0][0], unhidden[0][1])
    assert back, "the aim dots never came back after the shot: the preview is not " \
                 "meant to be hidden while aiming"
    assert last and abs(last[0] - mx) <= 16 and abs(last[1] - my) <= 16, \
        "the shot stopped at OAM %s but the marker was at %d,%d: the preview and the " \
        "shot disagree about the landing cell, which is what snap() is meant to " \
        "stop" % (last, mx, my)
    return "off-ray=%.0fpx land=%s" % (off, last)


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
    sfx_pop() leaves CH4 silent, and a drop warned late -- the off-by-one in
    sfx_warn()'s call site -- is caught by counting the shots between the burst and
    the slide, which must be exactly one.

    What this CANNOT see, so do not read it as covering these: sfx_pop() inside the
    pop loop rather than before it (CH4 retriggers, so N calls are one long burst and
    one edge), sfx_pop() above the n < MIN_MATCH early return (a non-matching landing
    still starts CH4 by the assertion's own measure), or a warn that comes EARLY --
    only the shot count separates "one shot early" from "two", and only the burst
    count separates it from "every shot". The pop assertion proves CH4 is reachable
    from resolve(), not how many times resolve() asks for it.
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
    # ...and the sweep, in which the preview walks the path it copies the flight
    # step to build -- wall reflections included. That is exactly where an
    # sfx_bounce() would leak into aiming, so it is watched a frame at a time: the
    # SFX are short bursts and one from the start of the sweep would be over by the
    # end of it.
    aiming = 0
    for _ in range(60):
        py.button_press("right")
        py.tick(1, True)
        py.button_release("right")
        aiming |= channels()
    assert not aiming, \
        "channel(s) %s ran while only steering: the aim preview's bounces are " \
        "making the shot's sound" % format(aiming, "04b")

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
    # previous shot's window. Counted in SHOTS, not frames: a frame gap has a lower
    # bound but no useful upper one, so "two shots early" and "every shot" both clear
    # it. Two shots early is `early == 2`; every shot is caught by `warns`, which
    # counts BURSTS rather than frames seen high -- one CH3 burst holds NR52's CH3
    # bit for its whole length, so an edge is the only honest burst count.
    warned, warn_shot, warns, was_warn, gaps, frame = None, None, 0, False, [], 0
    for shot in range(24):
        tick(6, "left")
        tick(3, "a")
        slide, slid, prev, n = None, False, None, 0
        while n < 160:
            py.tick(1, True)
            frame += 1
            n += 1
            on = bool(channels() & CH_WARN)
            if on and not was_warn:
                warns += 1
                warned, warn_shot = frame, shot
            was_warn = on
            scy = py.memory[SCY_REG]
            if scy % 8:
                slid = True            # mid-slide: the drop is happening now
                if slide is None:
                    slide = frame
            elif slid and scy and scy == prev:
                assert warned is not None, \
                    "a ceiling drop at frame %d was never warned: sfx_warn() is not " \
                    "wired to the shot before the drop" % slide
                gaps.append((shot - warn_shot, warns, slide - warned))
                break
            prev = scy
        if gaps:
            break

    assert gaps, "no ceiling drop within 24 shots: the drop warning is untested"
    early, warns, ahead = gaps[0]
    assert warns == 1, \
        "the drop warning sounded %d times before the drop: it is one telegraph per " \
        "drop, not one per shot" % warns
    assert early == 1, \
        "the drop warning came %d shot(s) before the drop (%d frames ahead): it has " \
        "to be exactly one -- later and the player is warned at the instant they are " \
        "punished, earlier and it does not read as being about the drop" % (early, ahead)

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
    return "fire/bounce/pop/warn(early=%d, bursts=%d)" % (early, warns)


# --- pop feedback and combo scoring ---
T_BURST = 48                     # main.c's T_BURST: 4 tiles, one 2x2 cell
BURST_TILES = range(T_BURST, T_BURST + 4)
# The forced pop: the cells the burst has to land on -- see force() below.
CLUSTER = ((1, 2), (1, 3), (1, 4), (2, 3))
# The numbers the pop's beat and the combo landed with. main.c's own #defines are
# read and asserted equal to these: a check that EXPECTED whatever main.c happens
# to say would adapt to a build that changed them and never fail, which is how
# POP_FRAMES = 60 passed here.
WANT_POP_FRAMES = 6
WANT_COMBO_MAX = 8


def screen_cell(r, c, parity):
    """The four BG map tiles a board cell occupies, as (col, row) SCREEN cells.

    Spelled out rather than derived from the drawn map so the check knows where a
    cell is even after the board in RAM has been rewritten -- which is the whole
    point of forcing one. The shift is SHIFTED(r) = (r ^ parity) & 1.
    """
    s = (r ^ parity) & 1
    return {(2 + c * 2 + s + dx, r * 2 + dy) for dx in (0, 1) for dy in (0, 1)}


def burst_tiles(py):
    """Every playfield cell showing a burst tile, as (col, row) screen cells."""
    return {(c, r) for r in range(16) for c in range(2, 18)
            if tile(py, c, r) in BURST_TILES}


def check_pop_and_combo(rom):
    """A popped cluster has to be marked before it goes, and pops in a row have to pay more.

    Everything here is measured on a board this check forces, because which shot
    pops is not predictable in ordinary play. Five claims:

      * **The burst.** Every cell of the cluster turns into a burst tile for a few
        frames (main.c's POP_FRAMES) before any of it is erased. Asserted as an
        exact tile set -- the burst has to cover all four cells of the forced
        cluster and nothing else, which pins both the beat and the cell it is drawn
        on. An instant erase leaves this set empty.

      * **Its duration.** The full 4-cell set is counted frame by frame and has to
        be up for POP_FRAMES - 1 of them. The -1 is measured, not a fudge: the
        erase loop's first cell goes in the same frame the last of
        wait_frames(POP_FRAMES)' vsyncs returns in, so that frame is already
        partial by the time this can sample it (measured 2/5/9 full frames at
        POP_FRAMES 3/6/10, i.e. POP_FRAMES - 1 every time), and the partial frames
        after it are not counted. WANT_POP_FRAMES pins the number itself, because a
        build that raised POP_FRAMES would otherwise just be sampled for longer and
        still agree with itself.

      * **The combo, and the cap from both sides.** COMBO_MAX + 2 shots that each
        pop four bubbles, one after another. The first COMBO_MAX gains have to rise
        by a constant positive step -- which is what says the multiplier is the run
        length and not flat and not doubling -- and the gains then have to PLATEAU:
        the three from COMBO_MAX on are equal. That is the cap from below (a run
        that keeps climbing is uncapped, main.c's `combo++` with the
        `if (combo < COMBO_MAX)` test dropped) and from above, since the cap has to
        be reached INSIDE the two extra shots (COMBO_MAX raised past the window
        never plateaus at all). The plateau's value has to be COMBO_MAX x the
        single-shot award, so a cap that is hit early fails on the position as well
        as on the value.

      * **A dud shot ends the run.** With the run at the cap, a forced shot that
        matches NOTHING -- force(pop=False): rows 1-7 empty and row 0 a row of a
        colour the launcher is not holding, so the bubble lands in row 1 beside a
        different colour -- has to pay exactly 0, and the pop after it has to be
        worth the single-shot base again, then twice it. That is main.c's
        `combo = 0` in resolve()'s early return: without it the cap survives a shot
        that popped nothing and the next pop pays COMBO_MAX times the base.

      * **No wrap.** The score is a uint16_t shown as five digits. Sampled at every
        pop and asserted non-decreasing -- a wrapped total reads as a drop -- and
        then forced, in RAM, to 65520 immediately before a pop: the field must read
        65535 afterwards. A build that lets the total wrap reads a few hundred
        there. That is main.c's add_score() clamp, and forcing the score is the only
        way to reach it headlessly: an honest smoke run is nowhere near 65535.

    WHAT THIS DOES NOT COVER.

      * **The per-board half of the reset**: main.c's `combo = 0` at the top of
        play(). The run has to start again at 1 on a new board, and nothing here
        says so -- a second board is the only thing that can tell, and reaching one
        means clearing a board or losing one. It is a named gap rather than a
        covered claim because the first board's combo starts at 0 in main.c's BSS
        whether the line is there or not, so on one board the two builds are
        identical: a check that claimed this would be measuring nothing. (The
        recipe, if it is ever wanted: force a board that the next pop EMPTIES, so
        the win screen and START lead to the next board, then force a pop there and
        assert it pays the base again. The forced board makes that route
        deterministic, but it is a second game state to drive, so it is not done
        here.)

      * **The award arithmetic itself.** The bound that makes one pop's points fit
        a uint16_t at COMBO_MAX is a property of main.c's constants, not of
        anything observable here: the board holds 60 bubbles (8 and 7 columns over
        8 rows), so the most one pop can pay is a three-bubble match with the other
        57 left floating -- 3*10 + 57*20 = 1170 -- which at COMBO_MAX 8 is 9360 of
        65535. The clamp is only reachable because the score is forced into RAM.

      * **The sound.** Nothing here hears the pop: sfx_pop() is check_audio()'s.

    Its own PyBoy, like check_audio(), and for the same kind of reason plus one
    more: it rewrites the board and the score in RAM, which leaves the machine in a
    state no other check can read. A fresh instance is a fresh board, which is what
    board_match() needs -- it locates board[] by matching it against the screen, and
    after this check has forced one the two no longer agree.
    """
    src = open(os.path.join(HERE, os.pardir, "main.c")).read()

    def define(name):
        m = re.search(r"#define\s+%s\s+(\d+)" % name, src)
        assert m, "main.c has no `#define %s`: update this check" % name
        return int(m.group(1))

    # Read out of main.c to be named in the messages, and checked against the
    # values the feature landed with rather than against themselves: a build that
    # moved POP_FRAMES or COMBO_MAX would otherwise just be sampled for longer, or
    # plateaued later, and agree with itself (POP_FRAMES = 60 passed here that way).
    pop_frames, combo_max = define("POP_FRAMES"), define("COMBO_MAX")

    py = PyBoy(rom, window="null", sound_emulated=False)

    def frames(n, key=None):
        for _ in range(n):
            if key:
                py.button_press(key)
            py.tick(1, True)
            if key:
                py.button_release(key)

    for _ in range(600):
        py.tick(1, True)
        if is_title(py):
            break
    else:
        raise AssertionError("the title screen never came up: cannot reach a board")

    frames(3, "start")
    for _ in range(300):
        py.tick(1, True)
        if all(py.memory[0xFE01 + s * 4] for s in (4, 5, 6)):
            break
    else:
        raise AssertionError("the aim dots never appeared: no board was started")

    addr, parity = board_match(py)
    scy = py.memory[SCY_REG]                # settled: only a ceiling drop moves it

    def dots_up():
        return all(py.memory[0xFE01 + s * 4] for s in (4, 5, 6))

    def want_burst():
        """The screen cells the burst has to cover, for the row shift in force().

        SHIFTED(r) is (r ^ parity) & 1, and a ceiling drop flips `parity`, so this
        is asked once per shot rather than once per check.
        """
        w = set()
        for r, c in CLUSTER:
            w |= screen_cell(r, c, parity)
        return w

    def force(pop=True):
        """Rewrite the board in RAM into the shape the next shot is sure to pop.

        Rows 2-7 empty, so the shot meets nothing on the way up; (1,2)-(1,4) in the
        colour the launcher is holding, so the bubble it lands joins a run of three
        and the pop is four cells; and row 0 a full row of a different colour, so
        the board is never cleared (which would end the level) and nothing is left
        floating (whose 20-point bonus would land in the same score gain).

        Three cells and not two: the default aim is dead vertical and the launcher
        is at x=80, which is the centre of column 3 in a shifted row and between
        columns 3 and 4 in an unshifted one -- and which of those a row is depends
        on the parity. Covering 2, 3 and 4 makes the pop the same either way, and
        the screen cells above are computed for the parity that is really there.

        pop=False leaves row 1 empty as well. The shot then lands in row 1 beside a
        row of a colour it is not, matches nothing, and nothing floats: a dud, which
        is what the combo's reset needs. It still lands and stays, and the check
        reads it back out of row 1.
        """
        cur = py.memory[0xFE02] >> 2                    # the launcher's colour
        for a in range(64):
            py.memory[addr + a] = 0
        for c in range(8 - (parity & 1)):               # ROW_COLS(0)
            py.memory[addr + c] = ((cur + 1) & 3) + 1   # ...a colour it is not
        if pop:
            for c in (2, 3, 4):
                py.memory[addr + 8 + c] = cur + 1
        return cur

    def shot(pop=True):
        """Force the board, fire, wait the shot out, and report what it did.

        The wait is for the aim dots to come back: play() hides them from the
        moment a shot fires until the next aim loop, which is after the pop's
        frames, the erase, the floaters and any ceiling drop. They have to be seen
        to go first -- a dud draws no burst, so "a burst appeared" cannot be the
        trigger for every shot.

        A ceiling drop comes every 8 shots at level 1 and flips `parity`, so it is
        tracked here from SCY, which only a drop moves: without that the shots
        after the drop would look for the burst one tile to the left of where the
        game draws it.
        """
        nonlocal parity, scy
        cur = force(pop)
        want = want_burst()
        before = score(py)
        wram0 = bytes(py.memory[0xC000:0xE000])
        frames(3, "a")
        burst, full, gone = None, 0, False
        for _ in range(200):
            py.tick(1, True)
            t = burst_tiles(py)
            if t and burst is None:
                burst = t
            if t == want:
                full += 1                    # the whole cluster, not a cell of it
            if not dots_up():
                gone = True
                continue
            if gone:
                break
        assert gone, \
            "a shot never handed the aim loop back: nothing popped, nothing landed, " \
            "or the game left the board"
        now = py.memory[SCY_REG]
        if now != scy:                       # a ceiling drop: the row shift flipped
            scy, parity = now, parity ^ 1
        return dict(cur=cur, want=want, burst=burst, full=full, before=before,
                    after=score(py), wram0=wram0, wram1=bytes(py.memory[0xC000:0xE000]))

    # --- the run, out to the cap and past it ----------------------------------
    # The cap the run is measured against is the one the feature landed with, not
    # main.c's, which is only named in the failure messages: a cap taken from
    # main.c would move the plateau with it and never fail.
    cap = WANT_COMBO_MAX
    gains, seen_score, fulls, first = [], [], [], None
    for shot_i in range(cap + 2):
        r = shot()
        assert r["burst"], \
            "shot %d popped %d bubbles and none of them was drawn as a burst before " \
            "it went (no tiles %d-%d anywhere on the board): the pop still reads as " \
            "an instant erase" % (shot_i, len(CLUSTER), T_BURST, T_BURST + 3)
        if first is None:
            first = r
        assert r["before"] is not None and r["after"] is not None, \
            "the score field stopped reading as SCORE + five digits during the pop " \
            "(before %s, after %s)" % (r["before"], r["after"])
        gains.append(r["after"] - r["before"])
        seen_score += [r["before"], r["after"]]
        fulls.append(r["full"])

    # The burst covers exactly the cells that are about to pop: the three forced
    # bubbles and the bubble this shot landed. Anything else would mean the beat is
    # not marking what it says it is marking.
    assert first["burst"] == first["want"], \
        "the burst covered %s, not the %d cells of the popped cluster (%s, colour " \
        "%d): something other than the matched bubbles is being marked" % (
            sorted(first["burst"] or ()), len(CLUSTER), sorted(first["want"]),
            first["cur"])

    # ...and it stays up for the beat, not for a frame and not for the rest of the
    # board: POP_FRAMES - 1 full frames per pop, the value the beat landed with
    # (main.c's own POP_FRAMES is in the message because a build that moved it
    # would be sampled for longer and still agree with itself).
    assert set(fulls) == {WANT_POP_FRAMES - 1}, \
        "the full %d-cell burst was up for %s frame(s) per pop, and main.c's " \
        "POP_FRAMES is %d: it has to be the %d frames the beat landed with (the " \
        "erase loop then takes the cells a frame each, and those partial frames " \
        "are not counted)" % (
            len(first["want"]) // 4, sorted(set(fulls)), pop_frames,
            WANT_POP_FRAMES - 1)

    # COMBO_MAX + 2 pops of four bubbles, back to back. Flat scoring pays the same
    # for each; an uncapped run never stops climbing; a multiplier equal to the run
    # length pays 40, 80, ... and then holds at the cap.
    assert all(g > 0 for g in gains), \
        "a forced shot into a ready-made cluster paid nothing: gains %s (the board " \
        "was forced but the shot did not pop it)" % gains

    steps = [gains[i + 1] - gains[i] for i in range(cap - 1)]
    assert len(set(steps)) == 1 and steps[0] > 0, \
        "the first %d forced pops of the same four bubbles paid %s, and main.c's " \
        "COMBO_MAX is %d: the step from one pop to the next over those %d pops is " \
        "%s, and it has to be the same positive number every time -- that is what " \
        "says the multiplier is the run length, where flat scoring steps by 0 and a " \
        "doubling scheme widens (a step that falls to 0 early is the cap being " \
        "reached early)" % (cap, gains[:cap], combo_max, cap, sorted(set(steps)))

    plateau = gains[cap - 1:cap + 2]
    assert plateau[0] == plateau[1] == plateau[2], \
        "main.c's COMBO_MAX is %d, and %d pops in a row paid %s: the run is at the " \
        "cap by the %dth pop, so from there the award has to stop climbing and the " \
        "last three gains have to be equal -- they are %s, so it is still climbing " \
        "(or the cap is further out than this window is long)" % (
            combo_max, cap + 2, gains, cap, plateau)
    assert plateau[0] == gains[0] * cap, \
        "the award stopped climbing at %d, not %d x the single-shot %d = %d: the cap " \
        "is being reached at the wrong run length" % (
            plateau[0], gains[0], cap, gains[0] * cap)

    assert seen_score == sorted(seen_score), \
        "the score went down during play: %s (escalating points wrapped the uint16_t " \
        "and the five-digit field is lying)" % seen_score

    # --- a dud shot ends the run (resolve()'s `combo = 0` early return) -------
    # The run is at the cap, so a build that keeps the multiplier across a shot that
    # popped nothing is worth COMBO_MAX times the base on the next pop and is caught
    # there; one that pays for the dud at all is caught here.
    dud = shot(pop=False)
    assert dud["before"] is not None and dud["after"] is not None, \
        "the score field stopped reading as SCORE + five digits over the dud shot " \
        "(before %s, after %s)" % (dud["before"], dud["after"])
    assert dud["after"] - dud["before"] == 0, \
        "a shot that popped nothing paid %d points (%s -> %s), and it has to pay " \
        "exactly nothing: only a pop can pay" % (
            dud["after"] - dud["before"], dud["before"], dud["after"])
    landed = [c for c in (2, 3, 4) if py.memory[addr + 8 + c] == dud["cur"] + 1]
    assert landed, \
        "the dud shot's bubble is not in row 1 (cols 2-4 hold %s and the launcher " \
        "was holding colour %d): the shot did not land where this check put it, so " \
        "it proves nothing about a dud" % (
            [py.memory[addr + 8 + c] for c in (2, 3, 4)], dud["cur"])

    again = shot()
    assert again["after"] - again["before"] == gains[0], \
        "the pop after a dud paid %d, not the single-shot base %d: the run was at " \
        "the cap %d before the dud, so this is the multiplier surviving a shot that " \
        "popped nothing" % (
            again["after"] - again["before"], gains[0], plateau[0])
    second = shot()
    assert second["after"] - second["before"] == gains[0] * 2, \
        "the second pop after a dud paid %d, not twice the single-shot base %d: the " \
        "run did not restart at 1" % (second["after"] - second["before"], gains[0])

    # --- the clamp, at the top -------------------------------------------------
    # The displayed total is the only handle on main.c's `score` (the linker map
    # lists no statics, so there is no symbol for it), and the score is a 16-bit
    # little-endian value in WRAM. Looking for the displayed number alone is not
    # enough -- two unrelated words can hold it, and the check then fails with a
    # message about a score that is fine. What is unique is the pair of
    # TRANSITIONS: the word that went from the old displayed total to the new one
    # across a pop, twice over, on two pops of different sizes. The board's own 64
    # bytes are excluded -- its cells are 0..4 and could hold the same pair.
    def transitions(r):
        b, a = r["wram0"], r["wram1"]
        old, new = r["before"], r["after"]
        return {i for i in range(0xE000 - 0xC000 - 1)
                if b[i] == (old & 0xFF) and b[i + 1] == (old >> 8)
                and a[i] == (new & 0xFF) and a[i + 1] == (new >> 8)
                and not (addr <= 0xC000 + i < addr + 64)
                and not (addr <= 0xC000 + i + 1 < addr + 64)}

    hits = transitions(again) & transitions(second)
    assert len(hits) == 1, \
        "found %d WRAM words that went from the displayed score to the next one " \
        "over both %s -> %s and %s -> %s, expected exactly 1: cannot force the " \
        "score to the top" % (len(hits), again["before"], again["after"],
                              second["before"], second["after"])

    top = score(py)
    word = 0xC000 + hits.pop()
    py.memory[word] = 0xF0                  # 65520: any pop at all overflows
    py.memory[word + 1] = 0xFF
    over = shot()
    assert over["before"] == top, \
        "the displayed score moved from %d to %s before the forced shot: cannot " \
        "tell what the clamp did" % (top, over["before"])
    assert over["after"] != top, \
        "the score never moved away from %d: the forced shot did not pop" % top
    assert over["after"] == 65535, \
        "the score was 65520 and one more pop of the same four bubbles pushed it " \
        "over the top, and the field reads %d: the award wrapped the uint16_t " \
        "instead of stopping at 65535, so the display is showing a number the " \
        "player has not earned" % over["after"]

    py.stop(save=False)
    return "burst=%d cells for %d frames combo=%s dud=%d,%d,%d clamp=%d" % (
        len(first["want"]) // 4, pop_frames - 1, "->".join(str(g) for g in gains),
        dud["after"] - dud["before"], again["after"] - again["before"],
        second["after"] - second["before"], over["after"])


def main():
    rom = sys.argv[1] if len(sys.argv) > 1 else "Puzzle-Balloon.gb"
    border = check_border_data()
    check_sgb_header(rom)
    check_font_order()
    ramp = check_difficulty()
    # Its own console, and so its own PyBoy: the one below runs with
    # sound_emulated=False, where every sound register reads 0 and every write is
    # thrown away. Run before that instance exists rather than alongside it.
    audio = check_audio(rom)
    # Also its own instance, and also before the shared one exists: it rewrites the
    # board and the score in RAM, which leaves a machine no other check can read.
    pop = check_pop_and_combo(rom)
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

    # ...and the aim preview must follow the shot's real path, bounced off the
    # walls, with a marker on the cell it will land in. See check_aim_preview().
    preview = check_aim_preview(py, frames)

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

    print("ok: sgb_border=%s walls=%d ramp=%s audio=%s pop=%s after_shot=%d counts=%s "
          "score=%d drop_scy=%d mid=%d game_over->title=%d frames (A: %d) preview=%s"
          % (border, walls, ramp, audio, pop, after_shot, sorted(seen), final_score,
             max(settled), len(mid), gap, skip, preview))
    py.stop(save=False)


if __name__ == "__main__":
    main()
