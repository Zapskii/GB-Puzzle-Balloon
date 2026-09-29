/* audio.c -- DMG sound, four one-shot SFX for BUBBLE.
 *
 * No driver, no timer, no ISR, no tables of note data, no frame waiting. Each
 * effect is a handful of writes to the sound registers; the hardware plays the
 * rest. The frame loop is never held up by anything in this file.
 *
 * One channel per effect, so no effect can ever cut another off:
 *   CH1 (pulse + sweep)  sfx_fire     falling "pew"
 *   CH2 (pulse)          sfx_bounce   short high tick
 *   CH3 (wave)           sfx_warn     low square beep
 *   CH4 (noise)          sfx_pop      hiss burst
 *
 * Frequencies. The DMG channel period register X (NRx4 bits 2-0 << 8 | NRx3)
 * gives f = 131072 / (2048 - X), so X = 2048 - 131072 / f. The X values below
 * are folded from the Hz on the right AT COMPILE TIME: all integer, nothing
 * computed on the console. (131072 is a long constant in the expression, but
 * the division folds in the preprocessor, so only the small result ships.)
 *
 * Lengths. CH1/2/4 have a 6-bit length: duration = (64 - NRx1[5:0]) / 256 s.
 * CH3 has an 8-bit length: (256 - NR31) / 256 s. At 60 Hz one frame is 1/60 s,
 * so a 0.25 s length is 15 frames -- that is where "it stops by itself" comes
 * from, with no code of ours involved.
 */

#include <gb/gb.h>
#include <stdint.h>

#include "audio.h"

/* ==========================================================================
 * THE CALIBRATION KNOBS.  Everything a human would want to change by ear is
 * here and nowhere else. Loudness of an effect is the HIGH NIBBLE of its *_ENV
 * (0-15); a longer tail is a bigger envelope pace (step time = pace/64 s) or a
 * bigger length; pitch is the *_HZ value and nothing else needs touching --
 * the period registers are derived from it.
 * ========================================================================== */

#define AUDIO_MASTER   0x77u   /* NR50: left volume 7, right volume 7, VIN off */
#define AUDIO_PAN      0xFFu   /* NR51: all four channels to both sides        */

/* --- CH1, sfx_fire: falling sweep --- */
#define FIRE_SWEEP     0x3Du   /* NR10: sweep pace 3 (~1.4 frames/step),
                                * falling (bit 3), shift 5 (~5% per step)      */
#define FIRE_LEN       0x80u   /* NR11: duty 50%, length 0 = (64-0)/256 = 0.25s */
#define FIRE_ENV       0xF1u   /* NR12: volume 15, decay, pace 1 (15 steps in
                                * 15/64 s = 0.23 s, so it fades out just as the
                                * length counter cuts it)                       */
#define FIRE_HZ        800u    /* sweep starts here, ~150 Hz by the cut         */

/* --- CH2, sfx_bounce: short high tick --- */
#define BOUNCE_LEN     0xB0u   /* NR21: duty 50%, length 48 = 0.0625 s = ~4f    */
#define BOUNCE_ENV     0xF1u   /* NR22: volume 15, decay, pace 1                */
#define BOUNCE_HZ      1200u

/* --- CH3, sfx_warn: low square beep --- */
#define WARN_LEN       0xC0u   /* NR31: (256-192)/256 = 0.25 s = 15 frames      */
#define WARN_LEVEL     0x20u   /* NR32: bits 6-5 = 01 -> 100% output            */
#define WARN_HZ        330u    /* E4-ish: low enough to read as "warning"       */

/* --- CH4, sfx_pop: noise burst --- */
#define POP_LEN        0x20u   /* NR41: (64-32)/256 = 0.125 s = ~7 frames       */
#define POP_ENV        0xF1u   /* NR42: volume 15, decay, pace 1                */
#define POP_POLY       0x60u   /* NR43: 15-bit, divisor 0, clock shift 6
                                * -> ~8192 Hz hiss                             */

/* ------------------------------------------------------------------------- */

/* 131072 / HZ, folded at compile time, then X = 2048 - that. */
#define CH_PERIOD(hz)  (2048u - (131072u / (hz)))
#define PERIOD_LO(x)   ((uint8_t)((x) & 0xFFu))
#define PERIOD_HI(x)   ((uint8_t)(0xC0u | ((x) >> 8)))  /* trigger + length on */

#define FIRE_PERIOD    CH_PERIOD(FIRE_HZ)      /* 800 Hz  -> 1885 = 0x75D */
#define BOUNCE_PERIOD  CH_PERIOD(BOUNCE_HZ)    /* 1200 Hz -> 1939 = 0x793 */
#define WARN_PERIOD    CH_PERIOD(WARN_HZ)      /* 330 Hz  -> 1651 = 0x673 */

/* Channel 3's waveform. 0x0F,0x0F,... is the sample sequence 0,F,0,F,... i.e. a
 * 50% square wave -- same shape the pulse channels make, but a different timbre
 * at low pitch, which is what keeps the warning from reading as another blip.
 * Only 16 bytes, and written once, because on a DMG the wave RAM is not
 * accessible while the channel is playing. */
static const uint8_t WAVE_SQUARE[16] = {
    0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F,
    0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F, 0x0F
};

static void load_wave(void)
{
    uint8_t i;
    for (i = 0; i < 16u; i++) AUD3WAVE[i] = WAVE_SQUARE[i];
}

void audio_init(void)
{
    /* Power the APU off and on again. Off zeroes every sound register, which is
     * the only way to be sure of a known state: the DMG boot ROM has already
     * powered the APU on and played its own jingle through CH1/CH2, and on a
     * DMG the sound registers are READ-ONLY until NR52 bit 7 is set. Two writes
     * for a guaranteed clean slate. */
    NR52_REG = AUDENA_OFF;
    NR52_REG = AUDENA_ON;

    /* Channel 3 first, while it is off: the wave RAM is only writable then. */
    NR30_REG = 0x00u;
    load_wave();
    NR30_REG = 0x80u;          /* DAC on, and it stays on */

    /* Everything static, once. The SFX below only write the period and trigger,
     * so each one is two or three stores. */
    NR10_REG = FIRE_SWEEP;
    NR11_REG = FIRE_LEN;
    NR12_REG = FIRE_ENV;

    NR21_REG = BOUNCE_LEN;
    NR22_REG = BOUNCE_ENV;

    NR31_REG = WARN_LEN;
    NR32_REG = WARN_LEVEL;

    NR41_REG = POP_LEN;
    NR42_REG = POP_ENV;
    NR43_REG = POP_POLY;

    /* A powered-up DAC with a zero volume envelope is silent, which is the
     * state we want at boot: nothing plays until an SFX triggers it. */
    NR50_REG = AUDIO_MASTER;
    NR51_REG = AUDIO_PAN;
}

/* --- the four effects. Fire and forget: no loops, no waiting, ~8-20 cycles a
 * write, and the sound runs on in hardware. --- */

void sfx_fire(void)
{
    NR13_REG = PERIOD_LO(FIRE_PERIOD);
    NR14_REG = PERIOD_HI(FIRE_PERIOD);
}

void sfx_bounce(void)
{
    NR23_REG = PERIOD_LO(BOUNCE_PERIOD);
    NR24_REG = PERIOD_HI(BOUNCE_PERIOD);
}

void sfx_pop(void)
{
    NR44_REG = 0xC0u;          /* trigger; NR41/42/43 already loaded */
}

void sfx_warn(void)
{
    NR33_REG = PERIOD_LO(WARN_PERIOD);
    NR34_REG = PERIOD_HI(WARN_PERIOD);
}
