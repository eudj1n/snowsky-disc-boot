/* A package's package.json and the files it lists. */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE
#include "manifest.h"
#include "boot_util.h"
#include <dirent.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static int fail(char *err, size_t cap, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    vsnprintf(err, cap, fmt, ap);
    va_end(ap);
    return -1;
}

static int component_char(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || strchr("._+@-", c);
}

/* Relative, no "." or ".." components, at most 8 deep, conservative characters. */
static int path_ok(const char *p) {
    size_t n = strlen(p);
    if (n < 1 || n > 200 || p[0] == '/' || p[n - 1] == '/') return 0;
    int depth = 0;
    const char *c = p;
    while (*c) {
        const char *start = c;
        while (*c && *c != '/') { if (!component_char(*c)) return 0; c++; }
        size_t len = (size_t)(c - start);
        if (!len || len > 64 || (len == 1 && start[0] == '.') || (len == 2 && start[0] == '.' && start[1] == '.')) return 0;
        if (++depth > 8) return 0;
        if (*c) c++;
    }
    return strcmp(p, "package.json") != 0;
}

static int hex64(const char *s) {
    if (strlen(s) != 64) return 0;
    for (; *s; s++) if (!((*s >= '0' && *s <= '9') || (*s >= 'a' && *s <= 'f'))) return 0;
    return 1;
}

static int string_at(const bjson *j, const char *key, char *out, size_t cap, int required) {
    int v = bjson_find(j, 0, key);
    if (v == -1 && !required) return 1;
    return v < 0 || bjson_string(j, v, out, cap) ? -1 : 0;
}

