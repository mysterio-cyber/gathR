
"""
gathR - Corporate Professional Network
Full-stack Flask app with SQLite auth, AI resume analysis, profile/post feed
"""

import os, io, re, json, uuid, unicodedata
from datetime import datetime
from functools import wraps
import PyPDF2
from flask import (Flask, request, jsonify, render_template_string,session, redirect, url_for, g)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
from anthropic import Anthropic
from dotenv import load_dotenv
load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

app = Flask(__name__)
app.secret_key = "gathR-super-secret-2025"
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024   # 10 MB
app.config["JSON_AS_ASCII"] = False

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DATABASE = "gathr.db"

if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
ai_client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ══════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════
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
        CREATE TABLE IF NOT EXISTS connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        db.commit()

init_db()

# ══════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)
    return decorated

def current_user():
    if "user_id" not in session:
        return None
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
        return sanitize("\n".join(p.extract_text() or "" for p in reader.pages))
    except:
        return ""

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in {
        "pdf", "txt", "png", "jpg", "jpeg", "gif", "doc", "docx"
    }

def row_to_dict(row):
    if row is None: return None
    return dict(row)

# ══════════════════════════════════════════════
#  FRONTEND HTML  — redesigned
# ══════════════════════════════════════════════
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
  --ink:#0a0c0f;
  --ink2:#12161c;
  --ink3:#1a2030;
  --line:#232b38;
  --line2:#2e3a4a;
  --sky:#1a6cff;
  --sky2:#0d4fd4;
  --violet:#7c3aed;
  --mint:#00c48c;
  --amber:#f59e0b;
  --rose:#f43f5e;
  --text:#e4ebf5;
  --muted:#5a6b80;
  --dim:#3a4a5c;
  --card:#0e1420;
  --glass:rgba(255,255,255,0.03);
  --r:14px;
  --r2:20px;
  --shadow:0 4px 24px rgba(0,0,0,0.5);
  --glow:0 0 40px rgba(26,108,255,0.15);
}
html{scroll-behavior:smooth}
body{font-family:'Cabinet Grotesk',sans-serif;background:var(--ink);color:var(--text);min-height:100vh;overflow-x:hidden}
a{color:inherit;text-decoration:none}
button,input,textarea,select{font-family:inherit}
button{cursor:pointer}

/* scrollbar */
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--line2);border-radius:4px}

/* ─── NOISE OVERLAY ─── */
body::before{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");
  opacity:.6;
}

/* ─── AUTH ─── */
.auth-page{
  min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;
  background:radial-gradient(ellipse 80% 60% at 20% 10%, rgba(26,108,255,.12) 0%, transparent 70%),
             radial-gradient(ellipse 60% 50% at 85% 90%, rgba(124,58,237,.1) 0%, transparent 65%),
             var(--ink);
  position:relative;
}
.auth-wordmark{
  position:absolute;top:28px;left:36px;
  font-family:'Cabinet Grotesk',sans-serif;font-size:1.5rem;font-weight:900;
  color:var(--text);letter-spacing:-.04em;
}
.auth-wordmark span{color:var(--sky)}

.auth-card{
  background:var(--card);
  border:1px solid var(--line);
  border-radius:24px;
  padding:44px 40px;
  width:100%;max-width:430px;
  box-shadow:var(--shadow),0 0 60px rgba(26,108,255,.06);
  position:relative;z-index:1;
}
.auth-badge{
  display:inline-flex;align-items:center;gap:6px;
  background:rgba(26,108,255,.1);border:1px solid rgba(26,108,255,.2);
  border-radius:999px;padding:5px 12px;font-size:.72rem;font-weight:700;
  color:var(--sky);text-transform:uppercase;letter-spacing:.06em;margin-bottom:18px;
}
.auth-badge::before{content:'';width:6px;height:6px;background:var(--sky);border-radius:50%}
.auth-h1{
  font-size:2rem;font-weight:900;letter-spacing:-.05em;line-height:1.1;margin-bottom:6px;
  color:var(--text);
}
.auth-h1 em{font-family:'Instrument Serif',serif;font-style:italic;color:var(--sky);font-weight:400}
.auth-sub{color:var(--muted);font-size:.85rem;margin-bottom:28px;line-height:1.5}

.auth-tabs{display:flex;gap:3px;background:var(--ink3);border-radius:10px;padding:3px;margin-bottom:26px}
.auth-tab{
  flex:1;padding:9px;border:none;background:transparent;
  border-radius:8px;color:var(--muted);font-size:.84rem;font-weight:700;
  transition:all .2s;letter-spacing:-.01em;
}
.auth-tab.active{background:var(--ink2);color:var(--text);box-shadow:0 1px 4px rgba(0,0,0,.4)}

.field{margin-bottom:14px}
.field label{
  display:block;font-size:.7rem;font-weight:800;color:var(--muted);
  text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;
}
.field input{
  width:100%;background:var(--ink3);border:1px solid var(--line);
  border-radius:10px;padding:11px 14px;color:var(--text);font-size:.9rem;
  outline:none;transition:all .2s;
}
.field input:focus{border-color:var(--sky);background:rgba(26,108,255,.04);box-shadow:0 0 0 3px rgba(26,108,255,.1)}

.auth-btn{
  width:100%;padding:13px;
  background:var(--sky);
  border:none;border-radius:11px;color:#fff;
  font-size:.95rem;font-weight:800;letter-spacing:-.01em;
  transition:all .2s;position:relative;overflow:hidden;
}
.auth-btn::after{
  content:'';position:absolute;inset:0;
  background:linear-gradient(135deg,rgba(255,255,255,.15) 0%,transparent 60%);
  pointer-events:none;
}
.auth-btn:hover{background:var(--sky2);transform:translateY(-1px);box-shadow:0 8px 28px rgba(26,108,255,.4)}
.auth-btn:active{transform:translateY(0)}
.auth-btn:disabled{opacity:.5;cursor:not-allowed;transform:none;box-shadow:none}

.auth-err{
  background:rgba(244,63,94,.08);border:1px solid rgba(244,63,94,.2);
  color:#fb7185;padding:10px 14px;border-radius:8px;font-size:.83rem;
  margin-bottom:14px;display:none;
}
.auth-err.show{display:block}

/* ─── APP SHELL ─── */
.app{display:none;min-height:100vh}
.app.show{display:block}

/* ─── TOPBAR ─── */
.topbar{
  position:sticky;top:0;z-index:100;
  background:rgba(10,12,15,.88);
  backdrop-filter:blur(20px) saturate(160%);
  border-bottom:1px solid var(--line);
  display:flex;align-items:center;
  padding:0 20px;height:56px;gap:12px;
}
.topbar-logo{
  font-weight:900;font-size:1.35rem;letter-spacing:-.05em;
  color:var(--text);flex-shrink:0;
}
.topbar-logo span{color:var(--sky)}

.topbar-search{flex:1;max-width:300px;position:relative}
.topbar-search input{
  width:100%;background:var(--ink3);border:1px solid var(--line);
  border-radius:8px;padding:7px 12px 7px 34px;
  color:var(--text);font-size:.82rem;outline:none;transition:all .2s;
}
.topbar-search input:focus{border-color:var(--sky);background:rgba(26,108,255,.04)}
.topbar-search .si{
  position:absolute;left:11px;top:50%;transform:translateY(-50%);
  color:var(--muted);font-size:.8rem;pointer-events:none;
}

