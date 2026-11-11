"""Film poster lookup for the UI: IMDb id -> poster image URL (hotlinked from Wikimedia, never re-hosted).

Source: the English Wikipedia page of the film, through the MediaWiki action API (no key):
  1. Batch pass: for up to 10 films per request we ask for "Title (YEAR film)" (and YEAR +/- 1, because our
     years can be the US release year), "Title (film)" and "Title", with `pilicense=any` so non-free posters
     are returned. A page is accepted only if it is not a disambiguation page, has a lead image, and its short
     description says it is a film from about that year: a wrong poster is worse than none.
  2. Search pass, for films still without a page: a Wikipedia search for "<title> <year> film"; the top
     results go through the same check.

Wikipedia throttles anonymous clients (HTTP 429), so live lookups run on ONE background worker and the HTTP
layer never waits for it: request() answers at once with what is known and lists the rest as pending.
The server also warms the whole catalog in idle time (most voted first) and keeps poster_urls.json.gz up to date
(URLs only, committed), so the demo does not depend on the live API. `python posters.py --warm` does the same
offline. Hits and real misses found live are cached in ~/.cache/group-watch/.

    python posters.py tt0068646 tt5074352     # The Godfather, Dangal
    python posters.py --warm                   # whole catalog (~15-30 min, resumable)
"""

from __future__ import annotations

import gzip
import json
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "GroupWatch-hackathon/1.0 (AWS M&E hackathon prototype)"
CACHE_FILE = Path.home() / ".cache" / "group-watch" / "posters.json"
SEED_FILE = Path(__file__).with_name("poster_urls.json.gz")     # committed: {imdb_id: url} for known posters
RETRY_MISS_AFTER_S = 24 * 3600
MIN_GAP_S = 0.7            # pace between API calls (anonymous clients are throttled)
FILMS_PER_CALL = 10        # 5 candidate titles each, the API takes at most 50 titles per call
LARGE_PX, THUMB_PX = 500, 330
FILM_WORDS = ("film", "movie", "documentary", "animated", "anime")
MISS_VERSION = 3   # bump when the matching rules change, so misses found with older rules are retried

_lock = threading.Lock()
_cache: dict[str, dict] | None = None   # imdb id -> {"url"} or {"miss": True, "at"}
_queue: "queue.Queue[tuple]" = queue.Queue()
_queued: set[str] = set()
_worker: threading.Thread | None = None
_last_call = 0.0
_pause = 1.0
_seed_mtime = 0.0


class Transient(Exception):
    """The API failed (rate limit, 5xx, timeout): the answer is unknown, so it must not be cached as a miss."""


# ---------------------------------------------------------------- cache

def _merge_seed() -> None:
    """Merge the committed seed into the cache (again whenever --warm has grown it)."""
    global _seed_mtime
    try:
        mtime = SEED_FILE.stat().st_mtime
        if mtime == _seed_mtime:
            return
        with gzip.open(SEED_FILE, "rt", encoding="utf-8") as f:
            seed = json.load(f)
        _seed_mtime = mtime
    except (OSError, ValueError, EOFError):
        return
    for k, v in seed.items():
        if not (_cache.get(k) or {}).get("url"):
            _cache[k] = {"url": v}


def _load() -> dict[str, dict]:
    global _cache
    if _cache is None:
        _cache = {}
        _merge_seed()
        try:  # what this machine learned since the seed was built (also overrides old misses)
            _cache.update(json.loads(CACHE_FILE.read_text()))
        except (OSError, ValueError):
            pass
    return _cache


def _save_local() -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_load(), ensure_ascii=False, separators=(",", ":")))
        tmp.replace(CACHE_FILE)
    except OSError:
        pass  # the cache is an optimisation only


def _urls(url: str) -> dict:
    """{"thumb", "large"} from the lead-image URL (Wikimedia thumbnails carry the width in the path)."""
    if "/thumb/" in url:
        return {"thumb": re.sub(r"/\d+px-", f"/{THUMB_PX}px-", url), "large": re.sub(r"/\d+px-", f"/{LARGE_PX}px-", url)}
    return {"thumb": url, "large": url}  # the original file is smaller than the requested size


