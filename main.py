@@ -1,40 +1,34 @@
# main.py
"""
Full production-ready Discord bot:
- Based on your Pastefy base + all requested feature additions
- Timetrack, rmute/runmute with auto-unmute + DM embed, rmute usage tracking
- Leaderboards: !tlb and !tdm
- Cache system: !rcache (deleted messages + attachments + reply info)
- Purge logging: logs content, attachments, reply info
- Staff pings: !rping / !hsping (pings members individually)
- RDM: opt-out from bot DMs
- Channel / role / webhook event logging
- Flask keep-alive server (daemon thread)
- All tasks started in on_ready()
- Uses role/channel IDs from your Pastefy
"""
# Full-featured Discord moderation + timetrack bot
# Requirements: discord.py, pytz, Flask (optional keep-alive)
# Ensure DISCORD_TOKEN env var is set and bot has intents+permissions:
# manage_roles, manage_messages, view_audit_log (optional), read_message_history, embed_links, attach_files, send_messages

import os
import json
import threading
import asyncio
import datetime
import pytz
import io
import tempfile
import traceback
from typing import Dict, Any, Optional, List

import discord
from discord.ext import commands, tasks
from flask import Flask
import threading

# ----------------------------
# CONFIG
# ----------------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Please set DISCORD_TOKEN environment variable.")
    raise RuntimeError("Please set DISCORD_TOKEN environment variable")

PREFIX = "!"
INTENTS = discord.Intents.all()
INTENTS.message_content = True  # message content intent must be enabled in dev portal
bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS, help_command=None)

# Role & Channel IDs from Pastefy (preserved)
@@ -47,96 +41,105 @@
DANGEROUS_LOG_USERS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

DATA_FILE = "bot_data.json"

# Ensure path exists
# initialize file if missing
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({
            "users": {},
            "mutes": {},
            "rmute_usage": {},
            "cached_messages": {},
            "rdm_users": []
        }, f, indent=4)

# Use an asyncio lock for JSON writes to reduce races
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"users": {}, "mutes": {}, "rmute_usage": {}, "cached_messages": {}, "rdm_users": []}, f, indent=4)

# lock for safe writes
file_lock = asyncio.Lock()

# ----------------------------
# UTILS
# UTILITIES
# ----------------------------
def utc_now() -> datetime.datetime:
    return datetime.datetime.now(pytz.utc)

def get_timestamp() -> int:
    return int(utc_now().timestamp())

def format_duration(seconds: int) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

def safe_load_data() -> Dict[str, Any]:
    # synchronous small read; we use the file_lock around writes
    try:
        with open(DATA_FILE, "r") as f:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "mutes": {}, "rmute_usage": {}, "cached_messages": {}, "rdm_users": []}

async def safe_save_data(data: Dict[str, Any]):
    async with file_lock:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w") as f:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, DATA_FILE)

def format_duration(seconds: int) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

async def send_dm_safe(user: discord.User, embed: discord.Embed):
    data = safe_load_data()
    if user.id in data.get("rdm_users", []):
async def safe_send_channel(channel: Optional[discord.TextChannel], embed: discord.Embed = None, content: str = None, files: List[discord.File] = None):
    if channel is None:
        return False
    try:
        await user.send(embed=embed)
        await channel.send(content=content, embed=embed, files=files)
        return True
    except Exception:
        return False

async def try_get_audit_deleter(guild: discord.Guild, message_id: int) -> Optional[int]:
    # Best-effort: returns the moderator user id who deleted message (if available)
async def download_attachment_to_file(attachment: discord.Attachment) -> Optional[discord.File]:
    """
    Downloads an attachment from a message delete/purge event and returns a discord.File
    so the bot can re-upload it to the log channel (this will display images/videos inline).
    """
    try:
        data = await attachment.read()
        bio = io.BytesIO(data)
        bio.seek(0)
        # Ensure filename exists
        filename = attachment.filename or "file"
        dfile = discord.File(bio, filename=filename)
        return dfile
    except Exception:
        return None

