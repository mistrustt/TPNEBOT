import discord
from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import os
import uuid
import copy
import hashlib
import json
import re
import textwrap
import logging
import asyncio
import time
import statistics
import psutil
from utils.misc import MiscUtils
from utils.metrics_charts import MetricsChartView
from utils.fairness import (
    SUPERGAMBLE_WIN_THRESHOLD, SUPERGAMBLE_BASE_MULTIPLIER,
    SUPERGAMBLE_BONUS_MULTIPLIER, SUPERGAMBLE_MEGA_THRESHOLD,
    DICE_PAYOUTS, DICE_EVEN_ODD_PAYOUT,
    LADDER_STEP_PROBS, LADDER_STEP_MULTS, LADDER_MAX_STEP,
    CRASH_RANGES, CRASH_BUCKET_NAMES,
    ROULETTE_ALL_NUMBERS, ROULETTE_RED_NUMBERS, ROULETTE_BLACK_NUMBERS,
    SLOTS_REEL_WEIGHTS, SLOTS_PAYLINES, SLOTS_SYMBOLS,
    evaluate_slots,
)
from sqlalchemy.exc import SQLAlchemyError
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from sqlalchemy import text, select, func
from typing import Optional, Union, Any, Iterable, List
from datetime import datetime, timedelta
from database.models import (
    CommandUsageDaily,
    CommandLatencyDaily,
    CommandErrorDaily,
    DailyUserExposure,
    RakebackBalance,
)
from database.manager import ItemType
import importlib.util
from utils.embeds import Embeds

logger = logging.getLogger("discord_bot")

MAX_FIELD_VALUE_LENGTH = 1024


ITEMS_PER_PAGE = 15


def _bar_visual(value: int | float, max_value: int | float, width: int = 12) -> str:
    """Return a small ASCII bar for embed visualisation."""
    if not max_value or max_value <= 0:
        return "░" * width
    ratio = min(1.0, max(0.0, float(value) / float(max_value)))
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)


def _human_number(value: int | float) -> str:
    """Format a number with commas, trimming decimals when whole."""
    value = float(value)
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


