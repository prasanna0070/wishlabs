import { friendlyError } from "../lib/errors";
import { Markdown } from "./ui";

/** An error as one plain sentence, the advice that followed it, and the raw engine text behind "Technical details". */
export function ErrorText({ text, full, fallback = "Something went wrong." }: { text?: string | null; full?: string | null; fallback?: string }) {
  const f = friendlyError(text || fallback, full);
  return (
    <div className="err-text">
      <Markdown text={f.headline} className="compact" />
      {f.rest ? <Markdown text={f.rest} className="compact" /> : null}
      {f.details ? (
        <details className="err-details">
          <summary>Technical details</summary>
          {f.fix ? (
            <p className="small">
              <strong>Suggested change:</strong> {f.fix}
            </p>
          ) : null}
          <pre>{f.details}</pre>
        </details>
      ) : null}
    </div>
  );
}
