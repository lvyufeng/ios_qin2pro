# 从固件里查证：这个 bootloader 支持什么

`fastboot boot`（把镜像下载到内存直接执行，不写闪存）是验证 payload 最安全的方式。
但目标里这一条**必须先用事实回答**，不能假设。答案可以从官方固件里直接读出来。

## 提取

PAC 里的 `UBOOTLoader` 分区就是引导程序：

```
$ python3 scripts/extract-pac.py list Qin2pro_s9863a1h10_v1.1.0.pac | grep -i uboot
UBOOTLoader        u-boot.bin                    size=508660     @0xbdf0236e
```

508 660 字节，开头是 `DHTB`（展锐的签名镜像头）。

**它和 `FDL2` 分区逐字节相同**：

```
$ cmp u-boot.bin fdl2.bin && echo 相同
相同
```

也就是说：ResearchDownload 刷机工具用的 FDL2 下载器，和开机时的引导程序是**同一份
U-Boot**。设备在两种模式下跑的是同样的代码，只是入口不同。

## fastboot 命令表

U-Boot 的 fastboot 命令在源码里是一张 `{ "名字", 处理函数 }` 的表，字符串连续排布。
在二进制 `0x65bb7` 处找到这块字符串池：

```
[ 0] getvar:
[ 1] download:
[ 2] flash:
[ 3] erase:
[ 4] powerdown
[ 5] continue
[ 6] reboot-bootloader
[ 7] flashing
[ 8] oem
[ 9] getlcs
[10] setrma
[11] getsocid
[12] unknown reason
[13] unknown command
```

**没有 `boot`**。

对照上游 U-Boot `drivers/fastboot/fb_command.c`，这张表就是它的调度表（末尾
`getlcs/setrma/getsocid` 是展锐自己加的）。上游表里 `boot` 是一项独立条目：

```c
	[FASTBOOT_COMMAND_ERASE] =  {
		.command = "erase",
		.dispatch = CONFIG_IS_ENABLED(FASTBOOT_FLASH, (erase), (NULL))
	},
	[FASTBOOT_COMMAND_BOOT] =  {
		.command = "boot",
		.dispatch = okay
	},
	[FASTBOOT_COMMAND_CONTINUE] =  {
		.command = "continue",
		.dispatch = okay
	},
```

而我们的二进制在 `erase:` 之后**直接是 `powerdown`**——`boot` 那一项不存在。
同理 `set_active` 也不在（`grep -c "set_active:"` 为 0），说明这个裁剪版只保留了
刷机必需的命令。

> 结论：`fastboot boot` **在这台设备上不存在**。发过去只会收到 `unknown command`。

## 支持的操作

| 命令 | 说明 |
| --- | --- |
| `getvar:` | 读变量 |
| `download:` | 把数据下载到设备内存缓冲（`fastbootbuffer@82000000`） |
| `flash:` | **把下载的数据写进分区** ← 唯一能部署自建镜像的途径 |
| `erase:` | 擦分区 |
| `flashing` | 展锐的解锁/上锁 |
| `oem` | 厂商命令 |
| `continue` / `reboot-bootloader` / `powerdown` | 控制流 |
| `getlcs` / `setrma` / `getsocid` | 展锐私有（生命周期状态、RMA、SoC ID） |

## 校验：解锁状态下会跳过

二进制里能直接读到这几句：

```
WARNNING: LOCK FLAG IS : UNLOCK, SKIP VERIFY!!!
   INFO: LOCK FLAG IS : UNLOCK!!!
Flashing Lock Flag is locked. Please unlock it first!
```

配合本机 `ro.boot.verifiedbootstate=orange`、`flash.locked=0`，可以确认：**引导程序已解锁，
刷入的镜像不会做签名校验**。

## 官方内核是什么格式

把 `boot.img` 的内核段挖出来看（偏移 `page`=`0x800`，长度 `kernel_size`）：

```
kernel_size = 17357464   kernel_addr = 0x8000   page = 2048
前 4 字节: 10 00 00 14          →  b +0x40
0x08:      text_offset = 0x80000
0x18:      flags = 0x0a
0x38:      "ARM\x64"            →  arm64 Image 魔数
```

**是标准的 arm64 Linux Image，没有套 `DHTB`**。这有两层含义：

1. bootloader 解析 `boot.img` 时不需要内核是签名的，普通 Image 就吃；
2. 我们的 payload 头（`code0 = b 0x40`、`magic@0x38 = ARM\x64`）与它**前 64 字节结构一致**，
   只是 `text_offset` 不同（我们填 0，官方是 `0x80000`）。

## 启动模式：cboot

二进制里的帮助文本：

```
bootcmd=cboot normal
mode: recovery, fastboot, dloader, charge, normal, vlx, caliberation.
cboot could enter a mode specified by the mode descriptor.
```

同一份 U-Boot 有两个角色（`two roles: dloader and cboot`）——这正是它既是 FDL2 又是
引导程序的原因。

**没有"从内存启动镜像"这个模式**，这是 `fastboot boot` 不可用的第二重佐证。

`oem` 的子命令只有 `getlcs` / `setrma` / `getsocid`（都在命令表字符串池里，紧跟在
`oem` 之后），没有 `oem boot` 之类的后门。

## 对方案的影响

原计划「`fastboot boot` 把 payload 放进内存跑，不碰闪存」**不成立**。剩下的路只有：

| 途径 | 可行性 | 风险 |
| --- | --- | --- |
| `fastboot flash boot <img>` | ✅ 命令存在，且解锁不校验 | 写闪存；失败会进不了系统 |
| ResearchDownload / FDL 刷 `.pac` | ✅ 官方工具，FDL2 就是那份 U-Boot | 需要 Windows 或 spd_dump |
| `fastboot boot` | ❌ 不存在 | — |

### 安全网

- 官方 `.pac`（3.0 GB）完整在手，`BOOT` 分区的原厂 `boot.img` 也已经导出
  （`/tmp/qinfw/boot.img`）。**任何时候都能刷回去**。
- 解锁状态下不做签名校验，所以刷错了不会变砖，最多是开不了机、需要重刷。

### 仍然未知

- `boot.img` 里的 `text_offset = 0x80000` 是否被 U-Boot 的 `booti` 用来决定放置位置。
  我们的 payload 填 0，理论上符合"新式内核"的约定，但没在设备上验证过。
- `max-download-size` 具体多大——嵌了内核的 loader（约 15.9 MB）能不能一次下载完，未知。
- `fastboot flash boot` 之后是否需要 `fastboot continue` 才启动，还是自动重启。

## 复现

```sh
python3 scripts/extract-pac.py list    /tmp/Qin2pro_s9863a1h10_v1.1.0.pac
python3 scripts/extract-pac.py extract /tmp/Qin2pro_s9863a1h10_v1.1.0.pac UBOOTLoader /tmp/u-boot.bin
strings -a /tmp/u-boot.bin | grep -n "getvar:"
python3 -c "d=open('/tmp/u-boot.bin','rb').read(); i=d.find(b'getvar:'); print([s for s in d[i:i+260].split(b'\0') if s])"
```