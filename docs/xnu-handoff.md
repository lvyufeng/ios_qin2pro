# 把控制权交给 XNU：交接协议

做真正的 loader 之前，必须先知道 XNU 期望被怎么进入。这份文档从两个权威来源整理：
UniXNU 的 `DarwinBoot`（`drivers/DarwinBoot`）和 XNU 源码的
`pexpert/pexpert/arm64/boot.h`。

## 寄存器与机器状态

`DarwinBoot` 的最后一步是一个汇编函数（`DarwinBootPkg/Driver/Entry.S`）：

```asm
// x0=boot_args PA, x1=entry PA, x2=owned PA, x3=owned size,
// x4=framebuffer PA (0 for none), x5=its size in bytes. EL1 only.
ASM_FUNC(DarwinEnter)
  msr daifset, #15        // 关中断
  ; 把 framebuffer 顶部 1/16 涂成绿色（BGRA 0xFF00FF00）
  ; 对 owned 区间做 cache 维护（dc civac / ic iallu）
  bic sctlr_el1, #1       // 关 MMU
  bic sctlr_el1, #4       // 关 D-cache
  bic sctlr_el1, #0x1000  // 关 I-cache
  tlbi vmalle1
  br x1                   // 跳到内核入口
```

**入口约定**：

| 寄存器 | 含义 |
| --- | --- |
| `x0` | **`boot_args` 的物理地址** ← 唯一必需的参数 |
| `x1` | 内核入口物理地址（`br x1` 用，跳完就没意义了） |

进入时：**EL1、MMU 关闭、I/D cache 关闭、DAIF 全屏蔽**。

> 那个"顶部涂绿条"的做法值得记：它是 loader 在跳转前留下的最后痕迹，如果内核没画东西，
> 屏幕上仍能看到绿条——说明"loader 跑到这里了"。我们 `payload/hello-uart.S` 里把顶部
> 涂白是同一个思路，独立想到的，算是对上了。

## `boot_args` 结构

来自 `xnu/pexpert/pexpert/arm64/boot.h`。偏移用编译器 `offsetof` 在 arm64 上实测过
（macOS 与 iOS 的 ABI 一致）：

| 偏移 | 字段 | 类型 | 我们该填什么 |
| --- | --- | --- | --- |
| `0x000` | `Revision` | u16 | `2`（`kBootArgsRevision2`） |
| `0x002` | `Version` | u16 | `2`（`kBootArgsVersion2`） |
| `0x008` | `virtBase` | u64 | **内核链接基址 `0xfffffff007004000`**（不是 DRAM 基址！） |
| `0x010` | `physBase` | u64 | 内核实际被搬到的**物理**地址 |
| `0x018` | `memSize` | u64 | 独占窗口大小（ADT 的 `darwinboot-memory-size`） |
| `0x020` | `topOfKernelData` | u64 | `physBase` + 已用长度（物理） |
| `0x028` | `Video` | 48 B | 6 × u64：`v_baseAddr, v_display, v_rowBytes, v_width, v_height, v_depth` |
| `0x058` | `machineType` | u32 | 机器类型 |
| `0x060` | `deviceTreeP` | u64 | **ADT 的物理地址** |
| `0x068` | `deviceTreeLength` | u32 | ADT 长度 |
| `0x06c` | `CommandLine` | char[1024] | 启动参数 |
| `0x470` | `bootFlags` | u64 | 见 `kBootFlagsDarkBoot` 等 |
| `0x478` | `memSizeActual` | u64 | 实际内存大小 |

`sizeof(boot_args) = 1152`（`0x480`）。

### 这三个字段的语义，我一开始写错了两个

`DarwinBoot.c` 第 745–750 行是权威参照：

```c
Args->VirtualBase      = Kernel.Base;              // 内核链接基址，不是 DRAM 基址
Args->PhysicalBase     = Address;                  // 内核被拷贝到的物理地址
Args->MemorySize       = WindowSize;               // ADT 里声明的独占窗口
Args->MemorySizeActual = WindowSize;
Args->TopOfKernelData  = Address + Used;
Args->DeviceTree       = Kernel.Base + TreeAddress - Address;   // ← 虚拟地址
```

我原先填的是 `virtBase = physBase = 0x80000000`、`deviceTreeP` = 树的物理地址。**两处都错**：

- `VirtualBase` 必须是内核的**链接地址**（`0xfffffff007004000`），XNU 靠它和 `PhysicalBase`
  的差值把一个地址在虚拟/物理之间换算。
