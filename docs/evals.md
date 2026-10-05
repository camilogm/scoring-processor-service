# Evals: keeping results trustworthy after a change

The scorer is an AI tool. Part of its output comes from a model and a prompt, and a small change can move every score without a single test failing. Unit tests prove the code does what it says. They can't prove the results are still good. This document describes how we check the results themselves before a change ships.

Each item is marked **exists** or **planned**, so the gap is visible.

## What can change a result

Four things change what the service returns. The first three are versioned, stored in every result's `provenance` and part of the cache key, so an old result is never served for new logic (**exists**).

| Change | Version to bump | Examples |
| --- | --- | --- |
| Prompt | `PROMPT_VERSION` in `app/pipeline/judge.py` | wording, examples, output schema |
| Rubric | `version` in `app/config/rubric.yaml` | weights, verdict thresholds, caps, tolerances |
| Pipeline | `PIPELINE_VERSION` in `app/settings.py` | extraction, signals, verify rules, measured scoring |
| Model | the model id, already in the cache key | another gateway model, a new provider version |

A version bump is the trigger for running evals. If none of these changed, the existing tests are enough.

## The eval suite

| Layer | What it answers | Status | Cost per run |
| --- | --- | --- | --- |
| 1. Unit and contract tests | Does the code do what it says? Rules, scoring maths, response shape | **exists**: `make test`, 191 tests | free |
| 2. Golden-set regression | Which results moved, and by how much? | **exists**: `scripts/run_dataset.py`, baseline of 4 October 2026 | ~$0.22 |
| 3. Repeatability | Does the same input give the same verdict? | **exists**: `scripts/repeatability.py` | ~$0.007 per run |
| 4. Bad cuts (perturbation) | Does the score drop when we break a clip on purpose? | planned | ~$1 |
| 5. Invariance | Does the score hold when nothing meaningful changed? | planned | ~$0.50 |
| 6. Labelled rules | Are the verify rules right (precision and misses)? | planned: needs one hand-labelling pass | free after labelling |
| 7. Human pairs | Does our ranking of two cuts match an editor's? | planned, periodic | an editor's time |

### 2. Golden-set regression

The baseline is the 30 dataset clips scored on prompt v2, rubric v2 and pipeline 0.3.1 ([dataset-run.md](dataset-run.md)). After a change, run the same clips with `--fresh` and compare per clip: verdict, overall score, and each dimension.

The point is not that nothing moves. A change is supposed to move something. The point is that **every move is explained**. Each verdict flip is read and labelled as intended (the change was meant to catch this) or a regression.

### 4. Bad cuts

Make broken versions of each clip with ffmpeg, so we know what the right answer is:

| Change | Expected effect |
| --- | --- |
| Start 3 s late, mid-sentence | boundary cap fires; completeness and hook drop |
| End mid-sentence | boundary cap fires |
| Remove the first 5 s | hook drops |
| 2 s of silence at the start | late-start rule fires; hook drops |
| Background noise, or −15 dB | audio drops |

The golden set contains only clips that were already posted, so it can't show whether the service tells a good cut from a bad one. This layer can.

A variant of the same idea also checks the fixes: apply the suggested fix (for example, "start at 0:06"), re-score, and the score should go up.

### 5. Invariance

Re-encode at another bitrate, export at 720p vs 1080p, rename the file, rephrase the title. None of these should change the verdict. The title matters most to check, because the model reads it: C27 scored 9.4 without a title and 8.2 with one.

### 6. Labelled rules

Label each golden clip once by hand: does it really start or end mid-sentence? Is each flagged quote really missing from what was said or shown? Then every run reports precision and misses for each verify rule. Those rules override the model, so a wrong rule is worse than a wrong model.

## The gate: what must pass before a change ships

| Check | Pass criteria |
| --- | --- |
| Unit tests | all pass |
| Failures on the golden set | 0 (no `failed` analyses) |
| Verdict flips vs baseline | every flip explained in the PR as intended; an unexplained flip blocks |
| Score movement | median absolute change per dimension reported; a dimension that moved more than 1 point needs a reason |
| Repeatability | 5 runs of the clips nearest a verdict threshold: no verdict flips |
| Bad cuts (when it exists) | expected drop in ≥ 90% of variants, and no regression vs the last run |
| Invariance (when it exists) | same verdict, score within ±0.5 |
| Rules (when labelled) | no drop in precision for the boundary and quote rules |
| Cost and time | mean cost per clip and median processing time reported, under the $1 target |

Which checks to run depends on what changed:

| Change | Unit | Golden set | Repeatability | Bad cuts | Invariance | Rules |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Prompt | ✓ | ✓ | ✓ | ✓ | ✓ | |
| Model | ✓ | ✓ | ✓ | ✓ | ✓ | |
| Rubric weights or thresholds | ✓ | ✓ | | | | |
| Verify rule | ✓ | ✓ | | ✓ | | ✓ |
| Extraction or signals | ✓ | ✓ | | ✓ | ✓ | ✓ |

A weight or threshold change is deterministic, so the golden set can be re-scored without new model calls by recomputing from the stored dimension scores. This isn't built yet.

## The process

1. Make the change on a branch and bump the right version.
2. Run the checks from the table above against a local stack or a staging app, with `--fresh` so the cache doesn't answer.
3. Put the comparison in the PR: verdict flips with an explanation for each, score movement per dimension, cost and time.
4. A reviewer reads the flips, not only the summary numbers.
5. After the merge and deploy, the new run becomes the baseline. Keep the old one, so any version can be compared with any other.

Baselines should be committed as scores only (clip id, versions, verdict, scores), without the dataset's view counts or media. The dataset isn't part of the repo. **Planned**: `run_dataset.py` writes the run to `var/dataset/`, which is ignored by git; a `--compare <baseline>` flag and a committed `evals/baselines/` folder are the missing pieces.

## After it ships: watching production

The gate covers known clips. Production also gets clips nobody tested, so these are watched over time (**planned**):

- **Verdict mix per account.** A sudden shift in the share of "post" verdicts with no version change means the inputs changed, or the gateway model changed underneath us.
- **Verify contradictions per analysis.** A rising rate of `quote_not_in_transcript` or `boundary_cut` means the model and the rules disagree more often. It's the earliest warning that something drifted.
- **Failures by `error_code`, cost per clip, processing time.**
- **Spot checks.** Read a few random reports every week, and add any clip that surprises us to the golden set, with a label saying what the right answer is.

## Known gaps

- The golden set has 30 clips from three English-language accounts, all already posted. It has no negative examples until the bad-cut layer exists.
- There is no retention data, so no eval says a high score means people keep watching. The human-pairs layer is the closest substitute until a creator shares watch-time data.
- The gate is a documented checklist, not a CI job: it calls a paid model, so it runs when a version is bumped, not on every push.
