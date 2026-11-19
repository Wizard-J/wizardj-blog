const el = id => document.getElementById(id);
const timezone = 'Asia/Shanghai';
const format = new Intl.DateTimeFormat('zh-CN', {
  timeZone: timezone, dateStyle: 'medium', timeStyle: 'medium',
});
const date = value => format.format(new Date(value));
const states = {
  active: '运行中', inactive: '已停止', failed: '运行失败',
  activating: '启动中', deactivating: '停止中',
};

async function refresh() {
  el('refresh').disabled = true;
  el('notice').textContent = '正在读取状态…';
  try {
    const response = await fetch('status.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('status unavailable');
    const data = await response.json();
    el('state').textContent = states[data.state] || '未知';
    el('model').textContent = data.model || '未读取到';
    el('project').textContent = data.project || '未读取到';
    el('generated').textContent = data.last_generated ? date(data.last_generated.time) : '暂无记录';
    el('accepted').textContent = data.last_accepted ? date(data.last_accepted.time) : '暂无记录';
    el('failure').textContent = data.last_failure
      ? `${data.last_failure.reason} · ${date(data.last_failure.time)}` : '暂无记录';
    el('events').replaceChildren(...data.events.map(event => {
      const item = document.createElement('li');
      const time = document.createElement('time');
      const detail = document.createElement('span');
      time.className = 'event-time';
      time.dateTime = event.time;
      time.textContent = date(event.time);
      detail.textContent = event.reason ? `${event.label} · ${event.reason}` : event.label;
      item.append(time, detail);
      return item;
    }));
    el('empty').hidden = data.events.length > 0;
    el('notice').textContent = `采集于 ${date(data.sampled_at)}（北京时间）`;
  } catch {
    el('notice').textContent = '暂时无法读取状态，请稍后重试。下方如有数据，为上次读取结果。';
  } finally {
    el('refresh').disabled = false;
  }
}

el('refresh').addEventListener('click', refresh);
refresh();

const sourceRole = new URLSearchParams(location.search).get('persona');
if (sourceRole === 'munger' || sourceRole === 'wizard') {
  const back = document.getElementById('back-to-tools');
  back.href = '/assistant/?panel=tools&persona=' + sourceRole;
}
