/* disc-boot: the boot layer's program (docs/contract.md). No network, no card code. */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE
#include "boot_util.h"
#include "manifest.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#if defined(__linux__) && !defined(DISC_BOOT_FIXTURE)
#include <sys/klog.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#endif
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef DISC_BUILD
#define DISC_BUILD "unknown"
#endif
#define RESTARTS 3
#define BOOT_LOOP 3
#define UI_STARTS 3
#define MENU_FAILURES 2
#define MAX_UIS 16
/* A role's status file: sixteen installed interfaces, each with its project's page, fit. */
#define STATUS_BYTES 8192
#define LOG_CAP 65536
#define RESERVE (16LL * 1024 * 1024)
#define STOCK_LIBS "/usr/lib:/usr/lib/pulseaudio/:/usr/data/lib:/usr/data/lib/ffmpeg"

extern char **environ;
/* Seconds; the fixture build shortens them. */
/* The card is mounted after S99 (stock mounts it once mq_player runs): recovery waits up to 90 s. */
static double t_confirm = 180, t_grace = 5, t_window = 600, t_backoff = 2, t_card = 90, t_ui_window = 120, t_menu = 60, t_pair = 2,
              t_install = 180;
static char profile[17], card[PATH_MAX] = "/tmp/sdcard", card_source[128] = "/dev/mmcblk0p1";
/* The boot program itself, which a package runs as `verify` (contract, "Environment"); the fixture's own file. */
static char program[PATH_MAX] = "/opt/disc-boot/disc-boot";
static char mode[9], reason[12];
static char last_request[200];
static volatile sig_atomic_t stopping;

static void on_term(int sig) { (void)sig; stopping = 1; }

static void fixture_init(const char *argv0) {
#ifdef DISC_BOOT_FIXTURE
    char self[PATH_MAX];
    if (realpath(argv0, self)) snprintf(program, sizeof(program), "%s", self);
    const char *root = getenv("DISC_BOOT_FIXTURE_ROOT");
    if (!root || root[0] != '/' || snprintf(boot_root, sizeof(boot_root), "%s", root) >= (int)sizeof(boot_root)) {
        fprintf(stderr, "disc-boot fixture: DISC_BOOT_FIXTURE_ROOT must be an absolute path\n"); exit(2);
    }
    const char *timing = getenv("DISC_BOOT_FIXTURE_TIMING");
    if (timing) {
        char copy[256]; snprintf(copy, sizeof(copy), "%s", timing);
        for (char *item = strtok(copy, ","); item; item = strtok(NULL, ",")) {
            char *eq = strchr(item, '=');
            if (!eq) continue;
            *eq = 0;
            double v = atof(eq + 1);
            if (!strcmp(item, "confirm")) t_confirm = v; else if (!strcmp(item, "grace")) t_grace = v;
            else if (!strcmp(item, "window")) t_window = v; else if (!strcmp(item, "backoff")) t_backoff = v;
            else if (!strcmp(item, "card")) t_card = v; else if (!strcmp(item, "ui")) t_ui_window = v;
            else if (!strcmp(item, "menu")) t_menu = v;
            else if (!strcmp(item, "pair")) t_pair = v;
            else if (!strcmp(item, "install")) t_install = v;
        }
    }
#else
    (void)argv0;
#endif
}

/* Volume Up and Play held at power-on, from the GPIO pin level: a key held from power-on never
   reaches the input layer (diskOS, validated on a device). Port B at 0x10010100, PxPIN, active low. */
static int read_keys(int *volume_up, int *play) {
#ifdef DISC_BOOT_FIXTURE
    char p[PATH_MAX], buf[256];
    bpath(p, "/fixture/keys");
    if (read_small(p, buf, sizeof(buf), NULL)) return -1;
    *volume_up = strstr(buf, "volume-up") != NULL; *play = strstr(buf, "play") != NULL;
    return 0;
#else
    int fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (fd < 0) return -1;
    void *map = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, 0x10010000);
    close(fd);
    if (map == MAP_FAILED) return -1;
    uint32_t gpb = *(volatile uint32_t *)((char *)map + 0x100);
    munmap(map, 4096);
    *volume_up = !((gpb >> 13) & 1u); *play = !((gpb >> 15) & 1u);
    return 0;
#endif
}

static int profile_ok(const char *s) {
    size_t n = strlen(s);
    if (n < 1 || n > 16) return 0;
    for (; *s; s++) if (!((*s >= '0' && *s <= '9') || *s == '.')) return 0;
    return 1;
}

static int abs_path_ok(const char *s) {
    return s[0] == '/' && strlen(s) < 200 && !strstr(s, "..") && !strpbrk(s, " \t\n\"\\");
}

static void options(int argc, char **argv, int from) {
    for (int i = from; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--profile") && profile_ok(argv[i + 1])) snprintf(profile, sizeof(profile), "%s", argv[i + 1]);
        else if (!strcmp(argv[i], "--card") && abs_path_ok(argv[i + 1])) snprintf(card, sizeof(card), "%s", argv[i + 1]);
        else if (!strcmp(argv[i], "--card-source") && abs_path_ok(argv[i + 1])) snprintf(card_source, sizeof(card_source), "%s", argv[i + 1]);
        else { fprintf(stderr, "disc-boot: bad option %s\n", argv[i]); exit(2); }
    }
}

static char other(char slot) { return slot == 'a' ? 'b' : 'a'; }

/* Where a package lives under DATA_DIR (contract, "Several UIs and the boot menu"): "service",
   "menu", or "ui/<name>" for each ui package. The role is what the domain holds. */
static const char *role_of(const char *domain) { return strncmp(domain, "ui/", 3) ? domain : "ui"; }
static const char *ui_name(const char *domain) { return strncmp(domain, "ui/", 3) ? NULL : domain + 3; }
static void ui_domain(char out[40], const char *name) { snprintf(out, 40, "ui/%s", name); }

/* The SHA-256 of a slot's package.json: the fingerprint a rollback target keeps. */
static int slot_fingerprint(const char *domain, char slot, char hex[65]) {
    char p[PATH_MAX];
    long long size;
    bpath(p, DATA_DIR "/%s/%c/package.json", domain, slot);
    return file_sha256(p, hex, &size);
}
/* The previous slot still holds the version that was confirmed there: a package may stage an
   update into its inactive slot, which is that one, and then it is no rollback target. */
static int previous_intact(const char *domain, const role_state *rs) {
    char hex[65];
    return rs->previous && rs->previous_manifest[0] && !slot_fingerprint(domain, rs->previous, hex) && !strcmp(hex, rs->previous_manifest);
}
/* Target becomes the tentative current slot; a confirmed current one becomes the rollback target,
   with its fingerprint, and a tentative one leaves the earlier target as it was. */
static void make_current(const char *domain, role_state *rs, char target) {
    if (rs->confirmed) {
        rs->previous = rs->current && rs->current != target && !slot_fingerprint(domain, rs->current, rs->previous_manifest) ? rs->current : 0;
    }
    if (rs->previous == target || !rs->previous) { rs->previous = 0; rs->previous_manifest[0] = 0; }
    rs->current = target; rs->confirmed = 0;
}

/* This boot's UI (contract, "The choice"): a ui package's name or "stock", how it was chosen,
   and whether the menu is still to ask. Kept in RUN_DIR/ui/choice.json for the launchers. */
typedef struct { char ui[33], by[10], note[200]; int menu; } ui_choice;

static void write_choice(const ui_choice *c) {
    char p[PATH_MAX], buf[600], note[260];
    bpath(p, RUN_DIR "/ui");
    mkdirs(p, 0755);
    bpath(p, RUN_DIR "/ui/choice.json");
    json_str(note, sizeof(note), c->note);
    int n = snprintf(buf, sizeof(buf), "{\"schema\":1,\"ui\":\"%s\",\"by\":\"%s\",\"menu\":%s,\"note\":%s}\n",
                     c->ui, c->by, c->menu ? "true" : "false", note);
    if (n > 0 && n < (int)sizeof(buf)) write_atomic(p, buf, (size_t)n, 0644);
}

static int read_choice(ui_choice *c) {
    char p[PATH_MAX], buf[SMALL_FILE];
    size_t len;
    memset(c, 0, sizeof(*c));
    bpath(p, RUN_DIR "/ui/choice.json");
    if (read_small(p, buf, sizeof(buf), &len)) return -1;
    bjson j;
    if (bjson_parse(&j, buf, len, 32)) return -1;
    int bad = bjson_string(&j, bjson_find(&j, 0, "ui"), c->ui, sizeof(c->ui)) || bjson_string(&j, bjson_find(&j, 0, "by"), c->by, sizeof(c->by))
        || bjson_bool(&j, bjson_find(&j, 0, "menu"), &c->menu);
    if (!bad && bjson_string(&j, bjson_find(&j, 0, "note"), c->note, sizeof(c->note))) c->note[0] = 0;
    bjson_free(&j);
    return bad || (strcmp(c->ui, "stock") && !package_name_ok(c->ui)) ? -1 : 0;
}

static int ui_installed(const char *name) {
    char domain[40];
    role_state rs;
    if (!package_name_ok(name)) return 0;
    ui_domain(domain, name);
    return !rstate_read(domain, &rs) && rs.current;
}

typedef struct { char name[33], version[65], title[33], homepage[201], slot; int confirmed; } ui_entry;

static int entry_order(const void *a, const void *b) { return strcmp(((const ui_entry *)a)->name, ((const ui_entry *)b)->name); }

/* The installed ui packages, by name. */
static int list_uis(ui_entry *out, int cap) {
    char dir[PATH_MAX];
    bpath(dir, DATA_DIR "/ui");
    DIR *d = opendir(dir);
    if (!d) return 0;
    manifest *m = malloc(sizeof(*m));
    struct dirent *e;
    int n = 0;
    while (m && n < cap && (e = readdir(d))) {
        char domain[40], slot[PATH_MAX], err[160];
        role_state rs;
        if (!package_name_ok(e->d_name)) continue;
        ui_domain(domain, e->d_name);
        if (rstate_read(domain, &rs) || !rs.current) continue;
        snprintf(out[n].name, sizeof(out[n].name), "%.32s", e->d_name);
        bpath(slot, DATA_DIR "/%s/%c", domain, rs.current);
        out[n].version[0] = out[n].title[0] = out[n].homepage[0] = 0;
        if (!manifest_load(slot, m, err, sizeof(err))) {
            snprintf(out[n].version, sizeof(out[n].version), "%s", m->version);
            snprintf(out[n].title, sizeof(out[n].title), "%s", m->title[0] ? m->title : m->name);
            snprintf(out[n].homepage, sizeof(out[n].homepage), "%s", m->homepage);
        }
        out[n].slot = rs.current;
        out[n].confirmed = rs.confirmed;
        n++;
    }
    closedir(d);
    free(m);
    qsort(out, (size_t)n, sizeof(*out), entry_order);
    return n;
}

