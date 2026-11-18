# Исходные подтверждения практики №8

PNG включены в основной отчёт по соответствующим разделам. screenshots.json
фиксирует SHA-256 каждого из 26 скриншотов, подпись и источник.
Содержимое скриншотов не перерисовывалось и не заменялось нарисованными окнами.
В PDF/Word при размещении части терминальных снимков скрыто пустое нижнее поле;
исходные PNG встроены без изменения байтов и приложены отдельно.

Полный стенд Compose и kind выполняются на отдельных временных Linux runner.
Терминальные изображения показывают реальные curl/pytest и исходные журналы
Docker/kubectl/hey/GHCR; для журналов runner место выполнения указано в отчёте.
Графики Grafana и дерево Jaeger сняты браузером с работающих приложений.

Основной итоговый Compose: https://github.com/konstmalinin-maker/order-service/actions/runs/44308798651
Повторная проверка main CI/GHCR/kind: https://github.com/konstmalinin-maker/order-service/actions/runs/44308798650
Исходный kind-журнал на скриншотах: https://github.com/konstmalinin-maker/order-service/actions/runs/44302454714
User CI/GHCR: https://github.com/konstmalinin-maker/user-service/actions/runs/44304910936

compose-main/verification.json — HTTP-проверки, restart, 429, 503, breaker/recovery.
compose-main/cross-service-trace.json — исходный Jaeger trace: успешный POST 201,
User GET и OrderCreated publish/consume в одном контексте.
compose-main/breaker-open-metric.json — фактическая метрика 1 во время отказа.
compose-main/hey-load.txt — 20 секунд по 5 запросов/с; hey-gateway.txt — отдельный burst.
kubernetes/ — исходный kubectl, три реплики и чтение данных из каждого pod.
*-tests-final.txt, *-coverage-summary.json — unit и RabbitMQ integration тесты.
*-publish-excerpt.txt — дословные строки успешной публикации образов в GHCR.
Ссылки на CI и digest образов — sources.json.

Скриншоты limiter/consumer включают ранее полученные реальные журналы того же
стенда. Сам ранний прогон остановился на выборе трейса; итоговый main-прогон
полностью успешен. Эти журналы подтверждают фактически выполненные операции.
