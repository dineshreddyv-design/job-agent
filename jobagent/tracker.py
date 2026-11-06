"""Local tracker page: http://localhost:8765 — filter roles and update application status."""
import json, html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from .store import STATUSES

PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Job Agent Tracker</title>
<style>
body{font:14px system-ui,Arial;margin:0;background:#f6f7f9;color:#1d2433}
header{background:#1F3864;color:#fff;padding:14px 22px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
header h1{font-size:18px;margin:0 12px 0 0}.pill{background:#ffffff22;border-radius:12px;padding:3px 10px;font-size:12px}
.bar{padding:12px 22px;display:flex;gap:10px;flex-wrap:wrap}input,select{padding:6px 8px;border:1px solid #c9ced8;border-radius:6px}
table{border-collapse:collapse;width:calc(100% - 44px);margin:0 22px 40px;background:#fff}
th,td{padding:8px;border-bottom:1px solid #e7e9ee;text-align:left;vertical-align:top}th{background:#eef1f6;position:sticky;top:0}
.score{font-weight:700}.hi{color:#0a7d3c}.mid{color:#a86b00}.lo{color:#999}
.miss{font-size:11px;color:#8a4b00}textarea{width:180px;height:34px;font:12px Arial}a{color:#1F3864}
details summary{cursor:pointer;color:#1F3864}details div{white-space:pre-wrap;max-height:300px;overflow:auto;font-size:12px;width:520px}
</style></head><body>
<header><h1>Job Agent Tracker</h1><span id=counts></span></header>
<div class=bar><input id=q placeholder="Search company / title" oninput=draw()>
<select id=st onchange=draw()><option value="">All statuses</option>__OPTS__</select>
<select id=va onchange=draw()><option value="">PM + TPM</option><option value=pm>PM</option><option value=tpm>TPM</option></select>
<select id=ms onchange=draw()><option value=0>Any fit</option><option value=60>Fit 60+</option><option value=75>Fit 75+</option></select>
<select id=lc onchange=draw()></select></div>
<table><thead><tr><th>Fit</th><th>Company</th><th>Role</th><th>Location</th><th>Found</th><th>Resume</th><th>Status</th><th>Notes</th></tr></thead><tbody id=tb></tbody></table>
<script>
const STATUSES=__STATUSES__;let JOBS=[];
// City filter: one entry per city, matching the spellings portals use
const CITIES={'Bengaluru':['bengaluru','bangalore'],'Hyderabad':['hyderabad'],'Pune':['pune'],'Chennai':['chennai'],
 'Mumbai':['mumbai'],'Delhi NCR':['gurugram','gurgaon','noida','delhi'],'Kolkata':['kolkata'],'Ahmedabad':['ahmedabad'],
 'Jaipur':['jaipur'],'Remote':['remote']};
function inCity(j,c){const l=(j.location||'').toLowerCase();
 if(c=='_other')return !Object.values(CITIES).flat().some(a=>l.includes(a));return CITIES[c].some(a=>l.includes(a))}
function cityOpts(){const el=document.getElementById('lc'),cur=el.value;
 el.innerHTML='<option value="">All locations</option>'+Object.keys(CITIES).concat('_other').map(c=>{
  const n=JOBS.filter(j=>inCity(j,c)).length;return n?`<option value="${c}">${c=='_other'?'Other / India unspecified':c} (${n})</option>`:''}).join('');
 el.value=cur}
async function load(){JOBS=await (await fetch('/api/jobs')).json();cityOpts();draw()}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function draw(){const q=document.getElementById('q').value.toLowerCase(),st=document.getElementById('st').value,
 va=document.getElementById('va').value,ms=+document.getElementById('ms').value,lc=document.getElementById('lc').value;
 const c={};JOBS.forEach(j=>c[j.status]=(c[j.status]||0)+1);
 document.getElementById('counts').innerHTML=Object.entries(c).map(([k,v])=>`<span class=pill>${k}: ${v}</span>`).join(' ');
 document.getElementById('tb').innerHTML=JOBS.filter(j=>(!q||(j.company+' '+j.title).toLowerCase().includes(q))&&(!st||j.status==st)&&(!va||j.variant==va)&&j.score>=ms&&(!lc||inCity(j,lc)))
 .map(j=>{const cls=j.score>=75?'hi':j.score>=60?'mid':'lo';const miss=JSON.parse(j.missing||'[]');
 return `<tr><td class="score ${cls}">${j.score}</td><td><b>${esc(j.company)}</b></td>
 <td><a href="${esc(j.url)}" target=_blank>${esc(j.title)}</a><details><summary>JD</summary><div>${esc(j.description)}</div></details>
 ${miss.length?`<div class=miss>Resume lacks: ${esc(miss.slice(0,8).join(', '))}</div>`:''}</td>
 <td>${esc(j.location)}</td><td>${(j.first_seen||'').slice(0,10)}</td>
 <td>${j.variant.toUpperCase()}${j.resume_file?`<br><a href="/file?p=${encodeURIComponent(j.resume_file)}">tailored</a>`:''}</td>
 <td><select onchange="upd('${j.source_id}',this.value)">${STATUSES.map(s=>`<option ${s==j.status?'selected':''}>${s}</option>`).join('')}</select>
 ${j.applied_on?`<div style="font-size:11px">applied ${j.applied_on}</div>`:''}</td>
 <td><textarea onchange="note('${j.source_id}',this.value)" placeholder="add note">${esc(j.notes)}</textarea></td></tr>`}).join('')}
async function upd(id,status){await fetch('/api/status',{method:'POST',body:JSON.stringify({id,status})});load()}
async function note(id,n){await fetch('/api/note',{method:'POST',body:JSON.stringify({id,note:n})})}
load();
</script></body></html>"""


def serve(store, port=8765):
    page = PAGE.replace("__OPTS__", "".join(f"<option>{s}</option>" for s in STATUSES)) \
               .replace("__STATUSES__", json.dumps(STATUSES))

    class H(BaseHTTPRequestHandler):
        def _send(self, body, ctype="application/json", code=200):
            b = body if isinstance(body, bytes) else body.encode()
            self.send_response(code); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self._send(page, "text/html; charset=utf-8")
            if u.path == "/api/jobs":
                return self._send(json.dumps(store.jobs()))
            if u.path == "/file":
                p = parse_qs(u.query).get("p", [""])[0]
                if p.startswith("output/") and ".." not in p:
                    with open(p, "rb") as f:
                        return self._send(f.read(), "application/octet-stream")
            self._send("not found", "text/plain", 404)

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
            if self.path == "/api/status":
                store.set_status(data["id"], data["status"])
            elif self.path == "/api/note":
                store.db.execute("UPDATE jobs SET notes=? WHERE source_id=?", (data["note"], data["id"]))
                store.db.commit()
            self._send('{"ok":true}')

        def log_message(self, *a):
            pass

    print(f"Tracker running at http://localhost:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
