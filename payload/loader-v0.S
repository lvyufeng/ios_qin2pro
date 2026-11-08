/*
 * loader v0 for the QIN 2 Pro: build a boot_args structure and report itself.
 *
 * This is the step between "does anything run at all" and "jump into XNU". It
 * does not start the kernel; it proves the pieces the hand-off needs are real:
 * an embedded Apple Device Tree, a boot_args filled at the offsets XNU reads,
 * and a readable serial console.
 *
 * Layout, following docs/xnu-handoff.md:
 *   boot_args at 0x83000000   (inside the 119.8 MiB free window above DRAM)
 *   ADT embedded right after the code, reached with adr
 *   framebuffer at 0x9d000000, 576x1440, 32bpp
 *
 * Output:
 *   QIN2PRO LOADER v0
 *   adt <16 hex digits>
 *   len <16 hex digits>
 *   args <16 hex digits>
 *   readback: revision/version, virtBase, memSize, dt, cmdline
 *
 * Registers: x20 UART base (never clobbered), x19 boot_args base,
 *            x18/x17 saved links, x1..x6 scratch.
 */

#define UART_BASE       0x70100000
#define BOOT_ARGS       0x83000000
#define DRAM_BASE       0x80000000
#define OWNED_SIZE      0x07000000  /* MUST match the ADT's darwinboot-memory-size;
                                       regenerate both together, or check-adt.py and
                                       this constant will disagree silently. */
#define FB_BASE         0x9D000000
#define FB_ROW_BYTES    0x00000900      /* 576 * 4 */
#define FB_WIDTH        0x00000240      /* 576 */
#define FB_HEIGHT       0x000005A0      /* 1440 */
#define TOP_OF_DATA     0x80FDC000      /* DRAM_BASE + kernel footprint */
#define XNU_VIRT_BASE   0xFFFFFFF007004000  /* kernel __TEXT vmaddr (link base) */
#define KERNEL_FOOTPRINT 0xFDC000       /* from scripts/kernel-loadplan.py */

    .text
    .globl _start
