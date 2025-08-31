# Discord Duty Bot

A Discord bot for managing PD/SAS duty time, HR tools, action logs, and announcement relays. Built on discord.py (Python 3.11) with timezone-aware timestamps and persistent button views.

## Features
- PD Clock IN/OUT
  - Persistent buttons gated by channel and PD roles
  - User “My Pontaje” modal with prefilled date
- SAS Clock IN/OUT
  - Separate channel/role gating
  - Coordinator panel
- SAS Action Log
  - “Evidență acțiune” post with 5-minute ✅ join window
  - Live participants update, reaction add/remove
  - Finalizes by awarding points via external API (optional)
- End‑of‑Day Confirm (EOD)
  - At 23:55, DM each user with an open session to react ✅ within a window (default 5 min)
  - On confirm: saves the session with end 23:59:59, edits the DM with interval and minutes, logs confirmation
  - On timeout: deletes the open session, edits the DM to “Neconfirmat…”, logs non-confirmation
  - If DMs are closed, falls back to posting in configured channels
- HR Panel
  - Daily report (all or per-user)
  - Add minutes (creates a finished session)
  - Remove specific sessions (UI picker)
  - List today’s ongoing sessions (PD/SAS)
  - Stop specific sessions (paginated UI)
  - Warn add/reset/status with channel posts and DMs
- Relay (announcements), working in another server as well
  - Post a Relay panel via !q
  - Draft message workflow (select channel, edit, tag PD roles, send/cancel)
  - Draft sessions auto-clean on message delete
- Logging
  - Rich embed logs to LOGS_CHANNEL_ID
  - File log logs.txt
  - Extra per-action channels for “add minutes”, “ongoing stop”, “delete pontaj”
  - Console warnings/errors mirrored to a Discord channel
- Misc
  - Leave messages on member exit
  - Console relay (stdin -> channel) via webhook (optional)

## Slash Commands (panels)
- /clockpanel – PD Clock IN/OUT panel
- /hrpanel – HR tools panel
- /saspanel – SAS IN/OUT panel
- /sascoordpanel – SAS Coordinator panel

## Message Command
- !q – posts the Relay panel (create/edit/send announcement drafts)

## End of day (EOD) Flow
- 23:55: bot collects open sessions (PD + SAS) for today and sends confirmation prompts
- User must react ✅ within EOD_CONFIRM_WINDOW_SECS (default 300s)
- Confirmed: end is set to 23:59:59, message edited with minutes, logged
- Not confirmed: session removed, message edited, logged
- Works in DMs; falls back to configured channel if DMs are blocked

## Requirements
- See requirements.txt file, after installing requirments.txt run pip install -r requirements.txt

## Quick start
1) Create a .env with your IDs and tokens (see template below).
2) Install deps:
   - pip install -U discord.py python-dotenv aiohttp
3) Ensure database.py exists (SQLite helpers: init_db, add_clock_in, update_clock_out, etc.).
4) Run:
   - python pontaje.py
5) Post panels with slash commands; use !q for the Relay panel.

## .env template
```env
# Bot token from the Discord Developer Portal
BOT_TOKEN=YOUR_TOKEN
# IANA timezone for all timestamps (e.g., Europe/Bucharest)
TIMEZONE=Europe/Bucharest

# Guild and channels
# Main guild where panels/messages are posted, basically the id of the main server where the bot is used
MAIN_GUILD_ID=0
# PD panel channel (Clock IN/OUT + HR panel live here)
ALLOWED_CHANNEL_ID=0
# HR-only helper channel (if used by your workflows), can be the same as the one above if you paste the hr panel there
ALLOWED_HR_CHANNEL_ID=0
# Public channel where warn embeds are posted
ALLOWED_PUNISH_CHANNEL_ID=0
# Central logs channel (command logs, EOD logs, warnings, console warnings)
LOGS_CHANNEL_ID=0
# Channel where “<user> has left the server” is posted
LEAVE_CHANNEL_ID=0

# Optional additional log channels (comma separated IDs), all three of them can be in a different discord server, this is the main utility of this log channels
# Also mirror “Adaugă Minute” logs here (besides LOGS_CHANNEL_ID)
ADDMINUTES_LOG_CHANNEL_ID=
# Also mirror “Oprește Pontaje” logs here
ONGOING_STOP_CHANNEL_ID=
# Also mirror “Șterge Pontaj” logs here
DELETE_PONTAJ_CHANNEL_ID=

# PD roles (comma-separated role IDs) and HR/Conducere role IDs
# PD role IDs allowed to use the PD panel; comma-separated numbers, no spaces (e.g., 123,456)
REQUIRED_PD_ROLE_NAME=123,456
# HR role ID (single number)
REQUIRED_HR_ROLE_NAME=0
# Conducere role ID (single number)
CONDUCERE_ROLE_ID=0

# SAS
# SAS IN/OUT panel channel
SAS_CHANNEL_ID=0
# SAS action-log channel (Evidență Acțiune), where the action will be posted
SAS_ACTIUNI_CHANNEL_ID=0
# SAS member role ID (single number)
SAS_ROLE_IDS=0
# SAS coordinator role ID (single number)
SAS_COORDONATOR_IDS=0

# Relay targets
# Channels used by the Relay draft “Send” action
IMPORTANT_ID=0
ANUNTURI_ID=0
CHAT_ID=0

# End-of-day confirm
# Seconds users have to react ✅ to save their session (default 300 = 5 minutes).
# Bot DMs first; if DMs are closed, it falls back to ALLOWED_CHANNEL_ID (PD) or SAS_CHANNEL_ID (SAS).
EOD_CONFIRM_WINDOW_SECS=300

# Console relay (optional)
# If set, console lines can be relayed to this channel via the console relay buttons
CONSOLE_RELAY_DEFAULT_CHANNEL_ID=
# Webhook name/avatar used by console relay (webhook created automatically if missing)
CONSOLE_WEBHOOK_NAME=console
CONSOLE_WEBHOOK_AVATAR_URL=

# External API for SAS callsigns (optional), this needs an excel with a sheet where the SAS callsigns are writeen in a column, and a script that will add a point to each callsign that was at that action
# POST endpoint + token to send SAS callsigns after action-log window closes
ACTIVITY_API_URL=
ACTIVITY_API_TOKEN=
# Target sheet/collection name (default: RAZII)
ACTIVITY_API_SHEET=
```

## Notes
- Persistent Views are registered in setup_hook; if you restart the bot, buttons on already-sent messages continue to work.
- Clearing reactions in fallback channels requires Manage Messages; if missing, the bot still edits the message.
- All times use your TIMEZONE (default Europe/Bucharest).
- The DB layer is abstracted in database.py (SQLite recommended).

## License
MIT, see license in the repository.
