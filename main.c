/*
 * BUBBLE - Puzzle Bobble / Bust-A-Move style skeleton for Game Boy (DMG)
 * GBDK-2020.  No external assets: bubble tiles are generated at startup.
 *
 * NOTE: written without access to a compiler, so expect to fix a typo or two.
 *
 * Layout (pixels):
 *   x 0..15    left wall  (tiles 0-1)
 *   x 16..143  playfield  (8 bubbles * 16px)
 *   x 144..159 right wall (tiles 18-19)
 *   y 0..127   8 rows * 16px      (row 7 = "lose" row)
 *   y 128..143 launcher strip (window layer, so a ceiling drop can scroll
 *              the playfield past it without dragging it along -- see main())
 *
 * Simplification: row pitch is 16px (2 tiles) so the grid stays tile-aligned.
 * Odd rows are shifted right by 8px (1 tile) and hold 7 bubbles, so the
 * neighbour logic is genuinely hexagonal, just a bit "looser" looking than
 * the arcade's 14px pitch. Moving to 14px pitch means drawing bubbles as
 * sprites or using a non-tile-aligned scheme; do that later if you care.
 */

#include <gb/gb.h>
#include <gb/sgb.h>
#include <rand.h>
#include <stdint.h>
#include <string.h>

#include "border_data.h"
#include "sgb_border.h"

/* ---------------- configuration ---------------- */
#define GRID_ROWS    8
#define GRID_COLS    8            /* keep 8: cell index = (r<<3)|c */
#define NUM_COLOURS  4
#define FIELD_X      16
#define LOSE_ROW     (GRID_ROWS - 1)
#define MIN_MATCH    3

#define LAUNCH_X     80
#define LAUNCH_Y     136
#define NEXT_X       32
#define NEXT_Y       136

#define WALL_L       (FIELD_X + 8)            /* min bubble centre x  = 24  */
#define WALL_R       (FIELD_X + 128 - 8)      /* max bubble centre x  = 136 */
#define HIT_DIST_SQ  196                      /* 14px collision radius^2    */

/* BG tile ids */
#define T_BLANK      0
#define T_WALL       1
#define T_BUBBLE     2            /* 16 tiles: colour c starts at 2 + c*4 */
/* BG tiles 18..30: just the glyphs the title text needs. "PUZZLE BALLOON" and
 * "PRESS START" between them use 12 letters and a space, so a full character set
 * would be a lot of ROM for nothing. BG ids must stay below 128 whatever gets
 * added here: LCDC bit 4 is 0, so >= 128 aliases into the sprite tiles. */
#define T_FONT       18

/* Sprite tile ids (8x16 mode: bubble colour c at c*4) */
#define S_DOT        16

/* Sprite slots (each bubble = 2 sprites in 8x16 mode) */
#define SPR_FLY      0
#define SPR_NEXT     2
#define SPR_DOT      4
#define NUM_DOTS     3
#define SPR_FALL     8            /* floaters, 2 sprites each: 8 .. 8+2*FALL_MAX-1 */

/* Floating bubbles fall off the bottom as sprites rather than blinking out.
 * Sprites because the BG grid only moves in 16px steps and this should read as
 * gravity. Starts are staggered: a whole row of floaters shares one cell_y, and
 * the hardware draws only 10 sprites per scanline, so they must not all occupy
 * the same rows at once. FALL_MAX is what the free sprite slots allow (slots
 * 0-6 are the launcher, next bubble and aim dots, of 40). */
#define FALL_MAX     12
#define FALL_SPEED   4            /* px per frame, same as a fired bubble */
#define FALL_DELAY   4            /* frames between one floater starting and the next */

/* ---------------- fixed-point aim table ----------------
 * 12.4 fixed point (16 units = 1px), speed = 4px/frame = 64 units.
 * 15 deg (right) to 165 deg (left) in 10 deg steps, PLUS the exact vertical
 * at index ANG_MID: a 15 + 10k sweep never lands on 90 deg, and the nearest
 * shots (85/95 deg) drift ~12px over the playfield's height -- more than half
 * a bubble -- so a target directly above the launcher could not be hit.
 * Index increases towards the left.                                  */
#define ANG_MID    8
#define NUM_ANGLES 17
static const int8_t ANG_DX[NUM_ANGLES] = {
     62,  58,  52,  45,  37,  27,  17,   6,
      0,
     -6, -17, -27, -37, -45, -52, -58, -62
};
static const int8_t ANG_DY[NUM_ANGLES] = {
    -17, -27, -37, -45, -52, -58, -62, -64,
    -64,
    -64, -62, -58, -52, -45, -37, -27, -17
};

/* ---------------- state ---------------- */
static uint8_t  board[GRID_ROWS][GRID_COLS];   /* 0 = empty, else colour+1 */
static uint8_t  parity;        /* toggles on every ceiling drop            */
static uint8_t  level;
static uint16_t score;         /* shown in the launcher strip by draw_score() */

/* Which tile row of the BG map board row 0 sits on: even, 0..30, and always
 * stepping by 2 because a board row is 2 tile rows tall.  A ceiling drop walks
 * it up the map and scrolls SCY to match (see ceiling_drop), so the board's
 * position on SCREEN is the invariant and never changes: screen row = row * 16,
 * whatever map_y0 happens to be.  That is what lets cell_y(), hit_test() and
 * snap() carry on in plain screen coordinates.  The map is 32 rows, the board
 * uses 16, and the walk is mod 32 -- 32 * 8px = 256 = SCY's whole range, so it
 * wraps with nothing to fix up. */
static uint8_t  map_y0;

/* A row's horizontal shift. Using a parity flag means a ceiling drop just
 * moves the rows down and toggles it: every existing row keeps its shift. */