.topbar-nav{display:flex;gap:2px;margin-left:auto}
.tnav{
  padding:7px 13px;border-radius:8px;border:none;
  background:transparent;color:var(--muted);font-size:.8rem;font-weight:700;
  transition:all .2s;display:flex;align-items:center;gap:5px;letter-spacing:-.01em;
}
.tnav:hover{background:var(--ink3);color:var(--text)}
.tnav.active{background:rgba(26,108,255,.12);color:var(--sky)}

.topbar-user{
  display:flex;align-items:center;gap:9px;cursor:pointer;
  padding:5px 10px;border-radius:9px;transition:background .2s;margin-left:6px;
}
.topbar-user:hover{background:var(--ink3)}

.avatar{
  width:32px;height:32px;border-radius:50%;
  background:linear-gradient(135deg,var(--sky),var(--violet));
  display:flex;align-items:center;justify-content:center;
  font-size:.72rem;font-weight:800;color:#fff;flex-shrink:0;overflow:hidden;
  letter-spacing:-.02em;
}
.avatar img{width:100%;height:100%;object-fit:cover}
.avatar.lg{width:72px;height:72px;font-size:1.5rem}
.avatar.xl{width:100px;height:100px;font-size:2rem;border:3px solid var(--ink)}

.uname{font-size:.82rem;font-weight:700;letter-spacing:-.01em}
.logout-btn{
  background:none;border:none;color:var(--muted);font-size:.78rem;
  cursor:pointer;padding:6px 10px;border-radius:8px;font-weight:600;
  transition:all .2s;
}
.logout-btn:hover{background:rgba(244,63,94,.08);color:var(--rose)}

/* ─── LAYOUT ─── */
.main-layout{
  display:grid;
  grid-template-columns:240px 1fr 270px;
  gap:18px;
  max-width:1160px;margin:0 auto;
  padding:22px 16px;
}
@media(max-width:1024px){
  .main-layout{grid-template-columns:0 1fr 0;padding:14px 10px}
  .sidebar-left,.sidebar-right{display:none}
}

/* ─── SIDEBAR ─── */
.sidebar-left,.sidebar-right{display:flex;flex-direction:column;gap:12px}

.s-card{background:var(--card);border:1px solid var(--line);border-radius:var(--r2);overflow:hidden}
.s-card-hero{
  padding:24px 20px 18px;
  background:linear-gradient(150deg,rgba(26,108,255,.12) 0%,rgba(124,58,237,.08) 100%);
  text-align:center;border-bottom:1px solid var(--line);
  position:relative;overflow:hidden;
}
.s-card-hero::before{
  content:'';position:absolute;top:-20px;right:-20px;
  width:80px;height:80px;background:rgba(26,108,255,.08);border-radius:50%;
}
.s-card-hero .avatar{margin:0 auto 12px;width:56px;height:56px;font-size:1.2rem}
.s-uname{font-weight:800;font-size:.95rem;letter-spacing:-.02em}
.s-headline{color:var(--muted);font-size:.75rem;margin-top:3px;font-weight:500}

.s-stats{display:grid;grid-template-columns:1fr 1fr;border-top:1px solid var(--line)}
.s-stat{padding:14px 10px;text-align:center}
.s-stat+.s-stat{border-left:1px solid var(--line)}
.s-stat .n{font-family:'Geist Mono',monospace;font-size:1.2rem;font-weight:500;color:var(--sky)}
.s-stat .l{font-size:.68rem;color:var(--muted);margin-top:2px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}

.s-links{padding:8px 0}
.s-link{
  display:flex;align-items:center;gap:10px;
  padding:9px 16px;color:var(--muted);font-size:.82rem;font-weight:700;
  transition:all .2s;cursor:pointer;letter-spacing:-.01em;
}
.s-link:hover,.s-link.active{background:var(--ink3);color:var(--text)}
.s-link.active{color:var(--sky)}
.s-link .icon{font-size:.9rem;width:18px;text-align:center}

.s-section{
  background:var(--card);border:1px solid var(--line);
  border-radius:var(--r2);padding:16px;
}
.s-section-title{
  font-size:.68rem;font-weight:800;color:var(--muted);
  text-transform:uppercase;letter-spacing:.1em;margin-bottom:12px;
}

/* ─── FEED ─── */
.feed{display:flex;flex-direction:column;gap:14px}

/* ─── COMPOSER ─── */
.composer{
  background:var(--card);border:1px solid var(--line);
  border-radius:var(--r2);padding:18px;
}
.composer-top{display:flex;gap:12px;align-items:flex-start}
.composer-input{
  flex:1;background:var(--ink3);border:1px solid var(--line);
  border-radius:12px;padding:12px 15px;color:var(--text);font-size:.88rem;
  resize:none;min-height:50px;outline:none;transition:all .25s;line-height:1.6;
}
.composer-input:focus{
  border-color:var(--sky);
  background:rgba(26,108,255,.03);
  min-height:88px;
  box-shadow:0 0 0 3px rgba(26,108,255,.08);
}
.composer-input::placeholder{color:var(--dim)}
.composer-bar{
  display:flex;align-items:center;gap:7px;
  margin-top:12px;padding-top:12px;
  border-top:1px solid var(--line);
}
.cbar-btn{
  padding:6px 13px;border-radius:7px;border:1px solid var(--line);
  background:transparent;color:var(--muted);font-size:.76rem;font-weight:700;
  transition:all .2s;
}
.cbar-btn:hover{border-color:var(--sky);color:var(--sky);background:rgba(26,108,255,.05)}
.post-btn{
  margin-left:auto;padding:8px 22px;
  background:var(--sky);border:none;border-radius:9px;
  color:#fff;font-size:.84rem;font-weight:800;letter-spacing:-.01em;
  transition:all .2s;
}
.post-btn:hover{background:var(--sky2);transform:translateY(-1px);box-shadow:0 6px 20px rgba(26,108,255,.35)}
.post-btn:disabled{opacity:.4;cursor:not-allowed;transform:none;box-shadow:none}

.attach-preview{
  display:none;align-items:center;gap:10px;
  background:rgba(26,108,255,.06);border:1px solid rgba(26,108,255,.18);
  border-radius:8px;padding:8px 12px;margin-top:10px;font-size:.8rem;
}
.attach-preview.show{display:flex}
.attach-preview span{flex:1;color:var(--sky);font-weight:600}
.attach-preview .rm{cursor:pointer;color:var(--muted);font-size:1rem;transition:color .2s}
.attach-preview .rm:hover{color:var(--rose)}

/* ─── POST CARD ─── */
.post-card{
  background:var(--card);border:1px solid var(--line);
  border-radius:var(--r2);overflow:hidden;
  animation:slideUp .35s cubic-bezier(.16,1,.3,1) both;
  transition:border-color .2s;
}
.post-card:hover{border-color:var(--line2)}
@keyframes slideUp{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:translateY(0)}}

.post-header{display:flex;align-items:flex-start;gap:11px;padding:18px 18px 0}
.post-meta{flex:1;min-width:0}
.post-author{font-weight:800;font-size:.88rem;letter-spacing:-.02em}
.post-headline{color:var(--muted);font-size:.75rem;margin-top:1px;font-weight:500}
.post-time{
  color:var(--dim);font-size:.7rem;margin-top:2px;
  font-family:'Geist Mono',monospace;font-weight:400;
}

.post-content{
  padding:12px 18px;font-size:.875rem;line-height:1.75;
  color:rgba(228,235,245,.88);
}

