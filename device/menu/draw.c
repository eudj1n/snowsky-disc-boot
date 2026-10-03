/* disc-menu's drawing (draw.h). Integer arithmetic in the frame loop: the player's toolchain is
   soft-float, so the ring's geometry is computed once and each frame only compares and blends. */
#include "draw.h"
#include "font.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define RING_OUTER 175
#define RING_INNER 169

static uint32_t blend(uint32_t under, uint32_t over, unsigned alpha) {
    if (alpha >= 255) return over;
    if (!alpha) return under;
    uint32_t r = ((under >> 16 & 255) * (255 - alpha) + (over >> 16 & 255) * alpha) / 255;
    uint32_t g = ((under >> 8 & 255) * (255 - alpha) + (over >> 8 & 255) * alpha) / 255;
    uint32_t b = ((under & 255) * (255 - alpha) + (over & 255) * alpha) / 255;
    return r << 16 | g << 8 | b;
}

static void put(canvas *c, int x, int y, uint32_t color, unsigned alpha) {
    if (x < 0 || y < 0 || x >= PANEL || y >= PANEL || !alpha) return;
    uint32_t *p = &c->px[y * PANEL + x];
    *p = blend(*p, color, alpha);
}

void draw_fill(canvas *c, uint32_t color) {
    for (int i = 0; i < PANEL * PANEL; i++) c->px[i] = color;
}

/* Coverage of a pixel by a disc, from the distance of its centre to the edge in 1/16 pixel:
   inside by half a pixel or more is full, outside by half a pixel or more is none. */
static unsigned coverage(int inside16) {
    if (inside16 >= 8) return 255;
    if (inside16 <= -8) return 0;
    return (unsigned)((inside16 + 8) * 255 / 16);
}

/* The integer square root, without floating point (the frame loop's own arithmetic). */
static int isqrt(long v) {
    if (v <= 0) return 0;
    unsigned long x = (unsigned long)v, r = 0, bit = 1UL << (sizeof(long) * 8 - 2);
    while (bit > x) bit >>= 2;
    while (bit) {
        if (x >= r + bit) { x -= r + bit; r = (r >> 1) + bit; }
        else r >>= 1;
        bit >>= 2;
    }
    return (int)r;
}

void draw_round_rect(canvas *c, int left, int top, int right, int bottom, int radius, uint32_t color) {
    for (int y = top - 1; y <= bottom; y++)
        for (int x = left - 1; x <= right; x++) {
            /* The nearest point of the inner rectangle the corners are rounded around. */
            int qx = x < left + radius ? left + radius : x >= right - radius ? right - radius - 1 : x;
            int qy = y < top + radius ? top + radius : y >= bottom - radius ? bottom - radius - 1 : y;
            long dx = (long)(x - qx) * 16 + (x < qx ? 8 : x > qx ? -8 : 0), dy = (long)(y - qy) * 16 + (y < qy ? 8 : y > qy ? -8 : 0);
            int inside = radius * 16 - isqrt(dx * dx + dy * dy);
            put(c, x, y, color, coverage(inside));
        }
}

void draw_disc(canvas *c, int cx, int cy, int r, uint32_t color) {
    for (int y = cy - r - 1; y <= cy + r + 1; y++)
        for (int x = cx - r - 1; x <= cx + r + 1; x++) {
            long dx = (long)(x - cx) * 16, dy = (long)(y - cy) * 16;
            put(c, x, y, color, coverage(r * 16 - isqrt(dx * dx + dy * dy)));
        }
}

/* The ring's pixels: where, how much of each the ring covers, and its angle from the top. */
typedef struct { int index; unsigned char alpha; unsigned short angle; } ring_px;
static ring_px *ring;
static int ring_count;

void ring_prepare(void) {
    if (ring) return;
    int cap = 0;
    for (int pass = 0; pass < 2; pass++) {
        int n = 0;
        for (int y = 0; y < PANEL; y++)
            for (int x = 0; x < PANEL; x++) {
                double dx = x + 0.5 - PANEL / 2.0, dy = y + 0.5 - PANEL / 2.0, d = sqrt(dx * dx + dy * dy);
                int outer = (int)((RING_OUTER - d) * 16), inner = (int)((d - RING_INNER) * 16);
                unsigned a = coverage(outer < inner ? outer : inner);
                if (!a) continue;
                if (pass) {
                    double turn = atan2(dx, -dy);                       /* from the top, clockwise */
                    if (turn < 0) turn += 2 * M_PI;
                    ring[n].index = y * PANEL + x;
                    ring[n].alpha = (unsigned char)a;
                    ring[n].angle = (unsigned short)(turn / (2 * M_PI) * 65535.0);
                }
                n++;
            }
        if (!pass) { cap = n; ring = calloc((size_t)cap, sizeof(*ring)); if (!ring) return; }
        else ring_count = n;
    }
}

void draw_ring(canvas *c, uint32_t track, uint32_t fill, unsigned left65536) {
    for (int k = 0; k < ring_count; k++) {
        uint32_t *p = &c->px[ring[k].index];
        *p = blend(*p, ring[k].angle < left65536 ? fill : track, ring[k].alpha);
    }
}

/* The next code point of UTF-8 text (the font's signs are two and three bytes long). */
static unsigned next_code(const char **s) {
    const unsigned char *p = (const unsigned char *)*s;
    unsigned code = *p++;
    if (code >= 0xe0 && p[0] && p[1]) { code = (code & 15) << 12 | (p[0] & 63) << 6 | (p[1] & 63); p += 2; }
    else if (code >= 0xc0 && p[0]) { code = (code & 31) << 6 | (p[0] & 63); p += 1; }
    *s = (const char *)p;
    return code;
}

static const menu_glyph *find(const menu_face *f, unsigned code) {
    for (int k = 0; k < f->count; k++) if (f->glyphs[k].code == code) return &f->glyphs[k];
    return code == '?' ? NULL : find(f, '?');
}

int text_width(int face, const char *s) {
    const menu_face *f = &menu_faces[face];
    long width64 = 0;
    while (*s) { const menu_glyph *g = find(f, next_code(&s)); if (g) width64 += g->advance64; }
    return (int)((width64 + 32) / 64);
}

void draw_text(canvas *c, int face, int x, int y, int align, const char *s, uint32_t color) {
    const menu_face *f = &menu_faces[face];
    int width = text_width(face, s);
    long pen64 = (long)(align == ALIGN_LEFT ? x : align == ALIGN_MIDDLE ? x - width / 2 : x - width) * 64;
    int baseline = y + (f->ascent - f->descent) / 2;
    while (*s) {
        const menu_glyph *g = find(f, next_code(&s));
        if (!g) continue;
        int gx = (int)((pen64 + 32) / 64) + g->x, gy = baseline + g->y;
        for (int row = 0; row < g->h; row++)
            for (int col = 0; col < g->w; col++)
                put(c, gx + col, gy + row, color, menu_alpha[g->offset + (unsigned)(row * g->w + col)]);
        pen64 += g->advance64;
    }
}
