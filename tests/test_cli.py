import argparse
import sys
from pathlib import Path

import pytest

from openmhs.cli.main import (
    _parse_cli_params,
    _positive_float,
    _valid_port,
    main,
    parse_args,
)


@pytest.mark.parametrize(
    "argv",
    [
        ["mhs", "serve", "--discovery-interval", "2.5"],
        ["mhs", "discover", "--discovery-interval", "1"],
    ],
)
def test_discovery_interval_is_parsed(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    args = parse_args()
    assert args.discovery_interval == float(argv[-1])


def test_discovery_interval_must_be_positive_and_finite(monkeypatch: pytest.MonkeyPatch) -> None:
    for invalid in ["0", "-1", "nan", "inf", "-inf"]:
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_float(invalid)


def test_valid_port():
    assert _valid_port("8000") == 8000
    assert _valid_port("1") == 1
    assert _valid_port("65535") == 65535
    for invalid in ["0", "65536", "-1", "abc"]:
        with pytest.raises(argparse.ArgumentTypeError):
            _valid_port(invalid)


def test_parse_cli_params():
    # Valid key=value pairs
    res = _parse_cli_params(["channel=1", 'name="test"', "ratio=0.5", "enabled=true"])
    assert res == {"channel": 1, "name": "test", "ratio": 0.5, "enabled": True}

    # Missing '='
    with pytest.raises(ValueError, match="key=value format"):
        _parse_cli_params(["invalid"])

    # Empty key
    with pytest.raises(ValueError, match="key cannot be empty"):
        _parse_cli_params(["=value"])

    # Duplicate key
    with pytest.raises(ValueError, match="Duplicate parameter key"):
        _parse_cli_params(["key=1", "key=2"])

    # Non-finite value
    with pytest.raises(ValueError, match="finite value"):
        _parse_cli_params(["val=NaN"])


@pytest.mark.asyncio
async def test_cli_main_no_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mhs"])
    ret = await main()
    assert ret == 1


@pytest.mark.asyncio
async def test_cli_main_demo_and_status(monkeypatch):
    # mhs demo
    monkeypatch.setattr(sys, "argv", ["mhs", "demo"])
    ret = await main()
    assert ret == 0

    # mhs status with simulation flag
    monkeypatch.setattr(sys, "argv", ["mhs", "status", "--simulation"])
    ret = await main()
    assert ret == 0


@pytest.mark.asyncio
async def test_cli_main_read_write_failures(monkeypatch):
    # Reading missing device -> exits 1
    monkeypatch.setattr(sys, "argv", ["mhs", "read", "missing_device", "status"])
    ret = await main()
    assert ret == 1

    # Writing missing device -> exits 1
    monkeypatch.setattr(sys, "argv", ["mhs", "write", "missing_device", "set", "val=1"])
    ret = await main()
    assert ret == 1

    # Malformed params -> exits 1
    monkeypatch.setattr(sys, "argv", ["mhs", "read", "some_device", "status", "bad_param"])
    ret = await main()
    assert ret == 1


@pytest.mark.asyncio
async def test_cli_main_discover_simulation(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mhs", "discover", "--simulation"])
    ret = await main()
    assert ret == 0


def test_console_scripts_use_sync_entrypoint() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'openmhs = "openmhs.cli.main:main_sync"' in text
    assert 'mhs = "openmhs.cli.main:main_sync"' in text
