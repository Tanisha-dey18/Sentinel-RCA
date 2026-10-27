import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import "./index.css";
import { Anomalies } from "./pages/Anomalies";
import { Dashboard } from "./pages/Dashboard";
import { IncidentDetailPage } from "./pages/IncidentDetail";
import { Incidents } from "./pages/Incidents";
import { Ingest } from "./pages/Ingest";
import { Logs } from "./pages/Logs";
import { Signatures } from "./pages/Signatures";
import { SystemPage } from "./pages/System";
import { Empty } from "./components/ui";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="incidents" element={<Incidents />} />
          <Route path="incidents/:id" element={<IncidentDetailPage />} />
          <Route path="anomalies" element={<Anomalies />} />
          <Route path="logs" element={<Logs />} />
          <Route path="ingest" element={<Ingest />} />
          <Route path="signatures" element={<Signatures />} />
          <Route path="system" element={<SystemPage />} />
          <Route path="*" element={<Empty title="Page not found" />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
