"""Public manufacturing units; source/database values retain their original units."""


def public_production_capacity(value: float | None, unit: str | None) -> tuple[float | None, str | None]:
    """Convert the value/unit pair together; repeated export must not rescale tonnes."""
    if unit == "万吨/年":
        return (value * 10_000 if value is not None else None), "t/year"
    return value, {"GW/年": "GW/year", "GWh/年": "GWh/year"}.get(unit, unit)
