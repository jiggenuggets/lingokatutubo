import { useState } from 'react';
import { getApiBaseUrl } from '@/lib/api-base';

const API_BASE = getApiBaseUrl();

export interface UploadResponse {
  job_id: string;
  status: string;
  message: string;
  progress_percent?: number;
  current_phase?: string;
  current_step?: string;
  phase_message?: string;
}

export interface UploadError {
  detail: string | Array<{ msg: string; field: string }>;
}

export function useUpload() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = async (
    file: File,
    sourceLanguage: string,
    targetLanguage: string
  ): Promise<UploadResponse | null> => {
    setIsLoading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('source_language', sourceLanguage);
      formData.append('target_language', targetLanguage);

      const response = await fetch(`${API_BASE}/translate`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const errorData: UploadError = await response.json();
        const errorMessage =
          typeof errorData.detail === 'string'
            ? errorData.detail
            : Array.isArray(errorData.detail)
            ? errorData.detail.map((e) => e.msg).join(', ')
            : 'Upload failed';
        setError(errorMessage);
        return null;
      }

      const data: UploadResponse = await response.json();
      return data;
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'An error occurred during upload';
      setError(message);
      return null;
    } finally {
      setIsLoading(false);
    }
  };

  return {
    upload,
    isLoading,
    error,
  };
}
