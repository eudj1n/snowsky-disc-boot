/* disc-health: a read-only, offline journal of the player's health (a service of boot API 2:
   contract, "Roles"; plan, stage 7). At its start and then every 10 minutes it reads what the
   player shows of itself: the battery's fuel gauge, the thermal zones, uptime, load and memory,
   the free space of /usr/data and of the card, the kernel's card errors and fatal signals, and
   the restarts of stock's UI and player that stock's watch loop logs. Each reading is one line of
   its journal ($DISC_BOOT_DATA/journal.jsonl, which becomes journal.1.jsonl at 128 KiB: 256 KiB
   in all) and the latest one is its report ($DISC_BOOT_RUN/status.json, at most 4 KiB), which the
   server shows. It only reads: no socket, no write outside its own two folders. */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE
#include "boot_util.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <time.h>
#include <unistd.h>
#if defined(__linux__) && !defined(DISC_HEALTH_FIXTURE)
#include <sys/klog.h>
#endif

#define INTERVAL 600
#define JOURNAL_BYTES (128 * 1024)
#define REPORT_BYTES 4096
#define LINE_BYTES 1536
#define MAX_ZONES 4
/* A clock before this (2023-11-14) was never set: the reading has no time. */
#define CLOCK_SET 1700000000L
/* How often a reading without the card looks for it, in seconds. */
#define CARD_LOOK 5
/* The folders boot names: well under PATH_MAX, so a file in them always fits. */
#define FOLDER 1024

static volatile sig_atomic_t stopping;
static double interval = INTERVAL;
static char data_dir[FOLDER], run_dir[FOLDER], card[FOLDER];
/* Counted since this start: kernel lines are read up to the last time stamp seen. */
static double kernel_seen = -1;
static long card_errors, fatal_signals, restarts_first = -1, restarts_last = -1, samples;
static time_t started;

static void on_term(int sig) { (void)sig; stopping = 1; }

/* A JSON text appended at o, never past cap; the caller checks o < cap at the end. */
static void put(char *out, size_t cap, size_t *o, const char *fmt, ...) __attribute__((format(printf, 4, 5)));
static void put(char *out, size_t cap, size_t *o, const char *fmt, ...) {
    if (*o >= cap) return;
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(out + *o, cap - *o, fmt, ap);
    va_end(ap);
    *o = n < 0 ? cap : *o + (size_t)n;
}

/* A short text of /proc or /sys, read to its end: their files say 0 or a page as their size, never
   the text's (the guest's emulated gauge is a plain file, the player's is not). */
