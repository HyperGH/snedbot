import logging
import typing as t

import asyncpg
import hikari
import lightbulb
import miru

import models
from etc import constants as const
from models import SnedBot
from models import SnedSlashContext
from models.checks import has_permissions
from utils import helpers
from utils.ratelimiter import BucketType
from utils.ratelimiter import RateLimiter

logger = logging.getLogger(__name__)

role_buttons = lightbulb.Plugin("Role-Buttons")

button_styles = {
    "Blurple": hikari.ButtonStyle.PRIMARY,
    "Grey": hikari.ButtonStyle.SECONDARY,
    "Green": hikari.ButtonStyle.SUCCESS,
    "Red": hikari.ButtonStyle.DANGER,
}
role_button_ratelimiter = RateLimiter(2, 1, BucketType.MEMBER, wait=False)


class RoleButton(miru.Button):
    def __init__(
        self,
        *,
        entry_id: int,
        emoji: t.Optional[hikari.Emoji],
        style: hikari.ButtonStyle,
        label: t.Optional[str] = None,
        role: hikari.SnowflakeishOr[hikari.Role],
    ):
        role_id = hikari.Snowflake(role)
        super().__init__(style=style, label=label, emoji=emoji, custom_id=f"RB:{entry_id}:{role_id}")


@role_buttons.listener(miru.ComponentInteractionCreateEvent, bind=True)
async def rolebutton_listener(plugin: lightbulb.Plugin, event: miru.ComponentInteractionCreateEvent) -> None:
    """Statelessly listen for rolebutton interactions"""

    if not event.interaction.custom_id.startswith("RB:"):
        return

    assert isinstance(plugin.app, SnedBot)

    entry_id = event.interaction.custom_id.split(":")[1]
    role_id = int(event.interaction.custom_id.split(":")[2])

    if not event.context.guild_id:
        return

    role = plugin.app.cache.get_role(role_id)

    if not role:
        embed = hikari.Embed(
            title="❌ Orphaned",
            description="The role this button was pointing to was deleted! Please notify an administrator!",
            color=0xFF0000,
        )
        await event.context.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    me = plugin.app.cache.get_member(event.context.guild_id, plugin.app.user_id)
    assert me is not None

    if not helpers.includes_permissions(lightbulb.utils.permissions_for(me), hikari.Permissions.MANAGE_ROLES):
        embed = hikari.Embed(
            title="❌ Missing Permissions",
            description="Bot does not have `Manage Roles` permissions! Contact an administrator!",
            color=0xFF0000,
        )
        await event.context.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    await role_button_ratelimiter.acquire(event.context)
    if role_button_ratelimiter.is_rate_limited(event.context):
        embed = hikari.Embed(
            title="❌ Slow Down!",
            description="You are clicking too fast!",
            color=0xFF0000,
        )
        await event.context.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    await event.context.defer(hikari.ResponseType.DEFERRED_MESSAGE_CREATE, flags=hikari.MessageFlag.EPHEMERAL)

    try:
        assert event.context.member is not None

        if role.id in event.context.member.role_ids:
            await event.context.member.remove_role(role, reason=f"Removed by role-button (ID: {entry_id})")
            embed = hikari.Embed(
                title="✅ Role removed",
                description=f"Removed role: {role.mention}",
                color=0x77B255,
            )
            await event.context.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

        else:
            await event.context.member.add_role(role, reason=f"Granted by role-button (ID: {entry_id})")
            embed = hikari.Embed(
                title="✅ Role added",
                description=f"Added role: {role.mention}",
                color=0x77B255,
            )
            embed.set_footer(text="If you would like it removed, click the button again!")
            await event.context.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

    except (hikari.ForbiddenError, hikari.HTTPError):
        embed = hikari.Embed(
            title="❌ Insufficient permissions",
            description="Failed adding role due to an issue with permissions and/or role hierarchy! Please contact an administrator!",
            color=0xFF0000,
        )
        await event.context.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@role_buttons.command
