"""
Browser automation — Playwright-based form filler.
Opens application URLs, identifies fields, auto-fills, uploads resume,
takes screenshot, and submits (when approved).
"""
import re
import asyncio
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from loguru import logger

from core.database import get_profile, log_to_db, save_form_questions
from core.llm_engine import answer_application_question

SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Common field selectors mapped to profile keys
FIELD_MAP = {
    # Name variants
    r"(full.?name|your.?name|applicant.?name|name)": "full_name",
    r"(first.?name|fname|given.?name)": "first_name",
    r"(last.?name|lname|surname|family.?name)": "last_name",
    # Contact
    r"(email|e-mail|email.?address)": "email",
    r"(phone|mobile|contact.?number|telephone|cell)": "phone",
    # Academic
    r"(university|college|institution|school)": "university",
    r"(degree|qualification|program|course)": "degree",
    r"(cgpa|gpa|grade.?point|percentage|marks)": "cgpa",
    r"(graduation|grad.?year|passing.?year|year.?of.?grad)": "graduation",
    # Links
    r"(linkedin|linked.?in)": "linkedin",
    r"(github|git.?hub)": "github",
    r"(portfolio|website|personal.?site)": "portfolio",
    # Skills
    r"(skills|technologies|tools|expertise)": "skills",
}

SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Submit")',
    'button:has-text("Apply")',
    'button:has-text("Send Application")',
    'button:has-text("Submit Application")',
    'a:has-text("Submit")',
    'a:has-text("Apply Now")',
]

FILE_UPLOAD_SELECTORS = [
    'input[type="file"]',
    'input[accept*="pdf"]',
    'input[accept*=".pdf"]',
    '[name*="resume"]',
    '[name*="cv"]',
    '[id*="resume"]',
    '[id*="cv"]',
]


def _profile_value(profile: dict, field_key: str) -> str:
    """Get string value from profile for a given field key."""
    if field_key == "first_name":
        return profile.get("full_name", "").split()[0] if profile.get("full_name") else ""
    if field_key == "last_name":
        parts = profile.get("full_name", "").split()
        return parts[-1] if len(parts) > 1 else ""
    if field_key == "skills":
        return ", ".join(profile.get("skills", []))
    return str(profile.get(field_key, "") or "")


def _match_field(label_or_name: str) -> Optional[str]:
    """Return profile key for a given field label/name, or None."""
    text = label_or_name.lower().strip()
    for pattern, key in FIELD_MAP.items():
        if re.search(pattern, text):
            return key
    return None


async def _fill_form_async(
    page,
    profile: dict,
    resume_path: Optional[str],
    opportunity: dict,
) -> dict:
    """
    Internal async function: iterate all inputs and fill them.
    Saves questions for user to answer instead of auto-filling.
    Returns {filled: int, skipped: int, questions_saved: int}
    """
    from playwright.async_api import TimeoutError as PWTimeout
    from core.database import get_conn
    opp_id = opportunity.get("id")

    filled = 0
    skipped = 0
    questions_found = []

    # Get all inputs, textareas, selects
    inputs = await page.query_selector_all(
        "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file]), "
        "textarea, select"
    )

    for inp in inputs:
        try:
            tag = (await inp.evaluate("el => el.tagName")).lower()
            itype = (await inp.get_attribute("type") or "text").lower()
            name = (await inp.get_attribute("name") or "").lower()
            placeholder = (await inp.get_attribute("placeholder") or "").lower()
            aria_label = (await inp.get_attribute("aria-label") or "").lower()

            # Try to find matching label element
            label_text = ""
            try:
                field_id = await inp.get_attribute("id")
                if field_id:
                    label_el = await page.query_selector(f'label[for="{field_id}"]')
                    if label_el:
                        label_text = (await label_el.inner_text()).lower().strip()
            except Exception:
                pass

            # Combine all hints
            hint = " ".join([name, placeholder, aria_label, label_text]).strip()
            if not hint:
                skipped += 1
                continue

            # Skip checkboxes/radio (too risky to auto-check)
            if itype in ("checkbox", "radio"):
                skipped += 1
                continue

            # --- File upload ---
            if itype == "file":
                if resume_path and Path(resume_path).exists():
                    await inp.set_input_files(resume_path)
                    filled += 1
                    logger.debug("Uploaded resume to file input")
                continue

            # --- Select dropdown ---
            if tag == "select":
                try:
                    options = await inp.query_selector_all("option")
                    profile_key = _match_field(hint)
                    if profile_key:
                        val = _profile_value(profile, profile_key).lower()
                        for opt in options:
                            opt_text = (await opt.inner_text()).lower()
                            if val[:6] in opt_text:
                                await inp.select_option(value=await opt.get_attribute("value"))
                                filled += 1
                                break
                except Exception:
                    pass
                continue

            # --- Regular text / email / tel inputs ---
            profile_key = _match_field(hint)
            if profile_key:
                value = _profile_value(profile, profile_key)
                if value:
                    await inp.fill(value)
                    filled += 1
                    continue

            # --- Long text / open-ended question (textarea) ---
            if tag == "textarea" or itype in ("text",):
                # Check if it looks like a question
                question_patterns = [
                    r"why", r"tell us", r"describe", r"explain", r"motivation",
                    r"strength", r"weakness", r"experience", r"goal", r"expect",
                    r"contribute", r"about yourself", r"cover letter", r"prefer",
                    r"why interested", r"why apply"
                ]
                if any(re.search(p, hint) for p in question_patterns):
                    # SAVE QUESTION FOR USER TO ANSWER instead of auto-filling
                    questions_found.append({
                        "question_text": label_text or placeholder or hint or "Application Question",
                        "is_required": 1
                    })
                    skipped += 1
                    continue

            skipped += 1

        except Exception as e:
            logger.debug("Field fill error (non-fatal): {}", e)
            skipped += 1

    # Save questions to database for user review
    if questions_found and opp_id:
        save_form_questions(opp_id, questions_found)
        logger.info("Saved {} questions for opportunity {} for user review", len(questions_found), opp_id)

    # Handle file uploads by alternative selectors if not caught above
    if resume_path and Path(resume_path).exists():
        for sel in FILE_UPLOAD_SELECTORS:
            try:
                file_inp = await page.query_selector(sel)
                if file_inp:
                    await file_inp.set_input_files(resume_path)
                    break
            except Exception:
                pass

    return {"filled": filled, "skipped": skipped, "questions_found": len(questions_found)}


