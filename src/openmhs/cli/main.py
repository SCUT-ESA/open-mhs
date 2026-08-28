"""CLI tool for MHS.

Usage:
    mhs discover          - List all connected devices
    mhs read <device> <capability> [params...]
    mhs write <device> <capability> [params...]
    mhs status            - Check all device health
    mhs serve             - Start MCP server
    mhs api               - Start REST API server
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import List, Optional

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from openmhs.core.device import DeviceState
from openmhs.core.driver import DriverConfig, global_driver_registry
from openmhs.core.protocol import Command, CommandType, MHSProtocol
from openmhs.core.registry import DeviceRegistry

console = Console()


async def discover_devices(registry: DeviceRegistry) -> None:
    """Discover and list all devices."""
    summary = registry.get_metadata_summary()
    
    if not summary:
        console.print("[yellow]No devices registered.[/yellow]")
        return

    table = Table(title="MHS Devices")
    table.add_column("Device ID", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Manufacturer", style="blue")
    table.add_column("State", style="yellow")
    table.add_column("Capabilities", style="magenta")

    for info in summary:
        state_color = {
            DeviceState.ONLINE.name: "green",
            DeviceState.BUSY.name: "yellow",
            DeviceState.ERROR.name: "red",
            DeviceState.OFFLINE.name: "dim",
        }.get(info["state"], "white")

        table.add_row(
            info["device_id"],
            info["device_type"],
            info["manufacturer"] or "N/A",
            f"[{state_color}]{info['state']}[/{state_color}]",
            ", ".join(info["capabilities"]),
        )

    console.print(table)


async def read_device(registry: DeviceRegistry, device_id: str, capability: str, params: List[str]) -> None:
    """Read from a device."""
    protocol = MHSProtocol(registry)
    
    # Parse params as key=value pairs
    param_dict = {}
    for p in params:
        if "=" in p:
            k, v = p.split("=", 1)
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
            param_dict[k] = v

    command = Command(
        command_id=f"cli_{asyncio.get_event_loop().time()}",
        command_type=CommandType.READ,
        device_id=device_id,
        capability=capability,
        parameters=param_dict,
    )

    response = await protocol.execute(command)
    
    if response.success:
        console.print_json(json.dumps(response.data))
    else:
        console.print(f"[red]Error:[/red] {response.error_message}")


async def write_device(registry: DeviceRegistry, device_id: str, capability: str, params: List[str]) -> None:
    """Write to a device."""
    protocol = MHSProtocol(registry)
    
    param_dict = {}
    for p in params:
        if "=" in p:
            k, v = p.split("=", 1)
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
            param_dict[k] = v

    command = Command(
        command_id=f"cli_{asyncio.get_event_loop().time()}",
        command_type=CommandType.WRITE,
        device_id=device_id,
        capability=capability,
        parameters=param_dict,
    )

    response = await protocol.execute(command)
    
    if response.success:
        console.print_json(json.dumps(response.data))
    else:
        console.print(f"[red]Error:[/red] {response.error_message}")


async def check_status(registry: DeviceRegistry) -> None:
    """Check health of all devices."""
    results = await registry.health_check_all()
    
    table = Table(title="Device Health Status")
    table.add_column("Device ID", style="cyan")
    table.add_column("Healthy", style="green")
    table.add_column("Details", style="blue")

    for device_id, result in results.items():
        healthy = result.get("healthy", False)
        status = "[green]✓[/green]" if healthy else "[red]✗[/red]"
        details = json.dumps(result) if not healthy else "OK"
        table.add_row(device_id, status, details)

    console.print(table)


async def setup_demo_devices(registry: DeviceRegistry) -> None:
    """Set up demo devices for testing."""
    from openmhs.adapters.sensors import BME280Driver
    from openmhs.adapters.camera import CameraDriver
    from openmhs.adapters.robots import RobotArmDriver, Printer3DDriver
    from openmhs.adapters.smart_home import SmartPlugDriver
    from openmhs.adapters.lab import MicroscopeDriver, LaserDriver

    drivers = [
        (BME280Driver, {"device_id": "sensor_001"}),
        (CameraDriver, {"device_id": "camera_001", "source": "0"}),
        (RobotArmDriver, {"device_id": "arm_001", "dof": 6}),
        (Printer3DDriver, {"device_id": "printer_001"}),
        (SmartPlugDriver, {"device_id": "plug_001"}),
        (MicroscopeDriver, {"device_id": "scope_001"}),
        (LaserDriver, {"device_id": "laser_001"}),
    ]

    for driver_cls, params in drivers:
        config = DriverConfig(driver_name=driver_cls.DRIVER_NAME, connection_params=params)
        driver = driver_cls(config)
        await driver.connect()
        if driver.device:
            await registry.register(driver.device)

    console.print(f"[green]Setup {len(drivers)} demo devices.[/green]")


async def serve_mcp(registry: DeviceRegistry, mode: str = "stdio") -> None:
    """Start MCP server."""
    from openmhs.mcp.server import MHSMcpServer
    
    server = MHSMcpServer(registry)
    if mode == "stdio":
        await server.run_stdio()
    else:
        await server.run_http()


async def serve_api(registry: DeviceRegistry, host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start REST API server."""
    from openmhs.api.server import create_app
    
    app = create_app(registry)
    import uvicorn
    await uvicorn.run(app, host=host, port=port)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mhs",
        description="Open Model Hardware Standard (MHS) CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # discover
    subparsers.add_parser("discover", help="List all connected devices")

    # read
    read_parser = subparsers.add_parser("read", help="Read from a device")
    read_parser.add_argument("device", help="Device ID")
    read_parser.add_argument("capability", help="Capability name")
    read_parser.add_argument("params", nargs="*", help="Parameters as key=value")

    # write
    write_parser = subparsers.add_parser("write", help="Write to a device")
    write_parser.add_argument("device", help="Device ID")
    write_parser.add_argument("capability", help="Capability name")
    write_parser.add_argument("params", nargs="*", help="Parameters as key=value")

    # status
    subparsers.add_parser("status", help="Check device health")

    # demo
    subparsers.add_parser("demo", help="Setup demo devices")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start MCP server")
    serve_parser.add_argument("--mode", choices=["stdio", "http"], default="stdio")

    # api
    api_parser = subparsers.add_parser("api", help="Start REST API server")
    api_parser.add_argument("--host", default="0.0.0.0")
    api_parser.add_argument("--port", type=int, default=8000)

    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    
    if not args.command:
        console.print("[red]No command specified. Use --help for usage.[/red]")
        return 1

    registry = DeviceRegistry()

    if args.command == "discover":
        await discover_devices(registry)
    elif args.command == "read":
        await read_device(registry, args.device, args.capability, args.params)
    elif args.command == "write":
        await write_device(registry, args.device, args.capability, args.params)
    elif args.command == "status":
        await check_status(registry)
    elif args.command == "demo":
        await setup_demo_devices(registry)
    elif args.command == "serve":
        await serve_mcp(registry, args.mode)
    elif args.command == "api":
        await serve_api(registry, args.host, args.port)
    else:
        console.print(f"[red]Unknown command: {args.command}[/red]")
        return 1

    return 0


def main_sync() -> int:
    """Synchronous entry point."""
    return asyncio.run(main())


if __name__ == "__main__":
    sys.exit(main_sync())
