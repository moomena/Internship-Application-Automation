"""
Orchestrator — ties everything together.
Runs the monitoring loop: fetch emails → extract info → score → approve → submit.
"""
import time
import threading
from datetime import datetime
from pathlib import Path

from loguru import logger

from core.database import (
    init_db, get_settings, set_setting, get_profile,
    save_opportunity, get_opportunities, update_opportunity_status,
    get_opportunity, get_default_resume, list_resumes,
    save_application, update_application, log_to_db
)
from core.gmail_client import (
    fetch_new_internship_emails, send_approval_email, mark_email_read, check_gmail_auth
)
from core.llm_engine import (
    extract_internship_info, calculate_match_score, pick_best_resume, check_ollama
)
from core.browser_agent import prepare_application, submit_application

# Setup file logging
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger.add(str(LOG_DIR / "agent_{time:YYYY-MM-DD}.log"), rotation="1 day", retention="14 days")

_monitor_thread: threading.Thread | None = None
_stop_event = threading.Event()


def process_email(email: dict, settings: dict, profile: dict) -> dict | None:
    """
    Full pipeline for a single email:
    extract → score → save → (if above threshold) prepare → notify
    Handles different request types: form, cover_letter, email, resume_only
    """
    threshold = float(settings.get("match_threshold", 65))
    logger.info("Processing email: {}", email.get("subject", "")[:80])

    # 1. Extract structured info
    info = extract_internship_info(email["subject"], email["body"])
    if not info.get("company") or info.get("company") == "Unknown":
        logger.info("Skipping — not a proper internship email")
        return None

    # 2. Calculate match score
    score, reason = calculate_match_score(info, profile)
    status = "pending" if score >= threshold else "rejected"

    # 3. Save opportunity
    request_type = info.get("request_type", "form")
    opp_data = {
        "gmail_msg_id": email["gmail_msg_id"],
        "company": info.get("company", "Unknown"),
        "role": info.get("role", email["subject"][:100]),
        "deadline": info.get("deadline", "Not specified"),
        "eligibility": info.get("eligibility", ""),
        "required_skills": info.get("required_skills", []),
        "apply_link": info.get("apply_link", ""),
        "match_score": score,
        "match_reason": reason,
        "status": status,
        "request_type": request_type,
        "raw_email": email.get("body", "")[:3000],
        "received_at": email.get("received_at", datetime.now().isoformat()),
    }
    opp_id = save_opportunity(opp_data)
    mark_email_read(email["gmail_msg_id"])

    if score < threshold:
        logger.info("Score {:.1f} below threshold {:.0f} — skipped", score, threshold)
        log_to_db("INFO", "orchestrator", f"Skipped opp_id={opp_id}: score {score:.1f} < {threshold}")
        return None

    logger.info("Match {:.1f}% ≥ threshold for {} at {} (type: {})", score, info.get("role"), info.get("company"), request_type)

    # 4. Pick best resume
    resumes = list_resumes()
    best_resume = pick_best_resume(resumes, info) or get_default_resume()
    resume_path = best_resume["file_path"] if best_resume else None

    apply_link = info.get("apply_link", "")
    screenshot_path = ""
    app_id = None

    if apply_link and apply_link not in ("Not found", ""):
        # 5. Prepare application (fill form, screenshot) - handle based on request type
        try:
            result = prepare_application(apply_link, info, resume_path, opp_id=opp_id, request_type=request_type)
            screenshot_path = result.get("screenshot_path", "")

            # 6. Save application record
            app_id = save_application({
                "opportunity_id": opp_id,
                "resume_id": best_resume["id"] if best_resume else None,
                "screenshot_path": screenshot_path,
                "approval_sent_at": datetime.now().isoformat(),
            })
        except Exception as e:
            logger.error("Form preparation failed: {}", e)
            log_to_db("ERROR", "orchestrator", f"Preparation failed opp_id={opp_id}: {e}")
    else:
        logger.info("No apply link found — saving for manual review")
        app_id = save_application({
            "opportunity_id": opp_id,
            "resume_id": best_resume["id"] if best_resume else None,
            "screenshot_path": "",
            "approval_sent_at": datetime.now().isoformat(),
        })

    # 7. Send approval email
    if profile.get("email"):
        send_approval_email(
            to=profile["email"],
            company=info.get("company", ""),
            role=info.get("role", ""),
            match_score=score,
            deadline=info.get("deadline", ""),
            screenshot_path=screenshot_path,
            opp_id=opp_id,
        )

    return {"opp_id": opp_id, "app_id": app_id, "score": score}


