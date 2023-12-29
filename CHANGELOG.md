# Changelog

## 0.7.1a5

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
