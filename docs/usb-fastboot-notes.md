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