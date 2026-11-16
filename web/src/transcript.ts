import type { Item } from "./reducer";

/** The conversation as Markdown: what the user said and what Jaynes finally answered. A draft the observer retracted, tool rows and errors are left out. */
export function transcriptMarkdown(items: Item[], title: string, now: Date = new Date()): string {
  const turns = items.flatMap((item) => {
    if (item.kind === "user") return [`**You:**\n\n${item.text.trim()}`];
    if (item.kind === "assistant" && !item.contested && item.text.trim()) return [`**Jaynes:**\n\n${item.text.trim()}`];
    return [];
  });
  return `# ${title}\n\nSaved from Jaynes on ${now.toISOString().slice(0, 10)}.\n\n${turns.join("\n\n---\n\n")}\n`;
}

export function transcriptFileName(title: string): string {
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  return `${slug || "conversation"}.md`;
}

/** Hand the text to the browser as a file to save. */
export function downloadText(fileName: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
