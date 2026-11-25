/* Same-origin research client. The task id in the URL makes refresh and sharing recoverable. */
'use strict';
const $ = id => document.getElementById(id);
const state = { tasks: [], id: null, prompt: '', steps: [], selected: -1, status: '', report: '', timer: null, generation: 0, submitting: false, tab: 'process', detailKey: '' };
const selectedTaskIds = new Set();
const labels = { pending: '等待执行', running: '正在执行', done: '已完成', error: '执行中断' };
const escapeText = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function safeMarkdown(text) {
  if (!window.marked || !window.DOMPurify) return '<pre>' + escapeText(text) + '</pre>';
  return DOMPurify.sanitize(marked.parse(String(text || '')), { FORBID_TAGS: ['style', 'form', 'input', 'button'], FORBID_ATTR: ['style'] });
}
function richText(element, text) {
  element.innerHTML = safeMarkdown(text);
  element.querySelectorAll('a').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
}
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, signal: AbortSignal.timeout(20000) });
  if (!response.ok) {
    let body; try { body = await response.json(); } catch {}
    const error = new Error(typeof body?.detail === 'string' ? body.detail : `请求失败（${response.status}），请稍后重试。`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}
function notice(text = '') { $('notice').textContent = text; $('notice').hidden = !text; }
function setBusy(busy) {
  state.submitting = busy;
  $('submitButton').disabled = busy;
  $('submitLabel').textContent = busy ? '正在提交' : '开始研究';
  $('promptInput').readOnly = busy;
}
function closeSidebar() { document.body.classList.remove('sidebar-open'); }
function setPage(history = false) {
  $('composeSection').hidden = history;
  $('historySection').hidden = !history;
  $('newResearch').classList.toggle('active', !history);
  $('historyNav').classList.toggle('active', history);
  $('pageLabel').textContent = history ? '研究记录' : '新建研究';
  $('emptyState').hidden = history || !!state.id;
  closeSidebar();
}
function stopPolling() { clearTimeout(state.timer); state.timer = null; }
function resetWorkspace() {
  state.generation++; stopPolling();
  Object.assign(state, { id: null, prompt: '', steps: [], selected: -1, status: '', report: '', detailKey: '' });
  history.replaceState(null, '', location.pathname);
  $('taskPanel').hidden = true; $('promptInput').value = '';
  notice(); setPage(); renderHistory(); $('promptInput').focus();
}
function relativeDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
}
function renderHistory() {
  $('historyCount').textContent = state.tasks.length || '';
  $('recentTasks').innerHTML = state.tasks.length ? state.tasks.slice(0, 20).map(t => `<button class="recent-task ${t.id === state.id ? 'selected' : ''}" data-id="${escapeText(t.id)}" title="${escapeText(t.prompt)}"><span class="task-file">▤</span><span class="task-name">${escapeText(t.prompt)}</span></button>`).join('') : '<p class="sidebar-empty muted">还没有研究记录。<br>从你的第一个问题开始。</p>';
  const query = $('historySearch').value.toLowerCase();
  const tasks = state.tasks.filter(t => t.prompt.toLowerCase().includes(query));
  const selectable = tasks.filter(t => t.status !== 'running');
  for (const id of selectedTaskIds) {
    if (!state.tasks.some(t => t.id === id && t.status !== 'running')) selectedTaskIds.delete(id);
  }
  $('historyList').innerHTML = tasks.length ? tasks.map(t => `<div class="history-row ${selectedTaskIds.has(t.id) ? 'checked' : ''}" data-row-id="${escapeText(t.id)}"><input type="checkbox" class="task-checkbox" data-select-id="${escapeText(t.id)}" aria-label="选择研究记录：${escapeText(t.prompt)}" title="${t.status === 'running' ? '研究运行中，结束后可选择' : '选择此记录'}" ${selectedTaskIds.has(t.id) ? 'checked' : ''} ${t.status === 'running' ? 'disabled' : ''}><button class="history-card" data-id="${escapeText(t.id)}"><span><strong>${escapeText(t.prompt)}</strong><small>${relativeDate(t.created_at)}</small></span><span class="status-badge ${escapeText(t.status)}">${labels[t.status] || escapeText(t.status)}</span></button></div>`).join('') : '<p class="muted sidebar-empty">没有找到研究记录。</p>';
  const selectedVisible = selectable.filter(t => selectedTaskIds.has(t.id)).length;
  $('selectAllTasks').disabled = !selectable.length;
  $('selectAllTasks').checked = !!selectable.length && selectedVisible === selectable.length;
  $('selectAllTasks').indeterminate = selectedVisible > 0 && selectedVisible < selectable.length;
  $('selectionCount').textContent = selectedTaskIds.size ? `已选择 ${selectedTaskIds.size} 条` : '未选择记录';
  $('deleteSelected').hidden = $('clearSelection').hidden = !selectedTaskIds.size;
  $('deleteSelected').textContent = `删除所选（${selectedTaskIds.size}）`;
}