#define SHIFTED(r)   ((uint8_t)(((r) ^ parity) & 1))
#define ROW_COLS(r)  ((uint8_t)(GRID_COLS - SHIFTED(r)))

/* scratch for flood fills */
static uint8_t visited[GRID_ROWS][GRID_COLS];
static uint8_t stack[GRID_ROWS * GRID_COLS];
static uint8_t cluster[GRID_ROWS * GRID_COLS];
static uint8_t nb_r[6], nb_c[6];

/* what is currently falling: where each floater came from, its colour, and how
 * many frames it stays on screen (which depends on how far it has to drop) */
static uint8_t fall_cells[FALL_MAX];
static uint8_t fall_colour[FALL_MAX];
static uint8_t fall_frames[FALL_MAX];

/* ---------------- graphics generation ---------------- */
static uint8_t gfx[16 * 16];   /* 4 colours * 4 tiles * 16 bytes */

static const uint8_t wall_gfx[16] = {
    0xAA,0xFF, 0x55,0xFF, 0xAA,0xFF, 0x55,0xFF,
    0xAA,0xFF, 0x55,0xFF, 0xAA,0xFF, 0x55,0xFF
};

/* tile 16 = small dot (aim indicator), tile 17 = blank (bottom half) */
static const uint8_t dot_gfx[32] = {
    0,0, 0,0, 0,0, 0x18,0x18, 0x18,0x18, 0,0, 0,0, 0,0,
    0,0, 0,0, 0,0, 0,0,       0,0,       0,0, 0,0, 0,0
};

/* Colour value (0-3) of pixel (px,py) in a 16x16 bubble of colour c */
static uint8_t bubble_pixel(uint8_t c, uint8_t px, uint8_t py)
{
    int8_t dx = (int8_t)(px * 2) - 15;     /* doubled coords, centre = 15 */
    int8_t dy = (int8_t)(py * 2) - 15;
    uint16_t d2 = (uint16_t)(dx * dx + dy * dy);

    if (d2 > 225) return 0;                /* outside circle  */
    if (d2 > 169) return 3;                /* dark outline    */
    /* little shine in the top-left */
    if (c && ((px == 4 && py == 4) || (px == 5 && py == 4) || (px == 4 && py == 5)))
        return 0;
    switch (c) {
        case 0:  return 0;                                 /* hollow    */
        case 1:  return 1;                                 /* light     */
        case 2:  return 2;                                 /* dark      */
        default: return ((px ^ py) & 1) ? 3 : 1;          /* checker   */
    }
}

/* Tile order per colour: LeftTop, LeftBottom, RightTop, RightBottom.
 * That order is what 8x16 sprites need, and the BG code below uses it too. */
static void build_bubble_gfx(void)
{
    uint8_t c, col, row, y, x, p, lo, hi;
    uint8_t *out = gfx;
    for (c = 0; c < NUM_COLOURS; c++)
        for (col = 0; col < 2; col++)
            for (row = 0; row < 2; row++)
                for (y = 0; y < 8; y++) {
                    lo = hi = 0;
                    for (x = 0; x < 8; x++) {
                        p  = bubble_pixel(c, (uint8_t)(col * 8 + x), (uint8_t)(row * 8 + y));
                        lo = (uint8_t)((lo << 1) | (p & 1));
                        hi = (uint8_t)((hi << 1) | (p >> 1));
                    }
                    *out++ = lo;
                    *out++ = hi;
                }
}

/* ---------------- grid helpers ---------------- */
static uint8_t cell_x(uint8_t r, uint8_t c)
{
    return (uint8_t)(FIELD_X + 8 + (c << 4) + (SHIFTED(r) << 3));
}
static uint8_t cell_y(uint8_t r)
{
    return (uint8_t)((r << 4) + 8);
}

/* Fill nb_r/nb_c with the valid hex neighbours of (r,c); returns count.
 * Same row: c-1, c+1.  Rows above/below: columns base and base+1, where
 * base = 0 for a shifted row, -1 for an unshifted one.                    */
static const int8_t DR[6] = { 0, 0, -1, -1,  1, 1 };
static const int8_t DC[6] = {-1, 1,  0,  1,  0, 1 };

static uint8_t get_neighbours(uint8_t r, uint8_t c)
{
    uint8_t n = 0, k;
    int8_t rr, cc;
    int8_t base = SHIFTED(r) ? 0 : -1;
    for (k = 0; k < 6; k++) {
        rr = (int8_t)r + DR[k];
        cc = (int8_t)c + DC[k];
        if (k >= 2) cc += base;
        if (rr < 0 || rr >= GRID_ROWS || cc < 0) continue;
        if ((uint8_t)cc >= ROW_COLS(rr)) continue;   /* cc >= 0, checked above */
        nb_r[n] = (uint8_t)rr;
        nb_c[n] = (uint8_t)cc;
        n++;
    }
    return n;
}

/* ---------------- drawing ---------------- */
static void draw_cell(uint8_t r, uint8_t c)
{
    uint8_t t[4];
    uint8_t v = board[r][c];
    if (v) {
        uint8_t b = (uint8_t)(T_BUBBLE + ((v - 1) << 2));
        t[0] = b;     /* top-left     */
        t[1] = b + 2; /* top-right    */
        t[2] = b + 1; /* bottom-left  */
        t[3] = b + 3; /* bottom-right */
    } else {
        t[0] = t[1] = t[2] = t[3] = T_BLANK;
    }
    set_bkg_tiles((uint8_t)((FIELD_X >> 3) + (c << 1) + SHIFTED(r)),
                  (uint8_t)((map_y0 + (r << 1)) & 31), 2, 2, t);
}

