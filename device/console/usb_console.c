/* Opt-in engineering console. No network listener, stock mutation or command replay. */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/mount.h>
#include <sys/vfs.h>
#include <sys/prctl.h>
#endif

#define BASE "/sys/kernel/config/usb_gadget"
#define GADGET BASE "/disc_web_debug"
#define ACK "DISC_WEB_LOCAL_ROOT_CONSOLE\n"
static const char *fixture = "", *sd = NULL, *source = NULL, *udc = NULL;
static volatile sig_atomic_t interrupted;
static int created;
static const char *dirs[] = {"strings/0x409", "configs/c.1", "configs/c.1/strings/0x409", "functions/acm.0"};
static const char *attrs[][2] = {
    {"idVendor", "0x0525\n"}, {"idProduct", "0xa4a7\n"}, {"bcdUSB", "0x0200\n"},
    {"bcdDevice", "0x0100\n"}, {"bDeviceClass", "0x02\n"},
    {"strings/0x409/manufacturer", "DISC Web engineering\n"},
    {"strings/0x409/product", "Local root diagnostic console\n"},
    {"strings/0x409/serialnumber", "disc-web-debug\n"},
    {"configs/c.1/MaxPower", "120\n"}, {"configs/c.1/bmAttributes", "0x80\n"},
    {"configs/c.1/strings/0x409/configuration", "USB diagnostic console\n"}
};

