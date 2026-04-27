import { useEffect, useState } from "react";
import { useAuth, readErrorMessage } from "../App";
import { formatDate, pct } from "../utils";

type SourceMetric = {
  source_id: string;
  name: string;
  poll_attempts_success: number;
  poll_attempts_failed: number;
  items_ingested_total: number;
  success_rate: number | null;
  error_rate: number | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_success_latency_ms: number | null;
  last_success_fetch_path: "primary" | "fallback" | null;
  last_poll_failure: { at: string; reason: string; error_class: string } | null;
};

function sourceHealth(s: SourceMetric): { dot: string; label: string; labelClass: string } {
  if (s.last_poll_failure) return { dot: "dot-red", label: "Failing", labelClass: "text-critical" };
  if (s.success_rate === null) return { dot: "dot-grey", label: "No data", labelClass: "text-dim" };
  if (s.success_rate >= 0.9) return { dot: "dot-green pulsing", label: "Healthy", labelClass: "text-success" };
  if (s.success_rate >= 0.5) return { dot: "dot-amber", label: "Degraded", labelClass: "" };
  return { dot: "dot-red", label: "Failing", labelClass: "text-critical" };
}

function inferJurisdiction(name: string): string {
  const upper = name.toUpperCase();
  if (upper.includes("FDA")) return "United States";
  if (upper.includes("EMA")) return "European Union";
  if (upper.includes("TGA")) return "Australia";
  return "—";
}

