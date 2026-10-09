/* disc-menu: the boot menu (contract, "Several UIs and the boot menu" and "The menu's screens";
   plan, stages 3b and 7). Runs in the UI's place at power-on, on three screens: the UIs boot offers,
   counting 5 s down to the default (it stands while packages wait on the card); the services and
   their autostart; the packages, those staged on the card to install and the ui and service
   packages to remove, a removal of everything of ours last. It takes Volume +/- and Play (or a
   touch), changes what boot keeps through the boot program's commands ($DISC_BOOT_PROGRAM), answers
   in $DISC_BOOT_RUN/choice and hands over to the UI launcher in its own process. Everything it opens
   closes on that exec. While boot installs (a start with Play, or an installation it asked for) it
   shows the installation and asks nothing; the power key held answers "poweroff". */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE
#include "boot_util.h"
#include "draw.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/wait.h>
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
/* A removal's question: the selection's row in a warmer, darker red. */
#define WARN 0x4a2a22
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
/* A service (autostart), a package to remove (role ui or service) or one staged on the card. */
typedef struct { char name[33], title[48], version[65], role[12], folder[48], refused[160]; int autostart, removing; } item;
#define MAX_ITEMS 34
#define MAX_ROWS 64
enum { SCREEN_START, SCREEN_SERVICES, SCREEN_PACKAGES, SCREEN_GONE };
/* A row of the screen: what Play (or a touch) on it does, and what it shows. */
enum { ROW_UI, ROW_WAITING, ROW_SERVICES, ROW_PACKAGES, ROW_BACK, ROW_SERVICE, ROW_STAGED, ROW_INSTALLED, ROW_EVERYTHING };
typedef struct { int kind, index; char label[64], value[48]; int hot, warn; } row;

static entry entries[MAX_ENTRIES];
static item services[MAX_ITEMS], packages[MAX_ITEMS], staged[MAX_ITEMS];
static int count, nservices, npackages, nstaged, waiting;
static row rows[MAX_ROWS];
static int nrows, screen = SCREEN_START, selected, chosen = -1, counting = 1, held[8], nheld;
/* A removal to confirm: its row's kind and index, and when the question goes away; everything of ours
   takes two. */
static int confirm_kind = -1, confirm_index, confirm_step;
static long confirm_until;
static long deadline, countdown_ms = COUNTDOWN_MS;
#define CONFIRM_MS 5000
/* boot's installation ($DISC_BOOT_STATUS/install.json), the power key's answer, and the boot program
   the menu asks (an installation runs while the menu shows it). */
static int installing, inst_done, inst_total, off;
static pid_t installer = -1;
static const char *program;
static long gone_at = -1;
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

static int read_items(const bjson *j, const char *key, item *out, int cap) {
    int list = bjson_find(j, 0, key), n = 0;
    for (int k = 0; list >= 0 && k < bjson_size(j, list) && n < cap; k++) {
        int at = bjson_item(j, list, k), v;
        item *e = &out[n];
        memset(e, 0, sizeof(*e));
        if (bjson_string(j, bjson_find(j, at, "name"), e->name, sizeof(e->name))) continue;
        if (bjson_string(j, bjson_find(j, at, "title"), e->title, sizeof(e->title))) snprintf(e->title, sizeof(e->title), "%s", e->name);
        bjson_string(j, bjson_find(j, at, "version"), e->version, sizeof(e->version));
        bjson_string(j, bjson_find(j, at, "role"), e->role, sizeof(e->role));
        bjson_string(j, bjson_find(j, at, "folder"), e->folder, sizeof(e->folder));
        if ((v = bjson_find(j, at, "refused")) >= 0 && !bjson_null(j, v)) bjson_string(j, v, e->refused, sizeof(e->refused));
        bjson_bool(j, bjson_find(j, at, "autostart"), &e->autostart);
        bjson_bool(j, bjson_find(j, at, "removing"), &e->removing);
        n++;
    }
    return n;
}

/* A ui package the menu asked to remove goes at the hand-over: it is no choice any more. */
static int removing_ui(const char *name) {
    for (int k = 0; k < npackages; k++) if (!strcmp(packages[k].role, "ui") && !strcmp(packages[k].name, name)) return packages[k].removing;
    return 0;
}

