# Telemetry Benchmark

Локальный стенд сравнения Apache Superset и Grafana на существующей внешней
ClickHouse. Репозиторий остаётся `check_bi`; Compose project называется
`telemetry-benchmark`. Данные не копируются, контейнера ClickHouse нет.

Три сценария:

1. Admin → telemetry-api (параметры embedding и guest token) → Superset → ClickHouse.
2. Admin → Grafana Infinity → telemetry-api → ClickHouse.
3. Admin → Grafana ClickHouse datasource → ClickHouse.

В первом сценарии SQL выполняет сам Superset, API выдаёт только параметры embedding.
Дашборды создаются вручную. Есть браузерные измерения через Playwright и локальную
очередь API; генерации charts/dashboards и пользовательской авторизации нет.

## Браузерный benchmark: запуск из Git Bash на Windows

### Предварительные условия и обнаруженные различия

Дашборды, SQL и SSH-настройки runner не меняет. Существующий SSH-туннель должен
быть запущен и доступен BI-контейнерам. Запустите стенд, затем проверьте чтение БД.

При проверке **16 ноября 2026** туннель `clickhouse-ssh-tunnel:18123` был недоступен.
Кроме того, у Superset `bench - raw records` обнаружены сохранённый фильтр
`metric = battery_voltage` и отсутствие `orderby`, тогда как Grafana читает всю
таблицу с `ORDER BY sent_at, device_id, metric`. Это несовместимые выборки.
Сохранённые панели не исправлялись. Пока вы сами не согласуете/устраните эти
различия и не восстановите туннель, правильный результат preflight — **failed**,
без таблицы вымышленных успешных времён.

### Обновление двух сервисов

Из корня `check_bi`, Git Bash:

```bash
docker compose up -d --build --no-deps --wait telemetry-api admin
```

Superset и Grafana не пересоздаются. Добавлен только volume
`telemetry-benchmark_benchmark_results` для JSON состояния очереди и отчётов API.
API по-прежнему работает одним процессом: не запускайте несколько Uvicorn workers
с этой файловой очередью.

### Установка runner один раз

Python 3.11+ должен быть доступен командой `python`. Активация venv не обязательна:

```bash
python -m venv .venv-benchmark
.venv-benchmark/Scripts/python.exe -m pip install -r benchmark/requirements.txt
.venv-benchmark/Scripts/python.exe -m benchmark.install
```

Устанавливаются Playwright 1.58.0, httpx и соответствующий Chromium в
`.cache/ms-playwright` внутри проекта. В Docker API браузер не устанавливается.
`.venv-benchmark`, кэш браузера и локальные отчёты исключены из Git.

### Runner в отдельном терминале

```bash
cd /c/dev/check_bi
.venv-benchmark/Scripts/python.exe -m benchmark.runner
```

Оставьте терминал открытым: runner опрашивает очередь и последовательно выполняет
созданные из Admin задания. Нового постоянно работающего Docker-сервиса нет.
Остановка — Ctrl+C. `--once` ждёт одно задание, выполняет его и завершает процесс.
Для диагностики можно добавить `--headed`; не смешивайте такие результаты с
headless-сериями.

Runner читает из корневой `.env` только публичные `ADMIN_PORT`, `API_PORT` и
необязательные `BENCHMARK_ADMIN_URL`, `BENCHMARK_API_URL`. Пароли BI и ClickHouse
ему не нужны. Для нестандартных URL можно явно задать:

```bash
.venv-benchmark/Scripts/python.exe -m benchmark.runner \
  --admin http://localhost:5173 --api http://localhost:8000
```

### Первое задание через Admin

1. Откройте http://localhost:5173. Убедитесь, что показано «Runner онлайн».
2. Выберите вкладку Superset, Grafana Infinity или Grafana Direct. Выбор вкладки
   не запускает dashboard автоматически; для обычного просмотра есть «Показать
   дашборд» и «Перезагрузить».
3. Выберите «Выбранная вкладка» либо «Все три последовательно», число повторений
   1, 10, 30 или другое целое до 50, режим контекста и таймаут 10–300 секунд.
4. Нажмите «Запустить измерение». Dashboard в пользовательской вкладке
   останавливается; просмотр заблокирован до завершения задания.
5. Runner сначала проверяет сопоставимость **всех трёх** вариантов в отдельных
   контекстах. При первой ошибке проверка останавливается, зачётная серия не идёт.
6. После успешной проверки выполняются нужные повторы. В Admin появляются
   отдельные запуски, строки/bytes, времена, ошибки и статистика. Фазы preflight,
   warmup и measured различаются; в сводке учитывается только measured/success.
7. Скачайте JSON/CSV в Admin. Runner также сохраняет оба файла в
   `benchmark-results/<job-id>.json` и `.csv` на Windows-хосте.

Во время теста не открывайте/обновляйте BI в других вкладках и не запускайте
другой benchmark: очередь исключает конкуренцию между её заданиями, но не может
запретить сторонние запросы к внешнему ClickHouse.

### Что именно измеряется

Начало каждого запуска — непосредственно перед открытием **Admin выбранного
сценария**, URL `/?scenario=superset|infinity|direct&autostart=1&benchmark=1`.
Обработчики запросов и событий устанавливаются до навигации. Другой сценарий
в этой странице не открывается. При Superset в интервал попадает получение
настоящего guest token через API.

