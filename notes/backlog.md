# Backlog

## Completed during Sprint 2

- Refactored Activity Monitor state labels into prose context hints before L1 sees them.
  Raw labels like `scattered`, `grinding`, `idle_loop`, and `normal` should not be present in `<runtime_context>`. Use a mapper similar to `mood_to_prompt_fragment`, for example `scattered -> he keeps switching windows. no settled focus.`

- Added per-message memory records for new conversations.
  New writes store `role=user` and `role=assistant` records under the same `turn_id`, so assistant-side prompt-safety filtering is cleaner. Existing local memories may still be legacy until migrated/cleaned.

- Added a memory inspection utility.
  Use `python scripts/memory/inspect_memory.py --limit 10` for a read-only report, `--migrate` to apply the current memory schema, and `--downgrade-unsafe` only when deliberately lowering unsafe legacy records.

## Sprint 2 candidates

- Keep post-tag L1 polish out of Sprint 1.5.
  New voice regressions found after `v0.1-sprint1.5` should be recorded here unless they are severe enough to block Sprint 2.

- Retrieval ranking can reinforce a repeated response motif instead of the requested fact.
  The restart prompt contained `Naevor`, but both recent and relevant memory also contained `persistent string` assistant turns. The model repeated that motif instead of stating the name, so this can worsen as similar turns accumulate. Revisit ranking or turn-side selection after Sprint 2; persistence itself is working.

## Sprint 3 deferred

- Add VRAM usage and GPU temperature to the Decoding/Model Console telemetry.
  Keep these fields unavailable in Sprint 2 until a real GPU metrics provider is wired; do not display synthetic values.
