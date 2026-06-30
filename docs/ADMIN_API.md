# Admin API Reference

The TPNEBOT Admin API is an in-process HTTP API built on [aiohttp](https://docs.aiohttp.org/) and exposed by `utils/admin_api.py`. It provides authenticated administrators with operational control over the running bot instance, including status checks, cog management, server (guild) operations, blacklist management, direct messaging, and a real-time WebSocket feed.

> **Security warning:** This API can perform destructive actions (shutdown, leave guilds, send DMs, manage blacklists). It should only be exposed to trusted networks and must be protected by a strong `ADMIN_API_SECRET`.

## Table of Contents

- [Enabling the API](#enabling-the-api)
- [Configuration](#configuration)
- [Authentication](#authentication)
  - [JWT session flow](#jwt-session-flow)
  - [HMAC request signatures](#hmac-request-signatures)
- [Authorization and IP restrictions](#authorization-and-ip-restrictions)
- [Rate limiting](#rate-limiting)
- [Common response formats](#common-response-formats)
- [Endpoint reference](#endpoint-reference)
  - [Authentication](#authentication-endpoints)
  - [Bot status and info](#bot-status-and-info)
  - [Cog management](#cog-management)
  - [Server (guild) operations](#server-guild-operations)
  - [Bot control](#bot-control)
  - [Blacklist](#blacklist)
  - [Dashboard](#dashboard)
  - [Confirmation tokens](#confirmation-tokens)
  - [WebSocket](#websocket)
- [High-risk action flow](#high-risk-action-flow)
- [Audit logging](#audit-logging)
- [Error codes](#error-codes)

---

## Enabling the API

The API is started automatically inside `bot.py` during the `on_ready` setup sequence (`bot.py:243`) if `ADMIN_API_SECRET` is set. It can also be started, stopped, and restarted at runtime via the owner commands in `cogs/owner.py`.

```python
# startup code path (bot.py)
api_server = AdminAPIServer(self, host=host, port=port, secret=secret)
await api_server.start()
```

If `ADMIN_API_SECRET` is not configured, the API is **disabled** and a warning is logged.

---

## Configuration

| Environment variable | Default | Description |
| --- | --- | --- |
| `ADMIN_API_SECRET` | *(none)* | **Required.** Shared secret used for login, HMAC signatures, and Fernet token encryption. |
| `ADMIN_API_HOST` | `127.0.0.1` | Hostname passed to `AdminAPIServer`. In containers this is often set to `0.0.0.0`. |
| `ADMIN_API_PORT` | `8080` | TCP port the API listens on. |
| `ADMIN_API_ALLOWLIST` | `127.0.0.0/8,::1/128` | Comma-separated list of allowed IPv4/IPv6 addresses or CIDR ranges. |
| `ADMIN_API_BIND_EXTERNAL` | `false` | Set to `true` to allow binding to a non-loopback address. Also accepts legacy alias `ADMIN_API_ALLOW_EXTERNAL_BIND`. |
| `ADMIN_API_MAX_SKEW` | `60` | Maximum allowed timestamp skew, in seconds, when validating HMAC signatures. |

### Example `.env` snippet

Only non-sensitive Admin API configuration belongs in `.env`. The actual
`ADMIN_API_SECRET` must be stored in Infisical and is fetched at runtime.

```bash
ADMIN_API_HOST=127.0.0.1
ADMIN_API_PORT=8080
ADMIN_API_ALLOWLIST=127.0.0.1/32,10.0.0.0/24
ADMIN_API_BIND_EXTERNAL=false
```

---

## Authentication

Protected endpoints require one of two authentication methods. Public endpoints are only `/auth/*` and the health/status endpoints?no, `/status` etc. are protected.

### JWT session flow

The preferred interactive flow is a bearer token obtained from `/auth/login`.

1. **Login** with the shared secret.

```bash
curl -X POST http://127.0.0.1:8080/auth/login \
  -H "Content-Type: application/json" \
  -d '{"secret": "change-me-to-a-long-random-value"}'
```

Response:

```json
{
  "token": "<encrypted-jwt-bearer-token>",
  "expires_at": "2026-06-16T01:23:45+00:00",
  "token_type": "Bearer"
}
```

2. **Use the token** on subsequent requests.

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/status
```

3. **Refresh** before expiry with `/auth/refresh`.

4. **Logout** with `/auth/logout` to invalidate the session server-side.

Sessions last **24 hours**, are refreshed when the remaining lifetime drops below 1 hour, and are stored in memory (restarting the bot invalidates all tokens).

### HMAC request signatures

Non-interactive clients may also authenticate by signing each request. This is the same secret used to log in.

Signature input:

```
<timestamp>.<METHOD>.<raw-path>
```

For example:

```
1718441000.GET./status
```

Compute `HMAC-SHA256` of that string with `ADMIN_API_SECRET`, and send:

```bash
curl -H "X-Admin-Timestamp: 1718441000" \
     -H "X-Admin-Signature: sha256=<hex-hmac>" \
     http://127.0.0.1:8080/status
```

The signature may be sent with or without the `sha256=` prefix. Timestamps older/newer than `ADMIN_API_MAX_SKEW` seconds are rejected.

> **Note:** The body is **not** included in the signature payload; only the timestamp, HTTP method, and raw path are signed.

### Legacy header authentication

For backwards compatibility, sending `X-Admin-Secret: <secret>` is also accepted. This should be avoided for production use because it transmits the secret on every request.

---

## Authorization and IP restrictions

Every protected endpoint checks the requester's IP against the `ADMIN_API_ALLOWLIST`. The default allowlist contains only loopback ranges (`127.0.0.0/8` and `::1/128`).

If the request passes through a proxy, the API inspects the `X-Forwarded-For` header and uses the first IP in the list. For direct connections, `request.remote` is used. Invalid IPs are denied.

---

## Rate limiting

A simple in-memory per-IP, per-endpoint rate limiter is applied to write endpoints:

| Endpoint(s) | Limit | Window |
| --- | --- | --- |
| `/auth/login` | 5 attempts | 60 seconds |
| `/cogs/load`, `/cogs/unload`, `/cogs/reload` | 5 requests | 30 seconds |
| `/server/{guild_id}/leave` | 2 requests | 60 seconds |
| `/blacklist` GET | 10 requests | 30 seconds |
| `/blacklist` POST | 5 requests | 60 seconds |
| `/confirmations/*` | 3 requests | 60 seconds |
| `/dashboard/*` | 10 requests | 30 seconds |

When limited, the API returns `429 Too Many Requests` with `{"error": "rate limited"}`.

---

## Common response formats

Success responses are JSON objects (`application/json`).

Standard error envelope:

```json
{
  "error": "description"
}
```

HTTP status codes used:

- `200` - OK
- `400` - Bad request / malformed input
- `401` - Unauthorized (missing/invalid auth or IP not allowed)
- `403` - Forbidden (action not allowed, e.g. disallowed confirmation action)
- `404` - Guild not found
- `429` - Rate limited
- `500` - Internal server error
- `501` - Not implemented
- `503` - Service unavailable (database not available)

---

## Endpoint reference

### Authentication endpoints

#### `POST /auth/login`

Exchange the shared secret for a JWT bearer token.

**Body:**

```json
{
  "secret": "string"
}
```

**Response (200):**

```json
{
  "token": "string",
  "expires_at": "2026-06-16T01:23:45+00:00",
  "token_type": "Bearer"
}
```

#### `POST /auth/logout`

Invalidate the current bearer token's session.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**

```json
{
  "status": "logged out"
}
```

#### `POST /auth/refresh`

Refresh the bearer token and extend the session lifetime. If the session is still fresh, it returns a new token without extending expiry.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**

```json
{
  "token": "string",
  "expires_at": "2026-06-16T02:23:45+00:00"
}
```

---

### Bot status and info

#### `GET /status`

Basic bot runtime status.

**Response (200):**

```json
{
  "uptime": "unknown",
  "guild_count": 3,
  "user_count": 150
}
```

#### `GET /cogs`

List all cog files in the `cogs/` directory and their load state.

**Response (200):**

```json
{
  "cogs": [
    {
      "name": "economy",
      "loaded": true,
      "command_count": 12
    }
  ]
}
```

#### `GET /servers`

List guilds the bot is currently in.

**Response (200):**

```json
{
  "servers": [
    {
      "id": "123456789012345678",
      "name": "My Server",
      "member_count": 42
    }
  ]
}
```

#### `GET /server/{guild_id}`

Detailed information about a specific guild.

**Response (200):**

```json
{
  "id": "123456789012345678",
  "name": "My Server",
  "member_count": 42,
  "text_channels": 5,
  "voice_channels": 2,
  "roles": 8,
  "emojis": 12,
  "boosts": 1
}
```

---

### Cog management

#### `POST /cogs/load`

Load one or more cogs dynamically.

**Body:**

```json
{
  "cogs": ["economy", "casino"]
}
```

The `cogs` field may also be a comma/space separated string, e.g. `"economy, casino"`.

**Response (200):**

```json
{
  "loaded": ["economy"],
  "failed": {
    "casino": "already loaded"
  }
}
```

Cog names are validated to prevent path traversal: alphanumeric and underscore only, length <= 50, and may not start with an underscore or digit.

#### `POST /cogs/unload`

Unload one or more cogs.

**Body:** same as `/cogs/load`.

**Response (200):**

```json
{
  "unloaded": ["casino"],
  "failed": {}
}
```

#### `POST /cogs/reload`

Reload one or more already loaded cogs.

**Body:** same as `/cogs/load`.

**Response (200):**

```json
{
  "reloaded": ["economy"],
  "failed": {}
}
```

---

### Server (guild) operations

#### `POST /server/{guild_id}/leave`

Make the bot leave the specified guild.

**Response (200):**

```json
{
  "status": "left"
}
```

#### `POST /invite/create`

Create an instant invite in a guild where the bot has permission.

**Body:**

```json
{
  "guild_id": "123456789012345678",
  "max_age": 86400,
  "max_uses": 1
}
```

Defaults: `max_age=86400` (24 hours), `max_uses=1`.

**Response (200):**

```json
{
  "invite": "https://discord.gg/AbCdEfG"
}
```

---

### Bot control

#### `POST /presence/set`

Update the bot's Discord presence.

**Body:**

```json
{
  "type": "game",
  "text": "with the economy",
  "status": "online"
}
```

- `type`: `game`, `watching`, or `listening`.
- `status`: optional, one of discord.py's `Status` values (`online`, `idle`, `dnd`, `offline`, `invisible`).

**Response (200):**

```json
{
  "status": "ok"
}
```

#### `POST /dm`

Send a direct message to a Discord user by ID.

**Body:**

```json
{
  "user_id": "123456789012345678",
  "message": "Hello from the admin API"
}
```

**Response (200):**

```json
{
  "status": "sent"
}
```

#### `POST /shutdown`

Gracefully shut down the bot. This endpoint uses the [confirmation-token flow](#high-risk-action-flow).

**Body (request token):**

```json
{}
```

or

```json
{
  "confirm_token": "<token>"
}
```

When no token is provided, a confirmation token is returned. When a valid token is provided, the shutdown task is scheduled.

**Response (request token, 200):**

```json
{
  "confirm_token": "<uuid-hex>",
  "expires_in": 600
}
```

**Response (execute, 200):**

```json
{
  "status": "shutdown scheduled"
}
```

---

### Blacklist

#### `GET /blacklist`

List blacklisted users with optional search and pagination.

**Query parameters:**

- `page` (int, default `1`)
- `per_page` (int, default `20`, max `100`)
- `search` (string, optional) - filters by `user_id` or reason

**Response (200):**

```json
{
  "blacklist": [
    {
      "id": 1,
      "user_id": "123456789012345678",
      "reason": "Spam"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total_entries": 1,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

#### `POST /blacklist`

Add or remove a user from the blacklist. Changes require a confirmation token.

**Body (create confirmation):**

```json
{
  "action": "add",
  "user_id": "123456789012345678",
  "reason": "Spam"
}
```

**Body (execute with token):**

```json
{
  "action": "add",
  "confirm_token": "<token>"
}
```

- `action`: `add` or `remove`.
- `reason`: optional when adding; defaults to `"No reason provided"`.

**Response (create confirmation, 200):**

```json
{
  "confirm_token": "<token>",
  "expires_in": 300,
  "action": "add",
  "params": {
    "user_id": 123456789012345678,
    "reason": "Spam"
  }
}
```

**Response (execute, 200):**

```json
{
  "status": "added",
  "user_id": "123456789012345678"
}
```

---

### Dashboard

#### `GET /dashboard/stats`

Return bot and economy statistics.

**Response (200):**

```json
{
  "treasury_balance": "10000",
  "supply": {
    "total_supply": "500000",
    "circulating_supply": "450000"
  },
  "economic_factors": {},
  "games": {
    "total_wins": 100,
    "total_losses": 50
  },
  "bot": {
    "guilds": 3,
    "users": 150,
    "latency_ms": 45.23
  }
}
```

#### `GET /dashboard/health`

System health check covering database, Discord connection, and economy.

**Response (200):**

```json
{
  "status": "healthy",
  "components": {
    "database": {
      "status": "healthy"
    },
    "discord": {
      "status": "healthy",
      "latency_ms": 45.23
    },
    "economy": {
      "status": "healthy",
      "score": 85,
      "status_text": "Good"
    }
  },
  "timestamp": "2026-06-15T12:34:56+00:00"
}
```

Top-level `status` values: `healthy`, `degraded`, `unhealthy`.

#### `GET /dashboard/trends`

Economic and game trends over a configurable period.

**Query parameters:**

- `days` (int, default `7`, range `1-90`)

**Response (200):**

```json
{
  "economic": {},
  "games": {},
  "period_days": 7,
  "generated_at": "2026-06-15T12:34:56+00:00"
}
```

---

### Confirmation tokens

#### `POST /confirmations/create`

Create a generic confirmation token for a high-risk action.

**Body:**

```json
{
  "action": "shutdown",
  "params": {}
}
```

Allowed actions: `shutdown`.

**Response (200):**

```json
{
  "confirm_token": "<token>",
  "expires_in": 600
}
```

#### `POST /confirmations/execute`

Execute a pending confirmation token.

**Body:**

```json
{
  "confirm_token": "<token>"
}
```

**Response (200):** depends on the action; for `shutdown`:

```json
{
  "status": "shutdown scheduled"
}
```

---

### WebSocket

#### `GET /ws`

Open a real-time WebSocket connection for admin panel updates.

**Authentication:** pass the bearer token either as a query parameter (`?token=<token>`) or in the `Authorization: Bearer <token>` header.

**Client -> Server message types:**

| Type | Payload | Description |
| --- | --- | --- |
| `ping` | `{}` | Server responds with `pong`. |
| `subscribe` | `{"channels": ["dashboard", "blacklist"]}` | Acknowledge subscription request. (Channel filtering is not yet implemented.) |
| `request_update` | `{"update_type": "dashboard_stats"}` or `{"update_type": "blacklist"}` | Request an immediate data push. |

**Server -> Client message types:**

| Type | Description |
| --- | --- |
| `connected` | Welcome message after successful authentication. |
| `pong` | Heartbeat/latency response. |
| `subscribed` | Subscription acknowledgement. |
| `dashboard_update` | Pushed dashboard stats object. |
| `blacklist_update` | Pushed blacklist snapshot. |
| `error` | Error response. |

The connection is closed with code `4001` if authentication fails. Server sends heartbeats every 30 seconds.

---

## High-risk action flow

Some actions cannot be executed directly; they require a two-step confirmation token to reduce accidental or automated misuse. This applies to:

- `POST /shutdown`
- `POST /blacklist` (add/remove)
- `POST /confirmations/create` + `POST /confirmations/execute` for `shutdown`

### Two-step example: shutting down the bot

**Step 1:** Request a confirmation token.

```bash
curl -X POST http://127.0.0.1:8080/shutdown \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Response:

```json
{
  "confirm_token": "abc123...",
  "expires_in": 600
}
```

**Step 2:** Execute the token within 10 minutes.

```bash
curl -X POST http://127.0.0.1:8080/shutdown \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"confirm_token": "abc123..."}'
```

Blacklist changes use a 5-minute token expiry instead of 10 minutes.

---

## Audit logging

Every authentication event and every privileged write operation is appended to `admin_api_audit.log` in the working directory as a single JSON line per event. Example:

```json
{"action": "auth.login", "status": "success", "session_id": "...", "ip": "127.0.0.1", "timestamp": "2026-06-15T12:34:56+00:00"}
```

Logged events include:

- `auth.login` (success/failed)
- `auth.logout`
- `auth.refresh`
- `cog_loaded` / `cog_load_failed`
- `cog_unloaded` / `cog_unload_failed`
- `cog_reloaded` / `cog_reload_failed`
- `server_left`
- `invite_created`
- `blacklist_list`
- `blacklist_add` / `blacklist_remove`
- `confirmation_requested`
- `shutdown_requested` / `shutdown_executed`
- `dashboard_stats` / `dashboard_health` / `dashboard_trends`
- `websocket_connect` / `websocket_disconnect`

---

## Error codes

| `error` value | Meaning |
| --- | --- |
| `unauthorized` | Missing/invalid authentication or IP not allowed. |
| `invalid secret` | Login secret mismatch. |
| `invalid JSON body` / `invalid json` | Request body is not valid JSON. |
| `bad request` | Generic malformed request. |
| `cogs missing` | `cogs` field absent in cog management request. |
| `invalid cog name` | Cog name failed validation. |
| `already loaded` / `not loaded` | Cog is in the wrong load state for the operation. |
| `file not found` | Cog file does not exist in `cogs/`. |
| `guild not found` | Specified guild ID was not found. |
| `no suitable channel` | No guild channel allows invite creation. |
| `failed to create invite` | Discord rejected invite creation. |
| `failed to send dm` | DM could not be sent. |
| `rate limited` | IP/endpoint rate limit hit. |
| `invalid_or_expired_token` | Confirmation token missing, expired, or mismatched action. |
| `db not available` / `database_unavailable` | Database manager is missing. |
| `action not allowed` | Generic confirmation action is not in the allowlist. |
| `action_not_implemented` | Confirmation action is recognized but has no handler. |
| `internal` | Unexpected server error; check `discord.log` for traceback. |

---

## Operational notes

- The API is single-process. Restarting the bot clears sessions, rate-limit counters, pending confirmations, and WebSocket connections.
- Binding to `0.0.0.0` requires `ADMIN_API_BIND_EXTERNAL=true`; otherwise `AdminAPIServer.start()` overrides the configured host with `127.0.0.1`.
- Place the API behind a reverse proxy with TLS if exposing it beyond localhost. Configure the proxy to set `X-Forwarded-For` and restrict access at the network layer.
- Do not commit `ADMIN_API_SECRET` to version control. Rotate it immediately if exposed.
