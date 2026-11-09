"""Apply to roles automatically: opens each job in Chrome and lets Claude fill and submit the application.

Picks roles at or above [apply] min_score, best fit first, within max_per_day and max_per_company
(per company_window_days). Each form page is read into a list of fields; Claude decides what to type,
pick, tick, upload or click, using [profile], resumes/master.json and the job description.

Passwords: one random password per portal, kept in Windows Credential Manager (service "jobagent:<host>").
Claude never sees it: it writes {{PASSWORD}} and the agent substitutes the real one.
Verification emails are read from Gmail over IMAP with the same app password used for the digest.
CAPTCHAs are never solved: the role is left for you with a note.
Every attempt leaves a log and screenshot in output/apply_logs/.
"""
import datetime, email, imaplib, json, os, re, secrets, string, time, traceback, unicodedata
from email.header import decode_header
from urllib.parse import urlparse

PROFILE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "jobagent", "browser")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
MAX_STEPS = 60            # Claude calls per application (Workday forms run 5-7 pages)
MAX_MINUTES = 35          # per application
MAX_ATTEMPTS = 2          # after this many failed tries a role is left for you

SNAPSHOT_JS = r"""(start) => {
  let n = start; const out = [];
  const txt = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 160);
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const labelOf = e => {
    let l = e.getAttribute('aria-label') || '';
    const lb = e.getAttribute('aria-labelledby');
    if (lb) l = lb.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    if (!l && e.id) { const f = document.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (f) l = f.innerText; }
    if (!l) { const c = e.closest('label'); if (c) l = c.innerText; }
    if (!l) { const fs = e.closest('fieldset'); const lg = fs && fs.querySelector('legend'); if (lg) l = lg.innerText; }
    if (!l) l = e.getAttribute('placeholder') || e.getAttribute('title') || e.getAttribute('name') || e.getAttribute('data-automation-id') || '';
    return txt(l);
  };
  const q = 'input,textarea,select,button,a[href],[role=button],[role=combobox],[role=radio],[role=checkbox],' +
            '[role=option],[role=link],[role=menuitem],[role=tab],[role=switch],[contenteditable=true]';
  for (const e of document.querySelectorAll(q)) {
    const type = (e.getAttribute('type') || '').toLowerCase(), role = e.getAttribute('role') || '';
    if (type === 'hidden') continue;
    if (type !== 'file' && !vis(e)) continue;
    if (e.tagName === 'A' && !txt(e.innerText) && !e.getAttribute('aria-label')) continue;
    let id = e.getAttribute('data-ja');
    if (!id) { id = String(n++); e.setAttribute('data-ja', id); }
    const d = {id: +id, tag: e.tagName.toLowerCase()};
    if (type || role) d.type = type || role;
    const label = labelOf(e); if (label) d.label = label;
    const t = txt(e.innerText || (['submit','button'].includes(type) ? e.value : ''));
    if (t && t !== label) d.text = t;
    if (d.tag === 'select') d.options = [...e.options].map(o => txt(o.text)).slice(0, 80);
    if (['input','textarea'].includes(d.tag) && !['checkbox','radio','file','submit','button'].includes(type) && e.value)
      d.value = /pass/i.test(type) ? '(filled)' : txt(e.value);
    if (['checkbox','radio'].includes(type) || ['checkbox','radio','switch'].includes(role))
      d.checked = !!e.checked || e.getAttribute('aria-checked') === 'true';
    if (e.required || e.getAttribute('aria-required') === 'true') d.required = true;
    if (e.getAttribute('aria-invalid') === 'true') d.invalid = true;
    if (e.disabled || e.getAttribute('aria-disabled') === 'true') d.disabled = true;
    out.push(d);
    if (out.length >= 300) break;
  }
  return {els: out, next: n, text: (document.body ? document.body.innerText : '').replace(/\n\s*\n+/g, '\n').slice(0, 5000)};
}"""