| Поле | Значение и ограничения |
|---|---|
| `iframe_ms` | Событие load документа iframe; отдельный показатель, не готовность |
| `panels.*.data_ms` | Завершение передачи тела ответа соответствующей панели браузеру, относительно начала открытия Admin |
| `data_ms` | Более позднее завершение данных двух панелей |
| `panels.*.ready_ms` | Наблюдаемая DOM-готовность панели **приблизительно** |
| `ready_ms` | Наблюдаемая готовность обеих панелей, приблизительно |
| `rows`, `fingerprint` | Проверенные число строк и SHA-256 нормализованного мультимножества данных |
| `renderer` | Тип панели, если доступен в запросе/metadata; иначе unknown |
| `response_bytes` | Размер тела по браузерному сетевому учёту; batched response учитывается один раз |
| `sql_ms` | `null`: надёжная сквозная привязка ClickHouse query_id не установлена |

Время данных берётся из browser request timing до разбора большого JSON Python-кодом.
У Grafana наблюдается **`/api/ds/query`**, включая Infinity backend → FastAPI →
ClickHouse → backend Grafana → браузер. Прямой запрос runner к FastAPI не
используется как подмена этого времени. Superset наблюдается через `/api/v1/chart/data`.

Проверки успешности:

- Нет HTTP/query errors; требуются непустые почасовые данные и ровно 100000 raw rows
  в **ответе**, а не число DOM-строк виртуализированной таблицы.
- Проверяется, что запросы не содержат дополнительных фильтров, Infinity использует
  backend parser и нужные endpoints, Raw Records имеет нужные поля, limit и
  сортировку. Неизвестный SQL или несовместимая структура отклоняются, не переписываются.
- Нормализованные значения сравниваются между тремя вариантами и с baseline
  каждого следующего запуска. Просто одинакового count недостаточно. Несовпадение
  — `different_data`; сравнение прекращается. Данные не сохраняются, только хеши.
- После получения данных ищутся нужные панели: Grafana `data-viz-panel-key`,
  Superset chart ID/контейнер с соответствующим заголовком. Проверяются ошибки,
  занятость панели, видимая таблица с ячейками либо canvas grid и наличие отрисовки
  canvas/SVG графика. Условие должно выполняться в двух наблюдениях с возможностью
  двух `requestAnimationFrame` между ними.

Тип панели фиксируется при доступности metadata. Текущие Superset/Grafana
могут использовать разные renderers (например bar и time series): вывод относится
к этим конкретным визуализациям, а не к универсальной скорости BI-продукта.

**Это не точная фиксация завершения GPU/compositor paint.** Наличие данных и
нарисованного DOM/canvas — приближение, зависящее от renderer BI. Интерфейс и
отчёты называют его `dom_observed_approximate`. `iframe.onload`, HTTP completion,
`networkidle` и одно исчезновение спиннера не считаются render readiness.
Если renderer нельзя опознать, возвращается ошибка/`null`, не вымышленное время.
Проверенные DOM-атрибуты относятся к установленным Superset 6.1.0/Grafana 13.1.0;
после обновления BI нужно повторно проверить адаптеры.

Числовые временные поля ожидаются в epoch milliseconds; ISO-даты — с timezone.
Неоднозначная дата без timezone отклоняется. При изменениях таблицы во время серии
или различных строках на границе LIMIT из-за одинаковых ключей fingerprint может
различаться; такой запуск не объявляется корректным сравнением.

### Режимы кэша и статистика

- **cold**: новый изолированный browser context для каждого запуска; браузерный
  HTTP-кэш, cookies и storage от предыдущего запуска не наследуются.
- **warm**: предварительный незачётный warmup данного варианта, затем новые
  страницы в одном и том же context этого варианта с контролируемым переиспользованием
  browser cache/cookies/storage. Service workers блокируются в обоих режимах.
- **both**: две отдельные группы cold/warm; статистика не смешивается.

Это не холодный/прогретый ClickHouse. Preflight сам читает данные и прогревает
серверные кэши. Superset data cache отключён текущей конфигурацией; его фактический
`is_cached` фиксируется, если есть в ответе. Состояние кэшей Grafana и ClickHouse
не гарантируется, в отчёте `server_cache=uncontrolled`. Перезапуск/очистка кэшей,
`SYSTEM FLUSH LOGS` и любые изменения удалённого сервера не выполняются.

Viewport одинаковый: 1600×1100, timezone браузера Europe/Moscow, headless Chromium
по умолчанию. Порядок вариантов
циклически меняется между повторами; начальное смещение зависит от ID серии.
Параллельных измерений нет. При первой ошибке/таймауте дальнейшие запуски задания
останавливаются: backend-запрос после закрытия browser page может ещё выполняться.

Для каждого варианта и режима отдельно считаются min, p50, p95, max по успешным
зачётным запускам. Percentile — линейная интерполяция позиции `(n−1)×p`.
Для одного успешного повтора все четыре числа одинаковы; p95 по 10 повторам
имеет ограниченную статистическую устойчивость. Ошибки и выполненное число
попыток показываются отдельно; непроведённые повторы не превращаются в нули.

### Очередь и локальные ограничения

- Только одно pending/running задание и один runner. Второй получает HTTP 409.
- Runner heartbeat каждые 3 секунды. Через 15 секунд без heartbeat UI показывает
  offline; через 60 секунд running переходит в failed/runner_lost. Забытое pending
  истекает через час. Если heartbeat есть, но результат отдельного запуска не
  поступил за его таймаут + 60 секунд, задание тоже завершается как зависшее.
  Проверки истечения выполняются при обращении к API очереди.
