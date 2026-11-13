# Quiet UI 0.12.0-ui-rc1 · review entry

Implemented on latest main@f26dddbf027a64732e8ce98bf1f6ecc8daf3752f. Full implementation and synthetic screenshots committed in c81e537e87049917151af97e9eee9461133892a2; source/content hashes were checked before application. The temporary delivery manifests and integration workflow have been removed.

## Evidence

Independent GitHub integration run [43346377188](https://github.com/Jovifei/Token_dishboard_codex/actions/runs/43346377188) compiled and tested the actual modified application, generated 11 real Tk screenshots, and committed the checked source and evidence. Result: **456 tests, 455 passed, 0 failures, 0 errors, 1 skipped** (Windows-specific test). The remote environment installed the existing Zstandard runtime requirement; its 13 decoder-dependent cases ran rather than skipping as they did locally.

- [Design, interaction contract and screenshots](../../docs/design/quiet/README.md)
- [Exact remote test receipt](../../docs/verification/0.12.0-ui-rc1/github-tests.json)
- [Remote test log](../../docs/verification/0.12.0-ui-rc1/github-test-log.txt)
- [Source-integrity receipt](../../docs/verification/0.12.0-ui-rc1/source-integrity.json)

## Scope

The native Tk layout and controls are rebuilt around current conversation, usage/cost, handoff, and preferences. New controls have no click animation, bevel, geometry change or rebound. Mouse/keyboard invocation, drag-out cancellation and disabled behavior have explicit regression tests. Core billing/indexing, project identity, compressed logs, handoff validation and config write safeguards remain in the latest baseline.

This candidate is not a new Windows EXE release. Windows real DPI, taskbar/native dialog/assistive-technology and live Codex workflow acceptance remain separate. Do not merge or replace the previously published package merely because the synthetic Linux suite passes.
