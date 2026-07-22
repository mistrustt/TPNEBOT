from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import delete, exists
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from typing import List, Optional, Tuple
from decimal import Decimal
import hashlib
import time
import secrets
import os
import aiohttp
from ..models import (
    Transaction,
    CryptoAsset,
    CryptoPrice,
    Wallet,
    Item,
    ItemType,
    ItemCategory,
    ItemRarity,
    ShopItem,
    ShopPurchaseLog,
    ItemCooldown,
    ActiveEffect,
    TradeLog,
    Bounty,
)
from datetime import timedelta, date
import discord
import uuid
import logging
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger("discord.client")


class InventoryMixin(BaseManager):
    async def add_crypto_asset(
        self, user_id: int, symbol: str, amount: Decimal, purchase_price: Decimal
    ):
        """Add a new crypto asset or update existing one for a user."""

        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    result = await session.execute(
                        select(CryptoAsset)
                        .where(
                            CryptoAsset.user_id == user_id,
                            CryptoAsset.symbol == symbol.upper(),
                        )
                        .order_by(CryptoAsset.purchase_date.desc())
                    )
                    assets = result.scalars().all()

                    # Handle duplicate entries - keep newest, delete oldest
                    if len(assets) > 1:
                        asset = assets[0]  # Newest (due to desc order)
                        for old_asset in assets[1:]:
                            await session.delete(old_asset)
                    elif len(assets) == 1:
                        asset = assets[0]
                    else:
                        asset = None

                    if asset:
                        new_amount = asset.amount + amount
                        if new_amount < 0:
                            raise ValueError("Cannot reduce asset below 0")

                        if amount > 0:
                            total_value = (asset.amount * asset.purchase_price) + (
                                amount * purchase_price
                            )
                            asset.purchase_price = total_value / new_amount
                        asset.amount = new_amount
                    else:
                        if amount < 0:
                            raise ValueError("Cannot create asset with negative amount")
                        asset = CryptoAsset(
                            user_id=user_id,
                            symbol=symbol.upper(),
                            amount=amount,
                            purchase_price=purchase_price,
                        )
                        session.add(asset)

                    await session.commit()
                    return asset

        except SQLAlchemyError as e:
            logging.error(f"Database error adding crypto asset: {str(e)}")
            raise

    async def get_crypto_assets(self, user_id: int) -> List[CryptoAsset]:
        """Get all crypto assets for a user."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CryptoAsset).where(CryptoAsset.user_id == user_id)
            )
            return result.scalars().all()

    async def get_crypto_asset(self, user_id: int, symbol: str) -> CryptoAsset:
        """Get specific crypto asset for a user."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CryptoAsset).where(
                    CryptoAsset.user_id == user_id, CryptoAsset.symbol == symbol.upper()
                )
            )
            return result.scalar_one_or_none()

    async def update_crypto_amount(self, user_id: int, symbol: str, amount: Decimal):
        """Update amount of a crypto asset."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(CryptoAsset).where(
                        CryptoAsset.user_id == user_id,
                        CryptoAsset.symbol == symbol.upper(),
                    )
                )
                asset = result.scalar_one_or_none()

                if not asset:
                    raise ValueError(f"No {symbol} asset found for user")

                if amount < 0:
                    if abs(amount) > asset.amount:
                        raise ValueError("Insufficient crypto balance")
                    asset.amount += amount
                else:
                    asset.amount += amount

                await session.commit()
                return asset

    async def transfer_crypto_asset(
        self,
        sender_user_id: int,
        receiver_user_id: int,
        symbol: str,
        amount: Decimal,
        description: str = "Crypto transfer",
    ):
        """Transfer crypto assets from one user to another."""

        await self.ensure_user_identity(sender_user_id)
        await self.ensure_user_identity(receiver_user_id)
        sender_user_id = self.hash_user_id(sender_user_id)
        receiver_user_id = self.hash_user_id(receiver_user_id)
        symbol = symbol.upper()
        amount = amount.quantize(Decimal("0.00000000"), ROUND_HALF_UP)

        if amount <= 0:
            raise ValueError("Transfer amount must be positive.")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Get sender and receiver crypto assets
                sender_result = await session.execute(
                    select(CryptoAsset).where(
                        CryptoAsset.user_id == sender_user_id,
                        CryptoAsset.symbol == symbol,
                    )
                )
                sender_asset = sender_result.scalar_one_or_none()

                receiver_result = await session.execute(
                    select(CryptoAsset).where(
                        CryptoAsset.user_id == receiver_user_id,
                        CryptoAsset.symbol == symbol,
                    )
                )
                receiver_asset = receiver_result.scalar_one_or_none()

                if not sender_asset:
                    raise ValueError(f"No {symbol} asset found for sender")
                if sender_asset.amount < amount:
                    raise ValueError("Insufficient crypto balance")

                # Create receiver asset if doesn't exist
                if not receiver_asset:
                    receiver_asset = CryptoAsset(
                        user_id=receiver_user_id,
                        symbol=symbol,
                        amount=Decimal("0"),
                    )
                    session.add(receiver_asset)
                    await session.flush()

                # Transfer amount
                sender_asset.amount -= amount
                receiver_asset.amount += amount

                # Record transaction
                txid = str(uuid.uuid4())
                session.add(
                    Transaction(
                        id=txid,
                        from_user_id=sender_user_id,
                        to_user_id=receiver_user_id,
                        amount=amount,
                        description=description,
                        timestamp=discord.utils.utcnow(),
                    )
                )

            await self.update_supply()
        return txid

    async def delete_crypto_asset(self, user_id: int, symbol: str):
        """Delete a crypto asset entry."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(CryptoAsset).where(
                        CryptoAsset.user_id == user_id,
                        CryptoAsset.symbol == symbol.upper(),
                    )
                )
                await session.commit()

    async def get_total_crypto_value(self, user_id: int) -> Decimal:
        """Get total value of all crypto assets at purchase price."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.sum(CryptoAsset.amount * CryptoAsset.purchase_price)).where(
                    CryptoAsset.user_id == user_id
                )
            )
            total = result.scalar_one_or_none()
            return total if total else Decimal("0")

    async def set_crypto_price(self, symbol: str, price: Decimal):
        """Set the price of a crypto asset."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(CryptoPrice).where(CryptoPrice.symbol == symbol.upper())
                )
                crypto_price = result.scalar_one_or_none()

                if crypto_price:
                    crypto_price.price = price
                else:
                    crypto_price = CryptoPrice(symbol=symbol.upper(), price=price)
                    session.add(crypto_price)
                await session.commit()

    async def get_crypto_price(self, symbol: str) -> Decimal:
        """Get the price of a crypto asset using FreeCryptoAPI."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CryptoPrice).where(CryptoPrice.symbol == symbol.upper())
            )
            price_record = result.scalar_one_or_none()

            now = discord.utils.utcnow()
            if (
                price_record
                and price_record.timestamp
                and price_record.timestamp >= now - timedelta(minutes=15)
            ):
                return price_record.price

            try:
                async with aiohttp.ClientSession() as api_session:
                    params = {"symbol": symbol.upper()}
                    headers = {
                        "Authorization": f"Bearer {os.getenv('FREECRYPTOAPI_API_KEY')}"
                    }

                    async with api_session.get(
                        "https://api.freecryptoapi.com/v1/getData",
                        params=params,
                        headers=headers,
                    ) as response:
                        if response.status == 429:
                            if price_record:
                                return price_record.price
                            raise Exception("Rate limit exceeded")

                        data = await response.json()
                        if data.get("status") != "success" or not data.get("symbols"):
                            raise Exception("Invalid API response")

                        crypto_data = data["symbols"][0]
                        price = Decimal(str(crypto_data["last"]))

                        if price_record:
                            price_record.price = price
                            price_record.timestamp = discord.utils.utcnow()
                        else:
                            new_record = CryptoPrice(
                                symbol=symbol.upper(),
                                price=price,
                                timestamp=discord.utils.utcnow(),
                            )
                            session.add(new_record)

                        await session.commit()
                        return price

            except Exception as e:
                logging.error(f"Error fetching crypto price: {str(e)}")

                if price_record:
                    return price_record.price
                return None

    async def get_user_inventory(self, user_id: int) -> List[Item]:
        """
        Retrieve all items owned by a user.

        Args:
            user_id: The Discord ID of the user

        Returns:
            List of Item objects in the user's inventory
        """

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Item).where(Item.user_id == user_id))
            return result.scalars().all()

    async def get_user_inventory_grouped(self, user_id: int) -> List[dict]:
        """Return inventory items grouped by name with aggregated quantity."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(
                    Item.name,
                    Item.description,
                    Item.item_type,
                    Item.category,
                    Item.rarity,
                    Item.targetable,
                    func.min(Item.id).label("id"),
                    func.count(Item.id).label("qty"),
                )
                .where(Item.user_id == user_id)
                .group_by(
                    Item.name,
                    Item.description,
                    Item.item_type,
                    Item.category,
                    Item.rarity,
                    Item.targetable,
                )
            )
            rows = result.all()
            return [
                {
                    "name": r[0],
                    "description": r[1],
                    "item_type": r[2].value if r[2] else None,
                    "category": r[3].value if r[3] else None,
                    "rarity": r[4].value if r[4] else None,
                    "targetable": bool(r[5]),
                    "id": r[6],
                    "quantity": r[7],
                }
                for r in rows
            ]

    async def get_user_item(self, user_id: int, item_id: int) -> Item:
        """
        Retrieve a specific item from user's inventory.

        Args:
            user_id: The Discord ID of the user
            item_id: The ID of the item

        Returns:
            Item object if found, None otherwise
        """

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.id == item_id)
            )
            return result.scalar_one_or_none()

    async def get_user_items_by_name(self, user_id: int, item_name: str) -> List[Item]:
        """
        Retrieve all items with a specific name from user's inventory.

        Args:
            user_id: The Discord ID of the user
            item_name: The name of the items to retrieve

        Returns:
            List of matching Item objects
        """

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.name == item_name)
            )
            return result.scalars().all()

    async def get_user_items_by_type(
        self, user_id: int, item_type: ItemType
    ) -> List[Item]:
        """
        Retrieve all items of a specific type from user's inventory.

        Args:
            user_id: The Discord ID of the user
            item_type: The ItemType to filter by

        Returns:
            List of matching Item objects
        """

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.item_type == item_type)
            )
            return result.scalars().all()

    async def transfer_item(
        self, from_user_id: int, to_user_id: int, item_id: int, quantity: int = 1
    ) -> bool:
        """Transfer items from one user to another."""

        await self.ensure_user_identity(from_user_id)
        await self.ensure_user_identity(to_user_id)
        from_user_id = self.hash_user_id(from_user_id)
        to_user_id = self.hash_user_id(to_user_id)

        if quantity <= 0:
            raise ValueError("Transfer quantity must be positive")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == from_user_id, Item.id == item_id)
                )
                source_item = result.scalar_one_or_none()

                if not source_item:
                    raise ValueError(
                        f"Item with ID {item_id} not found in user {from_user_id}'s inventory"
                    )

                if quantity > source_item.quantity:
                    raise ValueError(
                        f"Not enough items to transfer (have {source_item.quantity}, need {quantity})"
                    )

                if quantity == source_item.quantity:
                    source_item.user_id = to_user_id
                else:
                    source_item.quantity -= quantity
                    for _ in range(quantity):
                        new_item = Item(
                            user_id=to_user_id,
                            name=source_item.name,
                            serial_number=await self.generate_item_serial_number(),
                            description=source_item.description,
                            quantity=1,
                            item_type=source_item.item_type,
                            category=source_item.category,
                            rarity=source_item.rarity,
                            effect=source_item.effect,
                            effect_value=source_item.effect_value,
                            effect_duration=source_item.effect_duration,
                            cooldown_seconds=source_item.cooldown_seconds,
                            targetable=source_item.targetable,
                            daily_limit=source_item.daily_limit,
                            global_daily_limit=source_item.global_daily_limit,
                            tradable=source_item.tradable,
                            durability=source_item.durability,
                            max_uses=source_item.max_uses,
                        )
                        session.add(new_item)

                await session.commit()
                return True

    async def update_user_item(self, user_id: int, item_id: int, **kwargs) -> bool:
        """
        Update properties of an item in a user's inventory.

        Args:
            user_id: The Discord ID of the item owner
            item_id: The ID of the item to update
            **kwargs: The properties to update (name, description, etc.)

        Returns:
            True if update succeeded, False otherwise
        """

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == user_id, Item.id == item_id)
                )
                item = result.scalar_one_or_none()

                if not item:
                    return False

                allowed_props = [
                    "name",
                    "description",
                    "quantity",
                    "item_type",
                    "category",
                    "rarity",
                    "targetable",
                    "daily_limit",
                    "global_daily_limit",
                    "tradable",
                    "durability",
                    "max_uses",
                ]
                for prop, value in kwargs.items():
                    if prop in allowed_props and hasattr(item, prop):
                        setattr(item, prop, value)

                await session.commit()
                return True

    async def remove_user_item(
        self, user_id: int, item_id: int, quantity: int = None
    ) -> bool:
        """
        Remove an item from a user's inventory.

        Args:
            user_id: The Discord ID of the item owner
            item_id: The ID of the item to remove
            quantity: The quantity to remove (if None, removes all)

        Returns:
            True if removal succeeded, False otherwise
        """

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == user_id, Item.id == item_id)
                )
                item = result.scalar_one_or_none()

                if not item:
                    return False

                if quantity is None or quantity >= item.quantity:
                    await session.delete(item)
                else:
                    item.quantity -= quantity

                await session.commit()
                return True

    async def generate_item_serial_number(self) -> str:
        """
        Generate a unique serial number for a new item.

        Returns:
            A unique serial number string
        """
        timestamp = str(int(time.time() * 1000))
        random_number = str(secrets.randbelow(90000) + 10000)
        raw_serial = timestamp + random_number
        hash_object = hashlib.sha256(raw_serial.encode("utf-8"))
        hashed_serial = hash_object.hexdigest()
        serial_number = f"{hashed_serial[:8]}-{hashed_serial[8:12]}-{hashed_serial[12:16]}-{hashed_serial[16:20]}-{hashed_serial[20:32]}"
        return serial_number

    async def create_user_item(
        self,
        user_id: int,
        name: str,
        description: str,
        quantity: int = 1,
        item_type: ItemType = ItemType.COLLECTIBLE,
        category: ItemCategory = ItemCategory.COLLECTIBLE,
        rarity: ItemRarity = ItemRarity.COMMON,
        effect: str = None,
        effect_value: int = None,
        effect_duration: int = None,
        cooldown_seconds: int = None,
        targetable: bool = False,
        daily_limit: int = None,
        global_daily_limit: int = None,
        tradable: bool = True,
        durability: int = None,
        max_uses: int = None,
    ) -> Item:
        """
        Create a new item directly in a user's inventory (not from shop).

        Args:
            user_id: The Discord ID of the user
            name: Name of the item
            description: Description of the item
            quantity: Quantity to create (default: 1)
            item_type: Type of the item (default: COLLECTIBLE)

        Returns:
            The created Item object
        """

        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            serial_number = await self.generate_item_serial_number()
            item = Item(
                user_id=user_id,
                name=name,
                serial_number=serial_number,
                description=description,
                quantity=quantity,
                item_type=item_type,
                category=category,
                rarity=rarity,
                effect=effect,
                effect_value=effect_value,
                effect_duration=effect_duration,
                cooldown_seconds=cooldown_seconds,
                targetable=targetable,
                daily_limit=daily_limit,
                global_daily_limit=global_daily_limit,
                tradable=tradable,
                durability=durability,
                max_uses=max_uses,
            )
            session.add(item)
            await session.commit()
            return item

    async def add_item_to_inventory(
        self,
        user_id: int,
        name: str,
        description: str = None,
        quantity: int = 1,
        item_type: ItemType = ItemType.COLLECTIBLE,
        category: ItemCategory = ItemCategory.COLLECTIBLE,
        rarity: ItemRarity = ItemRarity.COMMON,
        effect: str = None,
        effect_value: int = None,
        effect_duration: int = None,
        cooldown_seconds: int = None,
        targetable: bool = False,
        daily_limit: int = None,
        global_daily_limit: int = None,
        tradable: bool = True,
        durability: int = None,
        max_uses: int = None,
    ) -> Item:
        """
        Alias for create_user_item that mirrors the legacy give-item signature.
        """

        return await self.create_user_item(
            user_id=user_id,
            name=name,
            description=description,
            quantity=quantity,
            item_type=item_type,
            category=category,
            rarity=rarity,
            effect=effect,
            effect_value=effect_value,
            effect_duration=effect_duration,
            cooldown_seconds=cooldown_seconds,
            targetable=targetable,
            daily_limit=daily_limit,
            global_daily_limit=global_daily_limit,
            tradable=tradable,
            durability=durability,
            max_uses=max_uses,
        )

    async def merge_duplicate_items(self, user_id: int) -> int:
        """
        Consolidate duplicate items in a user's inventory.
        Items with the same name will be merged.

        Args:
            user_id: The Discord ID of the user

        Returns:
            Number of items merged
        """

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == user_id)
                )
                items = result.scalars().all()

                item_groups = {}
                for item in items:
                    key = item.name
                    if key not in item_groups:
                        item_groups[key] = []
                    item_groups[key].append(item)

                merged_count = 0

                for group in item_groups.values():
                    if len(group) > 1:
                        primary_item = group[0]
                        for other_item in group[1:]:
                            primary_item.quantity += other_item.quantity
                            await session.delete(other_item)
                            merged_count += 1

                await session.commit()
                return merged_count

    async def update_shop_item_quantity(
        self, item_id: int, quantity_change: int
    ) -> bool:
        """
        Adjust the quantity of a shop item by a given change amount (can be positive or negative).
        Returns True if the change is applied successfully; otherwise False (for example, when stock would be negative).
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ShopItem).where(ShopItem.id == item_id)
                )
                shop_item = result.scalar_one_or_none()
                if not shop_item:
                    return False
                if shop_item.unlimited:
                    return True

                new_quantity = shop_item.quantity + quantity_change
                if new_quantity < 0:
                    return False
                shop_item.quantity = new_quantity
            await session.commit()
            return True

    async def update_shop_item(
        self, item_id: int, **kwargs
    ) -> Optional[ShopItem]:
        """
        Update arbitrary fields on a shop item.
        Allowed fields: name, description, price, quantity, unlimited, item_type,
        category, rarity, effect, effect_value, effect_duration, cooldown_seconds,
        targetable, daily_limit, global_daily_limit, tradable.
        Returns the updated ShopItem or None if not found.
        """

        allowed = {
            "name",
            "description",
            "price",
            "quantity",
            "unlimited",
            "item_type",
            "category",
            "rarity",
            "effect",
            "effect_value",
            "effect_duration",
            "cooldown_seconds",
            "targetable",
            "daily_limit",
            "global_daily_limit",
            "tradable",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return await self.get_shop_item_by_id(item_id)

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ShopItem).where(ShopItem.id == item_id)
                )
                shop_item = result.scalar_one_or_none()
                if not shop_item:
                    return None
                for key, value in updates.items():
                    setattr(shop_item, key, value)
            await session.commit()
            return shop_item

    async def remove_shop_item(self, item_id: int) -> bool:
        """
        Remove a shop item completely from the shop by its ID.
        Returns True if an item was removed, otherwise False.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(ShopItem).where(ShopItem.id == item_id)
                )
                if result.rowcount and result.rowcount > 0:
                    await session.commit()
                    return True
                return False

    async def list_shop_items(self) -> List[ShopItem]:
        """
        Retrieve all available shop items that have a positive stock level.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ShopItem).where(
                    (ShopItem.quantity > 0) | (ShopItem.unlimited == True)
                )
            )
            return result.scalars().all()

    async def get_shop_item_by_id(self, item_id: int) -> ShopItem:
        """
        Retrieve a specific shop item using its unique ID.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ShopItem).where(ShopItem.id == item_id)
            )
            return result.scalar_one_or_none()

    async def purchase_shop_item(
        self, user_id: int, shop_item_id: int, quantity: int = 1
    ) -> dict:
        """
        Purchase a shop item by:
          - Verifying available stock and daily purchase limits.
          - Checking if the buyer’s wallet has enough funds.
          - Deducting the total cost from the user’s wallet.
          - Reducing the shop’s stock.
          - Adding the purchased item to the user’s inventory.
          - Recording the purchase for daily limit tracking.
        Returns a dict containing purchase details.
        """

        raw_user_id = user_id
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ShopItem).where(ShopItem.id == shop_item_id)
                )
                shop_item = result.scalar_one_or_none()
                if not shop_item:
                    raise ValueError("Shop item not found.")
                if quantity <= 0:
                    raise ValueError("Quantity must be positive.")
                if not shop_item.unlimited and shop_item.quantity < quantity:
                    raise ValueError("Insufficient stock available.")

                # Enforce daily purchase limits
                limit_check = await self.check_purchase_limits(
                    raw_user_id, shop_item, quantity
                )
                if not limit_check["allowed"]:
                    raise ValueError(limit_check["reason"])

                total_cost = Decimal(str(shop_item.price * quantity))

                wallet_id = await self.get_wallet_id_for_user(raw_user_id)
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError(f"Wallet {wallet_id} not found.")
                wallet_balance = await self.get_wallet_balance(wallet_id)
                if wallet_balance < total_cost:
                    raise ValueError("Insufficient funds.")

                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                await self.process_treasury_transaction(
                    wallet_id,
                    -total_cost,
                    f"Purchased {quantity}x {shop_item.name}",
                    "standard",
                )

                if not shop_item.unlimited:
                    shop_item.quantity -= quantity

                for _ in range(quantity):
                    new_inventory_item = Item(
                        user_id=user_id,
                        name=shop_item.name,
                        serial_number=await self.generate_item_serial_number(),
                        description=shop_item.description,
                        quantity=1,
                        item_type=shop_item.item_type,
                        category=shop_item.category,
                        rarity=shop_item.rarity,
                        effect=shop_item.effect,
                        effect_value=shop_item.effect_value,
                        effect_duration=shop_item.effect_duration,
                        cooldown_seconds=shop_item.cooldown_seconds,
                        targetable=shop_item.targetable,
                        daily_limit=shop_item.daily_limit,
                        global_daily_limit=shop_item.global_daily_limit,
                        tradable=shop_item.tradable,
                    )
                    session.add(new_inventory_item)

                # Record purchase for daily limits
                session.add(
                    ShopPurchaseLog(
                        user_id=user_id,
                        shop_item_id=shop_item.id,
                        quantity=quantity,
                        purchase_date=date.today(),
                    )
                )
            await session.commit()
            return {
                "success": True,
                "item_name": shop_item.name,
                "quantity": quantity,
                "cost": total_cost,
            }

    async def use_inventory_item(self, user_id: int, item_id: int) -> str:
        """
        Use an item from the user's inventory according to its type.
          - For a CONSUMABLE, reduce its quantity by one (removing it if depleted).
          - For a REDEEMABLE, remove it after use.
          - For a COLLECTIBLE, simply showcase the item.
        Returns a message describing the result.
        """

        raw_user_id = user_id
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == user_id, Item.id == item_id)
                )
                item = result.scalar_one_or_none()
                if not item:
                    raise ValueError("Item not found in inventory.")

                if item.item_type == ItemType.CONSUMABLE:
                    message = f"You have consumed one {item.name}."
                    if item.effect == "currency" and item.effect_value:
                        wallet_id = await self.get_wallet_id_for_user(raw_user_id)
                        await self.process_treasury_transaction(
                            wallet_id,
                            Decimal(item.effect_value),
                            f"Used {item.name}",
                            "standard",
                        )
                    item.quantity -= 1
                    if item.quantity <= 0:
                        await session.delete(item)
                    return message
                elif item.item_type == ItemType.REDEEMABLE:
                    message = f"You have redeemed {item.name} and received its benefits."
                    if item.effect == "currency" and item.effect_value:
                        wallet_id = await self.get_wallet_id_for_user(raw_user_id)
                        await self.process_treasury_transaction(
                            wallet_id,
                            Decimal(item.effect_value),
                            f"Redeemed {item.name}",
                            "standard",
                        )
                    await session.delete(item)
                    return message
                elif item.item_type == ItemType.COLLECTIBLE:
                    return f"You are now showcasing your collectible {item.name}."
                else:
                    raise ValueError("Unknown item type.")

    async def set_item_cooldown(
        self, user_id: int, item_name: str, cooldown_seconds: int
    ) -> None:
        """Set a cooldown for a user on a specific item."""

        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        expiry = discord.utils.utcnow() + timedelta(seconds=cooldown_seconds)

        async with self.async_sessionmaker() as session:
            # Check if cooldown already exists
            stmt = select(ItemCooldown).where(
                ItemCooldown.user_id == user_id, ItemCooldown.item_name == item_name
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                existing.cooldown_expiry = expiry
            else:
                cooldown = ItemCooldown(
                    user_id=user_id, item_name=item_name, cooldown_expiry=expiry
                )
                session.add(cooldown)

            await session.commit()

    async def get_item_cooldown(self, user_id: int, item_name: str) -> int:
        """
        Get remaining cooldown seconds for a user's item.
        Returns 0 if no cooldown or if expired.
        """

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            stmt = select(ItemCooldown).where(
                ItemCooldown.user_id == user_id, ItemCooldown.item_name == item_name
            )
            cooldown = (await session.execute(stmt)).scalar_one_or_none()
            if not cooldown:
                return 0
            now = discord.utils.utcnow()
            if cooldown.cooldown_expiry <= now:
                # Cooldown expired, clean it up
                await session.delete(cooldown)
                await session.commit()
                return 0
            remaining = (cooldown.cooldown_expiry - now).total_seconds()
            return int(remaining)

    async def is_item_on_cooldown(self, user_id: int, item_name: str) -> bool:
        """Check if an item is on cooldown for a user."""

        raw_user_id = user_id
        user_id = self.hash_user_id(user_id)
        remaining = await self.get_item_cooldown(raw_user_id, item_name)
        return remaining > 0

    async def clear_item_cooldown(self, user_id: int, item_name: str) -> bool:
        """Clear a cooldown for a user's item. Returns True if cooldown was cleared."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(ItemCooldown).where(
                    ItemCooldown.user_id == user_id, ItemCooldown.item_name == item_name
                )
                cooldown = (await session.execute(stmt)).scalar_one_or_none()
                if cooldown:
                    await session.delete(cooldown)
                    await session.commit()
                    return True
            return False

    async def create_active_effect(
        self,
        user_id: int,
        effect_type: str,
        effect_value: Decimal,
        duration_seconds: int,
        source_item_name: str,
    ) -> ActiveEffect:
        """Create a timed effect for a user."""

        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                expires_at = discord.utils.utcnow() + timedelta(
                    seconds=duration_seconds
                )
                effect = ActiveEffect(
                    user_id=user_id,
                    effect_type=effect_type,
                    effect_value=effect_value,
                    source_item_name=source_item_name,
                    expires_at=expires_at,
                )
                session.add(effect)
            await session.commit()
            return effect

    async def get_user_active_effects(self, user_id: int) -> List[ActiveEffect]:
        """Get all non-expired effects for a user."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            now = discord.utils.utcnow()
            stmt = (
                select(ActiveEffect)
                .where(ActiveEffect.user_id == user_id, ActiveEffect.expires_at > now)
                .order_by(ActiveEffect.expires_at)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_active_effects_by_type(
        self, user_id: int, effect_type: str
    ) -> List[ActiveEffect]:
        """Get all non-expired effects of a specific type for a user."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            now = discord.utils.utcnow()
            stmt = (
                select(ActiveEffect)
                .where(
                    ActiveEffect.user_id == user_id,
                    ActiveEffect.effect_type == effect_type,
                    ActiveEffect.expires_at > now,
                )
                .order_by(ActiveEffect.expires_at)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def has_active_effect(self, user_id: int, effect_type: str) -> bool:
        """Return True if the user already has an active non-expired effect of this type."""

        effects = await self.get_active_effects_by_type(user_id, effect_type)
        return bool(effects)

    async def cleanup_expired_effects(self) -> int:
        """Remove all expired effects. Returns count of removed effects."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                now = discord.utils.utcnow()
                stmt = delete(ActiveEffect).where(ActiveEffect.expires_at <= now)
                result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def get_effect_multiplier(self, user_id: int, effect_type: str) -> Decimal:
        """
        Get the combined multiplier value for a specific effect type.
        Returns Decimal('1.0') if no active effects.
        For multipliers, returns the product of all active multipliers.
        """

        raw_user_id = user_id
        user_id = self.hash_user_id(user_id)
        effects = await self.get_active_effects_by_type(raw_user_id, effect_type)
        if not effects:
            return Decimal("1.0")
        combined = Decimal("1.0")
        for effect in effects:
            combined *= effect.effect_value
        return combined

    async def get_earning_multiplier(self, user_id: int) -> Decimal:
        """Return the combined active earning multiplier for a user.

        Positive earning_boost effects and negative earning_debuff effects are
        multiplied together, so a debuff value of 0.8 reduces earnings by 20%.
        """

        boost = await self.get_effect_multiplier(user_id, "earning_boost")
        debuff = await self.get_effect_multiplier(user_id, "earning_debuff")
        return max(Decimal("0.1"), min(Decimal("5.0"), boost * debuff))

    async def get_gambling_multiplier(self, user_id: int) -> Decimal:
        """Return the combined active gambling win multiplier for a user."""
        return await self.get_effect_multiplier(user_id, "gambling_multiplier")

    async def get_luck_multiplier(self, user_id: int) -> Decimal:
        """Return the combined active luck multiplier for a user."""
        return await self.get_effect_multiplier(user_id, "luck_boost")

    async def get_cooldown_multiplier(self, user_id: int) -> Decimal:
        """Return the combined active cooldown multiplier for a user."""
        # Values below 1.0 reduce cooldowns (e.g., 0.8 = 20% faster).
        # Values above 1.0 increase cooldowns (e.g., cooldown_increase debuffs).
        # Guard against broken values by clamping to a safe range.
        reduction = await self.get_effect_multiplier(user_id, "cooldown_reduction")
        increase = await self.get_effect_multiplier(user_id, "cooldown_increase")
        multiplier = reduction * increase
        return max(Decimal("0.25"), min(Decimal("2.0"), multiplier))

    async def remove_active_effect(self, effect_id: int) -> bool:
        """Remove a specific active effect by ID. Returns True if removed."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(ActiveEffect).where(ActiveEffect.id == effect_id)
                effect = (await session.execute(stmt)).scalar_one_or_none()
                if effect:
                    await session.delete(effect)
                    await session.commit()
                    return True
            return False

    async def create_trade_request(
        self, from_user_id: int, to_user_id: int, item_id: int, quantity: int = 1
    ) -> TradeLog:
        """
        Create a pending trade request.
        Validates that the sender owns the item.
        """

        await self.ensure_user_identity(from_user_id)
        await self.ensure_user_identity(to_user_id)
        from_user_id = self.hash_user_id(from_user_id)
        to_user_id = self.hash_user_id(to_user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Verify ownership
                stmt = select(Item).where(Item.id == item_id)
                item = (await session.execute(stmt)).scalar_one_or_none()
                if not item:
                    raise ValueError("Item not found.")
                if item.user_id != from_user_id:
                    raise ValueError("You don't own this item.")
                if not item.tradable:
                    raise ValueError("This item is not tradable.")
                if item.quantity < quantity:
                    raise ValueError(
                        f"Insufficient quantity. You have {item.quantity}, need {quantity}."
                    )

                trade = TradeLog(
                    from_user_id=from_user_id,
                    to_user_id=to_user_id,
                    item_id=item_id,
                    item_name=item.name,
                    quantity=quantity,
                    status="pending",
                )
                session.add(trade)
            await session.commit()
            return trade

    async def accept_trade_request(self, trade_id: int) -> TradeLog:
        """
        Accept a pending trade request.
        Transfers the item to the recipient and marks the trade as completed.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(TradeLog).where(TradeLog.id == trade_id)
                trade = (await session.execute(stmt)).scalar_one_or_none()
                if not trade:
                    raise ValueError("Trade not found.")
                if trade.status != "pending":
                    raise ValueError(f"Trade is already {trade.status}.")

                # Get the item
                item_stmt = select(Item).where(Item.id == trade.item_id)
                item = (await session.execute(item_stmt)).scalar_one_or_none()
                if not item:
                    raise ValueError("Item no longer exists.")
                if item.user_id != trade.from_user_id:
                    raise ValueError("Sender no longer owns this item.")
                if item.quantity < trade.quantity:
                    raise ValueError("Insufficient item quantity.")

                # Handle quantity transfer
                if item.quantity == trade.quantity:
                    # Transfer full ownership
                    item.user_id = trade.to_user_id
                else:
                    # Split the item - create new item for recipient
                    item.quantity -= trade.quantity
                    new_item = Item(
                        user_id=trade.to_user_id,
                        name=item.name,
                        serial_number=f"{item.serial_number}-{trade.to_user_id}",
                        description=item.description,
                        quantity=trade.quantity,
                        item_type=item.item_type,
                        category=item.category,
                        rarity=item.rarity,
                        effect=item.effect,
                        effect_value=item.effect_value,
                        effect_duration=item.effect_duration,
                        cooldown_seconds=item.cooldown_seconds,
                        targetable=item.targetable,
                        daily_limit=item.daily_limit,
                        global_daily_limit=item.global_daily_limit,
                        tradable=item.tradable,
                        durability=item.durability,
                        max_uses=item.max_uses,
                    )
                    session.add(new_item)

                # Update trade status
                trade.status = "completed"
                trade.completed_at = discord.utils.utcnow()
            await session.commit()
            return trade

    async def decline_trade_request(self, trade_id: int) -> TradeLog:
        """Decline a pending trade request."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(TradeLog).where(TradeLog.id == trade_id)
                trade = (await session.execute(stmt)).scalar_one_or_none()
                if not trade:
                    raise ValueError("Trade not found.")
                if trade.status != "pending":
                    raise ValueError(f"Trade is already {trade.status}.")
                trade.status = "cancelled"
            await session.commit()
            return trade

    async def get_pending_trades(self, user_id: int) -> List[TradeLog]:
        """Get all pending trades where the user is either sender or recipient."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            stmt = (
                select(TradeLog)
                .where(
                    TradeLog.status == "pending",
                    (TradeLog.to_user_id == user_id)
                    | (TradeLog.from_user_id == user_id),
                )
                .order_by(TradeLog.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_trade_by_id(self, trade_id: int) -> Optional[TradeLog]:
        """Get a trade by its ID."""
        async with self.async_sessionmaker() as session:
            stmt = select(TradeLog).where(TradeLog.id == trade_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_pending_trades_for_user(self, user_id: int) -> List[TradeLog]:
        """Get all pending trades where the user is the recipient."""

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            stmt = (
                select(TradeLog)
                .where(TradeLog.to_user_id == user_id, TradeLog.status == "pending")
                .order_by(TradeLog.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def use_inventory_item_with_effects(self, user_id: int, item_id: int) -> dict:
        """
        Enhanced version of use_inventory_item that handles cooldowns,
        effect types, and effect durations.

        Returns a dict with:
        - message: str - result message
        - effect_type: str (optional)
        - effect_applied: bool
        - cooldown_seconds: int (optional)
        """

        raw_user_id = user_id
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == user_id, Item.id == item_id)
                )
                item = result.scalar_one_or_none()
                if not item:
                    raise ValueError("Item not found in inventory.")

                # Check cooldown
                if item.cooldown_seconds:
                    remaining = await self.get_item_cooldown(raw_user_id, item.name)
                    if remaining > 0:
                        raise ValueError(
                            f"This item is on cooldown. {remaining} seconds remaining."
                        )

                response = {"message": "", "effect_applied": False}

                # Apply effect based on type
                effect = item.effect
                effect_value = item.effect_value
                effect_duration = item.effect_duration

                if effect == "currency" and effect_value:
                    # Direct currency grant
                    wallet_id = await self.get_wallet_id_for_user(raw_user_id)
                    await self.process_treasury_transaction(
                        wallet_id, Decimal(effect_value), f"Used {item.name}", "standard"
                    )
                    response[
                        "message"
                    ] = f"You received {effect_value} coins from {item.name}!"
                    response["effect_applied"] = True

                elif effect in (
                    "gambling_multiplier",
                    "luck_boost",
                    "earning_boost",
                    "cooldown_reduction",
                    "rtp_boost",
                    "anti_rob",
                    "anti_bank_rob",
                ):
                    # Prevent stacking the same effect type on yourself
                    if await self.has_active_effect(raw_user_id, effect):
                        raise ValueError(
                            f"You already have an active `{effect}` effect. "
                            "Wait for it to expire before using this item again."
                        )

                    # Create timed effect
                    if effect_duration:
                        await self.create_active_effect(
                            user_id=raw_user_id,
                            effect_type=effect,
                            effect_value=Decimal(str(effect_value)),
                            duration_seconds=effect_duration,
                            source_item_name=item.name,
                        )
                        duration_mins = effect_duration // 60
                        duration_secs = effect_duration % 60
                        duration_str = (
                            f"{duration_mins}m {duration_secs}s"
                            if duration_mins
                            else f"{duration_secs}s"
                        )

                        effect_names = {
                            "gambling_multiplier": f"{effect_value}x gambling multiplier",
                            "luck_boost": f"{effect_value}x luck boost",
                            "earning_boost": f"{effect_value}x earning boost",
                            "cooldown_reduction": f"{effect_value}% cooldown reduction",
                            "rtp_boost": f"{effect_value}% RTP boost",
                            "anti_rob": "Anti-Rob aura",
                            "anti_bank_rob": "Anti-Bank-Rob aura",
                        }
                        response[
                            "message"
                        ] = f"Activated {effect_names.get(effect, effect)} for {duration_str}!"
                        response["effect_applied"] = True
                    else:
                        response[
                            "message"
                        ] = f"Used {item.name} but no duration was specified."

                elif item.item_type == ItemType.COLLECTIBLE:
                    response[
                        "message"
                    ] = f"You are showcasing your collectible {item.name}."
                else:
                    response["message"] = f"You used {item.name}."

                # Set cooldown if applicable
                if item.cooldown_seconds and item.item_type != ItemType.COLLECTIBLE:
                    await self.set_item_cooldown(
                        raw_user_id, item.name, item.cooldown_seconds
                    )
                    response["cooldown_seconds"] = item.cooldown_seconds

                # Handle quantity/durability/uses reduction
                if item.item_type in (
                    ItemType.CONSUMABLE,
                    ItemType.DEFENSIVE,
                    ItemType.OFFENSIVE,
                ):
                    await self._consume_item_use(session, item)
                elif item.item_type == ItemType.REDEEMABLE:
                    await session.delete(item)

                return response

    async def place_bounty(
        self, issuer_id: int, target_id: int, reward: Decimal
    ) -> Bounty:
        """Place (or increase) a bounty on a user."""

        raw_issuer_id = issuer_id
        raw_target_id = target_id
        await self.ensure_user_identity(issuer_id)
        await self.ensure_user_identity(target_id)
        issuer_id = self.hash_user_id(issuer_id)
        target_id = self.hash_user_id(target_id)
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet_id = await self.get_wallet_id_for_user(raw_issuer_id)
                wallet_balance = await self.get_wallet_balance(wallet_id)
                if wallet_balance < reward:
                    raise ValueError("Insufficient balance to place bounty.")

                await self.process_treasury_transaction(
                    wallet_id,
                    -reward,
                    f"Placed bounty on {raw_target_id}",
                    "high_value",
                )

                stmt = select(Bounty).where(
                    Bounty.target_id == target_id, Bounty.active == True
                )
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if existing:
                    existing.reward += reward
                    bounty = existing
                else:
                    bounty = Bounty(
                        target_id=target_id,
                        issuer_id=issuer_id,
                        reward=reward,
                        active=True,
                    )
                    session.add(bounty)

            await session.commit()
            return bounty

    async def user_has_bounty(self, target_id: int) -> bool:
        """
        Return True if there is at least one active bounty on target_id.
        Does NOT assume uniqueness—just looks for ANY active row.
        """

        target_id = self.hash_user_id(target_id)
        async with self.async_sessionmaker() as session:
            stmt = select(
                exists().where(Bounty.target_id == target_id, Bounty.active == True)
            )
            result = await session.execute(stmt)
            return result.scalar()

    async def get_bounty_amount(self, target_id: int) -> Decimal:
        """
        Returns the total sum of all active bounties for target_id.
        If there are none, returns Decimal('0').
        """

        target_id = self.hash_user_id(target_id)
        async with self.async_sessionmaker() as session:
            stmt = select(func.sum(Bounty.reward).label("total_reward")).where(
                Bounty.target_id == target_id, Bounty.active == True
            )
            result = await session.execute(stmt)
            total = result.scalar_one()
            return total if total is not None else Decimal("0")

    async def get_top_bounty_users(self, limit=10) -> List[Tuple[int, Decimal]]:
        """
        Returns a list of (target_id, total_reward) for active bounties,
        ordered descending, limited to `limit`.
        """
        async with self.async_sessionmaker() as session:
            stmt = (
                select(Bounty.target_id, func.sum(Bounty.reward).label("total_reward"))
                .where(Bounty.active == True)
                .group_by(Bounty.target_id)
                .order_by(func.sum(Bounty.reward).desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.all()
            resolved = []
            for target_id, total_reward in rows:
                raw_target_id = await self.resolve_user_hash(target_id)
                resolved.append((raw_target_id, total_reward))
            return resolved

    async def get_active_bounties(self) -> List[Bounty]:
        """Get all active bounties."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Bounty).where(Bounty.active == True))
            return result.scalars().all()

    async def claim_bounty(self, claimer_id: int, target_id: int) -> Bounty:
        """
        Claim and remove the active bounty on target_id.
        Returns the deleted Bounty instance (detached) so you can inspect its data.
        """

        raw_claimer_id = claimer_id
        raw_target_id = target_id
        await self.ensure_user_identity(claimer_id)
        await self.ensure_user_identity(target_id)
        claimer_id = self.hash_user_id(claimer_id)
        target_id = self.hash_user_id(target_id)
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(Bounty).where(
                    Bounty.target_id == target_id, Bounty.active == True
                )
                bounty = (await session.execute(stmt)).scalar_one_or_none()
                if not bounty:
                    raise ValueError("No active bounty on this user.")

                reward_amount = bounty.reward

                await session.execute(delete(Bounty).where(Bounty.id == bounty.id))

                claimer_wallet = await self.get_wallet_id_for_user(raw_claimer_id)
                await self.process_treasury_transaction(
                    claimer_wallet,
                    reward_amount,
                    f"Claimed bounty on {raw_target_id}",
                    "high_value",
                )

            await session.commit()

            bounty.reward = reward_amount
            bounty.active = False
            bounty.claimer_id = claimer_id
            return bounty

    # ------------------------------------------------------------------
    # Shop purchase limits
    # ------------------------------------------------------------------

    async def get_user_daily_purchase_count(
        self, user_id: int, shop_item_id: int, purchase_date: date = None
    ) -> int:
        """Return how many of a shop item a user has bought on a given date."""

        user_id = self.hash_user_id(user_id)
        if purchase_date is None:
            purchase_date = discord.utils.utcnow().date()
        async with self.async_sessionmaker() as session:
            stmt = select(
                func.coalesce(func.sum(ShopPurchaseLog.quantity), 0)
            ).where(
                ShopPurchaseLog.user_id == user_id,
                ShopPurchaseLog.shop_item_id == shop_item_id,
                ShopPurchaseLog.purchase_date == purchase_date,
            )
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def get_global_daily_purchase_count(
        self, shop_item_id: int, purchase_date: date = None
    ) -> int:
        """Return how many of a shop item have been bought server-wide today."""

        if purchase_date is None:
            purchase_date = discord.utils.utcnow().date()
        async with self.async_sessionmaker() as session:
            stmt = select(
                func.coalesce(func.sum(ShopPurchaseLog.quantity), 0)
            ).where(
                ShopPurchaseLog.shop_item_id == shop_item_id,
                ShopPurchaseLog.purchase_date == purchase_date,
            )
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def check_purchase_limits(
        self, user_id: int, shop_item: ShopItem, quantity: int
    ) -> dict:
        """
        Check whether a purchase would violate per-user or global daily limits.
        Returns {"allowed": bool, "reason": str | None}.
        """

        if quantity <= 0:
            return {"allowed": False, "reason": "Quantity must be positive."}

        today = discord.utils.utcnow().date()
        if shop_item.daily_limit is not None:
            bought = await self.get_user_daily_purchase_count(
                user_id, shop_item.id, today
            )
            if bought + quantity > shop_item.daily_limit:
                remaining = max(0, shop_item.daily_limit - bought)
                return {
                    "allowed": False,
                    "reason": (
                        f"You can only buy **{remaining}** more "
                        f"**{shop_item.name}** today."
                    ),
                }

        if shop_item.global_daily_limit is not None:
            bought = await self.get_global_daily_purchase_count(shop_item.id, today)
            if bought + quantity > shop_item.global_daily_limit:
                remaining = max(0, shop_item.global_daily_limit - bought)
                return {
                    "allowed": False,
                    "reason": (
                        f"Global daily stock for **{shop_item.name}** is "
                        f"nearly gone (**{remaining}** remaining today)."
                    ),
                }

        return {"allowed": True, "reason": None}

    # ------------------------------------------------------------------
    # Defensive robbery helpers
    # ------------------------------------------------------------------

    async def has_active_defensive_effect(self, user_id: int, effect_type: str) -> bool:
        """Return True if the user has an active effect of the given type."""

        effects = await self.get_active_effects_by_type(user_id, effect_type)
        return bool(effects)

    async def _consume_defensive_item(
        self, user_id: int, effect: str
    ) -> Optional[Item]:
        """
        Consume one defensive item with the matching effect.
        Returns the consumed item (or None if no matching item was available).
        """

        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item)
                    .where(
                        Item.user_id == user_id,
                        Item.item_type == ItemType.DEFENSIVE,
                        Item.effect == effect,
                        Item.quantity > 0,
                    )
                    .order_by(Item.id)
                    .limit(1)
                )
                item = result.scalar_one_or_none()
                if not item:
                    return None
                await self._consume_item_use(session, item)
            await session.commit()
            return item

    async def check_robbery_defense(
        self, target_id: int, robbery_type: str = "wallet"
    ) -> tuple[bool, str]:
        """
        Check whether a robbery against target_id is blocked.
        robbery_type may be "wallet", "bank", or "any".
        Returns (blocked: bool, message: str).
        """

        if robbery_type in ("wallet", "any"):
            if await self.has_active_defensive_effect(target_id, "anti_rob"):
                return True, "🛡️ Your target is protected by an **Anti-Rob** aura."

        if robbery_type in ("bank", "any"):
            if await self.has_active_defensive_effect(target_id, "anti_bank_rob"):
                return True, "🏦 Your target's bank is protected by an **Anti-Bank-Rob** aura."

        shield = await self._consume_defensive_item(target_id, "rob_shield")
        if shield:
            return True, "🛡️ Your target's **Rob Shield** shattered and blocked the robbery!"

        return False, ""

    async def get_robbery_debuff_multiplier(self, user_id: int) -> Decimal:
        """
        Return the combined robbery debuff multiplier for a user.
        Values below 1.0 reduce the payout a robber receives from this target.
        """

        effects = await self.get_active_effects_by_type(user_id, "robbery_debuff")
        if not effects:
            return Decimal("1.0")

        multiplier = Decimal("1.0")
        for effect in effects:
            val = effect.effect_value
            if val is not None and val > 0:
                # Debuff values are interpreted as a multiplier (e.g. 0.75 = -25%).
                multiplier *= min(val, Decimal("1.0"))
        return max(Decimal("0.1"), min(Decimal("1.0"), multiplier))

    # ------------------------------------------------------------------
    # Targeted/offensive item usage
    # ------------------------------------------------------------------

    async def use_targeted_item(
        self, user_id: int, item_id: int, target_user_id: int
    ) -> dict:
        """
        Use a targetable item on another user.
        Applies a negative/timed effect to the target and consumes the item.
        Returns a dict with result details.
        """

        raw_user_id = user_id
        raw_target_id = target_user_id
        await self.ensure_user_identity(user_id)
        await self.ensure_user_identity(target_user_id)
        user_id_h = self.hash_user_id(user_id)

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == user_id_h, Item.id == item_id)
                )
                item = result.scalar_one_or_none()
                if not item:
                    raise ValueError("Item not found in your inventory.")
                if not item.targetable:
                    raise ValueError(f"**{item.name}** is not targetable.")
                if item.cooldown_seconds:
                    remaining = await self.get_item_cooldown(raw_user_id, item.name)
                    if remaining > 0:
                        raise ValueError(
                            f"This item is on cooldown. {remaining} seconds remaining."
                        )

                effect = item.effect
                effect_value = item.effect_value
                effect_duration = item.effect_duration
                if not effect or not effect_duration:
                    raise ValueError("That item has no usable targeted effect.")

                supported_target_effects = {
                    "robbery_debuff",
                    "fee_increase",
                    "cooldown_increase",
                    "earning_debuff",
                    "luck_shield",
                }
                if effect not in supported_target_effects:
                    raise ValueError(f"**{item.name}** cannot be used on another user.")

                # Prevent stacking the same debuff on the target
                if await self.has_active_effect(raw_target_id, effect):
                    raise ValueError(
                        f"<@{raw_target_id}> already has an active `{effect}` effect. "
                        "Wait for it to expire before using this item on them again."
                    )

                # Apply effect to target
                await self.create_active_effect(
                    raw_target_id,
                    effect,
                    Decimal(str(effect_value)) if effect_value else Decimal("1.0"),
                    effect_duration,
                    item.name,
                )

                if item.cooldown_seconds:
                    await self.set_item_cooldown(
                        raw_user_id, item.name, item.cooldown_seconds
                    )

                await self._consume_item_use(session, item)

                duration_minutes = effect_duration // 60
                return {
                    "message": (
                        f"You used **{item.name}** on <@{raw_target_id}>. "
                        f"They now suffer `{effect}` for {duration_minutes}m."
                    ),
                    "target_id": raw_target_id,
                    "effect": effect,
                    "effect_value": effect_value,
                    "duration_seconds": effect_duration,
                }

    async def _consume_item_use(self, session, item: Item) -> None:
        """
        Reduce an item's uses/durability/quantity after it is used.
        Deletes the row if its quantity drops to 0.
        """

        depleted = False
        if item.max_uses is not None:
            item.max_uses -= 1
            if item.max_uses <= 0:
                depleted = True
                item.max_uses = None
        elif item.durability is not None:
            item.durability -= 1
            if item.durability <= 0:
                depleted = True
                item.durability = None

        if depleted:
            item.quantity -= 1
        elif item.item_type == ItemType.CONSUMABLE:
            item.quantity -= 1
        elif item.item_type in (ItemType.DEFENSIVE, ItemType.OFFENSIVE):
            # Offensive/defensive items without explicit uses/durability still
            # consume one quantity per use.
            item.quantity -= 1

        if item.quantity <= 0:
            await session.delete(item)

    # ------------------------------------------------------------------
    # Shop catalog helpers
    # ------------------------------------------------------------------

    async def get_shop_items_by_category(
        self, category: ItemCategory
    ) -> List[ShopItem]:
        """Return in-stock shop items in a specific category."""

        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ShopItem).where(
                    ShopItem.category == category,
                    (ShopItem.quantity > 0) | (ShopItem.unlimited == True),
                )
            )
            return result.scalars().all()

    async def get_shop_items_by_rarity(
        self, rarity: ItemRarity
    ) -> List[ShopItem]:
        """Return in-stock shop items of a specific rarity."""

        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ShopItem).where(
                    ShopItem.rarity == rarity,
                    (ShopItem.quantity > 0) | (ShopItem.unlimited == True),
                )
            )
            return result.scalars().all()

    async def add_shop_item(
        self,
        name: str,
        description: str,
        price: Decimal,
        quantity: int,
        item_type: ItemType = ItemType.COLLECTIBLE,
        category: ItemCategory = ItemCategory.COLLECTIBLE,
        rarity: ItemRarity = ItemRarity.COMMON,
        unlimited: bool = False,
        effect: str = None,
        effect_value: Decimal = None,
        effect_duration: int = None,
        cooldown_seconds: int = None,
        targetable: bool = False,
        daily_limit: int = None,
        global_daily_limit: int = None,
        tradable: bool = True,
    ) -> ShopItem:
        """Create and store a new shop item with full catalog metadata."""

        async with self.async_sessionmaker() as session:
            async with session.begin():
                new_shop_item = ShopItem(
                    name=name,
                    description=description,
                    price=price,
                    quantity=quantity,
                    unlimited=unlimited,
                    item_type=item_type,
                    category=category,
                    rarity=rarity,
                    effect=effect,
                    effect_value=effect_value,
                    effect_duration=effect_duration,
                    cooldown_seconds=cooldown_seconds,
                    targetable=targetable,
                    daily_limit=daily_limit,
                    global_daily_limit=global_daily_limit,
                    tradable=tradable,
                )
                session.add(new_shop_item)
            await session.commit()
            return new_shop_item

    async def seed_default_shop_items(self) -> dict:
        """
        Seed the shop with the default Dank Memer-style mixed-scarcity catalog.
        Only creates items that do not already exist by name.
        Returns a summary dict with counts.
        """

        catalog = [
            # Defensive
            {
                "name": "Padlock",
                "description": "Protects your wallet from one robbery attempt.",
                "price": 12500,
                "quantity": 50,
                "item_type": ItemType.DEFENSIVE,
                "category": ItemCategory.DEFENSIVE,
                "rarity": ItemRarity.COMMON,
                "effect": "rob_shield",
                "daily_limit": 3,
                "global_daily_limit": 50,
            },
            {
                "name": "Anti-Rob Aura",
                "description": "Timed aura that blocks all wallet robberies.",
                "price": 75000,
                "quantity": 30,
                "item_type": ItemType.DEFENSIVE,
                "category": ItemCategory.DEFENSIVE,
                "rarity": ItemRarity.UNCOMMON,
                "effect": "anti_rob",
                "effect_value": 1,
                "effect_duration": 3600,
                "daily_limit": 2,
                "global_daily_limit": 30,
            },
            {
                "name": "Bank Insurance",
                "description": "Timed aura that blocks all bank robberies.",
                "price": 125000,
                "quantity": 20,
                "item_type": ItemType.DEFENSIVE,
                "category": ItemCategory.DEFENSIVE,
                "rarity": ItemRarity.RARE,
                "effect": "anti_bank_rob",
                "effect_value": 1,
                "effect_duration": 3600,
                "daily_limit": 1,
                "global_daily_limit": 20,
            },
            # Offensive / targeted
            {
                "name": "Handcuffs",
                "description": "Handcuff a user, reducing their robbery payouts for 30m.",
                "price": 50000,
                "quantity": 30,
                "item_type": ItemType.OFFENSIVE,
                "category": ItemCategory.OFFENSIVE,
                "rarity": ItemRarity.UNCOMMON,
                "effect": "robbery_debuff",
                "effect_value": 0.75,
                "effect_duration": 1800,
                "targetable": True,
                "daily_limit": 2,
                "global_daily_limit": 30,
            },
            {
                "name": "Cursed Coin",
                "description": "Hex a user so their earnings are reduced for 1h.",
                "price": 100000,
                "quantity": 20,
                "item_type": ItemType.OFFENSIVE,
                "category": ItemCategory.OFFENSIVE,
                "rarity": ItemRarity.RARE,
                "effect": "earning_debuff",
                "effect_value": 0.8,
                "effect_duration": 3600,
                "targetable": True,
                "daily_limit": 1,
                "global_daily_limit": 20,
            },
            # Utility / earning
            {
                "name": "Lucky Clover",
                "description": "Boosts your luck for 1h.",
                "price": 25000,
                "quantity": 50,
                "item_type": ItemType.CONSUMABLE,
                "category": ItemCategory.UTILITY,
                "rarity": ItemRarity.COMMON,
                "effect": "luck_boost",
                "effect_value": 1.25,
                "effect_duration": 3600,
                "daily_limit": 3,
                "global_daily_limit": 50,
            },
            {
                "name": "Money Multiplier",
                "description": "Increases gambling payouts for 1h.",
                "price": 37500,
                "quantity": 40,
                "item_type": ItemType.CONSUMABLE,
                "category": ItemCategory.UTILITY,
                "rarity": ItemRarity.COMMON,
                "effect": "gambling_multiplier",
                "effect_value": 1.5,
                "effect_duration": 3600,
                "daily_limit": 3,
                "global_daily_limit": 40,
            },
            {
                "name": "Energy Drink",
                "description": "Reduces command cooldowns for 30m.",
                "price": 20000,
                "quantity": 50,
                "item_type": ItemType.CONSUMABLE,
                "category": ItemCategory.UTILITY,
                "rarity": ItemRarity.COMMON,
                "effect": "cooldown_reduction",
                "effect_value": 0.75,
                "effect_duration": 1800,
                "daily_limit": 3,
                "global_daily_limit": 50,
            },
            # Redeemable
            {
                "name": "Instant Cash Crate",
                "description": "Redeem for an instant currency grant.",
                "price": 5000,
                "quantity": 50,
                "item_type": ItemType.REDEEMABLE,
                "category": ItemCategory.REDEEMABLE,
                "rarity": ItemRarity.COMMON,
                "effect": "currency",
                "effect_value": 2500,
                "daily_limit": 5,
                "global_daily_limit": 50,
            },
            # Collectible / cosmetic
            {
                "name": "Golden Pepe",
                "description": "A rare cosmetic collectible.",
                "price": 2500000,
                "quantity": 50,
                "item_type": ItemType.COLLECTIBLE,
                "category": ItemCategory.COLLECTIBLE,
                "rarity": ItemRarity.LEGENDARY,
                "daily_limit": 1,
                "global_daily_limit": 3,
            },
        ]

        created = 0
        skipped = 0
        async with self.async_sessionmaker() as session:
            for data in catalog:
                result = await session.execute(
                    select(ShopItem).where(ShopItem.name == data["name"])
                )
                if result.scalar_one_or_none():
                    skipped += 1
                    continue

                shop_item = ShopItem(**data)
                session.add(shop_item)
                created += 1
            await session.commit()

        return {"created": created, "skipped": skipped, "total": len(catalog)}
