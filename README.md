# Internship Phone Alert Watcher

This personal watcher targets a check every 15 minutes from 10:00 AM through midnight Eastern. GitHub Actions runs it even when your computer is off. The app also enforces the Eastern notification window itself, so a delayed GitHub run cannot alert after midnight.

It currently monitors:

- Simplify's Summer 2027 list
- Simplify's Off-Season 2027 list
- A curated 100-company target list
- 35 validated direct company feeds across Greenhouse, Lever, Ashby, and SmartRecruiters

The other 65 companies remain covered through Simplify. Their Workday, custom, iCIMS, Oracle, and SuccessFactors boards are recorded in `company_watchlist.json` for future adapters.

The filters focus on United States undergraduate internships related to backend, full-stack, platform, infrastructure, distributed systems, cloud, reliability,34 ML systems, data engineering, and research engineering. Confirmed hourly pay below $30 is excluded. When pay is not published in a feed, the Telegram alert says `Pay: unverified`.

This app only monitors and notifies. It never applies to a job or contacts anyone.

## Connect Telegram

1. In Telegram, open the verified `@BotFather` account.
2. Send `/newbot` and follow its prompts. BotFather will give you a bot token. Treat that token like a password and do not paste it into chat, source code, or `config.json`.
3. Open the new bot from the link BotFather provides and press **Start** once. You do not need to message it every day.
4. On your computer, open PowerShell in this folder and temporarily set the token for that window:

```powershell
$env:TELEGRAM_BOT_TOKEN = "paste-your-token-here"
python job_watcher.py --telegram-chat-id
```

5. Copy the numeric value printed after `TELEGRAM_CHAT_ID=`.
6. In your private GitHub repository, open **Settings > Secrets and variables > Actions** and create these repository secrets:

   - `TELEGRAM_BOT_TOKEN`: the token from BotFather
   - `TELEGRAM_CHAT_ID`: the numeric ID printed by the helper

7. Open **Actions > Check for new internships > Run workflow**. First run it with the test option enabled to confirm the Telegram alert. Then run it once with the test option off to establish the baseline.

New alerts contain the company, role, location, source, pay status, and a tappable **Open application** button.

## Put it on GitHub

1. Create a new private GitHub repository.
2. Upload every file in this folder, including the hidden `.github` folder.
3. Add the two Telegram secrets described above.
4. Enable GitHub Actions if GitHub asks.
5. Run the test and baseline workflows once.

The included schedule offers 60 possible checks per day, or at most 1,860 scheduled runs in a 31-day month. The workflow uses one UTC cron entry that covers both standard and daylight saving time. Python then enforces exactly 10:00 AM through midnight in `America/New_York`. GitHub can delay or drop scheduled jobs, so 15 minutes is a target rather than a real-time guarantee.

The first normal run creates a baseline and records existing roles without flooding your phone. Alerts begin for matching roles found after that baseline.

## Review or tune the watchlist

- `WATCHLIST.md` is the readable 100-company list, with core, broad, and selective priority labels.
- `company_watchlist.json` is the machine-readable registry used by the watcher.
- `config.json` controls fit, location, compensation, and notifications.

To monitor only a subset, add company names to `company_allowlist`. To suppress companies, use `company_blocklist`.

To narrow location, add values such as:

```json
"location_words": ["Remote", "Florida", "FL", "New York", "Seattle"]
```

Listings labeled PhD, doctoral, or MBA are excluded. Missing compensation is not treated as unpaid because many legitimate postings omit compensation from their public feed.

## Run locally

Python 3.11 or newer is enough. No packages are required.

```powershell
python job_watcher.py --dry-run
python job_watcher.py --test-notification
python job_watcher.py
```

To run it automatically only while your computer is on, create a Windows Task Scheduler task that runs `python job_watcher.py` at sign-in and repeats every 14 minutes. GitHub Actions is already the simpler always-on option.

## Optional ntfy fallback

Telegram is the default provider. To use ntfy instead, change `notification.provider` in `config.json` to `ntfy`, subscribe to a long private topic in the ntfy app, and add `NTFY_TOPIC` as a GitHub Actions secret.

## Safety and behavior

- Notification credentials stay in environment variables or GitHub Secrets.
- The repository stores only public job details and notification history.
- The workflow requests write access only to update `data/state.json`.
- One broken source becomes a warning and does not stop the others.
- Duplicate jobs found in several feeds produce one alert.
- A listing keeps the same identity when a company makes a small title or location edit.
- Delayed scheduled runs outside 10:00 AM through midnight Eastern exit without sending alerts.
- The bot can keep sending alerts after you press Start once. You do not have to keep chatting with it.

## Test the code

```powershell
python -m unittest discover -s tests -v
```
