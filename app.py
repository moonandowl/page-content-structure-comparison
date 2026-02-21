"""
Page Structure Comparison Tool - Web Interface
Simple Flask app for running the competitive analysis pipeline.
Uses background jobs to avoid request timeouts for long-running analysis.
"""

import json
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, Response, send_file, url_for

from main import load_config, run_pipeline_with_config

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.resolve()
AUTH_USERNAME = os.environ.get("AUTH_USERNAME")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD")
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
DATA_DIR = PROJECT_ROOT / "data"
JOB_STATUS_PATH = DATA_DIR / "job_status.json"


def _get_job_status() -> dict:
    """Read current job status from file."""
    if not JOB_STATUS_PATH.exists():
        return {"status": "idle", "filename": None, "error": None}
    try:
        with open(JOB_STATUS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "idle", "filename": None, "error": None}


def _set_job_status(status: str, filename: str = None, error: str = None) -> None:
    """Write job status to file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(JOB_STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump({"status": status, "filename": filename, "error": error}, f)


def _run_analysis_background(config: dict) -> None:
    """Run pipeline in background thread."""
    try:
        result = run_pipeline_with_config(config)
        if result["success"]:
            _set_job_status("completed", filename=result["output_filename"])
        else:
            _set_job_status("failed", error=result.get("error", "Unknown error"))
    except Exception as e:
        _set_job_status("failed", error=str(e))


def _run_merge_background(config: dict) -> None:
    """Run Ahrefs merge in background thread."""
    try:
        result = run_pipeline_with_config(config, skip_scrape=True, run_id="ahrefs")
        if result["success"]:
            _set_job_status("completed", filename=result["output_filename"])
        else:
            _set_job_status("failed", error=result.get("error", "Unknown error"))
    except Exception as e:
        _set_job_status("failed", error=str(e))


app = Flask(__name__, static_folder="static", template_folder="templates")


@app.before_request
def require_auth():
    """Require HTTP Basic Auth when AUTH_USERNAME and AUTH_PASSWORD are set."""
    if not AUTH_USERNAME or not AUTH_PASSWORD:
        return None
    auth = request.authorization
    if not auth or auth.username != AUTH_USERNAME or auth.password != AUTH_PASSWORD:
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="Page Structure Comparison Tool"'},
        )
    return None


def parse_cities_text(text: str) -> list[dict]:
    """Parse 'City, State' or 'City' lines into city dicts."""
    cities = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            cities.append({"city": parts[0], "state": parts[1], "country": "United States"})
        else:
            cities.append({"city": parts[0], "state": "", "country": "United States"})
    return cities


@app.route("/")
def index():
    """Show form with config defaults."""
    try:
        config = load_config()
    except Exception:
        config = {
            "procedure": "LASIK",
            "cities": [{"city": "Dallas", "state": "Texas", "country": "United States"}],
            "num_results": 3,
        }
    cities_text = "\n".join(
        f"{c['city']}, {c['state']}" if c.get("state") else c["city"]
        for c in config.get("cities", [])
    )
    return render_template(
        "index.html",
        procedure=config.get("procedure", "LASIK"),
        cities=cities_text,
        num_results=config.get("num_results", 3),
    )


@app.route("/run", methods=["POST"])
def run():
    """Start pipeline in background and redirect to processing page."""
    procedure = request.form.get("procedure", "LASIK").strip() or "LASIK"
    cities_text = request.form.get("cities", "").strip()
    num_results = int(request.form.get("num_results", 3) or 3)

    if not cities_text:
        return render_template(
            "index.html",
            procedure=procedure,
            cities="",
            num_results=num_results,
            error="Enter at least one city.",
        ), 400

    job = _get_job_status()
    if job["status"] == "running":
        return render_template(
            "index.html",
            procedure=procedure,
            cities=cities_text,
            num_results=num_results,
            error="A job is already running. Please wait for it to finish.",
        ), 400

    cities = parse_cities_text(cities_text)
    config = load_config()
    config["procedure"] = procedure
    config["cities"] = cities
    config["num_results"] = num_results

    _set_job_status("running")
    thread = threading.Thread(target=_run_analysis_background, args=(config,))
    thread.daemon = True
    thread.start()

    return redirect(url_for("processing"))


def _get_urls_from_last_run():
    """Get URLs from the most recent run for Ahrefs Step 2."""
    merged_path = DATA_DIR / "merged_data.json"
    if not merged_path.exists():
        return []
    try:
        with open(merged_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [p.get("url", "") for p in data.get("pages", []) if p.get("url")]
    except Exception:
        return []


@app.route("/processing")
def processing():
    """Show processing page that polls for job completion."""
    return render_template("processing.html")


@app.route("/job-status")
def job_status():
    """Return current job status as JSON for polling."""
    return _get_job_status()


@app.route("/results/<filename>")
def results(filename):
    """Show results summary, download link, and Step 2 Ahrefs upload."""
    output_path = OUTPUTS_DIR / filename
    if not output_path.exists():
        return "Report not found.", 404
    urls = _get_urls_from_last_run()
    return render_template("results.html", filename=filename, urls=urls)


@app.route("/merge-ahrefs", methods=["POST"])
def merge_ahrefs():
    """Step 2: Merge uploaded Ahrefs CSV and rebuild report (background)."""
    ahrefs_file = request.files.get("ahrefs_file")
    if not ahrefs_file or not ahrefs_file.filename or not ahrefs_file.filename.lower().endswith(".csv"):
        return render_template(
            "results.html",
            filename=request.form.get("filename", ""),
            urls=_get_urls_from_last_run(),
            error="Please upload an Ahrefs Batch Analysis CSV file.",
        ), 400

    job = _get_job_status()
    if job["status"] == "running":
        return render_template(
            "results.html",
            filename=request.form.get("filename", ""),
            urls=_get_urls_from_last_run(),
            error="A job is already running. Please wait for it to finish.",
        ), 400

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ahrefs_path = DATA_DIR / "ahrefs_batch.csv"
    ahrefs_file.save(str(ahrefs_path))

    config = load_config()
    _set_job_status("running")
    thread = threading.Thread(target=_run_merge_background, args=(config,))
    thread.daemon = True
    thread.start()

    return redirect(url_for("processing"))


@app.route("/download/<filename>")
def download(filename):
    """Serve the Excel file for download."""
    output_path = OUTPUTS_DIR / filename
    if not output_path.exists():
        return "File not found.", 404
    return send_file(
        output_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
