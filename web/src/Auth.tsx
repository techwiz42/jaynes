import { useState, type FormEvent } from "react";
import { login, register, type Account } from "./api";
import { BrainIcon } from "./BrainIcon";

/** Log in or register. Registration is open: anyone who can reach this page may make an account, and each account sees only its own conversations and memory. */
export function AuthForm({ onAuthenticated }: { onAuthenticated: (account: Account) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    try {
      onAuthenticated(await (mode === "login" ? login : register)(email, password));
    } catch (error) {
      setProblem((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <h1 className="brand">
        <BrainIcon size={34} />
        jaynes
      </h1>
      <form onSubmit={(event) => void submit(event)} aria-label={mode === "login" ? "Log in" : "Create an account"}>
        <label>
          Email
          <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required autoFocus />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            minLength={mode === "register" ? 10 : undefined}
            required
          />
        </label>
        {mode === "register" && <p className="hint">At least 10 characters. Use a password you use nowhere else.</p>}
        {problem && (
          <p className="problem" role="alert">
            {problem}
          </p>
        )}
        <button type="submit" disabled={busy || !email || !password}>
          {mode === "login" ? "Log in" : "Create account"}
        </button>
      </form>
      <button type="button" className="link" onClick={() => { setMode(mode === "login" ? "register" : "login"); setProblem(null); }}>
        {mode === "login" ? "New here? Create an account" : "Have an account? Log in"}
      </button>
    </div>
  );
}
