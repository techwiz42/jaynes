import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** A reply's text as Markdown. Raw HTML in the text is not rendered (react-markdown's default), and link addresses are sanitised by it. */
export function MarkdownText({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

/** Markdown reduced to plain words, for a one-line preview. */
export function plainText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, " code ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]|\d+\.)\s+/gm, "")
    .replace(/[*_`~]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