_start:
    /* --- arm64 image header (64 bytes) --- */
    .long   0x14000010          /* code0: b 0x40 */
    .long   0
    .quad   0                   /* text_offset */
    .quad   image_end - _start  /* image_size */
    .quad   0
    .quad   0
    .quad   0
    .quad   0
    .long   0x644d5241          /* "ARM\x64" */
    .long   0

    /* --- entry --- */
    movz    x20, #(UART_BASE & 0xffff)
    movk    x20, #(UART_BASE >> 16), lsl #16
    movz    x19, #(BOOT_ARGS & 0xffff)
    movk    x19, #(BOOT_ARGS >> 16), lsl #16

    adr     x1, msg_banner
    bl      puts

    /* ---- boot_args: Revision 2, Version 2 ---- */
    movz    w6, #0x0202
    strh    w6, [x19]

    /*
     * DarwinBoot (the reference loader) sets:
     *   VirtualBase  = Kernel.Base   (the kernel's link address, not DRAM)
     *   PhysicalBase = where the kernel was actually copied
     *   MemorySize   = the owned window size
     *   DeviceTree   = VirtualBase + (tree_phys - PhysicalBase)
     * so the kernel can translate between the two with one subtraction.
     *
     * Confirmed in the kernel's own entry code (__start -> _start_first_cpu):
     * it computes a link-time address from a runtime one as
     *     adrp target ; add virtBase ; sub physBase
     * which only works when virtBase - physBase equals the distance the image
     * was moved. So in v1 physBase MUST be the address the kernel is copied
     * to, and virtBase MUST be the address it was linked at.
     *
     * Our own load address is only known at run time: the bootloader may
     * relocate us. `adr` gives the current physical address.
     */
    adr     x21, .                      /* our own address: no symbol, no relocation */
    lsr     x21, x21, #12
    lsl     x21, x21, #12               /* page-aligned load address */

    movz    x22, #(XNU_VIRT_BASE & 0xffff)
    movk    x22, #((XNU_VIRT_BASE >> 16) & 0xffff), lsl #16
    movk    x22, #((XNU_VIRT_BASE >> 32) & 0xffff), lsl #32
    movk    x22, #((XNU_VIRT_BASE >> 48) & 0xffff), lsl #48
    str     x22, [x19, #0x08]           /* virtBase */
    str     x21, [x19, #0x10]           /* physBase */

    movz    x6, #(OWNED_SIZE & 0xffff)
    movk    x6, #(OWNED_SIZE >> 16), lsl #16
    str     x6, [x19, #0x18]            /* memSize */

    movz    x6, #(KERNEL_FOOTPRINT & 0xffff)
    movk    x6, #(KERNEL_FOOTPRINT >> 16), lsl #16
    add     x6, x21, x6
    str     x6, [x19, #0x20]            /* topOfKernelData = physBase + footprint */

    /* Video, 48 bytes at 0x28 */
    movz    x6, #(FB_BASE & 0xffff)
    movk    x6, #(FB_BASE >> 16), lsl #16
    str     x6, [x19, #0x28]            /* v_baseAddr */
    str     xzr, [x19, #0x30]           /* v_display */
    movz    x6, #(FB_ROW_BYTES & 0xffff)
    str     x6, [x19, #0x38]            /* v_rowBytes */
    movz    x6, #(FB_WIDTH & 0xffff)
    str     x6, [x19, #0x40]            /* v_width */
    movz    x6, #(FB_HEIGHT & 0xffff)
    str     x6, [x19, #0x48]            /* v_height */
    movz    x6, #32
    str     x6, [x19, #0x50]            /* v_depth */

    /* device tree */
    adr     x7, adt_blob                /* physical address of the tree */
    sub     x8, x7, x21                 /* offset inside the image window */
    add     x8, x8, x22                 /* VirtualBase + offset = what XNU reads */
    str     x8, [x19, #0x60]            /* deviceTreeP (virtual) */
    adr     x8, adt_size
    ldr     x6, [x8]
    str     w6, [x19, #0x68]            /* deviceTreeLength */

    /* command line, at 0x6c */
    adr     x1, cmdline
    add     x2, x19, #0x6c
1:  ldrb    w3, [x1], #1
    strb    w3, [x2], #1
    cbnz    w3, 1b

    /* tail fields */
    str     xzr, [x19, #0x470]          /* bootFlags */
    movz    x6, #(OWNED_SIZE & 0xffff)
    movk    x6, #(OWNED_SIZE >> 16), lsl #16
    str     x6, [x19, #0x478]           /* memSizeActual = window, as DarwinBoot does */

    /* ---- report ---- */
    adr     x1, msg_adt
    bl      puts
    adr     x1, adt_blob
    bl      puthex

    adr     x1, msg_len
    bl      puts
    adr     x8, adt_size
    ldr     x1, [x8]
    bl      puthex

    adr     x1, msg_args
    bl      puts
    mov     x1, x19
    bl      puthex

    /* read back the fields XNU actually looks at */
    adr     x1, msg_rev
    bl      puts
    ldrh    w1, [x19]
    bl      puthex

    adr     x1, msg_virt
    bl      puts
    ldr     x1, [x19, #0x08]
    bl      puthex

    adr     x1, msg_dt
    bl      puts
    ldr     x1, [x19, #0x60]
    bl      puthex

    adr     x1, msg_done
    bl      puts

    /* ---- paint the top band white so a running loader is visible ---- */
    movz    x9, #(FB_BASE & 0xffff)
    movk    x9, #(FB_BASE >> 16), lsl #16
    movz    w6, #0xffff
    movk    w6, #0xffff, lsl #16
    movz    x5, #0xa300
    movk    x5, #0x0002, lsl #16        /* 172800 pixels = 576 x 300 */
2:  str     w6, [x9], #4
    subs    x5, x5, #1
    b.ne    2b

3:  wfi
    b       3b

/* ---- putc: wait for the TX FIFO, give up after ~1M polls ---- */
putc:
    movz    x4, #0x0000
    movk    x4, #0x0010, lsl #16
4:  ldr     w5, [x20, #0x0c]
    and     w5, w5, #0xff00
    cbz     w5, 5f
    subs    x4, x4, #1
    b.ne    4b
    ret
5:  strb    w3, [x20]
    ret

/* ---- puts: x1 = NUL-terminated string ---- */
puts:
    mov     x18, x30
6:  ldrb    w3, [x1], #1
    cbz     w3, 7f
    bl      putc
    b       6b
7:  mov     x30, x18
    ret

/* ---- puthex: x1 = value, prints 16 lowercase hex digits ---- */
puthex:
    mov     x17, x30
    mov     x2, #60
8:  lsr     x3, x1, x2
    and     w3, w3, #0xf
    cmp     w3, #10
    b.lo    9f
    add     w3, w3, #0x57               /* 'a' - 10 */
    b       10f
9:  add     w3, w3, #0x30               /* '0' */
10: bl      putc
    subs    x2, x2, #4
    b.pl    8b
    mov     x30, x17
    ret

msg_banner:
    .asciz  "\r\nQIN2PRO LOADER v0\r\n"
msg_adt:
    .asciz  "adt "
msg_len:
    .asciz  "\r\nlen "
msg_args:
    .asciz  "\r\nargs "
msg_rev:
    .asciz  "\r\nrev "
msg_virt:
    .asciz  "\r\nvirt "
msg_dt:
    .asciz  "\r\ndt(va) "
msg_done:
    .asciz  "\r\nbuilt boot_args, halting\r\n"

cmdline:
    .asciz  "-v debug=0x144 serial=3"

    .balign 8
adt_blob:
    .incbin "build/adt.dtb"
adt_end:

    .balign 8
adt_size:
    .quad   adt_end - adt_blob          /* constant: same section on both sides */

image_end: