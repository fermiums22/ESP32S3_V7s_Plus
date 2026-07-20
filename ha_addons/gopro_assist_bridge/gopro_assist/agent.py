"""Cost-bounded OpenAI agent with Home Assistant tools."""

from __future__ import annotations

import base64
from collections import deque
from contextlib import closing
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

from .memory import WorldMemory


LOGGER = logging.getLogger("gopro_assist.agent")
RESPONSES_URL = "https://api.openai.com/v1/responses"
USAGE_PATH = Path("/data/agent_usage.json")
HISTORY_PATH = Path("/data/agent_history.json")
WORLD_DB_PATH = Path("/data/robot_world.db")


@dataclass(frozen=True)
class AgentConfig:
    api_key: str
    model: str
    system_prompt: str
    max_output_tokens: int
    history_turns: int
    max_tool_rounds: int
    daily_limit_usd: float
    monthly_limit_usd: float
    request_reserve_usd: float
    input_usd_per_million: float
    cached_input_usd_per_million: float
    output_usd_per_million: float
    camera_entity: str
    home_map_entity: str
    telemetry_entities: tuple[str, ...]


class BudgetExceeded(RuntimeError):
    pass


class SemanticEventJournal:
    """Convert raw HA state changes to compact robot events."""

    def __init__(self, max_events: int = 100, path: Path | None = None) -> None:
        self.events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self.sequence = 0
        self.last_frame_sequence = 0
        self.robot_state = "unknown"
        self.path = path
        if path is not None:
            self._initialize_database()
            self._load_recent(max_events)

    def _connect(self) -> sqlite3.Connection:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize_database(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS robot_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS robot_events_at
                    ON robot_events(occurred_at);
                CREATE TABLE IF NOT EXISTS world_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def _load_recent(self, max_events: int) -> None:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT seq, occurred_at, event, payload_json FROM robot_events "
                "ORDER BY seq DESC LIMIT ?", (max_events,)
            ).fetchall()
            state_row = connection.execute(
                "SELECT value_json FROM world_state WHERE key='robot_state'"
            ).fetchone()
        for seq, occurred_at, event_type, payload_json in reversed(rows):
            try:
                details = json.loads(payload_json)
            except json.JSONDecodeError:
                details = {}
            self.events.append({"seq": seq, "at": occurred_at,
                                "event": event_type, **details})
            self.sequence = max(self.sequence, int(seq))
        self.last_frame_sequence = self.sequence
        if state_row:
            try:
                self.robot_state = str(json.loads(state_row[0]).get("state", "unknown"))
            except (json.JSONDecodeError, AttributeError):
                pass

    def _persist_event(
        self, occurred_at: str, event_type: str, details: dict[str, Any]
    ) -> int:
        if self.path is None:
            return self.sequence + 1
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO robot_events(occurred_at, event, payload_json) VALUES(?,?,?)",
                (occurred_at, event_type,
                 json.dumps(details, ensure_ascii=False, separators=(",", ":"))),
            )
            connection.execute(
                "DELETE FROM robot_events WHERE seq <= "
                "(SELECT MAX(seq) - 10000 FROM robot_events)"
            )
            return int(cursor.lastrowid)

    def _persist_state(self, key: str, value: dict[str, Any], occurred_at: str) -> None:
        if self.path is None:
            return
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                 occurred_at),
            )

    def record(
        self, entity_id: str, old_state: str, new_state: str, occurred_at: str
    ) -> None:
        if old_state == new_state:
            return
        event_type = ""
        details: dict[str, Any] = {}
        if entity_id.endswith("_robot_state"):
            self.robot_state = new_state
            self._persist_state("robot_state", {"state": new_state}, occurred_at)
            event_type = {
                "Running": "motion_started", "Paused": "motion_paused",
                "Bumper stop": "motion_stopped_by_collision",
                "Emergency stop": "emergency_stop", "Motor fault": "motor_fault",
                "Docking": "docking_started", "Docked": "docked",
            }.get(new_state, "robot_state_changed")
            details["state"] = new_state
        elif new_state == "on" and entity_id.endswith("_left_bumper_pressed"):
            event_type, details = "collision", {"side": "left", "while": self.robot_state}
        elif new_state == "on" and entity_id.endswith("_right_bumper_pressed"):
            event_type, details = "collision", {"side": "right", "while": self.robot_state}
        elif new_state == "on" and entity_id.endswith("_left_bumper_latched"):
            event_type, details = "collision_latched", {"side": "left"}
        elif new_state == "on" and entity_id.endswith("_right_bumper_latched"):
            event_type, details = "collision_latched", {"side": "right"}
        elif new_state == "on" and entity_id.endswith("_emergency_stop"):
            event_type = "emergency_stop"
        elif new_state == "on" and entity_id.endswith("_left_motor_fault"):
            event_type, details = "motor_fault", {"side": "left"}
        elif new_state == "on" and entity_id.endswith("_right_motor_fault"):
            event_type, details = "motor_fault", {"side": "right"}
        elif entity_id.endswith("_robot_docked"):
            event_type = "docked" if new_state == "on" else "left_dock"
        elif entity_id.endswith("_proximity_event"):
            parts = new_state.split(";")
            motion, _, zones = parts[0].partition(":")
            event_type = {
                "approaching": "object_approaching",
                "receding": "object_receding",
                "stopped": "object_motion_ended",
            }.get(motion, "proximity_changed")
            details = {"zones": [zone for zone in zones.split(",") if zone]}
            for part in parts[1:]:
                key, separator, value = part.partition("=")
                if separator and key in {"strength", "seq"}:
                    try:
                        details[key] = int(value)
                    except ValueError:
                        pass
        if not event_type:
            return
        self.sequence = self._persist_event(occurred_at, event_type, details)
        self.events.append({"seq": self.sequence, "at": occurred_at,
                            "event": event_type, **details})

    def recent(self, limit: int = 20) -> str:
        return json.dumps(list(self.events)[-max(1, min(limit, 50)):],
                          ensure_ascii=False, separators=(",", ":"))

    def since_last_frame(self) -> str:
        events = [item for item in self.events
                  if int(item["seq"]) > self.last_frame_sequence]
        self.last_frame_sequence = self.sequence
        return json.dumps(events, ensure_ascii=False, separators=(",", ":"))


