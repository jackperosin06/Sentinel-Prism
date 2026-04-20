/** Parse FastAPI JSON error bodies into short user-facing strings. */

export async function readErrorMessage(r: Response): Promise<string> {
  try {
    const ct = r.headers.get("content-type") ?? "";
    if (ct.includes("application/json")) {
      const j = (await r.json()) as { detail?: string | { msg?: string }[] };
      if (typeof j.detail === "string") return j.detail;
      if (Array.isArray(j.detail) && j.detail[0]?.msg) return j.detail[0].msg;
    }
  } catch {
    /* fall through */
  }
  return `Request failed (${r.status} ${r.statusText})`;
}
