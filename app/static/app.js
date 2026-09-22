'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  notes: [], allNotes: [], tags: [], categories: [], categoryTree: [],
  activeNote: null, activeFilter: 'all', activeTag: null, activeCategoryId: null,
  expandedCategories: new Set(JSON.parse(localStorage.getItem('zettel-expanded-categories') || '[]')),
  editorMode: localStorage.getItem('zettel-editor-mode') || 'edit',
  saveTimer: null, searchTimer: null, graphAnimation: null, selectedFiles: [],
};

const els = {
  noteList: $('#noteList'), searchInput: $('#searchInput'), titleInput: $('#titleInput'), contentInput: $('#contentInput'), contentHighlight: $('#contentHighlight'),
  previewPane: $('#previewPane'), editorColumns: $('#editorColumns'), editorShell: $('#editorShell'), emptyState: $('#emptyState'),
  browserTitle: $('#browserTitle'), browserMeta: $('#browserMeta'), allCount: $('#allCount'), favCount: $('#favCount'),
  tagList: $('#tagList'), categoryTree: $('#categoryTree'), crumbTitle: $('#crumbTitle'), crumbCategory: $('#crumbCategory'),
  dateMeta: $('#dateMeta'), wordCount: $('#wordCount'), saveState: $('#saveState'), favoriteBtn: $('#favoriteBtn'),
  tagsEditor: $('#tagsEditor'), outgoingLinks: $('#outgoingLinks'), backlinks: $('#backlinks'), typedRelations: $('#typedRelations'),
  relatedNotes: $('#relatedNotes'), summaryBox: $('#summaryBox'), articleBox: $('#articleBox'), atomBox: $('#atomBox'),
  modelHint: $('#modelHint'), noteCategorySelect: $('#noteCategorySelect'), wikilinkChips: $('#wikilinkChips'),
  qualityBox: $('#qualityBox'), questionsBox: $('#questionsBox'), contradictionsBox: $('#contradictionsBox'),
};

async function api(path, options = {}) {
  const config = { ...options, headers: { ...(options.headers || {}) } };
  if (config.body && !(config.body instanceof FormData)) {
    config.headers['Content-Type'] = 'application/json';
    if (typeof config.body !== 'string') config.body = JSON.stringify(config.body);
  }
  const response = await fetch(path, config);
  if (!response.ok) {
    let message = `Ошибка ${response.status}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response;
}

function toast(message, duration = 2800) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove('show'), duration);
}

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function formatDate(value) {
  if (!value) return '';
  const date = new Date(value);
  return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}

function relativeDate(value) {
  const date = new Date(value);
  const diff = Date.now() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'сейчас';
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ч`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} дн`;
  return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' }).format(date);
}

function countWords(text = '') { return (text.trim().match(/[A-Za-zА-Яа-яЁё0-9_-]+/g) || []).length; }
function plural(number, one, few, many) { const n = Math.abs(number) % 100, n1 = n % 10; return n > 10 && n < 20 ? many : n1 > 1 && n1 < 5 ? few : n1 === 1 ? one : many; }
function categoryById(id) { return state.categories.find(x => Number(x.id) === Number(id)); }
function categoryTitle(id) { return id ? (categoryById(id)?.title || 'Категория') : 'Без категории'; }

function debounceSave() {
  if (!state.activeNote) return;
  els.saveState.textContent = 'Изменено';
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveActiveNote, 700);
}

// Categories ---------------------------------------------------------
async function loadCategories() {
  const data = await api('/api/categories');
  state.categories = data.flat || [];
  state.categoryTree = data.tree || [];
  state.unfiledCount = Number(data.unfiled || 0);

  // First start after upgrade: make top-level folders visible immediately.
  if (!localStorage.getItem('zettel-expanded-categories')) {
    state.categoryTree.forEach(node => state.expandedCategories.add(Number(node.id)));
    if (state.unfiledCount > 0) state.expandedCategories.add(0);
    localStorage.setItem('zettel-expanded-categories', JSON.stringify([...state.expandedCategories]));
  }

  renderCategoryTree(state.unfiledCount);
  fillCategorySelects();
}

function fillTreeSelect(select, { includeUnfiled = false, includeRoot = false, excludeIds = new Set() } = {}) {
  if (!select) return;
  const current = select.value;
  select.innerHTML = '';
  if (includeUnfiled) select.insertAdjacentHTML('beforeend', '<option value="0">Без категории</option>');
  if (includeRoot) select.insertAdjacentHTML('beforeend', '<option value="0">Корень каталога</option>');
  const addNodes = (nodes, depth = 0) => nodes.forEach(node => {
    if (!excludeIds.has(Number(node.id))) {
      const option = document.createElement('option');
      option.value = node.id;
      option.textContent = `${'— '.repeat(depth)}${node.title}`;
      select.appendChild(option);
    }
    addNodes(node.children || [], depth + 1);
  });
  addNodes(state.categoryTree);
  if ([...select.options].some(x => x.value === current)) select.value = current;
}

function fillCategorySelects() {
  [els.noteCategorySelect, $('#importCategorySelect')].forEach(select => fillTreeSelect(select, { includeUnfiled: true }));
  if (state.activeNote) els.noteCategorySelect.value = String(state.activeNote.category_id || 0);
}

function directNotesForCategory(categoryId) {
  return state.allNotes
    .filter(note => Number(note.category_id || 0) === Number(categoryId || 0))
    .sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), 'ru'));
}

function makeCatalogNoteRow(note, depth) {
  const row = document.createElement('button');
  row.type = 'button';
  row.className = `catalog-note-row${state.activeNote?.id === note.id ? ' active' : ''}`;
  row.style.paddingLeft = `${depth * 14 + 27}px`;
  row.title = note.title;
  row.innerHTML = `<span class="catalog-note-icon">◇</span><span class="catalog-note-title">${escapeHtml(note.title || 'Без названия')}</span><button type="button" class="catalog-note-delete" title="Удалить заметку">×</button>`;
  row.draggable = true;
  row.addEventListener('click', event => {
    if (event.target.closest('.catalog-note-delete')) return;
    event.stopPropagation();
    openNote(note.id);
  });
  $('.catalog-note-delete', row).addEventListener('click', event => {
    event.stopPropagation();
    deleteNoteById(note.id, note.title);
  });
  row.addEventListener('dragstart', event => {
    event.stopPropagation();
    event.dataTransfer.setData('text/zettel-note', String(note.id));
    event.dataTransfer.effectAllowed = 'move';
  });
  return row;
}

function renderCategoryTree(unfiledCount = state.unfiledCount || 0) {
  if (!els.categoryTree) return;
  els.categoryTree.innerHTML = '';

  const unfiledWrap = document.createElement('div');
  unfiledWrap.appendChild(makeCategoryRow({ id: 0, title: 'Без категории', total_count: unfiledCount, children: [] }, 0, true));
  if (state.expandedCategories.has(0)) {
    directNotesForCategory(0).forEach(note => unfiledWrap.appendChild(makeCatalogNoteRow(note, 0)));
  }
  els.categoryTree.appendChild(unfiledWrap);

  const renderNodes = (nodes, depth = 0, parent) => nodes.forEach(node => {
    const wrap = document.createElement('div');
    wrap.className = 'category-node';
    wrap.appendChild(makeCategoryRow(node, depth, false));

    if (state.expandedCategories.has(Number(node.id))) {
      const children = document.createElement('div');
      children.className = 'category-children';
      directNotesForCategory(node.id).forEach(note => children.appendChild(makeCatalogNoteRow(note, depth + 1)));
      renderNodes(node.children || [], depth + 1, children);
      wrap.appendChild(children);
    }
    parent.appendChild(wrap);
  });
  renderNodes(state.categoryTree, 0, els.categoryTree);

  if (!state.categories.length) {
    const empty = document.createElement('div');
    empty.className = 'catalog-empty';
    empty.innerHTML = 'Папок пока нет.<br><button type="button" id="catalogEmptyCreate">Создать первую папку</button>';
    els.categoryTree.appendChild(empty);
    $('#catalogEmptyCreate')?.addEventListener('click', () => openCategoryDialog());
  }
}

function toggleCategoryExpanded(id) {
  const numericId = Number(id);
  state.expandedCategories.has(numericId) ? state.expandedCategories.delete(numericId) : state.expandedCategories.add(numericId);
  localStorage.setItem('zettel-expanded-categories', JSON.stringify([...state.expandedCategories]));
  renderCategoryTree(state.unfiledCount || 0);
}

function makeCategoryRow(node, depth, isUnfiled) {
  const id = Number(node.id);
  const row = document.createElement('div');
  row.className = `category-row${state.activeFilter === 'category' && Number(state.activeCategoryId) === id ? ' active' : ''}`;
  row.style.paddingLeft = `${depth * 14 + 2}px`;
  row.dataset.categoryId = String(id);

  const directNotes = directNotesForCategory(id).length;
  const hasChildren = isUnfiled ? directNotes > 0 : ((node.children || []).length > 0 || directNotes > 0);
  const expanded = state.expandedCategories.has(id);
  row.innerHTML = `
    <button type="button" class="caret" title="${expanded ? 'Свернуть' : 'Развернуть'}">${hasChildren ? (expanded ? '▾' : '▸') : '·'}</button>
    <span class="folder">${isUnfiled ? '◇' : (expanded ? '▾' : '▰')}</span>
    <span class="category-title">${escapeHtml(node.title)}</span>
    <span class="category-count">${node.total_count || 0}</span>
    ${isUnfiled ? '' : `<span class="category-row-actions"><button type="button" data-action="child" title="Новая подпапка">＋</button><button type="button" data-action="edit" title="Изменить папку">⋯</button></span>`}`;

  $('.caret', row)?.addEventListener('click', event => {
    event.stopPropagation();
    if (hasChildren) toggleCategoryExpanded(id);
  });
  row.addEventListener('dblclick', event => {
    if (event.target.closest('.category-row-actions')) return;
    if (hasChildren) toggleCategoryExpanded(id);
  });
  row.addEventListener('click', event => {
    if (event.target.closest('.category-row-actions')) return;
    selectCategory(id, node.title);
  });

  $('[data-action="child"]', row)?.addEventListener('click', event => {
    event.stopPropagation();
    state.expandedCategories.add(id);
    openCategoryDialog({ parentId: id });
  });
  $('[data-action="edit"]', row)?.addEventListener('click', event => {
    event.stopPropagation();
    openCategoryDialog({ editId: id });
  });

  row.addEventListener('dragover', event => { event.preventDefault(); row.classList.add('drop-target'); });
  row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
  row.addEventListener('drop', async event => {
    event.preventDefault();
    event.stopPropagation();
    row.classList.remove('drop-target');
    const noteId = Number(event.dataTransfer.getData('text/zettel-note'));
    if (!noteId) return;
    try {
      await api(`/api/notes/${noteId}`, { method: 'PUT', body: { category_id: id } });
      if (state.activeNote?.id === noteId) {
        state.activeNote.category_id = id || null;
        els.noteCategorySelect.value = String(id);
        els.crumbCategory.textContent = categoryTitle(id);
      }
      await Promise.all([loadNotes(els.searchInput.value.trim()), loadCategories()]);
      toast(`Заметка перемещена: ${node.title}`);
    } catch (error) { toast(error.message); }
  });
  return row;
}

async function selectCategory(id, title) {
  state.activeFilter = 'category';
  state.activeCategoryId = id;
  state.activeTag = null;
  setActiveNavigation(null);
  els.browserTitle.textContent = title;
  if (!state.expandedCategories.has(Number(id))) {
    state.expandedCategories.add(Number(id));
    localStorage.setItem('zettel-expanded-categories', JSON.stringify([...state.expandedCategories]));
  }
  await loadNotes(els.searchInput.value.trim());
  renderCategoryTree(state.unfiledCount || 0);
}

function descendantCategoryIds(categoryId) {
  const found = new Set([Number(categoryId)]);
  const walk = parentId => state.categories.filter(x => Number(x.parent_id || 0) === Number(parentId)).forEach(child => {
    found.add(Number(child.id));
    walk(Number(child.id));
  });
  walk(Number(categoryId));
  return found;
}

function openCategoryDialog({ parentId = null, editId = null } = {}) {
  const modal = $('#categoryModal');
  const input = $('#categoryNameInput');
  const parentSelect = $('#categoryParentSelect');
  const saveBtn = $('#saveCategoryBtn');
  const deleteBtn = $('#deleteCategoryDialogBtn');
  const title = $('#categoryModalTitle');

  modal.dataset.editId = editId ? String(editId) : '';
  const category = editId ? categoryById(editId) : null;
  title.textContent = category ? 'Изменить папку' : (parentId ? 'Новая подпапка' : 'Новая папка');
  input.value = category?.title || '';

  const exclude = category ? descendantCategoryIds(category.id) : new Set();
  fillTreeSelect(parentSelect, { includeRoot: true, excludeIds: exclude });
  parentSelect.value = String(category ? (category.parent_id || 0) : (parentId || 0));

  saveBtn.textContent = category ? 'Сохранить' : 'Создать папку';
  deleteBtn.classList.toggle('hidden', !category);
  showModal('#categoryModal');
  setTimeout(() => { input.focus(); input.select(); }, 30);
}

async function saveCategoryDialog() {
  const modal = $('#categoryModal');
  const editId = Number(modal.dataset.editId || 0) || null;
  const title = $('#categoryNameInput').value.trim();
  const parentId = Number($('#categoryParentSelect').value) || null;
  if (!title) return toast('Введите название папки');

  const button = $('#saveCategoryBtn');
  button.disabled = true;
  try {
    let category;
    if (editId) {
      category = await api(`/api/categories/${editId}`, { method: 'PUT', body: { title, parent_id: parentId } });
    } else {
      category = await api('/api/categories', { method: 'POST', body: { title, parent_id: parentId } });
    }
    if (parentId) state.expandedCategories.add(Number(parentId));
    state.expandedCategories.add(Number(category.id));
    localStorage.setItem('zettel-expanded-categories', JSON.stringify([...state.expandedCategories]));
    hideModal(modal);
    await loadCategories();
    await selectCategory(Number(category.id), category.title);
    toast(editId ? 'Папка сохранена' : 'Папка создана');
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}

async function deleteCategoryFromDialog() {
  const modal = $('#categoryModal');
  const id = Number(modal.dataset.editId || 0);
  const category = categoryById(id);
  if (!id || !category) return;
  if (!confirm(`Удалить папку «${category.title}»? Заметки и подпапки перейдут на уровень выше.`)) return;
  try {
    await api(`/api/categories/${id}`, { method: 'DELETE' });
    state.expandedCategories.delete(id);
    localStorage.setItem('zettel-expanded-categories', JSON.stringify([...state.expandedCategories]));
    hideModal(modal);
    state.activeFilter = 'all'; state.activeCategoryId = null; setActiveNavigation('all');
    els.browserTitle.textContent = 'Все заметки';
    await Promise.all([loadNotes(), loadCategories()]);
    toast('Папка удалена, заметки сохранены');
  } catch (error) { toast(error.message); }
}

function createCategory(parentId = null) { openCategoryDialog({ parentId }); }
function categoryActions() {
  const id = state.activeFilter === 'category' ? Number(state.activeCategoryId) : 0;
  if (id) openCategoryDialog({ editId: id });
  else openCategoryDialog();
}

// Notes --------------------------------------------------------------
async function loadNotes(query = '') {
  const params = new URLSearchParams();
  if (query) params.set('q', query);
  if (state.activeFilter === 'favorites') params.set('favorite', 'true');
  if (state.activeFilter === 'category') params.set('category_id', String(state.activeCategoryId));
  const data = await api(`/api/notes?${params}`);
  state.notes = data;
  renderNoteList();
  await loadTags();
  const [all, fav] = await Promise.all([api('/api/notes'), api('/api/notes?favorite=true')]);
  state.allNotes = all;
  els.allCount.textContent = all.length;
  els.favCount.textContent = fav.length;
  renderCategoryTree(state.unfiledCount || 0);
}

function filteredNotes() {
  if (!state.activeTag) return state.notes;
  return state.notes.filter(note => (note.tags || []).includes(state.activeTag));
}

function renderNoteList() {
  const notes = filteredNotes();
  els.noteList.innerHTML = '';
  els.browserMeta.textContent = `${notes.length} ${plural(notes.length, 'элемент', 'элемента', 'элементов')}`;
  if (!notes.length) {
    els.noteList.innerHTML = '<div class="muted" style="padding:24px 12px;text-align:center">Ничего не найдено</div>';
    return;
  }
  notes.forEach(note => {
    const card = document.createElement('article');
    card.className = `note-card${state.activeNote?.id === note.id ? ' active' : ''}`;
    card.draggable = true;
    const tags = (note.tags || []).slice(0, 2).map(tag => `<span class="mini-tag">#${escapeHtml(tag)}</span>`).join('');
    const folder = note.category_title ? `<span class="mini-folder">▰ ${escapeHtml(note.category_title)}</span>` : '';
    card.innerHTML = `
      ${note.favorite ? '<span class="star">★</span>' : ''}
      <button type="button" class="note-delete-button" title="Удалить заметку">×</button>
      <h3>${escapeHtml(note.title || 'Без названия')}</h3>
      <p>${escapeHtml(note.excerpt || 'Пустая заметка')}</p>
      <div class="note-card-foot"><span>${relativeDate(note.updated_at)}</span>${folder}${tags}</div>`;
    card.addEventListener('click', event => {
      if (event.target.closest('.note-delete-button')) return;
      openNote(note.id);
    });
    $('.note-delete-button', card).addEventListener('click', event => {
      event.stopPropagation();
      deleteNoteById(note.id, note.title);
    });
    card.addEventListener('dragstart', event => { card.classList.add('dragging'); event.dataTransfer.setData('text/zettel-note', String(note.id)); event.dataTransfer.effectAllowed = 'move'; });
    card.addEventListener('dragend', () => card.classList.remove('dragging'));
    els.noteList.appendChild(card);
  });
}

async function loadTags() {
  state.tags = await api('/api/tags');
  els.tagList.innerHTML = '';
  state.tags.forEach(item => {
    const button = document.createElement('button');
    button.className = `tag-button${state.activeTag === item.tag ? ' active' : ''}`;
    button.innerHTML = `<span>${escapeHtml(item.tag)}</span><b>${item.count}</b>`;
    button.addEventListener('click', () => {
      state.activeTag = state.activeTag === item.tag ? null : item.tag;
      els.browserTitle.textContent = state.activeTag ? `#${state.activeTag}` : titleForFilter();
      renderNoteList(); loadTags();
    });
    els.tagList.appendChild(button);
  });
}

function titleForFilter() {
  if (state.activeFilter === 'favorites') return 'Избранное';
  if (state.activeFilter === 'category') return state.activeCategoryId ? categoryTitle(state.activeCategoryId) : 'Без категории';
  return 'Все заметки';
}

function setActiveNavigation(filter) {
  $$('.nav-item[data-filter]').forEach(item => item.classList.toggle('active', filter && item.dataset.filter === filter));
}

async function setFilter(filter) {
  state.activeFilter = filter; state.activeCategoryId = null; state.activeTag = null;
  setActiveNavigation(filter); els.browserTitle.textContent = titleForFilter();
  await Promise.all([loadNotes(els.searchInput.value.trim()), loadCategories()]);
}

async function openNote(id) {
  try {
    if (state.activeNote && els.saveState.textContent !== 'Сохранено') await saveActiveNote();
    const note = await api(`/api/notes/${id}`);
    state.activeNote = note;
    els.titleInput.value = note.title;
    els.contentInput.value = note.content;
    els.crumbTitle.textContent = note.title;
    els.crumbCategory.textContent = categoryTitle(note.category_id);
    els.dateMeta.textContent = `Изменено ${formatDate(note.updated_at)}`;
    els.favoriteBtn.textContent = note.favorite ? '★' : '☆';
    els.favoriteBtn.title = note.favorite ? 'Убрать из избранного' : 'Добавить в избранное';
    els.noteCategorySelect.value = String(note.category_id || 0);
    els.editorShell.classList.remove('hidden'); els.emptyState.classList.add('hidden');
    [els.summaryBox, els.articleBox, els.atomBox].forEach(x => x.classList.add('hidden'));
    updateEditorStats(); renderTags(note.tags || []); renderConnections(note); renderNoteList(); setEditorMode(state.editorMode);
    await Promise.all([loadRelated(false), loadSmart(false)]);
  } catch (error) { toast(error.message); }
}

async function createNote(title = 'Новая заметка', content = '') {
  try {
    let categoryId = null;
    if (state.activeFilter === 'category') categoryId = Number(state.activeCategoryId) || null;
    else if (state.activeNote?.category_id) categoryId = state.activeNote.category_id;
    const note = await api('/api/notes', { method: 'POST', body: { title, content, tags: [], category_id: categoryId } });
    await Promise.all([loadNotes(), loadCategories()]);
    await openNote(note.id);
    setTimeout(() => els.titleInput.select(), 40);
  } catch (error) { toast(error.message); }
}

async function saveActiveNote() {
  if (!state.activeNote) return;
  clearTimeout(state.saveTimer);
  els.saveState.textContent = 'Сохраняю…';
  try {
    const updated = await api(`/api/notes/${state.activeNote.id}`, { method: 'PUT', body: {
      title: els.titleInput.value.trim() || 'Без названия', content: els.contentInput.value,
      tags: state.activeNote.tags || [], favorite: state.activeNote.favorite,
      category_id: Number(els.noteCategorySelect.value),
    }});
    state.activeNote = { ...state.activeNote, ...updated };
    els.crumbTitle.textContent = updated.title; els.crumbCategory.textContent = categoryTitle(updated.category_id);
    els.dateMeta.textContent = `Изменено ${formatDate(updated.updated_at)}`; els.saveState.textContent = 'Сохранено';
    await Promise.all([loadNotes(els.searchInput.value.trim()), loadCategories()]);
    const refreshed = await api(`/api/notes/${updated.id}`); state.activeNote = refreshed; renderConnections(refreshed); renderWikilinks();
  } catch (error) { els.saveState.textContent = 'Ошибка'; toast(error.message); }
}

async function deleteNoteById(noteId, title = 'Без названия') {
  if (!noteId || !confirm(`Удалить заметку «${title}»? Это действие нельзя отменить.`)) return;
  try {
    await api(`/api/notes/${noteId}`, { method: 'DELETE' });
    if (state.activeNote?.id === Number(noteId)) {
      state.activeNote = null;
      els.editorShell.classList.add('hidden');
      els.emptyState.classList.remove('hidden');
    }
    await Promise.all([loadNotes(els.searchInput.value.trim()), loadCategories()]);
    toast('Заметка удалена');
  } catch (error) { toast(error.message); }
}

async function deleteActive() {
  if (!state.activeNote) return;
  await deleteNoteById(state.activeNote.id, state.activeNote.title);
}

// Markdown and active wikilinks -------------------------------------
function parseWikilinks(text = '') {
  const result = []; const re = /\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]/g; let match;
  while ((match = re.exec(text))) result.push({ title: match[1].trim(), alias: (match[2] || match[1]).trim(), start: match.index, end: re.lastIndex });
  return result;
}

function inlineMarkdown(raw) {
  const wikiTokens = [];
  let prepared = String(raw).replace(/\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]/g, (_, title, alias) => {
    const token = `@@ZETTELWIKI${wikiTokens.length}@@`;
    wikiTokens.push({ title: title.trim(), label: (alias || title).trim() });
    return token;
  });
  let text = escapeHtml(prepared);
  text = text.replace(/@@ZETTELWIKI(\d+)@@/g, (_, index) => {
    const item = wikiTokens[Number(index)];
    const exists = state.allNotes.some(n => n.title.trim().toLowerCase() === item.title.toLowerCase());
    return `<a href="#" class="wiki-link${exists ? '' : ' missing'}" data-wiki="${encodeURIComponent(item.title)}">${escapeHtml(item.label)}</a>`;
  });
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
  text = text.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  return text;
}

function renderMarkdown(markdown = '') {
  const lines = markdown.replace(/\r/g, '').split('\n');
  let html = '', inCode = false, inList = false;
  for (const line of lines) {
    if (line.trim().startsWith('```')) {
      if (inList) { html += '</ul>'; inList = false; }
      html += inCode ? '</code></pre>' : '<pre><code>'; inCode = !inCode; continue;
    }
    if (inCode) { html += `${escapeHtml(line)}\n`; continue; }
    if (/^\s*[-*+]\s+/.test(line)) {
      if (!inList) { html += '<ul>'; inList = true; }
      const item = line.replace(/^\s*[-*+]\s+/, '');
      const checked = item.match(/^\[([ xX])\]\s*(.*)$/);
      html += checked ? `<li><input type="checkbox" disabled ${checked[1].toLowerCase() === 'x' ? 'checked' : ''}> ${inlineMarkdown(checked[2])}</li>` : `<li>${inlineMarkdown(item)}</li>`;
      continue;
    }
    if (inList) { html += '</ul>'; inList = false; }
    if (/^###\s+/.test(line)) html += `<h3>${inlineMarkdown(line.replace(/^###\s+/, ''))}</h3>`;
    else if (/^##\s+/.test(line)) html += `<h2>${inlineMarkdown(line.replace(/^##\s+/, ''))}</h2>`;
    else if (/^#\s+/.test(line)) html += `<h1>${inlineMarkdown(line.replace(/^#\s+/, ''))}</h1>`;
    else if (/^>\s?/.test(line)) html += `<blockquote>${inlineMarkdown(line.replace(/^>\s?/, ''))}</blockquote>`;
    else if (/^---+$/.test(line.trim())) html += '<hr>';
    else if (!line.trim()) html += '<div style="height:.65em"></div>';
    else html += `<p>${inlineMarkdown(line)}</p>`;
  }
  if (inList) html += '</ul>'; if (inCode) html += '</code></pre>';
  return html;
}

async function openWiki(title) {
  try {
    if (state.activeNote && els.saveState.textContent !== 'Сохранено') await saveActiveNote();
    const note = await api('/api/notes/open-link', { method: 'POST', body: { title, content: '', tags: [], category_id: state.activeNote?.category_id || null } });
    await Promise.all([loadNotes(), loadCategories()]); await openNote(note.id);
  } catch (error) { toast(error.message); }
}

function wikiAtPosition(text, position) {
  // end is exclusive: a caret immediately after ]] must never activate the link.
  return parseWikilinks(text).find(link => position >= link.start && position < link.end) || null;
}

function renderEditorHighlight() {
  if (!els.contentHighlight) return;
  const text = els.contentInput.value || '';
  const links = parseWikilinks(text);
  let cursor = 0, html = '';
  for (const link of links) {
    html += escapeHtml(text.slice(cursor, link.start));
    const exists = state.allNotes.some(n => n.title.trim().toLowerCase() === link.title.toLowerCase());
    html += `<span class="editor-wiki-link${exists ? '' : ' missing'}" data-wiki-title="${encodeURIComponent(link.title)}">${escapeHtml(text.slice(link.start, link.end))}</span>`;
    cursor = link.end;
  }
  html += escapeHtml(text.slice(cursor));
  // A trailing newline is needed so textarea/pre scrolling stays aligned at EOF.
  els.contentHighlight.innerHTML = html + (text.endsWith('\n') ? '\n' : '');
  els.contentHighlight.scrollTop = els.contentInput.scrollTop;
  els.contentHighlight.scrollLeft = els.contentInput.scrollLeft;
}

function renderWikilinks() {
  const links = parseWikilinks(els.contentInput.value);
  els.wikilinkChips.innerHTML = '';
  if (!links.length) { els.wikilinkChips.innerHTML = '<span class="muted">ссылок пока нет</span>'; return; }
  const unique = [...new Map(links.map(x => [x.title.toLowerCase(), x])).values()];
  unique.forEach(link => {
    const exists = state.allNotes.some(n => n.title.trim().toLowerCase() === link.title.toLowerCase());
    const button = document.createElement('button'); button.className = `wiki-chip${exists ? '' : ' missing'}`;
    button.textContent = `${exists ? '↗' : '＋'} ${link.alias}`; button.title = exists ? 'Открыть заметку' : 'Создать заметку';
    button.addEventListener('click', () => openWiki(link.title)); els.wikilinkChips.appendChild(button);
  });
}

function setEditorMode(mode) {
  state.editorMode = ['edit', 'split', 'read'].includes(mode) ? mode : 'edit';
  localStorage.setItem('zettel-editor-mode', state.editorMode);
  els.editorColumns.className = `editor-columns mode-${state.editorMode}`;
  $$('.mode-switch button').forEach(button => button.classList.toggle('active', button.dataset.mode === state.editorMode));
  els.previewPane.innerHTML = renderMarkdown(els.contentInput.value);
}

function updateEditorStats() {
  els.wordCount.textContent = `${countWords(els.contentInput.value)} слов`;
  els.previewPane.innerHTML = renderMarkdown(els.contentInput.value);
  renderEditorHighlight();
  renderWikilinks();
}

// Connections and smart tools ---------------------------------------
function renderTags(tags) {
  els.tagsEditor.innerHTML = '';
  tags.forEach(tag => {
    const button = document.createElement('button'); button.className = 'editor-tag'; button.textContent = `#${tag} ×`; button.title = 'Удалить тег';
    button.addEventListener('click', () => { state.activeNote.tags = (state.activeNote.tags || []).filter(x => x !== tag); renderTags(state.activeNote.tags); debounceSave(); });
    els.tagsEditor.appendChild(button);
  });
}

function connectionCard(item, byTitle = false) {
  const card = document.createElement('div'); card.className = 'link-card';
  card.innerHTML = `<strong>${escapeHtml(item.title)}</strong>${item.excerpt ? `<span>${escapeHtml(item.excerpt)}</span>` : ''}${item.exists === false ? '<span>Будет создана при открытии</span>' : ''}`;
  card.addEventListener('click', () => byTitle ? openWiki(item.title) : openNote(item.id)); return card;
}

function renderConnections(note) {
  const outgoing = note.outgoing || [];
  els.outgoingLinks.innerHTML = outgoing.length ? '' : 'Нет ссылок'; outgoing.forEach(item => els.outgoingLinks.appendChild(connectionCard(item, true)));
  const backlinks = note.backlinks || [];
  els.backlinks.innerHTML = backlinks.length ? '' : 'Нет обратных ссылок'; backlinks.forEach(item => els.backlinks.appendChild(connectionCard(item, false)));
  const relations = note.relations || [];
  els.typedRelations.innerHTML = relations.length ? '' : 'Нет связей';
  relations.forEach(rel => {
    const outgoingRel = Number(rel.source_id) === Number(note.id);
    const otherId = outgoingRel ? rel.target_id : rel.source_id;
    const otherTitle = outgoingRel ? rel.target_title : rel.source_title;
    const card = document.createElement('div'); card.className = 'link-card';
    card.innerHTML = `<strong>${escapeHtml(otherTitle)}</strong><span>${outgoingRel ? '→' : '←'} ${escapeHtml(rel.relation_type)}</span><div class="card-actions"><button class="negative">Удалить связь</button></div>`;
    card.addEventListener('click', event => { if (!event.target.closest('button')) openNote(otherId); });
    $('button', card).addEventListener('click', async event => { event.stopPropagation(); await api(`/api/relations/${rel.id}`, { method: 'DELETE' }); const refreshed = await api(`/api/notes/${note.id}`); state.activeNote = refreshed; renderConnections(refreshed); });
    els.typedRelations.appendChild(card);
  });
}

async function addRelation() {
  if (!state.activeNote) return;
  const targetTitle = prompt('Точное название заметки, с которой создать связь:');
  if (!targetTitle?.trim()) return;
  const target = state.allNotes.find(n => n.title.trim().toLowerCase() === targetTitle.trim().toLowerCase());
  if (!target) return toast('Заметка с таким названием не найдена');
  const relationType = prompt('Тип связи:', 'связано с') || 'связано с';
  try {
    await api('/api/relations', { method: 'POST', body: { source_id: state.activeNote.id, target_id: target.id, relation_type: relationType } });
    const refreshed = await api(`/api/notes/${state.activeNote.id}`); state.activeNote = refreshed; renderConnections(refreshed); toast('Связь добавлена');
  } catch (error) { toast(error.message); }
}

async function loadRelated(showToast = true) {
  if (!state.activeNote) return;
  try {
    const data = await api(`/api/ai/related/${state.activeNote.id}`);
    els.relatedNotes.innerHTML = data.results.length ? '' : 'Подходящих заметок пока нет';
    data.results.forEach(item => {
      const card = document.createElement('div'); card.className = 'link-card';
      card.innerHTML = `<strong>${escapeHtml(item.title)}</strong><i class="score">${Math.round((item.score || 0) * 100)}%</i><span>${escapeHtml(item.reason || item.excerpt || '')}</span><div class="card-actions"><button data-add>＋ [[ссылка]]</button><button data-good>Полезно</button><button data-bad class="negative">Не связано</button></div>`;
      card.addEventListener('click', event => { if (!event.target.closest('button')) openNote(item.id); });
      $('[data-add]', card).addEventListener('click', event => { event.stopPropagation(); const token = `[[${item.title}]]`; if (!els.contentInput.value.includes(token)) els.contentInput.value += `\n\n${token}`; updateEditorStats(); debounceSave(); toast('Ссылка добавлена в текст'); });
      $('[data-good]', card).addEventListener('click', event => { event.stopPropagation(); saveRelatedFeedback(item.id, 1); });
      $('[data-bad]', card).addEventListener('click', event => { event.stopPropagation(); saveRelatedFeedback(item.id, -1); card.remove(); });
      els.relatedNotes.appendChild(card);
    });
    if (showToast) toast('Связи обновлены');
  } catch (error) { els.relatedNotes.textContent = 'Не удалось выполнить анализ'; if (showToast) toast(error.message); }
}

async function saveRelatedFeedback(targetId, rating) {
  try { await api('/api/ai/feedback', { method: 'POST', body: { kind: 'related', rating, query: state.activeNote?.title || '', source_note_id: state.activeNote?.id, target_note_id: targetId } }); toast(rating > 0 ? 'Связь отмечена полезной' : 'Отрицательная оценка сохранена'); } catch (error) { toast(error.message); }
}

async function loadSmart(showToast = true) {
  if (!state.activeNote) return;
  [els.qualityBox, els.questionsBox, els.contradictionsBox].forEach(x => x.textContent = 'Анализируется…');
  try {
    const data = await api(`/api/ai/smart/${state.activeNote.id}`); state.activeNote.smart = data;
    renderQuality(data.quality); renderQuestions(data.questions); renderContradictions(data.contradictions);
    if (showToast) toast('Умный анализ обновлён');
  } catch (error) { [els.qualityBox, els.questionsBox, els.contradictionsBox].forEach(x => x.textContent = 'Анализ недоступен'); if (showToast) toast(error.message); }
}

function renderQuality(quality) {
  els.qualityBox.innerHTML = `<div class="quality-score"><div class="quality-number">${quality.score}</div><div><strong>${quality.score >= 80 ? 'Хорошая заметка' : quality.score >= 55 ? 'Можно улучшить' : 'Нужна доработка'}</strong><div class="muted">${quality.words} слов · ${quality.links} ссылок · ${quality.tags} тегов</div></div></div>`;
  quality.checks.forEach(check => { const row = document.createElement('div'); row.className = `quality-check${check.ok ? '' : ' bad'}`; row.textContent = `${check.ok ? '✓' : '•'} ${check.label}${check.advice ? ` — ${check.advice}` : ''}`; els.qualityBox.appendChild(row); });
}
function renderQuestions(items) { els.questionsBox.innerHTML = ''; (items || []).forEach(text => { const div = document.createElement('div'); div.className = 'question-item'; div.textContent = text; els.questionsBox.appendChild(div); }); }
function renderContradictions(items) {
  els.contradictionsBox.innerHTML = items?.length ? '' : 'Явных противоречий не найдено';
  (items || []).forEach(item => { const div = document.createElement('div'); div.className = 'contradiction-item'; div.innerHTML = `<strong>${escapeHtml(item.note_title)}</strong><br>${escapeHtml(item.reason)}<br><span>${escapeHtml(item.left)}</span><br>↔<br><span>${escapeHtml(item.right)}</span>`; div.addEventListener('click', () => openNote(item.note_id)); els.contradictionsBox.appendChild(div); });
}

async function summarize() {
  if (!state.activeNote) return;
  try { const data = await api(`/api/ai/summarize/${state.activeNote.id}`); els.summaryBox.textContent = data.summary || 'Недостаточно текста'; els.summaryBox.classList.remove('hidden'); } catch (error) { toast(error.message); }
}

async function showArticleAnalysis() {
  if (!state.activeNote) return;
  if (!state.activeNote.smart) await loadSmart(false);
  const a = state.activeNote.smart.article;
  els.articleBox.innerHTML = ['purpose:Цель', 'methods:Методы', 'sample:Выборка', 'results:Результаты', 'limitations:Ограничения', 'practice:Практическое значение'].map(item => { const [key, label] = item.split(':'); return `<h4>${label}</h4><div>${escapeHtml(a[key])}</div>`; }).join('') + `<h4>Ключевые слова</h4><div>${(a.keywords || []).map(x => `#${escapeHtml(x)}`).join(' · ')}</div>`;
  els.articleBox.classList.remove('hidden');
}

async function showAtomize() {
  if (!state.activeNote) return;
  if (!state.activeNote.smart) await loadSmart(false);
  const atoms = state.activeNote.smart.atoms || [];
  els.atomBox.innerHTML = atoms.length ? '' : 'Не удалось выделить самостоятельные части';
  atoms.forEach((atom, index) => { const row = document.createElement('div'); row.className = 'atom-row'; row.innerHTML = `<label><input type="checkbox" checked data-index="${index}"><span><strong>${escapeHtml(atom.title)}</strong><br>${escapeHtml(atom.content.slice(0, 180))}${atom.content.length > 180 ? '…' : ''}</span></label>`; els.atomBox.appendChild(row); });
  if (atoms.length) { const button = document.createElement('button'); button.className = 'primary-button full'; button.style.marginTop = '9px'; button.textContent = 'Создать выбранные заметки'; button.addEventListener('click', async () => { const selected = $$('input[type="checkbox"]:checked', els.atomBox).map(x => atoms[Number(x.dataset.index)]); if (!selected.length) return; button.disabled = true; try { const data = await api(`/api/ai/atomize/${state.activeNote.id}/create`, { method: 'POST', body: { items: selected, link_from_source: true } }); await Promise.all([loadNotes(), loadCategories()]); await openNote(state.activeNote.id); toast(`Создано заметок: ${data.created.length}`); } catch (error) { toast(error.message); } finally { button.disabled = false; } }); els.atomBox.appendChild(button); }
  els.atomBox.classList.remove('hidden');
}

async function suggestTags() {
  if (!state.activeNote) return;
  try {
    const data = await api(`/api/ai/tags/${state.activeNote.id}`);
    if (!data.tags.length) return toast('Новых тегов не найдено');
    const choice = prompt(`Предложенные теги:\n${data.tags.join(', ')}\n\nВведите нужные через запятую:`, data.tags.slice(0, 4).join(', '));
    if (!choice) return;
    const selected = choice.split(',').map(x => x.trim().replace(/^#/, '').toLowerCase()).filter(Boolean);
    state.activeNote.tags = [...new Set([...(state.activeNote.tags || []), ...selected])]; renderTags(state.activeNote.tags); debounceSave();
  } catch (error) { toast(error.message); }
}

function addTag() {
  if (!state.activeNote) return;
  const tag = prompt('Название тега:')?.trim().replace(/^#/, '').toLowerCase();
  if (!tag) return;
  state.activeNote.tags = [...new Set([...(state.activeNote.tags || []), tag])]; renderTags(state.activeNote.tags); debounceSave();
}

// Search -------------------------------------------------------------
async function semanticSearch() {
  const query = els.searchInput.value.trim(); if (!query) { els.searchInput.focus(); return toast('Введите запрос'); }
  try {
    const data = await api('/api/search/semantic', { method: 'POST', body: { query, limit: 40 } });
    state.notes = data.results; state.activeTag = null; state.activeFilter = 'search'; setActiveNavigation(null);
    els.browserTitle.textContent = 'Гибридный поиск'; renderNoteList();
    els.modelHint.textContent = data.model.ready ? 'нейропоиск активен' : 'лексический режим';
  } catch (error) { toast(error.message); }
}

// Dashboard ----------------------------------------------------------
async function showDashboard() {
  showModal('#dashboardModal'); const target = $('#dashboardContent'); target.innerHTML = '<div class="muted">Анализ базы…</div>';
  try {
    const data = await api('/api/ai/dashboard');
    const s = data.stats;
    const healthLabel = s.health >= 80 ? 'сильная база' : s.health >= 60 ? 'хорошая, но есть пробелы' : s.health >= 40 ? 'нужна доработка связей' : 'база пока фрагментарная';
    target.innerHTML = `<div class="knowledge-health">
      <div class="health-score"><strong>${s.health}</strong><span>/100</span></div>
      <div class="health-copy"><h3>Здоровье базы: ${healthLabel}</h3><p>${s.connected_pct}% заметок включены в сеть · ${s.fresh_30} обновлялись за 30 дней. Анализ выполняется локально: связи, темы, факты, незавершённые мысли и история интересов не отправляются наружу.</p></div>
    </div>
    <div class="stat-grid"><div class="stat-card"><strong>${s.notes}</strong><span>заметок</span></div><div class="stat-card"><strong>${s.words.toLocaleString('ru-RU')}</strong><span>слов</span></div><div class="stat-card"><strong>${s.links}</strong><span>вики-ссылок</span></div><div class="stat-card"><strong>${s.categories}</strong><span>категорий</span></div></div>
    <section class="dashboard-section dashboard-priority"><div class="section-row"><h3>Что лучше сделать дальше</h3><span class="dashboard-badge">приоритеты</span></div><div id="dashboardActions" class="knowledge-actions"></div></section>
    <div class="dashboard-grid"></div>`;
    const actions = $('#dashboardActions', target);
    if (data.rediscover) {
      const b=document.createElement('button'); b.className='knowledge-action priority-low';
      b.innerHTML=`<b>Вспомнить: ${escapeHtml(data.rediscover.title)}</b><span>${data.rediscover.age_days} дней без внимания · ${escapeHtml(data.rediscover.reason)}</span>`;
      b.addEventListener('click',()=>openFromDashboard(data.rediscover.id)); actions.appendChild(b);
    }
    (data.recommendations || []).forEach(item => {
      const button = document.createElement('button'); button.className = `knowledge-action priority-${item.priority || 'low'}`;
      button.innerHTML = `<b>${escapeHtml(item.title)}</b><span>${escapeHtml(item.detail || '')}</span>`;
      button.addEventListener('click', () => { if (item.term) { hideModal($('#dashboardModal')); openWiki(item.term); } else if (item.note_id) openFromDashboard(item.note_id); }); actions.appendChild(button);
    });
    if (!actions.children.length) actions.innerHTML = '<div class="muted">Критичных проблем не найдено. База выглядит хорошо структурированной.</div>';
    const grid = $('.dashboard-grid', target);
    dashboardSection(grid, 'Темы базы — автоматически', data.topics, x => `${x.term} · ${x.count} заметок`, x => { hideModal($('#dashboardModal')); els.searchInput.value=x.term; semanticSearch(); });
    dashboardSection(grid, 'Незавершённые мысли', data.unfinished, x => `${x.title} · ${x.issues.join(', ')}`, x => openFromDashboard(x.id));
    dashboardSection(grid, 'Статус заметок', data.statuses?.slice(0,18), x => `${x.status} · ${x.title} · ${x.incoming+x.outgoing} связей`, x => openFromDashboard(x.id));
    dashboardSection(grid, 'Ключевые узлы базы', data.hubs, x => `${x.title} · ${x.degree} связей`, x => openFromDashboard(x.id));
    dashboardSection(grid, 'Слабые / короткие заметки', data.weak, x => `${x.title} · ${x.words} слов`, x => openFromDashboard(x.id));
    dashboardSection(grid, 'Изолированные заметки', data.orphans, x => x.title, x => openFromDashboard(x.id));
    dashboardSection(grid, 'Без тегов', data.untagged, x => x.title, x => openFromDashboard(x.id));
    dashboardSection(grid, 'Без категории', data.uncategorized, x => x.title, x => openFromDashboard(x.id));
    dashboardSection(grid, 'Без источника', data.without_sources, x => x.title, x => openFromDashboard(x.id));
    dashboardSection(grid, 'Устаревшие 90+ дней', data.stale, x => `${x.title} · ${relativeDate(x.updated_at)}`, x => openFromDashboard(x.id));
    dashboardSection(grid, 'Не созданные [[ссылки]]', data.missing_links, x => `${x.title} · ${x.mentions}`, x => { hideModal($('#dashboardModal')); openWiki(x.title); });
    dashboardSection(grid, 'Пробелы в знаниях', data.gaps, x => `${x.term} · ${x.mentions} упоминаний`, x => { hideModal($('#dashboardModal')); createNote(x.term, `# ${x.term}\n\n## Определение\n\n## Связанные материалы\n`); });
    dashboardSection(grid, 'Возможные дубликаты', data.duplicates, x => `${x.left.title} ↔ ${x.right.title} · ${Math.round(x.score * 100)}%`, x => openFromDashboard(x.left.id));
    dashboardSection(grid, 'Возможные расхождения фактов', data.fact_conflicts, x => `${x.left_title} ↔ ${x.right_title} · проверить числа`, x => openFromDashboard(x.left_id));
    const timeline=(data.interest_timeline||[]).map(x=>({label:`${x.month}: ${(x.topics||[]).map(t=>t.term).join(' · ')}`}));
    dashboardSection(grid, 'История интересов', timeline, x => x.label, () => {});
    const facts=[]; const labels={dates:'Даты',percentages:'Проценты',doi:'DOI',pmid:'PMID',urls:'Ссылки'};
    Object.entries(data.facts||{}).forEach(([kind,items]) => { if(items?.length) facts.push({kind,label:`${labels[kind]||kind}: ${items.length}`, sample:items.slice(0,4).map(x=>x.value).join(' · ')}); });
    dashboardSection(grid, 'Факты базы', facts, x => `${x.label} · ${x.sample}`, () => {});
  } catch (error) { target.textContent = error.message; }
}
function dashboardSection(grid, title, items, label, onClick) { const section = document.createElement('section'); section.className = 'dashboard-section'; section.innerHTML = `<h3>${escapeHtml(title)}</h3><div class="dashboard-list"></div>`; const list = $('.dashboard-list', section); if (!items?.length) list.innerHTML = '<div class="muted">Ничего не найдено</div>'; (items || []).forEach(item => { const button = document.createElement('button'); button.className = 'dashboard-item'; button.textContent = label(item); if(onClick) button.addEventListener('click', () => onClick(item)); list.appendChild(button); }); grid.appendChild(section); }
function openFromDashboard(id) { hideModal($('#dashboardModal')); openNote(id); }

// AI chat ------------------------------------------------------------
async function refreshModelStatus() {
  try {
    const data = await api('/api/ai/status'); const model = data.personal_model;
    $('#modelStatusTitle').textContent = model.ready ? 'Персональная модель обучена' : 'Модель не обучена';
    $('#modelStatusText').textContent = model.ready ? `${model.vocabulary} слов · ${model.trained_notes} заметок · ${model.trained_tokens} токенов` : 'Добавьте тексты и запустите обучение';
    els.modelHint.textContent = model.ready ? `модель: ${model.vocabulary} слов` : 'модель не обучена';
    await loadVerifiedAnswers();
  } catch (error) { els.modelHint.textContent = 'статус ИИ недоступен'; }
}

async function trainModel() {
  const button = $('#trainBtn'), progress = $('#trainProgress'); button.disabled = true; progress.classList.remove('hidden'); button.textContent = 'Обучение…';
  try { const result = await api('/api/ai/train', { method: 'POST', body: { epochs: Number($('#epochsSelect').value), dimensions: Number($('#dimsSelect').value) } }); toast(`Готово: ${result.vocabulary} слов, ${result.pairs} контекстов`, 4500); await refreshModelStatus(); if (state.activeNote) await Promise.all([loadRelated(false), loadSmart(false)]); }
  catch (error) { toast(error.message, 5000); }
  finally { button.disabled = false; progress.classList.add('hidden'); button.textContent = 'Обучить на всех заметках'; }
}

function addChatMessage(text, role, data = null, question = '') {
  const message = document.createElement('div'); message.className = `chat-message ${role}`; message.textContent = text;
  if (data?.sources?.length) { const sources = document.createElement('div'); sources.className = 'chat-sources'; data.sources.forEach((source, index) => { const button = document.createElement('button'); button.className = 'chat-source'; button.textContent = `[${index + 1}] ${source.title}`; button.addEventListener('click', () => { hideModal($('#aiModal')); openNote(source.id); }); sources.appendChild(button); }); message.appendChild(sources); }
  if (data && typeof data.confidence === 'number') { const confidence = document.createElement('div'); confidence.className = 'confidence'; confidence.textContent = `${data.mode === 'verified' ? 'Эталонный ответ' : 'Уверенность поиска'}: ${Math.max(0, Math.round(data.confidence * 100))}%`; message.appendChild(confidence); }
  if (data && data.mode !== 'verified') { const feedback = document.createElement('div'); feedback.className = 'chat-feedback'; feedback.innerHTML = '<button>Полезно</button><button>Не помогло</button>'; const buttons = $$('button', feedback); buttons[0].addEventListener('click', () => saveChatFeedback(question, data.sources?.[0]?.id, 1)); buttons[1].addEventListener('click', () => saveChatFeedback(question, data.sources?.[0]?.id, -1)); message.appendChild(feedback); }
  $('#chatMessages').appendChild(message); $('#chatMessages').scrollTop = $('#chatMessages').scrollHeight; return message;
}
async function saveChatFeedback(query, targetNoteId, rating) { try { await api('/api/ai/feedback', { method: 'POST', body: { kind: 'chat', rating, query, source_note_id: state.activeNote?.id || null, target_note_id: targetNoteId || null } }); toast('Оценка сохранена локально'); } catch (error) { toast(error.message); } }

async function sendChat(event) {
  event.preventDefault(); const input = $('#chatInput'), question = input.value.trim(); if (!question) return;
  const userMessage = addChatMessage(question, 'user'); input.value = '';
  // Auto-Capture analyses only the user's own thought. Assistant replies are never
  // silently promoted to knowledge, which protects the base from generated noise.
  autoCaptureMessage211(question, userMessage);
  const pending = addChatMessage('Ищу по локальной базе…', 'assistant');
  try { const data = await api('/api/ai/chat', { method: 'POST', body: { question, note_id: state.activeNote?.id || null } }); pending.remove(); addChatMessage(data.answer, 'assistant', data, question); }
  catch (error) { pending.textContent = error.message; }
}

async function saveVerifiedAnswer() {
  const question = $('#verifiedQuestion').value.trim(), answer = $('#verifiedAnswer').value.trim();
  if (!question || !answer) return toast('Заполните вопрос и ответ');
  try { await api('/api/answers', { method: 'POST', body: { question, answer, source_note_ids: state.activeNote ? [state.activeNote.id] : [], tags: [] } }); $('#verifiedQuestion').value = ''; $('#verifiedAnswer').value = ''; await loadVerifiedAnswers(); toast('Эталонный ответ сохранён'); } catch (error) { toast(error.message); }
}
async function loadVerifiedAnswers() { const list = $('#verifiedList'); const answers = await api('/api/answers'); list.innerHTML = ''; answers.slice(0, 20).forEach(answer => { const div = document.createElement('div'); div.className = 'verified-item'; div.innerHTML = `<strong>${escapeHtml(answer.question)}</strong><br>${escapeHtml(answer.answer.slice(0, 130))}${answer.answer.length > 130 ? '…' : ''}<button>×</button>`; $('button', div).addEventListener('click', async () => { await api(`/api/answers/${answer.id}`, { method: 'DELETE' }); loadVerifiedAnswers(); }); list.appendChild(div); }); }

// Import/export/settings --------------------------------------------
function showModal(selector) { $(selector).classList.remove('hidden'); }
function hideModal(modal) { modal.classList.add('hidden'); if (modal.id === 'graphModal' && state.graphAnimation) { cancelAnimationFrame(state.graphAnimation); state.graphAnimation = null; } }
async function openSettings() { try { const settings = await api('/api/settings'); $('#useOllama').checked = settings.use_ollama; $('#ollamaUrl').value = settings.ollama_url; $('#ollamaModel').value = settings.ollama_model; showModal('#settingsModal'); } catch (error) { toast(error.message); } }
async function saveSettings() { try { await api('/api/settings', { method: 'PUT', body: { use_ollama: $('#useOllama').checked, ollama_url: $('#ollamaUrl').value.trim(), ollama_model: $('#ollamaModel').value.trim() } }); hideModal($('#settingsModal')); toast('Настройки сохранены'); } catch (error) { toast(error.message); } }
async function exportBackup() { try { const response = await fetch('/api/export'); if (!response.ok) throw new Error('Не удалось создать резервную копию'); const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a'); link.href = url; link.download = `ZettelLocal-${new Date().toISOString().slice(0,10)}.zip`; link.click(); URL.revokeObjectURL(url); toast('Резервная копия создана'); } catch (error) { toast(error.message); } }
async function importBackup(file) { const form = new FormData(); form.append('file', file); try { const data = await api('/api/import', { method: 'POST', body: form }); await Promise.all([loadCategories(), loadNotes()]); toast(`Импортировано: ${data.imported} заметок, ${data.categories || 0} категорий`, 4500); } catch (error) { toast(error.message, 5000); } }
function openImportDocs() { state.selectedFiles = []; $('#docsInput').value = ''; $('#dropZone').firstChild.textContent = 'Перетащите файлы сюда или нажмите для выбора'; $('#importResult').classList.add('hidden'); $('#importCategorySelect').value = String(state.activeFilter === 'category' ? state.activeCategoryId : 0); showModal('#importDocsModal'); }
function updateSelectedFiles(files) { state.selectedFiles = [...files]; $('#dropZone').firstChild.textContent = state.selectedFiles.length ? `Выбрано файлов: ${state.selectedFiles.length}` : 'Перетащите файлы сюда или нажмите для выбора'; }
async function runDocumentImport() { if (!state.selectedFiles.length) return toast('Выберите файлы'); const button = $('#runImportBtn'); button.disabled = true; const form = new FormData(); state.selectedFiles.forEach(file => form.append('files', file)); const category = Number($('#importCategorySelect').value); try { const path = category ? `/api/import/files?category_id=${category}` : '/api/import/files'; const data = await api(path, { method: 'POST', body: form }); const result = $('#importResult'); result.classList.remove('hidden'); result.innerHTML = `Создано заметок: <strong>${data.created.length}</strong>${data.errors.length ? `<br>Ошибок: ${data.errors.map(x => `${escapeHtml(x.file)} — ${escapeHtml(x.error)}`).join('<br>')}` : ''}`; await Promise.all([loadNotes(), loadCategories()]); if (data.created[0]) openNote(data.created[0].id); } catch (error) { toast(error.message); } finally { button.disabled = false; } }

async function openDaily() { try { const note = await api('/api/daily', { method: 'POST' }); await Promise.all([loadNotes(), loadCategories()]); openNote(note.id); } catch (error) { toast(error.message); } }

// Graph --------------------------------------------------------------
let graphMode29='notes';
async function showGraph(mode=graphMode29) {
  if(typeof mode!=='string') mode=graphMode29;
  graphMode29=mode;
  showModal('#graphModal');
  document.querySelectorAll('[data-graph-mode]').forEach(b=>b.classList.toggle('active',b.dataset.graphMode===mode));
  const subtitle=$('#graphSubtitle');
  if(subtitle) subtitle.textContent=mode==='entities'?'Сплошная — подтверждённая, пунктир — предложенная, тонкая — косвенная связь':'Вики-ссылки и типизированные отношения между заметками';
  const legend=$('#graphLegend');
  if(legend) legend.style.display=mode==='entities'?'flex':'none';
  await new Promise(resolve => setTimeout(resolve, 30));
  try { drawGraph(await api(mode==='entities'?'/api/smart/entity-graph':'/api/graph')); } catch (error) { toast(error.message); }
}
function lineDistance29(px,py,x1,y1,x2,y2){
  const dx=x2-x1,dy=y2-y1,len2=dx*dx+dy*dy;if(!len2)return Math.hypot(px-x1,py-y1);
  let t=((px-x1)*dx+(py-y1)*dy)/len2;t=Math.max(0,Math.min(1,t));
  return Math.hypot(px-(x1+t*dx),py-(y1+t*dy));
}
async function showEntityGraphNode29(node){
  try{const d=await api('/api/smart/entities');const x=(d.items||[]).find(v=>v.name.toLowerCase()===node.name.toLowerCase());if(x)showEntityDossier28(x);else showTopicPicture29(node.name)}catch(e){showTopicPicture29(node.name)}
}
function hideRelationModal212(){const m=$('#relationModal212');if(m)m.classList.add('hidden');}
function showEntityRelation29(link){
  if(!link||link.kind==='indirect')return;
  const a=link.source.name||link.source.title,b=link.target.name||link.target.title;
  const modal=$('#relationModal212'),box=$('#relationModalContent212');
  if(!modal||!box)return;
  $('#relationModalTitle212').textContent=`${a} ↔ ${b}`;
  $('#relationModalSub212').textContent=link.kind==='confirmed'?'Подтверждённая связь':'Предполагаемая связь';
  box.innerHTML=`<div class="relation-explain"><div class="relation-status ${link.kind}">${link.kind==='confirmed'?'Сплошная линия · подтверждено':'Пунктирная линия · ZettelLocal предлагает'}</div><h3>Почему связаны?</h3><p>${escapeHtml(link.reason||'Связь обнаружена по структуре базы.')}</p>${link.notes?`<p><b>Совместных заметок:</b> ${link.notes}</p>`:''}<p><b>Сила связи:</b> ${link.strength||0}%</p></div><div class="review-actions">${link.kind==='suggested'?'<button type="button" class="primary-button" id="confirmEntityRelation29">✓ Подтвердить связь</button><button type="button" class="soft-button" id="rejectEntityRelation29">× Отклонить связь</button>':''}<button type="button" class="soft-button" id="openLeftEntity29">Открыть ${escapeHtml(a)}</button><button type="button" class="soft-button" id="openRightEntity29">Открыть ${escapeHtml(b)}</button></div>`;
  modal.classList.remove('hidden');
  const confirm=$('#confirmEntityRelation29'),reject=$('#rejectEntityRelation29');
  if(confirm){confirm.textContent='✓ Подтвердить связь';confirm.onclick=async()=>{confirm.disabled=true;confirm.textContent='Подтверждаю…';try{await api('/api/smart/entity-relation',{method:'POST',body:{source:a,target:b,status:'confirmed'}});hideRelationModal212();await showGraph('entities');toast('Связь подтверждена')}catch(e){confirm.disabled=false;confirm.textContent='✓ Подтвердить связь';toast(e.message)}};}
  if(reject){reject.textContent='× Отклонить связь';reject.onclick=async()=>{reject.disabled=true;try{await api('/api/smart/entity-relation',{method:'POST',body:{source:a,target:b,status:'rejected'}});hideRelationModal212();await showGraph('entities');toast('Связь скрыта')}catch(e){reject.disabled=false;toast(e.message)}};}
  $('#openLeftEntity29').onclick=()=>{hideRelationModal212();showTopicPicture29(a)};
  $('#openRightEntity29').onclick=()=>{hideRelationModal212();showTopicPicture29(b)};
}
function drawGraph(graph) {
  const canvas = $('#graphCanvas'), ctx = canvas.getContext('2d'), rect = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr; ctx.setTransform(dpr,0,0,dpr,0,0);
  const width = rect.width, height = rect.height, entityMode=graph.kind==='entities';
  const nodes = graph.nodes.map((node,i) => ({ ...node, x: width/2 + Math.cos(i*2.399)*(70+Math.sqrt(i)*25), y: height/2 + Math.sin(i*2.399)*(70+Math.sqrt(i)*25), vx:0, vy:0, radius: entityMode?Math.max(6,Math.min(12,6+(node.importance||0)/22)):(node.favorite?9:7) }));
  const byId = new Map(nodes.map(n => [n.id,n])); const links = graph.links.map(l => ({...l, source:byId.get(l.source), target:byId.get(l.target)})).filter(l => l.source && l.target);
  let dragged=null, dragStart=null, dragMoved=false;
  function step() { for(let i=0;i<nodes.length;i++) for(let j=i+1;j<nodes.length;j++){ const a=nodes[i],b=nodes[j]; let dx=b.x-a.x,dy=b.y-a.y; const d2=Math.max(dx*dx+dy*dy,90),force=Math.min(5400/d2,1.9),d=Math.sqrt(d2); dx/=d;dy/=d;a.vx-=dx*force;a.vy-=dy*force;b.vx+=dx*force;b.vy+=dy*force; } links.forEach(l=>{const dx=l.target.x-l.source.x,dy=l.target.y-l.source.y,d=Math.max(Math.hypot(dx,dy),1),desired=l.kind==='indirect'?175:135,f=(d-desired)*.006;l.source.vx+=dx/d*f;l.source.vy+=dy/d*f;l.target.vx-=dx/d*f;l.target.vy-=dy/d*f;}); nodes.forEach(n=>{n.vx+=(width/2-n.x)*.0007;n.vy+=(height/2-n.y)*.0007;n.vx*=.88;n.vy*=.88;if(n!==dragged){n.x+=n.vx;n.y+=n.vy;}n.x=Math.max(28,Math.min(width-28,n.x));n.y=Math.max(28,Math.min(height-28,n.y));}); render(); state.graphAnimation=requestAnimationFrame(step); }
  function render(){ctx.clearRect(0,0,width,height);links.forEach(l=>{ctx.beginPath();ctx.moveTo(l.source.x,l.source.y);ctx.lineTo(l.target.x,l.target.y);if(entityMode){if(l.kind==='suggested'){ctx.setLineDash([7,6]);ctx.strokeStyle='rgba(79,125,82,.60)';ctx.lineWidth=1.6}else if(l.kind==='indirect'){ctx.setLineDash([2,7]);ctx.strokeStyle='rgba(102,111,99,.18)';ctx.lineWidth=.8}else{ctx.setLineDash([]);ctx.strokeStyle='rgba(63,91,62,.72)';ctx.lineWidth=2.1}}else{ctx.setLineDash([]);ctx.strokeStyle=l.type==='wiki'?'rgba(90,105,82,.25)':'rgba(166,122,62,.42)';ctx.lineWidth=l.type==='wiki'?1:1.5}ctx.stroke();ctx.setLineDash([]);});nodes.forEach(n=>{ctx.beginPath();ctx.arc(n.x,n.y,n.radius,0,Math.PI*2);ctx.fillStyle=entityMode?'#4d7755':(n.favorite?'#aa9460':'#617254');ctx.fill();ctx.strokeStyle='rgba(255,255,255,.9)';ctx.lineWidth=2;ctx.stroke();ctx.font=entityMode?'11px system-ui':'11px system-ui';ctx.fillStyle='#41443e';ctx.textAlign='center';const raw=n.name||n.title||'',label=raw.length>27?raw.slice(0,26)+'…':raw;ctx.fillText(label,n.x,n.y+n.radius+15);});}
  const point=e=>{const r=canvas.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top};};
  canvas.onpointerdown=e=>{const p=point(e);dragged=nodes.find(n=>Math.hypot(n.x-p.x,n.y-p.y)<18)||null;dragStart=p;dragMoved=false;if(dragged)canvas.setPointerCapture(e.pointerId);};
  canvas.onpointermove=e=>{if(!dragged)return;const p=point(e);if(dragStart&&Math.hypot(p.x-dragStart.x,p.y-dragStart.y)>5)dragMoved=true;dragged.x=p.x;dragged.y=p.y;dragged.vx=dragged.vy=0;};
  canvas.onpointerup=e=>{const p=point(e);if(dragged){const clicked=dragMoved?null:dragged;dragged=null;dragStart=null;if(clicked){if(entityMode){showEntityGraphNode29(clicked)}else{hideModal($('#graphModal'));openNote(clicked.id)}}return;}if(entityMode){const hit=links.filter(l=>l.kind!=='indirect').map(l=>({l,d:lineDistance29(p.x,p.y,l.source.x,l.source.y,l.target.x,l.target.y)})).sort((a,b)=>a.d-b.d)[0];if(hit&&hit.d<8)showEntityRelation29(hit.l);}};
  if(state.graphAnimation)cancelAnimationFrame(state.graphAnimation);step();
}

// Events -------------------------------------------------------------
function wireEvents() {
  $('#emptyNewBtn').addEventListener('click', () => createNote());
  const toggleSidebar = () => {
    const closed = document.body.classList.toggle('sidebar-closed');
    localStorage.setItem('zettel-sidebar-closed', closed ? '1' : '0');
    const btn = $('#sidebarToggleBtn');
    if (btn) btn.title = closed ? 'Показать панель навигации' : 'Скрыть панель навигации';
  };
  $('#sidebarToggleBtn').addEventListener('click', toggleSidebar);
  $('#sidebarOpenBtn').addEventListener('click', toggleSidebar);
  $('#dailyBtn').addEventListener('click', openDaily); $('#dashboardBtn').addEventListener('click', showDashboard); $('#graphBtn').addEventListener('click', showGraph);
  $('#aiBtn').addEventListener('click', async () => { showModal('#aiModal'); await refreshModelStatus(); }); $('#settingsBtn').addEventListener('click', openSettings);
  $('#semanticBtn').addEventListener('click', semanticSearch); $('#focusBtn').addEventListener('click', () => {
    if (!state.activeNote) return toast('Сначала откройте заметку');
    document.body.classList.toggle('focus-mode');
    const active = document.body.classList.contains('focus-mode');
    $('#focusBtn').textContent = active ? '×' : '⛶';
    $('#focusBtn').title = active ? 'Выйти из режима фокуса' : 'Режим фокуса';
    requestAnimationFrame(() => {
      syncEditorHighlight();
      if (state.editorMode === 'read') els.previewPane.innerHTML = renderMarkdown(els.contentInput.value);
      else els.contentInput.focus({preventScroll:true});
    });
  });
  $('#deleteBtn').addEventListener('click', deleteActive); $('#refreshRelatedBtn').addEventListener('click', () => loadRelated(true)); $('#refreshSmartBtn').addEventListener('click', () => loadSmart(true));
  $('#summarizeBtn').addEventListener('click', summarize); $('#articleAnalyzeBtn').addEventListener('click', showArticleAnalysis); $('#atomizeBtn').addEventListener('click', showAtomize);
  $('#suggestTagsBtn').addEventListener('click', suggestTags); $('#addTagBtn').addEventListener('click', addTag); $('#addRelationBtn').addEventListener('click', addRelation);
  $('#trainBtn').addEventListener('click', trainModel); $('#chatForm').addEventListener('submit', sendChat); $('#saveVerifiedBtn').addEventListener('click', saveVerifiedAnswer);
  $('#saveSettingsBtn').addEventListener('click', saveSettings); $('#exportBtn').addEventListener('click', exportBackup);
  $('#importInput').addEventListener('change', event => { if (event.target.files[0]) importBackup(event.target.files[0]); event.target.value=''; });
  $('#importDocsBtn').addEventListener('click', openImportDocs); $('#docsInput').addEventListener('change', event => updateSelectedFiles(event.target.files)); $('#runImportBtn').addEventListener('click', runDocumentImport);
  const dropZone=$('#dropZone'); dropZone.addEventListener('dragover',e=>{e.preventDefault();dropZone.classList.add('dragover');});dropZone.addEventListener('dragleave',()=>dropZone.classList.remove('dragover'));dropZone.addEventListener('drop',e=>{e.preventDefault();dropZone.classList.remove('dragover');updateSelectedFiles(e.dataTransfer.files);});
  $('#addRootCategoryBtn').addEventListener('click', () => openCategoryDialog());
  $('#catalogNewNoteBtn').addEventListener('click', () => { if (state.activeFilter === 'category' && state.activeCategoryId != null) createNote(); else if (state.activeNote?.category_id) createNote(); else createNote(); });
  $('#saveCategoryBtn').addEventListener('click', saveCategoryDialog);
  $('#deleteCategoryDialogBtn').addEventListener('click', deleteCategoryFromDialog);
  $('#categoryNameInput').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); saveCategoryDialog(); } });
  const toggleInspector = () => { const hidden = $('#inspector').classList.toggle('hidden'); document.body.classList.toggle('inspector-closed', hidden); };
  $('#closeInspectorBtn').addEventListener('click', toggleInspector); $('#inspectorToggleBtn').addEventListener('click', toggleInspector);
  $('#favoriteBtn').addEventListener('click', () => { if (!state.activeNote) return; state.activeNote.favorite = !state.activeNote.favorite; els.favoriteBtn.textContent = state.activeNote.favorite ? '★' : '☆'; debounceSave(); });
  $$('.nav-item[data-filter]').forEach(item => item.addEventListener('click', () => setFilter(item.dataset.filter)));
  $$('.mode-switch button').forEach(button => button.addEventListener('click', () => setEditorMode(button.dataset.mode)));
  $$('.close-modal').forEach(button => button.addEventListener('click', () => hideModal(button.closest('.modal'))));
  $$('.modal').forEach(modal => modal.addEventListener('pointerdown', event => { if (event.target === modal) hideModal(modal); }));
  document.addEventListener('click', event => { const link = event.target.closest('[data-wiki]'); if (link) { event.preventDefault(); openWiki(decodeURIComponent(link.dataset.wiki)); } });

  els.titleInput.addEventListener('input', () => { if (!state.activeNote) return; els.crumbTitle.textContent = els.titleInput.value || 'Без названия'; debounceSave(); });
  els.contentInput.addEventListener('input', () => { updateEditorStats(); debounceSave(); clearTimeout(state.smartTypingTimer); state.smartTypingTimer=setTimeout(async()=>{ if(!state.activeNote) return; try { await saveActiveNote(); await Promise.all([loadRelated(false), loadSmart(false)]); } catch(_){} }, 1400); });
  els.contentInput.addEventListener('scroll', () => {
    if (!els.contentHighlight) return;
    els.contentHighlight.scrollTop = els.contentInput.scrollTop;
    els.contentHighlight.scrollLeft = els.contentInput.scrollLeft;
  });
  // In the editor, only the visible blue link span is clickable. The textarea
  // remains fully editable everywhere else, so clicking near a link only moves
  // the caret and never navigates accidentally.
  els.contentHighlight.addEventListener('click', event => {
    const linkEl = event.target.closest('.editor-wiki-link');
    if (!linkEl) return;
    event.preventDefault();
    event.stopPropagation();
    const title = linkEl.dataset.wikiTitle ? decodeURIComponent(linkEl.dataset.wikiTitle) : '';
    if (title) openWiki(title);
  });
  els.noteCategorySelect.addEventListener('change', () => { if (!state.activeNote) return; state.activeNote.category_id = Number(els.noteCategorySelect.value) || null; els.crumbCategory.textContent = categoryTitle(state.activeNote.category_id); debounceSave(); });
  els.searchInput.addEventListener('input', () => { clearTimeout(state.searchTimer); state.searchTimer=setTimeout(()=>{state.activeTag=null;els.browserTitle.textContent=els.searchInput.value.trim()?'Результаты поиска':titleForFilter();loadNotes(els.searchInput.value.trim());},230); });
  els.searchInput.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) semanticSearch(); });
  document.addEventListener('keydown', event => { const ctrl=event.ctrlKey||event.metaKey; if(ctrl&&event.key.toLowerCase()==='n'){event.preventDefault();createNote();} if(ctrl&&event.key.toLowerCase()==='s'){event.preventDefault();saveActiveNote();} if(ctrl&&event.key.toLowerCase()==='k'){event.preventDefault();els.searchInput.focus();els.searchInput.select();} if(ctrl&&event.key==='Enter'){event.preventDefault();setEditorMode(state.editorMode==='edit'?'split':state.editorMode==='split'?'read':'edit');} if(event.key==='Escape')$$('.modal:not(.hidden)').forEach(hideModal); });
  window.addEventListener('beforeunload', () => { if (state.activeNote && els.saveState.textContent !== 'Сохранено') saveActiveNote(); });
}

