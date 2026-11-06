"""Daily digest by email (Gmail SMTP) and WhatsApp (CallMeBot free API, or Twilio)."""
import smtplib, ssl, urllib.request, urllib.parse, base64, datetime, html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def build_digest(store, tracker_url):
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=1)).isoformat()
    new = store.jobs("first_seen >= ? AND status IN ('new','ready')", (since,))
    applied = store.jobs("applied_on >= ?", (since,))
    queue = store.jobs("status IN ('new','ready')")
    stale_cut = (today - datetime.timedelta(days=10)).isoformat()
    follow = store.jobs("status='applied' AND applied_on <= ?", (stale_cut,))
    counts = {}
    for j in store.jobs():
        counts[j["status"]] = counts.get(j["status"], 0) + 1

    def rows(js, n=25):
        return "".join(
            f"<tr><td>{j['score']}</td><td><b>{html.escape(j['company'])}</b></td>"
            f"<td><a href='{j['url']}'>{html.escape(j['title'])}</a></td><td>{html.escape(j['location'][:40])}</td>"
            f"<td>{j['variant'].upper()}</td></tr>" for j in js[:n]) or "<tr><td colspan=5>None</td></tr>"

    tbl = "<table border=1 cellpadding=4 cellspacing=0 style='border-collapse:collapse;font:13px Arial'>" \
          "<tr><th>Fit</th><th>Company</th><th>Role</th><th>Location</th><th>Resume</th></tr>{}</table>"
    body = f"""<div style='font:14px Arial'>
<h2>Job agent – {today:%a %d %b %Y}</h2>
<p><b>{len(new)}</b> new matching roles · <b>{len(applied)}</b> applied in last 24h ·
<b>{len(queue)}</b> waiting in your queue · <b>{len(follow)}</b> need a follow-up</p>
<p>Pipeline: {' · '.join(f'{k}: {v}' for k, v in sorted(counts.items()))}</p>
<p><a href='{tracker_url}'>Open tracker to update statuses</a></p>
<h3>New roles (best fit first)</h3>{tbl.format(rows(sorted(new, key=lambda j: -j['score'])))}
<h3>Applied in last 24h</h3>{tbl.format(rows(applied))}
<h3>Applied 10+ days ago, no update – find a referral or recruiter</h3>{tbl.format(rows(follow))}
</div>"""
    top = sorted(new, key=lambda j: -j["score"])[:5]
    wa = (f"Job agent {today:%d %b}: {len(new)} new roles, {len(applied)} applied, {len(queue)} in queue, "
          f"{len(follow)} need follow-up.\n" +
          "\n".join(f"- {j['company']}: {j['title']} ({j['score']})" for j in top))
    return f"Job agent: {len(new)} new roles, {len(applied)} applied ({today:%d %b})", body, wa


def send_email(cfg, subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["gmail_user"], cfg["to"]
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(cfg["gmail_user"], cfg["gmail_app_password"])
        s.send_message(msg)


def send_whatsapp(cfg, text):
    if cfg.get("provider") == "twilio":
        url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg['twilio_sid']}/Messages.json"
        data = urllib.parse.urlencode({"From": f"whatsapp:{cfg['twilio_from']}", "To": f"whatsapp:{cfg['phone']}",
                                       "Body": text[:1500]}).encode()
        req = urllib.request.Request(url, data=data)
        tok = base64.b64encode(f"{cfg['twilio_sid']}:{cfg['twilio_token']}".encode()).decode()
        req.add_header("Authorization", f"Basic {tok}")
    else:  # CallMeBot: free, personal use, one-time activation from your phone
        q = urllib.parse.urlencode({"phone": cfg["phone"], "text": text[:1500], "apikey": cfg["callmebot_apikey"]})
        req = urllib.request.Request(f"https://api.callmebot.com/whatsapp.php?{q}")
    urllib.request.urlopen(req, timeout=30).read()
