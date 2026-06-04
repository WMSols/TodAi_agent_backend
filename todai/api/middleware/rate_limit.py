"""
rate_limit.py — Groq free-tier limits (org-wide) + per-turn stats for the UI

Minute RPM/TPM use **wall-clock minutes** (reset at :00) so the header matches a new minute.
Day RPD/TPD use rolling 24h windows.

Defaults: RPM 30/min, TPM 6000/min, RPD 14400/day, TPD 500000/day
Env: GROQ_RPM_LIMIT, GROQ_TPM_LIMIT, GROQ_RPD_LIMIT, GROQ_TPD_LIMIT
"""

from __future__ import annotations

import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Any

_turn_calls: ContextVar[list[dict[str, Any]] | None] = ContextVar("groq_turn_calls", default=None)
_turn_user_id: ContextVar[str] = ContextVar("groq_turn_user_id", default="default")

_DAY_SEC = 86400.0
_DEFAULT_REQUESTS_PER_TURN = 2
_DEFAULT_TOKENS_PER_TURN = 2500
# Groq Retry-After can be large (e.g. 117s); cap cooldown for UX and pre-flight blocks.
_GROQ_COOLDOWN_CAP_SEC = 60.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def groq_limits() -> dict[str, int]:
    return {
        "rpm": _env_int("GROQ_RPM_LIMIT", 30),
        "tpm": _env_int("GROQ_TPM_LIMIT", 6000),
        "rpd": _env_int("GROQ_RPD_LIMIT", 14400),
        "tpd": _env_int("GROQ_TPD_LIMIT", 500_000),
    }


def groq_rpm_limit() -> int:
    return groq_limits()["rpm"]


