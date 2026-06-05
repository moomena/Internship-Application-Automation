"""
Internship Agent Dashboard — Streamlit UI
Run with: streamlit run app.py
"""
import streamlit as st
import pandas as pd
import json
import shutil
import os
from pathlib import Path
from datetime import datetime

# ── Init ──────────────────────────────────────────────────────────────────────
from core.database import init_db
init_db()

from core.database import (
    get_profile, upsert_profile, list_resumes, add_resume, delete_resume,
    get_opportunities, update_opportunity_status, get_applications_with_details,
    get_settings, set_setting, get_logs, log_to_db,
    get_form_questions, save_form_answer
)
from core.llm_engine import check_ollama
from core.gmail_client import check_gmail_auth
from core.orchestrator import (
    start_monitor, stop_monitor, is_monitor_running,
    submit_approved_opportunity, reject_opportunity, run_monitor_cycle
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Internship Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

RESUMES_DIR = Path("resumes")
RESUMES_DIR.mkdir(exist_ok=True)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
    --bg: #0a0e1a;
    --surface: #111827;
    --surface2: #1a2236;
    --accent: #6366f1;
    --accent2: #06b6d4;
    --green: #10b981;
    --red: #ef4444;
    --yellow: #f59e0b;
    --text: #e2e8f0;
    --muted: #64748b;
    --border: #1e293b;
}

.stApp {
    background: var(--bg);
    font-family: 'Space Grotesk', sans-serif;
    color: var(--text);
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1px solid var(--border);
}
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] p {
    color: var(--text) !important;
}

/* Cards */
.agent-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 24px;
    margin: 8px 0;
    transition: border-color 0.2s;
}
.agent-card:hover { border-color: var(--accent); }

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 12px;
}

