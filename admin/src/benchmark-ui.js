const names = { superset: 'Superset', infinity: 'Grafana Infinity', direct: 'Grafana Direct' };
const errors = {
  filtered_query: 'Обнаружен дополнительный фильтр', unsupported_query: 'Запрос или сортировка не соответствует benchmark',
  row_count: 'Raw Records содержит не 100 000 строк или неверный limit', different_data: 'Данные вариантов различаются',
  query_error: 'Ошибка ответа BI / SQL', network_error: 'Ошибка сети', timeout: 'Таймаут', embed_error: 'Ошибка embedding',
  missing_panels: 'Не найдены обе панели', invalid_data: 'Непустые сопоставимые данные не подтверждены',
  render_error: 'Панель отображает ошибку', render_unobservable: 'Не удалось подтвердить DOM-готовность',
  runner_lost: 'Runner потерян / задание отменено', preflight_failed: 'Предварительная проверка не пройдена', internal_error: 'Ошибка runner',
};
const ms = value => value == null ? '—' : `${(value / 1000).toFixed(3)} с`;
const date = value => new Date(value).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' }) + ' МСК';

async function api(path, body) {
  const response = await fetch('/api/benchmark/jobs' + path, {
    cache: 'no-store', ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : `Ошибка HTTP ${response.status}`);
  return value;
}

function table(headers, rows) {
  const wrapper = document.createElement('div'); wrapper.className = 'table-scroll';
  const element = document.createElement('table'); const head = document.createElement('thead'); const tr = document.createElement('tr');
  headers.forEach(text => { const th = document.createElement('th'); th.textContent = text; tr.append(th); }); head.append(tr);
  const body = document.createElement('tbody');
  rows.forEach(row => { const tr = document.createElement('tr'); row.forEach(text => { const td = document.createElement('td'); td.textContent = text ?? '—'; tr.append(td); }); body.append(tr); });
  element.append(head, body); wrapper.append(element); return wrapper;
}

export function initBenchmark(selectedVariant, stopDashboard) {
  document.querySelector('#benchmark-controls').innerHTML = `
    <section class="bench-controls"><div class="bench-heading"><strong>Браузерное измерение</strong><span id="runner-status" role="status">Проверка runner…</span></div>
    <div class="bench-inputs">
      <label>Варианты<select id="bench-variants"><option value="selected">Выбранная вкладка<option value="selected">Выбранная вкладка</option><option value="all">Все три последовательно</option></select></label>
      <label>Повторения<input id="bench-repeats" type="number" min="1" max="50" value="1" list="repeat-options"><datalist id="repeat-options"><option value="1"><option value="10"><option value="30"></datalist></label>
      <label>Контекст браузера<select id="bench-cache"><option value="cold">Новый контекст</option><option value="warm">Прогретый контекст</option><option value="both">Оба режима отдельно</option></select></label>
      <label>Таймаут, с<input id="bench-timeout" type="number" min="10" max="300" value="120"></label>
      <button id="bench-start" class="primary">Запустить измерение</button>
    </div><p class="bench-note">Перед серией проверяются все три варианта: одинаковые агрегаты и ровно 100 000 исходных строк. Серверные кэши не очищаются. DOM-готовность — приблизительная оценка, не GPU paint.</p>
    <p id="bench-message" role="status"></p></section>`;
  document.querySelector('#benchmark-results').innerHTML = `
    <section class="bench-results"><div class="bench-heading"><strong>Результаты</strong><div><a id="export-json">JSON ↓</a> <a id="export-csv">CSV ↓</a></div></div>
    <div class="job-toolbar"><label>Задание<select id="bench-jobs"></select></label><button id="bench-cancel" class="secondary">Отменить задание</button></div>
    <p id="job-status" role="status">Измерений пока нет. Запустите runner в отдельном терминале.</p>
    <div id="bench-summary"></div><p class="bench-note">Времена от начала открытия Admin выбранного сценария. В статистике только успешные зачётные запуски; preflight/warmup показаны отдельно. SQL-время ClickHouse: недоступно.</p>
    <details><summary>Отдельные запуски и панели</summary><div id="bench-runs"></div></details>
    </section>`;
  const $ = id => document.getElementById(id);
  let chosen, busy = false, running = false, lastRendered;
  const showMessage = text => { $('bench-message').textContent = text; };

  function render(job) {
    const signature = JSON.stringify([job.id, job.status, job.completed_runs, job.error]);
    if (signature === lastRendered) return;
    lastRendered = signature;
    $('job-status').textContent = `${job.status} · ${date(job.created_at)} · результатов: ${job.completed_runs}${job.error ? ' · ' + (errors[job.error] || job.error) : ''}`;
    $('bench-cancel').disabled = !['pending', 'running'].includes(job.status);
    for (const format of ['json', 'csv']) { const link = $('export-' + format); link.href = `/api/benchmark/jobs/${job.id}/export.${format}`; link.hidden = false; }
    $('bench-summary').replaceChildren(table(['Вариант', 'Контекст', 'p50 данных', 'min готовности ≈', 'p50 готовности ≈', 'p95 готовности ≈', 'max готовности ≈', 'Ошибки'], job.summary.map(s => [names[s.variant], s.cache, ms(s.data_ms.p50), ms(s.ready_ms.min), ms(s.ready_ms.p50), ms(s.ready_ms.p95), ms(s.ready_ms.max), `${s.errors}/${s.runs}`])));
    if (!job.summary.length) {
      const note = document.createElement('p'); note.className = 'bench-note';
      note.textContent = 'Зачётных замеров пока нет. Результаты предварительной проверки показаны ниже.';
      $('bench-summary').append(note);
    }
    if (job.status === 'failed') document.querySelector('#benchmark-results details').open = true;
    $('bench-runs').replaceChildren(table(['Вариант', 'Фаза / контекст', '№', 'Статус', 'iframe load', 'Данные обеих', 'Готовность обеих ≈', 'Hourly: данные / готовность ≈', 'Raw: данные / готовность ≈', 'Строки Hourly / Raw', 'Ответ, bytes'], job.runs.map(r => {
      const hourly = r.panels.hourly, raw = r.panels.raw;
      return [names[r.variant], `${r.phase} / ${r.cache}`, r.repetition, r.success ? 'Успешно' : r.errors.map(e => errors[e] || e).join('; '), ms(r.iframe_ms), ms(r.data_ms), ms(r.ready_ms), `${ms(hourly?.data_ms)} / ${ms(hourly?.ready_ms)}`, `${ms(raw?.data_ms)} / ${ms(raw?.ready_ms)}`, `${hourly?.rows ?? '—'} / ${raw?.rows ?? '—'}`, Object.values(r.panels).reduce((sum, p) => sum + p.response_bytes, 0)];
    })));
  }

  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      const list = await api('');
      $('runner-status').textContent = list.runner_online ? '● Runner онлайн' : '○ Runner не запущен';
      $('runner-status').className = list.runner_online ? 'online' : 'offline';
      const wasRunning = running;
      running = list.jobs.some(j => ['pending', 'running'].includes(j.status));
      if (running && !wasRunning) stopDashboard();
      document.getElementById('show').disabled = running;
      document.getElementById('reload').disabled = running;
      $('bench-start').disabled = running;
      const previous = chosen;
      $('bench-jobs').replaceChildren(...list.jobs.map(j => { const option = document.createElement('option'); option.value = j.id; option.textContent = `${date(j.created_at)} · ${j.status}`; return option; }));
      chosen = list.jobs.some(j => j.id === previous) ? previous : list.jobs[0]?.id;
      if (chosen) { $('bench-jobs').value = chosen; render(await api('/' + chosen)); }
      else { $('export-json').hidden = true; $('export-csv').hidden = true; $('bench-cancel').disabled = true; }
      if (running && !list.runner_online) showMessage('Задание ожидает runner. В Git Bash: .venv-benchmark/Scripts/python.exe -m benchmark.runner');
      else if (running) showMessage('Runner выполняет предварительную проверку или последовательные замеры. Дашборд в этой вкладке остановлен.');
      else if (wasRunning) showMessage('Задание завершено. Проверьте статус и результаты ниже.');
    } catch (error) { $('runner-status').textContent = 'API измерений недоступен'; showMessage(error.message); }
    finally { busy = false; }
  }
  $('bench-jobs').onchange = () => { chosen = $('bench-jobs').value; lastRendered = undefined; refresh(); };
  $('bench-start').onclick = async () => {
    if (running) return;
    const repetitions = Number($('bench-repeats').value), timeout = Number($('bench-timeout').value);
    if (!Number.isInteger(repetitions) || repetitions < 1 || repetitions > 50 || !Number.isInteger(timeout) || timeout < 10 || timeout > 300) { showMessage('Повторения: 1–50; таймаут: 10–300 с.'); return; }
    stopDashboard();
    try {
      const job = await api('', { variants: $('bench-variants').value === 'all' ? ['superset', 'infinity', 'direct'] : [selectedVariant()], repetitions, cache: $('bench-cache').value, timeout_seconds: timeout });
      chosen = job.id; running = true; lastRendered = undefined;
      showMessage('Задание создано. Ожидаем runner и предварительную проверку данных.'); await refresh();
    } catch (error) { showMessage(error.message); }
  };
  $('bench-cancel').onclick = async () => { if (chosen) { try { await api('/' + chosen + '/cancel', {}); await refresh(); } catch (error) { showMessage(error.message); } } };
  refresh(); setInterval(refresh, 2000);
}