class UsageLedger:
    def __init__(self, path: Path = USAGE_PATH) -> None:
        self.path = path
        self.data = self._load()

    @staticmethod
    def _periods() -> tuple[str, str]:
        now = datetime.now(UTC)
        return now.date().isoformat(), now.strftime("%Y-%m")

    def _empty(self) -> dict[str, Any]:
        day, month = self._periods()
        return {
            "day": day,
            "month": month,
            "today": self._counters(),
            "this_month": self._counters(),
            "lifetime": self._counters(),
        }

    @staticmethod
    def _counters() -> dict[str, int | float]:
        return {"requests": 0, "input_tokens": 0, "cached_tokens": 0,
                "output_tokens": 0, "cost_usd": 0.0}

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return self._empty()

    def _roll_periods(self) -> None:
        day, month = self._periods()
        if self.data.get("month") != month:
            self.data["month"] = month
            self.data["this_month"] = self._counters()
        if self.data.get("day") != day:
            self.data["day"] = day
            self.data["today"] = self._counters()

    def ensure_allowed(self, config: AgentConfig) -> None:
        self._roll_periods()
        reserve = config.request_reserve_usd
        if config.daily_limit_usd > 0 and (
            float(self.data["today"]["cost_usd"]) + reserve
            > config.daily_limit_usd
        ):
            raise BudgetExceeded("daily OpenAI budget reached")
        if config.monthly_limit_usd > 0 and (
            float(self.data["this_month"]["cost_usd"]) + reserve
            > config.monthly_limit_usd
        ):
            raise BudgetExceeded("monthly OpenAI budget reached")

    def add(self, usage: dict[str, Any], config: AgentConfig) -> None:
        self._roll_periods()
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        details = usage.get("input_tokens_details") or {}
        cached_tokens = min(input_tokens, int(details.get("cached_tokens", 0)))
        uncached_tokens = input_tokens - cached_tokens
        cost = (
            uncached_tokens * config.input_usd_per_million
            + cached_tokens * config.cached_input_usd_per_million
            + output_tokens * config.output_usd_per_million
        ) / 1_000_000
        for period in ("today", "this_month", "lifetime"):
            counters = self.data[period]
            counters["requests"] += 1
            counters["input_tokens"] += input_tokens
            counters["cached_tokens"] += cached_tokens
            counters["output_tokens"] += output_tokens
            counters["cost_usd"] = round(float(counters["cost_usd"]) + cost, 8)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")