int manifest_load(const char *dir, manifest *m, char *err, size_t cap) {
    memset(m, 0, sizeof(*m));
    char p[PATH_MAX];
    if (snprintf(p, sizeof(p), "%s/package.json", dir) >= (int)sizeof(p)) return fail(err, cap, "path too long");
    char *buf = malloc(MANIFEST_BYTES);
    if (!buf) return fail(err, cap, "out of memory");
    size_t len;
    if (read_small(p, buf, MANIFEST_BYTES, &len)) { free(buf); return fail(err, cap, "package.json missing or over 64 KiB"); }
    bjson j;
    if (bjson_parse(&j, buf, len, 4096)) { free(buf); return fail(err, cap, "package.json is not a JSON object"); }
    int r = -1, v;
    long long n;
    if ((v = bjson_find(&j, 0, "schema")) < 0 || bjson_int(&j, v, &n) || n != 1) { fail(err, cap, "schema must be 1"); goto done; }
    if (string_at(&j, "name", m->name, sizeof(m->name), 1) || !package_name_ok(m->name)) { fail(err, cap, "name must match [a-z0-9-]{1,32}"); goto done; }
    if (string_at(&j, "version", m->version, sizeof(m->version), 1) || !m->version[0]) { fail(err, cap, "version must be 1-64 printable ASCII"); goto done; }
    if (string_at(&j, "role", m->role, sizeof(m->role), 1) || (strcmp(m->role, "service") && strcmp(m->role, "ui") && strcmp(m->role, "menu"))) { fail(err, cap, "role must be service, ui or menu"); goto done; }
    if ((v = bjson_find(&j, 0, "bootApi")) < 0 || bjson_int(&j, v, &n) || n < 1 || n > 1000) { fail(err, cap, "bootApi must be a positive integer"); goto done; }
    m->boot_api = (int)n;
    if (string_at(&j, "arch", m->arch, sizeof(m->arch), 1) || !m->arch[0]) { fail(err, cap, "arch is required"); goto done; }
    if (string_at(&j, "entry", m->entry, sizeof(m->entry), 1) || !path_ok(m->entry)) { fail(err, cap, "entry must be a listed relative path"); goto done; }
    if ((v = string_at(&j, "player", m->player, sizeof(m->player), 0)) < 0 || (v == 0 && !path_ok(m->player))) { fail(err, cap, "player must be a listed relative path"); goto done; }
    m->ready = 30;
    if ((v = bjson_find(&j, 0, "ready")) != -1) {
        if (v < 0 || bjson_int(&j, v, &n) || n < 1 || n > MAX_READY) { fail(err, cap, "ready must be 1-120 seconds"); goto done; }
        m->ready = (int)n;
    }
    if ((v = bjson_find(&j, 0, "profiles")) < 0 || bjson_type(&j, v) != 'a' || bjson_size(&j, v) < 1 || bjson_size(&j, v) > MAX_PROFILES) { fail(err, cap, "profiles must list 1-8 firmware profiles"); goto done; }
    m->nprofiles = bjson_size(&j, v);
    for (int k = 0; k < m->nprofiles; k++)
        if (bjson_string(&j, bjson_item(&j, v, k), m->profiles[k], sizeof(m->profiles[k])) || !m->profiles[k][0]) { fail(err, cap, "a profile is not a short string"); goto done; }
    if ((v = bjson_find(&j, 0, "args")) != -1) {
        if (v < 0 || bjson_type(&j, v) != 'a' || bjson_size(&j, v) > MAX_ARGS) { fail(err, cap, "args must list at most 32 strings"); goto done; }
        m->nargs = bjson_size(&j, v);
        for (int k = 0; k < m->nargs; k++)
            if (bjson_string(&j, bjson_item(&j, v, k), m->args[k], sizeof(m->args[k]))) { fail(err, cap, "an argument is not a string of up to 256 printable ASCII"); goto done; }
    }
    int files = bjson_find(&j, 0, "files");
    if (files < 0 || bjson_type(&j, files) != 'o' || bjson_size(&j, files) < 1 || bjson_size(&j, files) > MAX_FILES) { fail(err, cap, "files must list 1-256 files"); goto done; }
    int k = files + 1;
    for (int f = 0; f < bjson_size(&j, files); f++) {
        pkg_file *pf = &m->files[f];
        if (bjson_string(&j, k, pf->path, sizeof(pf->path)) || !path_ok(pf->path)) { fail(err, cap, "file %d has an unsafe path", f + 1); goto done; }
        for (int g = 0; g < f; g++) if (!strcmp(m->files[g].path, pf->path)) { fail(err, cap, "%s is listed twice", pf->path); goto done; }
        int obj = k + 1;
        if (bjson_type(&j, obj) != 'o') { fail(err, cap, "%s needs size, sha256 and mode", pf->path); goto done; }
        char mode[8];
        int sz = bjson_find(&j, obj, "size"), sh = bjson_find(&j, obj, "sha256"), md = bjson_find(&j, obj, "mode");
        if (sz < 0 || bjson_int(&j, sz, &pf->size) || sh < 0 || bjson_string(&j, sh, pf->sha, sizeof(pf->sha)) || !hex64(pf->sha)
            || md < 0 || bjson_string(&j, md, mode, sizeof(mode)) || (strcmp(mode, "0755") && strcmp(mode, "0644"))) {
            fail(err, cap, "%s needs an integer size, a lower-case sha256 and mode 0755 or 0644", pf->path); goto done;
        }
        pf->mode = strcmp(mode, "0755") ? 0644 : 0755;
        m->total += pf->size;
        if (m->total > PACKAGE_BYTES) { fail(err, cap, "the package exceeds 32 MiB"); goto done; }
        k = bjson_skip(&j, obj);
        m->nfiles = f + 1;
    }
    int entry = -1;
    for (int f = 0; f < m->nfiles; f++) if (!strcmp(m->files[f].path, m->entry)) entry = f;
    if (entry < 0 || m->files[entry].mode != 0755) { fail(err, cap, "entry must be a listed file with mode 0755"); goto done; }
    /* Stock's watch loop finds the UI by its process name, and the menu runs in its place. */
    if (strcmp(m->role, "service")) {
        const char *base = strrchr(m->entry, '/');
        if (strcmp(base ? base + 1 : m->entry, "mq_ui")) { fail(err, cap, "a %s package's entry must be named mq_ui", m->role); goto done; }
    }
    if (m->player[0]) {
        int player = -1;
        for (int f = 0; f < m->nfiles; f++) if (!strcmp(m->files[f].path, m->player)) player = f;
        if (strcmp(m->role, "ui")) { fail(err, cap, "only a ui package brings a player launcher"); goto done; }
        if (player < 0 || m->files[player].mode != 0755) { fail(err, cap, "player must be a listed file with mode 0755"); goto done; }
    }
    r = 0;
done:
    bjson_free(&j);
    free(buf);
    return r;
}

