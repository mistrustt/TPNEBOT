# TPNEBOT Economy & Casino: A User's Guide

This guide explains how the economy and casino work **from your perspective** as a player. It is not a code reference; it is a map of the concepts, rules, and forces that shape every coin you earn, bet, lose, or win.

---

## 1. The Big Picture: A Closed-Circuit Economy

TPNEBOT's economy is not a faucet that prints infinite money. It is a **closed circuit** with one central reservoir:

- **The Treasury** is the bot's central bank. It holds the bulk of the currency.
- **Your Wallet** is money you can spend, gamble, or transfer immediately.
- **Your Bank** is money you have parked safely (mostly safe from robbery).
- **Crypto Assets** are speculative holdings whose value floats with mock market prices.

Every payout you receive comes from the Treasury. Every bet you lose goes back to the Treasury. Transfers, robberies, fees, shop purchases, and loans all move value around inside the same pool. Nothing enters or leaves the system except through the Treasury's own minting and burning.

This matters because **the Treasury's health determines almost everything**: how much you can bet, how much you can win, how expensive it is to move money, and how generous the daily rewards feel.

---

## 2. Supply, Circulation, and Treasury Health

### Total Supply

`Total Supply` = `Treasury` + `Circulating Supply`

- **Treasury**: coins held by the system.
- **Circulating Supply**: coins currently sitting in every player's wallet + bank + crypto holdings.

When the bot starts fresh, nearly all currency is in the Treasury. As players earn dailies, win bets, or receive rewards, currency moves into circulation.

### Treasury Health

**Treasury Health** is the single most important number in the economy:

```
Treasury Health = Treasury ÷ Total Supply
```

- A **high health** (e.g., 70–80%) means the Treasury is flush and the economy can afford big bets, big payouts, and low fees.
- A **low health** (below 20%) means the Treasury is stressed. Bet limits shrink, fees rise, and payouts may be capped to prevent collapse.
- A **10% hard floor** protects the Treasury from ever being fully drained. Below that, payouts become increasingly restricted.

The bot publishes this number in `!economy` and tracks it historically in `!economy trends` and `!economy health`.

### Dynamic Target

The Treasury target is not a fixed 50%. It adapts to how mature the economy is:

- If almost no currency has left the Treasury yet, the target is high (around 80%).
- As circulation grows, the target drops.
- At very high circulation, the target can fall to roughly 20–32%.

This prevents the system from frantically trying to refill the Treasury to impossible levels when most of the money is already in players' hands.

---

## 3. Your Money: Wallet, Bank, Net Worth

### Wallet
- This is your liquid cash.
- Used for gambling, transfers, shop purchases, loans, robbing, and paying fees.
- Robbery attempts can steal from your wallet.

### Bank
- A protected storage account.
- Moving money into/out of the bank costs no Treasury fee; it is purely internal.
- Bank money is harder to steal through normal robbery, though dedicated bank robberies can target it.
- Useful for protecting large balances.

### Net Worth

Your **net worth** determines your wealth tier and many of your limits. It includes:

- Wallet balance
- Bank balance
- Crypto holdings valued at current prices

It explicitly **does not** include shop items, inventory, or other virtual goods. A trillion coins in items does not make you a "wealthy" player for tax purposes.

---

## 4. The Auto-Rebalancer: How the Economy Self-Heals

The bot runs a background rebalancer that watches Treasury Health and tries to keep it near its dynamic target.

### When the Treasury is too high

If the Treasury is swollen far above its target, the bot **burns** currency out of the Treasury. This permanently removes it from Total Supply, fighting inflation and re-centering the economy.

### When the Treasury is too low

If the Treasury is below its target, the bot **mints** new currency into the Treasury. This expands Total Supply and lets the economy keep paying out winners. The lower the health, the faster and stronger the refill becomes.

### Rebalance Speed

- Normally the rebalancer acts cautiously, with a cooldown and a small daily mint cap.
- When health is stressed, the cooldown shortens and the daily mint cap increases so the Treasury can recover quickly.
- A "dead zone" stops tiny, annoying adjustments when health is already close enough to target.

### The Hard Floor

The bot will **never** burn the Treasury below 10% of Total Supply. If health ever reaches the floor, payouts are softly capped rather than cut off completely.

---

## 5. Volume-Linked Refills: Gambling Feeds the Treasury

There is a second refill mechanism tied to **gambling activity**.

