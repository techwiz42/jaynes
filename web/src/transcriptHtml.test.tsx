import { describe, expect, it } from "vitest";
import type { Item } from "./reducer";
import { transcriptHtml } from "./transcriptHtml";

describe("transcriptHtml", () => {
  it("draws replies as HTML with math, and shows the user's words as text", () => {
    const items: Item[] = [
      { kind: "user", key: "u1", text: "Is <b>this</b> energy \\(E=mc^2\\)?" },
      { kind: "assistant", messageId: "a1", role: "draft", text: "**Yes**: \\(E=mc^2\\).", complete: true, interrupted: false, contested: false, retractionIssues: [] },
    ];
    const html = transcriptHtml(items, "A <title>", new Date("2026-11-15T12:00:00Z"));
    expect(html).toContain("<title>A &lt;title&gt;</title>");
    expect(html).toContain("Is &lt;b&gt;this&lt;/b&gt; energy");
    expect(html).toContain("<strong>Yes</strong>");
    expect(html).toContain('class="katex"');
    expect(html).not.toContain("<b>this</b>");
  });
});
