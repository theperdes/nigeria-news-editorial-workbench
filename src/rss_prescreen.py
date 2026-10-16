from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path
from typing import Any

from pipeline_paths import add_run_date_arg, resolve_pipeline_input_path, save_json_with_latest
from source_priority import source_priority_score, with_source_priority


ROOT_DIR = Path(__file__).resolve().parents[1]


NEGATIVE_TERMS = [
    "football",
    "sports",
    "premier league",
    "celebrity",
    "entertainment",
    "gossip",
    "music",
    "movie",
    "bbnaija",
    "drug",
    "cocaine",
    "heroin",
    "opinion",
    "claim verification",
    "fact check",
]

ROUTINE_TERMS = [
    "congratulates",
    "felicitates",
    "condoles",
    "birthday",
    "endorsement",
    "endorses",
    "nomination form",
    "rally",
    "party chieftain",
    "lauds",
    "hails",
    "tasks",
    "urges",
]

CSR_TERMS = [
    "csr",
    "donates",
    "donation",
    "scholarship",
    "empowers",
    "training",
    "workshop",
    "award",
    "recognition",
]

PROJECT_DOMAINS = [
    "infrastructure",
    "project",
    "construction",
    "road",
    "highway",
    "bridge",
    "railway",
    "rail",
    "port",
    "airport",
    "transport",
    "transport system",
    "transport systems",
    "intelligent transport",
    "intelligent transportation",
    "smart transport",
    "smart transportation",
    "water",
    "housing",
    "hospital",
    "health infrastructure",
    "medical equipment",
    "waste",
    "recycling",
    "wastewater",
    "digital infrastructure",
    "data centre",
    "data center",
    "industrial park",
    "industrial cluster",
    "special economic zone",
    "factory",
    "plant",
    "agro processing",
    "agriculture",
    "livestock",
    "irrigation",
    "cold chain",
    "grain storage",
    "power",
    "electricity",
    "transmission",
    "national grid",
    "off-grid",
    "off grid",
    "electrification",
    "rural electrification",
    "solar",
    "mini grid",
    "mini-grid",
    "cng",
    "oil",
    "gas",
    "pipeline",
    "refinery",
    "lng",
]

PROJECT_ACTIONS = [
    "partner",
    "partners",
    "partnered",
    "partnership",
    "cooperation",
    "collaboration",
    "collaborate",
    "memorandum of understanding",
    "mou",
    "signs",
    "signed",
    "approves",
    "approved",
    "approval",
    "awards",
    "awarded",
    "contract",
    "flags off",
    "flag off",
    "launches",
    "launched",
    "commissions",
    "commissioned",
    "powers",
    "powered",
    "powering",
    "energise",
    "energised",
    "energize",
    "energized",
    "starts",
    "commences",
    "begins",
    "resumes",
    "rehabilitation",
    "reconstruction",
    "upgrade",
    "expansion",
    "completion",
    "completed",
    "nearing completion",
    "tender",
    "bid",
    "bidding",
    "procurement",
    "request for bids",
    "eoi",
    "ppp",
    "concession",
    "epc",
    "technology exchange",
    "knowledge exchange",
    "capacity building",
    "contractor",
    "suspends",
    "suspended",
    "delayed",
    "stalled",
]

FINANCE_TERMS = [
    "loan",
    "financing",
    "funding",
    "facility",
    "grant",
    "budget",
    "allocation",
    "debt",
    "capital expenditure",
    "tax",
    "tariff",
    "subsidy",
    "world bank",
    "afdb",
    "african development bank",
    "ifc",
    "afreximbank",
    "china exim",
    "jica",
    "eib",
    "ukeF",
]

INSTITUTIONS = [
    "tinubu",
    "president",
    "fec",
    "federal executive council",
    "national assembly",
    "senate",
    "minister",
    "governor",
    "nnpc",
    "npa",
    "faan",
    "tcn",
    "nrc",
    "rea",
    "nerc",
    "nuprc",
    "nmdpra",
    "ncdmb",
    "icrc",
    "bpp",
    "cbn",
]

# Portfolio edition: approved public examples can be added in config/entities.yaml.
COMPANIES: list[str] = []

POLITICS_TERMS = [
    "2027",
    "election",
    "elections",
    "presidential election",
    "presidential candidate",
    "candidate",
    "aspirant",
    "consensus",
    "consensus candidate",
    "screening",
    "primaries",
    "primary",
    "inec",
    "peter obi",
    "obi",
    "atiku",
    "jonathan",
    "ndc",
    "apc",
    "pdp",
    "lp",
    "cabinet",
    "reshuffle",
    "minister resignation",
    "coalition",
    "defection",
    "opposition",
]

