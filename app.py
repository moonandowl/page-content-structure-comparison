"""
Page Structure Comparison Tool - Web Interface
Simple Flask app for running the competitive analysis pipeline.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, send_file, url_for

from main import load_config, run_pipeline_with_config

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.resolve()
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
DATA_DIR = PROJECT_ROOT / "data"

app = Flask(__name__, static_folder="static", template_folder="templates")


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
    """Run the pipeline and redirect to results."""
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

    cities = parse_cities_text(cities_text)
    config = load_config()
    config["procedure"] = procedure
    config["cities"] = cities
    config["num_results"] = num_results

    # Handle Ahrefs file upload
    ahrefs_file = request.files.get("ahrefs_file")
    if ahrefs_file and ahrefs_file.filename and ahrefs_file.filename.lower().endswith(".csv"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ahrefs_path = DATA_DIR / "ahrefs_batch.csv"
        ahrefs_file.save(str(ahrefs_path))

    result = run_pipeline_with_config(config)

    if not result["success"]:
        return render_template(
            "index.html",
            procedure=procedure,
            cities=cities_text,
            num_results=num_results,
            error=result.get("error", "Pipeline failed."),
        ), 400

    # Redirect to results page with filename
    return redirect(url_for("results", filename=result["output_filename"]))


@app.route("/results/<filename>")
def results(filename):
    """Show results summary and download link."""
    output_path = OUTPUTS_DIR / filename
    if not output_path.exists():
        return "Report not found.", 404
    return render_template("results.html", filename=filename)


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
