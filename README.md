# Page Structure Comparison Tool

Competitive page analysis pipeline for medical procedure SEO.

## Quick Start

1. Clone and enter the project:
   ```bash
   cd "Page Sturcture Comparison Tool"
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Add your SerpAPI key to `.env`:
   ```
   SERPAPI_KEY=your_key_here
   ```

3. Run the CLI:
   ```bash
   python main.py
   ```
   Or run the web interface:
   ```bash
   python app.py
   ```
   Then open http://localhost:5000

## Deploy to Railway

1. Push your code to GitHub.

2. Go to [railway.app](https://railway.app) and create a new project from your GitHub repo.

3. In Railway, add a variable: `SERPAPI_KEY` = your SerpAPI key.

4. Railway will detect the Procfile and deploy. Your app will be available at a `*.railway.app` URL.

**Note:** Analysis runs take 30–60 seconds. Railway requests may time out around 60s; for large runs, consider using the CLI locally.
