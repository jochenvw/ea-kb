import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@/app";
import { OntologyProvider } from "@/ontology";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <OntologyProvider>
      <App />
    </OntologyProvider>
  </StrictMode>,
);
