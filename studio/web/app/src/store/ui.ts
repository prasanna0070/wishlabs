// Per-viewer layout preferences, kept in localStorage (wrapped: storage may be unavailable).
import { create } from "zustand";

function load<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v == null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}
function save(key: string, v: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* ignore */
  }
}

interface UIState {
  chatWidth: number;
  chatHidden: boolean;
  panelHidden: boolean;
  setChatWidth: (w: number) => void;
  setChatHidden: (v: boolean) => void;
  setPanelHidden: (v: boolean) => void;
}

export const useUI = create<UIState>((set) => ({
  chatWidth: load("gs.chatWidth", 500),
  chatHidden: load("gs.chatHidden", false),
  panelHidden: false,
  setChatWidth: (w) => {
    save("gs.chatWidth", w);
    set({ chatWidth: w });
  },
  setChatHidden: (v) => {
    save("gs.chatHidden", v);
    set({ chatHidden: v, ...(v ? { panelHidden: false } : {}) });
  },
  setPanelHidden: (v) => set({ panelHidden: v, ...(v ? { chatHidden: false } : {}) }),
}));
