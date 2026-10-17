// Pending Desktop requests share the same host/thread transport as normal commands.
function requestDetail(parent, label, value, code = false) {
  if (value === undefined || value === null || value === '') return;
  const row = document.createElement('div');
  row.className = 'request-detail';
  const name = document.createElement('strong');
  name.textContent = label;
  const body = document.createElement(code ? 'pre' : 'span');
  body.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  row.append(name, body);
  parent.append(row);
}

function requestButton(parent, label, handler, secondary = false) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.dataset.requestAction = 'true';
  if (secondary) button.className = 'secondary';
  button.onclick = handler;
  parent.append(button);
  return button;
}

async function answerPendingRequest(request, response, card) {
  if (!selected || !current?.owned || !current?.online) return;
  const target = {...selected};
  const key = `${target.host}/${target.thread}/${request.id}`;
  if (requestBusy.has(key) || card.dataset.submitted === 'true') return;
  requestBusy.add(key);
  const feedback = card.querySelector('.request-feedback');
  card.querySelectorAll('[data-request-action]').forEach(button => button.disabled = true);
  feedback.className = 'request-feedback';
  feedback.textContent = '正在提交到原桌面会话…';
  try {
    await issueCommand('resolve_request', {...target, payload: {
      requestId: request.id, method: request.method, turnId: request.turnId, response
    }});
    feedback.textContent = '✓ 已提交，等待桌面端同步';
    card.dataset.submitted = 'true';
    if (selected?.host === target.host && selected?.thread === target.thread) {
      await refreshThread();
    }
  } catch (error) {
    feedback.className = 'request-feedback error';
    feedback.textContent = error.message;
  } finally {
    requestBusy.delete(key);
    if (card.isConnected && card.dataset.submitted !== 'true') {
      card.querySelectorAll('[data-request-action]').forEach(button => button.disabled = false);
    }
    if (current && selected?.host === target.host && selected?.thread === target.thread) renderQuestionDock(current);
  }
}

function renderApprovalRequest(card, request) {
  const command = request.method === 'item/commandExecution/requestApproval';
  if (command) {
    if (request.networkApprovalContext) {
      requestDetail(card, '网络访问目标', request.networkApprovalContext, true);
    }
    requestDetail(card, '命令', request.command || request.item?.text, true);
    requestDetail(card, '运行目录', request.cwd);
    requestDetail(card, '附加权限', request.additionalPermissions, true);
    requestDetail(card, '操作详情', request.commandActions, true);
  } else {
    requestDetail(card, '拟修改文件', request.item?.detail || request.item?.text, true);
    requestDetail(card, '授权目录', request.grantRoot);
  }
  const actions = document.createElement('div');
  actions.className = 'request-actions';
  const choices = Array.isArray(request.availableDecisions) ?
    request.availableDecisions.map(value => typeof value === 'string' ? value :
      value?.decision || value?.type || Object.keys(value || {})[0]) : null;
  const offered = name => !choices?.length || choices.includes(name);
  for (const [decision, label, secondary] of [
    ['accept', '允许一次', false], ['acceptForSession', '本会话允许', true],
    ['decline', '拒绝', true], ['cancel', '取消', true]
  ]) {
    if (offered(decision)) requestButton(actions, label,
      () => answerPendingRequest(request, {decision}, card), secondary);
  }
  if (command && request.proposedExecpolicyAmendment &&
      offered('acceptWithExecpolicyAmendment')) {
    requestDetail(card, '建议加入的命令规则', request.proposedExecpolicyAmendment, true);
    const proposed = request.proposedExecpolicyAmendment;
    const amendment = Array.isArray(proposed) ? proposed : proposed.execpolicy_amendment;
    if (Array.isArray(amendment)) requestButton(actions, '允许并加入该规则',
      () => answerPendingRequest(request, {decision: {
        acceptWithExecpolicyAmendment: {execpolicy_amendment: amendment}
      }}, card), true);
  }
  card.append(actions);
}

