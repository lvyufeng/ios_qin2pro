# BROM 直连（SoC 内部）

fastboot 写不进自制镜像之后，剩下的路是绕开 U-Boot，直接跟 SoC 的 BootROM 说话。
这篇记录走到了哪一步、参数从哪来、以及踩到的坑。

## 为什么需要它

`fastboot flash boot` 对**任何改动过的镜像**都不回话、不写盘（原厂镜像可以）。
详见 `docs/usb-fastboot-notes.md`。BROM 在 SoC 内部，不经过 U-Boot，也就没有那层检查。

## 工具

`CE1CECL/spd_dump`，本机编译方式见 `docs/usb-fastboot-notes.md`。
产物 `/tmp/spd_dump/spd_dump`（约 93 KB，链接自编的 libusb）。

## 参数（全部有出处，不靠猜）

| 项 | 值 | 出处 |
| --- | --- | --- |
| 进 BROM 的按键 | **音量上** + 插 USB | 实测：音量下会进充电模式 |
| FDL1 装载地址 | `0x00005000` | PAC 里 `s9863a1h10.xml` 的 `<ID>FDL</ID><Base>` |
| FDL2 装载地址 | `0x9EFFFE00` | 同上，`<ID>FDL2</ID><Base>` |
| FDL1 文件 | PAC 的 `fdl1-sign.bin`（51 572 字节） | PAC 条目 `@0x00016908` |
| FDL2 文件 | PAC 的 `fdl2-sign.bin`（508 660 字节） | PAC 条目 `@0x0002327c` |

**用的是这台手机自己的 FDL**，所以不存在"FDL 和机型不匹配会把硬件弄坏"的风险。

## 已经跑通的部分

```
Waiting for connection (300s)
libusb_control_transfer ok
BSL_REP_VER: "SPRD3"                              ← BootROM 应答
EXEC FDL1
BSL_REP_VER: "Spreadtrum Boot Block version 1.1"  ← FDL1 起来了
```

**BootROM → FDL1 这一段完全正常。**

## 卡住的部分：FDL2 起不来

```
SEND fdl2-sign.bin to 0x9efffe00
send: type = 0x21        ← EXEC FDL2
usb_recv failed : LIBUSB_ERROR_TIMEOUT
recv: type = 0x81, size = 34
unexpected response (0x0081)     ← 设备仍在用 FDL1 的口吻回应
```

`0x81` 是版本应答，说明 **FDL2 没有接管**，设备还停在 FDL1。

### 原因很可能是 DHTB 封装

PAC 里的 `fdl2-sign.bin` 不是裸二进制：

```
0x000  "DHTB" 01 00 ...            头部
0x030  40 be 07 00                 = 0x7BE40 = 507 456（载荷大小）
0x200  0a 00 00 14                 b +0x28     ← 真正的入口跳转桩
0x204  1f 20 03 d5                 nop
0x208  00 00 00 9f ...             = 0x9F000000
0x210  e8 b6 07 00 ...             = 0x7B6E8 = 505 576（镜像大小）
0x228  c0 3e 00 10 ...             ARM64 代码
```

也就是说真正的代码从 `0x200` 开始。原样从 `0x9EFFFE00` 执行等于把 DHTB 头当指令跑。

已生成剥头版本：

```sh
python3 - <<'PY'
data = open('/tmp/qinfw/fdl2.bin','rb').read()
open('/tmp/qinfw/fdl2-raw.bin','wb').write(data[0x200:])
PY
```

**这个版本还没上机验证过**——最后一次尝试时手机没有进入 BROM（停在充电模式）。

另一个等价做法：把带头的文件装载到 `0x9EFFFC00`，让代码正好落在 `0x9EFFFE00`。

## 完整命令（等手机能进 BROM 时用）

```sh
cd /tmp/spd_dump && ./spd_dump --verbose 1 --wait 300 \
  keep_charge 1 \
  fdl /tmp/qinfw/fdl1-sign.bin 0x5000 \
  fdl /tmp/qinfw/fdl2-raw.bin 0x9EFFFE00 \
  disable_transcode \
  partition_list /tmp/qin-partitions.xml \
  skip_confirm 1 blk_size 0x2000 \
  write_part boot build/payload/hello-uart-boot-padded.img \
  read_part boot 0 0x1000 /tmp/boot-head-after.bin \
  reset
```

`partition_list` 是只读的，它成功就说明 FDL2 通了；失败会直接退出，不会走到 `write_part`。

## 坑：BROM 里退不出来

FDL1/FDL2 模式下**屏幕是黑的，按电源键没反应**，这是正常的——CPU 在跑下载程序。

退出办法（按顺序）：

1. **按住 电源 + 音量上 15~20 秒**（spd_dump 自己也这么提示）
2. 充电 5~10 分钟再试（BROM 状态耗电快，可能只是没电了）
3. 电池可拆的话，取下电池等 10 秒

## 还没验证的

- 剥头后的 FDL2 能否启动
- FDL2 启动后能否读分区表、写 `boot`
- 写完之后设备能否正常启动我们的镜像