class HomeAssistantTools:
    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")

    async def search_entities(self, query: str, domain: str = "") -> str:
        async with self.session.get(f"{self.base_url}/states") as response:
            response.raise_for_status()
            states = await response.json()
        needle = query.casefold().strip()
        domain_prefix = f"{domain}." if domain else ""
        matches: list[dict[str, Any]] = []
        for item in states:
            entity_id = str(item.get("entity_id", ""))
            name = str((item.get("attributes") or {}).get("friendly_name", ""))
            if domain_prefix and not entity_id.startswith(domain_prefix):
                continue
            if needle and needle not in f"{entity_id} {name}".casefold():
                continue
            matches.append({"entity_id": entity_id, "state": item.get("state"), "name": name})
            if len(matches) >= 30:
                break
        return json.dumps(matches, ensure_ascii=False)

    async def get_state(self, entity_id: str) -> str:
        async with self.session.get(f"{self.base_url}/states/{entity_id}") as response:
            if response.status == 404:
                return json.dumps({"error": "entity not found"})
            response.raise_for_status()
            item = await response.json()
        return json.dumps(item, ensure_ascii=False)[:12000]

    async def list_services(self, domain: str = "") -> str:
        async with self.session.get(f"{self.base_url}/services") as response:
            response.raise_for_status()
            services = await response.json()
        if not domain:
            return json.dumps([item.get("domain") for item in services], ensure_ascii=False)
        selected = next((item for item in services if item.get("domain") == domain), None)
        if selected is None:
            return json.dumps({"error": "domain not found"})
        compact = {
            name: {"description": spec.get("description", ""),
                   "fields": list((spec.get("fields") or {}).keys())}
            for name, spec in (selected.get("services") or {}).items()
        }
        return json.dumps(compact, ensure_ascii=False)[:16000]

    async def call_service(
        self, domain: str, service: str, entity_id: str = "", data: dict[str, Any] | None = None
    ) -> str:
        payload = dict(data or {})
        if entity_id:
            payload["entity_id"] = entity_id
        async with self.session.post(
            f"{self.base_url}/services/{domain}/{service}", json=payload
        ) as response:
            body = await response.text()
            if response.status >= 300:
                return json.dumps({"error": body[:1000], "status": response.status})
        return body[:12000] or json.dumps({"ok": True})

    async def camera_data_url(self, entity_id: str) -> str:
        async with self.session.get(
            f"{self.base_url}/camera_proxy/{entity_id}"
        ) as response:
            response.raise_for_status()
            image = await response.read()
            content_type = response.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
        if len(image) > 8 * 1024 * 1024:
            raise RuntimeError("camera image exceeds 8 MiB")
        return f"data:{content_type};base64,{base64.b64encode(image).decode('ascii')}"

    async def media_entity_data_url(self, entity_id: str) -> str:
        if entity_id.startswith("camera."):
            return await self.camera_data_url(entity_id)
        if not entity_id.startswith("image."):
            raise RuntimeError("map entity must be camera.* or image.*")
        async with self.session.get(f"{self.base_url}/states/{entity_id}") as response:
            response.raise_for_status()
            state = await response.json()
        picture = str((state.get("attributes") or {}).get("entity_picture", ""))
        if not picture.startswith("/api/"):
            raise RuntimeError("image entity has no local entity_picture")
        core_url = self.base_url.removesuffix("/api")
        async with self.session.get(f"{core_url}{picture}") as response:
            response.raise_for_status()
            image = await response.read()
            content_type = response.headers.get("Content-Type", "image/png").split(";", 1)[0]
        if len(image) > 8 * 1024 * 1024:
            raise RuntimeError("map image exceeds 8 MiB")
        return f"data:{content_type};base64,{base64.b64encode(image).decode('ascii')}"

    async def discover_navigation_sources(self) -> str:
        async with self.session.get(f"{self.base_url}/states") as response:
            response.raise_for_status()
            states = await response.json()
        candidates: list[dict[str, Any]] = []
        words = ("x20", "xiaomi", "vacuum", "пылесос", "map", "карта")
        for item in states:
            entity_id = str(item.get("entity_id", ""))
            if not entity_id.startswith(("vacuum.", "camera.", "image.")):
                continue
            attributes = item.get("attributes") or {}
            name = str(attributes.get("friendly_name", ""))
            haystack = f"{entity_id} {name} {attributes.get('model', '')}".casefold()
            if not any(word in haystack for word in words):
                continue
            candidates.append({
                "entity_id": entity_id,
                "name": name,
                "state": item.get("state"),
                "model": attributes.get("model"),
                "has_picture": bool(attributes.get("entity_picture")),
            })
        return json.dumps(candidates[:30], ensure_ascii=False, separators=(",", ":"))

    async def telemetry_snapshot(self, entity_ids: tuple[str, ...]) -> str:
        if not entity_ids:
            return "{}"
        wanted = set(entity_ids)
        async with self.session.get(f"{self.base_url}/states") as response:
            response.raise_for_status()
            states = await response.json()
        snapshot: dict[str, Any] = {}
        for item in states:
            entity_id = str(item.get("entity_id", ""))
            if entity_id not in wanted:
                continue
            attributes = item.get("attributes") or {}
            value: dict[str, Any] = {"state": item.get("state")}
            if attributes.get("unit_of_measurement"):
                value["unit"] = attributes["unit_of_measurement"]
            snapshot[entity_id] = value
        for missing in wanted - snapshot.keys():
            snapshot[missing] = {"state": "unavailable"}
        return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))