async function init() {
  if (localStorage.getItem('zettel-sidebar-closed') === '1') document.body.classList.add('sidebar-closed');
wireEvents(); setEditorMode(state.editorMode);
  try { await loadCategories(); await loadNotes(); await loadCategories(); await refreshModelStatus(); if (state.notes.length) await openNote(state.notes[0].id); }
  catch (error) { toast(`Не удалось подключиться к локальному серверу: ${error.message}`, 6000); }
}

init();

// ZettelLocal 2.7 — local smart workflow (no external API) -----------------
const smart27 = { workbench:null, reviewItems:[], reviewIndex:0, slashStart:null };
function smartModal(title, sub=''){ $('#smartModalTitle').textContent=title; $('#smartModalSub').textContent=sub; $('#smartModalContent').innerHTML=''; showModal('#smartModal'); return $('#smartModalContent'); }
async function loadWorkbench27(){
  if(!state.activeNote) return;
  try{
    const d=await api(`/api/smart/workbench/${state.activeNote.id}`); smart27.workbench=d;
    const na=$('#nextActionBox'); if(na){na.className='smart-box';na.innerHTML=`<div class="next-action-card"><b>${escapeHtml(d.next_action.title)}</b><div>${escapeHtml(d.next_action.detail)}</div></div>`;na.onclick=()=>runNextAction27(d.next_action);}
    const eb=$('#entitiesBox'); if(eb){eb.className='smart-box';eb.innerHTML=d.entities.length?d.entities.slice(0,18).map(x=>`<button class="entity-chip" data-concept="${encodeURIComponent(x.value)}"><b>${escapeHtml(x.type)}</b> ${escapeHtml(x.value)}</button>`).join(''):'Сущности не найдены'; eb.querySelectorAll('[data-concept]').forEach(b=>b.onclick=()=>showConcept27(decodeURIComponent(b.dataset.concept)));}
    const tb=$('#tasksBox'); if(tb){tb.className='smart-box';tb.innerHTML=d.tasks.length?d.tasks.map(x=>`<span class="task-chip">☐ ${escapeHtml(x.text)}</span>`).join(''):'Явных действий не найдено';}
  }catch(e){}
}
function runNextAction27(a){ if(a.kind==='link'){ $('#relatedNotes')?.scrollIntoView({behavior:'smooth'}); toast('Посмотрите предлагаемые связи'); } else if(a.kind==='tag') suggestTags(); else if(a.kind==='task') $('#tasksBox')?.scrollIntoView({behavior:'smooth'}); else { els.contentInput.focus(); toast(a.detail); } }
async function showConcept27(q){
  const box=smartModal(`Понятие: ${q}`,'Что я знаю об этом — виртуальная страница понятия'); box.innerHTML='<div class="muted-block">Собираю знания из вашей базы…</div>';
  try{const d=await api(`/api/smart/concept?q=${encodeURIComponent(q)}`); box.innerHTML=`<div class="concept-head">${escapeHtml(q)}</div><section class="dashboard-section"><h3>Упоминания · ${d.mentions.length}</h3><div class="smart-list" id="conceptMentions"></div></section><section class="dashboard-section"><h3>Связанные заметки</h3><div class="smart-list" id="conceptRelated"></div></section>${d.exact_note?'':`<button class="primary-button" id="createConceptBtn">Создать заметку [[${escapeHtml(q)}]]</button>`}`;
  const add=(root,items)=>items.forEach(x=>{const b=document.createElement('button');b.innerHTML=`<b>${escapeHtml(x.title)}</b><small>${x.count?x.count+' упомин. · ':''}${escapeHtml(x.excerpt||'')}</small>`;b.onclick=()=>{hideModal($('#smartModal'));openNote(x.id)};root.appendChild(b)}); add($('#conceptMentions'),d.mentions);add($('#conceptRelated'),d.related); if($('#createConceptBtn')) $('#createConceptBtn').onclick=()=>{hideModal($('#smartModal'));createNote(q,`# ${q}\n\n## Определение\n\n## Что я знаю\n\n## Связи\n`)};
  }catch(e){box.textContent=e.message}
}
async function showInbox27(){ const box=smartModal('Умные входящие','Заметки, которые стоит разобрать: без папки, тегов, связей или с незавершёнными действиями'); const d=await api('/api/smart/inbox'); smart27.reviewItems=d.items; $('#inboxCount').textContent=d.items.length; box.innerHTML='<div class="smart-list" id="inboxList"></div>'; d.items.forEach(x=>{const b=document.createElement('button');b.innerHTML=`<b>${escapeHtml(x.title)}</b><small>${escapeHtml(x.reasons.join(' · '))} → ${escapeHtml(x.next_action.title)}</small>`;b.onclick=()=>{hideModal($('#smartModal'));openNote(x.id)};$('#inboxList').appendChild(b)}); }
async function reviewBase27(){ const d=await api('/api/smart/inbox'); smart27.reviewItems=d.items;smart27.reviewIndex=0;renderReview27(); }
function renderReview27(){const x=smart27.reviewItems[smart27.reviewIndex];const box=smartModal('Разобрать базу',x?`${smart27.reviewIndex+1} из ${smart27.reviewItems.length}`:'Готово'); if(!x){box.innerHTML='<div class="empty-state"><h2>База разобрана</h2><p>Срочных проблемных заметок не найдено.</p></div>';return;} box.innerHTML=`<h2>${escapeHtml(x.title)}</h2><p>${escapeHtml(x.reasons.join(' · '))}</p><div class="next-action-card"><b>${escapeHtml(x.next_action.title)}</b><div>${escapeHtml(x.next_action.detail)}</div></div><div class="review-actions"><button id="reviewOpen">Открыть</button><button id="reviewTags">Предложить теги</button><button id="reviewSkip">Пропустить →</button></div>`;$('#reviewOpen').onclick=()=>{hideModal($('#smartModal'));openNote(x.id)};$('#reviewTags').onclick=async()=>{hideModal($('#smartModal'));await openNote(x.id);suggestTags()};$('#reviewSkip').onclick=()=>{smart27.reviewIndex++;renderReview27()}; }
async function checkedNewNote27(){ const title=prompt('Название новой заметки:',''); if(title===null)return; const t=title.trim()||'Новая заметка'; try{const d=await api(`/api/smart/duplicate?title=${encodeURIComponent(t)}`); if(d.results.length){const best=d.results[0]; if(!confirm(`Похожая заметка уже есть:\n«${best.title}» (${Math.round(best.score*100)}%)\n\nВсё равно создать новую?`)){return openNote(best.id)}} createNote(t,'');}catch(_){createNote(t,'')} }
function showSlash27(){const menu=$('#slashMenu');const r=els.contentInput.getBoundingClientRect();menu.style.left=Math.min(r.left+30,innerWidth-300)+'px';menu.style.top=Math.min(r.top+80,innerHeight-340)+'px';const cmds=[['/ссылка','[[Название]]'],['/todo','- [ ] '],['/дата',new Date().toLocaleDateString('ru-RU')],['/заголовок','## '],['/цитата','> '],['/источник','## Источник\n'],['/вывод','## Вывод\n']];menu.innerHTML=cmds.map((c,i)=>`<button data-i="${i}"><b>${c[0]}</b><span>${escapeHtml(c[1])}</span></button>`).join('');menu.classList.remove('hidden');menu.querySelectorAll('button').forEach(b=>b.onclick=()=>insertSlash27(cmds[+b.dataset.i][1]));}
function insertSlash27(text){const ta=els.contentInput,start=smart27.slashStart??ta.selectionStart,end=ta.selectionEnd;ta.setRangeText(text,start,end,'end');$('#slashMenu').classList.add('hidden');smart27.slashStart=null;ta.dispatchEvent(new Event('input',{bubbles:true}));ta.focus();}
function selectedKnowledge27(){const q=els.contentInput.value.slice(els.contentInput.selectionStart,els.contentInput.selectionEnd).trim(); if(q)showConcept27(q);else toast('Сначала выделите слово или фразу в тексте');}

