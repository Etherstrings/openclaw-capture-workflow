## ADDED Requirements

### Requirement: Video degradation states SHALL be explicit and evidence-grounded
The system SHALL classify degraded video runs into explicit states instead of collapsing them into a generic partial result.

#### Scenario: Usable partial coverage
- **WHEN** the system extracts enough evidence to judge the main thesis but cannot cover the full outline or full timeline
- **THEN** it marks the result as usable partial and explicitly states what parts of the video's meaning are verified and what remains unverified

#### Scenario: Model unavailable after evidence collection
- **WHEN** final video rendering fails after evidence collection produced usable facts
- **THEN** it marks the result as model unavailable and returns a bounded fallback content summary instead of a chunk dump

#### Scenario: Capture blocked
- **WHEN** the system cannot obtain stable speech or page evidence because of platform access failure
- **THEN** it marks the result as capture blocked and MUST NOT claim understanding of the video body

### Requirement: Video degradation state SHALL be observable in job results and validation outputs
The system SHALL expose the degradation kind and report-quality reason so operators can distinguish extraction failure from synthesis failure.

#### Scenario: Inspect degraded job result
- **WHEN** a caller fetches `/jobs/<id>` for a degraded video run
- **THEN** the result metadata includes the degradation kind, the evidence tracks used, and the primary reason for degradation

#### Scenario: Review regression output
- **WHEN** long-video regression validation is executed
- **THEN** the generated report identifies whether a sample degraded because of coverage, model availability, blocked capture, or refusal
