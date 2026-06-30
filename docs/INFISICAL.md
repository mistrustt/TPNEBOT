# Infisical Secrets Management

TPNEBOT can fetch its runtime secrets from [Infisical](https://infisical.com/) instead of reading them from `.env`.

## How it works

- At startup the bot authenticates to Infisical using a **Machine Identity** via Universal Auth.
- It fetches secrets (`TOKEN`, `DB_PW`, `USER_ID_HASH_KEY`, `STATS_SALT`, `ADMIN_API_SECRET`) and loads them into the process environment.
- A background task renews the Infisical access token before it expires.
- If Infisical is unreachable, the bot intentionally exits so Docker can restart it.
- If `INFISICAL_CLIENT_ID` is not set, the bot falls back to reading secrets directly from environment variables (useful for local development).

## Setup

1. Create a project in Infisical (e.g., `tpnebot`).
2. Create a **Machine Identity** and add it to your project with read access to the relevant secrets.
3. Create a **Universal Auth** credential for that identity. Copy the `clientId` and `clientSecret`.
4. Store your secrets in Infisical with these exact names (default `/` path, `shared` type):
   - `TOKEN`
   - `DB_PW`
   - `USER_ID_HASH_KEY`
   - `STATS_SALT`
   - `ADMIN_API_SECRET`
5. Put only the Infisical identity credentials in `.env`:

```env
INFISICAL_CLIENT_ID=your-client-id
INFISICAL_CLIENT_SECRET=your-client-secret
INFISICAL_SITE_URL=https://us.infisical.com
INFISICAL_PROJECT_ID=your-project-id
INFISICAL_ENVIRONMENT=prod
```

6. Remove the real secret values from `.env` or leave placeholder values. They will be overwritten at runtime when loaded from Infisical.
7. Run `docker compose up -d`.

## Notes

- Universal Auth access tokens are short-lived. The bot automatically re-authenticates before expiry; you do not need a separate cron job or sidecar.
- If you are using Infisical Cloud US, the default `INFISICAL_SITE_URL=https://us.infisical.com` is correct.
- There is **no local backup** of secrets. If Infisical is down, the bot will fail to start and Docker will restart it until Infisical is available.
- For local development without Infisical, simply leave `INFISICAL_CLIENT_ID` empty and the bot will use `.env` as before.
