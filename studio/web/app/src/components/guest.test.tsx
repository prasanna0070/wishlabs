// Magic-link guests (server app/guest.py): the showcase banner that replaces the composer, and the "Guest" note.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { ShowcaseBanner } from "./chat/ChatPanel";
import { GuestNote } from "./GuestNote";

describe("guest UI", () => {
  it("a showcase shows a view-only banner with a way to make your own wish", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <ShowcaseBanner />
      </MemoryRouter>,
    );
    expect(html).toContain("Showcase: view only.");
    expect(html).toMatch(/<a[^>]*href="\/"[^>]*>Make your own wish<\/a>/);
    expect(html).not.toContain("textarea");
  });

  it("the guest note says what is left today", () => {
    const left = { projects: 1, turns: 15, builds: 0, voice: 3, sound: 3 };
    const html = renderToStaticMarkup(<GuestNote guest={{ left, limits: { ...left, projects: 2, builds: 3 }, day: "2026-11-21" }} />);
    expect(html).toContain("Guest");
    expect(html).toContain("1 wish, 15 messages, 0 builds left");
  });
});
