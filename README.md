# 2.4G 外设电量

Windows 10/11 的无线外设电量托盘工具：2.4G 接收器 + 蓝牙设备。

> 本项目基于 [Ap0LoG1zE](https://github.com/Ap0LoG1zE) 的 [EZ2.4G](https://github.com/Ap0LoG1zE/EZ2.4G) 修改而来，核心思路与大部分底层代码出自原作者，详见文末[致谢](#致谢)。

纯 Python 标准库（`ctypes` + `tkinter`），**零第三方依赖**，不写日志。

> **甚至只需要网页版驱动就可以拿到电量协议**——不少 2.4G 外设的网页版驱动直接用 WebHID 查询电量，翻一眼协议比装 USBPcap 抓包省事得多。

![面板](docs/panel.png)

## 下载

两种方式任选：

- **免安装（推荐）**：到 [Releases](../../releases/latest) 下载 `2.4G.exe`，双击运行，无需 Python。
  每次提交自动构建的测试版在 [Actions](../../actions/workflows/build.yml) 的 Artifacts 里（需登录 GitHub）；原作者发布的原版在[这里](https://github.com/Ap0LoG1zE/EZ2.4G/releases)。
- **最小体积**：下载 `my_devices.py`（约 185 KB），安装 Python 3.10+ 后运行：

```bat
python my_devices.py
```

> exe 未签名，Windows 可能提示 SmartScreen，选择“更多信息 → 仍要运行”。

## 功能

- Windows 11 Fluent 风格界面：跟随系统深浅色和强调色，Win11 原生圆角与阴影
- 同时显示**蓝牙设备**电量（耳机、鼠标、手写笔等，和 Windows 设置里看到的一致）：已连接的自动出现，右键可改名或“不再显示”，托盘右键菜单可整体关闭
- 托盘只放**一个图标**，显示所有设备里**最低的电量**；鼠标悬停列出每台设备的电量。**左键**弹出电量面板（贴着任务栏浮出，点别处自动收起），**右键**菜单列出每台设备的电量，还可刷新、导入协议、开关开机自启、退出
- 面板顶部显示 **CPU / GPU 温度**，打开面板时每 3 秒刷新，圆点颜色随温度变化（<70°C 绿、<85°C 黄、更高红），详见下方[温度说明](#cpu--gpu-温度)
- 面板里每台设备一张卡片：设备图标、状态、电量圆环；右键卡片可重命名、设置读取、导入 / 导出协议、复制设备信息、删除
- 认得的设备自动添加；其他设备点面板右上角的「+」（或空面板里的「添加设备」）从列表里选，**不用插拔**；新插入的设备也会弹窗询问
- 面板右上角「编辑」可以给设备改名、删除设备（也可以双击设备名改名）；删掉的设备不会再被自动加回来，需要时从「添加设备」里加回
- 设备图标用的是微软 Fluent Emoji 的 3D 图标（鼠标、键盘、耳机、手柄、接收器）
- 罗技、雷蛇、ATK、迈从、英菲克、雷柏、ROG、宏碁等常见设备**插上就自动出现**，不用抓包，见[支持的设备](#支持的设备)；Xbox 兼容手柄也会显示
- 新设备插入或重新拔插时弹窗确认，显示设备名、VID:PID 和当前电量
- 双击卡片上的设备名即可重命名，名称保存在配置里
- 右键设备卡片可删除该设备，同时清除它的抓包协议和上次电量
- 自动刷新、开机自启（启动项名 `2.4G`）
- 只读查询，不修改设备设置

## 支持的设备

插上就能自动识别、自动出现在面板里，不用抓包、不用装厂商软件：

| 品牌 | 设备 | 方式 | 来源 |
|---|---|---|---|
| 罗技 | LIGHTSPEED / Unifying / Bolt 接收器下的鼠标键盘（G304、G102、GPW3、M750 等） | HID++ 2.0 | Voltide 真机 · Solaar |
| 雷蛇 | 毒蝰、炼狱蝰蛇、巴塞利斯蛇、眼镜蛇、那伽等无线鼠标接收器（约 26 款） | 90 字节控制帧 | Voltide 真机 · OpenRazer |
| 雷蛇 | 旋风黑鲨 V2 Pro 耳机 | 64 字节音频通道 | Voltide 真机 |
| ATK / VXE | A9 Air、F1 Ultimate 等 2.4G 鼠标（17 字节 CompX 协议） | 查询帧 + 校验 | Voltide 真机 · FerrisPulse |
| ATK | 64 字节协议的鼠标（如 `373B:1216`） | 查询帧 | 2.4G-battery-viewer 真机 |
| ATK | N9 V2 Ultimate 耳机 | 查询帧 | Voltide 真机 |
| IMECOO | P1 鼠标 | 同 ATK 17 字节协议 | Voltide 真机 |
| 迈从 MCHOSE | K99 V2、G75 Pro 等 20 字节协议的键盘 | 查询帧 + 校验 | Voltide 真机 · FerrisPulse |
| 英菲克 Inphic | IN10 系列（9 组接收器） | **只听不写**，鼠标每 2 秒自己上报 | InphicBatteryTray 真机 |
| 英菲克 Inphic | IN9 系列（`248A:5B2E/5B2F`） | Feature 查询 | InphicBatteryTray 真机 |
| 雷柏 Rapoo | 带 19 字节状态流的接收器（如 VT3 二代） | **只听不写** | FerrisPulse · VigilWatt 真机 |
| ROG | OMNI 接收器下的键盘（如夜魔 AZOTH EXTREME） | 查询帧 | Voltide 真机 |
| 宏碁 Acer | OMR 系列无线鼠标 | 查询帧 | FerrisPulse（贡献者真机） |
| 怒喵 Angry Miao | INFINITY MOUSE（`3151:5007` 接收器） | Feature 查询 | AngryMiaoBatteryMgr 真机 · HaloBattery |
| DARKRIM | SHADOW 耳机（杰理方案） | 查询帧 | 2.4G-battery-viewer 真机 |
| VAXEE / ZYGEN | 无线鼠标 | Feature 查询，5% 一档 | 两处文档一致，**未经真机验证** |
| LAMZU / Pulsar / WLmouse | 用 PixArt 方案的无线鼠标 | Feature 查询 | FerrisPulse 文档，**未经真机验证** |
| Xbox 兼容手柄 | Xbox、8BitDo 等 XInput 手柄 | XInput（只有四档：满 / 中 / 低 / 空） | Voltide |
| 蓝牙设备 | 耳机、鼠标、手写笔等 | Windows 自带电量 | — |

另外，任何上报**标准 HID 电量**的设备也能直接读。

### 安全原则

无线接收器是有状态的，乱发命令可能把键盘鼠标弄断连（VigilWatt 记录过这样的事故），所以：

- 每个适配器按**精确的硬件指纹**认领：VID、PID、厂商接口（usage page）、报告长度都要对上。
  例如 `3554` 是好几家共用的号段（迈从、AULA、VGN、派世……），迈从键盘的 20 字节接口和 ATK / IMECOO 鼠标的 17 字节接口分得清清楚楚，不会“同一个厂商编号就试一下”。
- 只发有出处的查询帧（真机验证过或公开协议文档里写明的），回复必须对上回显和校验和才采信。
- 能只听就不写：英菲克 IN10、雷柏只打开读句柄，等设备自己上报。
- 没有适配器认领的设备一个字节都不会发，显示「协议未知」。

### 读取顺序

1. **标准 HID 电量**：能直接读到就用；
2. **你保存的协议**：抓包学来的、或者导入的别人分享的协议；
3. **内置设备适配器**：上表里的设备；
4. 都不行就显示「协议未知」。

设备休眠或未开机时：

- 有协议、也有上次电量：显示上次电量，副标题「上次更新 HH:MM · 设备休眠」；
- 有协议、但没有上次电量：显示「设备休眠」；
- 没有协议：显示「协议未知，无法读取电量」，不会假装休眠。

## 导入 / 导出协议

抓包成功后，可以把协议分享给用同款设备的人，对方不用装 USBPcap、也不用再抓一次：

- **导出**：在设备卡片上右键 →「导出协议…」，保存成一个 `.json` 文件；
- **导入**：右键托盘图标 →「导入协议…」（或在设备卡片上右键、或在「读取设置」窗口里点「直接导入」），选中别人发来的文件。导入后会自动添加设备，插着就能马上读电量。

命令行也可以：

```bat
python my_devices.py --export 我的协议.json
python my_devices.py --import 我的协议.json
```

导入时会检查文件格式，只保留认识的字段；格式不对会用中文说明哪里有问题。

## 想让软件支持你的设备

**不用抓包。** 右键设备卡片 →「复制设备信息」，把复制到的内容（接口指纹、最近一次回复）和设备型号一起贴到 [Issue](../../issues) 里就行。

会写代码 / 懂协议的话，有两种办法自己加：

1. **设备规格文件（不用改代码）**：在导入文件里写一段 `specs`，描述指纹、查询帧和电量位置，并**附上一条真机回复样本 `sample`**——导入时会用样本回放解析，解析不出电量的规格直接拒收（参考 FerrisPulse 的 FDAS 规范）。会向设备写入的规格必须写明 PID。

   ```json
   {"specs": [{
     "name": "某品牌 X1 鼠标", "kind": "mouse",
     "ids": [["373B", "1234"]], "page": "0xFF02", "lens": {"in": 17, "out": 17},
     "send": ["setout"], "frames": ["08 04"],
     "csum": {"kind": "const", "const": 77, "from": 1, "to": 15, "at": 16},
     "reply": "input", "expect": [[0, 8], [1, 4]], "level": [6], "charge": [7, [1]],
     "sample": "08 04 00 00 00 02 50 00 00 00 00 00 00 00 00 00 25"
   }]}
   ```

   字段含义见 `my_devices.py` 里 `DEVICE_SPECS` 上方的注释；内置的大部分设备就是用这个格式写的。

2. **写一个适配器（改代码）**：协议需要多步握手、动态序号或特殊解析时（比如罗技 HID++、雷蛇），在 `my_devices.py` 里继承 `Adapter` 实现 `claim()` 和 `read()`，加到 `all_adapters()`，再在 `ci/test_protocol.py` 里用真机回复样本写一个离线测试。欢迎提交 PR。

## CPU / GPU 温度

- **GPU**：读的是 Windows 任务管理器同款数据，NVIDIA / AMD / Intel 独显都支持，不需要管理员权限。需要 Windows 10 2004 及以上和较新的显卡驱动；核显通常不提供温度。
- **CPU**：Windows 不给普通程序读 CPU 真实温度的接口，所以分两种情况：
  - 运行着 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)（需以管理员身份运行）时，读它的传感器，显示准确的封装 / 核心温度；
  - 否则退回系统的“热区”温度，数字前带 **≈** 表示近似。笔记本上大多可用；很多台式机读不到（显示 —）或是固定值。



- Windows 10/11 x64
- Python 3.10+（需包含 tkinter）
- 抓包需要 [USBPcap](https://desowin.org/usbpcap/) 和管理员权限

## 运行

```bat
python my_devices.py            :: 托盘常驻
python my_devices.py --once     :: 读一次并打印
python my_devices.py --list     :: 列出 HID 接口
python my_devices.py --panel    :: 只开面板，不常驻托盘
python my_devices.py --install  :: 创建开机自启 + 桌面入口
python my_devices.py --import 文件.json  :: 导入协议
python my_devices.py --export 文件.json  :: 导出协议
```

配置文件：`%APPDATA%\BatteryTray\config.json`

里面保存：

- `devices`：已添加的设备（VID、PID、名称）
- `protocols`：标准 HID、抓包或导入的协议
- `specs`：导入的设备规格
- `dismissed`：删掉后不再自动添加的设备
- `last`：每台设备上次成功读到的电量
- `interval`：刷新间隔
- `usbpcap`：USBPcapCMD 路径

## 高级：自己抓包（可选）

大多数人用不到：先看上面「想让软件支持你的设备」。下面是给愿意折腾的人准备的。

1. 安装 USBPcap，重启电脑
2. 点面板右上角的「+」，把要抓包的设备加进面板
3. **关闭厂家驱动**，点「开始抓包」并允许管理员权限
4. 打开厂家驱动，让它显示一次电量，记下数值
5. 点「停止」，填入刚才的电量
6. 只有一个字节符合：保存请求帧和电量字节位置
7. 多个字节符合：换一个不同电量再抓一次，对比两次字节差值
8. 保存后立即回读验证：
   - 主动回读失败时，会尝试被动监听输入报告；
   - 仍然失败会删除刚保存的协议，并提示「抓包中不含电量数据，可能是没按要求让厂商软件发电量请求」

提示：

- 没有匹配：注意抓包期间厂家驱动必须显示过电量；
- 匹配不准确：换一个相差 10% 以上的电量再抓；
- 多次没有结果：可能是厂家协议加密，无法通过抓包获取。

抓包分析会排除描述符、EP0 标准控制 setup、字符串描述符和全零报告；HID Feature Report 和厂商自定义报告会保留。

抓包保存的协议会记住请求和回复的形态，回读时按同样方式回放：

- 请求可以是中断 OUT、`SET_REPORT(Output)` 或 `SET_REPORT(Feature)`，回复可以是输入报告或 `GET_REPORT(Feature)`；
- 回复要和抓包时的报告特征（电量字节之前的前两个字节）一致才采信，不会把别的报告里的数字当成电量；
- 设备不使用报告 ID 时，自动按 Windows 缓冲区补齐首字节，偏移也随之修正。

旧版本保存的协议仍按原方式读取；如果读数不准，删除设备后重新抓一次即可升级为新格式。

## 限制

- 电量由设备上报，可能是 5% 步进
- 加密或私有握手协议无法通过抓包读取
- 仅支持 Windows 10/11 x64

## 文件

- `my_devices.py`：主程序（抓包助手已内置）
- `app.ico`：图标
- `启动.bat`：启动脚本（自动寻找 `pythonw` / `pyw`）
- `docs/panel.png`：界面截图
- `tools/make_device_art.py`：生成内嵌的设备图标（开发用）
- `ci/`：协议、蓝牙、托盘、温度、界面测试，在 GitHub Actions 的 Windows 机器上运行
- `.github/workflows/build.yml`：测试并用 PyInstaller 打包 `2.4G.exe`

## 致谢

衷心感谢 **[Ap0LoG1zE](https://github.com/Ap0LoG1zE)** 和原项目 **[EZ2.4G](https://github.com/Ap0LoG1zE/EZ2.4G)**。

这个工具最难的部分都是原作者解决的：

- 只用 `ctypes` 直接调用 Windows 的 SetupAPI / HID 接口，做到**零第三方依赖**、一个文件就能跑；
- 「**抓包学习协议**」的思路：用 USBPcap 抓下厂商驱动查询电量的过程，用已知电量去匹配字节，一次不够就换个电量再抓、比较两次的差值——不用逆向厂商协议，普通用户也能让自己的设备显示电量；
- 「看一眼网页版驱动的 WebHID 请求就能拿到协议」这个实用的经验；
- 托盘里每台设备一个电量圆环，以及以管理员权限运行抓包助手、读不到电量时沿用上次读数等细节。

本仓库在此基础上做的改动：

- Windows 11 Fluent 风格的界面与动效，托盘左键弹出面板、右键菜单；
- 显示蓝牙设备电量；
- 内置设备适配器、协议导入 / 导出和设备规格导入。方法与协议参考了这些项目，在此一并致谢：
  [Voltide](https://github.com/sdparklab/Voltide)（MIT，适配器架构与罗技 / 迈从 / ROG / 雷蛇 / ATK / IMECOO / XInput 协议）、
  [InphicBatteryTray](https://github.com/Tan-TanZi/InphicBatteryTray)（MIT，英菲克 IN10 / IN9）、
  [FerrisPulse](https://github.com/Ferris-echo/FerrisPulse)（GPL-3.0，只参考了协议文档和 FDAS 规格思路，未复制代码）、
  [VigilWatt](https://github.com/evren-oct/vigil-watt) 与 [HaloBattery](https://github.com/HeyOkay/HaloBattery)（MIT，安全规则与交叉核对）、
  [2.4G-battery-viewer](https://github.com/yszyszysz233/2.4G-battery-viewer)（ATK 64 字节协议、DARKRIM 协议）；
- 抓包协议记录请求/回复形态，支持 Feature 报告和不带报告 ID 的设备，回复按特征过滤；
- 修复若干问题（标准 HID 电量读取、充满通知正文、`--once`、启动脚本路径等），清理不再使用的旧代码；
- 加入测试与自动打包。

设备图标来自微软 [Fluent Emoji](https://github.com/microsoft/fluentui-emoji)（MIT 许可，© Microsoft Corporation）。

原项目没有声明开源许可证，原有代码的版权归原作者所有。如果原作者对本仓库的公开方式有任何意见，请通过 Issue 联系，我会第一时间配合调整或下架。
