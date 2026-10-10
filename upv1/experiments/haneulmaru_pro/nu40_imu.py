"""Legacy import path for the shared NU40 USB/BLE implementation."""
from pathlib import Path
import sys

# Preserve direct stream_server.py execution from the original app folder.
_package_root = str(Path(__file__).resolve().parents[2])
if _package_root not in sys.path:
    sys.path.insert(0, _package_root)

from multidevice_app.nu40_transport import (
    BleDecoder, FRESH_SECONDS, ImuHub, UART_TX, normalize,
)

__all__ = ["BleDecoder", "FRESH_SECONDS", "ImuHub", "UART_TX", "normalize"]
