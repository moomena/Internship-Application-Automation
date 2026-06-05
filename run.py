#!/usr/bin/env python3
"""
Entry point: Initialize DB, start background monitor, launch Streamlit.
Usage: python run.py
"""
import subprocess
import sys
import os
from pathlib import Path

# Ensure we're in the project directory
os.chdir(Path(__file__).parent)

# Init DB
from core.database import init_db
init_db()

print("""
╔══════════════════════════════════════════════════════╗
║         🤖  INTERNSHIP AGENT  —  STARTING           ║
╚══════════════════════════════════════════════════════╝

Prerequisites:
  ✓ Ollama running:  ollama serve
  ✓ Model pulled:    ollama pull llama3
  ✓ Gmail creds:     data/gmail_credentials.json

Dashboard: http://localhost:8501
""")

# Launch Streamlit
subprocess.run([
    sys.executable, "-m", "streamlit", "run", "app.py",
    "--server.port=8501",
    "--server.headless=false",
    "--browser.gatherUsageStats=false",
    "--theme.base=dark",
    "--theme.primaryColor=#6366f1",
    "--theme.backgroundColor=#0a0e1a",
    "--theme.secondaryBackgroundColor=#111827",
    "--theme.textColor=#e2e8f0",
])