- Ctrl+C освобождает runner; аварийное завершение обрабатывается lease timeout.
  «Отменить задание» запрещает дальнейшую публикацию результатов; текущая страница
  может закрыться только при следующем завершении проверки/таймауте. Следующий job
  не исполняется этим runner одновременно с предыдущим.
- До 50 повторений на вариант/режим, до 300 зачётных запусков в одном job.
  До 20 последних jobs в API, общий state до 32 MB, POST тела до 32 KB.
  Локальный каталог runner хранит до 20 пар отчётов с именами сгенерированных job ID.
- API управления не является публичным сервисом. Сохраняйте Compose bind на
  127.0.0.1. Для POST обязательны JSON и разрешённый origin Admin, если заголовок
  Origin присутствует. Пользовательская авторизация не добавлена.
- Схема отчётов запрещает произвольные поля. Не сохраняются guest tokens,
  Authorization, cookies, raw response bodies, SQL-тексты, HAR и Playwright traces.
  Ошибки представлены безопасными кодами. Техническая lease не экспортируется.

Если runner потерял доступ к API, непереданные метрики не выдаются за сохранённые:
задание истечёт по lease timeout. Runner можно запустить повторно для нового задания.

### Проверки реализации без внешней БД

```bash
.venv-benchmark/Scripts/python.exe -m pip install pytest==8.3.5
.venv-benchmark/Scripts/python.exe -m pytest benchmark/tests -q
docker compose run --rm --no-deps --user root \
  -v "$(pwd -W)/telemetry-api/app:/app/app" \
  -v "$(pwd -W)/telemetry-api/tests:/tests" \
  telemetry-api sh -c 'pip install --quiet pytest==8.3.5 && python -m pytest /tests -q -p no:cacheprovider'
```

Если Git Bash автоматически преобразует Docker volume paths, добавьте перед
командой `MSYS_NO_PATHCONV=1` и укажите Windows-пути к checkout явно.
Браузерные тесты используют только локальные **синтетические** страницы и ответы,
не создают и не меняют реальные dashboards и не измеряют производительность BI.
Фактические ограничения текущего стенда отражены в [VALIDATION.md](VALIDATION.md).

## Что подготовлено автоматически

- Четыре сервиса Docker Compose, общая сеть, healthchecks, порты только на `127.0.0.1`.
- Admin: три вкладки, понятные состояния без dashboard, повторная загрузка,
  Superset Embedded SDK и iframe Grafana.
- FastAPI: OpenAPI, health, проверка чтения ClickHouse, временные ряды,
  агрегации и исходные строки с обязательными фильтрами.
- Одна Grafana, два provisioned datasource, allowed hosts Infinity,
  iframe и настраиваемый анонимный Viewer.
- Superset: SQLite metadata в Docker volume, миграции **только этой SQLite**,
  администратор, технический пользователь, права получения настоящего guest token.
- ClickHouse dialect установлен. Команда создания записи подключения Superset
  из `.env` не создаёт datasets/charts и не обращается к внешней БД.
- Настройки Admin загружаются из API при выборе вкладки. Пересборка при смене URL/ID не нужна.

## Что выполнить вручную

1. Подготовить `.env`, заполнить ClickHouse read-only credentials.
2. Запустить Compose и проверить чтение из контейнеров.
3. Создать запись подключения Superset командой ниже либо через UI.
4. Создать datasets, charts, dashboard Superset; включить embedding.
5. Создать два dashboard Grafana, выбрав соответствующие datasource.
6. Указать embedded UUID и два Grafana URL в `.env`, пересоздать сервисы.

## Требования и версии

Docker Desktop в режиме Linux containers, Docker Compose v2, доступ в интернет
для первой сборки. Рекомендуется выделить Docker 4 CPU и 6–8 GB RAM.
Python/Node на Windows для запуска стенда не требуются; вспомогательные скрипты
проверки и генерации `.env` требуют Python 3.11+ либо запуска через Docker.

| Компонент | Зафиксированная версия |
|---|---|
| Apache Superset | 6.1.0 |
| Grafana | 13.1.0 |
| ClickHouse plugin | 4.23.5 |
| Infinity plugin | 4.1.6 |
| Python API | 3.11.12 |
| clickhouse-connect, включая Superset dialect | 0.8.18 |
| FastAPI / Uvicorn | 0.115.12 / 0.34.2 |
| Embedded SDK | 0.2.0 |
| Vite / Node для сборки | 6.4.6 / 22.15.0 |
| Nginx | 1.30.6-alpine |

Основные Python-зависимости зафиксированы в `pyproject.toml`, frontend — также
в `package-lock.json`. Транзитивные Python-зависимости разрешаются pip при сборке.
Плагины Grafana включены в образ; фоновая установка/обновление отключены.

## 1. Создание `.env`

Из корня репозитория, PowerShell:

```powershell
Copy-Item .env.example .env
```

Заполните пять служебных секретов: `SUPERSET_SECRET_KEY`,
`SUPERSET_GUEST_TOKEN_SECRET`, `SUPERSET_ADMIN_PASSWORD`,
`SUPERSET_TECH_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`.
Для генерации каждой строки можно использовать:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Альтернатива копированию: `python scripts/create_env.py` создаёт `.env`
из примера с уже случайными служебными секретами. Существующий файл не перезаписывается.
Без Python на Windows:

```powershell
docker run --rm -v "${PWD}:/workspace" -w /workspace python:3.11.12-slim python scripts/create_env.py
```

Для этого рабочего экземпляра `.env` уже создан со случайными локальными
секретами; ClickHouse оставлен незаполненным. Файл игнорируется Git.
Не меняйте `SUPERSET_SECRET_KEY` после создания metadata: им шифруются секреты
Superset. `.env` нужно хранить вместе с резервной копией volume в закрытом месте.

Настройки ClickHouse:

| Переменная | Что указать |
|---|---|
| `CLICKHOUSE_HOST` | DNS/IP **из Docker**, без `http://` и без порта |
| `CLICKHOUSE_HTTP_PORT` | HTTP(S) порт: обычно 8123 или 8443 |
| `CLICKHOUSE_NATIVE_PORT` | Native порт: обычно 9000 или 9440; зарезервирован, по умолчанию все используют HTTP |
| `CLICKHOUSE_DATABASE` | `telemetry` |
| `CLICKHOUSE_USER` | Отдельный существующий пользователь только для чтения |
| `CLICKHOUSE_PASSWORD` | Его пароль |
| `CLICKHOUSE_SECURE` | `true` для TLS; порт при этом указать явно |
| `CLICKHOUSE_VERIFY` | Проверка сертификата API и Superset, обычно `true` |
| `GRAFANA_CLICKHOUSE_TLS_SKIP_VERIFY` | Обратный флаг Grafana, обычно `false` |
| `CLICKHOUSE_TIMEZONE` | IANA timezone поля `sent_at`, уточнить у администратора БД; по умолчанию UTC |
| `CLICKHOUSE_CONNECT_TIMEOUT` | Таймаут соединения в секундах |
| `CLICKHOUSE_QUERY_TIMEOUT` | Максимальное время SQL в секундах |

На Docker Desktop `localhost` внутри контейнера означает сам контейнер.
Если БД на Windows-хосте, используйте `host.docker.internal`; для удалённого
сервера — доступный из Docker DNS/IP. Проверьте VPN, маршруты и firewall сервера.
Публиковать ClickHouse через Compose не требуется.

Значения с `$`, `#` и пробелами заключайте в одинарные кавычки в `.env`, например
`CLICKHOUSE_PASSWORD='example$with#symbols'`. Provisioning использует `$VAR`,
чтобы Grafana не выполнила повторную подстановку `$` внутри пароля.
Не публикуйте вывод `docker compose config`: он раскрывает значения окружения.

### Обязательные права ClickHouse

Администратор внешней БД должен предоставить отдельного пользователя с SELECT
на `telemetry.metrics` и необходимым чтением metadata для BI-интерфейсов.
У него не должно быть INSERT, ALTER, CREATE, DELETE, UPDATE, TRUNCATE или DROP.
Стенд не выдаёт права и не выполняет DDL в ClickHouse.

API и Grafana передают `max_execution_time`. При строгом `readonly=1` серверный
профиль должен разрешать изменение этого параметра через constraint
`CHANGEABLE_IN_READONLY`; это заранее настраивает администратор БД вне стенда.
Не переходите на пользователя с правами записи ради устранения ошибки настроек.
Успешный Save & test ещё не гарантирует, что рабочие SELECT с настройкой таймаута разрешены.

## 2. Запуск и состояние

```powershell
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 300
docker compose ps
```

Первый запуск Superset мигрирует SQLite и создаёт пользователей; это может занять
несколько минут. Отсутствие ClickHouse не мешает запуску интерфейсов.
Compose потребует заполненные служебные секреты и подскажет имя отсутствующей переменной.

| Интерфейс | Адрес по умолчанию |
|---|---|
| Admin | http://localhost:5173 |
| API / Swagger | http://localhost:8000/docs |
| API liveness | http://localhost:8000/health |
| Grafana | http://localhost:3000 |
| Superset | http://localhost:8088 |

Логины и пароли администраторов смотрите в своей `.env`.
Используйте одинаковое имя хоста `localhost` в URL Admin и в настройке embedding.
Если меняете порт Admin, измените также `ADMIN_ORIGIN`; для Superset/Grafana —
соответствующий `*_PUBLIC_URL` и ссылки дашбордов.

После **любого изменения `.env`**:

```powershell
docker compose up -d --force-recreate --wait --wait-timeout 300
```

Обычный `docker compose restart` не перечитывает окружение. Пересборка Admin
для смены dashboard URL/ID не требуется. Нажмите «Перезагрузить» в Admin.

## 3. Проверка ClickHouse