/* The ui role's status also says this boot's choice and what is installed. */
static void ui_extra(char *out, size_t cap) {
    char p[PATH_MAX], choice[SMALL_FILE], version[80], homepage[220];
    size_t len;
    bpath(p, RUN_DIR "/ui/choice.json");
    if (read_small(p, choice, sizeof(choice), &len)) snprintf(choice, sizeof(choice), "null");
    else while (len && choice[len - 1] == '\n') choice[--len] = 0;
    ui_entry list[MAX_UIS];
    int count = list_uis(list, MAX_UIS);
    size_t o = (size_t)snprintf(out, cap, ",\"choice\":%s,\"installed\":[", choice);
    for (int k = 0; k < count && o < cap; k++) {
        json_str(version, sizeof(version), list[k].version);
        if (list[k].homepage[0]) json_str(homepage, sizeof(homepage), list[k].homepage);
        else snprintf(homepage, sizeof(homepage), "null");
        o += (size_t)snprintf(out + o, cap - o, "%s{\"name\":\"%s\",\"version\":%s,\"homepage\":%s,\"slot\":\"%c\",\"confirmed\":%s}",
                              k ? "," : "", list[k].name, version, homepage, list[k].slot, list[k].confirmed ? "true" : "false");
    }
    if (o < cap) o += (size_t)snprintf(out + o, cap - o, "]");
    if (o >= cap) snprintf(out, cap, ",\"choice\":null,\"installed\":null");
}

static void role_status(const char *domain, const char *state, const manifest *m, const role_state *rs, int failures, const char *note) {
    const char *role = role_of(domain);
    plog("%s %s%s%s%s%s", domain, state, m ? " " : "", m ? m->version : "", note && *note ? ": " : "", note ? note : "");
    char p[PATH_MAX], buf[STATUS_BYTES], name[80], version[80], homepage[220] = "null", noted[300], request[260], previous[400] = "null",
         extra[6400] = "";
    bpath(p, RUN_DIR);
    mkdirs(p, 0755);
    bpath(p, RUN_DIR "/%s.json", role);
    json_str(name, sizeof(name), m ? m->name : "");
    json_str(version, sizeof(version), m ? m->version : "");
    if (m && m->homepage[0]) json_str(homepage, sizeof(homepage), m->homepage);
    json_str(noted, sizeof(noted), note ? note : "");
    json_str(request, sizeof(request), last_request);
    /* The version a rollback returns to, while its slot still holds it. */
    manifest *pm = rs && previous_intact(domain, rs) ? malloc(sizeof(*pm)) : NULL;
    char dir[PATH_MAX], err[160], pname[80], pversion[80];
    if (pm) {
        bpath(dir, DATA_DIR "/%s/%c", domain, rs->previous);
        if (!manifest_load(dir, pm, err, sizeof(err))) {
            json_str(pname, sizeof(pname), pm->name);
            json_str(pversion, sizeof(pversion), pm->version);
            if (snprintf(previous, sizeof(previous), "{\"slot\":\"%c\",\"name\":%s,\"version\":%s,\"manifest\":\"%.64s\"}",
                         rs->previous, pname, pversion, rs->previous_manifest) >= (int)sizeof(previous)) snprintf(previous, sizeof(previous), "null");
        }
        free(pm);
    }
    if (!strcmp(role, "ui")) ui_extra(extra, sizeof(extra));
    int n = snprintf(buf, sizeof(buf),
        "{\"schema\":1,\"role\":\"%s\",\"state\":\"%s\",\"name\":%s,\"version\":%s,\"homepage\":%s,\"slot\":%s%c%s,\"confirmed\":%s,"
        "\"failures\":%d,\"note\":%s,\"lastRequest\":%s,\"previous\":%s%s}\n",
        role, state, m ? name : "null", m ? version : "null", homepage,
        rs && rs->current ? "\"" : "nul", rs && rs->current ? rs->current : 'l', rs && rs->current ? "\"" : "",
        rs && rs->confirmed ? "true" : "false", failures, noted, last_request[0] ? request : "null", previous, extra);
    if (n > 0 && n < (int)sizeof(buf)) write_atomic(p, buf, (size_t)n, 0644);
}

/* The boot-loop count clears once nothing that runs in this boot is tentative: the service package,
   and the UI chosen for this boot when it is a package (the menu's answer is no condition).
   Called under the state lock. */
static void maybe_clear_loop(void) {
    role_state rs;
    ui_choice c;
    if (rstate_read("service", &rs) || (rs.current && !rs.confirmed)) return;
    if (!read_choice(&c) && strcmp(c.ui, "stock")) {
        char domain[40];
        ui_domain(domain, c.ui);
        if (rstate_read(domain, &rs) || (rs.current && !rs.confirmed)) return;
    }
    global_state g;
    if (!gstate_read(&g) && g.unconfirmed) { g.unconfirmed = 0; gstate_write(&g); }
}

static void confirm(const char *domain) {
    int lock = state_lock();
    role_state rs;
    if (!rstate_read(domain, &rs) && rs.current && !rs.confirmed) { rs.confirmed = 1; rstate_write(domain, &rs); }
    maybe_clear_loop();
    state_unlock(lock);
}

static int slot_check(const char *domain, char slot, manifest *m, char *err, size_t cap) {
    char dir[PATH_MAX];
    bpath(dir, DATA_DIR "/%s/%c", domain, slot);
    if (manifest_load(dir, m, err, cap) || manifest_fits(m, role_of(domain), profile, err, cap)) return -1;
    /* A ui package lives under its own name: an update staged under another name is not it. */
    const char *want = ui_name(domain);
    if (want && strcmp(m->name, want)) { snprintf(err, cap, "the package is named %s, not %s", m->name, want); return -1; }
    return package_verify(dir, m, 1, err, cap) ? -1 : 0;
}

static int rollback(const char *domain, role_state *rs) {
    if (!previous_intact(domain, rs)) return -1;
    int lock = state_lock();
    rs->current = rs->previous; rs->previous = 0; rs->previous_manifest[0] = 0; rs->confirmed = 1;
    int r = rstate_write(domain, rs);
    state_unlock(lock);
    return r;
}

/* An installed ui package goes, with its data when asked; the default and the next boot's choice
   forget it (a removed default makes stock's UI the default). Under the state lock. */
static void remove_ui_locked(const char *name, int purge) {
    char p[PATH_MAX];
    bpath(p, DATA_DIR "/ui/%s", name);
    remove_tree(p);
    if (purge) { bpath(p, DATA_DIR "/data/%s", name); remove_tree(p); }
    global_state g;
    if (!gstate_read(&g) && (!strcmp(g.ui, name) || !strcmp(g.next, name))) {
        if (!strcmp(g.ui, name)) snprintf(g.ui, sizeof(g.ui), "stock");
        if (!strcmp(g.next, name)) g.next[0] = 0;
        gstate_write(&g);
    }
}

/* The requests about the choice of UI, from any package (ui-remove only from the service or the
   menu): they change the next boots, and a removal waits for the launcher's next start. */
static void ui_request(const char *domain, const bjson *j, const char *action) {
    char wanted[33] = "", p[PATH_MAX];
    int v = bjson_find(j, 0, "ui");
    if (v < 0 || bjson_string(j, v, wanted, sizeof(wanted)) || (strcmp(wanted, "stock") && !package_name_ok(wanted))) {
        snprintf(last_request, sizeof(last_request), "%s refused: ui must name an installed ui package or stock", action);
        return;
    }
    int is_stock = !strcmp(wanted, "stock");
    if (!is_stock && !ui_installed(wanted)) { snprintf(last_request, sizeof(last_request), "%s refused: %s is not installed", action, wanted); return; }
    if (!strcmp(action, "ui-remove")) {
        if (strcmp(role_of(domain), "service") && strcmp(role_of(domain), "menu")) {
            snprintf(last_request, sizeof(last_request), "ui-remove refused: only the service or the menu removes a ui package"); return;
        }
        if (is_stock) { snprintf(last_request, sizeof(last_request), "ui-remove refused: stock's UI stays"); return; }
        int purge = 0, pv = bjson_find(j, 0, "purge");
        if (pv >= 0) bjson_bool(j, pv, &purge);
        bpath(p, DATA_DIR "/ui/%s/remove", wanted);
        const char *marker = purge ? "{\"purge\":true}\n" : "{\"purge\":false}\n";
        write_atomic(p, marker, strlen(marker), 0644);
        snprintf(last_request, sizeof(last_request), "%s removed at the UI's next start%s", wanted, purge ? " with its data" : "");
        return;
    }
    global_state g;
    gstate_read(&g);
    if (!strcmp(action, "ui-default")) snprintf(g.ui, sizeof(g.ui), "%s", wanted);
    else snprintf(g.next, sizeof(g.next), "%s", wanted);
    gstate_write(&g);
    snprintf(last_request, sizeof(last_request), "%s %s", !strcmp(action, "ui-default") ? "default ui" : "next boot's ui", wanted);
}

/* A package's request, applied after it exited (docs/contract.md, "Requests from a package").
   Returns 1 when one was there (even refused), 0 otherwise. */
static int handle_request(const char *domain, const char *current_name) {
    char p[PATH_MAX], buf[SMALL_FILE];
    bpath(p, DATA_DIR "/%s/request", domain);
    if (!exists(p)) return 0;
    size_t len;
    int readable = read_small(p, buf, sizeof(buf), &len) == 0;
    unlink(p);
    bjson j;
    char action[16] = "", err[160] = "";
    if (!readable || bjson_parse(&j, buf, len, 32)) { snprintf(last_request, sizeof(last_request), "refused: unreadable request"); return 1; }
    int a = bjson_find(&j, 0, "action");
    if (a < 0 || bjson_string(&j, a, action, sizeof(action))) { bjson_free(&j); snprintf(last_request, sizeof(last_request), "refused: no action"); return 1; }
    int lock = state_lock();
    role_state rs;
    if (rstate_read(domain, &rs)) { snprintf(last_request, sizeof(last_request), "refused: the role's state is unreadable"); goto done; }
    if (!strcmp(action, "activate")) {
        char target = rs.current ? other(rs.current) : 'a';
        manifest *m = malloc(sizeof(*m));
        if (!m || slot_check(domain, target, m, err, sizeof(err))) snprintf(last_request, sizeof(last_request), "activate refused: %s", m ? err : "out of memory");
        else {
            make_current(domain, &rs, target);
            rstate_write(domain, &rs);
            snprintf(last_request, sizeof(last_request), "activated %s %s", m->name, m->version);
        }
        free(m);
    } else if (!strcmp(action, "rollback")) {
        manifest *m = malloc(sizeof(*m));
        if (!rs.previous) snprintf(last_request, sizeof(last_request), "rollback refused: no previous version");
        else if (!previous_intact(domain, &rs)) snprintf(last_request, sizeof(last_request), "rollback refused: the previous version was replaced");
        else if (!m || slot_check(domain, rs.previous, m, err, sizeof(err))) snprintf(last_request, sizeof(last_request), "rollback refused: %s", m ? err : "out of memory");
        else {
            rs.current = rs.previous; rs.previous = 0; rs.previous_manifest[0] = 0; rs.confirmed = 1;
            rstate_write(domain, &rs);
            snprintf(last_request, sizeof(last_request), "rolled back to %s %s", m->name, m->version);
        }
        free(m);
    } else if (!strcmp(action, "default")) {
        char wanted[9] = "";
        int v = bjson_find(&j, 0, "mode");
        global_state g;
        if (v < 0 || bjson_string(&j, v, wanted, sizeof(wanted)) || (strcmp(wanted, "stock") && strcmp(wanted, "platform")))
            snprintf(last_request, sizeof(last_request), "default refused: mode must be stock or platform");
        else {
            gstate_read(&g);
            snprintf(g.mode, sizeof(g.mode), "%s", wanted);
            gstate_write(&g);
            snprintf(last_request, sizeof(last_request), "default mode %s", wanted);
        }
    } else if (!strcmp(action, "remove")) {
        int purge = 0, v = bjson_find(&j, 0, "purge");
        if (v >= 0) bjson_bool(&j, v, &purge);
        if (ui_name(domain)) remove_ui_locked(ui_name(domain), purge);
        else {
            bpath(p, DATA_DIR "/%s/a", domain); remove_tree(p);
            bpath(p, DATA_DIR "/%s/b", domain); remove_tree(p);
            bpath(p, DATA_DIR "/%s/state.json", domain); unlink(p);
            if (purge && current_name && current_name[0]) { bpath(p, DATA_DIR "/data/%s", current_name); remove_tree(p); }
        }
        maybe_clear_loop();
        snprintf(last_request, sizeof(last_request), purge ? "removed with its data" : "removed");
    } else if (!strcmp(action, "ui-default") || !strcmp(action, "ui-next") || !strcmp(action, "ui-remove")) {
        ui_request(domain, &j, action);
    } else snprintf(last_request, sizeof(last_request), "refused: unknown action");
done:
    state_unlock(lock);
    bjson_free(&j);
    blog("%s request: %s", domain, last_request);
    return 1;
}

