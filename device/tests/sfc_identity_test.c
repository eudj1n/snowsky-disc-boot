#include "sfc_x2000.h"
#include "nand_reader.h"
#include "identity.h"
#include "batch.h"
#include "digest.h"
#include "sha256.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define SFC 0xb3440000u
#define GPIO 0xb0010400u
struct fake {
    uint32_t regs[1025], gpio[4], gate, clock;
    unsigned starts, polls, writes, active, data_read, mode, clock_stuck;
    unsigned clock_stuck_on_write, clock_stuck_after_identity;
    unsigned page_mode, page_started, ecc, feature, id, fail_at, cache_stall;
    unsigned expected_page;
    volatile struct nr_result *published;
};
static uint32_t read32(void *context, uint32_t a) {
    struct fake *f = context;
    if (a == 0xb0000020) return f->gate;
    if (a == 0xb0000074) return f->clock | (f->clock_stuck ? 1u<<28 : 0);
    if (a == 0xb0000010 || a == 0xb0000014) return 19u << 20; /* 960 MHz fixture PLL. */
    for (unsigned i=0;i<4;i++) if(a==GPIO+0x10+16*i)return f->gpio[i];
    assert(a >= SFC && a <= SFC + 0x1000 && !(a & 3));
    if (a == SFC + 0x68 && f->active) {
        f->polls++;
        unsigned index = f->regs[0x7c/4] & 63;
        if (f->starts == f->fail_at) return 3;
        if (index == 5) return 16;
        if (index == 7 && !f->mode) {
            if (f->cache_stall && f->data_read >= 32)
                return f->cache_stall == 2 ? 16 : f->cache_stall == 3 ? 3 : 0;
            return f->data_read*4 < f->regs[0x2c/4] ? 4 : 16;
        }
        if (f->mode == 1) return 0;
        if (f->mode == 2) return 3;
        if (f->mode == 3) return f->data_read ? 0 : 4;
        if (f->mode == 4) return 16;
        return f->data_read ? 16 : 20;
    }
    if (a == SFC + 0x1000) {
        unsigned index = f->regs[0x7c/4] & 63;
        if (index == 7) {
            unsigned byte = 4*f->data_read++;
            uint32_t word = 0;
            for (unsigned i=0;i<4;i++) word |= ((byte+i+f->expected_page)&255u) << (8*i);
            return word;
        }
        f->data_read = 1;
        if (f->clock_stuck_after_identity && f->starts==5) f->clock_stuck=1;
        return index == 4 ? (f->regs[0x88/4] == 0xb0 ? f->feature :
            f->regs[0x88/4] == 0xc0 && f->page_started ? f->ecc : 0) : f->id;
    }
    return f->regs[(a-SFC)/4];
}
static void write32(void *context, uint32_t a, uint32_t v) {
    struct fake *f = context; f->writes++;
    if (f->published) assert(f->published->done == 0);
    if (a == 0xb0000020) {f->gate=v;return;}
    if (a == 0xb0000074) {
        f->clock=v & ~(1u<<29);
        if (f->clock_stuck_on_write) f->clock_stuck=1;
        return;
    }
    for (unsigned i=0;i<4;i++) {
        if(a==GPIO+0x14+16*i){f->gpio[i]|=v;return;}
        if(a==GPIO+0x18+16*i){f->gpio[i]&=~v;return;}
    }
    assert(a >= SFC && a < SFC + 0x1000 && !(a & 3)); /* Never writes FIFO. */
    f->regs[(a-SFC)/4]=v;
    if (a == SFC + 0x64 && v == 2) f->active=0;
    if (a == SFC + 0x64 && v == 1) {
        unsigned index=f->regs[0x7c/4]&63;
        assert(index==1 || index==2 || index==4 || (f->page_mode && (index==5 || index==7)));
        assert(!(f->regs[0x7c/4] & (1u<<30)) && !(f->regs[0] & ((1u<<13)|(1u<<6))));
        unsigned base=(0x800+16*index)/4;
        assert(!(f->regs[base] & 0x80000000u)); /* No chained command. */
        unsigned op=f->regs[base+1]&0xffff;
        assert(op==0x9f || op==0x0f || (f->page_mode && (op==0x13 || op==0x0b)));
        if (index==5 || index==7) {
            assert(f->regs[base+1] == (index==5 ? 0x0d000013u : 0x0911000bu));
            assert(f->regs[base] == (index==5 ? 1u : 0u));
            assert(f->regs[0x2c/4] == (index==5 ? 0u : 2176u));
            assert(f->regs[0x84/4] == (index==5 ? f->expected_page : 0u));
            assert(!f->regs[0x80/4] && !f->regs[0x88/4] && !f->regs[0x8c/4]);
            if(index==5)f->page_started=1;
        } else {
        assert(f->regs[base+1] == ((index==1 ? 0u : 1u)<<26 | 1u<<24 | 1u<<16 | op));
        assert(f->regs[base] == (index==1 ? 0u : index==2 ? 1u : 2u));
        assert(f->regs[0x2c/4] == (index==4 ? 1u : 3u));
        assert(!f->regs[0x80/4] && !f->regs[0x84/4] && !f->regs[0x8c/4]);
        if(index!=4)assert(!f->regs[0x88/4]);
        }
        f->starts++;f->active=1;f->data_read=0;
    }
}
static struct sfc_identity setup(struct fake *f) {
    memset(f,0,sizeof(*f));f->gate=0x77;f->clock=7;f->feature=16;f->id=0x563412;f->expected_page=11;
    for(unsigned i=0;i<4;i++)f->gpio[i]=0x55555555u;
    struct sfc_identity s={.io={f,read32,write32},.limit=8,.extal_mhz=24,.target_mhz=50};
    return s;
}
int main(void) {
    struct fake f;struct sfc_identity s=setup(&f);
    assert(sfc_identity_begin(&s)==0);
    assert((f.clock&255)==19); /* ceil(960/50)-1 */
    uint8_t bytes[5]={0xaa,0,0,0,0xbb};
    assert(sfc_identity_receive(&s,0x9f,0,1,0,bytes+1,3)==3);
    assert(bytes[0]==0xaa && bytes[4]==0xbb && bytes[1]==0x12 && bytes[3]==0x56);
    assert(sfc_identity_receive(&s,0x0f,0xb0,1,0,bytes+1,1)==1 && bytes[2]==0x34);
    unsigned writes=f.writes;
    for(unsigned op=0;op<256;op++)if(op!=0x9f)assert(sfc_identity_receive(&s,op,0,1,0,bytes+1,3)<0);
    assert(sfc_identity_receive(&s,0x9f,0,1,1,bytes+1,3)<0);
    assert(sfc_identity_receive(&s,0x9f,0,2,0,bytes+1,3)<0);
    assert(sfc_identity_receive(&s,0x9f,0,1,0,bytes+1,4)<0);
    assert(sfc_identity_receive(&s,0x0f,0xa1,1,0,bytes+1,1)<0);
    assert(f.writes==writes);
    assert(sfc_identity_end(&s)==0 && f.gate==0x77 && f.clock==7);
    for(unsigned i=0;i<4;i++)assert(f.gpio[i]==0x55555555u);
    assert(!s.ready && !f.active);

    for(unsigned mode=1;mode<=4;mode++) {
        s=setup(&f);assert(!sfc_identity_begin(&s));f.mode=mode;
        assert(sfc_identity_receive(&s,0x9f,0,1,0,bytes+1,3)<0);
        assert(f.polls<=s.limit && !f.active && f.starts==1);
        assert(!sfc_identity_end(&s));
    }
    s=setup(&f);f.clock=2u<<30;assert(sfc_identity_begin(&s)<0 && !f.writes);
    s=setup(&f);f.clock_stuck=1;assert(sfc_identity_begin(&s)<0 && !f.writes);
    s=setup(&f);assert(!sfc_identity_begin(&s));f.clock_stuck=1;
    assert(sfc_identity_end(&s)<0 && !s.ready && !s.touched && f.gate==0x77);

    s=setup(&f);assert(!sfc_identity_begin(&s));
    struct nr_bus bus={&s,sfc_identity_receive};
    struct nr_profile policy={.poll_limit=4,.id_address_bytes=1};
    struct nr_request q={NR_REQUEST_MAGIC,1,NR_IDENTIFY,{1,2,3,4},1,0,{0,0,0}};
    struct nr_result r;
    assert(nr_execute(&policy,&bus,&q,&r)==NR_OK && r.observed_id==0x563412 && !r.data_bytes);
    assert(f.starts==5 && !sfc_identity_end(&s));
    s=setup(&f);r.done=NR_DONE;f.published=&r;
    identity_run(&s,&policy,&q,&r);
    assert(r.done==NR_DONE && r.code==NR_OK && f.starts==5 && !f.active && f.gate==0x77);
    s=setup(&f);q.operation=NR_READ_PAGE;
    identity_run(&s,&policy,&q,&r);
    assert(r.done==NR_DONE && r.code==NR_INVALID && !f.writes);
    s=setup(&f);q.operation=NR_IDENTIFY;q.reserved[0]=1;
    identity_run(&s,&policy,&q,&r);
    assert(r.code==NR_INVALID && !f.writes);
    s=setup(&f);q.reserved[0]=0;f.mode=1;f.published=&r;
    identity_run(&s,&policy,&q,&r);
    assert(r.code==NR_IO && r.done==NR_DONE && f.starts==1 && !f.active && f.gate==0x77);
    s=setup(&f);f.clock_stuck_on_write=1;f.published=&r;
    identity_run(&s,&policy,&q,&r);
    assert(r.code==NR_IO && r.done==NR_DONE && !f.starts && !s.touched && f.gate==0x77);
    for(unsigned i=0;i<4;i++)assert(f.gpio[i]==0x55555555u);
    s=setup(&f);f.clock_stuck_after_identity=1;f.published=&r;
    identity_run(&s,&policy,&q,&r);
    assert(r.code==NR_IO && r.done==NR_DONE && f.starts==5 && !f.active && f.gate==0x77);
    struct nr_profile page_policy={.expected_id=0x120b,.id_mask=0xffff,.page_bytes=2048,
        .oob_bytes=128,.total_pages=131072,.poll_limit=4,.id_address_bytes=1,
        .feature_mask=0x50,.feature_value=0x10,.ecc_mask=0xf0,.ecc_shift=4,.ecc_admitted=0x1ff};
    q=(struct nr_request){NR_REQUEST_MAGIC,1,NR_READ_PAGE,{1,2,3,4},2,11,{0,0,0}};
    for(unsigned ecc=0;ecc<16;ecc++) {
        s=setup(&f);s.limit=64;f.page_mode=1;f.id=0xab120b;f.ecc=ecc<<4;f.published=&r;
        page_run(&s,&page_policy,&q,&r,11);
        assert(r.done==NR_DONE && r.code==(ecc<=8 ? NR_OK : NR_ECC));
        assert(r.data_bytes==(ecc<=8 ? 2176u : 0u) && f.starts==(ecc<=8 ? 10u : 7u));
        if(ecc<=8)for(unsigned i=0;i<2176;i++)assert(r.data[i]==(uint8_t)(i+11));
        assert(!f.active && !s.page_ready && f.gate==0x77 && f.clock==7);
    }
    for(unsigned failure=1;failure<=10;failure++) {
        s=setup(&f);s.limit=64;f.page_mode=1;f.id=0x120b;f.fail_at=failure;f.published=&r;
        page_run(&s,&page_policy,&q,&r,11);
        assert(r.code==NR_IO && !r.data_bytes && f.starts==failure && !f.active);
    }
    for(unsigned feature=0;feature<=0x50;feature+=0x50) {
        s=setup(&f);f.page_mode=1;f.id=0x120b;f.feature=feature;
        page_run(&s,&page_policy,&q,&r,11);
        assert(r.code==NR_FEATURE && !f.page_started);
    }
    s=setup(&f);f.page_mode=1; /* Different chip: no page command. */
    page_run(&s,&page_policy,&q,&r,11);assert(r.code==NR_ID_UNKNOWN && !f.page_started);
    s=setup(&f);q.page=12;
    page_run(&s,&page_policy,&q,&r,11);assert(r.code==NR_INVALID && !f.writes);q.page=11;
    s=setup(&f);q.operation=NR_IDENTIFY;
    page_run(&s,&page_policy,&q,&r,11);assert(r.code==NR_INVALID && !f.writes);q.operation=NR_READ_PAGE;
    for(unsigned fault=1;fault<=3;fault++) {
        s=setup(&f);s.limit=64;f.page_mode=1;f.id=0x120b;f.cache_stall=fault;
        page_run(&s,&page_policy,&q,&r,11);
        assert(r.code==NR_IO && !r.data_bytes && !f.active && f.starts==8);
    }
    s=setup(&f);assert(!sfc_identity_begin(&s));writes=f.writes;
    assert(sfc_page_receive(&s,0x13,0,3,0,0,0)<0 && f.writes==writes);
    assert(!sfc_identity_end(&s));
    s=setup(&f);s.page=11;s.cache_bytes=2176;f.page_mode=1;assert(!sfc_page_begin(&s));writes=f.writes;
    uint8_t cache[2176];
    for(unsigned op=0;op<256;op++)if(op!=0x0b)
        assert(sfc_page_receive(&s,op,0,2,8,cache,sizeof(cache))<0);
    assert(sfc_page_receive(&s,0x13,12,3,0,0,0)<0);
    assert(sfc_page_receive(&s,0x0b,1,2,8,cache,sizeof(cache))<0);
    assert(sfc_page_receive(&s,0x0b,0,2,8,cache,sizeof(cache)-1)<0);
    assert(f.writes==writes);assert(!sfc_identity_end(&s));
    static struct batch_request batch;
    static struct batch_result batch_result;
    batch.magic=BATCH_REQUEST_MAGIC;batch.version=1;batch.count=64;
    for(unsigned i=0;i<64;i++) {batch.requests[i]=q;batch.requests[i].sequence=i+1;}
    s=setup(&f);s.limit=64;f.page_mode=1;f.id=0x120b;
    batch_run(&s,&page_policy,&batch,&batch_result,11,13);
    assert(batch_result.magic==BATCH_RESULT_MAGIC && batch_result.completed==64 && !batch_result.code);
    assert(f.starts==640 && !f.active);
    for(unsigned i=0;i<64;i++)assert(batch_result.results[i].data_bytes==2176 &&
        batch_result.results[i].sequence==i+1 && batch_result.results[i].data[2175]==(uint8_t)(2175+11));
    {   /* The same batch read by digest (plan, stage 4b): each record carries the full result's
           counters and status, the first OOB bytes and the SHA-256 of the main bytes, checked
           against the boot program's own SHA-256 (common/sha256.c). */
        static struct batch_digest_result digests;
        s=setup(&f);s.limit=64;f.page_mode=1;f.id=0x120b;
        batch_digest_run(&s,&page_policy,&batch,&digests,11,13);
        assert(digests.magic==BATCH_DIGEST_RESULT_MAGIC && digests.completed==64 && !digests.code);
        assert(f.starts==640 && !f.active);
        for(unsigned i=0;i<64;i++) {
            const struct nr_digest *d=&digests.records[i];
            const struct nr_result *r=&batch_result.results[i];
            uint8_t want[32];sha256_ctx c;
            sha256_init(&c);sha256_update(&c,r->data,2048);sha256_final(&c,want);
            assert(d->magic==NR_DIGEST_MAGIC && d->version==1 && d->done==NR_DONE && !d->code &&
                   d->sequence==i+1 && d->page==r->page && d->operation==NR_READ_PAGE &&
                   !memcmp(d->nonce,r->nonce,16) && d->data_bytes==2176 && d->data_crc32==r->data_crc32 &&
                   d->observed_id==r->observed_id && d->protect==r->protect && d->feature==r->feature &&
                   d->status==r->status && d->polls==r->polls && d->transfers==r->transfers &&
                   !memcmp(d->main_sha256,want,32) && !memcmp(d->oob_head,r->data+2048,8) &&
                   !d->cycles && !d->reserved[0] && !d->reserved[1]);
        }
        uint8_t empty[32],abc[32],known[32];sha256_ctx c;
        sha256_init(&c);sha256_final(&c,known);digest_sha256((const uint8_t *)"",0,empty);assert(!memcmp(empty,known,32));
        static uint8_t long_input[4097];
        for(unsigned i=0;i<sizeof(long_input);i++)long_input[i]=(uint8_t)(i*7+3);
        for(unsigned n=0;n<=sizeof(long_input);n+=(n<130?1:257)) {
            sha256_init(&c);sha256_update(&c,long_input,n);sha256_final(&c,known);
            digest_sha256(long_input,n,abc);assert(!memcmp(abc,known,32));
        }
        struct nr_request saved=batch.requests[63];
        batch.requests[63].page=10;s=setup(&f);
        batch_digest_run(&s,&page_policy,&batch,&digests,11,13);
        assert(digests.magic==BATCH_DIGEST_RESULT_MAGIC && digests.code==NR_INVALID && !digests.completed && !f.writes);
        batch.requests[63]=saved;
        puts("SFC digests: 64-page records, SHA-256 against the boot program's, every length to 4 KiB, prevalidation passed");
    }
    /* An invalid final request must prevent every NAND operation. */
    for(unsigned fault=0;fault<7;fault++) {
        struct nr_request saved=batch.requests[63];
        if(fault==0)batch.requests[63].page=10;
        if(fault==1)batch.requests[63].page=13;
        if(fault==2)batch.requests[63].sequence=1;
        if(fault==3)batch.requests[63].nonce[0]^=1;
        if(fault==4)batch.requests[63].reserved[0]=1;
        if(fault==5)batch.requests[63].operation=NR_IDENTIFY;
        if(fault==6)batch.requests[63].magic=0;
        s=setup(&f);batch_run(&s,&page_policy,&batch,&batch_result,11,13);
        assert(batch_result.code==NR_INVALID && !batch_result.completed && !f.writes);
        batch.requests[63]=saved;
    }
    s=setup(&f);s.limit=64;f.page_mode=1;f.id=0x120b;f.fail_at=12;
    batch_run(&s,&page_policy,&batch,&batch_result,11,13);
    assert(batch_result.code==NR_IO && batch_result.completed==2 && f.starts==12);
    assert(batch_result.results[0].done==NR_DONE && !batch_result.results[2].done && !f.active);
    batch.count=1; /* Nonzero unused requests must be rejected too. */
    s=setup(&f);batch_run(&s,&page_policy,&batch,&batch_result,11,13);
    assert(batch_result.code==NR_INVALID && !f.writes);
    memset(&batch.requests[1],0,sizeof(batch.requests)-sizeof(batch.requests[0]));
    s=setup(&f);s.limit=64;f.page_mode=1;f.id=0x120b;
    batch_run(&s,&page_policy,&batch,&batch_result,11,12);
    assert(!batch_result.code && batch_result.completed==1 && f.starts==10 && !batch_result.results[1].done);
    /* Boot evidence uses page zero and a separate high OTA range. Check the
       actual MMIO command address and reject gaps/empty ranges before I/O. */
    const uint32_t edges[][3]={{0,0,1024},{1023,0,1024},{87552,87552,88064},{88063,87552,88064}};
    for(unsigned i=0;i<4;i++) {
        batch.requests[0].page=edges[i][0];
        s=setup(&f);s.limit=64;f.page_mode=1;f.id=0x120b;f.expected_page=edges[i][0];
        batch_run(&s,&page_policy,&batch,&batch_result,edges[i][1],edges[i][2]);
        assert(!batch_result.code && batch_result.completed==1 && f.starts==10 && !f.active);
        assert(batch_result.results[0].page==edges[i][0] &&
               batch_result.results[0].data[2175]==(uint8_t)(2175+edges[i][0]));
    }
    const uint32_t excluded[][3]={{0,11,13},{1024,0,1024},{87551,87552,88064},
                                 {88064,87552,88064},{0,0,0},{0,0,131073}};
    for(unsigned i=0;i<6;i++) {
        batch.requests[0].page=excluded[i][0];s=setup(&f);
        batch_run(&s,&page_policy,&batch,&batch_result,excluded[i][1],excluded[i][2]);
        assert(batch_result.code==NR_INVALID && !batch_result.completed && !f.writes);
    }
    puts("SFC boot ranges: page zero, upper edges, gap exclusion and zero-I/O rejection passed");
    puts("SFC batches: 64-page completion, prevalidation, bounds, partial failure and zero tail passed");
    puts("SFC identity/page: FIFO bursts, all ECC values, page admission and fault cases passed");
    puts("SFC identity: register trace, input allowlist, bounds, FIFO errors and core integration passed");
    return 0;
}
