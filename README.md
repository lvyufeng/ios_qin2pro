# ios_qin2pro

在 **QIN 2 Pro**（UNISOC **SC9863A** / SharkL3，板号 `s9863a1h10`）上 bring-up Darwin/XNU 内核。

本仓库是 [UniXNU](https://github.com/UniXNU/UniXNU) 的**设备/SoC 层**，参照树是展锐平台的
[`hxzbaka/UniXNU-t610yp`](https://github.com/hxzbaka/UniXNU-t610yp)（UMS512 / T610）。

> **权限与边界**：目标是"内核能起来、日志能出来"，不是可日常使用的系统。
> 全程 RAM 运行、不写分区。iOS 用户态与激活不在本项目范围内（见 HackiOS 的 `LEGAL.md`）。

## 设备事实（已实测）

| 项目 | 值 |
| --- | --- |
| SoC | UNISOC SC9863A（`sp9863a`，SharkL3），8 × Cortex-A55 |
| 板号 | `s9863a1h10`（`ro.product.device`） |
| 内存 | 1.82 GiB 可见，DRAM 基址 `0x80000000`，物理 2 GiB |
| 屏幕 | 576 × 1440，density 307 |
| GPU | Imagination PowerVR Rogue **GE8322**（无开源 Darwin 驱动） |
| 固件 | Android 9 / SDK 28 / arm64-v8a，user + release-keys，`ro.secure=1` |
| bootloader | 已解锁（`ro.boot.verifiedbootstate=orange`） |
| 分区 | A-only：`boot` `recovery` `dtbo` `vbmeta` `uboot` `sml` `trustos` …（无 `_a/_b`） |

## 关键地址（来源见 `docs/hardware-s9863a1h10.md`）

| 资源 | 地址 | 来源 |
| --- | --- | --- |
| UART0（ttyS0） | `0x70000000`（IRQ `GIC_SPI 2`） | 固件 DTB（`dtc` 反编译） |
| **UART1（ttyS1）** | **`0x70100000`**（IRQ `GIC_SPI 3`） | 同上；**console 就是它** |
| GICD | `0x14000000`（`0x20000`） | 厂商 `sc9863a.dtsi` |
| GICR | `0x14040000`（`0x100000`，stride `0x20000`） | 同上 |
| framebuffer 保留区 | **`0x9d000000`**（`0x2000000`） | 固件 DTB `fb_reserved` |
| 架构定时器 PPI | 13 / 14 / 11 / 10 | 厂商 DTS |

与 T610 的差异（同一套代码，改这几个数就能对上）：

| | T610YP | **SC9863A（本机）** |
| --- | --- | --- |
| UART | `0x70100000` | `0x70100000` ✅（但 console 是 uart1） |
| GICD / GICR | `0x12000000` / `0x12040000` | **`0x14000000` / `0x14040000`** |
| CPU | 6×A55 + 2×A75 | **8×A55**（两簇：0-3 / 4-7） |
| DRAM base | `0x40000000` | **`0x80000000`** |
| framebuffer | `0x9e000000` | **`0x9d000000`** |

## 路线

1. **里程碑 1（当前）**：改版 XNU 进入内核，日志打出来（UART 或 framebuffer）。
2. 里程碑 2：定时器、CPU bring-up、PSCI。
3. 里程碑 3：存储（eMMC/UFS 只读）、输入。
4. 之后：GPU（PowerVR，**无现成方案**，预计长期为空）。

## 目录

```
board.toml                      板级配置（进 UniXNU 构建系统）
feeds.toml                      XNU 源码版本锁定
configs/s9863a1h10_defconfig    内核配置
patches/common/                 与 SoC 无关的通用补丁（来自 ios-sweet）
patches/sc9863a/                SC9863A 专有补丁（polled UART，format 1 解析器用）
scripts/make-s9863a1h10-adt.py  生成最小 Apple Device Tree
scripts/dump-adt.py             反解 ADT，无 console 时用来核对
scripts/install-device-patches.py  把设备补丁放进 format 1 构建会扫的位置
scripts/extract-pac.py          官方 .pac 固件的分区列表/提取
scripts/fastboot-lite.py        libusb + ctypes 的最小 fastboot 客户端
scripts/bringup.sh              一条命令走完：进 fastboot → 探活 → 可选刷写
scripts/build-payload.py        汇编裸机 payload 并套官方 boot.img 头
scripts/check-adt.py            按 XNU 源码的字节级要求校验 ADT
scripts/kernel-loadplan.py      解析内核段表，算出 loader 该搬哪、跳哪
scripts/build-kernel-image.py   把内核摊平成可直接 memcpy 的镜像
payload/hello-uart.S            225 字节的 UART/显存试探程序
payload/loader-v0.S             构造 boot_args + 嵌入 ADT 的 loader
dts/s9863a1h10.dts              板级设备树（引用 SoC 层）
soc/UniXNU-sc9863a-common/      可复用的 SC9863A 共享 SoC 层（format 2 规范）
docs/sources.md                 全网可用源码/资源清单
docs/hardware-s9863a1h10.md     硬件事实（逐项注明来源）
docs/port-plan.md               分阶段执行计划与完成判据
docs/build-log.md               构建记录：怎么编出内核、踩了哪些坑
docs/firmware-extraction.md     从官方固件挖出真实 DTB 与 console 参数
docs/usb-fastboot-notes.md      fastboot 接口观察 / 卡死原因 / 恢复办法
docs/payload-bringup.md         最小 payload：一条命令验证两个前提
docs/bootloader-analysis.md     从固件查证 bootloader 支持哪些 fastboot 命令
docs/recovery.md                刷机前的回滚准备与原厂内核凭据
docs/xnu-handoff.md             把控制权交给 XNU 的协议（boot_args / 入口偏移）
```

## 构建内核

需要一份 UniXNU 检出，以及 `ninja`、可用的 `cmake`、Xcode（iPhoneOS SDK）：

```sh
cd /path/to/UniXNU
./unixnu fetch        -D /path/to/ios_qin2pro
./unixnu defconfig s9863a1h10_defconfig -D /path/to/ios_qin2pro
./unixnu build kernel --skip-prelink   -D /path/to/ios_qin2pro
```

产物在 `<检出>/out/s9863a1h10/kernel/kernel.macho`。`--skip-prelink` 表示不做
Apple kernelcache 预链接（当前没有 IPSW）。

## 自检

DTS（需要 `dtc`）：

```sh
dtc -I dts -O dtb -i soc/UniXNU-sc9863a-common/dts \
    -o build/hardware.dtb dts/s9863a1h10.dts
dtc -I dtb -O dts build/hardware.dtb | grep -E "serial@|bus@|memory@|framebuffer@"
```

ADT：

```sh
python3 scripts/make-s9863a1h10-adt.py --output build/adt.dtb \
    --dram-size 0x80000000 --owned-memory-size 0x10000000 \
    --cpu-regs 0,0x100,0x200,0x300,0x400,0x500,0x600,0x700
python3 scripts/dump-adt.py build/adt.dtb
```

配置解析（需要一份 UniXNU 检出）：

```sh
./unixnu status  -D /path/to/ios_qin2pro
./unixnu defconfig s9863a1h10_defconfig -D /path/to/ios_qin2pro
```

## 验证记录

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| UART 补丁可应用 | `git apply --check -p1`（对 xnu-8796.141.3） | ✅ rc=0 |
| 补丁上下文 | `patch -p1 --dry-run` | ✅ 两个文件均通过 |
| DTS 编译 | `dtc -I dts -O dtb` | ✅ rc=0，无警告 |
| DTS 地址 | `dtc -I dtb -O dts` | ✅ GICD `0x14000000`、GICR `0x14040000`、UART0 `0x70000000`、UART1 `0x70100000`、memory `0x80000000`+2GiB、framebuffer `0x9d000000` |
| 设备仓库解析 | `./unixnu status -D .` | ✅ 识别为 `s9863a1h10`，套出 base profile |
| 配置生成 | `./unixnu defconfig s9863a1h10_defconfig` | ✅ 生成 26 行 `.config` |
| 内核构建 | `./unixnu build kernel --skip-prelink` | ✅ `kernel.macho` 15 741 560 字节，Mach-O arm64 |
| 串口基址进内核 | `llvm-objdump -d \| grep 0x70100000` | ✅ `mov w0, #0x70100000` → `bl _ml_io_map` |
| 固件 DTB 提取 | `scripts/extract-pac.py` + `dtc` | ✅ 6318 行真实设备树；提取结果与手工逐字节一致 |
| 驱动进内核 | `nm kernel.macho \| grep sprd_uart` | ✅ 7 个 `sprd_uart*` 符号 |
| ADT 生成/反解 | 见上 | ✅ 13 488 字节，8 CPU，数值符合预期 |

详细过程与踩坑见 `docs/build-log.md`。

## 现状

- **内核已编出**：`build/artifacts/kernel.macho`，arm64 `MH_EXECUTE`，
  sha256 `e12c29ae…eb4504`，基于 `xnu-10063.141.1`，内含 SC9863A **ttyS1（`0x70100000`）** 的 polled UART 驱动。
- **未做过预链接**（没有 IPSW/kernelcache），`kernel.json` 记 `prelink_skipped: true`。
- **未在任何硬件上运行过**，`boot_verified: false`。
- 设备仓库按 **format 1** 被 UniXNU CLI 解析（`schema_version` 未声明）。
  format 2 需要 `uefi-<device>` 固件仓库与 `ipados-*` OS profile，**SC9863A 两者都没有**；
  共享 SoC 层已按 format 2 规范单独建立，可独立成库。
- 设备侧未有任何启动尝试：手机仍停在 bootloader 的 fastboot，等待定制 fastboot 工具。

## 已知风险

- **串口未必可达**：手机通常不引出 debug UART，`0x70100000`（ttyS1）可能打不出东西。
  备选是 framebuffer（`0x9d000000`，固件 DTB 里的 `fb_reserved`）或 loader 的 USB 日志通道。
- **定时器频率未知**：固件通过 `CNTFRQ` 设置，DTS 里不写。默认按 `26000000`，错了只影响计时，不影响出日志。
- **fastboot 非标准**：本机 bootloader 的 fastboot 无法被 Android platform-tools 识别，
  社区做法是使用展锐定制 fastboot（见 `docs/sources.md`）。
- **补丁双份**：`patches/sc9863a/` 与 `soc/.../patches/xnu/` 各有一份 UART 补丁，
  内容相同（后者是规范位置）。迁移到 format 2 后应删除设备侧那份。