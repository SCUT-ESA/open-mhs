# Open MHS 🦾

[English](README.md) | [简体中文](README_zh-CN.md)

[![PyPI](https://img.shields.io/pypi/v/openmhs)](https://pypi.org/project/openmhs)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Stars](https://img.shields.io/github/stars/SCUT-ESA/open-mhs)](https://github.com/SCUT-ESA/open-mhs)

> **Open Model Hardware Standard (MHS)** — An open-source implementation of AI agent hardware control, inspired by [Anthropic's Model Hardware Standard](https://www.anthropic.com/news/model-hardware-standard-research-preview).

MHS enables AI agents to **discover, monitor, and safely operate physical devices** through a unified protocol. Think of it as **MCP for the physical world**.

---

## What is MHS?

[Anthropic's MHS](https://www.anthropic.com/news/model-hardware-standard-research-preview) is a research preview standard that lets AI agents control lab equipment, robots, and manufacturing devices. **Open MHS** is an open-source implementation, making this capability accessible to everyone.

### Key Features

- **Unified Device Interface** — Single protocol for all hardware
- **Safety-First Design** — Built-in limits, validation, and safe defaults
- **Model & Agent Agnostic** — Works with Claude, GPT, Cursor, Cline, LangChain, or custom agents
- **MCP Native** — Dynamically exposes connected hardware as standard MCP tools
- **Multi-Transport** — CLI, REST API, and MCP server (stdio / HTTP)
- **Broad Hardware Support** — From sensors and oscilloscopes to robot arms

---

## Supported Hardware

| Category | Devices | Status |
|---|---|---|
| **Lab Equipment** | Oscilloscopes (UNI-T UPO6102N via VISA/USB), Microscopes, Lasers, Liquid Handlers | ✅ Ready |
| **Sensors** | Temperature, Humidity (BME280), Distance (HC-SR04), Light, Gas, IMU | ✅ Ready |
| **Cameras** | USB Webcam, IP Camera, Raspberry Pi Camera | ✅ Ready |
| **Embedded** | Raspberry Pi GPIO, Arduino (Serial) | ✅ Ready |
| **Smart Home** | MQTT devices, Smart Plugs, Lights | ✅ Ready |
| **Robotics** | Robot Arms (6-DoF), 3D Printers | ✅ Ready |

---

## Minimal End-to-End Walkthrough

Here is how to get a minimal end-to-end run working in under 5 minutes: install dependencies, connect hardware (e.g. an oscilloscope), run the MCP server, and execute a natural language task via an AI Agent.

### Step 1: Install Dependencies

We recommend using [`uv`](https://docs.astral.sh/uv/) for fast environment management, or standard `pip`:

```bash
# Clone the repository
git clone https://github.com/SCUT-ESA/open-mhs.git
cd open-mhs

# Option A: With uv (Recommended)
uv sync --extra mcp --extra oscilloscope

# Option B: With pip
pip install -e ".[mcp,oscilloscope]"
```

> **Note for VISA Instruments**: Ensure you have NI-VISA or a compatible VISA library installed (or `pip install pyvisa-py`).

---

### Step 2: Connect Hardware & Verify with CLI

Connect your oscilloscope (e.g., UNI-T UPO6102N) to your computer via USB.

Run device discovery to verify the connection:

```bash
# Using uv
uv run mhs discover

# Or if openmhs is installed in your active environment
mhs discover
```

You will see the detected hardware in the output:
```text
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Device ID          ┃ Type         ┃ Manufacturer ┃ State ┃ Capabilities                   ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ scope-d1dbfccf30e6 │ oscilloscope │ UNI-T        │ ONLINE│ identify, measure_vpp, run, ...│
└────────────────────┴──────────────┴──────────────┴───────┴────────────────────────────────┘
```

You can test a manual read directly via CLI:
```bash
uv run mhs read scope-d1dbfccf30e6 measure_vpp channel=1
# Output: {"channel": 1, "value": 0.02, "unit": "V"}
```

---

### Step 3: Configure the MCP Server

Open MHS exposes discovered devices as standard Model Context Protocol (MCP) tools dynamically.

#### For Claude Code / Cursor / Cline (`.mcp.json`)

Create or update `.mcp.json` in your workspace root:

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

#### For Claude Desktop

Add to your `claude_desktop_config.json`:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "openmhs": {
      "command": "uv",
      "args": ["--directory", "/path/to/open-mhs", "run", "mhs", "serve", "--mode", "stdio"]
    }
  }
}
```

---

### Step 4: Interact via AI Agents

Once the MCP server is configured, open your agent (Claude Code, Claude Desktop, Cursor, etc.) and complete a task with natural language:

#### Example Prompt:
> **User**: *"Help me check the oscilloscope identity and measure the peak-to-peak voltage ($V_{pp}$) on channel 1."*

#### Agent Flow:
1. **Agent Tool Discovery**: The agent calls `mcp_discover_devices` and finds `scope-d1dbfccf30e6` (UNI-T UPO6102N).
2. **Tool Execution**: The agent automatically invokes `mcp_scope-d1dbfccf30e6_measure_vpp(channel=1)`.
3. **Agent Response**:
   > *"I queried the oscilloscope (UNI-T UPO6102N). The peak-to-peak voltage measured on Channel 1 is **0.02 V** (20 mV)."*

---

## Standard Usage

### 1. Python API

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
        # Measure peak-to-peak voltage on channel 1
        result = await driver.device.read("measure_vpp", channel=1)
        print(f"Vpp: {result['value']} {result['unit']}")

    await driver.disconnect()

asyncio.run(main())
```

### 2. CLI Usage

```bash
# Setup demo devices for testing without physical hardware
mhs demo

# Discover connected hardware
mhs discover

# Read from a device
mhs read sensor_001 temperature
mhs read scope-d1dbfccf30e6 measure_vpp channel=1

# Write to a device
mhs write arm_001 cartesian_position x=250 y=100 z=300 speed=80
mhs write scope-d1dbfccf30e6 run

# Check system & device health
mhs status
```

### 3. REST API

```bash
# Start the REST API server
mhs api --port 8000

# List registered devices
curl http://localhost:8000/devices

# Read from a device
curl -X POST http://localhost:8000/devices/scope-d1dbfccf30e6/read/measure_vpp \
  -H "Content-Type: application/json" \
  -d '{"channel": 1}'
```

### 4. Standalone MCP Server

```bash
# Stdio mode (for AI Agent CLI / Claude Desktop)
mhs serve --mode stdio

# HTTP mode
mhs serve --mode http
```

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│             AI Agent (Claude Code, Cursor, GPT, etc.)       │
└──────────────────────────────┬──────────────────────────────┘
                               │ MCP / CLI / REST API
┌──────────────────────────────▼──────────────────────────────┐
│                  Open MHS Protocol Layer                    │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐   │
│  │  Read   │  │  Write   │  │ Discover │  │ Health Check│   │
│  └─────────┘  └──────────┘  └──────────┘  └─────────────┘   │
└──────────────────────────────┬──────────────────────────────┘
                               │ Unified Driver Interface
┌──────────────────────────────▼──────────────────────────────┐
│                    Hardware Adapters                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────┐ │
│  │Oscilloscopes │ │   Sensors    │ │ Robot Arms   │ │ ... │ │
│  │ (UNI-T VISA) │ │ (BME280/Temp)│ │ (6-DoF/UART) │ │     │ │
│  └──────────────┘ └──────────────┘ └──────────────┘ └─────┘ │
└──────────────────────────────┬──────────────────────────────┘
                               │ Physical Transport (USB/VISA/I2C/GPIO)
┌──────────────────────────────▼──────────────────────────────┐
│                      Physical Devices                       │
└─────────────────────────────────────────────────────────────┘
```

---

## Writing a Custom Driver

```python
from openmhs.core.device import BaseDevice, DeviceCapability, DeviceMetadata, DeviceState
from openmhs.core.driver import Driver, DriverConfig, register_driver

class MyDevice(BaseDevice):
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
class MyDriver(Driver):
    DRIVER_NAME = "my_device"
    SUPPORTED_DEVICES = ["my_device"]

    async def connect(self) -> bool:
        self._device = MyDevice(...)
        return True
```

---

## Project Status

This is an **alpha** implementation based on the Model Hardware Standard architecture. The project is actively maintained and expanding real hardware support.

### Roadmap

- [x] Core protocol implementation & dynamic registry
- [x] Device driver framework & safety boundaries
- [x] MCP server integration (stdio & HTTP) with auto-discovery
- [x] CLI & REST API tools
- [x] Oscilloscope adapter (UNI-T UPO6102N via PyVISA)
- [x] Sensor adapters (BME280, HC-SR04, analog)
- [x] Camera, Robot Arm, 3D Printer, GPIO, Arduino, MQTT adapters
- [ ] Real hardware I2C/SPI bus integration
- [ ] Device simulation environment
- [ ] Web dashboard & live telemetry
- [ ] Multi-agent collaborative lab workflows

---

## Contributing

Contributions are warmly welcomed! Areas where help is needed:
- New hardware drivers (multimeters, signal generators, power supplies, robotic stages)
- Physical hardware testing and validation
- Documentation, recipes, and agent integration examples

---

## License

MIT License — see [LICENSE](LICENSE) file.

---

## Acknowledgements

- Inspired by [Anthropic's Model Hardware Standard](https://www.anthropic.com/news/model-hardware-standard-research-preview) research preview and the [Model Context Protocol](https://modelcontextprotocol.io/).
- This project builds upon and utilizes source code from [tongriyaotxt/open-mhs](https://github.com/tongriyaotxt/open-mhs).