.post-file{margin:0 18px 12px;border-radius:10px;overflow:hidden;border:1px solid var(--line)}
.post-file img{width:100%;max-height:340px;object-fit:cover;display:block}
.file-attach{display:flex;align-items:center;gap:10px;padding:12px 14px;background:var(--ink3)}
.file-attach .ficon{font-size:1.3rem}
.file-attach .fname{font-size:.83rem;font-weight:700}
.file-attach .ftype{font-size:.69rem;color:var(--muted);font-family:'Geist Mono',monospace}
.file-attach a{
  margin-left:auto;font-size:.75rem;color:var(--sky);font-weight:700;
  padding:5px 12px;border:1px solid rgba(26,108,255,.3);border-radius:7px;
  transition:all .2s;
}
.file-attach a:hover{background:rgba(26,108,255,.1)}

.post-actions{
  display:flex;gap:3px;padding:8px 12px 12px;
  border-top:1px solid var(--line);margin-top:6px;
}
.p-action{
  padding:7px 13px;border-radius:8px;border:none;
  background:transparent;color:var(--muted);font-size:.78rem;font-weight:700;
  transition:all .2s;display:flex;align-items:center;gap:5px;letter-spacing:-.01em;
}
.p-action:hover{background:var(--ink3);color:var(--text)}
.p-action.liked{color:var(--sky);background:rgba(26,108,255,.08)}

/* ─── PROFILE PAGE ─── */
.profile-box{
  background:var(--card);border:1px solid var(--line);
  border-radius:var(--r2);overflow:hidden;margin-bottom:14px;
}
.profile-cover{
  height:160px;
  background:linear-gradient(135deg,
    rgba(26,108,255,.25) 0%,
    rgba(124,58,237,.2) 50%,
    rgba(0,196,140,.1) 100%);
  position:relative;overflow:hidden;
}
.profile-cover::after{
  content:'';position:absolute;bottom:-1px;left:0;right:0;height:60px;
  background:linear-gradient(to top, var(--card), transparent);
}
.profile-info{padding:0 22px 22px;margin-top:-44px;position:relative}
.profile-avatar-wrap{
  display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:14px;
}
.profile-edit-btn{
  padding:8px 18px;border:1px solid var(--line2);border-radius:9px;
  background:transparent;color:var(--text);font-size:.81rem;font-weight:800;
  transition:all .2s;letter-spacing:-.01em;
}
.profile-edit-btn:hover{border-color:var(--sky);color:var(--sky);background:rgba(26,108,255,.05)}
.profile-name{font-size:1.4rem;font-weight:900;letter-spacing:-.04em}
.profile-headline{color:var(--muted);font-size:.87rem;margin-top:4px;font-weight:500}
.profile-location{color:var(--dim);font-size:.79rem;margin-top:4px;font-family:'Geist Mono',monospace}
.profile-about{
  font-size:.86rem;line-height:1.75;color:rgba(228,235,245,.8);
  margin-top:14px;padding-top:14px;border-top:1px solid var(--line);
}
.profile-skills{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}
.skill-tag{
  background:rgba(26,108,255,.08);border:1px solid rgba(26,108,255,.2);
  color:var(--sky);padding:4px 12px;border-radius:999px;
  font-size:.73rem;font-weight:700;letter-spacing:-.01em;
  transition:all .2s;
}
.skill-tag:hover{background:rgba(26,108,255,.15)}

/* ─── RESUME SECTION ─── */
.resume-section{
  background:var(--card);border:1px solid var(--line);
  border-radius:var(--r2);padding:24px;margin-bottom:14px;
}
.rs-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.rs-title{font-size:1rem;font-weight:900;letter-spacing:-.03em}
.ai-badge{
  display:inline-flex;align-items:center;gap:5px;
  background:rgba(124,58,237,.1);border:1px solid rgba(124,58,237,.25);
  color:#a78bfa;padding:4px 11px;border-radius:999px;
  font-size:.68rem;font-weight:800;text-transform:uppercase;letter-spacing:.08em;
}
.ai-badge::before{content:'✦';font-size:.7rem}

.rs-upload{
  border:2px dashed var(--line2);border-radius:14px;
  padding:36px;text-align:center;cursor:pointer;
  transition:all .25s;position:relative;background:var(--ink3);
}
.rs-upload:hover,.rs-upload.drag{
  border-color:var(--sky);background:rgba(26,108,255,.04);
}
.rs-upload input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%}
.rs-upload .icon{font-size:2.2rem;margin-bottom:12px;display:block}
.rs-upload h4{font-size:.95rem;font-weight:800;margin-bottom:6px;letter-spacing:-.02em}
.rs-upload p{color:var(--muted);font-size:.8rem;font-weight:500}
.rs-upload strong{color:var(--sky)}

.analysis-result{display:none;margin-top:20px}
.analysis-result.show{display:block;animation:slideUp .4s ease both}

.score-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px}
.score-box{
  background:var(--ink3);border:1px solid var(--line);
  border-radius:12px;padding:16px 12px;text-align:center;
  transition:border-color .2s;
}
.score-box:hover{border-color:var(--line2)}
.score-box .val{
  font-family:'Geist Mono',monospace;font-size:1.5rem;font-weight:500;
  letter-spacing:-.04em;
}
.score-box .lbl{font-size:.68rem;color:var(--muted);margin-top:4px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}