Every time players lose bets, those coins flow into the Treasury. When the Treasury is below its target, the bot can mint **a fraction of those recent deposits** back into the Treasury as a volume-linked refill.

- More gambling activity = more deposits = faster refill.
- Near target, only ~1% of recent deposits are recycled.
- Deep stress, up to ~5% of recent deposits can be recycled.
- This refill uses its own daily budget so it does not steal from the main rebalancer.

In plain terms: **when the economy is low, your losses help refill the pot**, which means the next round of winners can still get paid.

---

## 6. Fees: The Hidden Cost of Moving Money

Almost every movement of money into or out of the Treasury carries a **dynamic fee**.

### Base Fee Rate

The fee starts at roughly 1% and adjusts based on Treasury Health:

- Healthy Treasury → lower fees.
- Stressed Treasury → higher fees, but capped so they do not spiral.

The fee curve is linear when health is below target, so it rises steadily rather than exploding.

### Wealth-Tier Fee Multiplier

The richer you are relative to Total Supply, the more you pay in fees. This is the "whale tax":

| Tier | Share of Total Supply | Fee Multiplier |
|------|------------------------|------------------|
| 0 | below 0.5% | 1.0× (normal) |
| 1 | 0.5% – 0.99% | 1.5× |
| 2 | 1% – 1.99% | 2.0× |
| 3 | 2% – 4.99% | 3.0× |
| 4 | 5% or more | 5.0× |

This makes it expensive for the biggest holders to shuffle money around, which helps keep currency circulating among more players.

### Fee-Exempt Transfers

Some transactions are **fee-free**:

- Game payouts (winnings)
- Rakeback claims
- Rewards (daily, weekly, drops, airdrops, beg rewards)

You receive the exact amount shown.

### Where Fees Go

Fees are paid into the Treasury, increasing its health. They are one of the main ways the economy recovers from heavy payouts.

---

## 7. Wealth Tiers: The Rich-Player Penalties

Wealth tiers are calculated from your **net worth ÷ total supply**. Crossing a threshold applies multipliers to several activities:

| Tier | Threshold | Bet Limit | Loan Size | Transfer Size | Fees |
|------|-----------|-----------|-----------|---------------|------|
| 0 | < 0.5% | 1.0× | 1.0× | 1.0× | 1.0× |
| 1 | ≥ 0.5% | 0.8× | 0.7× | 0.8× | 1.5× |
| 2 | ≥ 1% | 0.5× | 0.4× | 0.5× | 2.0× |
| 3 | ≥ 2% | 0.25× | 0.2× | 0.3× | 3.0× |
| 4 | ≥ 5% | 0.1× | 0.05× | 0.1× | 5.0× |

Being rich gives you leaderboard bragging rights, but it also makes gambling, borrowing, transferring, and even paying fees harder. The system intentionally discourages hoarding huge percentages of the economy.

---

## 8. Betting Limits: Why You Can't Always Go All-In

Your maximum gamble is not a fixed cap. It is calculated from several live factors:

### Core Inputs

- **Treasury Health**: the lower it is, the smaller your max bet.
- **Your Net Worth**: you cannot bet more than you own.
- **Game's Max Payout Multiplier**: high-payout games like Crash or SuperGamble reduce your allowed bet so one lucky win cannot bankrupt the Treasury.
- **Dynamic Treasury Target**: below target, the hard exposure cap shrinks.

### Extra Modifiers

- **Liquidity**: if too little currency is circulating, bets tighten.
- **Transaction Volume**: if activity is surging, bets tighten to reduce risk.
- **Active Users**: if few players are active, bets tighten to protect the Treasury.
- **Wealth Tier**: high-tier players face the multipliers above.

### Minimum Floor

New or poor players get a small minimum floor so they can still participate even when the economy is stressed.

### What You See

If you try to bet above the limit, the bot usually **auto-caps your bet** and tells you the new maximum. High-rollers see a warning that their bet was reduced.

---

## 9. Earning Money Without Gambling

### Daily (`!daily`)
- Once every 24 hours.
- Base reward is random between about 15,000 and 55,000.
- Multiplied by the **economic reward multiplier** and your personal **earning multiplier**.
- Fee-exempt reward.

### Weekly (`!weekly`)
- Once every 7 days.
- Base reward is random between about 110,000 and 310,000.
- Same multiplier system as daily.

### Beg (`!beg`)
- Very short cooldown.
- 40% chance of success.
- Reward depends on which fictional character appears and a random multiplier.
- Also scaled by the economic reward multiplier.

