"""NU40 USB NDJSON + BLE UART input. Keeps BLE subscribed while USB is preferred."""
from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import threading
import time
import uuid

UART_TX = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'
FRESH_SECONDS = 0.35


def normalize(packet):
    if not isinstance(packet, dict) or packet.get('v') != 1:
        raise ValueError('NU40 v1 packet required')
    stamp = packet.get('t_ms')
    if type(stamp) is not int or not 0 <= stamp <= 0xffffffff:
        raise ValueError('Invalid board timestamp')
    values = {k: packet.get(k) for k in ('ax', 'ay', 'az', 'gx', 'gy', 'gz')}
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values.values()):
        raise ValueError('Invalid IMU values')
    return {'v': 1, 't_ms': stamp, **values}


class BleDecoder:
    """Decode current 17-byte <BI6h notifications or legacy NDJSON fragments."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        if len(data) == 17 and data[0] == 1:
            v, stamp, ax, ay, az, gx, gy, gz = struct.unpack('<BI6h', data)
            return [normalize(dict(v=v, t_ms=stamp, ax=ax/10000, ay=ay/10000,
                                   az=az/10000, gx=gx/10, gy=gy/10, gz=gz/10))]
        self.buffer.extend(data)
        if len(self.buffer) > 8192:
            self.buffer.clear()
            return []
        result = []
        while b'\n' in self.buffer:
            line, _, rest = self.buffer.partition(b'\n')
            self.buffer = bytearray(rest)
            try:
                result.append(normalize(json.loads(line)))
            except (ValueError, UnicodeDecodeError, TypeError):
                pass
        return result


class ImuHub:
    def __init__(self, port=None, baud=115200, ble_address=None, ble=True, clock=time.monotonic):
        self.port, self.baud, self.ble_address, self.ble = port, baud, ble_address, ble
        self.clock = clock
        self.session = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.threads = []
        self.samples = {'USB': None, 'BLE': None}
        self.arrival = {'USB': -math.inf, 'BLE': -math.inf}
        self.status = {s: {'connected': False, 'count': 0, 'message': '장치 검색 중'} for s in self.samples}
        if not ble:
            self.status['BLE']['message'] = 'BLE 사용 안 함'

    def update_status(self, source, connected, message):
        with self.lock:
            self.status[source].update(connected=connected, message=message)
            if not connected:
                self.samples[source] = None

    def record(self, source, packet):
        packet = normalize(packet)
        with self.lock:
            self.samples[source] = packet
            self.arrival[source] = self.clock()
            self.status[source]['count'] += 1

    def snapshot(self):
        with self.lock:
            now = self.clock()
            sources = {s: {**self.status[s], 'fresh': self.samples[s] is not None and now-self.arrival[s] <= FRESH_SECONDS}
                       for s in self.samples}
            source = next((s for s in ('USB', 'BLE') if sources[s]['fresh']), None)
            return {'session': self.session, 'source': source, 'sources': sources,
                    'sample': dict(self.samples[source]) if source else None,
                    'age_ms': round((now-self.arrival[source])*1000) if source else None}

    def start(self):
        for worker in ([self.read_usb, self.read_ble] if self.ble else [self.read_usb]):
            thread = threading.Thread(target=worker, daemon=True)
            self.threads.append(thread)
            thread.start()

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=2)

    def read_usb(self):
        try:
            import serial
            from serial.tools import list_ports
        except ImportError:
            self.update_status('USB', False, 'pyserial 설치 필요')
            return
        while not self.stop_event.is_set():
            # Only open the known NU40 USB identity, unless a port was specified.
            try:
                ports = [self.port] if self.port else [p.device for p in list_ports.comports()
                                                      if (p.vid, p.pid) == (0x239a, 0x80da)]
            except Exception as exc:
                self.update_status('USB', False, '장치 검색 실패: '+str(exc))
                self.stop_event.wait(2)
                continue
            if len(ports) != 1:
                self.update_status('USB', False, 'USB 연결 대기' if not ports else '여러 NU40 감지: --imu-port로 선택하세요')
                self.stop_event.wait(1)
                continue
            try:
                device = serial.Serial(port=None, baudrate=self.baud, timeout=0.2,
                                       exclusive=True if os.name == 'posix' else None)
                device.port = ports[0]
                device.dtr, device.rts = True, False
                device.open()
                with device:
                    self.update_status('USB', True, 'USB 연결됨')
                    buffer = bytearray()
                    valid_at = self.clock()
                    while not self.stop_event.is_set():
                        buffer.extend(device.read(max(1, min(device.in_waiting, 4096))))
                        if len(buffer) > 8192:
                            buffer.clear()
                        while b'\n' in buffer:
                            line, _, rest = buffer.partition(b'\n')
                            buffer = bytearray(rest)
                            try:
                                self.record('USB', json.loads(line))
                                valid_at = self.clock()
                            except (ValueError, TypeError, UnicodeDecodeError):
                                pass
                        if self.clock()-valid_at > 3:
                            raise OSError('IMU 데이터 없음: 재연결 중')
            except (OSError, serial.SerialException, ValueError) as exc:
                self.update_status('USB', False, str(exc))
                self.stop_event.wait(1)

    def read_ble(self):
        try:
            asyncio.run(self.ble_loop())
        except ImportError:
            self.update_status('BLE', False, 'bleak 설치 필요')
        except Exception as exc:
            self.update_status('BLE', False, 'BLE 사용 불가: '+str(exc))

    async def ble_loop(self):
        from bleak import BleakClient, BleakScanner
        while not self.stop_event.is_set():
            try:
                if self.ble_address:
                    device = await BleakScanner.find_device_by_address(self.ble_address, timeout=5)
                else:
                    device = await BleakScanner.find_device_by_filter(
                        lambda d, a: (a.local_name or d.name) == 'NU40-IMU', timeout=5)
                if device is None:
                    self.update_status('BLE', False, 'NU40-IMU 검색 중')
                    continue
                async with BleakClient(device, timeout=10) as client:
                    decoder = BleDecoder()
                    def notification(_characteristic, data):
                        for packet in decoder.feed(data):
                            self.record('BLE', packet)
                    await client.start_notify(UART_TX, notification)
                    self.update_status('BLE', True, 'BLE 연결 유지')
                    connected_at = self.clock()
                    while client.is_connected and not self.stop_event.is_set():
                        with self.lock:
                            last_sample = self.arrival['BLE']
                        if self.clock() - max(connected_at, last_sample) > 5:
                            raise OSError('BLE IMU 데이터 없음')
                        await asyncio.sleep(0.2)
                self.update_status('BLE', False, 'BLE 연결 대기')
            except Exception as exc:
                self.update_status('BLE', False, 'BLE 재연결 중: '+str(exc))
                await asyncio.sleep(1)
