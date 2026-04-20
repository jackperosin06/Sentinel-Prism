import { useEffect, useState } from "react";

import { readErrorMessage } from "../httpErrors";

export type DashboardSummary = {
  severity_counts: Record<string, number>;
  new_items_count: number;
  new_items_window_hours: number;
  review_queue_count: number;
  top_sources: {
    source_id: string;
    name: string;
    metric: string;
    value: number;
  }[];
  top_sources_metric: string;
};

type Props = {
  apiBase: string;
  token: string;
};

export function Dashboard({ apiBase, token }: Props) {
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const r = await fetch(`${apiBase}/dashboard/summary`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (r.status === 401) {
          setErr("Session expired. Please log in again.");
          setData(null);
          return;
        }
        if (!r.ok) {
          setErr(await readErrorMessage(r));
          setData(null);
          return;
        }
        setData((await r.json()) as DashboardSummary);
      } catch (e) {
        if ((e as { name?: string }).name === "AbortError") return;
        setErr(e instanceof Error ? e.message : "Network error loading dashboard.");
        setData(null);
      } finally {
        setLoading(false);
      }
    })();
    return () => ctrl.abort();
  }, [apiBase, token]);

  const headingId = "dashboard-heading";

  return (
    <section role="region" aria-labelledby={headingId}>
      <h2 id={headingId}>Overview</h2>
      {loading && (
        <p role="status" aria-live="polite">
          Loading dashboard…
        </p>
      )}
      {err && (
        <p role="alert" style={{ color: "crimson" }}>
          {err}
        </p>
      )}
      {!loading && !err && data && (
        <div
          style={{
            display: "grid",
            gap: "1rem",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          }}
        >
          <article aria-labelledby="widget-severity">
            <h3 id="widget-severity">Severity distribution</h3>
            <ul style={{ margin: 0, paddingLeft: "1.2rem" }}>
              {Object.entries(data.severity_counts).length === 0 ? (
                <li>No classified items in audit snapshot yet.</li>
              ) : (
                Object.entries(data.severity_counts).map(([k, v]) => (
                  <li key={k}>
                    <strong>{k}</strong>: {v}
                  </li>
                ))
              )}
            </ul>
          </article>
          <article aria-labelledby="widget-new">
            <h3 id="widget-new">New items</h3>
            <p style={{ fontSize: "1.5rem", margin: 0 }}>{data.new_items_count}</p>
            <p style={{ fontSize: "0.85rem", color: "#444", margin: "0.25rem 0 0" }}>
              Recorded in the last {data.new_items_window_hours} hour
              {data.new_items_window_hours === 1 ? "" : "s"} (UTC).
            </p>
          </article>
          <article aria-labelledby="widget-review">
            <h3 id="widget-review">Human review backlog</h3>
            <p style={{ fontSize: "1.5rem", margin: 0 }}>{data.review_queue_count}</p>
          </article>
          <article aria-labelledby="widget-sources">
            <h3 id="widget-sources">Top sources</h3>
            <p style={{ fontSize: "0.85rem", color: "#444", marginTop: 0 }}>
              By {data.top_sources_metric}
            </p>
            <ol style={{ margin: 0, paddingLeft: "1.2rem" }}>
              {data.top_sources.length === 0 ? (
                <li>No sources registered.</li>
              ) : (
                data.top_sources.map((s) => (
                  <li key={s.source_id}>
                    <strong>{s.name}</strong> — {s.value}
                  </li>
                ))
              )}
            </ol>
          </article>
        </div>
      )}
    </section>
  );
}
