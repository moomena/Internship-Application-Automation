"""
Gmail integration — OAuth2 setup, inbox polling, email parsing.
Reads unread emails, filters for internship-related content.
"""
import base64
import re
import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from loguru import logger

from core.database import log_to_db

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

CREDS_DIR = Path(__file__).parent.parent / "data"
TOKEN_FILE = CREDS_DIR / "gmail_token.json"
CREDS_FILE = CREDS_DIR / "gmail_credentials.json"

INTERNSHIP_KEYWORDS = [
    "internship", "intern", "summer intern", "winter intern", "apply now",
    "application open", "hiring", "job opening", "opportunity", "recruitment",
    "campus hiring", "fresher", "graduate trainee", "trainee program",
]


def get_gmail_service():
    """Return authenticated Gmail service, triggering OAuth flow if needed."""
    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_FILE.exists():
                raise FileNotFoundError(
                    f"Gmail credentials not found at {CREDS_FILE}.\n"
                    "Download OAuth2 credentials from Google Cloud Console and save as "
                    "data/gmail_credentials.json"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def is_internship_email(subject: str, body: str) -> bool:
    """Heuristic check if an email is internship-related."""
    text = (subject + " " + body).lower()
    return any(kw in text for kw in INTERNSHIP_KEYWORDS)


def decode_body(payload: dict) -> str:
    """Recursively extract plain text from Gmail message payload."""
    body = ""
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    elif mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            # Strip HTML tags roughly
            body = re.sub(r"<[^>]+>", " ", html)
            body = re.sub(r"\s+", " ", body).strip()

    elif "parts" in payload:
        for part in payload["parts"]:
            chunk = decode_body(part)
            if chunk:
                body = body + "\n" + chunk if body else chunk

    return body.strip()


def fetch_new_internship_emails(since_history_id: Optional[str] = None) -> list[dict]:
    """
    Poll Gmail for unread emails that haven't been processed yet.
    Limits results by max_emails_per_cycle setting.
    Returns list of dicts with:
    { gmail_msg_id, subject, sender, body, received_at }
    """
    from core.database import is_email_processed, get_settings
    
    try:
        service = get_gmail_service()
        settings = get_settings()
        max_emails = int(settings.get("max_emails_per_cycle", 20))
        
        # Fetch more than max to filter out already processed ones
        fetch_limit = max_emails * 2
        
        # For testing: detect all emails from last 2 days
        query = "newer_than:2d"  # Only emails from last 2 days
        if since_history_id:
            query += f" after:{since_history_id}"

        result = service.users().messages().list(
            userId="me", q=query, maxResults=fetch_limit
        ).execute()
        messages = result.get("messages", [])
        logger.info("Gmail: {} messages found in last 2 days", len(messages))
        log_to_db("INFO", "gmail", f"Fetched {len(messages)} messages from last 2 days")

        internship_emails = []
        for msg_ref in messages:
            if len(internship_emails) >= max_emails:
                logger.info("Reached max_emails_per_cycle limit: {}", max_emails)
                break
                
            msg_id = msg_ref["id"]
            
            # Skip if already processed
            if is_email_processed(msg_id):
                logger.debug("Email {} already processed, skipping", msg_id)
                continue
            
            try:
                msg = service.users().messages().get(
                    userId="me", id=msg_id, format="full"
                ).execute()

                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                subject = headers.get("subject", "(no subject)")
                sender = headers.get("from", "")
                date_str = headers.get("date", "")
                body = decode_body(msg.get("payload", {}))

                if is_internship_email(subject, body):
                    internship_emails.append({
                        "gmail_msg_id": msg_id,
                        "subject": subject,
                        "sender": sender,
                        "body": body[:8000],  # cap to avoid huge LLM prompts
                        "received_at": date_str or datetime.now().isoformat(),
                    })
                    logger.info("Internship email found: {}", subject[:80])

            except Exception as e:
                logger.warning("Could not parse message {}: {}", msg_id, e)

        logger.info("Processing {} new unprocessed internship emails (max: {})", len(internship_emails), max_emails)
        return internship_emails

    except Exception as e:
        logger.error("Gmail fetch error: {}", e)
        log_to_db("ERROR", "gmail", str(e))
        return []


def send_approval_email(
    to: str,
    company: str,
    role: str,
    match_score: float,
    deadline: str,
    screenshot_path: str,
    opp_id: int,
    dashboard_url: str = "http://localhost:8501",
) -> bool:
    """Send approval request email with application details."""
    try:
        service = get_gmail_service()

        screenshot_note = ""
        if screenshot_path and Path(screenshot_path).exists():
            screenshot_note = f"\n📸 Screenshot saved: {screenshot_path}"

        body = f"""
🤖 Internship Application Awaiting Your Approval

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏢 Company    : {company}
💼 Role       : {role}
🎯 Match Score: {match_score:.1f}%
📅 Deadline   : {deadline}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The internship agent has found a matching opportunity and pre-filled the application form.

{screenshot_note}

👉 To APPROVE or REJECT, visit your dashboard:
   {dashboard_url}

Opportunity ID: {opp_id}
        """.strip()

        message_body = (
            f"From: me\nTo: {to}\n"
            f"Subject: [Approval Needed] {role} at {company} — {match_score:.0f}% Match\n\n"
            f"{body}"
        )
        encoded = base64.urlsafe_b64encode(message_body.encode()).decode()
        service.users().messages().send(
            userId="me", body={"raw": encoded}
        ).execute()

        logger.info("Approval email sent to {} for {} at {}", to, role, company)
        log_to_db("INFO", "gmail", f"Approval email sent for opp_id={opp_id}")
        return True

    except Exception as e:
        logger.error("Failed to send approval email: {}", e)
        log_to_db("ERROR", "gmail", f"Approval email failed: {e}")
        return False


def mark_email_read(msg_id: str):
    """Mark a Gmail message as read."""
    try:
        service = get_gmail_service()
        service.users().messages().modify(
            userId="me", id=msg_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    except Exception as e:
        logger.warning("Could not mark email {} as read: {}", msg_id, e)


def check_gmail_auth() -> bool:
    """Quick check to see if Gmail auth is configured."""
    try:
        get_gmail_service()
        return True
    except Exception:
        return False