После заполнения `.env` и пересоздания контейнеров:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/api/health/clickhouse
```

Второй endpoint выполняет `SELECT device_id, metric, sent_at FROM telemetry.metrics LIMIT 1`
из контейнера API. При пустых настройках он возвращает 503 с инструкцией,
при сетевой ошибке — понятное сообщение без утечки пароля.
Liveness `/health` при этом остаётся 200: исправный API может работать без БД.

Пример чтения, замените `YOUR_DEVICE_ID` на существующий:

```powershell
$params = 'device_id=YOUR_DEVICE_ID&metric=battery_voltage&start=2026-11-01T00%3A00%3A00Z&end=2026-11-02T00%3A00%3A00Z&limit=1000'
Invoke-RestMethod "http://localhost:8000/api/metrics/series?$params"
Invoke-RestMethod "http://localhost:8000/api/metrics/aggregate?$params&bucket=hour&aggregation=avg"
Invoke-RestMethod "http://localhost:8000/api/metrics/rows?$params"
```

Все три endpoint — GET и возвращают массив объектов JSON в корне:

```json
[{"time":"2026-11-01T00:00:00+00:00","value":3450.0}]
```

Агрегация дополнительно возвращает `samples`; raw rows — исходные поля таблицы.
Временной ряд и агрегация исключают `NULL value_num`; raw rows сохраняет их.
Агрегации: `avg`, `min`, `max`, `sum`, `count`; интервалы: `minute`, `hour`, `day`.
`count` считает только числовые строки, как и остальные агрегаты.

`device_id`, `metric`, `start`, `end` обязательны. Время — ISO 8601 с timezone,
диапазон `[start, end)`. При передаче `+03:00` в URL знак `+` кодируйте `%2B`.
`sent_at` имеет точность до секунды; для сравнения задавайте границы целыми секундами.
Выходные даты нормализуются в UTC. Бакеты выравниваются в `CLICKHOUSE_TIMEZONE`.
Сервер не принимает SQL, имя таблицы или выражение агрегации от клиента.

По умолчанию limit 1000, максимум `API_MAX_ROWS=10000`, диапазон до 180 дней.
Для тестирования 100000 строк установите `API_MAX_ROWS=100000`, пересоздайте
контейнер и передайте `limit=100000`. Ответ содержит первые строки в порядке
времени; заголовки `X-Result-Rows` и `X-Result-Truncated` показывают число строк
и усечение. Это не пагинация. Infinity может не показывать эти заголовки в панели.
При равных временных метках порядок одинаковых строк не гарантируется.

## 4. Подключение Superset к ClickHouse

Войдите администратором Superset. Автоматически создана только служебная SQLite,
не подключения к аналитической БД и не datasets.

Рекомендуемый способ после заполнения ClickHouse в `.env`:

```powershell
docker compose exec superset python /app/configure_clickhouse.py
```

Команда создаёт/обновляет **только запись Superset** `Telemetry ClickHouse`,
не выполняет даже проверочный запрос к ClickHouse. Она отключает DML/CTAS/CVAS
в настройках подключения. Впоследствии при старте Superset эта запись обновляется
из `.env` автоматически. Сохранённые datasets продолжат ссылаться на неё.
Пароль берётся через `SQLALCHEMY_CUSTOM_PASSWORD_STORE` из окружения при соединении.

Для создания через UI: Settings → Database Connections → + Database →
выберите ClickHouse Connect или SQLAlchemy URI. Получить URI без пароля:

```powershell
docker compose exec superset python -c "from superset_config import benchmark_clickhouse_uri; print(benchmark_clickhouse_uri())"
```

Он имеет вид `clickhousedb://reader@host:8123/telemetry?secure=false&verify=true&...`.
Пароль вручную в URI не вставляйте — его подставляет конфигурация из `.env`.
Сохраните подключение с именем `Telemetry ClickHouse`, чтобы оно далее обновлялось
из окружения. Не включайте asynchronous queries, DML, CTAS или CVAS.
Нажмите Test connection. Выполняйте только SELECT.

## 5. Dashboard Superset и embedding

1. Data → Datasets → + Dataset: выберите подключение, схему `telemetry`, таблицу `metrics`.
2. Создайте chart вручную, выберите `sent_at` как временное поле, числовой
   `value_num`, фильтры `device_id`, `metric`, одинаковый период и агрегацию.
3. Сохраните chart в новом dashboard, сохраните dashboard и опубликуйте его.
4. В меню dashboard выберите **Embed dashboard**, включите embedding и укажите
   Allowed domains: `http://localhost:5173` (точный origin Admin из `.env`).
5. Скопируйте **embedded UUID**, а не числовой ID из обычного URL dashboard.
6. В `.env` задайте `SUPERSET_EMBEDDED_DASHBOARD_ID=<UUID>` и
   `SUPERSET_PUBLIC_URL=http://localhost:8088`.
7. Пересоздайте контейнеры командой из раздела 2 и откройте вкладку Superset в Admin.

Origin проверяется в двух местах: CSP `frame-ancestors` из `ADMIN_ORIGIN` и
Allowed domains конкретного dashboard в Superset. При смене origin обновите оба.
Admin передаёт origin через referrer policy Embedded SDK; не включайте `no-referrer`.

Технический пользователь `SUPERSET_TECH_USER` получает отдельную роль
`BenchmarkTokenIssuer`: `can_read` и `can_grant_guest_token` на `SecurityRestApi`.
Это не администратор и не пользователь ClickHouse. API логинится на внутренний
`SUPERSET_INTERNAL_URL`, получает CSRF/cookie и подписанный Superset guest token.
SDK автоматически обновляет token через API. Секрет подписи не передаётся Admin.

Гость использует роль `BenchmarkGuest`, копирующую базовые разрешения Gamma
без широкого database access; доступ к dashboard ограничивается ресурсом guest token.
Не выдавайте техническому пользователю Admin и не добавляйте гостю all database access.
При создании пользователей пароль и системные роли admin/tech синхронизируются из `.env`
при каждом старте; не переименовывайте эти учётные записи только через UI.

Проверка endpoint без вывода token в консоль:

```powershell
$embed = Invoke-RestMethod http://localhost:8000/api/superset/embed
$embed | Select-Object id, supersetDomain
```

До настройки UUID ожидается 409 с инструкцией. Наличие token само по себе не
подтверждает существование dashboard, его доступность или успешную отрисовку.

