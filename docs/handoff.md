# 交接：当前状态与怎么继续

写给下一个接手的人（也可能是几周后的自己）。目标是让人**不读前面几十轮对话**也能接着干。

## 一句话现状

代码链路已完整构建并逐环验证到"能跳进 XNU"为止；**但一个字节都没在真机上跑过**，
因为手机卡在 USB 无响应状态，需要物理断电重启。

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

## 设备卡在哪

**当前状态**：`ioreg` 里设备在（`fastboot Gadget`，实例 id `0x10003dda3`），
`GET_DESCRIPTOR` 能返回 32 字节（很可能是 macOS 回放缓存），但
`GET_CONFIGURATION` / `GET_STATUS` / 所有 bulk 传输**全部超时**，`claim_interface` 返回 `-99`。

**怎么变成这样的**（两个都是我造成的，别重复）：

1. 第 1 轮用 platform-tools 发 `fastboot getvar`，命令挂住后我把它 kill 了——
   **中断在半路的 fastboot gagdet 会卡死**。
2. 第 3 轮我对卡死的 gadget 调了 `libusb_reset_device`，超时后设备从总线上消失。
3. 后续又试了 `set_configuration(0)`、反复 `clear_halt`，把状态弄得更糟。

**恢复办法**：长按电源键 10~15 秒。没有软件办法。

## 恢复后怎么继续

```sh
scripts/bringup.sh
```

它会：确认 Android 在线 → `adb reboot fastboot`（**注意不是 `bootloader`**，这是关键假设）
→ 只读探活 → 读 `max-download-size`。写 flash 必须额外加 `--flash <image>` 并手动确认。

如果 `adb reboot fastboot` 之后仍然不通，按顺序试：

1. **按键组合**：关机后按住 音量键 + 电源（不走 adb，U-Boot 走的分支不同）
2. **BROM**：`spd_dump` 已在本机编译通过（见 `docs/usb-fastboot-notes.md`），
   配合 `fdl1-sign.bin` 和从 PAC 提取的分区表，可绕开 U-Boot 直接读写闪存
3. **换机器**：Windows + 官方 ResearchDownload（整包 `.pac` 就是给它的）

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