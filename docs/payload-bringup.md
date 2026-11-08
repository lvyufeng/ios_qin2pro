# 最小 payload：一条命令验证两个前提

目标里有两个前提没验证：`fastboot boot` 能不能跑、UART 能不能出字。它们可以用**同一个
实验**回答——把一个 169 字节的裸机程序塞进 `boot` 槽跑一次。

## 为什么是裸机而不是 UEFI

HackiOS / UniXNU 都要求设备先有 UEFI 才能加载它们的 loader。SC9863A 没有任何 UEFI 移植，
`UniXNU-Unisoc` 是空壳。所以要证明"这台机器能跑我们造的东西"，最短路径是**不需要 UEFI**：
bootloader 本来就会把 `boot` 分区里镜像的内核段搬到 `kernel_addr` 然后跳进去。

## 它做什么

两个互不依赖的输出通道，谁活着谁给信号：

```
0x00  64 字节 arm64 image 头（code0 = b 0x40，magic "ARM\x64" 在 0x38）
0x40  movz/movk  x0 = 0x70100000      ← UART1 TXD（console=ttyS1）
      adr        x1 = message
      循环：取字符 → 等 STS1[15:8]==0（有界，最多 0x100000 次轮询）→ 写 TXD
      ★ 超时就跳到 framebuffer，绝不无限等
0x80  movz/movk  x9 = 0x9d000000      ← fb_reserved，32 MiB
      把顶部 576×300 像素填成不透明白色
      wfi 死循环
```

预期串口输出：

```
QIN2PRO: payload running
UART1 0x70100000 alive
```

预期panel变化：**屏幕顶部约 21%（300/1440）变成纯白**，其余保持开机 logo。

### 为什么串口等待必须有界

QIN 2 Pro 这类整机很可能**根本没引出调试串口**。如果等 TX FIFO 变成无限循环，
payload 会永远停在那儿，"屏幕没变化"就同时代表"UART 不通"和"代码可能跑了"——
两种结论混在一起，实验就白做了。加了 0x100000 次轮询上限后，两种信道互不阻塞。

### 为什么先 UART 再 framebuffer

framebuffer 的写入是唯一的"可能触发总线错误"的操作（地址有效但映射状态未知）。
放在 UART 之后，串口先给出结论；即使随后写显存出错挂住，日志也已经发出去了。

## 寄存器语义的交叉验证

payload 里每一个偏移和等待条件，都对着厂商内核驱动核过一遍
（`drivers/tty/serial/sprd_serial.c`，取自
`KevinXu07/android_kernel_qin_qin2pro@qin2pro-bringup`）：

| 我的用法 | 驱动里的定义 | 行号 |
| --- | --- | --- |
| `TXD = +0x00` | `#define SPRD_TXD 0x0000` | 46 |
| `RXD = +0x04` | `#define SPRD_RXD 0x0004` | 47 |
| `STS1 = +0x0c` | `#define SPRD_STS1 0x000C` | 58 |
| `TX 计数 = STS1[15:8]` | `#define SPRD_TX_FIFO_CNT_MASK GENMASK(15, 8)` | 60 |
| `IEN = +0x10` / `ICLR = +0x14` | `SPRD_IEN 0x0010` / `SPRD_ICLR 0x0014` | 63 / 70 |
| 等待 `(STS1 & 0xff00) == 0` | `sprd_tx_empty()`: `if (serial_in(port, SPRD_STS1) & SPRD_TX_FIFO_CNT_MASK) return 0;` | 161–167 |
| 发送写 `[base]` | `serial_out(port, SPRD_TXD, ch)` | 342 |

也就是说：等待条件是 **FIFO 计数为 0**（不是某个 ready 位），发数据就是往基址写一个字。
驱动的实现与 payload 完全一致。

## 怎么构建

```sh
python3 scripts/build-payload.py payload          # 汇编 → 抽 .text → build/payload/hello-uart.bin
python3 scripts/build-payload.py bootimg \
    --stock /path/to/stock/boot.img               # 套官方头部 → build/payload/hello-uart-boot.img
```

产出的 boot 镜像**沿用官方 `boot.img` 的头**（页大小 2048、`kernel_addr=0x8000`、
`console=ttyS1,115200n8`），只替换内核段——bootloader 看到的就是它本来就认的布局。

