# hass-autonomic
Home Assistant custom component for Autonomic e-Series whole home audio systems.

NOTE: This will only work with Autonomic. e-Series servers running firmware `6.1.20180215.0` or greater.


## Configuration:
```yaml
# Example configuration.yaml
media_player:
   - platform: autonomic
     hosts:
        - host: 192.168.1.80
```
### Configuration Options:

* **host**: Host IP of the MMS eSeries Server (Required)

## Services

Adds new service `media_player.autonomic_all_off` which takes no parameters and powers off all zones.