// Hook into existing UI after initialization.
setTimeout(()=>{ $('#inboxBtn')?.addEventListener('click',showInbox27);$('#reviewBtn')?.addEventListener('click',reviewBase27);$('#refreshWorkbenchBtn')?.addEventListener('click',loadWorkbench27);
  // Replace catalog/default new-note actions with duplicate-aware creation.
  ['catalogNewNoteBtn','emptyNewBtn'].forEach(id=>{const old=$('#'+id);if(!old)return;const clone=old.cloneNode(true);old.parentNode.replaceChild(clone,old);clone.addEventListener('click',checkedNewNote27)});
  els.contentInput.addEventListener('keyup',e=>{if(e.key==='/'){smart27.slashStart=els.contentInput.selectionStart-1;showSlash27()} });
  els.contentInput.addEventListener('contextmenu',e=>{const q=els.contentInput.value.slice(els.contentInput.selectionStart,els.contentInput.selectionEnd).trim();if(q){e.preventDefault();if(confirm(`Что я знаю об «${q}»?`))showConcept27(q)}});
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.shiftKey&&e.key.toLowerCase()==='k'){e.preventDefault();selectedKnowledge27()}});
  const oldOpen=openNote; openNote=async function(id){await oldOpen(id);await loadWorkbench27()};
  api('/api/smart/inbox').then(d=>{$('#inboxCount').textContent=d.items.length}).catch(()=>{});
},50);

