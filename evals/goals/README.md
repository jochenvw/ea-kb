# Strategic-goal evaluation data

`shell-annual-2025.json` contains provisional source-grounded reference annotations, not verified
human gold. Its report PDF is an ignored local input under `data\test-corpus\public\corporate_strategy`.
Each excerpt records both the printed report page and physical PDF page; the PDF has front-matter
offsets and occasional reading-order artifacts.
The evaluator checks the PDF hash, page count and every excerpt on its cited physical page before
scoring. Keep the copyrighted report local and ignored; only its short attributed evidence anchors
belong in this dataset.

The primary/acceptable-alternative distinction preserves ambiguity around the vision, operational
competitiveness and Goal Zero. Negative examples include business-unit strategy, subordinate
targets, capabilities, historical results, requirements, descriptive facts and market context.
Annotations include the official source URL and SHA-256; no complete report text is stored here.
Update labels only with documented review; do not put expected answers into extraction prompts.

Run `uv run ekb eval goals --compare-strategies` to compare whole-document, section-aware and
hierarchical extraction. See `docs\evaluation.md` for metrics, model profiles and interpretation.
The default profile uses offline rules, not a language model; live calls require `--live`, an
explicit profile and an available configured deployment.
