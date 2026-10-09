/* The player restarted from USB Boot (plan, stage 7): the watchdog started as the pinned SPL source's
   _machine_restart does on the X2000 (diskOS's u-boot-xburst: arch/mips/cpu/xburst2/cpu.c with
   asm/arch-x2000/wdt.h and base.h), after the installer's write, so that the chip restarts from NAND
   into the written system with the cable still connected. Memory and the TCU's registers only: no
   SFC, no NAND. */
#ifndef DISC_RESTART_H
#define DISC_RESTART_H
#include <stdint.h>
#define RESTART_TCU_BASE 0xb0002000u        /* TCU_BASE, which is also WDT_BASE (kseg1) */
#define RESTART_TCU_TSCR 0x3cu              /* the timers' stop-clear register */
#define RESTART_WDT_TDR 0x00u
#define RESTART_WDT_TCER 0x04u
#define RESTART_WDT_TCNT 0x08u
#define RESTART_WDT_TCSR 0x0cu
#define RESTART_TSCR_WDTSC (1u << 16)       /* the watchdog's clock no longer stopped */
#define RESTART_TCSR ((3u << 3) | (1u << 1)) /* prescale 64 (WDT_DIV), the RTC's 32768 Hz */
#define RESTART_TDR 2u                      /* 32768 / 64 * 4 ms (RESET_DELAY_MS) */
#define RESTART_TCER_TCEN 1u
/* The payload's bounded wait for the watchdog, in CP0 Count ticks (see identity_main.c). */
#define RESTART_WAIT_TICKS (1u << 28)
struct restart_io {
    void (*write32)(uint32_t address, uint32_t value);
};
/* The SPL's sequence, register by register. */
void restart_start(const struct restart_io *io);
/* The watchdog stopped again: the payload returns to the ROM when no restart came. */
void restart_cancel(const struct restart_io *io);
#endif
