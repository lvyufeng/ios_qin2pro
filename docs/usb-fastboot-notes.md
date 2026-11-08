# USB / fastboot 观察记录

结论先说：**设备的 fastboot 接口是标准的，是主机侧的工具打不开它；而且这个 gadget
一旦被中断在半路，就会卡死到只能断电。**

## 官方 `fastboot` 为什么看不见它

`adb devices` 空、`fastboot devices` 空。依次试过：

| 工具 | 结果 |
| --- | --- |
| platform-tools 37.0.1 `fastboot` | 无设备，`getvar` 直接挂住 |
| platform-tools 34.0.5 `fastboot` | 无设备 |
| platform-tools 33.0.3 `fastboot` | 无设备 |
| platform-tools 29.0.5 `fastboot` | x86_64 二进制，本机跑不了（`Bad CPU type`） |

`ioreg` 里设备是正常枚举的：

```
+-o fastboot Gadget@03100000  <class IOUSBHostDevice>
     "idVendor" = 6353 (0x18D1)   "idProduct" = 4095 (0x0FFF)
     "bDeviceClass" = 255
```

**用 libusb 直接读原始配置描述符**，看到的是标准 fastboot 接口：

```
interface 0: class 0xff, subclass 0x42, protocol 0x03   ← 正是 fastboot
endpoints:   0x06 OUT bulk 512  /  0x85 IN bulk 512     ← 端点地址不常见
```

所以不是"描述符不合规"。平台工具在此设备上为何失败仍未定论（可能与其对
非标准端点/序列号的假设有关）。

## 自己写客户端

`scripts/fastboot-lite.py` 用 ctypes 调 libusb，实现 `list` / `getvar` / `boot`。
本机没有 Homebrew，libusb 是用它自带的 Xcode 工程编的：

```sh
git clone --depth 1 -b v1.0.27 https://github.com/libusb/libusb
xcodebuild -project libusb/Xcode/libusb.xcodeproj -target libusb \
    -configuration Release build CODE_SIGNING_ALLOWED=NO \
    CONFIGURATION_BUILD_DIR=$PWD/libusb-out
python3 scripts/fastboot-lite.py --libusb libusb-out/libusb-1.0.0.dylib list
```

已验证到这一步：能枚举、能读描述符、能 `claim_interface(0)`。之后的 bulk 写开始超时。

## 卡死与（被我搞砸的）复位

第一次（第 1 轮）用平台工具 `fastboot getvar product` 时命令挂住，我把它 kill 了。
**中断在半路是致命的**：设备端的 fastboot gadget 会停在等待状态，端点的 data toggle
也对不上。之后再用 libusb 写就一直是：

```
write-failed(Entity not found)      ← 端点 0x01 不存在，我最初的猜测
write-failed(Operation timed out)   ← 找到 0x06 之后
clear_halt 0x06: Other error
```

我随后调了 `libusb_reset_device`，**它超时并把设备从总线上打没了**（`ioreg` 里
`fastboot Gadget` 归零，libusb 枚举从 8 个变 7 个）。这是个教训：**对已经卡住的
gadget 不要发 USB reset**。

## 恢复办法

物理长按电源键 10~15 秒强制关机重启。回到 Android 后 `adb devices` 应能看到
`Qin2ProSM1912020243`。

## 下次的正确做法

1. 进 fastboot 后**只**用 `scripts/fastboot-lite.py`，不要再用平台工具试探。
2. 每条命令都要有读超时，且在超时后**不要再发下一条**——先把设备断电重启。
3. 验证 `fastboot boot` 之前，先跑 `getvar:version` 确认链路是活的。
4. `fastboot-lite.py boot` 只做 `download` + `boot`，不写任何分区。

## 第二轮尝试：设备回来了，但 fastboot 仍然不通

用户重启后设备正常回到 Android（`adb` 认到 `Qin2ProSM1912020243`，
`verifiedbootstate=orange`、`flash.locked=0`）。`adb reboot bootloader` 之后设备重新枚举，
描述符完全正常：

```
bcdUSB 0x0200  class 0xff  speed=high
interface 0: ff/42/03          ← 标准 fastboot
endpoints:   0x06 OUT bulk 512 / 0x85 IN bulk 512
```

但**控制传输正常、批量传输全部失败**：