static void draw_board(void)
{
    uint8_t r, c, n;
    /* The walls and the blank field cover all 32 map rows, not just the 18 the
     * screen can show: a ceiling drop slides the view up two rows at a time, so
     * every row of the map comes past the walls sooner or later. */
    fill_bkg_rect(0, 0, 2, 32, T_WALL);
    fill_bkg_rect(18, 0, 2, 32, T_WALL);
    fill_bkg_rect(2, 0, 16, 32, T_BLANK);
    /* The strip is the window layer, so it needs its own copy of the walls --
     * without them the wall would stop 16px short of the bottom of the screen.
     * Clearing the middle here is what also takes a just-finished game's score
     * off the title screen when draw_board() runs for the title. */
    fill_win_rect(0, 0, 20, 2, T_BLANK);
    fill_win_rect(0, 0, 2, 2, T_WALL);
    fill_win_rect(18, 0, 2, 2, T_WALL);
    for (r = 0; r < GRID_ROWS; r++) {
        n = ROW_COLS(r);
        for (c = 0; c < n; c++)
            if (board[r][c]) draw_cell(r, c);
    }
}

/* Both live with the font, further down. */
static void draw_score(void);
static void draw_level(void);

static void redraw_all(void)
{
    DISPLAY_OFF;
    map_y0 = 0;             /* back to the top of the map */
    SCY_REG = 0;            /* SCY is always map_y0 * 8 */
    draw_board();
    draw_score();
    draw_level();
    DISPLAY_ON;
}

static void place_bubble_sprite(uint8_t spr, uint8_t colour, uint8_t cx, uint8_t cy)
{
    set_sprite_tile(spr,     (uint8_t)(colour << 2));
    set_sprite_tile(spr + 1, (uint8_t)((colour << 2) + 2));
    move_sprite(spr,     cx,     (uint8_t)(cy + 8));   /* OAM x = x+8, y = y+16 */
    move_sprite(spr + 1, (uint8_t)(cx + 8), (uint8_t)(cy + 8));
}

static void hide_all_sprites(void)
{
    uint8_t i;
    for (i = 0; i < 40; i++) move_sprite(i, 0, 0);
}

static void wait_frames(uint8_t n) { while (n--) vsync(); }

/* ---------------- board logic ---------------- */

/* Flood fill of same-colour cells from (r,c). Result in cluster[]. */
static uint8_t flood_same(uint8_t r, uint8_t c)
{
    uint8_t colour = board[r][c];
    uint8_t sp = 0, count = 0, n, k, idx, cr, cc;

    memset(visited, 0, sizeof visited);
    stack[sp++] = (uint8_t)((r << 3) | c);
    visited[r][c] = 1;

    while (sp) {
        idx = stack[--sp];
        cr = idx >> 3;
        cc = idx & 7;
        cluster[count++] = idx;
        n = get_neighbours(cr, cc);
        for (k = 0; k < n; k++) {
            uint8_t nr = nb_r[k], nc = nb_c[k];
            if (!visited[nr][nc] && board[nr][nc] == colour) {
                visited[nr][nc] = 1;
                stack[sp++] = (uint8_t)((nr << 3) | nc);
            }
        }
    }
    return count;
}

/* Drop the n recorded floaters off the bottom of the screen.
 *
 * Each one keeps its board cell until its sprite takes over, so nothing ever
 * blinks out: the bubble stays put, a sprite appears over it at the same spot,
 * the cell is cleared underneath, and the sprite falls from there. */
#define FALL_EXIT_Y  152          /* fully clear of the 144px screen by here */

static void animate_fall(uint8_t n)
{
    uint8_t k, t, total = 0;

    if (!n) return;

    for (k = 0; k < n; k++) {
        uint8_t r = (uint8_t)(fall_cells[k] >> 3);
        uint8_t f = (uint8_t)(((FALL_EXIT_Y - cell_y(r)) / FALL_SPEED) + 1);
        fall_frames[k] = f;
        if (f > total) total = f;                 /* the slowest one's flight */
    }
    total = (uint8_t)(total + (n - 1) * FALL_DELAY);

    for (t = 0; t < total; t++) {
        for (k = 0; k < n; k++) {
            uint8_t start = (uint8_t)(k * FALL_DELAY);
            uint8_t spr   = (uint8_t)(SPR_FALL + (k << 1));
            uint8_t r     = (uint8_t)(fall_cells[k] >> 3);
            uint8_t c     = (uint8_t)(fall_cells[k] & 7);
            uint8_t age;

            if (t < start) continue;              /* still on the board */
            age = (uint8_t)(t - start);

            if (age == 0) {                       /* the sprite takes over */
                board[r][c] = 0;
                draw_cell(r, c);
            }
            if (age >= fall_frames[k]) {
                move_sprite(spr, 0, 0);
                move_sprite((uint8_t)(spr + 1), 0, 0);
                continue;
            }
            place_bubble_sprite(spr, fall_colour[k], cell_x(r, c),
                                (uint8_t)(cell_y(r) + age * FALL_SPEED));
        }
        vsync();
    }
}

