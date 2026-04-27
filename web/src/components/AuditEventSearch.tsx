import { type FormEvent, useCallback, useId, useState } from "react";

import { readErrorMessage } from "../httpErrors";

type AuditItem = {
  id: string;
  created_at: string;
  run_id: string;
  action: string;
  source_id: string | null;
  actor_user_id: string | null;
  metadata: Record<string, unknown> | null;
};

type SearchResponse = {
  items: AuditItem[];
  total: number;
  limit: number;
  offset: number;
};

type Props = {
  apiBase: string;
  token: string;
  onUnauthorized: () => void;
};

const PAGE_SIZE = 50;

/** Example actions for datalist; free text allowed (API validates). */
const ACTION_HINTS = [
  "pipeline_scout_completed",
  "pipeline_normalize_completed",
  "pipeline_classify_completed",
  "human_review_approved",
  "human_review_rejected",
  "human_review_overridden",
  "briefing_generated",
  "routing_applied",
  "routing_config_changed",
  "classification_config_changed",
  "golden_set_config_changed",
];

function buildQuery(params: URLSearchParams): string {
  const q = params.toString();
  return q ? `?${q}` : "";
}

export function AuditEventSearch({ apiBase, token, onUnauthorized }: Props) {
  const headingId = useId();
  const actionListId = useId();

  const [runId, setRunId] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [actorUserId, setActorUserId] = useState("");
  const [createdAfter, setCreatedAfter] = useState("");
  const [createdBefore, setCreatedBefore] = useState("");
  const [action, setAction] = useState("");
  const [normalizedUpdateId, setNormalizedUpdateId] = useState("");

  const [data, setData] = useState<SearchResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const fetchPage = useCallback(
    async (nextOffset: number) => {
      setErr(null);
      setLoading(true);
      const p = new URLSearchParams();
      p.set("limit", String(PAGE_SIZE));
      p.set("offset", String(nextOffset));

      if (runId.trim()) p.set("run_id", runId.trim());
      if (sourceId.trim()) p.set("source_id", sourceId.trim());
      if (actorUserId.trim()) p.set("actor_user_id", actorUserId.trim());
      if (createdAfter.trim()) p.set("created_after", createdAfter.trim());
      if (createdBefore.trim()) p.set("created_before", createdBefore.trim());
      if (action.trim()) p.set("action", action.trim());
      if (normalizedUpdateId.trim()) p.set("normalized_update_id", normalizedUpdateId.trim());

      try {
        const r = await fetch(`${apiBase}/audit-events${buildQuery(p)}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (r.status === 401) {
          setData(null);
          onUnauthorized();
          return;
        }
        if (r.status === 403) {
          setData(null);
          setErr("You do not have permission to search audit events.");
          return;
        }
        if (!r.ok) {
          setData(null);
          setErr(await readErrorMessage(r));
          return;
        }
        setData((await r.json()) as SearchResponse);
        setOffset(nextOffset);
      } catch (e) {
        setData(null);
        setErr(e instanceof Error ? e.message : "Audit search failed.");
      } finally {
        setLoading(false);
      }
    },
    [
      apiBase,
      token,
      onUnauthorized,
      runId,
      sourceId,
      actorUserId,
      createdAfter,
      createdBefore,
      action,
      normalizedUpdateId,
    ],
  );

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void fetchPage(0);
  }

  function nextPage() {
    if (!data || offset + data.items.length >= data.total) return;
    void fetchPage(offset + PAGE_SIZE);
  }

  function prevPage() {
    if (offset <= 0) return;
    void fetchPage(Math.max(0, offset - PAGE_SIZE));
  }

  return (
    <section
      style={{
        marginTop: "2rem",
        padding: 16,
        background: "#f9f9f9",
        border: "1px solid #ccc",
        borderRadius: 4,
      }}
      aria-labelledby={headingId}
    >
      <h2 id="audit-search">Audit search (FR34)</h2>
      <p style={{ fontSize: "0.9rem", color: "#444" }}>
        Query append-only pipeline and configuration audit rows. Datetime filters must include a timezone
        offset (e.g. <code>2026-04-27T00:00:00+00:00</code>). If you filter by normalized update id and the
        update has no <code>run_id</code>, results use that update&apos;s source and a ±24h window around its{" "}
        <code>created_at</code>.
      </p>

      <form onSubmit={onSubmit} style={{ marginBottom: 16 }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: 12,
            marginBottom: 12,
          }}
        >
          <label>
            Run id
            <input
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
              autoComplete="off"
              style={{ width: "100%", boxSizing: "border-box" }}
            />
          </label>
          <label>
            Source id
            <input
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value)}
              autoComplete="off"
              style={{ width: "100%", boxSizing: "border-box" }}
            />
          </label>
          <label>
            Actor user id
            <input
              value={actorUserId}
              onChange={(e) => setActorUserId(e.target.value)}
              autoComplete="off"
              style={{ width: "100%", boxSizing: "border-box" }}
            />
          </label>
          <label>
            Normalized update id
            <input
              value={normalizedUpdateId}
              onChange={(e) => setNormalizedUpdateId(e.target.value)}
              autoComplete="off"
              style={{ width: "100%", boxSizing: "border-box" }}
            />
          </label>
          <label>
            Action
            <input
              value={action}
              onChange={(e) => setAction(e.target.value)}
              list={actionListId}
              autoComplete="off"
              style={{ width: "100%", boxSizing: "border-box" }}
            />
            <datalist id={actionListId}>
              {ACTION_HINTS.map((a) => (
                <option key={a} value={a} />
              ))}
            </datalist>
          </label>
          <label>
            Created after (inclusive)
            <input
              value={createdAfter}
              onChange={(e) => setCreatedAfter(e.target.value)}
              placeholder="2026-04-27T00:00:00+00:00"
              autoComplete="off"
              style={{ width: "100%", boxSizing: "border-box" }}
            />
          </label>
          <label>
            Created before (inclusive)
            <input
              value={createdBefore}
              onChange={(e) => setCreatedBefore(e.target.value)}
              placeholder="2026-04-28T23:59:59+00:00"
              autoComplete="off"
              style={{ width: "100%", boxSizing: "border-box" }}
            />
          </label>
        </div>
        <button type="submit" disabled={loading}>
          {loading ? "Loading…" : "Search"}
        </button>
      </form>

      {err && (
        <p style={{ color: "crimson", whiteSpace: "pre-wrap" }} role="alert">
          {err}
        </p>
      )}

      {data && (
        <>
          <p style={{ fontSize: "0.9rem" }}>
            <strong>{data.total}</strong> matching row{data.total === 1 ? "" : "s"} — showing offset{" "}
            <strong>{data.offset}</strong> (page size {data.limit})
          </p>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid #999" }}>
                  <th style={{ padding: "6px 8px" }}>Created</th>
                  <th style={{ padding: "6px 8px" }}>Action</th>
                  <th style={{ padding: "6px 8px" }}>Run id</th>
                  <th style={{ padding: "6px 8px" }}>Source</th>
                  <th style={{ padding: "6px 8px" }}>Actor</th>
                  <th style={{ padding: "6px 8px" }}>Metadata</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={row.id} style={{ borderBottom: "1px solid #ddd", verticalAlign: "top" }}>
                    <td style={{ padding: "6px 8px", whiteSpace: "nowrap" }}>{row.created_at}</td>
                    <td style={{ padding: "6px 8px" }}>{row.action}</td>
                    <td style={{ padding: "6px 8px", wordBreak: "break-all" }}>{row.run_id}</td>
                    <td style={{ padding: "6px 8px", wordBreak: "break-all" }}>
                      {row.source_id ?? "—"}
                    </td>
                    <td style={{ padding: "6px 8px", wordBreak: "break-all" }}>
                      {row.actor_user_id ?? "—"}
                    </td>
                    <td style={{ padding: "6px 8px", fontFamily: "monospace", fontSize: "0.75rem" }}>
                      <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                        {row.metadata ? JSON.stringify(row.metadata, null, 0).slice(0, 400) : "—"}
                        {row.metadata && JSON.stringify(row.metadata).length > 400 ? "…" : ""}
                      </pre>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
            <button type="button" onClick={prevPage} disabled={loading || offset <= 0}>
              Previous page
            </button>
            <button
              type="button"
              onClick={nextPage}
              disabled={loading || !data || offset + data.items.length >= data.total}
            >
              Next page
            </button>
          </div>
        </>
      )}
    </section>
  );
}
