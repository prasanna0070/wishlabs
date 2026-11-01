import { Check, ChevronDown, Sparkles, Zap } from "lucide-react";
import type { Mode } from "../lib/types";
import { Dropdown } from "./ui";

const MODES: { id: Mode; label: string; sub: string; icon: typeof Zap }[] = [
  { id: "fast", label: "Standard", sub: "Fast model. Quick answers and edits.", icon: Zap },
  { id: "pro", label: "Deep", sub: "Pro model. Slower, more careful design work.", icon: Sparkles },
];

export function ModeSelect({ value, onChange, up = false, disabled = false }: { value: Mode; onChange: (m: Mode) => void; up?: boolean; disabled?: boolean }) {
  const cur = MODES.find((m) => m.id === value) || MODES[0];
  const Icon = cur.icon;
  return (
    <Dropdown
      align="right"
      width={280}
      trigger={({ toggle, open }) => (
        <button type="button" className="mode-btn" onClick={toggle} aria-haspopup="menu" aria-expanded={open} aria-label="Build speed" disabled={disabled}>
          <Icon size={15} />
          {cur.label}
          <ChevronDown size={15} />
        </button>
      )}
    >
      {(close) => (
        <div role="menu" className={up ? "menu-up" : ""}>
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              role="menuitemradio"
              aria-checked={m.id === value}
              className="menu-item"
              onClick={() => {
                onChange(m.id);
                close();
              }}
            >
              <m.icon size={16} />
              <span className="grow">
                <div style={{ fontWeight: 500 }}>{m.label}</div>
                <div className="sub">{m.sub}</div>
              </span>
              {m.id === value ? <Check size={16} color="var(--primary)" /> : null}
            </button>
          ))}
        </div>
      )}
    </Dropdown>
  );
}