/* Remove everything not connected to the ceiling. Returns number removed. */
static uint8_t drop_floating(void)
{
    uint8_t sp = 0, n, k, idx, cr, cc, r, c, removed = 0;

    memset(visited, 0, sizeof visited);
    n = ROW_COLS(0);
    for (c = 0; c < n; c++)
        if (board[0][c]) { visited[0][c] = 1; stack[sp++] = c; }

    while (sp) {
        idx = stack[--sp];
        cr = idx >> 3;
        cc = idx & 7;
        n = get_neighbours(cr, cc);
        for (k = 0; k < n; k++) {
            uint8_t nr = nb_r[k], nc = nb_c[k];
            if (!visited[nr][nc] && board[nr][nc]) {
                visited[nr][nc] = 1;
                stack[sp++] = (uint8_t)((nr << 3) | nc);
            }
        }
    }

    /* Record the floaters rather than erasing them here: animate_fall clears each
     * cell at the moment its sprite takes over. Anything past FALL_MAX has no
     * sprite slot to spare, so it is simply removed. */
    for (r = 0; r < GRID_ROWS; r++) {
        n = ROW_COLS(r);
        for (c = 0; c < n; c++) {
            if (!board[r][c] || visited[r][c]) continue;
            if (removed < FALL_MAX) {
                fall_cells[removed]  = (uint8_t)((r << 3) | c);
                fall_colour[removed] = (uint8_t)(board[r][c] - 1);
            } else {
                board[r][c] = 0;
                draw_cell(r, c);
            }
            removed++;
        }
    }
    animate_fall(removed < FALL_MAX ? removed : (uint8_t)FALL_MAX);
    return removed;
}

/* Handle a newly placed bubble. Returns 1 if something popped. */
static uint8_t resolve(uint8_t r, uint8_t c)
{
    uint8_t n = flood_same(r, c);
    uint8_t i, idx, extra;
    if (n < MIN_MATCH) return 0;

    for (i = 0; i < n; i++) {
        idx = cluster[i];
        board[idx >> 3][idx & 7] = 0;
        draw_cell(idx >> 3, idx & 7);
        vsync();
    }
    extra = drop_floating();
    score += (uint16_t)n * 10 + (uint16_t)extra * 20;
    draw_score();
    return 1;
}

static uint8_t colour_mask(void)
{
    uint8_t r, c, m = 0;
    for (r = 0; r < GRID_ROWS; r++)
        for (c = 0; c < ROW_COLS(r); c++)
            if (board[r][c]) m |= (uint8_t)(1 << (board[r][c] - 1));
    return m;
}

/* Only hand out colours that are still on the board (avoids dead-ends). */
static uint8_t pick_colour(void)
{
    uint8_t mask = colour_mask(), c;
    if (!mask) return 0;
    do { c = rand() & 3; } while (!(mask & (1 << c)));
    return c;
}

static uint8_t bottom_reached(void)
{
    uint8_t c;
    for (c = 0; c < ROW_COLS(LOSE_ROW); c++)
        if (board[LOSE_ROW][c]) return 1;
    return 0;
}

/* Push every row down one and add a fresh row at the top, then slide the board
 * down into place.
 *
 * The slide is why the board lives on map rows that move. Rewriting the map --
 * which is what this used to do, under DISPLAY_OFF -- flashes the whole screen
 * black for the length of the rewrite. Instead the board walks up the 32-row map
 * and the VIEW slides down it: the new row 0 is written into the two map rows
 * that were one board row above the old row 0, which at this moment are two tile
 * rows off the top of the screen, so the map can be written with the LCD running
 * and nothing tears. SCY then walks those two rows into view a pixel a frame,
 * and the board appears to move down. Nothing between here and the next drop
 * depends on which map rows the board is on: screen position is what the rest of
 * the game works in, and that is unchanged.
 *
 * SCY decreases to move content down.  It goes past 0 and wraps (the first drop
 * takes it 0 -> 240), which is harmless: 32 map rows of 8px is exactly SCY's
 * 256, so a wrapped SCY shows the same rows shifted, and the walk is mod 32. */
static void ceiling_drop(void)
{
    uint8_t r, c, n, i, scy;
    for (r = GRID_ROWS - 1; r > 0; r--)
        memcpy(board[r], board[r - 1], GRID_COLS);
    parity ^= 1;
    memset(board[0], 0, GRID_COLS);
    n = ROW_COLS(0);
    for (c = 0; c < n; c++)
        board[0][c] = (uint8_t)((rand() & 3) + 1);

    map_y0 = (uint8_t)((map_y0 - 2) & 31);
    /* Blank the slot first: a shifted row only fills 7 of the 8 cells, and the
     * map rows being reused here held a board row that is now elsewhere. */
    fill_bkg_rect(2, map_y0, 16, 2, T_BLANK);
    for (c = 0; c < n; c++)
        if (board[0][c]) draw_cell(0, c);

    scy = (uint8_t)((map_y0 << 3) + 16);        /* the view before this drop */
    for (i = 0; i < 16; i++) {
        vsync();
        SCY_REG = (uint8_t)(scy - 1 - i);
    }
}

static void init_board(uint8_t rows)
{
    uint8_t r, c;
    memset(board, 0, sizeof board);
    parity = 0;
    for (r = 0; r < rows; r++)
        for (c = 0; c < ROW_COLS(r); c++)
            board[r][c] = (uint8_t)((rand() & 3) + 1);
}

/* ---------------- collision & snapping ---------------- */

static uint8_t hit_test(uint8_t cx, uint8_t cy)
{
    int8_t r, r0 = (int8_t)(cy >> 4);
    uint8_t c, n;
    int16_t dx, dy;

    for (r = r0 - 1; r <= r0 + 1; r++) {
        if (r < 0 || r >= GRID_ROWS) continue;
        n = ROW_COLS(r);
        for (c = 0; c < n; c++) {
            if (!board[r][c]) continue;
            dx = (int16_t)cx - cell_x(r, c);
            dy = (int16_t)cy - cell_y(r);
            if (dx >= 14 || dx <= -14 || dy >= 14 || dy <= -14) continue;
            if (dx * dx + dy * dy < HIT_DIST_SQ) return 1;
        }
    }
    return 0;
}

