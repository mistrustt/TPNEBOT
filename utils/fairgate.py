"""Async client for the FairGate provably-fair backend.

FairGate supplies deterministic game outcomes and verification proofs. TPNEBOT
remains responsible for wallets, payouts, rakeback, and session state.

The client targets the ``sha256_tag`` algorithm so outcomes match the tagged
HMAC-SHA256 RNG used by ``utils.fairness.py``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urljoin
import asyncio
import aiohttp

logger = logging.getLogger("discord_bot")


class FairGateError(Exception):
    """Raised for any FairGate API error."""

    def __init__(self, message: str, *, status: int | None = None, response: Any = None):
        super().__init__(message)
        self.status = status
        self.response = response


class SeedGoneError(FairGateError):
    """Raised when the supplied server_seed_hash is no longer active."""

    def __init__(self, message: str, *, current_hash: str | None = None, response: Any = None):
        super().__init__(message, status=410, response=response)
        self.current_hash = current_hash


class FairGateClient:
    """aiohttp-based client for FairGate.

    Usage::

        fg = FairGateClient.from_env()
        seed = await fg.get_seed()
        outcome = await fg.play(
            user_id=user.id,
            game="mines",
            params={"rows": 5, "cols": 5, "mines": 3},
            client_seed="my-seed",
            nonce=0,
        )
    """

    DEFAULT_ALGORITHM = "sha256_tag"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        admin_api_key: str | None = None,
        algorithm: str = DEFAULT_ALGORITHM,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.admin_api_key = admin_api_key
        self.algorithm = algorithm
        self._session: aiohttp.ClientSession | None = None
        # Default gives slow game servers more headroom while still catching
        # truly stuck connections quickly.
        self._timeout = timeout or aiohttp.ClientTimeout(
            total=30, connect=10, sock_read=25
        )
        self._seed: dict[str, Any] | None = None

    @classmethod
    def from_env(cls) -> "FairGateClient":
        """Build a client from environment variables loaded by bot.py.

        At least one of ``FAIRGATE_API_KEY`` (for gameplay) or
        ``FAIRGATE_ADMIN_API_KEY`` (for app management) must be set.
        ``FAIRGATE_TIMEOUT`` can override the default 30-second request timeout.
        """
        base_url = os.getenv("FAIRGATE_BASE_URL", "http://localhost:8080")
        api_key = os.getenv("FAIRGATE_API_KEY") or None
        admin_api_key = os.getenv("FAIRGATE_ADMIN_API_KEY") or None
        if not api_key and not admin_api_key:
            raise FairGateError(
                "Neither FAIRGATE_API_KEY nor FAIRGATE_ADMIN_API_KEY is configured"
            )

        timeout = None
        if raw_timeout := os.getenv("FAIRGATE_TIMEOUT"):
            try:
                timeout = aiohttp.ClientTimeout(total=float(raw_timeout))
            except ValueError:
                logger.warning(
                    "FAIRGATE_TIMEOUT value %r is not a number; using default", raw_timeout
                )

        return cls(
            base_url=base_url,
            api_key=api_key,
            admin_api_key=admin_api_key,
            timeout=timeout,
        )

    def _session_or_create(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    def _ensure_api_key(self) -> None:
        if not self.api_key:
            raise FairGateError("FairGate app API key is not configured")

    def _headers(self, *, admin: bool = False) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if admin:
            if not self.admin_api_key:
                raise FairGateError("FairGate admin API key is not configured")
            headers["Authorization"] = f"AdminKey {self.admin_api_key}"
        else:
            self._ensure_api_key()
            headers["X-API-Key"] = self.api_key
        return headers

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        admin: bool = False,
    ) -> dict[str, Any]:
        session = self._session_or_create()
        url = urljoin(self.base_url + "/", path.lstrip("/"))

        async with session.request(
            method,
            url,
            headers=self._headers(admin=admin),
            json=json_body,
            params=params,
        ) as resp:
            text = await resp.text()
            try:
                data = json.loads(text) if text else {}
            except json.JSONDecodeError:
                data = {"raw": text}

            if resp.status >= 400:
                message = data.get("error", f"FairGate returned {resp.status}")
                if resp.status == 410:
                    raise SeedGoneError(message, current_hash=data.get("current_hash"), response=data)
                raise FairGateError(message, status=resp.status, response=data)

            return data

    async def health(self) -> dict[str, Any]:
        """Health check. No authentication required."""
        return await self._request("GET", "/health")

    async def get_seed(self, *, force: bool = False) -> dict[str, Any]:
        """Return the active app seed commitment, caching it until expiry.

        With app-level seeds this hash is shared across all users. Callers should
        store it in ``GameHistory.hash``.
        """
        if not force and self._seed:
            return self._seed

        data = await self._request("GET", "/fairness/seed")
        self._seed = data
        logger.info(f"FairGate seed cached: {data.get('server_seed_hash', '')[:12]}...")
        return data

    def _active_hash(self, provided: str | None) -> str:
        if provided:
            return provided
        if not self._seed:
            raise FairGateError("No server_seed_hash provided and no cached seed")
        return self._seed["server_seed_hash"]

    async def play(
        self,
        *,
        user_id: int,
        game: str,
        params: dict[str, Any] | None = None,
        client_seed: str,
        nonce: int,
        server_seed_hash: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a single FairGate game/play draw.

        If ``server_seed_hash`` is omitted the cached active hash is used.
        On ``410 Gone`` the client refreshes the seed once and retries.
        """
        payload = {
            "user_id": str(user_id),
            "game": game,
            "client_seed": client_seed,
            "nonce": nonce,
            "server_seed_hash": self._active_hash(server_seed_hash),
            "params": params or {},
        }

        try:
            return await self._request("POST", "/play", json_body=payload)
        except SeedGoneError:
            logger.warning("FairGate seed rotated (410 Gone); refreshing and retrying")
            await self.get_seed(force=True)
            payload["server_seed_hash"] = self._active_hash(None)
            return await self._request("POST", "/play", json_body=payload)
        except FairGateError as exc:
            # Some FairGate instances return the mismatch as a non-410 error.
            msg = str(exc).lower()
            if "server seed hash" in msg or "active session" in msg:
                logger.warning("FairGate seed mismatch; refreshing and retrying")
                await self.get_seed(force=True)
                payload["server_seed_hash"] = self._active_hash(None)
                return await self._request("POST", "/play", json_body=payload)
            raise
        except (TimeoutError, asyncio.TimeoutError):
            # Re-raise as FairGateError so callers can uniformly catch failures.
            raise FairGateError("FairGate request timed out", status=None)
        except OSError as exc:
            # aiohttp network/connection errors subclass OSError.
            raise FairGateError(f"FairGate connection error: {exc}", status=None)

    async def create_app(
        self,
        *,
        name: str,
        allowed_games: list[str] | None = None,
        rotation_policy: str = "after_each_bet",
        rotation_config: dict[str, Any] | None = None,
        algorithm: str | None = None,
    ) -> dict[str, Any]:
        """Register a new FairGate app using the admin API key.

        Requires ``FAIRGATE_ADMIN_API_KEY`` to be configured. Returns the new
        app's metadata including its ``api_key``.
        """
        payload: dict[str, Any] = {"name": name, "rotation_policy": rotation_policy}
        if allowed_games is not None:
            payload["allowed_games"] = allowed_games
        if rotation_config is not None:
            payload["rotation_config"] = rotation_config
        if algorithm is not None:
            payload["algorithm"] = algorithm

        return await self._request("POST", "/apps", json_body=payload, admin=True)

    async def get_app(self, app_id: str) -> dict[str, Any]:
        """Read metadata for the authenticated app."""
        return await self._request("GET", f"/apps/{app_id}")

    async def rotate_app_seed(self, app_id: str) -> dict[str, Any]:
        """Manually rotate the app's active server seed."""
        return await self._request("POST", f"/apps/{app_id}/rotate")

    async def list_games(self) -> dict[str, Any]:
        """List the games supported by this FairGate instance."""
        return await self._request("GET", "/games")

    async def patch_app(
        self,
        app_id: str,
        *,
        name: str | None = None,
        allowed_games: list[str] | None = None,
        rotation_policy: str | None = None,
        rotation_config: dict[str, Any] | None = None,
        algorithm: str | None = None,
    ) -> dict[str, Any]:
        """Update metadata for an existing FairGate app.

        Requires ``FAIRGATE_ADMIN_API_KEY`` to be configured. Only fields
        that are passed are sent to the server.
        """
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if allowed_games is not None:
            payload["allowed_games"] = allowed_games
        if rotation_policy is not None:
            payload["rotation_policy"] = rotation_policy
        if rotation_config is not None:
            payload["rotation_config"] = rotation_config
        if algorithm is not None:
            payload["algorithm"] = algorithm
        if not payload:
            raise ValueError("patch_app called with no fields to update")
        return await self._request(
            "PATCH", f"/apps/{app_id}", json_body=payload, admin=True
        )

    async def verify(
        self,
        *,
        server_seed: str,
        server_seed_hash: str,
        client_seed: str,
        nonce: int,
        game: str,
        params: dict[str, Any] | None = None,
        algorithm: str | None = None,
    ) -> dict[str, Any]:
        """Verify a revealed outcome against FairGate's public verify endpoint."""
        query: dict[str, Any] = {
            "server_seed": server_seed,
            "server_seed_hash": server_seed_hash,
            "client_seed": client_seed,
            "nonce": nonce,
            "game": game,
            "algorithm": algorithm or self.algorithm,
        }
        if params:
            query["params"] = json.dumps(params, separators=(",", ":"), sort_keys=True)

        return await self._request("GET", "/fairness/verify", params=query)