// ZettelLocal 2.8 — Smart entities + stable editor ---------------------------
let entity28={items:[],type:'Все'};
async function showEntities28(){
  const box=smartModal('Умные сущности','Автоматическое досье объектов, которые ZettelLocal обнаружил во всей базе');
  box.innerHTML='<div class="muted-block">Собираю сущности из заметок…</div>';
  try{const d=await api('/api/smart/entities');entity28.items=d.items;entity28.type='Все';renderEntities28(box,d.types)}catch(e){box.textContent=e.message}
}
function renderEntities28(box,types={}){
 const names=['Все',...Object.keys(types).sort()];
 const items=entity28.type==='Все'?entity28.items:entity28.items.filter(x=>x.type===entity28.type);
 box.innerHTML=`<div class="entity-browser"><div class="entity-filters">${names.map(x=>`<button data-et="${encodeURIComponent(x)}" class="${x===entity28.type?'active':''}">${escapeHtml(x)}${x==='Все'?' · '+entity28.items.length:' · '+(types[x]||0)}</button>`).join('')}</div><div><div class="entity-grid" id="entityGrid28"></div></div></div>`;
 box.querySelectorAll('[data-et]').forEach(b=>b.onclick=()=>{entity28.type=decodeURIComponent(b.dataset.et);renderEntities28(box,types)});
 const grid=$('#entityGrid28');items.forEach(x=>{const c=document.createElement('div');c.className='entity-card';c.innerHTML=`<b>${escapeHtml(x.name)}</b><small>${escapeHtml(x.type)} · ${x.notes} заметок · ${x.mentions} упоминаний · значимость ${x.importance}%</small>${x.aliases.length>1?`<div>${x.aliases.slice(0,4).map(a=>`<span class="entity-alias">${escapeHtml(a)}</span>`).join('')}</div>`:''}`;c.onclick=()=>showEntityDossier28(x);grid.appendChild(c)});
}
async function showEntityDossier28(x){
 const box=smartModal(x.name,`${x.type} · умное досье сущности`);box.innerHTML='<div class="muted-block">Собираю контекст…</div>';
 try{const d=await api(`/api/smart/concept?q=${encodeURIComponent(x.name)}`);const aliases=x.aliases||[];box.innerHTML=`<div class="concept-head">${escapeHtml(x.name)}</div><p>${escapeHtml(x.type)} · значимость ${x.importance}% · ${x.mentions} упоминаний</p>${aliases.length>1?`<section class="dashboard-section"><h3>Варианты названия</h3>${aliases.map(a=>`<span class="entity-alias">${escapeHtml(a)}</span>`).join('')}</section>`:''}<section class="dashboard-section"><h3>Что база знает</h3><div class="smart-list" id="edMentions"></div></section><section class="dashboard-section"><h3>Связано с</h3><div class="smart-list" id="edRelated"></div></section><section class="dashboard-section"><h3>Открытые вопросы и действия</h3><div id="edQuestions" class="muted-block">Ищу в упоминаниях…</div></section>${d.exact_note?'':`<button class="primary-button" id="entityCreate28">Создать постоянную заметку [[${escapeHtml(x.name)}]]</button>`}`;
 const add=(root,arr)=>arr.forEach(n=>{const b=document.createElement('button');b.innerHTML=`<b>${escapeHtml(n.title)}</b><small>${escapeHtml(n.excerpt||'')}</small>`;b.onclick=()=>{hideModal($('#smartModal'));openNote(n.id)};root.appendChild(b)});add($('#edMentions'),d.mentions);add($('#edRelated'),d.related);
 const q=[];d.mentions.forEach(n=>{const t=n.excerpt||'';if(/\?|TODO|уточнить|проверить|дописать|сделать/i.test(t))q.push(`${n.title}: ${t}`)});$('#edQuestions').innerHTML=q.length?q.slice(0,8).map(v=>`<div class="entity-fact">${escapeHtml(v)}</div>`).join(''):'Явных открытых вопросов рядом с сущностью не найдено.';
 if($('#entityCreate28'))$('#entityCreate28').onclick=()=>{hideModal($('#smartModal'));createNote(x.name,`# ${x.name}\n\n## Что я знаю\n\n## Факты\n\n## Открытые вопросы\n\n## Связи\n`)};
 }catch(e){box.textContent=e.message}
}
setTimeout(()=>{$('#entitiesBtn')?.addEventListener('click',showEntities28);
 // Stable editing: the textarea is the only interactive editing surface.
 // Wiki highlighting stays visual underneath; a click is resolved from the native caret position.
 if(els.contentHighlight){els.contentHighlight.style.pointerEvents='none'}
 let downSel=null;
 els.contentInput.addEventListener('pointerdown',()=>{downSel=[els.contentInput.selectionStart,els.contentInput.selectionEnd]});
 els.contentInput.addEventListener('pointerup',()=>{setTimeout(()=>{const p=els.contentInput.selectionStart;if(els.contentInput.selectionStart!==els.contentInput.selectionEnd)return;const link=wikiAtPosition(els.contentInput.value,p);if(link&&downSel&&Math.abs((downSel[0]??p)-p)<=3)openWiki(link.title)},0)});
},80);

