# Sony PCM-A10 蓝牙遥控

> **下载**：[Windows 便携版与源码包](https://github.com/HappyAny/sony-pcm-a10-remote/releases/latest) · **许可**：GPL-3.0-only；第三方组件保留各自许可。

Windows 10/11 x64 上的非官方 PCM-A10 遥控程序。**0.3.2 提供单个便携 EXE，内置 .NET 和 Python，无需安装运行时。** 界面使用微软官方 WPF Fluent 主题，默认窗口为 540×620，可缩到 480×520。录音、暂停、继续合并为一个随设备状态变化的按钮，标题显示 PCM-A10。主页面集中放置录音、试音、电平和标记；完整设置放进第二个页签，设备详情与操作历史默认折叠。支持跟随系统、浅色和深色主题。

## 功能

- 一个按钮完成开始录音 → 暂停录音 → 继续录音；独立停止；右上角菜单或录音按钮右键菜单可仅进入待机、监听电平。另有自动试音开始／结束、增益加减和输入 0–30 直接设置，以及明确点击后将当前输入切到手动电平。
- 普通、重要、问题、新话题四种标记，支持自定义 UTF-8 名称，并显示设备返回的编号、时间、类型；可读取最近标记。
- 录音格式、场景、内置／外接麦克风灵敏度、音频输入灵敏度、外部输入类型、NCF／LCF 滤波、限幅器、预录音、VOR、同步录音、跨存储器录音、自动标记、标记间隔、标记时间信息，以及保存 My Scene 1/2。
- 电池、输入源、录音格式、场景修改状态、文件名、已录／剩余时间、预录缓存状态、L/R 电平与过载提示；设备提供峰值保持能力时，显示峰值及清除控制。
- 电量显示为四格电池图标：三／四格绿色、两格黄色、一格红色；充电状态显示闪电。充电时设备不提供电量档位，程序不推算百分比。
- 关闭窗口自动断开遥控并退出专属后端，保留 Windows 配对；窗口异常退出时也会回收其后端进程。

选项来自设备能力回执，不向 A10 添加未支持的录音格式。此台 A10 报告 15 项普通设置和 1 项 My Scene 保存控制，另有主页面中的试音能力。具体协议、覆盖表和未公开扩展项见 [控制功能覆盖表](docs/control-coverage.md)。

设置操作检查设备返回码并读回确认值；场景等操作后重新读取关联设置。修改 My Scene 前有覆盖确认。自动标记间隔的操作按 Sony 顺序先开启自动标记，再设置间隔；失败时显示实际已生效的值，不自动重发。

## 启动

- 直接双击 **[`dist/standalone/PCM-A10.exe`](dist/standalone/PCM-A10.exe)**。只需复制这一个文件，约 76 MB；无需安装 Python 或 .NET，也不需要 runtime 文件夹。
- 源码发布包中的 **`Launch.cmd`** 默认启动同一个单文件版。
- 双击 **`Demo.cmd`** 体验模拟器。标题、顶部徽标和底部文字都会标明模拟模式。
- 只保留 EXE 时，可运行 `PCM-A10.exe --demo` 进入模拟器。桌面界面和 Python 控制核心通过私有标准输入／输出通信，无需启动服务或打开网络端口。

便携版采用 .NET 官方的自包含单文件发布方式，包含 .NET 10.0.14 Windows Desktop Runtime x64 和 Python 控制核心。运行时会自动释放必要文件到当前用户的临时目录，不安装服务、不修改系统配对；Python 后端临时目录在正常退出时清理，异常退出后的目录在下次启动时回收。.NET 自身管理其原生运行库临时缓存。右上角菜单的「许可证」可查看内置许可证。

`Launch.cmd`、`Demo.cmd`、`python pcma10.py` 和 `python pcma10.py --demo` 优先使用单文件版。开发用目录版仍可通过 `scripts/build_fluent.py` 构建到 `dist/fluent/`，该变体需要安装 .NET 10 Windows Desktop Runtime x64。`src/pcma10_remote/gui.py` 仅保留旧 Tk 界面源码，不作为默认入口；新发布程序不包含 Tcl/Tk。

## 连接 PCM-A10

1. A10 开机，进入 **Bluetooth → REC Remote**。仅首次使用时选择 **Add Device (Pairing)**。
2. 首次使用时，在 Windows「蓝牙和设备」中添加 PCM-A10，并完成系统与录音机显示的确认。以后保留这个配对即可。
3. 断开手机或其他电脑遥控窗口的连接。同一时间仅使用一个控制连接。
4. 在程序中选择录音机，点击「连接设备」。设备改名后，可在选择框手动输入蓝牙地址。
5. 等待设备确认状态，再进行操作。右上角设置菜单可以切换主题和开启本地通信日志。

停止时按钮显示「开始录音」，录音中显示「暂停录音」，暂停／待机时显示「继续录音」。每次点击先读取设备状态，再决定开始或暂停，避免界面状态滞后引起误操作。「停止」也需得到设备状态确认才显示完成。仅监听电平请使用右上角菜单的「进入录音待机」，或右键点击录音按钮。

关闭窗口会优先断开蓝牙并释放设备，**不会自动停止录音，也不会删除配对**。再次打开选择原设备并点击「连接设备」即可，无需每次重新配对。录音机刚释放连接时可能短暂超时，程序仅对建立蓝牙通道重试一次，不重发录音、标记或设置命令。

当前设备设置下，A10 拒绝调整增益并返回 `0xA1`。界面会显示拒绝提示，保留设备实际返回的数值。可以点击“当前输入切为手动”，或在“录音设置”页明确选择手动灵敏度后重试；程序不会因增益失败而自行修改输入配置。

蓝牙应答丢失时，程序报告结果未确认并断开连接，不自动重复录音或标记操作。请检查录音机屏幕后重连。后台状态刷新不会反复禁用控制按钮。

## 测试与功能边界

控制核心已完成一轮 A10 实机测试：录音、暂停、继续、停止、标记、双声道电平、时间、电池和设备状态均获得真实响应。增益拒绝处理已实机复测。原始记录见 [实机测试报告](docs/hardware-test-2026-10-26.md)。

0.3.2 的验证记录见 [本次交付报告](docs/release-0.3.2.md) 和 [机器可读结果](docs/validation.json)。58 项自动测试通过。只复制单个 EXE 到独立的中文／空格目录，并移除测试进程的 Python／.NET 搜索路径后，原生界面全部模拟流程通过，退出码为 0；正常关闭、异常退出的后台回收，以及下次启动清理遗留临时目录均通过。充电显示代码与 0.3.1 完全一致。

0.3.1 的实机关闭／重连回执仍保留，测试沿用原有配对，不发送录音或设置更改命令。本轮单文件打包测试使用模拟器，未操作正在使用的真实连接。此前新增设置、试音和 My Scene 的真实写入仍待复测；模拟自检不替代硬件证据。

本程序只负责遥控，**不会把 PCM-A10 变成 USB／蓝牙实时录音声卡**。本次也没有导出、试听录音文件，尚未验证长时间无线连接。

## 命令行与源码

命令行需要 Python 3.11+，不依赖第三方 Python 库。界面构建需要 .NET 10 SDK 和 PyInstaller；项目没有第三方 NuGet 依赖。

```powershell
# 检查系统接口；不连接录音机
python pcma10.py doctor
python pcma10.py devices
python pcma10.py devices --scan
python pcma10.py devices --all

# 将示例地址换成实际设备地址；先断开桌面遥控连接
python pcma10.py command status --address AA:BB:CC:DD:EE:FF
python pcma10.py command record --address AA:BB:CC:DD:EE:FF
python pcma10.py command pause --address AA:BB:CC:DD:EE:FF
# 读取设备当前状态，自动选择录音／暂停
python pcma10.py command record-toggle --address AA:BB:CC:DD:EE:FF
python pcma10.py command mark --type 2 --name Important --address AA:BB:CC:DD:EE:FF
python pcma10.py command gain --value 15 --address AA:BB:CC:DD:EE:FF
python pcma10.py command stop --address AA:BB:CC:DD:EE:FF

# 全部录音设置与设备确认值
python pcma10.py settings --address AA:BB:CC:DD:EE:FF
python pcma10.py set-setting rec-mode 13 --address AA:BB:CC:DD:EE:FF
python pcma10.py set-setting pre-record 1 --address AA:BB:CC:DD:EE:FF
python pcma10.py command manual-input --address AA:BB:CC:DD:EE:FF
python pcma10.py command rehearsal-start --address AA:BB:CC:DD:EE:FF
python pcma10.py command rehearsal-stop --address AA:BB:CC:DD:EE:FF
python pcma10.py command last-mark --address AA:BB:CC:DD:EE:FF

# 模拟器与自动测试
python pcma10.py demo-check
python scripts/check.py

# 构建单文件便携版、隔离验证与完整源码发布
python scripts/build_portable.py
python scripts/portable_check.py
python scripts/package.py

# 开发用目录版（需要已安装的 .NET 10）
python scripts/build_fluent.py
python scripts/gui_check.py --app-dir dist/fluent
python scripts/lifecycle_check.py --app-dir dist/fluent
# 实机只读连接／关闭／重连；先关闭其他遥控窗口
python scripts/hardware_reconnect_check.py
```

源码位于 `desktop/PcmA10.Fluent/`（原生界面）、`src/pcma10_remote/`（控制核心，settings.py 为设置目录）和 `backend.py`（私有通信入口）。`scripts/build.py` 也会构建单文件版。首次构建会从官方 NuGet 源获取 .NET 运行库包，约 80 MB；发布不会裁剪 WPF 程序。协议事实与验证边界见 [docs/protocol.md](docs/protocol.md)。

启用通信日志后，日志保存到程序目录下的 `logs/`。日志可能包含设备标识，默认关闭；排障分享前应脱敏。原始通信日志、Sony APK、反编译材料、构建缓存及旧 Tk 二进制均不进入新发布包。发布包包含源码、许可证和逐文件 SHA256 清单，并验证 ZIP 的 CRC 与归档内容校验值。

用户自有代码采用 **GPL-3.0-only**；Python、PyInstaller 和 .NET 的原始许可证分别保留，见 [第三方说明](THIRD_PARTY_NOTICES.md)。

## 官方资料

- [Microsoft：WPF 官方 Fluent 主题与 ThemeMode](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/whats-new/net90)
- [Microsoft：.NET 10 WPF Fluent 改进](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/whats-new/net100)
- [Microsoft：自包含单文件发布与运行库临时解压](https://learn.microsoft.com/en-us/dotnet/core/deploying/single-file/overview)
- [Sony：REC Remote 功能](https://helpguide.sony.net/icd/pcma10/v1/en1/contents/TP0001684588.html)
- [Sony：录音、暂停、停止及录音待机](https://helpguide.sony.net/icd/pcma10/v1/en2/contents/TP0001684594.html)
- [Sony：PCM-A10 蓝牙规格](https://helpguide.sony.net/icd/pcma10/v1/en2/contents/TP0001684402.html)
- [Microsoft：SOCKADDR_BTH 与服务 UUID](https://learn.microsoft.com/en-us/windows/win32/api/ws2bth/ns-ws2bth-sockaddr_bth)
