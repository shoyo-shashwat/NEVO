# app/services/map_view.py
#
# Shared per-country map view (center + zoom) for the Demand Intelligence
# Map. Lives in services/ (not citizen/ or government/) because both
# blueprints need it and neither is allowed to import from the other
# (Master Prompt hard rule — cross-blueprint code goes through app/models/
# or app/services/, never directly between citizen/ and government/).
#
# Round 4 UI fix: the map used to be one unfiltered whole-world view
# (`L.map('map').setView([20, 0], 2)`) regardless of who was looking at it.
# Citizens and government users should only ever see their own country.

COUNTRY_MAP_VIEW = {
    "IN": {"center": [22.9734, 78.6569], "zoom": 5},
    "BR": {"center": [-14.2350, -51.9253], "zoom": 4},
    "RU": {"center": [61.5240, 105.3188], "zoom": 3},
}

DEFAULT_COUNTRY_CODE = "IN"


def get_map_view(country_code: str) -> dict:
    return COUNTRY_MAP_VIEW.get(country_code, COUNTRY_MAP_VIEW[DEFAULT_COUNTRY_CODE])
