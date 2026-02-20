"""
Page Structure Comparison Tool - Analyzer Module
Page type classification, content coverage, section order, authority vs content diagnosis.
"""

import re
from pathlib import Path
from urllib.parse import urlparse

from rapidfuzz import fuzz

PROJECT_ROOT = Path(__file__).parent.resolve()

# Page type weights for wireframe
PAGE_TYPE_WEIGHTS = {
    "Homepage": "Low",
    "Service Page": "High",
    "Procedure+Location": "High",
    "Geo Page": "Low",
    "Blog/Article": "Excluded",
}

# Content element weights for Content Richness Score (0-10)
ELEMENT_WEIGHTS = {
    "faq_section": 1.5,
    "testimonials": 1.2,
    "surgeon_credentials": 1.2,
    "technology_names": 1.2,
    "outcome_statistics": 1.2,
    "online_scheduling": 1.2,
    "cost_pricing": 0.8,
    "candidacy_quiz": 0.8,
    "before_after_photos": 0.8,
    "video_embed": 0.6,
    "cta_buttons": 0.5,
    "financing": 0.5,
    "trust_badges": 0.5,
    "press_mentions": 0.4,
    "live_chat": 0.3,
    "google_review_widget": 0.5,
    "video_testimonials": 0.6,
}


def _get_path_segments(url: str) -> list[str]:
    """Extract path segments from URL."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    return [s for s in path.split("/") if s] if path else []


def _path_contains_any(url: str, patterns: list[str]) -> bool:
    """Check if URL path contains any of the patterns."""
    path = urlparse(url).path.lower()
    return any(p.lower() in path for p in patterns)


def _path_has_date_pattern(path: str) -> bool:
    """Check for date-like patterns in path (e.g. /2024/01/, /jan-2024/)."""
    return bool(re.search(r"/\d{4}/|\d{4}-\d{2}|/\d{2}-\d{2}-\d{4}/|/[a-z]{3}-\d{4}/", path.lower())) or bool(
        re.search(r"^\d{4}", path)
    )


def classify_url_pre_scrape(serp_results: list[dict], config: dict) -> list[dict]:
    """
    URL-based page type classification (before scraping).
    Adds page_type_prelim and confidence_prelim to each result.
    """
    pc = config.get("page_classification", {})
    location_patterns = pc.get("location_folder_patterns", ["/locations/", "/offices/", "/clinics/"])
    procedure = config.get("procedure", "").lower()

    for row in serp_results:
        url = row.get("url", "")
        path = urlparse(url).path
        path_lower = path.lower()
        segments = _get_path_segments(url)

        # Homepage: no path, root, or /index
        if not path or path == "/" or path.rstrip("/") == "" or path_lower in ("/index", "/index.html", "/index.htm"):
            row["page_type_prelim"] = "Homepage"
            row["confidence_prelim"] = "High"
            row["url_signal"] = "No path or index"
            continue

        # Blog/Article
        if "/blog/" in path_lower or "/news/" in path_lower or "/articles/" in path_lower or _path_has_date_pattern(path):
            row["page_type_prelim"] = "Blog/Article"
            row["confidence_prelim"] = "High"
            row["url_signal"] = "Blog/news/date pattern"
            continue

        # Geo Page preliminary: location folder or city as primary path
        if _path_contains_any(url, location_patterns):
            row["page_type_prelim"] = "Geo Page"
            row["confidence_prelim"] = "Medium"
            row["url_signal"] = "Location folder in path"
            continue

        # Procedure+Location: procedure AND city-like segment in path
        has_procedure = procedure in path_lower if procedure else False
        # Simple heuristic: multi-segment path with procedure
        if has_procedure and len(segments) >= 2:
            row["page_type_prelim"] = "Procedure+Location"
            row["confidence_prelim"] = "Medium"
            row["url_signal"] = "Procedure + location in path"
            continue

        # Service Page: procedure with no clear location
        if has_procedure:
            row["page_type_prelim"] = "Service Page"
            row["confidence_prelim"] = "Medium"
            row["url_signal"] = "Procedure in path"
            continue

        # Default
        row["page_type_prelim"] = "Service Page"
        row["confidence_prelim"] = "Low"
        row["url_signal"] = "Default"

    return serp_results


def classify_pages_post_scrape(scraped_pages: list[dict], config: dict) -> list[dict]:
    """
    Content-based classification. Overrides URL signals when they conflict.
    Content signals always win.
    """
    pc = config.get("page_classification", {})
    geo_signals = [s.lower() for s in pc.get("geo_page_signals", [])]
    proc_signals = [s.lower() for s in pc.get("procedure_location_signals", [])]
    procedure = config.get("procedure", "").lower()

    for p in scraped_pages:
        prelim = p.get("page_type_prelim", "Service Page")
        content = p.get("content_elements", {})
        structure = p.get("structure", {})
        word_count = p.get("word_count", 0)
        visible_text = _get_page_text_for_classification(p)

        h1 = (structure.get("h1") or "").lower()
        h2_texts = " ".join(
            h.get("text", "") for h in structure.get("h2s", [])
        ).lower()

        content_signal = "Default"
        final_type = prelim
        confidence = p.get("confidence_prelim", "Medium")

        # Content: H1 leads with city + multiple procedures = Geo Page
        if h1 and word_count < 400:
            # Short page with city-first H1
            if any(c in h1 for c in ["dallas", "chicago", "location", "office", "our"]):
                for g in geo_signals:
                    if g in visible_text:
                        final_type = "Geo Page"
                        content_signal = "City H1 + geo signals"
                        confidence = "High"
                        break

        # Content: section listing multiple procedures = Geo Page
        for g in geo_signals:
            if g in visible_text or g in h2_texts:
                # Check if page lists multiple procedures
                proc_count = sum(1 for s in proc_signals if s in visible_text)
                proc_term = procedure.lower() if procedure else ""
                if proc_count < 2 and not (proc_term and proc_term in visible_text):
                    # Actually could be geo - multiple services
                    final_type = "Geo Page"
                    content_signal = f"Geo signal: {g}"
                    confidence = "High"
                break

        # Content: clinical detail for single procedure = Service or Procedure+Location
        for sig in proc_signals:
            if sig in visible_text:
                if prelim == "Geo Page":
                    # Override: content says procedure page
                    final_type = "Procedure+Location" if "location" in h1 or len(structure.get("h2s", [])) > 4 else "Service Page"
                    content_signal = f"Procedure signal: {sig}"
                    confidence = "High"
                break

        # H1 leads with procedure + clinical content
        if procedure and procedure in h1:
            if any(s in visible_text for s in proc_signals):
                if prelim in ("Geo Page", "Homepage"):
                    final_type = "Procedure+Location" if prelim == "Geo Page" else "Service Page"
                    content_signal = "Procedure H1 + clinical content"
                    confidence = "High"

        p["page_type"] = final_type
        p["page_type_confidence"] = confidence
        p["content_signal"] = content_signal
        p["wireframe_weight"] = PAGE_TYPE_WEIGHTS.get(final_type, "Low")

    return scraped_pages


def _get_page_text_for_classification(page: dict) -> str:
    """Get combined text for content classification."""
    parts = []
    structure = page.get("structure", {})
    parts.append(structure.get("h1", ""))
    for h2 in structure.get("h2s", []):
        parts.append(h2.get("text", ""))
        parts.extend(h2.get("h3s", []))
    # Content elements may have text
    ce = page.get("content_elements", {})
    sc = ce.get("surgeon_credentials", {})
    if isinstance(sc, dict):
        parts.append(sc.get("exact_text", ""))
    return " ".join(str(p) for p in parts).lower()


def _safe_dr(page: dict) -> float:
    """Get numeric Domain Rating for comparison."""
    dr = page.get("domain_rating", "")
    if isinstance(dr, (int, float)):
        return float(dr)
    if isinstance(dr, str):
        m = re.search(r"(\d+(?:\.\d+)?)", str(dr))
        return float(m.group(1)) if m else 0
    return 0


def _safe_ur(page: dict) -> float:
    """Get numeric URL Rating for comparison."""
    ur = page.get("url_rating", "")
    if isinstance(ur, (int, float)):
        return float(ur)
    if isinstance(ur, str):
        m = re.search(r"(\d+(?:\.\d+)?)", str(ur))
        return float(m.group(1)) if m else 0
    return 0


def _assess_ranking_driver(page: dict) -> tuple[str, str]:
    """
    Determine if page ranking is likely driven by authority (DA/PA) vs content.
    Returns (ranking_driver, ranking_driver_note).
    """
    dr = _safe_dr(page)
    ur = _safe_ur(page)
    score = page.get("content_richness_score", 0)
    page_type = page.get("page_type", "")
    is_pos1 = page.get("is_position_1", False)

    # Homepage or Geo Page at position 1 — almost always authority-driven
    if is_pos1 and page_type in ("Homepage", "Geo Page"):
        return (
            "Authority-driven",
            "Position 1 Homepage/Geo page — domain authority likely primary ranking factor; content optimization alone will not outrank",
        )

    # Content Gap: high authority, weak content
    if dr > 40 and score < 5:
        return (
            "Authority-driven",
            f"High DR ({dr}), low content score ({score}) — ranking on domain/URL authority; beatable with stronger content",
        )

    # High UR with low content (page-level authority carrying weak page)
    if ur > 30 and score < 5:
        return (
            "Authority-driven",
            f"High URL Rating ({ur}), low content ({score}) — page authority outweighs content; improve content to compete",
        )

    # Authority Gap: strong content, weak authority
    if dr < 40 and score > 6:
        return (
            "Content-driven",
            f"Low DR ({dr}), strong content ({score}) — ranking on content quality; needs more backlinks to compete with high-DR players",
        )

    # Competitive: both strong
    if dr > 40 and score > 6:
        return (
            "Authority + Content",
            f"High DR ({dr}), strong content ({score}) — benchmark to beat; requires both better content and link building",
        )

    # Unclear
    return (
        "Unclear",
        f"DR {dr}, content {score} — manual review recommended; unclear why it ranks",
    )


def _compute_content_richness_score(page: dict) -> float:
    """Compute Content Richness Score 0-10 from content elements."""
    ce = page.get("content_elements", {})
    if not ce:
        return 0
    score = 0
    total_weight = sum(ELEMENT_WEIGHTS.values())
    for key, weight in ELEMENT_WEIGHTS.items():
        el = ce.get(key, {})
        if isinstance(el, dict) and el.get("present"):
            score += weight
        elif isinstance(el, bool) and el:
            score += weight
    return min(10, round(score / (total_weight / 10), 1))


def _diagnose_page(dr: float, score: float) -> str:
    """Diagnose: Content Gap, Authority Gap, Both, Competitive."""
    if dr > 40 and score < 5:
        return "Content Gap"
    if dr < 40 and score > 6:
        return "Authority Gap"
    if dr < 40 and score < 5:
        return "Both"
    if dr > 40 and score > 6:
        return "Competitive"
    return "Both"  # borderline


# Content element keys for analysis
CONTENT_ELEMENT_KEYS = [
    "cta_buttons", "video_embed", "faq_section", "testimonials",
    "cost_pricing", "candidacy_quiz", "before_after_photos", "surgeon_credentials",
    "technology_names", "financing", "outcome_statistics", "trust_badges",
    "press_mentions", "live_chat", "online_scheduling", "google_review_widget",
    "video_testimonials",
]

ELEMENT_DISPLAY_NAMES = {
    "cta_buttons": "CTA Buttons",
    "video_embed": "Video Embed",
    "faq_section": "FAQ Section",
    "testimonials": "Testimonials/Reviews",
    "cost_pricing": "Cost/Pricing Section",
    "candidacy_quiz": "Candidacy Quiz/Self-Test",
    "before_after_photos": "Before/After Photos",
    "surgeon_credentials": "Surgeon Credentials",
    "technology_names": "Technology Names",
    "financing": "Financing/Payment Plans",
    "outcome_statistics": "Statistical/Outcome Claims",
    "trust_badges": "Trust Badges/Awards",
    "press_mentions": "Press Mentions/As Seen In",
    "live_chat": "Live Chat Widget",
    "online_scheduling": "Online Scheduling Widget",
    "google_review_widget": "Google Review Widget",
    "video_testimonials": "Video Testimonials",
}


def run_analysis(merged_data: dict, config: dict) -> dict:
    """
    Run full analysis across all pages.
    Returns analysis dict with coverage, section order, differentiators, etc.
    """
    pages = merged_data.get("pages", [])
    procedure = config.get("procedure", "Procedure")

    # Qualifying pages: Service Page and Procedure+Location only (for content coverage)
    qualifying = [p for p in pages if p.get("page_type") in ("Service Page", "Procedure+Location")]
    all_for_authority = pages  # All pages for authority profile

    analysis = {
        "procedure": procedure,
        "total_pages": len(pages),
        "qualifying_pages": len(qualifying),
        "content_coverage": [],
        "section_order": {},
        "position_1_differentiators": [],
        "authority_driven_rankings": [],
        "gap_opportunities": [],
        "section_intelligence": [],
        "consensus_order": [],
    }

    # Add Content Richness, Diagnosis, and Ranking Driver assessment to each page
    for p in pages:
        p["content_richness_score"] = _compute_content_richness_score(p)
        dr = _safe_dr(p)
        p["diagnosis"] = _diagnose_page(dr, p["content_richness_score"])
        driver, note = _assess_ranking_driver(p)
        p["ranking_driver"] = driver
        p["ranking_driver_note"] = note

    # Authority-driven: position 1 is Geo or Homepage
    for p in pages:
        if p.get("is_position_1") and p.get("page_type") in ("Geo Page", "Homepage"):
            city = p.get("city", "?")
            pos = p.get("position", 1)
            analysis["authority_driven_rankings"].append(
                f"{city} #{pos} is a {p['page_type']} — content optimization alone will not outrank it"
            )

    # Content coverage matrix (qualifying pages only)
    for key in CONTENT_ELEMENT_KEYS:
        present_count = 0
        pos1_has = 0
        pos1_total = 0
        pos2_3_not = 0
        pos2_3_total = 0
        for p in qualifying:
            ce = p.get("content_elements", {})
            el = ce.get(key, {})
            present = el.get("present", False) if isinstance(el, dict) else bool(el)
            if present:
                present_count += 1
            if p.get("is_position_1"):
                pos1_total += 1
                if present:
                    pos1_has += 1
            else:
                pos2_3_total += 1
                if not present:
                    pos2_3_not += 1

        pct = round(100 * present_count / len(qualifying), 1) if qualifying else 0
        is_diff = (
            pos1_total > 0 and pos1_has == pos1_total and pos2_3_total > 0 and pos2_3_not > 0
        )
        wireframe = "Differentiator" if is_diff else "Must Have" if pct > 50 else "Consider"
        analysis["content_coverage"].append({
            "element": ELEMENT_DISPLAY_NAMES.get(key, key),
            "key": key,
            "count": f"{present_count} of {len(qualifying)}",
            "percentage": pct,
            "position_1_differentiator": is_diff,
            "wireframe_priority": wireframe,
        })
        if is_diff:
            analysis["position_1_differentiators"].append({
                "element": ELEMENT_DISPLAY_NAMES.get(key, key),
                "summary": f"present on {pos1_has} of {pos1_total} position 1 pages, absent on {pos2_3_not} of {pos2_3_total} others",
            })

    # Gap opportunities: elements on zero pages
    for cov in analysis["content_coverage"]:
        if cov["percentage"] == 0 and cov["count"].startswith("0 of"):
            cov["wireframe_priority"] = "Gap Opportunity"
            analysis["gap_opportunities"].append(cov["element"])

    # Section order analysis - H2 sequences
    section_rows = {}
    for p in qualifying:
        h2s = [h.get("text", "") for h in p.get("structure", {}).get("h2s", [])]
        label = f"{p.get('city', '')} #{p.get('position', '')}"
        for i, h2 in enumerate(h2s):
            if i not in section_rows:
                section_rows[i] = {}
            section_rows[i][label] = h2
    analysis["section_order"] = section_rows

    # Fuzzy group section titles and consensus
    all_h2s = []
    for p in qualifying:
        for h in p.get("structure", {}).get("h2s", []):
            t = h.get("text", "")
            if t:
                all_h2s.append(t)
    grouped = _fuzzy_group_sections(all_h2s)
    consensus = []
    for i in sorted(section_rows.keys()):
        vals = [v for v in section_rows[i].values() if v]
        if vals:
            best = _most_common_fuzzy(vals)
            consensus.append(best)
    analysis["consensus_order"] = consensus
    analysis["section_groups"] = grouped

    # Section intelligence
    for group_name, members in grouped.items():
        count = len(members)
        pos1_incl = sum(
            1 for p in qualifying
            if p.get("is_position_1") and any(m in str(p.get("structure", {}).get("h2s", [])) for m in members)
        )
        rec = "Include" if count >= len(qualifying) // 2 else "Consider"
        analysis["section_intelligence"].append({
            "section_topic": group_name,
            "pages_containing": count,
            "position_1_include": pos1_incl,
            "typical_position": "early",
            "word_count_range": "varies",
            "wireframe_recommendation": rec,
        })

    return analysis


def _fuzzy_group_sections(titles: list[str], threshold: int = 80) -> dict[str, list[str]]:
    """Group similar section titles using fuzzy matching."""
    groups = {}
    for t in titles:
        matched = False
        for group_name, members in groups.items():
            if fuzz.ratio(t.lower(), group_name.lower()) >= threshold or \
               fuzz.partial_ratio(t.lower(), group_name.lower()) >= 90:
                members.append(t)
                matched = True
                break
        if not matched:
            groups[t] = [t]
    return groups


def _most_common_fuzzy(values: list[str]) -> str:
    """Find the most common value using fuzzy matching."""
    if not values:
        return ""
    best = values[0]
    best_count = 0
    for v in values:
        count = sum(1 for x in values if fuzz.ratio(v.lower(), x.lower()) >= 80)
        if count > best_count:
            best_count = count
            best = v
    return best
