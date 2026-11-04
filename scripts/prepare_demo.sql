-- Run ONLY against a disposable copy of public, never the active workspace.
BEGIN;

CREATE TEMP TABLE demo_sources AS
SELECT id FROM sources
WHERE payload #>> '{item,metadata,id}' IN ('nist-csf-2.0', 'nist-sp800-53-rev5')
  AND location IN (
    'https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/CSF/v2.0/json/NIST_CSF_v2.0_catalog.json',
    'https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json'
  );

DO $$
BEGIN
    IF (SELECT count(*) FROM demo_sources) <> 2 THEN
        RAISE EXCEPTION 'Both reviewed NIST sources must exist';
    END IF;
END $$;

TRUNCATE authority_events, reconciliation_decisions, enrichment_findings,
    issue_events, knowledge_issues, solution_assessments,
    solution_architecture_controls, solution_architecture_revisions,
    solution_architectures, processing_tasks, processing_runs, pipeline_executions,
    pipeline_leases, semantic_replay_cache, parse_checkpoints, segment_results;

DELETE FROM relationships WHERE occurrence_id IN (
    SELECT o.id FROM occurrences o
    JOIN segments s ON s.id = o.segment_id
    JOIN source_revisions r ON r.id = s.revision_id
    WHERE r.source_id NOT IN (SELECT id FROM demo_sources)
);
DELETE FROM occurrences WHERE segment_id IN (
    SELECT s.id FROM segments s JOIN source_revisions r ON r.id = s.revision_id
    WHERE r.source_id NOT IN (SELECT id FROM demo_sources)
);
DELETE FROM relationships
WHERE from_id NOT IN (
    SELECT v.concept_id FROM concept_versions v JOIN occurrences o
    ON o.concept_version_id = v.id
) OR to_id NOT IN (
    SELECT v.concept_id FROM concept_versions v JOIN occurrences o
    ON o.concept_version_id = v.id
);
DELETE FROM concept_versions WHERE id NOT IN (SELECT concept_version_id FROM occurrences);
DELETE FROM concepts WHERE id NOT IN (SELECT concept_id FROM concept_versions);
DELETE FROM segments WHERE revision_id IN (
    SELECT id FROM source_revisions WHERE source_id NOT IN (SELECT id FROM demo_sources)
);
DELETE FROM publication_assessments WHERE revision_id IN (
    SELECT id FROM source_revisions WHERE source_id NOT IN (SELECT id FROM demo_sources)
);
DELETE FROM source_revisions WHERE source_id NOT IN (SELECT id FROM demo_sources);
DELETE FROM sources WHERE id NOT IN (SELECT id FROM demo_sources);

-- These selectors contain excerpts of excluded publications, not source evidence.
UPDATE sources SET payload = jsonb_set(payload, '{item,metadata,architecture_principles}', '[]');
UPDATE source_revisions SET payload =
    jsonb_set(payload, '{metadata_snapshot,architecture_principles}', '[]');

COMMIT;
