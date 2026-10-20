# Ручной выпуск Can на два iPhone

20 октября 2026 года владелец выбрал **Ad Hoc с Mac, сборка по команде и установка по кабелю**. Платные сервисы сборки, Xcode Cloud и автоматическая публикация для этого процесса не нужны. Этот выбор заменяет прежний план приёмки через TestFlight.

Владелец подтвердил отсутствие платного Apple Developer Program. Команды подготовлены, но первый подписанный `.ipa` требует настройки платной команды. Установку на настоящие телефоны и реальный Apple login/APNs ещё необходимо выполнить.

## Обычный выпуск после однократной настройки

Скачать репозиторий на Mac с полным Xcode 26+ и Python 3.11+. XcodeGen, CocoaPods, backend-зависимости и отдельный сервис сборки не требуются. Из корня репозитория:

```sh
make ios-doctor
make ios-release
make ios-inspect
make ios-devices
make ios-install IOS_DEVICE="Имя первого iPhone"
make ios-install IOS_DEVICE="Имя второго iPhone"
```

Телефоны можно подключать по очереди. Разблокировать iPhone, подтвердить доверие Mac и включить Developer Mode, перезапустив телефон и подтвердив включение. Имя или идентификатор для `IOS_DEVICE` показывает `ios-devices`. Перед установкой проверяется настоящий UDID выбранного телефона: он должен присутствовать в профилях **приложения и виджета**. Виджет доставляется внутри приложения; после установки пользователь добавляет его на домашний экран или экран блокировки.

`ios-release` создаёт Release archive и выполняет локальный Ad Hoc export. В Xcode 26 метод называется `release-testing`; старое имя `ad-hoc` устарело. Xcode разрешено обновлять provisioning через Apple Developer. Upload в App Store Connect и установка на телефон не выполняются командой сборки.

Результаты находятся в `build/ios-adhoc/<UTC timestamp>/`:

- `export/Can.ipa` — подписанная универсальная сборка с виджетом;
- `Can.xcarchive` — архив для последующего выпуска/диагностики;
- `archive.log`, `export.log` — логи Xcode;
- `release.json` — версии, Git commit и признак локальных изменений (если есть Git checkout), SHA-256 и реальные сроки подписи.

`build/ios-adhoc/latest.json` указывает на последний **успешно проверенный** выпуск. Ошибка сборки или проверки не заменяет предыдущий результат. Артефакты, отчёты и содержащие UDID профили остаются вне Git. Скачанный ZIP без `.git` тоже можно собрать; commit в отчёте будет неизвестен.

`ios-inspect` повторно проверяет последнюю сборку. Другую `.ipa` можно выбрать явно:

```sh
make ios-inspect IOS_IPA="/путь/Can.ipa"
make ios-install IOS_IPA="/путь/Can.ipa" IOS_DEVICE="Имя iPhone"
```

Номер билда генерируется из UTC-времени. При необходимости: `make ios-release IOS_BUILD_NUMBER=42`. Приложение и extension получают одинаковые version/build numbers.

## Что подготовить один раз

