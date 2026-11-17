# Фактические проверки

## Дополнение: браузерный benchmark, 16 ноября 2026

Реализованы файловая очередь в существующем API, один host runner Playwright,
обновлённая Admin и экспорт. Новых Docker-сервисов нет. Изменения применены
только к API/Admin; SQL, существующие dashboards, datasource, секреты и
механизм SSH-подключения не изменялись.

Проверено:

- **26 API-тестов**: существующие telemetry endpoints, очередь, singleton job/runner,
  lease timeout, release, запрет measured до preflight, размеры, Origin, retention,
  replay-защита, схемы отчётов, p50/p95 и исключение ошибок.
- **12 runner-тестов**: контракты SQL/ответов, отбраковка фильтров и 99999 строк,
  серии 1/10/30 повторений и последовательное исполнение. Четыре из них используют
  настоящий Chromium и синтетические страницы/ответы Grafana и Superset.
- Сборка API и Admin; синтаксис Python и `git diff --check`.
- В настоящей Admin через Chromium: начальное открытие без iframe, выбор Direct
  без предварительной загрузки Superset, открытие всех трёх существующих embeds.
  Superset успешно получил настоящий guest token и открыл embedded страницу.
- Создание pending job кнопкой Admin, выполнение runner, переход в
  **failed/preflight_failed**, JSON/CSV в API и на Windows-хосте. Успешных измерений
  и статистики готовности в этом отчёте нет — отказ данных не маскируется успехом.
- На существующей панели Superset реальный observer вернул
  `success=false, errors=[filtered_query]`.

Ограничения текущего окружения:

1. Узел `clickhouse-ssh-tunnel:18123` недоступен из Docker; пользователь сообщил,
   что сейчас не может запустить туннель. Ни один успешный benchmark реальных
   данных не заявляется.
2. В Superset у `bench - raw records` сохранён фильтр `metric = battery_voltage`,
   а `orderby` пустой. У Grafana Direct/Infinity выборка без такого фильтра и
   с сортировкой по sent_at/device_id/metric. Настройки прочитаны только для
   диагностики; они не исправлялись. Корректная серия до устранения различий
   пользователем должна завершаться failed.
3. Реальные renderer-состояния с успешно полученными данными и равенство всех
   трёх наборов данных проверить нельзя. Успешный путь renderer observer проверен
   на **синтетических fixtures**, а не выдаётся за подтверждённый реальный render.
4. DOM-готовность всегда обозначена приблизительной; SQL-время — unavailable.
   Таблицы/настройки ClickHouse и system.query_log не изменялись.

Для запуска с реальными данными следуйте разделу Git Bash в начале README.
Runner после проверки остановлен, API/Admin доступны. Сохранён тестовый failed
отчёт, чтобы видеть диагностику и проверить экспорт в интерфейсе.

## Предыдущие инфраструктурные проверки, 12 ноября 2026

Проверено 12 ноября 2026 на Docker Desktop для Windows, Linux containers.
Внешний ClickHouse не настроен: `CLICKHOUSE_HOST` и `CLICKHOUSE_USER` пустые.
Dashboards, charts и datasets не создавались.

| Проверка | Результат |
|---|---|
| `docker compose config --quiet` | Успешно |
| Сборка четырёх образов | Успешно, включая frontend и установку двух plugins |
| `docker compose up -d --wait` | Все четыре сервиса healthy |
| Публикация портов | Только 127.0.0.1:5173, 8000, 3000, 8088 |
| Admin HTML и same-origin `/api/config` proxy | HTTP-запросы успешны |
| API `/health`, OpenAPI и ошибки отсутствующего подключения | Проверены; API не зависит от доступности ClickHouse при старте |
| API unit tests | 10 passed, без внешней БД |
| Grafana provisioning | Два datasource с постоянными UID |
| Grafana plugin versions | Проверены через API Grafana, соответствуют Dockerfile |
| Infinity custom healthcheck | Grafana обращается к API через Docker network |
| Infinity backend query | Настоящий `/api/ds/query` возвращает `ok` из `telemetry-api /health` |
| Superset SQLite init | Миграции и создание admin/tech успешны |
| Superset tech authentication | Настоящие login и CSRF успешны |
| Superset guest-token endpoint | Для несуществующего UUID ожидаемый HTTP 400 `EmbeddedDashboard not found`; не 401/403 и не ошибка CSRF |
| Superset dialect/password store | Драйвер загружается; URI/порт/TLS/пароль со спецсимволами подставляются из окружения, без внешнего соединения |
| Persistent metadata | ID существующих пользователей/объектов Superset и datasource Grafana сохранились после `--force-recreate` всех контейнеров |
| Frontend dependency audit | npm reported 0 vulnerabilities на момент сборки |
| Python syntax / `git diff --check` | Успешно |
| `.env` | Игнорируется Git; реальные секреты не добавлены в отслеживаемые файлы |

Тесты API проверяют ограничения и timezone фильтров, отсутствие произвольной
агрегации/интервала, передачу значений параметрами SQL, усечение ответа,
работу без ClickHouse, отсутствие секретов в публичной конфигурации и обмен
login/CSRF/cookie/guest token на mock HTTP transport.

Сохранение metadata проверено без тестового dashboard: создание dashboards
запрещено задачей. Проверка не утверждает, что вручную созданный dashboard
уже был сохранён и восстановлен.

## Что не проверено

- Реальная сеть, TLS, права и чтение `telemetry.metrics` внешней ClickHouse.
- Выполнение реальных SELECT драйвером API, Superset и Grafana Direct.
- Выдача настоящего guest token для существующего embedded dashboard:
  Superset 6.1.0 проверяет существование dashboard до выдачи token.
- Browser rendering, Embedded SDK и iframe с реальными графиками.
  Проверены сборка frontend и HTTP-доступность, без автоматизации браузера/Playwright.
- Получение 100000 реальных строк и любые performance-показатели.

После ручной настройки выполните шаги из README для каждого из трёх сценариев.
Скрипты `verify_stack.py` и `verify_persistence.py` не обращаются к внешней
ClickHouse и не создают dashboards. Контейнеры оставлены запущенными.
