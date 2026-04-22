# PushStream Python SDK

Python client for PushStream websocket connections plus a server-side publish helper.

## Current Status

This package now targets the current PushStream contract:

- websocket path `/app/{app_key}`
- websocket params `protocol`, `auth_version`, `client`, `version`
- publish signing via `auth_key`, `auth_timestamp`, `auth_version`, `body_md5`, `auth_signature`

## Install

```bash
pip install pushstream-python
```

## Realtime Usage

```python
from pushstream import PushStream

client = PushStream(
    'your-app-key',
    app_id='your-app-id',
    ws_url='wss://ws.pushstream.online',
    auth_endpoint='https://api.pushstream.online/api/apps/your-app-id/auth',
)

client.connect()
channel = client.subscribe('public-orders')
channel.bind('order.created', lambda data: print(data))
```

Use `subscribe_authenticated()` for private and presence channels.

## Server-Side Publish

```python
client = PushStream(
    'your-app-key',
    api_url='https://api.pushstream.online',
)

client.publish(
    'your-app-id',
    'your-app-secret',
    'public-orders',
    'order.created',
    {'id': 1},
)
```

Do not expose app secrets in browser or mobile code.
