# SC9863A / s9863a1h10 硬件事实

每个数字都注明来源，方便复核。

## 来源编号

- **[A]** 厂商内核 `KevinXu07/android_kernel_qin_qin2pro@qin2pro-bringup`，
  `arch/arm64/boot/dts/sprd/sc9863a.dtsi`
- **[B]** 同仓库 `arch/arm64/boot/dts/sprd/sp9863a-1h10-overlay.dts`（本机板级 overlay）
- **[C]** 同仓库 `arch/arm64/boot/dts/sprd/sharkl3.dtsi`
- **[D]** 主线 `torvalds/linux` `arch/arm64/boot/dts/sprd/{sc9863a.dtsi,sharkl3.dtsi,sp9863a-1h10.dts}`
- **[E]** 本机 `adb` 实测（Android 9）
- **[F]** **官方固件里的真实设备树**：`Qin2pro_s9863a1h10_v1.1.0.pac` → `DTB` → `dtb.img`
  （`SPRD` 容器，FDT 在 `0x800`）→ `dtc -I dtb -O dts`。提取步骤见
  `docs/firmware-extraction.md`。以及同一固件 `BOOT` 分区的 `boot.img` 头部 cmdline。

> **[F] 优先于 [A][B][C][D]**：前三者是「参考板」源码，[F] 是这台机器实际跑的树。

## CPU

| 项 | 值 | 来源 |
| --- | --- | --- |
| 核数 / 型号 | 8 × Cortex-A55（`CPU part 0xd05`） | [E] `/proc/cpuinfo` + [A] |
| 簇 | 两簇：CPU0-3（`cpufreq_clus0`, `fan53555_dcdc`）/ CPU4-7（`cpufreq_clus1`, `vddcpu`） | [A] |
| MPIDR | `0x0 0x100 0x200 0x300 0x400 0x500 0x600 0x700` | [A][D] |
| 定时器频率 | **26 MHz**（`0x18cba80`） | [F] `/timer/clock-frequency` |
| 定时器中断 | 非安全物理 PPI 14、hypervisor PPI 10（固件只列这两个） | [F] |
| 启动方式 | `enable-method = "psci"`，PSCI 0.2，`method = "smc"` | [A] |
| 缺的特性 | **无 PAuth**（`/proc/cpuinfo` Features 无 `apa/api/paca/pacg`），即 ARMv8.2 | [E] |

> 无 PAuth 不影响跑 arm64e 用户态 —— MT6768（同为无 PAC 的 A75/A55）已经跑通 iOS 16.5，
> 靠的是 `ios-sweet-0005-non-pac-build-fixes.patch` 这类构建期修补。

## 中断 / 定时器

| 项 | 值 | 来源 |
| --- | --- | --- |
| GIC | `arm,gic-v3` @ `0x14000000` | [A] |
| GICD | `0x14000000`，长 `0x20000` | [A] |
| GICR | `0x14040000`，长 `0x100000`，stride `0x20000`，`#redistributor-regions = <1>` | [A] |
| 架构定时器 | PPI 13/14/11/10（secure/ns/virt/hyp），`arm,no-tick-in-suspend` | [A] |
| 定时器频率 | **DTS 未写**，由固件 `CNTFRQ` 决定；先按 `26000000` 试 | — |

## 串口

**以固件里的真实设备树为准**（[F]，见下），UART 直接位于 `soc/ap-apb`，恒等映射到
物理地址，并非挂在某个 `0x71300000` 窗口里 —— 那个地址是 `ap_apb_regs` **syscon 寄存器块**，
不是串口。

| UART | 地址 | IRQ | status | 来源 |
| --- | --- | --- | --- | --- |
| uart0（ttyS0） | `0x70000000`，`reg = <0 0x70000000 0 0x100>` | `GIC_SPI 2` | okay | [F] |
| **uart1（ttyS1）** | **`0x70100000`**，`reg = <0 0x70100000 0 0x100>` | `GIC_SPI 3` | okay | [F] |
| uart2 | `0x70200000` | `GIC_SPI 4` | disabled | [F] |
| uart3 | `0x70300000` | `GIC_SPI 5` | disabled | [F] |

**console 已确认**：官方 `boot.img` 的 cmdline 是

