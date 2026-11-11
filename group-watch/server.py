"""Web UI for Group Watch. Standard library HTTP server plus boto3; run it and open the URL.

    python server.py            # http://127.0.0.1:8765

Endpoints: GET /api/profiles, GET /api/catalog, GET /api/official-trailer, POST /api/recommend, POST /api/trailer,
GET /api/jobs/<id>, GET /media/<id>.mp4. Synthetic data only.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import boto3

import agent

HERE = Path(__file__).resolve().parent
LANGS = {"es", "en", "fr", "de", "pt"}
MAX_BODY = 64 * 1024
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
_segments: dict[str, str] | None = None


def segment_names() -> dict[str, str]:
    global _segments
    if _segments is None:
        s3 = boto3.client("s3", region_name=agent.IDE_REGION)
        body = s3.get_object(Bucket=agent.assets_bucket(), Key="audience-360/audience-segments.json")["Body"].read()
        _segments = {s["segment_id"]: s["name"] for s in json.loads(body)["segments"]}
    return _segments


def new_job(kind: str) -> dict:
    job = {"id": uuid.uuid4().hex[:12], "kind": kind, "status": "running", "steps": [], "result": None, "error": None}
    with JOBS_LOCK:
        JOBS[job["id"]] = job
    return job


def stepper(job: dict):
    t_job = job.setdefault("t0", time.time())

    def step(name: str, status: str, detail: str = "") -> None:
        now = time.time()
        with JOBS_LOCK:
            for s in job["steps"]:
                if s["name"] == name:
                    s.update(status=status, detail=detail)
                    if status != "running":
                        s["secs"] = round(now - s["_t"], 1)
                    return
            job["steps"].append({"name": name, "status": status, "detail": detail, "_t": now,
                                 "at": round(now - t_job, 1), "secs": 0.0})
    return step


def log_timing(job: dict, cost: dict | None) -> None:
    """One line per finished search in the server log: total time, slowest steps and slowest Bedrock calls."""
    total = time.time() - job["t0"]
    steps = sorted((s for s in job["steps"] if s.get("secs")), key=lambda s: -s["secs"])[:4]
    calls = (cost or {}).get("slowest_calls", [])[:3]
    print(f"[timing] job {job['id']} {total:.0f}s | steps: " + ", ".join(f"{s['name']} {s['secs']}s" for s in steps)
          + " | bedrock: " + ", ".join(f"{c['model']} {c['secs']}s r{c['retries']} {c['stop']}" for c in calls)
          + (f" | retries {cost.get('retries', 0)} truncated {cost.get('truncated', 0)}" if cost else ""), flush=True)


def clean(text: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def build_viewers(raw: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Turn the request into slim profiles + wishes. Raises ValueError on bad input."""
    if not isinstance(raw, list) or not 2 <= len(raw) <= 6:
        raise ValueError("Elige entre 2 y 6 espectadores")
    ids = [v.get("profile_id") for v in raw if v.get("profile_id")]
    known = agent.load_profiles(ids) if ids else []
    known_by_id = {p["profile_id"]: p for p in known}
    viewers, wishes, guests = [], {}, 0
    for v in raw:
        if v.get("profile_id"):
            if v["profile_id"] in {x["profile_id"] for x in viewers}:
                raise ValueError("Hay un espectador repetido")
            profile = known_by_id[v["profile_id"]]
        else:
            guests += 1
            g = v.get("guest") or {}
            name = clean(g.get("name"), 40)
            if not name:
                raise ValueError("Cada invitado necesita un nombre")
            langs = [l for l in (clean(x, 5).lower() for x in g.get("languages", [])) if l][:4]
            profile = {
                "profile_id": f"GUEST-{guests}", "display_name": name,
                "preferred_languages": langs or ["es"],
                "localization_pref": g.get("localization_pref") if g.get("localization_pref") in
                ("subtitles", "dubbing", "original") else "subtitles",
                "content_affinities": {"genres": [clean(x, 30) for x in g.get("likes", [])][:8],
                                       "dislikes": [clean(x, 30) for x in g.get("dislikes", [])][:8]},
                "notes": clean(g.get("notes"), 200),
            }
        viewers.append(profile)
        wish = clean(v.get("wish"), 300)
        if wish:
            wishes[profile["profile_id"]] = wish
    return viewers, wishes


