import "katex/dist/katex.min.css";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { normalizeMath } from "./math";

/** A reply's text as Markdown, with LaTeX math ($x$, $$x$$, \(x\), \[x\]) drawn by KaTeX. Raw HTML in the text is not rendered (react-markdown's default), link addresses are
 *  sanitised by it, and KaTeX is not trusted to run commands that fetch or link anything. Math that will not parse is shown as it was written, in red, instead of failing the page. */
export function MarkdownText({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, trust: false, strict: "ignore" }]]}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
        }}
      >
        {normalizeMath(children)}
      </ReactMarkdown>
    </div>
  );
}

/** Markdown reduced to plain words, for a one-line preview. */
export function plainText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, " code ")
    .replace(/\$\$|\\[[\]()]/g, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]|\d+\.)\s+/gm, "")
    .replace(/[*_`~]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
