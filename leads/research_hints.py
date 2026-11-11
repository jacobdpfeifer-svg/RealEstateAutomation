"""No-network people-search URL builder for manual OSINT.

Deep links are best-effort. Do not fetch these URLs from the pipeline.
Results enter through `leads review paste` after a human corroborates them.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

# Same rule as BatchData: ZIP is the trailing token after a two-letter state,
# never a five-digit house number.
_SITUS_ZIP_RE = re.compile(r"\b[A-Za-z]{2}\s+(\d{5})(?:-\d{4})?\s*$")
_SITUS_STATE_RE = re.compile(r"\b([A-Za-z]{2})(?:\s+\d{5}(?:-\d{4})?)?\s*$")
_ENTITY_RE = re.compile(
    r"\b("
    r"LLC|L\.L\.C\.?|"
    r"LLP|L\.L\.P\.?|"
    r"INC(?:ORPORATED)?|"
    r"CORP(?:ORATION)?|"
    r"LTD|LIMITED|"
    r"LP|L\.P\.?|"
    r"TRUST|ESTATE|"
    r"HOA|"
    r"NA|N\.A\.?"
    r")\b",
    re.I,
)

TPS_RESULTS = "https://www.truepeoplesearch.com/results"
FPS_BASE = "https://www.fastpeoplesearch.com"
THATSTHEM_BASE = "https://thatsthem.com"
WHITEPAGES_BASE = "https://www.whitepages.com/name"
GOOGLE_SEARCH = "https://www.google.com/search"
OPENCORPORATES = "https://opencorporates.com/companies"
TX_COMPTROLLER = "https://mycpa.cpa.state.tx.us/coa/"
TX_SOSDIRECT = "https://www.sos.state.tx.us/corp/sosda/index.shtml"


def looks_like_entity(name: str) -> bool:
    """True when the owner string looks like an LLC, trust, HOA, or similar."""
    return bool(_ENTITY_RE.search((name or "").strip()))


def situs_zip(address: str) -> str:
    """Return a ZIP only when the situs ends with `ST #####` (optional +4)."""
    match = _SITUS_ZIP_RE.search(address or "")
    return match.group(1) if match else ""


def parse_situs(
    situs: str,
    *,
    city: str = "",
    state: str = "",
    zip_code: str = "",
) -> tuple[str, str, str, str]:
    """Split situs into street, city, state, ZIP. ZIP is never a house number."""
    situs = (situs or "").strip()
    street = situs.split(",", 1)[0].strip() if situs else ""
    parsed_city = (city or "").strip()
    parsed_state = (state or "").strip().upper()
    suffix_zip = situs_zip(situs)
    parsed_zip = suffix_zip
    explicit = (zip_code or "").strip()
    if not parsed_zip and re.fullmatch(r"\d{5}(?:-\d{4})?", explicit):
        house = re.match(r"(\d+)", street)
        explicit_zip = explicit.split("-", 1)[0]
        if not (house and explicit_zip == house.group(1)):
            parsed_zip = explicit_zip

    if not parsed_city:
        parts = [p.strip() for p in situs.rsplit(",", 2) if p.strip()]
        if len(parts) >= 2:
            parsed_city = parts[-2]

    if not parsed_state:
        match = _SITUS_STATE_RE.search(situs)
        if match:
            parsed_state = match.group(1).upper()

    return street, parsed_city, parsed_state, parsed_zip


def build_research_hints(
    *,
    name: str,
    situs: str,
    city: str = "",
    state: str = "",
    zip_code: str = "",
) -> dict[str, str]:
    """Return ordered label → URL for the Ship 1 people-search / entity set."""
    name = (name or "").strip()
    street, city, state, zip_code = parse_situs(
        situs, city=city, state=state, zip_code=zip_code
    )
    urls: dict[str, str] = {}

    tps_place = _city_state(city, state)
    tps_place_zip = _city_state(city, state, zip_code)
    if name:
        params = {"name": name}
        if tps_place:
            params["citystatezip"] = tps_place
        urls["TruePeopleSearch name"] = f"{TPS_RESULTS}?{urlencode(params)}"
    if street:
        params = {"streetaddress": street}
        if tps_place_zip:
            params["citystatezip"] = tps_place_zip
        urls["TruePeopleSearch address"] = f"{TPS_RESULTS}?{urlencode(params)}"

    fps_name = _path_slug(name, lower=True)
    fps_street = _path_slug(street, lower=True)
    fps_place = _path_slug(f"{city} {state}", lower=True)
    fps_place_zip = _path_slug(f"{city} {state} {zip_code}".strip(), lower=True)
    if fps_name:
        urls["FastPeopleSearch name"] = _fps_url("name", fps_name, fps_place)
    if fps_street:
        urls["FastPeopleSearch address"] = _fps_url("address", fps_street, fps_place_zip)

    tt_name = _path_slug(name)
    tt_street = _path_slug(street)
    tt_place = _path_slug(f"{city} {state}")
    tt_place_zip = _path_slug(f"{city} {state} {zip_code}".strip())
    if tt_name:
        urls["That's Them name"] = _join_url(THATSTHEM_BASE, "name", tt_name, tt_place)
    if tt_street:
        urls["That's Them address"] = _join_url(
            THATSTHEM_BASE, "address", tt_street, tt_place_zip
        )

    wp_name = _path_slug(name)
    wp_place = _path_slug(f"{city} {state}")
    if wp_name:
        urls["Whitepages"] = _join_url(WHITEPAGES_BASE, wp_name, wp_place)

    google_q = _google_query(name, street, city, state)
    if google_q:
        urls["Google"] = f"{GOOGLE_SEARCH}?{urlencode({'q': google_q})}"

    if looks_like_entity(name):
        oc_state = (state or "TX").lower()
        urls["OpenCorporates"] = (
            f"{OPENCORPORATES}?{urlencode({'q': name, 'jurisdiction_code': f'us_{oc_state}'})}"
        )
        if (state or "TX") == "TX":
            urls["Texas Comptroller"] = TX_COMPTROLLER
            urls["SOSDirect"] = TX_SOSDIRECT

    return urls


def _city_state(city: str, state: str, zip_code: str = "") -> str:
    place = ", ".join(part for part in (city.strip(), state.strip()) if part)
    if zip_code:
        return f"{place} {zip_code}".strip()
    return place


def _path_slug(value: str, *, lower: bool = False) -> str:
    text = re.sub(r"[^\w\s-]+", "", (value or "").strip())
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text.lower() if lower else text


def _fps_url(kind: str, slug: str, place: str) -> str:
    tail = f"{slug}_{place}" if place else slug
    return f"{FPS_BASE}/{kind}/{tail}"


def _join_url(base: str, *parts: str) -> str:
    cleaned = [base.rstrip("/")]
    cleaned.extend(part.strip("/") for part in parts if part)
    return "/".join(cleaned)


def _google_query(name: str, street: str, city: str, state: str) -> str:
    chunks: list[str] = []
    if name:
        chunks.append(f'"{name}"')
    if street:
        chunks.append(f'"{street}"')
    loc = " ".join(part for part in (city, state) if part)
    if loc:
        chunks.append(loc)
    return " ".join(chunks)
