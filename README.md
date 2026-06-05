# 🤖 Internship Agent

Fully automated internship discovery and application system — runs locally, uses free Ollama LLM, never touches paid APIs.

---

## ✨ What It Does

| Step | What Happens |
|------|-------------|
| **📧 Email Monitor** | Checks your Gmail every 10–15 min for internship emails |
| **🧠 AI Extraction** | Ollama LLM extracts company, role, deadline, skills, apply link |
| **🎯 Match Scoring** | LLM scores your fit against the opportunity (0–100%) |
| **🤖 Form Filling** | Playwright opens the apply page and auto-fills your profile |
| **📸 Screenshot** | Captures the filled form before submitting |
| **📨 Approval Email** | Sends you a summary email with screenshot path + dashboard link |
| **✅ You Decide** | Approve or reject from the Streamlit dashboard |
| **🚀 Submission** | On approval, Playwright submits the form |
| **📁 Logging** | All actions, screenshots, timestamps saved to SQLite |

---

## 📦 Installation

### 1. Clone / download this project

```bash
cd internship_agent
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Install and start Ollama

```bash
# Install Ollama from https://ollama.com
ollama pull llama3          # recommended
# alternatives: ollama pull qwen2  |  ollama pull gemma2  |  ollama pull mistral
ollama serve                # start the server (keep this running)
```

### 4. Set up Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Enable **Gmail API**: APIs & Services → Enable APIs → search "Gmail API"
4. Create credentials: APIs & Services → Credentials → Create → **OAuth 2.0 Client ID** → **Desktop App**
5. Download the JSON file
6. Save it as `data/gmail_credentials.json`

### 5. Run the dashboard

```bash
python run.py
```

Open **http://localhost:8501** in your browser.

On first run you'll be redirected to Google OAuth — sign in with your university Gmail.

---

## 🛠 Configuration (Settings Page)

| Setting | Default | Description |
|---------|---------|-------------|
| Ollama Model | `llama3` | Local LLM model to use |
| Check Interval | `10 min` | How often to poll Gmail |
| Match Threshold | `65%` | Minimum score to process an application |
| Auto-Submit | `OFF` | Skip approval step (⚠️ use with caution) |

---

## 📁 Project Structure

```
internship_agent/
├── app.py                  # Streamlit dashboard
├── run.py                  # Launcher (DB init + Streamlit)
├── monitor_cli.py          # Headless CLI monitor
├── requirements.txt
├── core/
│   ├── database.py         # SQLite schema + all CRUD
│   ├── gmail_client.py     # Gmail API: fetch + send emails
│   ├── llm_engine.py       # Ollama: extract, score, answer questions
│   ├── browser_agent.py    # Playwright: form fill + submit
│   └── orchestrator.py     # Main pipeline + background thread
├── data/
│   ├── gmail_credentials.json   # ← YOU ADD THIS
│   ├── gmail_token.json         # auto-created on first auth
│   └── internship_agent.db      # SQLite database
├── resumes/                # Uploaded resume PDFs
├── screenshots/            # Form screenshots
└── logs/                   # Daily log files
```

---

## 🖥 Dashboard Pages

- **🏠 Dashboard** — Stats overview, pending approvals, recent activity, score chart
- **⏳ Pending Review** — Approve / reject applications with screenshots
- **📋 All Opportunities** — Search & filter all found internships
- **📁 Applications** — Full history with submission results
- **👤 Profile** — Your personal info for form autofill
- **📄 Resumes** — Upload multiple resumes with skill tags; system picks best match
- **⚙️ Settings** — Configure LLM, monitor interval, threshold, Gmail
- **📊 Logs** — Real-time system logs with filtering

---

## 🔄 Running Headless (no UI)

```bash
# One-time scan
python monitor_cli.py --once

# Continuous monitor
python monitor_cli.py
```

---

## 💡 Tips

- **Multiple resumes**: Tag each resume with skill keywords. The LLM picks the best match per opportunity.
- **Threshold tuning**: Start at 65%. Lower it if you're missing good opportunities; raise it if you get too many.
- **Model choice**: `llama3` is best overall. `qwen2` is faster. `gemma2` is good for reasoning.
- **Form coverage**: The agent fills ~70–80% of typical form fields. Complex forms (multi-step, CAPTCHAs) may need manual handling.
- **Apply links**: If no link is found in the email, the opportunity is saved for manual action.

---

## ⚠️ Limitations

- CAPTCHAs cannot be solved automatically
- Multi-step or SSO-gated forms may not complete fully
- The system reads Gmail — ensure your OAuth scopes include `gmail.modify`
- Always review screenshots before approving!

---

## 🔐 Privacy

- All data stays on your machine
- No data sent to external APIs (Ollama is local)
- Gmail token stored locally in `data/gmail_token.json`
