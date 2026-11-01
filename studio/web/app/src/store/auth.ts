import { create } from "zustand";

interface AuthState {
  authed: boolean | null; // null = unknown (checking)
  setAuthed: (v: boolean) => void;
}

export const useAuth = create<AuthState>((set) => ({
  authed: null,
  setAuthed: (v) => set({ authed: v }),
}));
