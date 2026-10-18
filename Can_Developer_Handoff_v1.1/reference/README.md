# Расчёт версии 2

Это pure domain reference, не сервер и не готовый iOS-модуль интеграции. Здесь нет сети, Apple, транзакций, авторизации, queue reconciliation или real-time clocks.

Вход содержит `initial_minor`, `start_date`, **оригинальный** `end_date`, `as_of_date`, `days` (topups/expenses), `choices` и `revisions`.

Адаптер из API snapshot:

```text
initial_minor = snapshot.sprint.initial_minor
start_date = snapshot.sprint.start_date
end_date = snapshot.sprint.initial_end_date   # НЕ последний sprint.end_date
as_of_date = snapshot.business_date
days = snapshot.daily_totals (day, topups_minor, expenses_minor)
choices = {decision.day: decision.mode}
revisions = [
  {
    effective_day: revision.effective_day,
    sequence: revision.sequence,
    bank_adjustment_minor: revision.bank_adjustment_minor,
    end_date: revision.end_date_after
  }
]
```

Fixed delta складываются движком из revisions. Не добавлять их повторно в topups/expenses. API-адаптер должен фильтровать данные нужного спринта, нормализовать текущие effective entries и сделать доказанное одноразовое наложение unknown pending. Объём изменения аккаунта/истории зависит от закрытия R-01, не задаётся математическим движком.

Проверки:

```sh
cd reference
python -m unittest -v test_budget_engine.py
swiftc BudgetEngine.swift verify.swift -o /tmp/can-verify
/tmp/can-verify golden_cases.json
```

240 fixtures: 224 неизменённых регрессионных сценария из v1 и 16 добавленных сценариев v1.1. Новые отдельно в v11_core_cases.json. У новых явно заданы контрольные банк/сегодня/дни/carry; весь expected snapshot использован для Python/Swift parity. Паритет не является независимым доказательством всех правил или проверкой production-системы.
