"""Batch evaluation of Group Watch: N random groups -> same scores the web UI shows -> averages.

Standalone and read-only with respect to /workshop/group-watch: at start it copies that folder's
code to a temp dir and imports the copy, so teammates can keep editing while a long run is going.

    python eval_recommender.py --n 2  --out out-smoke      # smoke test (~0.5 USD)
    python eval_recommender.py --n 100 --workers 3         # full run; resumable (re-run the same command)

For each case it does what the UI does: agent.run() (Group Watch pick) + arena.run() (blind judges
rate the top-3 of Group Watch, IMDb ranking and TF-IDF). Needs AWS credentials (Bedrock in us-east-1,
S3 profiles). Rough cost: 0.25-0.5 USD and 1-3 min per case.

Outputs in --out: results.jsonl (one line per case, written as it goes), results.csv, results.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SRC = Path(os.environ.get("GROUP_WATCH_DIR", "/workshop/group-watch"))
SYSTEMS = ("group_watch", "imdb_top", "content_based")
LABEL = {"group_watch": "Group Watch", "imdb_top": "IMDb", "content_based": "TF-IDF"}


def snapshot_code() -> Path:
    """Copy code + catalog (not data/, out/, caches) so the run is immune to concurrent edits."""
    dst = Path(tempfile.mkdtemp(prefix="gw-snapshot-"))
    for p in SRC.iterdir():
        if p.is_file() and (p.suffix == ".py" or p.name.endswith((".json", ".json.gz"))):
            shutil.copy2(p, dst / p.name)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(dst))
    return dst


def make_cases(profile_ids: list[str], n: int, seed: int, min_size: int, max_size: int) -> list[list[str]]:
    rng = random.Random(seed)
    cases, seen = [], set()
    while len(cases) < n:
        group = tuple(sorted(rng.sample(profile_ids, rng.randint(min_size, max_size))))
        if group not in seen:
            seen.add(group)
            cases.append(list(group))
    return cases


def run_case(idx: int, ids: list[str], lang: str) -> dict:
    import agent
    import arena
    t0 = time.time()
    row = {"case": idx, "profiles": ids}
    try:
        viewers = agent.load_profiles(ids)
        result = agent.run(viewers, {}, lang)
        row["chosen"] = result["winner"]["title"]
        cmp_ = arena.run(result, viewers, {}, lang)
        row["winner"] = cmp_["winner"]
        row["tied_with"] = cmp_["tied_with"]
        row["systems"] = {s["key"]: {"mean": s["top1_mean"], "min": s["top1_min"], "fit": s["fit"],
                                    "top3_mean": s["top3_mean"], "top_pick": s["top"][0]["title"]}
                          for s in cmp_["systems"]}
    except Exception as e:  # keep the batch going; the case is listed as failed and excluded from averages
        traceback.print_exc()
        row["error"] = f"{type(e).__name__}: {e}"
    row["seconds"] = round(time.time() - t0)
    return row


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r and all(k in r["systems"] for k in SYSTEMS)]
    out = {"cases_ok": len(ok), "cases_failed": len(rows) - len(ok), "systems": {}}
    for k in SYSTEMS:
        out["systems"][k] = {m: mean([r["systems"][k][m] for r in ok]) for m in ("mean", "min", "fit")}
    gw_best = [r for r in ok if r["winner"] == "group_watch"]
    out["gw_wins_alone"] = len([r for r in gw_best if not r["tied_with"]])
    out["gw_wins_or_ties"] = len(gw_best)
    n = max(len(ok), 1)
    out["gw_win_rate_alone"] = out["gw_wins_alone"] / n
    out["gw_win_rate_or_tie"] = out["gw_wins_or_ties"] / n
    return out


def write_reports(rows: list[dict], names: dict[str, str], out: Path) -> str:
    rows = sorted(rows, key=lambda r: r["case"])
    s = summarize(rows)
    with (out / "results.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case", "viewers", "chosen_by_group_watch", "winner", "tie",
                    *[f"{LABEL[k]}_{m}" for k in SYSTEMS for m in ("mean", "min", "fit")], "seconds", "error"])
        for r in rows:
            sy = r.get("systems", {})
            w.writerow([r["case"], "; ".join(names.get(p, p) for p in r["profiles"]), r.get("chosen", ""),
                        LABEL.get(r.get("winner", ""), ""), "yes" if r.get("tied_with") else "",
                        *[sy.get(k, {}).get(m, "") for k in SYSTEMS for m in ("mean", "min", "fit")],
                        r["seconds"], r.get("error", "")])

    md = ["# Group Watch: evaluación por lotes", "",
          f"Casos válidos: **{s['cases_ok']}** (fallidos: {s['cases_failed']}). Puntuaciones 0-100 de jueces LLM "
          "(Nova Pro) sobre perfiles sintéticos: una estimación, no opinión de espectadores reales.", "",
          "## Resumen", "",
          "| Sistema | Media de medias (top-1) | Media del peor espectador | Media de ajuste (fit) |",
          "|---|---|---|---|"]
    for k in SYSTEMS:
        v = s["systems"][k]
        md.append(f"| {LABEL[k]} | {v['mean']:.1f} | {v['min']:.1f} | {v['fit']:.1f} |")
    md += ["", f"**Group Watch es el mejor en {s['gw_win_rate_alone']:.0%} de los casos** "
           f"({s['gw_wins_alone']}/{s['cases_ok']}); contando empates técnicos (diferencia de fit ≤ 3): "
           f"**{s['gw_win_rate_or_tie']:.0%}** ({s['gw_wins_or_ties']}/{s['cases_ok']}).", "",
           "## Casos", "",
           "| # | Espectadores | Elegida | GW media | IMDb media | TF-IDF media | Mejor |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            md.append(f"| {r['case']} | {len(r['profiles'])} | ERROR: {r['error'][:60]} | | | | |")
            continue
        sy = r["systems"]
        best = LABEL[r["winner"]] + (" (empate)" if r["tied_with"] else "")
        md.append(f"| {r['case']} | {', '.join(names.get(p, p) for p in r['profiles'])} | {r['chosen']} | "
                  + " | ".join(f"{sy[k]['mean']:.0f}" for k in SYSTEMS) + f" | {best} |")
    text = "\n".join(md) + "\n"
    (out / "results.md").write_text(text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=100, help="number of cases")
    ap.add_argument("--workers", type=int, default=3, help="cases in parallel (watch Bedrock throttling)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-size", type=int, default=2)
    ap.add_argument("--max-size", type=int, default=4)
    ap.add_argument("--lang", default="es")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    args = ap.parse_args()

    snap = snapshot_code()
    print(f"Código copiado a {snap}")
    import agent
    profiles = agent.all_profiles()
    names = {pid: p.get("display_name", pid) for pid, p in profiles.items()}
    cases = make_cases(sorted(profiles), args.n, args.seed, args.min_size, args.max_size)

    args.out.mkdir(parents=True, exist_ok=True)
    jsonl = args.out / "results.jsonl"
    rows = [json.loads(l) for l in jsonl.read_text().splitlines() if l.strip()] if jsonl.exists() else []
    done = {r["case"] for r in rows if "error" not in r}
    rows = [r for r in rows if r["case"] in done]  # failed cases are retried on resume
    jsonl.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    todo = [(i, ids) for i, ids in enumerate(cases, 1) if i not in done]
    print(f"{len(cases)} casos, {len(done)} ya hechos, {len(todo)} por ejecutar")

    lock = threading.Lock()

    def job(item):
        row = run_case(item[0], item[1], args.lang)
        with lock:
            rows.append(row)
            with jsonl.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            tag = row.get("error") or f"{LABEL[row['winner']]}{' (empate)' if row['tied_with'] else ''}"
            print(f"[{len(rows)}/{len(cases)}] caso {row['case']}: {tag} ({row['seconds']}s)", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(job, todo))

    print(write_reports(rows, names, args.out))
    print(f"Guardado en {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
