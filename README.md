# Clip Scoring Service

Upload a short-form video (TikTok / Instagram, up to 3 minutes) and get back a verdict, **post**, **improve** or **skip**, with a 0–10 score, six scores by area, evidence from the clip and a concrete fix for each.

This file covers how to run it. The design, the rubric, the decisions and their trade-offs are in **[docs/README.md](docs/README.md)**.

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
| `make restart-check CLIP=clip.mp4` | Durability check: `kill -9` the server mid-analysis and confirm nothing is lost |
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
