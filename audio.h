#ifndef AUDIO_H
#define AUDIO_H

#include <stdint.h>

/* DMG sound: four one-shot SFX, no driver, no timer, no frames burned. */
void audio_init(void);          /* once, from main(), after DISPLAY_ON */

void sfx_fire(void);            /* CH1: falling "pew"          */
void sfx_bounce(void);          /* CH2: short high tick        */
void sfx_pop(void);             /* CH4: noise burst            */
void sfx_warn(void);            /* CH3: one low beep           */

#endif
