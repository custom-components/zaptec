# Changelog

## 0.7.3

* Fix Azure service bus error causing HA to complain about blocking call, #149
* Fix TOTAL_INCREASING that caused negative power consumption, #145
* Add Swedish language tranlastion, #141
* Add Polish language tranlation, #140

## 0.7.2

* Add Dutch language translation, #133
* Prevent leaking username and passwords to logs, #131
* Added documentation of API calls which are not official, #126
* Add support for setting status light brightness, #112
* Add retry on 500 server errors from Zaptec cloud, #90
* Added new charger lock "Permanent cable lock" and removed the binary
  sensor of the same name #102
* Remove large and noisy charger states containg test or production data.
  Helps to reduce the entity attribute <16kb. #88
* Ignore updates with chargerid 000000...000, #84
* Add "online" binary entity in charger, #114
* Reduce the amount of logging from the API, #90
* Delete old stale devices, #89
* Changed "total_charger_power_session" to TOTAL, #87
* Added new installation sensors "Installation type", "Network type"
* Added new charger sensor "Device type"
* Added new service call "send_command" for sending any commands to the
  charger
* Fix error handling of service calls
* API: Moved ZConst() class into zconst.py and let this class handle all
  related to constants and types. Rewrite how this class is initalized from
  Account.build(). Move Account._obs_ids, ._set_ids and ._cmd_ids into
  attributes in ZConst().
* API: Add interpreted type to Installation attributes "current_user_role",
  "installation_type", "network_type"
* API: Add interpreted type to Charger attributes "authentication_type",
  "current_user_role", "device_type", "network_type"
* API: Add support for numeric charger commands
* API: Move Account._state_to_attrs() into ZaptecBase.state_to_attrs()

## 0.7.1

* Fixed issue with data leaking over with multiple chargers
* Fixed issues and improved diagnostics download
* Added new charger sensor "Humidity", "Temperature (internal)",
  "Allocated charge current", "Authentication Type"
* Added new installer sensor "Max Current"
* Added statistics attribute for each entity that measures something
* Fixed API request cleanups
* Cleaned debug output
* Cleanup of translations, more consistent usage of "Authorization"
* Change "Authorization Requred" switch to binary sensor. It is better to
  use Zaptec Portal for these types of adjustments
* Increase poll update delay slightly to ensure Zaptec Cloud data is updated
  by the time the next poll is run
* Change Circuit to use "active" (was "is_active")
* API: Added global var ZCONST for a dict-like object for accessing constants.
  This cleans up ATTR_TYPES logic considerably.

## 0.7.0

Major refactor of the component
* Objective: Update the component to the "HA way" using devices and entities
* Zaptec devices (installation, circuit and charger) use name set in Zaptec portal
* Added entities for each device. No need for using attrs and templates.
* Added support for selecting which chargers to add to zaptec
* Added support for adding an optional prefix to the device names
* Fixed adjustable charging currents, all or individual three phase.
* Added support for authorization and deauthorization of charging using the
  Zaptec internal *native* authentication
* Zaptec produces energy sensors that can be used with HA energy dashboard
* Refactor services in order to make them easier to use with the service call UIs
* Hardnened the cloud connection robustness (better timeout, better data and
  error handling)
* Use data update coordinator for polling entities
* Added "Download diagnostics"
* Bugfixes and documentation update
