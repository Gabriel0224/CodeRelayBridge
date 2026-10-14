/* Small on-demand surfaces for native Desktop features; no periodic requests. */
(() => {
  'use strict';
  const names = {tasks: '定时任务', permissions: '权限', tools: '技能与工具', review: '审阅', metadata: '上下文与用时'};
  const dialog = document.createElement('dialog');
  dialog.id = 'nativeSettingsDialog';
  dialog.className = 'native-settings-dialog';
  dialog.innerHTML = '<div class="native-header"><strong>原生设置</strong><button type="button" class="quiet" aria-label="关闭设置">×</button></div><nav class="native-tabs" aria-label="设置分类"></nav><div class="native-status" role="status"></div><section class="native-body"></section>';
  document.body.append(dialog);
  const body = dialog.querySelector('.native-body'), status = dialog.querySelector('.native-status');
  let target = null, section = 'tasks', epoch = 0, pending = false, readController = null;
  const readTimeoutMs = 15000;
  const element = (tag, value, className = '') => {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  const button = (title, fn) => {
    const node = element('button', title, 'secondary');
    node.type = 'button'; node.onclick = fn; return node;
  };
  const input = (label, value = '', tag = 'input') => {
    const wrap = element('label', label, 'native-field');
    const node = element(tag);
    node.value = value; wrap.append(node); return {wrap, node};
  };
  const select = (label, rows, value) => {
    const control = input(label, '', 'select');
    for (const row of rows) {
      const option = element('option', row.label);
      option.value = row.value; option.disabled = Boolean(row.disabled);
      control.node.append(option);
    }
    if (value !== undefined) control.node.value = value;
    return control;
  };
  function supportedSections() {
    const host = currentHost();
    if (!host || host.capabilities?.nativeSettings === false) return [];
    if (Array.isArray(host.capabilities?.nativeSections)) return host.capabilities.nativeSections.filter(key => key in names);
    return applicationId(host) === 'codex' ? Object.keys(names) : [];
  }
  function syncCapabilities() {
    const allowed = supportedSections();
    for (const node of document.querySelectorAll('[data-native-section]')) node.hidden = !allowed.includes(node.dataset.nativeSection);
    for (const tab of dialog.querySelectorAll('.native-tabs button')) tab.hidden = !allowed.includes(tab.dataset.section);
    if (dialog.open && (!valid() || !allowed.includes(section))) dialog.close();
  }
  function valid() {
    return !$('workspace').hidden && target && selected?.host === target.host && selected?.thread === target.thread;
  }
  async function command(action, payload, options = {}) {
    if (!valid()) throw new Error('当前对话已切换，请重新打开设置');
    return issueCommand(action, {...target, payload, ...options});
  }
  async function mutate(operation, payload = {}, action = 'native_action') {
    if (pending) return;
    pending = true; status.textContent = '正在由原 App 保存…';
    body.setAttribute('aria-busy', 'true');
    try {
      const result = await command(action, {...payload, ...(action === 'native_action' ? {operation} : {})});
      await load(section);
      if (result.warning) status.textContent = result.warning;
    } catch (error) { status.textContent = error.message; }
    finally { pending = false; body.removeAttribute('aria-busy'); }
  }
  function scheduleForm(existing = {}) {
    const box = element('div', undefined, 'native-schedule');
    const modes = [{value: 'interval', label: '按分钟间隔'}, {value: 'daily', label: '每天'}, {value: 'weekly', label: '每周'}];
    if (existing.mode === 'preserve') modes.unshift({value: 'preserve', label: '保留原安排'});
    const mode = select('频率', modes, existing.mode || 'interval');
    const minutes = input('间隔（分钟）', existing.minutes || 15); minutes.node.type = 'number'; minutes.node.min = '1'; minutes.node.max = '10080';
    const time = input('目标电脑当地时间', `${String(existing.hour ?? 9).padStart(2, '0')}:${String(existing.minute ?? 0).padStart(2, '0')}`); time.node.type = 'time';
    const day = select('星期', ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'].map((value, index) => ({value, label: '星期' + '一二三四五六日'[index]})), existing.day || 'MO');
    const update = () => { minutes.wrap.hidden = mode.node.value !== 'interval'; time.wrap.hidden = !['daily', 'weekly'].includes(mode.node.value); day.wrap.hidden = mode.node.value !== 'weekly'; };
    mode.node.onchange = update; update(); box.append(mode.wrap, minutes.wrap, time.wrap, day.wrap);
    return {box, value: () => {
      const [hour, minute] = time.node.value.split(':').map(Number);
      return {mode: mode.node.value, minutes: Number(minutes.node.value), hour, minute, day: day.node.value};
    }};
  }
  function taskEditor(task = null) {
    const card = element('form', undefined, 'native-card');
    const name = input('名称', task?.name || ''); name.node.maxLength = 200; name.node.required = true;
    const prompt = input('任务内容', task?.prompt || '', 'textarea'); prompt.node.maxLength = 32000; prompt.node.required = true; prompt.node.rows = 5;
    const schedule = scheduleForm(task?.schedule);
    const state = select('状态', [{value: 'ACTIVE', label: '启用'}, {value: 'PAUSED', label: '暂停'}], task?.status || 'ACTIVE');
    const notify = select('通知', [{value: 'default', label: '原 App 默认通知'}, {value: 'failed_runs_only', label: '仅失败通知'}], task?.notificationPolicy || 'default');
    const actions = element('div', undefined, 'native-actions');
    actions.append(button('取消', () => void load('tasks')));
    const save = element('button', task ? '保存修改' : '创建'); save.type = 'submit'; actions.append(save);
    card.append(name.wrap, prompt.wrap, schedule.box, state.wrap, notify.wrap, actions);
    card.onsubmit = async event => {
      event.preventDefault();
      const payload = {name: name.node.value, prompt: prompt.node.value, schedule: schedule.value(), status: state.node.value,
        notificationPolicy: notify.node.value === 'default' ? null : 'failed_runs_only'};
      if (task) Object.assign(payload, {id: task.id, revision: task.revision});
      await mutate(task ? 'task_update' : 'task_create', payload);
    };
    body.replaceChildren(card);
  }
  function tasks(data) {
    body.append(button('＋ 新建当前对话定时任务', () => taskEditor()));
    if (!data.tasks.length) body.append(element('p', '当前对话没有定时任务。'));
    for (const task of data.tasks) {
      const card = element('div', undefined, 'native-card');
      card.append(element('strong', task.name), element('p', `${task.status === 'ACTIVE' ? '启用' : '暂停'} · ${task.schedule.label}`));
      const details = element('details'); details.append(element('summary', '任务内容'), element('pre', task.prompt)); card.append(details);
      const actions = element('div', undefined, 'native-actions');
      actions.append(button('编辑', () => taskEditor(task)), button(task.status === 'ACTIVE' ? '暂停' : '恢复', () => mutate('task_update', {
        id: task.id, revision: task.revision, status: task.status === 'ACTIVE' ? 'PAUSED' : 'ACTIVE'})),
      button('删除', () => { if (confirm(`删除原 App 的定时任务“${task.name}”？`)) void mutate('task_delete', {id: task.id, revision: task.revision}); }));
      card.append(actions); body.append(card);
    }
  }
  function permissions(data) {
    if (data.applicationId === 'deepseek') {
      const labels = {'read-only': '仅可查看', 'workspace-write': '工作区内修改', 'danger-full-access': '完全权限', auto: '自动审阅（原生）', custom: '自定义'};
      const options = data.options.map(row => ({value: row.value, label: labels[row.value] || row.name, disabled: false}));
      if (!options.some(row => row.value === data.currentValue)) options.push({value: data.currentValue, label: labels[data.currentValue] || data.currentValue, disabled: true});
      const preset = select('DeepSeek 访问权限', options, data.currentValue); preset.node.id = 'harnessPermissionPreset';
      const description = element('p');
      const gate = element('label', undefined, 'native-field');
      const acknowledge = element('input'); acknowledge.type = 'checkbox'; acknowledge.id = 'harnessPermissionRisk';
      gate.append(acknowledge, document.createTextNode(' 我已了解风险，并愿意继续'));
      const apply = button('应用权限', () => mutate('', {preset: preset.node.value,
        expectedPreset: data.currentValue, confirmRisk: acknowledge.checked}, 'update_permissions'));
      apply.id = 'harnessPermissionApply';
      const update = () => {
        const risky = preset.node.value !== data.currentValue && ['danger-full-access', 'auto'].includes(preset.node.value);
        gate.hidden = !risky;
        description.textContent = data.options.find(o => o.value === preset.node.value)?.description ||
          (risky ? '该原生模式会减少人工审批，可能执行文件修改、外部命令或敏感操作。' : '使用原 Harness 的权限模式，仅修改当前会话，不改变其他会话或新会话默认值。');
        apply.disabled = preset.node.value === data.currentValue || risky && !acknowledge.checked;
      };
      preset.node.onchange = () => {acknowledge.checked = false; update();};
      acknowledge.onchange = update; update();
      body.append(preset.wrap, description, gate, apply);
      return;
    }
    const current = data.current || {}, active = current.activePermissionProfile;
    const id = (typeof active === 'object' ? active?.id : active) || current.permissions || (current?.fullAccess ? ':danger-full-access' : ':workspace');
    const labels = {':read-only': '只读', ':workspace': '项目读写', ':danger-full-access': '完全访问'};
    const profile = select('访问权限', data.profiles.map(row => ({value: row.id, label: labels[row.id] || row.description || row.id, disabled: !row.allowed})), id);
    const policy = select('执行审批', [{value: 'on-request', label: '按需询问'}, {value: 'untrusted', label: '不可信命令询问'},
      {value: 'on-failure', label: '失败时询问'}, {value: 'never', label: '不询问'}], typeof current.approvalPolicy === 'string' ? current.approvalPolicy : 'on-request');
    const reviewer = select('审批人', [{value: 'user', label: '用户'}, {value: 'auto_review', label: '原生自动审阅'}, {value: 'guardian_subagent', label: '原生 Guardian（旧配置）'}], current.approvalsReviewer || 'user');
    body.append(profile.wrap, policy.wrap, reviewer.wrap, element('p', '沿用原 App 权限配置；设置用于后续轮次，不改变正在运行的任务。'),
      button('应用权限', () => mutate('', {profileId: profile.node.value, approvalPolicy: policy.node.value, approvalsReviewer: reviewer.node.value}, 'update_permissions')));
  }
  function tools(data) {
    body.append(element('p', data.notice));
    if (data.applicationId === 'deepseek') {
      if (data.skillsAvailable !== false) {
        body.append(element('h3', '技能'));
        if (!data.skills.length) body.append(element('p', '当前会话没有可用技能。'));
        for (const row of data.skills) {
          const card = element('div', undefined, 'native-tool-row');
          card.dataset.harnessSkill = row.name;
          card.append(element('strong', row.name));
          if (row.description) {
            const desc = element('details'); desc.append(element('summary', '说明'), element('p', row.description)); card.append(desc);
          }
          card.append(button('加入输入框', () => {
            if (!valid()) return;
            // Harness uses a native leading slash command, not Codex's $skill syntax.
            $('message').value = '/' + row.name + ' ' + $('message').value;
            $('message').dispatchEvent(new Event('input', {bubbles: true}));
            dialog.close(); $('message').focus();
          }));
          body.append(card);
        }
      }
      for (const [title, rows, kind] of [['插件组合', data.bundles, 'bundle'], ['原生插件', data.plugins, 'plugin']]) {
        const group = element('details'); group.open = kind === 'bundle';
        group.append(element('summary', `${title}（${rows.length}）`));
        for (const row of rows) {
          const card = element('div', undefined, 'native-tool-row');
          card.dataset.harnessPlugin = row.id; card.dataset.harnessKind = kind;
          if (row.canToggle) {
            const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.checked = row.enabled;
            const label = element('label'); label.append(checkbox, document.createTextNode(' ' + row.label));
            checkbox.onchange = async () => {
              const enabled = checkbox.checked;
              if (pending || !confirm(`这会${enabled ? '启用' : '停用'}目标电脑 Harness 的“${row.label}”，影响该电脑所有会话；原 App 要求重启时将另行提示。确认修改？`)) {
                checkbox.checked = row.enabled; return;
              }
              checkbox.disabled = true;
              await mutate('harness_' + kind + '_enabled', {id: row.id, enabled, expectedEnabled: row.enabled, confirmGlobal: true});
              // On failure keep the last verified value; a fresh read owns successful state.
              if (checkbox.isConnected) { checkbox.checked = row.enabled; checkbox.disabled = false; }
            };
            card.append(label);
          } else {
            card.append(element('strong', row.label), element('small', row.installed === false ? '未安装 · 不提供开关' :
              `原生保护 · ${row.enabled ? '已启用' : '未启用'} · 不提供开关`));
          }
          if (row.description) {
            const desc = element('details'); desc.append(element('summary', '说明'), element('p', row.description)); card.append(desc);
          }
          group.append(card);
        }
        body.append(group);
      }
      return;
    }
    for (const [title, rows, kind] of [['技能', data.skills, 'skill'], ['已配置插件', data.plugins, 'plugin'], ['已配置 MCP', data.servers, 'mcp']]) {
      body.append(element('h3', title));
      if (!rows.length) body.append(element('p', '没有配置'));
      for (const row of rows) {
        const card = element('div', undefined, 'native-tool-row');
        const name = row.name || row.id;
        const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.checked = row.enabled !== false;
        const label = element('label'); label.append(checkbox, document.createTextNode(' ' + name));
        checkbox.onchange = async () => {
          const enabled = checkbox.checked; checkbox.disabled = true;
          await mutate(kind + '_enabled', kind === 'skill' ? {path: row.path, enabled} : {name, enabled, expectedEnabled: row.enabled !== false});
          checkbox.disabled = false;
        };
        card.append(label);
        if (row.description) { const desc = element('details'); desc.append(element('summary', '说明'), element('p', row.description)); card.append(desc); }
        if (kind === 'skill' && row.enabled !== false) card.append(button('加入输入框', () => {
          if (!valid()) return;
          $('message').value += ($('message').value ? '\n' : '') + '$' + row.name + ' ';
          $('message').dispatchEvent(new Event('input', {bubbles: true}));
          dialog.close(); $('message').focus();
        }));
        body.append(card);
      }
    }
  }
  function metadata(data) {
    const info = data.metadata || {};
    if (data.applicationId === 'deepseek') {
      const number = value => value == null ? uiText('原 App 暂未提供') : Number(value).toLocaleString();
      const seconds = value => value == null ? uiText('原 App 暂未提供') : uiText((value / 1000).toFixed(2) + ' 秒');
      const group = (title, rows) => {
        const list = element('dl', undefined, 'native-metadata');
        for (const [label, value] of rows) list.append(element('dt', label), element('dd', value));
        body.append(element('h3', title), list);
      };
      const usage = info.nativeUsage || {}, stats = info.stats || {}, pressure = info.contextPressure || {}, breakdown = info.contextBreakdown || {};
      group('原生对话累计 Token', [['未缓存输入', number(usage.uncachedInputTokens)], ['输出', number(usage.outputTokens)],
        ['缓存读取', number(usage.cacheReadTokens)], ['缓存写入', number(usage.cacheWriteTokens)]]);
      group('原生上下文状态', [['上下文窗口', number(pressure.contextWindow)], ['最近原生输入压力采样', number(pressure.pressureTokens)],
        ['下一次请求估计', number(pressure.projectedTokens)]]);
      group('上下文组成估计', [['系统提示', number(breakdown.systemTokens)], ['工具定义', number(breakdown.toolsTokens)], ['消息', number(breakdown.messageTokens)]]);
      group('原生累计统计', [['轮次', number(stats.turns)], ['模型步骤', number(stats.steps)], ['模型请求用时', seconds(stats.llmMs)],
        ['工具执行用时', seconds(stats.toolMs)], ['平均首 Token 等待', seconds(stats.ttftSteps > 0 && stats.ttftMs != null ? stats.ttftMs / stats.ttftSteps : null)],
        ['解码用时', seconds(stats.decodeMs)], ['解码 Token', number(stats.decodeTokens)]]);
      body.append(element('p', '沿用 Harness 原生投影：累计 Token 与上下文估计分开显示；模型/工具用时不是任务总耗时，也不是账号剩余额度。仅打开或手动刷新时读取。'));
      return;
    }
    const card = element('dl', undefined, 'native-metadata');
    const add = (label, value) => card.append(element('dt', label), element('dd', value == null ? '原 App 暂未提供' : String(value)));
    const labels = {inputTokens: '输入', cachedInputTokens: '缓存输入', outputTokens: '输出', reasoningOutputTokens: '推理输出', totalTokens: '总计'};
    for (const key of ['last', ...(info.turn ? ['turn'] : []), 'total']) {
      body.append(element('h3', key === 'last' ? '最近一次模型调用' : key === 'turn' ? '当前/最近轮次累计' : '对话累计'));
      const list = element('dl', undefined, 'native-metadata');
      for (const [name, label] of Object.entries(labels)) list.append(element('dt', label), element('dd', info[key]?.[name]?.toLocaleString() ?? '原 App 暂未提供'));
      body.append(list);
    }
    add('上下文窗口', info.modelContextWindow);
    if (info.modelContextWindow && info.last?.inputTokens != null) add('最近输入占窗口', (info.last.inputTokens / info.modelContextWindow * 100).toFixed(1) + '%');
    const last = info.turns?.at(-1);
    add('最近轮次用时', last?.durationMs == null ? (last?.completedAtMs && last?.startedAtMs ? ((last.completedAtMs - last.startedAtMs) / 1000).toFixed(1) + ' 秒' : null) : (last.durationMs / 1000).toFixed(1) + ' 秒');
    body.append(card, element('p', '显示原生计数，不按文字估算。Token 计数不是模型账号剩余额度。'));
  }
  function review(data, options = {}) {
    const view = select('范围', [{value: 'unstaged', label: '未暂存'}, {value: 'staged', label: '已暂存'}, {value: 'branch', label: '对比分支'}], options.view || 'unstaged');
    const base = input('基准分支', options.base || 'HEAD');
    const actions = element('div', undefined, 'native-actions');
    actions.append(button('读取差异', () => load('review', {view: view.node.value, base: base.node.value})),
      button('在原 App 打开审阅', () => mutate('review_open_native', {view: view.node.value, base: base.node.value})));
    body.append(view.wrap, base.wrap, actions);
    const diff = element('div', undefined, 'native-diff');
    let path = '', number = 0;
    for (const line of (data.diff || '').split('\n')) {
      const file = line.match(/^\+\+\+ b\/(.+)$/), hunk = line.match(/^@@ .* \+(\d+)/);
      if (file) path = file[1];
      if (hunk) number = Number(hunk[1]);
      const row = element('div', line, line.startsWith('+') ? 'diff-add' : line.startsWith('-') ? 'diff-remove' : '');
      if (path && number && !line.startsWith('-') && !/^[-+]{3}|^@@|^diff |^index /.test(line)) {
        const currentLine = number;
        row.dataset.line = String(currentLine);
        if (!line.startsWith('-')) number++;
        row.tabIndex = 0; row.title = `批注 ${path}:${currentLine}`;
        const annotate = () => annotation(path, currentLine, line.slice(1), data.cwd);
        row.onclick = annotate;
        row.onkeydown = event => { if (event.key === 'Enter') annotate(); };
      }
      diff.append(row);
    }
    body.append(diff);
    if (!data.diff) body.append(element('p', '此范围没有已跟踪文件差异。新建未跟踪文件可从文件管理器打开并批注。'));
    if (data.truncated) body.append(element('p', '差异超过 512 KB，已截断。请选择文件查看或在原 App 审阅。'));
  }
  function annotation(path, line, quote = '', cwd = '') {
    if (!dialog.open && selected) target = {...selected};
    if (!valid()) return;
    const message = prompt(`批注 ${path}:${line}`, '');
    if (!message?.trim()) return;
    const absolute = /^[A-Za-z]:[\\/]|^\//.test(path) ? path : cwd.replace(/[\\/]$/, '') + '/' + path;
    const content = `请处理文件批注：${absolute}:${line}\n${message.trim()}${quote ? '\n所选内容：\n' + quote.slice(0, 4000) : ''}`;
    $('message').value += ($('message').value ? '\n\n' : '') + content;
    $('message').dispatchEvent(new Event('input', {bubbles: true}));
    if (dialog.open) dialog.close(); $('filePreviewDialog')?.close(); $('message').focus();
  }
  async function load(name, options = {}) {
    if (!supportedSections().includes(name)) return;
    section = name;
    const version = ++epoch;
    readController?.abort();
    const controller = new AbortController();
    readController = controller;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, readTimeoutMs);
    for (const tab of dialog.querySelectorAll('.native-tabs button')) tab.setAttribute('aria-selected', String(tab.dataset.section === name));
    body.replaceChildren(); status.textContent = '正在读取目标 App…';
    body.append(button('重新读取', () => void load(name, options)));
    body.setAttribute('aria-busy', 'true');
    try {
      const data = await command('native_read', {operation: name, ...options}, {signal: controller.signal});
      if (version !== epoch || !dialog.open) return;
      if (!valid()) {
        status.textContent = '当前对话已切换，请关闭后重新打开设置';
        return;
      }
      status.textContent = '';
      body.replaceChildren();
      const refresh = button('刷新', () => load(name, options)); body.append(refresh);
      ({tasks, permissions, tools, review, metadata})[name](data, options);
    } catch (error) {
      if (version !== epoch || !dialog.open) return;
      status.textContent = timedOut ? '读取超时（15 秒），请点击重新读取；不会自动重试。' : error.message;
    } finally {
      clearTimeout(timer);
      if (readController === controller) readController = null;
      if (version === epoch) body.removeAttribute('aria-busy');
    }
  }
  function open(name = 'tasks') {
    if (!selected || !currentHost()?.online) {
      $('sendResult').textContent = '先打开在线电脑上的对话'; return;
    }
    target = {...selected};
    const allowed = supportedSections();
    if (!allowed.length) return;
    if (!allowed.includes(name)) name = allowed[0];
    section = name;
    syncCapabilities();
    dialog.querySelector('.native-header strong').textContent = `${APPLICATION_LABELS[applicationId(currentHost())] || applicationId(currentHost())} · ${currentHost().label} · 原生设置`;
    if (!dialog.open) dialog.showModal();
    void load(name);
  }
  for (const [key, name] of Object.entries(names)) {
    const tab = button(name, () => void load(key)); tab.dataset.section = key; tab.setAttribute('role', 'tab');
    dialog.querySelector('.native-tabs').append(tab);
  }
  dialog.querySelector('.native-header button').onclick = () => dialog.close();
  dialog.addEventListener('close', () => {
    // The queued close event can arrive after a quick reopen; do not cancel it.
    if (dialog.open) return;
    epoch++; readController?.abort(); readController = null;
    body.replaceChildren(); body.removeAttribute('aria-busy'); status.textContent = '';
  });
  $('nativeSettingsToggle').onclick = () => open();
  $('filePreviewAnnotation').onclick = () => {
    const tab = activeFilePreview();
    if (!tab?.sourcePath || selected?.host !== tab.hostId || !selected) return;
    const selection = window.getSelection();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    const pre = range?.startContainer?.parentElement?.closest('pre');
    if (!range || !pre || !tab.pane.contains(pre) || !pre.contains(range.endContainer)) {
      $('filePreviewStatus').textContent = '请先在源码中选择需要批注的文字。'; return;
    }
    const before = document.createRange(); before.selectNodeContents(pre);
    before.setEnd(range.startContainer, range.startOffset);
    const line = before.toString().split('\n').length;
    annotation(tab.sourcePath, line, selection.toString());
  };
  const settingsActions = element('div', undefined, 'native-actions');
  for (const name of ['permissions', 'tools', 'metadata']) {
    const action = button(names[name], () => open(name)); action.dataset.nativeSection = name; settingsActions.append(action);
  }
  $('modelControls').append(settingsActions);
  new MutationObserver(() => {
    if (!$('workspace').hidden) return;
    epoch++; target = null; readController?.abort(); readController = null;
    if (dialog.open) dialog.close();
    body.replaceChildren(); body.removeAttribute('aria-busy'); status.textContent = '';
  }).observe($('workspace'), {attributes: true, attributeFilter: ['hidden']});
  window.NativePanel = {open, annotation, syncCapabilities};
  syncCapabilities();
})();
