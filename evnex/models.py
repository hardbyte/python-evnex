from dataclasses import dataclass


@dataclass
class EvnexModelInfo:
    name: str
    connector: str
    cable_length: str
    colour: str
    power: str = "N/A"  # Only for X-series
    power_sensor: str = "N/A"  # Only for X-series
    configuration: str = "N/A"  # Only for X-series


# Lookup tables
CONNECTOR_MAP = {
    "1": "Type 1",
    "2": "Type 2",
}

NAME_MAP_E2 = {
    "E2": "E2 Plus",
    "E2C": "E2 Core",
}

CABLE_MAP_E2 = {
    "5": "5 metres",
    "8": "8 metres",
}

COLOUR_MAP = {
    "SN": "Snow",
    "ST": "Stone",
    "SA": "Sand",
    "VO": "Volcanic",
    "W": "White",
    "G": "Grey",
}

# Lookup tables for X-series
POWER_MAP = {
    "7": "7 kW",
    "22": "22 kW",
}

PS_MAP = {
    "T": "External PS",
    "P": "Onboard PS",
}

CONFIG_MAP = {
    "S": "Socket",
    "T": "Tether",
}


def parse_model(model_id: str) -> EvnexModelInfo:
    """Parse either an E2-series or X-series model."""
    if model_id.startswith("E2"):  # Handle E2 Series E2C-25VO
        try:
            prefix, spec = model_id.split("-", 1)
        except ValueError:
            return EvnexModelInfo("Unknown", "Unknown", "Unknown", "Unknown")

        name = NAME_MAP_E2.get(prefix, prefix)
        connector = CONNECTOR_MAP.get(spec[0], spec[0])
        cable_length = CABLE_MAP_E2.get(spec[1], spec[1])
        colour = COLOUR_MAP.get(spec[-2:], spec[-2:])

        return EvnexModelInfo(name, connector, cable_length, colour)

    elif model_id.startswith("X"):  # Handle X Series X7-T2S-G
        try:
            series, spec, colour = model_id.split("-", 2)
        except ValueError:
            return EvnexModelInfo("Unknown", "Unknown", "Unknown", "Unknown")

        # Extract power rating (X7, X22, etc.)
        power_key = series[1:]  # e.g. "7" or "22"
        power = POWER_MAP.get(power_key, power_key)

        # First char = Power Sensor (T or P)
        ps = PS_MAP.get(spec[0], spec[0])

        # Second char = Connector type (1 or 2)
        connector = CONNECTOR_MAP.get(spec[1], spec[1])

        # Third char = Configuration (S or T)
        configuration = CONFIG_MAP.get(spec[2], spec[2])

        # Fourth char = Colour (must exist: W or G)
        colour = COLOUR_MAP.get(colour[0], colour[0])

        return EvnexModelInfo(
            name=series,
            connector=connector,
            cable_length="N/A",  # Not applicable for X-series
            colour=colour,
            power=power,
            power_sensor=ps,
            configuration=configuration,
        )
    elif model_id.startswith(
        "E7"
    ):  # discontinued chargers E7-T2S-WC E7-T2T-WC E7-T1T-WC
        try:
            series, spec, colour = model_id.split("-", 2)
        except ValueError:
            return EvnexModelInfo("Unknown", "Unknown", "Unknown", "Unknown")

        # Second char = Connector type (1 or 2)
        connector = CONNECTOR_MAP.get(spec[1], spec[1])

        # Third char = Configuration (S or T)
        configuration = CONFIG_MAP.get(spec[2], spec[2])

        # Fourth char = Colour (must exist: W or G ?)
        colour = COLOUR_MAP.get(colour[0], colour[0])

        return EvnexModelInfo(
            name=series,
            connector=connector,
            cable_length="N/A",  # Not applicable for X-series
            colour=colour,
            power="7",
            configuration=configuration,
        )

    # Unknown series
    return EvnexModelInfo("Unknown", "Unknown", "Unknown", "Unknown")
