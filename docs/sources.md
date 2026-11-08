# 源码与资源清单

全网翻过一遍后，能实际用上的东西按用途分类。每条都注明**它给我们什么**。

## 1. 直接针对 QIN 2 Pro 的

| 资源 | 内容 | 用途 |
| --- | --- | --- |
| [KevinXu07/android_kernel_qin_qin2pro](https://github.com/KevinXu07/android_kernel_qin_qin2pro) | 分支 `qin2pro-bringup`，~187 MB，源自 [MotorolaMobilityLLC/kernel-sprd](https://github.com/MotorolaMobilityLLC/kernel-sprd) | **最有价值**。含 `arch/arm64/boot/dts/sprd/sp9863a-1h10-overlay.dts`（本机真实设备树）、`sc9863a.dtsi`、PMIC/面板/触摸/GPU 全部节点 |
| [KevinXu07/android_device_qin_qin2pro](https://github.com/KevinXu07/android_device_qin_qin2pro) | TWRP/AOSP device tree（WIP） | 分区布局、fstab、外设型号 |
| [KevinXu07/android_vendor_qin_qin2pro](https://github.com/KevinXu07/android_vendor_qin_qin2pro) | vendor blobs（WIP） | 固件（GPU/触摸/WiFi firmware），XNU 驱动阶段要用 |
| [Kscope-Devices/android_device_duoqin_qin2pro](https://github.com/Kscope-Devices/android_device_duoqin_qin2pro) | 多亲 Qin2Pro device tree | 交叉核对分区与外设 |
| [twrpdtgen/android_device_zte_s9863a1h10](https://github.com/twrpdtgen/android_device_zte_s9863a1h10) | 同名板 `s9863a1h10`（ZTE 也在用这块参考板） | 佐证板号与 UNISOC 参考设计一致 |

## 2. Darwin/XNU 在非苹果硬件上

| 资源 | 内容 | 用途 |
| --- | --- | --- |
| [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet) ★1313 | iOS 16 userland + XNU on Redmi Note 10 Pro（SM7150），**CPU 软件渲染** | `t610yp` 的 `ios-sweet-000x` 补丁全部来自这里；**非 PAC 构建修复**也在这 |
| [UniXNU/UniXNU](https://github.com/UniXNU/UniXNU) ★2115 | UEFI 层 + 构建系统 + ADT 生成 + 设备清单（265 条） | 我们挂靠的上游框架 |
| [hxzbaka/UniXNU-t610yp](https://github.com/hxzbaka/UniXNU-t610yp) | **展锐 UMS512 bring-up 树** | 本仓库的模板：`board.toml` / `feeds.toml` / defconfig / ADT 脚本 / UART 补丁 |
| [hxzbaka/UniXNU-Unisoc](https://github.com/hxzbaka/UniXNU-Unisoc) | 展锐总入口，"no device-specific port yet" | 汇合点，做完可以 PR 回去 |
| [oosexgang/hakios-xnu-kernel](https://github.com/oosexgang/hakios-xnu-kernel) ★2600+ | XNU + 自研 loader + 驱动，iOS 16.5 用户态 | `docs/porting/`、驱动清单、boot-arg 目录 |
| [oosexgang/hakios-mt6768](https://github.com/oosexgang/hakios-mt6768) | **联发科 Helio G85 移植**（首个非高通） | 证明非高通可行；`mtkloader` 链式加载思路可借鉴 |
| [DBC1349/hackios-mi10ultra](https://github.com/DBC1349/hackios-mi10ultra) | iOS 16.5 用户态在小米 10 Ultra | 另一份带日志的 bring-up 记录 |

> `ios-sweet` 的补丁里 `ios-sweet-0005-non-pac-build-fixes.patch` 和
> `ios-sweet-0011-physmap-alignment.patch` 是必须的：**Cortex-A55 没有 PAuth**，
> 而 iOS 16 用户态是 arm64e。这条我们已经从 MT6768 先例确认可解。

## 3. 展锐平台与引导链

| 资源 | 内容 | 用途 |
| --- | --- | --- |
| [copslock/sprd_bsp_bootloader_u-boot15](https://github.com/copslock/sprd_bsp_bootloader_u-boot15) | 展锐 BSP u-boot15 | 自建引导链的基础（HackiOS 的 U-Boot 路线需要它） |
| [himeno-hamster/sprd-u-boot](https://github.com/himeno-hamster/sprd-u-boot) | 另一份 sprd u-boot | 参考 |
| [sprd-linux/Linux-4.4.83](https://github.com/sprd-linux/Linux-4.4.83) | sc9832e / sl8541e 内核 | 同代 SoC，UART/GIC/PMIC 驱动参考 |
| [shoulewoba/kernel-alcatel-5024d](https://github.com/shoulewoba/kernel-alcatel-5024d) | **SC9863A** 厂商内核（Alcatel 1S 2019） | 与我们同 SoC，可对拍 DTS |
| [Hadenix/android_kernel_zte_sc9863a](https://github.com/Hadenix/android_kernel_zte_sc9863a) | ZTE SC9863A 内核 | 同上 |
| [torvalds/linux `arch/arm64/boot/dts/sprd/`](https://github.com/torvalds/linux/blob/master/arch/arm64/boot/dts/sprd/sp9863a-1h10.dts) | 主线有 `sp9863a-1h10.dts`，但**仅 39 行、只启 UART console** | 有限的交叉验证；证明上游还谈不上显示 |

## 4. 取资料/刷机工具

| 工具 | 用途 |
| --- | --- |
| [CE1CECL/spd_dump](https://github.com/CE1CECL/spd_dump) | 走展锐 **BROM** 直读 flash，**不需要 root**，可整机 dump（拿真实 DTB / boot.img 的最干净路径） |
| [TomKing062/action_spd_dump_it](https://github.com/TomKing062/action_spd_dump_it) | spd_dump 的可用构建 |
| [ABDO10DZ/SpdClient](https://github.com/ABDO10DZ/SpdClient) | SPD BootROM 访问层 |
| ResearchDownload (R23.19.3301) | 展锐官方刷机工具，解锁流程要用 |
| [解锁 + phh GSI 教程](https://listed.to/p/GYrDytL51l) | 本机解锁/刷机全流程；**明确：只能刷 `system`，`boot`/`recovery` 必须签名**；随包附定制 fastboot |

## 5. 已确认没有的

- **PowerVR 的 Darwin 驱动**：HackiOS / UniXNU / ios-sweet 三者都只做 Adreno 或 Mali，GE8322 无任何先例。
  Linux 侧有 `drivers/gpu/drm/imagination` 与 Mesa 的 PowerVR Vulkan，但是另一条技术栈。
- **SC9863A 的 UEFI（Mu-Silicium / EDK2）移植**：不存在，展锐是 U-Boot 体系。
- **QIN 2 Pro 的 `fastboot boot` 先例**：没有公开记录，需要自测。