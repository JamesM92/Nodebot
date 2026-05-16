"""
Second Meshtastic adapter — reads from [meshtastic1] in config.ini.

Add a [meshtastic1] section to config.ini to activate a second radio.
If the section is absent or port is blank, this adapter does nothing.
"""

from nodebot.transports.meshtastic_adapter import MeshtasticAdapter as _MeshtasticAdapter


class MeshtasticAdapter2(_MeshtasticAdapter):
    CONFIG_SECTION = "meshtastic1"
