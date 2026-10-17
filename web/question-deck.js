// One stable question surface, independent of the scrolling execution timeline.
// Paging never sends an answer or takes keyboard focus from the current editor.
window.CRBQuestionDeck = (() => {
  function update(root) {
    const all = [...root.querySelectorAll('.question-deck-body fieldset[data-question-key]')];
    const state = root.questionVisibilityState ||= {closed: false, skipped: new Set()};
    const keys = new Set(all.map(field => field.dataset.questionKey));
    for (const key of state.skipped) if (!keys.has(key)) state.skipped.delete(key);
    const fields = all.filter(field => !state.skipped.has(field.dataset.questionKey));
    const index = Math.max(0, fields.findIndex(field => field.dataset.questionKey === root.questionPageKey));
    const active = fields[index];
    root.hidden = !all.length;
    if (!all.length) {root.questionPageKey = '';state.closed = false;return;}
    const collapsed = state.closed || !active;
    root.classList.toggle('question-deck-collapsed', collapsed);
    root.querySelector('.question-deck-body').hidden = collapsed;
    root.querySelector('.question-deck-navigation').hidden = collapsed;
    root.querySelector('.question-deck-close').hidden = collapsed;
    root.querySelector('.question-deck-skip').hidden = collapsed;
    root.querySelector('.question-deck-reopen').hidden = !collapsed;
    root.querySelector('.question-deck-title').textContent = collapsed ? '有待回答的问题' : '需要你的回答';
    if (collapsed) return;
    root.questionPageKey = active.dataset.questionKey;
    for (const field of all) field.hidden = field !== active;
    for (const card of root.querySelector('.question-deck-body').children) {
      card.hidden = !card.contains(active);
      const ownFields = fields.filter(field => card.contains(field));
      for (const button of card.querySelectorAll('.question-page-submit')) button.hidden = active !== ownFields.at(-1);
    }
    root.querySelector('.question-page-counter').textContent = `${index + 1} / ${fields.length}`;
    root.querySelector('.question-page-previous').disabled = index === 0;
    root.querySelector('.question-page-next').disabled = index === fields.length - 1;
    root.onQuestionPageChange?.(root.questionPageKey);
  }
  function body(root) {
    let content = root.querySelector('.question-deck-body');
    if (content) return content;
    const header = document.createElement('div');header.className = 'question-deck-header';
    const title = document.createElement('strong');title.className = 'question-deck-title';title.textContent = '需要你的回答';
    const navigation = document.createElement('nav');navigation.className = 'question-deck-navigation';navigation.setAttribute('aria-label', '切换问题');
    for (const [direction, label, step] of [['previous', '上一题', -1], ['next', '下一题', 1]]) {
      const button = document.createElement('button');button.type = 'button';button.className = `secondary question-page-${direction}`;button.textContent = label;
      button.onclick = () => {
        const fields = [...content.querySelectorAll('fieldset[data-question-key]')].filter(field =>
          !root.questionVisibilityState?.skipped.has(field.dataset.questionKey));
        const index = fields.findIndex(field => field.dataset.questionKey === root.questionPageKey);
        const target = fields[index + step];
        if (target) {root.questionPageKey = target.dataset.questionKey;update(root);content.scrollTop = 0;}
      };
      if (step === 1) {
        const counter = document.createElement('span');counter.className = 'question-page-counter';
        counter.setAttribute('aria-live', 'polite');counter.setAttribute('data-i18n-ignore', '');navigation.append(counter);
      }
      navigation.append(button);
    }
    const actions = document.createElement('div');actions.className = 'question-deck-actions';
    for (const [name, label] of [['skip', '暂时跳过'], ['close', '×'], ['reopen', '重新打开问题']]) {
      const button = document.createElement('button');button.type = 'button';
      button.className = `secondary question-deck-${name}`;button.textContent = label;
      if (name === 'close') button.setAttribute('aria-label', '关闭问题面板');
      button.title = '只收起或暂存，不提交答案，也不停止任务';
      button.onclick = () => {
        const state = root.questionVisibilityState ||= {closed: false, skipped: new Set()};
        if (name === 'close') state.closed = true;
        else if (name === 'skip') {state.skipped.add(root.questionPageKey);root.questionPageKey = '';}
        else {state.closed = false;state.skipped.clear();}
        update(root);
      };
      actions.append(button);
    }
    header.append(title, navigation, actions);content = document.createElement('div');content.className = 'question-deck-body';
    root.append(header, content);return content;
  }
  function reveal(field) {
    const root = field.closest('#questionDock');
    if (root) {
      const state = root.questionVisibilityState;
      if (state) {state.closed = false;state.skipped.delete(field.dataset.questionKey);}
      root.questionPageKey = field.dataset.questionKey;update(root);
    }
  }
  return {body, update, reveal};
})();
