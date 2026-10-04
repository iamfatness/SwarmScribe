import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { App } from "./App";

it("renders the console's name", () => {
  render(<App />);
  expect(screen.getByRole("heading", { level: 1, name: "SwarmScribe console" })).toBeInTheDocument();
});
