"""Official trailer lookup: IMDb id -> YouTube video of the trailer, embedded in the UI.

We never download or store the video: the UI embeds it from YouTube
(https://www.youtube-nocookie.com/embed/<key>), which is how studios publish trailers.

Sources, in order (no key is required for the app to work):
  1. TMDB, if a key is available (TMDB_API_KEY or Secrets Manager hackathon/tmdb-api-key, us-east-1).
  2. Wikidata (free, no key): property P1651 "YouTube video ID" of the film with that IMDb id,
     preferring statements marked as trailer. Each candidate is checked with YouTube oEmbed
     so we only return videos that exist and can be embedded.
Returns None when neither source has one; the UI then links to a YouTube search.

    python official_trailer.py tt1375666          # Inception
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

import boto3
from botocore.exceptions import BotoCoreError, ClientError

TMDB = "https://api.themoviedb.org/3"
WIKIDATA = "https://query.wikidata.org/sparql"  # kept for reference; throttled, not used
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
SECRET_ID = "hackathon/tmdb-api-key"
SECRET_REGION = "us-east-1"
USER_AGENT = "GroupWatch-hackathon/1.0 (AWS M&E hackathon prototype)"
TRAILER_ROLE = "Q622550"  # Wikidata item "trailer"


def _get_json(url: str, headers: dict | None = None, data: bytes | None = None, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _result(key: str, name: str | None, source: str, language: str | None = None) -> dict:
    return {"youtube_key": key, "name": name, "language": language, "source": source,
            "embed_url": f"https://www.youtube-nocookie.com/embed/{key}",
            "watch_url": f"https://www.youtube.com/watch?v={key}"}


# ---------- 1. TMDB (optional) ----------

@lru_cache(maxsize=1)
def _tmdb_key() -> str | None:
    """The TMDB key, or None if it is not configured (looked up once per server run)."""
    if os.environ.get("TMDB_API_KEY"):
        return os.environ["TMDB_API_KEY"].strip()
    try:
        sm = boto3.client("secretsmanager", region_name=SECRET_REGION)
        return sm.get_secret_value(SecretId=SECRET_ID)["SecretString"].strip()
    except (ClientError, BotoCoreError):
        return None


def _tmdb(path: str, **params) -> dict:
    key = _tmdb_key()
    headers = {"Accept": "application/json"}
    if key.startswith("eyJ"):  # v4 read access token (JWT)
        headers["Authorization"] = f"Bearer {key}"
    else:                      # v3 API key
        params["api_key"] = key
    return _get_json(f"{TMDB}{path}?{urllib.parse.urlencode(params)}", headers)


def _pick(videos: list[dict]) -> dict | None:
    """Best YouTube trailer: official first, then type Trailer, then Teaser."""
    yt = [v for v in videos if v.get("site") == "YouTube" and v.get("key")]
    for kind, official in (("Trailer", True), ("Trailer", False), ("Teaser", True), ("Teaser", False)):
        for v in yt:
            if v.get("type") == kind and bool(v.get("official")) == official:
                return v
    return None


def _from_tmdb(imdb_id: str, lang: str) -> dict | None:
    if not _tmdb_key():
        return None
    found = _tmdb(f"/find/{imdb_id}", external_source="imdb_id")["movie_results"]
    if not found:
        return None
    for language in dict.fromkeys((lang, "en")):  # viewer's language first, then English
        v = _pick(_tmdb(f"/movie/{found[0]['id']}/videos", language=language).get("results", []))
        if v:
            return _result(v["key"], v.get("name"), "TMDB", language)
    return None


# ---------- 2. Wikidata (no key) ----------

def _oembed_title(key: str) -> str | None:
    """Title of the YouTube video if it exists, can be embedded and is not a paid film, else None."""
    url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
        {"format": "json", "url": f"https://www.youtube.com/watch?v={key}"})
    try:
        info = _get_json(url, timeout=8)
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    # Wikidata's P1651 often points to the full film for rent on the "YouTube Movies" channel:
    # that is not a trailer and does not play embedded, so skip it.
    if info.get("author_name") == "YouTube Movies":
        return None
    return info.get("title")


def _from_wikidata(imdb_id: str) -> dict | None:
    # The Wikidata action API (not the SPARQL endpoint, which is throttled to ~1 request per minute):
    # find the item whose IMDb id (P345) matches, then read its YouTube video ids (P1651).
    found = _get_json(f"{WIKIDATA_API}?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": f"haswbstatement:P345={imdb_id}", "srlimit": 1, "format": "json"}))
    hits = found.get("query", {}).get("search", [])
    if not hits:
        return None
    claims = _get_json(f"{WIKIDATA_API}?" + urllib.parse.urlencode(
        {"action": "wbgetclaims", "entity": hits[0]["title"], "property": "P1651", "format": "json"}))
    candidates = []
    for c in claims.get("claims", {}).get("P1651", []):
        key = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        roles = [q.get("datavalue", {}).get("value", {}).get("id") for q in c.get("qualifiers", {}).get("P3831", [])]
        if isinstance(key, str):
            candidates.append((key, TRAILER_ROLE in roles))
    checked = [(key, is_trailer, _oembed_title(key)) for key, is_trailer in dict.fromkeys(candidates)]
    # Only videos that exist, are embeddable and whose YouTube title says it is a trailer: P1651 often
    # points to clips, soundtracks or foreign-language uploads, and a wrong video is worse than a link.
    checked = [c for c in checked if c[2] and any(w in c[2].lower() for w in ("trailer", "tráiler", "teaser"))]
    checked.sort(key=lambda c: not c[1])  # statements marked as trailer in Wikidata first
    return _result(checked[0][0], checked[0][2], "Wikidata") if checked else None


_found: dict[tuple[str, str], dict] = {}  # only hits are cached, so a source that was down is retried


def lookup(imdb_id: str, lang: str = "es") -> dict | None:
    """Return {"youtube_key", "name", "source", "embed_url", "watch_url", ...} or None if no source has one."""
    if (imdb_id, lang) in _found:
        return _found[(imdb_id, lang)]
    for source in (lambda: _from_tmdb(imdb_id, lang), lambda: _from_wikidata(imdb_id)):
        try:
            found = source()
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError):
            found = None  # a source being down must not break the page; try the next one
        if found:
            _found[(imdb_id, lang)] = found
            return found
    return None


if __name__ == "__main__":
    print(json.dumps(lookup(sys.argv[1] if len(sys.argv) > 1 else "tt1375666"), ensure_ascii=False, indent=2))
