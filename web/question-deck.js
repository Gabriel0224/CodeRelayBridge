// One stable question surface, independent of the scrolling execution timeline.
// Paging never sends an answer or takes keyboard focus from the current editor.
window.CRBQuestionDeck = (() => {
  function update(root) {
    const fields = [...root.querySelectorAll('.question-deck-body fieldset[data-question-key]')];
    const index = Math.max(0, fields.findIndex(field => field.dataset.questionKey === root.questionPageKey));
    const active = fields[index];
    root.hidden = !active;
    if (!active) {root.questionPageKey = ''; return;}
    root.questionPageKey = active.dataset.questionKey;
    for (const field of fields) field.hidden = field !== active;
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
    const title = document.createElement('strong');title.textContent = '需要你的回答';
    const navigation = document.createElement('nav');navigation.setAttribute('aria-label', '切换问题');
    for (const [direction, label, step] of [['previous', '上一题', -1], ['next', '下一题', 1]]) {
      const button = document.createElement('button');button.type = 'button';button.className = `secondary question-page-${direction}`;button.textContent = label;
      button.onclick = () => {
        const fields = [...content.querySelectorAll('fieldset[data-question-key]')];
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
    header.append(title, navigation);content = document.createElement('div');content.className = 'question-deck-body';
    root.append(header, content);return content;
  }
  function reveal(field) {
    const root = field.closest('#questionDock');
    if (root) {root.questionPageKey = field.dataset.questionKey;update(root);}
  }
  return {body, update, reveal};
})();
