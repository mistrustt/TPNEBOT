from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import text, distinct
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from typing import List, Optional, Tuple
import secrets
from ..models import (
    Transaction,
    CryptoAsset,
    CryptoPrice,
    Supply,
    EconomicMetricsHistory,
    UserEconomicPreferences,
    Wallet,
    Loan,
    LoanPayment,
    Job,
)
from datetime import datetime, timedelta, timezone
import discord
import uuid
import logging
from decimal import Decimal, ROUND_HALF_UP
from utils.amount import AmountUtils

logger = logging.getLogger("discord.client")

ADMIN_IDS = {284439598422163476, 538773310704582666, 657182369240973312}  # Owner IDs

# Sentinel hash representing the treasury/system account. The raw value 0 is
# used historically; after hashing user IDs we store its hash instead so the
# column stays uniformly VARCHAR(64).
TREASURY_USER_ID = 0


def _treasury_hash() -> str:
    from utils.security import hash_user_id

    return hash_user_id(TREASURY_USER_ID)


_LAST_REBALANCE_AT: Optional[datetime] = None  # module-level memo
_DAILY_MINT_TOTAL: Decimal = Decimal("0")
_MINT_DAY: Optional[datetime] = None  # resets when date changes
_DAILY_BURN_TOTAL: Decimal = Decimal("0")
_BURN_DAY: Optional[datetime] = None

# Treasury floor: treasury must never drop below this fraction of total_supply
TREASURY_FLOOR_RATIO = Decimal("0.10")

# Wealth tier thresholds (percentage of total supply)
WEALTH_TIERS = {
    "tier_1": {"threshold": Decimal("0.005"), "label": "mild"},  # 0.5%
    "tier_2": {"threshold": Decimal("0.01"), "label": "moderate"},  # 1%
    "tier_3": {"threshold": Decimal("0.02"), "label": "severe"},  # 2%
    "tier_4": {"threshold": Decimal("0.05"), "label": "extreme"},  # 5%
}

# Penalty multipliers per tier (applied to different transaction types)
TIER_PENALTIES = {
    0: {
        "bet": Decimal("1.0"),
        "loan": Decimal("1.0"),
        "fee": Decimal("1.0"),
        "transfer": Decimal("1.0"),
    },
    1: {
        "bet": Decimal("0.8"),
        "loan": Decimal("0.7"),
        "fee": Decimal("1.5"),
        "transfer": Decimal("0.8"),
    },
    2: {
        "bet": Decimal("0.5"),
        "loan": Decimal("0.4"),
        "fee": Decimal("2.0"),
        "transfer": Decimal("0.5"),
    },
    3: {
        "bet": Decimal("0.25"),
        "loan": Decimal("0.2"),
        "fee": Decimal("3.0"),
        "transfer": Decimal("0.3"),
    },
    4: {
        "bet": Decimal("0.1"),
        "loan": Decimal("0.05"),
        "fee": Decimal("5.0"),
        "transfer": Decimal("0.1"),
    },
}