/* Whether a domain's pending request is about the choice of UI (ui-default, ui-next, ui-remove). */
static int choice_request_pending(const char *domain) {
    char p[PATH_MAX], buf[SMALL_FILE], action[16] = "";
    size_t len;
    bpath(p, DATA_DIR "/%s/request", domain);
    if (read_small(p, buf, sizeof(buf), &len)) return 0;
    bjson j;
    if (bjson_parse(&j, buf, len, 32)) return 0;
    int ok = !bjson_string(&j, bjson_find(&j, 0, "action"), action, sizeof(action)) && !strncmp(action, "ui-", 3);
    bjson_free(&j);
    return ok;
}

/* At a platform boot and at each start of the UI launcher no ui package runs: the requests about
   the choice that any package left (a running service's too) and the removals they asked for apply. */
static void apply_pending(void) {
    char saved[sizeof(last_request)], dir[PATH_MAX];
    snprintf(saved, sizeof(saved), "%s", last_request);
    if (choice_request_pending("service")) handle_request("service", NULL);
    if (choice_request_pending("menu")) handle_request("menu", NULL);
    bpath(dir, DATA_DIR "/ui");
    DIR *d = opendir(dir);
    if (d) {
        struct dirent *e;
        while ((e = readdir(d))) {
            char domain[40], p[PATH_MAX], buf[64];
            if (!package_name_ok(e->d_name)) continue;
            ui_domain(domain, e->d_name);
            if (choice_request_pending(domain)) handle_request(domain, NULL);
            bpath(p, DATA_DIR "/ui/%s/remove", e->d_name);
            if (!exists(p)) continue;
            int purge = !read_small(p, buf, sizeof(buf), NULL) && strstr(buf, "true") != NULL;
            int lock = state_lock();
            remove_ui_locked(e->d_name, purge);
            state_unlock(lock);
            blog("ui %s removed%s", e->d_name, purge ? " with its data" : "");
        }
        closedir(d);
    }
    snprintf(last_request, sizeof(last_request), "%s", saved);
}

/* This boot's choice of UI (contract, "The choice"): next (consumed), else the default, else the
   first installed ui package, else stock's UI; a name that is not installed gives stock's UI.
   The menu asks unless next chose. Returns whether the UI launcher has anything to start. */
static int decide_ui(int consume_next) {
    ui_choice c;
    char p[PATH_MAX];
    /* Again within a boot (after a recovery): a choice by next stands. */
    if (!consume_next && !read_choice(&c) && !strcmp(c.by, "next") && (!strcmp(c.ui, "stock") || ui_installed(c.ui))) {
        write_choice(&c);
        return strcmp(c.ui, "stock") != 0;
    }
    memset(&c, 0, sizeof(c));
    int lock = state_lock();
    global_state g;
    int readable = gstate_read(&g) == 0;
    if (consume_next && readable && g.next[0]) {
        snprintf(c.ui, sizeof(c.ui), "%s", g.next); snprintf(c.by, sizeof(c.by), "next");
        g.next[0] = 0;
        gstate_write(&g);
    } else {
        snprintf(c.by, sizeof(c.by), "default");
        if (readable && g.ui[0]) snprintf(c.ui, sizeof(c.ui), "%s", g.ui);
        else {
            ui_entry first[MAX_UIS];
            snprintf(c.ui, sizeof(c.ui), "%s", list_uis(first, MAX_UIS) ? first[0].name : "stock");
        }
    }
    state_unlock(lock);
    if (strcmp(c.ui, "stock") && !ui_installed(c.ui)) {
        snprintf(c.note, sizeof(c.note), "%s is not installed", c.ui);
        snprintf(c.ui, sizeof(c.ui), "stock"); snprintf(c.by, sizeof(c.by), "fallback");
    }
    role_state ms;
    c.menu = strcmp(c.by, "next") && !rstate_read("menu", &ms) && ms.current;
    /* What the menu left in an earlier boot is no answer for this one. */
    bpath(p, RUN_DIR "/menu/choice"); unlink(p);
    bpath(p, RUN_DIR "/menu/started"); unlink(p);
    bpath(p, RUN_DIR "/menu/failures"); unlink(p);
    write_choice(&c);
    return c.menu || strcmp(c.ui, "stock");
}

/* What the menu offers: every installed ui package (its title, or its name) and stock's UI with
   the firmware profile's version, with this boot's default. */
static void write_choices(const ui_choice *c) {
    char p[PATH_MAX], buf[SMALL_FILE], version[80], title[48];
    ui_entry list[MAX_UIS];
    int count = list_uis(list, MAX_UIS);
    size_t o = (size_t)snprintf(buf, sizeof(buf), "{\"schema\":1,\"default\":\"%s\",\"entries\":[", c->ui);
    for (int k = 0; k < count && o < sizeof(buf); k++) {
        json_str(version, sizeof(version), list[k].version);
        json_str(title, sizeof(title), list[k].title[0] ? list[k].title : list[k].name);
        o += (size_t)snprintf(buf + o, sizeof(buf) - o, "{\"ui\":\"%s\",\"title\":%s,\"version\":%s,\"confirmed\":%s},",
                              list[k].name, title, version, list[k].confirmed ? "true" : "false");
    }
    if (o < sizeof(buf)) o += (size_t)snprintf(buf + o, sizeof(buf) - o, "{\"ui\":\"stock\",\"version\":\"%s\"}]}\n", profile);
    if (o >= sizeof(buf)) return;
    bpath(p, RUN_DIR "/ui/choices.json");
    write_atomic(p, buf, o, 0644);
}

static void add_env(char **envp, int *n, const char *key, const char *value) {
    size_t len = strlen(key) + strlen(value) + 2;
    char *e = malloc(len);
    if (!e) exit(2);
    snprintf(e, len, "%s=%s", key, value);
    envp[(*n)++] = e;
}

/* The package's environment (docs/contract.md, "Environment of a package"). */
static char **package_env(const char *domain, const manifest *m, char slot, int inherit) {
    const char *role = role_of(domain);
    char slotdir[PATH_MAX], inactive[PATH_MAX], request[PATH_MAX], data[PATH_MAX], run[PATH_MAX], status[PATH_MAX], cardp[PATH_MAX], libs[PATH_MAX * 2];
    bpath(slotdir, DATA_DIR "/%s/%c", domain, slot);
    bpath(inactive, DATA_DIR "/%s/%c", domain, other(slot));
    bpath(request, DATA_DIR "/%s/request", domain);
    bpath(data, DATA_DIR "/data/%s", m->name);
    bpath(run, RUN_DIR "/%s", role);
    bpath(status, RUN_DIR);
    bpath(cardp, "%s", card);
    mkdirs(data, 0755); mkdirs(run, 0755); mkdirs(inactive, 0755);
    const char *stock_libs = inherit && getenv("LD_LIBRARY_PATH") ? getenv("LD_LIBRARY_PATH") : STOCK_LIBS;
    snprintf(libs, sizeof(libs), "%s/lib:%s", slotdir, stock_libs);
    size_t count = 0;
    if (inherit) for (char **e = environ; *e; e++) count++;
    char **envp = calloc(count + 20, sizeof(char *));
    if (!envp) exit(2);
    int n = 0;
    if (inherit)
        for (char **e = environ; *e; e++)
            if (strncmp(*e, "LD_LIBRARY_PATH=", 16) && strncmp(*e, "DISC_BOOT_", 10)) envp[n++] = *e;
    if (!inherit) { add_env(envp, &n, "PATH", "/usr/sbin:/usr/bin:/sbin:/bin"); add_env(envp, &n, "HOME", data); }
    add_env(envp, &n, "LD_LIBRARY_PATH", libs);
    char api[8]; snprintf(api, sizeof(api), "%d", BOOT_API);
    add_env(envp, &n, "DISC_BOOT_API", api);
    add_env(envp, &n, "DISC_BOOT_ROLE", role);
    add_env(envp, &n, "DISC_BOOT_PROFILE", profile);
    add_env(envp, &n, "DISC_BOOT_SLOT", slotdir);
    add_env(envp, &n, "DISC_BOOT_INACTIVE", inactive);
    add_env(envp, &n, "DISC_BOOT_REQUEST", request);
    add_env(envp, &n, "DISC_BOOT_DATA", data);
    add_env(envp, &n, "DISC_BOOT_RUN", run);
    add_env(envp, &n, "DISC_BOOT_STATUS", status);
    add_env(envp, &n, "DISC_BOOT_CARD", cardp);
    add_env(envp, &n, "DISC_BOOT_PROGRAM", program);
    if (!strcmp(role, "menu")) {
        /* Where the menu hands over once it answered: the UI launcher, in its own process. */
        char launcher[PATH_MAX];
        bpath(launcher, "/opt/disc-boot/mq_ui");
        add_env(envp, &n, "DISC_BOOT_LAUNCHER", launcher);
    }
#ifdef DISC_BOOT_FIXTURE
    /* The fixture's world reaches a package only so that the boot program it runs (verify) sees it too. */
    add_env(envp, &n, "DISC_BOOT_FIXTURE_ROOT", boot_root);
    if (getenv("DISC_BOOT_FIXTURE_TIMING")) add_env(envp, &n, "DISC_BOOT_FIXTURE_TIMING", getenv("DISC_BOOT_FIXTURE_TIMING"));
#endif
    envp[n] = NULL;
    return envp;
}

static char **package_argv(const char *argv0, const manifest *m) {
    char **argv = calloc((size_t)m->nargs + 2, sizeof(char *));
    if (!argv) exit(2);
    argv[0] = (char *)argv0;
    for (int k = 0; k < m->nargs; k++) argv[k + 1] = (char *)m->args[k];
    return argv;
}

/* Standard input, output and error are the only descriptors a program of ours or a package gets:
   what else is open (the boot log, the copies dup2 leaves) is closed. */
