# Puzzle Balloon build.
#   make          build Puzzle-Balloon.gb  (GBDK if GBDK_HOME is set, else Docker)
#   make border   regenerate border_data.c from art/border_sgb.png (SGB border)
#   make usage    ROM/RAM headroom
#   make shot     headless PyBoy screenshot (see tools/shot.py)
#   make clean
#
# The gbdk-dev image is shared with the other GB projects; build it from
# ../GB-Protector/Dockerfile (`make -C ../GB-Protector image`) if it is missing.

GBDK_HOME ?= /opt/gbdk/

ifneq ($(wildcard $(GBDK_HOME)/bin/lcc),)
  RUN   :=
  LCC   := $(GBDK_HOME)/bin/lcc
  USAGE := $(GBDK_HOME)/bin/romusage
  P2A   := $(GBDK_HOME)/bin/png2asset
else
  # GBDK is not installed on this host, so run the toolchain out of the image.
  # lcc must be the FULL PATH: it is not on PATH inside gbdk-dev.
  # -u keeps build artefacts owned by the user rather than root.
  RUN   := docker run --rm -u $(shell id -u):$(shell id -g) -v "$(CURDIR)":/work -w /work gbdk-dev
  LCC   := /opt/gbdk/bin/lcc
  USAGE := /opt/gbdk/bin/romusage
  P2A   := /opt/gbdk/bin/png2asset
endif

ifneq ($(wildcard .venv/bin/python),)
  PY ?= .venv/bin/python
else ifneq ($(wildcard ../GB-Protector/.venv/bin/python),)
  PY ?= ../GB-Protector/.venv/bin/python
else
  PY ?= python3
endif

# DMG-only, plain 32KB ROM (no MBC). Add -Wm-yc for GB Color compatible,
# or -Wm-yt0x01 -Wm-yo4 etc. for MBC1 when you outgrow 32KB.
# -Wl-m -Wl-j : linker map + NoICE symbols, for romusage and emulator debuggers.
# -Wm-ys : the SGB flag (header 0x0146 = 0x03). REQUIRED for anything SGB: without
#   it the SGB BIOS silently discards every SGB packet -- and mGBA decides the
#   model from the same flag, so it emulates a plain DMG and does the same. That
#   makes sgb_check() false, so the border is never uploaded and nothing looks
#   broken: it is. 0x014B (old licensee) is 0x33 already, which is the other half
#   of the test. A DMG ignores both bytes, so this costs nothing there.
CFLAGS = -Wm-ys -Wm-yn"Puzzle-Balloon" -Wl-m -Wl-j

CFILES = main.c audio.c sgb_border.c border_data.c
HFILES = audio.h sgb_border.h border_data.h

all: Puzzle-Balloon.gb

Puzzle-Balloon.gb: $(CFILES) $(HFILES)
	$(RUN) $(LCC) $(CFLAGS) -o $@ $(CFILES)

# The Super Game Boy border: art/border_sgb.png (256x224; the 160x144 game area
# at x=48,y=40 is transparent) -> border_data.c/.h. Those are committed, like the
# sibling projects, so a plain `make` needs no Python. -pack_mode sgb is what gets
# the SGB layout -- 4bpp tiles, a 256x224 map, one attribute byte per cell --
# instead of a GB screen; -use_map_attributes keeps that byte, which is the
# per-cell palette. Regenerate the art itself with tools/mkborder.py.
border:
	$(RUN) $(P2A) art/border_sgb.png -map -bpp 4 -max_palettes 4 \
	      -pack_mode sgb -use_map_attributes -c border_data.c

usage: Puzzle-Balloon.gb
	$(RUN) $(USAGE) Puzzle-Balloon.map -g

test: Puzzle-Balloon.gb
	$(PY) tools/smoke.py Puzzle-Balloon.gb

shot: Puzzle-Balloon.gb
	$(PY) tools/shot.py Puzzle-Balloon.gb /tmp/puzzle-balloon.png 120 "$(SCRIPT)"

clean:
	rm -f Puzzle-Balloon.gb *.map *.sym *.lst *.rel *.asm *.ihx *.noi *.adb *.cdb
	rm -rf /tmp/puzzle-balloon.png

.PHONY: all border test usage shot clean
