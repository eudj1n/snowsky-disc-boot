#include "nand_reader.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

/* Values are deliberately synthetic; this is not a real chip profile. */
static const struct nr_profile policy = {0x563412, 2048, 64, 128, 4, 0x10, 0x10, 0x30, 4, 3, 1, 0};
struct fake {
    unsigned calls, reads, ids, fail_at, short_at, busy, mismatch, feature, ecc, selected, feature_reads, drift;
    unsigned page_started, status_drift, page_busy;
    unsigned ops[64], addresses[64];
};
static int receive(void *context, uint8_t op, uint32_t addr, unsigned aw,
                   unsigned dummy, uint8_t *data, unsigned n) {
    struct fake *f = context;
    assert(f->calls < 64);
    f->ops[f->calls] = op; f->addresses[f->calls++] = addr;
    /* Exhaustive allowlist: any program, erase, unlock, SET_FEATURE or reset
       command aborts the test, including on injected error paths. */
    assert(op == 0x9f || op == 0x0f || op == 0x13 || op == 0x0b);
    if (f->calls == f->fail_at) return -1;
    if (f->calls == f->short_at) return n ? (int)n - 1 : -1;
    if (op == 0x9f) {
        assert(aw == 1 && addr == 0 && dummy == 0 && n == 3);
        data[0] = 0x12; data[1] = 0x34; data[2] = 0x56;
        if (++f->ids == 2 && f->mismatch) data[0] ^= 1;
    } else if (op == 0x0f) {
        assert(aw == 1 && n == 1 && dummy == 0);
        assert(addr == 0xa0 || addr == 0xb0 || addr == 0xc0);
        data[0] = addr == 0xa0 ? 0x7c : addr == 0xb0 ? f->feature : f->busy ? 1 : f->ecc;
        if (addr == 0xc0 && f->page_started && f->page_busy) data[0] = 1;
        if (addr == 0xc0 && f->reads && f->status_drift) data[0] ^= 0x10;
        if (addr == 0xb0 && ++f->feature_reads == 2 && f->drift) data[0] ^= 0x10;
    } else if (op == 0x13) {
        assert(aw == 3 && dummy == 0 && !n && !data && addr < policy.total_pages);
        f->selected = addr;
        f->page_started = 1;
    } else {
        assert(aw == 2 && addr == 0 && dummy == 8 && n == 2112);
        f->reads++;
        for (unsigned i = 0; i < n; i++) data[i] = (uint8_t)(i + f->selected);
    }
    return (int)n;
}
static struct nr_request request(unsigned op) {
    struct nr_request q = {NR_REQUEST_MAGIC, 1, op, {1, 2, 3, 4}, 9, 0, {0, 0, 0}};
    return q;
}
static int run(struct fake *f, struct nr_request *q, struct nr_result *r, const struct nr_profile *p) {
    struct nr_bus bus = {f, receive};
    int code = nr_execute(p, &bus, q, r);
    assert(r->magic == NR_RESULT_MAGIC && r->version == 1 && r->done == NR_DONE);
    assert(r->code == (unsigned)code && r->transfers == f->calls);
    if (code) assert(r->data_bytes == 0 && r->data_crc32 == 0);
    return code;
}
int main(int argc, char **argv) {
    struct nr_result r;
    struct nr_request q = request(NR_IDENTIFY);
    struct fake f = {.feature=0x10};
    assert(run(&f, &q, &r, &policy) == NR_OK && f.reads == 0);
    assert(r.observed_id == 0x563412 && r.protect == 0x7c && r.feature == 0x10);
    assert(!memcmp(r.nonce, q.nonce, sizeof(q.nonce)) && r.sequence == q.sequence && !r.data_bytes);

    q = request(NR_READ_PAGE); q.page = 127; f = (struct fake){.feature=0x10};
    assert(run(&f, &q, &r, &policy) == NR_OK && f.reads == 1);
    assert(r.page == 127 && r.data_bytes == 2112 && r.data[2048] == 127);
    /* Independently computed standard CRC-32 of synthetic page+OOB data. */
    assert(r.data_crc32 == 0x2c6e87aau);
    if (argc == 2 && !strcmp(argv[1], "--emit-fixture")) {
        return fwrite(&r, 1, sizeof(r), stdout) == sizeof(r) ? 0 : 1;
    }
    assert(argc == 1);
    unsigned normal_calls = f.calls;
    for (unsigned call = 1; call <= normal_calls; call++) {
        f = (struct fake){.feature=0x10, .fail_at=call};
        assert(run(&f, &q, &r, &policy) == NR_IO && f.calls == call);
        f = (struct fake){.feature=0x10, .short_at=call};
        assert(run(&f, &q, &r, &policy) == NR_IO && f.calls == call);
    }
    f = (struct fake){.feature=0x10, .busy=1};
    assert(run(&f, &q, &r, &policy) == NR_BUSY && f.calls == policy.poll_limit && !f.reads);
    f = (struct fake){.feature=0x10, .page_busy=1};
    assert(run(&f, &q, &r, &policy) == NR_BUSY && r.polls == 1 + policy.poll_limit && !f.reads);
    f = (struct fake){.feature=0x10, .mismatch=1};
    assert(run(&f, &q, &r, &policy) == NR_ID_UNSTABLE && !f.reads);
    f = (struct fake){.feature=0};
    assert(run(&f, &q, &r, &policy) == NR_FEATURE && !f.reads);
    f = (struct fake){.feature=0x10, .drift=1};
    assert(run(&f, &q, &r, &policy) == NR_FEATURE && f.reads == 1);
    f = (struct fake){.feature=0x10, .status_drift=1};
    assert(run(&f, &q, &r, &policy) == NR_ECC && f.reads == 1);
    for (unsigned ecc = 0; ecc < 4; ecc++) {
        f = (struct fake){.feature=0x10, .ecc=ecc<<4};
        assert(run(&f, &q, &r, &policy) == (ecc < 2 ? NR_OK : NR_ECC));
        assert(f.reads == (ecc < 2 ? 1u : 0u));
    }
    struct nr_profile other = policy; other.expected_id ^= 1;
    f = (struct fake){.feature=0x10};
    assert(run(&f, &q, &r, &other) == NR_ID_UNKNOWN && !f.reads);
    other = policy; other.page_bytes = 0xffffffffu;
    f = (struct fake){0}; assert(run(&f, &q, &r, &other) == NR_INVALID && !f.calls);
    q = request(NR_IDENTIFY); other.page_bytes = other.oob_bytes = other.total_pages = 0;
    f = (struct fake){.feature=0x10}; assert(run(&f, &q, &r, &other) == NR_OK && !f.reads);
    for (unsigned invalid = 0; invalid < 6; invalid++) {
        q = request(NR_READ_PAGE);
        if (invalid == 0) q.magic = 0;
        if (invalid == 1) q.operation = 3;
        if (invalid == 2) q.page = policy.total_pages;
        if (invalid == 3) q.reserved[2] = 1;
        if (invalid == 4) memset(q.nonce, 0, sizeof(q.nonce));
        if (invalid == 5) q.version = 2;
        f = (struct fake){0}; assert(run(&f, &q, &r, &policy) == NR_INVALID && !f.calls);
    }
    puts("NAND core: synthetic identity/page/OOB, CRC, nonce, bounds, ECC and all-transfer fault cases passed");
    return 0;
}
