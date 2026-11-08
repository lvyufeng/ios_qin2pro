# 刷机前的回滚准备

`fastboot boot` 不存在（见 `docs/bootloader-analysis.md`），想跑自建镜像只能**刷 `boot`
分区**。一旦刷进去的镜像起不来，设备就进不了 Android。所以在动手之前，先把回去的路铺好。

## 已经存好的东西

| 文件 | 大小 | sha256 |
| --- | --- | --- |
| `firmware/stock-boot.img.gz` | 6.7 MB | `944e87d2d117c842fc243fdec089e512bff20a6f4b683ad0f9313397a0cdbc64` |
| ↳ 解压后（原厂 `boot.img`） | 36 700 160 | `879580dc0784e33550473a56e7feea372cb6de1e94a0b390562d21485c0c6bb1` |

来源：官方固件 `Qin2pro_s9863a1h10_v1.1.0.pac` 的 `BOOT` 分区（`@0x03cec102`）。

校验：

```sh
gzip -dc firmware/stock-boot.img.gz | shasum -a 256
# 879580dc0784e33550473a56e7feea372cb6de1e94a0b390562d21485c0c6bb1
```

## 回滚途径一：fastboot（首选）

引导程序本身不受影响，所以只要 U-Boot 还在，fastboot 就还在。

```sh
# 长按电源 + 音量键进 fastboot，或从 Android: adb reboot bootloader
python3 scripts/fastboot-lite.py --libusb <libusb.dylib> getvar version
python3 scripts/fastboot-lite.py --libusb <libusb.dylib> \
    flash boot <(gzip -dc firmware/stock-boot.img.gz) --yes
```

> `<( ... )` 是进程替换，需要 shell 支持；稳妥起见先解压成文件再刷：
> `gzip -dc firmware/stock-boot.img.gz > /tmp/stock-boot.img`

## 回滚途径二：ResearchDownload / FDL（fastboot 也进不去时）

`UBOOTLoader` 分区和 `FDL2` 是**同一份二进制**，所以官方刷机工具对这台机器是完整的救援
通道。用 StudyComputer / ResearchDownload 加载官方 `.pac` 整包刷写即可。

前提是能进 **dloader 模式**（`cboot` 的六个模式之一），一般靠按键组合或断电重插触发。

## U-Boot 的启动模式

二进制里的 `cboot` 帮助文本：

```
mode: recovery, fastboot, dloader, charge, normal, vlx, caliberation.
cboot could enter a mode specified by the mode descriptor.
```

环境默认：`bootcmd=cboot normal`、`bootdelay=0`。

| 模式 | 用途 |
| --- | --- |
| `normal` | 正常启动 Android（默认） |
| `fastboot` | 我们用的刷机模式 |
| `dloader` | 下载模式（FDL2 / ResearchDownload） |
| `recovery` | recovery |
| `charge` | 关机充电 |
| `vlx` / `caliberation` | 产线校准 |

**没有"从内存启动镜像"这一项**，这是 `fastboot boot` 不可用的又一条佐证。

## 刷之前的检查清单

1. `fastboot getvar version` 有回应（链路活着）。
2. `gzip -dc firmware/stock-boot.img.gz | shasum -a 256` 与上表一致。
3. 记下当前 `boot` 分区的状态——如果设备还能起 Android，先 `adb shell` 确认
   `ro.boot.verifiedbootstate=orange`（解锁）仍在。
4. 刷完如果起不来：**别慌**，U-Boot 没被动过，重新进 fastboot 刷回原厂 `boot.img` 即可。
5. 刷写命令必须带 `--yes`，`scripts/fastboot-lite.py` 不带确认时**一个字节都不会发**。

## 还没验证的

- `fastboot flash boot` 在**这只具体的 bootloader** 上是否需要镜像带签名（`sprd_fb_secure_vboot_verify`
  的存在说明有校验路径，但 `SKIP VERIFY!!!` 说明解锁时跳过）。真机第一次刷写才能确认。
- 刷完后是自动重启还是需要 `fastboot continue`，未知。