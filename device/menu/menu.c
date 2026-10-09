/* disc-menu: the boot menu (contract, "Several UIs and the boot menu"; plan, stage 3b).
   Runs in the UI's place at power-on: shows the UIs boot offers, counts 5 s down to the default,
   takes Volume +/- and Play (or a touch), answers in $DISC_BOOT_RUN/choice and hands over to the
   UI launcher in its own process. Everything it opens closes on that exec. While boot installs from
   the card (a start with Play) it shows the installation and asks nothing; the power key held
   answers "poweroff". */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE
#include "boot_util.h"
#include "draw.h"
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <linux/fb.h>
#include <linux/input.h>
#endif

#define MAX_ENTRIES 17
#define ROWS 3
#define COUNTDOWN_MS 5000
/* The list layout the owner chose (2026-10-03), in the player page's dark colours. */
#define GROUND 0x181614
#define INK 0xece8e3
#define MUTED 0xa59e96
#define LINE 0x33302c
#define SELECTED 0x3a3530
#define ACCENT 0xff795a
/* The player's key events on event0 (read on a player, 2026-10-03): a click of Volume +, of
   Volume - and of Play; Volume's double clicks and holds arrive as their own codes. */
#define CODE_VOLUME_UP 0xfb
#define CODE_VOLUME_DOWN 0xfc
#define CODE_PLAY 0xfa
/* Stock's key driver reports Play's double press and hold as codes of their own. */
#define CODE_PLAY_HOLD 0x10c
#define CODE_PLAY_DOUBLE 0x10d
/* After a start with Play: Play's gestures this soon after the menu could answer are the release of
   the recovery's own Play, which the driver reports when the key is let go (2026-10-07). */
#define RECOVERY_RELEASE_MS 2000
/* The power key held: stock's UI switches the player off on it (snowsky-disc-qemu's reading of
   stock; each key's code goes to the menu's output, so the player can confirm it). */
#define CODE_POWER_HOLD 0x108
#define EV_KEY_TYPE 1
#define EV_ABS_TYPE 3
#define BTN_TOUCH_CODE 0x14a

/* A key or touch event as the player's 32-bit kernel writes it: 16 bytes. */
typedef struct { int32_t sec, usec; uint16_t type, code; int32_t value; } event32;
typedef struct { char ui[33], title[48], version[65]; } entry;

static entry entries[MAX_ENTRIES];
static int count, selected, chosen = -1, counting = 1, held[8], nheld;
static long deadline, countdown_ms = COUNTDOWN_MS;
/* boot's installation from the card ($DISC_BOOT_STATUS/install.json), and the power key's answer. */
static int installing, inst_done, inst_total, off;
static int recovery_start;            /* boot.json: this start had Play held at power-on */
static long answerable_at = -1;       /* when the menu first could take an answer (ms) */
static char inst_state[16], inst_current[48];
static canvas frame;

static long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static const char *env(const char *name, const char *fallback) {
    const char *v = getenv(name);
    return v && v[0] ? v : fallback;
}

/* What boot offers (ui/choices.json): stock's UI first, shown as FiiO, then each installed ui package. */
static int load_choices(const char *status) {
    char p[PATH_MAX], buf[SMALL_FILE], deflt[33] = "";
    size_t len;
    if (snprintf(p, sizeof(p), "%s/ui/choices.json", status) >= (int)sizeof(p) || read_small(p, buf, sizeof(buf), &len)) return -1;
    bjson j;
    if (bjson_parse(&j, buf, len, 256)) return -1;
    int list = bjson_find(&j, 0, "entries");
    bjson_string(&j, bjson_find(&j, 0, "default"), deflt, sizeof(deflt));
    for (int k = 0; list >= 0 && k < bjson_size(&j, list) && count < MAX_ENTRIES; k++) {
        int item = bjson_item(&j, list, k);
        entry *e = &entries[count];
        memset(e, 0, sizeof(*e));
        if (bjson_string(&j, bjson_find(&j, item, "ui"), e->ui, sizeof(e->ui))) continue;
        bjson_string(&j, bjson_find(&j, item, "version"), e->version, sizeof(e->version));
        if (!strcmp(e->ui, "stock")) snprintf(e->title, sizeof(e->title), "FiiO");
        else if (bjson_string(&j, bjson_find(&j, item, "title"), e->title, sizeof(e->title))) memcpy(e->title, e->ui, strlen(e->ui) + 1);
        if (!strcmp(e->ui, deflt)) selected = count;
        count++;
    }
    bjson_free(&j);
    return count ? 0 : -1;
}