.ai-card{
  background:var(--ink3);border:1px solid var(--line);
  border-radius:12px;padding:16px 18px;margin-bottom:12px;
  border-left:3px solid var(--violet);
  transition:border-left-color .2s;
}
.ai-card.warn{border-left-color:var(--amber)}
.ai-card h4{font-size:.82rem;font-weight:800;color:#a78bfa;margin-bottom:6px;letter-spacing:-.01em}
.ai-card.warn h4{color:var(--amber)}
.ai-card p{color:var(--muted);font-size:.82rem;line-height:1.7}

.job-card{
  background:var(--ink3);border:1px solid var(--line);
  border-radius:12px;padding:14px 16px;margin-bottom:9px;
  display:flex;align-items:center;gap:14px;
  transition:border-color .2s;
}
.job-card:hover{border-color:var(--line2)}
.job-info{flex:1;min-width:0}
.job-title{font-size:.88rem;font-weight:800;letter-spacing:-.02em}
.job-co{font-size:.74rem;color:var(--muted);margin-top:2px;font-weight:600}
.job-bar{width:90px;height:5px;background:var(--line2);border-radius:4px;overflow:hidden;flex-shrink:0}
.job-fill{height:100%;background:linear-gradient(90deg,var(--sky),var(--violet));border-radius:4px;transition:width 1.2s cubic-bezier(.16,1,.3,1)}
.job-pct{
  font-family:'Geist Mono',monospace;font-size:.8rem;font-weight:500;
  color:var(--sky);min-width:40px;text-align:right;
}

/* ─── NETWORK ─── */
.people-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.people-card{
  background:var(--card);border:1px solid var(--line);
  border-radius:var(--r2);padding:22px 18px;text-align:center;
  transition:all .2s;
}
.people-card:hover{border-color:var(--line2);transform:translateY(-2px)}
.people-card .avatar{margin:0 auto 12px;width:60px;height:60px;font-size:1.25rem}
.people-name{font-weight:800;font-size:.9rem;letter-spacing:-.02em}
.people-role{color:var(--muted);font-size:.76rem;margin:4px 0 14px;font-weight:500}
.connect-btn{
  width:100%;padding:8px;border-radius:8px;
  border:1px solid rgba(26,108,255,.35);background:transparent;
  color:var(--sky);font-size:.78rem;font-weight:800;
  transition:all .2s;letter-spacing:-.01em;
}
.connect-btn:hover{background:var(--sky);color:#fff;border-color:var(--sky)}
.connect-btn:disabled{background:rgba(0,196,140,.1);color:var(--mint);border-color:rgba(0,196,140,.3)}

/* ─── SUGGESTIONS ─── */
.suggest-item{
  display:flex;align-items:center;gap:11px;padding:10px 14px;
  transition:background .2s;border-bottom:1px solid var(--line);
}
.suggest-item:last-child{border-bottom:none}
.suggest-item:hover{background:var(--ink3)}
.suggest-info{flex:1;min-width:0}
.suggest-name{font-weight:800;font-size:.83rem;letter-spacing:-.01em}
.suggest-role{color:var(--muted);font-size:.72rem;font-weight:500}
.s-connect-btn{
  padding:5px 12px;border-radius:999px;border:1px solid rgba(26,108,255,.3);
  background:transparent;color:var(--sky);font-size:.72rem;font-weight:800;
  transition:all .2s;
}
.s-connect-btn:hover{background:var(--sky);color:#fff}

/* ─── MODAL ─── */
.modal-bg{
  display:none;position:fixed;inset:0;
  background:rgba(0,0,0,.7);backdrop-filter:blur(8px);
  z-index:200;align-items:center;justify-content:center;padding:20px;
}
.modal-bg.show{display:flex}
.modal{
  background:var(--ink2);border:1px solid var(--line2);
  border-radius:22px;padding:28px;width:100%;max-width:500px;
  max-height:86vh;overflow-y:auto;position:relative;
  animation:slideUp .3s cubic-bezier(.16,1,.3,1) both;
}
.modal h2{font-size:1.1rem;font-weight:900;letter-spacing:-.03em;margin-bottom:18px}
.modal-close{
  position:absolute;top:14px;right:14px;
  background:var(--ink3);border:none;border-radius:8px;
  width:30px;height:30px;display:flex;align-items:center;justify-content:center;
  cursor:pointer;color:var(--muted);font-size:.9rem;transition:all .2s;
}
.modal-close:hover{background:rgba(244,63,94,.1);color:var(--rose)}

.mfield{margin-bottom:13px}
.mfield label{display:block;font-size:.7rem;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px}
.mfield input,.mfield textarea{
  width:100%;background:var(--ink3);border:1px solid var(--line);
  border-radius:9px;padding:10px 13px;color:var(--text);font-size:.85rem;
  outline:none;transition:all .2s;
}
.mfield input:focus,.mfield textarea:focus{border-color:var(--sky);box-shadow:0 0 0 3px rgba(26,108,255,.08)}
.mfield textarea{resize:vertical;min-height:80px;line-height:1.6}
.modal-save{
  width:100%;padding:12px;background:var(--sky);border:none;
  border-radius:10px;color:#fff;font-size:.9rem;font-weight:800;
  margin-top:6px;transition:all .2s;letter-spacing:-.01em;
}
.modal-save:hover{background:var(--sky2);transform:translateY(-1px);box-shadow:0 6px 20px rgba(26,108,255,.3)}

/* ─── TAGS ─── */
.tag{display:inline-block;padding:3px 10px;border-radius:6px;font-size:.7rem;font-weight:800;letter-spacing:-.01em}
.tag-blue{background:rgba(26,108,255,.1);border:1px solid rgba(26,108,255,.2);color:var(--sky)}
.tag-green{background:rgba(0,196,140,.1);border:1px solid rgba(0,196,140,.2);color:var(--mint)}
.tag-purple{background:rgba(124,58,237,.1);border:1px solid rgba(124,58,237,.2);color:#a78bfa}
.tag-amber{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.2);color:var(--amber)}

/* ─── MISC ─── */
.page{display:none}.page.show{display:block}
.spinner{
  width:32px;height:32px;border:2.5px solid var(--line2);
  border-top-color:var(--sky);border-radius:50%;
  animation:spin .65s linear infinite;margin:0 auto;
}
@keyframes spin{to{transform:rotate(360deg)}}
.loading-wrap{padding:44px;text-align:center;color:var(--muted);font-size:.84rem}
.loading-wrap p{margin-top:12px;font-weight:600}
.empty-state{text-align:center;padding:56px 20px;color:var(--muted)}
.empty-state .icon{font-size:2.2rem;margin-bottom:12px;display:block;opacity:.5}
.empty-state h3{font-size:1rem;font-weight:900;color:var(--text);margin-bottom:6px;letter-spacing:-.02em}
.empty-state p{font-size:.83rem}

/* ─── NOTIFICATION ─── */
.notif{
  position:fixed;bottom:22px;right:22px;
  background:var(--mint);color:#051a12;
  padding:10px 18px;border-radius:10px;
  font-weight:800;font-size:.82rem;z-index:999;
  transform:translateY(80px);opacity:0;transition:all .3s cubic-bezier(.16,1,.3,1);
  display:flex;align-items:center;gap:7px;
}
.notif.show{transform:translateY(0);opacity:1}
.notif.err{background:var(--rose);color:#fff}
</style>
</head>
<body>

<!-- ═══════ AUTH PAGE ═══════ -->
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
      <div class="field"><label>Email</label><input type="email" id="loginEmail" placeholder="you@company.com" /></div>
      <div class="field"><label>Password</label><input type="password" id="loginPass" placeholder="••••••••" /></div>
      <button class="auth-btn" onclick="doLogin()">Sign in →</button>
    </div>
    <div id="registerForm" style="display:none">
      <div class="field"><label>Full name</label><input type="text" id="regName" placeholder="Jane Smith" /></div>
      <div class="field"><label>Email</label><input type="email" id="regEmail" placeholder="you@company.com" /></div>
      <div class="field"><label>Password</label><input type="password" id="regPass" placeholder="Min 6 characters" /></div>
      <div class="field"><label>Job title / headline</label><input type="text" id="regHeadline" placeholder="e.g. Software Engineer at TechCorp" /></div>
      <button class="auth-btn" onclick="doRegister()">Create account →</button>
    </div>
  </div>
</div>

<!-- ═══════ APP ═══════ -->
<div class="app" id="appShell">

  <!-- TOPBAR -->
  <div class="topbar">
    <div class="topbar-logo">gath<span>R</span></div>
    <div class="topbar-search">
      <span class="si">⌕</span>
      <input type="text" placeholder="Search people, posts..." id="searchInput" />
    </div>
    <div class="topbar-nav">
      <button class="tnav active" onclick="showPage('feed')">Feed</button>
      <button class="tnav" onclick="showPage('profile')">Profile</button>
      <button class="tnav" onclick="showPage('resume')">Resume</button>
      <button class="tnav" onclick="showPage('network')">Network</button>
    </div>
    <div class="topbar-user" onclick="showPage('profile')">
      <div class="avatar" id="navAvatar"></div>
      <span class="uname" id="navName"></span>
    </div>
    <button class="logout-btn" onclick="doLogout()">Sign out</button>
  </div>

  <!-- ── FEED PAGE ── -->
  <div class="page show" id="page-feed">
    <div class="main-layout">

      <!-- LEFT SIDEBAR -->
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
            <div class="s-link" onclick="showPage('profile')"><span class="icon">◎</span>My Profile</div>
            <div class="s-link" onclick="showPage('resume')"><span class="icon">▣</span>Resume & AI</div>
            <div class="s-link" onclick="showPage('network')"><span class="icon">⊕</span>Network</div>
          </div>
        </div>
      </div>

      <!-- CENTER FEED -->
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
        <div id="feedPosts">
          <div class="loading-wrap"><div class="spinner"></div><p>Loading feed...</p></div>
        </div>
      </div>

      <!-- RIGHT SIDEBAR -->
      <div class="sidebar-right">
        <div class="s-card">
          <div style="padding:14px 14px 0">
            <div class="s-section-title" style="padding:0 0 10px;font-size:.68rem;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.1em">People you may know</div>
          </div>
          <div id="suggestions"></div>
        </div>
        <div class="s-section">
          <div class="s-section-title">Your skills</div>
          <div id="sideSkills" style="display:flex;flex-wrap:wrap;gap:6px"></div>
          <button style="margin-top:12px;width:100%;padding:8px;border-radius:8px;border:1px dashed var(--line2);background:transparent;color:var(--muted);font-size:.76rem;font-weight:700;cursor:pointer;transition:all .2s" onclick="openEditProfile()" onmouseover="this.style.borderColor='var(--sky)';this.style.color='var(--sky)'" onmouseout="this.style.borderColor='var(--line2)';this.style.color='var(--muted)'">+ Add skills</button>
        </div>
      </div>

    </div>
  </div>

  <!-- ── PROFILE PAGE ── -->
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

  <!-- ── RESUME PAGE ── -->
  <div class="page" id="page-resume">
    <div class="main-layout">
      <div></div>
      <div>
        <div class="resume-section">
          <div class="rs-header">
            <div class="rs-title">Resume & AI Analysis</div>
            <span class="ai-badge">AI Powered</span>
          </div>
          <div class="rs-upload" id="resumeDropZone">
            <input type="file" id="resumeFileInput" accept=".pdf,.txt" onchange="handleResumeUpload(this)" />
            <span class="icon">⬆</span>
            <h4>Drop your resume here</h4>
            <p><strong>PDF or TXT</strong> — AI analyzes skills, ATS score & job matches</p>
          </div>
          <div id="resumeLoading" style="display:none" class="loading-wrap">
            <div class="spinner"></div>
            <p id="resumeLoadText">Analyzing with AI...</p>
          </div>
          <div class="analysis-result" id="analysisResult"></div>
        </div>
      </div>
      <div></div>
    </div>
  </div>

  <!-- ── NETWORK PAGE ── -->
  <div class="page" id="page-network">
    <div class="main-layout">
      <div></div>
      <div>
        <div style="background:var(--card);border:1px solid var(--line);border-radius:var(--r2);padding:24px">
          <div style="font-size:1rem;font-weight:900;letter-spacing:-.03em;margin-bottom:18px">Grow your network</div>
          <div id="networkList"><div class="loading-wrap"><div class="spinner"></div><p>Loading...</p></div></div>
        </div>
      </div>
      <div></div>
    </div>
  </div>

</div>

<!-- EDIT PROFILE MODAL -->
<div class="modal-bg" id="editProfileModal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('editProfileModal')">✕</button>
    <h2>Edit Profile</h2>
    <div class="mfield"><label>Full name</label><input id="ep_name" /></div>
    <div class="mfield"><label>Headline</label><input id="ep_headline" placeholder="e.g. Senior Engineer at Google" /></div>
    <div class="mfield"><label>Location</label><input id="ep_location" placeholder="e.g. Hyderabad, IN" /></div>
    <div class="mfield"><label>About</label><textarea id="ep_about" placeholder="Tell your professional story..."></textarea></div>
    <div class="mfield"><label>Skills (comma separated)</label><input id="ep_skills" placeholder="Python, React, Machine Learning..." /></div>
    <button class="modal-save" onclick="saveProfile()">Save changes</button>
  </div>
</div>

<div class="notif" id="notif"></div>

<script>
let ME = null;
let attachedFile = null;

window.addEventListener('DOMContentLoaded', checkSession);

async function checkSession() {
  try {
    const r = await fetch('/api/me');
    if (r.ok) { ME = await r.json(); showApp(); }
  } catch(e) {}
}

// ── AUTH ──
function showAuthTab(tab) {
  document.querySelectorAll('.auth-tab').forEach((t,i)=>t.classList.toggle('active',(i===0&&tab==='login')||(i===1&&tab==='register')));
  document.getElementById('loginForm').style.display=tab==='login'?'block':'none';
  document.getElementById('registerForm').style.display=tab==='register'?'block':'none';
  document.getElementById('authErr').classList.remove('show');
}
async function doLogin() {
  const email=document.getElementById('loginEmail').value.trim();
  const pass=document.getElementById('loginPass').value;
  if (!email||!pass){showAuthErr('Please fill all fields');return}
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password:pass})});
  const d=await r.json();
  if(d.error){showAuthErr(d.error);return}
  ME=d.user; showApp();
}
async function doRegister() {
  const name=document.getElementById('regName').value.trim();
  const email=document.getElementById('regEmail').value.trim();
  const pass=document.getElementById('regPass').value;
  const headline=document.getElementById('regHeadline').value.trim();
  if(!name||!email||!pass){showAuthErr('Please fill all fields');return}
  if(pass.length<6){showAuthErr('Password must be at least 6 characters');return}
  const r=await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,email,password:pass,headline})});
  const d=await r.json();
  if(d.error){showAuthErr(d.error);return}
  ME=d.user; showApp();
}
async function doLogout() {
  await fetch('/api/logout',{method:'POST'});
  ME=null;
  document.getElementById('appShell').classList.remove('show');
  document.getElementById('authPage').style.display='flex';
  showAuthTab('login');
}
function showAuthErr(msg){
  const el=document.getElementById('authErr');
  el.textContent=msg; el.classList.add('show');
}

