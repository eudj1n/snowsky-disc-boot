/* X2000 receive-only adapter. Page mode is separate and restricted to one page. */
#ifndef DISC_SFC_X2000_H
#define DISC_SFC_X2000_H
#include <stdint.h>
struct sfc_io {
    void *context;
    uint32_t (*read32)(void *, uint32_t);
    void (*write32)(void *, uint32_t, uint32_t);
};
struct sfc_identity {
    struct sfc_io io;
    uint32_t limit, extal_mhz, target_mhz;
    uint32_t saved_gate, saved_clock, saved_gpio[4];
    int touched, controller, ready, page_ready;
    uint32_t page, cache_bytes;
};
int sfc_identity_begin(struct sfc_identity *);
int sfc_identity_end(struct sfc_identity *);
int sfc_identity_receive(void *, uint8_t, uint32_t, unsigned, unsigned, uint8_t *, unsigned);
int sfc_page_begin(struct sfc_identity *);
int sfc_page_receive(void *, uint8_t, uint32_t, unsigned, unsigned, uint8_t *, unsigned);
#endif
