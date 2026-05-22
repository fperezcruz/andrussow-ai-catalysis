import os
import re
import json
import time
import random
import hashlib
import requests
import pandas as pd
from datetime import datetime
from urllib.parse import quote
from tqdm import tqdm
import feedparser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

MAX_RESULTS = 15
API_DELAY = 2.0

CORE_API_KEY = "5tf1LzlNRbZoqwTAVU3XjYPr76ypMmFJ"

RUN_ID = datetime.now().strftime("Andrussow_DB_%Y%m%d_%H%M%S")
BASE_DIR = os.path.join("OUTPUT", RUN_ID)

RAW_DIR = os.path.join(BASE_DIR, "raw")
os.makedirs(RAW_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# SCHEMA FIJO (BASE DE DATOS QUÍMICA)
# ─────────────────────────────────────────────

SCHEMA = {
    "paper_id": "string",
    "title": "string",
    "abstract": "string",
    "year": "Int64",
    "source": "string",
    "doi": "string",
    "url": "string",
    "citations": "Int64",
    "score": "float64",

    "yield_percent": "float64",
    "selectivity": "float64",
    "conversion": "float64",
    "stability_hours": "float64",
    "temperature_c": "float64",
}

# ─────────────────────────────────────────────
# CATALIZADORES ANDRUSSOW
# ─────────────────────────────────────────────

CATALYSTS = [
    "pt", "rh", "ir", "pt-rh",
    "platinum", "rhodium",
    "gauze", "mesh"
]

# ─────────────────────────────────────────────
# TOPIC ANDRUSSOW
# ─────────────────────────────────────────────

TOPICS = {
    "andrussow_hcn": [
        "Andrussow process hydrogen cyanide catalyst",
        "HCN synthesis platinum rhodium gauze",
        "methane ammonia oxidation HCN",
        "ammoxidation Pt Rh catalyst",
        "industrial hydrogen cyanide production catalyst"
    ]
}

# ─────────────────────────────────────────────
# REQUEST SESSION ROBUSTA
# ─────────────────────────────────────────────

session = requests.Session()

retry = Retry(
    total=5,
    backoff_factor=2,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)

adapter = HTTPAdapter(max_retries=retry)

session.mount("https://", adapter)
session.mount("http://", adapter)

HEADERS = {
    "Authorization": f"Bearer {CORE_API_KEY}",
    "User-Agent": "Mozilla/5.0"
}

# ─────────────────────────────────────────────
# PAPER ID
# ─────────────────────────────────────────────

def make_paper_id(title, year):

    base = f"{title}_{year}"

    return hashlib.md5(
        base.encode()
    ).hexdigest()

# ─────────────────────────────────────────────
# CATALYST DETECTION
# ─────────────────────────────────────────────

def detect_catalysts(text):

    if not text:
        return []

    text = text.lower()

    return [
        c for c in CATALYSTS
        if c in text
    ]

# ─────────────────────────────────────────────
# METRICS EXTRACTION
# ─────────────────────────────────────────────

def extract_metrics(text):

    if not text:
        return {}

    text = text.lower()

    yield_m = re.findall(r"(\d{1,3})\s?%\s?(yield|conversion)", text)
    sel_m = re.findall(r"(\d{1,3})\s?%\s?selectivity", text)
    temp_m = re.findall(r"(\d{3,4})\s?°?c", text)
    stab_m = re.findall(r"(\d+)\s?(hours|h|hour)", text)

    return {
        "yield_percent": float(yield_m[0][0]) if yield_m else None,
        "selectivity": float(sel_m[0]) if sel_m else None,
        "temperature_c": int(temp_m[0]) if temp_m else None,
        "stability_hours": int(stab_m[0][0]) if stab_m else None
    }

# ─────────────────────────────────────────────
# SCORE ANDRUSSOW
# ─────────────────────────────────────────────

def score(row):

    text = (row["title"] + " " + row["abstract"]).lower()

    s = 0

    if "andrussow" in text:
        s += 50
    if "hcn" in text:
        s += 30
    if "pt" in text or "platinum" in text:
        s += 20
    if "rh" in text or "rhodium" in text:
        s += 20

    s += (row["selectivity"] or 0) * 2
    s += (row["yield_percent"] or 0) * 1.5

    return s

# ─────────────────────────────────────────────
# ARXIV
# ─────────────────────────────────────────────

def search_arxiv(query):

    try:

        url = (
            "http://export.arxiv.org/api/query?"
            f"search_query=all:{quote(query)}"
            f"&max_results={MAX_RESULTS}"
        )

        feed = feedparser.parse(url)

        results = []

        for e in feed.entries:

            text = e.title + " " + e.summary

            results.append({
                "title": e.title,
                "abstract": e.summary,
                "year": e.published[:4],
                "source": "arXiv",
                "doi": "",
                "url": "",
                "citations": 0,

                **extract_metrics(text),
            })

        return results

    except Exception:
        return []

# ─────────────────────────────────────────────
# CORE API
# ─────────────────────────────────────────────

def search_core(query):

    try:

        url = "https://api.core.ac.uk/v3/search/works"

        r = session.get(
            url,
            headers=HEADERS,
            params={"q": query, "limit": MAX_RESULTS},
            timeout=30
        )

        if r.status_code != 200:
            return []

        data = r.json()

        results = []

        for i in data.get("results", []):

            text = (i.get("title","") + " " + i.get("description",""))

            results.append({

                "title": i.get("title",""),
                "abstract": i.get("description",""),
                "year": i.get("yearPublished",""),
                "source": "CORE",
                "doi": i.get("doi",""),
                "url": i.get("downloadUrl",""),
                "citations": 0,

                **extract_metrics(text)
            })

        return results

    except Exception:
        return []

# ─────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────

def run():

    all_data = []

    for topic, queries in TOPICS.items():

        for q in tqdm(queries):

            all_data += search_arxiv(q)
            all_data += search_core(q)

            time.sleep(API_DELAY + random.uniform(0.5, 1.5))

    # DATAFRAME
    df = pd.DataFrame(all_data)

    if df.empty:
        print("No data found")
        return

    # ───── SCHEMA ENFORCEMENT ─────

    df["paper_id"] = df.apply(
        lambda x: make_paper_id(
            str(x["title"]),
            str(x["year"])
        ),
        axis=1
    )

    # fill missing schema cols
    for col in SCHEMA:
        if col not in df.columns:
            df[col] = None

    # numeric conversion SAFE
    numeric_cols = [
        "year","citations",
        "score",
        "yield_percent",
        "selectivity",
        "conversion",
        "stability_hours",
        "temperature_c"
    ]

    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # catalysts
    df["catalysts"] = df["title"].apply(detect_catalysts)

    # score
    df["score"] = df.apply(score, axis=1)

    # sort
    df = df.sort_values("score", ascending=False)

    # ───── EXPORT ─────

    df.to_parquet(
        os.path.join(RAW_DIR, "andrussow.parquet"),
        index=False
    )

    df.to_csv(
        os.path.join(RAW_DIR, "andrussow.csv"),
        index=False
    )

    df.to_json(
        os.path.join(RAW_DIR, "andrussow.json"),
        orient="records",
        indent=2
    )

    print("\nDONE")
    print("Records:", len(df))
    print("Output:", BASE_DIR)

# ─────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run()