TOOLS = [
    {
        "type": "function", "name": "recent_robot_events",
        "description": "Read recent semantic robot events such as collisions, stops and faults.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
            "required": ["limit"], "additionalProperties": False}, "strict": True,
    },
    {
        "type": "function", "name": "ha_search_entities",
        "description": "Find Home Assistant entities by name or entity_id.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "domain": {"type": "string"}},
            "required": ["query", "domain"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "ha_list_services",
        "description": "List Home Assistant service domains or services in one domain.",
        "parameters": {"type": "object", "properties": {
            "domain": {"type": "string"}}, "required": ["domain"],
            "additionalProperties": False}, "strict": True,
    },
    {
        "type": "function", "name": "ha_get_state",
        "description": "Read one Home Assistant entity including its attributes.",
        "parameters": {"type": "object", "properties": {
            "entity_id": {"type": "string"}}, "required": ["entity_id"],
            "additionalProperties": False}, "strict": True,
    },
    {
        "type": "function", "name": "ha_call_service",
        "description": "Call any Home Assistant service.",
        "parameters": {"type": "object", "properties": {
            "domain": {"type": "string"}, "service": {"type": "string"},
            "entity_id": {"type": "string"}, "data": {"type": "object"}},
            "required": ["domain", "service", "entity_id", "data"],
            "additionalProperties": False}, "strict": False,
    },
    {
        "type": "function", "name": "look_from_robot",
        "description": "Get a current still image from the robot camera and inspect it.",
        "parameters": {"type": "object", "properties": {}, "required": [],
                       "additionalProperties": False}, "strict": True,
    },
    {
        "type": "function", "name": "discover_navigation_sources",
        "description": "Find the Xiaomi X20+ vacuum and map image entities in Home Assistant.",
        "parameters": {"type": "object", "properties": {}, "required": [],
                       "additionalProperties": False}, "strict": True,
    },
    {
        "type": "function", "name": "look_at_home_map",
        "description": "Inspect the configured Xiaomi X20+ LDS map of the home.",
        "parameters": {"type": "object", "properties": {}, "required": [],
                       "additionalProperties": False}, "strict": True,
    },
    {
        "type": "function", "name": "remember_artifact",
        "description": (
            "Persist a household object's observed location. Use after a clear camera "
            "observation or an explicit statement by a person; never store a guess."
        ),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
            "place": {"type": "string", "description": "Room or mapped place."},
            "position": {"type": "string", "description": "Shelf, table, corner or relative position."},
            "kind": {"type": "string"},
            "state": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "source": {"type": "string", "enum": ["user", "camera", "ha"]},
            "note": {"type": "string"}},
            "required": ["name", "place", "position", "kind", "state", "confidence", "source", "note"],
            "additionalProperties": False}, "strict": True,
    },
    {
        "type": "function", "name": "find_artifacts",
        "description": "Find the last confirmed location and observation time of household objects.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 30}},
            "required": ["query", "limit"], "additionalProperties": False}, "strict": True,
    },
    {
        "type": "function", "name": "recent_artifact_changes",
        "description": "Read recently discovered, moved or repositioned household objects.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 30}},
            "required": ["limit"], "additionalProperties": False}, "strict": True,
    },
]


