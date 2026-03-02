import discord
from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import os
import json
import io
import subprocess
import docker
import time
import uuid
import ast
import copy
import re
import traceback
from contextlib import redirect_stdout
import textwrap
import logging
import asyncio
import psutil
from utils.misc import MiscUtils
from utils.admin_api import AdminAPIServer
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
)
from database.manager import ItemType
import importlib.util

logger = logging.getLogger("discord_bot")

MAX_FIELD_VALUE_LENGTH = 1024
MAX_EMBED_DESC_LENGTH = 2048
MAX_EMBED_CHAR_LENGTH = 6000
MAX_FIELDS = 25


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
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(
                embed=self.get_page_embed(), view=self
            )
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, button: discord.ui.Button, interaction: discord.Interaction):
        total_pages = (len(self.tasks) - 1) // self.per_page + 1
        if self.current_page < total_pages - 1:
            self.current_page += 1
            await interaction.response.edit_message(
                embed=self.get_page_embed(), view=self
            )
        else:
            await interaction.response.defer()


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




class Owner(commands.Cog, name="Owner"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.currency_name = "<:coin:1359823671581085847>"
        self.utils = MiscUtils(self)
        self.process = psutil.Process(os.getpid())
        self._last_result: Optional[Any] = None
        self.start_time = discord.utils.utcnow()
        self.whitelist_clubhouse = [
            284439598422163476,  # E
            1166141915297743010,  # dennis
        ]
        self.whitelist_tpne = [
            284439598422163476,  # E
            1208003447388119040, # tpne alt
            1288160215241326674, # tpne alt 2
        ]
        self.whitelist_mistrust = [
            284439598422163476,  # E
            1095747082599530627, # envy
            657182369240973312, # chaos
            1219090700407279656, # toxic
        ]
        self.whitelist_private = [
            284439598422163476,  # E
        ]
        self.shh_emoji = "🤫"

    def is_whitelisted_wod(self, user_id: int):
        """Check if the user ID is in the whitelist."""
        return user_id in self.whitelist_wod

    def is_whitelisted_clubhouse(self, user_id: int):
        """Check if the user ID is in the whitelist."""
        return user_id in self.whitelist_clubhouse

    def is_whitelisted_tpne(self, user_id: int):
        """Check if the user ID is in the whitelist."""
        return user_id in self.whitelist_tpne

    def is_whitelisted_mistrust(self, user_id: int):
        """Check if the user ID is in the whitelist."""
        return user_id in self.whitelist_mistrust
    
    def is_whitelisted_private(self, user_id: int):
        """Check if the user ID is in the whitelist."""
        return user_id in self.whitelist_private

    class _MetricsGraphView(discord.ui.View):
        def __init__(
            self,
            owner,
            author_id: int,
            title: str,
            labels: list[str],
            values: list[float],
            *,
            kind: str = "bar",
            ylabel: str = "Count",
        ):
            super().__init__(timeout=120)
            self.owner = owner
            self.author_id = author_id
            self.title = title
            self.labels = labels
            self.values = values
            self.kind = kind
            self.ylabel = ylabel

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "You can’t use this button.", ephemeral=True
                )
                return False
            return True

        @discord.ui.button(label="Graph", style=discord.ButtonStyle.primary)
        async def graph(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            try:
                image_bytes = self.owner._render_metrics_chart(
                    title=self.title,
                    labels=self.labels,
                    values=self.values,
                    kind=self.kind,
                    ylabel=self.ylabel,
                )
            except Exception as e:
                await interaction.response.send_message(
                    f"Failed to render graph: {e}", ephemeral=True
                )
                return

            file = discord.File(fp=image_bytes, filename="metrics.png")
            await interaction.response.send_message(file=file, ephemeral=True)

    def _render_metrics_chart(
        self,
        *,
        title: str,
        labels: list[str],
        values: list[float],
        kind: str,
        ylabel: str,
    ):
        import io as _io
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 4.5))
        if kind == "line":
            ax.plot(labels, values, marker="o")
        else:
            ax.bar(labels, values)

        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_xlabel("")
        ax.tick_params(axis="x", labelrotation=45)
        fig.tight_layout()

        buffer = _io.BytesIO()
        fig.savefig(buffer, format="png", dpi=150)
        plt.close(fig)
        buffer.seek(0)
        return buffer

    @commands.group(
        name="metrics",
        help="Metrics commands for bot stats.",
        invoke_without_command=True,
        hidden=True,
    )
    @commands.is_owner()
    async def metrics(self, ctx: Context):
        embed = discord.Embed(
            title="Metrics",
            description="Available subcommands: usage, latency, errors, exposure, topguilds, perday",
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

    @metrics.command(name="usage", hidden=True)
    @commands.is_owner()
    async def metrics_usage(
        self,
        ctx: Context,
        days: int = 7,
        guild_id: Optional[int] = None,
    ):
        days = max(0, days)
        cutoff = None
        if days:
            cutoff = discord.utils.utcnow().date() - timedelta(days=days)

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
            if cutoff:
                stmt = stmt.where(CommandUsageDaily.bucket_date >= cutoff)
            if guild_id:
                stmt = stmt.where(CommandUsageDaily.guild_id == guild_id)

            result = await session.execute(stmt)
            rows = result.all()

        if not rows:
            return await ctx.send("No command usage data found for that range.")

        lines = []
        for name, is_slash, total in rows:
            kind = "slash" if is_slash else "prefix"
            lines.append(f"{name} ({kind}) - {total}")

        embed = discord.Embed(
            title="Command Usage",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Days: {days}")
        view = self._MetricsGraphView(
            self,
            ctx.author.id,
            title="Command Usage",
            labels=[f"{name} ({'slash' if is_slash else 'prefix'})" for name, is_slash, _ in rows],
            values=[int(total) for _, _, total in rows],
            ylabel="Calls",
        )
        await ctx.send(embed=embed, view=view)

    @metrics.command(name="latency", hidden=True)
    @commands.is_owner()
    async def metrics_latency(
        self,
        ctx: Context,
        days: int = 7,
        guild_id: Optional[int] = None,
    ):
        days = max(0, days)
        cutoff = None
        if days:
            cutoff = discord.utils.utcnow().date() - timedelta(days=days)

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
            if cutoff:
                stmt = stmt.where(CommandLatencyDaily.bucket_date >= cutoff)
            if guild_id:
                stmt = stmt.where(CommandLatencyDaily.guild_id == guild_id)

            result = await session.execute(stmt)
            rows = result.all()

        if not rows:
            return await ctx.send("No command latency data found for that range.")

        lines = []
        for name, is_slash, sum_ms, count in rows:
            avg_ms = int(sum_ms / count) if count else 0
            kind = "slash" if is_slash else "prefix"
            lines.append(f"{name} ({kind}) - avg {avg_ms} ms ({count} calls)")

        embed = discord.Embed(
            title="Command Latency",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Days: {days}")
        view = self._MetricsGraphView(
            self,
            ctx.author.id,
            title="Command Latency (Avg)",
            labels=[f"{name} ({'slash' if is_slash else 'prefix'})" for name, is_slash, _, _ in rows],
            values=[int(sum_ms / count) if count else 0 for _, _, sum_ms, count in rows],
            ylabel="Avg ms",
        )
        await ctx.send(embed=embed, view=view)

    @metrics.command(name="errors", hidden=True)
    @commands.is_owner()
    async def metrics_errors(
        self,
        ctx: Context,
        days: int = 7,
        guild_id: Optional[int] = None,
    ):
        days = max(0, days)
        cutoff = None
        if days:
            cutoff = discord.utils.utcnow().date() - timedelta(days=days)

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
            if cutoff:
                stmt = stmt.where(CommandErrorDaily.bucket_date >= cutoff)
            if guild_id:
                stmt = stmt.where(CommandErrorDaily.guild_id == guild_id)

            result = await session.execute(stmt)
            rows = result.all()

        if not rows:
            return await ctx.send("No command error data found for that range.")

        lines = []
        for name, error_type, is_slash, total in rows:
            kind = "slash" if is_slash else "prefix"
            lines.append(f"{name} ({kind}) - {error_type}: {total}")

        embed = discord.Embed(
            title="Command Errors",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Days: {days}")
        view = self._MetricsGraphView(
            self,
            ctx.author.id,
            title="Command Errors",
            labels=[f"{name} ({'slash' if is_slash else 'prefix'})" for name, _, is_slash, _ in rows],
            values=[int(total) for _, _, _, total in rows],
            ylabel="Errors",
        )
        await ctx.send(embed=embed, view=view)

    @metrics.command(name="exposure", hidden=True)
    @commands.is_owner()
    async def metrics_exposure(
        self,
        ctx: Context,
        days: int = 7,
        guild_id: Optional[int] = None,
    ):
        days = max(0, days)
        cutoff = None
        if days:
            cutoff = discord.utils.utcnow().date() - timedelta(days=days)

        async with self.bot.database.async_sessionmaker() as session:
            stmt = select(
                func.count().label("rows"),
                func.count(func.distinct(DailyUserExposure.user_hash)).label("unique"),
            )
            if cutoff:
                stmt = stmt.where(DailyUserExposure.bucket_date >= cutoff)
            if guild_id:
                stmt = stmt.where(DailyUserExposure.guild_id == guild_id)

            result = await session.execute(stmt)
            row = result.first()

        total_rows = row.rows if row else 0
        unique_users = row.unique if row else 0

        embed = discord.Embed(
            title="User Exposure",
            description=(
                f"Unique users: {unique_users}\nExposure rows: {total_rows}"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Days: {days}")
        view = self._MetricsGraphView(
            self,
            ctx.author.id,
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
    ):
        days = max(0, days)
        cutoff = None
        if days:
            cutoff = discord.utils.utcnow().date() - timedelta(days=days)

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
            if cutoff:
                stmt = stmt.where(CommandUsageDaily.bucket_date >= cutoff)

            result = await session.execute(stmt)
            rows = result.all()

        if not rows:
            return await ctx.send("No guild usage data found for that command.")

        lines = []
        for gid, total in rows:
            lines.append(f"{gid or 'DM'} - {total}")

        embed = discord.Embed(
            title=f"Top Guilds for {command_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Days: {days}")
        view = self._MetricsGraphView(
            self,
            ctx.author.id,
            title=f"Top Guilds for {command_name}",
            labels=[str(gid or "DM") for gid, _ in rows],
            values=[int(total) for _, total in rows],
            ylabel="Calls",
        )
        await ctx.send(embed=embed, view=view)

    @metrics.command(name="perday", hidden=True)
    @commands.is_owner()
    async def metrics_perday(
        self,
        ctx: Context,
        metric: str = "usage",
        command_name: Optional[str] = None,
        days: int = 7,
        guild_id: Optional[int] = None,
    ):
        metric = metric.lower()
        days = max(1, min(days, 90))
        cutoff = discord.utils.utcnow().date() - timedelta(days=days)

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
            else:
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

            result = await session.execute(stmt)
            rows = result.all()

        if not rows:
            return await ctx.send("No per-day data found for that range.")

        labels = []
        values = []
        for row in rows:
            bucket = row[0]
            labels.append(bucket.strftime("%Y-%m-%d"))
            if metric == "latency":
                sum_ms = row[1]
                count = row[2]
                values.append(int(sum_ms / count) if count else 0)
            else:
                values.append(int(row[1]))

        embed = discord.Embed(
            title=title,
            description="\n".join(f"{d}: {v}" for d, v in zip(labels, values)),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Days: {days} | Metric: {metric}")
        view = self._MetricsGraphView(
            self,
            ctx.author.id,
            title=title,
            labels=labels,
            values=values,
            kind="line",
            ylabel=ylabel,
        )
        await ctx.send(embed=embed, view=view)

    async def find_role(self, ctx: Context, role_name: str):
        """Helper method to find a role by partial name, ID, or mention."""
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

    @commands.command(name="startapi", hidden=True)
    @commands.is_owner()
    async def start_api(self, ctx: Context, host: str = "127.0.0.1", port: int = 8080, secret: str = None):
        """Start a lightweight admin API server (owner only).

        The server exposes a small JSON status endpoint and a shutdown endpoint
        protected by an admin secret. If `secret` is not provided one will be
        generated and DM'd to the command invoker.
        """
        if getattr(self.bot, "admin_api_server", None):
            return await ctx.send("⚠️ Admin API already running.")

        # generate secret if not provided
        secret = secret or uuid.uuid4().hex

        server = AdminAPIServer(self.bot, host=host, port=port, secret=secret)

        try:
            await server.start()
        except Exception as e:
            return await ctx.send(f"❌ Failed to start Admin API: {e}")

        # store reference on bot so it can be stopped later
        self.bot.admin_api_server = server
        self.bot.admin_api_secret = secret

        try:
            await ctx.author.send(f"Admin API started at http://{host}:{port}\nSecret: {secret}\nUse header X-Admin-Secret to authenticate.")
            await ctx.send(f"✅ Admin API started on {host}:{port} (secret sent to your DMs)")
        except Exception:
            await ctx.send(f"✅ Admin API started on {host}:{port} (could not send DM with secret)")

    @commands.command(name="stopapi", hidden=True)
    @commands.is_owner()
    async def stop_api(self, ctx: Context):
        """Stop the admin API server if it's running."""
        server = getattr(self.bot, "admin_api_server", None)
        if not server:
            return await ctx.send("⚠️ Admin API is not running.")

        try:
            await server.stop()
        except Exception as e:
            return await ctx.send(f"❌ Failed to stop Admin API: {e}")

        try:
            delattr(self.bot, "admin_api_server")
            delattr(self.bot, "admin_api_secret")
        except Exception:
            pass

        await ctx.send("✅ Admin API stopped.")

    @commands.group(
        name="todo", aliases=["task"], invoke_without_command=True, hidden=True
    )
    @commands.is_owner()
    async def todolist(self, ctx: Context):
        """Displays the available subcommands for managing your todo list."""
        embed = discord.Embed(
            title="Todo",
            description="Available subcommands for managing your todo list:",
            color=discord.Color.blurple(),
        )
        subcommands = [subcommand.name for subcommand in ctx.command.commands]
        if subcommands:
            embed.add_field(
                name="Subcommands", value=", ".join(subcommands), inline=False
            )
        await ctx.reply(embed=embed)

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
        embed = discord.Embed(
            title="Game Sessions",
            description="Available subcommands: list, show, events, end",
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

    @gamesession.command(name="list", hidden=True)
    @commands.is_owner()
    async def gamesession_list(self, ctx: Context, limit: int = 10):
        limit = max(1, min(limit, 25))
        sessions = await self.bot.database.list_game_sessions(limit=limit)
        if not sessions:
            return await ctx.send("No active game sessions.")

        lines = []
        for s in sessions:
            created = s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "?"
            lines.append(
                f"{s.id} • {s.game_name} • owner {s.owner_id} • {created}"
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

        embed = discord.Embed(
            title=f"Session {gs.id}", color=discord.Color.blurple()
        )
        embed.add_field(name="Game", value=gs.game_name, inline=True)
        embed.add_field(name="Owner", value=str(gs.owner_id), inline=True)
        embed.add_field(name="Channel", value=str(gs.channel_id), inline=True)
        embed.add_field(name="Wager", value=str(gs.wager_total), inline=True)
        embed.add_field(
            name="Participants",
            value=", ".join(str(p) for p in (gs.participants or [])) or "—",
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
        for r in rows:
            uid = int(r.user_id)
            user = None
            try:
                user = await self.bot.fetch_user(uid)
            except Exception:
                pass

            display_name = str(user) if user else "Unknown User"
            reason = getattr(r, "reason", None) or "No reason provided"
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
            embed.add_field(name="Reason", value=item["reason"], inline=False)
            embed.set_footer(
                text="Use ◀ / ▶ to navigate, ❌ to remove • Controls expire in 3 minutes"
            )
            return embed

        if not entries:
            return await ctx.send("No blacklisted users found.")

        class BlacklistPaginator(discord.ui.View):
            def __init__(self, author_id: int):
                super().__init__(timeout=180.0)
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
                await self.bot.database.remove_blacklisted_user(user_id)

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

        view = BlacklistPaginator(ctx.author.id)
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

            await self.bot.database.add_to_blacklist(member.id, reason)
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
            region = (
                str(guild.region).title() if hasattr(guild, "region") else "Unknown"
            )
            details = (
                f"**ID:** {guild.id}\n"
                f"**Owner:** {owner}\n"
                f"**Members:** {guild.member_count}\n"
                f"**Channels:** {len(guild.channels)}\n"
                f"**Created:** {created}\n"
                f"**Region:** {region}"
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

    @commands.command(
        name="server", help="Get information about a server by its ID.", hidden=True
    )
    @commands.is_owner()
    async def server_info(self, ctx: Context, guild_id: int):
        """Get information about a server by its ID."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.send(f"🚫 Could not find a server with ID `{guild_id}`.")

        member_count = guild.member_count
        online_members = len(
            [m for m in guild.members if m.status is not discord.Status.offline]
        )
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)
        roles = len(guild.roles)
        emojis = len(guild.emojis)
        boosts = guild.premium_subscription_count
        boost_level = guild.premium_tier
        created_at = guild.created_at.strftime("%b %d, %Y")

        embed = discord.Embed(
            title=f"Server Information: {guild.name}", color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="ID", value=guild.id)
        embed.add_field(name="Owner", value=guild.owner)
        embed.add_field(
            name="Members", value=f"{member_count} total\n{online_members} online"
        )
        embed.add_field(
            name="Channels",
            value=f"{text_channels} text\n{voice_channels} voice\n{categories} categories",
        )
        embed.add_field(name="Roles", value=roles)
        embed.add_field(name="Emojis", value=emojis)
        embed.add_field(name="Boosts", value=f"{boosts} (Level {boost_level})")
        embed.add_field(name="Created", value=created_at)
        await ctx.send(embed=embed)

    @commands.command(
        name="eval", help="Evaluate python code through the bot.", hidden=True
    )
    @commands.is_owner()
    async def _eval(self, ctx: Context, *, body: str):
        """Evaluates a code"""

        env = {
            "bot": self.bot,
            "ctx": ctx,
            "channel": ctx.channel,
            "author": ctx.author,
            "guild": ctx.guild,
            "message": ctx.message,
            "_": self._last_result,
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f"```py\n{e.__class__.__name__}: {e}\n```")

        func = env["func"]
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception as e:
            value = stdout.getvalue()
            await ctx.send(f"```py\n{value}{traceback.format_exc()}\n```")
        else:
            value = stdout.getvalue()
            try:
                await ctx.message.add_reaction("\u2705")
            except:
                pass

            if ret is None:
                if value:
                    await ctx.send(f"```py\n{value}\n```")
            else:
                self._last_result = ret
                await ctx.send(f"```py\n{value}{ret}\n```")

    @commands.command(name="sudo", help="Run a command as another user", hidden=True)
    @commands.is_owner()
    async def sudo(
        self,
        ctx: Context,
        who: Union[discord.Member, discord.User],
        channel: Optional[discord.TextChannel],
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
        MAX_REPETITIONS = 50  # Set a safe upper limit to prevent abuse
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

    @commands.command(name="sql", aliases=["db"], help="Run a SQL query", hidden=True)
    @commands.is_owner()
    async def run_sql(self, ctx, *, query: str):
        """Admin-only command to execute SQL queries on the database.
        Supports SELECT and non-SELECT queries while preventing dangerous operations.
        """

        # Remove SQL code block formatting if present
        if query.startswith("```sql") and query.endswith("```"):
            query = query[6:-3].strip()

        lower_query = query.lower()

        # Prevent delete queries without a WHERE clause to avoid wiping entire tables
        if lower_query.strip().startswith("delete") and "where" not in lower_query:
            embed = discord.Embed(
                title="Operation Denied",
                description="DELETE queries must include a WHERE clause to prevent wiping entire tables.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        try:
            async with self.bot.database.async_sessionmaker() as session:
                result = await session.execute(text(query))

                # If it's a SELECT query, fetch and paginate results
                if lower_query.strip().startswith("select"):
                    # Convert to list of mappings (dict-like) for stable column access
                    try:
                        rows = result.mappings().all()
                    except Exception:
                        rows = result.fetchall()

                    if not rows:
                        embed = discord.Embed(
                            title="SQL Query Results",
                            description="No results found.",
                            color=discord.Color.blurple(),
                        )
                        return await ctx.send(embed=embed)

                    # Extract column names (works for mappings or raw rows)
                    if hasattr(rows[0], "keys"):
                        columns = list(rows[0].keys())
                    elif hasattr(rows[0], "_mapping"):
                        columns = list(rows[0]._mapping.keys())
                    else:
                        # Fallback: enumerate columns
                        columns = [f"col{i}" for i in range(len(rows[0]))]

                    total_rows = len(rows)
                    rows_per_page = 5  # visually pleasing number of rows per embed
                    total_pages = (total_rows - 1) // rows_per_page + 1

                    # Helper to chunk long values safely
                    def chunk_text(s: str, limit: int):
                        return (
                            [s[i : i + limit] for i in range(0, len(s), limit)]
                            if len(s) > limit
                            else [s]
                        )

                    embeds: List[discord.Embed] = []

                    for page_idx in range(total_pages):
                        start = page_idx * rows_per_page
                        end = min(start + rows_per_page, total_rows)
                        embed = discord.Embed(
                            title="SQL Query Results",
                            color=discord.Color.blurple(),
                            description=f"Rows {start+1}-{end} of {total_rows}\nColumns: {', '.join(columns)}",
                        )
                        embed.set_author(
                            name=str(ctx.author),
                            icon_url=getattr(ctx.author, "avatar.url", None),
                        )
                        embed.timestamp = discord.utils.utcnow()

                        current_embed_chars = len(embed.description or "") + sum(
                            len(f.name) + len(f.value) for f in embed.fields
                        )

                        for idx in range(start, end):
                            row = rows[idx]
                            # Build pretty row representation
                            if hasattr(row, "items"):
                                parts = [f"**{col}:** {row[col]!s}" for col in columns]
                            elif hasattr(row, "_mapping"):
                                parts = [
                                    f"**{col}:** {row._mapping.get(col)!s}"
                                    for col in columns
                                ]
                            else:
                                parts = [
                                    f"**{i+1}:** {val!s}" for i, val in enumerate(row)
                                ]

                            row_text = "\n".join(parts)

                            # Split into field-sized chunks
                            chunks = chunk_text(row_text, MAX_FIELD_VALUE_LENGTH)
                            for chunk_i, chunk in enumerate(chunks):
                                field_name = f"Row {idx+1}" + (
                                    "" if chunk_i == 0 else f" (cont. {chunk_i})"
                                )
                                addition_length = len(field_name) + len(chunk) + 6
                                # Roll to new embed if we exceed embed limits or field count
                                if (
                                    current_embed_chars + addition_length
                                    > MAX_EMBED_CHAR_LENGTH
                                ) or (len(embed.fields) >= MAX_FIELDS):
                                    embed.set_footer(
                                        text=f"Page {len(embeds)+1} / {total_pages}"
                                    )
                                    embeds.append(embed)
                                    embed = discord.Embed(
                                        title="SQL Query Results (Continued)",
                                        color=discord.Color.blurple(),
                                    )
                                    embed.set_author(
                                        name=str(ctx.author),
                                        icon_url=getattr(
                                            ctx.author, "avatar.url", None
                                        ),
                                    )
                                    embed.timestamp = discord.utils.utcnow()
                                    current_embed_chars = 0

                                embed.add_field(
                                    name=field_name, value=(chunk or "—"), inline=False
                                )
                                current_embed_chars += addition_length

                        embed.set_footer(text=f"Page {len(embeds)+1} / {total_pages}")
                        embeds.append(embed)

                    # Pagination view with Next/Previous buttons and user lock
                    class PaginationView(discord.ui.View):
                        def __init__(self, embeds: List[discord.Embed], author_id: int):
                            super().__init__(timeout=120)
                            self.embeds = embeds
                            self.current = 0
                            self.author_id = author_id

                        async def interaction_check(
                            self, interaction: discord.Interaction
                        ) -> bool:
                            if interaction.user.id != self.author_id:
                                await interaction.response.send_message(
                                    "You are not allowed to interact with this paginator.",
                                    ephemeral=True,
                                )
                                return False
                            return True

                        @discord.ui.button(
                            label="⟨ Previous", style=discord.ButtonStyle.secondary
                        )
                        async def previous(
                            self,
                            button: discord.ui.Button,
                            interaction: discord.Interaction,
                        ):
                            if self.current > 0:
                                self.current -= 1
                                embed = self.embeds[self.current]
                                embed.set_footer(
                                    text=f"Page {self.current+1} / {len(self.embeds)}"
                                )
                                await interaction.response.edit_message(
                                    embed=embed, view=self
                                )
                            else:
                                await interaction.response.defer()

                        @discord.ui.button(
                            label="Next ⟩", style=discord.ButtonStyle.secondary
                        )
                        async def next(
                            self,
                            button: discord.ui.Button,
                            interaction: discord.Interaction,
                        ):
                            if self.current < len(self.embeds) - 1:
                                self.current += 1
                                embed = self.embeds[self.current]
                                embed.set_footer(
                                    text=f"Page {self.current+1} / {len(self.embeds)}"
                                )
                                await interaction.response.edit_message(
                                    embed=embed, view=self
                                )
                            else:
                                await interaction.response.defer()

                        async def on_timeout(self):
                            # Disable buttons when timed out
                            for child in self.children:
                                child.disabled = True
                            try:
                                message = await ctx.fetch_message(self.message_id)
                                await message.edit(view=self)
                            except Exception:
                                pass

                    view = PaginationView(embeds, ctx.author.id)
                    sent = await ctx.send(embed=embeds[0], view=view)
                    # store the message id so the view can disable buttons on timeout
                    view.message_id = sent.id

                else:
                    # For non-SELECT queries, commit the changes and show affected rowcount if available
                    await session.commit()
                    affected = getattr(result, "rowcount", None)
                    description = "The query was executed successfully."
                    if affected is not None:
                        description += f" Rows affected: {affected}"
                    embed = discord.Embed(
                        title="Query Executed",
                        description=description,
                        color=discord.Color.green(),
                    )
                    await ctx.send(embed=embed)

        except SQLAlchemyError as e:
            embed = discord.Embed(
                title="SQL Error",
                description=f"An error occurred: `{str(e)}`",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
        except Exception as exc:
            embed = discord.Embed(
                title="Error",
                description=f"Unexpected error: `{type(exc).__name__}: {exc}`",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @commands.command(name="shutdown", help="Make the bot shutdown.", hidden=True)
    @commands.is_owner()
    async def shutdown(self, ctx: Context) -> None:
        # Only allow shutdown commands in a server
        if isinstance(ctx.channel, discord.DMChannel):
            return await ctx.send("This command can only be used in a server.")

        # Confirmation view with timeout and proper disabling of buttons
        class ConfirmView(discord.ui.View):
            def __init__(self, timeout: float = 30):
                super().__init__(timeout=timeout)
                self.value = None

            @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger)
            async def yes_button(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                self.value = True
                for child in self.children:
                    child.disabled = True
                embed = discord.Embed(
                    description=f"{ctx.author.mention}, shutting down! ✅",
                    color=ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple(),
                )
                await interaction.response.edit_message(
                    embed=embed, view=self
                )
                self.stop()

            @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
            async def no_button(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                self.value = False
                for child in self.children:
                    child.disabled = True
                embed = discord.Embed(
                    description=f"{ctx.author.mention}, shutdown cancelled! ❌",
                    color=ctx.author.top_role.color
                    if ctx.author.top_role
                    else discord.Color.blurple(),
                )
                await interaction.response.edit_message(
                    embed=embed, view=self
                )
                self.stop()

            async def on_timeout(self):
                # If the view times out, disable all buttons and send a message.
                for child in self.children:
                    child.disabled = True
                if self.value is None:
                    try:
                        await ctx.send(
                            "No response received in time. Shutdown cancelled."
                        )
                    except Exception:
                        pass

        view = ConfirmView(timeout=30)
        embed = discord.Embed(
            description=f"{ctx.author.mention}, are you sure you want to shut down? ℹ️",
            color=ctx.author.top_role.color
            if ctx.author.top_role
            else discord.Color.blurple(),
        )

        # Send the embed with the confirmation buttons.
        await ctx.send(embed=embed, view=view)
        await view.wait()  # Wait until the view stops (button press or timeout)

        if view.value is None:
            # No response was given within the timeout period.
            await ctx.send("Shutdown cancelled due to no response.")
        elif view.value:
            # Confirmation received to shutdown.
            await asyncio.sleep(1)
            await self.bot.close()
        else:
            return

    @commands.command(
        name="resetcooldowns",
        help="Resets all cooldowns for a specified user or all users if 'all' is specified",
        aliases=["rscd"],
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

    @commands.command(
        name="e", aliases=["enable"], help="Enable a command bot-wide.", hidden=True
    )
    @commands.is_owner()
    async def enable_bot_command(self, ctx: Context, *, command_name: str):
        """Enable a command bot-wide."""
        command_exists = await self._check_command_exists(command_name)

        if not command_exists:
            await ctx.send(f"The command `{command_name}` does not exist.")
            return

        current_status = await self.bot.database.get_command_status(
            command_name, channel_id=None
        )

        if current_status:
            await ctx.send(f"The `{command_name}` command is already enabled globally.")
        else:
            await self.bot.database.set_command_status(
                command_name, enabled=True, channel_id=None
            )
            await ctx.send(f"The `{command_name}` command has been enabled globally.")

    @commands.command(
        name="d", aliases=["disable"], help="Disable a command bot-wide.", hidden=True
    )
    @commands.is_owner()
    async def disable_bot_command(self, ctx: Context, *, command_name: str):
        """Disable a command globally."""
        command_exists = await self._check_command_exists(command_name)

        if not command_exists:
            await ctx.send(f"The command `{command_name}` does not exist.")
            return

        current_status = await self.bot.database.get_command_status(
            command_name, channel_id=None
        )

        if not current_status:
            await ctx.send(
                f"The `{command_name}` command is already disabled globally."
            )
        else:
            await self.bot.database.set_command_status(
                command_name, enabled=False, channel_id=None
            )
            await ctx.send(f"The `{command_name}` command has been disabled globally.")

    @commands.group(name="bank", invoke_without_command=True, hidden=True)
    @commands.is_owner()
    async def adminbank(self, ctx: Context):
        """Admin bank management commands."""
        embed = discord.Embed(
            title="Admin Bank Commands",
            description="Available subcommands: `give`, `reset`, `steal`, `freeze`, `thaw`, `mint`, `burn`",
            color=discord.Color.blurple(),
        )
        await ctx.reply(embed=embed)

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
            embed.set_author(name="Admin Audit", icon_url=ctx.author.avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(
        name="steal", aliases=["take", "seize", "confiscate"], hidden=True
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
            embed.set_author(name="Admin Audit", icon_url=ctx.author.avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(name="freeze", hidden=True)
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
            embed.set_author(name="Admin Audit", icon_url=ctx.author.avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(name="thaw", hidden=True)
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
            embed.set_author(name="Admin Audit", icon_url=ctx.author.avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(name="reset", hidden=True)
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
            embed.set_author(name="Admin Audit", icon_url=ctx.author.avatar.url)
            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(embed=discord.Embed(description=f"❌ Error: {e}", color=discord.Color.red()))

    @adminbank.command(name="refund", hidden=True)
    @commands.is_owner()
    async def admin_bank_refund(self, ctx: Context, member: discord.Member, txid: str):
        """Refund a transaction."""

        try:
            #concept
            #await self.bot.database.refund_transaction(txid, f"Admin Audit - Refund by {ctx.author.name}")
            #await self.bot.database.validate_economy()

            embed = discord.Embed(
                description=f"✅ Successfully refunded transaction **{txid}** for user **{member.display_name}**.",
                color=discord.Color.green(),
            )
            embed.set_author(name="Admin Audit", icon_url=ctx.author.avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(name="mint", hidden=True)
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
            embed.set_author(name="Admin Audit", icon_url=ctx.author.avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
            )

    @adminbank.command(name="burn", hidden=True)
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
            embed.set_author(name="Admin Audit", icon_url=ctx.author.avatar.url)
            await ctx.send(embed=embed)
        except ValueError as e:
            await ctx.send(
                embed=discord.Embed(description=str(e), color=discord.Color.red())
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

    @commands.command(name="shh", hidden=True)
    async def shush(
        self, ctx: Context, member: discord.Member = None, *, input_str: str
    ):
        allowed_guilds = {
            1180709266538123345: self.is_whitelisted_wod,
            1336128367166095380: self.is_whitelisted_mistrust,
            1199083709735911465: self.is_whitelisted_private,
            1452021243669643324: self.is_whitelisted_clubhouse,
            1270962480742666311: self.is_whitelisted_tpne,
        }

        if ctx.guild.id not in allowed_guilds:
            return

        whitelist_check = allowed_guilds[ctx.guild.id]
        if not whitelist_check(ctx.author.id):
            return

        args = input_str.split()

        if len(args) > 1:
            try:
                member = await commands.MemberConverter().convert(ctx, args[0])
                role_identifier = " ".join(args[1:])
            except commands.BadArgument:
                role_identifier = input_str
                member = ctx.author
        else:
            role_identifier = input_str
            member = member or ctx.author

        role, error = await self.find_role(ctx, role_identifier)

        if error:
            await ctx.message.add_reaction("‼")
            await asyncio.sleep(1)
            await ctx.message.delete()
            return

        if role is None:
            await ctx.message.add_reaction("🚫")
            await asyncio.sleep(1)
            await ctx.message.delete()
            return

        if role.position >= ctx.me.top_role.position:
            error_embed = discord.Embed(
                description=f"🚫 I cannot manage the role '{role.name}' because it is higher or equal to my top role.",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        if role in member.roles:
            await member.remove_roles(role)
        else:
            await member.add_roles(role)

        await ctx.message.add_reaction(self.shh_emoji)
        await ctx.message.delete()

async def setup(bot) -> None:
    await bot.add_cog(Owner(bot))
    logger.debug("Owner cog initialized successfully")
