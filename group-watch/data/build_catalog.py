"""Build group-watch/catalog_real.json.gz from two public datasets (raw files stay OUTSIDE the repo).

  * BrightData/IMDb-Media (Hugging Face, Bright Data license): the base. IMDb genres (incl. Sport,
    Biography, Musical), rating, votes, director, countries, languages, plot summary and storyline,
    including films after 2017.  https://huggingface.co/datasets/BrightData/IMDb-Media
  * The Movies Dataset (Rounak Banik, CC0 on Kaggle; public mirror on Hugging Face): adds TMDB
    keywords, runtime and franchise (collection) where the IMDb id matches. (Its TMDB poster paths
    are from 2017 and mostly dead, so they are not used.)
    https://huggingface.co/datasets/gaygaaa/TheMoviesDataset

Download once into ~/.cache/group-watch/ (fetch() does it), then:

    python3 data/build_catalog.py

Keeps well-known films (enough IMDb votes to be recognisable) plus a larger block of non-English
films, with trimmed text so the gzip stays around 2 MB. Prototype use only: check the Bright Data
license before any wider use.
"""

from __future__ import annotations

import ast
import csv
import gzip
import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

CACHE = Path.home() / ".cache" / "group-watch"
OUT = Path(__file__).resolve().parent.parent / "catalog_real.json.gz"
HF = "https://huggingface.co/datasets"
FILES = {
    "movies_metadata.csv": f"{HF}/gaygaaa/TheMoviesDataset/resolve/main/movies_metadata.csv",
    "keywords.csv": f"{HF}/gaygaaa/TheMoviesDataset/resolve/main/keywords.csv",
    "brightdata_imdb_media.csv": f"{HF}/BrightData/IMDb-Media/resolve/main/IMDB%20media.csv",
}
MIN_VOTES_EN = 25_000     # English-language films: well known
MIN_VOTES_OTHER = 4_000   # other original languages are rarer, so the bar is lower
MAX_OTHER = 2_000
GENRES = {"Action", "Adventure", "Animation", "Biography", "Comedy", "Crime", "Documentary", "Drama", "Family",
          "Fantasy", "Film-Noir", "History", "Horror", "Music", "Musical", "Mystery", "Romance", "Sci-Fi", "Sport",
          "Thriller", "War", "Western"}
SUMMARY_CHARS, STORYLINE_CHARS = 240, 380
# Bright Data labels some series and video games as "Feature Film": series have no Director credit,
# and games are given away by their featured user review.
GAME_RE = re.compile(r"\b(gameplay|video ?games?|this game|best game|the game's|games? (i|i've) (ever )?played|ps[345]|"
                     r"playstation|xbox|nintendo|open[- ]world|side quests?|rpg|cutscenes?|dlc)\b", re.I)


def fetch() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        if not (CACHE / name).exists():
            print("downloading", name)
            urllib.request.urlretrieve(url, CACHE / name)


def dedupe(text: str) -> str:
    """IMDb storylines are often the same paragraph twice, glued together."""
    text = re.sub(r"\s+", " ", text or "").strip()
    head = text[:40]
    if head:
        again = text.find(head, len(head))
        if again > 0 and text[again:].startswith(text[:min(len(text) - again, 80)]):
            return text[:again].strip()
    return text


def trim(text: str, n: int) -> str:
    text = dedupe(text)
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > n // 2 else cut + "…"


def banik() -> dict[str, dict]:
    keywords = {}
    with open(CACHE / "keywords.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                keywords[r["id"]] = [k["name"] for k in ast.literal_eval(r["keywords"])][:10]
            except (ValueError, SyntaxError):
                pass
    out = {}
    with open(CACHE / "movies_metadata.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("imdb_id"):
                continue
            try:
                coll = ast.literal_eval(r["belongs_to_collection"])["name"] if r["belongs_to_collection"] else None
            except (ValueError, SyntaxError, TypeError, KeyError):
                coll = None
            try:
                runtime = int(float(r["runtime"] or 0)) or None
            except (ValueError, TypeError):
                runtime = None
            out[r["imdb_id"]] = {"keywords": keywords.get(r["id"], []), "runtime_min": runtime, "collection": coll}
    return out


def director(credit: str) -> str | None:
    try:
        for c in json.loads(credit or "[]"):
            if c.get("title", "").startswith("Director"):
                return ", ".join(n["name"] for n in c.get("names", [])[:2]) or None
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def main() -> int:
    fetch()
    csv.field_size_limit(sys.maxsize)
    extra = banik()
    en, other, skipped = [], [], 0
    with open(CACHE / "brightdata_imdb_media.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["media_type"] not in ("Feature Film", "Documentary") or not r["storyline"] or not r["imdb_rating"]:
                continue
            m = re.search(r"(tt\d+)", r.get("url") or "")
            year = re.search(r"(\d{4})", r.get("details_release_date") or "")
            try:
                votes, rating = int(float(r["imdb_rating_count"] or 0)), float(r["imdb_rating"])
                genres = [g for g in json.loads(r["genres"] or "[]") if g in GENRES]
            except (ValueError, TypeError):
                continue
            if not (m and year and genres):
                continue
            if not director(r["credit"]) or GAME_RE.search(r.get("featured_review") or ""):
                skipped += 1
                continue
            langs = [x.strip() for x in (r["details_language"] or "").split(",") if x.strip()][:4]
            row = {"id": m.group(1), "title": r["title"].strip(), "year": int(year.group(1)), "genres": genres,
                   "director": director(r["credit"]), "countries": (r["details_countries_of_origin"] or "").split(",")[0].strip() or None,
                   "original_language": langs[0] if langs else None, "spoken_languages": langs,
                   "imdb_rating": rating, "imdb_votes": votes,
                   "summary": trim(r.get("presentation", ""), SUMMARY_CHARS), "storyline": trim(r["storyline"], STORYLINE_CHARS),
                   **extra.get(m.group(1), {"keywords": [], "runtime_min": None, "collection": None})}
            if row["original_language"] == "English":
                if votes >= MIN_VOTES_EN:
                    en.append(row)
            elif votes >= MIN_VOTES_OTHER:
                other.append(row)

    other = sorted(other, key=lambda t: -t["imdb_votes"])[:MAX_OTHER]
    seen, titles = set(), []
    for t in sorted(en + other, key=lambda t: -t["imdb_votes"]):
        if t["id"] not in seen:
            seen.add(t["id"])
            titles.append(t)
    payload = {"sources": ["BrightData/IMDb-Media (Hugging Face)", "The Movies Dataset (Rounak Banik, CC0)"],
               "note": "Join on IMDb id. Prototype use only.", "titles": titles}
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    langs = Counter(t["original_language"] for t in titles)
    decades = Counter(t["year"] // 10 * 10 for t in titles)
    print(f"{len(titles)} titles -> {OUT.name} ({OUT.stat().st_size // 1024} KB); skipped {skipped} series/games")
    print("languages:", langs.most_common(10))
    print("decades:", sorted(decades.items()))
    print("with TMDB keywords:", sum(1 for t in titles if t["keywords"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