```
console=ttyS1,115200n8 buildvariant=user
```

即 **ttyS1 = uart1 = `0x70100000`**，115200 8n1。`kernel/log` 走这个口。

寄存器布局（UNISOC 通用，与 T610 相同）：

```
TXD 0x00   RXD 0x04   STS1 0x0c   IEN 0x10   ICLR 0x14
STS1: TX_COUNT 0xff00 (空=0)      RX_COUNT 0x00ff (空!=0)
```

> 这个发现纠正了早先的错误：`[A]` 里 `ap_apb_regs = 0x71300000` 的注释容易让人以为
> UART0 在 `0x71300000`，实际 UART 是 `0x70000000` 起、且 console 是**第二个**串口。

## 内存

| 项 | 值 | 来源 |
| --- | --- | --- |
| DRAM 基址 | `0x80000000` | [D][F] `memory@80000000` |
| 物理容量 | `0x80000000`（2 GiB） | [D][F] |
| 可见 | `1863288 kB` ≈ 1.82 GiB | [E] `/proc/meminfo` |
| framebuffer 保留区 | **`0x9d000000`，长 `0x2000000`（32 MiB）** | [F] `fb_reserved` |

## 显示

| 项 | 值 | 来源 |
| --- | --- | --- |
| 分辨率 | `576 × 1440`，density `307` | [E] `wm size/density` |
| 面板候选 | `boe_nt35596_mipi_fhd`、`truly_nt35695_mipi_fhd`、`truly_nt35532_mipi_fhd`、`edo_rm67191_mipi_fhd`、`lcd_ili9881c_mipi_skyworth_hd`、`lcd_dummy_mipi_hd` | [F] `panel-name` |
| 选型方式 | 运行时按 LCD ID 选择（多面板共用一组 DTS） | [A][F] |
| DSI 控制器 | `0x63100000`，IRQ 0x30/0x31 | [F] |
| GPU | PowerVR Rogue GE8322，GLES 3.2 `1.10@5187610` | [E] `dumpsys SurfaceFlinger` |

> **候选分辨率对不上本机**：`ili9881c` 的 display-timings 是 `720 × 1520`，其余是 FHD（1080 宽），
> 而本机 `wm size` 报 `576 × 1440`。说明 1h10 是 UNISOC 参考板名，整机面板未必在这份通用 DT 的
> 候选列表里，需要从 `dmesg` 里读实际 panel-name 才能定。

## 电源 / PMIC

| 项 | 值 | 来源 |
| --- | --- | --- |
| PMIC | `sc2730`/`sc2731` 系列（`sc2730.dtsi`），EIC、TYPC、FGU、ADC | [A][B] |
| CPU 供电 | 簇0 = `fan53555`（`fcs,fan53555` @ i2c `0x60`）；簇1 = `vddcpu` | [A][B] |

## Android 侧

| 项 | 值 | 来源 |
| --- | --- | --- |
| 品牌/型号 | `SPRD` / `s9863a1h10_Natv` | [E] |
| 版本 | Android 9，SDK 28，`PPR1.180610.011/12117`，user + release-keys | [E] |
| 安全 | `ro.secure=1`、`ro.debuggable=0` → 无 `adb root` | [E] |
| Treble | `ro.treble.enabled=true`，VNDK 28 | [E] |
| bootloader | `ro.boot.verifiedbootstate=orange`、`flash.locked=0` → **已解锁** | [E] |
| 分区 | A-only：`uboot` `sml` `trustos` `boot` `recovery` `dtbo` `vbmeta` … | [E] `/dev/block/by-name` |
| fastboot | 设备枚举为 `fastboot Gadget`（VID `0x18D1` PID `0x0FFF`，接口 `ff/42/03`），**platform-tools 认不出** | [E] 实测 |

## 待验证清单

1. `console=` 到底指向哪个 UART —— 读 `/proc/cmdline`。
2. 架构定时器频率 —— 读 `CNTFRQ_EL0`（需要内核或裸机代码）。
3. `fastboot boot` 是否接受任意镜像 —— 需展锐定制 fastboot。
4. 本机实际面板型号与分辨率对应关系 —— 从 vendor blobs 或 `dmesg` 取。
5. debug UART 是否在物理上可达（有无测试点/焊盘）。