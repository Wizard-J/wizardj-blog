const el = id => document.getElementById(id);
const format = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'medium',
});
const date = value => format.format(new Date(value));
const sources = { current: '原订阅节点', historical: '历史合格节点', dns: '域名 DNS', template: '订阅模板', official_sample: '官方网段分层抽样' };
const reasons = { connectivity: '连通性检查失败', jitter: '延迟波动', slow: '超过延迟阈值', relative: '相对延迟过高' };

function fillList(id, rows) {
  el(id).replaceChildren(...rows.map(([label, value]) => {
    const item = document.createElement('li');
    const name = document.createElement('span');
    const detail = document.createElement('span');
    name.textContent = label;
    detail.textContent = value;
    item.append(name, detail);
    return item;
  }));
}

const operationLabels = {
  idle: '测速完成后可手动发布本轮结果。',
  testing: '服务器正在测速…',
  ready: '本轮已验证，可以发布。结果一小时内有效。',
  held: '合格候选不足，本轮保留现有订阅。',
  publishing: '正在发布并校验订阅…',
  published: '本轮订阅已发布并通过 HTTPS 校验。',
  failed: '本轮未完成。请查看服务器日志或重新测速。',
};
let preparedRun;
let poll;
let waitingUntil = 0;

async function refresh() {
  el('refresh').disabled = true;
  el('notice').textContent = '正在读取状态…';
  try {
    const response = await fetch('status.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('status unavailable');
    const data = await response.json();
    const operationResponse = await fetch('operation.json', { cache: 'no-store' }).catch(() => null);
    const operation = operationResponse?.ok ? await operationResponse.json() : null;
    const busy = ['testing', 'publishing'].includes(operation?.state);
    if (busy || !['idle', 'ready', 'published', 'held', 'failed'].includes(operation?.state)) preparedRun = undefined;
    else preparedRun = operation.state === 'ready' ? operation.run : undefined;
    if (busy || operation?.updated_at * 1000 >= waitingUntil - 15000) waitingUntil = 0;
    const waiting = Date.now() < waitingUntil;
    el('test').disabled = !operation || busy || waiting;
    el('publish').disabled = !preparedRun || waiting;
    el('operation').textContent = waiting ? '请求已提交，等待服务器启动任务…'
      : operationLabels[operation?.state] || '暂时无法读取操作状态。';
    clearTimeout(poll);
    if (busy || waiting) poll = setTimeout(refresh, 4000);
    const running = ['activating', 'active'].includes(data.service);
    const state = running ? '运行中' : data.result === 'success' ? '已结束' : data.result ? '运行失败' : '暂无记录';
    el('run').textContent = data.started ? `${state} · ${date(data.started * 1000)}` : state;
    el('next').textContent = data.timer_active ? data.next_run || '待计划' : '计划任务未启用';
    el('published').textContent = data.published ? '已发布并通过 HTTPS 校验' : '本轮暂无发布回执';
    el('totals').textContent = data.totals ? `${data.totals.passed} / ${data.totals.tested}` : '暂无汇总';
    if (operation?.summary) {
      data.totals = operation.summary.totals;
      data.selected = operation.summary.selected;
      data.sources = operation.summary.sources;
      data.published = operation.summary.published;
      el('run').textContent = operationLabels[operation.state];
      el('published').textContent = data.published ? '已发布并通过 HTTPS 校验' : '本轮暂无发布回执';
      el('publish').title = preparedRun ? '发布当前已验证的测速结果' : '';
      el('totals').textContent = data.totals ? `${data.totals.passed} / ${data.totals.tested}` : '暂无汇总';
    }
    el('selected').textContent = data.selected ? `${data.selected.count} 个` : '暂无记录';
    el('latency').textContent = data.selected ? `${data.selected.client_min}–${data.selected.client_max} ms` : '暂无记录';
    fillList('sources', Object.entries(sources).map(([key, label]) => {
      const row = data.sources[key];
      return [label, row ? `通过 ${row.passed} / 测试 ${row.tested}` : '暂无记录'];
    }));
    fillList('rejections', Object.entries(reasons).map(([key, label]) =>
      [label, data.totals ? `${data.totals[key]} 个` : '暂无记录']));
    el('notice').textContent = `采集于 ${date(data.sampled_at)}（北京时间）`;
  } catch {
    el('test').disabled = true;
    el('publish').disabled = true;
    el('notice').textContent = '暂时无法读取状态。下方如有数据，为上次读取结果。';
  } finally {
    el('refresh').disabled = false;
  }
}

async function startAction(path) {
  el('test').disabled = true;
  el('publish').disabled = true;
  try {
    const result = await fetch(path, { method: 'POST', headers: { 'X-WizardJ-CF-Action': '1' } });
    if (!result.ok) throw new Error(result.status === 409 ? '任务正在运行，或本轮已经过期。' : '操作未提交。');
    waitingUntil = Date.now() + 15000;
    await refresh();
  } catch (error) {
    await refresh();
    el('operation').textContent = error.message;
  }
}

el('test').addEventListener('click', () => startAction('test'));
el('publish').addEventListener('click', () => {
  if (preparedRun && window.confirm('发布当前已验证的测速结果并更新订阅？')) {
    startAction('publish/' + preparedRun);
  }
});
el('refresh').addEventListener('click', refresh);
refresh();

const sourceRole = new URLSearchParams(location.search).get('persona');
if (sourceRole === 'munger' || sourceRole === 'wizard') {
  const back = document.getElementById('back-to-tools');
  back.href = '/assistant/?panel=tools&persona=' + sourceRole;
}
