"""Decide whether a role fits, which resume to send, and how well the resume covers the JD."""
import re

TITLE_INCLUDE = [
    r"\bsenior product manager\b", r"\bgroup product manager\b", r"\blead product manager\b",
    r"\bprincipal product manager\b", r"\bstaff product manager\b", r"\bproduct manager\b",
    r"\bproduct owner\b", r"\bproduct lead\b", r"\bhead of product\b", r"\bproduct management\b",
    r"\btechnical program manager\b", r"\btpm\b", r"\bprogram manager\b",
    r"\bsenior project manager\b", r"\bproject manager\b", r"\bdelivery manager\b", r"\bdelivery lead\b",
]
TITLE_EXCLUDE = [r"\bassociate product\b", r"\bapm\b", r"\bintern\b", r"\btrainee\b", r"\bjunior\b",
                 r"\bproduct marketing\b", r"\banalyst\b", r"\bsales\b", r"\bconstruction\b", r"\bclinical\b"]

INDIA = ["india", "hyderabad", "bengaluru", "bangalore", "pune", "chennai", "mumbai", "gurgaon", "gurugram",
         "noida", "delhi", "kolkata", "ahmedabad", "kochi", "coimbatore", "trivandrum", "thiruvananthapuram",
         "jaipur", "indore", "mysore", "mysuru", "vadodara", "chandigarh", "telangana", "karnataka", "maharashtra",
         "tamil nadu", "haryana", "uttar pradesh", "remote - in", ", in"]

TPM_WORDS = ["technical program", "tpm", "program manager", "project manager", "delivery manager", "delivery lead"]

# Words the agent looks for in a JD and the resumes; drives fit score and the "missing keywords" hint.
KEYWORDS = [
    "product strategy", "roadmap", "product vision", "product discovery", "backlog", "prioritization", "okr", "kpi",
    "stakeholder", "go-to-market", "user research", "customer journey", "a/b test", "experimentation", "analytics",
    "agile", "scrum", "safe", "jira", "release", "ci/cd", "devops", "sdlc", "program management", "dependencies",
    "risk", "governance", "roi", "budget", "vendor", "cross-functional",
    "ai", "genai", "generative ai", "llm", "machine learning", "agentic", "data platform", "data lake",
    "data governance", "data quality", "cloud", "aws", "azure", "gcp", "migration", "modernization", "api",
    "microservices", "platform", "payments", "cards", "lending", "mortgage", "banking", "fintech", "bfsi",
    "deposits", "credit", "insurance", "compliance", "regulatory", "fraud",
]


def title_ok(title):
    t = title.lower()
    return any(re.search(p, t) for p in TITLE_INCLUDE) and not any(re.search(p, t) for p in TITLE_EXCLUDE)


def location_ok(location, description=""):
    loc = (location or "").lower()
    if any(c in loc for c in INDIA):
        return True
    # Workday shows "3 Locations"; decide after the JD is fetched
    if re.match(r"^\d+ locations?$", loc.strip()) or not loc.strip():
        return None
    return False


def variant(title):
    t = title.lower()
    return "tpm" if any(w in t for w in TPM_WORDS) else "pm"


def years_required(jd):
    m = re.findall(r"(\d{1,2})\s*\+?\s*(?:-|to)?\s*\d{0,2}\s*\+?\s*years", jd.lower())
    nums = [int(x) for x in m if 0 < int(x) < 30]
    return max(nums) if nums else None


def _has(text, kw):
    return re.search(r"(?<![a-z])" + re.escape(kw) + r"s?(?![a-z])", text) is not None


def score(job, resume_text):
    """0-100 fit score plus the JD keywords the chosen resume does not mention."""
    jd = (job.title + "\n" + job.description).lower()
    rs = resume_text.lower()
    jd_kws = [k for k in KEYWORDS if _has(jd, k)]
    covered = [k for k in jd_kws if _has(rs, k)]
    missing = [k for k in jd_kws if k not in covered]
    s = 40 + (40 * len(covered) / len(jd_kws) if jd_kws else 20)
    t = job.title.lower()
    if re.search(r"senior|lead|principal|group|staff|head|manager ii|iii", t):
        s += 10
    yrs = years_required(job.description)
    if yrs and yrs <= 20:
        s += 10
    elif yrs and yrs > 20:
        s -= 10
    return round(min(s, 100)), missing
