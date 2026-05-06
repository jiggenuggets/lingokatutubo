'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Navigation } from '@/components/navigation';
import { ArrowLeft, ChevronLeft, ChevronRight, Download, AlertCircle } from 'lucide-react';
import { getApiBaseUrl } from '@/lib/api-base';

const API_BASE = getApiBaseUrl();

type PreviewData = {
  job_id: string;
  left_page_preview?: string | null;
  bilingual_first_page?: { blocks?: any[] };
  original_pages?: string[];
  translated_pages?: string[];
  page_count?: number;
};

type StructureData = {
  job_id: string;
  status?: string;
  pages?: Array<{ page_number: number; blocks?: any[] }>;
  warnings?: string[];
};

function fileNameFromPath(value?: string | null): string | null {
  if (!value) return null;
  const normalized = value.replace(/\\/g, '/');
  const fileName = normalized.split('/').pop();
  return fileName || null;
}

function buildPreviewUrl(jobId: string, pathOrName?: string | null): string | null {
  const fileName = fileNameFromPath(pathOrName);
  if (!fileName) return null;
  return `${API_BASE}/preview-image/${jobId}/${fileName}`;
}

export default function PreviewBilingualPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = params?.jobId;

  const [previewData, setPreviewData] = useState<PreviewData | null>(null);
  const [structure, setStructure] = useState<StructureData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    (async () => {
      setIsLoading(true);
      setLoadError(null);
      try {
        const [previewResp, structureResp] = await Promise.allSettled([
          fetch(`${API_BASE}/preview/${jobId}`),
          fetch(`${API_BASE}/structure/${jobId}`),
        ]);

        if (cancelled) return;

        if (previewResp.status === 'fulfilled' && previewResp.value.ok) {
          setPreviewData(await previewResp.value.json());
        } else if (previewResp.status === 'fulfilled' && previewResp.value.status === 404) {
          setLoadError(
            'This job is no longer available on the backend. Job state lives in memory only and is lost when the server restarts.'
          );
          return;
        } else {
          setLoadError('Could not load preview metadata. Make sure the backend is running.');
          return;
        }

        if (structureResp.status === 'fulfilled' && structureResp.value.ok) {
          setStructure(await structureResp.value.json());
        }
      } catch {
        if (!cancelled) {
          setLoadError('Could not reach the backend. Please confirm it is running on port 8000.');
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [jobId]);

  const pageCount = useMemo(() => {
    if (!previewData) return 0;
    const count = Math.max(
      previewData.original_pages?.length ?? 0,
      previewData.translated_pages?.length ?? 0,
      previewData.page_count ?? 0,
    );
    return count;
  }, [previewData]);

  // Clamp currentPage if backend reports fewer pages than expected.
  useEffect(() => {
    if (pageCount === 0) return;
    if (currentPage > pageCount) setCurrentPage(pageCount);
    if (currentPage < 1) setCurrentPage(1);
  }, [pageCount, currentPage]);

  const pageIndex = currentPage - 1;
  const originalUrl = buildPreviewUrl(
    jobId ?? '',
    previewData?.original_pages?.[pageIndex] ?? previewData?.left_page_preview ?? undefined,
  );
  const translatedUrl = buildPreviewUrl(
    jobId ?? '',
    previewData?.translated_pages?.[pageIndex] ?? undefined,
  );

  // Translation Details for the current page; fall back to first-page bilingual blocks.
  const structurePage = structure?.pages?.[pageIndex];
  const structureBlocks =
    structurePage?.blocks?.filter((block: any) => block.type === 'text' || block.block_type === 'text') ?? [];
  const fallbackFirstPageBlocks = previewData?.bilingual_first_page?.blocks ?? [];
  const detailsBlocks =
    structureBlocks.length > 0
      ? structureBlocks
      : pageIndex === 0
        ? fallbackFirstPageBlocks
        : [];

  const handleDownload = async () => {
    if (!jobId) return;
    try {
      const response = await fetch(`${API_BASE}/download/${jobId}`);
      if (!response.ok) {
        setLoadError(`Download failed (HTTP ${response.status}).`);
        return;
      }
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `translated-${jobId.slice(0, 8)}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch {
      setLoadError('Could not reach the backend to download.');
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-background via-white to-background">
      <Navigation />

      <main className="max-w-6xl mx-auto px-6 py-10">
        {/* Top bar */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-6">
          <div className="flex items-center gap-3">
            <Link
              href="/translate"
              className="inline-flex items-center gap-2 rounded-lg border-2 border-primary/30 bg-white px-4 py-2 text-sm font-semibold text-primary hover:bg-primary/5"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </Link>
            <div>
              <h1 className="text-2xl font-bold text-primary">Bilingual Preview</h1>
              <p className="text-xs text-foreground/55">Job {jobId?.slice(0, 8)}</p>
            </div>
          </div>

          <button
            onClick={handleDownload}
            disabled={!previewData}
            className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-accent to-primary px-5 py-2.5 text-sm font-bold text-white hover:shadow-lg disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Download className="w-4 h-4" />
            Download
          </button>
        </div>

        {/* Loading / error */}
        {isLoading && (
          <div className="rounded-xl border border-primary/20 bg-white p-8 text-center">
            <div className="mx-auto mb-3 inline-block w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            <p className="text-sm text-foreground/70">Loading preview...</p>
          </div>
        )}

        {!isLoading && loadError && (
          <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
            <div className="space-y-2">
              <p>{loadError}</p>
              <Link href="/translate" className="underline font-semibold">
                Return to upload
              </Link>
            </div>
          </div>
        )}

        {!isLoading && !loadError && previewData && (
          <>
            {/* Page navigation */}
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/20 bg-white px-4 py-3">
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  disabled={currentPage <= 1}
                  className="inline-flex items-center gap-1 rounded-md border border-primary/30 px-3 py-1.5 text-sm font-semibold text-primary hover:bg-primary/5 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <ChevronLeft className="w-4 h-4" />
                  Previous
                </button>
                <button
                  onClick={() => setCurrentPage((p) => Math.min(pageCount || 1, p + 1))}
                  disabled={currentPage >= (pageCount || 1)}
                  className="inline-flex items-center gap-1 rounded-md border border-primary/30 px-3 py-1.5 text-sm font-semibold text-primary hover:bg-primary/5 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Next
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
              <p className="text-sm text-foreground/70">
                Page <span className="font-bold text-primary">{currentPage}</span> of{' '}
                <span className="font-bold text-primary">{pageCount || 1}</span>
              </p>
            </div>

            {/* Side-by-side preview */}
            <div className="rounded-xl border border-primary/20 bg-white p-4">
              <div className="mb-3">
                <h2 className="text-lg font-bold text-primary">Document Preview</h2>
                <p className="text-sm text-foreground/60">
                  Layout-aware reconstruction. Layout preservation is partial — long
                  translations may shrink, truncate, or fall back to the original line.
                </p>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <p className="mb-2 text-xs font-bold uppercase text-foreground/45">Original</p>
                  {originalUrl ? (
                    <img
                      src={originalUrl}
                      alt={`Original page ${currentPage}`}
                      className="w-full rounded-lg border bg-white"
                    />
                  ) : (
                    <div className="rounded-lg border border-dashed border-foreground/20 p-6 text-sm text-foreground/60">
                      No original preview for this page.
                    </div>
                  )}
                </div>

                <div>
                  <p className="mb-2 text-xs font-bold uppercase text-foreground/45">Translated</p>
                  {translatedUrl ? (
                    <img
                      src={translatedUrl}
                      alt={`Translated page ${currentPage}`}
                      className="w-full rounded-lg border bg-white"
                    />
                  ) : (
                    <div className="rounded-lg border border-dashed border-foreground/20 p-6 text-sm text-foreground/60">
                      Translated preview not generated for this page. Use Download for the
                      full PDF.
                    </div>
                  )}
                </div>
              </div>
            </div>

            {pageCount > 20 && (
              <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">
                Backend currently generates preview images for the first 20 pages only.
                Download the full translated PDF for remaining pages.
              </p>
            )}

            {/* Secondary: collapsible Translation Details */}
            <details className="group mt-5 rounded-xl border border-primary/20 bg-white p-4">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
                <div>
                  <h3 className="text-lg font-bold text-primary">Translation Details</h3>
                  <p className="text-sm text-foreground/60">
                    Word-by-word / block-level output for review and debugging. Not the
                    main preview.
                  </p>
                </div>
                <span className="rounded-full border border-primary/30 px-3 py-1 text-xs font-semibold text-primary group-open:hidden">
                  Show
                </span>
                <span className="hidden rounded-full border border-primary/30 px-3 py-1 text-xs font-semibold text-primary group-open:inline">
                  Hide
                </span>
              </summary>

              {detailsBlocks.length > 0 ? (
                <div className="mt-4 space-y-3">
                  {detailsBlocks.map((block: any, index: number) => {
                    const sourceText = block.source_text ?? block.original_text ?? '-';
                    const translatedText = block.translated_text ?? 'UNKNOWN_FOR_REVIEW';
                    const needsReview =
                      !block.translated_text || block.translated_text === 'UNKNOWN_FOR_REVIEW';
                    return (
                      <div
                        key={block.block_id ?? index}
                        className="rounded-lg border border-foreground/10"
                      >
                        <div className="grid gap-0 md:grid-cols-2">
                          <div className="border-b border-foreground/10 p-4 md:border-b-0 md:border-r">
                            <p className="mb-2 text-xs font-bold uppercase text-foreground/45">
                              Original
                            </p>
                            <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/85">
                              {sourceText}
                            </p>
                          </div>
                          <div className="p-4">
                            <p className="mb-2 text-xs font-bold uppercase text-foreground/45">
                              Translation
                            </p>
                            <p
                              className={`whitespace-pre-wrap text-sm leading-relaxed ${
                                needsReview ? 'font-semibold text-red-600' : 'text-foreground/85'
                              }`}
                            >
                              {translatedText}
                            </p>
                          </div>
                        </div>
                        {(block.translation_method ||
                          block.cascade_stage ||
                          block.translation_confidence != null) && (
                          <div className="border-t border-foreground/10 px-4 py-2 text-xs text-foreground/55">
                            {block.translation_method ?? block.cascade_stage ?? 'unknown'}
                            {block.translation_confidence != null &&
                              ` - ${(block.translation_confidence * 100).toFixed(0)}%`}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="mt-4 rounded-lg border border-foreground/10 p-4 text-sm text-foreground/70">
                  No structured text blocks for page {currentPage}.
                </div>
              )}

              {structure?.warnings && structure.warnings.length > 0 && (
                <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                  <p className="font-bold mb-1">Layout warnings (across the document)</p>
                  <ul className="list-disc list-inside space-y-1">
                    {structure.warnings.slice(0, 6).map((warning, i) => (
                      <li key={i}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}
            </details>
          </>
        )}
      </main>
    </div>
  );
}
