# Autonomic e-Series integration for Home Assistant

NOTE: This integration **REQUIRES** [Home Assistant](https://www.home-assistant.io/) version `2023.6.0` or greater.

Provides support for controlling Autonomic Controls e-Series media systems (either paired with Amps or not) through Home Assistant. Requires Autonomic e-Series servers running firmware `6.1.20180215.0` or greater.

Currently Supports:

- Home Assistant UI configuration.
- [ZeroConf](https://www.home-assistant.io/integrations/zeroconf/) MMS discovery.
- One `media_player` object per Autonomic Zone.
- Entity names follow Zone numbers.
- Power, volume, mute and source selection.
- Media transports.
- Playing meta-data including Art.

>## IMPORTANT NOTE if upgrading from versions v2024.01.0 or lower
>This integration has been entirely re-written which results in a few **Breaking Changes**:
> - Before using this updated version you'll need to remove the configuration entry from your `configuration.yaml` file. There should be NO references to `autonomic` in your config file.
>
> - Default entity names in `MRAD_MODE` have been changed.  These used to be in the form `media_player.{{zoneName}}` such as `media_player.kitchen` but are now in the form `media_player.{{model}}_zone_{{zoneNumber}}` such as `media_player.mms5e_zone_01`.  You can re-name your entities once the new integration is installed to fix any issues you may encounter.
>
>----
>

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