# ---------------------------------------------------------------- Wikipedia

def _api(params: dict) -> dict:
    """One MediaWiki API call at a gentle pace. Raises Transient on rate limits and other temporary failures."""
    global _last_call, _pause
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    for _ in range(8):
        wait = _last_call + MIN_GAP_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
            _pause = max(1.0, _pause / 2)
            if "error" in data:
                raise Transient(data["error"].get("code", "api error"))
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:  # throttled or busy: wait as long as Wikipedia asks (capped)
                try:
                    asked = float(e.headers.get("Retry-After") or 0)
                except ValueError:
                    asked = 0.0
                _pause = min(max(_pause * 1.7, asked + 1), 65.0)
                time.sleep(_pause)
            else:
                raise Transient(f"HTTP {e.code}") from None
        except (urllib.error.URLError, ValueError, TimeoutError):
            time.sleep(1)
    raise Transient("Wikipedia did not answer")


def _candidates(title: str, year: int) -> list[str]:
    return [f"{title} ({year} film)", f"{title} ({year - 1} film)", f"{title} ({year + 1} film)", f"{title} (film)", title]


def _surnames(director: str | None) -> list[str]:
    return [n.split()[-1].lower() for n in (director or "").split(",") if n.strip() and len(n.split()[-1]) >= 3]


def _accept(page: dict | None, year: int, director: str | None = None) -> str | None:
    """The poster URL if this page is the film from about that year, else None.
    With `director`, the page's description or intro must also name one of the film's directors."""
    if not page or page.get("missing") or page.get("invalid") or "disambiguation" in (page.get("pageprops") or {}):
        return None
    thumb = (page.get("thumbnail") or {}).get("source")
    title_year = re.search(r"\((\d{4})(?: [\w ]+)? film\)$", page.get("title", ""))
    desc = (page.get("description") or "").lower()
    years = [int(y) for y in re.findall(r"\b(1[89]\d\d|20\d\d)\b", desc)]
    # Our year can be the US release, years after the original for foreign films. When the director is also
    # checked, that confirms identity, so a wider gap is fine.
    tol_title, tol_desc = (5, 5) if director else (1, 2)
    if title_year:
        ok = abs(int(title_year.group(1)) - year) <= tol_title
    else:
        ok = any(w in desc for w in FILM_WORDS) and any(abs(y - year) <= tol_desc for y in years)
    if ok and director is not None and _surnames(director):
        text = desc + " " + (page.get("extract") or "").lower()
        ok = any(n in text for n in _surnames(director))
    return thumb.split("?")[0] if ok and thumb else None


def _query(titles: list[str], intro: bool = False) -> dict[str, dict]:
    """Requested title -> resolved page (following normalisation and redirects). `intro` adds the lead sentences."""
    params = {"action": "query", "titles": "|".join(titles), "redirects": 1,
              "prop": "pageimages|description|pageprops" + ("|extracts" if intro else ""), "piprop": "thumbnail",
              "pithumbsize": LARGE_PX, "pilicense": "any", "pilimit": "max", "ppprop": "disambiguation"}
    if intro:
        params.update({"exintro": 1, "explaintext": 1, "exsentences": 2, "exlimit": "max"})
    data = _api(params)
    q = data.get("query", {})
    norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
    redir = {r["from"]: r["to"] for r in q.get("redirects", [])}
    pages = {p["title"]: p for p in q.get("pages", [])}
    out = {}
    for t in titles:
        name = norm.get(t, t)
        out[t] = pages.get(redir.get(name, name))
    return out


def _search(title: str, year: int) -> list[str]:
    data = _api({"action": "query", "list": "search", "srsearch": f"{title} {year} film", "srlimit": 3,
                 "srnamespace": 0, "srprop": ""})
    return [h["title"] for h in data.get("query", {}).get("search", [])]