// ZettelLocal 2.9 — smart relations, topic picture, stable writing and link picker
const editor29={entities:[],savedSelection:[0,0]};

function entityRanges29(text, entities=[]){
  const wiki=parseWikilinks(text); const blocked=wiki.map(x=>[x.start,x.end]); const out=[];
  const vals=[...new Set((entities||[]).map(x=>(x.value||x.name||'').trim()).filter(x=>x.length>=3))].sort((a,b)=>b.length-a.length);
  const occupied=[...blocked];
  const overlaps=(a,b)=>occupied.some(r=>a<r[1]&&b>r[0]);
  vals.slice(0,80).forEach(value=>{
    const escaped=value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    let re; try{re=new RegExp(`(^|[^\\p{L}\\p{N}_])(${escaped})(?=$|[^\\p{L}\\p{N}_])`,'giu')}catch(_){return}
    let m; while((m=re.exec(text))){const start=m.index+m[1].length,end=start+m[2].length;if(!overlaps(start,end)){out.push({start,end,value});occupied.push([start,end])} if(re.lastIndex===m.index)re.lastIndex++;}
  });
  return out.sort((a,b)=>a.start-b.start);
}

function renderEditorHighlight(){
  if(!els.contentHighlight)return;
  const text=els.contentInput.value||''; const parts=[];
  parseWikilinks(text).forEach(link=>parts.push({...link,kind:'wiki'}));
  entityRanges29(text,editor29.entities).forEach(x=>parts.push({...x,kind:'entity'}));
  parts.sort((a,b)=>a.start-b.start || (a.kind==='wiki'?-1:1));
  let cursor=0,html='';
  for(const p of parts){if(p.start<cursor)continue;html+=escapeHtml(text.slice(cursor,p.start));if(p.kind==='wiki'){const exists=state.allNotes.some(n=>n.title.trim().toLowerCase()===p.title.toLowerCase());html+=`<span class="editor-wiki-link${exists?'':' missing'}">${escapeHtml(text.slice(p.start,p.end))}</span>`;}else{html+=`<span class="editor-entity-mark">${escapeHtml(text.slice(p.start,p.end))}</span>`;}cursor=p.end;}
  html+=escapeHtml(text.slice(cursor)); els.contentHighlight.innerHTML=html+(text.endsWith('\n')?'\n':'');
  els.contentHighlight.scrollTop=els.contentInput.scrollTop;els.contentHighlight.scrollLeft=els.contentInput.scrollLeft;
}

const loadWorkbench29Base=loadWorkbench27;
loadWorkbench27=async function(){await loadWorkbench29Base();editor29.entities=smart27.workbench?.entities||[];renderEditorHighlight();};

function insertAtSelection29(text){
  const ta=els.contentInput;let [start,end]=editor29.savedSelection||[ta.selectionStart,ta.selectionEnd];
  start=Math.max(0,Math.min(start,ta.value.length));end=Math.max(start,Math.min(end,ta.value.length));
  ta.setRangeText(text,start,end,'end');ta.dispatchEvent(new Event('input',{bubbles:true}));ta.focus({preventScroll:true});
}
function openNoteLinkPicker29(){
  if(!state.activeNote)return toast('Сначала откройте заметку');
  editor29.savedSelection=[els.contentInput.selectionStart,els.contentInput.selectionEnd];
  const box=smartModal('Вставить статью в текст','Найдите заметку и вставьте её как кликабельную [[ссылку]]');
  box.innerHTML='<div class="link-picker-search"><input id="linkPickerSearch29" placeholder="Название или текст заметки…" autocomplete="off"></div><div class="link-picker-list" id="linkPickerList29"></div>';
  const input=$('#linkPickerSearch29'),list=$('#linkPickerList29');
  const draw=()=>{const q=input.value.trim().toLowerCase();const items=state.allNotes.filter(n=>n.id!==state.activeNote?.id&&(!q||n.title.toLowerCase().includes(q)||(n.excerpt||'').toLowerCase().includes(q))).slice(0,80);list.innerHTML='';if(!items.length){list.innerHTML='<div class="muted-block">Ничего не найдено</div>';return}items.forEach((n,i)=>{const b=document.createElement('button');b.className='link-picker-item'+(i===0?' active':'');b.innerHTML=`<b>${escapeHtml(n.title)}</b><small>${escapeHtml(n.excerpt||'')}</small>`;b.onclick=()=>{hideModal($('#smartModal'));insertAtSelection29(`[[${n.title}]]`);toast(`Добавлена ссылка [[${n.title}]]`)};list.appendChild(b)});};
  input.addEventListener('input',draw);input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();list.querySelector('.link-picker-item')?.click()}});draw();setTimeout(()=>input.focus(),20);
}

// Slash command /ссылка now opens the real note picker.
showSlash27=function(){const menu=$('#slashMenu');const r=els.contentInput.getBoundingClientRect();menu.style.left=Math.min(r.left+30,innerWidth-300)+'px';menu.style.top=Math.min(r.top+80,innerHeight-340)+'px';const cmds=[['/ссылка','Найти и вставить заметку','picker'],['/todo','- [ ] ','- [ ] '],['/дата',new Date().toLocaleDateString('ru-RU'),new Date().toLocaleDateString('ru-RU')],['/заголовок','## ','## '],['/цитата','> ','> '],['/источник','## Источник','## Источник\n'],['/вывод','## Вывод','## Вывод\n']];menu.innerHTML=cmds.map((c,i)=>`<button data-i="${i}"><b>${c[0]}</b><span>${escapeHtml(c[1])}</span></button>`).join('');menu.classList.remove('hidden');menu.querySelectorAll('button').forEach(b=>b.onclick=()=>{const c=cmds[+b.dataset.i];if(c[2]==='picker'){const start=smart27.slashStart??els.contentInput.selectionStart;els.contentInput.setRangeText('',start,els.contentInput.selectionStart,'end');els.contentInput.dispatchEvent(new Event('input',{bubbles:true}));$('#slashMenu').classList.add('hidden');editor29.savedSelection=[els.contentInput.selectionStart,els.contentInput.selectionEnd];openNoteLinkPicker29();}else insertSlash27(c[2])});};

