## zaptec charger custom component for home assistant

### Usage
Use hacs to install the package, add the config example for more usage see the `lovelace_example`

Config examples
```
zaptec:
  username: your_username
  password: your_password
  sensor:
    wanted_attributes:
      - -2
      - 201
      - 202
      - 270
      - 501
      - 507
      - 513
      - 553
      - 708
      - 710
      - 804
      - 809
      - 911
  switch:
```