/* What boot offers (ui/choices.json): stock's UI first, shown as FiiO, then each installed ui package;
   the services, the packages to remove and those staged on the card. */
static int load_choices(const char *status) {
    char p[PATH_MAX], buf[8192], deflt[33] = "", keep[33] = "";
    size_t len;
    if (count && screen == SCREEN_START && selected < count) snprintf(keep, sizeof(keep), "%s", entries[selected].ui);
    if (snprintf(p, sizeof(p), "%s/ui/choices.json", status) >= (int)sizeof(p) || read_small(p, buf, sizeof(buf), &len)) return -1;
    bjson j;
    if (bjson_parse(&j, buf, len, 1024)) return -1;
    nservices = read_items(&j, "services", services, MAX_ITEMS);
    npackages = read_items(&j, "packages", packages, MAX_ITEMS);
    nstaged = read_items(&j, "staged", staged, MAX_ITEMS);
    waiting = 0;
    for (int k = 0; k < nstaged; k++) waiting += !staged[k].refused[0];
    int list = bjson_find(&j, 0, "entries");
    bjson_string(&j, bjson_find(&j, 0, "default"), deflt, sizeof(deflt));
    count = 0;
    for (int k = 0; list >= 0 && k < bjson_size(&j, list) && count < MAX_ENTRIES; k++) {
        int at = bjson_item(&j, list, k);
        entry *e = &entries[count];
        memset(e, 0, sizeof(*e));
        if (bjson_string(&j, bjson_find(&j, at, "ui"), e->ui, sizeof(e->ui)) || removing_ui(e->ui)) continue;
        bjson_string(&j, bjson_find(&j, at, "version"), e->version, sizeof(e->version));
        if (!strcmp(e->ui, "stock")) snprintf(e->title, sizeof(e->title), "FiiO");
        else if (bjson_string(&j, bjson_find(&j, at, "title"), e->title, sizeof(e->title))) memcpy(e->title, e->ui, strlen(e->ui) + 1);
        if (!keep[0] && !strcmp(e->ui, deflt) && screen == SCREEN_START) selected = count;
        if (keep[0] && !strcmp(e->ui, keep)) selected = count;
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

static int add_row(int kind, int index, const char *label, const char *value, int hot) {
    if (nrows >= MAX_ROWS) return nrows;
    row *r = &rows[nrows];
    r->kind = kind; r->index = index; r->hot = hot; r->warn = 0;
    snprintf(r->label, sizeof(r->label), "%s", label);
    snprintf(r->value, sizeof(r->value), "%s", value ? value : "");
    return ++nrows;
}

static int confirming(int kind, int index) { return confirm_kind == kind && confirm_index == index && now_ms() < confirm_until; }

/* The rows of this screen (owner, 2026-10-09: the other screens are rows at the end of the list, and
   each of them starts with Back). */
static void build_rows(void) {
    char label[64];
    nrows = 0;
    if (screen == SCREEN_START) {
        for (int k = 0; k < count; k++) add_row(ROW_UI, k, entries[k].title, entries[k].version, 0);
        if (waiting) { snprintf(label, sizeof(label), "%d on the card", waiting); add_row(ROW_WAITING, 0, label, "install", 1); }
        add_row(ROW_SERVICES, 0, "Services", "\xe2\x80\xba", 0);
        add_row(ROW_PACKAGES, 0, "Packages", "\xe2\x80\xba", 0);
    } else if (screen == SCREEN_SERVICES) {
        add_row(ROW_BACK, 0, "\xe2\x80\xb9 Back", "", 0);
        for (int k = 0; k < nservices; k++)
            add_row(ROW_SERVICE, k, services[k].title, services[k].removing ? "removing" : services[k].autostart ? "On" : "Off",
                    !services[k].removing && services[k].autostart);
    } else if (screen == SCREEN_PACKAGES) {
        add_row(ROW_BACK, 0, "\xe2\x80\xb9 Back", "", 0);
        for (int k = 0; k < nstaged; k++) {
            add_row(ROW_STAGED, k, staged[k].title, staged[k].refused[0] ? "refused" : "install", !staged[k].refused[0]);
        }
        for (int k = 0; k < npackages; k++) {
            if (confirming(ROW_INSTALLED, k)) {
                add_row(ROW_INSTALLED, k, packages[k].title, "\xe2\x96\xb6 remove", 1);
                rows[nrows - 1].warn = 1;
            } else add_row(ROW_INSTALLED, k, packages[k].title, packages[k].removing ? "removing" : packages[k].version, 0);
        }
        if (confirming(ROW_EVERYTHING, 0)) {
            add_row(ROW_EVERYTHING, 0, confirm_step ? "Really everything?" : "Everything ours", "\xe2\x96\xb6 again", 1);
            rows[nrows - 1].warn = 1;
        } else add_row(ROW_EVERYTHING, 0, "Remove everything ours", "", 0);
    }
    if (selected >= nrows) selected = nrows ? nrows - 1 : 0;
}

/* The screen: the ring's countdown, the list (three rows at a time around the selection) and
   what the keys do, or what starts. */
static int first_row(void) {
    int top = selected - 1;
    if (top > nrows - ROWS) top = nrows - ROWS;
    return top < 0 ? 0 : top;
}

/* A line that fits its width: cut with an ellipsis. */
static void fit(char *out, size_t cap, int face, const char *s, int width) {
    snprintf(out, cap, "%s", s);
    size_t n = strlen(out);
    while (n > 1 && text_width(face, out) > width) {
        n--;
        while (n > 1 && ((unsigned char)out[n] & 0xc0) == 0x80) n--;
        snprintf(out + n, cap - n, "\xe2\x80\xa6");
    }
}

static void render(long now) {
    char line[96], shown[96];
    if (installing) { render_install(); return; }
    draw_fill(&frame, GROUND);
    if (off) {
        draw_text(&frame, FACE_TITLE, 180, 172, ALIGN_MIDDLE, "Switching off\xe2\x80\xa6", INK);
        return;
    }
    if (screen == SCREEN_GONE) {
        draw_ring(&frame, LINE, ACCENT, 0);
        draw_text(&frame, FACE_TITLE_BOLD, 180, 158, ALIGN_MIDDLE, "Everything ours goes", INK);
        draw_text(&frame, FACE_NOTE, 180, 196, ALIGN_MIDDLE, "at the next start", MUTED);
        return;
    }
    build_rows();
    long left = counting && chosen < 0 && screen == SCREEN_START ? deadline - now : 0;
    draw_ring(&frame, LINE, ACCENT, left > 0 && countdown_ms > 0 ? (unsigned)(left * 65536 / countdown_ms) : 0);
    draw_text(&frame, FACE_LABEL, 180, 74, ALIGN_MIDDLE, screen == SCREEN_SERVICES ? "SERVICES" : screen == SCREEN_PACKAGES ? "PACKAGES" : "START WITH", MUTED);
    int top = first_row();
    for (int k = top; k < nrows && k < top + ROWS; k++) {
        row *r = &rows[k];
        int y = 132 + (k - top) * 48, on = k == selected && (chosen < 0 || screen != SCREEN_START);
        if (screen == SCREEN_START && chosen >= 0) on = r->kind == ROW_UI && r->index == chosen;
        if (on) {
            draw_round_rect(&frame, 58, y - 21, 302, y + 21, 21, r->warn ? WARN : SELECTED);
            draw_disc(&frame, 80, y, 4, ACCENT);
        }
        int value_width = r->value[0] ? text_width(FACE_SMALL, r->value) + 10 : 0;
        fit(shown, sizeof(shown), on ? FACE_TITLE_BOLD : FACE_TITLE, r->label, 288 - 94 - value_width);
        draw_text(&frame, on ? FACE_TITLE_BOLD : FACE_TITLE, 94, y, ALIGN_LEFT, shown, on ? INK : MUTED);
        if (r->value[0]) draw_text(&frame, FACE_SMALL, 288, y, ALIGN_RIGHT, r->value, r->hot ? ACCENT : MUTED);
    }
    if (screen == SCREEN_START && chosen >= 0) {
        snprintf(line, sizeof(line), "Starting %s\xe2\x80\xa6", entries[chosen].title);
        draw_text(&frame, FACE_NOTE, 180, 296, ALIGN_MIDDLE, line, INK);
        return;
    }
    const char *hint = "Vol \xc2\xb1 choose  \xc2\xb7  \xe2\x96\xb6 start";
    if (screen == SCREEN_START) {
        if (waiting) {
            snprintf(line, sizeof(line), waiting == 1 ? "A package waits on the card" : "%d packages wait on the card", waiting);
            draw_text(&frame, FACE_NOTE, 180, 286, ALIGN_MIDDLE, line, MUTED);
        } else if (left > 0) {
            snprintf(line, sizeof(line), "Starts in %ld s", (left + 999) / 1000);
            draw_text(&frame, FACE_NOTE, 180, 286, ALIGN_MIDDLE, line, MUTED);
        }
    } else if (screen == SCREEN_SERVICES) {
        if (!nservices) draw_text(&frame, FACE_NOTE, 180, 230, ALIGN_MIDDLE, "No service is installed", MUTED);
        hint = "\xe2\x96\xb6 on/off  \xc2\xb7  from the next start";
    } else if (confirm_kind == ROW_INSTALLED && now < confirm_until) hint = "\xe2\x96\xb6 again: it goes with its data";
    else if (confirm_kind == ROW_EVERYTHING && now < confirm_until) hint = confirm_step ? "\xe2\x96\xb6 once more: all of ours goes" : "\xe2\x96\xb6 again to remove everything";
    else hint = "\xe2\x96\xb6 install or remove";
    draw_text(&frame, FACE_SMALL, 180, 306, ALIGN_MIDDLE, hint, MUTED);
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
    for (int k = top; k < nrows && k < top + ROWS; k++) {
        int ry = 132 + (k - top) * 48;
        if (x >= 58 && x < 302 && y >= ry - 24 && y < ry + 24) return k;
    }
    return -1;
}

static int is_play(int code) { return code == CODE_PLAY || code == CODE_PLAY_HOLD || code == CODE_PLAY_DOUBLE; }

/* The boot program's command (contract, "The menu's screens"): its answer's ok within 10 s. */
static int ask_boot(const char *a1, const char *a2, const char *a3) {
    if (!program) return -1;
    char *argv[] = {"disc-boot", (char *)a1, (char *)a2, (char *)a3, NULL};
    int pipefd[2];
    if (pipe(pipefd)) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return -1; }
    if (pid == 0) {
        dup2(pipefd[1], 1);
        close(pipefd[0]); close(pipefd[1]);
        execv(program, argv);
        _exit(127);
    }
    close(pipefd[1]);
    char out[512];
    size_t got = 0;
    long until = now_ms() + 10000;
    for (;;) {
        long left = until - now_ms();
        if (left <= 0) { kill(pid, SIGKILL); break; }
        struct pollfd pf = {pipefd[0], POLLIN, 0};
        int ready = poll(&pf, 1, (int)left);
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0) continue;
        ssize_t n = read(pipefd[0], out + got, sizeof(out) - 1 - got);
        if (n <= 0) break;
        got += (size_t)n;
        if (got + 1 >= sizeof(out)) break;
    }
    out[got] = 0;
    close(pipefd[0]);
    int status;
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    fprintf(stderr, "disc-menu: %s %s %s: %s", a1, a2 ? a2 : "", a3 ? a3 : "", out);
    return strstr(out, "\"ok\":true") ? 0 : -1;
}

