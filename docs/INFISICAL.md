# Infisical Secrets Management

TPNEBOT fetches all runtime secrets from [Infisical](https://infisical.com/). `.env` is used only for non-sensitive configuration (database host/port/name, Admin API host/port, developer channel ID) and for the Infisical machine-identity credentials.

## How it works

- At startup the bot authenticates to Infisical using a **Machine Identity** via Universal Auth.
- It fetches all secrets and loads them into the process environment before the bot or any cogs are initialized.
- A background task renews the Infisical access token before it expires.
- If Infisical is unreachable or the required secrets are missing, the bot intentionally exits so Docker can restart it.
- **Infisical is mandatory.** The bot will not start without `INFISICAL_CLIENT_ID` and `INFISICAL_CLIENT_SECRET`.

## Setup

1. Create a project in Infisical (e.g., `tpnebot`).
2. Create a **Machine Identity** and add it to your project with read access to the relevant secrets.
3. Create a **Universal Auth** credential for that identity. Copy the `clientId` and `clientSecret`.
4. Store your secrets in Infisical with these exact names (default `/` path, `shared` type):
   - `TOKEN`
   - `DB_PW`
   - `USER_ID_HASH_KEY`
   - `STATS_SALT`
   - `LOCATION_ENCRYPTION_KEY`
   - `ADMIN_API_SECRET`
   - `OPENROUTER_API_KEY`
   - `API_NINJAS_KEY`
   - `COINMARKETCAP_API_KEY`
   - `NASA_API_KEY`
   - `WEATHER_API_KEY`
   - `LASTFM_API_KEY`
   - `GENIUS_API_KEY`
   - `FREECRYPTOAPI_API_KEY`
5. Put only the Infisical identity credentials and non-sensitive config in `.env`:

```env
INFISICAL_CLIENT_ID=your-client-id
INFISICAL_CLIENT_SECRET=your-client-secret
INFISICAL_SITE_URL=https://us.infisical.com
INFISICAL_PROJECT_ID=your-project-id
INFISICAL_ENVIRONMENT=prod

# Non-sensitive configuration only
DEVELOPER_CHANNEL_ID=0
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_NAME=postgres
ADMIN_API_HOST=127.0.0.1
ADMIN_API_PORT=8080
ADMIN_API_BIND_EXTERNAL=0
```

6. Run `docker compose up -d`.

## Database password

Docker Compose needs `POSTGRES_PASSWORD` in `.env` to initialize the PostgreSQL container. The bot itself reads `DB_PW` from Infisical; for a local compose stack this should match `POSTGRES_PASSWORD`. In production, consider using Docker secrets or another out-of-band mechanism for `POSTGRES_PASSWORD` so it does not live on disk.

## Notes

- Universal Auth access tokens are short-lived. The bot automatically re-authenticates before expiry; you do not need a separate cron job or sidecar.
- If you are using Infisical Cloud US, the default `INFISICAL_SITE_URL=https://us.infisical.com` is correct.
- There is **no local backup** of secrets. If Infisical is down, the bot will fail to start and Docker will restart it until Infisical is available.
- Do not store secrets in `.env` on production systems. Keep `.env` under version control only if it contains non-sensitive values and placeholder Infisical credentials.
