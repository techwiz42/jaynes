import { useState, type FormEvent } from "react";
import { deleteAccount } from "./api";

/** The signed-in user's account: who they are and, behind a password, deleting it with everything saved under it. */
export function AccountPanel({ email, onClose, onAccountDeleted }: { email: string; onClose: () => void; onAccountDeleted: () => void }) {
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState("");

  const removeAccount = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    setProblem(null);
    try {
      await deleteAccount(password);
      onAccountDeleted();
    } catch (error) {
      setProblem((error as Error).message);
    }
  };

  return (
    <section className="account-panel" aria-label="Account">
      <div className="account-head">
        <h2>Account</h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="hint">Signed in as {email}. Only you can see your conversations.</p>
      {problem && (
        <p className="problem" role="alert">
          {problem}
        </p>
      )}
      {confirming ? (
        <form onSubmit={(event) => void removeAccount(event)}>
          <p className="hint">This deletes your account and every saved conversation. It cannot be undone. Enter your password to confirm.</p>
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" aria-label="Password" required />
          <button type="submit" className="danger" disabled={!password}>
            Delete my account and all my data
          </button>
          <button
            type="button"
            onClick={() => {
              setConfirming(false);
              setPassword("");
            }}
          >
            Cancel
          </button>
        </form>
      ) : (
        <button type="button" className="danger" onClick={() => setConfirming(true)}>
          Delete my account…
        </button>
      )}
    </section>
  );
}
