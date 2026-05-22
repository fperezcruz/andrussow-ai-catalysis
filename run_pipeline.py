import os
import re
import json
import time
import requests
import pandas as pd
from datetime import datetime
from urllib.parse import quote
import feedparser

# =========================
# CONFIG
# =========================

CORE_API_KEY = "5tf1LzlNRbZoqwTAVU3XjYPr76ypMmFJ"  # <- pon la tuya

MAX_RESULTS = 20
API_DELAY = 1.0

CATALYSTS = ["ru","fe","mo","co","ni","pt","pd","cu","zn","ir","rh"]

TOPICS = {
    "andrussow_process": [
        "andrussow process ammonia oxidation hydrogen cyanide catalyst",
        "hydrogen cyanide platinum rhodium gauze mechanism",
        "HCN industrial synthesis ammonia methane oxygen catalyst"
    ]
}

RUN_ID = datetime.now().strftime("RUN_%Y%m%d_%H%M%S")
BASE_DIR = os.path.join("OUTPUT", RUN_ID)

os.makedirs(BASE_DIR, exist_ok=True)

# =========================
# EXTRACTORS
# =========================

def detect_catalysts(text):
    text = text.lower()
    return list({c for c in CATALYSTS if c in text})


def extract_metrics(text):
    text = text.lower()

    yield_m = re.findall(r"(\d{1,3})\s?%", text)
    cycles = re.findall(r"(\d+)\s?(cycles|cycle)", text)

    return {
        "yield": int(yield_m[0]) if yield_m else None,
        "cycles": int(cycles[0][0]) if cycles else None
    }


def score_row(r):
    score = 0

    if r.get("yield"):
        score += r["yield"] * 2

    if r.get("cycles"):
        score += r["cycles"] * 3

    score += len(r.get("catalysts", [])) * 5

    return score

# =========================
# ARXIV SEARCH
# =========================

def search_arxiv(query):
    url = f"http://export.arxiv.org/api/query?search_query=all:{quote(query)}&start=0&max_results={MAX_RESULTS}"
    feed = feedparser.parse(url)

    results = []

    for e in feed.entries:
        text = e.title + " " + e.summary

        results.append({
            "source": "arxiv",
            "title": e.title,
            "abstract": e.summary,
            "catalysts": detect_catalysts(text),
            **extract_metrics(text)
        })

    return results

# =========================
# CORE SEARCH (SAFE)
# =========================

def search_core(query):
    url = "https://api.core.ac.uk/v3/search/works"

    headers = {
        "Authorization": f"Bearer {CORE_API_KEY}"
    }

    try:
        r = requests.get(url, headers=headers, params={"q": query, "limit": MAX_RESULTS}, timeout=20)

        if r.status_code != 200:
            print("CORE API error:", r.status_code)
            return []

        data = r.json()

    except Exception as e:
        print("CORE request failed:", e)
        return []

    results = []

    for item in data.get("results", []):

        text = (item.get("title","") + " " + item.get("description",""))

        results.append({
            "source": "core",
            "title": item.get("title",""),
            "abstract": item.get("description",""),
            "catalysts": detect_catalysts(text),
            **extract_metrics(text)
        })

    return results

# =========================
# DEDUP
# =========================

def deduplicate(data):
    seen = set()
    out = []

    for r in data:
        key = r.get("title","").lower().strip()

        if key in seen:
            continue

        seen.add(key)
        out.append(r)

    return out

# =========================
# PIPELINE
# =========================

def run():

    all_data = []

    for topic, queries in TOPICS.items():

        for q in queries:

            print("Query:", q)

            all_data += search_arxiv(q)
            all_data += search_core(q)

            time.sleep(API_DELAY)

    all_data = deduplicate(all_data)

    df = pd.DataFrame(all_data)

    df["score"] = df.apply(score_row, axis=1)

    df = df.sort_values("score", ascending=False)

    # =========================
    # EXPORTS
    # =========================

    df.to_csv(os.path.join(BASE_DIR, "dataset.csv"), index=False)
    df.to_parquet(os.path.join(BASE_DIR, "dataset.parquet"))

    with open(os.path.join(BASE_DIR, "metadata.json"), "w") as f:
        json.dump({
            "records": len(df),
            "top_score": int(df["score"].max()) if len(df) else 0
        }, f, indent=2)

    print("\nDONE")
    print("Output folder:", BASE_DIR)
    print("Records:", len(df))


if __name__ == "__main__":
    run()