ELECTION_MATERIAL_TERMS = [
    "2027",
    "election",
    "elections",
    "presidential",
    "presidential candidate",
    "candidate",
    "aspirant",
    "sole aspirant",
    "consensus",
    "consensus candidate",
    "screening",
    "clears",
    "cleared",
    "unveils",
    "nominates",
    "nomination",
    "primaries",
    "primary",
    "party",
    "opposition",
    "coalition",
    "peter obi",
    "atiku",
    "jonathan",
    "ndc",
    "apc",
    "pdp",
    "lp",
]

MARKET_IMPACT_TERMS = [
    "budget",
    "policy",
    "economic",
    "economy",
    "reform",
    "reforms",
    "project",
    "infrastructure",
    "investment",
    "partnership",
    "cooperation",
    "collaboration",
    "technology",
    "technology exchange",
    "knowledge exchange",
    "capacity building",
    "transport",
    "transportation",
    "traffic",
    "loan",
    "approval",
    "contract",
    "security",
    "energy",
    "power",
    "oil",
    "gas",
]

PRESIDENTIAL_EXTERNAL_IMPACT_TERMS = [
    "summit",
    "meeting",
    "cooperation",
    "partnership",
    "economic",
    "economy",
    "investment",
    "reform",
    "reforms",
    "transformation",
    "france-africa",
    "africa-france",
]

RISK_TERMS = [
    "attack",
    "bandit",
    "terrorist",
    "terrorism",
    "boko haram",
    "iswap",
    "vandalism",
    "protest",
    "protests",
    "blocked",
    "close road",
    "right of way",
    "compensation",
    "land acquisition",
    "community",
    "residents",
    "ebola",
    "outbreak",
    "epidemic",
    "pandemic",
    "disease outbreak",
    "public health emergency",
    "cholera",
    "lassa fever",
]

PUBLIC_HEALTH_TERMS = [
    "ebola",
    "outbreak",
    "epidemic",
    "pandemic",
    "disease outbreak",
    "public health",
    "public health emergency",
    "health alert",
    "disease surveillance",
    "cholera",
    "lassa fever",
    "ncdc",
    "world health organization",
    "airport screening",
    "border screening",
]

MACRO_ECONOMIC_TERMS = [
    "inflation",
    "interest rate",
    "monetary policy",
    "exchange rate",
    "forex",
    "fx",
    "naira",
    "devaluation",
    "currency",
    "cbn",
    "central bank",
    "gdp",
    "recession",
    "debt service",
    "fuel price",
    "fuel subsidy",
    "subsidy",
    "food prices",
    "customs duty",
    "tariff",
]

PUBLIC_HOLIDAY_TERMS = [
    "public holiday",
    "public holidays",
    "declares holiday",
    "declare holiday",
    "declares public holiday",
    "declared public holiday",
    "federal government declares",
    "fg declares",
    "eid-el-kabir",
    "eid el kabir",
    "eid al-adha",
    "eid al adha",
    "sallah",
    "sallah holiday",
    "ramadan holiday",
    "christmas holiday",
    "new year holiday",
    "independence day holiday",
    "workers day holiday",
]

GOVERNANCE_SHOCK_TERMS = [
    "state of emergency",
    "emergency rule",
    "impeachment",
    "cabinet reshuffle",
    "minister resignation",
    "resigns",
    "suspended",
    "supreme court",
    "tribunal",
    "electoral act",
    "inec",
    "coalition",
    "defection",
    "opposition",
]

EXTERNAL_TERMS = [
    "china",
    "eu",
    "uk",
    "us",
    "france",
    "saudi",
    "uae",
    "ecowas",
    "bilateral",
    "multilateral",
    "mou",
    "cooperation",
    "partnership",
    "partner",
    "partners",
    "technology exchange",
    "knowledge exchange",
    "capacity building",
    "foreign investment",
    "summit",
]


def _load_entity_lists() -> dict[str, Any]:
    """Load config/entities.yaml (curated strong-signal entity whitelist).

    Returns {} if the file is missing so prescreen still runs on the
    hardcoded lists alone.
    """
    import yaml

    path = ROOT_DIR / "config" / "entities.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def _merge_terms(base: list[str], extra: Any) -> list[str]:
    seen = {str(x).casefold() for x in base}
    out = list(base)
    for item in extra or []:
        key = str(item).casefold()
        if key not in seen:
            out.append(str(item))
            seen.add(key)
    return out


_ENTITY_LISTS = _load_entity_lists()

# Specific named orgs: a single match is enough to keep an item (no second
# co-occurring signal required), because a named contractor / financier /
# regulator / Chinese SOE is high precision. Generic words stay out of this set.
STRONG_ENTITIES = (
    [str(x) for x in _ENTITY_LISTS.get("china_actors", [])]
    + [str(x) for x in _ENTITY_LISTS.get("financiers", [])]
    + [str(x) for x in _ENTITY_LISTS.get("institutions", [])]
    + [str(x) for x in _ENTITY_LISTS.get("companies", [])]
    + [str(x) for x in _ENTITY_LISTS.get("fiscal_policy_terms", [])]
    + [str(x) for x in _ENTITY_LISTS.get("macro_indicators", [])]
)

