import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import ErrorBoundary from "./components/ErrorBoundary";
import "./index.css";
import { storage } from "./lib/storage";
import { StoreProvider } from "./lib/store";
import Crisis from "./pages/Crisis";
import Dashboard from "./pages/Dashboard";
import Onboarding from "./pages/Onboarding";
import Responder from "./pages/Responder";

function Home() {
  return storage.onboarded() ? <Dashboard /> : <Navigate to="/welcome" replace />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
    <StoreProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/welcome" element={<Onboarding />} />
          <Route path="/crisis" element={<Crisis />} />
          <Route path="/responder" element={<Responder />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </StoreProvider>
    </ErrorBoundary>
  </StrictMode>,
);

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => undefined));
}
