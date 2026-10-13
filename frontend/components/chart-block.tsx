"use client";

// Draws one ```chart block: a small JSON description of a bar, line, or pie
// chart (the format is described in prompts/1_system.txt).
import { useEffect, useRef, useState } from "react";
import { DiagramError } from "./diagram-error";

type ChartSpec = {
  type: "bar" | "line" | "pie";
  title?: string;
  labels: string[];
  datasets: { label?: string; data: number[] }[];
};

const COLORS = ["#0f6e6a", "#e0b400", "#14213d", "#6c8ebf", "#b8563f", "#7a9e7e", "#8a6fb0"];

/** Check the AI's JSON really is a chart we can draw. Returns null if not. */
export function parseChartSpec(source: string): ChartSpec | null {
  let spec: unknown;
  try {
    spec = JSON.parse(source);
  } catch {
    return null;
  }
  if (typeof spec !== "object" || spec === null) return null;
  const { type, title, labels, datasets } = spec as Record<string, unknown>;
  if (type !== "bar" && type !== "line" && type !== "pie") return null;
  if (!Array.isArray(labels) || labels.length === 0) return null;
  if (!Array.isArray(datasets) || datasets.length === 0) return null;
  for (const dataset of datasets) {
    if (typeof dataset !== "object" || dataset === null) return null;
    const data = (dataset as Record<string, unknown>).data;
    if (!Array.isArray(data) || data.length !== labels.length) return null;
    if (!data.every((value) => typeof value === "number" && Number.isFinite(value))) return null;
  }
  return {
    type,
    title: typeof title === "string" ? title : undefined,
    labels: labels.map(String),
    datasets: datasets as ChartSpec["datasets"],
  };
}

export function ChartBlock({ source }: { source: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  const spec = parseChartSpec(source);

  useEffect(() => {
    if (!spec || !canvas.current) return;
    let chart: { destroy: () => void } | undefined;
    let cancelled = false;
    (async () => {
      try {
        const { Chart } = await import("chart.js/auto");
        if (cancelled || !canvas.current) return;
        const pie = spec.type === "pie";
        chart = new Chart(canvas.current, {
          type: spec.type,
          data: {
            labels: spec.labels,
            datasets: spec.datasets.map((dataset, i) => ({
              label: dataset.label ?? "",
              data: dataset.data,
              // A pie gives each slice a colour; bars and lines get one per dataset.
              backgroundColor: pie ? COLORS : COLORS[i % COLORS.length],
              borderColor: pie ? "#ffffff" : COLORS[i % COLORS.length],
              borderWidth: pie ? 2 : spec.type === "line" ? 2.5 : 0,
            })),
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              title: { display: Boolean(spec.title), text: spec.title, color: "#14213d" },
              legend: { display: pie || spec.datasets.length > 1 },
            },
          },
        });
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      chart?.destroy();
    };
    // `source` decides `spec`, so it is the only thing to watch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  if (!spec || failed) return <DiagramError source={source} />;
  return (
    <div className="mb-5 h-72 rounded-lg border border-line bg-sheet p-4">
      <canvas ref={canvas} role="img" aria-label={spec.title ?? "Chart"} />
    </div>
  );
}