/* What stock's UI (or a package or the menu in its place) writes, for the boot log (owner's
   player, 2026-10-05/06: stock's UI died at every start after the menu in two boots, and keeps no
   log of its own). Stock's player keeps its own (fiio_player.log), and its output stays on the
   console, where the emulator waits for its network thread's line. Platform mode only: the UI's
   output goes to RUN_DIR/out, a tmpfs of its own, so it can never
   take more than 1 MiB of RAM (a full one refuses writes with ENOSPC, which no program dies of);
   the kernel prints the fatal signals of user programs. */
static void capture_prepare(void) {
    char p[PATH_MAX];
    bpath(p, RUN_DIR "/out");
    if (mkdirs(p, 0755)) return;
#if defined(__linux__) && !defined(DISC_BOOT_FIXTURE)
    if (mount("disc-boot-out", p, "tmpfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, "size=1m,mode=0755")) blog("capture: %s", strerror(errno));
    int fd = open("/proc/sys/kernel/print-fatal-signals", O_WRONLY | O_CLOEXEC);
    if (fd >= 0) { if (write(fd, "1\n", 2) != 2) blog("print-fatal-signals: %s", strerror(errno)); close(fd); }
#endif
}

/* RUN_DIR/out is a file system of its own (the bound); the fixture has no mounts. */
static int capture_bounded(void) {
    char out[PATH_MAX], run[PATH_MAX];
    struct stat a, b;
    bpath(out, RUN_DIR "/out");
    bpath(run, RUN_DIR);
    if (lstat(out, &a) || !S_ISDIR(a.st_mode) || lstat(run, &b)) return 0;
#ifdef DISC_BOOT_FIXTURE
    return 1;
#else
    return a.st_dev != b.st_dev;
#endif
}

/* Each line of a text in the boot log, after a prefix: at most `lines` of them, the last ones. */
static void plog_lines(const char *prefix, char *text, int lines) {
    int count = 0;
    for (char *c = text; *c; c++) if (*c == '\n') count++;
    char *line = text;
    for (int k = 0; *line; k++) {
        char *end = strchr(line, '\n');
        if (end) *end = 0;
        if (k >= count - lines && *line) {
            for (char *c = line; *c; c++) if ((unsigned char)*c < ' ' && *c != '\t') *c = ' ';
            plog("%s%.200s", prefix, line);
        }
        if (!end) break;
        line = end + 1;
    }
}

/* A process's name and state for the boot log ("mq_ui S"), or "?" when it is gone. */
static void proc_brief(long pid, char *out, size_t cap) {
    char p[PATH_MAX], buf[512];
    size_t n = 0;
    snprintf(out, cap, "?");
    bpath(p, "/proc/%ld/stat", pid);
    int fd = open(p, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return;
    ssize_t r = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (r <= 0) return;
    n = (size_t)r; buf[n] = 0;
    char *open_paren = strchr(buf, '('), *close_paren = strrchr(buf, ')');
    if (!open_paren || !close_paren || close_paren < open_paren || close_paren[1] != ' ') return;
    *close_paren = 0;
    snprintf(out, cap, "%.20s %c", open_paren + 1, close_paren[2]);
}

/* Stock's pair locks /usr/data/fiio/process_lock.txt with flock (util.c's process_lock_segment,
   a blocking LOCK_EX): who holds or waits for it, and every process in uninterruptible sleep (a
   holder stuck on I/O keeps the lock past the pair's restarts). */
static void capture_locks(void) {
    char p[PATH_MAX], line[256], who[40];
    struct stat lock;
    bpath(p, "/usr/data/fiio/process_lock.txt");
    if (!stat(p, &lock)) {
        bpath(p, "/proc/locks");
        FILE *f = fopen(p, "re");
        if (f) {
            char inode[32];
            snprintf(inode, sizeof(inode), ":%llu ", (unsigned long long)lock.st_ino);
            for (int k = 0; k < 64 && fgets(line, sizeof(line), f); k++) {
                if (!strstr(line, inode)) continue;
                long pid = 0;
                char kind[16] = "", *c = line;
                while (*c && *c != ' ') c++;                 /* "1:" */
                while (*c == ' ') c++;
                if (!strncmp(c, "-> ", 3)) { snprintf(kind, sizeof(kind), "waits"); c += 3; } else snprintf(kind, sizeof(kind), "holds");
                if (sscanf(c, "%*s %*s %*s %ld", &pid) == 1) {
                    proc_brief(pid, who, sizeof(who));
                    plog("process_lock %s by %ld (%s)", kind, pid, who);
                }
            }
            fclose(f);
        }
    }
    bpath(p, "/proc");
    DIR *d = opendir(p);
    if (!d) return;
    struct dirent *e;
    int stuck = 0;
    while ((e = readdir(d)) && stuck < 8) {
        char *end;
        long pid = strtol(e->d_name, &end, 10);
        if (*end || pid <= 0) continue;
        proc_brief(pid, who, sizeof(who));
        size_t len = strlen(who);
        if (len > 2 && who[len - 1] == 'D') { plog("uninterruptible %ld (%s)", pid, who); stuck++; }
    }
    closedir(d);
}

/* At each start of the pair in platform mode: the last lines the previous program of this name
   wrote, the kernel's fatal-signal lines since the last look and stock's queues into the boot
   log; then this program writes to a fresh file. Every failure leaves the program's output as
   it was. */
static void capture_start(const char *name) {
    char out[PATH_MAX], said[PATH_MAX], text[1024];
    if (!capture_bounded()) return;
    bpath(out, RUN_DIR "/out/%s.log", name);
    int fd = open(out, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd >= 0) {
        off_t size = lseek(fd, 0, SEEK_END), from = size > 900 ? size - 900 : 0;
        ssize_t n = pread(fd, text, sizeof(text) - 1, from);
        close(fd);
        if (n > 0) {
            text[n] = 0;
            snprintf(said, sizeof(said), "%s before said: ", name);
            char *start = from && strchr(text, '\n') ? strchr(text, '\n') + 1 : text;
            plog_lines(said, start, 6);
        }
        unlink(out);
    }
#if defined(__linux__) && !defined(DISC_BOOT_FIXTURE)
    {
        int len = klogctl(10, NULL, 0);   /* SYSLOG_ACTION_SIZE_BUFFER */
        char *ring = len > 0 && len <= (1 << 20) ? malloc((size_t)len + 1) : NULL;
        int got = ring ? klogctl(3, ring, len) : -1;   /* SYSLOG_ACTION_READ_ALL */
        if (got > 0) {
            ring[got] = 0;
            char seen[PATH_MAX], buf[32];
            bpath(seen, RUN_DIR "/out/fatal-seen");
            long before = read_small(seen, buf, sizeof(buf), NULL) ? 0 : atol(buf), total = 0;
            for (char *line = ring; line && *line; ) {
                char *end = strchr(line, '\n');
                if (end) *end = 0;
                if (strstr(line, "fatal signal")) {
                    if (++total > before) plog("kernel: %.200s", line);
                }
                line = end ? end + 1 : NULL;
            }
            int k = snprintf(buf, sizeof(buf), "%ld\n", total);
            write_atomic(seen, buf, (size_t)k, 0644);
        }
        free(ring);
    }
#endif
    {
        char q[PATH_MAX], line[256];
        bpath(q, "/dev/mqueue");
        DIR *d = opendir(q);
        if (d) {
            struct dirent *e;
            while ((e = readdir(d))) {
                if (e->d_name[0] == '.') continue;
                bpath(q, "/dev/mqueue/%s", e->d_name);
                size_t n = 0;
                if (read_small(q, line, sizeof(line), &n)) n = 0;
                line[n] = 0;
                for (char *c = line; *c; c++) if (*c == '\n' || *c == '\t') *c = ' ';
                plog("mqueue %s %.120s", e->d_name, line);
            }
            closedir(d);
        }
    }
    capture_locks();
    fd = open(out, O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW | O_CLOEXEC, 0644);
    if (fd < 0) return;
    if (dup2(fd, 1) < 0 || dup2(fd, 2) < 0) blog("capture %s: %s", name, strerror(errno));
    close(fd);
}

static void only_standard_descriptors(void) {
    long most = sysconf(_SC_OPEN_MAX);
    if (most < 0 || most > 4096) most = 4096;
    for (int fd = 3; fd < most; fd++) close(fd);
}

static pid_t spawn_service(const manifest *m, char slot) {
    char entry[PATH_MAX], data[PATH_MAX], log[PATH_MAX], ready[PATH_MAX];
    bpath(entry, DATA_DIR "/service/%c/%s", slot, m->entry);
    bpath(data, DATA_DIR "/data/%s", m->name);
    bpath(log, RUN_DIR "/service/log");
    bpath(ready, RUN_DIR "/service/ready");
    char **envp = package_env("service", m, slot, 0);
    char **argv = package_argv(entry, m);
    unlink(ready);
    pid_t pid = fork();
    if (pid == 0) {
        setsid();
        setpriority(PRIO_PROCESS, 0, 5);
        signal(SIGTERM, SIG_DFL);
        int in = open("/dev/null", O_RDONLY), out = open(log, O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW, 0644);
        if (in >= 0) dup2(in, 0);
        if (out >= 0) { dup2(out, 1); dup2(out, 2); }
        only_standard_descriptors();
        if (chdir(data)) _exit(126);
        execve(entry, argv, envp);
        _exit(127);
    }
    for (char **e = envp; *e; e++) free(*e);
    free(envp); free(argv);
    return pid;
}

static void stop_child(pid_t pid) {
    kill(-pid, SIGTERM); kill(pid, SIGTERM);
    double until = mono() + t_grace;
    while (mono() < until) {
        if (waitpid(pid, NULL, WNOHANG) == pid) { kill(-pid, SIGKILL); return; }
        pause_s(0.05);
    }
    kill(-pid, SIGKILL); kill(pid, SIGKILL);
    waitpid(pid, NULL, 0);
}

static void cap_log(const char *role) {
    char p[PATH_MAX];
    struct stat s;
    bpath(p, RUN_DIR "/%s/log", role);
    if (!lstat(p, &s) && S_ISREG(s.st_mode) && s.st_size > LOG_CAP && truncate(p, 0)) blog("cannot cap %s", p);
}

/* The service role: ready, confirmed, restarted within bounds, rolled back (contract, "Lifecycle"). */
static void supervise_service(void) {
    manifest *m = malloc(sizeof(*m));
    if (!m) return;
    int failures = 0, nrestarts = 0;
    double restarts[RESTARTS + 2];
    char err[200], ready[PATH_MAX];
    bpath(ready, RUN_DIR "/service/ready");
    while (!stopping) {
        role_state rs;
        if (rstate_read("service", &rs)) { role_status("service", "failed", NULL, NULL, failures, "the role's state is unreadable"); break; }
        if (!rs.current) { role_status("service", "absent", NULL, NULL, failures, NULL); break; }
        if (slot_check("service", rs.current, m, err, sizeof(err))) {
            blog("service slot %c refused: %s", rs.current, err);
            if (!rs.confirmed && !rollback("service", &rs)) { role_status("service", "rolled-back", NULL, &rs, failures, err); continue; }
            role_status("service", "failed", NULL, &rs, failures, err);
            break;
        }
        pid_t child = spawn_service(m, rs.current);
        if (child < 0) { role_status("service", "failed", m, &rs, failures, "fork failed"); break; }
        role_status("service", "starting", m, &rs, failures, NULL);
        double started = mono(), ready_at = 0;
        int is_ready = 0, confirmed_now = 0, exited = 0, timed_out = 0;
        while (!exited) {
            if (stopping) { stop_child(child); role_status("service", "stopped", m, &rs, failures, NULL); free(m); return; }
            if (waitpid(child, NULL, WNOHANG) == child) { exited = 1; break; }
            if (!is_ready && exists(ready)) { is_ready = 1; ready_at = mono(); role_status("service", "ready", m, &rs, failures, NULL); }
            if (!is_ready && mono() - started > m->ready) { stop_child(child); exited = timed_out = 1; break; }
            if (is_ready && !confirmed_now && mono() - ready_at >= t_confirm) {
                confirm("service"); confirmed_now = 1; rs.confirmed = 1;
                role_status("service", "confirmed", m, &rs, failures, NULL);
            }
            cap_log("service");
            pause_s(0.1);
        }
        if (handle_request("service", m->name)) { failures = 0; nrestarts = 0; continue; }
        failures++;
        const char *why = timed_out ? "not ready in time" : "exited";
        if (rstate_read("service", &rs)) continue;
        if (!rs.confirmed) {
            if (!rollback("service", &rs)) { role_status("service", "rolled-back", m, &rs, failures, why); nrestarts = 0; continue; }
            role_status("service", "failed", m, &rs, failures, timed_out ? "not ready in time before its confirmation" : "exited before its confirmation");
            break;
        }
        double now = mono();
        int kept = 0;
        for (int k = 0; k < nrestarts; k++) if (now - restarts[k] < t_window) restarts[kept++] = restarts[k];
        nrestarts = kept;
        if (nrestarts >= RESTARTS) { role_status("service", "failed", m, &rs, failures, "restarted too often"); break; }
        restarts[nrestarts++] = now;
        role_status("service", "restarting", m, &rs, failures, why);
        pause_s(t_backoff);
    }
    free(m);
}

static int card_mounted(void) {
    char p[PATH_MAX], line[512], dev[200], target[260];
    bpath(p, "/proc/mounts");
    FILE *f = fopen(p, "r");
    if (!f) return 0;
    int ok = 0;
    while (!ok && fgets(line, sizeof(line), f))
        ok = sscanf(line, "%199s %259s", dev, target) == 2 && !strcmp(dev, card_source) && !strcmp(target, card);
    fclose(f);
    return ok;
}

/* The installation's progress (contract, "Recovery from the card"), which the menu shows and the
   launchers wait on: waiting (for the card), installing (done of total, the current package), done. */
static void install_progress(const char *state, int done, int total, const char *current) {
    char p[PATH_MAX], buf[400], quoted[80] = "null";
    if (current) json_str(quoted, sizeof(quoted), current);
    int n = snprintf(buf, sizeof(buf), "{\"schema\":1,\"state\":\"%s\",\"done\":%d,\"total\":%d,\"current\":%s}\n",
                     state, done, total, quoted);
    bpath(p, RUN_DIR);
    mkdirs(p, 0755);
    bpath(p, RUN_DIR "/install.json");
    if (n > 0 && n < (int)sizeof(buf)) write_atomic(p, buf, (size_t)n, 0644);
}

/* A start with Play whose installation has not finished (or not begun). */
static int install_pending(void) {
    if (strcmp(reason, "recovery")) return 0;
    char p[PATH_MAX], buf[400], state[16] = "";
    size_t len;
    bpath(p, RUN_DIR "/install.json");
    if (read_small(p, buf, sizeof(buf), &len)) return 1;
    bjson j;
    if (bjson_parse(&j, buf, len, 16)) return 1;
    bjson_string(&j, bjson_find(&j, 0, "state"), state, sizeof(state));
    bjson_free(&j);
    return strcmp(state, "done") != 0;
}

/* A launcher holds its start until the installation is done (owner, 2026-10-07: the first answer
   no longer races the card), at most t_install. */
static void install_wait(const char *who) {
    if (!install_pending()) return;
    plog("%s waits for the installation from the card", who);
    double until = mono() + t_install;
    while (install_pending() && mono() < until && !stopping) pause_s(0.2);
    if (install_pending()) plog("%s: the installation did not finish in %.0f s", who, t_install);
}

/* Where the staged packages are read: the card where stock mounted it, else a mount of the boot
   layer's own of the same device (stock mounts it only once its player runs, which waits for the
   installation; one superblock, so stock's own mount later is unaffected), at most t_card. */
static int card_for_install(char *root, size_t cap, int *own) {
    *own = 0;
    double until = mono() + t_card;
    for (;;) {
        if (card_mounted()) { snprintf(root, cap, "%s", card); return 0; }
#if defined(__linux__) && !defined(DISC_BOOT_FIXTURE)
        char dir[PATH_MAX];
        bpath(dir, RUN_DIR "/card");
        mkdirs(dir, 0755);
        static const char *const types[] = {"vfat", "exfat"};
        for (size_t k = 0; k < sizeof(types) / sizeof(*types); k++)
            if (!mount(card_source, dir, types[k], MS_NOSUID | MS_NODEV | MS_NOEXEC, "iocharset=utf8")) {
                *own = 1;
                snprintf(root, cap, "%s", RUN_DIR "/card");
                plog("recovery: the card mounted for the installation (%s)", types[k]);
                return 0;
            }
#elif defined(DISC_BOOT_FIXTURE)
        /* The fixture's stand-in for the boot layer's own mount: the card's folder as it is. */
        if (getenv("DISC_BOOT_FIXTURE_MOUNTABLE")) { *own = 1; snprintf(root, cap, "%s", card); return 0; }
#endif
        if (mono() >= until || stopping) return -1;
        pause_s(0.5);
    }
}

static int install(const char *domain, const char *staged, char *note, size_t cap) {
    manifest *m = malloc(sizeof(*m));
    char err[200], slot[PATH_MAX], src[PATH_MAX], dst[PATH_MAX];
    int r = -1, lock = -1;
    if (!m) { snprintf(note, cap, "refused: out of memory"); return -1; }
    if (manifest_load(staged, m, err, sizeof(err)) || manifest_fits(m, role_of(domain), profile, err, sizeof(err)) || package_verify(staged, m, 0, err, sizeof(err))) {
        snprintf(note, cap, "refused: %s", err); goto out;
    }
    if (ui_name(domain) && strcmp(m->name, ui_name(domain))) { snprintf(note, cap, "refused: the folder is named %s, the package %s", ui_name(domain), m->name); goto out; }
    struct statvfs v;
    bpath(slot, DATA_DIR);
    mkdirs(slot, 0755);
    if (statvfs(slot, &v) || (long long)v.f_bavail * (long long)v.f_frsize < m->total + RESERVE) { snprintf(note, cap, "refused: not enough room in /usr/data"); goto out; }
    lock = state_lock();
    role_state rs;
    if (rstate_read(domain, &rs)) { snprintf(note, cap, "refused: the role's state is unreadable"); goto out; }
    char target = rs.current ? other(rs.current) : 'a';
    bpath(slot, DATA_DIR "/%s/%c", domain, target);
    if (remove_tree(slot) || mkdirs(slot, 0755)) { snprintf(note, cap, "refused: cannot prepare the slot"); goto out; }
    if (snprintf(src, sizeof(src), "%s/package.json", staged) >= (int)sizeof(src) || snprintf(dst, sizeof(dst), "%s/package.json", slot) >= (int)sizeof(dst)
        || copy_file(src, dst, 0644)) { snprintf(note, cap, "refused: cannot copy package.json"); goto out; }
    for (int f = 0; f < m->nfiles; f++) {
        if (snprintf(src, sizeof(src), "%s/%s", staged, m->files[f].path) >= (int)sizeof(src)
            || snprintf(dst, sizeof(dst), "%s/%s", slot, m->files[f].path) >= (int)sizeof(dst)) { snprintf(note, cap, "refused: path too long"); goto out; }
        char parent[PATH_MAX]; snprintf(parent, sizeof(parent), "%s", dst);
        *strrchr(parent, '/') = 0;
        if (mkdirs(parent, 0755) || copy_file(src, dst, (mode_t)m->files[f].mode)) { snprintf(note, cap, "refused: cannot copy %s", m->files[f].path); goto out; }
    }
    if (package_verify(slot, m, 1, err, sizeof(err))) { remove_tree(slot); snprintf(note, cap, "refused: the copy differs: %s", err); goto out; }
    make_current(domain, &rs, target);
    if (rstate_write(domain, &rs)) { snprintf(note, cap, "refused: cannot record the slot"); goto out; }
    remove_tree(staged);
    snprintf(note, cap, "installed %s %s", m->name, m->version);
    r = 0;
out:
    state_unlock(lock);
    free(m);
    return r;
}

/* Stock's running UI, found as stock's own watch loop finds it (pgrep -x mq_ui): by the process
   name, so the UI stock started itself (the card, and with it the package, came after it) and one
   the launcher started are both stopped; the watch loop then starts the launcher. */
static void stop_running_ui(void) {
    char dir[PATH_MAX];
    bpath(dir, "/proc");
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d))) {
        char *end, p[PATH_MAX], comm[32];
        long pid = strtol(e->d_name, &end, 10);
        if (*end || pid <= 1 || pid == (long)getpid()) continue;
        bpath(p, "/proc/%ld/comm", pid);
        FILE *f = fopen(p, "r");
        if (!f) continue;
        int match = fgets(comm, sizeof(comm), f) && !strcmp(comm, "mq_ui\n");
        fclose(f);
        if (match) { plog("stock's UI %ld stopped for the installed UI", pid); kill((pid_t)pid, SIGTERM); }
    }
    closedir(d);
}

