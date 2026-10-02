/* disc-boot: the boot layer's program (docs/contract.md). No network, no card code. */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE
#include "boot_util.h"
#include "manifest.h"
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
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
#define LOG_CAP 65536
#define RESERVE (16LL * 1024 * 1024)
#define STOCK_LIBS "/usr/lib:/usr/lib/pulseaudio/:/usr/data/lib:/usr/data/lib/ffmpeg"

extern char **environ;
static const char *ROLES[] = {"service", "ui"};
/* Seconds; the fixture build shortens them. */
static double t_confirm = 180, t_grace = 5, t_window = 600, t_backoff = 2, t_card = 30, t_ui_window = 120;
static char profile[17], card[PATH_MAX] = "/tmp/sdcard", card_source[128] = "/dev/mmcblk0p1";
static char mode[9], reason[12];
static char last_request[200];
static volatile sig_atomic_t stopping;

static void on_term(int sig) { (void)sig; stopping = 1; }

static void fixture_init(void) {
#ifdef DISC_BOOT_FIXTURE
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
        }
    }
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

/* The SHA-256 of a slot's package.json: the fingerprint a rollback target keeps. */
static int slot_fingerprint(const char *role, char slot, char hex[65]) {
    char p[PATH_MAX];
    long long size;
    bpath(p, DATA_DIR "/%s/%c/package.json", role, slot);
    return file_sha256(p, hex, &size);
}
/* The previous slot still holds the version that was confirmed there: a package may stage an
   update into its inactive slot, which is that one, and then it is no rollback target. */
static int previous_intact(const char *role, const role_state *rs) {
    char hex[65];
    return rs->previous && rs->previous_manifest[0] && !slot_fingerprint(role, rs->previous, hex) && !strcmp(hex, rs->previous_manifest);
}
/* Target becomes the tentative current slot; a confirmed current one becomes the rollback target,
   with its fingerprint, and a tentative one leaves the earlier target as it was. */
static void make_current(const char *role, role_state *rs, char target) {
    if (rs->confirmed) {
        rs->previous = rs->current && rs->current != target && !slot_fingerprint(role, rs->current, rs->previous_manifest) ? rs->current : 0;
    }
    if (rs->previous == target || !rs->previous) { rs->previous = 0; rs->previous_manifest[0] = 0; }
    rs->current = target; rs->confirmed = 0;
}

static void role_status(const char *role, const char *state, const manifest *m, const role_state *rs, int failures, const char *note) {
    char p[PATH_MAX], buf[1400], name[80], version[80], noted[300], request[260], previous[260] = "null";
    bpath(p, RUN_DIR);
    mkdirs(p, 0755);
    bpath(p, RUN_DIR "/%s.json", role);
    json_str(name, sizeof(name), m ? m->name : "");
    json_str(version, sizeof(version), m ? m->version : "");
    json_str(noted, sizeof(noted), note ? note : "");
    json_str(request, sizeof(request), last_request);
    /* The version a rollback returns to, while its slot still holds it. */
    manifest *pm = rs && previous_intact(role, rs) ? malloc(sizeof(*pm)) : NULL;
    char dir[PATH_MAX], err[160], pname[80], pversion[80];
    if (pm) {
        bpath(dir, DATA_DIR "/%s/%c", role, rs->previous);
        if (!manifest_load(dir, pm, err, sizeof(err))) {
            json_str(pname, sizeof(pname), pm->name);
            json_str(pversion, sizeof(pversion), pm->version);
            snprintf(previous, sizeof(previous), "{\"slot\":\"%c\",\"name\":%s,\"version\":%s,\"manifest\":\"%.64s\"}",
                     rs->previous, pname, pversion, rs->previous_manifest);
        }
        free(pm);
    }
    int n = snprintf(buf, sizeof(buf),
        "{\"schema\":1,\"role\":\"%s\",\"state\":\"%s\",\"name\":%s,\"version\":%s,\"slot\":%s%c%s,\"confirmed\":%s,"
        "\"failures\":%d,\"note\":%s,\"lastRequest\":%s,\"previous\":%s}\n",
        role, state, m ? name : "null", m ? version : "null",
        rs && rs->current ? "\"" : "nul", rs && rs->current ? rs->current : 'l', rs && rs->current ? "\"" : "",
        rs && rs->confirmed ? "true" : "false", failures, noted, last_request[0] ? request : "null", previous);
    write_atomic(p, buf, (size_t)n, 0644);
}

