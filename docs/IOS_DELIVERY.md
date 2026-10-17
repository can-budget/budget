# Выпуск Can на два семейных iPhone

Проверено по документации Apple 13 октября 2026 года. Это инструкция и рекомендация; подпись, доступ к Apple Developer, загрузка сборки и установка на телефоны ещё не выполнялись. Ранее согласованная приёмка через TestFlight остаётся в силе, пока владелец не выберет другой способ.

## Что выбрать при редких обновлениях

Начать стоит с **ручного выпуска с Mac**, сохранив автоматические проверки. Для двух телефонов отдельная автоматизация публикации сейчас добавляет настройку signing и обслуживание ещё одного процесса. Первый выпуск через TestFlight позволит провести уже согласованную приёмку.

Если после этого важнее реже перевыпускать неизменившееся приложение и оба телефона доступны рядом с Mac, практичный вариант — **ручной Ad Hoc Release**. Это предложение, а не выполненное переключение. Если важнее получать обновления на телефонах без подключения к Mac, удобнее оставить TestFlight и заранее планировать новые сборки.

| Способ | Как получаем обновление | Обслуживание |
| --- | --- | --- |
| Ручной TestFlight | Один upload с Mac; оба пользователя ставят сборку через TestFlight | Новая сборка до истечения 90 дней, даже без изменений кода |
| Ручной Ad Hoc с платной командой | Подписанная `.ipa` устанавливается на два зарегистрированных iPhone | Следить за реальными сроками профилей и сертификата; до истечения переподписать и установить обновление |
| Xcode Cloud → TestFlight | Облачные build/test/archive и доставка выбранной группе | Настроить доступ к репозиторию, workflow и контроль ошибок; ограничение 90 дней сохраняется |

Apple ограничивает доступность каждой TestFlight-сборки 90 днями. Автоматизация не продлевает уже установленную сборку: она должна создать и доставить новую. Для нашего процесса разумен внутренний ориентир перевыпуска через 60 дней, оставляющий запас на проверку и ошибки; **60 дней — наша рекомендация, не правило Apple**. [TestFlight overview](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/).

Платная установка с Mac также не бессрочна. У provisioning profile есть `ExpirationDate`; Apple указывает, что срок обычно не превышает года. Не обещаем «ровно год»: перед установкой записываем реальные даты профилей **приложения и виджета**, проверяем сертификат и планируем обновление заранее. Отзыв подписи тоже нельзя игнорировать. [Apple TN3125](https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles).

