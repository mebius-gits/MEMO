"""Editable rule thresholds. Both the API and the MQTT worker read them from the database on
every reading, so a change applies without restarting either process."""

from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import select

from memo.domain.entities import NotFound
from memo.domain.environment import RULES, RULES_BY_ID, Rule, configured, rule_problem
from memo.infrastructure.database import EventStateRow, RuleConfigRow
from memo.infrastructure.environment_events import cancel_undelivered, log_update


class InvalidRule(ValueError):
    pass


def thresholds(rule: Rule) -> dict:
    return {
        "trigger": rule.trigger,
        "recovery": rule.recovery,
        "hold_seconds": rule.hold,
        "recover_hold_seconds": rule.recover_hold,
        "levels": [{"threshold": t, "level": name} for t, name in rule.levels],
    }


def rule_view(rule: Rule, config: RuleConfigRow | None) -> dict:
    current = configured(rule, config.override if config else None)
    return {
        "id": rule.id,
        "metric": rule.metric,
        "direction": "rising" if rule.window else rule.direction,
        "window_seconds": rule.window or None,
        "enabled": config.enabled if config else True,
        "version": config.version if config else 1,
        "customized": config is not None,
        "updated_at": config.updated_at if config else None,
        **thresholds(current),
        "defaults": thresholds(rule),
    }


def reset_windows(state: dict, enabled: bool) -> tuple[dict, str | None]:
    """New thresholds restart unconfirmed holds and trend windows. A live event keeps its id
    and the rule version it started under; disabling a rule ends it without a notice."""
    state = dict(state)
    state.update(since=None, samples=[])
    if not state.get("live"):
        state["status"] = "normal"
        return state, None
    if not enabled:
        state.update(status="normal", live=False, level=None, notified_level=None)
        return state, "rule_disabled"
    if state.get("status") == "recovering":
        state["status"] = "active"
    return state, "rule_changed"


class RuleRepository:
    def __init__(self, session):
        self.session = session

    def list(self) -> list[dict]:
        configs = {row.rule_id: row for row in self.session.scalars(select(RuleConfigRow))}
        return [rule_view(rule, configs.get(rule.id)) for rule in RULES]

    def get(self, rule_id) -> dict:
        rule = RULES_BY_ID.get(rule_id)
        if rule is None:
            raise NotFound("Rule not found")
        return rule_view(rule, self.session.get(RuleConfigRow, rule_id))

    def update(self, rule_id, enabled: bool, override: dict) -> dict:
        rule = RULES_BY_ID.get(rule_id)
        if rule is None:
            raise NotFound("Rule not found")
        candidate = configured(rule, override)
        if problem := rule_problem(candidate):
            raise InvalidRule(problem)
        now = datetime.now(timezone.utc)
        config = self.session.get(RuleConfigRow, rule_id)
        stored = {
            key: value
            for key, value in asdict(candidate).items()
            if key in ("trigger", "recovery", "hold", "recover_hold", "levels")
        }
        if config is None:
            config = RuleConfigRow(rule_id=rule_id, version=1)
            self.session.add(config)
        config.enabled, config.override, config.updated_at = enabled, stored, now
        config.version += 1
        rows = self.session.scalars(
            select(EventStateRow).where(EventStateRow.rule_id == rule_id).with_for_update()
        ).all()
        for row in rows:
            state, kind = reset_windows(row.state, enabled)
            if kind == "rule_disabled":
                state["version"] = state.get("version", 0) + 1
                cancel_undelivered(self.session, row.device_id, rule_id)
            if kind:
                log_update(
                    self.session,
                    row.device_id,
                    rule_id,
                    state,
                    kind,
                    now,
                    {"rule_version": config.version},
                )
            row.state = state
        self.session.commit()
        return rule_view(rule, config)
