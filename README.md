# Order Service — практическая работа №8

![CI](https://github.com/konstmalinin-maker/order-service/actions/workflows/ci.yml/badge.svg?branch=main)
![Compose](https://github.com/konstmalinin-maker/order-service/actions/workflows/compose-lab.yml/badge.svg)

Самостоятельный REST-сервис заказов, интеграционный стенд и notification-service.
Контракт: [docs/api-contract.md](docs/api-contract.md), [OpenAPI](docs/openapi.json).
Docker: `ghcr.io/konstmalinin-maker/order-service:latest`.

| Метод | Путь | Результат |
|---|---|---|
| POST | /orders | 201 / 400 / 404 / 503 |
| GET | /orders | 200: список |
| GET | /orders/{order_id} | 200 / 404 |
| GET | /health | 200: SQLite, breaker, pending_events |

Перед записью заказа UserClient вызывает user-service по HTTP через
настоящий сгенерированный SDK из user_contract_client. Таймаут — 2 с,
всего 3 попытки, паузы 0,5 и 1 с. 404 не повторяется. Пять неудачных
логических вызовов открывают circuit breaker на 30 с; одна HALF_OPEN-проба
проверяет восстановление. При 503 заказ и событие не сохраняются.
Сумма хранится в целых копейках; лишние поля и некорректные типы отклоняются.

Создание заказа и outbox-события атомарно. Dispatcher публикует OrderCreated
с publisher confirms в durable очередь. Consumer подтверждает сообщение
после записи SQLite, повторный event_id не создаёт второе уведомление.
Проблема брокера не отменяет принятый заказ: событие ждёт восстановления.

```bash
# Перегенерировать SDK из зафиксированного контракта
bash scripts/generate_client.sh
# Настоящий брокерный интеграционный тест
TEST_RABBITMQ_URL='amqp://lab:lab-demo@localhost:5672/%2F' python -m pytest -q --cov --cov-fail-under=80
# kind: создаёт локальный одноузловой кластер, три реплики и проверяет HTTP/очередь
bash k8s/run_kind_lab.sh
```

Workflow Compose acceptance and screenshots поднимает весь стенд на отдельном
Linux runner, проверяет CRUD, ошибки, restart, 429, остановку upstream,
breaker/recovery и сохраняет реальные снимки Swagger/Grafana/Jaeger.
Артефакт compose-lab-evidence содержит исходные HTTP-результаты, логи,
нагрузку hey, JSON распределённого трейса и PNG без изменения содержимого.
Workflow CI and GHCR дополнительно выполняет реальный kind-стенд;
артефакт kubernetes-lab-evidence содержит kubectl и проверку трёх реплик.

## Быстрый запуск полного стенда

Нужны Git, Docker Engine с Compose v2 и свободные порты 80, 3000, 9090, 15672, 16686.
Репозитории должны лежать рядом:

```bash
git clone https://github.com/konstmalinin-maker/user-service.git
git clone https://github.com/konstmalinin-maker/order-service.git
cd order-service
docker compose build
docker compose up -d --no-build --wait
docker compose ps
```

Клиентские запросы идут через Nginx: `http://localhost/api/users`,
`http://localhost/api/orders`. Межсервисный адрес — `http://user-service:5001`.
В обычном Docker порты API наружу не публикуются.

```bash
curl -i -X POST http://localhost/api/users -H 'Content-Type: application/json' -d '{"email":"student@example.com","name":"Student"}'
curl -i -X POST http://localhost/api/orders -H 'Content-Type: application/json' -d '{"user_id":1,"total":1250.50}'
curl http://localhost/api/orders
```

Лимит Nginx — 10 запросов/с с одного IP, превышение даёт 429.
Учебные RabbitMQ: `lab` / `lab-demo`. Доступ к интерфейсам ограничен loopback.
Grafana использует анонимную роль Viewer. Такие настройки предназначены для локальной лаборатории.

| Интерфейс | Адрес |
|---|---|
| Swagger User | http://localhost/docs/users/docs |
| Swagger Order | http://localhost/docs/orders/docs |
| RabbitMQ | http://localhost:15672 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000/d/pr8/ |
| Jaeger | http://localhost:16686 |

SQLite-файлы раздельные: users-data, orders-data, notifications-data.
`docker compose restart` и `docker compose down` сохраняют данные.
`docker compose down -v` удаляет учебные данные.
Общий том SQLite нескольких реплик используется только на одном узле kind;
для промышленного кластера нужен серверный SQL и миграции.

## Проверки

Python 3.12:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
ruff check .
python -m pytest -q --cov --cov-fail-under=80
```

CI выполняет Ruff, pytest и контроль покрытия не ниже 80%, собирает Docker.
При push в main образ публикуется в GHCR с тегами latest и SHA коммита.
Точный digest сохраняется в артефакте image-digest.
Контракты в docs зафиксированы отдельным коммитом до реализации.

## Дополнительные задания

A — SQLite, слой репозиториев, WAL, постоянные тома, изолированные БД тестов.
B — FastAPI/Pydantic, локальные Swagger assets, generated SDK openapi-python-client.
C — Nginx, порт 80, маршрутизация и ограничение 10 запросов/с.
D — RabbitMQ, transactional outbox, отдельный notification-service, manual ACK,
идемпотентная запись уведомлений и интеграционный тест.
E — prometheus-flask-exporter через WSGI-монтирование Flask в FastAPI,
метрики запросов/latency/ошибок/breaker, Prometheus и готовый дашборд Grafana.
F — OpenTelemetry SDK, HTTPX-инструментация, общий контекст traceparent, Jaeger.
G — CI/CD обоих репозиториев, покрытие и GHCR.
H — Deployment/Service/ConfigMap/PVC, probes и проверенное масштабирование kind до 3 реплик.

## Отчёт и подтверждения

[Отчёт с 26 скриншотами](PR8_Отчёт_Малинин.md), [исходные журналы и результаты](docs/pr8-evidence/README.md).
