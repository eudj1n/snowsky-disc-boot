/* Register/command-table behavior reviewed against diskOS flash/my_write5.c.
   Original implementation; no NAND reset, unlock or writer code. */
#include "sfc_x2000.h"
#define SFC 0xb3440000u
#define GATE 0xb0000020u
#define CLOCK 0xb0000074u
#define GPIO 0xb0010400u
#define PINS 0x003f0000u
#define BUSY (1u << 28)
#define START 1u
#define STOP 2u
#define FLUSH 4u
#define END 16u
#define RECEIVE 4u

static uint32_t read32(struct sfc_identity *s, uint32_t address) {
    return s->io.read32(s->io.context, address);
}
static void write32(struct sfc_identity *s, uint32_t address, uint32_t value) {
    s->io.write32(s->io.context, address, value);
}
static int clock_ready(struct sfc_identity *s) {
    for (uint32_t i = 0; i < s->limit; i++) if (!(read32(s, CLOCK) & BUSY)) return 0;
    return -1;
}
static void halt(struct sfc_identity *s) {
    write32(s, SFC + 0x64, STOP);
    write32(s, SFC + 0x64, FLUSH);
    write32(s, SFC + 0x6c, 0x1f);
}
static void descriptor(struct sfc_identity *s, unsigned index, unsigned aw, unsigned arg, uint8_t opcode) {
    uint32_t address = SFC + 0x800 + 16u * index;
    write32(s, address, arg); /* No chain or hardware polling. */
    write32(s, address + 4, (aw << 26) | (1u << 24) | (1u << 16) | opcode);
    write32(s, address + 8, 0);
    write32(s, address + 12, 0);
}
int sfc_identity_begin(struct sfc_identity *s) {
    if (!s || !s->io.read32 || !s->io.write32 || s->touched || !s->limit || s->limit > 800000u ||
        !s->extal_mhz || s->extal_mhz > 50 || !s->target_mhz || s->target_mhz > 50) return -1;
    s->ready = s->controller = s->page_ready = 0;
    s->saved_gate = read32(s, GATE); s->saved_clock = read32(s, CLOCK);
    if (s->saved_clock & BUSY) return -1;
    uint32_t source = s->saved_clock >> 30;
    if (source > 1) return -1; /* Do not guess an unknown clock source. */
    uint32_t pll = read32(s, 0xb0000010u + 4 * source);
    uint32_t mhz = s->extal_mhz * (((pll >> 20) & 4095u) + 1u) * 2u;
    mhz /= ((pll >> 14) & 63u) + 1u;
    mhz /= 1u << ((pll >> 11) & 7u);
    if (!mhz) return -1;
    uint32_t divisor = (mhz + s->target_mhz - 1) / s->target_mhz;
    if (!divisor || divisor > 256) return -1;
    for (unsigned i = 0; i < 4; i++) s->saved_gpio[i] = read32(s, GPIO + 0x10 + 0x10 * i) & PINS;
    s->touched = 1;
    write32(s, GATE, s->saved_gate & ~4u);
    for (unsigned i = 0; i < 4; i++) write32(s, GPIO + 0x18 + 0x10 * i, PINS);
    write32(s, CLOCK, (s->saved_clock & ~(0xffu | (3u << 27))) | (1u << 29) | (divisor - 1));
    if (clock_ready(s)) return -1; /* Caller must restore the changed resources. */
    s->controller = 1;
    halt(s);
    for (unsigned i = 0; i < 6; i++) {
        write32(s, SFC + 0x14 + 4 * i, 0);
        write32(s, SFC + 0x30 + 4 * i, 0);
        write32(s, SFC + 0x48 + 4 * i, 0);
        write32(s, SFC + 0x9c + 4 * i, 0);
    }
    write32(s, SFC + 0x04, 0x47); /* Inspected single-SPI timing/idle levels. */
    write32(s, SFC + 0x08, 0); write32(s, SFC + 0x10, 0);
    write32(s, SFC + 0x2c, 0); write32(s, SFC + 0x60, 0);
    write32(s, SFC + 0x70, 0x1f); write32(s, SFC + 0x78, 0);
    descriptor(s, 1, 0, 0, 0x9f);
    descriptor(s, 2, 1, 1, 0x9f);
    descriptor(s, 4, 1, 2, 0x0f);
    s->ready = 1;
    return 0;
}
int sfc_identity_end(struct sfc_identity *s) {
    if (!s || !s->touched) return 0;
    s->ready = s->page_ready = 0;
    if (s->controller) halt(s);
    write32(s, CLOCK, (s->saved_clock & ~BUSY) | (1u << 29));
    int rc = clock_ready(s);
    for (unsigned i = 0; i < 4; i++) {
        write32(s, GPIO + 0x14 + 0x10 * i, s->saved_gpio[i]);
        write32(s, GPIO + 0x18 + 0x10 * i, PINS & ~s->saved_gpio[i]);
    }
    /* Preserve other clock-gate bits; SFC descriptors/configuration are not restored. */
    write32(s, GATE, (read32(s, GATE) & ~4u) | (s->saved_gate & 4u));
    s->touched = s->controller = 0;
    return rc;
}
static int transfer(struct sfc_identity *s, unsigned index, uint32_t address, uint8_t *data, unsigned n) {
    halt(s);
    uint32_t ci = read32(s, SFC + 0x7c) & ~(0xc000003fu);
    write32(s, SFC + 0x7c, ci | 0x80000000u | index); /* Receive direction only. */
    for (unsigned i = 0; i < 4; i++) write32(s, SFC + 0x80 + 4 * i,
        ((i == 2 && index == 4) || (i == 1 && index == 5)) ? address : 0);
    uint32_t glb = read32(s, SFC) & ~((1u << 13) | (7u << 3) | (63u << 7) | (1u << 6) | 3u);
    write32(s, SFC, glb | (32u << 7) | (1u << 3) | 2u | 0x4000u);
    write32(s, SFC + 0x2c, n); write32(s, SFC + 0x60, 0);
    write32(s, SFC + 0x64, FLUSH); write32(s, SFC + 0x64, START);
    unsigned received = 0;
    for (uint32_t poll = 0; poll < s->limit; poll++) {
        uint32_t status = read32(s, SFC + 0x68);
        if (status & 3u) break; /* FIFO under/overflow. */
        if (received < n && (status & RECEIVE)) {
            write32(s, SFC + 0x6c, RECEIVE);
            unsigned words = (n - received + 3u) / 4u;
            if (words > 32) words = 32; /* Configured FIFO request threshold. */
            for (unsigned w = 0; w < words; w++) {
                uint32_t word = read32(s, SFC + 0x1000);
                for (unsigned i = 0; i < 4 && received < n; i++) data[received++] = (uint8_t)(word >> (8 * i));
            }
        }
        if (status & END) {
            if (received != n) break;
            write32(s, SFC + 0x6c, END); return (int)n;
        }
    }
    halt(s);
    return -1;
}
int sfc_identity_receive(void *context, uint8_t op, uint32_t address, unsigned aw,
                         unsigned dummy, uint8_t *data, unsigned n) {
    struct sfc_identity *s = context;
    unsigned index;
    if (!s || !s->ready || !data || dummy) return -1;
    if (op == 0x9f && address == 0 && aw <= 1 && n == 3) index = aw ? 2 : 1;
    else if (op == 0x0f && aw == 1 && n == 1 &&
             (address == 0xa0 || address == 0xb0 || address == 0xc0)) index = 4;
    else return -1;
    return transfer(s, index, address, data, n);
}
int sfc_page_begin(struct sfc_identity *s) {
    if (!s || s->page >= 0x1000000u || !s->cache_bytes || s->cache_bytes > 4352u) return -1;
    if (sfc_identity_begin(s)) return -1;
    descriptor(s, 5, 3, 1, 0x13);
    write32(s, SFC + 0x854, (3u << 26) | (1u << 24) | 0x13); /* No data phase. */
    descriptor(s, 7, 2, 0, 0x0b);
    write32(s, SFC + 0x874, (2u << 26) | (8u << 17) | (1u << 24) | (1u << 16) | 0x0b);
    s->page_ready = 1;
    return 0;
}
int sfc_page_receive(void *context, uint8_t op, uint32_t address, unsigned aw,
                     unsigned dummy, uint8_t *data, unsigned n) {
    struct sfc_identity *s = context;
    if (!s || !s->ready || !s->page_ready) return -1;
    if (op == 0x13 && address == s->page && aw == 3 && !dummy && !data && !n)
        return transfer(s, 5, address, data, n);
    if (op == 0x0b && !address && aw == 2 && dummy == 8 && data &&
        n == s->cache_bytes && n && n <= 4352u) return transfer(s, 7, 0, data, n);
    return sfc_identity_receive(s, op, address, aw, dummy, data, n);
}
