'use strict';
const $ = id => document.getElementById(id);
const uiText = value => window.CRBI18n?.t(value) ?? value;

let selected = null;
let current = null;
let source = null;
let edgeRedirecting = false;
let panelRestarting = false;
let panelRestartOriginal = null;
let panelRestartDialogEpoch = 0;
let lastStreamAuthProbe = 0;
let busy = false;
let refreshing = false;
let loginPending = false;
let hostRemovalEpoch = 0;
const hostRemovals = new Map();
let refreshWhileBusy = false;
let liteCatalogVersion = '';
let workspaceTitle = 'CodeRelayBridge';
let workspaceAppVersion = '';
function applyFeedbackMetadata(data) {
  if (!data || !Object.prototype.hasOwnProperty.call(data, 'feedbackUrl')) return;
  const link = $('bugFeedback');
  let destination = '';
  try {
    const url = new URL(data.feedbackUrl);
    if (url.origin === 'https://github.com' && !url.username && !url.password &&
        /^\/Gabriel0224\/CodeRelayBridge(?:-[A-Za-z0-9-]+)?\/issues\/new$/.test(url.pathname) &&
        url.searchParams.get('template') === 'bug_report.yml') {
      url.search = '';
      url.hash = '';
      url.searchParams.set('template', 'bug_report.yml');
      if (/^\d+\.\d+\.\d+$/.test(data.appVersion || '')) url.searchParams.set('version', data.appVersion);
      destination = url.href;
    }
  } catch (_) {}
  link.setAttribute('aria-disabled', String(!destination));
  link.tabIndex = destination ? 0 : -1;
  if (destination) link.href = destination;
  else link.removeAttribute('href');
  link.title = uiText(destination ? '在 GitHub 反馈问题（需登录；请勿包含隐私信息）' : '反馈入口暂不可用；请刷新面板');
}
$('bugFeedback').addEventListener('click', event => {
  if ($('bugFeedback').getAttribute('aria-disabled') === 'true') event.preventDefault();
});
let relayConnectionMessage = '正在连接…';
const threadSnapshots = new Map();
const threadFetches = new Map();
const THREAD_CACHE_MAX_ENTRIES = 8;
const THREAD_CACHE_MAX_BYTES = 6 * 1024 * 1024;
const THREAD_PREVIEW_MAX_AGE_MS = 10 * 60 * 1000;
const PRELOAD_HOURLY_BYTES = 512 * 1024;
const PRELOAD_REQUEST_RESERVE_BYTES = 68 * 1024;
let relayGeneration = '';
let threadSnapshotEpoch = 0;
let threadPreviewOnly = false;
let threadWatchPending = false;
const manualThreadRefreshes = new Set();
let preloadTimer = null;
let preloadController = null;
const preloadAttempts = new Map();
let preloadBudget = {started: Date.now(), used: 0};
let scheduledRefresh = null;
let refreshPending = false;
let catalog = [];
const readMarksInFlight = new Set();
const readMarkRetryAfter = new Map();
const readAcknowledgedSnapshots = new Map();
let threadReadAttention = '';
let threadReadSnapshot = null;
let visibleReadTimer = null;
let trafficRefreshTimer = null;
let selectedHostId = '';
let selectedApplicationId = 'codex';
const APPLICATION_LABELS = {codex: 'Codex', deepseek: 'DeepSeek Harness', claude: 'Claude Code'};
function applicationMessageLabel() { return applicationId(currentHost()) === 'codex' ? 'CODEX' : (APPLICATION_LABELS[applicationId(currentHost())] || applicationId(currentHost())); }
const WORKSPACE_DEVICE_ID = '__workspace__';
let workspaceMode = false;
let workspaceThreads = [];
let workspaceUpdateBusy = false;
let workspaceNavigation = {hostId: '', threadId: '', archiveFilter: 'all',
  search: '', catalogScrollTop: 0};
let projectFilter = 'all';
let archiveFilter = 'active';
let lastCatalog = '';
let rendered = new Map();
let threadRenderSignature = '';
let queueRenderSignature = '';
const queueCollapseOverrides = new Map();
let requestRenderSignature = '';
const requestBusy = new Set();
const choiceBusy = new Set();
let questionPointerInteraction = null;
let questionPointerReleaseTimer = null;
const questionInteractionRenders = new Map();
const questionDraftSessions = new Map();
let capacityCountdownTimer = null;
const submittedChoices = new Set();
let editingQueuedMessageId = '';
let initialSelectionDone = false;
let modelDirty = false;
let permissionDirty = false;
let pendingSpeedChange = null;
let pendingFiles = [];
let newThreadFiles = [];
const drafts = new Map();
const attachmentDrafts = new Map();
const filePreviewUrls = new Map();
const expandedGroups = new Set();
const expandedProcesses = new Set();
const expandedExecutionGroups = new Set();
const wrappedTextBlocks = new Set();
const assetCache = new Map();
const historyPages = new Map();
const RECENT_HISTORY_TURNS = 5;
const HISTORY_CACHE_MAX_BYTES = 12 * 1024 * 1024;
// Browser-only delivery feedback must never become an authoritative cached view.
const localOutgoingMessages = new Map();
let outgoingScope = '';
let outgoingDBPromise = null;
let outgoingJournalQueue = Promise.resolve();
let outgoingScopeEpoch = 0;
let composerDispatch = null;
const knownThreadStatuses = new Map();
let nativeThreadStatesInitialized = false;
const hostNavigation = new Map();
const projectOrderByHost = new Map();
const terminalSessions = new Map();
let terminalListTimer = null;
let terminalSyncPromise = null;
let terminalSessionRevision = 0;
const filePaths = new Map();
const FONT_SCALE_VERSION = 2;
const DEFAULT_LAYOUT = {sidebarWidth: 316, terminalWidth: 520,
  composerHeight: 224, fontSize: 13};
let layoutState = {...DEFAULT_LAYOUT};
let activeTerminalId = '';
let terminalPanelOpen = false;
let terminalPollActive = false;
let terminalSequence = 0;
let terminalContext = null;
let terminalView = 'terminal';
let fileBrowserToken = 0;
let fileBrowserState = null;
let fileContext = null;
let threadContext = null;
let projectContext = null;
let projectRootsDraft = null;
let fileDefaultContextKey = '';
const softwareReports = new Map();
const softwareBusyHosts = new Set();




let pendingSoftwareUpdate = null;
const softwareHostBusy = id => softwareBusyHosts.has(id);
let fileClipboard = null;
let filePasteToComposerBusy = false;
let fileCreateContext = null;
let fileDeleteContext = null;
let fileOperationBusy = false;
const fileTreeStates = new Map();
let fileTreeOpen = true;
const filePreviewTabs = [];
let activeFilePreviewId = '';
let filePreviewSequence = 0;
let filePreviewFontSize = (() => {
  try {
    const saved = Number(localStorage.getItem('codexRelayPreviewFontSize'));
    if (Number.isInteger(saved) && saved >= 5 && saved <= 32) return saved;
  } catch (_) {}
  return 13;
})();
let draggingProject = null;
let draggingWorkspaceThread = null;
let completionTarget = null;

const softwareNotices = new Map();
const softwareErrors = new Map();




const mobileQuery = window.matchMedia('(max-width: 760px)');
let sidebarCollapsed = (() => {
  try { return localStorage.getItem('codexRelaySidebarCollapsed') === '1'; }
  catch (_) { return false; }
})();
const touchKeyboardQuery = window.matchMedia('(pointer: coarse)');
const themeQuery = window.matchMedia('(prefers-color-scheme: light)');
let theme = (() => {
  try {
    const saved = localStorage.getItem('codexRelayTheme');
    if (saved === 'light' || saved === 'dark') return saved;
  } catch (_) {}
  return themeQuery.matches ? 'light' : 'dark';
})();
const PALETTE_NAMES = {green: '绿色', red: '红色', blue: '蓝色', yellow: '黄色'};
let palette = (() => {
  try {
    const saved = localStorage.getItem('codexRelayPalette');
    if (Object.hasOwn(PALETTE_NAMES, saved)) return saved;
  } catch (_) {}
  return 'green';
})();

function applyPalette(value, remember = false) {
  palette = Object.hasOwn(PALETTE_NAMES, value) ? value : 'green';
  document.documentElement.dataset.palette = palette;
  if (remember) {
    try { localStorage.setItem('codexRelayPalette', palette); } catch (_) {}
  }
  for (const option of document.querySelectorAll('[data-palette-option]')) {
    option.setAttribute('aria-checked', String(option.dataset.paletteOption === palette));
  }
  $('paletteToggle').title = `主题配色：${PALETTE_NAMES[palette]}`;
  syncTerminalThemes();
}

function applyTheme(value, remember = false) {
  theme = value === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  if (remember) {
    try { localStorage.setItem('codexRelayTheme', theme); } catch (_) {}
  }
  const toggle = $('themeToggle');
  if (toggle) {
    const light = theme === 'light';
    toggle.textContent = light ? '☾ 夜间' : '☀ 日间';
    toggle.setAttribute('aria-label', light ? '切换夜间主题' : '切换日间主题');
    toggle.title = light ? '切换夜间主题' : '切换日间主题';
  }
  syncTerminalThemes();
}

applyTheme(theme);
applyPalette(palette);

const markdown = window.markdownit ? window.markdownit({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false
}) : null;

function isLocalFileUrl(value) {
  return /^file:/i.test(value || '') || /^\//.test(value || '') ||
    /^[A-Za-z]:[\\/]/.test(value || '');
}

function fileCitationPath(value) {
  let path = String(value || '');
  if (/^file:/i.test(path)) {
    try {
      const url = new URL(path);
      if ((url.hostname && url.hostname !== 'localhost') || url.search || url.hash) return '';
      path = decodeURIComponent(url.pathname);
      if (/^\/[A-Za-z]:\//.test(path)) path = path.slice(1);
    } catch (_) { return ''; }
  }
  if (/[\u0000-\u001f\u007f]/.test(path) || path.startsWith('//')) return '';
  return /^\//.test(path) || /^[A-Za-z]:[\\/]/.test(path) ? path : '';
}

function enableFileCitations(parser) {
  // Inline parsing leaves fenced code, inline code and escaped directives literal.
  parser.inline.ruler.before('text', 'codex_file_citation', (state, silent) => {
    if (state.linkLevel > 0) return false;
    const start = state.pos;
    const prefix = /^:{1,2}codex-file-citation\s*\{/.exec(state.src.slice(start, state.posMax));
    if (!prefix) return false;
    const begin = start + prefix[0].length;
    let end = begin, quote = '';
    for (; end < Math.min(state.posMax, begin + 16384); end += 1) {
      const ch = state.src[end];
      if (quote) {
        if (ch === '\\') end += 1;
        else if (ch === quote) quote = '';
      } else if (ch === '"' || ch === "'") quote = ch;
      else if (ch === '}') break;
    }
    if (state.src[end] !== '}' || quote) return false;
    const attributes = state.src.slice(begin, end);
    const rule = /\s*([A-Za-z][\w-]*)\s*=\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/gy;
    const values = Object.create(null);
    let offset = 0;
    while (offset < attributes.length) {
      if (!attributes.slice(offset).trim()) break;
      rule.lastIndex = offset;
      const match = rule.exec(attributes);
      if (!match || Object.hasOwn(values, match[1])) return false;
      values[match[1]] = match[2].slice(1, -1).replace(/\\(["'\\])/g, '$1');
      offset = rule.lastIndex;
    }
    const path = fileCitationPath(values.path);
    if (!path) return false;
    if (!silent) {
      const token = state.push('codex_file_citation', '', 0);
      token.content = path.replaceAll('\\', '/').split('/').at(-1) || path;
      token.meta = {path, label: values.label || values.title || token.content};
    }
    state.pos = end + 1;
    return true;
  });
  parser.renderer.rules.codex_file_citation = (tokens, index) => {
    const token = tokens[index], escape = parser.utils.escapeHtml;
    return '<a href="#" class="local-file-link" data-file-citation="true" ' +
      'data-local-source="' + escape(token.meta.path) + '" title="点击预览文件">' +
      escape(token.meta.label) + '</a>';
  };
}

function enableLatexDelimiters(parser, katex) {
  parser.inline.ruler.before('escape', 'latex_delimiters', (state, silent) => {
    const opener = state.src.slice(state.pos, state.pos + 2);
    if (opener !== '\\(' && opener !== '\\[') return false;
    const closer = opener === '\\(' ? '\\)' : '\\]';
    const end = state.src.indexOf(closer, state.pos + 2);
    if (end < 0 || end + 2 > state.posMax) return false;
    if (!silent) {
      const token = state.push('latex_delimiters', '', 0);
      token.content = state.src.slice(state.pos + 2, end);
      token.meta = {displayMode: opener === '\\['};
    }
    state.pos = end + 2;
    return true;
  });
  parser.renderer.rules.latex_delimiters = (tokens, index) => {
    const token = tokens[index];
    try {
      return katex.renderToString(token.content, {
        displayMode: token.meta.displayMode, throwOnError: true,
        strict: 'ignore', trust: false
      });
    } catch (_) {
      const delimiter = token.meta.displayMode ? ['\\[', '\\]'] : ['\\(', '\\)'];
      return parser.utils.escapeHtml(delimiter[0] + token.content + delimiter[1]);
    }
  };
}

if (markdown) {
  if (window.katex) enableLatexDelimiters(markdown, window.katex);
  enableFileCitations(markdown);
  markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
    const token = tokens[index];
    const href = token.attrGet('href') || '';
    if (isLocalFileUrl(href)) {
      token.attrSet('href', '#');
      token.attrJoin('class', 'local-file-link');
      token.attrSet('data-local-source', href);
      token.attrSet('title', '点击预览文件');
    } else {
      token.attrSet('target', '_blank');
      token.attrSet('rel', 'noopener noreferrer');
    }
    return renderer.renderToken(tokens, index, options);
  };
  const defaultImage = markdown.renderer.rules.image || ((tokens, index, options, env, renderer) =>
    renderer.renderToken(tokens, index, options));
  markdown.renderer.rules.image = (tokens, index, options, env, renderer) => {
    const src = tokens[index].attrGet('src') || '';
    if (env?.filePreview && !/^(https?:|data:|blob:)/i.test(src)) {
      const safe = markdown.utils.escapeHtml(src);
      return `<span class="file-preview-image-pending" data-preview-src="${safe}">正在载入图片…</span>`;
    }
    if (isLocalFileUrl(src)) {
      return '<span class="local-image-note">图片见下方附件</span>';
    }
    tokens[index].attrSet('loading', 'lazy');
    tokens[index].attrSet('referrerpolicy', 'no-referrer');
    return defaultImage(tokens, index, options, env, renderer);
  };
}

try {
  for (const key of JSON.parse(localStorage.getItem('codexRelayExpandedProjects') || '[]')) {
    if (typeof key === 'string') expandedGroups.add(key);
  }
} catch (_) {
  localStorage.removeItem('codexRelayExpandedProjects');
}

// Read/unread now follows Codex Desktop's account-scoped native state.
try { localStorage.removeItem('codexRelayViewedCompletions'); } catch (_) {}

try {
  const saved = JSON.parse(localStorage.getItem('codexRelayHostNavigation') || '{}');
  if (typeof saved.lastHostId === 'string') selectedHostId = saved.lastHostId;
  if (typeof saved.lastApplicationId === 'string') selectedApplicationId = saved.lastApplicationId;
  workspaceMode = saved.workspaceMode === true;
  const workspace = saved.workspace;
  if (workspace && typeof workspace === 'object' && !Array.isArray(workspace)) {
    workspaceNavigation = {
      hostId: typeof workspace.hostId === 'string' ? workspace.hostId : '',
      threadId: typeof workspace.threadId === 'string' ? workspace.threadId : '',
      archiveFilter: ['active', 'archived', 'all'].includes(workspace.archiveFilter) ?
        workspace.archiveFilter : 'all',
      search: typeof workspace.search === 'string' ? workspace.search : '',
      catalogScrollTop: Number.isFinite(workspace.catalogScrollTop) && workspace.catalogScrollTop >= 0 ?
        workspace.catalogScrollTop : 0
    };
  }
  if (saved.hosts && typeof saved.hosts === 'object' && !Array.isArray(saved.hosts)) {
    for (const [hostId, state] of Object.entries(saved.hosts)) {
      if (!hostId || !state || typeof state !== 'object' || Array.isArray(state)) continue;
      hostNavigation.set(hostId, {
        threadId: typeof state.threadId === 'string' ? state.threadId : '',
        projectFilter: typeof state.projectFilter === 'string' ? state.projectFilter : 'all',
        archiveFilter: ['active', 'archived', 'all'].includes(state.archiveFilter) ?
          state.archiveFilter : 'active',
        search: typeof state.search === 'string' ? state.search : '',
        catalogScrollTop: Number.isFinite(state.catalogScrollTop) && state.catalogScrollTop >= 0 ?
          state.catalogScrollTop : 0
      });
    }
  }
} catch (_) {
  localStorage.removeItem('codexRelayHostNavigation');
}

try {
  const saved = JSON.parse(localStorage.getItem('codexRelayProjectOrder') || '{}');
  if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
    for (const [hostId, order] of Object.entries(saved)) {
      if (Array.isArray(order)) {
        projectOrderByHost.set(hostId, order.filter(value => typeof value === 'string'));
      }
    }
  }
} catch (_) {
  localStorage.removeItem('codexRelayProjectOrder');
}

try {
  const saved = JSON.parse(localStorage.getItem('codexRelayLayout') || '{}');
  const savedFontSize = Number.isFinite(saved.fontSize) ? saved.fontSize :
    DEFAULT_LAYOUT.fontSize;
  layoutState = {
    sidebarWidth: Number.isFinite(saved.sidebarWidth) ? saved.sidebarWidth : DEFAULT_LAYOUT.sidebarWidth,
    terminalWidth: Number.isFinite(saved.terminalWidth) ? saved.terminalWidth : DEFAULT_LAYOUT.terminalWidth,
    composerHeight: Number.isFinite(saved.composerHeight) ? saved.composerHeight : DEFAULT_LAYOUT.composerHeight,
    fontSize: saved.fontScaleVersion === FONT_SCALE_VERSION ? savedFontSize :
      Math.max(savedFontSize, DEFAULT_LAYOUT.fontSize)
  };
} catch (_) {
  localStorage.removeItem('codexRelayLayout');
}

function groupKey(hostId, projectId) {
  return `${hostId}/${projectId || 'unassigned'}`;
}

function rememberExpandedGroups() {
  localStorage.setItem('codexRelayExpandedProjects', JSON.stringify([...expandedGroups]));
}

function setGroupExpanded(hostId, projectId, expanded) {
  const key = groupKey(hostId, projectId);
  if (expanded) expandedGroups.add(key);
  else expandedGroups.delete(key);
  rememberExpandedGroups();
}

function rememberHostNavigation() {
  try {
    localStorage.setItem('codexRelayHostNavigation', JSON.stringify({
      version: 1,
      lastHostId: selectedHostId,
      lastApplicationId: selectedApplicationId,
      workspaceMode,
      workspace: workspaceNavigation,
      hosts: Object.fromEntries(hostNavigation)
    }));
  } catch (_) {}
}

function captureHostNavigation(hostId = selectedHostId) {
  if (workspaceMode) {
    workspaceNavigation = {...workspaceNavigation,
      hostId: selected?.host || workspaceNavigation.hostId,
      threadId: selected?.thread || workspaceNavigation.threadId,
      archiveFilter, search: $('search')?.value || '',
      catalogScrollTop: $('catalog')?.scrollTop || 0};
    rememberHostNavigation();
    return;
  }
  if (!hostId) return;
  const previous = hostNavigation.get(hostId) || {};
  const currentHostVisible = hostId === selectedHostId;
  hostNavigation.set(hostId, {
    threadId: selected?.host === hostId ? selected.thread : previous.threadId || '',
    projectFilter: currentHostVisible ? projectFilter : previous.projectFilter || 'all',
    archiveFilter: currentHostVisible ? archiveFilter : previous.archiveFilter || 'active',
    search: currentHostVisible ? ($('search')?.value || '') : previous.search || '',
    catalogScrollTop: currentHostVisible ? ($('catalog')?.scrollTop || 0) :
      previous.catalogScrollTop || 0
  });
  rememberHostNavigation();
}

function applyHostNavigation(host) {
  const state = hostNavigation.get(host.id) || {};
  const projectIds = new Set([
    'all', 'unassigned', ...(host.projects || []).map(project => project.id)
  ]);
  projectFilter = projectIds.has(state.projectFilter) ? state.projectFilter : 'all';
  archiveFilter = ['active', 'archived', 'all'].includes(state.archiveFilter) ?
    state.archiveFilter : 'active';
  $('search').value = typeof state.search === 'string' ? state.search : '';
}

function revealThreadInFilters(thread) {
  const threadProject = thread.projectId || 'unassigned';
  if (projectFilter !== 'all' && projectFilter !== threadProject) {
    projectFilter = threadProject;
  }
  if (archiveFilter === 'active' && thread.archived) archiveFilter = 'archived';
  if (archiveFilter === 'archived' && !thread.archived) archiveFilter = 'active';
  const query = $('search').value.trim().toLowerCase();
  if (query && !`${thread.title} ${thread.cwd} ${thread.projectName} ${thread.model}`
      .toLowerCase().includes(query)) {
    $('search').value = '';
  }
}

function threadMatchesNavigation(thread) {
  if (archiveFilter === 'active' && thread.archived) return false;
  if (archiveFilter === 'archived' && !thread.archived) return false;
  const threadProject = thread.projectId || 'unassigned';
  if (projectFilter !== 'all' && projectFilter !== threadProject) return false;
  const query = $('search').value.trim().toLowerCase();
  return !query || `${thread.title} ${thread.cwd} ${thread.projectName} ${thread.model}`
    .toLowerCase().includes(query);
}

function rememberedThread(host) {
  const threadId = hostNavigation.get(host.id)?.threadId;
  return host.threads.find(thread => thread.id === threadId) ||
    host.threads.find(threadMatchesNavigation) ||
    host.threads.find(thread => !thread.archived) || host.threads[0] || null;
}

function restoreCatalogScroll(hostId) {
  if (workspaceMode) {
    $('catalog').scrollTop = workspaceNavigation.catalogScrollTop || 0;
    return;
  }
  if (!hostId || selectedHostId !== hostId) return;
  const top = hostNavigation.get(hostId)?.catalogScrollTop;
  if (Number.isFinite(top)) $('catalog').scrollTop = top;
}

function rememberProjectOrder() {
  try {
    localStorage.setItem('codexRelayProjectOrder',
      JSON.stringify(Object.fromEntries(projectOrderByHost)));
  } catch (_) {}
}

function orderedProjectGroups(host, groups) {
  const known = new Set(groups.map(group => group.id));
  const saved = (projectOrderByHost.get(host.id) || []).filter(id => known.has(id));
  for (const group of groups) {
    if (!saved.includes(group.id)) saved.push(group.id);
  }
  projectOrderByHost.set(host.id, saved);
  return [...groups].sort((left, right) => saved.indexOf(left.id) - saved.indexOf(right.id));
}

function moveProjectBefore(hostId, sourceId, targetId, after = false) {
  if (!sourceId || !targetId || sourceId === targetId) return;
  const host = catalog.find(item => item.id === hostId);
  if (!host) return;
  const ids = orderedProjectGroups(host, [
    ...(host.projects || []).map(project => ({id: project.id, name: project.name})),
    {id: 'unassigned', name: '未分类'}
  ]).map(group => group.id).filter(id => id !== sourceId);
  const index = ids.indexOf(targetId);
  const destination = index < 0 ? ids.length : index + (after ? 1 : 0);
  ids.splice(destination, 0, sourceId);
  projectOrderByHost.set(hostId, ids);
  rememberProjectOrder();
  lastCatalog = '';
  renderCatalog();
}

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

function rememberLayout() {
  try {
    localStorage.setItem('codexRelayLayout', JSON.stringify({
      ...layoutState, fontScaleVersion: FONT_SCALE_VERSION}));
  } catch (_) {}
}

function applyLayout() {
  const root = document.documentElement;
  const viewportWidth = Math.max(window.innerWidth, 320);
  const viewportHeight = Math.max(window.innerHeight, 480);
  layoutState.sidebarWidth = clamp(layoutState.sidebarWidth, 230,
    Math.max(230, Math.min(520, viewportWidth - 360)));
  layoutState.terminalWidth = clamp(layoutState.terminalWidth, 300,
    Math.max(300, Math.min(920, viewportWidth - 360)));
  layoutState.composerHeight = clamp(layoutState.composerHeight, 150,
    Math.min(620, viewportHeight - 220));
  layoutState.fontSize = clamp(layoutState.fontSize, 5, 32);
  root.style.setProperty('--sidebar-width', `${layoutState.sidebarWidth}px`);
  root.style.setProperty('--terminal-width', `${layoutState.terminalWidth}px`);
  root.style.setProperty('--composer-height', `${layoutState.composerHeight}px`);
  root.style.setProperty('--content-font-size', `${layoutState.fontSize}px`);
  root.style.setProperty('--ui-font-scale', String(layoutState.fontSize / DEFAULT_LAYOUT.fontSize));
  if ($('fontSize')) $('fontSize').value = String(layoutState.fontSize);
  if ($('fontSizeValue')) $('fontSizeValue').textContent = String(layoutState.fontSize);
  if ($('composerFontSize')) $('composerFontSize').textContent = String(layoutState.fontSize);
  if ($('fontDecrease')) $('fontDecrease').disabled = layoutState.fontSize <= 5;
  if ($('fontIncrease')) $('fontIncrease').disabled = layoutState.fontSize >= 32;
  for (const session of terminalSessions.values()) {
    if (session.term) session.term.options.fontSize = layoutState.fontSize;
  }
  fitActiveTerminal();
  positionFontMenu();
}

function resetLayout() {
  layoutState = {...DEFAULT_LAYOUT};
  rememberLayout();
  applyLayout();
}

function installPaneResizer(element, axis, getSize, setSize, direction = 1) {
  element.addEventListener('pointerdown', event => {
    if (event.button !== 0 || mobileQuery.matches) return;
    event.preventDefault();
    const start = axis === 'x' ? event.clientX : event.clientY;
    const initial = getSize();
    element.classList.add('resizing');
    document.body.classList.add('resizing-panes');
    if (axis === 'y') document.body.classList.add('resizing-rows');
    const move = moveEvent => {
      const current = axis === 'x' ? moveEvent.clientX : moveEvent.clientY;
      setSize(initial + direction * (current - start));
      applyLayout();
    };
    const finish = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', finish);
      element.classList.remove('resizing');
      document.body.classList.remove('resizing-panes', 'resizing-rows');
      rememberLayout();
      fitActiveTerminal();
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', finish, {once: true});
  });
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const h = Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}

function bytesToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function pendingFileKey(file) {
  if (file.remoteUploadId) return `remote:${file.remoteUploadId}`;
  if (file.remoteSourcePath) return `source:${file.hostId}:${file.remoteSourcePath}`;
  return `${file.name}\0${file.size}\0${file.lastModified}`;
}

function renderUploadTray() {
  const tray = $('uploadTray');
  tray.replaceChildren();
  tray.hidden = pendingFiles.length === 0;
  pendingFiles.forEach((file, index) => {
    const card = document.createElement('div');
    card.className = 'pending-upload';
    const preview = document.createElement('div');
    preview.className = 'pending-upload-preview';
    if (!file.remoteUploadId && !file.remoteSourcePath && file.type.startsWith('image/')) {
      let url = filePreviewUrls.get(file);
      if (!url) {
        url = URL.createObjectURL(file);
        filePreviewUrls.set(file, url);
      }
      const image = document.createElement('img');
      image.src = url;
      image.alt = '';
      preview.append(image);
    } else {
      preview.textContent = file.remoteUploadId || file.remoteSourcePath ? '主机' : 'FILE';
    }
    const info = document.createElement('div');
    info.className = 'pending-upload-info';
    const name = document.createElement('strong');
    name.textContent = file.name;
    const size = document.createElement('span');
    size.textContent = file.remoteSourcePath ? '目标机文件' :
      `${formatBytes(file.size)}${file.remoteUploadId ? ' · 目标机文件' : ''}`;
    info.append(name, size);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'pending-upload-remove';
    remove.textContent = '×';
    remove.title = `移除 ${file.name}`;
    remove.setAttribute('aria-label', `移除 ${file.name}`);
    remove.disabled = busy;
    remove.onclick = () => {
      const removed = pendingFiles[index];
      const url = filePreviewUrls.get(removed);
      if (url) URL.revokeObjectURL(url);
      filePreviewUrls.delete(removed);
      pendingFiles.splice(index, 1);
      if (selected) attachmentDrafts.set(historyKey(), [...pendingFiles]);
      renderUploadTray();
    };
    card.append(preview, info, remove);
    tray.append(card);
  });
  syncSendButton();
}

function syncSendButton() {
  const hasInput = Boolean($('message').value.trim() || pendingFiles.length);
  const running = current?.status === 'active';
  const capacity = current?.capacityRetry;
  const capacityContinue = capacity && supportsAction(currentHost(), 'continue_turn') && !hasInput;
  $('send').textContent = capacity?.pending ? '正在重试…' : capacityContinue ? '继续 ↗' : running ? '加入队列 ↑' :
    current?.canContinue && supportsAction(currentHost(), 'continue_turn') && !hasInput ? '继续 ↗' : '发送 ↑';
  if (capacity?.pending || (capacityContinue && !current?.canContinue)) $('send').disabled = true;
  syncCapacityRetryStatus();
}

function syncCapacityRetryStatus() {
  clearInterval(capacityCountdownTimer);
  capacityCountdownTimer = null;
  const caption = $('nativeRetryStatus');
  const capacity = current?.capacityRetry;
  caption.hidden = !capacity || !current?.online;
  if (caption.hidden) {caption.textContent = ''; return;}
  const paint = () => {
    if (capacity.pending) caption.textContent = '原 App 正在重试…';
    else if (capacity.countdownAvailable && capacity.automatic && Number.isFinite(capacity.retryAtMs)) {
      const seconds = Math.max(0, Math.ceil((capacity.retryAtMs - Date.now()) / 1000));
      caption.textContent = seconds ? `原 App 将在 ${seconds} 秒后自动重试` : '等待原 App 的重试状态…';
      if (!seconds) {clearInterval(capacityCountdownTimer); capacityCountdownTimer = null;}
    } else caption.textContent = capacity.automatic === false ? '模型满载，可继续或更换模型' :
      '模型满载 · 原 App 倒计时尚未同步';
  };
  paint();
  if (!document.hidden && capacity.countdownAvailable && capacity.automatic &&
      Number.isFinite(capacity.retryAtMs) && capacity.retryAtMs > Date.now()) {
    // Visual countdown only. ZERO commands, retries, network reads or resends.
    capacityCountdownTimer = setInterval(paint, 1000);
  }
}

async function runQueueAction(action, payload, successText) {
  if (busy || !selected || !currentHost()?.online || selectedThread()?.archived) return;
  const target = {...selected};
  busy = true;
  $('sendResult').className = '';
  $('sendResult').textContent = '正在同步待发送队列…';
  queueRenderSignature = '';
  renderQueuedMessages(current?.queuedMessages || [], current?.status === 'active',
    Boolean(current?.owned && current?.online));
  try {
    if (!current?.owned || !current?.online || current?.source !== 'desktop-live') {
      $('sendResult').textContent = '正在接入原桌面会话…';
      await issueCommand('attach_thread', target);
      await refreshThread();
    }
    const result = await issueCommand(action, {...target, payload});
    editingQueuedMessageId = '';
    queueRenderSignature = '';
    $('sendResult').textContent = action === 'queue_resume' && result.pending ? '已请求继续队列，等待原 App 确认' :
      action === 'queue_resume' && !result.resumed ? '队列当前未暂停' :
      action === 'queue_send_now' ? (result.behavior === 'start' ? '✓ 已立即发送' : '✓ 已介入当前任务') : successText;
    await refresh();
  } catch (error) {
    $('sendResult').textContent = error.message;
    $('sendResult').className = 'error';
  } finally {
    busy = false;
    if (selected?.host === target.host && selected?.thread === target.thread) {
      await refreshThread();
    }
  }
}

function renderQueuedMessages(messages, running, operable) {
  const values = Array.isArray(messages) ? messages : [];
  const interrupted = currentHost()?.applicationId !== 'deepseek' &&
    values.some(value => value.pausedReason === 'Interrupted before the steer was accepted.');
  const canResume = currentHost()?.applicationId !== 'deepseek' && currentHost()?.capabilities?.queueResume === true &&
    supportsAction(currentHost(), 'queue_resume');
  const queueKey = selected ? historyKey() : '';
  const collapsed = queueCollapseOverrides.has(queueKey) ?
    queueCollapseOverrides.get(queueKey) : values.length > 1;
  const signature = JSON.stringify([busy, operable, running, canResume, values.map(value =>
    [value.id, value.text, value.pausedReason || '', value.submission?.status, (value.attachments || []).map(item =>
      [item.name, item.size ?? null])]), collapsed, editingQueuedMessageId]);
  if (signature === queueRenderSignature &&
      (!editingQueuedMessageId || values.some(value => value.id === editingQueuedMessageId))) return;
  queueRenderSignature = signature;
  const tray = $('queuedMessages');
  tray.replaceChildren();
  tray.hidden = values.length === 0;
  tray.classList.toggle('collapsed', collapsed);
  if (!values.length) {
    editingQueuedMessageId = '';
    return;
  }
  const header = document.createElement('div');
  header.className = 'queue-header';
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'queue-toggle';
  toggle.setAttribute('aria-expanded', String(!collapsed));
  toggle.setAttribute('aria-label', `${collapsed ? '展开' : '折叠'}待发送消息（${values.length} 条）`);
  toggle.disabled = Boolean(editingQueuedMessageId);
  toggle.textContent = collapsed ? '▸' : '▾';
  toggle.onclick = () => {
    queueCollapseOverrides.set(queueKey, !collapsed);
    queueRenderSignature = '';
    renderQueuedMessages(values, running, operable);
  };
  const heading = document.createElement('strong');
  heading.textContent = `待发送 · ${values.length}`;
  toggle.append(heading);
  const hint = document.createElement('span');
  hint.textContent = interrupted ? '队列已暂停' : running ? '本轮结束后按顺序发送' : '等待桌面 App 接收';
  header.append(toggle, hint);
  tray.append(header);
  if (interrupted) {
    const banner = document.createElement('div');
    banner.className = 'queue-resume-banner';
    banner.setAttribute('role', 'status');
    const explanation = document.createElement('span');
    explanation.textContent = '由于你中断了当前响应，队列已暂停';
    const resume = document.createElement('button');
    resume.type = 'button';
    resume.className = 'secondary queue-action queue-resume';
    resume.textContent = '继续';
    resume.disabled = busy || !operable || running || !canResume || Boolean(editingQueuedMessageId);
    if (!canResume) resume.title = '请更新目标客户端以支持继续队列';
    resume.onclick = () => runQueueAction('queue_resume', {messageIds: values.map(value => value.id)}, '✓ 队列已继续');
    banner.append(explanation, resume);
    tray.append(banner);
  }
  if (collapsed) return;

  values.forEach((message, index) => {
    const card = document.createElement('article');
    card.className = 'queued-message';
    card.dataset.messageId = message.id;
    const number = document.createElement('span');
    number.className = 'queue-number';
    number.textContent = String(index + 1);
    const content = document.createElement('div');
    content.className = 'queue-content';
    const editing = editingQueuedMessageId === message.id;
    if (editing) {
      const editor = document.createElement('textarea');
      editor.className = 'queue-editor';
      editor.maxLength = 32000;
      editor.value = message.text || '';
      editor.rows = 2;
      const editorActions = document.createElement('div');
      editorActions.className = 'queue-editor-actions';
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'quiet';
      cancel.textContent = '取消';
      cancel.onclick = () => {
        editingQueuedMessageId = '';
        queueRenderSignature = '';
        renderQueuedMessages(values, running, operable);
      };
      const save = document.createElement('button');
      save.type = 'button';
      save.textContent = '保存';
      save.onclick = () => {
        const text = editor.value.trim();
        if (!text && !(message.attachments || []).length) {
          $('sendResult').className = 'error';
          $('sendResult').textContent = '队列消息不能同时没有文字和附件';
          return;
        }
        runQueueAction('queue_edit', {messageId: message.id, text}, '✓ 队列消息已更新');
      };
      editorActions.append(cancel, save);
      content.append(editor, editorActions);
      setTimeout(() => editor.focus(), 0);
    } else {
      const text = document.createElement('div');
      text.className = 'queue-text';
      text.textContent = message.text || (message.attachments || []).length ?
        (message.text || '仅附件') : '空消息';
      content.append(text);
      if ((message.attachments || []).length) {
        const files = document.createElement('div');
        files.className = 'queue-attachments';
        for (const attachment of message.attachments) {
          const file = document.createElement('span');
          file.textContent = `${attachment.kind === 'image' ? '图片' : '文件'} · ${attachment.name}`;
          file.title = attachment.size == null ? attachment.name :
            `${attachment.name} · ${formatBytes(attachment.size)}`;
          files.append(file);
        }
        content.append(files);
      }
      if (message.pausedReason && message.pausedReason !== 'Interrupted before the steer was accepted.') {
        const paused = document.createElement('div');
        paused.className = 'queue-paused';
        paused.textContent = `已暂停：${message.pausedReason}`;
        content.append(paused);
      }
    }
    const actions = document.createElement('div');
    actions.className = 'queue-actions';
    if (!editing) {
      if (values.length > 1 && supportsAction(currentHost(), 'queue_reorder')) {
        for (const [label, delta] of [['↑', -1], ['↓', 1]]) {
          const move = document.createElement('button');
          move.type = 'button';
          move.className = 'quiet queue-move';
          move.textContent = label;
          move.title = delta < 0 ? '向前移动' : '向后移动';
          move.disabled = busy || !operable || index + delta < 0 || index + delta >= values.length;
          move.onclick = () => {
            const ids = values.map(value => value.id);
            [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
            runQueueAction('queue_reorder', {messageIds: ids}, '✓ 队列顺序已更新');
          };
          actions.append(move);
        }
      }
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'secondary queue-action queue-edit';
      edit.textContent = '编辑';
      edit.disabled = busy || !operable;
      edit.onclick = () => {
        editingQueuedMessageId = message.id;
        queueCollapseOverrides.set(queueKey, false);
        queueRenderSignature = '';
        renderQueuedMessages(values, running, operable);
      };
      const steer = document.createElement('button');
      steer.type = 'button';
      steer.className = 'secondary queue-action queue-steer';
      steer.textContent = running ? '引导' : '立即发送';
      steer.disabled = busy || !operable;
      steer.onclick = () => runQueueAction('queue_send_now',
        {messageId: message.id}, running ? '✓ 已介入当前任务' : '✓ 已立即发送');
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'secondary queue-action queue-delete danger-text';
      remove.textContent = '删除';
      remove.disabled = busy || !operable;
      remove.onclick = () => runQueueAction('queue_delete',
        {messageId: message.id}, '✓ 队列消息已删除');
      edit.hidden = !supportsAction(currentHost(), 'queue_edit');
      steer.hidden = !supportsAction(currentHost(), 'queue_send_now');
      remove.hidden = !supportsAction(currentHost(), 'queue_delete');
      actions.append(edit, steer, remove);
    }
    card.append(number, content, actions);
    tray.append(card);
  });
}

function setPendingFiles(files) {
  pendingFiles = [...files];
  if (selected) attachmentDrafts.set(historyKey(), [...pendingFiles]);
  renderUploadTray();
}

function syncCopiedFileButton() {
  const button = $('pasteCopiedFile');
  button.hidden = !fileClipboard || fileClipboard.isDirectory || !supportsAction(currentHost(), 'attach_local_file');
  if (button.hidden) return;
  const sameHost = physicalHostId() === fileClipboard.hostId;
  button.disabled = !sameHost || $('addAttachment').disabled || busy ||
    filePasteToComposerBusy;
  button.title = sameHost ? `将 ${fileClipboard.name} 加入当前对话` :
    '请先打开该文件所在电脑的对话';
}

function clipboardFiles(data) {
  const found = new Map();
  for (const file of data?.files || []) {
    if (file) found.set(pendingFileKey(file), file);
  }
  for (const item of data?.items || []) {
    if (item.kind !== 'file') continue;
    const file = item.getAsFile?.();
    if (file) found.set(pendingFileKey(file), file);
  }
  return [...found.values()];
}

async function uploadFile(target, file, fileIndex, fileCount, progress) {
  const report = text => progress ? progress(text) : ($('sendResult').textContent = text);
  if (file.remoteSourcePath) {
    if (physicalHostId(catalog.find(host => host.id === target.host)) !== file.hostId) throw new Error('目标机文件不属于当前电脑');
    report(`正在添加 ${fileIndex + 1}/${fileCount}：${file.name}`);
    const result = await issueCommand('attach_local_file', {
      ...target, payload: {path: file.remoteSourcePath}
    });
    return {uploadId: result.uploadId, name: result.name,
      mime: result.mime, size: result.size};
  }
  if (file.remoteUploadId) {
    if (target.host !== file.hostId || target.thread !== file.threadId) {
      throw new Error('目标机文件附件不属于当前对话');
    }
    return {uploadId: file.remoteUploadId, name: file.name,
      mime: file.type, size: file.size};
  }
  const uploadId = uuid();
  const chunkSize = 512 * 1024;
  let offset = 0;
  let result = null;
  do {
    const end = Math.min(file.size, offset + chunkSize);
    const data = bytesToBase64(await file.slice(offset, end).arrayBuffer());
    const percent = file.size ? Math.round(end / file.size * 100) : 100;
    report(`正在上传 ${fileIndex + 1}/${fileCount}：${file.name} · ${percent}%`);
    result = await issueCommand('upload_asset_chunk', {
      ...target,
      payload: {uploadId, name: file.name, mime: file.type,
        size: file.size, offset, eof: end === file.size, data}
    });
    offset = end;
  } while (offset < file.size || (file.size === 0 && !result));
  return {uploadId: result.uploadId, name: result.name,
    mime: result.mime, size: result.size};
}

async function copyPlainText(text, button) {
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(String(text || ''));
    else throw new Error('clipboard unavailable');
  } catch (_) {
    const input = document.createElement('textarea');
    input.value = String(text || '');
    input.style.position = 'fixed';
    input.style.opacity = '0';
    document.body.append(input);
    input.select();
    document.execCommand('copy');
    input.remove();
  }
  const before = button.textContent;
  button.textContent = '已复制';
  setTimeout(() => {button.textContent = before;}, 1200);
}

async function copyMessageText(item, button) {
  const attachmentNames = (item.attachments || []).map(value => value.label || value.name);
  const text = [item.text || '', ...attachmentNames].filter(Boolean).join('\n');
  await copyPlainText(text, button);
}

async function api(path, data, options = {}) {
  if (panelRestarting && !path.startsWith('/api/panel/')) {
    throw new Error('面板正在完整重启，请等待自动重新连接');
  }
  const response = await fetch(path, {
    credentials: 'same-origin',
    method: data ? 'POST' : 'GET',
    headers: data ? {'Accept': 'application/json', 'Content-Type': 'application/json'} :
      {'Accept': 'application/json'},
    body: data ? JSON.stringify(data) : undefined,
    signal: options.signal
  });
  const redirectedToLogin = response.redirected &&
    new URL(response.url, window.location.href).pathname === '/login';
  const jsonResponse = (response.headers.get('content-type') || '').includes('json');
  if (redirectedToLogin || (response.status === 401 && !jsonResponse)) {
    if (!edgeRedirecting) {
      edgeRedirecting = true;
      source?.close();
      source = null;
      window.location.assign(`/login?next=${encodeURIComponent(
        window.location.pathname + window.location.search)}`);
    }
    throw new Error('公网登录已过期，正在返回登录页');
  }
  if (!jsonResponse) throw new Error(`服务器返回 HTTP ${response.status}，请稍后重试`);
  const value = await response.json();
  if (response.status === 401 && path !== '/api/login') showLogin();
  if (!response.ok) {
    const error = new Error(value.error || '请求失败');
    error.httpStatus = response.status;
    throw error;
  }
  return value;
}

function liteItemKey(item) {
  return `${item?.turnId || ''}/${item?.id || ''}`;
}

function applyLitePatches(base, patches) {
  let value = {...base, items: [...(base?.items || [])]};
  for (const patch of patches || []) {
    value = {...value, ...(patch.meta || {})};
    const items = new Map(value.items.map(item => [liteItemKey(item), {...item}]));
    for (const entry of patch.upserts || []) items.set(entry.key, {...entry.value});
    for (const entry of patch.updates || []) {
      const item = {...(items.get(entry.key) || {})};
      Object.assign(item, entry.set || {});
      for (const [field, suffix] of Object.entries(entry.append || {})) {
        item[field] = String(item[field] || '') + String(suffix || '');
      }
      for (const field of entry.unset || []) delete item[field];
      items.set(entry.key, item);
    }
    for (const key of patch.removed || []) items.delete(key);
    if (Array.isArray(patch.order)) {
      value.items = patch.order.map(key => items.get(key)).filter(Boolean);
    } else {
      value.items = value.items.map(item => items.get(liteItemKey(item))).filter(Boolean);
    }
  }
  return value;
}

function liteCatalogPath() {
  return '/api/lite/catalog' + (liteCatalogVersion ?
    `?version=${encodeURIComponent(liteCatalogVersion)}&patch=1` : '');
}

function clearThreadSnapshots() {
  threadSnapshotEpoch += 1;
  threadSnapshots.clear();
  threadFetches.clear();
  historyPages.clear();
  preloadAttempts.clear();
  clearTimeout(preloadTimer);
  preloadTimer = null;
  preloadController?.abort();
}

function cachedThreadSnapshot(target, forPreview = false) {
  const key = threadKey(target.host, target.thread);
  const cached = threadSnapshots.get(key);
  if (!cached || cached.generation !== relayGeneration ||
      (forPreview && Date.now() - cached.saved > THREAD_PREVIEW_MAX_AGE_MS)) return null;
  threadSnapshots.delete(key);
  threadSnapshots.set(key, cached);
  return cached;
}

function saveThreadSnapshot(target, thread, revision, generation = relayGeneration) {
  if (!thread || generation !== relayGeneration) return null;
  const key = threadKey(target.host, target.thread);
  const bytes = new TextEncoder().encode(JSON.stringify(thread)).byteLength;
  threadSnapshots.delete(key);
  if (bytes > THREAD_CACHE_MAX_BYTES) return null;
  const cached = {thread, revision, generation, bytes, saved: Date.now()};
  threadSnapshots.set(key, cached);
  let total = [...threadSnapshots.values()].reduce((sum, item) => sum + item.bytes, 0);
  while (threadSnapshots.size > THREAD_CACHE_MAX_ENTRIES || total > THREAD_CACHE_MAX_BYTES) {
    const oldest = threadSnapshots.keys().next().value;
    total -= threadSnapshots.get(oldest).bytes;
    threadSnapshots.delete(oldest);
  }
  return cached;
}

function snapshotFromReply(target, reply, base, epoch) {
  if (epoch !== threadSnapshotEpoch) return null;
  const generation = reply.generation || relayGeneration;
  if (generation !== relayGeneration) { scheduleCatalogRefresh(); return null; }
  let thread = reply.thread;
  if (reply.mode === 'unchanged') thread = base?.thread;
  else if (reply.mode === 'patch' && base) thread = applyLitePatches(base.thread, reply.patches);
  if (!thread) return null;
  const revision = reply.revision || base?.revision || 0;
  const newer = threadSnapshots.get(threadKey(target.host, target.thread));
  if (newer?.generation === generation && newer.revision > revision) {
    return {thread: newer.thread, revision: newer.revision, online: Boolean(reply.online)};
  }
  saveThreadSnapshot(target, thread, revision, generation);
  return {thread, revision, online: Boolean(reply.online)};
}

async function fetchThreadSnapshot(target, afterCommand = false, options = {}) {
  const key = threadKey(target.host, target.thread);
  // A read started before a mutation cannot acknowledge that mutation. Keep the
  // incremental base, but detach the old request so its late reply is ignored.
  if (afterCommand) threadFetches.delete(key);
  if (threadFetches.has(key)) return threadFetches.get(key);
  const epoch = threadSnapshotEpoch;
  const request = (async () => {
    const base = cachedThreadSnapshot(target);
    const path = `/api/lite/thread?host=${encodeURIComponent(target.host)}&id=${encodeURIComponent(target.thread)}`;
    let reply = await api(path + (base?.revision ? `&since=${base.revision}&generation=${encodeURIComponent(relayGeneration)}` : ''), undefined, options);
    if ((!base && ['patch', 'unchanged'].includes(reply.mode))) reply = await api(path, undefined, options);
    if (threadFetches.get(key) !== request) return null;
    return snapshotFromReply(target, reply, base, epoch);
  })();
  threadFetches.set(key, request);
  try { return await request; }
  finally { if (threadFetches.get(key) === request) threadFetches.delete(key); }
}

function threadPreloadAllowed() {
  const network = navigator.connection;
  return !document.hidden && !$('workspace').hidden && !edgeRedirecting &&
    !network?.saveData && !['slow-2g', '2g'].includes(network?.effectiveType);
}

async function reserveThreadPreload() {
  const reserve = () => {
    const now = Date.now();
    let shared;
    try { shared = JSON.parse(localStorage.getItem('codexRelayPreloadBudget') || 'null'); } catch (_) {}
    if (shared && Number.isFinite(shared.started) && Number.isFinite(shared.used) &&
        shared.started <= now && now - shared.started < 3600000 && shared.used >= 0) preloadBudget = shared;
    if (now < preloadBudget.started || now - preloadBudget.started >= 3600000) preloadBudget = {started: now, used: 0};
    if (preloadBudget.used + PRELOAD_REQUEST_RESERVE_BYTES > PRELOAD_HOURLY_BYTES) return false;
    preloadBudget.used += PRELOAD_REQUEST_RESERVE_BYTES;
    try { localStorage.setItem('codexRelayPreloadBudget', JSON.stringify(preloadBudget)); } catch (_) {}
    return true;
  };
  return navigator.locks?.request ? navigator.locks.request('codex-relay-preload-budget', reserve) : reserve();
}

function threadPreloadTargets() {
  let targets;
  if (workspaceMode) targets = workspaceThreadTargets().filter(({host, thread}) => workspaceThreadMatches(host, thread));
  else {
    targets = [...$('catalog').querySelectorAll('.thread-row-wrap')]
      .filter(row => row.offsetParent !== null)
      .map(row => sidebarThreadTarget(row.dataset.hostId, row.dataset.threadId));
  }
  const index = targets.findIndex(item => item.host?.id === selected?.host && item.thread?.id === selected?.thread);
  if (index >= 0) targets = [...targets.slice(index + 1), ...targets.slice(0, index)];
  return targets.filter(({host, thread}) => host?.online && thread && !thread.unavailable &&
    !(selected?.host === host.id && selected?.thread === thread.id)).slice(0, 2);
}

function scheduleThreadPreload(delay = 2500) {
  if (!threadPreloadAllowed() || preloadTimer || preloadController) return;
  preloadTimer = setTimeout(() => { preloadTimer = null; void preloadNextThread(); }, delay);
}

async function preloadNextThread() {
  if (!threadPreloadAllowed() || preloadController) return;
  if (busy || refreshing || threadWatchPending) { scheduleThreadPreload(5000); return; }
  const target = threadPreloadTargets().map(({host, thread}) => ({host: host.id, thread: thread.id}))
    .find(target => !cachedThreadSnapshot(target, true) &&
      Date.now() - (preloadAttempts.get(threadKey(target.host, target.thread)) || 0) >= THREAD_PREVIEW_MAX_AGE_MS);
  if (!target || !await reserveThreadPreload() || !threadPreloadAllowed()) return;
  const epoch = threadSnapshotEpoch;
  const key = threadKey(target.host, target.thread);
  preloadAttempts.set(key, Date.now());
  while (preloadAttempts.size > 100) preloadAttempts.delete(preloadAttempts.keys().next().value);
  const controller = new AbortController();
  preloadController = controller;
  try {
    const base = cachedThreadSnapshot(target);
    const path = `/api/lite/preload?host=${encodeURIComponent(target.host)}&id=${encodeURIComponent(target.thread)}`;
    const reply = await api(path + (base?.revision ? `&since=${base.revision}&generation=${encodeURIComponent(relayGeneration)}` : ''),
      undefined, {signal: controller.signal});
    snapshotFromReply(target, reply, base, epoch);
  } catch (_) {
    // Preloading is optional: never interrupt reading or retry in a tight loop.
  } finally {
    if (preloadController === controller) preloadController = null;
    scheduleThreadPreload(10000);
  }
}

function applyLiteCatalogPatch(hosts, patches) {
  const changes = new Map((patches || []).map(item => [item.id, item]));
  return hosts.map(host => {
    const patch = changes.get(host.id);
    if (!patch) return host;
    const threads = new Map((patch.threads || []).map(item => [item.id, item]));
    return {...host, ...(patch.set || {}),
      threads: (host.threads || []).map(item => threads.get(item.id) || item)};
  });
}

function showLogin() {
  if (edgeRedirecting) return;
  source?.close();
  source = null;
  clearThreadSnapshots();
  localOutgoingMessages.clear();
  outgoingScope = '';
  outgoingScopeEpoch++;
  readAcknowledgedSnapshots.clear();
  clearThread();
  liteCatalogVersion = '';
  initialSelectionDone = false;
  if ($('codexUpdatesDialog').open) $('codexUpdatesDialog').close();
  
  closeMobilePanels();
  $('workspace').hidden = true;
  $('login').hidden = false;
}

function applyWorkspaceTitle(value) {
  if (typeof value !== 'string' || !value.trim()) return;
  workspaceTitle = value.trim();
  document.querySelectorAll('.brand-label').forEach(node => {node.textContent = workspaceTitle;});
  $('brandEditTrigger').textContent = workspaceTitle;
  document.title = uiText(`${workspaceTitle} · 远程工作台`);
}

function applyAppVersion(value) {
  if (typeof value !== 'string' || !/^\d+\.\d+\.\d+$/.test(value)) return;
  workspaceAppVersion = value;
  const badge = $('appVersion');
  badge.textContent = `v${value}`;
  badge.title = uiText(`版本 v${value}`);
  badge.setAttribute('aria-label', badge.title);
  badge.hidden = false;
}

function setRelayConnectionStatus(value) {
  relayConnectionMessage = value;
  const status = $('connection');
  status.textContent = uiText(value);
  status.title = uiText(value);
}

function setModelSettingsOpen(open) {
  const expanded = Boolean(open && !$('modelSettingsToggle').hidden);
  document.body.classList.toggle('composer-settings-open', expanded);
  $('modelSettingsToggle').setAttribute('aria-expanded', String(expanded));
  $('modelControls').hidden = !expanded;
}

function plainTitle(value) {
  return (value || '未命名对话').replace(/\*\*/g, '').split('\n')[0];
}

function statusName(status) {
  return ({
    active: '● 正在运行', idle: '当前空闲', history: '历史记录',
    notLoaded: '未加载', systemError: '异常'
  })[status] || status || '历史记录';
}

function threadKey(hostId, threadId) {
  return `${hostId}/${threadId}`;
}

function nativeThreadStatusName(thread) {
  if (thread.pendingRequests > 0) return `◆ 待确认 ${thread.pendingRequests}`;
  if (thread.status === 'active') return '● 正在运行';
  if (thread.status === 'systemError') return '异常';
  if (thread.unread === true) return '● 未读';
  if (thread.unread === false) return '已读';
  return statusName(thread.status);
}

function showCompletionNotice(host, thread, newlyCompleted = true) {
  if (thread.completionNotification === false) return;
  if (isReadingSelectedThread(host.id, thread.id)) return;
  completionTarget = {host: host.id, thread: thread.id};
  const state = thread.status === 'systemError' ? '运行结束 · 异常' :
    newlyCompleted ? `已完成 · ${thread.unread === true ? '未读' : '已读'}` :
    '有未读内容';
  $('completionOpen').textContent = `${applicationShortName(host)} · ${host.label} · ${plainTitle(thread.title)} ${state}`;
  $('completionNotice').hidden = false;
}

function trackThreadCompletions(nextCatalog) {
  const present = new Set();
  for (const host of nextCatalog) {
    for (const thread of host.threads || []) {
      if (thread.isSubagent) continue;
      const key = threadKey(host.id, thread.id);
      present.add(key);
      const previous = knownThreadStatuses.get(key);
      const newlyCompleted = previous?.status === 'active' && thread.status !== 'active';
      const newlyUnread = previous?.unread === false && thread.unread === true &&
        thread.status !== 'active';
      if (nativeThreadStatesInitialized && previous && host.online &&
          host.desktopConnected && !thread.archived && (newlyCompleted || newlyUnread)) {
        showCompletionNotice(host, thread, newlyCompleted);
      }
      knownThreadStatuses.set(key, {
        status: thread.status || 'notLoaded', unread: thread.unread
      });
    }
  }
  for (const key of knownThreadStatuses.keys()) {
    if (!present.has(key)) {
      knownThreadStatuses.delete(key);
    }
  }
  nativeThreadStatesInitialized = true;
}

function effortName(value) {
  return ({
    none: '无', minimal: '极低', low: '低', medium: '中',
    high: '高', xhigh: '超高', max: '最高', ultra: 'Ultra'
  })[value] || value;
}

function currentHost() {
  if (workspaceMode && !selected) return null;
  return catalog.find(host => host.id === selectedHostId) || null;
}

function applicationId(host) { return host?.applicationId || 'codex'; }
function applicationShortName(host) {
  const id = applicationId(host);
  return ({codex: 'Codex', deepseek: 'DeepSeek', claude: 'Claude'})[id] || APPLICATION_LABELS[id] || id;
}
function supportsAction(host, action) {
  return !Array.isArray(host?.capabilities?.actions) || host.capabilities.actions.includes(action);
}
function filterSupportedAttachments(additions, statusId) {
  const host = currentHost();
  const types = host?.capabilities?.attachmentMimeTypes;
  const accepted = additions.filter(file => file.remoteSourcePath ? supportsAction(host, 'attach_local_file') :
    supportsAction(host, 'upload_asset_chunk') && (!Array.isArray(types) || types.includes(file.type)));
  if (accepted.length !== additions.length) {
    $(statusId).className = 'error';
    $(statusId).textContent = Array.isArray(types) ? '当前原生应用仅支持 PNG、JPEG、WebP、GIF 图片附件；未添加不支持的文件。' :
      '当前原生应用不支持此附件操作；未添加不支持的文件。';
  }
  return accepted;
}
function syncAttachmentCapabilities() {
  const host = currentHost();
  const accept = host?.capabilities?.attachmentMimeTypes?.join(',') || '';
  $('fileInput').accept = accept;
  $('newThreadFileInput').accept = accept;
  $('addAttachment').hidden = !supportsAction(host, 'upload_asset_chunk');
  $('newThreadAddFile').hidden = !supportsAction(host, 'upload_asset_chunk');
  syncCopiedFileButton();
}
function physicalHostId(host = currentHost()) { return host?.physicalHostId || host?.id || ''; }
function applicationHosts() { return catalog.filter(host => applicationId(host) === selectedApplicationId); }
function modelDisplayName(host, id) {
  const model = host?.models?.find(item => item.id === id);
  if (applicationId(host) !== 'codex') return (model?.shortLabel || model?.label?.split(' · ')[0] || String(id || '').split('::').pop()).replace(/^deepseek[-\s]+/i, '');
  return id || '';
}

function isWorkspaceThread(hostId, threadId) {
  return workspaceThreads.some(item => item.host === hostId && item.thread === threadId);
}

function sidebarThreadTarget(hostId, threadId) {
  const host = catalog.find(item => item.id === hostId);
  const thread = host?.threads.find(item => item.id === threadId);
  if (host && thread) return {host, thread};
  const saved = workspaceThreads.find(item => item.host === hostId && item.thread === threadId);
  if (!saved) return {};
  return {host: host || {id: hostId, label: saved.hostLabel || hostId, online: false,
    applicationId: saved.applicationId || (hostId.endsWith('.deepseek') ? 'deepseek' : 'codex'),
    desktopConnected: false, projects: [], threads: []},
  thread: {id: threadId, title: saved.title || '未命名对话', unavailable: true}};
}

function workspaceThreadTargets() {
  return workspaceThreads.map(item => sidebarThreadTarget(item.host, item.thread));
}

function workspaceThreadMatches(host, thread) {
  if (archiveFilter === 'active' && thread.archived) return false;
  if (archiveFilter === 'archived' && !thread.archived) return false;
  const query = $('search').value.trim().toLowerCase();
  return !query || `${APPLICATION_LABELS[applicationId(host)] || applicationId(host)} ${host.label} ${thread.title} ${thread.cwd || ''} ${thread.projectName || ''} ${thread.model || ''}`
    .toLowerCase().includes(query);
}

function applyWorkspaceNavigation() {
  projectFilter = 'all';
  archiveFilter = workspaceNavigation.archiveFilter;
  $('search').value = workspaceNavigation.search;
}

function rememberedWorkspaceTarget() {
  const targets = workspaceThreadTargets().filter(item => !item.thread.unavailable);
  return targets.find(item => item.host.id === workspaceNavigation.hostId &&
    item.thread.id === workspaceNavigation.threadId) ||
    targets.find(item => workspaceThreadMatches(item.host, item.thread)) || null;
}

function syncProjectLocator() {
  const host = catalog.find(item => item.id === selected?.host);
  const thread = host?.threads.find(item => item.id === selected?.thread);
  $('locateProject').hidden = !selected;
  $('locateProject').disabled = !thread;
  $('locateProject').title = thread ?
    `定位到 ${host.label} · ${thread.projectName || '未分类'}` : '当前对话的项目信息暂不可用';
}

function revealWorkspaceThreadOrigin(hostId, threadId, userInitiated = false) {
  if (workspaceMode) revealThreadProject(hostId, threadId, userInitiated);
}

function revealThreadProject(hostId, threadId, userInitiated = false) {
  // Resolve the current catalog, not the workspace link's saved project/title.
  const host = catalog.find(item => item.id === hostId);
  const thread = host?.threads.find(item => item.id === threadId);
  if (!host || !thread) return;
  captureHostNavigation();
  workspaceMode = false;
  selectedHostId = host.id;
  projectFilter = thread.projectId || 'unassigned';
  archiveFilter = thread.archived ? 'archived' : 'active';
  $('search').value = '';
  hostNavigation.set(host.id, {...hostNavigation.get(host.id), threadId: thread.id,
    projectFilter, archiveFilter, search: '', catalogScrollTop: 0});
  setGroupExpanded(host.id, projectFilter, true);
  lastCatalog = '';
  if (selected?.host === host.id && selected.thread === thread.id) {
    // Revealing an already open chat must not reload its timeline or draft.
    renderCatalog({preload: false});
  } else {
    void selectThread(host, thread, true, userInitiated);
  }
  if (mobileQuery.matches) document.body.classList.add('sidebar-open');
  else {
    sidebarCollapsed = false;
    try { localStorage.setItem('codexRelaySidebarCollapsed', '0'); } catch (_) {}
  }
  updateMobileBackdrop();
  
  requestAnimationFrame(() => {
    if (workspaceMode || selectedHostId !== host.id ||
        selected?.host !== host.id || selected.thread !== thread.id) return;
    const row = [...$('catalog').querySelectorAll('.thread-row-wrap')].find(element =>
      element.dataset.hostId === host.id && element.dataset.threadId === thread.id);
    row?.scrollIntoView({block: 'nearest', inline: 'nearest'});
    captureHostNavigation();
  });
}

async function updateWorkspaceMembership(action, hostId, threadId, extra = {}) {
  const result = await api('/api/ui/workspace', {...extra, action,
    host: hostId, thread: threadId});
  workspaceThreads = result.workspaceThreads;
  lastCatalog = '';
  renderCatalog();
  scheduleCatalogRefresh();
}

function workspaceMoveNeighbor(hostId, threadId, direction) {
  const visible = workspaceThreadTargets().filter(item => workspaceThreadMatches(item.host, item.thread));
  const index = visible.findIndex(item => item.host.id === hostId && item.thread.id === threadId);
  return index < 0 ? null : visible[index + direction] || null;
}

async function moveWorkspaceThread(source, target, after = false) {
  if (!workspaceMode || workspaceUpdateBusy || !source || !target ||
      source.host === target.host && source.thread === target.thread) return;
  workspaceUpdateBusy = true;
  captureHostNavigation();
  syncWorkspaceOrderControls();
  try {
    await updateWorkspaceMembership('move', source.host, source.thread,
      {targetHost: target.host, targetThread: target.thread, after});
    $('sendResult').className = '';
    $('sendResult').textContent = '✓ 工作区顺序已保存';
  } catch (error) {
    $('sendResult').className = 'error';
    $('sendResult').textContent = error.message;
  } finally {
    workspaceUpdateBusy = false;
    syncWorkspaceOrderControls();
    document.querySelectorAll('.thread-row-wrap').forEach(element =>
      element.classList.remove('workspace-dragging', 'workspace-drop-before', 'workspace-drop-after'));
  }
}

function syncWorkspaceOrderControls() {
  const hostId = threadContext?.hostId;
  const threadId = threadContext?.threadId;
  const show = workspaceMode && isWorkspaceThread(hostId, threadId);
  for (const [id, direction] of [['workspaceThreadUp', -1], ['workspaceThreadDown', 1]]) {
    $(id).hidden = !show;
    $(id).disabled = !show || workspaceUpdateBusy || !workspaceMoveNeighbor(hostId, threadId, direction);
  }
  document.querySelectorAll('.workspace-drag-handle').forEach(handle => {
    handle.disabled = workspaceUpdateBusy;
    handle.draggable = !workspaceUpdateBusy;
  });
}

async function toggleWorkspaceThread(hostId, threadId) {
  if (workspaceUpdateBusy) return;
  const removing = isWorkspaceThread(hostId, threadId);
  workspaceUpdateBusy = true;
  syncWorkspaceOrderControls();
  $('workspaceThreadToggle').disabled = true;
  try {
    await updateWorkspaceMembership(removing ? 'remove' : 'add', hostId, threadId);
    $('sendResult').className = '';
    $('sendResult').textContent = removing ? '✓ 已移出工作区' : '✓ 已添加到工作区';
  } catch (error) {
    $('sendResult').className = 'error';
    $('sendResult').textContent = error.message;
  } finally {
    workspaceUpdateBusy = false;
    syncWorkspaceOrderControls();
    $('workspaceThreadToggle').disabled = false;
  }
}



















function setSoftwareError(message = '', hostId = currentHost()?.id) {
        if (message) softwareErrors.set(hostId, message); else softwareErrors.delete(hostId);
        if (currentHost()?.id === hostId) renderCodexUpdates(); }

function setSoftwareNotice(message = '', warning = false, hostId = currentHost()?.id) {
        if (message) softwareNotices.set(hostId, {message, warning}); else softwareNotices.delete(hostId);
        if (currentHost()?.id === hostId) renderCodexUpdates(); }







function renderCodexUpdates(host = currentHost()) {
  const notice = softwareNotices.get(host?.id);
  $('codexUpdatesNotice').textContent = notice?.message || '';
  $('codexUpdatesNotice').hidden = !notice?.message;
  $('codexUpdatesNotice').classList.toggle('warning', Boolean(notice?.warning));
  $('codexUpdatesError').textContent = softwareErrors.get(host?.id) || '';
  $('codexUpdatesError').hidden = !$('codexUpdatesError').textContent;
  const report = softwareReports.get(`${host?.id}:codex`);
  const status = $('codexUpdateStatus');
  const detail = $('codexUpdateDetails');
  const button = $('codexStartUpdate');
  if (!report) {
    status.textContent = '尚未检测 Codex App 更新';
    detail.textContent = '仅在点击时读取目标机的软件版本。';
  } else if (!report.supported) {
    status.textContent = '此目标机暂不支持面板一键更新';
    detail.textContent = report.error || '未检测到受支持的安装方式。';
  } else {
    status.textContent = report.updateAvailable ?
      `${report.installed} → ${report.latest} 可更新` :
      `${report.installed} 已是${report.source === 'official-online' ? '官方在线' : '本机缓存'}版本`;
    detail.textContent = (report.source === 'official-online' ?
      '已读取官方软件源。' : '当前为本机缓存；更新时会先刷新官方软件源。') +
      '安装后会尝试重启目标机 Codex App；正在运行的任务可能中断。';
  }
  $('codexCheckUpdates').disabled = !host?.online || softwareHostBusy(host?.id);
  button.disabled = !host?.online || softwareHostBusy(host?.id) ||
    !report?.supported;
}

async function checkSoftware(product, host = currentHost()) {
  if (!host?.online) return null;
  const result = await issueCommand('software_check', {
    host: host.id, payload: {package: product}});
  softwareReports.set(`${host.id}:${product}`, result);
  if (currentHost()?.id === host.id) {
    ;
    renderCodexUpdates();
  }
  return result;
}













function selectedThread() {
  const host = currentHost();
  return host?.threads.find(thread => thread.id === selected?.thread) || null;
}

function setOptions(select, items, value) {
  const signature = JSON.stringify(items);
  if (select.dataset.signature !== signature) {
    select.replaceChildren(...items.map(item => {
      const option = document.createElement('option');
      option.value = item.value;
      option.textContent = item.label;
      const dataLabel = ['modelSelect', 'newThreadModel', 'fileAttachThread'].includes(select.id) ||
        ['projectFilter', 'newThreadProject', 'moveProject'].includes(select.id) &&
        !['all', 'unassigned', ''].includes(String(item.value)) || item.rawLabel;
      if (dataLabel) option.dataset.i18nIgnore = 'true';
      if (item.title) option.title = item.title;
      return option;
    }));
    select.dataset.signature = signature;
  }
  if (items.some(item => item.value === value)) select.value = value;
}

function addTextBlockControls(pre, key) {
  if (pre.closest('.text-box')) return;
  const box = document.createElement('div');
  box.className = 'text-box';
  const actions = document.createElement('div');
  actions.className = 'text-box-actions';
  actions.setAttribute('aria-label', '文本框操作');

  const wrap = document.createElement('button');
  wrap.type = 'button';
  wrap.className = 'text-box-action';
  const applyWrap = () => {
    const enabled = wrappedTextBlocks.has(key);
    box.classList.toggle('wrap-lines', enabled);
    wrap.classList.toggle('active', enabled);
    wrap.textContent = enabled ? '不换行' : '换行';
    wrap.title = enabled ? '恢复横向滚动' : '让超宽内容自动换行';
    wrap.setAttribute('aria-label', wrap.title);
    wrap.setAttribute('aria-pressed', String(enabled));
  };
  wrap.onclick = () => {
    if (wrappedTextBlocks.has(key)) wrappedTextBlocks.delete(key);
    else wrappedTextBlocks.add(key);
    applyWrap();
  };

  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'text-box-action';
  copy.textContent = '复制';
  copy.title = '复制完整内容';
  copy.setAttribute('aria-label', copy.title);
  copy.onclick = () => copyPlainText(pre.textContent, copy);

  pre.classList.add('controlled-text');
  pre.before(box);
  actions.append(wrap, copy);
  box.append(actions, pre);
  applyWrap();
}

function enhanceTextBlocks(target, keyPrefix) {
  [...target.querySelectorAll('pre')].forEach((pre, index) => {
    addTextBlockControls(pre, `${keyPrefix}/${index}`);
  });
}

function richText(target, text, keyPrefix, env = {}) {
  target.replaceChildren();
  if (!markdown) {
    target.textContent = String(text || '');
    return;
  }
  target.innerHTML = markdown.render(String(text || ''), env);
  if (window.renderMathInElement) {
    try {
      window.renderMathInElement(target, {
        delimiters: [
          {left: '$$', right: '$$', display: true},
          {left: '\\[', right: '\\]', display: true},
          {left: '\\(', right: '\\)', display: false},
          {left: '$', right: '$', display: false}
        ],
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
        throwOnError: false,
        strict: 'ignore'
      });
    } catch (_) {
      // Keep the original TeX visible when one malformed expression cannot render.
    }
  }
  enhanceTextBlocks(target, keyPrefix);
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '大小未知';
  const units = ['B', 'KB', 'MB', 'GB'];
  let amount = bytes;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

function decodeBase64(value) {
  const raw = atob(value || '');
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

function clearAssetCache() {
  for (const value of assetCache.values()) {
    Promise.resolve(value).then(asset => {
      if (asset?.objectUrl) URL.revokeObjectURL(asset.objectUrl);
    }).catch(() => {});
  }
  assetCache.clear();
}

function loadAsset(hostId, threadId, attachment, maxBytes = 256 * 1024 * 1024) {
  if (attachment.external && attachment.url) {
    return Promise.resolve({url: attachment.url, external: true,
      name: attachment.name, mime: attachment.mime});
  }
  const key = `${hostId}/${threadId}/${attachment.id}/${maxBytes}`;
  if (assetCache.has(key)) return assetCache.get(key);
  const promise = (async () => {
    if (attachment.localFileReference) {
      const result = await loadRemoteBlob(hostId, attachment.source, maxBytes, null, false);
      if (result.truncated) throw new Error('文件超过可下载上限');
      const objectUrl = URL.createObjectURL(result.blob);
      return {url: objectUrl, objectUrl, blob: result.blob,
        name: attachment.name, mime: result.mime};
    }
    const chunks = [];
    let offset = 0;
    let metadata = attachment;
    while (true) {
      const result = await issueCommand('fetch_asset', {
        host: hostId,
        thread: threadId,
        payload: {assetId: attachment.id, offset, limit: 2 * 1024 * 1024}
      });
      metadata = result.asset || metadata;
      if (result.offset !== offset) throw new Error('附件分片顺序不一致');
      chunks.push(decodeBase64(result.data));
      offset = result.nextOffset;
      if (result.eof) break;
      if (!Number.isFinite(offset) || offset <= 0 || offset > maxBytes) {
        throw new Error('附件大小超出网页处理范围');
      }
    }
    if (offset > maxBytes) throw new Error('附件大小超出网页处理范围');
    const blob = new Blob(chunks, {type: metadata.mime || 'application/octet-stream'});
    const objectUrl = URL.createObjectURL(blob);
    return {url: objectUrl, objectUrl, blob, name: metadata.name || attachment.name,
      mime: metadata.mime || attachment.mime};
  })();
  assetCache.set(key, promise);
  promise.catch(() => assetCache.delete(key));
  return promise;
}

async function downloadAttachment(hostId, threadId, attachment, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '准备中…';
  try {
    const asset = await loadAsset(hostId, threadId, attachment);
    const link = document.createElement('a');
    link.href = asset.url;
    link.download = asset.external ? '' : (asset.name || attachment.name || '附件');
    link.target = asset.external ? '_blank' : '_self';
    link.rel = 'noopener noreferrer';
    document.body.append(link);
    link.click();
    link.remove();
    button.textContent = asset.external ? '已打开' : '已下载';
  } catch (error) {
    button.textContent = '重试下载';
    button.title = error.message;
  } finally {
    button.disabled = false;
    if ((window.CRBI18n?.originalText(button) || button.textContent) === '准备中…') button.textContent = original;
  }
}

const previewTextExtensions = new Set([
  'txt', 'log', 'md', 'markdown', 'py', 'js', 'jsx', 'ts', 'tsx', 'json',
  'yaml', 'yml', 'toml', 'xml', 'html', 'css', 'scss', 'sh', 'bash', 'zsh',
  'ps1', 'bat', 'cmd', 'c', 'h', 'cpp', 'hpp', 'rs', 'go', 'java', 'kt',
  'swift', 'sql', 'ini', 'cfg', 'conf', 'env', 'csv', 'tsv', 'tex', 'bib'
]);
const previewImageExtensions = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif', 'svg']);
const previewLimits = {text: 2 * 1024 * 1024, markdown: 2 * 1024 * 1024,
  table: 2 * 1024 * 1024, html: 2 * 1024 * 1024,
  audio: 40 * 1024 * 1024, video: 40 * 1024 * 1024,
  image: 24 * 1024 * 1024, pdf: 40 * 1024 * 1024};

function filePreviewKind(name, mime = '') {
  const extension = String(name || '').split('.').pop().toLowerCase();
  const type = String(mime || '').toLowerCase().split(';')[0];
  if (['html', 'htm'].includes(extension) || type === 'text/html') return 'html';
  if (['csv', 'tsv'].includes(extension)) return 'table';
  if (['mp3', 'wav', 'ogg', 'm4a', 'flac'].includes(extension) || type.startsWith('audio/')) return 'audio';
  if (['mp4', 'webm', 'mov', 'm4v'].includes(extension) || type.startsWith('video/')) return 'video';
  if (extension === 'pdf' || type === 'application/pdf') return 'pdf';
  if (previewImageExtensions.has(extension) ||
      /^image\/(png|jpeg|gif|webp|bmp|avif)$/.test(type)) return 'image';
  if (extension === 'md' || extension === 'markdown') return 'markdown';
  if (previewTextExtensions.has(extension) || type.startsWith('text/') ||
      /^(application\/(json|xml|javascript|x-yaml))$/.test(type) ||
      !String(name || '').includes('.')) return 'text';
  return '';
}

function setFilePreviewFontSize(value, remember = false) {
  filePreviewFontSize = Math.min(32, Math.max(5, Number(value) || 13));
  $('filePreviewFontSize').value = String(filePreviewFontSize);
  $('filePreviewFontValue').textContent = `${filePreviewFontSize}px`;
  $('filePreviewContent').style.setProperty('--file-preview-font-size', `${filePreviewFontSize}px`);
  if (remember) {
    try { localStorage.setItem('codexRelayPreviewFontSize', String(filePreviewFontSize)); }
    catch (_) {}
  }
}

function activeFilePreview() {
  return filePreviewTabs.find(tab => tab.id === activeFilePreviewId) || null;
}

function renderFilePreviewTabs() {
  const tabs = $('filePreviewTabs');
  tabs.replaceChildren();
  for (const tab of filePreviewTabs) {
    const item = document.createElement('div');
    item.className = 'file-preview-tab' + (tab.id === activeFilePreviewId ? ' active' : '');
    item.draggable = true;
    item.setAttribute('role', 'tab');
    item.setAttribute('aria-selected', String(tab.id === activeFilePreviewId));
    item.ondragstart = event => event.dataTransfer.setData('text/plain', tab.id);
    item.ondragover = event => event.preventDefault();
    item.ondrop = event => {
      event.preventDefault();
      const from = event.dataTransfer.getData('text/plain');
      moveFilePreviewTab(from, tab.id);
    };
    const select = document.createElement('button');
    select.type = 'button';
    select.className = 'file-preview-tab-label';
    select.textContent = tab.name;
    select.title = tab.meta;
    select.onclick = () => activateFilePreview(tab.id);
    const left = document.createElement('button');
    left.type = 'button';
    left.className = 'file-preview-tab-control';
    left.textContent = '‹';
    left.title = '向左移动';
    left.disabled = filePreviewTabs[0] === tab;
    left.onclick = () => moveFilePreviewTab(tab.id, filePreviewTabs[filePreviewTabs.indexOf(tab) - 1]?.id);
    const right = document.createElement('button');
    right.type = 'button';
    right.className = 'file-preview-tab-control';
    right.textContent = '›';
    right.title = '向右移动';
    right.disabled = filePreviewTabs.at(-1) === tab;
    right.onclick = () => moveFilePreviewTab(tab.id, filePreviewTabs[filePreviewTabs.indexOf(tab) + 1]?.id);
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'file-preview-tab-control close';
    close.textContent = '×';
    close.title = `关闭 ${tab.name}`;
    close.onclick = () => closeFilePreviewTab(tab.id);
    item.append(select, left, right, close);
    tabs.append(item);
  }
  $('filePreviewToggle').disabled = !filePreviewTabs.length;
  $('filePreviewCount').textContent = String(filePreviewTabs.length);
}

function moveFilePreviewTab(fromId, targetId) {
  const from = filePreviewTabs.findIndex(tab => tab.id === fromId);
  const target = filePreviewTabs.findIndex(tab => tab.id === targetId);
  if (from < 0 || target < 0 || from === target) return;
  const [tab] = filePreviewTabs.splice(from, 1);
  filePreviewTabs.splice(target, 0, tab);
  renderFilePreviewTabs();
}

function activateFilePreview(id) {
  const tab = filePreviewTabs.find(item => item.id === id);
  if (!tab) return;
  activeFilePreviewId = id;
  for (const item of filePreviewTabs) item.pane.hidden = item !== tab;
  $('filePreviewTitle').textContent = tab.name;
  $('filePreviewMeta').textContent = tab.meta;
  $('filePreviewStatus').textContent = tab.status;
  $('filePreviewDownload').disabled = !tab.downloader;
  const remote = tab.key.startsWith('remote/');
  $('filePreviewEdit').hidden = !remote;
  $('filePreviewEdit').textContent = tab.editing ? '取消编辑' : '编辑';
  const attachHost = physicalHostId() === tab.hostId ? currentHost() : catalog.find(item => item.id === tab.hostId);
  $('filePreviewAttach').hidden = !remote || !supportsAction(attachHost, 'attach_local_file');
  $('filePreviewAttach').disabled = Boolean(tab.editing);
  $('filePreviewFontControl').hidden = !['text', 'markdown', 'table'].includes(tab.kind);
  const annotation = $('filePreviewAnnotation');
  if (annotation) annotation.hidden = !tab.sourcePath || !['text', 'markdown', 'table'].includes(tab.kind) || selected?.host !== tab.hostId;
  setFilePreviewFontSize(filePreviewFontSize);
  renderFilePreviewTabs();
  if (!$('filePreviewDialog').open) $('filePreviewDialog').showModal();
}

function closeFilePreviewTab(id) {
  const index = filePreviewTabs.findIndex(tab => tab.id === id);
  if (index < 0) return;
  const [tab] = filePreviewTabs.splice(index, 1);
  tab.closed = true;
  for (const url of tab.urls) URL.revokeObjectURL(url);
  tab.pane.remove();
  if (activeFilePreviewId === id) {
    activeFilePreviewId = '';
    const next = filePreviewTabs[Math.min(index, filePreviewTabs.length - 1)];
    if (next) activateFilePreview(next.id);
    else {
      $('filePreviewDialog').close();
      $('filePreviewTitle').textContent = '文件预览';
      $('filePreviewMeta').textContent = '';
      $('filePreviewStatus').textContent = '';
    }
  }
  renderFilePreviewTabs();
}

function setPreviewStatus(tab, status) {
  if (tab.closed) return;
  tab.status = status;
  if (activeFilePreviewId === tab.id) $('filePreviewStatus').textContent = status;
}

function beginFilePreview(key, name, meta, downloader, kind = '') {
  let tab = filePreviewTabs.find(item => item.key === key);
  if (tab) {
    activateFilePreview(tab.id);
    return {tab, existing: true};
  }
  const pane = document.createElement('div');
  pane.className = 'file-preview-pane';
  pane.textContent = '正在读取文件…';
  $('filePreviewContent').append(pane);
  tab = {id: `preview-${++filePreviewSequence}`, key, name: name || '文件预览',
    meta: meta || '', downloader, kind, pane, status: '', urls: [], closed: false,
    hostId: '', sourcePath: ''};
  filePreviewTabs.push(tab);
  activateFilePreview(tab.id);
  return {tab, existing: false};
}

function downloadBlobUrl(url, name) {
  const link = document.createElement('a');
  link.href = url;
  link.download = name || '文件';
  document.body.append(link);
  link.click();
  link.remove();
}

async function renderFilePreview(blob, tab, truncated = false) {
  if (tab.closed) return;
  const {name, kind} = tab;
  if (activeFilePreviewId === tab.id && $('filePreviewAnnotation')) {
    $('filePreviewAnnotation').hidden = !tab.sourcePath || !['text', 'markdown', 'table'].includes(kind) || selected?.host !== tab.hostId;
  }
  const content = tab.pane;
  content.replaceChildren();
  if (kind === 'table') {
    const value = await blob.text();
    if (tab.closed) return;
    const rows = parsePreviewTable(value, name.toLowerCase().endsWith('.tsv') ? '\t' : ',');
    const table = document.createElement('table'); table.className = 'preview-table';
    for (const row of rows.slice(0, 1000)) {
      const tr = document.createElement('tr');
      for (const cell of row.slice(0, 100)) {
        const td = document.createElement('td'); td.textContent = cell; tr.append(td);
      }
      table.append(tr);
    }
    content.append(table);
    const source = document.createElement('details'); const summary = document.createElement('summary'); summary.textContent = '查看源码';
    const pre = document.createElement('pre'); pre.className = 'file-source-text'; pre.textContent = value;
    source.append(summary, pre); content.append(source);
    setPreviewStatus(tab, truncated || rows.length > 1000 ? '只显示前 1000 行、100 列；可下载完整文件。' : '');
  } else if (kind === 'html') {
    const source = await blob.text();
    if (tab.closed) return;
    const frame = document.createElement('iframe'); frame.title = name;
    // Opaque origin, no scripts/network/forms: untrusted file cannot reach the panel.
    frame.setAttribute('sandbox', '');
    const policy = "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; base-uri 'none'; form-action 'none'";
    frame.srcdoc = `<meta http-equiv="Content-Security-Policy" content="${policy}">${source}`;
    content.append(frame);
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    summary.textContent = '查看 HTML 源码'; pre.textContent = source; details.append(summary, pre); content.append(details);
    setPreviewStatus(tab, '安全预览：不执行脚本或加载外部资源。');
  } else if (kind === 'audio' || kind === 'video') {
    const url = URL.createObjectURL(blob); tab.urls.push(url);
    const media = document.createElement(kind); media.controls = true; media.preload = 'metadata'; media.src = url;
    content.append(media);
  } else if (kind === 'text' || kind === 'markdown') {
    const bytes = await blob.arrayBuffer();
    if (tab.closed) return;
    const raw = new Uint8Array(bytes);
    if (raw.includes(0) && !((raw[0] === 0xff && raw[1] === 0xfe) ||
        (raw[0] === 0xfe && raw[1] === 0xff))) {
      content.textContent = '文件包含二进制内容，无法作为文本预览。';
      return;
    }
    const encoding = raw[0] === 0xff && raw[1] === 0xfe ? 'utf-16le' :
      raw[0] === 0xfe && raw[1] === 0xff ? 'utf-16be' : 'utf-8';
    const value = new TextDecoder(encoding).decode(raw);
    if (kind === 'markdown') {
      const rendered = document.createElement('div');
      rendered.className = 'content file-markdown';
      richText(rendered, value, `preview/${tab.id}`, {filePreview: true});
      content.append(rendered);
      void hydratePreviewImages(tab, rendered);
      const source = document.createElement('details');
      source.className = 'file-source';
      const summary = document.createElement('summary');
      summary.textContent = '查看 Markdown 源码';
      const pre = document.createElement('pre');
      pre.textContent = value;
      source.append(summary, pre);
      content.append(source);
    } else {
      const pre = document.createElement('pre');
      pre.className = 'file-source-text';
      pre.textContent = value;
      content.append(pre);
    }
    setPreviewStatus(tab, truncated ? '文件较大，仅预览前 2 MB；可下载完整文件。' : '');
  } else if (kind === 'image') {
    const url = URL.createObjectURL(blob);
    tab.urls.push(url);
    const image = document.createElement('img');
    image.src = url;
    image.alt = name;
    image.onerror = () => { if (!tab.closed) content.textContent = '浏览器无法显示这种图片格式。'; };
    content.append(image);
  } else if (kind === 'pdf') {
    const url = URL.createObjectURL(new Blob([blob], {type: 'application/pdf'}));
    tab.urls.push(url);
    const frame = document.createElement('iframe');
    frame.src = url;
    frame.title = name;
    content.append(frame);
    const external = document.createElement('a');
    external.href = url;
    external.target = '_blank';
    external.rel = 'noopener noreferrer';
    external.textContent = 'PDF 无法显示？在新标签打开';
    content.append(external);
  } else {
    content.textContent = '这种格式暂不支持在线预览，可下载后打开。';
  }
}

function parsePreviewTable(value, delimiter) {
  const rows = []; let row = [], cell = '', quoted = false;
  for (let index = 0; index < value.length && rows.length <= 1000; index++) {
    const char = value[index];
    if (char === '"') {
      if (quoted && value[index + 1] === '"') { cell += '"'; index++; }
      else quoted = !quoted;
    } else if (!quoted && (char === delimiter || char === '\n')) {
      if (row.length < 100) row.push(cell.replace(/\r$/, '')); cell = '';
      if (char === '\n') { rows.push(row); row = []; }
    } else if (cell.length < 64000) cell += char;
  }
  if (cell || row.length) { row.push(cell.replace(/\r$/, '')); rows.push(row); }
  return rows;
}

function resolvePreviewImagePath(base, source) {
  let value = String(source || '').trim();
  if (!value || /^(https?:|data:|blob:)/i.test(value)) return '';
  try { value = decodeURIComponent(value); } catch (_) {}
  value = value.split(/[?#]/)[0];
  if (/^file:/i.test(value)) {
    try { value = new URL(value).pathname.replace(/^\/(?=[A-Za-z]:)/, ''); } catch (_) { return ''; }
  }
  if (/^[A-Za-z]:[\\/]/.test(value) || value.startsWith('/')) return value;
  if (!base) return '';
  if (/^file:/i.test(base)) {
    try { base = decodeURIComponent(new URL(base).pathname).replace(/^\/(?=[A-Za-z]:)/, ''); }
    catch (_) { return ''; }
  }
  const slash = base.includes('\\') ? '\\' : '/';
  const parts = base.replaceAll('\\', '/').split('/');
  parts.pop();
  for (const part of value.replaceAll('\\', '/').split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') parts.pop();
    else parts.push(part);
  }
  return parts.join(slash) || slash;
}

async function hydratePreviewImages(tab, rendered) {
  for (const placeholder of rendered.querySelectorAll('[data-preview-src]')) {
    if (tab.closed) return;
    const path = resolvePreviewImagePath(tab.sourcePath, placeholder.dataset.previewSrc);
    if (!tab.hostId || !path) {
      placeholder.textContent = '无法定位文档中的本地图片';
      continue;
    }
    try {
      const result = await loadRemoteBlob(tab.hostId, path, previewLimits.image, tab);
      if (tab.closed || !placeholder.isConnected) return;
      if (result.truncated) throw new Error('图片超过在线预览上限');
      const url = URL.createObjectURL(result.blob);
      tab.urls.push(url);
      const image = document.createElement('img');
      image.src = url;
      image.alt = placeholder.dataset.previewSrc;
      image.loading = 'lazy';
      image.onerror = () => { image.replaceWith(document.createTextNode('图片无法显示')); };
      placeholder.replaceWith(image);
    } catch (error) {
      if (!tab.closed) placeholder.textContent = `图片读取失败：${error.message}`;
    }
  }
}

async function openAttachmentPreview(hostId, threadId, attachment) {
  if (attachment.localFileReference) {
    return openRemoteFilePreview(hostId, {path: attachment.source,
      name: attachment.name || attachment.label || '文件', size: null});
  }
  const name = attachment.name || attachment.label || '附件';
  const kind = filePreviewKind(name, attachment.mime);
  const {tab, existing} = beginFilePreview(`attachment/${hostId}/${threadId}/${attachment.id}`, name,
    `${attachment.mime || '附件'} · ${formatBytes(attachment.size)}`,
    attachment.available === false ? null : async () => {
      const asset = await loadAsset(hostId, threadId, attachment);
      if (asset.external) window.open(asset.url, '_blank', 'noopener,noreferrer');
      else downloadBlobUrl(asset.url, asset.name || name);
    }, kind);
  if (existing) return;
  tab.hostId = hostId;
  tab.sourcePath = attachment.source || '';
  if (attachment.available === false) {
    tab.pane.textContent = attachment.error || '目标机上的文件已不可用。';
    return;
  }
  if (!kind || attachment.external) {
    tab.pane.textContent = attachment.external ?
      '外部附件请使用“下载”按钮打开。' : '这种格式暂不支持在线预览，可下载后打开。';
    return;
  }
  if (attachment.size && attachment.size > previewLimits[kind] &&
      kind !== 'text' && kind !== 'markdown') {
    tab.pane.textContent = `文件超过在线预览上限（${formatBytes(previewLimits[kind])}），可下载后查看。`;
    return;
  }
  try {
    if (attachment.size > previewLimits[kind] &&
        (kind === 'text' || kind === 'markdown')) {
      const result = await issueCommand('fetch_asset', {host: hostId, thread: threadId,
        payload: {assetId: attachment.id, offset: 0, limit: previewLimits[kind]}});
      if (tab.closed) return;
      const blob = new Blob([decodeBase64(result.data)],
        {type: attachment.mime || 'text/plain'});
      await renderFilePreview(blob, tab, !result.eof);
      return;
    }
    const asset = await loadAsset(hostId, threadId, attachment, previewLimits[kind]);
    if (tab.closed) return;
    if (asset.external || !asset.blob) throw new Error('附件不支持内嵌预览');
    if (asset.blob.size > previewLimits[kind]) throw new Error('文件超过在线预览上限');
    await renderFilePreview(asset.blob, tab);
  } catch (error) {
    if (!tab.closed) tab.pane.textContent = `无法预览：${error.message}`;
  }
}

function localSourceKey(value) {
  let source = String(value || '');
  try { source = decodeURIComponent(source); } catch (_) {}
  return source.replaceAll('\\', '/').toLowerCase();
}

function localSourceName(value) {
  return localSourceKey(value).replace(/:\d+(?::\d+)?$/, '').split('/').at(-1);
}

function inlineAttachment(link, attachments) {
  const sourceKey = value => {
    const path = fileCitationPath(value);
    return path.startsWith('/') ? path : localSourceKey(path || value);
  };
  const source = sourceKey(link.dataset.localSource);
  const exact = attachments.find(attachment => attachment.source &&
    sourceKey(attachment.source) === source);
  const legacy = attachments.filter(attachment => !attachment.source);
  const byName = legacy.filter(attachment =>
    localSourceKey(attachment.name) === localSourceName(source));
  const byLabel = legacy.filter(attachment =>
    String(attachment.label || '').trim() === link.textContent.trim());
  return exact || (byName.length === 1 ? byName[0] :
    byLabel.length === 1 ? byLabel[0] : null);
}

function messageAttachments(target, item) {
  const attachments = [...(item.attachments || [])];
  for (const link of target.querySelectorAll('a[data-file-citation="true"]')) {
    if (inlineAttachment(link, attachments)) continue;
    const source = fileCitationPath(link.dataset.localSource);
    if (!source) continue;
    attachments.push({id: 'file-citation/' + encodeURIComponent(source),
      source, name: source.replaceAll('\\', '/').split('/').at(-1) || source,
      label: link.textContent, kind: 'file', localFileReference: true});
  }
  return attachments;
}

function bindInlineAttachmentPreviews(target, item, hostId, threadId) {
  const attachments = messageAttachments(target, item);
  for (const link of target.querySelectorAll('a.local-file-link')) {
    const attachment = inlineAttachment(link, attachments);
    if (!attachment) link.title = '未找到对应附件，请使用消息下方的附件卡片';
    link.addEventListener('click', event => {
      event.preventDefault();
      if (attachment) openAttachmentPreview(hostId, threadId, attachment);
    });
  }
}

function renderAttachments(target, item, hostId, threadId) {
  const attachments = messageAttachments(target, item);
  if (!attachments.length) return;
  const list = document.createElement('div');
  list.className = 'attachments';
  for (const attachment of attachments) {
    const card = document.createElement('section');
    card.className = `attachment ${attachment.kind === 'image' ? 'image-attachment' : ''}`;
    const preview = document.createElement('div');
    preview.className = 'attachment-preview';
    const info = document.createElement('div');
    info.className = 'attachment-info';
    const name = document.createElement('strong');
    name.textContent = attachment.label || attachment.name ||
      (attachment.kind === 'image' ? '图片' : '附件');
    const meta = document.createElement('span');
    meta.textContent = `${attachment.mime || '文件'} · ${formatBytes(attachment.size)}`;
    info.append(name, meta);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'attachment-download secondary';
    button.textContent = attachment.external ? '打开' : '下载';
    button.disabled = attachment.available === false;
    button.title = attachment.error || '';
    button.onclick = () => downloadAttachment(hostId, threadId, attachment, button);
    const actions = document.createElement('div');
    actions.className = 'attachment-actions';
    if (filePreviewKind(attachment.name || attachment.label, attachment.mime) &&
        attachment.available !== false) {
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'secondary';
      open.textContent = '预览';
      open.onclick = () => openAttachmentPreview(hostId, threadId, attachment);
      actions.append(open);
    }
    actions.append(button);
    card.append(preview, info, actions);
    list.append(card);

    if (attachment.kind === 'image' && attachment.available !== false &&
        (!attachment.size || attachment.size <= 24 * 1024 * 1024)) {
      const loading = document.createElement('span');
      loading.className = 'attachment-loading';
      loading.textContent = '正在载入图片…';
      preview.append(loading);
      loadAsset(hostId, threadId, attachment, previewLimits.image).then(asset => {
        if (!preview.isConnected) return;
        const image = document.createElement('img');
        image.src = asset.url;
        image.alt = attachment.label || attachment.name || '对话图片';
        image.loading = 'lazy';
        image.onclick = () => openAttachmentPreview(hostId, threadId, attachment);
        preview.replaceChildren(image);
      }).catch(error => {
        if (!preview.isConnected) return;
        loading.textContent = `无法在线预览：${error.message}`;
      });
    } else if (attachment.kind === 'image') {
      preview.textContent = attachment.error || '图片较大，请下载查看';
    } else {
      preview.textContent = '↓';
    }
  }
  target.append(list);
}

function updateMobileBackdrop() {
  const mobile = mobileQuery.matches;
  const open = mobile ? document.body.classList.contains('sidebar-open') : !sidebarCollapsed;
  document.body.classList.toggle('sidebar-collapsed', !mobile && sidebarCollapsed);
  $('mobileBackdrop').hidden = !mobile || !open;
  // The drawer is modal on phones: a backdrop alone does not stop background
  // scrollers or keyboard focus (especially iOS rubber-band scroll chaining).
  document.querySelector('#workspace > main').inert = mobile && open;
  $('terminalPanel').inert = mobile && open;
  $('menu').setAttribute('aria-expanded', String(open));
  $('menu').setAttribute('aria-label', open ? '隐藏侧栏' : '显示侧栏');
  $('menu').title = open ? '隐藏侧栏' : '显示侧栏';
  $('menu').querySelector('.sidebar-toggle-icon').textContent = mobile && open ? '×' : '☰';
  $('menu').querySelector('.sidebar-toggle-label').textContent = open ? '隐藏侧栏' : '显示侧栏';
}

function closeMobilePanels() {
  document.body.classList.remove('sidebar-open');
  updateMobileBackdrop();
}

function syncMobileLayout() {
  document.body.classList.toggle('mobile-layout', mobileQuery.matches);
  if (terminalPanelOpen) $('terminalResizer').hidden = mobileQuery.matches;
  applyFileTreeLayout();
  if (!mobileQuery.matches) closeMobilePanels();
  else updateMobileBackdrop();
  fitActiveTerminal();
}

function installMobileSidebarScrollGuard() {
  let finger = null;
  const locked = () => mobileQuery.matches && document.body.classList.contains('sidebar-open');
  const surfaces = '#sidebar, .thread-context-menu, .project-context-menu';
  const reset = () => {finger = null;};
  document.addEventListener('touchstart', event => {
    reset();
    if (!locked() || event.touches.length !== 1) return;
    const touch = event.touches[0];
    finger = {id: touch.identifier, y: touch.clientY};
  }, {passive: true, capture: true});
  document.addEventListener('touchmove', event => {
    if (!locked() || event.touches.length !== 1 || !finger) return;
    const touch = [...event.touches].find(item => item.identifier === finger.id);
    if (!touch) return;
    const dy = touch.clientY - finger.y;
    finger.y = touch.clientY;
    const target = event.target instanceof Element ? event.target : null;
    // A foreground modal opened from the sidebar retains its own scrolling.
    if (target?.closest('dialog[open]')) return;
    const surface = target?.closest(surfaces);
    if (surface) {
      if (target.closest('input, select, [contenteditable]:not([contenteditable="false"])')) return;
      if (!dy) return;
      for (let element = target; element; element = element.parentElement) {
        const style = getComputedStyle(element);
        const max = element.scrollHeight - element.clientHeight;
        if (max > 1 && /^(auto|scroll)$/.test(style.overflowY) &&
            ((dy > 0 && element.scrollTop > 0) || (dy < 0 && element.scrollTop < max - 1))) return;
        if (element === surface) break;
      }
    }
    // No drawer scroller can consume this movement: contain it rather than
    // passing it to the conversation/body. Also suppress swipe-generated taps.
    if (event.cancelable) event.preventDefault();
  }, {passive: false, capture: true});
  document.addEventListener('touchend', reset, {passive: true});
  document.addEventListener('touchcancel', reset, {passive: true});
  mobileQuery.addEventListener('change', reset);
  window.addEventListener('blur', reset);
}

function installMobileSidebarGesture() {
  const edgeWidth = 28, openDistance = 64;
  let gesture = null;
  const available = () => mobileQuery.matches && !$('workspace').hidden &&
    !document.body.classList.contains('sidebar-open') &&
    !document.querySelector('dialog[open]') && window.getSelection()?.isCollapsed !== false;
  const ignored = 'input, textarea, select, button, a, [role="slider"], ' +
    '[contenteditable]:not([contenteditable="false"]), .pane-resizer, ' +
    '#composer, #notice, #terminalPanel, .xterm, pre, table, .katex-display, ' +
    '.thread-context-menu, .project-context-menu, .file-context-menu';
  const reset = () => {gesture = null;};
  document.addEventListener('touchstart', event => {
    reset();
    if (!available() || event.defaultPrevented || event.touches.length !== 1 ||
        !(event.target instanceof Element) || event.target.closest(ignored)) return;
    const touch = event.touches[0];
    if (touch.clientX < 0 || touch.clientX > edgeWidth) return;
    gesture = {id: touch.identifier, x: touch.clientX, y: touch.clientY,
      started: performance.now(), horizontal: false};
  }, {passive: true});
  document.addEventListener('touchmove', event => {
    if (!gesture) return;
    if (!available() || event.touches.length !== 1 || performance.now() - gesture.started > 1200) {
      reset(); return;
    }
    const touch = [...event.touches].find(item => item.identifier === gesture.id);
    if (!touch) {reset(); return;}
    const dx = touch.clientX - gesture.x, dy = Math.abs(touch.clientY - gesture.y);
    if (dx < -10 || (dy > 12 && dy > Math.abs(dx) * 0.85)) {reset(); return;}
    if (dx > 12 && dx > dy * 1.5) gesture.horizontal = true;
    if (gesture.horizontal && event.cancelable) event.preventDefault();
  }, {passive: false});
  document.addEventListener('touchend', event => {
    const finished = gesture;
    reset();
    if (!finished || !finished.horizontal || !available() || event.touches.length ||
        performance.now() - finished.started > 1200) return;
    const touch = [...event.changedTouches].find(item => item.identifier === finished.id);
    if (!touch) return;
    const dx = touch.clientX - finished.x, dy = Math.abs(touch.clientY - finished.y);
    if (dx < openDistance || dy > Math.max(32, dx * 0.45)) return;
    if (event.cancelable) event.preventDefault();
    document.body.classList.add('sidebar-open');
    updateMobileBackdrop();
  }, {passive: false});
  document.addEventListener('touchcancel', reset, {passive: true});
  mobileQuery.addEventListener('change', reset);
  window.addEventListener('blur', reset);
}

function syncSidebarSelectors() {
  const selectedHost = currentHost();
  if (selectedHost && !workspaceMode) selectedApplicationId = applicationId(selectedHost);
  setOptions($('application'), [{value: WORKSPACE_DEVICE_ID, label: `工作区 (${workspaceThreads.length})`},
    ...[...new Set(['codex', 'deepseek', ...catalog.map(applicationId)])].map(id => ({
    value: id, label: APPLICATION_LABELS[id] || id
  }))], workspaceMode ? WORKSPACE_DEVICE_ID : selectedApplicationId);
  const available = applicationHosts();
  if (!available.some(host => host.id === selectedHostId) && !workspaceMode) {
    selectedHostId = available[0]?.id || '';
  }
  setOptions($('device'), available.map(host => ({
    value: host.id,
    label: `${host.label} · ${uiText(host.online ? '在线' : '离线')}`, rawLabel: true
  })), selectedHostId);
  $('device').hidden = workspaceMode;
  $('deviceSidebarLabel').hidden = workspaceMode;

  $('projectSidebarControls').hidden = workspaceMode;
  $('projectSidebarLabel').hidden = workspaceMode;
  $('newThread').hidden = workspaceMode;

  const host = currentHost();
  const projectItems = [
    {value: 'all', label: '全部项目'},
    {value: 'unassigned', label: '未分类'}
  ];
  for (const project of host?.projects || []) {
    projectItems.push({
      value: project.id,
      label: `${project.name} (${project.threadCount || 0})`
    });
  }
  if (!projectItems.some(item => item.value === projectFilter)) projectFilter = 'all';
  setOptions($('projectFilter'), projectItems, projectFilter);
  $('projectCount').textContent = host ? `${host.projects.length} 个` : '';
  $('newThread').disabled = busy || !host?.online || !host?.desktopConnected;
  $('newThread').hidden ||= !supportsAction(host, 'new_thread');
  $('addProject').hidden = !supportsAction(host, 'project_create');
  $('addProject').disabled = busy || !host?.online;
  ;
  ;
  $('codexUpdatesToggle').hidden = !host || applicationId(host) !== 'codex';
  $('codexUpdatesToggle').disabled = !host?.online;
  if ($('codexUpdatesDialog').open) {
    if ($('codexUpdatesToggle').hidden) $('codexUpdatesDialog').close();
    else renderCodexUpdates(host);
  }
  
  $('nativeSettingsToggle').hidden = !host || host.capabilities?.nativeSettings === false;
  if (terminalPanelOpen && terminalView === 'agents') renderSubagents();
  window.NativePanel?.syncCapabilities();
  $('archiveFilter').value = archiveFilter;
  syncTerminalControls();
}

function appendThread(parent, host, thread, showHost = false) {
  const row = document.createElement('div');
  row.className = 'thread-row-wrap';
  row.dataset.hostId = host.id;
  row.dataset.threadId = thread.id;
  if (showHost) {
    const handle = document.createElement('button');
    handle.type = 'button';
    handle.className = 'project-drag-handle workspace-drag-handle';
    handle.textContent = '⠇⠇';
    handle.disabled = workspaceUpdateBusy;
    handle.draggable = !workspaceUpdateBusy;
    handle.title = `拖动调整 ${plainTitle(thread.title)} 顺序，也可点击上移/下移`;
    handle.setAttribute('aria-label', handle.title);
    handle.onclick = event => {
      event.stopPropagation();
      const rect = handle.getBoundingClientRect();
      showThreadContextMenu(host.id, thread.id, rect.left, rect.bottom + 4);
    };
    const acceptsDrop = () => workspaceMode && !workspaceUpdateBusy && draggingWorkspaceThread &&
      !(draggingWorkspaceThread.host === host.id && draggingWorkspaceThread.thread === thread.id);
    const clearDrop = () => row.classList.remove('workspace-drop-before', 'workspace-drop-after');
    const dropsAfter = event => {
      const rect = row.getBoundingClientRect();
      return event.clientY > rect.top + rect.height / 2;
    };
    handle.ondragstart = event => {
      if (workspaceUpdateBusy) { event.preventDefault(); return; }
      closeThreadContextMenu();
      draggingWorkspaceThread = {host: host.id, thread: thread.id};
      row.classList.add('workspace-dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', JSON.stringify(draggingWorkspaceThread));
    };
    handle.ondragend = () => {
      draggingWorkspaceThread = null;
      document.querySelectorAll('.thread-row-wrap').forEach(element =>
        element.classList.remove('workspace-dragging', 'workspace-drop-before', 'workspace-drop-after'));
    };
    row.ondragover = event => {
      if (!acceptsDrop()) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      clearDrop();
      row.classList.add(dropsAfter(event) ? 'workspace-drop-after' : 'workspace-drop-before');
    };
    row.ondragleave = event => { if (!row.contains(event.relatedTarget)) clearDrop(); };
    row.ondrop = event => {
      clearDrop();
      if (!acceptsDrop()) return;
      event.preventDefault();
      const source = draggingWorkspaceThread;
      draggingWorkspaceThread = null;
      void moveWorkspaceThread(source, {host: host.id, thread: thread.id}, dropsAfter(event));
    };
    row.append(handle);
  }
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'thread-row' +
    (selected?.host === host.id && selected?.thread === thread.id ? ' selected' : '');
  button.disabled = thread.unavailable === true;

  const title = document.createElement('span');
  title.className = 'thread-name';
  title.textContent = plainTitle(thread.title);
  const titleLine = document.createElement('span');
  titleLine.className = 'thread-title-line';
  titleLine.append(title);
  if (thread.isPinned) {
    const pin = document.createElement('span');
    pin.className = 'thread-clock';
    pin.textContent = '★';
    pin.title = '原 App 已固定';
    pin.setAttribute('aria-label', '原 App 已固定');
    titleLine.append(pin);
  }
  if (isWorkspaceThread(host.id, thread.id)) {
    const mark = document.createElement('span');
    mark.className = 'thread-workspace-mark';
    mark.setAttribute('aria-label', '已加入工作区');
    mark.title = '已加入工作区';
    mark.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>';
    titleLine.append(mark);
  }
  const activeAutomations = activeThreadAutomations(thread);
  if (activeAutomations.length) {
    const clock = document.createElement('span');
    clock.className = 'thread-clock';
    clock.setAttribute('aria-label', '活跃的定时任务');
    clock.title = activeAutomations.map(item => `${item.name}（运行中）`).join('\n');
    clock.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/></svg>';
    titleLine.append(clock);
  }

  const meta = document.createElement('span');
  meta.className = 'thread-meta';
  const status = document.createElement('span');
  status.textContent = thread.unavailable ? '暂不可用' : showHost && !host.online ? '离线' : thread.archived ? '已归档' :
    nativeThreadStatusName(thread);
  if (thread.pendingRequests > 0) status.className = 'pending';
  else if (thread.status === 'active') status.className = 'running';
  else if (thread.unread === true) status.className = 'unread';
  else if (thread.unread === false) status.className = 'read';
  const detail = document.createElement('span');
  if (showHost) {
    meta.classList.add('workspace-thread-state');
    detail.className = 'workspace-thread-application';
    detail.textContent = applicationShortName(host);
    detail.title = APPLICATION_LABELS[applicationId(host)] || applicationId(host);
    meta.append(status, document.createTextNode('·'), detail);
    const source = document.createElement('span');
    source.className = 'thread-meta workspace-thread-source';
    const computer = document.createElement('span');
    computer.className = 'workspace-thread-computer';
    computer.textContent = host.label; computer.title = host.label;
    const project = document.createElement('span');
    project.className = 'workspace-thread-project';
    project.textContent = thread.projectName || '未分类'; project.title = project.textContent;
    source.append(computer, document.createTextNode('·'), project);
    button.append(titleLine, meta, source);
  } else {
    detail.textContent = modelDisplayName(host, thread.model) || thread.projectName || APPLICATION_LABELS[applicationId(host)] || applicationId(host);
    detail.title = thread.model || detail.textContent;
    meta.append(status, document.createTextNode('·'), detail);
    button.append(titleLine, meta);
  }
  let longPressAt = 0;
  button.onclick = event => {
    if (Date.now() - longPressAt < 900) {
      event.preventDefault();
      return;
    }
    selectThread(host, thread, false, event.isTrusted);
  };
  button.oncontextmenu = event => {
    event.preventDefault();
    showThreadContextMenu(host.id, thread.id, event.clientX, event.clientY);
  };
  const more = document.createElement('button');
  more.type = 'button';
  more.className = 'thread-more';
  more.textContent = '⋯';
  more.setAttribute('aria-label', `对话操作：${plainTitle(thread.title)}`);
  more.title = '对话操作';
  more.onclick = event => {
    event.stopPropagation();
    const rect = more.getBoundingClientRect();
    showThreadContextMenu(host.id, thread.id, rect.left, rect.bottom + 4);
  };
  more.oncontextmenu = event => {
    event.preventDefault();
    showThreadContextMenu(host.id, thread.id, event.clientX, event.clientY);
  };
  let press = null;
  button.addEventListener('touchstart', event => {
    if (press) clearTimeout(press.timer);
    if (event.touches.length !== 1) return;
    const touch = event.touches[0];
    const x = touch.clientX;
    const y = touch.clientY;
    press = {x, y, timer: setTimeout(() => {
      showThreadContextMenu(host.id, thread.id, x, y);
      longPressAt = Date.now();
      press = null;
    }, 550)};
  }, {passive: true});
  const cancelPress = () => {
    if (press) clearTimeout(press.timer);
    press = null;
  };
  button.addEventListener('touchmove', event => {
    if (press && event.touches.length === 1 &&
        Math.hypot(event.touches[0].clientX - press.x,
          event.touches[0].clientY - press.y) > 12) cancelPress();
  }, {passive: true});
  button.addEventListener('touchend', cancelPress, {passive: true});
  button.addEventListener('touchcancel', cancelPress, {passive: true});
  row.append(button, more);
  parent.append(row);
}

function threadContextTarget() {
  return sidebarThreadTarget(threadContext?.hostId, threadContext?.threadId);
}

function closeThreadContextMenu() {
  $('threadContextMenu').hidden = true;
  threadContext = null;
}

function positionThreadContextMenu(x, y) {
  const menu = $('threadContextMenu');
  const viewport = window.visualViewport;
  const left = viewport?.offsetLeft || 0;
  const top = viewport?.offsetTop || 0;
  const width = viewport?.width || window.innerWidth;
  const height = viewport?.height || window.innerHeight;
  menu.style.left = `${Math.max(left + 8, Math.min(x, left + width - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(top + 8, Math.min(y, top + height - menu.offsetHeight - 8))}px`;
}

function showThreadContextMenu(hostId, threadId, x, y) {
  const {host, thread} = sidebarThreadTarget(hostId, threadId);
  if (!host || !thread) return;
  closeFileContextMenu();
  closeTerminalContextMenu();
  closeProjectContextMenu();
  threadContext = {hostId, threadId};
  $('threadContextTarget').textContent = plainTitle(thread.title);
  $('threadContextTarget').title = plainTitle(thread.title);
  $('refreshThreadMenu').disabled = !host.online || thread.unavailable || manualThreadRefreshes.has(threadKey(hostId, threadId));
  setOptions($('moveProject'), [...(host.capabilities?.moveToUnassigned === false ? [] : [{value: '', label: '未分类'}]),
    ...(host.projects || []).map(project => ({value: project.id, label: project.name}))],
  thread.projectId || '');
  $('workspaceThreadToggle').textContent = isWorkspaceThread(hostId, threadId) ?
    '移出工作区' : '添加到工作区';
  $('workspaceThreadToggle').disabled = workspaceUpdateBusy;
  $('workspaceThreadOrigin').hidden = !workspaceMode;
  $('workspaceThreadOrigin').disabled = Boolean(thread.unavailable);
  syncWorkspaceOrderControls();
  $('moveProject').disabled = busy || !host.online || thread.unavailable;
  $('moveProject').disabled ||= !supportsAction(host, 'move_project');
  $('moveProject').hidden = !supportsAction(host, 'move_project');
  document.querySelector('label[for="moveProject"]').hidden = $('moveProject').hidden;
  $('renameThread').hidden = !supportsAction(host, 'rename_thread');
  $('archive').hidden = !supportsAction(host, thread.archived ? 'unarchive' : 'archive');
  $('renameThread').disabled = busy || !host.online || thread.unavailable;
  $('archive').textContent = thread.archived ? '取消归档' : '归档';
  $('archive').disabled = busy || !host.online || thread.unavailable ||
    (!thread.archived && thread.status === 'active');
  $('copyThreadCwd').disabled = !thread.cwd;
  $('copyThreadLink').hidden = applicationId(host) !== 'codex' || host.capabilities?.nativeDeepLinks === false;
  $('copyThreadLink').disabled = !thread.id;
  $('copyThreadMarkdown').disabled = busy || !host.online || thread.unavailable;
  $('threadContextMenu').hidden = false;
  positionThreadContextMenu(x, y);
}

function projectContextTarget() {
  const host = catalog.find(item => item.id === projectContext?.hostId);
  const project = host?.projects.find(item => item.id === projectContext?.projectId);
  return {host, project};
}

function closeProjectContextMenu() {
  $('projectContextMenu').hidden = true;
  projectContext = null;
}

function showProjectContextMenu(hostId, projectId, x, y) {
  const host = catalog.find(item => item.id === hostId);
  const project = host?.projects.find(item => item.id === projectId);
  if (!host || (projectId !== 'unassigned' && !project)) return;
  closeFileContextMenu();
  closeTerminalContextMenu();
  closeThreadContextMenu();
  projectContext = {hostId, projectId};
  $('projectContextTarget').textContent = project?.name || '未分类';
  $('projectMenuNewThread').disabled = busy || !host.online || !host.desktopConnected;
  $('projectMenuRename').disabled = busy || !host.online || !project;
  $('projectMenuDirectories').disabled = !project;
  $('projectMenuDirectories').disabled ||= applicationId(host) !== 'codex';
  $('projectMenuDirectories').hidden = host.capabilities?.projectRoots === false || applicationId(host) !== 'codex';
  $('projectMenuNewThread').hidden = !supportsAction(host, 'new_thread');
  $('projectMenuRename').hidden = !supportsAction(host, 'project_rename');
  $('projectMenuDelete').hidden = !supportsAction(host, 'project_delete');
  $('projectMenuOpenDirectory').disabled = !project?.roots?.[0] || !host.terminal?.supported;
  $('projectMenuDelete').disabled = busy || !host.online || !project;
  $('projectContextMenu').hidden = false;
  const menu = $('projectContextMenu');
  const viewport = window.visualViewport;
  const left = viewport?.offsetLeft || 0;
  const top = viewport?.offsetTop || 0;
  const width = viewport?.width || window.innerWidth;
  const height = viewport?.height || window.innerHeight;
  menu.style.left = `${Math.max(left + 8, Math.min(x, left + width - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(top + 8, Math.min(y, top + height - menu.offsetHeight - 8))}px`;
}

function activeThreadAutomations(thread) {
  return (thread.automations || []).filter(item => item.status === 'ACTIVE');
}

function renderProjectRootsDraft() {
  if (!projectRootsDraft) return;
  const host = catalog.find(item => item.id === projectRootsDraft.host);
  const editable = Boolean(host?.online && !busy);
  const list = $('projectRootsList');
  list.replaceChildren();
  if (!projectRootsDraft.roots.length) {
    const empty = document.createElement('p');
    empty.textContent = '此项目未关联目录。文件管理器将使用当前对话的工作目录。';
    list.append(empty);
  }
  projectRootsDraft.roots.forEach((path, index) => {
    const row = document.createElement('div');
    row.className = 'project-root-row';
    const label = document.createElement('div');
    label.className = 'project-root-label';
    label.textContent = index === 0 ? '默认目录' : `附加目录 ${index}`;
    const input = document.createElement('input');
    input.value = path;
    input.required = true;
    input.maxLength = 4096;
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.setAttribute('aria-label', `项目目录 ${index + 1}`);
    input.disabled = !editable;
    input.oninput = () => {projectRootsDraft.roots[index] = input.value;};
    const actions = document.createElement('div');
    actions.className = 'project-root-actions';
    const primary = document.createElement('button');
    primary.type = 'button'; primary.className = 'secondary';
    primary.textContent = index === 0 ? '默认' : '设为默认';
    primary.disabled = !editable || index === 0;
    primary.onclick = () => {
      const [root] = projectRootsDraft.roots.splice(index, 1);
      projectRootsDraft.roots.unshift(root);
      renderProjectRootsDraft();
    };
    const open = document.createElement('button');
    open.type = 'button'; open.className = 'secondary'; open.textContent = '打开目录';
    open.disabled = busy || !host?.terminal?.supported;
    open.onclick = () => {
      const root = projectRootsDraft.roots[index].trim();
      if (!root) return;
      if (JSON.stringify(projectRootsDraft.roots) !== JSON.stringify(projectRootsDraft.expectedRoots) &&
          !window.confirm('项目目录的修改尚未保存。放弃修改并打开目录？')) return;
      $('projectRootsDialog').close();
      openProjectFileDirectory(projectRootsDraft.host, root);
      projectRootsDraft = null;
    };
    const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'secondary'; remove.textContent = '移除关联';
    remove.disabled = !editable;
    remove.onclick = () => {projectRootsDraft.roots.splice(index, 1); renderProjectRootsDraft();};
    actions.append(primary, open, remove);
    row.append(label, input, actions);
    list.append(row);
  });
  $('projectRootsAdd').disabled = !editable || projectRootsDraft.roots.length >= 64;
  $('projectRootsSave').disabled = !editable;
  $('projectRootsCancel').disabled = busy;
}

function openProjectRootsDialog(host, project) {
  projectRootsDraft = {host: host.id, projectId: project.id,
    roots: [...(project.roots || [])], expectedRoots: [...(project.roots || [])]};
  $('projectRootsHint').textContent = `${host.label} · ${project.name}${host.online ? '' : ' · 离线，仅查看缓存目录'}`;
  $('projectRootsError').textContent = '';
  renderProjectRootsDraft();
  $('projectRootsDialog').showModal();
}

function appendProjectGroup(host, group, threads, searching) {
  const section = document.createElement('section');
  section.className = 'project-group';
  section.dataset.projectId = group.id;
  section.ondragover = event => {
    if (!draggingProject || draggingProject.host !== host.id ||
        draggingProject.project === group.id) return;
    event.preventDefault();
    section.classList.add('drop-target');
  };
  section.ondragleave = event => {
    if (!section.contains(event.relatedTarget)) section.classList.remove('drop-target');
  };
  section.ondrop = event => {
    event.preventDefault();
    section.classList.remove('drop-target');
    if (!draggingProject || draggingProject.host !== host.id) return;
    const rect = section.getBoundingClientRect();
    moveProjectBefore(host.id, draggingProject.project, group.id,
      event.clientY > rect.top + rect.height / 2);
  };

  const heading = document.createElement('div');
  heading.className = 'project-heading';
  const dragHandle = document.createElement('span');
  dragHandle.className = 'project-drag-handle';
  dragHandle.textContent = '⠇⠇';
  dragHandle.draggable = true;
  dragHandle.title = `拖动调整 ${group.name} 顺序`;
  dragHandle.setAttribute('aria-label', dragHandle.title);
  dragHandle.ondragstart = event => {
    draggingProject = {host: host.id, project: group.id};
    section.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', group.id);
  };
  dragHandle.ondragend = () => {
    draggingProject = null;
    document.querySelectorAll('.project-group').forEach(element =>
      element.classList.remove('dragging', 'drop-target'));
  };
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'project-toggle';
  const expanded = searching || expandedGroups.has(groupKey(host.id, group.id));
  toggle.setAttribute('aria-expanded', String(expanded));
  toggle.title = expanded ? `折叠 ${group.name}` : `展开 ${group.name}`;

  const chevron = document.createElement('span');
  chevron.className = 'project-chevron';
  chevron.textContent = expanded ? '⌄' : '›';
  const title = document.createElement('span');
  title.className = 'project-title';
  title.textContent = group.name;
  const total = document.createElement('span');
  total.className = 'project-total';
  total.textContent = String(threads.length);
  toggle.append(chevron, title, total);
  let longPressAt = 0;
  toggle.onclick = event => {
    if (Date.now() - longPressAt < 900) {
      event.preventDefault();
      return;
    }
    setGroupExpanded(host.id, group.id, !expandedGroups.has(groupKey(host.id, group.id)));
    lastCatalog = '';
    renderCatalog();
  };
  toggle.oncontextmenu = event => {
    event.preventDefault();
    showProjectContextMenu(host.id, group.id, event.clientX, event.clientY);
  };
  let press = null;
  const cancelPress = () => {
    if (press) clearTimeout(press.timer);
    press = null;
  };
  toggle.addEventListener('touchstart', event => {
    cancelPress();
    if (event.touches.length !== 1) return;
    const touch = event.touches[0];
    const x = touch.clientX;
    const y = touch.clientY;
    press = {x, y, timer: setTimeout(() => {
      showProjectContextMenu(host.id, group.id, x, y);
      longPressAt = Date.now();
      press = null;
    }, 550)};
  }, {passive: true});
  toggle.addEventListener('touchmove', event => {
    if (press && event.touches.length === 1 &&
        Math.hypot(event.touches[0].clientX - press.x,
          event.touches[0].clientY - press.y) > 12) cancelPress();
  }, {passive: true});
  toggle.addEventListener('touchend', cancelPress, {passive: true});
  toggle.addEventListener('touchcancel', cancelPress, {passive: true});

  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'project-new-thread';
  add.textContent = '＋';
  add.title = `在 ${group.name} 中新建对话`;
  add.setAttribute('aria-label', `在 ${group.name} 中新建对话`);
  add.disabled = busy || !host.online || !host.desktopConnected;
  add.onclick = () => openNewThreadDialog(group.id);
  heading.append(dragHandle, toggle, add);
  const more = document.createElement('button');
  more.type = 'button';
  more.className = 'project-delete';
  more.textContent = '•••';
  more.title = `项目操作：${group.name}`;
  more.setAttribute('aria-label', more.title);
  more.onclick = event => {
    event.stopPropagation();
    const rect = more.getBoundingClientRect();
    showProjectContextMenu(host.id, group.id, rect.left, rect.bottom + 4);
  };
  heading.append(more);

  const body = document.createElement('div');
  body.className = 'project-threads';
  body.hidden = !expanded;
  if (threads.length) {
    for (const thread of threads) appendThread(body, host, thread);
  } else {
    const empty = document.createElement('p');
    empty.className = 'project-empty';
    empty.textContent = '暂无对话';
    body.append(empty);
  }
  section.append(heading, body);
  $('catalog').append(section);
}

function renderCatalog({preload = true} = {}) {
  syncProjectLocator();
  syncSidebarSelectors();
  const host = currentHost();
  const workspaceTargets = workspaceMode ? workspaceThreadTargets() : null;
  const signature = JSON.stringify([
    workspaceMode, workspaceTargets?.map(({host: sourceHost, thread}) =>
      ({host: sourceHost.id, label: sourceHost.label, online: sourceHost.online, thread})) ||
      (host && {id: host.id, online: host.online, projects: host.projects, threads: host.threads}),
    workspaceThreads.map(item => [item.host, item.thread]),
    $('search').value, projectFilter, archiveFilter, selected, busy
  ]);
  if (signature === lastCatalog) return;
  lastCatalog = signature;
  $('catalog').replaceChildren();

  if (workspaceMode) {
    const visible = workspaceTargets.filter(item => workspaceThreadMatches(item.host, item.thread));
    for (const {host: sourceHost, thread} of visible) appendThread($('catalog'), sourceHost, thread, true);
    if (!visible.length) {
      const p = document.createElement('p');
      p.className = 'history-note';
      p.textContent = workspaceThreads.length ? '这个范围内没有匹配的工作区对话' :
        '右键一条对话，选择“添加到工作区”，即可集中查看不同应用和电脑的对话。';
      $('catalog').append(p);
    }
    restoreCatalogScroll(WORKSPACE_DEVICE_ID);
    syncWorkspaceOrderControls();
    if (preload) scheduleThreadPreload();
    return;
  }

  if (!host) {
    const p = document.createElement('p');
    p.className = 'history-note';
    p.textContent = '等待设备连接…';
    $('catalog').append(p);
    return;
  }

  const filter = $('search').value.trim().toLowerCase();
  const visible = host.threads.filter(thread => {
    if (thread.isSubagent === true) return false;
    if (archiveFilter === 'active' && thread.archived) return false;
    if (archiveFilter === 'archived' && !thread.archived) return false;
    const pid = thread.projectId || 'unassigned';
    if (projectFilter !== 'all' && pid !== projectFilter) return false;
    return !filter || `${thread.title} ${thread.cwd} ${thread.projectName} ${thread.model}`
      .toLowerCase().includes(filter);
  });

  const groups = orderedProjectGroups(host, [...(host.projects || []).map(project => ({
    id: project.id, name: project.name
  })), {id: 'unassigned', name: '未分类'}]);
  const shownGroups = projectFilter === 'all' ? groups :
    groups.filter(group => group.id === projectFilter);
  let renderedGroups = 0;
  for (const group of shownGroups) {
    const threads = visible.filter(thread => (thread.projectId || 'unassigned') === group.id)
      .sort((a, b) => Number(Boolean(b.isPinned)) - Number(Boolean(a.isPinned)));
    if (filter && !threads.length) continue;
    appendProjectGroup(host, group, threads, Boolean(filter));
    renderedGroups += 1;
  }

  if (!renderedGroups) {
    const p = document.createElement('p');
    p.className = 'history-note';
    p.textContent = filter ? '没有找到匹配的对话' :
      archiveFilter === 'archived' ? '这个范围内没有已归档对话' : '这个范围内没有活跃对话';
    $('catalog').append(p);
  }
  restoreCatalogScroll(host.id);
  if (preload) scheduleThreadPreload();
}

function clearThread() {
  clearThreadReadAttention();
  threadReadSnapshot = null;
  clearAssetCache();
  selected = null;
  current = null;
  syncProjectLocator();
  threadPreviewOnly = false;
  threadWatchPending = false;
  rendered.clear();
  threadRenderSignature = '';
  queueRenderSignature = '';
  requestRenderSignature = '';
  editingQueuedMessageId = '';
  modelDirty = false;
  permissionDirty = false;
  pendingFiles = [];
  renderUploadTray();
  $('timeline').replaceChildren();
  const empty = document.createElement('div');
  empty.className = 'empty';
  const icon = document.createElement('div');
  icon.className = 'empty-icon';
  icon.textContent = '◈';
  const h = document.createElement('h2');
  h.textContent = '选择一条对话';
  const p = document.createElement('p');
  p.textContent = '项目分类、历史内容和实时运行状态会显示在这里。';
  empty.append(icon, h, p);
  $('timeline').append(empty);
  $('title').textContent = uiText('选择一个对话');
  $('subtitle').textContent = uiText('从左侧选择项目和对话');
  $('status').textContent = '等待选择';
  $('status').className = 'status';
  $('sendResult').textContent = '';
  $('sendResult').className = '';
  $('notice').hidden = true;
  $('notice').replaceChildren();
  clearQuestionDock();
  $('message').disabled = true;
  $('addAttachment').disabled = true;
  syncCopiedFileButton();
  $('send').disabled = true;
  $('send').hidden = false;
  $('stopTurn').hidden = true;
  $('queuedMessages').replaceChildren();
  $('queuedMessages').hidden = true;
  $('attachThread').hidden = true;
  $('modelSettingsToggle').hidden = true;
  $('quickSpeedControl').hidden = true;
  $('fullAccess').closest('label').hidden = true;
  $('fullAccess').disabled = true;
  setModelSettingsOpen(false);
  $('jumpBottom').hidden = true;
  closeThreadContextMenu();
  closeProjectContextMenu();
}

function clearThreadReadAttention() {
  threadReadAttention = '';
  clearTimeout(visibleReadTimer);
  visibleReadTimer = null;
}

function isReadingSelectedThread(hostId, threadId) {
  const timeline = $('timeline');
  const rect = timeline.getBoundingClientRect();
  return Boolean(selected?.host === hostId && selected?.thread === threadId &&
    threadReadAttention === threadKey(hostId, threadId) &&
    !document.hidden && document.hasFocus() && !$('workspace').hidden &&
    !threadPreviewOnly && !threadWatchPending && current?.id === threadId && current.online &&
    (current.items || []).some(item => !item.localDelivery && visibleScheduledItem(item)) &&
    !document.querySelector('dialog[open]') &&
    !(mobileQuery.matches && (terminalPanelOpen || document.body.classList.contains('sidebar-open'))) &&
    timeline.clientHeight > 0 && rect.width > 0 && rect.bottom > 0 &&
    rect.top < window.innerHeight && timelineNearBottom());
}

function shouldAutoMarkVisibleThreadRead(host, thread) {
  const key = host && thread ? threadKey(host.id, thread.id) : '';
  return Boolean(host?.online && host.desktopConnected && thread?.unread === true &&
    !thread.archived && isReadingSelectedThread(host.id, thread.id) &&
    threadReadSnapshot?.key === key && threadReadSnapshot.unread &&
    readAcknowledgedSnapshots.get(key) !== threadReadSnapshot.signature);
}

function scheduleVisibleThreadRead() {
  if (visibleReadTimer || !shouldAutoMarkVisibleThreadRead(currentHost(), selectedThread())) return;
  const target = {...selected};
  // A quick click-through or a briefly displayed cache preview is not reading.
  visibleReadTimer = setTimeout(() => {
    visibleReadTimer = null;
    if (selected?.host === target.host && selected?.thread === target.thread) {
      void markOpenedThreadRead(target);
    }
  }, 700);
}

async function markOpenedThreadRead(target) {
  const key = threadKey(target.host, target.thread);
  const host = catalog.find(item => item.id === target.host);
  const meta = host?.threads.find(item => item.id === target.thread);
  if (!shouldAutoMarkVisibleThreadRead(host, meta)) return;
  if (readMarksInFlight.has(key) || Date.now() < (readMarkRetryAfter.get(key) || 0)) return;
  const snapshot = {...threadReadSnapshot};
  readMarksInFlight.add(key);
  try {
    const result = await issueCommand('mark_read', {...target,
      payload: snapshot.receipt ? {readReceipt: snapshot.receipt} : {}});
    if (result?.read === false) {
      // The source changed after display/during acknowledgement. Do not hide
      // the new unread reply behind a successful acknowledgement of an old one.
      if (selected?.host === target.host && selected?.thread === target.thread) {
        await refreshThread(true);
      }
      scheduleCatalogRefresh();
      scheduleVisibleThreadRead();
      return;
    }
    readMarkRetryAfter.delete(key);
    readAcknowledgedSnapshots.set(key, snapshot.signature);
    while (readAcknowledgedSnapshots.size > 256) {
      readAcknowledgedSnapshots.delete(readAcknowledgedSnapshots.keys().next().value);
    }
    if (threadReadSnapshot?.signature === snapshot.signature &&
        isReadingSelectedThread(target.host, target.thread)) {
      const host = catalog.find(item => item.id === target.host);
      const meta = host?.threads.find(item => item.id === target.thread);
      if (meta) meta.unread = false;
      const known = knownThreadStatuses.get(key);
      if (known) knownThreadStatuses.set(key, {...known, unread: false});
      if (completionTarget?.host === target.host && completionTarget?.thread === target.thread) {
        $('completionNotice').hidden = true;
        completionTarget = null;
      }
      if (current) {
        current.unread = false;
        if (meta) renderNativeThreadStatus(meta, current, host, current.online);
      }
      renderCatalog();
    }
    // Do not hide future native unread events behind a time-based read hold.
    scheduleCatalogRefresh();
  } catch (error) {
    readMarkRetryAfter.set(key, Date.now() + 10000);
    if (isReadingSelectedThread(target.host, target.thread)) {
      $('sendResult').textContent = `同步已读失败：${error.message}`;
      $('sendResult').className = 'error';
    }
    renderCatalog();
    scheduleCatalogRefresh();
    setTimeout(() => {
      if (isReadingSelectedThread(target.host, target.thread)) scheduleVisibleThreadRead();
    }, 10000);
  } finally {
    readMarksInFlight.delete(key);
  }
}

async function selectThread(host, thread, revealSelection = false, userInitiated = false, options = {}) {
  clearThreadReadAttention();
  threadReadSnapshot = null;
  clearTimeout(preloadTimer);
  preloadTimer = null;
  preloadController?.abort();
  if (selected) {
    drafts.set(`${selected.host}/${selected.thread}`, $('message').value);
    attachmentDrafts.set(`${selected.host}/${selected.thread}`, [...pendingFiles]);
  }
  if (selectedHostId) captureHostNavigation(selectedHostId);
  const changingHost = selectedHostId !== host.id;
  clearAssetCache();
  selectedHostId = host.id;
  if (!workspaceMode) {
    if (changingHost) applyHostNavigation(host);
    if (changingHost || revealSelection) revealThreadInFilters(thread);
  }
  selected = {host: host.id, thread: thread.id};
  if (userInitiated) threadReadAttention = threadKey(host.id, thread.id);
  const selection = selected;
  const target = {...selected};
  if (completionTarget?.host === target.host && completionTarget?.thread === target.thread) {
    $('completionNotice').hidden = true;
    completionTarget = null;
  }
  setGroupExpanded(host.id, thread.projectId || 'unassigned', true);
  current = null;
  threadPreviewOnly = false;
  threadWatchPending = true;
  modelDirty = false;
  permissionDirty = false;
  setModelSettingsOpen(false);
  rendered.clear();
  threadRenderSignature = '';
  queueRenderSignature = '';
  requestRenderSignature = '';
  editingQueuedMessageId = '';
  $('timeline').replaceChildren();
  $('notice').hidden = true;
  $('notice').replaceChildren();
  clearQuestionDock();
  $('jumpBottom').hidden = true;
  $('message').value = drafts.get(`${host.id}/${thread.id}`) || '';
  pendingFiles = [...(attachmentDrafts.get(`${host.id}/${thread.id}`) || [])];
  renderUploadTray();
  $('sendResult').textContent = '';
  $('title').textContent = plainTitle(thread.title);
  $('subtitle').textContent = `${host.label} · ${thread.projectName || '未分类'} · ${thread.cwd || ''}`;
  $('message').disabled = true;
  $('send').disabled = true;
  $('send').hidden = false;
  $('stopTurn').hidden = true;
  $('queuedMessages').replaceChildren();
  $('queuedMessages').hidden = true;
  $('attachThread').hidden = true;
  $('status').textContent = '同步中…';
  // Selector changes restore a chat for context, but are not a chat-row tap.
  if (!options.keepSidebarOpen) closeMobilePanels();
  syncThreadControls();
  renderCatalog();
  captureHostNavigation(host.id);
  syncFileDirectoryContext();
  const cached = cachedThreadSnapshot(target, true);
  if (cached) renderThreadSnapshot(cached.thread, thread, target, false, true, cached.revision);
  try {
    // Reading the relay cache does not need to wait for an agent round trip.
    // Keep native watch semantics, but run confirmation and the first GET together.
    const watching = issueCommand('watch', target).then(() => null, error => error);
    let loaded = await refreshThread();
    const watchError = await watching;
    if (selected !== selection) return;
    threadWatchPending = false;
    if (watchError) throw watchError;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      if (selected?.host !== target.host || selected?.thread !== target.thread) return;
      loaded = await refreshThread() || loaded;
      if (loaded) break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    if (!loaded) throw new Error('目标机已确认读取，但对话快照尚未返回，请稍后重试');
    void ensureRecentHistory(target);
    scheduleThreadPreload();
  } catch (error) {
    if (selected === selection) {
      threadWatchPending = false;
      $('status').textContent = '同步失败';
      $('status').className = 'status offline';
      $('sendResult').textContent = error.message;
      $('sendResult').className = 'error';
    }
  }
}

async function branchFromItem(item, button) {
  if (busy || !selected) return;
  const target = {...selected};
  const keepInWorkspace = workspaceMode;
  const forkCommandId = uuid();
  busy = true;
  button.disabled = true;
  $('sendResult').className = '';
  $('sendResult').textContent = '正在从这条消息创建分支对话…';
  syncThreadControls();
  try {
    const result = await issueCommand('fork_thread', {
      ...target, commandId: forkCommandId, payload: {lastTurnId: item.turnId || '', nativeSeq: item.nativeSeq}
    });
    if (keepInWorkspace) {
      try {
        await updateWorkspaceMembership('add', target.host, result.threadId, {forkCommandId});
      } catch (error) {
        throw new Error(`分支已创建，但加入工作区失败：${error.message}；可在来源电脑的对话菜单中添加，不要重复创建分支。`);
      }
    }
    let branch = null;
    let host = null;
    for (let attempt = 0; attempt < 14 && !branch; attempt += 1) {
      await refresh();
      host = catalog.find(value => value.id === target.host) || null;
      branch = host?.threads.find(value => value.id === result.threadId) || null;
      if (!branch) await new Promise(resolve => setTimeout(resolve, 500));
    }
    if (host && branch) {
      setGroupExpanded(host.id, branch.projectId || 'unassigned', true);
      await selectThread(host, branch);
      $('sendResult').textContent = keepInWorkspace ? '✓ 已创建分支对话并加入工作区' : '✓ 已创建分支对话';
    } else {
      $('sendResult').textContent = keepInWorkspace ?
        '✓ 分支已加入工作区，正在同步对话内容' : '✓ 分支已创建，正在同步到会话列表';
    }
  } catch (error) {
    $('sendResult').textContent = error.message;
    $('sendResult').className = 'error';
  } finally {
    busy = false;
    button.disabled = false;
    lastCatalog = '';
    syncThreadControls();
    renderCatalog();
    try { await refreshThread(); } catch (_) { scheduleCatalogRefresh(); }
  }
}

function asyncQuestionKey(item) {
  return `${selected?.host || ''}/${selected?.thread || ''}/${item.turnId || ''}/${item.id}`;
}

async function submitAsyncChoice(item, answers, card) {
  if (!selected || !current?.owned || !current?.online || busy) return;
  const target = {...selected};
  const key = asyncQuestionKey(item);
  if (choiceBusy.has(key) || submittedChoices.has(key)) return;
  choiceBusy.add(key);
  const feedback = card.querySelector('.async-choice-feedback');
  card.querySelectorAll('button, input').forEach(control => control.disabled = true);
  feedback.className = 'async-choice-feedback';
  feedback.textContent = '正在把选择送到原对话…';
  try {
    const result = await issueCommand('answer_choice', {...target,
      payload: {itemId: item.id, answers,
        ...(item.questions.every(question => question.questionItemId) ?
          {questionIds: item.questions.map(question => question.questionItemId)} : {})}});
    submittedChoices.add(key);
    const ui = result.questionUi;
    feedback.textContent = ui?.status === 'closed' ? '✓ 已送达，原 App 提问框已收起' :
      ui?.status === 'unavailable' ? (ui.detail || '✓ 已送达；原 App 提问框联动未确认，不会重发答案') :
      ui?.status === 'awaiting-native-acceptance' ? '✓ 已送达，等待原 App 接受后收起提问框' : '✓ 已送达原对话';
    threadRenderSignature = '';
    if (selected?.host === target.host && selected?.thread === target.thread) {
      await refreshThread();
    }
  } catch (error) {
    feedback.className = 'async-choice-feedback error';
    feedback.textContent = error.message;
    if (/选项已过期|部分问题已在原 App 回答/.test(error.message)) {
      // A native answer can race an in-flight browser submit. Refresh state,
      // never re-enable stale choices or resend a possibly delivered answer.
      if (selected?.host === target.host && selected?.thread === target.thread) {
        try { await refreshThread(); } catch (_) {}
      }
    } else if (card.isConnected) {
      card.querySelectorAll('button, input').forEach(control => control.disabled = false);
    }
  } finally {
    choiceBusy.delete(key);
    if (current && selected?.host === target.host && selected?.thread === target.thread) renderQuestionDock(current);
  }
}

// Reconcile connected nodes in place. A detached/reinserted input loses focus
// and can cancel an IME composition even if its value and node are reused.
// If order changes, move its siblings around the focused subtree instead.
function finishQuestionPointerInteraction() {
  clearTimeout(questionPointerReleaseTimer);
  questionPointerReleaseTimer = null;
  questionPointerInteraction = null;
  const updates = [...questionInteractionRenders.values()];
  questionInteractionRenders.clear();
  for (const update of updates) update();
}

function deferQuestionInteractionRender(kind, callback) {
  const press = questionPointerInteraction;
  if (!press || press.key !== historyKey() || !press.node.isConnected) return false;
  const key = press.key;
  // Native state and receipts still update immediately. Only the two question
  // surfaces wait until this gesture's click/default action has completed.
  // Otherwise hydration can move the label between down/up and swallow the
  // user's very first choice, before there is a focused editor to preserve.
  questionInteractionRenders.set(kind, () => {
    if (selected && historyKey() === key) callback();
  });
  return true;
}

function reconcileConnectedChildren(parent, children) {
  const wanted = new Set(children);
  for (const child of [...parent.childNodes]) if (!wanted.has(child)) child.remove();
  let cursor = parent.firstChild;
  for (const child of children) {
    if (child === cursor) {
      cursor = cursor.nextSibling;
    } else if (child.parentNode === parent && child.contains(document.activeElement)) {
      while (cursor && cursor !== child) {
        const next = cursor.nextSibling;
        parent.insertBefore(cursor, child.nextSibling);
        cursor = next;
      }
      cursor = child.nextSibling;
    } else parent.insertBefore(child, cursor);
  }
}

function renderAsyncQuestions(container, item, state) {
  const questions = Array.isArray(item.questions) ? item.questions : [];
  let form = container.querySelector('.async-choice-card');
  if (!questions.length || state === 'past') {form?.remove(); return;}
  if (!form) {
    form = document.createElement('form');
    form.className = 'async-choice-card';
    form.choiceFields = new Map();
    form.heading = document.createElement('strong');
    form.feedback = document.createElement('p');
    form.feedback.className = 'async-choice-feedback';
    form.feedback.setAttribute('role', 'status');
    form.submit = document.createElement('button');
    form.submit.type = 'submit';
    form.submit.className = 'question-page-submit';
    form.submit.textContent = '提交选择';
    container.append(form);
  }
  const heading = form.heading;
  heading.textContent = questions.length === 1 ? '选择回答' : '请回答以下问题';
  const fields = [];
  const nextFields = new Map();
  for (const [index, question] of questions.entries()) {
    const key = question.questionItemId || `${item.id}/${question.title}/${index}`;
    let entry = form.choiceFields.get(key);
    if (!entry) {
      const field = document.createElement('fieldset');
      const legend = document.createElement('legend');
      const custom = document.createElement('input');
      custom.type = 'text';
      custom.maxLength = 4000;
      custom.placeholder = '也可输入自定义回答';
      custom.oninput = () => {
        if (custom.value.trim()) field.querySelectorAll('input[type="radio"]')
          .forEach(input => {input.checked = false;});
      };
      entry = {field, legend, custom, optionSignature: '', options: []};
    }
    const {field, legend, custom} = entry;
    field.dataset.questionKey = JSON.stringify(['async', item.turnId || '', item.id, key]);
    legend.setAttribute('data-i18n-ignore', '');
    legend.textContent = question.title;
    const optionSignature = JSON.stringify(question.options || []);
    if (entry.optionSignature !== optionSignature) {
      const checked = field.querySelector('input[type="radio"]:checked')?.value;
      entry.options = (question.options || []).map(option => {
        const label = document.createElement('label');
        label.className = 'async-choice-option';
        const input = document.createElement('input');
        input.type = 'radio';
        input.name = `choice-${item.id}-${key}`;
        input.value = option;
        input.checked = option === checked;
        input.onchange = () => {if (input.checked) custom.value = '';};
        const text = document.createElement('span');
        text.setAttribute('data-i18n-ignore', '');
        text.textContent = option;
        label.append(input, text);
        return label;
      });
      entry.optionSignature = optionSignature;
    }
    custom.setAttribute('aria-label', `${question.title}的自定义回答`);
    reconcileConnectedChildren(field, [legend, ...entry.options, custom]);
    fields.push({field, custom, question});
    nextFields.set(key, entry);
  }
  form.choiceFields = nextFields;
  const feedback = form.feedback;
  const disabled = state !== 'ready';
  for (const {field} of fields) field.querySelectorAll('input').forEach(input => {
    if (input.disabled !== disabled) input.disabled = disabled;
  });
  if (disabled) {
    feedback.textContent = state === 'submitted' ? '答案已提交，等待原 App 确认；不会重复发送。' :
      state === 'submitting' ? '正在把选择送到原对话…' :
      '接入目标机的原对话后可选择';
  } else if (form.choiceState && form.choiceState !== 'ready') {
    feedback.className = 'async-choice-feedback';
    feedback.textContent = '';
  }
  form.choiceState = state;
  reconcileConnectedChildren(form, [heading, ...fields.map(entry => entry.field),
    ...(state === 'ready' ? [form.submit] : []), feedback]);
  form.onsubmit = event => {
    event.preventDefault();
    if (state !== 'ready') return;
    const answers = [];
    for (const {field, custom, question} of fields) {
      const selectedOption = field.querySelector('input[type="radio"]:checked');
      const answer = custom.value.trim() || selectedOption?.value || '';
      if (!answer) {
        window.CRBQuestionDeck.reveal(field);
        feedback.className = 'async-choice-feedback error';
        feedback.textContent = `请回答「${question.title}」`;
        return;
      }
      answers.push(answer);
    }
    submitAsyncChoice(item, answers, form);
  };
}

function clearQuestionDock() {
  const dock = $('questionDock');
  dock.hidden = true;dock.replaceChildren();dock.questionPageKey = '';dock.onQuestionPageChange = null;
  dock.questionVisibilityState = null;
}

function renderQuestionDock(thread, items = combinedHistoryItems(thread)) {
  if (deferQuestionInteractionRender('questions', () => {
    if (current) renderQuestionDock(current);
  })) return;
  const dock = $('questionDock');
  const identity = historyKey();
  let session = questionDraftSessions.get(identity);
  if (!session) {
    session = {cards: new Map(), activeKey: ''};questionDraftSessions.set(identity, session);
    while (questionDraftSessions.size > 20) questionDraftSessions.delete(questionDraftSessions.keys().next().value);
  }
  dock.questionVisibilityState = session.visibility ||= {closed: false, skipped: new Set()};
  const body = window.CRBQuestionDeck.body(dock);
  const live = Boolean(!threadPreviewOnly && current?.id === thread.id && current.owned && current.online);
  const next = new Map();
  for (const [key, item] of pendingQuestionItems(thread, items)) {
    const answerKey = asyncQuestionKey(item);
    const record = session.cards.get('async/' + key) || document.createElement('article');
    renderAsyncQuestions(record, item, submittedChoices.has(answerKey) ? 'submitted' :
      choiceBusy.has(answerKey) ? 'submitting' : live && thread.source === 'desktop-live' ? 'ready' : 'offline');
    next.set('async/' + key, record);
  }
  for (const request of thread.requests || []) {
    if (request.method !== 'item/tool/requestUserInput') continue;
    const key = 'native/' + JSON.stringify([request.turnId, request.id]);
    const controls = {...request};
    for (const name of ['reason', 'message', 'autoResolutionMs']) delete controls[name];
    const signature = JSON.stringify(controls);
    let card = session.cards.get(key);
    if (!card || card.controlSignature !== signature) {
      card = document.createElement('article');card.className = 'request-card';card.dataset.requestKey = key;card.controlSignature = signature;
      card.questionReason = document.createElement('p');card.questionReason.setAttribute('data-i18n-ignore', '');card.append(card.questionReason);
      renderUserInputRequest(card, request);
      const feedback = document.createElement('p');feedback.className = 'request-feedback';feedback.setAttribute('role', 'status');card.append(feedback);
    }
    card.questionReason.textContent = request.reason || request.message || '';
    card.questionReason.hidden = !card.questionReason.textContent;
    const disabled = !live || request.desktopOnly || card.dataset.submitted === 'true' || requestBusy.has(`${selected?.host}/${selected?.thread}/${request.id}`);
    card.querySelectorAll('input, button').forEach(control => {control.disabled = Boolean(disabled);});
    next.set(key, card);
  }
  session.cards = next;reconcileConnectedChildren(body, [...next.values()]);
  dock.questionPageKey = session.activeKey;dock.onQuestionPageChange = key => {session.activeKey = key;};
  window.CRBQuestionDeck.update(dock);
}

function messageDate(item) {
  const value = item.timestamp ?? item.scheduledAt;
  let date = null;
  if (value !== null && value !== undefined && value !== '') {
    const numeric = typeof value === 'number' || /^\d+(?:\.\d+)?$/.test(String(value));
    date = new Date(numeric ? Number(value) * (Number(value) < 1e11 ? 1000 : 1) : value);
    if (!Number.isFinite(date.getTime()) || date.getFullYear() < 2000 || date.getFullYear() >= 2100) date = null;
  }
  return date;
}

function messageTimestamp(item) {
  const date = messageDate(item);
  const label = document.createElement('time');
  label.className = 'message-timestamp';
  if (!date) {
    label.textContent = '时间未知';
    label.title = '原消息没有保存时间记录';
    return label;
  }
  const pad = number => String(number).padStart(2, '0');
  label.dateTime = date.toISOString();
  label.textContent = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  if (item.timestampSource === 'turn') label.textContent = '≈ ' + label.textContent;
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  label.title = (item.timestampSource === 'turn' ? '原记录仅有本轮时间，非精确消息时间' :
    item.timestampSource === 'recorded' ? '原消息写入日志的时间' : '原消息时间') + ` · ${zone}`;
  return label;
}

function renderItem(item, choiceState = 'past') {
  const chat = ['userMessage', 'steeringUserMessage', 'agentMessage', 'assistantMessage']
    .includes(item.type);
  const reasoning = item.type === 'reasoning';
  const notice = item.type === 'contextCompaction' || item.type === 'systemNotice';
  const key = `${item.turnId || ''}/${item.id}`;
  const textBoxKey = `${selected?.host || ''}/${selected?.thread || ''}/${key}`;
  let record = rendered.get(key);
  if (!record) {
    const el = document.createElement(chat || reasoning ? 'article' : notice ? 'div' : 'details');
    const label = document.createElement(chat || reasoning || notice ? 'div' : 'summary');
    if (chat || reasoning) label.className = 'byline';
    const content = document.createElement('div');
    content.className = 'content';
    el.append(label, content);
    record = {el, label, content, signature: ''};
    rendered.set(key, record);
  }
  const editable = supportsAction(currentHost(), 'edit_last_user_turn') && item.type === 'userMessage' && !item.scheduledTrigger && current?.owned && current?.online &&
    current?.source === 'desktop-live' && current?.status !== 'active' &&
    current?.editableUserMessage?.turnId === item.turnId &&
    current?.editableUserMessage?.itemId === item.id;
  const signature = JSON.stringify([item, editable, choiceState, threadPreviewOnly,
    current?.owned, current?.online]);
  if (signature !== record.signature) {
    record.signature = signature;
    if (!editable && record.editPanel) {
      record.editPanel.remove();
      record.editPanel = null;
    }
    if (chat) {
      record.el.className = 'message';
      if (['userMessage', 'steeringUserMessage'].includes(item.type)) {
        record.el.classList.add('user');
      } else if (item.phase === 'commentary') {
        record.el.classList.add('progress');
      } else {
        record.el.classList.add('final');
      }
      const labelText = item.scheduledTrigger ? '定时任务 · 自动发送' :
        item.type === 'userMessage' ? '你' :
        item.type === 'steeringUserMessage' ? '你 · 补充指令' :
        item.phase === 'commentary' ? `${applicationMessageLabel()} · 进展` : `${applicationMessageLabel()} · 最终回复`;
      record.label.replaceChildren();
      const label = document.createElement('span');
      label.textContent = labelText;
      if (item.localDelivery) {
        record.el.classList.add('local-outgoing');
        record.el.dataset.delivery = item.localDelivery;
      } else {
        delete record.el.dataset.delivery;
      }
      const actions = document.createElement('span');
      actions.className = 'message-actions';
      const copy = document.createElement('button');
      copy.type = 'button';
      copy.className = 'message-action';
      copy.textContent = '复制';
      copy.title = '复制这条消息';
      copy.onclick = () => copyMessageText(item, copy);
      actions.append(copy);
      if (editable) {
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.className = 'message-action';
        edit.textContent = '编辑重发';
        edit.title = '编辑最后一条消息并替换该轮回复';
        edit.onclick = () => showSentMessageEditor(item, record);
        actions.append(edit);
      }
      if (supportsAction(currentHost(), 'fork_thread') && item.turnId && !item.localDelivery && item.phase !== 'commentary' && !threadPreviewOnly) {
        const branch = document.createElement('button');
        branch.type = 'button';
        branch.className = 'message-action';
        branch.textContent = '分支到聊天';
        branch.title = '复制到该轮为止的历史并创建新对话';
        branch.onclick = () => branchFromItem(item, branch);
        actions.append(branch);
      }
      const meta = document.createElement('span');
      meta.className = 'message-meta';
      meta.append(messageTimestamp(item), actions);
      record.label.append(label, meta);
      if (item.scheduledTrigger) {
        const wasOpen = record.content.querySelector('.scheduled-trigger')?.open || false;
        const details = document.createElement('details');
        details.className = 'scheduled-trigger';
        details.open = wasOpen;
        const summary = document.createElement('summary');
        const body = document.createElement('div');
        body.className = 'scheduled-trigger-body';
        let hydrated = false;
        const showInstructions = () => {
          summary.textContent = details.open ? '自动轮询指令 · 收起' : '自动轮询指令 · 显示更多';
          if (!details.open || hydrated) return;
          hydrated = true;
          richText(body, item.text, `${textBoxKey}/message`);
          bindInlineAttachmentPreviews(body, item, selected?.host || '', selected?.thread || '');
          renderAttachments(body, item, selected?.host || '', selected?.thread || '');
        };
        details.append(summary, body);
        showInstructions();
        details.ontoggle = showInstructions;
        record.content.replaceChildren(details);
      } else {
        richText(record.content, item.text, `${textBoxKey}/message`);
        bindInlineAttachmentPreviews(record.content, item,
          selected?.host || '', selected?.thread || '');
        renderAttachments(record.content, item, selected?.host || '', selected?.thread || '');
      }
      if (item.localAttachmentNames?.length) {
        const names = document.createElement('div');
        names.className = 'local-outgoing-files';
        names.textContent = '附件：' + item.localAttachmentNames.join('、');
        record.content.append(names);
      }
      // Keep interactive answers outside rich-text replacement. Progress/text
      // patches can update the message without detaching its custom editor.
      // Questions live in the fixed question deck, never inside this stream.
      record.questions?.remove();
      record.deliveryFooter?.remove();
      record.deliveryFooter = null;
      if (['userMessage', 'steeringUserMessage'].includes(item.type) && !item.scheduledTrigger) {
        record.deliveryFooter = messageDeliveryFooter(item);
        record.el.append(record.deliveryFooter);
      }
    } else if (reasoning) {
      record.el.className = 'reasoning-card';
      record.label.textContent = '思考摘要';
      record.label.append(messageTimestamp(item));
      richText(record.content, item.text || '该步骤没有公开的思考摘要。',
        `${textBoxKey}/reasoning`);
      bindInlineAttachmentPreviews(record.content, item,
        selected?.host || '', selected?.thread || '');
      renderAttachments(record.content, item, selected?.host || '', selected?.thread || '');
    } else if (notice) {
      const kind = item.type === 'contextCompaction' ? 'compaction' :
        item.noticeKind === 'quota' ? 'quota' : item.noticeKind === 'info' ? 'info' : 'error';
      record.el.className = `system-notice ${kind}`;
      record.label.className = 'system-notice-label';
      record.label.textContent = kind === 'compaction' ? '上下文整理' :
        kind === 'quota' ? '额度提示' : kind === 'info' ? '状态提示' : '任务失败';
      record.label.append(messageTimestamp(item));
      record.content.className = 'system-notice-text';
      record.content.textContent = item.text || '';
    } else {
      const kind = ({
        reasoning: '思考过程', commandExecution: '执行命令',
        fileChange: '文件修改', plan: '计划', mcpToolCall: '工具调用',
        imageView: '查看图片', webSearch: '网页搜索', extension: '扩展工具',
        contextCompaction: '上下文整理'
      })[item.type] || item.type;
      record.el.className = 'tool';
      const kindLabel = document.createElement('span'); kindLabel.textContent = kind;
      const commandPreview = document.createElement('span'); commandPreview.dataset.i18nIgnore = 'true';
      commandPreview.textContent = `${item.status ? ' · ' + item.status : ''}  ${String(item.text || '').split('\n')[0].slice(0, 100)}`;
      record.label.replaceChildren(kindLabel, commandPreview);
      record.label.append(messageTimestamp(item));
      record.content.replaceChildren();
      if (item.text) {
        const primary = document.createElement(item.type === 'commandExecution' ? 'pre' : 'div');
        primary.className = 'tool-primary';
        primary.textContent = item.text;
        record.content.append(primary);
        if (item.type === 'commandExecution') {
          addTextBlockControls(primary, `${textBoxKey}/command`);
        }
      }
      if (item.detail) {
        const detail = document.createElement('pre');
        detail.className = 'tool-detail';
        detail.textContent = item.detail;
        record.content.append(detail);
        addTextBlockControls(detail, `${textBoxKey}/detail`);
      }
      if (item.type === 'fileChange') {
        const review = document.createElement('button'); review.type = 'button'; review.className = 'secondary';
        review.textContent = '审阅与行批注'; review.onclick = () => window.NativePanel?.open('review');
        record.content.append(review);
      }
      renderAttachments(record.content, item, selected?.host || '', selected?.thread || '');
    }
  }
  return record.el;
}

function editableMessageText(text) {
  const matches = [...String(text || '').matchAll(/## My request(?: for Codex)?:/g)];
  const marker = matches.at(-1);
  return marker ? String(text).slice(marker.index + marker[0].length).trim() : String(text || '');
}

function showSentMessageEditor(item, record) {
  if (record.editPanel) {
    record.editPanel.querySelector('textarea')?.focus();
    return;
  }
  const panel = document.createElement('div');
  panel.className = 'sent-edit-panel';
  const editor = document.createElement('textarea');
  editor.setAttribute('aria-label', '编辑最后一条已发送消息');
  editor.maxLength = 32000;
  editor.rows = 5;
  editor.value = editableMessageText(item.text);
  const hint = document.createElement('p');
  hint.textContent = '重新发送会替换这条消息之后的最新回复；原附件会保留。';
  const actions = document.createElement('div');
  actions.className = 'sent-edit-actions';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'quiet';
  cancel.textContent = '取消';
  cancel.onclick = () => { panel.remove(); record.editPanel = null; };
  const resend = document.createElement('button');
  resend.type = 'button';
  resend.textContent = '重新发送';
  resend.onclick = async () => {
    const text = editor.value.trim();
    if (!text || busy || !selected) {
      if (!text) {
        $('sendResult').className = 'error';
        $('sendResult').textContent = '请输入修改后的消息';
      }
      return;
    }
    const target = {...selected};
    busy = true;
    editor.disabled = true;
    resend.disabled = true;
    cancel.disabled = true;
    $('sendResult').className = '';
    $('sendResult').textContent = '正在编辑并重新发送…';
    try {
      await issueCommand('edit_last_user_turn', {...target,
        payload: {turnId: item.turnId, itemId: item.id, text}});
      historyPages.delete(historyKey(target));
      threadSnapshots.delete(threadKey(target.host, target.thread));
      threadFetches.delete(threadKey(target.host, target.thread));
      threadRenderSignature = '';
      clearAssetCache();
      panel.remove();
      record.editPanel = null;
      $('sendResult').textContent = '✓ 已重新发送，正在刷新消息流';
      await refresh();
    } catch (error) {
      $('sendResult').textContent = error.message;
      $('sendResult').className = 'error';
    } finally {
      busy = false;
      editor.disabled = false;
      resend.disabled = false;
      cancel.disabled = false;
      if (selected?.host === target.host && selected?.thread === target.thread) {
        await refreshThread();
      }
    }
  };
  actions.append(cancel, resend);
  panel.append(editor, hint, actions);
  record.editPanel = panel;
  record.el.append(panel);
  editor.focus();
  editor.setSelectionRange(editor.value.length, editor.value.length);
}

function isProcessItem(item) {
  if (item.type === 'contextCompaction' || item.type === 'systemNotice') return false;
  if (Array.isArray(item.questions) && item.questions.length) return false;
  return !['userMessage', 'steeringUserMessage', 'agentMessage', 'assistantMessage']
    .includes(item.type) || item.phase === 'commentary';
}

function isProgressItem(item) {
  return (['agentMessage', 'assistantMessage'].includes(item.type) &&
    item.phase === 'commentary') || item.type === 'reasoning' || item.type === 'plan';
}

function processBlocks(items) {
  const blocks = [];
  let execution = null;
  for (const item of items) {
    if (isProgressItem(item)) {
      blocks.push({kind: 'progress', item});
      execution = null;
    } else {
      if (!execution) {
        execution = {kind: 'execution', items: []};
        blocks.push(execution);
      }
      execution.items.push(item);
    }
  }
  return blocks;
}

function executionSummaryText(items) {
  const commands = items.filter(item => item.type === 'commandExecution').length;
  const files = items.filter(item => item.type === 'fileChange').length;
  const others = items.length - commands - files;
  return [commands && `命令 ${commands}`, files && `文件 ${files}`,
    others && `工具 ${others}`].filter(Boolean).join(' · ');
}

function executionSummaryTitle(items) {
  if (items.every(item => item.type === 'commandExecution')) return '执行命令汇总';
  if (items.every(item => item.type === 'fileChange')) return '文件修改汇总';
  return '执行汇总';
}

function groupTurns(items) {
  const groups = [];
  let current = null;
  let legacy = 0;
  for (const item of items) {
    let key = item.turnId;
    if (!key) {
      if (!current || ['userMessage', 'steeringUserMessage'].includes(item.type)) {
        legacy += 1;
        key = `legacy-${legacy}`;
      } else {
        key = current.key;
      }
    }
    if (!current || current.key !== key) {
      current = {key, items: []};
      groups.push(current);
    }
    current.items.push(item);
  }
  return groups;
}

function timelineSegments(items) {
  // A mid-turn question/reply is a chronology boundary, not another opening
  // prompt. Keep the original turn ID on items for all native actions.
  const segments = [];
  for (const turn of groupTurns(items)) {
    let segment = null;
    for (const item of turn.items) {
      const user = ['userMessage', 'steeringUserMessage'].includes(item.type);
      if (!segment || user && segment.items.some(value =>
          !['userMessage', 'steeringUserMessage'].includes(value.type))) {
        segment = {key: `${turn.key}/segment/${item.id}`, items: []};
        segments.push(segment);
      }
      segment.items.push(item);
      if (Array.isArray(item.questions) && item.questions.length) segment = null;
    }
  }
  return segments;
}

function itemKey(item) {
  return `${item.turnId || ''}/${item.id}`;
}

function historyKey(target = selected) {
  return target ? `${target.host}/${target.thread}` : '';
}

function recentHistoryHead(thread) {
  const item = [...(thread.items || [])].reverse().find(item =>
    ['userMessage', 'steeringUserMessage'].includes(item.type)) ||
    thread.items?.[thread.items.length - 1];
  return item ? String(item.turnId || item.id || '') : '';
}

function historyConversationGroups(items) {
  const turns = new Map();
  for (const group of groupTurns(items.map(visibleScheduledItem).filter(Boolean))) {
    if (!turns.has(group.key)) turns.set(group.key, {...group, items: []});
    turns.get(group.key).items.push(...group.items);
  }
  return [...turns.values()].filter(group =>
    group.items.some(item => !item.localDelivery &&
      (item.type === 'userMessage' ||
       ['agentMessage', 'assistantMessage'].includes(item.type) && item.phase !== 'commentary')));
}

function mergeHistorySequence(older, newer, keyFor) {
  const previous = new Map(older.map(item => [keyFor(item), item]));
  const incoming = new Map(newer.map(item => [keyFor(item), item]));
  const before = new Map();
  let pending = [];
  let overlaps = false;
  for (const [key, item] of previous) {
    if (incoming.has(key)) {
      overlaps = true;
      before.set(key, pending);
      pending = [];
    } else {
      pending.push(item);
    }
  }
  // Disjoint history pages are supplied oldest first. With overlap, use the
  // incoming native sequence as the chronology spine, not Map insertion order.
  if (!overlaps) return [...previous.values(), ...incoming.values()];
  const result = [];
  for (const [key, item] of incoming) result.push(...(before.get(key) || []), item);
  result.push(...pending);
  return result;
}

function mergeHistoryItems(older, newer) {
  const previousTimes = new Map(older.filter(item => item.timestamp).map(item => [itemKey(item), item]));
  newer = newer.map(item => {
    const previous = previousTimes.get(itemKey(item));
    if (!previous || item.timestamp && (item.timestampSource !== 'turn' || previous.timestampSource === 'turn')) return item;
    const merged = {...item, timestamp: previous.timestamp};
    if (previous.timestampSource) merged.timestampSource = previous.timestampSource;
    else delete merged.timestampSource;
    return merged;
  });
  const collect = items => {
    const turns = new Map();
    for (const item of items) {
      // Legacy records lack turn IDs: align those by their actual item IDs,
      // not per-page synthetic turn numbers that can refer to different turns.
      const key = item.turnId ? `turn/${item.turnId}` : `item/${itemKey(item)}`;
      if (!turns.has(key)) turns.set(key, {key, items: []});
      turns.get(key).items.push(item);
    }
    return turns;
  };
  const previous = collect(older);
  const incoming = collect(newer);
  const turns = mergeHistorySequence([...previous.values()], [...incoming.values()], turn => turn.key);
  return turns.flatMap(turn => {
    // Preserve tools missing from a bounded live view, but let live item values
    // win and keep an overlapping partial turn together before the next turn.
    const items = mergeHistorySequence(previous.get(turn.key)?.items || [], turn.items, itemKey);
    return items;
  });
}

function rememberHistoryCache(key, state) {
  state.bytes = new TextEncoder().encode(JSON.stringify(state.items)).byteLength;
  historyPages.delete(key);
  historyPages.set(key, state);
  let total = [...historyPages.values()].reduce((sum, value) => sum + (value.bytes || 0), 0);
  for (const [oldKey, value] of historyPages) {
    if (historyPages.size <= THREAD_CACHE_MAX_ENTRIES && total <= HISTORY_CACHE_MAX_BYTES) break;
    // Explicitly expanded history stays visible until this chat is left.
    if (oldKey === historyKey() || value.loading) continue;
    total -= value.bytes || 0;
    historyPages.delete(oldKey);
  }
}

function rememberRecentLiveHistory(target, thread, previous) {
  const key = historyKey(target);
  const state = historyPages.get(key);
  if (!state?.recentReady || state.loading || document.hidden ||
      state.recentHead !== recentHistoryHead(previous)) return;
  const head = recentHistoryHead(thread);
  const continuous = head === state.recentHead || (thread.items || []).some(item =>
    String(item.turnId || item.id) === state.recentHead);
  if (!continuous) return;
  state.items = mergeHistoryItems(state.items, thread.items || []);
  if (!state.userExpanded) {
    const turns = historyConversationGroups(state.items).slice(-RECENT_HISTORY_TURNS);
    const keys = new Set(turns.flatMap(group => group.items.map(itemKey)));
    const retained = state.items.filter(item => keys.has(itemKey(item)));
    if (retained.length < state.items.length) {
      // Native cursors are opaque: restart manual paging at the head after
      // pruning, so the oldest retained turn's predecessor cannot be skipped.
      state.cursor = null;
      state.exhausted = false;
    }
    state.items = retained;
  }
  state.recentHead = recentHistoryHead(thread);
  rememberHistoryCache(key, state);
}

async function ensureRecentHistory(target, {manual = false, signal} = {}) {
  if (selected?.host !== target.host || selected?.thread !== target.thread ||
      !current || threadPreviewOnly || threadWatchPending || document.hidden ||
      !current.online || !currentHost()?.online || !current.canLoadHistory ||
      !current.partialHistory || !manual && manualThreadRefreshes.has(threadKey(target.host, target.thread))) return;
  const state = historyPages.get(historyKey(target));
  if (state?.loading || !manual && state?.retryAfter > Date.now()) return;
  // A size-bounded snapshot's first turn may start halfway through its output.
  if (historyConversationGroups(current.items || []).length > RECENT_HISTORY_TURNS) return;
  if (state?.recentHead === recentHistoryHead(current) &&
      (state.exhausted || historyConversationGroups(combinedHistoryItems(current)).length >= RECENT_HISTORY_TURNS)) return;
  await loadOlderHistory({recent: true, signal});
}

function outgoingDB() {
  if (!outgoingDBPromise) outgoingDBPromise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('本地恢复数据库暂不可用')), 1500);
    const request = indexedDB.open('CodexRelayOutbox', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('messages', {keyPath: 'journalKey'});
    request.onsuccess = () => {clearTimeout(timer);resolve(request.result);};
    request.onerror = () => {clearTimeout(timer);reject(request.error);};
  });
  return outgoingDBPromise;
}

async function outgoingTransaction(operation) {
  const db = await outgoingDB();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('messages', 'readwrite');
    operation(transaction.objectStore('messages'));
    transaction.oncomplete = () => resolve(true);
    transaction.onerror = transaction.onabort = () => reject(transaction.error);
  });
}

function outgoingBackup(scope) {
  try {const rows = JSON.parse(sessionStorage.getItem('codexRelayOutbox:' + scope) || '[]');
    return Array.isArray(rows) ? rows.filter(row => row && typeof row === 'object') : [];}
  catch (_) {return [];}
}

function pruneOutgoingStore(store, scope) {
  const rows = [];
  const request = store.openCursor();
  request.onsuccess = () => {
    const cursor = request.result;
    if (cursor) {
      const row = cursor.value;
      if (!Number.isFinite(row.updatedAt) || Date.now() - row.updatedAt > 86400000) cursor.delete();
      else if (row.scope === scope) rows.push({key: cursor.key, updatedAt: row.updatedAt});
      cursor.continue();
    } else {
      rows.sort((a, b) => b.updatedAt - a.updatedAt).slice(40).forEach(row => store.delete(row.key));
    }
  };
}

function persistOutgoingRecord(record) {
  const scope = record.journalScope;
  // The native echo can resolve the send promise before its broker poll ends.
  // Once reconciled, a late finally/receipt must never recreate this journal.
  if (record.reconciled || !scope || scope !== outgoingScope) return Promise.resolve(false);
  const uploadedAll = record.uploadedAttachments.length >= record.originalFiles.length;
  const row = {version: 1, scope, journalKey: `${scope}:${record.item.id}`, updatedAt: Date.now(),
    revision: record.journalRevision = (record.journalRevision || 0) + 1,
    target: {...record.target}, commandId: record.commandId, attemptIds: [...record.attemptIds],
    originalText: record.originalText, originalFiles: uploadedAll ? [] : [...record.originalFiles],
    uploadedAttachments: [...record.uploadedAttachments], sendRequested: record.sendRequested,
    nativeReceipt: record.nativeReceipt ? {ok: true, queued: Boolean(record.nativeReceipt.queued),
      ...outgoingNativeIdentity(record)} : null,
    known: [...record.known].slice(-1500), associatedNativeKey: record.associatedNativeKey,
    afterNativeKey: record.afterNativeKey || '',
    item: {...record.item, localInFlight: false}, missingFiles: false};
  let backupSaved = false;
  try {
    const rows = outgoingBackup(scope).filter(value => value.journalKey !== row.journalKey &&
      Date.now() - value.updatedAt < 86400000).slice(-39);
    rows.push({...row, originalFiles: [], missingFiles: row.originalFiles.length > 0});
    sessionStorage.setItem('codexRelayOutbox:' + scope, JSON.stringify(rows));
    backupSaved = !row.originalFiles.length;
  } catch (_) {}
  const operation = outgoingJournalQueue.catch(() => {}).then(async () => {
    if (scope !== outgoingScope) return false;
    try {
      await outgoingTransaction(store => {store.put(row);pruneOutgoingStore(store, scope);});
      record.journalError = '';
      return true;
    } catch (_) {
      record.journalError = backupSaved ? '' : '本地恢复记录未完整保存；请保留页面，避免丢失未上传附件。';
      return backupSaved;
    }
  });
  outgoingJournalQueue = operation;
  return operation;
}

function removeOutgoingRecord(record) {
  record.reconciled = true;
  const scope = record.journalScope;
  if (!scope) return;
  const key = `${scope}:${record.item.id}`;
  try {sessionStorage.setItem('codexRelayOutbox:' + scope,
    JSON.stringify(outgoingBackup(scope).filter(row => row.journalKey !== key)));} catch (_) {}
  outgoingJournalQueue = outgoingJournalQueue.catch(() => {}).then(() =>
    outgoingTransaction(store => store.delete(key))).catch(() => {});
}

async function bindOutgoingScope(scope) {
  if (typeof scope !== 'string' || !/^[a-f0-9]{64}$/.test(scope) || scope === outgoingScope) return;
  const epoch = ++outgoingScopeEpoch;
  const previous = outgoingScope;
  outgoingScope = scope;
  localOutgoingMessages.clear();
  if (previous && previous !== scope) {drafts.clear();attachmentDrafts.clear();questionDraftSessions.clear();clearQuestionDock();clearThreadSnapshots();}
  let rows = [];
  try {
    await outgoingJournalQueue.catch(() => {});
    await outgoingTransaction(store => pruneOutgoingStore(store, scope));
    const db = await outgoingDB();
    rows = await new Promise((resolve, reject) => {
      const request = db.transaction('messages').objectStore('messages').getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } catch (_) {}
  if (epoch !== outgoingScopeEpoch || scope !== outgoingScope) return;
  const records = new Map();
  // Prefer the complete IndexedDB record when the backup has the same revision.
  for (const row of [...outgoingBackup(scope), ...rows]) {
    if (row?.scope !== scope || row.version !== 1 || Date.now() - row.updatedAt > 86400000 ||
      typeof row.originalText !== 'string' || row.originalText.length > 32000 ||
      !row.target?.host || !row.target?.thread || !/^[a-f0-9-]{36}$/i.test(row.commandId || '') ||
      !row.item?.id || row.journalKey !== `${scope}:${row.item.id}` ||
      !Array.isArray(row.uploadedAttachments) || !Array.isArray(row.originalFiles) ||
      !Array.isArray(row.attemptIds) || !Array.isArray(row.known)) continue;
    if ((records.get(row.journalKey)?.revision || 0) <= row.revision) records.set(row.journalKey, row);
  }
  for (const row of [...records.values()].slice(-40)) {
    let confirm;
    const confirmation = new Promise(resolve => {confirm = resolve;});
    const record = {...row, journalScope: scope, journalRevision: row.revision,
      attemptIds: new Set(row.attemptIds || [row.commandId]), known: new Set(row.known || []),
      confirmation, confirm, inFlight: false, item: {...row.item, localInFlight: false, localProgress: ''}};
    if (record.nativeReceipt) {
      record.item.localDelivery = record.nativeReceipt.queued ? 'queued' : 'accepted';
      record.item.localError = '';
      confirm(record.nativeReceipt);
    }
    else {
      record.item.localDelivery = record.sendRequested ? 'unknown' : 'failed';
      record.item.localError = record.missingFiles ? '未上传附件无法恢复，请重新选择原文件；不会发送缺少附件的消息。' :
        '已恢复发送记录；重试会先核对同一命令是否被接收，不会自动重发。';
    }
    const key = historyKey(record.target);
    localOutgoingMessages.set(key, [...(localOutgoingMessages.get(key) || []), record]);
  }
}

async function clearOutgoingJournal() {
  const scope = outgoingScope;
  outgoingScope = '';
  outgoingScopeEpoch++;
  localOutgoingMessages.clear();
  try {sessionStorage.removeItem('codexRelayOutbox:' + scope);} catch (_) {}
  await outgoingJournalQueue.catch(() => {});
  try {
    const db = await outgoingDB();
    await new Promise((resolve, reject) => {
      const transaction = db.transaction('messages', 'readwrite');
      const request = transaction.objectStore('messages').openCursor();
      request.onsuccess = () => {const cursor = request.result;if (cursor) {
        if (cursor.value.scope === scope) cursor.delete();cursor.continue();}};
      transaction.oncomplete = resolve;transaction.onerror = () => reject(transaction.error);
    });
  } catch (_) {}
}

function beginLocalOutgoing(target, commandId, text, files) {
  const key = historyKey(target);
  const records = localOutgoingMessages.get(key) || [];
  const knownItems = [...(historyPages.get(key)?.items || []), ...(current?.items || [])];
  let confirm;
  const confirmation = new Promise(resolve => {confirm = resolve;});
  const record = {target: {...target}, commandId, attemptIds: new Set([commandId]),
    confirmation, confirm, nativeReceipt: null, originalText: text, originalFiles: [...files],
    uploadedAttachments: [], sendRequested: false, inFlight: false,
    known: new Set(knownItems.map(itemKey)),
    afterNativeKey: knownItems.length ? itemKey(knownItems.at(-1)) : '',
    item: {id: `local-send-${commandId}`, type: 'userMessage', text: text.trim(),
      timestamp: Date.now(),
      localDelivery: files.length ? 'preparing' : 'sending',
      localAttachmentNames: files.map(file => file.name)}};
  localOutgoingMessages.set(key, [...records, record]);
  record.journalScope = outgoingScope;
  record.persistence = persistOutgoingRecord(record);
  showLocalOutgoing(target);
  return record;
}

function showLocalOutgoing(target) {
  if (selected?.host !== target.host || selected?.thread !== target.thread || !current) return;
  threadRenderSignature = '';
  renderTimeline(current);
}

function updateLocalOutgoing(target, record, delivery, error = '') {
  record.item.localDelivery = delivery;
  record.item.localError = error;
  record.persistence = persistOutgoingRecord(record);
  showLocalOutgoing(target);
}

function detachComposerSubmission(target, record) {
  const key = `${target.host}/${target.thread}`;
  if (drafts.get(key) === record.originalText) drafts.delete(key);
  const files = attachmentDrafts.get(key);
  if (files?.length === record.originalFiles.length && files.every((file, index) => file === record.originalFiles[index]))
    attachmentDrafts.delete(key);
  if (selected?.host !== target.host || selected?.thread !== target.thread) return;
  if ($('message').value === record.originalText) $('message').value = '';
  if (pendingFiles.length === record.originalFiles.length && pendingFiles.every((file, index) => file === record.originalFiles[index]))
    setPendingFiles([]);
  // A submitted message belongs to its timeline bubble, never both the bubble
  // and a retained composer draft. Its original text/files remain in the local
  // receipt for explicit retry; later typing is a separate draft.
}

function confirmLocalOutgoing(target, record, receipt) {
  record.nativeReceipt = receipt;
  record.confirm(receipt);
  record.item.localDelivery = receipt.queued ? 'queued' : 'accepted';
  record.item.localError = '';
  record.item.localProgress = '';
  record.persistence = persistOutgoingRecord(record);
  // A late receipt only changes that message. It must not touch a new draft or
  // restore a stale composer-level failure banner.
}

function outgoingNativeIdentity(record) {
  const receipt = record.nativeReceipt;
  const native = receipt?.nativeReceipt;
  return {turnId: native?.turnId || receipt?.turnId,
    itemId: native?.itemId || receipt?.itemId,
    clientUserMessageId: native?.clientUserMessageId || receipt?.clientUserMessageId};
}

function messageDeliveryFooter(item) {
  const target = {...selected};
  const outgoing = (localOutgoingMessages.get(historyKey(target)) || [])
    .find(record => record.item.id === (item.localReceiptId || item.id));
  const state = item.localDelivery || (item.type === 'steeringUserMessage' && item.status &&
    item.status !== 'accepted' ? (item.status === 'rejected' ? 'failed' : 'unknown') : 'accepted');
  const footer = document.createElement('div');
  footer.className = 'message-delivery-footer';
  footer.dataset.delivery = state;
  const delivery = document.createElement('span');
  delivery.className = 'message-delivery';
  delivery.setAttribute('role', 'status');
  delivery.setAttribute('aria-live', 'polite');
  delivery.textContent = item.localProgress || ({preparing: '准备附件中', sending: '发送中',
    accepted: '✓ 已接收', queued: '✓ 已接收 · 待发送',
    failed: '发送失败', unknown: '接收待确认'})[state];
  delivery.title = item.localError || outgoing?.journalError || '';
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'message-retry';
  retry.textContent = '↻ 重试';
  retry.disabled = !outgoing || outgoing.inFlight ||
    !['failed', 'unknown'].includes(state) || !current?.owned || !current?.online;
  retry.title = state === 'accepted' || state === 'queued' ? '已接收，无需重试' :
    '先核对原消息的接收状态，再重试；附件会保留';
  // Timeline nodes can be reused after a namespace/login rebind. Resolve the
  // current record on click, never keep an obsolete receipt in a DOM closure.
  retry.onclick = () => retryOutgoingMessage((localOutgoingMessages.get(historyKey(target)) || [])
    .find(record => record.item.id === (item.localReceiptId || item.id)));
  footer.append(delivery, retry);
  return footer;
}

function reconcileLocalOutgoing(target, thread) {
  // An exact native identity remains authoritative when it is only present in
  // a loaded history page. Text/synthetic-ID associations still require live
  // Desktop data and can NEVER prove message acceptance.
  const live = thread.source === 'desktop-live' && thread.owned;
  const key = historyKey(target);
  const records = localOutgoingMessages.get(key);
  if (!records?.length) return;
  const historyUsers = historyPages.get(key)?.items || [];
  const users = mergeHistoryItems(historyUsers, thread.items || []).filter(item => item.type === 'userMessage' ||
    item.type === 'steeringUserMessage' && item.status === 'accepted');
  const claimed = new Set();
  const remaining = records.filter(record => {
    const identity = outgoingNativeIdentity(record);
    const matchesIdentity = item => record.attemptIds.has(item.id) ||
      record.attemptIds.has(item.clientUserMessageId) || Boolean(identity.turnId && identity.itemId &&
        item.turnId === identity.turnId && item.id === identity.itemId);
    const queued = live && (thread.queuedMessages || []).find(message =>
      record.attemptIds.has(message.id) || record.attemptIds.has(message.clientUserMessageId));
    if (queued) {
      confirmLocalOutgoing(target, record, {ok: true, transport: 'desktop-live-echo', queued: true,
        position: thread.queuedMessages.indexOf(queued) + 1, messageId: record.commandId});
      removeOutgoingRecord(record);
      return false;
    }
    const exactMatch = users.find(item => !claimed.has(itemKey(item)) &&
      matchesIdentity(item));
    const match = exactMatch || (live && (thread.items || []).filter(item => item.type === 'userMessage' ||
      item.type === 'steeringUserMessage' && item.status === 'accepted').find(item => {
      const nativeKey = itemKey(item);
      if (claimed.has(nativeKey)) return false;
      if (record.attemptIds.has(item.id) || record.attemptIds.has(item.clientUserMessageId)) return true;
      if (record.known.has(nativeKey) && nativeKey !== record.associatedNativeKey) return false;
      const text = editableMessageText(item.text).trim();
      if (record.item.text) return text === record.item.text;
      // Files-only messages can carry Desktop's generated file context as text.
      return record.item.localAttachmentNames.length > 0 &&
        (item.attachments?.length > 0 ||
         record.item.localAttachmentNames.every(name => String(item.text || '').includes(name)));
    }));
    if (!match) return true;
    const exactIdentity = matchesIdentity(match);
    if (exactIdentity) {
      confirmLocalOutgoing(target, record, {ok: true, transport: 'desktop-live-echo',
        clientUserMessageId: record.commandId, turnId: match.turnId, itemId: match.id});
    }
    const nativeKey = itemKey(match);
    claimed.add(nativeKey);
    // Repeated identical prompts must not consume the same native message again
    // on the next refresh after this local receipt has been removed.
    for (const other of records) other.known.add(nativeKey);
    if (!exactIdentity && !['accepted', 'queued'].includes(record.item.localDelivery)) {
      // Legacy clients may omit the client ID. Associate the new visible echo
      // only to avoid rendering its text twice; this is NOT proof of delivery.
      // Keep the pending receipt and retry button on that native bubble.
      record.associatedNativeKey = nativeKey;
      return true;
    }
    removeOutgoingRecord(record);
    return false;
  });
  if (remaining.length) localOutgoingMessages.set(key, remaining);
  else localOutgoingMessages.delete(key);
}

function visibleScheduledItem(item) {
  if (!['userMessage', 'steeringUserMessage', 'agentMessage', 'assistantMessage']
    .includes(item.type) || typeof item.text !== 'string') return item;
  const blocks = [...item.text.matchAll(/<heartbeat(?:\s[^>]*)?>[\s\S]*?<\/heartbeat>/gi)];
  if (!blocks.length) return item;
  const block = blocks[blocks.length - 1][0];
  const field = name => block.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${name}\\s*>`, 'i'))?.[1]?.trim();
  if (['userMessage', 'steeringUserMessage'].includes(item.type)) {
    if (item.text.trim() === block.trim() && field('automation_id') !== undefined &&
        field('instructions') !== undefined) {
      return {...item, text: field('instructions') || '定时监控已触发。', scheduledTrigger: true,
        ...(field('current_time_iso') ? {scheduledAt: field('current_time_iso')} : {})};
    }
    return item;
  }
  const decision = field('decision')?.toUpperCase();
  if (decision === 'NOTIFY' || decision === 'DONT_NOTIFY') {
    const text = field('message') ?? item.text.replace(/<heartbeat(?:\s[^>]*)?>[\s\S]*?<\/heartbeat>/gi, '').trim();
    return text || item.attachments?.length || item.questions?.length ?
      {...item, text, notificationDecision: decision} : null;
  }
  return item;
}

function combinedHistoryItems(thread) {
  const page = historyPages.get(historyKey());
  const local = localOutgoingMessages.get(historyKey()) || [];
  const native = mergeHistoryItems(page?.items || [], thread.items || []);
  const projected = new Set();
  const combined = native.map(item => {
    const receipt = local.find(record => record.associatedNativeKey === itemKey(item));
    if (!receipt) return item;
    projected.add(receipt);
    return {...item, localReceiptId: receipt.item.id, localDelivery: receipt.item.localDelivery,
      localError: receipt.item.localError, localProgress: receipt.item.localProgress,
      localInFlight: receipt.item.localInFlight};
  });
  return placeOutgoingItems(combined, local.filter(record => !projected.has(record)))
    .map(visibleScheduledItem).filter(Boolean);
}

function placeOutgoingItems(native, records) {
  if (!records.length) return native;
  // Keep the native sequence as the chronology spine. Do not globally sort its
  // approximate timestamps or split a command/question/reply segment apart.
  const segments = timelineSegments(native);
  const anchorEnds = new Map();
  let offset = 0;
  const boundaries = segments.map(segment => {
    const start = offset;
    offset += segment.items.length;
    for (const item of segment.items) anchorEnds.set(itemKey(item), offset);
    const user = segment.items.find(item => ['userMessage', 'steeringUserMessage'].includes(item.type));
    const date = user ? messageDate(user) : segment.items.map(messageDate).find(Boolean);
    return {start, time: date?.getTime()};
  });
  const buckets = new Map();
  for (const record of records) {
    const after = anchorEnds.get(record.afterNativeKey);
    const time = messageDate(record.item)?.getTime();
    const next = Number.isFinite(time) ? boundaries.find(boundary =>
      boundary.start >= (after ?? 0) && boundary.time > time) : null;
    const index = next?.start ?? native.length;
    if (!buckets.has(index)) buckets.set(index, []);
    buckets.get(index).push(record.item);
  }
  for (const items of buckets.values()) items.sort((a, b) => {
    const left = messageDate(a)?.getTime(), right = messageDate(b)?.getTime();
    return Number.isFinite(left) && Number.isFinite(right) ? left - right : 0;
  });
  return native.flatMap((item, index) => [...(buckets.get(index) || []), item])
    .concat(buckets.get(native.length) || []);
}

function timelineNearBottom(distance = 150) {
  const timeline = $('timeline');
  return timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < distance;
}

function updateJumpBottom() {
  const hidden = !selected || timelineNearBottom();
  $('jumpBottom').hidden = hidden;
  $('jumpBottom').setAttribute('aria-hidden', String(hidden));
}

function keepCollapsedProcessSummaryVisible(timeline, details, summary) {
  if (!details.isConnected) return;
  const top = timeline.getBoundingClientRect().top +
    (parseFloat(getComputedStyle(timeline).paddingTop) || 0);
  if (summary.getBoundingClientRect().top < top - 1) {
    timeline.scrollTop += details.getBoundingClientRect().top - top;
  }
}

function renderHistoryNote(fragment, thread) {
  const state = historyPages.get(historyKey());
  const truncated = Boolean(thread.partialHistory || thread.totalItems > 500);
  const note = document.createElement('div');
  note.className = 'history-note';
  if (!truncated && !state?.items.length) {
    note.textContent = '与目标机的同一条对话';
  } else if (state?.exhausted) {
    note.textContent = '已通过 Codex 历史接口读取完整记录';
  } else if (thread.canLoadHistory) {
    const label = document.createElement('span');
    label.textContent = state?.error ? `加载失败：${state.error}` : state?.recentReady ?
      `当前已加载 ${historyConversationGroups(combinedHistoryItems(thread)).length} 轮对话` : '当前显示近期内容';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'load-history';
    button.textContent = state?.loading ? '正在加载…' : '加载更早历史';
    button.disabled = Boolean(state?.loading);
    button.onclick = () => loadOlderHistory();
    note.append(label, button);
  } else {
    note.textContent = '当前显示已加载的近期内容；目标机暂不支持历史分页';
  }
  fragment.append(note);
}

function pendingQuestionItems(thread, items) {
  const nativeIds = thread.asyncQuestionState?.pendingQuestionIds;
  const native = Array.isArray(nativeIds) ? new Set(nativeIds) : null;
  const answered = new Set(items.filter(item => item.type === 'userMessage' ||
    item.type === 'steeringUserMessage' && item.status === 'accepted')
    .flatMap(item => item.questionReplyTo || []));
  const pending = new Map();
  for (const item of items) {
    if (!['agentMessage', 'assistantMessage'].includes(item.type) || !Array.isArray(item.questions)) continue;
    const questions = item.questions.filter((question, index) => {
      const id = question.questionItemId || JSON.stringify(['request_user_input_async', item.id, index]);
      // Accepted native messages can arrive before the pending-ID projection.
      // Never let a lagging projection resurrect a question already answered
      // in original history. Local optimistic replies are not in this set.
      return (!native || native.has(id)) && !answered.has(id);
    });
    if (questions.length) pending.set(itemKey(item), {...item, questions});
  }
  return pending;
}

const EXECUTION_PHASE_LABELS = {
  syncing: '等待同步执行状态', preparing: '原 App 正在准备任务', resuming: '原 App 正在载入会话',
  waiting_output: '原 App 正在运行 · 尚无进展内容', waiting_approval: '等待确认',
  waiting_input: '等待回答', thinking: '正在思考', executing: '正在执行',
  responding: '正在输出回复', compacting: '正在压缩上下文', running: '正在运行',
  completed: '本轮已完成', interrupted: '本轮已停止', failed: '本轮执行异常', forked: '分支历史记录',
  capacity_wait: '模型满载 · 等待重试', retrying: '原 App 正在重试',
  idle: '原 App 当前空闲', unknown: '执行状态尚未同步', offline: '执行状态同步已断开'
};

function nativeExecutionFeedback(thread) {
  if (thread.executionState?.phase in EXECUTION_PHASE_LABELS) return thread.executionState;
  // Backward compatibility for already installed clients. A receipt alone is
  // never proof of running/thinking; use native runtime and native item IDs.
  const items = thread.items || [];
  const lastTurn = thread.nativeMetadata?.turns?.at(-1);
  const turnId = lastTurn?.id || items.at(-1)?.turnId;
  const output = items.filter(item => item.turnId === turnId &&
    !['userMessage', 'steeringUserMessage'].includes(item.type));
  let phase = thread.status === 'active' ? (output.length ? 'running' : 'waiting_output') :
    thread.status === 'systemError' || lastTurn?.status === 'failed' ? 'failed' :
    lastTurn?.status === 'interrupted' ? 'interrupted' :
    lastTurn?.status === 'completed' ? 'completed' : thread.status === 'idle' ? 'idle' : 'unknown';
  if (thread.pendingRequests > 0) phase = (thread.requests || []).some(r => r.method === 'item/tool/requestUserInput') ?
    'waiting_input' : 'waiting_approval';
  return {phase, turnId, hasOutput: Boolean(output.length)};
}

function executionFeedbackForGroup(thread, group, latest) {
  if (!latest) return null;
  const state = nativeExecutionFeedback(thread);
  const users = group.items.filter(item => ['userMessage', 'steeringUserMessage'].includes(item.type));
  const accepted = users.some(item => item.localDelivery === 'accepted');
  const native = group.items.some(item => !item.localDelivery || item.localReceiptId);
  const matches = state.turnId && group.items.some(item => item.turnId === state.turnId);
  // Before the new native echo arrives the previous turn may still be completed.
  // Never attach that old result to the newly accepted local bubble.
  if (!native && !accepted) return null;
  let phase = !native && accepted || state.turnId && !matches ? 'syncing' : state.phase;
  if (threadPreviewOnly) phase = 'syncing';
  else if (!currentHost()?.online || !(thread.online ?? current?.online) || !currentHost()?.desktopConnected) phase = 'offline';
  const label = EXECUTION_PHASE_LABELS[phase] || EXECUTION_PHASE_LABELS.unknown;
  const detail = phase === 'waiting_output' ? '原 App 已进入运行状态，但尚未提供思考、进展或执行内容；收到后会自动显示。' :
    phase === 'syncing' || phase === 'unknown' ? '消息接收回执与执行状态分别同步；尚未拿到这条消息的最新执行状态，不会重复发送。' :
    phase === 'idle' ? '原 App 当前没有运行中的任务；消息接收不等于已开始执行。' :
    phase === 'offline' ? '连接恢复后会继续同步原 App 状态，已接收的消息不会重新发送。' :
    phase === 'completed' ? '原 App 已结束本轮，没有提供思考或执行明细。' :
    phase === 'failed' ? '原 App 报告执行异常，请查看下方错误提示。' : label + '，状态来自原 App。';
  return {phase, label, detail};
}

function renderTimeline(thread, preserveAnchor = null) {
  const timeline = $('timeline');
  const scrollTop = timeline.scrollTop;
  const nearBottom = timelineNearBottom();
  const existing = new Set();
  const fragment = document.createDocumentFragment();
  renderHistoryNote(fragment, thread);
  const children = [...fragment.childNodes];
  const turns = new Map([...timeline.children].filter(node => node.classList.contains('turn'))
    .map(node => [node.dataset.segmentKey, node]));

  const allItems = combinedHistoryItems(thread);
  renderQuestionDock(thread, allItems);
  const pendingQuestions = pendingQuestionItems(thread, allItems);
  const groups = timelineSegments(allItems);
  const focused = document.activeElement;
  const focusedTurn = focused?.closest('.turn');
  const focusedItemKey = [...rendered].find(([, record]) => record.el.contains(focused))?.[0];
  const focusedGroup = groups.find(group => group.items.some(item => itemKey(item) === focusedItemKey));
  groups.forEach(group => {
    // History hydration can change a segment's first item/key. Reserve the
    // focused section for its actual message, not for the previous segment key.
    const previousTurn = turns.get(group.key);
    const turn = focusedTurn && focusedGroup === group ? focusedTurn :
      previousTurn && previousTurn !== focusedTurn ? previousTurn : document.createElement('section');
    turn.className = 'turn';
    turn.dataset.segmentKey = group.key;
    const turnChildren = [];
    const userItems = group.items.filter(item =>
      ['userMessage', 'steeringUserMessage'].includes(item.type));
    const processItems = group.items.filter(isProcessItem);
    const blocks = processBlocks(processItems);
    const compactionItems = group.items.filter(item => item.type === 'contextCompaction');
    const systemNotices = group.items.filter(item => item.type === 'systemNotice');
    const finalItems = group.items.filter(item =>
      ['agentMessage', 'assistantMessage'].includes(item.type) &&
      (item.phase !== 'commentary' || item.questions?.length));

    for (const item of userItems) {
      const key = `${item.turnId || ''}/${item.id}`;
      existing.add(key);
      turnChildren.push(renderItem(item));
    }
    const executionState = executionFeedbackForGroup(thread, group, group === groups.at(-1));
    if (processItems.length || executionState) {
      const details = document.createElement('details');
      details.className = 'process-group';
      if (executionState) details.dataset.executionPhase = executionState.phase;
      const processKey = `${selected?.host || ''}/${selected?.thread || ''}/${group.key}`;
      details.open = expandedProcesses.has(processKey);
      const summary = document.createElement('summary');
      const label = document.createElement('span');
      label.textContent = '思考与执行过程';
      const count = document.createElement('span');
      count.className = 'process-summary-status';
      count.textContent = (executionState ? executionState.label : '') + (processItems.length ?
        `${executionState ? ' · ' : ''}${blocks.filter(block => block.kind === 'progress').length} 条进展 · ` +
        `${blocks.filter(block => block.kind === 'execution').length} 组执行` : '');
      if (executionState) count.setAttribute('role', 'status');
      summary.append(label, count);
      const body = document.createElement('div');
      body.className = 'process-list';
      let processRendered = false;
      const processKeys = processItems.map(item => `${item.turnId || ''}/${item.id}`);
      const renderProcessBody = () => {
        if (processRendered) return;
        processRendered = true;
        if (!processItems.length && executionState) {
          const explanation = document.createElement('p');
          explanation.className = 'execution-state-note';
          explanation.textContent = executionState.detail;
          body.append(explanation);
        }
        for (const block of blocks) {
          if (block.kind === 'progress') {
            existing.add(itemKey(block.item));
            body.append(renderItem(block.item));
            continue;
          }
          const execution = document.createElement('details');
          execution.className = 'execution-group';
          const executionKey = `${processKey}/execution/${itemKey(block.items[0])}`;
          execution.open = expandedExecutionGroups.has(executionKey);
          const executionHeader = document.createElement('summary');
          const executionTitle = document.createElement('strong');
          executionTitle.textContent = executionSummaryTitle(block.items);
          const executionCount = document.createElement('span');
          executionCount.textContent = executionSummaryText(block.items);
          executionHeader.append(executionTitle, executionCount);
          const executionList = document.createElement('div');
          executionList.className = 'execution-list';
          for (const item of block.items) {
            existing.add(itemKey(item));
            executionList.append(renderItem(item));
          }
          execution.append(executionHeader, executionList);
          execution.ontoggle = () => {
            if (!execution.isConnected) return;
            if (execution.open) expandedExecutionGroups.add(executionKey);
            else expandedExecutionGroups.delete(executionKey);
          };
          body.append(execution);
        }
      };
      const clearProcessBody = () => {
        for (const key of processKeys) {
          existing.delete(key);
          const record = rendered.get(key);
          if (record) {
            record.el.remove();
            rendered.delete(key);
          }
        }
        body.replaceChildren();
        processRendered = false;
      };
      if (details.open) renderProcessBody();
      details.append(summary, body);
      details.ontoggle = () => {
        if (!details.isConnected) return;
        if (details.open) {
          expandedProcesses.add(processKey);
          renderProcessBody();
        } else {
          expandedProcesses.delete(processKey);
          clearProcessBody();
          requestAnimationFrame(() => keepCollapsedProcessSummaryVisible(
            $('timeline'), details, summary));
        }
      };
      turnChildren.push(details);
    }
    for (const item of compactionItems) {
      existing.add(itemKey(item));
      turnChildren.push(renderItem(item));
    }
    for (const item of finalItems) {
      const key = `${item.turnId || ''}/${item.id}`;
      existing.add(key);
      const choiceState = !pendingQuestions.has(key) ? 'past' :
        submittedChoices.has(asyncQuestionKey(item)) ? 'submitted' :
        choiceBusy.has(asyncQuestionKey(item)) ? 'submitting' :
        !threadPreviewOnly && current?.owned && current?.online && current?.source === 'desktop-live' ?
          'ready' : 'offline';
      turnChildren.push(renderItem(pendingQuestions.get(key) || item, choiceState));
    }
    for (const item of systemNotices) {
      existing.add(itemKey(item));
      turnChildren.push(renderItem(item));
    }
    reconcileConnectedChildren(turn, turnChildren);
    children.push(turn);
  });

  for (const [key, record] of rendered) {
    if (!existing.has(key)) {
      record.el.remove();
      rendered.delete(key);
    }
  }
  reconcileConnectedChildren(timeline, children);
  if (preserveAnchor) {
    timeline.scrollTop = preserveAnchor.top + timeline.scrollHeight - preserveAnchor.height;
  } else if (nearBottom) {
    timeline.scrollTop = timeline.scrollHeight;
  } else {
    timeline.scrollTop = scrollTop;
  }
  requestAnimationFrame(updateJumpBottom);
}

async function loadOlderHistory({recent = false, signal} = {}) {
  if (!selected || !current || threadPreviewOnly) return;
  const target = {...selected};
  const key = historyKey(target);
  const state = historyPages.get(key) || {
    items: [], cursor: null, exhausted: false, loading: false, error: ''
  };
  if (state.loading || !recent && state.exhausted) return;
  historyPages.set(key, state);
  const epoch = threadSnapshotEpoch;
  const head = recentHistoryHead(current);
  const baseThread = current;
  const timeline = $('timeline');
  const anchor = {top: timeline.scrollTop, height: timeline.scrollHeight};
  const countItems = () => new Set([...state.items, ...(baseThread.items || [])].map(itemKey)).size;
  const before = countItems();
  state.loading = true;
  state.error = '';
  state.retryAfter = 0;
  if (recent) {
    state.cursor = null;
    state.exhausted = false;
  } else state.userExpanded = true;
  renderTimeline(current, anchor);
  try {
    for (let attempt = 0; attempt < 6 && !state.exhausted; attempt += 1) {
      const result = await issueCommand('load_older', {
        host: target.host, thread: target.thread,
        payload: {cursor: state.cursor, limit: recent ? RECENT_HISTORY_TURNS : 2, mode: 'full'}, signal
      });
      if (epoch !== threadSnapshotEpoch || historyPages.get(key) !== state) return;
      // Read complete native turn items with the conversation page itself.
      // Opening a process/execution/tool group only renders this local data;
      // it must not request item pages or defer execution output.
      const incoming = result.items || [];
      state.items = recent && attempt === 0 ?
        mergeHistoryItems(state.items, incoming) : mergeHistoryItems(incoming, state.items);
      state.cursor = result.nextCursor || null;
      state.exhausted = !state.cursor;
      if (recent) {
        state.recentReady = true;
        if (attempt === 0) state.recentHead = recentHistoryHead({items: result.items || []}) || head;
      }
      rememberHistoryCache(key, state);
      if (selected?.host !== target.host || selected?.thread !== target.thread) break;
      if (recent ? historyConversationGroups([...state.items, ...(baseThread.items || [])]).length >= RECENT_HISTORY_TURNS :
          countItems() > before) break;
    }
  } catch (error) {
    state.error = error.message;
    state.retryAfter = Date.now() + 30000;
  } finally {
    state.loading = false;
    if (epoch === threadSnapshotEpoch && historyPages.get(key) === state) {
      rememberHistoryCache(key, state);
      if (selected?.host === target.host && selected?.thread === target.thread && current) {
        const readingAnchor = recent && timelineNearBottom() ? null :
          {top: timeline.scrollTop, height: timeline.scrollHeight};
        renderTimeline(current, readingAnchor);
      }
    }
  }
}

function populateEfforts(modelId, desired) {
  const host = currentHost();
  const model = host?.models.find(item => item.id === modelId);
  const items = (model?.efforts || []).map(item => ({
    value: item.id, label: effortName(item.id), title: item.description
  }));
  const value = items.some(item => item.value === desired) ? desired :
    model?.defaultEffort || items[0]?.value || '';
  setOptions($('effortSelect'), items, value);
}

function modelServiceTiers(modelId) {
  const host = currentHost();
  const model = host?.models?.find(item => item.id === modelId);
  return applicationId(host) === 'codex' && Array.isArray(model?.serviceTiers) ? model.serviceTiers : [];
}

function serviceTierForModel(modelId, desired = 'default') {
  const tiers = modelServiceTiers(modelId);
  if (tiers.some(tier => tier.id === desired)) return desired;
  if (['priority', 'fast'].includes(desired)) {
    const equivalent = tiers.find(tier => ['priority', 'fast'].includes(tier.id));
    if (equivalent) return equivalent.id;
  }
  return tiers.some(tier => tier.id === 'default') ? 'default' : '';
}

function syncSpeedControl(host, meta, activeModel) {
  const tiers = modelServiceTiers(activeModel);
  const fastTier = ['priority', 'fast', 'ultrafast'].find(id => tiers.some(tier => tier.id === id));
  const available = Boolean(fastTier && tiers.some(tier => tier.id === 'default'));
  const pending = pendingSpeedChange?.host === host.id && pendingSpeedChange.thread === meta.id;
  $('quickSpeedControl').hidden = applicationId(host) !== 'codex' || !activeModel;
  $('quickSpeed').checked = pending ? pendingSpeedChange.enabled :
    ['priority', 'fast', 'ultrafast'].includes(current?.serviceTier || meta.serviceTier);
  $('quickSpeed').disabled = !available || busy || threadPreviewOnly || !host.online ||
    !current?.online || !host.desktopConnected || meta.archived || !current?.owned ||
    current?.source !== 'desktop-live' || !supportsAction(host, 'update_settings') ||
    !host.models.find(model => model.id === activeModel)?.efforts?.some(effort =>
      effort.id === (current?.reasoningEffort || meta.reasoningEffort));
  const note = !tiers.length ?
    '目标客户端未提供速度选项；请更新目标电脑的 CRB 客户端后重试。' : available ?
      '加速模式会增加额度消耗；实际可用性由原 App 决定。' : '此模型未提供加速模式。';
  $('quickSpeedControl').title = note;
  $('speedUsageNote').textContent = note;
}

function populateNewThreadEfforts(desired) {
  const host = currentHost();
  const model = host?.models.find(item => item.id === $('newThreadModel').value);
  const items = (model?.efforts || []).map(item => ({
    value: item.id, label: effortName(item.id), title: item.description
  }));
  const value = items.some(item => item.value === desired) ? desired :
    model?.defaultEffort || items[0]?.value || '';
  setOptions($('newThreadEffort'), items, value);
  $('newThreadEffort').disabled = !items.length;
}

function syncNewThreadPasteButton() {
  const button = $('newThreadPasteCopied');
  const copied = fileClipboard;
  button.hidden = !copied || copied.isDirectory || copied.hostId !== physicalHostId() || !supportsAction(currentHost(), 'attach_local_file');
  if (!button.hidden) button.title = `将 ${copied.name} 加入首条消息`;
}

function renderNewThreadUploadTray() {
  const tray = $('newThreadUploadTray');
  tray.replaceChildren();
  tray.hidden = newThreadFiles.length === 0;
  newThreadFiles.forEach((file, index) => {
    const card = document.createElement('div');
    card.className = 'pending-upload';
    const preview = document.createElement('div');
    preview.className = 'pending-upload-preview';
    if (!file.remoteSourcePath && file.type.startsWith('image/')) {
      let url = filePreviewUrls.get(file);
      if (!url) {
        url = URL.createObjectURL(file);
        filePreviewUrls.set(file, url);
      }
      const image = document.createElement('img');
      image.src = url;
      image.alt = '';
      preview.append(image);
    } else {
      preview.textContent = file.remoteSourcePath ? '主机' : 'FILE';
    }
    const info = document.createElement('div');
    info.className = 'pending-upload-info';
    const name = document.createElement('strong');
    name.textContent = file.name;
    const size = document.createElement('span');
    size.textContent = file.remoteSourcePath ? '目标机文件' : formatBytes(file.size);
    info.append(name, size);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'pending-upload-remove';
    remove.textContent = '×';
    remove.title = `移除 ${file.name}`;
    remove.disabled = busy;
    remove.onclick = () => {
      const url = filePreviewUrls.get(file);
      if (url) URL.revokeObjectURL(url);
      filePreviewUrls.delete(file);
      newThreadFiles.splice(index, 1);
      renderNewThreadUploadTray();
    };
    card.append(preview, info, remove);
    tray.append(card);
  });
}

function addNewThreadFiles(additions) {
  const originalCount = additions.length;
  additions = filterSupportedAttachments(additions, 'threadError');
  if (!additions.length) return;
  const byKey = new Map(newThreadFiles.map(file => [pendingFileKey(file), file]));
  for (const file of additions) byKey.set(pendingFileKey(file), file);
  newThreadFiles = [...byKey.values()];
  if (additions.length === originalCount) $('threadError').textContent = '';
  renderNewThreadUploadTray();
}

function clearNewThreadFiles(revoke = false) {
  if (revoke) for (const file of newThreadFiles) {
    const url = filePreviewUrls.get(file);
    if (url) URL.revokeObjectURL(url);
    filePreviewUrls.delete(file);
  }
  newThreadFiles = [];
  renderNewThreadUploadTray();
}

function openNewThreadDialog(preferredProjectId = null) {
  const host = currentHost();
  if (!host || !host.online || !host.desktopConnected || busy) return;
  syncAttachmentCapabilities();
  $('newThreadTitle').textContent = `新建 ${APPLICATION_LABELS[applicationId(host)] || applicationId(host)} 对话`;
  $('newThreadPrompt').placeholder = `描述你希望 ${APPLICATION_LABELS[applicationId(host)] || applicationId(host)} 完成的任务…`;
  const meta = selectedThread();
  let projectId = preferredProjectId;
  if (projectId === null) {
    projectId = projectFilter !== 'all' ? projectFilter :
      selected?.host === host.id ? (meta?.projectId || 'unassigned') :
      host.projects[0]?.id || 'unassigned';
  }
  if (projectId === 'unassigned') projectId = '';
  setOptions($('newThreadProject'), [
    {value: '', label: '未分类（独立任务）'},
    ...host.projects.map(project => ({value: project.id, label: project.name}))
  ], projectId);

  const activeModel = selected?.host === host.id ? (current?.model || meta?.model || '') : '';
  const model = host.models.some(item => item.id === activeModel) ? activeModel :
    host.models[0]?.id || '';
  setOptions($('newThreadModel'), host.models.map(item => ({
    value: item.id, label: item.label, title: item.description
  })), model);
  $('newThreadModel').disabled = !host.models.length;
  populateNewThreadEfforts(selected?.host === host.id ?
    (current?.reasoningEffort || meta?.reasoningEffort || '') : '');
  $('threadDeviceHint').textContent = `目标电脑：${host.label}`;
  $('newThreadPrompt').value = '';
  $('threadError').textContent = '';
  clearNewThreadFiles(true);
  syncNewThreadPasteButton();
  $('threadDialog').showModal();
  $('newThreadPrompt').focus();
}

function syncThreadControls() {
  syncProjectLocator();
  const host = currentHost();
  const meta = selectedThread();
  window.NativePanel?.syncCapabilities();
  syncAttachmentCapabilities();
  $('message').placeholder = `向原来的 ${APPLICATION_LABELS[applicationId(host)] || applicationId(host)} 对话发送消息…`;
  syncManualThreadRefreshButton();
  if (!meta || !host) {
    $('modelSettingsToggle').hidden = true;
    $('quickSpeedControl').hidden = true;
    $('quickSpeed').disabled = true;
    setModelSettingsOpen(false);
    $('fullAccess').closest('label').hidden = true;
    $('fullAccess').disabled = true;
    return;
  }

  const activeModel = current?.model || meta.model || '';
  const models = [...host.models];
  if (activeModel && !models.some(model => model.id === activeModel)) {
    models.push({id: activeModel, label: activeModel, efforts: []});
  }
  $('modelSettingsToggle').hidden = !models.length;
  syncSpeedControl(host, meta, activeModel);
  if (!models.length) setModelSettingsOpen(false);
  if (models.length) {
    const modelLabel = models.find(model => model.id === activeModel)?.label || activeModel || '未设置模型';
    const effortLabel = effortName(current?.reasoningEffort || meta.reasoningEffort || '') || '未设置档位';
    const modelName = document.createElement('span');
    if (modelLabel !== '未设置模型') modelName.dataset.i18nIgnore = 'true';
    modelName.textContent = modelLabel;
    const effortNameLabel = document.createElement('span'); effortNameLabel.textContent = effortLabel;
    $('modelSettingsSummary').replaceChildren(modelName, document.createTextNode(' · '), effortNameLabel);
    $('modelSettingsToggle').title = '展开模型与推理设置';
    $('modelControls').hidden = !document.body.classList.contains('composer-settings-open');
    const desired = modelDirty ? $('modelSelect').value : activeModel;
    setOptions($('modelSelect'), models.map(model => ({
      value: model.id, label: model.label, title: model.description
    })), desired);
    if (!modelDirty) populateEfforts($('modelSelect').value,
      current?.reasoningEffort || meta.reasoningEffort);
    $('modelSelect').disabled = busy || threadPreviewOnly || !host.online || meta.archived;
    $('effortSelect').disabled = busy || threadPreviewOnly || !host.online || meta.archived;
    $('applySettings').disabled = busy || threadPreviewOnly || !host.online || meta.archived ||
      !$('modelSelect').value || !$('effortSelect').value;
    $('cancelSettings').disabled = busy;
  }
  if (!permissionDirty) $('fullAccess').checked = Boolean(current?.fullAccess);
  $('fullAccess').closest('label').hidden = applicationId(host) !== 'codex' || host.capabilities?.permissions === false || !supportsAction(host, 'update_permissions');
  $('fullAccess').disabled = busy || !host.online || meta.archived ||
    !current?.owned || current?.source !== 'desktop-live';
}

function renderNativeThreadStatus(meta, thread, host, online) {
  const live = online && thread.owned && thread.source === 'desktop-live' && !meta.archived;
  const nativeStatus = thread.status || meta.status || 'notLoaded';
  const nativeUnread = meta.unread ?? thread.unread;
  const running = nativeStatus === 'active' && !thread.capacityRetry;
  const desktopConnected = Boolean(host?.desktopConnected && online);
  const pendingRequests = thread.pendingRequests ?? meta.pendingRequests ?? 0;
  const phase = nativeExecutionFeedback(thread).phase;
  $('status').textContent = !host?.online ? '设备离线' :
    meta.archived ? '已归档' :
    !desktopConnected ? '桌面 App 未启动' :
    pendingRequests > 0 ? `◆ 待确认 ${pendingRequests}` :
    ['preparing', 'resuming', 'waiting_approval', 'waiting_input'].includes(phase) ? EXECUTION_PHASE_LABELS[phase] :
    running ? '● ' + (EXECUTION_PHASE_LABELS[phase] || '正在运行') :
    nativeUnread === true ? '✓ 已完成 · 未读' :
    nativeUnread === false ? (live ? '已读 · 当前空闲' : '已读 · 未接入') :
    live ? '当前空闲' : '未接入';
  $('status').className = 'status ' + (!host?.online || !desktopConnected ? 'offline' :
    pendingRequests > 0 ? 'pending' :
    running ? 'active' : nativeUnread === true ? 'unread' : live ? 'live' : '');
  return {live, nativeStatus, nativeUnread, running, desktopConnected};
}

function syncManualThreadRefreshButton() {
  const key = selected ? threadKey(selected.host, selected.thread) : '';
  const refreshing = manualThreadRefreshes.has(key);
  $('refreshThreadMenu').setAttribute('aria-busy', String(refreshing));
}

async function refreshThreadFromSource(hostId = selected?.host, threadId = selected?.thread) {
  const {host, thread} = sidebarThreadTarget(hostId, threadId);
  const target = {host: hostId, thread: threadId};
  const key = threadKey(hostId, threadId);
  if (!host?.online || !thread || thread.unavailable || manualThreadRefreshes.has(key)) return;
  manualThreadRefreshes.add(key);
  syncManualThreadRefreshButton();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    if (selected?.host !== hostId || selected?.thread !== threadId) {
      await selectThread(host, thread, true, false);
    }
    if (selected?.host === hostId && selected?.thread === threadId) {
      $('sendResult').className = '';
      $('sendResult').textContent = '正在重新读取目标 App 的最新对话…';
    }
    await issueCommand('native_read', {...target, payload: {operation: 'refresh_thread'}, signal: controller.signal});
    if (selected?.host !== hostId || selected?.thread !== threadId || $('workspace').hidden) return;
    // Detach stale foreground requests and abort optional preload reads. Do not
    // discard other chats, draft attachments, local send receipts, or history.
    preloadController?.abort();
    threadSnapshots.delete(key);
    threadFetches.delete(key);
    const loaded = await refreshThread(true, {signal: controller.signal});
    if (selected?.host !== hostId || selected?.thread !== threadId) return;
    if (!loaded) throw new Error('目标 App 已重新读取，但快照尚未返回，请重试');
    // Keep valid recent/expanded history, including any read already in flight.
    // Fetch another page only when the new head has a genuine gap or fewer than
    // five conversations remain cached, not on every manual refresh.
    await ensureRecentHistory(target, {manual: true, signal: controller.signal});
    if (controller.signal.aborted) throw controller.signal.reason;
    if (selected?.host === hostId && selected?.thread === threadId) {
      $('sendResult').className = historyPages.get(historyKey(target))?.error ? 'error' : '';
      $('sendResult').textContent = historyPages.get(historyKey(target))?.error ?
        '最新对话已刷新；近期历史读取失败：' + historyPages.get(historyKey(target)).error : '✓ 已刷新最新对话';
    }
  } catch (error) {
    if (selected?.host === hostId && selected?.thread === threadId && !$('workspace').hidden) {
      $('sendResult').className = 'error';
      $('sendResult').textContent = controller.signal.aborted ? '刷新超时，请稍后重试；正在运行的任务未停止' : '刷新对话失败：' + error.message;
    }
  } finally {
    clearTimeout(timer);
    manualThreadRefreshes.delete(key);
    syncManualThreadRefreshButton();
  }
}

async function refreshThread(afterCommand = false, options = {}) {
  if (!selected) return false;
  const selection = selected;
  const target = {...selection};
  const reply = await fetchThreadSnapshot(target, afterCommand, options);
  if (selected !== selection) return false;
  const thread = reply?.thread;
  const meta = selectedThread();
  if (!thread || !meta) {
    syncThreadControls();
    return false;
  }
  renderThreadSnapshot(thread, meta, target, reply.online, threadWatchPending, reply.revision);
  return true;
}

function renderThreadSnapshot(thread, meta, target, online, preview = false, revision = 0) {
  reconcileLocalOutgoing(target, thread);
  const previous = !threadPreviewOnly && !threadWatchPending && current?.id === thread.id ? current : null;
  threadPreviewOnly = preview;
  current = {...meta, ...thread, unread: meta.unread ?? thread.unread,
    online: preview ? false : online, owned: preview ? false : thread.owned};
  const queuedMessages = Array.isArray(thread.queuedMessages) ? thread.queuedMessages : [];
  const host = currentHost();
  const {live, nativeStatus, nativeUnread, running, desktopConnected} =
    renderNativeThreadStatus(meta, current, host, current.online);
  const attachable = Boolean(!preview && host?.online && !live && !meta.archived &&
    ((supportsAction(host, 'attach_thread') && (desktopConnected || host.capabilities?.launchApp !== false)) ||
     host.capabilities?.clientControl?.startBridge));
  $('title').textContent = plainTitle(thread.title || meta.title);
  $('subtitle').textContent = `${host?.label || target.host} · ${meta.projectName || '未分类'} · ${thread.cwd || meta.cwd || ''}`;
  renderPendingRequests(thread.requests || [], thread.pendingRequests || 0,
    host?.label || '目标机', live);
  renderQuestionDock(current);
  $('message').disabled = !live || (busy && !composerDispatch);
  $('addAttachment').disabled = !live || busy;
  syncCopiedFileButton();
  $('send').disabled = !live || busy;
  $('send').hidden = !live;
  $('stopTurn').hidden = !live || !running;
  $('stopTurn').disabled = !live || !running || busy;
  $('attachThread').hidden = !attachable;
  $('attachThread').disabled = busy;
  $('attachThread').textContent = desktopConnected ? '接入原会话' : '启动并接入';
  syncSendButton();
  $('messageHint').textContent = mobileQuery.matches || touchKeyboardQuery.matches ?
    '回车换行 · 点击发送按钮提交' : running ?
      '可拖入或粘贴文件 · Enter 加入队列 · Shift + Enter 换行' :
      '可拖入或粘贴文件 · Enter 发送 · Shift + Enter 换行';
  renderQueuedMessages(queuedMessages, running, Boolean(!preview && host?.online && !meta.archived));
  syncThreadControls();

  const items = Array.isArray(thread.items) ? thread.items : [];
  const last = items[items.length - 1] || {};
  const signature = JSON.stringify([
    target.host, target.thread, preview, revision, thread.revision ?? null, nativeStatus,
    nativeUnread ?? null, thread.canContinue ?? false,
    thread.editableUserMessage?.itemId || '',
    thread.asyncQuestionState ?? null, thread.executionState ?? null,
    host?.desktopConnected ?? false, current.online,
    thread.totalItems ?? items.length, items.length, last.id || '', last.status || '',
    String(last.text || '').length, String(last.detail || '').length,
    (localOutgoingMessages.get(historyKey(target)) || []).map(record =>
      [record.commandId, record.item.localDelivery, record.item.localError])
  ]);
  if (signature !== threadRenderSignature) {
    if (!preview && previous) rememberRecentLiveHistory(target, thread, previous);
    renderTimeline(thread);
    threadRenderSignature = signature;
  }
  if (preview) {
    $('status').textContent = '缓存预览 · 正在同步最新内容';
    $('status').className = 'status';
  }
  threadReadSnapshot = preview ? null : {key: threadKey(target.host, target.thread),
    signature: `${relayGeneration}/${revision}`, unread: thread.unread === true,
    receipt: thread.readReceipt || ''};
  scheduleVisibleThreadRead();
  if (!preview && !threadWatchPending) void ensureRecentHistory(target);
}

async function issueCommand(action, {host, thread = '', text = '', payload = {}, attachments = [], commandId = '', signal} = {}) {
  const id = commandId || uuid();
  const body = {id, host, thread, action};
  if (action === 'send' || action === 'steer') {
    body.text = text;
    body.attachments = attachments;
  }
  else if (action !== 'watch') body.payload = payload;
  await api('/api/command', body, {signal});
  const deadline = Date.now() + (95000);
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 700));
    signal?.throwIfAborted();
    const value = await api('/api/command?id=' + encodeURIComponent(id), undefined, {signal});
    if (value.result) {
      if (!value.result.ok) {
        const error = new Error(value.result.error || '目标机没有接受操作');
        const uncertain = value.result.unknown === true ||
          (['send', 'steer'].includes(action) && /(?:-timeout$|请求超时|确认超时)/i.test(error.message));
        error.commandRejected = !uncertain;
        error.deliveryUnknown = uncertain;
        throw error;
      }
      return value.result;
    }
  }
  throw new Error('确认超时；请先查看目标机状态，不要重复提交');
}

async function terminalApi(action, host, payload = {}, options = {}) {
  return api('/api/terminal', {action, host, payload}, options);
}

async function fileApi(action, host, payload = {}) {
  return api('/api/files', {action, host, payload});
}

function applyTerminalView() {
  const files = terminalView === 'files';
  const agents = terminalView === 'agents';
  if (!files) closeFileContextMenu();
  $('terminalPanelTitle').textContent = agents ? '子智能体' : files ? '目标机文件' : 'SSH 终端';
  const hostId = $('terminalHostSelect').value;
  const host = catalog.find(item => item.id === hostId);
  const session = terminalSessions.get(activeTerminalId);
  $('terminalHostHint').textContent = agents ? uiText('来自原 App 的子智能体会话') : files ?
    `${host?.label || '目标电脑'} · 浏览、编辑、加入对话` : session ?
      `${session.hostLabel} · ${session.shell} · ${session.cwd}` :
      `${host?.label || '目标电脑'} · 尚未启动 SSH 终端`;
  $('terminalModeTerminal').setAttribute('aria-selected', String(!files && !agents));
  $('terminalModeFiles').setAttribute('aria-selected', String(files));
  $('terminalModeAgents').setAttribute('aria-selected', String(agents));
  $('newTerminal').hidden = agents;
  $('terminalTabs').hidden = files || agents;
  $('terminalStack').hidden = files || agents;
  $('terminalStatus').hidden = files || agents;
  $('fileBrowser').hidden = !files;
  $('subagentPanel').hidden = !agents;
  if (agents) renderSubagents();
  if (files) applyFileTreeLayout();
  if (!files && !agents) fitActiveTerminal();
}

function renderSubagents() {
  const physical = $('terminalHostSelect').value || physicalHostId();
  const host = catalog.find(item => applicationId(item) === 'codex' && physicalHostId(item) === physical);
  const rows = $('subagentRows');
  rows.replaceChildren();
  const all = (host?.threads || []).filter(thread => thread.isSubagent === true);
  const scope = $('subagentScope').value;
  const root = selected?.host === host?.id ? selected.thread : '';
  const family = new Set(root ? [root] : []);
  if (root) {
    // Walk native parent IDs only, with a bounded/fixed-point cycle guard.
    for (let depth = 0; depth < all.length; depth++) {
      const before = family.size;
      for (const thread of all) if (family.has(thread.parentThreadId)) family.add(thread.id);
      if (family.size === before) break;
    }
  }
  const visible = all.filter(thread => scope === 'all' || family.has(thread.id));
  $('subagentCount').textContent = uiText(`${visible.length} 个`);
  const byId = new Map((host?.threads || []).map(thread => [thread.id, thread]));
  for (const thread of visible) {
    const card = document.createElement('div');
    card.className = 'subagent-card';
    const name = document.createElement('p');
    name.className = 'subagent-identity';
    name.textContent = thread.agentNickname || thread.agentPath || uiText('子智能体');
    if (thread.agentRole) name.textContent += ' · ' + thread.agentRole;
    const parent = document.createElement('p');
    parent.className = 'subagent-parent';
    const original = byId.get(thread.parentThreadId);
    parent.textContent = original ? uiText('来源对话') + ' · ' + plainTitle(original.title) : uiText('未提供父对话关联');
    card.append(name, parent);
    appendThread(card, host, thread);
    rows.append(card);
  }
  if (!visible.length) {
    const empty = document.createElement('p');
    empty.className = 'history-note';
    empty.textContent = uiText(scope === 'current' ? '当前对话没有已关联的子智能体；可切换本机全部查看' : '本机没有子智能体会话');
    rows.append(empty);
  }
}

function fileDirectoryContext(hostId) {
  const active = currentHost();
  const host = physicalHostId(active) === hostId ? active : catalog.find(item => item.id === hostId);
  const selectedHere = selected?.host === host?.id;
  const thread = selectedHere ? selectedThread() : host && rememberedThread(host);
  const projectId = thread?.projectId || (physicalHostId() === hostId && !workspaceMode &&
    !['all', 'unassigned'].includes(projectFilter) ? projectFilter : '');
  const project = host?.projects?.find(item => item.id === projectId);
  const cwd = (selectedHere && current?.id === thread?.id && current?.cwd) || thread?.cwd || '';
  const path = project?.roots?.[0] || cwd || filePaths.get(hostId) || '';
  return {path, key: JSON.stringify([hostId, project?.id || '', project?.roots?.[0] || cwd || ''])};
}

function fileStartPath(hostId) {
  return fileDirectoryContext(hostId).path;
}

function syncFileDirectoryContext(force = false) {
  if (!terminalPanelOpen || terminalView !== 'files') return;
  const hostId = $('terminalHostSelect').value;
  if (!hostId) return;
  const context = fileDirectoryContext(hostId);
  if (!force && context.key === fileDefaultContextKey && fileBrowserState?.host === hostId) return;
  fileDefaultContextKey = context.key;
  void loadFileDirectory(hostId, context.path);
}

function openProjectFileDirectory(hostId, path) {
  hostId = physicalHostId(catalog.find(host => host.id === hostId)) || hostId;
  if (![...$('terminalHostSelect').options].some(option => option.value === hostId)) return;
  $('terminalHostSelect').value = hostId;
  terminalView = 'files';
  showTerminalPanel();
  fileDefaultContextKey = fileDirectoryContext(hostId).key;
  void loadFileDirectory(hostId, path);
}

function setTerminalView(view) {
  terminalView = ['files', 'agents'].includes(view) ? view : 'terminal';
  applyTerminalView();
  syncTerminalPanelPolling();
  if (terminalView === 'files') {
    const hostId = $('terminalHostSelect').value;
    if (!hostId) {
      $('fileBrowserStatus').textContent = '没有可用的 SSH 目标电脑';
    } else {
      syncFileDirectoryContext();
      void ensureFileTreeRoots(hostId);
    }
  } else if (terminalPanelOpen && terminalView === 'terminal') {
    void syncServerTerminals().catch(error => setTerminalStatus(error.message, true));
    scheduleTerminalPoll(20);
  }
}

function fileTreeKey(hostId, path) {
  let key = String(path || '').replaceAll('\\', '/').replace(/\/+/g, '/');
  if (key.length > 1 && !/^[A-Za-z]:\/$/.test(key)) key = key.replace(/\/+$/, '');
  return catalog.find(item => item.id === hostId)?.terminal?.kind === 'windows' ?
    key.toLowerCase() : key;
}

function fileTreeState(hostId) {
  if (!fileTreeStates.has(hostId)) {
    fileTreeStates.set(hostId, {roots: null, nodes: new Map(), activePath: '',
      rootsPromise: null, rootsVersion: 0, revealToken: 0, error: ''});
  }
  return fileTreeStates.get(hostId);
}

function fileTreeNode(state, hostId, path, name) {
  const key = fileTreeKey(hostId, path);
  if (!state.nodes.has(key)) state.nodes.set(key, {
    path, name: name || path, children: null, nextOffset: 0, total: 0,
    hasChildren: null, expanded: false, loading: null, error: ''});
  const node = state.nodes.get(key);
  if (name) node.name = name;
  return node;
}

function applyFileTreeLayout() {
  const mobile = mobileQuery.matches;
  $('fileTreePanel').hidden = !fileTreeOpen;
  $('fileListPanel').hidden = mobile && fileTreeOpen;
  $('fileTreeToggle').setAttribute('aria-expanded', String(fileTreeOpen));
  $('fileTreeToggle').textContent = fileTreeOpen ?
    (mobile ? '关闭目录' : '隐藏目录') : '目录索引';
}

function closeFileContextMenu() {
  $('fileContextMenu').hidden = true;
  fileContext = null;
}

function fileContextDirectory(context) {
  return context?.entry?.isDirectory ? context.entry.path : context?.directory;
}

function positionFileContextMenu(x, y) {
  const menu = $('fileContextMenu');
  if (menu.hidden) return;
  const viewport = window.visualViewport;
  const left = viewport?.offsetLeft || 0;
  const top = viewport?.offsetTop || 0;
  const width = viewport?.width || window.innerWidth;
  const height = viewport?.height || window.innerHeight;
  menu.style.left = `${Math.max(left + 8, Math.min(x, left + width - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(top + 8, Math.min(y, top + height - menu.offsetHeight - 8))}px`;
}

function showFileContextMenu(hostId, entry, directory, x, y) {
  if (!terminalPanelOpen || terminalView !== 'files' ||
      $('terminalHostSelect').value !== hostId) return;
  closeTerminalContextMenu();
  fileContext = {hostId, entry, directory};
  const target = fileContextDirectory(fileContext);
  $('fileContextTarget').textContent = entry?.path || directory || '当前位置';
  $('fileContextTarget').title = entry?.path || directory || '';
  $('fileNewFolder').disabled = !target || fileOperationBusy;
  $('fileNewFile').disabled = !target || fileOperationBusy;
  $('fileCopy').disabled = !entry || entry.isRoot || fileOperationBusy;
  $('filePaste').disabled = !target || !fileClipboard ||
    fileClipboard.hostId !== hostId || fileOperationBusy;
  $('fileDelete').disabled = !entry || entry.isRoot || fileOperationBusy;
  const menu = $('fileContextMenu');
  menu.hidden = false;
  positionFileContextMenu(x, y);
}

function installFileContextMenu(container, tree = false) {
  const context = target => {
    const row = target.closest(tree ? '.file-tree-item' : '.file-row');
    const entry = row && container.contains(row) ? row.fileEntry : null;
    const hostId = $('terminalHostSelect').value;
    const directory = fileBrowserState?.host === hostId ? fileBrowserState.path :
      fileStartPath(hostId);
    return {hostId, entry, directory};
  };
  container.addEventListener('contextmenu', event => {
    event.preventDefault();
    event.stopPropagation();
    const target = context(event.target);
    showFileContextMenu(target.hostId, target.entry, target.directory,
      event.clientX, event.clientY);
  });
  let press = null;
  let longPressAt = 0;
  const cancelPress = () => {
    if (press) clearTimeout(press.timer);
    press = null;
  };
  container.addEventListener('touchstart', event => {
    cancelPress();
    if (event.touches.length !== 1) return;
    const touch = event.touches[0];
    const target = context(event.target);
    press = {x: touch.clientX, y: touch.clientY,
      timer: setTimeout(() => {
        showFileContextMenu(target.hostId, target.entry, target.directory,
          touch.clientX, touch.clientY);
        longPressAt = Date.now();
        press = null;
      }, 550)};
  }, {passive: true});
  container.addEventListener('touchmove', event => {
    if (!press || event.touches.length !== 1 ||
        Math.hypot(event.touches[0].clientX - press.x,
          event.touches[0].clientY - press.y) > 12) cancelPress();
  }, {passive: true});
  container.addEventListener('touchend', cancelPress, {passive: true});
  container.addEventListener('touchcancel', cancelPress, {passive: true});
  container.addEventListener('click', event => {
    if (Date.now() - longPressAt < 900) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);
}

function setFileTreeStatus(message, error = false) {
  $('fileTreeStatus').textContent = message;
  $('fileTreeStatus').className = `file-tree-status${error ? ' error' : ''}`;
}

function renderFileTree(hostId) {
  if (terminalView !== 'files' || $('terminalHostSelect').value !== hostId) return;
  const state = fileTreeState(hostId);
  const rows = $('fileTreeRows');
  rows.replaceChildren();
  if (!state.roots) {
    rows.textContent = '正在读取目录索引…';
    return;
  }
  const renderNode = (node, depth, ancestors, displayName = node.name, parentPath = '') => {
    const key = fileTreeKey(hostId, node.path);
    if (depth > 20 || ancestors.has(key)) return;
    const row = document.createElement('div');
    row.className = 'file-tree-item' +
      (key === fileTreeKey(hostId, state.activePath) ? ' active' : '');
    row.setAttribute('role', 'treeitem');
    row.setAttribute('aria-level', String(depth + 1));
    const expandable = node.hasChildren !== false;
    if (expandable) row.setAttribute('aria-expanded', String(node.expanded));
    row.style.paddingInlineStart = `${4 + depth * 15}px`;
    row.fileEntry = {name: displayName, path: node.path, isDirectory: true,
      isRoot: depth === 0, parent: parentPath || node.path};
    const expand = document.createElement(expandable ? 'button' : 'span');
    expand.className = expandable ? 'file-tree-expand' : 'file-tree-expand-spacer';
    if (expandable) {
      expand.type = 'button';
      expand.textContent = node.loading ? '…' : node.expanded ? '▾' : '▸';
      expand.setAttribute('aria-label', `${node.expanded ? '收起' : '展开'} ${displayName}`);
      expand.onclick = () => {
        node.expanded = !node.expanded;
        renderFileTree(hostId);
        if (node.expanded) void loadFileTreeChildren(hostId, node);
      };
    } else {
      expand.setAttribute('aria-hidden', 'true');
    }
    const label = document.createElement('button');
    label.type = 'button';
    label.className = 'file-tree-label';
    label.textContent = `▣ ${displayName}`;
    label.title = node.path;
    label.onclick = () => {
      if (expandable) {
        node.expanded = true;
        void loadFileTreeChildren(hostId, node);
      }
      if (mobileQuery.matches) {
        fileTreeOpen = false;
        applyFileTreeLayout();
      }
      void loadFileDirectory(hostId, node.path);
    };
    row.append(expand, label);
    rows.append(row);
    if (!expandable || !node.expanded) return;
    const next = new Set(ancestors);
    next.add(key);
    for (const child of node.children || []) renderNode(child, depth + 1, next,
      child.name, node.path);
    if (node.children && !node.children.length) {
      const empty = document.createElement('div');
      empty.className = 'file-tree-status';
      empty.style.paddingInlineStart = `${34 + depth * 15}px`;
      empty.textContent = '无子目录';
      rows.append(empty);
    }
    if (node.nextOffset < node.total) {
      const more = document.createElement('button');
      more.type = 'button';
      more.className = 'secondary file-tree-more';
      more.style.marginInlineStart = `${28 + depth * 15}px`;
      more.textContent = '显示更多子目录';
      more.onclick = () => void loadFileTreeChildren(hostId, node, true);
      rows.append(more);
    }
    if (node.error) {
      const error = document.createElement('div');
      error.className = 'file-tree-status error';
      error.textContent = node.error;
      rows.append(error);
    }
  };
  for (const root of state.roots) {
    renderNode(fileTreeNode(state, hostId, root.path, root.name), 0, new Set(), root.name);
  }
  setFileTreeStatus(state.error || '点箭头展开文件夹；点名称打开', Boolean(state.error));
}

async function ensureFileTreeRoots(hostId, force = false) {
  const state = fileTreeState(hostId);
  if (state.roots && !force) return state.roots;
  if (state.rootsPromise && !force) return state.rootsPromise;
  if (force) {
    state.nodes.clear();
    state.roots = null;
  }
  const version = ++state.rootsVersion;
  state.error = '';
  renderFileTree(hostId);
  const promise = fileApi('roots', hostId).then(result => {
    if (version !== state.rootsVersion) return state.roots || [];
    state.roots = result.roots || [];
    for (const root of state.roots) {
      const node = fileTreeNode(state, hostId, root.path, root.name);
      node.hasChildren = typeof root.hasChildren === 'boolean' ? root.hasChildren : null;
      if (node.hasChildren === false) node.expanded = false;
    }
    renderFileTree(hostId);
    return state.roots;
  }).catch(error => {
    if (version !== state.rootsVersion) return state.roots || [];
    state.error = error.message;
    state.roots = [];
    renderFileTree(hostId);
    return [];
  }).finally(() => { if (state.rootsPromise === promise) state.rootsPromise = null; });
  state.rootsPromise = promise;
  return promise;
}

async function loadFileTreeChildren(hostId, node, append = false) {
  if (node.loading) return node.loading;
  if (node.children && !append) return;
  const offset = append ? node.nextOffset : 0;
  node.loading = fileApi('folders', hostId, {path: node.path, offset, limit: 200})
    .then(result => {
      const state = fileTreeState(hostId);
      const children = append ? [...(node.children || [])] : [];
      const seen = new Set(children.map(item => fileTreeKey(hostId, item.path)));
      for (const entry of result.entries || []) {
        const child = fileTreeNode(state, hostId, entry.path, entry.name);
        child.hasChildren = typeof entry.hasChildren === 'boolean' ?
          entry.hasChildren : null;
        if (child.hasChildren === false) child.expanded = false;
        const key = fileTreeKey(hostId, entry.path);
        if (!seen.has(key)) children.push(child);
        seen.add(key);
      }
      node.children = children.sort((a, b) => a.name.localeCompare(b.name));
      node.nextOffset = result.nextOffset;
      node.total = result.total;
      node.hasChildren = result.total > 0;
      if (!node.hasChildren) node.expanded = false;
      node.error = '';
    }).catch(error => { node.error = error.message; })
    .finally(() => { node.loading = null; renderFileTree(hostId); });
  renderFileTree(hostId);
  return node.loading;
}

function fileTreeUnderRoot(hostId, path, root) {
  const value = fileTreeKey(hostId, path);
  const prefix = fileTreeKey(hostId, root);
  return value === prefix || value.startsWith(prefix.endsWith('/') ? prefix : `${prefix}/`);
}

async function revealFileTreePath(hostId, path) {
  const state = fileTreeState(hostId);
  const token = ++state.revealToken;
  state.activePath = path;
  renderFileTree(hostId);
  const roots = await ensureFileTreeRoots(hostId);
  if (token !== state.revealToken || !roots.length) return;
  const matches = roots.filter(root => fileTreeUnderRoot(hostId, path, root.path))
    .sort((a, b) => b.path.length - a.path.length);
  const root = matches[0] || {name: '当前位置', path, kind: 'current'};
  if (!matches.length) state.roots.unshift(root);
  let node = fileTreeNode(state, hostId, root.path, root.name);
  const relative = path.replaceAll('\\', '/').slice(root.path.replaceAll('\\', '/').length)
    .replace(/^\/+/, '');
  let current = root.path.replace(/[\\/]+$/, '');
  const separator = root.path.includes('\\') ? '\\' : '/';
  for (const part of relative.split('/').filter(Boolean)) {
    node.expanded = true;
    await loadFileTreeChildren(hostId, node);
    if (token !== state.revealToken) return;
    current = `${current}${separator}${part}`;
    const key = fileTreeKey(hostId, current);
    let child = (node.children || []).find(item => fileTreeKey(hostId, item.path) === key);
    if (!child) {
      child = fileTreeNode(state, hostId, current, part);
      node.children = [...(node.children || []), child]
        .sort((a, b) => a.name.localeCompare(b.name));
    }
    node = child;
  }
  node.expanded = true;
  await loadFileTreeChildren(hostId, node);
  if (token !== state.revealToken) return;
  renderFileTree(hostId);
}

function renderFileRow(hostId, entry) {
  const row = document.createElement('button');
  row.type = 'button';
  row.className = 'file-row';
  row.title = entry.path;
  const icon = document.createElement('span');
  icon.className = 'file-row-icon';
  icon.textContent = entry.isDirectory ? '▣' : '▤';
  const details = document.createElement('span');
  details.className = 'file-row-details';
  const name = document.createElement('strong');
  name.textContent = entry.name;
  const meta = document.createElement('small');
  const modified = entry.modified ? new Date(entry.modified * 1000).toLocaleString() : '';
  meta.textContent = `${entry.isDirectory ? '文件夹' :
    (entry.size === 0 ? '0 B' : formatBytes(entry.size))}${modified ? ' · ' + modified : ''}`;
  details.append(name, meta);
  const arrow = document.createElement('span');
  arrow.className = 'file-row-arrow';
  arrow.textContent = '›';
  row.append(icon, details, arrow);
  row.fileEntry = {...entry, parent: fileBrowserState?.path || ''};
  row.onclick = () => entry.isDirectory ?
    loadFileDirectory(hostId, entry.path) : openRemoteFilePreview(hostId, entry);
  return row;
}

async function loadFileDirectory(hostId, path = '', offset = 0, append = false) {
  const token = ++fileBrowserToken;
  $('fileBrowserStatus').textContent = '正在读取文件夹…';
  $('fileBrowserStatus').className = '';
  $('fileLoadMore').disabled = true;
  try {
    const result = await fileApi('list', hostId, {path, offset, limit: 250});
    if (token !== fileBrowserToken || terminalView !== 'files' ||
        $('terminalHostSelect').value !== hostId) return;
    fileBrowserState = {host: hostId, path: result.path, parent: result.parent,
      total: result.total, nextOffset: result.nextOffset};
    filePaths.set(hostId, result.path);
    $('filePath').value = result.path;
    if (!append) void revealFileTreePath(hostId, result.path);
    const rows = $('fileRows');
    if (!append) rows.replaceChildren();
    for (const entry of result.entries || []) rows.append(renderFileRow(hostId, entry));
    if (!rows.childElementCount) rows.textContent = '这个文件夹是空的';
    $('fileBrowserStatus').textContent = `${Math.min(result.nextOffset, result.total)} / ${result.total} 项`;
    $('fileGoParent').disabled = result.parent === result.path;
    $('fileLoadMore').hidden = result.nextOffset >= result.total;
    $('fileLoadMore').disabled = false;
  } catch (error) {
    if (token !== fileBrowserToken) return;
    $('fileBrowserStatus').textContent = error.message;
    $('fileBrowserStatus').className = 'error';
    $('fileLoadMore').disabled = false;
  }
}

function setFileBrowserStatus(message, error = false) {
  $('fileBrowserStatus').textContent = message;
  $('fileBrowserStatus').className = error ? 'error' : '';
}

function refreshFileTreeFolder(hostId, path) {
  const state = fileTreeState(hostId);
  const node = state.nodes.get(fileTreeKey(hostId, path));
  if (!node) return;
  node.children = null;
  node.nextOffset = 0;
  node.total = 0;
  node.hasChildren = null;
  void loadFileTreeChildren(hostId, node);
}

async function showChangedFileDirectory(hostId, directory, message) {
  refreshFileTreeFolder(hostId, directory);
  if (terminalPanelOpen && terminalView === 'files' &&
      $('terminalHostSelect').value === hostId) {
    await loadFileDirectory(hostId, directory);
    if ($('fileBrowserStatus').className !== 'error') setFileBrowserStatus(message);
  }
}

function openFileCreateDialog(kind) {
  const context = fileContext;
  const directory = fileContextDirectory(context);
  if (!context || !directory || fileOperationBusy) return;
  fileCreateContext = {hostId: context.hostId, directory, kind};
  closeFileContextMenu();
  $('fileCreateTitle').textContent = kind === 'directory' ? '新建文件夹' : '新建文件';
  $('fileCreateLocation').textContent = directory;
  $('fileCreateHint').textContent = kind === 'file' ?
    '创建空文件；可在名称中填写 .txt、.md、.py 等扩展名，再用预览窗口编辑 UTF-8 内容。' :
    '在这个目录中创建新文件夹。';
  $('fileCreateName').value = '';
  $('fileCreateError').textContent = '';
  $('fileCreateDialog').showModal();
  $('fileCreateName').focus();
}

async function submitFileCreate(event) {
  event.preventDefault();
  const context = fileCreateContext;
  if (!context || fileOperationBusy) return;
  fileOperationBusy = true;
  $('fileCreateConfirm').disabled = true;
  $('fileCreateError').textContent = '';
  try {
    const result = await fileApi('create', context.hostId, {
      path: context.directory, name: $('fileCreateName').value, kind: context.kind
    });
    $('fileCreateDialog').close();
    fileCreateContext = null;
    await showChangedFileDirectory(context.hostId, context.directory,
      `已新建 ${result.name}`);
  } catch (error) {
    $('fileCreateError').textContent = error.message;
  } finally {
    fileOperationBusy = false;
    $('fileCreateConfirm').disabled = false;
  }
}

function openFileDeleteDialog() {
  const context = fileContext;
  const entry = context?.entry;
  if (!entry || entry.isRoot || fileOperationBusy) return;
  fileDeleteContext = {hostId: context.hostId, entry,
    directory: entry.parent || context.directory};
  closeFileContextMenu();
  $('fileDeleteTarget').textContent = entry.path;
  $('fileDeleteWarning').textContent = entry.isDirectory ?
    '此文件夹及内部全部内容将永久删除，无法撤销。' : '此文件将永久删除，无法撤销。';
  $('fileDeleteNameLabel').hidden = !entry.isDirectory;
  $('fileDeleteName').hidden = !entry.isDirectory;
  $('fileDeleteName').value = '';
  $('fileDeleteError').textContent = '';
  $('fileDeleteDialog').showModal();
  if (entry.isDirectory) $('fileDeleteName').focus();
}

async function submitFileDelete(event) {
  event.preventDefault();
  const context = fileDeleteContext;
  if (!context || fileOperationBusy) return;
  if (context.entry.isDirectory && $('fileDeleteName').value !== context.entry.name) {
    $('fileDeleteError').textContent = '请输入完全一致的文件夹名称';
    return;
  }
  fileOperationBusy = true;
  $('fileDeleteConfirm').disabled = true;
  $('fileDeleteError').textContent = '';
  try {
    await fileApi('delete', context.hostId, {
      path: context.entry.path, confirmName: context.entry.name,
      recursive: context.entry.isDirectory
    });
    $('fileDeleteDialog').close();
    fileDeleteContext = null;
    for (const tab of [...filePreviewTabs]) {
      if (tab.hostId !== context.hostId || !tab.sourcePath) continue;
      if (tab.sourcePath === context.entry.path ||
          (context.entry.isDirectory && fileTreeUnderRoot(context.hostId,
            tab.sourcePath, context.entry.path))) closeFilePreviewTab(tab.id);
    }
    if (fileClipboard?.hostId === context.hostId &&
        (fileClipboard.path === context.entry.path ||
         (context.entry.isDirectory && fileTreeUnderRoot(context.hostId,
           fileClipboard.path, context.entry.path)))) {
      fileClipboard = null;
      syncCopiedFileButton();
    }
    await showChangedFileDirectory(context.hostId, context.directory,
      `已删除 ${context.entry.name}`);
  } catch (error) {
    $('fileDeleteError').textContent = error.message;
  } finally {
    fileOperationBusy = false;
    $('fileDeleteConfirm').disabled = false;
  }
}

async function pasteFileContext() {
  const context = fileContext;
  const clipboard = fileClipboard;
  const directory = fileContextDirectory(context);
  if (!context || !clipboard || !directory || fileOperationBusy) return;
  if (clipboard.hostId !== context.hostId) {
    setFileBrowserStatus('复制和粘贴目前仅支持同一台电脑', true);
    closeFileContextMenu();
    return;
  }
  closeFileContextMenu();
  fileOperationBusy = true;
  setFileBrowserStatus(`正在复制 ${clipboard.name}…`);
  try {
    const result = await fileApi('copy', context.hostId, {
      path: clipboard.path, destination: directory
    });
    await showChangedFileDirectory(context.hostId, directory,
      `已粘贴 ${result.name}`);
  } catch (error) {
    setFileBrowserStatus(`粘贴失败：${error.message}`, true);
  } finally {
    fileOperationBusy = false;
  }
}

async function loadRemoteBlob(hostId, path, maxBytes, tab, partial = false) {
  const chunks = [];
  let offset = 0;
  let metadata = null;
  while (offset < maxBytes) {
    if (tab?.closed) throw new Error('预览已关闭');
    const result = await fileApi('read', hostId, {path, offset,
      limit: Math.min(2 * 1024 * 1024, maxBytes - offset)});
    if (tab?.closed) throw new Error('预览已关闭');
    const bytes = decodeBase64(result.data);
    if (result.offset !== offset || result.nextOffset !== offset + bytes.length) {
      throw new Error('文件分片顺序不一致');
    }
    if (!partial && result.size > maxBytes) throw new Error('文件超过可下载或预览上限');
    metadata = result;
    if (!bytes.length && !result.eof) throw new Error('文件读取中断');
    chunks.push(bytes);
    offset = result.nextOffset;
    if (path === tab?.sourcePath) setPreviewStatus(tab,
      `已读取 ${formatBytes(offset)} / ${formatBytes(result.size)}`);
    if (result.eof) break;
  }
  return {blob: new Blob(chunks, {type: metadata?.mime || 'application/octet-stream'}),
    size: metadata?.size || 0, mime: metadata?.mime || '', truncated: offset < (metadata?.size || 0)};
}

async function downloadRemoteFile(hostId, entry, tab) {
  if (entry.size > 256 * 1024 * 1024) throw new Error('下载上限为 256 MB');
  const result = await loadRemoteBlob(hostId, entry.path, 256 * 1024 * 1024,
    tab, false);
  if (result.truncated) throw new Error('文件超过下载上限');
  const url = URL.createObjectURL(result.blob);
  downloadBlobUrl(url, entry.name);
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  setPreviewStatus(tab, '已开始下载');
}

async function openRemoteFilePreview(hostId, entry) {
  const kind = filePreviewKind(entry.name);
  const {tab, existing} = beginFilePreview(`remote/${hostId}/${entry.path}`, entry.name,
    `${catalog.find(item => item.id === hostId)?.label || hostId} · ${entry.path} · ${entry.size === 0 ? '0 B' : formatBytes(entry.size)}`,
    null, kind);
  if (existing) return;
  tab.hostId = hostId;
  tab.sourcePath = entry.path;
  tab.fileEntry = entry;
  tab.downloader = () => downloadRemoteFile(hostId, entry, tab);
  $('filePreviewDownload').disabled = false;
  if (!kind) {
    tab.pane.textContent = '这种格式暂不支持在线预览，可下载后打开。';
    return;
  }
  if (kind !== 'text' && kind !== 'markdown' && entry.size > previewLimits[kind]) {
    tab.pane.textContent = `文件超过在线预览上限（${formatBytes(previewLimits[kind])}），可下载后查看。`;
    return;
  }
  try {
    const result = await loadRemoteBlob(hostId, entry.path, previewLimits[kind],
      tab, kind === 'text' || kind === 'markdown');
    if (tab.closed) return;
    tab.previewBlob = result.blob;
    tab.previewTruncated = result.truncated;
    if (!tab.editing) await renderFilePreview(result.blob, tab, result.truncated);
  } catch (error) {
    if (!tab.closed) tab.pane.textContent = `无法预览：${error.message}`;
  }
}

async function cancelRemoteFileEdit(tab) {
  if (!tab?.editing) return;
  if (tab.editorInput?.value !== tab.editorOriginal &&
      !confirm('放弃尚未保存的文件修改？')) return;
  tab.editing = false;
  tab.editorInput = null;
  if (tab.previewBlob) await renderFilePreview(tab.previewBlob, tab, tab.previewTruncated);
  else tab.pane.textContent = '这种格式暂不支持在线预览，可下载后打开。';
  activateFilePreview(tab.id);
}

async function openRemoteFileEditor(tab) {
  if (!tab?.key.startsWith('remote/') || tab.closed) return;
  if (tab.editing) return cancelRemoteFileEdit(tab);
  setPreviewStatus(tab, '正在打开文本编辑器…');
  try {
    const result = await issueCommand('file_read_text', {host: tab.hostId,
      payload: {path: tab.sourcePath}});
    if (tab.closed) return;
    tab.editing = true;
    tab.editorOriginal = result.content;
    tab.editorSha = result.sha256;
    const wrapper = document.createElement('div');
    wrapper.className = 'file-editor';
    const editor = document.createElement('textarea');
    editor.value = result.content;
    editor.spellcheck = false;
    editor.setAttribute('aria-label', `编辑 ${tab.name}`);
    const actions = document.createElement('div');
    actions.className = 'file-editor-actions';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'secondary';
    cancel.textContent = '取消';
    cancel.onclick = () => cancelRemoteFileEdit(tab);
    const save = document.createElement('button');
    save.type = 'button';
    save.textContent = '保存到目标机';
    save.onclick = async () => {
      save.disabled = true;
      cancel.disabled = true;
      setPreviewStatus(tab, '正在保存到目标机…');
      try {
        const written = await issueCommand('file_write_text', {host: tab.hostId,
          payload: {path: tab.sourcePath, expectedSha256: tab.editorSha,
            content: editor.value}});
        if (tab.closed) return;
        tab.editorSha = written.sha256;
        tab.editing = false;
        tab.editorInput = null;
        tab.kind = tab.kind === 'markdown' ? 'markdown' : 'text';
        tab.previewBlob = new Blob([editor.value], {type: 'text/plain'});
        tab.previewTruncated = false;
        await renderFilePreview(tab.previewBlob, tab);
        setPreviewStatus(tab, `已保存到 ${tab.hostId} · ${formatBytes(written.size)}`);
        activateFilePreview(tab.id);
        if (terminalView === 'files' && fileBrowserState?.host === tab.hostId) {
          void loadFileDirectory(tab.hostId, fileBrowserState.path);
        }
      } catch (error) {
        setPreviewStatus(tab, `保存失败：${error.message}`);
      } finally {
        save.disabled = false;
        cancel.disabled = false;
      }
    };
    actions.append(cancel, save);
    wrapper.append(editor, actions);
    tab.pane.replaceChildren(wrapper);
    tab.editorInput = editor;
    setPreviewStatus(tab, '编辑 UTF-8 文本；保存前会检查文件是否已被其他程序修改');
    activateFilePreview(tab.id);
    editor.focus();
  } catch (error) {
    setPreviewStatus(tab, `无法编辑：${error.message}`);
  }
}

function openRemoteFileAttach(tab) {
  if (!tab?.key.startsWith('remote/') || tab.closed) return;
  const host = physicalHostId() === tab.hostId ? currentHost() : catalog.find(item => item.id === tab.hostId);
  if (!supportsAction(host, 'attach_local_file')) {
    setPreviewStatus(tab, '当前原生应用不支持加入目标电脑文件');
    return;
  }
  const threads = (host?.threads || []).filter(item => !item.archived);
  if (!host?.online || !threads.length) {
    setPreviewStatus(tab, '目标电脑离线或没有可加入的对话');
    return;
  }
  $('fileAttachName').textContent = `${host.label} · ${tab.sourcePath}`;
  $('fileAttachStatus').textContent = '';
  $('fileAttachStatus').className = '';
  setOptions($('fileAttachThread'), threads.map(item => ({value: item.id,
    label: plainTitle(item.title)})),
    selected?.host === host.id && threads.some(item => item.id === selected.thread) ?
      selected.thread : threads[0].id);
  $('fileAttachDialog').dataset.previewId = tab.id;
  $('fileAttachDialog').dataset.applicationHost = host.id;
  $('fileAttachDialog').showModal();
}

function addRemoteUploadToDraft(hostId, threadId, result) {
  const file = {remoteUploadId: result.uploadId, hostId, threadId,
    name: result.name, type: result.mime, size: result.size, lastModified: 0};
  const key = `${hostId}/${threadId}`;
  const existing = attachmentDrafts.get(key) ||
    (selected?.host === hostId && selected?.thread === threadId ? pendingFiles : []);
  const files = [...existing.filter(item => pendingFileKey(item) !== pendingFileKey(file)), file];
  attachmentDrafts.set(key, files);
  if (selected?.host === hostId && selected?.thread === threadId) setPendingFiles(files);
  return file;
}

async function attachRemoteFileToThread(event) {
  event.preventDefault();
  const tab = filePreviewTabs.find(item => item.id === $('fileAttachDialog').dataset.previewId);
  const host = catalog.find(item => item.id === $('fileAttachDialog').dataset.applicationHost);
  const thread = host?.threads.find(item => item.id === $('fileAttachThread').value && !item.archived);
  if (!tab || !host || !thread) return;
  const button = $('fileAttachConfirm');
  button.disabled = true;
  $('fileAttachStatus').className = '';
  $('fileAttachStatus').textContent = '正在目标电脑暂存文件…';
  try {
    const result = await issueCommand('attach_local_file', {host: host.id, thread: thread.id,
      payload: {path: tab.sourcePath}});
    addRemoteUploadToDraft(host.id, thread.id, result);
    $('fileAttachDialog').close();
    $('filePreviewDialog').close();
    if (selected?.host !== host.id || selected?.thread !== thread.id) {
      await selectThread(host, thread, true);
    }
    $('sendResult').className = '';
    $('sendResult').textContent = `✓ ${result.name} 已加入待发送附件；发送前可继续添加文字`;
  } catch (error) {
    $('fileAttachStatus').textContent = error.message;
    $('fileAttachStatus').className = 'error';
  } finally {
    button.disabled = false;
  }
}

async function pasteCopiedRemoteFile() {
  const copied = fileClipboard;
  const target = selected && {...selected};
  if (!copied || copied.isDirectory || !target || busy || filePasteToComposerBusy ||
      copied.hostId !== physicalHostId() || $('addAttachment').disabled) return;
  const button = $('pasteCopiedFile');
  filePasteToComposerBusy = true;
  button.disabled = true;
  $('send').disabled = true;
  $('sendResult').className = '';
  $('sendResult').textContent = `正在从目标电脑暂存 ${copied.name}…`;
  try {
    const result = await issueCommand('attach_local_file', {
      host: target.host, thread: target.thread, payload: {path: copied.path}
    });
    addRemoteUploadToDraft(target.host, target.thread, result);
    if (selected?.host === target.host && selected?.thread === target.thread) {
      $('sendResult').textContent = `✓ ${result.name} 已加入待发送附件`;
      $('message').focus();
    }
  } catch (error) {
    if (selected?.host === target.host && selected?.thread === target.thread) {
      $('sendResult').className = 'error';
      $('sendResult').textContent = `粘贴文件失败：${error.message}`;
    }
  } finally {
    filePasteToComposerBusy = false;
    syncCopiedFileButton();
    if (selected?.host === target.host && selected?.thread === target.thread) {
      $('send').disabled = $('addAttachment').disabled || busy;
    }
  }
}

function terminalTheme() {
  const colors = theme === 'light' ? {
    background: '#f7f9f7', foreground: '#26322a', cursor: '#2e7540',
    selectionBackground: '#a9cdb0aa', black: '#27302a', red: '#a64232',
    green: '#28723a', yellow: '#8a6a21', blue: '#365f9c', magenta: '#78559a',
    cyan: '#28757a', white: '#e7ebe7', brightBlack: '#778179', brightWhite: '#ffffff'
  } : {
    background: '#101412', foreground: '#d1dbd3', cursor: '#79c587',
    selectionBackground: '#4e7558aa', black: '#121613', red: '#dd7966',
    green: '#7bd28a', yellow: '#d3b66e', blue: '#7ba2d7', magenta: '#b995d6',
    cyan: '#72c2c7', white: '#d7dfd9', brightBlack: '#6f7a72', brightWhite: '#ffffff'
  };
  if (palette !== 'green') {
    const style = getComputedStyle(document.documentElement);
    for (const [key, token] of Object.entries({background: 'bg', foreground: 'fg',
      cursor: 'cursor', selectionBackground: 'selection'})) {
      colors[key] = style.getPropertyValue(`--palette-terminal-${token}`).trim();
    }
  }
  return colors;
}

function syncTerminalThemes() {
  if (!terminalSessions) return;
  for (const session of terminalSessions.values()) {
    if (session.term) session.term.options.theme = terminalTheme();
  }
}

function decodeTerminalData(value) {
  const raw = atob(value || '');
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

function rememberTerminalSessions() {
  try {
    localStorage.setItem('codexRelayTerminalSessions', JSON.stringify({
      version: 3,
      open: terminalPanelOpen,
      activeId: activeTerminalId
    }));
  } catch (_) {}
}

async function syncServerTerminals() {
  if (terminalSyncPromise) return terminalSyncPromise;
  const revision = terminalSessionRevision;
  terminalSyncPromise = (async () => {
    const result = await terminalApi('list', '');
    const records = terminalListRecords(result);
    // A list requested before a local start/close must not undo that operation.
    if (revision !== terminalSessionRevision) return [...terminalSessions.values()];
    const known = new Set(records.map(record => record.sessionId));
    for (const session of [...terminalSessions.values()]) {
      if (known.has(session.id) || session.closing) continue;
      if (terminalContext?.sessionId === session.id) closeTerminalContextMenu();
      clearTimeout(session.inputTimer);
      clearTimeout(session.resizeTimer);
      session.term.dispose();
      session.surface.remove();
      terminalSessions.delete(session.id);
    }
    const ordered = [];
    for (const record of records) {
      if (!record?.sessionId || !record.host) continue;
      const hostLabel = catalog.find(host => host.id === record.host)?.label || record.host;
      let session = terminalSessions.get(record.sessionId);
      if (!session) {
        session = createTerminalView({
          id: record.sessionId, host: record.host, hostLabel,
          label: `${hostLabel} ${record.ordinal || 1}`,
          shell: record.shell || '终端', cwd: record.cwd || '~',
          transport: 'ssh', sequence: record.ordinal || 0
        });
      } else {
        session.hostLabel = hostLabel;
        session.label = `${hostLabel} ${record.ordinal || 1}`;
        session.exited = Boolean(record.exited);
      }
      ordered.push(session);
    }
    // The server can remove a session before its close acknowledgement arrives.
    // Keep that tab until the close path has confirmed the outcome.
    for (const session of terminalSessions.values()) {
      if (session.closing && !known.has(session.id)) ordered.push(session);
    }
    terminalSessions.clear();
    for (const session of ordered) terminalSessions.set(session.id, session);
    if (!terminalSessions.has(activeTerminalId)) {
      activeTerminalId = ordered.at(-1)?.id || '';
    }
    for (const session of terminalSessions.values()) {
      session.surface.hidden = session.id !== activeTerminalId;
    }
    renderTerminalTabs();
    if (terminalPanelOpen && activeTerminalId) {
      const active = terminalSessions.get(activeTerminalId);
      $('terminalHostHint').textContent =
        `${active.hostLabel} · ${active.shell} · ${active.cwd}`;
      fitActiveTerminal();
      scheduleTerminalPoll(20);
    }
    rememberTerminalSessions();
    return ordered;
  })().finally(() => { terminalSyncPromise = null; });
  return terminalSyncPromise;
}

function terminalListRecords(result) {
  if (result?.ok === false || !Array.isArray(result?.sessions) ||
      result.sessions.some(record => !record || typeof record.sessionId !== 'string' ||
        !record.sessionId || typeof record.host !== 'string' || !record.host) ||
      new Set(result.sessions.map(record => record.sessionId)).size !== result.sessions.length) {
    throw new Error('服务器未返回有效的 SSH 终端列表');
  }
  return result.sessions;
}

function setTerminalStatus(message, error = false) {
  $('terminalStatus').textContent = message;
  $('terminalStatus').className = error ? 'error' : '';
}

function closeTerminalContextMenu() {
  $('terminalContextMenu').hidden = true;
  $('terminalPasteBox').hidden = true;
  $('terminalCopyBox').hidden = true;
  $('terminalPasteText').value = '';
  $('terminalCopyText').value = '';
  terminalContext = null;
}

function terminalLineAt(session, clientY) {
  const screen = session.surface.querySelector('.xterm-screen');
  if (!screen) return '';
  const rect = screen.getBoundingClientRect();
  const row = Math.floor((clientY - rect.top) / (rect.height / session.term.rows));
  if (row < 0 || row >= session.term.rows) return '';
  const buffer = session.term.buffer.active;
  return buffer.getLine(buffer.viewportY + row)?.translateToString(true) || '';
}

function fitTerminalContextMenu(x, y) {
  const menu = $('terminalContextMenu');
  if (menu.hidden) return;
  const viewport = window.visualViewport;
  const left = viewport?.offsetLeft || 0;
  const top = viewport?.offsetTop || 0;
  const width = viewport?.width || window.innerWidth;
  const height = viewport?.height || window.innerHeight;
  menu.style.left = `${Math.max(left + 8,
    Math.min(x, left + width - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(top + 8,
    Math.min(y, top + height - menu.offsetHeight - 8))}px`;
}

function openTerminalContextMenu(session, clientX, clientY, selection) {
  if (!terminalPanelOpen || session.id !== activeTerminalId) return;
  closeTerminalContextMenu();
  terminalContext = {
    sessionId: session.id,
    selection: selection || session.term.getSelection(),
    line: terminalLineAt(session, clientY)
  };
  $('terminalCopySelection').disabled = !terminalContext.selection;
  $('terminalCopyLine').disabled = !terminalContext.line;
  $('terminalPaste').disabled = session.exited;
  $('terminalSelectAll').disabled = false;
  const menu = $('terminalContextMenu');
  menu.hidden = false;
  fitTerminalContextMenu(clientX, clientY);
}

async function copyTerminalContextText(value) {
  if (!terminalContext || !value) return;
  let copied = false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      copied = true;
    }
  } catch (_) {}
  if (!copied) {
    const input = document.createElement('textarea');
    input.value = value;
    input.style.cssText = 'position:fixed;left:0;top:0;opacity:0;pointer-events:none';
    document.body.append(input);
    input.focus();
    input.select();
    input.setSelectionRange(0, value.length);
    try { copied = document.execCommand('copy'); } catch (_) {}
    input.remove();
  }
  if (copied) {
    setTerminalStatus('终端内容已复制');
    closeTerminalContextMenu();
  } else {
    $('terminalCopyText').value = value;
    $('terminalCopyBox').hidden = false;
    $('terminalPasteBox').hidden = true;
    fitTerminalContextMenu(parseFloat($('terminalContextMenu').style.left),
      parseFloat($('terminalContextMenu').style.top));
    $('terminalCopyText').focus();
    $('terminalCopyText').select();
  }
}

function pasteTerminalText(value) {
  const session = terminalSessions.get(terminalContext?.sessionId);
  if (!session || session.exited || !value) return;
  session.term.paste(value);
  setTerminalStatus('已粘贴到 SSH 终端');
  closeTerminalContextMenu();
  session.term.focus();
}

async function pasteTerminalClipboard() {
  const sessionId = terminalContext?.sessionId;
  if (!sessionId) return;
  try {
    if (!navigator.clipboard?.readText) throw new Error('clipboard unavailable');
    const value = await navigator.clipboard.readText();
    if (terminalContext?.sessionId !== sessionId) return;
    if (value) {
      pasteTerminalText(value);
      return;
    }
  } catch (_) {}
  if (terminalContext?.sessionId !== sessionId) return;
  $('terminalCopyBox').hidden = true;
  $('terminalPasteBox').hidden = false;
  const menu = $('terminalContextMenu');
  fitTerminalContextMenu(parseFloat(menu.style.left), parseFloat(menu.style.top));
  $('terminalPasteText').focus();
}

function installTerminalContextMenu(session) {
  const surface = session.surface;
  let press = null;
  const cancelPress = () => {
    if (press) clearTimeout(press.timer);
    press = null;
  };
  surface.addEventListener('mousedown', event => {
    if (event.button === 2) session.rightClickSelection = session.term.getSelection();
  }, true);
  surface.addEventListener('contextmenu', event => {
    event.preventDefault();
    event.stopPropagation();
    if (session.longPressShownAt && Date.now() - session.longPressShownAt < 1000) return;
    const selection = session.rightClickSelection || session.term.getSelection();
    session.rightClickSelection = '';
    openTerminalContextMenu(session, event.clientX, event.clientY, selection);
    cancelPress();
  }, true);
  surface.addEventListener('touchstart', event => {
    cancelPress();
    if (event.touches.length !== 1) return;
    const touch = event.touches[0];
    const x = touch.clientX;
    const y = touch.clientY;
    press = {x, y, timer: setTimeout(() => {
      session.longPressShownAt = Date.now();
      openTerminalContextMenu(session, x, y, session.term.getSelection());
      press = null;
    }, 550)};
  }, {capture: true, passive: true});
  surface.addEventListener('touchmove', event => {
    if (!press || event.touches.length !== 1 ||
        Math.hypot(event.touches[0].clientX - press.x,
          event.touches[0].clientY - press.y) > 12) cancelPress();
  }, {capture: true, passive: true});
  surface.addEventListener('touchend', cancelPress, {capture: true, passive: true});
  surface.addEventListener('touchcancel', cancelPress, {capture: true, passive: true});
}

function fitActiveTerminal() {
  if (!terminalPanelOpen || terminalView !== 'terminal' || !activeTerminalId) return;
  const session = terminalSessions.get(activeTerminalId);
  if (!session?.fit || session.surface.hidden) return;
  requestAnimationFrame(() => {
    if (!terminalPanelOpen || terminalView !== 'terminal' ||
        activeTerminalId !== session.id || session.surface.hidden) return;
    try {
      session.fit.fit();
    } catch (_) {}
  });
}

function scheduleTerminalResize(session, cols, rows) {
  if (session.exited || session.closing || !session.id ||
      terminalSessions.get(session.id) !== session) return;
  clearTimeout(session.resizeTimer);
  session.resizeTimer = setTimeout(async () => {
    try {
      await terminalApi('resize', session.host,
        {sessionId: session.id, cols, rows});
    } catch (error) {
      setTerminalStatus(error.message, true);
    }
  }, 180);
}

function nextTerminalInputChunk(session, limit = 48 * 1024) {
  if (!session.inputQueue.length) return null;
  const output = new Uint8Array(Math.min(limit,
    session.inputQueue.reduce((total, chunk) => total + chunk.length, 0)));
  let written = 0;
  while (session.inputQueue.length && written < output.length) {
    const chunk = session.inputQueue[0];
    const take = Math.min(chunk.length, output.length - written);
    output.set(chunk.subarray(0, take), written);
    written += take;
    if (take === chunk.length) session.inputQueue.shift();
    else session.inputQueue[0] = chunk.subarray(take);
  }
  return output.subarray(0, written);
}

async function flushTerminalInput(session) {
  clearTimeout(session.inputTimer);
  session.inputTimer = null;
  if (session.writeActive || session.exited || session.closing ||
      terminalSessions.get(session.id) !== session) return;
  const chunk = nextTerminalInputChunk(session);
  if (!chunk?.length) return;
  session.writeActive = true;
  try {
    await terminalApi('write', session.host,
      {sessionId: session.id, data: bytesToBase64(chunk.buffer)});
  } catch (error) {
    setTerminalStatus(error.message, true);
    session.term.writeln(`\r\n\x1b[31m[输入失败：${error.message}]\x1b[0m`);
  } finally {
    session.writeActive = false;
    if (session.inputQueue.length && !session.closing && !session.exited &&
        terminalSessions.get(session.id) === session) session.inputTimer = setTimeout(() =>
      flushTerminalInput(session), 20);
  }
}

function queueTerminalInput(session, data) {
  if (session.closing || session.exited || terminalSessions.get(session.id) !== session) return;
  const bytes = new TextEncoder().encode(data);
  for (let offset = 0; offset < bytes.length; offset += 48 * 1024) {
    session.inputQueue.push(bytes.slice(offset, offset + 48 * 1024));
  }
  if (!session.writeActive && !session.inputTimer) {
    session.inputTimer = setTimeout(() => flushTerminalInput(session),
      data.includes('\r') || data.includes('\n') ? 0 : 45);
  }
}

function createTerminalView(record) {
  if (!window.Terminal || !window.FitAddon?.FitAddon) {
    throw new Error('终端组件未加载');
  }
  const surface = document.createElement('div');
  surface.className = 'terminal-surface';
  surface.dataset.sessionId = record.id;
  surface.hidden = true;
  $('terminalStack').append(surface);
  const term = new window.Terminal({
    cursorBlink: true,
    cursorStyle: 'block',
    convertEol: false,
    disableStdin: false,
    fontFamily: 'SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
    fontSize: layoutState.fontSize,
    lineHeight: 1.18,
    scrollback: 10000,
    theme: terminalTheme()
  });
  const fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(surface);
  const session = {
    ...record, surface, term, fit, offset: 0, exited: false, error: '',
    inputQueue: [], writeActive: false, inputTimer: null, resizeTimer: null,
    readActive: false, closing: false, closeError: ''
  };
  term.onData(data => queueTerminalInput(session, data));
  term.onResize(size => scheduleTerminalResize(session, size.cols, size.rows));
  installTerminalContextMenu(session);
  terminalSessions.set(session.id, session);
  terminalSessionRevision += 1;
  return session;
}

function renderTerminalTabs() {
  const tabs = $('terminalTabs');
  tabs.replaceChildren();
  for (const session of terminalSessions.values()) {
    const tab = document.createElement('div');
    tab.className = 'terminal-tab' + (session.id === activeTerminalId ? ' active' : '') +
      (session.exited ? ' exited' : '');
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', String(session.id === activeTerminalId));
    const state = document.createElement('span');
    state.className = 'terminal-tab-state';
    state.textContent = '●';
    const select = document.createElement('button');
    select.type = 'button';
    select.className = 'terminal-tab-label';
    select.textContent = session.label;
    select.title = `${session.hostLabel} · ${session.shell} · ${session.cwd}`;
    select.onclick = () => activateTerminal(session.id, true);
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'terminal-tab-close';
    close.textContent = session.closing ? '…' : '×';
    close.disabled = session.closing;
    close.title = session.closing ? `正在断开 ${session.label}…` :
      session.closeError ? `${session.closeError}；点击重试关闭 ${session.label}` : `关闭 ${session.label}`;
    tab.setAttribute('aria-busy', String(session.closing));
    close.setAttribute('aria-label', close.title);
    close.onclick = () => closeTerminal(session.id);
    tab.append(state, select, close);
    tabs.append(tab);
  }
  $('terminalEmpty').hidden = Boolean(activeTerminalId && terminalSessions.has(activeTerminalId));
}

function showTerminalPanel() {
  terminalPanelOpen = true;
  $('extensionsToggle').setAttribute('aria-expanded', 'true');
  $('terminalPanel').hidden = false;
  $('terminalResizer').hidden = mobileQuery.matches;
  document.body.classList.add('terminal-open');
  rememberTerminalSessions();
  syncTerminalPanelPolling();
  renderTerminalTabs();
  applyTerminalView();
  if (!activeTerminalId && terminalView === 'terminal') {
    const host = catalog.find(item => item.id === $('terminalHostSelect').value);
    $('terminalHostHint').textContent = `${host?.label || '目标电脑'} · 尚未启动 SSH 终端`;
  }
  fitActiveTerminal();
  scheduleTerminalPoll(20);
}

function syncTerminalPanelPolling() {
  if (!terminalPanelOpen || terminalView !== 'terminal' || document.hidden) {
    clearInterval(terminalListTimer);
    terminalListTimer = null;
    clearTimeout(scheduleTerminalPoll.timer);
    return;
  }
  if (!terminalListTimer) terminalListTimer = setInterval(() => {
    void syncServerTerminals().catch(error => setTerminalStatus(error.message, true));
  }, 5000);
}

function hideTerminalPanel() {
  closeTerminalContextMenu();
  closeFileContextMenu();
  terminalPanelOpen = false;
  $('extensionsToggle').setAttribute('aria-expanded', 'false');
  clearInterval(terminalListTimer);
  terminalListTimer = null;
  $('terminalPanel').hidden = true;
  $('terminalResizer').hidden = true;
  document.body.classList.remove('terminal-open');
  rememberTerminalSessions();
}

function activateTerminal(sessionId, focusTerminal = false) {
  const session = terminalSessions.get(sessionId);
  if (!session) return;
  closeTerminalContextMenu();
  terminalView = 'terminal';
  activeTerminalId = sessionId;
  for (const value of terminalSessions.values()) value.surface.hidden = value.id !== sessionId;
  $('terminalEmpty').hidden = true;
  $('terminalHostHint').textContent = `${session.hostLabel} · ${session.shell} · ${session.cwd}`;
  showTerminalPanel();
  renderTerminalTabs();
  setTerminalStatus(session.closing ? `正在断开 ${session.label}…` : session.closeError ||
    (session.exited ? 'SSH 已结束，可查看已保留的输出' :
      `SSH 已连接 ${session.hostLabel} · ${session.shell}`), Boolean(session.closeError));
  fitActiveTerminal();
  if (focusTerminal) session.term.focus();
}

async function startTerminal() {
  const hostId = $('terminalHostSelect').value || selectedHostId;
  const host = catalog.find(item => item.id === hostId);
  if (!host) {
    setTerminalStatus('没有找到目标电脑', true);
    return;
  }
  if (!host.terminal?.supported) {
    setTerminalStatus('该电脑尚未配置 SSH 连接', true);
    return;
  }
  terminalView = 'terminal';
  showTerminalPanel();
  $('newTerminal').disabled = true;
  $('terminalEmptyCreate').disabled = true;
  setTerminalStatus(`服务器 正在通过 SSH 连接 ${host.label}…`);
  const meta = selected?.host === host.id ? selectedThread() : null;
  const cwd = (selected?.host === host.id ? current?.cwd : '') || meta?.cwd ||
    rememberedThread(host)?.cwd || '';
  try {
    const result = await terminalApi('start', host.id, {cwd, cols: 100, rows: 30});
    terminalSequence += 1;
    const hostCount = [...terminalSessions.values()].filter(value => value.host === host.id).length + 1;
    const session = createTerminalView({
      id: result.sessionId,
      host: host.id,
      hostLabel: host.label,
      label: `${host.label} ${hostCount}`,
      shell: result.shell || host.terminal.shell || '终端',
      cwd: result.cwd || cwd || '~',
      transport: 'ssh',
      sequence: terminalSequence
    });
    activateTerminal(session.id);
    rememberTerminalSessions();
  } catch (error) {
    setTerminalStatus(error.message, true);
  } finally {
    syncTerminalControls();
  }
}

async function closeTerminal(sessionId) {
  const session = terminalSessions.get(sessionId);
  if (!session || session.closing) return;
  if (terminalContext?.sessionId === sessionId) closeTerminalContextMenu();
  session.closing = true;
  session.closeError = '';
  session.term.options.disableStdin = true;
  terminalSessionRevision += 1;
  clearTimeout(session.inputTimer);
  clearTimeout(session.resizeTimer);
  session.inputTimer = null;
  session.resizeTimer = null;
  renderTerminalTabs();
  setTerminalStatus(`正在断开 ${session.label}…`);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  let closed = false;
  let failure;
  try {
    const result = await terminalApi('close', session.host, {sessionId: session.id},
      {signal: controller.signal});
    closed = result?.ok === true && result.closed === true && result.sessionId === session.id;
    if (!closed) throw new Error('服务器未确认此 SSH 会话已关闭');
  } catch (error) {
    failure = controller.signal.aborted ? new Error('断开请求超时') : error;
  } finally {
    clearTimeout(timeout);
  }
  if (!closed) {
    // A lost reply is not proof of failure or success. Reconcile once, read-only;
    // never resend the close automatically or trust an error message as proof.
    const check = new AbortController();
    const checkTimeout = setTimeout(() => check.abort(), 5000);
    try {
      const result = await terminalApi('list', '', {}, {signal: check.signal});
      closed = !terminalListRecords(result).some(record => record.sessionId === session.id);
    } catch (_) {
      // Keep the tab and its queued input when the server cannot be checked.
    } finally {
      clearTimeout(checkTimeout);
    }
  }
  session.closing = false;
  terminalSessionRevision += 1;
  if (terminalSessions.get(session.id) !== session) return;
  if (!closed) {
    session.closeError = `${session.label}：断开未确认，终端已保留；${failure?.message || '请重试'}`;
    session.term.options.disableStdin = session.exited;
    if (session.inputQueue.length) void flushTerminalInput(session);
    renderTerminalTabs();
    setTerminalStatus(session.closeError, true);
    return;
  }
  session.term.dispose();
  session.surface.remove();
  terminalSessions.delete(session.id);
  if (activeTerminalId === session.id) {
    const replacement = [...terminalSessions.values()].at(-1);
    activeTerminalId = replacement?.id || '';
    if (replacement) activateTerminal(replacement.id);
  }
  renderTerminalTabs();
  rememberTerminalSessions();
  if (!terminalSessions.size) {
    setTerminalStatus('所有电脑的 SSH 终端会显示在同一组标签中');
    const host = catalog.find(item => item.id === $('terminalHostSelect').value);
    $('terminalHostHint').textContent = `${host?.label || '目标电脑'} · 尚未启动 SSH 终端`;
  }
}

function scheduleTerminalPoll(delay = 650) {
  clearTimeout(scheduleTerminalPoll.timer);
  if (!terminalPanelOpen || terminalView !== 'terminal' || !activeTerminalId ||
      document.hidden) return;
  scheduleTerminalPoll.timer = setTimeout(pollActiveTerminal, delay);
}

async function pollActiveTerminal() {
  if (terminalPollActive || !terminalPanelOpen || terminalView !== 'terminal' ||
      !activeTerminalId) return;
  const session = terminalSessions.get(activeTerminalId);
  if (!session || session.readActive) return;
  terminalPollActive = true;
  session.readActive = true;
  let nextDelay = 0;
  try {
    const result = await terminalApi('read', session.host,
      {sessionId: session.id, offset: session.offset, waitMs: 1200});
    if (terminalSessions.get(session.id) !== session) return;
    if (result.truncated) {
      session.term.writeln('\r\n\x1b[33m[较早终端输出已从缓冲区清理]\x1b[0m');
    }
    const data = decodeTerminalData(result.data);
    if (data.length) {
      session.term.write(data);
      nextDelay = 0;
    }
    session.offset = result.offset;
    session.exited = Boolean(result.exited);
    if (session.exited) {
      if (activeTerminalId === session.id && !session.closing && !session.closeError) {
        setTerminalStatus(`SSH 已结束${result.exitCode === null ? '' : ` · 退出码 ${result.exitCode}`}`);
      }
    }
    renderTerminalTabs();
  } catch (error) {
    if (terminalSessions.get(session.id) !== session || session.closing) return;
    if (/SSH 终端已关闭|服务器 服务已重启/.test(error.message)) {
      terminalSessionRevision += 1;
      if (terminalContext?.sessionId === session.id) closeTerminalContextMenu();
      clearTimeout(session.inputTimer);
      clearTimeout(session.resizeTimer);
      session.term.dispose();
      session.surface.remove();
      terminalSessions.delete(session.id);
      if (activeTerminalId === session.id) {
        const replacement = [...terminalSessions.values()].at(-1);
        activeTerminalId = replacement?.id || '';
        if (replacement) activateTerminal(replacement.id);
      }
      renderTerminalTabs();
      rememberTerminalSessions();
      setTerminalStatus('旧 SSH 会话已失效，请新建终端');
      return;
    }
    session.error = error.message;
    setTerminalStatus(error.message, true);
    nextDelay = 1800;
  } finally {
    session.readActive = false;
    terminalPollActive = false;
    scheduleTerminalPoll(nextDelay);
  }
}

function syncTerminalControls() {
  const selector = $('terminalHostSelect');
  const previous = selector.value;
  const available = catalog.filter(host => physicalHostId(host) === host.id &&
    (host.terminal?.supported || (applicationId(host) === 'codex' && host.threads?.some(thread => typeof thread.isSubagent === 'boolean'))));
  const preferred = previous && available.some(host => host.id === previous) ? previous :
    (available.some(host => host.id === physicalHostId()) ? physicalHostId() : available[0]?.id || '');
  setOptions(selector, available.map(host => ({
    value: host.id, label: `${host.label} · ${host.terminal?.supported ? 'SSH' : uiText('子智能体')}`
  })), preferred);
  const host = available.find(item => item.id === selector.value);
  const enabled = Boolean(host?.terminal?.supported && !busy);
  const agentsAvailable = applicationId(currentHost()) !== 'deepseek' && applicationId(host) === 'codex' &&
    host?.threads?.some(thread => typeof thread.isSubagent === 'boolean');
  $('extensionsToggle').disabled = !available.length;
  $('terminalModeAgents').hidden = !agentsAvailable;
  $('terminalModeTerminal').disabled = !host?.terminal?.supported;
  $('terminalModeFiles').disabled = !host?.terminal?.supported;
  const previousView = terminalView;
  if (agentsAvailable && !host?.terminal?.supported) terminalView = 'agents';
  else if (!agentsAvailable && terminalView === 'agents') terminalView = host?.terminal?.supported ? 'files' : 'terminal';
  if (terminalView !== previousView) {
    applyTerminalView();
    syncTerminalPanelPolling();
    if (terminalPanelOpen && terminalView === 'files') syncFileDirectoryContext();
  }
  $('newTerminal').disabled = !enabled;
  $('terminalEmptyCreate').disabled = !enabled;
  $('newTerminal').title = enabled ? `通过 SSH 连接 ${host.label}` : '没有可用的 SSH 目标';
}

let terminalsRestored = false;
async function restoreTerminalSessions() {
  if (terminalsRestored) return;
  terminalsRestored = true;
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('codexRelayTerminalSessions') || '{}'); }
  catch (_) { localStorage.removeItem('codexRelayTerminalSessions'); }
  try { await syncServerTerminals(); }
  catch (error) { setTerminalStatus(`无法同步已开启的终端：${error.message}`, true); }
  activeTerminalId = terminalSessions.has(saved.activeId) ? saved.activeId : activeTerminalId;
  terminalPanelOpen = Boolean(saved.open && terminalSessions.size);
  if (activeTerminalId) {
    for (const session of terminalSessions.values()) {
      session.surface.hidden = session.id !== activeTerminalId;
    }
  }
  if (terminalPanelOpen && activeTerminalId) activateTerminal(activeTerminalId);
  else hideTerminalPanel();
  renderTerminalTabs();
}

function removeDisconnectedHosts(event) {
  let value;
  try { value = JSON.parse(event.data); } catch (_) { return; }
  if (!Array.isArray(value.hosts)) return;
  const removed = new Set(value.hosts.filter(id => typeof id === 'string' && id.length <= 200));
  if (!removed.size) return;
  const epoch = ++hostRemovalEpoch;
  for (const id of removed) hostRemovals.set(id, epoch);
  while (hostRemovals.size > 256) hostRemovals.delete(hostRemovals.keys().next().value);
  catalog = catalog.filter(host => !removed.has(host.id));
  if (selected && removed.has(selected.host)) clearThread();
  if (removed.has(selectedHostId)) selectedHostId = applicationHosts()[0]?.id || '';
  syncSidebarSelectors();
  renderCatalog();
  scheduleCatalogRefresh();
}

async function refresh(seed = null) {
  if (panelRestarting) return;
  if (refreshing) {
    refreshWhileBusy = true;
    return;
  }
  refreshing = true;
  const removalEpoch = hostRemovalEpoch;
  try {
    let data = seed || await api(liteCatalogPath());
    if (Array.isArray(data.hostPatches) &&
        data.baseVersion !== liteCatalogVersion) {
      data = await api('/api/lite/catalog');
    }
    if (Array.isArray(data.hostPatches)) {
      data.hosts = applyLiteCatalogPatch(catalog, data.hostPatches);
    }
    if (data.version) liteCatalogVersion = data.version;
    if (data.generation && data.generation !== relayGeneration) {
      clearThreadSnapshots();
      relayGeneration = data.generation;
    }
    await bindOutgoingScope(data.browserScope);
    if (data.uiTitle) applyWorkspaceTitle(data.uiTitle);
    applyAppVersion(data.appVersion);
    applyFeedbackMetadata(data);
    const workspaceChanged = Array.isArray(data.workspaceThreads);
    if (workspaceChanged) {
      workspaceThreads = data.workspaceThreads;
      lastCatalog = '';
    }
    if (Array.isArray(data.hosts)) {
      data.hosts = data.hosts.filter(host => (hostRemovals.get(host.id) || 0) <= removalEpoch);
      trackThreadCompletions(data.hosts);
      catalog = data.hosts;
      if (!catalog.some(host => host.id === selectedHostId)) {
        selectedHostId = applicationHosts()[0]?.id || '';
      }
      const restoringInitialSelection = !initialSelectionDone && !selected;
      let initialThread = null;
      let initialHost = null;
      if (restoringInitialSelection) {
        if (workspaceMode) {
          applyWorkspaceNavigation();
          const target = rememberedWorkspaceTarget();
          initialHost = target?.host;
          initialThread = target?.thread;
        } else {
          initialHost = currentHost();
          if (initialHost) {
            applyHostNavigation(initialHost);
            initialThread = rememberedThread(initialHost);
          }
        }
      }
      syncSidebarSelectors();
      renderCatalog();
      const selectedMeta = selectedThread();
      if (selectedMeta && current?.id === selectedMeta.id &&
          selected?.host === currentHost()?.id) {
        current.unread = selectedMeta.unread ?? current.unread;
        renderNativeThreadStatus(selectedMeta, current, currentHost(), current.online);
      }
      
      if (restoringInitialSelection) {
        initialSelectionDone = true;
        if (initialHost && initialThread) await selectThread(initialHost, initialThread, false);
        else captureHostNavigation();
      } else {
        const host = currentHost();
        const selectedStillExists = host && selected?.host === host.id &&
          host.threads.some(thread => thread.id === selected.thread);
        if (selected && !selectedStillExists) {
          clearThread();
          if (workspaceMode) {
            const target = rememberedWorkspaceTarget();
            if (target) await selectThread(target.host, target.thread, false);
            else renderCatalog();
          } else if (host) {
            applyHostNavigation(host);
            const fallback = rememberedThread(host);
            if (fallback) {
              await selectThread(host, fallback, false);
            } else {
              renderCatalog();
              captureHostNavigation(host.id);
            }
          }
        } else {
          await refreshThread();
        }
      }
    } else {
      if (workspaceChanged) renderCatalog();
      await refreshThread();
      
    }
    syncFileDirectoryContext();
    setRelayConnectionStatus('● 中继已连接');
  } catch (error) {
    setRelayConnectionStatus('连接中断，正在重试');
  } finally {
    refreshing = false;
    if (refreshWhileBusy) {
      refreshWhileBusy = false;
      scheduleCatalogRefresh();
    }
  }
}

function scheduleCatalogRefresh() {
  if (panelRestarting) return;
  if (document.hidden) {
    refreshPending = true;
    return;
  }
  if (scheduledRefresh) {
    refreshPending = true;
    return;
  }
  const delay = mobileQuery.matches || touchKeyboardQuery.matches ? 1800 : 900;
  scheduledRefresh = setTimeout(async () => {
    scheduledRefresh = null;
    refreshPending = false;
    await refresh();
    if (refreshPending) scheduleCatalogRefresh();
  }, delay);
}

document.addEventListener('visibilitychange', () => {
  syncCapacityRetryStatus();
  if (document.hidden) {
    clearThreadReadAttention();
    clearTimeout(preloadTimer);
    preloadTimer = null;
    preloadController?.abort();
  } else {
    scheduleThreadPreload();
  }
  if (!document.hidden && (refreshPending || selected)) scheduleCatalogRefresh();
  if (!document.hidden) ;
  syncTerminalPanelPolling();
  if (!document.hidden) scheduleTerminalPoll(50);
});
window.addEventListener('blur', clearThreadReadAttention);
document.addEventListener('pointerdown', event => {
  if (!event.isTrusted || event.isPrimary === false || event.button !== 0 || !selected) return;
  const node = event.target instanceof Element ? event.target : null;
  if (!node?.closest('.async-choice-card, .request-card')) return;
  finishQuestionPointerInteraction();
  questionPointerInteraction = {id: event.pointerId, key: historyKey(), node};
  // A lost release must not leave even the question surface stale forever.
  questionPointerReleaseTimer = setTimeout(finishQuestionPointerInteraction, 1500);
}, {capture: true, passive: true});
document.addEventListener('pointerup', event => {
  if (questionPointerInteraction?.id !== event.pointerId) return;
  // Click (including a label's synthesized input click) follows pointerup.
  // Do not mutate its target from a pointerup listener. Cancelled gestures get
  // a bounded fallback; normal mouse/touch clicks flush in the next task.
  clearTimeout(questionPointerReleaseTimer);
  questionPointerReleaseTimer = setTimeout(finishQuestionPointerInteraction, 1000);
}, {capture: true, passive: true});
document.addEventListener('click', () => {
  if (!questionPointerInteraction) return;
  clearTimeout(questionPointerReleaseTimer);
  questionPointerReleaseTimer = setTimeout(finishQuestionPointerInteraction, 0);
}, {capture: true, passive: true});
document.addEventListener('pointercancel', () => {
  if (questionPointerInteraction) finishQuestionPointerInteraction();
}, {capture: true, passive: true});
window.addEventListener('blur', finishQuestionPointerInteraction);
document.addEventListener('pointerdown', event => {
  if (!event.isTrusted) return;
  if (selected && ($('timeline').contains(event.target) || $('messageForm').contains(event.target) ||
      $('jumpBottom').contains(event.target))) {
    threadReadAttention = threadKey(selected.host, selected.thread);
    scheduleVisibleThreadRead();
  } else clearThreadReadAttention();
}, {capture: true, passive: true});
for (const type of ['wheel', 'keydown']) {
  document.addEventListener(type, event => {
    if (!event.isTrusted || !selected) return;
    if ($('timeline').contains(event.target) || $('messageForm').contains(event.target)) {
      threadReadAttention = threadKey(selected.host, selected.thread);
      scheduleVisibleThreadRead();
    } else clearThreadReadAttention();
  }, {capture: true, passive: true});
}
document.addEventListener('focusin', event => {
  if (!$('timeline').contains(event.target) && !$('messageForm').contains(event.target) &&
      !$('jumpBottom').contains(event.target)) clearThreadReadAttention();
});

async function start() {
  const initial = await api(liteCatalogPath());
  if (initial.version) liteCatalogVersion = initial.version;
  $('login').hidden = true;
  $('workspace').hidden = false;
  source?.close();
  source = new EventSource('/api/lite/events');
  source.addEventListener('hosts-removed', removeDisconnectedHosts);
  source.onmessage = scheduleCatalogRefresh;
  source.onerror = () => {
    setRelayConnectionStatus('连接中断，正在重试');
    if (!document.hidden && Date.now() - lastStreamAuthProbe >= 30000) {
      lastStreamAuthProbe = Date.now();
      void api(liteCatalogPath()).catch(() => {});
    }
  };
  await refresh(initial);
  await restoreTerminalSessions();
  syncTerminalControls();
  ;
}

async function openPanelRestart() {
  if (panelRestarting) return;
  const epoch = ++panelRestartDialogEpoch;
  const dialog = $('panelRestartDialog');
  panelRestartOriginal = null;
  $('panelRestartCancel').textContent = '取消';
  $('panelRestartStatus').textContent = '正在检查面板服务…';
  $('panelRestartConfirm').disabled = true;
  dialog.showModal();
  try {
    const status = await api('/api/panel/status', null, {signal: AbortSignal.timeout(15000)});
    if (!dialog.open || epoch !== panelRestartDialogEpoch) return;
    panelRestartOriginal = status;
    $('panelRestartConfirm').disabled = !status.enabled;
    $('panelRestartStatus').textContent = status.enabled ?
      `当前有 ${status.activeTerminals || 0} 个网页 SSH 终端；重启后需重新创建终端。` : '当前服务未启用重启入口';
  } catch (error) {
    if (dialog.open && epoch === panelRestartDialogEpoch) $('panelRestartStatus').textContent = error.message;
  }
}

async function submitPanelRestart(event) {
  event.preventDefault();
  if (panelRestarting || !panelRestartOriginal?.enabled) return;
  const mode = new FormData($('panelRestartForm')).get('panelRestartMode');
  const original = panelRestartOriginal, target = selected && {...selected};
  const id = uuid();
  if (target) {
    drafts.set(threadKey(target.host, target.thread), $('message').value);
    attachmentDrafts.set(threadKey(target.host, target.thread), [...pendingFiles]);
    captureHostNavigation();
  }
  panelRestarting = true;
  clearThreadReadAttention();
  source?.close(); source = null;
  clearTimeout(scheduledRefresh); scheduledRefresh = null;
  clearTimeout(preloadTimer); preloadTimer = null;
  preloadController?.abort();
  $('panelRestartForm').querySelectorAll('button,input').forEach(node => node.disabled = true);
  $('panelRestartStatus').textContent = '正在完整关闭并重新启动面板，请勿重复点击…';
  let completed = null, submissionError = null;
  try {
    try {
      await api('/api/panel/restart', {id, mode}, {signal: AbortSignal.timeout(15000)});
    } catch (error) {
      submissionError = error;
      // A lost acknowledgement must not submit a second restart.
      $('panelRestartStatus').textContent = '正在核对重启结果；不会自动重复提交…';
    }
    const deadline = Date.now() + 240000;
    let serviceReadyAt = 0;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      try {
        const status = await api('/api/panel/status', null, {signal: AbortSignal.timeout(4000)});
        const operation = status.restart;
        if (operation?.requestId !== id) {
          if (submissionError) {submissionError.restartTerminal = true; throw submissionError;}
          continue;
        }
        if (operation.phase === 'failed') {
          const error = new Error(operation.error || '重启失败，请使用 服务器 桌面脚本检查');
          error.restartTerminal = true; throw error;
        }
        if (['completed', 'partial'].includes(operation.phase) &&
            status.generation !== original.generation && status.processId !== original.processId) {
          serviceReadyAt ||= Date.now();
          if (!target || status.hostIds?.includes(target.host) || Date.now() - serviceReadyAt >= 20000) {
            completed = operation; break;
          }
          $('panelRestartStatus').textContent = '新面板服务已启动，正在等待目标电脑重新接入…';
          continue;
        }
        $('panelRestartStatus').textContent = operation.phase === 'agents' ?
          '服务已关闭，正在逐台重启面板代理；Codex App 未停止…' : '正在等待新的面板服务启动…';
      } catch (error) {
        if (error.restartTerminal) throw error;
        // Connection refusal/502 during a real stop is expected. Read only.
      }
    }
    if (!completed) throw new Error('重启确认超时，请查看 服务器 桌面脚本或服务日志；不会自动再次重启');
    panelRestarting = false;
    clearThreadSnapshots(); liteCatalogVersion = '';
    terminalsRestored = false;
    await start();
    if (target) {
      const host = catalog.find(row => row.id === target.host);
      const thread = host?.threads.find(row => row.id === target.thread);
      if (thread) await selectThread(host, thread, false, false);
    }
    const failed = (completed.agents || []).filter(agent => agent.status !== 'restarted').map(agent => agent.host);
    $('panelRestartStatus').textContent = failed.length ?
      `面板服务已重启；以下代理未确认重启：${failed.join('、')}。其他组件已继续启动。` :
      '✓ 面板已完整重启并重新接入；Codex App 与原任务未停止。';
  } catch (error) {
    panelRestarting = false;
    $('panelRestartStatus').textContent = error.message;
    try { await start(); } catch (_) { setRelayConnectionStatus('等待面板恢复，可使用 服务器 桌面重启脚本'); }
  } finally {
    panelRestarting = false;
    $('panelRestartForm').querySelectorAll('button,input').forEach(node => node.disabled = false);
    $('panelRestartConfirm').disabled = true;
    $('panelRestartCancel').textContent = '关闭';
  }
}

$('panelRestartToggle').onclick = () => void openPanelRestart();
$('panelRestartForm').onsubmit = submitPanelRestart;
$('panelRestartCancel').onclick = () => {if (!panelRestarting) $('panelRestartDialog').close();};
$('panelRestartDialog').addEventListener('cancel', event => {if (panelRestarting) event.preventDefault();});

;

$('loginForm').onsubmit = async event => {
  event.preventDefault();
  if (loginPending) return;
  loginPending = true;
  const button = $('loginForm').querySelector('[type="submit"]');
  button.disabled = true;
  $('loginError').textContent = '';
  try {
    await api('/api/login', {password: $('password').value});
    $('password').value = '';
    $('loginError').textContent = '';
    await start();
  } catch (error) {
    $('loginError').textContent = error.message;
  } finally {
    loginPending = false;
    button.disabled = false;
  }
};

$('logout').onclick = async () => {
  questionDraftSessions.clear();
  clearQuestionDock();
  await clearOutgoingJournal();
  try {
    const edgeLogout = await fetch('/logout', {credentials: 'same-origin'});
    if (edgeLogout.redirected && new URL(edgeLogout.url).pathname === '/login') {
      window.location.assign('/login');
      return;
    }
  } catch (_) {
    // The private relay has no form-login gateway, so use its API logout below.
  }
  await api('/api/logout', {});
  showLogin();
};

function openBrandDialog() {
  $('brandName').value = workspaceTitle;
  $('brandError').textContent = '';
  $('brandDialog').showModal();
  $('brandName').focus();
  $('brandName').select();
}

$('brandEditTrigger').ondblclick = openBrandDialog;
$('brandEditTrigger').onclick = event => {
  if (event.detail === 0 || touchKeyboardQuery.matches) openBrandDialog();
};
$('brandEditTrigger').onkeydown = event => {
  if (event.key === 'F2') {event.preventDefault(); openBrandDialog();}
};
$('brandCancel').onclick = () => $('brandDialog').close();
$('brandForm').onsubmit = async event => {
  event.preventDefault();
  const title = $('brandName').value.trim();
  if (!title) return;
  $('brandSave').disabled = true;
  $('brandError').textContent = '';
  try {
    const result = await api('/api/ui/title', {title});
    applyWorkspaceTitle(result.title);
    $('brandDialog').close();
  } catch (error) {
    $('brandError').textContent = error.message;
  } finally {
    $('brandSave').disabled = false;
  }
};

$('themeToggle').onclick = () => applyTheme(theme === 'light' ? 'dark' : 'light', true);
// Both sidebar pickers share dismissal, keyboard navigation and focus behavior.
const sidebarMenus = [];
function setupSidebarMenu(toggleId, menuId, optionSelector, select) {
  const toggle = $(toggleId), menu = $(menuId);
  const close = (restoreFocus = false) => {
    menu.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    if (restoreFocus) toggle.focus();
  };
  const open = () => {
    for (const other of sidebarMenus) other.close();
    menu.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    (menu.querySelector('[aria-checked="true"]') || menu.querySelector(optionSelector))?.focus();
  };
  toggle.onclick = () => menu.hidden ? open() : close(true);
  toggle.onkeydown = event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); open();
    }
  };
  menu.onclick = event => {
    const option = event.target.closest(optionSelector);
    if (option && select(option) !== false) close(true);
  };
  menu.onkeydown = event => {
    const options = [...menu.querySelectorAll(optionSelector)];
    const index = options.indexOf(document.activeElement);
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 :
        (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      options[next].focus();
    } else if (event.key === 'Tab') close(true);
  };
  sidebarMenus.push({toggle, menu, close});
}
setupSidebarMenu('paletteToggle', 'paletteMenu', '[data-palette-option]', option => {
  applyPalette(option.dataset.paletteOption, true);
});
setupSidebarMenu('languageToggle', 'languageMenu', '[data-language-option]', option =>
  window.CRBI18n?.setLanguage(option.dataset.languageOption) ?? false);
document.addEventListener('pointerdown', event => {
  for (const {toggle, menu, close} of sidebarMenus) {
    if (!menu.hidden && !toggle.parentElement.contains(event.target)) close();
  }
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    for (const {menu, close} of sidebarMenus) if (!menu.hidden) {
      event.preventDefault(); close(true);
    }
  }
});
function positionFontMenu() {
  const menu = $('fontMenu'), toggle = $('fontToggle');
  if (!menu || menu.hidden || !toggle) return;
  const anchor = toggle.getBoundingClientRect();
  const width = menu.offsetWidth, height = menu.offsetHeight;
  const viewport = window.visualViewport;
  const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
  const right = left + (viewport?.width || innerWidth);
  const bottom = top + (viewport?.height || innerHeight);
  menu.style.left = `${Math.max(left + 10, Math.min(anchor.right - width, right - width - 10))}px`;
  menu.style.top = `${Math.max(top + 10, Math.min(anchor.bottom + 8, bottom - height - 10))}px`;
}
function closeFontMenu(restoreFocus = false) {
  $('fontMenu').hidden = true;
  $('fontToggle').setAttribute('aria-expanded', 'false');
  if (restoreFocus) $('fontToggle').focus();
}
function openFontMenu() {
  for (const {close} of sidebarMenus) close();
  $('fontMenu').hidden = false;
  $('fontToggle').setAttribute('aria-expanded', 'true');
  positionFontMenu();
  $('fontSize').focus({preventScroll: true});
}
$('fontToggle').onclick = () => $('fontMenu').hidden ? openFontMenu() : closeFontMenu(true);
$('fontToggle').onkeydown = event => {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault(); openFontMenu();
  }
};
document.addEventListener('pointerdown', event => {
  if (!$('fontMenu').hidden && !$('fontMenu').contains(event.target) && !$('fontToggle').contains(event.target)) closeFontMenu();
});
document.addEventListener('focusin', event => {
  if (!$('fontMenu').hidden && !$('fontMenu').contains(event.target) && !$('fontToggle').contains(event.target)) closeFontMenu();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('fontMenu').hidden) {
    event.preventDefault(); closeFontMenu(true);
  }
});
window.addEventListener('resize', positionFontMenu);
window.visualViewport?.addEventListener('resize', positionFontMenu);
window.visualViewport?.addEventListener('scroll', positionFontMenu);
$('fontSize').oninput = () => {
  layoutState.fontSize = Number($('fontSize').value);
  applyLayout();
  rememberLayout();
};
$('fontDecrease').onclick = () => {
  layoutState.fontSize -= 1;
  applyLayout();
  rememberLayout();
};
$('fontIncrease').onclick = () => {
  layoutState.fontSize += 1;
  applyLayout();
  rememberLayout();
};
$('layoutReset').onclick = resetLayout;
function trafficDateInput(date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function trafficSize(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value < 1024) return `${Math.round(value)} B`;
  const unit = value < 1024 ** 2 ? 'KB' : value < 1024 ** 3 ? 'MB' : 'GB';
  const divisor = unit === 'KB' ? 1024 : unit === 'MB' ? 1024 ** 2 : 1024 ** 3;
  return `${(value / divisor).toFixed(value / divisor >= 100 ? 1 : 2)} ${unit}`;
}

async function refreshTraffic() {
  if (!$('trafficDialog').open) return;
  const from = new Date($('trafficFrom').value).getTime() / 1000;
  const to = new Date($('trafficTo').value).getTime() / 1000;
  if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to) {
    $('trafficError').textContent = '请选择有效的开始与结束时间';
    return;
  }
  try {
    const report = await api(`/api/traffic?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
    $('trafficError').textContent = '';
    $('trafficCurrentUpload').textContent = trafficSize(report.current.uploadBytes);
    $('trafficCurrentDownload').textContent = trafficSize(report.current.downloadBytes);
    $('trafficCurrentTotal').textContent = trafficSize(report.current.uploadBytes + report.current.downloadBytes);
    $('trafficStarted').textContent = `当前累计起点：${new Date(report.current.startedAt * 1000).toLocaleString()}`;
    $('trafficHistorySummary').textContent = `所选时段：合计 ${trafficSize(report.range.uploadBytes + report.range.downloadBytes)} · 上传 ${trafficSize(report.range.uploadBytes)} · 下载 ${trafficSize(report.range.downloadBytes)}`;
    const rows = $('trafficHistoryRows');
    rows.replaceChildren();
    const header = document.createElement('div');
    header.className = 'traffic-history-row traffic-history-head';
    for (const value of ['日期', '合计', '上传', '下载']) {
      const span = document.createElement('span'); span.textContent = value; header.append(span);
    }
    rows.append(header);
    for (const day of report.days || []) {
      const row = document.createElement('div');
      row.className = 'traffic-history-row';
      for (const value of [day.date, trafficSize(day.uploadBytes + day.downloadBytes),
                           trafficSize(day.uploadBytes), trafficSize(day.downloadBytes)]) {
        const span = document.createElement('span'); span.textContent = value; row.append(span);
      }
      rows.append(row);
    }
    if (!report.days?.length) {
      const empty = document.createElement('div');
      empty.className = 'traffic-history-row';
      empty.textContent = `所选时段暂无记录（开始统计：${new Date(report.firstRecordedAt * 1000).toLocaleString()}）`;
      rows.append(empty);
    }
  } catch (error) {
    $('trafficError').textContent = error.message;
  }
}

function setTrafficRange(days) {
  const end = new Date();
  $('trafficTo').value = trafficDateInput(end);
  $('trafficFrom').value = trafficDateInput(new Date(end.getTime() - days * 86400000));
  void refreshTraffic();
}

$('trafficToggle').onclick = () => {
  $('trafficDialog').showModal();
  setTrafficRange(7);
  if (trafficRefreshTimer) clearInterval(trafficRefreshTimer);
  trafficRefreshTimer = setInterval(refreshTraffic, 10000);
};
$('trafficClose').onclick = () => $('trafficDialog').close();
$('trafficDialog').addEventListener('close', () => {
  clearInterval(trafficRefreshTimer);
  trafficRefreshTimer = null;
});
$('trafficQuery').onclick = refreshTraffic;
document.querySelectorAll('.traffic-presets button').forEach(button => {
  button.onclick = () => setTrafficRange(Number(button.dataset.days));
});
$('trafficReset').onclick = async () => {
  if (!confirm('重置当前流量累计？历史记录仍会保留。')) return;
  $('trafficReset').disabled = true;
  try {
    await api('/api/traffic/reset', {});
    await refreshTraffic();
  } catch (error) {
    $('trafficError').textContent = error.message;
  } finally {
    $('trafficReset').disabled = false;
  }
};
;
;
$('codexUpdatesToggle').onclick = () => {
  if ($('codexUpdatesToggle').hidden) return;
  $('codexUpdatesBody').append($('codexUpdateCard'));
  renderCodexUpdates();
  $('codexUpdatesDialog').showModal();
};
$('codexUpdatesClose').onclick = () => $('codexUpdatesDialog').close();
;
$('codexCheckUpdates').onclick = async () => {
  const host = currentHost();
  if (!host) return;
  $('codexUpdateStatus').textContent = '正在检测目标机 Codex App…';
  try { await checkSoftware('codex', host); }
  catch (error) { $('codexUpdateDetails').textContent = error.message; }
};

function requestSoftwareUpdate(product) {
  const host = currentHost();
  const report = softwareReports.get(`${host?.id}:${product}`);
  
  pendingSoftwareUpdate = {hostId: host.id, product};
  const needsPassword = Boolean(report.requiresPassword);
  $('softwarePasswordTitle').textContent =
    `更新 ${host.label} 的 ${'Codex App'}`;
  $('softwarePasswordHint').textContent = needsPassword ?
    '请输入目标机的系统密码。密码仅用于本次安装，不写入命令历史或配置。更新可能中断正在运行的任务。' :
    '确认从官方发布页下载安装包并在目标机更新。目标机无需再操作；更新可能短暂中断目标 App。';
  $('softwarePasswordLabel').hidden = !needsPassword;
  $('softwarePassword').hidden = !needsPassword;
  $('softwarePassword').required = needsPassword;
  $('softwarePassword').value = '';
  $('softwarePasswordError').textContent = '';
  $('softwarePasswordDialog').showModal();
  (needsPassword ? $('softwarePassword') : $('softwarePasswordSubmit')).focus();
}

async function runSecureSoftwareUpdate(hostId, product, password) {
  softwareBusyHosts.add(hostId);
  renderCodexUpdates();
  setSoftwareNotice('正在目标机安装更新，请勿重复提交…', false, hostId);
  try {
    const request = {host: hostId, package: product, sudoPassword: password};
    const {id} = await api('/api/secure-update', request);
    request.sudoPassword = '';
    password = '';
    const deadline = Date.now() + 3610000;
    let result = null;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 10000));
      const response = await api('/api/secure-update?id=' + encodeURIComponent(id));
      if (response.result) { result = response.result; break; }
    }
    if (!result) throw new Error('更新确认超时；请先检查目标机版本，不要重复提交');
    if (!result.ok) throw new Error(result.error || '目标机更新失败');
    softwareReports.delete(`${hostId}:${product}`);
    const message = result.changed ?
      `${'Codex App'} 已更新至 ${result.version}` :
      `目标机已是 ${result.version}`;
    setSoftwareNotice(result.restartWarning ? `${message}；${result.restartWarning}` :
      product === 'codex' && result.changed ?
      `${message}；${result.clientRestarted ? '已重新启动' : '未确认重启状态'}` : message,
    Boolean(result.restartWarning), hostId);
    const host = catalog.find(item => item.id === hostId);
    if (host) await checkSoftware(product, host);
    
  } catch (error) {
    setSoftwareError(error.message || '更新失败', hostId);
  } finally {
    softwareBusyHosts.delete(hostId);
    if (currentHost()?.id === hostId) renderCodexUpdates();
  }
}

;
$('codexStartUpdate').onclick = () => requestSoftwareUpdate('codex');
$('softwarePasswordCancel').onclick = () => $('softwarePasswordDialog').close();
$('softwarePasswordDialog').addEventListener('close', () => {
  $('softwarePassword').value = '';
  pendingSoftwareUpdate = null;
});
$('softwarePasswordForm').onsubmit = event => {
  event.preventDefault();
  if (!pendingSoftwareUpdate) return;
  const {hostId, product} = pendingSoftwareUpdate;
  const password = $('softwarePassword').value;
  $('softwarePassword').value = '';
  $('softwarePasswordDialog').close();
  void runSecureSoftwareUpdate(hostId, product, password);
};
;
;
;
;
;
$('extensionsToggle').onclick = async () => {
  if (terminalPanelOpen) hideTerminalPanel();
  else {
    if ([...$('terminalHostSelect').options].some(option => option.value === physicalHostId())) {
      $('terminalHostSelect').value = physicalHostId();
    }
    syncTerminalControls();
    const host = catalog.find(item => item.id === $('terminalHostSelect').value);
    if (!host?.terminal?.supported && !$('terminalModeAgents').hidden) terminalView = 'agents';
    else if (terminalView === 'agents' && $('terminalModeAgents').hidden) terminalView = 'files';
    showTerminalPanel();
    if (terminalView === 'agents') return;
    if (terminalView === 'files') {
      const hostId = $('terminalHostSelect').value;
      if (hostId) {void ensureFileTreeRoots(hostId); syncFileDirectoryContext();}
      return;
    }
    try { await syncServerTerminals(); }
    catch (error) {
      setTerminalStatus(`无法同步已开启的终端：${error.message}`, true);
      return;
    }
    if (activeTerminalId && terminalSessions.has(activeTerminalId)) {
      activateTerminal(activeTerminalId);
    } else if (terminalSessions.size) {
      activateTerminal([...terminalSessions.keys()].at(-1));
    }
  }
};
$('newTerminal').onclick = startTerminal;
$('terminalEmptyCreate').onclick = startTerminal;
$('terminalPanelClose').onclick = hideTerminalPanel;
$('terminalModeTerminal').onclick = () => setTerminalView('terminal');
$('terminalModeFiles').onclick = () => setTerminalView('files');
$('terminalModeAgents').onclick = () => setTerminalView('agents');
$('subagentScope').onchange = renderSubagents;
$('fileTreeToggle').onclick = () => {
  fileTreeOpen = !fileTreeOpen;
  applyFileTreeLayout();
  if (fileTreeOpen) {
    const hostId = $('terminalHostSelect').value;
    if (hostId) void ensureFileTreeRoots(hostId);
  }
};
$('fileTreeRefresh').onclick = () => {
  const hostId = $('terminalHostSelect').value;
  if (!hostId) return;
  void ensureFileTreeRoots(hostId, true).then(() => {
    if (fileBrowserState?.host === hostId) void revealFileTreePath(hostId, fileBrowserState.path);
  });
};
$('filePathForm').onsubmit = event => {
  event.preventDefault();
  const hostId = $('terminalHostSelect').value;
  if (hostId) loadFileDirectory(hostId, $('filePath').value.trim());
};
$('fileGoParent').onclick = () => {
  if (fileBrowserState) loadFileDirectory(fileBrowserState.host, fileBrowserState.parent);
};
$('fileRefresh').onclick = () => {
  const hostId = $('terminalHostSelect').value;
  if (hostId) loadFileDirectory(hostId, fileBrowserState?.host === hostId ?
    fileBrowserState.path : fileStartPath(hostId));
};
$('fileLoadMore').onclick = () => {
  if (fileBrowserState) loadFileDirectory(fileBrowserState.host,
    fileBrowserState.path, fileBrowserState.nextOffset, true);
};
installFileContextMenu($('fileRows'));
installFileContextMenu($('fileTreeRows'), true);
$('fileNewFolder').onclick = () => openFileCreateDialog('directory');
$('fileNewFile').onclick = () => openFileCreateDialog('file');
$('fileCopy').onclick = () => {
  const context = fileContext;
  if (!context?.entry || context.entry.isRoot) return;
  fileClipboard = {hostId: context.hostId, path: context.entry.path,
    name: context.entry.name, isDirectory: context.entry.isDirectory};
  syncCopiedFileButton();
  closeFileContextMenu();
  setFileBrowserStatus(`已复制 ${fileClipboard.name}；可在文件夹粘贴${fileClipboard.isDirectory ? '' : '，或在当前对话点“粘贴文件”'}`);
};
$('filePaste').onclick = pasteFileContext;
$('pasteCopiedFile').onclick = pasteCopiedRemoteFile;
$('fileDelete').onclick = openFileDeleteDialog;
$('fileCreateCancel').onclick = () => $('fileCreateDialog').close();
$('fileCreateForm').onsubmit = submitFileCreate;
$('fileCreateDialog').addEventListener('close', () => { fileCreateContext = null; });
$('fileDeleteCancel').onclick = () => $('fileDeleteDialog').close();
$('fileDeleteForm').onsubmit = submitFileDelete;
$('fileDeleteDialog').addEventListener('close', () => { fileDeleteContext = null; });
document.addEventListener('pointerdown', event => {
  if (fileContext && !$('fileContextMenu').contains(event.target)) closeFileContextMenu();
  if (threadContext && !$('threadContextMenu').contains(event.target) &&
      !event.target.closest('.thread-more')) closeThreadContextMenu();
  if (projectContext && !$('projectContextMenu').contains(event.target) &&
      !event.target.closest('.project-delete')) closeProjectContextMenu();
}, true);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && fileContext) closeFileContextMenu();
  if (event.key === 'Escape' && threadContext) closeThreadContextMenu();
  if (event.key === 'Escape' && projectContext) closeProjectContextMenu();
});
window.visualViewport?.addEventListener('resize', () => {
  if (!fileContext) return;
  const menu = $('fileContextMenu');
  positionFileContextMenu(parseFloat(menu.style.left), parseFloat(menu.style.top));
});
window.visualViewport?.addEventListener('resize', () => {
  if (!threadContext) return;
  const menu = $('threadContextMenu');
  positionThreadContextMenu(parseFloat(menu.style.left), parseFloat(menu.style.top));
});
window.visualViewport?.addEventListener('resize', () => {
  if (!projectContext) return;
  const menu = $('projectContextMenu');
  showProjectContextMenu(projectContext.hostId, projectContext.projectId,
    parseFloat(menu.style.left), parseFloat(menu.style.top));
});
$('filePreviewClose').onclick = () => $('filePreviewDialog').close();
$('filePreviewEdit').onclick = () => openRemoteFileEditor(activeFilePreview());
$('filePreviewAttach').onclick = () => openRemoteFileAttach(activeFilePreview());
$('fileAttachCancel').onclick = () => $('fileAttachDialog').close();
$('fileAttachForm').onsubmit = attachRemoteFileToThread;
$('filePreviewToggle').onclick = () => {
  if (activeFilePreview()) activateFilePreview(activeFilePreviewId);
};
$('filePreviewFontSize').oninput = event => setFilePreviewFontSize(event.target.value, true);
$('filePreviewDownload').onclick = async () => {
  const tab = activeFilePreview();
  if (!tab?.downloader) return;
  const button = $('filePreviewDownload');
  const action = tab.downloader;
  button.disabled = true;
  button.textContent = '准备中…';
  try {
    await action();
  } catch (error) {
    setPreviewStatus(tab, `下载失败：${error.message}`);
  } finally {
    button.textContent = '下载';
    button.disabled = !activeFilePreview()?.downloader;
  }
};
$('terminalCopySelection').onclick = () =>
  copyTerminalContextText(terminalContext?.selection);
$('terminalCopyLine').onclick = () =>
  copyTerminalContextText(terminalContext?.line);
$('terminalPaste').onclick = pasteTerminalClipboard;
$('terminalSelectAll').onclick = () => {
  const session = terminalSessions.get(terminalContext?.sessionId);
  if (!session) return;
  session.term.selectAll();
  terminalContext.selection = session.term.getSelection();
  $('terminalCopySelection').disabled = !terminalContext.selection;
};
$('terminalPasteSend').onclick = () => pasteTerminalText($('terminalPasteText').value);
document.addEventListener('pointerdown', event => {
  if (terminalContext && !$('terminalContextMenu').contains(event.target)) {
    closeTerminalContextMenu();
  }
}, true);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && terminalContext) closeTerminalContextMenu();
});
window.visualViewport?.addEventListener('resize', () => {
  if (!terminalContext) return;
  const menu = $('terminalContextMenu');
  fitTerminalContextMenu(parseFloat(menu.style.left), parseFloat(menu.style.top));
});
$('terminalHostSelect').onchange = () => {
  syncTerminalControls();
  applyTerminalView();
  if (terminalView === 'files') {
    const hostId = $('terminalHostSelect').value;
    if (hostId) {
      void ensureFileTreeRoots(hostId);
      syncFileDirectoryContext(true);
    }
  }
  if (!activeTerminalId && terminalView === 'terminal') {
    const host = catalog.find(item => item.id === $('terminalHostSelect').value);
    $('terminalHostHint').textContent = `${host?.label || '目标电脑'} · 尚未启动 SSH 终端`;
  }
};
$('jumpBottom').onclick = () => {
  $('timeline').scrollTo({top: $('timeline').scrollHeight, behavior: 'smooth'});
};
$('timeline').addEventListener('scroll', () => {
  updateJumpBottom();
  // Programmatic scrolling can also produce trusted scroll events. It must
  // never create reading intent for an automatically restored conversation.
  scheduleVisibleThreadRead();
}, {passive: true});

$('attachThread').onclick = async () => {
  if (busy || !selected) return;
  const target = {...selected};
  const launchNeeded = !currentHost()?.desktopConnected;
  const appName = applicationShortName(currentHost());
  const idleLabel = launchNeeded ? '启动并接入' : '接入原会话';
  busy = true;
  $('attachThread').disabled = true;
  $('attachThread').textContent = launchNeeded ? '启动中…' : '接入中…';
  $('sendResult').className = '';
  $('sendResult').textContent = launchNeeded ?
    `正在启动目标 ${appName} App 并打开原会话…` :
    `正在请求目标 ${appName} App 打开原会话…`;
  syncThreadControls();
  try {
    const control = currentHost()?.capabilities?.clientControl;
    if (control?.controlOnly && control.startBridge) {
      await issueCommand(launchNeeded ? 'start_app' : 'start_bridge', {host: target.host});
      const deadline = Date.now() + 40000;
      while (Date.now() < deadline) {
        await refresh();
        const host = catalog.find(item => item.id === target.host);
        if (host?.desktopConnected && !host.capabilities?.clientControl?.controlOnly) break;
        await new Promise(resolve => setTimeout(resolve, 700));
      }
      if (!catalog.find(item => item.id === target.host)?.desktopConnected) throw new Error('桥接已开启，但原 App 尚未连接；请查看客户端状态');
    }
    const result = await issueCommand('attach_thread', target);
    if (selected?.host === target.host && selected?.thread === target.thread) {
      $('sendResult').textContent = result.launchedDesktop ?
        `✓ 已启动 ${appName} App 并接入原会话` :
        '✓ 已接入原桌面会话';
    }
    await refresh();
  } catch (error) {
    $('sendResult').textContent = error.message;
    $('sendResult').className = 'error';
  } finally {
    busy = false;
    $('attachThread').textContent = idleLabel;
    if (selected?.host === target.host && selected?.thread === target.thread) {
      await refreshThread();
    }
  }
};

$('menu').onclick = () => {
  if (mobileQuery.matches) document.body.classList.toggle('sidebar-open');
  else {
    sidebarCollapsed = !sidebarCollapsed;
    try { localStorage.setItem('codexRelaySidebarCollapsed', sidebarCollapsed ? '1' : '0'); }
    catch (_) {}
  }
  updateMobileBackdrop();
};
$('mobileBackdrop').onclick = closeMobilePanels;
$('sidebarClose').onclick = closeMobilePanels;
$('modelSettingsToggle').onclick = () => {
  setModelSettingsOpen(!document.body.classList.contains('composer-settings-open'));
};
mobileQuery.addEventListener('change', syncMobileLayout);
installMobileSidebarScrollGuard();
installMobileSidebarGesture();
installPaneResizer($('sidebarResizer'), 'x', () => layoutState.sidebarWidth,
  value => {layoutState.sidebarWidth = value;});
installPaneResizer($('terminalResizer'), 'x', () => layoutState.terminalWidth,
  value => {layoutState.terminalWidth = value;}, -1);
installPaneResizer($('composerResizer'), 'y', () => layoutState.composerHeight,
  value => {layoutState.composerHeight = value;}, -1);
$('sidebarResizer').ondblclick = () => {
  layoutState.sidebarWidth = DEFAULT_LAYOUT.sidebarWidth;
  applyLayout(); rememberLayout();
};
$('terminalResizer').ondblclick = () => {
  layoutState.terminalWidth = DEFAULT_LAYOUT.terminalWidth;
  applyLayout(); rememberLayout();
};
$('composerResizer').ondblclick = () => {
  layoutState.composerHeight = DEFAULT_LAYOUT.composerHeight;
  applyLayout(); rememberLayout();
};
window.addEventListener('resize', applyLayout, {passive: true});
applyLayout();
syncMobileLayout();
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !event.defaultPrevented) closeMobilePanels();
});
$('search').oninput = () => {
  $('catalog').scrollTop = 0;
  lastCatalog = '';
  captureHostNavigation();
  renderCatalog();
};
$('catalog').addEventListener('scroll', () => {
  if (workspaceMode) {
    workspaceNavigation.catalogScrollTop = $('catalog').scrollTop;
    return;
  }
  if (!selectedHostId) return;
  const state = hostNavigation.get(selectedHostId) || {};
  hostNavigation.set(selectedHostId, {
    ...state,
    catalogScrollTop: $('catalog').scrollTop
  });
}, {passive: true});
window.addEventListener('pagehide', () => {
  captureHostNavigation();
  rememberTerminalSessions();
  rememberLayout();
});
$('newThread').onclick = () => openNewThreadDialog();
$('addAttachment').onclick = () => {
  if (!busy && !$('addAttachment').disabled) $('fileInput').click();
};
function addPendingFiles(additions) {
  const originalCount = additions.length;
  additions = filterSupportedAttachments(additions, 'sendResult');
  if (!additions.length) return 0;
  const byKey = new Map(pendingFiles.map(file => [pendingFileKey(file), file]));
  for (const file of additions) byKey.set(pendingFileKey(file), file);
  if (additions.length === originalCount) {
    $('sendResult').className = '';
    $('sendResult').textContent = '';
  }
  setPendingFiles([...byKey.values()]);
  return additions.length;
}

$('fileInput').onchange = () => {
  const additions = [...$('fileInput').files];
  $('fileInput').value = '';
  addPendingFiles(additions);
};

$('message').addEventListener('paste', event => {
  const additions = clipboardFiles(event.clipboardData);
  if (!additions.length) return;
  event.preventDefault();
  if (busy || $('addAttachment').disabled) {
    $('sendResult').className = 'error';
    $('sendResult').textContent = '请先接入一个可输入的原会话';
    return;
  }
  const accepted = addPendingFiles(additions);
  if (accepted === additions.length) $('sendResult').textContent = `已粘贴 ${accepted} 个文件；发送前可继续输入文字`;
});

let attachmentDragDepth = 0;
function draggingFiles(event) {
  return [...(event.dataTransfer?.types || [])].includes('Files');
}
$('messageForm').addEventListener('dragenter', event => {
  if (!draggingFiles(event)) return;
  event.preventDefault();
  attachmentDragDepth += 1;
  if (!$('addAttachment').disabled) $('messageForm').classList.add('drag-active');
});
$('messageForm').addEventListener('dragover', event => {
  if (!draggingFiles(event)) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect =
    $('addAttachment').disabled ? 'none' : 'copy';
});
$('messageForm').addEventListener('dragleave', event => {
  if (!draggingFiles(event)) return;
  attachmentDragDepth = Math.max(0, attachmentDragDepth - 1);
  if (!attachmentDragDepth) $('messageForm').classList.remove('drag-active');
});
$('messageForm').addEventListener('drop', event => {
  if (!draggingFiles(event)) return;
  event.preventDefault();
  attachmentDragDepth = 0;
  $('messageForm').classList.remove('drag-active');
  if ($('addAttachment').disabled) {
    $('sendResult').className = 'error';
    $('sendResult').textContent = '请先接入一个可输入的原会话';
    return;
  }
  addPendingFiles([...(event.dataTransfer?.files || [])]);
});

$('completionOpen').onclick = async event => {
  const target = completionTarget;
  $('completionNotice').hidden = true;
  if (!target) return;
  const host = catalog.find(item => item.id === target.host);
  const thread = host?.threads.find(item => item.id === target.thread);
  if (host && thread) await selectThread(host, thread, true, event.isTrusted);
};
$('completionDismiss').onclick = () => {
  $('completionNotice').hidden = true;
  completionTarget = null;
};

$('application').onchange = async () => {
  if (selected) {
    drafts.set(`${selected.host}/${selected.thread}`, $('message').value);
    attachmentDrafts.set(`${selected.host}/${selected.thread}`, [...pendingFiles]);
  }
  captureHostNavigation();
  const application = $('application').value;
  workspaceMode = application === WORKSPACE_DEVICE_ID;
  if (!workspaceMode) {
    selectedApplicationId = application;
    selectedHostId = applicationHosts()[0]?.id || '';
  }
  lastCatalog = '';
  clearThread();
  const host = currentHost();
  if (workspaceMode) applyWorkspaceNavigation();
  else if (host) applyHostNavigation(host);
  syncSidebarSelectors();
  renderCatalog();
  
  if ($('codexUpdatesDialog').open) $('codexUpdatesDialog').close();
  const target = workspaceMode ? rememberedWorkspaceTarget() : host ? {host, thread: rememberedThread(host)} : null;
  if (target?.host && target.thread) await selectThread(target.host, target.thread, false, false, {keepSidebarOpen: true});
  else captureHostNavigation();
};

$('device').onchange = async () => {
  if (selected) {
    drafts.set(`${selected.host}/${selected.thread}`, $('message').value);
    attachmentDrafts.set(`${selected.host}/${selected.thread}`, [...pendingFiles]);
  }
  captureHostNavigation();
  const device = $('device').value;
  workspaceMode = false;
  selectedHostId = device;
  lastCatalog = '';
  clearThread();
  const host = currentHost();
  if (host) applyHostNavigation(host);
  syncSidebarSelectors();
  renderCatalog();
  
  const target = host ? {host, thread: rememberedThread(host)} : null;
  if (target?.host && target.thread) {
    await selectThread(target.host, target.thread, false, false, {keepSidebarOpen: true});
  } else {
    captureHostNavigation();
  }
  ;
};

$('projectFilter').onchange = () => {
  projectFilter = $('projectFilter').value;
  $('catalog').scrollTop = 0;
  lastCatalog = '';
  captureHostNavigation();
  renderCatalog();
};

$('archiveFilter').onchange = () => {
  archiveFilter = $('archiveFilter').value;
  $('catalog').scrollTop = 0;
  lastCatalog = '';
  captureHostNavigation();
  renderCatalog();
};

$('addProject').onclick = () => {
  const host = currentHost();
  if (!host) return;
  const meta = selectedThread();
  const path = selected?.host === host.id ? (meta?.cwd || '') : '';
  $('projectDeviceHint').textContent = `目标电脑：${host.label}`;
  $('projectPath').value = path;
  $('projectName').value = path.split(/[\\/]/).filter(Boolean).at(-1) || '';
  $('projectError').textContent = '';
  $('projectDialog').showModal();
  $('projectName').focus();
};

$('cancelProject').onclick = () => $('projectDialog').close();
$('cancelRenameProject').onclick = () => $('renameProjectDialog').close();
$('projectRootsCancel').onclick = () => {
  if (busy) return;
  $('projectRootsDialog').close();
  projectRootsDraft = null;
};
$('projectRootsDialog').addEventListener('cancel', event => {
  if (busy) event.preventDefault();
  else projectRootsDraft = null;
});
$('projectRootsAdd').onclick = () => {
  if (!projectRootsDraft || busy) return;
  projectRootsDraft.roots.push('');
  renderProjectRootsDraft();
  $('projectRootsList').querySelector('.project-root-row:last-child input')?.focus();
};
$('projectRootsForm').onsubmit = async event => {
  event.preventDefault();
  if (!projectRootsDraft || busy) return;
  const draft = projectRootsDraft;
  const roots = draft.roots.map(path => path.trim());
  busy = true;
  renderProjectRootsDraft();
  $('projectRootsError').textContent = '正在同步到目标 Codex App…';
  try {
    const result = await issueCommand('project_rename', {host: draft.host,
      payload: {mode: 'roots', projectId: draft.projectId, roots, expectedRoots: draft.expectedRoots}});
    const project = catalog.find(host => host.id === draft.host)?.projects.find(item => item.id === draft.projectId);
    if (project && result.project?.roots) project.roots = [...result.project.roots];
    $('projectRootsDialog').close();
    projectRootsDraft = null;
    $('sendResult').className = '';
    $('sendResult').textContent = '✓ 项目目录已同步，文件管理器使用默认目录';
    syncFileDirectoryContext();
    await refresh();
  } catch (error) {
    $('projectRootsError').textContent = error.message;
  } finally {
    busy = false;
    renderProjectRootsDraft();
    lastCatalog = '';
    syncThreadControls();
    renderCatalog();
  }
};

$('cancelThread').onclick = () => {
  if (busy) return;
  $('threadDialog').close();
  clearNewThreadFiles(true);
};
$('cancelRenameThread').onclick = () => $('renameThreadDialog').close();
$('newThreadModel').onchange = () => populateNewThreadEfforts('');
$('newThreadAddFile').onclick = () => {
  if (!busy) $('newThreadFileInput').click();
};
$('newThreadFileInput').onchange = () => {
  if (!busy) addNewThreadFiles([...($('newThreadFileInput').files || [])]);
  $('newThreadFileInput').value = '';
};
$('newThreadPasteCopied').onclick = () => {
  if (busy) return;
  const copied = fileClipboard;
  if (!copied || copied.isDirectory || copied.hostId !== physicalHostId()) return;
  addNewThreadFiles([{remoteSourcePath: copied.path, hostId: copied.hostId,
    name: copied.name, type: 'application/octet-stream', size: 0, lastModified: 0}]);
};
$('threadForm').addEventListener('paste', event => {
  const files = clipboardFiles(event.clipboardData);
  if (!files.length) return;
  event.preventDefault();
  if (!busy) addNewThreadFiles(files);
});
$('threadForm').addEventListener('dragover', event => {
  if (!draggingFiles(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = busy ? 'none' : 'copy';
  $('threadForm').classList.toggle('drag-active', !busy);
});
$('threadForm').addEventListener('dragleave', event => {
  if (event.relatedTarget && $('threadForm').contains(event.relatedTarget)) return;
  $('threadForm').classList.remove('drag-active');
});
$('threadForm').addEventListener('drop', event => {
  if (!draggingFiles(event)) return;
  event.preventDefault();
  $('threadForm').classList.remove('drag-active');
  if (!busy) addNewThreadFiles([...(event.dataTransfer?.files || [])]);
});
$('newThreadPrompt').onkeydown = event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
    event.preventDefault();
    $('threadForm').requestSubmit();
  }
};

$('threadForm').onsubmit = async event => {
  event.preventDefault();
  const host = currentHost();
  const prompt = $('newThreadPrompt').value.trim();
  const files = [...newThreadFiles];
  if (!host || busy) return;
  if (!prompt && !files.length) {
    $('threadError').textContent = '请输入第一条消息或添加文件';
    return;
  }
  const targetHostId = host.id;
  const projectId = $('newThreadProject').value;
  const deferred = files.length > 0;
  let createdId = '';
  busy = true;
  $('createThread').disabled = true;
  $('threadError').textContent = deferred ?
    '正在创建对话，随后发送首条消息和附件…' :
    `正在目标 ${applicationShortName(host)} App 创建并启动对话…`;
  syncSidebarSelectors();
  try {
    const result = await issueCommand('new_thread', {
      host: targetHostId,
      thread: selected?.host === targetHostId ? selected.thread : '',
      payload: {
        prompt: deferred ?
          '我接下来会发送包含任务说明和附件的消息。请等待下一条消息，不要开始执行。' : prompt,
        projectId,
        model: $('newThreadModel').value,
        effort: $('newThreadEffort').value
      }
    });
    createdId = result.threadId || result.clientThreadId || '';
    if (deferred && createdId) {
      drafts.set(`${targetHostId}/${createdId}`, prompt);
      attachmentDrafts.set(`${targetHostId}/${createdId}`, files);
    }
    $('threadDialog').close();
    clearNewThreadFiles(false);
    archiveFilter = 'active';
    projectFilter = projectId || 'unassigned';
    $('search').value = '';
    captureHostNavigation(targetHostId);
    setGroupExpanded(targetHostId, projectId || 'unassigned', true);
    lastCatalog = '';

    let created = null;
    for (let attempt = 0; attempt < (deferred ? 30 : 10) && !created; attempt += 1) {
      await refresh();
      const latestHost = catalog.find(item => item.id === targetHostId);
      created = latestHost?.threads.find(thread => thread.id === createdId) || null;
      if (!created) await new Promise(resolve => setTimeout(resolve, 500));
    }
    const latestHost = catalog.find(item => item.id === targetHostId);
    if (created && latestHost) await selectThread(latestHost, created);
    $('sendResult').className = '';
    let attachError = '';
    if (deferred && created && (!current?.owned || !current?.online)) {
      $('sendResult').textContent = '正在接入新对话以发送附件…';
      try {
        await issueCommand('attach_thread', {host: targetHostId, thread: created.id});
        await refreshThread();
      } catch (error) {
        attachError = error.message;
      }
    }
    if (deferred && created && current?.id === created.id && current?.owned && current?.online) {
      const target = {host: targetHostId, thread: created.id};
      const outgoing = beginLocalOutgoing(target, uuid(), prompt, files);
      detachComposerSubmission(target, outgoing);
      await deliverOutgoingMessage(outgoing);
    } else if (deferred) {
      $('sendResult').textContent = '新对话已创建；任务和附件保留在输入框，接入后请点击发送' +
        (attachError ? `（${attachError}）` : '');
    } else {
      $('sendResult').textContent = created ?
        `✓ 新对话已在目标 ${applicationShortName(host)} App 创建并开始执行` :
        `✓ ${applicationShortName(host)} App 已接受创建请求，新对话正在出现在列表中`;
    }
    if (result.projectAssignmentWarning) {
      $('sendResult').textContent += `；${result.projectAssignmentWarning}`;
    }
  } catch (error) {
    if (createdId) {
      if ($('threadDialog').open) {
        $('threadDialog').close();
        clearNewThreadFiles(false);
      }
      $('sendResult').className = 'error';
      $('sendResult').textContent = `新对话已创建，但正式任务未发送：${error.message}。草稿和附件已保留，可在输入框重试。`;
    } else {
      $('threadError').textContent = error.message;
    }
  } finally {
    busy = false;
    $('createThread').disabled = false;
    lastCatalog = '';
    captureHostNavigation(targetHostId);
    syncSidebarSelectors();
    renderCatalog();
    syncThreadControls();
  }
};

$('projectForm').onsubmit = async event => {
  event.preventDefault();
  const host = currentHost();
  if (!host || busy) return;
  busy = true;
  $('createProject').disabled = true;
  $('projectError').textContent = '正在目标电脑创建项目…';
  try {
    const result = await issueCommand('project_create', {
      host: host.id,
      payload: {name: $('projectName').value, root: $('projectPath').value}
    });
    $('projectDialog').close();
    if (result.project?.id) projectFilter = result.project.id;
    captureHostNavigation(host.id);
    $('sendResult').className = '';
    $('sendResult').textContent = '✓ 项目已添加';
    await refresh();
  } catch (error) {
    $('projectError').textContent = error.message;
  } finally {
    busy = false;
    $('createProject').disabled = false;
    syncSidebarSelectors();
    syncThreadControls();
  }
};

$('renameThread').onclick = () => {
  const {host, thread: meta} = threadContextTarget();
  if (!host || !meta || busy) return;
  const target = {...threadContext};
  closeThreadContextMenu();
  $('renameThreadDialog').dataset.hostId = target.hostId;
  $('renameThreadDialog').dataset.threadId = target.threadId;
  $('renameThreadHint').textContent = `目标电脑：${host.label}`;
  $('renameThreadName').value = plainTitle(meta.title);
  $('renameThreadError').textContent = '';
  $('renameThreadDialog').showModal();
  $('renameThreadName').focus();
  $('renameThreadName').select();
};

$('locateProject').onclick = event => {
  if (selected) revealThreadProject(selected.host, selected.thread, event.isTrusted);
};
$('refreshThreadMenu').onclick = () => {
  if (!threadContext) return;
  const {hostId, threadId} = threadContext;
  closeThreadContextMenu();
  void refreshThreadFromSource(hostId, threadId);
};

$('workspaceThreadOrigin').onclick = event => {
  if (!threadContext) return;
  const {hostId, threadId} = threadContext;
  closeThreadContextMenu();
  revealWorkspaceThreadOrigin(hostId, threadId, event.isTrusted);
};

$('workspaceThreadToggle').onclick = () => {
  if (!threadContext) return;
  const target = {...threadContext};
  closeThreadContextMenu();
  void toggleWorkspaceThread(target.hostId, target.threadId);
};

for (const [id, direction] of [['workspaceThreadUp', -1], ['workspaceThreadDown', 1]]) {
  $(id).onclick = () => {
    if (!threadContext || workspaceUpdateBusy) return;
    const {hostId, threadId} = threadContext;
    const neighbor = workspaceMoveNeighbor(hostId, threadId, direction);
    closeThreadContextMenu();
    if (neighbor) void moveWorkspaceThread({host: hostId, thread: threadId},
      {host: neighbor.host.id, thread: neighbor.thread.id}, direction > 0);
  };
}

$('renameThreadForm').onsubmit = async event => {
  event.preventDefault();
  const target = {host: $('renameThreadDialog').dataset.hostId,
    thread: $('renameThreadDialog').dataset.threadId};
  const host = catalog.find(item => item.id === target.host);
  const meta = host?.threads.find(item => item.id === target.thread);
  const name = $('renameThreadName').value.trim();
  if (!host || !meta || !name || busy) return;
  busy = true;
  $('saveRenameThread').disabled = true;
  $('renameThreadError').textContent = `正在同步到目标 ${applicationShortName(host)} App…`;
  syncThreadControls();
  try {
    await issueCommand('rename_thread', {...target, payload: {name}});
    $('renameThreadDialog').close();
    $('sendResult').className = '';
    $('sendResult').textContent = '✓ 对话名称已同步';
    await refresh();
  } catch (error) {
    $('renameThreadError').textContent = error.message;
  } finally {
    busy = false;
    $('saveRenameThread').disabled = false;
    lastCatalog = '';
    syncThreadControls();
    renderCatalog();
  }
};

async function deleteProjectItem(host, project) {
  if (!host || !project || busy) return;
  if (!window.confirm(`删除项目“${project.name}”？项目中的对话会保留并回到未分类。`)) return;
  busy = true;
  $('sendResult').className = '';
  $('sendResult').textContent = '正在删除项目…';
  syncSidebarSelectors();
  try {
    await issueCommand('project_delete', {
      host: host.id, payload: {projectId: project.id}
    });
    if (projectFilter === project.id) projectFilter = 'all';
    captureHostNavigation(host.id);
    $('sendResult').textContent = '✓ 项目已删除，对话仍然保留';
    await refresh();
  } catch (error) {
    $('sendResult').textContent = error.message;
    $('sendResult').className = 'error';
  } finally {
    busy = false;
    lastCatalog = '';
    renderCatalog();
  }
}

$('projectMenuNewThread').onclick = () => {
  const target = {...projectContext};
  closeProjectContextMenu();
  if (target.hostId !== selectedHostId) return;
  openNewThreadDialog(target.projectId === 'unassigned' ? '' : target.projectId);
};
$('projectMenuRename').onclick = () => {
  const {host, project} = projectContextTarget();
  if (!host || !project || busy) return;
  closeProjectContextMenu();
  $('renameProjectDialog').dataset.hostId = host.id;
  $('renameProjectDialog').dataset.projectId = project.id;
  $('renameProjectHint').textContent = `目标电脑：${host.label}`;
  $('renameProjectName').value = project.name;
  $('renameProjectError').textContent = '';
  $('renameProjectDialog').showModal();
  $('renameProjectName').focus();
  $('renameProjectName').select();
};
$('projectMenuDirectories').onclick = () => {
  const {host, project} = projectContextTarget();
  if (!host || !project) return;
  closeProjectContextMenu();
  openProjectRootsDialog(host, project);
};
$('projectMenuOpenDirectory').onclick = () => {
  const {host, project} = projectContextTarget();
  if (!host || !project?.roots?.[0]) return;
  closeProjectContextMenu();
  openProjectFileDirectory(host.id, project.roots[0]);
};
$('renameProjectForm').onsubmit = async event => {
  event.preventDefault();
  const hostId = $('renameProjectDialog').dataset.hostId;
  const projectId = $('renameProjectDialog').dataset.projectId;
  const name = $('renameProjectName').value.trim();
  if (!hostId || !projectId || !name || busy) return;
  busy = true;
  $('saveRenameProject').disabled = true;
  $('renameProjectError').textContent = '正在同步项目名称…';
  try {
    await issueCommand('project_rename', {host: hostId,
      payload: {projectId, name}});
    $('renameProjectDialog').close();
    $('sendResult').className = '';
    $('sendResult').textContent = '✓ 项目已重命名';
    await refresh();
  } catch (error) {
    $('renameProjectError').textContent = error.message;
  } finally {
    busy = false;
    $('saveRenameProject').disabled = false;
    lastCatalog = '';
    renderCatalog();
  }
};
$('projectMenuDelete').onclick = () => {
  const {host, project} = projectContextTarget();
  closeProjectContextMenu();
  deleteProjectItem(host, project);
};

$('moveProject').onchange = async () => {
  const {host, thread: meta} = threadContextTarget();
  if (!host || !meta || busy) return;
  const destinationProjectId = $('moveProject').value;
  if (destinationProjectId === (meta.projectId || '')) {
    closeThreadContextMenu();
    return;
  }
  const target = {host: host.id, thread: meta.id};
  closeThreadContextMenu();
  busy = true;
  $('sendResult').className = '';
  $('sendResult').textContent = '正在移动对话…';
  syncThreadControls();
  try {
    await issueCommand('move_project', {...target,
      payload: {projectId: destinationProjectId}});
    $('sendResult').textContent = '✓ 已移动到目标项目';
    await refresh();
  } catch (error) {
    $('sendResult').textContent = error.message;
    $('sendResult').className = 'error';
  } finally {
    busy = false;
    syncThreadControls();
    lastCatalog = '';
    renderCatalog();
  }
};

$('archive').onclick = async () => {
  const {host, thread: meta} = threadContextTarget();
  if (!host || !meta || busy) return;
  const target = {host: host.id, thread: meta.id};
  closeThreadContextMenu();
  const action = meta.archived ? 'unarchive' : 'archive';
  if (!meta.archived && !window.confirm(`归档“${plainTitle(meta.title)}”？`)) return;
  busy = true;
  $('sendResult').className = '';
  $('sendResult').textContent = meta.archived ? '正在取消归档…' : '正在归档…';
  syncThreadControls();
  try {
    await issueCommand(action, target);
    const wasSelected = selected?.host === target.host && selected?.thread === target.thread;
    if (wasSelected && action === 'archive') {
      clearThread();
      if (workspaceMode) workspaceNavigation = {...workspaceNavigation, threadId: ''};
      const navigation = hostNavigation.get(host.id) || {};
      hostNavigation.set(host.id, {...navigation, threadId: ''});
      rememberHostNavigation();
    }
    $('sendResult').textContent = action === 'archive' ? '✓ 已归档' : '✓ 已恢复为活跃会话';
    await refresh();
  } catch (error) {
    $('sendResult').textContent = error.message;
    $('sendResult').className = 'error';
  } finally {
    busy = false;
    syncThreadControls();
    lastCatalog = '';
    renderCatalog();
  }
};

$('copyThreadLink').onclick = async () => {
  const {host, thread} = threadContextTarget();
  if (!thread || applicationId(host) !== 'codex' || host.capabilities?.nativeDeepLinks === false) return;
  const link = `codex://threads/${encodeURIComponent(thread.id)}`;
  closeThreadContextMenu();
  await copyPlainText(link, $('copyThreadLink'));
  $('sendResult').className = '';
  $('sendResult').textContent = '✓ 已复制 Codex 对话链接';
};

$('copyThreadCwd').onclick = async () => {
  const {thread} = threadContextTarget();
  if (!thread?.cwd) return;
  closeThreadContextMenu();
  await copyPlainText(thread.cwd, $('copyThreadCwd'));
  $('sendResult').className = '';
  $('sendResult').textContent = '✓ 已复制工作目录';
};

function threadMarkdown(title, items) {
  const output = [`# ${plainTitle(title)}`];
  for (const original of items) {
    const item = visibleScheduledItem(original);
    if (!item || !['userMessage', 'steeringUserMessage', 'agentMessage',
      'assistantMessage'].includes(item.type)) continue;
    const text = String(item.text || '').trim();
    const attachments = (item.attachments || []).map(value =>
      value.label || value.name).filter(Boolean);
    if (!text && !attachments.length) continue;
    const speaker = ['userMessage', 'steeringUserMessage'].includes(item.type) ?
      '你' : item.phase === 'commentary' ? 'Codex · 进展' : 'Codex';
    output.push(`\n## ${speaker}\n`, text,
      ...attachments.map(name => `\n- 附件：${name}`));
  }
  return output.join('\n').trim() + '\n';
}

$('copyThreadMarkdown').onclick = async () => {
  const {host, thread} = threadContextTarget();
  if (!host || !thread || busy) return;
  const target = {host: host.id, thread: thread.id};
  closeThreadContextMenu();
  $('threadMarkdownText').value = '';
  $('threadMarkdownStatus').textContent = '正在读取对话…';
  $('threadMarkdownCopy').disabled = true;
  $('threadMarkdownDialog').showModal();
  try {
    const path = `/api/lite/thread?host=${encodeURIComponent(target.host)}` +
      `&id=${encodeURIComponent(target.thread)}`;
    const reply = await api(path);
    const snapshot = reply.thread;
    if (!snapshot) throw new Error('没有找到对话内容');
    let items = [...(snapshot.items || [])];
    if (snapshot.canLoadHistory) {
      const pages = [];
      const seen = new Set();
      let cursor = null;
      for (let page = 0; page < 1000; page += 1) {
        $('threadMarkdownStatus').textContent = `正在读取完整历史：第 ${page + 1} 页…`;
        const result = await issueCommand('load_older', {...target,
          payload: {cursor, limit: 5}});
        pages.unshift(...(result.items || []));
        cursor = result.nextCursor || null;
        if (!cursor) break;
        if (seen.has(cursor)) throw new Error('历史接口返回重复游标，已停止复制');
        seen.add(cursor);
        if (page === 999) throw new Error('对话历史超过可复制上限');
      }
      items = [...pages, ...items];
    } else if (snapshot.partialHistory || snapshot.totalItems > items.length) {
      throw new Error('目标机没有完整历史接口，无法保证复制完整对话');
    }
    const unique = new Map();
    for (const item of items) unique.set(itemKey(item), item);
    const markdown = threadMarkdown(thread.title, [...unique.values()]);
    $('threadMarkdownText').value = markdown;
    $('threadMarkdownStatus').textContent = `已读取 ${unique.size} 条记录，点击“复制”写入剪贴板。`;
    $('threadMarkdownCopy').disabled = false;
  } catch (error) {
    $('threadMarkdownStatus').textContent = `读取失败：${error.message}`;
  }
};
$('threadMarkdownCopy').onclick = () =>
  copyPlainText($('threadMarkdownText').value, $('threadMarkdownCopy'));
$('threadMarkdownClose').onclick = () => $('threadMarkdownDialog').close();

$('modelSelect').onchange = () => {
  modelDirty = true;
  populateEfforts($('modelSelect').value, '');
};
$('effortSelect').onchange = () => {modelDirty = true;};
function cancelModelSelection() {
  modelDirty = false;
  syncThreadControls();
  setModelSettingsOpen(false);
}
$('cancelSettings').onclick = cancelModelSelection;
$('quickSpeed').onchange = async () => {
  const host = currentHost();
  const meta = selectedThread();
  if (!host || !meta || busy || $('quickSpeed').disabled) {
    syncThreadControls();
    return;
  }
  // This switch changes the active conversation, never an unapplied model draft.
  const model = current?.model || meta.model;
  const effort = current?.reasoningEffort || meta.reasoningEffort;
  const enabled = $('quickSpeed').checked;
  const tier = enabled ? ['priority', 'fast', 'ultrafast'].find(id =>
    modelServiceTiers(model).some(item => item.id === id)) : 'default';
  const target = {host: host.id, thread: meta.id};
  const isTarget = () => selected?.host === target.host && selected?.thread === target.thread;
  pendingSpeedChange = {...target, enabled};
  busy = true;
  $('sendResult').className = '';
  $('sendResult').textContent = enabled ? '正在开启闪电加速…' : '正在关闭闪电加速…';
  syncThreadControls();
  try {
    await issueCommand('update_settings', {...target, payload: {model, effort, serviceTier: tier}});
    if (isTarget()) {
      if (current?.model === model) current.serviceTier = tier;
      $('sendResult').textContent = enabled ? '✓ 已开启闪电加速' : '✓ 已关闭闪电加速';
    }
  } catch (error) {
    if (isTarget()) {
      $('sendResult').textContent = error.message;
      $('sendResult').className = 'error';
    }
  } finally {
    pendingSpeedChange = null;
    busy = false;
    syncThreadControls();
    if (isTarget()) await refreshThread(true);
  }
};
$('fullAccess').onchange = async () => {
  const host = currentHost();
  const meta = selectedThread();
  if (!host || !meta || busy) return;
  const requested = $('fullAccess').checked;
  permissionDirty = true;
  busy = true;
  $('sendResult').className = '';
  $('sendResult').textContent = requested ? '正在开启完全访问…' : '正在恢复工作区访问…';
  syncThreadControls();
  try {
    await issueCommand('update_permissions', {
      host: host.id, thread: meta.id, payload: {fullAccess: requested}
    });
    if (current) current.fullAccess = requested;
    $('sendResult').textContent = requested ?
      '✓ 后续轮次已使用完全访问' : '✓ 后续轮次已恢复工作区访问';
  } catch (error) {
    $('fullAccess').checked = !requested;
    $('sendResult').textContent = error.message;
    $('sendResult').className = 'error';
  } finally {
    busy = false;
    permissionDirty = false;
    await refreshThread();
  }
};

$('applySettings').onclick = async () => {
  const host = currentHost();
  const meta = selectedThread();
  if (!host || !meta || busy) return;
  busy = true;
  $('sendResult').className = '';
  $('sendResult').textContent = '正在同步模型设置…';
  syncThreadControls();
  try {
    await issueCommand('update_settings', {
      host: host.id, thread: meta.id,
      payload: {model: $('modelSelect').value, effort: $('effortSelect').value,
        ...(modelServiceTiers($('modelSelect').value).length &&
            serviceTierForModel($('modelSelect').value, current?.serviceTier || 'default')
          ? {serviceTier: serviceTierForModel($('modelSelect').value, current?.serviceTier || 'default')} : {})}
    });
    modelDirty = false;
    $('sendResult').textContent = `✓ 原 ${APPLICATION_LABELS[applicationId(host)] || applicationId(host)} 对话的模型设置已同步`;
    await refresh();
    setModelSettingsOpen(false);
  } catch (error) {
    $('sendResult').textContent = error.message;
    $('sendResult').className = 'error';
  } finally {
    busy = false;
    syncThreadControls();
  }
};

$('message').onkeydown = event => {
  if (event.key === 'Enter' && !mobileQuery.matches && !touchKeyboardQuery.matches && !event.shiftKey &&
      !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    $('messageForm').requestSubmit();
  }
};
$('message').oninput = syncSendButton;

$('stopTurn').onclick = async () => {
  if (busy || !selected || current?.status !== 'active' || !current?.owned || !current?.online) return;
  const target = {...selected};
  busy = true;
  $('stopTurn').disabled = true;
  $('sendResult').className = '';
  $('sendResult').textContent = '正在停止当前任务…';
  try {
    const result = await issueCommand('stop_turn', target);
    $('sendResult').textContent = result.warning || '✓ 已请求停止当前任务';
  } catch (error) {
    $('sendResult').textContent = error.message;
    $('sendResult').className = 'error';
  } finally {
    busy = false;
    await refreshThread();
  }
};

async function prepareOutgoingRetry(record) {
  if (!record.sendRequested) return true; // Attachment preparation failed before any send POST.
  await refreshThread(true);
  if (record.nativeReceipt) return false;
  let value;
  try {value = await api('/api/command?id=' + encodeURIComponent(record.commandId));}
  catch (error) {
    // Reuse the SAME id if the POST was lost: the relay's persistent command
    // idempotency also protects against a late original POST arriving now.
    if (error.httpStatus === 404) return true;
    throw error;
  }
  if (record.nativeReceipt) return false;
  if (value.result?.ok) {
    confirmLocalOutgoing(record.target, record, value.result);
    showLocalOutgoing(record.target);
    return false;
  }
  if (!value.result) return true; // Rejoin the existing command, never dispatch it twice.
  const uncertain = value.result.unknown === true ||
    /(?:-timeout$|请求超时|确认超时)/i.test(value.result.error || '');
  if (uncertain && !window.confirm('尚无法确认原 App 是否已接收。再次发送可能产生重复消息。仍要重新发送这条消息吗？')) {
    record.item.localProgress = '';
    updateLocalOutgoing(record.target, record, 'unknown', '未重复发送；可稍后重试核对接收状态');
    return false;
  }
  if (record.nativeReceipt || selected?.host !== record.target.host ||
      selected?.thread !== record.target.thread) return false;
  // A definite rejection (or an explicit duplicate-risk confirmation) permits
  // a NEW attempt. It replaces the same bubble, not the user's current draft.
  record.commandId = uuid();
  record.attemptIds.add(record.commandId);
  record.sendRequested = false;
  return true;
}

async function deliverOutgoingMessage(record, retry = false) {
  const target = record.target;
  const confirmationController = new AbortController();
  record.inFlight = true;
  record.item.localInFlight = true;
  busy = true;
  composerDispatch = record;
  $('send').disabled = true;
  $('message').disabled = false;
  $('addAttachment').disabled = true;
  $('sendResult').className = '';
  $('sendResult').textContent = '';
  renderUploadTray();
  try {
    await record.persistence;
    if (retry) {
      record.item.localProgress = '正在核对接收状态…';
      showLocalOutgoing(target);
      if (!await prepareOutgoingRetry(record)) return;
      if (selected?.host !== target.host || selected?.thread !== target.thread || record.nativeReceipt) return;
    }
    record.item.localProgress = '';
    for (let index = record.uploadedAttachments.length; index < record.originalFiles.length; index += 1) {
      updateLocalOutgoing(target, record, 'preparing');
      record.uploadedAttachments.push(await uploadFile(target, record.originalFiles[index],
        index, record.originalFiles.length, text => {
          record.item.localProgress = text;
          showLocalOutgoing(target);
        }));
      await persistOutgoingRecord(record);
    }
    record.item.localProgress = '';
    updateLocalOutgoing(target, record, 'sending');
    record.sendRequested = true;
    await persistOutgoingRecord(record);
    const commandResult = issueCommand('send', {...target, text: record.originalText,
      attachments: record.uploadedAttachments, commandId: record.commandId,
      signal: confirmationController.signal});
    const result = await Promise.race([commandResult, record.confirmation])
      .catch(error => {if (record.nativeReceipt) return record.nativeReceipt; throw error;});
    confirmationController.abort();
    confirmLocalOutgoing(target, record, result);
    showLocalOutgoing(target);
    record.persistence = persistOutgoingRecord(record);
    // Accepted records cannot be retried. Release potentially large File refs.
    record.originalFiles = [];
    record.uploadedAttachments = [];
    scheduleCatalogRefresh();
  } catch (error) {
    record.item.localProgress = '';
    const rejected = error.commandRejected || (error.httpStatus >= 400 && error.httpStatus < 500);
    updateLocalOutgoing(target, record, !record.sendRequested || rejected ? 'failed' : 'unknown', error.message);
  } finally {
    confirmationController.abort();
    record.inFlight = false;
    record.item.localInFlight = false;
    record.item.localProgress = '';
    if (record.nativeReceipt) {
      record.originalFiles = [];
      record.uploadedAttachments = [];
    }
    if (composerDispatch === record) composerDispatch = null;
    busy = false;
    showLocalOutgoing(target);
    syncThreadControls();
    renderUploadTray();
    if (selected?.host === target.host && selected?.thread === target.thread) {
      const live = current?.owned && current?.online && !threadPreviewOnly;
      $('message').disabled = !live;
      $('addAttachment').disabled = !live;
      $('send').disabled = !live;
      try {await refreshThread(true);}
      catch (_) {setRelayConnectionStatus('连接中断，正在重试');}
    }
  }
}

async function retryOutgoingMessage(record) {
  if (!record || busy || record.inFlight || record.nativeReceipt ||
      !['failed', 'unknown'].includes(record.item.localDelivery) ||
      selected?.host !== record.target.host || selected?.thread !== record.target.thread ||
      !current?.owned || !current?.online || threadPreviewOnly) return;
  if (record.missingFiles) {
    // Storage-denied browsers must never send a text-only replacement by accident.
    window.alert('这条消息有未恢复的附件，请重新选择原文件后发送；未重复发送原消息。');
    return;
  }
  await deliverOutgoingMessage(record, true);
}

async function submitComposer() {
  if (busy || filePasteToComposerBusy || !selected || !current?.owned || !current?.online) return;
  if (!$('message').value.trim() && !pendingFiles.length) {
    if (!current.canContinue || !supportsAction(currentHost(), 'continue_turn')) return;
    const target = {...selected};
    busy = true;
    $('send').disabled = true;
    $('sendResult').className = '';
    $('sendResult').textContent = '正在继续已停止的任务…';
    try {
      await issueCommand('continue_turn', {...target, payload: current.capacityRetry ?
        {turnId: current.capacityRetry.turnId} : {}});
      $('sendResult').textContent = '✓ 已继续任务';
      await refresh();
    } catch (error) {
      $('sendResult').textContent = error.message;
      $('sendResult').className = 'error';
    } finally {
      busy = false;
      if (selected?.host === target.host && selected?.thread === target.thread) {
        await refreshThread();
      }
    }
    return;
  }
  const target = {...selected};
  const text = $('message').value;
  const files = [...pendingFiles];
  const commandId = uuid();
  const outgoing = beginLocalOutgoing(target, commandId, text, files);
  detachComposerSubmission(target, outgoing);
  await deliverOutgoingMessage(outgoing);
}

$('messageForm').onsubmit = event => {
  event.preventDefault();
  submitComposer();
};

window.addEventListener('crb-language-change', () => {
  applyWorkspaceTitle(workspaceTitle);
  applyAppVersion(workspaceAppVersion);
  setRelayConnectionStatus(relayConnectionMessage);
  syncSidebarSelectors();
  syncThreadControls();
  if (!selected) {
    $('title').textContent = uiText('选择一个对话');
    $('subtitle').textContent = uiText('从左侧选择项目和对话');
  }
});
document.title = uiText(document.title);
api('/api/ui/title').then(result => {
  applyWorkspaceTitle(result.title);
  applyAppVersion(result.appVersion);
  applyFeedbackMetadata(result);
}).catch(() => {});
start().catch(showLogin);
