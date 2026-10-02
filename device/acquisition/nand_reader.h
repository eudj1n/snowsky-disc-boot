/* Original freestanding read core. Not a ROM loader or qualified NAND driver. */
#ifndef DISC_NAND_READER_H
#define DISC_NAND_READER_H
#include <stdint.h>
#define NR_REQUEST_MAGIC 0x3151524eu
#define NR_RESULT_MAGIC 0x3153524eu
#define NR_DONE 0x454e4f44u
#define NR_DATA_MAX 4352u
#define NR_IDENTIFY 1u
#define NR_READ_PAGE 2u
enum nr_code { NR_OK, NR_INVALID, NR_IO, NR_BUSY, NR_ID_UNSTABLE,
               NR_ID_UNKNOWN, NR_FEATURE, NR_ECC };

/* Only these receive-side transactions are expressible by the core. The
   controller adapter must bound each call and return the exact received count.
   PAGE_READ has no data phase; it transfers a NAND page into the chip's cache. */
struct nr_bus {
    void *context;
    int (*receive)(void *, uint8_t opcode, uint32_t address, unsigned address_bytes,
                   unsigned dummy_bits, uint8_t *data, unsigned length);
};
/* Trusted chip policy, separate from the request; not physical qualification. */
struct nr_profile {
    uint32_t expected_id; /* Three wire-order ID bytes packed least-significant first. */
    uint32_t page_bytes, oob_bytes, total_pages, poll_limit;
    uint32_t feature_mask, feature_value;
    uint32_t ecc_mask, ecc_shift, ecc_admitted; /* Bitset of admitted decoded values. */
    uint32_t id_address_bytes;
    uint32_t id_mask; /* Zero preserves exact three-byte matching for older policies. */
};
struct nr_request {
    uint32_t magic, version, operation, nonce[4], sequence, page, reserved[3];
};
struct nr_result {
    uint32_t magic, version, done, code, nonce[4], sequence, operation, page;
    uint32_t data_bytes, data_crc32, observed_id, protect, feature, status;
    uint32_t polls, transfers;
    uint8_t data[NR_DATA_MAX];
};
_Static_assert(sizeof(struct nr_request) == 48, "Request ABI changed");
_Static_assert(sizeof(struct nr_result) == 76 + NR_DATA_MAX, "Result ABI changed");
/* Returns result code; done is set last. A future RAM adapter must provide the
   necessary uncached buffers/barriers before host readback. No USB is implemented. */
int nr_execute(const struct nr_profile *, const struct nr_bus *,
               const struct nr_request *, struct nr_result *);
#endif
