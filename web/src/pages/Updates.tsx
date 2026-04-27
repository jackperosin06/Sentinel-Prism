import { type FormEvent, useEffect, useRef, useState } from "react";
import { useAuth, readErrorMessage } from "../App";
import { SeverityBadge, sevColor } from "../components/SeverityBadge";
import { timeAgo, formatDate } from "../utils";

type UpdateListItem = {
  id: string;
  title: string | null;
  source_name: string;
  jurisdiction: string;
  document_type: string;
  item_url: string;
  created_at: string;
  published_at: string | null;
  derived_severity: string | null;
  explorer_status: "in_human_review" | "briefed" | "processed";
  body_snippet: string | null;
};

type DetailResponse = {
  normalized: Record<string, unknown>;
  raw_payload: Record<string, unknown>;
  classification: {
    severity: string | null;
    impact_categories: string[];
    confidence: number | null;
    rationale?: string | null;
  } | null;
};

const PAGE_SIZE = 50;

const STATUS_LABELS: Record<string, string> = {
  in_human_review: "In Review",
  briefed: "Briefed",
  processed: "Processed",
};

const FEEDBACK_KINDS = [
  { value: "incorrect_relevance", label: "Incorrect relevance" },
  { value: "incorrect_severity", label: "Incorrect severity" },
  { value: "false_positive", label: "False positive" },
  { value: "false_negative", label: "False negative" },
];

