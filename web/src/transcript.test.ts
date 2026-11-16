import { describe, expect, it } from "vitest";
import type { Item } from "./reducer";
import { transcriptFileName, transcriptMarkdown } from "./transcript";

const assistant = (messageId: string, text: string, contested = false): Item => ({
  kind: "assistant", messageId, role: "draft", text, complete: true, interrupted: false, contested, retractionIssues: [],
});

describe("transcript", () => {
  it("keeps what was said and drops retracted drafts, tools and errors", () => {
    const items: Item[] = [
      { kind: "user", key: "u1", text: "What is 2+2?" },
      assistant("a1", "It is 5.", true),
      { kind: "tool", key: "t1", callId: "c", turnId: "t", tool: "web_fetch", target: "x", status: "ok", summary: "", truncated: false },
      assistant("a2", "It is 4."),
      { kind: "error", key: "e1", where: "observer", message: "timed out" },
    ];
    const text = transcriptMarkdown(items, "Arithmetic", new Date("2026-11-15T12:00:00Z"));
    expect(text).toBe("# Arithmetic\n\nSaved from Jaynes on 2026-11-15.\n\n**You:**\n\nWhat is 2+2?\n\n---\n\n**Jaynes:**\n\nIt is 4.\n");
  });

  it("writes the model's math as dollar-sign math and leaves prices and code alone", () => {
    const items: Item[] = [assistant("a1", "Energy is \\(E=mc^2\\), and\n\n\\[x^2+y^2=z^2\\]\n\nIt costs $5 or $6. Code: `\\(a\\)`")];
    const text = transcriptMarkdown(items, "Math", new Date("2026-11-15T12:00:00Z"));
    expect(text).toContain("Energy is $E=mc^2$, and");
    expect(text).toContain("$$\nx^2+y^2=z^2\n$$");
    expect(text).toContain("It costs \\$5 or \\$6. Code: `\\(a\\)`");
  });

  it("makes a safe file name", () => {
    expect(transcriptFileName("Why is the sky blue? / ../etc")).toBe("why-is-the-sky-blue-etc.md");
    expect(transcriptFileName("???")).toBe("conversation.md");
  });
});
