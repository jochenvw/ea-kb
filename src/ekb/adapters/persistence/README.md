# Knowledge persistence adapters

`postgres` is the implemented backend and owns ORM representation, repositories and units of work.
Other layers receive domain values through `src\ekb\ports\__init__.py`, never ORM rows.

Future backends must preserve explicit commits, grounding, history, claims and replay semantics.
Do not introduce a second backend merely as an extensibility example.
See `postgres\README.md`, `docs\plugins.md` and `tests\contract\test_repositories.py`.