def run_monitor_cycle():
    """Single poll cycle: fetch + process new internship emails."""
    settings = get_settings()
    profile = get_profile()

    if not profile:
        logger.warning("No profile configured — skipping cycle")
        return

    logger.info("── Monitor cycle started ──")
    log_to_db("INFO", "orchestrator", "Monitor cycle started")

    emails = fetch_new_internship_emails()
    processed = 0
    for email in emails:
        try:
            result = process_email(email, settings, profile)
            if result:
                processed += 1
        except Exception as e:
            logger.error("Failed to process email {}: {}", email.get("gmail_msg_id"), e)
            log_to_db("ERROR", "orchestrator", f"Email processing error: {e}")

    logger.info("── Cycle done: {}/{} emails processed ──", processed, len(emails))
    log_to_db("INFO", "orchestrator", f"Cycle done: {processed}/{len(emails)} processed")


def submit_approved_opportunity(opp_id: int) -> bool:
    """
    Called when user approves from dashboard.
    Runs Playwright to actually submit the application.
    """
    from core.database import get_applications_with_details
    opp = get_opportunity(opp_id)
    if not opp:
        return False

    resumes = list_resumes()
    best_resume = pick_best_resume(resumes, opp) or get_default_resume()
    resume_path = best_resume["file_path"] if best_resume else None
    apply_link = opp.get("apply_link", "")
    request_type = opp.get("request_type", "form")

    # Find associated application record
    apps = [a for a in get_applications_with_details() if a["opportunity_id"] == opp_id]
    app_id = apps[0]["id"] if apps else None

    if not apply_link or apply_link == "Not found":
        update_opportunity_status(opp_id, "submitted")
        if app_id:
            update_application(app_id,
                approved_at=datetime.now().isoformat(),
                submitted_at=datetime.now().isoformat(),
                result="skipped",
                submission_log="No apply link — marked as manually handled"
            )
        log_to_db("INFO", "orchestrator", f"opp_id={opp_id} marked submitted (no link)")
        return True

    result = submit_application(apply_link, opp, resume_path, opp_id=opp_id, request_type=request_type)
    now = datetime.now().isoformat()
    outcome = "success" if result.get("success") else "failed"
    update_opportunity_status(opp_id, "submitted" if result.get("success") else "failed")
    if app_id:
        update_application(app_id,
            approved_at=now,
            submitted_at=now,
            result=outcome,
            submission_log=result.get("log", ""),
            screenshot_path=result.get("post_screenshot") or result.get("screenshot_path", ""),
        )
    log_to_db(
        "INFO" if result.get("success") else "ERROR",
        "orchestrator",
        f"Submission opp_id={opp_id}: {outcome} — {result.get('log', '')}"
    )
    return result.get("success", False)


def reject_opportunity(opp_id: int):
    update_opportunity_status(opp_id, "rejected")
    log_to_db("INFO", "orchestrator", f"opp_id={opp_id} rejected by user")


# ── Background thread ─────────────────────────────────────────────────────────

def _monitor_loop():
    global _stop_event
    while not _stop_event.is_set():
        settings = get_settings()
        interval = int(settings.get("check_interval_minutes", 10)) * 60
        try:
            run_monitor_cycle()
        except Exception as e:
            logger.error("Monitor loop error: {}", e)
        # Sleep in small chunks so we can respond to stop_event
        for _ in range(interval):
            if _stop_event.is_set():
                break
            time.sleep(1)
    logger.info("Monitor loop stopped")


def start_monitor():
    global _monitor_thread, _stop_event
    if _monitor_thread and _monitor_thread.is_alive():
        return False  # already running
    _stop_event.clear()
    set_setting("monitor_active", "1")
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True, name="InternshipMonitor")
    _monitor_thread.start()
    log_to_db("INFO", "orchestrator", "Monitor started")
    logger.info("Monitor started")
    return True


def stop_monitor():
    global _stop_event
    _stop_event.set()
    set_setting("monitor_active", "0")
    log_to_db("INFO", "orchestrator", "Monitor stopped")
    logger.info("Monitor stop requested")


def is_monitor_running() -> bool:
    return _monitor_thread is not None and _monitor_thread.is_alive()
