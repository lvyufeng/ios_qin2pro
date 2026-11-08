# 移植执行计划

目标：**XNU 在 SC9863A 上进入内核并输出日志**。之后每一步都独立可验证。

方法沿袭 HackiOS 的 [`docs/porting/`](https://github.com/oosexgang/hakios-xnu-kernel/blob/main/docs/porting/index.md)：
一次只开一个子系统，每个阶段写清"怎么算完成"。

## 阶段 0：前提（阻塞项）

| # | 检查 | 怎么验 | 状态 |
| --- | --- | --- | --- |
| 0.1 | `fastboot boot` 是否可用 | 从固件反查 U-Boot 命令表 | ❌ **不存在**。命令只有 `getvar/download/flash/erase/powerdown/continue/reboot-bootloader/flashing/oem/getlcs/setrma/getsocid`。见 `docs/bootloader-analysis.md` |
| 0.1b | 那怎么部署自建镜像 | — | ✅ `fastboot flash boot`，且解锁状态跳过校验（`SKIP VERIFY!!!`） |
| 0.2 | `fastboot boot` 是否接受任意镜像 | `fastboot boot <任意u-boot.bin>`，看是 `OKAY` 还是签名拒绝 | ⬜ 待验（依赖 0.1） |
| 0.3 | console 在哪个 UART | 官方 `boot.img` cmdline | ✅ **`console=ttyS1,115200n8` → uart1 = `0x70100000`** |
| 0.4 | 架构定时器频率 | 读 `CNTFRQ_EL0`；或先用 `26000000` 试跑 | ⬜ 待验 |
| 0.5 | debug UART 物理可达性 | 拆机找测试点，或全程走 framebuffer | ⬜ 待验（framebuffer 已定位 `0x9d000000`） |

> **0.1 是所有后续工作的闸门**。若 `fastboot boot` 被签名策略拒绝，整条
> "RAM 运行、不刷机" 的路线失效，只能回到刷 `boot`（且 `boot` 需签名）。

## 阶段 1：内核入口（当前里程碑）

目标：loader 起来 → XNU 起来 → 打出第一行日志。

需要的件：

1. `feeds.toml` 锁定的 xnu-8796.141.3 源码
2. `patches/common/*` 四个补丁（clang/PAC/physmap 相关，来自 ios-sweet）
3. `patches/common/amcc-machine-layer.patch`（去掉 Apple AMCC）
4. `patches/sc9863a/sc9863a-uart.patch`（polled UART @ `0x71300000`）
5. `scripts/make-s9863a1h10-adt.py` 产出的 ADT
6. UniXNU 的 loader

**完成判据**：日志里出现 loader banner → `kernel.ver=` → 内核前几行 → 自报
`hakios-kernel: N drivers built in`。

风险：UART 不通 → 全瞎。**对策**：`board.toml` 里 `panic_screen = true`，
framebuffer 在 `0x9e000000`，这条通道不依赖任何手机外设。

## 阶段 2：CPU / 中断 / 定时器

- GICv3：GICD `0x14000000`，GICR `0x14040000`，stride `0x20000`（固件 DTB 已确认）
- 架构定时器 PPI 13/14/11/10
- PSCI 0.2 via SMC，8 × A55（MPIDR `0x0…0x700`），两簇

**完成判据**：能打印各核启动、`mach_absolute_time` 走得对。

## 阶段 3：存储与输入

- eMMC：`sdio3 @ 0x20600000`（`sprd,sdhci-r11`）只读
- 触摸：型号待从 vendor blobs 确认（1h10 overlay 里未直说）
- 按键：`pmic_eic`

## 阶段 4：显示

- 面板：候选 NT35695 / NT35532 / NT35596（都是 FHD），而本机是 576×1440，
  说明运行时按 LCD ID 选型 —— **必须先 dump 出本机实际 panel dtsi**
- 输出走 framebuffer `0x9e000000`

## 阶段 5：GPU（预计长期为空）

PowerVR Rogue GE8322。三个活跃项目分别只做 Adreno（HackiOS/UniXNU）和
Mali（MT6768 移植走 panfrost）。**没有任何 Darwin 侧 PowerVR 驱动**。

短期对策：像 ios-sweet 那样全靠 CPU 画（它在 SM7150 上是 20–30 fps；
本机是 A55 @1.6GHz + 2GB，会更慢）。

## 立即可做的三件事

1. **拿到展锐定制 fastboot**（解锁包里的那份），验证 0.1 / 0.2。
2. **拿本机真实 DTB**：优先用 [spd_dump](https://github.com/CE1CECL/spd_dump) 走 BROM
   整机 dump（不需要 root），或从官方 `.pac` 里解 `boot.img`。
3. **对拍 T610 与本机**：跑 `scripts/make-s9863a1h10-adt.py` 与
   `make-t610yp-adt.py`，逐字节比 ADT，确认只有预期的偏移不同。