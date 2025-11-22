# 🤖 Bot Pontaje - Discord Time Tracking System

A comprehensive Discord bot for managing time tracking, attendance, and reporting for organizations with multiple departments (Police Department, Human Resources, and SAS).

## 📋 Features

### ⏰ Time Tracking System
- **Clock In/Out**: Members can clock in and out of their shifts with a single button
- **Automatic EOD Handling**: End-of-day confirmations at 23:55 with automatic session closure
- **Break Management**: Track breaks and pauses during shifts
- **Multi-department Support**: Separate tracking for PD, HR, and SAS roles

### 📊 Reporting & Analytics
- **Daily Reports**: View individual member attendance for specific dates
- **Weekly Reports**: Generate comprehensive weekly summaries (Sunday-Saturday)
  - Exportable as formatted `.txt` files
  - Shows daily breakdown and weekly totals
  - Automatic member sorting by callsign
- **Google Sheets Integration**: Sync attendance data to Google Sheets for advanced reporting
- **Historical Data**: Access past records with date range selection

### 👥 Role-Based Access Control
- **Police Department (PD)**: Standard time tracking
- **Human Resources (HR)**: Administrative oversight and reporting
- **SAS Coordinator**: Specialized team management and weekly reports
- **Department-specific Permissions**: Hierarchical access to features

### 🔔 Notifications & Reminders
- **EOD Confirmations**: Automated end-of-day session prompts
- **Session Warnings**: Alerts for extended shifts
- **Activity Logging**: Comprehensive audit trail of all actions

## 🚀 Setup

### Prerequisites
- Python 3.9+
- Discord Bot Token
- Google Sheets API credentials (for reporting features)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/AlexandruCL/BotPontajeProject.git
   cd BotPontajeProject
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   
   Create a `.env` file in the project root:
   ```env
   DISCORD_TOKEN=your_discord_bot_token_here
   GUILD_ID=your_guild_id_here
   ```

4. **Set up Google Sheets credentials**
   
   - Create a service account in Google Cloud Console
   - Download the credentials JSON file
   - Save it as `credentials.json` in the project root
   - Enable Google Sheets API and Google Drive API

5. **Configure database**
   
   The bot uses SQLite by default. The database will be created automatically on first run.

### Running the Bot

```bash
python bot2.py
```

## 📖 Usage

### For Members

**Clocking In/Out:**
1. Navigate to your department's channel
2. Click the "Clock In" button
3. When your shift ends, click "Clock Out"
4. Confirm or dismiss the End-of-Day prompt at 23:55

**Viewing Your Time:**
- Use the "Raport Pontaj / Zilnic" button to see your daily hours
- Select the date you want to view

### For Coordinators

**Weekly Reports (SAS):**
1. Click "Pontaje / Săptămânale" button
2. Choose "Săptămâna Curentă" or "Săptămâna Trecută"
3. Download the generated `.txt` report

**Report Format:**
```
Nume                       Du     Lu     Ma     Mi     Jo     Vi     Sb  Total
--------------------------------------------------------------------------------
[S-01] John Doe          120    180     90      0    150    200    100    840
[S-02] Jane Smith          0     60    120    180     90      0    150    600
```

### For Administrators

**HR Functions:**
- View all member attendance
- Generate department-wide reports
- Access Google Sheets sync features
- Manage historical records

## 🗂️ Project Structure

```
BotPontajeProject/
├── bot2.py              # Main bot file with Discord commands
├── database.py          # Database operations and queries
├── tickete.py           # Ticketing system (if applicable)
├── credentials.json     # Google Sheets API credentials (not in git)
├── .env                 # Environment variables (not in git)
├── requirements.txt     # Python dependencies
├── logs.txt            # Application logs
└── README.md           # This file
```

## 🔧 Configuration

### Role IDs
Update the role IDs in `bot2.py` to match your Discord server:

```python
PD_ROLE_IDS = [123456789, ...]  # Police Department roles
HR_ROLE_IDS = [123456789, ...]  # Human Resources roles
SAS_ROLE_IDS = [123456789, ...] # SAS roles
```

### Time Zone
The bot uses Romania timezone (`Europe/Bucharest`) by default. Modify in `bot2.py`:

```python
TZ = ZoneInfo("Europe/Bucharest")
```

## 📝 Database Schema

The bot uses SQLite with the following key tables:
- **clock_sessions**: Stores clock in/out records
- **eod_confirmations**: Tracks end-of-day confirmations
- **activity_logs**: Audit trail of all actions

## 🔒 Security Notes

- ✅ `credentials.json` is gitignored (never commit to version control)
- ✅ `.env` file is gitignored (keep tokens secure)
- ✅ Private repository recommended for production use
- ✅ Service account permissions should be minimal (read/write to specific sheets only)

## 🐛 Troubleshooting

**Bot not responding:**
- Check Discord token in `.env`
- Verify bot has necessary permissions in your server
- Check `logs.txt` for error messages

**Google Sheets errors:**
- Verify `credentials.json` is in the project root
- Ensure Google Sheets API is enabled
- Check service account has access to target sheets

**Time tracking issues:**
- Confirm user has the appropriate role
- Check database permissions
- Verify timezone settings match your location

## 🤝 Contributing

Contributions are welcome! Please follow these steps:
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License

See license in repository.

## 👤 Author

**Alexandru CL**
- GitHub: [@AlexandruCL](https://github.com/AlexandruCL)

## 🙏 Acknowledgments

- Discord.py community
- gspread library for Google Sheets integration
- All contributors and testers

---

**Note:** This bot is designed for internal organizational use. Ensure compliance with your organization's data policies and Discord's Terms of Service.
