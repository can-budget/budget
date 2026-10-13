# 10. Изменения v1.0 → v1.1

12.10.2026. Продукт: до 10 вместо двух; 0/1 active membership на user, creator без привилегий; Apple-only утверждён; QR/сканер/поделиться/brightness; сохранение staging; editing current bank/end_date утверждено, значение current bank отдельно подтверждено.

Техника: calculation_version=2; fixed adjustment + исторические версии конца спринта; update_sprint с optimistic revision и business date; snapshot/архив с SprintRevisions; schema с initial_end_date и sprint_revisions; capacity=10 в контракте; no-op/idempotency правил редактирования. Текущий остаток не заменяет immutable initial. Новое end не переписывает вчерашний день.

Надёжность: сохранены detailed sync/receipts, delayed-entry, widget freshness, audit. Добавлены проверки edit-vs-expense, late-after-adjustment, concurrent last-slot joins, QR и изоляции сред.

Не переносится как утверждённое: v1 запрет изменения даты, max2, только текстовое приглашение, автоматическое удаление всех пользовательских финансовых операций при удалении аккаунта. Последнее — R-01 до production.

Файлы старого ZIP/PDF v1.0 не являются действующим заданием и отсутствуют в новом пакете. Готового приложения/сервера и выполненной публикации по-прежнему нет. Обновлённый статус реальных проверок — qa/VALIDATION.md.
