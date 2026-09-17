from __future__ import annotations

from adapters.platforms.arcgis_owner import (
    ArcGISOwnerSearch,
    config_from_county,
    properties_from_owner_search,
    property_from_apn,
)
from adapters.registry import CountyConfig
from leads.models import PropertyRecord


class TarrantTaxAdapter:
    county_fips = "48439"

    def __init__(self, cfg: CountyConfig) -> None:
        self.cfg = cfg
        self.client = ArcGISOwnerSearch(
            config_from_county(
                cfg,
                defaults={
                    "mapserver_url": (
                        "https://mapit.tarrantcounty.com/arcgis/rest/services/"
                        "Dynamic/TADParcels/FeatureServer/0"
                    ),
                    "owner_field": "OWNER_NAME",
                    "apn_field": "ACCOUNT",
                    "address_fields": ("SITUS_ADDR", "CITY", "ZIPCODE"),
                    "value_field": "TOTAL_VALU",
                    "type_field": "PARCELTYPE",
                    "assessor_url_template": "https://www.tad.org/search-results?query={apn}",
                    "situs_state": "TX",
                },
            )
        )

    def search_by_owner(self, name: str) -> list[PropertyRecord]:
        return properties_from_owner_search(self.client, name)

    def get_by_apn(self, apn: str) -> PropertyRecord | None:
        return property_from_apn(self.client, apn)
