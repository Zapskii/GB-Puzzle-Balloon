# BUBBLE — Puzzle Balloon

A Puzzle Bobble / Bust-A-Move clone for the original Game Boy (DMG — four shades of
grey), written in C with [GBDK-2020](https://github.com/gbdk-2020/gbdk-2020) (`lcc` /
SDCC, target SM83). The output is a plain 32KB ROM with no MBC.

Aim a launcher, fire a coloured bubble, watch it bounce off the walls and stick to the
hex grid. Groups of three or more of the same colour pop; anything left hanging with no
path back to the ceiling falls. The ceiling drops every few shots, and you lose if the
bubbles reach the bottom row.

Public repo: **<https://github.com/Zapskii/GB-Puzzle-Balloon>** — by Zapski.

## Build

```sh
git clone https://github.com/Zapskii/GB-Puzzle-Balloon.git
cd GB-Puzzle-Balloon
make            # produces bubble.gb
make usage      # ROM/RAM headroom
```

If GBDK-2020 is installed, point `make` at it:

```sh
make GBDK_HOME=/path/to/gbdk
```

Otherwise `make` falls back to the shared `gbdk-dev` Docker image, which is where the
toolchain lives by default here. That image is built from the sibling GB project:

```sh
make -C ../GB-Protector image    # only if the image is missing
```

## Run

Any DMG emulator will do. mGBA, SameBoy and Emulicious all work:

```sh
open -a mGBA bubble.gb
```

Emulicious has the better debugger and VRAM viewer; mGBA is the quickest to just play.
Prefer a real emulator over a headless one for anything visual — PyBoy starts VRAM at
zero, which hides bugs that depend on what was already on screen at boot.

### Super Game Boy border

On a Super Game Boy the game is framed by a border: "PUZZLE BALLOON" in the game's own
font over a field of bubbles, `art/border_sgb.png`. The ROM uploads it at boot with
CHR_TRN/PCT_TRN, gated on `sgb_check()`, so a plain Game Boy boots exactly as it always
did and only pays four frames of delay.

One thing does have to be set in the cartridge, in the header rather than the code:
`-Wm-ys` in the `Makefile` sets `0x0146 = 0x03`, the SGB flag. Without it the SGB BIOS
silently discards every SGB packet — and mGBA picks the handheld model from the same byte,
so it emulates a plain DMG and does the same. `sgb_check()` is then false, the border is
never uploaded, and nothing else looks wrong. `make test` asserts both header bytes
(`0x0146 = 0x03` with `0x014B = 0x33`), so losing the flag fails the test.

To see it, run the ROM in an emulator with SGB support. mGBA autodetects from the header,
so a plain `mgba bubble.gb` is enough; to force the model, `-C sgb.model=sgb` (or `sgb2`).
PyBoy cannot show it at all, as it does not emulate the SGB. To redraw or change the
border art, edit `tools/mkborder.py`, then:

```sh
tools/mkborder.py    # art/border_sgb.png
make border          # border_data.c/.h
```

`border_data.c/.h` are committed, so a plain `make` needs no Python. Mind the two steps:
`make border` regenerates `border_data.c` but does not relink the ROM, and `make` relinks but
does not regenerate the art — skip either and the ROM keeps the old border, which looks exactly
like the change not working.

## Controls

| Button | |
|---|---|
| START (or A) | start a game from the title screen |
| LEFT / RIGHT | aim — the sweep walks the aim dots round the arc |
| A or B | fire |
| START | continue to the next board after clearing one |
| A / B / START | skip the pause after a game over |

The aim dots follow the shot's real path, wall bounces included, and a marker shows the cell it
will stick to — both come from the same code the shot itself runs, so the preview cannot disagree
with the shot. The default aim is straight up, and the sweep is symmetric about it.

## Scoring and levels

Each popped bubble is 10 points, and each stranded bubble that falls is 20. A pop also bursts on
the spot for a moment before the bubbles go, so a hit reads as the shape you made.

**Streaks pay more.** The multiplier is how many shots in a row have popped, capped at eight, so a
run of pops on the same four bubbles pays 40, then 80, then 120, up to 320 a pop. A shot that pops
nothing breaks the streak, and every new board starts it over. The score stops at 65535 rather than
wrapping round — it will never show you a number you have not earned.

Clearing a board advances the level: the next board starts a row deeper (four rows of bubbles at
the start, five from level 2, never more than five), and the ceiling drops more often (every
`8 - level` shots, down to a floor of 4). Clearing all the bubbles wins the level; bubbles on the
bottom row lose the game. Losing returns to the title screen by itself after about five seconds,
resetting the score and level.