- `DeviceTree` 存的是**虚拟地址** = `VirtualBase + (树物理地址 - PhysicalBase)`，不是物理地址。

修正后的 loader 在运行时用 `adr x21, .` 取得自己被搬到哪里（bootloader 可能重定位），
再算出这两个值——所以**不依赖任何写死的装载地址**。

> 这个错误如果留着，现象会是：内核一进去就 panic 或地址翻译错乱，而我们从日志里只看到
> 一串无关的崩溃——极难定位到"启动参数里的一个字段填错了"。

> 注意 `CommandLine` 在 `0x6c` 而不是 `0x70`：`deviceTreeLength` 是 u32，而 `char数组`
> 的对齐要求是 1，所以不补齐。这个 4 字节差很容易算错，实测出来的值以本表为准。

## ADT 的硬性契约（已用检查器固化）

XNU 的平台专家和 CPU 驱动对 ADT 有**字节级**要求，写错一个长度就拒绝启动。这些要求
读自源码并写成了 `scripts/check-adt.py`，可当构建门禁用：

| 要求 | 出处 | 我们的状态 |
| --- | --- | --- |
| `/cpus`、`/arm-io`、`/defaults`、`/options` 必须存在 | `ArmDTPlatform.cpp` 126–141 | ✅ |
| `/chosen` 的 `nvram-bank-size` 必须**正好 4 字节**、`nvram-proxy-data` 非空 | 同上 60–71 | ✅ |
| `/arm-io` 的 `ranges` 必须**正好 24 字节**（总线基址、父基址、长度） | `ArmCpu.cpp` 264–282 | ✅ |
| `/arm-io/intc` 的 `reg` 必须**正好 32 字节**（GICD 址、长、GICR 址、长） | 同上 | ✅ |
| `intc/compatible` 必须是 **11 字节** `"arm,gic-v3\0"` | 同上 | ✅ |
| `redistributor-stride` 只能是 `0x20000` / `0x30000` / `0x40000` | 同上 | ✅ |
| 每个 CPU 节点 `device_type` 为 `"cpu"`，`reg` 前 4 字节是 MPIDR | 同上 238–247 | ✅ |
| `/psci` 声明 `method` | 同上 532 | ✅ |

运行结果（25 项全过）：

```
$ python3 scripts/check-adt.py build/adt.dtb
  ok   intc/reg is exactly 32 bytes (GICD addr, size, GICR addr, size)
  ok   intc/compatible is exactly "arm,gic-v3\0" (11 bytes)
       GICD physical 0x14000000, GICR physical 0x14040000
  ...
all requirements satisfied
```

GIC 物理地址是检查器按 `ArmCpu` 自己的公式 `父基址 + 子偏移` 算出来的，与设备树里
读到的 `0x14000000` / `0x14040000` 一致——这条链路是通的。

## 串口日志这条路，逐环验证过

目标要求"把日志打上展锐 UART"。光把驱动编进内核不够——得确认它**真的会被初始化**。
这条链有四环，逐环查过：

| 环节 | 证据 |
| --- | --- |
| 1. 谁调 `serial_init` | `arm_init_cpu` 内的 `bl _serial_init`（`0xfffffff0073f478c`），早期启动就调 |
| 2. `serial_init` 会不会走到我们的代码 | 见下面的"提前返回陷阱" |
| 3. 注册与调用 | `sprd_uart_probe()` → `register_serial_functions()` → 函数末尾 `fns->init()` 循环 → `sprd_uart_init()` |
| 4. 寄存器语义 | 与厂商驱动 `sprd_serial.c` 逐条一致（见 `docs/payload-bringup.md`） |

### 提前返回陷阱

`serial_init()` 里这段**在我们的 probe 之前**：

```c
soc_base = pe_arm_get_soc_base_phys();
if (soc_base == 0) {
        uart_initted = true;
        return 0;            // ← 直接返回，永远到不了 sprd_uart_probe()
}
...
sprd_uart_probe();           // 我们的 probe 在这里
```

而 `pe_arm_get_soc_base_phys()` 的取值方式是：

```c
if (SecureDTFindEntry("name", "arm-io", &entryP) == kSuccess) {
        SecureDTGetProperty(entryP, "device_type", (void const **)&tmpStr, &prop_size);
        strlcpy(gPESoCDeviceTypeBuffer, tmpStr, SOC_DEVICE_TYPE_BUFFER_SIZE);  // 没有判空！
        SecureDTGetProperty(entryP, "ranges", (void const **)&ranges_prop, &prop_size);
        gPESoCBasePhys = *(ranges_prop + 1);   // ranges[1] = 父总线基址
}
return 0;                        // 找不到 arm-io 就返回 0
```

