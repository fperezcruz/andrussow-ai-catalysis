"""
=============================================================================
ADVANCED SCIENTIFIC LITERATURE EXTRACTOR (FINAL FIXED VERSION)
=============================================================================

FUENTES:
- OpenAlex
- CrossRef
- Semantic Scholar
- Google Scholar
- CORE
- Europe PMC
- arXiv

SIN API KEYS

=============================================================================
"""

import requests
import pandas as pd
import time
import os
import re
import feedparser
from urllib.parse import quote
from datetime import datetime
from tqdm import tqdm
from scholarly import scholarly

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

MAX_RESULTS = 30
API_DELAY = 1.0

OUTPUT_DIR = "scientific_results"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# TOPICS
# ─────────────────────────────────────────────────────────────

TOPICS = {
    "hydroxylamine": [
        "hydroxylamine ammonia catalyst",
        "green hydroxylamine synthesis",
    ],
    "urea": [
        "urea Bosch Meiser catalyst",
        "direct urea synthesis CO2 ammonia",
    ],
    "green_ammonia": [
        "electrocatalytic nitrogen reduction ammonia",
        "photocatalytic ammonia synthesis catalyst",
    ]
}

# ─────────────────────────────────────────────────────────────
# UTIL
# ─────────────────────────────────────────────────────────────

def normalize_title(title):
    if not title:
        return ""
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()

# ─────────────────────────────────────────────────────────────
# OPENALEX (FIXED NoneType SAFE)
# ─────────────────────────────────────────────────────────────

def search_openalex(query, max_results=30):

    url = "https://api.openalex.org/works"

    try:
        r = requests.get(url, params={"search": query, "per-page": max_results}, timeout=30)
        data = r.json()
    except:
        return []

    results = []

    for item in data.get("results", []) or []:

        try:
            authors = [
                (a.get("author") or {}).get("display_name", "")
                for a in (item.get("authorships") or [])
            ]

            journal = (
                ((item.get("primary_location") or {})
                .get("source") or {})
                .get("display_name", "")
            )

            pdf_url = (item.get("open_access") or {}).get("oa_url", "")

            results.append({
                "source": "OpenAlex",
                "title": item.get("display_name", ""),
                "authors": "; ".join(authors),
                "journal": journal,
                "year": item.get("publication_year", ""),
                "doi": item.get("doi", ""),
                "abstract": "",
                "citations": item.get("cited_by_count", 0),
                "url": item.get("id", ""),
                "pdf_url": pdf_url,
            })

        except:
            continue

    return results

# ─────────────────────────────────────────────────────────────
# CROSSREF
# ─────────────────────────────────────────────────────────────

def search_crossref(query, max_results=30):

    url = "https://api.crossref.org/works"

    try:
        r = requests.get(url, params={"query": query, "rows": max_results}, timeout=30)
        data = r.json()
    except:
        return []

    results = []

    for item in data.get("message", {}).get("items", []) or []:

        try:
            authors = []

            for a in item.get("author", []) or []:
                authors.append(
                    f"{a.get('given','')} {a.get('family','')}"
                )

            title = (item.get("title") or [""])[0]
            doi = item.get("DOI", "")

            results.append({
                "source": "CrossRef",
                "title": title,
                "authors": "; ".join(authors),
                "journal": (item.get("container-title") or [""])[0],
                "year": "",
                "doi": doi,
                "abstract": "",
                "citations": 0,
                "url": f"https://doi.org/{doi}" if doi else "",
                "pdf_url": "",
            })

        except:
            continue

    return results

# ─────────────────────────────────────────────────────────────
# SEMANTIC SCHOLAR
# ─────────────────────────────────────────────────────────────

def search_semantic_scholar(query, max_results=30):

    url = "https://api.semanticscholar.org/graph/v1/paper/search"

    try:
        r = requests.get(url, params={
            "query": query,
            "limit": max_results,
            "fields": "title,authors,year,abstract,url,citationCount,openAccessPdf"
        }, timeout=30)

        data = r.json()
    except:
        return []

    results = []

    for item in data.get("data", []) or []:

        try:
            authors = [a.get("name", "") for a in item.get("authors", []) or []]

            results.append({
                "source": "SemanticScholar",
                "title": item.get("title", ""),
                "authors": "; ".join(authors),
                "journal": "",
                "year": item.get("year", ""),
                "doi": "",
                "abstract": item.get("abstract", ""),
                "citations": item.get("citationCount", 0),
                "url": item.get("url", ""),
                "pdf_url": (item.get("openAccessPdf") or {}).get("url", ""),
            })

        except:
            continue

    return results

# ─────────────────────────────────────────────────────────────
# GOOGLE SCHOLAR
# ─────────────────────────────────────────────────────────────