int manifest_fits(const manifest *m, const char *role, const char *profile, char *err, size_t cap) {
    if (strcmp(m->role, role)) return fail(err, cap, "the package's role is %s, not %s", m->role, role);
    if (m->boot_api > BOOT_API) return fail(err, cap, "the package needs boot API %d (this boot layer has %d)", m->boot_api, BOOT_API);
    if (strcmp(m->arch, BOOT_ARCH)) return fail(err, cap, "the package is built for %s, not %s", m->arch, BOOT_ARCH);
    for (int k = 0; k < m->nprofiles; k++) if (!strcmp(m->profiles[k], profile)) return 0;
    return fail(err, cap, "the package does not support firmware profile %s", profile);
}

static int listed(const manifest *m, const char *rel) {
    for (int f = 0; f < m->nfiles; f++) if (!strcmp(m->files[f].path, rel)) return 1;
    return 0;
}

static int walk_extras(const char *root, const char *rel, const manifest *m, int depth, char *err, size_t cap) {
    char dir[PATH_MAX];
    if (snprintf(dir, sizeof(dir), "%s%s%s", root, *rel ? "/" : "", rel) >= (int)sizeof(dir)) return fail(err, cap, "path too long");
    DIR *d = opendir(dir);
    if (!d) return fail(err, cap, "cannot list %s", *rel ? rel : "the package");
    struct dirent *e;
    int r = 0;
    while (!r && (e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        char child[PATH_MAX], abs[PATH_MAX];
        if (snprintf(child, sizeof(child), "%s%s%s", rel, *rel ? "/" : "", e->d_name) >= (int)sizeof(child)
            || snprintf(abs, sizeof(abs), "%s/%s", root, child) >= (int)sizeof(abs)) { r = fail(err, cap, "path too long"); break; }
        struct stat s;
        if (lstat(abs, &s)) { r = fail(err, cap, "cannot read %s", child); break; }
        if (S_ISDIR(s.st_mode)) {
            if (depth >= 8) r = fail(err, cap, "%s is nested too deep", child);
            else r = walk_extras(root, child, m, depth + 1, err, cap);
        } else if (!S_ISREG(s.st_mode)) r = fail(err, cap, "%s is not a regular file", child);
        else if (!listed(m, child) && strcmp(child, "package.json")) r = fail(err, cap, "%s is not listed", child);
    }
    closedir(d);
    return r;
}

int package_verify(const char *dir, const manifest *m, int check_modes, char *err, size_t cap) {
    for (int f = 0; f < m->nfiles; f++) {
        const pkg_file *pf = &m->files[f];
        char abs[PATH_MAX], hex[65];
        long long size;
        struct stat s;
        if (snprintf(abs, sizeof(abs), "%s/%s", dir, pf->path) >= (int)sizeof(abs)) return fail(err, cap, "path too long");
        if (lstat(abs, &s) || !S_ISREG(s.st_mode)) return fail(err, cap, "%s is missing or not a regular file", pf->path);
        if (s.st_size != pf->size) return fail(err, cap, "%s has %lld bytes, not %lld", pf->path, (long long)s.st_size, pf->size);
        if (file_sha256(abs, hex, &size) || size != pf->size || strcmp(hex, pf->sha)) return fail(err, cap, "%s does not match its sha256", pf->path);
        if (check_modes && (int)(s.st_mode & 07777) != pf->mode) return fail(err, cap, "%s has mode %o, not %o", pf->path, (unsigned)(s.st_mode & 07777), (unsigned)pf->mode);
    }
    return walk_extras(dir, "", m, 0, err, cap);
}