/* Find the empty cell nearest to (cx,cy). Returns 0 if none. */
static uint8_t snap(uint8_t cx, uint8_t cy, uint8_t *pr, uint8_t *pc)
{
    int8_t r, r0 = (int8_t)(cy >> 4);
    uint8_t c, n, found = 0;
    uint16_t best = 0xFFFF, d2;
    int16_t dx, dy;

    for (r = r0 - 1; r <= r0 + 1; r++) {
        if (r < 0 || r >= GRID_ROWS) continue;
        n = ROW_COLS(r);
        for (c = 0; c < n; c++) {
            if (board[r][c]) continue;
            dx = (int16_t)cx - cell_x(r, c);
            dy = (int16_t)cy - cell_y(r);
            d2 = (uint16_t)(dx * dx + dy * dy);
            if (d2 < best) { best = d2; *pr = (uint8_t)r; *pc = c; found = 1; }
        }
    }
    return found;
}

/* ---------------- one round of play ---------------- */
/* returns 1 = level cleared, 0 = game over */
static uint8_t play(void)
{
    uint8_t cur, next, ang = ANG_MID, rep = 0, keys, prev = 0xFF, shots = 0;
    uint8_t drop_every = (level >= 5) ? 3 : (uint8_t)(8 - level);
    uint8_t i, hit, r, c, mask;
    int16_t fx, fy, fdx, fdy, cx, cy;

    cur  = pick_colour();
    next = pick_colour();

    for (;;) {
        /* ---------- aim ---------- */
        for (;;) {
            vsync();
            keys = joypad();

            if (keys & (J_LEFT | J_RIGHT)) {
                if (rep == 0) {
                    if (keys & J_LEFT)       { if (ang < NUM_ANGLES - 1) ang++; }
                    else                     { if (ang > 0) ang--; }
                }
                if (++rep >= 3) rep = 0;
            } else rep = 0;

            place_bubble_sprite(SPR_FLY,  cur,  LAUNCH_X, LAUNCH_Y);
            place_bubble_sprite(SPR_NEXT, next, NEXT_X,   NEXT_Y);
            for (i = 0; i < NUM_DOTS; i++) {
                int16_t n = (int16_t)(i + 1) * 4;
                uint8_t px = (uint8_t)(LAUNCH_X + ((ANG_DX[ang] * n) >> 4));
                uint8_t py = (uint8_t)(LAUNCH_Y + ((ANG_DY[ang] * n) >> 4));
                set_sprite_tile(SPR_DOT + i, S_DOT);
                move_sprite(SPR_DOT + i, (uint8_t)(px + 4), (uint8_t)(py + 12));
            }

            if ((keys & (J_A | J_B)) && !(prev & (J_A | J_B))) { prev = keys; break; }
            prev = keys;
        }

        /* ---------- fly ---------- */
        for (i = 0; i < NUM_DOTS; i++) move_sprite(SPR_DOT + i, 0, 0);
        place_bubble_sprite(SPR_NEXT, next, NEXT_X, NEXT_Y);

        fx  = (int16_t)LAUNCH_X << 4;
        fy  = (int16_t)LAUNCH_Y << 4;
        fdx = ANG_DX[ang];
        fdy = ANG_DY[ang];

        for (;;) {
            vsync();
            fx += fdx;
            fy += fdy;

            if (fx < WALL_L * 16)      { fx = WALL_L * 32 - fx; fdx = -fdx; }
            else if (fx > WALL_R * 16) { fx = WALL_R * 32 - fx; fdx = -fdx; }

            cx = fx >> 4;
            if (fy < 8 * 16) { cy = 8; hit = 1; }         /* ceiling */
            else { cy = fy >> 4; hit = hit_test((uint8_t)cx, (uint8_t)cy); }

            place_bubble_sprite(SPR_FLY, cur, (uint8_t)cx, (uint8_t)cy);
            if (hit) break;
        }

        /* ---------- land ---------- */
        move_sprite(SPR_FLY, 0, 0);
        move_sprite(SPR_FLY + 1, 0, 0);

        if (!snap((uint8_t)cx, (uint8_t)cy, &r, &c)) return 0;
        board[r][c] = (uint8_t)(cur + 1);
        draw_cell(r, c);
        resolve(r, c);

        /* Cleared the board -> win, tested BEFORE the drop below adds a fresh
         * row and un-clears it. */
        if (!colour_mask()) return 1;

        if (++shots >= drop_every) { shots = 0; ceiling_drop(); }

        if (bottom_reached()) return 0;

        /* next bubble becomes current; re-roll if its colour is gone */
        cur = next;
        mask = colour_mask();
        if (!(mask & (1 << cur))) cur = pick_colour();
        next = pick_colour();
    }
}

/* ---------------- misc ---------------- */
static void flash(uint8_t times)
{
    while (times--) {
        BGP_REG = 0x1B; wait_frames(8);
        BGP_REG = 0xE4; wait_frames(8);
    }
}

/* ---------------- font ---------------- */
/* Glyphs are 5x7, left-aligned in the top five bits; row 7 is blank so they sit
 * on a baseline. Written as shapes rather than as tile bytes because a tile needs
 * its two bit planes interleaved, and doing that in load_font() keeps the shapes
 * legible here. Ink is colour 3, black against the blank background. */
static const char FONT_ORDER[] = " ABCEGIKLMNOPRSTUVYZ0123456789";
/* Counted from the string above, not written twice: FONT_GLYPHS rows beyond
 * FONT_ORDER would leave the glyph blank and the char unmapped, silently. */
#define FONT_LEN (sizeof FONT_ORDER - 1)

