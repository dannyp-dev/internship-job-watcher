# Internship Phone Alert Watcher

This personal watcher targets a check every 15 minutes from 10:00 AM through midnight Eastern. A Cloudflare Cron Trigger provides the primary schedule and starts the GitHub Actions workflow even when your computer is off. A low-frequency GitHub schedule provides a fallback if Cloudflare stops dispatching. The app also enforces the Eastern notification window itself, so a delayed GitHub run cannot alert after midnight.

It currently monitors:

- Simplify's Summer 2027 list
- Simplify's Off-Season 2027 list
- Two additional public internship trackers as cross-checks
- 193 configured direct company feeds, including Amazon, Cloudflare, Meta, Google, Apple, Microsoft, Snowflake, Snap, Plaid, IMC, DRW, Virtu Financial, Flow Traders, validated Greenhouse and Ashby boards, and Workday boards

`coverage_watchlist.json` names 311 target companies, including a finance and quant tier. It is a planning roster, not 311 direct monitors. Bloomberg, Goldman Sachs, and JPMorgan Chase were already on the roster; Jane Street, Optiver, and other quant and banking firms are now included. Companies without a verified direct feed can appear through the four aggregate lists, but those lists do not guarantee complete or immediate coverage. IMC, DRW, Virtu Financial, and Flow Traders have direct Greenhouse feeds. A direct source should only be added when its live endpoint has been checked. `company_watchlist.json` retains the researched 99-company registry and its direct feeds.

Scale AI and Anthropic are monitored directly. The AI product and infrastructure group also includes Glean, Cohere, Perplexity, Cognition, Decagon, Sierra, Modal, Baseten, Cursor, and OpenAI, with feed-failure alerts and checks for unexpectedly empty feeds. Verified on October 19, 2026. Ashby locations include structured country and region data so city-only labels are not discarded. Decagon's verified software role titled `Engineering Intern` is accepted through a company-specific title rule; specialty and location exclusions still apply. Internship words must match complete words, preventing `Internal Tooling` or `Cooperative AI` from being mistaken for internships.

The October 21 expansion adds 48 employer boards independently of whether an internship is currently open, including 32 with no matching internship at verification. H&R Block, Siemens Digital Industries Software (US), Zillow, and Old Mission Capital now have direct feeds. The new sources accept healthy empty searches and alert on transport or schema failures. A captured list of known current URLs provides onboarding history; a future posting is not silently swallowed by a blanket first-success baseline. See [DIRECT_COVERAGE.md](DIRECT_COVERAGE.md) for the actual direct-company list, the aggregate-only roster, and remaining gaps.

A follow-up adds 11 more verified direct boards: GoDaddy, Squarespace, Intercom, Nextdoor, SeatGeek, IXL Learning, Gemini, Clear Street, Justworks, Oscar Health, and Trumid. Five Rings Capital was already directly connected. These employers remain monitored even without current matching openings. Company selection favors strong software internship opportunities and major US employment hubs; no hard salary or additional location filter has been introduced.

The filters favor United States undergraduate internships in backend, full-stack, platform, distributed systems, cloud, and site reliability. Titles focused on ML, research, data engineering, frontend-only, design, mobile, embedded, firmware, or hardware are excluded. RTX and its Raytheon, Collins Aerospace, and Pratt & Whitney brands, plus L3Harris, are blocked. The watcher does not filter, record, or report pay.

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

New alerts contain the company, role, location, source, and a tappable **Open application** button.

## LinkedIn alerts

LinkedIn sends job-alert emails directly to your Gmail inbox. The watcher does not read your LinkedIn emails or forward them to Telegram. Manage alert searches and their daily email settings in LinkedIn's **Jobs > Preferences > Job alerts** page. The old Gmail app password is no longer needed by this watcher and can be revoked in your Google Account.

## Put it on GitHub

1. Create a new private GitHub repository.
2. Upload every file in this folder, including the hidden `.github` folder.
3. Add the two Telegram secrets described above.
4. Enable GitHub Actions if GitHub asks.
5. Run the test and baseline workflows once.

The Cloudflare scheduler wakes every 15 minutes and dispatches the GitHub workflow only from 10:00 AM through 11:59 PM in `America/New_York`. This produces 56 primary checks per day. GitHub also invokes the workflow every four hours as a safety net; invocations outside the active Eastern window exit immediately. Together they use at most 1,922 workflow invocations in a 31-day month. The time checks automatically handle standard and daylight saving time.

The first normal run creates a baseline and records existing roles without flooding your phone. Alerts begin for matching roles found after that baseline.

New direct feeds also establish their own first-run baseline. Current listings on those feeds are recorded without a backlog of alerts. Subsequent additions are eligible for notifications. A direct feed is polled every scheduled check during the active window, but an official career site can change its API or block automated requests; critical feed failures produce a health alert. The 301-company roster remains a target list, not a claim that every company has a working direct feed.

Microsoft's careers search rate-limits broad pagination. Its direct check uses a small set of targeted software-internship searches on each run, while the aggregate trackers remain a fallback for results beyond those search pages. This feed is best-effort, not exhaustive.

Of the 25 companies in Tier 1, 21 now have configured direct feeds. ByteDance/TikTok, Uber, LinkedIn, and Rippling currently rely on the aggregate trackers. Their official sites do not currently provide a verified, reliable feed in this project, so coverage for those four is not guaranteed. A configured direct feed can also change or fail; the critical ones are checked for health on each run.

The workflow retries transient state-history push failures up to four times. This reduces the chance that a successfully delivered Telegram alert is treated as new again on the next check. A GitHub fallback run skips its check if Cloudflare already started one in the previous 10 minutes. A single Microsoft rate-limit response is recorded but does not trigger a phone alert; three consecutive rate-limited checks do. Meta careers searches also retry with a fresh page token when the public endpoint briefly omits results, and only repeated missing-result checks trigger a source alert.

## Review or tune the watchlist

- `coverage_watchlist.json` is the 301-company target roster. It does not itself add a feed.
- `WATCHLIST.md` and `company_watchlist.json` contain the 99-company research list, including 35 older direct sources.
- `config.json` contains the current direct source configuration; it is combined with enabled sources in `company_watchlist.json` at runtime.
- `config.json` controls fit, location, and notifications.

To monitor only a subset, add company names to `company_allowlist`. To suppress companies, use `company_blocklist`.

To narrow location, add values such as:

```json
"location_words": ["Remote", "Florida", "FL", "New York", "Seattle"]
```

Listings labeled PhD, doctoral, or MBA are excluded.

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