### 一个绕不开的限制：没有 ELF 链接器

本机没有 `ld.lld`（Xcode 不带），`ld64` 只能出 Mach-O。所以整个 payload 写成**零重定位**的
汇编：字符串用 `adr` 相对寻址、跳转用局部数字标号（`1b`/`2f`），汇编器在汇编期就能全部算好。
构建脚本会检查：**只要目标文件里还剩一条重定位就报错退出**，因为那意味着加载后地址是错的。

验收（已通过）：

```
hello-uart.bin: 169 bytes, no relocations
kernel_size=169 kernel_addr=0x8000 magic@0x38=0x644d5241 OK
```

## 怎么用（设备健康时）

```sh
# 1. 先进 fastboot：adb reboot bootloader
# 2. 确认链路活着，别急着 boot
python3 scripts/fastboot-lite.py --libusb <libusb.dylib> getvar version
# 3. 只读不写：download + boot，不碰任何分区
python3 scripts/fastboot-lite.py --libusb <libusb.dylib> boot build/payload/hello-uart-boot.img
```

## 结果怎么读

| 现象 | 说明 | 下一步 |
| --- | --- | --- |
| 串口出字 **且** 屏幕顶部变白 | ✅ 全通：能跑自有镜像、UART1 对、显存也对 | 直接做真正的 loader（ADT + 跳 XNU） |
| 只有屏幕变白，串口无字 | 镜像跑了，**UART 不通**（没引出 / 时钟没开 / 地址不对） | 日志改走 framebuffer；或试点 uart0 `0x70000000` |
| 只有串口出字，屏幕不变 | 镜像跑了，framebuffer 地址或格式不对 | 记录 UART 通路可用，显示另想办法 |
| 两个信道都没反应 | 镜像没被执行，或两者都不可用 | 看下一步的判定 |
| `FAILED (remote: ...)` | bootloader **拒绝**执行 | 记错误码；可能需要签名或别的镜像格式 |
| 设备挂住需要断电 | 跳进了无效代码（或写显存触发了总线错误） | 改成只写 1 个像素/1 个字符的最小探路版 |

**"两个都没反应"要拆开看**：如果 `fastboot boot` 返回 `OKAY` 而设备毫无变化，更可能是
镜像跑了但两个信道都不可用；如果连 `OKAY` 都没有，或者设备直接卡死，那才是没跑起来。
**先看 fastboot 的返回值，再看设备表现**，两者的组合才有意义。

## 风险

- `fastboot boot` 不写闪存，重启即恢复，理论上安全。
- 但 bootloader 若校验镜像签名并拒绝，可能让设备停在 bootloader（需断电重启）。
- payload 往 `0x70100000` 写数据；若该地址的时钟未使能，写操作可能挂起——所以循环里有
  FIFO 等待，一旦不通会停在那儿而不是乱写内存。
- **不要在 gadget 已卡死时再发任何命令**（见 `docs/usb-fastboot-notes.md`）。

## loader v0：从"能跑"到"能构造交接结构"

`payload/hello-uart.S` 只证明"能跑"。再往前一步是 `payload/loader-v0.S`：它**真的构造
`boot_args`**，把 ADT 嵌进镜像，并把自己的结果打印出来。

### 它做什么

| 地址 | 内容 |
| --- | --- |
| `0x83000000` | `boot_args`（`0x480` 字节），字段按 `docs/xnu-handoff.md` 的表填 |
| 镜像内 `adt_blob` | 13 488 字节的 ADT，由 `.incbin "build/adt.dtb"` 嵌入 |
| `0x9d000000` | framebuffer，顶部涂白 |

串口输出（十六进制逐项回读，证明 `adr` 与立即数都对）：

```
QIN2PRO LOADER v0
adt 0000000080000120        ← 树在内存里的物理地址（取决于 bootloader 把我们放哪）
len 00000000000034b0        ← 13488 字节
args 0000000083000000       ← boot_args
rev 0000000000000202        ← Revision 2 / Version 2
virt fffffff007004000       ← 内核链接基址
dt(va) fffffff007001120     ← virtBase + (adt - physBase)
built boot_args, halting
```

怎么读这组数：

