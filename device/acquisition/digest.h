/* The rootfs read by digest (plan, stage 4b): the batch of page reads the full read sends, each
   page read by the same core into a scratch result, and only its record returned: the counters
   and status the host checks, the first OOB bytes (the bad-block marker) and the SHA-256 of the
   page's main bytes. 128 bytes a page instead of 4428, so a batch's result is 8 KiB, not 277. */
#ifndef DISC_DIGEST_H
#define DISC_DIGEST_H
#include "batch.h"
#define BATCH_DIGEST_RESULT_MAGIC 0x3244424eu
#define NR_DIGEST_MAGIC 0x3144524eu
struct nr_digest {
    uint32_t magic, version, done, code, nonce[4], sequence, operation, page;
    uint32_t data_bytes, data_crc32, observed_id, protect, feature, status, polls, transfers;
    uint8_t oob_head[8];
    uint8_t main_sha256[32];
    uint32_t reserved[3];
};
struct batch_digest_result {
    uint32_t magic, version, completed, code;
    struct nr_digest records[BATCH_MAX];
};
_Static_assert(sizeof(struct nr_digest) == 128, "Digest record ABI");
_Static_assert(sizeof(struct batch_digest_result) == 8208, "Digest batch result ABI");
void digest_sha256(const volatile uint8_t *data, uint32_t length, uint8_t out[32]);
void batch_digest_run(struct sfc_identity *, const struct nr_profile *, const struct batch_request *,
                      volatile struct batch_digest_result *, uint32_t first_page, uint32_t end_page);
#endif
