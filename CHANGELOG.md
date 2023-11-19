# Changelog

## 0.7.1a1

* Fixed issue with failures during diagnostics that prevents useful downloads,
  custom-components/zaptec#63

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