- `len` 必须是 `0x34b0`（13488）——说明内嵌的 ADT 完整。
- `adt` 是**运行期**地址，可能每次不同（bootloader 重定位）；它的低 12 位应该和对镜像内
  `adt_blob` 的偏移一致。
- `virt` 必须正好是 `fffffff007004000`。
- `dt(va) - adt` 应该等于 `virt - physBase`，也就是同一个固定差值——这直接证明虚拟/物理
  换算是自洽的。

### 构建

```sh
python3 scripts/build-payload.py payload --source payload/loader-v0.S \
    --output build/payload/loader-v0.bin
python3 scripts/build-payload.py bootimg --payload build/payload/loader-v0.bin \
    --stock /path/to/stock/boot.img --output build/payload/loader-v0-boot.img
```

产物 14 144 字节（代码约 656 + ADT 13 488），**零重定位**，`boot.img` 16 384 字节。

### `boot.img` 头的 `id` 字段

Android boot image 头的偏移 `576` 处有一个 32 字节的 `id`。我们最初**直接抄了原厂的**，
这是错的——原厂那个值用任何标准公式都复现不出来：

```
原厂 id     2934b9dd53ed83ef43f9dfc09b8e9bc5e9faa02c
SHA1(kernel) 6b539acb71d9fd291fc5ae52b0d3cd6e7c45dc0b
```

也不是 AOSP 的 `SHA1(kernel ‖ kernel_size ‖ ramdisk ‖ ramdisk_size ‖ second ‖ second_size)`
（`407c9dba…`）。**它是展锐打包工具自己算的。**

抄过来等于声称"我们的内核哈希是原厂那个"——纯属误导。现在按 AOSP 公式现算，
三个镜像都验证过：

```
hello-uart   id matches AOSP formula: True
loader-v0    id matches AOSP formula: True
loader-v1    id matches AOSP formula: True
```

没有证据表明 U-Boot 会校验这个字段（校验失败通常会有对应的错误串，二进制里没有），
所以它更多是"自描述"的意义。但既然能算对，就不该留一个假的。

### 一个汇编上的坑

`movz` 的立即数必须是汇编期可求的常数，而 `adt_end - adt_blob` 这种**标签差**不行
（报 `immediate must be an integer in range [0, 65535]`）。改成在数据里放一个常量：

```asm
adt_size:
    .quad   adt_end - adt_blob      /* 同一段内两个符号相减，汇编期就算好，不产生重定位 */
```

运行时 `adr x8, adt_size; ldr x6, [x8]` 取出来用。

### 为什么它搬到哪里都能跑

bootloader 可能把镜像搬到 2 MB 对齐的地址再跳（arm64 Linux 头里的 `text_offset` 就是
干这个的）。所以代码必须**可重定位**。核过一遍：loader 里所有绝对立即数只有这些——

| 值 | 用途 |
| --- | --- |
| `0x70100000` | UART1 |
| `0x83000000` | `boot_args` |
| `0x80000000` | DRAM 基址 |
| `0x7000000` | 独占内存大小 |
| `0x80fdc000` | `topOfKernelData` |
| `0x9d000000` | framebuffer |
| `0xffff` | 白色像素 |

全是**故意的固定硬件地址**。所有自己数据的引用（消息串、ADT、长度常量）都用 `adr`
（PC 相对），没有一处依赖"代码被放在哪里"。所以被重定位也不影响。

### v0 与 v1 在 `physBase` 上的差别

v0 **不装内核**，所以 `physBase` 填的是"我们自己被放在哪"（`adr x21, .` 取运行期地址），
`topOfKernelData` 则用计划中的内核占用 `0xfdc000` 估算。这样无论 bootloader 把镜像搬到哪，
打印出来的虚拟/物理换算都是自洽的。

到 v1 真正装内核时，`physBase` **必须等于内核被拷贝到的那个地址**，而且要落在 ADT 声明的
独占窗口内——`DarwinBoot` 就是这么做的：窗口基址 = `Address`，内核拷到 `Address`，
`physBase = Address`，三者对齐。

内核侧确认过换算公式（`osfmk/arm64/arm_vm_init.c:1836`）：

```c
gVirtBase = args->virtBase;
gPhysBase = args->physBase;
gPhysSize = ((gPhysBase + args->memSize) & ~PAGE_MASK) - gPhysBase;
```

