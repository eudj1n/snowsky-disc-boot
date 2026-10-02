/* SHA-256 against FIPS 180-4 example vectors. */
#include "sha256.h"
#include <stdio.h>
#include <string.h>

static int check(const char *label, const void *data, size_t n, size_t repeat, const char *expected) {
    sha256_ctx c; uint8_t d[32]; char hex[65];
    sha256_init(&c);
    for (size_t k = 0; k < repeat; k++) sha256_update(&c, data, n);
    sha256_final(&c, d); sha256_hex(d, hex);
    if (strcmp(hex, expected)) { fprintf(stderr, "%s: %s != %s\n", label, hex, expected); return 1; }
    return 0;
}

int main(void) {
    int bad = 0;
    bad |= check("empty", "", 0, 1, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    bad |= check("abc", "abc", 3, 1, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    bad |= check("448 bits", "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", 56, 1,
                 "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    bad |= check("896 bits", "abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu", 112, 1,
                 "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1");
    bad |= check("a million a", "aaaaaaaaaa", 10, 100000, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    if (!bad) puts("sha256: 5 vectors OK");
    return bad;
}
