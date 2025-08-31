# Privacy Policy
Last updated: [2025-08-26]

1) Summary
This bot processes limited Discord data to provide time tracking, HR moderation, SAS action logging, announcement relay, and ticket sync features. No sale of personal data.

2) Data processed
- Identifiers: Discord user ID, username, display name, role IDs, channel IDs, message IDs.
- Activity and usage:
  - Clock sessions: start/end timestamps and totals (PD/SAS).
  - HR warns: counts and notes when used by authorized roles.
  - SAS actions: ✅ reactions for participation; callsigns parsed from display names (format S-##).
  - Announcement drafts and sent messages (admin-only relay).
  - Leave notifications (user left server).
- Message content:
  - Reads specific channel messages/embeds to extract ticket purchases for syncing to a Google Apps Script endpoint.
  - Reads and edits its own relay drafts.
- Logs:
  - Operational messages to configured Discord channels and a local logs.txt (timestamps and brief context/error text).

3) Sources
- Discord API events, commands, reactions, and message history in allowed channels.
- User-provided inputs via commands, buttons, and modals.
- Display names (for callsign extraction).

4) Purposes
- Provide and secure the bot’s features (time tracking, HR moderation, SAS participation).
- Generate admin reports and summaries.
- Sync ticket data to spreadsheets via Google Apps Script.
- Post announcements with optional role mentions (admin-only).
- Diagnostics and abuse prevention (logging and permission checks).

5) Legal bases (where applicable)
- Performance of a task requested by server admins/members.
- Legitimate interests of the server community in moderation and activity management.
- Consent where required (e.g., reacting to opt-in posts).

6) Sharing and transfers
- Discord: Processing occurs on Discord per their terms.
- Google Apps Script endpoint (tickete.py): Sends ticket data (Discord user ID and ticket code) to WEB_APP_URL.
- Activity API (bot2.py): Sends callsigns (e.g., S-##) to ACTIVITY_API_URL with a token.
- Hosting/infrastructure providers used to run the bot.
- No sale of personal data; additional sharing only if required by law or to protect rights/safety.

7) Retention
- Clock sessions and SAS sessions: kept until removed per server policy or admin request.
- HR warns: kept until reset by authorized roles.
- SAS action posts: live-updated for ~5 minutes; a summary message may remain. Callsigns may be sent to the external Activity API.
- Operational logs: kept in logs.txt and in Discord log channels per admin policy.
- Ticket sync data: transmitted to the Google Apps Script; storage/retention there is managed by that destination.
- Minimal records may be retained for moderation/audit.

8) Security
- Role- and channel-based access controls.
- Limited, purpose-focused collection.
- Reasonable technical and organizational measures; no method is 100% secure.

9) Your choices and rights
- You may request review or deletion of your data by contacting the operator; include your Discord user ID to locate records.
- Some records (e.g., moderation actions or aggregated reports) may be retained if needed for server operations or law.

10) Children
- Intended for Discord users who meet Discord’s minimum age. Do not use if below the applicable age in your region.

11) International transfers
- Data may be processed or stored in other countries depending on hosting and third-party endpoints (e.g., Google services).

12) Changes
- This policy may be updated; the “Last updated” date will be revised and material changes may be announced in the server.

13) Contact
- Controller/Operator: Cristea Laur-Alexandru
- Contact: alex.cristea.laur2004@gmail.com
