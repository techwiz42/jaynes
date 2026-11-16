import { renderToStaticMarkup } from "react-dom/server";
import { MarkdownText } from "./Markdown";
import { transcriptTurns } from "./transcript";
import type { Item } from "./reducer";

// The page needs KaTeX's stylesheet and fonts to draw math; the file links them from a CDN rather than carrying a copy (it draws plain, without them, when offline).
const KATEX_VERSION = "0.19.1";
const STYLE = `body{font:16px/1.55 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#1a1a1a}h1{font-size:1.5rem}.turn{margin:1.5rem 0;padding-top:1rem;border-top:1px solid #ddd}.who{font-weight:600;margin-bottom:.4rem}.you .text{white-space:pre-wrap}pre{overflow-x:auto;background:#f5f5f5;padding:.7rem;border-radius:6px}code{font-family:ui-monospace,monospace}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.25rem .5rem}.katex-display{overflow-x:auto}`;

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** The conversation as one self-contained HTML page: Jaynes's replies drawn as on the site (Markdown, tables, math), the user's words as typed. */
export function transcriptHtml(items: Item[], title: string, now: Date = new Date()): string {
  const turns = transcriptTurns(items).map((turn) =>
    turn.who === "You"
      ? `<section class="turn you"><div class="who">You</div><div class="text">${escapeHtml(turn.text)}</div></section>`
      : `<section class="turn"><div class="who">Jaynes</div>${renderToStaticMarkup(<MarkdownText>{turn.text}</MarkdownText>)}</section>`,
  );
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@${KATEX_VERSION}/dist/katex.min.css"><style>${STYLE}</style></head>
<body><h1>${escapeHtml(title)}</h1><p>Saved from Jaynes on ${now.toISOString().slice(0, 10)}.</p>
${turns.join("\n")}
</body></html>
`;
}
