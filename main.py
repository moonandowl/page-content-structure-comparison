#!/usr/bin/env python3
"""
Page Structure Comparison Tool - Main Runner
Competitive page analysis pipeline for medical procedure SEO.
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Project paths
PROJECT_ROOT = Path(__file__).parent.resolve()
DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# Configure logging - never crash, log and continue
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load configuration from config.json."""
    config_path = PROJECT_ROOT / "config.json"
    if not config_path.exists():
        logger.error("config.json not found. Please create it from the example.")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    """Save configuration back to config.json."""
    config_path = PROJECT_ROOT / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def cli_prompt(config: dict) -> dict:
    """
    Interactive CLI prompt for procedure and cities.
    Returns updated config dict.
    """
    print("\n=== PAGE STRUCTURE COMPARISON TOOL ===\n")
    print("Load from config.json? (y/n): ", end="")
    choice = input().strip().lower()

    if choice == "y" or choice == "":
        return config

    # Enter procedure
    default_proc = config.get("procedure", "LASIK")
    print(f"Enter procedure (e.g. LASIK, RLE, PRK, Cataract) [{default_proc}]: ", end="")
    procedure = input().strip() or default_proc
    config["procedure"] = procedure

    # Enter cities
    print("Enter cities one per line, blank line when done:")
    cities = []
    while True:
        line = input().strip()
        if not line:
            break
        # Support "City, State" or "City" format
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            cities.append({"city": parts[0], "state": parts[1], "country": "United States"})
        else:
            cities.append({"city": parts[0], "state": "", "country": "United States"})

    if cities:
        config["cities"] = cities

    return config


def fetch_serp_results(config: dict) -> list[dict]:
    """
    Query SerpAPI for organic results per city.
    Returns list of SERP result dicts.
    """
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key or api_key == "paste_your_key_here":
        raise ValueError("SERPAPI_KEY not set in .env. Add your SerpAPI key to environment variables.")

    procedure = config["procedure"]
    cities = config["cities"]
    num_results = config.get("num_results", 3)

    results = []
    for city_data in cities:
        city = city_data["city"]
        state = city_data.get("state", "")
        country = city_data.get("country", "United States")
        keyword = f"{procedure} {city}"
        location = f"{city}, {state}, {country}".strip(", ")

        params = {
            "q": keyword,
            "location": location,
            "api_key": api_key,
            "num": min(num_results + 5, 100),  # Request extra in case some are filtered
            "engine": "google",
        }

        try:
            resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"SerpAPI request failed for {keyword}: {e}")
            continue
        except json.JSONDecodeError as e:
            logger.error(f"SerpAPI response parse error for {keyword}: {e}")
            continue

        organic = data.get("organic_results", [])
        for i, item in enumerate(organic[:num_results]):
            pos = i + 1
            results.append({
                "position": pos,
                "url": item.get("link", ""),
                "page_title": item.get("title", ""),
                "meta_description": item.get("snippet", ""),
                "city": city,
                "state": state,
                "country": country,
                "keyword": keyword,
                "is_position_1": pos == 1,
            })

    return results


