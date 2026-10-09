#ifndef DISC_BATCH_H
#define DISC_BATCH_H
#include "identity.h"
#define BATCH_MAX 64u
#define BATCH_REQUEST_MAGIC 0x3151424eu
#define BATCH_RESULT_MAGIC 0x3152424eu
struct batch_request {
    uint32_t magic, version, count, reserved;
    struct nr_request requests[BATCH_MAX];
};
struct batch_result {
    uint32_t magic, version, completed, code;
    struct nr_result results[BATCH_MAX];
};
void batch_run(struct sfc_identity *, const struct nr_profile *, const struct batch_request *,
               volatile struct batch_result *, uint32_t first_page, uint32_t end_page);
_Static_assert(sizeof(struct batch_request) == 3088, "Batch request ABI");
_Static_assert(sizeof(struct batch_result) == 283408, "Batch result ABI");
#endif
