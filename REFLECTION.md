# Reflection

The final explanation of the system and what I learned building it. The full design, with sources and measurements, is in [docs/README.md](docs/README.md).

## TL;DR

**What it does.** Upload an MP4 with optional metadata and get an ID back right away. A background worker returns **post, improve or skip**, an overall score from 0 to 10, and six areas, each with evidence and one fix. The areas are hook, standalone completeness, pacing, message clarity, audio and captions, in the order a viewer experiences a clip.

**How it works: measure, judge, verify.**

- **Measure.** ffmpeg, faster-whisper and PySceneDetect measure what code can measure: transcript, pauses, cuts, loudness and frames. Pacing and audio are scored from those measurements alone.
- **Judge.** One model call (`google/gemini-2.5-flash` through Vercel AI Gateway) scores the four areas that need judgment.
- **Verify.** Code checks the model's quotes and timestamps against the measurements and overrides them when they disagree.
- **Score.** Code, not the model, computes the weighted score and the verdict.

Every state change is committed to Postgres, so completed results survive a `kill -9`.

**Main decisions.**

- **A deterministic core with one model call.** This keeps cost low, makes the result repeatable and lets code check the model's claims.
- **Versions in the cache key.** The duplicate key is the file's SHA-256 plus the prompt, rubric and pipeline versions, enforced by a unique index. The same clip is never paid for twice, and a change in logic never serves a stale result.
- **Local Gemma first, then a gateway model.** I developed without spending credits, then picked the gateway model for cost and quality.

**Results.**

- **Cost and time.** About **$0.007 and about 30 s per clip** (19 runs), far under the $1 target.
- **Repeatability.** 5 fresh runs each of two clips gave the same verdict and overall score 10 times out of 10.
- **Tests and durability.** 191 tests pass, and the kill -9 restart check passes.

**What I learned.**

- **The model needs one time format.** The first results looked plausible but were wrong. The model read "0:23" as 0.23 s and mistimed the hook. One time format in the prompt, times taken from the transcript, verify rules and denser hook frames fixed it.
- **Versioning is what made iteration safe.** I could compare results before and after each fix because every score records the logic that produced it.

**Limitations.**

- **Consistency is tested, usefulness is not.** Scores haven't been compared with real views or retention. The 30-clip evaluation wasn't run.
- **Narrow sample.** I tested five English, speech-led clips.
- **Simple worker.** One worker, no retries, and no resume after a crash.
- **One model call.** The areas can influence each other.
- **Exact-bytes duplicate check.** A re-encoded clip is analysed and paid for again.

**Next.**

1. Run the 30-clip evaluation and calibrate the weights and thresholds on it.
2. Run repeatability on clips near a verdict threshold.
3. Add OCR for captions.
4. Add leases and bounded retries.
5. Check resolution at upload, before paying for an analysis.

The sections below explain each point in detail.

## How it works

You upload an MP4 with optional metadata, and the service returns an ID right away. A worker thread in the same process then analyses the clip with a pattern I call **measure, judge, verify**:

1. **Measure.** ffmpeg, Whisper and PySceneDetect extract the transcript with word timestamps, pauses, cuts, loudness and frames. Code turns them into deterministic signals.
2. **Judge.** One model call (`google/gemini-2.5-flash` through Vercel AI Gateway) scores the four areas that need judgment, grounded on the transcript, the signals and 16 frames.
3. **Verify and score.** Code checks the model's claims (quotes, timestamps, a clip starting mid-sentence) against the measurements and overrides them when they disagree. Then code computes the weighted score and the verdict (post, improve or skip). The model never picks the verdict.

Every state change is committed to Postgres. Completed analyses survive a crash, and any analysis that was running is reported as `failed / interrupted_by_restart`.

## How I approached it

My first step was to understand the system and the business behaviour behind the task. In any system or task you're assigned, you may or may not own the upstream services (the ones that call your service) and the downstream ones (the ones that use your output). So it's important to cut the pie as small as you can to focus on the problem you need to solve, while still exploring the moving parts that shape the system's behaviour.

For me, that meant researching the products the team shared: what features this kind of service usually has, how they present their results, and what goal they have in common. That gave me a starting point to dig into how I could solve the same problem from a slightly different perspective. It also answered my questions about where in the overall workflow this service sits. This take-home belongs to a bigger system that can consume my service, so beyond the file extension I needed to understand what kind of videos we would receive (language, format, whether they were already edited by a tool or an editor, and even the main content). With that in mind, I could work out how to measure a video's virality or likely success.

## The rubric

The next step was to define the rubric, based on the common guidelines we can draw from how social media content reaches us as users. My first idea was to split it into video, speech and image. I also thought about adding categories such as "video quality" and "audio quality". But looking at how Retensis behaves, and going deeper into the research, gave me clearer ideas. I ended up with six areas: hook, standalone completeness, pacing, message clarity, audio and captions. They follow the order in which a viewer experiences a clip (stop, understand, stay, hear and read), which makes them easier to test against retention data once we have it. Each area has a weight that can change in future versions of the pipeline, and more areas can be added.

While I was still researching, I received the sample data and noticed the content followed a very specific trend. That gave me a more ambitious idea. We could explore the metrics of the accounts we manage as a company and extract information about them and their content (for example sports, music, history or comedy). Then we could evaluate each clip against that dynamic behaviour instead of static rules. The idea was to:

- keep 70% of the score for the rubric described in the documentation
- give the other 30% to a custom classification, based on the account, the type of content it publishes, and trends from any source we can consume

