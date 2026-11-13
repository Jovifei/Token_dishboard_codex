# Quiet UI rebuild · design brief

Baseline: main@f26dddbf027a64732e8ce98bf1f6ecc8daf3752f (2026-11-13 retrieval). Preserve latest billing/project identity/compressed-log and continuation functionality. Do not replace this baseline with an earlier delivered ZIP.

User goals: understand current context pressure, inspect cost and usage, hand off an old conversation to a new one, select personal directories without requiring Obsidian, and manage safe local configuration. Keep floating monitor and existing brand mark.

Direction: calm Apple-inspired desktop utility; a coherent navigation hierarchy, generous spacing, clear primary/secondary actions, consistent surface and typography tokens. No recessed effects, geometry changes, bounce, scale, or click ripple. Keyboard and pointer feedback must be visible without changing size or position. Preserve native keyboard activation, disabled states, focus, data provenance and write confirmations.

Implementation scope: rebuild actual Tk layout and control appearance, not an HTML-only visual mock. Preserve data semantics and native file dialogs. Verification must include ordinary/hover/pressed/focus/disabled states, rapid navigation, narrow layout, and the current full regression suite. Screenshots use isolated synthetic data; Windows runtime acceptance is separate.

Implementation: 0.12.0-ui-rc1. See docs/design/quiet/README.md and docs/verification/0.12.0-ui-rc1/ for actual Tk screenshots, control-state contracts, code mapping and verification boundaries. Windows release acceptance remains separate.
