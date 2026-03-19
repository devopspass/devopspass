import { Injectable } from '@angular/core';

import { AuthService } from './auth.service';

export interface PluginApp {
  plugin_key: string;
  app_id?: string;
  name: string;
  description: string;
  description_long: string;
  icon?: string;
  uniq?: boolean;
  category?: string;
  check_script?: string;
  agent_provider?: boolean;
  doc_types: Array<Record<string, unknown> & { key: string; title: string }>;
  settings: Record<string, {
    title: string;
    description: string;
    mandatory: boolean;
    type: string;
    default?: string | boolean | null;
    options: string[];
  }>;
}

export interface StoredDoc {
  id: number;
  app_id: string | null;
  doc_type: string;
  content: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  fact?: string | null;
}

export interface JobLogEntry {
  stream: 'stdout' | 'stderr';
  timestamp: string;
  entry: string;
}

export interface AgentActivityEntry {
  type: string;
  text: string;
  timestamp: string;
}

export interface JobInfo {
  id: string;
  job_type: string;
  status: 'queued' | 'blocked' | 'running' | 'success' | 'failed' | 'cancelled';
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  app_doc_id: number;
  app_id: string | null;
  dop_app_name: string | null;
  dop_app_icon: string | null;
  doc_type: string;
  doc_type_title: string;
  doc_name: string | null;
  summary: string | null;
  failure: string | null;
  result: Record<string, unknown> | null;
  can_cancel?: boolean;
  action_name?: string;
  thread_id?: number;
  depends_on_job_ids?: string[];
  dependent_job_ids?: string[];
  workflow_id?: string;
  workflow_max_parallel?: number;
  blocking_reason?: string | null;
  logs?: JobLogEntry[];
  agent_events?: AgentActivityEntry[];
}

export interface AskPassRequest {
  request_id: string;
  job_id: string;
  prompt: string;
  prompt_kind?: 'username' | 'password' | 'ssh_passphrase';
  can_save?: boolean;
}

export interface AskPassResponse {
  answered: boolean;
}

export interface AgentStreamEvent {
  type: string;
  text?: string;
  timestamp?: string;
  status?: string;
  message?: string;
}

export interface ProductResourceRef {
  app_id: string;
  doc_type: string;
  name: string;
  url?: string;
}

export interface SearchDocsResponse {
  results: StoredDoc[];
  total: number;
  offset: number;
  limit: number;
}

export interface ChatAgent {
  id: number;
  name: string;
  title: string;
  prompt: string;
  description?: string | null;
  model?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: number;
  thread_id: number;
  role: 'user' | 'assistant' | 'system';
  text: string;
  agent_mentions: ChatAgent[];
  doc_mentions: ProductResourceRef[];
  doc_mentions_docs: StoredDoc[];
  unresolved_doc_queries: string[];
  created_at: string;
}

export interface ChatMessageResponse {
  thread: ChatThread;
  job_id: string;
}

export interface ChatThread {
  id: number;
  name: string;
  attached_docs: ProductResourceRef[];
  attached_docs_docs: StoredDoc[];
  created_at: string;
  updated_at: string;
  message_count?: number;
  messages?: ChatMessage[];
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly baseUrl = this.resolveBaseUrl();

  constructor(private readonly auth: AuthService) {}

  private resolveBaseUrl(): string {
    const configured = (window as { __DOP_API_URL__?: string }).__DOP_API_URL__;
    if (configured !== undefined) {
      return configured;
    }

    if (window.location.port === '4200' || window.location.port === '4201') {
      return 'http://localhost:10818';
    }

    return '';
  }

  async listPluginApps(): Promise<PluginApp[]> {
    return this.request<PluginApp[]>('/api/plugin-apps');
  }

  async listApplications(): Promise<StoredDoc[]> {
    return this.request<StoredDoc[]>('/api/applications');
  }

  async listProducts(): Promise<StoredDoc[]> {
    return this.request<StoredDoc[]>('/api/products');
  }

  async addApplication(payload: { plugin_key: string; app_id: string; settings: Record<string, unknown> }): Promise<StoredDoc> {
    return this.request<StoredDoc>('/api/applications', { method: 'POST', body: JSON.stringify(payload) });
  }