## 6. Dashboard Grafana Infinity

Войдите администратором Grafana → Connections → Data sources.
Уже есть `Telemetry API Infinity` (`uid=telemetry-infinity`):

- plugin `yesoreyeram-infinity-datasource`;
- разрешён host `http://telemetry-api:8000` из `TELEMETRY_INTERNAL_URL`;
- custom healthcheck обращается к `/health` API;
- запрос выполняется backend-плагином в сети Docker.

Save & test проверяет доступность API, **не ClickHouse**. Provisioned datasource
нельзя редактировать в UI: изменяйте `.env`, затем пересоздавайте контейнеры.

Создайте dashboard → Add visualization → `Telemetry API Infinity`. Настройки запроса:

| Поле | Значение |
|---|---|
| Type | JSON |
| Parser | **Backend / JSONata** (значение модели `backend`) |
| Source | URL |
| Method | GET |
| URL | `http://telemetry-api:8000/api/metrics/aggregate` |
| Format | Time series для графика, Table для исходных строк |
| Root / Rows | Пусто: массив объектов находится в корне ответа |
| Columns | `time` типа Time, `value` типа Number; `samples` при необходимости |

В URL query parameters добавьте:

```text
device_id = YOUR_DEVICE_ID
metric = battery_voltage
start = ${__from:date:iso}
end = ${__to:date:iso}
bucket = hour
aggregation = avg
limit = 1000
```

Для числового ряда замените путь на `/api/metrics/series`, для таблицы —
`/api/metrics/rows` и выберите нужные исходные колонки (`sent_at` типа Time).
Используйте полный внутренний URL; на browser URL `localhost:8000` его менять нельзя.
Parser — настройка **каждого запроса**, а не datasource; provisioning задаёт сеть,
allowed hosts и healthcheck. Установка parser при создании панели остаётся ручным действием.
Сохраните dashboard и скопируйте browser URL в `GRAFANA_INFINITY_DASHBOARD_URL`.

## 7. Dashboard Grafana Direct

Выберите provisioned `ClickHouse Direct` (`uid=clickhouse-direct`). Он использует
те же `.env` host, HTTP port, username, password, TLS и database, но минует API.
Пароль provisioning передаёт через `secureJsonData`; литерала секрета в YAML нет.

Проверьте Save & test после заполнения ClickHouse. Создайте dashboard, добавьте
панель с SQL ниже и сопоставимыми фильтрами, диапазоном, бакетом и row limit.
Скопируйте browser URL в `GRAFANA_DIRECT_DASHBOARD_URL`.

Пример `.env` после ручного создания двух дашбордов:

```dotenv
GRAFANA_INFINITY_DASHBOARD_URL='http://localhost:3000/d/INFINITY_UID/infinity?orgId=1&kiosk'
GRAFANA_DIRECT_DASHBOARD_URL='http://localhost:3000/d/DIRECT_UID/direct?orgId=1&kiosk'
SUPERSET_EMBEDDED_DASHBOARD_ID=YOUR-EMBEDDED-UUID
```

Ссылки должны использовать `localhost`, доступный браузеру; `grafana:3000` нужен
только внутри Docker. Затем пересоздайте контейнеры; исходники и build Admin не меняются.
Все сохранённые вручную панели находятся в Grafana volume.

По умолчанию анонимный Viewer включён для iframe локального стенда. Если поставить
`GRAFANA_ANONYMOUS_ENABLED=false`, сначала войдите в Grafana в том же браузере.
Анонимный Viewer видит дашборды организации и может выполнять запросы datasource:
пользователь ClickHouse обязан быть read-only. Это конфигурация только для localhost.

## Benchmark endpoints для полного набора данных

Для сравнения Hourly Events и Raw Records без фильтров доступны два GET-маршрута:

- `/api/benchmark/events-hourly` — `toStartOfHour(sent_at)` и `count()` по всей
  таблице, сортировка по времени, без WHERE и LIMIT. Считает все события,
  включая строки с `NULL value_num`. Группировка использует timezone ClickHouse,
  как SQL `toStartOfHour(sent_at)` в остальных инструментах.
- `/api/benchmark/rows?limit=100000` — `sent_at`, `device_id`, `metric`,
  `value_num`, `value_str` из всей таблицы с `ORDER BY sent_at, device_id, metric`.
  Limit должен быть от 1 до `API_MAX_ROWS` (абсолютный максимум 100000).
  При превышении возвращается 422, без молчаливого усечения. Без limit используется
  меньшее из `API_DEFAULT_ROWS` и `API_MAX_ROWS`.

Оба ответа — JSON-массивы объектов в корне, без необходимости задавать
`device_id`, `metric`, `start`, `end`. Даты сериализуются в ISO 8601 UTC,
`NULL` сохраняется, NaN/Infinity заменяются на JSON `null`.
Используются существующие подключение и SQL timeout ClickHouse.

Для 100000 строк задайте в корневой `.env` `API_MAX_ROWS=100000`, затем:

```powershell
docker compose up -d --build --no-deps --force-recreate --wait telemetry-api
Invoke-RestMethod http://localhost:8000/api/benchmark/events-hourly
$rows = Invoke-RestMethod 'http://localhost:8000/api/benchmark/rows?limit=100000'
$rows.Count
$rows | Select-Object -First 3
```

Число строк может быть меньше limit, если таблица содержит меньше записей.
Для Infinity выберите существующий `Telemetry API Infinity`, Type JSON,
Parser Backend / JSONata, Method GET, root selector оставьте пустым.
Используйте внутренние URL:

