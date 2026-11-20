import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./theme.css";
import "./app.css";
import { App } from "./App";
import { rememberWish } from "./lib/wish";
import { reloadOnce } from "./lib/chunkReload";

// A wish from the landing page (/studio/?wish=...) is kept before the sign-in gate can redirect away from it.
rememberWish(location.search);

// A page opened before a deploy: Vite reports a code file the new deploy removed; one reload picks up the new files.
window.addEventListener("vite:preloadError", (e) => {
  if (reloadOnce()) e.preventDefault();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
