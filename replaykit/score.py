"""Score a replayed run against an incident's injected truth."""


def score(incident, run):
    """Return the score dict for *run* (a replay.RunResult) on *incident*."""
    truth = incident.truth
    diagnosis = run.diagnosis
    diagnosed = diagnosis is not None
    service_correct = diagnosed and diagnosis.service == truth.service
    type_correct = diagnosed and diagnosis.fault_type == truth.fault_type
    root_cause_correct = service_correct and type_correct

    evidence_valid = False
    evidence_precision = 0.0
    delay = None
    points = 0.0

    diagnosed_at = run.diagnosed_at
    if diagnosed and diagnosed_at is not None:
        times = {event["id"]: event["t"] for event in incident.events}
        cited = tuple(diagnosis.evidence)
        evidence_valid = bool(cited) and all(
            event_id in times and times[event_id] <= diagnosed_at for event_id in cited
        )
        if cited:
            hits = sum(1 for event_id in cited if event_id in truth.injected_ids)
            evidence_precision = round(hits / len(cited), 4)
        delay = diagnosed_at - truth.start
        if evidence_valid:
            span = incident.duration - truth.start
            timeliness = max(0.0, 1 - delay / span) if delay >= 0 else 0.0
            points = round(
                50 * service_correct + 30 * type_correct + 20 * timeliness * root_cause_correct,
                2,
            )

    return {
        "diagnosed": diagnosed,
        "service_correct": service_correct,
        "type_correct": type_correct,
        "root_cause_correct": root_cause_correct,
        "evidence_valid": evidence_valid,
        "evidence_precision": evidence_precision,
        "delay": delay,
        "points": points,
    }


def noise_score(incident, run):
    """Return decoy and noise diagnostics for *run* on *incident*."""
    truth = incident.truth
    diagnosis = run.diagnosis
    if diagnosis is None:
        return {"decoy_blamed": False, "noise_cited": 0}
    decoy_service = getattr(truth, "decoy_service", None)
    noise_ids = getattr(truth, "noise_ids", frozenset())
    return {
        "decoy_blamed": decoy_service is not None and diagnosis.service == decoy_service,
        "noise_cited": sum(1 for event_id in diagnosis.evidence if event_id in noise_ids),
    }


def brier(pairs):
    """Mean squared error of confidences against outcomes; None when empty."""
    pairs = list(pairs)
    if not pairs:
        return None
    total = sum((confidence - (1.0 if correct else 0.0)) ** 2 for confidence, correct in pairs)
    return round(total / len(pairs), 4)
