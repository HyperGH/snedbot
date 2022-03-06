import datetime
import enum
import logging
import re
import typing as t

import hikari
import lightbulb

import models
from etc import constants as const
from models import SnedSlashContext
from models.bot import SnedBot
from models.context import SnedContext, SnedUserContext
from models.db_user import User
from models.errors import BotRoleHierarchyError, RoleHierarchyError
from models.timer import Timer
from utils import helpers

logger = logging.getLogger(__name__)

mod = lightbulb.Plugin("Moderation", include_datastore=True)
mod.d.actions = lightbulb.utils.DataStore()

MAX_TIMEOUT_SECONDS = 2246400  # Duration of segments to break timeouts up to


default_mod_settings = {
    "dm_users_on_punish": True,
    "is_ephemeral": False,
}


class ActionType(enum.Enum):
    """Enum containing all possible moderation actions."""

    BAN = "Ban"
    SOFTBAN = "Softban"
    TEMPBAN = "Tempban"
    KICK = "Kick"
    TIMEOUT = "Timeout"
    WARN = "Warn"


@lightbulb.Check  # type: ignore
async def is_above_target(ctx: SnedContext) -> bool:
    """Check if the targeted user is above the bot's top role or not."""

    if not hasattr(ctx.options, "user"):
        return True

    if not ctx.guild_id:
        return True

    me = ctx.app.cache.get_member(ctx.guild_id, ctx.app.user_id)
    assert me is not None

    if isinstance(ctx.options.user, hikari.Member):
        member = ctx.options.user
    else:
        member = ctx.app.cache.get_member(ctx.guild_id, ctx.options.user)

    if not member:
        return True

    if helpers.is_above(me, member):
        return True

    raise BotRoleHierarchyError("Target user top role is higher than bot.")


@lightbulb.Check  # type: ignore
async def is_invoker_above_target(ctx: SnedContext) -> bool:
    """Check if the targeted user is above the invoker's top role or not."""

    if not hasattr(ctx.options, "user"):
        return True

    if not ctx.member or not ctx.guild_id:
        return True

    guild = ctx.get_guild()
    assert guild is not None

    if ctx.member.id == guild.owner_id:
        return True

    if isinstance(ctx.options.user, hikari.Member):
        member = ctx.options.user
    else:
        member = ctx.app.cache.get_member(ctx.guild_id, ctx.options.user)

    if not member:
        return True

    if helpers.is_above(ctx.member, member):
        return True

    raise RoleHierarchyError("Target user top role is higher than author.")


async def get_settings(guild_id: int) -> t.Dict[str, bool]:
    assert isinstance(mod.app, SnedBot)

    records = await mod.app.db_cache.get(table="mod_config", guild_id=guild_id)
    if records:
        mod_settings = {
            "dm_users_on_punish": records[0].get("dm_users_on_punish") or True,
            "is_ephemeral": records[0].get("is_ephemeral") or False,
        }
    else:
        mod_settings = default_mod_settings

    return mod_settings


mod.d.actions.get_settings = get_settings


async def pre_mod_actions(
    guild: hikari.SnowflakeishOr[hikari.Guild],
    target: t.Union[hikari.Member, hikari.User],
    action_type: ActionType,
    reason: t.Optional[str] = None,
) -> None:
    """
    Actions that need to be executed before a moderation action takes place.
    """
    helpers.format_reason(reason, max_length=1500)
    guild_id = hikari.Snowflake(guild)
    settings = await get_settings(guild_id)
    types_conj = {
        ActionType.WARN: "warned in",
        ActionType.TIMEOUT: "timed out in",
        ActionType.KICK: "kicked from",
        ActionType.BAN: "banned from",
        ActionType.SOFTBAN: "soft-banned from",
        ActionType.TEMPBAN: "temp-banned from",
    }

    if settings["dm_users_on_punish"] == True and isinstance(target, hikari.Member):
        gateway_guild = mod.app.cache.get_guild(guild_id)
        assert isinstance(gateway_guild, hikari.GatewayGuild)
        guild_name = gateway_guild.name if gateway_guild else "Unknown server"
        embed = hikari.Embed(
            title=f"❗ You have been {types_conj[action_type]} **{guild_name}**",
            description=f"You have been {types_conj[action_type]} **{guild_name}**.\n**Reason:** ```{reason}```",
            color=const.ERROR_COLOR,
        )
        try:
            await target.send(embed=embed)
        except (hikari.ForbiddenError, hikari.HTTPError):
            pass


async def post_mod_actions(
    guild: hikari.SnowflakeishOr[hikari.Guild],
    target: t.Union[hikari.Member, hikari.User],
    action_type: ActionType,
    reason: t.Optional[str] = None,
) -> None:
    """
    Actions that need to be executed after a moderation action took place.
    """
    pass


async def get_notes(
    user: hikari.SnowflakeishOr[hikari.PartialUser], guild: hikari.SnowflakeishOr[hikari.Guild]
) -> t.Optional[t.List[str]]:
    """Returns a list of strings corresponding to a user's journal."""

    assert isinstance(mod.app, SnedBot)
    user_id = hikari.Snowflake(user)
    guild_id = hikari.Snowflake(guild)

    db_user = await mod.app.global_config.get_user(user_id, guild_id)
    return db_user.notes


mod.d.actions.get_notes = get_notes


async def flag_user(
    user: hikari.SnowflakeishOr[hikari.PartialUser],
    guild: hikari.SnowflakeishOr[hikari.Guild],
    message: hikari.Message,
    reason: str,
) -> None:
    """Flag a message from a user, creating a log entry in the designated log channel."""

    user_id = hikari.Snowflake(user)
    guild_id = hikari.Snowflake(guild)

    reason = helpers.format_reason(reason, max_length=1500)

    user = (
        user
        if isinstance(user, hikari.User)
        else (mod.app.cache.get_member(guild_id, user_id) or (await mod.app.rest.fetch_user(user_id)))
    )
    content = helpers.format_reason(message.content, max_length=2000) if message.content else "No content found."

    embed = hikari.Embed(
        title="❗🚩 Message flagged",
        description=f"**{user}** `({user.id})` was flagged by auto-moderator for suspicious behaviour.\n**Reason:**```{reason}```\n**Content:** ```{content}```\n\n[Jump to message!]({message.make_link(guild_id)})",
        color=const.ERROR_COLOR,
    )
    userlog = mod.app.get_plugin("Logging")
    assert userlog is not None
    await userlog.d.actions.log("flags", embed, guild_id)