static const uint8_t FONT_GLYPHS[FONT_LEN][8] = {
    { 0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00 },   /* space */
    { 0x70,0x88,0x88,0xF8,0x88,0x88,0x88,0x00 },   /* A */
    { 0xF0,0x88,0x88,0xF0,0x88,0x88,0xF0,0x00 },   /* B */
    { 0x70,0x88,0x80,0x80,0x80,0x88,0x70,0x00 },   /* C */
    { 0xF8,0x80,0x80,0xF0,0x80,0x80,0xF8,0x00 },   /* E */
    { 0x70,0x88,0x80,0xB8,0x88,0x88,0x70,0x00 },   /* G */
    { 0xF8,0x20,0x20,0x20,0x20,0x20,0xF8,0x00 },   /* I */
    { 0x88,0x90,0xA0,0xC0,0xA0,0x90,0x88,0x00 },   /* K */
    { 0x80,0x80,0x80,0x80,0x80,0x80,0xF8,0x00 },   /* L */
    { 0x88,0xD8,0xA8,0x88,0x88,0x88,0x88,0x00 },   /* M */
    { 0x88,0xC8,0xA8,0x98,0x88,0x88,0x88,0x00 },   /* N */
    { 0x70,0x88,0x88,0x88,0x88,0x88,0x70,0x00 },   /* O */
    { 0xF0,0x88,0x88,0xF0,0x80,0x80,0x80,0x00 },   /* P */
    { 0xF0,0x88,0x88,0xF0,0xA0,0x90,0x88,0x00 },   /* R */
    { 0x78,0x80,0x80,0x70,0x08,0x08,0xF0,0x00 },   /* S */
    { 0xF8,0x20,0x20,0x20,0x20,0x20,0x20,0x00 },   /* T */
    { 0x88,0x88,0x88,0x88,0x88,0x88,0x70,0x00 },   /* U */
    { 0x88,0x88,0x88,0x88,0x88,0x50,0x20,0x00 },   /* V */
    { 0x88,0x88,0x50,0x20,0x20,0x20,0x20,0x00 },   /* Y */
    { 0xF8,0x08,0x10,0x20,0x40,0x80,0xF8,0x00 },   /* Z */
    { 0x70,0x88,0x88,0x88,0x88,0x88,0x70,0x00 },   /* 0 */
    { 0x20,0x60,0x20,0x20,0x20,0x20,0x70,0x00 },   /* 1 */
    { 0x70,0x88,0x08,0x10,0x20,0x40,0xF8,0x00 },   /* 2 */
    { 0xF8,0x10,0x20,0x10,0x08,0x88,0x70,0x00 },   /* 3 */
    { 0x10,0x30,0x50,0x90,0xF8,0x10,0x10,0x00 },   /* 4 */
    { 0xF8,0x80,0xF0,0x08,0x08,0x88,0x70,0x00 },   /* 5 */
    { 0x30,0x40,0x80,0xF0,0x88,0x88,0x70,0x00 },   /* 6 */
    { 0xF8,0x08,0x10,0x20,0x20,0x20,0x20,0x00 },   /* 7 */
    { 0x70,0x88,0x88,0x70,0x88,0x88,0x70,0x00 },   /* 8 */
    { 0x70,0x88,0x88,0x78,0x08,0x10,0x60,0x00 },   /* 9 */
};

static void load_font(void)
{
    uint8_t g, y, t[16];
    for (g = 0; g < FONT_LEN; g++) {
        for (y = 0; y < 8; y++) {
            t[y * 2]     = FONT_GLYPHS[g][y];   /* low plane  */
            t[y * 2 + 1] = FONT_GLYPHS[g][y];   /* high plane = colour 3 */
        }
        set_bkg_data((uint8_t)(T_FONT + g), 1, t);
    }
}

static uint8_t font_tile(char ch)
{
    uint8_t i;
    for (i = 0; i < FONT_LEN; i++)
        if (FONT_ORDER[i] == ch) return (uint8_t)(T_FONT + i);
    return T_FONT;                              /* anything else -> space */
}

/* Writes BG tiles one at a time, the same call draw_cell() makes, so it is safe
 * mid-game as well as under DISPLAY_OFF.
 *
 * `row` is a SCREEN tile row, not a map row. A ceiling drop leaves SCY non-zero
 * for the rest of the level, and a raw map row would then land the text however
 * far the board has walked up the map -- which is why the title screen, where
 * SCY is 0, is the only place the two happened to be the same. SCY is a multiple
 * of 8 everywhere except the 16 frames of a slide, so SCY >> 3 is exactly the map
 * row under a screen row. (The 16-frame slide is the one case a row can be
 * neither: nothing draws text mid-slide.) */
static void draw_text(uint8_t col, uint8_t row, const char *s)
{
    uint8_t t, mrow = (uint8_t)((row + (SCY_REG >> 3)) & 31);
    while (*s) {
        t = font_tile(*s++);
        set_bkg_tiles(col++, mrow, 1, 1, &t);
    }
}

/* Same as draw_text(), but on the window layer. The score is the only thing
 * that lives there (see the SCORE_ROW comment below). */
static void draw_win_text(uint8_t col, uint8_t row, const char *s)
{
    uint8_t t;
    while (*s) {
        t = font_tile(*s++);
        set_win_tiles(col++, row, 1, 1, &t);
    }
}

/* The launcher strip is the only free space on screen: the grid owns the BG rows
 * and the walls the outer columns. It is the first two rows of the WINDOW layer,
 * which lands on screen rows 128-143 (see main() for why it is not on the BG).
 * The sprites there -- the "next" preview at x 32 and the launcher at x 80 --
 * leave cols 12-17 clear, so the score is right-aligned against the wall.
 * "SCORE" and the number are stacked rather than side by side: eleven tiles will
 * not fit in the six columns the sprites leave free, but two 8px text rows stack
 * into the 16px strip. */
