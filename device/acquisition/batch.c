#include "batch.h"
static void barrier(void) {
#if defined(__mips__)
    __asm__ __volatile__("sync" ::: "memory");
#else
    __asm__ __volatile__("" ::: "memory");
#endif
}
static int valid(const struct batch_request *batch, const struct nr_profile *p,
                 uint32_t first, uint32_t end) {
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
void batch_run(struct sfc_identity *s, const struct nr_profile *p, const struct batch_request *q,
               volatile struct batch_result *out, uint32_t first, uint32_t end) {
    out->magic = 0;
    barrier();
    volatile uint8_t *zero = (volatile uint8_t *)out;
    for (unsigned i = 4; i < sizeof(*out); i++) zero[i] = 0;
    out->version = 1;
    out->code = NR_INVALID;
    /* Validate the entire batch before the first hardware operation. */
    if (valid(q, p, first, end)) {
        out->code = NR_OK;
        for (unsigned i = 0; i < q->count; i++) {
            page_run(s, p, &q->requests[i], &out->results[i], q->requests[i].page);
            out->completed = i+1;
            if (out->results[i].code) { out->code = out->results[i].code; break; }
        }
    }
    barrier();
    out->magic = BATCH_RESULT_MAGIC;
    barrier();
}
