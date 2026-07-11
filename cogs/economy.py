import re
import hmac
import discord
import logging
import secrets
import asyncio
import random
import hmac, hashlib
from discord import app_commands
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from discord import Button, Interaction
from discord.ui import View, Button
from discord.ext import commands, tasks
from utils.misc import MiscUtils
from utils.embeds import Embeds
from utils.amount import AmountUtils
from utils.cooldown import unified_cooldown
from utils.guardrails import check_slash_guardrails
from utils.security import resolve_id, resolve_ids
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Sequence, List, Any

logger = logging.getLogger("discord.client")

class DropView(discord.ui.View):
    def __init__(self, bot, inter, amount, drop_author, currency_name, emoji, cog):
        super().__init__(timeout=120.0)
        self.bot = bot
        self.inter = inter
        self.amount = amount
        self.drop_author = drop_author
        self.currency_name = currency_name
        self.emoji = emoji
        self.claimed = False
        self.expired = False
        self.cog = cog
        self.message = None
        self.lock = asyncio.Lock()

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary)
    async def claim_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        if interaction.user.bot:
            await interaction.response.send_message(
                "Bots are not allowed to claim drops.", ephemeral=True
            )
            return

        if interaction.user == self.drop_author:
            await interaction.response.send_message(
                "You can't claim your own drop!", ephemeral=True
            )
            return

        active_info = self.cog.active_drops.get(interaction.message.id)
        if self.claimed or (active_info and active_info.get("claimed")):
            await interaction.response.send_message(
                "This drop has already been claimed.", ephemeral=True
            )
            return

        if await self.cog.bot.database.is_user_blacklisted(int(interaction.user.id)):
            await interaction.response.send_message(
                "You are not allowed to claim drops.", ephemeral=True
            )
            return

        await self.complete_claim(interaction)

    async def complete_claim(self, interaction: discord.Interaction):
        async with self.lock:
            if self.expired:
                await interaction.response.send_message(
                    "This drop has expired.", ephemeral=True
                )
                return

            active_info = self.cog.active_drops.get(self.message.id)
            if self.claimed or (active_info and active_info.get("claimed")):
                await interaction.response.send_message(
                    "This drop has already been claimed.", ephemeral=True
                )
                return

            claim_wallet = await self.cog.bot.database.get_wallet_id_for_user(
                interaction.user.id
            )
            await self.cog.bot.database.process_treasury_transaction(
                wallet_id=claim_wallet,
                amount=Decimal(self.amount),
                description="Drop Claim",
            )
            self.claimed = True

            for child in self.children:
                child.disabled = True

        if self.message is not None and self.message.embeds:
            await self.message.edit(embed=self.message.embeds[0], view=self)

        await Embeds.success(
            interaction,
            f"**{interaction.user.display_name}** claimed the {self.currency_name} "
            f"**{await self.cog.formatter(self.amount)}** dropped by **{self.drop_author.display_name}**! 🎉",
        )

        if self.message and self.message.id in self.cog.active_drops:
            del self.cog.active_drops[self.message.id]

    async def on_timeout(self):
        if not self.claimed:
            self.expired = True
            refund_wallet = await self.cog.bot.database.get_wallet_id_for_user(
                self.drop_author.id
            )
            await self.cog.bot.database.process_treasury_transaction(
                wallet_id=refund_wallet,
                amount=Decimal(self.amount),
                description="Drop Refund",
            )

            for child in self.children:
                child.disabled = True

            if self.message:
                try:
                    await self.message.edit(
                        content="Drop ended! No one claimed the drop, so the money was refunded.",
                        view=self,
                    )
                except Exception:
                    pass

            if self.message and self.message.id in self.cog.active_drops:
                del self.cog.active_drops[self.message.id]


class AirDropView(discord.ui.View):
    def __init__(self, bot, amount, currency_name, initiator_wallet, initiator):
        super().__init__(timeout=None)
        self.bot = bot
        self.amount = amount
        self.currency_name = currency_name
        self.initiator_wallet = initiator_wallet
        self.initiator = initiator
        self.joiners = set()
        self.message = None

        self.auto_disable_task = asyncio.create_task(self.auto_disable())

    async def auto_disable(self):
        await asyncio.sleep(15)

        economy = self.bot.get_cog("Economy")

        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
        self.stop()

        if not self.joiners:
            async with self.bot.database.get_session() as session:
                async with session.begin():
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=self.initiator_wallet,
                        amount=self.amount,
                        description="AirDrop Refund",
                    )
            try:
                await Embeds.error(
                    self.message.channel,
                    "Airdrop cancelled! No one joined, so the money was refunded.",
                    delete_after=None,
                )
            except Exception:
                pass

        else:
            share = self.amount / Decimal(len(self.joiners))
            for user_id in self.joiners:
                recipient_wallet = await self.bot.database.get_wallet_id_for_user(
                    user_id
                )
                async with self.bot.database.get_session() as session:
                    async with session.begin():
                        await self.bot.database.process_treasury_transaction(
                            wallet_id=recipient_wallet,
                            amount=share,
                            description=f"Airdrop from {self.initiator.display_name}",
                        )

            winners = [f"<@{uid}>" for uid in self.joiners]
            winners_str = ", ".join(winners)

            try:
                embed = discord.Embed(
                    description=(
                        f"Airdrop ended! **{len(self.joiners)} user(s)** joined: {winners_str}\n"
                        f"Each received {self.currency_name} **{await economy.formatter(share)}**."
                    ),
                    color=discord.Color.gold(),
                )
                await self.message.edit(embed=embed, view=None)
            except Exception:
                pass

    @discord.ui.button(label="Join", style=discord.ButtonStyle.primary)
    async def join_gift(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id == self.initiator.id:
            return await interaction.response.send_message(
                "You cannot join your own airdrop!", ephemeral=True
            )
        if interaction.user.id in self.joiners:
            return await interaction.response.send_message(
                "You already joined the airdrop!", ephemeral=True
            )

        if await self.bot.database.is_user_blacklisted(int(interaction.user.id)):
            await interaction.response.send_message(
                "You are not allowed to claim drops.", ephemeral=True
            )
            return

        self.joiners.add(interaction.user.id)
        await interaction.response.send_message(
            "You joined the airdrop!", ephemeral=True
        )

        share = AmountUtils.round_currency(self.amount / Decimal(len(self.joiners)))
        economy = self.bot.get_cog("Economy")

        embed = discord.Embed(
            title="🎁 Airdrop In Progress",
            description=(
                f"**{self.initiator.display_name}** dropped **{economy.currency_name} "
                f"{await economy.formatter(self.amount)}**!\n\n"
                f"Click **Join** within **15s** to claim.  "
                f"**{len(self.joiners)}** joined so far, each will receive:"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Per Person Payout",
            value=f"{economy.currency_name} **{await economy.formatter(share)}**",
            inline=False,
        )
        embed.add_field(
            name="Current Joiners",
            value="\n".join(f"<@{uid}>" for uid in self.joiners),
            inline=False,
        )

        await self.message.edit(embed=embed, view=self)


class ShopView(View):
    def __init__(self, bot, shop_items, user_id, currency_name):
        """
        Redesigned shop view for browsing and purchasing shop items.

        Args:
            bot: The bot instance.
            shop_items: List of shop item objects.
            user_id: ID of the user using the shop.
            currency_name: Name of the currency.
        """
        super().__init__(timeout=90)
        self.bot = bot
        self.shop_items = shop_items
        self.user_id = user_id
        self.currency_name = currency_name
        self.current_index = 0

    async def update_embed(self, interaction: discord.Interaction):
        """Update the embed to reflect the current shop item details."""
        economy = self.bot.get_cog("Economy")
        if not self.shop_items:
            embed = discord.Embed(
                description="There are no items in the shop right now. Please check back later!",
                color=discord.Color.red(),
            )
        else:
            item = self.shop_items[self.current_index]
            item_price = Decimal(item.price)
            embed = discord.Embed(
                title=f"{item.name}",
                description=item.description or "No description provided.",
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Price",
                value=f"{self.currency_name} **{await economy.formatter(item_price)}**",
                inline=True,
            )
            qty_text = (
                "∞"
                if getattr(item, "unlimited", False)
                else f"**{item.quantity}** left in stock."
            )
            embed.add_field(name="Quantity", value=qty_text, inline=True)
            embed.set_footer(
                text=f"Item {self.current_index + 1} of {len(self.shop_items)} • Use the navigation buttons below."
            )
        if interaction.response.is_done():
            await interaction.followup.edit_message(
                message_id=interaction.message.id, embed=embed, view=self
            )
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⬅", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def previous_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        """Go to the previous shop item."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        if self.shop_items:
            self.current_index = (self.current_index - 1) % len(self.shop_items)
        await self.update_embed(interaction)

    @discord.ui.button(label="Buy Now", style=discord.ButtonStyle.success)
    async def buy_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        """
        Initiate purchase confirmation of the current shop item.
        On confirmation, charges the user and transfers the item.
        """
        economy = self.bot.get_cog("Economy")
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        if not self.shop_items:
            await interaction.response.send_message(
                "No items available for purchase.", ephemeral=True
            )
            return

        if await self.bot.database.is_user_blacklisted(int(interaction.user.id)):
            await interaction.response.send_message(
                "You are not allowed to purchase items.", ephemeral=True
            )
            return

        item = self.shop_items[self.current_index]
        wallet_id = await self.bot.database.get_wallet_id_for_user(self.user_id)
        user_balance = await self.bot.database.get_wallet_balance(wallet_id)
        item_price = Decimal(item.price)

        if user_balance < item_price:
            await interaction.response.send_message(
                "You don't have enough funds to purchase this item.", ephemeral=True
            )
            return

        if not getattr(item, "unlimited", False) and item.quantity <= 0:
            await interaction.response.send_message(
                "Sorry, this item is out of stock.", ephemeral=True
            )
            return

        stock_line = (
            f"Stock left: **{item.quantity}**"
            if not getattr(item, "unlimited", False)
            else "Unlimited stock"
        )
        confirm_embed = discord.Embed(
            title="Confirm Purchase",
            description=(
                f"Are you sure you want to buy **{item.name}** for "
                f"{self.currency_name} **{await economy.formatter(item_price)}**?\n"
                f"{stock_line}"
            ),
            color=discord.Color.gold(),
        )
        confirm_view = ConfirmPurchaseView(
            self.bot, self.user_id, item, self.currency_name, self
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=confirm_embed, view=confirm_view, ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=confirm_embed, view=confirm_view, ephemeral=True
            )

    @discord.ui.button(label="➡", style=discord.ButtonStyle.secondary, emoji="➡️")
    async def next_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        """Go to the next shop item."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        if self.shop_items:
            self.current_index = (self.current_index + 1) % len(self.shop_items)
        await self.update_embed(interaction)


class ConfirmPurchaseView(View):
    def __init__(self, bot, user_id, item, currency_name, parent_view: ShopView):
        """
        Confirmation view for purchasing an item.

        Args:
            bot: Bot instance.
            user_id: ID of the user confirming purchase.
            item: The shop item object.
            currency_name: Name of the currency.
            parent_view: The ShopView instance that spawned this confirmation.
        """
        super().__init__(timeout=30)
        self.bot = bot
        self.user_id = user_id
        self.item = item
        self.currency_name = currency_name
        self.parent_view = parent_view

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        """Handle the confirmation of the purchase."""
        economy = self.bot.get_cog("Economy")
        if not self.item:
            await interaction.response.send_message(
                "No item selected for purchase.", ephemeral=True
            )
            return
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return

        if await self.bot.database.is_user_blacklisted(int(interaction.user.id)):
            await interaction.response.send_message(
                "You are not allowed to purchase items.", ephemeral=True
            )
            return

        item_price = AmountUtils.round_currency(Decimal(self.item.price))
        try:
            async with self.bot.database.get_session() as session:
                await self.bot.database.purchase_shop_item(
                    self.user_id, self.item.id, quantity=1
                )
        except Exception as e:
            await interaction.response.send_message(
                f"Purchase failed due to an error: {str(e)}", ephemeral=True
            )
            return

        if not getattr(self.item, "unlimited", False):
            self.item.quantity -= 1
        if not getattr(self.item, "unlimited", False) and self.item.quantity <= 0:
            self.parent_view.shop_items.pop(self.parent_view.current_index)
            if self.parent_view.shop_items:
                self.parent_view.current_index %= len(self.parent_view.shop_items)
            else:
                self.parent_view.current_index = 0

        await interaction.response.send_message(
            f"✅ You successfully purchased **{self.item.name}** for {self.currency_name} **{await economy.formatter(item_price)}**!",
            ephemeral=True,
        )

        try:
            await self.parent_view.update_embed(interaction)
        except:
            pass
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        await interaction.response.send_message("Purchase canceled.", ephemeral=True)
        self.stop()


class ItemPaginator(View):
    def __init__(self, bot, entries, user_id, title="Items", items_per_page=1):
        super().__init__(timeout=180)
        self.bot = bot
        self.entries = entries
        self.user_id = user_id
        self.title = title
        self.items_per_page = items_per_page
        self.current_page = 0
        self.max_page = (len(entries) - 1) // items_per_page

        self.prev_button = Button(
            label="⬅ Previous", style=discord.ButtonStyle.secondary
        )
        self.next_button = Button(label="Next ➡", style=discord.ButtonStyle.secondary)
        self.refresh_button = Button(
            label="🔄 Refresh", style=discord.ButtonStyle.primary
        )

        self.prev_button.callback = self.prev_button_callback
        self.next_button.callback = self.next_button_callback
        self.refresh_button.callback = self.refresh_callback

        self.add_item(self.prev_button)
        self.add_item(self.refresh_button)
        self.add_item(self.next_button)

        self.update_buttons()

    def update_buttons(self):
        """Enable or disable buttons based on the current page."""
        self.prev_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= self.max_page

    async def build_embed(self):
        """Build and return the paginated embed message."""
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        embed = discord.Embed(title=self.title, color=discord.Color.blurple())

        if not self.entries:
            embed.add_field(
                name="No items", value="There are no items to display.", inline=False
            )
        else:
            for item in self.entries[start:end]:
                name = item.get("name", "Unnamed")
                quantity = item.get("quantity", 0)
                description = item.get("description", "No description available")
                item_id = item.get("id")
                item_type = item.get("item_type", "unknown")
                category = item.get("category")
                rarity = item.get("rarity")
                targetable = item.get("targetable", False)

                header = f"{name} (x{quantity})"
                if item_id is not None:
                    header += f" — ID: `{item_id}`"

                details_parts = [f"**Type:** {item_type}"]
                if category:
                    details_parts.append(f"**Category:** {category}")
                if rarity:
                    details_parts.append(f"**Rarity:** {rarity}")
                if targetable:
                    details_parts.append("🎯 **Targetable**")
                details = " | ".join(details_parts)

                embed.add_field(
                    name=header,
                    value=f"{description}\n{details}",
                    inline=False,
                )

        embed.set_footer(text=f"Page {self.current_page + 1} of {self.max_page + 1}")
        return embed

    async def send_page(self, interaction=None):
        """Send or update the paginated embed message."""
        embed = await self.build_embed()

        if interaction:
            if interaction.response.is_done():
                await interaction.followup.edit_message(
                    message_id=interaction.message.id, embed=embed, view=self
                )
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        else:
            return embed

    async def prev_button_callback(self, interaction: discord.Interaction):
        """Display the previous page."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await self.send_page(interaction)

    async def next_button_callback(self, interaction: discord.Interaction):
        """Display the next page."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        self.current_page = min(self.max_page, self.current_page + 1)
        self.update_buttons()
        await self.send_page(interaction)

    async def refresh_callback(self, interaction: discord.Interaction):
        """Refresh the current page."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        await asyncio.sleep(0.5)
        await self.send_page(interaction)


class UseItemPaginator(View):
    def __init__(self, bot, entries, user_id, title="Use an Item", items_per_page=1):
        super().__init__(timeout=180)
        self.bot = bot
        self.entries = entries
        self.user_id = user_id
        self.title = title
        self.items_per_page = items_per_page
        self.current_page = 0
        self.max_page = (len(entries) - 1) // items_per_page

        self.prev_button = Button(
            label="⬅ Previous", style=discord.ButtonStyle.secondary
        )
        self.next_button = Button(label="Next ➡", style=discord.ButtonStyle.secondary)
        self.refresh_button = Button(
            label="🔄 Refresh", style=discord.ButtonStyle.primary
        )

        self.use_button = Button(label="✅ Use Item", style=discord.ButtonStyle.success)

        self.prev_button.callback = self.prev_button_callback
        self.next_button.callback = self.next_button_callback
        self.refresh_button.callback = self.refresh_callback
        self.use_button.callback = self.use_callback

        self.add_item(self.prev_button)
        self.add_item(self.refresh_button)
        self.add_item(self.next_button)
        self.add_item(self.use_button)

        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= self.max_page

        if not self.entries:
            self.use_button.disabled = True
        else:
            entry = self.entries[self.current_page]
            self.use_button.disabled = entry.get("quantity", 0) <= 0

    async def build_embed(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page

        embed = discord.Embed(title=self.title, color=discord.Color.blurple())
        if not self.entries:
            embed.add_field(
                name="No items", value="Your inventory is empty.", inline=False
            )
        else:
            entry = self.entries[start]
            name = entry.get("name", "Unnamed")
            quantity = entry.get("quantity", 0)
            description = entry.get("description", "No description available.")
            embed.add_field(
                name=f"{name} (x{quantity})", value=description, inline=False
            )

        embed.set_footer(text=f"Page {self.current_page+1} of {self.max_page+1}")
        return embed

    async def send_page(self, interaction=None):
        embed = await self.build_embed()

        if interaction:
            if interaction.response.is_done():
                await interaction.followup.edit_message(
                    message_id=interaction.message.id, embed=embed, view=self
                )
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        else:
            return embed

    async def prev_button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await self.send_page(interaction)

    async def next_button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        self.current_page = min(self.max_page, self.current_page + 1)
        self.update_buttons()
        await self.send_page(interaction)

    async def refresh_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        await asyncio.sleep(0.5)
        await self.send_page(interaction)

    async def use_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your embed!", ephemeral=True
            )
            return
        entry = self.entries[self.current_page]
        item_id = entry.get("id")
        item_name = entry.get("name", "Unknown")
        try:
            # Check for cooldown first
            remaining = await self.bot.database.get_item_cooldown(self.user_id, item_name)
            if remaining > 0:
                mins, secs = divmod(remaining, 60)
                await interaction.response.send_message(
                    f"This item is on cooldown. Time remaining: {mins}m {secs}s",
                    ephemeral=True,
                )
                return

            # Use the enhanced method that handles effects
            result = await self.bot.database.use_inventory_item_with_effects(
                self.user_id, item_id
            )

            # Refresh inventory
            self.entries = await self.bot.database.get_user_inventory_grouped(
                self.user_id
            )
            self.max_page = (len(self.entries) - 1) // self.items_per_page
            self.current_page = min(self.current_page, self.max_page)
            self.update_buttons()

            embed = await self.build_embed()
            await interaction.response.edit_message(embed=embed, view=self)

            # Send result message
            message = result.get("message", "Item used successfully!")
            if result.get("cooldown_seconds"):
                mins, secs = divmod(result["cooldown_seconds"], 60)
                message += f"\nCooldown: {mins}m {secs}s"
            await interaction.followup.send(message, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)


class TradeRequestView(discord.ui.View):
    """View for accepting or declining trade requests."""

    def __init__(self, bot, trade_id: int, from_user_id: int, to_user_id: int, item_name: str, quantity: int):
        super().__init__(timeout=120.0)
        self.bot = bot
        self.trade_id = trade_id
        self.from_user_id = from_user_id
        self.to_user_id = to_user_id
        self.item_name = item_name
        self.quantity = quantity
        self.responded = False

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        if interaction.user.id != self.to_user_id:
            await interaction.response.send_message(
                "This trade request is not for you.", ephemeral=True
            )
            return

        if self.responded:
            await interaction.response.send_message(
                "This trade has already been processed.", ephemeral=True
            )
            return

        try:
            trade = await self.bot.database.accept_trade_request(self.trade_id)
            self.responded = True

            # Notify both parties
            from_user = self.bot.get_user(self.from_user_id)
            to_user = self.bot.get_user(self.to_user_id)
            from_name = from_user.display_name if from_user else f"User {self.from_user_id}"
            to_name = to_user.display_name if to_user else f"User {self.to_user_id}"

            embed = discord.Embed(
                title="Trade Completed",
                description=f"{to_name} accepted the trade!",
                color=discord.Color.green(),
            )
            embed.add_field(name="Item", value=f"{self.item_name} x{self.quantity}", inline=True)

            await interaction.response.edit_message(embed=embed, view=None)

            # Try to DM the sender
            if from_user:
                try:
                    dm_embed =await Embeds.custom(
                        from_user,
                        f"{to_name} accepted your trade request for {self.item_name} x{self.quantity}",
                        title="Trade Accepted",
                        color=discord.Color.green(),
                        delete_after=None,
                    )
                    await from_user.send(embed=dm_embed)
                except discord.Forbidden:
                    pass  # DMs disabled
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to complete trade: {str(e)}", ephemeral=True
            )

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        if interaction.user.id != self.to_user_id:
            await interaction.response.send_message(
                "This trade request is not for you.", ephemeral=True
            )
            return

        if self.responded:
            await interaction.response.send_message(
                "This trade has already been processed.", ephemeral=True
            )
            return

        try:
            await self.bot.database.decline_trade_request(self.trade_id)
            self.responded = True

            from_user = self.bot.get_user(self.from_user_id)
            to_user = self.bot.get_user(self.to_user_id)
            to_name = to_user.display_name if to_user else f"User {self.to_user_id}"

            embed = discord.Embed(
                title="Trade Declined",
                description=f"{to_name} declined the trade request.",
                color=discord.Color.red(),
            )
            await interaction.response.edit_message(embed=embed, view=None)

            # Try to DM the sender
            if from_user:
                try:
                    dm_embed = await Embeds.custom(
                        from_user,
                        f"{to_name} declined your trade request for {self.item_name}",
                        title="Trade Declined",
                        color=discord.Color.red(),
                        delete_after=None,
                    )
                    await from_user.send(embed=dm_embed)
                except discord.Forbidden:
                    pass
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to decline trade: {str(e)}", ephemeral=True
            )

    async def on_timeout(self):
        if not self.responded:
            try:
                await self.bot.database.decline_trade_request(self.trade_id)
            except:
                pass