async def try_get_audit_deleter(guild: discord.Guild, time_window_seconds: int = 15) -> Optional[discord.User]:
    """
    Best-effort attempt to find the actor who deleted messages recently in a guild audit log.
    Not guaranteed (Discord audit logs are not guaranteed to show every deletion, especially single deletes).
    We'll search recent message_delete entries.
    """
    try:
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.message_delete):
            # entry.extra may contain channel, count, but not message id for bulk deletes
            # Audit logs are not precise for single message deletion; this is a best-effort attempt.
            # We'll return the user who created the entry if timestamp is recent.
            ts_age = (utc_now() - entry.created_at.replace(tzinfo=pytz.utc)).total_seconds()
            if ts_age < 10:  # within 10 seconds
                return entry.user.id
            if ts_age <= time_window_seconds:
                return entry.user
    except Exception:
        return None
        pass
    return None

# ----------------------------
# FLASK KEEP-ALIVE
# FLASK keep-alive (daemon thread)
# ----------------------------
app = Flask("keepalive")

@app.route("/")
def home():
    return "Bot alive"
    return "Bot is running"

def run_flask():
    # Keep Flask quiet
    # If PORT env provided, use it
    port = int(os.environ.get("PORT", 8080))
    try:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
        app.run(host="0.0.0.0", port=port)
    except Exception:
        pass

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

# ----------------------------
# TIMETRACK TASK
# TIMETRACK LOOP
# ----------------------------
@tasks.loop(seconds=60)
async def timetrack_loop():
@@ -146,57 +149,55 @@ async def timetrack_loop():
        for member in guild.members:
            if member.bot:
                continue
            # Only track members who have at least one RCACHE role
            # Only track members with RCACHE roles:
            try:
                if not any(role.id in RCACHE_ROLES for role in member.roles):
                    continue
            except Exception:
                continue
            uid = str(member.id)
            udata = data.get("users", {}).get(uid, {})
            # initialize
            udata = data.get("users", {}).get(uid)
            if not udata:
                udata = {
                    "last_online": None,
                    "first_seen": now_ts,
                    "last_online": now_ts,
                    "last_message": None,
                    "last_edit": None,
                    "total_online_seconds": 0,
                    "online_start": None
                }
            # Online start handling
            # if online, ensure online_start set
            if member.status != discord.Status.offline:
                if udata.get("online_start") is None:
                if not udata.get("online_start"):
                    udata["online_start"] = now_ts
            else:
                # just went offline
                if udata.get("online_start") is not None:
                # went offline: finish session if online_start set
                if udata.get("online_start"):
                    session = now_ts - int(udata["online_start"])
                    udata["total_online_seconds"] = udata.get("total_online_seconds", 0) + session
                    udata["online_start"] = None
            udata["last_online"] = now_ts
            # last_message & last_edit are updated by event handlers
            # preserve last_message/last_edit set by events
            data.setdefault("users", {})[uid] = udata
    await safe_save_data(data)

# ----------------------------
# AUTO-UNMUTE TASK (checks JSON mutes & unmute)
# AUTO-UNMUTE LOOP
# ----------------------------
@tasks.loop(seconds=10)
async def auto_unmute_loop():
    data = safe_load_data()
    now_ts = get_timestamp()
    updated = False
    for uid, mute_record in list(data.get("mutes", {}).items()):
    changed = False
    for uid, mdata in list(data.get("mutes", {}).items()):
        try:
            if now_ts >= mute_record.get("unmute_time", 0):
                gid0 = None
                # Try to find guild where member exists
            if now_ts >= mdata.get("unmute_time", 0):
                # find member across guilds
                member_obj = None
                for guild in bot.guilds:
                    m = guild.get_member(int(uid))
                for g in bot.guilds:
                    m = g.get_member(int(uid))
                    if m:
                        member_obj = m
                        gid0 = guild.id
                        break
                if member_obj:
                    role = member_obj.guild.get_role(MUTE_ROLE_ID)
@@ -205,68 +206,50 @@ async def auto_unmute_loop():
                            await member_obj.remove_roles(role, reason="Auto-unmute")
                        except Exception:
                            pass
                    # Log
                    # log unmute
                    ch = bot.get_channel(TRACKING_CHANNEL_ID)
                    if ch:
                        embed = discord.Embed(
                            title="Auto Unmute",
                            description=f"{member_obj} was automatically unmuted.",
                            color=discord.Color.green(),
                            timestamp=utc_now()
                        )
                        embed = discord.Embed(title="Auto Unmute", description=f"{member_obj} has been unmuted (auto).", color=discord.Color.green(), timestamp=utc_now())
                        embed.add_field(name="User ID", value=str(member_obj.id), inline=True)
                        embed.add_field(name="Guild ID", value=str(gid0), inline=True)
                        await safe_send_channel(ch, embed)
                # remove mute record
                data["mutes"].pop(uid, None)
                updated = True
                        await safe_send_channel(ch, embed=embed)
                # remove record
                data.get("mutes", {}).pop(uid, None)
                changed = True
        except Exception:
            continue
    if updated:
    if changed:
        await safe_save_data(data)

