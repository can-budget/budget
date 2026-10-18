# Этапы и задачи реализации

Статус всех задач реализации в момент передачи: NOT STARTED. Эталонные проверки пакета указаны отдельно в qa/VALIDATION.md и не означают выполнение этих задач. Срок и стоимость оценивает исполнитель после M0; прежний ориентир 18–30 дней для v1 не является сметой v1.1.

## M0 — проверка и запуск проекта

CAN-001: прочитать пакет, выявить противоречия, закрепить версии, структура репозитория/CI, выполнить reference/contract checks.
CAN-002: Apple enrollment/signing, Bundle IDs prod/staging, реальные iOS/устройства; минимальная сборка с Apple-входом/виджетом, подтверждённый путь TestFlight.
CAN-003: сеть/домен/хостинг и постоянные среды; план доступа owner; зафиксировать реальные задержки.
CAN-004: закрыть R-01 deletion policy; согласовать небольшую серию собственных экранов/иконку (R-04).

Выход этапа: письменный аудит, закрытые критические неизвестные, план/оценка по этапам, рабочая сборка-проба и утверждённый UX-набор. Не начинать долгую UI-полировку до проверки Apple и сети.

## M1 — домен и сервер

CAN-005: BudgetDomain v2 + fixtures, серверная адаптация из snapshots/revisions; независимые тесты математических границ.
CAN-006: SQL миграции, membership max1/capacity10, равные права, active sprint, immutable audit/revisions.
CAN-007: backend Apple auth/challenge/refresh/logout/revoke, secrets; пригласить/create/preview/accept/revoke.
CAN-008: add/correct/void, choose_carry, update_sprint; receipts+outbox атомарно, expected version/date и no-op.
CAN-009: consistent sync/snapshot, архив, history, минимальные widget reads, реальная PostgreSQL конкуренция.

Выход: доступный staging API, тесты денег и гонок, сгенерированная актуальная API документация, демонстрация limit10/target edit/offline-retry на уровне запросов. AC-02/03/07–19/23/26 соответствующего серверного объёма.

## M2 — приложение и совместный путь

CAN-010: первый вход, create/join, Membership жизненный цикл и безопасный выход.
CAN-011: цифровой ввод/история/спринт/настройки/итог, адаптивный UI без категорий.
CAN-012: QR/copy/share/scanner, permission, brightness lifecycle, среда.
CAN-013: синхронизированные preview carry/update, сохранение формы при conflict, service events в истории.

Выход: два реальных пользователя проходят полный онлайн-сценарий в тестовой сборке; не только симулятор и mock API. Демонстрация UX, уточнение визуальных дефектов.

## M3 — offline и надёжность

CAN-014: локальные Entry+PendingCommand транзакции, перезапуск, очереди environment/user/membership.
CAN-015: loss-after-commit, согласованные receipts/snapshot, monotonic revision/date, повторы и очередь во время sync.
CAN-016: late entries/прошлый спринт/clock skew, settings conflict vs incoming expense, membership revoke с pending, локальная миграция данных.

Выход: испытания AC-14/15/17–22/29 на настоящей сети/БД и целевых устройствах. Ошибки не «лечатся» очисткой пользовательских данных.

## M4 — виджет и готовность выпуска

CAN-017: App Group snapshot/Keychain scope, timeline/reload/freshness/midnight/completed/deep link.
CAN-018: APNs app/widget каналы и coalescing worker с проверенной доступностью SDK; без обязательства мгновенной доставки.
CAN-019: безопасность/приватность/delete по закрытому R-01, audit/deletion replay, доступность и длительные сценарии.

Выход: AC-24–27/31 с evidence; реальные Home Screen виджеты, системные ограничения описаны честно.

## M5 — production и передача

CAN-020: production deploy, постоянный staging, health/monitoring/backup, восстановление и операционные инструкции.
CAN-021: TestFlight prod build и группа тестировщиков, processing/beta review при необходимости, два физических iPhone.
CAN-022: полная приёмка, исправления, сборка из чистого checkout, инвентаризация owner доступа, ответственность за будущие TestFlight builds.

Выход: все AC проверены, production доступен пользователям, акт сдачи, план обслуживания. «Сервер только на ноутбуке разработчика» или «проект нельзя собрать без его личных файлов» не сдача.
