# Analyst prompts

Versioned prompt set for the analyst graph (see `docs/ANALYST.md`). One file per step:
`analyst_system_v1.md`, `analyst_plan_v1.md`, `analyst_gather_v1.md`, `analyst_draft_v1.md`,
`analyst_critique_v1.md`. `service.load_prompts()` reads them and stores a sha256 of the whole set in
`intel.analyses.prompt_hash`, so a prompt change is a visible event in the history. Placeholders use
`str.format`; keep literal braces out of the text.