### Jobs (`!job`)
- Apply for a job (`janitor`, `cashier`, `developer`, `manager`, `executive`).
- Higher-tier jobs have higher salaries but lower hire chances.
- Use `!job work` to earn your salary.
- Miss work for 48+ hours and you are automatically fired.

### Drops and Airdrops (`!drop`, `!airdrop`)
- Drop coins in a channel for the fastest clicker to claim.
- Airdrop splits coins among everyone who joins within 15 seconds.
- Unclaimed drops and airdrops are refunded.

### Reputation / Karma
- Some actions award small reputation points (winning games, foiling robberies).
- Others reduce it (failed robberies).

### Economic Reward Multiplier

This global multiplier adjusts all rewards based on economic health:

- Healthy Treasury → rewards may be boosted.
- Stressed Treasury → rewards may be reduced.

You will see it in parentheses when it is not 1.0×.

---

## 10. The Casino: Games, House Edge, and RTP

### Available Games

| Game | Type | Notes |
|------|------|-------|
| `!gamble` | Coinflip | ~50% win chance, 2× payout |
| `!supergamble` / `!sg` | High-risk lottery | ~15% win chance, massive multipliers |
| `!dice` / `!roll` | Dice sum bet | Bet on even/odd or exact total |
| `!slots` | Slot machine | Multiple reels and paylines |
| `!blackjack` | Card game | Standard 21 rules |
| `!roulette` | Wheel betting | Interactive American roulette board |
| `!mines` | Grid evasion | Click gems, avoid bombs, cash out anytime |
| `!double` | Double-or-nothing | Flip to keep doubling or cash out |
| `!crash` | Multiplayer timing | Cash out before the crash |
| `!ladder` / `!luckyladder` | Risk ladder | Climb steps, survive to win |
| `!poker` / `!headsup` | Heads-up poker | Play or fold against the bot |
| `!hilo` | Card guess | Higher or lower than the current card |
| `!cards` | Card draw game | Varies by implementation |

### House Edge

Every game has a built-in **house edge** so that, on average, the house wins. This is how the Treasury grows over time despite paying out winners.

- The base house edge is typically around **4%**.
- It can never fall below **1%**, even with heavy VIP bonuses.
- When Treasury Health drops, a **health surcharge** is added: +0.5% to +3.0% depending on how stressed the Treasury is.
- Higher VIP tiers reduce the effective house edge through RTP bonuses.

### RTP (Return to Player)

RTP is the mirror of house edge:

```
RTP ≈ 100% - House Edge
```

A 96% RTP means that, over infinite play, you would get back 96 coins for every 100 wagered. Short-term luck can still make you rich or broke.

### VIP RTP Bonus

Higher VIP tiers add **RTP bonus points**:

| Tier | RTP Bonus |
|------|-----------|
| Bronze | +0% |
| Silver | +0.5% |
| Gold | +1.0% |
| Platinum | +1.5% |
| Diamond | +2.0% |

A Diamond player effectively turns a 96% RTP game into roughly a 98% RTP game. This matters enormously over millions in wagers.

### Luck Saves and Item Boosts

Some shop items can:
- Save you from a losing roll ("luck save").
- Multiply your winnings when you win.
- Reduce your losses.

These are applied after the core game outcome is determined.

### What Happens If FairGate Is Down

If the external provable-fairness server cannot be reached when you place a bet, the bot **refunds your bet** and cancels the game. You do not lose money to a network failure.

---

## 11. VIP Tiers and Rakeback: Rewards for Volume

### How VIP Works

VIP is based on **total amount wagered across all games**, not on balance or wins/losses.

| Tier | Required Wagered | Rakeback Rate | RTP Bonus |
|------|------------------|---------------|-----------|
| Unranked | 0 | 0% | 0% |
| Bronze | 100 billion | 1% | 0% |
| Silver | 100 trillion | 2% | 0.5% |
| Gold | 100 quadrillion | 3% | 1.0% |
| Platinum | 100 quintillion | 5% | 1.5% |
| Diamond | 100 sextillion | 10% | 2.0% |

### Rakeback

Every wager earns you a small percentage back into a separate **rakeback balance** based on your tier:

- Bronze: 1% of every wager.
- Diamond: 10% of every wager.

This is **not** a game win. It is a rebate on your betting volume. You claim it with `!vip claim`.

### Why Rakeback Matters

Rakeback is one of the only ways to reliably soften losses. A Diamond player gets 10% of every bet back regardless of outcome, which can turn a losing session into a much smaller loss.

