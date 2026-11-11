"""Group Watch: find the film several people with different tastes will all enjoy.

    python agent.py AUD-0001 AUD-0021 AUD-0041
    python agent.py AUD-0001 AUD-0021 --wish AUD-0001="hoy quiero deporte, nada de superhéroes" --lang es

Pipeline (each step reported through `step(name, status, detail)` for the web UI in server.py):
  1. Understand   one Bedrock call per viewer turns profile + wish into intent and explicit limits.
  2. Candidates   a curator agent proposes group films from its film knowledge (kept only if they are in the
                  catalog), plus recsys.shortlist() retrieval: ~50 films that break NO viewer's limits.
  3. Score        one persona agent per viewer (personas.py) scores every candidate as that person,
                  and flags dealbreakers.
  4. Decide       code, not a model: drop dealbreakers and scores under VETO, then maximise the least
                  happy viewer, mean as tie-break.
  5. Explain      where tastes clash and why the pick is a fair middle ground.

Viewer profiles come from the Audience 360 dataset (S3, read at run time). Synthetic data only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable

import boto3

import personas
import recsys

IDE_REGION = "us-east-2"  # S3 assets bucket and stacks live here; Bedrock runs in us-east-1 (personas.py)
MODEL_ID = personas.FAST_MODEL  # explanation; scoring uses personas.SCORER_MODEL
VETO = 25  # a title any viewer scores below this is vetoed

# Profile fields that matter for choosing a film (keeps prompts small).
PROFILE_FIELDS = ("profile_id", "display_name", "segment_id", "age_range", "country", "preferred_languages",
                  "localization_pref", "content_affinities", "recent_watch_themes", "viewing_behavior")

_profiles_cache: dict[str, dict] | None = None
_bedrock = personas.bedrock  # used by trailer.py and arena.py


def assets_bucket() -> str:
    if os.environ.get("HACKATHON_ASSETS_BUCKET"):
        return os.environ["HACKATHON_ASSETS_BUCKET"]
    cf = boto3.client("cloudformation", region_name=IDE_REGION)
    for stack in cf.describe_stacks()["Stacks"]:
        for out in stack.get("Outputs", []):
            if out["OutputKey"] == "HackathonAssetsBucketName":
                return out["OutputValue"]
    raise RuntimeError("HackathonAssetsBucketName not found; set HACKATHON_ASSETS_BUCKET")


def all_profiles() -> dict[str, dict]:
    """All Audience 360 profiles keyed by id (read from S3 once, kept in memory)."""
    global _profiles_cache
    if _profiles_cache is None:
        s3 = boto3.client("s3", region_name=IDE_REGION)
        body = s3.get_object(Bucket=assets_bucket(), Key="audience-360/audience-profiles.json")["Body"].read()
        _profiles_cache = {p["profile_id"]: p for p in json.loads(body)["profiles"]}
    return _profiles_cache


def slim(profile: dict) -> dict:
    return {k: profile[k] for k in PROFILE_FIELDS if k in profile}


def load_profiles(ids: list[str]) -> list[dict]:
    by_id = all_profiles()
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise ValueError(f"unknown profile id(s): {', '.join(missing)}")
    return [slim(by_id[i]) for i in ids]


def film(t: dict) -> dict:
    """Public shape of a catalog title (what the UI, arena and trailer use)."""
    return {"id": t["id"], "title": t["title"], "year": t["year"], "genres": t["genres"], "director": t.get("director"),
            "original_language": t.get("original_language"), "runtime_min": t.get("runtime_min"),
            "imdb_rating": t["imdb_rating"], "imdb_votes": t["imdb_votes"], "summary": t["summary"],
            "synopsis": t["storyline"] or t["summary"]}


def score_with_personas(viewers: list[dict], titles: list[dict], wishes: dict[str, str], lang: str,
                        step: Callable[[str, str, str], None]) -> dict[str, dict[str, dict]]:
    """{profile_id: {title_id: rating}}: one independent agent per viewer, in parallel."""
    options = [{**film(t), "option_id": t["id"]} for t in titles]
    by_title = {t["id"]: t["title"] for t in titles}

    def work(v: dict) -> tuple[str, dict]:
        name = v.get("display_name", v["profile_id"])
        step(f"Agente de {name}", "running", "Leyendo las sinopsis como esa persona…")
        got = personas.rate(v, wishes.get(v["profile_id"], ""), options, lang, personas.SCORER_MODEL)
        best = max(got.items(), key=lambda kv: kv[1]["score"])
        no = sum(1 for r in got.values() if r.get("dealbreaker"))
        step(f"Agente de {name}", "done", f"Su favorita: {by_title[best[0]]} ({best[1]['score']}). Rechaza {no}.")
        return v["profile_id"], got

    step("Agentes por espectador", "running", f"{len(viewers)} agentes puntúan {len(titles)} candidatas, cada uno solo con su perfil…")
    out = dict(personas.pmap(work, viewers))
    step("Agentes por espectador", "done", f"{len(viewers)} agentes han votado")
    return out


def rank(titles: list[dict], ratings: dict[str, dict[str, dict]]) -> list[dict]:
    """Non-vetoed first, then the least happy viewer, then the mean."""
    rows = []
    for t in titles:
        per = {pid: ratings[pid][t["id"]] for pid in ratings}
        vals = [r["score"] for r in per.values()]
        breakers = [pid for pid, r in per.items() if r.get("dealbreaker")]
        rows.append({"title_id": t["id"], "min": min(vals), "mean": round(sum(vals) / len(vals), 1),
                     "dealbreakers": breakers, "vetoed": bool(breakers) or min(vals) < VETO, "viewers": per})
    return sorted(rows, key=lambda r: (not r["vetoed"], r["min"], r["mean"]), reverse=True)


EXPLAIN_TOOL = {
    "name": "submit_explanation", "description": "Explain the group's pick.",
    "inputSchema": {"json": {"type": "object", "required": ["conflicts", "rationale"], "properties": {
        "conflicts": {"type": "string", "description": "1-2 sentences: where the viewers' tastes clash tonight."},
        "rationale": {"type": "string", "description": "2-3 sentences: why the chosen film is a fair middle ground; "
                      "what each person gains and concedes."}}}},
}


def explain(winner: dict, ranking: list[dict], viewers: list[dict], intents: dict[str, dict], lang: str,
            names: dict[str, dict]) -> tuple[str, str]:
    """Conflicts + rationale, about the film the code picked, from each person's own scores and limits."""
    who = {v["profile_id"]: v.get("display_name", v["profile_id"]) for v in viewers}
    best = ranking[0]
    payload = {
        "chosen_film": {k: winner[k] for k in ("title", "year", "genres", "director", "summary")},
        "scores_for_chosen": {who[p]: {"score": r["score"], "reason": r["reason"]} for p, r in best["viewers"].items()},
        "each_person_top3": {who[p]: [names[r["title_id"]]["title"] for r in
                                      sorted(ranking, key=lambda r: -r["viewers"][p]["score"])[:3]] for p in who},
        "what_each_wants_and_refuses": {who[p]: i["summary"] for p, i in intents.items()},
    }
    resp = personas.bedrock().converse(
        modelId=MODEL_ID,
        system=[{"text": f"You explain a group film pick. Language code '{lang}'. Plain text, no markdown. Use only the "
                 "data given; never say the film includes something a person refuses."}],
        messages=[{"role": "user", "content": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
        toolConfig={"tools": [{"toolSpec": EXPLAIN_TOOL}], "toolChoice": {"tool": {"name": EXPLAIN_TOOL["name"]}}},
        inferenceConfig={"maxTokens": 800, "temperature": 0.3})
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            out = block["toolUse"]["input"]
            return out["conflicts"], out["rationale"]
    raise RuntimeError("model returned no explanation")


def run(viewers: list[dict], wishes: dict[str, str], lang: str = "es",
        step: Callable[[str, str, str], None] | None = None) -> dict:
    """Full pipeline. `viewers` are slim profile dicts (guests allowed)."""
    step = step or (lambda *_: None)
    ids = [v["profile_id"] for v in viewers]
    display = {v["profile_id"]: v.get("display_name", v["profile_id"]) for v in viewers}

    step("Entender a cada persona", "running", "Leyendo perfiles y deseos: qué quiere y qué no acepta cada uno…")
    intents = recsys.understand(viewers, wishes, lang)
    limits = sum(len(i["avoid_genres"]) + len(i["avoid_terms"]) + bool(i["max_runtime_min"]) for i in intents.values())
    step("Entender a cada persona", "done", f"{limits} límites explícitos en el grupo" if limits else "Nadie ha puesto límites explícitos")

    catalog = recsys.catalog()
    step("Buscar candidatas", "running", f"Filtrando {len(catalog):,} películas…".replace(",", "."))
    step("Agente curador", "running", "Proponiendo películas que puedan gustar a todo el grupo…")
    curated = recsys.propose_bridges(viewers, intents)
    step("Agente curador", "done", f"{len(curated)} propuestas encontradas en el catálogo")
    sl = recsys.shortlist(viewers, intents, curated=curated)
    titles = sl["titles"]
    extra = f"; {sl['curated_rejected']} del curador no pasaban algún límite" if sl["curated_rejected"] else ""
    step("Buscar candidatas", "done", f"{len(titles)} candidatas; {sl['excluded']} descartadas por los límites de alguien{extra}")

    ratings = score_with_personas(viewers, titles, wishes, lang, step)

    step("Buscar el punto medio", "running", "Maximizando la puntuación de la persona menos satisfecha…")
    ranking = rank(titles, ratings)
    names = {t["id"]: t for t in titles}
    best = ranking[0]
    vetoed = sum(1 for r in ranking if r["vetoed"])
    step("Buscar el punto medio", "done", f"{vetoed} con veto; ganadora: {names[best['title_id']]['title']}")

    winner = film(names[best["title_id"]])
    step("Explicar la decisión", "running", "Redactando por qué es el punto medio…")
    conflicts, rationale = explain(winner, ranking, viewers, intents, lang, names)
    step("Explicar la decisión", "done", "Listo")

    by_id = {t["id"]: t for t in catalog}
    baselines = {k: [film(by_id[t["id"]]) for t in v] for k, v in recsys.group_lists(viewers, 10).items()}
    return {
        "winner": {**winner, "min": best["min"], "mean": best["mean"]},
        "conflicts": conflicts, "rationale": rationale, "veto": VETO,
        "catalog_size": len(catalog), "candidates": len(titles), "excluded": sl["excluded"], "curated": sl["curated"],
        "excluded_by": [{**e, "name": display[e["profile_id"]]} for e in sl["excluded_by"]],
        "intents": [{"profile_id": p, "name": display[p], **intents[p]} for p in ids],
        "viewers": [{"profile_id": i, "name": display[i]} for i in ids],
        "baselines": baselines,
        "ranking": [{"title_id": r["title_id"], "title": names[r["title_id"]]["title"], "year": names[r["title_id"]]["year"],
                     "genres": names[r["title_id"]]["genres"], "min": r["min"], "mean": r["mean"], "vetoed": r["vetoed"],
                     "source": sl["source"].get(r["title_id"]), "film": film(names[r["title_id"]]),
                     "dealbreakers": r["dealbreakers"],
                     "scores": {p: {"score": v["score"], "reason": v["reason"], "dealbreaker": bool(v.get("dealbreaker"))}
                                for p, v in r["viewers"].items()}}
                    for r in ranking],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pick a film a group will all enjoy.")
    ap.add_argument("profiles", nargs="+", help="Audience 360 profile ids, e.g. AUD-0001 AUD-0021")
    ap.add_argument("--wish", action="append", default=[], metavar="ID=text", help="what that person wants tonight")
    ap.add_argument("--lang", default="es", help="language of the explanation (default es)")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args(argv)
    if len(args.profiles) < 2:
        ap.error("give at least two profile ids")
    wishes = dict(w.split("=", 1) for w in args.wish)
    stray = [k for k in wishes if k not in args.profiles]
    if stray:
        ap.error(f"--wish for a profile not in the group: {', '.join(stray)}")

    result = run(load_profiles(args.profiles), wishes, args.lang,
                 step=lambda n, s, d: print(f"[{s}] {n}: {d}", file=sys.stderr))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    names = {v["profile_id"]: v["name"] for v in result["viewers"]}
    for i in result["intents"]:
        print(f"{i['name']}: {i['summary']}")
    print(f"\nChoques: {result['conflicts']}\n\nRanking (peor / media):")
    for r in result["ranking"][:5]:
        print(f"  {r['title']:<40} min {r['min']:>3}  media {r['mean']:.0f}{'  (veto)' if r['vetoed'] else ''}")
    print(f"\nElegida: {result['winner']['title']} ({result['winner']['year']})")
    for pid, v in result["ranking"][0]["scores"].items():
        print(f"  - {names[pid]} ({v['score']}): {v['reason']}")
    print(f"\n{result['rationale']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
