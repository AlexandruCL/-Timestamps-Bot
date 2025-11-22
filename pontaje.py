import os
import logging
import datetime
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
import pathlib
from typing import Dict, Any
from discord.ext import tasks
import calendar
import asyncio
import re
import aiohttp
import sys
import select
import time
import sqlite3
from discord import app_commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import io

from database import (
    init_db, add_clock_in, update_clock_out, get_clock_times,
    get_ongoing_sessions, remove_session,
    increment_punish_count, get_punish_count, reset_punish_count,
    add_clock_in_sas, update_clock_out_sas, get_clock_times_sas, get_ongoing_sessions_sas, remove_session_sas,
    checkpoint_and_vacuum,  # <-- add
    db_stats,
)

# --------------- Environment ---------------
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

def need(key: str) -> str:
    v = os.getenv(key)
    if not v or not v.strip():
        raise SystemExit(f"Missing env var: {key}")
    return v.strip()

ALLOWED_CHANNEL_ID            = int(need("ALLOWED_CHANNEL_ID"))
CONSOLE_RELAY_DEFAULT_CHANNEL_ID = int(os.getenv("CONSOLE_RELAY_DEFAULT_CHANNEL_ID", "0"))
CONSOLE_WEBHOOK_NAME = os.getenv("CONSOLE_WEBHOOK_NAME", "console")
CONSOLE_WEBHOOK_AVATAR_URL = os.getenv("CONSOLE_WEBHOOK_AVATAR_URL", "")
ALLOWED_HR_CHANNEL_ID      = int(need("ALLOWED_HR_CHANNEL_ID"))
ALLOWED_PUNISH_CHANNEL_ID     = int(need("ALLOWED_PUNISH_CHANNEL_ID"))
LOGS_CHANNEL_ID               = int(need("LOGS_CHANNEL_ID"))
ADDMINUTES_LOG_CHANNEL_ID = [
    int(x.strip()) for x in os.getenv("ADDMINUTES_LOG_CHANNEL_ID", "").split(",") if x.strip().isdigit()
]
ONGOING_STOP_CHANNEL_ID = [
    int(x.strip()) for x in os.getenv("ONGOING_STOP_CHANNEL_ID", "").split(",") if x.strip().isdigit()
]
DELETE_PONTAJ_CHANNEL_ID = [
    int(x.strip()) for x in os.getenv("DELETE_PONTAJ_CHANNEL_ID", "").split(",") if x.strip().isdigit()
]

REQUIRED_PD_ROLE_ID = [
     int(r.strip()) for r in os.getenv("REQUIRED_PD_ROLE_NAME", "").split(",") if r.strip()
]

REQUIRED_HR_ROLE_ID           = int(need("REQUIRED_HR_ROLE_NAME"))

CONDUCERE_ROLE_ID             = int(need("CONDUCERE_ROLE_ID"))


SAS_CHANNEL_ID = int(need("SAS_CHANNEL_ID"))
SAS_ACTIUNI_CHANNEL_ID = int(need("SAS_ACTIUNI_CHANNEL_ID"))
SAS_ROLE_IDS = int(need("SAS_ROLE_IDS"))
SAS_COORDONATOR_IDS = int(need("SAS_COORDONATOR_IDS"))
ACTIVITY_API_URL = os.getenv("ACTIVITY_API_URL")          
ACTIVITY_API_TOKEN = os.getenv("ACTIVITY_API_TOKEN")     
ACTIVITY_API_SHEET = os.getenv("ACTIVITY_API_SHEET", "RAZII")  

CALLSIGN_RE = re.compile(r"\[?S-(\d{1,2})\]?", re.IGNORECASE)
PD_CALLSIGN_RE = re.compile(r"\[(\d{1,3})\]")  

GOOGLE_SHEETS_CREDENTIALS_FILE = os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE")
PD_SPREADSHEET_ID = os.getenv("PD_SPREADSHEET_ID")
SAS_SPREADSHEET_ID = os.getenv("SAS_SPREADSHEET_ID")
SAS_MEMBER_NOTIFICATIONS_CHANNEL_ID = int(os.getenv("SAS_MEMBER_NOTIFICATIONS_CHANNEL_ID", "0"))  # Add this line
SAS_EVIDENTA_CHANNEL_ID = int(need("SAS_EVIDENTA_CHANNEL_ID"))

if not SAS_ROLE_IDS:
    logging.warning("SAS_ROLE_IDS empty – SAS buttons/commands will always fail role check.")

if not SAS_COORDONATOR_IDS:
    logging.warning("SAS_COORDONATOR_IDS empty – SAS coordinator checks will always fail.")

DEV_GUILD_ID_ENV = os.getenv("DEV_GUILD_ID")
DEV_GUILD_ID = int(DEV_GUILD_ID_ENV) if DEV_GUILD_ID_ENV and DEV_GUILD_ID_ENV.isdigit() else None
LEAVE_CHANNEL_ID = int(need("LEAVE_CHANNEL_ID"))

EOD_CONFIRM_WINDOW_SECS = int(os.getenv("EOD_CONFIRM_WINDOW_SECS", "300"))  # 5 minutes
EOD_CONFIRM_EMOJI = "✅"


# --------------- Logging (console + to Discord channel) ---------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

class DiscordHandler(logging.Handler):
    """Send WARNING+ log records to the LOGS_CHANNEL_ID as code blocks."""
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__(level=logging.WARNING)
        self.bot = bot
        self.channel_id = channel_id

    def emit(self, record: logging.LogRecord):
        if not self.bot.is_ready():
            return
        msg = self.format(record)
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            try:
                # avoid blocking; schedule async send
                self.bot.loop.create_task(channel.send(f"```\n{msg[:1900]}\n```"))
            except Exception:
                pass


# --------------- Time ---------------

TIMEZONE = os.getenv("TIMEZONE", "Europe/Bucharest")

def local_now() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(TIMEZONE))

# (optional) helper to parse stored times as local aware
def parse_local(date_str: str, time_str: str) -> datetime.datetime:
    return datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(TIMEZONE))


# --------------- DB ---------------
init_db()

# --------------- Logging ---------------
LOG_FILE_PATH = pathlib.Path("logs.txt")

def _append_log_line(text: str):
    try:
        with LOG_FILE_PATH.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass

async def log_command(
    interaction: discord.Interaction,
    action: str,
    *,
    target: discord.Member | None = None,
    extra: str | None = None,
    changed: bool = False,
    success: bool = True
):
    """
    action   : command name or descriptive action
    target   : affected user (if any)
    extra    : extra info text
    changed  : True if command modified data
    success  : False if failed / denied / error
    """
    channel = interaction.guild.get_channel(LOGS_CHANNEL_ID) if interaction.guild else None

    actor = interaction.user
    status = "SUCCESS" if success else "FAIL"
    kind = "MODIFY" if changed else "INFO"
    tgt_txt = f" | target={target}({target.id})" if target else ""
    extra_line = f" | {extra}" if extra else ""
    line = f"[{datetime.datetime.utcnow().isoformat()}Z] [{status}] [{kind}] {action} by {actor}({actor.id}){tgt_txt}{extra_line}"
    _append_log_line(line)

    # Build embed
    color = (
        discord.Color.green() if success and changed else
        discord.Color.blurple() if success else
        discord.Color.red()
    )
    desc_parts = [
        f"Actiune: `{action}`",
        f"Executor: {actor.mention} (`{actor.id}`)"
    ]
    if target:
        desc_parts.append(f"Target: {target.mention} (`{target.id}`)")
    desc_parts.append(f"Tip: {'Modificare' if changed else 'Informare'}")
    desc_parts.append(f"Status: {'Succes' if success else 'Eșec'}")
    if extra:
        desc_parts.append(f"Detalii: {extra[:500]}")
    embed = make_embed("Log Comandă", "\n".join(desc_parts), color, actor)

    if channel:
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    if (action == "adaugaminute-button" or action == "adaugaminute-sas-button") and ADDMINUTES_LOG_CHANNEL_ID:
        for cid in ADDMINUTES_LOG_CHANNEL_ID:
            if channel and cid == channel.id:
                continue  # avoid duplicate in same channel
            ch = bot.get_channel(cid)
            if ch:
                try:
                    await ch.send(embed=embed)
                except Exception:
                    pass

    if action == "ongoing-stop-button" and ONGOING_STOP_CHANNEL_ID:
        for cid in ONGOING_STOP_CHANNEL_ID:
            if channel and cid == channel.id:
                continue  # avoid duplicate in same channel
            ch = bot.get_channel(cid)
            if ch:
                try:
                    await ch.send(embed=embed)
                except Exception:
                    pass

    if action == "stergepontaj" and DELETE_PONTAJ_CHANNEL_ID:
        for cid in DELETE_PONTAJ_CHANNEL_ID:
            if channel and cid == channel.id:
                continue  # avoid duplicate in same channel
            ch = bot.get_channel(cid)
            if ch:
                try:
                    await ch.send(embed=embed)
                except Exception:
                    pass


# --------------- Helpers (report) ---------------
def _list_pd_members(guild: discord.Guild) -> list[discord.Member]:
    # Users having any PD role (REQUIRED_PD_ROLE_ID list)
    out = []
    for m in guild.members:
        if not m.bot and has_any(m, REQUIRED_PD_ROLE_ID):
            out.append(m)
    return out

def _callsign_sort_key(member: discord.Member, *, is_sas: bool) -> tuple[int, str]:
    """
    Returns a tuple used for sorting members by callsign.
    Members without a detectable callsign are pushed to the end.
    """
    name = (member.display_name or member.name or "")
    n = 10**6  # large default -> goes to end
    m = CALLSIGN_RE.search(name) if is_sas else PD_CALLSIGN_RE.search(name)
    if not m and not is_sas:
        # PD fallback: try to catch bare numbers like "001 Name"
        m = re.search(r"\b(\d{1,3})\b", name)
    if m:
        try:
            n = int(m.group(1))
        except Exception:
            pass
    return (n, name.lower())

def _get_week_dates(reference_date: datetime.datetime | None = None) -> tuple[str, list[str]]:
    """
    Returns (week_label, [date_strings]) for Sunday-Saturday week containing reference_date.
    date_strings are in YYYY-MM-DD format, starting with Sunday.
    """
    if reference_date is None:
        reference_date = local_now()
    
    # Find the Sunday of the current week (weekday 6 = Sunday in Python)
    days_since_sunday = (reference_date.weekday() + 1) % 7
    sunday = reference_date - datetime.timedelta(days=days_since_sunday)
    
    # Generate all 7 days (Sunday through Saturday)
    week_dates = []
    for i in range(7):
        day = sunday + datetime.timedelta(days=i)
        week_dates.append(day.strftime("%Y-%m-%d"))
    
    # Week label: "DD.MM - DD.MM.YYYY"
    week_label = f"{sunday.strftime('%d.%m')} - {(sunday + datetime.timedelta(days=6)).strftime('%d.%m.%Y')}"
    
    return week_label, week_dates

def build_week_report_sas(guild: discord.Guild, week_dates: list[str]) -> list[str]:
    """
    Build weekly report for SAS members.
    Returns lines containing the formatted table.
    """
    # Get all SAS members
    members = []
    for m in guild.members:
        if not m.bot and has_role(m, SAS_ROLE_IDS):
            members.append(m)
    
    # Sort by callsign
    members = sorted(members, key=lambda m: _callsign_sort_key(m, is_sas=True))
    
    # Build table header
    day_names = ["Du", "Lu", "Ma", "Mi", "Jo", "Vi", "Sb"]
    header = f"{'Nume':<25} " + " ".join(f"{d:>6}" for d in day_names) + "  Total"
    lines = [header, "-" * len(header)]
    
    # Build rows for ALL members
    for mem in members:
        # Use display name instead of callsign
        display_name = (mem.display_name or mem.name or "Unknown")[:25]  # Limit to 25 chars
        
        # Calculate minutes for each day
        day_minutes = []
        total = 0
        
        for date_str in week_dates:
            sessions = get_clock_times_sas(mem.id, date_str)
            day_total = 0
            
            for s in sessions:
                if s[0] and s[1]:
                    ci = parse_local(date_str, s[0])
                    co = parse_local(date_str, s[1])
                    mins = (co - ci).total_seconds() / 60
                    r = round_minutes(mins)
                    if r > 0:
                        day_total += r
            
            day_minutes.append(day_total)
            total += day_total
        
        # Include ALL members (even with 0 total)
        day_str = " ".join(f"{int(m):>6}" for m in day_minutes)
        row = f"{display_name:<25} {day_str}  {int(total):>5}"
        lines.append(row)
    
    if len(lines) == 2:  # Only header + separator (no members with SAS role)
        lines.append("Niciun membru SAS găsit.")
    
    return lines

def build_day_report(date_str: str, guild: discord.Guild, member: discord.Member | None = None, *, is_sas: bool = False) -> tuple[str, list[str]]:
    getter = get_clock_times if not is_sas else get_clock_times_sas
    lines = []
    members = [member] if member else _list_pd_members(guild)

    if not member:
        members = sorted(members, key=lambda m: _callsign_sort_key(m, is_sas=is_sas))

    for mem in members:
        sessions = getter(mem.id, date_str)
        total = 0
        for s in sessions:
            if s[0] and s[1]:
                ci = parse_local(date_str, s[0])
                co = parse_local(date_str, s[1])
                mins = (co - ci).total_seconds() / 60
                r = round_minutes(mins)
                if r > 0:
                    total += r
        if total > 0:
            if member:
                # Show session detail if single member
                detail_lines = []
                for idx, s in enumerate(sessions, start=1):
                    if s[0] and s[1]:
                        ci = parse_local(date_str, s[0])
                        co = parse_local(date_str, s[1])
                        mins = (co - ci).total_seconds() / 60
                        r = round_minutes(mins)
                        if r > 0:
                            detail_lines.append(f"{idx}. {s[0]} - {s[1]} ({int(r)}min)")
                lines.append(f"{mem.display_name} Total: ({int(total)})\n" + "\n".join(detail_lines))
            else:
                lines.append(f"{mem.display_name}: ({int(total)} min)")
    title = f"Raport {date_str}" + (f" - {member.display_name}" if member else " (toți)")
    if not lines:
        lines = ["Fără date."]
    return title, lines


# --------------- Helpers (warn) ---------------

def _punish_channel(guild: discord.Guild) -> discord.TextChannel | None:
    ch = guild.get_channel(ALLOWED_PUNISH_CHANNEL_ID) if guild else None
    return ch if isinstance(ch, discord.TextChannel) else None

def _warn_embed(actor: discord.Member, target: discord.Member, title: str, desc: str, color: discord.Color):
    return make_embed(title, f"{target.mention}\n{desc}", color, actor)

def _send_warn_to_channel(guild: discord.Guild, embed: discord.Embed, *, mention_when_3: bool = False):
    ch = _punish_channel(guild)
    if not ch:
        return
    content = None
    if mention_when_3:
        conducere_role = guild.get_role(CONDUCERE_ROLE_ID)
        hr_role = guild.get_role(REQUIRED_HR_ROLE_ID)
        mentions = []
        if conducere_role: mentions.append(conducere_role.mention)
        if hr_role: mentions.append(hr_role.mention)
        if mentions:
            content = "||" + " ".join(mentions) + "||"
    try:
        # we are already in async context
        # send message (no asyncio.create_task)
        guild.loop.create_task(ch.send(content=content, embed=embed))  # safe fire-and-forget
    except Exception:
        pass

def _build_warn_embed(actor: discord.Member, target: discord.Member, title: str, body: str, color: discord.Color):
    return make_embed(title, f"{target.mention}\n{body}", color, actor)

async def _post_warn(
    guild: discord.Guild,
    *,
    actor: discord.Member,
    target: discord.Member,
    kind: str,              # add | reset | status
    count: int | None = None,
    reason: str | None = None,
    note: str | None = None
):
    """
    Send a warn-related message to punish channel (public).
    Mirrors style of /warn command.
    """
    ch = _punish_channel(guild)
    if not ch:
        return
    if kind == "add":
        assert count is not None
        color = discord.Color.red() if count == 3 else discord.Color.orange()
        body = f"Avertizat de {actor.mention}\nWarn {count}/3\n\n{reason or ''}"
        embed = _build_warn_embed(actor, target, "Avertisment", body, color)
        # Mentions when 3/3
        content = None
        if count == 3:
            conducere_role = guild.get_role(CONDUCERE_ROLE_ID)
            hr_role = guild.get_role(REQUIRED_HR_ROLE_ID)
            mentions = []
            if conducere_role: mentions.append(conducere_role.mention)
            if hr_role: mentions.append(hr_role.mention)
            if mentions:
                content = "||" + " ".join(mentions) + "||"
        await ch.send(content=content, embed=embed)

    elif kind == "reset":
        body = f"Resetat de {actor.mention}.\n{(note or '').strip()}"
        embed = _build_warn_embed(actor, target, "Reset Warn-uri", body, discord.Color.green())
        await ch.send(embed=embed)

    elif kind == "status":
        assert count is not None
        body = f"Status warn: {count}/3"
        embed = _build_warn_embed(actor, target, "Status Warn", body, discord.Color.blurple())
        await ch.send(embed=embed)

# --------------- SAS EVIDENTA MEMBRII ---------------