# helper to send embed to channel but guard for failures
async def safe_send_channel(channel: Optional[discord.TextChannel], embed: discord.Embed):
    if channel is None:
        return False
    try:
        await channel.send(embed=embed)
        return True
    except Exception:
        return False

# ----------------------------
# EVENT HANDLERS (message edit/delete, status change)
# MESSAGE EVENTS: create/update/delete
# ----------------------------
@bot.event
async def on_ready():
    # Called once when bot is ready; start loops here
    print(f"[{utc_now().isoformat()}] Bot ready as {bot.user} ({bot.user.id})")
    if not timetrack_loop.is_running():
        timetrack_loop.start()
    if not auto_unmute_loop.is_running():
        auto_unmute_loop.start()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    # Update last message for timetrack (only if user in RCACHE roles)
    # update last_message for timetrack users only
    try:
        if any(role.id in RCACHE_ROLES for role in message.author.roles):
            data = safe_load_data()
            uid = str(message.author.id)
            udata = data.get("users", {}).get(uid, {})
            if not udata:
                udata = {}
                udata = {
                    "first_seen": get_timestamp(),
                    "last_online": get_timestamp(),
                    "last_message": None,
                    "last_edit": None,
                    "total_online_seconds": 0,
                    "online_start": None
                }
            udata["last_message"] = {
                "content": message.content,
                "timestamp": get_timestamp(),
                "channel": message.channel.id,
                "channel_id": message.channel.id,
                "message_id": message.id
            }
            data.setdefault("users", {})[uid] = udata
            # save asynchronously but don't block message handling heavily
            # save, but do not block (fire-and-forget)
            await safe_save_data(data)
    except Exception:
        pass
@@ -287,7 +270,7 @@ async def on_message_edit(before: discord.Message, after: discord.Message):
                "before": before.content,
                "after": after.content,
                "timestamp": get_timestamp(),
                "channel": after.channel.id,
                "channel_id": after.channel.id,
                "message_id": after.id
            }
            data.setdefault("users", {})[uid] = udata
@@ -297,192 +280,294 @@ async def on_message_edit(before: discord.Message, after: discord.Message):

@bot.event
async def on_message_delete(message: discord.Message):
    # cache deleted message with attachments and reply info and attempt to find deleter via audit logs
    if message.author.bot:
    # ignore bot deletions
    if message.author and message.author.bot:
        return
    try:
        data = safe_load_data()
        cache = {
            "message_id": message.id,
            "author_id": message.author.id,
            "author_name": str(message.author),
            "author_id": message.author.id if message.author else None,
            "author_name": str(message.author) if message.author else "Unknown",
            "content": message.content,
            "attachments": [a.url for a in message.attachments],
            "channel_id": message.channel.id,
            "attachments": [],
            "channel_id": message.channel.id if message.channel else None,
            "timestamp": get_timestamp()
        }
        # attachments: try to download to re-upload later; but also store URLs
        files_to_upload = []
        for att in message.attachments:
            cache["attachments"].append(att.url)
            # read bytes and create discord.File for re-upload to tracking channel
            try:
                dfile = await download_attachment_to_file(att)
                if dfile:
                    files_to_upload.append(dfile)
            except Exception:
                pass
        # reply info
        if message.reference:
            ref = message.reference.resolved
            if ref and isinstance(ref, discord.Message):
                cache["reply_to"] = {"author_id": ref.author.id, "content": ref.content, "message_id": ref.id}
                cache["reply_to"] = {"author_id": ref.author.id, "author_name": str(ref.author), "message_id": ref.id, "content": ref.content}
        # who deleted? best-effort via audit log
        deleter = None
        try:
            deleter = await try_get_audit_deleter(message.guild, message.id)
            deleter = await try_get_audit_deleter(message.guild, time_window_seconds=15)
            if deleter:
                cache["deleted_by"] = deleter
                cache["deleted_by"] = {"id": getattr(deleter, "id", None), "name": str(deleter)}
        except Exception:
            pass
        data.setdefault("cached_messages", {})[str(message.id)] = cache
        await safe_save_data(data)
        # log to tracking channel
        # Log to tracking channel with embed + re-uploaded files so videos show inline:
        ch = bot.get_channel(TRACKING_CHANNEL_ID)
        if ch:
            embed = discord.Embed(title="Message Deleted", color=discord.Color.red(), timestamp=utc_now())
            embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
            embed.add_field(name="Channel", value=f"{message.channel} ({message.channel.id})", inline=False)
            if message.author:
                embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
            if message.channel:
                embed.add_field(name="Channel", value=f"{message.channel} ({message.channel.id})", inline=False)
            embed.add_field(name="Content", value=message.content or "No content", inline=False)
            if message.attachments:
                embed.add_field(name="Attachments", value="\n".join(a.url for a in message.attachments), inline=False)
            if message.reference:
                ref = message.reference.resolved
                if ref:
                    embed.add_field(name="Reply To", value=f"{ref.author} ({ref.id})", inline=False)
            if deleter:
            if message.reference and "reply_to" in cache:
                embed.add_field(name="Reply To", value=f"{cache['reply_to'].get('author_name')} (id {cache['reply_to'].get('author_id')})", inline=False)
            if cache.get("deleted_by"):
                embed.add_field(name="Deleted By (audit)", value=f"{cache['deleted_by'].get('name')} ({cache['deleted_by'].get('id')})", inline=False)
            # Send embed first, then files (if any)
            await safe_send_channel(ch, embed=embed)
            for dfile in files_to_upload:
                try:
                    deleter_member = message.guild.get_member(deleter)
                    embed.add_field(name="Deleted By (audit log)", value=str(deleter_member or f"<@{deleter}>"), inline=False)
                    await ch.send(file=dfile)
                except Exception:
                    embed.add_field(name="Deleted By (audit log)", value=str(deleter), inline=False)
            await safe_send_channel(ch, embed)
                    pass
    except Exception:
        pass
        # never crash on delete handling
        traceback.print_exc()

