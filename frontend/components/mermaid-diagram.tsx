"use client";

// Draws one ```mermaid block. Mermaid is a big library, so it is only
// downloaded when a summary actually contains a diagram.
import { useEffect, useState } from "react";
import { DiagramError } from "./diagram-error";

let diagramCount = 0; // each diagram on the page needs its own id

export function MermaidDiagram({ code }: { code: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          // "strict" stops a diagram from running scripts or opening links.
          // The diagram text comes from an AI reading someone's book, so we
          // treat it as untrusted.
          securityLevel: "strict",
          theme: "base",
          themeVariables: {
            fontFamily: "var(--font-bricolage), system-ui, sans-serif",
            primaryColor: "#e3f1ef",
            primaryBorderColor: "#0f6e6a",
            primaryTextColor: "#14213d",
            lineColor: "#55607a",
            secondaryColor: "#fff3a8",
            tertiaryColor: "#f6f8f7",
          },
        });
        await mermaid.parse(code); // throws if the syntax is wrong
        const result = await mermaid.render(`kru-diagram-${++diagramCount}`, code);
        if (!cancelled) setSvg(result.svg);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code]);

  if (failed) return <DiagramError source={code} />;
  if (svg === null) {
    return <p className="mb-5 font-sans text-sm text-slate">Drawing the diagram…</p>;
  }
  return (
    <div
      className="diagram mb-5 overflow-x-auto rounded-lg border border-line bg-sheet p-4"
      // Safe: this SVG was produced by Mermaid in strict mode, not copied from the text.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
