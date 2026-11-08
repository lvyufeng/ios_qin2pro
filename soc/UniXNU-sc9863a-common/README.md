# UniXNU-sc9863a-common

UNISOC **SC9863A**（SharkL3）的共享 SoC 描述层。格式按 UniXNU `schema_version = 2`
的 SoC 仓库规范（对照 `UniXNU-sm8550-common`）。

> **状态：描述层已建立并验证，尚未接入任何可运行固件。**
> 展锐没有 UEFI 移植，`hxzbaka/UniXNU-Unisoc` 是空壳（仅 LICENSE），
> 目前没有可供本 SoC 使用的 `uefi-<device>` 仓库。

## 内容

```
soc.toml                        SoC 元数据与组件选择
dts/sc9863a.dtsi                CPU / GIC / timer / PSCI / AP-APB 串口
configs/sc9863a.config          所有 SC9863A 板共用的内核配置
patches/xnu/series              SoC 补丁列表
patches/xnu/sc9863a-uart.patch  polled UART 驱动（XNU pexpert）
```

## 数值来源

全部取自厂商内核
[`KevinXu07/android_kernel_qin_qin2pro`](https://github.com/KevinXu07/android_kernel_qin_qin2pro)
（分支 `qin2pro-bringup`，源自 MotorolaMobilityLLC/kernel-sprd）：

| 资源 | 值 | 文件 |
| --- | --- | --- |
| CPU | 8 × Cortex-A55，MPIDR `0x0…0x700`，`psci` | `sc9863a.dtsi` |
| PSCI | 0.2，`method = "smc"` | `sc9863a.dtsi` |
| GICD / GICR | `0x14000000` (`0x20000`) / `0x14040000` (`0x100000`) | `sc9863a.dtsi` |
| redistributor-stride | `0x20000` | `sc9863a.dtsi` |
| 定时器 | PPI 13/14/11/10，`arm,no-tick-in-suspend` | `sc9863a.dtsi` |
| AP-APB 总线 | `0x71300000` | `sc9863a.dtsi`（`ap_apb_regs` 注释） |
| UART0/1/2 | `0x71300000` / `0x71400000` / `0x71500000` | `sharkl3.dtsi` |
| UART IRQ | SPI 2 / 3 / 4，level high | `sharkl3.dtsi` |

主线 `torvalds/linux` 的 `sc9863a.dtsi`、`sharkl3.dtsi` 与之逐项一致（GIC 基址、
UART 偏移、PPI 编号）。

## 与 T610（UMS512 / SharkL5Pro）的差异

| | T610YP | SC9863A |
| --- | --- | --- |
| UART | `0x70100000` | `0x71300000` |
| GICD / GICR | `0x12000000` / `0x12040000` | `0x14000000` / `0x14040000` |
| CPU | 6×A55 + 2×A75 | 8×A55 |
| DRAM base | `0x40000000` | `0x80000000` |
| framebuffer | `0x9e000000` | `0x9e000000`（一致） |

`patches/xnu/sc9863a-uart.patch` 与 `UniXNU-t610yp` 的同名补丁**只差两行**
（`SPRD_UART_BASE` 与注释），hunk 行数完全一致，便于互相 diff。

## 已做的验证

```sh
# 1. 补丁能在官方 xnu-8796.141.3 上干净应用
cd <xnu-checkout> && git apply --check -p1 sc9863a-uart.patch   # rc=0
patch -p1 --dry-run < sc9863a-uart.patch                        # 两个文件均通过

# 2. DTS 编译且地址解析正确
dtc -I dts -O dtb -i soc/UniXNU-sc9863a-common/dts \
    -o build/hardware.dtb dts/s9863a1h10.dts                    # rc=0，无警告
dtc -I dtb -O dts build/hardware.dtb                            # UART0/1/2 与 GIC 地址符合预期
```

## 与真机设备树的交叉核对

这一层的每个地址都对着**官方固件里反编译出来的设备树**核过（`device.dts`，来自
`Qin2pro_s9863a1h10_v1.1.0.pac` 的 `DTB` 分区，6318 行）。厂商内核源码里的 DTSI 是
"参考板"描述，固件里的才是这台机器的实际配置。

| 项 | 本层写的 | 固件设备树 | 结果 |
| --- | --- | --- | --- |
| `serial@70000000` | IRQ `<0 2 4>`, `okay` | `interrupts = <0x00 0x02 0x04>`, `okay` | ✅ |
| `serial@70100000` | IRQ `<0 3 4>`, `okay`（板级启用） | `<0x00 0x03 0x04>`, `okay` | ✅ |
| `serial@70200000` | IRQ `<0 4 4>`, `disabled` | `<0x00 0x04 0x04>`, `disabled` | ✅ |
| `serial@70300000` | IRQ `<0 5 4>`, `disabled` | `<0x00 0x05 0x04>`, `disabled` | ✅ |
| GIC | reg 4 元组，`redistributor-stride = 0x20000` | `reg = <0 0x14000000 0 0x20000 0 0x14040000 0 0x100000>`，stride `0x20000` | ✅ |
| CPU | `cpu@0..700`，reg `0x0/0x100/.../0x700` | `CPU0..CPU7: cpu@0..cpu@700`，`reg = <0 0x0>` | ✅ |
| 内存 | 板级 `memory@80000000` 2 GiB | `<0x00 0x80000000 0x00 0x80000000>` | ✅ |
| 定时器频率 | `clock-frequency = <0x18cba80>`（26 MHz） | `clock-frequency = <0x18cba80>` | ✅ |

### 定时器：我们比厂商写得更全

| | 固件设备树 | 本层 |
| --- | --- | --- |
| 中断 | `<1 0xe 0xf08>, <1 0xa 0xf08>`（只有两个） | `<1 0xd 4>, <1 0xe 4>, <1 0xb 4>, <1 0xa 4>`（四个） |
| 触发标志 | `0xf08`（展锐老式复合标志） | `4`（标准的 level-high） |

四个 PPI（13/14/11/10）是 ARMv8 架构规定**全部存在**的，厂商只是没列出来；触发类型也
是标准的高电平。所以这里保留完整声明。

> 注意：XNU 用的是**虚拟定时器**（`CNTV_CTL_EL0`，见 `arm64/machine_routines.c:2318`）。
> 虚拟定时器要出中断，EL2 必须在进入 EL1 前设置好 `CNTVOFF` 并使能中断。展锐固件到底
> 传了什么，静态分析看不到——这是上机后要确认的点之一。

## 与官方 SoC 仓库的结构对照

对着 `UniXNU/UniXNU-sm8550-common` 核了一遍，让这一层能被 UniXNU 直接当 SoC 模块用：

| 项 | 官方 sm8550-common | 本层 | 说明 |
| --- | --- | --- | --- |
| `soc.toml` | ✅ `schema_version = 2` | ✅ | 同格式 |
| `configs/` | ✅ | ✅ | `sc9863a.config` |
| `dts/` | ✅ | ✅ | `sc9863a.dtsi` |
| `patches/` | ✅ | ✅ | `patches/xnu/{series,sc9863a-uart.patch}` |
| `README.md` | ✅ | ✅ | |
| `LICENSE` | ✅ BSD-2-Clause-Patent | ✅ 同 | 已补 |
| `kits/` | ✅ | ➖ | 本 SoC 没有需要捆绑的 kit |
| `source_revision` | 完整 commit | ✅ 完整 commit | 从分支名改成 `55ba83c0…` |

### 修正过的一处

`[components] include` 里原本写了 `XnuPrelink`，但它是 UniXNU 的 **kit**，不是
`drivers/` 下的组件。官方 sm8550 的清单里只列 driver（`ArmCpu`、`ArmDTPlatform`、
`Adreno740`、`AdrenoMetal`）。已删除，现在列的是：

```
ArmCpu, ArmDTPlatform, ArmVirtPlatform, SoftwareAES, DarwinBoot
```

这五个都能在 `UniXNU/UniXNU/drivers/` 下找到同名目录。

> 注意：本层目前**还没有被真正消费过**——设备仓库仍是 format 1，UniXNU 不会去读
> `soc.toml`。格式正确性是对照官方仓库核的，不是跑通的。

## 未做的

- 没有固件（UEFI）可用，`[firmware]` 段无从填写。
- 没有 OS profile（`ipados-*`）；当前按 format 1 的 `feeds.toml` 锁 xnu-8796.141.3。
- GPU 无组件：PowerVR GE8322 没有任何 Darwin 驱动。