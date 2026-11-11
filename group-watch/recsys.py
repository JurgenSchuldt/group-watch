"""Candidate generation and public baseline recommenders over the real catalog (~7,600 films).

Group Watch candidates (used by agent.py):
  1. understand()  A Bedrock call per viewer turns profile + wish for tonight into structured intent:
                   genres and search terms they want, and what they EXPLICITLY reject (genres, themes,
                   franchises, max runtime). Rejections become hard filters for the whole group, so no
                   candidate breaks anyone's stated limits ("no superheroes" removes Batman for everyone).
  2. propose_bridges()  A curator LLM proposes real films the WHOLE group could enjoy, from its film
                   knowledge; only titles found in the catalog are kept (no invented films).
  3. shortlist()   Curator picks + TF-IDF/genre/quality/director/era retrieval per viewer + "bridge" titles
                   that rank well for ALL viewers at once, deduplicated by franchise.

Baselines (used by arena.py), given only the structured profile, as a public recommender would be:
  * imdb_top       IMDb-style chart: weighted rating (Banik's Kaggle formula) within the viewer's genres.
  * content_based  Classic TF-IDF content-based recommender on genres, keywords and synopsis, weighted by
                   rating quality.
  Both only list films with BASELINE_MIN_VOTES+ votes, as public charts do.

Pure Python plus boto3.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

CATALOG_REAL = Path(__file__).with_name("catalog_real.json.gz")
INTENT_MODEL = os.environ.get("INTENT_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
INTENT_CACHE: dict[str, dict] = {}  # (profile, wish, lang) -> intent: the same person and wish never pay twice
INTENT_CACHE_MAX = 2000
RRF_K = 60
M_VOTES = 25_000  # weighted rating: votes needed to trust a title's own rating
GENRES = ["Action", "Adventure", "Animation", "Biography", "Comedy", "Crime", "Documentary", "Drama", "Family",
          "Fantasy", "Film-Noir", "History", "Horror", "Music", "Musical", "Mystery", "Romance", "Sci-Fi", "Sport",
          "Thriller", "War", "Western"]
LANG_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
              "ja": "Japanese", "ko": "Korean", "hi": "Hindi", "zh": "Mandarin", "nl": "Dutch", "tr": "Turkish",
              "ar": "Arabic", "ru": "Russian", "sv": "Swedish", "da": "Danish", "pl": "Polish", "ta": "Tamil"}
STOP = set("the and for with that this from his her their they who are was were has have had into over "
           "after when while about him she its but not out one two all can will more than then them film movie "
           "story life young new find finds must years world".split())


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z\-']{2,}", text.lower()) if w not in STOP]


@lru_cache(maxsize=1)
def catalog() -> list[dict]:
    with gzip.open(CATALOG_REAL, "rt", encoding="utf-8") as f:
        return json.load(f)["titles"]


def _doc_text(t: dict) -> str:
    return " ".join([t["title"]] + t["genres"] * 3 + t["keywords"] * 2 + [t["summary"], t["storyline"]])


@lru_cache(maxsize=1)
def _index() -> dict:
    """TF-IDF vectors per title, a lowercase text blob for hard filters, and the rating prior."""
    docs = [Counter(_tokens(_doc_text(t))) for t in catalog()]
    df = Counter(w for d in docs for w in d)
    n = len(docs)
    idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}
    vecs = []
    for d in docs:
        v = {w: (1 + math.log(c)) * idf[w] for w, c in d.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norm for w, x in v.items()})
    blobs = [" " + " ".join([t["title"]] + t["keywords"] + [t["summary"], t["storyline"]]).lower() + " " for t in catalog()]
    prior = sum(t["imdb_rating"] for t in catalog()) / n
    return {"idf": idf, "vecs": vecs, "blobs": blobs, "prior": prior}


def warm_up() -> None:
    _index()


def weighted_rating(t: dict) -> float:
    v, r, c = t["imdb_votes"], t["imdb_rating"], _index()["prior"]
    return v / (v + M_VOTES) * r + M_VOTES / (v + M_VOTES) * c


def _cosine(query: str) -> list[float]:
    ix = _index()
    q = Counter(w for w in _tokens(query) if w in ix["idf"])
    qv = {w: (1 + math.log(c)) * ix["idf"][w] for w, c in q.items()}
    norm = math.sqrt(sum(x * x for x in qv.values())) or 1.0
    return [sum(x * v.get(w, 0.0) for w, x in qv.items()) / norm for v in ix["vecs"]]


# ---------------------------------------------------------------- 1. understand each viewer (LLM)

INTENT_TOOL = {
    "name": "submit_intent",
    "description": "Structured movie-night intent for one viewer.",
    "inputSchema": {"json": {
        "type": "object",
        "required": ["want_genres", "want_terms", "like_directors", "era", "avoid_genres", "avoid_terms",
                     "understands_languages", "world_cinema", "max_runtime_min", "summary"],
        "properties": {
            "want_genres": {"type": "array", "items": {"type": "string", "enum": GENRES},
                            "description": "Genres this person would enjoy tonight (from profile and wish)."},
            "want_terms": {"type": "array", "items": {"type": "string"},
                           "description": "8-15 lowercase ENGLISH words likely to appear in synopses of films they "
                                          "want, e.g. basketball, coach, boxing, team, championship."},
            "like_directors": {"type": "array", "items": {"type": "string"},
                               "description": "Up to 10 film directors this person would likely enjoy, from your film "
                                              "knowledge (full names as credited). Empty if nothing fits."},
            "era": {"type": "string", "enum": ["any", "classic", "recent"],
                    "description": "classic = mostly before 1990, recent = mostly after 2010."},
            "avoid_genres": {"type": "array", "items": {"type": "string", "enum": GENRES},
                             "description": "ONLY genres the person explicitly rejects (dislikes, notes or wish). "
                                            "Never inferred from what they like. Usually empty."},
            "avoid_terms": {"type": "array", "items": {"type": "string"},
                            "description": "ONLY for explicit rejections: lowercase ENGLISH words or short phrases "
                                           "that identify such films in titles, keywords or synopses, incl. synonyms "
                                           "and franchise names. E.g. 'no superheroes' -> superhero, super hero, "
                                           "superheroes, marvel, dc comics, batman, superman, spider-man, avengers, "
                                           "x-men, justice league, iron man, captain america, wonder woman, thor. "
                                           "Usually empty."},
            "understands_languages": {"type": "array", "items": {"type": "string"},
                                      "description": "English names of languages they can follow, e.g. German, English."},
            "world_cinema": {"type": "boolean", "description": "true if they actively seek non-English/international films."},
            "max_runtime_min": {"type": "integer", "description": "Only if they ask for something short; else 0."},
            "summary": {"type": "string", "description": "One short sentence for the UI: what they want and refuse tonight."},
        }}},
}


def _intent_call(viewer: dict, wish: str, lang: str) -> dict:
    import personas
    system = ("You turn a streaming viewer's profile and their wish for tonight into search intent. "
              "Rejections (avoid_*) are hard filters for the whole group, so fill them ONLY with what the person "
              "explicitly refuses in 'dislikes', 'notes' or the wish; leave them empty otherwise. "
              f"Write 'summary' in language code '{lang}'.")
    payload = {"profile": viewer, "wish_for_tonight": wish or None}
    resp = personas.bedrock().converse(
        modelId=INTENT_MODEL, system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
        toolConfig={"tools": [{"toolSpec": INTENT_TOOL}], "toolChoice": {"tool": {"name": INTENT_TOOL["name"]}}},
        inferenceConfig={"maxTokens": 1200, "temperature": 0})
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"]["input"]
    raise RuntimeError("no intent returned")


def _clean_intent(raw: dict, viewer: dict) -> dict:
    words = lambda xs: [x.strip().lower() for x in xs if isinstance(x, str) and len(x.strip()) >= 3][:30]
    langs = [x for x in raw.get("understands_languages", []) if isinstance(x, str)]
    langs += [LANG_NAMES[c] for c in viewer.get("preferred_languages", []) if c in LANG_NAMES]
    return {"want_genres": [g for g in raw.get("want_genres", []) if g in GENRES],
            "want_terms": words(raw.get("want_terms", [])),
            "avoid_genres": [g for g in raw.get("avoid_genres", []) if g in GENRES],
            "avoid_terms": words(raw.get("avoid_terms", [])),
            "like_directors": [d.strip().lower() for d in raw.get("like_directors", []) if isinstance(d, str) and d.strip()][:10],
            "era": raw.get("era") if raw.get("era") in ("classic", "recent") else "any",
            "dubbing": viewer.get("localization_pref") == "dubbing",
            "languages": sorted(set(langs)), "world_cinema": bool(raw.get("world_cinema")),
            "max_runtime_min": max(int(raw.get("max_runtime_min") or 0), 0),
            "summary": str(raw.get("summary", ""))[:240]}


def understand(viewers: list[dict], wishes: dict[str, str], lang: str = "es") -> dict[str, dict]:
    """Intent per viewer id, in parallel; cached per (profile, wish, language)."""
    import personas

    def work(v):
        wish = wishes.get(v["profile_id"], "")
        key = json.dumps([v, wish, lang], sort_keys=True, ensure_ascii=False)
        if key not in INTENT_CACHE:
            if len(INTENT_CACHE) >= INTENT_CACHE_MAX:
                INTENT_CACHE.pop(next(iter(INTENT_CACHE)))
            INTENT_CACHE[key] = _clean_intent(_intent_call(v, wish, lang), v)
        return v["profile_id"], INTENT_CACHE[key]
    return dict(personas.pmap(work, viewers))


# ---------------------------------------------------------------- 1b. curator: bridge films from film knowledge (LLM)

BRIDGE_MODEL = os.environ.get("BRIDGE_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
BRIDGE_TOOL = {
    "name": "submit_films", "description": "Real films this whole group could enjoy together tonight.",
    "inputSchema": {"json": {"type": "object", "required": ["films"], "properties": {"films": {
        "type": "array", "items": {"type": "object", "required": ["title", "year"], "properties": {
            "title": {"type": "string", "description": "English release title as on IMDb."},
            "year": {"type": "integer"}}}}}}},
}


def _norm(title: str) -> str:
    t = re.sub(r"[^a-z0-9 ]", " ", title.lower().replace("&", " and "))
    return re.sub(r"\s+", " ", re.sub(r"^(the|a|an) ", "", t.strip())).strip()


@lru_cache(maxsize=1)
def _title_index() -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {}
    for i, t in enumerate(catalog()):
        out.setdefault(_norm(t["title"]), []).append((t["year"], i))
    return out


def propose_bridges(viewers: list[dict], intents: dict[str, dict], n: int = 25) -> list[int]:
    """Catalog indexes of real films an LLM curator proposes for the whole group (unmatched titles are dropped)."""
    import personas
    group = [{"name": v.get("display_name", v["profile_id"]), "wants_and_refuses": intents[v["profile_id"]]["summary"],
              "likes_genres": intents[v["profile_id"]]["want_genres"], "languages": intents[v["profile_id"]]["languages"],
              "refuses": intents[v["profile_id"]]["avoid_genres"] + intents[v["profile_id"]]["avoid_terms"][:6]}
             for v in viewers]
    system = (f"You are a film curator. Propose {n} real, well-known feature films (not series, not shorts) that "
              "EVERY person in this group could enjoy together tonight: films that combine what several of them "
              "want, never something any of them refuses. Vary eras, countries and tone. Prefer acclaimed films.")
    resp = personas.bedrock().converse(
        modelId=BRIDGE_MODEL, system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": json.dumps(group, ensure_ascii=False)}]}],
        toolConfig={"tools": [{"toolSpec": BRIDGE_TOOL}], "toolChoice": {"tool": {"name": BRIDGE_TOOL["name"]}}},
        inferenceConfig={"maxTokens": 2000, "temperature": 0.4})
    films = next((b["toolUse"]["input"].get("films", []) for b in resp["output"]["message"]["content"] if "toolUse" in b), [])
    idx, out = _title_index(), []
    for f in films:
        for year, i in idx.get(_norm(str(f.get("title", ""))), []):
            if abs(year - int(f.get("year") or year)) <= 1 and i not in out:
                out.append(i)
                break
    return out


# ---------------------------------------------------------------- 2. hard filters + retrieval

def _violations(i: int, t: dict, intent: dict) -> list[str]:
    """Why title i breaks this viewer's explicit limits (empty list = fine)."""
    out = [g for g in intent["avoid_genres"] if g in t["genres"]]
    blob = _index()["blobs"][i]
    out += [w for w in intent["avoid_terms"] if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", blob)]
    if intent["max_runtime_min"] and t.get("runtime_min") and t["runtime_min"] > intent["max_runtime_min"] + 15:
        out.append(f"> {intent['max_runtime_min']} min")
    return out


def _viewer_scores(intent: dict, allowed: list[int]) -> dict[int, float]:
    """Retrieval score in [0, ~1] for each allowed title index."""
    cat = catalog()
    query = " ".join(intent["want_terms"] + [g.lower() for g in intent["want_genres"]] * 2)
    cos = _cosine(query) if query.strip() else [0.0] * len(cat)
    wg = set(intent["want_genres"])
    directors = intent["like_directors"]
    out = {}
    for i in allowed:
        t = cat[i]
        genre = len(wg & set(t["genres"])) / max(1, min(len(wg), 3)) if wg else 0.0
        quality = (weighted_rating(t) - 5.5) / 3.5
        foreign = t["original_language"] not in intent["languages"]
        if intent["world_cinema"]:
            lang_fit = 0.08 if t["original_language"] != "English" else 0
        else:
            lang_fit = -0.2 if foreign and intent["dubbing"] else (-0.1 if foreign else 0)
        d = (t.get("director") or "").lower()
        auteur = 0.3 if d and any(x in d for x in directors) else 0
        era = 0.15 if (intent["era"] == "classic" and t["year"] < 1990) or (intent["era"] == "recent" and t["year"] >= 2010) else 0
        out[i] = 0.45 * min(cos[i] / 0.35, 1.2) + 0.30 * min(genre, 1.0) + 0.12 * quality + lang_fit + auteur + era
    return out


def _franchise(t: dict) -> str:
    return (t.get("collection") or re.split(r"[:\-–]| \d| II| Part", t["title"])[0]).strip().lower()


def shortlist(viewers: list[dict], intents: dict[str, dict], size: int = 50, curated: list[int] | None = None) -> dict:
    """Candidates that break NO viewer's explicit limits: curator picks, each viewer's best matches, bridge titles."""
    cat = catalog()
    ids = [v["profile_id"] for v in viewers]
    excluded: dict[int, dict[str, list[str]]] = {}
    for i, t in enumerate(cat):
        why = {pid: v for pid in ids if (v := _violations(i, t, intents[pid]))}
        if why:
            excluded[i] = why
    allowed = [i for i in range(len(cat)) if i not in excluded]
    scores = {pid: _viewer_scores(intents[pid], allowed) for pid in ids}

    # rank percentile per viewer, then "bridge" = best worst-percentile across the group
    order, pct = {}, {}
    for pid in ids:
        order[pid] = sorted(allowed, key=lambda i: -scores[pid][i])
        pct[pid] = {i: 1 - r / len(order[pid]) for r, i in enumerate(order[pid])}
    bridge = sorted(allowed, key=lambda i: (-min(pct[p][i] for p in ids), -sum(scores[p][i] for p in ids)))

    picked, franchises, source = [], set(), {}
    allowed_set = set(allowed)

    def take(i, why: str) -> bool:
        f = _franchise(cat[i])
        if i in picked or f in franchises or i not in allowed_set:
            return False
        picked.append(i)
        franchises.add(f)
        source[cat[i]["id"]] = why
        return True

    for i in (curated or [])[:20]:  # the curator's group picks (already real and in the catalog)
        take(i, "curator")
    per_viewer = max(5, (size // 3) // len(ids))  # a third: each viewer's own best matches
    for pid in ids:
        added = 0
        for i in order[pid]:
            if added >= per_viewer:
                break
            added += take(i, pid)
    for i in bridge:  # the rest: titles that rank well for everyone at once
        if len(picked) >= size:
            break
        take(i, "bridge")

    by_reason = Counter()
    for why in excluded.values():
        for pid, reasons in why.items():
            for r in reasons:
                by_reason[(pid, r)] += 1
    return {"titles": [cat[i] for i in picked], "source": source, "curated": sum(1 for v in source.values() if v == "curator"),
            "curated_rejected": sum(1 for i in (curated or []) if i not in allowed_set),
            "excluded": len(excluded), "allowed": len(allowed),
            "excluded_by": [{"profile_id": pid, "reason": r, "count": n} for (pid, r), n in by_reason.most_common(12)],
            "bridge_ids": [cat[i]["id"] for i in bridge[:10]]}


# ---------------------------------------------------------------- baselines (profile only, no LLM)

GENRE_WORDS = {
    "action": "Action", "adventure": "Adventure", "animation": "Animation", "animated": "Animation", "anime": "Animation",
    "biopic": "Biography", "biography": "Biography", "comedy": "Comedy", "sitcom": "Comedy", "romcom": "Comedy",
    "crime": "Crime", "documentary": "Documentary", "drama": "Drama", "family": "Family", "kids": "Family",
    "fantasy": "Fantasy", "history": "History", "historical": "History", "horror": "Horror", "music": "Music",
    "musical": "Musical", "mystery": "Mystery", "romance": "Romance", "romantic": "Romance", "sci-fi": "Sci-Fi",
    "science fiction": "Sci-Fi", "sport": "Sport", "sports": "Sport", "soccer": "Sport", "basketball": "Sport",
    "football": "Sport", "thriller": "Thriller", "war": "War", "western": "Western", "noir": "Film-Noir",
    "comedia": "Comedy", "terror": "Horror", "aventura": "Adventure", "deporte": "Sport", "violencia": "Crime",
}


def taste_terms(profile: dict) -> dict:
    """What a viewer likes, read from the structured profile only (what a public recommender would get)."""
    aff = profile.get("content_affinities") or {}
    likes = list(aff.get("genres", [])) + list(aff.get("themes", [])) + list(aff.get("favorite_sports", []))
    likes += list(profile.get("recent_watch_themes", []))
    words = " ".join(likes).lower()
    find = lambda text: {g for w, g in GENRE_WORDS.items() if re.search(rf"\b{re.escape(w)}", text)}
    return {"text": words, "genres": find(words), "avoid": find(" ".join(aff.get("dislikes", [])).lower()),
            "foreign": bool(re.search(r"world cinema|foreign|international|k-drama|anime|arthouse", words)),
            "classic": bool(re.search(r"classic|retrospective|restoration|golden age", words))}


BASELINE_MIN_VOTES = 50_000  # public charts only list widely seen films (IMDb Top 250 needs 25k+)


def imdb_top(profile: dict, k: int = 100) -> list[tuple[str, float]]:
    """IMDb chart restricted to the viewer's genres (all genres if the profile names none)."""
    tt = taste_terms(profile)
    scored = []
    for t in catalog():
        if t["imdb_votes"] < BASELINE_MIN_VOTES:
            continue
        g = set(t["genres"])
        if tt["avoid"] & g or (tt["genres"] and not tt["genres"] & g):
            continue
        if not tt["genres"] and tt["foreign"] and t["original_language"] == "English":
            continue
        bonus = 0.5 if tt["classic"] and t["year"] <= 1985 else 0
        scored.append((t["id"], weighted_rating(t) + bonus))
    return sorted(scored, key=lambda x: -x[1])[:k]


def content_based(profile: dict, k: int = 100) -> list[tuple[str, float]]:
    """TF-IDF cosine between the viewer's profile tastes and every title."""
    tt = taste_terms(profile)
    cos = _cosine(tt["text"] + " " + " ".join(g.lower() for g in tt["genres"]))
    out = []
    for t, c in zip(catalog(), cos):
        if tt["avoid"] & set(t["genres"]) or t["imdb_votes"] < BASELINE_MIN_VOTES:
            continue
        c += (0.15 if tt["foreign"] and t["original_language"] != "English" else 0)
        c += (0.1 if tt["classic"] and t["year"] <= 1985 else 0)
        c *= 0.6 + 0.4 * max(0.0, (weighted_rating(t) - 5.5) / 3.5)  # usual content + quality hybrid
        out.append((t["id"], c))
    return sorted(out, key=lambda x: -x[1])[:k]


def fuse(rankings: list[list[tuple[str, float]]], k: int = 10) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: titles that rank well for several viewers float up."""
    score: Counter = Counter()
    for ranking in rankings:
        for pos, (tid, _) in enumerate(ranking):
            score[tid] += 1.0 / (RRF_K + pos + 1)
    return score.most_common(k)


def group_lists(profiles: list[dict], k: int = 10) -> dict[str, list[dict]]:
    by_id = {t["id"]: t for t in catalog()}
    out = {}
    for name, fn in (("imdb_top", imdb_top), ("content_based", content_based)):
        out[name] = [{**by_id[tid], "fusion_score": round(s, 4)} for tid, s in fuse([fn(p) for p in profiles], k)]
    return out
