/* SHA-256 (FIPS 180-4) for verifying package files; no other use. */
#ifndef DISC_BOOT_SHA256_H
#define DISC_BOOT_SHA256_H
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t state[8];
    uint64_t length;
    uint8_t block[64];
    size_t used;
} sha256_ctx;

void sha256_init(sha256_ctx *c);
void sha256_update(sha256_ctx *c, const void *data, size_t n);
void sha256_final(sha256_ctx *c, uint8_t out[32]);
/* Lower-case hex of the digest; out holds 65 bytes. */
void sha256_hex(const uint8_t digest[32], char out[65]);
#endif
