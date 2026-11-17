import { useEffect, useState } from "react";
import { adminUsage, type UsageRow } from "./api";

export function formatLastLogin(seconds: number | null): string {
  return seconds === null ? "never" : new Date(seconds * 1000).toLocaleString();
}

/** One row per account: who, how much they have used (estimated tokens), how many times they have logged in and when they last did. Nothing from a conversation. */
export function UsageTable({ rows }: { rows: UsageRow[] }) {
  return (
    <table className="usage-table">
      <thead>
        <tr>
          <th>Account</th>
          <th className="num">Tokens used</th>
          <th className="num">Logins</th>
          <th>Last login</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.email}>
            <td>{row.email}</td>
            <td className="num">{row.tokens.toLocaleString()}</td>
            <td className="num">{row.login_count.toLocaleString()}</td>
            <td>{formatLastLogin(row.last_login_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The owner's usage page, over the chat. The server only answers the accounts it lists as admins. */
export function UsagePage({ onClose }: { onClose: () => void }) {
  const [rows, setRows] = useState<UsageRow[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    adminUsage().then(setRows, (error: unknown) => setProblem((error as Error).message));
  }, []);
  return (
    <div className="overlay" role="dialog" aria-label="Usage">
      <section className="usage-page">
        <div className="account-head">
          <h2>Usage</h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="hint">Tokens are estimated (a quarter of the characters sent and received). Logins before this page existed are counted from the sessions that had not yet expired.</p>
        {problem && (
          <p className="problem" role="alert">
            {problem}
          </p>
        )}
        {rows === null && !problem && <p className="hint">Loading…</p>}
        {rows !== null && <UsageTable rows={rows} />}
      </section>
    </div>
  );
}