let pendingDeleteIds = [];
let deleting = false;
function hideContextMenu() { $('historyContextMenu').hidden = true; }
function requestDelete(ids) {
  const tasks = ids.map(id => state.tasks.find(t => t.id === id));
  if (!tasks.length || tasks.some(t => !t || t.status === 'running')) return;
  pendingDeleteIds = [...ids];
  hideContextMenu();
  $('deleteDialogTitle').textContent = tasks.length === 1 ? '删除这条研究记录？' : `删除这 ${tasks.length} 条研究记录？`;
  $('deleteTaskName').textContent = tasks.map(t => t.prompt).join('\n');
  $('deleteError').hidden = true;
  $('deleteDialog').showModal();
}
function openContextMenu(event, row) {
  event.preventDefault();
  const id = row.dataset.rowId;
  const task = state.tasks.find(t => t.id === id);
  if (!task) return;
  if (!selectedTaskIds.has(id)) {
    selectedTaskIds.clear();
    if (task.status !== 'running') selectedTaskIds.add(id);
    renderHistory();
  }
  const menu = $('historyContextMenu');
  $('contextDelete').disabled = task.status === 'running';
  $('contextDelete').textContent = task.status === 'running' ? '运行中，暂不可删除' : selectedTaskIds.size > 1 ? `删除所选 ${selectedTaskIds.size} 条记录` : '删除';
  menu.hidden = false;
  const rect = row.getBoundingClientRect();
  const x = event.type === 'keydown' ? rect.left + 30 : event.clientX;
  const y = event.type === 'keydown' ? rect.top + 30 : event.clientY;
  menu.style.left = Math.max(8, Math.min(x, innerWidth - menu.offsetWidth - 8)) + 'px';
  menu.style.top = Math.max(8, Math.min(y, innerHeight - menu.offsetHeight - 8)) + 'px';
  if (!$('contextDelete').disabled) $('contextDelete').focus();
}
async function confirmDelete() {
  if (!pendingDeleteIds.length || deleting) return;
  const ids = [...pendingDeleteIds];
  deleting = true;
  $('confirmDelete').disabled = $('cancelDelete').disabled = true;
  $('confirmDelete').textContent = '正在删除…';
  $('deleteError').hidden = true;
  try {
    await api('/tasks/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({task_ids:ids})});
    state.tasks = state.tasks.filter(t => !ids.includes(t.id));
    ids.forEach(id => selectedTaskIds.delete(id));
    if (ids.includes(state.id)) { resetWorkspace(); setPage(true); }
    renderHistory();
    $('deleteDialog').close();
    pendingDeleteIds = [];
    await loadHistory();
  } catch (error) {
    $('deleteError').textContent = '删除失败：' + error.message + (error.status === 404 ? ' 请取消后刷新记录再试。' : '');
    $('deleteError').hidden = false;
    if (error.status === 404 || error.status === 409) await loadHistory();
  } finally {
    deleting = false;
    $('confirmDelete').disabled = $('cancelDelete').disabled = false;
    $('confirmDelete').textContent = '确认删除';
  }
}
async function loadHistory() {
  try { state.tasks = (await api('/tasks')).tasks; renderHistory(); }
  catch { $('recentTasks').innerHTML = '<p class="sidebar-empty muted">记录加载失败。<button id="retryHistory" class="secondary">重试</button></p>'; }
}
function stepName(title, index) {
  const lower = title.toLowerCase();
  if (lower.includes('tavily')) return '检索网页资料';
  if (lower.includes('arxiv')) return '匹配学术论文';
  if (lower.includes('final') || lower.includes('comprehensive markdown')) return '生成最终报告';
  if (lower.includes('editor') || lower.includes('review') || lower.includes('revise')) return '审阅与修订';
  if (lower.includes('outline')) return '构建报告大纲';
  if (lower.includes('writer') || lower.includes('draft')) return '撰写研究报告';
  if (lower.includes('synthesi') || lower.includes('rank')) return '整合研究证据';
  return /[\u4e00-\u9fff]/.test(title) ? title : `研究步骤 ${index + 1}`;
}
function renderDetails() {
  const step = state.steps[state.selected];
  if (!step) {
    if (state.status === 'error') {
      $('detailTitle').textContent = '研究未能继续'; $('detailAgent').textContent = '';
      $('stepDetail').innerHTML = '<div class="detail-placeholder">此次研究已中断。<br>请查看上方错误信息，或点击“再次研究”重新提交。</div>';
      return;
    }
    $('detailTitle').textContent = '制定研究计划'; $('detailAgent').textContent = '规划智能体';
    $('stepDetail').innerHTML = '<div class="detail-placeholder">正在分析研究问题并制定执行计划。<br>模型响应后，这里会展示每一步研究过程。</div>';
    return;
  }
  const key = JSON.stringify([state.selected, step]);
  if (key === state.detailKey) return;
  state.detailKey = key;
  $('detailTitle').textContent = stepName(step.title, state.selected);
  $('detailAgent').textContent = labels[step.status] || '';
  const details = (step.substeps || []).map(s => s.content).join('\n\n');
  if (details) richText($('stepDetail'), details);
  else {
    $('stepDetail').replaceChildren();
    const title = document.createElement('p'); title.textContent = step.title;
    const description = document.createElement('p'); description.className = 'muted';
    description.textContent = step.status === 'running' ? '智能体正在调用模型和研究工具，完成后将展示本步骤输出。' : step.description;
    $('stepDetail').append(title, description);
  }
}
function showTab(tab) {
  state.tab = tab;
  const report = tab === 'report';
  $('processView').hidden = report; $('reportView').hidden = !report;
  ['process','report'].forEach(name => {
    $(name + 'Tab').classList.toggle('selected', name === tab);
    $(name + 'Tab').setAttribute('aria-selected', String(name === tab));
  });
}
function renderTask() {
  $('taskPanel').hidden = false; $('emptyState').hidden = true;
  $('taskTitle').textContent = state.prompt;
  $('taskBadge').textContent = state.status === 'running' ? (state.steps.length ? '研究进行中' : '正在规划') : labels[state.status] || '读取中';
  $('taskBadge').className = 'status-badge ' + state.status;
  const done = state.steps.filter(s => s.status === 'done').length;
  const percent = state.steps.length ? Math.round(done / state.steps.length * 100) : 0;
  $('progressBar').style.width = percent + '%';
  $('progressTrack').setAttribute('aria-valuenow', percent);
  $('stepCount').textContent = state.steps.length ? `${done} / ${state.steps.length} 步骤` : '';
  $('progressCaption').textContent = state.status === 'done' ? '研究已完成，报告已保存' : state.status === 'error' ? '研究中断，已保留完成的步骤' : state.steps.length ? '研究工作正在有序进行' : '正在制定研究计划';
  if (state.selected < 0 && state.steps.length) {
    state.selected = state.steps.findIndex(s => s.status === 'running');
    if (state.selected < 0) state.selected = 0;
  }
  $('timeline').innerHTML = state.steps.length ? state.steps.map((s, i) => `<button class="step ${escapeText(s.status)} ${state.selected === i ? 'selected' : ''}" data-step="${i}" title="${escapeText(s.title)}" aria-pressed="${state.selected === i}"><span class="step-indicator">${s.status === 'done' ? '✓' : s.status === 'error' ? '!' : ''}</span><span class="step-text"><span class="step-name">${stepName(s.title, i)}</span><span class="step-state">${labels[s.status] || ''}</span></span></button>`).join('') : '<p class="muted sidebar-empty">计划生成后将自动开始研究。</p>';
  $('reportTab').disabled = !state.report;
  renderDetails();
}
async function pollTask(id, generation) {
  try {
    const task = await api('/task_status/' + encodeURIComponent(id));
    const progress = await api('/task_progress/' + encodeURIComponent(id));
    if (generation !== state.generation) return;
    const previousSteps = state.steps;
    state.steps = progress.steps || task.result?.history || [];
    if (state.selected < 0 || previousSteps[state.selected]?.status === 'running') {
      const running = state.steps.findIndex(s => s.status === 'running');
      if (running >= 0) state.selected = running;
    }
    state.status = task.status; state.prompt = task.prompt || state.prompt;
    const report = task.result?.html_report || '';
    if (report !== state.report) {
      state.report = report; richText($('reportContent'), report);
      if (report) showTab('report');
    }
    renderTask();
    notice(task.result?.error || '');
    if (state.status === 'running') state.timer = setTimeout(() => pollTask(id, generation), 2000);
    else loadHistory();
  } catch (error) {
    if (generation !== state.generation) return;
    if (error.status === 404) {
      notice('此研究记录不存在。请从侧栏选择其他记录，或新建研究。');
      state.status = 'error'; renderTask(); return;
    }
    notice('暂时无法获取研究进度：' + error.message + ' 将在 5 秒后自动重试。');
    state.timer = setTimeout(() => pollTask(id, generation), 5000);
  }
}
function openTask(id, prompt = '') {
  stopPolling(); state.generation++;
  Object.assign(state, {id, prompt, steps: [], selected: -1, status: 'running', report: '', detailKey: ''});
  history.replaceState(null, '', '?task=' + encodeURIComponent(id));
  setPage(); notice(); showTab('process'); renderTask(); renderHistory();
  $('taskPanel').scrollIntoView({behavior: 'smooth', block: 'start'});
  pollTask(id, state.generation);
}
async function submit(event) {
  event.preventDefault();
  if (state.submitting) return;
  const prompt = $('promptInput').value.trim();
  if (!prompt) { $('promptInput').focus(); notice('请输入你想研究的主题。'); return; }
  setBusy(true); notice();
  try {
    const data = await api('/generate_report', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt})});
    openTask(data.task_id, prompt); loadHistory();
  } catch (error) { notice('研究提交失败：' + error.message); }
  finally { setBusy(false); }
}
function download(extension) {
  if (!state.report) return;
  const content = extension === 'md' ? state.report : '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Research 研究报告</title><style>body{max-width:850px;margin:50px auto;padding:0 24px;font:16px/1.9 sans-serif;color:#222}pre,table{overflow:auto}img{max-width:100%}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px}a{color:#333}</style></head><body>' + safeMarkdown(state.report) + '</body></html>';
  const url = URL.createObjectURL(new Blob([content], {type: extension === 'md' ? 'text/markdown;charset=utf-8' : 'text/html;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = 'research_' + state.id + '.' + extension;
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('researchForm').addEventListener('submit', submit);
$('promptInput').addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') $('researchForm').requestSubmit(); });
$('newResearch').addEventListener('click', resetWorkspace);
$('workspaceHome').addEventListener('click', () => {
  if (!$('composeSection').hidden && !state.id) return;
  selectedTaskIds.clear();
  hideContextMenu();
  resetWorkspace();
  window.scrollTo({top: 0, behavior: 'smooth'});
});
$('historyNav').addEventListener('click', () => { stopPolling(); state.generation++; $('taskPanel').hidden = true; setPage(true); notice(); loadHistory(); });
$('historySearch').addEventListener('input', () => { selectedTaskIds.clear(); hideContextMenu(); renderHistory(); });
$('reusePrompt').addEventListener('click', () => { const prompt = state.prompt; resetWorkspace(); $('promptInput').value = prompt; });
$('processTab').addEventListener('click', () => showTab('process'));
$('reportTab').addEventListener('click', () => showTab('report'));
$('timeline').addEventListener('click', event => { const button = event.target.closest('[data-step]'); if (button) { state.selected = Number(button.dataset.step); renderTask(); } });
for (const container of [$('recentTasks'), $('historyList')]) container.addEventListener('click', event => {
  if (event.target.closest('#retryHistory')) return loadHistory();
  if (event.target.closest('[data-select-id]')) return;
  const button = event.target.closest('[data-id]'); if (button) openTask(button.dataset.id, state.tasks.find(t => t.id === button.dataset.id)?.prompt);
});

$('historyList').addEventListener('change', event => {
  const checkbox = event.target.closest('[data-select-id]');
  if (!checkbox) return;
  const id = checkbox.dataset.selectId;
  if (checkbox.checked) selectedTaskIds.add(id); else selectedTaskIds.delete(id);
  hideContextMenu(); renderHistory();
  $('historyList').querySelector(`[data-select-id="${CSS.escape(id)}"]`)?.focus();
});
$('historyList').addEventListener('contextmenu', event => {
  const row = event.target.closest('[data-row-id]');
  if (row) openContextMenu(event, row);
});
$('historyList').addEventListener('keydown', event => {
  const row = event.target.closest('[data-row-id]');
  if (row && (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))) openContextMenu(event, row);
});
$('selectAllTasks').addEventListener('change', event => {
  const query = $('historySearch').value.toLowerCase();
  const ids = state.tasks.filter(t => t.status !== 'running' && t.prompt.toLowerCase().includes(query)).map(t => t.id);
  ids.forEach(id => event.target.checked ? selectedTaskIds.add(id) : selectedTaskIds.delete(id));
  hideContextMenu(); renderHistory();
});
$('clearSelection').addEventListener('click', () => { selectedTaskIds.clear(); hideContextMenu(); renderHistory(); });
$('deleteSelected').addEventListener('click', () => requestDelete([...selectedTaskIds]));
$('contextDelete').addEventListener('click', () => requestDelete([...selectedTaskIds]));
document.addEventListener('pointerdown', event => { if (!event.target.closest('#historyContextMenu')) hideContextMenu(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') hideContextMenu(); });
window.addEventListener('resize', hideContextMenu);
window.addEventListener('scroll', hideContextMenu, true);

$('confirmDelete').addEventListener('click', confirmDelete);
$('cancelDelete').addEventListener('click', () => { $('deleteDialog').close(); pendingDeleteIds = []; });
$('deleteDialog').addEventListener('cancel', event => { if (deleting) event.preventDefault(); else pendingDeleteIds = []; });
document.querySelectorAll('[data-topic]').forEach(button => button.addEventListener('click', () => { $('promptInput').value = button.dataset.topic; $('promptInput').focus(); }));
document.querySelectorAll('.sidebar-toggle').forEach(button => button.addEventListener('click', () => { if (innerWidth <= 850) document.body.classList.toggle('sidebar-open'); else document.body.classList.toggle('sidebar-collapsed'); }));
document.addEventListener('click', event => { if (innerWidth <= 850 && !event.target.closest('.sidebar,.sidebar-toggle')) closeSidebar(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeSidebar(); });
$('downloadMd').addEventListener('click', () => download('md'));
$('downloadHtml').addEventListener('click', () => download('html'));
$('copyReport').addEventListener('click', async () => { try { await navigator.clipboard.writeText(state.report); $('copyReport').textContent = '已复制'; setTimeout(() => $('copyReport').textContent = '复制全文', 2000); } catch { notice('浏览器未允许复制，请下载报告后复制。'); } });
api('/config').then(config => $('modelName').textContent = config.model).catch(() => $('modelName').textContent = '研究模型');
loadHistory();
const initialTask = new URLSearchParams(location.search).get('task');
if (initialTask) openTask(initialTask);