/* One role's line of result.json. */
static int add_result(char *out, size_t cap, size_t *o, int first, const char *key, int ok, const char *note) {
    char quoted[300];
    json_str(quoted, sizeof(quoted), note);
    if (*o >= cap) return -1;
    *o += (size_t)snprintf(out + *o, cap - *o, "%s\"%s\":{\"installed\":%s,\"note\":%s}", first ? "" : ",", key, ok ? "true" : "false", quoted);
    return *o < cap ? 0 : -1;
}

static int name_order(const void *a, const void *b) { return strcmp((const char *)a, (const char *)b); }

/* Play held at power-on: the packages staged on the card (contract, "Recovery from the card"):
   install/service/, install/menu/ and one folder per ui package, install/ui/<name>/. It runs before
   the pair (the launchers wait for it, the menu shows its progress), from the card where stock
   mounted it or from a mount of the boot layer's own, and every step reaches the boot log. */
static void recovery(void) {
    install_progress("waiting", 0, 0, NULL);
    char root[PATH_MAX], result[SMALL_FILE], dir[PATH_MAX], note[260];
    int own = 0;
    if (card_for_install(root, sizeof(root), &own)) {
        plog("recovery: the card is not mounted");
        install_progress("done", 0, 0, NULL);
        return;
    }
    static const char *SINGLE[] = {"service", "menu"};
    char names[MAX_UIS][33];
    int count = 0, loose = 0, total = 0, done = 0;
    for (size_t k = 0; k < sizeof(SINGLE) / sizeof(*SINGLE); k++) {
        bpath(dir, "%s/.disc/boot/install/%s", root, SINGLE[k]);
        total += is_dir(dir);
    }
    bpath(dir, "%s/.disc/boot/install/ui", root);
    DIR *d = is_dir(dir) ? opendir(dir) : NULL;
    if (d) {
        struct dirent *e;
        while ((e = readdir(d))) {
            if (!strcmp(e->d_name, "package.json")) loose = 1;
            if (!package_name_ok(e->d_name) || count >= MAX_UIS) continue;
            char sub[PATH_MAX];
            bpath(sub, "%s/.disc/boot/install/ui/%s", root, e->d_name);
            if (is_dir(sub)) snprintf(names[count++], sizeof(names[0]), "%.32s", e->d_name);
        }
        closedir(d);
        qsort(names, (size_t)count, sizeof(names[0]), name_order);
    }
    total += count;
    plog("recovery: %d staged on the card", total);
    size_t o = (size_t)snprintf(result, sizeof(result), "{\"schema\":1,\"roles\":{");
    int any = 0, ui_installed_now = 0, menu_installed = 0;
    for (size_t k = 0; k < sizeof(SINGLE) / sizeof(*SINGLE); k++) {
        bpath(dir, "%s/.disc/boot/install/%s", root, SINGLE[k]);
        if (!is_dir(dir)) continue;
        install_progress("installing", done, total, SINGLE[k]);
        int ok = install(SINGLE[k], dir, note, sizeof(note)) == 0;
        done++;
        if (ok && !strcmp(SINGLE[k], "menu")) menu_installed = 1;
        add_result(result, sizeof(result), &o, !any, SINGLE[k], ok, note);
        any = 1;
        plog("recovery %s: %s", SINGLE[k], note);
    }
    if (d) {
        char first_ui[33] = "";
        if (o < sizeof(result)) o += (size_t)snprintf(result + o, sizeof(result) - o, "%s\"ui\":{", any ? "," : "");
        any = 1;
        int first = 1;
        if (loose) {
            add_result(result, sizeof(result), &o, first, "package.json", 0, "refused: stage a ui package in install/ui/<its name>/");
            first = 0;
        }
        for (int k = 0; k < count; k++) {
            char domain[40], staged[PATH_MAX];
            ui_domain(domain, names[k]);
            bpath(staged, "%s/.disc/boot/install/ui/%s", root, names[k]);
            install_progress("installing", done, total, names[k]);
            int ok = install(domain, staged, note, sizeof(note)) == 0;
            done++;
            if (ok && !first_ui[0]) snprintf(first_ui, sizeof(first_ui), "%.32s", names[k]);
            if (ok) ui_installed_now = 1;
            add_result(result, sizeof(result), &o, first, names[k], ok, note);
            first = 0;
            plog("recovery ui %s: %s", names[k], note);
        }
        if (o < sizeof(result)) o += (size_t)snprintf(result + o, sizeof(result) - o, "}");
        if (first_ui[0]) {
            /* The first ui package installed becomes the default when there is none. */
            int lock = state_lock();
            global_state g;
            if (!gstate_read(&g) && !g.ui[0]) { snprintf(g.ui, sizeof(g.ui), "%s", first_ui); gstate_write(&g); }
            state_unlock(lock);
        }
    }
    if (o < sizeof(result)) o += (size_t)snprintf(result + o, sizeof(result) - o, "}}\n");
    bpath(dir, "%s/.disc/boot", root);
    if (o < sizeof(result) && mkdirs(dir, 0755) == 0) {
        bpath(dir, "%s/.disc/boot/result.json", root);
        write_atomic(dir, result, o, 0644);
    }
    if (ui_installed_now || menu_installed) {
        /* This boot's choice again, with what was installed, and what a running menu offers. A
           launcher that waited goes on with it; a UI that started before the installation (stock
           mounted the card first) is stopped, so that stock's watch loop restarts the launcher. */
        char p[PATH_MAX];
        ui_choice c;
        int run = decide_ui(0);
        if (!read_choice(&c)) write_choices(&c);
        bpath(p, RUN_DIR "/ui/install-wait");
        if (run && !exists(p)) {
            bpath(p, RUN_DIR "/ui-launch");
            write_atomic(p, "ui\n", 3, 0644);
            stop_running_ui();
        }
    }
#if defined(__linux__) && !defined(DISC_BOOT_FIXTURE)
    if (own) {
        sync();
        bpath(dir, RUN_DIR "/card");
        if (umount(dir)) plog("recovery: the card's own mount stays: %s", strerror(errno));
    }
#else
    (void)own;
#endif
    install_progress("done", done, total, NULL);
    plog("recovery: done, %d of %d", done, total);
}