def get_google_sheets_client():
    """Initialize and return Google Sheets client."""
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_SHEETS_CREDENTIALS_FILE, scope)
    return gspread.authorize(creds)

def _extract_pd_callsign(member: discord.Member | None) -> str | None:
    """Extract PD callsign [xxx] from member display name."""
    if not member:
        return None
    name = (member.display_name or member.name)
    m = PD_CALLSIGN_RE.search(name)
    if not m:
        return None
    digits = m.group(1)
    try:
        num = int(digits)
    except ValueError:
        logging.warning("Invalid PD callsign digits '%s' in name '%s'", digits, name)
        return None
    if num < 1 or num > 999:
        return None
    return digits  # Return as-is (e.g., "203", "005")

def get_pd_id_by_callsign(callsign: str) -> str | None:
    """Find PD ID (column A) by callsign in PD spreadsheet."""
    try:
        client = get_google_sheets_client()
        sheet = client.open_by_key(PD_SPREADSHEET_ID).sheet1
        
        # Search for callsign in the sheet
        cell = sheet.find(callsign)
        if cell:
            # Get ID from column A of the same row
            return sheet.cell(cell.row, 1).value
        return None
    except Exception as e:
        logging.error(f"Error getting PD ID for callsign {callsign}: {e}")
        return None

def add_member_to_sas_excel(discord_id: str) -> tuple[bool, str]:
    """
    Add member ID to first empty spot in SAS excel (B23:B40).
    Returns (success, message with callsign from column D).
    """
    try:
        client = get_google_sheets_client()
        sheet = client.open_by_key(SAS_SPREADSHEET_ID).sheet1
        
        # Get range B23:B40
        cells = sheet.range('B23:B40')
        
        # Find first empty cell
        for cell in cells:
            if not cell.value or cell.value.strip() == "":
                # Write as formula to trigger auto-complete in other columns
                cell.value = discord_id
                sheet.update_cells([cell])
                
                # Force recalculation
                batch_data = [{
                    'range': cell.address,
                    'values': [[discord_id]]
                }]
                sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
                
                # Get callsign from column D (same row)
                row = cell.row
                callsign_cell = sheet.cell(row, 4)  # Column D is index 4
                callsign = callsign_cell.value or "N/A"
                
                return True, f"Membru adăugat: {callsign}"
        
        return False, "Nu există poziții libere în intervalul B23:B40"
    except Exception as e:
        logging.error(f"Error adding member to SAS excel: {e}")
        return False, f"Eroare: {str(e)}"

def move_member_in_sas_excel(discord_id: str, direction: str) -> tuple[bool, str]:
    try:
        client = get_google_sheets_client()
        sheet = client.open_by_key(SAS_SPREADSHEET_ID).sheet1
        
        ranges = {
            'coordonator_sas': sheet.range('B11:B12'),
            'coordonator_teste': sheet.range('B14:B16'),
            'agent_special': sheet.range('B18:B21'),
            'agent_sas': sheet.range('B23:B40')
        }
        
        member_range = None
        member_cell = None
        
        for range_name, cells in ranges.items():
            for cell in cells:
                if cell.value == discord_id:
                    member_range = range_name
                    member_cell = cell
                    break
            if member_range:
                break
        
        if not member_range:
            return False, "Membrul nu a fost găsit în niciun interval"
        
        if direction == 'up':
            if member_range == 'coordonator_sas':
                return False, "Membrul este deja la Coordonator SAS (cel mai înalt rang)"
            
            elif member_range == 'coordonator_teste':
                target_cells = ranges['coordonator_sas']
                for target_cell in target_cells:
                    if not target_cell.value or target_cell.value.strip() == "":
                        # Move and trigger recalculation
                        member_cell.value = ""
                        target_cell.value = discord_id
                        sheet.update_cells([member_cell, target_cell])
                        
                        # Force recalc
                        batch_data = [
                            {'range': member_cell.address, 'values': [[""]]},
                            {'range': target_cell.address, 'values': [[discord_id]]}
                        ]
                        sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
                        
                        # Get callsign from column D
                        callsign = sheet.cell(target_cell.row, 4).value or "N/A"
                        return True, f"Membru mutat la Coordonator SAS: {callsign}"
                return False, "Nu există poziții libere în Coordonator SAS (B11:B12)"
            
            elif member_range == 'agent_special':
                target_cells = ranges['coordonator_teste']
                for target_cell in target_cells:
                    if not target_cell.value or target_cell.value.strip() == "":
                        member_cell.value = ""
                        target_cell.value = discord_id
                        sheet.update_cells([member_cell, target_cell])
                        batch_data = [
                            {'range': member_cell.address, 'values': [[""]]},
                            {'range': target_cell.address, 'values': [[discord_id]]}
                        ]
                        sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
                        
                        # Get callsign from column D
                        callsign = sheet.cell(target_cell.row, 4).value or "N/A"
                        return True, f"Membru mutat la Coordonator SAS - TESTE: {callsign}"
                return False, "Nu există poziții libere în Coordonator SAS - TESTE (B14:B16)"
            
            elif member_range == 'agent_sas':
                target_cells = ranges['agent_special']
                for target_cell in target_cells:
                    if not target_cell.value or target_cell.value.strip() == "":
                        member_cell.value = ""
                        target_cell.value = discord_id
                        sheet.update_cells([member_cell, target_cell])
                        batch_data = [
                            {'range': member_cell.address, 'values': [[""]]},
                            {'range': target_cell.address, 'values': [[discord_id]]}
                        ]
                        sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
                        
                        # Get callsign from column D
                        callsign = sheet.cell(target_cell.row, 4).value or "N/A"
                        return True, f"Membru mutat la AGENT SPECIAL: {callsign}"
                return False, "Nu există poziții libere în AGENT SPECIAL (B18:B21)"
        
        elif direction == 'down':
            if member_range == 'agent_sas':
                return False, "Membrul este deja la AGENT S.A.S (cel mai jos rang)"
            
            elif member_range == 'coordonator_sas':
                target_cells = ranges['coordonator_teste']
                for target_cell in target_cells:
                    if not target_cell.value or target_cell.value.strip() == "":
                        member_cell.value = ""
                        target_cell.value = discord_id
                        sheet.update_cells([member_cell, target_cell])
                        batch_data = [
                            {'range': member_cell.address, 'values': [[""]]},
                            {'range': target_cell.address, 'values': [[discord_id]]}
                        ]
                        sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
                        
                        # Get callsign from column D
                        callsign = sheet.cell(target_cell.row, 4).value or "N/A"
                        return True, f"Membru mutat la Coordonator SAS - TESTE: {callsign}"
                return False, "Nu există poziții libere în Coordonator SAS - TESTE (B14:B16)"
            
            elif member_range == 'coordonator_teste':
                target_cells = ranges['agent_special']
                for target_cell in target_cells:
                    if not target_cell.value or target_cell.value.strip() == "":
                        member_cell.value = ""
                        target_cell.value = discord_id
                        sheet.update_cells([member_cell, target_cell])
                        batch_data = [
                            {'range': member_cell.address, 'values': [[""]]},
                            {'range': target_cell.address, 'values': [[discord_id]]}
                        ]
                        sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
                        
                        # Get callsign from column D
                        callsign = sheet.cell(target_cell.row, 4).value or "N/A"
                        return True, f"Membru mutat la AGENT SPECIAL: {callsign}"
                return False, "Nu există poziții libere în AGENT SPECIAL (B18:B21)"
            
            elif member_range == 'agent_special':
                target_cells = ranges['agent_sas']
                for target_cell in target_cells:
                    if not target_cell.value or target_cell.value.strip() == "":
                        member_cell.value = ""
                        target_cell.value = discord_id
                        sheet.update_cells([member_cell, target_cell])
                        batch_data = [
                            {'range': member_cell.address, 'values': [[""]]},
                            {'range': target_cell.address, 'values': [[discord_id]]}
                        ]
                        sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
                        
                        # Get callsign from column D
                        callsign = sheet.cell(target_cell.row, 4).value or "N/A"
                        return True, f"Membru mutat la AGENT S.A.S: {callsign}"
                return False, "Nu există poziții libere în AGENT S.A.S (B23:B40)"
        
        return False, "Direcție invalidă"
    except Exception as e:
        logging.error(f"Error moving member in SAS excel: {e}")
        return False, f"Eroare: {str(e)}"
    

def remove_member_from_sas_excel(discord_id: str) -> tuple[bool, str]:
    """
    Remove member from SAS excel.
    Returns (success, message).
    """
    try:
        client = get_google_sheets_client()
        sheet = client.open_by_key(SAS_SPREADSHEET_ID).sheet1

        # Get range B7:B46
        cells = sheet.range('B7:B46')
        
        # Find and clear member
        found = False
        for cell in cells:
            if cell.value == discord_id:
                cell.value = ""
                sheet.update_cells([cell])
                found = True
                return True, f"Membru șters de la poziția {cell.address}"
        
        if not found:
            return False, "Membrul nu a fost găsit în listă"
    except Exception as e:
        logging.error(f"Error removing member from SAS excel: {e}")
        return False, f"Eroare: {str(e)}"

# --------------- Calendar UI ---------------
class DayButton(discord.ui.Button):
    def __init__(self, parent: "DayCalendarView", day: int, row: int):
        super().__init__(style=discord.ButtonStyle.secondary, label=str(day), row=row, custom_id=f"cal_day_{day}")
        self.parent_view = parent
        self.day = day

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        date_str = f"{self.parent_view.year:04d}-{self.parent_view.month:02d}-{self.day:02d}"
        view = ReportUserChoiceView(date_str, interaction.user.id, is_sas=self.parent_view.is_sas)
        await interaction.response.edit_message(
            embed=make_embed("Raport Zi - Selectează",
                             f"Data: {date_str}\nAlege toți sau un user.",
                             discord.Color.blurple(),
                             interaction.user),
            view=view
        )

class MonthNavButton(discord.ui.Button):
    def __init__(self, parent: "DayCalendarView", forward: bool):
        label = "Luna ▶" if forward else "◀ Luna"
        cid = f"cal_month_{'next' if forward else 'prev'}"
        super().__init__(style=discord.ButtonStyle.primary, label=label, row=0, custom_id=cid)
        self.parent_view = parent
        self.forward = forward

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        m = self.parent_view.month + (1 if self.forward else -1)
        y = self.parent_view.year
        if m == 0:
            m = 12
            y -= 1
        elif m == 13:
            m = 1
            y += 1
        self.parent_view.year = y
        self.parent_view.month = m
        self.parent_view.page = 0
        self.parent_view.rebuild()
        await interaction.response.edit_message(
            embed=self.parent_view.embed(interaction.user),
            view=self.parent_view
        )

class PageNavButton(discord.ui.Button):
    def __init__(self, parent: "DayCalendarView", forward: bool):
        label = "Pg ▶" if forward else "◀ Pg"
        cid = f"cal_page_{'next' if forward else 'prev'}"
        super().__init__(style=discord.ButtonStyle.secondary, label=label, row=0, custom_id=cid)
        self.parent_view = parent
        self.forward = forward

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        pages = self.parent_view.total_pages()
        if pages > 1:
            if self.forward:
                self.parent_view.page = (self.parent_view.page + 1) % pages
            else:
                self.parent_view.page = (self.parent_view.page - 1) % pages
            self.parent_view.rebuild()
            await interaction.response.edit_message(
                embed=self.parent_view.embed(interaction.user),
                view=self.parent_view
            )