mod.d.actions.flag_user = flag_user


async def add_note(
    user: hikari.SnowflakeishOr[hikari.PartialUser], guild: hikari.SnowflakeishOr[hikari.Guild], note: str
) -> None:
    """Add a new journal entry to this user."""

    assert isinstance(mod.app, SnedBot)

    user_id = hikari.Snowflake(user)
    guild_id = hikari.Snowflake(guild)

    note = helpers.format_reason(note, max_length=256)

    db_user = await mod.app.global_config.get_user(user_id, guild_id)

    notes = db_user.notes if db_user.notes else []
    notes.append(f"{helpers.format_dt(helpers.utcnow(), style='d')}: {note}")
    db_user.notes = notes

    await mod.app.global_config.update_user(db_user)


mod.d.actions.add_note = add_note


async def clear_notes(
    user: hikari.SnowflakeishOr[hikari.PartialUser], guild: hikari.SnowflakeishOr[hikari.Guild]
) -> None:
    """Clear all notes a user has."""

    assert isinstance(mod.app, SnedBot)

    user_id = hikari.Snowflake(user)
    guild_id = hikari.Snowflake(guild)

    db_user = await mod.app.global_config.get_user(user_id, guild_id)
    db_user.notes = []
    await mod.app.global_config.update_user(db_user)


mod.d.actions.clear_notes = clear_notes


async def warn(member: hikari.Member, moderator: hikari.Member, reason: t.Optional[str] = None) -> hikari.Embed:
    """Warn a user, incrementing their warn counter, and logging the event if it is set up.

    Parameters
    ----------
    member : hikari.Member
        The member to be warned.
    moderator : hikari.Member
        The moderator who warned the member.
    reason : t.Optional[str], optional
        The reason for this action, by default None

    Returns
    -------
    hikari.Embed
        The response to show to the invoker.
    """

    assert isinstance(mod.app, SnedBot)

    db_user = await mod.app.global_config.get_user(member.id, member.guild_id)
    db_user.warns += 1
    await mod.app.global_config.update_user(db_user)
    reason = helpers.format_reason(reason, max_length=1000)

    embed = hikari.Embed(
        title="⚠️ Warning issued",
        description=f"**{member}** has been warned by **{moderator}**.\n**Reason:** ```{reason}```",
        color=const.WARN_COLOR,
    )
    log_embed = hikari.Embed(
        title="⚠️ Warning issued",
        description=f"**{member}** has been warned by **{moderator}**.\n**Warns:** {db_user.warns}\n**Reason:** ```{reason}```",
        color=const.WARN_COLOR,
    )
    await pre_mod_actions(member.guild_id, member, ActionType.WARN, reason)

    userlog = mod.app.get_plugin("Logging")
    assert userlog is not None

    await userlog.d.actions.log("warn", log_embed, member.guild_id)
    await post_mod_actions(member.guild_id, member, ActionType.WARN, reason)
    await add_note(member, member.guild_id, f"⚠️ **Warned by {moderator}:** {reason}")
    return embed


mod.d.actions.warn = warn


@mod.listener(models.TimerCompleteEvent)
async def timeout_extend(event: models.TimerCompleteEvent) -> None:
    """
    Extend timeouts longer than 28 days
    """

    assert isinstance(event.app, SnedBot)

    timer: Timer = event.timer

    if timer.event != "timeout_extend":
        return

    member = event.app.cache.get_member(timer.guild_id, timer.user_id)
    assert timer.notes is not None
    expiry = int(timer.notes)

    if member:
        me = mod.app.cache.get_member(timer.guild_id, event.app.user_id)
        assert me is not None

        if not helpers.can_harm(me, member, hikari.Permissions.MODERATE_MEMBERS):
            return

        if expiry - helpers.utcnow().timestamp() > MAX_TIMEOUT_SECONDS:

            await event.app.scheduler.create_timer(
                helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
                "timeout_extend",
                timer.guild_id,
                member,
                notes=timer.notes,
            )
            await member.edit(
                communication_disabled_until=helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
                reason="Automatic timeout extension applied.",
            )

        else:
            timeout_for = helpers.utcnow() + datetime.timedelta(seconds=expiry - round(helpers.utcnow().timestamp()))
            await member.edit(communication_disabled_until=timeout_for, reason="Automatic timeout extension applied.")

    else:
        db_user = await event.app.global_config.get_user(timer.user_id, timer.guild_id)
        if not db_user.flags:
            db_user.flags = {}

        if "timeout_on_join" not in db_user.flags.keys():
            db_user.flags["timeout_on_join"] = expiry
            await event.app.global_config.update_user(db_user)


@mod.listener(hikari.MemberCreateEvent)
async def member_create(event: hikari.MemberCreateEvent):
    """
    Reapply timeout if member left between two cycles
    """

    assert isinstance(event.app, SnedBot)

    me = mod.app.cache.get_member(event.guild_id, event.app.user_id)
    assert me is not None

    if not helpers.can_harm(me, event.member, hikari.Permissions.MODERATE_MEMBERS):
        return

    db_user: User = await event.app.global_config.get_user(event.member.id, event.guild_id)

    if not db_user.flags or "timeout_on_join" not in db_user.flags.keys():
        return

    expiry = db_user.flags["timeout_on_join"]

    if expiry - helpers.utcnow().timestamp() < 0:
        # If this is in the past already
        return

    if expiry - helpers.utcnow().timestamp() > MAX_TIMEOUT_SECONDS:
        await event.app.scheduler.create_timer(
            helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
            "timeout_extend",
            event.member.guild_id,
            event.member,
            notes=str(expiry),
        )
        await event.member.edit(
            communication_disabled_until=helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
            reason="Automatic timeout extension applied.",
        )

    else:
        await event.member.edit(
            communication_disabled_until=expiry,
            reason="Automatic timeout extension applied.",
        )


