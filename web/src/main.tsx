import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/barlow-condensed/700.css";
import "@fontsource/barlow-condensed/800.css";
import "@fontsource/barlow-condensed/800-italic.css";
// Barlow Condensed has no Greek: Fira Sans Condensed fills in those glyphs only.
import "@fontsource/fira-sans-condensed/greek-700.css";
import "@fontsource/fira-sans-condensed/greek-800.css";
import "@fontsource-variable/inter";
import { App } from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
