import discord
from discord.ext import commands
from discord.ext.commands import Context
from typing import Optional
import logging
import asyncio
from utils.cooldown import unified_cooldown
from utils.embeds import Embeds
from utils.guardrails import check_slash_guardrails

logger = logging.getLogger("discord.client")


class RolesPaginator(discord.ui.View):
    def __init__(self, user: discord.User, pages: list[list[str]]):
        super().__init__(timeout=60)
        self.user = user
        self.pages = pages
        self.current = 0

        self.prev_button.disabled = True

        if len(pages) == 1:
            self.next_button.disabled = True

    async def update_buttons(self):
        self.prev_button.disabled = self.current == 0
        self.next_button.disabled = self.current == len(self.pages) - 1

    @discord.ui.button(
        label="Previous",
        style=discord.ButtonStyle.grey,
        emoji="⬅️",
        custom_id="prev_page",
    )
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.user:
            return await interaction.response.send_message(
                "You can't control this pagination.", ephemeral=True
            )
        self.current -= 1
        await self.update_buttons()
        embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="Next", style=discord.ButtonStyle.grey, emoji="➡️", custom_id="next_page"
    )
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.user:
            return await interaction.response.send_message(
                "You can't control this pagination.", ephemeral=True
            )
        self.current += 1
        await self.update_buttons()
        embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def build_embed(self) -> discord.Embed:
        page = self.pages[self.current]
        embed = discord.Embed(
            title="Roles",
            description="\n".join(page) if page else "No roles to show.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Page {self.current+1}/{len(self.pages)}")
        return embed

    async def on_timeout(self):
        for btn in self.children:
            btn.disabled = True

        try:
            await self.message.edit(view=self)
        except Exception:
            pass


