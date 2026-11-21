import type { GuestInfo } from "../lib/types";

/** A magic-link guest: what is left of today's limits (server app/guest.py). */
export function GuestNote({ guest }: { guest: GuestInfo }) {
  const l = guest.left;
  return (
    <span className="guest-note small" title="Guest access through a shared link. Limits reset every day.">
      <b>Guest</b> · today: {l.projects} {l.projects === 1 ? "wish" : "wishes"}, {l.turns} messages, {l.builds}{" "}
      {l.builds === 1 ? "build" : "builds"} left
    </span>
  );
}
