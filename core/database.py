"""
Database layer — SQLite schema, initialization, and all CRUD helpers.
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime
from loguru import logger

DB_PATH = Path(__file__).parent.parent / "data" / "internship_agent.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS profile (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            full_name   TEXT,
            email       TEXT,
            phone       TEXT,
            cgpa        TEXT,
            university  TEXT,
            graduation  TEXT,
            degree      TEXT,
            skills      TEXT,        -- JSON list
            linkedin    TEXT,
            github      TEXT,
            portfolio   TEXT,
            extra       TEXT,        -- JSON dict for any extras
            updated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS resumes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            label       TEXT NOT NULL,
            file_path   TEXT NOT NULL,
            tags        TEXT,        -- JSON list of skill tags
            is_default  INTEGER DEFAULT 0,
            created_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS opportunities (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_msg_id    TEXT UNIQUE,
            company         TEXT,
            role            TEXT,
            deadline        TEXT,
            eligibility     TEXT,
            required_skills TEXT,    -- JSON list
            apply_link      TEXT,
            match_score     REAL,
            match_reason    TEXT,
            status          TEXT DEFAULT 'pending',
                            -- pending | approved | rejected | submitted | failed
            request_type    TEXT DEFAULT 'form',
                            -- form | cover_letter | email | resume_only
            raw_email       TEXT,
            received_at     TEXT,
            processed_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS applications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id  INTEGER REFERENCES opportunities(id),
            resume_id       INTEGER REFERENCES resumes(id),
            screenshot_path TEXT,
            approval_sent_at TEXT,
            approved_at     TEXT,
            submitted_at    TEXT,
            submission_log  TEXT,
            result          TEXT      -- success | failed | skipped
        );

        CREATE TABLE IF NOT EXISTS form_questions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id  INTEGER REFERENCES opportunities(id),
            question_text   TEXT NOT NULL,
            user_answer     TEXT,
            is_required     INTEGER DEFAULT 1,
            created_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS system_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            level       TEXT,
            module      TEXT,
            message     TEXT,
            created_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT
        );
    """)

    # Add new columns if they don't exist (migration for existing databases)
    cur.execute("PRAGMA table_info(opportunities)")
    columns = [row[1] for row in cur.fetchall()]
    if "request_type" not in columns:
        cur.execute("ALTER TABLE opportunities ADD COLUMN request_type TEXT DEFAULT 'form'")
        logger.info("Added request_type column to opportunities table")

    # Default settings
    defaults = {
        "match_threshold": "65",
        "check_interval_minutes": "10",
        "max_emails_per_cycle": "20",
        "ollama_model": "llama3.2:3b",
        "ollama_url": "http://localhost:11434",
        "auto_submit": "0",
        "monitor_active": "0",
    }
    for k, v in defaults.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
        )

    conn.commit()
    conn.close()
    logger.info("Database initialized at {}", DB_PATH)


# ── Profile ──────────────────────────────────────────────────────────────────

def get_profile() -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM profile WHERE id=1").fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["skills"] = json.loads(d["skills"] or "[]")
        d["extra"] = json.loads(d["extra"] or "{}")
        return d
    return None


