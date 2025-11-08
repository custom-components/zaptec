# Changelog

## 0.8.6 (draft)

This is a minor release that fixes a validation issue that caused the integration to stop working for users with at least one charger they only have the User role for.

In addition, it also fixes an issue where calls to the localSettings-API triggered an error on validating the response.

## 0.8.5

This is a minor release that fixes a bug in the setting of the Min/Max current of a charger.

This update is required to be able to run on HA Core 2025.11+.

## 0.8.4

This is a minor release that is required to be able to run on HA Core 2025.11+.

It contains minor fixes for pydantic validation (checks of the data from Zaptec portal) and increases the test coverage.

## 0.8.3

This is a release which primarily fixes an issue that caused the integration to stop working when being used by a user that doesn't have admin rights. See [#320](https://github.com/custom-components/zaptec/issues/320) for more information.

The release fixes compatibility with HA Core 2025.10+. Older versions of Zaptec will not work with newer HA versions and must be upgraded to this version. The minimum HA version compatible with this integration is 2025.7.

This release also introduces Norwegian translations, both bokmål and nynorsk language variants. There is a lot of ongoing efforts to improve the code quality, testability and structure of the integration. Most of this work is behind the scenes and should not affect the usage of it.

## 0.8.2

This release is a minor release, fixing a few critical issue regarding

Fix error in limit_current service call, which is used by blueprints
Adding documentation that the v0.8.x releases breaks the blueprints
If you're coming from v0.7.x, I recommend reading the [release notes for v0.8.1](#081) as well.

## 0.8.1

The v0.8.1 is a major release with a lot of changes. The integration has been completely redesigned and may impact your existing automations.

The main goal of this release has been to adopt the [Zaptec API Fair Use policy](https://docs.zaptec.com/docs/api-fair-use-policy#/). The mechanism for synchronizing HA entities with the Zaptec portal has been made more robust. It reduces the number of requests to Zaptec and fixes the issues of getting "429 Too many requests" -- especially on larger installations.

✨ Feature highlight
- New system for polling and updating information in HA (see [#202](https://github.com/custom-components/zaptec/issues/202))
  - Chargers are polled every 10 minutes in idle, while every minute when charging
  - General device information is polled every hour
  - Firmware version updates are polled once per day
- Implemented request rate limiter to avoid "429 Too many requests"
- Automatic polling from Zaptec after any button or value changes from HA to update the UI more quickly
- Change charger settings to use the official settings API
- Prevent sending pause/resume when not in the correct charging mode
- Support for reconfiguring the integration and integration reload now works
- Many internal changes and cleanups to classes and methods, including better logging
- Add support for 3 to 1-phase switching (PRO and Go 2 changers only) - NEW in v0.8.1

⚠️ Breaking changes
- `charger_mode` has changed values. E.g. from "Charging" to "connected_charging" due to using the lower-case native Zaptec values. Your automation might need an update.
- `permanent_cable_lock` has changed from _"lock"_ type to _"switch"_ type.
- There is no longer support for configuring Zaptec by YAML, only using the UI
- The user setting poll/scan interval has been removed, in favor of the improved polling system
- The _"Circuit"_ device and entity, notably _"Max Current"_, have been removed
- Service/action calls to named commands, such as _"resume_charging"_ are now deprecated in favor of the button entities. They will be removed in a later release.
ℹ️ Known issues
- Sending a _"deauthorize_and_stop"_ command will give an error. This is due to Zaptec sending back error code 500 (internal server error). However, the command seems to execute the task, despite the error.
- Setting custom poll intervals, like described [here](https://www.home-assistant.io/common-tasks/general/#defining-a-custom-polling-interval), will have unexpected effects. If the automatic polling is turned off, not all the data in the integration will update properly.

## 0.7.4

This release is a minor release fixing an issue with blocking calls within the Zaptec API calls.

## 0.7.3

This release is a minor fix. The two most important fixes are the blocking call warning from HA and a negative energy issue. The blocking call is is due to a 3rd party library which has been updated. The negative charge energy issue caused the energy view in HA to show up as a negative number.

## 0.7.2
This release is an improvement release of the Zaptec integration. It contains a lot of stability fixes, especially for the amount of logging and how API errors are handled. It adds a couple of new entities that have been requested such as cable locking and status light brightness setting. As the first non-English translation, Dutch translation has been added (shout-out to @c0mplex1 ). See below for the full list of features and fixes.

## 0.7.1

⚠️ IMPORTANT ⚠️
**If running version < 0.7.0: This release contains breaking changes!** Entities and device names have changed significantly. You will have to update your automations after installation. It can prove easier to uninstall the zaptec integration before installing 0.7.1 to ensure all entity names gets properly updated.

## 0.7.0

‼️ BREAKING CHANGE

⚠️ **NOTE:** This release contains breaking changes. Entities and device names have changed significantly. You will have to update your automations after installation.