PROMPT = """You are completing a job application in a real web browser on behalf of the candidate below.
You see the current page as visible text plus a numbered list of interactive elements. Return the actions
to take on THIS page, in order; you will then see the updated page.

Rules:
- Use CANDIDATE PROFILE and RESUME FACTS. Answer every question, required or not. If a question is not
  covered, give the most plausible answer consistent with the resume. Never invent employers, titles, dates,
  degrees or certifications. For work-history and education sections enter the entries from RESUME FACTS exactly.
- Sign in with LOGIN EMAIL. Wherever a password goes (including "confirm/verify password") write exactly
  {{PASSWORD}}. ACCOUNT tells you whether to sign in or create an account. If the site says the email is
  already registered or sign-in fails, use "Forgot password" and the email_code action to reset it to {{PASSWORD}}.
- Never use LinkedIn/Google/Indeed/social sign-in; choose "Apply manually" / "Apply with resume" style options.
  Autofill-from-resume is fine, but check and correct every field afterwards.
- Be fast: fill every field of a page in ONE step and click Next in the same step. For OPTIONAL multi-entry
  sections (skills, languages, websites, optional certifications) add at most 3 skills and skip the rest;
  add certifications only if the section is required. Required fields and work history/education always come first.
- Upload the resume with the upload action wherever a resume/CV is asked for.
- Accept privacy notices / terms needed to apply. Decline marketing opt-ins.
- Tick boxes and choose options exactly as labelled; for dropdowns use "select" for native <select> elements
  and "choose" for custom dropdowns, comboboxes and typeahead fields.
- After filling a page, click its Next / Continue / Save and Continue / Review / Submit button.
- SUBMIT_ALLOWED=__SUBMIT__. If false, stop at the final review page with status "ready_to_submit".
- If the page shows validation errors, fix them. If an action failed, try a different element.
- status "submitted" ONLY when the page confirms the application was received/submitted.
  status "blocked" if there is a CAPTCHA, the job is closed/unavailable, a page truly cannot be completed,
  or the site requires something you cannot provide. Give the reason.

Actions (JSON objects):
 {"do":"click","id":N}
 {"do":"fill","id":N,"value":"text"}            text inputs and textareas (replaces existing text)
 {"do":"select","id":N,"value":"option text"}   native <select>
 {"do":"choose","id":N,"value":"option text"}   custom dropdown / combobox / typeahead: opens or types, then picks the option
 {"do":"check","id":N}  {"do":"uncheck","id":N}
 {"do":"upload","id":N}                         file input or "upload/attach resume" button: attaches the resume
 {"do":"email_code"}                            waits for the site's verification email; result gives the code, or the link is opened
 {"do":"goto","url":"..."}
 {"do":"wait"}
Return JSON only: {"actions":[...], "status":"continue"|"submitted"|"ready_to_submit"|"blocked", "reason":"short"}"""


# ---------------------------------------------------------------- passwords (Windows Credential Manager)
def _service(host):
    return "jobagent:" + host


def has_password(host, user):
    import keyring
    return keyring.get_password(_service(host), user) is not None


def password_for(host, user):
    import keyring
    pw = keyring.get_password(_service(host), user)
    if not pw:
        core = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
        pw = "Ja" + core + "7!x"   # upper, lower, digit and symbol: meets typical portal rules
        keyring.set_password(_service(host), user, pw)
    return pw


