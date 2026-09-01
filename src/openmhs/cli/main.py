"""CLI tool for MHS.

Usage:
    mhs discover          - List all connected devices
    mhs read <device> <capability> [params...]
    mhs write <device> <capability> [params...]
    mhs status            - Check all device health
    mhs demo              - Setup demo devices
    mhs serve             - Start MCP server
    mhs api               - Start REST API server
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import uuid
from typing import Any

from rich.console import Console
from rich.table import Table

from openmhs.adapters.visa.discovery import VisaDiscoveryManager
from openmhs.core.device import AccessLevel, DeviceState
from openmhs.core.driver import DriverConfig
from openmhs.core.protocol import Command, CommandType, MHSProtocol
from openmhs.core.registry import DeviceRegistry
from openmhs.transport.security import is_loopback, validate_host_security

console = Console()


def _parse_cli_params(params: list[str]) -> dict[str, Any]:
    param_dict: dict[str, Any] = {}
    for p in params:
        if "=" not in p:
            raise ValueError(f"Parameter must be in key=value format: '{p}'")
        k, v = p.split("=", 1)
        k = k.strip()
        if not k:
            raise ValueError(f"Parameter key cannot be empty in '{p}'")
        if k in param_dict:
            raise ValueError(f"Duplicate parameter key: '{k}'")
        try:
            val = json.loads(v)
        except json.JSONDecodeError:
            val = v
        if isinstance(val, float) and not math.isfinite(val):
            raise ValueError(f"Parameter '{k}' must be a finite value")
        param_dict[k] = val
    return param_dict


async def discover_devices(registry: DeviceRegistry) -> int:
    """Discover and list all devices."""
    summary = registry.get_metadata_summary()

    if not summary:
        console.print("[yellow]No devices registered.[/yellow]")
        return 0

    table = Table(title="MHS Devices")
    table.add_column("Device ID", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Manufacturer", style="blue")
    table.add_column("State", style="yellow")
    table.add_column("Capabilities", style="magenta")

    for info in summary:
        state_color = {
            DeviceState.ONLINE.name: "green",
            DeviceState.SIMULATED.name: "cyan",
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
    return 0


async def read_device(
    registry: DeviceRegistry, device_id: str, capability: str, params: list[str]
) -> int:
    """Read from a device."""
    protocol = MHSProtocol(registry)
    try:
        param_dict = _parse_cli_params(params)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1

    command = Command(
        command_id=f"cli_{uuid.uuid4().hex[:12]}",
        command_type=CommandType.READ,
        device_id=device_id,
        capability=capability,
        parameters=param_dict,
    )

    response = await protocol.execute(command, access_level=AccessLevel.ADMIN)

    if response.success:
        console.print_json(json.dumps(response.data))
        return 0
    else:
        console.print(f"[red]Error:[/red] {response.error_message}")
        return 1


async def write_device(
    registry: DeviceRegistry, device_id: str, capability: str, params: list[str]
) -> int:
    """Write to a device."""
    protocol = MHSProtocol(registry)
    try:
        param_dict = _parse_cli_params(params)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1

    command = Command(
        command_id=f"cli_{uuid.uuid4().hex[:12]}",
        command_type=CommandType.WRITE,
        device_id=device_id,
        capability=capability,
        parameters=param_dict,
    )

    response = await protocol.execute(command, access_level=AccessLevel.ADMIN)

    if response.success:
        console.print_json(json.dumps(response.data))
        return 0
    else:
        console.print(f"[red]Error:[/red] {response.error_message}")
        return 1


async def check_status(registry: DeviceRegistry) -> int:
    """Check health of all devices."""
    results = await registry.health_check_all()

    table = Table(title="Device Health Status")
    table.add_column("Device ID", style="cyan")
    table.add_column("Healthy", style="green")
    table.add_column("Details", style="blue")

    all_healthy = True
    for device_id, result in results.items():
        healthy = result.get("healthy", False)
        if not healthy:
            all_healthy = False
        status = "[green]✓[/green]" if healthy else "[red]✗[/red]"
        details = json.dumps(result) if not healthy else "OK"
        table.add_row(device_id, status, details)

    console.print(table)
    return 0 if (all_healthy or not results) else 1


async def setup_demo_devices(registry: DeviceRegistry) -> int:
    """Set up demo devices for testing."""
    from openmhs.adapters.camera import CameraDriver
    from openmhs.adapters.lab import LaserDriver, MicroscopeDriver
    from openmhs.adapters.robots import Printer3DDriver, RobotArmDriver
    from openmhs.adapters.sensors import BME280Driver
    from openmhs.adapters.smart_home import SmartPlugDriver

    drivers: list[tuple[type[Any], dict[str, Any]]] = [
        (BME280Driver, {"device_id": "sensor_001"}),
        (CameraDriver, {"device_id": "camera_001", "source": "0"}),
        (RobotArmDriver, {"device_id": "arm_001", "dof": 6}),
        (Printer3DDriver, {"device_id": "printer_001"}),
        (SmartPlugDriver, {"device_id": "plug_001"}),
        (MicroscopeDriver, {"device_id": "scope_001"}),
        (LaserDriver, {"device_id": "laser_001"}),
    ]

    connected_count = 0
    for driver_cls, params in drivers:
        config = DriverConfig(
            driver_name=driver_cls.DRIVER_NAME,
            connection_params=params,
            simulation=True,
        )
        driver = driver_cls(config)
        ok = await driver.connect()
        if ok and driver.device:
            await registry.register(driver.device)
            connected_count += 1

    console.print(f"[green]Setup {connected_count} demo devices (simulation mode).[/green]")
    return 0 if connected_count > 0 else 1


async def serve_mcp(
    registry: DeviceRegistry,
    mode: str = "stdio",
    discovery_interval: float = 5.0,
    discovery_manager: VisaDiscoveryManager | None = None,
) -> None:
    """Start the selected server with one shared registry/manager graph."""
    from openmhs.mcp.server import MHSMcpServer

    manager = discovery_manager or VisaDiscoveryManager(registry, interval=discovery_interval)
    server = MHSMcpServer(registry, discovery_manager=manager)
    if mode == "stdio":
        await server.run_stdio()
    else:
        await server.run_http()


async def serve_api(
    registry: DeviceRegistry,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Start REST API server."""
    validate_host_security(host)
    from openmhs.api.server import create_app

    app = create_app(registry, is_local=is_loopback(host))
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