1. Оформить Apple Developer Program и добавить платную команду в **Xcode → Settings → Accounts**. Apple указывает $99 за год членства с региональными вариантами цены. Бесплатный Personal Team имеет семидневные профили и не заменяет выбранную Ad Hoc distribution. [Членство Apple](https://developer.apple.com/programs/enroll/), [Personal Team](https://developer.apple.com/help/account/basics/about-your-developer-account).
2. Заполнить **несекретные** значения [Owner.xcconfig](../apps/ios/Config/Owner.xcconfig): `CAN_DEVELOPMENT_TEAM`, `CAN_BUNDLE_ID`, `CAN_WIDGET_BUNDLE_ID`, `CAN_APP_GROUP`, `CAN_KEYCHAIN_GROUP`. Production API уже задан как `https:/$()/151.243.217.6`. Заглушки `YOUR_TEAM_ID` и `com.yourcompany.…` не подходят для выпуска. После первой установки не менять эти идентификаторы при обычном обновлении: от них зависит доступ к прежним данным и Keychain.
3. Открыть `apps/ios/Can.xcodeproj`, выбрать одну платную команду и automatic signing для **Can и CanWidget**. Для основного App ID включить Sign in with Apple, для соответствующих targets — App Groups, Keychain Sharing и Push Notifications. Xcode должен подготовить действующие signing identities и профили. Пароли, приватные ключи и экспортированные signing credentials в исходники не добавлять.
4. Зарегистрировать **оба iPhone** в платной команде. UDID можно получить в Xcode Devices and Simulators; `ios-devices` показывает доступные устройства. Ad Hoc требует App ID, distribution certificate и зарегистрированные устройства. Xcode может управлять профилями автоматически. Скрипт не регистрирует новые телефоны за владельца: если устройство отсутствует в любом из профилей, его нужно зарегистрировать и пересобрать. [Ad Hoc profiles](https://developer.apple.com/help/account/provisioning-profiles/create-an-ad-hoc-provisioning-profile/).
5. Настроить настоящий Sign in with Apple и APNs на уже работающем backend согласно [iOS README](../apps/ios/README.md). `CAN_APPLE_CLIENT_ID` должен совпадать с bundle ID приложения. Apple credentials пока не подключены; восьмичасовая сессия симулятора не является входом для физических телефонов. Приватные `.p8` остаются в защищённом серверном хранилище.
6. Выполнить проверки `make ios-build` и `make ios-test`, затем `make ios-doctor` и `make ios-release`. Первую установку проверить на обоих телефонах: вход Apple, общий кабинет, ввод офлайн/синхронизация, перезапуск, оба вида виджетов и сценарии удаления по [acceptance record](ACCEPTANCE_RECORD.md). Для первого запуска обеспечить доступ в интернет для проверки подписи Apple.

## Подпись и срок действия

Скрипт проверяет кодовые подписи приложения и виджета, embedded provisioning profiles, фактически использованные distribution certificates, Team/App IDs, App Group, приватную/общую Keychain group, Sign in with Apple и production APNs. Simulator, Personal Team, Development, Enterprise и App Store profiles не принимаются за Ad Hoc. Установка каждый раз повторяет проверку экспортированного `.ipa` и прав выбранного телефона.

Архив создаётся с development provisioning, затем Xcode экспортирует и переподписывает shipping bundle для Ad Hoc. Installer берёт `.app` **из экспортированного `.ipa`**, а не из development-signed архива. Для финального приложения и backend-регистрации APNs требуется `production`; это проверяется после экспорта. `devicectl` принимает `.app`, поэтому helper распаковывает проверяемую `.ipa`, сохраняя права executable-файлов.

Команда установки обновляет существующее приложение без вызова uninstall. Не удалять «Можно» перед обычным обновлением: на телефоне могут находиться неотправленные записи. Сохранять прежние bundle IDs, App Group и Keychain groups.

Apple не гарантирует ровно год. Профили обычно действуют не более года, сертификаты тоже имеют срок. Отчёт показывает ближайшую границу: минимум сроков **профилей и настоящих сертификатов приложения/виджета**. `renew_before` — наш запас 30 дней, а не правило Apple. При коротком сроке helper выводит предупреждение; обновить сертификат/профили через Xcode и пересобрать. [Apple TN3125](https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles).

До ближайшего срока выпустить и поставить новую сборку на **оба** телефона. Продление членства само по себе не переподписывает установленную версию. Отзыв подписи или прекращение членства может ограничить работу раньше указанной даты. Ad Hoc не имеет ограничения TestFlight в 90 дней; само приложение остаётся ограничено действующей подписью.

## Связь с backend CI/CD

Backend продолжает автоматически обновляться на VPS из `main`. Установленные приложение и виджет серверная выкладка не заменяет. Изменения UI, клиентских расчётов, виджетов или встроенного API origin доставляются новой ручной iOS-сборкой. Сервер должен сохранять совместимость с установленными версиями.

Существующий GitHub iOS workflow проверяет Simulator build и XCTest без владельческих credentials. `ios-release`, `ios-install`, регистрация устройств, upload и хранение signing credentials не добавлены в CI и не входят в `make verify`.

## Границы проверки

Локальные unit-тесты release helper используют синтетические профили/сертификаты и fake Apple commands. Они включены в `make infra-test` и `make verify`; Apple, Keychain и устройства ими не изменяются. Проверки Simulator/XCTest проекта не доказывают правильность реальной подписи.

20 октября 2026: `make verify` прошёл, включая 27 тестов release helper. Release archive для физического iPhone собран с `CODE_SIGNING_ALLOWED=NO`; оба targets содержат платформу iPhoneOS. Такой архив нельзя установить. Отдельно проверено сохранение подписи Simulator bundle после ZIP-упаковки и распаковки helper; это проверка целостности упаковки, а не Ad Hoc distribution. `ios-doctor` и `ios-release` корректно останавливаются на отсутствующей владельческой настройке до попытки подписи.

**Ещё не выполнены:** Ad Hoc archive/export с владельческой подписью, чтение реальных сроков профилей, установка на оба телефона и приёмка Apple login/APNs на устройствах. Для этого сначала необходимы платное членство, реальные идентификаторы и signing identities.
