# 11. Официальные источники и границы проверки

Ссылки для исполнителя, просмотр 12.10.2026. Конкретные SDK availability/entitlements/подпись и требования выпуска повторно проверяются в M0. У некоторых страниц Apple веб-представление требует JavaScript; эта ссылка не означает выполненный интеграционный тест. Продуктовые правила Can — решения заказчика, не рекомендации источников.

**S-01 — Apple: роли Individual/Organization.** https://developer.apple.com/help/account/access/roles/
Делегирование команды и ограничение App Store Connect users при Individual membership.

**S-02 — Apple: TestFlight overview.** https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/
Сборки, группы, внутренние/внешние тестировщики, beta review.

**S-03 — Apple: TestFlight tester information.** https://testflight.apple.com/
Период тестирования сборки до 90 дней.

**S-04 — Apple: Keeping a widget up to date.** https://developer.apple.com/documentation/widgetkit/keeping-a-widget-up-to-date
Timeline и системная политика обновления; проверить в актуальном SDK.

**S-05 — Apple: What’s new in widgets, WWDC25.** https://developer.apple.com/videos/play/wwdc2025/278/
WidgetKit push и ограничения обновлений, не постоянная realtime-подписка.

**S-06 — Apple: Pushing background updates.** https://developer.apple.com/documentation/usernotifications/pushing-background-updates-to-your-app
Фоновые уведомления — best effort; проверять entitlement и правила SDK.

**S-07 — Apple: Keychain Services.** https://developer.apple.com/documentation/security/keychain-services
Хранение ключей/токенов, выбор accessibility/access group требует device tests.

**S-08 — Apple: Sign in with Apple REST API.** https://developer.apple.com/documentation/signinwithapplerestapi
Проверка/обмен токенов и жизненный цикл Apple identity.

**S-09 — Apple: Offering account deletion.** https://developer.apple.com/help/app-review/guideline-reference/5-1-1-account-deletion
Удаление учётной записи и данных, не юридическое одобрение произвольного сохранения общей истории.

**S-10 — GitHub: Repository roles for an organization.** https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization
Разделение Write/Maintain/Admin и контроль владельца.

**S-11 — PostgreSQL: Partial Indexes.** https://www.postgresql.org/docs/current/indexes-partial.html
Уникальность строк по условию для active Membership.

**S-12 — PostgreSQL: Explicit Locking.** https://www.postgresql.org/docs/current/explicit-locking.html
Блокировки строк и транзакционное упорядочивание; протестировать на целевой версии.

**S-13 — Apple: UIScreen brightness.** https://developer.apple.com/documentation/uikit/uiscreen/brightness
Управление яркостью; восстановление предыдущего значения — наше обязательное UX-поведение.
