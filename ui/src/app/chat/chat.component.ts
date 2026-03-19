import { CommonModule } from '@angular/common';
import { Component, ElementRef, EventEmitter, Input, NgZone, OnDestroy, OnInit, Output, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import MarkdownIt from 'markdown-it';

import {
  ApiService,
  AgentStreamEvent,
  ChatAgent,
  ChatMessage,
  ChatThread,
  JobInfo,
  PluginApp,
  ProductResourceRef,
  StoredDoc,
} from '../api.service';

interface CancelledTranscriptJob {
  jobId: string;
  createdAt: string;
  finishedAt: string | null;
  events: AgentStreamEvent[];
}

type ChatTimelineItem =
  | { kind: 'message'; key: string; sortKey: number; message: ChatMessage }
  | { kind: 'cancelled-job'; key: string; sortKey: number; job: CancelledTranscriptJob };

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.css',
})
export class ChatComponent implements OnInit, OnDestroy {
  pendingUserMessage: ChatMessage | null = null;
  private static readonly SELECTED_THREAD_STORAGE_KEY = 'dop.chat.selectedThreadId';
  private readonly markdown = new MarkdownIt({
    html: false,
    linkify: true,
    typographer: false,
    breaks: true,
  });

  @ViewChild('messageInput') messageInput?: ElementRef<HTMLDivElement>;
  @ViewChild('messagesContainer') messagesContainer?: ElementRef<HTMLDivElement>;
  @Input() pluginApps: PluginApp[] = [];
  @Input() applications: StoredDoc[] = [];
  @Output() previewDoc = new EventEmitter<StoredDoc>();
  @Output() pendingJobsChange = new EventEmitter<boolean>();

  threads: ChatThread[] = [];
  selectedThread: ChatThread | null = null;
  agents: ChatAgent[] = [];

  loading = false;
  error = '';
  messageText = '';
  private readonly pendingChatJobByThreadId = new Map<number, string>();
  private readonly chatJobPollTimerByThreadId = new Map<number, number>();
  private readonly pollingInFlightThreadIds = new Set<number>();

  // Agent activity stream (live while job is running)
  private readonly agentActivityByThreadId = new Map<number, AgentStreamEvent[]>();
  // Completed activity transcript (kept after job finishes, until next message)
  readonly completedTranscriptByThreadId = new Map<number, AgentStreamEvent[]>();
  readonly completedTranscriptByMessageId = new Map<number, AgentStreamEvent[]>();
  readonly cancelledTranscriptJobsByThreadId = new Map<number, CancelledTranscriptJob[]>();
  // UI state: which thread transcripts are expanded
  readonly expandedTranscriptThreadIds = new Set<number>();
  readonly expandedTranscriptMessageIds = new Set<number>();
  readonly expandedCancelledTranscriptJobIds = new Set<string>();
  // Active EventSource connections
  private readonly activeEventSourceByThreadId = new Map<number, EventSource>();
  private readonly stoppingThreadIds = new Set<number>();

  showThreadDialog = false;
  editingThreadId: number | null = null;
  threadName = '';
  threadAttachedDocs: ProductResourceRef[] = [];
  resourceSearchText = '';
  resourcePickerOpen = false;
  activeResourceIndex = 0;
  resourceResults: StoredDoc[] = [];

  showAgentsDialog = false;
  showAgentEditDialog = false;
  showAgentPreviewDialog = false;
  editingAgentId: number | null = null;
  previewAgent: ChatAgent | null = null;
  agentName = '';
  agentTitle = '';
  agentDescription = '';
  agentModel = '';
  agentPrompt = '';

  messagePickerOpen = false;
  messagePickerMode: 'agent' | 'doc' = 'agent';
  activeMessagePickerIndex = 0;
  messageAgentSuggestions: ChatAgent[] = [];
  messageDocSuggestions: StoredDoc[] = [];
  private messageTriggerRange: Range | null = null;

  private resourceSearchTimer: number | null = null;
  private messageDocSearchTimer: number | null = null;

  constructor(private readonly api: ApiService, private readonly zone: NgZone) {}

  async ngOnInit(): Promise<void> {
    const preferredThreadId = this.readStoredSelectedThreadId();
    await Promise.all([this.loadThreads(preferredThreadId), this.loadAgents()]);
    if (this.selectedThread) {
      await this.syncThreadJobState(this.selectedThread.id);
    }
    this.notifyPendingJobsChange();
  }

  ngOnDestroy(): void {
    if (this.resourceSearchTimer !== null) {
      window.clearTimeout(this.resourceSearchTimer);
      this.resourceSearchTimer = null;
    }
    if (this.messageDocSearchTimer !== null) {
      window.clearTimeout(this.messageDocSearchTimer);
      this.messageDocSearchTimer = null;
    }
    for (const timerId of this.chatJobPollTimerByThreadId.values()) {
      window.clearInterval(timerId);
    }
    this.chatJobPollTimerByThreadId.clear();
    this.pendingChatJobByThreadId.clear();
    this.pollingInFlightThreadIds.clear();
    for (const source of this.activeEventSourceByThreadId.values()) {
      source.close();
    }
    this.activeEventSourceByThreadId.clear();
    this.notifyPendingJobsChange();
  }