// ── APP ──
function showApp() {
  document.getElementById('authPage').style.display='none';
  document.getElementById('appShell').classList.add('show');
  refreshUserUI(); loadFeed(); loadSuggestions(); loadSavedAnalysis();
}
function refreshUserUI() {
  if(!ME)return;
  const ini=ME.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
  ['navAvatar','sideAvatar','compAvatar','profAvatar'].forEach(id=>{
    const el=document.getElementById(id); if(el) el.textContent=ini;
  });
  setText('navName',ME.name.split(' ')[0]);
  setText('sideName',ME.name);
  setText('sideHeadline',ME.headline||'');
  setText('profName',ME.name);
  setText('profHeadline',ME.headline||'Professional on gathR');
  setText('profLocation',ME.location?'◎ '+ME.location:'');
  setText('profAbout',ME.about||'');
  const skills=JSON.parse(ME.skills||'[]');
  renderSkillTags('profSkills',skills);
  renderSkillTags('sideSkills',skills);
}
function renderSkillTags(cid,skills){
  const el=document.getElementById(cid); if(!el)return;
  el.innerHTML=skills.map(s=>`<span class="skill-tag">${s}</span>`).join('');
}

// ── PAGES ──
function showPage(page){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('show'));
  document.querySelectorAll('.tnav').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.s-link').forEach(b=>b.classList.remove('active'));
  document.getElementById('page-'+page).classList.add('show');
  document.querySelectorAll('.tnav').forEach(b=>{if(b.textContent.toLowerCase().includes(page.slice(0,4)))b.classList.add('active')});
  document.querySelectorAll('.s-link').forEach(b=>{if(b.textContent.toLowerCase().trim().startsWith(page.slice(0,4)))b.classList.add('active')});
  if(page==='profile')loadProfilePosts();
  if(page==='network')loadNetwork();
}