async def _run_playwright_session(
    url: str,
    profile: dict,
    resume_path: Optional[str],
    opportunity: dict,
    do_submit: bool = False,
    opp_id: int = 0,
    request_type: str = "form",
):
    """Full Playwright session: open page, fill, screenshot, optionally submit."""
    from playwright.async_api import async_playwright

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_name = f"opp_{opp_id}_{timestamp}.png"
    screenshot_path = str(SCREENSHOTS_DIR / screenshot_name)
    result = {"success": False, "screenshot_path": screenshot_path, "log": ""}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        try:
            logger.info("Navigating to: {} (type: {})", url, request_type)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # Handle based on request type
            if request_type == "form":
                # Fill form
                stats = await _fill_form_async(page, profile, resume_path, opportunity)
                log_msg = (
                    f"Filled {stats['filled']} fields, "
                    f"saved {stats['questions_found']} questions for user review, "
                    f"skipped {stats['skipped']}"
                )
                result["log"] = log_msg
                logger.info("Form fill stats: {}", log_msg)
            else:
                # For cover_letter type, try to find file upload and upload resume
                if request_type == "cover_letter" and resume_path:
                    for sel in FILE_UPLOAD_SELECTORS:
                        try:
                            file_inp = await page.query_selector(sel)
                            if file_inp:
                                await file_inp.set_input_files(resume_path)
                                logger.info("Uploaded resume for cover letter type")
                                result["log"] = "Resume uploaded, waiting for user to add cover letter"
                                break
                        except Exception:
                            pass

            await page.wait_for_timeout(1000)

            # Screenshot before submit
            await page.screenshot(path=screenshot_path, full_page=True)
            logger.info("Screenshot saved: {}", screenshot_path)

            if do_submit:
                submitted = False
                for sel in SUBMIT_SELECTORS:
                    try:
                        btn = await page.query_selector(sel)
                        if btn and await btn.is_visible():
                            await btn.click()
                            await page.wait_for_timeout(3000)
                            # Post-submit screenshot
                            post_screenshot = screenshot_path.replace(".png", "_submitted.png")
                            await page.screenshot(path=post_screenshot, full_page=True)
                            result["post_screenshot"] = post_screenshot
                            submitted = True
                            logger.info("Form submitted via: {}", sel)
                            break
                    except Exception:
                        continue

                if not submitted:
                    result["log"] += " | WARNING: No submit button found"
                    logger.warning("No submit button found for {}", url)

            result["success"] = True

        except Exception as e:
            result["log"] = f"Error: {e}"
            logger.error("Playwright error for {}: {}", url, e)
            # Try to screenshot error state
            try:
                await page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                pass

        finally:
            await browser.close()

    return result


def prepare_application(
    url: str,
    opportunity: dict,
    resume_path: Optional[str] = None,
    opp_id: int = 0,
    request_type: str = "form",
) -> dict:
    """
    Open the application page, fill in fields, take a screenshot.
    Does NOT submit — returns screenshot path for approval.
    
    Handles request types:
    - 'form': Fill form fields
    - 'cover_letter': Just upload resume, no form filling
    - 'email': Show email address for manual send
    - 'resume_only': Just upload resume
    """
    profile = get_profile()
    if not profile:
        log_to_db("ERROR", "playwright", "No profile found — cannot fill form")
        return {"success": False, "log": "No profile configured", "screenshot_path": ""}

    # For non-form types, skip browser automation - just log and return
    if request_type in ("email", "resume_only"):
        log_to_db("INFO", "playwright", f"Request type '{request_type}' for opp_id={opp_id} — no form filling needed")
        return {
            "success": True, 
            "log": f"Request type: {request_type}. Waiting for user approval.", 
            "screenshot_path": ""
        }

    log_to_db("INFO", "playwright", f"Preparing application for opp_id={opp_id} (type: {request_type})")
    result = asyncio.run(
        _run_playwright_session(url, profile, resume_path, opportunity, do_submit=False, opp_id=opp_id, request_type=request_type)
    )
    log_to_db("INFO", "playwright", f"Preparation done: {result['log']}")
    return result


def submit_application(
    url: str,
    opportunity: dict,
    resume_path: Optional[str] = None,
    opp_id: int = 0,
    request_type: str = "form",
) -> dict:
    """
    Fill and SUBMIT the application form.
    Handles different request types: form, cover_letter, email, resume_only
    """
    profile = get_profile()
    if not profile:
        return {"success": False, "log": "No profile configured", "screenshot_path": ""}

    log_to_db("INFO", "playwright", f"Submitting application for opp_id={opp_id} (type: {request_type})")
    result = asyncio.run(
        _run_playwright_session(url, profile, resume_path, opportunity, do_submit=True, opp_id=opp_id, request_type=request_type)
    )
    log_to_db(
        "INFO" if result["success"] else "ERROR",
        "playwright",
        f"Submission result for opp_id={opp_id}: {result['log']}"
    )
    return result
