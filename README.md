# TPNEBOT

NOTE: This file was Co-Authored by Claude on 2026-07-02.

TPNEBOT is a modular Discord bot featuring an economy, casino style games with provable fairness, moderation and community management tools, music and voice utilities, role management features, and extensible command cogs. It uses discord.py, SQLAlchemy (async), asyncpg, and a Postgres database.

## Key Features
- Economy and virtual currency with wallets, transactions, items, shop, bounties
- Casino and game suite (dice, gamble variants, slots, blackjack, mines, crash, ladder, roulette, poker, cards, etc.)
- Provable fairness system (HMAC based deterministic RNG and verifiers)
- Moderation (punishments, jail, mute variants, case notes, watchdog auditing)
- Community utilities (timezone, location, reactions, reputation, streaks, LastFM integration)
- Music and voice channel utilities (temporary voice channels, join to create management)
- Role and booster management, command role restrictions, lockdown handling
- Developer quality of life (dynamic cog loading, debug mode, structured logging)

## Documentation

- See [documentation](https://github.com/mistrustt/TPNEBOT/tree/main/docs)
- [Economy & Casino User Guide](docs/ECONOMY_AND_CASINO.md)
- [Terms of Service](docs/TERMS.md)
- [Privacy Policy](docs/PRIVACY.md)
- [Security Policy](docs/SECURITY.md)

## Data Deletion / Right to be Forgotten

Users can delete their personal data at any time by running `!forgetme`. The bot will send a confirmation PIN via DM; once confirmed, it removes:

- Profile and social data (reputation, reaction counters, LastFM link, favorite songs, timezone/location, name/role history).
- Economy data (wallet, bank, inventory, crypto, jobs, loans, trade/bounty history, rakeback, VIP status).
- Game data (game history, active effects/cooldowns, game session participation, heardle stats).
- Utility state (AFK status, command cooldowns, alt relationships, temporary voice channels).

The following are **retained** for community safety and anti-abuse purposes:

- Server moderation records: punishments, case notes, watchdog audit logs, jail history, blacklists, and suspicious-activity logs.
- The `user_identities` mapping row is also kept so retained moderation records remain resolvable by server staff.

## Requirements
See `requirements.txt` for exact pinned versions. Major libraries:
- discord.py: Core Discord API library
- SQLAlchemy + asyncpg: Async database ORM and driver
- python-dotenv: Environment variable loading
- aiosqlite (used optionally if configured elsewhere) but primary target is Postgres
- cryptography, hmac, hashlib: Secure operations (wallet keys, fairness)
- google generative libraries (optional advanced features)
- numpy, pillow, geopy, h3, timezonefinder: Assorted utility features

## Supported Python Version
Recommended Python 3.11 or newer. Ensure compatibility with pinned dependency versions (numpy >= 2.x).

## Quick Start
1. Clone repository:
   ```git clone https://github.com/mistrustt/TPNEBOT.git```
   ```cd TPNEBOT```
2. Create virtual environment (Windows PowerShell example):
   ```python -m venv .venv```
   ```.venv\\Scripts\\activate```
3. Install dependencies:
   ```pip install --upgrade pip```
   ```pip install -r requirements.txt```
4. Prepare Postgres (local default):
   - Install PostgreSQL
   - Ensure a database named postgres exists (default cluster) or adjust URL
   - Create user and grant privileges if needed
5. Create `.env` file in project root:
   `TOKEN=your_bot_token_here`
   `DB_PW=your_postgres_password_here`
   `DEVELOPER_CHANNEL_ID=123456789012345678`
6. Run database initialization automatically by starting bot:
   ```python bot.py```
7. Invite bot to your server using the OAuth2 URL (discord developer portal).

## Environment Variables (.env)
- `TOKEN`: Discord bot token (required)
- `DB_PW`: Password for Postgres user postgres (required)
- `DEVELOPER_CHANNEL_ID`: Channel ID for internal error reporting (required)
- `USER_ID_HASH_KEY`: HMAC-SHA256 key used to hash Discord user IDs before storing them in the database. Must match the key used when running `migrations/secure_user_ids.sql` (required for hashed storage; falls back to `STATS_SALT`)
- `LOCATION_ENCRYPTION_KEY`: Fernet key used to encrypt user locations stored in `user_locations`. Must match the key used by the running bot (required; loaded from Infisical)

### Docker (quick start)

```bash
cp .env.example .env       # fill in Infisical credentials and non-sensitive config
docker compose build
docker compose up -d
docker compose logs -f app
```

`docker-compose.yml` brings up the bot and a PostgreSQL 16 service with a named volume
(`tpnebot_pgdata`). The `app` service waits for the database to become healthy before
starting. The bot connects outbound to Discord only and does not expose any host ports.

## Disclaimer
This documentation was generated automatically based on the current repository structure.

