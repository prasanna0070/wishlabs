// The share page's 3D model: the studio viewer with zoom and reset. Loaded lazily (three.js is ~1 MB), and on touch
// screens it waits for a tap so a finger scrolling the page is not caught by the model.
import { useRef, useState } from "react";
import { Hand } from "lucide-react";
import { Viewer, ViewerControls, type ViewerHandle } from "../../three/Viewer";

export default function ShareViewer({ url, label }: { url: string; label: string }) {
  const handle = useRef<ViewerHandle | null>(null);
  const [active, setActive] = useState(() => typeof window === "undefined" || !window.matchMedia?.("(pointer: coarse)").matches);
  return (
    <div className={`sh-viewer-inner${active ? "" : " idle"}`}>
      <Viewer url={url} handle={handle} ariaLabel={label}>
        <ViewerControls handle={handle} />
      </Viewer>
      {!active ? (
        <button className="sh-viewer-tap" onClick={() => setActive(true)}>
          <Hand size={16} /> Tap to turn the model
        </button>
      ) : null}
    </div>
  );
}