# ----------------------------
# rcache command (view cached messages)
# rcache command: show cached deleted messages (restricted to RCACHE roles)
# ----------------------------
@bot.command()
@commands.has_permissions(manage_guild=True)
async def rcache(ctx, count: int = 10):
    # Only allow members who have RCACHE_ROLES as well (double-check)
    # additional role guard
    if not any(role.id in RCACHE_ROLES for role in ctx.author.roles):
        await ctx.reply("You don't have permission (missing RCACHE role).", mention_author=False)
        await ctx.reply("You don't have the RCACHE role permission.", mention_author=False)
        return
    data = safe_load_data()
    cached = data.get("cached_messages", {})
    if not cached:
        await ctx.send("No cached messages.")
        return
    items = list(cached.items())[-count:]
    embed = discord.Embed(title=f"Recent {len(items)} cached messages", color=discord.Color.orange(), timestamp=utc_now())
    embed = discord.Embed(title=f"Last {len(items)} cached deletions", color=discord.Color.orange(), timestamp=utc_now())
    for mid, info in items:
        author_name = info.get("author_name", "Unknown")
        author = info.get("author_name", "Unknown")
        content = info.get("content", "No content")
        short = content if len(content) < 500 else content[:497] + "..."
        val = f"{short}\nChannel: <#{info.get('channel_id')}>"
        short = content if len(content) < 400 else content[:397] + "..."
        desc = f"{short}\nChannel: <#{info.get('channel_id')}>"
        if info.get("attachments"):
            val += f"\nAttachments: " + ", ".join(info.get("attachments"))
            desc += f"\nAttachments: {', '.join(info.get('attachments'))}"
        if info.get("reply_to"):
            val += f"\nReply to: {info['reply_to'].get('author_id')} (msg {info['reply_to'].get('message_id')})"
        embed.add_field(name=f"{author_name} (ID {info.get('author_id')})", value=val, inline=False)
            rt = info.get("reply_to")
            desc += f"\nReply to: {rt.get('author_name')} (msg {rt.get('message_id')})"
        if info.get("deleted_by"):
            desc += f"\nDeleted by: {info['deleted_by'].get('name')} ({info['deleted_by'].get('id')})"
        embed.add_field(name=f"{author} (id {info.get('author_id')})", value=desc, inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# Purge command (logs purged messages)
# Purge command (delete & log messages)
# ----------------------------
@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, limit: int):
    if limit <= 0:
        await ctx.send("Limit must be positive.")
        await ctx.send("Please provide a positive limit.")
        return
    deleted = await ctx.channel.purge(limit=limit)
    # fetch messages to log
    deleted = await ctx.channel.purge(limit=limit, bulk=True)
    # prepare a text log file summarizing content
    lines = []
    files_to_send: List[discord.File] = []
    for msg in deleted:
        try:
            lines.append(f"--- MESSAGE ID: {msg.id} ---")
            lines.append(f"Author: {msg.author} ({msg.author.id})")
            lines.append(f"Channel: #{msg.channel.name} ({msg.channel.id})")
            lines.append(f"Timestamp: {msg.created_at.isoformat() if msg.created_at else 'unknown'}")
            lines.append("Content:")
            lines.append(msg.content or "<no content>")
            if msg.reference:
                ref = msg.reference.resolved
                if ref:
                    lines.append(f"Reply to: {ref.author} (msg {ref.id})")
            if msg.attachments:
                lines.append("Attachments:")
                for a in msg.attachments:
                    lines.append(f"- {a.url} ({a.filename})")
                    # try to download attachment bytes to re-upload
                    try:
                        dfile = await download_attachment_to_file(a)
                        if dfile:
                            files_to_send.append(dfile)
                    except Exception:
                        pass
            lines.append("")  # blank line
        except Exception:
            traceback.print_exc()
    # write text log to temp file and upload
    ts_str = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"purge_log_{ctx.guild.id}_{ctx.channel.id}_{ts_str}.txt"
    tmp_path = os.path.join(tempfile.gettempdir(), fname)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    text_file = discord.File(tmp_path, filename=fname)
    # Send to tracking channel
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if not ch:
        await ctx.send(f"Purged {len(deleted)} messages.")
    if ch:
        embed = discord.Embed(title="Purge Log", description=f"{ctx.author} purged {len(deleted)} messages in #{ctx.channel.name}", color=discord.Color.red(), timestamp=utc_now())
        await safe_send_channel(ch, embed=embed)
        # upload log file
        try:
            await ch.send(file=text_file)
        except Exception:
            pass
        # upload attachments (images/videos) so they show inline (not just url)
        for f in files_to_send:
            try:
                await ch.send(file=f)
            except Exception:
                pass
    await ctx.send(f"Purged {len(deleted)} messages. Logged to tracking channel.", delete_after=8)

