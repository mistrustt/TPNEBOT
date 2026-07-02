# TPNEBOT

NOTE: This file was Co-Authored by Claude on 2026-07-02.

TPNEBOT is a modular Discord bot featuring an economy, casino style games with provable fairness, moderation and community management tools, music and voice utilities, role management features, and extensible command cogs. It uses discord.py, SQLAlchemy (async), asyncpg, and a Postgres database.

## Key Features
- Economy and virtual currency with wallets, transactions, items, shop, bounties
- Casino and game suite (dice, gamble variants, slots, blackjack, mines, crash, ladder, roulette, poker, ridebus, etc.)
- Provable fairness system (HMAC based deterministic RNG and verifiers)
- Moderation (punishments, jail, mute variants, case notes, watchdog auditing)
- Community utilities (timezone, location, reactions, reputation, streaks, LastFM integration)
- Music and voice channel utilities (temporary voice channels, join to create management)
- Role and booster management, command role restrictions, lockdown handling
- Developer quality of life (dynamic cog loading, debug mode, structured logging)

## Required Discord Privileged Intents

TPNEBOT requires the following Discord privileged intents. Each is enabled only because the bot's stated functionality genuinely requires it and no reasonable alternative exists.

### Message Content
Used for:
- **Spam-channel enforcement** (`community` cog): deletes any message in a designated channel that is not exactly `"999"`.
- **Automated moderation** (`watchdog` cog): detects and deletes PII, credit-card numbers, and Discord tokens; logs the event to a server-configured channel.
- **Message logging** (`watchdog` and `general` cogs): records deleted/edited message content to server-configured mod-log channels.
- **Attachment filtering** (`moderation` cog): removes disallowed audio attachments.
- **Interactive prompts** (`music`, `owner`, `roles` cogs): reads user reply content for trivia, setup, and selection flows.
- **Channel cleanup** (`moderation` cog): identifies bot/command messages during `purge`.

The bot **does not** use message content to train AI/ML models, sell or share data, scrape users, profile users, or make decisions about employment, housing, insurance, etc.

### Server Members
Used for:
- **Server statistics**: human/bot/online member counts in `membercount` and `serverinfo`.
- **Member resolution**: lookup by name/nickname/ID/mention in moderation, role, and owner commands.
- **Role management**: listing role members, assigning/removing roles.
- **Rejoin handling**: `on_member_join` re-applies jail roles and autoroles.
- **Minimum-member gate**: leaves servers with fewer than 10 human members on `on_guild_join`.
- **Voice channels**: temporary-channel ownership based on current VC members.
- **Name-change tracking**: logs username/nickname changes when configured.

### Presence
Used for:
- **Server statistics**: online/idle/dnd/offline breakdown in `membercount` and `serverinfo`.
- **Spotify lookup** (`music` cog): finds a user's active Spotify listening activity.

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

## Architecture Overview
High level module structure:
- `bot.py`: Entry point, logging, database init, dynamic cog loading, status task
- `cogs/`: Feature modules (casino, economy, moderation, music, etc.) each as an extension
- `database/manager.py`: Database session factory, config loading, util methods (not shown here but referenced)
- `database/models.py`: SQLAlchemy ORM models (wallets, transactions, items, punishments, role histories, blocks, games, seeds)
- `utils/fairness.py`: Provable fairness deterministic RNG and verification functions
- `utils/cooldown.py`: Cooldown embed helper for command rate limiting

Detailed architecture is described in [ARCHITECTURE.md](https://github.com/mistrustt/TPNEBOT/blob/main/docs/ARCHITECTURE.md).

## Database
Uses Postgres with async SQLAlchemy. Models include:
- Wallet, Transaction, BankAccount, Item, ShopItem, Bounty
- Punishment, CaseNote, WatchdogLog, Jail related tables
- GameHistory, GameStats, MinesSettings, seeds and fairness related fields embedded in wallet
- Role, reputation, reaction, settings tables

Further schema explanations are in [DATABASE.md](https://github.com/mistrustt/TPNEBOT/blob/main/docs/DATABASE.md).

## Provable Fairness
Fairness uses HMAC SHA256 with server seed, client seed, and nonce to produce unbiased draws. Verification helpers in `utils/fairness.py` allow external reproduction and auditing of game outcomes. See [FAIRNESS.md](https://github.com/mistrustt/TPNEBOT/blob/main/docs/FAIRNESS.md) for reproducibility procedures.

## Development
Use logging output in `discord.log` for historical analysis. Rotating file handler restricts size.

## Extending
Add new cogs by creating a file in `cogs/` and loading via config (BotConfig table) or default auto load. Each cog should define a `setup` function returning an extension.

## Configuration
Runtime configuration stored in database tables (BotConfig, ServerSettings, CommandStatus, CommandRoleRestriction, etc.). See [CONFIGURATION.md](https://github.com/mistrustt/TPNEBOT/blob/main/docs/CONFIGURATION.md).

## Deployment
Production deployment guidance (containers, systemd, Docker) is documented in [DEPLOYMENT.md](https://github.com/mistrustt/TPNEBOT/blob/main/docs/DEPLOYMENT.md).

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

## Security
Operational database tables store Discord user IDs as deterministic HMAC-SHA256 hashes rather than raw values. A single `user_identities` mapping table records `user_hash -> user_id` so the bot can resolve hashes back to raw IDs when required by Discord API calls. Run `migrations/secure_user_ids.sql` to migrate an existing database, and keep `USER_ID_HASH_KEY` secret and backed up — losing it prevents resolving stored hashes. See [SECURITY.md](https://github.com/mistrustt/TPNEBOT/blob/main/docs/SECURITY.md).

## Privacy
TPNEBOT collects and processes Discord data only as needed to operate its features. Discord user IDs are hashed before storage, locations are encrypted at rest, and a `!forgetme` command lets users delete their personal/economy/game/social data. Server moderation records are retained for community safety. TPNEBOT is a non-commercial project and does not sell, monetize, or use data for AI/ML training. See the full [PRIVACY.md](https://github.com/mistrustt/TPNEBOT/blob/main/docs/PRIVACY.md).

## Disclaimer
This documentation was generated automatically based on the current repository structure.

