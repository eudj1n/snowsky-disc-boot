/* disc-menu's drawing on the round 360x360 panel: a canvas in XRGB8888 and the few shapes the
   list layout needs (contract, "Several UIs and the boot menu"; plan, stage 3b). */
#ifndef DISC_MENU_DRAW_H
#define DISC_MENU_DRAW_H
#include <stdint.h>

#define PANEL 360

typedef struct { uint32_t px[PANEL * PANEL]; } canvas;
/* A face of the generated font (font.h): 0 label, 1 title, 2 title_bold, 3 small, 4 note. */
enum { FACE_LABEL, FACE_TITLE, FACE_TITLE_BOLD, FACE_SMALL, FACE_NOTE };
/* Horizontal anchors of a line of text: by its start, its middle or its end; vertically the
   line is placed by the middle between its face's ascent and descent. */
enum { ALIGN_LEFT, ALIGN_MIDDLE, ALIGN_RIGHT };

void draw_fill(canvas *c, uint32_t color);
/* A rounded rectangle, anti-aliased: left, top, right, bottom and radius in pixels. */
void draw_round_rect(canvas *c, int left, int top, int right, int bottom, int radius, uint32_t color);
void draw_disc(canvas *c, int cx, int cy, int r, uint32_t color);
/* The ring along the panel's edge: its track, and the part left from the top, clockwise,
   as a share of 65536. ring_prepare must run once first. */
void ring_prepare(void);
void draw_ring(canvas *c, uint32_t track, uint32_t fill, unsigned left65536);
/* A line of printable ASCII and the font's signs (UTF-8); returns its width in pixels. */
int text_width(int face, const char *s);
void draw_text(canvas *c, int face, int x, int y, int align, const char *s, uint32_t color);
#endif
