import json
import time
import hmac
import hashlib
import requests
from websocket import WebSocketApp
from threading import Thread

class PushStream:
    def __init__(self, app_key, ws_url='ws://localhost:3001', api_url='http://localhost:8000'):
        self.app_key = app_key
        self.ws_url = ws_url
        self.api_url = api_url
        self.ws = None
        self.socket_id = None
        self.channels = {}
        self.connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.should_reconnect = True

    def connect(self):
        def on_message(ws, message):
            data = json.loads(message)
            
            if data['event'] == 'pusher:connection_established':
                payload = json.loads(data['data'])
                self.socket_id = payload['socket_id']
                self.connected = True
                print(f"[PushStream] Connected: {self.socket_id}")
            elif data['event'] == 'pusher:error':
                print(f"[PushStream] Error: {data['data']}")
            else:
                self._handle_message(data)

        def on_error(ws, error):
            print(f"[PushStream] Error: {error}")

        def on_close(ws, close_status_code, close_msg):
            print("[PushStream] Disconnected")
            self.connected = False
            if self.should_reconnect:
                self._attempt_reconnect()

        def on_open(ws):
            print("[PushStream] Connection opened")

        self.ws = WebSocketApp(
            self.ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )

        ws_thread = Thread(target=self.ws.run_forever)
        ws_thread.daemon = True
        ws_thread.start()

        # Wait for connection
        start_time = time.time()
        while not self.connected and (time.time() - start_time) < 10:
            time.sleep(0.1)

        if not self.connected:
            self._attempt_reconnect()
            raise TimeoutError('Connection timeout')

        return self.socket_id

    def _attempt_reconnect(self):
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            print('[PushStream] Max reconnection attempts reached')
            return

        delay = min(2 ** self.reconnect_attempts, 30)
        self.reconnect_attempts += 1

        print(f'[PushStream] Reconnecting in {delay}s (attempt {self.reconnect_attempts})')
        time.sleep(delay)
        
        try:
            self.connect()
        except Exception as e:
            print(f'[PushStream] Reconnect failed: {e}')

    def subscribe(self, channel_name):
        if not self.connected:
            raise RuntimeError('Not connected')
        
        channel = Channel(channel_name, self)
        self.channels[channel_name] = channel
        
        self._send({
            'event': 'pusher:subscribe',
            'data': {'channel': channel_name}
        })

        return channel

    def unsubscribe(self, channel_name):
        self._send({
            'event': 'pusher:unsubscribe',
            'data': {'channel': channel_name}
        })
        if channel_name in self.channels:
            del self.channels[channel_name]

    def _send(self, data):
        if self.ws and self.connected:
            self.ws.send(json.dumps(data))

    def _handle_message(self, message):
        channel_name = message.get('channel')
        if channel_name in self.channels:
            event = message['event']
            data = json.loads(message['data'])
            self.channels[channel_name]._handle_event(event, data)

    def disconnect(self):
        self.should_reconnect = False
        if self.ws:
            self.ws.close()
            self.ws = None
        self.connected = False

    def publish(self, app_id, app_secret, channel, event, data):
        timestamp = int(time.time())
        body = json.dumps({'name': event, 'channel': channel, 'data': data})
        path = f'/api/apps/{app_id}/events'
        query_string = f'auth_timestamp={timestamp}'
        string_to_sign = f'POST\n{path}\n{query_string}\n{body}'
        
        signature = hmac.new(
            app_secret.encode(),
            string_to_sign.encode(),
            hashlib.sha256
        ).hexdigest()
        
        auth_header = f'{app_id}:{signature}'
        
        response = requests.post(
            f'{self.api_url}{path}?{query_string}',
            headers={
                'Authorization': auth_header,
                'Content-Type': 'application/json'
            },
            data=body
        )
        
        response.raise_for_status()
        return response.json()


class Channel:
    def __init__(self, name, client):
        self.name = name
        self.client = client
        self.event_handlers = {}

    def bind(self, event, callback):
        if event not in self.event_handlers:
            self.event_handlers[event] = []
        self.event_handlers[event].append(callback)
        return self

    def unbind(self, event, callback=None):
        if event not in self.event_handlers:
            return
        
        if callback:
            self.event_handlers[event].remove(callback)
        else:
            del self.event_handlers[event]
        return self

    def _handle_event(self, event, data):
        if event in self.event_handlers:
            for handler in self.event_handlers[event]:
                handler(data)

    def unsubscribe(self):
        self.client.unsubscribe(self.name)
