export type Scope = Record<string, string | null>;

export interface SemanticVersion {
  major: number;
  minor: number;
  patch: number;
  prerelease: string[];
  build: string[];
}

export interface Publication {
  semantic_version: SemanticVersion | null;
  publisher_version: string | null;
  publication_status: string;
}

export interface Source {
  item: {
    id: string;
    name: string;
    location: string;
    format: string;
    connector: string;
    metadata: Record<string, unknown>;
  };
  status: string;
  checked_at: string;
  revision_id: string | null;
  error: string | null;
  publication: Publication;
}

export interface Version {
  id: string;
  semantic_version: SemanticVersion | null;
  candidate: {
    kind: string;
    statement: string;
    modality: string;
    confidence: number;
    uncertainty: string | null;
    scope: Scope;
  };
  publication: Publication;
  recorded_at: string;
}

export interface Concept {
  id: string;
  versions: Version[];
  occurrences: { id: string; excerpt: string; start: number; end: number }[];
  authority_states: {
    concept_version_id: string;
    governance_status: string;
    canonical_status: string;
    scope: Scope;
    effective_from: string | null;
    effective_until: string | null;
  }[];
  authority_events: unknown[];
  relationships: {
    from_concept_id: string;
    to_concept_id: string;
    kind: string;
    status: string;
  }[];
}

export interface Provenance {
  occurrence: {
    id: string;
    concept_version_id: string;
    excerpt: string;
    start: number;
    end: number;
    extraction_metadata: Record<string, unknown>;
  };
  segment: {
    id: string;
    heading: string | null;
    location: Record<string, unknown>;
  };
  revision: {
    id: string;
    content_hash: string;
    recorded_at: string;
    publication: Publication;
  };
  source: Source;
}

export interface Issue {
  id: string;
  issue_type: string;
  severity: string;
  status: string;
  explanation: string;
  confidence: number;
  observed: boolean;
  owner: string | null;
  scope: Scope;
  finding_ids: string[];
  concept_ids: string[];
  options: string[];
  last_observed: string;
}

export interface Finding {
  id: string;
  check_id: string;
  check_version: string;
  action: string;
  concept_ids: string[];
  version_ids: string[];
  explanation: string;
  confidence: number;
  uncertainty: string | null;
  relationship: string | null;
  scope: Scope;
  recorded_at: string;
  passages: {
    source_url: string;
    section: string;
    excerpt: string;
    start: number;
    end: number;
    content_hash: string;
  }[];
}

export interface Decision {
  id: string;
  finding_id: string;
  outcome: string;
  actor: string;
  rationale: string;
  recorded_at: string;
}

export interface SourceInspection {
  source: Source;
  revisions: number;
  segments: number;
  versions: number;
  occurrences: number;
  items: { concept_id: string; version: Version; evidence_count: number }[];
  next_cursor: string | null;
}

export interface KnowledgeGraph {
  root: string;
  nodes: { id: string; label: string; kind: string; href: string }[];
  edges: {
    source: string;
    target: string;
    kind: string;
    status: string;
    href: string;
  }[];
  truncated: boolean;
}

export interface BacklogPage {
  items: { finding: Finding; decision: Decision | null }[];
  total: number;
  next_cursor: string | null;
}

export interface TableInfo {
  name: string;
  count: number;
}

export interface TablePage {
  name: string;
  columns: { name: string; type: string; reference: string | null }[];
  rows: Record<string, unknown>[];
  next_cursor: string | null;
  previews: boolean;
}

export interface LogPage {
  events: {
    timestamp: string;
    event: string;
    process: string;
    fields: Record<string, string | number>;
  }[];
  files_read: number;
  truncated: boolean;
}

export interface Execution {
  id: string;
  kind: string;
  processor_id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  attempts: number;
  error: string | null;
  statistics: Record<string, number>;
}

export interface Pipeline {
  run: Execution;
  executions: Execution[];
}

export interface Scan {
  id: string;
  started_at: string;
  status: string;
  discovered: number;
  processed: number;
  skipped: number;
  failed: number;
  concepts: number;
}

export interface Statistics {
  task_count: number;
  failures: number;
  input_tokens: number;
  output_tokens: number;
  model_latency_ms: number;
  configured_cost: number | null;
  validation_failures: number;
}

export interface Control {
  control: {
    control: {
      control_id: string;
      title: string;
      security_principle: string | null;
      azure_guidance: string;
      generic_guidance: string | null;
      implementation_options: string[];
      implementation_options_are_alternatives: boolean | null;
      evidence: { excerpt: string; source_url: string };
      publication: Publication;
      subcontrols: { control_id: string; title: string; text: string }[];
    };
    mappings: {
      reference: {
        framework: string;
        control_id: string;
        resolution: string;
        coverage: string;
        evidence: { excerpt: string; source_url: string };
        resolved_concept_ids: string[];
      };
    }[];
    policies: {
      reference: { name: string; definition_id: string; url: string };
      definition: { name: string; version: string | null } | null;
      version_matches_reference: boolean | null;
    }[];
  };
  edition: { edition: string; publication: Publication };
  concept: Concept | null;
}

export interface Comparison {
  changes: {
    old: { control: { control_id: string; title: string } } | null;
    new: { control: { control_id: string; title: string } } | null;
    lineage: string;
    proposed_change: string | null;
    changed_fields: string[];
    version_note: string;
    guidance_diff: string;
  }[];
  added_control_ids: string[];
  removed_control_ids: string[];
  notes: string[];
  model_calls: number;
}