function renderPermissionRequest(card, request) {
  requestDetail(card, '请求的权限', request.permissions, true);
  requestDetail(card, '工作目录', request.cwd);
  const scope = document.createElement('select');
  scope.setAttribute('aria-label', '授权范围');
  for (const [value, label] of [['turn', '仅本轮'], ['session', '当前会话']]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    scope.append(option);
  }
  const scopeRow = document.createElement('label');
  scopeRow.className = 'request-field';
  scopeRow.append(document.createTextNode('授权范围'), scope);
  card.append(scopeRow);
  const actions = document.createElement('div');
  actions.className = 'request-actions';
  const empty = Array.isArray(request.permissions) ? [] : {};
  requestButton(actions, '批准所请求权限', () => answerPendingRequest(request,
    {permissions: request.permissions, scope: scope.value}, card));
  requestButton(actions, '拒绝', () => answerPendingRequest(request,
    {permissions: empty, scope: 'turn'}, card), true);
  card.append(actions);
  const custom = document.createElement('details');
  custom.className = 'request-custom';
  const summary = document.createElement('summary');
  summary.textContent = '自定义授权范围';
  const hint = document.createElement('p');
  hint.textContent = '编辑 JSON，仅保留要授予的权限；不能新增原请求以外的权限。';
  const editor = document.createElement('textarea');
  editor.setAttribute('aria-label', '自定义授权 JSON');
  editor.value = JSON.stringify(request.permissions, null, 2);
  editor.rows = 5;
  requestButton(custom, '提交自定义授权', () => {
    try {
      answerPendingRequest(request, {permissions: JSON.parse(editor.value),
        scope: scope.value}, card);
    } catch (_) {
      const feedback = card.querySelector('.request-feedback');
      feedback.className = 'request-feedback error';
      feedback.textContent = '自定义授权不是有效 JSON';
    }
  }, true);
  custom.prepend(summary, hint, editor);
  card.append(custom);
}

function renderUserInputRequest(card, request) {
  const questions = Array.isArray(request.questions) ? request.questions : [];
  if (Number.isFinite(request.autoResolutionMs) && request.autoResolutionMs > 0) {
    requestDetail(card, '自动处理', `桌面端可能在 ${Math.ceil(
      request.autoResolutionMs / 1000)} 秒后自动处理此问题`);
  }
  const form = document.createElement('form');
  form.className = 'request-form';
  const fields = [];
  for (const [index, question] of questions.entries()) {
    if (!question || !question.id) continue;
    const field = document.createElement('fieldset');
    field.dataset.questionKey = JSON.stringify(['native', request.turnId || '', request.id, question.id]);
    const legend = document.createElement('legend');
    legend.setAttribute('data-i18n-ignore', '');
    legend.textContent = `${question.header || `问题 ${index + 1}`} · ${question.question || ''}`;
    field.append(legend);
    const options = Array.isArray(question.options) ? question.options : [];
    const multi = question.isMultiSelect === true || question.allowMultiple === true;
    for (const option of options) {
      const label = document.createElement('label');
      label.className = 'request-option';
      const input = document.createElement('input');
      input.type = multi ? 'checkbox' : 'radio';
      input.name = `request-${request.id}-${question.id}`;
      input.value = option.label || '';
      const text = document.createElement('span');
      text.setAttribute('data-i18n-ignore', '');
      text.textContent = option.description ?
        `${option.label} · ${option.description}` : option.label;
      label.append(input, text);
      field.append(label);
    }
    const custom = document.createElement('input');
    custom.type = 'text';
    custom.maxLength = 4000;
    custom.placeholder = '自定义回答（可不选上面的选项）';
    custom.setAttribute('aria-label', `${question.header || question.question}的自定义回答`);
    field.append(custom);
    fields.push({question, field, custom, multi});
    form.append(field);
  }
  const actions = document.createElement('div');
  actions.className = 'request-actions';
  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.className = 'question-page-submit';
  submit.textContent = '提交回答';
  submit.dataset.requestAction = 'true';
  actions.append(submit);
  requestButton(actions, '跳过', () => answerPendingRequest(request,
    {answers: {}}, card), true);
  form.append(actions);
  form.onsubmit = event => {
    event.preventDefault();
    const answers = {};
    for (const {question, field, custom, multi} of fields) {
      const chosen = [...field.querySelectorAll('input:checked')].map(input => input.value);
      const typed = custom.value.trim();
      const values = multi ? [...chosen, ...(typed ? [typed] : [])] :
        (typed ? [typed] : chosen.slice(0, 1));
      if (!values.length) {
        window.CRBQuestionDeck.reveal(field);
        const feedback = card.querySelector('.request-feedback');
        feedback.className = 'request-feedback error';
        feedback.textContent = `请回答「${question.header || question.question}」或选择跳过`;
        return;
      }
      answers[question.id] = {answers: values};
    }
    answerPendingRequest(request, {answers}, card);
  };
  card.append(form);
}