def _current_minute_id() -> int:
    return int(time.time() // 60)


def _seconds_until_next_minute() -> float:
    return max(0.0, 60.0 - (time.time() % 60.0))


def _cap_groq_retry(seconds: float) -> float:
    return max(1.0, min(float(seconds), _GROQ_COOLDOWN_CAP_SEC))


@dataclass
class TurnAllowance:
    allowed: bool
    retry_after_seconds: float = 0.0
    limit_hit: str | None = None  # rpm | tpm | rpd | tpd
    message: str = ""

    def to_usage_extra(self) -> dict[str, Any]:
        return {
            "rate_limited": not self.allowed,
            "retry_after_seconds": round(self.retry_after_seconds, 1) if self.retry_after_seconds else 0,
            "limit_hit": self.limit_hit,
        }


class GroqRateTracker:
    """Org-wide counters (Groq caps all API keys together)."""

    def __init__(self) -> None:
        self._lock = Lock()
        # Wall-clock minute (display + enforcement for RPM/TPM)
        self._minute_id = _current_minute_id()
        self._minute_rpm = 0
        self._minute_tpm = 0
        # Rolling 24h for day limits
        self._day_requests: list[float] = []
        self._day_tokens: list[tuple[float, int]] = []
        self._last_turn: dict[str, dict[str, Any]] = {}
        self._last_block: dict[str, Any] = {}  # external=True when Groq returned 429

    def begin_turn(self, user_id: str) -> None:
        _turn_user_id.set(user_id)
        _turn_calls.set([])

    def _roll_minute_if_needed(self) -> None:
        mid = _current_minute_id()
        if mid != self._minute_id:
            self._minute_id = mid
            self._minute_rpm = 0
            self._minute_tpm = 0

    @staticmethod
    def _prune_day_ts(events: list[float], now: float) -> list[float]:
        return [t for t in events if now - t < _DAY_SEC]

    @staticmethod
    def _prune_day_tokens(pairs: list[tuple[float, int]], now: float) -> list[tuple[float, int]]:
        return [(t, n) for t, n in pairs if now - t < _DAY_SEC]

    def _minute_counts(self) -> tuple[int, int]:
        with self._lock:
            self._roll_minute_if_needed()
            return self._minute_rpm, self._minute_tpm

    def _day_counts(self) -> tuple[int, int]:
        now = time.monotonic()
        with self._lock:
            self._day_requests = self._prune_day_ts(self._day_requests, now)
            self._day_tokens = self._prune_day_tokens(self._day_tokens, now)
            return len(self._day_requests), sum(n for _, n in self._day_tokens)

    def _snapshot_counts(self) -> dict[str, Any]:
        rpm_used, tpm_used = self._minute_counts()
        rpd_used, tpd_used = self._day_counts()
        return {
            "rpm_used": rpm_used,
            "tpm_used": tpm_used,
            "rpd_used": rpd_used,
            "tpd_used": tpd_used,
            "minute_id": _current_minute_id(),
            "minute_resets_in_sec": round(_seconds_until_next_minute(), 1),
        }

    def _wait_for_minute_reset(self) -> float:
        return max(1.0, round(_seconds_until_next_minute(), 1))

    def _external_block_allowance(self) -> TurnAllowance | None:
        """Groq returned 429 — block new HTTP calls until cooldown expires."""
        with self._lock:
            block = self._last_block
        if not block.get("external"):
            return None
        until = float(block.get("until") or 0)
        remaining = until - time.time()
        if remaining <= 0:
            return None
        wait = max(1.0, round(remaining, 1))
        hit = str(block.get("limit_hit") or "rpm")
        return TurnAllowance(
            False,
            wait,
            hit,
            f"Groq rate limit (429). Wait about {int(min(wait, _GROQ_COOLDOWN_CAP_SEC))}s and try again.",
        )

    def check_turn_allowed(
        self,
        *,
        planned_requests: int = _DEFAULT_REQUESTS_PER_TURN,
        planned_tokens: int = _DEFAULT_TOKENS_PER_TURN,
    ) -> TurnAllowance:
        external = self._external_block_allowance()
        if external is not None:
            return external
        limits = groq_limits()
        counts = self._snapshot_counts()
        rpm_after = counts["rpm_used"] + planned_requests
        tpm_after = counts["tpm_used"] + planned_tokens
        rpd_after = counts["rpd_used"] + planned_requests
        tpd_after = counts["tpd_used"] + planned_tokens
        wait_min = self._wait_for_minute_reset()

        if rpm_after > limits["rpm"]:
            return TurnAllowance(
                False,
                wait_min,
                "rpm",
                f"Groq limit: {limits['rpm']} requests this minute. New minute in {wait_min:.0f}s.",
            )
        if tpm_after > limits["tpm"]:
            return TurnAllowance(
                False,
                wait_min,
                "tpm",
                f"Groq limit: {limits['tpm']:,} tokens this minute. New minute in {wait_min:.0f}s.",
            )
        if rpd_after > limits["rpd"]:
            return TurnAllowance(
                False,
                60.0,
                "rpd",
                f"Groq daily request limit ({limits['rpd']:,}). Try again later.",
            )
        if tpd_after > limits["tpd"]:
            return TurnAllowance(
                False,
                60.0,
                "tpd",
                f"Groq daily token limit ({limits['tpd']:,}). Try again later.",
            )
        return TurnAllowance(True)

    def check_single_request(self) -> TurnAllowance:
        return self.check_turn_allowed(planned_requests=1, planned_tokens=800)

    def record(
        self,
        user_id: str,
        *,
        phase: str,
        status: int | None,
        ok: bool,
        tokens: int = 0,
        skipped: bool = False,
    ) -> None:
        entry: dict[str, Any] = {
            "phase": phase,
            "status": status,
            "ok": ok,
            "skipped": skipped,
            "tokens": tokens,
        }
        turn = _turn_calls.get()
        if turn is not None:
            turn.append(entry)

        if skipped:
            return

        now = time.monotonic()
        with self._lock:
            self._roll_minute_if_needed()
            self._minute_rpm += 1
            if tokens > 0:
                self._minute_tpm += tokens
            self._day_requests = self._prune_day_ts(self._day_requests, now)
            self._day_tokens = self._prune_day_tokens(self._day_tokens, now)
            self._day_requests.append(now)
            if tokens > 0:
                self._day_tokens.append((now, tokens))

    def set_external_retry(self, retry_after_seconds: float, limit_hit: str = "rpm") -> None:
        wait = _cap_groq_retry(retry_after_seconds)
        with self._lock:
            self._last_block = {
                "retry_after_seconds": wait,
                "limit_hit": limit_hit,
                "external": True,
                "until": time.time() + wait,
            }

    def _clear_block_if_under_limits(self, limits: dict[str, int], counts: dict[str, Any]) -> None:
        block = self._last_block
        if block.get("external"):
            until = float(block.get("until") or 0)
            if until > time.time():
                return
        over = (
            counts["rpm_used"] >= limits["rpm"]
            or counts["tpm_used"] >= limits["tpm"]
            or counts["rpd_used"] >= limits["rpd"]
            or counts["tpd_used"] >= limits["tpd"]
        )
        if not over:
            self._last_block = {}

    def mark_preflight_only_turn(self, user_id: str) -> None:
        """Current turn used rules/local only — do not show prior turn's Groq phases in UI."""
        with self._lock:
            self._last_turn[user_id] = {
                "turn_requests": 0,
                "turn_phases": [],
                "turn_tokens": 0,
                "preflight_only": True,
            }

    def _turn_stats(self, user_id: str) -> tuple[int, list[str], int]:
        with self._lock:
            if (self._last_turn.get(user_id) or {}).get("preflight_only"):
                return 0, [], 0
        turn = _turn_calls.get() or []
        http_calls = [c for c in turn if not c.get("skipped")]
        tokens_turn = sum(int(c.get("tokens") or 0) for c in http_calls)
        if http_calls:
            count = len(http_calls)
            phases = [str(c.get("phase", "")) for c in http_calls]
            with self._lock:
                self._last_turn[user_id] = {
                    "turn_requests": count,
                    "turn_phases": phases,
                    "turn_tokens": tokens_turn,
                }
            return count, phases, tokens_turn
        # No Groq HTTP this turn — report zero (not a previous turn's failed router).
        with self._lock:
            self._last_turn[user_id] = {
                "turn_requests": 0,
                "turn_phases": [],
                "turn_tokens": 0,
            }
        return 0, [], 0

    def usage_snapshot(self, user_id: str) -> dict[str, Any]:
        limits = groq_limits()
        counts = self._snapshot_counts()
        turn_requests, turn_phases, turn_tokens = self._turn_stats(user_id)
        tpm_used = counts["tpm_used"]
        tpm_limit = limits["tpm"]
        with self._lock:
            self._clear_block_if_under_limits(limits, counts)
            block = dict(self._last_block)
        out: dict[str, Any] = {
            "turn_requests": turn_requests,
            "turn_phases": turn_phases,
            "turn_tokens": turn_tokens,
            "rpm_used": counts["rpm_used"],
            "rpm_limit": limits["rpm"],
            "rpm_remaining": max(0, limits["rpm"] - counts["rpm_used"]),
            "tpm_used": tpm_used,
            "tpm_limit": tpm_limit,
            "tpm_remaining": max(0, tpm_limit - tpm_used),
            "tpm_over_limit": tpm_used > tpm_limit,
            "rpd_used": counts["rpd_used"],
            "rpd_limit": limits["rpd"],
            "rpd_remaining": max(0, limits["rpd"] - counts["rpd_used"]),
            "tpd_used": counts["tpd_used"],
            "tpd_limit": limits["tpd"],
            "tpd_remaining": max(0, limits["tpd"] - counts["tpd_used"]),
            "minute_resets_in_sec": counts["minute_resets_in_sec"],
            "window_seconds": 60,
            "rate_limited": False,
            "retry_after_seconds": 0,
            "limit_hit": None,
        }
        with self._lock:
            last_turn = dict(self._last_turn.get(user_id) or {})
        if last_turn.get("preflight_only"):
            out["preflight_only"] = True
        if turn_requests == 0 and (
            last_turn.get("preflight_only") or block.get("external")
        ):
            out["turn_groq_skipped"] = True
        if block.get("external"):
            until = float(block.get("until") or 0)
            remaining = until - time.time()
            if remaining > 0:
                out["rate_limited"] = True
                out["retry_after_seconds"] = max(1.0, round(remaining, 1))
                out["limit_hit"] = block.get("limit_hit")
                if turn_requests == 0:
                    out["cooldown_from_prior"] = True
        elif block.get("retry_after_seconds"):
            out["rate_limited"] = True
            out["retry_after_seconds"] = max(block["retry_after_seconds"], counts["minute_resets_in_sec"])
            out["limit_hit"] = block.get("limit_hit")
        return out


groq_tracker = GroqRateTracker()


def current_turn_user_id() -> str:
    return _turn_user_id.get()


def rate_limit_user_message(check: TurnAllowance, usage: dict[str, Any]) -> str:
    wait = int(check.retry_after_seconds) if check.retry_after_seconds else 5
    hit = (check.limit_hit or "rpm").upper()
    return (
        f"{check.message or 'Rate limit reached.'} "
        f"Please wait about {wait} seconds before sending another message. ({hit})"
    )
