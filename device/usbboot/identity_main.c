#include "identity_layout.h"
#ifdef STAGING_CHECK
/* The staging check (plan, stage 4c): memory only, no SFC or NAND code linked in. */
#include "staging.h"
void identity_main(void) {
    struct staging_request request;
    const volatile uint8_t *input = (const volatile uint8_t *)(uintptr_t)IDENTITY_REQUEST;
    uint8_t *copy = (uint8_t *)&request;
    for (unsigned i = 0; i < sizeof(request); i++) copy[i] = input[i];
    staging_run(&request, (volatile struct staging_result *)(uintptr_t)IDENTITY_RESULT);
}
#else
#include "identity.h"
#include "batch.h"
#ifdef ROOTFS_DIGEST
#include "digest.h"
#endif
static uint32_t read32(void *context, uint32_t address) {
    (void)context;
    return *(volatile uint32_t *)(uintptr_t)address;
}
static void write32(void *context, uint32_t address, uint32_t value) {
    (void)context;
    *(volatile uint32_t *)(uintptr_t)address = value;
    __asm__ __volatile__("sync" ::: "memory");
}
void identity_main(void) {
#ifdef ROOTFS_FIRST_PAGE
    struct batch_request request;
    const volatile uint8_t *input = (const volatile uint8_t *)(uintptr_t)BATCH_REQUEST_ADDRESS;
#else
    struct nr_request request;
    const volatile uint8_t *input = (const volatile uint8_t *)(uintptr_t)IDENTITY_REQUEST;
#endif
    uint8_t *copy = (uint8_t *)&request;
    for (unsigned i = 0; i < sizeof(request); i++) copy[i] = input[i];
    /* Volatile zeroing prevents aggregate initialization from requiring libc. */
    struct sfc_identity s;
    struct nr_profile p;
    volatile uint8_t *zero = (volatile uint8_t *)&s;
    for (unsigned i = 0; i < sizeof(s); i++) zero[i] = 0;
    zero = (volatile uint8_t *)&p;
    for (unsigned i = 0; i < sizeof(p); i++) zero[i] = 0;
    s.io.read32 = read32; s.io.write32 = write32;
    s.limit = IDENTITY_SFC_POLLS; s.extal_mhz = IDENTITY_EXTAL_MHZ;
    s.target_mhz = IDENTITY_TARGET_MHZ;
    p.poll_limit = IDENTITY_NAND_POLLS; p.id_address_bytes = IDENTITY_ID_ADDRESS_BYTES;
#if defined(METADATA_PAGE) || defined(ROOTFS_FIRST_PAGE)
    p.expected_id = PAGE_EXPECTED_ID; p.id_mask = PAGE_ID_MASK;
    p.page_bytes = PAGE_MAIN_BYTES; p.oob_bytes = PAGE_OOB_BYTES; p.total_pages = PAGE_TOTAL_PAGES;
    p.feature_mask = PAGE_FEATURE_MASK; p.feature_value = PAGE_FEATURE_VALUE;
    p.ecc_mask = PAGE_ECC_MASK; p.ecc_shift = PAGE_ECC_SHIFT; p.ecc_admitted = PAGE_ECC_ADMITTED;
#if defined(ROOTFS_FIRST_PAGE) && defined(ROOTFS_DIGEST)
    batch_digest_run(&s, &p, &request, (volatile struct batch_digest_result *)(uintptr_t)BATCH_RESULT_ADDRESS,
                     ROOTFS_FIRST_PAGE, ROOTFS_END_PAGE);
#elif defined(ROOTFS_FIRST_PAGE)
    batch_run(&s, &p, &request, (volatile struct batch_result *)(uintptr_t)BATCH_RESULT_ADDRESS,
              ROOTFS_FIRST_PAGE, ROOTFS_END_PAGE);
#else
    page_run(&s, &p, &request, (volatile struct nr_result *)(uintptr_t)IDENTITY_RESULT, METADATA_PAGE);
#endif
#else
    identity_run(&s, &p, &request, (volatile struct nr_result *)(uintptr_t)IDENTITY_RESULT);
#endif
}
#endif