  async testApplication(payload: { plugin_key: string; app_id?: string; settings: Record<string, unknown> }): Promise<{ status: 'success' | 'failed'; message: string }> {
    return this.request<{ status: 'success' | 'failed'; message: string }>('/api/applications/test', { method: 'POST', body: JSON.stringify(payload) });
  }

  async getApplication(appDocId: number): Promise<StoredDoc> {
    return this.request<StoredDoc>(`/api/applications/${appDocId}`);
  }

  async updateApplication(appDocId: number, payload: { content: Record<string, unknown> }): Promise<StoredDoc> {
    return this.request<StoredDoc>(`/api/applications/${appDocId}`, { method: 'PUT', body: JSON.stringify(payload) });
  }

  async deleteApplication(appDocId: number): Promise<{ deleted: boolean }> {
    return this.request<{ deleted: boolean }>(`/api/applications/${appDocId}`, { method: 'DELETE' });
  }

  async addProduct(payload: {
    product_id: string;
    name: string;
    prompt?: string;
    description?: string;
    icon?: string;
    url?: string;
    resources: ProductResourceRef[];
  }): Promise<StoredDoc> {
    return this.request<StoredDoc>('/api/products', { method: 'POST', body: JSON.stringify(payload) });
  }

  async getProduct(productDocId: number): Promise<StoredDoc> {
    return this.request<StoredDoc>(`/api/products/${productDocId}`);
  }

  async updateProduct(productDocId: number, payload: {
    name?: string;
    prompt?: string;
    description?: string;
    icon?: string;
    url?: string;
    resources?: ProductResourceRef[];
  }): Promise<StoredDoc> {
    return this.request<StoredDoc>(`/api/products/${productDocId}`, { method: 'PUT', body: JSON.stringify(payload) });
  }

  async deleteProduct(productDocId: number): Promise<{ deleted: boolean }> {
    return this.request<{ deleted: boolean }>(`/api/products/${productDocId}`, { method: 'DELETE' });
  }

  async listChatThreads(): Promise<ChatThread[]> {
    return this.request<ChatThread[]>('/api/chat/threads');
  }

  async getChatThread(threadId: number): Promise<ChatThread> {
    return this.request<ChatThread>(`/api/chat/threads/${threadId}`);
  }

  async createChatThread(payload: { name: string; attached_docs: ProductResourceRef[] }): Promise<ChatThread> {
    return this.request<ChatThread>('/api/chat/threads', { method: 'POST', body: JSON.stringify(payload) });
  }

  async updateChatThread(threadId: number, payload: { name?: string; attached_docs?: ProductResourceRef[] }): Promise<ChatThread> {
    return this.request<ChatThread>(`/api/chat/threads/${threadId}`, { method: 'PUT', body: JSON.stringify(payload) });
  }

  async deleteChatThread(threadId: number): Promise<{ deleted: boolean }> {
    return this.request<{ deleted: boolean }>(`/api/chat/threads/${threadId}`, { method: 'DELETE' });
  }

  async sendChatMessage(threadId: number, payload: {
    text: string;
    agent_mentions?: string[];
    doc_mentions?: ProductResourceRef[];
  }): Promise<ChatMessageResponse> {
    return this.request<ChatMessageResponse>(`/api/chat/threads/${threadId}/messages`, { method: 'POST', body: JSON.stringify(payload) });
  }

  async listChatAgents(): Promise<ChatAgent[]> {
    return this.request<ChatAgent[]>('/api/chat/agents');
  }

  async getChatAgent(agentId: number): Promise<ChatAgent> {
    return this.request<ChatAgent>(`/api/chat/agents/${agentId}`);
  }

  async createChatAgent(payload: { name: string; title?: string; prompt: string; description?: string; model?: string }): Promise<ChatAgent> {
    return this.request<ChatAgent>('/api/chat/agents', { method: 'POST', body: JSON.stringify(payload) });
  }