# Also extend the co-occurrence lists so the existing combination rules see them.
COMPANIES = _merge_terms(
    COMPANIES,
    list(_ENTITY_LISTS.get("companies", [])) + list(_ENTITY_LISTS.get("china_actors", [])),
)
INSTITUTIONS = _merge_terms(INSTITUTIONS, _ENTITY_LISTS.get("institutions", []))
FINANCE_TERMS = _merge_terms(FINANCE_TERMS, _ENTITY_LISTS.get("financiers", []))
# Sector domains stay in the two-signal lane (they are generic, so they need a
# co-occurring action / amount / actor, not a single match).
PROJECT_DOMAINS = _merge_terms(PROJECT_DOMAINS, _ENTITY_LISTS.get("sector_domains", []))

AMOUNT_PATTERN = re.compile(
    r"(?:[$£€₦]\s?\d[\d,.]*|\d[\d,.]*\s?(?:billion|million|trillion|mw|gw|km|kilometres|kilometers|hectares|barrels|bpd|tonnes|tons))",
    re.IGNORECASE,
)


EVENT_TITLE_SIMILARITY_THRESHOLD = 0.30
EVENT_HIGH_TITLE_SIMILARITY_THRESHOLD = 0.72
DEFAULT_LLM_TARGET_ITEMS = int(os.getenv("RSS_PRESCREEN_TARGET_LLM_ITEMS", "0"))

EVENT_STOPWORDS = {
    "about",
    "after",
    "africa",
    "allafrica",
    "amid",
    "based",
    "bank",
    "before",
    "business",
    "court",
    "daily",
    "former",
    "from",
    "govt",
    "guardian",
    "minister",
    "new",
    "news",
    "nigeria",
    "nigerian",
    "official",
    "over",
    "premium",
    "project",
    "reuters",
    "says",
    "state",
    "states",
    "the",
    "this",
    "times",
    "under",
    "updated",
    "with",
    "world",
    "years",
}

GENERIC_EVENT_ENTITIES = {
    "africa",
    "based",
    "bank",
    "fund",
    "government",
    "govts",
    "infrastructure",
    "new",
    "private",
    "sector",
    "south",
    "world",
}

EVENT_ACTION_GROUPS = {
    "court_sentence": [
        "jail",
        "jailed",
        "jails",
        "sentence",
        "sentenced",
        "sentences",
        "convict",
        "convicted",
        "conviction",
        "prison",
        "imprisonment",
    ],
    "approval": ["approve", "approved", "approves", "approval", "fec approves"],
    "contract_award": ["award", "awarded", "awards", "contract", "contractor"],
    "project_start": ["flag off", "flags off", "launch", "launched", "commence", "commenced", "begins"],
    "commissioning": ["commission", "commissioned", "inaugurate", "inaugurated", "complete", "completed"],
    "financing": ["loan", "funding", "financing", "facility", "grant", "budget allocation"],
    "delay_risk": ["delay", "delayed", "suspend", "suspended", "stall", "stalled"],
    "policy_change": ["policy", "tariff", "reform", "regulation", "subsidy"],
}

