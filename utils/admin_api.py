import asyncio
import json
import logging
import os
import uuid
import time
import hmac
import hashlib
import ipaddress
from datetime import datetime
import discord
from aiohttp import web
from typing import Optional, List, Dict, Any
from sqlalchemy import text

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
        self._setup_routes()

    def _setup_routes(self):
        # Low-risk
        self.app.router.add_get("/status", self.handle_status)
        self.app.router.add_get("/cogs", self.handle_cogs)
        self.app.router.add_get("/servers", self.handle_servers)
        self.app.router.add_get(r"/server/{guild_id:\d+}", self.handle_server)

        # Medium/controlled (require secret)
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
            entry["timestamp"] = datetime.utcnow().isoformat()
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
        if not await self._require_auth(request):
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
        if not await self._require_auth(request):
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
        if not await self._require_auth(request):
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
        if not await self._require_auth(request):
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
        if not await self._require_auth(request):
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
        if not await self._require_auth(request):
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
        if not await self._require_auth(request):
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
        if not await self._require_auth(request):
            return self._unauthorized()
        try:
            db = getattr(self.bot, "database", None)
            if not db or not hasattr(db, "get_blacklisted_users"):
                return web.json_response({"error": "db not available"}, status=503)
            rows = await db.get_blacklisted_users()
            out = []
            for r in rows:
                out.append({"user_id": int(r.user_id), "reason": getattr(r, "reason", None), "added_at": getattr(r, "added_at", None)})
            return web.json_response({"blacklist": out})
        except Exception as e:
            logger.exception("blacklist get failed: %s", e)
            return web.json_response({"error": "internal"}, status=500)

    async def handle_blacklist_post(self, request: web.Request) -> web.Response:
        # This endpoint either requests a confirmation token or executes when a token is provided
        if not await self._require_auth(request):
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
            # execute
            data = self._pop_confirmation(token, expected_action=f"blacklist:{action}")
            if not data:
                return web.json_response({"error": "invalid_or_expired_token"}, status=400)
            # perform action
            try:
                db = getattr(self.bot, "database", None)
                if not db:
                    return web.json_response({"error": "db not available"}, status=503)
                if action == "add":
                    await db.add_to_blacklist(int(data["params"]["user_id"]), data["params"].get("reason") or "API")
                    self._log_audit({"event": "blacklist_add", "user_id": data["params"]["user_id"], "by": request.remote})
                    return web.json_response({"status": "added"})
                elif action == "remove":
                    await db.remove_from_blacklist(int(data["params"]["user_id"]))
                    self._log_audit({"event": "blacklist_remove", "user_id": data["params"]["user_id"], "by": request.remote})
                    return web.json_response({"status": "removed"})
                else:
                    return self._bad_request("unknown action")
            except Exception as e:
                logger.exception("blacklist exec failed: %s", e)
                return web.json_response({"error": "internal"}, status=500)
        else:
            # create confirmation token
            if action not in ("add", "remove"):
                return self._bad_request("action must be 'add' or 'remove'")
            if not user_id:
                return self._bad_request("user_id required")
            params = {"user_id": int(user_id), "reason": reason}
            token = self._make_token(f"blacklist:{action}", params, requester=request.remote)
            self._log_audit({"event": "blacklist_request", "action": action, "user_id": user_id, "token": token, "by": request.remote})
            return web.json_response({"confirm_token": token, "expires_in": 600})

    async def handle_confirmation_create(self, request: web.Request) -> web.Response:
        # create a generic confirmation token for a high-risk action
        if not await self._require_auth(request):
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
        allowed = {"shutdown", "db_sql", "eval", "sudo", "admin_bank"}
        if action not in allowed:
            return web.json_response({"error": "action not allowed"}, status=403)

        token = self._make_token(action, params, requester=request.remote)
        self._log_audit({"event": "confirmation_requested", "action": action, "token": token, "params": params, "by": request.remote})
        return web.json_response({"confirm_token": token, "expires_in": 600})

    async def handle_confirmation_execute(self, request: web.Request) -> web.Response:
        if not await self._require_auth(request):
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

        elif action == "db_sql":
            # require explicit env enable
            if os.getenv("ADMIN_API_ENABLE_SQL", "false").lower() != "true":
                return web.json_response({"error": "sql_disabled"}, status=403)
            query = params.get("query")
            if not query:
                return self._bad_request("query required")
            lower_q = query.strip().lower()
            if lower_q.startswith("delete") and "where" not in lower_q and os.getenv("ADMIN_API_ENABLE_SQL_WRITE", "false").lower() != "true":
                return web.json_response({"error": "dangerous_delete_blocked"}, status=403)
            try:
                async with self.bot.database.async_sessionmaker() as session:
                    result = await session.execute(text(query))
                    if lower_q.startswith("select"):
                        try:
                            rows = result.mappings().all()
                        except Exception:
                            rows = result.fetchall()
                        self._log_audit({"event": "db_sql_executed", "query": query[:200], "by": request.remote})
                        # try serializable
                        serial = []
                        for r in rows:
                            try:
                                serial.append(dict(r))
                            except Exception:
                                serial.append(list(r))
                        return web.json_response({"rows": serial})
                    else:
                        await session.commit()
                        affected = getattr(result, "rowcount", None)
                        self._log_audit({"event": "db_sql_executed", "query": query[:200], "affected": affected, "by": request.remote})
                        return web.json_response({"status": "ok", "affected": affected})
            except Exception as e:
                logger.exception("db_sql failed: %s", e)
                return web.json_response({"error": "exec_failed"}, status=500)

        elif action == "eval":
            if os.getenv("ADMIN_API_ENABLE_EVAL", "false").lower() != "true":
                return web.json_response({"error": "eval_disabled"}, status=403)
            code = params.get("code")
            if not code:
                return self._bad_request("code required")
            try:
                env = {"bot": self.bot}
                exec(code, env)
                self._log_audit({"event": "eval_executed", "by": request.remote, "token": token})
                return web.json_response({"status": "executed"})
            except Exception as e:
                logger.exception("eval failed: %s", e)
                return web.json_response({"error": "exec_failed"}, status=500)

        else:
            return web.json_response({"error": "action_not_implemented"}, status=501)

    async def handle_shutdown(self, request: web.Request) -> web.Response:
        # Shutdown must be executed via a confirmation token (two-step)
        if not await self._require_auth(request):
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

    async def start(self):
        if self.runner:
            return
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        # bind to localhost by default unless explicitly allowed to bind externally
        allow_external = os.getenv("ADMIN_API_BIND_EXTERNAL", "false").lower() == "true"
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
