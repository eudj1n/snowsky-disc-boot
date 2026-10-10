/* disc-network: the player keeps several Wi-Fi networks and joins whichever is in range (a service
   of boot API 2; plan, stage 7; docs/dev/packages/network.md). Stock's connection removes every saved network
   before it adds the new one (remove_network all), so its configuration (/usr/data/wpa_supplicant.conf)
   holds one, and stock's UI fails with more (the owner's player, 2026-10-09). While Wi-Fi is on, every
   5 s this service looks at stock's wpa_supplicant through wpa_cli: it keeps the network stock
   connected to (its block of the configuration) in its own store ($DISC_BOOT_DATA/networks.json, mode
   0600, at most 8, the least recently used dropped first). When Wi-Fi has had no connection for 20 s,
   a minute after the configuration last changed, and a kept network is in range, it puts that network
   in the place of stock's one with stock's own sequence through wpa_supplicant (remove all, add, set,
   select, save: the owner's decisions of 2026-10-09; it never edits the file), so the configuration
   keeps one network. A network that does not connect after that waits 10 minutes. It acts only when
   wpa_supplicant's networks are those saved in the file and both stayed so for two looks, never in the
   middle of stock's own sequence. A configuration with no network at all when Wi-Fi comes on (stock's
   reset; S43wifi writes a new one) makes it forget its own too. Its report ($DISC_BOOT_RUN/status.json)
   names the networks, never a key. */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE
#include "boot_util.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define INTERVAL 5
#define STEADY 2
/* In looks (of INTERVAL s): no connection for this long before another network takes stock's place;
   so long after the configuration changed (stock's connection or this service's); a network that did
   not connect after it took the place waits this long; a scan asked at most this often while away. */
#define AWAY_LOOKS 4
#define QUIET_LOOKS 12
#define RETRY_LOOKS 120
#define SCAN_LOOKS 6
#define MAX_KEPT 8
#define MAX_LISTED 32
#define ANSWER 8192
#define CONF_BYTES 16384
#define REPORT_BYTES 4096
/* The folders boot names: well under PATH_MAX, so a file in them always fits. */
#define FOLDER 1024
/* A configuration's value as wpa_supplicant writes it: a quoted text, or hex (a 32-byte SSID is 64). */
#define VALUE 72

extern char **environ;
static volatile sig_atomic_t stopping;
static double interval = INTERVAL;
static char data_dir[FOLDER], run_dir[FOLDER];

/* A kept network: its configuration's values as wpa_supplicant wrote them (ssid quoted or hex, psk a
   quoted passphrase or hex, key_mgmt NONE for an open one), and when it was last the connected one. */
typedef struct { char ssid[VALUE], psk[VALUE], key_mgmt[40]; int scan_ssid; long used; } kept;
static kept store[MAX_KEPT];
static int nstore;
static long last_used;

/* A network block of the configuration: the values kept, and whether it has others (EAP and the like,
   which this service does not copy). */
typedef struct { char ssid[VALUE], psk[VALUE], key_mgmt[40]; int scan_ssid, other; } block;
/* A network wpa_supplicant holds now (list_networks): its id, SSID and flags. */
typedef struct { int id, current, disabled; unsigned char ssid[32]; size_t len; } listed;
/* A network in range (scan_results): its SSID and best signal (dBm). */
typedef struct { unsigned char ssid[32]; size_t len; int signal; } heard;
/* A kept network that did not connect after it took stock's place, and the look it failed at. */
typedef struct { char ssid[VALUE]; long at; } missed;
static missed misses[MAX_KEPT];
static int nmisses;

static void on_term(int sig) { (void)sig; stopping = 1; }

static void put(char *out, size_t cap, size_t *o, const char *fmt, ...) __attribute__((format(printf, 4, 5)));
static void put(char *out, size_t cap, size_t *o, const char *fmt, ...) {
    if (*o >= cap) return;
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(out + *o, cap - *o, fmt, ap);
    va_end(ap);
    *o = n < 0 ? cap : *o + (size_t)n;
}