def fetch_batch(films: list[tuple], search: bool = True) -> dict[str, str | None]:
    """films = [(imdb_id, title, year[, director])], up to FILMS_PER_CALL. {imdb_id: poster URL or None}.
    Raises Transient if Wikipedia failed."""
    films = [(f[0], f[1], f[2], f[3] if len(f) > 3 else None) for f in films]
    out: dict[str, str | None] = {}
    wanted = sorted({c for _, t, y, _ in films for c in _candidates(t, y)})
    pages = _query(wanted)
    risky: dict[str, str] = {}   # imdb id -> page title that matched only through a redirect or another year
    for imdb_id, title, year, director in films:
        out[imdb_id] = None
        exact = {x.lower() for x in (title, f"{title} ({year} film)", f"{title} (film)")}
        for c in _candidates(title, year):
            page = pages.get(c)
            if _accept(page, year):
                if page["title"].lower() in exact:
                    out[imdb_id] = _accept(page, year)
                    break
                risky.setdefault(imdb_id, page["title"])   # e.g. "Aladdin (1993 film)" -> a Golden Films knock-off
    if risky:  # confirm those with the director named in the page intro
        by = {f[0]: f for f in films}
        res = _query(sorted(set(risky.values())), intro=True)
        for imdb_id, page_title in risky.items():
            if out[imdb_id] is None:
                out[imdb_id] = _accept(res.get(page_title), by[imdb_id][2], by[imdb_id][3] or "")
    if search:  # search hits are riskier (remakes, knock-offs): the director must match as well
        for imdb_id, title, year, director in films:
            if out[imdb_id] is None:
                found = _search(title, year)
                if found:
                    res = _query(found, intro=True)
                    out[imdb_id] = next((u for t in found if (u := _accept(res.get(t), year, director or ""))), None)
    return out


# ---------------------------------------------------------------- live lookups (background worker)

_warm_films: list[tuple] = []     # whole catalog, most voted first, fetched only when the UI asks for nothing
_warm_pos = 0
_warm_new = 0


def start_background_warm() -> None:
    """Fill the seed with the whole catalog in idle time (UI requests always go first). Safe to call once."""
    global _warm_films
    import recsys
    _warm_films = [(t["id"], t["title"], t["year"], t.get("director"))
                   for t in sorted(recsys.catalog(), key=lambda t: -t["imdb_votes"])]
    _ensure_worker()


def _next_warm_batch() -> list[tuple]:
    global _warm_pos
    batch = []
    with _lock:
        cache = _load()
        while _warm_pos < len(_warm_films) and len(batch) < FILMS_PER_CALL:
            f = _warm_films[_warm_pos]
            _warm_pos += 1
            hit = cache.get(f[0]) or {}
            if not hit.get("url") and hit.get("v") != MISS_VERSION and f[0] not in _queued:
                batch.append(f)
    return batch