static int read_boot(void) {
    char p[PATH_MAX], buf[SMALL_FILE], cardv[PATH_MAX], source[128];
    size_t len;
    bpath(p, RUN_DIR "/boot.json");
    if (read_small(p, buf, sizeof(buf), &len)) return -1;
    bjson j;
    if (bjson_parse(&j, buf, len, 64)) return -1;
    int bad = bjson_string(&j, bjson_find(&j, 0, "mode"), mode, sizeof(mode)) || bjson_string(&j, bjson_find(&j, 0, "reason"), reason, sizeof(reason))
        || bjson_string(&j, bjson_find(&j, 0, "profile"), profile, sizeof(profile))
        || bjson_string(&j, bjson_find(&j, 0, "card"), cardv, sizeof(cardv)) || bjson_string(&j, bjson_find(&j, 0, "cardSource"), source, sizeof(source));
    bjson_free(&j);
    if (bad || !abs_path_ok(cardv) || !abs_path_ok(source)) return -1;
    snprintf(card, sizeof(card), "%s", cardv);
    snprintf(card_source, sizeof(card_source), "%s", source);
    return 0;
}

static int cmd_early(void) {
    global_state g;
    int readable = gstate_read(&g) == 0;
    int volume_up = 0, play = 0, keys = read_keys(&volume_up, &play) == 0;
    const char *chosen = g.mode, *why = "default";
    if (keys && play) { chosen = "platform"; why = "recovery"; }
    else if (keys && volume_up) { chosen = strcmp(g.mode, "platform") ? "platform" : "stock"; why = "key"; }
    else if (!strcmp(g.mode, "platform") && g.unconfirmed >= BOOT_LOOP) { chosen = "stock"; why = "boot-loop"; }
    char p[PATH_MAX], buf[1024], cardq[260], sourceq[160];
    bpath(p, RUN_DIR);
    mkdirs(p, 0755);
    int platform = !strcmp(chosen, "platform"), launch = 0;
    if (platform) capture_prepare();
    /* This boot's UI. Nothing of a package runs yet, so what packages asked about the choice applies first. */
    /* With Play the launcher runs whatever is installed: it waits for the installation from the card
       and then starts what it chose, stock's UI included (owner, 2026-10-07). */
    if (platform) { apply_pending(); launch = decide_ui(1) || !strcmp(why, "recovery"); }
    else { bpath(p, RUN_DIR "/ui/choice.json"); unlink(p); }
    role_state rs;
    int installed = launch || (!rstate_read("service", &rs) && rs.current);
    int count = g.unconfirmed;
    if (platform && (installed || !strcmp(why, "recovery"))) {
        int lock = state_lock();
        global_state now;
        if (gstate_read(&now)) now = g;
        now.unconfirmed++;
        gstate_write(&now);
        state_unlock(lock);
    }
    /* The permission /sbin/mq_ui reads: without it stock's UI starts, the boot program out of its way. */
    bpath(p, RUN_DIR "/ui-launch");
    if (launch) write_atomic(p, "ui\n", 3, 0644);
    else unlink(p);
    bpath(p, RUN_DIR "/boot.json");
    json_str(cardq, sizeof(cardq), card);
    json_str(sourceq, sizeof(sourceq), card_source);
    int n = snprintf(buf, sizeof(buf),
        "{\"schema\":1,\"api\":%d,\"build\":\"%s\",\"profile\":\"%s\",\"mode\":\"%s\",\"reason\":\"%s\","
        "\"keys\":{\"read\":%s,\"volumeUp\":%s,\"play\":%s},\"unconfirmedBoots\":%d,\"stateReadable\":%s,\"card\":%s,\"cardSource\":%s}\n",
        BOOT_API, DISC_BUILD, profile, chosen, why, keys ? "true" : "false", volume_up ? "true" : "false", play ? "true" : "false",
        count, readable ? "true" : "false", cardq, sourceq);
    write_atomic(p, buf, (size_t)n, 0644);
    blog("boot mode %s (%s)", chosen, why);
    return 0;
}

static int cmd_start(void) {
    if (read_boot()) { blog("start: no boot decision"); return 0; }
    if (strcmp(mode, "platform")) { role_status("service", "stock-mode", NULL, NULL, 0, NULL); return 0; }
    pid_t first = fork();
    if (first < 0) return 0;
    if (first > 0) { waitpid(first, NULL, 0); return 0; }
    setsid();
    if (fork() != 0) _exit(0);
    char p[PATH_MAX], buf[32];
    bpath(p, RUN_DIR "/boot.log");
    int log = open(p, O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW, 0644), in = open("/dev/null", O_RDONLY);
    if (in >= 0) dup2(in, 0);
    if (log >= 0) { dup2(log, 1); dup2(log, 2); }
    only_standard_descriptors();
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    bpath(p, RUN_DIR "/supervisor.pid");
    int n = snprintf(buf, sizeof(buf), "%ld\n", (long)getpid());
    write_atomic(p, buf, (size_t)n, 0644);
    if (!strcmp(reason, "recovery")) recovery();
    supervise_service();
    bpath(p, RUN_DIR "/supervisor.pid");
    unlink(p);
    _exit(0);
}

static int cmd_stop(void) {
    char p[PATH_MAX], buf[32];
    bpath(p, RUN_DIR "/supervisor.pid");
    if (read_small(p, buf, sizeof(buf), NULL)) return 0;
    long pid = atol(buf);
    if (pid <= 1 || kill((pid_t)pid, SIGTERM)) return 0;
    double until = mono() + t_grace + 3;
    while (mono() < until && kill((pid_t)pid, 0) == 0) pause_s(0.1);
    return 0;
}

static void print_file(const char *name) {
    char p[PATH_MAX], buf[STATUS_BYTES];
    size_t len;
    bpath(p, RUN_DIR "/%s", name);
    if (read_small(p, buf, sizeof(buf), &len)) { fputs("null", stdout); return; }
    while (len && (buf[len - 1] == '\n')) buf[--len] = 0;
    fputs(buf, stdout);
}

static int cmd_status(void) {
    fputs("{\"boot\":", stdout); print_file("boot.json");
    fputs(",\"service\":", stdout); print_file("service.json");
    fputs(",\"ui\":", stdout); print_file("ui.json");
    fputs(",\"menu\":", stdout); print_file("menu.json");
    fputs(",\"choice\":", stdout); print_file("ui/choice.json");
    fputs(",\"player\":", stdout); print_file("ui/player.json");
    fputs("}\n", stdout);
    return 0;
}