@mod.listener(hikari.MemberUpdateEvent)
async def member_update(event: hikari.MemberUpdateEvent):
    """
    Remove all extensions if a user's timeout was removed
    """

    assert isinstance(event.app, SnedBot)

    if not event.old_member:
        return

    if event.old_member.communication_disabled_until() == event.member.communication_disabled_until():
        return

    if event.member.communication_disabled_until() is None:
        records = await event.app.pool.fetch(
            """SELECT * FROM timers WHERE guild_id = $1 AND user_id = $2 AND event = $3""",
            event.guild_id,
            event.member.id,
            "timeout_extend",
        )

        if not records:
            return

        for record in records:
            await event.app.scheduler.cancel_timer(record.get("id"), event.guild_id)


async def timeout(
    member: hikari.Member, moderator: hikari.Member, duration: datetime.datetime, reason: t.Optional[str] = None
) -> hikari.Embed:
    """
    Times out a member for the specified duration, converts duration from string.
    Returns the mute duration as datetime.
    """

    assert isinstance(mod.app, SnedBot)

    raw_reason = helpers.format_reason(reason, max_length=1500)
    reason = helpers.format_reason(reason, moderator, max_length=512)

    me = mod.app.cache.get_member(member.guild_id, mod.app.user_id)
    assert me is not None
    # Raise error if cannot harm user
    helpers.can_harm(me, member, hikari.Permissions.MODERATE_MEMBERS, raise_error=True)

    await pre_mod_actions(member.guild_id, member, ActionType.TIMEOUT, reason=raw_reason)
    if duration > helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS):
        await mod.app.scheduler.create_timer(
            helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
            "timeout_extend",
            member.guild_id,
            member,
            notes=str(round(duration.timestamp())),
        )
        await member.edit(
            communication_disabled_until=helpers.utcnow() + datetime.timedelta(seconds=MAX_TIMEOUT_SECONDS),
            reason=reason,
        )

    else:
        await member.edit(communication_disabled_until=duration, reason=reason)

    await post_mod_actions(member.guild_id, member, ActionType.TIMEOUT, reason=raw_reason)

    embed = hikari.Embed(
        title="🔇 " + "User timed out",
        description=f"**{member}** has been timed out until {helpers.format_dt(duration)}.\n**Reason:** ```{raw_reason}```",
        color=const.ERROR_COLOR,
    )
    return embed


mod.d.actions.timeout = timeout


async def remove_timeout(member: hikari.Member, moderator: hikari.Member, reason: t.Optional[str] = None) -> None:
    """
    Removes a timeout from a user with the specified reason.
    """

    reason = helpers.format_reason(reason, moderator)

    await member.edit(communication_disabled_until=None, reason=reason)


mod.d.actions.remove_timeout = remove_timeout


async def ban(
    user: t.Union[hikari.User, hikari.Member],
    moderator: hikari.Member,
    duration: t.Optional[datetime.datetime] = None,
    *,
    soft: bool = False,
    days_to_delete: int = 0,
    reason: t.Optional[str] = None,
) -> hikari.Embed:
    """Ban a user from a guild.

    Parameters
    ----------
    user : Union[hikari.User, hikari.Member]
        The user that needs to be banned.
    guild_id : Snowflake
        The guild this ban is taking place.
    moderator : hikari.Member
        The moderator to log the ban under.
    duration : Optional[str], optional
        If specified, the duration of the ban, by default None
    soft : bool, optional
        If True, the ban is a softban, by default False
    days_to_delete : int, optional
        The days of message history to delete, by default 1
    reason : Optional[str], optional
        The reason for the ban, by default hikari.UNDEFINED

    Returns
    -------
    hikari.Embed
        The response embed to display to the user. May include any
        potential input errors.

    Raises
    ------
    RuntimeError
        Both soft & tempban were specified.
    """

    assert isinstance(mod.app, SnedBot)

    reason = reason or "No reason provided."

    if duration and soft:
        raise RuntimeError("Ban type cannot be soft when a duration is specified.")

    me = mod.app.cache.get_member(moderator.guild_id, mod.app.user_id)
    assert me is not None

    perms = lightbulb.utils.permissions_for(me)

    if not helpers.includes_permissions(perms, hikari.Permissions.BAN_MEMBERS):
        raise lightbulb.BotMissingRequiredPermission(perms=hikari.Permissions.BAN_MEMBERS)

    if isinstance(user, hikari.Member) and not helpers.is_above(me, user):
        raise RoleHierarchyError

    if duration:
        reason = f"[TEMPBAN] Banned until: {duration} (UTC)  |  {reason}"

    elif soft:
        reason = f"[SOFTBAN] {reason}"

    raw_reason = reason
    reason = helpers.format_reason(reason, moderator, max_length=512)

    try:
        await pre_mod_actions(moderator.guild_id, user, ActionType.BAN, reason=raw_reason)
        await mod.app.rest.ban_user(moderator.guild_id, user.id, delete_message_days=days_to_delete, reason=reason)
        embed = hikari.Embed(
            title="🔨 User banned",
            description=f"**{user}** has been banned.\n**Reason:** ```{raw_reason}```",
            color=const.ERROR_COLOR,
        )

        if soft:
            await mod.app.rest.unban_user(moderator.guild_id, user.id, reason="Automatic unban by softban.")

        elif duration:
            await mod.app.scheduler.create_timer(expires=duration, event="tempban", guild=moderator.guild_id, user=user)

        await post_mod_actions(moderator.guild_id, user, ActionType.BAN, reason=raw_reason)
        return embed

    except (hikari.ForbiddenError, hikari.HTTPError):
        embed = hikari.Embed(
            title="❌ Ban failed",
            description="This could be due to a configuration or network error. Please try again later.",
            color=const.ERROR_COLOR,
        )
        return embed


mod.d.actions.ban = ban


@mod.listener(models.TimerCompleteEvent)
async def tempban_expire(event: models.TimerCompleteEvent) -> None:
    """Handle tempban timer expiry"""
    timer = event.timer

    # Ensure the guild still exists
    guild = mod.app.cache.get_guild(timer.guild_id)

    if not guild:
        return

    try:
        await guild.unban(timer.user_id, reason="User unbanned: Tempban expired.")
    except:
        return


