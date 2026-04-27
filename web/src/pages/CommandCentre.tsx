import { useEffect, useState } from "react";
import { useAuth, readErrorMessage } from "../App";
import { SeverityBadge, sevColor } from "../components/SeverityBadge";
import { timeAgo } from "../utils";

type DashboardSummary = {
  severity_counts: Record<string, number>;
  new_items_count: number;
  new_items_window_hours: number;
  review_queue_count: number;
  top_sources: { source_id: string; name: string; metric: string; value: number }[];
  top_sources_metric: string;
};

type UpdateItem = {
  id: string;
  title: string | null;
  source_name: string;
  jurisdiction: string;
  document_type: string;
  created_at: string;
  derived_severity: string | null;
  item_url: string;
};

type SourceMetric = {
  source_id: string;
  name: string;
  success_rate: number | null;
  error_rate: number | null;
  items_ingested_total: number;
  last_success_at: string | null;
  last_poll_failure: { reason: string } | null;
};

const AGENTS = [
  { name: "Scout", desc: "Source crawler" },
  { name: "Normalizer", desc: "Data enrichment" },
  { name: "Analyst", desc: "Impact classifier" },
  { name: "Briefing", desc: "Report generator" },
  { name: "Routing", desc: "Alert dispatcher" },
  { name: "Feedback", desc: "Quality tuner" },
];

const SEV_ORDER = ["critical", "high", "medium", "low"];

