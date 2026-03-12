"""Area extraction validation: diagram type and room_areas checks."""


def is_empty_value(value):
    """Check if a value is considered empty (None, "", "-", or similar)."""
    if value is None:
        return True
    if isinstance(value, str):
        v = value.strip()
        return v == "" or v == "-" or v == "－" or v == "—"
    return False


def get_diagram_type(data):
    """Get the diagram type from the parsed data."""
    if not isinstance(data, dict):
        return "unknown"
    diagram_type = str(data.get("diagram_type") or "").strip().lower()
    if diagram_type in ("detailed", "simple"):
        return diagram_type
    return "unknown"


def validate_room_areas(data):
    """Validate room_areas based on diagram type.

    For 'detailed' diagrams: Check if rooms have empty area_m2 when calculation is possible.
    For 'simple' diagrams: Only check if calculation/tatami exists but area_m2 is empty.

    Returns:
        tuple: (warnings, found_rooms, warning_rooms) where:
               - warnings is a list of warning messages
               - found_rooms is a list of all detected room names
               - warning_rooms is a list of room names with warnings
    """
    warnings = []
    found_rooms = []
    warning_rooms = []

    if not isinstance(data, dict):
        return warnings, found_rooms, warning_rooms

    diagram_type = get_diagram_type(data)
    room_rows = data.get("data", {}).get("room_areas", [])
    if not isinstance(room_rows, list):
        return warnings, found_rooms, warning_rooms

    for row in room_rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("room_name") or "").strip()
        if not name:
            continue

        found_rooms.append(name)
        area_m2 = row.get("area_m2")
        calculation = str(row.get("calculation") or "").strip()
        tatami = row.get("tatami")

        area_is_empty = is_empty_value(area_m2)
        tatami_is_empty = is_empty_value(tatami)

        if not area_is_empty:
            continue

        if calculation:
            warnings.append(
                f"部屋「{name}」に計算根拠（{calculation}）がありますが、area_m2が空欄です。"
            )
            warning_rooms.append(name)
        elif not tatami_is_empty:
            warnings.append(
                f"部屋「{name}」に帖数（{tatami}）がありますが、area_m2が空欄です。"
            )
            warning_rooms.append(name)
        elif diagram_type == "detailed":
            has_finish_info = any(
                not is_empty_value(row.get(key))
                for key in [
                    "floor",
                    "wall",
                    "ceiling",
                    "baseboard",
                    "床",
                    "壁",
                    "天井",
                    "巾木",
                ]
            )
            if has_finish_info:
                warnings.append(
                    f"部屋「{name}」は仕上表に記載されていますが、area_m2が空欄です。寸法を読み取って計算してください。"
                )
                warning_rooms.append(name)

    return warnings, found_rooms, warning_rooms
