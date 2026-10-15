export interface Source {
  id: string;
  title: string;
  edition_id: string;
  year: number;
  volume: string;
  source_url: string;
  page_count: number;
  rights_status: string;
  recognized_pages: number;
  translated_pages: number;
  reviewed_pages: number;
  first_recognized_page?: number;
}
export interface Version {
  id: string;
  transcription: string;
  translation: string;
  notes: string;
  published: number;
  reviewed: number;
  method: string;
  model: string;
  created_at: string;
  evidence_status?: string;
}
export interface Page {
  id: string;
  source_id: string;
  title: string;
  volume: string;
  scan_index: number;
  page_count: number;
  source_url: string;
  version: Version | null;
}
export interface Citation {
  citation_id: string;
  page_id: string;
  title: string;
  volume: string;
  scan_index: number;
  quote: string;
  reader_url: string;
  reviewed: boolean;
  evidence_status?: string;
}
export interface SearchHit {
  citation_id: string;
  title: string;
  volume: string;
  scan_index: number;
  transcription: string;
  translation: string;
  reviewed: number;
  reader_url: string;
  evidence_status?: string;
}
export interface ReadingOrderPage {
  id: string;
  sequence: number;
  scan_index: number;
  reading_order: number | null;
  original_page_no: number | null;
  leaf_label: string;
  side: string;
  page_type: string;
  version_count: number;
  current_version_id: string | null;
}
export interface EditionMaterial {
  id: string;
  title: string;
  year: number | null;
  volume: string;
  source_volume: string;
  verified_volume: string;
  material_type: string;
  page_count: number;
  pages: number;
  recognized: number;
}
export interface TimelineEvent {
  id: string;
  sort_year: number;
  year_label: string;
  title: string;
  body: string;
  event_type: string;
  source_note: string;
  source_url: string;
  published: number;
}
export interface Result {
  answer?: string;
  citations?: Citation[];
  uncertainties?: string[];
  version_id?: string;
  mode: string;
  method?: string;
}
export interface RunEvent {
  id: number;
  type: string;
  created_at: string;
  tool?: string;
  model?: string;
  summary?: string;
  error?: string;
  result?: Result;
}
export class ApiError extends Error {
  status: number;
  payload: unknown;
  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

function messageOf(status: number, payload: unknown): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const nested = (detail as { detail?: unknown }).detail;
    if (typeof nested === 'string') return nested;
  }
  return `请求失败（${status}）`;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const hasBody = options.body !== undefined && options.body !== null;
  const headers: HeadersInit = { ...options.headers };
  if (hasBody && !(headers as Record<string, string>)['Content-Type']) {
    (headers as Record<string, string>)['Content-Type'] = 'application/json';
  }
  const response = await fetch('/api/v1' + path, { ...options, headers });
  const data = await response
    .json()
    .catch(() => ({ detail: '服务返回了无效响应。' }));
  if (!response.ok) {
    throw new ApiError(messageOf(response.status, data), response.status, data);
  }
  return data as T;
}
export const post = (data: unknown) => ({
  method: 'POST',
  body: JSON.stringify(data),
});
export const auth = (token: string) => ({ Authorization: 'Bearer ' + token });