两个后果：

1. **ADT 里必须有 `/arm-io` 节点，且 `ranges` 的第二个 64 位值是父总线基址**。
   我们的是 `[0, 0x14000000, 0x5D500000]` → `soc_base = 0x14000000`，非 0 ✅
2. **`device_type` 缺失会让 `strlcpy` 从野指针读**——直接崩在启动早期。我们的
   `arm-io` 有 `device_type = "arm-io"` ✅

这两条现在是 `scripts/check-adt.py` 的检查项（`/arm-io` 存在、`ranges` 正好 24 字节）。

> 换句话说：**如果当初 ADT 少了 `device_type`，内核会在初始化串口之前就因为
> `strlcpy` 崩溃**，而现象是"没有任何输出"——和"串口没接"完全一样，极难排查。

## 入口处的汇编契约（从二进制直接读出）

`__start` 只有一条指令：`b _start_first_cpu`。真正开始干活的是后者，它的前几十条把
loader 的契约写得明明白白：

```asm
_start_first_cpu:
        msr     OSLAR_EL1, xzr
        msr     DAIFSet, #0xf
        mov     x20, x0              ; ★ x0 就是 boot_args 指针
        adrp    x0, _LowExceptionVectorBase
        msr     VBAR_EL1, x0
        ldr     x22, [x20, #0x8]     ; virtBase
        ldr     x23, [x20, #0x10]    ; physBase
        ldr     x24, [x20, #0x18]    ; memSize
        ldr     x26, [x20, #0x470]   ; bootFlags
        adrp    x0, _excepstack_top
        add     x0, x0, x22          ; 运行时地址 + virtBase
        sub     x0, x0, x23          ;            - physBase
        msr     SPSel, #0x1
        mov     sp, x0               ; ← 异常栈
```

### 这段代码说明了两件事

**1. `x0` 必须是 `boot_args` 的地址** —— 和 `DarwinBoot` 的 `br x1`（x0=Args）一致，
双重确认。

**2. `virtBase - physBase` 就是内核被搬动的距离**

`adrp` 在 MMU 关闭时取到的是**运行期地址**（即物理地址），所以
`运行时地址 + (virtBase - physBase)` 恰好还原出**链接期地址**。内核就是这样在
"MMU 关着、跑在物理地址"的状态下，把栈指针指向链接期的 `_excepstack_top`。

由此得到一条硬性规则：

> **`physBase` 必须是内核镜像实际被拷到的物理地址**，`virtBase` 必须是它的链接基址。
> 两者之差错了，内核算出来的每一个符号地址都会偏——栈会指向野内存。

这也解释了 13 轮里那次修正为什么是对的：`virtBase` 填 DRAM 基址会让差值变成
`0x80000000 - 0x80000000 = 0`，于是所有 `adrp` 结果都保持物理值，内核把物理地址当
虚拟地址用，`sp` 指着低内存的随机位置。

### 内核还会从内存里读自己的 Mach-O 头

`_start_first_cpu` 后续有一段：

```asm
adrp    x0, 0xfffffff007004000      ; 镜像基址（链接地址的运行时对应）
ldr     w1, [x0, #0x18]             ; mach_header_64.flags
tbz     w1, #0x1f, ...              ; bit31 没置位就跳过
ldr     w1, [x0, #0x20]             ; 第一条 load command 的 cmd
cmp     w1, #0x19                   ; LC_SEGMENT_64
b.ne    <self>                      ; 不匹配就死循环
ldr     x1, [x0, #0x38]             ; 第一个段的 vmaddr
sub     x1, x0, x1                  ; 运行时地址 - 链接地址 = −slide
```

**含义：装载时不能剥掉 Mach-O 头**，内核会在运行期按结构体偏移去读它。
（我们这份内核的 `flags = 0x00218001`，bit31 未置位，所以走跳过分支；但只要头部结构在，
两条路都安全。）

这条也解释了为什么"摊平"必须保留 `__TEXT` 的起始内容原样——`__TEXT` 的第一个字节就是
Mach-O 头，摊平只改变段的位置，不改段内字节。

## 冷启动路径审计：哪些属性是真·前置条件

按上一轮发现崩溃点的经验，把 `pe_init.c` 的冷启动路径逐条核了一遍。结论比预期乐观，
但过程里踩到两个**看着致命、其实不生效**的坑，值得记下来免得以后重复怀疑。

