# Bounded ingestion skills

- `labeling.md` labels exact segment-text spans; headings may ground unit scope but are not evidence.
- Organizational coverage is separately evidenced; mixed document layers do not imply company adoption.
- Framework outcomes need no imperative; native taxonomy is not a goal, ontology layer or adoption.
- `extraction.md` extracts candidates only within those labeled spans and scopes.
- Prompts are runtime assets; their content hashes independently invalidate skill replay.
- Both skills use typed domain inputs/outputs through `IngestionSkills`, with no tools or loops.
- Unknown layers stay unknown; architecture-domain labels do not become requirement candidates.
- Strategic-goal extraction distinguishes enterprise outcomes, unit goals, subordinate targets and non-goals;
  `goal_level` is internal metadata, not an OWL class.
- Test/evaluate each skill independently before selecting a cheaper model or editing a prompt.
- See `docs\evaluation.md` for split isolation, precision gates and live qualification limits.
