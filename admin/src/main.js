import { embedDashboard } from '@superset-ui/embedded-sdk';
import './style.css';
import { initBenchmark } from './benchmark-ui.js';

const modes = [
  { name: 'Superset', route: 'Admin → API (guest token) → Superset → ClickHouse', hint: 'Создайте dashboard в Superset, включите Embed dashboard и укажите SUPERSET_EMBEDDED_DASHBOARD_ID в .env.' },
  { name: 'Grafana Infinity', key: 'grafanaInfinity', route: 'Admin → Grafana Infinity → telemetry-api → ClickHouse', hint: 'Создайте dashboard с datasource Telemetry API Infinity и укажите GRAFANA_INFINITY_DASHBOARD_URL в .env.' },
  { name: 'Grafana Direct', key: 'grafanaDirect', route: 'Admin → Grafana ClickHouse → ClickHouse', hint: 'Создайте dashboard с datasource ClickHouse Direct и укажите GRAFANA_DIRECT_DASHBOARD_URL в .env.' },
];
document.querySelector('#app').innerHTML = `
  <header><div class="brand"><span class="mark">T</span><span>TELEMETRY LAB<span class="local">LOCAL</span></span></div><a href="/api/config" target="_blank" rel="noopener">Конфигурация ↗</a></header>
  <main><div class="eyebrow">CLICKHOUSE · COMPARISON WORKSPACE</div><h1>Telemetry Benchmark</h1>
  <p class="intro">Один набор телеметрии. Три пути к данным.</p>
  <nav aria-label="Сценарий"><div role="tablist"></div><div class="dashboard-actions"><button id="show" class="secondary">Показать дашборд</button><button id="reload" class="secondary">↻ Перезагрузить</button></div></nav>
  <div id="benchmark-controls"></div>
  <section class="workspace"><div class="toolbar"><span id="route"></span><span id="timing" role="status"></span></div><div id="dashboard"></div></section>
  <div id="benchmark-results"></div>
  <footer><span>Внешняя ClickHouse · только чтение</span><span>iframe load ≠ готовность. DOM-готовность приблизительная.</span></footer></main>`;
const tabs = document.querySelector('[role=tablist]');
const mount = document.querySelector('#dashboard');
const timing = document.querySelector('#timing');
const variantIds = ['superset', 'infinity', 'direct'];
const params = new URLSearchParams(location.search);
let selected = Math.max(0, variantIds.indexOf(params.get('scenario'))), generation = 0, embedded;
document.documentElement.classList.toggle('benchmark-run', params.get('benchmark') === '1');

function message(title, text, error = false) {
  mount.replaceChildren();
  const block = document.createElement('div');
  block.className = `placeholder ${error ? 'error' : ''}`;
  const icon = document.createElement('div'); icon.className = 'placeholder-icon'; icon.textContent = error ? '!' : '▥';
  const heading = document.createElement('h2'); heading.textContent = title;
  const body = document.createElement('p'); body.textContent = text;
  block.append(icon, heading, body); mount.append(block);
}

async function json(url) {
  const response = await fetch(url, { cache: 'no-store' });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`);
  return data;
}

async function show(index) {
  mount.closest('.workspace').classList.remove('idle');
  selected = index;
  const current = ++generation;
  embedded?.unmount(); embedded = undefined; mount.replaceChildren();
  [...tabs.children].forEach((tab, i) => { tab.setAttribute('aria-selected', String(i === index)); tab.classList.toggle('active', i === index); });
  document.querySelector('#route').textContent = modes[index].route;
  timing.textContent = '';
  message('Подключение…', 'Получаем конфигурацию стенда');
  try {
    const config = await json('/api/config');
    if (current !== generation) return;
    const mode = modes[index];
    if (index === 0) {
      if (!config.supersetConfigured) { message('Дашборд ещё не настроен', mode.hint); return; }
      const data = await json('/api/superset/embed');
      if (current !== generation) return;
      mount.replaceChildren();
      const target = document.createElement('div'); target.className = 'superset-mount'; mount.append(target);
      const start = performance.now();
      const observer = new MutationObserver(() => {
        const frame = target.querySelector('iframe');
        if (frame) {
          frame.addEventListener('load', () => { if (current === generation) timing.textContent = `iframe load: ${((performance.now() - start) / 1000).toFixed(2)} с · не готовность графиков`; }, { once: true });
          observer.disconnect();
        }
      });
      observer.observe(target, { childList: true, subtree: true });
      let initialToken = data.guestToken;
      // SDK refreshes the real guest token automatically using this callback.
      const instance = await embedDashboard({ id: data.id, supersetDomain: data.supersetDomain, mountPoint: target,
        fetchGuestToken: async () => {
          if (initialToken) { const token = initialToken; initialToken = undefined; return token; }
          return (await json('/api/superset/embed')).guestToken;
        },
        dashboardUiConfig: { hideTitle: true }, referrerPolicy: 'strict-origin-when-cross-origin' });
      if (current !== generation) { instance.unmount(); return; }
      embedded = instance;
      timing.textContent = 'Embedded SDK · ожидание iframe load…';
    } else {
      if (!config[mode.key]) { message('Дашборд ещё не настроен', mode.hint); return; }
      const url = new URL(config[mode.key]);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Dashboard URL должен использовать http или https.');
      mount.replaceChildren();
      const frame = document.createElement('iframe'); frame.title = mode.name; frame.referrerPolicy = 'strict-origin-when-cross-origin';
      const start = performance.now(); timing.textContent = 'Ожидание iframe load…';
      frame.onload = () => { if (current === generation) timing.textContent = `iframe load: ${((performance.now() - start) / 1000).toFixed(2)} с · не готовность графиков`; };
      frame.src = url.href; mount.append(frame);
    }
  } catch (error) { if (current === generation) message('Не удалось открыть дашборд', error.message, true); }
}
function stopDashboard() {
  ++generation;
  embedded?.unmount(); embedded = undefined;
  timing.textContent = '';
  mount.closest('.workspace').classList.add('idle');
  message('Дашборд остановлен', 'Измерение выполняет отдельный Chromium runner. В этой вкладке дополнительные BI-запросы не запускаются.');
}
function select(index) {
  stopDashboard(); selected = index;
  [...tabs.children].forEach((tab, i) => { tab.setAttribute('aria-selected', String(i === index)); tab.classList.toggle('active', i === index); });
  document.querySelector('#route').textContent = modes[index].route;
  message('Сценарий выбран', 'Нажмите «Показать дашборд» для просмотра или «Запустить измерение» для браузерного теста.');
}
modes.forEach((mode, index) => { const button = document.createElement('button'); button.textContent = mode.name; button.setAttribute('role', 'tab'); button.dataset.scenario = variantIds[index]; button.onclick = () => select(index); tabs.append(button); });
document.querySelector('#show').onclick = () => show(selected);
document.querySelector('#reload').onclick = () => show(selected);
select(selected);
if (params.get('autostart') === '1') show(selected);
if (params.get('benchmark') !== '1') initBenchmark(() => variantIds[selected], stopDashboard);