```text
http://telemetry-api:8000/api/benchmark/events-hourly
http://telemetry-api:8000/api/benchmark/rows?limit=100000
```

Для Hourly Events колонки `time` (Time), `value` (Number); для Raw Records —
Table и перечисленные выше исходные колонки (`sent_at` типа Time).
Не добавляйте параметры времени Grafana: эти маршруты намеренно читают всю таблицу.

## 8. Сопоставимые SELECT для ручных графиков

Ниже используются UTC и пример периода; замените устройство и timezone на реальные.
Во всех инструментах задайте одинаковые значения. Используйте SQL Lab/виртуальный
dataset в Superset либо соответствующие настройки физического dataset вручную.
Объект виртуального dataset сохраняется в SQLite Superset, не создаёт таблицу ClickHouse.

Почасовое среднее, соответствует API `aggregate?bucket=hour&aggregation=avg`:

```sql
SELECT
    toStartOfInterval(sent_at, INTERVAL 1 HOUR, 'UTC') AS time,
    avg(value_num) AS value,
    count() AS samples
FROM telemetry.metrics
WHERE device_id = 'YOUR_DEVICE_ID'
  AND metric = 'battery_voltage'
  AND sent_at >= toDateTime('2026-11-01 00:00:00', 'UTC')
  AND sent_at <  toDateTime('2026-11-02 00:00:00', 'UTC')
  AND value_num IS NOT NULL
GROUP BY time
ORDER BY time
LIMIT 1000
```

Числовой ряд, соответствует API `/series`:

```sql
SELECT sent_at AS time, value_num AS value
FROM telemetry.metrics
WHERE device_id = 'YOUR_DEVICE_ID'
  AND metric = 'battery_voltage'
  AND sent_at >= toDateTime('2026-11-01 00:00:00', 'UTC')
  AND sent_at <  toDateTime('2026-11-02 00:00:00', 'UTC')
  AND value_num IS NOT NULL
ORDER BY sent_at, value_num
LIMIT 1000
```

Исходные 100000 строк, соответствует API `/rows?limit=100000` при увеличенном лимите:

```sql
SELECT sent_at, device_id, metric, serial_number, model, product, value_str, value_num
FROM telemetry.metrics
WHERE device_id = 'YOUR_DEVICE_ID'
  AND metric = 'battery_voltage'
  AND sent_at >= toDateTime('2026-06-07 00:00:00', 'UTC')
  AND sent_at <  toDateTime('2026-11-13 00:00:00', 'UTC')
ORDER BY sent_at, serial_number, model, product, value_str, value_num
LIMIT 100000
```

Количество строк при таком фильтре может быть меньше 100000. Выберите устройство,
метрику и диапазон, для которых действительно достаточно записей.
Для динамических диапазонов Grafana Direct используйте явные границы
`sent_at >= toDateTime(intDiv(${__from}, 1000))` и
`sent_at < toDateTime(intDiv(${__to}, 1000))`, сохраняя правило `[from,to)`.
Не сравнивайте его с запросом, включающим обе границы.

Для будущего сравнения фиксируйте число панелей, интервалы, лимиты и состояние
кэша. API не кэширует ответы; data cache Superset отключён. Кэш ClickHouse,
браузера и возможности query cache Grafana нужно учитывать отдельно.
Superset может применять собственные row limits: измените их в настройках chart
для теста 100000 строк. API читает limit+1 строку, чтобы определить усечение.
SQL time смотрите в Query Inspector / SQL Lab и доступном read-only `system.query_log`
(только если администратор разрешил SELECT); время HTTP и время render измеряйте отдельно.

При обычном просмотре Admin показывает событие `iframe.onload` отдельно от
результатов runner. Оно может сработать даже для страницы ошибки и не означает
завершение запросов и отрисовки. Получение данных и приблизительную готовность
панелей оценивает только браузерный runner по правилам из начала README.
Нагрузочного тестирования нет; последовательные браузерные измерения Playwright
описаны в начале README.

## 9. Проверка трёх сценариев

1. Superset: вкладка в Admin → в browser Network есть `/api/superset/embed`,
   затем `/embedded/<UUID>` на Superset. SQL идёт из Superset к ClickHouse;
   endpoints `/api/metrics/*` не задействованы.
2. Infinity: вкладка Grafana Infinity → Grafana Query Inspector → backend запрос
   к внутреннему API. В `docker compose logs telemetry-api` видны GET `/api/metrics/*`.
3. Direct: вкладка Grafana Direct → Query Inspector показывает ClickHouse SQL,
   при обновлении панели запросов `/api/metrics/*` в API нет.

Вспомогательная read-only проверка с Windows-хоста:

```powershell
python scripts/verify_stack.py
```

Она проверяет Admin HTML и proxy, Superset login page, provisioning Grafana,
Infinity healthcheck и настоящий backend query `/health` через Docker network,
а также live login → CSRF → запрос guest token на несуществующий UUID.
Ожидаемый ответ Superset — `EmbeddedDashboard not found`: проверяется, что
аутентификация, CSRF и разрешение технического пользователя работают.
Выдача token для настоящего dashboard, подключение ClickHouse и render dashboard
требуют вашей ручной настройки и этим скриптом не подтверждаются.
Для доступа к Docker Desktop нужны права текущего пользователя.

API unit tests (не обращаются к БД):