class MetricsPaginator(discord.ui.View):
    """Paginator for displaying metrics data with navigation buttons."""

    def __init__(
        self,
        data: list[tuple],
        title: str,
        author_id: int,
        format_func: callable = None,
        footer_text: str = None,
        color: discord.Color = discord.Color.blurple(),
        summary: dict[str, str] = None,
        chart_view: discord.ui.View = None,
    ):
        super().__init__(timeout=180)
        self.data = data
        self.title = title
        self.author_id = author_id
        self.format_func = format_func or (lambda x, rank=None: str(x))
        self.footer_text = footer_text
        self.color = color
        self.summary = summary or {}
        self.chart_view = chart_view
        self.current_page = 0
        self.per_page = ITEMS_PER_PAGE
        self._formatted_lines: list[str] = []
        self._page_boundaries: list[tuple[int, int]] = []
        self._compute_pages()

    def _compute_pages(self):
        """Pre-format rows and split them into pages that fit in a Discord embed field."""
        self._formatted_lines.clear()
        self._page_boundaries.clear()
        if not self.data:
            self._page_boundaries.append((0, 0))
            return

        for idx, item in enumerate(self.data):
            line = self.format_func(item, rank=idx + 1)
            if len(line) > MAX_FIELD_VALUE_LENGTH:
                line = line[:MAX_FIELD_VALUE_LENGTH - 3] + "..."
            self._formatted_lines.append(line)

        start = 0
        current_len = 0
        for idx, line in enumerate(self._formatted_lines):
            line_len = len(line) + 1
            if idx > start and (
                idx - start >= self.per_page
                or current_len + line_len > MAX_FIELD_VALUE_LENGTH
            ):
                self._page_boundaries.append((start, idx))
                start = idx
                current_len = line_len
            else:
                current_len += line_len

        self._page_boundaries.append((start, len(self._formatted_lines)))

    def get_total_pages(self) -> int:
        """Calculate total number of pages."""
        return max(1, len(self._page_boundaries))

    def get_page_embed(self) -> discord.Embed:
        """Generate embed for current page."""
        embed = discord.Embed(title=self.title, color=self.color)

        if self.summary:
            embed.description = "\n".join(
                f"{emoji} **{label}:** {value}" for emoji, label, value in self._summary_rows()
            )

        if not self.data:
            if not embed.description:
                embed.description = "No data available."
            return embed

        start, end = self._page_boundaries[self.current_page]
        body = "\n".join(self._formatted_lines[start:end])

        if embed.description:
            embed.add_field(name="Entries", value=body, inline=False)
        else:
            embed.description = body

        total_pages = self.get_total_pages()
        footer = f"Page {self.current_page + 1}/{total_pages}"
        if self.footer_text:
            footer = f"{footer} | {self.footer_text}"
        embed.set_footer(text=footer)

        return embed

    def _summary_rows(self):
        """Yield (emoji, label, value) tuples for the embed summary."""
        emoji_map = {
            "total": "📊",
            "count": "🔢",
            "average": "📈",
            "peak": "🚀",
            "unique": "👤",
            "range": "📅",
            "guild": "🏰",
        }
        for label, value in self.summary.items():
            emoji = emoji_map.get(label.lower(), "•")
            yield emoji, label, value

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allow the original author to interact."""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This metrics view isn't for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="◀️")
    async def previous(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(
                embed=self.get_page_embed(), view=self
            )
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="▶️")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_pages = self.get_total_pages()
        if self.current_page < total_pages - 1:
            self.current_page += 1
            await interaction.response.edit_message(
                embed=self.get_page_embed(), view=self
            )
        else:
            await interaction.response.defer()

    @discord.ui.button(label="📊 Graph", style=discord.ButtonStyle.primary)
    async def show_graph(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not self.chart_view:
            return await interaction.response.defer()

        await interaction.response.defer()
        try:
            buffer = self.chart_view._render_chart()
            file = discord.File(buffer, filename="metrics.png")
            await interaction.followup.send(file=file)
        except Exception as e:
            await interaction.followup.send(
                f"Failed to render chart: {e}", ephemeral=True
            )


class TodoPaginator(discord.ui.View):
    def __init__(self, tasks, author, per_page=5):
        super().__init__(timeout=60)
        self.tasks = tasks
        self.author = author
        self.per_page = per_page
        self.current_page = 0

    def get_page_embed(self):
        embed = discord.Embed(title="Todo List", color=discord.Color.blurple())
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_tasks = self.tasks[start:end]
        if not page_tasks:
            embed.description = "Your todo list is empty!"
        else:
            for idx, item in enumerate(page_tasks, start=start + 1):
                status = (
                    "<:checkmark:1336131637494284462>"
                    if item.completed
                    else "<:crossmark:1336131639159291996>"
                )

                embed.add_field(
                    name=f"Task {idx} | Status: {status}", value=item.task, inline=False
                )
            total_pages = (len(self.tasks) - 1) // self.per_page + 1
            embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author.id

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(
                embed=self.get_page_embed(), view=self
            )
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_pages = (len(self.tasks) - 1) // self.per_page + 1
        if self.current_page < total_pages - 1:
            self.current_page += 1
            await interaction.response.edit_message(
                embed=self.get_page_embed(), view=self
            )
        else:
            await interaction.response.defer()


class ShopItemModal(discord.ui.Modal, title="Create Shop Item"):
    """Modal for creating/editing shop items with effect configuration."""

    def __init__(self, bot, edit_item=None):
        super().__init__()
        self.bot = bot
        self.edit_item = edit_item

        # Name input
        self.name_input = discord.ui.TextInput(
            label="Item Name",
            placeholder="Enter the item name",
            max_length=100,
            default=edit_item.name if edit_item else None,
        )
        self.add_item(self.name_input)

        # Description input
        self.description_input = discord.ui.TextInput(
            label="Description",
            placeholder="Enter item description",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False,
            default=edit_item.description if edit_item else None,
        )
        self.add_item(self.description_input)

        # Price input
        self.price_input = discord.ui.TextInput(
            label="Price (coins)",
            placeholder="Enter the price",
            max_length=20,
            default=str(edit_item.price) if edit_item else None,
        )
        self.add_item(self.price_input)

        # Quantity and unlimited
        self.quantity_input = discord.ui.TextInput(
            label="Quantity (or 'unlimited')",
            placeholder="Enter quantity or 'unlimited'",
            max_length=20,
            default="unlimited" if (edit_item and edit_item.unlimited) else (str(edit_item.quantity) if edit_item else None),
        )
        self.add_item(self.quantity_input)

        # Item type input
        item_type_default = edit_item.item_type.value if (edit_item and edit_item.item_type) else "collectible"
        self.item_type_input = discord.ui.TextInput(
            label="Item Type (collectible/redeemable/consumable)",
            placeholder="collectible, redeemable, or consumable",
            max_length=20,
            default=item_type_default,
        )
        self.add_item(self.item_type_input)

        # Effect configuration
        self.effect_input = discord.ui.TextInput(
            label="Effect Type (currency/gambling_multiplier/luck_boost/earning_boost/cooldown_reduction)",
            placeholder="Leave empty for no effect",
            max_length=50,
            required=False,
            default=edit_item.effect if (edit_item and edit_item.effect) else None,
        )
        self.add_item(self.effect_input)

        # Effect value
        self.effect_value_input = discord.ui.TextInput(
            label="Effect Value",
            placeholder="e.g., 100 for currency, 1.5 for 1.5x multiplier",
            max_length=20,
            required=False,
            default=str(edit_item.effect_value) if (edit_item and edit_item.effect_value) else None,
        )
        self.add_item(self.effect_value_input)

        # Effect duration
        self.effect_duration_input = discord.ui.TextInput(
            label="Effect Duration (seconds)",
            placeholder="e.g., 3600 for 1 hour. Leave empty for instant effects",
            max_length=10,
            required=False,
            default=str(edit_item.effect_duration) if (edit_item and edit_item.effect_duration) else None,
        )
        self.add_item(self.effect_duration_input)

        # Cooldown
        self.cooldown_input = discord.ui.TextInput(
            label="Cooldown (seconds)",
            placeholder="Time between uses. Leave empty for no cooldown",
            max_length=10,
            required=False,
            default=str(edit_item.cooldown_seconds) if (edit_item and edit_item.cooldown_seconds) else None,
        )
        self.add_item(self.cooldown_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            name = self.name_input.value.strip()
            description = self.description_input.value.strip() or None
            price = int(self.price_input.value.strip())

            # Handle quantity/unlimited
            quantity_str = self.quantity_input.value.strip().lower()
            unlimited = quantity_str == "unlimited"
            quantity = 1 if unlimited else int(quantity_str)

            # Handle item type
            item_type_str = self.item_type_input.value.strip().upper()
            try:
                item_type = ItemType[item_type_str]
            except KeyError:
                item_type = ItemType.COLLECTIBLE  # Default fallback

            # Handle effect configuration
            effect = self.effect_input.value.strip().lower() or None
            effect_value = None
            effect_duration = None
            cooldown_seconds = None

            if effect:
                if self.effect_value_input.value.strip():
                    effect_value = int(float(self.effect_value_input.value.strip()))
                if self.effect_duration_input.value.strip():
                    effect_duration = int(self.effect_duration_input.value.strip())

            if self.cooldown_input.value.strip():
                cooldown_seconds = int(self.cooldown_input.value.strip())

            if self.edit_item:
                # Update existing item
                async with self.bot.database.async_sessionmaker() as session:
                    from sqlalchemy import update
                    stmt = update(type(self.edit_item)).where(type(self.edit_item).id == self.edit_item.id).values(
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
                    await session.execute(stmt)
                    await session.commit()

                embed = discord.Embed(
                    title="Shop Item Updated",
                    description=f"Updated **{name}** in the shop.",
                    color=discord.Color.green(),
                )
            else:
                # Create new item
                await self.bot.database.add_shop_item(
                    name=name,
                    description=description,
                    price=price,
                    quantity=quantity,
                    item_type=item_type,
                    unlimited=unlimited,
                    effect=effect,
                    effect_value=effect_value,
                    effect_duration=effect_duration,
                    cooldown_seconds=cooldown_seconds,
                )

                embed = discord.Embed(
                    title="Shop Item Created",
                    description=f"Added **{name}** to the shop for {price} coins.",
                    color=discord.Color.green(),
                )

            if effect:
                embed.add_field(name="Effect", value=f"{effect}: {effect_value or 'N/A'}", inline=True)
            if effect_duration:
                embed.add_field(name="Duration", value=f"{effect_duration}s", inline=True)
            if cooldown_seconds:
                embed.add_field(name="Cooldown", value=f"{cooldown_seconds}s", inline=True)

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except ValueError as e:
            await interaction.response.send_message(
                f"Invalid input: {str(e)}. Please check your values and try again.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Error creating item: {str(e)}",
                ephemeral=True,
            )


def _parse_cog_list(arg: str) -> List[str]:
    """Split comma/space separated cogs safely; always return a list."""
    if not arg:
        return []
    # Support: "mod, util  ,admin" or "mod util admin"
    parts = [p.strip() for chunk in arg.split(",") for p in chunk.split() if p.strip()]
    # Deduplicate while preserving order
    seen = set()
    result = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _exists_cog_file(name: str) -> bool:
    return os.path.exists(os.path.join("cogs", f"{name}.py"))


def _importable_cog(path: str) -> bool:
    return importlib.util.find_spec(path) is not None


def _fmt_list(items: Iterable[str]) -> str:
    return ", ".join(items) if items else "—"


async def _owner_check(interaction: discord.Interaction) -> bool:
    """App-command check used for owner-only slash commands."""
    return await interaction.client.is_owner(interaction.user)

class Owner(commands.Cog, name="Owner"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.currency_name = "<:coin:1359823671581085847>"
        self.utils = MiscUtils(self)
        self.process = psutil.Process(os.getpid())
        self._last_result: Optional[Any] = None
        self.start_time = discord.utils.utcnow()

    @staticmethod
    def _is_hash(value) -> bool:
        """Return True if a stored user ID value is a HMAC-SHA256 hex hash."""
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(c in "0123456789abcdefABCDEF" for c in value)
        )

    async def _resolve_id(self, value):
        """Resolve a stored user ID to a raw Discord ID when it is a hash."""
        if value is None or isinstance(value, int):
            return value
        if self._is_hash(value):
            return await self.bot.database.resolve_user_hash(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _resolve_ids(self, values):
        """Batch-resolve stored user IDs, leaving raw IDs unchanged."""
        if not values:
            return {}
        unique = list(dict.fromkeys(v for v in values if v is not None))
        hashes = [v for v in unique if self._is_hash(v)]
        resolved = await self.bot.database.resolve_user_hashes(hashes) if hashes else {}
        mapping = {}
        for v in unique:
            if isinstance(v, int):
                mapping[v] = v
            elif self._is_hash(v):
                mapping[v] = resolved.get(v)
            else:
                try:
                    mapping[v] = int(v)
                except (TypeError, ValueError):
                    mapping[v] = None
        return mapping

    def is_whitelisted_clubhouse(self, user_id: int):
        """Check if the user ID is in the whitelist."""
        list_string = json.dumps(sorted(self.whitelist_clubhouse), separators=(',', ':'))
    
        list_hash = hashlib.sha256(list_string.encode()).hexdigest()
        return list_hash, user_id in self.whitelist_clubhouse

    def is_whitelisted_tpne(self, user_id: int):
        """Check if the user ID is in the whitelist."""
        list_string = json.dumps(sorted(self.whitelist_tpne), separators=(',', ':'))
    
        list_hash = hashlib.sha256(list_string.encode()).hexdigest()
        return list_hash, user_id in self.whitelist_tpne

    def is_whitelisted_mistrust(self, user_id: int):
        """Check if the user ID is in the whitelist."""
        list_string = json.dumps(sorted(self.whitelist_mistrust), separators=(',', ':'))
    
        list_hash = hashlib.sha256(list_string.encode()).hexdigest()
        return list_hash, user_id in self.whitelist_mistrust
    
    def is_whitelisted_private(self, user_id: int):
        """Check if the user ID is in the whitelist."""
        list_string = json.dumps(sorted(self.whitelist_private), separators=(',', ':'))
    
        list_hash = hashlib.sha256(list_string.encode()).hexdigest()
        return list_hash, user_id in self.whitelist_private

    @commands.group(
        name="metrics",
        help="Metrics commands for bot stats.",
        invoke_without_command=True,
        hidden=True,
    )
    @commands.is_owner()
    async def metrics(self, ctx: Context):
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
                lines.append(f"`{prefix}metrics {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}metrics {name}`{aliases}")

        if not lines:
            description = "No subcommands available."
        else:
            description = "\n".join(lines)

        embed = discord.Embed(
            title="Metrics — Available Commands",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Use {prefix}metrics <subcommand> for details.")

        await ctx.reply(embed=embed, mention_author=False)

    @metrics.command(name="usage", hidden=True)
    @commands.is_owner()
    async def metrics_usage(
        self,
        ctx: Context,
        days: int = 7,
        *,
        guild_id: Optional[int] = None,
    ) -> None:
        """Display command usage metrics for the specified time range.

        Args:
            days: Number of days to look back (1-365, default 7)
            guild_id: Optional guild ID to filter by
        """
        if days < 1 or days > 365:
            return await ctx.send("❌ Days must be between 1 and 365.")

        cutoff = discord.utils.utcnow().date() - timedelta(days=days)

        try:
            async with self.bot.database.async_sessionmaker() as session:
                stmt = (
                    select(
                        CommandUsageDaily.command_name,
                        CommandUsageDaily.is_slash,
                        func.sum(CommandUsageDaily.count).label("total"),
                    )
                    .group_by(CommandUsageDaily.command_name, CommandUsageDaily.is_slash)
                    .order_by(text("total DESC"))
                )
                stmt = stmt.where(CommandUsageDaily.bucket_date >= cutoff)
                if guild_id:
                    stmt = stmt.where(CommandUsageDaily.guild_id == guild_id)

                result = await session.execute(stmt)
                rows = result.all()
        except Exception as e:
            self.bot.logger.error(f"Database error in metrics_usage: {e}")
            return await ctx.send("❌ An error occurred while fetching usage data. Please try again later.")

        if not rows:
            return await ctx.send(f"No command usage data found for the last {days} day(s).")

        grand_total = sum(int(r[2]) for r in rows)
        max_total = max(int(r[2]) for r in rows)
        slash_total = sum(int(r[2]) for r in rows if r[1])
        prefix_total = grand_total - slash_total

        def format_row(row, rank):
            name, is_slash, total = row
            total = int(total)
            pct = (total / grand_total * 100) if grand_total else 0
            bar = _bar_visual(total, max_total)
            icon = "⚡" if is_slash else "⌨️"
            return (
                f"`{rank:>2}.` {icon} `{name[:24]:<24}` "
                f"{bar} `{_human_number(total):>10}` ({pct:.1f}%)"
            )

        summary = {
            "Total": _human_number(grand_total),
            "Slash": f"{_human_number(slash_total)} ({slash_total / grand_total * 100:.1f}%)",
            "Prefix": f"{_human_number(prefix_total)} ({prefix_total / grand_total * 100:.1f}%)",
            "Commands": str(len(rows)),
        }

        paginator = MetricsPaginator(
            data=rows,
            title="📊 Command Usage",
            author_id=ctx.author.id,
            format_func=format_row,
            footer_text=f"Days: {days}",
            summary=summary,
            chart_view=MetricsChartView(
                self.bot,
                ctx.author.id,
                chart_type="bar",
                title="Command Usage",
                labels=[f"{name} ({'slash' if is_slash else 'prefix'})" for name, is_slash, _ in rows],
                values=[int(total) for _, _, total in rows],
                ylabel="Calls",
                limit=15,
            ),
        )
        await ctx.send(embed=paginator.get_page_embed(), view=paginator)

    @metrics.command(name="latency", hidden=True)
    @commands.is_owner()
    async def metrics_latency(
        self,
        ctx: Context,
        days: int = 7,
        *,
        guild_id: Optional[int] = None,
    ) -> None:
        """Display command latency metrics for the specified time range.

        Args:
            days: Number of days to look back (1-365, default 7)
            guild_id: Optional guild ID to filter by
        """
        if days < 1 or days > 365:
            return await ctx.send("❌ Days must be between 1 and 365.")

        cutoff = discord.utils.utcnow().date() - timedelta(days=days)

        try:
            async with self.bot.database.async_sessionmaker() as session:
                stmt = (
                    select(
                        CommandLatencyDaily.command_name,
                        CommandLatencyDaily.is_slash,
                        func.sum(CommandLatencyDaily.latency_ms_sum).label("sum_ms"),
                        func.sum(CommandLatencyDaily.latency_count).label("count"),
                    )
                    .group_by(CommandLatencyDaily.command_name, CommandLatencyDaily.is_slash)
                    .order_by(text("sum_ms DESC"))
                )
                stmt = stmt.where(CommandLatencyDaily.bucket_date >= cutoff)
                if guild_id:
                    stmt = stmt.where(CommandLatencyDaily.guild_id == guild_id)

                result = await session.execute(stmt)
                rows = result.all()
        except Exception as e:
            self.bot.logger.error(f"Database error in metrics_latency: {e}")
            return await ctx.send("❌ An error occurred while fetching latency data. Please try again later.")

        if not rows:
            return await ctx.send(f"No command latency data found for the last {days} day(s).")

        processed = []
        for name, is_slash, sum_ms, count in rows:
            avg = int(sum_ms / count) if count else 0
            processed.append((name, is_slash, avg, int(count)))

        max_avg = max(r[2] for r in processed)
        grand_calls = sum(r[3] for r in processed)
        overall_avg = sum(r[2] * r[3] for r in processed) // grand_calls if grand_calls else 0

        def format_row(row, rank):
            name, is_slash, avg, count = row
            icon = "⚡" if is_slash else "⌨️"
            bar = _bar_visual(avg, max_avg)
            color = "🟢" if avg <= overall_avg * 1.2 else ("🟡" if avg <= overall_avg * 2 else "🔴")
            return (
                f"`{rank:>2}.` {icon} `{name[:24]:<24}` "
                f"{bar} `{avg:>6} ms` {color} ({_human_number(count)} calls)"
            )

        summary = {
            "Avg": f"{overall_avg} ms",
            "Calls": _human_number(grand_calls),
            "Entries": str(len(processed)),
        }

        paginator = MetricsPaginator(
            data=processed,
            title="⏱️ Command Latency",
            author_id=ctx.author.id,
            format_func=format_row,
            footer_text=f"Days: {days}",
            summary=summary,
            chart_view=MetricsChartView(
                self.bot,
                ctx.author.id,
                chart_type="bar",
                title="Command Latency (Avg)",
                labels=[f"{name} ({'slash' if is_slash else 'prefix'})" for name, is_slash, _, _ in rows],
                values=[avg for _, _, avg, _ in processed],
                ylabel="Avg ms",
                limit=15,
            ),
        )
        await ctx.send(embed=paginator.get_page_embed(), view=paginator)

    @metrics.command(name="errors", hidden=True)
    @commands.is_owner()
    async def metrics_errors(
        self,
        ctx: Context,
        days: int = 7,
        *,
        guild_id: Optional[int] = None,
    ) -> None:
        """Display command error metrics for the specified time range.

        Args:
            days: Number of days to look back (1-365, default 7)
            guild_id: Optional guild ID to filter by
        """
        if days < 1 or days > 365:
            return await ctx.send("❌ Days must be between 1 and 365.")

        cutoff = discord.utils.utcnow().date() - timedelta(days=days)

        try:
            async with self.bot.database.async_sessionmaker() as session:
                stmt = (
                    select(
                        CommandErrorDaily.command_name,
                        CommandErrorDaily.error_type,
                        CommandErrorDaily.is_slash,
                        func.sum(CommandErrorDaily.count).label("total"),
                    )
                    .group_by(
                        CommandErrorDaily.command_name,
                        CommandErrorDaily.error_type,
                        CommandErrorDaily.is_slash,
                    )
                    .order_by(text("total DESC"))
                )
                stmt = stmt.where(CommandErrorDaily.bucket_date >= cutoff)
                if guild_id:
                    stmt = stmt.where(CommandErrorDaily.guild_id == guild_id)

                result = await session.execute(stmt)
                rows = result.all()
        except Exception as e:
            self.bot.logger.error(f"Database error in metrics_errors: {e}")
            return await ctx.send("❌ An error occurred while fetching error data. Please try again later.")

        if not rows:
            return await ctx.send(f"No command error data found for the last {days} day(s).")

        grand_total = sum(int(r[3]) for r in rows)
        max_total = max(int(r[3]) for r in rows)

        def format_row(row, rank):
            name, error_type, is_slash, total = row
            total = int(total)
            pct = (total / grand_total * 100) if grand_total else 0
            bar = _bar_visual(total, max_total)
            icon = "⚡" if is_slash else "⌨️"
            short_type = error_type.split(".")[-1][:18]
            return (
                f"`{rank:>2}.` {icon} `{name[:20]:<20}` · `{short_type:<18}` "
                f"{bar} `{_human_number(total):>8}` ({pct:.1f}%)"
            )

        # Aggregate errors per command for the chart
        command_totals = {}
        for name, _, _, total in rows:
            command_totals[name] = command_totals.get(name, 0) + int(total)
        chart_labels, chart_values = zip(*sorted(command_totals.items(), key=lambda x: x[1], reverse=True)) if command_totals else ([], [])

        summary = {
            "Total": _human_number(grand_total),
            "Commands Affected": str(len(command_totals)),
            "Error Types": str(len(rows)),
        }

        paginator = MetricsPaginator(
            data=rows,
            title="⚠️ Command Errors",
            author_id=ctx.author.id,
            format_func=format_row,
            footer_text=f"Days: {days}",
            summary=summary,
            chart_view=MetricsChartView(
                self.bot,
                ctx.author.id,
                chart_type="bar",
                title="Command Errors",
                labels=list(chart_labels),
                values=list(chart_values),
                ylabel="Errors",
                limit=15,
            ),
        )
        await ctx.send(embed=paginator.get_page_embed(), view=paginator)

    @metrics.command(name="exposure", hidden=True)
    @commands.is_owner()
    async def metrics_exposure(
        self,
        ctx: Context,
        days: int = 7,
        *,
        guild_id: Optional[int] = None,
    ) -> None:
        """Display user exposure metrics for the specified time range.

        Args:
            days: Number of days to look back (1-365, default 7)
            guild_id: Optional guild ID to filter by
        """
        if days < 1 or days > 365:
            return await ctx.send("❌ Days must be between 1 and 365.")

        cutoff = discord.utils.utcnow().date() - timedelta(days=days)

        try:
            async with self.bot.database.async_sessionmaker() as session:
                stmt = select(
                    func.count().label("rows"),
                    func.count(func.distinct(DailyUserExposure.user_hash)).label("unique"),
                )
                stmt = stmt.where(DailyUserExposure.bucket_date >= cutoff)
                if guild_id:
                    stmt = stmt.where(DailyUserExposure.guild_id == guild_id)

                result = await session.execute(stmt)
                row = result.first()
        except Exception as e:
            self.bot.logger.error(f"Database error in metrics_exposure: {e}")
            return await ctx.send("❌ An error occurred while fetching exposure data. Please try again later.")

        total_rows = row.rows if row else 0
        unique_users = row.unique if row else 0

        embed = discord.Embed(
            title="👤 User Exposure",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Unique Users", value=_human_number(unique_users), inline=True)
        embed.add_field(name="Exposure Rows", value=_human_number(total_rows), inline=True)
        embed.add_field(name="Daily Average", value=_human_number(total_rows // days), inline=True)
        embed.set_footer(text=f"Days: {days}")

        view = MetricsChartView(
            self.bot,
            ctx.author.id,
            chart_type="bar",
            title="User Exposure",
            labels=["Unique users", "Exposure rows"],
            values=[int(unique_users), int(total_rows)],
            ylabel="Count",
        )
        await ctx.send(embed=embed, view=view)

    @metrics.command(name="topguilds", hidden=True)
    @commands.is_owner()
    async def metrics_topguilds(
        self,
        ctx: Context,
        command_name: str,
        days: int = 7,
    ) -> None:
        """Display top guilds by usage for a specific command.

        Args:
            command_name: Name of the command to analyze
            days: Number of days to look back (1-365, default 7)
        """
        if days < 1 or days > 365:
            return await ctx.send("❌ Days must be between 1 and 365.")

        if not command_name or not command_name.strip():
            return await ctx.send("❌ Command name cannot be empty.")

        command_name = command_name.strip().lower()
        cutoff = discord.utils.utcnow().date() - timedelta(days=days)

        try:
            async with self.bot.database.async_sessionmaker() as session:
                stmt = (
                    select(
                        CommandUsageDaily.guild_id,
                        func.sum(CommandUsageDaily.count).label("total"),
                    )
                    .where(CommandUsageDaily.command_name == command_name)
                    .group_by(CommandUsageDaily.guild_id)
                    .order_by(text("total DESC"))
                )
                stmt = stmt.where(CommandUsageDaily.bucket_date >= cutoff)

                result = await session.execute(stmt)
                rows = result.all()
        except Exception as e:
            self.bot.logger.error(f"Database error in metrics_topguilds: {e}")
            return await ctx.send("❌ An error occurred while fetching guild data. Please try again later.")

        if not rows:
            return await ctx.send(f"No guild usage data found for command `{command_name}` in the last {days} day(s).")

        grand_total = sum(int(r[1]) for r in rows)
        max_total = max(int(r[1]) for r in rows)
        guild_count = len(rows)

        def format_row(row, rank):
            guild_id, total = row
            total = int(total)
            pct = (total / grand_total * 100) if grand_total else 0
            bar = _bar_visual(total, max_total)
            label = f"DM" if guild_id is None else f"G:{guild_id}"
            return (
                f"`{rank:>2}.` 🏰 `{label:<18}` "
                f"{bar} `{_human_number(total):>10}` ({pct:.1f}%)"
            )

        summary = {
            "Command": f"`{command_name}`",
            "Total Uses": _human_number(grand_total),
            "Guilds": str(guild_count),
        }

        paginator = MetricsPaginator(
            data=rows,
            title=f"🏰 Top Guilds for `{command_name}`",
            author_id=ctx.author.id,
            format_func=format_row,
            footer_text=f"Days: {days}",
            summary=summary,
            chart_view=MetricsChartView(
                self.bot,
                ctx.author.id,
                chart_type="bar",
                title=f"Top Guilds for {command_name}",
                labels=[str(gid or "DM") for gid, _ in rows],
                values=[int(total) for _, total in rows],
                ylabel="Calls",
                limit=15,
            ),
        )
        await ctx.send(embed=paginator.get_page_embed(), view=paginator)

    @metrics.command(name="perday", hidden=True)
    @commands.is_owner()
    async def metrics_perday(
        self,
        ctx: Context,
        metric: str = "usage",
        days: int = 7,
        guild_id: Optional[int] = None,
        *,
        command_name: Optional[str] = None,
    ) -> None:
        """Display per-day metrics for a specific metric type.

        Args:
            metric: Metric type (usage, errors, latency, exposure)
            command_name: Optional command name to filter by
            days: Number of days to look back (1-365, default 7)
            guild_id: Optional guild ID to filter by
        """
        valid_metrics = {"usage", "errors", "latency", "exposure"}
        metric = metric.lower()
        if metric not in valid_metrics:
            return await ctx.send(f"❌ Invalid metric `{metric}`. Valid options: {', '.join(sorted(valid_metrics))}")

        if days < 1 or days > 365:
            return await ctx.send("❌ Days must be between 1 and 365.")

        if command_name is not None:
            command_name = command_name.strip().lower()
            if not command_name:
                command_name = None

        cutoff = discord.utils.utcnow().date() - timedelta(days=days)

        try:
            async with self.bot.database.async_sessionmaker() as session:
                if metric == "errors":
                    stmt = (
                        select(
                            CommandErrorDaily.bucket_date,
                            func.sum(CommandErrorDaily.count).label("total"),
                        )
                        .where(CommandErrorDaily.bucket_date >= cutoff)
                        .group_by(CommandErrorDaily.bucket_date)
                        .order_by(CommandErrorDaily.bucket_date.asc())
                    )
                    if command_name:
                        stmt = stmt.where(CommandErrorDaily.command_name == command_name)
                    if guild_id:
                        stmt = stmt.where(CommandErrorDaily.guild_id == guild_id)
                    ylabel = "Errors"
                    title = "Errors per Day"
                    value_label = "errors"
                elif metric == "latency":
                    stmt = (
                        select(
                            CommandLatencyDaily.bucket_date,
                            func.sum(CommandLatencyDaily.latency_ms_sum).label("sum_ms"),
                            func.sum(CommandLatencyDaily.latency_count).label("count"),
                        )
                        .where(CommandLatencyDaily.bucket_date >= cutoff)
                        .group_by(CommandLatencyDaily.bucket_date)
                        .order_by(CommandLatencyDaily.bucket_date.asc())
                    )
                    if command_name:
                        stmt = stmt.where(CommandLatencyDaily.command_name == command_name)
                    if guild_id:
                        stmt = stmt.where(CommandLatencyDaily.guild_id == guild_id)
                    ylabel = "Avg ms"
                    title = "Latency per Day"
                    value_label = "avg ms"
                elif metric == "exposure":
                    stmt = (
                        select(
                            DailyUserExposure.bucket_date,
                            func.count(func.distinct(DailyUserExposure.user_hash)).label(
                                "unique"
                            ),
                        )
                        .where(DailyUserExposure.bucket_date >= cutoff)
                        .group_by(DailyUserExposure.bucket_date)
                        .order_by(DailyUserExposure.bucket_date.asc())
                    )
                    if guild_id:
                        stmt = stmt.where(DailyUserExposure.guild_id == guild_id)
                    ylabel = "Unique users"
                    title = "Exposure per Day"
                    value_label = "users"
                else:  # usage
                    stmt = (
                        select(
                            CommandUsageDaily.bucket_date,
                            func.sum(CommandUsageDaily.count).label("total"),
                        )
                        .where(CommandUsageDaily.bucket_date >= cutoff)
                        .group_by(CommandUsageDaily.bucket_date)
                        .order_by(CommandUsageDaily.bucket_date.asc())
                    )
                    if command_name:
                        stmt = stmt.where(CommandUsageDaily.command_name == command_name)
                    if guild_id:
                        stmt = stmt.where(CommandUsageDaily.guild_id == guild_id)
                    ylabel = "Calls"
                    title = "Usage per Day"
                    value_label = "calls"

                result = await session.execute(stmt)
                rows = result.all()
        except Exception as e:
            self.bot.logger.error(f"Database error in metrics_perday: {e}")
            return await ctx.send("❌ An error occurred while fetching per-day data. Please try again later.")

        if not rows:
            return await ctx.send(f"No per-day data found for metric `{metric}` in the last {days} day(s).")

        # Build labels and values for chart
        labels = []
        values = []
        max_value = 0
        total_value = 0
        for row in rows:
            bucket = row[0]
            labels.append(bucket.strftime("%Y-%m-%d"))
            if metric == "latency":
                value = int(row[1] / row[2]) if row[2] else 0
            else:
                value = int(row[1])
            values.append(value)
            max_value = max(max_value, value)
            total_value += value

        def format_perday(row, rank=None):
            bucket = row[0]
            date_str = bucket.strftime("%a %b %d")
            if metric == "latency":
                value = int(row[1] / row[2]) if row[2] else 0
            else:
                value = int(row[1])
            bar = _bar_visual(value, max_value)
            return f"`{date_str}` {bar} `{_human_number(value):>10}` {value_label}"

        summary = {
            "Total": _human_number(total_value),
            "Average": _human_number(total_value // days),
            "Peak": _human_number(max_value),
        }
        if command_name:
            summary["Command"] = f"`{command_name}`"

        paginator = MetricsPaginator(
            data=rows,
            title=f"📅 {title}",
            author_id=ctx.author.id,
            format_func=format_perday,
            footer_text=f"Days: {days} | Metric: {metric}",
            summary=summary,
            chart_view=MetricsChartView(
                self.bot,
                ctx.author.id,
                chart_type="line",
                title=title,
                labels=labels,
                values=values,
                ylabel=ylabel,
            ),
        )
        await ctx.send(embed=paginator.get_page_embed(), view=paginator)

    async def find_role(self, ctx: Context, role_name: str):
        """Helper method to find a role by partial name, ID, or mention."""
        if not ctx.guild:
            return None, "This command must be used in a server."

        matching_roles = [
            role
            for role in ctx.guild.roles
            if role_name.lower() in role.name.lower()
            or role_name.lower() == str(role.id).lower()
            or role_name.lower() in role.mention.lower()
        ]

        if not matching_roles:
            return None, "Role not found."

        if len(matching_roles) > 1:
            role_list = "\n".join(
                [
                    f"{index + 1}. {role.mention}"
                    for index, role in enumerate(matching_roles)
                ]
            )
            embed = discord.Embed(
                description=f"Multiple roles found matching '**{role_name}**':\n{role_list}\nPlease reply with the number of the role you want."
            )
            msg = await ctx.send(embed=embed)

            def check(m):
                return (
                    m.author == ctx.author
                    and m.channel == ctx.channel
                    and m.content.isdigit()
                )

            try:
                response = await self.bot.wait_for("message", check=check, timeout=30.0)
                selected_index = int(response.content) - 1

                if selected_index < 0 or selected_index >= len(matching_roles):
                    return None, "Invalid selection. Command cancelled."

                await msg.delete()
                return matching_roles[selected_index], None
            except (ValueError, IndexError):
                return None, "Invalid selection. Command cancelled."
            except asyncio.TimeoutError:
                return None, "You took too long to respond. Command cancelled."
        else:
            return matching_roles[0], None

    def cleanup_code(self, content: str) -> str:
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith("```") and content.endswith("```"):
            return "\n".join(content.split("\n")[1:-1])

        # remove `foo`
        return content.strip("` \n")

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

        # < 1k
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
        The returned amount is truncated (not rounded) to two decimal places.
        """

        if not isinstance(amount_input, str):
            raise ValueError("Invalid amount input type.")

        amount_input = amount_input.strip().lower()

        if amount_input == "all" or amount_input == "max":
            amount = user_balance
        elif amount_input == "half":
            amount = user_balance / Decimal("2")
        elif amount_input == "quarter":
            amount = user_balance / Decimal("4")

        elif amount_input.endswith("%"):
            percentage_match = re.match(r"^([0-9]+(\.[0-9]+)?)%$", amount_input)
            if percentage_match:
                try:
                    percentage = Decimal(percentage_match.group(1))
                    if Decimal("1") <= percentage <= Decimal("100"):
                        amount = user_balance * (percentage / Decimal("100"))
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
            }

            multiplier_match = re.match(
                r"^([0-9]+(\.[0-9]+)?)(k|m|b|t|q|qu|s|se|o)?$", amount_input
            )
            if not multiplier_match:
                raise ValueError("Invalid amount format.")

            try:
                number = Decimal(multiplier_match.group(1))
                if multiplier_match.group(3):
                    multiplier = multipliers[multiplier_match.group(3)]
                    amount = number * multiplier
                else:
                    amount = number
            except (InvalidOperation, KeyError):
                raise ValueError("Invalid amount.")

        if amount.is_nan():
            raise ValueError("Invalid amount.")

        try:
            amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise ValueError("Invalid amount.")

        if amount > user_balance:
            raise ValueError("Insufficient Funds.")
        if amount <= Decimal("0"):
            raise ValueError("Amount must be greater than 0.")

        return amount

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

    @commands.group(
        name="todo", aliases=["task"], invoke_without_command=True, hidden=True
    )
    @commands.is_owner()
    async def todolist(self, ctx: Context):
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
                lines.append(f"`{prefix}todo {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}todo {name}`{aliases}")

        description = "\n".join(lines) if lines else "No subcommands available."

        embed = discord.Embed(
            title="Todo — Available Commands",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Use {prefix}todo <subcommand> for details.")
        await ctx.reply(embed=embed, mention_author=False)

    @todolist.command(name="add", hidden=True)
    @commands.is_owner()
    async def add_task(self, ctx: Context, *, task: str):
        """Add a new task to your todo list."""
        try:
            await self.bot.database.add_task(ctx.author.id, task)
            embed = discord.Embed(
                title="Task Added",
                description=f"Task: **{task}** has been added to your todo list.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
        except Exception as err:
            logging.error(f"Error adding task: {err}")
            embed = discord.Embed(
                title="Error Adding Task",
                description="An unexpected error occurred while adding your task.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @todolist.command(name="list", hidden=True)
    @commands.is_owner()
    async def view_tasks(self, ctx: Context):
        """View all tasks in your todo list."""
        try:
            tasks = await self.bot.database.get_tasks(ctx.author.id)
        except Exception as err:
            logging.error(f"Error retrieving tasks: {err}")
            embed = discord.Embed(
                title="Todo List",
                description="Failed to retrieve tasks.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        if not tasks:
            embed = discord.Embed(
                title="Todo List",
                description="Your todo list is empty!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        # If tasks list is short, send in one embed. Else, use pagination.
        if len(tasks) <= 5:
            embed = discord.Embed(title="Todo List", color=discord.Color.blurple())
            for idx, item in enumerate(tasks, 1):
                status = (
                    "<:checkmark:1360657064019365980>"
                    if item.completed
                    else "<:crossmark:1360656870305693742>"
                )
                embed.add_field(
                    name=f"Task {idx} | Status: {status}", value=item.task, inline=False
                )
            await ctx.send(embed=embed)
        else:
            paginator = TodoPaginator(tasks, ctx.author, per_page=5)
            await ctx.send(embed=paginator.get_page_embed(), view=paginator)

    @todolist.command(name="complete", hidden=True)
    @commands.is_owner()
    async def complete_task(self, ctx: Context, task_id: int):
        """Mark a specific task as complete."""
        try:
            success = await self.bot.database.complete_task(ctx.author.id, task_id)
            if success:
                embed = discord.Embed(
                    title="Task Completed",
                    description=f"Task **{task_id}** has been marked as complete!",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Error",
                    description="Invalid task number.",
                    color=discord.Color.red(),
                )
            await ctx.send(embed=embed)
        except Exception as err:
            logging.error(f"Error completing task {task_id}: {err}")
            embed = discord.Embed(
                title="Error",
                description="An error occurred while completing the task.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @todolist.command(name="delete", hidden=True)
    @commands.is_owner()
    async def delete_task(self, ctx: Context, task_id: int):
        """Delete a specific task from your todo list."""
        try:
            success = await self.bot.database.delete_task(ctx.author.id, task_id)
            if success:
                embed = discord.Embed(
                    title="Task Deleted",
                    description=f"Task **{task_id}** has been deleted.",
                    color=discord.Color.orange(),
                )
            else:
                embed = discord.Embed(
                    title="Error",
                    description="Invalid task number.",
                    color=discord.Color.red(),
                )
            await ctx.send(embed=embed)
        except Exception as err:
            logging.error(f"Error deleting task {task_id}: {err}")
            embed = discord.Embed(
                title="Error",
                description="An error occurred while deleting the task.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @todolist.command(name="edit", hidden=True)
    @commands.is_owner()
    async def edit_task(self, ctx: Context, task_id: int, *, new_task: str):
        """Edit an existing task in your todo list."""
        try:
            success = await self.bot.database.edit_task(
                ctx.author.id, task_id, new_task
            )
            if success:
                embed = discord.Embed(
                    title="Task Edited",
                    description=f"Task **{task_id}** has been updated.",
                    color=discord.Color.blurple(),
                )
            else:
                embed = discord.Embed(
                    title="Error",
                    description="Invalid task number.",
                    color=discord.Color.red(),
                )
            await ctx.send(embed=embed)
        except Exception as err:
            logging.error(f"Error editing task {task_id}: {err}")
            embed = discord.Embed(
                title="Error",
                description="An error occurred while editing the task.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @todolist.command(name="clear", hidden=True)
    @commands.is_owner()
    async def clear_tasks(self, ctx: Context):
        """Clear all tasks from your todo list."""
        try:
            count = await self.bot.database.clear_tasks(ctx.author.id)
            embed = discord.Embed(
                title="Todo List Cleared",
                description=f"All tasks ({count}) have been removed from your todo list.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
        except Exception as err:
            logging.error(f"Error clearing tasks: {err}")
            embed = discord.Embed(
                title="Error",
                description="An error occurred while clearing tasks.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @commands.command(name="setstatus", hidden=True)
    @commands.is_owner()
    async def set_status(self, ctx: Context, *, status: str):
        """Change the bot's status."""
        await self.bot.change_presence(activity=discord.Game(name=status))
        await ctx.send(f"Status changed to: {status}")

    @commands.command(name="setactivity", hidden=True)
    @commands.is_owner()
    async def set_activity(self, ctx: Context, *, activity: str):
        """Change the bot's activity."""
        await self.bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name=activity)
        )
        await ctx.send(f"Activity changed to: {activity}")

    @commands.command(name="setinviteurl", aliases=["setinvite"], hidden=True)
    @commands.is_owner()
    async def set_invite_url(self, ctx: Context):
        """Dynamically generates and sets the bot's invite URL."""
        try:
            # Generate the invite URL with required permissions
            invite_url = discord.utils.oauth_url(
                self.bot.user.id,
                permissions=discord.Permissions(8),  # Adjust permissions as needed
                scopes=["bot", "applications.commands"],
            )

            await self.bot.database.set_invite_url(invite_url)
            self.bot.invite_url = invite_url
            await ctx.send(f"Invite URL set to: {invite_url}")
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")
            return

    @commands.command(name="rtt", aliases=["latency"], hidden=True)
    @commands.is_owner()
    async def rtt(self, ctx: Context, readings: int = 5):
        """Measure Discord HTTP round-trip latency over several readings.

        Args:
            readings: Number of readings to take (2-10, default 5, edits sent in pairs)
        """
        if readings < 2 or readings > 10 or readings % 2 != 0:
            return await ctx.send("❌ Readings must be an even number between 2 and 10.")

        msg = await ctx.send("Calculating round-trip time...")
        times = []

        # Discord rate-limits edits to the same message; pair readings per edit
        # to reduce the number of edits and stay within the rate-limit bucket.
        for chunk_idx in range(0, readings, 2):
            start = time.perf_counter()
            line_1 = chunk_idx + 1
            line_2 = chunk_idx + 2
            content = f"Reading {line_1}: measuring...\nReading {line_2}: measuring..."
            await msg.edit(content=content)
            elapsed_total = (time.perf_counter() - start) * 1000
            # Split the elapsed time evenly across the two readings in this chunk.
            times.append(elapsed_total / 2)
            times.append(elapsed_total / 2)

        avg = statistics.mean(times)
        stdev = statistics.stdev(times) if len(times) > 1 else 0.0
        ws_latency = self.bot.latency * 1000

        lines = ["Calculating round-trip time...\n"]
        for i, value in enumerate(times, start=1):
            lines.append(f"Reading {i}: {value:.2f}ms")
        lines.append(f"\nAverage: {avg:.2f} ± {stdev:.2f}ms")
        lines.append(f"Websocket latency: {ws_latency:.2f}ms")

        await msg.edit(content="\n".join(lines))

    # ---------- LOAD ----------
    @commands.command(name="load", hidden=True)
    @commands.is_owner()
    async def load(self, ctx: commands.Context, *, cogs: str):
        """Load one or more cogs (comma or space separated) and update the database."""
        names = _parse_cog_list(cogs)
        if not names:
            return await ctx.send("⚠️ Provide at least one cog name.")

        succeeded, failed = [], []

        for name in names:
            cog_path = f"cogs.{name}"
            if not _exists_cog_file(name):
                failed.append(f"`{name}` (file not found)")
                continue
            if not _importable_cog(cog_path):
                failed.append(f"`{name}` (not importable)")
                continue
            if cog_path in self.bot.extensions:
                failed.append(f"`{name}` (already loaded)")
                continue

            await self.bot.load_extension(cog_path)
            db = getattr(self.bot, "database", None)
            if db:
                try:
                    await db.load_cog(name)
                except Exception as exc:
                    logger.error(
                        "Failed to mark cog %s as loaded in DB", name, exc_info=exc
                    )
                    failed.append(
                        f"`{name}` (loaded; DB error: {type(exc).__name__}: {exc})"
                    )
                    continue
            succeeded.append(name)

        embed = discord.Embed(color=discord.Color.blurple(), title="Cog Load Results")
        if succeeded:
            embed.add_field(name="✅ Loaded", value=_fmt_list(succeeded), inline=False)
        if failed:
            embed.add_field(
                name="🚫 Failed to load", value="\n".join(failed), inline=False
            )
        await ctx.send(embed=embed)

    @commands.group(
        name="gamesession",
        aliases=["gs"],
        help="Supervise active game sessions",
        invoke_without_command=True,
        hidden=True,
    )
    @commands.is_owner()
    async def gamesession(self, ctx: Context):
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
                lines.append(f"`{prefix}gamesession {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}gamesession {name}`{aliases}")

        description = "\n".join(lines) if lines else "No subcommands available."

        embed = discord.Embed(
            title="Game Sessions — Available Commands",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Use {prefix}gamesession <subcommand> for details.")
        await ctx.reply(embed=embed, mention_author=False)

    @gamesession.command(name="list", hidden=True)
    @commands.is_owner()
    async def gamesession_list(self, ctx: Context, limit: int = 10):
        limit = max(1, min(limit, 25))
        sessions = await self.bot.database.list_game_sessions(limit=limit)
        if not sessions:
            return await ctx.send("No active game sessions.")

        session_owner_ids = [getattr(s, "owner_id", None) for s in sessions]
        resolved_owners = await self._resolve_ids(session_owner_ids)

        lines = []
        for s in sessions:
            created = s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "?"
            raw_owner = resolved_owners.get(getattr(s, "owner_id", None))
            owner_str = f"<@{raw_owner}>" if raw_owner else "Unknown"
            lines.append(
                f"{s.id} • {s.game_name} • owner {owner_str} • {created}"
            )
        embed = discord.Embed(
            title="Active Game Sessions",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

    @gamesession.command(name="show", hidden=True)
    @commands.is_owner()
    async def gamesession_show(self, ctx: Context, session_id: str):
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return await ctx.send("Invalid session ID.")

        gs = await self.bot.database.get_game_session(sid)
        if not gs:
            return await ctx.send("Session not found.")

        participant_ids = list(gs.participants or [])
        resolved_participants = await self._resolve_ids(participant_ids)
        raw_owner = await self._resolve_id(getattr(gs, "owner_id", None))

        embed = discord.Embed(
            title=f"Session {gs.id}", color=discord.Color.blurple()
        )
        embed.add_field(name="Game", value=gs.game_name, inline=True)
        embed.add_field(name="Owner", value=f"<@{raw_owner}>" if raw_owner else "Unknown", inline=True)
        embed.add_field(name="Channel", value=str(gs.channel_id), inline=True)
        embed.add_field(name="Wager", value=str(gs.wager_total), inline=True)
        embed.add_field(
            name="Participants",
            value=", ".join(
                f"<@{resolved_participants.get(p, p)}>" if resolved_participants.get(p) else f"Unknown ({p})"
                for p in participant_ids
            ) or "—",
            inline=False,
        )
        embed.add_field(
            name="State",
            value=textwrap.shorten(str(gs.state or {}), width=900, placeholder="..."),
            inline=False,
        )
        embed.add_field(
            name="RNG",
            value=textwrap.shorten(str(gs.rng or {}), width=900, placeholder="..."),
            inline=False,
        )
        await ctx.send(embed=embed)

    @gamesession.command(name="events", hidden=True)
    @commands.is_owner()
    async def gamesession_events(
        self, ctx: Context, session_id: str, limit: int = 10
    ):
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return await ctx.send("Invalid session ID.")

        events = await self.bot.database.get_game_session_events(sid, limit=limit)
        if not events:
            return await ctx.send("No events found for this session.")

        lines = []
        for e in events:
            created = e.created_at.strftime("%Y-%m-%d %H:%M") if e.created_at else "?"
            lines.append(
                f"{created} • {e.event_type} • "
                f"{textwrap.shorten(str(e.payload), width=700, placeholder='...')}"
            )
        embed = discord.Embed(
            title=f"Session Events — {sid}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

    @gamesession.command(name="end", hidden=True)
    @commands.is_owner()
    async def gamesession_end(
        self, ctx: Context, session_id: str, refund: bool = False
    ):
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return await ctx.send("Invalid session ID.")

        casino = self.bot.get_cog("Casino")
        handled = False
        if casino:
            handled = await casino.force_end_session(sid, refund=refund)

        if handled:
            return await ctx.send("Session force-ended via live handler.")

        gs = await self.bot.database.get_game_session(sid)
        if not gs:
            return await ctx.send("Session not found.")

        if refund:
            refunds = (gs.state or {}).get("refunds", [])
            for r in refunds:
                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=r.get("wallet_id"),
                        amount=Decimal(r.get("amount")),
                        description="Game Session Refund",
                    )
                except Exception as exc:
                    logger.error("Refund failed for %s: %s", r, exc)

        await self.bot.database.end_game_session(
            sid,
            outcome="forced_end",
            reason="admin_end",
            final_state=gs.state or {},
        )
        await ctx.send("Session ended.")

    # ---------- UNLOAD ----------
    @commands.command(name="unload", hidden=True)
    @commands.is_owner()
    async def unload(self, ctx: commands.Context, *, cogs: str):
        """Unload one or more cogs (comma or space separated) and update the database."""
        names = _parse_cog_list(cogs)
        if not names:
            return await ctx.send("⚠️ Provide at least one cog name.")

        succeeded, failed = [], []

        for name in names:
            cog_path = f"cogs.{name}"
            if cog_path not in self.bot.extensions:
                failed.append(f"`{name}` (not loaded)")
                continue

            await self.bot.unload_extension(cog_path)
            db = getattr(self.bot, "database", None)
            if db:
                try:
                    await db.unload_cog(name)
                except Exception as exc:
                    logger.error(
                        "Failed to mark cog %s as unloaded in DB", name, exc_info=exc
                    )
                    failed.append(
                        f"`{name}` (unloaded; DB error: {type(exc).__name__}: {exc})"
                    )
                    continue
            succeeded.append(name)

        embed = discord.Embed(color=discord.Color.blurple(), title="Cog Unload Results")
        if succeeded:
            embed.add_field(
                name="✅ Unloaded", value=_fmt_list(succeeded), inline=False
            )
        if failed:
            embed.add_field(
                name="🚫 Failed to unload", value="\n".join(failed), inline=False
            )
        await ctx.send(embed=embed)

    # ---------- RELOAD ----------
    @commands.command(name="reload", hidden=True)
    @commands.is_owner()
    async def reload(self, ctx: commands.Context, *, cogs: str):
        """Reload one or more cogs (comma or space separated)."""
        names = _parse_cog_list(cogs)
        if not names:
            return await ctx.send("⚠️ Provide at least one cog name.")

        succeeded, failed = [], []

        for name in names:
            cog_path = f"cogs.{name}"
            if cog_path not in self.bot.extensions:
                failed.append(f"`{name}` (not loaded)")
                continue
            try:
                await self.bot.reload_extension(cog_path)
                succeeded.append(name)
            except Exception as e:
                failed.append(f"`{name}` ({type(e).__name__}: {e})")

        embed = discord.Embed(color=discord.Color.blurple(), title="Cog Reload Results")
        if succeeded:
            embed.add_field(
                name="🔁 Reloaded", value=_fmt_list(succeeded), inline=False
            )
        if failed:
            embed.add_field(
                name="🚫 Failed to reload", value="\n".join(failed), inline=False
            )
        await ctx.send(embed=embed)

    @commands.command(
        name="cogs",
        description="List all available cogs and their load status with command counts.",
        hidden=True,
    )
    @commands.is_owner()
    async def cogs(self, ctx: Context) -> None:
        """
        Lists all available cogs, their load status, and the number of commands they contain.
        """
        cog_dir = "cogs"
        try:
            cog_files = {
                f[:-3].lower() for f in os.listdir(cog_dir) if f.endswith(".py")
            }
        except FileNotFoundError:
            await ctx.send(f"Directory `{cog_dir}` does not exist.")
            return

        loaded_cogs = {}
        unloaded_cogs = set(cog_files)

        for cog_name, cog in self.bot.cogs.items():
            command_count = len(cog.get_commands())
            loaded_cogs[cog_name] = command_count
            unloaded_cogs.discard(cog_name.lower())

        embed = discord.Embed(
            title="Cogs Status",
            color=discord.Color.blurple(),
            description="Shows the status of all cogs along with their command counts.",
        )

        if loaded_cogs:
            loaded_cogs_info = "\n".join(
                f":white_check_mark: **{cog}** - {count} command(s)"
                for cog, count in loaded_cogs.items()
            )
            embed.add_field(name="Loaded Cogs", value=loaded_cogs_info, inline=False)
        else:
            embed.add_field(name="Loaded Cogs", value="None", inline=False)

        if unloaded_cogs:
            unloaded_cogs_info = "\n".join(
                f":x: **{cog.capitalize()}** - 0 command(s)" for cog in unloaded_cogs
            )
            embed.add_field(
                name="Unloaded Cogs", value=unloaded_cogs_info, inline=False
            )
        else:
            embed.add_field(name="Unloaded Cogs", value="None", inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="debug", hidden=True)
    @commands.is_owner()
    async def debug_mode(self, ctx):
        self.bot.debug_mode_active = not self.bot.debug_mode_active
        if self.bot.debug_mode_active:
            if hasattr(self.bot, "status_task") and self.bot.status_task:
                self.bot.status_task.cancel()
            await self.bot.change_presence(
                status=discord.Status.dnd, activity=discord.Game(name="in debug mode")
            )
        else:
            if hasattr(self.bot, "status_task") and self.bot.status_task:
                self.bot.status_task.start()
            await self.bot.change_presence(status=discord.Status.online)
        await ctx.reply(f"Debug Mode: {str(self.bot.debug_mode_active)}")

    @commands.command(
        name="sync", help="Sync hybrid/slash commands globally.", hidden=True
    )
    @commands.is_owner()
    async def sync(self, ctx: Context):
        await ctx.bot.tree.sync()
        await ctx.send(
            "Hybrid/Slash commands have been synced globally. Allow up to 24 hours for propagation"
        )

    @commands.command(
        name="syncguild",
        help="Sync hybrid/slash commands to a specific guild by ID.",
        hidden=True,
    )
    @commands.is_owner()
    async def sync_guild(self, ctx: Context, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        await ctx.bot.tree.sync(guild=guild)
        await ctx.send(
            f"Hybrid/Slash commands have been synced to {guild.name} {guild_id}."
        )

    @commands.group(
        name="blacklist",
        aliases=["bl"],
        help="View the blacklist",
        invoke_without_command=True,
        hidden=True,
    )
    @commands.is_owner()
    async def blacklist(self, ctx: Context):
        rows = await self.bot.database.get_blacklisted_users()
        if not rows:
            embed = discord.Embed(
                title="🚫 Blacklisted Users",
                description="No users are currently blacklisted.",
                color=discord.Color.dark_red(),
            )
            embed.set_author(
                name=str(ctx.author),
                icon_url=getattr(ctx.author, "avatar", None) and ctx.author.avatar.url,
            )
            return await ctx.send(embed=embed)

        # Prepare entries
        entries = []
        user_ids = [getattr(r, "user_id", None) for r in rows]
        admin_ids = [getattr(r, "admin_id", None) for r in rows if getattr(r, "admin_id", None)]
        resolved_ids = await self._resolve_ids(user_ids + admin_ids)

        for r in rows:
            uid = resolved_ids.get(getattr(r, "user_id", None))
            user = None
            if uid:
                try:
                    user = await self.bot.fetch_user(uid)
                except Exception:
                    pass

            display_name = str(user) if user else "Unknown User"
            reason = getattr(r, "reason", None) or "No reason provided"
            admin_id = resolved_ids.get(getattr(r, "admin_id", None))
            if admin_id:
                admin_user = None
                try:
                    admin_user = await self.bot.fetch_user(admin_id)
                except Exception:
                    pass
                if admin_user:
                    reason += f" (added by {admin_user})"
            added_at = getattr(r, "added_at", None)
            added_unix = None
            if added_at:
                try:
                    added_unix = int(added_at.timestamp())
                except Exception:
                    pass

            if len(reason) > 1024:
                reason = reason[:1021] + "…"

            entries.append(
                {
                    "user_id": uid,
                    "display_name": display_name,
                    "admin_id": admin_id,
                    "reason": reason,
                    "added_unix": added_unix,
                }
            )

        def make_embed(idx: int) -> discord.Embed:
            item = entries[idx]
            total = len(entries)
            embed = discord.Embed(
                title=f"🚫 Blacklisted User — {idx+1}/{total}",
                color=discord.Color.dark_red(),
            )
            embed.set_author(
                name=str(ctx.author),
                icon_url=getattr(ctx.author, "avatar", None) and ctx.author.avatar.url,
            )
            embed.add_field(name="User", value=item["display_name"], inline=False)
            embed.add_field(name="ID", value=f"`{item['user_id']}`", inline=True)
            if item["added_unix"]:
                embed.add_field(
                    name="Added",
                    value=f"<t:{item['added_unix']}:F> • <t:{item['added_unix']}:R>",
                    inline=True,
                )
            if item["admin_id"]:
                embed.add_field(name="Added by", value=f"<@{item['admin_id']}>", inline=True)
            embed.add_field(name="Reason", value=item["reason"], inline=False)
            embed.set_footer(
                text="Use ◀ / ▶ to navigate, ❌ to remove • Controls expire in 3 minutes"
            )
            return embed

        if not entries:
            return await ctx.send("No blacklisted users found.")

        class BlacklistPaginator(discord.ui.View):
            def __init__(self, bot, author_id: int):
                super().__init__(timeout=180.0)
                self.bot = bot
                self.author_id = author_id
                self.idx = 0
                self.msg = None

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != self.author_id:
                    await interaction.response.send_message(
                        "You can’t control this paginator.", ephemeral=True
                    )
                    return False
                return True

            async def _render(self, interaction: discord.Interaction):
                if entries:
                    await interaction.response.edit_message(
                        embed=make_embed(self.idx), view=self
                    )
                else:
                    embed = discord.Embed(
                        title="🚫 Blacklisted Users",
                        description="No users remain in the blacklist.",
                        color=discord.Color.dark_green(),
                    )
                    await interaction.response.edit_message(embed=embed, view=None)

            @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev_button(
                self, interaction: discord.Interaction, _: discord.ui.Button
            ):
                if entries:
                    self.idx = (self.idx - 1) % len(entries)
                    await self._render(interaction)

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next_button(
                self, interaction: discord.Interaction, _: discord.ui.Button
            ):
                if entries:
                    self.idx = (self.idx + 1) % len(entries)
                    await self._render(interaction)

            @discord.ui.button(label="❌ Remove", style=discord.ButtonStyle.danger)
            async def remove_button(
                self, interaction: discord.Interaction, _: discord.ui.Button
            ):
                if not entries:
                    return await interaction.response.defer()

                # Get the user being displayed
                item = entries[self.idx]
                user_id = item["user_id"]

                # Remove from DB
                await self.bot.database.remove_from_blacklist(user_id)

                # Also remove from local entries
                entries.pop(self.idx)

                # Adjust index safely
                if entries:
                    self.idx %= len(entries)
                else:
                    self.idx = 0

                await self._render(interaction)

            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                try:
                    if self.msg:
                        await self.msg.edit(view=self)
                except Exception:
                    pass

        view = BlacklistPaginator(self.bot, ctx.author.id)
        sent = await ctx.send(embed=make_embed(0), view=view)
        view.msg = sent

    @blacklist.command(
        name="add", aliases=["a"], help="Add a user to the blacklist.", hidden=True
    )
    @commands.is_owner()
    async def blacklist_add(self, ctx: Context, *, args: str):
        """
        Add a user to the blacklist. Usage: blacklist add <identifier> [reason...]
        """
        if not ctx.guild:
            return await ctx.send("⚠️ Blacklist commands must be used in a server.")

        # Split identifier and reason
        parts = args.strip().split(maxsplit=1)
        if not parts:
            await ctx.send("Please provide a user identifier.")
            return
        identifier = parts[0]
        reason = parts[1] if len(parts) > 1 else "No reason provided"

        member = None
        try:
            if re.match(r"^\d+$", identifier):
                try:
                    member = ctx.guild.get_member(int(identifier))
                    if not member:
                        member = await self.bot.fetch_user(int(identifier))
                except discord.NotFound:
                    pass

            elif re.match(r"^<@!?(\d+)>$", identifier):
                mention_match = re.match(r"^<@!?(\d+)>$", identifier)
                mention_id = mention_match.group(1)
                member = ctx.guild.get_member(int(mention_id))
                if not member:
                    member = await self.bot.fetch_user(int(mention_id))

            else:
                identifier_lower = identifier.lower()
                member = discord.utils.find(
                    lambda m: identifier_lower in m.name.lower(), ctx.guild.members
                )

            if not member:
                embed = discord.Embed(
                    description=f"No user found with the identifier: {identifier}. Please try again.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return

            is_blacklisted = await self.bot.database.is_user_blacklisted(member.id)
            if is_blacklisted:
                embed = discord.Embed(
                    description=f"User {getattr(member, 'name', str(member.id))} is already blacklisted.",
                    color=0x000000,
                )
                await ctx.send(embed=embed)
                return

            if member.id in self.bot.owner_ids:
                embed = discord.Embed(
                    description=f"You can't blacklist a bot admin.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return

            await self.bot.database.add_to_blacklist(member.id, ctx.author.id, reason)
            embed = discord.Embed(
                description=f"User {getattr(member, 'name', str(member.id))} has been blacklisted.\nReason: {reason}",
                color=0x000000,
            )
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                description=f"**Database Error**: ```{e}```", color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

    @blacklist.command(
        name="remove",
        aliases=["r"],
        help="Remove a user from the blacklist.",
        hidden=True,
    )
    @commands.is_owner()
    async def blacklist_remove(self, ctx: Context, *, identifier: str):
        """Removes a user from the blacklist."""
        if not ctx.guild:
            return await ctx.send("⚠️ Blacklist commands must be used in a server.")

        try:
            if re.match(r"^\d+$", identifier):
                try:
                    member = ctx.guild.get_member(int(identifier))
                    if not member:
                        member = await self.bot.fetch_user(int(identifier))
                except discord.NotFound:
                    pass

            elif re.match(r"^<@!?(\d+)>$", identifier):
                mention_match = re.match(r"^<@!?(\d+)>$", identifier)
                mention_id = mention_match.group(1)
                member = ctx.guild.get_member(int(mention_id))
                if not member:
                    member = await self.bot.fetch_user(int(mention_id))
            else:
                identifier = identifier.lower()
                member = discord.utils.find(
                    lambda m: identifier in m.name.lower(), ctx.guild.members
                )

            if not member:
                embed = discord.Embed(
                    description=f"No user found with the identifier: {identifier}. Please try again.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return

            is_blacklisted = await self.bot.database.is_user_blacklisted(member.id)
            if not is_blacklisted:
                embed = discord.Embed(
                    description=f"User {member.name} is not blacklisted.",
                    color=0x36393E,
                )
                await ctx.send(embed=embed)
                return

            await self.bot.database.remove_from_blacklist(member.id)
            embed = discord.Embed(
                description=f"User {member.name} has been removed from the blacklist.",
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                description=f"**Database Error**: ```{e}```", color=0x36393E
            )
            await ctx.send(embed=embed)
            return

    @blacklist.command(
        name="clear", aliases=["c"], help="Clear the entire blacklist.", hidden=True
    )
    @commands.is_owner()
    async def blacklist_clear(self, ctx: Context):
        """Clears the entire blacklist."""
        try:
            await self.bot.database.clear_blacklist()
            embed = discord.Embed(
                description="The blacklist has been cleared.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                description=f"**Database Error**: ```{e}```", color=0x36393E
            )
            await ctx.send(embed=embed)
            return

    @commands.command(
        name="dm", help="Send a direct message to a user by their ID.", hidden=True
    )
    @commands.is_owner()
    async def dm_user(self, ctx: Context, user_id: int, *, message: str):
        """Send a direct message to a user by their ID."""
        try:
            user = await self.bot.fetch_user(user_id)
            if not user:
                return await ctx.send(f"🚫 Could not find a user with ID `{user_id}`.")

            await user.send(message)
            await ctx.send(f"✅ Message sent to {user.name} (`{user_id}`).")
        except discord.Forbidden:
            await ctx.send(f"🚫 Cannot send a DM to user ID `{user_id}` (forbidden).")
        except Exception as e:
            await ctx.send(f"🚫 Failed to send DM: {e}")

    @commands.command(
        name="servers", help="List all servers the bot is currently in.", hidden=True
    )
    @commands.is_owner()
    async def list_servers(self, ctx: Context):
        """List all servers the bot is currently in with detailed information."""
        servers = self.bot.guilds
        if not servers:
            return await ctx.send("The bot is not in any servers.")

        # Prepare detailed info for each server
        server_details = []
        for guild in servers:
            owner = guild.owner if guild.owner else "Unknown"
            created = guild.created_at.strftime("%b %d, %Y")
            details = (
                f"**ID:** {guild.id}\n"
                f"**Owner:** {owner}\n"
                f"**Members:** {guild.member_count}\n"
                f"**Channels:** {len(guild.channels)}\n"
                f"**Created:** {created}"
            )
            server_details.append((guild.name, details, guild))

        # Pagination: set number of servers per embed
        servers_per_page = 1
        
        def rebuild_pages():
            """Helper to rebuild pages from current server_details."""
            pages = []
            for i in range(0, len(server_details), servers_per_page):
                embed = discord.Embed(
                    title="Servers Overview",
                    description="Detailed list of servers the bot is in.",
                    color=discord.Color.blurple(),
                )
                current_batch = server_details[i : i + servers_per_page]
                for name, details, _ in current_batch:
                    embed.add_field(name=name, value=details, inline=False)
                total_pages = ((len(server_details)-1)//servers_per_page)+1 if server_details else 0
                embed.set_footer(text=f"Page {len(pages)+1} of {total_pages}")
                pages.append(embed)
            return pages

        pages = rebuild_pages()

        # Pagination view
        class PaginationView(discord.ui.View):
            def __init__(self, embeds, server_details, bot):
                super().__init__(timeout=60)
                self.embeds = embeds
                self.server_details = server_details
                self.bot = bot
                self.current = 0

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if not await self.bot.is_owner(interaction.user):
                    await interaction.response.send_message(
                        "🚫 Only bot admins can interact with these buttons.", ephemeral=True
                    )
                    return False
                return True

            @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
            async def previous(
                self, interaction: discord.Interaction, _: discord.ui.Button
            ):
                if self.current > 0:
                    self.current -= 1
                else:
                    self.current = len(self.embeds) - 1
                await interaction.response.edit_message(
                    embed=self.embeds[self.current], view=self
                )

            @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
            async def next(
                self, interaction: discord.Interaction, _: discord.ui.Button
            ):
                if self.current < len(self.embeds) - 1:
                    self.current += 1
                    await interaction.response.edit_message(
                        embed=self.embeds[self.current], view=self
                    )
                else:
                    await interaction.response.defer()

            @discord.ui.button(label="Create Invite", style=discord.ButtonStyle.green)
            async def create_invite(
                self, interaction: discord.Interaction, _: discord.ui.Button
            ):
                guild = self.server_details[self.current][2]
                invite_channel = None
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).create_instant_invite:
                        invite_channel = channel
                        break

                if not invite_channel:
                    return await interaction.response.send_message(
                        f"❌ No suitable channel found in `{guild.name}` to create an invite.",
                        ephemeral=True
                    )

                try:
                    invite = await invite_channel.create_invite(
                        max_age=86400, max_uses=1, unique=True, reason="Created by owner"
                    )
                    await interaction.response.send_message(
                        f"✅ Invite created for **{guild.name}**: {invite.url}\n*Expires in 24 hours, 1 use.*",
                        ephemeral=True
                    )
                except Exception as e:
                    await interaction.response.send_message(
                        f"❌ Failed to create invite: {e}",
                        ephemeral=True
                    )

            @discord.ui.button(label="Leave Server", style=discord.ButtonStyle.red)
            async def leave_server(
                self, interaction: discord.Interaction, _: discord.ui.Button
            ):
                guild = self.server_details[self.current][2]
                try:
                    await guild.leave()
                    await interaction.response.send_message(
                        f"✅ Successfully left **{guild.name}**.",
                        ephemeral=True
                    )
                    # Remove from server_details and rebuild pages
                    self.server_details.pop(self.current)
                    self.embeds = rebuild_pages()
                    
                    if not self.embeds:
                        await interaction.message.edit(
                            content="The bot is not in any servers anymore.",
                            embed=None,
                            view=None
                        )
                    else:
                        if self.current >= len(self.embeds):
                            self.current = len(self.embeds) - 1
                        await interaction.message.edit(
                            embed=self.embeds[self.current], view=self
                        )
                except Exception as e:
                    await interaction.response.send_message(
                        f"❌ Failed to leave server: {e}",
                        ephemeral=True
                    )

        view = PaginationView(pages, server_details, self.bot)
        await ctx.send(embed=pages[0], view=view)

    @commands.command(name="sudo", help="Run a command as another user", hidden=True)
    @commands.is_owner()
    async def sudo(
        self,
        ctx: Context,
        who: Union[discord.Member, discord.User],
        channel: Optional[discord.TextChannel] = None,
        *,
        command: str,
    ):
        """Run a command as another user optionally in another channel.

        Use this command with caution. It allows the bot owner to impersonate another user
        and execute any command. A channel can be specified; otherwise, the current channel is used.
        """

        # Validate that a command was provided
        if not command.strip():
            return await ctx.send("Please provide a command to execute.")

        new_channel = channel or ctx.channel

        # Create a copy of the original message and modify its content, author, and channel.
        msg = copy.copy(ctx.message)
        msg.channel = new_channel
        msg.author = who
        msg.content = ctx.prefix + command

        # Retrieve a new context for the impersonated command.
        new_ctx = await self.bot.get_context(msg, cls=type(ctx))

        # Log the sudo usage for tracking
        logger.info(
            f"Sudo command invoked by {ctx.author} impersonating {who} in {new_channel}. Command: {command}"
        )

        # Check if the command exists in the new context.
        if new_ctx.command is None:
            return await ctx.send(f"The command `{command.split()[0]}` was not found.")

        try:
            # Invoke the command as the impersonated user.
            await self.bot.invoke(new_ctx)
            embed = discord.Embed(
                title="Command Executed",
                description=f"Command executed as {who.mention} in {new_channel.mention}:\n```{command}```",
                color=discord.Color.green(),
            )
            await ctx.reply(embed=embed)
        except Exception as e:
            logger.exception("Error during sudo invocation:", exc_info=e)
            await ctx.send(
                f"An error occurred while executing the command as {who.mention}: {e}"
            )

    @commands.command(
        name="do", help="Repeats a command a specified number of times.", hidden=True
    )
    @commands.is_owner()
    async def do(self, ctx: Context, times: int, *, command: str):
        """Repeats a command a specified number of times.

        This command is owner-only. It validates the repetition count,
        prevents recursive invocation of the 'do' command, and provides
        progress feedback.
        """
        # Validate the number of repetitions
        if times < 1:
            return await ctx.send(
                "Please provide a positive integer for the number of times."
            )
        MAX_REPETITIONS = 5  # Set a safe upper limit to prevent abuse
        if times > MAX_REPETITIONS:
            return await ctx.send(
                f"Too many repetitions requested (max allowed is {MAX_REPETITIONS})."
            )

        # Prevent recursive invocation of this command
        if command.strip().lower().startswith(ctx.prefix + "do"):
            return await ctx.send("Recursive command invocation is not allowed.")

        # Create a copy of the original message and update its content with the desired command
        msg = copy.copy(ctx.message)
        msg.content = ctx.prefix + command

        # Retrieve a new context for the command to be reinvoked
        new_ctx = await self.bot.get_context(msg, cls=type(ctx))

        # Provide initial feedback to the user
        feedback_message = await ctx.send(f"Executing `{command}` **{times}** times...")

        # Loop through and reinvoke the command, handling any errors per iteration
        for i in range(times):
            try:
                await new_ctx.reinvoke()
            except Exception as e:
                logger.exception(f"Error on iteration {i+1} of do command: {e}")
                await ctx.send(f"An error occurred on iteration {i+1}: {e}")
            else:
                # Optionally update feedback after each iteration (if desired)
                await feedback_message.edit(
                    content=f"Executed iteration **{i+1}/{times}**"
                )

        await ctx.send("Finished executing the command.")

    @commands.command(
        name="rscd",
        help="Resets all cooldowns for a specified user or all users if 'all' is specified",
        hidden=True,
    )
    @commands.is_owner()
    async def reset_cooldowns(self, ctx: Context, user: Union[discord.User, str] = None):
        """Resets all cooldowns for a specified user or all users if 'all' is specified."""
        user = user or ctx.author

        # Check if 'all' was passed
        if isinstance(user, str) and user.lower() == "all":
            all_commands = [cmd.name for cmd in self.bot.commands]
            
            # Clear all cooldowns for all users
            for command_name in all_commands:
                await self.bot.database.clear_all_cooldowns()
            
            embed = discord.Embed(
                description="All cooldowns for all users have been reset.",
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed, delete_after=5)
            return

        # If user is a string but not 'all', try to convert to User
        if isinstance(user, str):
            try:
                user = await commands.UserConverter().convert(ctx, user)
            except commands.BadArgument:
                return await ctx.send("Invalid user specified.")

        if user.bot:
            return await ctx.send("You cannot reset cooldowns for a bot.")

        all_commands = [cmd.name for cmd in self.bot.commands]

        for command_name in all_commands:
            current_cooldown = await self.bot.database.get_cooldown(
                user.id, command_name
            )
            if current_cooldown > 0:
                await self.bot.database.set_cooldown(user.id, command_name, 0)

        embed = discord.Embed(
            description=f"All cooldowns for {user.display_name} have been reset.",
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, delete_after=5)

    @commands.command(
        name="error", help="Raises an error for testing purposes.", hidden=True
    )
    @commands.is_owner()
    async def error(self, ctx: Context, type: str = "test"):
        """Raises an error for testing purposes."""
        if type == "test":
            raise Exception("This is a test exception.")
        elif type == "value":
            raise ValueError("This is a test value error.")
        elif type == "type":
            raise TypeError("This is a test type error.")
        elif type == "command":
            raise commands.CommandError("This is a test command error.")
        elif type == "database":
            raise SQLAlchemyError("This is a test database error.")
        elif type == "permission":
            raise commands.MissingPermissions("This is a test permission error.")
        elif type == "notfound":
            raise commands.CommandNotFound("This is a test command not found error.")
        elif type == "runtime":
            raise RuntimeError("This is a test runtime error.")
        elif type == "custom":

            class CustomError(Exception):
                pass

            raise CustomError("This is a custom test error.")
        elif type == "unknown":
            raise Exception("This is an unknown error type for testing purposes.")
        elif type == "syntax":
            raise SyntaxError("This is a test syntax error.")
        else:
            await ctx.send("Unknown error type.")

    async def _check_command_exists(self, command_name: str):
        """
        Check if a command or group exists in either normal or slash commands.
        This will return True if either exists (even for subcommands within groups).
        """
        command = self.bot.get_command(command_name)
        if command is not None:
            return True

        slash_command = self.bot.tree.get_command(command_name)
        if slash_command is not None:
            return True

        for cmd in self.bot.tree.walk_commands():
            if isinstance(cmd, app_commands.Group) and cmd.name == command_name:
                return True
            if isinstance(cmd, app_commands.Group):
                for subcommand in cmd.walk_commands():
                    if subcommand.name == command_name:
                        return True

        return False

    @commands.group(name="command", aliases=["cmd"], invoke_without_command=True, hidden=True)
    @commands.is_owner()
    async def command_cog(self, ctx: Context):
        """Manage bot commands."""
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
                lines.append(f"`{prefix}command {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}command {name}`{aliases}")

        description = "\n".join(lines) if lines else "No subcommands available."

        embed = discord.Embed(
            title="Command Management",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Use {prefix}command <subcommand> for details.")
        await ctx.reply(embed=embed, mention_author=False)

    @command_cog.command(
        name="enable", help="Enable a command bot-wide.", hidden=True
    )
    @commands.is_owner()
    async def enable_bot_command(self, ctx: Context, *, command_name: str):
        """Enable a command bot-wide."""
        command_exists = await self._check_command_exists(command_name)

        if not command_exists:
            await Embeds.send_error_embed(
                ctx.channel, ctx.author, f"The command `{command_name}` does not exist."
            )
            return

        current_status = await self.bot.database.get_command_status(
            command_name, channel_id=None
        )

        if current_status:
            await Embeds.send_error_embed(
                ctx.channel, ctx.author, f"The `{command_name}` command is already enabled globally."
            )
        else:
            await self.bot.database.set_command_status(
                command_name, enabled=True, channel_id=None
            )
            await Embeds.send_success_embed(
                ctx.channel, ctx.author, f"The `{command_name}` command has been enabled globally."
            )

    @command_cog.command(
        name="disable", help="Disable a command bot-wide.", hidden=True
    )
    @commands.is_owner()
    async def disable_bot_command(self, ctx: Context, *, command_name: str):
        """Disable a command globally."""
        command_exists = await self._check_command_exists(command_name)

        if not command_exists:
            await Embeds.send_error_embed(
                ctx.channel, ctx.author, f"The command `{command_name}` does not exist."
            )
            return

        current_status = await self.bot.database.get_command_status(
            command_name, channel_id=None
        )

        if not current_status:
            await Embeds.send_error_embed(
                ctx.channel, ctx.author, f"The `{command_name}` command is already disabled globally."
            )
        else:
            await self.bot.database.set_command_status(
                command_name, enabled=False, channel_id=None
            )
            await Embeds.send_success_embed(
                ctx.channel, ctx.author, f"The `{command_name}` command has been disabled globally."
            )

    @commands.group(name="bank", invoke_without_command=True, hidden=True)
    @commands.is_owner()
    async def adminbank(self, ctx: Context):
        """Admin bank management commands."""
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
                lines.append(f"`{prefix}bank {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}bank {name}`{aliases}")

        if not lines:
            description = "No subcommands available."
        else:
            description = "\n".join(lines)

        embed = discord.Embed(
            title="Bank — Available Commands",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Use {prefix}bank <subcommand> for details.")

        await ctx.reply(embed=embed, mention_author=False)

    @adminbank.command(name="give", aliases=["grant", "award", "wire"], hidden=True)
    @commands.is_owner()
    async def admin_bank_give(self, ctx: Context, member: discord.Member, amount: str):
        """Give a specified amount to a user's bank balance."""
        member_id = member.id
        member_wallet_id = await self.bot.database.get_wallet_id_for_user(member_id)
        try:
            treasury = await self.bot.database.get_treasury_balance()

            try:
                amount = await self.amount_handler(amount, treasury)
            except ValueError as e:
                embed = discord.Embed(description=str(e), color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return
            try:
                await self.bot.database.process_treasury_transaction(
                    member_wallet_id,
                    amount,
                    f"Admin Audit - Grant from {ctx.author.name}",
                )
            except ValueError as e:
                embed = discord.Embed(
                    description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
                )
                await ctx.reply(embed=embed, delete_after=5)
                return
            await self.bot.database.update_supply()

            embed = discord.Embed(
                description=f"Gave {self.currency_name} **{await self.formatter(amount)}** to {member.display_name}",
                color=discord.Color.green(),
            )
            embed.set_author(name="Admin Audit", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(
        name="take", aliases=["steal", "seize", "confiscate"], hidden=True
    )
    @commands.is_owner()
    async def admin_bank_steal(self, ctx: Context, member: discord.Member, amount: str):
        """Take a specified amount from a user's bank balance."""
        try:
            member_id = member.id
            member_wallet_id = await self.bot.database.get_wallet_id_for_user(member_id)
            member_bank_balance = await self.bot.database.get_bank_balance(
                member_wallet_id
            )
            member_wallet_balance = await self.bot.database.get_wallet_balance(
                member_wallet_id
            )
            total_balance = member_bank_balance + member_wallet_balance
            amount = await self.amount_handler(amount, total_balance)
            amount = Decimal(amount)
            try:
                await self.bot.database.withdraw_from_bank(
                    member_wallet_id,
                    amount,
                    f"Admin Audit - Seizure by {ctx.author.name}",
                )
                await self.bot.database.process_treasury_transaction(
                    member_wallet_id,
                    -amount,
                    f"Admin Audit - Seizure by {ctx.author.name}",
                )
            except ValueError as e:
                embed = discord.Embed(
                    description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
                )
                await ctx.reply(embed=embed, delete_after=5)
                return
            await self.bot.database.validate_economy()

            embed = discord.Embed(
                description=f"Took {self.currency_name} **{await self.formatter(amount)}** from {member.display_name}",
                color=discord.Color.red(),
            )
            embed.set_author(name="Admin Audit", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(name="freeze", aliases=["lock"], hidden=True)
    @commands.is_owner()
    async def admin_bank_freeze(self, ctx: Context, member: discord.Member):
        """Freeze a user's bank account."""
        try:
            member_id = member.id
            member_wallet_id = await self.bot.database.get_wallet_id_for_user(member_id)
            await self.bot.database.freeze_wallet(member_wallet_id)
            await self.bot.database.validate_economy()

            embed = discord.Embed(
                description=f"Froze {member.display_name}'s bank account.",
                color=discord.Color.red(),
            )
            embed.set_author(name="Admin Audit", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(name="thaw", aliases=["unfreeze"], hidden=True)
    @commands.is_owner()
    async def admin_bank_unfreeze(self, ctx: Context, member: discord.Member):
        """Thaw a user's bank account."""
        try:
            member_id = member.id
            member_wallet_id = await self.bot.database.get_wallet_id_for_user(member_id)
            await self.bot.database.unfreeze_wallet(member_wallet_id)
            await self.bot.database.validate_economy()

            embed = discord.Embed(
                description=f"Unfroze {member.display_name}'s bank account.",
                color=discord.Color.green(),
            )
            embed.set_author(name="Admin Audit", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(name="reset", aliases=["wipe"], hidden=True)
    @commands.is_owner()
    async def admin_bank_reset(self, ctx: Context, member: discord.Member):
        """Reset a user's bank and wallet balance to zero."""
        try:
            member_wallet_id = await self.bot.database.get_wallet_id_for_user(member.id)
            
            bank_balance = Decimal(str(await self.bot.database.get_bank_balance(member_wallet_id)))
            
            if bank_balance != 0:
                await self.bot.database.withdraw_from_bank(
                    member_wallet_id,
                    bank_balance,
                    f"Admin Audit - Reset (Bank Clear) by {ctx.author.name}",
                )

            total_wallet_balance = Decimal(str(await self.bot.database.get_wallet_balance(member_wallet_id)))

            if total_wallet_balance != 0:
                await self.bot.database.process_treasury_transaction(
                    member_wallet_id,
                    -total_wallet_balance,
                    f"Admin Audit - Reset (Full Wipe) by {ctx.author.name}",
                )

            await self.bot.database.validate_economy()

            embed = discord.Embed(
                description=f"✅ Successfully reset **{member.display_name}** to {self.currency_name} **0**.",
                color=discord.Color.orange(),
            )
            embed.set_author(name="Admin Audit", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(embed=discord.Embed(description=f"❌ Error: {e}", color=discord.Color.red()))

    @adminbank.command(name="refund", aliases=["reimburse"], hidden=True)
    @commands.is_owner()
    async def admin_bank_refund(self, ctx: Context, member: discord.Member, txid: str):
        """Refund a transaction."""

        try:
            await self.bot.database.refund_transaction(txid, f"Admin Audit - Refund by {ctx.author.name}")
            await self.bot.database.validate_economy()

            embed = discord.Embed(
                description=f"✅ Successfully refunded transaction **{txid}** for user **{member.display_name}**.",
                color=discord.Color.green(),
            )
            embed.set_author(name="Admin Audit", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(name="mint", aliases=["create"], hidden=True)
    @commands.is_owner()
    async def mint(self, ctx: Context, amount: str):
        """Mint to currency supply."""
        try:
            treasury = await self.bot.database.get_treasury_balance()
            amount = await self.amount_handler(amount, treasury)
            await self.bot.database.mint_currency(
                amount, f"Admin Audit - Mint by {ctx.author.name}"
            )
            await self.bot.database.validate_economy()

            embed = discord.Embed(
                description=f"Minted {self.currency_name} **{await self.formatter(amount)}**.",
                color=discord.Color.green(),
            )
            embed.set_author(name="Admin Audit", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(name="burn", aliases=["destroy"], hidden=True)
    @commands.is_owner()
    async def burn(self, ctx: Context, amount: str):
        """Burn from currency supply."""
        try:
            treasury = await self.bot.database.get_treasury_balance()
            amount = await self.amount_handler(amount, treasury)
            await self.bot.database.burn_currency(
                amount, f"Admin Audit - Burn {ctx.author.name}"
            )
            await self.bot.database.validate_economy()

            embed = discord.Embed(
                description=f"Burned {self.currency_name} **{await self.formatter(amount)}**.",
                color=discord.Color.green(),
            )
            embed.set_author(name="Admin Audit", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    # ==================== VIP Admin Commands ====================

    @adminbank.command(name="setviptier", hidden=True)
    @commands.is_owner()
    async def set_vip_tier(
        self,
        ctx: Context,
        user: discord.User,
        tier_name: str,
    ):
        """Manually set a user's VIP tier.

        Usage: !setviptier <user> <tier_name>
        Tier names: Bronze, Silver, Gold, Platinum, Diamond
        """
        try:
            # Get tier by name
            tier_name = tier_name.capitalize()
            tiers = await self.bot.database.get_all_vip_tiers()
            tier = next((t for t in tiers if t.name == tier_name), None)

            if not tier:
                valid_tiers = ", ".join(t.name for t in tiers)
                return await ctx.send(
                    f"Invalid tier name. Valid tiers: {valid_tiers}"
                )

            success = await self.bot.database.set_user_vip_tier(user.id, tier.id)

            if success:
                embed = discord.Embed(
                    title="VIP Tier Updated",
                    description=f"Set {user.mention}'s VIP tier to **{tier.name}**.",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Tier ID", value=str(tier.id), inline=True)
                embed.add_field(name="Level", value=str(tier.level), inline=True)
                embed.add_field(name="Rakeback Rate", value=f"{float(tier.rakeback_rate) * 100:.0f}%", inline=True)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Failed to update VIP tier.")

        except Exception as e:
            logger.error(f"Error in set_vip_tier: {e}")
            await ctx.send(f"Error: {e}")

    @adminbank.command(name="resetvip", hidden=True)
    @commands.is_owner()
    async def reset_vip(self, ctx: Context, user: discord.User):
        """Reset a user's VIP progress to default.

        Usage: !resetvip <user>
        """
        try:
            success = await self.bot.database.reset_user_vip(user.id)

            if success:
                embed = discord.Embed(
                    title="VIP Progress Reset",
                    description=f"Reset {user.mention}'s VIP progress to Bronze.",
                    color=discord.Color.green(),
                )
                await ctx.send(embed=embed)
            else:
                await ctx.send("Failed to reset VIP progress.")

        except Exception as e:
            logger.error(f"Error in reset_vip: {e}")
            await ctx.send(f"Error: {e}")

    @adminbank.command(name="vipconfig", hidden=True)
    @commands.is_owner()
    async def vip_config(self, ctx: Context):
        """Display VIP tier configuration.

        Usage: !vipconfig
        """
        try:
            tiers = await self.bot.database.get_all_vip_tiers()

            if not tiers:
                await self.bot.database.ensure_default_vip_tiers()
                tiers = await self.bot.database.get_all_vip_tiers()

            embed = discord.Embed(
                title="VIP Tier Configuration",
                color=discord.Color.gold(),
            )

            for tier in tiers:
                min_wagered_str = f"{float(tier.min_wagered):,.0f}"
                rakeback_pct = float(tier.rakeback_rate) * 100
                rtp_pct = float(tier.rtp_bonus) * 100

                embed.add_field(
                    name=f"Level {tier.level}: {tier.name}",
                    value=(
                        f"**Min Wagered:** {min_wagered_str}\n"
                        f"**Rakeback:** {rakeback_pct:.0f}%\n"
                        f"**RTP Bonus:** +{rtp_pct:.1f}%\n"
                        f"**ID:** {tier.id}"
                    ),
                    inline=False,
                )

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in vip_config: {e}")
            await ctx.send(f"Error: {e}")

    @adminbank.command(name="getvipwagered", hidden=True)
    @commands.is_owner()
    async def get_vip_wagered(
        self,
        ctx: Context,
        user: discord.User,
    ):
        """Get a user's total wagered amount."""
        try:
            # Get total wagered from GameHistory
            total_wagered = await self.bot.database.get_total_wagered_all_games(user.id)
            vip_info = await self.bot.database.get_rakeback_info(user.id)
            current_tier = vip_info.get("current_tier")

            embed = discord.Embed(
                title="VIP Wagered Info",
                description=f"{user.mention}'s total wagered (from GameHistory): **{float(total_wagered):,.0f}**",
                color=discord.Color.blue(),
            )

            if current_tier:
                embed.add_field(
                    name="Current Tier",
                    value=f"**{current_tier.name}** (Level {current_tier.level})",
                    inline=False,
                )

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in get_vip_wagered: {e}")
            await ctx.send(f"Error: {e}")

    @adminbank.command(name="addrakeback", hidden=True)
    @commands.is_owner()
    async def add_rakeback(
        self,
        ctx: Context,
        user: discord.User,
        amount: str,
    ):
        """Add rakeback to a user's balance."""
        try:
            amount_decimal = Decimal(amount.replace(",", "").replace("_", ""))

            # Add rakeback directly
            async with self.bot.database.async_sessionmaker() as session:
                from database.models import RakebackBalance
                from datetime import datetime

                result = await session.execute(
                    select(RakebackBalance).where(RakebackBalance.user_id == user.id)
                )
                balance = result.scalar_one_or_none()

                if not balance:
                    balance = RakebackBalance(user_id=user.id, accumulated=amount_decimal)
                    session.add(balance)
                else:
                    balance.accumulated = (balance.accumulated or Decimal("0")) + amount_decimal

                await session.commit()

            embed = discord.Embed(
                title="Rakeback Added",
                description=f"Added **{float(amount_decimal):,.0f}** rakeback to {user.mention}'s balance.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in add_rakeback: {e}")
            await ctx.send(f"Error: {e}")

    @adminbank.command(name="initviptiers", hidden=True)
    @commands.is_owner()
    async def init_vip_tiers(self, ctx: Context):
        """Initialize default VIP tiers.
        """
        try:
            await self.bot.database.ensure_default_vip_tiers()
            tiers = await self.bot.database.get_all_vip_tiers()

            embed = discord.Embed(
                title="VIP Tiers Initialized",
                description=f"Created {len(tiers)} default VIP tiers.",
                color=discord.Color.green(),
            )

            for tier in tiers:
                embed.add_field(
                    name=f"{tier.name}",
                    value=f"Level {tier.level} | {float(tier.rakeback_rate) * 100:.0f}% rakeback",
                    inline=True,
                )

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in init_vip_tiers: {e}")
            await ctx.send(f"Error: {e}")

    @adminbank.command(name="rebalance", aliases=["rebal"], hidden=True)
    @commands.is_owner()
    async def admin_bank_rebalance(self, ctx: Context):
        """Force an economic rebalance cycle and display the result."""
        try:
            result = await self.bot.database.perform_economic_rebalance()
            action = result.get("action", "unknown")

            if action in ("mint", "burn"):
                embed = discord.Embed(
                    title="Economic Rebalance",
                    description=(
                        f"**Action:** {action.upper()} {self.currency_name} **{await self.formatter(result['amount'])}**\n"
                        f"**Health:** {result['health_before']:.2%} → {result['health_after']:.2%}\n"
                        f"**Target:** {result['target']:.2%} (range: {result['min_hw']:.2%} – {result['max_hw']:.2%})\n"
                        f"**Daily Minted:** {result.get('daily_minted', 0)}\n"
                        f"**Daily Burned:** {result.get('daily_burned', 0)}"
                    ),
                    color=discord.Color.green() if action == "mint" else discord.Color.orange(),
                )
            elif action == "error":
                embed = discord.Embed(
                    title="Rebalance Error",
                    description=f"Error: {result.get('reason', 'unknown')}",
                    color=discord.Color.red(),
                )
            else:
                embed = discord.Embed(
                    title="Rebalance Skipped",
                    description=result.get("reason", "No action needed"),
                    color=discord.Color.greyple(),
                )
                if "treasury_health" in result:
                    embed.add_field(name="Treasury Health", value=f"{result['treasury_health']:.2%}", inline=True)
                if "target" in result:
                    embed.add_field(name="Target", value=f"{result['target']:.2%}", inline=True)

            embed.set_author(name="Admin Audit", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(
                embed=discord.Embed(description=f"Error: {e}", color=discord.Color.red())
            )

    @commands.command(name="shopitem", hidden=True)
    @commands.is_owner()
    async def add_item(self, ctx: commands.Context):
        """Allows an admin to add a new item to the shop with step‐by‐step prompts."""

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send("Please enter the item name:")
        try:
            item_name = await self.bot.wait_for("message", check=check, timeout=60.0)
            name = item_name.content
        except asyncio.TimeoutError:
            await ctx.send("You took too long to respond! Cancelling item addition.")
            return

        await ctx.send("Please enter the item description:")
        try:
            item_description = await self.bot.wait_for(
                "message", check=check, timeout=60.0
            )
            description = item_description.content
        except asyncio.TimeoutError:
            await ctx.send("You took too long to respond! Cancelling item addition.")
            return

        await ctx.send("Please enter the item price (numeric value):")
        try:
            item_price = await self.bot.wait_for("message", check=check, timeout=60.0)
            price = Decimal(item_price.content)
        except (ValueError, asyncio.TimeoutError):
            await ctx.send("Invalid input or timeout. Cancelling item addition.")
            return

        await ctx.send("Please enter the item quantity (whole number):")
        try:
            item_quantity = await self.bot.wait_for(
                "message", check=check, timeout=60.0
            )
            quantity = int(item_quantity.content)
        except (ValueError, asyncio.TimeoutError):
            await ctx.send("Invalid input or timeout. Cancelling item addition.")
            return

        await ctx.send("Should this item have unlimited stock? (yes/no):")
        try:
            unlimited_msg = await self.bot.wait_for(
                "message", check=check, timeout=60.0
            )
            unlimited = unlimited_msg.content.strip().lower() in ["yes", "y"]
        except asyncio.TimeoutError:
            await ctx.send("You took too long to respond! Cancelling item addition.")
            return

        await ctx.send(
            "Please enter the item type. Available types are: "
            + ", ".join([t.name for t in ItemType])
        )
        try:
            item_type_msg = await self.bot.wait_for(
                "message", check=check, timeout=60.0
            )
            type_input = item_type_msg.content.strip().upper()
            try:
                item_type = ItemType[type_input]
            except KeyError:
                await ctx.send("Invalid item type provided. Cancelling item addition.")
                return
        except asyncio.TimeoutError:
            await ctx.send("You took too long to respond! Cancelling item addition.")
            return

        await self.bot.database.add_shop_item(
            name=name,
            description=description,
            price=price,
            quantity=quantity,
            item_type=item_type,
            unlimited=unlimited,
        )
        stock_text = "unlimited" if unlimited else str(quantity)
        embed = discord.Embed(
            title="Shop",
            description=f"Added {stock_text} of '{name}' to the shop for {price} coins each.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @commands.command(name="restock", hidden=True)
    @commands.is_owner()
    async def restock_item(
        self, ctx: commands.Context, item_identifier: str, quantity: int
    ):
        """Allows an admin to restock an existing item in the shop by item ID or item name."""
        try:
            try:
                item_id = int(item_identifier)
                item = await self.bot.database.get_shop_item_by_id(item_id)
            except ValueError:
                item = await self.bot.database.get_shop_item(item_identifier)
        except Exception as e:
            embed = discord.Embed(
                title="Shop",
                description=f"Error retrieving item: {e}",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if not item:
            embed = discord.Embed(
                title="Shop",
                description="Item not found in the shop.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        success = await self.bot.database.update_shop_item_quantity(item.id, quantity)
        if success:
            embed = discord.Embed(
                title="Shop",
                description=f"{quantity} of '{item.name}' have been added to the shop.",
                color=discord.Color.green(),
            )
        else:
            embed = discord.Embed(
                title="Shop",
                description="Failed to update item quantity.",
                color=discord.Color.red(),
            )
        await ctx.send(embed=embed)

    @app_commands.command(name="shopmodal", description="Open a modal to create a new shop item with effect configuration.")
    @app_commands.check(_owner_check)
    async def shop_item_modal(self, interaction: discord.Interaction):
        """Open a modal to create a new shop item with effect configuration."""
        modal = ShopItemModal(self.bot)
        await interaction.response.send_modal(modal)

    @app_commands.command(name="editshopitem", description="Edit an existing shop item using a modal.")
    @app_commands.check(_owner_check)
    @app_commands.describe(item_id="The ID of the shop item to edit")
    async def edit_shop_item(self, interaction: discord.Interaction, item_id: int):
        """Edit an existing shop item using a modal."""
        item = await self.bot.database.get_shop_item_by_id(item_id)
        if not item:
            embed = discord.Embed(
                title="Shop",
                description=f"No item found with ID {item_id}.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        modal = ShopItemModal(self.bot, edit_item=item)
        await interaction.response.send_modal(modal)

    @commands.command(name="listitems", hidden=True)
    @commands.is_owner()
    async def list_shop_items(self, ctx: commands.Context, page: int = 1):
        """List all shop items with their details."""
        items = await self.bot.database.list_shop_items()
        if not items:
            embed = discord.Embed(
                title="Shop Items",
                description="No items in the shop.",
                color=discord.Color.orange(),
            )
            await ctx.send(embed=embed)
            return

        items_per_page = 10
        total_pages = (len(items) - 1) // items_per_page + 1
        page = max(1, min(page, total_pages))

        start = (page - 1) * items_per_page
        end = start + items_per_page
        page_items = items[start:end]

        embed = discord.Embed(
            title=f"Shop Items (Page {page}/{total_pages})",
            color=discord.Color.blurple(),
        )

        for item in page_items:
            stock = "Unlimited" if item.unlimited else str(item.quantity)
            effect_str = ""
            if item.effect:
                effect_str = f"\n**Effect:** {item.effect}"
                if item.effect_value:
                    effect_str += f" ({item.effect_value})"
                if item.effect_duration:
                    mins, secs = divmod(item.effect_duration, 60)
                    effect_str += f" [{mins}m {secs}s]"
                if item.cooldown_seconds:
                    effect_str += f" | CD: {item.cooldown_seconds}s"

            embed.add_field(
                name=f"ID: {item.id} | {item.name}",
                value=f"**Price:** {item.price} | **Stock:** {stock} | **Type:** {item.item_type.value}{effect_str}",
                inline=False,
            )

        await ctx.send(embed=embed)

    @commands.command(name="giveitem", hidden=True)
    @commands.is_owner()
    async def give_item(
        self,
        ctx: commands.Context,
        member: discord.Member,
        item_name: str,
        quantity: int = 1,
    ):
        """Give an item directly to a user."""
        # Get the shop item to copy
        shop_items = await self.bot.database.list_shop_items()
        shop_item = next((i for i in shop_items if i.name.lower() == item_name.lower()), None)

        if not shop_item:
            embed = discord.Embed(
                title="Give Item",
                description=f"No shop item found with name '{item_name}'.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        # Create inventory item(s) for the user
        for _ in range(quantity):
            await self.bot.database.add_item_to_inventory(
                user_id=member.id,
                name=shop_item.name,
                description=shop_item.description,
                quantity=1,
                item_type=shop_item.item_type,
                effect=shop_item.effect,
                effect_value=shop_item.effect_value,
                effect_duration=shop_item.effect_duration,
                cooldown_seconds=shop_item.cooldown_seconds,
            )

        embed = discord.Embed(
            title="Give Item",
            description=f"Gave {quantity}x **{shop_item.name}** to {member.display_name}.",
            color=discord.Color.green(),
        )
        if shop_item.effect:
            embed.add_field(name="Effect", value=f"{shop_item.effect}: {shop_item.effect_value or 'N/A'}")
        await ctx.send(embed=embed)

    @commands.command(name="vieweffects", hidden=True)
    @commands.is_owner()
    async def view_effects(self, ctx: commands.Context, member: discord.Member):
        """View a user's active effects."""
        effects = await self.bot.database.get_user_active_effects(member.id)

        if not effects:
            embed = discord.Embed(
                title="Active Effects",
                description=f"{member.display_name} has no active effects.",
                color=discord.Color.orange(),
            )
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(
            title=f"Active Effects for {member.display_name}",
            color=discord.Color.blurple(),
        )

        for effect in effects:
            expires_in = effect.expires_at - datetime.utcnow()
            mins, secs = divmod(int(expires_in.total_seconds()), 60)
            hours, mins = divmod(mins, 60)

            if hours > 0:
                time_str = f"{hours}h {mins}m"
            else:
                time_str = f"{mins}m {secs}s"

            embed.add_field(
                name=f"{effect.effect_type} (from {effect.source_item_name})",
                value=f"**Value:** {effect.effect_value}\n**Expires in:** {time_str}",
                inline=False,
            )

        await ctx.send(embed=embed)

    @commands.command(name="clearcooldown", hidden=True)
    @commands.is_owner()
    async def clear_cooldown(
        self, ctx: commands.Context, member: discord.Member, item_name: str
    ):
        """Clear a user's item cooldown (debug tool)."""
        cleared = await self.bot.database.clear_item_cooldown(member.id, item_name)

        if cleared:
            embed = discord.Embed(
                title="Cooldown Cleared",
                description=f"Cleared cooldown for **{item_name}** for {member.display_name}.",
                color=discord.Color.green(),
            )
        else:
            embed = discord.Embed(
                title="No Cooldown",
                description=f"{member.display_name} had no cooldown for **{item_name}**.",
                color=discord.Color.orange(),
            )

        await ctx.send(embed=embed)

    @commands.command(
        name="ownerlog",
        aliases=["ownerlogs", "adminlog"],
        description="View the owner-only command audit log.",
        hidden=True,
    )
    @commands.is_owner()
    async def owner_log(
        self,
        ctx: Context,
        user: Optional[Union[discord.Member, discord.User, int]] = None,
        command_name: Optional[str] = None,
        limit: int = 25,
    ):
        """Display recent invocations of owner-only commands.

        Arguments:
            user: Optional user filter (member, user, or raw ID).
            command_name: Optional command-name filter.
            limit: Number of entries to fetch (1-100, default 25).
        """
        limit = max(1, min(limit, 100))
        user_id = None
        if user is not None:
            user_id = user.id if isinstance(user, (discord.Member, discord.User)) else int(user)

        entries = await self.bot.database.get_owner_audit_log(
            user_id=user_id,
            command_name=command_name,
            guild_id=ctx.guild.id if ctx.guild else None,
            limit=limit,
        )

        if not entries:
            embed = discord.Embed(
                title="Owner Command Audit Log",
                description="No matching entries found.",
                color=discord.Color.orange(),
            )
            return await ctx.reply(embed=embed)

        lines = []
        for entry in entries:
            timestamp = discord.utils.format_dt(entry.created_at, "R") if entry.created_at else "unknown"
            user_resolved = await self._resolve_user_display(entry.user_id)
            location = f"guild `{entry.guild_id}`" if entry.guild_id else "DMs"
            if entry.channel_id:
                location += f" / channel `{entry.channel_id}`"
            arg_summary = ""
            if entry.args:
                arg_summary = f" | args: `{self._truncate(str(entry.args), 60)}`"
            lines.append(
                f"**`{entry.command_name}`** by {user_resolved} — {timestamp}\n↳ {location}{arg_summary}"
            )

        pages = []
        per_page = 5
        for i in range(0, len(lines), per_page):
            chunk = lines[i : i + per_page]
            embed = discord.Embed(
                title="Owner Command Audit Log",
                description="\n\n".join(chunk),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"Page {i // per_page + 1}/{(len(lines) - 1) // per_page + 1} • {len(entries)} entries")
            pages.append(embed)

        if len(pages) == 1:
            return await ctx.reply(embed=pages[0])

        view = self._SimplePaginator(pages, ctx.author.id)
        view.message = await ctx.reply(embed=pages[0], view=view)

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """Return a truncated string with an ellipsis if it exceeds max_len."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    async def _resolve_user_display(self, user_hash: str) -> str:
        """Try to resolve a stored user hash to a mention or ID string."""
        raw_id = await self.bot.database.resolve_user_hash(user_hash)
        if raw_id is None:
            return f"`{user_hash[:12]}...`"
        user = self.bot.get_user(raw_id)
        if user:
            return user.mention
        return f"<@{raw_id}>"

    class _SimplePaginator(discord.ui.View):
        """Minimal paginator for owner log embeds."""

        def __init__(self, pages: list[discord.Embed], author_id: int):
            super().__init__(timeout=180)
            self.pages = pages
            self.author_id = author_id
            self.current_page = 0

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "This paginator isn't for you.", ephemeral=True
                )
                return False
            return True

        @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="◀️")
        async def previous(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            if self.current_page > 0:
                self.current_page -= 1
                await interaction.response.edit_message(
                    embed=self.pages[self.current_page], view=self
                )
            else:
                await interaction.response.defer()

        @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="▶️")
        async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.current_page < len(self.pages) - 1:
                self.current_page += 1
                await interaction.response.edit_message(
                    embed=self.pages[self.current_page], view=self
                )
            else:
                await interaction.response.defer()


async def setup(bot) -> None:
    await bot.add_cog(Owner(bot))
    logger.debug("Owner cog initialized successfully") 
