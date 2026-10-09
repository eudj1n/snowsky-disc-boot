#include "restart.h"

void restart_start(const struct restart_io *io) {
    io->write32(RESTART_TCU_BASE + RESTART_TCU_TSCR, RESTART_TSCR_WDTSC);
    io->write32(RESTART_TCU_BASE + RESTART_WDT_TCNT, 0);
    io->write32(RESTART_TCU_BASE + RESTART_WDT_TDR, RESTART_TDR);
    io->write32(RESTART_TCU_BASE + RESTART_WDT_TCSR, RESTART_TCSR);
    io->write32(RESTART_TCU_BASE + RESTART_WDT_TCER, 0);
    io->write32(RESTART_TCU_BASE + RESTART_WDT_TCER, RESTART_TCER_TCEN);
}

void restart_cancel(const struct restart_io *io) {
    io->write32(RESTART_TCU_BASE + RESTART_WDT_TCER, 0);
}
