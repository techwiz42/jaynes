import { normalizeMath } from "./math";
import type { Item } from "./reducer";

export interface Turn {
  who: "You" | "Jaynes";
  text: string;
}

/** What was said and what Jaynes finally answered. A draft the observer retracted, tool rows and errors are left out. */
export function transcriptTurns(items: Item[]): Turn[] {
  return items.flatMap((item): Turn[] => {
    if (item.kind === "user") return [{ who: "You", text: item.text.trim() }];
    if (item.kind === "assistant" && !item.contested && item.text.trim()) return [{ who: "Jaynes", text: item.text.trim() }];
    return [];
  });
}

/** The conversation as Markdown, with Jaynes's math written as $x$ and $$x$$ (what most Markdown viewers draw). */
export function transcriptMarkdown(items: Item[], title: string, now: Date = new Date()): string {
  const turns = transcriptTurns(items).map((turn) => `**${turn.who}:**\n\n${turn.who === "Jaynes" ? normalizeMath(turn.text) : turn.text}`);
  return `# ${title}\n\nSaved from Jaynes on ${now.toISOString().slice(0, 10)}.\n\n${turns.join("\n\n---\n\n")}\n`;
}

export function transcriptFileName(title: string, extension: "md" | "html" = "md"): string {
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  return `${slug || "conversation"}.${extension}`;
}

/** Hand the text to the browser as a file to save. */
export function downloadText(fileName: string, text: string, type: string = "text/markdown"): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