/* Whether boot is still installing from the card: the progress while it is, 0 once it is done. */
static int read_install(const char *status) {
    char p[PATH_MAX], buf[400];
    size_t len;
    installing = 0;
    if (snprintf(p, sizeof(p), "%s/install.json", status) >= (int)sizeof(p) || read_small(p, buf, sizeof(buf), &len)) return 0;
    bjson j;
    if (bjson_parse(&j, buf, len, 16)) return 0;
    long long v;
    inst_state[0] = inst_current[0] = 0;
    bjson_string(&j, bjson_find(&j, 0, "state"), inst_state, sizeof(inst_state));
    bjson_string(&j, bjson_find(&j, 0, "current"), inst_current, sizeof(inst_current));
    inst_done = !bjson_int(&j, bjson_find(&j, 0, "done"), &v) ? (int)v : 0;
    inst_total = !bjson_int(&j, bjson_find(&j, 0, "total"), &v) ? (int)v : 0;
    bjson_free(&j);
    installing = inst_state[0] && strcmp(inst_state, "done") != 0;
    return installing;
}

static void render_install(void) {
    char line[96];
    draw_fill(&frame, GROUND);
    draw_ring(&frame, LINE, ACCENT, inst_total > 0 ? (unsigned)(inst_done * 65536 / inst_total) : 0);
    draw_text(&frame, FACE_LABEL, 180, 74, ALIGN_MIDDLE, "INSTALLING", MUTED);
    if (!strcmp(inst_state, "waiting")) {
        draw_text(&frame, FACE_TITLE, 180, 172, ALIGN_MIDDLE, "Reading the card\xe2\x80\xa6", INK);
        return;
    }
    draw_text(&frame, FACE_TITLE_BOLD, 180, 156, ALIGN_MIDDLE, inst_current[0] ? inst_current : "", INK);
    snprintf(line, sizeof(line), "%d of %d", inst_done < inst_total ? inst_done + 1 : inst_total, inst_total);
    draw_text(&frame, FACE_NOTE, 180, 196, ALIGN_MIDDLE, line, MUTED);
    draw_text(&frame, FACE_SMALL, 180, 306, ALIGN_MIDDLE, "from the card", MUTED);
}

/* The screen: the ring's countdown, the list (three rows at a time around the selection) and
   what the keys do, or what starts. */
static int first_row(void) {
    int top = selected - 1;
    if (top > count - ROWS) top = count - ROWS;
    return top < 0 ? 0 : top;
}

static void render(long now) {
    char line[96];
    if (installing) { render_install(); return; }
    draw_fill(&frame, GROUND);
    if (off) {
        draw_text(&frame, FACE_TITLE, 180, 172, ALIGN_MIDDLE, "Switching off\xe2\x80\xa6", INK);
        return;
    }
    long left = counting && chosen < 0 ? deadline - now : 0;
    draw_ring(&frame, LINE, ACCENT, left > 0 && countdown_ms > 0 ? (unsigned)(left * 65536 / countdown_ms) : 0);
    draw_text(&frame, FACE_LABEL, 180, 74, ALIGN_MIDDLE, "START WITH", MUTED);
    int top = first_row();
    for (int k = top; k < count && k < top + ROWS; k++) {
        int y = 132 + (k - top) * 48, on = k == (chosen >= 0 ? chosen : selected);
        if (on) {
            draw_round_rect(&frame, 58, y - 21, 302, y + 21, 21, SELECTED);
            draw_disc(&frame, 80, y, 4, ACCENT);
        }
        draw_text(&frame, on ? FACE_TITLE_BOLD : FACE_TITLE, 94, y, ALIGN_LEFT, entries[k].title, on ? INK : MUTED);
        draw_text(&frame, FACE_SMALL, 288, y, ALIGN_RIGHT, entries[k].version, MUTED);
    }
    if (chosen >= 0) {
        snprintf(line, sizeof(line), "Starting %s\xe2\x80\xa6", entries[chosen].title);
        draw_text(&frame, FACE_NOTE, 180, 296, ALIGN_MIDDLE, line, INK);
        return;
    }
    if (left > 0) {
        snprintf(line, sizeof(line), "Starts in %ld s", (left + 999) / 1000);
        draw_text(&frame, FACE_NOTE, 180, 286, ALIGN_MIDDLE, line, MUTED);
    }
    draw_text(&frame, FACE_SMALL, 180, 306, ALIGN_MIDDLE, "Vol \xc2\xb1 choose  \xc2\xb7  \xe2\x96\xb6 start", MUTED);
}

/* The panel: the framebuffer, drawn into the page not shown and then panned to (no tearing,
   and the emulator's evidence of a frame). The panel is mounted 180 degrees: a canvas pixel
   (x, y) is the framebuffer's (359 - x, 359 - y). */
static uint8_t *fb;
static int fb_fd = -1, page, pages = 1, line_length = PANEL * 4;

