# 交接：当前状态与怎么继续

写给下一个接手的人（也可能是几周后的自己）。目标是让人**不读前面几十轮对话**也能接着干。

## 一句话现状

**USB 与刷写链路已经打通**（见下），`hello-uart` 探针**已经写进 boot 分区**；
刷完之后设备失联，尚未确认是我们的代码跑起来了、还是 bootloader 卡住了——
**需要有人看一眼屏幕**。XNU 本身仍未在硬件上运行过。

## 已经验证的事实（可复核，不是推测）

| 事实 | 证据 | 文档 |
| --- | --- | --- |
| console 是 **UART1 = `0x70100000`**（`console=ttyS1`） | 官方 `boot.img` 头部 cmdline | `docs/firmware-extraction.md` |
| GICD `0x14000000` / GICR `0x14040000`，stride `0x20000` | 固件 DTB | `docs/hardware-s9863a1h10.md` |
| 定时器 **26 MHz** | 固件 DTB `/timer/clock-frequency = <0x18cba80>` | 同上 |
| framebuffer 保留区 `0x9d000000`，32 MB | 固件 DTB `fb_reserved` | 同上 |
| bootloader 是 **U-Boot 2015.07 (sharkl3)**，`UBOOTLoader` 与 `FDL2` **逐字节相同** | PAC 提取 | `docs/bootloader-analysis.md` |
| **`fastboot boot` 不存在** | fastboot 命令表字符串池里 `erase:` 后直接是 `powerdown` | 同上 |
| 解锁状态**跳过校验** | 二进制里的 `SKIP VERIFY!!!`，加 `verifiedbootstate=orange` | 同上 |
| `boot_args` 布局（`CommandLine` 在 `0x6c`，`sizeof = 0x480`） | 编译器 `offsetof` + `DarwinBoot` 的 `STATIC_ASSERT` | `docs/xnu-handoff.md` |
| 入口 `VA = 链接基址 + 0x3f8488`，符号是 `__start` | Mach-O `LC_UNIXTHREAD` + 符号表 | 同上 |
| 内核按 `runtime + (virtBase - physBase)` 还原链接地址 | 内核入口 `_start_first_cpu` 的反汇编 | 同上 |
| 内核文件**不是**按虚拟布局排列的 | `__DATA` 的 BSS 让后三段文件偏移前移 `0xd8000` | 同上 |

## 三个可上机的镜像

按风险从小到大，**必须按顺序试**，不要跳。

| 镜像 | 大小 | 验证什么 | 预期现象 |
| --- | --- | --- | --- |
| `build/payload/hello-uart-boot.img` | 4 KB | 能否跑自有代码 + 两个输出信道 | 串口出两行字，**或**屏幕顶部约 21% 变白 |
| `build/payload/loader-v0-boot.img` | 16 KB | `boot_args` 构造是否正确 | 串口打出 7 行十六进制（`adt`/`len`/`args`/`rev`/`virt`/`dt(va)`） |
| `build/payload/loader-v1-boot.img` | 16.6 MB | 搬运内核 + 跳进 XNU | 串口先出四行进度，然后**不知道**——这是未验证区 |

**一个现象就能定方向**：`hello-uart` 让屏幕顶部变白 = 整条链路通了。

## 数组的判读

| 现象 | 结论 | 下一步 |
| --- | --- | --- |
| 串口出字 + 屏幕变白 | 全通 | 跑 loader-v0 |
| 只有屏幕变白 | 镜像跑了，UART 不通 | 日志改走 framebuffer；或试 uart0 `0x70000000` |
| 只有串口出字 | 镜像跑了，framebuffer 地址/格式不对 | 记录，继续用串口 |
| 两者都无，但 `flash` 返回成功 | 镜像没被执行，或两信道都不可用 | 看 fastboot 返回值与设备是否卡死 |
| `FAILED (remote: ...)` | bootloader 拒绝 | 记错误码 |

## 通信是怎么打通的（重要，别再走弯路）

有很长一段时间我以为是设备不支持 macOS 的 fastboot。**其实是自写客户端有三个 bug**：

| Bug | 现象 | 修法 |
| --- | --- | --- |
| 读操作传了**长度 0** | macOS 对零长度 bulk IN 直接返回 stall，看起来像"设备卡死" | 新增 `Usb.read()`，显式给出缓冲区大小 |
| 响应解析要求 `OKAY` + 4 位十六进制长度 | 老版 U-Boot **不发长度前缀**，`getvar:version` 只回 7 字节 `OKAY1.0`，被判为空响应 | 只在长度字段自洽时才剥离 |
| 写超时当成致命错误 | macOS 上写经常返回 `-7` 但**数据已送达**，命令被发出后又被忽略 | 写当作尽力而为，以响应为准 |

