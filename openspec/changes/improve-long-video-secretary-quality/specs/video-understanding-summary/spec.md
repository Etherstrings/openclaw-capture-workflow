## ADDED Requirements

### Requirement: Long video summaries SHALL reconstruct the video's main meaning before detail
The system SHALL present long-video results with a meaning-first summary that states what the video is about, its main thesis, and the most important verified points before optional timeline or appendix sections.

#### Scenario: Long video with multi-section evidence
- **WHEN** a long video run reaches summarization with sufficient speech evidence and multiple timeline sections
- **THEN** the generated preview and persisted note explain the video's main topic and key conclusions instead of only listing timeline headings or chunk summaries

#### Scenario: Noisy repeated chunk evidence
- **WHEN** chunk summaries repeat the same claim or contain long raw ASR phrasing
- **THEN** the final summary rewrites them into deduplicated semantic points and SHALL NOT mirror long raw transcript fragments

### Requirement: Long video summaries SHALL preserve content structure without collapsing into a timeline dump
The system SHALL preserve the video's meaningful section structure and SHALL only include timeline sections, finance tables, or other appendices when they help explain verified content rather than replace it.

#### Scenario: Weak finance detail
- **WHEN** a finance-flavored long video has noisy or partial holding details
- **THEN** the summary omits the finance matrix and keeps only verified high-confidence judgments about the video's content

#### Scenario: Strong finance detail
- **WHEN** a long video explicitly supports concrete holdings, sectors, thesis details, or risk statements
- **THEN** the output may include a finance appendix without replacing the main content summary

### Requirement: Dry-run previews and final notes SHALL use the same long-video understanding contract
The system SHALL keep dry-run previews, persisted notes, and Telegram replies structurally aligned so preview quality represents the final long-video contract.

#### Scenario: Dry-run long-video preview
- **WHEN** a long-video request is processed in dry-run mode
- **THEN** the preview uses the same section order and degradation messaging contract as the final note path for the same summary state