static void path(char *out, const char *name) {
    if (snprintf(out, PATH_MAX, "%s%s", fixture, name) >= PATH_MAX) exit(2);
}
static void gadget_path(char *out, const char *name) {
    char rel[PATH_MAX];
    if (snprintf(rel, sizeof(rel), GADGET "/%s", name) >= (int)sizeof(rel)) exit(2);
    path(out, rel);
}
static double now(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) exit(2);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void tick(void) { struct timespec t = {0, 100000000}; nanosleep(&t, NULL); }
static void signal_stop(int sig) { (void)sig; interrupted = 1; }
static int exists(const char *name) { char p[PATH_MAX]; path(p, name); return access(p, F_OK) == 0; }
static int make_dir(const char *name) { char p[PATH_MAX]; path(p, name); return mkdir(p, 0700); }
static void remove_path(const char *name, int directory) {
    char p[PATH_MAX]; path(p, name);
    if (directory) (void)rmdir(p); else (void)unlink(p);
}
static int attribute(const char *name, const char *value) {
    char p[PATH_MAX]; gadget_path(p, name);
    int flags = O_WRONLY | O_TRUNC | O_CLOEXEC | O_NOFOLLOW;
#ifdef DISC_USB_FIXTURE
    if (exists("/run/disc-usb.fail-attribute")) return -1;
    flags |= O_CREAT;
#endif
    int fd = open(p, flags, 0600);
    if (fd < 0) { fprintf(stderr, "attribute open %s: %s\n", name, strerror(errno)); return -1; }
    size_t n = strlen(value);
    int ok = write(fd, value, n) == (ssize_t)n;
    close(fd); return ok ? 0 : -1;
}
static int mounted_card(void) {
    char p[PATH_MAX], line[1024], dev[256], target[256], fs[64];
    path(p, "/proc/mounts"); FILE *f = fopen(p, "r");
    if (!f) return 0;
    int ok = 0;
    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "%255s %255s %63s", dev, target, fs) == 3 &&
            !strcmp(dev, source) && !strcmp(target, sd) &&
            (!strcmp(fs, "vfat") || !strcmp(fs, "exfat") || !strcmp(fs, "ntfs") || !strcmp(fs, "fuseblk"))) ok++;
    }
    fclose(f); return ok == 1;
}
static int marker(void) {
    char p[PATH_MAX], rel[PATH_MAX], data[sizeof(ACK) + 1]; struct stat st;
    if (!mounted_card()) return 0;
    /* combined-008: the engineering switch lives in the service's folder on the card. */
    if (snprintf(rel, sizeof(rel), "%s/.disc/dev/usb-console", sd) >= (int)sizeof(rel)) return 0;
    path(p, rel);
    int fd = open(p, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return 0;
    ssize_t n = -1;
    if (!fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_size == (off_t)strlen(ACK)) n = read(fd, data, sizeof(data));
    close(fd); return n == (ssize_t)strlen(ACK) && !memcmp(data, ACK, strlen(ACK));
}
/* Revoked: the card is mounted and holds no marker. A card that is not mounted is no revocation:
   stock's player unmounts and mounts it again at every start of the pair, which is when a session
   matters most (owner's player, 2026-10-05: the pair restarting after the menu). */
static int revoked(void) { return mounted_card() && !marker(); }
static int foreign_gadget(void) {
    char p[PATH_MAX]; path(p, BASE); DIR *d = opendir(p);
    if (!d) return errno != ENOENT;
    struct dirent *e; int foreign = 0;
    while ((e = readdir(d))) {
        if (strcmp(e->d_name, ".") && strcmp(e->d_name, "..") &&
            !(created && !strcmp(e->d_name, "disc_web_debug"))) foreign = 1;
    }
    closedir(d); return foreign;
}
static int controller_ready(void) {
    char p[PATH_MAX]; path(p, "/sys/class/udc"); DIR *d = opendir(p);
    if (!d) return 0;
    struct dirent *e; int count = 0, matched = 0;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        count++; if (!strcmp(e->d_name, udc)) matched++;
    }
    closedir(d); return count == 1 && matched == 1;
}
static int configured(void) {
    char rel[PATH_MAX], p[PATH_MAX], state[64];
    if (snprintf(rel, sizeof(rel), "/sys/class/udc/%s/state", udc) >= (int)sizeof(rel)) return 0;
    path(p, rel); FILE *f = fopen(p, "r"); if (!f) return 0;
    int ok = fgets(state, sizeof(state), f) != NULL; fclose(f);
    if (ok) state[strcspn(state, "\r\n")] = 0;
    return ok && !strcmp(state, "configured");
}
static void cleanup(void) {
    if (!created) return;
    if (attribute("UDC", "\n")) fprintf(stderr, "unbind failed; inspect USB state before retry\n");
    remove_path(GADGET "/configs/c.1/acm.0", 0);
#ifdef DISC_USB_FIXTURE
    /* Regular files emulate kernel-owned attributes only in the CI build. */
    for (size_t i = 0; i < sizeof(attrs)/sizeof(attrs[0]); i++) {
        char p[PATH_MAX]; gadget_path(p, attrs[i][0]); (void)unlink(p);
    }
    remove_path(GADGET "/UDC", 0);
#endif
    for (size_t i = sizeof(dirs)/sizeof(dirs[0]); i > 0; i--) {
        char p[PATH_MAX]; gadget_path(p, dirs[i-1]); (void)rmdir(p);
#ifdef DISC_USB_FIXTURE
        if (i == 3) remove_path(GADGET "/configs/c.1/strings", 1);
#endif
    }
#ifdef DISC_USB_FIXTURE
    remove_path(GADGET "/functions", 1); remove_path(GADGET "/configs", 1); remove_path(GADGET "/strings", 1);
#endif
    remove_path(GADGET, 1);
    if (exists(GADGET)) fprintf(stderr, "gadget cleanup incomplete; reboot may be required\n");
    /* Never unmount global configfs or remove any stock gadget. */
}
static int setup(void) {
#if defined(__linux__) && !defined(DISC_USB_FIXTURE)
    struct statfs fs;
    if (statfs("/sys/kernel/config", &fs)) { perror("configfs statfs"); return -1; }
    if ((unsigned long)fs.f_type != 0x62656570UL && mount("none", "/sys/kernel/config", "configfs", 0, NULL)) { perror("configfs mount"); return -1; }
#elif !defined(DISC_USB_FIXTURE)
    return -1;
#endif
    if (foreign_gadget()) { fprintf(stderr, "gadget conflict\n"); return -1; }
    if (make_dir(GADGET)) { perror("gadget create"); return -1; }
    created = 1;
#ifdef DISC_USB_FIXTURE
    if (make_dir(GADGET "/strings") || make_dir(GADGET "/configs") || make_dir(GADGET "/functions")) return -1;
#endif
    for (size_t i = 0; i < sizeof(dirs)/sizeof(dirs[0]); i++) {
        char p[PATH_MAX]; gadget_path(p, dirs[i]); if (mkdir(p, 0700)) { fprintf(stderr, "mkdir %s: %s\n", dirs[i], strerror(errno)); return -1; }
#ifdef DISC_USB_FIXTURE
        if (i == 1 && make_dir(GADGET "/configs/c.1/strings")) return -1;
#endif
    }
    for (size_t i = 0; i < sizeof(attrs)/sizeof(attrs[0]); i++) if (attribute(attrs[i][0], attrs[i][1])) return -1;
    char link[PATH_MAX], target[PATH_MAX];
    gadget_path(link, "configs/c.1/acm.0"); gadget_path(target, "functions/acm.0");
    /* configfs looks up the supplied target from the caller's cwd before
       creating its own relative link. Use an absolute path regardless of cwd. */
#ifdef DISC_USB_FIXTURE
    /* A regular filesystem accepts dangling targets; model configfs lookup. */
    if (access(target, F_OK)) { perror("ACM target lookup"); return -1; }
#endif
    if (symlink(target, link)) { perror("ACM link"); return -1; }
    if (foreign_gadget() || !marker()) return -1;
    return attribute("UDC", udc);
}
static int still_bound(void) {
    char p[PATH_MAX], data[256]; gadget_path(p, "UDC");
    FILE *f = fopen(p, "r"); if (!f) return 0;
    int ok = fgets(data, sizeof(data), f) != NULL; fclose(f);
    if (ok) data[strcspn(data, "\r\n")] = 0;
    return ok && !strcmp(data, udc);
}
static void stop_child(pid_t child) {
    /* The child has not been reaped: its PID cannot refer to an unrelated process. */
    (void)kill(-child, SIGTERM); (void)kill(child, SIGTERM);
    double end = now() + 2;
    while (now() < end) { if (waitpid(child, NULL, WNOHANG) == child) return; tick(); }
    (void)kill(-child, SIGKILL); (void)kill(child, SIGKILL);
    end = now() + 1;
    while (now() < end) { if (waitpid(child, NULL, WNOHANG) == child) return; tick(); }
    /* A driver can keep even a killed child in kernel I/O. Unbind our gadget
       and return instead of turning optional diagnostics into an unbounded wait. */
    fprintf(stderr, "child reap pending after SIGKILL\n");
}
static int seconds(const char *value, int max) {
    char *end; long n = strtol(value, &end, 10);
    return *value && !*end && n > 0 && n <= max ? (int)n : 0;
}
int main(int argc, char **argv) {
    int startup = 0, session = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--sd-mount")) sd = argv[i+1];
        else if (!strcmp(argv[i], "--sd-source")) source = argv[i+1];
        else if (!strcmp(argv[i], "--udc")) udc = argv[i+1];
        else if (!strcmp(argv[i], "--startup-seconds")) startup = seconds(argv[i+1], 120);
        else if (!strcmp(argv[i], "--session-seconds")) session = seconds(argv[i+1], 900);
#ifdef DISC_USB_FIXTURE
        else if (!strcmp(argv[i], "--fixture-root")) fixture = argv[i+1];
#endif
        else return 2;
    }
    if (!(argc & 1) || !sd || !source || !udc || !startup || !session || sd[0] != '/' || strlen(udc) > 200) return 2;
