const el = id => document.getElementById(id);
const format = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'medium',
});
const date = value => format.format(new Date(value));
const sources = { current: '原订阅节点', dns: '域名 DNS', template: '订阅模板', official_sample: '官方网段分层抽样' };
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

async function refresh() {
  el('refresh').disabled = true;
  el('notice').textContent = '正在读取状态…';
  try {
    const response = await fetch('status.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('status unavailable');
    const data = await response.json();
    const running = ['activating', 'active'].includes(data.service);
    const state = running ? '运行中' : data.result === 'success' ? '已结束' : data.result ? '运行失败' : '暂无记录';
    el('run').textContent = data.started ? `${state} · ${date(data.started * 1000)}` : state;
    el('next').textContent = data.timer_active ? data.next_run || '待计划' : '计划任务未启用';
    el('publish').textContent = data.published ? '已发布并通过 HTTPS 校验' : '本轮暂无发布回执';
    el('totals').textContent = data.totals ? `${data.totals.passed} / ${data.totals.tested}` : '暂无汇总';
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
    el('notice').textContent = '暂时无法读取状态。下方如有数据，为上次读取结果。';
  } finally {
    el('refresh').disabled = false;
  }
}

el('refresh').addEventListener('click', refresh);
refresh();
