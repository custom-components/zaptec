## Zaptec EV charger component for Home Assistant

[![hacs][hacsbadge]][hacs]
[![GitHub Release][releases-shield]][releases]
[![License][license-shield]][license]
![Downloads][downloads-shield]

[![Project Maintenance][hellowlol-maintenance-shield]][hellowlol-profile]
[![BuyMeCoffee][buymecoffeebadge]][hellowlol-buymecoffee]
[![Project Maintenance][sveinse-maintenance-shield]][sveinse-profile]
[![BuyMeCoffee][buymecoffeebadge]][sveinse-buymecoffee]


# Features

* Integration for Home Assistant for Zaptec Chargers through the Zaptec
  portal/cloud API
* Provides start & stop of charging the EV
* Supports basic authentication (*native* authentication)
* Sensors for status, current, energy
* Adjustable charging currents, all or individual three phase

Confirmed to work with Zaptec products

* Zaptec Go
* Zaptec Home
* Zaptec PRO

To use this component, a user with access to
[Zaptec Portal](https://portal.zaptec.com/) is required.


# ⭐ Version 0.8.*

> [!IMPORTANT]
> The release has not undergone extensive testing, so your feedback is valuable.
> If you encounter any problems, please report them at
> https://github.com/custom-components/zaptec/issues
> We are grateful for all contributions, ranging from documentation, examples
> of how to use, testing, to code development.

The  [v0.8.0](https://github.com/custom-components/zaptec/releases/tag/v0.8.0)
is a major release with a lot of changes. The integration has been completely
redesigned and may impact your existing automations.

The main goal of this release has been to adopt the
[Zaptec API Fair Use policy](https://docs.zaptec.com/docs/api-fair-use-policy#/).
The mechanism for synchronizing HA entities with the Zaptec portal has been made
more robust. It reduces the number of requests to Zaptec and fixes the issues
of getting "429 Too many requests" -- especially on larger installations.

## ✨ Feature highlight

* New system for polling and updating information in HA (see #202)
  * Chargers are polled every 10 minutes in idle, while every minute when charging
  * General device information is polled every hour
  * Firmware version updates are polled once per day
* Implemented request rate limiter to avoid "429 Too many requests"
* Automatic polling from Zaptec after any button or value changes from HA
  to update the UI more quickly
* Change charger settings to use the official settings API
* Prevent sending pause/resume when not in the correct charging mode
* Support for reconfiguring the integration and integration reload now works
* Many internal changes and cleanups to classes and methods, including better
  logging
* Add support for 3 to 1-phase switching (PRO and Go 2 changers only)

The full list of changes is available in [CHANGELOG.md](CHANGELOG.md#080)

## ⚠️ Breaking changes

* `charger_mode` has changed values. E.g. from _"Charging"_ to
  _"connected_charging"_ due to using the lower-case native Zaptec values. Your
  automation might need an update. This also affects the
  [Load Balancing blueprint](#load-balancing-your-charger).
* `permanent_cable_lock` has changed from _"lock"_ type to _"switch"_ type.
* There is no longer support for configuring Zaptec by YAML, only using the UI
* The user setting poll/scan interval has been removed, in favor of the
  improved polling system
* The _"Circuit"_ device and entity, notably _"Max Current"_, have been removed
* Service/action calls to named commands, such as _"resume_charging"_ are now
  deprecated in favor of the button entities. They will be removed in a later
  release.

## ℹ️ Known issues

* Sending a _"deauthorize_and_stop"_ command will give an error. This is due to
  Zaptec sending back error code `500` (internal server error). However, the
  command seems to execute the task, despite the error.
* Setting custom poll intervals, like described
  [here](https://www.home-assistant.io/common-tasks/general/#defining-a-custom-polling-interval),
  will have unexpected effects. If the automatic polling is turned off, not all
  the data in the integration will update properly.


# Installation and setup

This integration is available in HACS (Home Assistant Community Store).

Just search for Zaptec in the HACS list or click the badge below:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=custom-components&repository=zaptec)

## Setting up Zaptec

After adding the Zaptec integration, it must be added to HA.

- Click Settings (left-hand side menu at the bottom)
- Click Devices & Services
- Select Integrations pane
- Press "+ Add integration" in the bottom right corner
- In the search dialog enter "Zaptec" and click it

Next the **Zaptec setup** dialog is presented. Fill in the form:

- **Username**: Your Zaptec portal username
- **Password**: Your Zaptec portal password
- **Optional prefix** specifies if a prefix on all entities is wanted. Leave
  this blank unless there is a specific need for it. It's generally better to
  rename entities in HA than using this feature.
- **Manually select chargers** will allow you to select which chargers
  should be included in HA. This is useful for large installations that have
  many chargers. When selected a new dialog asking for which chargers to add
  will be presented.

## Manual installation

This describes how Zaptec can be added manually if HACS cannot be used

- Clone or download the [Zaptec repository](https://github.com/custom-components/zaptec/)
  to the server where Home Assistant is installed.
- Copy the folder `custom_components/zaptec` from the downloaded repo into folder
  `config/custom_components/zaptec` in Home Assistant.
- Restart HA. It should now be available for being added to HA.

Continue as described above in [setting up Zaptec](#setting-up-zaptec)


# Usage

> [!NOTE]
> This integration uses the [official Web API](https://api.zaptec.com/help/index.html)
> provided by Zaptec. However, this integration also uses a few functions that
> are not officially supported by the API. Use at your own risk and they might
> break at any time.
>
>  * Authorize charging
>  * Setting cable lock
>  * Setting status light brightness


## Zaptec device concept

The Zaptec cloud API uses three levels of abstraction in their EVCP setup. Only
the top and bottom levels are represented as devices in HA

* **Installation** - This is the top-level entity and represents the entire
  site. This is where the current limit for the entire installation is set.

* **Circuit** - An installation can have one or more (electrical) circuits. One
  circuit has one common circuit breaker. This level is not used in HA.

* **Charger** - This is the actual EV charge point connected to a circuit. Each
  circuit might have more than one charger. This is where the start & stop
  interaction is done and information about the charging and sessions.


## Start & stop charging

Starting and stopping charging can be done by several methods. If the charger
is configured to not require authentication, connecting the charger to the
EV will by default start charging.

To start the charging from HA, this can be done in several ways:

- Press the _"Resume charging"_ button, or
- Toggle the _"Charging"_ switch, or
- Send `zaptec.restart_charger` service call (deprecated)

Similarly, pausing the charging can be done by:

- Pressing the _"Stop charging"_ button, or
- Turn off the _"Charging"_ switch, or
- Send `zaptec.stop_pause_charging` service call (deprecated)

> [!TIP]
> Zaptec will unlock the cable when charging is paused unless it is permanently
> locked.


## Prevent charging auto start

Zaptec will by default start charging as soon as everything is ready
under the following conditions; (1) Cable connected to car, (2) Car is ready to
charge, (3) authentication is given (optional).

If auto start is not wanted, e.g. for delayed start or energy control, one
of the following will prevent auto start:

* Delay authorization of the charger
* Set the available charge current to `0 A`. There are two ways to do it
   * _"Available current"_ in the installation object
   * _"Charger max current"_ in the charger object

> [!TIP]
> Using _"Available current"_ will affect __all__ chargers if there are more
> than one charger.


## Setting charging current

The _"Available current"_ number entity in the installation device will set
the maximum current the EV can use. This slider will set all 3 phases at
the same time.

> [!NOTE]
> This entity is adjusting the available current for the entire installation.
> If the installation has several chargers installed, changing this value will
> affect all.

> [!IMPORTANT]
> Many EVs don't like getting too frequent changes to the available charge
> current. Zaptec recommends not changing the values more often than every
> 15 minutes.


### 3 phase current adjustment

The service call `limit_current` can be used with the arguments
`available_current_phase1`, `available_current_phase2` and
`available_current_phase3` to set the available current on individual phases.


### 3 phase to 1 phase switch

For more recent chargers (currently Zaptec PRO and Go 2), switching between
3-phase and 1-phase charging is supported. This is done by controlling the
entity "3 to 1-phase switch current" found in the Installation device.

It can be set to three modes:
1. Forced 3-phase charging: Set value to 0
2. Forced 1-phase charging: Set value to 32
3. Automatic switchover from 3-phase to 1-phase: Value 1–31 sets the
   switchover current threshold. If the current is lower than this threshold,
   the chargers will switch to 1-phase charging. Above it will use 3-phase
   charging.

> [!NOTE]
> Adjusting this value for installations with chargers that don't support
> 3-to-1 phase switching won't have any effect.

See documentation from Zaptec:
https://docs.zaptec.com/docs/3-to-1-phase-switching-with-zaptec-go-2#/


## Require charging authorization

Many users want to set up their charger to require authorization before giving
power to charge any EV. This integration does not offer any options to configure
authorization. Please use the official
[Zaptec portal](https://portal.zaptec.com/) or app.

If the charger has been set up with authorization required, the car will go
into _Waiting_ mode when the cable is inserted. Authentication must be
presented before being able to charge. This can be RFID tags, the Zaptec app
and more.

If the installation is configured for _native authentication_ it is possible
to authorize charging from Home Assistant using the _"Authorize charging"_
button. It stays authorized until either the cable is removed or the button
_"Deauthorize charging"_ is pressed.

> [!TIP]
> Zaptec unlocks the cable when charging is paused unless it is permanently
> locked.

> [!NOTE]
> Charge authorization from HA only works when the installation is set with
> *Authentication Type* set to **Native authentication** in Zaptec portal.


## Templates

The special diagnostics entities named _"x Installation"_ and _"x Charger"_
contain all attributes from the Zaptec API for each of these devices. This
corresponds to the old `zaptec_installation_*` and `zaptec_charger_*` objects.
These attributes can be used with template sensors to retrieve additional or
missing information.

Example: Add the following to your `configuration.yaml`

```yaml
template:
  - sensor:
     - name: Charger Humidity
       unique_id: charger_humidity
       unit_of_measurement: '%Humidity'
       state: >
        {{ state_attr('binary_sensor.X_charger', 'humidity') | round(0) }}
       # Replace "X_charger" with actual entity name
```

The list of attributes can be found by looking at the attributes for the
entities. Note that the names cannot contain spaces. Replace capital letters
with lower case and spaces with underscore (_). E.g. The attribute
_"Charger max current"_ is `charger_max_current` in the template.


## Diagnostics

The integration supports downloading of diagnostics data. This can be reached
by **Settings -> Devices & Services -> <one of your zaptec devices>** and then
press **Download diagnostics**. The file downloaded is anonymized and should
not contain any personal information.

> [!IMPORTANT]
> Please review the diagnostics file and double check that it doesn't contain
> any personal information before sharing.


## Debugging

Debug log for Zaptec can be enabled by going to **Settings -> Devices & Services
-> Integration (pane) -> Zaptec EV Charger -> Enable debug logging**.

The most interesting stuff happens when the integration is started, so in the
same view press `...` under *Integration entries* and press "reload". When the
button *Enable debug logging* is turned off the browser will download the
debug logs.

Alternatively, debug can be enabled by manually adding the following to
`configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.zaptec: debug
```

> [!NOTE]
> The Zaptec integration logs massive amounts in
> debug. This is nice for finding errors, but it will generate a large amount of
> data if left enabled for long. Do not use in production setups.

#### Redaction filter

The integration has a *redaction filter* which replaces sensitive information by
redaction strings. E.g. A full Charger UID `184abd73-19c6-bc88-9844-ab3874bf82ed`
becomes `<--Charger[bf82ed]>`. This makes the logs safer to share.

Each redaction is given a unique name which can be used to trace back the actual
value. At startup the list of redactions will be printed in the logs. This
list must **NOT** be shared.


#### Sharing debug logs

> [!WARNING]
> The debug logs may contain identifiable information about your Zaptec setup
> such as name or serial numbers. The redaction filter is designed to remove
> most sensitive information, but it is not perfect. Please review any
> logs before they are shared or made public.
>
> The Zaptec entity names might contain sensitive information. These are not
> redacted from the logs. E.g. by default name is using the unit serial number,
> `ZAP123456`. You need to evaluate this information before sharing the logs.


## Using the integration

### Load balancing your charger

By using the [Zaptec Load Balancing](https://github.com/svenakela/ha/tree/main/zaptec)
blueprint you'll get automatic load balancing for your charger (i.e. the charger
limit is updated constantly to avoid fuse overload).

The automation created with the blueprint manages current limiting. If charging
is enabled and possible without tripping fuses it will manage the limit over the
charging session.

How to set up the automation, how the logic works and what all settings mean is
documented in the
[blueprint readme](https://github.com/svenakela/ha/blob/main/zaptec/README.md).


## Development

The document [DEVELOPMENT.md](DEVELOPMENT.md) contains information about
how to develop the Zaptec integration. It contains tips and tricks and how to
set up the Dev Container.


# Changes

## Changes from 0.7.x to 0.8.x

The Circuit device type has been removed since it was not really used in HA. The
information in the old Circuit device is now included with the full data of the
charger in the attributes of the `<name> Charger` diagnostics sensor. If you rely on
this information, it can be retrieved using [Templates](#templates)

The permanent cable lock has been changed to a Switch entity (from
`lock.*_permanent_cable_lock` to `switch.*_permanent_cable_lock`). This is to conform
to the HA convention that the Lock entity type should only be used for physical locks
that are used to enter the house.

The Charger mode has been changed to use the native zaptec values in lower case. The
display values are still the same, but automations using the state will need to be
updated. The English mapping of the changed values is
- connected_requesting: Waiting
- connected_charging:   Charging
- connected_finished:   Charge done
- disconnected:         Disconnected
- unknown:              Unknown

The changes to the Charger mode will also cause the
[Load Balancing blueprint](#load-balancing-your-charger) compatible with 0.7.x
to stop working. There is an [open PR](https://github.com/svenakela/ha/pull/10)
for a 0.8.x-compatible version. If you use this blueprint, or a variant of it,
you will need to update your blueprint/automation accordingly.


## Changes from older versions <0.7.0

> [!CAUTION]
> This release will BREAK your current automations

The Zaptec integration has been completely refactored. The way to interact
with your Zaptec charger from Home Assistant has been changed. The Zaptec data
is now represented as proper entities (like sensors, numbers, buttons, etc).
This makes logging and interactions much simpler and it needs no additional
templates.

The integration is set up as one device for each of the detected Zaptec
devices. Most users will have two devices: An installation device and a
charger, and each provides different functionality.

The previous zaptec entities were named `zaptec_charger_<uuid>`,
`zaptec_installation_<uuid>` and `zaptec_circuit_<uuid>`. The full data were
available as attributes in these objects, and they could be retrieved with
the aid of manual templates. The same objects exist, but under the names
`<name> Installer` and `<name> Charger` (see [Changes from 0.7 to 0.8](#changes-from-07-to-08)
for the new treatment of the Circuit level)


[hellowlol-buymecoffee]: https://www.buymeacoffee.com/hellowlol1
[sveinse-buymecoffee]: https://www.buymeacoffee.com/sveinse
[buymecoffeebadge]: https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png
[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Default-blue.svg
[license]: https://github.com/custom-components/zaptec/blob/master/LICENSE
[license-shield]: https://img.shields.io/github/license/custom-components/zaptec.svg
[hellowlol-maintenance-shield]: https://img.shields.io/badge/maintainer-Hellowlol-blue.svg
[sveinse-maintenance-shield]: https://img.shields.io/badge/maintainer-sveinse-blue.svg
[releases-shield]: https://img.shields.io/github/release/custom-components/zaptec.svg
[releases]: https://github.com/custom-components/zaptec/releases
[downloads-shield]: https://img.shields.io/github/downloads/custom-components/zaptec/total.svg
[hellowlol-profile]: https://github.com/hellowlol
[sveinse-profile]: https://github.com/sveinse
