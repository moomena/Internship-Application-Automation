"""
LLM engine — uses Ollama (local, free) for:
1. Extracting structured info from internship emails.
2. Generating a match score against the user's profile.
3. Generating answers to common application questions.
"""
import json
import re
import requests
from loguru import logger

from core.database import get_settings, log_to_db


def _ollama_chat(prompt: str, system: str = "", model: str | None = None) -> str:
    settings = get_settings()
    model = model or settings.get("ollama_model", "llama3.2:3b")
    base_url = settings.get("ollama_url", "http://localhost:11434")

    payload = {
        "model": model,
        "messages": [],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 1500},
    }
    if system:
        payload["messages"].append({"role": "system", "content": system})
    payload["messages"].append({"role": "user", "content": prompt})

    resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


def _safe_json(text: str) -> dict:
    """Extract first JSON object from LLM output."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find JSON block
    match = re.search(r"\{[\s\S]+\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def extract_internship_info(email_subject: str, email_body: str) -> dict:
    """
    Ask LLM to pull structured fields out of a raw email.
    Returns dict with: company, role, deadline, eligibility,
                        required_skills, apply_link, request_type
    """
    system = (
        "You are a precise data extractor. "
        "Respond ONLY with valid JSON — no markdown fences, no commentary."
    )
    prompt = f"""Extract internship details from this email. Return ONLY a JSON object with these keys:
- company (string, company/organization name)
- role (string, internship title/position)
- deadline (string, application deadline date or "Not specified")
- eligibility (string, who can apply — year, CGPA, branch, etc.)
- required_skills (array of strings)
- apply_link (string, direct application URL or "Not found")
- request_type (string, one of: 'form', 'cover_letter', 'email', 'resume_only')

Determine request_type by analyzing what the company is asking for:
- 'form': They ask to fill an online application form
- 'cover_letter': They specifically ask for a cover letter
- 'email': They ask to send resume via email or to an email address
- 'resume_only': They only need the resume, no other documents

Email Subject: {email_subject}
Email Body:
{email_body[:4000]}

Return JSON only:"""

    try:
        raw = _ollama_chat(prompt, system)
        data = _safe_json(raw)
        # Ensure required_skills is always a list
        if isinstance(data.get("required_skills"), str):
            data["required_skills"] = [s.strip() for s in data["required_skills"].split(",") if s.strip()]
        # Ensure request_type is valid
        if data.get("request_type") not in ("form", "cover_letter", "email", "resume_only"):
            data["request_type"] = "form"  # default
        logger.debug("Extracted: {}", data)
        log_to_db("INFO", "llm", f"Extracted info for: {data.get('company')} — {data.get('role')} (type: {data.get('request_type')})")
        return data
    except Exception as e:
        logger.error("LLM extraction error: {}", e)
        log_to_db("ERROR", "llm", f"Extraction failed: {e}")
        return {
            "company": "Unknown",
            "role": email_subject[:80],
            "deadline": "Not specified",
            "eligibility": "Not specified",
            "required_skills": [],
            "apply_link": "Not found",
            "request_type": "form",
        }


def calculate_match_score(opportunity: dict, profile: dict) -> tuple[float, str]:
    """
    Returns (score: float 0-100, reason: str).
    Uses LLM to reason about fit.
    """
    system = (
        "You are an internship compatibility evaluator. "
        "Respond ONLY with valid JSON — no markdown, no extra text."
    )

    user_skills = ", ".join(profile.get("skills", []))
    req_skills = ", ".join(opportunity.get("required_skills", []))

    prompt = f"""Rate how well this candidate matches this internship. Return ONLY JSON:
{{
  "score": <integer 0-100>,
  "reason": "<2-3 sentence explanation>",
  "matching_skills": ["<skill1>", ...],
  "missing_skills": ["<skill1>", ...]
}}

CANDIDATE PROFILE:
- Name: {profile.get("full_name")}
- University: {profile.get("university")}
- Degree: {profile.get("degree")}
- CGPA: {profile.get("cgpa")}
- Skills: {user_skills}
- Graduation: {profile.get("graduation")}

INTERNSHIP:
- Company: {opportunity.get("company")}
- Role: {opportunity.get("role")}
- Eligibility: {opportunity.get("eligibility", "Not specified")}
- Required Skills: {req_skills}
- Deadline: {opportunity.get("deadline")}

Score 0–100 where:
80–100 = Excellent match (apply immediately)
60–79  = Good match (worth applying)
40–59  = Partial match (could try)
0–39   = Poor match (likely won't qualify)

Return JSON only:"""

    try:
        raw = _ollama_chat(prompt, system)
        data = _safe_json(raw)
        score = float(data.get("score", 0))
        score = max(0.0, min(100.0, score))
        reason = data.get("reason", "No explanation provided.")
        log_to_db("INFO", "llm", f"Match score {score} for {opportunity.get('company')}")
        return score, reason
    except Exception as e:
        logger.error("Match scoring error: {}", e)
        log_to_db("ERROR", "llm", f"Scoring failed: {e}")
        return 50.0, "Could not evaluate match — defaulting to 50."


def answer_application_question(question: str, profile: dict, context: str = "") -> str:
    """
    Generate a natural, profile-specific answer to an application form question.
    """
    skills = ", ".join(profile.get("skills", []))
    system = (
        "You are helping a student fill out an internship application form. "
        "Write natural, honest, concise answers (1-3 sentences). "
        "No generic filler phrases."
    )
    prompt = f"""Answer this application question for the student below.
Keep it professional, specific, and under 100 words.

STUDENT:
Name: {profile.get("full_name")}
University: {profile.get("university")} | Degree: {profile.get("degree")} | CGPA: {profile.get("cgpa")}
Skills: {skills}
GitHub: {profile.get("github", "")} | LinkedIn: {profile.get("linkedin", "")}

{f"CONTEXT: {context}" if context else ""}

QUESTION: {question}

Answer:"""

    try:
        answer = _ollama_chat(prompt, system)
        # Clean up common LLM preambles
        for prefix in ["Answer:", "A:", "Response:"]:
            if answer.startswith(prefix):
                answer = answer[len(prefix):].strip()
        return answer
    except Exception as e:
        logger.error("Answer generation error: {}", e)
        return ""


def pick_best_resume(resumes: list[dict], opportunity: dict) -> dict | None:
    """Pick the most relevant resume for a given opportunity."""
    if not resumes:
        return None
    if len(resumes) == 1:
        return resumes[0]

    req = set(s.lower() for s in opportunity.get("required_skills", []))
    best = None
    best_score = -1
    for r in resumes:
        tags = set(t.lower() for t in r.get("tags", []))
        score = len(req & tags)
        if score > best_score:
            best_score = score
            best = r
    return best or resumes[0]


def check_ollama() -> tuple[bool, str]:
    """Check if Ollama is running and model is available."""
    settings = get_settings()
    base_url = settings.get("ollama_url", "http://localhost:11434")
    model = settings.get("ollama_model", "llama3")
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        has_model = any(model in m for m in models)
        if has_model:
            return True, f"✅ Ollama running with model '{model}'"
        else:
            available = ", ".join(models) or "none"
            return False, f"⚠️ Ollama running but model '{model}' not found. Available: {available}"
    except Exception as e:
        return False, f"❌ Ollama not reachable at {base_url}: {e}"