async def unban(user: hikari.User, moderator: hikari.Member, reason: t.Optional[str] = None) -> hikari.Embed:
    """Unban a user from a guild.

    Parameters
    ----------
    user : hikari.User
        The user to be unbanned.
    moderator : hikari.Member
        The moderator who is unbanning this user.
    reason : t.Optional[str], optional
        The reason for the unban, by default None

    Returns
    -------
    hikari.Embed
        The response to show to the invoker.

    Raises
    ------
    lightbulb.BotMissingRequiredPermission
        Application is missing permissions to BAN_MEMBERS.
    """

    assert isinstance(mod.app, SnedBot)

    me = mod.app.cache.get_member(moderator.guild_id, mod.app.user_id)
    assert me is not None

    perms = lightbulb.utils.permissions_for(me)

    raw_reason = reason
    reason = helpers.format_reason(reason, moderator, max_length=512)

    if not helpers.includes_permissions(perms, hikari.Permissions.BAN_MEMBERS):
        raise lightbulb.BotMissingRequiredPermission(perms=hikari.Permissions.BAN_MEMBERS)

    try:
        await mod.app.rest.unban_user(moderator.guild_id, user.id, reason=reason)
        embed = hikari.Embed(
            title="🔨 User unbanned",
            description=f"**{user}** has been unbanned.\n**Reason:** ```{raw_reason}```",
            color=const.EMBED_GREEN,
        )
        return embed
    except (hikari.HTTPError, hikari.ForbiddenError, hikari.NotFoundError) as e:
        if isinstance(e, hikari.NotFoundError):
            embed = hikari.Embed(
                title="❌ Unban failed",
                description="This user does not appear to be banned!",
                color=const.ERROR_COLOR,
            )
        else:
            embed = hikari.Embed(
                title="❌ Unban failed",
                description="This could be due to a configuration or network error. Please try again later.",
                color=const.ERROR_COLOR,
            )
        return embed


mod.d.actions.unban = unban


async def kick(
    member: hikari.Member,
    moderator: hikari.Member,
    *,
    reason: t.Optional[str] = None,
) -> hikari.Embed:
    """[summary]

    Parameters
    ----------
    member : hikari.Member
        The member that needs to be kicked.
    moderator : hikari.Member
        The moderator to log the kick under.
    reason : Optional[str], optional
        The reason for the kick, by default None

    Returns
    -------
    hikari.Embed
        The response embed to display to the user. May include any
        potential input errors.
    """

    assert isinstance(mod.app, SnedBot)

    raw_reason = reason or "No reason provided."
    reason = helpers.format_reason(reason, moderator, max_length=512)

    me = mod.app.cache.get_member(member.guild_id, mod.app.user_id)
    assert me is not None
    # Raise error if cannot harm user
    helpers.can_harm(me, member, hikari.Permissions.MODERATE_MEMBERS, raise_error=True)

    try:
        await pre_mod_actions(member.guild_id, member, ActionType.KICK, reason=raw_reason)
        await mod.app.rest.kick_user(member.guild_id, member, reason=reason)
        embed = hikari.Embed(
            title="🚪👈 User kicked",
            description=f"**{member}** has been kicked.\n**Reason:** ```{raw_reason}```",
            color=const.ERROR_COLOR,
        )
        await post_mod_actions(member.guild_id, member, ActionType.KICK, reason=raw_reason)
        return embed

    except (hikari.ForbiddenError, hikari.HTTPError):
        embed = hikari.Embed(
            title="❌ Kick failed",
            description="This could be due to a configuration or network error. Please try again later.",
            color=const.ERROR_COLOR,
        )
        return embed


mod.d.actions.kick = kick


@mod.command()
@lightbulb.option("user", "The user to show information about.", type=hikari.User, required=True)
@lightbulb.command("whois", "Show user information about the specified user.")
@lightbulb.implements(lightbulb.SlashCommand)
async def whois(ctx: SnedSlashContext) -> None:
    embed = await helpers.get_userinfo(ctx, ctx.options.user)
    await ctx.mod_respond(embed=embed)


@mod.command()
@lightbulb.command("Show Userinfo", "Show user information about the target user.")
@lightbulb.implements(lightbulb.UserCommand)
async def whois_user_command(ctx: SnedUserContext) -> None:
    embed = await helpers.get_userinfo(ctx, ctx.options.target)
    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


