export function sevColor(sev: string | null | undefined): string {
  switch (sev?.toLowerCase()) {
    case "critical": return "var(--critical)";
    case "high": return "var(--high)";
    case "medium": return "var(--medium)";
    case "low": return "var(--low)";
    default: return "var(--text-dim)";
  }
}

export function sevBadgeClass(sev: string | null | undefined): string {
  switch (sev?.toLowerCase()) {
    case "critical": return "badge badge-critical";
    case "high": return "badge badge-high";
    case "medium": return "badge badge-medium";
    case "low": return "badge badge-low";
    default: return "badge badge-unknown";
  }
}

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  return (
    <span className={sevBadgeClass(severity)}>
      <span className="badge-dot" />
      {severity ?? "Unknown"}
    </span>
  );
}
