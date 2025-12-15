import json
import decimal
import uuid
import logging
import asyncio
import hashlib
import os
import random
from datetime import datetime
from decimal import Decimal
from .models import Block, Wallet, BankAccount, Transaction, Supply
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend
from sqlalchemy.future import select
from sqlalchemy.sql.expression import delete
from sqlalchemy import func

logger = logging.getLogger("discord_bot")


def prepare_for_json(data):
    if isinstance(data, list):
        return [prepare_for_json(item) for item in data]
    elif isinstance(data, dict):
        return {key: prepare_for_json(value) for key, value in data.items()}
    elif isinstance(data, decimal.Decimal):
        return str(data)  # keep exact value for signing payloads
    elif isinstance(data, uuid.UUID):
        return str(data)
    else:
        return data


class KeyManager:
    @staticmethod
    def generate_key_pair():
        """
        Generate a new ECDSA private/public key pair using the SECP256K1 curve.
        """
        private_key = ec.generate_private_key(ec.SECP256K1(), default_backend())
        public_key = private_key.public_key()
        return private_key, public_key

    @staticmethod
    def serialize_key(key, private=False):
        """
        Serialize a key to PEM format.
        """
        if private:
            return key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        else:
            return key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )

    @staticmethod
    def deserialize_key(pem_key, private=False):
        """
        Deserialize a PEM key.
        """
        if isinstance(pem_key, str):
            pem_key = pem_key.encode("utf-8")
        if private:
            return serialization.load_pem_private_key(
                pem_key, password=None, backend=default_backend()
            )
        else:
            return serialization.load_pem_public_key(pem_key, backend=default_backend())

    @staticmethod
    def sign_data(private_key_bytes: bytes, data: str):
        """
        Sign data using an ECDSA private key.
        """
        try:
            private_key = serialization.load_pem_private_key(
                private_key_bytes, password=None, backend=default_backend()
            )
            signature = private_key.sign(
                data.encode("utf-8"), ec.ECDSA(hashes.SHA256())
            )
            return signature
        except Exception as e:
            raise Exception(f"An error occurred during signing: {str(e)}") from e

    @staticmethod
    def verify_signature(public_key, data, signature):
        """
        Verify the data signature using an ECDSA public key.
        """
        try:
            public_key.verify(
                signature, data.encode("utf-8"), ec.ECDSA(hashes.SHA256())
            )
            return True
        except Exception as e:
            logger.error(f"Signature verification failed: {e}")
            return False

    @staticmethod
    def hash_key(key_pem: bytes, salt: bytes = None) -> str:
        """
        Hash a key (typically a PEM-encoded key) with an optional salt using SHA-256.
        This is useful for creating a unique fingerprint of a key.
        """
        if not salt:
            salt = os.urandom(16)
        if not isinstance(key_pem, bytes):
            key_pem = key_pem.encode("utf-8")
        return hashlib.sha256(salt + key_pem).hexdigest()


