import { API_URL } from '@/config/endpoints';

export type CheckInKey = 'speaker' | 'mic' | 'camera' | 'screen';

export type JobListItem = {
  job_uid: string;
  name: string;
  question_count: number;
  created_at: string;
};

export type JobDetail = {
  job_uid: string;
  name: string;
  duties: string;
  requirements: string;
  notes?: string | null;
  csv_filename?: string | null;
  created_at: string;
  updated_at: string;
  questions: Array<{
    id: number;
    question: string;
    reference_answer: string;
    ability_dimension: string;
    scoring_boundary: string;
    best_standard: string;
    medium_standard: string;
    worst_standard: string;
    output_format: string;
    sort_order: number;
  }>;
};

export type InterviewListItem = {
  token: string;
  candidate_name: string;
  duration_minutes: number;
  question_count: number;
  notes?: string | null;
  status: string;
  created_at: string;
  completed_at?: string | null;
  job: {
    job_uid: string;
    name: string;
  };
};

export type InterviewDetail = {
  token: string;
  candidate_name: string;
  duration_minutes: number;
  question_count: number;
  notes?: string | null;
  status: string;
  created_at: string;
  completed_at?: string | null;
  job: {
    job_uid: string;
    name: string;
  };
  selected_questions?: Array<{
    sort_order: number;
    question: string;
  }>;
  required_checkins: CheckInKey[];
  interview_link: string;
  completed: boolean;
  completion_message?: string;
  turns?: Array<{
    role: string;
    content: string;
    created_at: string;
    sort_order: number;
  }>;
  audio?: {
    candidate_url: string;
    interviewer_url: string;
  };
};

const request = async <T>(path: string, init?: RequestInit): Promise<T> => {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      credentials: 'include',
    });
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error('无法连接后台服务，请确认 Admin API 服务已启动并检查代理/跨域配置');
    }
    throw error instanceof Error ? error : new Error('请求失败，请稍后重试');
  }

  let data: any = null;
  try {
    data = await response.json();
  } catch (_error) {
    data = null;
  }
  if (!response.ok) {
    const detail =
      (data && typeof data.detail === 'string' && data.detail) ||
      `请求失败(${response.status})`;
    throw new Error(detail);
  }
  return data as T;
};

export const adminApi = {
  login: (username: string, password: string) =>
    request<{ ok: boolean }>('/api/admin/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }),

  logout: () =>
    request<{ ok: boolean }>('/api/admin/auth/logout', {
      method: 'POST',
    }),

  me: () => request<{ admin: { id: number; username: string } }>('/api/admin/auth/me'),

  listJobs: (q = '') =>
    request<{ items: JobListItem[]; total: number }>(
      `/api/admin/jobs?q=${encodeURIComponent(q)}&page=1&page_size=100`,
    ),

  getJob: (jobUid: string) => request<{ job: JobDetail }>(`/api/admin/jobs/${jobUid}`),

  createJob: (form: FormData) =>
    request<{ job: JobListItem }>(`/api/admin/jobs`, {
      method: 'POST',
      body: form,
    }),

  deleteJob: (jobUid: string) =>
    request<{ ok: boolean }>(`/api/admin/jobs/${jobUid}`, {
      method: 'DELETE',
    }),

  listInterviews: (q = '') =>
    request<{ items: InterviewListItem[]; total: number }>(
      `/api/admin/interviews?q=${encodeURIComponent(q)}&page=1&page_size=100`,
    ),

  createInterview: (payload: {
    candidate_name: string;
    job_uid: string;
    duration_minutes: number;
    notes?: string;
    required_checkins?: CheckInKey[];
  }) =>
    request<{ interview: InterviewListItem & { interview_link: string } }>(
      '/api/admin/interviews',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    ),

  getInterview: (token: string) =>
    request<{ interview: InterviewDetail }>(`/api/admin/interviews/${token}`),

  deleteInterview: (token: string) =>
    request<{ ok: boolean }>(`/api/admin/interviews/${token}`, {
      method: 'DELETE',
    }),
};