#ifdef DISC_USB_FIXTURE
    if (!*fixture || fixture[0] != '/') return 2;
#endif
    struct sigaction sa; memset(&sa, 0, sizeof(sa)); sa.sa_handler = signal_stop;
    sigemptyset(&sa.sa_mask); sigaction(SIGTERM, &sa, NULL); sigaction(SIGINT, &sa, NULL); sigaction(SIGHUP, &sa, NULL);
    char p[PATH_MAX]; path(p, "/run/disc-usb.lock");
    int lock = open(p, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (lock < 0) { perror("supervisor lock"); return 1; }
    if (flock(lock, LOCK_EX | LOCK_NB)) { close(lock); return 0; }
    remove_path("/run/disc-usb.stop", 0);
    double deadline = now() + startup;
    const char *reason = "startup deadline";
    fprintf(stderr, "supervisor entered: sd=%s source=%s udc=%s startup=%d session=%d\n", sd, source, udc, startup, session);
    int previous = -1, changes = 0;
    while (!interrupted && !exists("/run/disc-usb.stop") && now() < deadline) {
        int mounted = mounted_card(), controller = controller_ready();
        int state = mounted | (controller << 1);
        if (state != previous && changes++ < 16) {
            fprintf(stderr, "readiness: mounted=%d controller=%d\n", mounted, controller);
            previous = state;
        }
        if (mounted) {
            if (!marker()) { reason = "not opted in"; goto done; }
            if (foreign_gadget()) { reason = "stock USB owns port"; goto done; }
            if (controller) break;
        }
        tick();
    }
    if (interrupted || exists("/run/disc-usb.stop") || now() >= deadline) goto done;
    if (setup()) { reason = "gadget setup failed"; goto done; }
    while ((!exists("/dev/ttyGS0") || !configured()) && now() < deadline && !interrupted && marker() && !foreign_gadget() && !exists("/run/disc-usb.stop")) tick();
    path(p, "/dev/ttyGS0"); struct stat tty;
#ifdef DISC_USB_FIXTURE
    int typed = stat(p, &tty);
#else
    int typed = lstat(p, &tty);
#endif
    if (typed || !S_ISCHR(tty.st_mode) || !configured() || interrupted || now() >= deadline || !marker() || foreign_gadget() || exists("/run/disc-usb.stop")) {
        reason = "console unavailable"; goto done;
    }
    pid_t parent = getpid(), child = fork();
    if (child < 0) { reason = "fork failed"; goto done; }
    if (child == 0) {
#ifdef __linux__
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parent) _exit(1);
#else
        (void)parent;
#endif
        if (setsid() < 0) _exit(1);
        int fd = open(p, O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (fd < 0 || ioctl(fd, TIOCSCTTY, 0)) _exit(1);
        int flags = fcntl(fd, F_GETFL);
        if (flags < 0 || fcntl(fd, F_SETFL, flags & ~O_NONBLOCK)) _exit(1);
        for (int i = 0; i < 3; i++) if (dup2(fd, i) < 0) _exit(1);
        if (fd > 2) close(fd);
        close(lock);
        unsetenv("ENV"); unsetenv("BASH_ENV"); unsetenv("LD_PRELOAD"); unsetenv("LD_LIBRARY_PATH");
        execl("/bin/sh", "sh", "-i", (char *)NULL); _exit(1);
    }
    fprintf(stderr, "console started\n"); fflush(stderr);
    deadline = now() + session; reason = "session expired or revoked";
    while (!interrupted && now() < deadline && !revoked() && !foreign_gadget() && configured() && still_bound() && !exists("/run/disc-usb.stop")) {
        if (waitpid(child, NULL, WNOHANG) == child) { child = -1; reason = "console exited"; break; }
        tick();
    }
    if (child > 0) stop_child(child);
done:
    cleanup(); fprintf(stderr, "stopped: %s\n", reason); close(lock); return 0;
}