  async updateChatAgent(agentId: number, payload: { name?: string; title?: string; prompt?: string; description?: string; model?: string }): Promise<ChatAgent> {
    return this.request<ChatAgent>(`/api/chat/agents/${agentId}`, { method: 'PUT', body: JSON.stringify(payload) });
  }

  async deleteChatAgent(agentId: number): Promise<{ deleted: boolean }> {
    return this.request<{ deleted: boolean }>(`/api/chat/agents/${agentId}`, { method: 'DELETE' });
  }

  async searchDocs(params: { q?: string; doc_type?: string; app_id?: string; offset?: number; limit?: number }): Promise<SearchDocsResponse> {
    const search = new URLSearchParams();
    if (params.q) search.set('q', params.q);
    if (params.doc_type) search.set('doc_type', params.doc_type);
    if (params.app_id) search.set('app_id', params.app_id);
    if (params.offset !== undefined) search.set('offset', String(params.offset));
    if (params.limit !== undefined) search.set('limit', String(params.limit));
    return this.request<SearchDocsResponse>(`/api/docs?${search.toString()}`);
  }

  async getDoc(docId: number): Promise<StoredDoc> {
    return this.request<StoredDoc>(`/api/docs/${docId}`);
  }

  async reloadConfigs(): Promise<{ reloaded: boolean; apps_count: number }> {
    return this.request<{ reloaded: boolean; apps_count: number }>('/api/configs/reload', { method: 'POST' });
  }

  async listJobs(): Promise<JobInfo[]> {
    return this.request<JobInfo[]>('/api/jobs');
  }

  async getJob(jobId: string): Promise<JobInfo> {
    return this.request<JobInfo>(`/api/jobs/${jobId}`);
  }

  async cancelJob(jobId: string): Promise<JobInfo> {
    return this.request<JobInfo>(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
  }

  async createDocsRefreshJob(payload: {
    app_doc_id?: number;
    app_id?: string;
    doc_type: string;
    depends_on_job_ids?: string[];
    workflow_id?: string;
    max_parallel?: number;
  }): Promise<JobInfo> {
    return this.request<JobInfo>('/api/jobs/docs-refresh', { method: 'POST', body: JSON.stringify(payload) });
  }

  async createDocActionJob(payload: {
    doc_id: number;
    action_name: string;
    depends_on_job_ids?: string[];
    workflow_id?: string;
    max_parallel?: number;
  }): Promise<JobInfo> {
    return this.request<JobInfo>('/api/jobs/doc-action', { method: 'POST', body: JSON.stringify(payload) });
  }

  async answerAskPassRequest(requestId: string, password: string, save = false): Promise<AskPassResponse> {
    return this.request<AskPassResponse>(`/api/askpass/answer/${requestId}`, {
      method: 'POST',
      body: JSON.stringify({ password, save })
    });
  }

  async cancelAskPassRequest(requestId: string): Promise<AskPassResponse> {
    return this.request<AskPassResponse>(`/api/askpass/cancel/${requestId}`, {
      method: 'POST'
    });
  }

  async getJobAskPassRequests(jobId: string): Promise<AskPassRequest[]> {
    return this.request<AskPassRequest[]>(`/api/jobs/${jobId}/askpass`);
  }

  streamJobEvents(
    jobId: string,
    onEvent: (event: AgentStreamEvent) => void,
    onDone: (status: string) => void,
  ): EventSource {
    const url = `${this.baseUrl}/api/jobs/${encodeURIComponent(jobId)}/stream`;
    const source = new EventSource(url);

    source.onmessage = (msg) => {
      try {
        const event = JSON.parse(String(msg.data)) as AgentStreamEvent;
        if (event.type === 'done' || event.type === 'error') {
          source.close();
          onDone(event.status ?? event.type);
        } else {
          onEvent(event);
        }
      } catch {
        // malformed SSE payload — ignore
      }
    };

    source.onerror = () => {
      source.close();
      onDone('error');
    };

    return source;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = await this.auth.getIdToken();
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
    });

    if (!response.ok) {
      const message = await response.text();
      throw new Error(message || `Request failed: ${response.status}`);
    }

    return response.json() as Promise<T>;
  }
}
