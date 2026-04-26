import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { readErrorMessage } from "../httpErrors";

export type ExplorerListItem = {
  id: string;
  raw_capture_id: string;
  source_id: string;
  source_name: string;
  jurisdiction: string;
  title: string | null;
  published_at: string | null;
  item_url: string;
  document_type: string;
  body_snippet: string | null;
  run_id: string | null;
  created_at: string;
  explorer_status: "in_human_review" | "briefed" | "processed";
  derived_severity: string | null;
};

type ListResponse = {
  items: ExplorerListItem[];
  total: number;
  limit: number;
  offset: number;
  sort: string;
  default_sort: string;
};

type DetailResponse = {
  normalized: Record<string, unknown>;
  raw_payload: Record<string, unknown>;
  classification: {
    severity: string | null;
    impact_categories: string[];
    confidence: number | null;
  } | null;
};

type Props = {
  apiBase: string;
  token: string;
  /** From `/auth/me`; when null, feedback panel is not shown. */
  userRole: string | null;
};

const PAGE_SIZE = 50;
const MAX_FEEDBACK_COMMENT = 10_000;

function statusLabel(s: ExplorerListItem["explorer_status"]): string {
  switch (s) {
    case "in_human_review":
      return "In human review";
    case "briefed":
      return "Briefed";
    default:
      return "Processed";
  }
}

const FEEDBACK_KINDS = [
  { value: "incorrect_relevance", label: "Incorrect relevance" },
  { value: "incorrect_severity", label: "Incorrect severity" },
  { value: "false_positive", label: "False positive" },
  { value: "false_negative", label: "False negative" },
] as const;

