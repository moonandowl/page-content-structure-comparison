"""
Page Structure Comparison Tool - Ahrefs Parser Module
Parses Ahrefs Batch Analysis CSV and merges with scraped/SERP data.
"""

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).parent.resolve()
DATA_DIR = PROJECT_ROOT / "data"
AHREFS_CSV = DATA_DIR / "ahrefs_batch.csv"

# Common Ahrefs column name variations (case-insensitive)
AHREFS_COLUMN_ALIASES = {
    "domain rating": ["domain rating", "dr", "domain authority"],
    "url rating": ["url rating", "ur"],
    "referring domains": ["referring domains", "refdomains", "referring domains"],
    "backlinks": ["backlinks", "links"],
    "organic traffic": ["organic traffic", "traffic", "est. traffic"],
}


def _normalize_url_for_match(url: str) -> str:
    """
    Normalize URL for matching: lowercase, no trailing slash, https, no www.
    """
    if not url or not url.strip():
        return ""
    url = url.strip().lower()
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    netloc = parsed.netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/") or "/"
    return f"https://{netloc}{path}"


def _url_variants(url: str) -> Set[str]:
    """Generate URL variants for flexible matching."""
    normalized = _normalize_url_for_match(url)
    variants = {normalized}
    parsed = urlparse(normalized)
    netloc = parsed.netloc
    path = parsed.path
    # With/without www
    if "www." in netloc:
        variants.add(f"https://{netloc[4:]}{path}")
    else:
        variants.add(f"https://www.{netloc}{path}")
    # With/without trailing slash
    if path != "/":
        variants.add(normalized + "/")
        variants.add(normalized.rstrip("/"))
    # http
    variants.add(normalized.replace("https://", "http://"))
    # Path capitalization - try lowercase path
    path_lower = path.lower()
    if path_lower != path:
        variants.add(f"https://{netloc}{path_lower}")
    return variants


def _find_column(header_row: List[str], *names: str) -> Optional[int]:
    """Find column index by possible names (case-insensitive)."""
    for i, col in enumerate(header_row):
        col_lower = col.strip().lower()
        for name in names:
            if name in col_lower or col_lower in name:
                return i
    return None


def parse_ahrefs_csv() -> Optional[Dict[str, dict]]:
    """
    Parse ahrefs_batch.csv.
    Returns dict mapping normalized URL -> {dr, ur, referring_domains, backlinks, organic_traffic}
    or None if file does not exist.
    """
    if not AHREFS_CSV.exists():
        return None

    url_to_data = {}
    with open(AHREFS_CSV, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        if not header:
            return url_to_data

        url_col = _find_column(header, "url", "address", "target", "page")
        dr_col = _find_column(header, "domain rating", "dr")
        ur_col = _find_column(header, "url rating", "ur")
        rd_col = _find_column(header, "referring domains", "refdomains")
        bl_col = _find_column(header, "backlinks", "links")
        traffic_col = _find_column(header, "organic traffic", "traffic")

        if url_col is None:
            # First column might be URL
            url_col = 0

        for row in reader:
            if len(row) <= url_col:
                continue
            url = row[url_col].strip()
            if not url or url.startswith("#"):
                continue
            key = _normalize_url_for_match(url)
            entry = {
                "domain_rating": "Not available" if dr_col is None else _safe_val(row, dr_col),
                "url_rating": "Not available" if ur_col is None else _safe_val(row, ur_col),
                "referring_domains": "Not available" if rd_col is None else _safe_val(row, rd_col),
                "backlinks": "Not available" if bl_col is None else _safe_val(row, bl_col),
                "organic_traffic": "Not available" if traffic_col is None else _safe_val(row, traffic_col),
            }
            url_to_data[key] = entry
            # Also store variants for lookup
            for v in _url_variants(url):
                url_to_data[v] = entry

    return url_to_data


def _safe_val(row: list, idx: int) -> str:
    """Safely get value from row."""
    if idx is None or idx >= len(row):
        return ""
    val = row[idx].strip()
    return val if val else "Not available"


def match_url_to_ahrefs(url: str, ahrefs_map: Dict[str, dict]) -> Tuple[Optional[dict], bool]:
    """
    Match a URL to Ahrefs data.
    Returns (ahrefs_data, matched).
    """
    if not ahrefs_map:
        return None, False
    variants = _url_variants(url)
    for v in variants:
        if v in ahrefs_map:
            return ahrefs_map[v], True
    return None, False


def merge_ahrefs_data(
    serp_results: List[dict],
    scraped_pages: List[dict],
    config: dict,
) -> dict:
    """
    Merge SERP + scraped data with Ahrefs.
    Returns merged structure: {pages: [...], ...}
    """
    # Build page list from scraped (already has SERP fields merged)
    pages = []
    for sp in scraped_pages:
        pages.append(dict(sp))

    ahrefs_map = parse_ahrefs_csv()
    if ahrefs_map is None:
        print("No Ahrefs data found — drop ahrefs_batch.csv into the data folder and rerun with --skip-scrape to add authority data without re-scraping.")
    else:
        for p in pages:
            url = p.get("url", "")
            data, matched = match_url_to_ahrefs(url, ahrefs_map)
            p["ahrefs_matched"] = matched
            if matched and data:
                p["domain_rating"] = data.get("domain_rating", "Not available")
                p["url_rating"] = data.get("url_rating", "Not available")
                p["referring_domains"] = data.get("referring_domains", "Not available")
                p["backlinks"] = data.get("backlinks", "Not available")
                p["organic_traffic"] = data.get("organic_traffic", "Not available")
            else:
                p["domain_rating"] = "Not available — add this URL to Ahrefs Batch Analysis and re-export."
                p["url_rating"] = "Not available"
                p["referring_domains"] = "Not available"
                p["backlinks"] = "Not available"
                p["organic_traffic"] = "Not available"

    return {
        "pages": pages,
        "config": config,
    }