RESULT_CACHE: dict[str, dict] = {}  # same group + wishes + options -> same answer, no Bedrock cost
RESULT_CACHE_MAX, RESULT_CACHE_TTL_S = 200, 6 * 3600


def run_recommend(job: dict, viewers: list[dict], wishes: dict[str, str], lang: str, compare: bool) -> None:
    import personas
    step = stepper(job)
    key = json.dumps([viewers, wishes, lang, compare], sort_keys=True, ensure_ascii=False)
    try:
        step("Cargar perfiles", "done", ", ".join(v.get("display_name", v["profile_id"]) for v in viewers))
        hit = RESULT_CACHE.get(key)
        if hit and time.time() - hit["at"] < RESULT_CACHE_TTL_S:
            step("Reutilizar búsqueda", "done", "Este grupo ya pidió esto: misma respuesta, sin coste de Bedrock")
            job["result"] = {**hit["result"], "cost": {**hit["result"]["cost"], "cached": True, "usd": 0.0}}
            job["status"] = "done"
            return
        meter = personas.metered()  # counts every Bedrock call of this request, incl. parallel agents
        result = agent.run(viewers, wishes, lang, step=step)
        result["wishes"] = wishes
        result["lang"] = lang
        result["cost_recommend"] = meter.summary()
        if compare and result.get("baselines"):
            try:  # the comparison is a bonus: if it fails the recommendation is still shown
                import arena
                result["arena"] = arena.run(result, viewers, wishes, lang, step=step)
            except Exception as e:
                traceback.print_exc()
                result["arena_error"] = f"{type(e).__name__}: {e}"
        result["cost"] = {**meter.summary(), "cached": False}
        log_timing(job, result["cost"])
        step("Coste de la búsqueda", "done", f"{result['cost']['calls']} llamadas a Bedrock · {result['cost']['usd']:.4f} USD"
             + (f" ({result['cost_recommend']['usd']:.4f} USD sin la comparativa)" if result.get("arena") else ""))
        if len(RESULT_CACHE) >= RESULT_CACHE_MAX:
            RESULT_CACHE.pop(next(iter(RESULT_CACHE)))
        RESULT_CACHE[key] = {"at": time.time(), "result": result}
        job["result"] = result
        job["status"] = "done"
    except Exception as e:  # shown to the user in the UI
        traceback.print_exc()
        job["error"], job["status"] = f"{type(e).__name__}: {e}", "error"


def run_trailer(job: dict, movie: dict, lang: str, viewer_names: list[str]) -> None:
    import trailer  # imported lazily: it pulls in the ComfyUI client
    step = stepper(job)
    try:
        path = trailer.render(movie, lang, viewer_names, job["id"], step)
        job["result"] = {"video": f"/media/{path.name}", "title": movie["title"]}
        job["status"] = "done"
    except Exception as e:
        traceback.print_exc()
        job["error"], job["status"] = f"{type(e).__name__}: {e}", "error"


