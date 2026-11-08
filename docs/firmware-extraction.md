# 从官方固件提取设备树与启动参数

固件里的一手信息纠正了三处猜测（console 串口、UART 基址、framebuffer 地址）。
这里记录完整的可复现步骤。

## 1. 取固件

官方包是社区维护的下载：[Qin2Pro_s9863a1h10_v1.1.0.zip](https://drive.google.com/file/d/1U0WRqSJy9aEXJCCGPeuU652lkPDoLSLn)
（引用自[解锁教程](https://listed.to/p/GYrDytL51l)）。1.5 GB，解压得到单个
`Qin2pro_s9863a1h10_v1.1.0.pac`（3.19 GB）。

Google Drive 对大文件会先返回"病毒扫描警告"页，需要带上表单里的
`id / export / confirm / uuid` 再请求一次 `drive.usercontent.google.com/download`。

> 这个包是判过签的官方固件，含完整 `system`/`vendor`，**不要**直接用于刷机实验。

## 2. PAC 格式

字符串是 UTF-16LE。布局：

```
0x000  全局头
         0x000  "BP_R1.0.0"（UTF-16LE）
         0x030  uint32 校验
         0x034  产品名 productName[256]  (UTF-16)
         0x234  固件名 firmwareName[256] (UTF-16)
         0x434  uint32 partitionCount        = 35
         0x438  uint32 partitionsListStart   = 0x84c
0x84c  分区条目数组（变长，每条长度写在开头）
```

每条分区条目：

```
0x000  uint32  本条目长度（实际观测恒为 2580 = 0xa14）
0x004  char16  partitionName[256]
0x204  char16  fileName[512]
0x604  uint32  partitionSize
0x60c  int32[2]
0x614  uint32  partitionAddrInPac   ← 数据在 .pac 里的偏移
```

> 注意：`0x604`/`0x614` 是按实测对齐出来的，与部分公开的 C 结构体定义有 4 字节出入；
> 以本机固件实测为准（首个条目 `FDL` / `fdl1-sign.bin` / 51 572 字节 @ `0x16908` 可自校验）。

## 3. 关键分区

| 分区 | 文件 | 大小 | .pac 内偏移 |
| --- | --- | --- | --- |
| BOOT | `boot.img` | 36 700 160 | `0x03cec102` |
| DTB | `dtb.img` | 8 388 608 | `0x05fec102` |
| DTBO | `dtbo.img` | 8 388 608 | `0x067ec102` |
| VBMETA | `vbmeta.img` | 1 048 576 | `0x0017fa6c` |
| SPL | `u-boot-spl-16k.bin` | 53 652 | `0x001728d8` |
| UBOOT | `u-boot.bin` | 508 660 | `0xbdf0236e` |
| Trustos | `tos.bin` | 1 238 916 | `0xaf84a836` |
| SML | `sml.bin` | 57 092 | `0xaf978fba` |

## 4. boot.img

标准 Android boot image，`page_size = 2048`、`header_version = 1`：

```
magic      ANDROID!
kernel     17 357 464 字节 @ 0x8000
ramdisk    0
console    console=ttyS1,115200n8 buildvariant=user
```

## 5. dtb.img

不是裸 FDT，前面有 0x800 字节展锐容器头：

```
0x000  "SPRD" | 01 00 00 00 | ...
0x800  d0 0d fe ed ...        ← FDT totalsize = 150 305
```

用 dtc 反编译：

```sh
dd if=dtb.img of=device.dtb bs=1 skip=2048 count=150305   # 或 python 切片
dtc -I dtb -O dts device.dtb > device.dts                 # 6318 行，126 条 warning
```

## 6. 由此确认的事实

| 项 | 值 | 说明 |
| --- | --- | --- |
| model | `Spreadtrum SC9863A Board` / `sprd,sp9863a`,`sprd,sc9863a` | |
| console | **`ttyS1`，115200 8n1** | 来自 boot.img cmdline |
| uart0 / **uart1** | `0x70000000` / **`0x70100000`** | ttyS1 → uart1 |
| uart2 / uart3 | `0x70200000` / `0x70300000` | 都是 `disabled` |
| UART IRQ | SPI 2 / 3 / 4 / 5 | 与地址一一对应 |
| GIC | `0x14000000` + `0x14040000`，stride `0x20000` | 与我们先前一致 ✅ |
| memory | `0x80000000` + `0x80000000`（2 GiB） | 一致 ✅ |
| framebuffer 保留区 | **`0x9d000000`，32 MiB** | 早先误以为是 `0x9e000000`/`0x7e9000` |

早先的错误来源：厂商 DTSI 里 `ap_apb_regs` 的注释写着 `0x71300000`，我把它当成了串口窗口的
基址。实际上那是 **syscon 寄存器**，UART 在 `0x70000000` 起，而 console 还是**第二个**口。

## 7. 复现脚本

`scripts/extract-pac.py` 做了上面第 2 步的解析：列出全部分区并可按键名导出。

```sh
python3 scripts/extract-pac.py list  Qin2pro_s9863a1h10_v1.1.0.pac
python3 scripts/extract-pac.py extract Qin2pro_s9863a1h10_v1.1.0.pac BOOT out/boot.img
```