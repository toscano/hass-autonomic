# Autonomic e-Series integration for Home Assistant

Provides support for controlling Autonomic Controls e-Series media systems (Media players paired with Amps) through Home Assistant. Requires Autonomic e-Series servers running firmware `6.1.20180215.0` or greater.

Currently Supports:

- One `media_player` object per enabled Autonomic Zone
- Entity names follow Zone names.
- Power, volume, mute and source selction
- Media transports
- Playing meta-data including Art.

## Manual Installation

Copy the `custom_components/autonomic` directory to your `custom_components` folder. Modify your `configuration.yaml` as below and restart Home Assistant.

Configuration Options:

* **host**: Host IP of the MMS eSeries Server (Required)

```yaml
# Example configuration.yaml
media_player:
   - platform: autonomic
     hosts:
        - host: 192.168.1.80
```

## Services

Adds new service `media_player.autonomic_all_off` which takes no parameters and powers off all zones.