/* Every installed role confirmed: the boot-loop count clears. Called under the state lock. */
static void maybe_clear_loop(void) {
    for (size_t k = 0; k < sizeof(ROLES) / sizeof(*ROLES); k++) {
        role_state rs;
        if (rstate_read(ROLES[k], &rs)) return;
        if (rs.current && !rs.confirmed) return;
    }
    global_state g;
    if (!gstate_read(&g) && g.unconfirmed) { g.unconfirmed = 0; gstate_write(&g); }
}

static void confirm(const char *role) {
    int lock = state_lock();
    role_state rs;
    if (!rstate_read(role, &rs) && rs.current && !rs.confirmed) { rs.confirmed = 1; rstate_write(role, &rs); }
    maybe_clear_loop();
    state_unlock(lock);
}

static int slot_check(const char *role, char slot, manifest *m, char *err, size_t cap) {
    char dir[PATH_MAX];
    bpath(dir, DATA_DIR "/%s/%c", role, slot);
    return manifest_load(dir, m, err, cap) || manifest_fits(m, role, profile, err, cap) || package_verify(dir, m, 1, err, cap) ? -1 : 0;
}

static int rollback(const char *role, role_state *rs) {
    if (!previous_intact(role, rs)) return -1;
    int lock = state_lock();
    rs->current = rs->previous; rs->previous = 0; rs->previous_manifest[0] = 0; rs->confirmed = 1;
    int r = rstate_write(role, rs);
    state_unlock(lock);
    return r;
}

/* A package's request, applied after it exited (docs/contract.md, "Requests from a package").
   Returns 1 when one was there (even refused), 0 otherwise. */
