# Open MHS 🦾

[English](README.md) | [简体中文](README_zh-CN.md)

[![PyPI](https://img.shields.io/pypi/v/openmhs)](https://pypi.org/project/openmhs)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Stars](https://img.shields.io/github/stars/SCUT-ESA/open-mhs)](https://github.com/SCUT-ESA/open-mhs)

> **Open Model Hardware Standard (MHS)** —— 面向 AI Agent 物理硬件控制的开源标准实现，灵感源自 [Anthropic Model Hardware Standard](https://www.anthropic.com/news/model-hardware-standard-research-preview)。

Open MHS 让各类 AI Agent（如 Claude、GPT、Cursor、Cline 等）能够通过统一的协议**发现、监控并安全控制真实的物理硬件设备**。可以将其理解为**物理硬件世界的 MCP（Model Context Protocol）**。

---

## 什么是 MHS？

[Anthropic 的 MHS](https://www.anthropic.com/news/model-hardware-standard-research-preview) 是一套用于让 AI Agent 控制实验室仪器、机器人和制造设备的标准。**Open MHS** 是其开源实现，致力于将这一能力普及给所有开发者与研究者。

### 核心特性

- **统一的设备接口**：一套抽象协议连接并操作所有传感器、仪器与执行器
- **安全第一的设计**：内置参数范围校验、安全边界检查与只读/写入权限隔离
- **模型与 Agent 无关**：无缝兼容 Claude Code、Claude Desktop、Cursor、Cline、LangChain 及自定义 Agent
- **原生 MCP 支持**：将扫描到的真实硬件动态暴露为标准 MCP Tools
- **多传输方式**：支持 CLI 命令行、REST API 与 MCP Server（stdio / HTTP）
- **广泛的硬件适配**：涵盖示波器、传感器、机械臂、摄像头到 3D 打印机等

---

## 支持的硬件设备

| 分类 | 设备 / 模块 | 状态 |
|---|---|---|
| **实验室仪器** | 示波器（UNI-T UPO6102N，基于 PyVISA/USB）、波形发生器（UNI-T UTG2062X）、显微镜、激光发生器、移液机 | ✅ 就绪 |
| **传感器** | 温湿度（BME280）、超声波测距（HC-SR04）、光照、气体、IMU | ✅ 就绪 |
| **视觉与摄像头** | USB 摄像头、IP 网络摄像头、树莓派摄像头（Pi Camera） | ✅ 就绪 |
| **嵌入式与主控** | 树莓派 GPIO、Arduino（串口通信） | ✅ 就绪 |
| **智能家居** | MQTT 设备、智能插座、智能灯泡 | ✅ 就绪 |
| **机器人与制造** | 六自由度机械臂、3D 打印机（G-code） | ✅ 就绪 |

---

## 🚀 最小运行实战指南

本指南将带你在 5 分钟内完成一次从零到一的**最小运行**：安装依赖、连接硬件（以示波器为例）、启动 MCP 服务，并使用 AI Agent 通过自然语言完成一次测量。

### 第一步：下载依赖

推荐使用现代 Python 包管理器 [`uv`](https://docs.astral.sh/uv/)（速度极快），或使用常规 `pip`：

```bash
# 1. 克隆代码仓库
git clone https://github.com/SCUT-ESA/open-mhs.git
cd open-mhs

# 2. 安装依赖（包含 MCP 与示波器支持）
# 方式 A：使用 uv（推荐）
uv sync --extra mcp --extra visa

# 方式 B：使用 pip
pip install -e ".[mcp,visa]"

# 旧的示波器 extra 仍然兼容：
# uv sync --extra mcp --extra oscilloscope
# pip install -e ".[mcp,oscilloscope]"
# 也可以只安装波形发生器支持：
# uv sync --extra waveform_generator
# pip install -e ".[waveform_generator]"
```

> **注意（VISA 驱动环境）**：操作真实示波器或仪器时，请确保系统已安装 VISA 运行时（如 NI-VISA，或安装 `pip install pyvisa-py`）。

VISA 自动发现支持 UNI-T UPO6102N 示波器，以及 UTG2000X 系列中的 UNI-T UTG2062X 波形发生器。发现和连接阶段只发送 `*IDN?`，不会启用输出，也不会改变波形或幅度。发生器提供 `identify`、`output_state`、`output`、`waveform` 和 `amplitude` 能力；通道参数必须是整数 1 或 2，写工具必须明确提供 `channel`，读工具省略时默认通道 1。示波器也提供同样通道规则的 `measure_frequency`。`amplitude` 使用仪器当前配置的电压单位，允许范围取决于负载和仪器配置。动态工具名格式为 `mhs_<device_id>_<capability>`（例如 `mhs_wavegen-<hash>_output`）。

真实 USB 测试默认关闭且只读：只能枚举资源、查询 `*IDN?` 和执行只读查询。不要把 `examples/oscilloscope.py` 中包含写操作的示例当作 UTG 安全验证流程。

---

### 第二步：连接硬件与命令行验证

将示波器（如 **UNI-T UPO6102N**）通过 USB 数据线连接到电脑。

运行设备扫描以验证系统已正确识别硬件：

```bash
# 使用 uv 运行
uv run mhs discover

# 或者直接运行 mhs（如果在激活的虚拟环境中）
mhs discover
```

终端将打印已发现的设备表格：
```text
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Device ID          ┃ Type         ┃ Manufacturer ┃ State ┃ Capabilities                   ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ scope-d1dbfccf30e6 │ oscilloscope │ UNI-T        │ ONLINE│ identify, measure_vpp, run, ...│
└────────────────────┴──────────────┴──────────────┴───────┴────────────────────────────────┘
```

可以直接通过 CLI 进行一次读数测试：
```bash
uv run mhs read scope-d1dbfccf30e6 measure_vpp channel=1
# 预期输出：{"channel": 1, "value": 0.02, "unit": "V"}
```

---

### 第三步：配置与运行 MCP Server

Open MHS 会自动将已连接的硬件包装为标准 MCP 工具并对外提供服务。

#### 场景 1：在 Claude Code / Cursor / Cline 中使用 (`.mcp.json`)

在项目根目录下创建或编辑 `.mcp.json`：

```json
{
  "mcpServers": {
    "openmhs": {
      "command": "uv",
      "args": ["run", "mhs", "serve", "--mode", "stdio"]
    }
  }
}
```

#### 场景 2：在 Claude Desktop 中使用

编辑 `claude_desktop_config.json` 配置文件：
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "openmhs": {
      "command": "uv",
      "args": ["--directory", "E:/code/open-mhs", "run", "mhs", "serve", "--mode", "stdio"]
    }
  }
}
```

---

### 第四步：使用 AI Agent 完成一次对话交互

配置完成后，启动你喜爱的 AI Agent 工具（例如在项目目录直接运行 `claude`，或在 Cursor / Claude Desktop 中新建对话），发起自然语言交互：

#### 交互示例：
> **用户**：*"帮我通过示波器测量一下通道 1 的峰峰值电压。"*

#### Agent 执行流程：
1. **设备发现**：Agent 调用 `mhs_discover_devices` 工具，获取当前在线设备 `scope-d1dbfccf30e6`（UNI-T UPO6102N）。
2. **工具调用**：Agent 自动调用 `mhs_scope-d1dbfccf30e6_measure_vpp(channel=1)` 发送测量指令。
3. **Agent 输出结果**：
   > *"已通过示波器（UNI-T UPO6102N）完成测量，通道 1 当前的峰峰值电压（$V_{pp}$）为 **0.02 V**（即 **20 mV**）。"*

---

## 常用操作与开发指南

### 1. Python 编程式调用

```python
import asyncio
from openmhs.adapters.oscilloscope import OscilloscopeDriver
from openmhs.core.driver import DriverConfig

async def main():
    config = DriverConfig(
        driver_name="oscilloscope",
        connection_params={
            "device_id": "scope_001",
            "visa_resource": "USB0::0x5656::0x0832::APXA326100149::INSTR",
        },
    )
    driver = OscilloscopeDriver(config)
    await driver.connect()

    if driver.device:
        # 读取通道 1 的峰峰值电压
        result = await driver.device.read("measure_vpp", channel=1)
        print(f"峰峰值电压: {result['value']} {result['unit']}")

    await driver.disconnect()

asyncio.run(main())
```

### 2. CLI 命令行操作

```bash
# 生成模拟演示设备（无需真实硬件即可测试）
mhs demo

# 发现并列出所有设备
mhs discover

# 从设备读取数据
mhs read sensor_001 temperature
mhs read scope-d1dbfccf30e6 measure_vpp channel=1

# 向设备写入控制指令
mhs write arm_001 cartesian_position x=250 y=100 z=300 speed=80
mhs write scope-d1dbfccf30e6 run

# 检查所有设备健康状态
mhs status
```

### 3. REST API 服务

```bash
# 启动 REST API 服务（默认端口 8000）
mhs api --port 8000

# 查询设备列表
curl http://localhost:8000/devices

# 读取设备能力
curl -X POST http://localhost:8000/devices/scope-d1dbfccf30e6/read/measure_vpp \
  -H "Content-Type: application/json" \
  -d '{"channel": 1}'
```

### 4. 独立 MCP 服务

```bash
# Stdio 模式（用于 Agent CLI、Claude Desktop）
mhs serve --mode stdio

# HTTP 模式
mhs serve --mode http
```

---

## 系统架构

```text
┌─────────────────────────────────────────────────────────────┐
│          AI Agent（Claude Code、Cursor、GPT、Cline 等）       │
└──────────────────────────────┬──────────────────────────────┘
                               │ MCP / CLI / REST API
┌──────────────────────────────▼──────────────────────────────┐
│                  Open MHS 统一协议层                         │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐   │
│  │  Read   │  │  Write   │  │ Discover │  │ Health Check│   │
│  │ (读取)  │  │ (控制)   │  │ (发现)   │  │  (健康检查) │   │
│  └─────────┘  └──────────┘  └──────────┘  └─────────────┘   │
└──────────────────────────────┬──────────────────────────────┘
                               │ 统一驱动接口（Driver Interface）
┌──────────────────────────────▼──────────────────────────────┐
│                      硬件适配器层                            │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────┐ │
│  │ 示波器/仪器  │ │  各类传感器  │ │  机械臂/执行 │ │ ... │ │
│  │ (UNI-T VISA) │ │ (BME280/Temp)│ │ (6-DoF/UART) │ │     │ │
│  └──────────────┘ └──────────────┘ └──────────────┘ └─────┘ │
└──────────────────────────────┬──────────────────────────────┘
                               │ 物理传输协议 (USB/VISA/I2C/SPI/GPIO)
┌──────────────────────────────▼──────────────────────────────┐
│                      物理硬件设备                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 编写自定义硬件驱动

只需继承 `BaseDevice` 与 `Driver` 并使用 `@register_driver` 装饰器：

```python
from openmhs.core.device import BaseDevice, DeviceCapability, DeviceMetadata, DeviceState
from openmhs.core.driver import Driver, DriverConfig, register_driver

class MyCustomDevice(BaseDevice):
    async def connect(self) -> bool:
        self._set_state(DeviceState.ONLINE)
        return True

    async def _do_read(self, capability: str, **params):
        if capability == "status":
            return {"status": "ok", "value": 42}
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params):
        return {"result": "success", "params": params}

@register_driver
class MyCustomDriver(Driver):
    DRIVER_NAME = "my_custom_device"
    SUPPORTED_DEVICES = ["my_custom_device"]

    async def connect(self) -> bool:
        self._device = MyCustomDevice(...)
        return True
```

---

## 项目状态与路线图

本项目目前处于 **Alpha** 阶段，正在积极迭代并持续扩充真实硬件驱动支持。

- [x] 核心协议与动态设备注册表
- [x] 驱动生命周期管理与安全边界校验
- [x] MCP Server 服务集成（stdio / HTTP）与动态热发现
- [x] CLI 命令行与 REST API 服务
- [x] 示波器适配器（UNI-T UPO6102N，PyVISA 驱动与会话保护）
- [x] 传感器适配器（BME280、HC-SR04 超声波、模拟量传感器等）
- [x] 机械臂、摄像头、3D 打印机、GPIO、Arduino、MQTT 适配器
- [ ] 真实 I2C / SPI 总线自动扫描支持
- [ ] 物理设备数字孪生与仿真环境
- [ ] Web 控制台与实时遥测波形看板
- [ ] 多 Agent 协同实验室自动化工作流

---

## 参与贡献

非常欢迎提交 Issue、PR 以及新增硬件驱动支持！主要贡献方向：
- 新增硬件适配器（万用表、信号发生器、可编程电源、电动位移台等）
- 真实硬件的测试与兼容性反馈
- 丰富 Agent 自动化集成用例与文档

---

## 开源协议

基于 [MIT License](LICENSE) 开源。

---

## 致谢

- 灵感来自 [Anthropic Model Hardware Standard](https://www.anthropic.com/news/model-hardware-standard-research-preview) 与 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)。
- 本项目基于并使用了 [tongriyaotxt/open-mhs](https://github.com/tongriyaotxt/open-mhs) 的开源代码。