### Claiming Rakeback

- Use `!vip claim` (one-hour cooldown).
- Rakeback claims are **fee-exempt**: you receive the full accumulated amount.
- Unclaimed rakeback sits in reserve and can grow indefinitely.

### Tracking Progress

- `!vip status` shows your tier, progress bar, total wagered, rakeback rate, and RTP bonus.
- `!vip tiers` shows every tier and requirement.
- `!vip leaderboard` shows top players by total wagered.

---

## 12. Provable Fairness: Can You Trust the Roll?

TPNEBOT uses a **provably fair** system for casino outcomes. The idea: neither you nor the bot can secretly manipulate a result after the bet is placed.

### The Three Ingredients

1. **Server Seed**: generated by the server and hidden from you until later.
2. **Client Seed**: supplied by you (or randomly assigned). You can change it with `!casino setseed`.
3. **Nonce**: a counter that increments on every draw so the same seeds cannot produce duplicate results.

### How a Result Is Made

For each draw the bot computes:

```
HMAC_SHA256(key = server_seed, message = "client_seed:nonce:tag")
```

The first 8 bytes of the result become a random number. Rejection sampling removes bias for ranges that are not powers of two.

### Commitment and Reveal

- Before you bet, the bot publishes the **hash of the server seed** (a commitment).
- After the bet, the bot **reveals the actual server seed**.
- Anyone can then recompute the outcome and verify it matches.

### Verifying a Game

Use `!game verify <game> <nonce>` to look up a recorded game and check it against the public FairGate verification endpoint. The bot shows the server seed hash, client seed, exact parameters, and the recomputed result.

### Why This Matters

You do not have to trust the bot's internal randomness. As long as the published server seed hash matches the revealed seed, and your client seed + nonce are what you expect, the outcome is deterministic and tamper-evident.

---

## 13. The Shop and Items

The shop sells items that can give you advantages or effects.

### Item Types

- **Consumables / Redeemables**: one-time use items.
- **Defensive Auras**: may protect against robbery or negative effects.
- **Offensive Items**: targetable against other players.
- **Gambling Multipliers**: boost winnings in casino games.
- **Collectibles**: vanity or status items.

### Effects

Active effects can:
- Boost RTP temporarily.
- Multiply gambling winnings.
- Reduce robbery damage.
- Apply other buffs or debuffs.

### Cooldowns

Many items have cooldowns after use. You can check remaining cooldown in your inventory.

### Beta Status

The shop and inventory systems are in beta. Report any weird behavior.

---

## 14. Social & Economic Crime

### Robbery (`!rob`)

Attempt to steal from another player's **wallet**. Outcomes include:
- Critical success: steal a large chunk, plus a 10% counter-loss penalty charged to the victim.
- Success: steal a moderate chunk.
- Partial failure: steal something, but half is recouped by the victim.
- Failure: you are fined a percentage of your own balance, and the target receives a small bonus.
- Bank robbery: rare outcome that targets the victim's bank instead.

Requirements:
- Target wallet must have at least 10,000.
- You must have at least 100,000 to attempt.

### Drain (`!drain`)

A more aggressive attempt to drain a target's wallet. Subject to fees and defenses.

### Bounties (`!bounty`)

Place a bounty on another user. If someone successfully robs that user, they claim the bounty.

### Robbery Defenses

Some items or effects can block robbery attempts or reduce losses.

### Reputation Impact

Failed robberies cost reputation. Foiling a robbery gives reputation.

---

## 15. Circuit Breakers: Emergency Brakes

If the economy becomes dangerously frozen — very low activity **and** almost no currency circulating — the bot can trip an **economic circuit breaker**.

When active:
- Player-to-player transfers are blocked.
- Robberies and drains are blocked.

This is a safety valve to stop a runaway drain while the rebalancer and volume-linked minting restore health. It is rare in an active economy.

---

## 16. Loans, Transfers, and Crypto

### Loans (`!loan`)

You can take a loan from the Treasury. The maximum loan depends on your net worth, Treasury Health, and wealth tier. Loans must be repaid; unpaid loans can lead to forced recovery or penalties.

### Transfers (`!transfer` / `!give`)

Send money to another player. Transfers incur:
- A base fee scaled by Treasury Health.
- A wealth-tier fee multiplier if you are very rich.

You can choose whether the fee is deducted from the transferred amount or added on top.

### Crypto (`!crypto`)