/* An installation boot runs while the menu shows it (its progress in install.json). */
static void install_staged(const char *folder) {
    if (!program || installer > 0) return;
    pid_t pid = fork();
    if (pid == 0) {
        int null = open("/dev/null", O_WRONLY);
        if (null >= 0) dup2(null, 1);
        char *argv[] = {"disc-boot", "install", (char *)folder, NULL};
        execv(program, argv);
        _exit(127);
    }
    installer = pid;
}

static void play(const char *status) {
    if (selected >= nrows) return;
    row *r = &rows[selected];
    switch (r->kind) {
    case ROW_UI: chosen = r->index; break;
    case ROW_WAITING: case ROW_PACKAGES: screen = SCREEN_PACKAGES; selected = 0; break;
    case ROW_SERVICES: screen = SCREEN_SERVICES; selected = 0; break;
    case ROW_BACK: screen = SCREEN_START; selected = 0; confirm_kind = -1; break;
    case ROW_SERVICE:
        if (!services[r->index].removing && !ask_boot("autostart", services[r->index].name, services[r->index].autostart ? "off" : "on"))
            load_choices(status);
        break;
    case ROW_STAGED: if (!staged[r->index].refused[0]) install_staged(staged[r->index].folder); break;
    case ROW_INSTALLED:
        if (packages[r->index].removing) break;
        if (!confirming(ROW_INSTALLED, r->index)) { confirm_kind = ROW_INSTALLED; confirm_index = r->index; confirm_until = now_ms() + CONFIRM_MS; break; }
        confirm_kind = -1;
        if (!ask_boot("remove", packages[r->index].role, packages[r->index].name)) load_choices(status);
        break;
    case ROW_EVERYTHING:
        if (!confirming(ROW_EVERYTHING, 0)) { confirm_kind = ROW_EVERYTHING; confirm_index = 0; confirm_step = 0; confirm_until = now_ms() + CONFIRM_MS; break; }
        if (!confirm_step) { confirm_step = 1; confirm_until = now_ms() + CONFIRM_MS; break; }
        confirm_kind = -1;
        if (!ask_boot("remove-everything", NULL, NULL)) { screen = SCREEN_GONE; gone_at = now_ms(); }
        break;
    }
}