def search_google_scholar(query, max_results=15):

    results = []

    try:
        search = scholarly.search_pubs(query)

        for _ in range(max_results):

            try:
                pub = next(search)
                bib = pub.get("bib", {})

                authors = bib.get("author", "")
                if isinstance(authors, list):
                    authors = "; ".join(authors)

                results.append({
                    "source": "GoogleScholar",
                    "title": bib.get("title", ""),
                    "authors": authors,
                    "journal": bib.get("venue", ""),
                    "year": bib.get("pub_year", ""),
                    "doi": "",
                    "abstract": bib.get("abstract", ""),
                    "citations": pub.get("num_citations", 0),
                    "url": pub.get("pub_url", ""),
                    "pdf_url": "",
                })

            except StopIteration:
                break
            except:
                continue

    except:
        pass

    return results

# ─────────────────────────────────────────────────────────────
# CORE (FIXED AUTHORS + SAFE STRINGS)
# ─────────────────────────────────────────────────────────────

def search_core(query, max_results=30):

    url = "https://api.core.ac.uk/v3/search/works"

    try:
        r = requests.get(url, params={"q": query, "limit": max_results}, timeout=30)
        data = r.json()
    except:
        return []

    results = []

    for item in data.get("results", []) or []:

        try:
            raw_authors = item.get("authors", [])

            authors = []

            for a in raw_authors:

                if isinstance(a, dict):
                    authors.append(a.get("name", ""))
                else:
                    authors.append(str(a))

            results.append({
                "source": "CORE",
                "title": item.get("title", ""),
                "authors": "; ".join(authors),
                "journal": item.get("publisher", ""),
                "year": item.get("yearPublished", ""),
                "doi": item.get("doi", ""),
                "abstract": item.get("description", ""),
                "citations": 0,
                "url": item.get("downloadUrl", ""),
                "pdf_url": item.get("downloadUrl", ""),
            })

        except:
            continue

    return results

# ─────────────────────────────────────────────────────────────
# EUROPE PMC
# ─────────────────────────────────────────────────────────────

def search_europe_pmc(query, max_results=30):

    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    try:
        r = requests.get(url, params={
            "query": query,
            "format": "json",
            "pageSize": max_results
        }, timeout=30)

        data = r.json()
    except:
        return []

    results = []

    for item in (data.get("resultList", {}).get("result", []) or []):

        results.append({
            "source": "EuropePMC",
            "title": item.get("title", ""),
            "authors": item.get("authorString", ""),
            "journal": item.get("journalTitle", ""),
            "year": item.get("pubYear", ""),
            "doi": item.get("doi", ""),
            "abstract": item.get("abstractText", ""),
            "citations": item.get("citedByCount", 0),
            "url": item.get("fullTextUrlList", ""),
            "pdf_url": "",
        })

    return results

# ─────────────────────────────────────────────────────────────
# ARXIV (FIX URL ENCODING ERROR)
# ─────────────────────────────────────────────────────────────

def search_arxiv(query, max_results=30):

    query_encoded = quote(query)

    url = f"http://export.arxiv.org/api/query?search_query=all:{query_encoded}&start=0&max_results={max_results}"

    try:
        feed = feedparser.parse(url)
    except:
        return []

    results = []

    for entry in feed.entries or []:

        try:
            authors = [a.name for a in entry.authors]

            pdf_url = ""

            for link in entry.links:
                if "pdf" in link.href:
                    pdf_url = link.href

            results.append({
                "source": "arXiv",
                "title": entry.title,
                "authors": "; ".join(authors),
                "journal": "arXiv",
                "year": entry.published[:4],
                "doi": "",
                "abstract": entry.summary,
                "citations": 0,
                "url": entry.link,
                "pdf_url": pdf_url,
            })

        except:
            continue

    return results

# ─────────────────────────────────────────────────────────────
# DEDUPLICATION
# ─────────────────────────────────────────────────────────────

def normalize(title):
    if not title:
        return ""
    title = title.lower()
    title = re.sub(r"[^a-z0-9 ]", "", title)
    return re.sub(r"\s+", " ", title).strip()

def deduplicate(records):

    seen = set()
    unique = []

    for r in records:

        key = (
            str(r.get("doi", "")).lower().strip(),
            normalize(r.get("title", ""))[:120]
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(r)

    return unique

# ─────────────────────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────────────────────

def run():

    all_data = []

    for topic, queries in TOPICS.items():

        for q in tqdm(queries):

            q_clean = q.strip()

            all_data.extend(search_openalex(q_clean))
            all_data.extend(search_crossref(q_clean))
            all_data.extend(search_semantic_scholar(q_clean))
            all_data.extend(search_google_scholar(q_clean))
            all_data.extend(search_core(q_clean))
            all_data.extend(search_europe_pmc(q_clean))
            all_data.extend(search_arxiv(q_clean))

            time.sleep(API_DELAY)

    all_data = deduplicate(all_data)

    df = pd.DataFrame(all_data)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    out_xlsx = os.path.join(OUTPUT_DIR, f"literature_{ts}.xlsx")
    out_csv = os.path.join(OUTPUT_DIR, f"literature_{ts}.csv")

    df.to_excel(out_xlsx, index=False)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    print("\nDONE")
    print("Records:", len(df))
    print(out_xlsx)
    print(out_csv)

if __name__ == "__main__":
    run()