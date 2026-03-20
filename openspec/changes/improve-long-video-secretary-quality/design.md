## Context

The current long-video path already collects materially better evidence than the earlier page-only pipeline. `EvidenceExtractor` can combine platform metadata, subtitles, ASR, keyframes, and keyframe OCR, and `WorkflowProcessor` already distinguishes partial, refused, and blocked situations at a coarse level.

The main remaining quality problem happens after extraction:

- `EvidenceDrivenVideoSummarizer` builds chunk summaries, then depends on a separate video-specific final rendering path.
- When the final model path fails, `_fallback_summary_from_chunks()` degrades to timeline-heavy prose that is honest but does not clearly explain the video's meaning.
- `render_video_note_markdown()` expands timeline sections and finance blocks directly from the summary, so any noisy chunk wording leaks into user-visible output.
- Current quality checks mostly measure extraction coverage and outline retention, but do not enforce understanding quality or degradation-specific output contracts.

This change must improve long-video output without regressing the existing strengths:

- extraction evidence must stay evidence-grounded and inspectable in `/jobs/<id>`
- short/medium videos that already work must not become more verbose, more generic, or less faithful
- dry-run previews must remain representative of final output
- existing `SummaryResult` and note-writing flows should remain backward-compatible during rollout

## Goals / Non-Goals

**Goals:**

- Produce clear understanding-first summaries for long videos instead of chunk dumps.
- Separate evidence synthesis from final prose rendering so long-video output is less sensitive to one fragile model hop.
- Make degradation states specific and user-visible: usable partial, model unavailable, capture blocked, refusal.
- Keep long-video notes, dry-run previews, and Telegram replies structurally aligned.
- Add regression validation for current long Bilibili and Xiaohongshu samples.

**Non-Goals:**

- Reworking the robot payload contract or `POST /ingest` entry format.
- Replacing current video extraction providers or solving all provider-side access failures.
- Implementing browser cleanup, hierarchical classification path, or general note-style changes for non-video inputs.
- Guaranteeing perfect full-length understanding for every long video; the goal is better reconstruction and better honesty, not fake completeness.

## Decisions

### 1. Introduce an internal `VideoUnderstandingPlan` between evidence synthesis and final rendering

The long-video path will build an internal structured plan before producing `SummaryResult` or note markdown.

The plan will capture normalized fields such as:

- `summary_kind` / `degradation_kind`
- `main_topic`
- `core_thesis`
- `section_map`
- `verified_facts`
- `supporting_evidence`
- `open_questions`
- `timeline_sections`
- `appendix_blocks`
- `evidence_basis`
- `report_quality_reasons`

Rationale:

- Current chunk summaries are too close to raw ASR phrasing.
- Rendering needs a stable contract that is smaller and more deterministic than raw chunk prose.
- The same plan can drive preview, saved note, and Telegram reply.

Alternatives considered:

- Prompt-tune the current final summary harder: rejected because it does not solve model-path fragility or fallback structure.
- Fix rendering only: rejected because renderer-only cleanup cannot reliably infer what is a verified fact versus a noisy chunk line.

### 2. Split long-video summarization into synthesis and rendering phases

Long-video summarization will become:

1. timed evidence -> chunk summaries / fact blocks
2. chunk summaries -> deduplicated `VideoUnderstandingPlan`
3. `VideoUnderstandingPlan` -> `SummaryResult` + note/reply rendering

The synthesis phase will normalize repeated points, trim raw ASR leakage, infer the video's main sections, and classify which facts belong to the primary understanding summary versus optional appendix/timeline detail.

Rationale:

- The current path collapses synthesis and rendering, so model failure causes a visible UX failure.
- A stable plan layer allows deterministic fallback behavior even when a prose model is unavailable.

Alternatives considered:

- Keep one-shot final JSON generation from chunk summaries: rejected because it remains all-or-nothing.

### 3. Add an explicit degradation taxonomy and map it to user-visible contracts

The system will distinguish at least these states:

- `complete`
- `usable_partial`
- `coverage_partial`
- `model_unavailable`
- `capture_blocked`
- `refused`

Each state will map to a different user-facing contract:

