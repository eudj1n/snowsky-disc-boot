/* Files, JSON and state of the boot layer. */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE
#define JSMN_STATIC
#define JSMN_STRICT
#include "jsmn.h" /* implementation first; the header's guarded re-include is a no-op */
#include "boot_util.h"
#include "sha256.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

char boot_root[PATH_MAX];

void bpath(char out[PATH_MAX], const char *fmt, ...) {
    char rel[PATH_MAX];
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(rel, sizeof(rel), fmt, ap);
    va_end(ap);
    if (n < 0 || n >= (int)sizeof(rel) || snprintf(out, PATH_MAX, "%s%s", boot_root, rel) >= PATH_MAX) {
        fprintf(stderr, "disc-boot: path too long\n"); exit(2);
    }
}

double mono(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

void pause_s(double seconds) {
    struct timespec t = {(time_t)seconds, (long)((seconds - (time_t)seconds) * 1e9)};
    while (nanosleep(&t, &t) && errno == EINTR) {}
}

void blog(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    fprintf(stderr, "disc-boot: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

int exists(const char *abs) { struct stat s; return lstat(abs, &s) == 0; }
int is_dir(const char *abs) { struct stat s; return lstat(abs, &s) == 0 && S_ISDIR(s.st_mode); }

int read_small(const char *abs, char *buf, size_t cap, size_t *len) {
    int fd = open(abs, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat s;
    if (fstat(fd, &s) || !S_ISREG(s.st_mode) || s.st_size < 0 || (size_t)s.st_size >= cap) { close(fd); return -1; }
    size_t got = 0;
    while (got < (size_t)s.st_size) {
        ssize_t r = read(fd, buf + got, (size_t)s.st_size - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) { close(fd); return -1; }
        got += (size_t)r;
    }
    close(fd);
    buf[got] = 0;
    if (len) *len = got;
    return 0;
}

static int sync_dir_of(const char *abs) {
    char dir[PATH_MAX];
    snprintf(dir, sizeof(dir), "%s", abs);
    char *slash = strrchr(dir, '/');
    if (!slash) return 0;
    if (slash == dir) slash[1] = 0; else *slash = 0;
    int fd = open(dir, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    int r = fsync(fd);
    close(fd);
    return r;
}

static int write_all(int fd, const char *data, size_t len) {
    while (len) {
        ssize_t w = write(fd, data, len);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return -1;
        data += w; len -= (size_t)w;
    }
    return 0;
}

int write_atomic(const char *abs, const char *data, size_t len, mode_t mode) {
    char tmp[PATH_MAX];
    if (snprintf(tmp, sizeof(tmp), "%s.new", abs) >= (int)sizeof(tmp)) return -1;
    (void)unlink(tmp);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, mode);
    if (fd < 0) return -1;
    if (write_all(fd, data, len) || fsync(fd)) { close(fd); unlink(tmp); return -1; }
    close(fd);
    if (rename(tmp, abs)) { unlink(tmp); return -1; }
    return sync_dir_of(abs);
}

int mkdirs(const char *abs, mode_t mode) {
    char p[PATH_MAX];
    if (snprintf(p, sizeof(p), "%s", abs) >= (int)sizeof(p)) return -1;
    for (char *c = p + 1; ; c++) {
        if (*c == '/' || !*c) {
            char saved = *c; *c = 0;
            struct stat s;
            if (lstat(p, &s)) { if (mkdir(p, mode) && errno != EEXIST) return -1; }
            else if (!S_ISDIR(s.st_mode)) return -1;
            *c = saved;
            if (!saved) break;
        }
    }
    return 0;
}

static int remove_at(const char *abs, int depth) {
    struct stat s;
    if (lstat(abs, &s)) return errno == ENOENT ? 0 : -1;
    if (!S_ISDIR(s.st_mode)) return unlink(abs);
    if (depth > 16) return -1;
    DIR *d = opendir(abs);
    if (!d) return -1;
    struct dirent *e;
    int r = 0;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        char child[PATH_MAX];
        if (snprintf(child, sizeof(child), "%s/%s", abs, e->d_name) >= (int)sizeof(child) || remove_at(child, depth + 1)) r = -1;
    }
    closedir(d);
    return r || rmdir(abs) ? -1 : 0;
}
int remove_tree(const char *abs) { return remove_at(abs, 0); }

int copy_file(const char *src, const char *dst, mode_t mode) {
    int in = open(src, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (in < 0) return -1;
    struct stat s;
    if (fstat(in, &s) || !S_ISREG(s.st_mode)) { close(in); return -1; }
    int out = open(dst, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (out < 0) { close(in); return -1; }
    char buf[65536];
    int r = 0;
    for (;;) {
        ssize_t n = read(in, buf, sizeof(buf));
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) { r = -1; break; }
        if (!n) break;
        if (write_all(out, buf, (size_t)n)) { r = -1; break; }
    }
    if (!r && (fchmod(out, mode) || fsync(out))) r = -1;
    close(in); close(out);
    if (r) unlink(dst);
    return r;
}

int file_sha256(const char *abs, char hex[65], long long *size) {
    int fd = open(abs, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat s;
    if (fstat(fd, &s) || !S_ISREG(s.st_mode)) { close(fd); return -1; }
    sha256_ctx c; sha256_init(&c);
    char buf[65536];
    long long total = 0;
    for (;;) {
        ssize_t n = read(fd, buf, sizeof(buf));
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) { close(fd); return -1; }
        if (!n) break;
        sha256_update(&c, buf, (size_t)n); total += n;
    }
    close(fd);
    uint8_t d[32]; sha256_final(&c, d); sha256_hex(d, hex);
    if (size) *size = total;
    return 0;
}

void json_str(char *out, size_t cap, const char *s) {
    size_t o = 0;
    if (cap < 3) { if (cap) *out = 0; return; }
    out[o++] = '"';
    for (; *s && o + 3 < cap; s++) {
        unsigned char c = (unsigned char)*s;
        if (c < 0x20 || c > 0x7e) continue;
        if (c == '"' || c == '\\') { if (o + 4 >= cap) break; out[o++] = '\\'; }
        out[o++] = (char)c;
    }
    out[o++] = '"'; out[o] = 0;
}

int bjson_parse(bjson *j, const char *js, size_t len, int max_tokens) {
    j->js = js; j->n = 0;
    j->t = calloc((size_t)max_tokens, sizeof(jsmntok_t));
    if (!j->t) return -1;
    jsmn_parser p; jsmn_init(&p);
    int n = jsmn_parse(&p, js, len, j->t, (unsigned)max_tokens);
    if (n < 1 || j->t[0].type != JSMN_OBJECT) { bjson_free(j); return -1; }
    j->n = n;
    return 0;
}

void bjson_free(bjson *j) { free(j->t); j->t = NULL; j->n = 0; }

int bjson_skip(const bjson *j, int i) {
    if (i < 0 || i >= j->n) return j->n;
    int count = j->t[i].type == JSMN_OBJECT ? 2 * j->t[i].size : j->t[i].type == JSMN_ARRAY ? j->t[i].size : 0;
    int k = i + 1;
    while (count-- > 0) k = bjson_skip(j, k);
    return k;
}

static int key_is(const bjson *j, int i, const char *key) {
    size_t n = strlen(key);
    return j->t[i].type == JSMN_STRING && (size_t)(j->t[i].end - j->t[i].start) == n && !memcmp(j->js + j->t[i].start, key, n);
}

int bjson_find(const bjson *j, int obj, const char *key) {
    if (obj < 0 || obj >= j->n || j->t[obj].type != JSMN_OBJECT) return -1;
    int found = -1, k = obj + 1;
    for (int pair = 0; pair < j->t[obj].size && k < j->n; pair++) {
        if (key_is(j, k, key)) { if (found >= 0) return -2; found = k + 1; }
        k = bjson_skip(j, k + 1);
    }
    return found;
}

char bjson_type(const bjson *j, int i) {
    if (i < 0 || i >= j->n) return 0;
    switch (j->t[i].type) {
    case JSMN_OBJECT: return 'o';
    case JSMN_ARRAY: return 'a';
    case JSMN_STRING: return 's';
    case JSMN_PRIMITIVE: return 'p';
    default: return 0;
    }
}

int bjson_size(const bjson *j, int i) { return i >= 0 && i < j->n ? j->t[i].size : 0; }

int bjson_item(const bjson *j, int array, int k) {
    if (array < 0 || array >= j->n || j->t[array].type != JSMN_ARRAY || k < 0 || k >= j->t[array].size) return -1;
    int i = array + 1;
    while (k-- > 0) i = bjson_skip(j, i);
    return i;
}

int bjson_string(const bjson *j, int i, char *out, size_t cap) {
    if (i < 0 || i >= j->n || j->t[i].type != JSMN_STRING) return -1;
    size_t o = 0;
    for (int p = j->t[i].start; p < j->t[i].end; p++) {
        char c = j->js[p];
        if (c == '\\') {
            if (++p >= j->t[i].end) return -1;
            c = j->js[p];
            if (c != '"' && c != '\\' && c != '/') return -1;
        }
        if ((unsigned char)c < 0x20 || (unsigned char)c > 0x7e || o + 1 >= cap) return -1;
        out[o++] = c;
    }
    out[o] = 0;
    return 0;
}

int bjson_int(const bjson *j, int i, long long *out) {
    if (i < 0 || i >= j->n || j->t[i].type != JSMN_PRIMITIVE) return -1;
    int len = j->t[i].end - j->t[i].start;
    const char *s = j->js + j->t[i].start;
    if (len < 1 || len > 15) return -1;
    long long v = 0;
    for (int k = 0; k < len; k++) {
        if (s[k] < '0' || s[k] > '9') return -1;
        v = v * 10 + (s[k] - '0');
    }
    *out = v;
    return 0;
}

int bjson_bool(const bjson *j, int i, int *out) {
    if (i < 0 || i >= j->n || j->t[i].type != JSMN_PRIMITIVE) return -1;
    const char *s = j->js + j->t[i].start;
    int len = j->t[i].end - j->t[i].start;
    if (len == 4 && !memcmp(s, "true", 4)) { *out = 1; return 0; }
    if (len == 5 && !memcmp(s, "false", 5)) { *out = 0; return 0; }
    return -1;
}

int bjson_null(const bjson *j, int i) {
    return i >= 0 && i < j->n && j->t[i].type == JSMN_PRIMITIVE && j->t[i].end - j->t[i].start == 4 && !memcmp(j->js + j->t[i].start, "null", 4);
}

int package_name_ok(const char *s) {
    size_t n = strlen(s);
    if (n < 1 || n > 32) return 0;
    for (; *s; s++) if (!((*s >= 'a' && *s <= 'z') || (*s >= '0' && *s <= '9') || *s == '-')) return 0;
    return 1;
}

/* An optional choice of UI: absent or null is none; otherwise "stock" or a package's name. */
static int ui_value(const bjson *j, const char *key, char out[33]) {
    int v = bjson_find(j, 0, key);
    out[0] = 0;
    if (v == -1 || bjson_null(j, v)) return 0;
    return v < 0 || bjson_string(j, v, out, 33) || (strcmp(out, "stock") && !package_name_ok(out)) ? -1 : 0;
}

int gstate_read(global_state *g) {
    char p[PATH_MAX], buf[SMALL_FILE];
    snprintf(g->mode, sizeof(g->mode), "platform");
    g->unconfirmed = 0; g->ui[0] = g->next[0] = 0;
    bpath(p, DATA_DIR "/state.json");
    size_t len;
    if (read_small(p, buf, sizeof(buf), &len)) return exists(p) ? -1 : 0;
    bjson j;
    long long n = 0;
    char mode[9];
    int m = -1, u = -1;
    if (bjson_parse(&j, buf, len, 32)) return -1;
    int bad = (m = bjson_find(&j, 0, "default")) < 0 || bjson_string(&j, m, mode, sizeof(mode))
        || (strcmp(mode, "platform") && strcmp(mode, "stock")) || (u = bjson_find(&j, 0, "unconfirmed")) < 0 || bjson_int(&j, u, &n) || n > 1000
        || ui_value(&j, "ui", g->ui) || ui_value(&j, "next", g->next);
    bjson_free(&j);
    if (bad) { g->ui[0] = g->next[0] = 0; return -1; }
    snprintf(g->mode, sizeof(g->mode), "%s", mode);
    g->unconfirmed = (int)n;
    return 0;
}

int gstate_write(const global_state *g) {
    char p[PATH_MAX], buf[256];
    bpath(p, DATA_DIR);
    if (mkdirs(p, 0755)) return -1;
    bpath(p, DATA_DIR "/state.json");
    char ui[40], next[40];
    snprintf(ui, sizeof(ui), g->ui[0] ? "\"%s\"" : "null", g->ui);
    snprintf(next, sizeof(next), g->next[0] ? "\"%s\"" : "null", g->next);
    int n = snprintf(buf, sizeof(buf), "{\"schema\":1,\"default\":\"%s\",\"unconfirmed\":%d,\"ui\":%s,\"next\":%s}\n",
                     g->mode, g->unconfirmed, ui, next);
    return write_atomic(p, buf, (size_t)n, 0644);
}

static int slot_value(const bjson *j, int i, char *out) {
    char s[4];
    if (bjson_null(j, i)) { *out = 0; return 0; }
    if (bjson_string(j, i, s, sizeof(s)) || (strcmp(s, "a") && strcmp(s, "b"))) return -1;
    *out = s[0];
    return 0;
}

int rstate_read(const char *domain, role_state *r) {
    char p[PATH_MAX], buf[SMALL_FILE];
    r->current = r->previous = 0; r->confirmed = 0; r->previous_manifest[0] = 0;
    bpath(p, DATA_DIR "/%s/state.json", domain);
    size_t len;
    if (read_small(p, buf, sizeof(buf), &len)) return exists(p) ? -1 : 0;
    bjson j;
    int c, pv, cf, pm;
    if (bjson_parse(&j, buf, len, 32)) return -1;
    int bad = (c = bjson_find(&j, 0, "current")) < 0 || slot_value(&j, c, &r->current)
        || (pv = bjson_find(&j, 0, "previous")) < 0 || slot_value(&j, pv, &r->previous)
        || (cf = bjson_find(&j, 0, "confirmed")) < 0 || bjson_bool(&j, cf, &r->confirmed)
        || (r->previous && r->previous == r->current) || (!r->current && (r->previous || r->confirmed));
    /* Optional: absent or null leaves the previous slot without a fingerprint, so no rollback to it. */
    if (!bad && (pm = bjson_find(&j, 0, "previousManifest")) != -1 && (pm < 0 || !bjson_null(&j, pm))) {
        bad = bjson_string(&j, pm, r->previous_manifest, sizeof(r->previous_manifest)) || strlen(r->previous_manifest) != 64;
        for (int i = 0; !bad && i < 64; i++) bad = !strchr("0123456789abcdef", r->previous_manifest[i]);
    }
    bjson_free(&j);
    if (bad) { r->current = r->previous = 0; r->confirmed = 0; r->previous_manifest[0] = 0; return -1; }
    if (!r->previous) r->previous_manifest[0] = 0;
    return 0;
}

int rstate_write(const char *domain, const role_state *r) {
    char p[PATH_MAX], buf[256], cur[8], prev[8], manifest[72];
    bpath(p, DATA_DIR "/%s", domain);
    if (mkdirs(p, 0755)) return -1;
    bpath(p, DATA_DIR "/%s/state.json", domain);
    snprintf(cur, sizeof(cur), r->current ? "\"%c\"" : "null", r->current);
    snprintf(prev, sizeof(prev), r->previous ? "\"%c\"" : "null", r->previous);
    if (r->previous && r->previous_manifest[0]) snprintf(manifest, sizeof(manifest), "\"%.64s\"", r->previous_manifest);
    else snprintf(manifest, sizeof(manifest), "null");
    int n = snprintf(buf, sizeof(buf), "{\"schema\":1,\"current\":%s,\"confirmed\":%s,\"previous\":%s,\"previousManifest\":%s}\n",
                     cur, r->confirmed ? "true" : "false", prev, manifest);
    return write_atomic(p, buf, (size_t)n, 0644);
}

int state_lock(void) {
    char p[PATH_MAX];
    bpath(p, DATA_DIR);
    if (mkdirs(p, 0755)) return -1;
    bpath(p, DATA_DIR "/.lock");
    int fd = open(p, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return -1;
    if (flock(fd, LOCK_EX)) { close(fd); return -1; }
    return fd;
}

void state_unlock(int fd) { if (fd >= 0) { flock(fd, LOCK_UN); close(fd); } }
