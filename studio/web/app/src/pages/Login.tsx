import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ArrowRight, KeyRound } from "lucide-react";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../store/auth";
import { Logo, Spinner } from "../components/ui";

export function LoginPage() {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const loc = useLocation() as any;
  const setAuthed = useAuth((s) => s.setAuthed);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await api.login(key.trim());
      setAuthed(true);
      nav(loc.state?.from || "/", { replace: true });
    } catch (e: any) {
      setErr(
        e instanceof ApiError && e.status === 429
          ? "Too many attempts. Wait a few minutes and try again."
          : e instanceof ApiError && e.status === 401
            ? "That access key is not right."
            : "Could not sign in. Check your connection and try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <div className="login-art" aria-hidden="true">
        <div className="blob b1" />
        <div className="blob b2" />
        <div className="blob b3" />
      </div>
      <form className="login-card" onSubmit={submit}>
        <Logo size={34} />
        <h1>Sign in</h1>
        <p className="muted">Describe a gadget, get a buildable product: shell, circuit board, firmware, manual and an India delivered price.</p>
        <label className="label" htmlFor="key">
          Access key
        </label>
        <div className="login-input">
          <KeyRound size={16} className="muted" />
          <input
            id="key"
            className="input"
            type="password"
            autoFocus
            autoComplete="current-password"
            placeholder="Paste your access key"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            aria-invalid={!!err}
          />
        </div>
        {err ? (
          <div className="callout danger" role="alert">
            {err}
          </div>
        ) : null}
        <button className="btn primary lg block" disabled={busy || !key.trim()}>
          {busy ? <Spinner /> : null} Continue <ArrowRight size={16} />
        </button>
        <p className="muted small" style={{ margin: 0 }}>
          Access is by invitation while Gadget Studio is in early access.
        </p>
      </form>
    </div>
  );
}
