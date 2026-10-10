import json
import struct
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from nu40_imu import ImuHub, BleDecoder, normalize

PACKET=dict(v=1,t_ms=123,ax=.1,ay=-.2,az=1,gx=12.3,gy=-6.1,gz=0)

class InputTests(unittest.TestCase):
    def test_binary_and_fragmented_ble_match_usb(self):
        decoder=BleDecoder()
        raw=struct.pack('<BI6h',1,123,1000,-2000,10000,123,-61,0)
        self.assertEqual(decoder.feed(raw),[PACKET])
        data=json.dumps(PACKET).encode()+b'\n'
        self.assertEqual(decoder.feed(data[:13]),[])
        self.assertEqual(decoder.feed(data[13:]),[PACKET])
        self.assertEqual(decoder.feed(b'x'*9000),[])

    def test_invalid_values_are_not_sensor_samples(self):
        for bad in ({'ready':True},{**PACKET,'gx':float('nan')},{**PACKET,'t_ms':-1},{**PACKET,'gx':True}):
            with self.assertRaises(ValueError):normalize(bad)

    def test_usb_priority_ble_fallback_and_stale_stop(self):
        now=[0.0];hub=ImuHub(clock=lambda:now[0])
        hub.record('USB',PACKET);hub.record('BLE',PACKET)
        self.assertEqual(hub.snapshot()['source'],'USB')
        now[0]=.4;hub.record('BLE',{**PACKET,'t_ms':523})
        self.assertEqual(hub.snapshot()['source'],'BLE')
        now[0]=.5;hub.record('USB',{**PACKET,'t_ms':623})
        self.assertEqual(hub.snapshot()['source'],'USB')
        now[0]=1;self.assertIsNone(hub.snapshot()['sample'])
        self.assertEqual(hub.snapshot()['sources']['BLE']['count'],2)

    def test_disconnect_immediately_invalidates_usb(self):
        hub=ImuHub();hub.record('USB',PACKET);hub.record('BLE',PACKET)
        hub.update_status('USB',False,'unplugged')
        self.assertEqual(hub.snapshot()['source'],'BLE')

    def test_imu_routes_require_host_token(self):
        import stream_server as app
        app.IMU=ImuHub();app.IMU.record('USB',PACKET)
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            root=f'http://127.0.0.1:{server.server_port}'
            for path in ('imu','imu-model'):
                for token,status in (('',401),(app.PHONE,403)):
                    with self.assertRaises(HTTPError) as error:urlopen(Request(root+'/stream/'+path,headers={'Authorization':'Bearer '+token}))
                    self.assertEqual(error.exception.code,status)
                with urlopen(Request(root+'/stream/'+path,headers={'Authorization':'Bearer '+app.HOST})) as response:
                    self.assertEqual(response.status,200)
            for asset in ('imu-input.js','imu-direction.js','drone-coordinates.js','coordinate-ui.js'):
                with urlopen(root+'/'+asset) as response:self.assertEqual(response.status,200)
        finally:
            server.shutdown();server.server_close();worker.join();app.IMU=None

if __name__=='__main__':unittest.main()
