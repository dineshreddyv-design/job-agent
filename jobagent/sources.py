"""Fetch open roles from company careers portals via their public job-board APIs.

Supported: Workday, Greenhouse, Lever, SmartRecruiters, Oracle Recruiting Cloud.
A company row can give a direct ATS URL, or just a careers page (ats=auto); in that
case we fetch the page once, look for a known ATS link, and cache what we find.
"""
import json, re, time, urllib.request, urllib.parse, html
from dataclasses import dataclass, field

UA = "Mozilla/5.0 (personal job-search agent; one request per company per run)"


@dataclass
class Job:
    company: str
    title: str
    location: str
    url: str
    source_id: str          # stable unique id, e.g. "workday:wellsfargo:R-12345"
    posted: str = ""
    description: str = ""   # plain text, filled lazily for shortlisted jobs
    extra: dict = field(default_factory=dict)


def _get(url, data=None, headers=None, timeout=25):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip().startswith(("{", "[")) else body


def strip_html(s):
    s = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", s or "", flags=re.I)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


# ---------------- ATS detection ----------------
PATTERNS = [
    ("amazon", re.compile(r"amazon\.jobs()")),
    ("workday", re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([\w-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)")),
    # Oracle Recruiting Cloud: usually *.oraclecloud.com, but some companies use their own domain (e.g. Dell)
    ("oracle", re.compile(r"https?://([\w.-]+)/hcmUI/CandidateExperience/[a-z]{2}/sites/([\w-]+)")),
]


def detect(url_or_html):
    for kind, rx in PATTERNS:
        m = rx.search(url_or_html)
        if m:
            return kind, m.groups()
    return None, None


def resolve(company):
    """Return (kind, params) for a company row; fetches the careers page if needed."""
    kind, groups = detect(company["careers_url"])
    if kind:
        return kind, groups
    page = _get(company["careers_url"], headers={"Accept": "text/html"})
    return detect(page if isinstance(page, str) else json.dumps(page))


# ---------------- Fetchers ----------------
def fetch_workday(company, tenant, wd, site, queries):
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    seen, out = set(), []
    for q in queries:
        for offset in range(0, 200, 20):
            data = _get(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": q})
            posts = data.get("jobPostings", [])
            for p in posts:
                path = p.get("externalPath", "")
                if path in seen:
                    continue
                seen.add(path)
                out.append(Job(company, p.get("title", ""), p.get("locationsText", ""), f"{base}/{site}{path}",
                               f"workday:{tenant}:{path.rsplit('_', 1)[-1]}", p.get("postedOn", ""),
                               extra={"detail_api": f"{api}{path}"}))
            if len(posts) < 20:
                break
            time.sleep(0.5)
    return out


def workday_detail(job):
    d = _get(job.extra["detail_api"]).get("jobPostingInfo", {})
    locs = [d.get("location", "")] + (d.get("additionalLocations") or [])
    job.location = "; ".join(l for l in locs if l) or job.location
    job.description = strip_html(d.get("jobDescription", ""))


def fetch_greenhouse(company, board, queries):
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    return [Job(company, j["title"], (j.get("location") or {}).get("name", ""), j["absolute_url"],
                f"greenhouse:{board}:{j['id']}", j.get("updated_at", "")[:10], strip_html(html.unescape(j.get("content", ""))))
            for j in data.get("jobs", [])]


def fetch_lever(company, slug, queries):
    data = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in data:
        desc = j.get("descriptionPlain", "") + "\n" + "\n".join(
            l.get("text", "") + "\n" + strip_html(l.get("content", "")) for l in j.get("lists", []))
        out.append(Job(company, j["text"], (j.get("categories") or {}).get("location", ""), j["hostedUrl"],
                       f"lever:{slug}:{j['id']}", time.strftime("%Y-%m-%d", time.gmtime(j.get("createdAt", 0) / 1000)), desc))
    return out


def fetch_smartrecruiters(company, cid, queries):
    out = []
    for q in queries:
        data = _get(f"https://api.smartrecruiters.com/v1/companies/{cid}/postings?country=in&limit=100&q={urllib.parse.quote(q)}")
        for j in data.get("content", []):
            loc = j.get("location", {})
            out.append(Job(company, j["name"], f"{loc.get('city', '')}, {loc.get('country', '')}",
                           f"https://jobs.smartrecruiters.com/{cid}/{j['id']}", f"smartrecruiters:{cid}:{j['id']}",
                           j.get("releasedDate", "")[:10], extra={"detail_api": j.get("ref", "")}))
    return out


def smartrecruiters_detail(job):
    d = _get(job.extra["detail_api"])
    secs = (d.get("jobAd") or {}).get("sections", {})
    job.description = "\n".join(strip_html((secs.get(k) or {}).get("text", ""))
                                for k in ("jobDescription", "qualifications", "additionalInformation"))


def fetch_oracle(company, host, site, queries):
    out = []
    for q in queries:
        finder = f'findReqs;siteNumber={site},keyword="{q}",limit=100,offset=0,sortBy=POSTING_DATES_DESC'
        url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
               f"&expand=requisitionList&finder={urllib.parse.quote(finder, safe='=;,')}")
        data = _get(url)
        for item in data.get("items", []):
            for r in item.get("requisitionList", []):
                out.append(Job(company, r.get("Title", ""), r.get("PrimaryLocation", ""),
                               f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}",
                               f"oracle:{host}:{r['Id']}", r.get("PostedDate", ""),
                               extra={"host": host, "site": site, "id": r["Id"]}))
    return out


def oracle_detail(job):
    x = job.extra
    finder = f'ById;Id="{x["id"]}",siteNumber={x["site"]}'
    d = _get(f"https://{x['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
             f"?expand=all&onlyData=true&finder={urllib.parse.quote(finder, safe='=;,')}")
    d = (d.get("items") or [{}])[0]
    locs = [job.location] + [l.get("Name", "") for l in d.get("secondaryLocations") or []]
    job.location = "; ".join(l for l in locs if l)
    job.description = "\n".join(strip_html(d.get(k) or "") for k in
                                ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")).strip()


def fetch_amazon(company, _, queries):
    out = []
    for q in queries:
        url = ("https://www.amazon.jobs/en/search.json?result_limit=100&sort=recent&country=IND&base_query="
               + urllib.parse.quote(q))
        for j in _get(url).get("jobs", []):
            out.append(Job(company, j.get("title", ""), j.get("normalized_location", "") or j.get("location", ""),
                           "https://www.amazon.jobs" + j.get("job_path", ""), f"amazon:{j.get('id_icims')}",
                           j.get("posted_date", ""), strip_html(j.get("description", "") + "\n" +
                                                                j.get("basic_qualifications", "") + "\n" +
                                                                j.get("preferred_qualifications", ""))))
    return out


FETCHERS = {
    "amazon": lambda c, g, q: fetch_amazon(c, g, q),
    "workday": lambda c, g, q: fetch_workday(c, g[0], g[1], g[2], q),
    "greenhouse": lambda c, g, q: fetch_greenhouse(c, g[0], q),
    "lever": lambda c, g, q: fetch_lever(c, g[0], q),
    "smartrecruiters": lambda c, g, q: fetch_smartrecruiters(c, g[0], q),
    "oracle": lambda c, g, q: fetch_oracle(c, g[0], g[1], q),
}


def fill_description(job):
    """Fetch the full JD for sources whose list API doesn't include it."""
    if job.description:
        return
    if job.source_id.startswith("workday:"):
        workday_detail(job)
    elif job.source_id.startswith("smartrecruiters:"):
        smartrecruiters_detail(job)
    elif job.source_id.startswith("oracle:"):
        oracle_detail(job)
