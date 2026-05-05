'use client';

import { useState, useRef, useEffect } from 'react';
import { Navigation } from '@/components/navigation';
import { Upload, FileText, Check, AlertCircle, Download, Globe, ArrowUpDown } from 'lucide-react';
import { useUpload } from '@/hooks/use-upload';
import { getApiBaseUrl } from '@/lib/api-base';

const API_BASE = getApiBaseUrl();

const SOURCE_LANGUAGES = [
  { value: 'auto',     label: '🔍 Auto-Detect' },
  { value: 'english',  label: 'English' },
  { value: 'filipino', label: 'Filipino / Tagalog' },
  { value: 'cebuano',  label: 'Cebuano' },
  { value: 'tagabawa', label: 'Bagobo-Tagabawa' },
];

const TARGET_LANGUAGES = [
  { value: 'tagabawa', label: 'Bagobo-Tagabawa' },
  { value: 'english',  label: 'English' },
  { value: 'filipino', label: 'Filipino / Tagalog' },
  { value: 'cebuano',  label: 'Cebuano' },
];

export default function TranslatePage() {
  const [previewData, setPreviewData] = useState<any>(null);
  const [documentStructure, setDocumentStructure] = useState<any>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [sourceLanguage, setSourceLanguage] = useState('auto');
  const [targetLanguage, setTargetLanguage] = useState('tagabawa');
  const [uploadStatus, setUploadStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [structureStatus, setStructureStatus] = useState<'idle' | 'loading' | 'ready' | 'fallback' | 'error'>('idle');
  const [structureMessage, setStructureMessage] = useState<string | null>(null);
  const [detectedLanguage, setDetectedLanguage] = useState<string | null>(null);
  const [detectionConfidence, setDetectionConfidence] = useState<number | null>(null);
  const [isDetectingLanguage, setIsDetectingLanguage] = useState(false);
  const [isJobComplete, setIsJobComplete] = useState(false);
  const detectStartRef = useRef<number>(0);
  const detectionShownRef = useRef<boolean>(false);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const detectionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollAbortRef = useRef<AbortController | null>(null);
  const isMountedRef = useRef<boolean>(true);

  const { upload, isLoading: isUploading, error: uploadError } = useUpload();

  const clearPolling = () => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (detectionTimerRef.current) {
      clearTimeout(detectionTimerRef.current);
      detectionTimerRef.current = null;
    }
    if (pollAbortRef.current) {
      pollAbortRef.current.abort();
      pollAbortRef.current = null;
    }
  };

  useEffect(() => {
    return () => {
      isMountedRef.current = false;
      clearPolling();
    };
  }, []);

  const formatLanguageName = (lang: string): string => {
    const names: Record<string, string> = {
      filipino: 'Filipino',
      english: 'English',
      cebuano: 'Cebuano',
      tagabawa: 'Bagobo-Tagabawa',
    };
    return names[lang.toLowerCase()] ?? (lang.charAt(0).toUpperCase() + lang.slice(1));
  };

  const supportedFormats = ['PDF', 'DOCX', 'JPG', 'PNG'];

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      const file = files[0];
      if (isValidFile(file)) {
        setSelectedFile(file);
        setUploadStatus('success');
      } else {
        setUploadStatus('error');
      }
    }
  };

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.currentTarget.files;
    if (files && files.length > 0) {
      const file = files[0];
      if (isValidFile(file)) {
        setSelectedFile(file);
        setUploadStatus('success');
        setErrorMessage(null);
      } else {
        setUploadStatus('error');
        setErrorMessage('Invalid file type. Please upload PDF, DOCX, JPG, or PNG.');
      }
    }
  };

  const isValidFile = (file: File): boolean => {
    const validTypes = [
      'application/pdf',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'image/jpeg',
      'image/png',
    ];
    const maxSize = 50 * 1024 * 1024;
    return validTypes.includes(file.type) && file.size <= maxSize;
  };

  const handleTranslate = async () => {
    if (!selectedFile) return;
    clearPolling();
    setPreviewData(null);
    setDocumentStructure(null);
    setStructureStatus('idle');
    setStructureMessage(null);
    setErrorMessage(null);
    setDetectedLanguage(null);
    setDetectionConfidence(null);
    setIsDetectingLanguage(false);
    setIsJobComplete(false);
    detectionShownRef.current = false;

    const response = await upload(selectedFile, sourceLanguage, targetLanguage);

    if (response) {
      setUploadId(response.job_id);
      setUploadStatus('success');
      setIsDetectingLanguage(true);
      detectStartRef.current = Date.now();

      // Poll for status to get detected language and completion
      pollStatus(response.job_id);
    } else {
      setUploadStatus('error');
      setErrorMessage(uploadError || 'Translation failed. Please try again.');
    }
  };

  const pollStatus = async (jobId: string) => {
    let attempts = 0;
    let consecutiveNetworkErrors = 0;
    const maxAttempts = 120; // ~3 minutes at 1.5s interval

    const poll = async () => {
      if (!isMountedRef.current) {
        clearPolling();
        return;
      }

      if (attempts >= maxAttempts) {
        clearPolling();
        setIsDetectingLanguage(false);
        setErrorMessage('Translation is taking too long. Please try again.');
        return;
      }
      attempts++;

      try {
        pollAbortRef.current?.abort();
        const controller = new AbortController();
        pollAbortRef.current = controller;
        const res = await fetch(`${API_BASE}/status/${jobId}`, { signal: controller.signal });
        pollAbortRef.current = null;

        if (!res.ok) {
          consecutiveNetworkErrors++;
          if (consecutiveNetworkErrors >= 5) {
            clearPolling();
            setIsDetectingLanguage(false);
            setErrorMessage(`Status check failed (HTTP ${res.status}).`);
            return;
          }
          pollTimerRef.current = setTimeout(poll, 1500);
          return;
        }

        consecutiveNetworkErrors = 0;
        const data = await res.json();

        if (data.detected_language && !detectionShownRef.current) {
          detectionShownRef.current = true;
          const elapsed = Date.now() - detectStartRef.current;
          const delay = Math.max(0, 1500 - elapsed);
          detectionTimerRef.current = setTimeout(() => {
            if (!isMountedRef.current) return;
            setDetectedLanguage(formatLanguageName(data.detected_language));
            setDetectionConfidence(data.detection_confidence ?? null);
            setIsDetectingLanguage(false);
          }, delay);
        }

        if (data.status === 'completed') {
          clearPolling();
          setIsDetectingLanguage(false);
          setIsJobComplete(true);
          setStructureStatus('loading');
          setStructureMessage(null);
          try {
            const [previewResult, structureResult] = await Promise.allSettled([
              fetch(`${API_BASE}/preview/${jobId}`),
              fetch(`${API_BASE}/structure/${jobId}`),
            ]);

            if (previewResult.status === 'fulfilled' && previewResult.value.ok) {
              setPreviewData(await previewResult.value.json());
            }

            if (structureResult.status === 'fulfilled') {
              const structureResponse = structureResult.value;
              if (structureResponse.ok) {
                setDocumentStructure(await structureResponse.json());
                setStructureStatus('ready');
              } else {
                setStructureStatus('fallback');
                setStructureMessage(`/structure/${jobId} returned HTTP ${structureResponse.status}. Showing preview fallback.`);
              }
            } else {
              setStructureStatus('error');
              setStructureMessage('Structured bilingual data could not be loaded. Showing preview fallback when available.');
            }
          } catch {
            setStructureStatus('error');
            setStructureMessage('Preview data could not be loaded, but the translated file may still be available for download.');
          }
          return;
        }

        if (data.status === 'failed') {
          clearPolling();
          setIsDetectingLanguage(false);
          setErrorMessage(data.error || data.message || 'Translation failed. Please try again.');
          return;
        }

        if (data.status === 'not_found') {
          clearPolling();
          setIsDetectingLanguage(false);
          setErrorMessage('Job not found on the backend. Please upload again.');
          return;
        }

        if (data.status === 'processing' || data.status === 'queued') {
          pollTimerRef.current = setTimeout(poll, 1500);
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return;
        }
        consecutiveNetworkErrors++;
        if (consecutiveNetworkErrors >= 5) {
          clearPolling();
          setIsDetectingLanguage(false);
          setErrorMessage('Lost connection to backend server. Please check that it is running.');
          return;
        }
        pollTimerRef.current = setTimeout(poll, 1500);
      }
    };

    poll();
  };

  const handleDownload = async () => {
    if (!uploadId) {
      setErrorMessage('Missing job ID. Please translate again.');
      return;
    }
    try {
      const response = await fetch(`${API_BASE}/download/${uploadId}`);
      if (response.status === 404) {
        setErrorMessage('Translated PDF was not created. Please check backend logs.');
        return;
      }
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        setErrorMessage(data.error || `Download failed (${response.status}). Please try again.`);
        return;
      }
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `translated-${selectedFile?.name || 'document'}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch {
      setErrorMessage('Backend server is not running. Please start the backend.');
    }
  };

  const handleSwapLanguages = () => {
    if (sourceLanguage === 'auto') return;
    const src = sourceLanguage;
    setSourceLanguage(targetLanguage);
    setTargetLanguage(src);
  };

  const resetTranslation = () => {
    clearPolling();
    setPreviewData(null);
    setDocumentStructure(null);
    setStructureStatus('idle');
    setStructureMessage(null);
    setSelectedFile(null);
    setUploadId(null);
    setUploadStatus('idle');
    setErrorMessage(null);
    setDetectedLanguage(null);
    setDetectionConfidence(null);
    setIsDetectingLanguage(false);
    setIsJobComplete(false);
    detectionShownRef.current = false;
  };

  const langLabel = (val: string) =>
    TARGET_LANGUAGES.find((l) => l.value === val)?.label ?? val;

  const getPreviewImageUrl = (jobId: string, imageValue?: string): string | null => {
    if (!jobId || !imageValue) return null;
    if (imageValue.startsWith('http://') || imageValue.startsWith('https://')) return imageValue;
    const normalized = imageValue.replace(/\\/g, '/');
    const fileName = normalized.split('/').pop();
    if (!fileName) return null;
    return `${API_BASE}/preview-image/${jobId}/${fileName}`;
  };

  const resolvePreviewUrl = (urlValue?: string): string | null => {
    if (!urlValue) return null;
    if (urlValue.startsWith('http://') || urlValue.startsWith('https://')) return urlValue;
    return `${API_BASE}${urlValue.startsWith('/') ? '' : '/'}${urlValue}`;
  };

  const firstOriginalPreview =
    resolvePreviewUrl(previewData?.left_page_preview) ??
    getPreviewImageUrl(previewData?.job_id, previewData?.original_pages?.[0]);

  const structureBlocks =
    documentStructure?.pages?.[0]?.blocks?.filter((block: any) => block.type === 'text') ?? [];
  const bilingualBlocks =
    structureBlocks.length > 0
      ? structureBlocks
      : previewData?.bilingual_first_page?.blocks ?? [];
  const canShowBilingualOutput = isJobComplete || previewData || documentStructure;

  return (
    <div className="min-h-screen bg-gradient-to-b from-background via-white to-background">
      <Navigation />

      <main className="max-w-5xl mx-auto px-6 py-16">
        {/* Header */}
        <div className="mb-12">
          <h1 className="text-4xl font-bold text-primary mb-3">Document Translator</h1>
          <p className="text-lg text-foreground/70">
            Translate between Bagobo-Tagabawa, Filipino, English, and Cebuano
          </p>
        </div>

        <div className="grid lg:grid-cols-3 gap-8">
          {/* Upload Section */}
          <div className="lg:col-span-2">
            <div
              onDragEnter={handleDragEnter}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`relative border-4 border-dashed rounded-2xl p-12 transition-all duration-300 text-center cursor-pointer ${
                isDragging
                  ? 'border-primary bg-primary/10 scale-[1.02]'
                  : 'border-primary/40 bg-white hover:border-primary/60 hover:bg-primary/5'
              }`}
            >
              <input
                type="file"
                onChange={handleFileInput}
                className="absolute inset-0 opacity-0 cursor-pointer"
                accept=".pdf,.docx,.jpg,.jpeg,.png"
              />

              {selectedFile ? (
                <div className="py-4">
                  <div className="w-16 h-16 bg-gradient-to-br from-primary to-accent rounded-full flex items-center justify-center mx-auto mb-4 shadow-lg">
                    <FileText className="w-8 h-8 text-white" />
                  </div>
                  <p className="text-xl font-bold text-primary mb-2">{selectedFile.name}</p>
                  <p className="text-sm text-foreground/60 mb-4">
                    {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                  </p>
                  <button
                    onClick={(e) => { e.stopPropagation(); resetTranslation(); }}
                    className="text-sm text-secondary hover:underline"
                  >
                    Choose different file
                  </button>
                </div>
              ) : (
                <>
                  <Upload className="w-16 h-16 text-primary/40 mx-auto mb-4" />
                  <p className="text-xl font-bold text-primary mb-2">
                    Drop your document here
                  </p>
                  <p className="text-foreground/60 mb-4">or click to browse</p>
                  <div className="flex justify-center gap-2 text-xs text-foreground/50">
                    {supportedFormats.map((fmt) => (
                      <span key={fmt} className="px-3 py-1 bg-accent/20 rounded-full">
                        {fmt}
                      </span>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* Error */}
            {(uploadStatus === 'error' || errorMessage) && (
              <div className="mt-4 p-4 bg-red-50 border border-red-200 rounded-lg flex items-center gap-3">
                <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0" />
                <p className="text-sm text-red-700">
                  {errorMessage || 'Invalid file. Please upload PDF, DOCX, JPG, or PNG.'}
                </p>
              </div>
            )}

            {/* Success */}
            {uploadStatus === 'success' && selectedFile && !uploadId && (
              <div className="mt-4 p-4 bg-green-50 border border-green-200 rounded-lg flex items-center gap-3">
                <Check className="w-5 h-5 text-green-600 flex-shrink-0" />
                <p className="text-sm text-green-700">File ready. Click Translate to begin.</p>
              </div>
            )}

            {/* Detected language result */}
            {(isDetectingLanguage || detectedLanguage) && (
              <div className="mt-4 p-4 bg-blue-50 border border-blue-200 rounded-lg">
                {isDetectingLanguage ? (
                  <p className="text-sm text-blue-600 flex items-center gap-2">
                    <span className="inline-block w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin shrink-0" />
                    Detecting source language...
                  </p>
                ) : (
                  <p className="text-sm text-blue-700">
                    ✓ Detected source language:{' '}
                    <strong>{detectedLanguage}</strong>
                    {detectionConfidence != null && (
                      <span className="ml-2 text-blue-500">
                        ({(detectionConfidence * 100).toFixed(0)}% confidence)
                      </span>
                    )}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Settings Sidebar */}
          <div className="space-y-4">
            {/* Source Language */}
            <div>
              <label className="flex items-center gap-2 text-sm font-bold text-primary mb-3">
                <Globe className="w-4 h-4" />
                Source Language
              </label>
              <select
                value={sourceLanguage}
                onChange={(e) => setSourceLanguage(e.target.value)}
                className="w-full px-4 py-3 border-2 border-primary/30 rounded-lg bg-white text-foreground focus:border-primary focus:outline-none transition-colors"
              >
                {SOURCE_LANGUAGES.map((lang) => (
                  <option key={lang.value} value={lang.value}>
                    {lang.label}
                  </option>
                ))}
              </select>
              {sourceLanguage === 'auto' && (
                <p className="text-xs text-purple-600 mt-1 flex items-center gap-1">
                  <span>✨</span>
                  <span>System will automatically detect the source language</span>
                </p>
              )}
            </div>

            {/* Swap button */}
            <div className="flex justify-center">
              <button
                onClick={handleSwapLanguages}
                disabled={sourceLanguage === 'auto'}
                title={
                  sourceLanguage === 'auto'
                    ? 'Cannot swap while auto-detect is active'
                    : 'Swap languages'
                }
                className={`p-2 rounded-full transition-all ${
                  sourceLanguage === 'auto'
                    ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                    : 'bg-primary/10 text-primary hover:bg-primary hover:text-white'
                }`}
              >
                <ArrowUpDown className="w-5 h-5" />
              </button>
            </div>

            {/* Target Language */}
            <div>
              <label className="flex items-center gap-2 text-sm font-bold text-secondary mb-3">
                <Globe className="w-4 h-4" />
                Target Language
              </label>
              <select
                value={targetLanguage}
                onChange={(e) => setTargetLanguage(e.target.value)}
                className="w-full px-4 py-3 border-2 border-secondary/30 rounded-lg bg-white text-foreground focus:border-secondary focus:outline-none transition-colors"
              >
                {TARGET_LANGUAGES.map((lang) => (
                  <option key={lang.value} value={lang.value}>
                    {lang.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Action Buttons */}
            <div className="pt-2 space-y-3">
              {(() => {
                const isProcessing = !!uploadId && !isJobComplete && !errorMessage;
                const hasFailed = !!errorMessage && !isJobComplete;
                const canStart = !!selectedFile && !isUploading && !uploadId && !errorMessage;

                // Failed state: show Try Again
                if (hasFailed) {
                  return (
                    <button
                      onClick={resetTranslation}
                      className="w-full py-4 font-bold rounded-lg transition-all duration-200 text-white bg-gradient-to-r from-red-500 to-red-600 hover:shadow-xl hover:scale-105 cursor-pointer"
                    >
                      Try Again
                    </button>
                  );
                }

                // Completed state: show Download button instead
                if (isJobComplete) {
                  return (
                    <button
                      onClick={handleDownload}
                      className="w-full py-4 font-bold rounded-lg transition-all duration-200 text-white bg-gradient-to-r from-accent to-primary hover:shadow-xl hover:scale-105 cursor-pointer flex items-center justify-center gap-2"
                    >
                      <Download className="w-5 h-5" />
                      Download Translated File
                    </button>
                  );
                }

                // Default/Start/Uploading/Processing
                return (
                  <button
                    onClick={handleTranslate}
                    disabled={!canStart}
                    className={`w-full py-4 font-bold rounded-lg transition-all duration-200 text-white ${
                      canStart
                        ? 'bg-gradient-to-r from-primary to-secondary hover:shadow-xl hover:scale-105 cursor-pointer'
                        : 'bg-foreground/30 cursor-not-allowed opacity-60'
                    }`}
                  >
                    {isUploading ? (
                      <div className="flex items-center justify-center gap-2">
                        <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                        Uploading...
                      </div>
                    ) : isProcessing ? (
                      <div className="flex items-center justify-center gap-2">
                        <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                        Processing...
                      </div>
                    ) : !selectedFile ? (
                      'Start Translation'
                    ) : (
                      `Translate -> ${langLabel(targetLanguage)}`
                    )}
                  </button>
                );
              })()}
            </div>

            {/* Info box */}
            <div className="p-4 bg-accent/20 border-2 border-accent/30 rounded-lg space-y-2">
              <p className="text-xs text-foreground/70 leading-relaxed">
                <span className="font-bold text-accent">💡 Tip:</span> Layout, images, and
                formatting are preserved during translation.
              </p>
              <p className="text-xs text-foreground/70 leading-relaxed">
                <span className="font-bold text-primary">🔍 Auto-Detect:</span> Automatically
                identifies English, Filipino, Cebuano, or Bagobo-Tagabawa.
              </p>
            </div>

            {/* Supported languages card */}
            <div className="p-4 bg-primary/10 rounded-lg">
              <h4 className="text-xs font-bold text-primary mb-2">Supported Languages</h4>
              <ul className="text-xs text-foreground/70 space-y-1">
                <li>🟠 Bagobo-Tagabawa (Indigenous)</li>
                <li>🔵 Filipino / Tagalog (National)</li>
                <li>🟢 English (Educational)</li>
                <li>🟡 Cebuano (Regional)</li>
              </ul>
            </div>
          </div>
        </div>

        {canShowBilingualOutput && (
          <div className="mt-10 space-y-5">
            <div className="rounded-xl border border-primary/20 bg-white p-4">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h3 className="text-lg font-bold text-primary">Bilingual Output</h3>
                  <p className="text-sm text-foreground/60">
                    Page 1 blocks are shown in reading order.
                  </p>
                </div>
                {structureStatus === 'loading' && (
                  <p className="text-sm text-blue-600 flex items-center gap-2">
                    <span className="inline-block w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin shrink-0" />
                    Loading structured blocks...
                  </p>
                )}
              </div>

              {(structureStatus === 'fallback' || structureStatus === 'error') && structureMessage && (
                <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                  {structureMessage}
                </div>
              )}

              {bilingualBlocks.length > 0 ? (
                <div className="mt-4 space-y-3">
                  {bilingualBlocks.map((block: any, index: number) => {
                    const sourceText = block.source_text ?? block.original_text ?? '-';
                    const translatedText = block.translated_text ?? 'UNKNOWN_FOR_REVIEW';
                    const needsReview = !block.translated_text || block.translated_text === 'UNKNOWN_FOR_REVIEW';

                    return (
                      <div key={block.block_id ?? index} className="rounded-lg border border-foreground/10">
                        <div className="grid gap-0 md:grid-cols-2">
                          <div className="border-b border-foreground/10 p-4 md:border-b-0 md:border-r">
                            <p className="mb-2 text-xs font-bold uppercase text-foreground/45">Original</p>
                            <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/85">{sourceText}</p>
                          </div>
                          <div className="p-4">
                            <p className="mb-2 text-xs font-bold uppercase text-foreground/45">Translation</p>
                            <p className={`whitespace-pre-wrap text-sm leading-relaxed ${needsReview ? 'font-semibold text-red-600' : 'text-foreground/85'}`}>
                              {translatedText}
                            </p>
                          </div>
                        </div>
                        {(block.translation_method || block.cascade_stage || block.translation_confidence != null) && (
                          <div className="border-t border-foreground/10 px-4 py-2 text-xs text-foreground/55">
                            {block.translation_method ?? block.cascade_stage ?? 'unknown'}
                            {block.translation_confidence != null && ` - ${(block.translation_confidence * 100).toFixed(0)}%`}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="mt-4 rounded-lg border border-foreground/10 p-4 text-sm text-foreground/70">
                  {structureStatus === 'loading'
                    ? 'Waiting for structured bilingual blocks...'
                    : 'No bilingual text blocks are available for this job yet.'}
                </div>
              )}
            </div>

            <div className="rounded-xl border border-primary/20 bg-white p-4">
              <h3 className="mb-3 text-lg font-bold text-primary">Original Page Preview</h3>
              {firstOriginalPreview ? (
                <img src={firstOriginalPreview} alt="Original page preview" className="w-full rounded-lg border" />
              ) : (
                <p className="text-sm text-foreground/70">No original preview available.</p>
              )}
            </div>
          </div>
        )}

        {/* Feature cards */}
        <div className="mt-20 grid md:grid-cols-4 gap-6">
          <div className="p-6 bg-white border-2 border-purple-200 rounded-xl">
            <div className="text-3xl font-bold text-purple-600 mb-2">✓</div>
            <h3 className="font-bold text-purple-600 mb-2">Auto-Detect</h3>
            <p className="text-sm text-foreground/70">
              Automatically identifies the source language of your document
            </p>
          </div>
          <div className="p-6 bg-white border-2 border-primary/20 rounded-xl">
            <div className="text-3xl font-bold text-primary mb-2">✓</div>
            <h3 className="font-bold text-primary mb-2">4 Languages</h3>
            <p className="text-sm text-foreground/70">
              Translate between Tagabawa, Filipino, English, and Cebuano
            </p>
          </div>
          <div className="p-6 bg-white border-2 border-secondary/20 rounded-xl">
            <div className="text-3xl font-bold text-secondary mb-2">✓</div>
            <h3 className="font-bold text-secondary mb-2">Smart Layout</h3>
            <p className="text-sm text-foreground/70">
              Preserves document structure and formatting
            </p>
          </div>
          <div className="p-6 bg-white border-2 border-accent/20 rounded-xl">
            <div className="text-3xl font-bold text-accent mb-2">✓</div>
            <h3 className="font-bold text-accent mb-2">Fast Download</h3>
            <p className="text-sm text-foreground/70">Get your translated PDF ready in moments</p>
          </div>
        </div>
      </main>
    </div>
  );
}