def _positive_float(value: str) -> float:
    """Parse a strictly positive command-line interval."""
    try:
        parsed = float(value)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return parsed


def _valid_port(value: str) -> int:
    """Parse a valid TCP port number."""
    try:
        port = int(value)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mhs",
        description="Open Model Hardware Standard (MHS) CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # discover
    discover_parser = subparsers.add_parser("discover", help="List all connected devices")
    discover_parser.add_argument(
        "--discovery-interval",
        type=_positive_float,
        default=5.0,
        help="Discovery polling interval in seconds",
    )
    discover_parser.add_argument(
        "--simulation",
        action="store_true",
        help="Include simulated demo devices",
    )

    # read
    read_parser = subparsers.add_parser("read", help="Read from a device")
    read_parser.add_argument("device", help="Device ID")
    read_parser.add_argument("capability", help="Capability name")
    read_parser.add_argument("params", nargs="*", help="Parameters as key=value")
    read_parser.add_argument(
        "--simulation",
        action="store_true",
        help="Include simulated demo devices",
    )

    # write
    write_parser = subparsers.add_parser("write", help="Write to a device")
    write_parser.add_argument("device", help="Device ID")
    write_parser.add_argument("capability", help="Capability name")
    write_parser.add_argument("params", nargs="*", help="Parameters as key=value")
    write_parser.add_argument(
        "--simulation",
        action="store_true",
        help="Include simulated demo devices",
    )

    # status
    status_parser = subparsers.add_parser("status", help="Check device health")
    status_parser.add_argument(
        "--simulation",
        action="store_true",
        help="Include simulated demo devices",
    )

    # demo
    subparsers.add_parser("demo", help="Setup demo devices")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start MCP server")
    serve_parser.add_argument("--mode", choices=["stdio", "http"], default="stdio")
    serve_parser.add_argument(
        "--discovery-interval",
        type=_positive_float,
        default=5.0,
        help="Discovery polling interval in seconds",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for HTTP mode (default: 127.0.0.1)",
    )
    serve_parser.add_argument(
        "--port",
        type=_valid_port,
        default=8080,
        help="Port for HTTP mode (default: 8080)",
    )

    # api
    api_parser = subparsers.add_parser("api", help="Start REST API server")
    api_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind REST API (default: 127.0.0.1)",
    )
    api_parser.add_argument(
        "--port",
        type=_valid_port,
        default=8000,
        help="Port for REST API (default: 8000)",
    )

    return parser.parse_args()


async def main() -> int:
    args = parse_args()

    if not args.command:
        console.print("[red]No command specified. Use --help for usage.[/red]")
        return 1

    registry = DeviceRegistry()

    if args.command == "discover":
        if getattr(args, "simulation", False):
            await setup_demo_devices(registry)
        manager = VisaDiscoveryManager(registry, interval=args.discovery_interval)
        try:
            result = await manager.scan_once()
            for issue in result.errors:
                console.print(f"[yellow]Discovery: {issue.message}[/yellow]")
            return await discover_devices(registry)
        finally:
            await manager.close()

    elif args.command in {"read", "write", "status"}:
        if getattr(args, "simulation", False):
            await setup_demo_devices(registry)
        manager = VisaDiscoveryManager(registry)
        try:
            await manager.scan_once()
            if args.command == "read":
                return await read_device(registry, args.device, args.capability, args.params)
            elif args.command == "write":
                return await write_device(registry, args.device, args.capability, args.params)
            elif args.command == "status":
                return await check_status(registry)
            return 0
        finally:
            await manager.close()

    elif args.command == "demo":
        return await setup_demo_devices(registry)

    elif args.command == "serve":
        manager = None
        if args.mode == "stdio":
            manager = VisaDiscoveryManager(registry, interval=args.discovery_interval)
        await serve_mcp(registry, args.mode, args.discovery_interval, manager)
        return 0

    elif args.command == "api":
        await serve_api(registry, args.host, args.port)
        return 0

    else:
        console.print(f"[red]Unknown command: {args.command}[/red]")
        return 1


def main_sync() -> int:
    """Synchronous entry point."""
    return asyncio.run(main())


if __name__ == "__main__":
    sys.exit(main_sync())