export function Updates() {
  const { token, apiBase, me } = useAuth();

  const [items, setItems] = useState<UpdateListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loadingList, setLoadingList] = useState(true);
  const [listErr, setListErr] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);

  const [jurisdiction, setJurisdiction] = useState("");
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailResponse | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailErr, setDetailErr] = useState<string | null>(null);

  const [feedbackKind, setFeedbackKind] = useState(FEEDBACK_KINDS[0].value);
  const [feedbackComment, setFeedbackComment] = useState("");
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [feedbackErr, setFeedbackErr] = useState<string | null>(null);
  const [feedbackOk, setFeedbackOk] = useState<string | null>(null);

  const latestSelectedId = useRef<string | null>(null);
  latestSelectedId.current = selectedId;

  useEffect(() => {
    const ctrl = new AbortController();
    (async () => {
      setLoadingList(true);
      setListErr(null);
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(offset),
        sort: "created_at_desc",
      });
      if (jurisdiction) params.set("jurisdiction", jurisdiction);
      if (severity) params.set("severity", severity);
      if (status) params.set("explorer_status", status);
      if (search.trim()) params.set("source_name_contains", search.trim());
      try {
        const r = await fetch(`${apiBase}/updates?${params}`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (!r.ok) { setListErr(await readErrorMessage(r)); setItems([]); return; }
        const j = (await r.json()) as { items: UpdateListItem[]; total: number };
        setItems(j.items);
        setTotal(j.total);
        if (j.items.length && !selectedId) setSelectedId(j.items[0].id);
      } catch { /* ignore abort */ }
      finally { if (!ctrl.signal.aborted) setLoadingList(false); }
    })();
    return () => ctrl.abort();
  }, [token, apiBase, offset, refresh]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    const ctrl = new AbortController();
    (async () => {
      setLoadingDetail(true);
      setDetailErr(null);
      setDetail(null);
      try {
        const r = await fetch(`${apiBase}/updates/${selectedId}`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (!r.ok) { setDetailErr(await readErrorMessage(r)); return; }
        setDetail((await r.json()) as DetailResponse);
      } catch { /* ignore */ }
      finally { if (!ctrl.signal.aborted) setLoadingDetail(false); }
    })();
    return () => ctrl.abort();
  }, [token, apiBase, selectedId]);

  useEffect(() => {
    setFeedbackErr(null);
    setFeedbackOk(null);
    setFeedbackComment("");
    setFeedbackKind(FEEDBACK_KINDS[0].value);
  }, [selectedId]);

  function applyFilters(e: FormEvent) {
    e.preventDefault();
    setOffset(0);
    setRefresh((n) => n + 1);
  }

  async function submitFeedback(e: FormEvent) {
    e.preventDefault();
    if (!selectedId) return;
    const submittedId = selectedId;
    const comment = feedbackComment.trim();
    if (!comment) { setFeedbackErr("Comment is required."); return; }
    setFeedbackSubmitting(true);
    setFeedbackErr(null);
    try {
      const r = await fetch(`${apiBase}/updates/${submittedId}/feedback`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ kind: feedbackKind, comment }),
      });
      if (latestSelectedId.current !== submittedId) return;
      if (!r.ok) { setFeedbackErr(await readErrorMessage(r)); return; }
      setFeedbackOk("Feedback submitted — thank you.");
      setFeedbackComment("");
    } catch (err) {
      if (latestSelectedId.current === submittedId)
        setFeedbackErr(err instanceof Error ? err.message : "Failed to submit.");
    } finally {
      if (latestSelectedId.current === submittedId) setFeedbackSubmitting(false);
    }
  }

  const selectedItem = items.find((i) => i.id === selectedId) ?? null;
  const canFeedback = me?.role === "analyst" || me?.role === "admin";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 64px)" }}>
      <div className="page-header">
        <div>
          <div className="page-title">Intelligence Feed</div>
          <div className="page-subtitle">
            {total > 0 ? `${total} updates ingested across all sources` : "Browse and classify regulatory updates"}
          </div>
        </div>
      </div>

      <div className="updates-shell">
        {/* Left: list pane */}
        <div className="updates-list-pane">
          <form className="updates-filter-bar" onSubmit={applyFilters}>
            <div className="filter-row">
              <input
                className="form-input"
                style={{ flex: 1 }}
                placeholder="Search by source…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <button className="btn btn-secondary btn-sm" type="submit">Filter</button>
            </div>
            <div className="filter-row">
              <select
                className="form-select"
                style={{ flex: 1 }}
                value={jurisdiction}
                onChange={(e) => setJurisdiction(e.target.value)}
              >
                <option value="">All jurisdictions</option>
                <option value="FDA">FDA</option>
                <option value="EMA">EMA</option>
                <option value="TGA">TGA</option>
              </select>
              <select
                className="form-select"
                style={{ flex: 1 }}
                value={severity}
                onChange={(e) => setSeverity(e.target.value)}
              >
                <option value="">All severities</option>
                <option value="critical">Critical</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </div>
            <select
              className="form-select"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="">All statuses</option>
              <option value="in_human_review">In Review</option>
              <option value="briefed">Briefed</option>
              <option value="processed">Processed</option>
            </select>
          </form>

          <div className="updates-list">
            {loadingList ? (
              <div className="loading-center" style={{ padding: 32 }}>
                <div className="spinner" />
              </div>
            ) : listErr ? (
              <div className="error-banner" style={{ margin: 8 }}>{listErr}</div>
            ) : items.length === 0 ? (
              <div className="empty-state" style={{ padding: "32px 16px" }}>
                <h3>No updates found</h3>
                <p>Try adjusting your filters.</p>
              </div>
            ) : (
              items.map((item) => (
                <div
                  key={item.id}
                  className={`update-list-item${selectedId === item.id ? " selected" : ""}`}
                  onClick={() => setSelectedId(item.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && setSelectedId(item.id)}
                >
                  <div
                    className="update-list-sev"
                    style={{ background: sevColor(item.derived_severity) }}
                  />
                  <div className="update-list-content">
                    <div className="update-list-title">{item.title || item.item_url}</div>
                    <div className="update-list-meta">
                      <SeverityBadge severity={item.derived_severity} />
                      <span className="update-source-chip">{item.source_name}</span>
                      <span className="update-list-time">{timeAgo(item.created_at)}</span>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          {!loadingList && total > 0 && (
            <div className="pagination-row">
              <button
                className="btn btn-ghost btn-sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                type="button"
              >← Prev</button>
              <span className="pagination-info">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <button
                className="btn btn-ghost btn-sm"
                disabled={offset + items.length >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                type="button"
              >Next →</button>
            </div>
          )}
        </div>

        {/* Right: detail pane */}
        <div className="updates-detail-pane">
          {!selectedId && (
            <div className="detail-placeholder">
              <svg width="40" height="40" viewBox="0 0 40 40" fill="none" opacity="0.2">
                <rect x="6" y="6" width="28" height="28" rx="4" stroke="currentColor" strokeWidth="2" />
                <path d="M13 16h14M13 21h10M13 26h6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              <span>Select an update to view details</span>
            </div>
          )}

          {selectedId && loadingDetail && (
            <div className="loading-center"><div className="spinner" /></div>
          )}

          {selectedId && detailErr && (
            <div className="error-banner">{detailErr}</div>
          )}

          {selectedItem && detail && !loadingDetail && (
            <>
              <div className="detail-title">{selectedItem.title || selectedItem.item_url}</div>

              <div className="detail-meta-grid">
                <MetaItem label="Source" value={selectedItem.source_name} />
                <MetaItem label="Jurisdiction" value={selectedItem.jurisdiction || "—"} />
                <MetaItem label="Document Type" value={selectedItem.document_type || "—"} />
                <MetaItem label="Status" value={STATUS_LABELS[selectedItem.explorer_status] ?? selectedItem.explorer_status} />
                <MetaItem label="Published" value={selectedItem.published_at ? formatDate(selectedItem.published_at) : "—"} />
                <MetaItem label="Ingested" value={formatDate(selectedItem.created_at)} />
              </div>

              {selectedItem.body_snippet && (
                <div style={{ marginBottom: 18, padding: "14px 16px", background: "rgba(255,255,255,0.03)", borderRadius: 10, border: "1px solid var(--card-border)" }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>
                    Excerpt
                  </div>
                  <p style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.7 }}>
                    {selectedItem.body_snippet}
                  </p>
                </div>
              )}

              {detail.classification ? (
                <div className="classification-card">
                  <div className="classification-header">
                    <span className="classification-section-label">AI Classification</span>
                    <div className="confidence-row">
                      <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Confidence</span>
                      <div className="confidence-bar-wrap">
                        <div
                          className="confidence-bar-fill"
                          style={{ width: `${(detail.classification.confidence ?? 0) * 100}%` }}
                        />
                      </div>
                      <span className="confidence-pct">
                        {detail.classification.confidence != null
                          ? `${Math.round(detail.classification.confidence * 100)}%`
                          : "—"}
                      </span>
                    </div>
                  </div>
                  <SeverityBadge severity={detail.classification.severity} />
                  {detail.classification.impact_categories.length > 0 && (
                    <div className="impact-categories">
                      {detail.classification.impact_categories.map((cat) => (
                        <span key={cat} className="impact-chip">
                          {cat.replace(/_/g, " ")}
                        </span>
                      ))}
                    </div>
                  )}
                  {(detail.classification as Record<string, unknown>).rationale && (
                    <div className="rationale-text">
                      {String((detail.classification as Record<string, unknown>).rationale)}
                    </div>
                  )}
                </div>
              ) : (
                <div className="classification-card">
                  <div className="classification-section-label" style={{ marginBottom: 8 }}>
                    AI Classification
                  </div>
                  <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
                    This update has not been classified yet.
                  </p>
                </div>
              )}

              {/* Feedback form */}
              {canFeedback ? (
                <div className="feedback-section">
                  <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 14 }}>
                    Submit Feedback
                  </div>
                  {feedbackErr && <div className="error-banner" role="alert">{feedbackErr}</div>}
                  {feedbackOk && <div className="success-banner" role="status">{feedbackOk}</div>}
                  <form onSubmit={submitFeedback}>
                    <div className="form-group" style={{ marginBottom: 12 }}>
                      <label className="form-label">Category</label>
                      <select
                        className="form-select"
                        value={feedbackKind}
                        onChange={(e) => setFeedbackKind(e.target.value)}
                      >
                        {FEEDBACK_KINDS.map((k) => (
                          <option key={k.value} value={k.value}>{k.label}</option>
                        ))}
                      </select>
                    </div>
                    <div className="form-group" style={{ marginBottom: 12 }}>
                      <label className="form-label">Comment</label>
                      <textarea
                        className="form-textarea"
                        rows={3}
                        placeholder="Describe the issue with this classification…"
                        value={feedbackComment}
                        onChange={(e) => setFeedbackComment(e.target.value)}
                        maxLength={10_000}
                        required
                      />
                    </div>
                    <button
                      type="submit"
                      className="btn btn-primary btn-sm"
                      disabled={feedbackSubmitting || !feedbackComment.trim()}
                    >
                      {feedbackSubmitting ? <><div className="spinner spinner-sm" />Submitting…</> : "Submit feedback"}
                    </button>
                  </form>
                </div>
              ) : (
                <p style={{ fontSize: 13, color: "var(--text-dim)", marginTop: 8 }}>
                  Analyst or admin role required to submit feedback.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail-meta-item">
      <div className="detail-meta-label">{label}</div>
      <div className="detail-meta-value">{value}</div>
    </div>
  );
}
