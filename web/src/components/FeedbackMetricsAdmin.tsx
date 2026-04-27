import { useCallback, useEffect, useId, useState } from "react";

import { readErrorMessage } from "../httpErrors";

type MetricsResponse = {
  since: string | null;
  until: string | null;
  kind_counts: Record<string, number>;
  kind_percent: Record<string, number>;
  total_feedback: number;
  human_review_approved: number;
  human_review_rejected: number;
  human_review_overridden: number;
  human_review_decisions_total: number;
  human_review_override_rate: number | null;
};

type Props = {
  apiBase: string;
  token: string;
  onUnauthorized: () => void;
};

export function FeedbackMetricsAdmin({ apiBase, token, onUnauthorized }: Props) {
  const sectionId = useId();
  const [data, setData] = useState<MetricsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    setLoading(true);
    try {
      const r = await fetch(`${apiBase}/admin/feedback-metrics`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 401) {
        setData(null);
        setErr("Session expired. Log in again.");
        onUnauthorized();
        return;
      }
      if (r.status === 403) {
        setData(null);
        setErr("Feedback metrics are available to admin accounts only.");
        return;
      }
      if (!r.ok) {
        setData(null);
        setErr(await readErrorMessage(r));
        return;
      }
      setData((await r.json()) as MetricsResponse);
    } catch (e) {
      setData(null);
      setErr(e instanceof Error ? e.message : "Failed to load feedback metrics.");
    } finally {
      setLoading(false);
    }
  }, [apiBase, onUnauthorized, token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function downloadExport() {
    try {
      const r = await fetch(`${apiBase}/admin/feedback-metrics/export`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 401) {
        onUnauthorized();
        return;
      }
      if (!r.ok) {
        setErr(await readErrorMessage(r));
        return;
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "feedback_metrics.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Export failed.");
    }
  }

  return (
    <section
      style={{
        marginTop: "2rem",
        padding: 16,
        background: "#f6f6f6",
        border: "1px solid #ccc",
        borderRadius: 4,
      }}
      aria-labelledby={sectionId}
    >
      <h2 id={sectionId} style={{ marginTop: 0 }}>
        Feedback metrics
      </h2>
      <p style={{ fontSize: "0.9rem", color: "#444" }}>
        User feedback by category and human review outcomes (FR28). Data uses the same time window
        for both tables when you add date filters; currently all time.
      </p>
      {loading ? (
        <p aria-live="polite">Loading…</p>
      ) : err ? (
        <p role="alert" style={{ color: "#7a1f1f" }}>
          {err}
        </p>
      ) : data ? (
        <>
          <div style={{ marginBottom: 12 }}>
            <button type="button" onClick={() => void downloadExport()} style={{ fontWeight: 600 }}>
              Export CSV
            </button>
          </div>
          <h3 style={{ fontSize: "1rem" }}>User feedback (category distribution)</h3>
          <p style={{ fontSize: "0.88rem", color: "#333" }} aria-live="polite">
            Total feedback rows: <strong>{data.total_feedback}</strong>
            {data.since || data.until
              ? ` (window: ${data.since ?? "—"} → ${data.until ?? "—"})`
              : " (all time)"}
          </p>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              background: "#fff",
              fontSize: "0.9rem",
            }}
          >
            <caption className="visually-hidden" style={{ captionSide: "top", textAlign: "left" }}>
              Counts and percentages for each user feedback kind
            </caption>
            <thead>
              <tr style={{ borderBottom: "2px solid #999" }}>
                <th scope="col" style={{ textAlign: "left", padding: "6px 8px" }}>
                  Category
                </th>
                <th scope="col" style={{ textAlign: "right", padding: "6px 8px" }}>
                  Count
                </th>
                <th scope="col" style={{ textAlign: "right", padding: "6px 8px" }}>
                  % of feedback
                </th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(data.kind_counts)
                .sort()
                .map((k) => (
                  <tr key={k} style={{ borderBottom: "1px solid #ddd" }}>
                    <td style={{ padding: "6px 8px" }}>{k}</td>
                    <td style={{ textAlign: "right", padding: "6px 8px" }}>{data.kind_counts[k]}</td>
                    <td style={{ textAlign: "right", padding: "6px 8px" }}>
                      {data.kind_percent[k]?.toFixed(2) ?? "0.00"}%
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>

          <h3 style={{ fontSize: "1rem", marginTop: "1.5rem" }}>Human review (audit)</h3>
          <p style={{ fontSize: "0.88rem", color: "#333" }} aria-live="polite">
            Review override rate:{" "}
            <strong>
              {data.human_review_override_rate === null
                ? "— (no review decisions in window)"
                : `${(data.human_review_override_rate * 100).toFixed(2)}%`}
            </strong>{" "}
            ({data.human_review_overridden} overridden / {data.human_review_decisions_total} decisions
            total)
          </p>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              background: "#fff",
              fontSize: "0.9rem",
              marginTop: 8,
            }}
          >
            <thead>
              <tr style={{ borderBottom: "2px solid #999" }}>
                <th scope="col" style={{ textAlign: "left", padding: "6px 8px" }}>
                  Outcome
                </th>
                <th scope="col" style={{ textAlign: "right", padding: "6px 8px" }}>
                  Count
                </th>
              </tr>
            </thead>
            <tbody>
              <tr style={{ borderBottom: "1px solid #ddd" }}>
                <td style={{ padding: "6px 8px" }}>human_review_approved</td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>
                  {data.human_review_approved}
                </td>
              </tr>
              <tr style={{ borderBottom: "1px solid #ddd" }}>
                <td style={{ padding: "6px 8px" }}>human_review_rejected</td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>
                  {data.human_review_rejected}
                </td>
              </tr>
              <tr style={{ borderBottom: "1px solid #ddd" }}>
                <td style={{ padding: "6px 8px" }}>human_review_overridden</td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>
                  {data.human_review_overridden}
                </td>
              </tr>
            </tbody>
          </table>
        </>
      ) : null}
    </section>
  );
}
