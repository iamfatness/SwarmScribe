import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyTheme, readTheme } from "./app/theme";
import "./styles.css";

// Before the first render, so the page never flashes the wrong theme.
applyTheme(readTheme());

const root = document.getElementById("root");
if (root === null) throw new Error("index.html has no #root");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
