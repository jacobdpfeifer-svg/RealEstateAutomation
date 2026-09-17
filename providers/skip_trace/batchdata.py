from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from leads.models import ContactRecord
from leads.http import SourceChangedError, SourceSession


class BatchDataSkipTraceProvider:
    """BatchData Property Skip Trace v3 — requires BATCHDATA_API_KEY."""

    name = "batchdata"
    API_URL = "https://api.batchdata.com/api/v3/property/skip-trace"

    def __init__(self, api_key: str | None = None, session=None) -> None:
        self.api_key = (api_key if api_key is not None else os.environ.get("BATCHDATA_API_KEY", "")).strip()
        if not self.api_key:
            raise RuntimeError(
                "BATCHDATA_API_KEY not set. Copy config/secrets.env.example to "
                "config/secrets.env and add your key."
            )
        self.session = session or SourceSession()

    def _build_request(
        self,
        name: str,
        address: str,
        city: str,
        state: str,
        apn: str = "",
    ) -> dict[str, Any]:
        req: dict[str, Any] = {}
        if name:
            req["ownerName"] = name
        if apn:
            req["apn"] = apn
            req["state"] = state
        street = address.split(",", 1)[0].strip() if address else ""
        if street or city or state:
            prop_addr: dict[str, str] = {}
            if street:
                prop_addr["street"] = street
            if city:
                prop_addr["city"] = city
            if state:
                prop_addr["state"] = state
            # Zip may be trailing token in address like "Houston, TX 77002"
            # Require a state/ZIP suffix: a five-digit house number is not a ZIP.
            zip_match = re.search(r"\b[A-Za-z]{2}\s+(\d{5})(?:-\d{4})?\s*$", address)
            if zip_match:
                prop_addr["zip"] = zip_match.group(1)
            req["propertyAddress"] = prop_addr
        return {"requests": [req]}

    @staticmethod
    def _extract_persons(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize v3 response shapes into a flat persons list."""
        results = data.get("results")
        persons: list[dict[str, Any]] = []
        if isinstance(results, dict):
            raw = results.get("persons") or results.get("people") or []
            if isinstance(raw, list):
                persons.extend(p for p in raw if isinstance(p, dict))
            # Some payloads nest per-property objects under results.properties
            props = results.get("properties") or results.get("items") or []
            if isinstance(props, list):
                for prop in props:
                    if not isinstance(prop, dict):
                        continue
                    for p in prop.get("persons") or prop.get("people") or []:
                        if isinstance(p, dict):
                            persons.append(p)
        elif isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("persons"), list):
                    persons.extend(p for p in item["persons"] if isinstance(p, dict))
                elif "phoneNumbers" in item or "emails" in item or "name" in item:
                    persons.append(item)
        return persons

    @staticmethod
    def _best_phone(person: dict[str, Any]) -> str:
        phones = person.get("phoneNumbers") or person.get("phones") or []
        if isinstance(phones, list) and phones:
            for item in phones:
                if isinstance(item, dict):
                    item = item.get("number") or item.get("phoneNumber") or item.get("value")
                if isinstance(item, str) and item.strip():
                    return item.strip()
        for key in ("phone", "mobile", "mobilePhone", "landline"):
            if person.get(key):
                return str(person[key])
        return ""

    @staticmethod
    def _best_email(person: dict[str, Any]) -> str:
        emails = person.get("emails") or []
        if isinstance(emails, list) and emails:
            for item in emails:
                if isinstance(item, dict):
                    item = item.get("email") or item.get("address") or item.get("value")
                if isinstance(item, str) and item.strip():
                    return item.strip()
        value = person.get("email")
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _full_name(person: dict[str, Any], fallback: str) -> str:
        if person.get("full") or person.get("fullName"):
            return str(person.get("full") or person.get("fullName"))
        name = person.get("name")
        if isinstance(name, dict):
            parts = [
                str(name.get("first") or ""),
                str(name.get("middle") or ""),
                str(name.get("last") or ""),
            ]
            joined = " ".join(p for p in parts if p).strip()
            if joined:
                return joined
        if isinstance(name, str) and name.strip():
            return name.strip()
        return fallback

    @staticmethod
    def _confidence(person: dict[str, Any]) -> float:
        for key in ("rank", "score", "confidence", "matchScore"):
            if key in person and person[key] is not None:
                try:
                    val = float(person[key])
                except (TypeError, ValueError):
                    continue
                if val > 1.0:
                    return min(val / 100.0, 1.0)
                return max(0.0, min(val, 1.0))
        return 0.8

    def trace(
        self,
        name: str,
        address: str,
        city: str,
        state: str,
        apn: str = "",
    ) -> list[ContactRecord]:
        payload = self._build_request(name, address, city, state, apn=apn)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = self.session.post(self.API_URL, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not isinstance(data.get("results"), (dict, list)) or data.get("error"):
            raise SourceChangedError("BatchData error or missing results")
        status = data.get("status")
        if isinstance(status, dict) and str(status.get("code", "200")) != "200":
            raise SourceChangedError("BatchData reported an unsuccessful result")
        contacts: list[ContactRecord] = []
        now = datetime.utcnow()
        for person in self._extract_persons(data)[:3]:
            if not self._best_phone(person) and not self._best_email(person):
                continue
            contacts.append(
                ContactRecord(
                    id=None,
                    property_id=0,
                    name=self._full_name(person, name),
                    phone=self._best_phone(person),
                    email=self._best_email(person),
                    address=address,
                    provider=self.name,
                    confidence=self._confidence(person),
                    retrieved_at=now,
                )
            )
        return contacts