export function Sources() {
  const { token, apiBase } = useAuth();
  const [sources, setSources] = useState<SourceMetric[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [polling, setPolling] = useState<Record<string, boolean>>({});
  const [pollMsg, setPollMsg] = useState<Record<string, string>>({});

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(`${apiBase}/ops/source-metrics?limit=200`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) { setErr(await readErrorMessage(r)); return; }
      setSources((await r.json()) as SourceMetric[]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load source metrics.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [token, apiBase]);

  async function pollSource(sourceId: string) {
    setPolling((s) => ({ ...s, [sourceId]: true }));
    setPollMsg((s) => ({ ...s, [sourceId]: "" }));
    try {
      const r = await fetch(`${apiBase}/sources/${encodeURIComponent(sourceId)}/poll`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.ok) {
        setPollMsg((s) => ({ ...s, [sourceId]: "Poll triggered." }));
        setTimeout(() => void load(), 2000);
      } else if (r.status === 404) {
        setPollMsg((s) => ({ ...s, [sourceId]: "Manual poll not available." }));
      } else {
        const msg = await readErrorMessage(r);
        setPollMsg((s) => ({ ...s, [sourceId]: msg }));
      }
    } catch {
      setPollMsg((s) => ({ ...s, [sourceId]: "Poll request failed." }));
    } finally {
      setPolling((s) => ({ ...s, [sourceId]: false }));
    }
  }

  const healthy = sources.filter((s) => s.last_poll_failure === null && (s.success_rate ?? 1) >= 0.9).length;
  const degraded = sources.filter((s) => {
    const h = sourceHealth(s);
    return h.label === "Degraded";
  }).length;
  const failing = sources.filter((s) => s.last_poll_failure !== null || (s.success_rate ?? 1) < 0.5).length;

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">Source Health</div>
          <div className="page-subtitle">
            Real-time ingestion metrics for all monitored regulatory sources.
          </div>
        </div>
        <button className="btn btn-secondary" type="button" onClick={() => void load()}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M13 7A6 6 0 1 1 7 1M13 1v4H9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Refresh
        </button>
      </div>

      {/* Summary pills */}
      {sources.length > 0 && (
        <div className="flex gap-12 mb-20">
          <div className="card-sm flex items-center gap-10" style={{ padding: "10px 16px" }}>
            <span className="status-dot dot-green pulsing" />
            <span style={{ fontSize: 13, color: "var(--text)" }}>{healthy} healthy</span>
          </div>
          {degraded > 0 && (
            <div className="card-sm flex items-center gap-10" style={{ padding: "10px 16px" }}>
              <span className="status-dot dot-amber" />
              <span style={{ fontSize: 13, color: "var(--text)" }}>{degraded} degraded</span>
            </div>
          )}
          {failing > 0 && (
            <div className="card-sm flex items-center gap-10" style={{ padding: "10px 16px" }}>
              <span className="status-dot dot-red" />
              <span style={{ fontSize: 13, color: "var(--text)" }}>{failing} failing</span>
            </div>
          )}
        </div>
      )}

      {err && <div className="error-banner" role="alert">{err}</div>}

      {loading ? (
        <div className="loading-center"><div className="spinner" />Loading source metrics…</div>
      ) : sources.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">
            <svg viewBox="0 0 48 48" fill="none">
              <circle cx="24" cy="24" r="20" stroke="currentColor" strokeWidth="2" />
              <path d="M24 14v10l6 6" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
            </svg>
          </div>
          <h3>No sources registered</h3>
          <p>Sources appear here once the pipeline begins ingesting regulatory data.</p>
        </div>
      ) : (
        <div className="sources-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Jurisdiction</th>
                <th>Status</th>
                <th>Success Rate</th>
                <th>Items Ingested</th>
                <th>Last Polled</th>
                <th>Latency</th>
                <th>Last Failure</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => {
                const health = sourceHealth(s);
                return (
                  <tr key={s.source_id}>
                    <td>
                      <span style={{ fontWeight: 600 }}>{s.name}</span>
                    </td>
                    <td style={{ color: "var(--text-secondary)" }}>
                      {inferJurisdiction(s.name)}
                    </td>
                    <td>
                      <div className="flex items-center gap-8">
                        <span className={`status-dot ${health.dot}`} />
                        <span className={health.labelClass} style={{ fontSize: 13 }}>
                          {health.label}
                        </span>
                      </div>
                    </td>
                    <td>
                      <div className="flex items-center gap-8">
                        <div style={{
                          width: 60, height: 4, background: "rgba(255,255,255,0.06)",
                          borderRadius: 2, overflow: "hidden",
                        }}>
                          <div style={{
                            height: "100%", borderRadius: 2,
                            width: `${(s.success_rate ?? 0) * 100}%`,
                            background: health.dot.includes("green") ? "var(--success)"
                              : health.dot.includes("amber") ? "var(--medium)"
                              : "var(--critical)",
                          }} />
                        </div>
                        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                          {pct(s.success_rate)}
                        </span>
                      </div>
                    </td>
                    <td style={{ fontWeight: 600 }}>{s.items_ingested_total.toLocaleString()}</td>
                    <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>
                      {s.last_success_at ? formatDate(s.last_success_at) : "—"}
                    </td>
                    <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>
                      {s.last_success_latency_ms != null ? `${s.last_success_latency_ms}ms` : "—"}
                    </td>
                    <td style={{ maxWidth: 200 }}>
                      {s.last_poll_failure ? (
                        <span style={{ fontSize: 12, color: "var(--critical)" }} className="truncate" title={s.last_poll_failure.reason}>
                          {s.last_poll_failure.error_class}: {s.last_poll_failure.reason.slice(0, 60)}
                          {s.last_poll_failure.reason.length > 60 ? "…" : ""}
                        </span>
                      ) : (
                        <span style={{ fontSize: 12, color: "var(--text-dim)" }}>—</span>
                      )}
                    </td>
                    <td>
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                        <button
                          className="btn btn-secondary btn-xs"
                          type="button"
                          disabled={polling[s.source_id]}
                          onClick={() => void pollSource(s.source_id)}
                        >
                          {polling[s.source_id] ? (
                            <><div className="spinner" style={{ width: 10, height: 10 }} />Polling…</>
                          ) : "Poll now"}
                        </button>
                        {pollMsg[s.source_id] && (
                          <span style={{ fontSize: 11, color: "var(--text-dim)", whiteSpace: "nowrap" }}>
                            {pollMsg[s.source_id]}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
