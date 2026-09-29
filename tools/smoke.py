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


def main():
    rom = sys.argv[1] if len(sys.argv) > 1 else "bubble.gb"
    border = check_border_data()
    check_sgb_header(rom)
    check_font_order()
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

    # main() runs the DMG boot ROM, generates the bubble tiles, then parks in
    # `while (!(joypad() & (J_START | J_A)))`. Hold START across that whole
    # window, then RELEASE it: the release is what lets `waitpadup()` through,
    # and the board is not built until it does. A press/release per frame is not
    # enough here -- the hold has to span frames for the boot wait to see it.
    for _ in range(150):
        py.button_press("start")
        py.tick(1, True)
    py.button_release("start")

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

    start = bubble_tiles(py)
    assert start >= 24, "starting board is empty (%d tiles)" % start

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
    seen = {start}
    for _ in range(3):
        frames(6, "left")
        frames(3, "a")
        frames(70)
        seen.add(bubble_tiles(py))
    assert len(seen) > 1, "board never changed after 3 shots (stuck at %d tiles)" % start

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

    print("ok: sgb_border=%s walls=%d start_tiles=%d counts=%s score=%d "
          "drop_scy=%d mid=%d game_over->title=%d frames (A: %d)"
          % (border, walls, start, sorted(seen), final_score, max(settled),
             len(mid), gap, skip))
    py.stop(save=False)


if __name__ == "__main__":
    main()