static int hexval(char c) {
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

/* A configuration's SSID as bytes: "text" (taken as it is), P"escaped" or hex. */
static int conf_bytes(const char *v, unsigned char out[32], size_t *len) {
    size_t n = strlen(v);
    *len = 0;
    if (n >= 2 && v[0] == '"' && v[n - 1] == '"') {
        if (n - 2 > 32) return -1;
        memcpy(out, v + 1, n - 2);
        *len = n - 2;
        return 0;
    }
    if (n >= 3 && v[0] == 'P' && v[1] == '"' && v[n - 1] == '"') {
        for (size_t k = 2; k < n - 1 && *len < 32; k++) {
            int c = (unsigned char)v[k];
            if (c == '\\' && k + 1 < n - 1) {
                char e = v[++k];
                if (e == 'x' && k + 2 < n - 1 && hexval(v[k + 1]) >= 0 && hexval(v[k + 2]) >= 0) { c = hexval(v[k + 1]) * 16 + hexval(v[k + 2]); k += 2; }
                else c = e == 'n' ? '\n' : e == 'r' ? '\r' : e == 't' ? '\t' : e == 'e' ? 27 : e;
            }
            out[(*len)++] = (unsigned char)c;
        }
        return 0;
    }
    if (n % 2 || n > 64) return -1;
    for (size_t k = 0; k < n; k += 2) {
        if (hexval(v[k]) < 0 || hexval(v[k + 1]) < 0) return -1;
        out[(*len)++] = (unsigned char)(hexval(v[k]) * 16 + hexval(v[k + 1]));
    }
    return 0;
}

/* An SSID as wpa_cli prints it (printf_encode: \\, \", \e, \n, \r, \t and \xNN) as bytes. */
static void printed_bytes(const char *v, unsigned char out[32], size_t *len) {
    *len = 0;
    for (size_t k = 0; v[k] && *len < 32; k++) {
        int c = (unsigned char)v[k];
        if (c == '\\' && v[k + 1]) {
            char e = v[++k];
            if (e == 'x' && hexval(v[k + 1]) >= 0 && hexval(v[k + 2]) >= 0) { c = hexval(v[k + 1]) * 16 + hexval(v[k + 2]); k += 2; }
            else c = e == 'n' ? '\n' : e == 'r' ? '\r' : e == 't' ? '\t' : e == 'e' ? 27 : e;
        }
        out[(*len)++] = (unsigned char)c;
    }
}

static int same(const unsigned char *a, size_t la, const unsigned char *b, size_t lb) { return la == lb && !memcmp(a, b, la); }

/* A name for a change's words: printable ASCII as it is, else "a network". */
static void ascii_name(char *out, size_t cap, const unsigned char *s, size_t len) {
    int plain = len > 0;
    for (size_t k = 0; plain && k < len; k++) plain = s[k] >= 0x20 && s[k] < 0x7f;
    if (plain) snprintf(out, cap, "%.*s", (int)len, (const char *)s);
    else snprintf(out, cap, "a network");
}

/* A name for the report: UTF-8 as it is, anything else a question mark; JSON-quoted. */
static void put_name(char *out, size_t cap, size_t *o, const unsigned char *s, size_t len) {
    put(out, cap, o, "\"");
    for (size_t k = 0; k < len; ) {
        unsigned char c = s[k];
        size_t need = c < 0x80 ? 1 : (c & 0xe0) == 0xc0 ? 2 : (c & 0xf0) == 0xe0 ? 3 : (c & 0xf8) == 0xf0 ? 4 : 0;
        int valid = need && k + need <= len;
        for (size_t m = 1; valid && m < need; m++) valid = (s[k + m] & 0xc0) == 0x80;
        if (!valid || (need == 1 && (c < 0x20 || c == 0x7f))) { put(out, cap, o, "?"); k++; continue; }
        if (need == 1 && (c == '"' || c == '\\')) put(out, cap, o, "\\%c", c);
        else put(out, cap, o, "%.*s", (int)need, (const char *)s + k);
        k += need;
    }
    put(out, cap, o, "\"");
}

/* wpa_cli -i wlan0 -- <args>: its answer (at most cap - 1 bytes) within 5 s; 0 when it ran and exited 0.
   The "--" ends wpa_cli's options: glibc's getopt takes any later argument that begins with "-" for one
   (the owner's player, 2026-10-09: "set_network 1 priority -1" answered "invalid option -- '1'"). */
static int wpa(char *out, size_t cap, ...) {
    char cli[PATH_MAX];
    const char *argv[12] = {"wpa_cli", "-i", "wlan0", "--"};
    int argc = 4;
    va_list ap;
    va_start(ap, cap);
    for (const char *a; argc < 11 && (a = va_arg(ap, const char *)); ) argv[argc++] = a;
    va_end(ap);
    argv[argc] = NULL;
    bpath(cli, "/usr/sbin/wpa_cli");
    int pipefd[2];
    out[0] = 0;
    if (pipe(pipefd)) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return -1; }
    if (pid == 0) {
        int null = open("/dev/null", O_RDWR);
        if (null >= 0) { dup2(null, 0); dup2(null, 2); }
        dup2(pipefd[1], 1);
        for (int fd = 3; fd < 256; fd++) close(fd);
        execve(cli, (char *const *)argv, environ);
        _exit(127);
    }
    close(pipefd[1]);
    size_t got = 0;
    double until = mono() + 5;
    for (;;) {
        double left = until - mono();
        if (left <= 0) { kill(pid, SIGKILL); break; }
        struct pollfd p = {pipefd[0], POLLIN, 0};
        int ready = poll(&p, 1, (int)(left * 1000) + 1);
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0) continue;
        ssize_t n = read(pipefd[0], out + got, cap - 1 - got);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        got += (size_t)n;
        if (got + 1 >= cap) break;
    }
    out[got] = 0;
    close(pipefd[0]);
    int status;
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : -1;
}

