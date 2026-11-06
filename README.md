# Job Agent: PM / PO / TPM / Delivery roles at GCCs in India

Version 1 runs on your laptop. Every morning it:
1. Checks the careers portals of every company in `companies.csv` for new PM / PO / TPM / Program / Project / Delivery Manager roles in India.
2. Reads each job description, scores your fit (0–100), picks the PM or TPM resume, and lists the JD keywords your resume doesn't cover.
3. Writes a tailored resume (DOCX) for each role scoring 60+. It only uses facts from `resumes/master.json`, and rejects any output that adds a number not in your resume.
4. Sends you a daily summary by email and WhatsApp.

Open the tracker (`python -m jobagent tracker`) to see every role, open the JD and the tailored resume, and change status:
new → ready → applied → screening → interview → final round → offer / rejected / no response / skipped / closed.

## One-time setup (about 20 minutes)
1. Install Python 3.11 or newer. Then, in this folder: `pip install python-docx`
2. `copy config.example.toml config.toml` (Windows) or `cp config.example.toml config.toml` (Mac), then fill in:
   - **Gmail app password**: Google Account → Security → turn on 2-Step Verification → App passwords → create one for "Mail". Paste the 16 characters into `gmail_app_password`.
   - **WhatsApp (CallMeBot, free)**: save +34 644 66 32 62 in your contacts, then send it the WhatsApp message `I allow callmebot to send me messages`. It replies with an API key; paste it into `callmebot_apikey`. (Twilio also works if you prefer a paid, more reliable option.)
   - **Resume tailoring (no cost)**: install Claude Code and run `claude` once to log in with your Claude account. The agent calls it in the background. Leave `api_key` blank.
3. Test it: `python -m jobagent scan`, then `python -m jobagent health` to see which company portals worked.
4. Fix or remove the companies marked ERR in `companies.csv`. For an ERR company, open its careers site, click any job, and copy the job-board link (for example `xyz.wd3.myworkdayjobs.com/External`) into `careers_url`.

## Run it daily
- **Windows**: Task Scheduler → Create Basic Task → Daily 8:00 → Start a program: `python`, arguments `-m jobagent daily`, "Start in" = this folder.
- **Mac**: `crontab -e`, then add `0 8 * * * cd /path/to/jobagent && /usr/bin/python3 -m jobagent daily >> agent.log 2>&1`

## Commands
| Command | What it does |
|---|---|
| `python -m jobagent daily` | scan + tailor + apply + digest |
| `python -m jobagent apply` | apply automatically to the top roles (limits in `[apply]`) |
| `python -m jobagent scan` | look for new roles only |
| `python -m jobagent tailor` | tailor resumes for new roles scoring 60+ |
| `python -m jobagent digest` | send the email and WhatsApp summary now |
| `python -m jobagent tracker` | open the tracker at http://localhost:8765 |
| `python -m jobagent health` | which company portals could or couldn't be read |

## Adding companies
Add a row to `companies.csv`. A direct job-board link works best. Supported job boards: Workday (`*.myworkdayjobs.com`), Greenhouse, Lever, SmartRecruiters, Oracle (`*.oraclecloud.com/hcmUI/CandidateExperience`), and Amazon. For other companies, the agent opens the careers page and looks for one of these links.

## Coming next
- **Apply assist**: a browser bot (Playwright) using your Chrome profile. It opens each "ready" role, logs in or creates the portal account (and records it in the `accounts` table; passwords stay in Bitwarden), fills the form from `[profile]`, attaches the tailored resume, and stops for your one-click submit.
- **Referral finder**: for each role, find your LinkedIn connections at that company and draft outreach messages.
- **More companies**: Eightfold and SuccessFactors portals (Amex, Microsoft and several banks use these).
