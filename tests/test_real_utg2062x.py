"""Opt-in, read-only checks for a connected UTG2062X."""

import os

import pytest

if os.getenv("OPENMHS_REAL_UTG2062X") != "1":
    pytest.skip("set OPENMHS_REAL_UTG2062X=1 to enable hardware tests", allow_module_level=True)

pyvisa = pytest.importorskip("pyvisa")


@pytest.mark.parametrize("channel", [1, 2])
def test_utg2062x_read_only_queries(channel: int) -> None:
    resource_name = os.getenv("OPENMHS_UTG2062X_RESOURCE")
    if not resource_name:
        pytest.skip("OPENMHS_UTG2062X_RESOURCE is not set")
    manager = pyvisa.ResourceManager()
    instrument = None
    try:
        instrument = manager.open_resource(resource_name)
        instrument.timeout = 2000
        identity = instrument.query("*IDN?").strip()
        fields = [field.strip() for field in identity.split(",")]
        if (
            len(fields) < 2
            or "UNI-T" not in fields[0].upper()
            or fields[1].upper() != "UTG2062X"
        ):
            pytest.skip("connected resource is not a UNI-T UTG2062X")
        instrument.query(f":CHANnel{channel}:OUTPut?")
        instrument.query(f":CHANnel{channel}:BASE:WAVe?")
        instrument.query(f":CHANnel{channel}:BASE:AMPLitude?")
    finally:
        if instrument is not None:
            instrument.close()
        manager.close()
