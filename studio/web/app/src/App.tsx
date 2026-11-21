import { useEffect, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./lib/api";
import { safeShareUrl, STUDIO_BASE } from "./lib/urls";
import { useAuth } from "./store/auth";
import { LoginPage } from "./pages/Login";
import { HomePage } from "./pages/Home";
import { ProjectPage } from "./pages/ProjectPage";
import { Spinner } from "./components/ui";

function Gate({ children }: { children: ReactNode }) {
  const authed = useAuth((s) => s.authed);
  const loc = useLocation();
  if (authed === null)
    return (
      <div className="empty">
        <Spinner lg />
      </div>
    );
  if (!authed) return <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
  return <>{children}</>;
}

export function App() {
  const setAuthed = useAuth((s) => s.setAuthed);
  const setShareUrl = useAuth((s) => s.setShareUrl);
  const setVoiceOff = useAuth((s) => s.setVoiceOff);
  const setGuest = useAuth((s) => s.setGuest);
  useEffect(() => {
    api.session().then(
      (r) => {
        setShareUrl(safeShareUrl(r?.share_url));
        setVoiceOff(r?.voice === false);
        setGuest(r?.authenticated && r.kind === "guest" && r.guest ? r.guest : null);
        setAuthed(!!r?.authenticated);
      },
      () => setAuthed(false),
    );
  }, [setAuthed, setShareUrl, setVoiceOff, setGuest]);
  return (
    <BrowserRouter basename={STUDIO_BASE || "/"}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/s/:token/:tab?" element={<ProjectPage shared />} />
        <Route
          path="/p/:pid/:tab?"
          element={
            <Gate>
              <ProjectPage />
            </Gate>
          }
        />
        <Route
          path="/"
          element={
            <Gate>
              <HomePage />
            </Gate>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