修完后的实测（2026-11-08）：

```
getvar version          -> OKAY 1.0
download:0010           -> DATA 00000010   -> OKAY
36 MB 下载              -> 2.8 秒，零错误
flash boot（原厂镜像）  -> OKAY
reboot                  -> Android 正常起来
flash boot（hello-uart）-> 写完设备失联
```

**所以 `fastboot flash` 是可用的**，回滚也验证过（原厂镜像写回去后系统正常）。

## 当前卡在哪

刷 `hello-uart` 之后，设备的 `fastboot Gadget` 重新枚举（实例 id 变成 `0x100047467`），
然后不再响应任何命令，`adb` 也没有。

两种可能，**只能靠看屏幕判断**：

- **A：我们的代码跑了**（活的里程碑）。屏幕顶部约 21%（576×300 像素）应是**纯白色**
- **B：还在 bootloader 里卡着**。屏幕是 fastboot 界面或开机 logo，没有白条

长按电源键 10~15 秒后：
- 能进 Android → 刷写没落盘（仍是原厂 boot）
- 进不去 → **我们的代码在跑**，需要用按键组合进 fastboot 刷回原厂

回滚一条命令（已支持 `.gz`）：

```sh
python3 scripts/fastboot-lite.py --libusb /tmp/libusb/out/libusb-1.0.0.dylib \
    flash boot firmware/stock-boot.img.gz --yes
```

## 早期把设备弄坏的操作（别重复）

1. 第 1 轮用 platform-tools 发 `fastboot getvar`，命令挂住后 kill 了它——
   **中断在半路的 fastboot gadget 会卡死**。
2. 第 3 轮对卡死的 gadget 调 `libusb_reset_device`，超时后设备从总线上消失。
3. 后续又试 `set_configuration(0)`、反复 `clear_halt`，越弄越糟。

**恢复办法**：长按电源键 10~15 秒。没有软件办法。

## 恢复后怎么继续

```sh
# 1. 进 fastboot（Android 在线时）
adb reboot bootloader

# 2. 探活
python3 scripts/fastboot-lite.py --libusb /tmp/libusb/out/libusb-1.0.0.dylib getvar version

# 3. 按风险从低到高刷
python3 scripts/fastboot-lite.py --libusb /tmp/libusb/out/libusb-1.0.0.dylib \
    flash boot build/payload/hello-uart-boot.img --yes
```

**注意**：`adb reboot fastboot`（用 `fastboot` 而不是 `bootloader`）在这台机器上
**只是普通重启回 Android**，不会进 fastboot。必须用 `bootloader`。

如果刷完之后连 fastboot 都进不去：关机后按住 音量键 + 电源。
还不行就走 BROM（`spd_dump` 已在本机编译通过，见 `docs/usb-fastboot-notes.md`），
或换 Windows 用官方 ResearchDownload。

## 刷写前必须做的一件事

```sh
gzip -dc firmware/stock-boot.img.gz | shasum -a 256
# 必须是 879580dc0784e33550473a56e7feea372cb6de1e94a0b390562d21485c0c6bb1
```

这是唯一的回滚凭据。`scripts/fastboot-lite.py flash` 不带 `--yes` 时**一个字节都不发**。

## 还没解决的技术未知

1. **bootloader 跳到我们时的 MMU/缓存状态**。loader-v1 假设 MMU 已关（U-Boot 的
   `cleanup_before_linux()` 通常会关），但没实测。
2. **虚拟定时器能否出中断**。XNU 用 `CNTV_CTL_EL0`，要 EL2 设好 `CNTVOFF` 并使能。
3. **`0x9f000000` 以上是否真的空闲**。按固件保留区推断，ATF 是否另有占用未知。
4. **XNU 能否在非 Apple 平台走到出日志**——一步都没验证过。
5. **112 MB 内存窗口够不够**。UniXNU 的设备普遍用 2~4 GiB，备选配置见
   `docs/xnu-handoff.md`（`0x9f000000` 起 1.5 GiB）。

## 仓库里最该先读的三份文档

1. `docs/xnu-handoff.md` —— 内核要什么（`boot_args`、ADT 契约、入口约定）
2. `docs/bootloader-analysis.md` —— bootloader 能做什么（以及 `fastboot boot` 为什么不行）
3. `docs/payload-bringup.md` —— 三个镜像分别验证什么、结果怎么读