export function UpdateExplorer({ apiBase, token, userRole }: Props) {
  const headingId = useId();
  const [items, setItems] = useState<ExplorerListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loadingList, setLoadingList] = useState(true);
  const [listErr, setListErr] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailResponse | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailErr, setDetailErr] = useState<string | null>(null);

  const [jurisdiction, setJurisdiction] = useState("");
  const [documentType, setDocumentType] = useState("");
  const [severity, setSeverity] = useState("");
  const [sourceName, setSourceName] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [publishedFrom, setPublishedFrom] = useState("");
  const [publishedTo, setPublishedTo] = useState("");
  const [status, setStatus] = useState("");
  const [includeUnknownSeverity, setIncludeUnknownSeverity] = useState(false);
  const [sort, setSort] = useState("created_at_desc");
  const [offset, setOffset] = useState(0);
  const [listRefresh, setListRefresh] = useState(0);
  const [feedbackKind, setFeedbackKind] = useState<string>(FEEDBACK_KINDS[0].value);
  const [feedbackComment, setFeedbackComment] = useState("");
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [feedbackErr, setFeedbackErr] = useState<string | null>(null);
  const [feedbackOk, setFeedbackOk] = useState<string | null>(null);
  const latestSelectedId = useRef<string | null>(null);
  latestSelectedId.current = selectedId;
  const feedbackKindId = useId();
  const feedbackCommentId = useId();

  useEffect(() => {
    const ctrl = new AbortController();
    (async () => {
      setLoadingList(true);
      setListErr(null);
      const params = new URLSearchParams();
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(offset));
      params.set("sort", sort);
      if (jurisdiction.trim()) params.set("jurisdiction", jurisdiction.trim());
      if (documentType.trim()) params.set("document_type", documentType.trim());
      if (severity.trim()) params.set("severity", severity.trim());
      if (severity.trim() && includeUnknownSeverity) params.set("include_unknown_severity", "true");
      if (sourceName.trim()) params.set("source_name_contains", sourceName.trim());
      if (createdFrom) params.set("created_from", createdFrom);
      if (createdTo) params.set("created_to", createdTo);
      if (publishedFrom) params.set("published_from", publishedFrom);
      if (publishedTo) params.set("published_to", publishedTo);
      if (status) params.set("explorer_status", status);
      try {
        const r = await fetch(`${apiBase}/updates?${params}`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (r.status === 401) {
          setListErr("Session expired. Please log in again.");
          setItems([]);
          setTotal(0);
          setSelectedId(null);
          return;
        }
        if (!r.ok) {
          setListErr(await readErrorMessage(r));
          setItems([]);
          setTotal(0);
          setSelectedId(null);
          return;
        }
        const j = (await r.json()) as ListResponse;
        setItems(j.items);
        setTotal(j.total);
        if (!j.items.length) {
          setSelectedId(null);
        } else {
          setSelectedId((prev) =>
            prev && j.items.some((i) => i.id === prev) ? prev : j.items[0].id
          );
        }
      } catch (e) {
        if ((e as { name?: string }).name === "AbortError") return;
        setListErr(e instanceof Error ? e.message : "Network error loading updates.");
        setItems([]);
        setTotal(0);
        setSelectedId(null);
      } finally {
        if (!ctrl.signal.aborted) setLoadingList(false);
      }
    })();
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- filters refetch when user applies them; pagination refetches immediately.
  }, [apiBase, token, listRefresh, offset]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailErr(null);
      setLoadingDetail(false);
      return;
    }
    const ctrl = new AbortController();
    (async () => {
      setLoadingDetail(true);
      setDetailErr(null);
      try {
        const r = await fetch(`${apiBase}/updates/${selectedId}`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (r.status === 401) {
          setDetailErr("Session expired.");
          setDetail(null);
          return;
        }
        if (r.status === 404) {
          setDetailErr("Update not found.");
          setDetail(null);
          return;
        }
        if (!r.ok) {
          setDetailErr(await readErrorMessage(r));
          setDetail(null);
          return;
        }
        setDetail((await r.json()) as DetailResponse);
      } catch (e) {
        if ((e as { name?: string }).name === "AbortError") return;
        setDetailErr(e instanceof Error ? e.message : "Network error loading detail.");
        setDetail(null);
      } finally {
        if (!ctrl.signal.aborted) setLoadingDetail(false);
      }
    })();
    return () => ctrl.abort();
  }, [apiBase, token, selectedId]);

  useEffect(() => {
    latestSelectedId.current = selectedId;
    setFeedbackErr(null);
    setFeedbackOk(null);
    setFeedbackComment("");
    setFeedbackKind(FEEDBACK_KINDS[0].value);
    setFeedbackSubmitting(false);
  }, [selectedId]);

  const applyFilters = (e: FormEvent) => {
    e.preventDefault();
    setOffset(0);
    setListRefresh((n) => n + 1);
  };

  const canPageBack = offset > 0;
  const canPageForward = offset + items.length < total;

  const canSubmitFeedback = userRole === "analyst" || userRole === "admin";

  async function submitFeedback(e: FormEvent) {
    e.preventDefault();
    if (!selectedId) return;
    const submittedId = selectedId;
    const trimmed = feedbackComment.trim();
    if (!trimmed) {
      setFeedbackErr("Comment is required.");
      return;
    }
    setFeedbackSubmitting(true);
    setFeedbackErr(null);
    setFeedbackOk(null);
    try {
      const r = await fetch(`${apiBase}/updates/${submittedId}/feedback`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ kind: feedbackKind, comment: trimmed }),
      });
      if (latestSelectedId.current !== submittedId) return;
      if (r.status === 401) {
        setFeedbackErr("Session expired. Please log in again.");
        return;
      }
      if (r.status === 403) {
        setFeedbackErr("Your account cannot submit feedback (analyst or admin only).");
        return;
      }
      if (!r.ok) {
        setFeedbackErr(await readErrorMessage(r));
        return;
      }
      setFeedbackOk("Feedback saved.");
      setFeedbackComment("");
    } catch (err) {
      if (latestSelectedId.current === submittedId) {
        setFeedbackErr(
          err instanceof Error ? err.message : "Network error submitting feedback."
        );
      }
    } finally {
      if (latestSelectedId.current === submittedId) {
        setFeedbackSubmitting(false);
      }
    }
  }

  return (
    <section role="region" aria-labelledby={headingId}>
      <h2 id={headingId}>Update explorer</h2>
      <p style={{ fontSize: "0.9rem", color: "#444" }}>
        {total} update{total === 1 ? "" : "s"} — filters apply server-side (FR31). Default sort: newest{" "}
        <code>created_at</code> first.
      </p>

      <form
        onSubmit={applyFilters}
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "0.5rem",
          alignItems: "flex-end",
          marginBottom: "1rem",
        }}
      >
        <label>
          Created from{" "}
          <input
            type="datetime-local"
            value={createdFrom}
            onChange={(e) => setCreatedFrom(e.target.value)}
            aria-label="Filter by created from"
          />
        </label>
        <label>
          Created to{" "}
          <input
            type="datetime-local"
            value={createdTo}
            onChange={(e) => setCreatedTo(e.target.value)}
            aria-label="Filter by created to"
          />
        </label>
        <label>
          Published from{" "}
          <input
            type="datetime-local"
            value={publishedFrom}
            onChange={(e) => setPublishedFrom(e.target.value)}
            aria-label="Filter by published from"
          />
        </label>
        <label>
          Published to{" "}
          <input
            type="datetime-local"
            value={publishedTo}
            onChange={(e) => setPublishedTo(e.target.value)}
            aria-label="Filter by published to"
          />
        </label>
        <label>
          Jurisdiction{" "}
          <input
            value={jurisdiction}
            onChange={(e) => setJurisdiction(e.target.value)}
            aria-label="Filter by jurisdiction"
          />
        </label>
        <label>
          Source name{" "}
          <input
            value={sourceName}
            onChange={(e) => setSourceName(e.target.value)}
            aria-label="Filter by source name"
          />
        </label>
        <label>
          Document type{" "}
          <input
            value={documentType}
            onChange={(e) => setDocumentType(e.target.value)}
            aria-label="Filter by document type (topic facet)"
          />
        </label>
        <label>
          Severity{" "}
          <input
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
            placeholder="e.g. high"
            aria-label="Filter by derived severity"
          />
        </label>
        <label>
          Status{" "}
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by explorer status"
          >
            <option value="">Any</option>
            <option value="in_human_review">In human review</option>
            <option value="briefed">Briefed</option>
            <option value="processed">Processed</option>
          </select>
        </label>
        <label>
          Sort{" "}
          <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort updates">
            <option value="created_at_desc">Created newest first</option>
            <option value="created_at_asc">Created oldest first</option>
            <option value="published_at_desc">Published newest first</option>
            <option value="published_at_asc">Published oldest first</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={includeUnknownSeverity}
            onChange={(e) => setIncludeUnknownSeverity(e.target.checked)}
          />{" "}
          Include unknown severity
        </label>
        <button type="submit">Apply filters</button>
      </form>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
          gap: "1rem",
        }}
        className="update-explorer-grid"
      >
        <div>
          <h3 style={{ fontSize: "1rem" }}>List</h3>
          {loadingList && (
            <p role="status" aria-live="polite">
              Loading…
            </p>
          )}
          {listErr && (
            <p role="alert" style={{ color: "crimson" }}>
              {listErr}
            </p>
          )}
          {!loadingList && !listErr && (
            <>
              <ul
                role="listbox"
                aria-label="Updates"
                style={{ listStyle: "none", padding: 0, margin: 0 }}
              >
                {items.map((it) => {
                  const sevText = it.derived_severity ? `[${it.derived_severity}] ` : "";
                  const selected = it.id === selectedId;
                  return (
                    <li key={it.id} style={{ marginBottom: 4 }}>
                      <button
                        type="button"
                        role="option"
                        onClick={() => setSelectedId(it.id)}
                        aria-selected={selected}
                        style={{
                          width: "100%",
                          textAlign: "left",
                          padding: "6px 8px",
                          border: selected ? "2px solid #333" : "1px solid #ccc",
                          background: selected ? "#f4f4f4" : "#fff",
                          cursor: "pointer",
                        }}
                      >
                        <span style={{ fontWeight: 600 }}>{it.title || it.item_url}</span>
                        <br />
                        <span style={{ fontSize: "0.8rem", color: "#444" }}>
                          <span aria-hidden="true">{sevText}</span>
                          <span className="sr-only">
                            {it.derived_severity
                              ? `Severity ${it.derived_severity}. `
                              : "Severity unknown. "}
                          </span>
                          {statusLabel(it.explorer_status)} · {it.jurisdiction} · {it.document_type}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
              <p style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <button type="button" onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={!canPageBack}>
                  Previous
                </button>
                <span style={{ fontSize: "0.85rem" }}>
                  Showing {items.length ? offset + 1 : 0}–{offset + items.length} of {total}
                </span>
                <button type="button" onClick={() => setOffset(offset + PAGE_SIZE)} disabled={!canPageForward}>
                  Next
                </button>
              </p>
            </>
          )}
        </div>

        <div>
          <h3 style={{ fontSize: "1rem" }}>Detail</h3>
          {loadingDetail && (
            <p role="status" aria-live="polite">
              Loading detail…
            </p>
          )}
          {detailErr && (
            <p role="alert" style={{ color: "crimson" }}>
              {detailErr}
            </p>
          )}
          {!loadingDetail && !detailErr && detail && (
            <>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
                  gap: "0.75rem",
                }}
                className="update-explorer-detail-split"
              >
                <div>
                  <h4 style={{ fontSize: "0.9rem", margin: "0 0 0.5rem" }}>Raw capture</h4>
                  <pre
                    style={{
                      fontSize: "0.75rem",
                      overflow: "auto",
                      maxHeight: 360,
                      background: "#fafafa",
                      padding: 8,
                      border: "1px solid #ddd",
                    }}
                  >
                    {JSON.stringify(detail.raw_payload, null, 2)}
                  </pre>
                </div>
                <div>
                  <h4 style={{ fontSize: "0.9rem", margin: "0 0 0.5rem" }}>Normalized</h4>
                  <pre
                    style={{
                      fontSize: "0.75rem",
                      overflow: "auto",
                      maxHeight: 360,
                      background: "#fafafa",
                      padding: 8,
                      border: "1px solid #ddd",
                    }}
                  >
                    {JSON.stringify(detail.normalized, null, 2)}
                  </pre>
                  {detail.classification && (
                    <p style={{ fontSize: "0.85rem", marginTop: 8 }}>
                      <strong>Classification (briefing):</strong> severity{" "}
                      {detail.classification.severity ?? "—"}, categories{" "}
                      {detail.classification.impact_categories.join(", ") || "—"}, confidence{" "}
                      {detail.classification.confidence ?? "—"}
                    </p>
                  )}
                </div>
              </div>
              {userRole === "viewer" && (
                <p style={{ fontSize: "0.85rem", color: "#555", marginTop: "1rem" }}>
                  Signed in as a viewer. Submitting classification feedback requires an analyst or admin
                  account.
                </p>
              )}
              {canSubmitFeedback && (
                <form
                  onSubmit={submitFeedback}
                  style={{ marginTop: "1rem", maxWidth: 480 }}
                  aria-label="Feedback on classification"
                >
                  <h4 style={{ fontSize: "0.9rem", margin: "0 0 0.5rem" }}>Feedback</h4>
                  {feedbackErr && (
                    <p role="alert" style={{ color: "crimson", fontSize: "0.9rem" }}>
                      {feedbackErr}
                    </p>
                  )}
                  {feedbackOk && (
                    <p role="status" style={{ color: "green", fontSize: "0.9rem" }} aria-live="polite">
                      {feedbackOk}
                    </p>
                  )}
                  <div style={{ marginBottom: 8 }}>
                    <label htmlFor={feedbackKindId} style={{ display: "block", fontSize: "0.85rem" }}>
                      Category
                    </label>
                    <select
                      id={feedbackKindId}
                      value={feedbackKind}
                      onChange={(e) => setFeedbackKind(e.target.value)}
                      aria-required="true"
                    >
                      {FEEDBACK_KINDS.map((k) => (
                        <option key={k.value} value={k.value}>
                          {k.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div style={{ marginBottom: 8 }}>
                    <label htmlFor={feedbackCommentId} style={{ display: "block", fontSize: "0.85rem" }}>
                      Comment <span aria-hidden="true">(required)</span>
                    </label>
                    <textarea
                      id={feedbackCommentId}
                      value={feedbackComment}
                      onChange={(e) => setFeedbackComment(e.target.value)}
                      rows={4}
                      required
                      maxLength={MAX_FEEDBACK_COMMENT}
                      aria-required="true"
                      style={{ width: "100%", boxSizing: "border-box" }}
                    />
                  </div>
                  <button type="submit" disabled={feedbackSubmitting}>
                    {feedbackSubmitting ? "Submitting…" : "Submit feedback"}
                  </button>
                </form>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