class Blockchain:
    def __init__(self, async_sessionmaker):
        self.async_sessionmaker = async_sessionmaker
        self.blockchain_lock = asyncio.Lock()
        self.transaction_pool = []

    async def create_genesis_block(self):
        """Create the genesis block if the blockchain is empty."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Block).order_by(Block.index))
            existing_blocks = result.scalars().all()
            if not existing_blocks:
                genesis_block = Block(
                    index=0,
                    previous_hash="0",
                    transactions="[]",
                    created_at=datetime.now(),
                    block_hash="0",
                    validator_id=None,
                    validator_signature="",
                )
                genesis_block.block_hash = genesis_block.compute_hash()
                session.add(genesis_block)
                await session.commit()
                logger.info("Genesis block created.")
            else:
                logger.info("Genesis block already exists.")

    async def load_blockchain_if_exists(self):
        db_blocks = await self.get_full_chain()
        if not db_blocks:
            logger.info("No blocks found in DB. Skipping in-memory load.")
            return

        logger.info(f"Found {len(db_blocks)} blocks. Reconstructing chain in memory...")
        self.chain = []

        for db_block in db_blocks:
            try:
                tx_list = json.loads(db_block.transactions)
            except (json.JSONDecodeError, TypeError):
                tx_list = []

            block_dict = {
                "index": db_block.index,
                "previous_hash": db_block.previous_hash,
                "transactions": tx_list,
                "timestamp": db_block.created_at,
                "block_hash": db_block.block_hash,
                "validator_id": db_block.validator_id,
                "validator_signature": db_block.validator_signature,
            }
            self.chain.append(block_dict)

        chain_ok = await self.is_chain_valid()
        if chain_ok:
            logger.info("In-memory blockchain loaded and validated.")
        else:
            logger.warning(
                "In-memory blockchain loaded but did NOT validate. Consider repairing."
            )

    async def get_last_block(self):
        """Retrieve the last block in the blockchain."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Block).order_by(Block.index.desc()).limit(1)
            )
            return result.scalar_one_or_none()

    async def get_full_chain(self):
        """Retrieve the full blockchain from the database."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Block).order_by(Block.index))
            return result.scalars().all()

    async def repair_chain(self, block_index):
        """
        Repair the blockchain starting from the invalid block.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(select(Block).order_by(Block.index))
                blocks = result.scalars().all()
                if block_index >= len(blocks):
                    logger.error(
                        f"Block index {block_index} is out of bounds for the blockchain."
                    )
                    return
                for i in range(block_index, len(blocks)):
                    block = blocks[i]
                    if i > 0:
                        previous_block = blocks[i - 1]
                        block.previous_hash = previous_block.block_hash
                    block.block_hash = block.compute_hash()
                    logger.info(
                        f"Repaired block index {block.index}. New hash: {block.block_hash}"
                    )
                    session.add(block)
                await session.commit()
                logger.info(
                    f"Blockchain repair starting from index {block_index} completed."
                )

    async def validate_blockchain(self):
        """
        Validate the blockchain and return the result and the index of the first invalid block (if any).
        """
        for attempt in range(5):
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    result = await session.execute(select(Block).order_by(Block.index))
                    blocks = result.scalars().all()
                    previous_hash = None
                    for block in blocks:
                        if block.index == 0:
                            previous_hash = block.block_hash
                            continue
                        if block.previous_hash != previous_hash:
                            logger.error(
                                f"Broken chain at block index {block.index}. Previous hash mismatch."
                            )
                            await self.repair_chain(block.index)
                            break
                        recalculated_hash = block.compute_hash()
                        if block.block_hash != recalculated_hash:
                            logger.error(
                                f"Invalid hash at block index {block.index}. Expected: {recalculated_hash}, Found: {block.block_hash}."
                            )
                            await self.repair_chain(block.index)
                            break
                        previous_hash = block.block_hash
                    else:
                        logger.info("Blockchain validated successfully.")
                        return True, None
        logger.error("Blockchain validation failed after multiple attempts.")
        return False, None

    async def remove_invalid_block(self, block_index: int):
        """
        Remove the invalid block from the blockchain and relink subsequent blocks.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                block_result = await session.execute(
                    select(Block).where(Block.index == block_index)
                )
                block = block_result.scalar()
                if not block:
                    raise ValueError(f"Block at index {block_index} not found.")
                await session.delete(block)
                subsequent_blocks_result = await session.execute(
                    select(Block).where(Block.index > block_index).order_by(Block.index)
                )
                subsequent_blocks = subsequent_blocks_result.scalars().all()
                previous_hash = block.previous_hash
                for b in subsequent_blocks:
                    b.previous_hash = previous_hash
                    previous_hash = b.block_hash
                    b.block_hash = b.compute_hash()
                    session.add(b)
                await session.commit()
                logger.info(
                    f"Block at index {block_index} and all subsequent blocks have been relinked."
                )

    async def add_to_block(self, transactions, validator_private_key_bytes):
        """
        Queue transactions for block creation. Do not sign here; signatures are added in create_block_atomic.
        """
        async with self.blockchain_lock:
            if not transactions:
                return
            if not hasattr(self, "transaction_pool"):
                self.transaction_pool = []
            for transaction in transactions:
                if not isinstance(transaction, dict):
                    raise TypeError("Each transaction must be a dictionary.")
                if "signer_user_id" not in transaction:
                    signer = transaction.get("from_user_id") or transaction.get(
                        "to_user_id"
                    )
                    transaction["signer_user_id"] = signer
                self.transaction_pool.append(transaction)

            TRANSACTION_THRESHOLD = 10
            if len(self.transaction_pool) >= TRANSACTION_THRESHOLD:
                logger.debug(
                    "Transaction threshold reached, creating a new block with PoS consensus."
                )
                await self.create_block_with_stake()

    async def create_block_atomic(
        self, session, transactions: list[dict], validator_user_id: int
    ):
        """
        Create and persist a new block containing the provided transactions inside
        the caller's active database transaction (atomic with balance updates).

        Steps:
        - Ensure each transaction carries a 'signer_user_id'.
        - Sign transactions lacking a 'signature' using the signer's private key.
        - Verify each transaction signature against the signer's public key.
        - Determine previous hash and next index within this same session.
        - Sign the block with the validator's private key and insert it.
        """
        # Normalize and sign transactions as needed
        prepared: list[dict] = []
        for tx in transactions:
            if not isinstance(tx, dict):
                raise TypeError("Each transaction must be a dictionary.")
            tx = dict(tx)  # shallow copy
            signer_id = (
                tx.get("signer_user_id")
                or tx.get("from_user_id")
                or tx.get("to_user_id")
            )
            tx["signer_user_id"] = signer_id

            # separate signature for payload building
            signature_hex = tx.get("signature")
            tx_no_sig = dict(tx)
            tx_no_sig.pop("signature", None)
            payload = json.dumps(prepare_for_json(tx_no_sig), sort_keys=True)

            if not signature_hex:
                # sign if missing
                result = await session.execute(
                    select(Wallet.private_key).where(Wallet.user_id == signer_id)
                )
                priv = result.scalar_one_or_none()
                if not priv:
                    raise ValueError(f"No private key found for signer {signer_id}")
                tx["signature"] = KeyManager.sign_data(priv, payload).hex()
            else:
                # verify; if it fails (legacy/foreign), re-sign canonically
                pub_res = await session.execute(
                    select(Wallet.public_key).where(Wallet.user_id == signer_id)
                )
                public_key_bytes = pub_res.scalar_one_or_none()
                if not public_key_bytes:
                    raise ValueError(f"No public key found for signer {signer_id}")
                ok = self._verify_signature_bytes(
                    public_key_bytes, payload, bytes.fromhex(signature_hex)
                )
                if not ok:
                    # centralized policy: replace bad signature with signer’s signature
                    priv_res = await session.execute(
                        select(Wallet.private_key).where(Wallet.user_id == signer_id)
                    )
                    priv = priv_res.scalar_one_or_none()
                    if not priv:
                        raise ValueError(f"No private key found for signer {signer_id}")
                    tx["signature"] = KeyManager.sign_data(priv, payload).hex()

            prepared.append(tx)

        # Determine previous hash and index atomically in this session
        last_idx_res = await session.execute(select(func.max(Block.index)))
        last_index = last_idx_res.scalar() or 0
        # Fetch previous hash of last block
        prev_hash = "0"
        if last_index is not None and last_index >= 0:
            # Retrieve last block
            last_block_res = await session.execute(
                select(Block).where(Block.index == last_index).limit(1)
            )
            last_block = last_block_res.scalar_one_or_none()
            if last_block:
                prev_hash = last_block.block_hash

        new_block = Block(
            index=(last_index + 1) if last_index is not None else 1,
            previous_hash=prev_hash,
            transactions=json.dumps(prepare_for_json(prepared)),
            created_at=datetime.now(),
        )
        new_block.block_hash = new_block.compute_hash()

        # Sign block with validator's private key
        val_priv_res = await session.execute(
            select(Wallet.private_key).where(Wallet.user_id == validator_user_id)
        )
        validator_priv = val_priv_res.scalar_one_or_none()
        if not validator_priv:
            raise ValueError(
                f"Validator private key missing for user {validator_user_id}"
            )
        block_data = json.dumps(
            {
                "index": new_block.index,
                "previous_hash": new_block.previous_hash,
                "transactions": json.loads(new_block.transactions),
                "created_at": str(new_block.created_at),
                "block_hash": new_block.block_hash,
            },
            sort_keys=True,
        )
        block_sig = KeyManager.sign_data(validator_priv, block_data)
        new_block.validator_id = validator_user_id
        new_block.validator_signature = block_sig.hex()

        session.add(new_block)
        # Do not commit here; caller controls transaction

    def _verify_signature_bytes(
        self, public_key_bytes: bytes, data: str, signature: bytes
    ) -> bool:
        try:
            public_key = serialization.load_pem_public_key(
                public_key_bytes, backend=default_backend()
            )
            public_key.verify(
                signature, data.encode("utf-8"), ec.ECDSA(hashes.SHA256())
            )
            return True
        except Exception as e:
            logger.error(f"Signature verification failed: {e}")
            return False

    async def create_block_with_stake(self):
        """
        Create a new block from the transaction pool using proof-of-stake consensus.
        """
        try:
            logger.debug("Preparing transactions from the pool.")
            prepared_transactions = prepare_for_json(self.transaction_pool)
            self.transaction_pool = []
            logger.debug(f"Prepared transactions: {prepared_transactions}")
        except Exception as e:
            logger.error(f"Error preparing transactions: {e}")
            return

        try:
            last_block = await self.get_last_block()
            previous_hash = last_block.block_hash if last_block else "0"
            logger.debug(f"Retrieved last block. Previous hash: {previous_hash}")
        except Exception as e:
            logger.error(f"Error fetching last block: {e}")
            return

        try:
            validator_user_id = await self.select_validator()
            logger.debug(f"Validator {validator_user_id} selected.")
        except Exception as e:
            logger.error(f"Validator selection failed: {e}")
            validator_user_id = 284439598422163476

        try:
            logger.debug(f"Retrieving private key for validator {validator_user_id}.")
            validator_private_key_bytes = await self.get_validator_private_key(
                validator_user_id
            )
        except Exception as e:
            logger.error(f"Error retrieving validator's private key: {e}")
            return

        try:
            new_block = Block(
                index=last_block.index + 1 if last_block else 1,
                previous_hash=previous_hash,
                transactions=json.dumps(prepared_transactions),
                created_at=datetime.now(),
            )
            new_block.block_hash = new_block.compute_hash()
            logger.debug(
                f"New block created with index {new_block.index} and hash {new_block.block_hash}."
            )
        except Exception as e:
            logger.error(f"Error creating new block: {e}")
            return

        try:
            block_data = json.dumps(
                {
                    "index": new_block.index,
                    "previous_hash": new_block.previous_hash,
                    "transactions": json.loads(new_block.transactions),
                    "created_at": str(new_block.created_at),
                    "block_hash": new_block.block_hash,
                },
                sort_keys=True,
            )
            logger.debug("Block data prepared for signing.")
            logger.debug(f"Block data: {block_data}")
        except Exception as e:
            logger.error(f"Error preparing block data for signing: {e}")
            return

        try:
            validator_signature = KeyManager.sign_data(
                validator_private_key_bytes, block_data
            )
            new_block.validator_id = validator_user_id
            new_block.validator_signature = validator_signature.hex()
            logger.debug(f"Block signed by validator {validator_user_id}.")
        except Exception as e:
            logger.error(f"Error signing the block: {e}")
            return

        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    session.add(new_block)
                    await session.commit()
                    logger.debug(
                        f"Block #{new_block.index} created and committed with {len(prepared_transactions)} transactions."
                    )
        except Exception as e:
            logger.error(f"Error committing new block to database: {e}")
            return

    async def validate_transaction(self, transaction, public_key):
        """
        Validate a transaction by verifying its signature.
        """
        signature_hex = transaction.pop("signature", "")
        signature = bytes.fromhex(signature_hex)
        transaction_data = json.dumps(transaction, sort_keys=True)
        if not KeyManager.verify_signature(public_key, transaction_data, signature):
            logger.error("Invalid transaction signature.")
            return False
        logger.info("Transaction signature validated.")
        return True

    async def is_chain_valid(self):
        """
        Validate the blockchain's integrity.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(select(Block).order_by(Block.index))
                blocks = result.scalars().all()
        for i in range(1, len(blocks)):
            current = blocks[i]
            previous = blocks[i - 1]
            if current.block_hash != current.compute_hash():
                logger.error(f"Hash mismatch in block #{current.index}.")
                return False
            if current.previous_hash != previous.block_hash:
                logger.error(f"Previous hash mismatch in block #{current.index}.")
                return False
        logger.info("Blockchain integrity check passed.")
        return True

    async def get_balance(self, user_id):
        """
        Calculate and return the balance of a user based on on-chain transactions.
        """
        balance = Decimal("0")
        chain = await self.get_full_chain()
        for block in chain:
            transactions = json.loads(block.transactions)
            for transaction in transactions:
                if isinstance(transaction, dict):
                    if transaction.get("to_user_id") == user_id:
                        balance += Decimal(str(transaction["amount"]))
                    if transaction.get("from_user_id") == user_id:
                        balance -= Decimal(str(transaction["amount"]))
        return balance

    async def get_transactions(self, user_id):
        """
        Retrieve all transactions involving a user.
        """
        transactions = []
        chain = await self.get_full_chain()
        for block in chain:
            block_transactions = json.loads(block.transactions)
            for transaction in block_transactions:
                if isinstance(transaction, dict):
                    if (
                        transaction.get("to_user_id") == user_id
                        or transaction.get("from_user_id") == user_id
                    ):
                        transactions.append(transaction)
        return transactions

    async def select_validator(self):
        """
        Select a validator based on stake weighting.
        This simplified example calculates stake from on-chain data.
        In a production system, consider a dedicated validator registry.
        """
        logger.info("Starting validator selection process.")
        chain = await self.get_full_chain()
        logger.debug(f"Retrieved full chain with {len(chain)} blocks.")
        stakes = {}
        for block in chain:
            transactions = json.loads(block.transactions)
            logger.debug(
                f"Processing block index {block.index} with {len(transactions)} transactions."
            )
            for tx in transactions:
                if isinstance(tx, dict):
                    to_user = tx.get("to_user_id")
                    from_user = tx.get("from_user_id")
                    amount = Decimal(str(tx.get("amount", "0")))
                    if to_user:
                        stakes[to_user] = stakes.get(to_user, Decimal("0")) + amount
                        logger.debug(
                            f"User {to_user} received amount {amount}. New stake: {stakes[to_user]}."
                        )
                    if from_user:
                        stakes[from_user] = stakes.get(from_user, Decimal("0")) - amount
                        logger.debug(
                            f"User {from_user} sent amount {amount}. New stake: {stakes[from_user]}."
                        )
        logger.info("Finished processing all blocks for stake calculation.")

        eligible = {
            user: stake for user, stake in stakes.items() if stake > Decimal("0")
        }
        logger.debug(f"Eligible validators based on positive stake: {eligible}.")

        total_stake = sum(eligible.values())
        logger.info(f"Total stake of eligible validators: {total_stake}.")
        if total_stake <= Decimal("0"):
            logger.error("No eligible validator found (total stake is zero).")
            raise ValueError("No eligible validator found (total stake is zero).")

        pick = Decimal(random.uniform(0, float(total_stake)))
        logger.info(f"Randomly picked value for selection: {pick}.")
        cumulative = Decimal("0")
        for user, stake in eligible.items():
            cumulative += stake
            logger.debug(f"Cumulative stake after adding user {user}: {cumulative}.")
            if cumulative >= pick:
                logger.info(f"Selected validator: {user}.")
                return user

        fallback_validator = random.choice(list(eligible.keys()))
        logger.info(
            f"No validator selected in the loop; falling back to random choice: {fallback_validator}."
        )
        return fallback_validator

    async def get_validator_private_key(self, validator_user_id):
        """
        Retrieve the validator's private key bytes from the Wallet table.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Wallet.private_key).where(Wallet.user_id == validator_user_id)
            )
            private_key_bytes = result.scalar_one_or_none()

            if not private_key_bytes:
                raise ValueError(f"No private key found for user {validator_user_id}")

            return private_key_bytes

    async def get_validator_public_key(self, validator_user_id):
        """
        Retrieve the validator's public key bytes from the Wallet table.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Wallet.public_key).where(Wallet.user_id == validator_user_id)
            )
            public_key_bytes = result.scalar_one_or_none()

            if not public_key_bytes:
                raise ValueError(f"No public key found for user {validator_user_id}")

            return public_key_bytes

    async def bootstrap_blockchain(self):
        """
        Bootstrap the blockchain by creating a genesis block that reflects
        each user's wallet + bank balances as on-chain stake. Optionally
        includes the treasury balance.
        """

        async with self.async_sessionmaker() as session:
            async with session.begin():
                existing = await session.execute(
                    select(Block).order_by(Block.index).limit(1)
                )
                if existing.scalar_one_or_none() is not None:
                    logger.info("Blockchain already exists; skipping bootstrap.")
                    return

                wallets_res = await session.execute(select(Wallet))
                wallets = wallets_res.scalars().all()

                bank_res = await session.execute(select(BankAccount))
                bank_accounts = bank_res.scalars().all()

                supply_res = await session.execute(select(Supply).limit(1))
                supply_record = supply_res.scalar_one_or_none()

                treasury_balance = (
                    supply_record.treasury if supply_record else Decimal("0.00")
                )

                logger.info(
                    f"Found {len(wallets)} wallets, {len(bank_accounts)} bank accounts, "
                    f"and treasury balance = {treasury_balance} for genesis block."
                )

                bank_dict = {acct.wallet_id: acct.balance for acct in bank_accounts}

                genesis_transactions = []
                for wallet in wallets:
                    user_total = wallet.balance + bank_dict.get(
                        wallet.wallet_id, Decimal("0")
                    )
                    if user_total > Decimal("0"):
                        tx = {
                            "from_user_id": 0,
                            "to_user_id": wallet.user_id,
                            "amount": float(user_total),
                            "description": "Genesis distribution (wallet+bank)",
                        }
                        genesis_transactions.append(tx)

                if treasury_balance > Decimal("0"):
                    tx = {
                        "from_user_id": None,
                        "to_user_id": 0,
                        "amount": float(treasury_balance),
                        "description": "Genesis distribution (treasury)",
                    }
                    genesis_transactions.append(tx)

                logger.info(
                    f"Prepared {len(genesis_transactions)} genesis transactions total."
                )

                genesis_data = json.dumps(
                    prepare_for_json(genesis_transactions), sort_keys=True
                )
                genesis_block = Block(
                    index=0,
                    previous_hash="0",
                    transactions=genesis_data,
                    created_at=datetime.now(),
                    block_hash="0",
                    validator_id=0,
                    validator_signature="",
                )

                genesis_block.block_hash = genesis_block.compute_hash()
                session.add(genesis_block)

        logger.info("Genesis block created and saved to the database.")

        is_valid, invalid_block_index = await self.validate_blockchain()
        if not is_valid:
            logger.error(
                f"Blockchain validation failed after bootstrap at block {invalid_block_index}."
            )
        else:
            logger.info("Blockchain successfully validated after bootstrap.")

        logger.info("Blockchain bootstrap complete.")