Бесплатный **Personal Team** — другой режим: его профили истекают через 7 дней. При наличии платного Apple Developer Program нужно выбрать именно платную команду владельца в обоих targets; платная подписка сама по себе не меняет выбранную Xcode команду. [Developer account overview](https://developer.apple.com/help/account/basics/about-your-developer-account).

## Что подготовить один раз

1. На Mac открыть `apps/ios/Can.xcodeproj`, выбрать общую схему `Can`. Конфигурация проекта поддерживает iOS 17+; в проверочном CI выбран Xcode 26.3. Перед загрузкой сверить действующие [требования Apple](https://developer.apple.com/news/upcoming-requirements/).
2. Заполнить **несекретные** значения в [Owner.xcconfig](../apps/ios/Config/Owner.xcconfig): Team ID, bundle IDs приложения и виджета, App Group, shared Keychain group, HTTPS origin production API. Текущий адрес `.invalid` намеренно блокирует сетевую работу.
3. Настроить Apple capabilities и provisioning обоих targets по [iOS README](../apps/ios/README.md). Основному приложению нужен Sign in with Apple; приложению и виджету — соответствующие App Group/Keychain/Push entitlements. Сохранить отдельную приватную группу Keychain приложения. Backend `CAN_APPLE_CLIENT_ID` должен совпадать с bundle ID приложения.
4. Подготовить работающий production API с Apple/APNs настройками. Приватные ключи, signing credentials и UDID устройств не добавлять в исходники. Для TestFlight создать запись приложения в App Store Connect и закрытую группу двух тестировщиков.
5. Выполнить на Mac из корня репозитория:

   ```sh
   make ios-build
   make ios-test
   ```

Эти команды проверяют simulator build и XCTest; они **не** подписывают выпуск и **не** загружают его. Полноценный Apple login, APNs и доступ к App Group нужно дополнительно проверить на подписанных устройствах. Виджет встроен в приложение и доставляется вместе с ним; пользователь затем добавляет его на домашний экран.

## Ручной выпуск через TestFlight

1. Выбрать проверенный commit для выпуска. Увеличить `CURRENT_PROJECT_VERSION`; `MARKETING_VERSION` менять при необходимости. Приложение и extension используют общие значения из `Owner.xcconfig`. Сохранить запись о commit и номере сборки.
2. В Xcode выбрать схему `Can`, физическую iOS build destination и **Product → Archive**. В Organizer проверить подпись обоих targets, затем **Validate / Distribute App → App Store Connect → Upload**. Выпускается один архив с виджетом, не два независимых приложения. Подготовка идентификаторов и версии описана в [Preparing your app for distribution](https://developer.apple.com/documentation/xcode/preparing-your-app-for-distribution).
3. После обработки в App Store Connect проверить export compliance и сведения для тестирования, назначить сборку группе. Семейного пользователя можно пригласить внешним тестировщиком без выдачи ему роли в App Store Connect; первая внешняя сборка проходит Beta App Review. Не считать срок проверки гарантированным. Оба пользователя принимают приглашение и устанавливают сборку в TestFlight. [TestFlight overview](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/).
4. На обоих телефонах проверить Apple login, общий кабинет, ввод офлайн и последующую синхронизацию, перезапуск, виджет и удаление аккаунта по [acceptance record](ACCEPTANCE_RECORD.md). Записать фактическую дату истечения сборки и следующий плановый выпуск.
5. Даже если код не менялся, до истечения срока повторить выпуск с новым build number. Проверить, что **оба телефона установили новую сборку**: одного успешного upload недостаточно.

TestFlight не требует включения Developer Mode для обычной установки. [Enabling Developer Mode](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device).

## Ручная установка Ad Hoc с Mac

Этот путь применяем после выбора владельца. Он предназначен Apple для распространения тестовых сборок на известные устройства и не даёт бессрочной установки.

1. Подключить оба iPhone к Mac и зарегистрировать их UDID в платной команде. Apple допускает до 100 iPhone за год членства; два телефона укладываются в этот лимит. Регистрация нового телефона впоследствии потребует включить его в профили новой сборки. [Devices overview](https://developer.apple.com/help/account/devices/devices-overview).
2. Подготовить Ad Hoc profiles с distribution certificate, правильными App IDs, двумя устройствами и capabilities для приложения и extension. При automatic signing Xcode может управлять профилями; всё равно проверить фактический результат экспорта. [Create an ad hoc provisioning profile](https://developer.apple.com/help/account/provisioning-profiles/create-an-ad-hoc-provisioning-profile).
3. После проверок создать **Release archive** схемы `Can` и экспортировать `.ipa` для зарегистрированных устройств с Ad Hoc distribution signing. Названия пунктов Organizer зависят от версии Xcode; проверить выбранный метод и итоговую подпись, не полагаться только на название меню. Установить `.ipa` на оба телефона через Xcode/Device Hub либо Apple Configurator. Для локальной установки включить Developer Mode по инструкции Apple. [Distributing to registered devices](https://developer.apple.com/documentation/xcode/distributing-your-app-to-registered-devices).
4. Для нашего Release профиля APNs environment должен быть `production` и в entitlements, и в регистрации устройства на backend. Обычный запуск Debug из Xcode использует `development` entitlement и `sandbox` APNs; это отдельный режим проверки, его не следует случайно принять за выпуск.
5. Записать сроки профилей приложения/extension и сертификата; поставить напоминание заранее, например за 30 дней до ближайшего применимого срока. Выпустить и установить обновление до истечения. При первом запуске обеспечить интернет: для команд, созданных после 6 июня 2021 года, Apple описывает проверку подписи через PPQ. Специальные семидневные offline profiles для нашей задачи не нужны. [Provisioning profile updates](https://developer.apple.com/help/account/provisioning-profiles/provisioning-profile-updates/).

При любом способе обновлять существующую установку с прежними bundle IDs и группами. Не удалять приложение как обычный шаг обновления: локальная очередь может содержать ещё не отправленные покупки. При смене способа распространения сначала проверить сохранение данных и Keychain на контролируемой установке.

## Когда автоматизировать TestFlight

Если ручное обслуживание станет неудобным, рассмотреть **Xcode Cloud**: оно поддерживает TestFlight post-action, ручные запуски и расписание. Для внешней TestFlight-доставки Apple требует clean build. Предлагаемый workflow — тесты, Release archive, upload и доставка закрытой группе; запуск по отдельному iOS release tag либо вручную, плюс контроль приближения срока сборки. Не привязывать выпуск iPhone-приложения к каждому backend commit в `main`. [Xcode Cloud workflow reference](https://developer.apple.com/documentation/xcode/xcode-cloud-workflow-reference).

Apple Developer Program включает 25 compute hours Xcode Cloud в месяц. Это лимит сервиса, не гарантия достаточности для наших тестов; дополнительные планы сейчас не подключаем. Первый workflow требует настройки в Xcode и доступа сервиса к репозиторию владельца. [Get started with Xcode Cloud](https://developer.apple.com/xcode-cloud/get-started/), [First workflow](https://developer.apple.com/documentation/xcode/configuring-your-first-xcode-cloud-workflow).

В текущем репозитории iOS CI выполняет проверки без подписи. Автоматическая публикация iOS, Xcode Cloud и хранение signing credentials **не настроены** этим документом.

## Связь с backend CI/CD

Развёртывание backend из `main` обновляет сервер. Установленные приложение и виджет оно не заменяет. Совместимое исправление сервера можно выпустить без новой iOS-сборки; UI, клиентская синхронизация, локальные расчёты, виджет и встроенный API origin обновляются через новый выпуск приложения.

Работа в ветке `dev` сама по себе не создаёт отдельный сервер и не переключает телефоны на другое окружение. У текущего iOS-приложения один production API origin. Пока существуют старые установленные сборки, серверные изменения должны сохранять совместимость с ними. Изменения правил расчёта требуют согласования и проверки сервера вместе с локальным Swift engine.

## Что действительно проверено

Выполнены чтение конфигурации и CI репозитория, сверка правил по официальным источникам Apple, проверка локальных ссылок этого документа. Ранее прошла статическая проверка проекта; актуальный общий отчёт — [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

**NOT RUN:** Swift/Xcode build и XCTest, владельческая подпись и реальные даты профилей, Apple login/APNs, архивирование/экспорт, TestFlight upload/review, Ad Hoc установка, Xcode Cloud и двухтелефонная приёмка. 17 октября 2026 года владелец подтвердил наличие только обычного Apple ID; платное членство Apple Developer Program пока не оформлено. Реальные Sign in with Apple/APNs credentials и подпись ещё не настроены.
