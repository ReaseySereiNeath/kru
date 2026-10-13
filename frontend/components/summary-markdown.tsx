"use client";

// Turns the summary's Markdown into a page. Two kinds of code block get
// special treatment: ```mermaid becomes a diagram and ```chart a chart.
// Raw HTML inside the Markdown is NOT rendered (react-markdown's default),
// which matters because the text comes from an AI reading someone's file.
import { Children, isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { slugify } from "@/lib/format";
import { ChartBlock } from "./chart-block";
import { MermaidDiagram } from "./mermaid-diagram";

/** The plain text inside some rendered Markdown (e.g. a heading with **bold**). */
function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

export function SummaryMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="summary">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Give each section heading an id, so the contents list can link to it.
          h2: ({ children }) => <h2 id={slugify(textOf(children))}>{children}</h2>,
          // A code block arrives as <pre><code class="language-xxx">…</code></pre>.
          pre: ({ children }) => {
            const code = Children.toArray(children)[0];
            if (isValidElement<{ className?: string; children?: ReactNode }>(code)) {
              const language = /language-(\w+)/.exec(code.props.className ?? "")?.[1];
              const source = textOf(code.props.children).trim();
              if (language === "mermaid") return <MermaidDiagram code={source} />;
              if (language === "chart") return <ChartBlock source={source} />;
            }
            return <pre>{children}</pre>;
          },
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
