/*
 * loader v1: copy the flattened kernel into DRAM, build boot_args, jump to XNU.
 *
 * Everything it needs is embedded, so one flashed boot.img contains the whole
 * chain: loader + kernel image + device tree. Layout, all inside the 1.5 GiB
 * window at the top of DRAM (the only large hole left after the firmware
 * carve-outs):
 *
 *   0x9f000000            kernel image, 0xfdc000 bytes (from build-kernel-image.py)
 *   0x9f100000            Apple Device Tree
 *   0x9f101000            boot_args
 *
 * Why the kernel goes there and not where the loader runs: boot_args.physBase
 * must name the address the kernel was copied to, and the kernel recovers link
 * addresses as `runtime + (virtBase - physBase)` (see docs/xnu-handoff.md).
 * Copying onto ourselves would corrupt the embedded image, so the destination
 * is deliberately far away.
 *
 * The device tree is embedded with `.incbin`, generated to match this window:
 *   make-s9863a1h10-adt.py --dram-base 0x9f000000 --owned-memory-size 0x61000000
 *
 * NOT YET RUN ON HARDWARE. See docs/payload-bringup.md for the assumptions it
 * makes about the state the bootloader leaves us in.
 *
 * Registers: x20 UART, x19 boot_args, x22 virtBase, x21 our own base.
 */

#define UART_BASE       0x70100000
#define KERNEL_DST      0x9F000000
#define KERNEL_SIZE     0x00FDC000      /* footprint from kernel-loadplan.py */
#define KERNEL_ENTRY    0x003F8488      /* entry offset from the image base */
#define ADT_DST         0x9F100000
#define ARGS_DST        0x9F101000
#define XNU_VIRT_BASE   0xFFFFFFF007004000
#define OWNED_SIZE      0x61000000      /* must match the embedded ADT */
#define FB_BASE         0x9D000000
#define FB_ROW_BYTES    0x00000900
#define FB_WIDTH        0x00000240
#define FB_HEIGHT       0x000005A0

    .text
    .globl _start
_start:
    .long   0x14000010          /* code0: b 0x40 */
    .long   0
    .quad   0
    .quad   image_end - _start
    .quad   0
    .quad   0
    .quad   0
    .quad   0
    .long   0x644d5241          /* "ARM\x64" */
    .long   0

    /* ---- entry ---- */
    movz    x20, #(UART_BASE & 0xffff)
    movk    x20, #(UART_BASE >> 16), lsl #16
    adr     x1, msg_boot
    bl      puts

    /* ---- 1. copy the flattened kernel to KERNEL_DST ---- */
    movz    x0, #(KERNEL_DST & 0xffff)
    movk    x0, #(KERNEL_DST >> 16), lsl #16
    adr     x1, kernel_blob
    movz    x2, #(KERNEL_SIZE & 0xffff)
    movk    x2, #(KERNEL_SIZE >> 16), lsl #16
1:  ldp     x3, x4, [x1], #16
    ldp     x5, x6, [x1], #16
    stp     x3, x4, [x0], #16
    stp     x5, x6, [x0], #16
    subs    x2, x2, #32
    b.ne    1b

    adr     x1, msg_kernel
    bl      puts

    /* ---- 2. copy the device tree to ADT_DST ---- */
    movz    x0, #(ADT_DST & 0xffff)
    movk    x0, #(ADT_DST >> 16), lsl #16
    adr     x1, adt_blob
    adr     x8, adt_size
    ldr     x2, [x8]
2:  cbz     x2, 3f
    ldrb    w3, [x1], #1
    strb    w3, [x0], #1
    subs    x2, x2, #1
    b.ne    2b
