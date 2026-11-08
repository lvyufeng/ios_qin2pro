# 构建记录

第一个能跑通的构建链路：**XNU 从苹果开源源码编出 arm64 内核，并包含 SC9863A 的 UART 驱动**。

## 结果

| 项 | 值 |
| --- | --- |
| 产物 | `build/artifacts/kernel.macho` |
| 大小 | 15 741 560 字节 |
| 类型 | Mach-O 64-bit executable **arm64**（`MH_EXECUTE`） |
| sha256 | `e12c29aeb7df5bcd41872a1c4dd259c97ef02b05223b8ac5b460164923eb4504` |
| XNU | `xnu-10063.141.1`（Darwin 23.6.0），提交 `d8b80295` |
| 机器配置 | `ARM64` / `DEVELOPMENT` / `BCM2837` |
| 预链接 | **跳过**（无 IPSW/kernelcache），`kernel.json` 记 `prelink_skipped: true`、`boot_verified: false` |

内核里带我们的驱动（符号已核实，共 7 个 `sprd_uart*`）：

反汇编里能直接看到 console 串口的基址：

```
fffffff007955238:  mov  w0, #0x70100000 ; =1880096768
fffffff00795523c:  mov  w1, #0x1000     ; =4096
fffffff007955240:  bl   _ml_io_map
```


```
fffffff007b6a178 b _sprd_uart_base
fffffff007955134 s _sprd_uart_init
fffffff007955164 s _sprd_uart_transmit_data
fffffff007955180 s _sprd_uart_receive_ready
fffffff007955198 s _sprd_uart_receive_data
fffffff007208b68 s _sprd_uart_serial_functions
```

源码侧确认：`.cache/xnu-work/pexpert/arm/pe_serial.c:751` 为
`#define SPRD_UART_BASE       0x70100000ULL`。

> 这个值改过一次：最初按厂商 DTSI 里 `ap_apb_regs = 0x71300000` 的注释推断 UART0 在
> `0x71300000`，但固件 `boot.img` 的 cmdline 是 `console=ttyS1`，真实设备树里
> uart1 = `0x70100000`。详见 `docs/firmware-extraction.md`。

## 命令

```sh
# 需要 ninja 与可用的 cmake 在 PATH 里
cd <UniXNU 检出>
./unixnu fetch -D <本仓库>
./unixnu defconfig s9863a1h10_defconfig -D <本仓库>
./unixnu build kernel --skip-prelink -D <本仓库>
```

## 主机准备（本机原本缺的）

| 工具 | 处理 |
| --- | --- |
| `ninja` | macOS 预编译包，放到 `PATH` |
| `cmake` | 原本的 `~/.local/bin/cmake` 是坏的 python 包装（`No module named cmake`），换成 Kitware 3.31.6 官方二进制 |
| `dtc` | 从 `dgibson/dtc` 源码编译（`NO_PYTHON=1 NO_YAML=1`，另需一个 `pkg-config` 桩） |

Xcode 27.0 + iPhoneOS 27.0 SDK 本机已有，内核编译走它。

## 途中修掉的四个问题

### 1. XNU 版本与基础补丁不匹配

原 `feeds.toml` 沿用 T610YP 的 `xnu-8796.141.3`（Darwin 22），而 UniXNU 当前的基础补丁是
按 `xnu-10063.141.1`（Darwin 23）写的，打 `osfmk/arm64/start.s` 时失败。

改为上游 pin：

```toml
ref = "xnu-10063.141.1"
commit = "d8b80295118ef25ac3a784134bcf95cd8e88109f"
darwin = "23.6.0"
version = "10063.141.1"
driverkit_tag = "xnu-10063.121.3"
trustcache_tag = "xnu-8796.101.5"
```

我们的 UART 补丁在新版本上**同样干净应用**（`git apply --check` rc=0），无需改动。

> 注意：`unixnu.lock` 优先于 `feeds.toml`。改 pin 后必须删掉锁里的对应条目再 fetch，
> 否则它会继续拉旧提交。构建脚本还会复用 `.cache/xnu-work`，也要一并删。

### 2. corecrypto 未链接

`prng_random.o` 引用 `_corecrypto_kext_start`：`CONFIG_XNU_SOURCE_CORECRYPTO14=1` 时
必须把源码 provider 归档链进去。补上归档计划：

```toml
"build-corecrypto-14.py",
"build-corecrypto-14.py --namespace",
```

构建结果：529 个 C 源文件、0 失败；命名空间版导出全局为 0。

### 3. 缺 ArmCpu / ArmDTPlatform / ArmVirtPlatform / libpthread 归档

模板里的 `build-armcpu.py` 是设备仓库脚本，不在 UniXNU 中。改用组件目录里的通用构建器：

```toml
"build-arm-driver.py ArmCpu",
"build-arm-driver.py ArmDTPlatform",
"build-arm-driver.py ArmVirtPlatform",
"build-libpthread-kernel.py",
```

四个归档的 `-Werror` 构建与 arm64-apple-ios17.0 对象审计均通过。

### 4. 设备补丁根本没被应用

format 1 的构建脚本只扫 `patches/xnu/*.patch`；设备仓库的补丁落在 `patches/<name>/`。
而设备仓库同名模块会**覆盖**上游（模块名先到先得），所以不能把该目录直接改名为 `xnu`
——那会丢掉全部基础补丁。

临时方案：`scripts/install-device-patches.py` 作为归档计划第一步，把设备补丁复制进
`tree/patches/xnu/`（该目录是 UniXNU 检出的软链，只会多出一个未跟踪文件）。

**正解**是迁到 `schema_version = 2` 的 `[kernel] patch_series` + `device:patches/xnu/series`，
届时删掉这个脚本。

## bootstrap 阶段"失败"是正常的

`build kernel` 会在首次构建时先跑一遍 bootstrap pass，只为产出
`startup.o.json` 等编译记录。它**故意不链接归档**，因此必然在链接阶段失败并被容错；
真正的结果看 `logs/kernel.log`，而不是 `logs/kernel-bootstrap.log`。

## 仍未做到

- 没有 `kernel.macho` 之外的镜像：payload / boot image 需要固件（UEFI）与 ADT 打包，
  SC9863A 没有可用固件。
- 没有启动过：设备仍停在 bootloader，且 fastboot 工具不匹配。
- `boot_verified: false` —— 这个内核**没有被任何硬件执行过**。