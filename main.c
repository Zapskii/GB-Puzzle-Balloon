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
 *   y 128..143 launcher strip
 *
 * Simplification: row pitch is 16px (2 tiles) so the grid stays tile-aligned.
 * Odd rows are shifted right by 8px (1 tile) and hold 7 bubbles, so the
 * neighbour logic is genuinely hexagonal, just a bit "looser" looking than
 * the arcade's 14px pitch. Moving to 14px pitch means drawing bubbles as
 * sprites or using a non-tile-aligned scheme; do that later if you care.
 */

#include <gb/gb.h>
#include <rand.h>
#include <stdint.h>
#include <string.h>

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

/* Sprite tile ids (8x16 mode: bubble colour c at c*4) */
#define S_DOT        16

/* Sprite slots (each bubble = 2 sprites in 8x16 mode) */
#define SPR_FLY      0
#define SPR_NEXT     2
#define SPR_DOT      4
#define NUM_DOTS     3

/* ---------------- fixed-point aim table ----------------
 * 12.4 fixed point (16 units = 1px), speed = 4px/frame = 64 units.
 * 16 angles from 15 deg (right) to 165 deg (left) in 10 deg steps.
 * Index increases towards the left.                                  */
#define NUM_ANGLES 16
static const int8_t ANG_DX[NUM_ANGLES] = {
     62,  58,  52,  45,  37,  27,  17,   6,
     -6, -17, -27, -37, -45, -52, -58, -62
};
static const int8_t ANG_DY[NUM_ANGLES] = {
    -17, -27, -37, -45, -52, -58, -62, -64,
    -64, -62, -58, -52, -45, -37, -27, -17
};

/* ---------------- state ---------------- */
static uint8_t  board[GRID_ROWS][GRID_COLS];   /* 0 = empty, else colour+1 */
static uint8_t  parity;        /* toggles on every ceiling drop            */
static uint8_t  level;
static uint16_t score;         /* not displayed yet */

/* A row's horizontal shift. Using a parity flag means a ceiling drop just
 * moves the rows down and toggles it: every existing row keeps its shift. */
#define SHIFTED(r)   ((uint8_t)(((r) ^ parity) & 1))
#define ROW_COLS(r)  ((uint8_t)(GRID_COLS - SHIFTED(r)))

/* scratch for flood fills */
static uint8_t visited[GRID_ROWS][GRID_COLS];
static uint8_t stack[GRID_ROWS * GRID_COLS];
static uint8_t cluster[GRID_ROWS * GRID_COLS];
static uint8_t nb_r[6], nb_c[6];

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
                  (uint8_t)(r << 1), 2, 2, t);
}

static void draw_board(void)
{
    uint8_t r, c, n;
    fill_bkg_rect(0, 0, 2, 18, T_WALL);
    fill_bkg_rect(18, 0, 2, 18, T_WALL);
    fill_bkg_rect(2, 0, 16, 18, T_BLANK);
    for (r = 0; r < GRID_ROWS; r++) {
        n = ROW_COLS(r);
        for (c = 0; c < n; c++)
            if (board[r][c]) draw_cell(r, c);
    }
}

static void redraw_all(void)
{
    DISPLAY_OFF;
    draw_board();
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

    for (r = 0; r < GRID_ROWS; r++) {
        n = ROW_COLS(r);
        for (c = 0; c < n; c++) {
            if (board[r][c] && !visited[r][c]) {
                board[r][c] = 0;
                draw_cell(r, c);
                removed++;
                vsync();                 /* one per frame = cheap "fall" effect */
            }
        }
    }
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

static void ceiling_drop(void)
{
    uint8_t r, c, n;
    for (r = GRID_ROWS - 1; r > 0; r--)
        memcpy(board[r], board[r - 1], GRID_COLS);
    parity ^= 1;
    memset(board[0], 0, GRID_COLS);
    n = ROW_COLS(0);
    for (c = 0; c < n; c++)
        board[0][c] = (uint8_t)((rand() & 3) + 1);
    redraw_all();
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
    uint8_t cur, next, ang = 7, rep = 0, keys, prev = 0xFF, shots = 0;
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

void main(void)
{
    uint16_t seed = 0;
    uint8_t won;

    DISPLAY_OFF;
    SPRITES_8x16;
    BGP_REG  = 0xE4;
    OBP0_REG = 0xE4;

    build_bubble_gfx();
    /* tile 0 must be blank: the second half of dot_gfx is all zeroes */
    set_bkg_data(T_BLANK, 1, dot_gfx + 16);
    set_bkg_data(T_WALL, 1, wall_gfx);
    set_bkg_data(T_BUBBLE, 16, gfx);
    set_sprite_data(0, 16, gfx);
    set_sprite_data(S_DOT, 2, dot_gfx);

    /* The boot ROM drew its Nintendo logo into the BG map and left it there,
     * so clear the screen before turning the LCD back on: otherwise the wait
     * for START shows that logo (or uninitialised VRAM, on hardware that does
     * not zero it the way an emulator does) instead of a blank screen. */
    fill_bkg_rect(0, 0, 20, 18, T_BLANK);

    hide_all_sprites();
    /* Show the launcher and the "next" bubble while waiting, so an empty board
     * reads as "ready" rather than as a dead screen. Placeholder until there is
     * a font and a real title screen. */
    place_bubble_sprite(SPR_FLY,  1, LAUNCH_X, LAUNCH_Y);
    place_bubble_sprite(SPR_NEXT, 2, NEXT_X,   NEXT_Y);
    SHOW_BKG;
    SHOW_SPRITES;
    DISPLAY_ON;

    /* wait for START; time spent waiting seeds the RNG */
    while (!(joypad() & (J_START | J_A))) { seed++; vsync(); }
    waitpadup();
    initrand(seed);

    level = 0;
    for (;;) {
        init_board((uint8_t)(4 + (level > 2 ? 2 : level)));
        redraw_all();
        won = play();
        hide_all_sprites();

        if (won) { level++; flash(3); }
        else     { level = 0; score = 0; flash(6); }

        /* TODO: show "STAGE CLEAR" / "GAME OVER" (needs a font tileset) */
        waitpad(J_START);
        waitpadup();
    }
}
