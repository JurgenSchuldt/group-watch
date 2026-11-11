# Our team

**Team name:** (completar)
**Members:** (completar)
**Use case:** Content discovery agent for groups ("Group Watch"): recommends the title a whole group will enjoy, plus a personalised trailer.

## Before you build

Answer these before you write any code. They become the first slide of your pitch.

1. **The problem.** Choosing what to watch together takes long and someone always loses: each person wants something different, so groups scroll for 20+ minutes or settle for a title that only one person likes.
2. **Who has it.** Couples, families and friends watching on a shared screen (the "family co-viewers" segment, plus mixed groups), and the streaming platforms that lose watch time to indecision. Today they negotiate out loud, vote, or fall back on the algorithm's per-account recommendations, which only reflect one profile.
3. **Rules or AI?** Fixed rules (match genres, filter by rating) can find overlaps, but fail when tastes do not overlap. The agent has to reason about trade-offs: language and subtitle needs, who can follow what, what each person asked for tonight in free text, and what each person concedes. Rules still do the final decision (maximise the least happy viewer) so the result is explainable and fair; the model does the soft judgment and the explanation.
4. **Data.** Viewer profiles: Audience 360 (60 synthetic profiles, 6 segments) in the team S3 bucket (`audience-360/`), read at run time. Plus guests typed in the UI, and what each person wants tonight. Catalog: 7,580 real feature films in `group-watch/catalog_real.json.gz` (2 MB), built by `group-watch/data/build_catalog.py` from BrightData/IMDb-Media (IMDb genres, rating, votes, director, plot summary and storyline, including films after 2017; Bright Data license, prototype use only) joined on IMDb id with The Movies Dataset (Rounak Banik, CC0) for TMDB keywords and franchise. Series and video games that the source mislabels as films are filtered out. Raw files stay in `~/.cache/group-watch/`, not in the repo. Not used: the IMDb Spoiler Dataset (its movie synopses are only on Kaggle and need credentials; the Hugging Face copy has reviews only). Viewer profiles are synthetic.
5. **Success metric.** A group of 2 to 6 gets one agreed pick in about 1 minute (measured: ~60 s for 3 viewers and 32 titles), with every viewer's estimated satisfaction at or above 50/100 for the chosen title in our test groups, versus the ~20 minutes of browsing we assume today (to be validated). The score is the model's estimate from the profile, not a measured outcome: next step is to compare it with real viewers' ratings.

## Architecture

- **Front end:** `group-watch/index.html` (single page, no build), served by `group-watch/server.py` (Python standard library).
- **Agent:** `group-watch/agent.py`, `recsys.py`, `personas.py`. Five steps: (1) one Bedrock call per viewer turns profile + wish into structured intent and explicit limits ("no superheroes" becomes hard filters for the whole group); (2) a curator agent proposes real films the whole group could enjoy, kept only if they exist in the catalog (no invented films), and TF-IDF/genre/director/era retrieval adds each viewer's best matches, giving ~50 candidates that break nobody's limits out of 7,580; (3) one independent persona agent per viewer (Claude Haiku 4.5, `us-east-1`, parallel, forced tool use; chosen over Sonnet 4.5 for speed: a 3-person search takes ~23 s instead of ~51 s and costs ~0.11 USD instead of ~0.27 USD, measured; quality difference not yet measured beyond one group) scores every candidate seeing only its own profile and flags dealbreakers; (4) plain code picks the film that maximises the least happy viewer, so no single model decides for the group; (5) a last call explains the clash and the middle ground for the film that was actually picked.
- **Baselines:** `group-watch/recsys.py`. Two public-style recommenders get the same viewer profiles: an IMDb-style chart (weighted rating within the viewer's genres) and a TF-IDF content-based recommender, both limited to widely seen films as public charts are. Per-viewer lists are merged with reciprocal rank fusion.
- **Arena:** `group-watch/arena.py`. One judge agent per viewer (own profile and wish only, parallel) rates the top 3 of Group Watch, the IMDb chart and the content-based recommender, blind and shuffled, from the synopsis alone. Judges run on Amazon Nova Pro, a different model family from the Claude agents that picked the film, so the comparison is not circular. Code scores each system (fit = half the least happy viewer, half the average) and Claude writes the verdict.
- **Data:** Audience 360 in S3 (`us-east-2`, read-only), real catalog in the repo (2 MB gzip).
- **Trailer:** the official trailer is embedded from YouTube when TMDB or Wikidata has one (`group-watch/official_trailer.py`); otherwise the button opens a YouTube search. Posters are hotlinked from Wikipedia (`group-watch/posters.py`). The ComfyUI trailer generator (`group-watch/trailer.py`) is no longer shown in the UI.
- **Secrets:** ComfyUI token is read from Secrets Manager at run time, never stored.

## Demo plan

1. Open the UI and add 3 contrasting viewers (for example a sports superfan, a premium cinephile and a family co-viewer), plus a made-up guest.
2. Type what each wants tonight ("hoy quiero algo de deporte", "algo tranquilo").
3. Press "Buscar película para todos" and show the process panel while the agent works.
4. Show the winner, where tastes clash, and why it is the middle ground; open the score table and click rows to see each person's reason.
5. Show the comparison: three systems, each viewer's own agent scoring their pick, and the verdict. Results vary by group: in our tests Group Watch won some groups and a baseline won others, and scorer and judge models disagree by 10 to 20 points, so present it as an evaluation method, not as proof that we win.
6. Change one viewer or one wish and rerun to show the pick changes (the agent reasons, it does not return a fixed result).
7. Generate the trailer for the winner (start it early, it takes minutes) and play it.

Run it: `cd group-watch && python3 server.py`, then open http://127.0.0.1:8765.