| 操作 | 结果 |
| --- | --- |
| 读配置描述符（EP0） | ✅ 32 字节正确 |
| 写 `getvar:version`（`0x06`） | `-7` 超时 |
| 读（`0x85`） | `-8` stall / `-9` overflow |
| 零长度写 | `-7` 超时 |
| 512 字节整包写 | `-7` 超时 |
| `clear_halt` / `set_interface_alt_setting` | `-99`（后端不支持） |
| 反复尝试后 `claim_interface` | 也变成 `-99` |

**官方 platform-tools `fastboot devices` 同样为空**，所以不是自写客户端的问题。

### 最可能的原因：设备停在某个菜单/提示上

U-Boot 的字符串里有：

```
mode: recovery, fastboot, dloader, charge, normal, vlx, caliberation.
```

如果 `adb reboot bootloader` 没有指明模式，或者设备在等按键确认，**USB gadget 会被枚举
出来但不进入 fastboot 服务循环**——现象正是"能读描述符、不能做批量传输"。

### 下一步要确认的

1. 手机屏幕在 fastboot 状态下显示什么？有没有菜单或提示？
2. 拔插 USB 线能否让它重新枚举并武装端点？
3. 关机后按 **音量键 + 电源** 直接进 fastboot，是否与 `adb reboot bootloader` 不同？

## 备用通道：BROM / spd_dump

如果 fastboot 在 macOS 上就是不通，可以绕开 U-Boot，直接和 SoC 的 BootROM 说话。

`CE1CECL/spd_dump` 已在本机编译通过（用它自带的 libusb）：

```sh
git clone --depth 1 https://github.com/CE1CECL/spd_dump
mkdir -p /tmp/libusb-inc/libusb-1.0
ln -sf /tmp/libusb/libusb/libusb.h /tmp/libusb-inc/libusb-1.0/libusb.h
cd spd_dump && make GITVER.h
clang -O2 -DUSE_LIBUSB=1 -I/tmp/libusb-inc -o spd_dump spd_dump.c common.c \
    /tmp/libusb/out/libusb-1.0.0.dylib -Wl,-rpath,/tmp/libusb/out -lm
```

BROM 模式在 SoC 内部，不受 U-Boot 状态影响，但它需要**物理按键组合**进入，而且写分区
要按展锐的 DLOAD 协议走（FDL1 → FDL2）。这条路尚未在设备上验证过。

## 第三轮：fastboot 本身通了，但 flash 被内容校验挡住

修好客户端的三个 bug 之后（零长度读、长度前缀、写超时），通信完全正常：

```
getvar version           -> OKAY 1.0
download:0010            -> DATA 00000010 -> OKAY
36 MB 下载               -> 2.8 秒，零错误
flash boot（原厂镜像）   -> OKAY，重启后系统正常
```

但**只要镜像内容被改过，`flash:boot` 就不回话**，设备的 USB 重新枚举后失联，
必须断电重启。手机随后仍能正常启动，说明 boot 分区**没有被写**。

| 镜像 | 结果 |
| --- | --- |
| 原厂 `boot.img`（未改动） | `OKAY` |
| `hello-uart`（4 KB） | 无响应 |
| `hello-uart` 补零到 36,700,160（同尺寸） | 无响应 |
| 原厂镜像 + 命令行改一处 | **无响应** |
| 原厂镜像 + 填充区改一个字节 | 未测完（设备已卡） |

### 原因：原厂镜像带 AVB 签名

原厂 `boot.img` 末尾 64 字节是 AVB footer：

```
magic                AVBf  @ 36,700,096
original_image_size  17,360,896
vbmeta_offset        17,362,944
vbmeta_size          2,112
```

我们生成的镜像没有这个结构。bootloader 在 `flash` 时会解析并校验它，
校验不过就不写、并让 USB 挂死。改一个命令行字节就足以触发。

**这意味着 fastboot 这条路对自制镜像是关的**——没有 OEM 私钥就没法生成合法签名。

### 仍然可行的路

1. **官方刷机工具（ResearchDownload / SPD Flash Tool）** 走 BROM/FDL2，
   完全绕开 U-Boot 的校验。PAC 里 `boot.img` 位于偏移 `0x03cec102`，
   长度 36,700,160，正好等于我们补齐后的镜像长度，可以**原位替换**。
2. 找找 bootloader 有没有关掉校验的 OEM 命令（fastboot 命令表里有 `oem`）。