static void fb_open(const char *path) {
    fb_fd = open(path, O_RDWR | O_CLOEXEC);
    if (fb_fd < 0) return;
    size_t size = (size_t)PANEL * PANEL * 4 * 3;
#ifdef __linux__
    struct fb_var_screeninfo var;
    struct fb_fix_screeninfo fix;
    if (!ioctl(fb_fd, FBIOGET_VSCREENINFO, &var) && !ioctl(fb_fd, FBIOGET_FSCREENINFO, &fix) && var.xres == PANEL && var.yres == PANEL
        && var.bits_per_pixel == 32) {
        line_length = (int)fix.line_length;
        pages = (int)(var.yres_virtual / var.yres);
        size = fix.smem_len;
    }
#endif
    struct stat s;
    if (!fstat(fb_fd, &s) && S_ISREG(s.st_mode) && (size_t)s.st_size < size) size = (size_t)s.st_size;
    void *map = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fb_fd, 0);
    fb = map == MAP_FAILED ? NULL : map;
}

static void show(void) {
    if (!fb) return;
    int target = pages > 1 ? (page + 1) % 2 : 0;
    uint8_t *base = fb + (size_t)target * PANEL * (size_t)line_length;
    for (int y = 0; y < PANEL; y++) {
        uint32_t *row = (uint32_t *)(base + (size_t)(PANEL - 1 - y) * (size_t)line_length);
        for (int x = 0; x < PANEL; x++) row[PANEL - 1 - x] = frame.px[y * PANEL + x];
    }
#ifdef __linux__
    if (pages > 1) {
        struct fb_var_screeninfo var;
        if (!ioctl(fb_fd, FBIOGET_VSCREENINFO, &var)) {
            var.yoffset = (unsigned)(target * PANEL);
            var.activate = FB_ACTIVATE_NOW;
            if (!ioctl(fb_fd, FBIOPAN_DISPLAY, &var)) page = target;
        }
    }
#endif
}

/* Input: the keys on event0 (taken for the menu alone while stock's player runs beside it,
   that is when a player already ran in this boot), the touch panel on event1. */
static int keys_fd = -1, touch_fd = -1, touch_x, touch_y, touching;

static int is_held(int code) {
    for (int k = 0; k < nheld; k++) if (held[k] == code) return 1;
    return 0;
}

static void let_go(int code) {
    for (int k = 0; k < nheld; k++) if (held[k] == code) held[k] = held[--nheld];
}