def _write_seed() -> None:
    """Persist every known poster URL into the committed seed (URLs only)."""
    with _lock:
        seed = {k: v["url"] for k, v in _load().items() if v.get("url")}
    tmp = SEED_FILE.with_suffix(".tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(seed, f, separators=(",", ":"))
        tmp.replace(SEED_FILE)
    except OSError:
        pass


def _run() -> None:
    global _warm_new
    while True:
        try:   # 1. what the UI is showing
            batch, live = [_queue.get(timeout=0.5)], True
            while len(batch) < FILMS_PER_CALL:
                try:
                    batch.append(_queue.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:   # 2. idle: keep warming the catalog
            batch, live = _next_warm_batch(), False
            if not batch:
                batch, live = [_queue.get()], True
        try:
            found = fetch_batch(batch)
            with _lock:
                for imdb_id, url in found.items():
                    _load()[imdb_id] = {"url": url} if url else {"miss": True, "at": time.time(), "v": MISS_VERSION}
                _save_local()
            if not live:
                _warm_new += 1
                if _warm_new % 20 == 0 or _warm_pos >= len(_warm_films):
                    _write_seed()
        except Transient:
            time.sleep(5)  # unknown right now: leave it uncached; the UI asks again and it is re-queued
        finally:
            if live:
                with _lock:
                    for f in batch:
                        _queued.discard(f[0])


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="poster-worker", daemon=True)
            _worker.start()


def warm_progress() -> dict:
    with _lock:
        known = sum(1 for v in _load().values() if v.get("url"))
    return {"posters": known, "checked": _warm_pos, "catalog": len(_warm_films)}


def _enqueue(imdb_id: str, title: str, year: int, director: str | None = None) -> None:
    with _lock:
        if imdb_id in _queued:
            return
        _queued.add(imdb_id)
    _ensure_worker()
    _queue.put((imdb_id, title, year, director))


def request(films: list[tuple]) -> tuple[dict[str, dict | None], list[str]]:
    """films = [(imdb_id, title, year, director)] in priority order.
    Returns ({id: {"thumb","large"} or None} for what is known now, [ids still pending]). Never blocks."""
    known: dict[str, dict | None] = {}
    pending: list[str] = []
    with _lock:
        _load()
        _merge_seed()
    for imdb_id, title, year, *rest in films:
        with _lock:
            hit = _load().get(imdb_id)
        if hit and hit.get("url"):
            known[imdb_id] = _urls(hit["url"])
        elif hit and hit.get("miss") and hit.get("v") == MISS_VERSION and time.time() - hit.get("at", 0) < RETRY_MISS_AFTER_S:
            known[imdb_id] = None
        else:
            _enqueue(imdb_id, title, year, rest[0] if rest else None)
            pending.append(imdb_id)
    return known, pending


# ---------------------------------------------------------------- warm the whole catalog into the seed

def warm(limit: int | None = None) -> None:
    import recsys
    films = sorted(recsys.catalog(), key=lambda t: -t["imdb_votes"])[:limit]
    seed: dict[str, str] = {}
    try:
        with gzip.open(SEED_FILE, "rt", encoding="utf-8") as f:
            seed = json.load(f)
    except (OSError, ValueError):
        pass
    tried_file = CACHE_FILE.with_name("posters_warm_tried.json")   # films already checked without a poster
    try:
        tried = set(json.loads(tried_file.read_text()))
    except (OSError, ValueError):
        tried = set()
    todo = [(t["id"], t["title"], t["year"], t.get("director")) for t in films if t["id"] not in seed and t["id"] not in tried]
    print(f"{len(seed)} posters known, {len(todo)} films to check", flush=True)
    t0 = time.time()

    def save():
        tmp = SEED_FILE.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(seed, f, separators=(",", ":"))
        tmp.replace(SEED_FILE)
        tried_file.write_text(json.dumps(sorted(tried)))

    # pass 1: batched page lookups only (fast); pass 2: search for the rest
    for search in (False, True):
        group = [f for f in todo if f[0] not in seed] if search else todo
        for i in range(0, len(group), FILMS_PER_CALL):
            batch = group[i:i + FILMS_PER_CALL]
            try:
                found = fetch_batch(batch, search=search)
            except Transient as e:
                print("  Wikipedia unavailable, stopping here (run again to resume):", e, flush=True)
                save()
                return
            for imdb_id, url in found.items():
                if url:
                    seed[imdb_id] = url
                elif search:
                    tried.add(imdb_id)
            if (i // FILMS_PER_CALL) % 20 == 0:
                save()
                print(f"  {'search' if search else 'batch '} {i + len(batch)}/{len(group)} | {len(seed)} posters | "
                      f"{time.time() - t0:.0f}s", flush=True)
    save()
    print(f"done: {len(seed)} posters for {len(films)} films ({SEED_FILE.name}, {SEED_FILE.stat().st_size // 1024} KB)", flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--warm"]:
        warm(int(sys.argv[2]) if len(sys.argv) > 2 else None)
        sys.exit(0)
    import recsys
    by_id = {t["id"]: t for t in recsys.catalog()}
    ids = sys.argv[1:] or ["tt0068646", "tt5074352", "tt0103639"]
    films = [(i, by_id[i]["title"], by_id[i]["year"], by_id[i].get("director")) for i in ids]
    for (imdb_id, title, year, _), url in zip(films, fetch_batch(films).values()):
        print(title, year, "->", url)