/* wpa_cli's answer to a change: "OK" on a line of its own. */
static int ok(const char *answer) { return !strncmp(answer, "OK", 2); }

/* Stock's wpa_supplicant runs and answers on wlan0. */
static int wifi_on(void) {
    char p[PATH_MAX], answer[64];
    bpath(p, "/var/run/wpa_supplicant/wlan0");
    return exists(p) && !wpa(answer, sizeof(answer), "ping", NULL) && strstr(answer, "PONG") != NULL;
}

/* wpa_state and, when connected, the SSID. */
static void status(char state[32], unsigned char ssid[32], size_t *len) {
    char answer[ANSWER];
    state[0] = 0; *len = 0;
    if (wpa(answer, sizeof(answer), "status", NULL)) return;
    for (char *line = strtok(answer, "\n"); line; line = strtok(NULL, "\n")) {
        if (!strncmp(line, "wpa_state=", 10)) snprintf(state, 32, "%.31s", line + 10);
        else if (!strncmp(line, "ssid=", 5)) printed_bytes(line + 5, ssid, len);
    }
}

/* list_networks: "id\tssid\tbssid\tflags" after a header. -1 when wpa_cli does not answer. */
static int list(listed *out, int cap) {
    char answer[ANSWER];
    int n = 0;
    if (wpa(answer, sizeof(answer), "list_networks", NULL)) return -1;
    char *save = NULL;
    for (char *line = strtok_r(answer, "\n", &save); line && n < cap; line = strtok_r(NULL, "\n", &save)) {
        char *tab = strchr(line, '\t');
        if (!tab || line[0] < '0' || line[0] > '9') continue;
        *tab = 0;
        char *ssid = tab + 1, *rest = strchr(ssid, '\t');
        if (rest) *rest++ = 0;
        out[n].id = atoi(line);
        printed_bytes(ssid, out[n].ssid, &out[n].len);
        out[n].current = rest && strstr(rest, "[CURRENT]") != NULL;
        out[n].disabled = rest && strstr(rest, "[DISABLED]") != NULL;
        n++;
    }
    return n;
}