async function showTopicPicture29(name){
  const box=smartModal(`Картина темы: ${name}`,'Автоматическая структура темы, отношения, пробелы и уверенность — только по вашей базе');
  box.innerHTML='<div class="muted-block">Строю картину темы…</div>';
  try{const d=await api(`/api/smart/topic?q=${encodeURIComponent(name)}`);const c=d.confidence;box.innerHTML=`
    <div class="topic-hero"><div><div class="concept-head">${escapeHtml(name)}</div><div>${d.notes} заметок · зрелость темы ${d.maturity}%</div></div><div class="topic-score">${c.score}%</div></div>
    <div class="topic-grid">
      <section class="topic-panel"><h3>Ядро и отношения</h3><div id="topicRelations29"></div></section>
      <section class="topic-panel"><h3>Уверенность знаний</h3><b>${escapeHtml(c.level)}</b><div class="confidence-bar"><i style="width:${c.score}%"></i></div><div class="confidence-reasons">${c.reasons.map(x=>`<span>✓ ${escapeHtml(x)}</span>`).join('')}${c.possible_conflicts?`<span>⚠ Возможных числовых расхождений: ${c.possible_conflicts}</span>`:''}</div></section>
      <section class="topic-panel"><h3>Автоматические подтемы</h3><div id="topicSubs29"></div></section>
      <section class="topic-panel"><h3>Мосты между темами</h3><div id="topicBridges29"></div></section>
      <section class="topic-panel"><h3>Пробелы</h3><div>${d.gaps.length?d.gaps.map(x=>`<div class="topic-gap">${escapeHtml(x)}</div>`).join(''):'<div class="topic-good">Явных структурных пробелов не найдено</div>'}</div></section>
      <section class="topic-panel"><h3>Открытые вопросы · ${d.questions.length}</h3><div class="smart-list" id="topicQuestions29"></div></section>
      <section class="topic-panel"><h3>Действия · ${d.actions.length}</h3><div class="smart-list" id="topicActions29"></div></section>
      <section class="topic-panel" style="grid-column:1/-1"><h3>Источники · ${d.sources.length}</h3><div class="smart-list" id="topicSources29"></div></section>
    </div>`;
    const rel=$('#topicRelations29');d.related.slice(0,14).forEach(x=>{const row=document.createElement('div');row.className='relation-row';row.innerHTML=`<div><b>${escapeHtml(x.name)}</b><small> · ${escapeHtml(x.type)} · ${escapeHtml(x.relation)}</small></div><span class="relation-strength">сила ${x.strength}% · ${x.notes} заметок</span>`;row.onclick=()=>showTopicPicture29(x.name);row.style.cursor='pointer';rel.appendChild(row)});if(!d.related.length)rel.innerHTML='<div class="muted-block">Связей пока мало</div>';
    const subs=$('#topicSubs29');d.subtopics.forEach(x=>{const b=document.createElement('button');b.className='subtopic-chip';b.textContent=`${x.name} · ${x.notes}`;b.onclick=()=>showTopicPicture29(x.name);subs.appendChild(b)});if(!d.subtopics.length)subs.innerHTML='<div class="muted-block">Подтемы пока не выделились</div>';
    const bridges=$('#topicBridges29');(d.bridges||[]).forEach(x=>{const row=document.createElement('div');row.className='relation-row';row.innerHTML=`<div><b>${escapeHtml(x.name)}</b><small> · соединяет текущую тему с другими заметками</small></div><span class="relation-strength">${x.inside_notes} внутри · ${x.outside_notes} снаружи</span>`;row.onclick=()=>showTopicPicture29(x.name);row.style.cursor='pointer';bridges.appendChild(row)});if(!(d.bridges||[]).length)bridges.innerHTML='<div class="muted-block">Явные мосты пока не обнаружены</div>';
    const fill=(id,arr)=>{const root=$(id);arr.forEach(x=>{const b=document.createElement('button');b.innerHTML=`<b>${escapeHtml(x.note_title||x.title)}</b><small>${escapeHtml(x.text||x.excerpt||'')}</small>`;b.onclick=()=>{hideModal($('#smartModal'));openNote(x.note_id||x.id)};root.appendChild(b)});if(!arr.length)root.innerHTML='<div class="muted-block">Нет</div>'};fill('#topicQuestions29',d.questions);fill('#topicActions29',d.actions);fill('#topicSources29',d.sources);
  }catch(e){box.textContent=e.message}
}

// Enrich the entity dossier with topic picture and explicit relation reasoning.
showEntityDossier28=async function(x){
 const box=smartModal(x.name,`${x.type} · умное досье сущности`);box.innerHTML='<div class="muted-block">Собираю контекст…</div>';
 try{const [d,t]=await Promise.all([api(`/api/smart/concept?q=${encodeURIComponent(x.name)}`),api(`/api/smart/topic?q=${encodeURIComponent(x.name)}`)]);const aliases=x.aliases||[];box.innerHTML=`<div class="concept-head">${escapeHtml(x.name)}</div><p>${escapeHtml(x.type)} · значимость ${x.importance}% · ${x.mentions} упоминаний</p><div class="entity-dossier-actions"><button class="primary-button" id="entityTopic29">◉ Картина темы</button>${d.exact_note?`<button id="entityOpenExact29">Открыть заметку</button>`:''}</div>${aliases.length>1?`<section class="dashboard-section"><h3>Варианты названия</h3>${aliases.map(a=>`<span class="entity-alias">${escapeHtml(a)}</span>`).join('')}</section>`:''}<section class="dashboard-section"><h3>Умные отношения</h3><div id="entityRelations29"></div></section><section class="dashboard-section"><h3>Уверенность</h3><div class="entity-fact"><b>${escapeHtml(t.confidence.level)} · ${t.confidence.score}%</b><br>${t.confidence.reasons.map(escapeHtml).join(' · ')}</div></section><section class="dashboard-section"><h3>Что база знает</h3><div class="smart-list" id="edMentions"></div></section><section class="dashboard-section"><h3>Связано с</h3><div class="smart-list" id="edRelated"></div></section><section class="dashboard-section"><h3>Открытые вопросы и действия</h3><div id="edQuestions" class="muted-block">Ищу в упоминаниях…</div></section>${d.exact_note?'':`<button class="primary-button" id="entityCreate28">Создать постоянную заметку [[${escapeHtml(x.name)}]]</button>`}`;
 const rel=$('#entityRelations29');t.related.slice(0,10).forEach(r=>{const row=document.createElement('div');row.className='relation-row';row.innerHTML=`<div><b>${escapeHtml(r.name)}</b><small> · ${escapeHtml(r.relation)}</small></div><span class="relation-strength">${r.strength}% · ${r.notes} заметок</span>`;row.title=`Почему связаны: вместе встречаются в ${r.notes} заметках`;row.onclick=()=>showTopicPicture29(r.name);row.style.cursor='pointer';rel.appendChild(row)});if(!t.related.length)rel.innerHTML='<div class="muted-block">Устойчивые отношения пока не обнаружены</div>';
 const add=(root,arr)=>arr.forEach(n=>{const b=document.createElement('button');b.innerHTML=`<b>${escapeHtml(n.title)}</b><small>${escapeHtml(n.excerpt||'')}</small>`;b.onclick=()=>{hideModal($('#smartModal'));openNote(n.id)};root.appendChild(b)});add($('#edMentions'),d.mentions);add($('#edRelated'),d.related);
 const q=[];d.mentions.forEach(n=>{const z=n.excerpt||'';if(/\?|TODO|уточнить|проверить|дописать|сделать/i.test(z))q.push(`${n.title}: ${z}`)});$('#edQuestions').innerHTML=q.length?q.slice(0,8).map(v=>`<div class="entity-fact">${escapeHtml(v)}</div>`).join(''):'Явных открытых вопросов рядом с сущностью не найдено.';
 $('#entityTopic29').onclick=()=>showTopicPicture29(x.name);if($('#entityOpenExact29'))$('#entityOpenExact29').onclick=()=>{hideModal($('#smartModal'));openNote(d.exact_note.id)};if($('#entityCreate28'))$('#entityCreate28').onclick=()=>{hideModal($('#smartModal'));createNote(x.name,`# ${x.name}\n\n## Что я знаю\n\n## Факты\n\n## Открытые вопросы\n\n## Связи\n`)};
 }catch(e){box.textContent=e.message}
};

setTimeout(()=>{
  $('#insertNoteLinkBtn')?.addEventListener('click',openNoteLinkPicker29);
  // Remember a real native selection only; no mirror DOM ever changes the caret.
  ['keyup','mouseup','select','focus'].forEach(ev=>els.contentInput.addEventListener(ev,()=>{editor29.savedSelection=[els.contentInput.selectionStart,els.contentInput.selectionEnd]}));
  // Force the highlight layer to stay non-interactive in every mode, including focus mode.
  if(els.contentHighlight)els.contentHighlight.style.pointerEvents='none';
  renderEditorHighlight();
},120);

setTimeout(()=>{document.querySelectorAll('[data-graph-mode]').forEach(b=>b.addEventListener('click',()=>showGraph(b.dataset.graphMode)));},0);

// ZettelLocal 2.11 — local conversation Auto-Capture -------------------------
function autoCaptureEnabled211(){
  const el=$('#autoCaptureToggle');
  return el ? el.checked : localStorage.getItem('zettel.autocapture.enabled') !== '0';
}
function autoCaptureSensitivity211(){
  const el=$('#autoCaptureSensitivity');
  return el?.value || localStorage.getItem('zettel.autocapture.sensitivity') || 'normal';
}
function initAutoCapture211(){
  const toggle=$('#autoCaptureToggle'), sensitivity=$('#autoCaptureSensitivity'), history=$('#autoCaptureHistoryBtn');
  if(!toggle||!sensitivity) return;
  toggle.checked=localStorage.getItem('zettel.autocapture.enabled')!=='0';
  sensitivity.value=localStorage.getItem('zettel.autocapture.sensitivity')||'normal';
  toggle.addEventListener('change',()=>{localStorage.setItem('zettel.autocapture.enabled',toggle.checked?'1':'0');toast(toggle.checked?'Auto-Capture включён':'Auto-Capture выключен')});
  sensitivity.addEventListener('change',()=>localStorage.setItem('zettel.autocapture.sensitivity',sensitivity.value));
  if(history) history.addEventListener('click',showAutoCaptureHistory211);
  refreshAutoCaptureCount211();
}
async function refreshAutoCaptureCount211(){
  try{const d=await api('/api/smart/autocapture/history');const n=$('#autoCaptureCount');if(n)n.textContent=d.count||0;}catch(_){ }
}
async function autoCaptureMessage211(text,messageEl=null){
  if(!autoCaptureEnabled211()) return;
  const status=$('#autoCaptureStatus212');
  if(status){status.textContent='анализ…';status.className='autocapture-debug-212';}
  try{
    const d=await api('/api/smart/autocapture',{method:'POST',body:{text,sensitivity:autoCaptureSensitivity211(),active_note_id:state.activeNote?.id||null}});
    if(d.count){
      const last=$('#autoCaptureLast');
      if(last){last.classList.remove('hidden');last.innerHTML=`✓ Auto-Capture: ${d.captured.map(x=>`<b>${escapeHtml(x.kind)}</b> · ${escapeHtml(x.title)}`).join(' &nbsp; ')}`;setTimeout(()=>last.classList.add('hidden'),6500)}
      if(messageEl){const badge=document.createElement('div');badge.className='capture-badge';badge.textContent=`✓ сохранено знаний: ${d.count}`;badge.title=d.captured.map(x=>x.title).join('\n');messageEl.appendChild(badge)}
      const n=$('#autoCaptureCount');if(n)n.textContent=d.history_count||d.count;if(status){status.textContent=`захвачено +${d.count}`;status.className='autocapture-debug-212 ok';}
      // Refresh base metadata quietly: captured notes immediately participate in entities/links.
      loadNotes().catch(()=>{}); loadCategories().catch(()=>{});
    }else if(d.skipped?.length && messageEl){if(status){status.textContent='дубль';status.className='autocapture-debug-212 warn';}
      const badge=document.createElement('div');badge.className='capture-badge';badge.textContent='≈ знание уже есть в базе';badge.title=d.skipped.map(x=>x.title).join('\n');messageEl.appendChild(badge);
    }else if(!d.count){if(status){status.textContent='нет события';status.className='autocapture-debug-212';}}
  }catch(e){if(status){status.textContent='ошибка';status.className='autocapture-debug-212 err';status.title=e.message;}console.warn('Auto-Capture:',e);toast('Auto-Capture: '+e.message,4500)}
}
async function showAutoCaptureHistory211(){
  const box=smartModal('Auto-Capture','Локально извлечённые знания из ваших реплик');
  box.innerHTML='<div class="muted-block">Загружаю историю…</div>';
  try{
    const d=await api('/api/smart/autocapture/history');
    box.innerHTML=`<div class="entity-fact"><b>Как это работает</b><br>Анализируются только ваши сообщения. ZettelLocal ищет решения, инсайты, гипотезы, вопросы, факты и действия, проверяет дубли, ставит теги и добавляет найденные [[связи]]. Ответы ассистента автоматически не сохраняются.</div><div class="smart-list" id="captureHistoryList211"></div>`;
    const root=$('#captureHistoryList211');
    (d.items||[]).forEach(x=>{const c=document.createElement('div');c.className='capture-card';c.innerHTML=`<span class="capture-type">${escapeHtml(x.kind||'знание')}</span><b>${escapeHtml(x.title)}</b><small>${escapeHtml((x.tags||[]).slice(0,5).join(' · '))}</small>`;c.style.cursor='pointer';c.onclick=()=>{hideModal($('#smartModal'));hideModal($('#aiModal'));openNote(x.id)};root.appendChild(c)});
    if(!(d.items||[]).length) root.innerHTML='<div class="muted-block">Пока ничего не захвачено. Включите Auto-Capture и продолжайте обычный разговор.</div>';
  }catch(e){box.textContent=e.message}
}
setTimeout(initAutoCapture211,0);

// 2.12 relation overlay controls
setTimeout(()=>{const c=$('#relationModalClose212'),m=$('#relationModal212');if(c)c.onclick=hideRelationModal212;if(m)m.addEventListener('click',e=>{if(e.target===m)hideRelationModal212()});document.addEventListener('keydown',e=>{if(e.key==='Escape'&&m&&!m.classList.contains('hidden')){e.stopPropagation();hideRelationModal212();}});},0);
