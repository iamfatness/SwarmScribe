import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BreakPath } from "./BreakPath";

describe("BreakPath", () => {
  it("offers a break after every slash and leaves the text as it was", () => {
    const { container } = render(
      <span>
        <BreakPath text="incoming/2026/meeting-2.wav" />
      </span>,
    );
    expect(container.textContent).toBe("incoming/2026/meeting-2.wav");
    expect(container.querySelectorAll("wbr")).toHaveLength(2);
    // The break comes after the slash, never before it.
    expect(container.innerHTML).toBe(
      '<span><span class="path-part">incoming/</span><wbr><span class="path-part">2026/</span><wbr>' +
        '<span class="path-part">meeting-2.wav</span></span>',
    );
  });

  it("treats a backslash path the same, and a name with no slash as one piece", () => {
    const win = render(
      <span>
        <BreakPath text={"D:\\media\\intake"} />
      </span>,
    );
    expect(win.container.textContent).toBe("D:\\media\\intake");
    expect(win.container.querySelectorAll("wbr")).toHaveLength(2);
    const bare = render(
      <span>
        <BreakPath text="meeting-2.wav" />
      </span>,
    );
    expect(bare.container.querySelectorAll("wbr")).toHaveLength(0);
  });
});