@lightbulb.command("rolebutton", "Commands relating to rolebuttons.")
@lightbulb.implements(lightbulb.SlashCommandGroup)
async def rolebutton(ctx: SnedSlashContext) -> None:
    pass


@rolebutton.child
@lightbulb.add_checks(has_permissions(hikari.Permissions.MANAGE_ROLES))
@lightbulb.command("list", "List all registered rolebuttons on this server.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def rolebutton_list(ctx: SnedSlashContext) -> None:

    assert ctx.guild_id is not None

    records = await ctx.app.db_cache.get(table="button_roles", guild_id=ctx.guild_id)

    if not records:
        embed = hikari.Embed(
            title="❌ Error: No role-buttons",
            description="There are no role-buttons for this server.",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    paginator = lightbulb.utils.StringPaginator(max_chars=500)
    for record in records:
        role = ctx.app.cache.get_role(record["role_id"])
        channel = ctx.app.cache.get_guild_channel(record["channel_id"])

        if role and channel:
            paginator.add_line(f"**#{record['entry_id']}** - {channel.mention} - {role.mention}")

        else:
            paginator.add_line(f"**#{record['entry_id']}** - C: {record['channel_id']} - R: {record['role_id']}")

    embeds = []
    for page in paginator.build_pages():
        embed = hikari.Embed(
            title="Rolebuttons on this server:",
            description=page,
            color=const.EMBED_BLUE,
        )
        embeds.append(embed)

    navigator = models.AuthorOnlyNavigator(ctx, pages=embeds)
    await navigator.send(ctx.interaction)


@rolebutton.child
@lightbulb.add_checks(has_permissions(hikari.Permissions.MANAGE_ROLES))
@lightbulb.option(
    "button_id",
    "The ID of the rolebutton to delete. You can get this via /rolebutton list",
    type=int,
)
@lightbulb.command("delete", "Delete a rolebutton.", pass_options=True)
@lightbulb.implements(lightbulb.SlashSubCommand)
async def rolebutton_del(ctx: SnedSlashContext, button_id: int) -> None:
    assert ctx.guild_id is not None

    records = await ctx.app.db_cache.get(table="button_roles", guild_id=ctx.guild_id, entry_id=button_id, limit=1)

    if not records:
        embed = hikari.Embed(
            title="❌ Not found",
            description="There is no rolebutton by that ID. Check your existing rolebuttons via `/rolebutton list`",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    try:
        message = await ctx.app.rest.fetch_message(records[0]["channel_id"], records[0]["msg_id"])
    except hikari.ForbiddenError:
        embed = hikari.Embed(
            title="❌ Insufficient permissions",
            description="The bot cannot see the channel where the button is supposed to be located, and thus cannot remove it!",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    except hikari.NotFoundError:
        pass
    else:  # Remove button if message still exists
        view = miru.View.from_message(message, timeout=None)

        for item in view.children:
            if item.custom_id == f"RB:{button_id}:{records[0]['role_id']}":
                view.remove_item(item)
        try:
            message = await message.edit(components=view.build())
        except hikari.ForbiddenError:
            embed = hikari.Embed(
                title="❌ Insufficient permissions",
                description="The bot cannot send messages in the channel where the button is located, and thus cannot remove it!",
                color=const.ERROR_COLOR,
            )
            await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
            return

    await ctx.app.db.execute(
        """DELETE FROM button_roles WHERE guild_id = $1 AND entry_id = $2""",
        ctx.guild_id,
        button_id,
    )
    await ctx.app.db_cache.refresh(table="button_roles", guild_id=ctx.guild_id, entry_id=button_id)

    embed = hikari.Embed(
        title="✅ Deleted!",
        description=f"Rolebutton was successfully deleted!",
        color=const.EMBED_GREEN,
    )
    await ctx.respond(embed=embed)

    try:
        message = await ctx.app.rest.fetch_message(records[0]["channel_id"], records[0]["msg_id"])
    except hikari.NotFoundError:
        pass
    else:  # Remove button if message still exists
        view = miru.View.from_message(message, timeout=None)

        for item in view.children:
            if item.custom_id == f"RB:{button_id}:{records[0]['role_id']}":
                view.remove_item(item)

        message = await message.edit(components=view.build())


@rolebutton.child
@lightbulb.add_checks(has_permissions(hikari.Permissions.MANAGE_ROLES))
@lightbulb.option("buttonstyle", "The style of the button.", choices=["Blurple", "Grey", "Red", "Green"])
@lightbulb.option("label", "The label that should appear on the button.", required=False)
@lightbulb.option("emoji", "The emoji that should appear in the button.", type=hikari.Emoji)
@lightbulb.option("role", "The role that should be handed out by the button.", type=hikari.Role)
@lightbulb.option(
    "message_link",
    "The link of a message that MUST be from the bot, the rolebutton will be attached here.",
)
@lightbulb.command(
    "add",
    "Add a new rolebutton.",
)
@lightbulb.implements(lightbulb.SlashSubCommand)
async def rolebutton_add(ctx: SnedSlashContext) -> None:

    assert ctx.guild_id is not None

    buttonstyle = ctx.options.buttonstyle or "Grey"

    message = await helpers.parse_message_link(ctx, ctx.options.message_link)
    if not message:
        return

    if message.author.id != ctx.app.user_id:
        embed = hikari.Embed(
            title="❌ Message not authored by bot",
            description="This message was not sent by the bot, and thus it cannot be edited to add the button.\n\n**Tip:** If you want to create a new message for the rolebutton with custom content, use the `/echo` or `/embed` command!",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    record = await ctx.app.db.fetchrow("""SELECT entry_id FROM button_roles ORDER BY entry_id DESC""")
    entry_id = record.get("entry_id") + 1 if record else 1
    emoji = hikari.Emoji.parse(ctx.options.emoji) if ctx.options.emoji else None
    button_style = button_styles[buttonstyle.capitalize()]

    button = RoleButton(
        entry_id=entry_id,
        role=ctx.options.role,
        emoji=emoji,
        label=ctx.options.label,
        style=button_style,
    )

    view = miru.View.from_message(message, timeout=None)

    try:
        view.add_item(button)
    except ValueError:
        embed = hikari.Embed(
            title="❌ Too many buttons",
            description="This message has too many buttons attached to it already, please choose a different message!",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    message = await message.edit(components=view.build())

    await ctx.app.db.execute(
        """
        INSERT INTO button_roles (entry_id, guild_id, channel_id, msg_id, emoji, buttonlabel, buttonstyle, role_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        entry_id,
        ctx.guild_id,
        message.channel_id,
        message.id,
        str(emoji),
        ctx.options.label,
        ctx.options.buttonstyle,
        ctx.options.role.id,
    )
    await ctx.app.db_cache.refresh(table="button_roles", guild_id=ctx.guild_id, entry_id=entry_id)

    channel = ctx.app.cache.get_guild_channel(message.channel_id)
    assert channel is not None

    embed = hikari.Embed(
        title="✅ Done!",
        description=f"A new rolebutton for role {ctx.options.role.mention} in channel `#{channel.name}` has been created!",
        color=const.EMBED_GREEN,
    )
    await ctx.respond(embed=embed)

    embed = hikari.Embed(
        title="❇️ Role-Button was added",
        description=f"A role-button for role {ctx.options.role.mention} has been created by {ctx.author.mention} in channel <#{channel.id}>.\n\n__Note:__ Anyone who can see this channel can now obtain this role!",
        color=const.EMBED_GREEN,
    )
    try:
        userlog = ctx.app.get_plugin("Logging")
        assert userlog is not None
        await userlog.d.actions.log("roles", embed, ctx.guild_id)
    except AttributeError:
        pass


def load(bot: SnedBot) -> None:
    bot.add_plugin(role_buttons)


def unload(bot: SnedBot) -> None:
    bot.remove_plugin(role_buttons)
