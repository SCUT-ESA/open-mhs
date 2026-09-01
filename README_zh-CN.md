# Open MHS 🦾

[English](README.md) | [简体中文](README_zh-CN.md)

[![PyPI](https://img.shields.io/pypi/v/openmhs)](https://pypi.org/project/openmhs)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Stars](https://img.shields.io/github/stars/SCUT-ESA/open-mhs)](https://github.com/SCUT-ESA/open-mhs)

> **Open Model Hardware Standard (MHS)** —— 面向 AI Agent 物理硬件控制与仿真驱动的开源标准实现，灵感源自 [Anthropic Model Hardware Standard](https://www.anthropic.com/news/model-hardware-standard-research-preview)。

Open MHS 让各类 AI Agent（如 Claude、GPT、Cursor、Cline 等）能够通过统一的协议**发现、监控并安全控制真实的物理硬件设备与确定性仿真模型**。可以将其理解为**物理硬件世界的 MCP（Model Context Protocol）**。

---

## 什么是 MHS？

[Anthropic 的 MHS](https://www.anthropic.com/news/model-hardware-standard-research-preview) 是一套用于让 AI Agent 控制实验室仪器、机器人和制造设备的标准。**Open MHS** 提供了安全第一、失败关闭（Fail-Closed）的开源实现。

### 核心特性

- **统一的设备接口**：一套抽象协议连接并操作所有传感器、仪器与执行器
- **安全第一与失败关闭（Fail-Closed）**：内置参数范围校验、非有限数值拦截、安全边界检查与默认安全回退
- **模型与 Agent 无关**：无缝兼容 Claude Code、Claude Desktop、Cursor、Cline、LangChain 及自定义 Agent
- **原生 MCP 支持**：将扫描到的真实硬件动态暴露为标准 MCP Tools（支持 stdio 与 Streamable HTTP `/mcp`）
- **多传输与网络安全**：支持 CLI 命令行、REST API 与标准 MCP Server；默认仅监听回环地址，非回环访问强制 Bearer Token 与 TLS 反向代理防护
- **广泛的硬件与仿真适配**：涵盖已验证的 VISA 实验室仪器、实验性嵌入式 I/O 与确定性仿真适配器

---

## 支持的硬件设备与适配器状态

| 分类 | 设备 / 模块 | 后端支持状态 |
|---|---|---|
| **实验室仪器** | 示波器（UNI-T UPO6102N，基于 PyVISA/USB）、波形发生器（UNI-T UTG2062X） | ✅ 已验证（真实硬件驱动） |
| **视觉与摄像头** | USB 摄像头、IP 网络摄像头（基于 OpenCV） | 🔬 实验性真实驱动 & 仿真 |
| **嵌入式与主控** | 树莓派 GPIO（基于 gpiozero）、Arduino 串口通信（协议握手验证） | 🔬 实验性真实驱动 & 仿真 |
| **传感器** | 温湿度（BME280）、超声波测距（HC-SR04）、通用模拟量 | 🧪 确定性仿真 / 模型适配器 |
| **智能家居** | MQTT 设备、智能插座 | 🧪 确定性仿真 / 模型适配器 |
| **机器人与制造** | 六自由度机械臂、3D 打印机（FDM）、数码显微镜、移液机器人、激光器 | 🧪 确定性仿真 / 模型适配器 |

---

## 🚀 最小运行实战指南

本指南将带你在 5 分钟内完成一次从零到一的**最小运行**：安装依赖、连接硬件（以示波器为例）、启动 MCP 服务，并使用 AI Agent 通过自然语言完成一次测量。

### 第一步：下载依赖

推荐使用现代 Python 包管理器 [`uv`](https://docs.astral.sh/uv/)（速度极快），或使用常规 `pip`：

```bash
# 1. 克隆代码仓库
git clone https://github.com/SCUT-ESA/open-mhs.git
cd open-mhs

# 2. 安装依赖（包含 MCP、VISA 与 REST API 支持）
# 方式 A：使用 uv（推荐）
uv sync --extra mcp --extra visa --extra api

# 方式 B：使用 pip
pip install -e ".[mcp,visa,api]"
```

> **注意（VISA 驱动环境）**：操作真实示波器或仪器时，请确保系统已安装 VISA 运行时（如 NI-VISA，或安装 `pip install pyvisa-py`）。

VISA 自动发现支持 UNI-T UPO6102N 示波器，以及 UTG2000X 系列中的 UNI-T UTG2062X 波形发生器。发现和连接阶段只发送 `*IDN?`，不会启用输出，也不会改变波形或幅度。规范设备 ID 为确定性哈希格式（例如 `scope-<hash>` / `wavegen-<hash>`）。

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
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Device ID          ┃ Type         ┃ Manufacturer ┃ State   ┃ Capabilities                   ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ scope-a1b2c3d4e5f6 │ oscilloscope │ UNI-T        │ ONLINE  │ identify, measure_vpp, run, ...│
└────────────────────┴──────────────┴──────────────┴─────────┴────────────────────────────────┘
```

可以直接通过 CLI 进行一次读数测试：
```bash
uv run mhs read scope-a1b2c3d4e5f6 measure_vpp channel=1
```

如无物理硬件，可使用 `--simulation` 参数测试仿真适配器：
```bash
uv run mhs discover --simulation
uv run mhs read --simulation sensor_001 temperature
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
      "args": ["--directory", "<path/to/open-mhs>", "run", "mhs", "serve", "--mode", "stdio"]
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
1. **设备发现**：Agent 调用 `mhs_discover_devices` 工具，获取当前在线设备 `scope-a1b2c3d4e5f6`（UNI-T UPO6102N）。
2. **工具调用**：Agent 自动调用 `mhs_scope-a1b2c3d4e5f6_measure_vpp(channel=1)` 发送测量指令。
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
    ok = await driver.connect()
    if not ok or driver.device is None:
        print("连接示波器失败。")
        return

    try:
        # 读取通道 1 的峰峰值电压
        result = await driver.device.read("measure_vpp", channel=1)
        print(f"峰峰值电压: {result['value']} {result['unit']}")
    finally:
        await driver.disconnect()

asyncio.run(main())
```

### 2. CLI 命令行操作

```bash
# 注册演示设备
mhs demo

# 发现硬件设备
mhs discover

# 发现并包含仿真模型设备
mhs discover --simulation

# 从设备读取数据
mhs read --simulation sensor_001 temperature

# 向设备写入控制指令
mhs write --simulation arm_001 cartesian_position x=250 y=100 z=300 speed=80

# 检查所有设备健康状态
mhs status
```

### 3. REST API 服务

```bash
# 启动 REST API 服务（默认绑定 127.0.0.1:8000）
mhs api --port 8000

# 查询设备列表
curl http://127.0.0.1:8000/devices

# 读取设备能力
curl -X POST http://127.0.0.1:8000/devices/scope-a1b2c3d4e5f6/read/measure_vpp \
  -H "Content-Type: application/json" \
  -d "{\"params\": {\"channel\": 1}}"
```

> **安全说明**：绑定非回环地址（如 `--host 0.0.0.0`）必须配置 `OPENMHS_BEARER_TOKEN`，并通过 TLS 反向代理（`OPENMHS_BEHIND_TLS_PROXY=1`）或显式允许非安全 HTTP（`OPENMHS_ALLOW_INSECURE_HTTP=1`）。

### 4. 独立 MCP 服务

```bash
# Stdio 模式（用于 Agent CLI、Claude Desktop）
mhs serve --mode stdio

# 标准 Streamable HTTP 模式（/mcp 端点）
mhs serve --mode http --host 127.0.0.1 --port 8080
```

---

## 编写自定义硬件驱动

```python
from openmhs.core.backend import BackendDevice, BackendPolicy
from openmhs.core.device import DeviceCapability, DeviceMetadata
from openmhs.core.driver import Driver, DriverConfig, register_driver

class MyCustomDevice(BackendDevice):
    def __init__(self, device_id: str, *, simulation: bool = False):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="my_custom_device",
            manufacturer="Custom",
            model="Model 1",
            capabilities=[
                DeviceCapability(name="status", description="Get status", read_only=True),
                DeviceCapability(name="set_value", description="Set value", read_only=False),
            ],
        )
        super().__init__(metadata, BackendPolicy(simulation=simulation, backend_name="custom"))

    async def _do_read(self, capability: str, **params):
        if capability == "status":
            return {"status": "ok", "value": 42}
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params):
        if capability == "set_value":
            return {"result": "success", "params": params}
        return {"error": f"Unknown capability: {capability}"}

@register_driver
class MyCustomDriver(Driver):
    DRIVER_NAME = "my_custom_device"
    SUPPORTED_DEVICES = ["my_custom_device"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "my_001")
        candidate = MyCustomDevice(device_id, simulation=self.simulation)
        return await self._connect_candidate(candidate)
```

---

## 项目状态与路线图

本项目目前处于 **Alpha** 阶段，提供严格的类型边界、失败关闭安全设计与标准 MCP/REST/CLI 接入。

- [x] 核心协议与动态设备注册表
- [x] 驱动生命周期管理、参数与安全边界校验
- [x] 原生 MCP Server 服务（stdio / Streamable HTTP `/mcp`）
- [x] CLI 命令行与 REST API 服务（支持回环默认与 Token 安全）
- [x] 示波器适配器（UNI-T UPO6102N，PyVISA 驱动、只读连接保护与型号精确匹配）
- [x] 波形发生器适配器（UNI-T UTG2062X，PyVISA 驱动与会话保护）
- [x] 摄像头、GPIO、Arduino、传感器、智能家居、机械臂、3D打印机适配器（支持显式仿真与可注入后端）
- [ ] 真实 I2C / SPI 总线自动扫描支持
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