# ---------------------------------------------------------------- verification emails (Gmail IMAP)
def wait_for_email(ecfg, since, timeout=180):
    """Return ("code", "123456") or ("link", url) from the newest email received after `since`."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            m = imaplib.IMAP4_SSL("imap.gmail.com")
            m.login(ecfg["gmail_user"], ecfg["gmail_app_password"])
            m.select("INBOX")
            _, ids = m.search(None, f'(SINCE "{since:%d-%b-%Y}")')
            for i in reversed(ids[0].split()[-15:]):
                _, data = m.fetch(i, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])
                when = email.utils.parsedate_to_datetime(msg["Date"])
                if when.timestamp() < since.timestamp() - 30:
                    continue
                subj = "".join(t.decode(c or "utf-8", "replace") if isinstance(t, bytes) else t
                               for t, c in decode_header(msg.get("Subject", "")))
                body = ""
                for part in msg.walk():
                    if part.get_content_type() in ("text/plain", "text/html"):
                        body += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                if not re.search(r"verif|confirm|code|activate|password|one.time|otp|passcode", subj + body[:3000], re.I):
                    continue
                link = re.search(r'https?://[^\s"\'<>]*(?:verif|confirm|activat|reset|password|token)[^\s"\'<>]*', body, re.I)
                code = re.search(r"(?<![\d#])(\d{6}|\d{4}|\d{8})(?!\d)", re.sub(r"<[^>]+>", " ", subj + " " + body))
                m.logout()
                if link:
                    return "link", link.group(0).replace("&amp;", "&")
                if code:
                    return "code", code.group(1)
            m.logout()
        except Exception:
            pass
        time.sleep(15)
    return None, None


# ---------------------------------------------------------------- browser session for one application
class Session:
    def __init__(self, context, job, resume, cfg, log):
        self.ctx, self.job, self.resume, self.cfg, self.log = context, job, resume, cfg, log
        self.counter, self.where = 1, {}
        self.user = cfg["email"]["gmail_user"]
        self.started = datetime.datetime.now(datetime.timezone.utc)

    @property
    def page(self):
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        return pages[-1]

    def host(self):
        return urlparse(self.page.url).hostname or ""

    def snapshot(self):
        page = self.page
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        els, texts = [], []
        for fr in page.frames:
            try:
                r = fr.evaluate(SNAPSHOT_JS, self.counter)
            except Exception:
                continue
            self.counter = max(self.counter, r["next"])
            for e in r["els"]:
                self.where[e["id"]] = fr
            els += r["els"]
            if r["text"].strip():
                texts.append(r["text"])
        return els, "\n".join(texts)[:6000]

    def loc(self, i):
        fr = self.where.get(int(i))
        if fr is None:
            raise ValueError(f"no element {i} on this page")
        return fr.locator(f'[data-ja="{int(i)}"]').first

    def _pw(self, v):
        if "{{PASSWORD}}" in v:
            v = v.replace("{{PASSWORD}}", password_for(self.host(), self.user))
        return v

    def act(self, a):
        do = a.get("do")
        if do == "wait":
            time.sleep(3); return "ok"
        if do == "goto":
            self.page.goto(a["url"], timeout=45000); return "ok"
        if do == "email_code":
            kind, val = wait_for_email(self.cfg["email"], self.started)
            self.started = datetime.datetime.now(datetime.timezone.utc)
            if kind == "link":
                self.page.goto(val, timeout=45000); return "opened verification link from email"
            if kind == "code":
                return f"verification code from email: {val}"
            return "no verification email arrived within 3 minutes"
        el = self.loc(a["id"])
        if do != "upload":  # file inputs are often hidden; scrolling to them times out
            try:
                el.scroll_into_view_if_needed(timeout=4000)
            except Exception:
                pass
        if do == "click":
            click(el)
        elif do == "fill":
            v = self._pw(str(a.get("value", "")))
            try:
                el.fill(v, timeout=8000)
            except Exception:
                click(el); el.press("Control+A"); el.press_sequentially(v, delay=20)
            if "{{PASSWORD}}" in str(a.get("value", "")):
                return "ok (password filled)"
        elif do == "select":
            v = str(a.get("value", ""))
            try:
                el.select_option(label=v, timeout=8000)
            except Exception:
                opts = el.evaluate("e => [...e.options].map(o => o.text)")
                best = (next((o for o in opts if norm(o) == norm(v)), None)
                        or next((o for o in opts if norm(v) in norm(o) or (norm(o) and norm(o) in norm(v))), None))
                if not best:
                    return f"no option like '{v}'; options: {opts[:40]}"
                el.select_option(label=best, timeout=8000)
        elif do == "choose":
            return self.choose(el, str(a.get("value", "")))
        elif do in ("check", "uncheck"):
            want = do == "check"
            try:
                el.set_checked(want, timeout=6000)
            except Exception:
                if el.evaluate("e => !!e.checked || e.getAttribute('aria-checked') === 'true'") != want:
                    click(el)
        elif do == "upload":
            if (el.get_attribute("type") or "").lower() == "file":
                el.set_input_files(self.resume, timeout=15000)
            else:
                with self.page.expect_file_chooser(timeout=10000) as fc:
                    click(el)
                fc.value.set_files(self.resume)
            time.sleep(4)
        else:
            return f"unknown action {do}"
        time.sleep(0.8)
        return "ok"

    def choose(self, el, v):
        """Custom dropdown / typeahead: open it (typing for search boxes), then click the best-matching option."""
        editable = el.evaluate("e => (e.tagName == 'INPUT' && !['checkbox','radio','button'].includes(e.type)) || e.isContentEditable")
        click(el)
        if editable:
            try:
                el.fill("", timeout=3000)
            except Exception:
                pass
            el.press_sequentially(v[:40], delay=40)
        time.sleep(1.5)
        frames = [self.where.get(int(el.get_attribute("data-ja") or 0)) or self.page.main_frame] + list(self.page.frames)
        for fr in frames:
            try:
                picked = fr.evaluate(OPTION_JS, v)
            except Exception:
                continue
            if picked:
                o = fr.locator('[data-ja-opt="1"]').first
                click(o)
                # Workday-style options carry their own radio/checkbox: make sure it is ticked
                try:
                    box = o.locator("input[type=radio], input[type=checkbox]").first
                    if box.count() and not box.is_checked():
                        click(box)
                except Exception:
                    pass
                time.sleep(1)
                return f"ok (picked '{picked}')"
        if editable:
            el.press("Enter"); time.sleep(1)
            return f"typed '{v}' and pressed Enter (no matching option was visible; check the field)"
        shown = [t.strip()[:50] for t in self.page.locator('[role=option]').all_inner_texts()][:40]
        return f"no option matching '{v}'. Visible options: {shown}"


def click(el):
    """Normal click; if an overlay intercepts it (common on Workday), force it, then fall back to a DOM click."""
    try:
        el.click(timeout=6000); return
    except Exception:
        pass
    try:
        el.click(timeout=4000, force=True); return
    except Exception:
        pass
    el.evaluate("e => e.click()")


def norm(t):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()).strip().lower()


OPTION_JS = r"""(v) => {
  const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
  const want = norm(v);
  const sels = '[role=option],[role=menuitem],[role=menuitemradio],[role=treeitem],[data-automation-id*=promptOption],li';
  let best = null, score = 0;
  for (const e of document.querySelectorAll(sels)) {
    const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const t = norm(e.innerText); if (!t || t.length > 200) continue;
    // exact > option starts with value > value starts with option ("Indian" -> "India") > whole-word match > substring
    const s = t === want ? 5 : t.startsWith(want) ? 4 : (want.startsWith(t) && t.length > 3) ? 3 :
              (' ' + t + ' ').includes(' ' + want + ' ') ? 2 : t.includes(want) ? 1 : 0;
    if (s > score || (s && s === score && best && best.contains(e))) { best = e; score = s; }
  }
  document.querySelectorAll('[data-ja-opt]').forEach(x => x.removeAttribute('data-ja-opt'));
  if (!best) return null;
  best.setAttribute('data-ja-opt', '1');
  return best.innerText.trim().slice(0, 80);
}"""


def resume_facts(master, variant):
    v = master[variant]
    return json.dumps({"contact": master["contact"], "education": master["education"],
                       "certifications": master["certifications"], "headline": v["headline"],
                       "summary": v["summary"], "jobs": v["jobs"]}, indent=0)[:9000]


def apply_one(ctx, job, resume, cfg, master, call, logf):
    s = Session(ctx, job, resume, cfg, logf)
    prof = dict(cfg.get("profile", {}))
    system = PROMPT.replace("__SUBMIT__", str(cfg["apply"].get("submit", True)).lower())
    history, t0 = [], time.time()
    s.page.goto(job["url"], timeout=60000)
    for step in range(MAX_STEPS):
        if time.time() - t0 > MAX_MINUTES * 60:
            return "blocked", f"took longer than {MAX_MINUTES} minutes"
        time.sleep(2)
        els, text = s.snapshot()
        host = s.host()
        acct = ("an account was created here before: sign in with the stored password"
                if has_password(host, s.user) else "no stored account for this site: create one if asked to sign in")
        user = (f"CANDIDATE PROFILE: {json.dumps(prof)}\nNAME: Dinesh Veluru (first: Dinesh, last: Veluru)\n"
                f"RESUME FACTS ({job['variant'].upper()} resume): {resume_facts(master, job['variant'])}\n"
                f"LOGIN EMAIL: {s.user}\nACCOUNT ({host}): {acct}\n"
                f"JOB: {job['title']} at {job['company']} ({job['location']})\nJD (start): {job['description'][:2500]}\n\n"
                f"HISTORY (latest last): {json.dumps(history[-12:])}\n\n"
                f"PAGE URL: {s.page.url}\nPAGE TEXT:\n{text}\n\nELEMENTS:\n" + "\n".join(json.dumps(e) for e in els))
        try:
            plan = call(system, user)
        except Exception as e:
            history.append({"step": step, "error": f"planner failed: {e}"[:200]}); continue
        logf.write(f"\n--- step {step} {s.page.url}\n{json.dumps(plan)[:3000]}\n")
        results = []
        for a in plan.get("actions", [])[:40]:
            try:
                r = s.act(a)
            except Exception as e:
                r = f"FAILED: {type(e).__name__}: {str(e).splitlines()[0][:160]}"
            results.append({**a, "result": r} if a.get("do") != "fill" or "{{PASSWORD}}" not in str(a.get("value"))
                           else {"do": "fill", "id": a.get("id"), "value": "{{PASSWORD}}", "result": r})
        history.append({"step": step, "status": plan.get("status"), "reason": plan.get("reason", ""), "actions": results})
        logf.write(json.dumps(results)[:3000] + "\n")
        st = plan.get("status", "continue")
        if st in ("submitted", "blocked", "ready_to_submit") and not any(r.get("do") == "click" for r in results[-1:]):
            return st, plan.get("reason", "")
        if st in ("submitted", "blocked", "ready_to_submit"):
            # the final click happened in this step: confirm on the next page before reporting
            time.sleep(4)
            _, text = s.snapshot()
            if st == "submitted" and not re.search(r"thank|submitted|received|success|applied|application (is )?complete", text, re.I):
                history.append({"note": "you reported submitted but the page shows no confirmation; check the page"})
                continue
            return st, plan.get("reason", "")
    return "blocked", "ran out of steps"


RANK_PROMPT = """You are a senior recruiter. Rate how apt the candidate below is for each role: how likely a hiring
manager would shortlist him. Weigh: seniority match (19+ years; titles like Senior/Principal/Staff PM, Senior/Lead TPM,
Delivery Lead fit; very junior or VP/Director-only roles fit poorly), domain (BFSI, lending, cards, payments, data
platforms, cloud migration, AI products are strong), required skills he truly lacks (e.g. deep hardware, consumer
growth, specific tech he never used), and location (Hyderabad best, then other Indian cities).
Be discriminating: spread scores across 0-100; only a few roles should score above 85.
Return JSON only: {"ratings":[{"id":"<id>","fit":0-100,"why":"<=15 words"}]}"""


def rank(store, jobs, master, call):
    """Have Claude rate unrated roles for aptness; results are kept in jobs.ai_fit / ai_reason."""
    todo = [j for j in jobs if j.get("ai_fit") is None]
    for i in range(0, len(todo), 20):
        chunk = todo[i:i + 20]
        cand = {"headline_pm": master["pm"]["headline"], "headline_tpm": master["tpm"]["headline"],
                "summary": master["pm"]["summary"], "competencies": master["pm"].get("competencies"),
                "history": [(x["title"], x["company"], x["dates"]) for x in master["pm"]["jobs"]],
                "certifications": master["certifications"], "base": "Hyderabad, India"}
        roles = [{"id": j["source_id"], "title": j["title"], "company": j["company"], "location": j["location"],
                  "jd": re.sub(r"\s+", " ", j["description"] or "")[:1500]} for j in chunk]
        try:
            res = call(RANK_PROMPT, f"CANDIDATE: {json.dumps(cand)}\n\nROLES: {json.dumps(roles)}")
        except Exception as e:
            print(f"  (ranking failed, falling back to keyword score: {e})"); return
        for r in res.get("ratings", []):
            store.db.execute("UPDATE jobs SET ai_fit=?, ai_reason=? WHERE source_id=?",
                             (int(r.get("fit", 0)), str(r.get("why", ""))[:200], str(r.get("id"))))
        store.db.commit()
        for j in chunk:
            row = store.db.execute("SELECT ai_fit, ai_reason FROM jobs WHERE source_id=?", (j["source_id"],)).fetchone()
            j["ai_fit"], j["ai_reason"] = row[0], row[1]


def pick_jobs(store, acfg, master=None, call=None):
    today = datetime.date.today()
    applied_today = store.db.execute("SELECT COUNT(*) FROM jobs WHERE applied_on = ?", (today.isoformat(),)).fetchone()[0]
    room = max(0, acfg.get("max_per_day", 10) - applied_today)
    since = (today - datetime.timedelta(days=acfg.get("company_window_days", 30))).isoformat()
    per_co = {r[0]: r[1] for r in store.db.execute(
        "SELECT company, COUNT(*) FROM jobs WHERE applied_on >= ? GROUP BY company", (since,))}
    cands = store.jobs("status IN ('new','ready') AND score >= ? AND COALESCE(apply_attempts,0) < ?",
                       (acfg.get("min_score", 75), MAX_ATTEMPTS))
    skip = {c.lower() for c in acfg.get("exclude_companies", [])}  # e.g. your current employer
    cands = [j for j in cands if j["company"].lower() not in skip]
    cands.sort(key=lambda j: (-j["score"], j["resume_file"] is None))
    if room and master and call:
        # Keyword score saturates at 100, so Claude rates every candidate once for aptness (cached in ai_fit).
        rank(store, cands, master, call)
        floor = acfg.get("min_ai_fit", 70)
        rated = [j for j in cands if j.get("ai_fit") is not None]
        if rated:
            cands = sorted((j for j in rated if j["ai_fit"] >= floor), key=lambda j: (-j["ai_fit"], -j["score"]))
    out = []
    for j in cands:
        if len(out) >= room:
            break
        if per_co.get(j["company"], 0) >= acfg.get("max_per_company", 3):
            continue
        if any(o["company"] == j["company"] and o["title"] == j["title"] for o in out):
            continue  # same posting listed under two requisition numbers
        per_co[j["company"]] = per_co.get(j["company"], 0) + 1
        out.append(j)
    return out


def run(cfg, store, only=None):
    from playwright.sync_api import sync_playwright
    from .tailor import tailor, write_docx, call_claude_code
    acfg = cfg.get("apply", {})
    if not acfg.get("enabled", False):
        print("Apply step is off ([apply] enabled = false)."); return
    master = json.load(open("resumes/master.json", encoding="utf-8"))
    jobs = [j for j in store.jobs() if j["source_id"] == only] if only else pick_jobs(store, acfg, master, call_claude_code)
    if not jobs:
        print("Nothing to apply to (daily or per-company limit reached, or no roles above the score)."); return
    print(f"Applying to {len(jobs)} roles...")
    for j in jobs:
        if j.get("ai_fit") is not None:
            print(f"  [{j['ai_fit']}] {j['company']}: {j['title']} - {j.get('ai_reason') or ''}")
    os.makedirs("output/apply_logs", exist_ok=True)
    with sync_playwright() as pw:
        kw = dict(headless=acfg.get("headless", False), accept_downloads=False, viewport={"width": 1366, "height": 900})
        if os.path.exists(CHROME):
            kw["executable_path"] = CHROME
        ctx = pw.chromium.launch_persistent_context(PROFILE_DIR, **kw)
        for j in jobs:
            safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{j['company']}_{j['title']}")[:80]
            resume = j["resume_file"]
            if not resume:  # tailor on the spot; fall back to the base resume
                try:
                    path = f"output/Dinesh_Veluru_{safe}.docx"
                    write_docx(tailor(j, master, j["variant"], cfg["anthropic"].get("api_key", ""),
                                      cfg["anthropic"].get("model", "claude-sonnet-5-5")), master, path)
                    store.set_resume(j["source_id"], path); resume = path
                except Exception as e:
                    resume = f"resumes/{master[j['variant']]['file']}.docx"
                    print(f"  (tailoring failed, using base resume: {e})")
            resume = os.path.abspath(resume)
            logpath = f"output/apply_logs/{datetime.date.today()}_{safe}"
            with open(logpath + ".txt", "w", encoding="utf-8", buffering=1) as logf:  # line-buffered: readable mid-run
                logf.write(f"{j['company']} | {j['title']} | {j['url']}\nresume: {resume}\n")
                try:
                    st, reason = apply_one(ctx, j, resume, cfg, master, call_claude_code, logf)
                except Exception as e:
                    st, reason = "blocked", f"{type(e).__name__}: {e}"[:200]
                    logf.write(traceback.format_exc())
                logf.write(f"\nRESULT: {st} {reason}\n")
            try:
                ctx.pages[-1].screenshot(path=logpath + ".png", full_page=True)
            except Exception:
                pass
            if st == "submitted":
                store.set_status(j["source_id"], "applied", f"auto-applied (log: {logpath}.txt)")
            else:
                store.db.execute("UPDATE jobs SET apply_attempts = COALESCE(apply_attempts,0) + 1 WHERE source_id=?",
                                 (j["source_id"],)); store.db.commit()
                store.set_status(j["source_id"], j["status"] if j["status"] != "new" else "ready",
                                 f"auto-apply {st}: {reason} (log: {logpath}.txt)")
            print(f"  [{st}] {j['company']}: {j['title']} {('- ' + reason) if reason and st != 'submitted' else ''}")
            for p in ctx.pages[1:]:  # keep one tab between applications
                try:
                    p.close()
                except Exception:
                    pass
        ctx.close()