class DayCalendarView(discord.ui.View):
    """
    Paginated month day picker.
    - Shows up to 20 day buttons (4 rows x 5 columns) per page.
    - Row 0 reserved for navigation buttons.
    """
    COLUMNS = 5
    ROWS = 4               # usable rows for days (1-4)
    PAGE_SIZE = COLUMNS * ROWS  # 20

    def __init__(self, requester_id: int, year: int, month: int, is_sas: bool = False):
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.year = year
        self.month = month
        self.page = 0
        self.is_sas = is_sas
        self.rebuild()

    def total_pages(self) -> int:
        days = calendar.monthrange(self.year, self.month)[1]
        return (days + self.PAGE_SIZE - 1) // self.PAGE_SIZE

    def embed(self, user: discord.abc.User):
        days = calendar.monthrange(self.year, self.month)[1]
        pages = self.total_pages()
        header = f"{calendar.month_name[self.month]} {self.year} (Pagina {self.page + 1}/{pages})"
        return make_embed("Raport Zi - Alege data", header, discord.Color.blurple(), user)

    def rebuild(self):
        self.clear_items()
        # Navigation (row 0)
        self.add_item(MonthNavButton(self, forward=False))
        self.add_item(MonthNavButton(self, forward=True))
        if self.total_pages() > 1:
            self.add_item(PageNavButton(self, forward=False))
            self.add_item(PageNavButton(self, forward=True))

        # Day buttons
        days_in_month = calendar.monthrange(self.year, self.month)[1]
        start_day = self.page * self.PAGE_SIZE + 1
        end_day = min(days_in_month, start_day + self.PAGE_SIZE - 1)
        current_row = 1
        col = 0
        for day in range(start_day, end_day + 1):
            row = 1 + ((day - start_day) // self.COLUMNS)  # 1..4
            btn = DayButton(self, day, row=row)
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

class ReportUserChoiceView(discord.ui.View):
    def __init__(self, date_str: str, requester_id: int, *, is_sas: bool = False):
        super().__init__(timeout=180)
        self.date_str = date_str
        self.requester_id = requester_id
        self.is_sas = is_sas

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Toți", style=discord.ButtonStyle.success, custom_id="report_all_btn")
    async def all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        title, lines = build_day_report(self.date_str, interaction.guild, is_sas=self.is_sas)
        await interaction.response.edit_message(
            embed=make_embed(title, "\n".join(lines)[:3900], discord.Color.green(), interaction.user),
            view=None
        )
        try:
            await log_command(interaction, "day-report-all", changed=False, extra=f"date={self.date_str}")
        except Exception:
            pass

    @discord.ui.button(label="Alege User", style=discord.ButtonStyle.primary, custom_id="report_pick_user_btn")
    async def pick_user_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Replace with user select
        view = ReportPickUserView(self.date_str, self.requester_id, is_sas=self.is_sas)
        await interaction.response.edit_message(
            embed=make_embed("Raport Zi - Alege user", f"Data: {self.date_str}\nSelectează un user.", discord.Color.blurple(), interaction.user),
            view=view
        )

class UserSelect(discord.ui.UserSelect):
    def __init__(self, parent: "ReportPickUserView"):
        super().__init__(placeholder="Selectează un user", min_values=1, max_values=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        member = self.values[0]
        title, lines = build_day_report(self.parent_view.date_str, interaction.guild, member, is_sas=self.parent_view.is_sas)
        await interaction.response.edit_message(
            embed=make_embed(title, "\n".join(lines)[:3900], discord.Color.green(), interaction.user),
            view=None
        )
        try:
            await log_command(
                interaction,
                "day-report-user",
                target=member,
                changed=False,
                extra=f"date={self.parent_view.date_str}"
            )
        except Exception:
            pass

class ReportPickUserView(discord.ui.View):
    def __init__(self, date_str: str, requester_id: int, *, is_sas: bool = False):
        super().__init__(timeout=180)
        self.date_str = date_str
        self.requester_id = requester_id
        self.is_sas = is_sas
        self.add_item(UserSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

class MyPontajeModal(discord.ui.Modal, title="Pontajele mele - Zi"):
    def __init__(self, user: discord.Member ,is_sas: bool = False):
        super().__init__(timeout=180)
        self.user = user
        self.is_sas = is_sas
        today = local_now().strftime("%Y-%m-%d")
        self.date_input = discord.ui.TextInput(
            label="Data (YYYY-MM-DD)",
            placeholder=today,  # auto placeholder = current day
            default=today,
            max_length=10,
            required=False
        )
        self.add_item(self.date_input)

    async def on_submit(self, interaction: discord.Interaction):
        # only the requester
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return

        raw = (self.date_input.value or "").strip()
        date_str = raw or local_now().strftime("%Y-%m-%d")

        # Validate date
        try:
            datetime.datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                embed=make_embed("Dată invalidă", "Format corect: YYYY-MM-DD", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        if(self.is_sas):
            sessions = get_clock_times_sas(self.user.id, date_str)
        else:
            sessions = get_clock_times(self.user.id, date_str)
        if not sessions:
            await interaction.response.send_message(
                embed=make_embed("Pontajele mele", f"{date_str}\nFără sesiuni.", discord.Color.orange(), interaction.user),
                ephemeral=True
            )
            try:
                await log_command(interaction, "my-pontaje", changed=False, extra=f"date={date_str} none")
            except Exception:
                pass
            return

        # Build details and total
        total = 0
        lines = []
        now = local_now()
        for idx, s in enumerate(sessions, start=1):
            ci_s, co_s = s[0], s[1]
            if ci_s:
                if co_s:
                    ci = parse_local(date_str, ci_s)
                    co = parse_local(date_str, co_s)
                    mins = (co - ci).total_seconds() / 60
                    r = round_minutes(mins)
                    if r > 0:
                        total += r
                    lines.append(f"{idx}. {ci_s} - {co_s} ({int(r)} min)")
                else:
                    # ongoing -> count up to now if same day
                    ci = parse_local(date_str, ci_s)
                    mins = (now - ci).total_seconds() / 60 if now.strftime("%Y-%m-%d") == date_str else 0
                    r = max(0, round_minutes(mins))
                    if r > 0:
                        total += r
                    lines.append(f"{idx}. {ci_s} - ... ({int(r)} min, activă)")
        if not lines:
            lines = ["Fără sesiuni valide."]
        title = f"Pontajele mele - {date_str} (Total: {int(total)} min)"
        await interaction.response.send_message(
            embed=make_embed(title, "\n".join(lines)[:3900], discord.Color.green(), interaction.user),
            ephemeral=True
        )
        try:
            if self.is_sas == False:
                await log_command(interaction, "my-pontaje", changed=False, extra=f"date={date_str} total={int(total)}")
            else:
                await log_command(interaction, "my-pontaje-sas", changed=False, extra=f"date={date_str} total={int(total)}")
        except Exception:
            pass

# --------------- Button View ---------------
class ClockButtons(discord.ui.View):
    """Persistent clock in/out buttons."""
    def __init__(self):
        super().__init__(timeout=None)

    async def _check_basic(self, interaction: discord.Interaction) -> bool:
        # Channel
        if not interaction.channel or interaction.channel.id != ALLOWED_CHANNEL_ID:
            await interaction.response.send_message(
                embed=make_embed("Canal invalid", f"Folosește în <#{ALLOWED_CHANNEL_ID}>.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return False
        # Roles
        if not has_any(interaction.user, REQUIRED_PD_ROLE_ID):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Ai nevoie de rol PD.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Clock IN", style=discord.ButtonStyle.success, custom_id="clock_in_btn")
    async def clock_in_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)  # ACK fast

        user_id = interaction.user.id
        now = local_now()
        if (now.hour == 23 and now.minute > 55):
            await interaction.followup.send(
                embed=make_embed("Clock IN", "Nu poți să te înregistrezi după ora 23:55. Așteaptă te rog până la 00:00", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        elif (now.hour == 5 and now.minute > 25 and now.minute < 30):
            await interaction.followup.send(
                embed=make_embed("Clock IN", "Nu poți să te înregistrezi înainte de ora 05:30. Așteaptă te rog până la 05:30", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        date_str = now.strftime("%Y-%m-%d")
        sessions = get_clock_times(user_id, date_str)
        for s in sessions:
            if s[1] is None:
                await interaction.followup.send(
                    embed=make_embed("Activ", f"Deja pornit la {s[0]}. Apasă Clock OUT.", discord.Color.orange(), interaction.user),
                    ephemeral=True
                )
                return
        try:
            add_clock_in(user_id, date_str, now.strftime("%H:%M:%S"))
        except sqlite3.OperationalError as e:
            if "database or disk is full" in str(e).lower():
                # try to reclaim space and retry once
                try:
                    checkpoint_and_vacuum()
                    add_clock_in(user_id, date_str, now.strftime("%H:%M:%S"))
                except Exception:
                    await interaction.followup.send(
                        embed=make_embed("Stocare plină", "Nu se poate salva în DB. Rulează VACUUM sau eliberează spațiu.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
            else:
                raise
        await interaction.followup.send(
            embed=make_embed("Clock IN", f"Start {now.strftime('%H:%M:%S')} ({date_str})", discord.Color.green(), interaction.user),
            ephemeral=True
        )
        try:
            await log_command(interaction, "clockin-button", changed=True, extra=f"time={now.strftime('%H:%M:%S')}")
        except Exception:
            pass

    @discord.ui.button(label="Clock OUT", style=discord.ButtonStyle.danger, custom_id="clock_out_btn")
    async def clock_out_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)  # ACK fast

        user_id = interaction.user.id
        now = local_now()
        date_str = now.strftime("%Y-%m-%d")
        sessions = get_clock_times(user_id, date_str)
        for s in sessions:
            if s[1] is None:
                update_clock_out(user_id, date_str, now.strftime("%H:%M:%S"))
                start_dt = parse_local(date_str, s[0])
                mins = minutes_diff(start_dt, now)
                rounded = round_minutes(mins)
                await interaction.followup.send(
                    embed=make_embed("Clock OUT", f"Stop {now.strftime('%H:%M:%S')}\nDurată: {rounded} minute", discord.Color.green(), interaction.user),
                    ephemeral=True
                )
                try:
                    await log_command(
                        interaction,
                        "clockout-button",
                        changed=True,
                        extra=f"start={s[0]} end={now.strftime('%H:%M:%S')} mins={rounded}"
                    )
                except Exception:
                    pass
                return
        await interaction.followup.send(
            embed=make_embed("Fără sesiune", "Nu ai sesiune activă.", discord.Color.orange(), interaction.user),
            ephemeral=True
        )
    
    @discord.ui.button(label="Pontajele Mele", style=discord.ButtonStyle.grey, custom_id="my_pontaje_btn")
    async def my_pontaje_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        # Open date modal with today placeholder
        await interaction.response.send_modal(MyPontajeModal(interaction.user, is_sas=False))

class HrButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _check_basic(self, interaction: discord.Interaction) -> bool:
        # Channel
        if not interaction.channel or interaction.channel.id != ALLOWED_CHANNEL_ID:
            await interaction.response.send_message(
                embed=make_embed("Canal invalid", f"Folosește în <#{ALLOWED_CHANNEL_ID}>.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return False
        # Roles
        if not hr_or_conducere_check():
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Ai nevoie de rol HR sau Conducere.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Pontaje Deschise", style=discord.ButtonStyle.grey, custom_id="clock_ongoing_btn")
    async def clock_ongoing_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        if not is_mgmt(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită HR sau Conducere.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        today = local_now().strftime("%Y-%m-%d")
        lines = []
        total = 0
        for uid, date_val, ci in get_ongoing_sessions():
            if date_val == today:  # only today
                member = interaction.guild.get_member(uid) if interaction.guild else None
                name = member.display_name if member else str(uid)
                lines.append(f"{name} - {ci}")
                total += 1
        if not lines:
            desc = "Nu există sesiuni active azi."
        else:
            desc = "\n".join(lines)[:3900]
        try:
            await interaction.response.send_message(
                embed=make_embed(f"Sesiuni active azi - Total: {total}", desc, discord.Color.blurple(), interaction.user),
                ephemeral=True
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                embed=make_embed(f"Sesiuni active azi - Total: {total}", desc, discord.Color.blurple(), interaction.user),
                ephemeral=True
            )
        # Log (INFO)
        try:
            await log_command(
                interaction,
                "ongoing-button",
                changed=False,
                extra=f"count={len(lines)} date={today}"
            )
        except Exception:
            pass
        
    @discord.ui.button(label="Opreste Pontaje", style=discord.ButtonStyle.grey, custom_id="clock_ongoing_stop_btn")
    async def clock_ongoing_stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Must be in allowed channel and management
        if not await self._check_basic(interaction):
            return
        if not is_mgmt(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită HR sau Conducere.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        today = local_now().strftime("%Y-%m-%d")
        sessions = [(uid, date_val, ci) for uid, date_val, ci in get_ongoing_sessions() if date_val == today]
        if not sessions:
            await interaction.response.send_message(
                embed=make_embed("Opreste Pontaje", "Nu există sesiuni active azi.", discord.Color.blue(), interaction.user),
                ephemeral=True
            )
            return
        view = OngoingStopView(sessions, interaction.user.id, today, is_sas=False)
        await interaction.response.send_message(
            embed=make_embed("Opreste Pontaje", f"Sesiuni active azi ({len(sessions)}). Apasă Stop pentru a elimina.", discord.Color.orange(), interaction.user),
            view=view,
            ephemeral=True
        )
        try:
            await log_command(
                interaction,
                "ongoing-stop-panel",
                changed=False,
                extra=f"count={len(sessions)}"
            )
        except Exception:
            pass   
    
    @discord.ui.button(label="Warn", style=discord.ButtonStyle.grey, custom_id="warn_panel_btn")
    async def warn_panel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Must be in ALLOWED_CHANNEL_ID
        if not interaction.channel or interaction.channel.id != ALLOWED_CHANNEL_ID:
            await interaction.response.send_message(
                embed=make_embed("Canal invalid", f"Folosește în <#{ALLOWED_CHANNEL_ID}>.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        if not is_mgmt(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită HR sau Conducere.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        view = WarnUserSelectView(interaction.user.id)
        await interaction.response.send_message(
            embed=make_embed("Warn Panel", "Selectează userul pentru acțiuni (Add / Status / Reset).", discord.Color.blurple(), interaction.user),
            view=view,
            ephemeral=True
        )
        try:
            await log_command(interaction, "warn-panel-open", changed=False)
        except Exception:
            pass
    
    @discord.ui.button(label="Pontaje / ZI", style=discord.ButtonStyle.grey, custom_id="day_report_btn")
    async def day_report_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        if not is_mgmt(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită HR sau Conducere.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        now = local_now()
        view = DayCalendarView(interaction.user.id, now.year, now.month, is_sas=False)
        try:
            await interaction.response.send_message(
                embed=view.embed(interaction.user),
                view=view,
                ephemeral=True
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                embed=view.embed(interaction.user),
                view=view,
                ephemeral=True
            )
        try:
            await log_command(interaction, "pontaje", changed=False, extra=f"month={now.month} year={now.year}")
        except Exception:
            pass
    
    @discord.ui.button(label="Adaugă Minute", style=discord.ButtonStyle.grey, custom_id="add_minutes_btn")
    async def add_minutes_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Channel + perms
        if not interaction.channel or interaction.channel.id != ALLOWED_CHANNEL_ID:
            await interaction.response.send_message(
                embed=make_embed("Canal invalid", f"Folosește în <#{ALLOWED_CHANNEL_ID}>.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        if not is_mgmt(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită HR sau Conducere.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        view = AddMinutesUserSelectView(interaction.user.id, is_sas=False)
        await interaction.response.send_message(
            embed=make_embed("Adaugă Minute", "Selectează userul pentru care adaugi minute.", discord.Color.blurple(), interaction.user),
            view=view,
            ephemeral=True
        )
        try:
            await log_command(interaction, "adaugaminute-panel-open", changed=False)
        except Exception:
            pass
    
    @discord.ui.button(label="Șterge Pontaj", style=discord.ButtonStyle.grey, custom_id="rmv_panel_btn")
    async def rmv_panel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Channel + perms
        if not interaction.channel or interaction.channel.id != ALLOWED_CHANNEL_ID:
            await interaction.response.send_message(
                embed=make_embed("Canal invalid", f"Folosește în <#{ALLOWED_CHANNEL_ID}>.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        if not is_mgmt(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită HR sau Conducere.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        view = RemovePontajUserSelectView(interaction.user.id)
        await interaction.response.send_message(
            embed=make_embed("Șterge Pontaj", "Selectează userul apoi introdu data (YYYY-MM-DD) pentru a alege sesiunea de șters.", discord.Color.blurple(), interaction.user),
            view=view,
            ephemeral=True
        )
        try:
            await log_command(interaction, "stergepontaj-panel-open", changed=False)
        except Exception:
            pass

class SASClockButtons(discord.ui.View):
    """Persistent SAS clock buttons."""
    def __init__(self):
        super().__init__(timeout=None)

    async def _check_basic(self, interaction: discord.Interaction) -> bool:
        if not interaction.channel or interaction.channel.id != SAS_CHANNEL_ID:
            await interaction.response.send_message(
                embed=make_embed("Canal invalid", f"Folosește în <#{SAS_CHANNEL_ID}>.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return False
        if not has_role(interaction.user, SAS_ROLE_IDS):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Ai nevoie de rol SAS.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="SAS IN", style=discord.ButtonStyle.success, custom_id="sas_clock_in_btn")
    async def sas_clock_in_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        uid = interaction.user.id
        now = local_now()
        if (now.hour == 23 and now.minute > 55):
            await interaction.followup.send(
                embed=make_embed("Clock IN", "Nu poți să te înregistrezi după ora 23:55. Așteaptă te rog până la 00:00", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        elif (now.hour == 5 and now.minute > 25 and now.minute < 30):
            await interaction.followup.send(
                embed=make_embed("Clock IN", "Nu poți să te înregistrezi înainte de ora 05:30. Așteaptă te rog până la 05:30", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        date = now.strftime("%Y-%m-%d")
        sessions = get_clock_times_sas(uid, date)
        if any(s[1] is None for s in sessions):
            await interaction.followup.send(
                embed=make_embed("Activ", "Deja ai o sesiune SAS. Apasă SAS OUT.", discord.Color.orange(), interaction.user),
                ephemeral=True
            )
            return
        try:
            add_clock_in_sas(uid, date, now.strftime("%H:%M:%S"))
        except sqlite3.OperationalError as e:
            if "database or disk is full" in str(e).lower():
                # try to reclaim space and retry once
                try:
                    checkpoint_and_vacuum()
                    add_clock_in_sas(uid, date, now.strftime("%H:%M:%S"))
                except Exception:
                    await interaction.followup.send(
                        embed=make_embed("Stocare plină", "Nu se poate salva în DB. Rulează VACUUM sau eliberează spațiu.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
            else:
                raise
        await interaction.followup.send(
            embed=make_embed("SAS IN", f"Start {now.strftime('%H:%M:%S')} ({date})", discord.Color.green(), interaction.user),
            ephemeral=True
        )
        await log_command(interaction, "sasclockin-button", changed=True, extra=f"time={now.strftime('%H:%M:%S')}")

    @discord.ui.button(label="SAS OUT", style=discord.ButtonStyle.danger, custom_id="sas_clock_out_btn")
    async def sas_clock_out_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        uid = interaction.user.id
        now = local_now()
        date = now.strftime("%Y-%m-%d")
        sessions = get_clock_times_sas(uid, date)
        for s in sessions:
            if s[1] is None:
                update_clock_out_sas(uid, date, now.strftime("%H:%M:%S"))
                start_dt = parse_local(date, s[0])
                mins = minutes_diff(start_dt, now)
                rounded = round_minutes(mins)
                await interaction.followup.send(
                    embed=make_embed("SAS OUT", f"Stop {now.strftime('%H:%M:%S')}\nDurată: {rounded} minute", discord.Color.green(), interaction.user),
                    ephemeral=True
                )
                await log_command(
                    interaction,
                    "sasclockout-button",
                    changed=True,
                    extra=f"start={s[0]} end={now.strftime('%H:%M:%S')} mins={rounded}"
                )
                return
        await interaction.followup.send(
            embed=make_embed("Fără sesiune", "Nu ai sesiune SAS activă.", discord.Color.orange(), interaction.user),
            ephemeral=True
        )

    @discord.ui.button(label="Pontajele Mele", style=discord.ButtonStyle.grey, custom_id="my_pontaje_sas_btn")
    async def my_pontaje_sas_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        # Open date modal with today placeholder
        await interaction.response.send_modal(MyPontajeModal(interaction.user, is_sas=True))

class SASCoordonatorButtons(discord.ui.View):
    """Persistent SAS clock buttons."""
    def __init__(self):
        super().__init__(timeout=None)

    async def _check_basic(self, interaction: discord.Interaction) -> bool:
        if not interaction.channel or interaction.channel.id != SAS_CHANNEL_ID:
            await interaction.response.send_message(
                embed=make_embed("Canal invalid", f"Folosește în <#{SAS_CHANNEL_ID}>.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return False
        if not has_role(interaction.user, SAS_COORDONATOR_IDS):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Ai nevoie de rol SAS Coordonator.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Evidență Acțiune", style=discord.ButtonStyle.grey, custom_id="sas_action_log_btn")
    async def sas_action_log_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        await interaction.response.send_modal(SASActionModal(interaction.user))

    @discord.ui.button(label="Pontaje Deschise", style=discord.ButtonStyle.grey, custom_id="clock_ongoing_sas_btn")
    async def clock_ongoing_sas_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        if not is_csas(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită COORDONATOR SAS", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        today = local_now().strftime("%Y-%m-%d")
        lines = []
        total = 0
        for uid, date_val, ci in get_ongoing_sessions_sas():
            if date_val == today:  # only today
                member = interaction.guild.get_member(uid) if interaction.guild else None
                name = member.display_name if member else str(uid)
                lines.append(f"{name} - {ci}")
                total += 1
        if not lines:
            desc = "Nu există sesiuni active azi."
        else:
            desc = "\n".join(lines)[:3900]
        try:
            await interaction.response.send_message(
                embed=make_embed(f"Sesiuni active azi - Total: {total}", desc, discord.Color.blurple(), interaction.user),
                ephemeral=True
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                embed=make_embed(f"Sesiuni active azi - Total: {total}", desc, discord.Color.blurple(), interaction.user),
                ephemeral=True
            )
        # Log (INFO)
        try:
            await log_command(
                interaction,
                "ongoingsas-button",
                changed=False,
                extra=f"count={len(lines)} date={today}"
            )
        except Exception:
            pass

    @discord.ui.button(label="Opreste Pontaje", style=discord.ButtonStyle.grey, custom_id="clock_ongoing_stop_sas_btn")
    async def clock_ongoing_stop_sas_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Must be in allowed channel and management
        if not await self._check_basic(interaction):
            return
        if not is_csas(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită COORDONATOR SAS", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        today = local_now().strftime("%Y-%m-%d")
        sessions = [(uid, date_val, ci) for uid, date_val, ci in get_ongoing_sessions_sas() if date_val == today]
        if not sessions:
            await interaction.response.send_message(
                embed=make_embed("Opreste Pontaje", "Nu există sesiuni active azi.", discord.Color.blue(), interaction.user),
                ephemeral=True
            )
            return
        view = OngoingStopView(sessions, interaction.user.id, today, is_sas=True)
        await interaction.response.send_message(
            embed=make_embed("Opreste Pontaje", f"Sesiuni active azi ({len(sessions)}). Apasă Stop pentru a elimina.", discord.Color.orange(), interaction.user),
            view=view,
            ephemeral=True
        )
        try:
            await log_command(
                interaction,
                "ongoingsas-stop-panel",
                changed=False,
                extra=f"count={len(sessions)}"
            )
        except Exception:
            pass   

    @discord.ui.button(label="Pontaje / ZI", style=discord.ButtonStyle.grey, custom_id="day_report_sas_btn")
    async def day_report_sas_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        if not is_csas(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită COORDONATOR SAS.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        now = local_now()
        view = DayCalendarView(interaction.user.id, now.year, now.month, is_sas=True)
        try:
            await interaction.response.send_message(
                embed=view.embed(interaction.user),
                view=view,
                ephemeral=True
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                embed=view.embed(interaction.user),
                view=view,
                ephemeral=True
            )
        try:
            await log_command(interaction, "pontaje-sas", changed=False, extra=f"month={now.month} year={now.year}")
        except Exception:
            pass

    @discord.ui.button(label="Adaugă Minute", style=discord.ButtonStyle.grey, custom_id="add_minutes_sas_btn")
    async def add_minutes_sas_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Channel + perms
        if not interaction.channel or interaction.channel.id != SAS_CHANNEL_ID:
            await interaction.response.send_message(
                embed=make_embed("Canal invalid", f"Folosește în <#{SAS_CHANNEL_ID}>.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        if not is_csas(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită Coordonator SAS.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        view = AddMinutesUserSelectView(interaction.user.id, is_sas=True)
        await interaction.response.send_message(
            embed=make_embed("Adaugă Minute", "Selectează userul pentru care adaugi minute.", discord.Color.blurple(), interaction.user),
            view=view,
            ephemeral=True
        )
        try:
            await log_command(interaction, "adaugaminute-sas-panel-open", changed=False)
        except Exception:
            pass

    @discord.ui.button(label="Pontaje / Săptămânale", style=discord.ButtonStyle.grey, custom_id="week_report_sas_btn")
    async def week_report_sas_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        if not is_csas(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Necesită COORDONATOR SAS.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        
        # Show week selection view instead of generating report immediately
        view = WeekSelectionView(interaction.user.id)
        await interaction.response.send_message(
            embed=make_embed(
                "Selectează Săptămâna",
                "Alege săptămâna pentru raport:",
                discord.Color.blurple(),
                interaction.user
            ),
            view=view,
            ephemeral=True
        )

class WeekSelectionView(discord.ui.View):
    """View to select current or previous week for weekly report."""
    def __init__(self, requester_id: int):
        super().__init__(timeout=180)
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Săptămâna Curentă", style=discord.ButtonStyle.primary, custom_id="current_week_btn")
    async def current_week_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._generate_week_report(interaction, weeks_ago=0)

    @discord.ui.button(label="Săptămâna Trecută", style=discord.ButtonStyle.secondary, custom_id="previous_week_btn")
    async def previous_week_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._generate_week_report(interaction, weeks_ago=1)

    async def _generate_week_report(self, interaction: discord.Interaction, weeks_ago: int):
        # Defer response as this might take time
        await interaction.response.defer(ephemeral=True)
        
        # Calculate the reference date
        reference_date = local_now() - datetime.timedelta(weeks=weeks_ago)
        
        week_label, week_dates = _get_week_dates(reference_date)
        lines = build_week_report_sas(interaction.guild, week_dates)
        
        # Create well-formatted .txt file content
        report_lines = []
        
        # Add header with title and date range
        report_lines.append("=" * 80)
        report_lines.append(f"RAPORT SĂPTĂMÂNAL SAS - {week_label}".center(80))
        report_lines.append("=" * 80)
        report_lines.append("")
        
        # Add the table (already formatted from build_week_report_sas)
        report_lines.extend(lines)
        
        # Add footer with generation info
        report_lines.append("")
        report_lines.append("-" * 80)
        report_lines.append(f"Generat la: {local_now().strftime('%d/%m/%Y %H:%M:%S')}")
        report_lines.append(f"Generat de: {interaction.user.display_name}")
        report_lines.append(f"Total membri: {len(lines) - 2}")  # Exclude header + separator
        report_lines.append("=" * 80)
        
        report_text = "\n".join(report_lines)
        
        # Create file buffer with UTF-8 encoding (with BOM for better Windows compatibility)
        file_buffer = io.BytesIO(report_text.encode('utf-8-sig'))
        file_buffer.seek(0)
        
        # Create Discord file
        filename = f"Raport_SAS_{week_label.replace(' ', '_').replace('.', '_')}.txt"
        discord_file = discord.File(file_buffer, filename=filename)
        
        # Send file
        await interaction.followup.send(
            embed=make_embed(
                f"📊 Raport Săptămânal SAS - {week_label}",
                f"**Raport generat pentru {len(lines) - 2} membri.**\n\n"
                f"Descarcă fișierul `.txt` de mai jos pentru a vizualiza raportul formatat.\n"
                f"📁 Poate fi deschis în Notepad, Excel, sau orice editor de text.",
                discord.Color.green(),
                interaction.user
            ),
            file=discord_file,
            ephemeral=True
        )
        
        try:
            await log_command(
                interaction,
                "week-report-sas",
                changed=False,
                extra=f"week={week_label} weeks_ago={weeks_ago} format=txt members={len(lines)-2}"
            )
        except Exception:
            pass

class RelayButtons(discord.ui.View):
    def __init__(self, ):
        super().__init__(timeout=None)
    
    async def _check_basic(self, interaction: discord.Interaction) -> bool:
        OWNER_ID = 286492096242909185
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("Permisiune refuzată.", ephemeral=True)
            return
        return True

    @discord.ui.button(label="Say", style=discord.ButtonStyle.grey, custom_id="relay_say_btn")
    async def relay_open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        
        # Prevent duplicate drafts
        existing = bot.relay_sessions.get(interaction.user.id)
        if existing:
            url = f"https://discord.com/channels/{interaction.guild.id}/{existing['draft_channel_id']}/{existing['draft_id']}"
            await interaction.response.send_message(f"Ai deja un draft: {url}", ephemeral=True)
            return
        # Create draft in this channel
        try:
            await _relay_start_session(interaction.user, interaction.channel)
            await interaction.response.send_message("Draft relay creat.", ephemeral=True)
        except Exception:
            await interaction.response.send_message("Eroare la crearea draftului.", ephemeral=True)
    

    @discord.ui.button(label="Close", style=discord.ButtonStyle.grey, custom_id="relay_close_btn")
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        existing = bot.relay_sessions.get(interaction.user.id)
        if existing:
            await _relay_end_session(interaction.user.id)
            await interaction.response.send_message("Draft relay închis.", ephemeral=True)
        else:
            await interaction.response.send_message("Nu ai un draft activ.", ephemeral=True)

    @discord.ui.button(label="Console Start", style=discord.ButtonStyle.grey, custom_id="relay_console_start")
    async def console_start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        try:
            await bot.loop.run_in_executor(None, _drain_stdin, 0.2)
        except Exception:
            pass
        bot.console_relay_enabled = True
        if bot._console_task is None or bot._console_task.done():
            bot._console_task = bot.loop.create_task(bot._console_relay())
        await interaction.response.send_message(
            embed=make_embed("Console Relay", f"Pornit. Canal: <#{bot.console_relay_channel_id}>", discord.Color.green(), interaction.user),
            ephemeral=True
        )
        try:
            await log_command(interaction, "console-relay-start", changed=False, extra=f"channel={bot.console_relay_channel_id}")
        except Exception:
            pass

    @discord.ui.button(label="Console Stop", style=discord.ButtonStyle.grey, custom_id="relay_console_stop")
    async def console_stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        bot.console_relay_enabled = False
        await interaction.response.send_message(
            embed=make_embed("Console Relay", "Oprit.", discord.Color.orange(), interaction.user),
            ephemeral=True
        )
        try:
            await log_command(interaction, "console-relay-stop", changed=False)
        except Exception:
            pass

    @discord.ui.button(label="Console Status", style=discord.ButtonStyle.grey, custom_id="relay_console_status")
    async def console_status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        ch = bot.get_channel(bot.console_relay_channel_id) if bot.console_relay_channel_id else None
        status = "Pornit" if bot.console_relay_enabled else "Oprit"
        where = ch.mention if isinstance(ch, discord.TextChannel) else "nesetat"
        await interaction.response.send_message(
            embed=make_embed("Console Relay", f"Status: {status}\nCanal: {where}", discord.Color.blurple(), interaction.user),
            ephemeral=True
        )

class SASMemberManagementButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _check_basic(self, interaction: discord.Interaction) -> bool:
        if not interaction.channel or interaction.channel.id != SAS_EVIDENTA_CHANNEL_ID:
            await interaction.response.send_message(
                embed=make_embed("Canal invalid", f"Folosește în <#{SAS_EVIDENTA_CHANNEL_ID}>.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return False
        if not has_role(interaction.user, SAS_COORDONATOR_IDS):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Ai nevoie de rol SAS Coordonator.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Add Member", style=discord.ButtonStyle.success, custom_id="sas_member_add_btn")
    async def add_member_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        view = SASMemberSelectView(interaction.user.id, action="add")
        await interaction.response.send_message(
            embed=make_embed("Adaugă Membru SAS", "Selectează membrul pentru a-l adăuga în evidență.", discord.Color.blurple(), interaction.user),
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="Up Button", style=discord.ButtonStyle.primary, custom_id="sas_member_up_btn")
    async def up_member_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        view = SASMemberSelectView(interaction.user.id, action="up")
        await interaction.response.send_message(
            embed=make_embed("Mută Membru Sus", "Selectează membrul pentru a-l promova.", discord.Color.blurple(), interaction.user),
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="Down Button", style=discord.ButtonStyle.primary, custom_id="sas_member_down_btn")
    async def down_member_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        view = SASMemberSelectView(interaction.user.id, action="down")
        await interaction.response.send_message(
            embed=make_embed("Mută Membru Jos", "Selectează membrul pentru a-l muta în jos.", discord.Color.blurple(), interaction.user),
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="Out Button", style=discord.ButtonStyle.danger, custom_id="sas_member_out_btn")
    async def out_member_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_basic(interaction):
            return
        view = SASMemberSelectView(interaction.user.id, action="out")
        await interaction.response.send_message(
            embed=make_embed("Șterge Membru SAS", "Selectează membrul pentru a-l șterge din evidență.", discord.Color.orange(), interaction.user),
            view=view,
            ephemeral=True
        )

class SASMemberUserSelect(discord.ui.UserSelect):
    def __init__(self, parent: "SASMemberSelectView", action: str):
        super().__init__(placeholder="Selectează user", min_values=1, max_values=1)
        self.parent_view = parent
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        
        member = self.values[0]
        
        # Defer response for long operations
        await interaction.response.defer(ephemeral=True)
        
        try:
            if self.action == "add":
                # Extract callsign
                callsign = _extract_pd_callsign(member)
                if not callsign:
                    await interaction.followup.send(
                        embed=make_embed("Eroare", f"Nu s-a putut extrage callsign-ul pentru {member.mention}. Format așteptat: [xxx]", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                # Get PD ID from PD excel
                pd_id = get_pd_id_by_callsign(callsign)
                if not pd_id:
                    await interaction.followup.send(
                        embed=make_embed("Eroare", f"Callsign-ul {callsign} nu a fost găsit în spreadsheet-ul PD.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                # Add to SAS excel
                success, message = add_member_to_sas_excel(pd_id)
                color = discord.Color.green() if success else discord.Color.red()
                await interaction.followup.send(
                    embed=make_embed("Add Member" if success else "Eroare", f"{member.mention} ({callsign})\nID PD: {pd_id}\n{message}", color, interaction.user),
                    ephemeral=True
                )
                
                if success:
                    await log_command(interaction, "sas-member-add", target=member, changed=True, extra=f"callsign={callsign} pd_id={pd_id}")
                    # Send public notification
                    if SAS_MEMBER_NOTIFICATIONS_CHANNEL_ID:
                        notif_ch = bot.get_channel(SAS_MEMBER_NOTIFICATIONS_CHANNEL_ID)
                        if notif_ch:
                            notif_embed = discord.Embed(
                                title="✅ Membru Adăugat în Evidență SAS",
                                description=(
                                    f"**Membru:** {member.mention}\n"
                                    f"**Callsign PD:** {callsign}\n"
                                    f"**Callsign SAS:** {message}\n"
                                    f"**Adăugat de:** {interaction.user.mention}"
                                ),
                                color=discord.Color.green(),
                                timestamp=local_now()
                            )
                            await notif_ch.send(embed=notif_embed)
            
            elif self.action == "up":
                # Extract callsign and get PD ID
                callsign = _extract_pd_callsign(member)
                if not callsign:
                    await interaction.followup.send(
                        embed=make_embed("Eroare", f"Nu s-a putut extrage callsign-ul pentru {member.mention}.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                pd_id = get_pd_id_by_callsign(callsign)
                if not pd_id:
                    await interaction.followup.send(
                        embed=make_embed("Eroare", f"Callsign-ul {callsign} nu a fost găsit în spreadsheet-ul PD.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                # Find current range
                client = get_google_sheets_client()
                sheet = client.open_by_key(SAS_SPREADSHEET_ID).sheet1
                
                ranges = {
                    'coordonator_sas': sheet.range('B11:B12'),
                    'coordonator_teste': sheet.range('B14:B16'),
                    'agent_special': sheet.range('B18:B21'),
                    'agent_sas': sheet.range('B23:B40')
                }
                
                member_range = None
                for range_name, cells in ranges.items():
                    for cell in cells:
                        if cell.value == pd_id:
                            member_range = range_name
                            break
                    if member_range:
                        break
                
                if not member_range:
                    await interaction.followup.send(
                        embed=make_embed("Eroare", "Membrul nu a fost găsit în evidență.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                if member_range == 'coordonator_sas':
                    await interaction.followup.send(
                        embed=make_embed("Info", "Membrul este deja la Coordonator SAS (cel mai înalt rang).", discord.Color.orange(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                # Show role selection view
                role_view = SASRoleSelectView(self.parent_view.requester_id, member, member_range)
                if not role_view.available_roles:
                    await interaction.followup.send(
                        embed=make_embed("Info", "Nu există ranguri disponibile pentru promovare.", discord.Color.orange(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                await interaction.followup.send(
                    embed=make_embed("Selectează Rang", f"{member.mention} ({callsign})\nSelectează rangul țintă pentru promovare:", discord.Color.blurple(), interaction.user),
                    view=role_view,
                    ephemeral=True
                )
            
            elif self.action == "down":
                # Extract callsign and get PD ID
                callsign = _extract_pd_callsign(member)
                if not callsign:
                    await interaction.followup.send(
                        embed=make_embed("Eroare", f"Nu s-a putut extrage callsign-ul pentru {member.mention}.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                pd_id = get_pd_id_by_callsign(callsign)
                if not pd_id:
                    await interaction.followup.send(
                        embed=make_embed("Eroare", f"Callsign-ul {callsign} nu a fost găsit în spreadsheet-ul PD.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                # Find current range
                client = get_google_sheets_client()
                sheet = client.open_by_key(SAS_SPREADSHEET_ID).sheet1
                
                ranges = {
                    'coordonator_sas': sheet.range('B11:B12'),
                    'coordonator_teste': sheet.range('B14:B16'),
                    'agent_special': sheet.range('B18:B21'),
                    'agent_sas': sheet.range('B23:B40')
                }
                
                member_range = None
                for range_name, cells in ranges.items():
                    for cell in cells:
                        if cell.value == pd_id:
                            member_range = range_name
                            break
                    if member_range:
                        break
                
                if not member_range:
                    await interaction.followup.send(
                        embed=make_embed("Eroare", "Membrul nu a fost găsit în evidență.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                if member_range == 'agent_sas':
                    await interaction.followup.send(
                        embed=make_embed("Info", "Membrul este deja la AGENT S.A.S (cel mai jos rang).", discord.Color.orange(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                # Show role selection view for demotion
                role_view = SASRoleSelectView(self.parent_view.requester_id, member, member_range, is_demotion=True)
                if not role_view.available_roles:
                    await interaction.followup.send(
                        embed=make_embed("Info", "Nu există ranguri disponibile pentru retrogradare.", discord.Color.orange(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                await interaction.followup.send(
                    embed=make_embed("Selectează Rang", f"{member.mention} ({callsign})\nSelectează rangul țintă pentru retrogradare:", discord.Color.blurple(), interaction.user),
                    view=role_view,
                    ephemeral=True
                )
            
            elif self.action == "out":
                # Extract callsign and get PD ID
                callsign = _extract_pd_callsign(member)
                if not callsign:
                    await interaction.followup.send(
                        embed=make_embed("Eroare", f"Nu s-a putut extrage callsign-ul pentru {member.mention}.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                pd_id = get_pd_id_by_callsign(callsign)
                if not pd_id:
                    await interaction.followup.send(
                        embed=make_embed("Eroare", f"Callsign-ul {callsign} nu a fost găsit în spreadsheet-ul PD.", discord.Color.red(), interaction.user),
                        ephemeral=True
                    )
                    return
                
                # Remove member
                success, message = remove_member_from_sas_excel(pd_id)
                color = discord.Color.green() if success else discord.Color.orange()
                await interaction.followup.send(
                    embed=make_embed("Remove Member" if success else "Info", f"{member.mention} ({callsign})\n{message}", color, interaction.user),
                    ephemeral=True
                )
                
                if success:
                    await log_command(interaction, "sas-member-out", target=member, changed=True, extra=f"callsign={callsign} pd_id={pd_id}")
                    # Send public notification
                    if SAS_MEMBER_NOTIFICATIONS_CHANNEL_ID:
                        notif_ch = bot.get_channel(SAS_MEMBER_NOTIFICATIONS_CHANNEL_ID)
                        if notif_ch:
                            notif_embed = discord.Embed(
                                title="❌ Membru Șters din Evidență SAS",
                                description=(
                                    f"**Membru:** {member.mention}\n"
                                    f"**Callsign PD:** {callsign}\n"
                                    f"**Șters de:** {interaction.user.mention}"
                                ),
                                color=discord.Color.red(),
                                timestamp=local_now()
                            )
                            await notif_ch.send(embed=notif_embed)
        
        except Exception as e:
            logging.exception(f"Error in SAS member management: {e}")
            await interaction.followup.send(
                embed=make_embed("Eroare", f"A apărut o eroare: {str(e)}", discord.Color.red(), interaction.user),
                ephemeral=True
            )

class SASRoleSelectView(discord.ui.View):
    """View to select target role for promotion or demotion."""
    def __init__(self, requester_id: int, member: discord.Member, current_range: str, is_demotion: bool = False):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.member = member
        self.current_range = current_range
        self.is_demotion = is_demotion
        
        # Define available promotions/demotions based on current range
        self.available_roles = self._get_available_roles()
        
        if not self.available_roles:
            # No promotions/demotions available
            return
        
        # Create select menu
        options = [
            discord.SelectOption(label=label, value=value, description=desc)
            for label, value, desc in self.available_roles
        ]
        self.role_select = discord.ui.Select(
            placeholder="Selectează rangul țintă",
            options=options,
            min_values=1,
            max_values=1
        )
        self.role_select.callback = self.role_select_callback
        self.add_item(self.role_select)
    
    def _get_available_roles(self) -> list[tuple[str, str, str]]:
        """Returns list of (label, value, description) for available promotions/demotions."""
        if self.is_demotion:
            # Demotions (reversed)
            demotions = {
                'coordonator_sas': [
                    ('Coordonator SAS - TESTE', 'coordonator_teste', 'B14:B16'),
                    ('AGENT SPECIAL', 'agent_special', 'B18:B21 - ALPHA/OMEGA/DELTA/TITAN'),
                    ('AGENT S.A.S', 'agent_sas', 'B23:B40')
                ],
                'coordonator_teste': [
                    ('AGENT SPECIAL', 'agent_special', 'B18:B21 - ALPHA/OMEGA/DELTA/TITAN'),
                    ('AGENT S.A.S', 'agent_sas', 'B23:B40')
                ],
                'agent_special': [
                    ('AGENT S.A.S', 'agent_sas', 'B23:B40')
                ],
                'agent_sas': []  # Already at bottom
            }
            return demotions.get(self.current_range, [])
        else:
            # Promotions
            promotions = {
                'agent_sas': [
                    ('AGENT SPECIAL', 'agent_special', 'B18:B21 - ALPHA/OMEGA/DELTA/TITAN'),
                    ('Coordonator SAS - TESTE', 'coordonator_teste', 'B14:B16'),
                    ('Coordonator SAS', 'coordonator_sas', 'B11:B12')
                ],
                'agent_special': [
                    ('Coordonator SAS - TESTE', 'coordonator_teste', 'B14:B16'),
                    ('Coordonator SAS', 'coordonator_sas', 'B11:B12')
                ],
                'coordonator_teste': [
                    ('Coordonator SAS', 'coordonator_sas', 'B11:B12')
                ],
                'coordonator_sas': []  # Already at top
            }
            return promotions.get(self.current_range, [])
    
    async def role_select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        
        target_range = self.role_select.values[0]
        
        # Defer response
        await interaction.response.defer(ephemeral=True)
        
        # Extract callsign and get PD ID
        callsign = _extract_pd_callsign(self.member)
        if not callsign:
            await interaction.followup.send(
                embed=make_embed("Eroare", f"Nu s-a putut extrage callsign-ul pentru {self.member.mention}.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        
        pd_id = get_pd_id_by_callsign(callsign)
        if not pd_id:
            await interaction.followup.send(
                embed=make_embed("Eroare", f"Callsign-ul {callsign} nu a fost găsit în spreadsheet-ul PD.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        
        # Move member to selected range
        success, message = move_member_to_specific_range(pd_id, target_range)
        color = discord.Color.green() if success else discord.Color.orange()
        
        await interaction.followup.send(
            embed=make_embed("Move Member" if success else "Info", f"{self.member.mention} ({callsign})\n{message}", color, interaction.user),
            ephemeral=True
        )
        
        if success:
            action = "sas-member-down" if self.is_demotion else "sas-member-up"
            await log_command(interaction, action, target=self.member, changed=True, extra=f"callsign={callsign} pd_id={pd_id} target={target_range}")
            # Send public notification
            if SAS_MEMBER_NOTIFICATIONS_CHANNEL_ID:
                notif_ch = bot.get_channel(SAS_MEMBER_NOTIFICATIONS_CHANNEL_ID)
                if notif_ch:
                    role_names = {
                        'agent_special': 'AGENT SPECIAL',
                        'coordonator_teste': 'Coordonator SAS - TESTE',
                        'coordonator_sas': 'Coordonator SAS',
                        'agent_sas': 'AGENT S.A.S'
                    }
                    target_name = role_names.get(target_range, target_range)
                    emoji = "⬇️" if self.is_demotion else "⬆️"
                    action_text = "Retrogradat" if self.is_demotion else "Promovat"
                    notif_embed = discord.Embed(
                        title=f"{emoji} Membru {action_text}",
                        description=(
                            f"**Membru:** {self.member.mention}\n"
                            f"**Callsign PD:** {callsign}\n"
                            f"**Rang nou:** {target_name}\n"
                            f"**Poziție:** {message}\n"
                            f"**{action_text} de:** {interaction.user.mention}"
                        ),
                        color=discord.Color.blue(),
                        timestamp=local_now()
                    )
                    await notif_ch.send(embed=notif_embed)
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

def move_member_to_specific_range(discord_id: str, target_range_name: str) -> tuple[bool, str]:
    """
    Move member to a specific target range.
    target_range_name: 'agent_special', 'coordonator_teste', or 'coordonator_sas'
    """
    try:
        client = get_google_sheets_client()
        sheet = client.open_by_key(SAS_SPREADSHEET_ID).sheet1
        
        ranges = {
            'coordonator_sas': sheet.range('B11:B12'),
            'coordonator_teste': sheet.range('B14:B16'),
            'agent_special': sheet.range('B18:B21'),
            'agent_sas': sheet.range('B23:B40')
        }
        
        # Find current position
        member_cell = None
        for cells in ranges.values():
            for cell in cells:
                if cell.value == discord_id:
                    member_cell = cell
                    break
            if member_cell:
                break
        
        if not member_cell:
            return False, "Membrul nu a fost găsit în niciun interval"
        
        # Get target range
        if target_range_name not in ranges:
            return False, "Rang țintă invalid"
        
        target_cells = ranges[target_range_name]
        
        # Find empty slot in target range
        for target_cell in target_cells:
            if not target_cell.value or target_cell.value.strip() == "":
                # Move member
                member_cell.value = ""
                target_cell.value = discord_id
                sheet.update_cells([member_cell, target_cell])
                
                # Force recalc
                batch_data = [
                    {'range': member_cell.address, 'values': [[""]]},
                    {'range': target_cell.address, 'values': [[discord_id]]}
                ]
                sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
                
                # Get callsign from column D
                callsign = sheet.cell(target_cell.row, 4).value or "N/A"
                
                role_names = {
                    'coordonator_sas': 'Coordonator SAS',
                    'coordonator_teste': 'Coordonator SAS - TESTE',
                    'agent_special': 'AGENT SPECIAL'
                }
                role_name = role_names.get(target_range_name, target_range_name)
                
                return True, f"Membru mutat la {role_name}: {callsign}"
        
        # No empty slots
        range_labels = {
            'coordonator_sas': 'Coordonator SAS (B11:B12)',
            'coordonator_teste': 'Coordonator SAS - TESTE (B14:B16)',
            'agent_special': 'AGENT SPECIAL (B18:B21)'
        }
        return False, f"Nu există poziții libere în {range_labels.get(target_range_name, target_range_name)}"
        
    except Exception as e:
        logging.error(f"Error moving member to specific range: {e}")
        return False, f"Eroare: {str(e)}"

class SASMemberSelectView(discord.ui.View):
    def __init__(self, requester_id: int, action: str):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.action = action
        self.add_item(SASMemberUserSelect(self, action))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True
# --------------- SAS Action Log (NEW) ---------------
CHECK_EMOJI = "✅"

class SASActionModal(discord.ui.Modal, title="Tip acțiune SAS"):
    def __init__(self, creator: discord.Member):
        super().__init__(timeout=300)
        self.creator = creator
        self.tip = discord.ui.TextInput(
            label="Tip acțiune",
            placeholder="Ex: razie cayo",
            max_length=100,
            required=True
        )
        self.add_item(self.tip)

    async def on_submit(self, interaction: discord.Interaction):
        # Post public action log & start 5-min reaction collection
        tip_txt = self.tip.value.strip()
        now = local_now()
        date_str = now.strftime("%d/%m/%Y")
        time_str = now.strftime("%H:%M")
        header = (
            f"📋 **Evidență Acțiune**\n"
            f"**Tip activitate:** {tip_txt}\n"
            f"**Data / Ora:** {date_str} - {time_str}\n"
            f"**Membrii care au participat:** - (0)"
        )
        await interaction.response.send_message(
            embed=make_embed(
                "Acțiune creată",
                f"Mesaj trimis în <#{SAS_ACTIUNI_CHANNEL_ID}>. Reacțiile (✅) timp de 5 minute.",
                discord.Color.green(),
                interaction.user
            ),
            ephemeral=True
        )
        # Post in actiuni channel (target)
        target_channel = bot.get_channel(SAS_ACTIUNI_CHANNEL_ID)
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.followup.send(
                embed=make_embed("Eroare", "Canalul de acțiuni nu a fost găsit.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        msg = await target_channel.send(header)
        try:
            await msg.add_reaction(CHECK_EMOJI)
        except Exception:
            pass
        # Store session
        bot.active_action_logs[msg.id] = {
            "type": tip_txt,
            "channel_id": msg.channel.id,
            "message_id": msg.id,
            "created_at": now,
            "members": set(),        # user ids
            "lock": asyncio.Lock()
        }
        # Schedule finalizer
        bot.loop.create_task(finalize_action_log_after(msg.id, 300))
        try:
            await log_command(interaction, "sas-action-create", changed=True, extra=f"type={tip_txt}")
        except Exception:
            pass

async def finalize_action_log_after(message_id: int, delay: int):
    await asyncio.sleep(delay)
    sess = bot.active_action_logs.get(message_id)
    if not sess:
        return
    channel = bot.get_channel(sess["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        bot.active_action_logs.pop(message_id, None)
        return
    guild = channel.guild

    # Collect participant callsigns
    member_ids = list(sess.get("members", []))
    callsigns: set[str] = set()
    for uid in member_ids:
        member = guild.get_member(uid)
        cs = _extract_callsign(member)
        if cs:
            callsigns.add(cs)

    # Send to web app
    api_ok = await _send_callsigns_activity_api(callsigns)

    # Fetch original message & clear reactions
    try:
        msg = await channel.fetch_message(message_id)
    except Exception:
        bot.active_action_logs.pop(message_id, None)
        return
    try:
        await msg.clear_reactions()
    except Exception:
        pass

    if callsigns:
        summary = f"+1 punct pentru {len(callsigns)} callsign-uri: {', '.join(sorted(callsigns))}."
        logging.info("Finalize action %s participants=%s", message_id, sorted(callsigns))
        color = discord.Color.green() if api_ok else discord.Color.orange()
    else:
        summary = "Niciun callsign valid (format S-##) găsit."
        color = discord.Color.orange()

    try:
        await channel.send(embed=make_embed("Evidență Acțiune - Puncte", summary, color))
    except Exception:
        pass

    bot.active_action_logs.pop(message_id, None)

async def _update_action_message(sess: dict):
    channel = bot.get_channel(sess["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        msg = await channel.fetch_message(sess["message_id"])
    except Exception:
        return
    # Build participants line
    guild = channel.guild
    member_ids = list(sess["members"])
    formatted = []
    for uid in member_ids:
        m = guild.get_member(uid)
        if m:
            # Custom formatting attempts similar to example:
            # @[partialID] display_name
            formatted.append(f"<@{uid}>")
    if not formatted:
        participants_line = "- (0)"
    else:
        participants_line = ", ".join(formatted) + f" ({len(formatted)})"

    # Reconstruct message text using stored type & original timestamp
    created_at = sess["created_at"]
    date_str = created_at.strftime("%d/%m/%Y")
    time_str = created_at.strftime("%H:%M")
    new_content = (
        f"📋 **Evidență Acțiune**\n"
        f"**Tip activitate:** {sess['type']}\n"
        f"**Data / Ora:** {date_str} - {time_str}\n"
        f"**Membrii care au participat:** {participants_line}"
    )
    try:
        await msg.edit(content=new_content)
    except Exception:
        pass

# --------------- SAS Action Log (Write to excel) ---------------

def _extract_callsign(member: discord.Member | None) -> str | None:
    if not member:
        return None
    name = (member.display_name or member.name)
    m = CALLSIGN_RE.search(name)
    if not m:
        return None
    digits = m.group(1)
    try:
        num = int(digits)
    except ValueError:
        logging.warning("Invalid callsign digits '%s' in name '%s'", digits, name)
        return None
    if num < 1 or num > 99:
        return None
    return f"S-{num:02d}"

async def _send_callsigns_activity_api(callsigns: set[str]) -> bool:  # NEW
    if not (ACTIVITY_API_URL and ACTIVITY_API_TOKEN and callsigns):
        return False
    payload = {
        "token": ACTIVITY_API_TOKEN,
        "sheet": ACTIVITY_API_SHEET,
        "callsigns": sorted(callsigns)
    }
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(ACTIVITY_API_URL, json=payload) as resp:
                text = await resp.text()
                if resp.status == 200 and text.startswith("OK"):
                    return True
                logging.warning("Activity API bad response %s: %s", resp.status, text[:200])
    except Exception as e:
        logging.warning("Activity API error: %s", e)
    return False

#--------------- Helper Classes -------------

class StopSessionButton(discord.ui.Button):
    def __init__(self, parent_view: "OngoingStopView", idx: int, user_id: int, start_time: str, display_name: str, row: int):
        self.parent_view = parent_view  # assign first
        self.target_uid = user_id
        self.start_time = start_time
        self.idx = idx
        custom_id = f"stop_{'sas' if parent_view.is_sas else 'pd'}_{user_id}_{start_time}_{idx}"
        label = f"{display_name} {start_time}"
        super().__init__(label=label, style=discord.ButtonStyle.danger, custom_id=custom_id, row=row)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id or not is_mgmt(interaction.user):
            await interaction.response.send_message(
                embed=make_embed("Permisiune", "Nu poți folosi acest panou.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return

        # ACK fast to avoid "Unknown interaction"
        if not interaction.response.is_done():
            await interaction.response.defer()  # not ephemeral; we'll edit the panel message

        try:
            if self.parent_view.is_sas:
                remove_session_sas(self.target_uid, self.parent_view.day, self.start_time)
            else:
                remove_session(self.target_uid, self.parent_view.day, self.start_time)
            member = interaction.guild.get_member(self.target_uid)
            if member:
                try:
                    await member.send(f"Sesiunea ta ({self.start_time} - {self.parent_view.day}) a fost oprită de {interaction.user.display_name}.")
                except Exception:
                    pass
            # Update sessions
            self.parent_view.sessions = [
                s for s in self.parent_view.sessions
                if not (s[0] == self.target_uid and s[2] == self.start_time)
            ]
            # Adjust page if needed
            total_pages = self.parent_view.total_pages()
            if self.parent_view.page_index >= total_pages:
                self.parent_view.page_index = max(0, total_pages - 1)
            self.parent_view.rebuild()

            content_embed = make_embed(
                "Opreste Pontaje",
                f"Sesiune oprită: {member.mention if member else self.target_uid} ({self.start_time}). "
                f"Rămase: {len(self.parent_view.sessions)}",
                discord.Color.green(),
                interaction.user
            )
            # After defer(), always edit_original_response
            await interaction.edit_original_response(embed=content_embed, view=self.parent_view)

            try:
                await log_command(
                    interaction,
                    "ongoing-stop-button",
                    target=member,
                    changed=True,
                    extra=f"start={self.start_time}"
                )
            except Exception:
                pass

        except Exception:
            # If editing failed, try to at least tell the user
            err_embed = make_embed("Eroare", "Eroare la oprire.", discord.Color.red(), interaction.user)
            try:
                await interaction.followup.send(embed=err_embed, ephemeral=True)
            except Exception:
                pass

class NavButton(discord.ui.Button):
    def __init__(self, parent_view: "OngoingStopView", forward: bool):
        label = "▶" if forward else "◀"
        custom_id = f"ongoing_nav_{'next' if forward else 'prev'}"
        super().__init__(style=discord.ButtonStyle.secondary, label=label, custom_id=custom_id, row=4)
        self.parent_view = parent_view
        self.forward = forward

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return

        # ACK fast
        if not interaction.response.is_done():
            await interaction.response.defer()

        total = self.parent_view.total_pages()
        if self.forward:
            self.parent_view.page_index = (self.parent_view.page_index + 1) % total
        else:
            self.parent_view.page_index = (self.parent_view.page_index - 1) % total
        self.parent_view.rebuild()

        # After defer(), edit the original message
        try:
            await interaction.edit_original_response(view=self.parent_view)
        except Exception:
            pass

class OngoingStopView(discord.ui.View):
    """
    Paginated vertical list of open sessions (today) with a stop button each.
    4 sessions per page (rows 0-3), row 4 reserved for navigation.
    """
    PAGE_SIZE = 4

    def __init__(self, sessions: list[tuple[int, str, str]], requester_id: int, day: str, *, is_sas: bool = False):
        super().__init__(timeout=180)
        self.sessions = sessions          # (uid, date, ci)
        self.requester_id = requester_id
        self.day = day
        self.page_index = 0
        self.is_sas = is_sas
        self.rebuild()

    def total_pages(self) -> int:
        if not self.sessions:
            return 1
        return (len(self.sessions) + self.PAGE_SIZE - 1) // self.PAGE_SIZE

    def rebuild(self):
        self.clear_items()
        start = self.page_index * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        slice_sessions = self.sessions[start:end]

        # Add stop buttons (vertical stack)
        for row, (uid, date_val, ci) in enumerate(slice_sessions):
            member = None
            try:
                # first guild assumption (adjust if multi-guild)
                if bot.guilds:
                    member = bot.guilds[0].get_member(uid)
            except Exception:
                pass
            name = member.display_name if member else str(uid)
            self.add_item(StopSessionButton(self, row=row, idx=start + row + 1, user_id=uid, start_time=ci, display_name=name))

        # Navigation if multiple pages
        if self.total_pages() > 1:
            self.add_item(NavButton(self, forward=False))
            self.add_item(NavButton(self, forward=True))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

class WarnReasonModal(discord.ui.Modal, title="Motiv Warn"):
    def __init__(self, actor: discord.Member, target: discord.Member):
        super().__init__(timeout=300)
        self.actor = actor
        self.target = target
        self.reason = discord.ui.TextInput(
            label="Motiv",
            style=discord.TextStyle.paragraph,
            max_length=400,
            required=True
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_mgmt(interaction.user):
            await interaction.response.send_message("Permisiune refuzată.", ephemeral=True)
            return
        current = get_punish_count(self.target.id)
        if current >= 3:
            await interaction.response.send_message(
                embed=make_embed("Limită", f"{self.target.mention} are deja 3/3. Reset necesar.", discord.Color.orange(), interaction.user),
                ephemeral=True
            )
            return
        new_count = increment_punish_count(self.target.id)
        emb = _warn_embed(self.actor, self.target, "Avertisment",
                          f"Avertizat de {self.actor.mention}\nWarn {new_count}/3\n\n{self.reason.value}",
                          discord.Color.red() if new_count == 3 else discord.Color.orange())
        _send_warn_to_channel(interaction.guild, emb, mention_when_3=(new_count == 3))
        await _post_warn(
            interaction.guild,
            actor=self.actor,
            target=self.target,
            kind="add",
            count=new_count,
            reason=self.reason.value
        ) 
        await interaction.response.send_message(
            embed=make_embed("Warn trimis", f"{self.target.mention} -> {new_count}/3", discord.Color.green(), interaction.user),
            ephemeral=True
        )
        try:
            await self.target.send(f"Ai primit un warn ({new_count}/3) de la {self.actor.display_name}: {self.reason.value}")
        except Exception:
            pass
        try:
            await log_command(interaction, "warn-add-ui", target=self.target, changed=True, extra=f"new_count={new_count}")
        except Exception:
            pass

class WarnResetModal(discord.ui.Modal, title="Reset Warn-uri"):
    def __init__(self, actor: discord.Member, target: discord.Member):
        super().__init__(timeout=300)
        self.actor = actor
        self.target = target
        self.note = discord.ui.TextInput(
            label="Notă (opțional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_role(interaction.user, CONDUCERE_ROLE_ID):
            await interaction.response.send_message("Doar Conducere poate reseta.", ephemeral=True)
            return
        reset_punish_count(self.target.id)
        emb = _warn_embed(self.actor, self.target, "Reset Warn-uri",
                          f"Resetat de {self.actor.mention}.\n{self.note.value.strip()}", discord.Color.green())
        _send_warn_to_channel(interaction.guild, emb)
        await _post_warn(
            interaction.guild,
            actor=self.actor,
            target=self.target,
            kind="reset",
            note=self.note.value
        )
        await interaction.response.send_message(
            embed=make_embed("Reset", f"{self.target.mention} resetat la 0/3.", discord.Color.green(), interaction.user),
            ephemeral=True
        )
        try:
            await self.target.send(f"Warn-urile tale au fost resetate de {self.actor.display_name}.")
        except Exception:
            pass
        try:
            await log_command(interaction, "warn-reset-ui", target=self.target, changed=True)
        except Exception:
            pass

class WarnActionView(discord.ui.View):
    def __init__(self, requester_id: int, target: discord.Member):
        super().__init__(timeout=200)
        self.requester_id = requester_id
        self.target = target

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Adaugă Warn", style=discord.ButtonStyle.danger, custom_id="warn_add_btn")
    async def add_warn_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_mgmt(interaction.user):
            await interaction.response.send_message("Permisiune refuzată.", ephemeral=True)
            return
        await interaction.response.send_modal(WarnReasonModal(interaction.user, self.target))

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, custom_id="warn_status_btn")
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_mgmt(interaction.user):
            await interaction.response.send_message("Permisiune refuzată.", ephemeral=True)
            return
        count = get_punish_count(self.target.id)
        emb = _warn_embed(interaction.user, self.target, "Status Warn", f"{self.target.mention} are {count}/3.", discord.Color.blurple())
        _send_warn_to_channel(interaction.guild, emb)
        await interaction.response.send_message(
            embed=make_embed("Status trimis", f"{self.target.mention} are {count}/3.", discord.Color.blurple(), interaction.user),
            ephemeral=True
        )
        try:
            await log_command(interaction, "warn-status-ui", target=self.target, changed=False, extra=f"count={count}")
        except Exception:
            pass

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.success, custom_id="warn_reset_btn")
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_role(interaction.user, CONDUCERE_ROLE_ID):
            await interaction.response.send_message("Doar Conducere poate reseta.", ephemeral=True)
            return
        await interaction.response.send_modal(WarnResetModal(interaction.user, self.target))

class WarnUserSelect(discord.ui.UserSelect):
    def __init__(self, parent: "WarnUserSelectView"):
        super().__init__(placeholder="Selectează user", min_values=1, max_values=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        member = self.values[0]
        view = WarnActionView(self.parent_view.requester_id, member)
        await interaction.response.edit_message(
            embed=make_embed("Warn Panel",
                             f"Țintă: {member.mention}\nAlege acțiunea.",
                             discord.Color.orange(),
                             interaction.user),
            view=view
        )
        try:
            await log_command(interaction, "warn-target-select", target=member, changed=False)
        except Exception:
            pass

class WarnUserSelectView(discord.ui.View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.add_item(WarnUserSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

class AddMinutesModal(discord.ui.Modal, title="Adaugă Minute"):
    def __init__(self, actor: discord.Member, target: discord.Member, is_sas: bool):
        super().__init__(timeout=300)
        self.actor = actor
        self.target = target
        self.is_sas = is_sas
        today = local_now().strftime("%Y-%m-%d")
        self.date_input = discord.ui.TextInput(
            label="Data (YYYY-MM-DD)",
            placeholder=today,
            default=today,
            max_length=10,
            required=True
        )
        self.minutes_input = discord.ui.TextInput(
            label="Minute de adăugat",
            placeholder="Ex: 45",
            max_length=6,
            required=True
        )
        self.add_item(self.date_input)
        self.add_item(self.minutes_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Permission check
        if self.is_sas:
            if not is_csas(interaction.user):
                await interaction.response.send_message("Permisiune refuzată.", ephemeral=True)
                return
        else:
            if not is_mgmt(interaction.user):
                await interaction.response.send_message("Permisiune refuzată.", ephemeral=True)
                return
            
        date_str = self.date_input.value.strip()
        minutes_raw = self.minutes_input.value.strip().replace(",", ".")
        # Validate date
        try:
            datetime.datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                embed=make_embed("Dată invalidă", "Format corect: YYYY-MM-DD", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        # Validate minutes
        try:
            minutes_val = float(minutes_raw)
        except ValueError:
            await interaction.response.send_message(
                embed=make_embed("Minute invalide", "Introdu un număr.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        if minutes_val <= 0:
            await interaction.response.send_message(
                embed=make_embed("Minute invalide", "Trebuie să fie > 0.", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return

        # Replicate /adaugaminute logic
        if self.is_sas:
            sessions = get_clock_times_sas(self.target.id, date_str)
        else:
            sessions = get_clock_times(self.target.id, date_str)

        # Pick a unique start time beginning at 00:00:00 to avoid duplicates.
        occupied = {s[0] for s in sessions if s and s[0]}
        base_dt = parse_local(date_str, "00:00:00")
        while base_dt.strftime("%H:%M:%S") in occupied:
            base_dt += datetime.timedelta(seconds=1)

        # Compute end; clamp within the same day
        new_co = base_dt + datetime.timedelta(minutes=minutes_val)
        if new_co.date() != base_dt.date():
            new_co = base_dt.replace(hour=23, minute=59, second=59)

        # Insert new session (finished)
        if self.is_sas:
            add_clock_in_sas(self.target.id, date_str, base_dt.strftime("%H:%M:%S"))
            update_clock_out_sas(self.target.id, date_str, new_co.strftime("%H:%M:%S"), base_dt.strftime("%H:%M:%S"))
            msg = f"Sesiune nouă SAS {base_dt.strftime('%H:%M:%S')} -> {new_co.strftime('%H:%M:%S')} ({minutes_val:.0f}m)"
        else:
            add_clock_in(self.target.id, date_str, base_dt.strftime("%H:%M:%S"))
            update_clock_out(self.target.id, date_str, new_co.strftime("%H:%M:%S"), base_dt.strftime("%H:%M:%S"))
            msg = f"Sesiune nouă {base_dt.strftime('%H:%M:%S')} -> {new_co.strftime('%H:%M:%S')} ({minutes_val:.0f}m)"
        
        await interaction.response.send_message(
            embed=make_embed("Minute adăugate", f"{self.target.mention} | {msg}", discord.Color.green(), interaction.user),
            ephemeral=True
        )
        try:
            if self.is_sas:
                await log_command(
                    interaction,
                    "adaugaminute-sas-button",
                    target=self.target,
                    changed=True,
                    extra=f"date={date_str} minutes={minutes_val}"
                )
            else:
                await log_command(
                    interaction,
                    "adaugaminute-button",
                    target=self.target,
                    changed=True,
                    extra=f"date={date_str} minutes={minutes_val}"
                )
        except Exception:
            pass
        try:
            if self.is_sas:
                await self.target.send(f"{minutes_val:.0f} minute adaugate prin sesiunea SAS {base_dt.strftime('%H:%M:%S')} -> {new_co.strftime('%H:%M:%S')}  de {interaction.user.display_name}.")
            else:
                await self.target.send(f"{minutes_val:.0f} minute adaugate prin sesiunea {base_dt.strftime('%H:%M:%S')} -> {new_co.strftime('%H:%M:%S')}  de {interaction.user.display_name}.")
        except Exception:
            pass

class AddMinutesUserSelect(discord.ui.UserSelect):
    def __init__(self, parent: "AddMinutesUserSelectView", is_sas: bool):
        super().__init__(placeholder="Selectează user", min_values=1, max_values=1)
        self.parent_view = parent
        self.is_sas = is_sas

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        member = self.values[0]
        # Open modal
        await interaction.response.send_modal(AddMinutesModal(interaction.user, member, is_sas=self.is_sas))
        try:
            await log_command(interaction, "addminutes-target-select", target=member, changed=False)
        except Exception:
            pass

class AddMinutesUserSelectView(discord.ui.View):
    def __init__(self, requester_id: int, is_sas: bool):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.is_sas = is_sas
        self.add_item(AddMinutesUserSelect(self, is_sas=self.is_sas))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

class RemovePontajButton(discord.ui.Button):
    def __init__(self, parent: "RemovePontajSessionsView", idx: int, start_time: str):
        # Grid layout: 5 columns per row
        row = (idx - 1) // 5  # 0..4
        super().__init__(
            label=f"{idx}",
            style=discord.ButtonStyle.danger,
            custom_id=f"rmv_{idx}",
            row=row
        )
        self.parent_view = parent
        self.idx = idx
        self.start_time = start_time

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        if not is_mgmt(interaction.user):
            await interaction.response.send_message("Permisiune refuzată.", ephemeral=True)
            return
        sessions = get_clock_times(self.parent_view.target.id, self.parent_view.date_str)
        if self.idx < 1 or self.idx > len(sessions):
            await interaction.response.send_message("Index invalid (a fost modificat între timp).", ephemeral=True)
            return
        session = sessions[self.idx - 1]
        # Remove by start time
        try:
            remove_session(self.parent_view.target.id, self.parent_view.date_str, session[0])
        except Exception:
            await interaction.response.send_message("Eroare la ștergere.", ephemeral=True)
            return

        # Log + notify
        try:
            await log_command(
                interaction,
                "stergepontaj",
                target=self.parent_view.target,
                changed=True,
                extra=f"date={self.parent_view.date_str} start={session[0]} end={session[1]}"
            )
        except Exception:
            pass
        try:
            await self.parent_view.target.send(
                f"Sesiunea ({session[0]} - {session[1] or '...'} / {self.parent_view.date_str}) a fost ștearsă de {interaction.user.display_name}."
            )
        except Exception:
            pass

        # Disable all buttons after removal
        for child in self.parent_view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(
            embed=make_embed(
                "Pontaj șters",
                f"{self.parent_view.target.mention} | {self.parent_view.date_str}\nȘters index #{self.idx}: {session}",
                discord.Color.green(),
                interaction.user
            ),
            view=self.parent_view
        )

class RemovePontajSessionsView(discord.ui.View):
    def __init__(self, requester_id: int, target: discord.Member, date_str: str, sessions: list[tuple]):
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.target = target
        self.date_str = date_str
        # Add buttons (cap at 20 for safety)
        for idx, s in enumerate(sessions[:20], start=1):
            self.add_item(RemovePontajButton(self, idx, s[0]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

class RemovePontajDateModal(discord.ui.Modal, title="Dată Pontaj"):
    def __init__(self, requester: discord.Member, target: discord.Member):
        super().__init__(timeout=180)
        self.requester = requester
        self.target = target
        today=local_now().strftime("%Y-%m-%d")
        self.date_input = discord.ui.TextInput(
            label="Data (YYYY-MM-DD)",
            placeholder=today,
            default=today,
            max_length=10
        )
        self.add_item(self.date_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        date_str = self.date_input.value.strip()
        try:
            datetime.datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                embed=make_embed("Dată invalidă", "Format corect: YYYY-MM-DD", discord.Color.red(), interaction.user),
                ephemeral=True
            )
            return
        sessions = get_clock_times(self.target.id, date_str)
        if not sessions:
            await interaction.response.send_message(
                embed=make_embed("Fără sesiuni", f"Nimic pentru {self.target.mention} la {date_str}.", discord.Color.orange(), interaction.user),
                ephemeral=True
            )
            return
        # Build list
        lines = []
        for i, s in enumerate(sessions, start=1):
            lines.append(f"{i}. {s[0]} - {s[1] or '...'}")
        view = RemovePontajSessionsView(self.requester.id, self.target, date_str, sessions)
        await interaction.response.send_message(
            embed=make_embed(
                "Alege sesiunea",
                f"{self.target.mention} | {date_str}\nSelectează index pentru ștergere:\n" + "\n".join(lines)[:3900],
                discord.Color.orange(),
                interaction.user
            ),
            view=view,
            ephemeral=True
        )

class RemovePontajUserSelect(discord.ui.UserSelect):
    def __init__(self, parent: "RemovePontajUserSelectView"):
        super().__init__(placeholder="Selectează user", min_values=1, max_values=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        member = self.values[0]
        # Open date modal
        await interaction.response.send_modal(RemovePontajDateModal(interaction.user, member))
        try:
            await log_command(interaction, "stergepontaj-select-user", target=member, changed=False)
        except Exception:
            pass

class RemovePontajUserSelectView(discord.ui.View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.add_item(RemovePontajUserSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

# --------------- EOD HELPERS ---------------

async def _send_eod_confirm_request(uid: int, *, is_sas: bool, date: str, start_time: str, end_time: str = "23:59:59"):
    """
    Ask the user to confirm saving the open session by reacting ✅ within the window.
    DM first; if DM blocked, post in the appropriate guild channel.
    """
    start_dt = parse_local(date, start_time)
    end_dt = parse_local(date, end_time)
    
    # Calculate minutes
    mins = (end_dt - start_dt).total_seconds() / 60.0
    rounded = round_minutes(mins)
    
    # Determine reminder text based on end_time
    reminder = "**NU UITA SA PORNESTI PONTAJUL DUPA ORA 00:00**" if end_time == "23:59:59" else "**POTI PORNI PONTAJUL DUPA ORA 05:30**"
    
    text = (
        f"Confirmare pontaj {'SAS' if is_sas else 'PD'} pentru {date}\n"
        f"Start: {start_time} -> End propus: {end_time}. Total: {rounded} minute\n"
        f"Reacționează cu {EOD_CONFIRM_EMOJI} în {EOD_CONFIRM_WINDOW_SECS // 60} minute pentru a salva.\n"
        f"Dacă nu reacționezi, sesiunea NU va fi salvată.\n"
        f"{reminder}"
    )
    # Try DM
    msg = None
    user = bot.get_user(uid) or await bot.fetch_user(uid)
    if user:
        try:
            msg = await user.send(text)
        except Exception:
            msg = None
    # Fallback to channels
    if msg is None:
        ch_id = 1410377156156198963
        ch = bot.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            try:
                msg = await ch.send(f"<@{uid}>\n{text}")
            except Exception:
                msg = None
    if msg is None:
        return False
    try:
        await msg.add_reaction(EOD_CONFIRM_EMOJI)
    except Exception:
        pass
    bot.pending_eod_confirms[msg.id] = {
        "uid": uid,
        "is_sas": is_sas,
        "date": date,
        "ci": start_time,
        "end_time": end_time,  # Store the end_time
        "channel_id": getattr(msg.channel, "id", None)
    }
    bot.loop.create_task(_finalize_eod_confirm_after(msg.id, EOD_CONFIRM_WINDOW_SECS))
    return True

async def _finalize_eod_confirm_after(message_id: int, delay: int):
    await asyncio.sleep(delay)
    data = bot.pending_eod_confirms.pop(message_id, None)
    if not data:
        return
    # Not confirmed -> delete the open session (do not save)
    try:
        if data["is_sas"]:
            remove_session_sas(data["uid"], data["date"], data["ci"])
        else:
            remove_session(data["uid"], data["date"], data["ci"])
    except Exception:
        pass
    # Update the confirmation message (expired)
    try:
        ch = bot.get_channel(data["channel_id"]) or await bot.fetch_channel(data["channel_id"])
        msg = await ch.fetch_message(message_id)
        try:
            await msg.clear_reactions()
        except Exception:
            pass
        await msg.edit(content=msg.content + "\n\nNeconfirmat în timp – pontaj Nesalvat.")
    except Exception:
        pass

    try:
        log_ch = bot.get_channel(1410237389321928745)
        who = f"<@{data['uid']}> (`{data['uid']}`)"
        kind = "SAS" if data["is_sas"] else "PD"
        text = (
            f"{who}\n"
            f"Tip: {kind}\n"
            f"Dată: {data['date']}\n"
            f"Start: {data['ci']}\n"
            f"Status: NU a confirmat în timp – sesiunea a fost ștearsă."
        )
        emb = make_embed("EOD neconfirmat", text, discord.Color.orange())
        if log_ch:
            await log_ch.send(embed=emb)
        _append_log_line(
            f"[{datetime.datetime.utcnow().isoformat()}Z] [EOD] NOT_CONFIRMED uid={data['uid']} "
            f"type={kind} date={data['date']} start={data['ci']}"
        )
    except Exception:
        pass

# --------------- Bot ---------------
class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True  # add this to remove warning (enable in Dev Portal)
        intents.reactions = True
        super().__init__(command_prefix="!", intents=intents)
        self.start_time = datetime.datetime.utcnow()
        self.relay_sessions: Dict[int, Dict[str, Any]] = {}
        self.last_auto_close_day: str | None = None
        self.last_auto_close_night: str | None = None  # Add this line  
        self.active_action_logs: Dict[int, Dict[str, Any]] = {}
        self.console_relay_enabled: bool = False
        self.console_relay_channel_id: int | None = CONSOLE_RELAY_DEFAULT_CHANNEL_ID or None
        self._console_task: asyncio.Task | None = None
        self._console_webhooks: Dict[int, discord.Webhook] = {}
        self.pending_eod_confirms: Dict[int, Dict[str, Any]] = {}

    async def setup_hook(self):
        try:
            # Register persistent button view
            self.add_view(ClockButtons())
            self.add_view(SASClockButtons())
            self.add_view(HrButtons())
            self.add_view(RelayButtons())
            self.add_view(SASCoordonatorButtons())
            self.add_view(SASMemberManagementButtons())  # Add this line
        except NameError:
            # View defined later; silent if ordering changes
            pass
        try:
            if DEV_GUILD_ID:
                guild = discord.Object(id=DEV_GUILD_ID)
                synced = await self.tree.sync(guild=guild)
                logging.info(f"Synced {len(synced)} guild slash commands to {DEV_GUILD_ID}")
            else:
                synced = await self.tree.sync()
                logging.info(f"Synced {len(synced)} global slash commands")
        except Exception:
            logging.exception("Slash command sync failed")

                # Start 23:59 auto-closer
        try:
            self.auto_close_today_sessions.start()
        except RuntimeError:
            pass
        if self._console_task is None:
            self._console_task = self.loop.create_task(self._console_relay())
    
    async def _get_console_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        """Get or create the webhook used for console relay in this channel."""
        cached = self._console_webhooks.get(channel.id)
        if cached:
            return cached
        try:
            hooks = await channel.webhooks()
            hook = next((h for h in hooks if h.name == CONSOLE_WEBHOOK_NAME), None)
            if hook is None:
                hook = await channel.create_webhook(name=CONSOLE_WEBHOOK_NAME, reason="Console relay")
            self._console_webhooks[channel.id] = hook
            return hook
        except Exception as e:
            logging.warning("Console relay: cannot get/create webhook in #%s: %s", channel.id, e)
            return None

    async def _console_relay(self):
        await self.wait_until_ready()
        loop = asyncio.get_running_loop()
        while not self.is_closed():
            try:
                if not self.console_relay_enabled:
                    await asyncio.sleep(1.0)
                    continue
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    await asyncio.sleep(1.0)
                    continue
                line = line.strip()
                if not line:
                    continue

                ch = None
                if self.console_relay_channel_id:
                    ch = self.get_channel(self.console_relay_channel_id) or await self.fetch_channel(self.console_relay_channel_id)
                if isinstance(ch, discord.TextChannel):
                    hook = await self._get_console_webhook(ch)
                    for chunk_start in range(0, len(line), 1900):
                        chunk = line[chunk_start:chunk_start + 1900]
                        try:
                            if hook:
                                await hook.send(
                                    chunk,
                                    username=CONSOLE_WEBHOOK_NAME,
                                    avatar_url=(CONSOLE_WEBHOOK_AVATAR_URL or None),
                                    allowed_mentions=discord.AllowedMentions.none()
                                )
                            else:
                                await ch.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                        except Exception as e:
                            logging.warning("Console relay send failed: %s", e)
                            break
                    _append_log_line(f"[{datetime.datetime.utcnow().isoformat()}Z] [CONSOLE] {line}")
                else:
                    logging.warning("Console relay: target channel not set or invalid.")
                    await asyncio.sleep(1.0)
            except Exception as e:
                logging.exception("Console relay error: %s", e)
                await asyncio.sleep(2.0)

    @tasks.loop(minutes=1)
    async def auto_close_today_sessions(self):
        now = local_now()
        day = now.strftime("%Y-%m-%d")  # Always use current day
        
        # Case 1: End of night shift (05:30) - close sessions started TODAY between 00:00-05:30
        if now.hour == 5 and now.minute == 25:
            # Use separate tracking variable to allow both to run same day
            if hasattr(self, 'last_auto_close_night') and self.last_auto_close_night == day:
                return
            
            # Filter sessions: only those starting between 00:00 and 05:30 TODAY
            pd_night = []
            sas_night = []
            
            for uid, date, ci in get_ongoing_sessions():
                if date == day:  # Current day
                    # Parse start time to check if it's between 00:00 and 05:25
                    try:
                        hour, minute = map(int, ci.split(":")[:2])
                        if hour < 5 or (hour == 5 and minute <= 25):  # 00:00 to 05:25
                            pd_night.append((uid, date, ci))
                    except Exception:
                        pass
            
            for uid, date, ci in get_ongoing_sessions_sas():
                if date == day:  # Current day
                    try:
                        hour, minute = map(int, ci.split(":")[:2])
                        if hour < 5 or (hour == 5 and minute <= 25):  # 00:00 to 05:25
                            sas_night.append((uid, date, ci))
                    except Exception:
                        pass
            
            sent_pd = 0
            sent_sas = 0
            
            for uid, date, ci in pd_night:
                ok = await _send_eod_confirm_request(uid, is_sas=False, date=date, start_time=ci, end_time="05:30:00")
                if ok:
                    sent_pd += 1
            
            for uid, date, ci in sas_night:
                ok = await _send_eod_confirm_request(uid, is_sas=True, date=date, start_time=ci, end_time="05:30:00")
                if ok:
                    sent_sas += 1
            
            self.last_auto_close_night = day
            summary = f"Night shift confirm (05:25) trimis pentru {day}: PD={sent_pd} SAS={sent_sas} (fereastră {EOD_CONFIRM_WINDOW_SECS//60}m)"
            _append_log_line(f"[{datetime.datetime.utcnow().isoformat()}Z] [EOD_NIGHT] {summary}")
            ch = self.get_channel(LOGS_CHANNEL_ID)
            if ch:
                try:
                    await ch.send(embed=make_embed("Night Shift Confirm", summary, discord.Color.teal()))
                except Exception:
                    pass
        
        # Case 2: End of day (23:55) - close ALL sessions from current day
        elif now.hour == 23 and now.minute == 55:
            if self.last_auto_close_day == day:
                return  # already processed
            
            pd_open = [(uid, date, ci) for uid, date, ci in get_ongoing_sessions() if date == day]
            sas_open = [(uid, date, ci) for uid, date, ci in get_ongoing_sessions_sas() if date == day]

            sent_pd = 0
            sent_sas = 0
            for uid, date, ci in pd_open:
                ok = await _send_eod_confirm_request(uid, is_sas=False, date=date, start_time=ci, end_time="23:59:59")
                if ok:
                    sent_pd += 1
            for uid, date, ci in sas_open:
                ok = await _send_eod_confirm_request(uid, is_sas=True, date=date, start_time=ci, end_time="23:59:59")
                if ok:
                    sent_sas += 1

            self.last_auto_close_day = day
            summary = f"EOD confirm (23:55) trimis pentru {day}: PD={sent_pd} SAS={sent_sas} (fereastră {EOD_CONFIRM_WINDOW_SECS//60}m)"
            _append_log_line(f"[{datetime.datetime.utcnow().isoformat()}Z] [EOD] {summary}")
            ch = self.get_channel(LOGS_CHANNEL_ID)
            if ch:
                try:
                    await ch.send(embed=make_embed("EOD Confirm", summary, discord.Color.teal()))
                except Exception:
                    pass

    @auto_close_today_sessions.before_loop
    async def before_auto_close_today_sessions(self):
        await self.wait_until_ready()

bot = Bot()
logger = logging.getLogger("discord_bot")
logger.addHandler(DiscordHandler(bot, LOGS_CHANNEL_ID))

# --------------- Helpers ---------------
def make_embed(
    title: str | None = None,
    desc: str | None = None,
    color: discord.Color = discord.Color.blurple(),
    user: discord.abc.User | None = None,
) -> discord.Embed:
    # Use localized time (Europe/Bucharest by default) instead of UTC
    now_local = local_now()
    e = discord.Embed(title=title, description=desc, color=color, timestamp=now_local)
    if user:
        try:
            e.set_footer(text=f"{user.display_name} • {now_local.strftime('%H:%M:%S')}",  
                         icon_url=user.display_avatar.url)
        except Exception:
            e.set_footer(text=f"{user.display_name} • {now_local.strftime('%H:%M:%S')}")
    return e

def has_role(member: discord.Member, role_id: int) -> bool:
    return any(r.id == role_id for r in member.roles)

def has_any(member: discord.Member, ids: list[int]) -> bool:
    return any(r.id in ids for r in member.roles)

def is_mgmt(member: discord.Member) -> bool:
    return has_role(member, REQUIRED_HR_ROLE_ID) or has_role(member, CONDUCERE_ROLE_ID)

def is_csas(member: discord.Member) -> bool:
    return has_role(member, SAS_COORDONATOR_IDS)

def round_minutes(m: float) -> int:
    return round(m / 5) * 5

def minutes_diff(start: datetime.datetime, end: datetime.datetime) -> int:
    # Normalize both to aware datetimes in configured timezone
    tz = ZoneInfo(TIMEZONE)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    return int((end - start).total_seconds() // 60)

def _drain_stdin(timeout: float = 0.2) -> None:
    """
    Drain pending data from STDIN without blocking so only new lines
    (typed after starting) are forwarded.
    """
    try:
        fd = sys.stdin.fileno()
    except Exception:
        return
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if not r:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
            continue
        try:
            chunk = os.read(fd, 65536)  # discard
        except Exception:
            break
        if not chunk:
            break

# ---- Check factories (no wrapper altering signatures) ----
def in_channel_check(channel_id: int):
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.channel or interaction.channel.id != channel_id:
            raise app_commands.CheckFailure(f"WRONG_CHANNEL:{channel_id}")
        return True
    return app_commands.check(predicate)

def pd_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        # REQUIRED_PD_ROLE_ID is a list -> check if user has ANY of them
        if not has_any(interaction.user, REQUIRED_PD_ROLE_ID):
            raise app_commands.CheckFailure("NO_PD")
        return True
    return app_commands.check(predicate)

def hr_or_conducere_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not (has_role(interaction.user, REQUIRED_HR_ROLE_ID) or has_role(interaction.user, CONDUCERE_ROLE_ID)):
            raise app_commands.CheckFailure("NO_HR_OR_CONDUCERE")
        return True
    return app_commands.check(predicate)

def sas_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not has_role(interaction.user, SAS_ROLE_IDS):
            raise app_commands.CheckFailure("NO_SAS")
        return True
    return app_commands.check(predicate)

def sas_coordonator_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not has_role(interaction.user, SAS_COORDONATOR_IDS):
            raise app_commands.CheckFailure("NO_SAS_COORDONATOR")
        return True
    return app_commands.check(predicate)

def in_sas_channel_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.channel or interaction.channel.id != SAS_CHANNEL_ID:
            raise app_commands.CheckFailure(f"WRONG_CHANNEL:{SAS_CHANNEL_ID}")
        return True
    return app_commands.check(predicate)

def in_sas_actiuni_channel_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.channel or interaction.channel.id != SAS_ACTIUNI_CHANNEL_ID:
            raise app_commands.CheckFailure(f"WRONG_CHANNEL:{SAS_ACTIUNI_CHANNEL_ID}")
        return True
    return app_commands.check(predicate)




# --------------- Events ---------------
@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user} ({bot.user.id})")

@bot.event
async def on_member_remove(member: discord.Member):
    """
    Fires when a member leaves (voluntary leave, kick, or after ban).
    Sends: "<discordId> <username> has left the server" to LEAVE_CHANNEL_ID if set.
    """
    if LEAVE_CHANNEL_ID is None:
        return  # Not configured
    channel = member.guild.get_channel(LEAVE_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return
    msg = f"**<@{member.id}> has left the server**"
    try:
        await channel.send(msg)
    except Exception:
        logging.exception("Failed to send leave message")
    # Minimal file log
    _append_log_line(f"[{datetime.datetime.utcnow().isoformat()}Z] [LEAVE] {msg}")

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    emoji = str(payload.emoji)

    # 1) End-of-day confirmations
    if payload.message_id in bot.pending_eod_confirms:
        if emoji != EOD_CONFIRM_EMOJI:
            return
        data = bot.pending_eod_confirms.get(payload.message_id)
        if not data:
            return
        if payload.user_id != data["uid"]:
            return  # only the owner can confirm
        
        end_time = data.get("end_time", "23:59:59")  # Use stored end_time
        
        try:
            # Save session
            if data["is_sas"]:
                update_clock_out_sas(data["uid"], data["date"], end_time)
            else:
                update_clock_out(data["uid"], data["date"], end_time)

            # Compute minutes for the saved interval
            start_dt = parse_local(data["date"], data["ci"])
            end_dt = parse_local(data["date"], end_time)
            mins = (end_dt - start_dt).total_seconds() / 60.0
            rounded = round_minutes(mins)

            # Edit message -> confirmed + show minutes
            try:
                ch = bot.get_channel(payload.channel_id) or await bot.fetch_channel(payload.channel_id)
                msg = await ch.fetch_message(payload.message_id)
                try:
                    await msg.clear_reactions()
                except Exception:
                    pass
                await msg.edit(
                    content=msg.content
                    + f"\n\nConfirmat – pontaj salvat."
                    + f"\nInterval: {data['ci']} → {end_time} ({rounded} minute)"
                )
            except Exception:
                pass
            # NEW: log confirmation (channel + file)
            try:
                kind = "SAS" if data["is_sas"] else "PD"
                log_ch = bot.get_channel(1410382233855856680)
                desc = (
                    f"<@{data['uid']}> (`{data['uid']}`)\n"
                    f"Tip: {kind}\n"
                    f"Dată: {data['date']}\n"
                    f"Interval: {data['ci']} → {end_time} ({rounded} min)\n"
                    f"Status: Confirmat"
                )
                if log_ch:
                    await log_ch.send(embed=make_embed("EOD confirmat", desc, discord.Color.green()))
                _append_log_line(
                    f"[{datetime.datetime.utcnow().isoformat()}Z] [EOD] CONFIRMED uid={data['uid']} "
                    f"type={kind} date={data['date']} start={data['ci']} end={end_time} mins={rounded}"
                )
            except Exception:
                pass
        except Exception as e:
            logging.warning("EOD confirm save failed for uid=%s: %s", data["uid"], e)
        finally:
            bot.pending_eod_confirms.pop(payload.message_id, None)
        return

    # 2) SAS action log join list
    if payload.message_id not in bot.active_action_logs:
        return
    if emoji != CHECK_EMOJI:
        return
    sess = bot.active_action_logs.get(payload.message_id)
    if not sess:
        return
    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member:
        return
    if not has_role(member, SAS_ROLE_IDS):
        return
    async with sess["lock"]:
        if payload.user_id in sess["members"]:
            return
        sess["members"].add(payload.user_id)
    await _update_action_message(sess)

@bot.event  
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    """
    When someone removes their ✅ reaction during the 5‑minute window,
    remove them from the participant list and update the message.
    """
    if payload.message_id not in bot.active_action_logs:
        return
    if str(payload.emoji) != CHECK_EMOJI:
        return
    if payload.user_id == bot.user.id:
        return
    sess = bot.active_action_logs.get(payload.message_id)
    if not sess:
        return
    async with sess["lock"]:
        if payload.user_id in sess["members"]:
            sess["members"].remove(payload.user_id)
        else:
            return  
    await _update_action_message(sess)

@bot.event  
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    # Do not delete anything; just clear pending state if a confirm was removed manually
    bot.pending_eod_confirms.pop(payload.message_id, None)

@bot.event  
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent):
    for mid in payload.message_ids:
        bot.pending_eod_confirms.pop(mid, None)

# --------------- Error Handler ---------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        key = str(error)
        if key.startswith("WRONG_CHANNEL:"):
            cid = key.split(":", 1)[1]
            msg = f"Folosește comanda în <#{cid}>."
        elif key == "NO_PD":
            msg = "Ai nevoie de rolul PD."
        elif key == "NO_HR":
            msg = "Ai nevoie de rolul HR."
        elif key == "NO_HR_OR_CONDUCERE":
            msg = "Necesită HR sau Conducere."
        elif key == "NO_SAS":
            msg = "Necesită rolul SAS."
        else:
            msg = "Nu ai permisiune."
        if interaction.response.is_done():
            await interaction.followup.send(embed=make_embed("Permisiune", msg, discord.Color.red(), interaction.user), ephemeral=True)
        else:
            await interaction.response.send_message(embed=make_embed("Permisiune", msg, discord.Color.red(), interaction.user), ephemeral=True)

        # Log failed attempt
        try:
            await log_command(
                interaction,
                interaction.command.name if interaction.command else "unknown",
                extra=msg,
                success=False,
                changed=False
            )
        except Exception:
            pass
        return

    logging.exception("App command error: %s", error)
    if interaction.response.is_done():
        await interaction.followup.send(
            embed=make_embed("Eroare", "Eroare internă.", discord.Color.red(), interaction.user),
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            embed=make_embed("Eroare", "Eroare internă.", discord.Color.red(), interaction.user),
            ephemeral=True
        )
    # Log generic failure
    try:
        await log_command(
            interaction,
            interaction.command.name if interaction.command else "unknown",
            extra=repr(error),
            success=False,
            changed=False
        )
    except Exception:
        pass

# --------------- Slash Commands ---------------
@bot.tree.command(name="clockpanel", description="Postează panoul Clock IN/OUT (HR sau Conducere)")
@in_channel_check(ALLOWED_CHANNEL_ID)
@hr_or_conducere_check()
async def clockpanel_cmd(interaction: discord.Interaction):
    OWNER_ID = 286492096242909185
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            embed=make_embed("Permisiune", "Restricționat.", discord.Color.red(), interaction.user),
            ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=make_embed("Pontaj", "Folosește butoanele pentru a începe / opri pontajul.", discord.Color.blurple(), interaction.user),
        view=ClockButtons()
    )
    try:
        await log_command(interaction, "clockpanel", changed=False)
    except Exception:
        pass

@bot.tree.command(name="hrpanel", description="Postează panoul HR")
@in_channel_check(ALLOWED_CHANNEL_ID)
@hr_or_conducere_check()
async def hrpanel_cmd(interaction: discord.Interaction):
    OWNER_ID = 286492096242909185
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            embed=make_embed("Permisiune", "Restricționat.", discord.Color.red(), interaction.user),
            ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=make_embed("HR Panel", "Folosește butoanele pentru a interactiona cu panoul HR.", discord.Color.blurple(), interaction.user),
        view=HrButtons()
    )
    try:
        await log_command(interaction, "hrpanel", changed=False)
    except Exception:
        pass

@bot.tree.command(name="saspanel", description="Postează panoul SAS IN/OUT")
@in_sas_channel_check()
@hr_or_conducere_check()
async def saspanel_cmd(interaction: discord.Interaction):
    OWNER_ID = 286492096242909185
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            embed=make_embed("Permisiune", "Restricționat.", discord.Color.red(), interaction.user),
            ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=make_embed("SAS Pontaj", "Folosește butoanele pentru SAS.", discord.Color.blurple(), interaction.user),
        view=SASClockButtons()
    )
    await log_command(interaction, "saspanel", changed=False)

@bot.tree.command(name="sascoordpanel", description="Postează panoul SAS Coordonator")
@in_sas_channel_check()
@hr_or_conducere_check()
async def sascoordpanel_cmd(interaction: discord.Interaction):
    OWNER_ID = 286492096242909185
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            embed=make_embed("Permisiune", "Restricționat.", discord.Color.red(), interaction.user),
            ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=make_embed("SAS Coordonator Panel", "Folosește butoanele pentru a interactiona cu panelul.", discord.Color.blurple(), interaction.user),
        view=SASCoordonatorButtons()
    )
    await log_command(interaction, "sascoordpanel", changed=False)

@bot.tree.command(name="sasmemberpanel", description="Postează panoul Evidență Membri SAS")
async def sasmemberpanel_cmd(interaction: discord.Interaction):
    OWNER_ID = 286492096242909185
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            embed=make_embed("Permisiune", "Restricționat.", discord.Color.red(), interaction.user),
            ephemeral=True
        )
        return
    await interaction.response.send_message(
        embed=make_embed("Evidență Membri SAS", "Folosește butoanele pentru a gestiona membrii SAS în spreadsheet.", discord.Color.blurple(), interaction.user),
        view=SASMemberManagementButtons()
    )
    await log_command(interaction, "sasmemberpanel", changed=False)

# ------------ Say command -----------------
MAIN_GUILD_ID = int(need("MAIN_GUILD_ID"))

IMPORTANT_ID = int(need("IMPORTANT_ID"))
ANUNTURI_ID = int(need("ANUNTURI_ID"))
CHAT_ID = int(need("CHAT_ID"))

def _pd_mentions(guild: discord.Guild) -> str:
    ids = REQUIRED_PD_ROLE_ID if isinstance(REQUIRED_PD_ROLE_ID, list) else [REQUIRED_PD_ROLE_ID]
    parts = []
    for rid in ids:
        if isinstance(rid, int):
            role = guild.get_role(rid)
            if role:
                parts.append(role.mention)
    return " ".join(parts)

def _relay_state_text(sess: dict) -> str:
    ch_name = {
        IMPORTANT_ID: "Important",
        ANUNTURI_ID: "Anunțuri",
        CHAT_ID: "Chat"
    }.get(sess.get("channel_id"), "Neselectat")
    tag_txt = "DA" if sess.get("tag") else "NU"
    msg = sess.get("content") or "*<gol>*"
    return (
        f"📝 Draft Mesaj\n"
        f"Canal: **{ch_name}** | Tag PD: **{tag_txt}**\n"
        f"----------------------\n"
        f"{msg}"
    )

class RelayChannelSelect(discord.ui.Select):
    def __init__(self, user_id: int):
        options = [
            discord.SelectOption(label="Important", value=str(IMPORTANT_ID), description="Canal Important"),
            discord.SelectOption(label="Anunțuri", value=str(ANUNTURI_ID), description="Canal Anunțuri"),
            discord.SelectOption(label="Chat", value=str(CHAT_ID), description="Canal Chat"),
        ]
        super().__init__(placeholder="Alege canalul țintă", options=options, min_values=1, max_values=1, custom_id="relay_select_channel")
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return
        sess = bot.relay_sessions.get(self.user_id)
        if not sess:
            await interaction.response.send_message("Sesiune expirată.", ephemeral=True)
            return
        sess["channel_id"] = int(self.values[0])
        # Re-render
        await _relay_update_message(sess, interaction)

class RelayDraftView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        # Add select
        self.add_item(RelayChannelSelect(user_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nu este sesiunea ta.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Editează", style=discord.ButtonStyle.primary, custom_id="relay_edit_btn")
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        sess = bot.relay_sessions.get(self.user_id)
        if not sess:
            await interaction.response.send_message("Sesiune expirată.", ephemeral=True)
            return
        await interaction.response.send_modal(RelayEditModal(self.user_id, sess.get("content", "")))

    @discord.ui.button(label="Comută Tag", style=discord.ButtonStyle.secondary, custom_id="relay_toggle_tag")
    async def toggle_tag(self, interaction: discord.Interaction, button: discord.ui.Button):
        sess = bot.relay_sessions.get(self.user_id)
        if not sess:
            await interaction.response.send_message("Sesiune expirată.", ephemeral=True)
            return
        sess["tag"] = not sess.get("tag", False)
        await _relay_update_message(sess, interaction)

    @discord.ui.button(label="Trimite", style=discord.ButtonStyle.success, custom_id="relay_send")
    async def send_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        sess = bot.relay_sessions.get(self.user_id)
        if not sess:
            await interaction.response.send_message("Sesiune expirată.", ephemeral=True)
            return
        if not sess.get("channel_id"):
            await interaction.response.send_message("Selectează un canal.", ephemeral=True)
            return
        if not sess.get("content"):
            await interaction.response.send_message("Mesaj gol. Editează-l înainte.", ephemeral=True)
            return
        main_guild = bot.get_guild(MAIN_GUILD_ID)
        if not main_guild:
            await interaction.response.send_message("Main guild indisponibil.", ephemeral=True)
            return
        target = main_guild.get_channel(sess["channel_id"])
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Canal țintă invalid.", ephemeral=True)
            return
        content = sess["content"]
        if sess.get("tag"):
            mentions = _pd_mentions(main_guild)
            if mentions:
                content = f"{content}\n||{mentions}||"
        try:
            await target.send(content)
        except discord.Forbidden:
            await interaction.response.send_message("Fără permisiuni în canalul țintă.", ephemeral=True)
            return
        # Delete draft
        try:
            draft_ch = bot.get_channel(sess["draft_channel_id"])
            if isinstance(draft_ch, discord.TextChannel):
                msg = await draft_ch.fetch_message(sess["draft_id"])
                await msg.delete()
        except Exception:
            pass
        bot.relay_sessions.pop(self.user_id, None)
        await interaction.response.send_message("Trimis și șters draftul.", ephemeral=True)

    @discord.ui.button(label="Anulează", style=discord.ButtonStyle.danger, custom_id="relay_cancel")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        sess = bot.relay_sessions.pop(self.user_id, None)
        if sess:
            try:
                draft_ch = bot.get_channel(sess["draft_channel_id"])
                if isinstance(draft_ch, discord.TextChannel):
                    msg = await draft_ch.fetch_message(sess["draft_id"])
                    await msg.delete()
            except Exception:
                pass
        await interaction.response.send_message("Draft anulat.", ephemeral=True)

class RelayEditModal(discord.ui.Modal, title="Editează mesaj"):
    def __init__(self, user_id: int, current: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.content_input = discord.ui.TextInput(
            label="Mesaj",
            style=discord.TextStyle.paragraph,
            default=current,
            required=True,
            max_length=1800
        )
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction):
        sess = bot.relay_sessions.get(self.user_id)
        if not sess:
            await interaction.response.send_message("Sesiune expirată.", ephemeral=True)
            return
        sess["content"] = self.content_input.value.strip()
        await interaction.response.send_message("Mesaj actualizat.", ephemeral=True)
        # Update draft message
        await _relay_edit_draft_message(sess)

async def _relay_edit_draft_message(sess: dict):
    try:
        draft_ch = bot.get_channel(sess["draft_channel_id"])
        if isinstance(draft_ch, discord.TextChannel):
            msg = await draft_ch.fetch_message(sess["draft_id"])
            await msg.edit(content=_relay_state_text(sess), view=RelayDraftView(sess["user_id"]))
    except Exception:
        pass

async def _relay_update_message(sess: dict, interaction: discord.Interaction):
    try:
        await interaction.response.edit_message(content=_relay_state_text(sess), view=RelayDraftView(sess["user_id"]))
    except discord.InteractionResponded:
        # Fallback if already responded
        await interaction.edit_original_response(content=_relay_state_text(sess), view=RelayDraftView(sess["user_id"]))
    except Exception:
        pass

async def _relay_start_session(user: discord.Member, channel: discord.abc.Messageable) -> discord.Message:
    sess = {
        "user_id": user.id,
        "draft_channel_id": channel.id if hasattr(channel, "id") else None,
        "draft_id": None,
        "channel_id": None,
        "content": "",
        "tag": False
    }
    msg = await channel.send("Creare draft...", view=RelayDraftView(user.id))
    sess["draft_id"] = msg.id
    bot.relay_sessions[user.id] = sess
    await msg.edit(content=_relay_state_text(sess), view=RelayDraftView(user.id))
    return msg


async def _relay_end_session(user_id: int):
    sess = bot.relay_sessions.pop(user_id, None)
    if sess:
        try:
            draft_ch = bot.get_channel(sess["draft_channel_id"])
            if isinstance(draft_ch, discord.TextChannel):
                msg = await draft_ch.fetch_message(sess["draft_id"])
                await msg.delete()
        except Exception:
            pass

@bot.command(name="q", help="Inițiază draft relay (selectezi canal, editezi, apoi trimiți)")
async def relay_cmd(ctx: commands.Context):
    OWNER_ID = 286492096242909185
    if ctx.author.id != OWNER_ID:
        await ctx.reply("Permisiune refuzată.", mention_author=False, delete_after=5)
        return
    # Optionally delete the invoking message
    try:
        await ctx.message.delete()
    except Exception:
        pass

    # Post the Relay panel message with the button that opens your draft
    await ctx.send(
        embed=make_embed("Relay Panel", "Apasă 'Say' pentru a-ți deschide draftul Relay.", discord.Color.blurple(), ctx.author),
        view=RelayButtons()
    )

@bot.command(name="dbv", aliases=["dbvacumm"], help="Checkpoint WAL + VACUUM (owner only)")
async def dbvacuum_prefix(ctx: commands.Context):
    OWNER_ID = 286492096242909185
    if ctx.author.id != OWNER_ID:
        try:
            await ctx.reply("Permisiune refuzată.", mention_author=False, delete_after=5)
        except Exception:
            pass
        return
    try:
        checkpoint_and_vacuum()
        await ctx.reply(f"VACUUM OK. {db_stats()}", mention_author=False)
    except Exception as e:
        await ctx.reply(f"Eroare: {e}", mention_author=False)

@bot.command(name="dbs", aliases=["dbstats"], help="Afișează statistici DB (owner only)")
async def db_stats_command(ctx: commands.Context):
    OWNER_ID = 286492096242909185
    if ctx.author.id != OWNER_ID:
        try:
            await ctx.reply("Permisiune refuzată.", mention_author=False, delete_after=5)
        except Exception:
            pass
        return
    try:
        db_stats_text = db_stats()
        await ctx.reply(f"DB Stats:\n{db_stats_text}", mention_author=False)
    except Exception as e:
        await ctx.reply(f"Eroare: {e}", mention_author=False)
# --------------- Run ---------------
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("BOT_TOKEN missing (regenerate in Developer Portal).")
    bot.run(TOKEN)
