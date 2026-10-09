/* A package's package.json and the files it lists (docs/dev/contract.md, "Packages"). */
#ifndef DISC_BOOT_MANIFEST_H
#define DISC_BOOT_MANIFEST_H
#include <stddef.h>
#define MAX_FILES 256
#define MAX_ARGS 32
#define MAX_PROFILES 8
#define MANIFEST_BYTES 65536
#define PACKAGE_BYTES (32LL * 1024 * 1024)
#define MAX_READY 120

#ifdef DISC_BOOT_FIXTURE
#define BOOT_ARCH "fixture"
#elif defined(__mips__) && defined(__MIPSEL__)
#define BOOT_ARCH "mips32el-linux-static"
#else
#define BOOT_ARCH "host"
#endif

typedef struct { char path[201]; long long size; char sha[65]; int mode; } pkg_file;
typedef struct {
    /* player: a ui package's own launcher of stock's player ("" when it brings none);
       title: the name a menu shows ("" when none: the menu shows the name);
       homepage: the project's page the status names ("" when none or not a plain https address). */
    char name[33], version[65], role[8], arch[48], entry[201], player[201], title[33], homepage[201];
    int boot_api, ready, nargs, nprofiles, nfiles;
    char args[MAX_ARGS][257];
    char profiles[MAX_PROFILES][17];
    pkg_file files[MAX_FILES];
    long long total;
} manifest;

/* Reads and validates dir/package.json; err names the first problem. */
int manifest_load(const char *dir, manifest *m, char *err, size_t cap);
/* The package fits this boot layer: role, API, architecture and firmware profile. */
int manifest_fits(const manifest *m, const char *role, const char *profile, char *err, size_t cap);
/* Every listed file is a regular file of its size and digest (and mode, when asked);
   nothing else but package.json and folders lies in dir. */
int package_verify(const char *dir, const manifest *m, int check_modes, char *err, size_t cap);
#endif
