import os
import re
import json
import time
import requests
import pandas as pd
from datetime import datetime
from urllib.parse import quote

import fitz  # PyMuPDF
import camelot
import tabula

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

MAX_RESULTS = 20
API_DELAY = 1.0

CATALYSTS = ["ru","fe","mo","co","ni","pt","pd","cu","zn","ir","rh"]

OUTPUT_ROOT = "CATALYSIS_INDUSTRIAL_OUTPUT"

os.makedirs(OUTPUT_ROOT, exist_ok=True)

# ─────────────────────────────────────────────
# QUERY BUILDER
# ─────────────────────────────────────────────

def build_query(base):
    return f"""
    ({base})
    AND (catalyst OR electrocatal* OR photocatal* OR heterogeneous)
    AND (TON OR TOF OR selectivity OR yield OR conversion)
    """

# ─────────────────────────────────────────────
# PDF TEXT EXTRACTION
# ─────────────────────────────────────────────

def extract_text_pdf(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        return " ".join([page.get_text() for page in doc])
    except:
        return ""

# ─────────────────────────────────────────────
# TABLE EXTRACTION
# ─────────────────────────────────────────────

def extract_tables_camelot(pdf_path):
    try:
        return camelot.read_pdf(pdf_path, pages="all", flavor="lattice")
    except:
        return []

def extract_tables_tabula(pdf_path):
    try:
        return tabula.read_pdf(pdf_path, pages="all", multiple_tables=True)
    except:
        return []

# ─────────────────────────────────────────────
# TABLE FILTER
# ─────────────────────────────────────────────

def is_catalysis_table(df):
    text = " ".join(df.astype(str).values.flatten()).lower()

    keywords = [
        "ton","tof","selectivity",
        "conversion","yield",
        "catalyst","ammonia",
        "reaction","activity"
    ]

    return sum(k in text for k in keywords) >= 3

# ─────────────────────────────────────────────
# METRICS EXTRACTION
# ─────────────────────────────────────────────

def extract_metrics_from_table(df):
    text = " ".join(df.astype(str).values.flatten()).lower()

    def find(pattern):
        m = re.findall(pattern, text)
        return float(m[0]) if m else None

    return {
        "TON": find(r"ton[^0-9]{0,10}(\d+\.?\d*)"),
        "TOF": find(r"tof[^0-9]{0,10}(\d+\.?\d*)"),
        "selectivity_%": find(r"selectivity[^0-9]{0,10}(\d{1,3})"),
        "conversion_%": find(r"conversion[^0-9]{0,10}(\d{1,3})"),
        "yield_%": find(r"yield[^0-9]{0,10}(\d{1,3})")
    }

# ─────────────────────────────────────────────
# NORMALIZACIÓN CATALIZADORES
# ─────────────────────────────────────────────

def normalize_catalyst_string(text):
    text = text.lower()
    text = text.replace("–", "/")
    text = text.replace("-", "/")
    text = text.replace(" on ", "/")
    text = text.replace(" supported on ", "/")
    return text

# ─────────────────────────────────────────────
# CATALYST DETECTION (AVANZADO)
# ─────────────────────────────────────────────

def detect_catalysts(text):

    if not text:
        return []

    text = text.lower()

    metals = ["ru","fe","mo","co","ni","pt","pd","cu","zn","ir","rh"]

    supports = [
        "tio2","al2o3","sio2","ceo2","zro2",
        "carbon","graphene","cnt","mgo"
    ]

    synthesis = [
        "impregnation","wet impregnation","incipient wetness",
        "solvothermal","hydrothermal","deposition",
        "coprecipitation","sol-gel","deposition-precipitation",
        "electrodeposition"
    ]

    found = []

    for m in metals:
        if m in text:

            support_found = [s for s in supports if s in text]
            synth_found = [s for s in synthesis if s in text]

            found.append({
                "metal": m,
                "support": support_found if support_found else None,
                "synthesis": synth_found if synth_found else None
            })

    # DEDUP interno
    unique = []
    seen = set()

    for f in found:
        key = (
            f["metal"],
            tuple(sorted(f["support"])) if f["support"] else None,
            tuple(sorted(f["synthesis"])) if f["synthesis"] else None
        )

        if key not in seen:
            seen.add(key)
            unique.append(f)

    return unique

# ─────────────────────────────────────────────
# RELEVANCE SCORE
# ─────────────────────────────────────────────

def relevance_score(text):
    keys = ["ton","tof","selectivity","yield","conversion","catalyst","ammonia"]
    text = text.lower()
    return sum(k in text for k in keys)

# ─────────────────────────────────────────────
# ARXIV
# ─────────────────────────────────────────────

def search_arxiv(query, max_results=20):

    query = quote(query)
    url = f"http://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results={max_results}"

    import feedparser
    feed = feedparser.parse(url)

    results = []

    for e in feed.entries:

        text = e.title + " " + e.summary

        results.append({
            "source": "arXiv",
            "title": e.title,
            "year": e.published[:4],
            "abstract": e.summary,
            "text": text,
            "catalysts": json.dumps(detect_catalysts(text), ensure_ascii=False),
            "score": relevance_score(text),
            "pdf_path": None
        })

    return results

# ─────────────────────────────────────────────
# CORE
# ─────────────────────────────────────────────

def search_core(query, max_results=20):

    url = "https://api.core.ac.uk/v3/search/works"

    r = requests.get(url, params={"q": query, "limit": max_results})

    data = r.json()

    results = []

    for i in data.get("results", []) or []:

        text = (i.get("title","") + " " + (i.get("description","") or ""))

        results.append({
            "source": "CORE",
            "title": i.get("title",""),
            "year": i.get("yearPublished",""),
            "abstract": i.get("description",""),
            "text": text,
            "catalysts": json.dumps(detect_catalysts(text), ensure_ascii=False),
            "score": relevance_score(text),
            "pdf_path": None
        })

    return results

# ─────────────────────────────────────────────
# PDF PROCESSOR
# ─────────────────────────────────────────────

def process_pdf(pdf_path, title):

    results = []

    tables = extract_tables_camelot(pdf_path)

    if not tables:
        tables = extract_tables_tabula(pdf_path)

    for t in tables:

        try:

            if hasattr(t, "df"):
                df = t.df
            else:
                df = t

            if not is_catalysis_table(df):
                continue

            metrics = extract_metrics_from_table(df)

            results.append({
                "title": title,
                "TON": metrics["TON"],
                "TOF": metrics["TOF"],
                "selectivity_%": metrics["selectivity_%"],
                "conversion_%": metrics["conversion_%"],
                "yield_%": metrics["yield_%"],
                "table_shape": str(df.shape)
            })

        except:
            continue

    return results

# ─────────────────────────────────────────────
# DEDUP GLOBAL
# ─────────────────────────────────────────────

def normalize(t):
    return re.sub(r"[^a-z0-9 ]","",t.lower()).strip()

def deduplicate(data):

    seen = set()
    out = []

    for r in data:

        title = normalize(r.get("title",""))[:120]
        catalysts = normalize(str(r.get("catalysts","")))

        key = title + "||" + catalysts

        if key in seen:
            continue

        seen.add(key)
        out.append(r)

    return out

# ─────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────

def run():

    RUN_ID = datetime.now().strftime("CATALYSIS_%Y%m%d_%H%M%S")
    OUT_DIR = os.path.join(OUTPUT_ROOT, RUN_ID)

    os.makedirs(OUT_DIR, exist_ok=True)

    base_queries = [
        "ammonia synthesis",
        "nitrogen reduction reaction",
        "hydroxylamine synthesis",
        "urea Bosch Meiser"
    ]

    all_data = []

    print("\n🚀 STARTING INDUSTRIAL CATALYSIS PIPELINE\n")

    for q in base_queries:

        query = build_query(q)

        print("QUERY:", query)

        all_data += search_arxiv(query)
        all_data += search_core(query)

        time.sleep(API_DELAY)

    enriched = []

    for r in all_data:

        pdf = r.get("pdf_path")

        if pdf and os.path.exists(pdf):

            tables = process_pdf(pdf, r["title"])

            if tables:
                for t in tables:
                    r.update(t)

        enriched.append(r)

    enriched = deduplicate(enriched)

    df = pd.DataFrame(enriched)
    df["score"] = df["score"].fillna(0)
    df = df.sort_values("score", ascending=False)

    excel_path = os.path.join(OUT_DIR, "full_dataset.xlsx")
    csv_path = os.path.join(OUT_DIR, "full_dataset.csv")

    df.to_excel(excel_path, index=False)
    df.to_csv(csv_path, index=False)

    for c in ["ru","fe","mo","co","ni"]:

        df[df["catalysts"].astype(str).str.contains(c)].to_excel(
            os.path.join(OUT_DIR, f"{c}_papers.xlsx"),
            index=False
        )

    meta = {
        "run_id": RUN_ID,
        "papers": len(df),
        "top_score": float(df["score"].max()) if len(df) else 0
    }

    with open(os.path.join(OUT_DIR, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print("\n✅ DONE")
    print("OUTPUT:", OUT_DIR)
    print("PAPERS:", len(df))

# ─────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run()