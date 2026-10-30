# NIST CSF 2.0 extraction references

- The three supplied reference JSON files and prediction sample are imported without rewriting.
- `source-excerpts.json` contains exact whitespace-normalized passages audited against the retained
  NIST CSWP 29 PDF; originals remain ignored under `data\test-corpus`.
- `archive-provenance.json` records the supplied archive identity and integration decisions.
- Native taxonomy is 6 Functions / 22 Categories / 106 Subcategories, not additional OWL vocabulary.
- The 25 class mappings are provisional; source-verified location corrections remain separate.
- The 14 authored negative claims are challenges, not source quotations; absent decisions are NOT RUN.
- Strategic requirement candidates remain separate from adoption. The supplied blanket prohibition
  and the earlier PR.DS-01 conceptual/technical disagreement are reported, not silently harmonized.
- Run `uv run ekb eval nist`; use `--excerpts-only` for offline replay without the original PDF.
- `--live` opts into a configured model; `--predictions` scores an explicit external export.
- See `docs\evaluation.md`; example predictions are not model benchmarks or qualification.