.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
.badge-pending   { background: rgba(245,158,11,0.15); color: #f59e0b; }
.badge-approved  { background: rgba(99,102,241,0.15); color: #6366f1; }
.badge-submitted { background: rgba(16,185,129,0.15); color: #10b981; }
.badge-rejected  { background: rgba(239,68,68,0.12);  color: #ef4444; }
.badge-failed    { background: rgba(239,68,68,0.12);  color: #ef4444; }

.score-pill {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 99px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 14px;
    font-weight: 600;
}
.score-high   { background: rgba(16,185,129,0.15); color: #10b981; }
.score-medium { background: rgba(245,158,11,0.15); color: #f59e0b; }
.score-low    { background: rgba(239,68,68,0.12);  color: #ef4444; }

.stat-box {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 20px;
    text-align: center;
}
.stat-number {
    font-size: 32px;
    font-weight: 700;
    line-height: 1;
    margin-bottom: 4px;
}
.stat-label {
    font-size: 12px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.8px;
}

.section-title {
    font-size: 20px;
    font-weight: 600;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
}

.status-dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    display: inline-block;
}
.dot-green { background: #10b981; box-shadow: 0 0 8px #10b981; }
.dot-red   { background: #ef4444; }
.dot-yellow{ background: #f59e0b; }

.log-entry {
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    padding: 6px 12px;
    border-left: 3px solid;
    margin: 3px 0;
    background: var(--surface2);
    border-radius: 0 6px 6px 0;
    color: var(--text);
}
.log-INFO    { border-color: var(--accent2); }
.log-WARNING { border-color: var(--yellow); }
.log-ERROR   { border-color: var(--red); color: #fca5a5; }

.empty-state {
    text-align: center;
    padding: 48px 20px;
    color: var(--muted);
}
.empty-icon { font-size: 48px; margin-bottom: 12px; }

/* Override Streamlit widget colors */
.stButton > button {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 500;
}
.stTextInput input, .stTextArea textarea, .stSelectbox select {
    background: var(--surface2) !important;
    color: var(--text) !important;
    border-color: var(--border) !important;
}
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def score_class(score: float) -> str:
    if score >= 75: return "score-high"
    if score >= 55: return "score-medium"
    return "score-low"


def status_badge(status: str) -> str:
    return f'<span class="badge badge-{status}">{status}</span>'


def score_pill(score: float) -> str:
    cls = score_class(score)
    return f'<span class="score-pill {cls}">{score:.0f}%</span>'


def render_stat(value, label: str, color: str = "#6366f1"):
    st.markdown(f"""
    <div class="stat-box">
        <div class="stat-number" style="color:{color}">{value}</div>
        <div class="stat-label">{label}</div>
    </div>
    """, unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🤖 Internship Agent")
    st.markdown("---")

    page = st.selectbox(
        "Navigation",
        ["🏠 Dashboard", "⏳ Pending Review", "📋 All Opportunities",
         "📁 Applications", "👤 Profile", "📄 Resumes", "⚙️ Settings", "📊 Logs"],
        label_visibility="collapsed"
    )

    st.markdown("---")
    st.markdown("**System Status**")

    ollama_ok, ollama_msg = check_ollama()
    gmail_ok = check_gmail_auth()
    monitor_running = is_monitor_running()

    col1, col2 = st.columns([1, 4])
    col1.markdown(f'<span class="status-dot {"dot-green" if ollama_ok else "dot-red"}"></span>', unsafe_allow_html=True)
    col2.markdown("Ollama LLM")

    col1, col2 = st.columns([1, 4])
    col1.markdown(f'<span class="status-dot {"dot-green" if gmail_ok else "dot-red"}"></span>', unsafe_allow_html=True)
    col2.markdown("Gmail API")

    col1, col2 = st.columns([1, 4])
    col1.markdown(f'<span class="status-dot {"dot-green" if monitor_running else "dot-yellow"}"></span>', unsafe_allow_html=True)
    col2.markdown("Monitor")

    st.markdown("---")
    if monitor_running:
        if st.button("⏹ Stop Monitor", use_container_width=True):
            stop_monitor()
            st.rerun()
    else:
        if st.button("▶ Start Monitor", use_container_width=True, type="primary"):
            if not gmail_ok:
                st.error("Gmail not connected!")
            elif not ollama_ok:
                st.error("Ollama not running!")
            else:
                start_monitor()
                st.rerun()

    if st.button("🔄 Manual Scan Now", use_container_width=True):
        with st.spinner("Scanning Gmail..."):
            run_monitor_cycle()
        st.success("Scan complete!")
        st.rerun()


# ── Pages ─────────────────────────────────────────────────────────────────────

# ===== DASHBOARD =====
if page == "🏠 Dashboard":
    st.markdown('<div class="section-title">🏠 Dashboard Overview</div>', unsafe_allow_html=True)

    opps = get_opportunities()
    apps = get_applications_with_details()

    pending = [o for o in opps if o["status"] == "pending"]
    submitted = [o for o in opps if o["status"] == "submitted"]
    rejected = [o for o in opps if o["status"] == "rejected"]
    avg_score = sum(o["match_score"] or 0 for o in opps) / len(opps) if opps else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: render_stat(len(opps), "Total Found", "#6366f1")
    with c2: render_stat(len(pending), "Pending Review", "#f59e0b")
    with c3: render_stat(len(submitted), "Submitted", "#10b981")
    with c4: render_stat(len(rejected), "Rejected", "#ef4444")
    with c5: render_stat(f"{avg_score:.0f}%", "Avg Match Score", "#06b6d4")

    st.markdown("---")

    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.markdown("#### ⏳ Awaiting Your Approval")
        if pending:
            for opp in pending[:5]:
                score = opp["match_score"] or 0
                st.markdown(f"""
                <div class="agent-card">
                    <div class="card-header">
                        <div>
                            <strong style="font-size:16px">{opp['company']}</strong>
                            <span style="color:#94a3b8; margin-left:8px">→</span>
                            <span style="color:#e2e8f0"> {opp['role']}</span>
                        </div>
                        {score_pill(score)}
                    </div>
                    <div style="font-size:13px; color:#64748b">
                        📅 {opp['deadline'] or 'No deadline'} &nbsp;|&nbsp;
                        📨 {(opp['received_at'] or '')[:10]}
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown('<div class="empty-state"><div class="empty-icon">✅</div>No pending approvals</div>', unsafe_allow_html=True)

    with col_right:
        st.markdown("#### 🕒 Recent Activity")
        logs = get_logs(limit=8)
        for log in logs:
            lvl = log["level"]
            icon = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌"}.get(lvl, "•")
            ts = (log["created_at"] or "")[:16]
            st.markdown(f"""
            <div class="log-entry log-{lvl}">
                {icon} <span style="opacity:.6">{ts}</span> {log['message'][:70]}
            </div>
            """, unsafe_allow_html=True)
        if not logs:
            st.markdown('<p style="color:#64748b;font-size:13px">No logs yet.</p>', unsafe_allow_html=True)

    # Chart: scores
    if opps:
        st.markdown("---")
        st.markdown("#### 📈 Match Score Distribution")
        scores = [o["match_score"] or 0 for o in opps if o["match_score"] is not None]
        if scores:
            import collections
            buckets = {"0–40": 0, "40–60": 0, "60–75": 0, "75–100": 0}
            for s in scores:
                if s < 40: buckets["0–40"] += 1
                elif s < 60: buckets["40–60"] += 1
                elif s < 75: buckets["60–75"] += 1
                else: buckets["75–100"] += 1
            df_scores = pd.DataFrame({"Range": list(buckets.keys()), "Count": list(buckets.values())})
            st.bar_chart(df_scores.set_index("Range"))


# ===== PENDING REVIEW =====
elif page == "⏳ Pending Review":
    st.markdown('<div class="section-title">⏳ Pending Approval</div>', unsafe_allow_html=True)
    pending = get_opportunities(status="pending")

    if not pending:
        st.markdown("""
        <div class="empty-state">
            <div class="empty-icon">🎉</div>
            <strong>All clear!</strong><br>
            <span style="color:#64748b">No applications waiting for review.</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.write(f"✅ Found {len(pending)} pending opportunities")
        st.divider()
        
        for opp in pending:
            score = opp["match_score"] or 0
            
            # Card header
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"### {opp['company']} • {opp['role']}")
            with col2:
                st.markdown(f"{score_pill(score)}", unsafe_allow_html=True)
            
            # Card details
            request_type = opp.get("request_type", "form")
            request_icon = {
                "form": "📝",
                "cover_letter": "📄",
                "email": "📧",
                "resume_only": "📎"
            }.get(request_type, "📝")
            
            st.markdown(f"""
            📅 **Deadline:** {opp['deadline'] or 'Not specified'}  
            🎓 **Eligibility:** {opp['eligibility'] or 'Open eligibility'}  
            📨 **Received:** {(opp['received_at'] or '')[:10]}  
            {request_icon} **Request Type:** {request_type.replace('_', ' ').title()}
            """)
            
            # Skills
            if opp.get("required_skills"):
                st.write("**Required Skills:**")
                skills_text = ", ".join(opp["required_skills"][:8])
                st.write(skills_text)
            
            # Match reason
            if opp.get("match_reason"):
                st.info(f"💡 {opp['match_reason']}")
            
            # Details expander
            with st.expander("📋 View Full Details & Screenshot"):
                if opp.get("apply_link") and opp["apply_link"] != "Not found":
                    st.markdown(f"**Apply Link:** [{opp['apply_link'][:70]}...]({opp['apply_link']})")

                apps = get_applications_with_details()
                app = next((a for a in apps if a["opportunity_id"] == opp["id"]), None)
                if app and app.get("screenshot_path"):
                    sp = Path(app["screenshot_path"])
                    if sp.exists():
                        st.image(str(sp), caption="Pre-filled form screenshot", use_container_width=True)
                    else:
                        st.info("Screenshot file not found at " + str(sp))
                else:
                    st.info("No screenshot available (no apply link or form prep not completed).")

                if opp.get("raw_email"):
                    st.text_area("Raw Email Snippet", opp["raw_email"][:1500], height=150, disabled=True)
            
            # ACTION BUTTONS
            st.write("**Take Action:**")
            
            # Check for form questions
            questions = get_form_questions(opp["id"])
            
            if questions:
                st.warning(f"⚠️ This application has {len(questions)} question(s) that need your input")
                st.write("**Please answer these before approving:**")
                
                with st.form(key=f"form_questions_{opp['id']}"):
                    for q in questions:
                        st.text_area(
                            label=q["question_text"],
                            value=q.get("user_answer", ""),
                            key=f"answer_{q['id']}",
                            height=100
                        )
                    
                    form_submitted = st.form_submit_button("💾 Save Answers", use_container_width=True)
                    if form_submitted:
                        for q in questions:
                            answer = st.session_state.get(f"answer_{q['id']}", "")
                            save_form_answer(q["id"], answer)
                        st.success("✅ Answers saved!")
                        st.rerun()
                
                st.divider()
            
            col_a, col_r, col_space = st.columns([1, 1, 3])
            
            approve_key = f"approve_btn_{opp['id']}"
            reject_key = f"reject_btn_{opp['id']}"
            
            with col_a:
                btn_approve = st.button(
                    "✅ Approve & Submit",
                    key=approve_key,
                    use_container_width=True,
                    type="primary"
                )
                if btn_approve:
                    if questions and any(not q.get("user_answer") for q in get_form_questions(opp["id"])):
                        st.error("❌ Please answer all questions before submitting!")
                    else:
                        st.write(f"Processing approval for ID: {opp['id']}...")
                        with st.spinner("Submitting application..."):
                            ok = submit_approved_opportunity(opp["id"])
                        if ok:
                            st.success("✅ Approved and submitted!")
                            st.rerun()
                        else:
                            st.error("❌ Submission failed. Check logs.")
            
            with col_r:
                btn_reject = st.button(
                    "❌ Reject",
                    key=reject_key,
                    use_container_width=True
                )
                if btn_reject:
                    st.write(f"Rejecting ID: {opp['id']}...")
                    reject_opportunity(opp["id"])
                    st.success("❌ Opportunity rejected.")
                    st.rerun()
            
            st.divider()


# ===== ALL OPPORTUNITIES =====
elif page == "📋 All Opportunities":
    st.markdown('<div class="section-title">📋 All Opportunities</div>', unsafe_allow_html=True)

    col_f1, col_f2 = st.columns([3, 1])
    with col_f1:
        search = st.text_input("Search by company or role...", placeholder="e.g. Google, Software Engineer")
    with col_f2:
        status_filter = st.selectbox("Filter by status", ["All", "pending", "approved", "submitted", "rejected", "failed"])

    opps = get_opportunities(None if status_filter == "All" else status_filter)
    if search:
        q = search.lower()
        opps = [o for o in opps if q in (o.get("company") or "").lower() or q in (o.get("role") or "").lower()]

    if not opps:
        st.markdown('<div class="empty-state"><div class="empty-icon">🔍</div>No opportunities found.</div>', unsafe_allow_html=True)
    else:
        df = pd.DataFrame([{
            "ID": o["id"],
            "Company": o.get("company", ""),
            "Role": o.get("role", ""),
            "Match %": f"{o.get('match_score') or 0:.0f}%",
            "Deadline": o.get("deadline", ""),
            "Status": o.get("status", ""),
            "Received": (o.get("received_at") or "")[:10],
        } for o in opps])
        st.dataframe(df, use_container_width=True, hide_index=True)


# ===== APPLICATIONS =====
elif page == "📁 Applications":
    st.markdown('<div class="section-title">📁 Application History</div>', unsafe_allow_html=True)
    apps = get_applications_with_details()

    if not apps:
        st.markdown('<div class="empty-state"><div class="empty-icon">📭</div>No applications yet.</div>', unsafe_allow_html=True)
    else:
        for app in apps:
            result_icon = {"success": "✅", "failed": "❌", "skipped": "⏭"}.get(app.get("result") or "", "🔄")
            st.markdown(f"""
            <div class="agent-card">
                <div class="card-header">
                    <div>
                        {result_icon} <strong>{app['company']}</strong>
                        <span style="color:#94a3b8"> · {app['role']}</span>
                    </div>
                    <div>
                        {score_pill(app.get('match_score') or 0)}
                        &nbsp;{status_badge(app.get('result') or 'pending')}
                    </div>
                </div>
                <div style="font-size:12px;color:#64748b">
                    Resume: {app.get('resume_label') or '—'} &nbsp;|&nbsp;
                    Sent: {(app.get('approval_sent_at') or '')[:16]} &nbsp;|&nbsp;
                    Submitted: {(app.get('submitted_at') or '—')[:16]}
                </div>
            </div>
            """, unsafe_allow_html=True)

            with st.expander("Details"):
                if app.get("submission_log"):
                    st.code(app["submission_log"])
                sp = app.get("screenshot_path")
                if sp and Path(sp).exists():
                    st.image(sp, caption="Submission screenshot", use_container_width=True)

        st.markdown("---")
        df = pd.DataFrame([{
            "Company": a["company"], "Role": a["role"],
            "Score": f"{a.get('match_score') or 0:.0f}%",
            "Result": a.get("result") or "—",
            "Submitted": (a.get("submitted_at") or "")[:16],
        } for a in apps])
        csv = df.to_csv(index=False)
        st.download_button("⬇ Export CSV", csv, "applications.csv", "text/csv")


# ===== PROFILE =====
elif page == "👤 Profile":
    st.markdown('<div class="section-title">👤 Your Profile</div>', unsafe_allow_html=True)
    st.caption("This information is used to auto-fill application forms.")

    profile = get_profile() or {}

    with st.form("profile_form"):
        col1, col2 = st.columns(2)
        with col1:
            full_name = st.text_input("Full Name *", value=profile.get("full_name", ""))
            email = st.text_input("University Email *", value=profile.get("email", ""))
            phone = st.text_input("Phone Number", value=profile.get("phone", ""))
            cgpa = st.text_input("CGPA / Percentage", value=profile.get("cgpa", ""))
        with col2:
            university = st.text_input("University *", value=profile.get("university", ""))
            degree = st.text_input("Degree / Program", value=profile.get("degree", ""), placeholder="e.g. B.Sc Computer Science")
            graduation = st.text_input("Expected Graduation Year", value=profile.get("graduation", ""), placeholder="e.g. 2026")

        st.markdown("##### 🔗 Links")
        col3, col4, col5 = st.columns(3)
        with col3:
            linkedin = st.text_input("LinkedIn URL", value=profile.get("linkedin", ""))
        with col4:
            github = st.text_input("GitHub URL", value=profile.get("github", ""))
        with col5:
            portfolio = st.text_input("Portfolio / Website", value=profile.get("portfolio", ""))

        st.markdown("##### 🛠 Skills")
        skills_str = st.text_area(
            "Skills (comma-separated)",
            value=", ".join(profile.get("skills", [])),
            placeholder="Python, React, SQL, Machine Learning, ...",
            height=80
        )

        submitted = st.form_submit_button("💾 Save Profile", type="primary", use_container_width=True)

        if submitted:
            if not full_name or not email:
                st.error("Name and email are required.")
            else:
                skills = [s.strip() for s in skills_str.split(",") if s.strip()]
                upsert_profile({
                    "full_name": full_name, "email": email, "phone": phone,
                    "cgpa": cgpa, "university": university, "degree": degree,
                    "graduation": graduation, "linkedin": linkedin,
                    "github": github, "portfolio": portfolio, "skills": skills,
                })
                st.success("✅ Profile saved!")
                log_to_db("INFO", "dashboard", "Profile updated")


# ===== RESUMES =====
elif page == "📄 Resumes":
    st.markdown('<div class="section-title">📄 Resume Management</div>', unsafe_allow_html=True)

    resumes = list_resumes()
    if resumes:
        for r in resumes:
            col1, col2, col3 = st.columns([5, 2, 1])
            with col1:
                default_badge = "⭐ DEFAULT &nbsp;" if r["is_default"] else ""
                tags = " ".join(
                    f'<span style="background:#1e293b;border:1px solid #334155;padding:1px 6px;border-radius:4px;font-size:10px">{t}</span>'
                    for t in r.get("tags", [])[:6]
                )
                st.markdown(f"""
                <div class="agent-card" style="padding:12px 16px">
                    <strong>{default_badge}{r['label']}</strong>
                    <div style="font-size:11px;color:#64748b;margin-top:4px">
                        📁 {r['file_path']} &nbsp;|&nbsp; Added: {(r.get('created_at') or '')[:10]}
                    </div>
                    <div style="margin-top:6px">{tags}</div>
                </div>
                """, unsafe_allow_html=True)
            with col2:
                if not r["is_default"]:
                    if st.button("⭐ Set Default", key=f"def_{r['id']}"):
                        from core.database import get_conn
                        conn = get_conn()
                        conn.execute("UPDATE resumes SET is_default=0")
                        conn.execute("UPDATE resumes SET is_default=1 WHERE id=?", (r["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()
            with col3:
                if st.button("🗑", key=f"del_{r['id']}"):
                    delete_resume(r["id"])
                    st.rerun()
    else:
        st.info("No resumes uploaded yet.")

    st.markdown("---")
    st.markdown("#### ➕ Upload New Resume")
    with st.form("resume_form"):
        label = st.text_input("Label", placeholder="e.g. ML/Data Science Resume, Frontend Dev Resume")
        tags_str = st.text_input("Skill Tags (comma-separated)", placeholder="Python, TensorFlow, ML, Data Analysis")
        is_default = st.checkbox("Set as default resume")
        resume_file = st.file_uploader("Upload PDF Resume", type=["pdf"])

        if st.form_submit_button("📤 Upload Resume", type="primary"):
            if not label:
                st.error("Please add a label.")
            elif not resume_file:
                st.error("Please select a PDF file.")
            else:
                save_path = RESUMES_DIR / resume_file.name
                with open(save_path, "wb") as f:
                    f.write(resume_file.read())
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                add_resume(label, str(save_path), tags, is_default)
                st.success(f"✅ Resume '{label}' uploaded!")
                log_to_db("INFO", "dashboard", f"Resume added: {label}")
                st.rerun()


# ===== SETTINGS =====
elif page == "⚙️ Settings":
    st.markdown('<div class="section-title">⚙️ Configuration</div>', unsafe_allow_html=True)

    settings = get_settings()

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 🤖 AI Model")
        st.info(ollama_msg)
        model = st.text_input("Ollama Model", value=settings.get("ollama_model", "llama3"),
                               help="e.g. llama3, qwen2, gemma2, mistral")
        ollama_url = st.text_input("Ollama URL", value=settings.get("ollama_url", "http://localhost:11434"))

        st.markdown("#### ⏱ Monitor Settings")
        interval = st.slider("Check Interval (minutes)", 5, 60,
                              value=int(settings.get("check_interval_minutes", 10)))
        max_emails = st.slider("Max Emails per Cycle", 5, 50,
                                value=int(settings.get("max_emails_per_cycle", 20)),
                                help="Limit how many emails to process per monitoring cycle (prevents reprocessing)")
        threshold = st.slider("Match Score Threshold (%)", 10, 95,
                               value=int(settings.get("match_threshold", 65)),
                               help="Only process opportunities above this score")

    with col2:
        st.markdown("#### 📧 Gmail Setup")
        creds_path = Path("data/gmail_credentials.json")
        if creds_path.exists():
            st.success("✅ gmail_credentials.json found")
        else:
            st.error("❌ gmail_credentials.json not found")
            st.markdown("""
            **Setup steps:**
            1. Go to [Google Cloud Console](https://console.cloud.google.com)
            2. Create a project → Enable Gmail API
            3. Create OAuth2 credentials (Desktop App)
            4. Download JSON → save as `data/gmail_credentials.json`
            5. Run a manual scan to trigger auth flow
            """)
        creds_file = st.file_uploader("Upload gmail_credentials.json", type=["json"])
        if creds_file:
            Path("data").mkdir(exist_ok=True)
            with open("data/gmail_credentials.json", "wb") as f:
                f.write(creds_file.read())
            st.success("Credentials saved! Run a scan to authenticate.")

        st.markdown("#### 🤖 Auto-Submit")
        st.warning("⚠️ Enabling this submits applications automatically without your approval.")
        auto_submit = st.checkbox("Enable auto-submit (skip approval step)",
                                   value=settings.get("auto_submit") == "1")

    if st.button("💾 Save Settings", type="primary"):
        set_setting("ollama_model", model)
        set_setting("ollama_url", ollama_url)
        set_setting("check_interval_minutes", str(interval))
        set_setting("max_emails_per_cycle", str(max_emails))
        set_setting("match_threshold", str(threshold))
        set_setting("auto_submit", "1" if auto_submit else "0")
        st.success("✅ Settings saved!")
        log_to_db("INFO", "dashboard", "Settings updated")


# ===== LOGS =====
elif page == "📊 Logs":
    st.markdown('<div class="section-title">📊 System Logs</div>', unsafe_allow_html=True)

    col1, col2 = st.columns([2, 1])
    with col1:
        level_filter = st.selectbox("Filter level", ["All", "INFO", "WARNING", "ERROR"])
    with col2:
        limit = st.selectbox("Show last", [50, 100, 200, 500], index=1)

    logs = get_logs(limit=limit)
    if level_filter != "All":
        logs = [l for l in logs if l["level"] == level_filter]

    if not logs:
        st.markdown('<div class="empty-state"><div class="empty-icon">📋</div>No logs found.</div>', unsafe_allow_html=True)
    else:
        for log in logs:
            lvl = log["level"]
            icon = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌"}.get(lvl, "•")
            ts = (log.get("created_at") or "")[:19]
            module = log.get("module") or ""
            msg = log.get("message") or ""
            st.markdown(f"""
            <div class="log-entry log-{lvl}">
                {icon} <code style="font-size:11px;opacity:.6">{ts} [{module}]</code> {msg}
            </div>
            """, unsafe_allow_html=True)

        df = pd.DataFrame([{
            "Time": l.get("created_at", "")[:19],
            "Level": l.get("level", ""),
            "Module": l.get("module", ""),
            "Message": l.get("message", ""),
        } for l in logs])
        st.download_button("⬇ Export Logs CSV", df.to_csv(index=False), "logs.csv", "text/csv")