def upsert_profile(data: dict):
    skills = json.dumps(data.get("skills", []))
    extra = json.dumps(data.get("extra", {}))
    conn = get_conn()
    conn.execute("""
        INSERT INTO profile (id, full_name, email, phone, cgpa, university,
            graduation, degree, skills, linkedin, github, portfolio, extra, updated_at)
        VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            full_name=excluded.full_name, email=excluded.email,
            phone=excluded.phone, cgpa=excluded.cgpa,
            university=excluded.university, graduation=excluded.graduation,
            degree=excluded.degree, skills=excluded.skills,
            linkedin=excluded.linkedin, github=excluded.github,
            portfolio=excluded.portfolio, extra=excluded.extra,
            updated_at=excluded.updated_at
    """, (
        data.get("full_name"), data.get("email"), data.get("phone"),
        data.get("cgpa"), data.get("university"), data.get("graduation"),
        data.get("degree"), skills, data.get("linkedin"),
        data.get("github"), data.get("portfolio"), extra,
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


# ── Resumes ───────────────────────────────────────────────────────────────────

def list_resumes() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM resumes ORDER BY is_default DESC, id DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d["tags"] or "[]")
        result.append(d)
    return result


def add_resume(label: str, file_path: str, tags: list, is_default: bool = False) -> int:
    conn = get_conn()
    if is_default:
        conn.execute("UPDATE resumes SET is_default=0")
    cur = conn.execute(
        "INSERT INTO resumes (label, file_path, tags, is_default, created_at) VALUES (?,?,?,?,?)",
        (label, file_path, json.dumps(tags), int(is_default), datetime.now().isoformat())
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def delete_resume(resume_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM resumes WHERE id=?", (resume_id,))
    conn.commit()
    conn.close()


def get_default_resume() -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM resumes WHERE is_default=1 LIMIT 1").fetchone()
    if not row:
        row = conn.execute("SELECT * FROM resumes ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["tags"] = json.loads(d["tags"] or "[]")
        return d
    return None


# ── Opportunities ─────────────────────────────────────────────────────────────

def save_opportunity(data: dict) -> int:
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM opportunities WHERE gmail_msg_id=?",
        (data.get("gmail_msg_id"),)
    ).fetchone()
    if existing:
        conn.close()
        return existing["id"]

    skills = json.dumps(data.get("required_skills", []))
    cur = conn.execute("""
        INSERT INTO opportunities
            (gmail_msg_id, company, role, deadline, eligibility,
             required_skills, apply_link, match_score, match_reason,
             status, request_type, raw_email, received_at, processed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.get("gmail_msg_id"), data.get("company"), data.get("role"),
        data.get("deadline"), data.get("eligibility"), skills,
        data.get("apply_link"), data.get("match_score"), data.get("match_reason"),
        data.get("status", "pending"), data.get("request_type", "form"),
        data.get("raw_email"), data.get("received_at"), datetime.now().isoformat()
    ))
    oid = cur.lastrowid
    conn.commit()
    conn.close()
    return oid


def is_email_processed(gmail_msg_id: str) -> bool:
    """Check if an email has already been processed."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM opportunities WHERE gmail_msg_id=?",
        (gmail_msg_id,)
    ).fetchone()
    conn.close()
    return row is not None


def get_opportunities(status: str | None = None) -> list[dict]:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM opportunities WHERE status=? ORDER BY received_at DESC",
            (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM opportunities ORDER BY received_at DESC"
        ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["required_skills"] = json.loads(d["required_skills"] or "[]")
        result.append(d)
    return result


def update_opportunity_status(opp_id: int, status: str):
    conn = get_conn()
    conn.execute("UPDATE opportunities SET status=? WHERE id=?", (status, opp_id))
    conn.commit()
    conn.close()


def get_opportunity(opp_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM opportunities WHERE id=?", (opp_id,)).fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["required_skills"] = json.loads(d["required_skills"] or "[]")
        return d
    return None


# ── Applications ──────────────────────────────────────────────────────────────

def save_application(data: dict) -> int:
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO applications
            (opportunity_id, resume_id, screenshot_path,
             approval_sent_at, approved_at, submitted_at, submission_log, result)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        data.get("opportunity_id"), data.get("resume_id"),
        data.get("screenshot_path"), data.get("approval_sent_at"),
        data.get("approved_at"), data.get("submitted_at"),
        data.get("submission_log"), data.get("result")
    ))
    aid = cur.lastrowid
    conn.commit()
    conn.close()
    return aid


def update_application(app_id: int, **kwargs):
    conn = get_conn()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    conn.execute(f"UPDATE applications SET {sets} WHERE id=?", (*kwargs.values(), app_id))
    conn.commit()
    conn.close()


def get_applications_with_details() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT a.*, o.company, o.role, o.match_score, o.deadline,
               o.apply_link, r.label as resume_label
        FROM applications a
        JOIN opportunities o ON a.opportunity_id = o.id
        LEFT JOIN resumes r ON a.resume_id = r.id
        ORDER BY a.id DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Form Questions ────────────────────────────────────────────────────────────

def save_form_questions(opportunity_id: int, questions: list[dict]) -> None:
    """Save form questions that need user input. Each dict has: question_text, is_required"""
    conn = get_conn()
    for q in questions:
        conn.execute(
            "INSERT INTO form_questions (opportunity_id, question_text, is_required, created_at) VALUES (?, ?, ?, ?)",
            (opportunity_id, q["question_text"], q.get("is_required", 1), datetime.now().isoformat())
        )
    conn.commit()
    conn.close()


def get_form_questions(opportunity_id: int) -> list[dict]:
    """Get all form questions for an opportunity"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, question_text, user_answer, is_required FROM form_questions WHERE opportunity_id = ? ORDER BY id",
        (opportunity_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_form_answer(question_id: int, answer: str) -> None:
    """Save user's answer to a form question"""
    conn = get_conn()
    conn.execute(
        "UPDATE form_questions SET user_answer = ? WHERE id = ?",
        (answer, question_id)
    )
    conn.commit()
    conn.close()


# ── Settings ──────────────────────────────────────────────────────────────────

def get_settings() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key: str, value: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value)
    )
    conn.commit()
    conn.close()


# ── Logs ──────────────────────────────────────────────────────────────────────

def log_to_db(level: str, module: str, message: str):
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO system_logs (level, module, message, created_at) VALUES (?,?,?,?)",
            (level, module, message, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_logs(limit: int = 200) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM system_logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