static void input_open(const char *keys, const char *touch, const char *status) {
    char p[PATH_MAX];
    keys_fd = open(keys, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    touch_fd = open(touch, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    snprintf(p, sizeof(p), "%s/player-ran", status);
#ifdef __linux__
    struct stat s;
    if (keys_fd >= 0 && !stat(p, &s)) ioctl(keys_fd, EVIOCGRAB, (void *)1);
    /* A key already down (Play at power-on was the recovery) counts only after its release. */
    unsigned char state[KEY_MAX / 8 + 1];
    memset(state, 0, sizeof(state));
    if (keys_fd >= 0 && ioctl(keys_fd, EVIOCGKEY(sizeof(state)), state) >= 0)
        for (int code = 0; code <= KEY_MAX && nheld < 8; code++) if (state[code / 8] & (1 << (code % 8))) held[nheld++] = code;
#else
    (void)p;
#endif
#ifdef DISC_MENU_FIXTURE
    for (const char *h = getenv("DISC_MENU_HELD"); h && *h && nheld < 8; ) {
        held[nheld++] = (int)strtol(h, (char **)&h, 0);
        while (*h == ',') h++;
    }
#endif
}

/* boot.json says this start had Play held at power-on (its reason "recovery"). */
static int started_with_play(const char *status) {
    char p[PATH_MAX], buf[1024];
    if (snprintf(p, sizeof(p), "%s/boot.json", status) >= (int)sizeof(p)) return 0;
    FILE *f = fopen(p, "r");
    if (!f) return 0;
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = 0;
    return strstr(buf, "\"reason\":\"recovery\"") != NULL;
}

/* The row a touch at canvas (x, y) lands on, or -1. */
static int row_at(int x, int y) {
    int top = first_row();
    for (int k = top; k < count && k < top + ROWS; k++) {
        int ry = 132 + (k - top) * 48;
        if (x >= 58 && x < 302 && y >= ry - 24 && y < ry + 24) return k;
    }
    return -1;
}

static int is_play(int code) { return code == CODE_PLAY || code == CODE_PLAY_HOLD || code == CODE_PLAY_DOUBLE; }

static void key(int code, int value) {
    fprintf(stderr, "disc-menu: key 0x%x %d\n", code, value);
    if (value == 0) { let_go(code); return; }
    if (value != 1 || is_held(code) || installing) return;
    if (recovery_start && is_play(code) && (answerable_at < 0 || now_ms() - answerable_at < RECOVERY_RELEASE_MS)) {
        fprintf(stderr, "disc-menu: Play's release after the recovery, not an answer\n");
        return;
    }
    if (code == CODE_POWER_HOLD) { off = 1; return; }
    counting = 0;
    if (code == CODE_VOLUME_UP && selected > 0) selected--;
    else if (code == CODE_VOLUME_DOWN && selected < count - 1) selected++;
    else if (code == CODE_PLAY) chosen = selected;
}

static void read_events(void) {
    event32 e;
    while (keys_fd >= 0 && read(keys_fd, &e, sizeof(e)) == (ssize_t)sizeof(e))
        if (e.type == EV_KEY_TYPE) key(e.code, e.value);
    while (touch_fd >= 0 && read(touch_fd, &e, sizeof(e)) == (ssize_t)sizeof(e)) {
        if (e.type == EV_ABS_TYPE && (e.code == 0x35 || e.code == 0x00)) touch_x = e.value;
        else if (e.type == EV_ABS_TYPE && (e.code == 0x36 || e.code == 0x01)) touch_y = e.value;
        else if (e.type == EV_KEY_TYPE && e.code == BTN_TOUCH_CODE) {
            if (e.value) { touching = 1; counting = 0; }
            else if (touching) {
                /* The panel's coordinates are the canvas turned 180 degrees. */
                int row = row_at(PANEL - 1 - touch_x, PANEL - 1 - touch_y);
                touching = 0;
                if (row >= 0 && !installing) { selected = row; chosen = row; }
            }
        }
    }
}

/* The answer, then the hand-over to the UI launcher in this process. */
static void answer(const char *run, const char *launcher, const char *ui) {
    char p[PATH_MAX], buf[64];
    int n = snprintf(buf, sizeof(buf), "{\"ui\":\"%s\"}\n", ui);
    if (snprintf(p, sizeof(p), "%s/choice", run) < (int)sizeof(p)) write_atomic(p, buf, (size_t)n, 0644);
    if (launcher) {
        char *argv[] = {"mq_ui", NULL};
        execv(launcher, argv);
    }
    exit(0);   /* without a launcher: stock's loop restarts the pair, and the answer stands */
}

int main(void) {
    const char *status = env("DISC_BOOT_STATUS", "/run/disc-boot"), *run = env("DISC_BOOT_RUN", "/run/disc-boot/menu");
    const char *launcher = getenv("DISC_BOOT_LAUNCHER");
    const char *fb_path = "/dev/fb0", *keys = "/dev/input/event0", *touch = "/dev/input/event1";
#ifdef DISC_MENU_FIXTURE
    fb_path = env("DISC_MENU_FB", fb_path); keys = env("DISC_MENU_KEYS", keys); touch = env("DISC_MENU_TOUCH", touch);
    countdown_ms = atol(env("DISC_MENU_COUNTDOWN_MS", "5000"));
#endif
    if (load_choices(status)) { fprintf(stderr, "disc-menu: nothing to offer\n"); return 1; }
    ring_prepare();
    fb_open(fb_path);
    input_open(keys, touch, status);
    recovery_start = started_with_play(status);
    setvbuf(stderr, NULL, _IONBF, 0);
    deadline = now_ms() + countdown_ms;
    long shown = -1, polled = 0;
    int last_selected = -1, last_counting = -1, was_installing = read_install(status), last_done = -1;
    for (;;) {
        read_events();
        long now = now_ms();
        if (now - polled >= 200) {
            polled = now;
            int was = installing;
            read_install(status);
            if (was && !installing) {
                /* Installed: what boot offers now, and the countdown from the start. */
                count = 0; selected = 0; chosen = -1; counting = 1;
                load_choices(status);
                deadline = now + countdown_ms;
                last_selected = -1;
            }
        }
        if (installing) {
            deadline = now + countdown_ms;
            if (!was_installing || inst_done != last_done || now - shown >= 500) {
                render(now); show(); shown = now; last_done = inst_done;
            }
            was_installing = 1;
            pause_s(0.05);
            continue;
        }
        was_installing = 0;
        if (answerable_at < 0) answerable_at = now;
        if (off) { render(now); show(); answer(run, launcher, "poweroff"); }
        if (counting && now >= deadline) { chosen = selected; counting = 0; }
        /* A frame on every change, and ten a second while the ring runs down. */
        if (chosen >= 0 || selected != last_selected || counting != last_counting || (counting && now - shown >= 100)) {
            render(now);
            show();
            shown = now; last_selected = selected; last_counting = counting;
        }
        if (chosen >= 0) answer(run, launcher, entries[chosen].ui);
        pause_s(0.02);
    }
}
