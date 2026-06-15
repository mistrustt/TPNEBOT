import asyncio
import json
import logging
import os
import uuid
import time
import hmac
import hashlib
import ipaddress
import secrets
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Set

import discord
from aiohttp import web
from sqlalchemy import text
from cryptography.fernet import Fernet

logger = logging.getLogger("admin_api")

class AdminAPIServer:
    def __init__(self, bot, host: str = "127.0.0.1", port: int = 8080, secret: Optional[str] = None):
        self.bot = bot
        self.host = host
        self.port = port
        # require secret from parameter or env
        self.secret = secret or os.getenv("ADMIN_API_SECRET")
        if not self.secret:
            raise ValueError("ADMIN_API_SECRET must be set to start the Admin API")

        # JWT Session Management (in-memory)
        # Sessions stored as: {session_id: {"created_at": datetime, "expires_at": datetime, "last_activity": datetime}}
        self._sessions: Dict[str, Dict[str, datetime]] = {}
        self._session_timeout = timedelta(hours=24)  # Sessions expire after 24 hours
        self._session_refresh_threshold = timedelta(hours=1)  # Refresh if < 1 hour remaining
        
        # Generate Fernet key for JWT token encryption (derived from secret)
        # Using SHA256 to ensure key is exactly 32 bytes for Fernet
        import hashlib
        key = hashlib.sha256(self.secret.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

        # parse optional IP allowlist (comma separated CIDR/IPs) - default to loopback only
        allowlist_env = os.getenv("ADMIN_API_ALLOWLIST")
        if allowlist_env:
            parts = [p.strip() for p in allowlist_env.split(",") if p.strip()]
            parsed = []
            for p in parts:
                try:
                    if "/" in p:
                        parsed.append(ipaddress.ip_network(p, strict=False))
                    else:
                        parsed.append(ipaddress.ip_network(p + "/32"))
                except Exception:
                    logger.warning("Invalid allowlist entry: %s", p)
            self._ip_allowlist = parsed
        else:
            # default allowlist: local loopback only
            self._ip_allowlist = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]
        self.runner = None
        self.site = None
        self.app = web.Application()
        self._ws_connections: Set[web.WebSocketResponse] = set()  # Active WebSocket connections
        self._setup_routes()

    def _setup_routes(self):
        # Authentication endpoints (public)
        self.app.router.add_post("/auth/login", self.handle_auth_login)
        self.app.router.add_post("/auth/logout", self.handle_auth_logout)
        self.app.router.add_post("/auth/refresh", self.handle_auth_refresh)

        # Low-risk (require JWT auth)
        self.app.router.add_get("/status", self.handle_status)
        self.app.router.add_get("/cogs", self.handle_cogs)
        self.app.router.add_get("/servers", self.handle_servers)
        self.app.router.add_get(r"/server/{guild_id:\d+}", self.handle_server)

        # Medium/controlled (require JWT auth)
        self.app.router.add_post("/shutdown", self.handle_shutdown)
        self.app.router.add_post("/cogs/load", self.handle_cogs_load)
        self.app.router.add_post("/cogs/unload", self.handle_cogs_unload)
        self.app.router.add_post("/cogs/reload", self.handle_cogs_reload)
        self.app.router.add_post(r"/server/{guild_id:\d+}/leave", self.handle_server_leave)
        self.app.router.add_post("/invite/create", self.handle_invite_create)
        self.app.router.add_post("/presence/set", self.handle_presence_set)
        self.app.router.add_post("/dm", self.handle_dm)
        # Blacklist endpoints
        self.app.router.add_get("/blacklist", self.handle_blacklist_get)
        self.app.router.add_post("/blacklist", self.handle_blacklist_post)
        # Confirmation endpoints for high-risk actions
        self.app.router.add_post("/confirmations/create", self.handle_confirmation_create)
        self.app.router.add_post("/confirmations/execute", self.handle_confirmation_execute)
        # Dashboard endpoints
        self.app.router.add_get("/dashboard/stats", self.handle_dashboard_stats)
        self.app.router.add_get("/dashboard/health", self.handle_dashboard_health)
        self.app.router.add_get("/dashboard/trends", self.handle_dashboard_trends)
        # WebSocket endpoint for real-time updates
        self.app.router.add_get("/ws", self.handle_websocket)
        
        # Add request/response logging middleware
        self.app.middlewares.append(self._logging_middleware)
    
    @web.middleware
    async def _logging_middleware(self, request: web.Request, handler):
        """Middleware to log all requests and responses."""
        start_time = time.time()
        ip = request.remote or request.headers.get("X-Forwarded-For", "-")
        method = request.method
        path = request.path
        
        # Log incoming request
        logger.info(f"Request: {method} {path} from {ip}")
        
        try:
            response = await handler(request)
            duration_ms = (time.time() - start_time) * 1000
            logger.info(f"Response: {method} {path} - Status {response.status} - {duration_ms:.2f}ms")
            return response
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.exception(f"Error: {method} {path} - {type(e).__name__}: {e} - {duration_ms:.2f}ms")
            raise

    async def handle_status(self, request: web.Request) -> web.Response:
        uptime = None
        try:
            uptime = (self.bot.owner_cog.start_time and (web.json_response))
        except Exception:
            pass

        data = {
            "uptime": str(getattr(self.bot, "uptime", "unknown")),
            "guild_count": len(self.bot.guilds) if hasattr(self.bot, "guilds") else 0,
            "user_count": sum(g.member_count for g in self.bot.guilds) if hasattr(self.bot, "guilds") else 0,
        }
        return web.json_response(data)

    def _unauthorized(self) -> web.Response:
        return web.json_response({"error": "unauthorized"}, status=401)

    def _bad_request(self, msg: str = "bad request") -> web.Response:
        return web.json_response({"error": msg}, status=400)

    async def _require_auth(self, request: web.Request) -> bool:
        # first, reject if requester IP not allowed
        try:
            if not self._ip_allowed(request):
                return False
        except Exception:
            # on error, deny
            return False

        # Prefer HMAC signature flow: X-Admin-Signature and X-Admin-Timestamp
        sig = request.headers.get("X-Admin-Signature")
        ts = request.headers.get("X-Admin-Timestamp")
        if sig and ts:
            try:
                # timestamp must be int-ish
                tstamp = int(ts)
            except Exception:
                return False
            # enforce skew
            max_skew = int(os.getenv("ADMIN_API_MAX_SKEW", "60"))
            if abs(int(time.time()) - tstamp) > max_skew:
                return False

            # compute expected HMAC over: <timestamp>.<method>.<raw_path>
            # NOTE: do NOT read the body here to avoid consuming it for handlers.
            try:
                msg = b"%d.%b.%b" % (tstamp, request.method.encode(), request.raw_path.encode())
            except Exception:
                try:
                    msg = f"{tstamp}.{request.method}.{request.raw_path}".encode()
                except Exception:
                    return False

            expected = hmac.new(self.secret.encode(), msg, hashlib.sha256).hexdigest()
            # signature may be prefixed with sha256=
            if sig.startswith("sha256="):
                sig_val = sig.split("=", 1)[1]
            else:
                sig_val = sig
            return hmac.compare_digest(expected, sig_val)

        # fallback to legacy header
        hdr = request.headers.get("X-Admin-Secret")
        return bool(self.secret and hdr == self.secret)

    def _ip_allowed(self, request: web.Request) -> bool:
        # allow if remote IP is within any network in the allowlist
        remote = request.remote or request.headers.get("X-Forwarded-For")
        if not remote:
            return False
        # if header contains comma-separated list, take first
        if "," in remote:
            remote = remote.split(",")[0].strip()
        try:
            ip = ipaddress.ip_address(remote)
        except Exception:
            return False
        for net in getattr(self, "_ip_allowlist", []):
            try:
                if ip in net:
                    return True
            except Exception:
                continue
        return False

    def _log_audit(self, entry: Dict[str, Any]) -> None:
        try:
            path = os.path.join(os.getcwd(), "admin_api_audit.log")
            entry["timestamp"] = discord.utils.utcnow().isoformat()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            logger.exception("failed to write audit log")

    def _make_token(self, action: str, params: Dict[str, Any], requester: str) -> str:
        token = uuid.uuid4().hex
        now = time.time()
        # store in-memory; simple structure
        if not hasattr(self, "_confirmations"):
            self._confirmations = {}
        self._confirmations[token] = {"action": action, "params": params, "requested_at": now, "requester": requester, "expires_at": now + 600}
        return token

    def _pop_confirmation(self, token: str, expected_action: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not hasattr(self, "_confirmations"):
            return None
        data = self._confirmations.get(token)
        if not data:
            return None
        if expected_action and data.get("action") != expected_action:
            return None
        if data.get("expires_at", 0) < time.time():
            try:
                del self._confirmations[token]
            except Exception:
                pass
            return None
        # consume token
        try:
            del self._confirmations[token]
        except Exception:
            pass
        return data

    def _rate_limited(self, request: web.Request, limit: int = 20, window: int = 60) -> bool:
        # very simple in-memory per-IP per-endpoint rate limiting
        ip = request.remote or request.headers.get("X-Forwarded-For", "-")
        key = f"{ip}:{request.path}"
        now = time.time()
        if not hasattr(self, "_ratemap"):
            self._ratemap = {}
        arr = self._ratemap.get(key, [])
        # drop old
        arr = [t for t in arr if now - t < window]
        if len(arr) >= limit:
            self._ratemap[key] = arr
            return True
        arr.append(now)
        self._ratemap[key] = arr
        return False

    def _validate_cog_name(self, name: str) -> bool:
        """Validate cog name to prevent path traversal attacks."""
        if not name or len(name) > 50:
            return False
        # Only allow alphanumeric and underscore
        if not name.replace("_", "").isalnum():
            return False
        # Reject names starting with underscore or digit
        if name[0] in "_0123456789":
            return False
        return True

    # ==================== JWT Session Management ====================

    def _create_jwt_token(self, session_id: str) -> str:
        """Create an encrypted JWT token for a session."""
        payload = {
            "session_id": session_id,
            "iat": int(time.time()),
        }
        token_data = json.dumps(payload).encode()
        return self._fernet.encrypt(token_data).decode()

    def _validate_jwt_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate and decrypt a JWT token. Returns payload or None if invalid."""
        try:
            decrypted = self._fernet.decrypt(token.encode())
            payload = json.loads(decrypted)
            session_id = payload.get("session_id")
            if not session_id:
                return None
            # Check if session exists and is valid
            session = self._sessions.get(session_id)
            if not session:
                return None
            # Check expiration
            if session["expires_at"] < datetime.now(timezone.utc):
                del self._sessions[session_id]
                return None
            return payload
        except Exception:
            return None

    def _create_session(self) -> Dict[str, str]:
        """Create a new session and return session_id + JWT token."""
        session_id = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + self._session_timeout

        self._sessions[session_id] = {
            "created_at": now,
            "expires_at": expires_at,
            "last_activity": now,
        }

        token = self._create_jwt_token(session_id)
        return {"session_id": session_id, "token": token, "expires_at": expires_at.isoformat()}

    def _cleanup_expired_sessions(self) -> int:
        """Remove expired sessions. Returns count of removed sessions."""
        now = datetime.now(timezone.utc)
        expired = [sid for sid, sess in self._sessions.items() if sess["expires_at"] < now]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)

    def _get_session(self, request: web.Request) -> Optional[Dict[str, Any]]:
        """Extract and validate session from Authorization header."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[7:]  # Remove "Bearer " prefix
        return self._validate_jwt_token(token)

    def _update_session_activity(self, session_id: str) -> None:
        """Update last_activity timestamp for a session."""
        session = self._sessions.get(session_id)
        if session:
            session["last_activity"] = datetime.now(timezone.utc)

    def _invalidate_session(self, session_id: str) -> bool:
        """Invalidate a session. Returns True if session existed."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    async def _require_jwt_auth(self, request: web.Request) -> Optional[Dict[str, Any]]:
        """
        Validate JWT session for protected endpoints.
        Returns session payload if valid, None if unauthorized.
        Also updates session activity and performs cleanup.
        """
        # Check IP allowlist first
        if not self._ip_allowed(request):
            return None

        # Get and validate session
        payload = self._get_session(request)
        if not payload:
            return None

        session_id = payload.get("session_id")
        if not session_id:
            return None

        # Update activity timestamp
        self._update_session_activity(session_id)

        return payload

    # ==================== Auth Endpoints ====================

    async def handle_auth_login(self, request: web.Request) -> web.Response:
        """Handle login with admin secret to obtain JWT token."""
        # Rate limit login attempts
        if self._rate_limited(request, limit=5, window=60):
            return web.json_response({"error": "rate limited"}, status=429)

        # Check IP allowlist
        if not self._ip_allowed(request):
            return self._unauthorized()

        try:
            data = await request.json()
        except Exception:
            return self._bad_request("invalid JSON body")

        secret = data.get("secret")
        if not secret:
            return self._bad_request("secret required")

        # Validate secret (timing-safe comparison)
        if not hmac.compare_digest(secret, self.secret):
            self._log_audit({"action": "auth.login", "status": "failed", "ip": request.remote})
            return web.json_response({"error": "invalid secret"}, status=401)

        # Cleanup expired sessions periodically
        self._cleanup_expired_sessions()

        # Create new session
        session_data = self._create_session()

        self._log_audit({
            "action": "auth.login",
            "status": "success",
            "session_id": session_data["session_id"],
            "ip": request.remote,
        })

        return web.json_response({
            "token": session_data["token"],
            "expires_at": session_data["expires_at"],
            "token_type": "Bearer",
        })

    async def handle_auth_logout(self, request: web.Request) -> web.Response:
        """Invalidate current session."""
        payload = self._get_session(request)
        if not payload:
            return self._unauthorized()

        session_id = payload.get("session_id")
        self._invalidate_session(session_id)

        self._log_audit({
            "action": "auth.logout",
            "session_id": session_id,
            "ip": request.remote,
        })

        return web.json_response({"status": "logged out"})

    async def handle_auth_refresh(self, request: web.Request) -> web.Response:
        """Refresh JWT token, extending session lifetime."""
        payload = self._get_session(request)
        if not payload:
            return self._unauthorized()

        session_id = payload.get("session_id")
        session = self._sessions.get(session_id)
        if not session:
            return self._unauthorized()

        # Check if session is within refresh threshold
        now = datetime.now(timezone.utc)
        time_until_expiry = session["expires_at"] - now
        if time_until_expiry > self._session_timeout - self._session_refresh_threshold:
            # Session is still fresh, just return new token
            token = self._create_jwt_token(session_id)
            self._update_session_activity(session_id)
            return web.json_response({
                "token": token,
                "expires_at": session["expires_at"].isoformat(),
            })

        # Extend session lifetime
        session["expires_at"] = now + self._session_timeout
        session["last_activity"] = now
        token = self._create_jwt_token(session_id)

        self._log_audit({
            "action": "auth.refresh",
            "session_id": session_id,
            "ip": request.remote,
        })

        return web.json_response({
            "token": token,
            "expires_at": session["expires_at"].isoformat(),
        })

    # ==================== Protected Endpoint Handlers ====================

    async def handle_cogs(self, request: web.Request) -> web.Response:
        try:
            cogs_dir = os.path.join(os.getcwd(), "cogs")
            files = [f for f in os.listdir(cogs_dir) if f.endswith(".py")] if os.path.isdir(cogs_dir) else []
            names = [os.path.splitext(f)[0] for f in files]
            result = []
            for name in names:
                loaded = f"cogs.{name}" in getattr(self.bot, "extensions", {})
                # try to get command count
                cmd_count = 0
                cog_obj = self.bot.cogs.get(name)
                if cog_obj:
                    try:
                        cmd_count = len(cog_obj.get_commands())
                    except Exception:
                        cmd_count = 0
                result.append({"name": name, "loaded": bool(loaded), "command_count": cmd_count})
            return web.json_response({"cogs": result})
        except Exception as e:
            logger.exception("handle_cogs error: %s", e)
            return web.json_response({"error": "internal"}, status=500)

    async def handle_servers(self, request: web.Request) -> web.Response:
        try:
            out = []
            for g in list(self.bot.guilds):
                out.append({"id": g.id, "name": g.name, "member_count": g.member_count})
            return web.json_response({"servers": out})
        except Exception as e:
            logger.exception("handle_servers error: %s", e)
            return web.json_response({"error": "internal"}, status=500)

    async def handle_server(self, request: web.Request) -> web.Response:
        try:
            gid = int(request.match_info["guild_id"])
            g = self.bot.get_guild(gid)
            if not g:
                return web.json_response({"error": "guild not found"}, status=404)
            data = {
                "id": g.id,
                "name": g.name,
                "member_count": g.member_count,
                "text_channels": len(g.text_channels),
                "voice_channels": len(g.voice_channels),
                "roles": len(g.roles),
                "emojis": len(g.emojis),
                "boosts": getattr(g, "premium_subscription_count", None),
            }
            return web.json_response(data)
        except Exception as e:
            logger.exception("handle_server error: %s", e)
            return web.json_response({"error": "internal"}, status=500)

    async def handle_cogs_load(self, request: web.Request) -> web.Response:
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=5, window=30):
            return web.json_response({"error": "rate_limited"}, status=429)
        try:
            body = await request.json()
        except Exception:
            return self._bad_request("invalid json")
        names = []
        if isinstance(body.get("cogs"), list):
            names = body.get("cogs")
        elif isinstance(body.get("cogs"), str):
            names = [c.strip() for c in body.get("cogs").replace(",", " ").split() if c.strip()]
        else:
            return self._bad_request("cogs missing")

        succeeded: List[str] = []
        failed: Dict[str, str] = {}
        for name in names:
            if not self._validate_cog_name(name):
                failed[name] = "invalid cog name"
                continue
            try:
                path = f"cogs.{name}"
                if path in getattr(self.bot, "extensions", {}):
                    failed[name] = "already loaded"
                    continue
                if not os.path.exists(os.path.join("cogs", f"{name}.py")):
                    failed[name] = "file not found"
                    continue
                await self.bot.load_extension(path)
                # optional DB call
                db = getattr(self.bot, "database", None)
                if db and hasattr(db, "load_cog"):
                    try:
                        await db.load_cog(name)
                    except Exception:
                        pass
                succeeded.append(name)
                self._log_audit({"event": "cog_loaded", "cog": name, "by": request.remote})
            except Exception as e:
                logger.exception("load cog %s failed: %s", name, e)
                failed[name] = str(e)
                self._log_audit({"event": "cog_load_failed", "cog": name, "error": str(e), "by": request.remote})
        return web.json_response({"loaded": succeeded, "failed": failed})

    async def handle_cogs_unload(self, request: web.Request) -> web.Response:
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=5, window=30):
            return web.json_response({"error": "rate_limited"}, status=429)
        try:
            body = await request.json()
        except Exception:
            return self._bad_request("invalid json")
        names = []
        if isinstance(body.get("cogs"), list):
            names = body.get("cogs")
        elif isinstance(body.get("cogs"), str):
            names = [c.strip() for c in body.get("cogs").replace(",", " ").split() if c.strip()]
        else:
            return self._bad_request("cogs missing")

        succeeded: List[str] = []
        failed: Dict[str, str] = {}
        for name in names:
            if not self._validate_cog_name(name):
                failed[name] = "invalid cog name"
                continue
            try:
                path = f"cogs.{name}"
                if path not in getattr(self.bot, "extensions", {}):
                    failed[name] = "not loaded"
                    continue
                await self.bot.unload_extension(path)
                db = getattr(self.bot, "database", None)
                if db and hasattr(db, "unload_cog"):
                    try:
                        await db.unload_cog(name)
                    except Exception:
                        pass
                succeeded.append(name)
                self._log_audit({"event": "cog_unloaded", "cog": name, "by": request.remote})
            except Exception as e:
                logger.exception("unload cog %s failed: %s", name, e)
                failed[name] = str(e)
                self._log_audit({"event": "cog_unload_failed", "cog": name, "error": str(e), "by": request.remote})
        return web.json_response({"unloaded": succeeded, "failed": failed})

    async def handle_cogs_reload(self, request: web.Request) -> web.Response:
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=5, window=30):
            return web.json_response({"error": "rate_limited"}, status=429)
        try:
            body = await request.json()
        except Exception:
            return self._bad_request("invalid json")
        names = []
        if isinstance(body.get("cogs"), list):
            names = body.get("cogs")
        elif isinstance(body.get("cogs"), str):
            names = [c.strip() for c in body.get("cogs").replace(",", " ").split() if c.strip()]
        else:
            return self._bad_request("cogs missing")

        succeeded: List[str] = []
        failed: Dict[str, str] = {}
        for name in names:
            if not self._validate_cog_name(name):
                failed[name] = "invalid cog name"
                continue
            try:
                path = f"cogs.{name}"
                if path not in getattr(self.bot, "extensions", {}):
                    failed[name] = "not loaded"
                    continue
                await self.bot.reload_extension(path)
                succeeded.append(name)
                self._log_audit({"event": "cog_reloaded", "cog": name, "by": request.remote})
            except Exception as e:
                logger.exception("reload cog %s failed: %s", name, e)
                failed[name] = str(e)
                self._log_audit({"event": "cog_reload_failed", "cog": name, "error": str(e), "by": request.remote})
        return web.json_response({"reloaded": succeeded, "failed": failed})

    async def handle_server_leave(self, request: web.Request) -> web.Response:
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=2, window=60):
            return web.json_response({"error": "rate_limited"}, status=429)

        try:
            gid = int(request.match_info["guild_id"])
            g = self.bot.get_guild(gid)
            if not g:
                return web.json_response({"error": "guild not found"}, status=404)
            await g.leave()
            self._log_audit({"event": "server_left", "guild_id": gid, "by": request.remote})
            return web.json_response({"status": "left"})
        except Exception as e:
            logger.exception("handle_server_leave error: %s", e)
            return web.json_response({"error": "internal"}, status=500)

    async def handle_invite_create(self, request: web.Request) -> web.Response:
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        try:
            body = await request.json()
        except Exception:
            return self._bad_request("invalid json")
        gid = body.get("guild_id")
        if not gid:
            return self._bad_request("guild_id required")
        try:
            gid = int(gid)
        except Exception:
            return self._bad_request("invalid guild_id")

        g = self.bot.get_guild(gid)
        if not g:
            return web.json_response({"error": "guild not found"}, status=404)

        max_age = int(body.get("max_age", 86400))
        max_uses = int(body.get("max_uses", 1))

        # find a suitable channel
        invite_chan = None
        for ch in g.text_channels:
            try:
                if ch.permissions_for(g.me).create_instant_invite:
                    invite_chan = ch
                    break
            except Exception:
                continue

        if not invite_chan:
            return web.json_response({"error": "no suitable channel"}, status=400)

        try:
            inv = await invite_chan.create_invite(max_age=max_age, max_uses=max_uses, unique=True)
            self._log_audit({"event": "invite_created", "guild_id": gid, "invite": str(inv.code), "by": request.remote})
            return web.json_response({"invite": str(inv.url)})
        except Exception as e:
            logger.exception("invite create failed: %s", e)
            return web.json_response({"error": "failed to create invite"}, status=500)

    async def handle_presence_set(self, request: web.Request) -> web.Response:
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        try:
            body = await request.json()
        except Exception:
            return self._bad_request("invalid json")

        typ = body.get("type", "game")
        text = body.get("text", "")
        status = body.get("status")
        try:
            if typ == "watching":
                activity = discord.Activity(type=discord.ActivityType.watching, name=text)
            elif typ == "listening":
                activity = discord.Activity(type=discord.ActivityType.listening, name=text)
            else:
                activity = discord.Game(name=text)

            if status:
                await self.bot.change_presence(activity=activity, status=getattr(discord.Status, status))
            else:
                await self.bot.change_presence(activity=activity)
            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.exception("presence set failed: %s", e)
            return web.json_response({"error": "failed"}, status=500)

    async def handle_dm(self, request: web.Request) -> web.Response:
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        try:
            body = await request.json()
        except Exception:
            return self._bad_request("invalid json")
        user_id = body.get("user_id")
        message = body.get("message")
        if not user_id or not message:
            return self._bad_request("user_id and message required")
        try:
            user = await self.bot.fetch_user(int(user_id))
            await user.send(message)
            return web.json_response({"status": "sent"})
        except Exception as e:
            logger.exception("dm failed: %s", e)
            return web.json_response({"error": "failed to send dm"}, status=500)

    # ----------------- Blacklist management (requires confirmation for changes) -----------------
    async def handle_blacklist_get(self, request: web.Request) -> web.Response:
        """Get paginated blacklist entries with optional search."""
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=10, window=30):
            return web.json_response({"error": "rate_limited"}, status=429)

        try:
            # Parse pagination parameters
            page = int(request.query.get("page", 1))
            per_page = min(int(request.query.get("per_page", 20)), 100)  # Max 100 per page
            search = request.query.get("search", "").strip()

            if page < 1:
                page = 1
            if per_page < 1:
                per_page = 20

            db = getattr(self.bot, "database", None)
            if not db or not hasattr(db, "get_blacklisted_users"):
                return web.json_response({"error": "db not available"}, status=503)

            # Get all entries (database doesn't have paginated query yet)
            all_entries = await db.get_blacklisted_users()

            # Filter by search if provided
            if search:
                filtered = []
                for entry in all_entries:
                    if search in str(entry.user_id) or search.lower() in (entry.reason or "").lower():
                        filtered.append(entry)
                all_entries = filtered

            # Calculate pagination
            total = len(all_entries)
            total_pages = (total + per_page - 1) // per_page if total > 0 else 1
            offset = (page - 1) * per_page
            page_entries = all_entries[offset:offset + per_page]

            # Format entries
            entries = []
            for entry in page_entries:
                entries.append({
                    "id": entry.id,
                    "user_id": int(entry.user_id),
                    "reason": entry.reason or "No reason provided"
                })

            self._log_audit({
                "event": "blacklist_list",
                "page": page,
                "per_page": per_page,
                "search": search,
                "by": request.remote
            })

            return web.json_response({
                "blacklist": entries,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total_entries": total,
                    "total_pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1
                }
            })
        except Exception as e:
            logger.exception("blacklist get failed: %s", e)
            return web.json_response({"error": "internal"}, status=500)

    async def handle_blacklist_post(self, request: web.Request) -> web.Response:
        """Add or remove blacklist entries with confirmation token flow."""
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=5, window=60):
            return web.json_response({"error": "rate_limited"}, status=429)

        try:
            body = await request.json()
        except Exception:
            return self._bad_request("invalid json")

        action = body.get("action")
        user_id = body.get("user_id")
        reason = body.get("reason")
        token = body.get("confirm_token")

        if token:
            # Execute confirmed action
            data = self._pop_confirmation(token, expected_action=f"blacklist:{action}")
            if not data:
                return web.json_response({"error": "invalid_or_expired_token"}, status=400)

            try:
                db = getattr(self.bot, "database", None)
                if not db:
                    return web.json_response({"error": "db not available"}, status=503)

                if action == "add":
                    await db.add_to_blacklist(str(data["params"]["user_id"]), data["params"].get("reason") or "No reason provided")
                    self._log_audit({
                        "event": "blacklist_add",
                        "user_id": data["params"]["user_id"],
                        "reason": data["params"].get("reason"),
                        "by": request.remote
                    })
                    return web.json_response({"status": "added", "user_id": data["params"]["user_id"]})
                elif action == "remove":
                    await db.remove_from_blacklist(str(data["params"]["user_id"]))
                    self._log_audit({
                        "event": "blacklist_remove",
                        "user_id": data["params"]["user_id"],
                        "by": request.remote
                    })
                    return web.json_response({"status": "removed", "user_id": data["params"]["user_id"]})
                else:
                    return self._bad_request("unknown action")
            except Exception as e:
                logger.exception("blacklist exec failed: %s", e)
                return web.json_response({"error": "internal"}, status=500)
        else:
            # Create confirmation token
            if action not in ("add", "remove"):
                return self._bad_request("action must be 'add' or 'remove'")

            if not user_id:
                return self._bad_request("user_id required")

            try:
                user_id = int(user_id)
            except (ValueError, TypeError):
                return self._bad_request("user_id must be a valid integer")

            if action == "add" and not reason:
                reason = "No reason provided"

            params = {"user_id": user_id, "reason": reason}
            token = self._create_confirmation(f"blacklist:{action}", params)
            return web.json_response({
                "confirm_token": token,
                "expires_in": 300,
                "action": action,
                "params": params
            })

    async def handle_confirmation_create(self, request: web.Request) -> web.Response:
        # create a generic confirmation token for a high-risk action
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=3, window=60):
            return web.json_response({"error": "rate_limited"}, status=429)
        try:
            body = await request.json()
        except Exception:
            return self._bad_request("invalid json")

        action = body.get("action")
        params = body.get("params", {})
        if not action:
            return self._bad_request("action required")

        # restrict allowed high-risk actions
        allowed = {"shutdown"}
        if action not in allowed:
            return web.json_response({"error": "action not allowed"}, status=403)

        token = self._make_token(action, params, requester=request.remote)
        self._log_audit({"event": "confirmation_requested", "action": action, "token": token, "params": params, "by": request.remote})
        return web.json_response({"confirm_token": token, "expires_in": 600})

    async def handle_confirmation_execute(self, request: web.Request) -> web.Response:
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=3, window=60):
            return web.json_response({"error": "rate_limited"}, status=429)
        try:
            body = await request.json()
        except Exception:
            return self._bad_request("invalid json")

        token = body.get("confirm_token")
        if not token:
            return self._bad_request("confirm_token required")

        data = self._pop_confirmation(token)
        if not data:
            return web.json_response({"error": "invalid_or_expired_token"}, status=400)

        action = data.get("action")
        params = data.get("params", {})

        # dispatch small set of high-risk actions
        if action == "shutdown":
            loop = asyncio.get_event_loop()
            try:
                loop.create_task(self._shutdown_bot())
            except Exception as e:
                logger.exception("Failed to schedule shutdown: %s", e)
                return web.json_response({"error": "failed to shutdown"}, status=500)
            self._log_audit({"event": "shutdown_executed", "by": request.remote, "token": token})
            return web.json_response({"status": "shutdown scheduled"})

        else:
            return web.json_response({"error": "action_not_implemented"}, status=501)

    async def handle_shutdown(self, request: web.Request) -> web.Response:
        # Shutdown must be executed via a confirmation token (two-step)
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        try:
            body = await request.json()
        except Exception:
            return self._bad_request("invalid json")

        token = body.get("confirm_token")
        if not token:
            # request a confirmation token
            t = self._make_token("shutdown", {}, requester=request.remote)
            self._log_audit({"event": "shutdown_requested", "token": t, "by": request.remote})
            return web.json_response({"confirm_token": t, "expires_in": 600})

        # execute
        data = self._pop_confirmation(token, expected_action="shutdown")
        if not data:
            return web.json_response({"error": "invalid_or_expired_token"}, status=400)

        # schedule shutdown
        loop = asyncio.get_event_loop()
        try:
            loop.create_task(self._shutdown_bot())
            self._log_audit({"event": "shutdown_executed", "by": request.remote})
            return web.json_response({"status": "shutdown scheduled"})
        except Exception as e:
            logger.exception("Failed to schedule shutdown: %s", e)
            return web.json_response({"error": "failed to shutdown"}, status=500)

    async def _shutdown_bot(self):
        await asyncio.sleep(0.5)
        try:
            await self.bot.close()
        except Exception:
            try:
                # best-effort: stop the loop
                loop = asyncio.get_event_loop()
                loop.stop()
            except Exception:
                pass

    async def handle_dashboard_stats(self, request: web.Request) -> web.Response:
        """Dashboard statistics endpoint - returns bot and economy statistics."""
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=10, window=30):
            return web.json_response({"error": "rate_limited"}, status=429)

        try:
            db = getattr(self.bot, "database", None)
            if not db:
                return web.json_response({"error": "database_unavailable"}, status=503)

            # Gather statistics
            stats = {}

            # Treasury balance
            try:
                treasury = await db.get_treasury_balance()
                stats["treasury_balance"] = str(treasury) if treasury else "0"
            except Exception as e:
                logger.warning("Failed to get treasury balance: %s", e)
                stats["treasury_balance"] = "0"

            # Supply record
            try:
                supply = await db.get_supply_record()
                if supply:
                    stats["supply"] = {
                        "total_supply": str(supply.total_supply) if hasattr(supply, "total_supply") else "0",
                        "circulating_supply": str(supply.circulating_supply) if hasattr(supply, "circulating_supply") else "0",
                    }
                else:
                    stats["supply"] = {"total_supply": "0", "circulating_supply": "0"}
            except Exception as e:
                logger.warning("Failed to get supply record: %s", e)
                stats["supply"] = {"total_supply": "0", "circulating_supply": "0"}

            # Economic factors
            try:
                factors = await db.get_economic_factors()
                stats["economic_factors"] = factors if factors else {}
            except Exception as e:
                logger.warning("Failed to get economic factors: %s", e)
                stats["economic_factors"] = {}

            # Global wins/losses
            try:
                wins = await db.get_global_wins()
                losses = await db.get_global_losses()
                stats["games"] = {
                    "total_wins": wins if wins else 0,
                    "total_losses": losses if losses else 0,
                }
            except Exception as e:
                logger.warning("Failed to get game stats: %s", e)
                stats["games"] = {"total_wins": 0, "total_losses": 0}

            # Bot statistics
            stats["bot"] = {
                "guilds": len(self.bot.guilds),
                "users": sum(g.member_count or 0 for g in self.bot.guilds),
                "latency_ms": round(self.bot.latency * 1000, 2) if self.bot.latency else None,
            }

            self._log_audit({"action": "dashboard_stats", "status": "success", "by": request.remote})
            return web.json_response(stats)

        except Exception as e:
            logger.exception("Dashboard stats error: %s", e)
            self._log_audit({"action": "dashboard_stats", "status": "error", "error": str(e), "by": request.remote})
            return web.json_response({"error": "internal"}, status=500)

    async def handle_dashboard_health(self, request: web.Request) -> web.Response:
        """Dashboard health endpoint - returns system health check."""
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=10, window=30):
            return web.json_response({"error": "rate_limited"}, status=429)

        try:
            db = getattr(self.bot, "database", None)
            health = {
                "status": "healthy",
                "components": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Database health
            if db:
                try:
                    # Test database connection with a simple query
                    if hasattr(db, "get_treasury_balance"):
                        await db.get_treasury_balance()
                    health["components"]["database"] = {"status": "healthy"}
                except Exception as e:
                    health["components"]["database"] = {"status": "unhealthy", "error": str(e)}
                    health["status"] = "degraded"
            else:
                health["components"]["database"] = {"status": "unavailable"}
                health["status"] = "degraded"

            # Bot connection health
            try:
                if self.bot.is_ready() and self.bot.is_connected():
                    health["components"]["discord"] = {
                        "status": "healthy",
                        "latency_ms": round(self.bot.latency * 1000, 2) if self.bot.latency else None,
                    }
                else:
                    health["components"]["discord"] = {"status": "unhealthy", "error": "not_connected"}
                    health["status"] = "unhealthy"
            except Exception as e:
                health["components"]["discord"] = {"status": "unhealthy", "error": str(e)}
                health["status"] = "unhealthy"

            # Economic health score (if available)
            if db and hasattr(db, "get_economic_health_score"):
                try:
                    health_score = await db.get_economic_health_score()
                    if health_score:
                        health["components"]["economy"] = {
                            "status": "healthy" if health_score.get("score", 0) >= 70 else "degraded",
                            "score": health_score.get("score"),
                            "status_text": health_score.get("status"),
                        }
                except Exception as e:
                    health["components"]["economy"] = {"status": "unknown", "error": str(e)}

            self._log_audit({"action": "dashboard_health", "status": "success", "by": request.remote})
            return web.json_response(health)

        except Exception as e:
            logger.exception("Dashboard health error: %s", e)
            self._log_audit({"action": "dashboard_health", "status": "error", "error": str(e), "by": request.remote})
            return web.json_response({"error": "internal"}, status=500)

    async def handle_dashboard_trends(self, request: web.Request) -> web.Response:
        """Dashboard trends endpoint - returns usage trends over time."""
        if not await self._require_jwt_auth(request):
            return self._unauthorized()
        if self._rate_limited(request, limit=10, window=30):
            return web.json_response({"error": "rate_limited"}, status=429)

        try:
            db = getattr(self.bot, "database", None)
            if not db:
                return web.json_response({"error": "database_unavailable"}, status=503)

            # Get days parameter (default to 7)
            days = 7
            try:
                days_param = request.query.get("days", "7")
                days = int(days_param)
                if days < 1 or days > 90:
                    days = 7
            except Exception:
                days = 7

            trends = {}

            # Economic trends
            if hasattr(db, "get_economic_trends"):
                try:
                    economic_trends = await db.get_economic_trends(days)
                    trends["economic"] = economic_trends if economic_trends else {}
                except Exception as e:
                    logger.warning("Failed to get economic trends: %s", e)
                    trends["economic"] = {}

            # Game history trends (if available)
            if hasattr(db, "get_game_history"):
                try:
                    # This would need to be implemented in the database manager
                    # For now, we'll return an empty object
                    trends["games"] = {}
                except Exception as e:
                    logger.warning("Failed to get game trends: %s", e)
                    trends["games"] = {}

            trends["period_days"] = days
            trends["generated_at"] = datetime.now(timezone.utc).isoformat()

            self._log_audit({"action": "dashboard_trends", "status": "success", "days": days, "by": request.remote})
            return web.json_response(trends)

        except Exception as e:
            logger.exception("Dashboard trends error: %s", e)
            self._log_audit({"action": "dashboard_trends", "status": "error", "error": str(e), "by": request.remote})
            return web.json_response({"error": "internal"}, status=500)

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket endpoint for real-time admin panel updates."""
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        # Authenticate via JWT token (passed as query parameter or header)
        token = request.query.get("token") or request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            await ws.send_json({"error": "authentication_required", "message": "JWT token required"})
            await ws.close(code=4001, reason="Authentication required")
            return ws

        # Validate JWT token
        session_id = self._validate_jwt_token(token)
        if not session_id:
            await ws.send_json({"error": "invalid_token", "message": "Invalid or expired token"})
            await ws.close(code=4001, reason="Invalid token")
            return ws

        # Check session exists
        if session_id not in self._sessions:
            await ws.send_json({"error": "session_not_found", "message": "Session not found"})
            await ws.close(code=4001, reason="Session not found")
            return ws

        # Add connection to tracking set
        self._ws_connections.add(ws)
        client_ip = request.remote or "unknown"
        self._log_audit({"action": "websocket_connect", "status": "success", "by": client_ip, "session_id": session_id})
        logger.info("WebSocket client connected from %s (session: %s)", client_ip, session_id)

        try:
            # Send welcome message
            await ws.send_json({
                "type": "connected",
                "message": "WebSocket connection established",
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            # Message handling loop
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_type = data.get("type")

                        if msg_type == "ping":
                            await ws.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})

                        elif msg_type == "subscribe":
                            # Subscribe to specific update channels
                            channels = data.get("channels", [])
                            # For now, we'll acknowledge subscription (future: implement channel filtering)
                            await ws.send_json({
                                "type": "subscribed",
                                "channels": channels,
                                "message": "Subscription acknowledged"
                            })

                        elif msg_type == "request_update":
                            # Request immediate update for specific data
                            update_type = data.get("update_type")
                            if update_type == "dashboard_stats":
                                # Send dashboard stats update
                                stats = await self._get_dashboard_stats()
                                await ws.send_json({"type": "dashboard_update", "data": stats})
                            elif update_type == "blacklist":
                                # Send blacklist update
                                blacklist = await self._get_blacklist_snapshot()
                                await ws.send_json({"type": "blacklist_update", "data": blacklist})
                            else:
                                await ws.send_json({"type": "error", "message": f"Unknown update type: {update_type}"})

                        else:
                            await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "message": "Invalid JSON format"})
                    except Exception as e:
                        logger.exception("WebSocket message handling error: %s", e)
                        await ws.send_json({"type": "error", "message": "Internal error processing message"})

                elif msg.type == web.WSMsgType.ERROR:
                    logger.error("WebSocket connection error: %s", ws.exception())
                    break

        finally:
            # Remove connection from tracking set
            self._ws_connections.discard(ws)
            self._log_audit({"action": "websocket_disconnect", "status": "success", "by": client_ip, "session_id": session_id})
            logger.info("WebSocket client disconnected from %s (session: %s)", client_ip, session_id)

        return ws

    async def _get_dashboard_stats(self) -> Dict[str, Any]:
        """Get dashboard stats for WebSocket updates."""
        try:
            db = getattr(self.bot, "database", None)
            stats = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "bot_status": {
                    "is_ready": self.bot.is_ready() if hasattr(self.bot, "is_ready") else False,
                    "is_connected": self.bot.is_connected() if hasattr(self.bot, "is_connected") else False,
                    "latency_ms": round(self.bot.latency * 1000, 2) if hasattr(self.bot, "latency") and self.bot.latency else None,
                    "guild_count": len(self.bot.guilds) if hasattr(self.bot, "guilds") else 0,
                    "user_count": sum(g.member_count or 0 for g in self.bot.guilds) if hasattr(self.bot, "guilds") else 0,
                }
            }

            if db:
                if hasattr(db, "get_treasury_balance"):
                    stats["treasury"] = {"balance": float(await db.get_treasury_balance() or 0)}
                if hasattr(db, "get_blacklisted_users"):
                    blacklist = await db.get_blacklisted_users()
                    stats["blacklist_count"] = len(blacklist) if blacklist else 0

            return stats
        except Exception as e:
            logger.exception("Error getting dashboard stats for WebSocket: %s", e)
            return {"error": str(e), "timestamp": datetime.now(timezone.utc).isoformat()}

    async def _get_blacklist_snapshot(self) -> Dict[str, Any]:
        """Get blacklist snapshot for WebSocket updates."""
        try:
            db = getattr(self.bot, "database", None)
            if not db or not hasattr(db, "get_blacklisted_users"):
                return {"blacklisted_users": [], "count": 0}

            blacklist = await db.get_blacklisted_users()
            return {
                "blacklisted_users": [{"user_id": uid, "reason": reason} for uid, reason in (blacklist or [])],
                "count": len(blacklist) if blacklist else 0,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.exception("Error getting blacklist snapshot for WebSocket: %s", e)
            return {"error": str(e), "blacklisted_users": [], "count": 0}

    async def broadcast_to_websockets(self, message: Dict[str, Any]) -> None:
        """Broadcast a message to all connected WebSocket clients."""
        if not self._ws_connections:
            return

        disconnected = set()
        for ws in self._ws_connections:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning("Failed to send WebSocket message: %s", e)
                disconnected.add(ws)

        # Clean up disconnected clients
        for ws in disconnected:
            self._ws_connections.discard(ws)

    async def start(self):
        if self.runner:
            return
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        # bind to localhost by default unless explicitly allowed to bind externally.
        # Accept both ADMIN_API_BIND_EXTERNAL (the canonical name) and the older
        # ADMIN_API_ALLOW_EXTERNAL_BIND alias used in .env.example/README/compose.
        allow_external = os.getenv(
            "ADMIN_API_BIND_EXTERNAL",
            os.getenv("ADMIN_API_ALLOW_EXTERNAL_BIND", "false"),
        ).lower() == "true"
        bind_host = self.host if allow_external else "127.0.0.1"
        self.site = web.TCPSite(self.runner, bind_host, self.port)
        await self.site.start()
        logger.info(f"Admin API started on {bind_host}:{self.port} (external_bind={allow_external})")

    async def stop(self):
        if not self.runner:
            return
        try:
            await self.runner.cleanup()
        finally:
            self.runner = None
            self.site = None
            logger.info("Admin API stopped")
