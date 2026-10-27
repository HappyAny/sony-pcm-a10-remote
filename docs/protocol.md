# PCM-A10 REC Remote 协议实现笔记

这是本项目的实现依据，不是 Sony 发布的协议规范。结论来自 Sony REC Remote 3.5.0 Android 客户端的本地静态分析。原始 APK 及反编译源代码只留在项目的 `.research/` 中，不进入源码发布包、可执行文件或知识库。下面记录接口事实、独立实现和验证边界。

## 来源与身份检查

REC Remote Android 包名：`com.sony.songpal.recremote`，版本 3.5.0，versionCode 30501。

| 项目 | 值 |
|---|---|
| APK 文件 SHA-256 | `9a316f380719b95c7ce850c12e33e76be922dd9f1192705479e4a0c238e47303` |
| 签名证书 SHA-256 | `4276919680f4aba6d41924031c20ac2624feea5e8ec68a36529b2acb664d911e` |
| 证书主体 | `CN=mobile.link, OU=Corporation, O=Sony Corporation, L=Shinagawa, ST=Tokyo, C=jp` |
| 签名验证 | Android SDK apksigner：v1、v2 验证通过 |
| 分析工具 | JADX 1.5.6；全应用反编译有 13 项错误，关键帧编码类可重新编译并验证 |

