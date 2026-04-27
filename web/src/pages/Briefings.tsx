import { useEffect, useState } from "react";
import { useAuth, readErrorMessage } from "../App";
import { SeverityBadge } from "../components/SeverityBadge";
import { formatDate, formatDateShort } from "../utils";

type BriefingListItem = {
  id: string;
  run_id: string;
  created_at: string;
  group_count: number;
  summary: string;
};

type BriefingSections = {
  what_changed: string;
  why_it_matters: string;
  who_should_care: string;
  confidence: string;
  suggested_actions: string | null;
};

type BriefingMember = {
  normalized_update_id: string | null;
  item_url: string;
  title: string | null;
  jurisdiction: string;
  document_type: string;
  severity: string | null;
  confidence: number | null;
  impact_categories: string[];
};

type BriefingGroup = {
  dimensions: Record<string, string>;
  sections: BriefingSections;
  members: BriefingMember[];
};

type BriefingDetail = {
  id: string;
  run_id: string;
  created_at: string;
  grouping_dimensions: string[];
  groups: BriefingGroup[];
};

type ListResponse = { items: BriefingListItem[] };

export function Briefings() {
  const { token, apiBase } = useAuth();
  const [items, setItems] = useState<BriefingListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<BriefingDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailErr, setDetailErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const r = await fetch(`${apiBase}/briefings?limit=50`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok) { setErr(await readErrorMessage(r)); return; }
        const j = (await r.json()) as ListResponse;
        setItems(j.items);
      } catch (e) {
        setErr(e instanceof Error ? e.message : "Failed to load briefings.");
      } finally {
        setLoading(false);
      }
    })();
  }, [token, apiBase]);

  function openBriefing(id: string) {
    setOpenId(id);
    setDetail(null);
    setDetailErr(null);
    setLoadingDetail(true);
    (async () => {
      try {
        const r = await fetch(`${apiBase}/briefings/${id}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok) { setDetailErr(await readErrorMessage(r)); return; }
        setDetail((await r.json()) as BriefingDetail);
      } catch (e) {
        setDetailErr(e instanceof Error ? e.message : "Failed to load briefing.");
      } finally {
        setLoadingDetail(false);
      }
    })();
  }

  function closeDetail() {
    setOpenId(null);
    setDetail(null);
    setDetailErr(null);
  }

  if (loading) {
    return <div className="loading-center"><div className="spinner" />Loading briefings…</div>;
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">Regulatory Briefings</div>
          <div className="page-subtitle">
            AI-generated summaries grouping updates by jurisdiction and impact.
          </div>
        </div>
      </div>

      {err && <div className="error-banner" role="alert">{err}</div>}

      {!err && items.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon">
            <svg viewBox="0 0 48 48" fill="none">
              <rect x="8" y="6" width="32" height="36" rx="4" stroke="currentColor" strokeWidth="2" />
              <path d="M16 16h16M16 22h16M16 28h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </div>
          <h3>No briefings yet</h3>
          <p>Briefings are generated automatically when the pipeline processes a batch of updates.</p>
        </div>
      )}

      <div className="briefings-grid">
        {items.map((b) => (
          <div
            key={b.id}
            className="briefing-card"
            onClick={() => openBriefing(b.id)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && openBriefing(b.id)}
          >
            <div className="briefing-card-header">
              <div>
                <div className="briefing-date">{formatDateShort(b.created_at)}</div>
                <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 2 }}>
                  {b.group_count} {b.group_count === 1 ? "group" : "groups"}
                </div>
              </div>
              <div className="flex items-center gap-8">
                <span className="badge badge-info">View Briefing →</span>
              </div>
            </div>
            {b.summary && (
              <div className="briefing-summary">{b.summary}</div>
            )}
            {!b.summary && (
              <div className="briefing-summary" style={{ color: "var(--text-dim)", fontStyle: "italic" }}>
                Generated {formatDate(b.created_at)}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Detail overlay */}
      {openId && (
        <div className="briefing-detail-overlay" onClick={(e) => e.target === e.currentTarget && closeDetail()}>
          <div className="briefing-detail-panel">
            <button className="briefing-close" type="button" onClick={closeDetail} aria-label="Close">×</button>

            {loadingDetail && (
              <div className="loading-center"><div className="spinner" />Loading…</div>
            )}
            {detailErr && <div className="error-banner">{detailErr}</div>}

            {detail && (
              <>
                <div style={{ marginBottom: 24 }}>
                  <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 4 }}>
                    Regulatory Briefing
                  </div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: "var(--text)", letterSpacing: "-0.01em" }}>
                    {formatDate(detail.created_at)}
                  </div>
                  {detail.grouping_dimensions.length > 0 && (
                    <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                      <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Grouped by:</span>
                      {detail.grouping_dimensions.map((d) => (
                        <span key={d} className="badge badge-info" style={{ fontSize: 11 }}>
                          {d.replace(/_/g, " ")}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {detail.groups.map((group, gi) => (
                  <div key={gi} className="briefing-group">
                    {Object.keys(group.dimensions).length > 0 && (
                      <div className="flex items-center gap-8" style={{ marginBottom: 14 }}>
                        {Object.entries(group.dimensions).map(([k, v]) => (
                          <span key={k} className="badge badge-info">
                            {String(v)}
                          </span>
                        ))}
                        <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                          {group.members.length} {group.members.length === 1 ? "update" : "updates"}
                        </span>
                      </div>
                    )}

                    <BriefingSection label="What changed" text={group.sections.what_changed} />
                    <BriefingSection label="Why it matters" text={group.sections.why_it_matters} />
                    <BriefingSection label="Who should care" text={group.sections.who_should_care} />
                    {group.sections.suggested_actions && (
                      <BriefingSection label="Suggested actions" text={group.sections.suggested_actions} />
                    )}
                    <div className="briefing-section-block">
                      <div className="briefing-section-label">Confidence assessment</div>
                      <div className="briefing-section-text" style={{ fontSize: 12, color: "var(--text-dim)" }}>
                        {group.sections.confidence}
                      </div>
                    </div>

                    {group.members.length > 0 && (
                      <div style={{ marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--card-border)" }}>
                        <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 10 }}>
                          Covered Updates ({group.members.length})
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                          {group.members.map((m, mi) => (
                            <div key={mi} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", background: "rgba(255,255,255,0.03)", borderRadius: 8 }}>
                              <SeverityBadge severity={m.severity} />
                              <span style={{ fontSize: 13, color: "var(--text)", flex: 1, minWidth: 0 }} className="truncate">
                                {m.title || m.item_url}
                              </span>
                              {m.jurisdiction && (
                                <span className="update-source-chip">{m.jurisdiction}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function BriefingSection({ label, text }: { label: string; text: string }) {
  if (!text) return null;
  return (
    <div className="briefing-section-block">
      <div className="briefing-section-label">{label}</div>
      <div className="briefing-section-text">{text}</div>
    </div>
  );
}
