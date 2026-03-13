# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Bot

```bash
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the bot
python bot.py
```

Required environment variables in `.env`:
- `TOKEN`: Discord bot token
- `DB_PW`: PostgreSQL password for the postgres user
- `DEVELOPER_CHANNEL_ID`: Channel ID for internal error reports

## Architecture

### Entry Point
`bot.py` - DiscordBot class handles startup, logging, database init, dynamic cog loading, and status updates.

### Directory Structure
- `cogs/` - Feature modules (economy.py, casino.py, moderation.py, music.py, etc.)
- `database/` - Data layer
  - `models.py` - SQLAlchemy ORM models
  - `manager.py` - Database session factory and async operations (~6000+ lines)
  - `blockchain.py` - Internal ledger operations
- `utils/` - Shared utilities
  - `fairness.py` - Provable fairness HMAC-based RNG and verifiers
  - `cooldown.py` - Cooldown embed helpers
  - `embeds.py` - Common embed templates
  - `admin_api.py` - HTTP admin API server

### Database Models (key tables)
- **Economy**: Wallet, Transaction, BankAccount, Item, ShopItem, Bounty, Supply, Block
- **Games**: GameHistory, GameStats, MinesSettings
- **Moderation**: Punishment, CaseNote, WatchdogLog, JailSetting, JailedUser, UserAlt
- **Access Control**: CommandStatus, CommandRoleRestriction, Blacklist
- **User Data**: UserRoleHistory, UserNameHistory, Reputation, UserTimezone, UserLocation

### Command Invocation Flow
The bot's `invoke` method enforces: maintenance mode check → blacklist check → command/channel enable check (CommandStatus) → role restriction check (CommandRoleRestriction) → cooldown enforcement.

### Cog Pattern
Each cog in `cogs/` follows discord.py extension pattern:
```python
class SomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

async def setup(bot):
    await bot.add_cog(SomeCog(bot))
```

Cogs are loaded dynamically based on `BotConfig.loaded_cogs` or all discovered cogs by default.

### Database Manager Pattern
The `DatabaseManager` class in `database/manager.py` is attached to the bot as `bot.database`. All database operations go through async methods on this class. Use `self.bot.database` from within cogs.

### Fairness System
Games use HMAC-SHA256 for provable fairness via `utils/fairness.py`. Wallet model contains `server_seed`, `previous_server_seed`, `client_seed`, and `nonce`. Each draw increments nonce. Verifier functions reproduce outcomes given seeds and nonce.

### Economy Commands
The economy command group in `cogs/economy.py` uses subcommands:
- `!economy stats` - View economy statistics
- `!economy health` - View economic health score with recommendations
- `!economy trends [days]` - View economic trends

## Key Conventions

- All currency values use `Decimal` from `decimal` module for precision
- Async database operations use `async with self.async_sessionmaker() as session`
- Discord embeds are used extensively for command responses
- Logging uses the custom `LoggingFormatter` with colored output and rotating file handler to `discord.log`
- Background tasks use `discord.ext.tasks` loop decorator

## Testing

No automated test suite exists. Manual testing by running the bot and invoking commands in Discord.

## Database Migrations

Schema is initialized programmatically in `DatabaseManager.__init__()`. For structural changes:
1. Add new columns with nullable defaults
2. Populate backfill logic in initialization routine
3. Consider adopting Alembic for versioned migrations when schema churn increases