export function CommandCentre() {
  const { token, apiBase, me } = useAuth();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [updates, setUpdates] = useState<UpdateItem[]>([]);
  const [sources, setSources] = useState<SourceMetric[]>([]);
  const [loading, setLoading] = useState(true);
  const [agentsOnline, setAgentsOnline] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    const headers = { Authorization: `Bearer ${token}` };
    (async () => {
      setLoading(true);
      try {
        const [summaryR, updatesR, sourcesR] = await Promise.all([
          fetch(`${apiBase}/dashboard/summary`, { headers, signal: ctrl.signal }),
          fetch(`${apiBase}/updates?limit=10&sort=created_at_desc`, { headers, signal: ctrl.signal }),
          fetch(`${apiBase}/ops/source-metrics?limit=20`, { headers, signal: ctrl.signal }),
        ]);
        if (summaryR.ok) {
          setSummary((await summaryR.json()) as DashboardSummary);
          setAgentsOnline(true);
        }
        if (updatesR.ok) {
          const j = (await updatesR.json()) as { items: UpdateItem[] };
          setUpdates(j.items);
        }
        if (sourcesR.ok) {
          setSources((await sourcesR.json()) as SourceMetric[]);
        }
      } catch { /* ignore abort */ }
      finally { setLoading(false); }
    })();
    return () => ctrl.abort();
  }, [token, apiBase]);

  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const firstName = me?.email?.split("@")[0] ?? "there";

  const sevCounts = summary?.severity_counts ?? {};
  const totalClassified = SEV_ORDER.reduce((s, k) => s + (sevCounts[k] ?? 0), 0);

  function sourceHealthStatus(s: SourceMetric): { dot: string; label: string } {
    if (s.last_poll_failure) return { dot: "dot-red", label: "Degraded" };
    if (s.success_rate === null) return { dot: "dot-grey", label: "No data" };
    if (s.success_rate >= 0.9) return { dot: "dot-green", label: "Healthy" };
    if (s.success_rate >= 0.5) return { dot: "dot-amber", label: "Degraded" };
    return { dot: "dot-red", label: "Failing" };
  }

  const knownSources = ["FDA", "EMA", "TGA"];
  const sourceRows = knownSources.map((name) => {
    const match = sources.find((s) => s.name.toUpperCase().includes(name));
    return { name, metric: match, health: match ? sourceHealthStatus(match) : { dot: "dot-grey", label: "Unknown" } };
  });

  if (loading) {
    return (
      <div className="loading-center">
        <div className="spinner" />
        Loading Command Centre…
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">{greeting}, {firstName}</div>
          <div className="page-subtitle">
            {new Date().toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}
          </div>
        </div>
        <div className="flex items-center gap-8">
          <span className={`status-dot ${agentsOnline ? "dot-green pulsing" : "dot-grey"}`} />
          <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
            {agentsOnline ? "Platform operational" : "Connecting…"}
          </span>
        </div>
      </div>

      {/* Agent strip */}
      <div className="agent-strip">
        {AGENTS.map((agent) => (
          <div key={agent.name} className="agent-card">
            <span className={`status-dot ${agentsOnline ? "dot-green pulsing" : "dot-grey"}`} />
            <div className="agent-info">
              <div className="agent-name">{agent.name}</div>
              <div className={`agent-status-text${agentsOnline ? "" : " offline"}`}>
                {agentsOnline ? "Operational" : "Unknown"}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Stat cards */}
      <div className="stat-grid">
        <StatCard
          icon={<svg width="18" height="18" viewBox="0 0 18 18" fill="none"><path d="M9 2v5M9 11v5M2 9h5M11 9h5" stroke="white" strokeWidth="1.6" strokeLinecap="round" /></svg>}
          iconBg="rgba(37, 99, 235, 0.2)"
          label="New Updates (24h)"
          value={summary?.new_items_count ?? 0}
          meta={`Past ${summary?.new_items_window_hours ?? 24} hours`}
        />
        <StatCard
          icon={<svg width="18" height="18" viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="7" stroke="white" strokeWidth="1.6" /><path d="M9 5v4l2 2" stroke="white" strokeWidth="1.6" strokeLinecap="round" /></svg>}
          iconBg="rgba(255, 138, 0, 0.2)"
          label="Pending Review"
          value={summary?.review_queue_count ?? 0}
          meta="Awaiting analyst decision"
          alert={(summary?.review_queue_count ?? 0) > 0}
        />
        <StatCard
          icon={<svg width="18" height="18" viewBox="0 0 18 18" fill="none"><rect x="3" y="2" width="12" height="14" rx="2" stroke="white" strokeWidth="1.6" /><path d="M6 6h6M6 9h6M6 12h4" stroke="white" strokeWidth="1.5" strokeLinecap="round" /></svg>}
          iconBg="rgba(43, 213, 118, 0.2)"
          label="Briefings Generated"
          value={0}
          meta="All time"
        />
        <StatCard
          icon={<svg width="18" height="18" viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="7" stroke="white" strokeWidth="1.6" /><path d="M2 9h14M9 2a12 12 0 0 1 0 14M9 2a12 12 0 0 0 0 14" stroke="white" strokeWidth="1.5" /></svg>}
          iconBg="rgba(139, 150, 168, 0.2)"
          label="Sources Monitored"
          value={sources.length || 3}
          meta="FDA · EMA · TGA"
        />
      </div>

      {/* Main content — 3:2 split */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 380px", gap: 20 }}>
        {/* Activity feed */}
        <div className="card" style={{ padding: "22px 24px" }}>
          <div className="flex justify-between items-center" style={{ marginBottom: 16 }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: "var(--text)" }}>Live Activity</span>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>10 most recent</span>
          </div>
          {updates.length === 0 ? (
            <div className="empty-state" style={{ padding: "32px 16px" }}>
              <div className="empty-state-icon">
                <svg viewBox="0 0 48 48" fill="none"><rect x="8" y="8" width="32" height="32" rx="4" stroke="currentColor" strokeWidth="2" /><path d="M16 20h16M16 26h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
              </div>
              <h3>No recent activity</h3>
              <p>Updates will appear here as they're ingested.</p>
            </div>
          ) : (
            updates.map((u) => (
              <div key={u.id} className="activity-item">
                <div
                  className="activity-sev-bar"
                  style={{ background: sevColor(u.derived_severity) }}
                />
                <div className="activity-content">
                  <div className="activity-title">{u.title || u.item_url}</div>
                  <div className="activity-meta">
                    <SeverityBadge severity={u.derived_severity} />
                    <span className="update-source-chip">{u.source_name}</span>
                    <span className="activity-time">{timeAgo(u.created_at)}</span>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Right column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Source health */}
          <div className="card" style={{ padding: "22px 24px" }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text)", marginBottom: 16 }}>
              Source Health
            </div>
            {sourceRows.map(({ name, health, metric }) => (
              <div key={name} className="health-row">
                <span className="health-source-name">{name}</span>
                <span className={`status-dot ${health.dot}`} />
                <div className="health-bar-wrap">
                  <div
                    className="health-bar-fill"
                    style={{
                      width: `${Math.round((metric?.success_rate ?? 0) * 100)}%`,
                      background: health.dot === "dot-green" ? "var(--success)" : health.dot === "dot-amber" ? "var(--medium)" : health.dot === "dot-red" ? "var(--critical)" : "var(--text-dim)",
                    }}
                  />
                </div>
                <span className="health-pct">
                  {metric?.success_rate != null ? `${Math.round(metric.success_rate * 100)}%` : "—"}
                </span>
              </div>
            ))}
            {sourceRows.every(r => !r.metric) && (
              <p style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 8 }}>
                No ingestion data yet. Health updates as sources are polled.
              </p>
            )}
          </div>

          {/* Severity distribution */}
          <div className="card" style={{ padding: "22px 24px" }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text)", marginBottom: 4 }}>
              Severity Distribution
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 2 }}>
              {totalClassified} classified updates
            </div>
            <div className="sev-bar-track">
              {totalClassified > 0 && SEV_ORDER.map((sev) => {
                const count = sevCounts[sev] ?? 0;
                const pct = (count / totalClassified) * 100;
                if (pct === 0) return null;
                return (
                  <div
                    key={sev}
                    className="sev-bar-segment"
                    style={{ width: `${pct}%`, background: sevColor(sev) }}
                  />
                );
              })}
              {totalClassified === 0 && (
                <div className="sev-bar-segment" style={{ width: "100%", background: "var(--text-dim)", opacity: 0.3 }} />
              )}
            </div>
            <div className="sev-bar-legend">
              {SEV_ORDER.map((sev) => (
                <div key={sev} className="sev-legend-item">
                  <div className="sev-legend-dot" style={{ background: sevColor(sev) }} />
                  <span style={{ textTransform: "capitalize" }}>{sev}</span>
                  <span style={{ color: "var(--text-dim)" }}>({sevCounts[sev] ?? 0})</span>
                </div>
              ))}
            </div>
          </div>

          {/* Top sources */}
          {(summary?.top_sources.length ?? 0) > 0 && (
            <div className="card" style={{ padding: "18px 20px" }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 12 }}>
                Top Sources
              </div>
              {summary!.top_sources.slice(0, 5).map((s) => (
                <div key={s.source_id} className="flex justify-between items-center" style={{ marginBottom: 8 }}>
                  <span style={{ fontSize: 13, color: "var(--text)" }}>{s.name}</span>
                  <span style={{ fontSize: 12, color: "var(--text-dim)" }}>{s.value} {s.metric}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({
  icon,
  iconBg,
  label,
  value,
  meta,
  alert,
}: {
  icon: React.ReactNode;
  iconBg: string;
  label: string;
  value: number;
  meta?: string;
  alert?: boolean;
}) {
  return (
    <div className="stat-card">
      <div className="stat-icon-wrap" style={{ background: iconBg }}>{icon}</div>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={alert && value > 0 ? { color: "var(--high)" } : {}}>
        {value}
      </div>
      {meta && <div className="stat-meta">{meta}</div>}
    </div>
  );
}