/* scan_results: "bssid\tfrequency\tsignal\tflags\tssid" after a header; each SSID once, its best signal. */
static int scan_results(heard *out, int cap) {
    char answer[ANSWER];
    int n = 0;
    if (wpa(answer, sizeof(answer), "scan_results", NULL)) return 0;
    char *save = NULL;
    for (char *line = strtok_r(answer, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char *field[5] = {line};
        int f = 1;
        for (char *c = line; *c && f < 5; c++) if (*c == '\t') { *c = 0; field[f++] = c + 1; }
        if (f < 5 || !strchr(field[0], ':')) continue;
        unsigned char ssid[32];
        size_t len;
        printed_bytes(field[4], ssid, &len);
        if (!len) continue;
        int signal = atoi(field[2]), at = -1;
        for (int k = 0; k < n; k++) if (same(out[k].ssid, out[k].len, ssid, len)) at = k;
        if (at >= 0) { if (signal > out[at].signal) out[at].signal = signal; continue; }
        if (n == cap) continue;
        memcpy(out[n].ssid, ssid, len); out[n].len = len; out[n].signal = signal;
        n++;
    }
    return n;
}

/* The configuration's network blocks, and the file's identity for the steadiness. -1 when unreadable. */
static int conf(block *out, int cap, struct stat *st) {
    char p[PATH_MAX], *text = malloc(CONF_BYTES);
    size_t len;
    bpath(p, "/usr/data/wpa_supplicant.conf");
    if (!text || stat(p, st) || read_small(p, text, CONF_BYTES, &len)) { free(text); return -1; }
    int n = 0, inside = 0;
    for (char *line = strtok(text, "\n"); line; line = strtok(NULL, "\n")) {
        while (*line == ' ' || *line == '\t') line++;
        if (!inside) {
            if (!strncmp(line, "network={", 9) && n < cap) { inside = 1; memset(&out[n], 0, sizeof(*out)); }
            continue;
        }
        if (line[0] == '}') { inside = 0; n++; continue; }
        char *eq = strchr(line, '=');
        if (!eq || line[0] == '#') continue;
        *eq = 0;
        const char *key = line, *value = eq + 1;
        block *b = &out[n];
        if (!strcmp(key, "ssid") && strlen(value) < VALUE) snprintf(b->ssid, VALUE, "%.71s", value);
        else if (!strcmp(key, "psk") && strlen(value) < VALUE) snprintf(b->psk, VALUE, "%.71s", value);
        else if (!strcmp(key, "key_mgmt") && strlen(value) < sizeof(b->key_mgmt)) snprintf(b->key_mgmt, sizeof(b->key_mgmt), "%.39s", value);
        else if (!strcmp(key, "scan_ssid")) b->scan_ssid = atoi(value);
        else if (strcmp(key, "priority") && strcmp(key, "disabled") && strcmp(key, "bssid")) b->other = 1;
    }
    free(text);
    return n;
}

static void store_path(char out[PATH_MAX]) { snprintf(out, PATH_MAX, "%s/networks.json", data_dir); }

static void store_load(void) {
    char p[PATH_MAX], *buf = malloc(SMALL_FILE * 2);
    size_t len;
    bjson j;
    nstore = 0; last_used = 0;
    store_path(p);
    if (!buf || read_small(p, buf, SMALL_FILE * 2, &len) || bjson_parse(&j, buf, len, 256)) { free(buf); return; }
    int a = bjson_find(&j, 0, "networks");
    for (int k = 0; a >= 0 && bjson_type(&j, a) == 'a' && k < bjson_size(&j, a) && nstore < MAX_KEPT; k++) {
        int e = bjson_item(&j, a, k), v;
        long long used = 0, scan = 0;
        kept *s = &store[nstore];
        memset(s, 0, sizeof(*s));
        if (bjson_string(&j, bjson_find(&j, e, "ssid"), s->ssid, VALUE)) continue;
        if ((v = bjson_find(&j, e, "psk")) >= 0 && !bjson_null(&j, v) && bjson_string(&j, v, s->psk, VALUE)) continue;
        if ((v = bjson_find(&j, e, "keyMgmt")) >= 0 && !bjson_null(&j, v) && bjson_string(&j, v, s->key_mgmt, sizeof(s->key_mgmt))) continue;
        if ((v = bjson_find(&j, e, "scanSsid")) >= 0) bjson_int(&j, v, &scan);
        if ((v = bjson_find(&j, e, "used")) >= 0) bjson_int(&j, v, &used);
        s->scan_ssid = (int)scan; s->used = (long)used;
        if (s->used > last_used) last_used = s->used;
        nstore++;
    }
    bjson_free(&j);
    free(buf);
}

/* The store, written beside and renamed, readable by root only (it holds the keys). */
static void store_save(void) {
    char p[PATH_MAX], buf[SMALL_FILE * 2], a[VALUE * 2 + 8], b[VALUE * 2 + 8], c[96];
    size_t o = 0;
    put(buf, sizeof(buf), &o, "{\"schema\":1,\"networks\":[");
    for (int k = 0; k < nstore; k++) {
        json_str(a, sizeof(a), store[k].ssid);
        if (store[k].psk[0]) json_str(b, sizeof(b), store[k].psk); else snprintf(b, sizeof(b), "null");
        if (store[k].key_mgmt[0]) json_str(c, sizeof(c), store[k].key_mgmt); else snprintf(c, sizeof(c), "null");
        put(buf, sizeof(buf), &o, "%s{\"ssid\":%s,\"psk\":%s,\"keyMgmt\":%s,\"scanSsid\":%d,\"used\":%ld}", k ? "," : "", a, b, c,
            store[k].scan_ssid, store[k].used);
    }
    put(buf, sizeof(buf), &o, "]}\n");
    if (o >= sizeof(buf)) return;
    store_path(p);
    write_atomic(p, buf, o, 0600);
}

static int kept_bytes(const kept *s, unsigned char out[32], size_t *len) { return conf_bytes(s->ssid, out, len); }

/* The network stock connected to, from its block: kept, or its keys updated; the most recent first
   to stay, the least recent dropped beyond 8. Returns whether the store changed. */
static int keep(const block *b, char *change, size_t cap) {
    unsigned char want[32], have[32];
    size_t wl, hl;
    if (conf_bytes(b->ssid, want, &wl)) return 0;
    int found = -1, oldest = 0;
    for (int k = 0; k < nstore; k++) {
        if (!kept_bytes(&store[k], have, &hl) && same(want, wl, have, hl)) found = k;
        if (store[k].used < store[oldest].used) oldest = k;
    }
    if (found >= 0 && !strcmp(store[found].psk, b->psk) && !strcmp(store[found].key_mgmt, b->key_mgmt)
        && store[found].scan_ssid == b->scan_ssid && store[found].used == last_used) return 0;
    int k = found >= 0 ? found : nstore < MAX_KEPT ? nstore++ : oldest;
    kept *s = &store[k];
    char name[40];
    ascii_name(name, sizeof(name), want, wl);
    snprintf(change, cap, "%s %s", found >= 0 ? "kept again" : "kept", name);
    snprintf(s->ssid, VALUE, "%.71s", b->ssid);
    snprintf(s->psk, VALUE, "%.71s", b->psk);
    snprintf(s->key_mgmt, sizeof(s->key_mgmt), "%.39s", b->key_mgmt);
    s->scan_ssid = b->scan_ssid;
    s->used = ++last_used;
    return 1;
}

/* A kept network in the place of stock's one, by stock's own sequence (connect_wifi): every network
   removed, this one added, set, selected and saved, so the configuration holds it alone. */
static int take_place(const kept *s) {
    char answer[64], id[16];
    if (wpa(answer, sizeof(answer), "remove_network", "all", NULL) || !ok(answer)) return -1;
    if (wpa(answer, sizeof(answer), "add_network", NULL) || answer[0] < '0' || answer[0] > '9') return -1;
    snprintf(id, sizeof(id), "%d", atoi(answer));
    int good = !wpa(answer, sizeof(answer), "set_network", id, "ssid", s->ssid, NULL) && ok(answer);
    if (good && s->psk[0]) good = !wpa(answer, sizeof(answer), "set_network", id, "psk", s->psk, NULL) && ok(answer);
    if (good && s->key_mgmt[0]) good = !wpa(answer, sizeof(answer), "set_network", id, "key_mgmt", s->key_mgmt, NULL) && ok(answer);
    if (good && s->scan_ssid) good = !wpa(answer, sizeof(answer), "set_network", id, "scan_ssid", "1", NULL) && ok(answer);
    if (good) good = !wpa(answer, sizeof(answer), "select_network", id, NULL) && ok(answer);
    if (good) good = !wpa(answer, sizeof(answer), "save_config", NULL) && ok(answer);
    return good ? 0 : -1;
}

/* Whether a kept network failed to connect within RETRY_LOOKS. */
static int missed_lately(const kept *s, long look) {
    for (int k = 0; k < nmisses; k++) if (!strcmp(misses[k].ssid, s->ssid)) return look - misses[k].at < RETRY_LOOKS;
    return 0;
}

static void miss(const char *ssid, long look) {
    int at = nmisses;
    for (int k = 0; k < nmisses; k++) if (!strcmp(misses[k].ssid, ssid)) at = k;
    if (at == MAX_KEPT) at = 0;
    snprintf(misses[at].ssid, VALUE, "%.71s", ssid);
    misses[at].at = look;
    if (at == nmisses) nmisses++;
}

static const char *wifi_word(int on, const char *state) {
    if (!on) return "off";
    if (!strcmp(state, "COMPLETED")) return "connected";
    if (!strcmp(state, "AUTHENTICATING") || !strcmp(state, "ASSOCIATING") || !strcmp(state, "ASSOCIATED")
        || !strcmp(state, "4WAY_HANDSHAKE") || !strcmp(state, "GROUP_HANDSHAKE")) return "connecting";
    return "searching";
}

static void report(int on, const char *state, const unsigned char *ssid, size_t len, const listed *present, int npresent,
                   const heard *range, int nrange, const char *change, time_t changed) {
    char p[PATH_MAX], buf[REPORT_BYTES];
    size_t o = 0;
    put(buf, sizeof(buf), &o, "{\"schema\":1,\"wifi\":\"%s\",\"connected\":", wifi_word(on, state));
    if (on && !strcmp(state, "COMPLETED")) put_name(buf, sizeof(buf), &o, ssid, len); else put(buf, sizeof(buf), &o, "null");
    put(buf, sizeof(buf), &o, ",\"networks\":[");
    for (int k = 0; k < nstore; k++) {
        unsigned char name[32];
        size_t nl;
        if (kept_bytes(&store[k], name, &nl)) nl = 0;
        int here = 0, near = 0;
        for (int m = 0; m < npresent; m++) here |= same(name, nl, present[m].ssid, present[m].len);
        for (int m = 0; m < nrange; m++) near |= same(name, nl, range[m].ssid, range[m].len);
        put(buf, sizeof(buf), &o, "%s{\"name\":", k ? "," : "");
        put_name(buf, sizeof(buf), &o, name, nl);
        put(buf, sizeof(buf), &o, ",\"open\":%s,\"held\":%s,\"inRange\":%s}", !strcmp(store[k].key_mgmt, "NONE") ? "true" : "false",
            here ? "true" : "false", near ? "true" : "false");
    }
    put(buf, sizeof(buf), &o, "],\"lastChange\":");
    if (change[0]) {
        char quoted[300];
        json_str(quoted, sizeof(quoted), change);
        /* json_str keeps ASCII only: a change's name in UTF-8 is in networks, by the same order. */
        put(buf, sizeof(buf), &o, "{\"what\":%s,\"t\":", quoted);
        if (changed >= 1700000000L) put(buf, sizeof(buf), &o, "%lld}", (long long)changed); else put(buf, sizeof(buf), &o, "null}");
    } else put(buf, sizeof(buf), &o, "null");
    put(buf, sizeof(buf), &o, "}\n");
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
#ifdef DISC_NETWORK_FIXTURE
    const char *root = getenv("DISC_BOOT_FIXTURE_ROOT");
    if (root && root[0] == '/') snprintf(boot_root, sizeof(boot_root), "%s", root);
    if (getenv("DISC_NETWORK_INTERVAL")) interval = atof(getenv("DISC_NETWORK_INTERVAL"));
#endif
    if (env_path("DISC_BOOT_DATA", data_dir) || env_path("DISC_BOOT_RUN", run_dir)) {
        fprintf(stderr, "disc-network: runs as a service of the boot layer (DISC_BOOT_DATA, DISC_BOOT_RUN)\n");
        return 2;
    }
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    signal(SIGPIPE, SIG_IGN);
    store_load();
    char ready[PATH_MAX], change[200] = "", seen[80] = "", placed[VALUE] = "";
    time_t changed = 0;
    snprintf(ready, sizeof(ready), "%s/ready", run_dir);
    /* fresh: the first look since Wi-Fi came on (or this service started), where an empty
       configuration means a reset. away: looks in a row without a connection. The look the
       configuration last changed at, a scan was last asked at, and a kept network took stock's place at
       (placed, until it connects or misses). */
    int steady = 0, first = 1, fresh = 1, away = 0;
    long look = 0, conf_look = -QUIET_LOOKS, scan_look = -SCAN_LOOKS, placed_look = 0;
    static listed present[MAX_LISTED], before[MAX_LISTED];
    static block blocks[MAX_LISTED];
    static heard range[MAX_LISTED];
    int nbefore = -1;
    while (!stopping) {
        char state[32] = "";
        unsigned char ssid[32];
        size_t slen = 0;
        int on = wifi_on(), npresent = 0, nrange = 0;
        look++;
        if (on) {
            status(state, ssid, &slen);
            npresent = list(present, MAX_LISTED);
            nrange = scan_results(range, MAX_LISTED);
            struct stat st;
            int nblocks = conf(blocks, MAX_LISTED, &st);
            /* The file and wpa_supplicant's networks as they were at the last look: steady. */
            char now[80];
            snprintf(now, sizeof(now), "%lld:%lld:%lld", (long long)st.st_ino, (long long)st.st_size, (long long)st.st_mtime);
            int listed_same = npresent == nbefore;
            for (int k = 0; listed_same && k < npresent; k++)
                listed_same = present[k].id == before[k].id && present[k].disabled == before[k].disabled
                              && same(present[k].ssid, present[k].len, before[k].ssid, before[k].len);
            if (seen[0] && strcmp(now, seen)) conf_look = look;
            steady = nblocks >= 0 && npresent >= 0 && !strcmp(now, seen) && listed_same ? steady + 1 : 0;
            snprintf(seen, sizeof(seen), "%s", now);
            memcpy(before, present, sizeof(present));
            nbefore = npresent;
            /* What wpa_supplicant holds is what the file saves: the same networks, in the same order. */
            int saved = nblocks == npresent;
            for (int k = 0; saved && k < npresent; k++) {
                unsigned char b[32];
                size_t bl;
                saved = !conf_bytes(blocks[k].ssid, b, &bl) && same(b, bl, present[k].ssid, present[k].len);
            }
            int connected = !strcmp(state, "COMPLETED");
            const char *word = wifi_word(on, state);
            away = connected ? 0 : away + 1;
            /* The network that took stock's place connected, or did not in its time: it waits. */
            if (placed[0]) {
                unsigned char want[32];
                size_t wl;
                if (connected && !conf_bytes(placed, want, &wl) && same(want, wl, ssid, slen)) placed[0] = 0;
                else if (look - placed_look >= QUIET_LOOKS) { miss(placed, look); placed[0] = 0; }
            }
            if (steady >= STEADY && saved && strcmp(word, "connecting")) {
                int store_changed = 0, removed = 0, failed = 0;
                if (nblocks == 0 && fresh) {
                    if (nstore) {
                        snprintf(change, sizeof(change), "forgot %d networks: the player has none saved (reset)", nstore);
                        nstore = 0; last_used = 0; store_changed = 1;
                    }
                } else {
                    if (connected) {
                        for (int k = 0; k < nblocks; k++) {
                            unsigned char b[32];
                            size_t bl;
                            if (conf_bytes(blocks[k].ssid, b, &bl) || !same(b, bl, ssid, slen)) continue;
                            if (blocks[k].other) snprintf(change, sizeof(change), "not kept: the network needs settings this service does not copy");
                            else if (keep(&blocks[k], change, sizeof(change))) store_changed = 1;
                            break;
                        }
                    }
                    /* Stock keeps one network: kept ones beside it (an earlier version of this service
                       added them back) go, all but the connected one or else the first, stock's. */
                    int stay = 0;
                    for (int k = 0; k < npresent; k++) if (present[k].current) stay = k;
                    for (int k = 0; nblocks > 1 && k < npresent; k++) {
                        unsigned char name[32];
                        size_t nl;
                        int ours = 0;
                        for (int m = 0; m < nstore; m++) ours |= !kept_bytes(&store[m], name, &nl) && same(name, nl, present[k].ssid, present[k].len);
                        if (k == stay || !ours) continue;
                        char answer[64], id[16];
                        snprintf(id, sizeof(id), "%d", present[k].id);
                        if (!wpa(answer, sizeof(answer), "remove_network", id, NULL) && ok(answer)) removed++; else failed++;
                    }
                    if (removed) {
                        char answer[64];
                        if (wpa(answer, sizeof(answer), "save_config", NULL) || !ok(answer)) failed++;
                        snprintf(change, sizeof(change), "stock keeps one network: removed %d an earlier version added", removed);
                    } else if (!connected && nblocks <= 1 && away >= AWAY_LOOKS && look - conf_look >= QUIET_LOOKS && !placed[0]) {
                        /* Out of reach: the strongest kept network in range, other than stock's own, takes its place. */
                        unsigned char own[32];
                        size_t ol = 0;
                        if (nblocks == 1 && conf_bytes(blocks[0].ssid, own, &ol)) ol = 0;
                        int best = -1, signal = 0;
                        for (int k = 0; k < nstore; k++) {
                            unsigned char name[32];
                            size_t nl;
                            if (kept_bytes(&store[k], name, &nl) || (nblocks == 1 && same(name, nl, own, ol)) || missed_lately(&store[k], look)) continue;
                            for (int m = 0; m < nrange; m++)
                                if (same(name, nl, range[m].ssid, range[m].len) && (best < 0 || range[m].signal > signal)) { best = k; signal = range[m].signal; }
                        }
                        if (best >= 0) {
                            unsigned char name[32];
                            size_t nl;
                            char words[40];
                            if (kept_bytes(&store[best], name, &nl)) nl = 0;
                            ascii_name(words, sizeof(words), name, nl);
                            if (take_place(&store[best])) { failed++; snprintf(change, sizeof(change), "could not switch to %s", words); }
                            else snprintf(change, sizeof(change), "switched to %s: in range, stock's network out of reach", words);
                            snprintf(placed, sizeof(placed), "%s", store[best].ssid);
                            placed_look = look;
                        } else if (look - scan_look >= SCAN_LOOKS) {
                            char answer[64];
                            wpa(answer, sizeof(answer), "scan", NULL);
                            scan_look = look;
                        }
                    }
                }
                if (store_changed) store_save();
                if (store_changed || removed || failed || placed_look == look) { changed = time(NULL); steady = 0; }
                fresh = 0;
            }
        } else { steady = 0; nbefore = -1; seen[0] = 0; fresh = 1; away = 0; }
        report(on, state, ssid, slen, present, npresent > 0 ? npresent : 0, range, nrange, change, changed);
        if (first) { first = 0; int fd = open(ready, O_WRONLY | O_CREAT | O_CLOEXEC, 0644); if (fd >= 0) close(fd); }
        double until = mono() + interval;
        while (!stopping && mono() < until) pause_s(until - mono() < 1 ? until - mono() : 1);
    }
    return 0;
}
