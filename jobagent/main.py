"""Command line: python -m jobagent <command>

  scan      check every company's careers portal, store new matching roles
  tailor    make tailored resumes for new roles at or above the fit threshold
  digest    send the daily email + WhatsApp summary
  daily     scan + tailor + digest (what the scheduler runs)
  tracker   open the status tracker at http://localhost:8765
  status    update a role from the terminal: status <source_id> <status> [note]
  health    list companies whose portal could not be read (fix their URL in companies.csv)
"""
import csv, json, sys, time, tomllib, traceback, re
from concurrent.futures import ThreadPoolExecutor
from . import sources, match, notify, tracker
from .store import Store, STATUSES


def load_cfg():
    with open("config.toml", "rb") as f:
        return tomllib.load(f)


def resume_texts():
    m = json.load(open("resumes/master.json"))
    return m, {v: json.dumps(m[v]) + json.dumps(m["certifications"]) for v in ("pm", "tpm")}


def scan_company(row, store, queries):
    name = row["company"].strip()
    try:
        # A direct job-board link in companies.csv always wins, so edits to the CSV take effect.
        # The cache only saves re-fetching careers pages for auto-detect rows.
        kind, params = sources.detect(row["careers_url"])
        if not kind:
            kind, params = store.cached_source(name)
        if not kind:
            kind, params = sources.resolve(row)
        if not kind:
            store.health(name, None, [], False, "no supported job board found on careers page")
            return name, []
        jobs = sources.FETCHERS[kind](name, params, queries)
        store.health(name, kind, list(params), True, "", len(jobs))
        return name, jobs
    except Exception as e:
        store.health(name, None, [], False, f"{type(e).__name__}: {e}"[:300])
        return name, []


def scan(cfg, store):
    master, rtext = resume_texts()
    queries = cfg["search"]["queries"]
    rows = [r for r in csv.DictReader(open("companies.csv", encoding="utf-8")) if r.get("careers_url", "").strip()]
    print(f"Scanning {len(rows)} companies...")
    with ThreadPoolExecutor(max_workers=cfg["search"].get("parallel", 6)) as ex:
        results = list(ex.map(lambda r: scan_company(r, store, queries), rows))
    added = 0
    for name, jobs in results:
        for j in jobs:
            if store.exists(j.source_id) or not match.title_ok(j.title):
                continue
            loc = match.location_ok(j.location)
            if loc is False:
                continue
            try:
                sources.fill_description(j)
                time.sleep(0.3)
            except Exception:
                pass
            if loc is None and match.location_ok(j.location) is not True and \
                    not any(c in j.description.lower() for c in ("india", "hyderabad", "bengaluru", "bangalore", "pune", "chennai", "gurugram")):
                continue
            v = match.variant(j.title)
            s, missing = match.score(j, rtext[v])
            store.add(j, v, s, missing)
            added += 1
            print(f"  + [{s}] {name}: {j.title} ({j.location})")
    print(f"Done. {added} new matching roles.")
    return added


def tailor_new(cfg, store):
    from .tailor import tailor, write_docx
    key = cfg.get("anthropic", {}).get("api_key", "")  # blank = use Claude Code on your subscription (no cost)
    master, _ = resume_texts()
    th = cfg["search"].get("tailor_min_score", 60)
    cap = cfg["search"].get("tailor_max_per_day", 15)  # keeps you inside your plan's usage limits
    todo = sorted(store.jobs("status='new' AND score >= ? AND resume_file IS NULL", (th,)), key=lambda j: -j["score"])
    for j in todo[:cap]:  # best fit first
        safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{j['company']}_{j['title']}")[:80]
        path = f"output/Dinesh_Veluru_{safe}.docx"
        try:
            t = tailor(j, master, j["variant"], key, cfg["anthropic"].get("model", "claude-sonnet-5-5"))
            write_docx(t, master, path)
            store.set_resume(j["source_id"], path)
            gaps = t.get("jd_keywords_not_supported") or []
            store.set_status(j["source_id"], "ready", ("Gaps vs JD: " + ", ".join(gaps)) if gaps else "")
            print(f"  tailored: {path}")
        except Exception as e:
            print(f"  could not tailor {j['company']} / {j['title']}: {e}")


def digest(cfg, store):
    subject, body, wa = notify.build_digest(store, cfg.get("tracker_url", "http://localhost:8765"))
    if cfg.get("email", {}).get("gmail_app_password"):
        notify.send_email(cfg["email"], subject, body); print("Email sent.")
    wcfg = cfg.get("whatsapp", {})
    has_key = wcfg.get("twilio_sid") if wcfg.get("provider") == "twilio" else wcfg.get("callmebot_apikey")
    if wcfg.get("phone") and has_key:
        notify.send_whatsapp(wcfg, wa); print("WhatsApp sent.")
    else:
        print("WhatsApp skipped (no API key in config.toml).")
    open("output/latest_digest.html", "w", encoding="utf-8").write(body)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "help":
        print(__doc__); return
    cfg = load_cfg()
    store = Store(cfg.get("db", "jobs.db"))
    import os; os.makedirs("output", exist_ok=True)
    if cmd == "scan":
        scan(cfg, store)
    elif cmd == "tailor":
        tailor_new(cfg, store)
    elif cmd == "digest":
        digest(cfg, store)
    elif cmd == "daily":
        for step in (scan, tailor_new, digest):
            try:
                step(cfg, store)
            except Exception:
                traceback.print_exc()
    elif cmd == "tracker":
        tracker.serve(store, cfg.get("tracker_port", 8765))
    elif cmd == "status":
        sid, st = sys.argv[2], sys.argv[3]
        if st not in STATUSES:
            sys.exit(f"status must be one of: {', '.join(STATUSES)}")
        store.set_status(sid, st, " ".join(sys.argv[4:]))
    elif cmd == "health":
        for r in store.db.execute("SELECT * FROM source_health ORDER BY last_error DESC, company"):
            flag = "OK " if not r["last_error"] else "ERR"
            print(f"{flag} {r['company']:<35} {r['kind'] or '-':<15} jobs={r['jobs_found'] or 0:<4} {r['last_error'] or ''}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
