import os
import re
import json
import time
import requests
import pandas as pd
from datetime import datetime
from urllib.parse import quote
from tqdm import tqdm
from scholarly import scholarly
import feedparser

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

MAX_RESULTS = 25
API_DELAY = 1.0

CATALYST_FILTER = None   # ej: "ru", "fe", "mo"

CATALYSTS = ["ru","fe","mo","co","ni","pt","pd","cu","zn","ir","rh"]

TOPICS = {
    "green_ammonia": [
        "electrocatalytic nitrogen reduction ammonia catalyst",
        "Haber Bosch green catalyst iron ruthenium",
    ],
    "urea_synthesis": [
        "Bosch Meiser urea catalyst CO2 ammonia",
        "direct urea synthesis catalytic mechanism",
    ],
    "hydroxylamine": [
        "hydroxylamine ammonia oxidation catalyst",
        "selective hydroxylamine synthesis catalyst",
    ]
}

# ─────────────────────────────────────────────
# OUTPUT DIR (ROBUST NAME)
# ─────────────────────────────────────────────

RUN_ID = datetime.now().strftime("Catalysis_Review_%Y%m%d_%H%M%S")
BASE_DIR = os.path.join("OUTPUT", RUN_ID)

os.makedirs(BASE_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# CHEMISTRY EXTRACTION ENGINE
# ─────────────────────────────────────────────

def detect_catalysts(text):
    if not text:
        return []
    text = text.lower()
    return [c for c in CATALYSTS if c in text]


def extract_metrics(text):

    if not text:
        return {"yield": None, "cycles": None, "recovery": None}

    text = text.lower()

    yield_m = re.findall(r"(\d{1,3})\s?%", text)
    cycles_m = re.findall(r"(\d+)\s?(cycles|cycle)", text)
    rec_m = re.findall(r"(\d{1,3})\s?%\s?(recovery|recovered)", text)

    return {
        "yield": int(yield_m[0]) if yield_m else None,
        "cycles": int(cycles_m[0][0]) if cycles_m else None,
        "recovery": int(rec_m[0][0]) if rec_m else None
    }


def score_row(r):

    score = 0

    score += int(r.get("citations") or 0)

    if r.get("yield"):
        score += r["yield"] * 2

    if r.get("cycles"):
        score += r["cycles"] * 3

    if r.get("recovery"):
        score += r["recovery"] * 2

    return score

# ─────────────────────────────────────────────
# SAFE ARXIV
# ─────────────────────────────────────────────

def search_arxiv(query, max_results=20):

    query = quote(query)

    url = f"http://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results={max_results}"

    feed = feedparser.parse(url)

    results = []

    for e in feed.entries:

        text = (e.title + " " + e.summary)

        results.append({
            "source": "arXiv",
            "title": e.title,
            "authors": ", ".join([a.name for a in e.authors]),
            "year": e.published[:4],
            "citations": 0,
            "abstract": e.summary,
            "catalysts": detect_catalysts(text),
            **extract_metrics(text),
        })

    return results

# ─────────────────────────────────────────────
# CORE FIXED (SAFE AUTHORS)
# ─────────────────────────────────────────────

def search_core(query, max_results=20):

    url = "https://api.core.ac.uk/v3/search/works"

    r = requests.get(url, params={"q": query, "limit": max_results})

    data = r.json()

    results = []

    for i in data.get("results", []) or []:

        authors = []

        for a in i.get("authors", []) or []:

            if isinstance(a, dict):
                authors.append(a.get("name",""))
            else:
                authors.append(str(a))

        text = (i.get("title","") + " " + (i.get("description","") or ""))

        results.append({
            "source": "CORE",
            "title": i.get("title",""),
            "authors": "; ".join(authors),
            "year": i.get("yearPublished",""),
            "citations": 0,
            "abstract": i.get("description",""),
            "catalysts": detect_catalysts(text),
            **extract_metrics(text),
        })

    return results

# ─────────────────────────────────────────────
# (otros sources simplificados para foco)
# ─────────────────────────────────────────────

def search_openalex(q): return []
def search_crossref(q): return []
def search_semantic(q): return []
def search_google(q): return []
def search_europe(q): return []

# ─────────────────────────────────────────────
# DEDUP
# ─────────────────────────────────────────────

def normalize(t):
    return re.sub(r"[^a-z0-9 ]","",t.lower()).strip()

def deduplicate(data):

    seen = set()
    out = []

    for r in data:

        key = normalize(r.get("title",""))[:120]

        if key in seen:
            continue

        seen.add(key)
        out.append(r)

    return out

# ─────────────────────────────────────────────
# FILTER
# ─────────────────────────────────────────────

def filter_catalyst(df, cat):

    if not cat:
        return df

    return df[df["catalysts"].astype(str).str.contains(cat)]

# ─────────────────────────────────────────────
# RUN PIPELINE
# ─────────────────────────────────────────────

def run():

    all_data = []

    for topic, queries in TOPICS.items():

        for q in tqdm(queries):

            text = q.lower()

            all_data += search_arxiv(q)
            all_data += search_core(q)

            time.sleep(API_DELAY)

    all_data = deduplicate(all_data)

    df = pd.DataFrame(all_data)

    df["score"] = df.apply(score_row, axis=1)

    df = df.sort_values("score", ascending=False)

    df = filter_catalyst(df, CATALYST_FILTER)

    # ─────────────────────────────
    # EXPORTS
    # ─────────────────────────────

    excel_path = os.path.join(BASE_DIR, "full_dataset.xlsx")
    csv_path = os.path.join(BASE_DIR, "full_dataset.csv")

    df.to_excel(excel_path, index=False)
    df.to_csv(csv_path, index=False)

    # filtered exports
    for c in ["ru","fe","mo","co"]:

        df_c = filter_catalyst(df, c)

        df_c.to_excel(
            os.path.join(BASE_DIR, f"{c}_filtered.xlsx"),
            index=False
        )

    # metadata
    meta = {
        "run_id": RUN_ID,
        "records": len(df),
        "top_score": int(df["score"].max()) if len(df) else 0
    }

    with open(os.path.join(BASE_DIR, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print("\nDONE")
    print("Output:", BASE_DIR)
    print("Records:", len(df))

# ─────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run()