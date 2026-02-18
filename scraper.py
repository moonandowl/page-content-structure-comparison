"""
Page Structure Comparison Tool - Scraper Module
Scrapes URLs and extracts structure, content elements, and above-the-fold data.
"""

import re
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Realistic browser user agent
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 2

# Elements to exclude from visible text (nav, header, footer, etc.)
EXCLUDE_SELECTORS = [
    "nav", "header", "footer", "[role='navigation']",
    ".navbar", ".nav", ".header", ".footer", ".menu",
    ".cookie-banner", ".cookie-consent", "#cookie",
    "script", "style", "noscript", "iframe",
]


def _get_visible_text(soup: BeautifulSoup, exclude_selectors: Optional[List[str]] = None) -> str:
    """Extract visible text, excluding nav/header/footer/cookie banners."""
    selectors = exclude_selectors or EXCLUDE_SELECTORS
    work = soup.select_one("body") or soup
    if not work:
        return ""

    # Clone and remove excluded elements
    for sel in selectors:
        for el in work.select(sel):
            el.decompose()
    return work.get_text(separator=" ", strip=True)


def _word_count(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def _normalize_url(url: str) -> str:
    """Normalize URL for internal link detection."""
    return url.strip().rstrip("/")


def _is_internal_link(href: str, base_domain: str) -> bool:
    """Check if link is internal to the same domain."""
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return False
    if href.startswith("/"):
        return True
    try:
        parsed = urlparse(href)
        return parsed.netloc == "" or parsed.netloc == base_domain
    except Exception:
        return False


def _get_domain(url: str) -> str:
    """Extract domain from URL."""
    parsed = urlparse(url)
    netloc = parsed.netloc or ""
    if netloc.startswith("www."):
        return netloc[4:]
    return netloc


def _detect_element_position(word_count_total: int, element_position_chars: int) -> str:
    """Determine if element is early/mid/late on page (by character position proxy)."""
    if word_count_total < 10:
        return "early"
    threshold = word_count_total * 5  # approx chars per word
    if element_position_chars < threshold * 0.33:
        return "early"
    if element_position_chars < threshold * 0.66:
        return "mid"
    return "late"


def _extract_above_fold_mobile(soup: BeautifulSoup, visible_text: str) -> dict:
    """
    Simulate mobile viewport (390px) - heuristic approach.
    Hero = first substantial content block (before scroll).
    """
    hero = {
        "headline": "",
        "subheadline": "",
        "cta_text": "",
        "has_video": False,
        "has_background_image": False,
        "has_trust_badge": False,
    }
    body = soup.select_one("body")
    if not body:
        return hero

    # Look for hero-like structures: first h1, first section, first .hero, etc.
    h1 = soup.find("h1")
    if h1:
        hero["headline"] = h1.get_text(strip=True)

    # First substantial paragraph or div after h1
    candidates = body.find_all(["p", "h2", "div"], limit=20)
    for el in candidates:
        text = el.get_text(strip=True)
        if text and len(text) > 20 and el.name != "h1":
            if not hero["subheadline"] and el.name in ("p", "div"):
                hero["subheadline"] = text[:200]
            break

    # CTA buttons in first 1500 chars of HTML (roughly above fold)
    html_str = str(body)[:3000]
    for a in soup.find_all(["a", "button"]):
        cls = " ".join(a.get("class", []))
        text = a.get_text(strip=True)
        if text and any(x in cls.lower() for x in ["btn", "button", "cta", "schedule", "consult"]):
            hero["cta_text"] = hero["cta_text"] or text
            break
    if not hero["cta_text"]:
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            if text and len(text) < 50 and any(w in text.lower() for w in ["schedule", "consult", "book", "get started", "learn more"]):
                hero["cta_text"] = text
                break

    # Video in hero area
    for tag in soup.find_all(["video", "iframe"]):
        src = tag.get("src", "") or ""
        if "youtube" in src or "vimeo" in src or tag.name == "video":
            hero["has_video"] = True
            break

    # Background image
    for tag in soup.find_all(style=True):
        style = tag.get("style", "")
        if "background-image" in style or "background:" in style:
            hero["has_background_image"] = True
            break
    for tag in soup.find_all(class_=re.compile(r"hero|banner|header", re.I)):
        if tag.get("style", "") or tag.find(["img", "video"]):
            hero["has_background_image"] = True
            break

    # Trust badge / credential in hero area
    trust_patterns = ["certified", "accredited", "award", "years", "board", " fellowship", "md", "do"]
    first_block = str(body)[:4000]
    if any(p in first_block.lower() for p in trust_patterns):
        hero["has_trust_badge"] = True

    return hero


def _extract_content_elements(
    soup: BeautifulSoup,
    visible_text: str,
    tech_keywords: List[str],
    base_url: str,
) -> dict:
    """Detect presence and position of content elements."""
    text_lower = visible_text.lower()
    html_lower = str(soup).lower()
    word_total = _word_count(visible_text)
    char_pos = 0

    def position_for_text(search: str) -> str:
        idx = text_lower.find(search.lower())
        if idx < 0:
            return "mid"
        return _detect_element_position(word_total, idx)

    elements = {
        "cta_buttons": {"present": False, "position": "", "texts": []},
        "video_embed": {"present": False, "position": ""},
        "faq_section": {"present": False, "position": "", "count": 0},
        "testimonials": {"present": False, "position": "", "type": ""},
        "cost_pricing": {"present": False, "position": ""},
        "candidacy_quiz": {"present": False, "position": ""},
        "before_after_photos": {"present": False, "position": ""},
        "surgeon_credentials": {"present": False, "position": "", "exact_text": ""},
        "technology_names": {"present": False, "position": "", "found": []},
        "financing": {"present": False, "position": ""},
        "outcome_statistics": {"present": False, "position": "", "claims": []},
        "trust_badges": {"present": False, "position": ""},
        "press_mentions": {"present": False, "position": ""},
        "live_chat": {"present": False, "position": ""},
        "online_scheduling": {"present": False, "position": ""},
        "google_review_widget": {"present": False, "position": ""},
        "video_testimonials": {"present": False, "position": "", "count": 0},
    }

    # CTA buttons
    for a in soup.find_all(["a", "button"]):
        t = a.get_text(strip=True)
        if t and 3 < len(t) < 80:
            elements["cta_buttons"]["texts"].append(t)
    if elements["cta_buttons"]["texts"]:
        elements["cta_buttons"]["present"] = True
        elements["cta_buttons"]["position"] = position_for_text(elements["cta_buttons"]["texts"][0])

    # Video embed
    if "youtube" in html_lower or "vimeo" in html_lower or soup.find("video"):
        elements["video_embed"]["present"] = True
        elements["video_embed"]["position"] = "mid"

    # FAQ - common patterns
    faq_patterns = ["faq", "frequently asked", "common questions", "q&a", "questions and answers"]
    for p in faq_patterns:
        if p in text_lower:
            elements["faq_section"]["present"] = True
            elements["faq_section"]["position"] = position_for_text(p)
            # Count questions (rough)
            elements["faq_section"]["count"] = len(re.findall(r"\?", visible_text))
            break
    # Also check for details/summary or accordion
    if soup.find_all(["details", "[data-faq]", ".faq"]):
        elements["faq_section"]["present"] = True
        elements["faq_section"]["count"] = max(elements["faq_section"]["count"], len(soup.find_all("details")))

    # Testimonials
    testimonial_signals = ["testimonial", "review", "patient story", "what our patients", "google reviews", "realself", "healthgrades"]
    for s in testimonial_signals:
        if s in text_lower:
            elements["testimonials"]["present"] = True
            elements["testimonials"]["position"] = position_for_text(s)
            if "video" in s or soup.find_all(class_=re.compile("video.*testimonial|testimonial.*video", re.I)):
                elements["testimonials"]["type"] = "video"
            elif "google" in s or "realself" in s or "healthgrades" in s:
                elements["testimonials"]["type"] = "third-party embed"
            elif "star" in text_lower or "rating" in text_lower:
                elements["testimonials"]["type"] = "star rating widget"
            else:
                elements["testimonials"]["type"] = "text quote"
            break

    # Cost/pricing
    cost_signals = ["cost", "price", "pricing", "$", "affordable", "investment", "financing"]
    for s in cost_signals:
        if s in text_lower:
            elements["cost_pricing"]["present"] = True
            elements["cost_pricing"]["position"] = position_for_text(s)
            break

    # Candidacy quiz
    quiz_signals = ["candidate", "candidacy", "quiz", "self-test", "am i a candidate", "find out if"]
    for s in quiz_signals:
        if s in text_lower:
            elements["candidacy_quiz"]["present"] = True
            elements["candidacy_quiz"]["position"] = position_for_text(s)
            break

    # Before/after
    ba_signals = ["before and after", "before & after", "before/after", "results gallery"]
    for s in ba_signals:
        if s in text_lower:
            elements["before_after_photos"]["present"] = True
            elements["before_after_photos"]["position"] = position_for_text(s)
            break

    # Surgeon credentials - capture exact text
    credential_signals = ["fellowship", "years of experience", "board certified", "procedure count", "surgeon", "dr.", "md", "credentials"]
    for tag in soup.find_all(["p", "div", "span", "li"]):
        t = tag.get_text(strip=True)
        if any(s in t.lower() for s in credential_signals) and 20 < len(t) < 500:
            elements["surgeon_credentials"]["present"] = True
            elements["surgeon_credentials"]["exact_text"] = (elements["surgeon_credentials"]["exact_text"] + " | " + t).strip(" | ")
            elements["surgeon_credentials"]["position"] = position_for_text(t[:50])
            break

    # Technology names from config
    found_tech = []
    for kw in tech_keywords:
        if kw.lower() in text_lower or kw in visible_text:
            found_tech.append(kw)
    if found_tech:
        elements["technology_names"]["present"] = True
        elements["technology_names"]["found"] = found_tech
        elements["technology_names"]["position"] = "mid"

    # Financing
    fin_signals = ["financing", "payment plan", "carecredit", "afford", "monthly"]
    for s in fin_signals:
        if s in text_lower:
            elements["financing"]["present"] = True
            elements["financing"]["position"] = position_for_text(s)
            break

    # Outcome statistics - capture exact claim
    stat_patterns = [
        r"\d+%\s+of\s+patients.*?(?:\.|achieved|vision)",
        r"\d+\%\s+.*?20/20",
        r"over\s+\d+,?\d*\s+procedures",
        r"\d+\+\s+years",
    ]
    for pat in stat_patterns:
        for m in re.finditer(pat, visible_text, re.I):
            elements["outcome_statistics"]["present"] = True
            elements["outcome_statistics"]["claims"].append(m.group(0).strip())
    elements["outcome_statistics"]["claims"] = list(set(elements["outcome_statistics"]["claims"]))[:5]
    if elements["outcome_statistics"]["claims"]:
        elements["outcome_statistics"]["position"] = position_for_text(elements["outcome_statistics"]["claims"][0])

    # Trust badges
    if any(x in text_lower for x in ["certified", "accredited", "award", "top doctor", "best of"]):
        elements["trust_badges"]["present"] = True
        elements["trust_badges"]["position"] = "early"

    # Press / As seen in
    if any(x in text_lower for x in ["as seen in", "featured in", "press", "media"]):
        elements["press_mentions"]["present"] = True
        elements["press_mentions"]["position"] = "early"

    # Live chat
    if "live chat" in html_lower or "chat widget" in html_lower or "intercom" in html_lower or "drift" in html_lower or "crisp" in html_lower:
        elements["live_chat"]["present"] = True

    # Online scheduling
    if any(x in text_lower for x in ["schedule online", "book online", "online scheduling", "schedule your"]):
        elements["online_scheduling"]["present"] = True
        elements["online_scheduling"]["position"] = "mid"

    # Google review widget
    if "google" in html_lower and ("review" in html_lower or "rating" in html_lower):
        elements["google_review_widget"]["present"] = True

    # Video testimonials count
    if elements["testimonials"]["type"] == "video" or "video testimonial" in text_lower:
        elements["video_testimonials"]["present"] = True
        elements["video_testimonials"]["count"] = text_lower.count("video")  # rough

    return elements


def _extract_internal_links(soup: BeautifulSoup, page_url: str) -> List[dict]:
    """List internal links with anchor text."""
    base_domain = _get_domain(page_url)
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        full_url = urljoin(page_url, href)
        if not _is_internal_link(href, base_domain) and _get_domain(full_url) != base_domain:
            continue
        anchor = a.get_text(strip=True)
        key = (full_url, anchor)
        if key not in seen:
            seen.add(key)
            links.append({"url": full_url, "anchor_text": anchor})
    return links


def _extract_structure(soup: BeautifulSoup) -> dict:
    """Extract H1, H2s, H3s in document order."""
    h1_text = ""
    h1 = soup.find("h1")
    if h1:
        h1_text = h1.get_text(strip=True)

    h2s = []
    for h2 in soup.find_all("h2"):
        h2s.append({"text": h2.get_text(strip=True), "h3s": []})
    # Group H3s under preceding H2
    for h3 in soup.find_all("h3"):
        parent = None
        for sib in h3.find_previous_siblings():
            if sib.name == "h2":
                parent = sib.get_text(strip=True)
                break
            if sib.name == "h3":
                break
        for h2_entry in h2s:
            if h2_entry["text"] == parent or not parent:
                h2_entry["h3s"].append(h3.get_text(strip=True))
                break
        else:
            if h2s:
                h2s[-1]["h3s"].append(h3.get_text(strip=True))

    return {"h1": h1_text, "h2s": h2s, "h3s": [h.get_text(strip=True) for h in soup.find_all("h3")]}


def _extract_procedure_section(soup: BeautifulSoup, procedure: str) -> Optional[dict]:
    """
    For homepages: find procedure-specific content block.
    Capture H2 section where procedure appears + content until next H2.
    """
    proc_lower = procedure.lower()
    for h2 in soup.find_all("h2"):
        h2_text = h2.get_text(strip=True).lower()
        if proc_lower in h2_text:
            content_parts = [h2_text]
            for sib in h2.find_next_siblings():
                if sib.name == "h2":
                    break
                content_parts.append(sib.get_text(separator=" ", strip=True))
            return {"h2": h2.get_text(strip=True), "content": " ".join(content_parts)}
    return None


def scrape_single_url(
    url: str,
    config: dict,
    page_type_prelim: str = "Service Page",
) -> dict:
    """
    Scrape a single URL and return extracted data.
    Never crashes - returns error info on failure.
    """
    tech_keywords = config.get("technology_keywords", [])
    procedure = config.get("procedure", "")

    result = {
        "url": url,
        "scraped": False,
        "scrape_failed": False,
        "error": None,
        "js_rendering_flagged": False,
        "structure": {},
        "above_fold_mobile": {},
        "content_elements": {},
        "internal_links": [],
        "word_count": 0,
        "procedure_section": None,
    }

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            result["scrape_failed"] = True
            result["error"] = f"HTTP {resp.status_code}"
            return result
    except Exception as e:
        result["scrape_failed"] = True
        result["error"] = str(e)
        return result

    soup = BeautifulSoup(resp.text, "lxml")
    visible_text = _get_visible_text(soup)
    word_count = _word_count(visible_text)

    result["word_count"] = word_count
    if word_count < 200:
        result["js_rendering_flagged"] = True

    result["structure"] = _extract_structure(soup)
    result["above_fold_mobile"] = _extract_above_fold_mobile(soup, visible_text)
    result["content_elements"] = _extract_content_elements(soup, visible_text, tech_keywords, url)
    result["internal_links"] = _extract_internal_links(soup, url)
    result["scraped"] = True

    if page_type_prelim == "Homepage" and config.get("homepage_handling") == "extract_section":
        result["procedure_section"] = _extract_procedure_section(soup, procedure)

    return result


def scrape_urls(serp_results: List[dict], config: dict) -> List[dict]:
    """
    Scrape all URLs from SERP results.
    Merges SERP data with scraped data per URL.
    """
    pages = []
    for i, row in enumerate(serp_results):
        url = row.get("url", "")
        if not url:
            continue
        page_type_prelim = row.get("page_type_prelim", "Service Page")
        scraped = scrape_single_url(url, config, page_type_prelim)
        merged = {**row, **scraped}
        pages.append(merged)
        if i < len(serp_results) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)
    return pages