static int cmd_verify(const char *role, const char *dir) {
    manifest *m = malloc(sizeof(*m));
    char err[200], quoted[260];
    if (!m) return 2;
    if (!profile[0]) read_boot();
    int ok = strcmp(role, "service") && strcmp(role, "ui") && strcmp(role, "menu") ? (snprintf(err, sizeof(err), "role must be service, ui or menu"), 0)
           : !(manifest_load(dir, m, err, sizeof(err)) || manifest_fits(m, role, profile, err, sizeof(err)) || package_verify(dir, m, 1, err, sizeof(err)));
    if (ok) {
        char name[80], version[80];
        json_str(name, sizeof(name), m->name); json_str(version, sizeof(version), m->version);
        printf("{\"ok\":true,\"name\":%s,\"version\":%s,\"bytes\":%lld}\n", name, version, m->total);
    } else { json_str(quoted, sizeof(quoted), err); printf("{\"ok\":false,\"error\":%s}\n", quoted); }
    free(m);
    return ok ? 0 : 1;
}

/* The ui role's watcher: ready, then confirmed while the UI keeps running (contract, "The ui role"). */
/* The UI's process lives on under its name; a pid taken by another program does not count. */
static int ui_alive(pid_t pid) {
    if (kill(pid, 0)) return 0;
#if defined(__linux__) && !defined(DISC_BOOT_FIXTURE)
    char p[64], comm[32];
    snprintf(p, sizeof(p), "/proc/%ld/comm", (long)pid);
    FILE *f = fopen(p, "r");
    if (!f) return 1;
    int ok = fgets(comm, sizeof(comm), f) && !strcmp(comm, "mq_ui\n");
    fclose(f);
    return ok;
#else
    return 1;
#endif
}

static void ui_watch(pid_t ui, const char *domain, const manifest *m, role_state rs) {
    char ready[PATH_MAX];
    bpath(ready, RUN_DIR "/ui/ready");
    double until = mono() + m->ready;
    while (!exists(ready)) {
        if (!ui_alive(ui)) return;
        if (mono() > until) { role_status(domain, "not-ready", m, &rs, 0, "not ready in time"); kill(ui, SIGKILL); return; }
        pause_s(0.1);
    }
    role_status(domain, "ready", m, &rs, 0, NULL);
    until = mono() + t_confirm;
    while (mono() < until) { if (!ui_alive(ui)) return; pause_s(0.1); }
    confirm(domain);
    rs.confirmed = 1;
    char p[PATH_MAX];
    bpath(p, RUN_DIR "/ui/starts");
    unlink(p);
    role_status(domain, "confirmed", m, &rs, 0, NULL);
}

static void exec_stock(char **argv) {
    char stock[PATH_MAX];
    plog("mq_ui: stock's UI");
    bpath(stock, "/usr/bin/mq_ui");
    argv[0] = "mq_ui";
    execv(stock, argv);
    _exit(127);
}

/* Counts this start among the recent ones; returns how many came before within the window. */
static int count_start(void) {
    char p[PATH_MAX], buf[SMALL_FILE], out[SMALL_FILE];
    double times[8], now = mono();
    int n = 0, before = 0, o = 0;
    bpath(p, RUN_DIR "/ui/starts");
    if (!read_small(p, buf, sizeof(buf), NULL))
        for (char *s = strtok(buf, "\n"); s && n < 7; s = strtok(NULL, "\n")) times[n++] = atof(s);
    for (int k = 0; k < n; k++) if (now - times[k] < t_ui_window) { before++; o += snprintf(out + o, sizeof(out) - (size_t)o, "%.3f\n", times[k]); }
    o += snprintf(out + o, sizeof(out) - (size_t)o, "%.3f\n", now);
    write_atomic(p, out, (size_t)o, 0644);
    return before;
}

/* Whether a player already ran in this boot. Stock's player starts the hardware watchdog (10 s)
   and it keeps running across a restart of the pair; before the first player of a boot nothing
   runs it. The wrapper's own start of stock's player marks it too. */
static int player_ran(void) {
    char p[PATH_MAX];
    bpath(p, RUN_DIR "/player-ran");
    return exists(p);
}

static void mark_player_ran(void) {
    char p[PATH_MAX];
    bpath(p, RUN_DIR "/player-ran");
    write_atomic(p, "\n", 1, 0644);
}

/* The menu's failures in this boot, after adding add. */
static int menu_failures(int add) {
    char p[PATH_MAX], buf[32];
    bpath(p, RUN_DIR "/menu/failures");
    int n = read_small(p, buf, sizeof(buf), NULL) ? 0 : atoi(buf);
    if (add) {
        n += add;
        int len = snprintf(buf, sizeof(buf), "%d\n", n);
        write_atomic(p, buf, (size_t)len, 0644);
    }
    return n;
}

/* The menu's time is bounded: unanswered after t_menu seconds, it is stopped and the default runs. */
static void menu_watch(pid_t menu) {
    char answer[PATH_MAX], started[PATH_MAX];
    bpath(answer, RUN_DIR "/menu/choice");
    bpath(started, RUN_DIR "/menu/started");
    double until = mono() + t_menu;
    ui_choice c;
    while (mono() < until) {
        if (!ui_alive(menu) || exists(answer) || read_choice(&c) || !c.menu) return;
        /* While an installation from the card runs, the menu shows it and asks nothing yet. */
        if (install_pending()) until = mono() + t_menu;
        pause_s(0.1);
    }
    if (exists(answer) || read_choice(&c) || !c.menu) return;
    c.menu = 0;
    snprintf(c.note, sizeof(c.note), "the menu did not answer in time");
    write_choice(&c);
    unlink(started);
    int failures = menu_failures(1);
    role_state rs;
    rstate_read("menu", &rs);
    role_status("menu", "failed", NULL, &rs, failures, "did not answer in time");
    kill(menu, SIGKILL);
}

/* Off, as stock's UI switches the player off (`poweroff -f`): the disks synced, no init scripts. */
static void power_off(void) {
    sync();
#if defined(__linux__) && !defined(DISC_BOOT_FIXTURE)
    reboot(RB_POWER_OFF);
#else
    char p[PATH_MAX];
    bpath(p, RUN_DIR "/poweroff");
    write_atomic(p, "\n", 1, 0644);
#endif
    _exit(0);
}

/* The menu's turn (contract, "The menu's turn"). The menu runs in the UI's place and exits with
   its answer; stock's watch loop then restarts the pair, and at this next start the answer
   settles the boot's choice. Returns when the choice is settled; otherwise runs the menu. */
static int menu_turn(ui_choice *c, manifest *m, char **argv) {
    char answer[PATH_MAX], started[PATH_MAX], p[PATH_MAX], err[200] = "", entry[PATH_MAX];
    role_state rs;
    bpath(p, RUN_DIR "/menu");
    mkdirs(p, 0755);
    bpath(answer, RUN_DIR "/menu/choice");
    bpath(started, RUN_DIR "/menu/started");
    int failures = menu_failures(0);
    if (exists(answer)) {
        char buf[SMALL_FILE], wanted[33] = "";
        size_t len;
        bjson j;
        int readable = !read_small(answer, buf, sizeof(buf), &len) && !bjson_parse(&j, buf, len, 16);
        if (readable) { readable = !bjson_string(&j, bjson_find(&j, 0, "ui"), wanted, sizeof(wanted)); bjson_free(&j); }
        unlink(answer);
        unlink(started);
        if (readable && !strcmp(wanted, "poweroff")) {
            /* The power key held in the menu (owner, 2026-10-06): switched off as stock's UI does it. */
            rstate_read("menu", &rs);
            role_status("menu", "answered", NULL, &rs, failures, "poweroff");
            plog("menu: the player switches off");
            power_off();
        }
        if (readable && (!strcmp(wanted, "stock") || ui_installed(wanted))) {
            snprintf(c->ui, sizeof(c->ui), "%s", wanted);
            snprintf(c->by, sizeof(c->by), "menu");
            c->note[0] = 0;
            c->menu = 0;
            write_choice(c);
            /* A valid answer confirms a tentative menu, and may be all this boot runs of ours. */
            confirm("menu");
            /* The last answer becomes the default: the menu starts on it next time and its countdown
               takes it (owner, 2026-10-07; no setting of its own). */
            int lock = state_lock();
            global_state g;
            if (!gstate_read(&g) && strcmp(g.ui, wanted)) {
                snprintf(g.ui, sizeof(g.ui), "%s", wanted);
                gstate_write(&g);
                plog("default ui %s: the menu's last answer", wanted);
            }
            state_unlock(lock);
            /* The status names the menu's package, as while it asked. */
            rstate_read("menu", &rs);
            char dir[PATH_MAX], merr[160];
            bpath(dir, DATA_DIR "/menu/%c", rs.current ? rs.current : 'a');
            role_status("menu", "answered", rs.current && !manifest_load(dir, m, merr, sizeof(merr)) ? m : NULL, &rs, failures, wanted);
            return 1;
        }
        if (readable) snprintf(err, sizeof(err), "it answered %s, which is not installed", wanted);
        else snprintf(err, sizeof(err), "its answer is unreadable");
        failures = menu_failures(1);
    } else if (exists(started)) {
        unlink(started);
        snprintf(err, sizeof(err), "it exited without an answer");
        failures = menu_failures(1);
    }
    if (rstate_read("menu", &rs) || !rs.current) {
        c->menu = 0; write_choice(c);
        role_status("menu", "absent", NULL, NULL, failures, NULL);
        return 0;
    }
    if (failures && !rs.confirmed && !rollback("menu", &rs)) blog("menu: a tentative version failed (%s); back to the previous one", err);
    if (failures >= MENU_FAILURES) {
        c->menu = 0;
        snprintf(c->note, sizeof(c->note), "the menu failed %d times (%.150s)", failures, err);
        write_choice(c);
        role_status("menu", "failed", NULL, &rs, failures, err);
        return 0;
    }
    if (slot_check("menu", rs.current, m, err, sizeof(err))) {
        int recovered = !rs.confirmed && !rollback("menu", &rs) && !slot_check("menu", rs.current, m, err, sizeof(err));
        if (!recovered) {
            c->menu = 0;
            snprintf(c->note, sizeof(c->note), "the menu fails its check: %.150s", err);
            write_choice(c);
            role_status("menu", "failed", NULL, &rs, failures, err);
            return 0;
        }
    }
    write_choices(c);
    write_atomic(started, "\n", 1, 0644);
    role_status("menu", "asking", m, &rs, failures, NULL);
    pid_t self = getpid(), first = fork();
    if (first == 0) {
        setsid();
        if (fork() == 0) {
            int in = open("/dev/null", O_RDWR);
            if (in >= 0) { dup2(in, 0); dup2(in, 1); dup2(in, 2); }
            only_standard_descriptors();
            menu_watch(self);
        }
        _exit(0);
    }
    if (first > 0) waitpid(first, NULL, 0);
    bpath(entry, DATA_DIR "/menu/%c/%s", rs.current, m->entry);
    char **envp = package_env("menu", m, rs.current, 1);
    char **args = package_argv("mq_ui", m);
    execve(entry, args, envp);
    (void)argv;
    unlink(started);
    failures = menu_failures(1);
    c->menu = 0;
    snprintf(c->note, sizeof(c->note), "the menu did not start");
    write_choice(c);
    role_status("menu", "failed", m, &rs, failures, "did not start");
    return 0;
}