class BalanceView(discord.ui.View):
    def __init__(self, cog, member, requesting_user):
        super().__init__(timeout=60)
        self.cog = cog
        self.member = member
        self.requesting_user = requesting_user

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requesting_user.id:
            await interaction.response.send_message(
                "This isn't your embed!", ephemeral=True
            )
            return False
        return True

    def __init__(self, cog, member, requesting_user, original_embed):
        super().__init__(timeout=60)
        self.cog = cog
        self.member = member
        self.requesting_user = requesting_user
        self.original_embed = original_embed
        self.back_button.disabled = True

    @discord.ui.button(label="View Crypto Assets", style=discord.ButtonStyle.primary)
    async def assets_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        assets = await self.cog.bot.database.get_crypto_assets(self.member.id)
        filtered = [a for a in assets if a.amount >= Decimal("0.01")]
        
        if not filtered:
            return await interaction.response.send_message(
                "No crypto assets to display.", ephemeral=True
            )
            
        embed = discord.Embed(
            title="💼 Crypto Portfolio",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        
        # Calculate totals
        total_value = Decimal("0")
        total_cost = Decimal("0")
        total_pnl = Decimal("0")
        
        # Build asset details
        asset_details = []
        for asset in filtered:
            price = await self.cog.bot.database.get_crypto_price(asset.symbol)
            if price:
                value = asset.amount * price
                cost = asset.amount * asset.purchase_price
                pnl = value - cost
                pnl_pct = (pnl / cost * 100) if cost > 0 else Decimal("0")
                
                total_value += value
                total_cost += cost
                total_pnl += pnl
                
                symbol = "📈" if pnl >= 0 else "📉"
                asset_details.append({
                    "symbol": asset.symbol,
                    "amount": asset.amount,
                    "price": price,
                    "value": value,
                    "cost": cost,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "icon": symbol
                })
            else:
                embed.add_field(
                    name=asset.symbol, value="Price data unavailable", inline=False
                )
        
        # Add portfolio summary at top
        portfolio_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else Decimal("0")
        portfolio_icon = "📈" if total_pnl >= 0 else "📉"
        
        embed.add_field(
            name="📊 Portfolio Summary",
            value=(
                f"Total Value: **{await self.cog.short_formatter(total_value)} {self.cog.currency_name}**\n"
                f"Total Cost: **{await self.cog.short_formatter(total_cost)} {self.cog.currency_name}**\n"
                f"{portfolio_icon} Total P/L: **{await self.cog.short_formatter(total_pnl)}** ({portfolio_pnl_pct:.2f}%)"
            ),
            inline=False
        )
        embed.add_field(name="\u200b", value="\u200b", inline=False)  # Divider
        
        # Add individual asset details
        for asset_info in asset_details:
            embed.add_field(
                name=f"{asset_info['icon']} {asset_info['symbol']}",
                value=(
                    f"Amount: **{await self.cog.short_formatter(asset_info['amount'])}**\n"
                    f"Price: **{await self.cog.short_formatter(asset_info['price'])}**\n"
                    f"Value: **{await self.cog.short_formatter(asset_info['value'])}**\n"
                    f"P/L: **{await self.cog.short_formatter(asset_info['pnl'])}** ({asset_info['pnl_pct']:.2f}%)"
                ),
                inline=False
            )
        
        embed.set_footer(text=f"Portfolio for {self.member.display_name}")
        
        # Enable back button and update view
        self.back_button.disabled = False
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.red, disabled=True)
    async def back_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Disable back button and re-enable assets button
        self.back_button.disabled = True
        await interaction.response.edit_message(embed=self.original_embed, view=self)