@mod.command()
@lightbulb.add_cooldown(20, 1, lightbulb.ChannelBucket)
@lightbulb.add_checks(
    lightbulb.bot_has_guild_permissions(hikari.Permissions.MANAGE_MESSAGES, hikari.Permissions.READ_MESSAGE_HISTORY)
)
@lightbulb.option("user", "Only delete messages authored by this user.", type=hikari.User, required=False)
@lightbulb.option("regex", "Only delete messages that match with the regular expression.", required=False)
@lightbulb.option("embeds", "Only delete messages that contain embeds.", type=bool, required=False)
@lightbulb.option("links", "Only delete messages that contain links.", type=bool, required=False)
@lightbulb.option("invites", "Only delete messages that contain Discord invites.", type=bool, required=False)
@lightbulb.option("attachments", "Only delete messages that contain files & images.", type=bool, required=False)
@lightbulb.option("onlytext", "Only delete messages that exclusively contain text.", type=bool, required=False)
@lightbulb.option("notext", "Only delete messages that do not contain text.", type=bool, required=False)
@lightbulb.option("endswith", "Only delete messages that end with the specified text.", required=False)
@lightbulb.option("startswith", "Only delete messages that start with the specified text.", required=False)
@lightbulb.option("count", "The amount of messages to delete.", type=int, min_value=1, max_value=100)
@lightbulb.command("purge", "Purge multiple messages in this channel.")
@lightbulb.implements(lightbulb.SlashCommand)
async def purge(ctx: SnedSlashContext) -> None:

    channel = ctx.get_channel()
    assert isinstance(channel, hikari.TextableGuildChannel)

    predicates = [
        # Ignore deferred typing indicator so it doesn't get deleted lmfao
        lambda message: not (hikari.MessageFlag.LOADING & message.flags)
    ]

    if ctx.options.regex:
        try:
            regex = re.compile(ctx.options.regex)
        except re.error as error:
            embed = hikari.Embed(
                title="❌ Invalid regex passed",
                description=f"Failed parsing regex: ```{str(error)}```",
                color=const.ERROR_COLOR,
            )
            await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

            assert ctx.invoked is not None and ctx.invoked.cooldown_manager is not None
            return await ctx.invoked.cooldown_manager.reset_cooldown(ctx)
        else:
            predicates.append(lambda message, regex=regex: regex.match(message.content) if message.content else False)

    if ctx.options.startswith:
        predicates.append(
            lambda message: message.content.startswith(ctx.options.startswith) if message.content else False
        )

    if ctx.options.endswith:
        predicates.append(lambda message: message.content.endswith(ctx.options.endswith) if message.content else False)

    if ctx.options.notext:
        predicates.append(lambda message: not message.content)

    if ctx.options.onlytext:
        predicates.append(lambda message: message.content and not message.attachments and not message.embeds)

    if ctx.options.attachments:
        predicates.append(lambda message: bool(message.attachments))

    if ctx.options.invites:
        predicates.append(
            lambda message: helpers.is_invite(message.content, fullmatch=False) if message.content else False
        )

    if ctx.options.links:
        predicates.append(
            lambda message: helpers.is_url(message.content, fullmatch=False) if message.content else False
        )

    if ctx.options.embeds:
        predicates.append(lambda message: bool(message.embeds))

    if ctx.options.user:
        predicates.append(lambda message: message.author.id == ctx.options.user.id)

    await ctx.mod_respond(hikari.ResponseType.DEFERRED_MESSAGE_CREATE)

    messages = (
        await ctx.app.rest.fetch_messages(channel)
        .take_until(lambda m: (helpers.utcnow() - datetime.timedelta(days=14)) > m.created_at)
        .filter(*predicates)
        .limit(ctx.options.count)
    )

    if messages:
        try:
            await ctx.app.rest.delete_messages(channel, messages)
            embed = hikari.Embed(
                title="🗑️ Messages purged",
                description=f"**{len(messages)}** messages have been deleted.",
                color=const.EMBED_GREEN,
            )

        except hikari.BulkDeleteError as error:
            embed = hikari.Embed(
                title="🗑️ Messages purged",
                description=f"Only **{len(error.messages_deleted)}/{len(messages)}** messages have been deleted due to an error.",
                color=const.WARN_COLOR,
            )
            raise error
    else:
        embed = hikari.Embed(
            title="🗑️ Not found",
            description=f"No messages matched the specified criteria from the past two weeks!",
            color=const.ERROR_COLOR,
        )

    await ctx.mod_respond(embed=embed)


@mod.command()
@lightbulb.command("journal", "Access and manage the moderation journal.")
@lightbulb.implements(lightbulb.SlashCommandGroup)
async def journal(ctx: SnedSlashContext) -> None:
    pass


