// Models write math in several styles. remark-math understands $...$ and $$...$$; this turns \( \) and \[ \] into those, and keeps ordinary dollar signs (prices) from being
// read as math, using the usual rules: an opening $ is followed by a non-space, a closing $ is preceded by a non-space and not followed by a digit.

const CODE = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/g;      // code fences (even unfinished, while streaming) and inline code: never touched
const DISPLAY = /(\$\$[\s\S]*?\$\$)/g;

function isSpace(char: string | undefined): boolean {
  return char === undefined || /\s/.test(char);
}

function nextDollar(text: string, from: number): number {
  for (let i = from; i < text.length; i++) {
    if (text[i] === "\\") i++;
    else if (text[i] === "$") return i;
    else if (text[i] === "\n" && text[i + 1] === "\n") return -1;           // math does not run across paragraphs
  }
  return -1;
}

function pairDollars(text: string): string {
  let out = "";
  let i = 0;
  while (i < text.length) {
    const char = text[i]!;
    if (char === "\\") {
      out += char + (text[i + 1] ?? "");
      i += 2;
    } else if (char === "$") {
      let close = -1;
      if (!isSpace(text[i + 1])) {
        for (let j = nextDollar(text, i + 1); j !== -1; j = nextDollar(text, j + 1)) {
          if (j > i + 1 && !isSpace(text[j - 1]) && !/\d/.test(text[j + 1] ?? "")) {
            close = j;
            break;
          }
        }
      }
      if (close === -1) {
        out += "\\$";                                                       // an ordinary dollar sign
        i += 1;
      } else {
        out += text.slice(i, close + 1);
        i = close + 1;
      }
    } else {
      out += char;
      i += 1;
    }
  }
  return out;
}

function convert(text: string): string {
  const converted = text
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, inner: string) => `$$${inner}$$`)                 // laid out as display math by the step below
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, inner: string) => `$${inner.trim()}$`);
  return converted
    .split(DISPLAY)
    .map((part, i) => (i % 2 === 1 ? `\n\n$$\n${part.slice(2, -2).trim()}\n$$\n\n` : pairDollars(part)))     // display math on its own lines, or it is drawn inline
    .join("");
}

/** The text with math written in the styles above turned into the one remark-math reads, and prices left alone. Code is not touched. */
export function normalizeMath(markdown: string): string {
  return markdown
    .split(CODE)
    .map((part, i) => (i % 2 === 1 ? part : convert(part)))
    .join("");
}
