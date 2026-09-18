"use client";

import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Citation } from "@/lib/types";
import { Empty, Spinner } from "./ui/primitives";

/* The evidence pane.
 *
 * Rectangles arrive from the API in PDF points with the origin top-left, which is
 * the same convention PDF.js uses once the viewport scale is applied — so placing a
 * highlight is a multiply, not a coordinate-system conversion.
 *
 * Pages render at devicePixelRatio so text stays crisp on retina displays; the CSS
 * size stays in layout pixels so the overlay maths is unaffected.
 */

interface Props {
  documentId: string | null;
  citations: Citation[];
  activeLabel: string | null;
  onActivate: (label: string) => void;
}

interface RenderedPage {
  index: number;
  width: number;
  height: number;
  canvas: HTMLCanvasElement;
}

export function DocumentViewer({ documentId, citations, activeLabel, onActivate }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pagesRef = useRef<HTMLDivElement>(null);
  const [pages, setPages] = useState<RenderedPage[]>([]);
  // Which render last finished, and how. Loading is derived -- "the render we want is
  // not the one that settled" -- so no effect has to flip a flag before it starts.
  const [settled, setSettled] = useState<{ key: string; error: string | null } | null>(null);
  const [scale, setScale] = useState(1);
  const [containerWidth, setContainerWidth] = useState(0);
  const renderKey = documentId && containerWidth > 0 ? `${documentId}@${containerWidth}` : null;
  const loading = renderKey !== null && settled?.key !== renderKey;
  const error = renderKey !== null && settled?.key === renderKey ? settled.error : null;

  // Track available width so pages fit without a horizontal scrollbar.
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setContainerWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!documentId || containerWidth === 0) return;
    const key = `${documentId}@${containerWidth}`;

    let cancelled = false;

    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          "pdfjs-dist/build/pdf.worker.min.mjs",
          import.meta.url,
        ).toString();

        const bytes = await api.documentBytes(documentId);
        if (cancelled) return;

        const pdf = await pdfjs.getDocument({ data: bytes }).promise;
        if (cancelled) return;

        // Fit the widest page to the pane, capped so a narrow pane does not
        // magnify a page into illegibility.
        const first = await pdf.getPage(1);
        const natural = first.getViewport({ scale: 1 });
        const horizontalPadding = 48;
        const fitted = Math.min(
          (containerWidth - horizontalPadding) / natural.width,
          1.9,
        );
        const nextScale = Math.max(fitted, 0.55);

        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const rendered: RenderedPage[] = [];

        for (let number = 1; number <= pdf.numPages; number++) {
          if (cancelled) return;
          const page = await pdf.getPage(number);
          const viewport = page.getViewport({ scale: nextScale });
          const canvas = document.createElement("canvas");
          canvas.width = Math.floor(viewport.width * dpr);
          canvas.height = Math.floor(viewport.height * dpr);
          canvas.style.width = `${viewport.width}px`;
          canvas.style.height = `${viewport.height}px`;

          const context = canvas.getContext("2d");
          if (!context) continue;
          context.scale(dpr, dpr);
          await page.render({ canvas, canvasContext: context, viewport }).promise;

          rendered.push({
            index: number - 1,
            width: viewport.width,
            height: viewport.height,
            canvas,
          });
        }

        if (cancelled) return;
        setScale(nextScale);
        setPages(rendered);
        setSettled({ key, error: null });
      } catch (cause) {
        if (!cancelled) {
          setSettled({ key, error: cause instanceof Error ? cause.message : "Could not render" });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [documentId, containerWidth]);

  // Attach the rendered canvases into the DOM once pages change.
  useEffect(() => {
    const host = pagesRef.current;
    if (!host) return;
    for (const page of pages) {
      const slot = host.querySelector(`[data-canvas-slot="${page.index}"]`);
      if (slot && !slot.contains(page.canvas)) {
        slot.replaceChildren(page.canvas);
      }
    }
  }, [pages]);

  const byPage = useMemo(() => {
    const map = new Map<number, { citation: Citation; rectIndex: number }[]>();
    citations.forEach((citation) => {
      citation.rects.forEach((rect, rectIndex) => {
        const bucket = map.get(rect.page) ?? [];
        bucket.push({ citation, rectIndex });
        map.set(rect.page, bucket);
      });
    });
    return map;
  }, [citations]);

  /* The extent of each cited passage, per page.
   *
   * A citation is a whole retrieved chunk, which on a résumé is often an entire
   * section. Tinting every line of it reads as "the document is highlighted" rather
   * than "this passage is the evidence". A margin bar down the side states the same
   * thing quietly, the way an annotation tool would.
   *
   * Sentence-level spans would be better still, and belong on the retrieval side
   * rather than here -- the UI can only mark what the API gives it. */
  const extents = useMemo(() => {
    const map = new Map<number, { citation: Citation; top: number; bottom: number; left: number }[]>();
    citations.forEach((citation) => {
      const perPage = new Map<number, { top: number; bottom: number; left: number }>();
      citation.rects.forEach((rect) => {
        const current = perPage.get(rect.page);
        perPage.set(rect.page, {
          top: current ? Math.min(current.top, rect.y0) : rect.y0,
          bottom: current ? Math.max(current.bottom, rect.y1) : rect.y1,
          left: current ? Math.min(current.left, rect.x0) : rect.x0,
        });
      });
      perPage.forEach((bounds, page) => {
        const bucket = map.get(page) ?? [];
        bucket.push({ citation, ...bounds });
        map.set(page, bucket);
      });
    });
    return map;
  }, [citations]);

  const scrollToLabel = useCallback((label: string) => {
    const target = pagesRef.current?.querySelector(`[data-highlight="${label}"]`);
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  useEffect(() => {
    if (activeLabel) scrollToLabel(activeLabel);
  }, [activeLabel, scrollToLabel]);

  return (
    <div ref={scrollRef} className="relative h-full overflow-auto bg-[var(--surface-sunk)]">
      {!documentId && (
        <Empty
          title="No document selected"
          body="Pick a document from the library, or ask a question and the source will open here."
        />
      )}

      {documentId && loading && (
        <div className="flex h-full items-center justify-center gap-2 text-[var(--ink-3)]">
          <Spinner />
          <span className="text-[13px]">Rendering document…</span>
        </div>
      )}

      {documentId && error && (
        <Empty title="Could not render this document" body={error} />
      )}

      <div ref={pagesRef} className="flex flex-col items-center gap-4 p-6">
        {pages.map((page) => (
          <div
            key={page.index}
            className="relative shrink-0 overflow-hidden rounded-[var(--radius-sm)] border border-[var(--rule)] bg-white shadow-[var(--shadow-pop)]"
            style={{ width: page.width, height: page.height }}
          >
            <div data-canvas-slot={page.index} />

            {(extents.get(page.index) ?? []).map(({ citation, top, bottom, left }) => (
              <span
                key={`extent-${citation.label}`}
                aria-hidden
                className={clsx(
                  "absolute rounded-full transition-colors duration-150",
                  activeLabel === citation.label
                    ? "bg-[var(--evidence)]"
                    : "bg-[var(--evidence)]/35",
                )}
                style={{
                  left: Math.max(left * scale - 9, 2),
                  top: top * scale,
                  width: 2,
                  height: Math.max((bottom - top) * scale, 8),
                }}
              />
            ))}

            {(byPage.get(page.index) ?? []).map(({ citation, rectIndex }) => {
              const rect = citation.rects[rectIndex];
              if (!rect) return null;
              const active = activeLabel === citation.label;
              return (
                <button
                  key={`${citation.label}-${rectIndex}`}
                  data-highlight={rectIndex === 0 ? citation.label : undefined}
                  onClick={() => onActivate(citation.label)}
                  aria-label={`Evidence ${citation.label}`}
                  className={clsx(
                    "absolute cursor-pointer rounded-[2px] transition-all duration-150",
                    active
                      ? "bg-[var(--evidence-fill-active)] ring-[1.5px] ring-[var(--evidence-edge)]"
                      : "bg-[var(--evidence-fill)] hover:bg-[var(--evidence-fill-active)]",
                  )}
                  style={{
                    left: rect.x0 * scale,
                    top: rect.y0 * scale,
                    width: Math.max((rect.x1 - rect.x0) * scale, 2),
                    height: Math.max((rect.y1 - rect.y0) * scale, 2),
                  }}
                />
              );
            })}

            <span className="data absolute bottom-1.5 right-2 select-none opacity-45">
              {page.index + 1}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
