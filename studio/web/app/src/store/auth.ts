import { create } from "zustand";
import type { GuestInfo } from "../lib/types";

interface AuthState {
  authed: boolean | null; // null = unknown (checking)
  setAuthed: (v: boolean) => void;
  /** A read-only share link the sign-in page offers as "See a real build" (server env SHARE_URL); null hides it. */
  shareUrl: string | null;
  setShareUrl: (v: string | null) => void;
  /** The server has the voice wish switched off (LIVE_ENABLED=0): Talk buttons hide. */
  voiceOff: boolean;
  setVoiceOff: (v: boolean) => void;
  /** A magic-link guest (own projects + read-only showcases, daily limits); null = the team or unknown. */
  guest: GuestInfo | null;
  setGuest: (v: GuestInfo | null) => void;
}

export const useAuth = create<AuthState>((set) => ({
  authed: null,
  setAuthed: (v) => set({ authed: v }),
  shareUrl: null,
  setShareUrl: (v) => set({ shareUrl: v }),
  voiceOff: false,
  setVoiceOff: (v) => set({ voiceOff: v }),
  guest: null,
  setGuest: (v) => set({ guest: v }),
}));
