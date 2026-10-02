/* Files, JSON and state of the boot layer (docs/contract.md). */
#ifndef DISC_BOOT_UTIL_H
#define DISC_BOOT_UTIL_H
#include <limits.h>
#include <stddef.h>
#include <sys/types.h>

#define BOOT_API 1
#define DATA_DIR "/usr/data/disc-boot"
#define RUN_DIR "/run/disc-boot"
#define SMALL_FILE 4096

/* The fixture root ("" on a player): every absolute path the program touches starts with it. */
extern char boot_root[PATH_MAX];
void bpath(char out[PATH_MAX], const char *fmt, ...);
double mono(void);
void pause_s(double seconds);
void blog(const char *fmt, ...);

int exists(const char *abs);
int is_dir(const char *abs);
/* A regular file (never a link) smaller than cap, NUL-terminated. */
int read_small(const char *abs, char *buf, size_t cap, size_t *len);
/* Written beside the target, synced, renamed into place, the folder synced. */
int write_atomic(const char *abs, const char *data, size_t len, mode_t mode);
int mkdirs(const char *abs, mode_t mode);
/* Removes a tree without following links; refuses depth beyond 16. */
int remove_tree(const char *abs);
int copy_file(const char *src, const char *dst, mode_t mode);
int file_sha256(const char *abs, char hex[65], long long *size);
/* A JSON string literal (quotes included) of printable ASCII. */
void json_str(char *out, size_t cap, const char *s);

struct jsmntok;
/* Parsed JSON whose root is an object; tokens are owned until bjson_free. */
typedef struct { const char *js; struct jsmntok *t; int n; } bjson;
int bjson_parse(bjson *j, const char *js, size_t len, int max_tokens);
void bjson_free(bjson *j);
int bjson_skip(const bjson *j, int i);
/* The value of key in object obj: -1 when absent, -2 when the key repeats. */
int bjson_find(const bjson *j, int obj, const char *key);
int bjson_item(const bjson *j, int array, int k);
/* 'o' object, 'a' array, 's' string, 'p' primitive, 0 out of range; size counts members or items. */
char bjson_type(const bjson *j, int i);
int bjson_size(const bjson *j, int i);
/* Printable ASCII strings only; \uXXXX is refused. */
int bjson_string(const bjson *j, int i, char *out, size_t cap);
int bjson_int(const bjson *j, int i, long long *out);
int bjson_bool(const bjson *j, int i, int *out);
int bjson_null(const bjson *j, int i);

typedef struct { char mode[9]; int unconfirmed; } global_state;
/* previous_manifest: the SHA-256 of the previous slot's package.json when it became the
   rollback target; a slot rewritten since (an update staged there) is no rollback target. */
typedef struct { char current, previous; int confirmed; char previous_manifest[65]; } role_state;
int gstate_read(global_state *g);
int gstate_write(const global_state *g);
/* 0 with current 0 when the role has nothing installed; -1 when its state is unreadable. */
int rstate_read(const char *role, role_state *r);
int rstate_write(const char *role, const role_state *r);
int state_lock(void);
void state_unlock(int fd);
#endif
