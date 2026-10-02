#include "identity.h"
static void barrier(void) {
#if defined(__mips__)
    __asm__ __volatile__("sync" ::: "memory");
#else
    __asm__ __volatile__("" ::: "memory");
#endif
}
static int receive(void *context, uint8_t op, uint32_t address, unsigned aw,
                   unsigned dummy, uint8_t *data, unsigned length) {
    struct sfc_identity *s = context;
    if (!s->ready && sfc_identity_begin(s)) return -1;
    return sfc_identity_receive(s, op, address, aw, dummy, data, length);
}
static int page_receive(void *context, uint8_t op, uint32_t address, unsigned aw,
                        unsigned dummy, uint8_t *data, unsigned length) {
    struct sfc_identity *s = context;
    if (!s->ready && sfc_page_begin(s)) return -1;
    return sfc_page_receive(s, op, address, aw, dummy, data, length);
}
static void run(struct sfc_identity *s, const struct nr_profile *p,
                const struct nr_request *q, volatile struct nr_result *output, int page_mode) {
    struct nr_result result;
    struct nr_bus bus = {s, page_mode ? page_receive : receive};
    output->done = 0;
    barrier();
    if ((!page_mode && q->operation == NR_IDENTIFY) ||
        (page_mode && q->operation == NR_READ_PAGE && q->page == s->page)) nr_execute(p, &bus, q, &result);
    else {
        uint8_t *raw = (uint8_t *)&result;
        for (unsigned i = 0; i < sizeof(result); i++) raw[i] = 0;
        result.magic = NR_RESULT_MAGIC; result.version = 1; result.code = NR_INVALID;
        for (unsigned i = 0; i < 4; i++) result.nonce[i] = q->nonce[i];
        result.sequence = q->sequence; result.operation = q->operation; result.page = q->page;
    }
    if (sfc_identity_end(s)) result.code = NR_IO;
    /* Completion is visible only after controller cleanup and all result stores. */
    result.done = 0;
    const uint8_t *raw = (const uint8_t *)&result;
    volatile uint8_t *out = (volatile uint8_t *)output;
    for (unsigned i = 0; i < sizeof(result); i++) out[i] = raw[i];
    barrier();
    output->done = NR_DONE;
    barrier();
}
void identity_run(struct sfc_identity *s, const struct nr_profile *p,
                  const struct nr_request *q, volatile struct nr_result *output) {
    run(s, p, q, output, 0);
}
void page_run(struct sfc_identity *s, const struct nr_profile *p,
              const struct nr_request *q, volatile struct nr_result *output, uint32_t allowed_page) {
    s->page = allowed_page;
    s->cache_bytes = p->page_bytes + p->oob_bytes;
    run(s, p, q, output, 1);
}
