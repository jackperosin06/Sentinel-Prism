import { useEffect, useState } from "react";
import { useAuth, readErrorMessage } from "../App";
import { SeverityBadge, sevColor } from "../components/SeverityBadge";
import { timeAgo } from "../utils";

type ClassificationRow = {
  item_url: string;
  source_id: string | null;
  in_scope: boolean | null;
  severity: string | null;
  confidence: number | null;
  needs_human_review: boolean | null;
  rationale_excerpt: string;
  impact_categories: string[];
  urgency: string | null;
};

type ReviewQueueItem = {
  run_id: string;
  source_id: string | null;
  queued_at: string;
  classifications: ClassificationRow[];
};

type QueueResponse = { items: ReviewQueueItem[] };

type ResumingState = Record<string, "approving" | "overriding" | null>;

export function ReviewQueue() {
  const { token, apiBase } = useAuth();
  const [items, setItems] = useState<ReviewQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [resuming, setResuming] = useState<ResumingState>({});
  const [actionErr, setActionErr] = useState<Record<string, string>>({});

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(`${apiBase}/review-queue`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) { setErr(await readErrorMessage(r)); return; }
      const j = (await r.json()) as QueueResponse;
      setItems(j.items);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load review queue.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [token, apiBase]);

  async function resumeRun(runId: string, decision: "approve" | "reject") {
    setResuming((s) => ({ ...s, [runId]: decision === "approve" ? "approving" : "overriding" }));
    setActionErr((s) => ({ ...s, [runId]: "" }));
    try {
      const r = await fetch(`${apiBase}/runs/${encodeURIComponent(runId)}/resume`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ decision, note: "" }),
      });
      if (!r.ok) {
        const msg = await readErrorMessage(r);
        setActionErr((s) => ({ ...s, [runId]: msg }));
        return;
      }
      setItems((prev) => prev.filter((i) => i.run_id !== runId));
    } catch (e) {
      setActionErr((s) => ({ ...s, [runId]: e instanceof Error ? e.message : "Action failed." }));
    } finally {
      setResuming((s) => ({ ...s, [runId]: null }));
    }
  }

  const pendingCount = items.length;

  if (loading) {
    return (
      <div className="loading-center">
        <div className="spinner" />
        Loading review queue…
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title" style={{ display: "flex", alignItems: "center", gap: 12 }}>
            Review Queue
            {pendingCount > 0 && (
              <span className="badge badge-high">{pendingCount} pending</span>
            )}
          </div>
          <div className="page-subtitle">
            Updates flagged by AI for human review before routing and briefing.
          </div>
        </div>
        <button className="btn btn-secondary" type="button" onClick={() => void load()}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M13 7A6 6 0 1 1 7 1M13 1v4H9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Refresh
        </button>
      </div>

      {err && <div className="error-banner" role="alert">{err}</div>}

      {!err && items.length === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon">
            <svg viewBox="0 0 48 48" fill="none">
              <circle cx="24" cy="24" r="20" stroke="currentColor" strokeWidth="2" />
              <path d="M16 24l6 6 10-10" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h3>Queue is empty</h3>
          <p>All flagged updates have been reviewed. New items will appear here when the pipeline flags them.</p>
        </div>
      )}

      <div className="review-grid">
        {items.map((item) => {
          const primary = item.classifications[0] ?? null;
          const sev = primary?.severity ?? null;
          const confidence = primary?.confidence;
          const rationale = primary?.rationale_excerpt;
          const url = primary?.item_url ?? "";
          const displayTitle = url.length > 80 ? url.slice(0, 80) + "…" : url;
          const isActing = !!resuming[item.run_id];

          return (
            <div key={item.run_id} className="review-card">
              <div
                className="review-sev-bar"
                style={{ background: sevColor(sev) }}
              />
              <div className="review-card-body">
                <div className="review-card-header">
                  <div>
                    <div className="review-card-title">{displayTitle}</div>
                    <div className="flex items-center gap-8" style={{ marginTop: 6 }}>
                      <SeverityBadge severity={sev} />
                      {primary?.urgency && (
                        <span className="badge badge-info" style={{ textTransform: "capitalize" }}>
                          {primary.urgency.replace(/_/g, " ")}
                        </span>
                      )}
                    </div>
                  </div>
                  <span style={{ fontSize: 12, color: "var(--text-dim)", whiteSpace: "nowrap", flexShrink: 0 }}>
                    {timeAgo(item.queued_at)}
                  </span>
                </div>

                {rationale && (
                  <div className="review-rationale">{rationale}</div>
                )}

                {primary?.impact_categories && primary.impact_categories.length > 0 && (
                  <div className="flex gap-6 flex-wrap" style={{ marginBottom: 12 }}>
                    {primary.impact_categories.map((cat) => (
                      <span key={cat} className="impact-chip">{cat.replace(/_/g, " ")}</span>
                    ))}
                  </div>
                )}

                {actionErr[item.run_id] && (
                  <div className="error-banner" style={{ marginBottom: 12 }}>
                    {actionErr[item.run_id]}
                  </div>
                )}

                <div className="review-card-footer">
                  {confidence != null && (
                    <div className="confidence-chip">
                      <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                        <circle cx="6" cy="6" r="5" stroke="currentColor" strokeWidth="1.3" />
                        <path d="M6 4v2.5L7.5 8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                      </svg>
                      {Math.round(confidence * 100)}% confidence
                    </div>
                  )}
                  <div style={{ flex: 1 }} />
                  <button
                    className="btn btn-danger btn-sm"
                    type="button"
                    disabled={isActing}
                    onClick={() => void resumeRun(item.run_id, "reject")}
                  >
                    {resuming[item.run_id] === "overriding" ? (
                      <><div className="spinner spinner-sm" />Overriding…</>
                    ) : "Override"}
                  </button>
                  <button
                    className="btn btn-approve btn-sm"
                    type="button"
                    disabled={isActing}
                    onClick={() => void resumeRun(item.run_id, "approve")}
                  >
                    {resuming[item.run_id] === "approving" ? (
                      <><div className="spinner spinner-sm" />Approving…</>
                    ) : (
                      <>
                        <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                          <path d="M2.5 6l3 3 4.5-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                        Approve
                      </>
                    )}
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
