/*
 * Minimal AArch64 payload for the QIN 2 Pro: prove the bootloader runs an image
 * we built, and try two independent output channels.
 *
 *   1. UART1 at 0x70100000 (console=ttyS1 in the stock cmdline), with a bounded
 *      wait: a dead or unclocked UART must not stop the rest.
 *   2. The framebuffer reservation at 0x9d000000, 32 MiB (fb_reserved in the
 *      device tree): paint the top band white, so a running payload is visible
 *      on the panel even when no debug UART is wired.
 *
 * Begins with a 64-byte arm64 image header so a bootloader that expects one
 * accepts it. code0 is `b 0x40` — branch over the header into the code below.
 *
 * Every reference is section-local (adr, numeric labels), so the object has no
 * relocations and its .text can be copied out verbatim: this host has no
 * aarch64 ELF linker.
 *
 * Registers: x0 UART base, x1 message cursor, w2 char, w3 STS1, x4 timeout,
 *            x9 framebuffer, w6 fill word, x5 pixel count.
 */

    .text
    .globl _start
_start:
    /* --- arm64 image header (64 bytes) --- */
    .long   0x14000010          /* code0: b 0x40 (16 instructions ahead) */
    .long   0                   /* code1: unused */
    .quad   0                   /* text_offset: image loads where it is put */
    .quad   image_end - _start  /* image_size */
    .quad   0                   /* flags: little endian, 4K pages */
    .quad   0                   /* res2 */
    .quad   0                   /* res3 */
    .quad   0                   /* res4 */
    .long   0x644d5241          /* magic: "ARM\x64" */
    .long   0                   /* res5 */

    /* --- entry (offset 0x40): UART1 --- */
    movz    x0, #0x0000
    movk    x0, #0x7010, lsl #16        /* x0 = 0x70100000, UART1 TXD */

    adr     x1, message
1:  ldrb    w2, [x1]
    cbz     w2, 3f
    movz    x4, #0x0000
    movk    x4, #0x0010, lsl #16        /* 0x00100000 polls before giving up */
2:  ldr     w3, [x0, #0x0c]             /* STS1 */
    and     w3, w3, #0xff00             /* TX FIFO count */
    cbz     w3, 5f                      /* empty: ready to send */
    subs    x4, x4, #1
    b.ne    2b
    b       3f                          /* no UART: fall through to the panel */
5:  strb    w2, [x0]                    /* TXD */
    add     x1, x1, #1
    b       1b

    /* --- framebuffer: fill 576 x 300 pixels of the top band with white --- */
3:  movz    x9, #0x0000
    movk    x9, #0x9d00, lsl #16        /* 0x9d000000, fb_reserved */
    movz    w6, #0xffff
    movk    w6, #0xffff, lsl #16        /* opaque white, any 32-bit order */
    movz    x5, #0xa300
    movk    x5, #0x0002, lsl #16        /* 0x2a300 = 172800 pixels = 576 x 300 */
4:  str     w6, [x9], #4
    subs    x5, x5, #1
    b.ne    4b

6:  wfi
    b       6b

message:
    .asciz  "\r\nQIN2PRO: payload running\r\nUART1 0x70100000 alive\r\n"
image_end: