# Dataset run: results and reflection

All 30 sample clips were scored on 4 October 2026, on the deployed service (`https://clip-scoring.fly.dev`). The model was `google/gemini-2.5-flash` with prompt v2, rubric v2 and pipeline 0.3.1. The service received only each clip's title, account, platform (`tiktok`) and sample id. Views were joined afterwards, and only to compare within an account (views ÷ that account's median from `dataset/account-context.csv`).

Reproduce it, or rebuild the tables from the saved responses:

```bash
CLIP_API_URL=https://clip-scoring.fly.dev CLIP_API_AUTH=user:password uv run python scripts/run_dataset.py
uv run python scripts/run_dataset.py --report-only      # var/dataset/report.md from var/dataset/raw/*.json
```

The script resumes and resubmits analyses lost to a machine restart. In this run none were lost.

## The run itself

| | |
| --- | --- |
| Completed | 30 / 30, no failures, no retries |
| Cost | $0.2162 in total; $0.0072 per clip on average, $0.0089 at most |
| Processing time | median 25 s, max 44 s per clip (the clips are 32 to 115 s long) |

The service holds up operationally. One run of the whole dataset costs about 20 cents and finishes in about 15 minutes on a single worker.

## Results

| Account | median views | post | improve | skip | mean score | capped | ρ(score, reach) | ρ without outliers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| American Power | 4006 | 8 | 2 | 0 | 8.8 | 2 | −0.02 | −0.47 |
| Robinson's Podcast | 491 | 7 | 3 | 0 | 8.1 | 2 | −0.18 | −0.36 |
| WSJ Opinion | 3090 | 7 | 2 | 1 | 7.5 | 3 | 0.07 | 0.50 |
| **All** | | **22** | **7** | **1** | **8.1** | **7** | | |

ρ is the Spearman rank correlation between the overall score and reach (views ÷ the account median), within one account. The last column drops the reach outliers: clips at 5× the median or more, or at a fifth of it or less. With n ≈ 10, ρ has to be beyond about ±0.63 to be significant at p < 0.05. **None of them is.**

| Clip | Account | Title | score | verdict | reach |
| --- | --- | --- | ---: | --- | ---: |
| C01 | American Power | Three priorities for NATO | 9.6 | post | 0.28× |
| C02 | American Power | What victory for Ukraine could mean | 10.0 | post | 0.32× |
| C03 | American Power | NATO fears and Ukraine | 9.6 | post | 0.51× |
| C04 | American Power | Climate policy beyond recycling | 6.0 | improve (capped) | 0.56× |
| C05 | American Power | The claim about hidden oil reserves | 6.0 | improve (capped) | 0.93× |
| C06 | American Power | Ukraine and renewable technology | 10.0 | post | 0.81× |
| C07 | American Power | US dependence on OPEC | 9.8 | post | 6.55× |
| C08 | American Power | The Donroe Doctrine comparison | 9.1 | post | 0.63× |
| C09 | American Power | Trump and global trade | 8.3 | post | 4.70× |
| C10 | American Power | Why the US needs Canada | 9.7 | post | 23.53× |
| C11 | WSJ Opinion | Montgomery on a Strait of Hormuz deal | 8.9 | post | 0.31× |
| C12 | WSJ Opinion | Swaim on university reform | 4.5 | skip (capped) | 0.28× |
| C13 | WSJ Opinion | Swaim on DSA candidates | 8.7 | post | 4.54× |
| C14 | WSJ Opinion | Paul Ryan on benefit cliffs | 6.0 | improve (capped) | 1.21× |
| C15 | WSJ Opinion | Paul Ryan on welfare and work | 9.2 | post | 2.78× |
| C16 | WSJ Opinion | Strassel on lawsuits against Trump | 7.2 | post | 10.05× |
| C17 | WSJ Opinion | Gigot on the administration's economic plan | 6.0 | improve (capped) | 6.61× |
| C18 | WSJ Opinion | Sternberg on Taiwan policy | 7.8 | post | 0.54× |
| C19 | WSJ Opinion | Rasmussen on Republican turnout | 8.9 | post | 2.27× |
| C20 | WSJ Opinion | Kaufman on Netanyahu's response to October 7 | 7.7 | post | 0.98× |
| C21 | Robinson's | Wolff on colonial narratives about Native Americans | 9.2 | post | 0.21× |
| C22 | Robinson's | Wolff on Marx and the state | 9.2 | post | 8.34× |
| C23 | Robinson's | Wolff on unpredictable US tariffs | 9.7 | post | 0.86× |
| C24 | Robinson's | Butler on belief in Trump's claims | 9.2 | post | 1.24× |
| C25 | Robinson's | Finkelstein on Israel's response to October 7 | 7.2 | post | 1.16× |
| C26 | Robinson's | Hanson explains the Central Valley Project | 6.0 | improve (capped) | 0.40× |
| C27 | Robinson's | Hedges on Clinton and the Democrats | 8.2 | post | 538.35× |
| C28 | Robinson's | Wolff on the US and Iran | 9.2 | post | 0.31× |
| C29 | Robinson's | Wolff on the Paris Metro and communists | 6.9 | improve | 1.12× |
| C30 | Robinson's | Wolff on Europe and migration | 6.0 | improve (capped) | 2.54× |

