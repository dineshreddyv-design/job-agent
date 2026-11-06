"""Tailor the PM or TPM resume to one job description using Claude, then write an ATS-safe DOCX.

Truthfulness guard: Claude may only reorder, trim and reword facts from resumes/master.json.
Any number in the output that does not appear in the master is rejected and the job keeps the base resume.
"""
import json, re, os, urllib.request

MODEL = "claude-sonnet-5-5"  # change in config.toml ([anthropic] model) to any current model
PROMPT = """You tailor a resume to a job description. Hard rules:
1. Use ONLY facts in MASTER. Never add employers, titles, dates, tools, certifications, metrics or responsibilities that MASTER does not state.
2. You may reorder bullets, drop weak ones, shorten them, and swap in the JD's wording where it means the same thing as MASTER (e.g. "stakeholder alignment" for "partnering with stakeholders").
3. Keep job titles, companies and dates exactly as in MASTER.
4. Write a 3-4 sentence summary aimed at this JD, and a headline of the form "<Role family> | <2-3 strengths that match the JD> | BFSI".
5. Order competency lines so the JD's priorities come first; add JD terms to a competency line only if MASTER shows that skill.
Return JSON only: {"headline": str, "summary": str, "impact": [4 str], "competencies": [[label, text], ...],
"jobs": [{"title","company","dates","bullets":[str]}], "jd_keywords_not_supported": [str]}
The last field lists important JD requirements MASTER cannot honestly support, so the candidate knows the gaps."""


def call_claude(api_key, model, system, user):
    body = {"model": model, "max_tokens": 4000, "system": system, "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    text = "".join(b.get("text", "") for b in out["content"])
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def call_claude_code(system, user):
    """No-cost path: uses the Claude Code CLI logged in with your Claude subscription."""
    import subprocess, shutil
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code CLI not found. Install it, run `claude` once to log in, then retry.")
    r = subprocess.run([exe, "-p"], input=system + "\n\n" + user, capture_output=True, text=True,
                       timeout=300, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300] or "claude -p failed")
    text = r.stdout
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def numbers(s):
    return set(re.findall(r"\d[\d,.]*\+?%?", s))


def unsupported_numbers(tailored, master_variant, master_all):
    allowed = numbers(json.dumps(master_all))
    return sorted(numbers(json.dumps({k: v for k, v in tailored.items() if k != "jd_keywords_not_supported"})) - allowed)


def tailor(job, master, variant, api_key, model=MODEL):
    base = master[variant]
    user = (f"MASTER (base {variant.upper()} resume):\n{json.dumps(base, indent=1)}\n\n"
            f"Shared facts: {json.dumps({k: master[k] for k in ('contact', 'education', 'certifications')})}\n\n"
            f"JOB: {job['title']} at {job['company']} ({job['location']})\n\nJOB DESCRIPTION:\n{job['description'][:12000]}")
    t = call_claude(api_key, model, PROMPT, user) if api_key else call_claude_code(PROMPT, user)
    bad = unsupported_numbers(t, base, master)
    if bad:
        raise ValueError(f"tailored resume introduced numbers not in master: {bad}")
    return t


def write_docx(t, master, path):
    from docx import Document
    from docx.shared import Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    navy = RGBColor(0x1F, 0x38, 0x64)
    d = Document()
    for s in d.sections:
        s.page_width, s.page_height = Cm(21), Cm(29.7)
        s.top_margin = s.bottom_margin = Cm(1.2); s.left_margin = s.right_margin = Cm(1.3)
    st = d.styles["Normal"]; st.font.name = "Calibri"; st.font.size = Pt(10)
    st.paragraph_format.space_after = Pt(2)

    def para(text="", bold=False, size=10, color=None, align=None, italic=False):
        p = d.add_paragraph(); r = p.add_run(text); r.bold, r.italic, r.font.size = bold, italic, Pt(size)
        if color: r.font.color.rgb = color
        if align is not None: p.alignment = align
        return p

    def head(text):
        p = para(text.upper(), True, 11, navy); p.paragraph_format.space_before = Pt(8)

    def bullet(text):
        p = d.add_paragraph(text, style="List Bullet"); p.paragraph_format.space_after = Pt(1)

    para("DINESH VELURU", True, 18, navy, WD_ALIGN_PARAGRAPH.CENTER)
    para(t["headline"], True, 10.5, None, WD_ALIGN_PARAGRAPH.CENTER)
    para(master["contact"], False, 9.5, None, WD_ALIGN_PARAGRAPH.CENTER)
    head("Professional Summary"); para(t["summary"])
    head("Key Achievements"); [bullet(x) for x in t["impact"]]
    head("Core Competencies")
    for label, text in t["competencies"]:
        p = d.add_paragraph(); r = p.add_run(label + ": "); r.bold = True; p.add_run(text)
    head("Professional Experience")
    for j in t["jobs"]:
        p = d.add_paragraph(); p.paragraph_format.space_before = Pt(5)
        p.paragraph_format.tab_stops.add_tab_stop(Cm(18.4), WD_TAB_ALIGNMENT.RIGHT)
        r = p.add_run(j["title"]); r.bold = True; r2 = p.add_run("\t" + j["dates"]); r2.bold = True
        para(j["company"], italic=True)
        [bullet(b) for b in j["bullets"]]
    head("Certifications"); [bullet(c) for c in master["certifications"]]
    head("Education"); para(master["education"]["degree"], True); para(master["education"]["school"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    d.save(path)
    return path