def run_pipeline(skip_scrape: bool = False, ahrefs_only: bool = False, no_prompt: bool = False) -> None:
    """Execute the full pipeline."""
    from analyzer import run_analysis
    from ahrefs_parser import parse_ahrefs_csv, merge_ahrefs_data
    from output_builder import build_excel
    from scraper import scrape_urls
    from analyzer import classify_url_pre_scrape, classify_pages_post_scrape

    # Ensure directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    config = load_config()

    if not no_prompt:
        config = cli_prompt(config)
        save_config(config)

    procedure = config["procedure"]
    cities = config["cities"]

    # --- STEP 1: SERP Data (unless skip-scrape or ahrefs-only) ---
    if ahrefs_only:
        serp_path = DATA_DIR / "serp_results.json"
        scraped_path = DATA_DIR / "scraped_pages.json"
        merged_path = DATA_DIR / "merged_data.json"
        if not merged_path.exists() and not scraped_path.exists():
            logger.error("No existing data. Run full pipeline first or provide scraped_pages.json.")
            sys.exit(1)
        # Load pages from merged or serp+scraped, then re-merge Ahrefs (picks up new CSV)
        if merged_path.exists():
            with open(merged_path, "r", encoding="utf-8") as f:
                prev = json.load(f)
            scraped_pages = prev.get("pages", [])
        else:
            with open(serp_path, "r", encoding="utf-8") as f:
                serp_results = json.load(f)
            with open(scraped_path, "r", encoding="utf-8") as f:
                scraped_pages = json.load(f)
        merged_data = merge_ahrefs_data([], scraped_pages, config)
        with open(merged_path, "w", encoding="utf-8") as f:
            json.dump(merged_data, f, indent=2)
        analysis = run_analysis(merged_data, config)
        with open(DATA_DIR / "analysis.json", "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2)
        output_path = build_excel(procedure, merged_data, analysis, config)
        _print_summary(procedure, cities, merged_data, analysis, output_path)
        return

    if not skip_scrape:
        serp_results = fetch_serp_results(config)
        with open(DATA_DIR / "serp_results.json", "w", encoding="utf-8") as f:
            json.dump(serp_results, f, indent=2)
        logger.info(f"Saved {len(serp_results)} SERP results to data/serp_results.json")

        # Preliminary URL-based classification
        serp_results = classify_url_pre_scrape(serp_results, config)

        # --- STEP 2: Scraping ---
        scraped_pages = scrape_urls(serp_results, config)
        with open(DATA_DIR / "scraped_pages.json", "w", encoding="utf-8") as f:
            json.dump(scraped_pages, f, indent=2)
        logger.info(f"Saved scraped data to data/scraped_pages.json")

        # Re-classify with content signals
        scraped_pages = classify_pages_post_scrape(scraped_pages, config)
        with open(DATA_DIR / "scraped_pages.json", "w", encoding="utf-8") as f:
            json.dump(scraped_pages, f, indent=2)
    else:
        # Load from saved files
        serp_path = DATA_DIR / "serp_results.json"
        scraped_path = DATA_DIR / "scraped_pages.json"
        if not serp_path.exists() or not scraped_path.exists():
            logger.error("--skip-scrape requires existing serp_results.json and scraped_pages.json")
            sys.exit(1)
        with open(serp_path, "r", encoding="utf-8") as f:
            serp_results = json.load(f)
        with open(scraped_path, "r", encoding="utf-8") as f:
            scraped_pages = json.load(f)

    # --- STEP 3: Ahrefs ---
    merged_data = merge_ahrefs_data(serp_results, scraped_pages, config)
    with open(DATA_DIR / "merged_data.json", "w", encoding="utf-8") as f:
        json.dump(merged_data, f, indent=2)

    # --- STEP 4: Analysis ---
    analysis = run_analysis(merged_data, config)
    with open(DATA_DIR / "analysis.json", "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2)

    # --- STEP 5: Excel ---
    output_path = build_excel(procedure, merged_data, analysis, config)

    _print_summary(procedure, cities, merged_data, analysis, output_path)


def run_pipeline_with_config(
    config: dict,
    skip_scrape: bool = False,
    run_id: Optional[str] = None,
) -> dict:
    """
    Run pipeline with provided config. For web/API use.
    Returns dict with: success, output_path, output_filename, summary, error
    """
    run_id = run_id or uuid.uuid4().hex[:8]
    try:
        from analyzer import run_analysis
        from ahrefs_parser import merge_ahrefs_data
        from output_builder import build_excel
        from scraper import scrape_urls
        from analyzer import classify_url_pre_scrape, classify_pages_post_scrape
    except ImportError as e:
        return {"success": False, "error": f"Import error: {e}"}

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    procedure = config["procedure"]
    cities = config["cities"]

    try:
        if skip_scrape:
            serp_path = DATA_DIR / "serp_results.json"
            scraped_path = DATA_DIR / "scraped_pages.json"
            if not serp_path.exists() or not scraped_path.exists():
                return {"success": False, "error": "No saved data. Run full scrape first."}
            with open(serp_path, "r", encoding="utf-8") as f:
                serp_results = json.load(f)
            with open(scraped_path, "r", encoding="utf-8") as f:
                scraped_pages = json.load(f)
        else:
            serp_results = fetch_serp_results(config)
            with open(DATA_DIR / "serp_results.json", "w", encoding="utf-8") as f:
                json.dump(serp_results, f, indent=2)
            serp_results = classify_url_pre_scrape(serp_results, config)
            scraped_pages = scrape_urls(serp_results, config)
            with open(DATA_DIR / "scraped_pages.json", "w", encoding="utf-8") as f:
                json.dump(scraped_pages, f, indent=2)
            scraped_pages = classify_pages_post_scrape(scraped_pages, config)
            with open(DATA_DIR / "scraped_pages.json", "w", encoding="utf-8") as f:
                json.dump(scraped_pages, f, indent=2)

        merged_data = merge_ahrefs_data(serp_results, scraped_pages, config)
        with open(DATA_DIR / "merged_data.json", "w", encoding="utf-8") as f:
            json.dump(merged_data, f, indent=2)

        analysis = run_analysis(merged_data, config)
        with open(DATA_DIR / "analysis.json", "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2)

        output_path = build_excel(procedure, merged_data, analysis, config, run_id=run_id)
        pages = merged_data.get("pages", [])
        type_counts = {}
        for p in pages:
            t = p.get("page_type", "Unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "success": True,
            "output_path": str(output_path),
            "output_filename": output_path.name,
            "summary": {
                "procedure": procedure,
                "cities": ", ".join(c.get("city", "") for c in cities),
                "total_pages": len(pages),
                "scraped_ok": sum(1 for p in pages if p.get("scraped") and not p.get("scrape_failed")),
                "ahrefs_matched": sum(1 for p in pages if p.get("ahrefs_matched", False)),
                "page_types": type_counts,
                "differentiators": analysis.get("position_1_differentiators", [])[:5],
                "auth_driven": analysis.get("authority_driven_rankings", []),
            },
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception("Pipeline failed")
        return {"success": False, "error": str(e)}


def _print_summary(
    procedure: str,
    cities: list,
    merged_data: dict,
    analysis: dict,
    output_path: Path,
) -> None:
    """Print terminal summary."""
    pages = merged_data.get("pages", [])
    total = len(pages)
    scraped_ok = sum(1 for p in pages if p.get("scraped") and not p.get("scrape_failed"))
    failed = sum(1 for p in pages if p.get("scrape_failed"))
    js_flag = sum(1 for p in pages if p.get("js_rendering_flagged"))
    ahrefs_matched = sum(1 for p in pages if p.get("ahrefs_matched"))
    city_names = ", ".join(c.get("city", "") for c in cities)

    # Page type counts
    type_counts = {}
    for p in pages:
        t = p.get("page_type", "Unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    print("\n========================================")
    print("=== PAGE STRUCTURE COMPARISON TOOL ===\n")
    print(f"Procedure: {procedure}")
    print(f"Cities analyzed: {city_names}")
    print(f"Total pages pulled: {total}")
    print(f"Successfully scraped: {scraped_ok}")
    if failed > 0:
        print(f"Failed / blocked: {failed} (see logs)")
    if js_flag > 0:
        print(f"JS rendering flagged: {js_flag}")
    print(f"Ahrefs data matched: {ahrefs_matched} of {total}")

    print("\nPage types detected:")
    for t in ["Service Page", "Procedure+Location", "Geo Page", "Homepage", "Blog/Article"]:
        c = type_counts.get(t, 0)
        suffix = " ⚠️" if t in ("Geo Page", "Homepage") and c > 0 else ""
        print(f"  {t}s: {c}{suffix}")

    # Top differentiators
    diff = analysis.get("position_1_differentiators", [])
    if diff:
        print("\n⭐ Top Position 1 Differentiators:")
        for i, d in enumerate(diff[:5], 1):
            print(f"  {i}. {d.get('element', '')} ({d.get('summary', '')})")

    # Authority-driven rankings
    auth_flags = analysis.get("authority_driven_rankings", [])
    if auth_flags:
        print("\n⚠️ Authority-driven rankings detected:")
        for flag in auth_flags:
            print(f"  {flag}")

    print(f"\nOutput saved to: {output_path}")
    print("======================================\n")


def main():
    parser = argparse.ArgumentParser(description="Page Structure Comparison Tool")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip SERP and scraping, load from saved JSON")
    parser.add_argument("--ahrefs-only", action="store_true", help="Only parse Ahrefs CSV and rebuild Excel")
    parser.add_argument("--no-prompt", action="store_true", help="Skip CLI prompts, use config.json as-is")
    args = parser.parse_args()
    try:
        run_pipeline(
            skip_scrape=args.skip_scrape,
            ahrefs_only=args.ahrefs_only,
            no_prompt=args.no_prompt,
        )
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
