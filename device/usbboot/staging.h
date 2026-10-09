/* The staging check (plan, stage 4c): run from the writer's code region before the writer is
   uploaded there, it checks the image's DRAM region with patterns written and read on the player,
   and hashes the staged image on the player, so that neither crosses USB twice. It touches no
   NAND and no SFC: memory only, inside the bounds the host's plan names. */
#ifndef DISC_STAGING_H
#define DISC_STAGING_H
#include <stdint.h>
#define STAGING_REQUEST_MAGIC 0x51475453u   /* "STGQ" */
#define STAGING_RESULT_MAGIC 0x52475453u    /* "STGR", written last: the completion marker */
#define STAGING_OP_PATTERN 1u               /* xorshift32 words from the seed, then their complement */
#define STAGING_OP_SHA256 2u                /* SHA-256 of the region as bytes */
#define STAGING_OK 0u
#define STAGING_INVALID 1u
#define STAGING_MISMATCH 2u
/* The region must be word-aligned DRAM (kseg1, uncached) of a whole number of 64-byte blocks. */
#define STAGING_DRAM_BASE 0xa0000000u
#define STAGING_DRAM_END 0xa8000000u
struct staging_request {
    uint32_t magic, version, op, address, length, seed;
    uint32_t nonce[4];
    uint32_t reserved[2];
};
struct staging_result {
    uint32_t magic, version, op, code;
    uint32_t nonce[4];
    uint32_t address, length, checked, bad_address;
    uint8_t sha256[32];
};
_Static_assert(sizeof(struct staging_request) == 48, "Staging request ABI");
_Static_assert(sizeof(struct staging_result) == 80, "Staging result ABI");
void staging_run(const struct staging_request *, volatile struct staging_result *);
#endif
