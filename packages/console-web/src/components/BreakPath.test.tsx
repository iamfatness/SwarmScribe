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

  it("keeps an address's scheme and host as one piece: never a break after https://", () => {
    const { container } = render(
      <span>
        <BreakPath text="https://eu-1.leaders.example" />
      </span>,
    );
    expect(container.innerHTML).toBe('<span><span class="path-part path-host">https://eu-1.leaders.example</span></span>');
    const deep = render(
      <span>
        <BreakPath text="https://eu-1.leaders.example:8443/swarm/leader" />
      </span>,
    );
    expect(deep.container.textContent).toBe("https://eu-1.leaders.example:8443/swarm/leader");
    expect(deep.container.innerHTML).toBe(
      '<span><span class="path-part path-host">https://eu-1.leaders.example:8443/</span><wbr>' +
        '<span class="path-part">swarm/</span><wbr><span class="path-part">leader</span></span>',
    );
  });

  it("lets a host too long for any column break, as a long name does", () => {
    const long = `https://${"a".repeat(60)}.example`;
    const { container } = render(
      <span>
        <BreakPath text={long} />
      </span>,
    );
    expect(container.textContent).toBe(long);
    expect(container.querySelector(".path-host")).toBeNull();
    expect(container.querySelectorAll(".path-part")).toHaveLength(1);
  });

  it("still breaks a storage address after its slashes: only a scheme's own two stay together", () => {
    const { container } = render(
      <span>
        <BreakPath text="s3://bucket/a//b" />
      </span>,
    );
    expect(container.textContent).toBe("s3://bucket/a//b");
    expect(Array.from(container.querySelectorAll(".path-part"), (el) => el.textContent)).toEqual([
      "s3://bucket/",
      "a/",
      "/",
      "b",
    ]);
  });
});
