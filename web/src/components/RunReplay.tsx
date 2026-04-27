import { type FormEvent, useId, useState } from "react";

import { readErrorMessage } from "../httpErrors";

type ReplayResponse = {
  original_run_id: string;
  replay_run_id: string;
  replayed_nodes: string[];
  started_at: string;
  finished_at: string;
  status: string;
  errors: { step: string; message: string; error_class: string; detail: string | null }[];
};

type Props = {
  apiBase: string;
  token: string;
  onUnauthorized: () => void;
};

export function RunReplay({ apiBase, token, onUnauthorized }: Props) {
  const headingId = useId();

  const [runId, setRunId] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [data, setData] = useState<ReplayResponse | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setLoading(true);
    setData(null);
    const rid = runId.trim();
    if (!rid) {
      setErr("Run id is required.");
      setLoading(false);
      return;
    }
    try {
      const r = await fetch(`${apiBase}/runs/${encodeURIComponent(rid)}/replay`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ from_node: "classify", to_node: "route" }),
      });
      if (r.status === 401) {
        onUnauthorized();
        return;
      }
      if (r.status === 403) {
        setErr("You do not have permission to replay runs.");
        return;
      }
      if (!r.ok) {
        setErr(await readErrorMessage(r));
        return;
      }
      setData((await r.json()) as ReplayResponse);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Replay request failed.");
    } finally {
      setLoading(false);
    }
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
      <h2 id="workflow-replay">Workflow replay (FR35)</h2>
      <p style={{ fontSize: "0.9rem", color: "#444" }}>
        Operator tool: replays a tail segment from persisted graph state. Replay is non-destructive and does not send notifications.
      </p>

      <form onSubmit={onSubmit} style={{ marginBottom: 16 }}>
        <label>
          Run id
          <input
            value={runId}
            onChange={(e) => setRunId(e.target.value)}
            autoComplete="off"
            style={{ width: "100%", boxSizing: "border-box", marginTop: 4 }}
          />
        </label>
        <div style={{ marginTop: 12 }}>
          <button type="submit" disabled={loading}>
            {loading ? "Replaying…" : "Replay classify → route"}
          </button>
        </div>
      </form>

      {err && (
        <p style={{ color: "crimson", whiteSpace: "pre-wrap" }} role="alert">
          {err}
        </p>
      )}

      {data && (
        <div style={{ fontSize: "0.9rem" }}>
          <p>
            <strong>Status:</strong> {data.status}
          </p>
          <p>
            <strong>Original run:</strong> <code>{data.original_run_id}</code>
            <br />
            <strong>Replay run:</strong> <code>{data.replay_run_id}</code>
          </p>
          <p>
            <strong>Nodes:</strong> {data.replayed_nodes.join(" → ")}
          </p>
          {data.errors.length > 0 && (
            <>
              <p>
                <strong>Errors:</strong>
              </p>
              <ul>
                {data.errors.map((e, idx) => (
                  <li key={idx}>
                    <code>{e.step}</code> — {e.message} ({e.error_class})
                  </li>
                ))}
              </ul>
            </>
          )}
          <details style={{ marginTop: 12 }}>
            <summary>Raw response</summary>
            <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(data, null, 2)}</pre>
          </details>
        </div>
      )}
    </section>
  );
}

