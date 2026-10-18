# 04. Архитектура, данные и API

## Компоненты

iOS: Swift / SwiftUI, WidgetKit, SwiftData (минимум iOS 17 как проектная база), Keychain, App Group. Чистый `BudgetDomain` отдельно от UI/сети. API client, local repository/outbox, sync coordinator, авторизация и widget snapshot store раздельны.

Backend: один Python/FastAPI сервис, PostgreSQL, worker для best-effort APNs. HTTPS. Docker/версионированные миграции/CI. Без Redis, Kafka, Kubernetes и микросервисов. Backend единственный источник подтверждённого общего состояния. Телефон делает локальную предварительную проекцию. APNs только сигнал, не транзакционный журнал.

Версии инструментов и зависимостей закрепить в M0 по поддерживаемым версиям на дату реализации. Наличие Swift-компилятора Linux в проверке пакета не заменяет Xcode/iOS SDK.

## Данные

Users + Apple identity credentials; Cabinets; Memberships; Sprints; Entries + EntryRevisions; CarryDecisions; SprintRevisions; CommandReceipts; Invitations; Sessions/RefreshTokens/AuthChallenges; Devices/WidgetTokens; PushOutbox; DeletionJobs.

`creator_id` кабинета информационный. `Membership.left_at IS NULL` означает активное участие. Уникальный partial index на user_id ограничивает одно активное членство. Лимит десяти не выражается простым CHECK/индексом по одной строке: проверяется под блокировкой кабинета. Последний выход архивирует кабинет и отзывает приглашения/доступы к нему.

Sprints имеет immutable initial_minor/initial_end_date и текущий end_date как кэш. calculation_version=2. SprintRevisions хранит sequence, effective_day, bank_before/after, фиксированную delta, end_before/after, автора, command_id и basis revision. Повтор не создаёт revision. Сумма поправки не маскируется под обычный expense/topup.

`contracts/schema.sql` — проект целевой схемы, НЕ готовая проверенная миграция из deployed v1. Он ещё не выполнен на PostgreSQL. Исполнитель обязан подготовить миграции, роли БД, интеграционные тесты CHECK/FK/UNIQUE/гонок и реальные планы резервирования. Базовые NULL/CASCADE пересмотреть после R-01 политики удаления.

## Общая транзакция команды

Аутентификация и проверка активной личности → transaction → пользовательская блокировка при смене членства → кабинет → проверка текущего Membership → receipt по (user_id,command_id) → сравнение hash → проверки expected revision/date/прав → изменение → увеличение cabinet.revision → receipt + outbox в той же транзакции → commit. При повторе идемпотентной успешной leave-команды вернуть безопасный ограниченный receipt без выдачи уже закрытых финансовых данных; не требовать повторного членства для подтверждения собственного выхода.

Зафиксировать единый порядок блокировок user→cabinet(отсортированные id)→entity, не смешивать обратный порядок в join/leave/delete. Команды обычных покупок не требуют expected revision для независимой операции. Любой новый write после выхода запрещён. Старые receipt-only запросы должны раскрывать только состояние собственной команды, а не обновлённый чужой snapshot.

При повторном command_id с другим payload возвращать conflict, а не применять новый смысл. Entry.id уникален независимо от command_id. Получение receipt и snapshot должно быть согласовано: см. SYNC_PROTOCOL. Финансовые receipts не удалять по короткому TTL, пока существует относящаяся к ним история.

## Особые команды

`add_entry`: независимая покупка, сумма и вид валидны, дата в периоде, прошлый завершённый период требует allow_completed. Не подставлять current sprint вместо sprint_id из pending. При повторном входе в тот же кабинет записи от завершённого Membership не становятся автоматически валидными: membership-at-entry проверяется по истории и подтверждённым данным.

`correct_entry`/`void_entry`: expected_entry_revision. При устаревании 409 без silent overwrite. Автор исходной записи остаётся, редактор записывается отдельно. Ручная смена occurred_at отсутствует.

`choose_carry`: expected_cabinet_revision, дата и единственность sprint+day. First accepted. На старый preview подтверждение повторяется с новым command_id после пересмотра.

`update_sprint`: expected_cabinet_revision + expected_business_date, только dirty fields bank_minor/end_date. Под блокировкой вычислить bank, delta и новый N. Изменение состояния/даты → 409, без «подстроить target молча». No-op успешно записывает receipt, не создаёт revision. Способ carry сохранён. Редактирование требует отправки локальной очереди текущего устройства до получения базы формы.

`accept_invitation`: lock user then cabinet, пользователь без другого членства, кабинет не архивирован, приглашение валидно, участников <10. Создание Membership и consumption приглашения атомарны. Два пользователя на последний слот: один успешный, один cabinet_full. Повтор успешной команды не потребляет второй код.

## Контракт

`contracts/openapi.yaml`: login/refresh/logout/Apple notifications, me, create cabinet, preview/accept invitation, общий POST commands, sync/snapshot, архив/entries/projection, devices/widget token/widget snapshot и health. Новый тип команды update_sprint включён в дискриминированный Command. Snapshot и архивная проекция включают sprint_revisions. Участники maxItems=10; calc_version=2. Счётчики API сами по себе не обеспечивают инвариантов — нужна транзакция.

QR генерируется на устройстве; отдельный QR-server не нужен. Изменения служебной истории получает клиент из sprint_revisions, а ordinary history — из Entries. Нельзя дважды прибавить delta в обоих источниках.

Чтения страницы истории с устойчивым cursor по occurred_at/id; не offset для активного списка. Все ID из запроса проверяются на принадлежность cabinet/sprint; UUID не является авторизацией. Ошибки стабильно кодированы, без внутренних SQL и stack traces.

## Версии и пример

Спецификация 1.1, API 1.1.0 и расчёт 2 — разные номера. calculation_version=2 нужен всем устройствам; неизвестная версия запрещает локальный расчёт по старой формуле. Финансовые данные существующей v1-системы не мигрированы этим пакетом: такой системы не предъявлено.

Пример edit: `type=update_sprint`, payload `sprint_id`, `expected_cabinet_revision`, `expected_business_date`, `bank_minor=900000` (9 000) и/или `end_date`. При 8 800 банк delta=20 000 minor. Не отправлять initial_minor в edit.

Официальные сведения о partial indexes и row locks — [S-11], [S-12] в SOURCES.md. Они обосновывают механизм, но не доказывают корректность непроверенной миграции/транзакции этого проекта.