即 `[gVirtBase, +memSize)` 映射到 `[gPhysBase, +memSize)`。**虚拟基址填错，整个内核的
地址翻译就是错的**。

### 还没做的

- **不跳 XNU**。这版只构造结构后停机。下一步才是把内核搬到 `0x82000000` 并
  `br` 到 `0x823f8488`（入口偏移 `0x3f8488`）。
- **内核尚未嵌入**。嵌进去镜像会变成约 15.9 MB，`fastboot` 的 `max-download-size` 是否够
  还不知道——设备回来才能查 `getvar:max-download-size`。


## loader v1：真正跳进 XNU

`payload/loader-v1.S` 是链条的最后一段——它不再只是构造结构，而是**把内核搬到位并转移控制权**。

### 内存布局

| 地址 | 内容 | 大小 |
| --- | --- | --- |
| `0x9f000000` | 摊平后的内核镜像 | `0xfdc000` |
| `0x9f100000` | Apple Device Tree | 13 488 |
| `0x9f101000` | `boot_args` | `0x480` |

都在 DRAM 顶部那 1.5 GiB 空隙里（`0x9f000000..0x100000000`，固件保留区到 `0x9f000000` 为止）。
配套 ADT 必须用同一窗口生成：

```sh
python3 scripts/make-s9863a1h10-adt.py --output build/adt-loader.dtb \
    --dram-base 0x9f000000 --dram-size 0x80000000 --owned-memory-size 0x61000000 \
    --cpu-regs 0,0x100,0x200,0x300,0x400,0x500,0x600,0x700
```

### 它做五件事

1. 把内嵌的 `kernel-flat.bin` 拷到 `0x9f000000`（`ldp`/`stp`，32 字节一轮）
2. 把内嵌的 ADT 拷到 `0x9f100000`
3. 在 `0x9f101000` 建 `boot_args`：
   `virtBase = 0xfffffff007004000`、`physBase = 0x9f000000`、
   `memSize = 0x61000000`、`deviceTreeP = virtBase + 0x100000`
4. 对刚写过的区间做 `dc civac` 清缓存（bootloader 可能开着 D-cache）
5. 关 MMU / I-cache / D-cache，`br` 到 `0x9f000000 + 0x3f8488`，`x0 = boot_args`

### 构建

```sh
python3 scripts/build-kernel-image.py build/artifacts/kernel.macho \
    --output build/payload/kernel-flat.bin
python3 scripts/build-payload.py payload --source payload/loader-v1.S \
    --output build/payload/loader-v1.bin
python3 scripts/build-payload.py bootimg --payload build/payload/loader-v1.bin \
    --stock /path/to/stock/boot.img --output build/payload/loader-v1-boot.img
```

产物 16 643 984 字节（内核 16 629 760 + ADT 13 488 + 代码），`boot.img` 约 16.6 MB，
比原厂 36 MB 小，能进 `boot` 分区。

已验证：**零重定位**；内嵌的内核镜像与 ADT 与源文件**逐字节一致**；入口处字节 `f5060014`
与 `kernel-flat.bin` 对应位置相同。

### 未验证的假设（上机后最可能出问题的地方）

1. **bootloader 跳到我们时 MMU/缓存的状态**。若它开着 MMU 且不是恒等映射，第 5 步关 MMU
   之后取指就会错。U-Boot 的 `cleanup_before_linux()` 通常会关掉，但没实测过。
2. **装载地址不与自己重叠**。bootloader 一般把 `boot.img` 的内核段放在 DRAM 低处
   （约 `0x80000000`），而我们搬到 `0x9f000000`，相距 500 MB。若它恰好在 `0x9f000000`
   附近装载，拷贝会覆盖正在执行的代码。
3. **`0x9f000000` 以上确实无人使用**。这是按固件保留区推断的，ATF/bootloader 是否另有
   占用未知。
4. **XNU 能否在非 Apple 平台上走到出日志那一步**——这一步完全没验证过。

### 建议的推进顺序

先跑 `hello-uart`（225 字节，只验证"能不能跑我们的代码 + 串口/屏幕通不通"），
确认无误后再跑 `loader-v1`。**不要一上来就跳 loader v1**——它一次性改变了太多变量，
出问题时分不清是搬运错了、参数错了，还是 XNU 本身起不来。