class Handler(BaseHTTPRequestHandler):
    server_version = "GroupWatch/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        if "/api/jobs/" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("Petición demasiado grande")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                body = (HERE / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")  # always revalidate: the UI changes often during the hackathon
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/profiles":
                segs = segment_names()
                self._json([{
                    "profile_id": p["profile_id"], "name": p["display_name"], "segment_id": p["segment_id"],
                    "segment": segs.get(p["segment_id"], p["segment_id"]), "age_range": p.get("age_range"),
                    "country": p.get("country"), "languages": p.get("preferred_languages", []),
                    "localization_pref": p.get("localization_pref"),
                    "genres": p.get("content_affinities", {}).get("genres", []),
                    "themes": p.get("content_affinities", {}).get("themes", []),
                    "recent": p.get("recent_watch_themes", []),
                } for p in agent.all_profiles().values()])
            elif path == "/api/catalog":
                import recsys
                self._json({"size": len(recsys.catalog())})
            elif path == "/api/official-trailer":
                self._official_trailer()
            elif path == "/api/posters":
                self._posters()
            elif path == "/api/posters/progress":
                import posters
                self._json(posters.warm_progress())
            elif path.startswith("/api/jobs/"):
                job = JOBS.get(path.rsplit("/", 1)[1])
                if job:
                    with JOBS_LOCK:
                        public = {**job, "steps": [{k: v for k, v in s.items() if k != "_t"} for s in job["steps"]]}
                    self._json(public, 200)
                else:
                    self._json({"error": "job not found"}, 404)
            elif re.fullmatch(r"/media/[a-f0-9]{12}\.mp4", path):
                self._serve_video(HERE / "out" / path.rsplit("/", 1)[1])
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _posters(self) -> None:
        """GET /api/posters?ids=tt0068646,tt0110912 (priority order, max 80) -> {"posters": {id: {thumb, large}|null}, "pending": [ids]}.
        Answers at once from cache; unknown posters are fetched in the background, so the UI asks again for the pending ones."""
        from urllib.parse import parse_qs, urlsplit
        import posters
        import recsys
        ids = [i for i in (parse_qs(urlsplit(self.path).query).get("ids") or [""])[0].split(",") if re.fullmatch(r"tt\d{5,10}", i)][:80]
        by_id = {t["id"]: t for t in recsys.catalog()}  # titles come from our catalog, never from the client
        films = [(i, by_id[i]["title"], by_id[i]["year"], by_id[i].get("director")) for i in dict.fromkeys(ids) if i in by_id]
        known, pending = posters.request(films)
        self._json({"posters": known, "pending": pending})

    def _official_trailer(self) -> None:
        """GET /api/official-trailer?id=tt1375666&lang=es -> YouTube trailer via TMDB or Wikidata (embedded, never downloaded)."""
        from urllib.parse import parse_qs, urlsplit
        import official_trailer
        q = parse_qs(urlsplit(self.path).query)
        imdb_id = (q.get("id") or [""])[0]
        lang = (q.get("lang") or ["es"])[0]
        if not re.fullmatch(r"tt\d{5,10}", imdb_id):
            return self._json({"error": "id de IMDb no válido"}, 400)
        try:
            found = official_trailer.lookup(imdb_id, lang if lang in LANGS else "es")
        except Exception:  # the official trailer is a bonus: never fail the page because of it
            traceback.print_exc()
            found = None
        if not found:
            return self._json({"error": "No hay tráiler oficial incrustable para esta película"}, 404)
        self._json(found)

    def _serve_video(self, file: Path) -> None:
        if not file.is_file():
            return self._json({"error": "not found"}, 404)
        size = file.stat().st_size
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else end
            else:
                start = max(size - int(m.group(2)), 0)
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with file.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_POST(self) -> None:
        try:
            data = self._body()
            if self.path == "/api/recommend":
                lang = data.get("lang") if data.get("lang") in LANGS else "es"
                viewers, wishes = build_viewers(data.get("viewers"))
                job = new_job("recommend")
                threading.Thread(target=run_recommend, args=(job, viewers, wishes, lang, data.get("compare", True) is not False),
                                 daemon=True).start()
                self._json({"job_id": job["id"]}, 202)
            elif self.path == "/api/trailer":
                src = JOBS.get(str(data.get("job_id")))
                if not src or src["status"] != "done":
                    raise ValueError("Primero busca una película")
                res = src["result"]
                job = new_job("trailer")
                threading.Thread(target=run_trailer, daemon=True,
                                 args=(job, res["winner"], res["lang"], [v["name"] for v in res["viewers"]])).start()
                self._json({"job_id": job["id"]}, 202)
            else:
                self._json({"error": "not found"}, 404)
        except (ValueError, json.JSONDecodeError) as e:
            self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main() -> None:
    import posters
    import recsys
    recsys.warm_up()  # build the TF-IDF index before the first request
    posters.start_background_warm()  # posters for the whole catalog, in idle time
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    print(f"Group Watch en http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
