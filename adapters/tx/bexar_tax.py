from __future__ import annotations

from adapters.platforms.arcgis_owner import (
    ArcGISOwnerSearch,
    config_from_county,
    properties_from_owner_search,
    property_from_apn,
)
from adapters.registry import CountyConfig
from leads.models import PropertyRecord


class BexarTaxAdapter:
    county_fips = "48029"

    def __init__(self, cfg: CountyConfig) -> None:
        self.cfg = cfg
        self.client = ArcGISOwnerSearch(
            config_from_county(
                cfg,
                defaults={
                    "mapserver_url": "https://maps.bexar.org/arcgis/rest/services/Parcels/MapServer/0",
                    "owner_field": "Owner",
                    "apn_field": "AcctNumb",
                    "address_fields": ("Situs",),
                    "value_field": "TotVal",
                    "type_field": "State_cd",
                    "assessor_url_template": "https://esearch.bcad.org/Property/?searchText={apn}",
                    "situs_state": "TX",
                },
            )
        )

    def search_by_owner(self, name: str) -> list[PropertyRecord]:
        return properties_from_owner_search(self.client, name)

    def get_by_apn(self, apn: str) -> PropertyRecord | None:
        return property_from_apn(self.client, apn)
