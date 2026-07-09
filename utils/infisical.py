"""Infisical secrets manager for TPNEBOT.

Loads runtime secrets from Infisical at startup and keeps the access token
renewed in the background. If Infisical is unreachable when secrets are needed,
the bot intentionally fails (no local .env fallback) so Docker can restart it.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp

logger = logging.getLogger("discord.client")


class InfisicalSecretsManager:
    """Fetch secrets from Infisical using Universal Auth and refresh the token automatically."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        site_url: str = "https://us.infisical.com",
        project_id: Optional[str] = None,
        environment: str = "prod",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.site_url = site_url.rstrip("/")
        self.project_id = project_id
        self.environment = environment
        self.access_token: Optional[str] = None
        self.expires_at: Optional[datetime] = None

    @classmethod
    def from_env(cls) -> "InfisicalSecretsManager":
        return cls(
            client_id=os.getenv("INFISICAL_CLIENT_ID", ""),
            client_secret=os.getenv("INFISICAL_CLIENT_SECRET", ""),
            site_url=os.getenv("INFISICAL_SITE_URL", "https://us.infisical.com"),
            project_id=os.getenv("INFISICAL_PROJECT_ID") or None,
            environment=os.getenv("INFISICAL_ENVIRONMENT", "prod"),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def authenticate(self) -> None:
        """Exchange Universal Auth client credentials for an access token."""
        url = f"{self.site_url}/api/v1/auth/universal-auth/login"
        payload = {"clientId": self.client_id, "clientSecret": self.client_secret}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Infisical auth failed ({resp.status}): {text}")
                data = await resp.json()

        self.access_token = data.get("accessToken")
        if not self.access_token:
            raise RuntimeError("Infisical auth response missing accessToken")

        expires_in = data.get("expiresIn", 6 * 3600)
        self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        logger.info(f"Infisical token acquired, expires in {expires_in}s")

    async def get_secret(
        self,
        secret_name: str,
        *,
        secret_path: str = "/",
        secret_type: str = "shared",
    ) -> str:
        """Fetch a single secret value from Infisical."""
        if not self.access_token:
            raise RuntimeError("Not authenticated to Infisical")

        if not self.project_id:
            raise RuntimeError("INFISICAL_PROJECT_ID is required to fetch secrets")

        path = secret_path.strip("/")
        encoded_path = "%2F" if not path else f"%2F{path.replace('/', '%2F')}"
        url = (
            f"{self.site_url}/api/v4/secrets/{secret_name}"
            f"?projectId={self.project_id}"
            f"&environment={self.environment}"
            f"&secretPath={encoded_path}"
            f"&type={secret_type}"
            f"&viewSecretValue=true"
            f"&expandSecretReferences=true"
            f"&includeImports=true"
        )

        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(
                        f"Infisical secret fetch failed ({resp.status}): {text}"
                    )
                data = await resp.json()

        secret = data.get("secret", {})
        value = secret.get("secretValue")
        if value is None:
            raise RuntimeError(f"Infisical secret '{secret_name}' has no value")
        return value

    async def load_secrets_into_environ(self, mapping: dict[str, str]) -> None:
        """
        Load secrets into os.environ.

        Args:
            mapping: {os.environ_name: infisical_secret_name}
        """
        logger.info("-------------------")
        for env_name, secret_name in mapping.items():
            value = await self.get_secret(secret_name)
            os.environ[env_name] = value
            logger.info(f"Loaded Infisical secret '{secret_name}' as '{env_name}'")

    async def refresh_loop(self) -> None:
        """
        Background task that re-authenticates before the token expires.

        If re-authentication fails and the token is about to expire, the task
        raises so that Docker will restart the container.
        """
        while True:
            if self.expires_at is None:
                await asyncio.sleep(60)
                continue

            expires_in = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
            # Re-auth when 75% of the lifetime has elapsed, or 5 minutes before expiry,
            # whichever comes first, but never less than 60 seconds from now.
            refresh_in = min(expires_in * 0.25, expires_in - 300)
            refresh_in = max(60, refresh_in)

            logger.debug(f"Infisical token refresh in {refresh_in:.0f}s")
            await asyncio.sleep(refresh_in)

            try:
                await self.authenticate()
                logger.info("Infisical token refreshed")
            except Exception as e:
                logger.error(f"Failed to refresh Infisical token: {e}")
                expires_in = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
                if expires_in < 300:
                    raise RuntimeError(
                        "Infisical token expiring and refresh failed"
                    ) from e