static const char *status_dir;

static void key(int code, int value) {
    fprintf(stderr, "disc-menu: key 0x%x %d\n", code, value);
    if (value == 0) { let_go(code); return; }
    if (value != 1 || is_held(code) || installing || installer > 0 || screen == SCREEN_GONE) return;
    if (recovery_start && is_play(code) && (answerable_at < 0 || now_ms() - answerable_at < RECOVERY_RELEASE_MS)) {
        fprintf(stderr, "disc-menu: Play's release after the recovery, not an answer\n");
        return;
    }
    if (code == CODE_POWER_HOLD) { off = 1; return; }
    counting = 0;
    build_rows();
    if (code == CODE_VOLUME_UP && selected > 0) selected--;
    else if (code == CODE_VOLUME_DOWN && selected < nrows - 1) selected++;
    else if (code == CODE_PLAY) play(status_dir);
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
                build_rows();
                int r = row_at(PANEL - 1 - touch_x, PANEL - 1 - touch_y);
                touching = 0;
                if (r >= 0 && !installing && installer < 0 && screen != SCREEN_GONE) { selected = r; play(status_dir); }
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
    program = getenv("DISC_BOOT_PROGRAM");
    status_dir = status;
    if (load_choices(status)) { fprintf(stderr, "disc-menu: nothing to offer\n"); return 1; }
    /* Packages waiting on the card hold the countdown (owner, 2026-10-09). */
    counting = !waiting;
    ring_prepare();
    fb_open(fb_path);
    input_open(keys, touch, status);
    recovery_start = started_with_play(status);
    setvbuf(stderr, NULL, _IONBF, 0);
    deadline = now_ms() + countdown_ms;
    long shown = -1, polled = 0;
    int last_selected = -1, last_counting = -1, last_screen = -1, last_rows = -1, was_installing = read_install(status), last_done = -1;
    for (;;) {
        read_events();
        long now = now_ms();
        if (installer > 0 && waitpid(installer, NULL, WNOHANG) == installer) {
            /* The menu's installation ended: what boot offers now, on this screen. */
            installer = -1;
            load_choices(status);
            last_rows = -1;
        }
        if (now - polled >= 200) {
            polled = now;
            int was = installing;
            read_install(status);
            if (was && !installing && installer < 0 && screen == SCREEN_START) {
                /* Installed with Play: what boot offers now, and the countdown from the start. */
                count = 0; selected = 0; chosen = -1;
                load_choices(status);
                counting = !waiting;
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
        if (screen == SCREEN_GONE && now - gone_at >= 3000) answer(run, launcher, "stock");
        if (counting && screen == SCREEN_START && now >= deadline) { chosen = selected < count ? selected : 0; counting = 0; }
        /* A frame on every change, ten a second while the ring runs down, four otherwise (a question
           to confirm goes after its 5 s). */
        if (chosen >= 0 || selected != last_selected || counting != last_counting || screen != last_screen || nrows != last_rows
            || now - shown >= (counting ? 100 : 250)) {
            render(now);
            show();
            shown = now; last_selected = selected; last_counting = counting; last_screen = screen; last_rows = nrows;
        }
        if (chosen >= 0) answer(run, launcher, entries[chosen].ui);
        pause_s(0.02);
    }
}
