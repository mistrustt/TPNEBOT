from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import delete, exists
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from typing import List, Optional, Tuple
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
    ShopItem,
    ItemCooldown,
    ActiveEffect,
    TradeLog,
    Bounty,
)
from datetime import timedelta
import discord
import uuid
import logging
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger("discord_bot")


class InventoryMixin(BaseManager):
    async def add_crypto_asset(
        self, user_id: int, symbol: str, amount: Decimal, purchase_price: Decimal
    ):
        """Add a new crypto asset or update existing one for a user."""
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    result = await session.execute(
                        select(CryptoAsset).where(
                            CryptoAsset.user_id == user_id,
                            CryptoAsset.symbol == symbol.upper(),
                        ).order_by(CryptoAsset.purchase_date.desc())
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
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CryptoAsset).where(CryptoAsset.user_id == user_id)
            )
            return result.scalars().all()
    async def get_crypto_asset(self, user_id: int, symbol: str) -> CryptoAsset:
        """Get specific crypto asset for a user."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CryptoAsset).where(
                    CryptoAsset.user_id == user_id, CryptoAsset.symbol == symbol.upper()
                )
            )
            return result.scalar_one_or_none()
    async def update_crypto_amount(self, user_id: int, symbol: str, amount: Decimal):
        """Update amount of a crypto asset."""
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
                    headers = {"Authorization": f"Bearer {os.getenv('FREECRYPTOAPI_API_KEY')}"}

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
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Item).where(Item.user_id == user_id))
            return result.scalars().all()
    async def get_user_inventory_grouped(self, user_id: int) -> List[dict]:
        """Return inventory items grouped by name with aggregated quantity."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item.name, Item.description, func.count(Item.id).label("qty"))
                .where(Item.user_id == user_id)
                .group_by(Item.name, Item.description)
            )
            rows = result.all()
            return [{"name": r[0], "description": r[1], "quantity": r[2]} for r in rows]
    async def get_user_item(self, user_id: int, item_id: int) -> Item:
        """
        Retrieve a specific item from user's inventory.

        Args:
            user_id: The Discord ID of the user
            item_id: The ID of the item

        Returns:
            Item object if found, None otherwise
        """
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
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.item_type == item_type)
            )
            return result.scalars().all()
    async def transfer_item(
        self, from_user_id: int, to_user_id: int, item_id: int, quantity: int = 1
    ) -> bool:
        """Transfer items from one user to another."""

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
                            effect=source_item.effect,
                            effect_value=source_item.effect_value,
                            effect_duration=source_item.effect_duration,
                            cooldown_seconds=source_item.cooldown_seconds,
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
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == user_id, Item.id == item_id)
                )
                item = result.scalar_one_or_none()

                if not item:
                    return False

                allowed_props = ["name", "description", "quantity", "item_type"]
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
        async with self.async_sessionmaker() as session:
            serial_number = await self.generate_item_serial_number()
            item = Item(
                user_id=user_id,
                name=name,
                serial_number=serial_number,
                description=description,
                quantity=quantity,
                item_type=item_type,
            )
            session.add(item)
            await session.commit()
            return item
    async def merge_duplicate_items(self, user_id: int) -> int:
        """
        Consolidate duplicate items in a user's inventory.
        Items with the same name will be merged.

        Args:
            user_id: The Discord ID of the user

        Returns:
            Number of items merged
        """
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
    async def add_shop_item(
        self,
        name: str,
        description: str,
        price: Decimal,
        quantity: int,
        item_type: ItemType = ItemType.COLLECTIBLE,
        unlimited: bool = False,
        effect: str = None,
        effect_value: int = None,
        effect_duration: int = None,
        cooldown_seconds: int = None,
    ) -> ShopItem:
        """Create and store a new shop item."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                new_shop_item = ShopItem(
                    name=name,
                    description=description,
                    price=price,
                    quantity=quantity,
                    unlimited=unlimited,
                    item_type=item_type,
                    effect=effect,
                    effect_value=effect_value,
                    effect_duration=effect_duration,
                    cooldown_seconds=cooldown_seconds,
                )
                session.add(new_shop_item)
            await session.commit()
            return new_shop_item
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
          - Verifying available stock.
          - Checking if the buyer’s wallet has enough funds.
          - Deducting the total cost from the user’s wallet.
          - Reducing the shop’s stock.
          - Adding the purchased item to the user’s inventory.
        Returns a dict containing purchase details.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ShopItem).where(ShopItem.id == shop_item_id)
                )
                shop_item = result.scalar_one_or_none()
                if not shop_item:
                    raise ValueError("Shop item not found.")
                if not shop_item.unlimited and shop_item.quantity < quantity:
                    raise ValueError("Insufficient stock available.")

                total_cost = Decimal(str(shop_item.price * quantity))

                wallet_id = await self.get_wallet_id_for_user(user_id)
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError(f"Wallet {wallet_id} not found.")
                wallet_balance = await self.get_wallet_balance(wallet_id)
                if wallet_balance < total_cost:
                    raise ValueError("Insufficient funds.")

                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                await self.process_treasury_transaction(
                    wallet_id, -total_cost, f"Purchased {quantity}x {shop_item.name}", "standard"
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
                        effect=shop_item.effect,
                        effect_value=shop_item.effect_value,
                        effect_duration=shop_item.effect_duration,
                        cooldown_seconds=shop_item.cooldown_seconds,
                    )
                    session.add(new_inventory_item)
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
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.id == item_id)
            )
            item = result.scalar_one_or_none()
            if not item:
                raise ValueError("Item not found in inventory.")

            if item.item_type == ItemType.CONSUMABLE:
                message = f"You have consumed one {item.name}."
                if item.effect == "currency" and item.effect_value:
                    wallet_id = await self.get_wallet_id_for_user(user_id)
                    await self.process_treasury_transaction(
                        wallet_id, Decimal(item.effect_value), f"Used {item.name}", "standard"
                    )
                async with session.begin():
                    item.quantity -= 1
                    if item.quantity <= 0:
                        await session.delete(item)
                await session.commit()
                return message
            elif item.item_type == ItemType.REDEEMABLE:
                message = f"You have redeemed {item.name} and received its benefits."
                if item.effect == "currency" and item.effect_value:
                    wallet_id = await self.get_wallet_id_for_user(user_id)
                    await self.process_treasury_transaction(
                        wallet_id, Decimal(item.effect_value), f"Redeemed {item.name}", "standard"
                    )
                async with session.begin():
                    await session.delete(item)
                await session.commit()
                return message
            elif item.item_type == ItemType.COLLECTIBLE:
                return f"You are now showcasing your collectible {item.name}."
            else:
                raise ValueError("Unknown item type.")
    async def set_item_cooldown(
        self, user_id: int, item_name: str, cooldown_seconds: int
    ) -> None:
        """Set a cooldown for a user on a specific item."""
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
        remaining = await self.get_item_cooldown(user_id, item_name)
        return remaining > 0
    async def clear_item_cooldown(self, user_id: int, item_name: str) -> bool:
        """Clear a cooldown for a user's item. Returns True if cooldown was cleared."""
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
        async with self.async_sessionmaker() as session:
            async with session.begin():
                expires_at = discord.utils.utcnow() + timedelta(seconds=duration_seconds)
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
    async def cleanup_expired_effects(self) -> int:
        """Remove all expired effects. Returns count of removed effects."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                now = discord.utils.utcnow()
                stmt = delete(ActiveEffect).where(ActiveEffect.expires_at <= now)
                result = await session.execute(stmt)
            await session.commit()
            return result.rowcount
    async def get_effect_multiplier(
        self, user_id: int, effect_type: str
    ) -> Decimal:
        """
        Get the combined multiplier value for a specific effect type.
        Returns Decimal('1.0') if no active effects.
        For multipliers, returns the product of all active multipliers.
        """
        effects = await self.get_active_effects_by_type(user_id, effect_type)
        if not effects:
            return Decimal("1.0")
        combined = Decimal("1.0")
        for effect in effects:
            combined *= effect.effect_value
        return combined
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
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Verify ownership
                stmt = select(Item).where(Item.id == item_id)
                item = (await session.execute(stmt)).scalar_one_or_none()
                if not item:
                    raise ValueError("Item not found.")
                if item.user_id != from_user_id:
                    raise ValueError("You don't own this item.")
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
                        effect=item.effect,
                        effect_value=item.effect_value,
                        effect_duration=item.effect_duration,
                        cooldown_seconds=item.cooldown_seconds,
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
        async with self.async_sessionmaker() as session:
            stmt = (
                select(TradeLog)
                .where(TradeLog.to_user_id == user_id, TradeLog.status == "pending")
                .order_by(TradeLog.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
    async def use_inventory_item_with_effects(
        self, user_id: int, item_id: int
    ) -> dict:
        """
        Enhanced version of use_inventory_item that handles cooldowns,
        effect types, and effect durations.

        Returns a dict with:
        - message: str - result message
        - effect_type: str (optional)
        - effect_applied: bool
        - cooldown_seconds: int (optional)
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.id == item_id)
            )
            item = result.scalar_one_or_none()
            if not item:
                raise ValueError("Item not found in inventory.")

            # Check cooldown
            if item.cooldown_seconds:
                remaining = await self.get_item_cooldown(user_id, item.name)
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
                wallet_id = await self.get_wallet_id_for_user(user_id)
                await self.process_treasury_transaction(
                    wallet_id, Decimal(effect_value), f"Used {item.name}", "standard"
                )
                response["message"] = f"You received {effect_value} coins from {item.name}!"
                response["effect_applied"] = True

            elif effect in (
                "gambling_multiplier",
                "luck_boost",
                "earning_boost",
                "cooldown_reduction",
                "rtp_boost",
            ):
                # Create timed effect
                if effect_duration:
                    await self.create_active_effect(
                        user_id=user_id,
                        effect_type=effect,
                        effect_value=Decimal(str(effect_value)),
                        duration_seconds=effect_duration,
                        source_item_name=item.name,
                    )
                    duration_mins = effect_duration // 60
                    duration_secs = effect_duration % 60
                    duration_str = f"{duration_mins}m {duration_secs}s" if duration_mins else f"{duration_secs}s"

                    effect_names = {
                        "gambling_multiplier": f"{effect_value}x gambling multiplier",
                        "luck_boost": f"{effect_value}x luck boost",
                        "earning_boost": f"{effect_value}x earning boost",
                        "cooldown_reduction": f"{effect_value}% cooldown reduction",
                        "rtp_boost": f"{effect_value}% RTP boost",
                    }
                    response["message"] = (
                        f"Activated {effect_names.get(effect, effect)} for {duration_str}!"
                    )
                    response["effect_applied"] = True
                else:
                    response["message"] = f"Used {item.name} but no duration was specified."

            elif item.item_type == ItemType.COLLECTIBLE:
                response["message"] = f"You are showcasing your collectible {item.name}."
            else:
                response["message"] = f"You used {item.name}."

            # Set cooldown if applicable
            if item.cooldown_seconds and item.item_type != ItemType.COLLECTIBLE:
                await self.set_item_cooldown(user_id, item.name, item.cooldown_seconds)
                response["cooldown_seconds"] = item.cooldown_seconds

            # Handle quantity reduction based on item type
            if item.item_type == ItemType.CONSUMABLE:
                async with session.begin():
                    item.quantity -= 1
                    if item.quantity <= 0:
                        await session.delete(item)
                await session.commit()
            elif item.item_type == ItemType.REDEEMABLE:
                async with session.begin():
                    await session.delete(item)
                await session.commit()

            return response
    async def place_bounty(
        self, issuer_id: int, target_id: int, reward: Decimal
    ) -> Bounty:
        """Place (or increase) a bounty on a user."""
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet_id = await self.get_wallet_id_for_user(issuer_id)
                wallet_balance = await self.get_wallet_balance(wallet_id)
                if wallet_balance < reward:
                    raise ValueError("Insufficient balance to place bounty.")

                await self.process_treasury_transaction(
                    wallet_id, -reward, f"Placed bounty on {target_id}", "high_value"
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

            return result.all()
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

                claimer_wallet = await self.get_wallet_id_for_user(claimer_id)
                await self.process_treasury_transaction(
                    claimer_wallet, reward_amount, f"Claimed bounty on {target_id}", "high_value"
                )

            await session.commit()

            bounty.reward = reward_amount
            bounty.active = False
            bounty.claimer_id = claimer_id
            return bounty
