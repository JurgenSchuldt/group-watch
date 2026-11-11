# Group Watch

**3rd place, AWS M&E Agentic AI Partner Hackathon (Media & Entertainment), Madrid, November 2026.**
Built in one day by a team of five from Capgemini.

Choosing a film as a group takes a long time, and usually someone ends up watching something they did not want. Group Watch recommends one film for a group of 2 to 6 people. Each person says what they feel like watching tonight, and the app picks the film that works best for the least happy person in the group, then explains why.

## How it works

1. **Understand each viewer.** One Amazon Bedrock call per viewer turns their profile and their wish for tonight into structured intent and explicit limits. "No superheroes" becomes a hard filter for the whole group.
2. **Find candidates.** A curator agent (Claude Sonnet 4.5) proposes real films the whole group could enjoy, kept only if they exist in the catalog. TF-IDF, genre, director and era retrieval adds each viewer's best matches. The result is up to 50 candidates from 7,580 real films, none of which breaks anyone's limits.
3. **One agent per viewer.** An independent persona agent for each viewer (Claude Haiku 4.5, run in parallel with forced tool use) scores every candidate seeing only its own viewer's profile, and flags dealbreakers.
4. **Decide with code, not a model.** Plain Python picks the film that maximises the score of the least happy viewer, so no single model decides for the group and the result can be explained.
5. **Explain.** A final call describes where tastes clash and why the chosen film is a fair middle ground.

## Evaluation

The app compares itself, blind, against two public-style recommenders given the same profiles: an IMDb-style chart and a TF-IDF content-based recommender. One judge agent per viewer rates each system's top picks from the synopsis alone, with options shuffled and anonymised. The judges run on **Amazon Nova Pro**, a different model family from the Claude agents that picked the film, so the evaluation is not circular.

Batch run over 100 groups (scores 0-100):

| System | Mean of means (top-1) | Mean of the least happy viewer | Mean fit |
|---|---|---|---|
| **Group Watch** | **63.1** | **51.3** | **57.2** |
| IMDb-style chart | 54.5 | 40.4 | 47.5 |
| TF-IDF | 52.6 | 37.5 | 45.1 |

Group Watch got the top score in 64 of 100 groups (53 by more than 3 points). Viewer profiles are synthetic and the scores are LLM estimates, not real viewer ratings. Full results: [`eval/out/results.md`](eval/out/results.md).

## AWS services

- **Amazon Bedrock** (Converse API, `us-east-1`): Claude Haiku 4.5, Claude Sonnet 4.5 and Amazon Nova Pro.
- **Amazon S3**: Audience 360 viewer profiles and segments (synthetic dataset), read at run time.
- **AWS Secrets Manager**: ComfyUI token, read at run time and never stored.
- Each request meters its own Bedrock token usage and cost.

## Repository structure

| Path | Contents |
|---|---|
| `group-watch/` | The app: web UI (`index.html`), server (`server.py`), agent pipeline (`agent.py`, `personas.py`, `recsys.py`), blind comparison (`arena.py`), catalog |
| `group-watch/data/build_catalog.py` | Builds the film catalog |
| `eval/` | Batch evaluation script and results |
| `presentacion/` | Slides presented at the hackathon (Spanish) |
| `comfyui/` | ComfyUI client and workflows (trailer generator, not shown in the final UI) |
| `TEAM.md` | Problem, users, data and success metric |

## Running it

Requires Python 3, `boto3`, AWS credentials with Amazon Bedrock access in `us-east-1`, and the Audience 360 profiles in an S3 bucket (`HACKATHON_ASSETS_BUCKET`).

```bash
cd group-watch
pip install -r requirements.txt
python3 server.py   # http://127.0.0.1:8765
```

## Data

- Catalog: 7,580 feature films built from BrightData/IMDb-Media (Bright Data license, prototype use only), joined with The Movies Dataset by Rounak Banik (CC0) for keywords and franchises.
- Viewer profiles: Audience 360, a synthetic dataset provided at the hackathon (not included).