## Tests

```sh
make test       # headless PyBoy smoke test (tools/smoke.py)
make shot       # screenshot + scripted input, for eyeballing one frame
```

`make test` boots the ROM, starts a game and fires shots, then reads the BG tilemap, the
window tilemap and the sprite table to assert the game actually plays: the title screen
matches tile for tile, the walls are drawn, shots land and the board changes, stranded
bubbles fall, the score reads and moves, the default shot flies dead straight, a board's
first press fires without needing a release first, a ceiling drop slides the board instead
of flashing it (SCY leaves 0, is caught mid-slide, the LCD stays on, and the new row is on
screen at the top when it settles), and a game over hands back to the title on time. It
deliberately checks tile ids rather than the game's own variables, so it needs no symbol map
and does not break every time a global is added.

Two checks force a board rather than playing one, because blind play cannot reliably produce the
state they need: the ceiling drop's colours, and the pop and its scoring. The pop check rewrites
the board in RAM so the next shot is certain to pop four bubbles, then asserts the burst covers
exactly those four cells and nothing else, that it stays up for the right number of frames, that
ten pops in a row climb and then stop climbing at the cap, that a shot into a board it cannot
match pays nothing and ends the streak, and that a score forced to 65520 reads 65535 after one
more pop instead of wrapping.

It also checks the difficulty curve's two numbers straight out of `main.c` (at least two free rows
above the losing row at every level, and a ceiling drop no more often than every four shots), since
a headless run never clears a board and so never gets past level 1 to measure them in play.

Sound is checked in its own console, because the PyBoy the rest of the tests use runs with
`sound_emulated=False`, where every sound register reads 0 and every write is discarded. Each effect
has to reach the APU on its own channel: CH1 on firing, CH2 on a wall bounce, CH4 on a pop, and CH3
on the ceiling drop's warning. The warning is the fiddly one — it has to sound exactly one shot
before the drop, and exactly once per drop — so that check counts shots and CH3 bursts rather than
just listening for the tone.

It also checks the shape of the SGB border data, because PyBoy is not a Super Game Boy
and the upload itself never runs in the test — the sizes png2asset emits are what break
quietly if `make border` is run with the wrong flags. Two more static checks cover mistakes
that a tile-id comparison cannot see: `check_sgb_header()` (the two header bytes, without
which no SGB ever shows a border) and `check_font_order()`, which asserts the `FONT_GLYPHS`
comments spell out `FONT_ORDER` — a glyph row at the wrong index renders the wrong letter
while every tile id still lines up.

## Layout notes for contributors

The grid is 8 columns by 8 rows on a 16px pitch, so everything stays tile-aligned and the
board lives on the BG layer. Rows alternate between 8 bubbles and 7 (shifted right by 8px);
a ceiling drop pushes the rows down and toggles a parity flag rather than rewriting them,
so always use the `SHIFTED()` / `ROW_COLS()` macros and never `r & 1`.

A ceiling drop *slides* the board down rather than redrawing it, and that is why the launcher
strip and the playfield are on different layers. The board walks up the 32-row tilemap
(`map_y0`, always even) while `SCY` follows it down a pixel a frame, so the board's position
on *screen* is the one thing that never changes — which is what lets `cell_y()`, `hit_test()`
and `snap()` stay in plain screen coordinates. The strip is on the **window** layer, because
the window is the one layer `SCY` does not scroll; on the BG the score would slide off the
bottom of the screen with the board. Two consequences: draw the board only through
`draw_cell()`, which knows about `map_y0`, and write the strip with `set_win_tiles` rather
than `set_bkg_tiles`.

Two hardware limits shape the rendering: BG tile ids must stay below 128, because LCDC.4
is clear and anything higher aliases into the sprite tiles; and the DMG draws at most 10
sprites per scanline, which is why the fall animation staggers its floaters.

## Sound

Four one-shot effects, one per channel, so none of them can cut another off: a falling "pew" on
CH1 when a bubble is fired, a tick on CH2 when it bounces off a wall, a hiss on CH4 when a group
pops, and a low beep on CH3 as the ceiling drop telegraphs itself one shot early. No driver, no
timer, no note data — each effect is a few writes to the sound registers, and the hardware plays
the rest, so the frame loop never waits for audio. `audio.c` holds the module and its calibration
knobs.

Not done yet: no music, and no hand-designed levels (boards are random).
