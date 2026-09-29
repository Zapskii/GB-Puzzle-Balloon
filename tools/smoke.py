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

    # main() must clear the BG map before it shows the display. The DMG boot ROM
    # leaves its Nintendo logo in that map, and an uncleared map shows the logo
    # (or, on hardware that does not zero VRAM the way an emulator does, random
    # garbage) while the game waits for START.
    frames(150)
    stale = [(r, c) for r in range(18) for c in range(20) if tile(py, c, r) != 0]
    assert not stale, "screen not cleared before START: %d stale tiles, e.g. %s" % (
        len(stale), stale[:4])

    # ...but the idle screen is not empty. It shows the launcher and the next
    # bubble as sprites, so a blank board does not read as a dead screen.
    assert py.memory[0xFE00] != 0, "idle screen shows no launcher sprite"

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