static int handle_request(const char *role, const char *current_name) {
    char p[PATH_MAX], buf[SMALL_FILE];
    bpath(p, DATA_DIR "/%s/request", role);
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
    if (rstate_read(role, &rs)) { snprintf(last_request, sizeof(last_request), "refused: the role's state is unreadable"); goto done; }
    if (!strcmp(action, "activate")) {
        char target = rs.current ? other(rs.current) : 'a';
        manifest *m = malloc(sizeof(*m));
        if (!m || slot_check(role, target, m, err, sizeof(err))) snprintf(last_request, sizeof(last_request), "activate refused: %s", m ? err : "out of memory");
        else {
            make_current(role, &rs, target);
            rstate_write(role, &rs);
            snprintf(last_request, sizeof(last_request), "activated %s %s", m->name, m->version);
        }
        free(m);
    } else if (!strcmp(action, "rollback")) {
        manifest *m = malloc(sizeof(*m));
        if (!rs.previous) snprintf(last_request, sizeof(last_request), "rollback refused: no previous version");
        else if (!previous_intact(role, &rs)) snprintf(last_request, sizeof(last_request), "rollback refused: the previous version was replaced");
        else if (!m || slot_check(role, rs.previous, m, err, sizeof(err))) snprintf(last_request, sizeof(last_request), "rollback refused: %s", m ? err : "out of memory");
        else {
            rs.current = rs.previous; rs.previous = 0; rs.previous_manifest[0] = 0; rs.confirmed = 1;
            rstate_write(role, &rs);
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
        bpath(p, DATA_DIR "/%s/a", role); remove_tree(p);
        bpath(p, DATA_DIR "/%s/b", role); remove_tree(p);
        bpath(p, DATA_DIR "/%s/state.json", role); unlink(p);
        if (purge && current_name && current_name[0]) { bpath(p, DATA_DIR "/data/%s", current_name); remove_tree(p); }
        maybe_clear_loop();
        snprintf(last_request, sizeof(last_request), purge ? "removed with its data" : "removed");
    } else snprintf(last_request, sizeof(last_request), "refused: unknown action");
done:
    state_unlock(lock);
    bjson_free(&j);
    blog("%s request: %s", role, last_request);
    return 1;
}

static void add_env(char **envp, int *n, const char *key, const char *value) {
    size_t len = strlen(key) + strlen(value) + 2;
    char *e = malloc(len);
    if (!e) exit(2);
    snprintf(e, len, "%s=%s", key, value);
    envp[(*n)++] = e;
}

/* The package's environment (docs/contract.md, "Environment of a package"). */
static char **package_env(const char *role, const manifest *m, char slot, int inherit) {
    char slotdir[PATH_MAX], inactive[PATH_MAX], request[PATH_MAX], data[PATH_MAX], run[PATH_MAX], status[PATH_MAX], cardp[PATH_MAX], libs[PATH_MAX * 2];
    bpath(slotdir, DATA_DIR "/%s/%c", role, slot);
    bpath(inactive, DATA_DIR "/%s/%c", role, other(slot));
    bpath(request, DATA_DIR "/%s/request", role);
    bpath(data, DATA_DIR "/data/%s", m->name);
    bpath(run, RUN_DIR "/%s", role);
    bpath(status, RUN_DIR);
    bpath(cardp, "%s", card);
    mkdirs(data, 0755); mkdirs(run, 0755); mkdirs(inactive, 0755);
    const char *stock_libs = inherit && getenv("LD_LIBRARY_PATH") ? getenv("LD_LIBRARY_PATH") : STOCK_LIBS;
    snprintf(libs, sizeof(libs), "%s/lib:%s", slotdir, stock_libs);
    size_t count = 0;
    if (inherit) for (char **e = environ; *e; e++) count++;
    char **envp = calloc(count + 16, sizeof(char *));
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

static int install(const char *role, const char *staged, char *note, size_t cap) {
    manifest *m = malloc(sizeof(*m));
    char err[200], slot[PATH_MAX], src[PATH_MAX], dst[PATH_MAX];
    int r = -1, lock = -1;
    if (!m) { snprintf(note, cap, "refused: out of memory"); return -1; }
    if (manifest_load(staged, m, err, sizeof(err)) || manifest_fits(m, role, profile, err, sizeof(err)) || package_verify(staged, m, 0, err, sizeof(err))) {
        snprintf(note, cap, "refused: %s", err); goto out;
    }
    struct statvfs v;
    bpath(slot, DATA_DIR);
    mkdirs(slot, 0755);
    if (statvfs(slot, &v) || (long long)v.f_bavail * (long long)v.f_frsize < m->total + RESERVE) { snprintf(note, cap, "refused: not enough room in /usr/data"); goto out; }
    lock = state_lock();
    role_state rs;
    if (rstate_read(role, &rs)) { snprintf(note, cap, "refused: the role's state is unreadable"); goto out; }
    char target = rs.current ? other(rs.current) : 'a';
    bpath(slot, DATA_DIR "/%s/%c", role, target);
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
    make_current(role, &rs, target);
    if (rstate_write(role, &rs)) { snprintf(note, cap, "refused: cannot record the slot"); goto out; }
    remove_tree(staged);
    snprintf(note, cap, "installed %s %s", m->name, m->version);
    r = 0;
out:
    state_unlock(lock);
    free(m);
    return r;
}

/* Play held at power-on: the packages staged on the card (contract, "Recovery from the card"). */
static void recovery(void) {
    double until = mono() + t_card;
    while (!card_mounted() && mono() < until && !stopping) pause_s(0.5);
    if (!card_mounted()) { blog("recovery: the card is not mounted"); return; }
    char result[2048], dir[PATH_MAX];
    int n = snprintf(result, sizeof(result), "{\"schema\":1,\"roles\":{");
    int any = 0, ui_installed = 0;
    for (size_t k = 0; k < sizeof(ROLES) / sizeof(*ROLES); k++) {
        bpath(dir, "%s/.disc/boot/install/%s", card, ROLES[k]);
        if (!is_dir(dir)) continue;
        char note[260], quoted[300];
        int ok = install(ROLES[k], dir, note, sizeof(note)) == 0;
        if (ok && !strcmp(ROLES[k], "ui")) ui_installed = 1;
        json_str(quoted, sizeof(quoted), note);
        n += snprintf(result + n, sizeof(result) - (size_t)n, "%s\"%s\":{\"installed\":%s,\"note\":%s}", any ? "," : "", ROLES[k], ok ? "true" : "false", quoted);
        any = 1;
        blog("recovery %s: %s", ROLES[k], note);
    }
    n += snprintf(result + n, sizeof(result) - (size_t)n, "}}\n");
    bpath(dir, "%s/.disc/boot", card);
    if (mkdirs(dir, 0755) == 0) {
        bpath(dir, "%s/.disc/boot/result.json", card);
        write_atomic(dir, result, (size_t)n, 0644);
    }
    if (ui_installed) {
        /* Stock's watch loop restarts the UI it finds missing; the launcher then runs the package. */
        char p[PATH_MAX], buf[32];
        bpath(p, RUN_DIR "/ui-launch");
        write_atomic(p, "ui\n", 3, 0644);
        bpath(p, RUN_DIR "/ui/pid");
        if (!read_small(p, buf, sizeof(buf), NULL)) { long pid = atol(buf); if (pid > 1) kill((pid_t)pid, SIGTERM); }
    }
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
    int installed = 0;
    for (size_t k = 0; k < sizeof(ROLES) / sizeof(*ROLES); k++) { role_state rs; if (!rstate_read(ROLES[k], &rs) && rs.current) installed = 1; }
    int count = g.unconfirmed;
    if (!strcmp(chosen, "platform") && (installed || !strcmp(why, "recovery"))) {
        int lock = state_lock();
        global_state now;
        if (gstate_read(&now)) now = g;
        now.unconfirmed++;
        gstate_write(&now);
        state_unlock(lock);
    }
    char p[PATH_MAX], buf[1024], cardq[260], sourceq[160];
    bpath(p, RUN_DIR);
    mkdirs(p, 0755);
    /* The permission /sbin/mq_ui reads: without it stock's UI starts, the boot program out of its way. */
    role_state ui;
    bpath(p, RUN_DIR "/ui-launch");
    if (!strcmp(chosen, "platform") && !rstate_read("ui", &ui) && ui.current) write_atomic(p, "ui\n", 3, 0644);
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
    char p[PATH_MAX], buf[SMALL_FILE];
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
    fputs("}\n", stdout);
    return 0;
}

static int cmd_verify(const char *role, const char *dir) {
    manifest *m = malloc(sizeof(*m));
    char err[200], quoted[260];
    if (!m) return 2;
    if (!profile[0]) read_boot();
    int ok = strcmp(role, "service") && strcmp(role, "ui") ? (snprintf(err, sizeof(err), "role must be service or ui"), 0)
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

static void ui_watch(pid_t ui, const manifest *m, role_state rs) {
    char ready[PATH_MAX];
    bpath(ready, RUN_DIR "/ui/ready");
    double until = mono() + m->ready;
    while (!exists(ready)) {
        if (!ui_alive(ui)) return;
        if (mono() > until) { role_status("ui", "not-ready", m, &rs, 0, "not ready in time"); kill(ui, SIGKILL); return; }
        pause_s(0.1);
    }
    role_status("ui", "ready", m, &rs, 0, NULL);
    until = mono() + t_confirm;
    while (mono() < until) { if (!ui_alive(ui)) return; pause_s(0.1); }
    confirm("ui");
    rs.confirmed = 1;
    char p[PATH_MAX];
    bpath(p, RUN_DIR "/ui/starts");
    unlink(p);
    role_status("ui", "confirmed", m, &rs, 0, NULL);
}

static void exec_stock(char **argv) {
    char stock[PATH_MAX];
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

static int launcher(int argc, char **argv) {
    (void)argc;
    char p[PATH_MAX], buf[32], err[200], fallback[PATH_MAX];
    fixture_init();
    if (read_boot()) exec_stock(argv);
    bpath(p, RUN_DIR "/ui");
    mkdirs(p, 0755);
    bpath(p, RUN_DIR "/ui/pid");
    int n = snprintf(buf, sizeof(buf), "%ld\n", (long)getpid());
    write_atomic(p, buf, (size_t)n, 0644);
    bpath(fallback, RUN_DIR "/ui/fallback");
    manifest *m = malloc(sizeof(*m));
    role_state rs;
    if (!m) exec_stock(argv);
    char current_name[33] = "";
    if (!rstate_read("ui", &rs) && rs.current) {
        char dir[PATH_MAX]; bpath(dir, DATA_DIR "/ui/%c", rs.current);
        if (!manifest_load(dir, m, err, sizeof(err))) snprintf(current_name, sizeof(current_name), "%s", m->name);
    }
    handle_request("ui", current_name);
    if (strcmp(mode, "platform") || exists(fallback) || rstate_read("ui", &rs) || !rs.current) {
        role_status("ui", strcmp(mode, "platform") ? "stock-mode" : exists(fallback) ? "fallback" : "absent", NULL, NULL, 0, NULL);
        exec_stock(argv);
    }
    int before = count_start();
    if (before >= UI_STARTS) {
        if (!rs.confirmed && !rollback("ui", &rs)) {
            bpath(p, RUN_DIR "/ui/starts"); unlink(p); count_start();
            role_status("ui", "rolled-back", NULL, &rs, before, "started too often before its confirmation");
        } else {
            write_atomic(fallback, "\n", 1, 0644);
            role_status("ui", "fallback", NULL, &rs, before, "started too often");
            exec_stock(argv);
        }
    }
    if (slot_check("ui", rs.current, m, err, sizeof(err))) {
        /* A tentative slot that fails its check gives way to the previous one, if that one checks. */
        int recovered = !rs.confirmed && !rollback("ui", &rs) && !slot_check("ui", rs.current, m, err, sizeof(err));
        if (!recovered) { write_atomic(fallback, "\n", 1, 0644); role_status("ui", "fallback", NULL, &rs, before, err); exec_stock(argv); }
    }
    char ready[PATH_MAX], entry[PATH_MAX];
    bpath(ready, RUN_DIR "/ui/ready");
    unlink(ready);
    bpath(entry, DATA_DIR "/ui/%c/%s", rs.current, m->entry);
    role_status("ui", "starting", m, &rs, before, NULL);
    pid_t ui = getpid(), first = fork();
    if (first == 0) {
        setsid();
        if (fork() == 0) {
            int in = open("/dev/null", O_RDWR);
            if (in >= 0) { dup2(in, 0); dup2(in, 1); dup2(in, 2); }
            ui_watch(ui, m, rs);
        }
        _exit(0);
    }
    if (first > 0) waitpid(first, NULL, 0);
    char **envp = package_env("ui", m, rs.current, 1);
    char **args = package_argv("mq_ui", m);
    execve(entry, args, envp);
    exec_stock(argv);
    return 127;
}

int main(int argc, char **argv) {
    const char *base = strrchr(argv[0], '/');
    base = base ? base + 1 : argv[0];
    if (!strcmp(base, "mq_ui")) return launcher(argc, argv);
    fixture_init();
    if (argc < 2) { fprintf(stderr, "usage: disc-boot early|start|stop|status|verify ROLE DIR [options]\n"); return 2; }
    if (!strcmp(argv[1], "early")) { options(argc, argv, 2); return cmd_early(); }
    if (!strcmp(argv[1], "start")) return cmd_start();
    if (!strcmp(argv[1], "stop")) return cmd_stop();
    if (!strcmp(argv[1], "status")) return cmd_status();
    if (!strcmp(argv[1], "verify") && argc >= 4) { options(argc, argv, 4); return cmd_verify(argv[2], argv[3]); }
    fprintf(stderr, "disc-boot: unknown command\n");
    return 2;
}
