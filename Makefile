# BUBBLE build.
#   make          build bubble.gb   (GBDK if GBDK_HOME is set, else Docker)
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
else
  # GBDK is not installed on this host, so run the toolchain out of the image.
  # lcc must be the FULL PATH: it is not on PATH inside gbdk-dev.
  # -u keeps build artefacts owned by the user rather than root.
  RUN   := docker run --rm -u $(shell id -u):$(shell id -g) -v "$(CURDIR)":/work -w /work gbdk-dev
  LCC   := /opt/gbdk/bin/lcc
  USAGE := /opt/gbdk/bin/romusage
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
CFLAGS = -Wm-yn"BUBBLE" -Wl-m -Wl-j

all: bubble.gb

bubble.gb: main.c
	$(RUN) $(LCC) $(CFLAGS) -o $@ main.c

usage: bubble.gb
	$(RUN) $(USAGE) bubble.map -g

test: bubble.gb
	$(PY) tools/smoke.py bubble.gb

shot: bubble.gb
	$(PY) tools/shot.py bubble.gb /tmp/bubble.png 120 "$(SCRIPT)"

clean:
	rm -f bubble.gb *.map *.sym *.lst *.rel *.asm *.ihx *.noi *.adb *.cdb
	rm -rf /tmp/bubble.png

.PHONY: all test usage shot clean
