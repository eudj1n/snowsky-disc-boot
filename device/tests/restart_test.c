/* The restart payload's register sequence (device/usbboot/restart.c), recorded on the computer. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "restart.h"

static uint32_t addresses[16], values[16], ticks, step;
static int writes;

static uint32_t clock_now(void) {
    ticks += step;
    return ticks;
}

static void record(uint32_t address, uint32_t value) {
    assert(writes < 16);
    addresses[writes] = address;
    values[writes] = value;
    writes++;
}

int main(void) {
    const struct restart_io io = {record, clock_now};
    restart_start(&io);
    /* As the pinned SPL's _machine_restart: TSCR, TCNT, TDR, TCSR, TCER off, then on. */
    const uint32_t want_addresses[] = {0xb000203cu, 0xb0002008u, 0xb0002000u, 0xb000200cu, 0xb0002004u, 0xb0002004u};
    const uint32_t want_values[] = {1u << 16, 0, 2, (3u << 3) | (1u << 1), 0, 1};
    assert(writes == 6);
    for (int k = 0; k < 6; k++) {
        assert(addresses[k] == want_addresses[k]);
        assert(values[k] == want_values[k]);
    }
    /* A running Count ends the wait by its ticks; one that stands still, by the loop's turns. */
    step = 1u << 20;
    assert(restart_wait(&io) == RESTART_WAIT_TICKS / step - 1);
    step = 0;
    assert(restart_wait(&io) == RESTART_WAIT_LOOPS);
    writes = 0;
    restart_cancel(&io);
    assert(writes == 1 && addresses[0] == 0xb0002004u && values[0] == 0);
    puts("Restart: the SPL's watchdog sequence, the bounded wait and the cancel passed");
    return 0;
}