static int read_text(const char *abs, char *buf, size_t cap) {
    int fd = open(abs, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    size_t len = 0;
    while (len + 1 < cap) {
        ssize_t n = read(fd, buf + len, cap - 1 - len);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        len += (size_t)n;
    }
    close(fd);
    buf[len] = 0;
    len = strlen(buf);
    while (len && (buf[len - 1] == '\n' || buf[len - 1] == ' ')) buf[--len] = 0;
    return 0;
}

static int read_long(const char *abs, long long *out) {
    char buf[64], *end;
    if (read_text(abs, buf, sizeof(buf)) || !buf[0]) return -1;
    errno = 0;
    *out = strtoll(buf, &end, 10);
    return errno || *end ? -1 : 0;
}

/* Tenths of a unit as a decimal: -5 is -0.5. */
static void put_tenths(char *out, size_t cap, size_t *o, long long tenths) {
    put(out, cap, o, "%s%lld.%lld", tenths < 0 ? "-" : "", llabs(tenths) / 10, llabs(tenths) % 10);
}

/* The fuel gauge: the first power supply, by name, that has a capacity (cw221X-bat on the player:
   capacity in percent, voltage_now in microvolts, current_now in microamperes, temp in tenths of a
   degree, cycle_count). */
static void battery(char *out, size_t cap, size_t *o) {
    char dir[PATH_MAX], p[PATH_MAX], best[64] = "";
    bpath(dir, "/sys/class/power_supply");
    DIR *d = opendir(dir);
    struct dirent *e;
    while (d && (e = readdir(d))) {
        long long v;
        if (e->d_name[0] == '.' || strlen(e->d_name) >= sizeof(best)) continue;
        bpath(p, "/sys/class/power_supply/%s/capacity", e->d_name);
        if (!read_long(p, &v) && (!best[0] || strcmp(e->d_name, best) < 0)) snprintf(best, sizeof(best), "%.63s", e->d_name);
    }
    if (d) closedir(d);
    if (!best[0]) { put(out, cap, o, "null"); return; }
    static const struct { const char *file, *key; long long divide; int tenths; } FIELDS[] = {
        {"capacity", "percent", 1, 0}, {"voltage_now", "mV", 1000, 0}, {"current_now", "mA", 1000, 0},
        {"temp", "celsius", 1, 1}, {"cycle_count", "cycles", 1, 0},
    };
    put(out, cap, o, "{\"name\":\"");
    for (const char *c = best; *c; c++) put(out, cap, o, "%c", (*c >= 0x20 && *c < 0x7f && *c != '"' && *c != '\\') ? *c : '_');
    put(out, cap, o, "\"");
    for (size_t k = 0; k < sizeof(FIELDS) / sizeof(*FIELDS); k++) {
        long long v;
        bpath(p, "/sys/class/power_supply/%s/%s", best, FIELDS[k].file);
        if (read_long(p, &v)) continue;
        put(out, cap, o, ",\"%s\":", FIELDS[k].key);
        if (FIELDS[k].tenths) put_tenths(out, cap, o, v);
        else put(out, cap, o, "%lld", v / FIELDS[k].divide);
    }
    put(out, cap, o, "}");
}

/* The thermal zones (temp in thousandths of a degree), at most four, by number. */
static void thermal(char *out, size_t cap, size_t *o) {
    char p[PATH_MAX], type[33];
    int any = 0;
    put(out, cap, o, "[");
    for (int zone = 0; zone < 16 && any < MAX_ZONES; zone++) {
        long long v;
        bpath(p, "/sys/class/thermal/thermal_zone%d/temp", zone);
        if (read_long(p, &v)) continue;
        bpath(p, "/sys/class/thermal/thermal_zone%d/type", zone);
        if (read_text(p, type, sizeof(type))) snprintf(type, sizeof(type), "zone%d", zone);
        for (char *c = type; *c; c++) if (*c < 0x20 || *c >= 0x7f || *c == '"' || *c == '\\') *c = '_';
        put(out, cap, o, "%s{\"zone\":\"%s\",\"celsius\":", any++ ? "," : "", type);
        put_tenths(out, cap, o, v / 100);
        put(out, cap, o, "}");
    }
    put(out, cap, o, "]");
}

/* /proc/meminfo's total and available, in KiB. */
static void memory(char *out, size_t cap, size_t *o) {
    char p[PATH_MAX], line[128];
    long long total = -1, available = -1;
    bpath(p, "/proc/meminfo");
    FILE *f = fopen(p, "r");
    while (f && fgets(line, sizeof(line), f)) {
        sscanf(line, "MemTotal: %lld kB", &total);
        sscanf(line, "MemAvailable: %lld kB", &available);
    }
    if (f) fclose(f);
    if (total < 0) { put(out, cap, o, "null"); return; }
    put(out, cap, o, "{\"totalKB\":%lld", total);
    if (available >= 0) put(out, cap, o, ",\"availableKB\":%lld", available);
    put(out, cap, o, "}");
}

/* The card is where boot says (DISC_BOOT_CARD) and mounted there: else statvfs would describe the
   file system beneath the mount point. */
static int card_mounted(void) {
    char p[PATH_MAX], line[512], dev[256], target[512];
    /* The mount table names the card as the player does: without the fixture's root. */
    size_t rooted = strlen(boot_root);
    const char *want = rooted && !strncmp(card, boot_root, rooted) ? card + rooted : card;
    bpath(p, "/proc/mounts");
    FILE *f = fopen(p, "r");
    int ok = 0;
    while (f && !ok && fgets(line, sizeof(line), f)) ok = sscanf(line, "%255s %511s", dev, target) == 2 && card[0] && !strcmp(target, want);
    if (f) fclose(f);
    return ok;
}

static void space(char *out, size_t cap, size_t *o, const char *abs) {
    struct statvfs v;
    if (!abs || statvfs(abs, &v)) { put(out, cap, o, "null"); return; }
    put(out, cap, o, "{\"freeKB\":%llu,\"totalKB\":%llu}", (unsigned long long)v.f_bavail * v.f_frsize / 1024,
        (unsigned long long)v.f_blocks * v.f_frsize / 1024);
}

/* A kernel line about the card: mmc's or mmcblk's, naming an error, a timeout or a failure. */
static int card_line(const char *line) {
    if (!strstr(line, "mmcblk") && !strstr(line, "mmc0:") && !strstr(line, "mmc1:")) return 0;
    static const char *const WORDS[] = {"error", "Error", "ERROR", "timeout", "Timeout", "fail", "Fail"};
    for (size_t k = 0; k < sizeof(WORDS) / sizeof(*WORDS); k++) if (strstr(line, WORDS[k])) return 1;
    return 0;
}

/* The kernel's ring (the fixture's file) since the last line seen: card errors, and fatal signals
   (boot has the kernel print them in platform mode). Lines carry "[seconds.micros]". */
static void kernel(long *cards, long *fatal) {
    *cards = *fatal = 0;
    char *ring = NULL;
    int got = -1;
#if defined(__linux__) && !defined(DISC_HEALTH_FIXTURE)
    int len = klogctl(10, NULL, 0);   /* SYSLOG_ACTION_SIZE_BUFFER */
    ring = len > 0 && len <= (1 << 20) ? malloc((size_t)len + 1) : NULL;
    got = ring ? klogctl(3, ring, len) : -1;   /* SYSLOG_ACTION_READ_ALL */
#else
    char p[PATH_MAX];
    size_t len;
    bpath(p, "/fixture/kmsg");
    ring = malloc(1 << 20);
    if (ring && !read_small(p, ring, 1 << 20, &len)) got = (int)len;
#endif
    if (got <= 0) { free(ring); return; }
    ring[got] = 0;
    double last = kernel_seen;
    for (char *line = ring; line && *line; ) {
        char *end = strchr(line, '\n');
        if (end) *end = 0;
        char *stamp = strchr(line, '[');
        double at = stamp ? strtod(stamp + 1, NULL) : -1;
        if (at > kernel_seen) {
            if (card_line(line)) (*cards)++;
            if (strstr(line, "fatal signal")) (*fatal)++;
            if (at > last) last = at;
        }
        line = end ? end + 1 : NULL;
    }
    kernel_seen = last;
    free(ring);
}

/* Stock's watch loop logs each restart of its UI and player ("Restarting") in process_failed.txt. */
static long pair_restarts(void) {
    char p[PATH_MAX], line[512];
    bpath(p, "/usr/data/fiio/log/process_failed.txt");
    FILE *f = fopen(p, "r");
    if (!f) return -1;
    long n = 0;
    while (fgets(line, sizeof(line), f)) if (strstr(line, "Restarting")) n++;
    fclose(f);
    return n;
}

/* One reading as a JSON object. */
static size_t reading(char *out, size_t cap) {
    size_t o = 0;
    char p[PATH_MAX], text[128], data[PATH_MAX];
    time_t now = time(NULL);
    put(out, cap, &o, "{");
    if (now >= CLOCK_SET) put(out, cap, &o, "\"t\":%lld", (long long)now);
    else put(out, cap, &o, "\"t\":null");
    bpath(p, "/proc/sys/kernel/random/boot_id");
    if (!read_text(p, text, sizeof(text)) && strlen(text) >= 8 && strspn(text, "0123456789abcdef-") == strlen(text))
        put(out, cap, &o, ",\"boot\":\"%.8s\"", text);
    bpath(p, "/proc/uptime");
    if (!read_text(p, text, sizeof(text))) put(out, cap, &o, ",\"uptime\":%ld", atol(text));
    bpath(p, "/proc/loadavg");
    double l1, l5, l15;
    if (!read_text(p, text, sizeof(text)) && sscanf(text, "%lf %lf %lf", &l1, &l5, &l15) == 3)
        put(out, cap, &o, ",\"load\":[%.2f,%.2f,%.2f]", l1, l5, l15);
    put(out, cap, &o, ",\"battery\":"); battery(out, cap, &o);
    put(out, cap, &o, ",\"thermal\":"); thermal(out, cap, &o);
    put(out, cap, &o, ",\"memory\":"); memory(out, cap, &o);
    bpath(data, "/usr/data");
    put(out, cap, &o, ",\"space\":{\"data\":"); space(out, cap, &o, data);
    put(out, cap, &o, ",\"card\":"); space(out, cap, &o, card_mounted() ? card : NULL);
    long cards, fatal, restarts = pair_restarts();
    kernel(&cards, &fatal);
    card_errors += cards; fatal_signals += fatal;
    if (restarts_first < 0) restarts_first = restarts;
    restarts_last = restarts;
    put(out, cap, &o, "},\"kernel\":{\"cardErrors\":%ld,\"fatalSignals\":%ld}", cards, fatal);
    if (restarts >= 0) put(out, cap, &o, ",\"pairRestarts\":%ld", restarts);
    put(out, cap, &o, "}");
    return o < cap ? o : 0;
}

/* One line more in the journal; at JOURNAL_BYTES the journal becomes journal.1.jsonl (replacing
   the one before), so both together stay within 256 KiB. */
static void journal(const char *line, size_t len) {
    char p[PATH_MAX], older[PATH_MAX];
    struct stat s;
    snprintf(p, sizeof(p), "%s/journal.jsonl", data_dir);
    snprintf(older, sizeof(older), "%s/journal.1.jsonl", data_dir);
    if (!lstat(p, &s) && s.st_size + (off_t)len + 1 > JOURNAL_BYTES && rename(p, older)) return;
    int fd = open(p, O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW | O_CLOEXEC, 0644);
    if (fd < 0) return;
    if (write(fd, line, len) == (ssize_t)len && write(fd, "\n", 1) == 1) fsync(fd);
    close(fd);
}

static void report(const char *line, size_t len) {
    char p[PATH_MAX], buf[REPORT_BYTES];
    struct stat s;
    long long bytes = 0;
    snprintf(p, sizeof(p), "%s/journal.jsonl", data_dir);
    if (!lstat(p, &s)) bytes += s.st_size;
    snprintf(p, sizeof(p), "%s/journal.1.jsonl", data_dir);
    if (!lstat(p, &s)) bytes += s.st_size;
    size_t o = 0;
    put(buf, sizeof(buf), &o, "{\"schema\":1,\"interval\":%.0f,\"samples\":%ld,", interval, samples);
    if (started >= CLOCK_SET) put(buf, sizeof(buf), &o, "\"started\":%lld,", (long long)started);
    else put(buf, sizeof(buf), &o, "\"started\":null,");
    put(buf, sizeof(buf), &o, "\"journalBytes\":%lld,\"sinceStart\":{\"cardErrors\":%ld,\"fatalSignals\":%ld", bytes, card_errors, fatal_signals);
    if (restarts_first >= 0 && restarts_last >= restarts_first) put(buf, sizeof(buf), &o, ",\"pairRestarts\":%ld", restarts_last - restarts_first);
    put(buf, sizeof(buf), &o, "},\"latest\":%.*s}\n", (int)len, line);
    if (o >= sizeof(buf)) return;
    snprintf(p, sizeof(p), "%s/status.json", run_dir);
    write_atomic(p, buf, o, 0644);
}

static int env_path(const char *name, char out[FOLDER]) {
    const char *v = getenv(name);
    if (!v || v[0] != '/' || strlen(v) >= FOLDER) return -1;
    snprintf(out, FOLDER, "%s", v);
    return 0;
}

int main(void) {
#ifdef DISC_HEALTH_FIXTURE
    const char *root = getenv("DISC_BOOT_FIXTURE_ROOT");
    if (root && root[0] == '/') snprintf(boot_root, sizeof(boot_root), "%s", root);
    if (getenv("DISC_HEALTH_INTERVAL")) interval = atof(getenv("DISC_HEALTH_INTERVAL"));
#endif
    if (env_path("DISC_BOOT_DATA", data_dir) || env_path("DISC_BOOT_RUN", run_dir)) {
        fprintf(stderr, "disc-health: runs as a service of the boot layer (DISC_BOOT_DATA, DISC_BOOT_RUN)\n");
        return 2;
    }
    if (env_path("DISC_BOOT_CARD", card)) card[0] = 0;
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    started = time(NULL);
    char line[LINE_BYTES], ready[PATH_MAX];
    snprintf(ready, sizeof(ready), "%s/ready", run_dir);
    while (!stopping) {
        int card_seen = card_mounted();
        size_t len = reading(line, sizeof(line));
        if (len) { samples++; journal(line, len); report(line, len); }
        if (samples == 1 && !exists(ready)) { int fd = open(ready, O_WRONLY | O_CREAT | O_CLOEXEC, 0644); if (fd >= 0) close(fd); }
        /* At a start the card comes later (stock mounts it once its player runs): a reading without it
           is taken again as soon as it is there. */
        double until = mono() + interval, look = mono() + CARD_LOOK;
        while (!stopping && mono() < until) {
            pause_s(until - mono() < 1 ? until - mono() : 1);
            if (!card_seen && card[0] && mono() >= look) { if (card_mounted()) break; look = mono() + CARD_LOOK; }
        }
    }
    return 0;
}
