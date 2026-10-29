import { useState, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, Inbox, RefreshCw, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { label } from "@/lib/api";
import type { Scope } from "@/types";

export function Heading({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1 tabIndex={-1}>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {children && <div className="heading-actions">{children}</div>}
    </div>
  );
}

export function Panel({
  title,
  description,
  children,
  action,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="panel-heading">
        <div>
          <CardTitle>
            <h2>{title}</h2>
          </CardTitle>
          {description && (
            <CardDescription className="panel-description">
              {description}
            </CardDescription>
          )}
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function Status({ value }: { value: string }) {
  return (
    <Badge variant="outline" className={`status status-${value}`}>
      <span className="status-dot" aria-hidden="true" />
      {label(value)}
    </Badge>
  );
}

export function LoadState({
  resource,
  empty,
  children,
}: {
  resource: { loading: boolean; error: string | null; reload: () => void };
  empty?: boolean;
  children: ReactNode;
}) {
  if (resource.error)
    return (
      <div className="empty" role="alert">
        <RefreshCw aria-hidden="true" />
        <strong>Could not load this information</strong>
        <p>{resource.error} No results have been substituted.</p>
        <Button variant="outline" onClick={resource.reload}>
          Try again
        </Button>
      </div>
    );
  if (resource.loading)
    return (
      <div className="loading" role="status" aria-label="Loading information">
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
        <span className="sr-only">Loading information</span>
      </div>
    );
  if (empty) return <Empty />;
  return children;
}

export function Empty({
  title = "Nothing to show yet",
  description = "Try another search or filter. New results appear after ingestion.",
}: {
  title?: string;
  description?: string;
}) {
  return (
    <div className="empty">
      <Inbox aria-hidden="true" />
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}

export function SearchBox({
  value,
  onChange,
  placeholder = "Search",
  name = "Search",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  name?: string;
}) {
  return (
    <div className="search-box">
      <Search aria-hidden="true" />
      <Input
        type="search"
        aria-label={name}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

export function Filter({
  name,
  value,
  onChange,
  options,
}: {
  name: string;
  value: string;
  onChange: (value: string) => void;
  options: [string, string][];
}) {
  return (
    <select
      className="control"
      aria-label={name}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {options.map(([key, text]) => (
        <option key={key} value={key}>
          {text}
        </option>
      ))}
    </select>
  );
}

export function Pager({
  page,
  count,
  size = 25,
  onChange,
}: {
  page: number;
  count: number;
  size?: number;
  onChange: (page: number) => void;
}) {
  return (
    <div className="pager">
      <span>
        {count ? page * size + 1 : 0}–{Math.min((page + 1) * size, count)} of{" "}
        {count.toLocaleString()} loaded results
      </span>
      <div>
        <Button
          variant="outline"
          size="sm"
          aria-label="Previous page"
          disabled={page === 0}
          onClick={() => onChange(page - 1)}
        >
          <ArrowLeft />
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          aria-label="Next page"
          disabled={(page + 1) * size >= count}
          onClick={() => onChange(page + 1)}
        >
          Next
          <ArrowRight />
        </Button>
      </div>
    </div>
  );
}

export function ScopeInfo({ scope }: { scope: Scope }) {
  const entries = Object.entries(scope).filter(([, value]) => value);
  return entries.length ? (
    <dl className="metadata">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{label(key)}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  ) : (
    <p className="muted">
      Scope is unknown. Applicability has not been established.
    </p>
  );
}

export function CursorPager({
  page,
  count,
  onNext,
  onPrevious,
  disabled = false,
}: {
  page: number;
  count: number;
  onNext: () => void;
  onPrevious: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="pager">
      <span>
        Page {page} · {count} records on this page
      </span>
      <div>
        <Button
          size="sm"
          variant="outline"
          disabled={disabled || page === 1}
          onClick={onPrevious}
        >
          <ArrowLeft />
          Previous
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={disabled || count !== 50}
          onClick={onNext}
        >
          Next
          <ArrowRight />
        </Button>
      </div>
    </div>
  );
}

export function Technical({
  value,
  title = "Technical details",
}: {
  value: unknown;
  title?: string;
}) {
  return (
    <details className="technical">
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export function Metric({
  title,
  value,
  detail,
}: {
  title: string;
  value: string | number | undefined;
  detail: string;
}) {
  return (
    <Card className="metric">
      <CardContent>
        <span>{title}</span>
        <strong>
          {value === undefined
            ? "—"
            : typeof value === "number"
              ? value.toLocaleString()
              : value}
        </strong>
        <small>{detail}</small>
      </CardContent>
    </Card>
  );
}

export function usePage<T>(items: T[], size = 25) {
  const [requested, setPage] = useState(0);
  const page = Math.min(
    requested,
    Math.max(0, Math.ceil(items.length / size) - 1),
  );
  return { page, setPage, rows: items.slice(page * size, (page + 1) * size) };
}