EVENT_TOPIC_TERMS = {
    "airport",
    "bridge",
    "budget",
    "corruption",
    "electricity",
    "fraud",
    "gas",
    "highway",
    "housing",
    "infrastructure",
    "oil",
    "port",
    "power",
    "rail",
    "railway",
    "refinery",
    "road",
    "solar",
    "transmission",
    "transport",
    "water",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hard-filter RSS metadata before LLM prefiltering.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--target-llm-items",
        type=int,
        default=DEFAULT_LLM_TARGET_ITEMS,
        help=(
            "Optional target number of RSS candidates to pass to LLM prefiltering. "
            "When set above 0, weakly relevant skipped items may be backfilled toward the target. "
            "Defaults to 0, so hard-filtered candidates are not padded."
        ),
    )
    parser.add_argument(
        "--no-event-dedupe",
        action="store_true",
        help="Keep all hard-filtered RSS items instead of collapsing likely same-event items.",
    )
    parser.add_argument(
        "--input-file",
        default="discovered_news.json",
        help="Run-local input file name. Defaults to discovered_news.json.",
    )
    add_run_date_arg(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = resolve_pipeline_input_path(ROOT_DIR, args.run_date, args.input_file)
    items = load_json(input_path)
    if args.limit is not None:
        items = items[: args.limit]

    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for index, item in enumerate(items):
        decision = prescreen_item(item)
        enriched = dict(item)
        enriched.update(decision)
        enriched["_rss_prescreen_result_index"] = index
        results.append(enriched)
        if decision["rss_prescreen_decision"] == "keep":
            kept.append(enriched)
        else:
            skipped.append(enriched)

    soft_backfilled: list[dict[str, Any]] = []
    event_duplicates: list[dict[str, Any]] = []
    kept_before_event_dedupe = len(kept)
    if args.target_llm_items > 0:
        kept, skipped, soft_backfilled, event_duplicates = apply_soft_backfill(
            kept=kept,
            skipped=skipped,
            target_count=args.target_llm_items,
            event_dedupe=not args.no_event_dedupe,
        )
        for item in soft_backfilled:
            result_index = item.get("_rss_prescreen_result_index")
            if isinstance(result_index, int) and 0 <= result_index < len(results):
                results[result_index] = item
    elif not args.no_event_dedupe:
        kept, event_duplicates = dedupe_same_event_items(kept)

    results = [strip_internal_fields(item) for item in results]
    kept = [strip_internal_fields(item) for item in kept]
    skipped = [strip_internal_fields(item) for item in skipped]

    results_path = save_json_with_latest(ROOT_DIR, args.run_date, "rss_prescreen_results.json", results)
    kept_path = save_json_with_latest(ROOT_DIR, args.run_date, "rss_prescreened_news.json", kept)
    skipped_path = save_json_with_latest(ROOT_DIR, args.run_date, "rss_prescreen_skipped_news.json", skipped)
    event_duplicates_path = save_json_with_latest(
        ROOT_DIR, args.run_date, "rss_event_duplicates.json", event_duplicates
    )

    print(f"Loaded {len(items)} RSS items from {input_path}")
    print(f"Kept {kept_before_event_dedupe} items after hard filtering")
    if args.no_event_dedupe:
        print("Skipped event-level RSS dedupe")
    else:
        print(f"Collapsed {len(event_duplicates)} likely same-event RSS items")
    if args.target_llm_items > 0:
        print(f"Soft-backfilled {len(soft_backfilled)} weakly relevant items toward target {args.target_llm_items}")
    print(f"Kept {len(kept)} items for LLM prefiltering")
    print(f"Skipped {len(skipped)} items before LLM")
    print(f"Saved decisions to {results_path}")
    print(f"Saved kept items to {kept_path}")
    print(f"Saved skipped items to {skipped_path}")
    print(f"Saved event duplicate report to {event_duplicates_path}")


def prescreen_item(item: dict[str, Any]) -> dict[str, Any]:
    title = strip_source_suffix(clean_text(item.get("title", "")))
    summary = clean_text(item.get("rss_summary", ""))
    search_keyword = clean_text(item.get("search_keyword", ""))
    search_pool = clean_text(item.get("search_pool", ""))
    text = " ".join([title, summary, search_keyword, search_pool])

    signals = detect_signals(text)
    # A specific named org in the TITLE (not the summary) is enough on its own to
    # keep the item. Title-scoping matters: rss_summary carries unrelated
    # related-article links and the source name (e.g. "Sahara Reporters"), which
    # would otherwise trigger false matches.
    strong_entity_matches = find_matches(title, STRONG_ENTITIES)
    if strong_entity_matches:
        signals = ["named high-value entity: " + ", ".join(strong_entity_matches[:3])] + signals
    negative_matches = find_matches(text, NEGATIVE_TERMS)
    routine_matches = find_matches(title, ROUTINE_TERMS)
    csr_matches = find_matches(text, CSR_TERMS)

    if negative_matches and not signals:
        return skip("clear low-value topic", signals, negative_matches)

    if routine_matches and not signals and not is_material_election_item(text):
        return skip("routine politics or ceremonial item without market impact", signals, routine_matches)

    if csr_matches and not has_material_context(text):
        return skip("CSR/training/award item without project or funding signal", signals, csr_matches)

    if signals:
        return {
            "rss_prescreen_decision": "keep",
            "rss_prescreen_reason": "; ".join(signals[:4]),
            "rss_prescreen_signals": signals,
        }

    return skip("no project, finance, policy, risk or external cooperation signal", signals, [])


def apply_soft_backfill(
    kept: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    target_count: int,
    event_dedupe: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not event_dedupe:
        needed = max(0, target_count - len(kept))
        backfilled = [mark_soft_keep(item) for item in ranked_soft_candidates(skipped)[:needed]]
        backfilled_keys = {result_key(item) for item in backfilled}
        return (
            kept + backfilled,
            [item for item in skipped if result_key(item) not in backfilled_keys],
            backfilled,
            [],
        )

    deduped_kept, event_duplicates = dedupe_same_event_items(kept)
    if len(deduped_kept) >= target_count:
        return deduped_kept, skipped, [], event_duplicates

    candidates = ranked_soft_candidates(skipped)
    selected_backfills: list[dict[str, Any]] = []
    selected_keys: set[str] = set()

    for candidate in candidates:
        duplicate_match = find_duplicate_against_kept(candidate, deduped_kept)
        if duplicate_match:
            duplicate_item = dict(candidate)
            duplicate_item["rss_event_duplicate"] = duplicate_match
            event_duplicates.append(strip_internal_fields(duplicate_item))
            continue

        backfilled = mark_soft_keep(candidate)
        selected_backfills.append(backfilled)
        selected_keys.add(result_key(backfilled))
        deduped_kept.append(backfilled)
        if len(deduped_kept) >= target_count:
            break

    remaining_skipped = [item for item in skipped if result_key(item) not in selected_keys]
    return deduped_kept, remaining_skipped, selected_backfills, event_duplicates


def find_duplicate_against_kept(item: dict[str, Any], kept: list[dict[str, Any]]) -> dict[str, Any] | None:
    for cluster_index, existing in enumerate(kept, start=1):
        match = event_duplicate_match(item, existing, cluster_index)
        if match:
            return match
    return None


def ranked_soft_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for item in items:
        score, signals = soft_prescreen_score(item)
        if score <= 0:
            continue
        enriched = dict(item)
        enriched["_rss_soft_score"] = score
        enriched["_rss_soft_signals"] = signals
        scored.append(enriched)
    scored.sort(key=soft_candidate_sort_key, reverse=True)
    return scored


def soft_candidate_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    return (
        int(item.get("_rss_soft_score", 0)),
        source_priority_score(item),
        len(clean_text(item.get("rss_summary", ""))),
    )


def soft_prescreen_score(item: dict[str, Any]) -> tuple[int, list[str]]:
    title = strip_source_suffix(clean_text(item.get("title", "")))
    summary = clean_text(item.get("rss_summary", ""))
    search_keyword = clean_text(item.get("search_keyword", ""))
    search_pool = clean_text(item.get("search_pool", ""))
    text = " ".join([title, summary, search_keyword, search_pool])

    signals: list[str] = []
    score = 0
    checks = [
        ("single project/domain term", PROJECT_DOMAINS, 4),
        ("single finance/fiscal term", FINANCE_TERMS, 5),
        ("market-impact term", MARKET_IMPACT_TERMS, 3),
        ("credible public/private actor", INSTITUTIONS + COMPANIES, 2),
        ("external cooperation term", EXTERNAL_TERMS, 2),
        ("security/social risk term", RISK_TERMS, 2),
        ("politics/governance term", POLITICS_TERMS, 1),
        ("public-health risk term", PUBLIC_HEALTH_TERMS, 4),
        ("macro-economic shock term", MACRO_ECONOMIC_TERMS, 4),
        ("public-holiday closure notice", PUBLIC_HOLIDAY_TERMS, 4),
        ("governance shock term", GOVERNANCE_SHOCK_TERMS, 3),
    ]
    for label, terms, weight in checks:
        matches = find_matches(text, terms)
        if matches:
            score += weight
            signals.append(f"{label}: {', '.join(matches[:3])}")

    if AMOUNT_PATTERN.search(text):
        score += 3
        signals.append("amount or scale mentioned")

    if find_matches(text, NEGATIVE_TERMS):
        score -= 6
    if find_matches(title, ROUTINE_TERMS):
        score -= 3
    if find_matches(text, CSR_TERMS):
        score -= 2

    return score, signals


def mark_soft_keep(item: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    original_reason = str(enriched.get("rss_prescreen_reason", "")).strip()
    soft_signals = [str(signal) for signal in enriched.get("_rss_soft_signals", [])]
    enriched["rss_prescreen_decision"] = "keep"
    enriched["rss_prescreen_reason"] = (
        "soft backfill toward LLM candidate target"
        + (f"; original skip: {original_reason}" if original_reason else "")
    )
    enriched["rss_prescreen_signals"] = soft_signals
    enriched["rss_prescreen_soft_backfill"] = True
    enriched["rss_prescreen_soft_score"] = int(enriched.get("_rss_soft_score", 0))
    return enriched


def result_key(item: dict[str, Any]) -> str:
    return str(item.get("_rss_prescreen_result_index", item.get("google_news_url") or item.get("title") or id(item)))


def strip_internal_fields(item: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(item)
    for key in ["_rss_prescreen_result_index", "_rss_soft_score", "_rss_soft_signals"]:
        cleaned.pop(key, None)
    return cleaned


def detect_signals(text: str) -> list[str]:
    signals: list[str] = []
    sector_text = remove_political_power_usage(text)
    has_domain = bool(find_matches(sector_text, PROJECT_DOMAINS))
    has_action = bool(find_matches(text, PROJECT_ACTIONS))
    has_finance = bool(find_matches(text, FINANCE_TERMS))
    has_institution = bool(find_matches(text, INSTITUTIONS))
    has_company = bool(find_matches(text, COMPANIES))
    has_politics = bool(find_matches(text, POLITICS_TERMS))
    has_material_election = is_material_election_item(text)
    has_market_impact = bool(find_matches(sector_text, MARKET_IMPACT_TERMS))
    has_risk = bool(find_matches(text, RISK_TERMS))
    has_public_health = is_material_public_health_item(text)
    has_macro_economic = is_material_macro_economic_item(text)
    has_public_holiday = is_material_public_holiday_item(text)
    has_governance_shock = is_material_governance_shock(text)
    has_external = bool(find_matches(text, EXTERNAL_TERMS))
    has_presidential_actor = bool(find_matches(text, ["tinubu", "president"]))
    has_presidential_external_context = bool(find_matches(text, PRESIDENTIAL_EXTERNAL_IMPACT_TERMS))
    has_amount = bool(AMOUNT_PATTERN.search(text))

    if has_domain and has_action:
        signals.append("project/domain plus concrete action")
    if has_domain and has_amount:
        signals.append("project/domain plus amount or scale")
    if has_domain and (has_institution or has_company):
        signals.append("project/domain plus credible actor")
    if has_finance and (has_domain or has_institution or has_amount):
        signals.append("finance/fiscal signal with actor, amount or project context")
    if has_politics and has_market_impact:
        signals.append("politics/governance with policy, project or investment impact")
    if has_material_election:
        signals.append("material 2027 election/candidate development")
    if has_governance_shock:
        signals.append("material governance or political shock")
    if has_public_health:
        signals.append("major public-health risk with national or economic implications")
    if has_macro_economic:
        signals.append("major macro-economic or fiscal signal")
    if has_public_holiday:
        signals.append("national public-holiday or major festival closure notice")
    if has_risk and (has_domain or has_market_impact):
        signals.append("security/social risk affecting project or market environment")
    if has_external and has_market_impact:
        signals.append("external cooperation with investment/project/policy relevance")
    if has_presidential_actor and has_external and has_presidential_external_context:
        signals.append("presidential external engagement with economic/cooperation relevance")
    if has_amount and (has_institution or has_company):
        signals.append("amount/scale plus credible actor")

    return unique_preserving_order(signals)


def is_material_public_health_item(text: str) -> bool:
    if not find_matches(text, PUBLIC_HEALTH_TERMS):
        return False
    nigeria_response_context = [
        "nigeria",
        "federal government",
        "fg",
        "ncdc",
        "ministry of health",
        "airport",
        "border",
        "screening",
        "alert",
        "confirmed",
        "cases",
        "case",
        "emergency",
        "surveillance",
        "high alert",
    ]
    outbreak_context = [
        "outbreak",
        "confirmed",
        "cases",
        "case",
        "death",
        "deaths",
        "emergency",
        "alert",
        "surveillance",
        "screening",
        "travel restrictions",
        "quarantine",
    ]
    return bool(find_matches(text, nigeria_response_context)) and bool(find_matches(text, outbreak_context))


def is_material_macro_economic_item(text: str) -> bool:
    if not find_matches(text, MACRO_ECONOMIC_TERMS):
        return False
    material_context = [
        "cbn",
        "central bank",
        "federal government",
        "fg",
        "finance minister",
        "monetary policy committee",
        "mpc",
        "imf",
        "world bank",
        "budget",
        "policy",
        "rate",
        "rates",
        "price",
        "prices",
        "market",
        "import",
        "export",
        "debt",
        "tax",
        "tariff",
        "reserves",
        "liquidity",
        "depreciation",
        "devaluation",
        "week-on-week",
        "capital inflow",
        "capital inflows",
    ]
    return bool(find_matches(text, material_context))


def is_material_public_holiday_item(text: str) -> bool:
    if not find_matches(text, PUBLIC_HOLIDAY_TERMS):
        return False
    official_actor = [
        "federal government",
        "fg",
        "minister",
        "ministry of interior",
        "interior minister",
        "presidency",
    ]
    official_action = [
        "declares",
        "declared",
        "announces",
        "announced",
        "holiday",
        "holidays",
        "nationwide",
    ]
    closure_or_market_context = [
        "closure",
        "closed",
        "shuts",
        "shutdown",
        "travel",
        "transport",
        "train",
        "airport",
        "border",
        "bank",
        "banks",
        "ngx",
        "stock market",
        "trading",
        "money transfer",
        "payment",
        "security",
        "gridlock",
    ]
    return (
        bool(find_matches(text, official_actor)) and bool(find_matches(text, official_action))
    ) or bool(find_matches(text, closure_or_market_context))


def is_material_governance_shock(text: str) -> bool:
    shock_matches = find_matches(text, GOVERNANCE_SHOCK_TERMS)
    if not shock_matches:
        return False
    strong_shocks = [
        "state of emergency",
        "emergency rule",
        "impeachment",
        "cabinet reshuffle",
        "minister resignation",
        "supreme court",
        "tribunal",
        "electoral act",
        "inec",
    ]
    material_context = [
        "president",
        "tinubu",
        "national assembly",
        "senate",
        "governor",
        "minister",
        "supreme court",
        "tribunal",
        "inec",
        "election",
        "2027",
        "policy",
        "budget",
        "security",
    ]
    if find_matches(text, strong_shocks):
        return bool(find_matches(text, material_context))
    weak_context = [
        "president",
        "tinubu",
        "national assembly",
        "senate",
        "house of representatives",
        "reps",
        "governor",
        "minister",
        "inec",
        "election",
        "2027",
        "apc",
        "pdp",
        "lp",
        "ndc",
        "coalition",
        "opposition",
    ]
    return bool(find_matches(text, weak_context))


def remove_political_power_usage(text: str) -> str:
    replacements = [
        "political power",
        "seek power",
        "seeks power",
        "seeking power",
        "grab power",
        "grabs power",
        "retain power",
        "retains power",
        "return to power",
        "returns to power",
        "back to power",
        "power brokers",
        "power belongs",
        "power shift",
        "power rotation",
        "power sharing",
        "power tussle",
    ]
    cleaned = str(text or "")
    for phrase in replacements:
        cleaned = re.sub(rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])", "politics", cleaned, flags=re.IGNORECASE)
    return cleaned


def is_material_election_item(text: str) -> bool:
    matches = find_matches(text, ELECTION_MATERIAL_TERMS)
    if not matches:
        return False
    has_2027_or_election = bool(find_matches(text, ["2027", "election", "elections", "presidential"]))
    has_actor_or_party = bool(
        find_matches(
            text,
            [
                "peter obi",
                "obi",
                "atiku",
                "jonathan",
                "tinubu",
                "ndc",
                "apc",
                "pdp",
                "lp",
                "opposition",
                "coalition",
            ],
        )
    )
    has_process = bool(
        find_matches(
            text,
            [
                "candidate",
                "aspirant",
                "consensus",
                "screening",
                "clears",
                "cleared",
                "unveils",
                "nominates",
                "nomination",
                "primaries",
                "primary",
            ],
        )
    )
    has_obi_ndc_process = bool(find_matches(text, ["obi"])) and bool(find_matches(text, ["ndc"])) and has_process
    return (has_2027_or_election and has_actor_or_party and has_process) or has_obi_ndc_process


def has_material_context(text: str) -> bool:
    return bool(
        find_matches(text, PROJECT_DOMAINS)
        or find_matches(text, PROJECT_ACTIONS)
        or find_matches(text, FINANCE_TERMS)
        or AMOUNT_PATTERN.search(text)
    )


def skip(reason: str, signals: list[str], matches: list[str]) -> dict[str, Any]:
    details = reason
    if matches:
        details = f"{reason}: {', '.join(matches[:5])}"
    return {
        "rss_prescreen_decision": "skip",
        "rss_prescreen_reason": details,
        "rss_prescreen_signals": signals,
    }


def find_matches(text: str, keywords: list[str]) -> list[str]:
    matches: list[str] = []
    for keyword in keywords:
        pattern = r"(?<![A-Za-z0-9])" + re.escape(str(keyword)) + r"(?![A-Za-z0-9])"
        if re.search(pattern, text, flags=re.IGNORECASE):
            matches.append(str(keyword))
    return matches


def clean_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text.replace("&nbsp;", " ")).strip()


def dedupe_same_event_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    duplicate_reports: list[dict[str, Any]] = []

    for item in items:
        matching_cluster, match = find_event_cluster(item, clusters)
        if matching_cluster is None:
            clusters.append([item])
            continue
        duplicate_item = dict(item)
        duplicate_item["rss_event_duplicate"] = match
        duplicate_reports.append(duplicate_item)
        matching_cluster.append(item)

    return [merge_event_cluster(cluster) for cluster in clusters], duplicate_reports


def find_event_cluster(
    item: dict[str, Any], clusters: list[list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    for cluster_index, cluster in enumerate(clusters, start=1):
        for existing in cluster:
            match = event_duplicate_match(item, existing, cluster_index)
            if match:
                return cluster, match
    return None, None


def event_duplicate_match(
    item: dict[str, Any], existing: dict[str, Any], cluster_index: int
) -> dict[str, Any] | None:
    if article_urls(item) & article_urls(existing):
        return {
            "match_type": "url",
            "cluster_index": cluster_index,
            "score": 1.0,
            "kept_title": existing.get("title", ""),
        }

    left = event_signature(item)
    right = event_signature(existing)
    title_score = jaccard(left["tokens"], right["tokens"])
    shared_entities = left["entities"] & right["entities"]
    shared_actions = left["actions"] & right["actions"]
    shared_numbers = left["numbers"] & right["numbers"]
    shared_topics = left["topics"] & right["topics"]
    has_specific_entity = any(is_specific_event_entity(entity) for entity in shared_entities)

    if title_score >= EVENT_HIGH_TITLE_SIMILARITY_THRESHOLD:
        match_type = "high_title_similarity"
    elif has_specific_entity and shared_actions and (
        shared_numbers
        or title_score >= EVENT_TITLE_SIMILARITY_THRESHOLD
        or len(shared_topics) >= 1
    ):
        match_type = "entity_action"
    elif shared_numbers and (
        has_specific_entity
        or title_score >= 0.45
        or (shared_actions and len(shared_topics) >= 1)
    ):
        match_type = "entity_number"
    elif has_specific_entity and len(shared_entities) >= 2 and title_score >= 0.45:
        match_type = "entity_title"
    else:
        return None

    return {
        "match_type": match_type,
        "cluster_index": cluster_index,
        "score": round(title_score, 3),
        "shared_entities": sorted(shared_entities),
        "shared_actions": sorted(shared_actions),
        "shared_numbers": sorted(shared_numbers),
        "shared_topics": sorted(shared_topics),
        "kept_title": existing.get("title", ""),
    }


def event_signature(item: dict[str, Any]) -> dict[str, set[str]]:
    title = strip_source_suffix(clean_text(item.get("title", "")))
    summary = strip_source_suffix(clean_text(item.get("rss_summary", "")))
    text = f"{title} {summary}"
    normalized = normalize_for_event(text)
    return {
        "tokens": event_tokens(normalized),
        "entities": event_entities(title),
        "actions": event_actions(normalized),
        "numbers": event_numbers(normalized),
        "topics": event_topics(normalized),
    }


def event_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.sub(r"[^a-z0-9.%]+", " ", text).split()
        if len(token) > 2 and token not in EVENT_STOPWORDS
    }


def event_entities(title: str) -> set[str]:
    terms: set[str] = set()
    clean_title = strip_source_suffix(html.unescape(str(title or "")))

    for phrase in re.findall(r"\b[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,4}\b", clean_title):
        normalized = normalize_entity(phrase)
        if is_event_entity(normalized):
            terms.add(normalized)
            words = normalized.split()
            if len(words) > 1:
                terms.update(word for word in words if is_event_entity(word))

    for acronym in re.findall(r"\b[A-Z]{2,}(?:\d+)?\b", clean_title):
        normalized = normalize_entity(acronym)
        if is_event_entity(normalized):
            terms.add(normalized)

    return terms


def event_actions(text: str) -> set[str]:
    actions: set[str] = set()
    for group, terms in EVENT_ACTION_GROUPS.items():
        if find_matches(text, terms):
            actions.add(group)
    return actions


def event_numbers(text: str) -> set[str]:
    numbers: set[str] = set()
    patterns = [
        r"\b\d[\d,.]*\s?(?:years?|months?|days?)\b",
        r"\b\d[\d,.]*\s?(?:bn|billion|m|million|trillion)\b",
        r"\b\d[\d,.]*\s?(?:mw|gw|km|kilometres|kilometers|hectares|bpd|tons|tonnes)\b",
        r"(?:\$|n|ngn)\s?\d[\d,.]*\s?(?:bn|billion|m|million|trillion)?\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            numbers.add(re.sub(r"\s+", " ", match.group(0).casefold()).strip())
    return numbers


def event_topics(text: str) -> set[str]:
    return {term for term in EVENT_TOPIC_TERMS if re.search(rf"\b{re.escape(term)}\b", text)}


def strip_source_suffix(value: str) -> str:
    return re.sub(r"\s+-\s+[^-]+$", "", str(value or "")).strip()


def normalize_for_event(value: str) -> str:
    text = html.unescape(str(value or "")).casefold()
    text = text.replace("ex-", "ex ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_entity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def is_event_entity(entity: str) -> bool:
    if not entity or entity in EVENT_STOPWORDS:
        return False
    if entity.startswith("nigeria ") or entity == "nigeria":
        return False
    if len(entity) < 3:
        return False
    return True


def is_specific_event_entity(entity: str) -> bool:
    if entity in GENERIC_EVENT_ENTITIES:
        return False
    if len(entity.split()) > 1:
        return True
    if re.search(r"\d", entity):
        return True
    return entity not in EVENT_STOPWORDS


def article_urls(item: dict[str, Any]) -> set[str]:
    return {
        str(url).strip().rstrip("/").casefold()
        for url in [
            item.get("google_news_url"),
            item.get("resolved_url"),
            item.get("original_url"),
            item.get("url"),
        ]
        if url
    }


def merge_event_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    primary = max(cluster, key=rss_event_quality)
    duplicates = [item for item in cluster if item is not primary]
    merged = with_source_priority(primary)
    merged["rss_event_cluster_size"] = len(cluster)
    merged["rss_event_duplicate_count"] = len(duplicates)
    merged["rss_event_duplicate_sources"] = unique_preserving_order(
        [str(item.get("source", "")) for item in duplicates]
    )
    merged["rss_event_duplicate_titles"] = unique_preserving_order(
        [str(item.get("title", "")) for item in duplicates]
    )
    merged["rss_event_duplicate_urls"] = unique_preserving_order(
        [url for item in duplicates for url in article_urls(item)]
    )
    return merged


def rss_event_quality(item: dict[str, Any]) -> tuple[int, int, int]:
    source_score = source_priority_score(item)
    summary_length = len(clean_text(item.get("rss_summary", "")))
    title_length = len(strip_source_suffix(clean_text(item.get("title", ""))))
    return (source_score, summary_length, title_length)


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def unique_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def load_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


if __name__ == "__main__":
    main()
