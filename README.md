# Ovvi Fleet Error Dashboard

Interactive dashboard for tracking Ovvi error trends across the fleet.
Built with [Streamlit](https://streamlit.io/) + [Plotly](https://plotly.com/).

## Quick Start

### 1. Install Python (if you don't have it)

Download Python 3.11+ from https://www.python.org/downloads/
During install, **check "Add Python to PATH"**.

### 2. Set up the project

Open a terminal (PowerShell on Windows) and run:

```bash
cd ovvi-dashboard
pip install -r requirements.txt
```

### 3. Add your data

Create a `data/` folder inside the project and drop the Fleet QC spreadsheet in:

```
ovvi-dashboard/
├── app.py
├── parsers.py
├── requirements.txt
├── README.md
└── data/
    └── Fleet_QC_Report_-_2025_-_2026.xlsx
```

The app will automatically find the most recent .xlsx file in `data/`.
You can also drag-and-drop a file using the upload widget in the sidebar.

### 4. Run the dashboard

```bash
streamlit run app.py
```

Your browser will open to `http://localhost:8501` with the dashboard.

## Updating Data

When you get a new spreadsheet export:
1. Drop it in the `data/` folder (or upload via the sidebar)
2. Refresh the browser — the app reloads automatically

## Project Structure

```
app.py         — Dashboard layout and charts (Streamlit)
parsers.py     — Spreadsheet parser (ONLY file to change if input format changes)
requirements.txt
```

**Key design decision:** `parsers.py` is the only file that knows about the
spreadsheet format. Everything in `app.py` works off a normalized DataFrame.
When the input format changes (e.g. Lauren starts providing raw weekly CSVs),
you only need to update `parsers.py`.

## Iterating with Claude Code

This project is designed to be modified conversationally with Claude Code.
Examples of things you can ask:

- "Move the heatmap to the first tab"
- "Add a toggle to normalize errors by number of active units"
- "Change the default date range to last 3 months"
- "Add firmware update vertical lines with these dates: ..."
- "Make the bar chart colors match our brand"
- "Add a new chart showing errors per unit per week"

## Future: Deployment

When ready to share with the team:
1. Push to GitHub (private repo)
2. Deploy on Streamlit Cloud, Railway, or Render
3. Add password protection (simple shared password or Streamlit auth)