class InRolePaginator(discord.ui.View):
    def __init__(
        self,
        members: list[discord.Member],
        role: discord.Role,
        requester: discord.Member,
        per_page: int = 10,
    ):
        super().__init__(timeout=60)
        self.members = members
        self.role = role
        self.requester = requester
        self.per_page = per_page
        self.current_page = 0

        self.prev_btn: discord.ui.Button = self.children[0]
        self.next_btn: discord.ui.Button = self.children[1]
        self._update_buttons()

    def _update_buttons(self):
        total_pages = (len(self.members) + self.per_page - 1) // self.per_page
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page >= total_pages - 1

    def get_embed(self) -> discord.Embed:
        start = self.current_page * self.per_page
        end = start + self.per_page
        page = self.members[start:end]

        desc = "\n".join(f"• {m.mention}" for m in page)
        embed = discord.Embed(
            title=f"Members in “{self.role.name}”",
            description=desc,
            color=self.role.color or discord.Color.blurple(),
        )
        total_pages = max(1, (len(self.members) + self.per_page - 1) // self.per_page)
        embed.set_footer(
            text=f"Page {self.current_page+1}/{total_pages} • {len(self.members)} total"
        )
        return embed

    @discord.ui.button(label="⬅", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message(
                "This paginator isn’t yours!", ephemeral=True
            )

        self.current_page = max(self.current_page - 1, 0)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="➡", style=discord.ButtonStyle.secondary, emoji="➡️")
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message(
                "This paginator isn’t yours!", ephemeral=True
            )

        total_pages = (len(self.members) + self.per_page - 1) // self.per_page
        self.current_page = min(self.current_page + 1, total_pages - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


class RoleTools(commands.Cog, name="Roles"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.default_avatar_url = "https://cdn.discordapp.com/embed/avatars/1.png"
        self.forced_roles = {}

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_slash_guardrails(self, interaction)

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

    @commands.hybrid_group(
        name="role",
        aliases=["r"],
        description="Group for role management. Use subcommands or call directly to toggle a role on a member.",
        invoke_without_command=True,
    )
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def role(
        self, ctx: Context, member: Optional[discord.Member] = None, *, role_name: Optional[str] = None
    ):
        """Toggle one or more comma-separated roles on a member (base behavior when calling group directly)."""
        # If a subcommand was invoked, let it handle things and don't error about missing args
        if ctx.invoked_subcommand:
            return

        if member is None or role_name is None:
            return await ctx.reply(
                embed=discord.Embed(
                    description="🚫 Usage: `role <member> <role1, role2, ...>`", color=discord.Color.red()
                ),
                delete_after=5,
            )

        role_names = [r.strip() for r in role_name.split(",") if r.strip()]
        added = []
        removed = []
        failed = []

        for rn in role_names:
            role, error = await self.find_role(ctx, rn)
            if error:
                failed.append(f"{rn} ({error})")
                continue

            # Prevent acting on roles higher or equal to the command author (unless author is guild owner)
            if role.position >= ctx.author.top_role.position and ctx.author != ctx.guild.owner:
                failed.append(f"{role.name} (higher or equal to your top role)")
                continue

            # Prevent acting on roles higher or equal to the bot
            if role.position >= ctx.me.top_role.position:
                failed.append(f"{role.name} (higher or equal to my top role)")
                continue

            # Skip default or managed roles which cannot be assigned/removed
            if role == ctx.guild.default_role or role.managed:
                failed.append(f"{role.name} (cannot manage default or managed roles)")
                continue

            try:
                if role in member.roles:
                    await member.remove_roles(role, reason=f"Role toggled by {ctx.author}")
                    removed.append(role.name)
                else:
                    await member.add_roles(role, reason=f"Role toggled by {ctx.author}")
                    added.append(role.name)
            except discord.Forbidden:
                failed.append(f"{role.name} (no permission)")
            except discord.HTTPException:
                failed.append(f"{role.name} (http error)")

        desc_parts = []
        if added:
            desc_parts.append(f"✅ Added: {', '.join(added)}")
        if removed:
            desc_parts.append(f"✅ Removed: {', '.join(removed)}")
        if failed:
            desc_parts.append(f"⚠️ Failed: {', '.join(failed)}")

        if not desc_parts:
            return await ctx.send(
                embed=discord.Embed(description="No roles were processed.", color=discord.Color.red())
            )

        await ctx.send(embed=discord.Embed(description="\n".join(desc_parts)))

    @role.command(name="create", description="Creates a new role.")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def create_role(self, ctx: Context, *, role_name: str):
        try:
            guild = ctx.guild
            new_role = await guild.create_role(
                name=role_name, color=discord.Color.default()
            )

            embed = discord.Embed(
                description=f"✅ Created a new role: {new_role.mention}"
            )
            await ctx.send(embed=embed)

        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to create roles."
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while creating the role."
            )
            return await ctx.reply(embed=embed)

    @role.command(name="strip", description="Removes all roles from a member")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def strip_roles(self, ctx: commands.Context, member: discord.Member = None):
        try:
            member = member or ctx.author
            if member.top_role >= ctx.me.top_role:
                embed = discord.Embed(
                    description="🚫 I cannot strip roles from this member because their top role is higher or equal to mine.",
                    color=discord.Color.red(),
                )
                return await ctx.reply(embed=embed, delete_after=5)

            if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
                embed = discord.Embed(
                    description="🚫 You cannot strip roles from this member because their top role is higher or equal to yours.",
                    color=discord.Color.red(),
                )
                return await ctx.reply(embed=embed, delete_after=5)

            roles_to_remove = [
                role
                for role in member.roles
                if role != ctx.guild.default_role and not role.managed
            ]

            class ConfirmView(discord.ui.View):
                def __init__(self, author_id):
                    super().__init__(timeout=60)
                    self.author_id = author_id
                    self.value = None

                async def interaction_check(
                    self, interaction: discord.Interaction
                ) -> bool:
                    if interaction.user.id != self.author_id:
                        await interaction.response.send_message(
                            "You can't use this.", ephemeral=True
                        )
                        return False
                    return True

                @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
                async def confirm(
                    self, interaction: discord.Interaction, button: discord.ui.Button
                ):
                    if not await self.interaction_check(interaction):
                        return

                    self.value = True
                    self.stop()

                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                async def cancel(
                    self, interaction: discord.Interaction, button: discord.ui.Button
                ):
                    if not await self.interaction_check(interaction):
                        return

                    self.value = False
                    self.stop()

            confirm_view = ConfirmView(ctx.author.id)
            orig_message = await ctx.reply(
                embed=discord.Embed(
                    title="Confirm Role Strip",
                    description=f"Are you sure you want to remove {len(roles_to_remove)} roles from {member.mention}?",
                    color=discord.Color.red(),
                ),
                view=confirm_view,
            )
            await confirm_view.wait()
            if confirm_view.value:
                new_embed = discord.Embed(
                    description=f"✅ Stripping roles from {member.mention}...",
                    color=discord.Color.green(),
                )
                await orig_message.edit(embed=new_embed, view=None)
            else:
                new_embed = discord.Embed(
                    description="❌ Role strip cancelled.", color=discord.Color.red()
                )
                await orig_message.edit(embed=new_embed, view=None)

            if confirm_view.value is None:
                embed = discord.Embed(
                    description="🚫 Confirmation timed out. Role strip cancelled.",
                    color=discord.Color.red(),
                )
                return await ctx.reply(embed=embed, delete_after=5)

            if confirm_view.value is False:
                embed = discord.Embed(
                    description="❌ Role strip cancelled.", color=discord.Color.red()
                )
                return await ctx.reply(embed=embed, delete_after=5)

            await member.remove_roles(
                *roles_to_remove, reason=f"Roles stripped by {ctx.author}"
            )

            embed = discord.Embed(
                description=f"✅ Removed {len(roles_to_remove)} roles from {member.mention}"
            )
            await ctx.send(embed=embed)

        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to strip roles."
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while stripping roles."
            )
            return await ctx.reply(embed=embed)

    @role.command(name="rename", description="Rename a role.")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def edit_role_name(self, ctx: Context, role_name: str, *, new_name: str):
        """Changes the name of an existing role."""
        role, error = await self.find_role(ctx, role_name)
        if error:
            error_embed = discord.Embed(
                description=f"🚫 {error}", color=discord.Color.red()
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        try:
            await role.edit(name=new_name)
            embed = discord.Embed(
                description=f"✅ Renamed role `{role.name}` to `{new_name}`.",
                color=role.color,
            )
            await ctx.send(embed=embed)

        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to edit roles. "
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while editing the role."
            )
            return await ctx.reply(embed=embed)

    @role.command(
        name="color", description="Change a role color. Usage: role color <role> <#hex>"
    )
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def edit_role_color(self, ctx: Context, role_name: str, color: discord.Color):
        """Changes the color of an existing role."""
        role, error = await self.find_role(ctx, role_name)
        if error:
            error_embed = discord.Embed(
                description=f"🚫 {error}", color=discord.Color.red()
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        try:
            await role.edit(color=color)
            embed = discord.Embed(
                description=f"✅ Changed the color of `{role.name}` to `{str(color)}`.",
                color=color,
            )
            await ctx.send(embed=embed)

        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to edit roles. "
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while editing the role."
            )
            return await ctx.reply(embed=embed)

    @role.command(name="delete", description="Deletes an existing role.")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def delete_role(self, ctx: Context, *, role_name: str):
        role, error = await self.find_role(ctx, role_name)
        if error:
            error_embed = discord.Embed(
                description=f"🚫 {error}", color=discord.Color.red()
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        try:
            await role.delete()
            embed = discord.Embed(description=f"✅ Deleted the role: {role.name}")
            await ctx.send(embed=embed)

        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to delete roles."
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while deleting the role.\nIt may have already been deleted."
            )
            return await ctx.reply(embed=embed)

    @role.command(
        name="force", description="Forces a role on a user, reapplying it if removed."
    )
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def force_role(self, ctx: Context, member: discord.Member, *, role_name: str):
        """Forces a role on a user, reapplying it if removed."""
        role, error = await self.find_role(ctx, role_name)
        if error:
            error_embed = discord.Embed(
                description=f"🚫 {error}", color=discord.Color.red()
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        if role.position >= ctx.me.top_role.position:
            error_embed = discord.Embed(
                description=f"🚫 I cannot manage the role '{role.name}' because it is higher or equal to my top role.",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        if role.position >= ctx.author.top_role.position and ctx.author != ctx.guild.owner:
            error_embed = discord.Embed(
                description=f"🚫 You cannot force the role '{role.name}' because it is higher or equal to your top role.",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        if member == ctx.author:
            error_embed = discord.Embed(
                description=f"🚫 You cannot force a role on yourself.",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        if ctx.guild.id not in self.forced_roles:
            self.forced_roles[ctx.guild.id] = {}

        if member.id not in self.forced_roles[ctx.guild.id]:
            self.forced_roles[ctx.guild.id][member.id] = []

        if role.id not in self.forced_roles[ctx.guild.id][member.id]:
            self.forced_roles[ctx.guild.id][member.id].append(role.id)
            try:
                await member.add_roles(role, reason="Forced role")
                await ctx.send(f"✅ Forced role {role.name} on {member.name}.")
            except discord.Forbidden:
                embed = discord.Embed(
                    description="🚫 I do not have permission to manage roles. "
                )
                return await ctx.reply(embed=embed)
            except discord.HTTPException as e:
                embed = discord.Embed(
                    description=f"🚫 An error occurred while manage the users roles."
                )
                return await ctx.reply(embed=embed)
        else:
            await ctx.send(f"{role.name} is already a forced role for {member.name}.")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Re-apply forced roles if they are removed."""
        if before.roles == after.roles:
            return

        guild_id = after.guild.id
        member_id = after.id

        if (
            guild_id not in self.forced_roles
            or member_id not in self.forced_roles[guild_id]
        ):
            return

        forced_role_ids = self.forced_roles[guild_id][member_id]
        current_role_ids = [role.id for role in after.roles]

        for role_id in forced_role_ids:
            if role_id not in current_role_ids:
                role = after.guild.get_role(role_id)
                if role:
                    try:
                        await after.add_roles(role, reason="Forced role re-applied")
                        logger.info(
                            f"Re-applied forced role {role.name} to {after.name}"
                        )
                    except (discord.Forbidden, discord.HTTPException) as e:
                        logger.error(f"Failed to re-apply forced role: {e}")

    @role.command(name="unforce", description="Removes a forced role from a user.")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def unforce_role(
        self, ctx: Context, member: discord.Member, *, role_name: str
    ):
        """Removes a forced role from a user."""
        role, error = await self.find_role(ctx, role_name)
        if error:
            error_embed = discord.Embed(
                description=f"🚫 {error}", color=discord.Color.red()
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        if (
            ctx.guild.id in self.forced_roles
            and member.id in self.forced_roles[ctx.guild.id]
            and role.id in self.forced_roles[ctx.guild.id][member.id]
        ):
            self.forced_roles[ctx.guild.id][member.id].remove(role.id)
            try:
                await member.remove_roles(role, reason="Forced role removed")
                await ctx.send(
                    f"✅ Removed forced role {role.name} from {member.name}."
                )
            except discord.Forbidden:
                await ctx.send(
                    f"⚠️ Removed from forced roles list, but I don't have permission to remove the role."
                )
            except discord.HTTPException:
                await ctx.send(
                    f"⚠️ Removed from forced roles list, but failed to remove the role from the user."
                )
        else:
            await ctx.send(f"{role.name} is not a forced role for {member.name}.")

    @role.command(
        name="transfer",
        aliases=["moverole", "swaprole", "tr"],
        description="Transfers a user's roles to another account",
    )
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def transfer_role(
        self,
        ctx: Context,
        from_user: discord.Member = None,
        to_user: discord.Member = None,
    ):
        if not from_user or not to_user:
            embed = discord.Embed(
                description="🚫 Please specify both a source and a target user.",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, delete_after=5)

        if from_user == to_user:
            embed = discord.Embed(
                description="🚫 You cannot transfer roles from a user to themselves.",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, delete_after=5)

        roles_to_transfer = [
            role
            for role in from_user.roles
            if role != ctx.guild.default_role
            and role.position < ctx.me.top_role.position
        ]

        if not roles_to_transfer:
            embed = discord.Embed(
                description=f"⚠️ {from_user.display_name} has no roles that can be transferred."
            )
            await ctx.send(embed=embed)
            return

        try:
            for role in to_user.roles:
                if role.position < ctx.me.top_role.position:
                    try:
                        await to_user.remove_roles(role)
                    except discord.NotFound:
                        continue
                    except discord.Forbidden:
                        await ctx.send(
                            f"I do not have permission to remove the role: {role.name}"
                        )
                        continue

            await to_user.add_roles(*roles_to_transfer)
            await asyncio.sleep(5)
            await from_user.remove_roles(*roles_to_transfer)

            embed = discord.Embed(
                title="Roles Transferred",
                description=f"Transferred {len(roles_to_transfer)} roles from {from_user.display_name} to {to_user.display_name}.",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Roles Transferred",
                value=", ".join([role.name for role in roles_to_transfer]),
                inline=False,
            )
            embed.set_footer(
                text=f"Requested by {ctx.author.display_name}",
                icon_url=ctx.author.avatar.url,
            )

            await ctx.send(embed=embed)

        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to move roles between these users."
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while restoring roles"
            )
            return await ctx.reply(embed=embed)

    @role.command(
        name="restore", description="Restores previously stripped roles for a member"
    )
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def restore_roles(self, ctx: Context, member: discord.Member):
        """
        Restores previously stripped roles for a member if they exist in the log.
        """
        roles_to_restore = await self.bot.database.get_user_roles(member.id)

        if not roles_to_restore:
            embed = discord.Embed(
                description=f"🚫 No roles to restore for {member.name}.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed)
            return

        roles_to_add = [
            ctx.guild.get_role(role_id)
            for role_id in roles_to_restore
            if ctx.guild.get_role(role_id)
        ]

        if not roles_to_add:
            embed = discord.Embed(
                description=f"🚫 No valid roles to restore for {member.name}.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed)
            return

        try:
            await member.add_roles(
                *roles_to_add, reason=f"Roles restored by {ctx.author.name}"
            )

            await self.bot.database.remove_user_roles(member.id)

            for cache_attr in (
                "user_roles_cache",
                "role_restore_cache",
                "cached_user_roles",
            ):
                cache = getattr(self.bot, cache_attr, None)
                if isinstance(cache, dict):
                    cache.pop(member.id, None)

            db_cache = getattr(self.bot.database, "cache", None)
            if isinstance(db_cache, dict):
                db_cache.pop(member.id, None)

            embed = discord.Embed(
                description=f"Restored {len(roles_to_add)} roles to {member.mention}"
            )
            await ctx.send(embed=embed)

        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to add roles to this user."
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while restoring roles"
            )
            return await ctx.reply(embed=embed)

    @role.command(
        name="giveall",
        aliases=["ga"],
        description="Gives a role to all members in the server.",
    )
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def give_all_role(self, ctx: Context, *, role_name: str):
        role, error = await self.find_role(ctx, role_name)
        if error:
            error_embed = discord.Embed(
                description=f"🚫 {error}", color=discord.Color.red()
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        try:
            delay = 1
            for member in ctx.guild.members:
                if not member.bot and role not in member.roles:
                    while True:
                        try:
                            await member.add_roles(role)
                            break
                        except discord.HTTPException as e:
                            if e.status == 429:
                                await asyncio.sleep(delay)
                                delay *= 2
                            elif (
                                e.status == 404
                                or ctx.guild.get_member(member.id) is None
                            ):
                                break
                            else:
                                raise e
                    await asyncio.sleep(1)

            embed = discord.Embed(
                description=f"✅ Gave the role '{role.name}' to all members."
            )
            await ctx.send(embed=embed)

        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to manage roles. "
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while managing the users roles."
            )
            return await ctx.reply(embed=embed)

    @role.command(
        name="takeall",
        aliases=["ta"],
        description="Removes a role from all members in the server.",
    )
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def remove_all_role(self, ctx: Context, *, role_name: str):
        role, error = await self.find_role(ctx, role_name)
        if error:
            error_embed = discord.Embed(
                description=f"🚫 {error}", color=discord.Color.red()
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        try:
            delay = 1
            for member in ctx.guild.members:
                if not member.bot and role in member.roles:
                    while True:
                        try:
                            await member.remove_roles(role)
                            break
                        except discord.HTTPException as e:
                            if e.status == 429:
                                await asyncio.sleep(delay)
                                delay *= 2
                            elif (
                                e.status == 404
                                or ctx.guild.get_member(member.id) is None
                            ):
                                break
                            else:
                                raise e
                    await asyncio.sleep(1)

            embed = discord.Embed(
                description=f"✅ Removed the role '{role.name}' from all members."
            )
            await ctx.send(embed=embed)

        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to take roles. "
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while taking the roles."
            )
            return await ctx.reply(embed=embed)

    @commands.hybrid_command(
        name="roles",
        aliases=["lr", "listroles"],
        description="Lists all roles or the roles of a specific user.",
    )
    @unified_cooldown(10)
    async def list_roles(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ):
        if member:
            raw_roles = [r for r in member.roles if r.name != "@everyone"]
            title = f"{member.display_name}'s Roles"
        else:
            raw_roles = [r for r in ctx.guild.roles if r.name != "@everyone"]
            title = "Server Roles"

        sorted_roles = sorted(raw_roles, key=lambda r: r.position, reverse=True)
        mentions = [r.mention for r in sorted_roles]

        per_page = 10
        pages = [mentions[i : i + per_page] for i in range(0, len(mentions), per_page)]
        if not pages:
            pages = [[]]

        embed = discord.Embed(
            title=title,
            description="\n".join(pages[0]) or "No roles to show.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Page 1/{len(pages)}")

        if len(pages) == 1:
            return await ctx.send(embed=embed)

        paginator = RolesPaginator(ctx.author, pages)
        message = await ctx.send(embed=embed, view=paginator)
        paginator.message = message

    @commands.hybrid_command(
        name="staffroles",
        aliases=["lsr", "liststaffroles"],
        description="Lists all roles with staff permissions.",
    )
    @commands.has_permissions(administrator=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def list_staff_roles(self, ctx: Context) -> None:
        """Lists all roles in the server with staff permissions."""

        staff_permissions = [
            "administrator",
            "manage_guild",
            "manage_messages",
            "kick_members",
            "ban_members",
            "moderate_members",
        ]

        #guild = self.bot.fetch_guild(1270962480742666311)

        staff_roles = []
        for role in ctx.guild.roles:
            for perm_name, value in role.permissions:
                if perm_name in staff_permissions and value:
                    staff_roles.append(role)
                    break

        if not staff_roles:
            await ctx.send(
                embed=discord.Embed(
                    description="No roles with staff permissions found.",
                    color=discord.Color.red(),
                )
            )
            return

        role_names = "\n".join([f"{role.mention}" for role in staff_roles])

        embed = discord.Embed(
            title="Staff Roles",
            description=f"The following roles have staff permissions:\n\n{role_names}",
            color=ctx.author.top_role.color
            if ctx.author.top_role
            else discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar.url
        )

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="roleinfo",
        aliases=["ri"],
        description="Displays information about a specific role.",
    )
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def roleinfo(self, ctx: Context, *, role_name: str):
        role, error = await self.find_role(ctx, role_name)
        if error:
            embed = discord.Embed(description=f"🚫 {error}", color=discord.Color.red())
            return await ctx.reply(embed=embed, delete_after=5)

        try:
            role_color = role.color
            permissions = ", ".join(
                perm.replace("_", " ").title()
                for perm, value in role.permissions
                if value
            )
            permissions = permissions if permissions else "No permissions"

            embed = discord.Embed(
                title=f"Role Information - {role.name}", color=role_color
            )
            embed.add_field(name="Role ID", value=role.id, inline=True)
            embed.add_field(name="Color", value=str(role.color), inline=True)
            embed.add_field(name="Position", value=role.position, inline=True)
            embed.add_field(
                name="Mentionable",
                value="Yes" if role.mentionable else "No",
                inline=True,
            )
            embed.add_field(
                name="Managed", value="Yes" if role.managed else "No", inline=True
            )
            embed.add_field(
                name="Members with this role", value=len(role.members), inline=True
            )
            embed.add_field(
                name="Created On",
                value=role.created_at.strftime("%Y/%M/%D %I:%M:%S %p"),
                inline=True,
            )
            embed.add_field(name="Permissions", value=permissions, inline=False)
            embed.set_footer(
                text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar.url
            )

            await ctx.send(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to view role information."
            )
            return await ctx.reply(embed=embed)

    @commands.hybrid_command(
        name="inrole",
        aliases=["ir"],
        description="Lists all members with a specific role.",
    )
    @unified_cooldown(10)
    async def in_role(self, ctx: commands.Context, *, role_name: str):
        role, error = await self.find_role(ctx, role_name)
        if error:
            error_embed = discord.Embed(
                description=f"🚫 {error}", color=discord.Color.red()
            )
            return await ctx.reply(embed=error_embed, delete_after=5)

        members = sorted(role.members, key=lambda m: m.display_name)
        if not members:
            embed = discord.Embed(
                title=f"No members with role “{role.name}”", color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        view = InRolePaginator(members=members, role=role, requester=ctx.author)
        embed = view.get_embed()
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(
        name="rolebots",
        aliases=["rb"],
        description="Gives a role to all bots in the server.",
    )
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def give_all_bots_role(self, ctx: Context, *, role_name: str):
        role, error = await self.find_role(ctx, role_name)
        if error:
            error_embed = discord.Embed(
                description=f"🚫 {error}", color=discord.Color.red()
            )
            return await ctx.reply(embed=error_embed, delete_after=5)
        try:
            delay = 1
            for member in ctx.guild.members:
                if member.bot and role not in member.roles:
                    while True:
                        try:
                            await member.add_roles(role)
                            break
                        except discord.HTTPException as e:
                            if e.status == 429:
                                await asyncio.sleep(delay)
                                delay *= 2
                            elif e.status == 404:
                                break
                            else:
                                raise e
                    await asyncio.sleep(1)

            embed = discord.Embed(
                description=f"✅ Gave the role '{role.name}' to all bots."
            )
            await ctx.send(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to manage roles. "
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while managing the bots roles."
            )
            return await ctx.reply(embed=embed)

    @commands.hybrid_command(name='rolehumans', aliases=['rh'], description="Gives a role to all humans in the server.")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def give_all_humans_role(self, ctx: Context, *, role_name: str):
        role, error = await self.find_role(ctx, role_name)
        if error:
            error_embed = discord.Embed(
                description=f"🚫 {error}", color=discord.Color.red()
            )
            return await ctx.reply(embed=error_embed, delete_after=5)
        try:
            delay = 1
            for member in ctx.guild.members:
                if not member.bot and role not in member.roles:
                    while True:
                        try:
                            await member.add_roles(role)
                            break
                        except discord.HTTPException as e:
                            if e.status == 429:
                                await asyncio.sleep(delay)
                                delay *= 2
                            elif e.status == 404:
                                break
                            else:
                                raise e
                    await asyncio.sleep(1)

            human_count = sum(1 for m in role.members if not m.bot)
            embed = discord.Embed(
                description=f"✅ Gave the role '{role.name}' to all humans. ({human_count} members)"
            )
            await ctx.send(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                description="🚫 I do not have permission to manage roles. "
            )
            return await ctx.reply(embed=embed)
        except discord.HTTPException as e:
            embed = discord.Embed(
                description=f"🚫 An error occurred while managing the humans roles."
            )
            return await ctx.reply(embed=embed)

    @commands.hybrid_group(
        name="autorole",
        aliases=["ar"],
        invoke_without_command=True,
        description="Manage autoroles given to users when they join.",
    )
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def autorole(self, ctx: Context):
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
                lines.append(f"`{prefix}autorole {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}autorole {name}`{aliases}")

        description = "\n".join(lines) if lines else "No subcommands available."

        embed = discord.Embed(
            title="Autorole — Available Commands",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Use {prefix}autorole <subcommand> for details.")
        await ctx.reply(embed=embed, mention_author=False)

    @autorole.command(name="add", description="Add a role to autoroles.")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def autorole_add(self, ctx: Context, *, role_name: str):
        role, error = await self.find_role(ctx, role_name)
        if error:
            return await Embeds.error(ctx, f"🚫 {error}", delete_after=5, reply=True)

        if role.position >= ctx.me.top_role.position:
            return await Embeds.error(ctx, f"🚫 I cannot manage the role '{role.name}' because it is higher or equal to my top role.", delete_after=5, reply=True)

        try:
            await self.bot.database.add_auto_role(ctx.guild.id, role.id)
            await ctx.send(embed=discord.Embed(description=f"✅ Added autorole: {role.mention}"))
        except Exception as e:
            logger.error(f"Failed to add autorole: {e}")
            await Embeds.error(ctx, "🚫 Failed to add autorole.", reply=True)

    @autorole.command(name="remove", description="Remove a role from autoroles.")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def autorole_remove(self, ctx: Context, *, role_name: str):
        role, error = await self.find_role(ctx, role_name)
        if error:
            return await Embeds.error(ctx, f"🚫 {error}", delete_after=5, reply=True)

        try:
            await self.bot.database.remove_auto_role(ctx.guild.id, role.id)
            await ctx.send(embed=discord.Embed(description=f"✅ Removed autorole: {role.mention}"))
        except Exception as e:
            logger.error(f"Failed to remove autorole: {e}")
            await Embeds.error(ctx, "🚫 Failed to remove autorole.", reply=True)

    @autorole.command(name="list", description="List configured autoroles for this server.")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def autorole_list(self, ctx: Context):
        try:
            ids = await self.bot.database.get_auto_roles(ctx.guild.id)
            roles = [ctx.guild.get_role(rid) for rid in ids]
            roles = [r for r in roles if r]
            if not roles:
                return await Embeds.error(ctx, "No autoroles configured.", reply=False)

            desc = "\n".join(r.mention for r in roles)
            await ctx.send(embed=discord.Embed(title="Autoroles", description=desc, color=discord.Color.blurple()))
        except Exception as e:
            logger.error(f"Failed to list autoroles: {e}")
            await Embeds.error(ctx, "🚫 Failed to retrieve autoroles.", reply=True)

    @autorole.command(name="clear", description="Clear all autoroles for this server.")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    @discord.app_commands.default_permissions(manage_roles=True)
    @unified_cooldown(10)
    async def autorole_clear(self, ctx: Context):
        try:
            await self.bot.database.clear_auto_roles(ctx.guild.id)
            await ctx.send(embed=discord.Embed(description="✅ Cleared autoroles."))
        except Exception as e:
            logger.error(f"Failed to clear autoroles: {e}")
            await Embeds.error(ctx, "🚫 Failed to clear autoroles.", reply=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # Assign configured autoroles to new members (multiple roles supported)
        try:
            if member.bot:
                return

            guild = member.guild
            ids = await self.bot.database.get_auto_roles(guild.id)
            if not ids:
                return

            me = guild.me
            roles_to_add = []
            for rid in ids:
                role = guild.get_role(rid)
                if not role:
                    continue
                if role.managed:
                    continue
                if role.position >= me.top_role.position:
                    continue
                if role in member.roles:
                    continue
                roles_to_add.append(role)

            if not roles_to_add:
                return

            try:
                await member.add_roles(*roles_to_add, reason="Autorole")
                logger.info(f"Applied autoroles to {member} in {guild.name}: {[r.name for r in roles_to_add]}")
            except discord.Forbidden:
                logger.warning(f"Missing permissions to apply autoroles in guild {guild.id}")
            except discord.HTTPException as e:
                logger.error(f"HTTP error applying autoroles: {e}")

        except Exception as e:
            logger.exception(f"Unexpected error in autorole on_member_join: {e}")


async def setup(bot) -> None:
    await bot.add_cog(RoleTools(bot))
    logger.debug("Role cog initialized successfully")
