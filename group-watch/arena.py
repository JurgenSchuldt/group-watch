"""Arena: one independent agent per viewer judges what Group Watch picked against public recommenders.

For N viewers there are N persona agents (separate Bedrock calls, run in parallel, each seeing ONLY its
own profile and wish for tonight). They rate, from the synopsis alone, the top-3 of three systems:

    group_watch   our mediator (agent.py)
    imdb_top      IMDb-style chart filtered by each viewer's genres (recsys.py)
    content_based TF-IDF content-based recommender (recsys.py)

Options are shuffled and anonymised ("Option 1", "Option 2"...) so the personas do not know which
system produced which title. Code then scores each system and a short verdict is written.
Judges run on personas.JUDGE_MODEL (Nova Pro), a different model family from the Claude agents that picked
the film, so the comparison is not circular. Personas are LLM role-play from synthetic profiles:
an estimate, not real viewer feedback.
"""

from __future__ import annotations

import json
import random
from typing import Callable

import personas
from agent import _bedrock

SYSTEMS = {
    "group_watch": "Group Watch (agente mediador)",
    "imdb_top": "Ranking IMDb por perfil",
    "content_based": "Recomendador por contenido (TF-IDF)",
}
TOP_N = 3
TIE_POINTS = 3  # systems within this many fit points of the best are reported as a tie


def _persona(viewer: dict, wish: str, options: list[dict], lang: str) -> dict[str, dict]:
    """One judge agent per viewer. Uses JUDGE_MODEL, a different model family from the one that picked the film."""
    return personas.rate(viewer, wish, options, lang, personas.JUDGE_MODEL)


def _verdict(summary: dict, lang: str) -> str:
    resp = _bedrock().converse(
        modelId=personas.FAST_MODEL,
        system=[{"text": f"You compare recommender systems for a group of viewers. In 3 sentences, language code '{lang}', "
                 "plain text (no markdown), say which system's top pick fits the group best (the given 'winner' unless it is "
                 "tied) and why, citing what the viewers said. Be honest if systems tie or the winner is weak. Use only the data given."}],
        messages=[{"role": "user", "content": [{"text": json.dumps(summary, ensure_ascii=False)}]}],
        inferenceConfig={"maxTokens": 500, "temperature": 0.3})
    return resp["output"]["message"]["content"][0]["text"].strip()


def run(result: dict, viewers: list[dict], wishes: dict[str, str], lang: str = "es",
        step: Callable[[str, str, str], None] | None = None) -> dict:
    """`result` is agent.run()'s output (needs result['baselines']). Returns the comparison."""
    step = step or (lambda *_: None)
    base = result.get("baselines")
    if not base:
        raise RuntimeError("no baseline recommenders available (real catalog not built)")

    import recsys
    by_id = {t["id"]: t for t in recsys.catalog()}
    lists = {"group_watch": [_option(by_id[r["title_id"]]) for r in result["ranking"][:TOP_N]]}
    for name in ("imdb_top", "content_based"):
        lists[name] = [_option(by_id[t["id"]]) for t in base[name][:TOP_N]]

    # one anonymised, shuffled option list shared by every persona
    pool: dict[str, dict] = {}
    for items in lists.values():
        for it in items:
            pool.setdefault(it["id"], dict(it))
    options = list(pool.values())
    random.Random(7).shuffle(options)
    for i, o in enumerate(options, 1):
        o["option_id"] = f"Option {i}"
    opt_of = {o["id"]: o["option_id"] for o in options}

    step("Jueces a ciegas", "running", f"{len(viewers)} agentes puntúan {len(options)} películas a ciegas…")

    def work(v: dict) -> tuple[str, dict]:
        name = v.get("display_name", v["profile_id"])
        step(f"Juez de {name}", "running", "Leyendo las sinopsis como esa persona…")
        ratings = _persona(v, wishes.get(v["profile_id"], ""), options, lang)
        step(f"Juez de {name}", "done", f"{sum(1 for r in ratings.values() if r['would_watch'])} de {len(options)} opciones le valdrían")
        return v["profile_id"], ratings

    by_viewer = dict(personas.pmap(work, viewers))
    step("Jueces a ciegas", "done", f"{len(viewers)} agentes han votado")

    step("Comparar sistemas", "running", "Puntuando el top-1 y el top-3 de cada sistema…")
    ids = [v["profile_id"] for v in viewers]
    names = {v["profile_id"]: v.get("display_name", v["profile_id"]) for v in viewers}
    systems = []
    for key, items in lists.items():
        def sc(it, pid):
            return by_viewer[pid][opt_of[it["id"]]]
        top1 = items[0]
        s1 = [sc(top1, pid)["score"] for pid in ids]
        all_scores = [sc(it, pid)["score"] for it in items for pid in ids]
        systems.append({
            "key": key, "name": SYSTEMS[key], "top1_min": min(s1), "top1_mean": round(sum(s1) / len(s1), 1),
            "fit": round((min(s1) + sum(s1) / len(s1)) / 2, 1),
            "top3_mean": round(sum(all_scores) / len(all_scores), 1),
            "would_watch": sum(1 for pid in ids if sc(top1, pid)["would_watch"]), "viewers": len(ids),
            "dealbreakers": sum(1 for pid in ids if sc(top1, pid).get("dealbreaker")),
            "top": [{**it, "scores": {pid: {"name": names[pid], "score": sc(it, pid)["score"],
                                           "would_watch": sc(it, pid)["would_watch"],
                                           "dealbreaker": bool(sc(it, pid).get("dealbreaker")),
                                           "reason": sc(it, pid)["reason"]} for pid in ids}} for it in items],
        })
    # fit = half the least happy viewer, half the average: rewards fairness without letting 2 points of noise decide
    systems.sort(key=lambda s: (s["fit"], s["top3_mean"]), reverse=True)
    best = systems[0]
    tied = [s["name"] for s in systems[1:] if best["fit"] - s["fit"] <= TIE_POINTS]
    step("Comparar sistemas", "done", f"Gana: {best['name']}" + (f" (empata con {', '.join(tied)})" if tied else ""))

    step("Veredicto", "running", "Redactando el veredicto…")
    verdict = _verdict({"winner": best["name"], "tied_with": tied,
                        "systems": [{"system": s["name"], "top_pick": s["top"][0]["title"], "min": s["top1_min"],
                                     "mean": s["top1_mean"], "fit": s["fit"], "would_watch": f"{s['would_watch']}/{s['viewers']}",
                                     "refused_by": s["dealbreakers"],
                                     "viewer_reasons": {v["name"]: v["reason"] for v in s["top"][0]["scores"].values()}}
                                    for s in systems]}, lang)
    step("Veredicto", "done", "Listo")
    return {"systems": systems, "winner": best["key"], "tied_with": tied, "verdict": verdict}


def _option(t: dict) -> dict:
    import agent
    return agent.film(t)