# ----------------------------
# RMute / Runmute commands (applying mute role and logging)
# ----------------------------
@bot.command()
@commands.has_permissions(manage_roles=True)
async def rmute(ctx, members: commands.Greedy[discord.Member], duration: int, *, reason: str = "No reason provided"):
    if not members:
        await ctx.send("No valid members provided.")
        return
    embed = discord.Embed(title="Purge Log", description=f"{ctx.author} purged {len(deleted)} messages in #{ctx.channel.name}", color=discord.Color.red(), timestamp=utc_now())
    for msg in deleted:
        content = msg.content or "No content"
        name = f"{msg.author} ({msg.author.id})"
        field_val = content if len(content) < 1024 else content[:1020] + "..."
        embed.add_field(name=name, value=field_val, inline=False)
        if msg.attachments:
            embed.add_field(name="Attachments", value="\n".join(a.url for a in msg.attachments), inline=False)
        if msg.reference:
            ref = msg.reference.resolved
            if ref:
                embed.add_field(name=f"Reply To {ref.author}", value=ref.content or "No content", inline=False)
    await safe_send_channel(ch, embed)
    # optional notify executor
    data = safe_load_data()
    now_ts = get_timestamp()
    for m in members:
        try:
            mute_role = ctx.guild.get_role(MUTE_ROLE_ID)
            if not mute_role:
                await ctx.send("Mute role not found in this guild.")
                return
            await m.add_roles(mute_role, reason=f"RMute by {ctx.author}: {reason}")
            unmute_time = now_ts + int(duration)
            data.setdefault("mutes", {})[str(m.id)] = {
                "moderator": ctx.author.id,
                "start_time": now_ts,
                "duration": int(duration),
                "reason": reason,
                "unmute_time": unmute_time
            }
            data.setdefault("rmute_usage", {})
            # track mod usage (counts how many rmute commands this moderator executed)
            data["rmute_usage"][str(ctx.author.id)] = data.get("rmute_usage", {}).get(str(ctx.author.id), 0) + 1
            # DM to muted user
            embed = discord.Embed(title="You have been muted", color=discord.Color.red(), timestamp=utc_now())
            embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
            embed.add_field(name="Duration (s)", value=str(duration), inline=False)
            embed.add_field(name="Reason", value=reason, inline=False)
            # try to DM, respecting rdm opt-out
            try:
                await send_dm_safe(m, embed)
            except Exception:
                pass
            # Log to tracking channel
            ch = bot.get_channel(TRACKING_CHANNEL_ID)
            if ch:
                log = discord.Embed(title="Mute Applied", color=discord.Color.red(), timestamp=utc_now())
                log.add_field(name="User", value=f"{m} ({m.id})", inline=False)
                log.add_field(name="Moderator", value=f"{ctx.author} ({ctx.author.id})", inline=False)
                log.add_field(name="Duration (s)", value=str(duration), inline=True)
                log.add_field(name="Reason", value=reason, inline=False)
                await safe_send_channel(ch, embed=log)
        except Exception:
            traceback.print_exc()
    await safe_save_data(data)
    try:
        await ctx.send(f"Purged {len(deleted)} messages. Logged to tracking channel.", delete_after=10)
        await ctx.message.delete()
    except Exception:
        pass