#define SCORE_COL     12
#define SCORE_ROW     0              /* window row, i.e. screen row 16 */
#define SCORE_LABEL   "SCORE"
#define SCORE_DIGITS  5              /* 65535 fits; the score is uint16_t */

/* The level readout, in the other free run of the strip. A sprite at x covers the
 * screen from x-8 (OAM is offset by 8), so the next preview (x32) owns cols 3-4,
 * the launcher (x80) cols 9-10, the score cols 12-17 and the walls 0-1 and 18-19.
 * That leaves cols 5-8 -- four tiles, all of them, which is why the label is "LV"
 * and not "LEVEL", and why the field is flush against the launcher. Label over
 * number, the same shape as the score and on its own columns, so neither field can
 * reflow into the other. */
#define LEVEL_COL     5
#define LEVEL_ROW     0
#define LEVEL_LABEL   "LV"
#define LEVEL_NUM_COL 7              /* LEVEL_COL + the two tiles of "LV" */
#define LEVEL_DIGITS  2              /* see draw_level() for the ceiling this sets */

/* Zero-padded, so the field never reflows as it grows. */
static void draw_score(void)
{
    char s[SCORE_DIGITS + 1];
    uint16_t v = score;
    int8_t i;

    draw_win_text(SCORE_COL, SCORE_ROW, SCORE_LABEL);

    s[SCORE_DIGITS] = 0;
    for (i = SCORE_DIGITS - 1; i >= 0; i--) {     /* five divides, once per shot */
        s[i] = (char)('0' + (v % 10));
        v /= 10;
    }
    draw_win_text(SCORE_COL, SCORE_ROW + 1, s);
}

/* Shown as level + 1: `level` is 0-based, so the first board would otherwise read
 * "LV 00". Drawn once per board, from redraw_all(), which is the only place the
 * level can change -- it rises when a board is cleared, and the strip is the
 * window layer, which no ceiling drop touches, so nothing redraws it mid-level.
 *
 * ponytail: two digits, so "LV 99" is the last level that fits the four-tile run.
 * The level only rises on a cleared board, so reaching it means clearing 99 of
 * them; widen the field if that ever stops being true. The strip has no spare
 * column, so widening means moving the next preview -- see the LEVEL_COL note. */
static void draw_level(void)
{
    char s[LEVEL_DIGITS + 1];
    uint8_t v = (uint8_t)(level + 1);
    int8_t i;

    draw_win_text(LEVEL_COL, LEVEL_ROW, LEVEL_LABEL);

    s[LEVEL_DIGITS] = 0;
    for (i = LEVEL_DIGITS - 1; i >= 0; i--) {
        s[i] = (char)('0' + (v % 10));
        v /= 10;
    }
    draw_win_text(LEVEL_NUM_COL, LEVEL_ROW + 1, s);
}

/* ---------------- title screen ---------------- */
/* Built from the bubble tiles that are already loaded: no new art, no font, no
 * extra VRAM, and nothing that can drift out of sync with the bubble graphics.
 * An arch of bubbles above the launcher reads as a board built up ready to play.
 *
 * How many bubbles each row holds and the column it starts at, so the pile tapers
 * on BOTH sides instead of looking lopsided. The starts are what centre a row: an
 * unshifted row of n is centred at (8-n)/2, a shifted row, which sits half a
 * bubble further right, at (7-n)/2. */
static const uint8_t TITLE_COUNT[6] = { 8, 7, 6, 5, 4, 3 };
static const uint8_t TITLE_START[6] = { 0, 0, 1, 1, 2, 2 };

/* Both centred in the 16 tile playfield between the walls (cols 2..17). */
static const char TITLE_NAME[]   = "PUZZLE BALLOON";
static const char TITLE_PROMPT[] = "PRESS START";

/* Waits for START, and returns how many frames that took, for the RNG seed. */
static uint16_t title_screen(void)
{
    uint8_t r, c, n;
    uint16_t waited = 0;

    parity = 0;
    memset(board, 0, sizeof board);
    for (r = 0; r < 6; r++) {
        for (c = TITLE_START[r], n = (uint8_t)(TITLE_START[r] + TITLE_COUNT[r]);
             c < n && c < ROW_COLS(r); c++)
            board[r][c] = (uint8_t)(((r + c) & 3) + 1);   /* diagonal banding */
    }

    /* The text goes down inside the same display-off window as the board. The
     * strip below the grid is drawn by draw_board() too, and clearing it there is
     * what stops the title coming back with a just-finished game's score still
     * hanging over it. */
    DISPLAY_OFF;
    map_y0 = 0;             /* the title always sits at the top of the map */
    SCY_REG = 0;
    draw_board();
    draw_text(3, 12, TITLE_NAME);
    draw_text(4, 14, TITLE_PROMPT);
    DISPLAY_ON;

    /* The prompt is also the launcher bubble pulsing: a moving bubble says "this
     * is interactive" more directly than the text alone does. */
    for (;;) {
        place_bubble_sprite(SPR_FLY,  (uint8_t)((waited & 8) ? 1 : 2),
                            LAUNCH_X, LAUNCH_Y);
        place_bubble_sprite(SPR_NEXT, 3, NEXT_X, NEXT_Y);
        vsync();
        if (joypad() & (J_START | J_A)) break;
        waited++;
    }
    waitpadup();
    return waited;
}

/* Game over holds the finished board for about five seconds -- 300 frames at
 * roughly 60Hz -- before the title comes back. A or B (or START) cuts it short. */