### 冷启动根本不走这段

`pe_init.c` 里：

```c
if (!vm_initialized) {
        SecureDTInit(...);
        pe_identify_machine(boot_args_ptr);      // ← 冷启动走这里
} else {
        pe_arm_init_interrupts(args);            // ← 只有唤醒/二次初始化才走
        pe_arm_init_debug(args);
}
```

而 `pe_arm_map_interrupt_controller()`——就是那个要求 `interrupt-controller == "master"`
和 `device_type == "timer"` 的函数——**只被 `pe_arm_init_interrupts()` 调用**。全仓搜过，
没有别的调用点。所以：

- 我们 ADT 里 `interrupt-controller` 是**空属性**（不是 `"master"`）→ 不影响开机
- `timer` 节点**没有 `device_type`** → 不影响开机

> arm64 的中断控制器是 `ArmCpu::mapGic()` 直接按 `/arm-io/ranges` 和
> `/arm-io/intc/reg` 映射的（要求字节精确，见上节表），跟这两个字符串属性无关。

### 真正会 assert 的地方

`pe_identify_machine()` 开头：

```c
err = SecureDTLookupEntry(NULL, "/cpus", &cpus);
assert(err == kSuccess);
err = SecureDTInitEntryIterator(cpus, &iter);
assert(err == kSuccess);
```

DEVELOPMENT 构建里 `assert` 是真会 panic 的，所以 **`/cpus` 必须存在**。
然后它只从 `state == "running"` 的 CPU 上读 `timebase-frequency`——如果一个 running 的都没有，
时钟信息就全是默认值，时序从一开始就是错的（不一定立刻崩，但后面到处出问题）。

这两条已经进了 `scripts/check-adt.py`：

```
ok   /cpus has a cpu with state "running"
ok   a running cpu carries timebase-frequency
```

### `/pram` 缺失的后果没有想象中严重

`gPanicBase` 来自 ADT 的 `/pram` 节点 + `/chosen/embedded-panic-log-size`，我们两者都没有。
但 `PE_get_offset_into_panic_region()` 里那几个 `assert(gPanicBase != 0)` 的调用点全在
`pexpert/i386/pe_init.c`——**x86 专用**，arm64 不会执行到。

所以实际后果只是：**panic 日志不会持久化到内存区**（重启后读不到），但 panic 文字照样
从 console 打出来。对当前阶段够用。若将来需要"死机后取证"，再补 `/pram`。

## 内存放哪：一次真实的地址冲突

`chosen/darwinboot-memory-size` 告诉 XNU "你独占多少内存"，窗口是从 `dram-base`
起算的。原先填的是 `0x10000000`（256 MB），也就是 `0x80000000..0x90000000`——
而这段里压着固件的多个保留区：

| 保留区 | 范围 | 大小 |
| --- | --- | --- |
| `pstore` | `0x877c0000..0x87800000` | 0.2 MB |
| `sipc-mem` | `0x87800000..0x88000000` | 8 MB |
| `wcn-mem` | `0x88000000..0x88300000` | 3 MB |
| `gnss-mem` | `0x88300000..0x8845a800` | 1.4 MB |
| **`cp-modem`** | `0x89600000..0x8ee50000` | **88 MB** |

后果是 XNU 会把 modem 的内存当成自己的空闲页分配出去——这不是"可能有问题"，
是**必然踩踏**。

### 安全的窗口

`0x80000000` 到第一个保留区 `0x877c0000` 之间是 **119.8 MB** 连续空闲。所以：

- 生成器默认改为 `0x7000000`（112 MB），留 7.8 MB 余量；
- 新增 `RESERVED_WINDOWS` 表，超出安全范围直接**报错退出**（`--allow-reserved-overlap`
  可强制覆盖，仅供实验）；
- `scripts/check-adt.py` 也会独立复查一遍，改坏了会 FAIL。

```
$ python3 scripts/make-s9863a1h10-adt.py --owned-memory-size 0x10000000 ...
owned memory 0x80000000..0x90000000 overlaps pstore (...), cp-modem (...);
pick a smaller --owned-memory-size or pass --allow-reserved-overlap
```

### 112 MB 够不够？看看别人用多大

翻了 UniXNU 的设备记录，实际部署的窗口都比我们大得多：

| 设备 | `owned_memory_size` |
| --- | --- |
| oneplus13t | `0x100000000`（4 GiB） |
| ishtar | `0x130000000`（4.75 GiB） |
| uke-tonghuaqwq | `0x130000000`（4.75 GiB） |
| gta4l | `0x80000000`（2 GiB） |