@bot.command()
@commands.has_permissions(manage_roles=True)
async def runmute(ctx, member: discord.Member, duration: int, *, reason: str = "No reason provided"):
    # single-user wrapper
    await rmute(ctx, [member], duration, reason=reason)

# ----------------------------
# RMute / Runmute logging helper
# RMute leaderboard: which moderators used rmute the most
# ----------------------------
async def log_mute_action(target_member: discord.Member, moderator: discord.Member, duration: int, reason: str):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if not ch:
        return
    embed = discord.Embed(title="Mute Applied", color=discord.Color.red(), timestamp=utc_now())
    embed.add_field(name="User", value=f"{target_member} ({target_member.id})", inline=False)
    embed.add_field(name="Moderator", value=f"{moderator} ({moderator.id})", inline=False)
    embed.add_field(name="Duration (s)", value=str(duration), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await safe_send_channel(ch, embed)
@bot.command()
async def rmlb(ctx, top: int = 10):
    data = safe_load_data()
    usage = data.get("rmute_usage", {})
    sorted_usage = sorted(usage.items(), key=lambda x: x[1], reverse=True)[:top]
    embed = discord.Embed(title="RMute Usage Leaderboard (moderators)", color=discord.Color.blue(), timestamp=utc_now())
    for i, (modid, count) in enumerate(sorted_usage, start=1):
        member = ctx.guild.get_member(int(modid))
        name = str(member) if member else f"<@{modid}>"
        embed.add_field(name=f"{i}. {name}", value=f"RMute actions: {count}", inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# Staff ping functions (ping members individually and log)
# Staff ping commands pinging members individually (and logging)
# ----------------------------
async def ping_members_by_role_and_log(ctx: commands.Context, role_id: int):
async def ping_members_by_role_and_log(ctx: commands.Context, role_id: int, title: str):
    members = [m for m in ctx.guild.members if any(r.id == role_id for r in m.roles)]
    if not members:
        await ctx.send("No members with that role were found.", delete_after=8)
        await ctx.send("No members with that role found.", delete_after=8)
        return
    mentions = " ".join(m.mention for m in members)
    # Send to invoking channel (so staff are pinged in place), then log
    try:
        await ctx.send(mentions)
    except Exception:
        # try sending smaller chunks in case of length issues
        chunk = []
        length = 0
        for m in members:
            part = m.mention + " "
            if length + len(part) > 1900:
                try:
                    await ctx.send("".join(chunk))
                except Exception:
                    pass
                chunk = []
                length = 0
            chunk.append(part)
            length += len(part)
        if chunk:
    # build mention chunks to avoid single message too long
    chunk = ""
    for m in members:
        mention = m.mention + " "
        if len(chunk) + len(mention) > 1900:
            try:
                await ctx.send("".join(chunk))
                await ctx.send(chunk)
            except Exception:
                pass
    # delete the command message (silence)
            chunk = mention
        else:
            chunk += mention
    if chunk:
        try:
            await ctx.send(chunk)
        except Exception:
            pass
    # try to delete original command message for cleanliness
    try:
        await ctx.message.delete()
    except Exception:
        pass
    # Log
    for log_channel_id in STAFF_LOG_CHANNELS:
        ch = bot.get_channel(log_channel_id)
    # log the ping
    for ch_id in STAFF_LOG_CHANNELS:
        ch = bot.get_channel(ch_id)
        if ch:
            embed = discord.Embed(title="Staff Ping", description=f"{ctx.author} triggered a staff ping.", color=discord.Color.blue(), timestamp=utc_now())
            embed = discord.Embed(title=title, description=f"{ctx.author} triggered {title}", color=discord.Color.blue(), timestamp=utc_now())
            if ctx.message.reference:
                embed.add_field(name="Replying To", value=f"Message ID: {ctx.message.reference.message_id}", inline=False)
            await safe_send_channel(ch, embed)
            await safe_send_channel(ch, embed=embed)

@bot.command()
@commands.has_permissions(send_messages=True)
async def rping(ctx):
    await ping_members_by_role_and_log(ctx, STAFF_PING_ROLE)
    await ping_members_by_role_and_log(ctx, STAFF_PING_ROLE, "Staff Ping")

@bot.command()
@commands.has_permissions(send_messages=True)
async def hsping(ctx):
    await ping_members_by_role_and_log(ctx, HIGHER_STAFF_PING_ROLE)
    await ping_members_by_role_and_log(ctx, HIGHER_STAFF_PING_ROLE, "Higher Staff Ping")

# ----------------------------
# Timetrack leaderboards (tlb/tdm)
# Timetrack leaderboards: tlb (with RCACHE roles), tdm (without RCACHE roles)
# ----------------------------
@bot.command()
async def tlb(ctx, top: int = 10):
@@ -499,14 +584,15 @@ async def tlb(ctx, top: int = 10):
            total = udata.get("total_online_seconds", 0)
            if udata.get("online_start"):
                total += now_ts - int(udata.get("online_start"))
            # compute days based on earliest activity (use last_online as proxy)
            days = max(1, (now_ts - int(udata.get("last_online", now_ts))) / 86400)
            avg = total / days
            rows.append((member, avg, total))
            # days: from first_seen to now
            first_seen = int(udata.get("first_seen", now_ts))
            days = max(1, (now_ts - first_seen) / 86400)
            avg_daily = total / days
            rows.append((member, avg_daily, total))
        except Exception:
            continue
    rows.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard (RCACHE roles)", color=discord.Color.green(), timestamp=utc_now())
    embed = discord.Embed(title="Timetrack Leaderboard (RCACHE Roles)", color=discord.Color.green(), timestamp=utc_now())
    for i, (member, avg, total) in enumerate(rows[:top], start=1):
        embed.add_field(name=f"{i}. {member}", value=f"Avg/day: {format_duration(int(avg))} — Total: {format_duration(int(total))}", inline=False)
    await ctx.send(embed=embed)
@@ -526,36 +612,36 @@ async def tdm(ctx, top: int = 10):
            total = udata.get("total_online_seconds", 0)
            if udata.get("online_start"):
                total += now_ts - int(udata.get("online_start"))
            days = max(1, (now_ts - int(udata.get("last_online", now_ts))) / 86400)
            avg = total / days
            rows.append((member, avg, total))
            first_seen = int(udata.get("first_seen", now_ts))
            days = max(1, (now_ts - first_seen) / 86400)
            avg_daily = total / days
            rows.append((member, avg_daily, total))
        except Exception:
            continue
    rows.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard (No RCACHE roles)", color=discord.Color.green(), timestamp=utc_now())
    embed = discord.Embed(title="Timetrack Leaderboard (No RCACHE Roles)", color=discord.Color.green(), timestamp=utc_now())
    for i, (member, avg, total) in enumerate(rows[:top], start=1):
        embed.add_field(name=f"{i}. {member}", value=f"Avg/day: {format_duration(int(avg))} — Total: {format_duration(int(total))}", inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# Channel / Role / Webhook event logging
# Channel/Role/Webhook event logging
# ----------------------------
@bot.event
async def on_guild_channel_create(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Channel Created", color=discord.Color.green(), timestamp=utc_now())
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        embed.add_field(name="Type", value=str(type(channel)), inline=False)
        await safe_send_channel(ch, embed)
        embed = discord.Embed(title="Channel Created", description=f"{channel.name}", color=discord.Color.green(), timestamp=utc_now())
        embed.add_field(name="Channel ID", value=str(channel.id), inline=False)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_guild_channel_delete(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Channel Deleted", color=discord.Color.red(), timestamp=utc_now())
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        await safe_send_channel(ch, embed)
        embed = discord.Embed(title="Channel Deleted", description=f"{channel.name}", color=discord.Color.red(), timestamp=utc_now())
        embed.add_field(name="Channel ID", value=str(channel.id), inline=False)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_guild_channel_update(before, after):
@@ -564,23 +650,23 @@ async def on_guild_channel_update(before, after):
        embed = discord.Embed(title="Channel Updated", color=discord.Color.orange(), timestamp=utc_now())
        embed.add_field(name="Before", value=f"{before.name} ({before.id})", inline=False)
        embed.add_field(name="After", value=f"{after.name} ({after.id})", inline=False)
        await safe_send_channel(ch, embed)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_guild_role_create(role):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Role Created", color=discord.Color.green(), timestamp=utc_now())
        embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
        await safe_send_channel(ch, embed)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_guild_role_delete(role):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Role Deleted", color=discord.Color.red(), timestamp=utc_now())
        embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
        await safe_send_channel(ch, embed)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_guild_role_update(before, after):
@@ -589,38 +675,37 @@ async def on_guild_role_update(before, after):
        embed = discord.Embed(title="Role Updated", color=discord.Color.orange(), timestamp=utc_now())
        embed.add_field(name="Before", value=f"{before.name} ({before.id})", inline=False)
        embed.add_field(name="After", value=f"{after.name} ({after.id})", inline=False)
        await safe_send_channel(ch, embed)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_webhook_update(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Webhook Updated", color=discord.Color.purple(), timestamp=utc_now())
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        await safe_send_channel(ch, embed)
        await safe_send_channel(ch, embed=embed)

# ----------------------------
# Help command listing everything
# Help command
# ----------------------------
@bot.command()
async def rhelp(ctx):
    e = discord.Embed(title="Help — Commands", color=discord.Color.blue(), timestamp=utc_now())
    e.add_field(name="!timetrack [user]", value="Show timetrack info for a user", inline=False)
    e.add_field(name="!tlb [top]", value="Timetrack leaderboard (RCACHE)", inline=False)
    e.add_field(name="!tdm [top]", value="Timetrack leaderboard (no RCACHE)", inline=False)
    e.add_field(name="!rmute [users] [seconds] [reason]", value="Mute multiple users with duration in seconds", inline=False)
    e.add_field(name="!runmute [user] [seconds] [reason]", value="Mute a single user", inline=False)
    e.add_field(name="!rmlb", value="Show top rmute users (by moderator usage)", inline=False)
    e.add_field(name="!rcache [count]", value="Show deleted messages cache (restricted)", inline=False)
    e.add_field(name="!purge [limit]", value="Purge messages and log them", inline=False)
    e.add_field(name="!rping", value="Ping all members who have STAFF_PING_ROLE", inline=False)
    e.add_field(name="!hsping", value="Ping all members who have HIGHER_STAFF_PING_ROLE", inline=False)
    e.add_field(name="!rdm", value="Toggle opt-out from bot DMs", inline=False)
    await ctx.send(embed=e)
    embed = discord.Embed(title="Bot Commands", color=discord.Color.blue(), timestamp=utc_now())
    embed.add_field(name="!timetrack [user]", value="Show timetrack stats for a user (if tracked).", inline=False)
    embed.add_field(name="!tlb [top]", value="Timetrack leaderboard (RCACHE roles)", inline=False)
    embed.add_field(name="!tdm [top]", value="Timetrack leaderboard (no RCACHE roles)", inline=False)
    embed.add_field(name="!rmute [users] [seconds] [reason]", value="Mute one or more users for seconds", inline=False)
    embed.add_field(name="!runmute [user] [seconds] [reason]", value="Mute single user", inline=False)
    embed.add_field(name="!rmlb", value="Top moderators who used rmute the most", inline=False)
    embed.add_field(name="!rcache [count]", value="View cached deleted messages (RCACHE roles only).", inline=False)
    embed.add_field(name="!purge [limit]", value="Purge messages and log them", inline=False)
    embed.add_field(name="!rping", value="Ping staff by member (STAFF_PING_ROLE)", inline=False)
    embed.add_field(name="!hsping", value="Ping higher staff by member (HIGHER_STAFF_PING_ROLE)", inline=False)
    embed.add_field(name="!rdm", value="Toggle opt-out from bot DMs", inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# Start the bot
# Start bot (loops start in on_ready)
# ----------------------------
if __name__ == "__main__":
    # Do not start loops here — they are started in on_ready()
    bot.run(TOKEN)