The bot supports mock cryptocurrency trading:
- Buy crypto with coins.
- Sell crypto back for coins.
- Transfer crypto to other players.
- Your crypto value counts toward net worth for wealth-tier calculations.

Crypto prices move over time, creating profit/loss.

---

## 17. How Everything Interconnects

The economy is not a collection of isolated features. It is a feedback web:

```
Players earn dailies / jobs / rewards
        ↓
Currency moves from Treasury → Circulation
        ↓
Players gamble / transfer / rob / shop
        ↓
Fees and losses flow back to Treasury
        ↓
Treasury Health changes
        ↓
Bet limits, fees, house edge, reward multipliers adjust
        ↓
Player behavior changes
        ↓
Back to top
```

### Concrete Interconnections

1. **Heavy gambling losses** increase Treasury deposits, which triggers volume-linked minting when health is low. So your losses help refill the pot.

2. **Big winners** drain the Treasury, lowering health, which then raises fees and lowers bet limits for everyone until the rebalancer refills.

3. **Rich players** face higher fees and lower bet limits, encouraging them to spend, invest, or donate rather than hoard.

4. **VIP players** wager more to climb tiers, which earns rakeback, which they can claim fee-free, which puts currency back into circulation.

5. **Crypto gains** increase net worth, which can push a player into a higher wealth tier, which then raises their fees and lowers their gambling limits.

6. **Daily/weekly rewards** shrink when the Treasury is stressed, reducing new money entering circulation and helping the Treasury recover.

7. **Payouts are soft-capped** when health is very low, so even a jackpot cannot bankrupt the Treasury in one spin.

---

## 17. Reading the Economy: Useful Commands

| Command | What It Shows |
|---------|---------------|
| `!balance` / `!bal` | Your wallet, bank, recent transactions |
| `!economy` | Treasury, supply, fee rate, passive rate, your portfolio share |
| `!economy health` | Overall economic health score and breakdown |
| `!economy trends` | Historical metrics over the last N days |
| `!leaderboard` / `!lb` | Top 10 players by net worth |
| `!vip status` | Your VIP tier, wagered, rakeback rate, RTP bonus |
| `!vip claim` | Claim accumulated rakeback |
| `!game stats <game>` | Your win/loss stats for a specific game |
| `!game history` | Your recent casino game history |
| `!game verify <game> <nonce>` | Verify a FairGate game outcome |
| `!casino seed` | Your current client seed and server seed hash |
| `!casino setseed [seed]` | Change your client seed |
| `!shop` | Browse shop items |
| `!inventory` | View your items |
| `!crypto` | Crypto trading commands |

---

## 18. Practical Strategy Tips

### For New Players
- Claim `!daily` and `!weekly` consistently.
- Use `!beg` on cooldown early on.
- Bank large balances to protect them from robbery.
- Start with low-risk games like `!gamble` or `!dice`.

### For Gamblers
- Track `!economy health` and `!economy trends`. Bet more aggressively when health is high; tighten up when it is low.
- Higher VIP tiers dramatically improve your expected return through rakeback and RTP bonuses. Volume is rewarded.
- Use rakeback claims during healthy Treasury periods to avoid any payout friction.
- Understand that SuperGamble and high-multiplier games have huge variance; bankroll management matters.

### For Wealthy Players
- Remember that holding 0.5%, 1%, 2%, or 5% of total supply triggers penalties.
- Consider keeping value in bank, crypto, or shop assets if you want to manage your wealth tier, but note that net worth still includes bank and crypto.
- Expect higher fees on transfers; plan large movements accordingly.

### For Economy Watchers
- If Treasury Health is rising, rewards and bet limits will improve.
- If Treasury Health is falling, the rebalancer will eventually mint more, but in the short term fees rise and payouts shrink.
- Volume-linked minting means that **active gambling helps the economy recover faster**.

---

## 19. Summary in One Paragraph

TPNEBOT's economy is a closed loop centered on the Treasury. Treasury Health controls fees, bet limits, payouts, and reward sizes. Players earn money through dailies, jobs, begging, drops, and gambling; they spend or lose it back through games, fees, transfers, robberies, loans, and the shop. The bot actively rebalances by minting when the Treasury is low and burning when it is high, with gambling volume accelerating refills. Wealthy players face whale taxes, VIP players earn rakeback and RTP bonuses, and every casino outcome is provably fair. Understanding these forces lets you decide when to bet big, when to bank, and when to let the economy catch its breath.
