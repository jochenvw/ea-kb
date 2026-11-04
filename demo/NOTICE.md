# Demo data attribution and exclusions

The snapshot contains existing, precomputed interpretations of these two sources from the
US National Institute of Standards and Technology (NIST):

- [Cybersecurity Framework 2.0 OSCAL catalog](https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/CSF/v2.0/json/NIST_CSF_v2.0_catalog.json).
- [SP 800-53 Revision 5 OSCAL catalog](https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json).

The upstream [OSCAL content license](https://github.com/usnistgov/oscal-content/blob/main/LICENSE.md)
places the work in the US public domain and waives copyright worldwide through
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
The upstream license text is retained in `UPSTREAM-LICENSE.txt`; originals include their own notices.
No NIST endorsement is implied. Source URLs, retrieval timestamps, original hashes, stable IDs,
segments and exact evidence are retained. Upstream `main` can change; the bundled bytes are pinned.

This is a filtered export, not a backup of the complete development workspace.
It excludes corporate annual reports and strategy pages, ISO material, Microsoft Learn pages,
other publications, organization curation, solution designs, governance decisions, model replay,
worker checkpoints, run history, errors and leases. Source configuration's architecture-principle
selectors are removed because they refer to excluded publications. No evidence text is rewritten.
Candidates remain candidates, unknown classification stays null, and nothing is approved.
The demo therefore does not include corporate-strategy or cross-source reconciliation examples.