  async loadThreads(selectThreadId?: number | null): Promise<void> {
    this.loading = true;
    this.error = '';

    try {
      this.threads = await this.api.listChatThreads();
      const requestedThreadId = selectThreadId ?? this.selectedThread?.id ?? this.readStoredSelectedThreadId();
      const fallbackThreadId = this.threads[0]?.id ?? null;
      const nextThreadId = requestedThreadId !== null && this.threads.some((thread) => thread.id === requestedThreadId)
        ? requestedThreadId
        : fallbackThreadId;

      if (nextThreadId !== null) {
        await this.selectThread(nextThreadId);
      } else {
        this.selectedThread = null;
        this.storeSelectedThreadId(null);
      }
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  async loadAgents(): Promise<void> {
    try {
      this.agents = await this.api.listChatAgents();
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  async selectThread(threadId: number): Promise<void> {
    try {
      this.selectedThread = this.mergeThreadMessages(this.selectedThread, await this.api.getChatThread(threadId));
      this.storeSelectedThreadId(this.selectedThread.id);
      await this.syncThreadJobState(this.selectedThread.id);
      this.scrollMessagesToBottom();
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  openCreateThreadDialog(): void {
    this.showThreadDialog = true;
    this.editingThreadId = null;
    this.threadName = '';
    this.threadAttachedDocs = [];
    this.resourceSearchText = '';
    this.resourcePickerOpen = false;
    this.activeResourceIndex = 0;
    this.resourceResults = [];
  }

  openEditThreadDialog(): void {
    if (!this.selectedThread) {
      return;
    }

    this.showThreadDialog = true;
    this.editingThreadId = this.selectedThread.id;
    this.threadName = this.selectedThread.name;
    this.threadAttachedDocs = [...this.selectedThread.attached_docs];
    this.resourceSearchText = '';
    this.resourcePickerOpen = false;
    this.activeResourceIndex = 0;
    this.resourceResults = [];
  }

  closeThreadDialog(): void {
    this.showThreadDialog = false;
    this.editingThreadId = null;
    this.resourcePickerOpen = false;
  }

  async saveThread(): Promise<void> {
    const name = this.threadName.trim();
    if (!name) {
      this.error = 'Chat name is required.';
      return;
    }

    this.loading = true;
    this.error = '';

    try {
      const payload = {
        name,
        attached_docs: [...this.threadAttachedDocs],
      };
      const thread = this.editingThreadId === null
        ? await this.api.createChatThread(payload)
        : await this.api.updateChatThread(this.editingThreadId, payload);

      this.closeThreadDialog();
      await this.loadThreads(thread.id);
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  async deleteSelectedThread(): Promise<void> {
    if (!this.selectedThread) {
      return;
    }

    const shouldDelete = window.confirm(`Delete chat ${this.selectedThread.name}?`);
    if (!shouldDelete) {
      return;
    }

    this.loading = true;
    this.error = '';

    try {
      const currentId = this.selectedThread.id;
      await this.api.deleteChatThread(currentId);
      this.selectedThread = null;
      const nextId = this.threads.find((thread) => thread.id !== currentId)?.id ?? null;
      await this.loadThreads(nextId);
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  async sendMessage(): Promise<void> {
    const composed = this.composeMessagePayload();
    const composedText = composed.text.trim();
    if (!this.selectedThread) {
      this.error = 'Create a chat first.';
      return;
    }
    if (this.isThreadPending(this.selectedThread.id)) {
      this.error = 'This chat already has a pending message job.';
      return;
    }
    if (!composedText) {
      this.error = 'Message text is required.';
      return;
    }

    this.error = '';
    const selectedThreadId = this.selectedThread.id;

    // Show user message immediately
    this.pendingUserMessage = {
      id: -1,
      thread_id: selectedThreadId,
      role: 'user',
      text: composedText,
      created_at: new Date().toISOString(),
      agent_mentions: [],
      doc_mentions: composed.doc_mentions,
      doc_mentions_docs: [],
      unresolved_doc_queries: [],
    };
    this.notifyPendingJobsChange();
    this.clearMessageEditor();
    this.closeMessagePicker();
    this.scrollMessagesToBottom();

    try {
      const response = await this.api.sendChatMessage(selectedThreadId, {
        text: composedText,
        agent_mentions: composed.agent_mentions,
        doc_mentions: composed.doc_mentions,
      });
      if (this.selectedThread?.id === selectedThreadId) {
        this.selectedThread = this.mergeThreadMessages(this.selectedThread, response.thread);
      }
      this.pendingUserMessage = null;
      this.notifyPendingJobsChange();
      this.scrollMessagesToBottom();
      this.startChatJobPolling(response.job_id, response.thread.id);
    } catch (error) {
      this.error = this.asError(error);
      this.pendingUserMessage = null;
      this.notifyPendingJobsChange();
    }
  }

  get selectedThreadPending(): boolean {
    return this.selectedThread !== null && this.isThreadPending(this.selectedThread.id);
  }

  get selectedThreadHasPendingUserMessage(): boolean {
    return this.selectedThread !== null && this.pendingUserMessage?.thread_id === this.selectedThread.id;
  }

  private isThreadPending(threadId: number): boolean {
    return this.pendingChatJobByThreadId.has(threadId);
  }

  private startChatJobPolling(jobId: string, threadId: number): void {
    this.stopChatJobPolling(threadId);
    // Reset only live activity for the new job. Keep the previous completed
    // transcript visible until this job finishes and replaces it.
    this.agentActivityByThreadId.delete(threadId);
    this.pendingChatJobByThreadId.set(threadId, jobId);
    this.notifyPendingJobsChange();
    const timerId = window.setInterval(() => {
      void this.pollChatJob(jobId, threadId);
    }, 1500);
    this.chatJobPollTimerByThreadId.set(threadId, timerId);
    this.startJobEventStream(jobId, threadId);
  }

  private async pollChatJob(jobId: string, threadId: number): Promise<void> {
    const trackedJobId = this.pendingChatJobByThreadId.get(threadId);
    if (trackedJobId !== jobId) {
      return;
    }
    if (this.pollingInFlightThreadIds.has(threadId)) {
      return;
    }

    this.pollingInFlightThreadIds.add(threadId);
    try {
      const job = await this.api.getJob(jobId);
      const persistedTranscript = this.extractTranscriptFromJob(job);
      const fallbackTranscript = this.completedTranscriptByThreadId.get(threadId) ?? [];
      const effectiveTranscript = persistedTranscript.length > 0 ? persistedTranscript : fallbackTranscript;
      if (job.status === 'success' || job.status === 'failed' || job.status === 'cancelled') {
        const updatedThread = await this.api.getChatThread(threadId);
        const updatedThreadList = await this.api.listChatThreads();
        this.zone.run(() => {
          if (job.status === 'cancelled' && effectiveTranscript.length > 0) {
            const cancelledJobs = this.cancelledTranscriptJobsByThreadId.get(threadId) ?? [];
            const nextCancelledJobs = cancelledJobs.filter((item) => item.jobId !== job.id);
            nextCancelledJobs.push({
              jobId: job.id,
              createdAt: job.created_at,
              finishedAt: job.finished_at,
              events: effectiveTranscript,
            });
            nextCancelledJobs.sort((a, b) => this.timestampMs(b.finishedAt ?? b.createdAt) - this.timestampMs(a.finishedAt ?? a.createdAt));
            this.cancelledTranscriptJobsByThreadId.set(threadId, nextCancelledJobs);
          } else if (effectiveTranscript.length > 0) {
            const assistantMessageId = this.findLatestAssistantMessageId(updatedThread);
            if (assistantMessageId !== null) {
              this.completedTranscriptByMessageId.set(assistantMessageId, effectiveTranscript);
            }
            this.completedTranscriptByThreadId.set(threadId, effectiveTranscript);
          }
          this.stopChatJobPolling(threadId);
          if (this.selectedThread?.id === threadId) {
            this.selectedThread = this.mergeThreadMessages(this.selectedThread, updatedThread);
          }
          this.threads = updatedThreadList;
          this.scrollMessagesToBottom();
        });
      }
    } catch {
      this.zone.run(() => {
        this.stopChatJobPolling(threadId);
      });
    } finally {
      this.pollingInFlightThreadIds.delete(threadId);
    }
  }

  private stopChatJobPolling(threadId: number): void {
    const timerId = this.chatJobPollTimerByThreadId.get(threadId);
    if (timerId !== undefined) {
      window.clearInterval(timerId);
      this.chatJobPollTimerByThreadId.delete(threadId);
    }
    this.pendingChatJobByThreadId.delete(threadId);
    this.pollingInFlightThreadIds.delete(threadId);
    this.stopJobEventStream(threadId);
    this.finalizeTranscript(threadId);
    this.notifyPendingJobsChange();
  }

  private startJobEventStream(jobId: string, threadId: number): void {
    const source = this.api.streamJobEvents(
      jobId,
      (event) => {
        this.zone.run(() => {
          const current = this.agentActivityByThreadId.get(threadId) ?? [];
          this.agentActivityByThreadId.set(threadId, [...current, event]);
          this.scrollMessagesToBottom();
        });
      },
      (_status) => {
        this.zone.run(() => {
          this.stopJobEventStream(threadId);
          this.finalizeTranscript(threadId);
        });
      },
    );
    this.activeEventSourceByThreadId.set(threadId, source);
  }

  private stopJobEventStream(threadId: number): void {
    const source = this.activeEventSourceByThreadId.get(threadId);
    if (source) {
      source.close();
      this.activeEventSourceByThreadId.delete(threadId);
    }
  }

  private finalizeTranscript(threadId: number): void {
    if (this.completedTranscriptByThreadId.has(threadId)) {
      return; // already captured
    }
    const pending = this.agentActivityByThreadId.get(threadId) ?? [];
    if (pending.length > 0) {
      this.completedTranscriptByThreadId.set(threadId, [...pending]);
    }
    this.agentActivityByThreadId.delete(threadId);
  }

  getAgentActivity(threadId: number): AgentStreamEvent[] {
    return this.agentActivityByThreadId.get(threadId) ?? [];
  }

  getCompletedTranscript(threadId: number): AgentStreamEvent[] {
    return this.completedTranscriptByThreadId.get(threadId) ?? [];
  }

  getMessageTranscript(messageId: number): AgentStreamEvent[] {
    return this.completedTranscriptByMessageId.get(messageId) ?? [];
  }

  getVisibleTranscript(thread: ChatThread, message: ChatMessage): AgentStreamEvent[] {
    const direct = this.getMessageTranscript(message.id);
    if (direct.length > 0) {
      return direct;
    }
    if (message.role !== 'user' && this.isLastAssistantMessage(thread, message)) {
      return this.getCompletedTranscript(thread.id);
    }
    return [];
  }

  private async syncThreadJobState(threadId: number): Promise<void> {
    try {
      const jobs = await this.api.listJobs();
      const threadJobs = jobs.filter((job) => job.job_type === 'chat_message' && job.thread_id === threadId);

      const pendingJob = threadJobs
        .filter((job) => job.status === 'queued' || job.status === 'running')
        .sort((a, b) => this.jobTimestampMs(b) - this.jobTimestampMs(a))[0];

      if (pendingJob) {
        const trackedJobId = this.pendingChatJobByThreadId.get(threadId);
        if (trackedJobId !== pendingJob.id) {
          this.startChatJobPolling(pendingJob.id, threadId);
        } else if (!this.activeEventSourceByThreadId.has(threadId)) {
          this.startJobEventStream(pendingJob.id, threadId);
        }
      } else if (this.pendingChatJobByThreadId.has(threadId)) {
        this.stopChatJobPolling(threadId);
      }

      if (!this.completedTranscriptByThreadId.has(threadId)) {
        this.completedTranscriptByThreadId.delete(threadId);
      }

      if (this.selectedThread?.id === threadId) {
        const currentMessageIds = new Set((this.selectedThread.messages ?? []).map((message) => message.id));
        for (const messageId of Array.from(this.completedTranscriptByMessageId.keys())) {
          if (!currentMessageIds.has(messageId)) {
            this.completedTranscriptByMessageId.delete(messageId);
          }
        }
      }

      const completedJobs = threadJobs
        .filter((job) => job.status === 'success' || job.status === 'failed')
        .sort((a, b) => this.jobTimestampMs(a) - this.jobTimestampMs(b));

      const detailedCompletedJobs: JobInfo[] = [];
      for (const completedJob of completedJobs) {
        detailedCompletedJobs.push(await this.api.getJob(completedJob.id));
      }

      const selectedThreadSnapshot = this.selectedThread?.id === threadId ? this.selectedThread : await this.api.getChatThread(threadId);
      const usedAssistantMessageIds = new Set<number>();
      let lastTranscript: AgentStreamEvent[] = [];
      for (const detailedJob of detailedCompletedJobs) {
        const transcript = this.extractTranscriptFromJob(detailedJob);
        if (transcript.length === 0) {
          continue;
        }

        const matchedMessageId = this.findAssistantMessageIdForJob(selectedThreadSnapshot, detailedJob, usedAssistantMessageIds);
        if (matchedMessageId !== null) {
          this.completedTranscriptByMessageId.set(matchedMessageId, transcript);
          usedAssistantMessageIds.add(matchedMessageId);
        }
        lastTranscript = transcript;
      }

      if (lastTranscript.length > 0) {
        this.completedTranscriptByThreadId.set(threadId, lastTranscript);
      }

      const cancelledJobs = threadJobs
        .filter((job) => job.status === 'cancelled')
        .sort((a, b) => this.jobTimestampMs(a) - this.jobTimestampMs(b));

      const cancelledTranscripts: CancelledTranscriptJob[] = [];
      for (const cancelledJob of cancelledJobs) {
        const detailedJob = await this.api.getJob(cancelledJob.id);
        const transcript = this.extractTranscriptFromJob(detailedJob);
        if (transcript.length === 0) {
          continue;
        }
        cancelledTranscripts.push({
          jobId: cancelledJob.id,
          createdAt: cancelledJob.created_at,
          finishedAt: cancelledJob.finished_at,
          events: transcript,
        });
      }

      if (cancelledTranscripts.length > 0) {
        this.cancelledTranscriptJobsByThreadId.set(threadId, cancelledTranscripts);
      } else {
        this.cancelledTranscriptJobsByThreadId.delete(threadId);
      }

      this.notifyPendingJobsChange();
    } catch {
      // Keep thread UI responsive even if job-state sync fails temporarily.
    }
  }

  private extractTranscriptFromJob(job: JobInfo): AgentStreamEvent[] {
    return (job.agent_events ?? [])
      .map((event) => ({
        type: event.type,
        text: event.text,
        timestamp: event.timestamp,
      }))
      .filter((event) => typeof event.text === 'string' && event.text.trim().length > 0);
  }

  private jobTimestampMs(job: JobInfo): number {
    const value = job.finished_at ?? job.started_at ?? job.created_at;
    return this.timestampMs(value);
  }

  private timestampMs(value: string | null | undefined): number {
    if (!value) {
      return 0;
    }
    const timestamp = Date.parse(value);
    return Number.isNaN(timestamp) ? 0 : timestamp;
  }

  private mergeThreadMessages(current: ChatThread | null, next: ChatThread): ChatThread {
    if (!current || current.id !== next.id) {
      return next;
    }

    const mergedMessagesById = new Map<number, ChatMessage>();
    for (const message of current.messages ?? []) {
      mergedMessagesById.set(message.id, message);
    }
    for (const message of next.messages ?? []) {
      mergedMessagesById.set(message.id, message);
    }

    return {
      ...next,
      messages: Array.from(mergedMessagesById.values()).sort((a, b) => a.id - b.id),
    };
  }

  getCancelledJobTranscripts(threadId: number): CancelledTranscriptJob[] {
    return this.cancelledTranscriptJobsByThreadId.get(threadId) ?? [];
  }

  getThreadTimeline(thread: ChatThread): ChatTimelineItem[] {
    const items: ChatTimelineItem[] = [];

    for (const message of thread.messages ?? []) {
      items.push({
        kind: 'message',
        key: `message-${message.id}`,
        sortKey: this.timestampMs(message.created_at),
        message,
      });
    }

    for (const job of this.getCancelledJobTranscripts(thread.id)) {
      items.push({
        kind: 'cancelled-job',
        key: `cancelled-${job.jobId}`,
        sortKey: this.timestampMs(job.finishedAt ?? job.createdAt),
        job,
      });
    }

    return items.sort((a, b) => a.sortKey - b.sortKey);
  }

  trackByTimelineItem(index: number, item: ChatTimelineItem): string {
    return item.key;
  }

  getCancelledTimelineJob(item: ChatTimelineItem): CancelledTranscriptJob | null {
    return item.kind === 'cancelled-job' ? item.job : null;
  }

  async stopSelectedThreadJob(): Promise<void> {
    if (!this.selectedThread) {
      return;
    }

    const threadId = this.selectedThread.id;
    const jobId = this.pendingChatJobByThreadId.get(threadId);
    if (!jobId || this.stoppingThreadIds.has(threadId)) {
      return;
    }

    this.stoppingThreadIds.add(threadId);
    this.error = '';
    try {
      await this.api.cancelJob(jobId);
      await this.pollChatJob(jobId, threadId);
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.stoppingThreadIds.delete(threadId);
    }
  }

  isStoppingSelectedThreadJob(): boolean {
    return this.selectedThread !== null && this.stoppingThreadIds.has(this.selectedThread.id);
  }

  isTranscriptExpanded(threadId: number): boolean {
    return this.expandedTranscriptThreadIds.has(threadId);
  }

  isMessageTranscriptExpanded(messageId: number): boolean {
    return this.expandedTranscriptMessageIds.has(messageId);
  }

  isCancelledTranscriptExpanded(jobId: string): boolean {
    return this.expandedCancelledTranscriptJobIds.has(jobId);
  }

  toggleTranscript(threadId: number): void {
    if (this.expandedTranscriptThreadIds.has(threadId)) {
      this.expandedTranscriptThreadIds.delete(threadId);
    } else {
      this.expandedTranscriptThreadIds.add(threadId);
    }
  }

  toggleMessageTranscript(messageId: number): void {
    if (this.expandedTranscriptMessageIds.has(messageId)) {
      this.expandedTranscriptMessageIds.delete(messageId);
    } else {
      this.expandedTranscriptMessageIds.add(messageId);
    }
  }

  toggleCancelledTranscript(jobId: string): void {
    if (this.expandedCancelledTranscriptJobIds.has(jobId)) {
      this.expandedCancelledTranscriptJobIds.delete(jobId);
    } else {
      this.expandedCancelledTranscriptJobIds.add(jobId);
    }
  }

  isLastAssistantMessage(thread: ChatThread, message: ChatMessage): boolean {
    const messages = thread.messages ?? [];
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role !== 'user') {
        return messages[i].id === message.id;
      }
    }
    return false;
  }

  hasAssistantMessages(thread: ChatThread): boolean {
    return (thread.messages ?? []).some((message) => message.role !== 'user');
  }

  private findLatestAssistantMessageId(thread: ChatThread): number | null {
    const messages = thread.messages ?? [];
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role !== 'user') {
        return messages[i].id;
      }
    }
    return null;
  }

  private findAssistantMessageIdForJob(
    thread: ChatThread,
    job: JobInfo,
    usedAssistantMessageIds: Set<number>,
  ): number | null {
    const jobCreatedAt = this.timestampMs(job.created_at);
    const assistantMessages = (thread.messages ?? [])
      .filter((message) => message.role !== 'user' && !usedAssistantMessageIds.has(message.id))
      .sort((a, b) => this.timestampMs(a.created_at) - this.timestampMs(b.created_at));

    for (const message of assistantMessages) {
      if (this.timestampMs(message.created_at) >= jobCreatedAt) {
        return message.id;
      }
    }

    return assistantMessages.length > 0 ? assistantMessages[assistantMessages.length - 1].id : null;
  }

  private notifyPendingJobsChange(): void {
    this.pendingJobsChange.emit(this.pendingUserMessage !== null || this.pendingChatJobByThreadId.size > 0);
  }

  openAgentsDialog(): void {
    this.showAgentsDialog = true;
  }

  closeAgentsDialog(): void {
    this.showAgentsDialog = false;
  }

  openCreateAgentDialog(): void {
    this.showAgentEditDialog = true;
    this.editingAgentId = null;
    this.agentName = '';
    this.agentTitle = '';
    this.agentDescription = '';
    this.agentModel = '';
    this.agentPrompt = '';
  }

  openEditAgentDialog(agent: ChatAgent): void {
    this.showAgentEditDialog = true;
    this.editingAgentId = agent.id;
    this.agentName = agent.name;
    this.agentTitle = agent.title;
    this.agentDescription = agent.description ?? '';
    this.agentModel = agent.model ?? '';
    this.agentPrompt = agent.prompt;
  }

  closeAgentEditDialog(): void {
    this.showAgentEditDialog = false;
    this.editingAgentId = null;
  }

  openAgentPreview(agent: ChatAgent): void {
    this.previewAgent = agent;
    this.showAgentPreviewDialog = true;
  }

  closeAgentPreview(): void {
    this.previewAgent = null;
    this.showAgentPreviewDialog = false;
  }

  async saveAgent(): Promise<void> {
    const name = this.agentName.trim().toLowerCase();
    const prompt = this.agentPrompt.trim();
    if (!name || !prompt) {
      this.error = 'Agent name and prompt are required.';
      return;
    }

    this.loading = true;
    this.error = '';

    try {
      const payload = {
        name,
        title: this.agentTitle.trim() || name,
        description: this.agentDescription.trim() || undefined,
        model: this.agentModel.trim() || undefined,
        prompt,
      };
      if (this.editingAgentId === null) {
        await this.api.createChatAgent(payload);
      } else {
        await this.api.updateChatAgent(this.editingAgentId, payload);
      }

      this.closeAgentEditDialog();
      await this.loadAgents();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  async deleteAgent(agent: ChatAgent): Promise<void> {
    const shouldDelete = window.confirm(`Delete agent @${agent.name}?`);
    if (!shouldDelete) {
      return;
    }

    this.loading = true;
    this.error = '';

    try {
      await this.api.deleteChatAgent(agent.id);
      if (this.previewAgent?.id === agent.id) {
        this.closeAgentPreview();
      }
      await this.loadAgents();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  onResourceFocus(): void {
    this.resourcePickerOpen = true;
    this.activeResourceIndex = 0;
    if (this.resourceSearchText.trim()) {
      void this.searchResources();
    }
  }

  onResourceInput(value: string): void {
    this.resourceSearchText = value;
    this.resourcePickerOpen = true;
    this.activeResourceIndex = 0;

    if (this.resourceSearchTimer !== null) {
      window.clearTimeout(this.resourceSearchTimer);
    }

    this.resourceSearchTimer = window.setTimeout(() => {
      void this.searchResources();
    }, 200);
  }

  onResourceKeydown(event: KeyboardEvent): void {
    if (!this.resourcePickerOpen || this.filteredResourceResults.length === 0) {
      if (event.key === 'Escape') {
        event.preventDefault();
        this.resourcePickerOpen = false;
      }
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.activeResourceIndex = (this.activeResourceIndex + 1) % this.filteredResourceResults.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.activeResourceIndex = (this.activeResourceIndex - 1 + this.filteredResourceResults.length) % this.filteredResourceResults.length;
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const selected = this.filteredResourceResults[this.activeResourceIndex];
      if (selected) {
        this.onSelectResource(selected);
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.resourcePickerOpen = false;
    }
  }

  async searchResources(): Promise<void> {
    const { q, doc_type, app_id } = this.parseDocSearchInput(this.resourceSearchText);
    if (!q && !doc_type && !app_id) {
      this.resourceResults = [];
      return;
    }

    try {
      const response = await this.api.searchDocs({ q: q || undefined, doc_type, app_id });
      this.resourceResults = response.results;
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  onSelectResource(doc: StoredDoc): void {
    const resource = this.docToResourceRef(doc);
    const key = this.resourceKey(resource);
    if (!this.threadAttachedDocs.some((item) => this.resourceKey(item) === key)) {
      this.threadAttachedDocs = [...this.threadAttachedDocs, resource];
    }
    this.resourceSearchText = '';
    this.resourcePickerOpen = false;
    this.resourceResults = [];
  }

  removeThreadResource(resource: ProductResourceRef): void {
    const target = this.resourceKey(resource);
    this.threadAttachedDocs = this.threadAttachedDocs.filter((item) => this.resourceKey(item) !== target);
  }

  openResolvedDoc(doc: StoredDoc | undefined): void {
    if (!doc) {
      return;
    }
    this.previewDoc.emit(doc);
  }

  messageDocItems(message: ChatMessage): Array<{ resource: ProductResourceRef; doc?: StoredDoc }> {
    const docsByKey = new Map<string, StoredDoc>();
    for (const doc of message.doc_mentions_docs) {
      docsByKey.set(this.resourceKey(this.docToResourceRef(doc)), doc);
    }

    return message.doc_mentions.map((resource) => ({
      resource,
      doc: docsByKey.get(this.resourceKey(resource)),
    }));
  }

  attachedDocItems(thread: ChatThread): Array<{ resource: ProductResourceRef; doc?: StoredDoc }> {
    const docsByKey = new Map<string, StoredDoc>();
    for (const doc of thread.attached_docs_docs) {
      docsByKey.set(this.resourceKey(this.docToResourceRef(doc)), doc);
    }

    return thread.attached_docs.map((resource) => ({
      resource,
      doc: docsByKey.get(this.resourceKey(resource)),
    }));
  }

  get filteredResourceResults(): StoredDoc[] {
    const selected = new Set(this.threadAttachedDocs.map((resource) => this.resourceKey(resource)));
    return this.resourceResults.filter((doc) => !selected.has(this.resourceKey(this.docToResourceRef(doc))));
  }

  getDocTypeMeta(docType: string): { title: string; icon?: string } {
    for (const app of this.pluginApps) {
      const match = app.doc_types.find((doc) => doc.key === docType);
      if (match) {
        return {
          title: String((match as { title?: string }).title ?? docType),
          icon: (match as { icon?: string }).icon ?? app.icon,
        };
      }
    }

    return { title: docType };
  }

  docDisplayName(doc: StoredDoc): string {
    const name = doc.content['name'];
    if (typeof name === 'string' && name.trim()) {
      return name;
    }
    return this.getDocTypeMeta(doc.doc_type).title;
  }

  docToResourceRef(doc: StoredDoc): ProductResourceRef {
    const url = this.docUrl(doc);
    return {
      app_id: doc.app_id ?? '',
      doc_type: doc.doc_type,
      name: this.docDisplayName(doc),
      ...(url ? { url } : {}),
    };
  }

  docUrl(doc: StoredDoc): string | null {
    const value = doc.content['url'];
    if (typeof value !== 'string') {
      return null;
    }
    const trimmed = value.trim();
    return trimmed || null;
  }

  resourceKey(resource: ProductResourceRef): string {
    return [resource.app_id, resource.doc_type, resource.name, resource.url ?? '']
      .map((item) => item.toLowerCase())
      .join('|');
  }

  resourceIcon(resource: ProductResourceRef): string | undefined {
    return this.getDocTypeMeta(resource.doc_type).icon;
  }

  formatDate(value: string): string {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return `${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  }

  renderMessageMarkdown(value: string): string {
    const html = this.markdown.render(value ?? '');
    return html.replace(/<a\s/g, '<a target="_blank" rel="noopener noreferrer" ');
  }

  onAssistantMarkdownClick(event: MouseEvent): void {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const link = target.closest('a');
    if (!(link instanceof HTMLAnchorElement)) {
      return;
    }

    const href = link.getAttribute('href') ?? '';
    const resource = this.parseDopDocHref(href);
    if (!resource) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    void this.openDocByResourceRef(resource);
  }

  private parseDopDocHref(href: string): ProductResourceRef | null {
    if (!href.toLowerCase().startsWith('dopdoc://')) {
      return null;
    }

    let payload = href.slice('dopdoc://'.length);
    if (payload.startsWith('open?')) {
      payload = payload.slice('open?'.length);
    }
    if (payload.startsWith('?')) {
      payload = payload.slice(1);
    }

    const params = new URLSearchParams(payload);
    const appId = (params.get('app_id') ?? '').trim();
    const docType = (params.get('doc_type') ?? '').trim();
    const name = (params.get('name') ?? '').trim();
    const url = (params.get('url') ?? '').trim();

    if (!appId || !docType || !name) {
      return null;
    }

    return {
      app_id: appId,
      doc_type: docType,
      name,
      ...(url ? { url } : {}),
    };
  }

  private async openDocByResourceRef(resource: ProductResourceRef): Promise<void> {
    try {
      const response = await this.api.searchDocs({
        q: resource.name,
        doc_type: resource.doc_type,
        app_id: resource.app_id || undefined,
      });
      const docs = response.results;

      if (docs.length === 0) {
        return;
      }

      const normalizedName = resource.name.toLowerCase();
      const normalizedUrl = (resource.url ?? '').trim().toLowerCase();
      const exact = docs.find((doc) => {
        const sameName = this.docDisplayName(doc).toLowerCase() === normalizedName;
        const sameType = doc.doc_type === resource.doc_type;
        const sameApp = resource.app_id ? (doc.app_id ?? '') === resource.app_id : true;
        const sameUrl = normalizedUrl ? (this.docUrl(doc) ?? '').toLowerCase() === normalizedUrl : true;
        return sameName && sameType && sameApp && sameUrl;
      });

      this.openResolvedDoc(exact ?? docs[0]);
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  trackByThread(index: number, thread: ChatThread): number {
    return thread.id;
  }

  trackByMessage(index: number, message: ChatMessage): number {
    return message.id;
  }

  trackByMessageChunk(index: number): number {
    return index;
  }

  messageTextChunks(message: ChatMessage): Array<{ text: string; doc?: StoredDoc }> {
    const resolvedMentions = this.messageDocItems(message)
      .map((item) => ({
        token: `#${item.resource.name}`,
        doc: item.doc,
      }))
      .filter((item): item is { token: string; doc: StoredDoc } => !!item.doc && !!item.token.trim());

    if (resolvedMentions.length === 0) {
      return [{ text: message.text }];
    }

    const chunks: Array<{ text: string; doc?: StoredDoc }> = [];
    const source = message.text;
    const lowerSource = source.toLowerCase();
    let cursor = 0;

    while (cursor < source.length) {
      let nextPos = -1;
      let nextToken = '';
      let nextDoc: StoredDoc | undefined;

      for (const mention of resolvedMentions) {
        const pos = lowerSource.indexOf(mention.token.toLowerCase(), cursor);
        if (pos === -1) {
          continue;
        }
        if (nextPos === -1 || pos < nextPos) {
          nextPos = pos;
          nextToken = source.slice(pos, pos + mention.token.length);
          nextDoc = mention.doc;
        }
      }

      if (nextPos === -1 || !nextDoc) {
        chunks.push({ text: source.slice(cursor) });
        break;
      }

      if (nextPos > cursor) {
        chunks.push({ text: source.slice(cursor, nextPos) });
      }

      chunks.push({ text: nextToken, doc: nextDoc });
      cursor = nextPos + nextToken.length;
    }

    return chunks.length > 0 ? chunks : [{ text: source }];
  }

  hasRenderedDocChunk(message: ChatMessage): boolean {
    return this.messageTextChunks(message).some((chunk) => !!chunk.doc);
  }

  onMessageInput(): void {
    this.messageText = this.serializeMessageComposer();
    this.updateMessagePicker();
  }

  onMessageKeydown(event: KeyboardEvent): void {
    if (this.messagePickerOpen && this.currentMessageSuggestions.length > 0) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        this.activeMessagePickerIndex = (this.activeMessagePickerIndex + 1) % this.currentMessageSuggestions.length;
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        this.activeMessagePickerIndex =
          (this.activeMessagePickerIndex - 1 + this.currentMessageSuggestions.length) % this.currentMessageSuggestions.length;
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        this.closeMessagePicker();
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        const selected = this.currentMessageSuggestions[this.activeMessagePickerIndex];
        if (!selected) {
          return;
        }
        if (this.messagePickerMode === 'agent') {
          this.onSelectMessageAgent(selected as ChatAgent);
        } else {
          this.onSelectMessageDoc(selected as StoredDoc);
        }
        return;
      }
    }

    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void this.sendMessage();
      return;
    }

    if (event.key === 'Enter') {
      event.preventDefault();
      this.insertTextAtCursor('\n');
      window.setTimeout(() => {
        this.messageText = this.serializeMessageComposer();
        this.updateMessagePicker();
      }, 0);
      return;
    }

    if (event.key === 'Backspace' || event.key === 'Delete') {
      if (this.removeAdjacentMention(event.key === 'Backspace' ? 'backward' : 'forward')) {
        event.preventDefault();
        this.messageText = this.serializeMessageComposer();
        this.updateMessagePicker();
        return;
      }
    }

    window.setTimeout(() => this.updateMessagePicker(), 0);
  }

  onMessageBlur(): void {
    window.setTimeout(() => this.closeMessagePicker(), 120);
  }

  onMessageClick(event: MouseEvent): void {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const docMention = target.closest('.composer-inline-mention-doc');
    if (!(docMention instanceof HTMLElement)) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    void this.openEditorDocMention(docMention);
  }

  onSelectMessageAgent(agent: ChatAgent): void {
    this.insertInlineMention({
      token: `@${agent.name}`,
      label: `@${agent.name}`,
      kind: 'agent',
      agentName: agent.name,
    });
  }

  onSelectMessageDoc(doc: StoredDoc): void {
    const name = this.docDisplayName(doc);
    this.insertInlineMention({
      token: `#${name}`,
      label: `#${name}`,
      kind: 'doc',
      icon: this.getDocTypeMeta(doc.doc_type).icon,
      appId: doc.app_id ?? '',
      docType: doc.doc_type,
      url: this.docUrl(doc) ?? undefined,
    });
  }

  get currentMessageSuggestions(): Array<ChatAgent | StoredDoc> {
    return this.messagePickerMode === 'agent' ? this.messageAgentSuggestions : this.messageDocSuggestions;
  }

  private parseDocSearchInput(value: string): { q: string; doc_type?: string; app_id?: string } {
    const docTypeMatch = value.match(/#(\S+)/);
    const appMatch = value.match(/@(\S+)/);
    const q = value
      .replace(/#\S+/g, '')
      .replace(/@\S+/g, '')
      .replace(/\s+/g, ' ')
      .trim();

    return {
      q,
      doc_type: docTypeMatch ? docTypeMatch[1].trim() : undefined,
      app_id: appMatch ? appMatch[1].trim() : undefined,
    };
  }

  private asError(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  private async openEditorDocMention(mentionElement: HTMLElement): Promise<void> {
    const name = (mentionElement.dataset['name'] ?? '').trim();
    const docType = (mentionElement.dataset['docType'] ?? '').trim();
    const appId = (mentionElement.dataset['appId'] ?? '').trim();

    if (!name || !docType) {
      return;
    }

    try {
      const response = await this.api.searchDocs({
        q: name,
        doc_type: docType,
        app_id: appId || undefined,
      });
      const docs = response.results;

      const normalizedName = name.toLowerCase();
      const exact = docs.find((doc) => {
        const sameName = this.docDisplayName(doc).toLowerCase() === normalizedName;
        const sameType = doc.doc_type === docType;
        const sameApp = appId ? (doc.app_id ?? '') === appId : true;
        return sameName && sameType && sameApp;
      });

      this.openResolvedDoc(exact ?? docs[0]);
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  private getComponentScopeAttribute(element: HTMLElement): string | null {
    for (const attr of element.getAttributeNames()) {
      if (attr.startsWith('_ngcontent-')) {
        return attr;
      }
    }
    return null;
  }

  private readStoredSelectedThreadId(): number | null {
    if (typeof window === 'undefined') {
      return null;
    }

    const raw = window.localStorage.getItem(ChatComponent.SELECTED_THREAD_STORAGE_KEY);
    if (!raw) {
      return null;
    }

    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }

  private storeSelectedThreadId(threadId: number | null): void {
    if (typeof window === 'undefined') {
      return;
    }

    if (threadId === null) {
      window.localStorage.removeItem(ChatComponent.SELECTED_THREAD_STORAGE_KEY);
      return;
    }

    window.localStorage.setItem(ChatComponent.SELECTED_THREAD_STORAGE_KEY, String(threadId));
  }

  private updateMessagePicker(): void {
    const input = this.messageInput?.nativeElement;
    if (!input) {
      this.closeMessagePicker();
      return;
    }

    const trigger = this.extractMessageTrigger();
    if (!trigger) {
      this.closeMessagePicker();
      return;
    }

    this.messageTriggerRange = trigger.range;

    if (trigger.mode === 'agent') {
      const query = trigger.query.trim().toLowerCase();
      this.messageAgentSuggestions = this.agents
        .filter((agent) => {
          if (!query) {
            return true;
          }
          return agent.name.toLowerCase().includes(query) || agent.title.toLowerCase().includes(query);
        })
        .slice(0, 8);

      if (this.messageAgentSuggestions.length === 0) {
        this.closeMessagePicker();
        return;
      }

      this.messagePickerMode = 'agent';
      this.messagePickerOpen = true;
      this.activeMessagePickerIndex = 0;
      return;
    }

    const docQuery = trigger.query.replace(/\s+/g, ' ').trim();
    if (this.messageDocSearchTimer !== null) {
      window.clearTimeout(this.messageDocSearchTimer);
    }

    this.messagePickerMode = 'doc';
    this.messagePickerOpen = true;
    this.activeMessagePickerIndex = 0;

    this.messageDocSearchTimer = window.setTimeout(() => {
      void this.loadMessageDocSuggestions(docQuery);
    }, 180);
  }

  private async loadMessageDocSuggestions(query: string): Promise<void> {
    if (!this.messagePickerOpen || this.messagePickerMode !== 'doc') {
      return;
    }

    if (!query) {
      this.messageDocSuggestions = [];
      return;
    }

    try {
      const response = await this.api.searchDocs({ q: query });
      this.messageDocSuggestions = response.results.slice(0, 8);
      if (this.messageDocSuggestions.length === 0) {
        this.closeMessagePicker();
      }
    } catch (error) {
      this.error = this.asError(error);
      this.closeMessagePicker();
    }
  }

  private insertInlineMention(mention: {
    token: string;
    label: string;
    kind: 'agent' | 'doc';
    icon?: string;
    appId?: string;
    docType?: string;
    url?: string;
    agentName?: string;
  }): void {
    const editor = this.messageInput?.nativeElement;
    if (!editor) {
      return;
    }

    const selection = window.getSelection();
    if (!selection) {
      return;
    }

    const range = this.messageTriggerRange ?? (selection.rangeCount > 0 ? selection.getRangeAt(0).cloneRange() : null);
    if (!range) {
      return;
    }

    range.deleteContents();
    const scopeAttr = this.getComponentScopeAttribute(editor);

    const chip = document.createElement('span');
    chip.className = `composer-inline-mention composer-inline-mention-${mention.kind}`;
    chip.setAttribute('contenteditable', 'false');
    chip.setAttribute('data-token', mention.token);
    chip.setAttribute('data-kind', mention.kind);
    if (mention.kind === 'agent' && mention.agentName) {
      chip.setAttribute('data-agent-name', mention.agentName);
    }
    if (mention.kind === 'doc') {
      chip.setAttribute('data-app-id', mention.appId ?? '');
      chip.setAttribute('data-doc-type', mention.docType ?? '');
      chip.setAttribute('data-name', mention.label.replace(/^#/, ''));
      if (mention.url) {
        chip.setAttribute('data-url', mention.url);
      }
    }
    if (scopeAttr) {
      chip.setAttribute(scopeAttr, '');
    }

    if (mention.kind === 'doc') {
      if (mention.icon) {
        const icon = document.createElement('img');
        icon.className = 'composer-inline-mention-icon';
        icon.src = mention.icon;
        icon.alt = '';
        if (scopeAttr) {
          icon.setAttribute(scopeAttr, '');
        }
        chip.appendChild(icon);
      }

      const label = document.createElement('span');
      label.className = 'composer-inline-mention-label';
      label.textContent = mention.label;
      if (scopeAttr) {
        label.setAttribute(scopeAttr, '');
      }
      chip.appendChild(label);

      if (mention.appId || mention.docType) {
        const meta = document.createElement('span');
        meta.className = 'composer-inline-mention-meta';
        meta.textContent = `${mention.appId || '-'}:${mention.docType || '-'}`;
        if (scopeAttr) {
          meta.setAttribute(scopeAttr, '');
        }
        chip.appendChild(meta);
      }
    } else {
      const label = document.createElement('span');
      label.className = 'composer-inline-mention-label';
      label.textContent = mention.label;
      if (scopeAttr) {
        label.setAttribute(scopeAttr, '');
      }
      chip.appendChild(label);
    }

    const trailingSpace = document.createTextNode(' ');
    range.insertNode(trailingSpace);
    range.insertNode(chip);

    const caretRange = document.createRange();
    caretRange.setStart(trailingSpace, 1);
    caretRange.collapse(true);
    selection.removeAllRanges();
    selection.addRange(caretRange);

    editor.focus();
    this.messageText = this.serializeMessageComposer();
    this.closeMessagePicker();
  }

  private closeMessagePicker(): void {
    this.messagePickerOpen = false;
    this.messageAgentSuggestions = [];
    this.messageDocSuggestions = [];
    this.activeMessagePickerIndex = 0;
    this.messageTriggerRange = null;
    if (this.messageDocSearchTimer !== null) {
      window.clearTimeout(this.messageDocSearchTimer);
      this.messageDocSearchTimer = null;
    }
  }

  private extractMessageTrigger(): { mode: 'agent' | 'doc'; query: string; range: Range } | null {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
      return null;
    }

    const range = selection.getRangeAt(0);
    if (!range.collapsed || range.startContainer.nodeType !== Node.TEXT_NODE) {
      return null;
    }

    const textNode = range.startContainer as Text;
    const textBeforeCaret = textNode.data.slice(0, range.startOffset);
    const lastAt = textBeforeCaret.lastIndexOf('@');
    const lastHash = textBeforeCaret.lastIndexOf('#');
    const start = Math.max(lastAt, lastHash);
    if (start < 0) {
      return null;
    }

    const mode: 'agent' | 'doc' = start === lastAt ? 'agent' : 'doc';
    const before = start > 0 ? textBeforeCaret[start - 1] : ' ';
    if (!/\s/.test(before)) {
      return null;
    }

    const query = textBeforeCaret.slice(start + 1);
    if (mode === 'agent' && /\s/.test(query)) {
      return null;
    }

    const triggerRange = document.createRange();
    triggerRange.setStart(textNode, start);
    triggerRange.setEnd(textNode, range.startOffset);
    return { mode, query, range: triggerRange };
  }

  private insertTextAtCursor(text: string): void {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
      return;
    }

    const range = selection.getRangeAt(0);
    range.deleteContents();
    const node = document.createTextNode(text);
    range.insertNode(node);

    const nextRange = document.createRange();
    nextRange.setStart(node, node.length);
    nextRange.collapse(true);
    selection.removeAllRanges();
    selection.addRange(nextRange);
  }

  private removeAdjacentMention(direction: 'backward' | 'forward'): boolean {
    const editor = this.messageInput?.nativeElement;
    const selection = window.getSelection();
    if (!editor || !selection || selection.rangeCount === 0) {
      return false;
    }

    const range = selection.getRangeAt(0);
    if (!range.collapsed) {
      return false;
    }

    const container = range.startContainer;
    const offset = range.startOffset;

    if (container.nodeType === Node.TEXT_NODE) {
      const textNode = container as Text;
      if (direction === 'backward' && offset === 0) {
        const previous = textNode.previousSibling;
        if (this.isMentionNode(previous)) {
          previous.remove();
          return true;
        }
      }
      if (direction === 'forward' && offset === textNode.length) {
        const next = textNode.nextSibling;
        if (this.isMentionNode(next)) {
          next.remove();
          return true;
        }
      }
      return false;
    }

    if (container.nodeType === Node.ELEMENT_NODE) {
      const element = container as Element;
      const targetIndex = direction === 'backward' ? offset - 1 : offset;
      if (targetIndex < 0 || targetIndex >= element.childNodes.length) {
        return false;
      }
      const target = element.childNodes[targetIndex];
      if (this.isMentionNode(target)) {
        target.remove();
        return true;
      }
    }

    return false;
  }

  private isMentionNode(node: Node | null): node is HTMLSpanElement {
    return !!node && node.nodeType === Node.ELEMENT_NODE && (node as Element).classList.contains('composer-inline-mention');
  }

  private serializeMessageComposer(): string {
    const editor = this.messageInput?.nativeElement;
    if (!editor) {
      return this.messageText;
    }

    const serializeNode = (node: Node): string => {
      if (node.nodeType === Node.TEXT_NODE) {
        return (node as Text).data;
      }

      if (node.nodeType !== Node.ELEMENT_NODE) {
        return '';
      }

      const element = node as HTMLElement;
      if (element.classList.contains('composer-inline-mention')) {
        return element.dataset['token'] ?? '';
      }

      if (element.tagName === 'BR') {
        return '\n';
      }

      let combined = '';
      for (const child of Array.from(element.childNodes)) {
        combined += serializeNode(child);
      }
      return combined;
    };

    return serializeNode(editor);
  }

  private composeMessagePayload(): { text: string; agent_mentions: string[]; doc_mentions: ProductResourceRef[] } {
    const editor = this.messageInput?.nativeElement;
    if (!editor) {
      return { text: this.messageText, agent_mentions: [], doc_mentions: [] };
    }

    const agentMentions: string[] = [];
    const docMentions: ProductResourceRef[] = [];
    const seenAgents = new Set<string>();
    const seenDocs = new Set<string>();

    const serializeNode = (node: Node): string => {
      if (node.nodeType === Node.TEXT_NODE) {
        return (node as Text).data;
      }

      if (node.nodeType !== Node.ELEMENT_NODE) {
        return '';
      }

      const element = node as HTMLElement;
      if (element.classList.contains('composer-inline-mention')) {
        const token = element.dataset['token'] ?? '';
        const kind = element.dataset['kind'];
        if (kind === 'agent') {
          const name = (element.dataset['agentName'] ?? token.replace(/^@/, '')).trim().toLowerCase();
          if (name && !seenAgents.has(name)) {
            seenAgents.add(name);
            agentMentions.push(name);
          }
        }
        if (kind === 'doc') {
          const ref: ProductResourceRef = {
            app_id: (element.dataset['appId'] ?? '').trim(),
            doc_type: (element.dataset['docType'] ?? '').trim(),
            name: (element.dataset['name'] ?? token.replace(/^#/, '')).trim(),
            ...(element.dataset['url'] ? { url: element.dataset['url'] } : {}),
          };
          const key = this.resourceKey(ref);
          if (ref.doc_type && ref.name && !seenDocs.has(key)) {
            seenDocs.add(key);
            docMentions.push(ref);
          }
        }
        return token;
      }

      if (element.tagName === 'BR') {
        return '\n';
      }

      let combined = '';
      for (const child of Array.from(element.childNodes)) {
        combined += serializeNode(child);
      }
      return combined;
    };

    return {
      text: serializeNode(editor),
      agent_mentions: agentMentions,
      doc_mentions: docMentions,
    };
  }

  private scrollMessagesToBottom(): void {
    window.setTimeout(() => {
      const container = this.messagesContainer?.nativeElement;
      if (!container) {
        return;
      }
      container.scrollTop = container.scrollHeight;
    }, 0);
  }

  private clearMessageEditor(): void {
    const editor = this.messageInput?.nativeElement;
    this.messageText = '';
    if (!editor) {
      return;
    }
    editor.innerHTML = '';
  }
}