@journal.child()  # type: ignore
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.VIEW_AUDIT_LOG))
@lightbulb.option("user", "The user to retrieve the journal for.", type=hikari.User)
@lightbulb.command("get", "Retrieve the journal for the specified user.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def journal_get(ctx: SnedSlashContext) -> None:

    assert ctx.guild_id is not None
    notes = await get_notes(ctx.options.user.id, ctx.guild_id)
    paginator = lightbulb.utils.StringPaginator(max_chars=1500)

    if notes:
        notes_fmt = []
        for i, note in enumerate(notes):
            notes_fmt.append(f"`#{i}` {note}")

        for note in notes_fmt:
            paginator.add_line(note)

        embeds = []
        for page in paginator.build_pages():
            embed = hikari.Embed(
                title="📒 " + "Journal entries for this user:",
                description=page,
                color=const.EMBED_BLUE,
            )
            embeds.append(embed)

        navigator = models.AuthorOnlyNavigator(ctx, pages=embeds)

        ephemeral = (await get_settings(ctx.guild_id))["is_ephemeral"]
        await navigator.send(ctx.interaction, ephemeral=ephemeral)

    else:
        embed = hikari.Embed(
            title="📒 Journal entries for this user:",
            description=f"There are no journal entries for this user yet. Any moderation-actions will leave an entry here, or you can set one manually with `/journal add {ctx.options.user}` ",
            color=const.EMBED_BLUE,
        )
        await ctx.mod_respond(embed=embed)


@journal.child()  # type: ignore
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.VIEW_AUDIT_LOG))
@lightbulb.option("note", "The journal note to add.")
@lightbulb.option("user", "The user to add a journal entry for.", type=hikari.User)
@lightbulb.command("add", "Add a new journal entry for the specified user.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def journal_add(ctx: SnedSlashContext) -> None:

    assert ctx.guild_id is not None

    await add_note(ctx.options.user.id, ctx.guild_id, f"💬 **Note by {ctx.author}:** {ctx.options.note}")
    embed = hikari.Embed(
        title="✅ Journal entry added!",
        description=f"Added a new journal entry to user **{ctx.options.user}**. You can view this user's journal via the command `/journal get {ctx.options.user}`.",
        color=const.EMBED_GREEN,
    )
    await ctx.mod_respond(embed=embed)


@mod.command()
@lightbulb.add_checks(is_invoker_above_target, lightbulb.has_guild_permissions(hikari.Permissions.VIEW_AUDIT_LOG))
@lightbulb.option("reason", "The reason for this warn", required=False)
@lightbulb.option("user", "The user to be warned.", type=hikari.Member)
@lightbulb.command("warn", "Warn a user. This gets added to their journal and their warn counter is incremented.")
@lightbulb.implements(lightbulb.SlashCommand)
async def warn_cmd(ctx: SnedSlashContext) -> None:
    helpers.is_member(ctx.options.user)
    assert ctx.member is not None
    embed = await warn(ctx.options.user, ctx.member, reason=ctx.options.reason)
    await ctx.mod_respond(embed=embed)


@mod.command()
@lightbulb.command("warns", "Manage warnings.")
@lightbulb.implements(lightbulb.SlashCommandGroup)
async def warns(ctx: SnedSlashContext) -> None:
    pass


@warns.child()  # type: ignore
@lightbulb.option("user", "The user to show the warning count for.", type=hikari.Member)
@lightbulb.command("list", "List the current warning count for a user.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def warns_list(ctx: SnedSlashContext) -> None:
    helpers.is_member(ctx.options.user)
    assert ctx.guild_id is not None

    db_user: User = await ctx.app.global_config.get_user(ctx.options.user.id, ctx.guild_id)
    warns = db_user.warns
    embed = hikari.Embed(
        title=f"{ctx.options.user}'s warnings",
        description=f"**Warnings:** `{warns}`",
        color=const.WARN_COLOR,
    )
    embed.set_thumbnail(ctx.options.user.display_avatar_url)
    await ctx.mod_respond(embed=embed)


@warns.child()  # type: ignore
@lightbulb.add_checks(is_invoker_above_target, lightbulb.has_guild_permissions(hikari.Permissions.VIEW_AUDIT_LOG))
@lightbulb.option("reason", "The reason for clearing this user's warns.", required=False)
@lightbulb.option("user", "The user to show the warning count for.", type=hikari.Member)
@lightbulb.command("clear", "Clear warnings for the specified user.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def warns_clear(ctx: SnedSlashContext) -> None:
    helpers.is_member(ctx.options.user)

    assert ctx.guild_id is not None

    db_user: User = await ctx.app.global_config.get_user(ctx.options.user.id, ctx.guild_id)
    db_user.warns = 0
    await ctx.app.global_config.update_user(db_user)

    reason = helpers.format_reason(ctx.options.reason)

    embed = hikari.Embed(
        title="✅ Warnings cleared",
        description=f"**{ctx.options.user}**'s warnings have been cleared.\n**Reason:** ```{reason}```",
        color=const.EMBED_GREEN,
    )
    log_embed = hikari.Embed(
        title="⚠️ Warnings cleared.",
        description=f"{ctx.options.user.mention}'s warnings have been cleared by {ctx.author.mention}.\n**Reason:** ```{reason}```",
        color=const.EMBED_GREEN,
    )

    await add_note(ctx.options.user, ctx.guild_id, f"⚠️ **Warnings cleared by {ctx.author}:** {reason}")
    userlog = mod.app.get_plugin("Logging")
    assert userlog is not None
    await userlog.d.actions.log("warn", log_embed, ctx.guild_id)
    await ctx.mod_respond(embed=embed)


def load(bot: SnedBot) -> None:
    bot.add_plugin(mod)


def unload(bot: SnedBot) -> None:
    bot.remove_plugin(mod)


@mod.command()
@lightbulb.add_checks(
    lightbulb.bot_has_guild_permissions(hikari.Permissions.MODERATE_MEMBERS),
    lightbulb.has_guild_permissions(hikari.Permissions.MODERATE_MEMBERS),
    is_above_target,
    is_invoker_above_target,
)
@lightbulb.option("reason", "The reason for timing out this user.", required=False)
@lightbulb.option(
    "duration", "The duration to time the user out for. Example: '10 minutes', '2022-03-01', 'tomorrow 20:00'"
)
@lightbulb.option("user", "The user to time out.", type=hikari.Member)
@lightbulb.command("timeout", "Timeout a user, supports durations longer than 28 days.")
@lightbulb.implements(lightbulb.SlashCommand)
async def timeout_cmd(ctx: SnedSlashContext) -> None:
    helpers.is_member(ctx.options.user)
    member: hikari.Member = ctx.options.user
    reason: str = helpers.format_reason(ctx.options.reason, max_length=1024)
    assert ctx.member is not None

    if member.communication_disabled_until() is not None:
        embed = hikari.Embed(
            title="❌ User already timed out",
            description="User is already timed out. Use `/timeouts remove` to remove it.",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    try:
        duration: datetime.datetime = await ctx.app.scheduler.convert_time(
            ctx.options.duration, user=ctx.user, future_time=True
        )
    except ValueError:
        embed = hikari.Embed(
            title="❌ Invalid data entered",
            description="Your entered timeformat is invalid.",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    await ctx.mod_respond(hikari.ResponseType.DEFERRED_MESSAGE_CREATE)

    await timeout(member, ctx.member, duration, reason)

    embed = hikari.Embed(
        title="🔇 " + "User timed out",
        description=f"**{member}** has been timed out until {helpers.format_dt(duration)}.\n**Reason:** ```{reason}```",
        color=const.EMBED_GREEN,
    )
    await ctx.mod_respond(embed=embed)


@mod.command()
@lightbulb.command("timeouts", "Manage timeouts.")
@lightbulb.implements(lightbulb.SlashCommandGroup)
async def timeouts(ctx: SnedSlashContext) -> None:
    pass


@timeouts.child()  # type: ignore
@lightbulb.add_checks(
    lightbulb.bot_has_guild_permissions(hikari.Permissions.MODERATE_MEMBERS),
    lightbulb.has_guild_permissions(hikari.Permissions.MODERATE_MEMBERS),
    is_above_target,
    is_invoker_above_target,
)
@lightbulb.option("reason", "The reason for timing out this user.", required=False)
@lightbulb.option("user", "The user to time out.", type=hikari.Member)
@lightbulb.command("remove", "Remove timeout from a user.")
@lightbulb.implements(lightbulb.SlashSubCommand)
async def timeouts_remove_cmd(ctx: SnedSlashContext) -> None:
    helpers.is_member(ctx.options.user)
    member: hikari.Member = ctx.options.user
    reason: str = helpers.format_reason(ctx.options.reason, max_length=1024)

    assert ctx.member is not None

    if member.communication_disabled_until() is None:
        embed = hikari.Embed(
            title="❌ User not timed out",
            description="This user is not timed out.",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    await ctx.mod_respond(hikari.ResponseType.DEFERRED_MESSAGE_CREATE)
    await remove_timeout(member, ctx.member, reason)

    embed = hikari.Embed(
        title="🔉 " + "Timeout removed",
        description=f"**{member}**'s timeout was removed.\n**Reason:** ```{reason}```",
        color=const.EMBED_GREEN,
    )
    await ctx.mod_respond(embed=embed)


@mod.command()
@lightbulb.add_checks(
    lightbulb.bot_has_guild_permissions(hikari.Permissions.BAN_MEMBERS),
    lightbulb.has_guild_permissions(hikari.Permissions.BAN_MEMBERS),
    is_above_target,
    is_invoker_above_target,
)
@lightbulb.option(
    "days_to_delete",
    "The number of days of messages to delete. If not set, defaults to 0.",
    choices=["0", "1", "2", "3", "4", "5", "6", "7"],
    required=False,
    default=0,
)
@lightbulb.option(
    "duration",
    "If specified, how long the ban should last. Example: '10 minutes', '2022-03-01', 'tomorrow 20:00'",
    required=False,
)
@lightbulb.option("reason", "The reason why this ban was performed", required=False)
@lightbulb.option("user", "The user to be banned", type=hikari.User)
@lightbulb.command("ban", "Bans a user from the server. Optionally specify a duration to make this a tempban.")
@lightbulb.implements(lightbulb.SlashCommand)
async def ban_cmd(ctx: SnedSlashContext) -> None:

    assert ctx.member is not None

    try:
        duration: datetime.datetime = await ctx.app.scheduler.convert_time(
            ctx.options.duration, user=ctx.user, future_time=True
        )
    except ValueError:
        embed = hikari.Embed(
            title="❌ Invalid data entered",
            description="Your entered timeformat is invalid.",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    await ctx.mod_respond(hikari.ResponseType.DEFERRED_MESSAGE_CREATE)

    embed = await ban(
        ctx.options.user,
        ctx.member,
        duration=duration,
        days_to_delete=int(ctx.options.days_to_delete) or 0,
        reason=ctx.options.reason,
    )
    await ctx.mod_respond(embed=embed)


@mod.command()
@lightbulb.add_checks(
    lightbulb.bot_has_guild_permissions(hikari.Permissions.BAN_MEMBERS),
    lightbulb.has_guild_permissions(hikari.Permissions.BAN_MEMBERS),
    is_above_target,
    is_invoker_above_target,
)
@lightbulb.option(
    "days_to_delete",
    "The number of days of messages to delete. If not set, defaults to 0.",
    choices=["0", "1", "2", "3", "4", "5", "6", "7"],
    required=False,
    default=0,
)
@lightbulb.option("reason", "The reason why this ban was performed", required=False)
@lightbulb.option("user", "The user to be banned", type=hikari.User)
@lightbulb.command("softban", "Bans a user from the server. Optionally specify a duration to make this a tempban.")
@lightbulb.implements(lightbulb.SlashCommand)
async def softban_cmd(ctx: SnedSlashContext) -> None:

    assert ctx.member is not None

    await ctx.respond(hikari.ResponseType.DEFERRED_MESSAGE_CREATE)
    embed = await ban(
        ctx.options.user,
        ctx.member,
        soft=True,
        days_to_delete=int(ctx.options.days_to_delete) or 0,
        reason=ctx.options.reason,
    )
    await ctx.mod_respond(embed=embed)


@mod.command()
@lightbulb.add_checks(
    lightbulb.bot_has_guild_permissions(hikari.Permissions.BAN_MEMBERS),
    lightbulb.has_guild_permissions(hikari.Permissions.BAN_MEMBERS),
    is_above_target,
    is_invoker_above_target,
)
@lightbulb.option("reason", "The reason why this ban was performed", required=False)
@lightbulb.option("user", "The user to be banned", type=hikari.User)
@lightbulb.command("unban", "Unban a user who was previously banned.")
@lightbulb.implements(lightbulb.SlashCommand)
async def unban_cmd(ctx: SnedSlashContext) -> None:

    assert ctx.member is not None

    await ctx.mod_respond(hikari.ResponseType.DEFERRED_MESSAGE_CREATE)
    embed = await unban(ctx.options.user, ctx.member, reason=ctx.options.reason)
    await ctx.mod_respond(embed=embed)


@mod.command()
@lightbulb.add_checks(
    lightbulb.bot_has_guild_permissions(hikari.Permissions.KICK_MEMBERS),
    lightbulb.has_guild_permissions(hikari.Permissions.KICK_MEMBERS),
    is_above_target,
    is_invoker_above_target,
)
@lightbulb.option("reason", "The reason why this kick was performed.", required=False)
@lightbulb.option("user", "The user to be banned", type=hikari.Member)
@lightbulb.command("kick", "Kick a user from this server.")
@lightbulb.implements(lightbulb.SlashCommand)
async def kick_cmd(ctx: SnedSlashContext) -> None:

    helpers.is_member(ctx.options.user)
    assert ctx.member is not None

    await ctx.mod_respond(hikari.ResponseType.DEFERRED_MESSAGE_CREATE)
    embed = await kick(ctx.options.user, ctx.member, reason=ctx.options.reason)
    await ctx.mod_respond(embed=embed)


@mod.command()
@lightbulb.add_cooldown(60.0, 1, bucket=lightbulb.GuildBucket)
@lightbulb.add_checks(
    lightbulb.bot_has_guild_permissions(hikari.Permissions.BAN_MEMBERS),
    lightbulb.has_guild_permissions(hikari.Permissions.BAN_MEMBERS),
)
@lightbulb.option(
    "show",
    "Only perform this as a dry-run and only show users that would have been banned. Defaults to False.",
    type=bool,
    default=False,
    required=False,
)
@lightbulb.option("reason", "Reason to ban all matched users with.", required=False)
@lightbulb.option("regex", "A regular expression to match usernames against. Uses Python regex spec.", required=False)
@lightbulb.option(
    "no-avatar", "Only match users without an avatar. Defaults to False.", type=bool, default=False, required=False
)
@lightbulb.option(
    "no-roles", "Only match users without a role. Defaults to False.", type=bool, default=False, required=False
)
@lightbulb.option(
    "created", "Only match users that signed up to Discord x minutes before.", type=int, min_value=1, required=False
)
@lightbulb.option(
    "joined", "Only match users that joined this server x minutes before.", type=int, min_value=1, required=False
)
@lightbulb.option("joined-before", "Only match users that joined before this user.", type=hikari.Member, required=False)
@lightbulb.option("joined-after", "Only match users that joined after this user.", type=hikari.Member, required=False)
@lightbulb.command("massban", "Ban a large number of users based on a set of criteria. Useful for handling raids.")
@lightbulb.implements(lightbulb.SlashCommand)
async def massban(ctx: SnedSlashContext) -> None:

    if ctx.options["joined-before"]:
        helpers.is_member(ctx.options["joined-before"])
    if ctx.options["joined-after"]:
        helpers.is_member(ctx.options["joined-after"])

    predicates = [
        lambda member: not member.is_bot,
        lambda member: member.id != ctx.author.id,
        lambda member: member.discriminator != "0000",  # Deleted users
    ]

    guild = ctx.get_guild()
    assert guild is not None

    def is_above_member(member: hikari.Member, me=guild.get_member(ctx.app.user_id)) -> bool:
        # Check if the bot's role is above the member's or not to reduce invalid requests.
        assert me is not None
        return helpers.is_above(me, member)

    predicates.append(is_above_member)

    if ctx.options.regex:
        try:
            regex = re.compile(ctx.options.regex)
        except re.error as error:
            embed = hikari.Embed(
                title="❌ Invalid regex passed",
                description=f"Failed parsing regex: ```{str(error)}```",
                color=const.ERROR_COLOR,
            )
            await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
            assert ctx.invoked is not None and ctx.invoked.cooldown_manager is not None
            await ctx.invoked.cooldown_manager.reset_cooldown(ctx)
            return
        else:
            predicates.append(lambda member, regex=regex: regex.match(member.username))

    await ctx.mod_respond(hikari.ResponseType.DEFERRED_MESSAGE_CREATE)

    # Ensure the specified guild is explicitly chunked
    await ctx.app.request_guild_members(guild, include_presences=False)

    members = list(guild.get_members().values())

    if ctx.options["no-avatar"]:
        predicates.append(lambda member: member.avatar_url is None)
    if ctx.options["no-roles"]:
        predicates.append(lambda member: len(member.role_ids) <= 1)

    now = helpers.utcnow()

    if ctx.options.created:

        def created(member: hikari.User, offset=now - datetime.timedelta(minutes=ctx.options.created)) -> bool:
            return member.created_at > offset

        predicates.append(created)

    if ctx.options.joined:

        def joined(member: hikari.User, offset=now - datetime.timedelta(minutes=ctx.options.joined)) -> bool:
            if not isinstance(member, hikari.Member):
                return True
            else:
                return member.joined_at and member.joined_at > offset

        predicates.append(joined)

    if ctx.options["joined-after"]:

        def joined_after(member: hikari.Member, joined_after=ctx.options["joined-after"]) -> bool:
            return member.joined_at and joined_after.joined_at and member.joined_at > joined_after.joined_at

        predicates.append(joined_after)

    if ctx.options["joined-before"]:

        def joined_before(member: hikari.Member, joined_before=ctx.options["joined-before"]) -> bool:
            return member.joined_at and joined_before.joined_at and member.joined_at < joined_before.joined_at

        predicates.append(joined_before)

    to_ban = [member for member in members if all(predicate(member) for predicate in predicates)]

    if len(to_ban) == 0:
        embed = hikari.Embed(
            title="❌ No members match criteria",
            description=f"No members found that match all criteria.",
            color=const.ERROR_COLOR,
        )
        await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)
        return

    content = [f"Sned Massban Session: {guild.name}   |  Matched members against criteria: {len(to_ban)}\n{now}\n"]

    for member in to_ban:
        content.append(f"{member} ({member.id})  |  Joined: {member.joined_at}  |  Created: {member.created_at}")

    content = "\n".join(content)
    file = hikari.Bytes(content.encode("utf-8"), "members_to_ban.txt")

    if ctx.options.show == True:
        await ctx.mod_respond(attachment=file)
        return

    reason = ctx.options.reason if ctx.options.reason is not None else "No reason provided."
    helpers.format_reason(reason, ctx.member, max_length=512)

    embed = hikari.Embed(
        title="⚠️ Confirm Massban",
        description=f"You are about to ban **{len(to_ban)}** users. Are you sure you want to do this? Please review the attached list above for a full list of matched users. The user journals will not be updated.",
        color=const.WARN_COLOR,
    )
    confirm_embed = hikari.Embed(
        title="Starting Massban...",
        description="This could take some time...",
        color=const.WARN_COLOR,
    )
    cancel_embed = hikari.Embed(
        title="Massban interrupted",
        description="Massban session was terminated prematurely. No users were banned.",
        color=const.ERROR_COLOR,
    )

    is_ephemeral = (await get_settings(guild.id))["is_ephemeral"]
    flags = hikari.MessageFlag.EPHEMERAL if is_ephemeral else hikari.MessageFlag.NONE
    confirmed = await ctx.confirm(
        embed=embed,
        flags=flags,
        cancel_payload={"embed": cancel_embed, "flags": flags, "components": []},
        confirm_payload={"embed": confirm_embed, "flags": flags, "components": []},
        attachment=file,
    )
    if not confirmed:
        return

    userlog = ctx.app.get_plugin("Logging")
    assert userlog is not None
    await userlog.d.actions.freeze_logging(guild.id)

    count = 0

    for member in to_ban:
        try:
            await guild.ban(member, reason=reason)
        except (hikari.HTTPError, hikari.ForbiddenError):
            pass
        else:
            count += 1

    log_embed = hikari.Embed(
        title="🔨 Smartban concluded",
        description=f"Banned **{count}/{len(to_ban)}** users.\n**Moderator:** `{ctx.author} ({ctx.author.id if ctx.author else '0'})`\n**Reason:** ```{reason}```",
        color=const.ERROR_COLOR,
    )
    file = hikari.Bytes(content.encode("utf-8"), "members_banned.txt")
    await userlog.d.actions.log("ban", log_embed, ctx.guild_id, file=file, bypass=True)

    embed = hikari.Embed(
        title="✅ Smartban finished",
        description=f"Banned **{count}/{len(to_ban)}** users.",
        color=const.EMBED_GREEN,
    )
    await ctx.mod_respond(embed=embed)

    await userlog.d.actions.unfreeze_logging(ctx.guild_id)
