# Autonomic e-Series integration for Home Assistant

NOTE: This integration **REQUIRES** [Home Assistant](https://www.home-assistant.io/) version `2022.7.0` or greater.

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

* ```host```: Host IP of the MMS eSeries Server (Required)
* ```mode```: standalone OR mrad (Optional defaults to mrad)

## Autonomic System
```mode: mrad```

Use this mode when you have a complete Autonomic system including at least one Autonomic eSeries Media Player and at least one Autonomic eSeries Amplifier.
* `media_player` objects in Home Assistant created as part of an Autonomic System always have `power` and `volume` controls.

```yaml
# Example configuration.yaml for use with a complete Autonomic System
# (Player and one or more Amps aka mrad mode)
media_player:
   - platform: autonomic
     hosts:
        - host: 192.168.1.80
```
![mrad-example](./images/mrad.png )

## Standalone mode:
``` mode: standalone```

Use this mode when you only have an Autonomic eSeries Media Player
* `media_player` objects in Home Assistant created as part of a Standalone Media Player never have `power` controls and only have `volume` controls if ***not*** configured to have `fixed` volume. Consult your Autonomic configuration.

```yaml
# Example configuration.yaml for use with a stanalone Autonomic Media Player
media_player:
   - platform: autonomic
     hosts:
        - host: 192.168.1.80
          mode: standalone
```
![standalone-example](./images/standalone.png )

## Services

Adds new service `media_player.autonomic_all_off` which takes no parameters and powers off all players.