#define GAME_OVER_FLASH   6              /* flashes, 16 frames each */
#define GAME_OVER_HOLD    204            /* the rest of the 5s: 6*16 + 204 = 300 */

/* The end-of-board messages, drawn on the BG over the board itself: the window
 * strip is two tile rows tall and the score and level already own most of it. The
 * board is 16 tile rows, so row 12 sits across its lower half, and the columns
 * centre a message in the 16-tile playfield (cols 2..17) the same way the title
 * centres its name. Rows here are SCREEN rows, which is what draw_text() takes. */
#define MSG_ROW       12
#define MSG_CLEAR     "STAGE CLEAR"
#define MSG_CLEAR_COL 4                  /* 2 + (16 - 11) / 2 */
#define MSG_OVER      "GAME OVER"
#define MSG_OVER_COL  5                  /* 2 + (16 -  9) / 2 */

/* Wait out the finished board, but let A or B skip it: whoever just lost knows it
 * and five seconds is a long time to stare at a board that will not change. START
 * too, since it is what carries on from the title anyway.
 *
 * The button that fired the losing shot may still be down, and a held button must
 * not read as a skip -- hence the release first. The release at the end is so a
 * held button does not fall straight through the title screen that follows. */
static void wait_or_skip(uint16_t frames)
{
    waitpadup();
    while (frames--) {
        vsync();
        if (joypad() & (J_A | J_B | J_START)) break;
    }
    waitpadup();
}

/* All of the tile data, BG and sprite. A function rather than inline in main()
 * because the SGB border transfer below overwrites VRAM to do its job, so the
 * tiles have to go up a second time afterwards. */
static void load_tiles(void)
{
    build_bubble_gfx();
    /* tile 0 must be blank: the second half of dot_gfx is all zeroes */
    set_bkg_data(T_BLANK, 1, dot_gfx + 16);
    set_bkg_data(T_WALL, 1, wall_gfx);
    set_bkg_data(T_BUBBLE, 16, gfx);
    set_sprite_data(0, 16, gfx);
    set_sprite_data(S_DOT, 2, dot_gfx);
    load_font();
}

/* Both tile maps, cleared. The SGB border upload writes VRAM and leaves the map
 * areas full of border tiles, so this runs again after it. */
static void blank_maps(void)
{
    fill_bkg_rect(0, 0, 20, 32, T_BLANK);
    fill_win_rect(0, 0, 20, 2, T_BLANK);
}

void main(void)
{
    uint8_t won, i;

    DISPLAY_OFF;
    SPRITES_8x16;
    BGP_REG  = 0xE4;
    OBP0_REG = 0xE4;

    load_tiles();

    /* The boot ROM drew its Nintendo logo into the BG map and left it there,
     * so clear the screen before turning the LCD back on: otherwise the wait
     * for START shows that logo (or uninitialised VRAM, on hardware that does
     * not zero it the way an emulator does) instead of a blank screen. */
    blank_maps();

    /* The launcher strip goes on the window layer, which is the one layer SCY
     * does not scroll. A ceiling drop slides the whole board down by scrolling
     * SCY, and the strip has to stay put while that happens -- on the BG it
     * would slide up into the playfield with everything else. Window rows 0-1
     * land on screen rows 128-143, exactly the strip. The window map is 0x9C00
     * and the playfield keeps the BG at 0x9800, so set_bkg_*() and set_win_*()
     * address two different maps and neither disturbs the other. */
    move_win(7, 128);                 /* WX 7 = flush left, WY 128 = the strip */
    LCDC_REG |= LCDCF_WIN9C00;
    SHOW_WIN;

    hide_all_sprites();
    SHOW_BKG;
    SHOW_SPRITES;
    DISPLAY_ON;

    /* The Super Game Boy border, once, at boot. The SGB reads the CHR_TRN/PCT_TRN
     * payloads off the rendered screen, so this has to come after DISPLAY_ON --
     * and it trashes VRAM getting there, hence load_tiles() again below. Four
     * frames first: a PAL SNES needs that delay at startup or no border shows.
     * sgb_check() is false on a DMG, so an ordinary Game Boy boots exactly as it
     * did before and pays only the four frames. */
    for (i = 0; i != 4; i++) vsync();
    if (sgb_check()) {
        set_sgb_border((unsigned char *)border_data_tiles, sizeof(border_data_tiles),
                       (unsigned char *)border_data_map, sizeof(border_data_map),
                       (unsigned char *)border_data_palettes, sizeof(border_data_palettes));
        load_tiles();
        blank_maps();
    }

    /* A game is titles -> boards until it is lost -> back to the title, forever.
     * Losing ends the game; clearing a board only advances the level. */
    for (;;) {
        /* How long the player took to press START seeds the RNG, the DMG having
         * no timer to sample. */
        initrand(title_screen());
        level = 0;
        score  = 0;

        for (;;) {
            init_board((uint8_t)(4 + (level > 2 ? 2 : level)));
            redraw_all();
            won = play();
            hide_all_sprites();

            if (!won) break;             /* game over */

            level++;
            /* Drawn before the flash so the message is what blinks. */
            draw_text(MSG_CLEAR_COL, MSG_ROW, MSG_CLEAR);
            flash(3);
            waitpad(J_START);            /* START carries on to the next board */
            waitpadup();
        }

        draw_text(MSG_OVER_COL, MSG_ROW, MSG_OVER);
        flash(GAME_OVER_FLASH);
        wait_or_skip(GAME_OVER_HOLD);

        /* No need to clear the message: title_screen() redraws the whole board
         * inside DISPLAY_OFF, and it resets SCY and map_y0 with it. */
    }
}
