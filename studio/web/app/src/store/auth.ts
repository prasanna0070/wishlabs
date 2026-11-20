import { create } from "zustand";

interface AuthState {
  authed: boolean | null; // null = unknown (checking)
  setAuthed: (v: boolean) => void;
  /** A read-only share link the sign-in page offers as "See a real build" (server env SHARE_URL); null hides it. */
  shareUrl: string | null;
  setShareUrl: (v: string | null) => void;
}

export const useAuth = create<AuthState>((set) => ({
  authed: null,
  setAuthed: (v) => set({ authed: v }),
  shareUrl: null,
  setShareUrl: (v) => set({ shareUrl: v }),
}));
