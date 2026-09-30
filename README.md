# Clip Scoring Service

A microservice that analyses short-form videos (TikTok / Instagram style, typically 1–2 minutes) and tells you whether a clip is ready to **post**, needs to be **improved**, or should be **skipped**, with a score per area and concrete fixes.

Built for the Home Game product engineering take-home.

> **Status:** design brief. Sections marked `TBD` get filled in as the build progresses (real outputs, measured cost, repeatability numbers, reflection).
>
> **Scope:** trimmed to fit the 12-hour cap with a real buffer. Everything deferred is listed, with the reason, in [section 23](#23-production-path).

---

## Contents

**Part 1: Business scope**

1. [The problem](#1-the-problem)
2. [Who uses it and when](#2-who-uses-it-and-when)
3. [In scope / out of scope](#3-in-scope--out-of-scope)
4. [Assumptions](#4-assumptions)
5. [The scoring rubric](#5-the-scoring-rubric)
6. [How we'll know it works](#6-how-well-know-it-works)
7. [Known limitations](#7-known-limitations)
8. [Product research and rationale](#8-product-research-and-rationale)

**Part 2: Technical solution**

9. [Architecture](#9-architecture)
10. [Analysis pipeline](#10-analysis-pipeline)
11. [Tools, feasibility and judges](#11-tools-feasibility-and-judges)
12. [API](#12-api)
13. [Storage and durability](#13-storage-and-durability)
14. [Repeatability and caching](#14-repeatability-and-caching)
15. [Interrupted runs, duplicates and concurrency](#15-interrupted-runs-duplicates-and-concurrency)
16. [Model strategy](#16-model-strategy)
17. [Stack](#17-stack)
18. [Setup and running](#18-setup-and-running)
19. [Example requests and output](#19-example-requests-and-output)
20. [HTML report](#20-html-report)
21. [Verification](#21-verification)
22. [Cost](#22-cost)
23. [Production path](#23-production-path)
24. [Reflection and time log](#24-reflection-and-time-log)

---

# Part 1: Business scope

## 1. The problem

A team producing short-form clips ends up with more clips than it can post. Someone has to decide which ones go out, and today that decision is manual and inconsistent. This service gives every clip the same structured review: what works, what doesn't, and what to fix, so the team can prioritise publishing and spend editing time where it pays off.

The service judges **craft** (how well the clip is built to hold a viewer), not whether the clip will go viral. Reach depends on many things outside the video itself (account size, timing, algorithm, topic), so the score is a quality signal, not a view forecast.

## 2. Who uses it and when

The team described two moments where scoring helps:

| Moment                       | Question                                                   | Covered                                          |
| ---------------------------- | ---------------------------------------------------------- | ------------------------------------------------ |
| **(a) Before edit / render** | Which raw cuts are worth editing?                          | Not in this version; designed so it can be added |
| **(b) After edit / render**  | Is this clip ready to post, or should we improve it first? | **Yes, primary focus**                           |

The sample dataset consists of clips already posted to TikTok, which matches moment (b).

Clips are **uploaded and analysed one at a time**. There is no batch upload and no `group_id`: each result stands on its own and is never ranked against other clips.

The output for each clip is:

- **Verdict:** `post`, `improve`, or `skip`
- **Overall score** (0–10)
- **Score per dimension**, each with evidence (timestamps, measured values) and a suggested fix
- **Confidence** per dimension, so the reader knows which scores are solid and which are best guesses
- **Viral potential**: what helps this clip travel, what holds it back, and its most shareable line. These are signals linked to watching and sharing, read from the clip itself, not a view forecast

## 3. In scope / out of scope

**In scope**

- Upload an MP4 with basic metadata and get an analysis ID
- Retrieve the result, status, or failure reason by ID
- Six scoring dimensions, an overall score and a verdict
- Results that survive a hard crash (`kill -9`) and restart
- Clear, documented errors
- Repeatability check and a run over the provided 30-clip dataset

**Out of scope (documented, not built)**

- Choosing clips from a long-form video (upstream clipping)
- Pre-production strategy or trend advice (what to film next)
- Ranking clips within a batch from the same source (`group_id`): the team confirmed test clips are unrelated
- Job retries and recovery after a crash: interrupted jobs are marked as failed with a clear reason (behaviour and next steps in [section 15](#15-interrupted-runs-duplicates-and-concurrency))
- Platform-specific scoring (one general score for TikTok and Instagram)
- Languages other than English

## 4. Assumptions

Confirmed with the team:

1. **English only.** Spanish is a natural next step.
2. **Clips arrive already edited**, but some may look unedited (no captions, no headline). Missing captions is scored lightly and turns into a suggestion rather than a heavy penalty.
3. **Clips are independent.** No batch ranking or grouping.

Own assumptions:

4. **Craft only, stance-neutral.** The sample clips are political and policy content. The service scores how a clip is built, never the opinion it expresses. Prompts explicitly instruct the model to ignore political position.
5. **Clips cut out of context** are handled by the _standalone completeness_ dimension (does it make sense without the full episode?).
6. **General audience.** No tuning per niche or client in this version.

## 5. The scoring rubric

### Why a rubric

A rubric is an agreed checklist of what makes a clip good. It makes every review **consistent** (same criteria every time), **explainable** (every score has a reason) and **actionable** (a low score points to a specific fix).

### The six dimensions

The dimensions follow the viewer's journey through a clip: stop scrolling, understand it, stay, and be able to hear and read it.

| #   | Dimension                      | Question it answers                                                        | Weight |
| --- | ------------------------------ | -------------------------------------------------------------------------- | ------ |
| 1   | **Hook**                       | Do the first ~3 seconds give a reason to keep watching?                    | 25%    |
| 2   | **Standalone completeness**    | Does it start and end cleanly, and make sense without the full episode?    | 20%    |
| 3   | **Message clarity and payoff** | Is there one clear point, and does it land by the end?                     | 20%    |
| 4   | **Pacing and energy**          | Is it free of dead air and flat stretches? Does it keep moving?            | 15%    |
| 5   | **Audio and speech quality**   | Can you hear the speaker clearly and comfortably?                          | 10%    |
| 6   | **On-screen text**             | Are there captions or a headline that help viewers watching without sound? | 10%    |

Weights are a starting point and will be tuned on a small development subset of the dataset (see [section 6](#6-how-well-know-it-works)).

### Scale and combination

- Each dimension is scored **0–10** (0 = fails completely, 5 = acceptable, 10 = excellent).
- **Overall score** = weighted average of the dimensions that apply to the clip.
- **Cap rule:** if a clip starts or ends mid-sentence, the overall score is capped at **6.0**, so it can never be recommended for posting as is, however good the rest is.
- **Non-applicable dimensions** (for example, speech quality in a music-only clip) are left out and the remaining weights are rescaled to add up to 100%.

### Verdict

| Overall score | Verdict   | Meaning                                    |
| ------------- | --------- | ------------------------------------------ |
| 7.0 – 10      | `post`    | Ready to publish                           |
| 5.0 – 6.9     | `improve` | Worth publishing after the suggested fixes |
| 0 – 4.9       | `skip`    | Not worth the editing time                 |

Thresholds are provisional and will be tuned on the dataset.

### Alternatives considered

| Alternative                                     | Why not (for now)                                                                         |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Single overall score from the model             | A black box: no reasons, less consistent, and doesn't meet the 3–10 dimension requirement |
| Three broad buckets (hook, content, production) | Too broad to tell an editor what to fix                                                   |
| Ten or more detailed checks                     | Overlapping items, noisier scores, harder to read                                         |
| Grouping by signal type (visual, audio, text)   | Natural for engineers, but doesn't match how viewers behave                               |
| Call-to-action strength                         | Rarely present in interview and podcast clips, so it would score format, not quality      |
| Emotion / virality scoring                      | Hard to measure reliably and, with political content, risks rewarding outrage             |
| Pass/fail checklist                             | Clear but can't rank clips; the idea is kept as the cap rule                              |
| Head-to-head comparison                         | Better for ranking related clips, but clips here are unrelated                            |
| Weights learned from performance data           | The right long-term approach, but 30 clips is far too few; planned as future work         |

## 6. How we'll know it works

There is no perfect ground truth: the dataset has views, likes and shares, but no watch time or retention, and reach depends on much more than the video. So validation is about **sanity and consistency**, not proving predictive accuracy.

1. **Tune on a small development subset** (a few clips per account), then run all 30 clips once with the rubric frozen.
2. **Compare within each account, not across accounts.** Views are normalised by each account's median (from `account-context.csv`), because a large account's weak clip can outperform a small account's great one.
3. **Directional check:** do higher-scored clips tend to perform better than their account's median? With 10 clips per account this is a signal, not proof.
4. **Manual spot check:** read the evidence and fixes for a handful of clips and judge whether a human editor would agree.
5. **Repeatability:** analyse 2 clips 5 times each from scratch and measure how much scores move, and whether the verdict ever flips.
6. **Fairness across styles:** check that one production style (interview, studio opinion, podcast) isn't systematically favoured.
7. **Comparison with an external analyser** _(deferred, see [section 23](#23-production-path))_: run the clips under 2 minutes through ClipAPI (its free plan allows 50 analyses a month, capped at 120 s per clip) and compare its `hook_strength` and `audio_clarity` with this service's hook and audio scores. Agreement with another unvalidated model is a sanity check, not proof; large disagreements are read clip by clip to see which tool is right.

Performance numbers are **never** given to the analysis. They are only used afterwards, for evaluation.

Results: `TBD`

## 7. Known limitations

- **Small sample:** 30 clips, 10 per account. Correlations are directional only.
- **No retention data:** the link between a strong hook and viewers staying is an industry assumption, not something this dataset can confirm.
- **Speech-only in this version:** almost every sample clip is someone talking, so the service assumes speech. Clips with little speech (music, slideshows) are flagged with low confidence rather than scored on a separate path.
- **One general audience:** no tuning per niche, client or platform.
- **English only.**
- **Model-dependent judgment:** some dimensions (clarity, payoff) rely on a language model and carry more uncertainty than measured ones (loudness, pauses). Confidence levels make this visible.

## 8. Product research and rationale

### How the problem was researched

- **Reference products.** Reviewed the five products listed in the brief (including ClipAPI's public API reference), plus clip engines such as WayinVideo and Vizard, focusing on what each measures, how it explains its scores, and how it's built.
- **The team.** Asked three scoping questions in Slack; the answers are in [section 4](#4-assumptions).
- **The dataset.** Reviewed what the 30 clips and their metadata can and can't prove (no retention data, reach depends on the account).
- **Judging with models.** Read current guidance on using LLMs as judges for the dimensions that need judgment ([section 11](#11-tools-feasibility-and-judges)).
- **Planned experiment (deferred).** Comparing hook and audio scores with ClipAPI's and Retensis's free tiers is documented as a next step ([section 23](#23-production-path)), to keep the build within the time cap.

### Reference products

| Product                               | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | What this service takes from it                                                                                                                                                                     | What it leaves out, and why                                                                                                                                                                                                                                                                        |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Retensis**                          | Scores hook, pacing, audio, visual and engagement 0–100, gives prioritised improvement actions, predicts a retention curve with timestamped drop-off reasons, and compares against the creator's past uploads                                                                                                                                                                                                                                                                                                                                                    | Score per dimension with a fix, timestamped evidence, a report a non-technical reader can scan                                                                                                      | The predicted retention curve: with no retention data it can't be validated, so it would look precise while being invented. History comparison: clips here are unrelated                                                                                                                           |
| **Pacing**                            | Extracts frames, separates audio, reads cuts on a precise timeline, then cross-checks each model recommendation against the measured audio and frames and discards contradictions                                                                                                                                                                                                                                                                                                                                                                                | The core architecture: **measure, then judge, then verify**                                                                                                                                         | Audio stem separation: too heavy for the time budget                                                                                                                                                                                                                                               |
| **Twelve Labs**                       | Video understanding infrastructure (video embeddings and a video-language model), not a scorer                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Considered as a component                                                                                                                                                                           | Not used: asking a video model for an opinion is close to forwarding a finished score, and its claims are harder to verify than measurements                                                                                                                                                       |
| **Hookami**                           | Pre-production strategy and trend advice                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Nothing                                                                                                                                                                                             | Out of scope: it helps decide what to film, not which finished clip to post                                                                                                                                                                                                                        |
| **ClipAPI**                           | A video analysis API: send a TikTok, Reel or Short (URL or upload) and get 60+ dimensions back, in three layers: extracted media signals (transcript, scenes, audio, entities), creator analysis (hook type and score, drop-risk moments, story beats) and growth predictions (predicted view range, virality, predicted retention curve). Aggregate 0–100 scores for hook, retention design, visual clarity, audio clarity, call to action and educational value, plus one overall confidence. Async API: submit, get an `analysis_id`, poll or stream progress | Validates the async submit-and-poll design. Borrowed: a `current_step` field while processing, and an optional `external_id` in the metadata. Its scores overlap with this rubric on hook and audio | The growth layer (view range, virality, retention curve): nothing in this dataset can validate it. A call-to-action dimension: interview and podcast clips rarely have one. Using it as a component: that would be forwarding a provider's finished scores, and reviewers would need a ClipAPI key |
| **Clip engines** (WayinVideo, Vizard) | Pick moments from long videos using hook strength, narrative completeness and standalone value                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | The **standalone completeness** dimension, which ClipAPI and Retensis don't score                                                                                                                   | Choosing clips from long-form video happens upstream and is out of scope                                                                                                                                                                                                                           |

**Two points across all of them.** Confidence: ClipAPI gives one overall confidence number; this service gives one per dimension, so a reader sees which scores are solid. Validation: none of these products publish evidence that their scores correlate with real performance. This service states its uncertainty openly instead (confidence per dimension, assumptions marked as assumptions).

### Three ways the market builds this

| Approach                              | How it works                                                                                                                                       | Why not / why                                                                            |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Model watches the video               | Send the video to a video-language model and ask for scores                                                                                        | Simplest, but the score is the model's opinion, hard to check and close to "forwarding"  |
| **Measure, judge, verify** _(chosen)_ | Deterministic tools measure; a model judges only what needs judgment, grounded on the measurements; code checks the model against the measurements | Explainable, cheaper, more repeatable, and it's where this service adds its own analysis |
| Transcript-first ranking              | Rank candidate moments from a transcript                                                                                                           | Built for choosing clips from long-form video, which is upstream of this problem         |

### Key decisions

| Decision                            | Options considered                                             | Chosen                             | Why                                                                                                                                                                                 |
| ----------------------------------- | -------------------------------------------------------------- | ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| When scoring happens                | Before editing (a) or after editing (b)                        | **After editing (b)**              | The dataset is posted clips, and the team said either was fine                                                                                                                      |
| What the user gets                  | Scores only, or scores with a verdict and fixes                | **Verdict, scores and fixes**      | A decision and a next action are more useful than a number                                                                                                                          |
| Rubric size                         | 3 broad, 6 focused or 10+ detailed dimensions                  | **6**                              | Detailed enough to point to a fix, short enough to read in a minute                                                                                                                 |
| Who computes the overall score      | The model or code                                              | **Code**                           | Same inputs, same arithmetic; the model can't drift the verdict                                                                                                                     |
| How the model judges                | One call, one specialist per dimension, or a panel             | **One call now; specialists next** | One structured call is the fastest to build and debug. Specialist judges reduce bleed between dimensions and are the first upgrade ([section 11](#11-tools-feasibility-and-judges)) |
| Grouping clips (`group_id`)         | Build it or not                                                | **Not built**                      | The team confirmed test clips are unrelated                                                                                                                                         |
| Processing                          | Synchronous or background                                      | **Background**                     | Analyses take tens of seconds; the spec accepts either                                                                                                                              |
| Report                              | Single-page app or server-rendered HTML                        | **Server-rendered HTML**           | No build step, fits the time budget ([section 20](#20-html-report))                                                                                                                 |
| Retention curve and view prediction | Predict them (as Retensis and ClipAPI do) or not               | **Not predicted**                  | No data to validate them; the report shows checkable evidence instead                                                                                                               |
| Scope under the 12-hour cap         | Build everything, or build the core well and document the rest | **Core plus a simple report**      | The brief says optional work doesn't compensate for an incomplete core. Deferred items are listed in [section 23](#23-production-path)                                              |
| Call-to-action dimension            | Include it (as ClipAPI does) or not                            | **Not included**                   | Interview and podcast clips rarely have one; it would penalise the format rather than the craft                                                                                     |

### Assumptions and findings

The brief asks to separate what was assumed from what was tested. This table is updated as results come in.

| Statement                                          | Status                          | How it's checked                                                             |
| -------------------------------------------------- | ------------------------------- | ---------------------------------------------------------------------------- |
| A strong hook keeps viewers watching               | Assumption (industry consensus) | Needs retention data, which this dataset doesn't have                        |
| Clips that start or end mid-sentence perform worse | Assumption                      | Directional check: capped vs uncapped clips against account-normalised views |
| Scores are stable across fresh runs                | To be tested                    | Repeatability experiment ([section 21](#21-verification))                    |
| The rubric doesn't favour one production style     | To be tested                    | Score distributions per account                                              |
| The measured signals are accurate on these clips   | To be tested                    | Manual spot check of 5 clips                                                 |
| Cost stays under $1 per clip                       | To be measured                  | Gateway per-generation usage                                                 |

### What success looks like for this version

- A reviewer can set it up from this README alone and analyse their own clip.
- Every score comes with evidence a person can check against the video.
- The verdict for the same clip stays the same across fresh runs.
- Cost stays well under $1 per clip.

### Sources

- ClipAPI API reference: https://www.clipapi.io/docs
- Other reference products (Pacing, Retensis, Hookami, Twelve Labs): links as given in the take-home brief. `TODO: paste the exact URLs`
- Tooling and LLM-as-a-judge sources: see [section 11](#11-tools-feasibility-and-judges)

---

# Part 2: Technical solution

## 9. Architecture

```mermaid
flowchart LR
    subgraph Upstream["Upstream (out of scope)"]
        LV[Long-form video] --> CS[Clipping / editing] --> CL[Edited clip]
    end

    CL -->|POST /analyses<br/>MP4 + metadata| API[FastAPI]
    API --> V{Validate}
    V -->|invalid| E4[4xx error<br/>nothing stored]
    V -->|valid| DB[(Postgres<br/>status = queued)]
    DB -->|202 + analysis ID| C[Client]
    DB --> W[Worker]
    W --> P[Pipeline:<br/>extract → judge → verify → score]
    P -->|success| R[(status = completed<br/>result JSON)]
    P -->|error| F[(status = failed<br/>failure reason)]
    C -->|GET /analyses/:id| API
    API -->|status / result / reason| C
```

Key rule: **every state change is committed to the database before it is reported.** A client never receives an ID that doesn't exist on disk.

## 10. Analysis pipeline

The design follows a **measure → judge → verify** pattern: measure what can be measured with deterministic tools, let a model judge only what needs judgment (grounded on those measurements), then check the model's claims against the measurements. This avoids simply forwarding a provider's opinion of the video.

### Stage 1: Validate

File is a readable MP4, has a video stream, and is within the duration limit (default 240 s, configurable). Metadata fields are well-formed.

### Stage 2: Speech check (simplified)

The service assumes the clip is speech-led, which matches the dataset. If the measured speech ratio is low (below about 30%), the clip is marked `low_speech` and the speech-based dimensions get low confidence. A full speech / music / noise classifier is deferred ([section 23](#23-production-path)).

### Stage 3: Extract signals (deterministic, no AI judgment)

| Signal                                            | Tool                                                | Feeds                       |
| ------------------------------------------------- | --------------------------------------------------- | --------------------------- |
| Transcript with word timestamps                   | faster-whisper (local)                              | Hook, completeness, clarity |
| Time to first word, first sentence                | transcript                                          | Hook                        |
| Starts / ends mid-sentence                        | speech active at first / last frame + opening words | Completeness, cap rule      |
| Speech ratio, pauses > 1 s, words per minute      | VAD + transcript                                    | Pacing                      |
| Scene cuts per minute                             | PySceneDetect (AdaptiveDetector)                    | Pacing, hook                |
| Loudness (LUFS), clipping, silence                | ffmpeg `ebur128` / `silencedetect`                  | Audio                       |
| Sampled frames (dense in first 3 s, sparse after) | ffmpeg                                              | Hook, on-screen text        |

### Stage 4: Judge (one model call)

One call to the pinned model scores the four model-judged dimensions (hook, completeness, clarity, on-screen text). It receives the transcript with timestamps, the measured signals, 4–6 sampled frames and the anchored rubric for each dimension, and returns **structured JSON only**: per dimension, a score, evidence with timestamps, one suggested fix and a confidence level, plus the summary, the clip's point and the viral potential fields. Pacing and audio are scored from measurements. The model never computes the overall score or verdict. Design and rationale in [section 11](#11-tools-feasibility-and-judges).

### Stage 5: Verify

Three deterministic rules check the model against the measurements:

- **Late start:** first word after 4 s → hook score capped, whatever the model says.
- **Boundary cut:** speech active at the first or last frame plus a conjunction as the first word → completeness flagged and the cap rule applied.
- **Missing evidence:** a score whose evidence cites a timestamp beyond the clip's length → confidence lowered.

More rules can be added later without changing the pipeline.

Contradictions lower the dimension's confidence and are recorded in the output.

### Stage 6: Score

Overall score, cap rule and verdict are computed **in code**, from the rubric config. Same inputs always produce the same arithmetic.

## 11. Tools, feasibility and judges

Every dimension in the rubric can be built with standard, mostly local tools, within the $1 target and the 12-hour cap. The dimensions are **not** equally reliable, though: some rest on hard measurements, and in this version two (clarity and on-screen text) are almost entirely model judgment. This section makes that difference explicit.

### Toolbox

| Tool                                         | Used for                                                                  | Why this one                                                                                    | Alternative considered                                                                                                                      |
| -------------------------------------------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **ffmpeg**                                   | Decoding, frame sampling, loudness (`ebur128`), silence (`silencedetect`) | Standard, deterministic, already required                                                       | –                                                                                                                                           |
| **faster-whisper**                           | Transcript with word timestamps; built-in Silero VAD                      | Runs locally on CPU (int8), free, fast enough for 1–3 min clips                                 | **WhisperX**: adds wav2vec2 forced alignment for more precise word timestamps. Worth it if boundary detection proves unreliable             |
| **PySceneDetect** (`AdaptiveDetector`)       | Cut detection, cuts per minute, visual change in first 3 s                | The adaptive detector copes better with camera movement than a fixed threshold                  | ffmpeg scene filter (fewer dependencies, cruder)                                                                                            |
| **inaSpeechSegmenter** _(deferred)_          | Speech / music / noise zones for mode detection                           | Tags singing as music, which is exactly the "lyrics transcribed as speech" guard                | YAMNet / PANNs audio classifiers. Note: it depends on TensorFlow and supports Python up to 3.12, so it's optional and the service pins 3.12 |
| **RapidOCR** (ONNX Runtime) _(deferred)_     | Detecting burned-in captions and headlines, and reading their text        | Light, offline, Apache 2.0, no GPU needed                                                       | Vision model on frames (costs tokens); Tesseract (weaker on stylised captions)                                                              |
| **librosa / Praat-parselmouth** _(deferred)_ | Pitch and loudness variation as an "energy" proxy                         | Cheap, deterministic                                                                            | Leave energy entirely to the judge                                                                                                          |
| **Gateway model** (pinned)                   | The judge call, including 4–6 frames                                      | Needs vision for frames; model picked in the selection phase ([section 16](#16-model-strategy)) | Sending the whole video to a video-native model: simpler, but closer to "forwarding" and harder to verify                                   |
| **Ollama + local `gemma3`**                  | Development runs                                                          | Free, same OpenAI-compatible client; `gemma3` has vision, so frames are judged locally too      | Llama 3.1 8B (text only: on-screen text couldn't be judged)                                                                                 |

### Per dimension: how it's measured and how much to trust it

| Dimension                    | Measured (code)                                                                                           | Judged (model)                                                                                          | Measured / judged | Confidence  | Main risk                                                                                                                                     |
| ---------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hook**                     | Time to first word, cuts and visual change in first 3 s, text on first frames                             | Does the opening line or image create a question, claim or tension?                                     | ~40 / 60          | Medium      | "Reason to keep watching" is subjective; the hook-to-retention link is an assumption                                                          |
| **Standalone completeness**  | Speech already active at 0.0 s or at the last frame; first word is a conjunction ("and", "so", "because") | Dangling references ("as I said", unexplained "he"); does the clip end on a finished thought?           | ~50 / 50          | Medium–high | Whisper tidies punctuation, so punctuation alone is not used. The cap rule needs **two agreeing signals** to avoid wrongly capping good clips |
| **Clarity and payoff**       | –                                                                                                         | Can the judge state the clip's point in one sentence? Does the ending resolve it?                       | ~0 / 100          | Low–medium  | Most run-to-run variance; exposure to stance bias on political clips                                                                          |
| **Pacing and energy**        | Words per minute, pauses > 1 s, speech ratio, cuts per minute, loudness / pitch variation                 | Optional context note only (e.g. a deliberate dramatic pause)                                           | ~90 / 10          | High        | Thresholds are norms. Cuts are weighted lightly so static podcast cameras aren't penalised                                                    |
| **Audio and speech quality** | Integrated loudness, true peak / clipping, silence, Whisper confidence as intelligibility proxy           | –                                                                                                       | ~100 / 0          | High        | Music masking speech is only roughly measured without source separation                                                                       |
| **On-screen text**           | – (OCR and caption sync deferred)                                                                         | Captions and headline read from 4–6 sampled frames: present, when they start, readability and placement | ~0 / 100          | Medium      | Frames are samples, so short gaps in captions can be missed; OCR would make presence measurable                                               |

Measurements also **bound** judgments: for example, if the first word arrives after 4 s, the hook can't score above a configured ceiling, whatever the judge says. That is the verify step (Stage 5).

### Judge design: one call now, specialist judges next

Three options were considered:

| Option                                                         | How                                                          | Pros                                                                                                                                             | Cons                                                                                                      |
| -------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| **One call scores all dimensions** _(chosen for this version)_ | Full evidence + full rubric in one prompt                    | Cheapest, simplest, fastest to build and debug                                                                                                   | The first dimension's impression can bleed into the others (anchoring); a long prompt dilutes each rubric |
| Specialist judge per dimension _(next step)_                   | One focused call per model-judged dimension, run in parallel | Less bleed between dimensions; each prompt is short and anchored; failures isolated to one dimension; each judge only gets the evidence it needs | More calls to orchestrate                                                                                 |
| Panel of judges                                                | Several models, or repeated runs, averaged                   | Most stable scores                                                                                                                               | Multiplies cost; reserved for the eval and the repeatability experiment                                   |

The single call is structured to limit anchoring: one section per dimension, each with its own anchored scale, and the model must write evidence before each score. If repeatability shows scores moving together, that's the signal to split into specialist judges. What each specialist judge would receive:

| Judge              | Evidence it gets                                                                                           |
| ------------------ | ---------------------------------------------------------------------------------------------------------- |
| Hook               | First ~5 s of transcript, time to first word, 3–4 frames from the first 3 s, cut timestamps in that window |
| Completeness       | First and last two sentences with timestamps, boundary signals, full transcript for reference checks       |
| Clarity and payoff | Full transcript only                                                                                       |
| On-screen text     | 4–6 sampled frames (plus OCR output once added)                                                            |

Splitting costs about the same in tokens, since each judge sees a slice of the evidence, not all of it.

**Prompt rules, taken from LLM-as-a-judge practice**

- **Anchored scale.** Judges score on a **0–5 scale** with a written description for every level, then code maps it to 0–10. Research on grading scales found human–LLM agreement highest on 0–5, and coarse anchored scales reduce indeterminacy.
- **Evidence first, score second.** The judge must cite timestamps or quotes before giving the score, which reduces unsupported scores.
- **A few balanced examples** from the development subset (a weak, a middle and a strong clip) calibrate each judge.
- **Length cap on rationales.** LLM judges tend to reward length; short, capped rationales avoid that.
- **Stance-neutral clause** in every prompt: score how the clip is built, never the opinion expressed.
- **Temperature 0, pinned model, versioned prompts.** The rubric is treated as a hypothesis: versioned like code and recalibrated after changes.

The verifier stays **deterministic code**, not another model: it's free, repeatable and can't hallucinate.

### Built in this version vs deferred

| Built                                                                                                                       | Deferred ([section 23](#23-production-path))                                                 |
| --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| faster-whisper transcript and timestamps, boundary checks, pacing from word timestamps, PySceneDetect cuts, ffmpeg loudness | RapidOCR and caption sync, pitch-based energy, speech / music classifier, WhisperX alignment |
| One judge call with frames, three verify rules, scoring in code                                                             | Specialist judges, a larger verify rule set                                                  |

### Sources

- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- WhisperX (forced alignment for word timestamps): https://github.com/m-bain/whisperX
- PySceneDetect: https://github.com/Breakthrough/PySceneDetect
- inaSpeechSegmenter: https://github.com/ina-foss/inaSpeechSegmenter
- RapidOCR: https://github.com/RapidAI/RapidOCR
- Grading scale impact on LLM judges (0–5 alignment): https://arxiv.org/abs/2601.03444
- Autorubric, rubric-based LLM evaluation (few-shot calibration, behavioural anchoring): https://arxiv.org/abs/2603.00077
- Judge prompt patterns (per-criterion calls, verbosity bias, rubric drift): https://galtea.ai/blog/llm-as-a-judge-prompts-templates-rubrics-and-best-practices

## 12. API

**Interactive docs:** Swagger UI at [`/docs`](http://localhost:9500/docs) (upload a clip with "Try it out"), ReDoc at `/redoc`, raw schema at `/openapi.json`.

The response models in `app/api/schemas.py` mirror [`docs/report-example.json`](docs/report-example.json) and are used as `response_model`, so the docs show the real shape and every response is validated against it at runtime. Tests fail if the schema and the example drift apart.

### `POST /analyses`

Multipart form:

- `file`: the MP4
- `metadata`: JSON string, e.g. `{"title": "...", "account": "...", "platform": "tiktok", "external_id": "C07"}` (all optional except what's listed as required in `TBD`)
  - `external_id` is the caller's own reference (for example a sample ID or post ID). It's returned unchanged in every response, so the caller can match results to their records. It doesn't affect the analysis and isn't part of the duplicate check.

Query parameters:

- `fresh=true`: force a new analysis even if an identical one exists (see [section 14](#14-repeatability-and-caching))

Response:

- `202 Accepted` with `{"id": "...", "status": "queued"}` for a new analysis
- `200 OK` with the existing analysis and `"deduplicated": true` when the same clip is already queued, processing or completed (see [section 15](#15-interrupted-runs-duplicates-and-concurrency))

### `GET /analyses/{id}`

Returns the analysis with its current status:

| Status       | Meaning                                                                                  |
| ------------ | ---------------------------------------------------------------------------------------- |
| `queued`     | Stored, waiting to be processed                                                          |
| `processing` | Pipeline running; `current_step` shows the stage (`extract`, `judge`, `verify`, `score`) |
| `completed`  | Result available                                                                         |
| `failed`     | Includes `error.code` and `error.message`                                                |

### `GET /analyses/{id}/report` _(optional)_

Human-readable HTML view of one stored result. See [section 20](#20-html-report).

### `GET /health`

Liveness check.

### Sync vs async

**Async (proposed).** An analysis takes tens of seconds, which is too long to hold an HTTP request open reliably. `POST` returns immediately with an ID and the client polls `GET`. For this take-home the worker runs in the same process; in production it would be a separate worker (see [section 23](#23-production-path)).

### HTTP codes

| Code  | When                                                                                                      |
| ----- | --------------------------------------------------------------------------------------------------------- |
| `200` | `GET` found the analysis (including `failed` analyses: the request succeeded, the analysis didn't)        |
| `200` | `POST` matched an existing analysis of the same clip; returns that analysis instead of creating a new one |
| `202` | `POST` accepted and stored as a new analysis                                                              |
| `400` | Malformed metadata                                                                                        |
| `404` | Unknown analysis ID                                                                                       |
| `413` | File too large                                                                                            |
| `415` | Not an MP4                                                                                                |
| `422` | Valid MP4 but unusable (no video stream, too long, corrupt)                                               |
| `500` | Unexpected server error                                                                                   |

Failure reasons stored on `failed` analyses: `extraction_failed`, `model_error`, `model_invalid_output`, `interrupted_by_restart`, and others as needed (full list `TBD`).

## 13. Storage and durability

**Postgres** (16, run by docker compose with a named volume). Every write is a committed transaction; JSON columns are `JSONB` and timestamps `TIMESTAMPTZ`. The schema is managed with Alembic migrations written as plain SQL (`app/storage/alembic/versions/`), applied by `make migrate`, or on startup behind an advisory lock when `AUTO_RUN_MIGRATIONS=true` (the default in `example.env` and docker compose; without it the service refuses to start on a database with pending migrations); `make migration m="..."` creates a new one.

### `analyses` table (draft)

| Column                                                             | Purpose                                                                                                                                                                                                      |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `id`                                                               | Analysis ID                                                                                                                                                                                                  |
| `status`                                                           | `queued` / `processing` / `completed` / `failed`                                                                                                                                                             |
| `current_step`                                                     | Pipeline stage while `processing`, for callers polling progress                                                                                                                                              |
| `external_id`                                                      | Caller's own reference, returned as is                                                                                                                                                                       |
| `file_sha256`                                                      | Content hash of the video                                                                                                                                                                                    |
| `cache_key`                                                        | Hash of file + analysis-relevant metadata + config versions. Partial unique index while the row is `queued`, `processing` or `completed` (see [section 15](#15-interrupted-runs-duplicates-and-concurrency)) |
| `metadata_json`                                                    | Metadata sent by the client                                                                                                                                                                                  |
| `result_json`                                                      | Full result when completed                                                                                                                                                                                   |
| `error_code`, `error_message`                                      | Failure reason                                                                                                                                                                                               |
| `model_id`, `prompt_version`, `rubric_version`, `pipeline_version` | What produced this result                                                                                                                                                                                    |
| `cost_usd`, `duration_ms`                                          | Measured per analysis                                                                                                                                                                                        |
| `created_at`, `started_at`, `finished_at`                          | Timestamps                                                                                                                                                                                                   |

Uploaded files are stored on disk under their SHA-256 hash.

### Surviving `kill -9`

- Every write is a committed transaction before anything is returned.
- On startup, any analysis left in `queued` or `processing` is marked `failed` with reason `interrupted_by_restart`. Resuming is out of scope; see [section 15](#15-interrupted-runs-duplicates-and-concurrency) for what happens and what would come next.
- A script reproduces the check: upload, `kill -9` the server, restart, `GET` the ID (see [section 21](#21-verification)).

## 14. Repeatability and caching

Two different questions, handled separately:

- **Cached retrieval:** the same video with the same metadata and config returns the stored result instantly, marked `"source": "cached"`. This saves cost and gives identical answers by definition.
- **Fresh inference:** `fresh=true` forces the full pipeline to run again, marked `"source": "fresh"`. This is what the repeatability test uses, so it measures the model's real stability rather than the cache.

To keep fresh runs stable: temperature 0, fixed seed where the provider supports it, pinned model version, and scores computed in code rather than by the model. Every result records the model, prompt, rubric and pipeline versions that produced it.

## 15. Interrupted runs, duplicates and concurrency

The spec puts job recovery and retries out of scope, and says documenting duplicate and concurrency handling is enough. This section describes what the service does today and what would come next. The guiding rule is simple: **a client sending the same clip should never pay for the same analysis twice.**

### When the service stops during a run

**What happens today**

- Every status change (`queued` → `processing` → `completed` / `failed`) is committed before it is reported, so the database is always the source of truth.
- The worker queue lives in memory, so a crash loses it. On startup, the service marks every analysis left in `queued` or `processing` as `failed` with `error.code = interrupted_by_restart`. Nothing stays stuck forever, and the caller gets a clear reason from `GET`.
- The caller can simply resubmit the clip. Failed analyses are excluded from deduplication (below), so a resubmission starts a new analysis instead of returning the old failure.
- Completed analyses are never touched by the sweep.

**What would come next**

1. **Checkpoint each stage.** Store the output of the expensive stages (transcript and extracted signals, keyed by `file_sha256` + `pipeline_version`). A rerun after a crash or a model error skips straight to the step that failed. Since transcription runs locally, the main saving is time; the model call is the step that costs money, and it only runs once per successful attempt.
2. **Leases instead of a startup sweep.** A worker claims a job by setting `lease_expires_at` and renews it while working. If the worker dies, the lease expires and the job goes back to `queued` automatically, without waiting for a restart. This is also what makes multiple workers safe.
3. **Bounded retries, only for transient errors.** Retry model timeouts, rate limits (`429`) and provider `5xx` errors, at most 2 times with exponential backoff and jitter. Never retry an unreadable file, and retry invalid model output at most once. Each attempt is recorded (`attempt`, `cost_usd`) so retries can't silently multiply cost, and an analysis stops if it exceeds a per-clip cost ceiling (for example, $0.50).
4. **Hand it to the orchestrator in production.** Each pipeline stage becomes a Temporal activity or Trigger.dev task, which provides leases, retries, backoff and resume-from-last-step without custom code.

### Duplicate and concurrent requests

**How a duplicate is recognised**

`cache_key` = SHA-256 of the file bytes + the metadata fields that actually influence the analysis + the settings that change the result (model, frame budget, vision, seed, JSON mode, Whisper model and compute type) + the prompt, rubric and pipeline versions. Bookkeeping fields (for example, an external post ID) are left out, so changing them doesn't trigger a paid re-analysis.

**What happens in each case**

| Case                                                        | Behaviour                                                                                                                                            | Model cost                                                    |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| Same clip, previous analysis `completed`                    | Return the existing analysis (`200`, `"deduplicated": true`, `"source": "cached"`)                                                                   | None                                                          |
| Same clip, previous analysis still `queued` or `processing` | Return the in-flight analysis ID (`200`, `"deduplicated": true`); the client polls it like any other                                                 | None                                                          |
| Two identical uploads at the same moment                    | The database decides: both try to insert, the partial unique index on `cache_key` lets only one succeed, and the other request reads the winner's ID | Paid once                                                     |
| Same clip, previous analysis `failed`                       | New analysis (`202`)                                                                                                                                 | Paid again, by design                                         |
| Same clip with `fresh=true`                                 | New analysis (`202`), used for repeatability tests                                                                                                   | Paid again, by design                                         |
| Rubric, prompt or model version changed                     | New analysis, because results from an old configuration aren't comparable                                                                            | Paid again; with stage checkpoints only the model step reruns |

**Why the database handles the race**

A "check, then insert" in application code has a gap: two requests can both check, both find nothing and both start a paid analysis. A partial unique index closes that gap:

```sql
CREATE UNIQUE INDEX one_live_analysis_per_clip
  ON analyses (cache_key)
  WHERE status IN ('queued', 'processing', 'completed');
```

The `POST` handler runs `INSERT ... ON CONFLICT DO NOTHING`. If no row was inserted, it selects the existing row by `cache_key` and returns it. Failed rows fall outside the index, which is why a failed clip can be resubmitted.

**Known gaps and next steps**

- **Re-encoded copies aren't detected.** The same video exported twice produces different bytes and a different hash. A perceptual video hash or an audio fingerprint would catch these.
- **`fresh=true` can be abused.** Any caller can force a paid run. In production it would sit behind an admin flag or a per-client quota.
- **Client retries after a network timeout** are already covered by content hashing. An `Idempotency-Key` header would add protection for clients that send slightly different metadata on each retry.
- **Upload bandwidth is still spent** on duplicates, because the hash is computed while the file streams in. A client could send the hash first and skip the upload if it's already known.

## 16. Model strategy

**The model does not need to be fixed from day one, but it must be fixed before tuning and before any reported numbers.**

The code talks to models through an OpenAI-compatible client, configured by environment variables. Both Vercel AI Gateway and Ollama expose this format, so switching is a config change, not a code change:

```bash
# Local development (free, offline)
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=gemma3:latest

# Final runs (reported results)
LLM_BASE_URL=https://ai-gateway.vercel.sh/v1
LLM_MODEL=TBD   # pinned after testing
```

### Phases

| Phase      | Model                       | Purpose                                                                                                        |
| ---------- | --------------------------- | -------------------------------------------------------------------------------------------------------------- |
| **Build**  | Local `gemma3` via Ollama   | Wire the pipeline, prompts, JSON parsing, errors and durability without spending credits                       |
| **Select** | 2 gateway models on 2 clips | Compare output quality, JSON reliability, video/image support, cost and latency. Record what was tried and why |
| **Pin**    | One gateway model           | Tune weights and thresholds, run the dataset, run repeatability. Everything reported comes from this model     |

Why pin before tuning: weights and thresholds are calibrated to how a specific model scores. Switching models afterwards means re-tuning.

Notes on local models:

- `gemma3` (4B) is the local default because it has vision, so frame-based checks (hook visuals, on-screen text) run locally too.
- With a text-only model, set `LLM_VISION=false`: frames are skipped and on-screen text is marked not applicable (weights rescaled). The other dimensions still work thanks to the measure-first design.
- Observed with `gemma3`: it ignores the 25-word cap on evidence items. A reason to pin a stronger gateway model for reported runs.
- Transcription runs locally with faster-whisper in every phase, so it adds no API cost.

## 17. Stack

| Component            | Choice                   | Why                                                                   |
| -------------------- | ------------------------ | --------------------------------------------------------------------- |
| Language / framework | Python 3.12 + FastAPI    | Matches Home Game's stack; best ecosystem for video and audio tooling |
| Media processing     | ffmpeg                   | Standard, reliable, deterministic                                     |
| Transcription        | faster-whisper           | Local, free, word timestamps                                          |
| Cut detection        | PySceneDetect            | Adaptive to camera movement                                           |
| Storage              | Postgres 16 (psycopg 3)  | Crash-safe, real concurrency, partial unique index for dedupe         |
| Model access         | OpenAI-compatible client | Same code for Vercel AI Gateway and Ollama                            |
| Report               | Jinja2 templates         | Server-rendered HTML, no build step                                   |
| Packaging            | Docker + docker compose  | Reviewers don't need to install ffmpeg or Python deps                 |

### In use in this version

What the code actually runs today. Anything marked _deferred_ in [section 11](#11-tools-feasibility-and-judges) is not installed.

| Tool / package                             | Where                                  | What it does here                                                                            |
| ------------------------------------------ | -------------------------------------- | -------------------------------------------------------------------------------------------- |
| `ffprobe`                                  | `app/pipeline/validate.py`             | Upload validation: duration, video / audio streams, resolution, fps                          |
| `ffmpeg`                                   | `app/pipeline/extract.py`              | 16 kHz mono WAV, `ebur128` (loudness, true peak), `silencedetect`, frame sampling            |
| `faster-whisper` (`base.en`, CPU int8)     | `app/pipeline/extract.py`              | Transcript with word timestamps; Silero VAD via `vad_filter=True`                            |
| `scenedetect` + `opencv-python-headless`   | `app/pipeline/extract.py`              | Cut detection with `AdaptiveDetector`                                                        |
| `openai` (client only)                     | `app/llm/client.py`                    | One judge call to any OpenAI-compatible endpoint (Ollama `gemma3` now; gateway model TBD)    |
| `fastapi`, `uvicorn`, `python-multipart`   | `app/api/`, `app/main.py`              | HTTP API and multipart uploads                                                               |
| `pydantic`, `pydantic-settings`            | `app/pipeline/judge.py`, `app/settings.py` | Judge output validation; config from environment / `.env`                                |
| `psycopg[binary]`, `psycopg-pool`          | `app/storage/db.py`                    | Postgres access with a thread-safe pool, partial unique index for deduplication              |
| `threading` / `queue` (stdlib)             | `app/worker.py`                        | In-process background worker                                                                 |
| `jinja2`                                   | `app/report/`                          | Server-rendered HTML report                                                                  |
| `pyyaml`                                   | `app/config/rubric.py`                 | Loads `rubric.yaml`                                                                          |
| `pytest`, `httpx` (dev)                    | `tests/`, `scripts/`                   | Tests; HTTP client for the evaluation scripts                                                |

Not used yet: inaSpeechSegmenter, RapidOCR, librosa / Praat-parselmouth, WhisperX, and a pinned gateway model.

### Project structure (draft)

```
.
├── app/
│   ├── api/            # FastAPI routes and error handlers
│   ├── pipeline/       # validate, extract, judge, verify, score
│   ├── storage/        # Postgres access and Alembic migrations
│   ├── llm/            # OpenAI-compatible client wrapper
│   ├── report/         # Jinja2 templates for the HTML report
│   └── config/         # rubric.yaml, prompts, settings
├── scripts/
│   ├── restart_check.sh
│   ├── repeatability.py
│   └── run_dataset.py
├── tests/
├── docs/               # report examples, diagrams (BPMN)
├── data/               # dataset goes here (not committed)
├── docker-compose.yml
├── Dockerfile
├── example.env
└── README.md
```

The rubric (dimensions, weights, thresholds, cap rule) lives in `rubric.yaml`, so it can be changed without touching code and its version is stored with every result.

## 18. Setup and running

**Docker (recommended: ffmpeg is included)**

```bash
docker compose up --build     # Postgres in a named volume; uploads and the Whisper model cache in ./var
curl http://localhost:9500/health
```

**Local (needs `ffmpeg` on the PATH: `brew install ffmpeg`)**

```bash
docker compose up -d db      # Postgres on localhost:5432
uv sync
uv run uvicorn app.main:app --port 9500
uv run pytest                 # unit, API, storage and pipeline tests (need the db; no ffmpeg or model)
```

**Configuration** (`.env`, all optional):

| Variable                                                | Default                     | Notes                                                                      |
| ------------------------------------------------------- | --------------------------- | -------------------------------------------------------------------------- |
| `LLM_BASE_URL`                                          | `http://localhost:11434/v1` | Ollama locally; `https://ai-gateway.vercel.sh/v1` for reported runs        |
| `LLM_MODEL`                                             | `gemma3:latest`             | Any OpenAI-compatible model id; part of the cache key                      |
| `DOCKER_LLM_BASE_URL`                                  | `http://host.docker.internal:11434/v1` | Used by docker compose instead of `LLM_BASE_URL` (`localhost` in a container is the container) |
| `AI_GATEWAY_API_KEY` / `LLM_API_KEY`                    | `ollama`                    | Never commit it                                                            |
| `LLM_VISION`                                            | `true`                      | `false` skips frames; on-screen text is then marked not applicable         |
| `LLM_MAX_FRAMES`                                        | `16`                        | Frame budget: 3 in the hook, a time grid, then frames just after cuts. ~275 tokens each on gemma3, so a 4k-context model fits about 6 |
| `LLM_JSON_MODE`                                         | `true`                      | Sends `response_format=json_object`; turn off for providers that reject it |
| `LLM_PRICE_INPUT_PER_MTOK`, `LLM_PRICE_OUTPUT_PER_MTOK` | `0`                         | Cost estimate when the provider doesn't report cost in `usage`             |
| `WHISPER_MODEL`                                         | `base.en`                   | faster-whisper model, CPU int8                                             |
| `MAX_UPLOAD_MB`, `MAX_DURATION_S`                       | `200`, `240`                |                                                                            |
| `DATABASE_URL`                                          | `postgresql://clip:clip@localhost:5432/clip_scoring` | docker compose points it at the `db` service           |
| `TEST_DATABASE_URL`                                     | `postgresql://clip:clip@localhost:5432/postgres` | Tests create and drop one database per test on this server |
| `DATA_DIR`                                              | `var`                       | Uploads, Whisper model cache, temp work dirs                               |

**Checks**

```bash
scripts/restart_check.sh data/videos/C01.mp4                      # kill -9 durability
uv run python scripts/repeatability.py data/videos/C01.mp4 --runs 5
uv run python scripts/run_dataset.py data/videos --metadata <csv> --accounts data/account-context.csv
```

**Code quality (local SonarQube)**

```bash
make sonar-up       # SonarQube on :9002 (own compose project in quality/), admin password set, token in quality/.sonar-token
make sonar-scan     # tests with coverage, scanner in Docker, report printed in the terminal
make sonar-report   # print the last report again; make sonar-open for the dashboard
make sonar-down     # stop, keeping the history (make sonar-clean wipes it)
```

`coverage.xml` is written with repo-relative paths (`relative_files` in `pyproject.toml`); with absolute host paths the scanner container matches no file and reports 0% coverage. Sonar's coverage counts `scripts/` too, so it reads lower than pytest's `app/`-only figure. The numbers are a floor, not a verdict: a clean rating does not replace reading the code.

## 19. Example requests and output

```bash
# Submit
curl -X POST http://localhost:9500/analyses \
  -F "file=@data/videos/C01.mp4" \
  -F 'metadata={"title": "Why permit approvals take so long", "account": "example_account", "external_id": "C07"}'

# Retrieve
curl http://localhost:9500/analyses/<id>
```

Output shape (**illustrative, to be replaced with a real output**). Abridged here; the full response behind the example report is in [`docs/report-example.json`](docs/report-example.json), and every field is described in [section 20](#20-html-report).

```json
{
  "id": "an_01J8XK4Q2M7C9D3F5H6J8K0N1P",
  "status": "completed",
  "current_step": null,
  "source": "fresh",
  "input": { "filename": "C07.mp4", "sha256": "9f2c…e41a", "duration_s": 74.2 },
  "metadata": {
    "title": "Why permit approvals take so long",
    "external_id": "C07"
  },
  "mode": "speech",
  "overall": {
    "score": 6.6,
    "verdict": "improve",
    "capped": false,
    "summary": "Clear, well-delivered point with a strong ending, but the first six seconds are setup and captions only start at 0:18.",
    "point": "Permit approvals take 14 months on average, and the guest argues a 90-day limit would fix it."
  },
  "potential": {
    "helps": [
      "A concrete, surprising number (14 months) that people repeat when they share."
    ],
    "holds_back": [
      "The first six seconds are setup, so many viewers scroll away before the claim."
    ],
    "shareable_line": {
      "t": 6.0,
      "text": "Fourteen months to approve a permit that should take ninety days."
    }
  },
  "priority_fixes": [
    {
      "rank": 1,
      "dimension": "hook",
      "fix": "Start the clip at 0:06, where the main claim begins…",
      "weighted_gap": 1.25
    }
  ],
  "dimensions": [
    {
      "id": "hook",
      "label": "Hook",
      "score": 5,
      "weight": 0.25,
      "weighted_gap": 1.25,
      "confidence": "high",
      "basis": "mixed",
      "evidence": [
        "First word at 0.4 s, so there is no dead air.",
        "0:00 to 0:06 is background context; the main claim only arrives at 0:06."
      ],
      "fix": "Start the clip at 0:06, where the main claim begins, or add a headline that states the claim from the first frame."
    }
  ],
  "signals": {
    "time_to_first_word_s": 0.4,
    "words_per_minute": 168,
    "longest_pause": { "t": 41.0, "duration_s": 1.8 }
  },
  "verification": { "checks_run": 3, "contradictions": [] },
  "provenance": {
    "model": "<pinned gateway model>",
    "rubric_version": "v1",
    "cost_usd": 0.03,
    "duration_ms": 41000
  },
  "error": null
}
```

## 20. HTML report

A stakeholder shouldn't have to read JSON. The spec lists "a simple UI or readable report" as optional, so the report is built **only after the core service works**, and it reads the same stored result the API returns.

### Pages

| Page        | Endpoint                    | What it shows                                                                                                                                                                                                                                                       |
| ----------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Clip report | `GET /analyses/{id}/report` | Verdict and one-line summary, overall score, the clip's point in one sentence, viral potential, top 3 fixes, every dimension with evidence, fix, weight and confidence, the measured signals, and what produced the result (model, versions, cost, fresh or cached) |

The report covers **one clip**, matching how clips are uploaded. There's no overview or ranking page, because clips aren't grouped (see [section 4](#4-assumptions)).

Example with illustrative data: [`docs/report-example.html`](docs/report-example.html), rendered from [`docs/report-example.json`](docs/report-example.json). Open the HTML in a browser.

### The report contract

The report has **no data of its own**. `GET /analyses/{id}/report` loads the same stored result that `GET /analyses/{id}` returns and passes it to a Jinja2 template. So the JSON is the contract: anything the page shows must exist in it, and the template only formats (seconds to `m:ss`, fractions to percentages). This keeps the API and the page from ever disagreeing, and any other client (a UI, a Slack bot, another service) gets exactly what the report shows.

**Where each part of the page comes from**

| Report block                                    | JSON path                                                                                 | Notes                                                                     |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Clip line (file, account, length)               | `input.filename`, `metadata.account`, `input.duration_s`                                  |                                                                           |
| Verdict headline                                | `overall.verdict`, `overall.verdict_label`                                                | Label text lives in the rubric config, so wording can change without code |
| Summary sentence                                | `overall.summary`                                                                         | Written by the judge call                                                 |
| Overall score                                   | `overall.score`                                                                           | Rounded to one decimal; `raw_score` keeps full precision                  |
| Cap notice _(when applied)_                     | `overall.capped`, `overall.cap_reason`                                                    |                                                                           |
| The clip's point                                | `overall.point`                                                                           | One sentence from the judge call                                          |
| Viral potential                                 | `potential.helps`, `potential.holds_back`, `potential.shareable_line`, `potential.caveat` | From the same judge call; no extra model call                             |
| What to fix first                               | `priority_fixes[]`                                                                        | Top 3 by `weighted_gap`, ties broken by weight; computed in code          |
| Dimension cards                                 | `dimensions[]`                                                                            | Score, weight, confidence, evidence, fix; bar colour from the score band  |
| Measured signals                                | `signals.*`                                                                               | Deterministic measurements only, never model output                       |
| Footer (model, versions, cost, fresh or cached) | `provenance.*`, `source`                                                                  |                                                                           |
| Status page _(not completed)_                   | `status`, `current_step`, `error.code`, `error.message`                                   | Shown instead of the report for `queued`, `processing` and `failed`       |

**Field reference**

| Field                              | Type                                                | Always present | Meaning                                                                            |
| ---------------------------------- | --------------------------------------------------- | -------------- | ---------------------------------------------------------------------------------- |
| `id`                               | string                                              | yes            | Analysis ID                                                                        |
| `status`                           | `queued` \| `processing` \| `completed` \| `failed` | yes            | Lifecycle state                                                                    |
| `current_step`                     | string \| null                                      | yes            | Pipeline stage while `processing`, otherwise `null`                                |
| `source`                           | `fresh` \| `cached`                                 | when completed | Whether the model ran for this request or a stored result was returned             |
| `input`                            | object                                              | yes            | File facts measured on upload: name, SHA-256, size, duration, resolution, fps      |
| `metadata`                         | object                                              | yes            | What the caller sent, returned as is (including `external_id`)                     |
| `mode`                             | `speech` \| `low_speech`                            | when completed | `low_speech` lowers confidence on speech-based dimensions                          |
| `overall.score`                    | number 0–10, one decimal                            | when completed | Weighted average of applicable dimensions, after the cap rule                      |
| `overall.verdict`                  | `post` \| `improve` \| `skip`                       | when completed | From the thresholds in `rubric.yaml`                                               |
| `overall.capped`, `cap_reason`     | boolean, string \| null                             | when completed | Whether the mid-sentence cap applied, and why                                      |
| `overall.summary`, `overall.point` | string                                              | when completed | One-line verdict explanation; the clip's point in one sentence                     |
| `potential.helps`, `holds_back`    | string[] (up to 3 each)                             | when completed | Strengths and blockers for reach, read from the clip                               |
| `potential.shareable_line`         | `{t, text, note}` \| null                           | when completed | Most quotable sentence and its timestamp; `null` when there's no speech            |
| `priority_fixes[]`                 | `{rank, dimension, label, fix, weighted_gap}`       | when completed | Up to 3; empty when nothing needs fixing                                           |
| `dimensions[]`                     | array of 6                                          | when completed | One entry per rubric dimension, in rubric order                                    |
| `dimensions[].score`               | integer 0–10 \| null                                | when completed | `null` when `applicable` is false (for example audio quality on a music-only clip) |
| `dimensions[].weighted_gap`        | number                                              | when completed | `weight × (10 − score)`, the most a fix could recover                              |
| `dimensions[].confidence`          | `high` \| `medium` \| `low`                         | when completed | Lowered when the verify step finds a contradiction                                 |
| `dimensions[].basis`               | `measured` \| `judged` \| `mixed`                   | when completed | Where the score comes from ([section 11](#11-tools-feasibility-and-judges))        |
| `dimensions[].evidence`            | string[]                                            | when completed | Checkable observations, with timestamps where possible                             |
| `dimensions[].fix`                 | string \| null                                      | when completed | `null` when no change is needed                                                    |
| `signals`                          | object                                              | when completed | Raw measurements behind the scores                                                 |
| `verification`                     | `{checks_run, contradictions[]}`                    | when completed | What the verify step checked and what it found                                     |
| `provenance`                       | object                                              | when completed | Model, prompt, rubric and pipeline versions, number of model calls, cost, duration |
| `error`                            | `{code, message}` \| null                           | yes            | Set only when `status` is `failed`                                                 |

Adding a field to the page means adding it to this contract first, and bumping `pipeline_version`.

### Viral potential

The section a reader looks at to decide whether the clip is worth pushing. It has three parts:

- **What helps it travel:** strengths linked to watching and sharing, such as a concrete, repeatable claim, a clear payoff, or clean audio.
- **What holds it back:** the weaknesses most likely to lose viewers early, taken from the lowest-scoring dimensions.
- **Most shareable line:** the single sentence most likely to be quoted or used as a headline, with its timestamp. Often the best candidate for the opening frame.

All three come from the same judge call and the measurements, with no extra model call. The section never shows a predicted view count or virality score. Nothing in this dataset could validate one, and the page says so in a one-line caveat.

A queued, processing or failed analysis renders a short page with its status and failure reason instead.

### How "What to fix first" is ranked

Each dimension's **weighted gap** = weight × (10 − score). For example, a hook scoring 5 with a 25% weight costs 0.25 × 5 = **1.25 points** of the overall score. Fixes are listed by weighted gap, so the first fix is the one that could recover the most score. It's the most a fix could recover, not a promise that it will.

### No retention curve, by design

The report deliberately differs from Retensis and ClipAPI here: there's **no predicted retention curve**. Without retention data, a curve would look precise but be invented. Every claim on the page is backed by evidence a reader can check against the video. A timeline of measured events (speech, pauses, cuts, captions) is a planned next step ([section 23](#23-production-path)).

### How it's built (about 45 minutes)

- One Jinja2 template, inline CSS, **no JavaScript and no build step**.
- Soft colour palette with a dark-mode variant; web font with a system fallback.
- Works on mobile.
- If time runs short, the report is dropped and the JSON response stands on its own; the report goes to the deferred list.

## 21. Verification

| Check            | How                                                                                                | Result |
| ---------------- | -------------------------------------------------------------------------------------------------- | ------ |
| Unit tests       | Scoring math, cap rule, weight rescaling, validation                                               | `TBD`  |
| API tests        | Each documented error code                                                                         | `TBD`  |
| Crash durability | `scripts/restart_check.sh`: upload, `kill -9`, restart, `GET`                                      | `TBD`  |
| Repeatability    | `scripts/repeatability.py`: 5 fresh runs on 2 clips, report spread per dimension and verdict flips | `TBD`  |
| Dataset run      | `scripts/run_dataset.py`: all 30 clips, scores vs views normalised by account median               | `TBD`  |

## 22. Cost

Target: under $1 per video. Transcription is local, so cost is mainly the model call. Actual cost per analysis is taken from the gateway's per-generation usage data and stored with each result.

Measured average: `TBD`

## 23. Production path

### Deferred from this version

Cut to keep the build inside the 12-hour cap with a real buffer. Each one is a deliberate choice, not an oversight.

| Deferred                                               | What this version does instead                                      | What it would add                                                             | Rough effort |
| ------------------------------------------------------ | ------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ------------ |
| Specialist judges (one call per dimension)             | One structured call with a section and anchored scale per dimension | Less anchoring between dimensions, failures isolated per dimension            | ~1 h         |
| OCR (RapidOCR) and caption sync                        | The judge reads captions from 4–6 sampled frames                    | Measured caption coverage and sync, so on-screen text becomes mostly measured | ~0.5–1 h     |
| Speech / music / noise classifier (inaSpeechSegmenter) | Assume speech; flag `low_speech` clips with low confidence          | A proper non-speech scoring path for music clips and slideshows               | ~0.5 h       |
| Full verify rule set                                   | Three rules (late start, boundary cut, missing evidence)            | More model claims checked against measurements                                | ~0.5 h       |
| Report timeline                                        | Evidence and fixes with timestamps in text                          | A visual map of speech, pauses, cuts, captions and flagged moments            | ~0.5 h       |
| Pitch and energy variation (librosa)                   | Pacing from speech rate, pauses and cuts                            | A measured "energy" signal instead of leaving it implicit                     | ~0.5 h       |
| Comparison with ClipAPI and Retensis                   | Repeatability, account-normalised views and a manual spot check     | An external sanity check on hook and audio scores                             | ~1 h         |
| Model selection across 3+ models                       | 2 models on 2 clips, one pinned                                     | A stronger basis for the model choice                                         | ~0.5 h       |

### Beyond this take-home

- **Workflow engine:** each pipeline stage maps to a Temporal activity or Trigger.dev task, which provides retries, timeouts and recovery for free.
- **Object storage** (S3 / GCS) for video files.
- **Separate workers** that scale with queue depth.
- **Learned weights** once hundreds of clips have retention or completion data.
- **Spanish support**, per-niche or per-client rubric tuning, and use case (a): scoring raw cuts before editing by skipping edit-dependent dimensions.

## 24. Reflection and time log

Reflection (~1 page): `TBD`

### Time budget

| Block                                                     | Planned hours |
| --------------------------------------------------------- | ------------- |
| Scaffold, Docker, config                                  | 1.0           |
| API, storage, durability, restart check                   | 1.5           |
| Signal extraction (transcript, cuts, loudness, frames)    | 1.5           |
| Judge call, verify rules, scoring                         | 1.5           |
| Model selection, dataset run, repeatability, light tuning | 1.0           |
| HTML report (no timeline)                                 | 0.75          |
| README and reflection                                     | 1.25          |
| **Planned work**                                          | **8.5**       |
| Buffer                                                    | 3.5           |
| **Cap**                                                   | **12.0**      |

The buffer is time for things going wrong, not extra scope. The report starts only once the core works; if the core runs late, the report is the first thing dropped.

| Date | Hours | Work |
| ---- | ----- | ---- |
|      |       |      |

Cap: 12 hours total.
