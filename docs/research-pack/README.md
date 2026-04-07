# Research Pack

This folder is a non-code handoff package for outside reviewers who can help evaluate edge, execution quality, and strategy viability without access to the private codebase.

Purpose:
- explain what the bot is trying to do
- show how decisions are made
- show what actually happened in paper trading
- surface open questions and failure modes
- invite critique on edge, not implementation details

How to use this pack:
1. Share the whole folder, or export it as a zip/pdf bundle.
2. Remove anything you do not want to disclose, such as exact wallet references or internal file names.
3. Ask reviewers to focus on:
   - whether the strategy can plausibly have edge
   - whether execution assumptions are realistic
   - whether market selection is wrong
   - what should be disabled, simplified, or specialized

Files:
- `01-strategy-overview.md`
- `02-feature-dictionary.md`
- `03-decision-trace-samples.md`
- `04-performance-summary.md`
- `05-open-questions.md`
- `06-architecture-summary.md`
- `07-review-request.md`

Current project status, in one line:
- the bot is mechanically functional, but not yet proven profitable; weather was actively losing, fast lifecycle bugs were recently fixed, and crypto edge remains unproven.