3:
    adr     x1, msg_tree
    bl      puts

    /* ---- 3. build boot_args at ARGS_DST ---- */
    movz    x19, #(ARGS_DST & 0xffff)
    movk    x19, #(ARGS_DST >> 16), lsl #16

    movz    w6, #0x0202
    strh    w6, [x19]                   /* Revision 2, Version 2 */

    movz    x22, #(XNU_VIRT_BASE & 0xffff)
    movk    x22, #((XNU_VIRT_BASE >> 16) & 0xffff), lsl #16
    movk    x22, #((XNU_VIRT_BASE >> 32) & 0xffff), lsl #32
    movk    x22, #((XNU_VIRT_BASE >> 48) & 0xffff), lsl #48
    str     x22, [x19, #0x08]           /* virtBase = link base */

    movz    x21, #(KERNEL_DST & 0xffff)
    movk    x21, #(KERNEL_DST >> 16), lsl #16
    str     x21, [x19, #0x10]           /* physBase = where the kernel went */

    movz    x6, #(OWNED_SIZE & 0xffff)
    movk    x6, #(OWNED_SIZE >> 16), lsl #16
    str     x6, [x19, #0x18]            /* memSize */
    str     x6, [x19, #0x478]           /* memSizeActual */

    movz    x6, #(KERNEL_SIZE & 0xffff)
    movk    x6, #(KERNEL_SIZE >> 16), lsl #16
    add     x6, x21, x6
    str     x6, [x19, #0x20]            /* topOfKernelData */

    movz    x6, #(FB_BASE & 0xffff)
    movk    x6, #(FB_BASE >> 16), lsl #16
    str     x6, [x19, #0x28]            /* video base */
    str     xzr, [x19, #0x30]           /* display */
    movz    x6, #(FB_ROW_BYTES & 0xffff)
    str     x6, [x19, #0x38]
    movz    x6, #(FB_WIDTH & 0xffff)
    str     x6, [x19, #0x40]
    movz    x6, #(FB_HEIGHT & 0xffff)
    str     x6, [x19, #0x48]
    movz    x6, #32
    str     x6, [x19, #0x50]

    /* deviceTreeP is a *virtual* address: virtBase + (tree - physBase) */
    movz    x6, #(ADT_DST & 0xffff)
    movk    x6, #(ADT_DST >> 16), lsl #16
    sub     x6, x6, x21
    add     x6, x6, x22
    str     x6, [x19, #0x60]

    adr     x8, adt_size
    ldr     x6, [x8]
    str     w6, [x19, #0x68]            /* deviceTreeLength */

    adr     x1, cmdline
    add     x2, x19, #0x6c
4:  ldrb    w3, [x1], #1
    strb    w3, [x2], #1
    cbnz    w3, 4b

    str     xzr, [x19, #0x470]          /* bootFlags */

    adr     x1, msg_args
    bl      puts

    /* ---- 4. clean caches over what we just wrote ---- */
    mrs     x6, ctr_el0
    ubfx    x6, x6, #16, #4
    mov     x7, #4
    lsl     x6, x7, x6                  /* cache line size */
    movz    x4, #(KERNEL_DST & 0xffff)
    movk    x4, #(KERNEL_DST >> 16), lsl #16
    movz    x5, #0x0000
    movk    x5, #0x9f12, lsl #16        /* past tree and args */
5:  dc      civac, x4
    add     x4, x4, x6
    cmp     x4, x5
    b.lo    5b
    dsb     sy

    adr     x1, msg_jump
    bl      puts

    /* ---- 5. MMU and caches off, then branch to the kernel ---- */
    mrs     x4, sctlr_el1
    bic     x4, x4, #1                  /* MMU */
    bic     x4, x4, #4                  /* D-cache */
    bic     x4, x4, #0x1000             /* I-cache */
    msr     sctlr_el1, x4
    isb
    tlbi    vmalle1
    dsb     sy
    /* DarwinEnter also invalidates the I-cache before branching; we never
     * executed at KERNEL_DST, but one instruction is cheaper than a stale
     * line theory. */
    ic      iallu
    dsb     sy
    isb

    movz    x1, #(KERNEL_ENTRY & 0xffff)
    movk    x1, #(KERNEL_ENTRY >> 16), lsl #16
    add     x1, x1, x21                 /* kernel entry, physical */
    mov     x0, x19                     /* boot_args */
    br      x1

6:  wfi
    b       6b

/* ---- putc with a bounded wait ---- */
putc:
    movz    x9, #0x0000
    movk    x9, #0x0010, lsl #16
7:  ldr     w8, [x20, #0x0c]
    and     w8, w8, #0xff00
    cbz     w8, 8f
    subs    x9, x9, #1
    b.ne    7b
    ret
8:  strb    w3, [x20]
    ret

puts:
    mov     x18, x30
9:  ldrb    w3, [x1], #1
    cbz     w3, 10f
    bl      putc
    b       9b
10: mov     x30, x18
    ret

msg_boot:
    .asciz  "\r\nQIN2PRO LOADER v1: copying kernel\r\n"
msg_kernel:
    .asciz  "kernel at 0x9f000000\r\n"
msg_tree:
    .asciz  "tree at 0x9f100000\r\n"
msg_args:
    .asciz  "boot_args at 0x9f101000, jumping\r\n"
msg_jump:
    .asciz  "entry +0x3f8488\r\n"

cmdline:
    .asciz  "-v debug=0x144 serial=3"

    .balign 8
adt_blob:
    .incbin "build/adt-loader.dtb"
adt_end:

    .balign 8
adt_size:
    .quad   adt_end - adt_blob

    .balign 8
kernel_blob:
    .incbin "build/payload/kernel-flat.bin"

image_end: