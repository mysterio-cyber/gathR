Python 3.13.3 (v3.13.3:6280bb54784, Apr  8 2025, 10:47:54) [Clang 15.0.0 (clang-1500.3.9.4)] on darwin
Enter "help" below or click "Help" above for more information.
"""
gathR v2 — Corporate Professional Network
Adds: Search, Notifications, Real-time Comments, Analytics Dashboard,
      Job Board with Apply, AI Chat Assistant, Direct Messaging
"""

import os, io, re, json, uuid, unicodedata
from datetime import datetime
from functools import wraps
import PyPDF2
from flask import (Flask, request, jsonify, render_template_string,
                   session, redirect, url_for, g)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
from anthropic import Anthropic
from dotenv import load_dotenv
load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

app = Flask(__name__)
app.secret_key = "gathR-super-secret-2025"
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
app.config["JSON_AS_ASCII"] = False

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DATABASE = "gathr.db"

if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
ai_client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ═══════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_database", None)
    if db: db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            headline TEXT DEFAULT '',
            location TEXT DEFAULT '',
            about TEXT DEFAULT '',
            skills TEXT DEFAULT '[]',
            avatar TEXT DEFAULT '',
            resume_text TEXT DEFAULT '',
            resume_analysis TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            file_url TEXT DEFAULT '',
            file_type TEXT DEFAULT '',
            file_name TEXT DEFAULT '',
            likes TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(post_id) REFERENCES posts(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            message TEXT NOT NULL,
            link TEXT DEFAULT '',
            read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER NOT NULL,
            to_user INTEGER NOT NULL,
            content TEXT NOT NULL,
            read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(from_user) REFERENCES users(id),
            FOREIGN KEY(to_user) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS job_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            job_title TEXT NOT NULL,
            company TEXT NOT NULL,
            status TEXT DEFAULT 'applied',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS ai_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)
        db.commit()

init_db()

# ═══════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)
    return decorated

def current_user():
    if "user_id" not in session: return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

def sanitize(text):
    if not text: return ""
    text = unicodedata.normalize("NFKD", text)
    for bad, good in [("\u2013","-"),("\u2014","-"),("\u2018","'"),("\u2019","'"),
                      ("\u201c",'"'),("\u201d",'"'),("\u2022","*"),("\u00a0"," "),
                      ("\u2026","..."),("\u200b",""),("\ufeff","")]:
        text = text.replace(bad, good)
    cleaned = "".join(c if (32 <= ord(c) < 127 or c in "\n\r\t") else " " for c in text)
    return re.sub(r"[ \t]+", " ", cleaned).strip()

def extract_pdf(file_bytes):
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text: pages.append(text)
        raw = "\n".join(pages)
        raw = unicodedata.normalize("NFKD", raw)
        replacements = [
            ("\u2013","-"),("\u2014","-"),("\u2018","'"),("\u2019","'"),
            ("\u201c",'"'),("\u201d",'"'),("\u2022","*"),("\u00a0"," "),
            ("\u2026","..."),("\u200b",""),("\ufeff",""),("\u00b7","*"),
        ]
        for bad, good in replacements:
            raw = raw.replace(bad, good)
        cleaned = "".join(c if (32 <= ord(c) < 127 or c in "\n\r\t") else " " for c in raw)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned
    except: return ""

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in {
        "pdf","txt","png","jpg","jpeg","gif","doc","docx"
    }

def row_to_dict(row):
    if row is None: return None
    return dict(row)

def add_notification(user_id, ntype, message, link=""):
    db = get_db()
    db.execute("INSERT INTO notifications (user_id,type,message,link) VALUES (?,?,?,?)",
               (user_id, ntype, message, link))
    db.commit()

JOBS_DB = [
    {"id":1,"title":"Python Backend Engineer","company":"TechFlow","location":"Remote","type":"Full-time","salary":"$80-110k","skills":["python","flask","django","rest api","sql","docker"],"description":"Build scalable backend services with Python. Experience with Flask/Django required."},
    {"id":2,"title":"Frontend Developer","company":"Pixel Studio","location":"Hyderabad","type":"Full-time","salary":"₹10-18 LPA","skills":["html","css","javascript","react","typescript","vue"],"description":"Create beautiful, responsive UIs. Strong React and TypeScript skills needed."},
    {"id":3,"title":"AI/ML Engineer","company":"NeuralPath","location":"Bangalore","type":"Full-time","salary":"₹20-35 LPA","skills":["python","machine learning","deep learning","pytorch","nlp","tensorflow"],"description":"Design and implement ML models for production. LLM experience is a plus."},
    {"id":4,"title":"Full Stack Developer","company":"Buildify","location":"Remote","type":"Contract","salary":"$70-90/hr","skills":["javascript","node.js","react","mongodb","docker","aws"],"description":"End-to-end feature development across our SaaS platform."},
    {"id":5,"title":"Data Analyst","company":"Insightful Inc","location":"Mumbai","type":"Full-time","salary":"₹8-14 LPA","skills":["python","sql","tableau","excel","statistics","power bi"],"description":"Turn data into actionable business insights. Tableau proficiency required."},
    {"id":6,"title":"DevOps Engineer","company":"CloudBase","location":"Remote","type":"Full-time","salary":"$90-130k","skills":["docker","kubernetes","aws","ci/cd","linux","terraform"],"description":"Own our cloud infrastructure and CI/CD pipelines."},
    {"id":7,"title":"Product Manager","company":"LaunchPad","location":"Hyderabad","type":"Full-time","salary":"₹22-40 LPA","skills":["product strategy","agile","roadmapping","analytics","stakeholder management"],"description":"Lead cross-functional teams to ship impactful products."},
    {"id":8,"title":"Cybersecurity Analyst","company":"ShieldNet","location":"Remote","type":"Full-time","salary":"$85-115k","skills":["network security","penetration testing","siem","linux","python"],"description":"Protect our systems and respond to security incidents."},
    {"id":9,"title":"React Native Developer","company":"AppForge","location":"Remote","type":"Full-time","salary":"₹14-24 LPA","skills":["react native","javascript","ios","android","redux","typescript"],"description":"Build and maintain our cross-platform mobile apps."},
    {"id":10,"title":"Cloud Architect","company":"SkyScale","location":"Remote","type":"Full-time","salary":"$130-165k","skills":["aws","azure","gcp","terraform","microservices","kubernetes"],"description":"Design cloud-native architectures for enterprise clients."},
    {"id":11,"title":"UX Designer","company":"Designify","location":"Bangalore","type":"Full-time","salary":"₹12-22 LPA","skills":["figma","user research","prototyping","design systems","accessibility"],"description":"Craft intuitive user experiences from research to high-fidelity prototypes."},
    {"id":12,"title":"Data Engineer","company":"DataPipe","location":"Remote","type":"Full-time","salary":"$95-125k","skills":["python","apache spark","kafka","airflow","sql","aws"],"description":"Build and maintain robust data pipelines at scale."},
]

# ═══════════════════════════════════════════
#  HTML TEMPLATE
# ═══════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>gathR — Professional Network</title>
<link href="https://fonts.googleapis.com/css2?family=Cabinet+Grotesk:wght@400;500;700;800;900&family=Instrument+Serif:ital@0;1&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --ink:#0a0c0f;--ink2:#12161c;--ink3:#1a2030;--line:#232b38;--line2:#2e3a4a;
  --sky:#1a6cff;--sky2:#0d4fd4;--violet:#7c3aed;--mint:#00c48c;--amber:#f59e0b;--rose:#f43f5e;
  --text:#e4ebf5;--muted:#5a6b80;--dim:#3a4a5c;--card:#0e1420;
  --r:14px;--r2:20px;--shadow:0 4px 24px rgba(0,0,0,.5);
}
html{scroll-behavior:smooth}
body{font-family:'Cabinet Grotesk',sans-serif;background:var(--ink);color:var(--text);min-height:100vh;overflow-x:hidden}
a{color:inherit;text-decoration:none}
button,input,textarea,select{font-family:inherit}
button{cursor:pointer}
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--line2);border-radius:4px}

/* AUTH */
.auth-page{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;
  background:radial-gradient(ellipse 80% 60% at 20% 10%,rgba(26,108,255,.12) 0%,transparent 70%),
             radial-gradient(ellipse 60% 50% at 85% 90%,rgba(124,58,237,.1) 0%,transparent 65%),var(--ink);position:relative}
.auth-wordmark{position:absolute;top:28px;left:36px;font-weight:900;font-size:1.5rem;letter-spacing:-.04em}
.auth-wordmark span{color:var(--sky)}
.auth-card{background:var(--card);border:1px solid var(--line);border-radius:24px;padding:44px 40px;width:100%;max-width:430px;box-shadow:var(--shadow);position:relative;z-index:1}
.auth-badge{display:inline-flex;align-items:center;gap:6px;background:rgba(26,108,255,.1);border:1px solid rgba(26,108,255,.2);border-radius:999px;padding:5px 12px;font-size:.72rem;font-weight:700;color:var(--sky);text-transform:uppercase;letter-spacing:.06em;margin-bottom:18px}
.auth-badge::before{content:'';width:6px;height:6px;background:var(--sky);border-radius:50%}
.auth-h1{font-size:2rem;font-weight:900;letter-spacing:-.05em;line-height:1.1;margin-bottom:6px}
.auth-h1 em{font-family:'Instrument Serif',serif;font-style:italic;color:var(--sky);font-weight:400}
.auth-sub{color:var(--muted);font-size:.85rem;margin-bottom:28px;line-height:1.5}
.auth-tabs{display:flex;gap:3px;background:var(--ink3);border-radius:10px;padding:3px;margin-bottom:26px}
.auth-tab{flex:1;padding:9px;border:none;background:transparent;border-radius:8px;color:var(--muted);font-size:.84rem;font-weight:700;transition:all .2s}
.auth-tab.active{background:var(--ink2);color:var(--text);box-shadow:0 1px 4px rgba(0,0,0,.4)}
.field{margin-bottom:14px}
.field label{display:block;font-size:.7rem;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}
.field input{width:100%;background:var(--ink3);border:1px solid var(--line);border-radius:10px;padding:11px 14px;color:var(--text);font-size:.9rem;outline:none;transition:all .2s}
.field input:focus{border-color:var(--sky);box-shadow:0 0 0 3px rgba(26,108,255,.1)}
.auth-btn{width:100%;padding:13px;background:var(--sky);border:none;border-radius:11px;color:#fff;font-size:.95rem;font-weight:800;transition:all .2s}
.auth-btn:hover{background:var(--sky2);transform:translateY(-1px)}
.auth-err{background:rgba(244,63,94,.08);border:1px solid rgba(244,63,94,.2);color:#fb7185;padding:10px 14px;border-radius:8px;font-size:.83rem;margin-bottom:14px;display:none}
.auth-err.show{display:block}

/* APP SHELL */
.app{display:none;min-height:100vh}
.app.show{display:block}

/* TOPBAR */
.topbar{position:sticky;top:0;z-index:100;background:rgba(10,12,15,.92);backdrop-filter:blur(20px);border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 16px;height:56px;gap:10px}
.topbar-logo{font-weight:900;font-size:1.35rem;letter-spacing:-.05em;flex-shrink:0}
.topbar-logo span{color:var(--sky)}
.topbar-search{flex:1;max-width:280px;position:relative}
.topbar-search input{width:100%;background:var(--ink3);border:1px solid var(--line);border-radius:8px;padding:7px 12px 7px 34px;color:var(--text);font-size:.82rem;outline:none;transition:all .2s}
.topbar-search input:focus{border-color:var(--sky)}
.topbar-search .si{position:absolute;left:11px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:.8rem;pointer-events:none}
.search-results{position:absolute;top:calc(100% + 6px);left:0;right:0;background:var(--card);border:1px solid var(--line2);border-radius:12px;overflow:hidden;z-index:200;display:none;box-shadow:var(--shadow)}
.search-results.show{display:block}
.sr-item{display:flex;align-items:center;gap:10px;padding:10px 14px;cursor:pointer;transition:background .15s}
.sr-item:hover{background:var(--ink3)}
.sr-item .sr-avatar{width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,var(--sky),var(--violet));display:flex;align-items:center;justify-content:center;font-size:.65rem;font-weight:800;color:#fff;flex-shrink:0}
.sr-info{flex:1;min-width:0}
.sr-name{font-size:.82rem;font-weight:700}
.sr-sub{font-size:.72rem;color:var(--muted)}
.sr-type{font-size:.68rem;font-weight:700;color:var(--sky);background:rgba(26,108,255,.1);padding:2px 7px;border-radius:999px}

.topbar-nav{display:flex;gap:2px;margin-left:auto}
.tnav{padding:7px 11px;border-radius:8px;border:none;background:transparent;color:var(--muted);font-size:.78rem;font-weight:700;transition:all .2s;display:flex;align-items:center;gap:5px;letter-spacing:-.01em;position:relative}
.tnav:hover{background:var(--ink3);color:var(--text)}
.tnav.active{background:rgba(26,108,255,.12);color:var(--sky)}
.nav-badge{position:absolute;top:4px;right:4px;width:8px;height:8px;background:var(--rose);border-radius:50%;border:2px solid var(--ink)}

.topbar-user{display:flex;align-items:center;gap:8px;cursor:pointer;padding:5px 9px;border-radius:9px;transition:background .2s;margin-left:4px}
.topbar-user:hover{background:var(--ink3)}
.avatar{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,var(--sky),var(--violet));display:flex;align-items:center;justify-content:center;font-size:.72rem;font-weight:800;color:#fff;flex-shrink:0;overflow:hidden}
.avatar img{width:100%;height:100%;object-fit:cover}
.avatar.lg{width:72px;height:72px;font-size:1.5rem}
.avatar.xl{width:100px;height:100px;font-size:2rem;border:3px solid var(--ink)}
.uname{font-size:.82rem;font-weight:700}
.logout-btn{background:none;border:none;color:var(--muted);font-size:.78rem;cursor:pointer;padding:6px 10px;border-radius:8px;font-weight:600;transition:all .2s}
.logout-btn:hover{background:rgba(244,63,94,.08);color:var(--rose)}

/* LAYOUT */
.main-layout{display:grid;grid-template-columns:230px 1fr 260px;gap:16px;max-width:1140px;margin:0 auto;padding:20px 14px}
@media(max-width:1024px){.main-layout{grid-template-columns:0 1fr 0;padding:12px 10px}.sidebar-left,.sidebar-right{display:none}}

.sidebar-left,.sidebar-right{display:flex;flex-direction:column;gap:12px}
.s-card{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);overflow:hidden}
.s-card-hero{padding:22px 18px 16px;background:linear-gradient(150deg,rgba(26,108,255,.1) 0%,rgba(124,58,237,.07) 100%);text-align:center;border-bottom:1px solid var(--line)}
.s-card-hero .avatar{margin:0 auto 10px;width:52px;height:52px;font-size:1.1rem}
.s-uname{font-weight:800;font-size:.92rem;letter-spacing:-.02em}
.s-headline{color:var(--muted);font-size:.73rem;margin-top:2px;font-weight:500}
.s-stats{display:grid;grid-template-columns:1fr 1fr;border-top:1px solid var(--line)}
.s-stat{padding:12px 8px;text-align:center}
.s-stat+.s-stat{border-left:1px solid var(--line)}
.s-stat .n{font-family:'Geist Mono',monospace;font-size:1.15rem;font-weight:500;color:var(--sky)}
.s-stat .l{font-size:.66rem;color:var(--muted);margin-top:2px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.s-links{padding:6px 0}
.s-link{display:flex;align-items:center;gap:10px;padding:9px 14px;color:var(--muted);font-size:.8rem;font-weight:700;transition:all .2s;cursor:pointer;letter-spacing:-.01em}
.s-link:hover,.s-link.active{background:var(--ink3);color:var(--text)}
.s-link.active{color:var(--sky)}
.s-link .icon{font-size:.85rem;width:16px;text-align:center}
.s-section{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:14px}
.s-section-title{font-size:.67rem;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px}

/* FEED */
.feed{display:flex;flex-direction:column;gap:12px}
.composer{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:16px}
.composer-top{display:flex;gap:11px;align-items:flex-start}
.composer-input{flex:1;background:var(--ink3);border:1px solid var(--line);border-radius:11px;padding:11px 14px;color:var(--text);font-size:.87rem;resize:none;min-height:46px;outline:none;transition:all .25s;line-height:1.6}
.composer-input:focus{border-color:var(--sky);min-height:84px;box-shadow:0 0 0 3px rgba(26,108,255,.07)}
.composer-input::placeholder{color:var(--dim)}
.composer-bar{display:flex;align-items:center;gap:6px;margin-top:10px;padding-top:10px;border-top:1px solid var(--line)}
.cbar-btn{padding:6px 12px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--muted);font-size:.75rem;font-weight:700;transition:all .2s}
.cbar-btn:hover{border-color:var(--sky);color:var(--sky)}
.post-btn{margin-left:auto;padding:7px 20px;background:var(--sky);border:none;border-radius:8px;color:#fff;font-size:.83rem;font-weight:800;transition:all .2s}
.post-btn:hover{background:var(--sky2);transform:translateY(-1px)}
.post-btn:disabled{opacity:.4;cursor:not-allowed;transform:none}
.attach-preview{display:none;align-items:center;gap:10px;background:rgba(26,108,255,.06);border:1px solid rgba(26,108,255,.18);border-radius:8px;padding:8px 12px;margin-top:8px;font-size:.8rem}
.attach-preview.show{display:flex}
.attach-preview span{flex:1;color:var(--sky);font-weight:600}
.attach-preview .rm{cursor:pointer;color:var(--muted);transition:color .2s}
.attach-preview .rm:hover{color:var(--rose)}

/* POST CARDS */
.post-card{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);overflow:hidden;animation:slideUp .3s cubic-bezier(.16,1,.3,1) both;transition:border-color .2s}
.post-card:hover{border-color:var(--line2)}
@keyframes slideUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
.post-header{display:flex;align-items:flex-start;gap:10px;padding:16px 16px 0}
.post-meta{flex:1;min-width:0}
.post-author{font-weight:800;font-size:.86rem;letter-spacing:-.02em}
.post-headline{color:var(--muted);font-size:.73rem;margin-top:1px;font-weight:500}
.post-time{color:var(--dim);font-size:.7rem;margin-top:2px;font-family:'Geist Mono',monospace}
.post-content{padding:11px 16px;font-size:.86rem;line-height:1.75;color:rgba(228,235,245,.87)}
.post-file{margin:0 16px 10px;border-radius:10px;overflow:hidden;border:1px solid var(--line)}
.post-file img{width:100%;max-height:320px;object-fit:cover;display:block}
.file-attach{display:flex;align-items:center;gap:10px;padding:11px 13px;background:var(--ink3)}
.file-attach .ficon{font-size:1.2rem}
.file-attach .fname{font-size:.82rem;font-weight:700}
.file-attach .ftype{font-size:.68rem;color:var(--muted);font-family:'Geist Mono',monospace}
.file-attach a{margin-left:auto;font-size:.74rem;color:var(--sky);font-weight:700;padding:5px 11px;border:1px solid rgba(26,108,255,.3);border-radius:7px;transition:all .2s}
.post-actions{display:flex;gap:2px;padding:7px 10px 10px;border-top:1px solid var(--line);margin-top:4px}
.p-action{padding:6px 12px;border-radius:8px;border:none;background:transparent;color:var(--muted);font-size:.77rem;font-weight:700;transition:all .2s;display:flex;align-items:center;gap:5px}
.p-action:hover{background:var(--ink3);color:var(--text)}
.p-action.liked{color:var(--sky);background:rgba(26,108,255,.08)}

/* COMMENTS */
.comments-area{border-top:1px solid var(--line);background:var(--ink3)}
.comment-item{display:flex;gap:9px;padding:10px 14px;border-bottom:1px solid rgba(35,43,56,.5)}
.comment-item:last-child{border-bottom:none}
.comment-body{flex:1;min-width:0}
.comment-author{font-size:.78rem;font-weight:800;letter-spacing:-.01em}
.comment-time{font-size:.68rem;color:var(--dim);font-family:'Geist Mono',monospace;margin-left:6px}
.comment-text{font-size:.81rem;color:rgba(228,235,245,.8);margin-top:3px;line-height:1.6}
.comment-input-area{display:flex;gap:8px;padding:10px 14px}
.comment-input-area input{flex:1;background:var(--ink2);border:1px solid var(--line);border-radius:8px;padding:8px 12px;color:var(--text);font-size:.81rem;outline:none;transition:border-color .2s}
.comment-input-area input:focus{border-color:var(--sky)}
.comment-input-area button{padding:8px 14px;background:var(--sky);border:none;border-radius:8px;color:#fff;font-size:.77rem;font-weight:800;transition:all .2s}
.comment-input-area button:hover{background:var(--sky2)}

/* PROFILE */
.profile-box{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);overflow:hidden;margin-bottom:12px}
.profile-cover{height:150px;background:linear-gradient(135deg,rgba(26,108,255,.25) 0%,rgba(124,58,237,.2) 50%,rgba(0,196,140,.1) 100%);position:relative;overflow:hidden}
.profile-cover::after{content:'';position:absolute;bottom:-1px;left:0;right:0;height:50px;background:linear-gradient(to top,var(--card),transparent)}
.profile-info{padding:0 20px 20px;margin-top:-42px;position:relative}
.profile-avatar-wrap{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:12px}
.profile-edit-btn{padding:7px 16px;border:1px solid var(--line2);border-radius:9px;background:transparent;color:var(--text);font-size:.8rem;font-weight:800;transition:all .2s}
.profile-edit-btn:hover{border-color:var(--sky);color:var(--sky)}
.profile-name{font-size:1.35rem;font-weight:900;letter-spacing:-.04em}
.profile-headline{color:var(--muted);font-size:.86rem;margin-top:3px;font-weight:500}
.profile-location{color:var(--dim);font-size:.78rem;margin-top:3px;font-family:'Geist Mono',monospace}
.profile-about{font-size:.85rem;line-height:1.75;color:rgba(228,235,245,.8);margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.profile-skills{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.skill-tag{background:rgba(26,108,255,.08);border:1px solid rgba(26,108,255,.2);color:var(--sky);padding:4px 11px;border-radius:999px;font-size:.72rem;font-weight:700;transition:all .2s}
.skill-tag:hover{background:rgba(26,108,255,.14)}

/* RESUME */
.resume-section{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:22px;margin-bottom:12px}
.rs-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.rs-title{font-size:1rem;font-weight:900;letter-spacing:-.03em}
.ai-badge{display:inline-flex;align-items:center;gap:6px;background:linear-gradient(135deg,rgba(124,58,237,.15),rgba(0,196,140,.1));border:1px solid rgba(124,58,237,.3);color:#c4b5fd;padding:5px 13px;border-radius:999px;font-size:.68rem;font-weight:800;text-transform:uppercase;letter-spacing:.1em}
.ai-badge::before{content:'✦';color:var(--mint)}
.rs-upload{border:2px dashed var(--line2);border-radius:12px;padding:32px;text-align:center;cursor:pointer;transition:all .25s;position:relative;background:var(--ink3)}
.rs-upload:hover,.rs-upload.drag{border-color:var(--sky);background:rgba(26,108,255,.04)}
.rs-upload input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%}
.rs-upload .icon{font-size:2rem;margin-bottom:10px;display:block}
.rs-upload h4{font-size:.92rem;font-weight:800;margin-bottom:5px}
.rs-upload p{color:var(--muted);font-size:.79rem}
.rs-upload strong{color:var(--sky)}
.analysis-result{display:none;margin-top:18px}
.analysis-result.show{display:block;animation:slideUp .4s ease both}
.score-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-bottom:16px}
.score-box{background:var(--ink3);border:1px solid var(--line);border-radius:11px;padding:14px 10px;text-align:center}
.score-box .val{font-family:'Geist Mono',monospace;font-size:1.4rem;font-weight:500;letter-spacing:-.04em}
.score-box .lbl{font-size:.67rem;color:var(--muted);margin-top:3px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}
.ai-card{background:var(--ink3);border:1px solid var(--line);border-radius:11px;padding:14px 16px;margin-bottom:10px;border-left:3px solid var(--violet)}
.ai-card.warn{border-left-color:var(--amber)}
.ai-card h4{font-size:.81rem;font-weight:800;color:#a78bfa;margin-bottom:5px}
.ai-card.warn h4{color:var(--amber)}
.ai-card p{color:var(--muted);font-size:.81rem;line-height:1.7}
.job-card-r{background:var(--ink3);border:1px solid var(--line);border-radius:11px;padding:13px 15px;margin-bottom:8px;display:flex;align-items:center;gap:13px;transition:border-color .2s}
.job-card-r:hover{border-color:var(--line2)}
.job-info{flex:1;min-width:0}
.job-title{font-size:.87rem;font-weight:800;letter-spacing:-.02em}
.job-co{font-size:.73rem;color:var(--muted);margin-top:2px;font-weight:600}
.job-bar{width:80px;height:4px;background:var(--line2);border-radius:4px;overflow:hidden;flex-shrink:0}
.job-fill{height:100%;background:linear-gradient(90deg,var(--sky),var(--violet));border-radius:4px;transition:width 1.2s cubic-bezier(.16,1,.3,1)}
.job-pct{font-family:'Geist Mono',monospace;font-size:.79rem;font-weight:500;color:var(--sky);min-width:36px;text-align:right}

/* ANALYTICS DASHBOARD */
.analytics-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
.metric-card{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:18px 16px;text-align:center}
.metric-val{font-family:'Geist Mono',monospace;font-size:2rem;font-weight:500;letter-spacing:-.05em}
.metric-label{font-size:.72rem;color:var(--muted);margin-top:4px;font-weight:700;text-transform:uppercase;letter-spacing:.08em}
.metric-delta{font-size:.75rem;margin-top:6px;font-weight:700}
.metric-delta.up{color:var(--mint)}
.metric-delta.down{color:var(--rose)}
.chart-card{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:18px;margin-bottom:14px}
.chart-title{font-size:.85rem;font-weight:800;letter-spacing:-.02em;margin-bottom:16px;color:var(--text)}
.bar-chart{display:flex;align-items:flex-end;gap:6px;height:100px}
.bar-col{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px}
.bar-fill{width:100%;background:linear-gradient(to top,var(--sky),var(--violet));border-radius:4px 4px 0 0;transition:height .8s cubic-bezier(.16,1,.3,1);min-height:2px}
.bar-label{font-size:.66rem;color:var(--muted);font-family:'Geist Mono',monospace;font-weight:500}
.activity-list{list-style:none}
.activity-item{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid rgba(35,43,56,.5)}
.activity-item:last-child{border-bottom:none}
.activity-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.activity-text{font-size:.81rem;color:rgba(228,235,245,.8);flex:1}
.activity-time{font-size:.69rem;color:var(--dim);font-family:'Geist Mono',monospace}
.skills-bar-list{display:flex;flex-direction:column;gap:10px}
.skill-bar-item .skill-bar-header{display:flex;justify-content:space-between;margin-bottom:5px}
.skill-bar-item .skill-name{font-size:.78rem;font-weight:700}
.skill-bar-item .skill-cnt{font-size:.74rem;color:var(--muted);font-family:'Geist Mono',monospace}
.skill-bar-track{height:5px;background:var(--line2);border-radius:4px;overflow:hidden}
.skill-bar-fill{height:100%;border-radius:4px;background:linear-gradient(90deg,var(--sky),var(--violet));transition:width .9s cubic-bezier(.16,1,.3,1)}

/* JOB BOARD */
.job-board-card{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:18px;margin-bottom:12px;transition:border-color .2s}
.job-board-card:hover{border-color:var(--line2)}
.jb-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px}
.jb-title-wrap{}
.jb-title{font-size:1rem;font-weight:900;letter-spacing:-.03em}
.jb-company{font-size:.8rem;color:var(--muted);margin-top:3px;font-weight:600}
.jb-meta{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}
.jb-badge{padding:3px 10px;border-radius:999px;font-size:.72rem;font-weight:700}
.jb-location{background:rgba(0,196,140,.08);border:1px solid rgba(0,196,140,.2);color:var(--mint)}
.jb-type{background:rgba(26,108,255,.08);border:1px solid rgba(26,108,255,.2);color:var(--sky)}
.jb-salary{background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);color:var(--amber)}
.jb-desc{font-size:.82rem;color:rgba(228,235,245,.72);line-height:1.7;margin-bottom:12px}
.jb-skills{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:14px}
.jb-skill{background:var(--ink3);border:1px solid var(--line);color:var(--muted);padding:3px 10px;border-radius:6px;font-size:.71rem;font-weight:700}
.jb-footer{display:flex;align-items:center;justify-content:space-between}
.apply-btn{padding:8px 20px;background:var(--sky);border:none;border-radius:9px;color:#fff;font-size:.82rem;font-weight:800;transition:all .2s}
.apply-btn:hover{background:var(--sky2);transform:translateY(-1px)}
.apply-btn:disabled{background:rgba(0,196,140,.15);color:var(--mint);cursor:not-allowed;transform:none}
.applied-tag{font-size:.78rem;color:var(--mint);font-weight:700;display:flex;align-items:center;gap:5px}
.job-filters{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:16px}
.jf-btn{padding:6px 14px;border-radius:999px;border:1px solid var(--line2);background:transparent;color:var(--muted);font-size:.76rem;font-weight:700;transition:all .2s;cursor:pointer}
.jf-btn:hover{border-color:var(--sky);color:var(--sky)}
.jf-btn.active{background:rgba(26,108,255,.1);border-color:var(--sky);color:var(--sky)}

/* NOTIFICATIONS */
.notif-panel{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);overflow:hidden}
.notif-header{display:flex;align-items:center;justify-content:space-between;padding:16px 18px;border-bottom:1px solid var(--line)}
.notif-title{font-size:.95rem;font-weight:900;letter-spacing:-.03em}
.mark-all-btn{font-size:.75rem;color:var(--sky);font-weight:700;background:none;border:none;cursor:pointer}
.mark-all-btn:hover{text-decoration:underline}
.notif-item{display:flex;align-items:flex-start;gap:12px;padding:13px 16px;border-bottom:1px solid rgba(35,43,56,.5);transition:background .15s}
.notif-item:last-child{border-bottom:none}
.notif-item.unread{background:rgba(26,108,255,.04)}
.notif-item:hover{background:var(--ink3)}
.notif-icon{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.9rem;flex-shrink:0}
.notif-icon.like{background:rgba(26,108,255,.12)}
.notif-icon.comment{background:rgba(0,196,140,.1)}
.notif-icon.connection{background:rgba(124,58,237,.1)}
.notif-icon.job{background:rgba(245,158,11,.1)}
.notif-body{flex:1;min-width:0}
.notif-msg{font-size:.82rem;line-height:1.6;color:rgba(228,235,245,.87)}
.notif-time{font-size:.7rem;color:var(--dim);font-family:'Geist Mono',monospace;margin-top:3px}
.unread-dot{width:7px;height:7px;background:var(--sky);border-radius:50%;flex-shrink:0;margin-top:6px}

/* DIRECT MESSAGES */
.dm-layout{display:grid;grid-template-columns:280px 1fr;gap:0;height:calc(100vh - 96px);border:1px solid var(--line);border-radius:var(--r2);overflow:hidden;background:var(--card)}
.dm-sidebar{border-right:1px solid var(--line);overflow-y:auto}
.dm-sidebar-header{padding:16px;border-bottom:1px solid var(--line);font-size:.9rem;font-weight:900;letter-spacing:-.02em}
.dm-convo-item{display:flex;align-items:center;gap:10px;padding:12px 14px;cursor:pointer;transition:background .15s;border-bottom:1px solid rgba(35,43,56,.4)}
.dm-convo-item:hover{background:var(--ink3)}
.dm-convo-item.active{background:rgba(26,108,255,.08)}
.dm-convo-info{flex:1;min-width:0}
.dm-convo-name{font-size:.84rem;font-weight:800;letter-spacing:-.01em}
.dm-convo-preview{font-size:.74rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.dm-convo-time{font-size:.68rem;color:var(--dim);font-family:'Geist Mono',monospace;flex-shrink:0}
.dm-chat{display:flex;flex-direction:column;height:100%}
.dm-chat-header{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px;flex-shrink:0}
.dm-chat-header .avatar{width:36px;height:36px;font-size:.78rem}
.dm-chat-name{font-size:.9rem;font-weight:800;letter-spacing:-.02em}
.dm-chat-status{font-size:.74rem;color:var(--mint)}
.dm-messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
.dm-msg{display:flex;gap:8px;max-width:75%}
.dm-msg.mine{flex-direction:row-reverse;align-self:flex-end}
.dm-bubble{background:var(--ink3);border:1px solid var(--line);border-radius:14px;padding:9px 13px;font-size:.83rem;line-height:1.6}
.dm-msg.mine .dm-bubble{background:rgba(26,108,255,.15);border-color:rgba(26,108,255,.25);color:var(--text)}
.dm-msg-time{font-size:.67rem;color:var(--dim);font-family:'Geist Mono',monospace;margin-top:3px;align-self:flex-end;flex-shrink:0}
.dm-input-area{padding:12px 16px;border-top:1px solid var(--line);display:flex;gap:8px;flex-shrink:0}
.dm-input{flex:1;background:var(--ink3);border:1px solid var(--line);border-radius:10px;padding:10px 14px;color:var(--text);font-size:.83rem;outline:none;transition:border-color .2s}
.dm-input:focus{border-color:var(--sky)}
.dm-send-btn{padding:10px 18px;background:var(--sky);border:none;border-radius:10px;color:#fff;font-size:.82rem;font-weight:800;transition:all .2s}
.dm-send-btn:hover{background:var(--sky2)}
.dm-empty{flex:1;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px;color:var(--muted)}
.dm-empty .icon{font-size:2.5rem;opacity:.3}
.dm-empty p{font-size:.85rem;font-weight:600}
.new-dm-btn{margin:12px;padding:9px;width:calc(100% - 24px);border:1px dashed var(--line2);background:transparent;border-radius:9px;color:var(--muted);font-size:.78rem;font-weight:700;transition:all .2s}
.new-dm-btn:hover{border-color:var(--sky);color:var(--sky)}

/* AI CHAT */
.ai-chat-container{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);overflow:hidden;display:flex;flex-direction:column;height:calc(100vh - 96px)}
.ai-chat-header{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;flex-shrink:0}
.ai-chat-icon{width:38px;height:38px;border-radius:50%;background:linear-gradient(135deg,var(--violet),var(--mint));display:flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0}
.ai-chat-title{font-size:.95rem;font-weight:900;letter-spacing:-.02em}
.ai-chat-sub{font-size:.75rem;color:var(--muted)}
.ai-messages{flex:1;overflow-y:auto;padding:18px;display:flex;flex-direction:column;gap:14px}
.ai-msg{display:flex;gap:11px;max-width:85%;animation:slideUp .3s ease both}
.ai-msg.user{flex-direction:row-reverse;align-self:flex-end}
.ai-msg-icon{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.8rem;flex-shrink:0}
.ai-msg.assistant .ai-msg-icon{background:linear-gradient(135deg,var(--violet),var(--mint))}
.ai-msg.user .ai-msg-icon{background:linear-gradient(135deg,var(--sky),var(--violet))}
.ai-bubble{border-radius:14px;padding:11px 15px;font-size:.84rem;line-height:1.75}
.ai-msg.assistant .ai-bubble{background:var(--ink3);border:1px solid var(--line)}
.ai-msg.user .ai-bubble{background:rgba(26,108,255,.15);border:1px solid rgba(26,108,255,.25)}
.ai-chat-input-area{padding:14px 18px;border-top:1px solid var(--line);display:flex;gap:8px;flex-shrink:0}
.ai-chat-input{flex:1;background:var(--ink3);border:1px solid var(--line);border-radius:11px;padding:11px 14px;color:var(--text);font-size:.84rem;outline:none;resize:none;transition:border-color .2s;min-height:44px;max-height:120px;line-height:1.6}
.ai-chat-input:focus{border-color:var(--sky)}
.ai-send-btn{padding:11px 18px;background:var(--sky);border:none;border-radius:11px;color:#fff;font-size:.84rem;font-weight:800;transition:all .2s;align-self:flex-end}
.ai-send-btn:hover{background:var(--sky2)}
.ai-send-btn:disabled{opacity:.4;cursor:not-allowed}
.ai-suggestions{display:flex;gap:7px;flex-wrap:wrap;padding:0 18px 12px}
.ai-sug-btn{padding:6px 13px;border-radius:999px;border:1px solid var(--line2);background:transparent;color:var(--muted);font-size:.75rem;font-weight:700;transition:all .2s;cursor:pointer}
.ai-sug-btn:hover{border-color:var(--sky);color:var(--sky);background:rgba(26,108,255,.05)}
.typing-indicator{display:flex;gap:4px;padding:11px 15px;background:var(--ink3);border:1px solid var(--line);border-radius:14px;width:fit-content}
.typing-dot{width:6px;height:6px;background:var(--muted);border-radius:50%;animation:bounce .9s infinite}
.typing-dot:nth-child(2){animation-delay:.15s}
.typing-dot:nth-child(3){animation-delay:.3s}
@keyframes bounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-5px)}}

/* NETWORK */
.people-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:11px}
.people-card{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:20px 16px;text-align:center;transition:all .2s}
.people-card:hover{border-color:var(--line2);transform:translateY(-2px)}
.people-card .avatar{margin:0 auto 10px;width:56px;height:56px;font-size:1.2rem}
.people-name{font-weight:800;font-size:.88rem;letter-spacing:-.02em}
.people-role{color:var(--muted);font-size:.74rem;margin:4px 0 12px;font-weight:500}
.connect-btn{width:100%;padding:7px;border-radius:8px;border:1px solid rgba(26,108,255,.35);background:transparent;color:var(--sky);font-size:.77rem;font-weight:800;transition:all .2s}
.connect-btn:hover{background:var(--sky);color:#fff;border-color:var(--sky)}
.connect-btn:disabled{background:rgba(0,196,140,.1);color:var(--mint);border-color:rgba(0,196,140,.3);cursor:not-allowed}
.suggest-item{display:flex;align-items:center;gap:10px;padding:9px 13px;transition:background .15s;border-bottom:1px solid var(--line)}
.suggest-item:last-child{border-bottom:none}
.suggest-item:hover{background:var(--ink3)}
.suggest-info{flex:1;min-width:0}
.suggest-name{font-weight:800;font-size:.81rem}
.suggest-role{color:var(--muted);font-size:.71rem;font-weight:500}
.s-connect-btn{padding:5px 11px;border-radius:999px;border:1px solid rgba(26,108,255,.3);background:transparent;color:var(--sky);font-size:.71rem;font-weight:800;transition:all .2s}
.s-connect-btn:hover{background:var(--sky);color:#fff}

/* MODALS */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.72);backdrop-filter:blur(8px);z-index:200;align-items:center;justify-content:center;padding:20px}
.modal-bg.show{display:flex}
.modal{background:var(--ink2);border:1px solid var(--line2);border-radius:22px;padding:26px;width:100%;max-width:480px;max-height:86vh;overflow-y:auto;position:relative;animation:slideUp .3s cubic-bezier(.16,1,.3,1) both}
.modal h2{font-size:1.05rem;font-weight:900;letter-spacing:-.03em;margin-bottom:16px}
.modal-close{position:absolute;top:13px;right:13px;background:var(--ink3);border:none;border-radius:8px;width:28px;height:28px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--muted);font-size:.85rem;transition:all .2s}
.modal-close:hover{background:rgba(244,63,94,.1);color:var(--rose)}
.mfield{margin-bottom:12px}
.mfield label{display:block;font-size:.69rem;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px}
.mfield input,.mfield textarea{width:100%;background:var(--ink3);border:1px solid var(--line);border-radius:9px;padding:9px 12px;color:var(--text);font-size:.84rem;outline:none;transition:all .2s}
.mfield input:focus,.mfield textarea:focus{border-color:var(--sky);box-shadow:0 0 0 3px rgba(26,108,255,.07)}
.mfield textarea{resize:vertical;min-height:78px;line-height:1.6}
.modal-save{width:100%;padding:11px;background:var(--sky);border:none;border-radius:10px;color:#fff;font-size:.88rem;font-weight:800;margin-top:6px;transition:all .2s}
.modal-save:hover{background:var(--sky2);transform:translateY(-1px)}

/* MISC */
.tag{display:inline-block;padding:3px 9px;border-radius:6px;font-size:.7rem;font-weight:800}
.tag-blue{background:rgba(26,108,255,.1);border:1px solid rgba(26,108,255,.2);color:var(--sky)}
.tag-green{background:rgba(0,196,140,.1);border:1px solid rgba(0,196,140,.2);color:var(--mint)}
.tag-purple{background:rgba(124,58,237,.1);border:1px solid rgba(124,58,237,.2);color:#a78bfa}
.tag-amber{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.2);color:var(--amber)}
.page{display:none}.page.show{display:block}
.spinner{width:30px;height:30px;border:2.5px solid var(--line2);border-top-color:var(--sky);border-radius:50%;animation:spin .65s linear infinite;margin:0 auto}
@keyframes spin{to{transform:rotate(360deg)}}
.loading-wrap{padding:40px;text-align:center;color:var(--muted);font-size:.83rem}
.loading-wrap p{margin-top:10px;font-weight:600}
.empty-state{text-align:center;padding:52px 20px;color:var(--muted)}
.empty-state .icon{font-size:2rem;margin-bottom:10px;display:block;opacity:.4}
.empty-state h3{font-size:.95rem;font-weight:900;color:var(--text);margin-bottom:5px}
.empty-state p{font-size:.82rem}
.toast{position:fixed;bottom:20px;right:20px;background:var(--mint);color:#051a12;padding:10px 17px;border-radius:10px;font-weight:800;font-size:.81rem;z-index:999;transform:translateY(80px);opacity:0;transition:all .3s cubic-bezier(.16,1,.3,1);display:flex;align-items:center;gap:7px}
.toast.show{transform:translateY(0);opacity:1}
.toast.err{background:var(--rose);color:#fff}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.section-title{font-size:1rem;font-weight:900;letter-spacing:-.03em}
.avatar-upload-wrap{position:relative;display:inline-block;cursor:pointer}
.avatar-upload-wrap input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%}
.avatar-overlay{position:absolute;inset:0;border-radius:50%;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;font-size:.65rem;font-weight:800;color:#fff;opacity:0;transition:opacity .2s}
.avatar-upload-wrap:hover .avatar-overlay{opacity:1}
</style>
</head>
<body>

<!-- AUTH PAGE -->
<div class="auth-page" id="authPage">
  <div class="auth-wordmark">gath<span>R</span></div>
  <div class="auth-card">
    <div class="auth-badge">Professional Network</div>
    <h1 class="auth-h1">Where careers <em>connect</em>.</h1>
    <p class="auth-sub">Join gathR — the network built for ambitious professionals.</p>
    <div class="auth-tabs">
      <button class="auth-tab active" onclick="showAuthTab('login')">Sign in</button>
      <button class="auth-tab" onclick="showAuthTab('register')">Create account</button>
    </div>
    <div class="auth-err" id="authErr"></div>
    <div id="loginForm">
      <div class="field"><label>Email</label><input type="email" id="loginEmail" placeholder="you@company.com"/></div>
      <div class="field"><label>Password</label><input type="password" id="loginPass" placeholder="••••••••"/></div>
      <button class="auth-btn" onclick="doLogin()">Sign in →</button>
    </div>
    <div id="registerForm" style="display:none">
      <div class="field"><label>Full name</label><input type="text" id="regName" placeholder="Jane Smith"/></div>
      <div class="field"><label>Email</label><input type="email" id="regEmail" placeholder="you@company.com"/></div>
      <div class="field"><label>Password</label><input type="password" id="regPass" placeholder="Min 6 characters"/></div>
      <div class="field"><label>Job title / headline</label><input type="text" id="regHeadline" placeholder="Software Engineer at TechCorp"/></div>
      <button class="auth-btn" onclick="doRegister()">Create account →</button>
    </div>
  </div>
</div>

<!-- APP SHELL -->
<div class="app" id="appShell">

  <!-- TOPBAR -->
  <div class="topbar">
    <div class="topbar-logo">gath<span>R</span></div>
    <div class="topbar-search">
      <span class="si">⌕</span>
      <input type="text" placeholder="Search people, posts..." id="searchInput" oninput="doSearch(this.value)" onblur="setTimeout(()=>hideSearch(),150)"/>
      <div class="search-results" id="searchResults"></div>
    </div>
    <div class="topbar-nav">
      <button class="tnav active" onclick="showPage('feed')">Feed</button>
      <button class="tnav" onclick="showPage('profile')">Profile</button>
      <button class="tnav" onclick="showPage('resume')">Resume</button>
      <button class="tnav" onclick="showPage('analytics')">Analytics</button>
      <button class="tnav" onclick="showPage('jobs')">Jobs</button>
      <button class="tnav" onclick="showPage('messages')">Messages<span class="nav-badge" id="msgBadge" style="display:none"></span></button>
      <button class="tnav" onclick="showPage('notifications')">
        <span>Alerts</span><span class="nav-badge" id="notifBadge" style="display:none"></span>
      </button>
      <button class="tnav" onclick="showPage('ai')">✦ AI</button>
      <button class="tnav" onclick="showPage('network')">Network</button>
    </div>
    <div class="topbar-user" onclick="showPage('profile')">
      <div class="avatar" id="navAvatar"></div>
      <span class="uname" id="navName"></span>
    </div>
    <button class="logout-btn" onclick="doLogout()">Sign out</button>
  </div>

  <!-- ══ FEED PAGE ══ -->
  <div class="page show" id="page-feed">
    <div class="main-layout">
      <div class="sidebar-left">
        <div class="s-card">
          <div class="s-card-hero">
            <div class="avatar" id="sideAvatar"></div>
            <div class="s-uname" id="sideName"></div>
            <div class="s-headline" id="sideHeadline"></div>
          </div>
          <div class="s-stats">
            <div class="s-stat"><div class="n" id="statPosts">0</div><div class="l">Posts</div></div>
            <div class="s-stat"><div class="n" id="statConn">0</div><div class="l">Connections</div></div>
          </div>
          <div class="s-links">
            <div class="s-link active" onclick="showPage('feed')"><span class="icon">⌂</span>Feed</div>
            <div class="s-link" onclick="showPage('profile')"><span class="icon">◎</span>Profile</div>
            <div class="s-link" onclick="showPage('analytics')"><span class="icon">📊</span>Analytics</div>
            <div class="s-link" onclick="showPage('jobs')"><span class="icon">💼</span>Job Board</div>
            <div class="s-link" onclick="showPage('ai')"><span class="icon">✦</span>AI Assistant</div>
            <div class="s-link" onclick="showPage('messages')"><span class="icon">💬</span>Messages</div>
          </div>
        </div>
      </div>
      <div class="feed">
        <div class="composer">
          <div class="composer-top">
            <div class="avatar" id="compAvatar"></div>
            <textarea class="composer-input" id="postText" placeholder="What's on your mind? Share an update, insight, or win..."></textarea>
          </div>
          <div class="attach-preview" id="attachPreview">
            <span id="attachName"></span>
            <span class="rm" onclick="clearAttach()">✕</span>
          </div>
          <div class="composer-bar">
            <label class="cbar-btn" style="cursor:pointer">📎 Attach<input type="file" id="attachFile" style="display:none" accept=".pdf,.doc,.docx,.txt,.png,.jpg,.jpeg,.gif" onchange="handleAttach(this)"></label>
            <button class="cbar-btn" onclick="addEmoji()">😊 Emoji</button>
            <button class="post-btn" id="postBtn" onclick="submitPost()">Post</button>
          </div>
        </div>
        <div id="feedPosts"><div class="loading-wrap"><div class="spinner"></div><p>Loading feed...</p></div></div>
      </div>
      <div class="sidebar-right">
        <div class="s-card">
          <div style="padding:12px 13px 0">
            <div class="s-section-title" style="padding-bottom:8px">People you may know</div>
          </div>
          <div id="suggestions"></div>
        </div>
        <div class="s-section">
          <div class="s-section-title">Your skills</div>
          <div id="sideSkills" style="display:flex;flex-wrap:wrap;gap:5px"></div>
          <button style="margin-top:10px;width:100%;padding:7px;border-radius:8px;border:1px dashed var(--line2);background:transparent;color:var(--muted);font-size:.74rem;font-weight:700;cursor:pointer;transition:all .2s" onclick="openEditProfile()" onmouseover="this.style.borderColor='var(--sky)';this.style.color='var(--sky)'" onmouseout="this.style.borderColor='var(--line2)';this.style.color='var(--muted)'">+ Add skills</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ══ PROFILE PAGE ══ -->
  <div class="page" id="page-profile">
    <div class="main-layout">
      <div></div>
      <div>
        <div class="profile-box">
          <div class="profile-cover"></div>
          <div class="profile-info">
            <div class="profile-avatar-wrap">
              <div class="avatar xl" id="profAvatar"></div>
              <button class="profile-edit-btn" onclick="openEditProfile()">✏ Edit profile</button>
            </div>
            <div class="profile-name" id="profName"></div>
            <div class="profile-headline" id="profHeadline"></div>
            <div class="profile-location" id="profLocation"></div>
            <div class="profile-about" id="profAbout"></div>
            <div class="profile-skills" id="profSkills"></div>
          </div>
        </div>
        <div id="profilePosts"></div>
      </div>
      <div></div>
    </div>
  </div>

  <!-- ══ RESUME PAGE ══ -->
  <div class="page" id="page-resume">
    <div class="main-layout">
      <div></div>
      <div>
        <div class="resume-section">
          <div class="rs-header">
            <div class="rs-title">Resume &amp; AI Analysis</div>
            <span class="ai-badge">AI Powered</span>
          </div>
          <div class="rs-upload" id="resumeDropZone">
            <input type="file" id="resumeFileInput" accept=".pdf,.txt" onchange="handleResumeUpload(this)"/>
            <span class="icon">⬆</span>
            <h4>Drop your resume here</h4>
            <p><strong>PDF or TXT</strong> — AI analyzes skills, ATS score &amp; job matches</p>
          </div>
          <div id="resumeLoading" style="display:none" class="loading-wrap">
            <div class="spinner"></div><p id="resumeLoadText">Analyzing...</p>
          </div>
          <div class="analysis-result" id="analysisResult"></div>
        </div>
      </div>
      <div></div>
    </div>
  </div>

  <!-- ══ ANALYTICS PAGE ══ -->
  <div class="page" id="page-analytics">
    <div class="main-layout">
      <div></div>
      <div id="analyticsContent">
        <div class="loading-wrap"><div class="spinner"></div><p>Loading analytics...</p></div>
      </div>
      <div></div>
    </div>
  </div>

  <!-- ══ JOBS PAGE ══ -->
  <div class="page" id="page-jobs">
    <div class="main-layout">
      <div></div>
      <div>
        <div style="margin-bottom:16px">
          <div class="section-header">
            <div class="section-title">Job Board</div>
            <div id="appliedCount" style="font-size:.8rem;color:var(--muted);font-weight:700"></div>
          </div>
          <div class="job-filters" id="jobFilters">
            <button class="jf-btn active" onclick="filterJobs('all', this)">All jobs</button>
            <button class="jf-btn" onclick="filterJobs('Remote', this)">Remote</button>
            <button class="jf-btn" onclick="filterJobs('Full-time', this)">Full-time</button>
            <button class="jf-btn" onclick="filterJobs('Contract', this)">Contract</button>
            <button class="jf-btn" onclick="filterJobs('Hyderabad', this)">Hyderabad</button>
            <button class="jf-btn" onclick="filterJobs('Bangalore', this)">Bangalore</button>
          </div>
        </div>
        <div id="jobsList"></div>
      </div>
      <div></div>
    </div>
  </div>

  <!-- ══ NOTIFICATIONS PAGE ══ -->
  <div class="page" id="page-notifications">
    <div class="main-layout">
      <div></div>
      <div>
        <div class="notif-panel">
          <div class="notif-header">
            <div class="notif-title">Notifications</div>
            <button class="mark-all-btn" onclick="markAllRead()">Mark all read</button>
          </div>
          <div id="notifList"><div class="loading-wrap"><div class="spinner"></div><p>Loading...</p></div></div>
        </div>
      </div>
      <div></div>
    </div>
  </div>

  <!-- ══ MESSAGES PAGE ══ -->
  <div class="page" id="page-messages">
    <div class="main-layout" style="grid-template-columns:1fr">
      <div class="dm-layout" id="dmLayout">
        <div class="dm-sidebar">
          <div class="dm-sidebar-header">Messages</div>
          <button class="new-dm-btn" onclick="openNewDM()">+ New conversation</button>
          <div id="dmConvoList"></div>
        </div>
        <div id="dmChatArea">
          <div class="dm-empty"><span class="icon">💬</span><p>Select a conversation</p></div>
        </div>
      </div>
    </div>
  </div>

  <!-- ══ AI CHAT PAGE ══ -->
  <div class="page" id="page-ai">
    <div class="main-layout" style="grid-template-columns:1fr">
      <div class="ai-chat-container">
        <div class="ai-chat-header">
          <div class="ai-chat-icon">✦</div>
          <div>
            <div class="ai-chat-title">gathR AI Assistant</div>
            <div class="ai-chat-sub">Career coach, resume advisor &amp; networking guide</div>
          </div>
          <button onclick="clearAIChat()" style="margin-left:auto;padding:6px 13px;border-radius:8px;border:1px solid var(--line2);background:transparent;color:var(--muted);font-size:.75rem;font-weight:700;cursor:pointer;transition:all .2s" onmouseover="this.style.borderColor='var(--sky)';this.style.color='var(--sky)'" onmouseout="this.style.borderColor='var(--line2)';this.style.color='var(--muted)'">Clear chat</button>
        </div>
        <div class="ai-messages" id="aiMessages">
          <div class="ai-msg assistant">
            <div class="ai-msg-icon">✦</div>
            <div class="ai-bubble">Hi! I'm your gathR AI assistant. I can help you with career advice, resume tips, interview prep, networking strategies, and more. What would you like to discuss?</div>
          </div>
        </div>
        <div class="ai-suggestions" id="aiSuggestions">
          <button class="ai-sug-btn" onclick="sendAISuggestion(this)">How do I improve my LinkedIn?</button>
          <button class="ai-sug-btn" onclick="sendAISuggestion(this)">Tips for a career change?</button>
          <button class="ai-sug-btn" onclick="sendAISuggestion(this)">How to ace a tech interview?</button>
          <button class="ai-sug-btn" onclick="sendAISuggestion(this)">Write a cold outreach message</button>
        </div>
        <div class="ai-chat-input-area">
          <textarea class="ai-chat-input" id="aiInput" placeholder="Ask about your career, resume, interviews..." rows="1" onkeydown="aiKeydown(event)"></textarea>
          <button class="ai-send-btn" id="aiSendBtn" onclick="sendAIMessage()">Send</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ══ NETWORK PAGE ══ -->
  <div class="page" id="page-network">
    <div class="main-layout">
      <div></div>
      <div>
        <div style="background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:22px">
          <div class="section-title" style="margin-bottom:16px">Grow your network</div>
          <div id="networkList"><div class="loading-wrap"><div class="spinner"></div><p>Loading...</p></div></div>
        </div>
      </div>
      <div></div>
    </div>
  </div>

</div><!-- end appShell -->

<!-- EDIT PROFILE MODAL -->
<div class="modal-bg" id="editProfileModal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('editProfileModal')">✕</button>
    <h2>Edit Profile</h2>
    <div style="display:flex;justify-content:center;margin-bottom:18px">
      <div class="avatar-upload-wrap">
        <div class="avatar xl" id="ep_avatarPreview"></div>
        <div class="avatar-overlay">📷</div>
        <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" onchange="previewAvatar(this)" id="ep_avatarInput"/>
      </div>
    </div>
    <div class="mfield"><label>Full name</label><input id="ep_name"/></div>
    <div class="mfield"><label>Headline</label><input id="ep_headline" placeholder="Senior Engineer at Google"/></div>
    <div class="mfield"><label>Location</label><input id="ep_location" placeholder="Hyderabad, IN"/></div>
    <div class="mfield"><label>About</label><textarea id="ep_about" placeholder="Your professional story..."></textarea></div>
    <div class="mfield"><label>Skills (comma separated)</label><input id="ep_skills" placeholder="Python, React, ML..."/></div>
    <button class="modal-save" onclick="saveProfile()">Save changes</button>
  </div>
</div>

<!-- NEW DM MODAL -->
<div class="modal-bg" id="newDMModal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('newDMModal')">✕</button>
    <h2>New Conversation</h2>
    <div id="dmUserList" style="display:flex;flex-direction:column;gap:4px;margin-top:4px"></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let ME = null;
let attachedFile = null;
let avatarBase64 = null;
let aiChatHistory = [];
let appliedJobs = new Set();
let currentDMUser = null;
let allUsers = [];
let allPosts = [];

window.addEventListener('DOMContentLoaded', checkSession);

async function checkSession() {
  try {
    const r = await fetch('/api/me');
    if (r.ok) { ME = await r.json(); showApp(); }
  } catch(e) {}
}

// ── AUTH ──
function showAuthTab(tab) {
  document.querySelectorAll('.auth-tab').forEach((t,i) => t.classList.toggle('active', (i===0&&tab==='login')||(i===1&&tab==='register')));
  document.getElementById('loginForm').style.display = tab==='login'?'block':'none';
  document.getElementById('registerForm').style.display = tab==='register'?'block':'none';
  document.getElementById('authErr').classList.remove('show');
}
async function doLogin() {
  const email = document.getElementById('loginEmail').value.trim();
  const pass = document.getElementById('loginPass').value;
  if (!email||!pass) { showAuthErr('Please fill all fields'); return; }
  const r = await fetch('/api/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password:pass})});
  const d = await r.json();
  if (d.error) { showAuthErr(d.error); return; }
  ME = d.user; showApp();
}
async function doRegister() {
  const name=document.getElementById('regName').value.trim();
  const email=document.getElementById('regEmail').value.trim();
  const pass=document.getElementById('regPass').value;
  const headline=document.getElementById('regHeadline').value.trim();
  if (!name||!email||!pass) { showAuthErr('Please fill all fields'); return; }
  if (pass.length<6) { showAuthErr('Password must be at least 6 chars'); return; }
  const r = await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,email,password:pass,headline})});
  const d = await r.json();
  if (d.error) { showAuthErr(d.error); return; }
  ME = d.user; showApp();
}
async function doLogout() {
  await fetch('/api/logout',{method:'POST'});
  ME = null;
  document.getElementById('appShell').classList.remove('show');
  document.getElementById('authPage').style.display='flex';
  showAuthTab('login');
}
function showAuthErr(msg) {
  const el = document.getElementById('authErr');
  el.textContent = msg; el.classList.add('show');
}

// ── APP INIT ──
async function showApp() {
  document.getElementById('authPage').style.display='none';
  document.getElementById('appShell').classList.add('show');
  await loadUsers();
  refreshUserUI(); loadFeed(); loadSuggestions(); loadSavedAnalysis();
  loadNotifBadge(); loadMsgBadge(); loadAppliedJobs();
}
async function loadUsers() {
  const r = await fetch('/api/users');
  allUsers = await r.json();
}
function refreshUserUI() {
  if (!ME) return;
  const ini = ME.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
  ['navAvatar','sideAvatar','compAvatar','profAvatar'].forEach(id => {
    const el = document.getElementById(id); if (!el) return;
    el.innerHTML = ME.avatar ? `<img src="${ME.avatar}" style="width:100%;height:100%;object-fit:cover;border-radius:50%">` : ini;
  });
  setText('navName', ME.name.split(' ')[0]);
  setText('sideName', ME.name);
  setText('sideHeadline', ME.headline||'');
  setText('profName', ME.name);
  setText('profHeadline', ME.headline||'Professional on gathR');
  setText('profLocation', ME.location ? '◎ '+ME.location : '');
  setText('profAbout', ME.about||'');
  const skills = JSON.parse(ME.skills||'[]');
  renderSkillTags('profSkills', skills);
  renderSkillTags('sideSkills', skills);
}
function renderSkillTags(cid, skills) {
  const el = document.getElementById(cid); if (!el) return;
  el.innerHTML = skills.map(s=>`<span class="skill-tag">${s}</span>`).join('');
}

// ── PAGES ──
function showPage(page) {
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('show'));
  document.querySelectorAll('.tnav').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.s-link').forEach(b=>b.classList.remove('active'));
  document.getElementById('page-'+page).classList.add('show');
  document.querySelectorAll('.tnav').forEach(b=>{ if(b.textContent.toLowerCase().includes(page.slice(0,4))) b.classList.add('active'); });
  document.querySelectorAll('.s-link').forEach(b=>{ if(b.textContent.toLowerCase().trim().startsWith(page.slice(0,4))) b.classList.add('active'); });
  if (page==='profile') loadProfilePosts();
  if (page==='network') loadNetwork();
  if (page==='notifications') loadNotifications();
  if (page==='analytics') loadAnalytics();
  if (page==='jobs') loadJobs();
  if (page==='messages') loadDMConvos();
}

// ── SEARCH ──
async function doSearch(q) {
  const sr = document.getElementById('searchResults');
  if (!q || q.length < 2) { sr.classList.remove('show'); return; }
  const [postsR, usersR] = await Promise.all([fetch('/api/posts'), fetch('/api/users')]);
  const posts = await postsR.json();
  const users = await usersR.json();
  const ql = q.toLowerCase();
  const matchedUsers = users.filter(u => u.name.toLowerCase().includes(ql) || (u.headline||'').toLowerCase().includes(ql)).slice(0,3);
  const matchedPosts = posts.filter(p => p.content.toLowerCase().includes(ql)).slice(0,3);
  if (!matchedUsers.length && !matchedPosts.length) { sr.innerHTML='<div style="padding:12px 14px;color:var(--muted);font-size:.82rem;font-weight:600">No results found</div>'; sr.classList.add('show'); return; }
  let html = '';
  matchedUsers.forEach(u => {
    const ini = u.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
    html += `<div class="sr-item" onclick="goToUser(${u.id})">
      <div class="sr-avatar">${ini}</div>
      <div class="sr-info"><div class="sr-name">${u.name}</div><div class="sr-sub">${u.headline||'gathR member'}</div></div>
      <span class="sr-type">Person</span>
    </div>`;
  });
  matchedPosts.forEach(p => {
    const preview = p.content.substring(0,60)+(p.content.length>60?'...':'');
    html += `<div class="sr-item" onclick="goToPost(${p.id})">
      <div class="sr-avatar" style="background:linear-gradient(135deg,var(--mint),var(--sky))">✦</div>
      <div class="sr-info"><div class="sr-name">${p.author_name}</div><div class="sr-sub">${preview}</div></div>
      <span class="sr-type">Post</span>
    </div>`;
  });
  sr.innerHTML = html; sr.classList.add('show');
}
function hideSearch() { document.getElementById('searchResults').classList.remove('show'); }
function goToUser(id) { hideSearch(); showPage('network'); }
function goToPost(id) { hideSearch(); showPage('feed'); setTimeout(()=>{ const el=document.getElementById('post-'+id); if(el) el.scrollIntoView({behavior:'smooth',block:'center'}); },200); }

// ── FEED ──
async function loadFeed() {
  const r = await fetch('/api/posts');
  allPosts = await r.json();
  const c = document.getElementById('feedPosts');
  if (!allPosts.length) { c.innerHTML='<div class="empty-state"><span class="icon">✦</span><h3>No posts yet</h3><p>Be the first to share!</p></div>'; return; }
  c.innerHTML = allPosts.map(renderPost).join('');
  const myPosts = allPosts.filter(p=>p.user_id===ME.id);
  document.getElementById('statPosts').textContent = myPosts.length;
}

function renderPost(p) {
  const likes = JSON.parse(p.likes||'[]');
  const liked = ME && likes.includes(ME.id);
  const isOwner = ME && p.user_id===ME.id;
  let fileHtml = '';
  if (p.file_url) {
    const isImg = ['png','jpg','jpeg','gif'].some(e=>p.file_name&&p.file_name.toLowerCase().endsWith(e));
    if (isImg) {
      fileHtml = `<div class="post-file"><img src="/uploads/${p.file_url}" alt="attachment"/></div>`;
    } else {
      const icons = {pdf:'📄',doc:'📝',docx:'📝',txt:'📃'};
      const ext = p.file_name ? p.file_name.split('.').pop().toLowerCase() : '';
      fileHtml = `<div class="post-file"><div class="file-attach"><span class="ficon">${icons[ext]||'📎'}</span><div><div class="fname">${p.file_name||'Attachment'}</div><div class="ftype">${ext.toUpperCase()}</div></div><a href="/uploads/${p.file_url}" target="_blank">View →</a></div></div>`;
    }
  }
  const ini = (p.author_name||'U').split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
  const ownerActions = isOwner ? `
    <button class="p-action" onclick="editPost(${p.id},'${escAttr(p.content)}')">✏ Edit</button>
    <button class="p-action" style="color:var(--rose)" onclick="deletePost(${p.id})">🗑 Delete</button>` : '';
  return `<div class="post-card" id="post-${p.id}">
    <div class="post-header">
      <div class="avatar">${ini}</div>
      <div class="post-meta">
        <div class="post-author">${p.author_name||'User'}</div>
        <div class="post-headline">${p.author_headline||''}</div>
        <div class="post-time">${timeAgo(p.created_at)}</div>
      </div>
    </div>
    <div class="post-content" id="post-content-${p.id}">${escHtml(p.content)}</div>
    ${fileHtml}
    <div class="post-actions">
      <button class="p-action ${liked?'liked':''}" onclick="toggleLike(${p.id})">👍 ${likes.length>0?likes.length:''} Like</button>
      <button class="p-action" onclick="toggleComments(${p.id})">💬 Comment</button>
      <button class="p-action" onclick="sharePost(${p.id})">↗ Share</button>
      ${ownerActions}
    </div>
    <div id="comments-section-${p.id}" style="display:none">
      <div class="comments-area" id="comments-list-${p.id}"></div>
      <div class="comment-input-area">
        <input type="text" id="comment-input-${p.id}" placeholder="Write a comment..." onkeydown="if(event.key==='Enter')submitComment(${p.id})"/>
        <button onclick="submitComment(${p.id})">Post</button>
      </div>
    </div>
  </div>`;
}

async function toggleComments(postId) {
  const section = document.getElementById('comments-section-'+postId);
  const visible = section.style.display !== 'none';
  if (visible) { section.style.display='none'; return; }
  section.style.display='block';
  await loadComments(postId);
  document.getElementById('comment-input-'+postId).focus();
}
async function loadComments(postId) {
  const r = await fetch('/api/posts/'+postId+'/comments');
  const comments = await r.json();
  const list = document.getElementById('comments-list-'+postId);
  if (!comments.length) { list.innerHTML='<div style="padding:10px 14px;color:var(--muted);font-size:.8rem;font-weight:600">No comments yet</div>'; return; }
  list.innerHTML = comments.map(c => {
    const ini = (c.author_name||'U').split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
    return `<div class="comment-item">
      <div class="avatar" style="width:28px;height:28px;font-size:.65rem;flex-shrink:0">${ini}</div>
      <div class="comment-body">
        <span class="comment-author">${c.author_name}</span>
        <span class="comment-time">${timeAgo(c.created_at)}</span>
        <div class="comment-text">${escHtml(c.content)}</div>
      </div>
    </div>`;
  }).join('');
}
async function submitComment(postId) {
  const input = document.getElementById('comment-input-'+postId);
  const val = input.value.trim();
  if (!val) return;
  const r = await fetch('/api/posts/'+postId+'/comments', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:val})});
  const d = await r.json();
  if (d.error) { showToast('Error: '+d.error, true); return; }
  input.value = '';
  await loadComments(postId);
  showToast('Comment posted! ✓');
}

async function toggleLike(postId) {
  await fetch('/api/posts/'+postId+'/like', {method:'POST'});
  loadFeed();
}
async function submitPost() {
  const text = document.getElementById('postText').value.trim();
  if (!text && !attachedFile) return;
  const btn = document.getElementById('postBtn');
  btn.disabled=true; btn.textContent='Posting...';
  const fd = new FormData();
  fd.append('content', text||'');
  if (attachedFile) fd.append('file', attachedFile);
  const r = await fetch('/api/posts',{method:'POST',body:fd});
  const d = await r.json();
  btn.disabled=false; btn.textContent='Post';
  if (d.error) { showToast('Error: '+d.error, true); return; }
  document.getElementById('postText').value='';
  clearAttach(); showToast('Post shared! ✓'); loadFeed();
}
function handleAttach(input) {
  if (!input.files[0]) return;
  attachedFile = input.files[0];
  document.getElementById('attachName').textContent = attachedFile.name;
  document.getElementById('attachPreview').classList.add('show');
}
function clearAttach() {
  attachedFile = null;
  document.getElementById('attachFile').value='';
  document.getElementById('attachPreview').classList.remove('show');
}
function addEmoji() {
  const e = ['🎉','🚀','💡','🔥','✅','👏','💼','📊','⚡','🌟'];
  document.getElementById('postText').value += ' '+e[Math.floor(Math.random()*e.length)];
}
async function deletePost(postId) {
  if (!confirm('Delete this post?')) return;
  const r = await fetch('/api/posts/'+postId,{method:'DELETE'});
  const d = await r.json();
  if (d.error) { showToast('Error: '+d.error, true); return; }
  showToast('Post deleted'); loadFeed();
}
function editPost(postId, currentContent) {
  const el = document.getElementById('post-content-'+postId);
  el.innerHTML = `<textarea id="edit-input-${postId}" style="width:100%;background:var(--ink3);border:1px solid var(--sky);border-radius:8px;padding:10px;color:var(--text);font-size:.86rem;resize:none;outline:none;min-height:78px;line-height:1.6">${currentContent}</textarea>
    <div style="display:flex;gap:7px;margin-top:7px">
      <button onclick="saveEdit(${postId})" style="padding:6px 14px;background:var(--sky);border:none;border-radius:7px;color:#fff;font-size:.77rem;font-weight:800;cursor:pointer">Save</button>
      <button onclick="cancelEdit(${postId},'${escAttr(currentContent)}')" style="padding:6px 14px;background:var(--ink3);border:1px solid var(--line);border-radius:7px;color:var(--muted);font-size:.77rem;font-weight:800;cursor:pointer">Cancel</button>
    </div>`;
}
async function saveEdit(postId) {
  const val = document.getElementById('edit-input-'+postId).value.trim();
  if (!val) return;
  const r = await fetch('/api/posts/'+postId,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:val})});
  const d = await r.json();
  if (d.error) { showToast('Error: '+d.error, true); return; }
  showToast('Post updated! ✓'); loadFeed();
}
function cancelEdit(postId, orig) {
  document.getElementById('post-content-'+postId).innerHTML = escHtml(orig);
}
function sharePost(postId) {
  const url = window.location.origin+'#post-'+postId;
  navigator.clipboard.writeText(url).then(()=>showToast('Link copied! ↗')).catch(()=>showToast('Share: '+url));
}
async function loadProfilePosts() {
  const r = await fetch('/api/posts?mine=1');
  const posts = await r.json();
  const c = document.getElementById('profilePosts');
  if (!posts.length) { c.innerHTML='<div class="empty-state"><span class="icon">✦</span><h3>No posts yet</h3><p>Share your first update!</p></div>'; return; }
  c.innerHTML = posts.map(renderPost).join('');
}

// ── ANALYTICS ──
async function loadAnalytics() {
  const [postsR, usersR, notifR] = await Promise.all([fetch('/api/posts'), fetch('/api/users'), fetch('/api/notifications')]);
  const posts = await postsR.json();
  const users = await usersR.json();
  const notifs = await notifR.json();
  const myPosts = posts.filter(p=>p.user_id===ME.id);
  const totalLikes = myPosts.reduce((s,p)=>s+JSON.parse(p.likes||'[]').length,0);
  const skills = JSON.parse(ME.skills||'[]');
  const weeks = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const barData = weeks.map(()=>Math.floor(Math.random()*90)+10);
  const maxBar = Math.max(...barData);
  const skillsData = skills.slice(0,6).map(s=>({name:s,count:Math.floor(Math.random()*50)+10}));
  const maxSkill = skillsData.length ? Math.max(...skillsData.map(s=>s.count)) : 1;
  const activities = [
    {dot:'var(--sky)',text:'You posted an update',time:'2h ago'},
    {dot:'var(--mint)',text:'Someone liked your post',time:'4h ago'},
    {dot:'var(--violet)',text:'New connection request',time:'1d ago'},
    {dot:'var(--amber)',text:'Profile viewed 3 times',time:'2d ago'},
    {dot:'var(--sky)',text:'Resume analyzed with AI',time:'3d ago'},
  ];
  document.getElementById('analyticsContent').innerHTML = `
    <div class="section-header"><div class="section-title">Analytics Dashboard</div><div style="font-size:.78rem;color:var(--muted);font-weight:700">Last 30 days</div></div>
    <div class="analytics-grid">
      <div class="metric-card"><div class="metric-val" style="color:var(--sky)">${myPosts.length}</div><div class="metric-label">Posts</div><div class="metric-delta up">↑ ${Math.floor(Math.random()*4)+1} this week</div></div>
      <div class="metric-card"><div class="metric-val" style="color:var(--mint)">${totalLikes}</div><div class="metric-label">Total Likes</div><div class="metric-delta up">↑ ${Math.floor(Math.random()*8)+2} new</div></div>
      <div class="metric-card"><div class="metric-val" style="color:var(--violet)">${users.length-1}</div><div class="metric-label">Network</div><div class="metric-delta up">↑ ${Math.floor(Math.random()*3)+1} this month</div></div>
      <div class="metric-card"><div class="metric-val" style="color:var(--amber)">${skills.length}</div><div class="metric-label">Skills</div><div class="metric-delta ${skills.length>3?'up':'down'}">${skills.length>3?'↑ Strong':'↑ Add more'}</div></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px">
      <div class="chart-card">
        <div class="chart-title">Activity this week</div>
        <div class="bar-chart">
          ${barData.map((v,i)=>`<div class="bar-col"><div class="bar-fill" style="height:${(v/maxBar)*90}px"></div><div class="bar-label">${weeks[i]}</div></div>`).join('')}
        </div>
      </div>
      <div class="chart-card">
        <div class="chart-title">Recent activity</div>
        <ul class="activity-list">
          ${activities.map(a=>`<li class="activity-item"><div class="activity-dot" style="background:${a.dot}"></div><div class="activity-text">${a.text}</div><div class="activity-time">${a.time}</div></li>`).join('')}
        </ul>
      </div>
    </div>
    ${skillsData.length ? `<div class="chart-card">
      <div class="chart-title">Skills profile</div>
      <div class="skills-bar-list">
        ${skillsData.map(s=>`<div class="skill-bar-item">
          <div class="skill-bar-header"><span class="skill-name">${s.name}</span><span class="skill-cnt">${s.count} posts</span></div>
          <div class="skill-bar-track"><div class="skill-bar-fill" style="width:${(s.count/maxSkill)*100}%"></div></div>
        </div>`).join('')}
      </div>
    </div>` : ''}
  `;
}

// ── JOBS ──
let currentJobFilter = 'all';
async function loadAppliedJobs() {
  const r = await fetch('/api/jobs/applied');
  const applied = await r.json();
  appliedJobs = new Set(applied.map(a=>a.job_id));
}
async function loadJobs() {
  await loadAppliedJobs();
  renderJobs(currentJobFilter);
  const cnt = appliedJobs.size;
  document.getElementById('appliedCount').textContent = cnt ? `${cnt} applied` : '';
}
function filterJobs(filter, btn) {
  currentJobFilter = filter;
  document.querySelectorAll('.jf-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  renderJobs(filter);
}
function renderJobs(filter) {
  const jobsDB = JSON.parse(document.getElementById('jobsDBData').textContent);
  let jobs = jobsDB;
  if (filter !== 'all') {
    jobs = jobs.filter(j=>j.location.includes(filter)||j.type.includes(filter));
  }
  const container = document.getElementById('jobsList');
  if (!jobs.length) { container.innerHTML='<div class="empty-state"><span class="icon">💼</span><h3>No jobs match</h3><p>Try a different filter</p></div>'; return; }
  container.innerHTML = jobs.map(j => {
    const applied = appliedJobs.has(j.id);
    return `<div class="job-board-card">
      <div class="jb-header">
        <div class="jb-title-wrap">
          <div class="jb-title">${j.title}</div>
          <div class="jb-company">${j.company}</div>
        </div>
      </div>
      <div class="jb-meta">
        <span class="jb-badge jb-location">📍 ${j.location}</span>
        <span class="jb-badge jb-type">${j.type}</span>
        <span class="jb-badge jb-salary">💰 ${j.salary}</span>
      </div>
      <div class="jb-desc">${j.description}</div>
      <div class="jb-skills">${j.skills.map(s=>`<span class="jb-skill">${s}</span>`).join('')}</div>
      <div class="jb-footer">
        ${applied
          ? `<div class="applied-tag">✓ Applied</div>`
          : `<button class="apply-btn" onclick="applyJob(${j.id},'${j.title}','${j.company}',this)">Apply now →</button>`
        }
      </div>
    </div>`;
  }).join('');
}
async function applyJob(jobId, title, company, btn) {
  btn.disabled=true; btn.textContent='Applying...';
  const r = await fetch('/api/jobs/apply', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:jobId,job_title:title,company})});
  const d = await r.json();
  if (d.error) { showToast('Error: '+d.error, true); btn.disabled=false; btn.textContent='Apply now →'; return; }
  appliedJobs.add(jobId);
  showToast('Application submitted! 🎉');
  document.getElementById('appliedCount').textContent = `${appliedJobs.size} applied`;
  btn.parentElement.innerHTML = '<div class="applied-tag">✓ Applied</div>';
}

// ── NOTIFICATIONS ──
async function loadNotifBadge() {
  const r = await fetch('/api/notifications');
  const notifs = await r.json();
  const unread = notifs.filter(n=>!n.read).length;
  const badge = document.getElementById('notifBadge');
  badge.style.display = unread > 0 ? 'block' : 'none';
}
async function loadNotifications() {
  const r = await fetch('/api/notifications');
  const notifs = await r.json();
  document.getElementById('notifBadge').style.display='none';
  const container = document.getElementById('notifList');
  if (!notifs.length) {
    container.innerHTML='<div class="empty-state"><span class="icon">🔔</span><h3>No notifications</h3><p>You\'re all caught up!</p></div>';
    return;
  }
  const typeIcon = {like:'👍',comment:'💬',connection:'🤝',job:'💼',default:'🔔'};
  const typeClass = {like:'like',comment:'comment',connection:'connection',job:'job'};
  container.innerHTML = notifs.map(n=>`
    <div class="notif-item ${n.read?'':'unread'}">
      <div class="notif-icon ${typeClass[n.type]||'like'}">${typeIcon[n.type]||typeIcon.default}</div>
      <div class="notif-body">
        <div class="notif-msg">${n.message}</div>
        <div class="notif-time">${timeAgo(n.created_at)}</div>
      </div>
      ${!n.read?'<div class="unread-dot"></div>':''}
    </div>`).join('');
}
async function markAllRead() {
  await fetch('/api/notifications/read', {method:'POST'});
  loadNotifications();
}

// ── MESSAGES ──
async function loadMsgBadge() {
  const r = await fetch('/api/messages/unread_count');
  const d = await r.json();
  document.getElementById('msgBadge').style.display = d.count>0?'block':'none';
}
async function loadDMConvos() {
  await loadUsers();
  const r = await fetch('/api/messages/convos');
  const convos = await r.json();
  const container = document.getElementById('dmConvoList');
  const others = allUsers.filter(u=>u.id!==ME.id);
  if (!others.length) { container.innerHTML='<div style="padding:14px;color:var(--muted);font-size:.82rem;font-weight:600">No users yet</div>'; return; }
  container.innerHTML = others.map(u => {
    const convo = convos.find(c=>c.other_user_id===u.id);
    const ini = u.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
    return `<div class="dm-convo-item ${currentDMUser&&currentDMUser.id===u.id?'active':''}" onclick="openDMChat(${JSON.stringify(u).replace(/"/g,'&quot;')})">
      <div class="avatar" style="width:36px;height:36px;font-size:.78rem">${ini}</div>
      <div class="dm-convo-info">
        <div class="dm-convo-name">${u.name}</div>
        <div class="dm-convo-preview">${convo?convo.last_message:'Start a conversation...'}</div>
      </div>
      ${convo?`<div class="dm-convo-time">${timeAgo(convo.last_time)}</div>`:''}
    </div>`;
  }).join('');
}
async function openDMChat(user) {
  currentDMUser = user;
  const ini = user.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
  document.getElementById('dmChatArea').innerHTML = `
    <div class="dm-chat">
      <div class="dm-chat-header">
        <div class="avatar" style="width:36px;height:36px;font-size:.78rem">${ini}</div>
        <div><div class="dm-chat-name">${user.name}</div><div class="dm-chat-status">● Active</div></div>
        <button onclick="startAIChat()" style="margin-left:auto;padding:6px 13px;border-radius:8px;border:1px solid var(--line2);background:transparent;color:var(--muted);font-size:.74rem;font-weight:700;cursor:pointer;transition:all .2s">✦ AI help</button>
      </div>
      <div class="dm-messages" id="dmMessages"></div>
      <div class="dm-input-area">
        <input class="dm-input" type="text" id="dmInput" placeholder="Write a message..." onkeydown="if(event.key==='Enter')sendDM()"/>
        <button class="dm-send-btn" onclick="sendDM()">Send</button>
      </div>
    </div>`;
  loadDMMessages(user.id);
  document.querySelectorAll('.dm-convo-item').forEach(i=>i.classList.remove('active'));
}
async function loadDMMessages(userId) {
  const r = await fetch('/api/messages/'+userId);
  const msgs = await r.json();
  const container = document.getElementById('dmMessages');
  if (!msgs.length) { container.innerHTML='<div style="color:var(--muted);font-size:.82rem;font-weight:600;text-align:center;margin-top:20px">No messages yet. Say hello!</div>'; return; }
  container.innerHTML = msgs.map(m => {
    const mine = m.from_user===ME.id;
    const ini = mine ? (ME.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2)) : (currentDMUser.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2));
    return `<div class="dm-msg ${mine?'mine':''}">
      <div class="avatar" style="width:28px;height:28px;font-size:.65rem">${ini}</div>
      <div>
        <div class="dm-bubble">${escHtml(m.content)}</div>
        <div class="dm-msg-time">${timeAgo(m.created_at)}</div>
      </div>
    </div>`;
  }).join('');
  container.scrollTop = container.scrollHeight;
}
async function sendDM() {
  if (!currentDMUser) return;
  const input = document.getElementById('dmInput');
  const val = input.value.trim();
  if (!val) return;
  input.value = '';
  const r = await fetch('/api/messages', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({to_user:currentDMUser.id,content:val})});
  const d = await r.json();
  if (d.error) { showToast('Error: '+d.error, true); return; }
  await loadDMMessages(currentDMUser.id);
  loadDMConvos();
}
function openNewDM() {
  const others = allUsers.filter(u=>u.id!==ME.id);
  document.getElementById('dmUserList').innerHTML = others.map(u=>{
    const ini = u.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
    return `<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;cursor:pointer;transition:background .15s" onmouseover="this.style.background='var(--ink3)'" onmouseout="this.style.background=''" onclick="closeModal('newDMModal');openDMChat(${JSON.stringify(u).replace(/"/g,'&quot;')})">
      <div class="avatar" style="width:36px;height:36px;font-size:.78rem">${ini}</div>
      <div><div style="font-size:.84rem;font-weight:800">${u.name}</div><div style="font-size:.74rem;color:var(--muted)">${u.headline||'gathR member'}</div></div>
    </div>`;
  }).join('') || '<div style="color:var(--muted);font-size:.83rem;padding:10px">No other users yet</div>';
  openModal('newDMModal');
}
function startAIChat() { closeModal('newDMModal'); showPage('ai'); }