而且 `uke-tonghuaqwq` 的记录里有一句关键的话：**"the 320 MiB ceiling was lifted"** ——
他们在 320 MiB 上撞过墙。

我们因为保留区切割只能拿到 112 MB，**明显偏小**。

### 备选：顶部那 1.5 GiB

`0x9f000000..0x100000000` 是 DRAM 里最大的一块连续空闲（framebuffer 正好在 `0x9f000000`
结束）。`DarwinBoot` 会把 `chosen/dram-base` 改写成它实际分配的窗口，所以窗口**不必**从
物理 DRAM 起点开始。可以直接这样生成：

```sh
python3 scripts/make-s9863a1h10-adt.py --output build/adt.dtb \
    --dram-base 0x9f000000 --dram-size 0x80000000 \
    --owned-memory-size 0x61000000 \
    --cpu-regs 0,0x100,0x200,0x300,0x400,0x500,0x600,0x700
```

检查器两种配置都放行：

```
ok   owned window 0x80000000+0x7000000 clears firmware carve-outs    (112 MB，默认)
ok   owned window 0x9f000000+0x61000000 clears firmware carve-outs   (1.5 GiB，备选)
```

**建议顺序**：先用默认的 112 MB 试——它落在最"干净"的空隙里，即使 XNU 因内存不足而
panic，那本身也是我们要的日志，能定位到具体阶段；确认真的是内存不够，再换 1.5 GiB 配置。

### DRAM 里的其他空隙

内核镜像本身要 15.9 MB（见下节）。按保留区切完，够大的空隙有：

| 空隙 | 大小 |
| --- | --- |
| `0x80000000..0x877c0000` | 119.8 MB |
| `0x8845a800..0x89600000` | 17.6 MB |
| `0x8ee50000..0x94000000` | 81.7 MB |
| `0x96100000..0x98100000` | 32.0 MB |
| `0x9f000000..0x100000000` | 1552 MB |

## 内核镜像本身怎么摆

`scripts/kernel-loadplan.py` 解析 `kernel.macho` 的段表，算出"搬到哪、跳哪"：

```
$ python3 scripts/kernel-loadplan.py build/artifacts/kernel.macho --base 0x80000000
  __TEXT             copy 0x00164000 bytes from file+0x0 to 0x80000000
  __DATA_CONST       copy 0x000dc000 bytes from file+0x164000 to 0x80164000
  __TEXT_EXEC        copy 0x00808000 bytes from file+0x240000 to 0x80240000
  ...
  entry              branch to 0x803f8488
  region ends at 0x80fdc000
```

要点：

- 18 个段，只有 **10 个** `filesize > 0`，其余是 `vmsize = 0` 的占位（prelink 相关）。
- `__DATA` 的 `vmsize`（`0x11c000`）**大于** `filesize`（`0x44000`），loader 必须把差额**清零**。
- 整个镜像占 `0xfdc000`（15.9 MB）。
- 入口在镜像基址 + `0x3f8488`。

### ⚠️ 不能把整个文件一次性 memcpy 过去

这是我差点写错的地方。直觉上"文件按顺序排，搬到基址就行"——**错**。

`__DATA` 的 `vmsize - filesize = 0xd8000`（BSS）**在文件里根本不存在**。于是它后面三个段
的文件偏移整体前移了 `0xd8000`：

| 段 | 文件偏移 | 应放的虚拟地址 |
| --- | --- | --- |
| `__BOOTDATA` | `0xa98000` | `0xfffffff007b74000` |
| `__LINKINFO` | `0xb24000` | `0xfffffff007c00000` |
| `__LINKEDIT` | `0xb68000` | `0xfffffff007c44000` |

如果按"整体搬运"来做，这三个段会各自**低 `0xd8000`**，也就是说 `__BOOTDATA`——内核启动
数据所在——会落在错误位置。现象会是内核跑起来后莫名其妙地崩，而反汇编看起来完全正常。

**正确做法**（`DarwinBoot` 就是这么做的）：

```c
for (Index = 0; Index < Kernel.Count; Index++) {
    CopyMem ((VOID *)(UINTN)(Address + Kernel.Segments[Index].Address - Kernel.Base),
             (UINT8 *)Image + Kernel.Segments[Index].FileOffset,
             (UINTN)Kernel.Segments[Index].FileSize);
}
```