取得来源：[APKPure 版本信息及文件摘要](https://apkpure.net/rec-remote-sony-ic-recorder/com.sony.songpal.recremote)。Sony 证书指纹另与 [APKMirror 上 3.1.0 的签名记录](https://www.apkmirror.com/apk/sony-video-sound-products-inc/rec-remote-sony-ic-recorder/rec-remote-sony-ic-recorder-3-1-0-release/rec-remote-sony-ic-recorder-3-1-0-android-apk-download/)一致。镜像网站只是 APK 获取／摘要核对途径；协议事实取自本地已验签客户端，不能由镜像说明直接证明。

## 传输与帧

客户端 `com.sony.songpal.linkservice.SppService` 使用 `createRfcommSocketToServiceRecord`，服务 UUID 为：

```text
84C6978C-DEB9-4073-8C9D-D7BF48790EF7
```

这是经典蓝牙 RFCOMM，不使用 BLE GATT，也不使用标准 SPP 的 `00001101` UUID。Windows 实现通过 `SOCKADDR_BTH.serviceClassId` 指定 Sony UUID，`port=0`，让系统通过 SDP 解析 RFCOMM 通道。Microsoft 的 `ws2bth.h` 使用 `pshpack1.h`，该地址结构必须按 **1 字节对齐，共 30 字节**；设备枚举的 `BLUETOOTH_DEVICE_INFO` 在 Windows 为 560 字节。

未转义的数据体：

```text
type:u8 | sequence:u8 | payload_length:u32_big_endian | payload | checksum:u8
```

`checksum` 是此前所有数据体字节之和模 256，不是 CRC。帧以 `3E` 开始、`3C` 结束；数据体中的 `3C`、`3D`、`3E` 分别转义为 `3D 2C`、`3D 2D`、`3D 2E`。长度只计算 payload，不包含头和校验。

| 类型 | 含义 | 应答 |
|---|---|---|
| `09` | 可靠数据，sequence 为 0／1 | 发送 `01`，sequence 为收到值的反值，payload 长度 0 |
| `01` | ACK | 只接受与当前待确认帧相配的序号 |
| `19` | Shot 实时通知，常见 sequence 为 `FF` | 不发送 ACK |

每个方向独立管理序号，可靠数据采用交替位；重复可靠帧仍应答，但不重复分发。一次只允许一个待确认发送帧。本实现遇到应答超时会报告结果不确定并断开，不自动重发动作，也不模仿原客户端在多次重试后翻转序号的行为。

## 握手

1. RFCOMM 建立后等待设备发送 `00` greeting，先按传输层规则 ACK。
2. greeting 包含协议版本、能力版本、型号类别、长度前缀型号字符串、长度前缀设备标识。版本 `>=3000` 时多出型号类别字段。
3. 本程序仅接受型号字符串 `PCM-A10`、型号类别 `01`、协议版本 `3000..3010`。否则关闭连接，不发送录音控制。
4. 发送 `01 01`，表示应用在前台；发送 `02` 请求能力，等待 `03` 能力列表。
5. 查询状态、显示信息、增益和时间。只有握手及有效状态返回完成后才启用按钮。

上述流程依据初始化处理器 `p017g1.b`、`W0.C0112e`、`W0.C0113f`、`W0.C0114g`、`W0.C0115h`。型号类别会影响按键语义；本版本针对 A10 的类别 `01` 实现，不推测兼容其他 Sony 录音机。

## 已实现命令

以下为 payload，外层仍需帧封装。

| 功能 | payload | 返回／依据 |
|---|---|---|
| 应用前台 | `01 01` | 初始化后发送 |
| 查询能力 | `02` | `03` |
| 停止 | `10 01` | 后续设备状态 `0704=1` |
| 暂停／录音待机 | `10 02` | 后续设备状态 `0704=2` |
| 录音／继续 | `10 03` | 后续设备状态 `0704=3` |
| 增益加／减 | `10 04` / `10 05` | 再查询 `0604` |
| 设置增益 | `15 06 04 value:u16be` | 设置结果 `16`；再查询 `0604` |
| 查询增益 | `17 06 04` | `18` |
| 添加标记 | `11 06 03 type:u16be name_len:u8 utf8_name` | `12`，首个状态字节 0 表示成功 |
| 查询录音状态 | `20` | `21`；状态变化通知 `22` |
| 查询显示／电池 | `23` | `24`；变化通知 `25` |
| 已录制时间 | `30 07 06` | `31`；通知 `32` |
| 剩余录音时间 | `30 07 07` | 同上 |

按键编码依据 `W0.y` 与 `p033m1.q`；按钮绑定依据 `p1.c` 和 `q1.a/b/c`；A10 类别依据 greeting 解析及 `Y0.b` 中的 A10 模拟资料。增益加减方向另核对 `RecLevelLayout`、`q1.m/n`、`p033m1.o`。这些是静态调用链证据，尚无 A10 实机命令回执。

状态／数值列表结构为 `command status:u8 count:u8 (item:u16be value:u16be)*`。状态为 0 时才更新正常状态；`0704` 是录音状态，`0701` 是电池枚举，`0604` 是录音增益。电池是档位／充电等枚举，不能转换为精确百分比。增益范围 0..30 来自原 App 的数值限制，实际能否调整还取决于设备输入／自动增益设置。

时间返回为 `command status count item_high item_low hour_high_bcd hour_low_bcd minute_bcd second_bcd`；时间字段用 BCD 解码。实时电平来自命令 `40` 通知；原客户端存在每条 11 字节和 17 字节两种记录结构，程序依据严格的记录长度选择解析。两者的 dB 表示不同，不可仅对原字节取负数。本项目未验证 A10 固件实际选择哪种结构。

## 验证边界

2026-10-26 后续实机结果：A10 返回协议 `0x3000`、型号类别 `01`，已验证实际握手、状态、录音／暂停／继续／停止、标记、时间及双声道电平；下列“尚未实机”条目保留的是首次实现时的边界。当前状态以 [hardware-test-2026-10-26.md](hardware-test-2026-10-26.md) 和 `validation.json` 为准。

实际捕获的增益设置回复为 `16 A1 02 06 04 00 0A 02 01 00 08`，随后增益查询仍会成功返回原值 10。因此，成功查询当前值不能证明先前的设置成功。0.1.1 会先验证 `16` 的设备设置结果，再核对实际增益，拒绝码与数值不一致均不会发出 `action_confirmed`。该回复已加入回归测试。

- `scripts/reference_vectors.py` 直接编译本地反编译的帧编码类 `T0.c` 和求和类 `T0.e`，对比 6 组输入，包括全部 256 种字节、转义及多字节长度。Python 输出逐字节一致。结果见 `reference-validation.json`。
- 自动测试验证帧解析、随机分片、校验失败、序号、重复帧、型号拒绝、状态确认、丢失 ACK 和模拟控制流程。
- 原生 Windows 枚举可检测到 1 个蓝牙无线电，本次检测到的 PCM-A10 为 0。原生 RFCOMM socket 可创建，Windows 地址 ABI 已核对。
- 尚未通过设备 SDP 查询、真实 RFCOMM 建链、设备 greeting、实际录音文件或长时间连接验证。模拟器是根据相同协议事实编写的测试对端，不是独立的硬件证据。

## 外部原始资料

- [Sony PCM-A10 使用指南](https://helpguide.sony.net/icd/pcma10/v1/en2/index.html)
- [Sony 录音／暂停／停止与录音待机说明](https://helpguide.sony.net/icd/pcma10/v1/en2/contents/TP0001684594.html)
- [Microsoft SOCKADDR_BTH](https://learn.microsoft.com/en-us/windows/win32/api/ws2bth/ns-ws2bth-sockaddr_bth)
- [Microsoft Windows SDK 头文件 ws2bth.h](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/ws2bth.h)
- [JADX 1.5.6](https://github.com/skylot/jadx/releases/tag/v1.5.6)

## 0.3.0 扩展

新增能力驱动的设置目录、通用项目读写、试音、四类标记回执、最近标记、文件名、预录状态、峰值及设备通知。设置命令为 `15 item:u16be value:u16be`，读取为 `17 item:u16be`；所有写入项均来自白名单及设备能力。类型 2 描述符中的数值指定控制格式，不能当成当前设置值。

完整字段、命令确认与本次实机边界见 [control-coverage.md](control-coverage.md)。真实能力数据见 `a10-capabilities-2026-10-26.json`，原生集成自检见 `fluent-self-test.json`。本轮连接失败，因此未把新增写入操作记为实机成功。
