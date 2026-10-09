#include "digest.h"
#include "identity.h"

static void barrier(void) {
#if defined(__mips__)
    __asm__ __volatile__("sync" ::: "memory");
#else
    __asm__ __volatile__("" ::: "memory");
#endif
}

/* SHA-256 (FIPS 180-4) without libc: the payload is freestanding. */
static const uint32_t K[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
static uint32_t ror(uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); }

static void compress(uint32_t h[8], const uint8_t block[64]) {
    uint32_t w[64], a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], k = h[7];
    for (unsigned i = 0; i < 16; i++)
        w[i] = (uint32_t)block[4*i] << 24 | (uint32_t)block[4*i+1] << 16 | (uint32_t)block[4*i+2] << 8 | block[4*i+3];
    for (unsigned i = 16; i < 64; i++) {
        uint32_t s0 = ror(w[i-15], 7) ^ ror(w[i-15], 18) ^ (w[i-15] >> 3);
        uint32_t s1 = ror(w[i-2], 17) ^ ror(w[i-2], 19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    for (unsigned i = 0; i < 64; i++) {
        uint32_t t1 = k + (ror(e, 6) ^ ror(e, 11) ^ ror(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
        uint32_t t2 = (ror(a, 2) ^ ror(a, 13) ^ ror(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
        k = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += k;
}

void digest_sha256(const volatile uint8_t *data, uint32_t length, uint8_t out[32]) {
    /* Element by element: an aggregate initializer may become a memcpy, and there is no libc. */
    uint32_t h[8];
    h[0] = 0x6a09e667; h[1] = 0xbb67ae85; h[2] = 0x3c6ef372; h[3] = 0xa54ff53a;
    h[4] = 0x510e527f; h[5] = 0x9b05688c; h[6] = 0x1f83d9ab; h[7] = 0x5be0cd19;
    uint8_t block[64];
    uint32_t done = 0;
    while (length - done >= 64) {
        for (unsigned i = 0; i < 64; i++) block[i] = data[done + i];
        compress(h, block);
        done += 64;
    }
    unsigned rest = length - done;
    for (unsigned i = 0; i < 64; i++) block[i] = i < rest ? data[done + i] : 0;
    block[rest] = 0x80;
    if (rest >= 56) { compress(h, block); for (unsigned i = 0; i < 64; i++) block[i] = 0; }
    /* The length in bits as two words: 64-bit shifts would need libgcc on MIPS32. */
    uint32_t low = length << 3, high = length >> 29;
    for (unsigned i = 0; i < 4; i++) { block[63 - i] = (uint8_t)(low >> (8 * i)); block[59 - i] = (uint8_t)(high >> (8 * i)); }
    compress(h, block);
    for (unsigned i = 0; i < 8; i++)
        for (unsigned j = 0; j < 4; j++) out[4*i + j] = (uint8_t)(h[i] >> (24 - 8 * j));
}

/* The full read's batch check (batch.c's valid(), which stays as reviewed), the same rules. */
static int valid(const struct batch_request *batch, const struct nr_profile *p, uint32_t first, uint32_t end) {
    if (first >= end || end > p->total_pages || batch->magic != BATCH_REQUEST_MAGIC ||
        batch->version != 1 || !batch->count || batch->count > BATCH_MAX || batch->reserved) return 0;
    const struct nr_request *initial = &batch->requests[0];
    if (!initial->sequence || initial->sequence > 0xffffffffu-(batch->count-1u) ||
        !(initial->nonce[0] | initial->nonce[1] | initial->nonce[2] | initial->nonce[3])) return 0;
    for (unsigned i = 0; i < BATCH_MAX; i++) {
        const struct nr_request *q = &batch->requests[i];
        if (i >= batch->count) {
            const uint8_t *raw = (const uint8_t *)q;
            for (unsigned j = 0; j < sizeof(*q); j++) if (raw[j]) return 0;
            continue;
        }
        if (q->magic != NR_REQUEST_MAGIC || q->version != 1 || q->operation != NR_READ_PAGE ||
            q->page < first || q->page >= end || q->sequence != initial->sequence+i ||
            q->reserved[0] || q->reserved[1] || q->reserved[2]) return 0;
        for (unsigned j = 0; j < 4; j++) if (q->nonce[j] != initial->nonce[j]) return 0;
    }
    return 1;
}

/* CP0 Count, which runs at a fixed share of the CPU clock in kernel mode; 0 on the host. */
static uint32_t cycles(void) {
#if defined(__mips__)
    uint32_t count;
    __asm__ __volatile__("mfc0 %0, $9" : "=r"(count));
    return count;
#else
    return 0;
#endif
}

void batch_digest_run(struct sfc_identity *s, const struct nr_profile *p, const struct batch_request *q,
                      volatile struct batch_digest_result *out, uint32_t first, uint32_t end) {
    struct nr_result scratch;   /* one page's full result, on the stack, never returned */
    out->magic = 0;
    barrier();
    volatile uint8_t *zero = (volatile uint8_t *)out;
    for (unsigned i = 4; i < sizeof(*out); i++) zero[i] = 0;
    out->version = 1;
    out->code = NR_INVALID;
    if (valid(q, p, first, end)) {
        out->code = NR_OK;
        for (unsigned i = 0; i < q->count; i++) {
            volatile struct nr_result *r = &scratch;
            volatile struct nr_digest *d = &out->records[i];
            uint32_t began = cycles();
            page_run(s, p, &q->requests[i], r, q->requests[i].page);
            d->version = r->version; d->done = r->done; d->code = r->code;
            for (unsigned j = 0; j < 4; j++) d->nonce[j] = r->nonce[j];
            d->sequence = r->sequence; d->operation = r->operation; d->page = r->page;
            d->data_bytes = r->data_bytes; d->data_crc32 = r->data_crc32; d->observed_id = r->observed_id;
            d->protect = r->protect; d->feature = r->feature; d->status = r->status;
            d->polls = r->polls; d->transfers = r->transfers;
            if (!r->code && r->data_bytes == p->page_bytes + p->oob_bytes && p->oob_bytes >= 8) {
                uint8_t sum[32];
                digest_sha256(r->data, p->page_bytes, sum);
                for (unsigned j = 0; j < 32; j++) d->main_sha256[j] = sum[j];
                for (unsigned j = 0; j < 8; j++) d->oob_head[j] = r->data[p->page_bytes + j];
            }
            d->cycles = cycles() - began;
            barrier();
            d->magic = NR_DIGEST_MAGIC;
            out->completed = i+1;
            if (r->code) { out->code = r->code; break; }
        }
    }
    barrier();
    out->magic = BATCH_DIGEST_RESULT_MAGIC;
    barrier();
}
