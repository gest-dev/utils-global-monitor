"""Real HTTP adapter tests against a local ephemeral fixture; no outside sends."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'extension'))
from global_monitor import providers
from global_monitor.engine import validate_integration


class Handler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.requests.append((self.path, {key.lower(): value for key, value in self.headers.items()}, json.loads(body)))
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/leaked')
            self.end_headers()
        elif self.path == '/failure':
            self.send_response(500)
            self.end_headers()
        else:
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b'Accepted')


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        Handler.requests.clear()

    def test_actual_json_request_and_custom_authentication(self):
        item = validate_integration({'name': 'Fixture', 'type': 'webhook', 'url': self.url + '/notify',
            'auth_header': 'X-API-Key', 'auth_prefix': '', 'secret': 'test-local-only', 'recipient': 'local',
            'body_template': '{"number":"{{destination}}","text":"{{title}} — {{body}}"}'})
        result = providers.deliver(item, {'title': 'Olá "mundo"', 'body': '{{destination}} <&>'}, 'fixture-event')
        self.assertTrue(result['accepted'])
        path, headers, body = Handler.requests[0]
        self.assertEqual(path, '/notify')
        self.assertEqual(headers['x-api-key'], 'test-local-only')
        self.assertEqual(body, {'number': 'local', 'text': 'Olá "mundo" — {{destination}} <&>'})
        self.assertEqual(headers['idempotency-key'], 'fixture-event')

    def test_credentials_do_not_follow_redirects(self):
        item = {'name': 'Fixture', 'url': self.url + '/redirect', 'secret': 'test-local-only'}
        with self.assertRaises(providers.DataError):
            providers.deliver(item, {'title': 'Only local'}, 'fixture-event')
        self.assertEqual(len(Handler.requests), 1)
        self.assertNotIn('/leaked', [row[0] for row in Handler.requests])

    def test_server_error_is_reported(self):
        with self.assertRaises(providers.DataError):
            providers.deliver({'url': self.url + '/failure'}, {'title': 'Only local'}, 'fixture-event')


if __name__ == '__main__':
    unittest.main()
