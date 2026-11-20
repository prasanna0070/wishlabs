// The working card's markup (react-dom/server, no DOM needed): what a reloaded page shows while a turn runs.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaleCard, WorkingCard } from "./chat/Messages";
import type { TurnState } from "../lib/working";

const t: TurnState = {
  turnId: "t_1",
  running: true,
  remote: true,
  startedAt: 1_000_000,
  userMsgId: "m_1",
  status: "Thinking",
  action: "text",
  steps: [
    { id: "r:0", label: "Reading your message", state: "done" },
    { id: "r:1", label: "Fixing the design (round 1)", state: "running", detail: "Moon Bot: the wall is too thin" },
  ],
  thoughts: "**Reading the wish**\n\nA calm robot.\n\n**Choosing questions**\n\nAge first.\n\n",
};

describe("working card", () => {
  it("collapsed: spinner, elapsed time, the step (polite live region), one live thought line, a toggle", () => {
    const html = renderToStaticMarkup(<WorkingCard t={t} now={t.startedAt + 125_000} />);
    expect(html).toContain("Working on it");
    expect(html).toContain("2m 5s");
    expect(html).toContain("spinner");
    expect(html).toMatch(/aria-live="polite"[^>]*>Fixing the design \(round 1\)…/);
    expect(html).toContain('class="wc-latest"');
    expect(html).toContain("Choosing questions"); // the newest thought, in one line
    expect(html).not.toContain("Age first"); // the full thinking stays collapsed
    expect(html).toMatch(/<button type="button" class="wc-toggle" aria-expanded="false" aria-controls="[^"]+">/);
    expect(html).toContain("Show thinking");
    expect(html).toContain("hidden");
  });

  it("expanded: the steps with a repair round's reason, and the thought summaries", () => {
    const html = renderToStaticMarkup(<WorkingCard t={t} now={t.startedAt + 5_000} open />);
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("Hide thinking");
    expect(html).toContain("Moon Bot: the wall is too thin");
    expect(html).toContain("<strong>Reading the wish</strong>");
    expect(html).toContain("Age first");
    expect(html).toMatch(/class="wc-thoughts" tabindex="0" role="region" aria-label="The assistant&#x27;s thinking"/);
  });

  it("without thoughts it still shows the step and says none were shared yet", () => {
    const html = renderToStaticMarkup(<WorkingCard t={{ ...t, thoughts: "" }} now={t.startedAt} open />);
    expect(html).not.toContain('class="wc-latest"');
    expect(html).toContain("No thoughts shared yet");
  });

  it("a turn with no progress for a minute says it may have stopped and when it can be tried again", () => {
    const html = renderToStaticMarkup(<WorkingCard t={{ ...t, quiet: true }} now={t.startedAt + 5 * 60_000} />);
    expect(html).toContain("No progress for over a minute, so it may have stopped");
    expect(html).toContain("try again in about 10 min");
    expect(renderToStaticMarkup(<WorkingCard t={t} now={t.startedAt + 5 * 60_000} />)).not.toContain("No progress");
  });

  it("a stale turn says it stopped and offers Retry", () => {
    const html = renderToStaticMarkup(<StaleCard t={{ ...t, running: false, stale: true, status: "Designing concepts" }} now={t.startedAt + 16 * 60_000} />);
    expect(html).toContain('role="status"'); // polite: it shows on every open of a project with an old lock
    expect(html).not.toContain('role="alert"');
    expect(html).toContain("The last step stopped unexpectedly");
    expect(html).toContain("16 min ago");
    expect(html).toContain("last step: Designing concepts");
    expect(html).toContain("Retry");
  });
});
