/* The staging check (plan, stage 4c; staging.h). Freestanding: no libc, element-by-element
   copies only (an aggregate initializer may become a memcpy). */
#include "staging.h"

/* A request's address as the payload reaches it; a host test maps it into its own buffer. */
#ifndef STAGING_POINTER
#define STAGING_POINTER(address) ((uintptr_t)(address))
#endif

static void barrier(void) {
#if defined(__mips__)
    __asm__ __volatile__("sync" ::: "memory");
#endif
}

static uint32_t next(uint32_t x) {
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return x;
}

/* Every word of the region from the seed's sequence (or its complement), all written before any
   is read back, so an alias inside the region shows. Returns how many words matched before the
   first that differs (count when all did). */
static uint32_t pattern_pass(volatile uint32_t *words, uint32_t count, uint32_t seed, uint32_t invert) {
    uint32_t x = seed;
    for (uint32_t i = 0; i < count; i++) { x = next(x); words[i] = x ^ invert; }
    barrier();
    x = seed;
    for (uint32_t i = 0; i < count; i++) {
        x = next(x);
        if (words[i] != (x ^ invert)) return i;
    }
    return count;
}

static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

static uint32_t ror(uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); }

static void compress(uint32_t h[8], const uint32_t block[16]) {
    uint32_t w[64], a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], k = h[7];
    for (unsigned i = 0; i < 16; i++) w[i] = block[i];
    for (unsigned i = 16; i < 64; i++) {
        uint32_t s0 = ror(w[i - 15], 7) ^ ror(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ror(w[i - 2], 17) ^ ror(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    for (unsigned i = 0; i < 64; i++) {
        uint32_t t1 = k + (ror(e, 6) ^ ror(e, 11) ^ ror(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
        uint32_t t2 = (ror(a, 2) ^ ror(a, 13) ^ ror(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
        k = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += k;
}

/* The region's bytes as SHA-256 sees them: each little-endian word read once, its bytes taken in
   memory order (a big-endian word for the algorithm). The length is a whole number of blocks. */
static void hash(const volatile uint32_t *words, uint32_t length, uint8_t out[32]) {
    uint32_t h[8], block[16];
    h[0] = 0x6a09e667; h[1] = 0xbb67ae85; h[2] = 0x3c6ef372; h[3] = 0xa54ff53a;
    h[4] = 0x510e527f; h[5] = 0x9b05688c; h[6] = 0x1f83d9ab; h[7] = 0x5be0cd19;
    for (uint32_t i = 0; i < length / 4; i += 16) {
        for (unsigned j = 0; j < 16; j++) {
            uint32_t v = words[i + j];
            block[j] = (v >> 24) | ((v >> 8) & 0xff00u) | ((v << 8) & 0xff0000u) | (v << 24);
        }
        compress(h, block);
    }
    block[0] = 0x80000000u;
    for (unsigned j = 1; j < 14; j++) block[j] = 0;
    block[14] = length >> 29;
    block[15] = length << 3;
    compress(h, block);
    for (unsigned i = 0; i < 8; i++) {
        out[4 * i] = (uint8_t)(h[i] >> 24); out[4 * i + 1] = (uint8_t)(h[i] >> 16);
        out[4 * i + 2] = (uint8_t)(h[i] >> 8); out[4 * i + 3] = (uint8_t)h[i];
    }
}

void staging_run(const struct staging_request *q, volatile struct staging_result *out) {
    out->magic = 0;
    barrier();
    out->version = 1;
    out->op = q->op;
    out->code = STAGING_INVALID;
    for (unsigned j = 0; j < 4; j++) out->nonce[j] = q->nonce[j];
    out->address = q->address;
    out->length = q->length;
    out->checked = 0;
    out->bad_address = 0;
    for (unsigned j = 0; j < 32; j++) out->sha256[j] = 0;
    int valid = q->magic == STAGING_REQUEST_MAGIC && q->version == 1 && q->reserved[0] == 0 && q->reserved[1] == 0
                && q->address >= STAGING_DRAM_BASE && q->address % 4 == 0 && q->length > 0 && q->length % 64 == 0
                && q->length <= STAGING_DRAM_END - q->address;
    if (valid && q->op == STAGING_OP_PATTERN && q->seed != 0) {
        volatile uint32_t *words = (volatile uint32_t *)STAGING_POINTER(q->address);
        uint32_t count = q->length / 4, first = pattern_pass(words, count, q->seed, 0), second = 0;
        if (first == count) second = pattern_pass(words, count, q->seed, 0xffffffffu);
        out->checked = first + second;
        out->code = first == count && second == count ? STAGING_OK : STAGING_MISMATCH;
        if (out->code != STAGING_OK) out->bad_address = q->address + 4 * (first < count ? first : second);
    } else if (valid && q->op == STAGING_OP_SHA256 && q->seed == 0) {
        uint8_t sum[32];
        hash((const volatile uint32_t *)STAGING_POINTER(q->address), q->length, sum);
        for (unsigned j = 0; j < 32; j++) out->sha256[j] = sum[j];
        out->checked = q->length;
        out->code = STAGING_OK;
    }
    barrier();
    out->magic = STAGING_RESULT_MAGIC;
    barrier();
}