## What the results say

### 1. The score does not predict reach, and that is not the whole story

Within each account, the score and reach are not related (ρ between −0.18 and 0.07). The verdicts don't separate reach either. On WSJ, the median "improve" clip reached 3.9× the account median and the median "post" clip 2.3×. The dataset README warns this would happen: reach depends on the news cycle, the topic, the distribution and the age of the post. The outliers show it most clearly. C27 (Hedges on Clinton, 538×) and C10 (Why the US needs Canada, 24×) scored 8.2 and 9.7, both comfortably "post" but not exceptional. No craft rubric can see that a clip lands on that day's news, and this one shouldn't try.

The signal is better where it can be measured fairly: **clips cut from the same episode** share the speaker, audio, setting and usually the topic, so mostly the cut differs. Across the 6 groups there are 15 pairs. In 8 the higher score went with more reach, in 4 with less, and 3 were ties. When the score gap is large (3 points or more) the direction is right in **5 of 6 pairs**:

| Pair | Scores | Reach | |
| --- | --- | --- | --- |
| C12 vs C13 (Swaim) | 4.5 vs 8.7 | 0.28× vs 4.54× | agrees |
| C14 vs C15 (Paul Ryan) | 6.0 vs 9.2 | 1.21× vs 2.78× | agrees |
| C04 vs C06, C07 (energy block) | 6.0 vs 10.0, 9.8 | 0.56× vs 0.81×, 6.55× | agrees |
| C05 vs C07 | 6.0 vs 9.8 | 0.93× vs 6.55× | agrees |
| C05 vs C06 | 6.0 vs 10.0 | 0.93× vs 0.81× | disagrees |

That is still 6 pairs, and it isn't evidence. But it is the right test for what the service claims: given this source material, is this cut ready to post? It says what the next evaluation should look like (see *What's next*).

### 2. 22 of 30 clips are "post", which is what a set of posted clips should get

Professional teams cut and published every clip in this dataset, so it is a sample of clips that editors already chose to post. A high "post" rate is the expected answer, not a sign of a lenient judge. This dataset can't measure leniency, because it has no bad clips to compare against.

The other verdicts still mean something. "Improve" doesn't say a clip should not have been posted; it says the cut has a fixable defect. Six of the eight non-"post" verdicts are exactly 6.0, the score the boundary cap forces, and most of those defects are real when you read the clip (see point 3). Without the cap, 27 of 30 clips would be "post": C04, C05, C14, C17 and C26 have raw scores of 7.1 to 8.6.

What the data does show is that some dimensions don't tell these clips apart:

- **On American Power, hook and on-screen text are 10 on all ten clips.** That may be correct (the account's editing is very consistent), but a dimension that doesn't vary can't rank one clip against another, which is why its ρ is blank in the report.
- `clarity_payoff` averages 9.2 to 9.8 in every account.

So the open question is not whether the service says "post" too often, but whether it **tells a good cut from a bad cut of the same material**. Answering it needs negative examples, for example bad versions of these clips (see *What I would change*).

### 3. The boundary rule's "two agreeing signals" is really one signal

The boundary cap is meant to fire only when two signals agree: speech at 0:00 (measured), plus a continuing conjunction or the judge's `starts_mid_thought` flag. **On this dataset, all 30 clips have their first word at 0.0 s.** Editors cut professional short-form clips tight, so the measured half of the rule is always true. In practice, the cap fires whenever the judge says "mid-thought". The guarantee in `verify._boundary_cut` ("a good clip is never capped on one noisy signal") doesn't hold on real clips.

Most of the caps still look right when you read them: C12 opens with "taking away their funding.", C30 with "if they continue with this war.", C04 with "I want to add a little bit to what you said". C26 really does end on "You". **C17 is the doubtful one.** "Besant is now talking about…" is a complete sentence. The only issue is that the viewer may not know who Bessent is. That clip reached 6.6× its account median, more than every uncapped WSJ clip but C16. For the same reason the `late_start` rule never fired once: its signal doesn't vary either.

### 4. Quote verification still flags on-screen text: 18 false contradictions

The fix in pipeline 0.3.1 exempts the `on_screen_text` dimension. But the judge also quotes burned-in headlines as evidence for the **hook** (10 cases) and **clarity/payoff** (6 cases), as in C04 ("CLIMATE CHANGE IS NOT SOLVED WITH 'INDIVIDUAL RESPONSIBILITY'") and C05 ("OPEC HID 500 MILLION BARRELS…"). Those quotes aren't in the transcript because they are on screen, so the rule lowers the confidence of a correct observation. That is why most American Power hooks are "medium" confidence while scoring 10. The real fix is to check quotes against the OCR or frame text too, not only against the transcript, or to let the judge mark where a quote comes from (`said` or `shown`).

### 5. Audio quality mostly measures loudness, which platforms normalise

`audio_quality` comes mostly from integrated loudness against a −14 LUFS target. WSJ clips sit at −20 to −31 LUFS and average 6.3, against 8.7 for American Power. Yet C17, at −31 LUFS, the quietest clip in the set, reached 6.6×. TikTok and Instagram normalise loudness on playback, so a quiet master costs less than the rubric assumes. On American Power, audio has the strongest correlation with reach of any dimension (ρ = −0.68), and it is negative. With n = 10 that is almost certainly noise, but it is the opposite of what the weight assumes. Measuring speech intelligibility (noise floor, clipping, speech-to-music ratio) would describe audio better than distance from a loudness target.

## What I would change, in order

1. **Make the boundary cap need real evidence.** `speech_at_start` alone isn't a second signal in tight cuts. Require a lowercase opener, a continuing conjunction, or an unresolved pronoun or name in the first sentence, so the judge's flag alone can't cap a clip. Re-check C17.
2. **Verify quotes against on-screen text as well.** Run OCR on the sampled frames, or tag each quote as `said` or `shown`, so headline quotes stop lowering confidence on the hook and payoff.
3. **Test the service on bad cuts of the same clips.** Use ffmpeg to make controlled versions of each clip: one starting 3 s late in the middle of a sentence, one ending mid-sentence, and one with the first 5 s (the hook) cut off. The score should drop against the original every time; where it doesn't, the dimension isn't seeing the defect. The cost is about $0.65 for 30 clips × 3 versions. Only if this shows the judged dimensions missing defects should we calibrate them against anchored reference clips in the prompt.
4. **Score audio on intelligibility, not loudness.** Keep loudness as a fix ("normalise to −14 LUFS") but take it out of the score.
5. **Evaluate inside groups.** The fair test is pairwise: among clips from the same source and account, does the higher-scored cut do better? Collect more groups (more cuts per episode, and retention data if creators share it) before tuning weights. Reach alone, across topics, will never validate a craft rubric.

Each of these changes the output, so each needs a version bump (rubric, prompt or pipeline). Then re-run `scripts/run_dataset.py` and compare it with this baseline.

## Caveats

- Ten clips per account. Every correlation here describes this sample; none of them proves anything.
- The view counts are cumulative snapshots at different post ages (9 to 51 days), not a fixed window.
- One run per clip. Earlier repeatability runs (5 each on C10 and C30) didn't flip a verdict, but C27 scored 9.4 on pipeline 0.3.0 with no metadata, against 8.2 here with title and account. The title is part of the prompt, so metadata can move the score.