- `usable_partial`: show core thesis, verified value, and explicit unknowns
- `model_unavailable`: show bounded fallback brief, not a timeline dump
- `capture_blocked`: explicitly refuse body understanding
- `refused`: keep current refusal semantics

Rationale:

- The current single `partial` bucket mixes "we learned enough to decide" with "the summarizer broke".
- Operators need to tell extraction failure from synthesis failure in `/jobs/<id>` and validation reports.

Alternatives considered:

- Keep current `partial` plus warning strings: rejected because it is too easy to render the wrong note style from the same summary state.

### 4. Use a provider-agnostic final rendering fallback instead of a single video-only final model hop

The specialized long-video path may still use chunking and video-oriented prompts, but final summary generation must not depend on a single model family. If the video-specific final rendering path fails, the system should fall back to:

- deterministic `VideoUnderstandingPlan` rendering, and/or
- the project's primary summarizer model for final prose shaping

without dropping into a chunk dump.

Rationale:

- Recent long Bilibili runs show model-specific final rendering failure can dominate the final UX even when evidence collection succeeded.
- Reusing the project's primary summarizer path lowers divergence between video and non-video output quality expectations.

Alternatives considered:

- Only fix the current Gemini credentials/provider behavior: rejected because it addresses one outage mode but not the structural fallback problem.

### 5. Enforce an understanding-first summary contract and gate appendix-heavy blocks

Long-video rendering will enforce:

- topic/thesis summary first
- 3-6 semantic key points before appendix detail
- capped, selective timeline sections
- appendix-only finance tables unless evidence is explicit and stable
- no generic placeholder conclusions such as "已提取核心事实"
- no long raw ASR sentences in primary bullets

Rationale:

- The current direct video note body can overexpose timeline and finance appendix content before the user understands the video's actual thesis.
- A useful summary must frontload "what this video is saying" before auxiliary structure.

Alternatives considered:

- Keep current rendering and just shorten more lines: rejected because ordering is the main problem, not only length.

### 6. Add long-video report-quality regression checks, not just extraction checks

Validation will include fixtures for the current long Bilibili and long Xiaohongshu samples and assert:

- presence of topic/thesis-first summary sections
- degradation kind when relevant
- absence of generic pseudo-summary phrasing
- bounded raw-ASR leakage
- stable preview/note structure across dry-run and persisted paths

Rationale:

- Current validation already tracks extraction truth and outline coverage, but not the specific video-understanding output contract.

Alternatives considered:

- Rely on manual preview review: rejected because this problem has already survived multiple extraction fixes.

## Risks / Trade-offs

- [Risk] More structure may oversimplify niche long videos. → Mitigation: keep selective timeline and evidence appendix available behind the main summary.
- [Risk] Introducing `VideoUnderstandingPlan` may duplicate existing `SummaryResult` semantics. → Mitigation: keep the plan internal and map it cleanly into existing result objects during rollout.
- [Risk] Provider-agnostic fallback may increase latency or cost. → Mitigation: reuse cacheable synthesized plans and only invoke additional prose shaping on degraded final-render cases.
- [Risk] Finance-specific videos may lose detail if appendix gating is too aggressive. → Mitigation: gate only the main summary; preserve finance appendix when evidence confidence is explicit and sufficient.
- [Risk] Short strong samples could regress if the new contract is applied too broadly. → Mitigation: scope the new path to long-video or degraded long-video cases first and validate against existing strong XHS/Bilibili samples.

## Migration Plan

1. Introduce the internal `VideoUnderstandingPlan` and degradation taxonomy alongside current `SummaryResult` production.
2. Switch long-video preview generation to the new contract first so dry-run validation exposes issues before saved-note rollout.
3. Update persisted note and Telegram rendering to consume the new plan for long-video cases.
4. Keep the old chunk-dump fallback path as an internal rollback option until regression coverage is stable.
5. Remove or narrow old fallback-specific rendering branches once the new regression suite passes on the known long-video samples.

## Open Questions

- Should finance matrix rendering remain available in Telegram replies at all, or only in Obsidian appendix content?
- What exact threshold should qualify a long video for the new path: duration-based, outline-count-based, or both?