逐段按 `fileoff → vmaddr` 拷贝，再把两处 BSS 清零：

```
__DATA      0xd8000 字节，位于 va 0xfffffff007a9c000
__LINKEDIT  0xd88   字节，位于 va 0xfffffff007fdf278
```

### 解法：在主机上先摊平

既然"逐段搬"容易错，就不要让 loader 去学 Mach-O。`scripts/build-kernel-image.py` 在主机上
把段表应用一遍，产出**文件偏移 = 虚拟偏移**的镜像：

```sh
python3 scripts/build-kernel-image.py build/artifacts/kernel.macho \
    --output build/payload/kernel-flat.bin
# 16629760 bytes (0xfdc000), 15741560 segment bytes placed, entry 0x3f8488
```

产物的自检（逐段比对前 64 字节 + 入口）：

```
__TEXT             OK      __DATA             OK
__DATA_CONST       OK      __BOOTDATA         OK
__TEXT_EXEC        OK      __LINKINFO         OK
...
naive copy would put __BOOTDATA at 0xa98000, correct is 0xb70000 (off by 0xd8000)
entry 0x3f8488: flat f5060014 file f5060014
```

`15741560` 正好等于原文件大小——所有字节都有着落，没有搬运遗漏。

于是 loader 的工作退化成一件事：

> 把 `kernel-flat.bin` 的 `0xfdc000` 字节拷到装载基址，`br` 到基址 `+ 0x3f8488`，
> `x0` = `boot_args`。

不用解析 Mach-O、不用处理 BSS 空洞、不用怕段顺序。**把"容易写错的逻辑"从跑在设备上的
汇编，前移到可以在主机上验证的构建步骤**——这是这一轮最实质的改进。

`scripts/kernel-loadplan.py` 也会把这条警告和 BSS 清单打出来，万一以后换了内核版本，
两条路都能立刻发现布局变了。

## 我们的 loader 需要做什么

按这个协议，从 `payload/hello-uart.S` 往前走一步，loader 要完成：

1. **建 ADT**（Apple Device Tree）。`scripts/make-s9863a1h10-adt.py` 已经能生成，
   但那是给 UniXNU 构建系统用的独立文件；loader 要么内置一段生成代码，要么把预生成的
   ADT 跟在镜像后面搬进内存。
2. **填 `boot_args`**：上面的表，`Revision/Version` 必须对，`deviceTreeP/Length` 最关键。
3. **视频信息**：`0x9d000000`、行字节数、576×1440、位深。
4. **把内核搬到它要的物理地址**，做 cache 维护。
5. **关 MMU/cache、屏蔽中断，`br` 到内核入口，x0 = boot_args**。

## 还没搞清的部分

- ~~内核入口地址~~ **已确定**：`build/artifacts/kernel.macho` 的 `LC_UNIXTHREAD` 给出
  `pc = 0xfffffff0073fc488`，而 `__TEXT` 的 `vmaddr = 0xfffffff007004000`。
  所以入口相对镜像基址的偏移是 **`0x3f8488`**，物理入口 = 装载基址 + `0x3f8488`
  （这正是 `DarwinBoot` 里 `Address + Kernel.Entry - Kernel.Base` 的算法）。
  链接地址是构建时传的 `ARM64_LINK_ADDR=0xfffffff007004000`。
- **`machineType` 的取值**：Apple 平台的枚举值，SC9863A 该填什么未知。
- **ADT 的内容是否够**：我们生成的 ADT 只有 CPU/GIC/UART/framebuffer，XNU 的
  `ArmDTPlatform` 还需要哪些节点没验证过。
- **XNU 是否能在无 Apple 平台专家的情况下走到 `launchd`**：这是 UniXNU/HackiOS 花最多
  力气的地方，我们离得还很远。

## 已确定的数字

| 项 | 值 | 来源 |
| --- | --- | --- |
| `__TEXT` vmaddr | `0xfffffff007004000` | `otool -l` |
| 入口 VA | `0xfffffff0073fc488` | `LC_UNIXTHREAD` 的 `pc` |
| 入口偏移（相对镜像基址） | **`0x3f8488`** | 上两者相减 |
| `boot_args` 大小 | `0x480`（1152） | 编译器 `sizeof` |

## 参考

- `UniXNU/drivers/DarwinBoot/DarwinBootPkg/Driver/Entry.S`（交接汇编）
- `UniXNU/drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c`（`DarwinEnter` 调用点，
  第 821 行附近）
- `xnu/pexpert/pexpert/arm64/boot.h`（`boot_args`）