from __future__ import annotations

import re
from typing import Any

from processors.freshness import assess_freshness
from source_priority import source_priority


MAJOR_PUBLIC_HEALTH_TERMS = [
    "Ebola",
    "cholera",
    "Lassa fever",
    "disease outbreak",
    "public health emergency",
    "health alert",
    "border screening",
    "airport screening",
    "NCDC",
    "WHO",
    "Federal Ministry of Health",
]

PUBLIC_HEALTH_CONTEXT_TERMS = [
    "outbreak",
    "epidemic",
    "pandemic",
    "virus",
    "disease",
    "infection",
    "public health",
    "quarantine",
    "surveillance",
    "screening",
]

MACRO_ECONOMIC_TERMS = [
    "inflation",
    "interest rate",
    "monetary policy",
    "MPC",
    "CBN",
    "Central Bank",
    "exchange rate",
    "forex",
    "FX",
    "naira",
    "devaluation",
    "debt service",
    "fuel subsidy",
    "customs duty",
    "tariff",
    "fiscal deficit",
    "food prices",
]

MACRO_CONTEXT_TERMS = [
    "policy",
    "rate",
    "market",
    "investor",
    "import",
    "export",
    "budget",
    "debt",
    "fiscal",
    "fuel",
    "food",
    "prices",
    "tax",
    "customs",
]

PUBLIC_HOLIDAY_TERMS = [
    "public holiday",
    "public holidays",
    "declares public holiday",
    "declared public holiday",
    "federal government declares",
    "fg declares",
    "ministry of interior",
    "eid-el-kabir",
    "eid el kabir",
    "eid al-adha",
    "eid al adha",
    "sallah",
    "sallah holiday",
    "holiday closure",
]

GOVERNANCE_SHOCK_TERMS = [
    "state of emergency",
    "impeachment",
    "cabinet reshuffle",
    "minister resignation",
    "ministerial resignation",
    "Supreme Court",
    "tribunal",
    "electoral act",
    "INEC",
    "anti-corruption probe",
    "EFCC",
    "coalition",
    "opposition realignment",
    "party merger",
    "defection",
]

GOVERNANCE_CONTEXT_TERMS = [
    "president",
    "Tinubu",
    "National Assembly",
    "Senate",
    "governor",
    "minister",
    "election",
    "elections",
    "2027",
    "policy",
    "budget",
    "security",
    "court",
]


