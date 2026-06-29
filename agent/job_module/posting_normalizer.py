"""Job Posting Normalizer — deterministic text-based heuristic extraction.

No LLM required. Extracts title, company, location, remote policy, salary,
tech stack and requirements from free-form job posting text.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field


class JobPosting(BaseModel):
    title: str = ""
    company: str = ""
    location: Optional[str] = None
    remote_policy: str = "unknown"
    requirements: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    salary_text: Optional[str] = None
    raw_text: str = ""
    source_url: Optional[str] = None
    source_name: str = "manual"
    detected_language: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


REMOTE_KEYWORDS: dict[str, list[str]] = {
    "de": ["remote", "homeoffice", "home office", "heimarbeit", "vollständig remote"],
    "en": ["remote", "work from home", "wfh", "fully remote", "remote work"],
}

SALARY_PATTERNS = [
    r"\b(\d[\d.,]+)\s*[-–]\s*(\d[\d.,]+)\s*(€|EUR|euro|eur)\b",
    r"\b(\d[\d.,]+)\s*(€|EUR|euro|eur)\b",
    r"\b(€|EUR|euro)\s*(\d[\d.,]+)\b",
    r"\b(\d[\d.,]+)\s*[-–]\s*(\d[\d.,]+)\s*(k|K)\b",
]

TECH_KEYWORDS = [
    "python", "java", "javascript", "typescript", "react", "angular", "vue",
    "docker", "kubernetes", "aws", "azure", "gcp", "postgresql", "mysql",
    "mongodb", "redis", "flask", "django", "fastapi", "spring", "node.js",
    "nodejs", "git", "linux", "terraform", "ci/cd", "graphql", "rest api",
    "microservices", "kafka", "elasticsearch", "rust", "go", "golang", "scala",
    "jenkins", "github actions", "gitlab ci", "ansible", "prometheus",
]

GERMAN_CITIES = [
    "münchen", "berlin", "hamburg", "frankfurt", "köln", "düsseldorf",
    "stuttgart", "leipzig", "dresden", "nürnberg", "hannover", "bonn", "wien",
    "zürich", "linz", "graz", "bern", "basel",
]

PLZ_PATTERN = re.compile(r"\b\d{4,5}\b")

REQUIREMENT_SECTION_HEADERS = [
    "anforderungen", "requirements", "qualifikationen", "qualifications",
    "ihr profil", "dein profil", "voraussetzungen", "what you bring",
    "what we're looking for", "was du mitbringst", "was wir erwarten",
]


def _detect_language(text: str) -> str:
    """Very simple language detection based on common German words."""
    german_markers = ["und", "der", "die", "das", "mit", "für", "ist", "wir", "sie", "ein"]
    lower = text.lower()
    german_count = sum(1 for w in german_markers if f" {w} " in lower)
    return "de" if german_count >= 3 else "en"


def normalize_posting(
    raw_text: str,
    source_url: Optional[str] = None,
    source_name: str = "manual",
) -> JobPosting:
    """Normalize a job posting text into a structured JobPosting model.

    This is deterministic and does not call any LLM.
    """
    lines = [line.strip() for line in raw_text.strip().splitlines() if line.strip()]
    lower_text = raw_text.lower()
    lang = _detect_language(raw_text)

    # Title: first non-empty line is the title candidate
    title = lines[0] if lines else ""

    # Company: look for "bei X", "at X", "Firma:", "Company:", "Arbeitgeber:"
    company = ""
    company_patterns = [
        r"(?:bei|at|for|für|arbeitgeber:|employer:|company:|firma:)\s+([A-Z][^\n,;|]{2,40})",
        r"([A-Z][a-zA-Z ]+(?:GmbH|AG|SE|KG|LLC|Inc\.|Ltd\.|Corp\.)[^\n,;|]{0,20})",
    ]
    for pat in company_patterns:
        m = re.search(pat, raw_text)
        if m:
            company = m.group(1).strip()
            break

    # Location: PLZ or known city
    location = None
    plz_match = PLZ_PATTERN.search(raw_text)
    if plz_match:
        # Try to get surrounding context
        start = max(0, plz_match.start() - 20)
        end = min(len(raw_text), plz_match.end() + 30)
        location = raw_text[start:end].strip().replace("\n", " ")
    else:
        for city in GERMAN_CITIES:
            if city in lower_text:
                location = city.title()
                break

    # Remote policy
    remote_policy = "unknown"
    keywords = REMOTE_KEYWORDS.get(lang, REMOTE_KEYWORDS["en"])
    if any(kw in lower_text for kw in keywords):
        remote_policy = "remote"
    elif "hybrid" in lower_text or "flexibel" in lower_text:
        remote_policy = "hybrid"
    elif any(kw in lower_text for kw in ["vor ort", "onsite", "on-site", "im büro", "in office"]):
        remote_policy = "onsite"

    # Salary
    salary_text = None
    for pat in SALARY_PATTERNS:
        m = re.search(pat, raw_text, re.IGNORECASE)
        if m:
            salary_text = m.group(0)
            break

    # Tech stack detection
    tech_found = [kw for kw in TECH_KEYWORDS if kw.lower() in lower_text]
    # Remove duplicates while preserving order
    seen = set()
    tech_stack = []
    for t in tech_found:
        if t not in seen:
            seen.add(t)
            tech_stack.append(t)

    # Requirements: lines after section headers
    requirements: list[str] = []
    in_requirements = False
    for line in lines[1:]:
        lower_line = line.lower()
        if any(h in lower_line for h in REQUIREMENT_SECTION_HEADERS):
            in_requirements = True
            continue
        if in_requirements:
            # Stop at another section header or empty-ish lines
            if re.match(r"^[A-Z][a-z].{5,}:$", line) and not line.startswith("-"):
                in_requirements = False
            elif line.startswith("-") or line.startswith("•") or line.startswith("*"):
                requirements.append(line.lstrip("-•* ").strip())
            elif len(line) > 20:
                requirements.append(line)

    return JobPosting(
        title=title,
        company=company,
        location=location,
        remote_policy=remote_policy,
        requirements=requirements[:20],  # cap at 20 items
        salary_text=salary_text,
        raw_text=raw_text,
        source_url=source_url,
        source_name=source_name,
        detected_language=lang,
        metadata={"tech_stack": tech_stack},
    )