// ── FEED ──
async function loadFeed(){
  const r=await fetch('/api/posts');
  const posts=await r.json();
  const c=document.getElementById('feedPosts');
  if(!posts.length){c.innerHTML='<div class="empty-state"><span class="icon">✦</span><h3>No posts yet</h3><p>Be the first to share something!</p></div>';return}
  c.innerHTML=posts.map(renderPost).join('');
  document.getElementById('statPosts').textContent=posts.filter(p=>p.user_id===ME.id).length;
}

function renderPost(p){
  const likes=JSON.parse(p.likes||'[]');
  const liked=ME&&likes.includes(ME.id);
  const isOwner=ME&&p.user_id===ME.id;
  let fileHtml='';
  if(p.file_url){
    const isImg=['png','jpg','jpeg','gif'].some(e=>p.file_name&&p.file_name.toLowerCase().endsWith(e));
    if(isImg){
      fileHtml=`<div class="post-file"><img src="/uploads/${p.file_url}" alt="attachment"/></div>`;
    } else {
      const icons={pdf:'📄',doc:'📝',docx:'📝',txt:'📃'};
      const ext=p.file_name?p.file_name.split('.').pop().toLowerCase():'';
      fileHtml=`<div class="post-file"><div class="file-attach"><span class="ficon">${icons[ext]||'📎'}</span><div><div class="fname">${p.file_name||'Attachment'}</div><div class="ftype">${ext.toUpperCase()}</div></div><a href="/uploads/${p.file_url}" target="_blank">View →</a></div></div>`;
    }
  }
  const ini=(p.author_name||'U').split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
  const ownerActions=isOwner?`
    <button class="p-action" onclick="editPost(${p.id},'${escAttr(p.content)}')">✏ Edit</button>
    <button class="p-action" style="color:var(--rose)" onclick="deletePost(${p.id})">🗑 Delete</button>
  `:'';
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
      <button class="p-action" onclick="toggleComment(${p.id})">💬 Comment</button>
      <button class="p-action" onclick="sharePost(${p.id})">↗ Share</button>
      ${ownerActions}
    </div>
    <div class="comment-box" id="comment-box-${p.id}" style="display:none;padding:10px 18px;border-top:1px solid var(--line)">
      <div style="display:flex;gap:8px">
        <textarea id="comment-input-${p.id}" placeholder="Write a comment..." style="flex:1;background:var(--ink3);border:1px solid var(--line);border-radius:8px;padding:8px 12px;color:var(--text);font-size:.82rem;resize:none;outline:none;min-height:40px"></textarea>
        <button onclick="submitComment(${p.id})" style="padding:8px 14px;background:var(--sky);border:none;border-radius:8px;color:#fff;font-size:.78rem;font-weight:800;cursor:pointer">Post</button>
      </div>
    </div>
  </div>`;
}

// ── LIKE ──
async function toggleLike(postId){
  await fetch('/api/posts/'+postId+'/like',{method:'POST'});
  loadFeed();
}

// ── COMMENT ──
function toggleComment(postId){
  const box=document.getElementById('comment-box-'+postId);
  box.style.display=box.style.display==='none'?'block':'none';
  if(box.style.display==='block')document.getElementById('comment-input-'+postId).focus();
}
function submitComment(postId){
  const val=document.getElementById('comment-input-'+postId).value.trim();
  if(!val)return;
  showNotif('Comment posted! ✓');
  document.getElementById('comment-input-'+postId).value='';
  toggleComment(postId);
}

// ── SHARE ──
function sharePost(postId){
  const url=window.location.origin+'#post-'+postId;
  navigator.clipboard.writeText(url).then(()=>showNotif('Link copied! ↗')).catch(()=>showNotif('Share: '+url));
}

// ── DELETE ──
async function deletePost(postId){
  if(!confirm('Delete this post?'))return;
  const r=await fetch('/api/posts/'+postId,{method:'DELETE'});
  const d=await r.json();
  if(d.error){showNotif('Error: '+d.error,true);return}
  showNotif('Post deleted');
  loadFeed();
  loadProfilePosts();
}

// ── EDIT ──
function editPost(postId, currentContent){
  const contentEl=document.getElementById('post-content-'+postId);
  contentEl.innerHTML=`
    <textarea id="edit-input-${postId}" style="width:100%;background:var(--ink3);border:1px solid var(--sky);border-radius:8px;padding:10px;color:var(--text);font-size:.875rem;resize:none;outline:none;min-height:80px;line-height:1.6">${currentContent}</textarea>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button onclick="saveEdit(${postId})" style="padding:7px 16px;background:var(--sky);border:none;border-radius:7px;color:#fff;font-size:.78rem;font-weight:800;cursor:pointer">Save</button>
      <button onclick="cancelEdit(${postId},'${escAttr(currentContent)}')" style="padding:7px 16px;background:var(--ink3);border:1px solid var(--line);border-radius:7px;color:var(--muted);font-size:.78rem;font-weight:800;cursor:pointer">Cancel</button>
    </div>`;
}
async function saveEdit(postId){
  const val=document.getElementById('edit-input-'+postId).value.trim();
  if(!val)return;
  const r=await fetch('/api/posts/'+postId,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:val})});
  const d=await r.json();
  if(d.error){showNotif('Error: '+d.error,true);return}
  showNotif('Post updated! ✓');
  loadFeed();
  loadProfilePosts();
}
function cancelEdit(postId, originalContent){
  document.getElementById('post-content-'+postId).innerHTML=escHtml(originalContent);
}

async function submitPost(){
  const text=document.getElementById('postText').value.trim();
  if(!text&&!attachedFile)return;
  const btn=document.getElementById('postBtn');
  btn.disabled=true; btn.textContent='Posting...';
  const fd=new FormData();
  fd.append('content',text||'');
  if(attachedFile)fd.append('file',attachedFile);
  const r=await fetch('/api/posts',{method:'POST',body:fd});
  const d=await r.json();
  btn.disabled=false; btn.textContent='Post';
  if(d.error){showNotif('Error: '+d.error,true);return}
  document.getElementById('postText').value='';
  clearAttach(); showNotif('Post shared! ✓'); loadFeed();
}

function handleAttach(input){
  if(!input.files[0])return;
  attachedFile=input.files[0];
  document.getElementById('attachName').textContent=attachedFile.name;
  document.getElementById('attachPreview').classList.add('show');
}
function clearAttach(){
  attachedFile=null;
  document.getElementById('attachFile').value='';
  document.getElementById('attachPreview').classList.remove('show');
}
function addEmoji(){
  const emojis=['🎉','🚀','💡','🔥','✅','👏','💼','📊','⚡','🌟'];
  document.getElementById('postText').value+=' '+emojis[Math.floor(Math.random()*emojis.length)];
}

// ── PROFILE ──
async function loadProfilePosts(){
  const r=await fetch('/api/posts?mine=1');
  const posts=await r.json();
  const c=document.getElementById('profilePosts');
  if(!posts.length){c.innerHTML='<div class="empty-state"><span class="icon">✦</span><h3>No posts yet</h3><p>Share your first update!</p></div>';return}
  c.innerHTML=posts.map(renderPost).join('');
}
function openEditProfile(){
  if(!ME)return;
  document.getElementById('ep_name').value=ME.name||'';
  document.getElementById('ep_headline').value=ME.headline||'';
  document.getElementById('ep_location').value=ME.location||'';
  document.getElementById('ep_about').value=ME.about||'';
  document.getElementById('ep_skills').value=JSON.parse(ME.skills||'[]').join(', ');
  openModal('editProfileModal');
}
async function saveProfile(){
  const skills=document.getElementById('ep_skills').value.split(',').map(s=>s.trim()).filter(Boolean);
  const data={
    name:document.getElementById('ep_name').value.trim(),
    headline:document.getElementById('ep_headline').value.trim(),
    location:document.getElementById('ep_location').value.trim(),
    about:document.getElementById('ep_about').value.trim(),
    skills:JSON.stringify(skills),
  };
  const r=await fetch('/api/profile',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const d=await r.json();
  if(d.error){showNotif('Error saving',true);return}
  ME={...ME,...data}; refreshUserUI();
  closeModal('editProfileModal'); showNotif('Profile updated! ✓');
}

// ── RESUME ──
const rdz=document.getElementById('resumeDropZone');
rdz.addEventListener('dragover',e=>{e.preventDefault();rdz.classList.add('drag')});
rdz.addEventListener('dragleave',()=>rdz.classList.remove('drag'));
rdz.addEventListener('drop',e=>{
  e.preventDefault();rdz.classList.remove('drag');
  const f=e.dataTransfer.files[0]; if(f)processResume(f);
});
function handleResumeUpload(input){if(input.files[0])processResume(input.files[0])}

async function processResume(file){
  document.getElementById('resumeLoading').style.display='block';
  document.getElementById('analysisResult').classList.remove('show');
  const steps=['Extracting text...','Analyzing skills...','Scoring ATS...','Finding job matches...','Building roadmap...'];
  let si=0;
  const iv=setInterval(()=>{document.getElementById('resumeLoadText').textContent=steps[si%steps.length];si++;},1200);
  const fd=new FormData(); fd.append('resume',file);
  try {
    const r=await fetch('/api/analyze_resume',{method:'POST',body:fd});
    const d=await r.json();
    clearInterval(iv);
    document.getElementById('resumeLoading').style.display='none';
    if(d.error){showNotif('Analysis failed: '+d.error,true);return}
    renderAnalysis(d);
    if(d.skills){ME.skills=JSON.stringify(d.skills);refreshUserUI();}
  } catch(e){
    clearInterval(iv);
    document.getElementById('resumeLoading').style.display='none';
    showNotif('Something went wrong.',true);
  }
}
function renderAnalysis(d){
  const ats=d.ats||{};
  const gap=d.gap||{};
  const jobs=d.jobs||[];
  let html=`
  <div class="ai-card">
    <h4>✦ AI Profile Summary</h4>
    <p>${d.ai_summary||'Analysis complete.'}</p>
  </div>
  <div class="score-grid">
    <div class="score-box"><div class="val" style="color:var(--sky)">${d.profile_score||0}<span style="font-size:.7em">%</span></div><div class="lbl">Profile</div></div>
    <div class="score-box"><div class="val" style="color:#a78bfa">${ats.overall||0}<span style="font-size:.7em">%</span></div><div class="lbl">ATS</div></div>
    <div class="score-box"><div class="val" style="color:var(--mint)">${ats.keywords||0}<span style="font-size:.7em">%</span></div><div class="lbl">Keywords</div></div>
    <div class="score-box"><div class="val" style="color:var(--amber)">${ats.readability||0}<span style="font-size:.7em">%</span></div><div class="lbl">Readability</div></div>
  </div>`;
  if(d.skills&&d.skills.length){
    html+=`<div style="margin-bottom:16px">
      <div style="font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;font-weight:800;margin-bottom:8px">Detected Skills</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px">${d.skills.map(s=>`<span class="tag tag-blue">${s}</span>`).join('')}</div>
    </div>`;
  }
  if(jobs.length){
    html+=`<div style="margin-bottom:16px">
      <div style="font-size:.9rem;font-weight:900;letter-spacing:-.03em;margin-bottom:10px">🎯 Top Job Matches</div>`;
    jobs.slice(0,5).forEach(j=>{
      html+=`<div class="job-card">
        <div class="job-info"><div class="job-title">${j.title}</div><div class="job-co">${j.company} · ${j.type}</div></div>
        <div class="job-bar"><div class="job-fill" style="width:0" data-w="${j.match_pct}%"></div></div>
        <div class="job-pct">${j.match_pct}%</div>
      </div>`;
    });
    html+=`</div>`;
  }
  if(gap.missing_skills&&gap.missing_skills.length){
    html+=`<div class="ai-card warn">
      <h4>📈 Skills to Develop</h4>
      <p style="margin-bottom:10px">${gap.overview||''}</p>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">${gap.missing_skills.map(s=>`<span class="tag tag-purple">${s}</span>`).join('')}</div>
    </div>`;
  }
  if(ats.suggestions&&ats.suggestions.length){
    html+=`<div style="font-size:.88rem;font-weight:900;letter-spacing:-.03em;margin-bottom:10px">⚠ ATS Improvements</div>`;
    ats.suggestions.forEach(s=>{
      html+=`<div class="ai-card warn" style="margin-bottom:10px"><h4>${s.title}</h4><p>${s.detail}</p></div>`;
    });
  }
  const result=document.getElementById('analysisResult');
  result.innerHTML=html; result.classList.add('show');
  setTimeout(()=>document.querySelectorAll('.job-fill').forEach(b=>b.style.width=b.dataset.w),150);
}
function loadSavedAnalysis(){
  if(ME&&ME.resume_analysis&&ME.resume_analysis!=='{}'){
    try{const d=JSON.parse(ME.resume_analysis);if(d&&d.ai_summary)renderAnalysis(d);}catch(e){}
  }
}

// ── NETWORK ──
async function loadSuggestions(){
  const r=await fetch('/api/users');
  const users=await r.json();
  const others=users.filter(u=>u.id!==ME.id).slice(0,4);
  document.getElementById('suggestions').innerHTML=others.length?
    others.map(u=>`<div class="suggest-item">
      <div class="avatar" style="width:30px;height:30px;font-size:.68rem">${u.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2)}</div>
      <div class="suggest-info"><div class="suggest-name">${u.name}</div><div class="suggest-role">${u.headline||'gathR member'}</div></div>
      <button class="s-connect-btn" onclick="connect(${u.id},this)">Connect</button>
    </div>`).join(''):
    '<div style="padding:16px;color:var(--muted);font-size:.82rem;font-weight:600">No suggestions yet</div>';
}
async function loadNetwork(){
  const r=await fetch('/api/users');
  const users=await r.json();
  const others=users.filter(u=>u.id!==ME.id);
  document.getElementById('networkList').innerHTML=others.length?
    `<div class="people-grid">${others.map(u=>`
    <div class="people-card">
      <div class="avatar">${u.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2)}</div>
      <div class="people-name">${u.name}</div>
      <div class="people-role">${u.headline||'gathR member'}</div>
      <button class="connect-btn" onclick="connect(${u.id},this)">Connect</button>
    </div>`).join('')}</div>`:
    '<div class="empty-state"><span class="icon">⊕</span><h3>No members yet</h3><p>Invite your colleagues!</p></div>';
}
async function connect(userId,btn){
  btn.textContent='Connected ✓'; btn.disabled=true;
  await fetch('/api/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({to_user:userId})});
  showNotif('Connection sent!');
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
function showNotif(msg,isErr=false){
  const el=document.getElementById('notif');
  el.textContent=msg;
  el.className='notif'+(isErr?' err':'');
  el.classList.add('show');
  setTimeout(()=>el.classList.remove('show'),2800);
}
</script>
</body>
</html>"""

# ══════════════════════════════════════════════
#  API ROUTES
# ══════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(HTML)

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
    db.execute("INSERT INTO users (name,email,password,headline) VALUES (?,?,?,?)",
               (name, email, hashed, headline))
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
    db.execute("UPDATE users SET name=?,headline=?,location=?,about=?,skills=? WHERE id=?",
               (d.get("name"), d.get("headline"), d.get("location"), d.get("about"), d.get("skills"), session["user_id"]))
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
        rows = db.execute("""
            SELECT p.*,u.name as author_name,u.headline as author_headline
            FROM posts p JOIN users u ON p.user_id=u.id
            WHERE p.user_id=? ORDER BY p.created_at DESC
        """, (session["user_id"],)).fetchall()
    else:
        rows = db.execute("""
            SELECT p.*,u.name as author_name,u.headline as author_headline
            FROM posts p JOIN users u ON p.user_id=u.id
            ORDER BY p.created_at DESC LIMIT 50
        """).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/posts", methods=["POST"])
@login_required
def create_post():
    content = request.form.get("content", "").strip()
    file = request.files.get("file")
    file_url = file_name = file_type = ""
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit(".", 1)[1].lower()
        fname = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(UPLOAD_FOLDER, fname))
        file_url = fname
        file_name = secure_filename(file.filename)
        file_type = ext
    if not content and not file_url:
        return jsonify({"error": "Post cannot be empty"}), 400
    db = get_db()
    db.execute("INSERT INTO posts (user_id,content,file_url,file_name,file_type) VALUES (?,?,?,?,?)",
               (session["user_id"], content, file_url, file_name, file_type))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/posts/<int:post_id>", methods=["PUT"])
@login_required
def edit_post(post_id):
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        return jsonify({"error": "Not found"}), 404
    if post["user_id"] != session["user_id"]:
        return jsonify({"error": "Unauthorized"}), 403
    content = (request.json.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Content cannot be empty"}), 400
    db.execute("UPDATE posts SET content=? WHERE id=?", (content, post_id))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/posts/<int:post_id>", methods=["DELETE"])
@login_required
def delete_post(post_id):
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        return jsonify({"error": "Not found"}), 404
    if post["user_id"] != session["user_id"]:
        return jsonify({"error": "Unauthorized"}), 403
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
    if uid in likes: likes.remove(uid)
    else: likes.append(uid)
    db.execute("UPDATE posts SET likes=? WHERE id=?", (json.dumps(likes), post_id))
    db.commit()
    return jsonify({"ok": True})
# ── USERS ──
@app.route("/api/users")
@login_required
def get_users():
    db = get_db()
    rows = db.execute("SELECT id,name,email,headline,location,skills FROM users").fetchall()
    return jsonify([row_to_dict(r) for r in rows])

# ── CONNECT ──
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
    return jsonify({"ok": True})

# ── RESUME AI ──
JOBS_DB = [
    {"id":1,"title":"Python Backend Engineer","company":"TechFlow","location":"Remote","type":"Full-time","salary":"$80-110k","skills":["python","flask","django","rest api","sql","docker"]},
    {"id":2,"title":"Frontend Developer","company":"Pixel Studio","location":"Hyderabad","type":"Full-time","salary":"₹10-18 LPA","skills":["html","css","javascript","react","typescript","vue"]},
    {"id":3,"title":"AI/ML Engineer","company":"NeuralPath","location":"Bangalore","type":"Full-time","salary":"₹20-35 LPA","skills":["python","machine learning","deep learning","pytorch","nlp","tensorflow"]},
    {"id":4,"title":"Full Stack Developer","company":"Buildify","location":"Remote","type":"Contract","salary":"$70-90/hr","skills":["javascript","node.js","react","mongodb","docker","aws"]},
    {"id":5,"title":"Data Analyst","company":"Insightful Inc","location":"Mumbai","type":"Full-time","salary":"₹8-14 LPA","skills":["python","sql","tableau","excel","statistics","power bi"]},
    {"id":6,"title":"DevOps Engineer","company":"CloudBase","location":"Remote","type":"Full-time","salary":"$90-130k","skills":["docker","kubernetes","aws","ci/cd","linux","terraform"]},
    {"id":7,"title":"Product Manager","company":"LaunchPad","location":"Hyderabad","type":"Full-time","salary":"₹22-40 LPA","skills":["product strategy","agile","roadmapping","analytics","stakeholder management"]},
    {"id":8,"title":"Cybersecurity Analyst","company":"ShieldNet","location":"Remote","type":"Full-time","salary":"$85-115k","skills":["network security","penetration testing","siem","linux","python"]},
    {"id":9,"title":"React Native Developer","company":"AppForge","location":"Remote","type":"Full-time","salary":"₹14-24 LPA","skills":["react native","javascript","ios","android","redux","typescript"]},
    {"id":10,"title":"Cloud Architect","company":"SkyScale","location":"Remote","type":"Full-time","salary":"$130-165k","skills":["aws","azure","gcp","terraform","microservices","kubernetes"]},
]

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
        resume_text = sanitize(fb.decode("utf-8", errors="ignore"))
    else:
        return jsonify({"error": "Upload PDF or TXT only"}), 400
    if not resume_text or len(resume_text) < 50:
        return jsonify({"error": "Could not read resume content"}), 400

    jobs_str = "\n".join(f"- {j['title']} at {j['company']} | {', '.join(j['skills'])}" for j in JOBS_DB)
    prompt = f"""Analyze this resume and return ONLY valid JSON with no markdown.

RESUME:
{resume_text[:3500]}

JOBS:
{jobs_str}

Return exactly:
{{
  "skills": ["skill1","skill2"],
  "profile_score": 75,
  "ai_summary": "2-3 sentence summary",
  "job_matches": [{{"job_title":"exact title","match_pct":80,"matched_skills":["s1"]}}],
  "ats": {{"overall":70,"keywords":65,"formatting":80,"readability":75,"overview":"2 sentences","suggestions":[{{"title":"Issue","detail":"Fix"}}]}},
  "gap": {{"overview":"2 sentences","missing_skills":["s1"],"strong_skills":["s2"],"roadmap":[{{"skill":"Learn X","reason":"Because Y"}}]}}
}}
Only jobs with match_pct >= 20, sorted descending."""

    try:
        msg = ai_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```json|^```|```$", "", raw, flags=re.MULTILINE).strip()
        ai_data = json.loads(raw)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    matched = []
    for m in ai_data.get("job_matches", []):
        job = next((j for j in JOBS_DB if j["title"] == m["job_title"]), None)
        if job:
            matched.append({**job, "match_pct": m["match_pct"], "matched_skills": m.get("matched_skills", [])})

    result = {
        "resume_text": resume_text[:2000],
        "skills": ai_data.get("skills", []),
        "profile_score": ai_data.get("profile_score", 70),
        "ai_summary": ai_data.get("ai_summary", ""),
        "jobs": matched,
        "ats": ai_data.get("ats", {}),
        "gap": ai_data.get("gap", {}),
    }
    db = get_db()
    skills_json = json.dumps(ai_data.get("skills", []))
    db.execute("UPDATE users SET resume_text=?,resume_analysis=?,skills=? WHERE id=?",
               (resume_text[:3000], json.dumps(result), skills_json, session["user_id"]))
    db.commit()
    return jsonify(result)

# ── STATIC UPLOADS ──
from flask import send_from_directory
@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
