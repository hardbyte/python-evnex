

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
CONNECTOR_MAP_E2 = {
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

COLOUR_MAP_E2 = {
    "SN": "Snow",
    "ST": "Stone",
    "SA": "Sand",
    "VO": "Volcanic",
}

# Lookup tables for X-series
POWER_MAP_X = {
    "7": "7 kW",
    "22": "22 kW",
}

PS_MAP_X = {
    "T": "External PS",
    "P": "Onboard PS",
}

CONNECTOR_MAP_X = {
    "1": "Type 1",
    "2": "Type 2",
}

CONFIG_MAP_X = {
    "S": "Socket",
    "T": "5m Tether",
}

COLOUR_MAP_X = {
    "W": "White",
    "G": "Grey",
}


def parse_model(model_id: str) -> EvnexModelInfo:
    """Parse either an E2-series or X-series model."""
    if model_id.startswith("E2"):  # Handle E2 Series
        try:
            prefix, spec = model_id.split("-", 1)
        except ValueError:
            return EvnexModelInfo("Unknown", "Unknown", "Unknown", "Unknown")

        name = NAME_MAP_E2.get(prefix, prefix)
        connector = CONNECTOR_MAP_E2.get(spec[0], spec[0])
        cable_length = CABLE_MAP_E2.get(spec[1], spec[1])
        colour = COLOUR_MAP_E2.get(spec[-2:], spec[-2:])

        return EvnexModelInfo(name, connector, cable_length, colour)

    elif model_id.startswith("X"):  # Handle X Series
        try:
            series, spec = model_id.split("-", 1)
        except ValueError:
            return EvnexModelInfo("Unknown", "Unknown", "Unknown", "Unknown")

        # Extract power rating (X7, X22, etc.)
        power_key = series[1:]  # e.g. "7" or "22"
        power = POWER_MAP_X.get(power_key, power_key)

        # First char = Power Sensor (T or P)
        ps = PS_MAP_X.get(spec[0], spec[0])

        # Second char = Connector type (1 or 2)
        connector = CONNECTOR_MAP_X.get(spec[1], spec[1])

        # Third char = Configuration (S or T)
        configuration = CONFIG_MAP_X.get(spec[2], spec[2])

        # Fourth char = Colour (must exist: W or G)
        colour = COLOUR_MAP_X.get(spec[3], spec[3])

        return EvnexModelInfo(
            name=f"X{power_key}",
            connector=connector,
            cable_length="N/A",  # Not applicable for X-series
            colour=colour,
            power=power,
            power_sensor=ps,
            configuration=configuration,
        )

    # Unknown series
    return EvnexModelInfo("Unknown", "Unknown", "Unknown", "Unknown")