function renderMcpRequest(card, request) {
  requestDetail(card, '服务', request.serverName);
  if (request.desktopOnly) return;
  if (request.mode === 'url') {
    try {
      const url = new URL(request.url);
      if (!['https:', 'http:'].includes(url.protocol)) throw new Error('unsupported');
      const link = document.createElement('a');
      link.href = url.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = `打开授权页面：${url.host}`;
      card.append(link);
    } catch (_) {
      requestDetail(card, '授权地址', request.url);
    }
  }
  const schema = request.requestedSchema || {};
  if (request.mode === 'form' || request.mode === 'openai/form') {
    const form = document.createElement('form');
    form.className = 'request-form';
    const fields = [];
    for (const [name, property] of Object.entries(schema.properties || {})) {
      if (!property || typeof property !== 'object') continue;
      const label = document.createElement('label');
      label.className = 'request-field';
      label.append(document.createTextNode(`${property.title || name}${
        (schema.required || []).includes(name) ? ' *' : ''}`));
      let input;
      if (Array.isArray(property.enum)) {
        input = document.createElement('select');
        for (const choice of property.enum) {
          const option = document.createElement('option');
          option.value = String(choice);
          option.textContent = String(choice);
          input.append(option);
        }
      } else if (property.type === 'boolean') {
        input = document.createElement('input');
        input.type = 'checkbox';
      } else if (property.type === 'number' || property.type === 'integer') {
        input = document.createElement('input');
        input.type = 'number';
        if (property.type === 'integer') input.step = '1';
      } else if (property.type === 'object' || property.type === 'array') {
        input = document.createElement('textarea');
        input.rows = 3;
        input.placeholder = 'JSON';
      } else {
        input = document.createElement('input');
        input.type = property.format === 'password' ? 'password' : 'text';
      }
      if (property.description) input.title = property.description;
      input.setAttribute('aria-label', property.title || name);
      label.append(input);
      form.append(label);
      fields.push({name, property, input});
    }
    const advanced = document.createElement('details');
    advanced.className = 'request-custom';
    const summary = document.createElement('summary');
    summary.textContent = '高级：自定义 JSON 表单';
    const editor = document.createElement('textarea');
    editor.rows = 5;
    editor.placeholder = '填写完整 JSON 对象时，将以此内容提交';
    editor.setAttribute('aria-label', '自定义表单 JSON');
    advanced.append(summary, editor);
    form.append(advanced);
    const actions = document.createElement('div');
    actions.className = 'request-actions';
    const submit = document.createElement('button');
    submit.type = 'submit';
    submit.dataset.requestAction = 'true';
    submit.textContent = '提交表单';
    actions.append(submit);
    requestButton(actions, '拒绝', () => answerPendingRequest(request,
      {action: 'decline', content: null}, card), true);
    requestButton(actions, '取消', () => answerPendingRequest(request,
      {action: 'cancel', content: null}, card), true);
    form.append(actions);
    form.onsubmit = event => {
      event.preventDefault();
      try {
        let content = {};
        if (editor.value.trim()) {
          content = JSON.parse(editor.value);
        } else {
          for (const {name, property, input} of fields) {
            const raw = input.type === 'checkbox' ? input.checked : input.value;
            if (raw === '' && !(schema.required || []).includes(name)) continue;
            if (raw === '' && (schema.required || []).includes(name)) {
              throw new Error(`请填写「${property.title || name}」`);
            }
            content[name] = (property.type === 'object' || property.type === 'array') ?
              JSON.parse(raw) : property.type === 'boolean' ? Boolean(raw) :
              ['number', 'integer'].includes(property.type) ? Number(raw) : raw;
          }
        }
        if (!content || typeof content !== 'object' || Array.isArray(content)) {
          throw new Error('表单内容需要 JSON 对象');
        }
        answerPendingRequest(request, {action: 'accept', content}, card);
      } catch (error) {
        const feedback = card.querySelector('.request-feedback');
        feedback.className = 'request-feedback error';
        feedback.textContent = error.message || '表单 JSON 格式错误';
      }
    };
    card.append(form);
  } else if (request.mode === 'url') {
    const actions = document.createElement('div');
    actions.className = 'request-actions';
    requestButton(actions, '已完成外部授权，继续', () => answerPendingRequest(request,
      {action: 'accept', content: null}, card));
    requestButton(actions, '拒绝', () => answerPendingRequest(request,
      {action: 'decline', content: null}, card), true);
    requestButton(actions, '取消', () => answerPendingRequest(request,
      {action: 'cancel', content: null}, card), true);
    card.append(actions);
  }
}

