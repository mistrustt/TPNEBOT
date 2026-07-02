# Privacy Policy for TPNEBOT

**Effective date:** 2026-07-02

**Last updated:** 2026-07-02

This Privacy Policy describes how TPNEBOT ("the bot", "we", "us") collects, uses, stores, and deletes data when you interact with it on Discord. By adding or using TPNEBOT in a Discord server, you acknowledge this policy.

TPNEBOT is operated as a hobby/community project. **We do not charge for use of the bot and we do not monetize user data in any way.**

---

## 1. What Data We Collect

### 1.1 Discord identifiers
- Your Discord **user ID**, **guild ID**, and **channel ID** are used to provide bot features.
- Discord user IDs are stored as **deterministic HMAC-SHA256 hashes** in most operational database tables. A single `user_identities` mapping table retains the original ID so staff can resolve moderation records.
- Guild and channel IDs are stored as plain numeric values because they are server-level configuration data.

### 1.2 Message content
We read message content only where required by specific features:
- **Spam-channel enforcement:** messages in a designated channel that are not exactly `"999"` are deleted.
- **Automated moderation:** messages are scanned for PII, credit-card numbers, and Discord tokens; matching messages are deleted and logged.
- **Message delete/edit logging:** deleted and edited message content is posted to a server-configured mod-log channel.
- **Attachment filtering:** messages with disallowed audio attachments are removed.
- **Interactive command prompts:** some commands ask you to reply with a number or choice.
- **Channel cleanup:** the `purge` command identifies bot/command messages to delete.

We do **not** read message content for general monitoring, advertising, or AI/ML training.

### 1.3 Member/presence data
- **Guild member lists** are used for server statistics, role management, and member lookup commands.
- **Online/idle/dnd/offline status** is used only for the `membercount`/`serverinfo` status breakdown.
- **Spotify activity** is read only when a user invokes a music feature that looks up their currently playing Spotify track.

### 1.4 Economy, game, and social data
We store data needed for the bot's economy, casino, games, music, and community features, including but not limited to:
- Wallet and bank balances, transaction history, inventory, items, jobs, loans, crypto holdings.
- Game history, heardle stats, rakeback, VIP status.
- Reputation, reaction counters, favorite songs, timezone, location, role/nickname history.
- Command usage, latency, and error statistics (aggregated by user hash).

### 1.5 Location data
- Locations provided via weather/timezone commands are **encrypted at rest** with Fernet (`LOCATION_ENCRYPTION_KEY`) and stored as coarse coordinates or lookup prefixes.

### 1.6 Third-party service data
- **Last.fm username:** stored when you link your Last.fm account.
- **OpenRouter:** when you use `!ai` or `!8ball`, your question/prompt text is sent to OpenRouter's API to generate a response. We do not retain the response beyond sending it back to you.

---

## 2. How We Use Data

We use collected data solely to operate the bot's stated features:
- Server moderation and audit logging.
- Economy, casino, games, and role management.
- Music, weather, and community utilities.
- Bot health and aggregated usage statistics.

We do **not**:
- Sell, license, rent, or commercialize user data.
- Share user data with data brokers, advertising networks, or monetization services.
- Use message content to train machine-learning or large-language models.
- Profile users, discriminate, or make decisions about employment, housing, insurance, etc.
- Contact users outside Discord using API data.
- Send unsolicited direct messages for marketing.

---

## 3. Data Sharing

We share data only in the following limited circumstances:

### 3.1 Discord
Data is processed through Discord's APIs according to Discord's Terms of Service and Developer Policy.

### 3.2 OpenRouter
User-provided prompts for `!ai` and `!8ball` are sent to [OpenRouter](https://openrouter.ai/) for response generation. OpenRouter's privacy practices are governed by their own policies.

### 3.3 Last.fm
Linked Last.fm usernames and requested Last.fm data are sent to [Last.fm](https://www.last.fm/) APIs.

### 3.4 Service providers
We do not use additional service providers for data processing beyond the services listed above.

### 3.5 Legal requirements
We may disclose data if required by applicable law or a valid court order.

---

## 4. Data Retention and Deletion

### 4.1 Automatic deletion
You can delete most of your personal data at any time by running the `!forgetme` command. The bot will send you a confirmation PIN via DM; once confirmed, it removes:

- Profile and social data (reputation, reaction counters, LastFM link, favorite songs, timezone/location, name/role history).
- Economy data (wallet, bank, inventory, crypto, jobs, loans, trade/bounty history, rakeback, VIP status).
- Game data (game history, active effects/cooldowns, game session participation, heardle stats).
- Utility state (AFK status, command cooldowns, alt relationships, temporary voice channels).

### 4.2 Data we retain
Even after `!forgetme`, the following are kept for community safety and anti-abuse purposes:

- Server moderation records: punishments, case notes, watchdog audit logs, jail history, blacklists, and suspicious-activity logs.
- The `user_identities` mapping row so retained moderation records remain resolvable by server staff.

### 4.3 Server removal
If TPNEBOT is removed from a server, the guild's configuration data may remain until a server owner or bot administrator requests deletion.

### 4.4 Aggregated/anonymized data
Aggregated statistics that cannot reasonably identify you may be retained for bot health and improvement.

---

## 5. Data Security

- Discord user IDs are hashed before storage in operational tables.
- Location data is encrypted at rest.
- Secrets (bot token, database password, API keys, encryption keys) are stored in [Infisical](https://infisical.com/) and fetched at runtime; they are not committed to the repository.
- The bot does not expose any host network ports.
- We use commercially reasonable efforts to protect data, including access controls and encrypted storage.

---

## 6. Children's Privacy

TPNEBOT is not directed at users under the age of digital consent. Discord requires users to be at least 13 years old (or the minimum age in their country). We do not knowingly collect data from users under 13.

---

## 7. Monetization

**TPNEBOT is a non-commercial project.** We do not charge for features, accept donations through the bot, or monetize API data. There are no Premium App features.

---

## 8. Changes to This Policy

We may update this Privacy Policy as the bot's features or legal requirements change. The effective date will be updated at the top of this document. Continued use of the bot after changes constitutes acceptance of the updated policy.

---

## 9. Contact and Data Requests

For privacy questions, data-deletion requests, or to report a concern:

- Open an issue in the GitHub repository: [https://github.com/mistrustt/TPNEBOT](https://github.com/mistrustt/TPNEBOT)
- Contact the bot maintainers through the designated support channel on the support Discord server.

For sensitive matters (e.g., security incidents), please refer to [SECURITY.md](SECURITY.md) and contact maintainers privately rather than opening a public issue.