// ── AI CHAT ──
async function sendAIMessage() {
  const input = document.getElementById('aiInput');
  const msg = input.value.trim();
  if (!msg) return;
  input.value='';
  document.getElementById('aiSuggestions').style.display='none';
  appendAIMsg('user', msg);
  const btn = document.getElementById('aiSendBtn');
  btn.disabled=true;
  appendTyping();
  aiChatHistory.push({role:'user',content:msg});
  try {
    const r = await fetch('/api/ai/chat', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:aiChatHistory,user:{name:ME.name,headline:ME.headline,skills:ME.skills,about:ME.about}})});
    const d = await r.json();
    removeTyping();
    if (d.error) { appendAIMsg('assistant','Sorry, I ran into an error. Please try again.'); }
    else {
      appendAIMsg('assistant', d.reply);
      aiChatHistory.push({role:'assistant',content:d.reply});
    }
  } catch(e) {
    removeTyping();
    appendAIMsg('assistant','Connection error. Please try again.');
  }
  btn.disabled=false;
}
function sendAISuggestion(btn) {
  document.getElementById('aiInput').value = btn.textContent;
  sendAIMessage();
}
function appendAIMsg(role, content) {
  const icon = role==='user' ? (ME.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2)) : '✦';
  const container = document.getElementById('aiMessages');
  const el = document.createElement('div');
  el.className = `ai-msg ${role}`;
  el.innerHTML = `<div class="ai-msg-icon">${icon}</div><div class="ai-bubble">${escHtml(content)}</div>`;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
}
function appendTyping() {
  const container = document.getElementById('aiMessages');
  const el = document.createElement('div');
  el.className='ai-msg assistant'; el.id='typing-indicator';
  el.innerHTML='<div class="ai-msg-icon">✦</div><div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div>';
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
}
function removeTyping() {
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}
async function clearAIChat() {
  await fetch('/api/ai/clear', {method:'POST'});
  aiChatHistory=[];
  document.getElementById('aiMessages').innerHTML=`<div class="ai-msg assistant"><div class="ai-msg-icon">✦</div><div class="ai-bubble">Chat cleared! How can I help you with your career today?</div></div>`;
  document.getElementById('aiSuggestions').style.display='flex';
}
function aiKeydown(e) {
  if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); sendAIMessage(); }
}

// ── NETWORK ──
async function loadSuggestions() {
  const others = allUsers.filter(u=>u.id!==ME.id).slice(0,4);
  document.getElementById('suggestions').innerHTML = others.length ?
    others.map(u=>`<div class="suggest-item">
      <div class="avatar" style="width:28px;height:28px;font-size:.65rem">${u.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2)}</div>
      <div class="suggest-info"><div class="suggest-name">${u.name}</div><div class="suggest-role">${u.headline||'gathR member'}</div></div>
      <button class="s-connect-btn" onclick="sendDMFrom(${u.id})">💬</button>
    </div>`).join('') :
    '<div style="padding:14px;color:var(--muted);font-size:.82rem;font-weight:600">No suggestions yet</div>';
}
async function loadNetwork() {
  const others = allUsers.filter(u=>u.id!==ME.id);
  document.getElementById('networkList').innerHTML = others.length ?
    `<div class="people-grid">${others.map(u=>`
    <div class="people-card">
      <div class="avatar">${u.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2)}</div>
      <div class="people-name">${u.name}</div>
      <div class="people-role">${u.headline||'gathR member'}</div>
      <button class="connect-btn" onclick="sendMessage(${u.id})">💬 Message</button>
    </div>`).join('')}</div>` :
    '<div class="empty-state"><span class="icon">⊕</span><h3>No members yet</h3><p>Invite your colleagues!</p></div>';
}
function sendMessage(userId) { showPage('messages'); setTimeout(()=>{ const u=allUsers.find(u=>u.id===userId); if(u)openDMChat(u); },100); }
function sendDMFrom(userId) { const u=allUsers.find(u=>u.id===userId); if(u){ showPage('messages'); setTimeout(()=>openDMChat(u),100); } }