class OpenAIAgent:
    def __init__(
        self,
        config: AgentConfig,
        session: aiohttp.ClientSession,
        tools: HomeAssistantTools,
        sensor_callback: Callable[[str, str, dict[str, Any] | None], Awaitable[None]],
        events: SemanticEventJournal,
        memory: WorldMemory,
    ) -> None:
        self.config = config
        self.session = session
        self.tools = tools
        self.sensor_callback = sensor_callback
        self.events = events
        self.memory = memory
        self.ledger = UsageLedger()
        self.history = self._load_history()

    def _load_history(self) -> list[dict[str, str]]:
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(history, list):
                limit = 2 * self.config.history_turns
                return history[-limit:] if limit else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return []

    def _save_history(self) -> None:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        limit = 2 * self.config.history_turns
        HISTORY_PATH.write_text(
            json.dumps(self.history[-limit:] if limit else [], ensure_ascii=False),
            encoding="utf-8",
        )

    async def _publish_usage(self, blocked: bool = False) -> None:
        self.ledger._roll_periods()
        today = self.ledger.data["today"]
        month = self.ledger.data["this_month"]
        await self.sensor_callback("sensor.robot_agent_input_tokens", str(today["input_tokens"]), {
            "cached_tokens_today": today["cached_tokens"],
            "input_tokens_month": month["input_tokens"], "period": self.ledger.data["day"]})
        await self.sensor_callback("sensor.robot_agent_output_tokens", str(today["output_tokens"]), {
            "output_tokens_month": month["output_tokens"], "period": self.ledger.data["day"]})
        await self.sensor_callback("sensor.robot_agent_requests_today", str(today["requests"]), {
            "requests_month": month["requests"], "period": self.ledger.data["day"]})
        await self.sensor_callback("sensor.robot_agent_cost_today", f"{today['cost_usd']:.6f}", {
            "unit_of_measurement": "USD", "limit_usd": self.config.daily_limit_usd,
            "blocked": blocked})
        await self.sensor_callback("sensor.robot_agent_cost_month", f"{month['cost_usd']:.6f}", {
            "unit_of_measurement": "USD", "limit_usd": self.config.monthly_limit_usd,
            "blocked": blocked})

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ledger.ensure_allowed(self.config)
        headers = {"Authorization": f"Bearer {self.config.api_key}",
                   "Content-Type": "application/json"}
        async with self.session.post(RESPONSES_URL, headers=headers, json=payload) as response:
            body = await response.text()
            if response.status >= 300:
                raise RuntimeError(f"OpenAI response {response.status}: {body[:1000]}")
        result = json.loads(body)
        self.ledger.add(result.get("usage") or {}, self.config)
        await self._publish_usage()
        return result

    @staticmethod
    def _output_text(response: dict[str, Any]) -> str:
        chunks: list[str] = []
        for item in response.get("output") or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if content.get("type") == "output_text":
                    chunks.append(str(content.get("text", "")))
        return "".join(chunks).strip()

    async def _execute_tool(self, call: dict[str, Any]) -> tuple[str, str | None]:
        name = str(call.get("name", ""))
        try:
            args = json.loads(call.get("arguments") or "{}")
            if name == "ha_search_entities":
                return await self.tools.search_entities(**args), None
            if name == "ha_get_state":
                return await self.tools.get_state(**args), None
            if name == "ha_list_services":
                return await self.tools.list_services(**args), None
            if name == "ha_call_service":
                return await self.tools.call_service(**args), None
            if name == "recent_robot_events":
                return self.events.recent(**args), None
            if name == "look_from_robot":
                image = await self.tools.camera_data_url(self.config.camera_entity)
                telemetry = await self.tools.telemetry_snapshot(self.config.telemetry_entities)
                events = self.events.since_last_frame()
                return (f"Current robot camera frame attached. State: {telemetry}. "
                        f"Events since previous frame: {events}"), image
            if name == "discover_navigation_sources":
                return await self.tools.discover_navigation_sources(), None
            if name == "look_at_home_map":
                if not self.config.home_map_entity:
                    return (json.dumps({"error": "home_map_entity is not configured",
                                        "candidates": json.loads(
                                            await self.tools.discover_navigation_sources())},
                                       ensure_ascii=False), None)
                image = await self.tools.media_entity_data_url(self.config.home_map_entity)
                return "Current Xiaomi X20+ LDS home map attached.", image
            if name == "remember_artifact":
                return self.memory.remember_artifact(**args), None
            if name == "find_artifacts":
                return self.memory.find_artifacts(**args), None
            if name == "recent_artifact_changes":
                return self.memory.recent_artifact_changes(**args), None
            return json.dumps({"error": f"unknown tool {name}"}), None
        except Exception as err:
            LOGGER.exception("agent tool failed name=%s", name)
            return json.dumps({"error": str(err)[:1000]}), None

    async def ask(self, text: str) -> str:
        if not self.config.api_key:
            raise RuntimeError("OpenAI API key is not configured")
        input_items: list[dict[str, Any]] = [*self.history, {"role": "user", "content": text}]
        memory_context = self.memory.prompt_context()
        instructions = self.config.system_prompt
        if memory_context:
            instructions += "\n\nPersistent local memory:\n" + memory_context
        payload: dict[str, Any] = {
            "model": self.config.model,
            "instructions": instructions,
            "input": input_items,
            "tools": TOOLS,
            "max_output_tokens": self.config.max_output_tokens,
            "store": False,
        }
        try:
            response = await self._request(payload)
            for _ in range(self.config.max_tool_rounds):
                calls = [item for item in response.get("output") or []
                         if item.get("type") == "function_call"]
                if not calls:
                    break
                next_input: list[dict[str, Any]] = []
                for call in calls:
                    output, image = await self._execute_tool(call)
                    next_input.append({"type": "function_call_output",
                                       "call_id": call["call_id"], "output": output})
                    if image:
                        next_input.append({"role": "user", "content": [
                            {"type": "input_text", "text": "Изучи текущий кадр с камеры робота."},
                            {"type": "input_image", "image_url": image, "detail": "low"}]})
                response = await self._request({
                    "model": self.config.model,
                    "instructions": instructions,
                    "input": [*(response.get("output") or []), *next_input],
                    "tools": TOOLS,
                    "max_output_tokens": self.config.max_output_tokens,
                    "store": False,
                })
            answer = self._output_text(response)
            if not answer:
                answer = "Команда выполнена."
            self.history.extend(({"role": "user", "content": text},
                                 {"role": "assistant", "content": answer}))
            limit = 2 * self.config.history_turns
            self.history = self.history[-limit:] if limit else []
            self._save_history()
            return answer
        except BudgetExceeded:
            await self._publish_usage(blocked=True)
            raise
