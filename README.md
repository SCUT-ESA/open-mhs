# Open MHS 🦾

[English](README.md) | [简体中文](README_zh-CN.md)

[![PyPI](https://img.shields.io/pypi/v/openmhs)](https://pypi.org/project/openmhs)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Stars](https://img.shields.io/github/stars/SCUT-ESA/open-mhs)](https://github.com/SCUT-ESA/open-mhs)

> **Open Model Hardware Standard (MHS)** — An open-source implementation of AI agent hardware control, inspired by [Anthropic's Model Hardware Standard](https://www.anthropic.com/news/model-hardware-standard-research-preview).

MHS enables AI agents to **discover, monitor, and safely operate physical and simulated devices** through a unified protocol. Think of it as **MCP for the physical world**.

---

## What is MHS?

[Anthropic's MHS](https://www.anthropic.com/news/model-hardware-standard-research-preview) is a research preview standard that lets AI agents control lab equipment, robots, and manufacturing devices. **Open MHS** provides a secure, fail-closed open-source implementation.

### Key Features

- **Unified Device Interface** — Single protocol for all hardware
- **Safety-First & Fail-Closed** — Built-in parameter limits, finite number validation, and safe defaults (non-operational when unverified)
- **Model & Agent Agnostic** — Works with Claude, GPT, Cursor, Cline, LangChain, or custom agents
- **MCP Native** — Dynamically exposes connected hardware as standard MCP tools (stdio & Streamable HTTP `/mcp`)
- **Multi-Transport & Secure** — CLI, REST API, and standard MCP server with loopback-by-default and Bearer token security for remote bindings
- **Broad Hardware & Simulation Support** — Verified VISA lab instruments, experimental embedded I/O, and deterministic simulation adapters

---

## Supported Hardware & Adapters

| Category | Devices / Models | Backend Status |
|---|---|---|
| **Lab Instruments** | Oscilloscopes (UNI-T UPO6102N via PyVISA/USB), Waveform Generators (UNI-T UTG2062X) | ✅ Verified (Real Hardware) |
| **Vision** | USB Webcam, IP Camera (via OpenCV) | 🔬 Experimental Real & Simulation |
| **Embedded & I/O** | Raspberry Pi GPIO (via gpiozero), Arduino Serial (Verified Protocol Handshake) | 🔬 Experimental Real & Simulation |
| **Sensors** | Temperature/Humidity (BME280), Distance (HC-SR04), Generic Analog | 🧪 Simulation / Model Adapter |
| **Smart Home** | MQTT Devices, Smart Plugs | 🧪 Simulation / Model Adapter |
| **Robotics & Lab** | 6-DoF Robot Arm, 3D Printer (FDM), Digital Microscope, Pipetting Robot, Laser | 🧪 Simulation / Model Adapter |

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
uv sync --extra mcp --extra visa --extra api

# Option B: With pip
pip install -e ".[mcp,visa,api]"
```

> **Note for VISA Instruments**: Ensure you have NI-VISA or a compatible VISA backend installed (or `pip install pyvisa-py`).

VISA discovery supports UNI-T UPO6102N oscilloscopes and UNI-T UTG2062X waveform generators. Discovery and connection send only read-only `*IDN?` queries; they never enable output or change waveform parameters. Canonical device IDs are deterministic hashes (`scope-<hash>` / `wavegen-<hash>`).

---

### Step 2: Connect Hardware & Verify with CLI

Connect your instrument (e.g., UNI-T UPO6102N) to your computer via USB.

Run device discovery to verify the connection:

```bash
# Using uv
uv run mhs discover

# Or if openmhs is installed in your active environment
mhs discover
```

You will see detected hardware in the output:
```text
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Device ID          ┃ Type         ┃ Manufacturer ┃ State   ┃ Capabilities                   ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
┃ scope-a1b2c3d4e5f6 ┃ oscilloscope ┃ UNI-T        ┃ ONLINE  ┃ identify, measure_vpp, run, ...┃
└────────────────────┴──────────────┴──────────────┴─────────┴────────────────────────────────┘
```

You can test a manual read directly via CLI:
```bash
uv run mhs read scope-a1b2c3d4e5f6 measure_vpp channel=1
```

For simulated demo devices without physical hardware:
```bash
uv run mhs discover --simulation
uv run mhs read --simulation sensor_001 temperature
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
> **User**: *"Check connected instruments, identify the oscilloscope, and measure the peak-to-peak voltage on channel 1."*

#### Agent Flow:
1. **Agent Tool Discovery**: The agent calls `mhs_discover_devices` and finds `scope-a1b2c3d4e5f6` (UNI-T UPO6102N).
2. **Tool Execution**: The agent automatically invokes `mhs_scope-a1b2c3d4e5f6_measure_vpp(channel=1)`.
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
    ok = await driver.connect()
    if not ok or driver.device is None:
        print("Failed to connect to oscilloscope.")
        return

    try:
        # Measure peak-to-peak voltage on channel 1
        result = await driver.device.read("measure_vpp", channel=1)
        print(f"Vpp: {result['value']} {result['unit']}")
    finally:
        await driver.disconnect()

asyncio.run(main())
```

### 2. CLI Usage

```bash
# Setup demo devices for testing
mhs demo

# Discover hardware
mhs discover

# Discover with simulation adapters included
mhs discover --simulation

# Read and write
mhs read --simulation sensor_001 temperature
mhs write --simulation arm_001 cartesian_position x=250 y=100 z=300 speed=80

# Check health
mhs status
```

### 3. REST API

```bash
# Start the REST API server (binds to 127.0.0.1:8000 by default)
mhs api --port 8000

# List registered devices
curl http://127.0.0.1:8000/devices

# Read from a device
curl -X POST http://127.0.0.1:8000/devices/scope-a1b2c3d4e5f6/read/measure_vpp \
  -H "Content-Type: application/json" \
  -d "{\"params\": {\"channel\": 1}}"
```

> **Security Note**: Non-loopback bindings (e.g. `--host 0.0.0.0`) require `OPENMHS_BEARER_TOKEN` and running behind a TLS reverse proxy (`OPENMHS_BEHIND_TLS_PROXY=1`) or explicit insecure override (`OPENMHS_ALLOW_INSECURE_HTTP=1`).

### 4. Standalone MCP Server

```bash
# Stdio mode (for AI Agent CLI / Claude Desktop)
mhs serve --mode stdio

# Standard Streamable HTTP mode (/mcp endpoint)
mhs serve --mode http --host 127.0.0.1 --port 8080
```

---

## Writing a Custom Driver

```python
from openmhs.core.backend import BackendDevice, BackendPolicy
from openmhs.core.device import DeviceCapability, DeviceMetadata, DeviceState
from openmhs.core.driver import Driver, DriverConfig, register_driver

class MyDevice(BackendDevice):
    def __init__(self, device_id: str, *, simulation: bool = False):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="custom_device",
            manufacturer="Custom",
            model="Model X",
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
class MyDriver(Driver):
    DRIVER_NAME = "my_device"
    SUPPORTED_DEVICES = ["custom_device"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "my_001")
        candidate = MyDevice(device_id, simulation=self.simulation)
        return await self._connect_candidate(candidate)
```

---

## License

MIT License — see [LICENSE](LICENSE) file.

---

## Acknowledgements

- Inspired by [Anthropic's Model Hardware Standard](https://www.anthropic.com/news/model-hardware-standard-research-preview) research preview and the [Model Context Protocol](https://modelcontextprotocol.io/).
- This project builds upon and utilizes source code from [tongriyaotxt/open-mhs](https://github.com/tongriyaotxt/open-mhs).
