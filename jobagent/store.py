"""SQLite tracker for every role the agent has seen and where each application stands."""
import sqlite3, json, datetime

STATUSES = ["new", "ready", "applied", "screening", "interview", "final round", "offer",
            "rejected", "no response", "skipped", "closed"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  source_id TEXT PRIMARY KEY, company TEXT, title TEXT, location TEXT, url TEXT, posted TEXT,
  description TEXT, variant TEXT, score INTEGER, missing TEXT, status TEXT DEFAULT 'new',
  first_seen TEXT, status_changed TEXT, applied_on TEXT, resume_file TEXT, notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS history (source_id TEXT, status TEXT, at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS source_health (company TEXT PRIMARY KEY, kind TEXT, params TEXT,
  last_ok TEXT, last_error TEXT, jobs_found INTEGER);
CREATE TABLE IF NOT EXISTS accounts (portal TEXT PRIMARY KEY, company TEXT, login_email TEXT,
  created_on TEXT, vault_item TEXT);
"""


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path="jobs.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def exists(self, source_id):
        return self.db.execute("SELECT 1 FROM jobs WHERE source_id=?", (source_id,)).fetchone() is not None

    def add(self, job, variant, score, missing):
        self.db.execute(
            "INSERT OR IGNORE INTO jobs (source_id,company,title,location,url,posted,description,variant,score,missing,"
            "status,first_seen,status_changed) VALUES (?,?,?,?,?,?,?,?,?,?, 'new', ?, ?)",
            (job.source_id, job.company, job.title, job.location, job.url, job.posted, job.description,
             variant, score, json.dumps(missing), now(), now()))
        self.db.commit()

    def set_status(self, source_id, status, note=""):
        assert status in STATUSES, status
        extra = ", applied_on=COALESCE(applied_on, ?)" if status == "applied" else ""
        args = [status, now()] + ([now()[:10]] if extra else []) + [source_id]
        self.db.execute(f"UPDATE jobs SET status=?, status_changed=?{extra} WHERE source_id=?", args)
        self.db.execute("INSERT INTO history VALUES (?,?,?,?)", (source_id, status, now(), note))
        if note:
            self.db.execute("UPDATE jobs SET notes = TRIM(notes || char(10) || ?) WHERE source_id=?",
                            (f"{now()[:10]}: {note}", source_id))
        self.db.commit()

    def set_resume(self, source_id, path):
        self.db.execute("UPDATE jobs SET resume_file=? WHERE source_id=?", (path, source_id))
        self.db.commit()

    def jobs(self, where="1=1", args=()):
        return [dict(r) for r in self.db.execute(
            f"SELECT * FROM jobs WHERE {where} ORDER BY first_seen DESC, score DESC", args)]

    def health(self, company, kind, params, ok, error="", found=0):
        self.db.execute(
            "INSERT INTO source_health VALUES (?,?,?,?,?,?) ON CONFLICT(company) DO UPDATE SET kind=excluded.kind, "
            "params=excluded.params, last_ok=COALESCE(excluded.last_ok, last_ok), last_error=excluded.last_error, "
            "jobs_found=excluded.jobs_found",
            (company, kind, json.dumps(params), now() if ok else None, error, found))
        self.db.commit()

    def cached_source(self, company):
        r = self.db.execute("SELECT kind, params FROM source_health WHERE company=? AND kind IS NOT NULL",
                            (company,)).fetchone()
        return (r["kind"], tuple(json.loads(r["params"]))) if r else (None, None)

    # Accounts: which portals already have a login. Passwords stay in your password manager.
    def account(self, portal):
        r = self.db.execute("SELECT * FROM accounts WHERE portal=?", (portal,)).fetchone()
        return dict(r) if r else None

    def add_account(self, portal, company, email, vault_item):
        self.db.execute("INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?)",
                        (portal, company, email, now()[:10], vault_item))
        self.db.commit()