function renderPendingRequests(requests, count, hostLabel, live) {
  if (deferQuestionInteractionRender('requests', () => renderPendingRequests(
      current?.requests || [], current?.pendingRequests || 0,
      currentHost()?.label || hostLabel,
      Boolean(current?.owned && current?.online && !threadPreviewOnly)))) return;
  const sourceRequests = Array.isArray(requests) ? requests : [];
  const values = sourceRequests.filter(request => request.method !== 'item/tool/requestUserInput');
  count = Math.max(values.length, count - (sourceRequests.length - values.length));
  const signature = JSON.stringify([selected?.host, selected?.thread, hostLabel, live,
    count, values]);
  if (signature === requestRenderSignature) return;
  requestRenderSignature = signature;
  const panel = $('notice');
  panel.hidden = count === 0;
  if (!count) {panel.replaceChildren(); return;}
  const heading = panel.querySelector(':scope > h2') || document.createElement('h2');
  heading.textContent = `◆ 待确认 · ${count}`;
  const children = [heading];
  const previous = new Map([...panel.querySelectorAll(':scope > .request-card')]
    .map(card => [card.dataset.requestKey, card]));
  if (!values.length) {
    const hint = document.createElement('p');
    hint.textContent = `${hostLabel} 的代理尚未同步确认详情，请更新代理或在桌面 App 处理。`;
    children.push(hint);
    reconcileConnectedChildren(panel, children);
    return;
  }
  for (const request of values) {
    const key = JSON.stringify([selected?.host, selected?.thread, request.id, request.method]);
    const signature = JSON.stringify([request, live]);
    const controls = {...request};
    for (const name of ['reason', 'message', 'autoResolutionMs']) delete controls[name];
    const controlSignature = JSON.stringify([controls, live]);
    const prior = previous.get(key);
    if (prior?.requestSignature === signature) {
      // Another request or its count can update while this editor is composing.
      // Reuse the connected card; do not rebuild or refocus its inputs.
      children.push(prior);
      continue;
    }
    const card = document.createElement('article');
    card.className = 'request-card';
    card.dataset.requestKey = key;
    card.requestSignature = signature;
    card.controlSignature = controlSignature;
    const title = document.createElement('h3');
    title.textContent = ({
      'item/commandExecution/requestApproval': '运行命令',
      'item/fileChange/requestApproval': '修改文件',
      'item/permissions/requestApproval': '扩展访问权限',
      'item/tool/requestUserInput': '需要你的回答',
      'mcpServer/elicitation/request': '外部服务请求',
      'item/plan/requestImplementation': '计划待执行'
    })[request.method] || '其他桌面请求';
    card.append(title);
    requestDetail(card, '原因', request.reason || request.message);
    if (request.method === 'item/plan/requestImplementation') {
      requestDetail(card, '计划', request.plan, true);
    }
    if (!live || request.desktopOnly) {
      const hint = document.createElement('p');
      hint.className = 'request-feedback';
      hint.textContent = request.method === 'item/plan/requestImplementation' ?
        '这是计划执行提示，可在下方输入框回复。' :
        request.mode === 'openai/userVerification' ?
        '此身份验证含桌面端专用挑战，请在原桌面 App 完成。' :
        '该请求尚不能在此处理，请在原桌面 App 查看。';
      card.append(hint);
      if (live && request.method === 'item/plan/requestImplementation') {
        requestButton(card, '填入“按计划执行”', () => {
          $('message').value = '按上述计划执行';
          $('message').focus();
        }, true);
      }
    } else {
      if (request.method === 'item/commandExecution/requestApproval' ||
          request.method === 'item/fileChange/requestApproval') {
        renderApprovalRequest(card, request);
      } else if (request.method === 'item/permissions/requestApproval') {
        renderPermissionRequest(card, request);
      } else if (request.method === 'item/tool/requestUserInput') {
        renderUserInputRequest(card, request);
      } else if (request.method === 'mcpServer/elicitation/request') {
        renderMcpRequest(card, request);
      }
      const feedback = document.createElement('p');
      feedback.className = 'request-feedback';
      feedback.setAttribute('role', 'status');
      card.append(feedback);
    }
    if (prior?.controlSignature === controlSignature) {
      // Descriptive/countdown text can change without changing a question or
      // its permissions. Keep its editors/actions continuously connected.
      const interactive = node => node.nodeType === Node.ELEMENT_NODE &&
        (node.matches('.request-feedback, input, textarea, select, button') ||
         node.querySelector('input, textarea, select, button'));
      const keep = [...prior.childNodes].filter(interactive);
      const updated = [...card.childNodes].map(node => interactive(node) ? keep.shift() || node : node);
      reconcileConnectedChildren(prior, updated);
      prior.requestSignature = signature;
      children.push(prior);
    } else children.push(card);
  }
  reconcileConnectedChildren(panel, children);
}