```powershell
docker compose run --rm --no-deps --user root -v "${PWD}/telemetry-api/tests:/tests" telemetry-api sh -c 'pip install --quiet pytest==8.3.5 && python -m pytest /tests -q -p no:cacheprovider'
```

Проверка dialect и подстановки пароля без соединения с ClickHouse:

```powershell
docker compose exec superset python /app/test_config.py
```

Проверка существующих metadata ID с пересозданием контейнеров (volumes сохраняются):

```powershell
python scripts/verify_persistence.py
```

Последняя команда ненадолго прерывает работу стенда. Она не создаёт тестовых дашбордов.

## 10. Логи и диагностика

```powershell
docker compose ps
docker compose logs --tail 100 telemetry-api
docker compose logs --tail 100 superset
docker compose logs --tail 100 grafana
docker compose logs -f admin telemetry-api
```

| Симптом | Что проверить |
|---|---|
| Compose требует переменную | Заполнены ли служебные секреты в корневой `.env` |
| API 503 ClickHouse | Host из контейнера, VPN/firewall, HTTP/TLS порт, read-only права, timeout |
| Setting max_execution_time locked | Серверный read-only профиль должен разрешать эту настройку |
| API 422 | Timezone, `start < end`, API_MAX_RANGE_DAYS, API_MAX_ROWS |
| API embed 409 | Dashboard ещё не настроен или вместо embedded UUID указан числовой ID |
| API embed 502 | Health Superset, tech username/password, права роли, UUID |
| Superset iframe blocked | ADMIN_ORIGIN, Allowed domains dashboard, CSP и browser console |
| Superset 403 у гостя | Dashboard опубликован, Embed включён, UUID совпадает, guest role не изменена |
| Infinity host not allowed | Полный URL начинается с TELEMETRY_INTERNAL_URL, allowedHosts пересозданы |
| Infinity нет строк | Backend parser, пустой root selector, device/metric/время и типы columns |
| Grafana iframe login | Anonymous отключён; войти в UI того же localhost |
| Dashboard URL не обновился | Нужен up --force-recreate, затем «Перезагрузить» в Admin |
| Порт занят | Изменить *_PORT и связанные browser URL/origin в .env |

Прямой API порт не включает CORS: Admin использует same-origin proxy `/api` Nginx,
а Infinity backend — внутреннюю сеть. Отдельный CORS для браузерного доступа не нужен.
Для сертификатов частного CA потребуется смонтировать доверенный CA в контейнеры
или настроить TLS соответствующего datasource; отключение verify предназначено только
для диагностики. Grafana имеет отдельный флаг пропуска проверки сертификата.

## 11. Остановка, перезапуск, сохранность

```powershell
docker compose stop
docker compose start
# Либо удалить только контейнеры и сеть, сохранив volumes:
docker compose down
docker compose up -d --wait --wait-timeout 300
```

Не используйте `docker compose down -v`: это удалит metadata и вручную созданные
дашборды. Volumes: `telemetry-benchmark_superset_data` и
`telemetry-benchmark_grafana_data`. Их содержимое не попадает в Git.
Перед резервным копированием SQLite остановите соответствующий сервис, затем
скопируйте содержимое volume средствами Docker Desktop; сохраните `.env` отдельно.
Не меняйте project name, если хотите подключить прежние volumes.

Superset использует один gunicorn worker с потоками и SQLite timeout 30s.
SQLite поддерживается выбранной версией и достаточна для этого одиночного
локального стенда. Это не конфигурация для production или масштабирования metadata.
Предупреждение Flask-Limiter об in-memory storage ожидаемо: Redis не требуется.
Grafana хранит metadata в своей SQLite в постоянном volume.

Пароль Grafana из `.env` применяется при **первом** создании её пользователя.
После этого меняйте его через UI или штатный `grafana cli admin reset-admin-password`
и синхронизируйте `.env`; повторная подстановка GF_SECURITY_ADMIN_PASSWORD не сбрасывает
существующий пароль. Скрипт verify_stack использует пароль из окружения.

## Структура

```text
admin/                  Vite + JavaScript + Embedded SDK, Nginx proxy
telemetry-api/app/      FastAPI, фиксированные SELECT, клиент Superset
telemetry-api/tests/    проверки API без внешней БД
grafana/               образ с плагинами и provisioning двух datasource
superset/              конфигурация, bootstrap SQLite, optional connection setup
scripts/               генерация .env и read-only smoke проверки
docker-compose.yml     четыре сервиса, сеть и три volumes (включая benchmark results)
.env.example           все настройки; настоящая .env игнорируется
```

Для небольшой Admin использован обычный JavaScript с Vite: React не требуется
для трёх вкладок и iframe, это уменьшает число зависимостей.

## Официальные источники

- [ClickHouse datasource: provisioning, протоколы и read-only ограничения](https://grafana.com/docs/plugins/grafana-clickhouse-datasource/latest/configure/).
- [Infinity: allowed hosts, healthcheck и provisioning](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/configure/).
- [Superset Embedded SDK и guest-token flow](https://github.com/apache/superset/tree/6.1.0/superset-embedded-sdk).
- [Конфигурация Superset 6.1.0, включая SQLite и guest token](https://github.com/apache/superset/blob/6.1.0/superset/config.py).
- [ClickHouse Connect: совместное использование клиента и sessions](https://clickhouse.com/docs/integrations/language-clients/python/advanced-usage).

Фактические результаты проверок этого экземпляра приведены в [VALIDATION.md](VALIDATION.md).
