import { useEffect, useMemo, useState } from "react";

import { readErrorMessage } from "../httpErrors";

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
  last_poll_failure:
    | { at: string; reason: string; error_class: string }
    | null;
};

type Props = {
  apiBase: string;
  token: string;
  onUnauthorized: () => void;
};

function fmtRate(v: number | null): string {
  if (v === null || Number.isNaN(v)) return "—";
  return `${Math.round(v * 100)}%`;
}

function fmtDt(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString();
}

function isFailing(row: SourceMetric): boolean {
  if (row.last_poll_failure) return true;
  if (row.poll_attempts_failed > 0) return true;
  if (row.error_rate !== null && row.error_rate > 0) return true;
  return false;
}

export function OpsDashboard({ apiBase, token, onUnauthorized }: Props) {
  const [data, setData] = useState<SourceMetric[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [failingOnly, setFailingOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);

  const PAGE_SIZE = 200;

  useEffect(() => {
    const ctrl = new AbortController();
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const r = await fetch(`${apiBase}/ops/source-metrics?limit=${PAGE_SIZE}&offset=0`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (r.status === 401) {
          onUnauthorized();
          return;
        }
        if (r.status === 403) {
          setErr("You do not have permission to view Ops metrics.");
          setData([]);
          setHasMore(false);
          setOffset(0);
          return;
        }
        if (!r.ok) {
          setErr(await readErrorMessage(r));
          setData([]);
          setHasMore(false);
          setOffset(0);
          return;
        }
        const rows = (await r.json()) as SourceMetric[];
        setData(rows);
        setOffset(rows.length);
        setHasMore(rows.length === PAGE_SIZE);
      } catch (e) {
        if ((e as { name?: string }).name === "AbortError") return;
        setErr(e instanceof Error ? e.message : "Network error loading ops metrics.");
        setData([]);
        setHasMore(false);
        setOffset(0);
      } finally {
        setLoading(false);
      }
    })();
    return () => ctrl.abort();
  }, [apiBase, token, onUnauthorized]);

  async function loadMore() {
    if (loading || !hasMore) return;
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(`${apiBase}/ops/source-metrics?limit=${PAGE_SIZE}&offset=${offset}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 401) {
        onUnauthorized();
        return;
      }
      if (r.status === 403) {
        setErr("You do not have permission to view Ops metrics.");
        setHasMore(false);
        return;
      }
      if (!r.ok) {
        setErr(await readErrorMessage(r));
        setHasMore(false);
        return;
      }
      const rows = (await r.json()) as SourceMetric[];
      setData((xs) => [...xs, ...rows]);
      setOffset((x) => x + rows.length);
      setHasMore(rows.length === PAGE_SIZE);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Network error loading ops metrics.");
      setHasMore(false);
    } finally {
      setLoading(false);
    }
  }

  const filtered = useMemo(() => {
    const xs = data;
    if (!failingOnly) return xs;
    return xs.filter(isFailing);
  }, [data, failingOnly]);

  return (
    <section
      style={{
        marginTop: "2rem",
        padding: 16,
        background: "#f9f9f9",
        border: "1px solid #ccc",
        borderRadius: 4,
      }}
      aria-labelledby="ops-heading"
    >
      <h2 id="ops-heading">Ops (NFR8/NFR9)</h2>
      <p style={{ marginTop: 0, color: "#444" }}>
        Per-source ingestion health and links to operator tools.
      </p>
      <p style={{ marginTop: 0 }}>
        Jump to: <a href="#audit-search">Audit search</a> ·{" "}
        <a href="#workflow-replay">Workflow replay</a>
      </p>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <label>
          <input
            type="checkbox"
            checked={failingOnly}
            onChange={(e) => setFailingOnly(e.target.checked)}
          />{" "}
          Show failing sources only
        </label>
      </div>

      {loading && (
        <p role="status" aria-live="polite">
          Loading ops metrics…
        </p>
      )}
      {err && (
        <p role="alert" style={{ color: "crimson" }}>
          {err}
        </p>
      )}

      {!loading && !err && (
        <>
          <p style={{ fontSize: "0.9rem", color: "#444" }}>
            Showing {filtered.length} of {data.length} loaded sources.
          </p>
          {!failingOnly && hasMore && (
            <p style={{ fontSize: "0.9rem", color: "#444", marginTop: 0 }}>
              Showing the first {data.length} sources. Load more to see additional sources.
            </p>
          )}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {!failingOnly && hasMore ? (
              <button type="button" onClick={loadMore} disabled={loading}>
                Load more
              </button>
            ) : null}
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {[
                    "Source",
                    "Success rate",
                    "Error rate",
                    "Last success",
                    "Last failure",
                    "Last latency (ms)",
                    "Items ingested",
                    "Last failure (reason)",
                  ].map((h) => (
                    <th
                      key={h}
                      scope="col"
                      style={{
                        textAlign: "left",
                        borderBottom: "1px solid #ccc",
                        padding: "8px 6px",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={8} style={{ padding: "10px 6px", color: "#444" }}>
                      No sources match the current filter.
                    </td>
                  </tr>
                ) : (
                  filtered.map((s) => (
                    <tr key={s.source_id} style={{ borderBottom: "1px solid #eee" }}>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        <strong>{s.name}</strong>
                        <div style={{ fontSize: "0.8rem", color: "#666" }}>{s.source_id}</div>
                      </td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        {fmtRate(s.success_rate)}
                      </td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        {fmtRate(s.error_rate)}
                      </td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        {fmtDt(s.last_success_at)}
                      </td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        {fmtDt(s.last_failure_at)}
                      </td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        {s.last_success_latency_ms ?? "—"}
                      </td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        {s.items_ingested_total}
                      </td>
                      <td style={{ padding: "8px 6px" }}>
                        {s.last_poll_failure ? (
                          <>
                            <strong>{s.last_poll_failure.error_class}</strong>:{" "}
                            {s.last_poll_failure.reason}
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