def score_article(article: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    text = article_text(article)
    title = article.get("title", "")
    weights = rules["weights"]
    caps = rules["caps"]

    score = 0
    reasons: list[str] = []
    matched_keywords: list[str] = []

    entities = extract_entities(text, rules)

    score += add_keyword_group(
        text, rules["sector_keywords"], weights["sector_keyword"], caps["sector_keyword"],
        "sector keyword", reasons, matched_keywords
    )
    score += add_keyword_group(
        text, rules["project_signals"], weights["project_signal"], caps["project_signal"],
        "project signal", reasons, matched_keywords
    )
    score += add_keyword_group(
        text, rules["financiers"], weights["financier"], caps["financier"],
        "financier", reasons, matched_keywords
    )
    score += add_keyword_group(
        text, rules["institutions"], weights["institution"], caps["institution"],
        "institution", reasons, matched_keywords
    )
    score += add_keyword_group(
        text, rules["companies"], weights["company"], caps["company"],
        "company", reasons, matched_keywords
    )
    score += add_keyword_group(
        text, rules["locations"], weights["location"], caps["location"],
        "location", reasons, matched_keywords
    )

    score += add_entity_score(
        "amount", entities["amounts"], weights["amount"], caps["amount"], reasons
    )
    score += add_entity_score(
        "distance/capacity",
        entities["distances"] + entities["capacities"],
        weights["distance_or_capacity"],
        caps["distance_or_capacity"],
        reasons,
    )
    score += add_entity_score(
        "date/deadline", entities["dates"], weights["date_or_deadline"],
        caps["date_or_deadline"], reasons
    )

    combination_score = score_combinations(text, entities, rules, reasons)
    score += min(combination_score, caps["combination_signal"])

    china_score, china_matches = score_china_signals(text, rules, reasons)
    score += china_score
    matched_keywords.extend(china_matches)

    election_score, election_matches = score_election_signals(text, rules, reasons)
    score += election_score
    matched_keywords.extend(election_matches)

    news_type = classify_news_type(text, rules)
    selection_lane = classify_selection_lane(article, news_type)
    lane_score, lane_value_signals, lane_matches, lane_reasons = score_lane_specific_signals(
        text, title, selection_lane, rules
    )
    score += lane_score
    matched_keywords.extend(lane_matches)
    reasons.extend(lane_reasons)

    negative_matches = find_matches(text, rules["negative_signals"])
    if negative_matches:
        delta = len(negative_matches) * weights["negative_signal"]
        score += delta
        matched_keywords.extend(negative_matches)
        reasons.append(reason("negative signal", negative_matches, weights["negative_signal"], delta))

    routine_matches = find_matches(title, rules["routine_politics_signals"])
    if routine_matches:
        delta = len(routine_matches) * weights["routine_politics_signal"]
        score += delta
        matched_keywords.extend(routine_matches)
        reasons.append(
            reason("routine politics signal", routine_matches, weights["routine_politics_signal"], delta)
        )

    value_signals = detect_value_signals(text, title, entities, rules, selection_lane)
    value_signals.extend(lane_value_signals)
    value_signals = unique_preserving_order(value_signals)
    project_stage = classify_project_stage(text, rules)
    freshness = assess_freshness(
        article,
        max_age_days=rules["thresholds"].get("max_article_age_days", 2),
    )
    relevance_level = level_for_score(score, rules["thresholds"])
    if freshness["freshness_status"] == "stale":
        reasons.append(f"freshness stale: {freshness['freshness_reason']}")

    scored_article = dict(article)
    scored_article.update(
        {
            "relevance_score": score,
            "relevance_level": relevance_level,
            "relevance_reasons": reasons or ["no scoring keywords matched"],
            "matched_keywords": unique_preserving_order(matched_keywords),
            "review_candidate_status": "candidate",
            "news_type": news_type,
            "selection_lane": selection_lane,
            "value_signals": value_signals,
            "project_stage": project_stage,
            **freshness,
            "entities": entities,
            "watch_reason": build_watch_reason(news_type, project_stage, entities, score),
            "evidence_snippets": evidence_snippets(text, scored_terms(matched_keywords, entities)),
            **source_priority(article),
        }
    )
    return scored_article


def article_text(article: dict[str, Any]) -> str:
    parts = [
        article.get("title", ""),
        article.get("source", ""),
        article.get("rss_summary", ""),
        article.get("article_text", ""),
    ]
    return "\n".join(part for part in parts if part)


def add_keyword_group(
    text: str,
    keywords: list[str],
    weight: int,
    cap: int,
    label: str,
    reasons: list[str],
    matched_keywords: list[str],
) -> int:
    matches = find_matches(text, keywords)
    if not matches:
        return 0
    matched_keywords.extend(matches)
    raw_score = len(matches) * weight
    applied_score = min(raw_score, cap)
    reasons.append(reason(label, matches, weight, applied_score))
    return applied_score


def add_entity_score(
    label: str,
    values: list[str],
    weight: int,
    cap: int,
    reasons: list[str],
) -> int:
    if not values or weight == 0 or cap == 0:
        return 0
    raw_score = len(values) * weight
    applied_score = min(raw_score, cap)
    reasons.append(f"{label} (+{weight} each, capped at {cap}): {', '.join(values[:8])}")
    return applied_score


def extract_entities(text: str, rules: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "amounts": unique_preserving_order(extract_amounts(text)),
        "distances": unique_preserving_order(extract_distances(text)),
        "capacities": unique_preserving_order(extract_capacities(text)),
        "dates": unique_preserving_order(extract_dates(text)),
        "locations": find_matches(text, rules["locations"]),
        "agencies": find_matches(text, rules["institutions"]),
        "companies": find_matches(text, rules["companies"]),
        "financiers": find_matches(text, rules["financiers"]),
    }


def extract_amounts(text: str) -> list[str]:
    patterns = [
        r"(?:₦|\$|£|€|NGN|USD|GBP|EUR)\s?\d[\d,.]*(?:\s?(?:billion|million|trillion))?",
        r"\d[\d,.]*\s?(?:billion|million|trillion)\s?(?:naira|dollars|euros|pounds)?",
    ]
    return regex_matches(text, patterns)


def extract_distances(text: str) -> list[str]:
    patterns = [r"\d[\d,.]*\s?(?:km|kilometres|kilometers|metres|meters|hectares)"]
    return regex_matches(text, patterns)


def extract_capacities(text: str) -> list[str]:
    patterns = [
        r"\d[\d,.]*\s?(?:MW|GW|MWh|tonnes|tons|TEUs|barrels|bpd|sqm|square metres|hectares)",
        r"\d[\d,.]*\s?(?:passengers|vehicles|trucks|jobs|workdays)",
    ]
    return regex_matches(text, patterns)


def extract_dates(text: str) -> list[str]:
    patterns = [
        r"\b20\d{2}\b",
        r"\bQ[1-4]\s?20\d{2}\b",
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\b",
    ]
    return regex_matches(text, patterns)


def regex_matches(text: str, patterns: list[str]) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(match.group(0) for match in re.finditer(pattern, text, re.IGNORECASE))
    return matches


def score_combinations(
    text: str,
    entities: dict[str, list[str]],
    rules: dict[str, Any],
    reasons: list[str],
) -> int:
    weight = rules["weights"]["combination_signal"]
    score = 0
    combination_reasons: list[str] = []

    has_project = bool(find_matches(text, rules["project_signals"]))
    has_finance = bool(entities["financiers"] or find_matches(text, rules["news_types"]["financing_budget"]))
    has_delivery = bool(find_matches(text, ["approved", "awarded", "contract awarded", "FEC", "PPP", "EPC", "concession"]))
    has_security = bool(find_matches(text, rules["news_types"]["security_risk"]))
    has_node = bool(find_matches(text, ["road", "bridge", "highway", "port", "airport", "pipeline", "railway", "power plant"]))
    has_election = bool(find_matches(text, rules["news_types"]["election_politics"]))
    has_policy = bool(find_matches(text, rules["news_types"]["policy_regulation"] + rules["news_types"]["financing_budget"]))
    has_public_health = has_material_public_health_event(text)
    has_macro_economic = has_material_macro_economic_event(text)
    has_governance_shock = has_material_governance_shock(text)

    if has_project and (entities["amounts"] or entities["distances"] or entities["capacities"]):
        score += weight
        combination_reasons.append("project plus measurable amount/scale")
    if has_project and (entities["agencies"] or entities["companies"]):
        score += weight
        combination_reasons.append("project plus agency/company")
    if has_finance and has_project:
        score += weight
        combination_reasons.append("financing/budget plus project context")
    if has_delivery and has_project:
        score += weight
        combination_reasons.append("approval/award/procurement plus project context")
    if has_security and has_node:
        score += weight
        combination_reasons.append("security risk affecting transport/energy/logistics node")
    if has_election and has_policy:
        score += weight
        combination_reasons.append("election politics with policy/fiscal context")
    if has_public_health:
        score += weight
        combination_reasons.append("major public-health risk with national or economic implications")
    if has_macro_economic and has_policy:
        score += weight
        combination_reasons.append("macro-economic signal with policy/fiscal context")
    if has_governance_shock and (has_policy or has_election):
        score += weight
        combination_reasons.append("major governance/election shock with policy context")

    if combination_reasons:
        reasons.append(f"combination signal (+{weight} each): {', '.join(combination_reasons)}")
    return score


def score_china_signals(
    text: str,
    rules: dict[str, Any],
    reasons: list[str],
) -> tuple[int, list[str]]:
    china_rules = rules.get("china_signals", {})
    if not china_rules:
        return 0, []

    actor_matches = find_matches(text, china_rules.get("actors", []))
    context_matches = find_matches(text, china_rules.get("context", []))
    if not actor_matches or not context_matches:
        return 0, []

    weight = int(rules["weights"].get("china_signal", 0) or 0)
    cap = int(rules["caps"].get("china_signal", weight) or weight)
    if not weight or not cap:
        return 0, actor_matches

    applied_score = min(weight * min(len(actor_matches), 2), cap)
    reasons.append(
        "China-related signal "
        f"(+{weight} each, capped at {cap}): "
        f"{', '.join((actor_matches + context_matches)[:8])}"
    )
    return applied_score, actor_matches + context_matches


def score_election_signals(
    text: str,
    rules: dict[str, Any],
    reasons: list[str],
) -> tuple[int, list[str]]:
    election_terms = rules.get("election_signals", {})
    if not election_terms:
        election_terms = {
            "actors": [
                "Peter Obi",
                "Obi",
                "Atiku",
                "Jonathan",
                "Tinubu",
                "NDC",
                "APC",
                "PDP",
                "Labour Party",
                "LP",
            ],
            "process": [
                "2027",
                "election",
                "elections",
                "presidential candidate",
                "consensus candidate",
                "sole aspirant",
                "screening",
                "screening committee",
                "presidential screening",
                "primary",
                "primaries",
                "coalition",
                "opposition",
            ],
        }

    actor_matches = find_matches(text, election_terms.get("actors", []))
    process_matches = find_matches(text, election_terms.get("process", []))
    if not actor_matches or not process_matches:
        return 0, actor_matches + process_matches

    weight = int(rules["weights"].get("election_signal", 0) or 0)
    cap = int(rules["caps"].get("election_signal", weight) or weight)
    if not weight or not cap:
        return 0, actor_matches + process_matches

    applied_score = min(weight * min(len(actor_matches), 2), cap)
    reasons.append(
        "material election signal "
        f"(+{weight} each, capped at {cap}): "
        f"{', '.join((actor_matches + process_matches)[:10])}"
    )
    return applied_score, actor_matches + process_matches


def has_material_public_health_event(text: str) -> bool:
    direct_matches = find_matches(text, MAJOR_PUBLIC_HEALTH_TERMS)
    if direct_matches:
        return True
    public_health_context = [
        term for term in PUBLIC_HEALTH_CONTEXT_TERMS if term not in {"outbreak", "epidemic", "pandemic"}
    ]
    return bool(
        find_matches(text, ["outbreak", "epidemic", "pandemic"])
        and find_matches(text, public_health_context)
    )


def has_material_macro_economic_event(text: str) -> bool:
    macro_matches = find_matches(text, MACRO_ECONOMIC_TERMS)
    if len(macro_matches) >= 2:
        return True
    return bool(macro_matches and find_matches(text, MACRO_CONTEXT_TERMS))


def has_material_public_holiday_event(text: str) -> bool:
    if not find_matches(text, PUBLIC_HOLIDAY_TERMS):
        return False
    context_terms = [
        "federal government",
        "fg",
        "minister",
        "ministry of interior",
        "declares",
        "declared",
        "nigeria",
        "nationwide",
        "holiday",
        "holidays",
        "eid",
        "sallah",
    ]
    return bool(find_matches(text, context_terms))


def has_material_governance_shock(text: str) -> bool:
    if find_matches(text, GOVERNANCE_SHOCK_TERMS) and find_matches(text, GOVERNANCE_CONTEXT_TERMS):
        return True
    election_terms = [
        "2027",
        "election",
        "elections",
        "presidential candidate",
        "consensus candidate",
        "sole aspirant",
        "screening committee",
        "presidential screening",
        "primary",
        "primaries",
    ]
    election_actors = [
        "Peter Obi",
        "Obi",
        "Atiku",
        "Jonathan",
        "Tinubu",
        "APC",
        "PDP",
        "Labour Party",
        "LP",
        "opposition",
        "coalition",
    ]
    return bool(find_matches(text, election_terms) and find_matches(text, election_actors))


def score_lane_specific_signals(
    text: str,
    title: str,
    lane: str,
    rules: dict[str, Any],
) -> tuple[int, list[str], list[str], list[str]]:
    lane_rules = rules.get("lane_rules", {}).get(lane, {})
    score = 0
    value_signals: list[str] = []
    matched_keywords: list[str] = []
    reasons: list[str] = []

    if lane == "finance_policy":
        macro_matches = find_matches(text, MACRO_ECONOMIC_TERMS)
        macro_context = find_matches(text, MACRO_CONTEXT_TERMS)
        matched_keywords.extend(macro_matches + macro_context)

        if has_material_macro_economic_event(text):
            score += 14
            value_signals.append("macro-economic or monetary-policy shock affecting market conditions")
            reasons.append(
                "macro-economic/monetary policy lane signal (+14): "
                + ", ".join((macro_matches + macro_context)[:8])
            )
        elif macro_matches:
            score += 7
            value_signals.append("macro-economic policy watch item")
            reasons.append(f"macro-economic watch signal (+7): {', '.join(macro_matches[:8])}")

    if lane == "security_risk":
        health_matches = find_matches(text, MAJOR_PUBLIC_HEALTH_TERMS + PUBLIC_HEALTH_CONTEXT_TERMS)
        security_matches = find_matches(text, rules["news_types"]["security_risk"])
        risk_context = find_matches(
            text,
            [
                "airport",
                "border",
                "screening",
                "surveillance",
                "emergency",
                "national",
                "state",
                "travel",
                "logistics",
                "market",
                "project",
                "infrastructure",
                "energy",
                "transport",
            ],
        )
        matched_keywords.extend(health_matches + security_matches + risk_context)

        if has_material_public_health_event(text):
            score += 16
            value_signals.append("major public-health risk with national or economic implications")
            reasons.append(
                "major public-health risk lane signal (+16): "
                + ", ".join((health_matches + security_matches + risk_context)[:8])
            )
        elif security_matches and risk_context:
            score += 8
            value_signals.append("security/social risk affecting project or market environment")
            reasons.append(f"security/social risk lane signal (+8): {', '.join((security_matches + risk_context)[:8])}")

    if lane == "market_logistics":
        holiday_matches = find_matches(text, PUBLIC_HOLIDAY_TERMS)
        holiday_context = find_matches(
            text,
            [
                "federal government",
                "fg",
                "minister",
                "ministry of interior",
                "declares",
                "declared",
                "nigeria",
                "nationwide",
                "holiday",
                "holidays",
                "eid",
                "sallah",
                "closure",
                "travel",
                "logistics",
                "business",
                "market",
            ],
        )
        matched_keywords.extend(holiday_matches + holiday_context)
        if has_material_public_holiday_event(text):
            score += 20
            value_signals.append("national public-holiday or major festival closure notice")
            reasons.append(
                "public-holiday logistics lane signal (+20): "
                + ", ".join((holiday_matches + holiday_context)[:8])
            )

    if not lane_rules:
        return score, unique_preserving_order(value_signals), unique_preserving_order(matched_keywords), reasons

    if lane == "politics_governance":
        triggers = find_matches(text, lane_rules.get("strong_triggers", []))
        impacts = find_matches(text, lane_rules.get("impact_terms", []))
        presidential_actors = find_matches(text, lane_rules.get("presidential_actors", []))
        itinerary = find_matches(text, lane_rules.get("itinerary_terms", []))
        routine = find_matches(title, lane_rules.get("routine_negative", []))
        matched_keywords.extend(triggers + impacts + presidential_actors + itinerary + routine)

        if presidential_actors and itinerary:
            score += 12
            value_signals.append("presidential itinerary/location signal")
            reasons.append(
                "presidential itinerary/location signal (+12): "
                + ", ".join((presidential_actors + itinerary)[:8])
            )

        if triggers and impacts:
            score += 12
            value_signals.append("politics/governance event with itinerary, policy, fiscal, or project relevance")
            reasons.append(f"politics/governance lane signal (+12): {', '.join((triggers + impacts)[:8])}")
        elif triggers:
            score += 5
            value_signals.append("material politics/governance event")
            reasons.append(f"politics/governance trigger (+5): {', '.join(triggers[:8])}")

        if routine and not impacts and not (presidential_actors and itinerary):
            score -= 6
            reasons.append(f"routine politics downweight (-6): {', '.join(routine[:6])}")

    if lane == "bilateral_multilateral":
        organizations = find_matches(text, lane_rules.get("organizations", []))
        regional = find_matches(text, lane_rules.get("regional_policy_triggers", []))
        economic = find_matches(text, lane_rules.get("economic_triggers", []))
        routine = find_matches(title, lane_rules.get("routine_negative", []))
        matched_keywords.extend(organizations + regional + economic + routine)

        if organizations and regional:
            score += 14
            value_signals.append("regional organization or partner plus cross-border policy change")
            reasons.append(f"regional policy lane signal (+14): {', '.join((organizations + regional)[:8])}")
        elif organizations and economic:
            score += 10
            value_signals.append("bilateral/multilateral cooperation with economic relevance")
            reasons.append(f"bilateral/multilateral economic signal (+10): {', '.join((organizations + economic)[:8])}")
        elif regional:
            score += 7
            value_signals.append("cross-border trade, visa, tariff, or border policy signal")
            reasons.append(f"regional policy trigger (+7): {', '.join(regional[:8])}")

        if routine and not (regional or economic):
            score -= 5
            reasons.append(f"routine diplomacy downweight (-5): {', '.join(routine[:6])}")

    return score, unique_preserving_order(value_signals), unique_preserving_order(matched_keywords), reasons


def classify_selection_lane(article: dict[str, Any], news_type: str) -> str:
    llm_type = str(article.get("llm_prefilter_news_type", ""))
    title = str(article.get("title", ""))
    summary = str(article.get("rss_summary", ""))
    article_body = str(article.get("article_text", ""))
    llm_text = llm_type.casefold()
    type_text = f"{llm_text} {news_type}".casefold()
    full_text = f"{type_text} {title} {summary} {article_body}".casefold()

    politics_terms = [
        "\u653f\u6cbb",
        "\u653f\u5e9c\u6cbb\u7406",
        "\u6cbb\u7406",
        "\u9009\u4e3e",
        "tinubu",
        "president tinubu",
        "president bola tinubu",
        "presidency",
        "presidential",
        "state house",
        "aso rock",
        "election",
        "electoral",
        "2027",
        "peter obi",
        "obi",
        "atiku",
        "jonathan",
        "ndc",
        "apc",
        "pdp",
        "presidential candidate",
        "consensus candidate",
        "sole aspirant",
        "screening",
        "primaries",
        "politics",
        "governance",
        "inec",
    ]
    security_terms = [
        "\u5b89\u5168",
        "\u793e\u4f1a",
        "\u5730\u7f18",
        "security",
        "risk",
        "public health",
        "health alert",
        "ebola",
        "outbreak",
        "epidemic",
        "pandemic",
        "cholera",
        "lassa fever",
    ]
    power_terms = ["\u7535\u529b", "\u65b0\u80fd\u6e90", "power", "renewable"]
    oil_gas_terms = ["\u6cb9\u6c14", "\u80fd\u6e90\u5316\u5de5", "oil", "gas"]
    agriculture_terms = ["\u519c\u4e1a", "\u4ea7\u4e1a\u94fe", "\u5de5\u4e1a", "agriculture", "industrial"]
    finance_terms = [
        "\u878d\u8d44",
        "\u91d1\u878d",
        "\u653f\u7b56",
        "\u76d1\u7ba1",
        "\u8d22\u653f",
        "budget",
        "policy",
        "financing",
        "loan",
        "inflation",
        "interest rate",
        "monetary policy",
        "exchange rate",
        "forex",
        "fx",
        "naira",
        "devaluation",
        "cbn",
        "central bank",
        "debt",
    ]
    bilateral_terms = [
        "\u53cc\u8fb9",
        "\u591a\u8fb9",
        "\u5bf9\u5916",
        "\u533a\u57df",
        "bilateral",
        "multilateral",
        "regional",
        "ecowas",
        "afcfta",
        "visa",
        "border",
        "trade facilitation",
    ]
    project_terms = ["\u57fa\u7840\u8bbe\u65bd", "\u9879\u76ee", "project", "opportunity", "logistics"]

    has_bilateral_anchor = any(
        term in full_text for term in ["ecowas", "afcfta", "african union", "bilateral", "multilateral"]
    )
    has_regional_policy = any(
        term in full_text for term in ["visa", "border", "tariff", "trade facilitation", "free movement"]
    )
    has_presidential_itinerary = any(
        term in full_text
        for term in ["tinubu", "president tinubu", "president bola tinubu", "state house", "aso rock"]
    ) and any(
        term in full_text
        for term in [
            "visit",
            "meeting",
            "meets",
            "met with",
            "delegation",
            "sidelines",
            "forum",
            "summit",
            "state visit",
            "official visit",
            "working visit",
        ]
    )
    has_material_election = any(
        term in full_text
        for term in [
            "2027",
            "election",
            "elections",
            "presidential candidate",
            "consensus candidate",
            "sole aspirant",
            "screening",
            "primaries",
        ]
    ) and any(
        term in full_text
        for term in ["peter obi", "atiku", "jonathan", "tinubu", "ndc", "apc", "pdp", "opposition", "coalition"]
    )
    has_public_health_shock = has_material_public_health_event(full_text)
    has_macro_economic_shock = has_material_macro_economic_event(full_text)
    has_public_holiday = has_material_public_holiday_event(full_text)
    has_governance_shock = has_material_governance_shock(full_text)

    if any(term in llm_text for term in bilateral_terms) or (has_bilateral_anchor and has_regional_policy):
        return "bilateral_multilateral"
    if any(term in type_text for term in politics_terms):
        return "politics_governance"
    if has_presidential_itinerary:
        return "politics_governance"
    if has_material_election:
        return "politics_governance"
    if has_governance_shock:
        return "politics_governance"
    if has_public_health_shock:
        return "security_risk"
    if has_public_holiday:
        return "market_logistics"
    if any(term in type_text for term in security_terms):
        return "security_risk"
    if any(term in type_text for term in power_terms):
        return "power_energy"
    if any(term in type_text for term in oil_gas_terms):
        return "oil_gas"
    if any(term in type_text for term in agriculture_terms):
        return "agriculture_industry"
    if has_macro_economic_shock:
        return "finance_policy"
    if any(term in type_text for term in finance_terms):
        return "finance_policy"
    if any(term in type_text for term in project_terms):
        return "project"
    if any(term in full_text for term in ["election", "electoral", "inec"]) and any(
        term in full_text for term in ["policy", "budget", "subsidy", "tax", "reform", "project", "infrastructure"]
    ):
        return "politics_governance"
    return "other"


def detect_value_signals(
    text: str,
    title: str,
    entities: dict[str, list[str]],
    rules: dict[str, Any],
    lane: str,
) -> list[str]:
    signals: list[str] = []
    project_domains = rules["sector_keywords"] + [
        "hospital",
        "health infrastructure",
        "medical equipment",
        "waste management",
        "recycling",
        "digital infrastructure",
        "data centre",
        "industrial park",
        "housing",
        "hotel",
        "pipeline",
        "transmission",
        "mini grid",
        "CNG",
    ]
    project_actions = rules["project_signals"] + [
        "flagged off",
        "commence",
        "commences",
        "starts",
        "resumes",
        "suspended",
        "halted",
    ]
    project_risks = [
        "protest",
        "protests",
        "blocked",
        "close road",
        "compensation",
        "right of way",
        "land acquisition",
        "community",
        "residents",
        "stalled",
        "delayed",
        "suspended",
        "vandalism",
    ]
    public_health_terms = MAJOR_PUBLIC_HEALTH_TERMS + PUBLIC_HEALTH_CONTEXT_TERMS
    finance_terms = rules["news_types"]["financing_budget"] + [
        "tax",
        "tariff",
        "subsidy",
        "customs",
        "facility",
        "grant",
        "interest rate",
        "monetary policy",
        "exchange rate",
        "forex",
        "FX",
        "naira",
        "devaluation",
        "CBN",
        "Central Bank",
    ]
    policy_terms = rules["news_types"]["policy_regulation"] + [
        "market reform",
        "electricity market",
        "procurement",
        "single window",
    ]
    political_actors = [
        "Tinubu",
        "president",
        "presidency",
        "State House",
        "Aso Rock",
        "FEC",
        "minister",
        "governor",
        "National Assembly",
        "senate",
        "2027",
        "INEC",
        "Peter Obi",
        "Obi",
        "Atiku",
        "Jonathan",
        "NDC",
        "APC",
        "PDP",
        "Labour Party",
    ]
    market_impact_terms = [
        "election",
        "elections",
        "presidential candidate",
        "consensus candidate",
        "sole aspirant",
        "screening",
        "primaries",
        "primary",
        "opposition",
        "coalition",
        "visit",
        "meeting",
        "delegation",
        "summit",
        "forum",
        "conference",
        "arrives",
        "returns",
        "departs",
        "budget",
        "inflation",
        "interest rate",
        "monetary policy",
        "exchange rate",
        "forex",
        "FX",
        "naira",
        "devaluation",
        "CBN",
        "Central Bank",
        "debt service",
        "fuel subsidy",
        "project",
        "infrastructure",
        "investment",
        "reform",
        "security",
        "loan",
        "approval",
        "energy",
        "power",
        "oil",
        "gas",
    ]
    external_terms = [
        "China",
        "Chinese",
        "Beijing",
        "EU",
        "UK",
        "US",
        "France",
        "Saudi",
        "ECOWAS",
        "World Bank",
        "AfDB",
        "IFC",
        "investment",
        "loan",
        "infrastructure",
        "energy",
        "trade",
        "finance",
        "corridor",
    ]

    has_domain = bool(find_matches(text, project_domains))
    has_action = bool(find_matches(text, project_actions))
    has_actor = bool(entities["agencies"] or entities["companies"] or entities["financiers"])
    has_scale = bool(entities["amounts"] or entities["distances"] or entities["capacities"])
    has_risk = bool(find_matches(text, project_risks))
    has_finance = bool(find_matches(text, finance_terms) or entities["financiers"])
    has_policy = bool(find_matches(text, policy_terms))
    has_political_actor = bool(find_matches(text, political_actors))
    has_market_impact = bool(find_matches(text, market_impact_terms))
    has_external = bool(find_matches(text, external_terms))
    has_public_health = has_material_public_health_event(text)
    has_macro_economic = has_material_macro_economic_event(text)
    has_governance_shock = has_material_governance_shock(text)

    if lane in {"project", "power_energy", "oil_gas", "agriculture_industry"}:
        if has_domain and has_action:
            signals.append("project domain plus concrete action")
        if has_domain and has_scale:
            signals.append("project domain plus measurable scale")
        if has_domain and has_actor:
            signals.append("project domain plus agency/company/financier")
        if has_domain and has_risk:
            signals.append("project domain plus execution risk")

    if lane == "finance_policy":
        if has_finance and (has_scale or has_actor):
            signals.append("finance/fiscal signal plus amount or institution")
        if has_policy and has_market_impact:
            signals.append("policy/regulatory signal with market impact")
        if has_macro_economic:
            signals.append("macro-economic signal affecting market conditions")

    if lane == "politics_governance":
        if has_political_actor and has_market_impact:
            signals.append("political actor plus itinerary/policy/project/investment relevance")
        if has_governance_shock:
            signals.append("major governance or election shock")
        if find_matches(text, ["2027", "election", "elections"]) and find_matches(
            text,
            [
                "Peter Obi",
                "Obi",
                "Atiku",
                "Jonathan",
                "NDC",
                "APC",
                "PDP",
                "presidential candidate",
                "consensus candidate",
                "screening",
                "primaries",
            ],
        ):
            signals.append("material 2027 election/candidate development")

    if lane == "security_risk":
        if has_risk and (has_domain or has_market_impact):
            signals.append("security/social risk affecting project or market environment")
        if has_public_health and find_matches(text, public_health_terms):
            signals.append("public-health risk with national or economic implications")

    if lane == "bilateral_multilateral":
        if has_external and has_market_impact:
            signals.append("external cooperation with investment/project/policy relevance")

    if not signals and has_scale and has_actor:
        signals.append("amount/scale plus credible actor")

    return unique_preserving_order(signals)


def classify_news_type(text: str, rules: dict[str, Any]) -> str:
    scores: dict[str, int] = {}
    for news_type, keywords in rules["news_types"].items():
        scores[news_type] = len(find_matches(text, keywords))
    best_type, best_score = max(scores.items(), key=lambda item: item[1])
    return best_type if best_score > 0 else "irrelevant"


def classify_project_stage(text: str, rules: dict[str, Any]) -> str:
    for stage, keywords in rules["project_stages"].items():
        if find_matches(text, keywords):
            return stage
    return "unknown"


def find_matches(text: str, keywords: list[str]) -> list[str]:
    matches: list[str] = []
    for keyword in keywords:
        keyword_text = str(keyword)
        if keyword_matches(text, keyword_text):
            matches.append(keyword_text)
    return matches


def keyword_matches(text: str, keyword: str) -> bool:
    pattern = r"(?<![A-Za-z0-9])" + re.escape(keyword) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def reason(label: str, matches: list[str], weight: int, applied_score: int) -> str:
    signed_weight = f"+{weight}" if weight > 0 else str(weight)
    return f"{label} ({signed_weight} each, applied {applied_score}): {', '.join(matches)}"


def level_for_score(score: int, thresholds: dict[str, int]) -> str:
    if score >= thresholds["high"]:
        return "high"
    if score >= thresholds["medium"]:
        return "medium"
    if score >= thresholds["low"]:
        return "low"
    return "below_threshold"


def build_watch_reason(
    news_type: str,
    project_stage: str,
    entities: dict[str, list[str]],
    score: int,
) -> str:
    parts = [f"classified as {news_type}"]
    if project_stage != "unknown":
        parts.append(f"project stage appears to be {project_stage}")
    if entities["financiers"]:
        parts.append(f"financier signal: {', '.join(entities['financiers'][:3])}")
    if entities["agencies"]:
        parts.append(f"agency signal: {', '.join(entities['agencies'][:3])}")
    if entities["companies"]:
        parts.append(f"company signal: {', '.join(entities['companies'][:3])}")
    if entities["amounts"]:
        parts.append(f"amount signal: {', '.join(entities['amounts'][:3])}")
    parts.append(f"score {score}")
    return "; ".join(parts) + "."


def evidence_snippets(text: str, terms: list[str], max_snippets: int = 5) -> list[str]:
    snippets: list[str] = []
    compact_text = re.sub(r"\s+", " ", text)
    for term in terms:
        if not term:
            continue
        match = re.search(re.escape(term), compact_text, re.IGNORECASE)
        if not match:
            continue
        start = max(match.start() - 100, 0)
        end = min(match.end() + 160, len(compact_text))
        snippets.append(compact_text[start:end].strip())
        if len(snippets) >= max_snippets:
            break
    return snippets


def scored_terms(matched_keywords: list[str], entities: dict[str, list[str]]) -> list[str]:
    terms = list(matched_keywords)
    for values in entities.values():
        terms.extend(values)
    return unique_preserving_order(terms)


def unique_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        normalized = item.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_items.append(item)
    return unique_items
