from __future__ import annotations

from adapters.platforms.arcgis_owner import (
    ArcGISOwnerSearch,
    config_from_county,
    properties_from_owner_search,
    property_from_apn,
)
from adapters.registry import CountyConfig
from leads.models import PropertyRecord


class MaricopaTaxAdapter:
    county_fips = "04013"

    def __init__(self, cfg: CountyConfig) -> None:
        self.cfg = cfg
        self.client = ArcGISOwnerSearch(
            config_from_county(
                cfg,
                defaults={
                    "mapserver_url": "https://gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer/0",
                    "owner_field": "OWNER_NAME",
                    "apn_field": "APN",
                    "address_fields": ("PHYSICAL_ADDRESS",),
                    "value_field": "FCV_CUR",
                    "assessor_url_template": "https://mcassessor.maricopa.gov/mcs/?q={apn}",
                    "situs_state": "AZ",
                },
            )
        )

    def search_by_owner(self, name: str) -> list[PropertyRecord]:
        return properties_from_owner_search(self.client, name)

    def get_by_apn(self, apn: str) -> PropertyRecord | None:
        return property_from_apn(self.client, apn)
