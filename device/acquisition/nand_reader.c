#include "nand_reader.h"

static int receive(const struct nr_bus *b, struct nr_result *r, uint8_t op,
                   uint32_t address, unsigned aw, unsigned dummy, uint8_t *out, unsigned n) {
    r->transfers++;
    return b->receive(b->context, op, address, aw, dummy, out, n) == (int)n ? NR_OK : NR_IO;
}
static int feature(const struct nr_bus *b, struct nr_result *r, uint32_t address, uint32_t *out) {
    uint8_t byte = 0;
    int rc = receive(b, r, 0x0f, address, 1, 0, &byte, 1);
    if (!rc) *out = byte;
    return rc;
}
static int ready(const struct nr_profile *p, const struct nr_bus *b, struct nr_result *r) {
    for (uint32_t i = 0; i < p->poll_limit; i++) {
        r->polls++;
        int rc = feature(b, r, 0xc0, &r->status);
        if (rc) return rc;
        if (!(r->status & 1u)) return NR_OK;
    }
    return NR_BUSY;
}
static int id(const struct nr_profile *p, const struct nr_bus *b, struct nr_result *r, uint32_t *out) {
    uint8_t bytes[3] = {0, 0, 0};
    int rc = receive(b, r, 0x9f, 0, p->id_address_bytes, 0, bytes, 3);
    if (!rc) *out = bytes[0] | ((uint32_t)bytes[1] << 8) | ((uint32_t)bytes[2] << 16);
    return rc;
}
static uint32_t crc32(const uint8_t *data, uint32_t n) {
    uint32_t crc = 0xffffffffu;
    for (uint32_t i = 0; i < n; i++) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; bit++) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
static int valid_profile(const struct nr_profile *p, uint32_t operation) {
    if (!p || p->id_address_bytes > 1 || !p->poll_limit || p->poll_limit > 10000) return 0;
    if (operation == NR_IDENTIFY) return 1; /* Identification does not assume chip geometry. */
    if (!p->page_bytes || p->page_bytes > 4096 || !p->oob_bytes || p->oob_bytes > 256) return 0;
    if (!p->total_pages || p->total_pages > 0x1000000u) return 0;
    if (p->id_mask && p->id_mask != 0xffffu && p->id_mask != 0xffffffu) return 0;
    if (!p->feature_mask || p->feature_mask > 255 || (p->feature_value & ~p->feature_mask)) return 0;
    if (p->ecc_shift > 7 || !p->ecc_mask || p->ecc_mask > 255) return 0;
    uint32_t mask = p->ecc_mask >> p->ecc_shift;
    if (mask > 15 || (mask << p->ecc_shift) != p->ecc_mask || (mask & (mask + 1u))) return 0;
    return p->ecc_admitted && !(p->ecc_admitted >> (mask + 1u));
}
static int execute(const struct nr_profile *p, const struct nr_bus *b,
                   const struct nr_request *q, struct nr_result *r) {
    if (!q || !b || !b->receive || !valid_profile(p, q->operation)) return NR_INVALID;
    for (unsigned i = 0; i < 4; i++) r->nonce[i] = q->nonce[i];
    r->sequence = q->sequence; r->operation = q->operation; r->page = q->page;
    if (q->magic != NR_REQUEST_MAGIC || q->version != 1 ||
        !(q->nonce[0] | q->nonce[1] | q->nonce[2] | q->nonce[3]) ||
        q->reserved[0] || q->reserved[1] || q->reserved[2]) return NR_INVALID;
    if (q->operation != NR_IDENTIFY && q->operation != NR_READ_PAGE) return NR_INVALID;
    if (q->operation == NR_IDENTIFY && q->page) return NR_INVALID;
    if (q->operation == NR_READ_PAGE && (q->page >= p->total_pages ||
        !p->expected_id || p->expected_id >= 0xffffffu)) return NR_INVALID;

    int rc = ready(p, b, r);
    if (rc) return rc;
    rc = id(p, b, r, &r->observed_id);
    if (rc) return rc;
    uint32_t second = 0;
    rc = id(p, b, r, &second);
    if (rc) return rc;
    if (r->observed_id != second) return NR_ID_UNSTABLE;
    if (!second || second == 0xffffffu) return NR_ID_UNKNOWN;
    rc = feature(b, r, 0xa0, &r->protect);
    if (rc) return rc;
    rc = feature(b, r, 0xb0, &r->feature);
    if (rc) return rc;
    if (q->operation == NR_IDENTIFY) return NR_OK;
    uint32_t id_mask = p->id_mask ? p->id_mask : 0xffffffu;
    if ((second & id_mask) != (p->expected_id & id_mask)) return NR_ID_UNKNOWN;
    if ((r->feature & p->feature_mask) != p->feature_value) return NR_FEATURE;

    rc = receive(b, r, 0x13, q->page, 3, 0, 0, 0);
    if (rc) return rc;
    rc = ready(p, b, r);
    if (rc) return rc;
    uint32_t page_status = r->status;
    unsigned ecc = (page_status & p->ecc_mask) >> p->ecc_shift;
    if (!(p->ecc_admitted & (1u << ecc))) return NR_ECC;
    uint32_t bytes = p->page_bytes + p->oob_bytes;
    rc = receive(b, r, 0x0b, 0, 2, 8, r->data, bytes);
    if (rc) return rc;
    rc = feature(b, r, 0xc0, &r->status);
    if (rc) return rc;
    if ((r->status & 1u) || r->status != page_status) return NR_ECC;
    uint32_t after = 0;
    rc = feature(b, r, 0xb0, &after);
    if (rc) return rc;
    if (after != r->feature) return NR_FEATURE;
    r->data_crc32 = crc32(r->data, bytes);
    r->data_bytes = bytes;
    return NR_OK;
}
int nr_execute(const struct nr_profile *p, const struct nr_bus *b,
               const struct nr_request *q, struct nr_result *r) {
    if (!r) return NR_INVALID;
    uint8_t *raw = (uint8_t *)r;
    for (unsigned i = 0; i < sizeof(*r); i++) raw[i] = 0;
    r->magic = NR_RESULT_MAGIC; r->version = 1;
    int rc = execute(p, b, q, r);
    r->code = (uint32_t)rc;
    /* Force the compiler to publish completion after all result stores. The
       eventual hardware adapter also needs the MIPS memory/cache discipline. */
    __asm__ __volatile__("" ::: "memory");
    r->done = NR_DONE;
    return rc;
}
