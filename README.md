# zaptec charger custom component for home assistant

# Usage
Create a directory called custom_components in your ha config dir, copy/clone the zaptec folder inside this

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
