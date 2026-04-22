import hashlib
import hmac
import json
import unittest
from unittest.mock import Mock

from pushstream import PushStream


class PushStreamPythonTests(unittest.TestCase):
    def test_build_signed_query_matches_backend_contract(self):
        client = PushStream('public-key', api_url='https://api.pushstream.online')
        body = json.dumps({
            'name': 'order.created',
            'channel': 'public-orders',
            'data': json.dumps({'id': 1}, separators=(',', ':')),
        }, separators=(',', ':'))

        query = client.build_signed_query('POST', '/api/apps/app-id/events', body, 'secret-key')

        self.assertEqual(query['auth_key'], 'public-key')
        self.assertEqual(query['auth_version'], 'v1')
        self.assertEqual(query['body_md5'], hashlib.md5(body.encode()).hexdigest())

        canonical = '&'.join([
            f'auth_key={query["auth_key"]}',
            f'auth_timestamp={query["auth_timestamp"]}',
            f'auth_version={query["auth_version"]}',
            f'body_md5={query["body_md5"]}',
        ])
        expected = hmac.new(
            b'secret-key',
            f'POST\n/api/apps/app-id/events\n{canonical}'.encode(),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(query['auth_signature'], expected)

    def test_public_subscribe_requires_connection(self):
        client = PushStream('public-key', ws_url='wss://ws.pushstream.online')

        with self.assertRaisesRegex(RuntimeError, 'Not connected'):
            client.subscribe('public-orders')

    def test_private_subscribe_requires_authenticated_flow(self):
        client = PushStream('public-key', ws_url='wss://ws.pushstream.online')
        client.connected = True

        with self.assertRaisesRegex(RuntimeError, 'subscribe_authenticated'):
            client.subscribe('private-orders')

    def test_handle_message_preserves_non_json_payload(self):
        client = PushStream('public-key', ws_url='wss://ws.pushstream.online')
        received = []

        class StubChannel:
            def _handle_event(self, event, data):
                received.append((event, data))

        client.channels['public-orders'] = StubChannel()
        client._handle_message({
            'channel': 'public-orders',
            'event': 'order.created',
            'data': '{not-json}',
        })

        self.assertEqual(received, [('order.created', '{not-json}')])

    def test_constructor_accepts_cross_sdk_aliases(self):
        client = PushStream(
            'public-key',
            appId='app-id',
            wsUrl='wss://ws.pushstream.online',
            apiUrl='https://api.pushstream.online',
            authEndpoint='https://api.pushstream.online/auth',
            authVersion='v1',
            enableLogging=True,
        )

        self.assertEqual(client.app_id, 'app-id')
        self.assertEqual(client.ws_url, 'wss://ws.pushstream.online')
        self.assertEqual(client.api_url, 'https://api.pushstream.online')
        self.assertEqual(client.auth_endpoint, 'https://api.pushstream.online/auth')
        self.assertEqual(client.auth_version, 'v1')
        self.assertTrue(client.enable_logging)

    def test_publish_uses_injected_http_client(self):
        response = Mock()
        response.json.return_value = {'ok': True}
        response.raise_for_status.return_value = None

        http_client = Mock()
        http_client.post.return_value = response

        client = PushStream(
            'public-key',
            api_url='https://api.pushstream.online',
            http_client=http_client,
        )

        result = client.publish('app-id', 'secret-key', 'public-orders', 'order.created', {'id': 1})

        self.assertEqual(result, {'ok': True})
        args, kwargs = http_client.post.call_args
        self.assertIn('auth_signature=', args[0])
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/json')

    def test_resubscribe_channels_reauthenticates_non_public_channels(self):
        client = PushStream('public-key', ws_url='wss://ws.pushstream.online')
        sent = []
        client.socket_id = '123.456'
        client.connected = True
        client._send = lambda payload: sent.append(payload)
        client._authorize_channel = Mock(return_value={
            'auth': 'public-key:signed',
            'channel_data': '{"user_id":"1"}',
        })

        public_channel = type('ChannelStub', (), {
            'name': 'public-orders',
            'channel_type': 'public',
            'auth_payload': {},
            'auth_headers': {},
        })()
        presence_channel = type('ChannelStub', (), {
            'name': 'presence-orders',
            'channel_type': 'presence',
            'auth_payload': {'tenant': 'org-1'},
            'auth_headers': {'Authorization': 'Bearer token'},
        })()

        client.channels = {
            public_channel.name: public_channel,
            presence_channel.name: presence_channel,
        }

        client._resubscribe_channels()

        self.assertEqual(sent[0]['data']['channel'], 'public-orders')
        self.assertEqual(sent[1]['data']['channel'], 'presence-orders')
        self.assertEqual(sent[1]['data']['auth'], 'public-key:signed')


if __name__ == '__main__':
    unittest.main()
