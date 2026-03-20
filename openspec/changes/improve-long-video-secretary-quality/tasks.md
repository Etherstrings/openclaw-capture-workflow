## 1. Understanding Contract

- [ ] 1.1 Define the internal `VideoUnderstandingPlan` schema and degradation taxonomy for long-video runs
- [ ] 1.2 Map `VideoUnderstandingPlan` back into `SummaryResult` and `/jobs/<id>` metadata without breaking existing consumers
- [ ] 1.3 Add report-quality metadata for long-video understanding quality and degradation reasons

## 2. Long-Video Synthesis

- [ ] 2.1 Refactor the long-video summarizer to merge chunk summaries into deduplicated fact blocks instead of timeline-heavy prose
- [ ] 2.2 Add a provider-agnostic final rendering fallback so model-specific final-render failures do not degrade into chunk dumps
- [ ] 2.3 Gate finance appendix generation and timeline expansion on evidence confidence and understanding-first summary rules

## 3. Rendering and UX

- [ ] 3.1 Update long-video preview, Obsidian note, and Telegram reply rendering to use the new understanding-first contract
- [ ] 3.2 Add distinct user-visible output paths for `usable_partial`, `model_unavailable`, `capture_blocked`, and `refused`
- [ ] 3.3 Ensure dry-run previews and persisted long-video notes stay structurally aligned

## 4. Regression Validation

- [ ] 4.1 Add regression fixtures and tests for the current long Bilibili and Xiaohongshu samples
- [ ] 4.2 Add assertions for topic/thesis-first structure, degradation-kind correctness, and bounded raw-ASR leakage
- [ ] 4.3 Re-run targeted long-video previews and document before/after quality evidence
