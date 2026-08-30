import sys
from pathlib import Path

import pytest

from openmhs.cli.main import parse_args


@pytest.mark.parametrize("argv", [["mhs", "serve", "--discovery-interval", "2.5"], ["mhs", "discover", "--discovery-interval", "1"]])
def test_discovery_interval_is_parsed(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    args = parse_args()
    assert args.discovery_interval == float(argv[-1])


def test_discovery_interval_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["mhs", "serve", "--discovery-interval", "0"])
    with pytest.raises(SystemExit):
        parse_args()


def test_console_scripts_use_sync_entrypoint() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'openmhs = "openmhs.cli.main:main_sync"' in text
    assert 'mhs = "openmhs.cli.main:main_sync"' in text
