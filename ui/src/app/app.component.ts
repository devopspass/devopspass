import { CommonModule } from '@angular/common';
import { Component, HostListener, OnDestroy, OnInit, ViewChild, ElementRef, NgZone } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import MarkdownIt from 'markdown-it';

import { ApiService, JobInfo, StoredDoc, PluginApp, AskPassRequest, ProductResourceRef } from './api.service';
import { AuthService } from './auth.service';
import { ChatComponent } from './chat/chat.component';

type Section = 'applications' | 'products' | 'docs' | 'chat';
type OnboardingStatus = 'not_started' | 'in_progress' | 'skipped' | 'completed';

interface OnboardingResourceSelection {
  git_repo: string;
  selected: boolean;
  linked_doc?: StoredDoc;
}

interface OnboardingProductSelection {
  id: string;
  name: string;
  selected: boolean;
  resources: OnboardingResourceSelection[];
}

interface OnboardingAnalysisPreviewItem {
  kind: 'activity' | 'stdout' | 'stderr';
  text: string;
  timestamp: string;
}

@Component({
  selector: 'dop-main',
  standalone: true,
  imports: [CommonModule, FormsModule, ChatComponent],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent implements OnInit, OnDestroy {
  @ViewChild('docsSearchInput') docsSearchInput?: ElementRef<HTMLInputElement>;
  @ViewChild(ChatComponent) chatComponent?: ChatComponent;
  activeSection: Section = 'applications';

  pluginApps: PluginApp[] = [];
  applications: StoredDoc[] = [];
  products: StoredDoc[] = [];
  docs: StoredDoc[] = [];
  displayedDocs: StoredDoc[] = [];

  appSearchText = '';
  productSearchText = '';
  docsSearchText = '';
  docsSearchType = '';
  docsSearchApp = '';
  docsSearchLoading = false;
  docsTypePickerOpen = false;
  docsTypeFieldOpen = false;
  docsAppFieldOpen = false;
  activeDocTypeIndex = 0;
  activeAppIndex = 0;

  docsCurrentPage = 0;
  docsPerPage = 25;
  docsLoadingMore = false;
  docsUseInfiniteScroll = true;
  docsTotalCount = 0;
  @ViewChild('docsGridContainer') docsGridContainer?: ElementRef<HTMLElement>;
  private docsScrollListener: (() => void) | null = null;
  private readonly docsInfiniteScrollStorageKey = 'dop.docs.useInfiniteScroll';

  showSettingsMenu = false;
  showJobsMenu = false;

  showAddDialog = false;
  showProductDialog = false;
  productDialogTab: 'general' | 'resources' = 'general';
  showPreviewDialog = false;
  showJobDialog = false;
  selectedPreviewDoc: StoredDoc | null = null;
  runningActionKey: string | null = null;
  editingApplicationDocId: number | null = null;
  editingProductDocId: number | null = null;
  selectedJob: JobInfo | null = null;

  showAskPassDialog = false;
  pendingAskPassRequests: AskPassRequest[] = [];
  currentAskPassRequest: AskPassRequest | null = null;
  askPassPassword = '';
  askPassSave = false;

  selectedPluginKey = '';
  addAppSearchText = '';
  addAppPickerOpen = false;
  activeAddAppIndex = 0;
  newAppId = '';
  editDescription = '';
  editUrl = '';
  addFormSettings: Record<string, unknown> = {};
  addAppTestStatus: 'idle' | 'running' | 'success' | 'failed' = 'idle';
  addAppTestMessage = '';

  onboardingStatus: OnboardingStatus = 'not_started';
  onboardingStep = 1;
  onboardingLoading = false;
  onboardingAiConfigured = false;
  onboardingGitConfigured = false;
  onboardingGitRefreshJobIds: string[] = [];
  onboardingThreadId: number | null = null;
  onboardingAnalysisJobId: string | null = null;
  onboardingAnalysisJobDetails: JobInfo | null = null;
  onboardingAnalysisPromptSent = false;
  onboardingProductsAutoLoading = false;
  private onboardingAnalysisRecoveryAttempted = false;
  private onboardingProductsAutoRetryDone = false;
  onboardingProducts: OnboardingProductSelection[] = [];
  onboardingCreatedProductIds = new Set<string>();
  onboardingWorkflowByProductId: Record<string, string> = {};
  onboardingResourceJobIdsByProductId: Record<string, string[]> = {};
  onboardingProductJobIds: string[] = [];
  onboardingEnvJobIds: string[] = [];

  newProductId = '';
  editProductName = '';
  editProductPrompt = '';
  editProductIcon = '';
  editProductUrl = '';
  editProductResources: ProductResourceRef[] = [];
  previewProductEnvironments: StoredDoc[] = [];
  previewProductEnvironmentsLoading = false;
  productResourceSearchText = '';
  productResourcePickerOpen = false;
  activeProductResourceIndex = 0;
  productResourceResults: StoredDoc[] = [];
  productResourceDocsIndex: Record<string, { app_id: string; doc_type: string; name: string; url?: string; hasFacts: boolean }> = {};

  jobs: JobInfo[] = [];
  notifications: Array<{
    id: string;
    status: 'success' | 'failed';
    icon: string | null;
    appId: string;
    title: string;
    summary: string;
    job: JobInfo;
  }> = [];

  loading = false;
  error = '';
  chatPendingUi = false;

  private readonly markdown = new MarkdownIt({
    html: false,
    linkify: true,
    typographer: false,
    breaks: true,
  });

  private docsSearchTimer: number | null = null;
  private docsSearchRequestToken = 0;
  private productResourceSearchTimer: number | null = null;
  private jobsPollTimer: number | null = null;
  private askPassPollTimer: number | null = null;
  private previewProductEnvironmentsQueryKey: string | null = null;
  private readonly notifiedCompletedJobs = new Set<string>();
  private jobsSignature = '';
  private selectedJobSignature: string | null = null;
  private readonly jobsPollFastMs = 1000;
  private readonly jobsPollIdleMs = 10000;
  private readonly jobsLastSeenStorageKey = 'dop.jobs.lastSeenCompletedAt';
  private lastSeenCompletedJobAt = 0;
  private readonly stoppingJobIds = new Set<string>();

  constructor(
    private readonly api: ApiService,
    private readonly zone: NgZone,
    private readonly auth: AuthService,
    private readonly router: Router,
  ) {}

  async ngOnInit(): Promise<void> {
    this.lastSeenCompletedJobAt = this.loadLastSeenCompletedJobAt();
    this.loadDocsPreferences();
    await this.refreshApplications();
    await this.refreshProducts();
    await this.initOnboardingState();
    await this.pollJobs();
    this.scheduleJobsPoll(this.jobsPollIdleMs);
    // Load initial docs page
    await this.searchDocs();
  }

  ngOnDestroy(): void {
    if (this.jobsPollTimer !== null) {
      window.clearTimeout(this.jobsPollTimer);
      this.jobsPollTimer = null;
    }
    if (this.askPassPollTimer !== null) {
      window.clearTimeout(this.askPassPollTimer);
      this.askPassPollTimer = null;
    }
    this.removeDocsScrollListener();
  }

  async refreshApplications(): Promise<void> {
    this.loading = true;
    this.error = '';

    try {
      this.pluginApps = await this.api.listPluginApps();
      this.applications = await this.api.listApplications();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  async refreshProducts(): Promise<void> {
    this.loading = true;
    this.error = '';

    try {
      this.products = await this.api.listProducts();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  private async initOnboardingState(): Promise<void> {
    const status = this.getOnboardingStatusFromApp();
    this.onboardingStatus = status;
    this.onboardingGitRefreshJobIds = [];
    if (status === 'completed') {
      return;
    }

    const hasAi = this.applications.some((app) => this.pluginByKey(String(app.content['plugin_key'] ?? ''))?.category === 'ai-agents');
    const hasGit = this.applications.some((app) => this.pluginByKey(String(app.content['plugin_key'] ?? ''))?.category === 'git');
    this.onboardingAiConfigured = hasAi;
    this.onboardingGitConfigured = hasGit;

    if (status === 'not_started') {
      this.onboardingStatus = 'in_progress';
      await this.persistOnboardingStatus('in_progress');
    }

    if (hasAi && hasGit) {
      // If products already exist, user has already passed stage 3.
      if (this.products.length > 0) {
        this.onboardingStep = 4;
        return;
      }

      this.onboardingStep = 3;
      return;
    }

    this.onboardingStep = hasAi ? 2 : 1;
  }

  private pluginByKey(pluginKey: string): PluginApp | undefined {
    return this.pluginApps.find((item) => item.plugin_key === pluginKey);
  }

  private builtinAppDoc(): StoredDoc | undefined {
    return this.applications.find((app) => app.app_id === 'devops-pass-ai' && app.doc_type === 'dop_app');
  }

  private getOnboardingStatusFromApp(): OnboardingStatus {
    const builtin = this.builtinAppDoc();
    if (!builtin) {
      return 'not_started';
    }
    const settings = (builtin.content['settings'] as Record<string, unknown> | undefined) ?? {};
    const value = String(settings['onboarding.status'] ?? 'not_started').trim().toLowerCase();
    if (value === 'in_progress' || value === 'skipped' || value === 'completed') {
      return value;
    }
    return 'not_started';
  }

  private async persistOnboardingStatus(status: OnboardingStatus): Promise<void> {
    const builtin = this.builtinAppDoc();
    if (!builtin) {
      return;
    }
    const settings = { ...((builtin.content['settings'] as Record<string, unknown> | undefined) ?? {}) };
    settings['onboarding.status'] = status;
    settings['onboarding.updated_at'] = new Date().toISOString();
    await this.api.updateApplication(builtin.id, { content: { settings } });
    const refreshed = await this.api.getApplication(builtin.id);
    const index = this.applications.findIndex((app) => app.id === builtin.id);
    if (index >= 0) {
      this.applications = [
        ...this.applications.slice(0, index),
        refreshed,
        ...this.applications.slice(index + 1),
      ];
    }
    this.onboardingStatus = status;
  }

  get showOnboarding(): boolean {
    return this.onboardingStatus === 'not_started' || this.onboardingStatus === 'in_progress';
  }

  get onboardingAiApps(): PluginApp[] {
    return this.pluginApps.filter((app) => app.category === 'ai-agents');
  }

  get onboardingGitApps(): PluginApp[] {
    return this.pluginApps.filter((app) => app.category === 'git');
  }

  async skipOnboarding(): Promise<void> {
    this.error = '';
    try {
      await this.persistOnboardingStatus('skipped');
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  async resumeOnboarding(): Promise<void> {
    this.error = '';
    try {
      await this.persistOnboardingStatus('in_progress');
      await this.initOnboardingState();
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  openAddDialog(): void {
    this.showAddDialog = true;
    this.editingApplicationDocId = null;
    this.selectedPluginKey = '';
    this.addAppSearchText = '';
    this.addAppPickerOpen = false;
    this.activeAddAppIndex = 0;
    this.newAppId = '';
    this.editDescription = '';
    this.editUrl = '';
    this.addFormSettings = {};
    this.addAppTestStatus = 'idle';
    this.addAppTestMessage = '';
  }

  openAddProductDialog(): void {
    this.showProductDialog = true;
    this.editingProductDocId = null;
    this.productDialogTab = 'general';
    this.newProductId = '';
    this.editProductName = '';
    this.editProductPrompt = '';
    this.editProductIcon = '';
    this.editProductUrl = '';
    this.editProductResources = [];
    this.productResourceSearchText = '';
    this.productResourcePickerOpen = false;
    this.activeProductResourceIndex = 0;
    this.productResourceResults = [];
    this.productResourceDocsIndex = {};
  }

  closeAddDialog(): void {
    this.showAddDialog = false;
    this.editingApplicationDocId = null;
    this.addAppPickerOpen = false;
    this.addAppTestStatus = 'idle';
    this.addAppTestMessage = '';
  }

  closeProductDialog(): void {
    this.showProductDialog = false;
    this.editingProductDocId = null;
    this.productResourcePickerOpen = false;
    this.productDialogTab = 'general';
  }

  async openEditDialog(doc: StoredDoc): Promise<void> {
    this.loading = true;
    this.error = '';

    try {
      const application = await this.api.getApplication(doc.id);
      const pluginKey = String(application.content['plugin_key'] ?? '');

      this.showAddDialog = true;
      this.editingApplicationDocId = application.id;
      this.selectedPluginKey = pluginKey;
      this.addAppSearchText = this.selectedPlugin?.name ?? '';
      this.addAppPickerOpen = false;
      this.newAppId = application.app_id ?? '';
      this.editDescription = String(application.content['description'] ?? '');
      this.editUrl = String(application.content['url'] ?? '');
      this.addFormSettings = { ...(application.content['settings'] as Record<string, unknown> ?? {}) };
      this.addAppTestStatus = 'success';
      this.addAppTestMessage = '';
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  async openEditProductDialog(doc: StoredDoc): Promise<void> {
    this.loading = true;
    this.error = '';

    try {
      const product = await this.api.getProduct(doc.id);
      this.showProductDialog = true;
      this.productDialogTab = 'general';
      this.editingProductDocId = product.id;
      this.newProductId = product.app_id ?? '';
      this.editProductName = String(product.content['name'] ?? '');
      this.editProductPrompt = String(product.content['prompt'] ?? product.content['description'] ?? '');
      this.editProductIcon = String(product.content['icon'] ?? '');
      this.editProductUrl = String(product.content['url'] ?? '');
      this.editProductResources = this.asProductResources(product.content['resources']);
      this.productResourceSearchText = '';
      this.productResourcePickerOpen = false;
      this.activeProductResourceIndex = 0;
      this.productResourceResults = [];
      this.productResourceDocsIndex = {};
      this.indexResourceDocs(product.content['resources_docs']);
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  onSelectPlugin(): void {
    this.addFormSettings = {};
    this.addAppTestStatus = 'idle';
    this.addAppTestMessage = '';
    const plugin = this.selectedPlugin;
    if (!plugin) {
      this.newAppId = '';
      return;
    }

    if (!this.isEditingApplication) {
      this.newAppId = plugin.uniq ? this.fixedAppIdForPlugin(plugin) : '';
    }

    Object.entries(plugin.settings).forEach(([key, setting]) => {
      this.addFormSettings[key] = setting.default ?? (setting.type === 'boolean' ? false : '');
    });
  }

  onAddAppFieldFocus(): void {
    this.addAppPickerOpen = true;
    this.activeAddAppIndex = 0;
  }

  onAddAppFieldInput(value: string): void {
    this.addAppSearchText = value;
    this.addAppPickerOpen = true;
    this.activeAddAppIndex = 0;
    this.selectedPluginKey = '';
    this.newAppId = '';
    this.addFormSettings = {};
  }

  onAddAppFieldKeydown(event: KeyboardEvent): void {
    if (!this.addAppPickerOpen || this.filteredPluginApps.length === 0) {
      if (event.key === 'Escape') {
        event.preventDefault();
        this.addAppPickerOpen = false;
      }
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.activeAddAppIndex = (this.activeAddAppIndex + 1) % this.filteredPluginApps.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.activeAddAppIndex = (this.activeAddAppIndex - 1 + this.filteredPluginApps.length) % this.filteredPluginApps.length;
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      const selected = this.filteredPluginApps[this.activeAddAppIndex];
      if (selected) {
        this.onSelectAddApp(selected.plugin_key);
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.addAppPickerOpen = false;
    }
  }

  onSelectAddApp(pluginKey: string): void {
    this.selectedPluginKey = pluginKey;
    this.addAppPickerOpen = false;
    this.activeAddAppIndex = 0;
    this.addAppSearchText = this.selectedPlugin?.name ?? '';
    this.onSelectPlugin();
  }

  get selectedPluginRequiresTest(): boolean {
    const plugin = this.selectedPlugin;
    return !!plugin?.check_script && !this.isEditingApplication;
  }

  get canSaveApplication(): boolean {
    if (this.loading) {
      return false;
    }
    if (!this.selectedPluginRequiresTest) {
      return true;
    }
    return this.addAppTestStatus === 'success';
  }

  async testApplicationConfig(): Promise<void> {
    const plugin = this.selectedPlugin;
    const appId = this.resolvedNewAppId();
    if (!plugin) {
      this.error = 'Select an application first.';
      return;
    }
    if (!plugin.check_script) {
      this.addAppTestStatus = 'success';
      this.addAppTestMessage = 'No test required.';
      return;
    }

    this.error = '';
    this.addAppTestStatus = 'running';
    this.addAppTestMessage = '';
    try {
      const result = await this.api.testApplication({
        plugin_key: plugin.plugin_key,
        app_id: appId || undefined,
        settings: this.addFormSettings,
      });
      this.addAppTestStatus = result.status;
      this.addAppTestMessage = result.message;
    } catch (error) {
      this.addAppTestStatus = 'failed';
      this.addAppTestMessage = this.asError(error);
    }
  }

  async addApplication(): Promise<void> {
    const appId = this.resolvedNewAppId();
    if (!this.selectedPlugin || !appId) {
      this.error = 'Application ID and plugin are required.';
      return;
    }

    for (const [key, setting] of Object.entries(this.selectedPlugin.settings)) {
      if (setting.mandatory && !this.addFormSettings[key]) {
        this.error = `Mandatory setting is required: ${setting.title}`;
        return;
      }
    }

    this.loading = true;
    this.error = '';

    try {
      if (this.editingApplicationDocId === null) {
        if (this.selectedPluginRequiresTest && this.addAppTestStatus !== 'success') {
          this.error = 'Please run Test and ensure it passes before adding this application.';
          return;
        }

        const created = await this.api.addApplication({
          plugin_key: this.selectedPluginKey,
          app_id: appId,
          settings: this.addFormSettings,
        });

        if (this.onboardingStatus !== 'completed') {
          const pluginCategory = this.selectedPlugin?.category;
          if (pluginCategory === 'ai-agents') {
            this.onboardingAiConfigured = true;
            this.onboardingStep = 2;
          }

          if (pluginCategory === 'git') {
            this.onboardingGitConfigured = true;
            await this.triggerInitialGitSync(created, this.selectedPlugin as PluginApp);
            this.onboardingStep = 2;
          }
        }
      } else {
        await this.api.updateApplication(this.editingApplicationDocId, {
          content: {
            settings: this.addFormSettings,
            description: this.editDescription,
            url: this.editUrl,
          },
        });
      }
      this.closeAddDialog();
      await this.refreshApplications();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  async saveProduct(): Promise<void> {
    const productId = this.newProductId.trim();
    const name = this.editProductName.trim();
    if (!productId || !name) {
      this.error = 'Product ID and name are required.';
      return;
    }

    const prompt = this.editProductPrompt.trim();
    const resources = [...this.editProductResources];

    this.loading = true;
    this.error = '';

    try {
      if (this.editingProductDocId === null) {
        await this.api.addProduct({
          product_id: productId,
          name,
          prompt,
          description: prompt,
          icon: this.editProductIcon.trim() || undefined,
          url: this.editProductUrl.trim() || undefined,
          resources,
        });
      } else {
        await this.api.updateProduct(this.editingProductDocId, {
          name,
          prompt,
          description: prompt,
          icon: this.editProductIcon.trim() || undefined,
          url: this.editProductUrl.trim() || undefined,
          resources,
        });
      }

      this.closeProductDialog();
      await this.refreshProducts();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  async deleteApplication(doc: StoredDoc): Promise<void> {
    const shouldDelete = window.confirm(`Delete application ${doc.app_id ?? doc.id}?`);
    if (!shouldDelete) {
      return;
    }

    this.loading = true;
    this.error = '';
    try {
      await this.api.deleteApplication(doc.id);
      await this.refreshApplications();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  async deleteProduct(doc: StoredDoc): Promise<void> {
    const shouldDelete = window.confirm(`Delete product ${doc.app_id ?? doc.id}?`);
    if (!shouldDelete) {
      return;
    }

    this.loading = true;
    this.error = '';
    try {
      await this.api.deleteProduct(doc.id);
      await this.refreshProducts();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  onProductResourceFocus(): void {
    this.productResourcePickerOpen = true;
    this.activeProductResourceIndex = 0;
    if (this.productResourceSearchText.trim()) {
      void this.searchProductResources();
    }
  }

  onProductResourceInput(value: string): void {
    this.productResourceSearchText = value;
    this.productResourcePickerOpen = true;
    this.activeProductResourceIndex = 0;

    if (this.productResourceSearchTimer !== null) {
      window.clearTimeout(this.productResourceSearchTimer);
    }

    this.productResourceSearchTimer = window.setTimeout(() => {
      void this.searchProductResources();
    }, 200);
  }

  onProductResourceKeydown(event: KeyboardEvent): void {
    if (!this.productResourcePickerOpen || this.filteredProductResourceResults.length === 0) {
      if (event.key === 'Escape') {
        event.preventDefault();
        this.productResourcePickerOpen = false;
      }
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.activeProductResourceIndex = (this.activeProductResourceIndex + 1) % this.filteredProductResourceResults.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.activeProductResourceIndex =
        (this.activeProductResourceIndex - 1 + this.filteredProductResourceResults.length) % this.filteredProductResourceResults.length;
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const selected = this.filteredProductResourceResults[this.activeProductResourceIndex];
      if (selected) {
        this.onSelectProductResource(selected);
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.productResourcePickerOpen = false;
    }
  }

  async searchProductResources(): Promise<void> {
    const { q, doc_type, app_id } = this.parseDocSearchInput(this.productResourceSearchText);
    if (!q && !doc_type && !app_id) {
      this.productResourceResults = [];
      return;
    }

    try {
      const response = await this.api.searchDocs({
        q: q || undefined,
        doc_type,
        app_id,
      });
      this.productResourceResults = response.results;
      for (const doc of response.results) {
        this.cacheResourceRef(doc);
      }
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  onSelectProductResource(doc: StoredDoc): void {
    const resource = this.docToResourceRef(doc);
    const key = this.resourceKey(resource);
    if (!this.editProductResources.some((item) => this.resourceKey(item) === key)) {
      this.editProductResources = [...this.editProductResources, resource];
    }
    this.cacheResourceRef(doc);
    this.productResourceSearchText = '';
    this.productResourcePickerOpen = false;
    this.productResourceResults = [];
  }

  removeProductResource(resource: ProductResourceRef): void {
    const target = this.resourceKey(resource);
    this.editProductResources = this.editProductResources.filter((item) => this.resourceKey(item) !== target);
  }

  async openProductResource(resource: ProductResourceRef): Promise<void> {
    this.error = '';
    try {
      const response = await this.api.searchDocs({
        q: resource.name,
        doc_type: resource.doc_type,
        app_id: resource.app_id,
      });
      const docs = response.results;

      const resourceName = resource.name.trim().toLowerCase();
      const resourceUrl = (resource.url ?? '').trim().toLowerCase();
      const matchedDoc = docs.find((doc) => {
        const docName = this.docDisplayName(doc).trim().toLowerCase();
        const docUrl = (this.docUrl(doc) ?? '').trim().toLowerCase();
        if (resourceUrl && docUrl) {
          return docUrl === resourceUrl;
        }
        return docName === resourceName;
      });

      if (matchedDoc) {
        this.cacheResourceRef(matchedDoc);
        this.openDocPreview(matchedDoc);
      }
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  viewApplication(doc: StoredDoc): void {
    this.openDocPreview(doc);
  }

  async searchDocs(): Promise<void> {
    const requestToken = ++this.docsSearchRequestToken;
    this.docsSearchLoading = true;
    this.error = '';
    this.docsCurrentPage = 0;
    this.displayedDocs = [];
    this.docsTotalCount = 0;

    try {
      const response = await this.api.searchDocs({
        q: this.docsSearchText.trim() || undefined,
        doc_type: this.docsSearchType.trim() || undefined,
        app_id: this.docsSearchApp.trim() || undefined,
        offset: 0,
        limit: this.docsPerPage,
      });
      if (requestToken === this.docsSearchRequestToken) {
        this.displayedDocs = response.results;
        this.docsTotalCount = response.total;
        this.docsCurrentPage = 1;
        this.setupDocsScrollListener();
      }
    } catch (error) {
      if (requestToken === this.docsSearchRequestToken) {
        this.error = this.asError(error);
      }
    } finally {
      if (requestToken === this.docsSearchRequestToken) {
        this.docsSearchLoading = false;
      }
    }
  }

  private async loadMoreDocs(): Promise<void> {
    const offset = this.docsCurrentPage * this.docsPerPage;
    if (offset >= this.docsTotalCount) {
      return;
    }

    try {
      const response = await this.api.searchDocs({
        q: this.docsSearchText.trim() || undefined,
        doc_type: this.docsSearchType.trim() || undefined,
        app_id: this.docsSearchApp.trim() || undefined,
        offset,
        limit: this.docsPerPage,
      });
      this.displayedDocs = [...this.displayedDocs, ...response.results];
      this.docsCurrentPage++;
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  private setupDocsScrollListener(): void {
    this.removeDocsScrollListener();

    if (this.docsTotalCount <= this.docsPerPage) {
      return;
    }

    this.zone.runOutsideAngular(() => {
      setTimeout(() => {
        const container = this.docsGridContainer?.nativeElement;
        if (!container) return;

        this.docsScrollListener = () => {
          const { scrollTop, scrollHeight, clientHeight } = container;
          const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);

          if (distanceFromBottom < 200 && !this.docsLoadingMore && this.docsCurrentPage * this.docsPerPage < this.docsTotalCount) {
            if (this.docsUseInfiniteScroll) {
              this.zone.run(() => this.loadMoreDocsWithScroll());
            }
          }
        };

        container.addEventListener('scroll', this.docsScrollListener);
      }, 0);
    });
  }

  private removeDocsScrollListener(): void {
    if (this.docsScrollListener) {
      const container = this.docsGridContainer?.nativeElement;
      if (container) {
        container.removeEventListener('scroll', this.docsScrollListener);
      }
      this.docsScrollListener = null;
    }
  }

  private loadMoreDocsWithScroll(): void {
    if (!this.docsUseInfiniteScroll || this.docsLoadingMore) return;

    this.docsLoadingMore = true;
    this.loadMoreDocs().finally(() => {
      this.docsLoadingMore = false;
    });
  }

  private loadDocsPreferences(): void {
    const stored = localStorage.getItem(this.docsInfiniteScrollStorageKey);
    if (stored !== null) {
      this.docsUseInfiniteScroll = stored === 'true';
    }
  }

  toggleDocsScrollMode(): void {
    this.docsUseInfiniteScroll = !this.docsUseInfiniteScroll;
    localStorage.setItem(this.docsInfiniteScrollStorageKey, String(this.docsUseInfiniteScroll));
    if (this.docsUseInfiniteScroll) {
      this.setupDocsScrollListener();
    } else {
      this.removeDocsScrollListener();
    }
  }

  async loadMoreDocsManual(): Promise<void> {
    if (this.docsCurrentPage * this.docsPerPage < this.docsTotalCount) {
      this.docsLoadingMore = true;
      try {
        await this.loadMoreDocs();
      } finally {
        this.docsLoadingMore = false;
      }
    }
  }

  onDocsSearchInput(value: string): void {
    this.docsSearchText = value;
    this.docsTypePickerOpen = value.includes('#');
    if (this.docsTypePickerOpen) {
      this.activeDocTypeIndex = 0;
    }

    if (this.docsSearchTimer !== null) {
      window.clearTimeout(this.docsSearchTimer);
    }

    this.docsSearchTimer = window.setTimeout(() => {
      this.searchDocs();
    }, 200);
  }

  onSelectDocType(docType: string): void {
    this.docsSearchType = docType;
    this.docsSearchText = this.docsSearchText.replace(/#\S*/g, '').trim();
    this.docsTypePickerOpen = false;
    this.docsTypeFieldOpen = false;
    this.searchDocs();

    setTimeout(() => this.docsSearchInput?.nativeElement.focus(), 0);
  }

  onDocsSearchKeydown(event: KeyboardEvent): void {
    if (!this.docsTypePickerOpen || this.filteredDocTypes.length === 0) {
      if (event.key === 'Escape' && (this.docsSearchType || this.docsSearchApp)) {
        event.preventDefault();
        this.docsSearchType = '';
        this.docsSearchApp = '';
        this.docsTypePickerOpen = false;
        this.docsTypeFieldOpen = false;
        this.docsAppFieldOpen = false;
        this.searchDocs();
      }
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.activeDocTypeIndex = (this.activeDocTypeIndex + 1) % this.filteredDocTypes.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.activeDocTypeIndex =
        (this.activeDocTypeIndex - 1 + this.filteredDocTypes.length) % this.filteredDocTypes.length;
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      const selected = this.filteredDocTypes[this.activeDocTypeIndex];
      if (selected) {
        this.onSelectDocType(selected.key);
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.docsTypePickerOpen = false;
      this.docsSearchType = '';
      this.docsSearchApp = '';
      this.docsTypeFieldOpen = false;
      this.docsAppFieldOpen = false;
      this.searchDocs();
    }
  }

  onDocTypeFieldInput(value: string): void {
    this.docsSearchType = value;
    this.docsTypeFieldOpen = true;
    this.activeDocTypeIndex = 0;
  }

  onDocTypeFieldFocus(): void {
    this.docsTypeFieldOpen = true;
    this.activeDocTypeIndex = 0;
  }

  onDocTypeFieldKeydown(event: KeyboardEvent): void {
    if (!this.docsTypeFieldOpen || this.filteredDocTypesForField.length === 0) {
      if (event.key === 'Escape' && this.docsSearchType) {
        event.preventDefault();
        this.docsSearchType = '';
        this.docsTypeFieldOpen = false;
      }
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.activeDocTypeIndex = (this.activeDocTypeIndex + 1) % this.filteredDocTypesForField.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.activeDocTypeIndex =
        (this.activeDocTypeIndex - 1 + this.filteredDocTypesForField.length) % this.filteredDocTypesForField.length;
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      const selected = this.filteredDocTypesForField[this.activeDocTypeIndex];
      if (selected) {
        this.onSelectDocType(selected.key);
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.docsTypeFieldOpen = false;
      this.docsSearchType = '';
    }
  }

  onSelectDocApp(appId: string): void {
    this.docsSearchApp = appId;
    this.docsAppFieldOpen = false;
    this.searchDocs();

    setTimeout(() => this.docsSearchInput?.nativeElement.focus(), 0);
  }

  onDocAppFieldInput(value: string): void {
    this.docsSearchApp = value;
    this.docsAppFieldOpen = true;
    this.activeAppIndex = 0;
  }

  onDocAppFieldFocus(): void {
    this.docsAppFieldOpen = true;
    this.activeAppIndex = 0;
  }

  onDocAppFieldKeydown(event: KeyboardEvent): void {
    if (!this.docsAppFieldOpen || this.filteredAppsForField.length === 0) {
      if (event.key === 'Escape' && this.docsSearchApp) {
        event.preventDefault();
        this.docsSearchApp = '';
        this.docsAppFieldOpen = false;
      }
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.activeAppIndex = (this.activeAppIndex + 1) % this.filteredAppsForField.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.activeAppIndex =
        (this.activeAppIndex - 1 + this.filteredAppsForField.length) % this.filteredAppsForField.length;
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      const selected = this.filteredAppsForField[this.activeAppIndex];
      if (selected) {
        this.onSelectDocApp(selected.appId);
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.docsAppFieldOpen = false;
      this.docsSearchApp = '';
    }
  }

  get docTypeQuery(): string {
    const match = this.docsSearchText.match(/#(\S*)/);
    return match ? match[1].toLowerCase() : '';
  }

  get allDocTypes(): Array<{ key: string; title: string; icon?: string }> {
    const items = this.pluginApps.flatMap((app) =>
      app.doc_types.map((doc) => ({
        key: doc.key,
        title: String((doc as { title?: string }).title ?? doc.key),
        icon: (doc as { icon?: string }).icon ?? app.icon,
      }))
    );

    const byKey = new Map<string, { key: string; title: string; icon?: string }>();
    for (const item of items) {
      if (!byKey.has(item.key)) {
        byKey.set(item.key, item);
      } else {
        const existing = byKey.get(item.key);
        if (existing && !existing.icon && item.icon) {
          byKey.set(item.key, item);
        }
      }
    }

    return Array.from(byKey.values()).sort((a, b) => a.key.localeCompare(b.key));
  }

  get filteredDocTypes(): Array<{ key: string; title: string; icon?: string }> {
    const query = this.docTypeQuery;
    if (!query) {
      return this.allDocTypes;
    }
    return this.allDocTypes.filter((docType) =>
      [docType.key, docType.title].some((value) => value.toLowerCase().includes(query))
    );
  }

  get filteredDocTypesForField(): Array<{ key: string; title: string; icon?: string }> {
    const query = this.docsSearchType.trim().toLowerCase();
    if (!query) {
      return this.allDocTypes;
    }
    return this.allDocTypes.filter((docType) =>
      [docType.key, docType.title].some((value) => value.toLowerCase().includes(query))
    );
  }

  get selectedDocTypeForField(): { key: string; title: string; icon?: string } | undefined {
    const key = this.docsSearchType.trim().toLowerCase();
    if (!key) {
      return undefined;
    }

    return this.allDocTypes.find((docType) => docType.key.toLowerCase() === key);
  }

  get allAppsForField(): Array<{ appId: string; title: string; icon?: string }> {
    const byAppId = new Map<string, { appId: string; title: string; icon?: string }>();

    for (const app of this.applications) {
      const appId = (app.app_id ?? '').trim();
      if (!appId || byAppId.has(appId)) {
        continue;
      }

      const name = app.content['name'];
      const icon = app.content['icon'];
      byAppId.set(appId, {
        appId,
        title: typeof name === 'string' && name.trim() ? name : appId,
        icon: typeof icon === 'string' && icon.trim() ? icon : undefined,
      });
    }

    return Array.from(byAppId.values()).sort((a, b) => a.appId.localeCompare(b.appId));
  }

  get filteredAppsForField(): Array<{ appId: string; title: string; icon?: string }> {
    const query = this.docsSearchApp.trim().toLowerCase();
    if (!query) {
      return this.allAppsForField;
    }

    return this.allAppsForField.filter((app) =>
      [app.appId, app.title].some((value) => value.toLowerCase().includes(query))
    );
  }

  get selectedAppForField(): { appId: string; title: string; icon?: string } | undefined {
    const appId = this.docsSearchApp.trim().toLowerCase();
    if (!appId) {
      return undefined;
    }

    return this.allAppsForField.find((app) => app.appId.toLowerCase() === appId);
  }

  openDocPreview(doc: StoredDoc): void {
    this.selectedPreviewDoc = doc;
    this.showPreviewDialog = true;
    this.previewProductEnvironments = [];
    this.previewProductEnvironmentsLoading = false;

    if (doc.doc_type === 'dop_product') {
      this.loadPreviewProductEnvironments(doc.id, doc.app_id);
      // For products, hydrate from both endpoints and keep product structure with guaranteed fact value.
      void Promise.all([this.api.getProduct(doc.id), this.api.getDoc(doc.id)])
        .then(([fullProduct, fullDoc]) => {
          if (this.selectedPreviewDoc?.id === fullProduct.id && this.selectedPreviewDoc?.doc_type === fullProduct.doc_type) {
            this.selectedPreviewDoc = {
              ...fullProduct,
              fact: fullDoc.fact ?? fullProduct.fact,
            };
            this.loadPreviewProductEnvironments(fullProduct.id, fullProduct.app_id);
          }
        })
        .catch(() => { /* keep currently shown doc if hydration fails */ });
      return;
    }

    // Hydrate full doc content/facts by id.
    void this.api.getDoc(doc.id).then((full) => {
      if (this.selectedPreviewDoc?.id === full.id && this.selectedPreviewDoc?.doc_type === full.doc_type) {
        this.selectedPreviewDoc = full;
      }
    }).catch(() => { /* keep currently shown doc if hydration fails */ });
  }

  onDocCardClick(event: MouseEvent, doc: StoredDoc): void {
    if (this.hasActiveSelection()) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    this.openDocPreview(doc);
  }

  closeDocPreview(): void {
    this.showPreviewDialog = false;
    this.selectedPreviewDoc = null;
    this.previewProductEnvironments = [];
    this.previewProductEnvironmentsLoading = false;
    this.previewProductEnvironmentsQueryKey = null;
    this.runningActionKey = null;
  }

  onChatPreviewDoc(doc: StoredDoc): void {
    this.openDocPreview(doc);
  }

  onChatPendingJobsChange(hasPending: boolean): void {
    this.chatPendingUi = hasPending;
    if (hasPending) {
      void this.pollJobs();
    }
  }

  onPreviewBackdropClick(): void {
    this.closeDocPreview();
  }

  previewDocTypes(): Array<{ key: string; title: string; icon?: string; source?: string }> {
    if (!this.selectedPreviewDoc || this.selectedPreviewDoc.doc_type !== 'dop_app') {
      return [];
    }

    const docTypes = this.selectedPreviewDoc.content['doc_types'];
    if (!Array.isArray(docTypes)) {
      return [];
    }

    const appIcon = typeof this.selectedPreviewDoc.content['icon'] === 'string'
      ? this.selectedPreviewDoc.content['icon']
      : undefined;

    const result: Array<{ key: string; title: string; icon?: string; source?: string }> = [];

    for (const item of docTypes) {
      if (typeof item === 'string') {
        result.push({ key: item, title: this.humanizeKey(item), icon: appIcon });
        continue;
      }

      if (this.isPlainObject(item)) {
        const key = String(item['key'] ?? '').trim();
        if (!key) {
          continue;
        }

        const titleValue = item['title'];
        const iconValue = item['icon'];
        const sourceValue = item['source'];
        result.push({
          key,
          title: typeof titleValue === 'string' && titleValue.trim() ? titleValue : this.humanizeKey(key),
          icon: typeof iconValue === 'string' && iconValue.trim() ? iconValue : appIcon,
          source: typeof sourceValue === 'string' && sourceValue.trim() ? sourceValue : undefined,
        });
      }
    }

    return result;
  }

  previewDocActions(): Array<{ key: string; title: string; source: string; icon?: string }> {
    if (!this.selectedPreviewDoc) {
      return [];
    }
    return this.getDocTypeActions(this.selectedPreviewDoc.doc_type);
  }

  previewProductResources(): Array<{ resource: ProductResourceRef; doc?: StoredDoc }> {
    if (!this.selectedPreviewDoc || this.selectedPreviewDoc.doc_type !== 'dop_product') {
      return [];
    }

    const resources = this.selectedPreviewDoc.content['resources'];
    if (!Array.isArray(resources)) {
      return [];
    }

    // Build a lookup from the embedded resources_docs (present when product was
    // fetched via getProduct(), e.g. from the edit dialog path).
    const toKey = (appId: string | null | undefined, docType: string | undefined, name: string | undefined): string => {
      return `${(appId ?? '').trim().toLowerCase()}|${(docType ?? '').trim().toLowerCase()}|${(name ?? '').trim().toLowerCase()}`;
    };

    const embeddedDocsMap = new Map<string, StoredDoc>();
    const resourcesDocs = this.selectedPreviewDoc.content['resources_docs'];
    if (Array.isArray(resourcesDocs)) {
      for (const item of resourcesDocs) {
        if (this.isPlainObject(item) && typeof item['doc_type'] === 'string' && this.isPlainObject(item['content'])) {
          const doc = item as unknown as StoredDoc;
          const name = this.docDisplayName(doc);
          embeddedDocsMap.set(toKey(doc.app_id, doc.doc_type, name), doc);
        }
      }
    }

    return resources
      .filter((item) => this.isPlainObject(item))
      .map((item) => {
        const resource = item as unknown as ProductResourceRef;
        const key = toKey(resource.app_id, resource.doc_type, resource.name);
        // Prefer embedded docs; fall back to the currently loaded docs list.
        const doc = embeddedDocsMap.get(key) ??
          this.docs.find(
            (d) => toKey(d.app_id, d.doc_type, this.docDisplayName(d)) === key
          );
        return { resource, doc };
      });
  }

  private getDocTypeActions(docType: string): Array<{ key: string; title: string; source: string; icon?: string }> {
    for (const app of this.pluginApps) {
      const match = app.doc_types.find((doc) => doc.key === docType);
      if (!match || !this.isPlainObject(match)) {
        continue;
      }

      const actions = (match as { actions?: Record<string, unknown> }).actions;
      if (!actions || !this.isPlainObject(actions)) {
        return [];
      }

      const parsed: Array<{ key: string; title: string; source: string; icon?: string }> = [];
      for (const [key, value] of Object.entries(actions)) {
        if (!this.isPlainObject(value)) {
          continue;
        }
        const title = String((value as { title?: string }).title ?? '').trim() || this.humanizeKey(key);
        const source = String((value as { source?: string }).source ?? '').trim();
        const icon = String((value as { icon?: string }).icon ?? '').trim();
        if (!source) {
          continue;
        }
        parsed.push({ key, title, source, ...(icon && { icon }) });
      }
      return parsed;
    }

    return [];
  }

  openDocsForTypeFromPreview(docType: string): void {
    this.closeDocPreview();
    this.activeSection = 'docs';
    window.setTimeout(() => this.onSelectDocType(docType), 0);
  }

  async refreshDocTypeFromPreview(event: MouseEvent, docType: string): Promise<void> {
    event.preventDefault();
    event.stopPropagation();

    const appDocId = this.selectedPreviewDoc?.id;
    if (!appDocId) {
      this.error = 'Application doc ID is required to refresh docs.';
      return;
    }

    this.error = '';
    try {
      await this.api.createDocsRefreshJob({ app_doc_id: appDocId, doc_type: docType });
      await this.pollJobs();
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  async runDocActionFromPreview(event: MouseEvent, actionKey: string): Promise<void> {
    event.preventDefault();
    event.stopPropagation();

    const docId = this.selectedPreviewDoc?.id;
    if (!docId) {
      this.error = 'Document ID is required to run action.';
      return;
    }

    this.runningActionKey = actionKey;
    this.error = '';
    try {
      await this.api.createDocActionJob({ doc_id: docId, action_name: actionKey });
      await this.pollJobs();
    } catch (error) {
      this.error = this.asError(error);
      this.runningActionKey = null;
    }
  }

  openProductResourcePreview(event: Event, doc: StoredDoc): void {
    event.preventDefault();
    event.stopPropagation();
    this.openDocPreview(doc);
  }

  openProductEnvironmentPreview(event: Event, doc: StoredDoc): void {
    this.openProductResourcePreview(event, doc);
  }

  linkedProductForEnv(doc: StoredDoc): StoredDoc | null {
    if (doc.doc_type !== 'dop_env' || !doc.app_id) {
      return null;
    }

    return this.products.find((item) => item.doc_type === 'dop_product' && item.app_id === doc.app_id) ?? null;
  }

  openLinkedProductFromEnv(event: Event, doc: StoredDoc): void {
    event.preventDefault();
    event.stopPropagation();
    const product = this.linkedProductForEnv(doc);
    if (!product) {
      return;
    }
    this.openDocPreview(product);
  }

  productEnvironmentTypeLabel(doc: StoredDoc): string {
    const raw = String(doc.content['type'] ?? '').trim();
    return raw ? this.humanizeKey(raw.toLowerCase()) : 'Unknown';
  }

  productEnvironmentTypeClass(doc: StoredDoc): string {
    const type = String(doc.content['type'] ?? '').trim().toLowerCase();
    if (!type) {
      return 'preview-env-type-unknown';
    }
    if (type.includes('prod')) {
      return 'preview-env-type-production';
    }
    if (type.includes('stag') || type.includes('preprod') || type.includes('uat')) {
      return 'preview-env-type-staging';
    }
    if (type.includes('dev') || type.includes('local') || type.includes('sandbox')) {
      return 'preview-env-type-development';
    }
    if (type.includes('test') || type.includes('qa')) {
      return 'preview-env-type-testing';
    }
    return 'preview-env-type-unknown';
  }

  hasRenderableFact(doc: StoredDoc): boolean {
    const value = this.factValue(doc);
    return value.length > 0 && value !== '__exists__';
  }

  hasAnyFact(doc: StoredDoc): boolean {
    return this.factValue(doc).length > 0;
  }

  private factValue(doc: StoredDoc): string {
    return typeof doc.fact === 'string' ? doc.fact.trim() : '';
  }

  toggleSettingsMenu(): void {
    this.showSettingsMenu = !this.showSettingsMenu;
    if (this.showSettingsMenu) {
      this.showJobsMenu = false;
    }
  }

  toggleJobsMenu(): void {
    this.showJobsMenu = !this.showJobsMenu;
    if (this.showJobsMenu) {
      this.showSettingsMenu = false;
      void this.pollJobs();
    }
  }

  async logout(): Promise<void> {
    this.error = '';
    try {
      await this.auth.logout();
      await this.router.navigateByUrl('/login');
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  async refreshDocs(app: StoredDoc, docType: string): Promise<void> {
    this.error = '';
    try {
      await this.api.createDocsRefreshJob({ app_doc_id: app.id, doc_type: docType });
      await this.pollJobs();
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  private async triggerInitialGitSync(app: StoredDoc, plugin: PluginApp): Promise<void> {
    const docTypes = plugin.doc_types ?? [];
    const jobResults = await Promise.all(
      docTypes.map((docType) => this.api.createDocsRefreshJob({ app_doc_id: app.id, doc_type: docType.key }))
    );
    this.onboardingGitRefreshJobIds = [...this.onboardingGitRefreshJobIds, ...jobResults.map((job) => job.id)];
    await this.pollJobs();
  }

  get onboardingGitRefreshJobsRunning(): boolean {
    if (this.onboardingGitRefreshJobIds.length === 0) {
      return false;
    }
    return this.onboardingGitRefreshJobIds.some((jobId) => {
      const job = this.jobs.find((j) => j.id === jobId);
      return !job || job.status === 'running' || job.status === 'queued' || job.status === 'blocked';
    });
  }

  private maybeAdvanceOnboardingAfterGitSync(): void {
    if (!this.showOnboarding || this.onboardingStatus === 'completed') {
      return;
    }
    if (this.onboardingStep !== 2 || !this.onboardingGitConfigured) {
      return;
    }
    if (this.onboardingGitRefreshJobIds.length === 0 || this.onboardingGitRefreshJobsRunning) {
      return;
    }

    this.onboardingStep = 3;
    if (!this.onboardingAnalysisPromptSent) {
      void this.startOnboardingAnalysisIfNeeded();
    }
  }

  private onboardingGitProviderName(): string {
    const gitApp = this.applications.find(
      (app) => this.pluginByKey(String(app.content['plugin_key'] ?? ''))?.category === 'git'
    );
    if (!gitApp) {
      return 'configured Git provider';
    }

    const configuredName = String(gitApp.content['name'] ?? '').trim();
    if (configuredName) {
      return configuredName;
    }

    const pluginName = this.pluginByKey(String(gitApp.content['plugin_key'] ?? ''))?.name;
    if (pluginName && pluginName.trim()) {
      return pluginName.trim();
    }

    return gitApp.app_id?.trim() || 'configured Git provider';
  }

  private onboardingPrompt(): string {
    const gitProviderName = this.onboardingGitProviderName();

    return `Analyze my Git activity for the last 6 months.

Goal:
Infer which products I am working on based on my recent repository activity. A product is a subset of applications/APIs working together as one logical entity.

Requirements:
1. Look at my Git activity from the last 6 months.
2. Extract all repositories where I had meaningful activity.
3. Infer products from repository names, repo groups, infrastructure naming, k8s app names, Terraform/Terragrunt modules, Chef cookbooks, and other DevOps signals.
4. If multiple names appear to refer to the same product, merge them into one product.
5. Leave only one canonical product ID per product.
6. Prefer the strongest product identifier available:
   - first: authoritative app/product ID like \`prd####\`
   - second: stable product tag used in resources
   - third: normalized product slug from repo/resource naming
7. Do not keep infra-only buckets as products unless there is strong evidence they are real products.
8. For each product, include related Git repositories that support the conclusion.
9. Be explicit about uncertainty, but still return the best merged result.

Output format:
Return JSON only, as an array of objects in this exact structure:

[
  {
    "name": "name",
    "id": "product_id",
    "related_resources": [
      { "git_repo": "repo_url" }
    ]
  }
]

Additional rules:
- Merge aliases and component names into the parent product when they clearly share the same product ID.
- Do not output duplicate repositories.
- Keep only the final merged product list.
- No explanation outside JSON.

For my recent Git activities ask agent configured for "${gitProviderName}". There you'll find list of repos I'm working on.

After producing JSON response, also write the final JSON array to /tmp/onboarding.json.`;
  }

  private async getOrCreateOnboardingThread(): Promise<number> {
    const threads = await this.api.listChatThreads();
    const existing = threads.find((thread) => thread.name.trim().toLowerCase() === 'onboarding');
    if (existing) {
      return existing.id;
    }
    const created = await this.api.createChatThread({ name: 'Onboarding', attached_docs: [] });
    return created.id;
  }

  private async getExistingOnboardingThreadId(): Promise<number | null> {
    const threads = await this.api.listChatThreads();
    const existing = threads.find((thread) => thread.name.trim().toLowerCase() === 'onboarding');
    return existing ? existing.id : null;
  }

  private pickLatestOnboardingAnalysisJob(jobs: JobInfo[], threadId: number): JobInfo | null {
    const threadJobs = jobs
      .filter((job) => job.job_type === 'chat_message' && job.thread_id === threadId)
      .sort((left, right) => right.created_at.localeCompare(left.created_at));

    const active = threadJobs.find((job) => job.status === 'running' || job.status === 'queued' || job.status === 'blocked');
    if (active) {
      return active;
    }

    return threadJobs.find((job) => job.status === 'success') ?? null;
  }

  private async recoverOnboardingAnalysisJobIfNeeded(jobs: JobInfo[]): Promise<void> {
    if (this.onboardingAnalysisJobId || this.onboardingAnalysisRecoveryAttempted) {
      return;
    }
    if (!this.showOnboarding || this.onboardingStep < 3) {
      return;
    }

    try {
      const threadId = this.onboardingThreadId ?? await this.getExistingOnboardingThreadId();
      if (threadId === null) {
        this.onboardingAnalysisRecoveryAttempted = true;
        return;
      }

      this.onboardingThreadId = threadId;
      const recovered = this.pickLatestOnboardingAnalysisJob(jobs, threadId);
      if (!recovered) {
        this.onboardingAnalysisRecoveryAttempted = true;
        return;
      }

      this.onboardingAnalysisJobId = recovered.id;
      this.onboardingAnalysisPromptSent = true;
      this.onboardingStep = 3;
      this.onboardingAnalysisRecoveryAttempted = true;
    } catch {
      // Retry recovery on next poll if API is temporarily unavailable.
    }
  }

  private recoverOnboardingGitRefreshJobsIfNeeded(jobs: JobInfo[]): void {
    if (!this.showOnboarding || this.onboardingStatus === 'completed') {
      return;
    }
    if (this.onboardingStep !== 2 || !this.onboardingGitConfigured) {
      return;
    }
    if (this.onboardingGitRefreshJobIds.length > 0) {
      return;
    }

    const gitApp = this.applications.find(
      (app) => this.pluginByKey(String(app.content['plugin_key'] ?? ''))?.category === 'git'
    );
    if (!gitApp) {
      return;
    }

    const gitRefreshJobs = jobs.filter(
      (job) =>
        job.job_type === 'docs_refresh' &&
        job.app_doc_id === gitApp.id &&
        (job.status === 'running' || job.status === 'queued' || job.status === 'blocked')
    );

    if (gitRefreshJobs.length > 0) {
      this.onboardingGitRefreshJobIds = gitRefreshJobs.map((job) => job.id);
    }
  }

  async startOnboardingAnalysisIfNeeded(): Promise<void> {
    if (this.onboardingAnalysisPromptSent) {
      return;
    }
    this.error = '';
    this.onboardingLoading = true;
    this.onboardingProductsAutoRetryDone = false;
    try {
      const threadId = await this.getOrCreateOnboardingThread();
      this.onboardingThreadId = threadId;
      const response = await this.api.sendChatMessage(threadId, { text: this.onboardingPrompt() });
      this.onboardingAnalysisJobId = response.job_id;
      this.onboardingAnalysisPromptSent = true;
      this.onboardingStep = 3;
      await this.pollJobs();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.onboardingLoading = false;
    }
  }

  get onboardingAnalysisJob(): JobInfo | null {
    if (!this.onboardingAnalysisJobId) {
      return null;
    }
    return this.jobs.find((job) => job.id === this.onboardingAnalysisJobId) ?? null;
  }

  get onboardingAnalysisProductsFromJob(): Array<Record<string, unknown>> {
    const job = this.onboardingAnalysisJobDetails ?? this.onboardingAnalysisJob;
    if (!job?.result) {
      return [];
    }
    const value = job.result['onboarding_products'];
    return Array.isArray(value) ? value.filter((item) => this.isPlainObject(item)) as Array<Record<string, unknown>> : [];
  }

  get onboardingAnalysisPreviewItems(): OnboardingAnalysisPreviewItem[] {
    const details = this.onboardingAnalysisJobDetails;
    if (!details) {
      return [];
    }

    const activity: OnboardingAnalysisPreviewItem[] = (details.agent_events ?? []).map((item) => ({
      kind: 'activity',
      text: item.text,
      timestamp: item.timestamp,
    }));
    const logs: OnboardingAnalysisPreviewItem[] = (details.logs ?? []).map((item) => ({
      kind: item.stream,
      text: item.entry,
      timestamp: item.timestamp,
    }));

    return [...activity, ...logs]
      .sort((left, right) => left.timestamp.localeCompare(right.timestamp))
      .slice(-60);
  }

  private async ensureOnboardingProductsLoaded(): Promise<void> {
    if (this.onboardingProducts.length > 0) {
      return;
    }

    const parsed = this.onboardingAnalysisProductsFromJob;
    this.onboardingProducts = parsed.map((item) => {
      const id = String(item['id'] ?? '').trim();
      const name = String(item['name'] ?? '').trim() || id;
      const rawResources = Array.isArray(item['related_resources']) ? item['related_resources'] : [];
      const resources: OnboardingResourceSelection[] = rawResources
        .filter((res) => this.isPlainObject(res))
        .map((res) => ({
          git_repo: String((res as Record<string, unknown>)['git_repo'] ?? '').trim(),
          selected: true,
        }))
        .filter((res) => !!res.git_repo);
      return {
        id,
        name,
        selected: true,
        resources,
      };
    }).filter((item) => item.id && item.name);
  }

  private async requestOnboardingJsonWriteAgain(): Promise<void> {
    if (!this.onboardingThreadId) {
      return;
    }
    const response = await this.api.sendChatMessage(this.onboardingThreadId, {
      text: 'Please write result in expected format to /tmp/onboarding.json and confirm done.',
    });
    this.onboardingAnalysisJobId = response.job_id;
    await this.pollJobs();
  }

  async proceedFromOnboardingAnalysis(): Promise<void> {
    const job = this.onboardingAnalysisJob;
    if (!job || job.status !== 'success') {
      this.error = 'Onboarding analysis job is not finished yet.';
      return;
    }

    await this.ensureOnboardingProductsLoaded();
    if (this.onboardingProducts.length === 0) {
      await this.requestOnboardingJsonWriteAgain();
      await this.ensureOnboardingProductsLoaded();
    }

    if (this.onboardingProducts.length === 0) {
      this.error = 'No onboarding products found in /tmp/onboarding.json. Please retry analysis.';
      return;
    }

    this.onboardingStep = 3;
  }

  toggleOnboardingProduct(index: number, selected: boolean): void {
    this.onboardingProducts = this.onboardingProducts.map((item, itemIndex) => {
      if (itemIndex !== index) {
        return item;
      }
      return {
        ...item,
        selected,
      };
    });
  }

  toggleOnboardingResource(productIndex: number, resourceIndex: number, selected: boolean): void {
    this.onboardingProducts = this.onboardingProducts.map((product, pIndex) => {
      if (pIndex !== productIndex) {
        return product;
      }
      return {
        ...product,
        resources: product.resources.map((resource, rIndex) => {
          if (rIndex !== resourceIndex) {
            return resource;
          }
          return {
            ...resource,
            selected,
          };
        }),
      };
    });
  }

  private async resolveRepoUrlToResource(repoUrl: string): Promise<ProductResourceRef | null> {
    const normalized = repoUrl.trim();
    if (!normalized) {
      return null;
    }

    const response = await this.api.searchDocs({ q: normalized, doc_type: 'gitlab_repos' });
    const docs = response.results;
    if (docs.length === 0) {
      return null;
    }

    const exact = docs.find((doc) => (this.docUrl(doc) ?? '').trim().toLowerCase() === normalized.toLowerCase());
    const target = exact ?? docs[0];
    return this.docToResourceRef(target);
  }

  async applyOnboardingProducts(): Promise<void> {
    this.error = '';
    this.onboardingLoading = true;
    try {
      for (const product of this.onboardingProducts) {
        if (!product.selected) {
          continue;
        }

        const resources: ProductResourceRef[] = [];
        const seen = new Set<string>();
        for (const resource of product.resources) {
          if (!resource.selected) {
            continue;
          }
          const resolved = await this.resolveRepoUrlToResource(resource.git_repo);
          if (!resolved) {
            continue;
          }
          const key = this.resourceKey(resolved);
          if (seen.has(key)) {
            continue;
          }
          seen.add(key);
          resources.push(resolved);
        }

        await this.api.addProduct({
          product_id: product.id,
          name: product.name,
          prompt: '',
          description: '',
          resources,
        });
        this.onboardingCreatedProductIds.add(product.id);
      }

      await this.refreshProducts();
      this.onboardingStep = 4;
      await this.startOnboardingSummaryJobs();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.onboardingLoading = false;
    }
  }

  private async getDocIdByResource(resource: ProductResourceRef): Promise<number | null> {
    const response = await this.api.searchDocs({
      q: resource.name,
      doc_type: resource.doc_type,
      app_id: resource.app_id,
    });
    const match = response.results.find((doc) => {
      const docName = this.docDisplayName(doc).trim().toLowerCase();
      const targetName = resource.name.trim().toLowerCase();
      if (docName !== targetName) {
        return false;
      }
      if (!resource.url) {
        return true;
      }
      return (this.docUrl(doc) ?? '').trim().toLowerCase() === resource.url.trim().toLowerCase();
    });
    return match?.id ?? null;
  }

  private async startOnboardingSummaryJobs(): Promise<void> {
    this.onboardingResourceJobIdsByProductId = {};
    this.onboardingWorkflowByProductId = {};
    this.onboardingProductJobIds = [];
    this.onboardingEnvJobIds = [];

    for (const product of this.products) {
      if (!this.onboardingCreatedProductIds.has(product.app_id ?? '')) {
        continue;
      }

      const resources = this.asProductResources(product.content['resources']);
      const resourceJobIds: string[] = [];
      let workflowId: string | undefined;

      for (let index = 0; index < resources.length; index += 1) {
        const resource = resources[index];
        const docId = await this.getDocIdByResource(resource);
        if (!docId) {
          continue;
        }

        const created = await this.api.createDocActionJob({
          doc_id: docId,
          action_name: 'devops_summary',
          workflow_id: workflowId,
          max_parallel: 3,
        });
        workflowId = workflowId ?? created.workflow_id;
        resourceJobIds.push(created.id);
      }

      const productJob = await this.api.createDocActionJob({
        doc_id: product.id,
        action_name: 'devops_summary',
        depends_on_job_ids: resourceJobIds,
        workflow_id: workflowId,
      });
      this.onboardingProductJobIds.push(productJob.id);
      this.onboardingResourceJobIdsByProductId[product.app_id ?? String(product.id)] = resourceJobIds;
      this.onboardingWorkflowByProductId[product.app_id ?? String(product.id)] = productJob.workflow_id ?? (workflowId ?? '');

      const envs = await this.api.searchDocs({ doc_type: 'dop_env', app_id: product.app_id ?? undefined });
      for (const env of envs.results) {
        const envJob = await this.api.createDocActionJob({
          doc_id: env.id,
          action_name: 'devops_summary',
          depends_on_job_ids: [productJob.id],
          workflow_id: productJob.workflow_id,
        });
        this.onboardingEnvJobIds.push(envJob.id);
      }
    }

    await this.pollJobs();
  }

  get onboardingResourceSummaryJobs(): JobInfo[] {
    const allIds = new Set(Object.values(this.onboardingResourceJobIdsByProductId).flat());
    return this.jobs.filter((job) => allIds.has(job.id));
  }

  get onboardingProductSummaryJobs(): JobInfo[] {
    const ids = new Set(this.onboardingProductJobIds);
    return this.jobs.filter((job) => ids.has(job.id));
  }

  get onboardingEnvSummaryJobs(): JobInfo[] {
    const ids = new Set(this.onboardingEnvJobIds);
    return this.jobs.filter((job) => ids.has(job.id));
  }

  private isAllJobsSuccessful(jobs: JobInfo[]): boolean {
    return jobs.length > 0 && jobs.every((job) => job.status === 'success');
  }

  private isAnyJobsFailed(jobs: JobInfo[]): boolean {
    return jobs.some((job) => job.status === 'failed' || job.status === 'cancelled');
  }

  async finalizeOnboardingIfReady(): Promise<void> {
    const stageJobs = [
      ...this.onboardingResourceSummaryJobs,
      ...this.onboardingProductSummaryJobs,
      ...this.onboardingEnvSummaryJobs,
    ];

    if (stageJobs.length === 0) {
      return;
    }
    if (this.isAnyJobsFailed(stageJobs)) {
      this.error = 'Some onboarding summary jobs failed. Open job logs and retry.';
      return;
    }
    if (!this.isAllJobsSuccessful(stageJobs)) {
      return;
    }

    await this.persistOnboardingStatus('completed');
  }

  async openOnboardingConfigureApp(plugin: PluginApp): Promise<void> {
    this.openAddDialog();
    this.onSelectAddApp(plugin.plugin_key);
  }

  async openJobDialog(job: JobInfo): Promise<void> {
    this.error = '';
    try {
      this.selectedJob = this.mergeJobWithListData(await this.api.getJob(job.id), job);
      this.selectedJobSignature = this.selectedJob ? this.jobSignature(this.selectedJob) : null;
      this.showJobDialog = true;
      this.showJobsMenu = false;
      // Start polling for askpass requests if job is running
      if (this.selectedJob && this.selectedJob.status === 'running') {
        this.startAskPassPolling();
      }
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  closeJobDialog(): void {
    this.showJobDialog = false;
    this.selectedJob = null;
    this.selectedJobSignature = null;
    this.stopAskPassPolling();
  }

  jumpToChatFromJob(job: JobInfo): void {
    const threadId = typeof job.thread_id === 'number' ? job.thread_id : null;
    if (threadId === null) {
      return;
    }

    this.closeJobDialog();
    this.activeSection = 'chat';
    try {
      window.localStorage.setItem('dop.chat.selectedThreadId', String(threadId));
    } catch {
      // ignore local storage errors
    }
    this.focusChatThread(threadId, 12);
  }

  canOpenDocFromJob(job: JobInfo): boolean {
    return job.job_type === 'doc_action' && !!job.doc_name && !!job.doc_type;
  }

  async openDocFromJob(job: JobInfo, event: MouseEvent): Promise<void> {
    event.preventDefault();
    event.stopPropagation();

    if (!this.canOpenDocFromJob(job)) {
      return;
    }

    this.error = '';
    try {
      const target = await this.findDocForJob(job);

      if (!target) {
        this.error = `Could not find document ${job.doc_name ?? ''}.`;
        return;
      }

      this.closeJobDialog();
      this.openDocPreview(target);
    } catch (error) {
      this.error = this.asError(error);
    }
  }

  canRestartJob(job: JobInfo): boolean {
    if (job.status !== 'failed') {
      return false;
    }
    return job.job_type === 'docs_refresh' || (job.job_type === 'doc_action' && !!job.action_name);
  }

  async restartJob(job: JobInfo, event?: Event): Promise<void> {
    event?.preventDefault();
    event?.stopPropagation();

    if (!this.canRestartJob(job)) {
      return;
    }

    this.loading = true;
    this.error = '';
    try {
      let created: JobInfo;
      if (job.job_type === 'docs_refresh') {
        if (!job.app_doc_id) {
          this.error = 'Cannot restart docs refresh: missing application ID.';
          return;
        }
        created = await this.api.createDocsRefreshJob({
          app_doc_id: job.app_doc_id,
          doc_type: job.doc_type,
        });
      } else {
        const actionName = job.action_name;
        if (!actionName) {
          this.error = 'Cannot restart action job: missing action name.';
          return;
        }
        const targetDoc = await this.findDocForJob(job);
        if (!targetDoc) {
          this.error = `Cannot restart action job: document ${job.doc_name ?? ''} not found.`;
          return;
        }
        created = await this.api.createDocActionJob({
          doc_id: targetDoc.id,
          action_name: actionName,
        });
      }

      this.replaceOnboardingSummaryJobIdIfTracked(job, created);

      await this.pollJobs();
      await this.openJobDialog(created);
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  private async findDocForJob(job: JobInfo): Promise<StoredDoc | null> {
    if (!job.doc_name || !job.doc_type) {
      return null;
    }

    const response = await this.api.searchDocs({
      q: job.doc_name,
      doc_type: job.doc_type,
      app_id: job.app_id || undefined,
      limit: 25,
    });

    const targetName = job.doc_name.trim().toLowerCase();
    const exact = response.results.find((doc) => this.docDisplayName(doc).trim().toLowerCase() === targetName);
    return exact ?? response.results[0] ?? null;
  }

  private replaceOnboardingSummaryJobIdIfTracked(oldJob: JobInfo, newJob: JobInfo): void {
    if (oldJob.action_name !== 'devops_summary') {
      return;
    }

    let changed = false;

    const replaceInList = (ids: string[]): string[] => {
      if (!ids.includes(oldJob.id)) {
        return ids;
      }
      changed = true;
      return ids.map((id) => (id === oldJob.id ? newJob.id : id));
    };

    const nextProductJobIds = replaceInList(this.onboardingProductJobIds);
    const nextEnvJobIds = replaceInList(this.onboardingEnvJobIds);

    const nextResourceByProductId: Record<string, string[]> = {};
    for (const [productId, ids] of Object.entries(this.onboardingResourceJobIdsByProductId)) {
      nextResourceByProductId[productId] = replaceInList(ids);
    }

    if (!changed) {
      return;
    }

    this.onboardingProductJobIds = nextProductJobIds;
    this.onboardingEnvJobIds = nextEnvJobIds;
    this.onboardingResourceJobIdsByProductId = nextResourceByProductId;
  }

  private focusChatThread(threadId: number, remainingAttempts: number): void {
    if (this.chatComponent) {
      void this.chatComponent.selectThread(threadId);
      return;
    }

    if (remainingAttempts <= 0) {
      return;
    }

    window.setTimeout(() => {
      this.focusChatThread(threadId, remainingAttempts - 1);
    }, 100);
  }

  async reloadConfigs(): Promise<void> {
    this.loading = true;
    this.error = '';
    this.showSettingsMenu = false;

    try {
      await this.api.reloadConfigs();
      this.pluginApps = await this.api.listPluginApps();
      await this.refreshApplications();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  private startAskPassPolling(): void {
    this.stopAskPassPolling();
    this.scheduleAskPassPoll();
  }

  private stopAskPassPolling(): void {
    if (this.askPassPollTimer !== null) {
      window.clearTimeout(this.askPassPollTimer);
      this.askPassPollTimer = null;
    }
  }

  private scheduleAskPassPoll(): void {
    this.askPassPollTimer = window.setTimeout(() => {
      void this.zone.runOutsideAngular(() => this.pollAskPassRequests());
    }, 500);
  }

  private async pollAskPassRequests(): Promise<void> {
    if (!this.selectedJob) {
      return;
    }

    try {
      const requests = await this.api.getJobAskPassRequests(this.selectedJob.id);
      const applyUpdates = (): void => {
        this.pendingAskPassRequests = requests;

        // Show the first pending request if there are any and the dialog is not already open
        if (requests.length > 0 && !this.showAskPassDialog) {
          this.currentAskPassRequest = requests[0];
          this.askPassPassword = '';
          this.askPassSave = false;
          this.showAskPassDialog = true;
        }
      };

      this.zone.run(() => applyUpdates());
    } catch (error) {
      // Silently ignore polling errors
      console.error('Failed to poll askpass requests:', error);
    }

    // Schedule next poll if job is still selected
    if (this.selectedJob) {
      this.scheduleAskPassPoll();
    }
  }

  closeAskPassDialog(): void {
    this.showAskPassDialog = false;
    this.currentAskPassRequest = null;
    this.askPassPassword = '';
    this.askPassSave = false;
  }

  askPassInputType(request: AskPassRequest): 'text' | 'password' {
    return this.isUsernameAskPassRequest(request) ? 'text' : 'password';
  }

  askPassPlaceholder(request: AskPassRequest): string {
    return this.isUsernameAskPassRequest(request) ? 'Enter username' : 'Enter password or passphrase';
  }

  askPassSaveLabel(request: AskPassRequest): string {
    if (this.isSshPassphraseAskPassRequest(request)) {
      return 'Save for current session (add key to ssh-agent)';
    }
    return 'Save for current session';
  }

  isAskPassSaveEnabled(request: AskPassRequest): boolean {
    if (typeof request.can_save === 'boolean') {
      return request.can_save;
    }
    return !this.isUsernameAskPassRequest(request);
  }

  private isUsernameAskPassRequest(request: AskPassRequest): boolean {
    if (request.prompt_kind === 'username') {
      return true;
    }
    return request.prompt.toLowerCase().includes('username for');
  }

  private isSshPassphraseAskPassRequest(request: AskPassRequest): boolean {
    if (request.prompt_kind === 'ssh_passphrase') {
      return true;
    }
    return request.prompt.toLowerCase().includes('passphrase');
  }

  async cancelAskPassDialog(): Promise<void> {
    if (!this.currentAskPassRequest) {
      this.closeAskPassDialog();
      return;
    }

    this.loading = true;
    this.error = '';

    try {
      await this.api.cancelAskPassRequest(this.currentAskPassRequest.request_id);
      this.closeAskPassDialog();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  async submitAskPassPassword(): Promise<void> {
    if (!this.currentAskPassRequest) {
      return;
    }

    this.loading = true;
    this.error = '';

    try {
      await this.api.answerAskPassRequest(
        this.currentAskPassRequest.request_id,
        this.askPassPassword,
        this.askPassSave
      );
      this.closeAskPassDialog();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.loading = false;
    }
  }

  private async collectPendingAskPassRequests(jobs: JobInfo[]): Promise<AskPassRequest[]> {
    const activeJobs = jobs.filter((job) => job.status === 'running' || job.status === 'queued' || job.status === 'blocked');
    if (activeJobs.length === 0) {
      return [];
    }

    const unique = new Map<string, AskPassRequest>();
    for (const job of activeJobs) {
      try {
        const requests = await this.api.getJobAskPassRequests(job.id);
        for (const request of requests) {
          unique.set(request.request_id, request);
        }
      } catch {
        // Ignore askpass polling errors for a specific job.
      }
    }

    return Array.from(unique.values());
  }

  private askPassRequestsSignature(requests: AskPassRequest[]): string {
    return requests
      .map((request) => request.request_id)
      .sort((left, right) => left.localeCompare(right))
      .join('|');
  }

  async pollJobs(): Promise<void> {
    await this.pollJobsInternal(true);
  }

  private async pollJobsOutside(): Promise<void> {
    await this.pollJobsInternal(false);
  }

  private async pollJobsInternal(runInZone: boolean): Promise<void> {
    try {
      const jobs = await this.api.listJobs();
      await this.recoverOnboardingAnalysisJobIfNeeded(jobs);
      this.recoverOnboardingGitRefreshJobsIfNeeded(jobs);
      this.recoverOnboardingSummaryJobsIfNeeded(jobs);
      const pendingAskPassRequests = await this.collectPendingAskPassRequests(jobs);
      const onboardingDetail = this.onboardingAnalysisJobId
        ? await this.api.getJob(this.onboardingAnalysisJobId).catch(() => null)
        : null;
      const jobsSignature = this.jobsListSignature(jobs);
      const jobsChanged = jobsSignature !== this.jobsSignature;
      const askPassChanged = this.askPassRequestsSignature(pendingAskPassRequests) !== this.askPassRequestsSignature(this.pendingAskPassRequests);
      let nextSelectedJob: JobInfo | null = null;
      let nextSelectedSignature: string | null = this.selectedJobSignature;
      let selectedJobChanged = false;

      if (this.selectedJob) {
        const selected = jobs.find((job) => job.id === this.selectedJob?.id);
        if (selected) {
          nextSelectedJob = this.mergeJobWithListData(await this.api.getJob(selected.id), selected);
          nextSelectedSignature = this.jobSignature(nextSelectedJob);
          selectedJobChanged = nextSelectedSignature !== this.selectedJobSignature;
          if (nextSelectedJob.status !== 'running' && nextSelectedJob.status !== 'queued') {
            this.stopAskPassPolling();
          }
        }
      }

      const notificationsToPush: JobInfo[] = [];
      let shouldSearchDocs = false;
      if (jobsChanged) {
        for (const job of jobs) {
          if (
            (job.status === 'success' || job.status === 'failed')
            && !this.notifiedCompletedJobs.has(job.id)
            && this.isJobNewerThanLastSeen(job)
          ) {
            notificationsToPush.push(job);
            if (job.status === 'success') {
              shouldSearchDocs = true;
            }
          }
        }
      }

      const onboardingChanged = (onboardingDetail?.id ?? null) !== (this.onboardingAnalysisJobDetails?.id ?? null)
        || (onboardingDetail?.status ?? null) !== (this.onboardingAnalysisJobDetails?.status ?? null)
        || (onboardingDetail?.logs?.length ?? 0) !== (this.onboardingAnalysisJobDetails?.logs?.length ?? 0)
        || (onboardingDetail?.agent_events?.length ?? 0) !== (this.onboardingAnalysisJobDetails?.agent_events?.length ?? 0);
      const hasUpdates = jobsChanged || selectedJobChanged || notificationsToPush.length > 0 || askPassChanged || onboardingChanged;
      if (!hasUpdates) {
        const hasActiveJobs = jobs.some((job) => job.status === 'running' || job.status === 'queued' || job.status === 'blocked');
        this.scheduleJobsPoll(hasActiveJobs ? this.jobsPollFastMs : this.jobsPollIdleMs);
        return;
      }

      const applyUpdates = (): void => {
        if (jobsChanged) {
          this.jobsSignature = jobsSignature;
          this.jobs = jobs;
        }

        if (selectedJobChanged && nextSelectedJob) {
          this.selectedJobSignature = nextSelectedSignature;
          this.selectedJob = nextSelectedJob;
        }

        if (askPassChanged) {
          this.pendingAskPassRequests = pendingAskPassRequests;
          if (!this.showAskPassDialog && pendingAskPassRequests.length > 0) {
            this.currentAskPassRequest = pendingAskPassRequests[0];
            this.askPassPassword = '';
            this.askPassSave = false;
            this.showAskPassDialog = true;
          }
        }

        if (onboardingChanged) {
          this.onboardingAnalysisJobDetails = onboardingDetail;
        }

        this.maybeAdvanceOnboardingAfterGitSync();

            if ((onboardingDetail?.status ?? null) === 'success' && this.onboardingStep === 3 && this.onboardingProducts.length === 0) {
              void this.maybeAutoLoadOnboardingProducts();
            }

        for (const job of notificationsToPush) {
          this.notifiedCompletedJobs.add(job.id);
          this.pushNotification(job);
        }

        if (shouldSearchDocs) {
          void this.searchDocs();
        }
      };

      if (runInZone) {
        applyUpdates();
      } else {
        this.zone.run(() => applyUpdates());
      }

      const hasActiveJobs = jobs.some((job) => job.status === 'running' || job.status === 'queued' || job.status === 'blocked');
      this.scheduleJobsPoll(hasActiveJobs ? this.jobsPollFastMs : this.jobsPollIdleMs);

      if (this.onboardingStep === 4 && this.onboardingStatus !== 'completed') {
        void this.finalizeOnboardingIfReady();
      }
    } catch (error) {
      if (runInZone) {
        this.error = this.asError(error);
      } else {
        this.zone.run(() => {
          this.error = this.asError(error);
        });
      }
      this.scheduleJobsPoll(this.jobsPollIdleMs);
    }
  }

  private recoverOnboardingSummaryJobsIfNeeded(jobs: JobInfo[]): void {
    if (!this.showOnboarding || this.onboardingStatus === 'completed') {
      return;
    }

    const summaryJobs = jobs.filter(
      (job) => job.job_type === 'doc_action' && job.action_name === 'devops_summary'
    );
    if (summaryJobs.length === 0) {
      return;
    }

    // If summary jobs are present, onboarding is at stage 4 even after reload.
    if (this.onboardingStep < 4) {
      this.onboardingStep = 4;
    }

    if (this.onboardingProductJobIds.length === 0) {
      this.onboardingProductJobIds = summaryJobs
        .filter((job) => job.doc_type === 'dop_product')
        .map((job) => job.id);
    }

    if (this.onboardingEnvJobIds.length === 0) {
      this.onboardingEnvJobIds = summaryJobs
        .filter((job) => job.doc_type === 'dop_env')
        .map((job) => job.id);
    }

    if (Object.keys(this.onboardingResourceJobIdsByProductId).length === 0) {
      const resourceIds = summaryJobs
        .filter((job) => job.doc_type !== 'dop_product' && job.doc_type !== 'dop_env')
        .map((job) => job.id);
      if (resourceIds.length > 0) {
        this.onboardingResourceJobIdsByProductId = {
          recovered: resourceIds,
        };
      }
    }
  }

  private scheduleJobsPoll(delayMs: number): void {
    if (this.jobsPollTimer !== null) {
      window.clearTimeout(this.jobsPollTimer);
      this.jobsPollTimer = null;
    }

    this.zone.runOutsideAngular(() => {
      this.jobsPollTimer = window.setTimeout(() => {
        void this.pollJobsOutside();
      }, delayMs);
    });
  }

  pushNotification(job: JobInfo): void {
    const id = `${job.id}-${Date.now()}`;
    const notification = {
      id,
      status: job.status === 'success' ? 'success' as const : 'failed' as const,
      icon: job.dop_app_icon,
      appId: job.app_id ?? '-',
      title: job.doc_type_title || job.doc_type,
      summary: job.summary ?? (job.status === 'success' ? 'Completed' : 'Failed'),
      job,
    };

    // Clear running action if this job matches
    if (job.job_type === 'doc_action' && job.action_name === this.runningActionKey) {
      this.runningActionKey = null;
    }

    // Auto-open URI if job result contains a uri field
    if (job.status === 'success' && job.result && typeof job.result === 'object' && 'uri' in job.result) {
      const uri = job.result['uri'];
      if (typeof uri === 'string' && uri) {
        window.open(uri, '_blank');
      }
    }

    this.notifications = [notification, ...this.notifications].slice(0, 5);
    this.updateLastSeenCompletedJobAt(job);
    window.setTimeout(() => {
      this.notifications = this.notifications.filter((item) => item.id !== id);
    }, 5000);
  }

  @HostListener('document:keydown.escape')
  onEscapeKey(): void {
    if (this.showPreviewDialog) {
      this.closeDocPreview();
    }
    if (this.showJobDialog) {
      this.closeJobDialog();
    }
    if (this.showAddDialog) {
      this.closeAddDialog();
    }
    if (this.showProductDialog) {
      this.closeProductDialog();
    }
    if (this.showAskPassDialog) {
      void this.cancelAskPassDialog();
    }
    this.showSettingsMenu = false;
    this.showJobsMenu = false;
    this.docsTypePickerOpen = false;
    this.docsTypeFieldOpen = false;
    this.docsAppFieldOpen = false;
    this.addAppPickerOpen = false;
    this.productResourcePickerOpen = false;
  }

  onAddDialogBackdropClick(): void {
    this.closeAddDialog();
  }

  onProductDialogBackdropClick(): void {
    this.closeProductDialog();
  }

  @HostListener('document:click')
  onDocumentClick(): void {
    if (this.hasActiveSelection()) {
      return;
    }

    if (!this.showSettingsMenu && !this.showJobsMenu && !this.docsTypeFieldOpen && !this.addAppPickerOpen && !this.productResourcePickerOpen) {
      return;
    }

    this.showSettingsMenu = false;
    this.showJobsMenu = false;
    this.docsTypeFieldOpen = false;
    this.docsAppFieldOpen = false;
    this.addAppPickerOpen = false;
    this.productResourcePickerOpen = false;
  }

  previewEntries(): Array<{
    label: string;
    html?: string;
    isNested?: boolean;
    nestedEntries?: Array<{ label: string; html: string }>;
    isList?: boolean;
    listItems?: Array<{ html?: string; nestedEntries?: Array<{ label: string; html: string }> }>;
  }> {
    if (!this.selectedPreviewDoc) {
      return [];
    }

    const hidden = new Set<string>(['plugin_key', 'name', 'icon', 'url']);
    if (this.selectedPreviewDoc.doc_type === 'dop_app') {
      hidden.add('settings');
      hidden.add('doc_types');
    }
    if (this.selectedPreviewDoc.doc_type === 'dop_product') {
      hidden.add('environments');
      hidden.add('resources');
      hidden.add('resources_docs');
    }
    if (this.selectedPreviewDoc.doc_type === 'dop_env') {
      hidden.add('type');
    }

    return Object.entries(this.selectedPreviewDoc.content)
      .filter(([key, value]) => !hidden.has(key) && value !== null && value !== undefined && value !== '')
      .map(([key, value]) => {
        if (Array.isArray(value)) {
          const listItems = value
            .filter((item) => item !== null && item !== undefined && item !== '')
            .map((item) => {
              if (this.isPlainObject(item)) {
                return {
                  nestedEntries: this.objectToNestedEntries(item),
                };
              }

              return {
                html: this.renderMarkdown(String(item ?? '')),
              };
            });

          return {
            label: this.humanizeKey(key),
            isList: true,
            listItems,
          };
        }

        if (this.isPlainObject(value)) {
          return {
            label: this.humanizeKey(key),
            isNested: true,
            nestedEntries: this.objectToNestedEntries(value),
          };
        }

        return {
          label: this.humanizeKey(key),
          html: this.renderMarkdown(String(value ?? '')),
        };
      });
  }

  objectToNestedEntries(value: Record<string, unknown>): Array<{ label: string; html: string }> {
    return Object.entries(value)
      .filter(([, nestedValue]) => nestedValue !== null && nestedValue !== undefined && nestedValue !== '')
      .map(([nestedKey, nestedValue]) => ({
        label: this.humanizeKey(nestedKey),
        html: this.renderMarkdown(String(nestedValue ?? '')),
      }));
  }

  isPlainObject(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
  }

  private async maybeAutoLoadOnboardingProducts(): Promise<void> {
    if (this.onboardingProductsAutoLoading || this.onboardingProducts.length > 0) {
      return;
    }

    this.onboardingProductsAutoLoading = true;
    try {
      await this.ensureOnboardingProductsLoaded();
      if (this.onboardingProducts.length === 0 && !this.onboardingProductsAutoRetryDone) {
        this.onboardingProductsAutoRetryDone = true;
        await this.requestOnboardingJsonWriteAgain();
        await this.ensureOnboardingProductsLoaded();
      }
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.onboardingProductsAutoLoading = false;
    }
  }

  repoLinkLabel(url: string): string {
    try {
      const parsed = new URL(url);
      return `${parsed.host}${parsed.pathname}`;
    } catch {
      return url;
    }
  }

  renderMarkdown(value: string | null | undefined): string {
    const html = this.markdown.render(value ?? '');
    return html.replace(/<a\s/g, '<a target="_blank" rel="noopener noreferrer" ');
  }

  isJsonLine(text: string): boolean {
    const t = text.trim();
    if (t.length === 0 || (t[0] !== '{' && t[0] !== '[')) {
      return false;
    }
    try {
      JSON.parse(t);
      return true;
    } catch {
      return false;
    }
  }

  filteredAgentEvents(events: JobInfo['agent_events']): NonNullable<JobInfo['agent_events']> {
    return (events ?? []).filter((item) => !this.isJsonLine(item.text));
  }

  filteredJobLogs(logs: JobInfo['logs']): NonNullable<JobInfo['logs']> {
    return (logs ?? []).filter((log) => !this.isJsonLine(log.entry));
  }

  humanizeKey(key: string): string {
    return key
      .replace(/_/g, ' ')
      .replace(/\burl\b/gi, 'URL')
      .replace(/\s+/g, ' ')
      .replace(/^./, (match) => match.toUpperCase());
  }

  settingOptionParts(option: string): { value: string; title: string } {
    const [value, title] = option.split('|');
    return { value, title: title ?? value };
  }

  formatDate(value: string | null | undefined): string {
    if (!value) {
      return '-';
    }

    const timestamp = Date.parse(value);
    if (Number.isNaN(timestamp)) {
      return value;
    }

    return new Date(timestamp).toLocaleString();
  }

  asText(value: unknown): string {
    return String(value ?? '');
  }

  asError(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  private fixedAppIdForPlugin(plugin: PluginApp): string {
    const configuredAppId = String(plugin.app_id ?? '').trim();
    if (configuredAppId) {
      return configuredAppId;
    }
    return plugin.plugin_key;
  }

  resolvedNewAppId(): string {
    if (this.isSelectedPluginAppIdLocked) {
      const plugin = this.selectedPlugin;
      if (!plugin) {
        return '';
      }
      return this.fixedAppIdForPlugin(plugin);
    }
    return this.newAppId.trim();
  }

  getJobById(jobId: string): JobInfo | undefined {
    return this.jobs.find((j) => j.id === jobId);
  }

  get isSelectedPluginAppIdLocked(): boolean {
    return !this.isEditingApplication && !!this.selectedPlugin?.uniq;
  }

  get selectedPlugin(): PluginApp | undefined {
    return this.pluginApps.find((item) => item.plugin_key === this.selectedPluginKey);
  }

  get filteredPluginApps(): PluginApp[] {
    const availableApps = this.pluginApps.filter((pluginApp) => {
      if (!pluginApp.uniq) {
        return true;
      }

      return !this.applications.some((application) =>
        application.content['plugin_key'] === pluginApp.plugin_key
      );
    });

    const query = this.addAppSearchText.trim().toLowerCase();
    if (!query) {
      return availableApps;
    }

    return availableApps.filter((app) =>
      [app.name, app.plugin_key].some((value) => value.toLowerCase().includes(query))
    );
  }

  get selectedPluginForAdd(): PluginApp | undefined {
    return this.selectedPlugin;
  }

  isBuiltinApp(app: StoredDoc): boolean {
    return app.app_id === 'devops-pass-ai';
  }

  get isEditingApplication(): boolean {
    return this.editingApplicationDocId !== null;
  }

  get isEditingProduct(): boolean {
    return this.editingProductDocId !== null;
  }

  get filteredApplications(): StoredDoc[] {
    const text = this.appSearchText.trim().toLowerCase();
    if (!text) {
      return this.applications;
    }

    return this.applications.filter((doc) => JSON.stringify(doc.content).toLowerCase().includes(text));
  }

  get filteredProducts(): StoredDoc[] {
    const text = this.productSearchText.trim().toLowerCase();
    if (!text) {
      return this.products;
    }

    return this.products.filter((doc) => JSON.stringify(doc.content).toLowerCase().includes(text));
  }

  productCardDescription(doc: StoredDoc): string {
    const description = doc.content['description'];
    if (typeof description === 'string' && description.trim()) {
      return description;
    }
    const prompt = doc.content['prompt'];
    if (typeof prompt === 'string' && prompt.trim()) {
      return prompt;
    }
    return '';
  }

  productCardIcon(doc: StoredDoc): string {
    const icon = doc.content['icon'];
    if (typeof icon === 'string' && icon.trim()) {
      return icon.trim();
    }
    return '/assets/logo.png';
  }

  resourceLabel(resource: ProductResourceRef): string {
    return resource.name;
  }

  resourceIcon(resource: ProductResourceRef): string | undefined {
    return this.getDocTypeMeta(resource.doc_type).icon;
  }

  getResourceFactsStatus(resource: ProductResourceRef): { hasFacts: boolean; title: string } {
    const cached = this.productResourceDocsIndex[this.resourceKey(resource)];
    const hasFacts = cached?.hasFacts ?? false;
    return {
      hasFacts,
      title: hasFacts ? 'Facts available for this document' : 'No facts available for this document'
    };
  }

  private cacheResourceRef(doc: StoredDoc): void {
    const resource = this.docToResourceRef(doc);
    const key = this.resourceKey(resource);
    const hasFacts = doc.fact !== null && doc.fact !== undefined && doc.fact.trim() !== '';
    this.productResourceDocsIndex[key] = {
      app_id: resource.app_id,
      doc_type: resource.doc_type,
      name: resource.name,
      ...(resource.url ? { url: resource.url } : {}),
      hasFacts
    };
  }

  get filteredProductResourceResults(): StoredDoc[] {
    const selected = new Set(this.editProductResources.map((resource) => this.resourceKey(resource)));
    return this.productResourceResults.filter((doc) => !selected.has(this.resourceKey(this.docToResourceRef(doc))));
  }

  getDocTypeMeta(docType: string): { title: string; icon?: string; hiddenFields: string[] } {
    for (const app of this.pluginApps) {
      const match = app.doc_types.find((doc) => doc.key === docType);
      if (match) {
        const docIcon = (match as { icon?: string }).icon ?? app.icon;
        const hiddenFields = Array.isArray((match as { hidden_fields?: string[] }).hidden_fields)
          ? (match as { hidden_fields?: string[] }).hidden_fields ?? []
          : [];
        return {
          title: String((match as { title?: string }).title ?? docType),
          icon: docIcon,
          hiddenFields,
        };
      }
    }

    return { title: docType, hiddenFields: [] };
  }

  docDisplayEntries(doc: StoredDoc): Array<{ label: string; value: string }> {
    const meta = this.getDocTypeMeta(doc.doc_type);
    const hidden = new Set<string>([...meta.hiddenFields, 'plugin_key', 'name', 'icon', 'url']);
    const entries = Object.entries(doc.content)
      .filter(([key, value]) => !hidden.has(key) && value !== null && value !== undefined && value !== '')
      .map(([key, value]) => ({ label: this.humanizeKey(key), value: String(value) }));

    const urlIndex = entries.findIndex((entry) => entry.label.toLowerCase() === 'url');
    if (urlIndex > 0) {
      const [urlEntry] = entries.splice(urlIndex, 1);
      entries.unshift(urlEntry);
    }

    return entries;
  }

  docUrl(doc: StoredDoc): string | null {
    const url = doc.content['url'];
    if (typeof url !== 'string') {
      return null;
    }
    const trimmed = url.trim();
    return trimmed ? trimmed : null;
  }

  docDisplayName(doc: StoredDoc): string {
    const name = doc.content['name'];
    if (typeof name === 'string' && name.trim()) {
      return name;
    }
    return this.getDocTypeMeta(doc.doc_type).title;
  }

  appDocTypes(doc: StoredDoc): Array<{ key: string; title: string }> {
    const docTypes = doc.content['doc_types'];
    if (!Array.isArray(docTypes)) {
      return [];
    }

    return docTypes
      .map((item) => {
        if (typeof item === 'string') {
          return { key: item, title: this.humanizeKey(item) };
        }

        if (this.isPlainObject(item)) {
          const key = String(item['key'] ?? '').trim();
          if (!key) {
            return null;
          }
          const title = String(item['title'] ?? '').trim() || this.humanizeKey(key);
          return { key, title };
        }

        return null;
      })
      .filter((item): item is { key: string; title: string } => item !== null);
  }

  isAnyJobRunning(): boolean {
    return this.chatPendingUi || this.jobs.some((job) => job.status === 'running' || job.status === 'queued' || job.status === 'blocked');
  }

  canCancelJob(job: JobInfo): boolean {
    return !!job.can_cancel && (job.status === 'queued' || job.status === 'blocked' || job.status === 'running');
  }

  isStoppingJob(jobId: string): boolean {
    return this.stoppingJobIds.has(jobId);
  }

  async cancelJob(job: JobInfo, event?: Event): Promise<void> {
    event?.preventDefault();
    event?.stopPropagation();
    if (!this.canCancelJob(job) || this.stoppingJobIds.has(job.id)) {
      return;
    }

    this.error = '';
    this.stoppingJobIds.add(job.id);
    try {
      const updatedJob = await this.api.cancelJob(job.id);
      const jobIndex = this.jobs.findIndex((item) => item.id === updatedJob.id);
      if (jobIndex >= 0) {
        this.jobs = [
          ...this.jobs.slice(0, jobIndex),
          this.mergeJobWithListData(updatedJob, this.jobs[jobIndex]),
          ...this.jobs.slice(jobIndex + 1),
        ];
      }
      if (this.selectedJob?.id === updatedJob.id) {
        this.selectedJob = this.mergeJobWithListData(updatedJob, this.selectedJob);
        this.selectedJobSignature = this.jobSignature(this.selectedJob);
      }
      await this.pollJobs();
    } catch (error) {
      this.error = this.asError(error);
    } finally {
      this.stoppingJobIds.delete(job.id);
    }
  }

  jobStatusLabel(job: JobInfo): string {
    if (job.status === 'running') {
      return 'running';
    }
    if (job.status === 'blocked') {
      return 'blocked';
    }
    if (job.status === 'queued') {
      return 'queued';
    }
    if (job.status === 'success') {
      return 'ok';
    }
    if (job.status === 'cancelled') {
      return 'cancelled';
    }
    return 'failed';
  }

  private jobSignature(job: JobInfo): string {
    const logs = job.logs ?? [];
    const lastLog = logs.length > 0 ? logs[logs.length - 1] : null;

    return [
      job.id,
      job.status,
      job.started_at ?? '',
      job.finished_at ?? '',
      job.summary ?? '',
      job.failure ?? '',
      job.result?.['count'] ?? '',
      logs.length,
      lastLog?.timestamp ?? '',
      lastLog?.stream ?? '',
      lastLog?.entry ?? '',
    ].join('|');
  }

  private jobsListSignature(jobs: JobInfo[]): string {
    return jobs.map((job) => this.jobSignature(job)).join('||');
  }

  private hasActiveSelection(): boolean {
    const selection = window.getSelection();
    return !!selection && !selection.isCollapsed && selection.toString().trim().length > 0;
  }

  private asStringList(value: unknown): string[] {
    if (!Array.isArray(value)) {
      return [];
    }

    return value
      .map((item) => String(item ?? '').trim())
      .filter((item) => item.length > 0);
  }

  private asProductResources(value: unknown): ProductResourceRef[] {
    if (!Array.isArray(value)) {
      return [];
    }

    const resources: ProductResourceRef[] = [];
    for (const item of value) {
      if (!this.isPlainObject(item)) {
        continue;
      }

      const appId = String(item['app_id'] ?? '').trim();
      const docType = String(item['doc_type'] ?? '').trim();
      const name = String(item['name'] ?? '').trim();
      const url = String(item['url'] ?? '').trim();
      if (!appId || !docType || !name) {
        continue;
      }

      resources.push({
        app_id: appId,
        doc_type: docType,
        name,
        ...(url ? { url } : {}),
      });
    }

    return resources;
  }

  private indexResourceDocs(value: unknown): void {
    if (!Array.isArray(value)) {
      return;
    }

    for (const item of value) {
      if (!this.isPlainObject(item)) {
        continue;
      }

      const id = Number(item['id']);
      const appId = item['app_id'];
      const docType = item['doc_type'];
      const content = item['content'];
      const createdAt = item['created_at'];
      const updatedAt = item['updated_at'];
      const fact = item['fact'];
      if (!Number.isFinite(id) || typeof docType !== 'string' || !this.isPlainObject(content) || typeof createdAt !== 'string' || typeof updatedAt !== 'string') {
        continue;
      }

      const doc: StoredDoc = {
        id,
        app_id: typeof appId === 'string' ? appId : null,
        doc_type: docType,
        content,
        created_at: createdAt,
        updated_at: updatedAt,
        ...(typeof fact === 'string' ? { fact } : {}),
      };
      this.cacheResourceRef(doc);
    }
  }

  private docToResourceRef(doc: StoredDoc): ProductResourceRef {
    const name = this.docDisplayName(doc);
    const url = this.docUrl(doc);
    return {
      app_id: doc.app_id ?? '',
      doc_type: doc.doc_type,
      name,
      ...(url ? { url } : {}),
    };
  }

  private resourceKey(resource: ProductResourceRef): string {
    return [resource.app_id, resource.doc_type, resource.name, resource.url ?? '']
      .map((item) => item.toLowerCase())
      .join('|');
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

  private loadPreviewProductEnvironments(productDocId: number, productAppId: string | null): void {
    if (!productAppId) {
      this.previewProductEnvironments = [];
      this.previewProductEnvironmentsLoading = false;
      this.previewProductEnvironmentsQueryKey = null;
      return;
    }

    const queryKey = `${productDocId}|${productAppId}`;
    if (this.previewProductEnvironmentsQueryKey === queryKey) {
      return;
    }

    this.previewProductEnvironmentsQueryKey = queryKey;
    this.previewProductEnvironmentsLoading = true;
    void this.api.searchDocs({ doc_type: 'dop_env', app_id: productAppId })
      .then((response) => {
        if (this.selectedPreviewDoc?.id === productDocId && this.selectedPreviewDoc?.doc_type === 'dop_product') {
          this.previewProductEnvironments = response.results;
        }
      })
      .catch(() => {
        if (this.selectedPreviewDoc?.id === productDocId && this.selectedPreviewDoc?.doc_type === 'dop_product') {
          this.previewProductEnvironments = [];
        }
      })
      .finally(() => {
        if (this.selectedPreviewDoc?.id === productDocId && this.selectedPreviewDoc?.doc_type === 'dop_product') {
          this.previewProductEnvironmentsLoading = false;
        }
      });
  }

  jobSummaryText(job: JobInfo): string {
    return (job.summary ?? '').trim();
  }

  private isJobNewerThanLastSeen(job: JobInfo): boolean {
    return this.jobCompletedAt(job) > this.lastSeenCompletedJobAt;
  }

  private jobCompletedAt(job: JobInfo): number {
    const raw = job.finished_at ?? job.created_at;
    const timestamp = Date.parse(raw);
    return Number.isFinite(timestamp) ? timestamp : 0;
  }

  private updateLastSeenCompletedJobAt(job: JobInfo): void {
    const completedAt = this.jobCompletedAt(job);
    if (completedAt <= this.lastSeenCompletedJobAt) {
      return;
    }

    this.lastSeenCompletedJobAt = completedAt;
    this.saveLastSeenCompletedJobAt(completedAt);
  }

  private loadLastSeenCompletedJobAt(): number {
    try {
      const value = window.localStorage.getItem(this.jobsLastSeenStorageKey);
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : 0;
    } catch {
      return 0;
    }
  }

  private saveLastSeenCompletedJobAt(value: number): void {
    try {
      window.localStorage.setItem(this.jobsLastSeenStorageKey, String(value));
    } catch {
      return;
    }
  }

  private mergeJobWithListData(jobFromDetails: JobInfo, jobFromList: JobInfo): JobInfo {
    const summary = (jobFromDetails.summary ?? '').trim()
      ? jobFromDetails.summary
      : jobFromList.summary;

    return {
      ...jobFromDetails,
      summary,
    };
  }
}
