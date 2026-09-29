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
import sys

from pyboy import PyBoy

MAP = 0x9800            # BG map base (LCDC bit 3 clear)
WALL_TILES = 18 * 4     # both side walls, full height

# The title screen's arch and text. Must match main.c.
TITLE_COUNT = [8, 7, 6, 5, 4, 3]
TITLE_START = [0, 0, 1, 1, 2, 2]
T_FONT = 18                              # first font tile id
FONT_ORDER = " ABELNOPRSTUZ"             # glyph order, matches FONT_ORDER in main.c
TITLE_TEXT = [("PUZZLE BALLOON", 3, 12),  # string, tile column, tile row
              ("PRESS START", 4, 14)]


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
    return py.memory[MAP + row * 32 + col]


def bubble_tiles(py):
    """Bubble tiles (a bubble is 4 tiles) in the playfield."""
    return sum(1 for r in range(16) for c in range(2, 18)
               if 2 <= tile(py, c, r) <= 17)


def wall_count(py):
    return sum(1 for r in range(18) for c in (0, 1, 18, 19)
               if tile(py, c, r) == 1)


def main():
    rom = sys.argv[1] if len(sys.argv) > 1 else "bubble.gb"
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
    frames(5)

    walls = wall_count(py)
    assert walls == WALL_TILES, "walls not drawn: %d/%d tiles" % (walls, WALL_TILES)

    start = bubble_tiles(py)
    assert start >= 24, "starting board is empty (%d tiles)" % start

    # Fire a few shots; each should stick somewhere, so the board must change.
    seen = {start}
    for _ in range(3):
        frames(6, "left")
        frames(3, "a")
        frames(70)
        seen.add(bubble_tiles(py))
    assert len(seen) > 1, "board never changed after 3 shots (stuck at %d tiles)" % start

    print("ok: walls=%d start_tiles=%d counts=%s" % (walls, start, sorted(seen)))
    py.stop(save=False)


if __name__ == "__main__":
    main()