class TransactionPaginator(discord.ui.View):
    def __init__(self, cog, transactions, member, requesting_user):
        super().__init__(timeout=60)
        self.cog = cog
        self.transactions = transactions
        self.member = member
        self.requesting_user = requesting_user
        self.current_page = 0
        self.per_page = 5
        self.message = None

        for child in self.children:
            if child.label == "Previous":
                child.disabled = True
            if child.label == "Next" and len(transactions) <= self.per_page:
                child.disabled = True

    @property
    def max_pages(self):
        return max(1, (len(self.transactions) + self.per_page - 1) // self.per_page)

    async def get_page_embed(self, page):
        start_idx = page * self.per_page
        end_idx = min(start_idx + self.per_page, len(self.transactions))
        page_transactions = self.transactions[start_idx:end_idx]

        color = (
            self.member.top_role.color
            if hasattr(self.member, "top_role")
            else discord.Color.blurple()
        )
        embed = discord.Embed(
            title=f"{self.member.display_name}'s Transactions", color=color
        )

        tx_ids = []
        for tx in page_transactions:
            tx_ids.extend([getattr(tx, "from_user_id", None), getattr(tx, "to_user_id", None)])
        resolved_tx_ids = await resolve_ids(self.cog.bot.database, tx_ids)

        for tx in page_transactions:
            amount = Decimal(tx.amount) if tx.amount else Decimal("0")
            formatted_amount = await self.cog.formatter(amount)
            description = tx.description if tx.description else "No description"

            timestamp = (
                tx.timestamp.strftime("%Y-%m-%d %H:%M")
                if tx.timestamp
                else "Unknown time"
            )

            raw_from = resolved_tx_ids.get(getattr(tx, "from_user_id", None))
            raw_to = resolved_tx_ids.get(getattr(tx, "to_user_id", None))

            if raw_from == self.member.id and raw_to != self.member.id:
                direction = "📤 Sent"
            elif raw_from != self.member.id and raw_to == self.member.id:
                direction = "📥 Received"
            else:
                direction = "🔄 Internal"

            embed.add_field(
                name=f"{direction} • {timestamp}",
                value=(
                    f"**Amount:** {self.cog.currency_name} **{formatted_amount}**\n"
                    f"**Details:** {description}\n"
                    f"**ID:** `{tx.id}`"
                ),
                inline=False,
            )

        embed.set_footer(
            text=f"Page {page + 1} of {self.max_pages} • Total: {len(self.transactions)} transactions"
        )
        return embed

    @discord.ui.button(label="⬅", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.requesting_user.id:
            await interaction.response.send_message(
                "This isn't your embed!", ephemeral=True
            )
            return

        self.current_page = max(0, self.current_page - 1)

        button.disabled = self.current_page == 0
        next_button = [x for x in self.children if x.label == "Next"][0]
        next_button.disabled = self.current_page >= (self.max_pages - 1)

        embed = await self.get_page_embed(self.current_page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="➡", style=discord.ButtonStyle.secondary, emoji="➡️")
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.requesting_user.id:
            await interaction.response.send_message(
                "This isn't your embed!", ephemeral=True
            )
            return

        self.current_page = min(self.max_pages - 1, self.current_page + 1)

        button.disabled = self.current_page >= (self.max_pages - 1)
        prev_button = [x for x in self.children if x.label == "Previous"][0]
        prev_button.disabled = self.current_page == 0

        embed = await self.get_page_embed(self.current_page)
        await interaction.response.edit_message(embed=embed, view=self)

U64_RANGE = 1 << 64

def _u64_from_hmac(server_seed: str, client_seed: str, nonce: int, tag: str) -> int:
    """
    Produce a 64-bit unsigned int via HMAC(server_seed, f"{client_seed}:{nonce}:{tag}").
    'tag' provides domain-separation across functions so the same nonce doesn't correlate outputs.
    """
    msg = f"{client_seed}:{nonce}:{tag}".encode()
    digest = hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big")

def _rehash_u64(u64: int) -> int:
    """Deterministically 'stretch' to a fresh 64-bit value for rejection sampling."""
    return int.from_bytes(hashlib.sha256(u64.to_bytes(8, "big")).digest()[:8], "big")

def _rand_below_unbiased(u64: int, n: int) -> int:
    """
    Rejection sampling to remove modulo bias. Returns x in [0, n).
    """
    if n <= 0:
        raise ValueError("upper bound must be positive")
    limit = U64_RANGE - (U64_RANGE % n)
    while u64 >= limit:
        u64 = _rehash_u64(u64)
    return u64 % n

class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.utils = MiscUtils(self)
        self.currency_name = "<:coin:1359823671581085847>"
        self.active_drops = {}
        self.active_players = set()
        self.immune_user_ids = None
        self.roll_history = defaultdict(list)
        self.games = [
            "gamble",
            "supergamble",
            "dice",
            "slots",
            "blackjack",
            "roulette",
            "mines",
            "double",
            "drop",
            "ladder",
            "poker",
            "crash",
            "hilo",
            "ridebus",
        ]
        self.exchange_rate = Decimal("1000000000000")
        self.validate_economy_task.start()
        self.fire_inactive_employees_task.start()
        self.use_item.autocomplete("item_id")(self.use_item_autocomplete)

    @commands.Cog.listener()
    async def on_ready(self):
        if self.immune_user_ids is None:
            try:
                app_info = await self.application_info()
                team_members = getattr(
                    getattr(app_info, "team", None), "members", []
                )
                self.immune_user_ids = [int(member.id) for member in team_members]
            except Exception:
                self.immune_user_ids = []
        logger.info(f"Cog {self.__class__.__name__} is ready!")

    def cog_unload(self):
        self.validate_economy_task.cancel()
        self.fire_inactive_employees_task.cancel()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_slash_guardrails(self, interaction)

    async def _next_u64(self, user_id: int, *, tag: str) -> tuple[int, dict]:
        server_seed, client_seed, nonce = await self.bot.database.bump_and_get(user_id)
        msg = f"{client_seed}:{nonce}:{tag}".encode()
        digest = hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
        u64 = int.from_bytes(digest[:8], "big")
        proof = {
            "server_seed_hash": hashlib.sha256(server_seed.encode()).hexdigest(),
            "client_seed": client_seed,
            "nonce": nonce,
            "tag": tag,
            "u64_hex": digest[:8].hex(),
        }
        return u64, proof

    async def fair_randbelow(
        self, user_id: int, upper: int, *, tag: str = "randbelow"
    ) -> int:
        if upper <= 0:
            raise ValueError("upper must be > 0")
        u64, _ = await self._next_u64(user_id, tag=tag)

        limit = U64_RANGE - (U64_RANGE % upper)
        while u64 >= limit:
            u64 = int.from_bytes(
                hashlib.sha256(u64.to_bytes(8, "big")).digest()[:8], "big"
            )
        return u64 % upper

    async def fair_random(self, user_id: int) -> float:
        u64, _ = await self._next_u64(user_id, tag="random")
        return u64 / float(U64_RANGE)

    async def fair_sample(self, user_id: int, seq: Sequence[Any], k: int) -> List[Any]:
        """
        Sample k elements without replacement using a Fisher–Yates shuffle.
        We increment the nonce inside fair_randbelow per swap; there is NO extra increment here.
        """
        if k < 0 or k > len(seq):
            raise ValueError(
                "Sample size cannot exceed sequence length and must be non-negative."
            )
        clone = list(seq)
        await self.fair_shuffle(user_id, clone)
        return clone[:k]

    async def fair_choice(
        self, user_id: int, seq: Sequence[Any], *, tag: str = "choice"
    ):
        if not seq:
            raise ValueError("sequence must be non-empty")
        idx = await self.fair_randbelow(user_id, len(seq), tag=tag)
        return seq[idx]

    async def fair_shuffle(self, user_id: int, deck: List[Any]) -> None:
        for i in range(len(deck) - 1, 0, -1):
            j = await self.fair_randbelow(user_id, i + 1, tag=f"shuffle:{i}")
            deck[i], deck[j] = deck[j], deck[i]

    async def fair_uniform(
        self, user_id: int, min_value: float, max_value: float
    ) -> float:
        if min_value >= max_value:
            raise ValueError("min_value must be less than max_value")
        r = await self.fair_random(user_id)
        return min_value + (max_value - min_value) * r

    async def fair_systemrandom(
        self, user_id: int, min_value: int, max_value: int, *, tag: str = "systemrandom"
    ) -> int:
        if min_value > max_value:
            raise ValueError("min_value must be <= max_value")
        span = (max_value - min_value) + 1
        idx = await self.fair_randbelow(user_id, span, tag=tag)
        return min_value + idx

    async def prove_fairness(self, user_id: int) -> dict:
        """
        Publish commitment only; reveal raw seed only after rotation.
        """
        server_seed = await self.bot.database.get_server_seed(user_id)
        client_seed, nonce = await self.bot.database.get_client_seed(user_id)
        return {
            "server_seed_hash": hashlib.sha256(server_seed.encode()).hexdigest(),
            "client_seed": client_seed,
            "nonce": nonce,
        }

    def _fmt_no_sci(
        self, x: Decimal, *, max_frac: int = 2, rounding=ROUND_HALF_UP
    ) -> str:
        """
        Format a Decimal without scientific notation, with commas,
        and trim trailing zeros up to max_frac places.
        """
        if max_frac < 0:
            max_frac = 0
        quant = Decimal(1).scaleb(-max_frac) if max_frac else Decimal(1)
        xq = x.quantize(quant, rounding=rounding)
        s = f"{xq:,.{max_frac}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    async def formatter(self, value: Decimal) -> str:
        """Currency-friendly long format (e.g., 1000000 -> '1 million')."""
        negative = value < 0
        value = abs(value)

        suffixes = [
            (Decimal("1e33"), " decillion"),
            (Decimal("1e30"), " nonillion"),
            (Decimal("1e27"), " octillion"),
            (Decimal("1e24"), " septillion"),
            (Decimal("1e21"), " sextillion"),
            (Decimal("1e18"), " quintillion"),
            (Decimal("1e15"), " quadrillion"),
            (Decimal("1e12"), " trillion"),
            (Decimal("1e9"), " billion"),
            (Decimal("1e6"), " million"),
            (Decimal("1e3"), " thousand"),
        ]

        for threshold, suffix in suffixes:
            if value >= threshold:
                num = self._fmt_no_sci(value / threshold, max_frac=2)
                out = f"{num}{suffix}"
                return f"-{out}" if negative else out

        num = self._fmt_no_sci(value, max_frac=2)
        return f"-{num}" if negative else num

    async def short_formatter(self, value: Decimal) -> str:
        """Currency-friendly short format (e.g., 1000000 -> '1 mil')."""
        negative = value < 0
        value = abs(value)

        suffixes = [
            (Decimal("1e33"), " dec"),
            (Decimal("1e30"), " non"),
            (Decimal("1e27"), " oct"),
            (Decimal("1e24"), " sept"),
            (Decimal("1e21"), " sext"),
            (Decimal("1e18"), " quin"),
            (Decimal("1e15"), " quad"),
            (Decimal("1e12"), " tril"),
            (Decimal("1e9"), " bil"),
            (Decimal("1e6"), " mil"),
            (Decimal("1e3"), "k"),
        ]

        for threshold, suffix in suffixes:
            if value >= threshold:
                num = self._fmt_no_sci(value / threshold, max_frac=2)
                out = f"{num}{suffix}"
                return f"-{out}" if negative else out

        num = self._fmt_no_sci(value, max_frac=2)
        return f"-{num}" if negative else num

    async def amount_handler(self, amount_input: str, user_balance: Decimal) -> Decimal:
        """
        Process the bet input and return the corresponding bet amount.
        Supports keywords ('all', 'half', 'quarter'), percentages, and suffixed values.
        Uses ROUND_DOWN for 'all'/'max' keywords, ROUND_HALF_UP for other amounts.
        Rejects amounts below the minimum currency threshold (0.01).
        """

        if not isinstance(amount_input, str):
            raise ValueError("Invalid amount input type.")

        amount_input = amount_input.strip().lower()

        if amount_input == "all" or amount_input == "max":
            amount = AmountUtils.truncate_currency(user_balance)
        elif amount_input == "half":
            amount = AmountUtils.round_currency(user_balance / Decimal("2"))
        elif amount_input == "quarter":
            amount = AmountUtils.round_currency(user_balance / Decimal("4"))

        elif amount_input.endswith("%"):
            percentage_match = re.match(r"^([0-9]+(\.[0-9]+)?)%$", amount_input)
            if percentage_match:
                try:
                    percentage = Decimal(percentage_match.group(1))
                    if Decimal("1") <= percentage <= Decimal("100"):
                        amount = AmountUtils.round_currency(user_balance * (percentage / Decimal("100")))
                    else:
                        raise ValueError("Percentage must be between 1% and 100%.")
                except InvalidOperation:
                    raise ValueError("Invalid percentage value.")
            else:
                raise ValueError("Invalid percentage format.")
        else:
            multipliers = {
                "k": Decimal("1000"),
                "m": Decimal("1000000"),
                "b": Decimal("1000000000"),
                "t": Decimal("1000000000000"),
                "q": Decimal("1000000000000000"),
                "qu": Decimal("1000000000000000000"),
                "s": Decimal("1000000000000000000000"),
                "se": Decimal("1000000000000000000000000"),
                "o": Decimal("1000000000000000000000000000"),
                "n": Decimal("1000000000000000000000000000000"),
                "d": Decimal("1000000000000000000000000000000000"),
            }

            multiplier_match = re.match(
                r"^([0-9]+(\.[0-9]+)?)(k|m|b|t|q|qu|s|se|o|n|d)?$", amount_input
            )
            if not multiplier_match:
                raise ValueError("Invalid amount format.")

            try:
                number = Decimal(multiplier_match.group(1))
                if multiplier_match.group(3):
                    multiplier = multipliers[multiplier_match.group(3)]
                    amount = AmountUtils.round_currency(number * multiplier)
                else:
                    amount = AmountUtils.round_currency(number)
            except (InvalidOperation, KeyError):
                raise ValueError("Invalid amount.")

        if amount.is_nan():
            raise ValueError("Invalid amount.")

        if amount > user_balance:
            raise ValueError("Insufficient Funds.")

        valid, error_msg = AmountUtils.validate_currency_minimum(amount)
        if not valid:
            raise ValueError(error_msg)

        return amount

    @tasks.loop(minutes=10)
    async def validate_economy_task(self):
        try:
            valid = await self.bot.database.validate_economy()
        except ValueError as e:
            await self.bot.database.initialize_supply_record()
            logger.error(f"ValueError validating economy: {e}")
            valid = False
        except Exception as e:
            logger.error(f"Error validating economy: {e}")
            valid = False

        if valid:
            logger.info("Successfully validated the economy.")
        else:
            logger.warning("Economy validation failed; see earlier error logs for details.")

    @validate_economy_task.before_loop
    async def before_validate_economy_task(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def fire_inactive_employees_task(self):
        """Fire employees who haven't worked in 48+ hours."""
        try:
            employees_to_fire = await self.bot.database.get_employees_for_firing()
            for job in employees_to_fire:
                raw_user_id = await resolve_id(self.bot.database,job.user_id)
                if raw_user_id:
                    await self.bot.database.fire_employee(raw_user_id)
                    logger.info(f"Fired employee {raw_user_id} from {job.title} for inactivity")
        except Exception as e:
            logger.error(f"Error firing inactive employees: {e}")

    @tasks.loop(minutes=5)
    async def cleanup_expired_effects_task(self):
        """Clean up expired effects from the database."""
        try:
            removed = await self.bot.database.cleanup_expired_effects()
            if removed > 0:
                logger.info(f"Cleaned up {removed} expired effect(s)")
        except Exception as e:
            logger.error(f"Error cleaning up expired effects: {e}")

    @cleanup_expired_effects_task.before_loop
    async def before_cleanup_expired_effects_task(self):
        await self.bot.wait_until_ready()

    @fire_inactive_employees_task.before_loop
    async def before_fire_inactive_employees_task(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(
        name="balance", aliases=["bal"], description="Check your current balance."
    )
    @unified_cooldown(10)
    async def balance(self, ctx: commands.Context, member: discord.Member = None):
        """Check your current balance."""
        try:
            member = member or ctx.author
            wallet_id = await self.bot.database.get_wallet_id_for_user(member.id)

            wallet_balance = await self.bot.database.get_wallet_balance_by_user_id(member.id)
            wallet_balance = Decimal(wallet_balance)
            wallet_balance = wallet_balance if wallet_balance is not None else 0

            bank_balance = await self.bot.database.get_bank_balance(wallet_id)
            bank_balance = Decimal(bank_balance)
            bank_balance = bank_balance if bank_balance is not None else 0

            assets = await self.bot.database.get_crypto_assets(member.id)
            filtered = [a for a in assets if a.amount >= Decimal("0.01")]


            color = discord.Color.blurple()
            if not isinstance(ctx.channel, discord.DMChannel):
                try:
                    top_role = ctx.author.top_role
                    if top_role and top_role.color:
                        color = top_role.color
                except Exception:
                    color = discord.Color.blurple()
            embed = discord.Embed(

                color=color,
            )
            embed.set_author(
                name=f"{member.display_name}'s Balance",
                icon_url=self.utils.get_avatar_url(member),
            )




            wallet_str = f"{self.currency_name} **{await self.short_formatter(wallet_balance)}**"
            bank_str = f"{self.currency_name} **{await self.short_formatter(bank_balance)}**"

            embed.add_field(
                name="Wallet",
                value=wallet_str,
                inline=False
            )
            embed.add_field(
                name="Bank",
                value=bank_str,
                inline=False
            )

            # Add Assets button if user has crypto assets
            view = None
            if filtered:
                view = BalanceView(self, member, ctx.author, embed)

            # Fetch last 5 transactions
            user_transactions = await self.bot.database.get_transactions_by_user_id(
                member.id, limit=5
            )

            if user_transactions:
                transactions_text = []
                tx_ids = []
                for tx in user_transactions:
                    tx_ids.extend([getattr(tx, "from_user_id", None), getattr(tx, "to_user_id", None)])
                resolved_tx_ids = await resolve_ids(self.bot.database,tx_ids)

                for tx in user_transactions:
                    amount = Decimal(tx.amount) if tx.amount else Decimal("0")
                    formatted_amount = await self.short_formatter(amount)
                    description = tx.description if tx.description else "No description"
                    raw_from = resolved_tx_ids.get(getattr(tx, "from_user_id", None))
                    raw_to = resolved_tx_ids.get(getattr(tx, "to_user_id", None))

                    if raw_from == member.id and raw_to != member.id:
                        direction = "📤"
                    elif raw_from != member.id and raw_to == member.id:
                        direction = "📥"
                    else:
                        direction = "🔄"
                    
                    # Truncate description if too long
                    if len(description) > 25:
                        description = description[:22] + "..."
                    
                    tx_txt = f"{direction} **{formatted_amount}** • {description}"

                    transactions_text.append(
                        tx_txt
                    )
                
                embed.add_field(
                    name="Recent Transactions",
                    value="\n".join(transactions_text),
                    inline=False
                )

            embed.set_footer(
                text=f"Total Balance: {await self.formatter(wallet_balance + bank_balance)}"
            )

            if view:
                await ctx.reply(embed=embed, view=view)
            else:
                await ctx.reply(embed=embed)
        except ValueError as e:
            await Embeds.error(
                ctx,
                str(e.args[0]),
                delete_after=5,
                reply=True,
                author={"name": "Amount Error", "icon_url": self.utils.get_avatar_url(ctx.author)},
            )

    @commands.hybrid_command(
        name="leaderboard",
        aliases=["lb"],
        description="View the top 10 users by net balance.",
    )
    @unified_cooldown(10)
    async def leaderboard(self, ctx: commands.Context):
        """Displays the top 10 users by net balance."""
        top_users = await self.bot.database.get_top_balance_users(limit=10)
        embed = discord.Embed(
            color=ctx.author.top_role.color
            if ctx.author.top_role
            else discord.Color.blurple()
        )

        if top_users:
            top_list = []
            rank_emojis = ["<:crown:1360657246165537011>"] + [
                f"{idx}." for idx in range(2, 11)
            ]
            resolved_users = await resolve_ids(self.bot.database,[uid for uid, _ in top_users])
            for idx, (user_id, total_balance) in enumerate(top_users):
                raw_id = resolved_users.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    display_name = "Unknown user"
                emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."


                lb_str = f"{emoji} **{display_name}** (`{await self.short_formatter(total_balance)}`)"

                top_list.append(lb_str)

            embed.add_field(
                name="Top 10 Users by Net Balance",
                value="\n".join(top_list),
                inline=False,
            )
        else:
            embed.add_field(
                name="Top 10 Users by Net Balance",
                value="No data available",
                inline=False,
            )

        embed.set_author(
            name="Economy Leaderboard", icon_url=self.utils.get_avatar_url(ctx.author)
        )

        await ctx.reply(embed=embed)

    @commands.group(
        name="economy", aliases=["eco", "econ"], invoke_without_command=True
    )
    async def economy(self, ctx: commands.Context):
        """Fetch and display economy statistics."""
        if ctx.invoked_subcommand is None:
            try:
                wallet_id = await self.bot.database.get_wallet_id_for_user(ctx.author.id)
                treasury_balance = await self.bot.database.get_treasury_balance()
                supply = await self.bot.database.get_supply_record()
                balance = await self.bot.database.get_wallet_balance_by_user_id(
                    ctx.author.id
                )
                balance = Decimal(balance)
                bank_balance = await self.bot.database.get_bank_balance(wallet_id)
                bank_balance = Decimal(bank_balance)
                balance = balance if balance is not None else 0
                bank_balance = bank_balance if bank_balance is not None else 0

                # Get new economic factors
                economic_factors = await self.bot.database.get_economic_factors()

                embed = discord.Embed(
                    title="📊 Economy Overview",
                    description="Current state of the server economy",
                    color=discord.Color.blurple()
                )

                # Supply section
                embed.add_field(
                    name="💵 Supply",
                    value=f"**Total:** {self.currency_name} {await self.formatter(supply.total_supply)}\n"
                          f"**Circulating:** {self.currency_name} {await self.formatter(supply.circulating)}",
                    inline=True,
                )
                embed.add_field(
                    name="🏦 Treasury",
                    value=f"{self.currency_name} **{await self.formatter(treasury_balance)}**",
                    inline=True,
                )

                total_supply = supply.total_supply
                percentage = (
                    ((balance + bank_balance) / total_supply) * 100
                    if total_supply > 0
                    else 0
                )

                # Rates section
                fee_pct = economic_factors['fee_rate'] * 100
                passive_pct = economic_factors['passive_income_rate'] * 100
                embed.add_field(
                    name="⚙️ Rates",
                    value=f"**Fee:** {fee_pct:.2f}%\n**Passive Income:** {passive_pct:.2f}%",
                    inline=True,
                )

                # Stats section
                global_wins = await self.bot.database.get_global_wins()
                global_losses = await self.bot.database.get_global_losses()
                win_rate = (global_wins / (global_wins + global_losses) * 100) if (global_wins + global_losses) > 0 else 0
                embed.add_field(
                    name="🎲 Global Stats",
                    value=f"**Wins:** {global_wins:,}\n**Losses:** {global_losses:,}\n**Win Rate:** {win_rate:.1f}%",
                    inline=True,
                )
                embed.add_field(
                    name="💼 Your Portfolio",
                    value=f"**{percentage:.4f}%** of total supply\n({self.currency_name} {await self.formatter(balance + bank_balance)})",
                    inline=True,
                )

                embed.set_footer(text="💡 Use !economy health for detailed analysis • !economy trends for historical data")

                await ctx.reply(embed=embed)

            except Exception as e:
                await ctx.reply(
                    f"🚫 Error fetching economy stats, Please try again later.",
                    delete_after=5,
                )
                logger.error(f"Error fetching economy stats: {e}")

    @economy.command(
        name="trends", aliases=["trend"], description="View economic trends over time."
    )
    @unified_cooldown(10)
    async def economy_trends(self, ctx: commands.Context, days: int = 30):
        """Display economic trends over the specified number of days."""
        if not hasattr(self.bot, 'database'):
            return await ctx.send("❌ Database not available.")

        if days > 365:
            return await ctx.send("❌ Maximum trend period is 365 days.")

        try:
            trends = await self.bot.database.get_economic_trends(days)

            if "error" in trends:
                return await ctx.send(f"❌ {trends['error']}")

            embed = discord.Embed(
                title=f"📈 Economic Trends ({days} days)",
                description=f"Data points: {trends['data_points']}",
                color=discord.Color.green()
            )

            # Add metrics with their trends
            for metric_name, metric_data in trends['metrics'].items():
                # Format metric name to be more readable
                formatted_name = metric_name.replace('_', ' ').title()

                # Determine trend indicator
                if metric_data['trend_slope'] > 0:
                    trend_indicator = "↗️"
                elif metric_data['trend_slope'] < 0:
                    trend_indicator = "↘️"
                else:
                    trend_indicator = "➡️"

                # Format the value appropriately
                if metric_name in ['treasury_health', 'liquidity_ratio']:
                    current_val = f"{metric_data['current']*100:.2f}%"
                    avg_val = f"{metric_data['average']*100:.2f}%"
                else:
                    current_val = f"{metric_data['current']:.4f}"
                    avg_val = f"{metric_data['average']:.4f}"

                value_text = (
                    f"Current: {current_val} {trend_indicator}\n"
                    f"Average: {avg_val}\n"
                    f"Range: {metric_data['min']:.4f} - {metric_data['max']:.4f}"
                )

                embed.add_field(
                    name=formatted_name,
                    value=value_text,
                    inline=True
                )

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error getting economic trends: {e}")
            await ctx.send("❌ Error retrieving trends. Please try again.")

    @economy.command(
        name="health", aliases=["status"], description="Get the overall economic health score."
    )
    @unified_cooldown(10)
    async def economy_health(self, ctx: commands.Context):
        """Display the overall economic health score and breakdown with personalized recommendations."""
        if not hasattr(self.bot, 'database'):
            return await ctx.send("❌ Database not available.")

        health_data = await self.bot.database.get_economic_health_score()
        recommendations = await self.bot.database.get_personalized_economic_recommendations(ctx.author.id)

        embed = discord.Embed(
            title="🏥 Economic Health Score",
            description=f"Overall Score: **{health_data['score']}/100** ({health_data['status']})",
            color=discord.Color.orange()
        )

        # Add component scores
        for component_name, component_data in health_data['components'].items():
            formatted_name = component_name.replace('_', ' ').title()
            value_text = (
                f"Value: {component_data['value']}%\n"
                f"Score: {component_data['score']}/{component_data['weight']}"
            )

            # Color coding for health
            if component_data['score'] / component_data['weight'] > 0.8:
                field_color = "🟢"
            elif component_data['score'] / component_data['weight'] > 0.6:
                field_color = "🟡"
            elif component_data['score'] / component_data['weight'] > 0.4:
                field_color = "🟠"
            else:
                field_color = "🔴"

            embed.add_field(
                name=f"{field_color} {formatted_name}",
                value=value_text,
                inline=True
            )

        # Add recommendations section
        if recommendations.get('recommendations'):
            rec_text = ""
            for rec in recommendations['recommendations']:
                priority_emoji = {"high": "🔴", "medium": "🟡", "info": "🔵"}
                emoji = priority_emoji.get(rec.get('priority', 'info'), "🔹")
                rec_text += f"{emoji} {rec['message']}\n"

            embed.add_field(
                name="💡 Recommendations",
                value=rec_text.strip(),
                inline=False
            )

        embed.set_footer(text="Higher scores indicate better economic health")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="daily", description="Claim your daily reward.")
    @unified_cooldown(86400)
    async def daily(self, ctx: commands.Context):
        """Receive a daily reward."""
        try:
            # Generate base reward between 15000 and 70000
            base_amount = secrets.randbelow(55000 - 15000) + 15000
            
            # Apply dynamic reward multiplier based on economic conditions
            multiplier = await self.bot.database.get_dynamic_reward_multiplier()
            earning_multiplier = await self.bot.database.get_earning_multiplier(ctx.author.id)
            daily_amount = int((Decimal(base_amount) * multiplier * earning_multiplier).quantize(Decimal("1")))

            wallet_id = await self.bot.database.get_wallet_id_for_user(ctx.author.id)
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=Decimal(daily_amount),
                    description="Daily Reward",
                )
            except ValueError as e:
                await Embeds.error(
                    ctx,
                    f"🚫 Transaction failed: {e}",
                    delete_after=5,
                    reply=True,
                )
                return
                return
            color = discord.Color.blurple()
            if isinstance(ctx.channel, discord.DMChannel):
                color = discord.Color.blurple()
            else:
                color = (
                    ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple()
                )
            await self.bot.database.add_reputation_score(ctx.author.id, 1)

            # Build embed with multiplier info
            multiplier_parts = []
            if multiplier != Decimal("1.0"):
                multiplier_parts.append(f"Econ ×{multiplier:.2f}")
            if earning_multiplier != Decimal("1.0"):
                multiplier_parts.append(f"Boost ×{earning_multiplier:.2f}")
            multiplier_text = f" ({', '.join(multiplier_parts)})" if multiplier_parts else ""
            embed = discord.Embed(
                description=f"You received your daily reward of {self.currency_name} **{await self.formatter(daily_amount)}**{multiplier_text}!",
                color=color,
            )
            if multiplier_parts:
                embed.add_field(
                    name="Multipliers",
                    value=f"Base: {self.currency_name} {await self.formatter(base_amount)}" + (
                        f" × {multiplier:.2f}" if multiplier != Decimal("1.0") else ""
                    ) + (
                        f" × {earning_multiplier:.2f}" if earning_multiplier != Decimal("1.0") else ""
                    ),
                    inline=False,
                )
            embed.set_author(
                name="Daily", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            await ctx.reply(embed=embed)
        except ValueError as e:
            await Embeds.error(
                ctx,
                str(e.args[0]),
                delete_after=5,
                reply=True,
                author={"name": "Amount Error", "icon_url": self.utils.get_avatar_url(ctx.author)},
            )

    @commands.hybrid_command(name="weekly", description="Claim your weekly reward.")
    @unified_cooldown(604800)
    async def weekly(self, ctx: commands.Context):
        """Receive a weekly reward."""
        try:
            # Generate base reward between 110000 and 310000
            base_amount = secrets.randbelow(310000 - 110000) + 110000
            
            # Apply dynamic reward multiplier based on economic conditions
            multiplier = await self.bot.database.get_dynamic_reward_multiplier()
            earning_multiplier = await self.bot.database.get_earning_multiplier(ctx.author.id)
            weekly_amount = int((Decimal(base_amount) * multiplier * earning_multiplier).quantize(Decimal("1")))

            wallet_id = await self.bot.database.get_wallet_id_for_user(ctx.author.id)
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=Decimal(weekly_amount),
                    description="Weekly Reward",
                )
            except ValueError as e:
                await Embeds.error(
                    ctx,
                    f"🚫 Transaction failed: {e}",
                    delete_after=5,
                    reply=True,
                )
                return
                return

            color = discord.Color.blurple()
            if isinstance(ctx.channel, discord.DMChannel):
                color = discord.Color.blurple()
            else:
                color = (
                    ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple()
                )
            await self.bot.database.add_reputation_score(ctx.author.id, 2)

            # Build embed with multiplier info
            multiplier_parts = []
            if multiplier != Decimal("1.0"):
                multiplier_parts.append(f"Econ ×{multiplier:.2f}")
            if earning_multiplier != Decimal("1.0"):
                multiplier_parts.append(f"Boost ×{earning_multiplier:.2f}")
            multiplier_text = f" ({', '.join(multiplier_parts)})" if multiplier_parts else ""
            embed = discord.Embed(
                description=f"You received your weekly reward of {self.currency_name} **{await self.formatter(weekly_amount)}**{multiplier_text}!",
                color=color,
            )
            if multiplier_parts:
                embed.add_field(
                    name="Multipliers",
                    value=f"Base: {self.currency_name} {await self.formatter(base_amount)}" + (
                        f" × {multiplier:.2f}" if multiplier != Decimal("1.0") else ""
                    ) + (
                        f" × {earning_multiplier:.2f}" if earning_multiplier != Decimal("1.0") else ""
                    ),
                    inline=False,
                )
            embed.set_author(
                name="Weekly", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            await ctx.reply(embed=embed)
        except ValueError as e:
            await Embeds.error(
                ctx,
                str(e.args[0]),
                delete_after=5,
                reply=True,
                author={"name": "Amount Error", "icon_url": self.utils.get_avatar_url(ctx.author)},
            )

    @commands.hybrid_command(name="monthly", description="Claim your monthly reward.")
    @unified_cooldown(2592000)
    async def monthly(self, ctx: commands.Context):
        """Receive a monthly reward."""
        try:
            base_amount = secrets.randbelow(9799990 - 3399990) + 3399990
            multiplier = await self.bot.database.get_dynamic_reward_multiplier()
            earning_multiplier = await self.bot.database.get_earning_multiplier(ctx.author.id)
            monthly_amount = int((Decimal(base_amount) * multiplier * earning_multiplier).quantize(Decimal("1")))
            wallet_id = await self.bot.database.get_wallet_id_for_user(ctx.author.id)
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=Decimal(monthly_amount),
                    description="Monthly Reward",
                )
            except ValueError as e:
                await Embeds.error(
                    ctx,
                    f"🚫 Transaction failed: {e}",
                    delete_after=5,
                    reply=True,
                )
                return
                return
            color = discord.Color.blurple()
            if isinstance(ctx.channel, discord.DMChannel):
                color = discord.Color.blurple()
            else:
                color = (
                    ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple()
                )
            await self.bot.database.add_reputation_score(ctx.author.id, 5)
            multiplier_parts = []
            if multiplier != Decimal("1.0"):
                multiplier_parts.append(f"Econ ×{multiplier:.3f}")
            if earning_multiplier != Decimal("1.0"):
                multiplier_parts.append(f"Boost ×{earning_multiplier:.3f}")
            multiplier_text = f" ({', '.join(multiplier_parts)})" if multiplier_parts else ""
            embed = discord.Embed(
                description=f"Your monthly reward is **{self.currency_name} {await self.formatter(monthly_amount)}**.{multiplier_text}",
                color=color,
            )
            embed.set_author(
                name="Monthly", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            if multiplier_parts:
                embed.add_field(
                    name="Multipliers",
                    value=f"Base: {await self.formatter(base_amount)}" + (
                        f" × {multiplier:.3f}" if multiplier != Decimal("1.0") else ""
                    ) + (
                        f" × {earning_multiplier:.3f}" if earning_multiplier != Decimal("1.0") else ""
                    ),
                    inline=False
                )
            await ctx.reply(embed=embed)
        except ValueError as e:
            await Embeds.error(
                ctx,
                str(e.args[0]),
                delete_after=5,
                reply=True,
                author={"name": "Amount Error", "icon_url": self.utils.get_avatar_url(ctx.author)},
            )

    @commands.hybrid_command(name="beg", description="Beg for money. Maybe you'll get lucky!")
    @unified_cooldown(3)
    async def beg(self, ctx: commands.Context):
        """Beg for money. Maybe you'll get lucky!"""

        names_data = [
            ("Dennis", 5, 2.0, 3.5),
            ("Googly (Idiot 🤡)", 5, 2.0, 3.5),
            ("Lenny", 5, 2.0, 3.5),
            ("Shogani", 5, 2.0, 3.5),
            ("G Money", 10, 1.5, 1.5),
            ("Lil Bibby", 10, 1.2, 1.5),
            ("Pete", 10, 1.0, 1.3),
            ("Ally Lotti", 10, 1.0, 1.1),
            ("Chris Long", 10, 1.0, 1.2),
            ("DJ Relentt", 8, 1.7, 2.5),
            ("DJ Scheme", 8, 1.7, 2.5),
            ("Juice WRLD", 3, 3.0, 5.0),
            ("Drake", 4, 2.5, 4.0),
            ("Lil Durk", 6, 1.8, 3.0),
            ("King Von", 6, 1.8, 3.0),
            ("Chief Keef", 6, 1.8, 3.0),
            ("Seezyn", 8, 1.7, 2.5),
            ("Lil Uzi Vert", 3, 3.0, 5.0),
            ("Playboi Carti", 3, 3.0, 5.0),
            ("Young Thug", 3, 3.0, 5.0),
            ("Gunna", 6, 1.8, 3.0),
            ("CqllMeToxic", 10, 1.0, 1.2),
            ("Mysterious Stranger", 1, 7.5, 10.0),
        ]

        user_id = ctx.author.id
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)

        # Weighted random selection
        total_weight = sum(weight for _, weight, _, _ in names_data)
        rand_val = await self.fair_randbelow(user_id, total_weight, tag="beg_name")
        
        cumulative = 0
        selected_entry = names_data[0]
        for entry in names_data:
            cumulative += entry[1]
            if rand_val < cumulative:
                selected_entry = entry
                break
        
        name, _, min_mult, max_mult = selected_entry

        positive_interactions = [
            f"**{name}** smiles and says, 'Here, take this. It's not much, but it should help.'",
            f"**{name}** looks at you sympathetically and hands you a few coins. 'Hang in there,' they say.",
            f"'You look like you could use this,' **{name}** says, giving you some spare change.",
            f"**{name}** laughs, 'I was going to buy a coffee, but you need this more than I do.'",
            f"'I hope this helps,' **{name}** says, pressing some money into your hand.",
            f"**{name}** digs into their pocket and pulls out a crumpled bill. 'Here, it's yours,' they say.",
            f"'I don't usually give out money, but you seem like a good person,' **{name}** says as they hand you some cash.",
            f"**{name}** winks and slips you some money. 'Don't spend it all in one place,' they joke.",
            f"'It's not much, but it's something,' **{name}** says, offering you a small amount.",
            f"**{name}** passes by and drops some change in your hand with a nod of encouragement.",
            f"**{name}** stops and says, 'You remind me of myself when I was younger,' before handing you some cash.",
            f"'Today's your lucky day,' **{name}** says cheerfully as they give you some money.",
            f"**{name}** reaches into their wallet and says, 'I just got paid, here you go.'",
            f"'Everyone needs help sometimes,' **{name}** says kindly while giving you some coins.",
            f"**{name}** tosses you some bills and says, 'Put this to good use, alright?'",
            f"'I've been where you are,' **{name}** says softly before offering you some money.",
            f"**{name}** grins and says, 'Consider this a gift from the universe,' as they hand you cash.",
            f"'You've got kind eyes,' **{name}** remarks before dropping some money in your hand.",
            f"**{name}** pulls out their phone, then reconsiders and gives you the cash instead.",
            f"'My grandma always told me to help others,' **{name}** says while handing you some bills.",
        ]

        negative_interactions = [
            f"**{name}** walks past you without even making eye contact. Tough luck.",
            f"**{name}** frowns and says, 'Get a job,' before walking away.",
            f"'Sorry, I don't have any spare change,' **{name}** mutters as they hurry off.",
            f"**{name}** pretends not to hear you and continues on their way.",
            f"'Not today, buddy,' **{name}** says, shaking their head and moving on.",
            f"**{name}** gives you a look of disdain and ignores your request.",
            f"'You think I'm made of money?' **{name}** scoffs and walks away.",
            f"**{name}** just shrugs and says, 'Maybe next time,' as they keep walking.",
            f"'I can't help you,' **{name}** says bluntly before disappearing into the crowd.",
            f"**{name}** gives you a cold stare and continues on their way without a word.",
            f"**{name}** looks you dead in the eye and screams, 'TOXIC HUMANS IS NEVER COMING'",
            f"**{name}** pulls out their earbuds just to say 'No' before putting them back in.",
            f"'I'm broke too,' **{name}** claims while clearly holding a designer bag.",
            f"**{name}** laughs mockingly and says, 'Nice try, but I'm not falling for that.'",
            f"'Go ask someone else,' **{name}** says dismissively without breaking stride.",
            f"**{name}** crosses to the other side of the street to avoid you.",
            f"'I don't carry cash,' **{name}** lies while their wallet visibly bulges in their pocket.",
            f"**{name}** rolls their eyes and mutters something under their breath as they pass.",
            f"'The audacity,' **{name}** whispers loudly before speed-walking away.",
            f"**{name}** pretends to be on an important phone call and rushes past you.",
        ]

        seq = [True] * 4 + [False] * 6
        is_successful = await self.fair_choice(user_id, seq, tag="beg_success")

        try:
            if is_successful:
                response = secrets.choice(positive_interactions)
                base_amount = secrets.randbelow(6000) + 200
                
                # Apply character multiplier
                char_multiplier = await self.fair_uniform(user_id, min_mult, max_mult)
                amount = Decimal(base_amount) * Decimal(str(char_multiplier))
                
                # Apply dynamic economic multiplier
                economic_multiplier = await self.bot.database.get_dynamic_reward_multiplier()
                amount = amount * economic_multiplier
                amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id, amount=amount, description="Beg"
                    )
                except ValueError as e:
                    await Embeds.error(ctx, f"🚫 Transaction failed: {e}", delete_after=5, reply=True)
                    return
                color = discord.Color.blurple()
                if isinstance(ctx.channel, discord.DMChannel):
                    color = discord.Color.blurple()
                else:
                    color = (
                        ctx.author.top_role.color
                        if ctx.author.top_role
                        else discord.Color.blurple()
                    )
                
                # Build multiplier text
                multiplier_parts = []
                if char_multiplier > 1.0:
                    multiplier_parts.append(f"×{char_multiplier:.2f}")
                if economic_multiplier != Decimal("1.0"):
                    multiplier_parts.append(f"Econ×{economic_multiplier:.2f}")
                multiplier_text = f" ({', '.join(multiplier_parts)})" if multiplier_parts else ""
                
                embed = discord.Embed(
                    description=f"{response}\n\n**{name}** gave you {self.currency_name} **{await self.formatter(amount)}**{multiplier_text}.",
                    color=color,
                )
                embed.set_author(
                    name="Beg",
                    icon_url=self.utils.get_avatar_url(ctx.author),
                )
            else:
                response = secrets.choice(negative_interactions)

                embed = discord.Embed(
                    description=f"{response}\n\n**{name}** completely ignored you.",
                    color=discord.Color.red(),
                )
                embed.set_author(
                    name="Beg",
                    icon_url=self.utils.get_avatar_url(ctx.author),
                )
        except ValueError as e:
            await Embeds.error(ctx, str(e), delete_after=5, reply=True)

        await ctx.reply(embed=embed)

    # Predefined job definitions
    JOBS = {
        "janitor": {"title": "Janitor", "base_salary": Decimal("500000")},
        "cashier": {"title": "Cashier", "base_salary": Decimal("750000")},
        "developer": {"title": "Developer", "base_salary": Decimal("1500000")},
        "manager": {"title": "Manager", "base_salary": Decimal("2000000")},
        "executive": {"title": "Executive", "base_salary": Decimal("3000000")},
    }

    @commands.group(name="job", invoke_without_command=True)
    async def job(self, ctx: commands.Context):
        """Group command for jobs."""
        if ctx.invoked_subcommand is None:
            await Embeds.custom(
                ctx,
                "Available job commands:\n"
                "• `!job list` - View all available jobs\n"
                "• `!job apply <job>` - Apply for a job\n"
                "• `!job work` - Work to earn your salary\n"
                "• `!job info` - View your job details\n\n"
                "⚠️ **Warning:** If you don't work for more than 48 hours, you'll be fired!",
                color=discord.Color.blurple(),
                author={"name": "Jobs", "icon_url": self.utils.get_avatar_url(ctx.author)},
                delete_after=None,
                reply=True,
                mention_author=True,
            )

    @job.command(name="list", description="View all available jobs.")
    @unified_cooldown(10)
    async def job_list(self, ctx: commands.Context):
        """List all available jobs with their base salaries."""
        color = (
            discord.Color.blurple()
            if isinstance(ctx.channel, discord.DMChannel)
            else (
                ctx.author.top_role.color
                if ctx.author.top_role
                else discord.Color.blurple()
            )
        )
        lines = []
        for job_key, job_data in self.JOBS.items():
            lines.append(f"**{job_data['title']}** (`{job_key}`) - Base Salary: {self.currency_name} **{await self.formatter(job_data['base_salary'])}**")

        await Embeds.custom(
            ctx,
            "\n".join(lines),
            title="Available Jobs",
            color=color,
            footer="Use !job apply <job_name> to apply for a job",
            delete_after=None,
            reply=True,
            mention_author=True,
        )

    @job.command(name="apply", description="Apply for a job to earn some money.")
    @unified_cooldown(86400)
    async def job_apply(self, ctx: commands.Context, job_name: str):
        """Apply for a job."""
        user_id = ctx.author.id
        job_name = job_name.lower()

        # Check for existing job first
        existing_job = await self.bot.database.get_job(user_id)
        if existing_job:
            return await Embeds.error(ctx, "You already have a job. Use `!job work` to earn your salary.\nIf you miss work for 48 hours, you'll be fired automatically.", delete_after=5, reply=True)

        if job_name not in self.JOBS:
            valid_jobs = ", ".join(self.JOBS.keys())
            return await Embeds.error(ctx, f"Invalid job. Available jobs: {valid_jobs}", delete_after=5, reply=True)

        job_data = self.JOBS[job_name]

        # Hiring chance based on job tier (higher salary = lower chance)
        hire_chances = {
            "janitor": 0.80,
            "cashier": 0.65,
            "developer": 0.45,
            "manager": 0.30,
            "executive": 0.15,
        }
        hire_chance = hire_chances.get(job_name, 0.50)

        # Roll for hiring (roll < hire_chance means success)
        roll = secrets.randbelow(100) / 100
        if roll < hire_chance:
            color = (
                discord.Color.blurple()
                if isinstance(ctx.channel, discord.DMChannel)
                else (
                    ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple()
                )
            )
            await self.bot.database.apply_for_job(
                user_id=user_id,
                job_title=job_data["title"],
                base_salary=job_data["base_salary"],
            )
            await Embeds.custom(
                ctx,
                f"You were hired as a **{job_data['title']}**!\n"
                f"Base Salary: {self.currency_name} **{await self.formatter(job_data['base_salary'])}**\n"
                f"Use `!job work` to collect your salary daily.\n\n"
                f"⚠️ **Warning:** If you don't work for 48 hours, you'll be fired!",
                color=color,
                author={"name": "Job Applied", "icon_url": self.utils.get_avatar_url(ctx.author)},
                delete_after=None,
                reply=True,
                mention_author=True,
            )
        else:
            await Embeds.warning(
                ctx,
                f"Unfortunately, you were not hired as a **{job_data['title']}**. "
                f"The position was filled by another candidate.\n\n"
                f"You can apply again in 24 hours.",
                author={"name": "Application Rejected", "icon_url": self.utils.get_avatar_url(ctx.author)},
                delete_after=None,
                reply=True,
                mention_author=True,
            )

    @job.command(name="work", description="Work to earn your salary (24h cooldown).")
    @unified_cooldown(86400)
    async def job_work(self, ctx: commands.Context):
        """Work at your job to earn salary."""
        user_id = ctx.author.id

        try:
            job, salary = await self.bot.database.work_job(user_id)

            # Apply dynamic economic multiplier and earning boost
            economic_multiplier = await self.bot.database.get_dynamic_reward_multiplier()
            earning_multiplier = await self.bot.database.get_earning_multiplier(user_id)
            salary = salary * economic_multiplier * earning_multiplier
            salary = salary.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # Pay the user via treasury
            wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=salary,
                description=f"Job Salary: {job.title}",
            )

            color = (
                discord.Color.blurple()
                if isinstance(ctx.channel, discord.DMChannel)
                else (
                    ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple()
                )
            )

            # Calculate tenure bonus display
            weeks_employed = job.days_employed / 7
            salary_multiplier = min(2.0, 1.0 + (weeks_employed * 0.05))

            # Build multiplier text
            multiplier_parts = []
            if salary_multiplier > 1.0:
                multiplier_parts.append(f"×{salary_multiplier:.2f}")
            if economic_multiplier != Decimal("1.0"):
                multiplier_parts.append(f"Econ×{economic_multiplier:.2f}")
            if earning_multiplier != Decimal("1.0"):
                multiplier_parts.append(f"Boost×{earning_multiplier:.2f}")
            multiplier_text = f" ({', '.join(multiplier_parts)})" if multiplier_parts else ""

            await Embeds.custom(
                ctx,
                f"You worked as a **{job.title}** and earned {self.currency_name} **{await self.formatter(salary)}**{multiplier_text}\n\n"
                f"**Streak:** {job.streak} consecutive days\n"
                f"**Tenure:** {job.days_employed} days employed",
                color=color,
                delete_after=None,
                reply=True,
                mention_author=True,
            )

        except ValueError as e:
            await Embeds.error(ctx, str(e), delete_after=5, reply=True)

    @job.command(name="info", description="View your current job details.")
    @unified_cooldown(10)
    async def job_info(self, ctx: commands.Context):
        """View your current job information."""
        user_id = ctx.author.id

        try:
            job = await self.bot.database.get_job(user_id)
            if not job:
                return await Embeds.error(ctx, "You don't have a job. Use `!job list` to see available jobs.", delete_after=5, reply=True)

            current_salary = await self.bot.database.calculate_salary(job)
            weeks_employed = job.days_employed / 7
            salary_multiplier = min(2.0, 1.0 + (weeks_employed * 0.05))

            color = (
                discord.Color.blurple()
                if isinstance(ctx.channel, discord.DMChannel)
                else (
                    ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple()
                )
            )

            embed = discord.Embed(
                title=f"💼 {job.title}",
                color=color,
            )
            embed.add_field(
                name="Base Salary",
                value=f"{self.currency_name} {await self.formatter(job.base_salary)}",
                inline=True,
            )
            embed.add_field(
                name="Current Salary",
                value=f"{self.currency_name} {await self.formatter(current_salary)} (×{salary_multiplier:.2f})",
                inline=True,
            )
            embed.add_field(
                name="Tenure",
                value=f"{job.days_employed} days",
                inline=True,
            )
            embed.add_field(
                name="Work Streak",
                value=f"{job.streak} consecutive days",
                inline=True,
            )

            if job.last_worked:
                next_work = job.last_worked + timedelta(hours=24)
                fire_deadline = job.last_worked + timedelta(hours=48)
                now = discord.utils.utcnow()
                if next_work > now:
                    remaining = next_work - now
                    hours = remaining.seconds // 3600
                    minutes = (remaining.seconds % 3600) // 60
                    embed.add_field(
                        name="Next Work",
                        value=f"{hours}h {minutes}m",
                        inline=True,
                    )
                else:
                    embed.add_field(
                        name="Next Work",
                        value="Ready now!",
                        inline=True,
                    )

                # Warning if close to being fired (24-48 hours since last work)
                time_since_last = (now - job.last_worked).total_seconds()
                if time_since_last > 86400:  # More than 24 hours since last work
                    time_until_fire = 172800 - time_since_last  # 48 hours - time elapsed
                    if time_until_fire > 0:
                        fire_hours = int(time_until_fire // 3600)
                        fire_minutes = int((time_until_fire % 3600) // 60)
                        embed.add_field(
                            name="⚠️ Warning",
                            value=f"You'll be **fired** in {fire_hours}h {fire_minutes}m if you don't work!",
                            inline=False,
                        )
            else:
                embed.add_field(
                    name="Next Work",
                    value="Ready now!",
                    inline=True,
                )

            embed.set_footer(text=f"Hired: {job.hired_at.strftime('%Y-%m-%d') if hasattr(job.hired_at, 'strftime') else job.hired_at}")
            await ctx.reply(embed=embed)

        except ValueError as e:
            await Embeds.error(ctx, str(e), delete_after=5, reply=True)

    @commands.group(name="loan", aliases=["loans"], invoke_without_command=True)
    async def loan(self, ctx: commands.Context):
        """Group command for managing loans."""
        if ctx.invoked_subcommand is None:
            user_id = ctx.author.id
            active_loan = await self.bot.database.get_active_loans_for_user(user_id)
            if not active_loan:
                return await Embeds.error(ctx, "You have no active loans.", delete_after=5, reply=True)

            await self.bot.database.date_check_loans()

            loan = active_loan[0]
            remaining_balance = loan.total_repay - loan.amount_paid
            payment_percentage = (loan.amount_paid / loan.total_repay * 100) if loan.total_repay > 0 else 0
            
            color = (
                discord.Color.blurple()
                if isinstance(ctx.channel, discord.DMChannel)
                else (
                    ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple()
                )
            )
            embed = discord.Embed(
                title="Loan Status",
                description=(
                    f"Principal: {self.currency_name} **{await self.formatter(loan.principal)}**\n"
                    f"Total to Repay: {self.currency_name} **{await self.formatter(loan.total_repay)}**\n"
                    f"Amount Paid: {self.currency_name} **{await self.formatter(loan.amount_paid)}**\n"
                    f"Remaining Balance: {self.currency_name} **{await self.formatter(remaining_balance)}**\n"
                    f"Progress: **{payment_percentage:.1f}%** paid\n"
                    f"Status: **{loan.status}**\n"
                    f"Due Date: {loan.due_date.strftime('%Y-%m-%d') if hasattr(loan.due_date, 'strftime') else loan.due_date}"
                ),
                color=color,
            )
            embed.set_author(name="Loan Status", icon_url=self.utils.get_avatar_url(ctx.author))
            embed.set_footer(text=f"Interest Rate: {loan.interest_rate * 100:.1f}%")
            await ctx.reply(embed=embed)

    @loan.command(name="take", aliases=["get"], description="Take out a new loan.")
    @unified_cooldown(10)
    async def loan_take(self, ctx: commands.Context, amount: str):
        """Take out a loan. Pay it back with interest!"""
        user_id = ctx.author.id
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        treasury = await self.bot.database.get_treasury_balance()
        safe_loan_amount = await self.bot.database.get_max_loan_amount(user_id)
        current_loan = await self.bot.database.get_active_loans_for_user(user_id)

        await self.bot.database.date_check_loans()

        try:
            try:
                amount = await self.amount_handler(amount, safe_loan_amount)
            except ValueError as e:
                await Embeds.error(
                    ctx,
                    f"Loan amount cannot exceed {self.currency_name} **{await self.formatter(safe_loan_amount)}**.",
                    delete_after=5,
                    reply=True,
                )
                return
            
            amount_decimal = Decimal(amount)
            if current_loan:
                raise ValueError("You already have a loan. Please repay it before taking out another.")
            if amount_decimal <= 0:
                raise ValueError("Loan amount must be greater than zero.")
            if amount_decimal > treasury:
                raise ValueError("The treasury does not have enough funds to cover this loan at the moment. Please try a smaller amount or come back later.")
        
            interest_rate = Decimal("0.10")

            total_repay = (amount_decimal * (Decimal("1") + interest_rate)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id, amount=amount_decimal, description="Loan Disbursement"
            )
            await self.bot.database.add_loan_record(
                user_id=user_id,
                principal=amount_decimal,
                total_repay=total_repay,
                interest_rate=interest_rate,
                due_date=discord.utils.utcnow() + timedelta(days=7),
                status="active",
            )
            color = (
                discord.Color.blurple()
                if isinstance(ctx.channel, discord.DMChannel)
                else (
                    ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple()
                )
            )
            await Embeds.custom(
                ctx,
                f"You have taken out a loan of {self.currency_name} **{await self.formatter(amount_decimal)}**.\n"
                f"Total to repay (with 10% interest): {self.currency_name} **{await self.formatter(total_repay)}**.\n"
                f"Please repay your loan within **7 days** to avoid `penalties.`",
                color=color,
                author={"name": "Loan", "icon_url": self.utils.get_avatar_url(ctx.author)},
                delete_after=None,
                reply=True,
                mention_author=True,
            )
        except ValueError as e:
            await Embeds.error(ctx, str(e.args[0]), delete_after=5, reply=True)

    @loan.command(name="repay", aliases=["pay"], description="Repay an active loan.")
    @unified_cooldown(10)
    async def loan_repay(self, ctx: commands.Context, amount: str):
        """Repay part or all of an active loan."""
        user_id = ctx.author.id
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = await self.bot.database.get_wallet_balance(wallet_id)
        current_loan = await self.bot.database.get_active_loans_for_user(user_id)

        await self.bot.database.date_check_loans()

        if not current_loan:
            return await Embeds.error(ctx, "You have no active loans to repay.", delete_after=5, reply=True)
        loan = current_loan[0]
        try:
            amount = await self.amount_handler(amount, balance)
        except ValueError as e:
            await Embeds.error(ctx, str(e), delete_after=5, reply=True)
            return
        try:
            amount_decimal = Decimal(amount)

            if amount_decimal <= 0:
                raise ValueError("Repayment amount must be greater than zero.")
            if amount_decimal > Decimal(balance):
                raise ValueError("You do not have enough funds to make this repayment.")
            
            # Calculate remaining balance and cap payment at remaining balance
            remaining_balance = loan.total_repay - loan.amount_paid
            if amount_decimal > remaining_balance:
                amount_decimal = remaining_balance
            
            # Process the payment using the new payment system
            payment_result = await self.bot.database.make_loan_payment(
                user_id=user_id,
                payment_amount=amount_decimal,
                notes=f"Payment via loan repay command"
            )
            
            # Deduct from wallet
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id, amount=-amount_decimal, description="Loan Repayment"
            )
            
            color = (
                discord.Color.blurple()
                if isinstance(ctx.channel, discord.DMChannel)
                else (
                    ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple()
                )
            )
            await Embeds.custom(
                ctx,
                f"You have repaid {self.currency_name} **{await self.formatter(payment_result['payment_amount'])}** of your loan.\n"
                f"Remaining balance to repay: {self.currency_name} **{await self.formatter(payment_result['remaining_balance'])}**.\n"
                f"{'**Your loan is now fully repaid!**' if payment_result['loan_status'] == 'paid' else ''}",
                color=color,
                author={"name": "Loan Repayment", "icon_url": self.utils.get_avatar_url(ctx.author)},
                delete_after=None,
                reply=True,
                mention_author=True,
            )
        except ValueError as e:
            await Embeds.error(ctx, str(e.args[0]), delete_after=5, reply=True)

    @commands.command(
        name="scout",
        aliases=["mark"],
    )
    async def scout(self, ctx: commands.Context):
        guild_member_ids = {m.id for m in ctx.guild.members}

        top_users = await self.bot.database.get_top_wallet_users(limit=50)
        resolved_users = await resolve_ids(self.bot.database,[uid for uid, _ in top_users])

        eligible = [
            (uid, bal)
            for uid, bal in top_users
            if bal >= Decimal("10000")
            and resolved_users.get(uid) in guild_member_ids
            and resolved_users.get(uid) != ctx.author.id
        ]

        if not eligible:
            return await Embeds.error(
                ctx,
                "No eligible guild members with balance ≥ 10,000 were found.",
                title="Scout Result",
                delete_after=5,
                reply=True,
                mention_author=True,
            )

        user_id, balance = random.choice(eligible)
        raw_id = resolved_users.get(user_id)

        member = ctx.guild.get_member(raw_id) if raw_id else None
        if not member:
            try:
                member = await ctx.guild.fetch_member(raw_id) if raw_id else None
            except discord.NotFound:
                member = None

        if member:
            name = member.mention
            avatar = member.avatar.url if member.avatar else ctx.guild.icon.url
        else:
            name = f"`{raw_id}`" if raw_id else "Unknown user"
            avatar = None

        embed = discord.Embed(title="Scout Result", color=discord.Color.blurple())
        embed.add_field(name="User", value=name, inline=True)
        embed.add_field(
            name="Balance",
            value=f"**{self.currency_name} {await self.formatter(balance)}**",
            inline=True,
        )
        if avatar:
            embed.set_thumbnail(url=avatar)

        await ctx.reply(embed=embed, delete_after=15)

    @commands.command(name="rob")
    async def rob(self, ctx: commands.Context, target: discord.Member):
        """Attempt to rob another user."""
        user_id = ctx.author.id
        user_wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        target_wallet_id = await self.bot.database.get_wallet_id_for_user(target.id)
        robber_balance = Decimal(
            str(await self.bot.database.get_wallet_balance(user_wallet_id))
        )
        target_balance = Decimal(
            str(await self.bot.database.get_wallet_balance(target_wallet_id))
        )
        target_bank_balance = Decimal(
            str(await self.bot.database.get_bank_balance(target_wallet_id))
        )

        if target.id == self.bot.user.id:
            return await ctx.reply("Get away from me.", delete_after=5)
        if user_id == target.id:
            return await ctx.reply("You cannot rob yourself!", delete_after=5)
        if target.bot:
            return await ctx.reply("You cannot rob a bot!", delete_after=5)

        if target_balance <= Decimal("10000"):
            return await Embeds.error(ctx, f"{target.display_name} doesn't have enough money to rob.", delete_after=5, reply=True)
        if robber_balance < Decimal("100000"):
            return await Embeds.error(ctx, "You need at least 100,000 to attempt a robbery.", delete_after=5, reply=True)

        blocked, defense_message = await self.bot.database.check_robbery_defense(
            target.id, robbery_type="wallet"
        )
        if blocked:
            return await Embeds.error(
                ctx,
                defense_message,
                delete_after=5,
                reply=True,
            )

        outcomes = {
            "critical_success": 10,
            "success": 40,
            "partial_failure": 25,
            "failure": 23,
        }
        total_weight = sum(outcomes.values())
        roll = secrets.randbelow(total_weight)
        cumulative = 0
        for outcome, weight in outcomes.items():
            cumulative += weight
            if roll < cumulative:
                result = outcome
                break

        robbery_debuff = await self.bot.database.get_robbery_debuff_multiplier(
            target.id
        )

        result_message = ""
        cooldown_seconds = 1800
        try:
            if result == "critical_success":
                percentage = Decimal(secrets.randbelow(21) + 40) / Decimal("100")
                amount_stolen = (target_balance * percentage * robbery_debuff).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
                counter_loss = (target_balance * Decimal("0.10") * robbery_debuff).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
                total_theft = amount_stolen + counter_loss

                target_has_bounty = await self.bot.database.user_has_bounty(target.id)
                bounty_msg = ""
                if target_has_bounty:
                    await self.bot.database.claim_bounty(ctx.author.id, target.id)
                    bounty_msg = (
                        f"\nYou also claimed the bounty on {target.display_name}."
                    )

                await self.bot.database.process_p2p_transaction(
                    sender_wallet_id=target_wallet_id,
                    receiver_wallet_id=user_wallet_id,
                    amount=total_theft,
                    description=f"Critical Robbery by {ctx.author.name}",
                    guild_id=ctx.guild.id if ctx.guild else None,
                    fee_from_amount=True,
                )
                result_message = (
                    f"🔥 **YOU STOLE BASICALLY EVERYTHING LMFAOOOOOOOOOOOO**.\n"
                    f"{target.mention} woke up missing {self.currency_name} **{await self.formatter(total_theft)}**"
                    f"{bounty_msg}"
                )
            elif result == "success":
                percentage = Decimal(secrets.randbelow(16) + 20) / Decimal("100")
                amount_stolen = (target_balance * percentage * robbery_debuff).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )

                target_has_bounty = await self.bot.database.user_has_bounty(target.id)
                bounty_msg = ""
                if target_has_bounty:
                    await self.bot.database.claim_bounty(ctx.author.id, target.id)
                    bounty_msg = (
                        f"\nYou also claimed the bounty on {target.display_name}."
                    )

                await self.bot.database.process_p2p_transaction(
                    sender_wallet_id=target_wallet_id,
                    receiver_wallet_id=user_wallet_id,
                    amount=amount_stolen,
                    description=f"Robbery by {ctx.author.name}",
                    guild_id=ctx.guild.id if ctx.guild else None,
                    fee_from_amount=True,
                )

                result_message = (
                    f"😎 You **robbed** {target.mention} and stole "
                    f"{self.currency_name} **{await self.formatter(amount_stolen)}**."
                    f"{bounty_msg}"
                )
            elif result == "partial_failure":
                percentage = Decimal(secrets.randbelow(6) + 10) / Decimal("100")
                stolen = (target_balance * percentage * robbery_debuff).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
                recoup = (stolen * Decimal("0.50")).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
                net_gain = stolen - recoup
                await self.bot.database.process_p2p_transaction(
                    sender_wallet_id=target_wallet_id,
                    receiver_wallet_id=user_wallet_id,
                    amount=net_gain,
                    description=f"Partial Robbery by {ctx.author.name}",
                    guild_id=ctx.guild.id if ctx.guild else None,
                    fee_from_amount=True,
                )
                result_message = (
                    f"🤏 You **robbed** {target.mention} but they fought back, you managed to steal "
                    f"{self.currency_name} **{await self.formatter(net_gain)}** after they recovered some of it."
                )
            elif result == "failure":
                percentage = Decimal(secrets.randbelow(5) + 1) / Decimal("100")
                amount_fined = (robber_balance * percentage).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=user_wallet_id,
                        amount=-amount_fined,
                        description="Fine for failed robbery",
                    )
                except ValueError as e:
                    await Embeds.error(ctx, f"🚫 Transaction failed: {e}", delete_after=5, reply=True)
                    return
                bonus = (target_balance * Decimal("0.01")).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
                result_message = (
                    f"💥 **You failed** to rob {target.mention} and were fined "
                    f"{self.currency_name} **{await self.formatter(amount_fined)}**!\n"
                    f"{target.mention} received {self.currency_name} **{await self.formatter(bonus)}** as compensation."
                )
                await self.bot.database.add_reputation_score(user_id, -1)
                await self.bot.database.add_reputation_score(target.id, 1)
                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=target_wallet_id,
                        amount=bonus,
                        description="Bonus for foiling robbery",
                    )
                except ValueError as e:
                    await Embeds.error(ctx, f"🚫 Transaction failed: {e}", delete_after=5, reply=True)
                    return
            elif result == "bank_robbery":
                bank_blocked, bank_defense_message = (
                    await self.bot.database.check_robbery_defense(
                        target.id, robbery_type="bank"
                    )
                )
                if bank_blocked:
                    return await Embeds.error(
                        ctx,
                        bank_defense_message,
                        delete_after=5,
                        reply=True,
                    )

                percentage = Decimal(secrets.randbelow(11) + 10) / Decimal("100")
                amount_stolen = (
                    target_bank_balance * percentage * robbery_debuff
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if amount_stolen > 0:
                    await self.bot.database.withdraw_from_bank(
                        wallet_id=target_wallet_id,
                        amount=amount_stolen,
                        description=f"Bank Robbery by {ctx.author.name}",
                    )
                    try:
                        await self.bot.database.process_treasury_transaction(
                            wallet_id=user_wallet_id,
                            amount=amount_stolen,
                            description="Bank Robbery Success",
                        )
                    except ValueError as e:
                        await Embeds.error(ctx, f"🚫 Transaction failed: {e}", delete_after=5, reply=True)
                        return
                    result_message = (
                        f"🏦 **You successfully robbed** {target.mention}'s bank account and stole "
                        f"{self.currency_name} **{await self.formatter(amount_stolen)}**!"
                    )
                else:
                    result_message = f"💥 **You attempted to rob** {target.mention}'s bank account but found nothing to steal!"

            await Embeds.custom(
                ctx,
                result_message,
                color=(
                    discord.Color.green()
                    if result in ["critical_success", "success", "bank_robbery"]
                    else discord.Color.red()
                ),
                author={"name": "Robbery", "icon_url": self.utils.get_avatar_url(ctx.author)},
                delete_after=None,
                reply=True,
                mention_author=True,
            )
        except ValueError as e:
            await Embeds.error(
                ctx,
                str(e),
                delete_after=5,
                reply=True,
                author={"name": "Robbery Error", "icon_url": self.utils.get_avatar_url(ctx.author)},
            )

    @commands.command(
        name="drain",
    )
    async def drain(self, ctx: commands.Context, target: discord.Member):
        """Attempt to drain another user's wallet."""

        if target.id == self.bot.user.id:
            await ctx.reply("Get away from me.", delete_after=5)
            return
        if target.id == ctx.author.id:
            await ctx.reply("You cannot drain your own wallet.", delete_after=5)
            return
        if target.bot:
            await ctx.reply("You cannot drain a bot's wallet.", delete_after=5)
            return

        robber_wallet = await self.bot.database.get_wallet_id_for_user(ctx.author.id)
        target_wallet = await self.bot.database.get_wallet_id_for_user(target.id)
        target_balance = Decimal(
            str(await self.bot.database.get_wallet_balance(target_wallet))
        )
        robber_balance = Decimal(
            str(await self.bot.database.get_wallet_balance(robber_wallet))
        )

        if target_balance <= Decimal("10000"):
            return await Embeds.error(ctx, f"{target.display_name} doesn't have enough money to drain.", delete_after=5, reply=True)

        if robber_balance < Decimal("100000"):
            return await ctx.reply(
                f"You need at least {self.currency_name} **100k** to attempt a drain.",
                delete_after=5,
            )

        blocked, defense_message = await self.bot.database.check_robbery_defense(
            target.id, robbery_type="wallet"
        )
        if blocked:
            return await Embeds.error(
                ctx,
                defense_message,
                delete_after=5,
                reply=True,
            )

        outcome_roll = secrets.randbelow(100)
        success_threshold = 80

        robbery_debuff = await self.bot.database.get_robbery_debuff_multiplier(
            target.id
        )

        if outcome_roll < success_threshold:
            drain_amount = (target_balance * robbery_debuff).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
            try:
                await self.bot.database.process_p2p_transaction(
                    sender_wallet_id=target_wallet,
                    receiver_wallet_id=robber_wallet,
                    amount=drain_amount,
                    description=f"Drained by {ctx.author.name}",
                    guild_id=ctx.guild.id if ctx.guild else None,
                    fee_from_amount=True,
                )

                result_message = (
                    f"💰 You successfully drained {self.currency_name} **{await self.formatter(drain_amount)}**"
                    f"from {target.mention}'s wallet!"
                )

                target_has_bounty = await self.bot.database.user_has_bounty(target.id)
                if target_has_bounty:
                    await self.bot.database.claim_bounty(ctx.author.id, target.id)
                    result_message += (
                        f"\nYou also claimed the bounty on {target.display_name}."
                    )

                await Embeds.success(
                    ctx,
                    result_message,
                    author={"name": "Drain", "icon_url": self.utils.get_avatar_url(ctx.author)},
                    delete_after=None,
                    reply=True,
                    mention_author=True,
                )

            except ValueError as e:
                await Embeds.error(ctx, f"An error occurred: {str(e)}", delete_after=5, reply=True)
                return
        else:
            fine_percentage = Decimal("0.05")
            amount_fined = (robber_balance * fine_percentage).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=robber_wallet,
                    amount=-amount_fined,
                    description="Fine for failed drain attempt",
                )
            except ValueError as e:
                await Embeds.error(
                    ctx,
                    f"🚫 Transaction failed: {e}",
                    delete_after=5,
                    reply=True,
                )
                return
                return
            await Embeds.error(
                ctx,
                f"🚨 You attempted to drain {target.mention}'s wallet but got caught! "
                f"You were fined {self.currency_name} **{await self.formatter(amount_fined)}**",
                author={"name": "Drain", "icon_url": self.utils.get_avatar_url(ctx.author)},
                delete_after=None,
                reply=True,
                mention_author=True,
            )

    @commands.hybrid_command(
        name="send",
        aliases=["transfer", "tfr", "give"],
        description="Transfer currency to another user.",
    )
    @unified_cooldown(10)
    async def transfer(
        self, ctx: commands.Context, member: discord.Member, amount: str
    ):
        """Transfer currency to another user."""
        sender = ctx.author
        receiver = member

        try:
            if member.id == self.bot.user.id:
                await ctx.reply("I don't need your money.", delete_after=5)
                return

            if member.bot:
                await ctx.reply("You can't transfer money to bots.", delete_after=5)
                return

            if sender == receiver:
                await ctx.reply("You can't transfer money to yourself.", delete_after=5)
                return
            if receiver.bot:
                await ctx.reply("You can't transfer money to bots.", delete_after=5)
                return

            sender_wallet_id = await self.bot.database.get_wallet_id_for_user(sender.id)
            receiver_wallet_id = await self.bot.database.get_wallet_id_for_user(
                receiver.id
            )
            sender_balance = await self.bot.database.get_wallet_balance(
                sender_wallet_id
            )
            sender_balance = Decimal(str(sender_balance))
            try:
                amount = await self.amount_handler(amount, sender_balance)
            except ValueError as e:
                await Embeds.error(ctx, str(e), delete_after=5, reply=True)
                return

            # Check wealth-tier transfer limit
            max_transfer = await self.bot.database.get_max_transfer_amount(sender.id)
            if amount > max_transfer:
                return await Embeds.error(
                    ctx,
                    f"🚫 Transfer exceeds your wealth-tier limit.\n\n"
                    f"**Your max transfer:** {self.currency_name} **{await self.formatter(max_transfer)}**\n"
                    f"**Attempted:** {self.currency_name} **{await self.formatter(amount)}**\n\n"
                    f"High-wealth users have reduced transfer limits to promote economic balance.",
                    author={"name": "Transfer Limit", "icon_url": self.utils.get_avatar_url(ctx.author)},
                    delete_after=10,
                    reply=True,
                    mention_author=True,
                )
                return

            txid = await self.bot.database.process_p2p_transaction(
                sender_wallet_id=sender_wallet_id,
                receiver_wallet_id=receiver_wallet_id,
                amount=amount,
                description=f"Transfer from {sender.name} to {receiver.name}",
                guild_id=ctx.guild.id,
            )
            color = discord.Color.blurple()
            if isinstance(ctx.channel, discord.DMChannel):
                color = discord.Color.blurple()
            else:
                color = (
                    ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple()
                )
            await Embeds.custom(
                ctx,
                f"**{sender.mention}** transferred {self.currency_name} "
                f"**{await self.formatter(amount)}** to **{receiver.mention}**.\n"
                f"ID: `{txid}`",
                color=color,
                author={"name": "Transfer", "icon_url": self.utils.get_avatar_url(ctx.author)},
                delete_after=None,
                reply=True,
                mention_author=True,
            )
            embed = discord.Embed(
                description=(
                    f"**{sender.mention}** transferred {self.currency_name} "
                    f"**{await self.formatter(amount)}** to you.\n"
                    f"ID: `{txid}`"
                ), color=None)
        except ValueError as e:
            await Embeds.error(
                ctx,
                str(e),
                delete_after=5,
                reply=True,
                author={"name": "Transfer Error", "icon_url": self.utils.get_avatar_url(ctx.author)},
            )

    @commands.command(name="drop")
    async def drop(self, ctx: commands.Context, amount: str):
        user_id = ctx.author.id
        drop_wallet = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = await self.bot.database.get_wallet_balance(drop_wallet)
        balance = Decimal(str(balance))
        try:
            amount = await self.amount_handler(amount, balance)
        except ValueError as e:
            await Embeds.error(ctx, str(e), delete_after=5, reply=True)
            return

        if amount < Decimal("100000"):
            await Embeds.error(ctx, f"You don't have enough in your wallet to do a drop!\n\nMinimum is {self.currency_name} **{await self.formatter(Decimal('100000'))}**.", delete_after=5, reply=True)
            return

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=drop_wallet, amount=-amount, description="Money Drop"
            )
        except ValueError as e:
            await Embeds.error(
                ctx,
                f"🚫 Transaction failed: {e}",
                delete_after=5,
                reply=True,
            )
            return
            return

        symbols = ["💰", "💸", "💳", "💵", "💶", "🪙", "💷", "💴"]
        choice = secrets.choice(symbols)

        embed = discord.Embed(
            description=(
                f"**{ctx.author.display_name}** has dropped {self.currency_name} **{await self.formatter(amount)}**!\n\n"
                f"Click the **Button** below to claim it!"
            ),
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=self.utils.get_avatar_url(ctx.author))
        embed.set_author(
            name="Money Drop", icon_url=self.utils.get_avatar_url(ctx.author)
        )

        await self.bot.database.add_reputation_score(ctx.author.id, 1)
        view = DropView(
            self.bot, ctx, amount, ctx.author, self.currency_name, choice, self
        )
        drop_message = await ctx.reply(embed=embed, view=view)
        view.message = drop_message

        self.active_drops[drop_message.id] = {
            "amount": amount,
            "claimed": False,
            "author": ctx.author,
        }

    @commands.command(name="airdrop")
    async def airdrop(self, ctx: commands.Context, amount: str):
        user_id = ctx.author.id
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = await self.bot.database.get_wallet_balance(wallet_id)
        balance = Decimal(str(balance))

        try:
            amount_converted = await self.amount_handler(amount, balance)
        except ValueError as e:
            await Embeds.error(ctx, str(e), delete_after=5, reply=True)
            return

        if amount_converted < Decimal("100000"):
            await Embeds.error(ctx, f"You don't have enough in your wallet to do an airdrop!\n\nMinimum is {self.currency_name} **{await self.formatter(Decimal('100000'))}**.", delete_after=5, reply=True)
            return

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id, amount=-amount_converted, description="Airdrop"
            )
        except ValueError as e:
            await Embeds.error(
                ctx,
                f"🚫 Transaction failed: {e}",
                delete_after=5,
                reply=True,
            )
            return
            return

        embed = discord.Embed(
            description=(
                f"**{ctx.author.display_name}** has initiated an **Airdrop** of "
                f"{self.currency_name} **{await self.formatter(amount_converted)}**!\n\n"
                "Click the **Join** button below within **15 seconds** to claim it!"
            ),
            color=discord.Color.gold(),
        )
        embed.set_author(
            name=f"AirDrop", icon_url=self.utils.get_avatar_url(ctx.author)
        )

        view = AirDropView(
            self.bot, amount_converted, self.currency_name, wallet_id, ctx.author
        )
        await self.bot.database.add_reputation_score(ctx.author.id, 1)
        message = await ctx.reply(embed=embed, view=view)
        view.message = message

    @commands.command(name='xmas')
    async def xmas(self, ctx: commands.Context):
        """Open your Christmas gift!"""
        
        # 1. Date Check: Only allow on December 25th
        now = datetime.now(timezone.utc)
        if now.month != 12 or now.day != 25:
            return await Embeds.warning(ctx, "🎁 **It's not Christmas yet!** This command only works on December 25th.", delete_after=10, reply=True)

        user_id = ctx.author.id
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        
        gift_amount = Decimal(str(user_id)) * Decimal("2") 

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=gift_amount,
                description="Christmas Gift",
            )
        except ValueError as e:
            await Embeds.error(
                ctx,
                f"🚫 Transaction failed: {e}",
                delete_after=5,
                reply=True,
            )
            return
            return

        # Determine embed color based on role or DM
        color = (
            discord.Color.blurple()
            if isinstance(ctx.channel, discord.DMChannel)
            else (
                ctx.author.top_role.color
                if ctx.author.top_role
                else discord.Color.blurple()
            )
        )

        await Embeds.custom(
            ctx,
            f"🎄 Merry Christmas from TPNE! You received a gift of {self.currency_name} **{await self.formatter(gift_amount)}**!",
            color=color,
            author={"name": "Christmas Gift", "icon_url": self.utils.get_avatar_url(ctx.author)},
            delete_after=None,
            reply=True,
            mention_author=True,
        )

    @commands.command(name="newyear")
    async def newyear(self, ctx: commands.Context):
        """Open your New Year's gift!"""
        
        # 1. Date Check: Only allow on January 1st
        now = datetime.now(timezone.utc)
        if now.month != 1 or now.day != 1:
            return await Embeds.warning(ctx, "🎉 **It's not New Year's Day yet!** This command only works on January 1st.", delete_after=10, reply=True)

        user_id = ctx.author.id
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        gift_amount = Decimal(str(user_id)) * Decimal("3")
        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=gift_amount,
                description="New Year's Gift",
            )
        except ValueError as e:
            await Embeds.error(
                ctx,
                f"🚫 Transaction failed: {e}",
                delete_after=5,
                reply=True,
            )
            return
            return

    @commands.group(name="crypto", aliases=["coin","coins"], invoke_without_command=True)
    async def crypto(self, ctx: commands.Context):
        prefix = "/"
        if ctx.message:
            prefix = await self.bot.get_prefix(ctx.message)
            if isinstance(prefix, list):
                prefix = prefix[0]

        subcmds = getattr(ctx.command, "commands", []) or []
        lines = []
        for cmd in sorted(subcmds, key=lambda c: c.name):
            name = cmd.name
            aliases = (
                f" (or: {', '.join(cmd.aliases)})"
                if getattr(cmd, "aliases", None)
                else ""
            )
            desc = (cmd.help or cmd.description or "").strip()
            if desc:
                lines.append(f"`{prefix}crypto {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}crypto {name}`{aliases}")

        description = "\n".join(lines) if lines else "No subcommands available."

        await Embeds.custom(
            ctx,
            description,
            title="Crypto — Available Commands",
            color=discord.Color.blurple(),
            footer=f"Use {prefix}crypto <subcommand> for details.",
            delete_after=None,
            reply=True,
            mention_author=False,
        )

    @crypto.command(name="buy", description="Buy cryptocurrency with your balance")
    @unified_cooldown(10)
    async def crypto_buy(self, ctx: commands.Context, currency: str, amount: str):
        user_id = ctx.author.id
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = Decimal(str(await self.bot.database.get_wallet_balance(wallet_id)))

        symbol = currency.upper()
        price = await self.bot.database.get_crypto_price(symbol)
        if not price or price <= Decimal("0.00000001"):
            return await ctx.reply(
                f"Price data for '{symbol}' is invalid or unavailable.", delete_after=5
            )

        try:
            spend = await self.amount_handler(amount, balance)
        except ValueError as e:
            return await ctx.reply(str(e), delete_after=5)

        if spend > balance:
            return await ctx.reply(
                f"Insufficient balance. You only have {await self.short_formatter(balance)}.",
                delete_after=5,
            )

        # Prevent InvalidOperation by validating values before quantize
        if spend <= Decimal("0"):
            return await ctx.reply("Invalid amount: must be greater than 0", delete_after=5)
        
        if price <= Decimal("0"):
            return await ctx.reply("Invalid price: must be greater than 0", delete_after=5)
        
        # Check for NaN or Infinity which would cause quantize to fail
        if not spend.is_finite() or not price.is_finite():
            return await ctx.reply("Invalid numeric value detected", delete_after=5)

        try:
            coins = AmountUtils.round_crypto(spend / price)
        except InvalidOperation:
            return await ctx.reply(
                f"Invalid price calculation result for {symbol}.", delete_after=5
            )
        await self.bot.database.process_treasury_transaction(
            wallet_id, -spend, f"Buy {symbol}"
        ) 
        await self.bot.database.add_crypto_asset(user_id, symbol, coins, price)

        embed = discord.Embed(
            title="✅ Crypto Purchase",
            description=f"Successfully purchased **{symbol}**",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(
            name="Amount Bought",
            value=f"**{await self.short_formatter(coins)} {symbol}**",
            inline=True
        )
        embed.add_field(
            name="Price per Coin",
            value=f"**{await self.short_formatter(price)} {self.currency_name}**",
            inline=True
        )
        embed.add_field(
            name="Total Cost",
            value=f"**{await self.short_formatter(spend)} {self.currency_name}**",
            inline=True
        )
        await ctx.reply(embed=embed)

    @crypto.command(name="sell", description="Sell cryptocurrency for your balance")
    @unified_cooldown(10)
    async def crypto_sell(self, ctx: commands.Context, currency: str, amount: str):
        user_id = ctx.author.id
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        symbol = currency.upper()
        asset = await self.bot.database.get_crypto_asset(user_id, symbol)
        if not asset or asset.amount <= 0:
            return await ctx.reply(f"You have no '{symbol}' to sell.", delete_after=5)

        balance_coins = asset.amount
        try:
            sell_amt = await self.crypto_amount_handler(amount, balance_coins)
        except ValueError as e:
            return await ctx.reply(str(e), delete_after=5)

        price = await self.bot.database.get_crypto_price(symbol)
        if not price or price <= Decimal("0.00000001"):
            return await ctx.reply(
                f"Price data for '{symbol}' is unavailable.", delete_after=5
            )

        proceeds = AmountUtils.round_currency(sell_amt * price)
        await self.bot.database.process_treasury_transaction(
            wallet_id, proceeds, f"Sell {symbol}"
        )
        await self.bot.database.update_crypto_amount(user_id, symbol, -sell_amt)

        # Calculate P/L for this sale
        cost_basis = Decimal(asset.purchase_price * sell_amt)
        pnl = proceeds - cost_basis
        pnl_percentage = (pnl / cost_basis * 100) if cost_basis != 0 else 0
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        pnl_color = discord.Color.green() if pnl >= 0 else discord.Color.red()
        pnl_text = f"**{pnl_emoji} {await self.short_formatter(pnl)} {self.currency_name}** ({pnl_percentage:+.2f}%)"

        embed = discord.Embed(
            title="✅ Crypto Sale",
            description=f"Successfully sold **{symbol}**",
            color=pnl_color,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(
            name="Amount Sold",
            value=f"**{await self.short_formatter(sell_amt)} {symbol}**",
            inline=True
        )
        embed.add_field(
            name="Price per Coin",
            value=f"**{await self.short_formatter(price)} {self.currency_name}**",
            inline=True
        )
        embed.add_field(
            name="Total Proceeds",
            value=f"**{await self.short_formatter(proceeds)} {self.currency_name}**",
            inline=True
        )
        embed.add_field(
            name="Profit/Loss",
            value=pnl_text,
            inline=False
        )
        await ctx.reply(embed=embed)

    @crypto.command(name="transfer", description="Transfer cryptocurrency to another user")
    @unified_cooldown(10)
    async def crypto_transfer(self, ctx: commands.Context, recipient: discord.Member, currency: str, amount: str):
        sender_id = ctx.author.id
        receiver_id = recipient.id
        
        if sender_id == receiver_id:
            return await ctx.reply("You cannot transfer crypto to yourself.", delete_after=5)
        
        symbol = currency.upper()
        asset = await self.bot.database.get_crypto_asset(sender_id, symbol)
        if not asset or asset.amount <= 0:
            return await ctx.reply(f"You have no '{symbol}' to transfer.", delete_after=5)

        balance_coins = asset.amount
        try:
            transfer_amt = await self.crypto_amount_handler(amount, balance_coins)
        except ValueError as e:
            return await ctx.reply(str(e), delete_after=5)

        try:
            txid = await self.bot.database.transfer_crypto_asset(
                sender_id,
                receiver_id,
                symbol,
                transfer_amt,
                f"Transfer to {recipient.display_name}"
            )
        except ValueError as e:
            return await ctx.reply(str(e), delete_after=5)

        embed = discord.Embed(
            title="✅ Crypto Transfer",
            description=f"Successfully transferred **{symbol}** to **{recipient.display_name}**",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(
            name="Amount Transferred",
            value=f"**{await self.short_formatter(transfer_amt)} {symbol}**",
            inline=True
        )
        embed.add_field(
            name="Recipient",
            value=f"**{recipient.display_name}**",
            inline=True
        )
        embed.add_field(
            name="Transaction ID",
            value=f"`{txid}`",
            inline=True
        )
        embed.set_footer(text="No fees applied")
        await ctx.reply(embed=embed)

    async def crypto_amount_handler(self, input_str: str, balance: Decimal) -> Decimal:
        if not isinstance(input_str, str):
            raise ValueError("Invalid amount input type.")

        amount_input = input_str.strip().lower()

        if amount_input == "all" or amount_input == "max":
            amount = AmountUtils.truncate_crypto(balance)
        elif amount_input == "half":
            amount = AmountUtils.round_crypto(balance / Decimal("2"))
        elif amount_input == "quarter":
            amount = AmountUtils.round_crypto(balance / Decimal("4"))

        elif amount_input.endswith("%"):
            percentage_match = re.match(r"^([0-9]+(\.[0-9]+)?)%$", amount_input)
            if percentage_match:
                try:
                    percentage = Decimal(percentage_match.group(1))
                    if Decimal("1") <= percentage <= Decimal("100"):
                        amount = AmountUtils.round_crypto(balance * (percentage / Decimal("100")))
                    else:
                        raise ValueError("Percentage must be between 1% and 100%.")
                except InvalidOperation:
                    raise ValueError("Invalid percentage value.")
            else:
                raise ValueError("Invalid percentage format.")
        else:
            multipliers = {
                "k": Decimal("1000"),
                "m": Decimal("1000000"),
                "b": Decimal("1000000000"),
                "t": Decimal("1000000000000"),
                "q": Decimal("1000000000000000"),
                "qu": Decimal("1000000000000000000"),
                "s": Decimal("1000000000000000000000"),
                "se": Decimal("1000000000000000000000000"),
                "o": Decimal("1000000000000000000000000000"),
                "n": Decimal("1000000000000000000000000000000"),
                "d": Decimal("1000000000000000000000000000000000"),
            }
            multiplier_match = re.match(
                r"^([0-9]+(\.[0-9]+)?)(k|m|b|t|q|qu|s|se|o|n|d)?$", amount_input
            )
            if not multiplier_match:
                raise ValueError("Invalid amount format.")

            try:
                number = Decimal(multiplier_match.group(1))
                if multiplier_match.group(3):
                    multiplier = multipliers[multiplier_match.group(3)]
                    amount = AmountUtils.round_crypto(number * multiplier)
                else:
                    amount = AmountUtils.round_crypto(number)
            except (InvalidOperation, KeyError):
                raise ValueError("Invalid amount.")

        if amount.is_nan():
            raise ValueError("Invalid amount.")

        if amount > balance:
            raise ValueError("Insufficient Funds.")

        valid, error_msg = AmountUtils.validate_crypto_minimum(amount)
        if not valid:
            raise ValueError(error_msg)

        return amount
    
    @commands.command(
        name="deposit",
        aliases=["dep", "dp", "depo"],
    )
    async def deposit(self, ctx: commands.Context, amount: str):
        """Deposit currency into the bank."""
        user_id = ctx.author.id
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = await self.bot.database.get_wallet_balance(wallet_id)
        balance = Decimal(str(balance))

        try:
            amount = await self.amount_handler(amount, balance)
        except ValueError as e:
            await Embeds.error(ctx, str(e), delete_after=5, reply=True)
            return

        async with self.bot.database.get_session() as session:
            async with session.begin():
                try:
                    await self.bot.database.deposit_to_bank(
                        wallet_id, amount, "Bank Deposit"
                    )

                    await Embeds.success(ctx, f"You successfully deposited {self.currency_name} **{await self.formatter(amount)}**.", reply=True)

                except ValueError as e:
                    await session.rollback()
                    await Embeds.error(ctx, "An error occurred during deposit.", delete_after=5, reply=True)
                except commands.UnexpectedQuoteError:
                    await Embeds.error(
                        ctx,
                        "Lol dumbass.",
                        delete_after=5,
                        reply=True,
                    )

    @commands.command(
        name="withdraw",
        aliases=["with", "wd"],
    )
    async def withdraw(self, ctx: commands.Context, amount: str):
        """Withdraw currency from the bank."""
        user_id = ctx.author.id
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        bank_balance = await self.bot.database.get_bank_balance(wallet_id)
        bank_balance = Decimal(str(bank_balance))

        try:
            amount = await self.amount_handler(amount, bank_balance)
        except ValueError as e:
            await Embeds.error(ctx, str(e), delete_after=5, reply=True)
            return

        async with self.bot.database.get_session() as session:
            async with session.begin():
                try:
                    await self.bot.database.withdraw_from_bank(
                        wallet_id, amount, "Bank Withdrawal"
                    )

                    await Embeds.success(ctx, f"You successfully withdrew {self.currency_name} **{await self.formatter(amount)}**.", reply=True)

                except ValueError as e:
                    await session.rollback()
                    await Embeds.error(ctx, "An error occurred during withdrawal.", delete_after=5, reply=True)
                    await Embeds.error(ctx, str(e), delete_after=5, reply=True)
                except commands.UnexpectedQuoteError:
                    await Embeds.error(ctx, "Lol dumbass.", delete_after=5, reply=True)

    @commands.command(
        name="treasury",
        aliases=["treas"],
    )
    async def treasury_info(self, ctx: commands.Context):
        """Fetch and display treasury balance and latest transactions."""
        try:
            treasury_balance = await self.bot.database.get_treasury_balance()

            embed = discord.Embed(
                title="🏦 Treasury Information", color=discord.Color.gold()
            )
            embed.add_field(
                name="Treasury Balance",
                value=f"💰 **{await self.formatter(treasury_balance)}**",
                inline=False,
            )
            await ctx.reply(embed=embed)

        except Exception as e:
            await Embeds.error(
                ctx,
                "Error fetching treasury info.",
                delete_after=5,
                reply=True,
            )
            logger.error(f"Error fetching treasury info: {str(e)}")
            logger.error(f"Error fetching treasury info: {str(e)}")

    @commands.command(
        name="transactions",
        aliases=["txs"],
    )
    async def transactions_cmd(
        self, ctx: commands.Context, member: discord.Member = None
    ):
        member = member or ctx.author

        requesting_user = ctx.author

        user_transactions = await self.bot.database.get_transactions_by_user_id(
            member.id, limit=100
        )

        if not user_transactions:
            await Embeds.error(
                ctx,
                "No transactions found.",
                delete_after=None,
                reply=True,
                mention_author=True,
            )
        else:
            paginator = TransactionPaginator(
                self, user_transactions, member, requesting_user
            )
            initial_embed = await paginator.get_page_embed(0)
            message = await ctx.reply(embed=initial_embed, view=paginator)
            paginator.message = message

    @commands.command(
        name="transaction", aliases=["tx"]
    )
    async def transaction_lookup_cmd(self, ctx: commands.Context, txid: str):
        """Look up a transaction by its UUID and present a clean, informative embed."""
        try:
            import uuid
            from datetime import datetime, timezone

            try:
                uuid_obj = uuid.UUID(txid)
                txid = str(uuid_obj)
            except ValueError:
                await ctx.reply(
                    "❌ Invalid transaction ID format. Please provide a valid UUID.",
                    delete_after=5,
                )
                return

            transaction = await self.bot.database.get_transaction_by_id(txid)
            if not transaction:
                await ctx.reply("🔍 No transaction found with that ID.", delete_after=5)
                return

            raw_from_id = await resolve_id(self.bot.database,getattr(transaction, "from_user_id", None))
            raw_to_id = await resolve_id(self.bot.database,getattr(transaction, "to_user_id", None))

            async def resolve_user(uid):
                if not uid:
                    return None

                try:
                    if ctx.guild:
                        member = ctx.guild.get_member(uid)
                        if member:
                            return member
                    user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                    return user
                except Exception:
                    return None

            from_user = await resolve_user(raw_from_id)
            to_user = await resolve_user(raw_to_id)

            amt = getattr(transaction, "amount", None)
            try:
                amt_decimal = Decimal(str(amt)) if amt is not None else Decimal("0")
            except Exception:
                amt_decimal = Decimal("0")

            if amt_decimal > 0:
                color = discord.Color.green()
                sign = "+"
            elif amt_decimal < 0:
                color = discord.Color.red()
                sign = "-"
            else:
                color = discord.Color.blurple()
                sign = ""

            formatted_amount = (
                await self.formatter(abs(amt_decimal))
                if hasattr(self, "formatter")
                else str(abs(amt_decimal))
            )
            amount_field = f"{self.currency_name} {sign}**{formatted_amount}**"

            embed = discord.Embed(title="📄 Transaction Details", color=color)
            embed.add_field(
                name="Transaction ID", value=f"`{transaction.id}`", inline=False
            )

            if from_user:
                from_display = f"{getattr(from_user, 'mention', getattr(from_user, 'display_name', str(from_user)))}"
            else:
                from_display = (
                    "System"
                    if not raw_from_id
                    else f"User ID: `{raw_from_id}`"
                )

            if to_user:
                to_display = f"{getattr(to_user, 'mention', getattr(to_user, 'display_name', str(to_user)))}"
            else:
                to_display = (
                    "System"
                    if not raw_to_id
                    else f"User ID: `{raw_to_id}`"
                )

            embed.add_field(name="From", value=from_display, inline=True)
            embed.add_field(name="To", value=to_display, inline=True)

            embed.add_field(name="Amount", value=amount_field, inline=False)
            tx_type = (
                "P2P"
                if getattr(transaction, "from_user_id", None)
                and getattr(transaction, "to_user_id", None)
                else "Treasury / System"
            )
            embed.add_field(name="Type", value=tx_type, inline=True)

            sender_wallet = getattr(transaction, "from_wallet_id", None) or getattr(
                transaction, "sender_wallet_id", None
            )
            receiver_wallet = getattr(transaction, "to_wallet_id", None) or getattr(
                transaction, "receiver_wallet_id", None
            )
            if sender_wallet:
                embed.add_field(
                    name="Sender Wallet", value=f"`{sender_wallet}`", inline=True
                )
            if receiver_wallet:
                embed.add_field(
                    name="Receiver Wallet", value=f"`{receiver_wallet}`", inline=True
                )

            desc = getattr(transaction, "description", "") or ""
            if desc:
                if len(desc) > 1024:
                    desc = desc[:1016] + "…"
                embed.add_field(name="Description", value=desc, inline=False)

            ts = getattr(transaction, "timestamp", None)
            if ts:
                try:
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
                embed.timestamp = ts
                embed.set_footer(text="Transaction time (UTC)")
            else:
                embed.set_footer(text="Transaction time: Unknown")

            try:
                author_icon = None
                if from_user and getattr(from_user, "avatar", None):
                    author_icon = (
                        getattr(from_user, "avatar").url
                        if getattr(from_user, "avatar", None)
                        else None
                    )
                if not author_icon:
                    author_icon = self.utils.get_avatar_url(ctx.author)
                embed.set_author(
                    name=f"{ctx.author.display_name}", icon_url=author_icon
                )
            except Exception:
                pass

            await ctx.reply(embed=embed)

        except Exception as e:
            logger.exception("Error in transaction lookup")
            await ctx.reply(
                "❌ An unexpected error occurred while fetching the transaction.",
                delete_after=5,
            )

    @commands.group(name="bounty", invoke_without_command=True)
    async def bounty(self, ctx: commands.Context):
        """Group command for managing bounties."""
        prefix = "/"
        if ctx.message:
            prefix = await self.bot.get_prefix(ctx.message)
            if isinstance(prefix, list):
                prefix = prefix[0]

        subcmds = getattr(ctx.command, "commands", []) or []
        lines = []
        for cmd in sorted(subcmds, key=lambda c: c.name):
            name = cmd.name
            aliases = (
                f" (or: {', '.join(cmd.aliases)})"
                if getattr(cmd, "aliases", None)
                else ""
            )
            desc = (cmd.help or cmd.description or "").strip()
            if desc:
                lines.append(f"`{prefix}bounty {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}bounty {name}`{aliases}")

        description = "\n".join(lines) if lines else "No subcommands available."

        await Embeds.custom(
            ctx,
            description,
            title="Bounty — Available Commands",
            color=discord.Color.blurple(),
            footer=f"Use {prefix}bounty <subcommand> for details.",
            delete_after=None,
            reply=True,
            mention_author=False,
        )

    @bounty.command(name="set", description="Set a bounty on another user")
    @unified_cooldown(10)
    async def bounty_set(
        self, ctx: commands.Context, member: discord.Member, amount: str
    ):
        await ctx.reply("Setting bounty...")
        user = ctx.author
        if member == user:
            return await ctx.reply(
                "You cannot set a bounty on yourself.", delete_after=5
            )
        if member == ctx.guild.me:
            return await ctx.reply(
                "Nice try, but no bounties on me!", delete_after=5
            )

        wallet_id = await self.bot.database.get_wallet_id_for_user(user.id)
        balance = Decimal(str(await self.bot.database.get_wallet_balance(wallet_id)))

        try:
            amt = await self.amount_handler(amount, balance)
        except ValueError as e:
            return await ctx.reply(f"🚫 {e}", delete_after=5)

        bounty = await self.bot.database.place_bounty(user.id, member.id, amt)
        if bounty is None:
            return await ctx.reply(
                "🚫 Failed to place bounty. Try again later.", delete_after=5
            )

        desc = f"{user.mention} has placed a bounty of {self.currency_name} **{await self.short_formatter(amt)}** on {member.mention}!"
        await Embeds.success(
            ctx,
            desc,
            delete_after=None,
            reply=True,
            mention_author=True,
        )

    @bounty.command(name="list", description="List top active bounties")
    @unified_cooldown(10)
    async def bounty_list(self, ctx: commands.Context):
        msg = await ctx.reply("Fetching top bounties...")
        top_bounties: List = await self.bot.database.get_top_bounty_users(10)
        if not top_bounties:
            return await msg.edit(
                "There are no active bounties right now.", delete_after=5
            )

        embed = discord.Embed(title="🎯 Top Bounties", color=discord.Color.blurple())
        resolved_bounties = await resolve_ids(self.bot.database,[uid for uid, _ in top_bounties])
        for user_id, total in top_bounties:
            raw_id = resolved_bounties.get(user_id)
            member = ctx.guild.get_member(raw_id) if raw_id else None
            if member:
                name = member.display_name
            else:
                try:
                    user_obj = await self.bot.fetch_user(raw_id) if raw_id else None
                    name = user_obj.name if user_obj else "Unknown user"
                except:
                    name = "Unknown user"
            embed.add_field(
                name=name,
                value=f"{self.currency_name} **{await self.short_formatter(total)}**",
                inline=False,
            )
        await msg.edit(embed=embed)

    @app_commands.command(name="shop", description="View the shop and buy items.")
    @app_commands.checks.bot_has_permissions(embed_links=True, send_messages=True)
    @unified_cooldown(10)
    async def shop(self, interaction: Interaction):
        shop_items = await self.bot.database.list_shop_items()
        if not shop_items:
            await Embeds.error(
                interaction,
                "There are no items in the shop currently. Check back later!",
                ephemeral=True,
            )
        else:
            view = ShopView(
                bot=self.bot,
                shop_items=shop_items,
                user_id=interaction.user.id,
                currency_name=self.currency_name,
            )
            item = shop_items[0]
            embed = discord.Embed(
                title=item.name,
                description=item.description or "No description available.",
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Price",
                value=f"{self.currency_name} **{await self.formatter(Decimal(item.price))}**",
            )
            qty_text = (
                "∞"
                if getattr(item, "unlimited", False)
                else f"**{item.quantity}** left in stock."
            )
            embed.add_field(name="Quantity", value=qty_text, inline=True)
            embed.set_footer(text=f"Item 1 of {len(shop_items)}")

            await interaction.response.send_message(
                "Welcome to the shop!", embed=embed, view=view, ephemeral=True
            )

    @app_commands.command(name="inventory", description="View your items")
    @app_commands.checks.bot_has_permissions(embed_links=True, send_messages=True)
    @unified_cooldown(10)
    async def inventory(self, interaction: Interaction, member: discord.Member = None):
        member = member or interaction.user

        entries = await self.bot.database.get_user_inventory_grouped(member.id)
        if not entries:
            await Embeds.error(
                interaction,
                "Your inventory is empty.",
                title="Inventory",
                ephemeral=True,
            )
            return

        paginator = ItemPaginator(
            self.bot,
            entries,
            user_id=member.id,
            title=f"{member.display_name}'s Inventory",
        )
        embed = await paginator.send_page()

        await interaction.response.send_message(embed=embed, view=paginator)

    @app_commands.command(name="use", description="Use an item from your inventory")
    @app_commands.checks.bot_has_permissions(embed_links=True, send_messages=True)
    @unified_cooldown(10)
    @app_commands.describe(
        item_id="The item to use (find the ID with /inventory)",
        target="Target user for targetable/offensive items",
    )
    async def use_item(
        self,
        interaction: Interaction,
        item_id: int,
        target: discord.Member = None,
    ):
        item = await self.bot.database.get_user_item(
            interaction.user.id, item_id
        )
        if not item:
            await Embeds.warning(
                interaction,
                "You don't own that item or the ID is invalid.",
                title="Use Item",
                ephemeral=True,
            )
            return

        # Targetable/offensive items require a valid target
        if item.targetable or item.item_type.value in ("offensive",):
            if target is None or target.bot or target.id == interaction.user.id:
                await Embeds.warning(
                    interaction,
                    f"**{item.name}** must be used on another user. "
                    "Provide the `target` option.",
                    title="Use Item",
                    ephemeral=True,
                )
                return

            try:
                result = await self.bot.database.use_targeted_item(
                    interaction.user.id, item_id, target.id
                )
                embed = discord.Embed(
                    title="Item Used",
                    description=result["message"],
                    color=discord.Color.red(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception as e:
                await Embeds.error(
                    interaction,
                    str(e),
                    title="Use Item",
                    ephemeral=True,
                )
            return

        # Self-use path for consumables, redeemables, defensive auras, collectibles
        try:
            remaining = await self.bot.database.get_item_cooldown(
                interaction.user.id, item.name
            )
            if remaining > 0:
                mins, secs = divmod(remaining, 60)
                await Embeds.warning(
                    interaction,
                    f"**{item.name}** is on cooldown. Time remaining: {mins}m {secs}s",
                    title="Use Item",
                    ephemeral=True,
                )
                return

            result = await self.bot.database.use_inventory_item_with_effects(
                interaction.user.id, item_id
            )

            message = result.get("message", "Item used successfully!")
            if result.get("cooldown_seconds"):
                mins, secs = divmod(result["cooldown_seconds"], 60)
                message += f"\nCooldown: {mins}m {secs}s"

            embed = discord.Embed(
                title="Item Used",
                description=message,
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await Embeds.error(
                interaction,
                str(e),
                title="Use Item",
                ephemeral=True,
            )

    async def use_item_autocomplete(
        self,
        interaction: Interaction,
        current: str,
    ) -> list[app_commands.Choice[int]]:
        """Autocomplete item names for the /use command."""

        entries = await self.bot.database.get_user_inventory_grouped(
            interaction.user.id
        )
        choices = []
        for entry in entries:
            name = entry.get("name", "Unknown")
            item_id = entry.get("id")
            if item_id is None:
                continue
            quantity = entry.get("quantity", 1)
            display = f"{name} (x{quantity})"
            if current.lower() in name.lower() or not current:
                choices.append(
                    app_commands.Choice(name=display[:100], value=item_id)
                )
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="trade", description="Trade an item to another user")
    @app_commands.checks.bot_has_permissions(embed_links=True, send_messages=True)
    @unified_cooldown(10)
    async def trade_item(
        self,
        interaction: Interaction,
        member: discord.Member,
        item_id: int,
        quantity: int = 1,
    ):
        if member.bot or member.id == interaction.user.id:
            await interaction.response.send_message(
                "Invalid target user.", ephemeral=True
            )
            return

        # Get the item to verify ownership and get details
        item = await self.bot.database.get_user_item(interaction.user.id, item_id)
        if not item:
            await interaction.response.send_message(
                "You don't own this item.", ephemeral=True
            )
            return

        if item.quantity < quantity:
            await interaction.response.send_message(
                f"You only have {item.quantity} of this item.", ephemeral=True
            )
            return

        try:
            # Create pending trade request
            trade = await self.bot.database.create_trade_request(
                interaction.user.id, member.id, item_id, quantity
            )

            embed = discord.Embed(
                title="Trade Request Sent",
                description=f"Request to trade **{item.name}** x{quantity} to {member.display_name}",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Trade ID", value=str(trade.id), inline=True)
            embed.set_footer(text="Waiting for recipient to respond...")

            # Create trade request view for the recipient
            view = TradeRequestView(
                bot=self.bot,
                trade_id=trade.id,
                from_user_id=interaction.user.id,
                to_user_id=member.id,
                item_name=item.name,
                quantity=quantity,
            )

            # Send confirmation to recipient
            recipient_embed = discord.Embed(
                title="Trade Request",
                description=f"{interaction.user.display_name} wants to trade with you!",
                color=discord.Color.gold(),
            )
            recipient_embed.add_field(name="Item", value=f"{item.name} x{quantity}", inline=True)
            if item.description:
                recipient_embed.add_field(name="Description", value=item.description, inline=False)
            recipient_embed.set_footer(text="You have 2 minutes to respond")

            try:
                await member.send(embed=recipient_embed, view=view)
                embed.add_field(name="Status", value="Notification sent to recipient", inline=False)
            except discord.Forbidden:
                # DMs disabled, send to channel
                embed.add_field(name="Status", value="Could not DM recipient - they may have DMs disabled", inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                f"Failed to create trade: {str(e)}", ephemeral=True
            )

    @app_commands.command(name="effects", description="View your active effects from items")
    @unified_cooldown(30)
    @app_commands.checks.bot_has_permissions(embed_links=True, send_messages=True)
    async def view_effects(self, interaction: Interaction):
        effects = await self.bot.database.get_user_active_effects(interaction.user.id)

        if not effects:
            await Embeds.warning(
                interaction,
                "You have no active effects.",
                title="Active Effects",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Your Active Effects",
            color=discord.Color.blurple(),
        )

        for effect in effects:
            expires_in = effect.expires_at - datetime.utcnow()
            mins, secs = divmod(int(expires_in.total_seconds()), 60)
            hours, mins = divmod(mins, 60)

            if hours > 0:
                time_str = f"{hours}h {mins}m {secs}s"
            elif mins > 0:
                time_str = f"{mins}m {secs}s"
            else:
                time_str = f"{secs}s"

            effect_names = {
                "currency": "💰 Currency",
                "gambling_multiplier": "🎰 Gambling Multiplier",
                "luck_boost": "🍀 Luck Boost",
                "earning_boost": "📈 Earning Boost",
                "cooldown_reduction": "⏱️ Cooldown Reduction",
                "rtp_boost": "📊 RTP Boost",
            }
            display_name = effect_names.get(effect.effect_type, effect.effect_type)

            embed.add_field(
                name=f"{display_name}",
                value=f"**Value:** {effect.effect_value}x\n**From:** {effect.source_item_name}\n**Expires in:** {time_str}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="trades", description="View your pending trade requests")
    @unified_cooldown(30)
    @app_commands.checks.bot_has_permissions(embed_links=True, send_messages=True)
    async def pending_trades(self, interaction: Interaction):
        trades = await self.bot.database.get_pending_trades(interaction.user.id)

        if not trades:
            await Embeds.warning(
                interaction,
                "You have no pending trade requests.",
                title="Pending Trades",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Your Pending Trades",
            color=discord.Color.blurple(),
        )

        trade_user_ids = []
        for t in trades:
            trade_user_ids.extend([getattr(t, "from_user_id", None), getattr(t, "to_user_id", None)])
        resolved_trade_ids = await resolve_ids(self.bot.database,trade_user_ids)

        incoming = [t for t in trades if resolved_trade_ids.get(getattr(t, "to_user_id", None)) == interaction.user.id]
        outgoing = [t for t in trades if resolved_trade_ids.get(getattr(t, "from_user_id", None)) == interaction.user.id]

        if incoming:
            incoming_str = "\n".join([
                f"**ID {t.id}:** {t.item_name} x{t.quantity} from <@{resolved_trade_ids.get(getattr(t, 'from_user_id', None)) or 'Unknown user'}>"
                for t in incoming[:5]
            ])
            if len(incoming) > 5:
                incoming_str += f"\n... and {len(incoming) - 5} more"
            embed.add_field(name="📥 Incoming", value=incoming_str, inline=False)

        if outgoing:
            outgoing_str = "\n".join([
                f"**ID {t.id}:** {t.item_name} x{t.quantity} to <@{resolved_trade_ids.get(getattr(t, 'to_user_id', None)) or 'Unknown user'}>"
                for t in outgoing[:5]
            ])
            if len(outgoing) > 5:
                outgoing_str += f"\n... and {len(outgoing) - 5} more"
            embed.add_field(name="📤 Outgoing", value=outgoing_str, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==================== VIP Commands ====================

    @commands.group(name="vip", invoke_without_command=True)
    async def vip_group(self, ctx: commands.Context):
        """Group command for VIP tier information."""
        prefix = "/"
        if ctx.message:
            prefix = await self.bot.get_prefix(ctx.message)
            if isinstance(prefix, list):
                prefix = prefix[0]

        subcmds = getattr(ctx.command, "commands", []) or []
        lines = []
        for cmd in sorted(subcmds, key=lambda c: c.name):
            name = cmd.name
            aliases = (
                f" (or: {', '.join(cmd.aliases)})"
                if getattr(cmd, "aliases", None)
                else ""
            )
            desc = (cmd.help or cmd.description or "").strip()
            if desc:
                lines.append(f"`{prefix}vip {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}vip {name}`{aliases}")

        description = "\n".join(lines) if lines else "No subcommands available."

        await Embeds.custom(
            ctx,
            description,
            title="VIP — Available Commands",
            color=discord.Color.gold(),
            footer=f"Use {prefix}vip <subcommand> for details.",
            delete_after=None,
            reply=True,
            mention_author=False,
        )

    @vip_group.command(name="status", aliases=["stat"], description="View your VIP tier status and progress")
    @unified_cooldown(10)
    async def vip_status(self, ctx: commands.Context):
        """View your current VIP tier status."""
        try:
            vip_info = await self.bot.database.get_rakeback_info(ctx.author.id)
            current_tier = vip_info.get("current_tier")
            next_tier = vip_info.get("next_tier")
            total_wagered = vip_info.get("total_wagered", Decimal("0"))
            rakeback_rate = vip_info.get("rakeback_rate", Decimal("0.01"))

            if current_tier is None:
                # Ensure default VIP tiers exist
                await self.bot.database.upgrade_user_vip(ctx.author.id)  # This will create a VIP record if it doesn't exist
                vip_info = await self.bot.database.get_rakeback_info(ctx.author.id)
                current_tier = vip_info.get("current_tier")
                next_tier = vip_info.get("next_tier")
                total_wagered = vip_info.get("total_wagered", Decimal("0"))
                rakeback_rate = vip_info.get("rakeback_rate", Decimal("0.01"))

            tier_colors = {
                "Unranked": discord.Color.dark_grey(),
                "Bronze": discord.Color.orange(),
                "Silver": discord.Color.light_grey(),
                "Gold": discord.Color.gold(),
                "Platinum": discord.Color.lighter_grey(),
                "Diamond": discord.Color.blue(),
            }

            color = tier_colors.get(current_tier.name if current_tier else "Bronze", discord.Color.default())

            embed = discord.Embed(
                title=f"{'💎' if current_tier and current_tier.name == 'Diamond' else '👑'} VIP Status",
                color=color,
            )

            tier_emoji = {
                "Unranked": "⚪",
                "Bronze": "🥉",
                "Silver": "🥈",
                "Gold": "🥇",
                "Platinum": "💠",
                "Diamond": "💎",
            }

            if current_tier:
                emoji = tier_emoji.get(current_tier.name, "⭐")
                embed.add_field(
                    name="Current Tier",
                    value=f"{emoji} **{current_tier.name}** (Level {current_tier.level})",
                    inline=False,
                )

                # Progress bar
                if next_tier:
                    progress = float(total_wagered) / float(next_tier.min_wagered)
                    progress = min(progress, 1.0)
                    filled = int(progress * 10)
                    bar = "█" * filled + "░" * (10 - filled)
                    progress_pct = f"{progress * 100:.1f}%"
                    next_amount = float(next_tier.min_wagered) - float(total_wagered)
                    embed.add_field(
                        name="Progress to Next Tier",
                        value=f"`{bar}` {progress_pct}\nNeed **{await self.formatter(Decimal(str(next_amount)))}** more to reach **{next_tier.name}**",
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="Progress",
                        value="✨ **Maximum Tier Reached!**",
                        inline=False,
                    )

                embed.add_field(
                    name="Total Wagered",
                    value=f"💰 **{await self.formatter(total_wagered)}**",
                    inline=True,
                )
                embed.add_field(
                    name="Rakeback Rate",
                    value=f"📈 **{float(rakeback_rate) * 100:.1f}%**",
                    inline=True,
                )
                embed.add_field(
                    name="RTP Bonus",
                    value=f"📊 **+{float(current_tier.rtp_bonus) * 100:.1f}%**",
                    inline=True,
                )
            else:
                embed.add_field(
                    name="Current Tier",
                    value="🥉 **Bronze** (Level 1)",
                    inline=False,
                )
                embed.add_field(
                    name="Total Wagered",
                    value=f"💰 **{await self.formatter(total_wagered)}**",
                    inline=True,
                )

            await ctx.reply(embed=embed)

        except Exception as e:
            logger.error(f"Error in vip_status: {e}")
            await ctx.reply(
                "An error occurred while fetching your VIP status.",
                ephemeral=True,
            )

    @vip_group.command(name="tiers", aliases=['tier'], description="View all VIP tiers and their benefits")
    @unified_cooldown(10)
    async def vip_tiers(self, ctx: commands.Context):
        """Display all VIP tiers with benefits."""
        try:
            tiers = await self.bot.database.get_all_vip_tiers()

            if not tiers:
                await self.bot.database.upgrade_user_vip(ctx.author.id)  # This will create a VIP record if it doesn't exist
                tiers = await self.bot.database.get_all_vip_tiers()

            tier_emojis = {
                "Unranked": "⚪",
                "Bronze": "🥉",
                "Silver": "🥈",
                "Gold": "🥇",
                "Platinum": "💠",
                "Diamond": "💎",
            }

            embed = discord.Embed(
                title="👑 VIP Tiers",
                description="Climb the ranks by wagering more! Higher tiers get better rakeback and RTP bonuses.",
                color=discord.Color.gold(),
            )

            for tier in tiers:
                emoji = tier_emojis.get(tier.name, "⭐")
                rakeback_pct = float(tier.rakeback_rate) * 100
                rtp_pct = float(tier.rtp_bonus) * 100
                min_wagered_str = await self.formatter(tier.min_wagered)

                embed.add_field(
                    name=f"{emoji} {tier.name}",
                    value=(
                        f"**Min Wagered:** {min_wagered_str}\n"
                        f"**Rakeback:** {rakeback_pct:.0f}%\n"
                        f"**RTP Bonus:** +{rtp_pct:.1f}%"
                    ),
                    inline=True,
                )

            await ctx.reply(embed=embed)

        except Exception as e:
            logger.error(f"Error in vip_tiers: {e}")
            await ctx.reply(
                "An error occurred while fetching VIP tiers."
            )

    @vip_group.command(name="leaderboard", aliases=["lb"], description="View top players by total wagered")
    @unified_cooldown(10)
    async def vip_leaderboard(self, ctx: commands.Context):
        """Display top VIP players by total wagered."""
        try:
            leaderboard = await self.bot.database.get_vip_leaderboard(limit=10)

            if not leaderboard:
                await ctx.reply(
                    "No VIP data available yet.",
                )
                return

            tier_emojis = {
                "Unranked": "⚪",
                "Bronze": "🥉",
                "Silver": "🥈",
                "Gold": "🥇",
                "Platinum": "💠",
                "Diamond": "💎",
            }

            embed = discord.Embed(
                title="🏆 VIP Leaderboard",
                description="Top players by total wagered",
                color=discord.Color.gold(),
            )

            description_lines = []
            user_ids = [entry.get("user_id") for entry in leaderboard]
            resolved_vip_ids = await resolve_ids(self.bot.database,user_ids)
            for i, entry in enumerate(leaderboard, 1):
                user_id = entry.get("user_id")
                total_wagered = entry["total_wagered"]
                tier = entry.get("tier")

                tier_name = tier.name if tier else "Bronze"
                emoji = tier_emojis.get(tier_name, "⭐")

                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"

                formatted_wagered = await self.formatter(total_wagered)
                raw_id = resolved_vip_ids.get(user_id)
                mention = f"<@{raw_id}>" if raw_id else "Unknown user"
                description_lines.append(f"{medal} {mention} - {emoji} {tier_name} - **{formatted_wagered}** wagered")

            embed.description = "\n".join(description_lines)

            await ctx.reply(embed=embed)

        except Exception as e:
            logger.error(f"Error in vip_leaderboard: {e}")
            await ctx.reply(
                "An error occurred while fetching the leaderboard.",
            )

    @vip_group.command(name="claim", description="Claim your accumulated rakeback")
    @unified_cooldown(3600)
    async def claim_rakeback(self, ctx: commands.Context):
        """Claim accumulated rakeback."""
        try:
            balance = await self.bot.database.get_rakeback_balance(ctx.author.id)

            if balance <= Decimal("0"):
                await Embeds.warning(
                    ctx,
                    "You have no accumulated rakeback to claim.\n\nPlay more games to earn rakeback on your wagers!",
                    title="Rakeback",
                    ephemeral=True,
                    delete_after=None,
                    reply=True,
                )
                return

            claimed = await self.bot.database.claim_rakeback(ctx.author.id)

            if claimed > 0:
                formatted_amount = await self.formatter(claimed)
                await Embeds.success(
                    ctx,
                    f"You claimed **{formatted_amount}** {self.currency_name}!",
                    title="💸 Rakeback Claimed!",
                    delete_after=None,
                    reply=True,
                    mention_author=True,
                )
            else:
                await ctx.reply(
                    "No rakeback available to claim.",
                )

        except Exception as e:
            logger.error(f"Error in claim_rakeback: {e}")
            await ctx.reply(
                "An error occurred while claiming rakeback.",
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
    logger.debug("Economy cog initialized successfully")