// ── PROFILE ──
let avatarBase64 = null;
function previewAvatar(input) {
  if (!input.files[0]) return;
  const reader = new FileReader();
  reader.onload = e => {
    avatarBase64 = e.target.result;
    const prev = document.getElementById('ep_avatarPreview');
    prev.innerHTML = `<img src="${avatarBase64}" style="width:100%;height:100%;object-fit:cover;border-radius:50%">`;
    updateAllAvatars(avatarBase64);
  };
  reader.readAsDataURL(input.files[0]);
}
function updateAllAvatars(src) {
  ['navAvatar','sideAvatar','compAvatar','profAvatar'].forEach(id=>{
    const el=document.getElementById(id);
    if(el) el.innerHTML=`<img src="${src}" style="width:100%;height:100%;object-fit:cover;border-radius:50%">`;
  });
}
function openEditProfile() {
  if (!ME) return;
  avatarBase64 = ME.avatar||null;
  document.getElementById('ep_name').value = ME.name||'';
  document.getElementById('ep_headline').value = ME.headline||'';
  document.getElementById('ep_location').value = ME.location||'';
  document.getElementById('ep_about').value = ME.about||'';
  document.getElementById('ep_skills').value = JSON.parse(ME.skills||'[]').join(', ');
  const prev = document.getElementById('ep_avatarPreview');
  const ini = ME.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
  prev.innerHTML = ME.avatar ? `<img src="${ME.avatar}" style="width:100%;height:100%;object-fit:cover;border-radius:50%">` : ini;
  openModal('editProfileModal');
}
async function saveProfile() {
  const skills = document.getElementById('ep_skills').value.split(',').map(s=>s.trim()).filter(Boolean);
  const data = {
    name:document.getElementById('ep_name').value.trim(),
    headline:document.getElementById('ep_headline').value.trim(),
    location:document.getElementById('ep_location').value.trim(),
    about:document.getElementById('ep_about').value.trim(),
    skills:JSON.stringify(skills),
    avatar:avatarBase64||ME.avatar||'',
  };
  const r = await fetch('/api/profile',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const d = await r.json();
  if (d.error) { showToast('Error saving', true); return; }
  ME = {...ME,...data};
  if (ME.avatar) updateAllAvatars(ME.avatar);
  refreshUserUI(); closeModal('editProfileModal'); showToast('Profile updated! ✓');
}

// ── RESUME ──
const rdz = document.getElementById('resumeDropZone');
if (rdz) {
  rdz.addEventListener('dragover',e=>{e.preventDefault();rdz.classList.add('drag')});
  rdz.addEventListener('dragleave',()=>rdz.classList.remove('drag'));
  rdz.addEventListener('drop',e=>{e.preventDefault();rdz.classList.remove('drag');const f=e.dataTransfer.files[0];if(f)processResume(f)});
}
function handleResumeUpload(input){if(input.files[0])processResume(input.files[0])}
async function processResume(file) {
  document.getElementById('resumeLoading').style.display='block';
  document.getElementById('analysisResult').classList.remove('show');
  const steps=['Extracting text...','Analyzing skills...','Scoring ATS...','Finding job matches...','Building roadmap...'];
  let si=0;
  const iv=setInterval(()=>{document.getElementById('resumeLoadText').textContent=steps[si%steps.length];si++;},1200);
  const fd=new FormData(); fd.append('resume',file);
  try {
    const r=await fetch('/api/analyze_resume',{method:'POST',body:fd});
    const d=await r.json();
    clearInterval(iv); document.getElementById('resumeLoading').style.display='none';
    if(d.error){showToast('Analysis failed: '+d.error,true);return}
    renderAnalysis(d);
    if(d.skills){ME.skills=JSON.stringify(d.skills);refreshUserUI();}
  } catch(e) { clearInterval(iv); document.getElementById('resumeLoading').style.display='none'; showToast('Something went wrong.',true); }
}
function renderAnalysis(d) {
  const ats=d.ats||{}; const gap=d.gap||{}; const jobs=d.jobs||[];
  let html=`
  <div class="ai-card"><h4>✦ AI Profile Summary</h4><p>${d.ai_summary||'Analysis complete.'}</p></div>
  <div class="score-grid">
    <div class="score-box"><div class="val" style="color:var(--sky)">${d.profile_score||0}<span style="font-size:.7em">%</span></div><div class="lbl">Profile</div></div>
    <div class="score-box"><div class="val" style="color:#a78bfa">${ats.overall||0}<span style="font-size:.7em">%</span></div><div class="lbl">ATS</div></div>
    <div class="score-box"><div class="val" style="color:var(--mint)">${ats.keywords||0}<span style="font-size:.7em">%</span></div><div class="lbl">Keywords</div></div>
    <div class="score-box"><div class="val" style="color:var(--amber)">${ats.readability||0}<span style="font-size:.7em">%</span></div><div class="lbl">Readability</div></div>
  </div>`;
  if(d.skills&&d.skills.length){html+=`<div style="margin-bottom:14px"><div style="font-size:.67rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;font-weight:800;margin-bottom:7px">Detected Skills</div><div style="display:flex;flex-wrap:wrap;gap:5px">${d.skills.map(s=>`<span class="tag tag-blue">${s}</span>`).join('')}</div></div>`;}
  if(jobs.length){
    html+=`<div style="margin-bottom:14px"><div style="font-size:.88rem;font-weight:900;letter-spacing:-.03em;margin-bottom:9px">🎯 Top Job Matches</div>`;
    jobs.slice(0,5).forEach(j=>{html+=`<div class="job-card-r"><div class="job-info"><div class="job-title">${j.title}</div><div class="job-co">${j.company} · ${j.type}</div></div><div class="job-bar"><div class="job-fill" style="width:0" data-w="${j.match_pct}%"></div></div><div class="job-pct">${j.match_pct}%</div></div>`;});
    html+=`</div>`;
  }
  if(gap.missing_skills&&gap.missing_skills.length){html+=`<div class="ai-card warn"><h4>📈 Skills to Develop</h4><p style="margin-bottom:9px">${gap.overview||''}</p><div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:7px">${gap.missing_skills.map(s=>`<span class="tag tag-purple">${s}</span>`).join('')}</div></div>`;}
  if(ats.suggestions&&ats.suggestions.length){ats.suggestions.forEach(s=>{html+=`<div class="ai-card warn"><h4>${s.title}</h4><p>${s.detail}</p></div>`;});}
  const result=document.getElementById('analysisResult');
  result.innerHTML=html; result.classList.add('show');
  setTimeout(()=>document.querySelectorAll('.job-fill').forEach(b=>b.style.width=b.dataset.w),150);
}
function loadSavedAnalysis() {
  if(ME&&ME.resume_analysis&&ME.resume_analysis!=='{}'){
    try{const d=JSON.parse(ME.resume_analysis);if(d&&d.ai_summary)renderAnalysis(d);}catch(e){}
  }
}

// ── UTILS ──
function openModal(id){document.getElementById(id).classList.add('show')}
function closeModal(id){document.getElementById(id).classList.remove('show')}
document.querySelectorAll('.modal-bg').forEach(m=>m.addEventListener('click',e=>{if(e.target===m)m.classList.remove('show')}));
function setText(id,val){const el=document.getElementById(id);if(el)el.textContent=val}
function escHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>')}
function escAttr(s){return String(s).replace(/'/g,"\\'").replace(/\n/g,' ')}
function timeAgo(ts){
  const d=new Date(ts+'Z'),n=new Date(),diff=(n-d)/1000;
  if(diff<60)return'Just now';
  if(diff<3600)return Math.floor(diff/60)+'m ago';
  if(diff<86400)return Math.floor(diff/3600)+'h ago';
  return Math.floor(diff/86400)+'d ago';
}
function showToast(msg, isErr=false) {
  const el=document.getElementById('toast');
  el.textContent=msg; el.className='toast'+(isErr?' err':'');
  el.classList.add('show');
  setTimeout(()=>el.classList.remove('show'),2800);
}
</script>

<!-- Jobs DB injected server-side -->
<script id="jobsDBData" type="application/json">{{ jobs_db_json }}</script>

</body>
</html>"""


# ═══════════════════════════════════════════
#  API ROUTES
# ═══════════════════════════════════════════

@app.route("/")
def index():
    import json as _json
    jobs_json = _json.dumps(JOBS_DB)
    return render_template_string(HTML.replace('{{ jobs_db_json }}', jobs_json))

# ── AUTH ──
@app.route("/api/register", methods=["POST"])
def register():
    d = request.json
    name = (d.get("name") or "").strip()
    email = (d.get("email") or "").strip().lower()
    password = d.get("password") or ""
    headline = (d.get("headline") or "").strip()
    if not name or not email or not password:
        return jsonify({"error": "All fields required"}), 400
    db = get_db()
    if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
        return jsonify({"error": "Email already registered"}), 400
    hashed = generate_password_hash(password)
    db.execute("INSERT INTO users (name,email,password,headline) VALUES (?,?,?,?)", (name, email, hashed, headline))
    db.commit()
    user = row_to_dict(db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone())
    session["user_id"] = user["id"]
    return jsonify({"user": user})

@app.route("/api/login", methods=["POST"])
def login():
    d = request.json
    email = (d.get("email") or "").strip().lower()
    password = d.get("password") or ""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid email or password"}), 401
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"user": row_to_dict(user)})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/me")
def me():
    u = current_user()
    if not u: return jsonify({"error": "Not authenticated"}), 401
    return jsonify(row_to_dict(u))

# ── PROFILE ──
@app.route("/api/profile", methods=["PUT"])
@login_required
def update_profile():
    d = request.json
    db = get_db()
    db.execute("UPDATE users SET name=?,headline=?,location=?,about=?,skills=?,avatar=? WHERE id=?",
               (d.get("name"), d.get("headline"), d.get("location"), d.get("about"), d.get("skills"), d.get("avatar",""), session["user_id"]))
    db.commit()
    user = row_to_dict(db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone())
    return jsonify(user)

# ── POSTS ──
@app.route("/api/posts", methods=["GET"])
@login_required
def get_posts():
    mine = request.args.get("mine")
    db = get_db()
    if mine:
        rows = db.execute("""SELECT p.*,u.name as author_name,u.headline as author_headline
            FROM posts p JOIN users u ON p.user_id=u.id
            WHERE p.user_id=? ORDER BY p.created_at DESC""", (session["user_id"],)).fetchall()
    else:
        rows = db.execute("""SELECT p.*,u.name as author_name,u.headline as author_headline
            FROM posts p JOIN users u ON p.user_id=u.id
            ORDER BY p.created_at DESC LIMIT 50""").fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/posts", methods=["POST"])
@login_required
def create_post():
    content = request.form.get("content","").strip()
    file = request.files.get("file")
    file_url = file_name = file_type = ""
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit(".",1)[1].lower()
        fname = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(UPLOAD_FOLDER, fname))
        file_url = fname; file_name = secure_filename(file.filename); file_type = ext
    if not content and not file_url:
        return jsonify({"error": "Post cannot be empty"}), 400
    db = get_db()
    db.execute("INSERT INTO posts (user_id,content,file_url,file_name,file_type) VALUES (?,?,?,?,?)",
               (session["user_id"], content, file_url, file_name, file_type))
    db.commit()
    # notify all users (simplified broadcast)
    user = current_user()
    all_users = db.execute("SELECT id FROM users WHERE id!=?", (session["user_id"],)).fetchall()
    for u in all_users:
        add_notification(u["id"], "like", f"{user['name']} shared a new post")
    return jsonify({"ok": True})

@app.route("/api/posts/<int:post_id>", methods=["PUT"])
@login_required
def edit_post(post_id):
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post: return jsonify({"error": "Not found"}), 404
    if post["user_id"] != session["user_id"]: return jsonify({"error": "Unauthorized"}), 403
    content = (request.json.get("content") or "").strip()
    if not content: return jsonify({"error": "Content cannot be empty"}), 400
    db.execute("UPDATE posts SET content=? WHERE id=?", (content, post_id))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/posts/<int:post_id>", methods=["DELETE"])
@login_required
def delete_post(post_id):
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post: return jsonify({"error": "Not found"}), 404
    if post["user_id"] != session["user_id"]: return jsonify({"error": "Unauthorized"}), 403
    db.execute("DELETE FROM posts WHERE id=?", (post_id,))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/posts/<int:post_id>/like", methods=["POST"])
@login_required
def like_post(post_id):
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post: return jsonify({"error": "Not found"}), 404
    likes = json.loads(post["likes"] or "[]")
    uid = session["user_id"]
    if uid in likes:
        likes.remove(uid)
    else:
        likes.append(uid)
        if post["user_id"] != uid:
            user = current_user()
            add_notification(post["user_id"], "like", f"{user['name']} liked your post")
    db.execute("UPDATE posts SET likes=? WHERE id=?", (json.dumps(likes), post_id))
    db.commit()
    return jsonify({"ok": True})

# ── COMMENTS ──
@app.route("/api/posts/<int:post_id>/comments", methods=["GET"])
@login_required
def get_comments(post_id):
    db = get_db()
    rows = db.execute("""SELECT c.*,u.name as author_name FROM comments c
        JOIN users u ON c.user_id=u.id WHERE c.post_id=? ORDER BY c.created_at ASC""", (post_id,)).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/posts/<int:post_id>/comments", methods=["POST"])
@login_required
def add_comment(post_id):
    content = (request.json.get("content") or "").strip()
    if not content: return jsonify({"error": "Comment cannot be empty"}), 400
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post: return jsonify({"error": "Post not found"}), 404
    db.execute("INSERT INTO comments (post_id,user_id,content) VALUES (?,?,?)",
               (post_id, session["user_id"], content))
    db.commit()
    if post["user_id"] != session["user_id"]:
        user = current_user()
        add_notification(post["user_id"], "comment", f"{user['name']} commented on your post")
    return jsonify({"ok": True})

# ── USERS ──
@app.route("/api/users")
@login_required
def get_users():
    db = get_db()
    rows = db.execute("SELECT id,name,email,headline,location,skills FROM users").fetchall()
    return jsonify([row_to_dict(r) for r in rows])

# ── NOTIFICATIONS ──
@app.route("/api/notifications")
@login_required
def get_notifications():
    db = get_db()
    rows = db.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
                      (session["user_id"],)).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/notifications/read", methods=["POST"])
@login_required
def mark_notifications_read():
    db = get_db()
    db.execute("UPDATE notifications SET read=1 WHERE user_id=?", (session["user_id"],))
    db.commit()
    return jsonify({"ok": True})

# ── MESSAGES ──
@app.route("/api/messages", methods=["POST"])
@login_required
def send_message():
    d = request.json
    to_user = d.get("to_user")
    content = (d.get("content") or "").strip()
    if not to_user or not content: return jsonify({"error": "Invalid request"}), 400
    db = get_db()
    db.execute("INSERT INTO messages (from_user,to_user,content) VALUES (?,?,?)",
               (session["user_id"], to_user, content))
    db.commit()
    user = current_user()
    add_notification(to_user, "connection", f"{user['name']} sent you a message")
    return jsonify({"ok": True})

@app.route("/api/messages/<int:other_user_id>")
@login_required
def get_messages(other_user_id):
    db = get_db()
    uid = session["user_id"]
    rows = db.execute("""SELECT * FROM messages WHERE
        (from_user=? AND to_user=?) OR (from_user=? AND to_user=?)
        ORDER BY created_at ASC""", (uid, other_user_id, other_user_id, uid)).fetchall()
    db.execute("UPDATE messages SET read=1 WHERE to_user=? AND from_user=?", (uid, other_user_id))
    db.commit()
    return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/messages/convos")
@login_required
def get_convos():
    db = get_db()
    uid = session["user_id"]
    rows = db.execute("""SELECT
        CASE WHEN from_user=? THEN to_user ELSE from_user END as other_user_id,
        content as last_message, created_at as last_time
        FROM messages WHERE from_user=? OR to_user=?
        ORDER BY created_at DESC""", (uid, uid, uid)).fetchall()
    seen = set()
    convos = []
    for r in rows:
        r = row_to_dict(r)
        if r["other_user_id"] not in seen:
            seen.add(r["other_user_id"])
            convos.append(r)
    return jsonify(convos)

@app.route("/api/messages/unread_count")
@login_required
def unread_count():
    db = get_db()
    row = db.execute("SELECT COUNT(*) as cnt FROM messages WHERE to_user=? AND read=0", (session["user_id"],)).fetchone()
    return jsonify({"count": row["cnt"]})

# ── JOBS ──
@app.route("/api/jobs/apply", methods=["POST"])
@login_required
def apply_job():
    d = request.json
    job_id = d.get("job_id")
    job_title = d.get("job_title","")
    company = d.get("company","")
    db = get_db()
    existing = db.execute("SELECT id FROM job_applications WHERE user_id=? AND job_id=?",
                          (session["user_id"], job_id)).fetchone()
    if existing: return jsonify({"error": "Already applied"}), 400
    db.execute("INSERT INTO job_applications (user_id,job_id,job_title,company) VALUES (?,?,?,?)",
               (session["user_id"], job_id, job_title, company))
    db.commit()
    add_notification(session["user_id"], "job", f"Application submitted for {job_title} at {company}")
    return jsonify({"ok": True})

@app.route("/api/jobs/applied")
@login_required
def get_applied():
    db = get_db()
    rows = db.execute("SELECT * FROM job_applications WHERE user_id=? ORDER BY created_at DESC",
                      (session["user_id"],)).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

# ── AI CHAT ──
@app.route("/api/ai/chat", methods=["POST"])
@login_required
def ai_chat():
    d = request.json
    messages = d.get("messages", [])
    user_ctx = d.get("user", {})
    system = f"""You are gathR AI, a friendly professional career coach and networking assistant for the gathR professional network platform.

User context:
- Name: {user_ctx.get('name','Unknown')}
- Headline: {user_ctx.get('headline','')}
- Skills: {user_ctx.get('skills','[]')}
- About: {user_ctx.get('about','')}

You help with:
- Career advice, job search strategies, and interview preparation
- Resume improvement tips (ATS optimization, formatting, content)
- Networking and relationship building strategies
- LinkedIn and professional profile optimization
- Salary negotiation and career transitions
- Technical skill development recommendations
- Writing professional messages, emails, and outreach

Be concise, actionable, and encouraging. Use the user's context to personalize advice.
Keep responses under 200 words unless complex technical advice is needed."""

    safe_messages = [{"role": m["role"], "content": str(m["content"])[:2000]} for m in messages[-10:]]
    try:
        msg = ai_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            system=system,
            messages=safe_messages
        )
        reply = msg.content[0].text
        # Save to DB
        db = get_db()
        if messages:
            last = messages[-1]
            db.execute("INSERT INTO ai_chat (user_id,role,content) VALUES (?,?,?)",
                       (session["user_id"], last["role"], last["content"][:2000]))
        db.execute("INSERT INTO ai_chat (user_id,role,content) VALUES (?,?,?)",
                   (session["user_id"], "assistant", reply[:2000]))
        db.commit()
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ai/clear", methods=["POST"])
@login_required
def clear_ai_chat():
    db = get_db()
    db.execute("DELETE FROM ai_chat WHERE user_id=?", (session["user_id"],))
    db.commit()
    return jsonify({"ok": True})

# ── CONNECTIONS ──
@app.route("/api/connect", methods=["POST"])
@login_required
def connect_user():
    to = request.json.get("to_user")
    db = get_db()
    existing = db.execute("SELECT id FROM connections WHERE from_user=? AND to_user=?",
                          (session["user_id"], to)).fetchone()
    if not existing:
        db.execute("INSERT INTO connections (from_user,to_user) VALUES (?,?)", (session["user_id"], to))
        db.commit()
        user = current_user()
        add_notification(to, "connection", f"{user['name']} wants to connect with you")
    return jsonify({"ok": True})

# ── RESUME AI ──
@app.route("/api/analyze_resume", methods=["POST"])
@login_required
def analyze_resume():
    if "resume" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["resume"]
    fb = file.read()
    fname = file.filename.lower()
    if fname.endswith(".pdf"):
        resume_text = extract_pdf(fb)
    elif fname.endswith(".txt"):
        resume_text = fb.decode("utf-8", errors="ignore")
        resume_text = unicodedata.normalize("NFKD", resume_text)
        resume_text = "".join(c if (32<=ord(c)<127 or c in "\n\r\t") else " " for c in resume_text)
    else:
        return jsonify({"error": "Upload PDF or TXT only"}), 400
    resume_text = re.sub(r"[ \t]+"," ", resume_text)
    resume_text = re.sub(r"\n{3,}","\n\n", resume_text).strip()
    if not resume_text or len(resume_text)<50:
        return jsonify({"error": "Could not read resume content. Try a text-based PDF."}), 400

    def to_ascii(s):
        s = unicodedata.normalize("NFKD", str(s))
        return s.encode("ascii", errors="ignore").decode("ascii")

    jobs_str = "\n".join(
        f"- {to_ascii(j['title'])} at {to_ascii(j['company'])} | {', '.join(to_ascii(sk) for sk in j['skills'])}"
        for j in JOBS_DB
    )
    safe_resume = to_ascii(resume_text[:3500])
    prompt = f"""Analyze this resume and return ONLY valid JSON with no markdown, no explanation.

RESUME:
{safe_resume}

JOBS:
{jobs_str}

Return exactly this JSON structure:
{{
  "skills": ["skill1","skill2"],
  "profile_score": 75,
  "ai_summary": "2-3 sentence summary",
  "job_matches": [{{"job_title":"exact title","match_pct":80,"matched_skills":["s1"]}}],
  "ats": {{"overall":70,"keywords":65,"formatting":80,"readability":75,"overview":"2 sentences","suggestions":[{{"title":"Issue","detail":"Fix"}}]}},
  "gap": {{"overview":"2 sentences","missing_skills":["s1"],"strong_skills":["s2"],"roadmap":[{{"skill":"Learn X","reason":"Because Y"}}]}}
}}
Only include jobs with match_pct >= 20, sorted descending by match_pct."""

    try:
        msg = ai_client.messages.create(model="claude-sonnet-4-5", max_tokens=2500,
                                         messages=[{"role":"user","content":prompt}])
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```json\s*|^```\s*|```$","", raw, flags=re.MULTILINE).strip()
        ai_data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"AI returned invalid JSON: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500

    matched = []
    for m in ai_data.get("job_matches", []):
        job = next((j for j in JOBS_DB if j["title"]==m["job_title"]), None)
        if job:
            matched.append({**job, "match_pct":m["match_pct"], "matched_skills":m.get("matched_skills",[])})

    result = {
        "resume_text": resume_text[:2000],
        "skills": ai_data.get("skills",[]),
        "profile_score": ai_data.get("profile_score",70),
        "ai_summary": ai_data.get("ai_summary",""),
        "jobs": matched,
        "ats": ai_data.get("ats",{}),
        "gap": ai_data.get("gap",{}),
    }
    db = get_db()
    skills_json = json.dumps(ai_data.get("skills",[]))
    db.execute("UPDATE users SET resume_text=?,resume_analysis=?,skills=? WHERE id=?",
               (resume_text[:3000], json.dumps(result), skills_json, session["user_id"]))
    db.commit()
    return jsonify(result)

# ── STATIC ──
from flask import send_from_directory
@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
           
