"""Exercise both real HTTP handlers without tunnels, accounts or camera access."""
import importlib
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ReplayRoutesTest(unittest.TestCase):
    def test_static_assets_and_private_files_for_both_servers(self):
        for module_name in ('server', 'stream_server'):
            with self.subTest(server=module_name):
                module = importlib.import_module(module_name)
                http = ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
                thread = threading.Thread(target=http.serve_forever, daemon=True)
                thread.start()
                base = f'http://127.0.0.1:{http.server_port}'
                try:
                    with urlopen(base + '/replay/') as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn('가상 데이터 재생', response.read().decode('utf-8'))
                    for asset in ('app', 'core', 'providers'):
                        with urlopen(base + f'/replay/{asset}.mjs') as response:
                            self.assertEqual(response.headers['Content-Type'], 'text/javascript')
                    with urlopen(base + '/replay/data/catalog.json') as response:
                        self.assertEqual(response.headers['Content-Type'], 'application/json')
                        catalog = json.load(response)
                    for item in catalog['scenarios']:
                        with urlopen(base + f'/replay/data/{item["id"]}.json') as response:
                            self.assertEqual(json.load(response)['source'], 'synthetic')
                    for private in ('/replay/aws-handler.mjs', '/replay/replay.test.mjs', '/infra/aws/template.yaml', '/replay/../../.env'):
                        with self.assertRaises(HTTPError) as error:
                            urlopen(base + private)
                        self.assertEqual(error.exception.code, 404)
                finally:
                    http.shutdown()
                    http.server_close()
                    thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
