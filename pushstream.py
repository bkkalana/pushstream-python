import hashlib
import hmac
import json
import os
import random
import time
from threading import Thread
from urllib.parse import urlencode

import requests
from websocket import WebSocketApp


class PushStream:
    def __init__(
        self,
        app_key,
        app_id=None,
        ws_url=None,
        api_url=None,
        auth_endpoint=None,
        auth_headers=None,
        protocol='pushstream-v1',
        auth_version='v1',
        client='pushstream-python',
        version='2.1.0',
        request_timeout=10,
        max_reconnect_attempts=5,
        enable_logging=False,
        on_state_change=None,
        on_error=None,
        on_reconnect_attempt=None,
        http_client=None,
        websocket_factory=None,
        **aliases,
    ):
        app_id = aliases.pop('appId', app_id)
        ws_url = aliases.pop('wsUrl', ws_url)
        api_url = aliases.pop('apiUrl', api_url)
        auth_endpoint = aliases.pop('authEndpoint', auth_endpoint)
        auth_headers = aliases.pop('authHeaders', auth_headers)
        auth_version = aliases.pop('authVersion', auth_version)
        request_timeout = aliases.pop('requestTimeout', request_timeout)
        max_reconnect_attempts = aliases.pop('maxReconnectAttempts', max_reconnect_attempts)
        enable_logging = aliases.pop('enableLogging', enable_logging)
        client = aliases.pop('clientName', client)
        version = aliases.pop('clientVersion', version)
        if aliases:
            raise TypeError(f'Unexpected options: {", ".join(sorted(aliases.keys()))}')

        self.app_key = app_key
        self.app_id = app_id
        self.ws_url = self._normalize_ws_url(ws_url or os.getenv('PUSHSTREAM_WS_URL'))
        self.api_url = self._normalize_http_url(api_url or os.getenv('PUSHSTREAM_API_URL'))
        self.auth_endpoint = auth_endpoint
        self.auth_headers = auth_headers or {}
        self.protocol = protocol
        self.auth_version = auth_version
        self.client = client
        self.version = version
        self.request_timeout = request_timeout
        self.ws = None
        self.socket_id = None
        self.channels = {}
        self.connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = max_reconnect_attempts
        self.should_reconnect = True
        self.enable_logging = enable_logging
        self.ws_thread = None
        self.on_state_change = on_state_change
        self.on_error = on_error
        self.on_reconnect_attempt = on_reconnect_attempt
        self.http_client = http_client or requests
        self.websocket_factory = websocket_factory or WebSocketApp

    def connect(self):
        if not self.app_key:
            raise ValueError('app_key is required')
        if not self.ws_url:
            raise ValueError('ws_url is required')

        self.should_reconnect = True
        self.connected = False

        def on_message(_, message):
            data = self._safe_json_loads(message)
            if not data:
                self._log('warn', 'Ignored malformed websocket payload')
                return

            if data['event'] == 'pusher:connection_established':
                payload = self._safe_json_loads(data.get('data'))
                if not payload or 'socket_id' not in payload:
                    self._notify_error(ValueError('Invalid connection_established payload'))
                    return
                self.socket_id = payload['socket_id']
                self.connected = True
                self.reconnect_attempts = 0
                self._notify_state_change('connected', {'socket_id': self.socket_id})
                self._resubscribe_channels()
            elif data['event'] == 'pusher:error':
                self._notify_error(RuntimeError(f"Realtime error: {data.get('data')}"))
            else:
                self._handle_message(data)

        def on_error(_, error):
            self._notify_error(error)

        def on_close(_, close_status_code, close_msg):
            self._notify_state_change('disconnected', {
                'code': close_status_code,
                'reason': close_msg,
            })
            self.connected = False
            self.socket_id = None
            if self.should_reconnect:
                self._attempt_reconnect()

        def on_open(_):
            self._notify_state_change('transport_open', {})

        self.ws = self.websocket_factory(
            self._build_ws_url(),
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )

        self.ws_thread = Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()

        start_time = time.time()
        while not self.connected and (time.time() - start_time) < self.request_timeout:
            time.sleep(0.1)

        if not self.connected:
            if self.should_reconnect:
                self._attempt_reconnect()
            raise TimeoutError('Connection timeout')

        return self.socket_id

    def subscribe(self, channel_name):
        if not self.connected:
            raise RuntimeError('Not connected')
        if channel_name.startswith('private-') or channel_name.startswith('presence-'):
            raise RuntimeError('Use subscribe_authenticated() for private and presence channels')

        channel = Channel(channel_name, self, channel_type='public')
        self.channels[channel_name] = channel
        self._send({
            'event': 'pusher:subscribe',
            'data': {'channel': channel_name},
        })

        return channel

    def subscribe_authenticated(self, channel_name, auth_payload=None, headers=None):
        if not self.connected or not self.socket_id:
            raise RuntimeError('Not connected')
        if not self.auth_endpoint:
            raise RuntimeError('auth_endpoint is required')

        channel = Channel(
            channel_name,
            self,
            channel_type='presence' if channel_name.startswith('presence-') else 'private',
            auth_payload=auth_payload or {},
            auth_headers=headers or {},
        )
        auth_data = self._authorize_channel(channel_name, auth_payload or {}, headers or {})
        self.channels[channel_name] = channel
        payload = {
            'channel': channel_name,
            'auth': auth_data['auth'],
        }
        if 'channel_data' in auth_data:
            payload['channel_data'] = auth_data['channel_data']

        self._send({
            'event': 'pusher:subscribe',
            'data': payload,
        })

        return channel

    def unsubscribe(self, channel_name):
        self._send({
            'event': 'pusher:unsubscribe',
            'data': {'channel': channel_name},
        })
        self.channels.pop(channel_name, None)

    def disconnect(self):
        self.should_reconnect = False
        if self.ws:
            self.ws.close()
            self.ws = None
        self.connected = False
        self.socket_id = None

    def publish(self, app_id, app_secret, channel, event, data, socket_id=None):
        if not self.api_url:
            raise ValueError('api_url is required')

        body = json.dumps({
            'name': event,
            'channel': channel,
            'data': data if isinstance(data, str) else json.dumps(data, separators=(',', ':')),
            **({'socket_id': socket_id} if socket_id else {}),
        }, separators=(',', ':'))
        path = f'/api/apps/{app_id}/events'
        query = self.build_signed_query('POST', path, body, app_secret)

        response = self.http_client.post(
            f'{self.api_url}{path}?{urlencode(query)}',
            headers={'Content-Type': 'application/json'},
            data=body,
            timeout=self.request_timeout,
        )

        response.raise_for_status()
        return response.json()

    def build_signed_query(self, method, path, body, app_secret):
        query = {
            'auth_key': self.app_key,
            'auth_timestamp': str(int(time.time())),
            'auth_version': self.auth_version,
        }
        if body:
            query['body_md5'] = hashlib.md5(body.encode()).hexdigest()

        canonical = urlencode(sorted(query.items()))
        query['auth_signature'] = hmac.new(
            app_secret.encode(),
            f'{method.upper()}\n{path}\n{canonical}'.encode(),
            hashlib.sha256,
        ).hexdigest()

        return query

    def _authorize_channel(self, channel_name, auth_payload, headers):
        payload = {
            'socket_id': self.socket_id,
            'channel_name': channel_name,
            **auth_payload,
        }

        response = self.http_client.post(
            self.auth_endpoint,
            headers={
                'Content-Type': 'application/json',
                **self.auth_headers,
                **headers,
            },
            data=json.dumps(payload),
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        data = response.json()
        if 'auth' not in data:
            raise ValueError('Invalid auth response')
        return data

    def _send(self, data):
        if self.ws and self.connected:
            self.ws.send(json.dumps(data))

    def _handle_message(self, message):
        channel_name = message.get('channel')
        if channel_name not in self.channels:
            return

        event = message.get('event')
        data = self._safe_json_loads(message.get('data'))
        if data is None and isinstance(message.get('data'), str):
            data = message['data']
        self.channels[channel_name]._handle_event(event, data)

    def _attempt_reconnect(self):
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            self._notify_error(RuntimeError('Max reconnection attempts reached'))
            return

        delay = min(2 ** self.reconnect_attempts, 30) + random.uniform(0, 0.25)
        self.reconnect_attempts += 1
        self._notify_state_change('reconnecting', {
            'attempt': self.reconnect_attempts,
            'delay_seconds': delay,
        })
        if self.on_reconnect_attempt:
            self.on_reconnect_attempt(self.reconnect_attempts, delay)
        time.sleep(delay)

        try:
            self.connect()
        except Exception as error:
            self._notify_error(error)

    def _resubscribe_channels(self):
        if not self.socket_id:
            return

        for channel in self.channels.values():
            if channel.channel_type == 'public':
                self._send({
                    'event': 'pusher:subscribe',
                    'data': {'channel': channel.name},
                })
                continue

            auth_data = self._authorize_channel(channel.name, channel.auth_payload or {}, channel.auth_headers or {})
            payload = {
                'channel': channel.name,
                'auth': auth_data['auth'],
            }
            if 'channel_data' in auth_data:
                payload['channel_data'] = auth_data['channel_data']

            self._send({
                'event': 'pusher:subscribe',
                'data': payload,
            })

    def _build_ws_url(self):
        base = self.ws_url.rstrip('/')
        query = {
            'protocol': self.protocol,
            'auth_version': self.auth_version,
            'client': self.client,
            'version': self.version,
        }
        if self.app_id:
            query['app_id'] = self.app_id

        return f'{base}/app/{self.app_key}?{urlencode(query)}'

    def _normalize_http_url(self, value):
        if value is None:
            return None
        if not value.startswith('http://') and not value.startswith('https://'):
            raise ValueError('api_url must use http or https')
        return value.rstrip('/')

    def _normalize_ws_url(self, value):
        if value is None:
            return None
        if not value.startswith('ws://') and not value.startswith('wss://'):
            raise ValueError('ws_url must use ws or wss')
        return value.rstrip('/')

    def _safe_json_loads(self, value):
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def _log(self, level, message):
        if not self.enable_logging:
            return
        print(f'[PushStream:{level}] {message}')

    def _notify_state_change(self, state, meta):
        self._log('info', f'State changed: {state}')
        if callable(self.on_state_change):
            self.on_state_change(state, meta)

    def _notify_error(self, error):
        self._log('error', str(error))
        if callable(self.on_error):
            self.on_error(error)


class Channel:
    def __init__(self, name, client, channel_type='public', auth_payload=None, auth_headers=None):
        self.name = name
        self.client = client
        self.channel_type = channel_type
        self.auth_payload = auth_payload or {}
        self.auth_headers = auth_headers or {}
        self.event_handlers = {}

    def bind(self, event, callback):
        if event not in self.event_handlers:
            self.event_handlers[event] = []
        self.event_handlers[event].append(callback)
        return self

    def unbind(self, event, callback=None):
        if event not in self.event_handlers:
            return self

        if callback:
            self.event_handlers[event].remove(callback)
        else:
            del self.event_handlers[event]
        return self

    def _handle_event(self, event, data):
        if event not in self.event_handlers:
            return
        for handler in self.event_handlers[event]:
            handler(data)

    def unsubscribe(self):
        self.client.unsubscribe(self.name)