static int launcher(int argc, char **argv) {
    (void)argc;
    char p[PATH_MAX], buf[32], err[200], fallback[PATH_MAX], domain[40], name[33] = "";
    fixture_init(argv[0]);
    if (read_boot()) exec_stock(argv);
    if (!strcmp(mode, "platform")) capture_start("mq_ui");
    bpath(p, RUN_DIR "/ui");
    mkdirs(p, 0755);
    bpath(p, RUN_DIR "/ui/pid");
    int n = snprintf(buf, sizeof(buf), "%ld\n", (long)getpid());
    write_atomic(p, buf, (size_t)n, 0644);
    bpath(fallback, RUN_DIR "/ui/fallback");
    if (strcmp(mode, "platform")) { role_status("ui", "stock-mode", NULL, NULL, 0, NULL); exec_stock(argv); }
    manifest *m = malloc(sizeof(*m));
    ui_choice c;
    if (!m) exec_stock(argv);
    if (read_choice(&c)) { role_status("ui", "fallback", NULL, NULL, 0, "no choice of UI for this boot"); exec_stock(argv); }
    plog("mq_ui launcher %ld: choice %s by %s%s", (long)getpid(), c.ui, c.by[0] ? c.by : "default", c.menu ? ", the menu asks" : "");
    /* A start with Play: the installation from the card comes first (owner, 2026-10-07). With a menu,
       the menu shows its progress and answers after it; without one, the launcher waits for it and
       takes the choice made with what was installed. */
    bpath(p, RUN_DIR "/ui/install-wait");
    if (install_pending()) {
        write_atomic(p, "\n", 1, 0644);
        if (!c.menu) {
            install_wait("mq_ui launcher");
            unlink(p);
            if (read_choice(&c)) { role_status("ui", "fallback", NULL, NULL, 0, "no choice of UI for this boot"); exec_stock(argv); }
            plog("mq_ui launcher %ld: choice %s by %s%s", (long)getpid(), c.ui, c.by[0] ? c.by : "default", c.menu ? ", the menu asks" : "");
        }
    } else unlink(p);
    /* What ran last has exited: its requests apply, the chosen UI's and the menu's, then those
       about the choice that any package left. */
    if (strcmp(c.ui, "stock")) { ui_domain(domain, c.ui); handle_request(domain, c.ui); }
    role_state rs;
    if (!rstate_read("menu", &rs) && rs.current) {
        bpath(p, DATA_DIR "/menu/%c", rs.current);
        if (!manifest_load(p, m, err, sizeof(err))) snprintf(name, sizeof(name), "%s", m->name);
    }
    handle_request("menu", name);
    apply_pending();
    int answered = c.menu ? menu_turn(&c, m, argv) : 0;
    if (exists(fallback)) { role_status("ui", "fallback", NULL, NULL, 0, NULL); exec_stock(argv); }
    if (!strcmp(c.ui, "stock")) { role_status("ui", "stock-ui", NULL, NULL, 0, c.note[0] ? c.note : NULL); exec_stock(argv); }
    ui_domain(domain, c.ui);
    if (rstate_read(domain, &rs) || !rs.current) {
        role_status("ui", "absent", NULL, NULL, 0, "the chosen ui package is not installed");
        exec_stock(argv);
    }
    int before = count_start();
    if (before >= UI_STARTS) {
        if (!rs.confirmed && !rollback(domain, &rs)) {
            bpath(p, RUN_DIR "/ui/starts"); unlink(p); count_start();
            role_status(domain, "rolled-back", NULL, &rs, before, "started too often before its confirmation");
        } else {
            write_atomic(fallback, "\n", 1, 0644);
            role_status(domain, "fallback", NULL, &rs, before, "started too often");
            exec_stock(argv);
        }
    }
    if (slot_check(domain, rs.current, m, err, sizeof(err))) {
        /* A tentative slot that fails its check gives way to the previous one, if that one checks. */
        int recovered = !rs.confirmed && !rollback(domain, &rs) && !slot_check(domain, rs.current, m, err, sizeof(err));
        if (!recovered) { write_atomic(fallback, "\n", 1, 0644); role_status(domain, "fallback", NULL, &rs, before, err); exec_stock(argv); }
    }
    if (answered && m->player[0] && player_ran()) {
        /* Stock's player already runs (a player ran before the menu in this boot), and the chosen UI
           brings its own: stock's loop restarts the pair when the UI is gone, the menu settled. */
        role_status(domain, "starting", m, &rs, before, "the pair restarts for the package's player");
        _exit(0);
    }
    char ready[PATH_MAX], entry[PATH_MAX];
    bpath(ready, RUN_DIR "/ui/ready");
    unlink(ready);
    bpath(entry, DATA_DIR "/%s/%c/%s", domain, rs.current, m->entry);
    role_status(domain, "starting", m, &rs, before, NULL);
    pid_t ui = getpid(), first = fork();
    if (first == 0) {
        setsid();
        if (fork() == 0) {
            int in = open("/dev/null", O_RDWR);
            if (in >= 0) { dup2(in, 0); dup2(in, 1); dup2(in, 2); }
            only_standard_descriptors();
            ui_watch(ui, domain, m, rs);
        }
        _exit(0);
    }
    if (first > 0) waitpid(first, NULL, 0);
    char **envp = package_env(domain, m, rs.current, 1);
    char **args = package_argv("mq_ui", m);
    execve(entry, args, envp);
    exec_stock(argv);
    return 127;
}

static void exec_stock_player(char **argv) {
    char stock[PATH_MAX];
    plog("mq_player: stock's player");
    mark_player_ran();
    bpath(stock, "/usr/bin/mq_player");
    argv[0] = "mq_player";
    execv(stock, argv);
    _exit(127);
}

static void player_status(const char *launch, const manifest *m, const char *note) {
    plog("player %s%s%s%s%s", launch, m ? " " : "", m ? m->name : "", note && *note ? ": " : "", note ? note : "");
    char p[PATH_MAX], buf[600], name[80], version[80], noted[300];
    bpath(p, RUN_DIR "/ui");
    mkdirs(p, 0755);
    bpath(p, RUN_DIR "/ui/player.json");
    json_str(name, sizeof(name), m ? m->name : "");
    json_str(version, sizeof(version), m ? m->version : "");
    json_str(noted, sizeof(noted), note);
    int n = snprintf(buf, sizeof(buf), "{\"schema\":1,\"launch\":\"%s\",\"name\":%s,\"version\":%s,\"note\":%s}\n",
                     launch, m ? name : "null", m ? version : "null", noted);
    if (n > 0 && n < (int)sizeof(buf)) write_atomic(p, buf, (size_t)n, 0644);
}

/* Stock's player, through the launcher the chosen ui package brings while that package's UI runs
   (contract, "The ui role"); the launcher ends in stock's player. Everything else (stock's UI
   chosen, the menu still choosing) and every failure here starts stock's player at once: the
   player never waits for a package. */
static int player_launcher(int argc, char **argv) {
    (void)argc;
    char fallback[PATH_MAX], err[200], entry[PATH_MAX], domain[40];
    fixture_init(argv[0]);
    bpath(fallback, RUN_DIR "/ui/fallback");
    role_state rs;
    ui_choice c;
    manifest *m = malloc(sizeof(*m));
    if (!m || read_boot() || strcmp(mode, "platform") || exists(fallback) || read_choice(&c)) {
        player_status("stock", NULL, m ? "no ui package runs" : "out of memory");
        exec_stock_player(argv);
    }
    /* A start with Play: the player starts after the installation, as the UI it follows does. */
    if (install_pending()) {
        install_wait("player");
        if (read_choice(&c)) { player_status("stock", NULL, "no choice of UI for this boot"); exec_stock_player(argv); }
    }
    if (c.menu && !player_ran()) {
        /* The first start of the pair in this boot: no player ran, so no watchdog runs yet. The player
           waits for the menu's choice and starts as the chosen UI needs it, without a restart. Its
           process already carries the name stock's watch loop looks for. */
        player_status("waiting", NULL, "the menu is choosing");
        double until = mono() + t_menu + 10;
        while (mono() < until && !read_choice(&c) && c.menu) pause_s(0.2);
        if (read_choice(&c)) { player_status("stock", NULL, "no choice of UI for this boot"); exec_stock_player(argv); }
        /* fiio_init.sh starts stock's player 2 s after its UI, and the two then share a flock of
           /usr/data/fiio/process_lock.txt and their queues. After the menu's choice the UI starts at
           once in the menu's process: the player keeps stock's order behind it (owner's player,
           2026-10-05/06: stock's UI hung or died after the menu's choice in three starts). */
        if (!c.menu) { plog("player follows the UI by %.0f s, as stock's loop starts them", t_pair); pause_s(t_pair); }
    }
    if (c.menu) { player_status("stock", NULL, "the menu is choosing"); exec_stock_player(argv); }
    if (!strcmp(c.ui, "stock")) { player_status("stock", NULL, "stock's UI was chosen"); exec_stock_player(argv); }
    ui_domain(domain, c.ui);
    if (rstate_read(domain, &rs) || !rs.current) { player_status("stock", NULL, "the chosen ui package is not installed"); exec_stock_player(argv); }
    if (slot_check(domain, rs.current, m, err, sizeof(err))) { player_status("stock", NULL, err); exec_stock_player(argv); }
    if (!m->player[0]) { player_status("stock", m, "the ui package brings no player launcher"); exec_stock_player(argv); }
    /* Stock's watch loop finds the player by its process name, which the kernel takes from the
       path executed: the launcher runs through a link named mq_player, as diskOS's image does. */
    char named[PATH_MAX], fresh[PATH_MAX];
    bpath(entry, DATA_DIR "/%s/%c/%s", domain, rs.current, m->player);
    bpath(named, RUN_DIR "/ui/mq_player");
    bpath(fresh, RUN_DIR "/ui");
    mkdirs(fresh, 0755);
    bpath(fresh, RUN_DIR "/ui/.mq_player.%ld", (long)getpid());
    unlink(fresh);
    if (symlink(entry, fresh) || rename(fresh, named)) {
        unlink(fresh);
        player_status("stock", m, "cannot name the player launcher");
        exec_stock_player(argv);
    }
    char **envp = package_env(domain, m, rs.current, 1);
    player_status("package", m, "");
    argv[0] = "mq_player";
    mark_player_ran();
    execve(named, argv, envp);
    player_status("stock", m, "the player launcher did not start");
    exec_stock_player(argv);
    return 127;
}

int main(int argc, char **argv) {
    const char *base = strrchr(argv[0], '/');
    base = base ? base + 1 : argv[0];
    if (!strcmp(base, "mq_ui")) return launcher(argc, argv);
    if (!strcmp(base, "mq_player")) return player_launcher(argc, argv);
    fixture_init(argv[0]);
    if (argc < 2) { fprintf(stderr, "usage: disc-boot early|start|stop|status|verify ROLE DIR [options]\n"); return 2; }
    if (!strcmp(argv[1], "early")) { options(argc, argv, 2); return cmd_early(); }
    if (!strcmp(argv[1], "start")) return cmd_start();
    if (!strcmp(argv[1], "stop")) return cmd_stop();
    if (!strcmp(argv[1], "status")) return cmd_status();
    if (!strcmp(argv[1], "verify") && argc >= 4) { options(argc, argv, 4); return cmd_verify(argv[2], argv[3]); }
    fprintf(stderr, "disc-boot: unknown command\n");
    return 2;
}
