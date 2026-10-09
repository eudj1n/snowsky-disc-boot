#include "identity_layout.h"
    .set noreorder
    .set mips32
    .section .text.entry,"ax",@progbits
    .globl _start
    .ent _start
_start:
    move $t0, $sp
    li $sp, IDENTITY_STACK_TOP
    addiu $sp, $sp, -32
    sw $t0, 16($sp)
    sw $ra, 20($sp)
    sw $gp, 24($sp)
    sw $fp, 28($sp)
    /* The bottom 16 bytes are the o32 outgoing argument area. */
    jal identity_main
    nop
    lw $t0, 16($sp)
    lw $ra, 20($sp)
    lw $gp, 24($sp)
    lw $fp, 28($sp)
    sync
    move $sp, $t0
    jr $ra
    nop
    .end _start
