# Clip Scoring Service

Upload a short-form video (TikTok / Instagram, up to 3 minutes) and get back a verdict, **post**, **improve** or **skip**, with a 0–10 score, six scores by area, evidence from the clip and a concrete fix for each.

This file covers how to run it. Four other documents:

- **[docs/dataset-run.md](docs/dataset-run.md)**: all 30 sample clips scored on the deployment, compared with reach within each account, and what that says about the rubric.
- **[docs/evals.md](docs/evals.md)**: how a change to the prompt, rubric, pipeline or model is evaluated before it ships.
- **[REFLECTION.md](REFLECTION.md)**: a one-page explanation of the system. It covers the main decisions, what worked and what didn't, the limitations, what's next, and the AI tools used.
- **[docs/README.md](docs/README.md)**: the full design, the rubric, the research and the measurements.

## How it works

One process: a FastAPI app, a worker thread and Postgres.

```
POST /analyses ─► validate ─► store file ─► queue ─┐        GET /analyses/{id}
                                                   │              ▲
                  ┌────────────────────────────────┘              │
                  ▼                                               │
  extract (ffmpeg, Whisper, scene cuts) ─► signals ─► judge (1 LLM call)
                                                           │
                                   score (code) ◄─ verify (code) ─► Postgres
```

1. **Measure.** Code extracts the audio, transcript, frames, cuts, pauses and loudness, and computes deterministic signals from them.
2. **Judge.** One model call, through Vercel AI Gateway, judges what needs judgment, grounded on those signals.
3. **Verify and score.** Code checks the model's claims against the measurements, then computes the scores and the verdict. The model never picks the verdict.

Every state change is committed to Postgres, so a restart never loses an analysis: work that was in progress is marked `failed / interrupted_by_restart`. Uploading the same clip twice returns the existing analysis instead of paying for a second model call.

## What you need

| Requirement | Why | Install |
| --- | --- | --- |
| Docker with docker compose | Runs Postgres and the API (ffmpeg included) | [docker.com](https://docs.docker.com/get-docker/) |
| A Vercel AI Gateway API key | The model call | Vercel dashboard → AI Gateway → API Keys |
| `make` (optional) | Shortcuts below | preinstalled on macOS and Linux |

To run outside Docker you also need Python 3.12, [uv](https://docs.astral.sh/uv/) and ffmpeg on your `PATH`.

## Environment variables

```bash
cp example.env .env
```

Then set **one** value:

| Variable | Required | Value |
| --- | --- | --- |
| `AI_GATEWAY_API_KEY` | **yes** | Your Vercel AI Gateway key. Never commit it. |

`example.env` already has the rest set for the gateway. These are the ones worth knowing:

| Variable | Default in `example.env` | What it does |
| --- | --- | --- |
| `LLM_BASE_URL` | `https://ai-gateway.vercel.sh/v1` | Model endpoint for runs outside Docker |
| `DOCKER_LLM_BASE_URL` | `https://ai-gateway.vercel.sh/v1` | Model endpoint under docker compose (see the note below) |
| `LLM_MODEL` | `google/gemini-2.5-flash` | The pinned model. Must accept images. |
| `LLM_VISION` / `LLM_MAX_FRAMES` | `true` / `16` | Send frames to the model, and how many |
| `LLM_PRICE_INPUT_PER_MTOK` / `LLM_PRICE_OUTPUT_PER_MTOK` | `0.30` / `2.50` | `LLM_MODEL`'s price per million tokens, used when the gateway doesn't report the billed cost. Required for any non-local endpoint; update them with `LLM_MODEL`. |
| `MAX_COST_PER_CLIP_USD` | `1.0` | Budget per clip. Going over it is logged, not blocked. |
| `LLM_MODEL_CHOICES` | unset | Extra models a caller may pick per upload, for comparing models. Leave unset otherwise. |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | unset | Basic auth on every route but `/health`. Set both or neither. |
| `DATABASE_URL` | local compose Postgres | Compose replaces it with the `db` service |
| `AUTO_RUN_MIGRATIONS` | `true` | Apply database migrations on startup |
| `MAX_UPLOAD_MB` / `MAX_DURATION_S` | `200` / `240` | Upload limits |

> **Docker note.** docker compose does **not** use `LLM_BASE_URL`: inside a container `localhost` is the container itself, so compose reads `DOCKER_LLM_BASE_URL` instead, and its fallback is a local Ollama. If you edit `.env` by hand, keep **both** URLs pointing at the gateway, or the container will try to reach Ollama and every analysis fails with a model error.

Settings are read from `.env` by `app/settings.py`. A misspelled variable is ignored silently, not rejected, so copy names from `example.env`.

**The service refuses to start** when a non-local model endpoint (anything but `localhost`, `127.0.0.1` or `host.docker.internal`) is missing its API key or the two prices, and the error lists every missing variable at once. Without them the first analysis would fail with a 401, or a clip whose cost the gateway did not report would be stored as $0. A local Ollama needs neither. Setting both prices to `0` declares an endpoint free.

## Run it

### With Docker (recommended)

```bash
cp example.env .env        # then set AI_GATEWAY_API_KEY
make up                    # or: docker compose up --build -d
curl http://localhost:9500/health
```

The first analysis is slower: Whisper downloads its model (~150 MB) into `var/`, which is kept between runs.

### Without Docker

```bash
cp example.env .env        # then set AI_GATEWAY_API_KEY
make install               # uv sync
make run                   # starts Postgres in Docker, then the API on :9500
```

## Try it

| URL | What it is |
| --- | --- |
| http://localhost:9500/demo | A small page to upload clips, follow their status and open the reports |
| http://localhost:9500/docs | OpenAPI (Swagger) for every endpoint |
| http://localhost:9500/analyses/{id}/report | The HTML report of one analysis |

From the command line:

```bash
# Submit a clip: 202 with an id (or 200 and "deduplicated": true if it was already analysed)
curl -X POST http://localhost:9500/analyses \
  -F "file=@path/to/clip.mp4" \
  -F 'metadata={"title": "Why permit approvals take so long", "platform": "tiktok"}'

# Poll until status is "completed" or "failed"
curl http://localhost:9500/analyses/<id>

# Recent analyses
curl http://localhost:9500/analyses
```

- `metadata` is optional. `platform` is `tiktok` or `instagram`.
- `?fresh=true` forces a new analysis of a clip that was already analysed.
- The response shape is documented in [docs/report-example.json](docs/report-example.json).
- If Basic auth is on, add `-u user:password`.

## A real analysis

[docs/examples/C30.json](docs/examples/C30.json) is an unedited `GET /analyses/{id}` response, produced on 2 October 2026 by the deployed service (same code as `main`, `google/gemini-2.5-flash` through Vercel AI Gateway).

**The clip.** `C30.mp4` from the sample dataset: a 46.6 s Robinson's Podcast clip titled "Wolff on Europe and migration", 720×1280, SHA-256 `99f192841f429b5191665265b9b03b0cd7a9ce227335d23251e80759fcc1adda` (matches `dataset/SHA256SUMS.txt`).

**The request.**

```bash
curl -X POST "http://localhost:9500/analyses?fresh=true" \
  -F "file=@dataset/videos/C30.mp4" \
  -F 'metadata={"title": "Wolff on Europe and migration", "account": "robinsonspodcast", "platform": "tiktok", "external_id": "C30"}'
# 202 {"id": "an_01a0fa98637bb38091f3abaf", "status": "queued", ...}

curl http://localhost:9500/analyses/an_01a0fa98637bb38091f3abaf
```

**What came back** (abridged; the file has the evidence, fixes and signals in full):

| | |
| --- | --- |
| Verdict | **improve**, 6.0 / 10 (raw 6.1, capped at 6.0) |
| Why capped | "Clip starts mid-sentence (speech already active at 0:00, opens with “if”)." Applied by code from two agreeing measurements (speech at the first frame, a lowercase "if" opener), not taken from the model |
| Summary | Continued bombing in Iran will lead to an unmanageable migration crisis for Europe |
| Top fix | Re-edit to start with a clear topic statement, defining "they" and "this war" upfront (worth up to 2.0 points) |
| Shareable line | 0:20.5, "They can't handle more migration, but where the hell is that migration gonna go?" (time taken from the transcript, not the model) |
| Cost / time | $0.0072 billed by the gateway, 40 s end to end |

| Area | Score | Basis | Key evidence |
| --- | --- | --- | --- |
| Hook | 2 | mixed | Opens with "if they continue with this war." at 0:00, no context for "they" |
| Standalone completeness | 4 | mixed | Starts mid-sentence (measured); ends cleanly at 0:45 |
| Message clarity and payoff | 10 | judged | Point stated at 0:08, payoff at 0:43 |
| Pacing and energy | 8 | measured | 163 wpm, speech 91% of the clip, but no cut for the last 13.5 s |
| Audio and speech quality | 8 | measured | -20.7 LUFS against a ~-14 target, no clipping |
| On-screen text | 8 | judged | Captions throughout, no headline in the first frame |

The upload checks report alongside the score: `format_check` is `acceptable` (9:16, but below the recommended 1080×1920) and `duration_check` is `within_target`.

**Reproduce it.** The dataset is not committed; with your copy at `dataset/`, run the request above against a local stack (`make up`) or the deployed service (below). What to expect:

- **Measured values are deterministic.** Signals, pacing, audio, the cap and the upload checks come from code and repeat exactly.
- **Judged values come from the model** (temperature 0, fixed seed, pinned model and prompt version), so they *should* repeat but are not guaranteed to. A second fresh run of this clip with the same metadata and configuration returned a byte-identical result apart from IDs and timing, and was billed again (so not a gateway cache hit). Over 5 fresh runs each of C30 and C10 the verdict and overall score never changed, but one C30 run read the clip differently and moved two judged areas by one step; see [the repeatability results](docs/README.md#repeatability-results). `make repeatability` reruns the experiment (set `CLIP_API_URL` and `CLIP_API_AUTH=user:password` to target the deployment).
- **Without `?fresh=true`** an identical upload (same file, `title`, `platform` and configuration) returns the stored analysis: `200`, `"deduplicated": true`, `"source": "cached"`, no new model cost. Analyses created with `fresh=true` are never reused, so the first plain upload after a fresh one runs once more.
- A different `title` or `platform` is a different analysis, because both reach the judge.

**Deployed service.** https://clip-scoring.fly.dev runs the same build behind Basic auth (credentials are shared privately, never in this repo). Upload from https://clip-scoring.fly.dev/demo, or add `-u user:password` to the commands above with that base URL. The HTML report of this analysis is at `/analyses/an_01a0fa98637bb38091f3abaf/report`.

## Makefile

`make help` lists every target. The ones you will use:

| Target | What it does |
| --- | --- |
| `make up` / `make down` | Build and start the full stack in the background / stop it (data is kept) |
| `make logs` / `make ps` | Follow the API logs / show the stack status |
| `make watch` | Full stack that syncs and restarts on code changes |
| `make install` | Install Python dependencies (`uv sync`) |
| `make run` / `make dev` | API outside Docker, without / with auto-reload (starts Postgres first) |
| `make db` / `make db-stop` / `make db-shell` | Start, stop or `psql` into Postgres alone |
| `make test` | Test suite. Needs only Postgres: ffmpeg and the model are stubbed. Narrow it with `T=tests/test_store.py::test_name` |
| `make lint` | Ruff |
| `make migrate` / `make migration m="..."` | Apply pending migrations / create a new one |
| `make restart-check CLIP=clip.mp4` | Durability check: `kill -9` the server mid-analysis, restart, and confirm the interrupted analysis reports why and a completed one is unchanged. With Basic auth on, add `CLIP_API_AUTH=user:password` |
| `make repeatability CLIP=clip.mp4 RUNS=5` | Analyse the same clip several times and report how stable the scores are |
| `make spend` | Vercel AI Gateway balance and total spend |
| `make coverage` / `make sonar-up` / `make sonar-scan` | Coverage, and a local SonarQube to reproduce the quality numbers |
| `make clean` | Remove caches |

Deployment to Fly.io (`make fly-setup`, `make fly-secrets`, `make deploy`) is optional and explained in [docs/adr/0001-deploy-on-fly.md](docs/adr/0001-deploy-on-fly.md).

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Analysis fails with `model_error` under Docker | `DOCKER_LLM_BASE_URL` is missing, so the container calls Ollama. See the Docker note. |
| Analysis fails with `model_error` and the logs show a 401 | `AI_GATEWAY_API_KEY` is missing or wrong |
| The service refuses to start with pending migrations | `AUTO_RUN_MIGRATIONS=false`: run `make migrate` |
| The service refuses to start: "… is not a local endpoint, so these must be set: …" | Add the variables it lists to `.env` (copy them from `example.env`), or to `fly.toml` / `make fly-secrets` on Fly |
| Tests fail to connect | Postgres is not up: `make db` |
| Port 5432 or 9500 already in use | Stop the other service, or change the port mapping in `docker-compose.yml` |

## Repository map

```
app/
  api/          routes, validation, response shape, Basic auth
  pipeline/     extract → signals → judge → verify → score
  llm/          OpenAI-compatible client (gateway or Ollama)
  storage/      Postgres access and Alembic migrations
  report/       HTML report and the /demo page
  config/       rubric.yaml and the judge prompt
scripts/        restart check, repeatability, dataset runner
tests/          pytest suite
docs/           design doc, ADRs, response example
```