That would give us a dataset to evaluate success in a way customised to each account, and to check how these experiments behave on retention on real social media. I discarded it before implementing anything, and built the service on the general rubric only. It has a trade-off worth keeping in mind if it comes back: a score tuned per account is less comparable across accounts, and the 30% would need real performance data to validate it before we trust it.

## Pitfalls and what I learned

- **What I tested and what I didn't.** I tested **consistency**: 5 fresh runs each of two clips gave the same verdict and overall score in 10 of 10 runs, and the measured areas were identical every time. I haven't tested **usefulness**: the scores haven't been compared with real views or retention yet, so whether a high score means a better clip is still my own judgment, not a finding. The choice of model also matters a lot.
- **The first results were wrong.** The documentation helped me start the service with a clear first approach. But in the first development cycle, the results of extracting the video and formatting it for the model were wrong and hard to interpret. They looked like they made sense, but after watching the clip they were a little off about when the hook happened, and they missed important content in the video. What fixed it:
  - **One time format in the prompt.** The transcript now reaches the model one sentence per line, as `[23.0s]` (prompt v2). Before, the model read "0:23" and returned 0.23.
  - **Times from the transcript, not the model.** The shareable line's time is taken from the word-level transcript.
  - **Verify rules.** Every quote and timestamp the model cites is checked against the transcript: a quote that isn't there lowers confidence, and a wrong time is corrected.
  - **More frames where the hook is.** Frames are taken at 0.2 s, 1.5 s and 2.8 s for the hook, then spread over the rest of the clip.
- **Re-processing.** Using the SHA-256 in the duplicate check worked well. The duplicate key combines the file's hash, the metadata the model sees (`title`, `platform`), the settings that affect the result, and the prompt, rubric and pipeline versions. So the same file is never paid for twice, even when two uploads arrive at the same time: a unique index in Postgres enforces it, not app code. Changing the title starts a new analysis on purpose, because the model reads it. But a clip engine rarely produces the exact same bytes twice. A re-export, a re-encode or a 0.1 s trim gives a new hash, so the same clip is analysed and paid for again. A real system would need to recognise the same _content_, for example with a perceptual video hash or a transcript fingerprint, or with a stable ID from the upstream step that produced the clip.
- **The video format.** At the very beginning, the format of the video wasn't part of my considerations. In my opinion it's one of the most critical things, along with the duration. For the duration, a clip over 4 minutes is rejected at upload (422 `too_long`), and I think I overthought the rest: clips under that limit only get a warning about the optimal length, up to 3 minutes. Resolution and aspect ratio are reported next to the score (for example, 9:16 but below 1080×1920), but they don't stop the pipeline and don't change the score. I think the resolution is a really good metric, so my next step would be to check it at upload and reject or penalise clips before paying for the analysis.
- **Local models first, then model choice.** Testing locally with Gemma through Ollama was a good decision. It made testing easier with a model that was good enough for the results. It was confusing to think about the possible impact of changing models on the deliverable. In the end I decided to expose a way to use different models from the gateway, because that's the decision I would take in a real development scenario: test the product under different conditions to find the balance between analysis quality and operating cost.
- **Versioning the pipeline.** From previous experience, versioning pipelines turned out to be a common decision, and once again it helped me iterate on the product and think about possible outcomes. Today the versions do two things:
  - They're part of the duplicate key, so after a change the same clip is analysed again, instead of returning a result made by the old logic.
  - They're stored in each result, so every score says which logic produced it. That's how I compared the results before and after a fix.

  They don't protect analyses that are already running: a deploy restarts the process, and those analyses are marked `interrupted_by_restart`. In a real scenario I would combine versioning with leases (next steps) to support hot deployments without breaking work in progress.

In general, I learned a lot about using tools I had used before for more traditional data processing on this kind of media processing, which was fairly new to me. I also learned new ones: faster-whisper for transcripts with word timestamps, PySceneDetect for cuts, and using a language model as a judge.

## Known limitations

- Tested on five clips, English only, and speech-led clips only.
- The measured signals (transcript, cuts, loudness) weren't checked by hand against the video.
- No retention data, so "a strong hook keeps viewers watching" is an assumption, not a finding.
- One worker, an in-memory queue, no retries and no resume after a crash.
- The single model call lets areas influence each other: in one repeatability run the hook and on-screen text moved together by one step.
- A clip near a verdict threshold (5.0 or 7.0) could flip on a one-step change. Neither clip I tested was near one.

## What I would do next

1. Run the 30-clip evaluation, and calibrate the weights and thresholds on it.
2. Repeat the repeatability experiment on clips near a threshold, and with specialist judges.
3. Add OCR to measure captions instead of asking the model.
4. Replace the startup sweep with leases (`SELECT … FOR UPDATE SKIP LOCKED`), and add bounded retries for transient model errors.
5. Add an event or workflow manager to split some of the tasks. It could be an external service such as Camunda, Lambda functions, message brokers, or any workflow tool that fits the company's stack.

## AI coding tools

I used **Claude Code** throughout:

- to explore the reference products and the research on using models as evaluators
- to write and refactor code and tests
- to review pull requests against the brief
- to draft and tighten the docs

I set the direction and the scope: the rubric, measure-judge-verify, Postgres, what to defer. I reviewed every change, and I checked claims against the code or a real run before keeping them. For example, I recomputed the cost by hand and re-ran the restart check. Local models through Ollama were only used during development, for free iteration.