class EconomyMixin(BaseManager):
    async def initialize_supply_record(self):
        """
        Creates a default supply record if it doesn't exist.
        This is useful for a fresh economy where no supply record is present.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                supply = await session.get(Supply, 1)
                if not supply:
                    amount = Decimal("1000000000000.00")
                    supply = Supply(
                        id=1,
                        total_supply=Decimal("1000000000000.00"),
                        circulating=Decimal("0.00"),
                        treasury=Decimal("1000000000000.00"),
                    )
                    session.add(supply)
                    logging.info("Created default supply record.")

    async def _atomic_balance_change(
        self,
        session,
        table,
        pk_field,
        pk_value,
        delta: Decimal,
        *,
        balance_col: str = "balance",
        frozen_field: str | None = None,
    ):
        """
        Atomically add `delta` (can be negative) to `balance` column of `table`
        and guarantee non-negative result. Raises on race or freeze.
        """
        params = {"pk_val": pk_value, "delta": delta}

        frozen_sql = f"AND {frozen_field} IS NOT TRUE" if frozen_field else ""
        stmt = text(
            f"""
            UPDATE {table}
            SET    {balance_col} = {balance_col} + :delta
            WHERE  {pk_field} = :pk_val
            {frozen_sql}
            AND   {balance_col} + :delta >= 0
            RETURNING {balance_col}
        """
        )

        result = await session.execute(stmt, params)
        if result.rowcount == 0:
            raise ValueError(
                f"Race or insufficient funds on {table}.{pk_field}={pk_value}"
            )
        return result.scalar_one()

    async def wipe_economy(
        self, caller_id: int, *, confirm: bool = False, dry_run: bool = False
    ) -> dict:
        """
        Comprehensive economy wipe - resets ALL economy-related tables.

        This completely wipes the economy and starts fresh:
        - All wallets, transactions, and supply reset
        - All items, shops, and trade logs cleared
        - All loans, bounties, and payments cleared
        - All game history and sessions cleared
        - All VIP/rakeback data cleared
        - All social currency (reputation, sobs, etc.) cleared
        - All crypto assets and prices cleared
        - All transfer tracking and suspicious activity cleared

        Preserved (NOT wiped):
        - Bot configuration and server settings
        - Moderation data (punishments, jails, watchdog)
        - Command statistics and cooldowns
        - User preferences (timezones, locations)
        - Music/LastFM data
        - Role management data

        Args:
            caller_id: Discord ID of the caller (must be in ADMIN_IDS)
            confirm: Safety flag - must be True to execute
            dry_run: If True, returns what would be wiped without actually wiping

        Returns:
            dict with 'wiped_tables' list and 'dry_run' boolean
        """
        if caller_id not in ADMIN_IDS:
            raise PermissionError("You do not have permission to wipe the economy.")
        caller_id = self.hash_user_id(caller_id)

        if not confirm:
            raise ValueError("`confirm=True` is required as a safety flag.")

        # All economy-related tables to wipe, organized by category
        economy_tables = [
            # Core economy
            "wallets",
            "transactions",
            "supply",
            # Items and trading
            "item_cooldowns",
            "active_effects",
            "trade_logs",
            # Loans and bounties
            "bounties",
            "loans",
            "loan_payments",
            # Jobs system
            "jobs",
            # Games and gambling
            "game_history",
            "game_sessions",
            "game_session_events",
            # Crypto
            "crypto_assets",
            # User economy data
            "user_economic_preferences",
            # VIP system
            "user_vip",
            "rakeback_balances",
            "rakeback_transactions",
            # Economic metrics
            "economic_metrics_history",
        ]

        if dry_run:
            return {
                "dry_run": True,
                "wiped_tables": economy_tables,
                "message": f"Would wipe {len(economy_tables)} economy tables",
            }

        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Build TRUNCATE statement for all economy tables
                tables_str = ", ".join(economy_tables)
                await session.execute(
                    text(f"TRUNCATE TABLE {tables_str} RESTART IDENTITY CASCADE")
                )

            # Re-initialize supply record
            await self.initialize_supply_record()

        return {
            "dry_run": False,
            "wiped_tables": economy_tables,
            "message": f"Successfully wiped {len(economy_tables)} economy tables",
        }

    async def get_supply_record(self) -> Supply:
        """
        Retrieve the supply record from the database or create one if it does not exist.
        """
        async with self.async_sessionmaker() as session:
            supply = await session.get(Supply, 1)
            if not supply:
                await self.initialize_supply_record()
                supply = await session.get(Supply, 1)
            return supply

    async def get_treasury_balance(self) -> Decimal:
        """
        Retrieve the current balance of the treasury.
        """
        supply = await self.get_supply_record()
        return supply.treasury

    async def create_wallet(self, user_id: int) -> None:
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    return

                new_wallet = Wallet(
                    user_id=user_id,
                    balance=Decimal("0.00"),
                    bank_balance=Decimal("0.00"),
                    client_seed=secrets.token_hex(16),
                    nonce=0,
                )
                session.add(new_wallet)
                await session.flush()

                logging.info(f"Created wallet for user {user_id}.")

    async def get_wallet_by_user_id(self, user_id: int) -> Wallet:
        """
        Return the Wallet row for the given user_id, creating one if needed.
        """
        raw_user_id = user_id
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Wallet).where(Wallet.user_id == user_id)
            )
            wallet = result.scalar_one_or_none()

            if not wallet:
                await self.create_wallet(raw_user_id)

                result = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id)
                )
                wallet = result.scalar_one_or_none()

            return wallet

    async def get_wallet_id_for_user(self, user_id: int) -> uuid.UUID:
        """
        Return just the wallet_id (the UUID primary key) for the given user_id,
        creating a wallet if needed.
        """
        raw_user_id = user_id
        user_id = self.hash_user_id(raw_user_id)
        wallet = await self.get_wallet_by_user_id(raw_user_id)
        return f"{wallet.wallet_id}"

    async def freeze_wallet(self, wallet_id: str):
        """Freeze a wallet to block outgoing transactions."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError(f"Wallet {wallet_id} not found.")
                wallet.wallet_frozen = True

    async def unfreeze_wallet(self, wallet_id: str):
        """Unfreeze a wallet, allowing transactions again."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError(f"Wallet {wallet_id} not found.")
                wallet.wallet_frozen = False

    async def get_wallet_balance(self, wallet_id: str) -> Decimal:
        async with self.async_sessionmaker() as session:
            wallet = await session.get(Wallet, wallet_id)
            return wallet.balance if wallet else Decimal("0.00")

    async def get_wallet_balance_by_user_id(self, user_id: int) -> Decimal:
        """
        Return the wallet balance for a given user, creating wallet if it doesn't exist.
        """
        raw_user_id = user_id
        user_id = self.hash_user_id(raw_user_id)
        wallet = await self.get_wallet_by_user_id(raw_user_id)
        return wallet.balance

    async def get_bank_balance(self, wallet_id: str) -> Decimal:
        async with self.async_sessionmaker() as session:
            wallet = await session.get(Wallet, wallet_id)
            return wallet.bank_balance if wallet else Decimal("0.00")

    async def deposit_to_bank(self, wallet_id: str, amount: Decimal, description: str):
        """Transfer funds from wallet to bank without affecting treasury."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = AmountUtils.round_currency(amount)

                # 1) load + gate
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError("Wallet not found.")
                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                # 2) ensure bank row exists
                bank = await session.get(Wallet, wallet_id)
                if not bank:
                    bank = Wallet(wallet_id=wallet_id, bank_balance=Decimal("0.00"))
                    session.add(bank)

                # 3) atomic balance moves
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    wallet_id,
                    -amount,
                    frozen_field="wallet_frozen",
                )
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    wallet_id,
                    +amount,
                    balance_col="bank_balance",
                )

                # 4) record TX
                txid = str(uuid.uuid4())
                session.add(
                    Transaction(
                        id=txid,
                        from_user_id=wallet.user_id,
                        to_user_id=wallet.user_id,
                        amount=amount,
                        description=description,
                        timestamp=discord.utils.utcnow(),
                    )
                )

            return txid

    async def withdraw_from_bank(
        self, wallet_id: str, amount: Decimal, description: str
    ):
        """Transfer funds from bank to wallet without affecting treasury."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = AmountUtils.round_currency(amount)

                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError("Wallet not found.")
                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                bank = await session.get(Wallet, wallet_id)
                if not bank:
                    raise ValueError("Bank account missing.")

                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    wallet_id,
                    -amount,
                    balance_col="bank_balance",
                )
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    wallet_id,
                    +amount,
                    frozen_field="wallet_frozen",
                )

                txid = str(uuid.uuid4())
                session.add(
                    Transaction(
                        id=txid,
                        from_user_id=wallet.user_id,
                        to_user_id=wallet.user_id,
                        amount=amount,
                        description=description,
                        timestamp=discord.utils.utcnow(),
                    )
                )

            return txid

    async def process_p2p_transaction(
        self,
        sender_wallet_id: str,
        receiver_wallet_id: str,
        amount: Decimal,
        description: str,
        guild_id: int = None,
        fee_from_amount: bool = False,
    ):
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        # Calculate wealth-adjusted fee for P2P transfer
        # Base fee rate from dynamic economic factors
        base_fee_rate = await self.get_enhanced_fee_rate("standard")
        base_fee = AmountUtils.round_currency(amount * base_fee_rate)

        async with self.async_sessionmaker() as session:
            async with session.begin():
                sender = await session.get(Wallet, sender_wallet_id)
                receiver = await session.get(Wallet, receiver_wallet_id)
                if not sender or not receiver:
                    raise ValueError("Invalid sender or receiver wallet.")
                if sender.wallet_frozen:
                    raise ValueError("Sender's wallet is frozen.")
                if receiver.wallet_frozen:
                    raise ValueError("Receiver's wallet is frozen.")

                raw_sender_id = await self.resolve_user_hash(sender.user_id)

                # Apply wealth-adjusted fee based on sender's tier
                adjusted_fee = await self.calculate_wealth_adjusted_fee(
                    raw_sender_id, base_fee
                )
                net_amt = AmountUtils.round_currency(amount)

                if fee_from_amount:
                    # Fee deducted from transfer amount (sender pays exact amount, receiver gets less)
                    receiver_gets = net_amt - adjusted_fee
                    if receiver_gets <= 0:
                        raise ValueError("Transfer amount too small to cover fees.")
                    total_deduction = net_amt
                else:
                    # Fee added on top (sender pays amount + fee, receiver gets full amount)
                    receiver_gets = net_amt
                    total_deduction = net_amt + adjusted_fee

                # 1) atomic moves
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    sender_wallet_id,
                    -total_deduction,
                    frozen_field="wallet_frozen",
                )
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    receiver_wallet_id,
                    +receiver_gets,
                )
                # Fee goes to treasury
                await self._atomic_balance_change(
                    session,
                    "supply",
                    "id",
                    1,
                    +adjusted_fee,
                    balance_col="treasury",
                )

                # 2) record DB txs - main transfer and fee
                txid_main = str(uuid.uuid4())
                txid_fee = str(uuid.uuid4())
                session.add_all(
                    [
                        Transaction(
                            id=txid_main,
                            from_user_id=sender.user_id,
                            to_user_id=receiver.user_id,
                            amount=receiver_gets,
                            description=description,
                            timestamp=discord.utils.utcnow(),
                        ),
                        Transaction(
                            id=txid_fee,
                            from_user_id=sender.user_id,
                            to_user_id=_treasury_hash(),  # Treasury
                            amount=adjusted_fee,
                            description=f"P2P transfer fee (base: {base_fee_rate:.2%}, wealth-adjusted)",
                            timestamp=discord.utils.utcnow(),
                        ),
                    ]
                )

            await self.update_supply()

        return txid_main

    async def drain_wallet(
        self,
        target_wallet_id: str,
        robber_wallet_id: str,
        target_user_id: int,
        robber_user_id: int,
        description: str,
        robbery_debuff: Decimal,
        fee_from_amount: bool = True,
    ) -> tuple[str, Decimal]:
        """
        Atomically drain the target's wallet into the robber's wallet.

        The target's balance is re-read inside the transaction so the drain
        succeeds even if the balance changed between the command's initial read
        and the actual transfer. Returns (txid, actual_drained_amount).
        """

        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        base_fee_rate = await self.get_enhanced_fee_rate("standard")
        adjusted_fee: Decimal | None = None

        async with self.async_sessionmaker() as session:
            async with session.begin():
                target = await session.get(Wallet, target_wallet_id)
                robber = await session.get(Wallet, robber_wallet_id)
                if not target or not robber:
                    raise ValueError("Invalid target or robber wallet.")
                if target.wallet_frozen:
                    raise ValueError("Target's wallet is frozen.")
                if robber.wallet_frozen:
                    raise ValueError("Robber's wallet is frozen.")

                # Re-read balance inside the transaction to avoid race conditions.
                fresh_target_balance = AmountUtils.round_currency(
                    Decimal(str(target.balance))
                )
                if fresh_target_balance <= 0:
                    raise ValueError("Target has no funds to drain.")

                # Calculate fee based on the actual current balance.
                raw_target_id = await self.resolve_user_hash(target.user_id)
                drain_amount = AmountUtils.round_currency(
                    fresh_target_balance * robbery_debuff
                )
                base_fee = AmountUtils.round_currency(drain_amount * base_fee_rate)
                adjusted_fee = await self.calculate_wealth_adjusted_fee(
                    raw_target_id, base_fee
                )

                if fee_from_amount:
                    receiver_gets = drain_amount - adjusted_fee
                    if receiver_gets <= 0:
                        raise ValueError("Drain amount too small to cover fees.")
                    total_deduction = drain_amount
                else:
                    receiver_gets = drain_amount
                    total_deduction = drain_amount + adjusted_fee

                if fresh_target_balance < total_deduction:
                    raise ValueError("Target balance is insufficient to cover drain.")

                # Atomic balance changes
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    target_wallet_id,
                    -total_deduction,
                    frozen_field="wallet_frozen",
                )
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    robber_wallet_id,
                    +receiver_gets,
                )
                await self._atomic_balance_change(
                    session,
                    "supply",
                    "id",
                    1,
                    +adjusted_fee,
                    balance_col="treasury",
                )

                txid_main = str(uuid.uuid4())
                txid_fee = str(uuid.uuid4())
                session.add_all(
                    [
                        Transaction(
                            id=txid_main,
                            from_user_id=target.user_id,
                            to_user_id=robber.user_id,
                            amount=receiver_gets,
                            description=description,
                            timestamp=discord.utils.utcnow(),
                        ),
                        Transaction(
                            id=txid_fee,
                            from_user_id=target.user_id,
                            to_user_id=_treasury_hash(),
                            amount=adjusted_fee,
                            description=f"Drain fee (base: {base_fee_rate:.2%}, wealth-adjusted)",
                            timestamp=discord.utils.utcnow(),
                        ),
                    ]
                )

            await self.update_supply()

        return txid_main, drain_amount

    async def process_treasury_transaction(
        self,
        wallet_id: str,
        amount: Decimal,
        description: str,
        transaction_type: str = "standard",
        guild_id: int = None,
    ):
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        # Get wallet to determine user for wealth-adjusted fee
        async with self.async_sessionmaker() as session:
            wallet = await session.get(Wallet, wallet_id)
            if not wallet:
                raise ValueError("Wallet missing.")
            user_id = wallet.user_id
            raw_user_id = await self.resolve_user_hash(user_id)

        # Calculate base fee rate and apply wealth adjustment
        base_fee_rate = await self.get_enhanced_fee_rate(transaction_type)
        base_fee = AmountUtils.round_currency(abs(amount) * base_fee_rate)
        adjusted_fee = await self.calculate_wealth_adjusted_fee(raw_user_id, base_fee)

        amount = AmountUtils.round_currency(amount)
        if amount == 0:
            raise ValueError("Cannot process zero-amount transaction.")

        gross = abs(amount)
        fee = adjusted_fee
        net = gross - fee

        fee_rate = fee / gross if gross > 0 else Decimal("0.00")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError("Wallet missing.")
                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                if amount > 0:  # payout: treasury → wallet
                    # Enforce treasury floor: cap payout to protect reserves
                    supply = await session.get(Supply, 1)
                    if supply and supply.total_supply > 0:
                        treasury_floor = AmountUtils.round_currency(
                            supply.total_supply * TREASURY_FLOOR_RATIO
                        )
                        max_payout = supply.treasury - treasury_floor
                        if max_payout < gross:
                            if max_payout <= 0:
                                raise ValueError(
                                    "Treasury reserves are protected — payout unavailable."
                                )
                            # Cap the payout to stay above floor
                            gross = AmountUtils.round_currency(max_payout)
                            net = gross - fee
                            if net <= 0:
                                raise ValueError(
                                    "Treasury reserves are too low for this payout after fees."
                                )
                            logger.warning(
                                f"[TREASURY FLOOR] Payout capped from {abs(amount)} to {gross} "
                                f"(floor={treasury_floor}, treasury={supply.treasury})"
                            )

                    await self._atomic_balance_change(
                        session, "supply", "id", 1, -gross, balance_col="treasury"
                    )
                    await self._atomic_balance_change(
                        session,
                        "wallets",
                        "wallet_id",
                        wallet_id,
                        +net,
                        frozen_field="wallet_frozen",
                    )
                    await self._atomic_balance_change(
                        session, "supply", "id", 1, +fee, balance_col="treasury"
                    )
                    from_uid, to_uid = _treasury_hash(), wallet.user_id
                else:  # deposit: wallet → treasury
                    await self._atomic_balance_change(
                        session,
                        "wallets",
                        "wallet_id",
                        wallet_id,
                        -gross,
                        frozen_field="wallet_frozen",
                    )
                    await self._atomic_balance_change(
                        session, "supply", "id", 1, +net + fee, balance_col="treasury"
                    )
                    from_uid, to_uid = wallet.user_id, _treasury_hash()

                # DB tx rows
                tid_main = str(uuid.uuid4())
                tid_fee = str(uuid.uuid4())
                session.add_all(
                    [
                        Transaction(
                            id=tid_main,
                            from_user_id=from_uid,
                            to_user_id=to_uid,
                            amount=net,
                            description=description,
                            timestamp=discord.utils.utcnow(),
                        ),
                        Transaction(
                            id=tid_fee,
                            from_user_id=from_uid,
                            to_user_id=_treasury_hash(),  # Fee always goes to treasury
                            amount=fee,
                            description=f"{description} (fee @ {fee_rate:.2%})",
                            timestamp=discord.utils.utcnow(),
                        ),
                    ]
                )

            await self.update_supply()

        return tid_main

    async def refund_transaction(
        self, txid: str, reason: str, treasury_fallback: bool = True
    ):
        """
        Refund a transaction.

        Args:
            txid: The transaction ID to refund
            reason: The reason for the refund
            treasury_fallback: If True, treasury will cover if receiver has insufficient funds

        Returns:
            The refund transaction ID
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                tx = await session.get(Transaction, txid)
                if not tx:
                    raise ValueError("Transaction not found.")
                if tx.amount == 0:
                    raise ValueError("Cannot refund zero-amount transaction.")

                raw_from_id = await self.resolve_user_hash(tx.from_user_id)
                raw_to_id = await self.resolve_user_hash(tx.to_user_id)
                from_wallet_id = await self.get_wallet_id_for_user(raw_from_id)
                to_wallet_id = await self.get_wallet_id_for_user(raw_to_id)

                # Always give money back to the original sender
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    from_wallet_id,
                    +tx.amount,
                )

                # Try to take money from the receiver
                try:
                    await self._atomic_balance_change(
                        session,
                        "wallets",
                        "wallet_id",
                        to_wallet_id,
                        -tx.amount,
                    )
                except ValueError as e:
                    # Receiver has insufficient funds
                    if treasury_fallback:
                        # Treasury will cover the shortfall
                        supply = await session.get(Supply, 1)
                        if supply and supply.treasury >= tx.amount:
                            supply.treasury -= tx.amount
                            logger.warning(
                                f"Refund {txid}: Receiver had insufficient funds. "
                                f"Treasury covered {tx.amount}. Reason: {reason}"
                            )
                        else:
                            # Create money (mint) if treasury is also insufficient
                            logger.warning(
                                f"Refund {txid}: Treasury insufficient. Minting {tx.amount}. "
                                f"Reason: {reason}"
                            )
                    else:
                        raise ValueError(
                            f"Cannot refund: receiver has insufficient funds and treasury fallback is disabled."
                        ) from e

                # Record refund transaction
                refund_txid = str(uuid.uuid4())
                session.add(
                    Transaction(
                        id=refund_txid,
                        from_user_id=tx.to_user_id,
                        to_user_id=tx.from_user_id,
                        amount=-tx.amount,
                        description=f"Refund for {txid}: {reason}",
                        timestamp=discord.utils.utcnow(),
                    )
                )

            await self.update_supply()
        return refund_txid

    async def get_transaction_by_id(self, txid: str) -> Transaction:
        """
        Fetch a single transaction record by its Transaction ID (UUID).

        :param txid: The UUID string of the transaction you want to look up.
        :return: A single Transaction object, or None if not found.
        """
        async with self.async_sessionmaker() as session:
            try:
                result = await session.execute(
                    select(Transaction).where(Transaction.id == txid)
                )
                transaction = result.scalar_one_or_none()
                return transaction
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving transaction by id {txid}: {str(e)}")
                return None

    async def get_transactions_by_user_id(self, user_id: int, limit: int = 10) -> list:
        """
        Retrieve the latest transactions for a specific user.
        A transaction is included if the user is either the sender or the receiver.

        :param user_id: The Discord ID (int) of the user.
        :param limit: How many of the most recent transactions to retrieve (default=10).
        :return: A list of Transaction objects sorted by timestamp descending.
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            try:
                stmt = (
                    select(Transaction)
                    .where(
                        (Transaction.from_user_id == user_id)
                        | (Transaction.to_user_id == user_id)
                    )
                    .order_by(Transaction.timestamp.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                transactions = result.scalars().all()
                return transactions
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving user {user_id} transactions: {str(e)}")
                return []

    async def update_supply(self):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                supply = await session.get(Supply, 1)
                if not supply:
                    raise ValueError("Supply record missing.")

                # Ensure supply fields are initialized (handle NULL values)
                if supply.treasury is None:
                    supply.treasury = Decimal("0.00")
                if supply.circulating is None:
                    supply.circulating = Decimal("0.00")
                if supply.total_supply is None:
                    supply.total_supply = Decimal("0.00")

                wallet_total_result = await session.execute(
                    select(func.sum(Wallet.balance))
                )
                wallet_total = wallet_total_result.scalar() or Decimal("0.00")

                cryptocurrency_total_result = await session.execute(
                    select(func.sum(CryptoAsset.amount * CryptoPrice.price)).join(
                        CryptoPrice, CryptoAsset.symbol == CryptoPrice.symbol
                    )
                )
                cryptocurrency_total = cryptocurrency_total_result.scalar() or Decimal(
                    "0.00"
                )

                bank_total_result = await session.execute(
                    select(func.sum(Wallet.bank_balance))
                )
                bank_total = bank_total_result.scalar() or Decimal("0.00")

                circulating_supply = AmountUtils.round_currency(
                    wallet_total + bank_total + cryptocurrency_total
                )

                total_supply = AmountUtils.round_currency(
                    circulating_supply + supply.treasury
                )

                treasury_health = (
                    supply.treasury / total_supply
                    if total_supply > 0
                    else Decimal("0.00")
                )

                if treasury_health < Decimal("0.25"):
                    logger.warning(
                        "⚠️ Treasury health is low! Consider minting more currency."
                    )

                supply.circulating = circulating_supply
                supply.total_supply = total_supply

            await session.commit()

    async def get_top_balance_users(self, limit: int = 10) -> list[tuple[int, Decimal]]:
        """
        Retrieve the top users by total balance (wallet + bank + crypto holdings at current prices).

        Args:
            limit: Number of top users to retrieve (default=10)

        Returns:
            List of tuples containing (user_id, total_balance) sorted by total balance descending
        """
        async with self.async_sessionmaker() as session:
            try:
                # Subquery to get the latest price for each crypto symbol
                latest_prices = (
                    select(CryptoPrice.symbol, CryptoPrice.price)
                    .distinct(CryptoPrice.symbol)
                    .order_by(CryptoPrice.symbol, CryptoPrice.timestamp.desc())
                ).subquery()

                # Main query: wallet + bank + crypto holdings at current prices
                stmt = (
                    select(
                        Wallet.user_id,
                        (
                            Wallet.balance
                            + func.coalesce(Wallet.bank_balance, Decimal("0"))
                            + func.coalesce(
                                func.sum(CryptoAsset.amount * latest_prices.c.price),
                                Decimal("0"),
                            )
                        ).label("total_balance"),
                    )
                    .outerjoin(CryptoAsset, CryptoAsset.user_id == Wallet.user_id)
                    .outerjoin(
                        latest_prices, latest_prices.c.symbol == CryptoAsset.symbol
                    )
                    .group_by(Wallet.user_id, Wallet.balance, Wallet.bank_balance)
                    .order_by(
                        (
                            Wallet.balance
                            + func.coalesce(Wallet.bank_balance, Decimal("0"))
                            + func.coalesce(
                                func.sum(CryptoAsset.amount * latest_prices.c.price),
                                Decimal("0"),
                            )
                        ).desc()
                    )
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = result.all()
                mapping = await self.resolve_user_hashes([h for h, _ in rows])
                return [(mapping.get(h), bal) for h, bal in rows]
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving top balance users: {str(e)}")
                return []

    async def get_balance_user_rank(self, user_id: int) -> Optional[int]:
        """
        Retrieve the supplied users leaderboard position by total balance (wallet + bank combined).
        Args:
            user_id: The Discord ID (int) of the user.
        Returns:
            The rank of the user (1-based), or None if the user has no wallet.
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            try:
                subquery = (
                    select(
                        Wallet.user_id,
                        (Wallet.balance + func.coalesce(Wallet.bank_balance, 0)).label(
                            "total_balance"
                        ),
                    )
                    .select_from(Wallet)
                    .outerjoin(Wallet, Wallet.wallet_id == Wallet.wallet_id)
                    .subquery()
                )

                ranked_query = select(
                    subquery.c.user_id,
                    func.rank()
                    .over(order_by=subquery.c.total_balance.desc())
                    .label("rank"),
                ).subquery()

                stmt = select(ranked_query.c.rank).where(
                    ranked_query.c.user_id == user_id
                )
                result = await session.execute(stmt)
                rank = result.scalar_one_or_none()
                return rank
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving user {user_id} rank: {str(e)}")
                return None

    async def get_top_wallet_users(self, limit: int = 10) -> list[tuple[int, Decimal]]:
        """
        Retrieve the top users by wallet balance.

        Args:
            limit: Number of top users to retrieve (default=10)

        Returns:
            List of tuples containing (user_id, wallet_balance) sorted by wallet balance descending
        """
        async with self.async_sessionmaker() as session:
            try:
                stmt = (
                    select(Wallet.user_id, Wallet.balance)
                    .order_by(Wallet.balance.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = result.all()
                mapping = await self.resolve_user_hashes([h for h, _ in rows])
                return [(mapping.get(h), bal) for h, bal in rows]
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving top wallet users: {str(e)}")
                return []

    async def get_top_bank_users(self, limit: int = 10) -> list[tuple[int, Decimal]]:
        """
        Retrieve the top users by bank balance.

        Args:
            limit: Number of top users to retrieve (default=10)

        Returns:
            List of tuples containing (user_id, bank_balance) sorted by bank balance descending
        """
        async with self.async_sessionmaker() as session:
            try:
                stmt = (
                    select(Wallet.user_id, Wallet.bank_balance)
                    .order_by(Wallet.bank_balance.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = result.all()
                mapping = await self.resolve_user_hashes([h for h, _ in rows])
                return [(mapping.get(h), bal) for h, bal in rows]
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving top bank users: {str(e)}")
                return []

    async def mint_currency(self, amount: Decimal, description: str):
        """
        Mint new currency and add it to the treasury balance.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = AmountUtils.round_currency(amount)
                supply = await session.get(Supply, 1)
                if not supply:
                    raise ValueError("Supply record missing!")

                supply.treasury += amount

                txid = str(uuid.uuid4())
                transaction_db = Transaction(
                    id=txid,
                    from_user_id=None,
                    to_user_id=_treasury_hash(),
                    amount=amount,
                    description=description,
                    timestamp=discord.utils.utcnow(),
                )
                session.add(transaction_db)

            await self.update_supply()

    async def burn_currency(self, amount: Decimal, description: str):
        """
        Burns currency by removing it from treasury, effectively reducing total supply.
        Enforces a treasury floor: will not burn below TREASURY_FLOOR_RATIO of total_supply.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = AmountUtils.round_currency(amount)
                supply = await session.get(Supply, 1)
                if not supply:
                    raise ValueError("Supply record missing!")

                if supply.treasury < amount:
                    raise ValueError("Not enough treasury balance to burn.")

                # Enforce treasury floor
                total_after_burn = supply.circulating + (supply.treasury - amount)
                floor = AmountUtils.round_currency(
                    total_after_burn * TREASURY_FLOOR_RATIO
                )
                if (supply.treasury - amount) < floor:
                    # Cap the burn to stay above floor
                    max_burnable = supply.treasury - floor
                    if max_burnable <= 0:
                        raise ValueError("Cannot burn: treasury is at or below floor.")
                    amount = AmountUtils.round_currency(max_burnable)

                supply.treasury -= amount

                txid = str(uuid.uuid4())
                transaction_db = Transaction(
                    id=txid,
                    from_user_id=_treasury_hash(),
                    to_user_id=None,
                    amount=amount,
                    description=description,
                    timestamp=discord.utils.utcnow(),
                )
                session.add(transaction_db)

            await self.update_supply()

    async def validate_economy(self):
        """
        Cross-check that:
        - sum of all wallets & banks & crypto == supply.circulating
        - supply.circulating + supply.treasury == supply.total_supply
        Returns True if all checks out, False otherwise.
        """
        # Use a small tolerance for Decimal comparisons; strict equality is
        # too sensitive to minute rounding differences and concurrent transactions.
        TOLERANCE = Decimal("0.01")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Refresh supply from within the same transaction as the sums
                # so we get a consistent snapshot.
                supply = await session.get(Supply, 1)
                if not supply:
                    return False

                if supply.treasury is None:
                    supply.treasury = Decimal("0.00")
                if supply.circulating is None:
                    supply.circulating = Decimal("0.00")
                if supply.total_supply is None:
                    supply.total_supply = Decimal("0.00")

                result_wallet = await session.execute(select(func.sum(Wallet.balance)))
                total_wallet = result_wallet.scalar() or Decimal("0.00")

                result_bank = await session.execute(
                    select(func.sum(Wallet.bank_balance))
                )
                total_bank = result_bank.scalar() or Decimal("0.00")

                cryptocurrency_total_result = await session.execute(
                    select(func.sum(CryptoAsset.amount * CryptoPrice.price)).join(
                        CryptoPrice, CryptoAsset.symbol == CryptoPrice.symbol
                    )
                )
                cryptocurrency_total = cryptocurrency_total_result.scalar() or Decimal(
                    "0.00"
                )

                actual_circulating = AmountUtils.round_currency(
                    total_wallet + total_bank + cryptocurrency_total
                )
                expected_circulating = supply.circulating
                circulating_diff = (
                    actual_circulating - expected_circulating
                ).copy_abs()

                actual_total = AmountUtils.round_currency(
                    actual_circulating + supply.treasury
                )
                expected_total = supply.total_supply
                total_diff = (actual_total - expected_total).copy_abs()

                ok = True
                if circulating_diff > TOLERANCE:
                    logging.error(
                        "Circulating mismatch: actual=%s, stored=%s, diff=%s",
                        actual_circulating,
                        expected_circulating,
                        circulating_diff,
                    )
                    ok = False

                if total_diff > TOLERANCE:
                    logging.error(
                        "Total supply mismatch: actual=%s, stored=%s, diff=%s",
                        actual_total,
                        expected_total,
                        total_diff,
                    )
                    ok = False

                # Refresh stored supply values if validation passed and the
                # snapshot is still consistent. This prevents the background
                # task from constantly reporting stale supply values.
                if ok and actual_circulating != expected_circulating:
                    supply.circulating = actual_circulating
                    supply.total_supply = actual_total

        return ok

    def _calculate_dynamic_target(self, supply) -> tuple[Decimal, Decimal, Decimal]:
        """
        Calculate the dynamic treasury target and thresholds based on economy maturity.

        In a young economy (low circulation), the treasury is expected to hold most of the supply.
        As users accumulate currency and circulating ratio rises, the target tightens toward 50%.

        Returns:
            (target, min_hw, max_hw) — dynamic treasury health target and thresholds
        """
        total_supply = supply.total_supply
        circulating = supply.circulating

        if total_supply <= 0:
            return Decimal("0.50"), Decimal("0.20"), Decimal("0.95")

        circulation_ratio = (circulating / total_supply).quantize(Decimal("0.0001"))

        # Young economy (<20% circulating) → target ~0.75
        # Mature economy (>50% circulating) → target 0.50
        target = max(
            Decimal("0.50"), Decimal("0.75") - (circulation_ratio * Decimal("0.50"))
        )
        target = target.quantize(Decimal("0.0001"))

        # Thresholds are ±15 points from target, clamped to safe range
        max_hw = min(Decimal("0.95"), target + Decimal("0.15"))
        min_hw = max(Decimal("0.20"), target - Decimal("0.15"))

        return target, min_hw, max_hw

    async def perform_economic_rebalance(self) -> dict:
        """
        Perform a single economic rebalance cycle with dynamic maturity-aware targets.

        Uses a quadratic intensity curve: gentle corrections near the target,
        aggressive corrections at extremes. Includes:
        - Dynamic target based on circulation ratio (economy maturity)
        - Treasury floor protection (never burn below 10% of total_supply)
        - Conservative auto-mint (40% of burn rate, 0.5%/day cap)
        - Dead zone (±3% of target) to avoid micro-churn
        - Emergency mode (±25%) doubles the adjustment cap

        Returns a dict describing what action was taken (or why none was taken).
        """
        global _LAST_REBALANCE_AT, _DAILY_MINT_TOTAL, _MINT_DAY, _DAILY_BURN_TOTAL, _BURN_DAY

        STEP = Decimal("0.05")
        COOLDOWN = timedelta(hours=1)
        DEAD_ZONE = Decimal("0.03")
        EMERGENCY_DISTANCE = Decimal("0.25")
        MINT_DAMPENER = Decimal("0.4")  # Mint at 40% of burn rate
        DAILY_MINT_CAP_RATIO = Decimal("0.005")  # 0.5% of total_supply per day

        now = discord.utils.utcnow()
        today = now.date()

        # Reset daily counters if day changed
        if _MINT_DAY is None or _MINT_DAY != today:
            _DAILY_MINT_TOTAL = Decimal("0")
            _MINT_DAY = today
        if _BURN_DAY is None or _BURN_DAY != today:
            _DAILY_BURN_TOTAL = Decimal("0")
            _BURN_DAY = today

        # Enforce cooldown
        if _LAST_REBALANCE_AT is not None and now - _LAST_REBALANCE_AT < COOLDOWN:
            remaining = COOLDOWN - (now - _LAST_REBALANCE_AT)
            return {
                "action": "skipped",
                "reason": f"Cooldown active ({remaining.seconds}s remaining)",
            }

        supply = await self.get_supply_record()
        treasury, total_supply = supply.treasury, supply.total_supply

        if total_supply <= 0:
            return {"action": "skipped", "reason": "No supply exists"}

        treasury_health = (treasury / total_supply).quantize(Decimal("0.0001"))
        target, min_hw, max_hw = self._calculate_dynamic_target(supply)
        treasury_floor = AmountUtils.round_currency(total_supply * TREASURY_FLOOR_RATIO)
        base_cap = AmountUtils.round_currency(total_supply * Decimal("0.02"))

        distance = abs(treasury_health - target)

        # Dead zone: skip if within ±3% of target
        if distance < DEAD_ZONE:
            return {
                "action": "skipped",
                "reason": f"Within dead zone (health={treasury_health:.2%}, target={target:.2%}, distance={distance:.2%})",
                "treasury_health": treasury_health,
                "target": target,
            }

        # Quadratic intensity: gentle near target, aggressive at extremes
        intensity = min(Decimal("1"), (distance / Decimal("0.15")) ** 2)

        # Emergency mode: double cap at extreme distances
        cap = base_cap * 2 if distance > EMERGENCY_DISTANCE else base_cap

        gap = (target * total_supply) - treasury
        adj = AmountUtils.round_currency(intensity * STEP * abs(gap))
        adj = min(adj, cap)

        if adj <= 0:
            return {"action": "skipped", "reason": "Calculated adjustment is zero"}

        action_taken = "none"
        amount_adjusted = Decimal("0")
        health_before = treasury_health

        try:
            if gap < 0:
                # Treasury too high → burn
                # Enforce treasury floor: don't burn below floor
                max_burnable = treasury - treasury_floor
                if max_burnable <= 0:
                    return {
                        "action": "skipped",
                        "reason": f"Treasury at floor (treasury={treasury}, floor={treasury_floor})",
                        "treasury_health": treasury_health,
                        "target": target,
                    }
                adj = min(adj, max_burnable)
                await self.burn_currency(
                    adj,
                    f"Auto-burn {adj} (health {treasury_health:.2%}, target {target:.2%})",
                )
                logger.info(
                    f"[AUTO-REBALANCE] Burned {adj} (health {treasury_health:.2%} → target {target:.2%})"
                )
                _DAILY_BURN_TOTAL += adj
                action_taken = "burn"
                amount_adjusted = adj

            elif gap > 0:
                # Treasury too low → mint (conservative)
                # Apply dampener: mint at 40% of what burn would do
                mint_adj = AmountUtils.round_currency(adj * MINT_DAMPENER)

                # Enforce daily mint cap
                daily_mint_cap = AmountUtils.round_currency(
                    total_supply * DAILY_MINT_CAP_RATIO
                )
                remaining_daily = daily_mint_cap - _DAILY_MINT_TOTAL
                if remaining_daily <= 0:
                    return {
                        "action": "skipped",
                        "reason": f"Daily mint cap reached ({_DAILY_MINT_TOTAL} / {daily_mint_cap})",
                        "treasury_health": treasury_health,
                        "target": target,
                    }
                mint_adj = min(mint_adj, remaining_daily)

                if mint_adj <= 0:
                    return {
                        "action": "skipped",
                        "reason": "Mint adjustment too small after dampening",
                    }

                # Safety: don't mint if circulation is already >80% (too much in player hands)
                circulation_ratio = (
                    supply.circulating / total_supply
                    if total_supply > 0
                    else Decimal("0")
                )
                if circulation_ratio > Decimal("0.80"):
                    return {
                        "action": "skipped",
                        "reason": f"Circulation too high for minting ({circulation_ratio:.2%})",
                        "treasury_health": treasury_health,
                        "target": target,
                    }

                await self.mint_currency(
                    mint_adj,
                    f"Auto-mint {mint_adj} (health {treasury_health:.2%}, target {target:.2%})",
                )
                logger.info(
                    f"[AUTO-REBALANCE] Minted {mint_adj} (health {treasury_health:.2%} → target {target:.2%})"
                )
                _DAILY_MINT_TOTAL += mint_adj
                action_taken = "mint"
                amount_adjusted = mint_adj

            _LAST_REBALANCE_AT = now

            # Recalculate health after action
            supply = await self.get_supply_record()
            health_after = (
                (supply.treasury / supply.total_supply).quantize(Decimal("0.0001"))
                if supply.total_supply > 0
                else Decimal("0")
            )

            return {
                "action": action_taken,
                "amount": amount_adjusted,
                "health_before": health_before,
                "health_after": health_after,
                "target": target,
                "min_hw": min_hw,
                "max_hw": max_hw,
                "daily_minted": _DAILY_MINT_TOTAL,
                "daily_burned": _DAILY_BURN_TOTAL,
            }

        except Exception as e:
            logger.error(f"[AUTO-REBALANCE ERROR]: {e}")
            return {"action": "error", "reason": str(e)}

    async def get_economic_factors(self) -> dict:
        """
        Pure-read function: calculates and returns economic metrics and fee rates.
        Does NOT perform any rebalancing or mutations. Use perform_economic_rebalance() for that.
        """
        supply = await self.get_supply_record()
        treasury, total_supply = supply.treasury, supply.total_supply

        if total_supply <= 0:
            return {
                "treasury_health": Decimal("0"),
                "risk_scalar": Decimal("0"),
                "fee_rate": Decimal("0"),
                "passive_income_rate": Decimal("0"),
                "treasury_balance": Decimal("0"),
                "total_supply": Decimal("0"),
                "velocity_of_money": Decimal("0"),
                "liquidity_ratio": Decimal("0"),
                "volatility_index": Decimal("0"),
                "target_ratio": Decimal("0.50"),
                "min_health_threshold": Decimal("0.20"),
                "max_health_threshold": Decimal("0.95"),
            }

        treasury_health = (treasury / total_supply).quantize(Decimal("0.0001"))

        # Load cached/latest metrics
        metrics = getattr(self, "_latest_metrics", {})

        # Calculate liquidity ratio
        liquidity_ratio = (
            (supply.circulating / total_supply).quantize(Decimal("0.0001"))
            if total_supply > 0
            else Decimal("0")
        )

        # Wealth-inequality Gini is not a useful signal for this economy because
        # a small number of active players hold multi-hundred-trillion balances
        # while most users hold little or nothing. It stays pinned near 1.0 and
        # skews fees/health scores without indicating real instability. Keep the
        # value at 0 so existing consumers and the DB column stay compatible.
        volatility_index = Decimal("0")

        # Calculate velocity of money with protection against division by zero
        async with self.async_sessionmaker() as session:
            # Transaction volume (last 24 hours)
            yesterday = discord.utils.utcnow() - timedelta(days=1)
            volume_stmt = select(func.sum(Transaction.amount)).where(
                Transaction.timestamp >= yesterday
            )
            volume_result = await session.execute(volume_stmt)
            transaction_volume = volume_result.scalar() or Decimal("0.00")

            if supply.circulating > 0:
                velocity_of_money = (
                    transaction_volume / supply.circulating
                ).quantize(Decimal("0.0001"))
            else:
                velocity_of_money = Decimal("0")

        # Dynamic target based on economy maturity
        TARGET, MIN_HW, MAX_HW = self._calculate_dynamic_target(supply)

        BASE_FEE = Decimal("0.01")
        BASE_PASS = Decimal("0.005")
        MAX_FEE_RATE = Decimal("0.10")
        MIN_FEE_RATE = Decimal("0.001")

        if treasury_health < TARGET:
            d = TARGET - treasury_health
            fee_base = (BASE_FEE * (1 + d**2)).quantize(Decimal("0.0001"))
            passive = (BASE_PASS * (1 - d)).quantize(Decimal("0.0001"))
            risk = (treasury_health / TARGET).quantize(Decimal("0.0001"))
        else:
            fee_base = BASE_FEE
            passive = BASE_PASS
            risk = (Decimal("1.0") + (treasury_health - TARGET)).quantize(
                Decimal("0.0001")
            )

        # Adjust fee based on volatility
        fee = fee_base * (1 + volatility_index * Decimal("0.5"))
        fee = max(MIN_FEE_RATE, min(MAX_FEE_RATE, fee))

        return {
            "treasury_health": treasury_health,
            "risk_scalar": risk,
            "fee_rate": fee,
            "passive_income_rate": passive,
            "treasury_balance": treasury,
            "total_supply": total_supply,
            "target_ratio": TARGET,
            "min_health_threshold": MIN_HW,
            "max_health_threshold": MAX_HW,
            "velocity_of_money": velocity_of_money,
            "liquidity_ratio": liquidity_ratio,
            "volatility_index": volatility_index,
        }

    async def _calculate_wealth_gini(self) -> Decimal:
        """
        Compute the Gini coefficient across all user wealth.

        Wealth includes wallet balance, bank balance, and crypto holdings at the
        latest known prices. The treasury wallet is excluded. Returns a value in
        [0, 1] where 0 is perfect equality and 1 is maximum inequality.
        """
        async with self.async_sessionmaker() as session:
            latest_prices = (
                select(CryptoPrice.symbol, CryptoPrice.price)
                .distinct(CryptoPrice.symbol)
                .order_by(CryptoPrice.symbol, CryptoPrice.timestamp.desc())
            ).subquery()

            wealth_stmt = (
                select(
                    (
                        Wallet.balance
                        + func.coalesce(Wallet.bank_balance, Decimal("0"))
                        + func.coalesce(
                            func.sum(CryptoAsset.amount * latest_prices.c.price),
                            Decimal("0"),
                        )
                    ).label("wealth")
                )
                .outerjoin(CryptoAsset, CryptoAsset.user_id == Wallet.user_id)
                .outerjoin(
                    latest_prices, latest_prices.c.symbol == CryptoAsset.symbol
                )
                .where(Wallet.user_id != _treasury_hash())
                .group_by(Wallet.user_id, Wallet.balance, Wallet.bank_balance)
            )

            result = await session.execute(wealth_stmt)
            balances = [float(r[0]) for r in result.fetchall()]

        if len(balances) <= 1:
            return Decimal("0.00")

        sorted_balances = sorted(balances)
        n = len(sorted_balances)
        total = sum(sorted_balances)
        if total <= 0:
            return Decimal("0.00")

        # Gini = sum((2i - n - 1) * x_i) / (n^2 * mean)
        cumsum = sum(
            (2 * (i + 1) - n - 1) * x for i, x in enumerate(sorted_balances)
        )
        gini = cumsum / (n * n * (total / n))
        gini = max(0.0, min(1.0, gini))
        return Decimal(str(gini)).quantize(Decimal("0.0001"))

    async def get_user_net_worth(self, user_id: int) -> Decimal:
        """
        Calculate a user's net worth for limit and tier calculations.

        Net worth is defined as: wallet balance + bank balance + crypto holdings
        valued at the latest known prices. Shop items, inventory, and other
        virtual goods are explicitly excluded from this value.

        Args:
            user_id: The user's Discord ID (int)

        Returns:
            Decimal: Total net worth (always non-negative)
        """
        raw_user_id = user_id
        user_id = self.hash_user_id(raw_user_id)

        async with self.async_sessionmaker() as session:
            # Wallet + bank balances
            result = await session.execute(
                select(Wallet.balance, Wallet.bank_balance).where(
                    Wallet.user_id == user_id
                )
            )
            row = result.one_or_none()
            wallet_bal = row[0] if row else Decimal("0.00")
            bank_bal = row[1] if row else Decimal("0.00")

            net_worth = wallet_bal + bank_bal

            # Crypto holdings at latest prices
            latest_prices = (
                select(CryptoPrice.symbol, CryptoPrice.price)
                .distinct(CryptoPrice.symbol)
                .order_by(CryptoPrice.symbol, CryptoPrice.timestamp.desc())
            ).subquery()

            crypto_value_result = await session.execute(
                select(
                    func.coalesce(
                        func.sum(CryptoAsset.amount * latest_prices.c.price),
                        Decimal("0.00"),
                    )
                )
                .select_from(CryptoAsset)
                .outerjoin(
                    latest_prices, latest_prices.c.symbol == CryptoAsset.symbol
                )
                .where(CryptoAsset.user_id == user_id)
            )
            crypto_value = crypto_value_result.scalar() or Decimal("0.00")

            return AmountUtils.round_currency(net_worth + crypto_value)

    async def get_user_wealth_tier(self, user_id: int) -> int:
        """
        Calculate the user's wealth tier based on their percentage of total supply.

        Wealth is calculated on-demand as: wallet + bank + crypto at current prices.

        Returns:
            int: Tier 0-4 (0 = no penalty, 1-4 = escalating penalties)
        """
        user_wealth = await self.get_user_net_worth(user_id)

        # Get total supply from economy snapshot
        snapshot = await self.get_economy_snapshot()
        total_supply = snapshot["total_supply"]

        if total_supply <= 0:
            return 0

        # Calculate user's percentage of total supply
        user_ratio = user_wealth / total_supply

        # Determine tier (check from highest to lowest)
        if user_ratio >= WEALTH_TIERS["tier_4"]["threshold"]:
            return 4
        elif user_ratio >= WEALTH_TIERS["tier_3"]["threshold"]:
            return 3
        elif user_ratio >= WEALTH_TIERS["tier_2"]["threshold"]:
            return 2
        elif user_ratio >= WEALTH_TIERS["tier_1"]["threshold"]:
            return 1
        else:
            return 0

    def get_wealth_penalty_multipliers(self, tier: int) -> dict:
        """
        Get penalty multipliers for a given wealth tier.

        Args:
            tier: Wealth tier (0-4)

        Returns:
            dict: Multipliers for 'bet', 'loan', 'fee', 'transfer'
        """
        return TIER_PENALTIES.get(tier, TIER_PENALTIES[0])

    async def get_wealth_tier_info(self, user_id: int) -> dict:
        """
        Get comprehensive wealth tier information for a user.

        Args:
            user_id: The user's ID

        Returns:
            dict: Contains tier, wealth, percentage, thresholds, and multipliers
        """
        raw_user_id = user_id
        user_wealth = await self.get_user_net_worth(raw_user_id)

        # Get total supply from economy snapshot
        snapshot = await self.get_economy_snapshot()
        total_supply = snapshot["total_supply"]

        if total_supply <= 0:
            return {
                "tier": 0,
                "wealth": user_wealth,
                "percentage": Decimal("0"),
                "next_tier_threshold": WEALTH_TIERS["tier_1"]["threshold"],
                "multipliers": TIER_PENALTIES[0],
            }

        # Calculate user's percentage of total supply
        user_ratio = user_wealth / total_supply

        # Determine tier
        tier = await self.get_user_wealth_tier(raw_user_id)

        # Determine next tier threshold
        next_tier_threshold = None
        if tier == 0:
            next_tier_threshold = WEALTH_TIERS["tier_1"]["threshold"]
        elif tier == 1:
            next_tier_threshold = WEALTH_TIERS["tier_2"]["threshold"]
        elif tier == 2:
            next_tier_threshold = WEALTH_TIERS["tier_3"]["threshold"]
        elif tier == 3:
            next_tier_threshold = WEALTH_TIERS["tier_4"]["threshold"]
        # tier 4 has no next tier

        return {
            "tier": tier,
            "wealth": user_wealth,
            "percentage": user_ratio,
            "next_tier_threshold": next_tier_threshold,
            "multipliers": self.get_wealth_penalty_multipliers(tier),
        }

    async def calculate_wealth_adjusted_fee(
        self, user_id: int, base_fee: Decimal
    ) -> Decimal:
        """
        Calculate a wealth-adjusted fee for high-wealth users.

        Args:
            user_id: The user's ID
            base_fee: The base fee amount

        Returns:
            Decimal: Adjusted fee based on user's wealth tier
        """
        raw_user_id = user_id
        user_id = self.hash_user_id(raw_user_id)
        user_tier = await self.get_user_wealth_tier(raw_user_id)
        if user_tier == 0:
            return base_fee

        multipliers = self.get_wealth_penalty_multipliers(user_tier)
        return AmountUtils.round_currency(base_fee * multipliers["fee"])

    async def get_max_gamble_amount(
        self,
        user_id: int,
        raise_if_limited: bool = False,
        max_payout_multiplier: Decimal = Decimal("1.0"),
    ) -> Decimal:
        """
        Calculates the maximum amount a user can gamble based on dynamic risk controls.

        Risk model includes:
        - Dynamic base bet size scaled to treasury health.
        - Absolute cap on treasury exposure.
        - Whale mitigation (players exceeding 1% of total supply).
        - Minimum floor for newcomers.
        - Adaptive adjustments based on:
            - Market volatility
            - Liquidity levels
            - Recent transaction volume
            - Active user engagement
        - Max payout multiplier adjustment for high-payout games.

        Args:
            user_id: The user's ID
            raise_if_limited: If True, raises ValueError when user exceeds limit
            max_payout_multiplier: Maximum payout multiplier for the game (e.g., 50.0 for crash)
        """
        raw_user_id = user_id

        # ---- constants -------------------------------------------------------
        MAX_TREASURY_EXPOSURE = Decimal("0.02")  # 2% of treasury
        MIN_ABSOLUTE_FLOOR = Decimal("100.00")  # Floor value for small players

        # ---- fetch user data -------------------------------------------------
        # Net worth = wallet + bank + crypto at current prices.
        # Shop items and other inventory are intentionally excluded.
        user_total = await self.get_user_net_worth(raw_user_id)

        # ---- economy snapshot -----------------------------------------------
        supply = await self.get_supply_record()
        treasury = supply.treasury
        total_supply = supply.total_supply

        if treasury <= 0 or total_supply <= 0:
            return Decimal("0.00")  # Economy not initialized or broken

        # ---- dynamic economic factors ----------------------------------------
        factors = await self.get_economic_factors()
        health = factors["treasury_health"]
        liquidity_ratio = factors.get("liquidity_ratio", Decimal("0.50"))
        transaction_volume = factors.get("transaction_volume", Decimal("0.00"))
        active_users = factors.get("active_users", 1)

        # ---- dynamic base coefficient ---------------------------------------
        # Wealth-inequality "volatility" is no longer used; bet limits scale
        # directly off treasury health.
        if health >= Decimal("0.60"):
            base_coeff = Decimal("0.01")  # 1%
        elif health >= Decimal("0.30"):
            t = (health - Decimal("0.30")) / Decimal("0.30")
            base_coeff = Decimal("0.0025") + (Decimal("0.01") - Decimal("0.0025")) * (
                t**2
            )
        else:
            base_coeff = Decimal("0.00125")  # 0.125%

        # ---- apply wealth tier penalty --------------------------------------
        user_tier = await self.get_user_wealth_tier(raw_user_id)
        if user_tier > 0:
            multipliers = self.get_wealth_penalty_multipliers(user_tier)
            base_coeff *= multipliers["bet"]  # Apply tier-based bet multiplier

        # ---- adjust for liquidity scarcity ----------------------------------
        LIQUIDITY_ADJUSTMENT_FACTOR = Decimal("0.8")
        if liquidity_ratio < Decimal("0.3"):  # Less than 30% circulating
            base_coeff *= LIQUIDITY_ADJUSTMENT_FACTOR  # Tighten betting limits

        # ---- adjust for surge in activity -----------------------------------
        AVG_VOLUME_EXPECTED = Decimal("50000")  # Expected daily volume baseline
        if transaction_volume > AVG_VOLUME_EXPECTED * Decimal("1.5"):
            base_coeff *= Decimal("0.9")  # Lower bet size during high traffic

        # ---- adjust for low engagement --------------------------------------
        MIN_ACTIVE_USERS = 10
        if active_users < MIN_ACTIVE_USERS:
            base_coeff *= Decimal("0.9")  # Discourage gambling during low participation

        # ---- calculate tentative limit --------------------------------------
        by_treasury = AmountUtils.round_currency(treasury * base_coeff)
        hard_cap = AmountUtils.round_currency(treasury * MAX_TREASURY_EXPOSURE)
        # Adjust hard cap by max payout multiplier to limit treasury exposure
        # For games with 50x max payout, this ensures max_bet * 50 <= hard_cap
        adjusted_hard_cap = (
            hard_cap / max_payout_multiplier
            if max_payout_multiplier > Decimal("1.0")
            else hard_cap
        )
        provisional = min(by_treasury, adjusted_hard_cap, user_total)

        # ---- enforce adaptive minimum floor ---------------------------------
        adaptive_floor = min(
            MIN_ABSOLUTE_FLOOR,
            AmountUtils.round_currency(user_total * Decimal("0.02")),
        )
        final_limit = max(provisional, adaptive_floor)

        # ---- optional enforcement -------------------------------------------
        if raise_if_limited and user_total > final_limit:
            raise ValueError(
                f"You’re limited to **{final_limit} {self.currency_name}** "
                f"this hand by risk management."
            )

        return final_limit

    async def get_max_loan_amount(self, user_id: int) -> Decimal:
        """
        Calculate the maximum safe loan amount a user can take based on their balance and economic factors.
        Uses a similar risk model to gambling limits but with more leniency.
        """
        raw_user_id = user_id
        # Net worth = wallet + bank + crypto at current prices.
        # Shop items and other inventory are intentionally excluded.
        user_total = await self.get_user_net_worth(raw_user_id)

        supply = await self.get_supply_record()
        treasury = supply.treasury
        total_supply = supply.total_supply

        if treasury <= 0 or total_supply <= 0:
            return Decimal("0.00")

        factors = await self.get_economic_factors()
        health = factors["treasury_health"]

        if health < Decimal("0.25"):
            base_coeff = Decimal("0.0005")  # 0.05% of treasury
        elif health < Decimal("0.50"):
            base_coeff = Decimal("0.001")  # 0.1% of treasury
        else:
            base_coeff = Decimal("0.002")  # 0.2% of treasury

        # ---- apply wealth tier penalty --------------------------------------
        user_tier = await self.get_user_wealth_tier(raw_user_id)
        if user_tier > 0:
            multipliers = self.get_wealth_penalty_multipliers(user_tier)
            base_coeff *= multipliers["loan"]  # Apply tier-based loan multiplier

        max_loan = AmountUtils.round_currency(treasury * base_coeff)
        return min(
            max_loan, user_total * Decimal("1.5")
        )  # Cap at 1.5x user's total balance

    async def get_max_transfer_amount(self, user_id: int) -> Decimal:
        """
        Calculate the maximum amount a user can transfer in a single transaction.
        Uses wealth tier penalties to limit high-wealth users' transfer capabilities.

        Args:
            user_id: The user's ID

        Returns:
            Decimal: Maximum transfer amount
        """
        raw_user_id = user_id
        # Net worth = wallet + bank + crypto at current prices.
        # Shop items and other inventory are intentionally excluded.
        user_total = await self.get_user_net_worth(raw_user_id)

        supply = await self.get_supply_record()
        treasury = supply.treasury
        total_supply = supply.total_supply

        if treasury <= 0 or total_supply <= 0:
            return Decimal("0.00")

        factors = await self.get_economic_factors()
        health = factors["treasury_health"]

        # Base transfer coefficient based on treasury health
        if health < Decimal("0.25"):
            base_coeff = Decimal("0.05")  # 5% of user's wealth
        elif health < Decimal("0.50"):
            base_coeff = Decimal("0.10")  # 10% of user's wealth
        elif health < Decimal("0.75"):
            base_coeff = Decimal("0.15")  # 15% of user's wealth
        else:
            base_coeff = Decimal("0.20")  # 20% of user's wealth

        # Apply wealth tier penalty
        user_tier = await self.get_user_wealth_tier(raw_user_id)
        if user_tier > 0:
            multipliers = self.get_wealth_penalty_multipliers(user_tier)
            base_coeff *= multipliers[
                "transfer"
            ]  # Apply tier-based transfer multiplier

        # Calculate max transfer as percentage of user's wealth
        max_transfer = AmountUtils.round_currency(user_total * base_coeff)

        # Absolute cap based on treasury (max 5% of treasury in single transfer)
        treasury_cap = AmountUtils.round_currency(treasury * Decimal("0.05"))

        return min(max_transfer, treasury_cap)

    async def collect_daily_economy_snapshot(self):
        """
        Collects a snapshot of key economy metrics using existing tables.
        Can be used for logging, alerting, or feeding into predictive models.
        """
        today = discord.utils.utcnow().date()
        yesterday_start = today - timedelta(days=1)
        yesterday_end = today

        async with self.async_sessionmaker() as session:
            # Supply info
            supply = await session.get(Supply, 1)
            treasury_balance = supply.treasury
            total_supply = supply.total_supply
            circulating_supply = supply.circulating

            # Wallet summary
            wallet_sum_result = await session.execute(select(func.sum(Wallet.balance)))
            wallet_total = wallet_sum_result.scalar() or Decimal("0.00")

            bank_sum_result = await session.execute(
                select(func.sum(Wallet.bank_balance))
            )
            bank_total = bank_sum_result.scalar() or Decimal("0.00")

            avg_wallet_balance = Decimal("0.00")
            wallet_count_result = await session.execute(
                select(func.count(Wallet.wallet_id))
            )
            wallet_count = wallet_count_result.scalar()
            if wallet_count and wallet_count > 0:
                avg_wallet_balance = AmountUtils.round_currency(
                    wallet_total / wallet_count
                )

            # Transaction volume (yesterday)
            volume_stmt = select(func.sum(Transaction.amount)).where(
                Transaction.timestamp >= yesterday_start,
                Transaction.timestamp < yesterday_end,
            )
            volume_result = await session.execute(volume_stmt)
            transaction_volume = volume_result.scalar() or Decimal("0.00")

            # Active users (distinct senders/receivers yesterday)
            active_users_stmt = select(
                func.count(distinct(Transaction.from_user_id)).label("senders"),
                func.count(distinct(Transaction.to_user_id)).label("receivers"),
            ).where(
                Transaction.timestamp >= yesterday_start,
                Transaction.timestamp < yesterday_end,
            )
            active_users_result = await session.execute(active_users_stmt)
            row = active_users_result.fetchone()
            active_users = (row.senders or 0) + (row.receivers or 0)

            # Wealth-inequality Gini is kept at 0 for this economy; see
            # get_economic_factors() for the rationale.
            volatility_index = Decimal("0")

            # Calculate additional economic metrics
            liquidity_ratio = (
                (circulating_supply / total_supply).quantize(Decimal("0.0001"))
                if total_supply > 0
                else Decimal("0")
            )

            # Calculate velocity of money with protection against division by zero
            if circulating_supply > 0:
                velocity_of_money = (transaction_volume / circulating_supply).quantize(
                    Decimal("0.0001")
                )
            else:
                velocity_of_money = Decimal("0")

            # Get current economic factors for additional metrics
            economic_factors = await self.get_economic_factors()

            # Store in historical table
            historical_record = EconomicMetricsHistory(
                date=today,
                total_supply=total_supply,
                circulating_supply=circulating_supply,
                treasury_balance=treasury_balance,
                treasury_health=economic_factors.get("treasury_health", Decimal("0")),
                liquidity_ratio=liquidity_ratio,
                velocity_of_money=velocity_of_money,
                volatility_index=volatility_index,
                transaction_volume=transaction_volume,
                active_users=active_users,
                avg_wallet_balance=avg_wallet_balance,
                fee_rate=economic_factors.get("fee_rate", Decimal("0")),
                passive_income_rate=economic_factors.get(
                    "passive_income_rate", Decimal("0")
                ),
                auto_minted_today=_DAILY_MINT_TOTAL,
                auto_burned_today=_DAILY_BURN_TOTAL,
                rebalance_target=economic_factors.get("target_ratio", Decimal("0.50")),
            )

            # Check if record already exists for today
            existing_stmt = select(EconomicMetricsHistory).where(
                EconomicMetricsHistory.date == today
            )
            existing_result = await session.execute(existing_stmt)
            existing_record = existing_result.scalar_one_or_none()

            if existing_record:
                # Update existing record
                for key, value in historical_record.__dict__.items():
                    if not key.startswith("_") and key != "id":
                        setattr(existing_record, key, value)
            else:
                # Insert new record
                session.add(historical_record)

            await session.commit()

            # Optionally store in Redis/file/local cache for use in dynamic adjustments
            self._latest_metrics = {
                "date": today,
                "treasury_balance": treasury_balance,
                "total_supply": total_supply,
                "circulating_supply": circulating_supply,
                "avg_wallet_balance": avg_wallet_balance,
                "transaction_volume": transaction_volume,
                "active_users": active_users,
                "volatility_index": volatility_index,
                "liquidity_ratio": liquidity_ratio,
                "velocity_of_money": velocity_of_money,
            }

        logger.info("[DAILY SNAPSHOT] Economy metrics collected.")

    async def get_economy_snapshot(self) -> dict:
        """
        Get the current economy snapshot.
        Returns cached metrics if available, otherwise calculates fresh snapshot.

        Returns:
            Dictionary with economy metrics including total_supply, circulating_supply, etc.
        """
        # Return cached metrics if available
        if hasattr(self, "_latest_metrics") and self._latest_metrics:
            return self._latest_metrics

        # Otherwise calculate fresh snapshot
        async with self.async_sessionmaker() as session:
            # Get supply info
            supply = await session.get(Supply, 1)
            if not supply:
                return {
                    "total_supply": Decimal("0"),
                    "circulating_supply": Decimal("0"),
                    "treasury_balance": Decimal("0"),
                }

            return {
                "total_supply": supply.total_supply,
                "circulating_supply": supply.circulating,
                "treasury_balance": supply.treasury,
            }

    async def get_or_create_user_economic_preferences(
        self, user_id: int
    ) -> UserEconomicPreferences:
        """
        Get user economic preferences, creating default ones if they don't exist.

        Args:
            user_id: Discord user ID

        Returns:
            UserEconomicPreferences object
        """
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            # Try to get existing preferences
            stmt = select(UserEconomicPreferences).where(
                UserEconomicPreferences.user_id == user_id
            )
            result = await session.execute(stmt)
            preferences = result.scalar_one_or_none()

            # If no preferences exist, create default ones
            if not preferences:
                preferences = UserEconomicPreferences(user_id=user_id)
                session.add(preferences)
                await session.commit()

            return preferences

    async def update_user_economic_preferences(self, user_id: int, **kwargs) -> None:
        """
        Update user economic preferences.

        Args:
            user_id: Discord user ID
            **kwargs: Preference fields to update
        """
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(UserEconomicPreferences).where(
                    UserEconomicPreferences.user_id == user_id
                )
                result = await session.execute(stmt)
                preferences = result.scalar_one_or_none()

                if not preferences:
                    preferences = UserEconomicPreferences(user_id=user_id)
                    session.add(preferences)

                # Update provided fields
                for key, value in kwargs.items():
                    if hasattr(preferences, key):
                        setattr(preferences, key, value)

                await session.commit()

    async def get_users_for_economic_alerts(self) -> list:
        """
        Get all users who have economic alerts enabled.

        Returns:
            List of user IDs
        """
        async with self.async_sessionmaker() as session:
            stmt = select(UserEconomicPreferences.user_id).where(
                UserEconomicPreferences.economic_alerts_enabled == True
            )
            result = await session.execute(stmt)
            hashes = [row[0] for row in result.fetchall()]
            mapping = await self.resolve_user_hashes(hashes)
            return [mapping.get(h) for h in hashes]

    async def check_and_send_economic_alerts(self) -> dict:
        """
        Check economic conditions and send alerts to users who have them enabled.

        Returns:
            Dictionary with alert statistics
        """
        # Get current economic factors
        factors = await self.get_economic_factors()
        velocity = float(factors.get("velocity_of_money", 0))
        liquidity = float(factors.get("liquidity_ratio", 0))
        volatility = float(factors.get("volatility_index", 0))

        # Get users with alerts enabled
        user_ids = await self.get_users_for_economic_alerts()

        alerts_sent = {
            "velocity": 0,
            "liquidity": 0,
            "volatility": 0,
            "total_users": len(user_ids),
        }

        # For now, we'll just return the stats without actually sending DMs
        # In a real implementation, we would send DMs to users through the bot

        return alerts_sent

    async def get_personalized_economic_recommendations(self, user_id: int) -> dict:
        """
        Get personalized economic recommendations based on user preferences and current conditions.

        Args:
            user_id: Discord user ID

        Returns:
            Dictionary with recommendations
        """
        raw_user_id = user_id
        user_id = self.hash_user_id(raw_user_id)
        # Get user preferences
        preferences = await self.get_or_create_user_economic_preferences(raw_user_id)

        # Get current economic factors
        factors = await self.get_economic_factors()
        health_score = await self.get_economic_health_score()

        recommendations = {
            "timestamp": discord.utils.utcnow().isoformat(),
            "user_risk_profile": preferences.risk_tolerance,
            "user_investment_style": preferences.investment_style,
            "economic_health_score": health_score["score"],
            "economic_health_status": health_score["status"],
            "current_conditions": {
                "treasury_health": float(factors.get("treasury_health", 0)),
                "liquidity_ratio": float(factors.get("liquidity_ratio", 0)),
                "velocity_of_money": float(factors.get("velocity_of_money", 0)),
                "volatility_index": float(factors.get("volatility_index", 0)),
                "fee_rate": float(factors.get("fee_rate", 0)),
            },
            "recommendations": [],
        }

        # Generate recommendations based on conditions
        liquidity_ratio = float(factors.get("liquidity_ratio", 0))
        velocity_of_money = float(factors.get("velocity_of_money", 0))

        # Liquidity-based recommendations (tiered to match scoring)
        # Scoring: 0.3-0.8 = 25pts (perfect), <0.3 scales down: 25 * (ratio/0.3)
        # So: 0.25 → 21pts (yellow), 0.15 → 12.5pts (orange), <0.15 → red
        if liquidity_ratio < 0.15:
            recommendations["recommendations"].append(
                {
                    "type": "liquidity",
                    "priority": "high",
                    "message": "Critical liquidity shortage. Economy is tight - cash is valuable.",
                    "action": "hold_cash",
                }
            )
        elif liquidity_ratio < 0.3:
            recommendations["recommendations"].append(
                {
                    "type": "liquidity",
                    "priority": "medium",
                    "message": "Low liquidity detected. Consider holding cash as opportunities may arise.",
                    "action": "hold_cash",
                }
            )
        elif liquidity_ratio > 0.85:
            recommendations["recommendations"].append(
                {
                    "type": "liquidity",
                    "priority": "medium",
                    "message": "Very high liquidity. Consider investments or large transactions.",
                    "action": "consider_investing",
                }
            )

        # Velocity-based recommendations (tiered to match scoring)
        # Scoring: velocity * 250, capped at 25 points
        # So: 0.1 → 25pts, 0.06 → 15pts, 0.02 → 5pts
        if velocity_of_money < 0.1:
            recommendations["recommendations"].append(
                {
                    "type": "velocity",
                    "priority": "high",
                    "message": "Very low economic activity. Transaction volumes are critically low.",
                    "action": "reduce_trading_activity",
                }
            )
        elif velocity_of_money < 0.2:
            recommendations["recommendations"].append(
                {
                    "type": "velocity",
                    "priority": "medium",
                    "message": "Low economic activity. Transaction volumes are below normal.",
                    "action": "reduce_trading_activity",
                }
            )
        elif velocity_of_money > 0.6:
            recommendations["recommendations"].append(
                {
                    "type": "velocity",
                    "priority": "info",
                    "message": "High economic activity. Markets are very active.",
                    "action": "increase_activity",
                }
            )

        # Risk tolerance based recommendations
        if preferences.risk_tolerance == "low":
            recommendations["recommendations"].append(
                {
                    "type": "risk",
                    "priority": "info",
                    "message": "Conservative risk profile detected. Focus on stable assets and avoid speculative trades.",
                    "action": "focus_stable_assets",
                }
            )
        elif preferences.risk_tolerance == "high":
            recommendations["recommendations"].append(
                {
                    "type": "risk",
                    "priority": "info",
                    "message": "Aggressive risk profile detected. You may consider higher-risk opportunities.",
                    "action": "consider_opportunities",
                }
            )

        return recommendations

    async def get_economic_trends(self, days: int = 30) -> dict:
        """
        Get economic trends over the specified number of days.

        Args:
            days: Number of days to analyze (default: 30)

        Returns:
            Dictionary containing trend analysis for key metrics
        """
        cutoff_date = discord.utils.utcnow().date() - timedelta(days=days)

        async with self.async_sessionmaker() as session:
            stmt = (
                select(EconomicMetricsHistory)
                .where(EconomicMetricsHistory.date >= cutoff_date)
                .order_by(EconomicMetricsHistory.date)
            )

            result = await session.execute(stmt)
            records = result.scalars().all()

            if not records:
                return {"error": "No historical data available"}

            # Calculate trends
            trends = {"period_days": days, "data_points": len(records), "metrics": {}}

            # Define metrics to analyze
            metrics_to_analyze = [
                "treasury_health",
                "liquidity_ratio",
                "velocity_of_money",
                "volatility_index",
                "transaction_volume",
                "active_users",
                "fee_rate",
                "passive_income_rate",
            ]

            for metric in metrics_to_analyze:
                values = [
                    getattr(record, metric)
                    for record in records
                    if getattr(record, metric) is not None
                ]
                if values:
                    # Calculate basic statistics
                    current = values[-1] if values else Decimal("0")
                    previous = values[-2] if len(values) > 1 else Decimal("0")

                    if len(values) > 1:
                        avg = sum(values) / len(values)
                        # Calculate trend (slope approximation)
                        trend_slope = (values[-1] - values[0]) / len(values)

                        trends["metrics"][metric] = {
                            "current": float(current),
                            "average": float(avg),
                            "change_from_previous": float(current - previous)
                            if previous != 0
                            else 0,
                            "change_percent": float(
                                ((current - previous) / previous) * 100
                            )
                            if previous != 0
                            else 0,
                            "trend_slope": float(trend_slope),
                            "min": float(min(values)),
                            "max": float(max(values)),
                        }
                    else:
                        trends["metrics"][metric] = {
                            "current": float(current),
                            "average": float(current),
                            "change_from_previous": 0,
                            "change_percent": 0,
                            "trend_slope": 0,
                            "min": float(current),
                            "max": float(current),
                        }

            return trends

    async def get_economic_health_score(self) -> dict:
        """
        Calculate an overall economic health score based on multiple factors.

        Returns:
            Dictionary containing health score and contributing factors
        """
        factors = await self.get_economic_factors()

        # Extract key metrics
        treasury_health = float(factors.get("treasury_health", 0))
        liquidity_ratio = float(factors.get("liquidity_ratio", 0))
        velocity_of_money = float(factors.get("velocity_of_money", 0))

        # Weighted scoring system
        # Volatility/wealth-inequality is not used; its 20% weight is redistributed
        # proportionally to the remaining three components:
        #   treasury: 30% → 37.5%
        #   liquidity: 25% → 31.25%
        #   velocity: 25% → 31.25%
        treasury_score = Decimal(str(treasury_health)) * Decimal("37.5")

        # Liquidity ratio (31.25% weight) - ideal is around 0.5-0.8
        if 0.3 <= liquidity_ratio <= 0.8:
            liquidity_score = Decimal("31.25")  # Perfect score for healthy liquidity
        elif liquidity_ratio > 0.8:
            liquidity_score = Decimal("31.25") * (
                Decimal("0.8") / Decimal(str(liquidity_ratio))
            )  # Decrease score for too much liquidity
        else:
            liquidity_score = Decimal("31.25") * (
                Decimal(str(liquidity_ratio)) / Decimal("0.3")
            )  # Decrease score for too little liquidity

        # Velocity of money (31.25% weight) - higher is generally better.
        # Daily velocity of 0.1 (10% of circulating supply transacted per day)
        # is a healthy, active Discord economy; scale so that reaches full points.
        velocity_score = min(Decimal("31.25"), Decimal(str(velocity_of_money)) * Decimal("312.5"))  # Cap at 31.25 points

        # Calculate total score (0-100)
        total_score = treasury_score + liquidity_score + velocity_score

        # Normalize to 0-100 range
        health_score = max(0, min(100, float(total_score)))

        # Determine health status
        if health_score >= 80:
            status = "Excellent"
        elif health_score >= 60:
            status = "Good"
        elif health_score >= 40:
            status = "Fair"
        elif health_score >= 20:
            status = "Poor"
        else:
            status = "Critical"

        return {
            "score": round(health_score, 2),
            "status": status,
            "components": {
                "treasury_health": {
                    "value": round(treasury_health * 100, 2),
                    "score": round(float(treasury_score), 2),
                    "weight": Decimal("37.5"),
                },
                "liquidity_ratio": {
                    "value": round(liquidity_ratio * 100, 2),
                    "score": round(float(liquidity_score), 2),
                    "weight": Decimal("31.25"),
                },
                "velocity_of_money": {
                    "value": round(velocity_of_money, 4),
                    "score": round(float(velocity_score), 2),
                    "weight": Decimal("31.25"),
                },
            },
        }

    async def get_historical_economic_metrics(self, days: int = 30) -> list:
        """
        Get raw historical economic metrics for the specified number of days.

        Args:
            days: Number of days to retrieve (default: 30)

        Returns:
            List of historical metrics dictionaries
        """
        cutoff_date = discord.utils.utcnow().date() - timedelta(days=days)

        async with self.async_sessionmaker() as session:
            stmt = (
                select(EconomicMetricsHistory)
                .where(EconomicMetricsHistory.date >= cutoff_date)
                .order_by(EconomicMetricsHistory.date.desc())
            )

            result = await session.execute(stmt)
            records = result.scalars().all()

            # Convert to dictionary format for easy consumption
            metrics_list = []
            for record in records:
                metrics_list.append(
                    {
                        "date": record.date.isoformat(),
                        "treasury_health": float(record.treasury_health),
                        "liquidity_ratio": float(record.liquidity_ratio),
                        "velocity_of_money": float(record.velocity_of_money),
                        "volatility_index": float(record.volatility_index),
                        "transaction_volume": float(record.transaction_volume),
                        "active_users": record.active_users,
                        "fee_rate": float(record.fee_rate),
                        "passive_income_rate": float(record.passive_income_rate),
                    }
                )

            return metrics_list

    async def add_loan_record(
        self,
        user_id: int,
        principal: Decimal,
        interest_rate: Decimal,
        total_repay: Decimal,
        due_date: datetime,
        status: str = "active",
    ):
        """
        Add a new loan record to the database for a user. Ensure one loan per user for simplicity.
        """
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                existing_loan = await session.execute(
                    select(Loan).where(Loan.user_id == user_id, Loan.status == "active")
                )
                if existing_loan.scalar_one_or_none():
                    raise ValueError("User already has an active loan.")

                new_loan = Loan(
                    user_id=user_id,
                    principal=principal,
                    interest_rate=interest_rate,
                    total_repay=total_repay,
                    due_date=due_date,
                    status=status,
                )
                session.add(new_loan)
            await session.commit()

    async def get_active_loans_for_user(self, user_id: int) -> list[Loan]:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Loan).where(
                    Loan.user_id == user_id,
                    Loan.status.in_(["active", "overdue", "defaulted"]),
                )
            )
            return result.scalars().all()

    async def update_loan_for_user(
        self,
        user_id: int,
        new_status: str,
        new_principal: Decimal = None,
        new_interest_rate: Decimal = None,
        new_total_repay: Decimal = None,
        new_due_date: datetime = None,
    ):
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Loan).where(Loan.user_id == user_id, Loan.status == "active")
                )
                active_loan = result.scalar_one_or_none()
                if not active_loan:
                    raise ValueError("No active loan found for user.")
                active_loan.status = new_status
                if new_principal is not None:
                    active_loan.principal = new_principal
                if new_interest_rate is not None:
                    active_loan.interest_rate = new_interest_rate
                if new_total_repay is not None:
                    active_loan.total_repay = new_total_repay
                if new_due_date is not None:
                    active_loan.due_date = new_due_date
            await session.commit()

    async def make_loan_payment(
        self, user_id: int, payment_amount: Decimal, notes: str = None
    ):
        """
        Record a partial or full loan payment. Updates the loan's amount_paid field
        and creates a LoanPayment record. Automatically marks loan as 'paid' if fully repaid.

        Args:
            user_id: Discord user ID
            payment_amount: Amount to pay (must be > 0 and <= remaining balance)
            notes: Optional notes about the payment

        Returns:
            dict with payment details and new balance

        Raises:
            ValueError: If no active loan exists or payment amount is invalid
        """
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)

        if payment_amount <= 0:
            raise ValueError("Payment amount must be greater than 0.")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Loan).where(
                        Loan.user_id == user_id, Loan.status.in_(["active", "overdue"])
                    )
                )
                active_loan = result.scalar_one_or_none()
                if not active_loan:
                    raise ValueError("No active loan found for user.")

                remaining_balance = active_loan.total_repay - active_loan.amount_paid
                if payment_amount > remaining_balance:
                    raise ValueError(
                        f"Payment amount exceeds remaining balance of {remaining_balance}."
                    )

                # Create payment record
                payment = LoanPayment(
                    loan_id=active_loan.id,
                    user_id=user_id,
                    payment_method="manual",  # Could be extended to support different methods
                    payment_amount=payment_amount,
                    notes=notes,
                )
                session.add(payment)

                # Update loan amount paid
                active_loan.amount_paid += payment_amount

                # Check if fully paid
                if active_loan.amount_paid >= active_loan.total_repay:
                    active_loan.status = "paid"
                    active_loan.amount_paid = (
                        active_loan.total_repay
                    )  # Ensure exact match

            await session.commit()

            return {
                "payment_amount": payment_amount,
                "previous_paid": active_loan.amount_paid - payment_amount,
                "new_amount_paid": active_loan.amount_paid,
                "remaining_balance": active_loan.total_repay - active_loan.amount_paid,
                "loan_status": active_loan.status,
            }

    async def get_loan_payment_history(self, user_id: int) -> list[LoanPayment]:
        """
        Retrieve all payment records for a user's loans.

        Args:
            user_id: Discord user ID

        Returns:
            List of LoanPayment records ordered by payment_date descending
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(LoanPayment)
                .join(Loan, LoanPayment.loan_id == Loan.id)
                .where(Loan.user_id == user_id)
                .order_by(LoanPayment.payment_date.desc())
            )
            return result.scalars().all()

    async def get_loan_remaining_balance(self, user_id: int) -> Decimal:
        """
        Calculate the remaining balance on a user's active loan.

        Args:
            user_id: Discord user ID

        Returns:
            Decimal representing remaining balance (total_repay - amount_paid)

        Raises:
            ValueError: If no active loan exists
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Loan).where(
                    Loan.user_id == user_id, Loan.status.in_(["active", "overdue"])
                )
            )
            active_loan = result.scalar_one_or_none()
            if not active_loan:
                raise ValueError("No active loan found for user.")

            return active_loan.total_repay - active_loan.amount_paid

    async def date_check_loans(self):
        """
        Check all active loans and mark those past due as 'defaulted'.
        Add penalty of 10% of loan amount to the total_repay amount for defaulted loans each day it is not repaid.
        If the loan is not paid back in 7 days after the due date, freeze the user's wallet.
        Automatically unfreeze the wallet 7 days after defaulting.
        This can be scheduled to run periodically (e.g., every hour).
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                now = discord.utils.utcnow()

                # Process overdue loans
                result = await session.execute(
                    select(Loan).where(Loan.status == "active", Loan.due_date < now)
                )
                overdue_loans = result.scalars().all()
                for loan in overdue_loans:
                    days_overdue = (now - loan.due_date).days
                    if days_overdue > 0:
                        loan.status = "overdue"
                        loan.total_repay += (
                            loan.principal * Decimal("0.1") * days_overdue
                        ).quantize(Decimal("0.1"))
                    if days_overdue >= 7:
                        loan.status = "defaulted"
                        loan.defaulted_date = now
                        raw_uid = await self.resolve_user_hash(loan.user_id)
                        wallet = await self.get_wallet_by_user_id(raw_uid)
                        if wallet and not wallet.wallet_frozen:
                            await self.freeze_wallet(wallet.wallet_id)

                # Unfreeze wallets 7 days after defaulting
                result = await session.execute(
                    select(Loan)
                    .join(Wallet, Loan.user_id == Wallet.user_id)
                    .where(
                        Loan.status == "defaulted",
                        Loan.defaulted_date != None,
                        Wallet.wallet_frozen == True,
                    )
                )
                defaulted_loans = result.scalars().all()
                for loan in defaulted_loans:
                    days_defaulted = (now - loan.defaulted_date).days
                    if days_defaulted >= 7:
                        raw_uid = await self.resolve_user_hash(loan.user_id)
                        wallet = await self.get_wallet_by_user_id(raw_uid)
                        if wallet and wallet.wallet_frozen:
                            await self.unfreeze_wallet(wallet.wallet_id)

    async def get_dynamic_reward_multiplier(self) -> Decimal:
        """
        Calculate dynamic reward multiplier based on economic conditions.

        Uses linear interpolation for smooth transitions instead of step tiers:
          liquidity <= 0.20 → 1.5x (max boost)
          liquidity  = 0.50 → 1.0x (neutral)
          liquidity >= 0.80 → 0.85x (slight reduction)

        Also factors in treasury health: if treasury is below its dynamic target,
        rewards are reduced to slow outflow; if above, rewards are boosted.
        Final multiplier is clamped to [0.80, 1.60].
        """
        factors = await self.get_economic_factors()
        liquidity_ratio = factors.get("liquidity_ratio", Decimal("0.5"))
        treasury_health = factors.get("treasury_health", Decimal("0.5"))
        target_ratio = factors.get("target_ratio", Decimal("0.50"))

        # Linear interpolation for liquidity-based multiplier
        if liquidity_ratio <= Decimal("0.20"):
            liquidity_mult = Decimal("1.5")
        elif liquidity_ratio <= Decimal("0.50"):
            # Interpolate from 1.5 (at 0.20) to 1.0 (at 0.50)
            t = (liquidity_ratio - Decimal("0.20")) / Decimal("0.30")
            liquidity_mult = Decimal("1.5") - (t * Decimal("0.5"))
        elif liquidity_ratio <= Decimal("0.80"):
            # Interpolate from 1.0 (at 0.50) to 0.85 (at 0.80)
            t = (liquidity_ratio - Decimal("0.50")) / Decimal("0.30")
            liquidity_mult = Decimal("1.0") - (t * Decimal("0.15"))
        else:
            liquidity_mult = Decimal("0.85")

        # Treasury health adjustment: slow outflow when treasury is low, boost when high
        treasury_adj = Decimal("0")
        if treasury_health < target_ratio - Decimal("0.10"):
            treasury_adj = Decimal("-0.10")  # Treasury stressed, reduce rewards
        elif treasury_health > target_ratio + Decimal("0.10"):
            treasury_adj = Decimal("0.10")  # Treasury overflowing, boost rewards

        multiplier = liquidity_mult + treasury_adj

        # Clamp to safe range
        return max(Decimal("0.80"), min(Decimal("1.60"), multiplier))

    async def check_economic_circuit_breaker(self) -> dict:
        """
        Check if economic circuit breakers should be triggered based on velocity and other metrics.

        Returns a dictionary with breaker status and reason.
        """
        factors = await self.get_economic_factors()
        velocity = factors.get("velocity_of_money", Decimal("0"))
        liquidity_ratio = factors.get("liquidity_ratio", Decimal("0.5"))
        # volatility = factors.get("volatility_index", Decimal("0.02"))

        # Define thresholds
        VELOCITY_CRISIS_THRESHOLD = Decimal("0.001")  # Near-zero money velocity
        LIQUIDITY_CRISIS_THRESHOLD = Decimal("0.005")  # Less than 0.5% circulating

        # Bootstrap guard: a fresh/empty database has no circulating currency and
        # therefore no velocity. The circuit breaker is meant to detect crises in
        # an active economy, not to prevent the economy from starting up.
        if liquidity_ratio <= 0:
            return {
                "triggered": False,
                "reasons": [],
                "velocity": velocity,
                "liquidity_ratio": liquidity_ratio,
            }

        reason = []

        if velocity < VELOCITY_CRISIS_THRESHOLD:
            reason.append("Low velocity of money")

        if liquidity_ratio < LIQUIDITY_CRISIS_THRESHOLD:
            reason.append("Low liquidity")

        # Both conditions must be met to trigger the circuit breaker
        circuit_breaker_triggered = (
            velocity < VELOCITY_CRISIS_THRESHOLD
            and liquidity_ratio < LIQUIDITY_CRISIS_THRESHOLD
        )

        return {
            "triggered": circuit_breaker_triggered,
            "reasons": reason,
            "velocity": velocity,
            "liquidity_ratio": liquidity_ratio,
        }

    async def get_enhanced_fee_rate(
        self, transaction_type: str = "standard"
    ) -> Decimal:
        """
        Calculate enhanced fee rate based on multiple economic factors.

        Args:
            transaction_type: Type of transaction ('standard', 'high_value', 'gambling')
        """
        factors = await self.get_economic_factors()
        base_fee = factors.get("fee_rate", Decimal("0.01"))
        volatility = factors.get("volatility_index", Decimal("0.02"))
        liquidity_ratio = factors.get("liquidity_ratio", Decimal("0.5"))
        velocity = factors.get("velocity_of_money", Decimal("0.1"))

        # Adjust fee based on transaction type
        if transaction_type == "high_value":
            # Higher fees for high-value transactions during volatile times
            fee = base_fee * (1 + volatility * Decimal("2.0"))
        elif transaction_type == "gambling":
            # Special fee structure for gambling
            fee = base_fee * (1 + (Decimal("1.0") - liquidity_ratio))
        else:
            # Standard transaction fees
            fee = base_fee * (1 + volatility * Decimal("0.5"))

        # Additional adjustment based on velocity
        if velocity < Decimal("0.05"):
            # Low velocity indicates economic slowdown - reduce fees to stimulate activity
            fee *= Decimal("0.8")
        elif velocity > Decimal("0.5"):
            # High velocity indicates hot market - increase fees to cool down
            fee *= Decimal("1.2")

        # Ensure fee stays within reasonable bounds
        MAX_FEE = Decimal("0.20")  # 20%
        MIN_FEE = Decimal("0.001")  # 0.1%

        return max(MIN_FEE, min(MAX_FEE, fee))

    async def get_job(self, user_id: int) -> Optional[Job]:
        """Get a user's current job, if any."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Job).where(Job.user_id == user_id))
            return result.scalar_one_or_none()

    async def apply_for_job(
        self, user_id: int, job_title: str, base_salary: Decimal
    ) -> Job:
        """Apply for a job. Creates a new job record for the user."""
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Check if user already has a job
                existing = await session.execute(
                    select(Job).where(Job.user_id == user_id)
                )
                if existing.scalar_one_or_none():
                    raise ValueError(
                        "You already have a job. Quit your current job first."
                    )

                job = Job(
                    user_id=user_id,
                    title=job_title,
                    base_salary=base_salary,
                    days_employed=1,
                    streak=0,
                    hired_at=discord.utils.utcnow(),
                )
                session.add(job)
            await session.commit()
            return job

    async def quit_job(self, user_id: int) -> bool:
        """Remove a user's job record."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Job).where(Job.user_id == user_id)
                )
                job = result.scalar_one_or_none()
                if not job:
                    raise ValueError("You don't have a job to quit.")
                await session.delete(job)
            await session.commit()
            return True

    async def work_job(self, user_id: int) -> Tuple[Job, Decimal]:
        """
        Process a user's work action.
        Returns the updated job and the calculated salary.
        Raises ValueError if cooldown hasn't expired, no job found, or user was fired for absence.
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Job).where(Job.user_id == user_id)
                )
                job = result.scalar_one_or_none()
                if not job:
                    raise ValueError("You don't have a job. Apply for one first!")

                now = discord.utils.utcnow()

                # Check if user missed a day (more than 48 hours since last work)
                # They get fired for not showing up
                if job.last_worked:
                    time_since_last = (now - job.last_worked).total_seconds()
                    if time_since_last > 172800:  # 48 hours = 172800 seconds
                        # Fire the employee for missing work
                        await session.delete(job)
                        await session.commit()
                        raise ValueError(
                            "You were fired for missing work! You didn't show up for more than 48 hours. "
                            "You'll need to apply for a new job."
                        )

                    # Check if 24 hours have passed since last work (normal cooldown)
                    cooldown_remaining = (
                        86400 - time_since_last
                    )  # 24 hours = 86400 seconds
                    if cooldown_remaining > 0:
                        hours = int(cooldown_remaining // 3600)
                        minutes = int((cooldown_remaining % 3600) // 60)
                        raise ValueError(
                            f"You need to wait {hours}h {minutes}m before working again."
                        )

                # Calculate salary with tenure bonus
                weeks_employed = job.days_employed / 7
                salary_multiplier = min(2.0, 1.0 + (weeks_employed * 0.05))
                current_salary = job.base_salary * Decimal(str(salary_multiplier))
                current_salary = current_salary.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

                # Update job record
                job.last_worked = now
                job.days_employed += 1
                job.streak += 1

            await session.commit()
            return job, current_salary

    async def calculate_salary(self, job: Job) -> Decimal:
        """Calculate current salary based on tenure."""
        weeks_employed = job.days_employed / 7
        salary_multiplier = min(2.0, 1.0 + (weeks_employed * 0.05))
        current_salary = job.base_salary * Decimal(str(salary_multiplier))
        return current_salary.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    async def get_employees_for_firing(self) -> List[Job]:
        """
        Get employees who haven't worked in 48+ hours.
        These users should be fired.
        """
        threshold = discord.utils.utcnow() - timedelta(hours=48)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Job).where(
                    (Job.last_worked != None) & (Job.last_worked < threshold)
                )
            )
            return result.scalars().all()

    async def fire_employee(self, user_id: int) -> bool:
        """Remove a job record for an inactive employee."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Job).where(Job.user_id == user_id)
                )
                job = result.scalar_one_or_none()
                if job:
                    await session.delete(job)
            await session.commit()
            return True

    async def get_all_jobs(self) -> List[Job]:
        """Get all job records (for admin purposes)."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Job))
            return result.scalars().all()
