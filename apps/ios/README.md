# Can for iPhone

SwiftUI app and WidgetKit extension, targeting iOS 17+. Building requires macOS and Xcode 26+. The app currently uses a Russian interface; [project documentation in Russian](../../README.ru.md).

## Configure your API

Create `Config/Local.xcconfig` (ignored by Git). Keep both values consistent:

```xcconfig
CAN_API_BASE_URL = https:/$()/api.example.com
CAN_API_HOST = api.example.com
```

For an IP endpoint, use the same publicly routable IP in both settings and a publicly trusted certificate with a matching IP SAN. `$()` preserves the double slash in Xcode configuration syntax. The checked-in `Owner.xcconfig` supplies placeholders; its optional local include is used by Xcode and the Python build helpers. Do not store passwords or signing keys in either file.

## Build

From the repository root:

```sh
make ios-project-check
make ios-build
make ios-test
make ios-sideload
```

The last command creates `build/ios-sideload/<timestamp>/Can-SideStore.ipa`, containing the app and its widget. It does not sign with an Apple Account or install anything. Import into SideStore on each phone and keep the widget extension. Keep the same signing account and identifiers when updating, and preserve local data.

Detailed setup: [iPhone installation](../../docs/IOS_DELIVERY.md) and [signature renewal](../../docs/IOS_AUTO_REFRESH.md). Paid Ad Hoc helpers remain available through `make ios-doctor`, `make ios-release` and `make ios-install`; they require paid Apple membership and configured team/identifiers in `Local.xcconfig`.

## Simulator

`make ios-test IOS_DESTINATION="platform=iOS Simulator,id=UUID"` selects a Simulator explicitly. Tests cover calculations, draft projections, offline reconciliation, account deletion and rewritten signing groups.

For an interactive owner-operated test, `make ios-simulator` builds a dedicated Simulator app and uses the operator's SSH access to issue a separate eight-hour synthetic session. Configure `--ssh-host` through `SIMULATOR_ARGS` for your server. This writes a test identity to the selected API, so it is deliberately excluded from `make verify`. It is not a public bypass-login route.

## Accounts and widgets

There is no public registration. The operator creates accounts with `python -m can_api.local_accounts create --login member1` inside the API runtime; the CLI prompts for a 15–128-character password. `reset` revokes previous sessions. The app keeps rotating session credentials in its private Keychain and gives widgets only a limited read credential.

Widgets support the Home Screen and Lock Screen. They share a protected local snapshot and may fetch a small remote projection. iOS controls update timing. The free SideStore build does not enable Apple sign-in or APNs; these remain optional separately configured integrations.

Local pending operations are scoped to the immutable server user ID. Switching accounts does not send an old user's queue as a new user. An unknown request outcome remains recoverable. Do not delete the app while it contains unsent operations.
