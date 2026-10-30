export type Scope = Record<string, string | null>;

export type SemanticType =
  | "StrategicGoal"
  | "StrategicRequirement"
  | "OrganizationalScope"
  | "ArchitectureDomain"
  | "ConceptualRequirement"
  | "TechnicalRequirement"
  | "PlatformRequirement";

export interface OntologyTerm {
  name: string;
  iri: string;
  label: string;
  definition: string;
  kind: "class" | "object_property" | "datatype_property";
  category: "concept" | "relation" | "classification";
  domains: string[];
  ranges: string[];
  characteristics: ("symmetric" | "transitive")[];
}

export interface OntologyCatalog {
  iri: string;
  label: string;
  definition: string;
  namespace: string;
  version: string;
  sha256: string;
  terms: OntologyTerm[];
}

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
    semantic_type: SemanticType | null;
    statement: string;
    modality: string;
    confidence: number;
    uncertainty: string | null;
    scope: Scope;
  };
  publication: Publication;
  recorded_at: string;
  assertion?: {
    text: string | null;
    uncertainty: string | null;
    model_id: string;
    prompt_version: string;
    occurrence_id: string;
    source_id: string;
  } | null;
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
  matching_version_id?: string | null;
  levels?: Record<string, "top_level" | "implementation">;
  sources?: {
    id: string;
    name: string;
    publisher: string | null;
    location: string;
    version_ids: string[];
    evidence_counts?: Record<string, number>;
  }[];
}

export interface Provenance {
  context_before?: {
    text: string;
    heading: string | null;
    location: Record<string, unknown>;
  } | null;
  context_after?: {
    text: string;
    heading: string | null;
    location: Record<string, unknown>;
  } | null;
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
    text: string;
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
  items: {
    concept_id: string;
    version: Version;
    evidence_count: number;
    level?: string;
  }[];
  next_cursor: string | null;
}

export interface KnowledgeGraph {
  root: string;
  nodes: {
    id: string;
    label: string;
    kind: string;
    href: string;
    level?: string | null;
  }[];
  edges: {
    source: string;
    target: string;
    kind: string;
    status: string;
    href: string;
    evidence?: {
      excerpt: string;
      segment_id: string;
      start: number;
      end: number;
    } | null;
    note?: string | null;
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
