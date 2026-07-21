from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from array import array
import json
import math
import sys
import types
import unittest

try:
    import aiohttp  # noqa: F401
except ModuleNotFoundError:
    sys.modules["aiohttp"] = types.SimpleNamespace(ClientSession=object)
try:
    import websockets  # noqa: F401
except ModuleNotFoundError:
    sys.modules["websockets"] = types.SimpleNamespace()

from gopro_assist.agent import (
    AgentConfig,
    BudgetExceeded,
    OpenAIAgent,
    SemanticEventJournal,
    UsageLedger,
)
from gopro_assist.main import (
    BUILTIN_PROMPT_PATH,
    DialogHistory,
    LUNA_PROMPT_PATH,
    SOL_ADDENDUM_PATH,
    brief_voice_response,
    follow_wheel_targets,
    high_pass_pcm16,
    is_cloud_prompt_echo,
    is_finish_conversation,
    is_local_non_speech_label,
    is_location_query,
    local_context_response,
    local_personality_response,
    local_robot_command,
    needs_agent_tools,
    place_enrollment_label,
    speaker_enrollment_name,
)
from gopro_assist.speaker_id import SpeakerProfiles
from gopro_assist.visual_places import signature_from_gray, similarity
from gopro_assist.memory import WorldMemory
from gopro_assist.realtime import is_finish_phrase, pcm16_16k_to_24k


CONFIG = AgentConfig(
    api_key="test",
    model="gpt-4o-mini",
    system_prompt="test",
    max_output_tokens=250,
    history_turns=6,
    max_tool_rounds=4,
    reasoning_effort="none",
    daily_limit_usd=0.25,
    monthly_limit_usd=3.0,
    request_reserve_usd=0.01,
    input_usd_per_million=0.15,
    cached_input_usd_per_million=0.075,
    output_usd_per_million=0.60,
    vision_model="gpt-5.6-luna",
    vision_prompt="test vision",
    vision_max_output_tokens=300,
    vision_input_usd_per_million=1.0,
    vision_cached_input_usd_per_million=0.1,
    vision_output_usd_per_million=6.0,
    camera_entity="camera.robot_eyes",
    home_map_entity="",
    telemetry_entities=("sensor.v7s_plus_robot_state",),
)


class DialogHistoryTest(unittest.TestCase):
    def test_keeps_twenty_full_turns_and_survives_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "dialog.json"
            history = DialogHistory(path, max_turns=20, max_chars=6000)
            for index in range(23):
                history.start_turn(f"вопрос {index}", "Виктор")
                history.finish_turn("длинный ответ " + str(index) + " " + "я" * 500)

            restored = DialogHistory(path, max_turns=20, max_chars=6000)
            self.assertEqual(len(restored.turns), 20)
            self.assertEqual(restored.turns[0]["user"], "вопрос 3")
            self.assertTrue(restored.turns[-1]["assistant"].endswith("я" * 500))
            self.assertEqual(restored.attributes()["max_turns"], 20)

    def test_message_limit_is_explicit_and_preserves_unicode(self) -> None:
        with TemporaryDirectory() as directory:
            history = DialogHistory(
                Path(directory) / "dialog.json", max_turns=20, max_chars=255
            )
            history.start_turn("ф" * 400)
            history.finish_turn("о" * 400)
            self.assertEqual(len(history.turns[0]["user"]), 255)
            self.assertEqual(len(history.turns[0]["assistant"]), 255)
            self.assertTrue(history.turns[0]["assistant"].endswith("…"))


class AudioFrontEndTest(unittest.TestCase):
    def test_high_pass_removes_constant_gopro_offset(self) -> None:
        source = array("h", [1200] * 1600).tobytes()
        filtered = array("h")
        filtered.frombytes(high_pass_pcm16(source))
        self.assertEqual(len(filtered), 1600)
        self.assertLess(abs(filtered[-1]), 10)

    def test_dialog_finish_phrase_is_explicit(self) -> None:
        self.assertTrue(is_finish_conversation("Спасибо, всё!"))
        self.assertFalse(is_finish_conversation("Расскажи всё подробно"))

    def test_local_non_speech_labels_are_rejected(self) -> None:
        self.assertTrue(is_local_non_speech_label("[музыка]"))
        self.assertTrue(is_local_non_speech_label("(неразборчиво)"))
        self.assertFalse(is_local_non_speech_label("Сокол, включи камеру"))

    def test_cloud_prompt_echo_is_treated_as_empty(self) -> None:
        prompt = "Русская речь. Калибровка, порог речи, Сокол, Виктор."
        self.assertTrue(is_cloud_prompt_echo(prompt, prompt))
        self.assertTrue(
            is_cloud_prompt_echo(
                "Русская речь, калибровка, порог речи, Сокол", prompt
            )
        )
        self.assertFalse(
            is_cloud_prompt_echo("Сокол, как ты думаешь, камера работает?", prompt)
        )


class UsageLedgerTest(unittest.TestCase):
    def test_counts_tokens_and_cost(self) -> None:
        with TemporaryDirectory() as directory:
            ledger = UsageLedger(Path(directory) / "usage.json")
            ledger.add({
                "input_tokens": 1000,
                "output_tokens": 500,
                "input_tokens_details": {"cached_tokens": 400},
            }, CONFIG)
            today = ledger.data["today"]
            self.assertEqual(today["requests"], 1)
            self.assertEqual(today["input_tokens"], 1000)
            self.assertEqual(today["cached_tokens"], 400)
            self.assertEqual(today["output_tokens"], 500)
            self.assertAlmostEqual(today["cost_usd"], 0.00042)
            self.assertAlmostEqual(today["roles"]["dialogue"]["cost_usd"], 0.00042)

    def test_vision_uses_separate_rates_and_bucket(self) -> None:
        with TemporaryDirectory() as directory:
            ledger = UsageLedger(Path(directory) / "usage.json")
            ledger.add({"input_tokens": 1000, "output_tokens": 100}, CONFIG,
                       rates=(1.0, 0.1, 6.0), role="vision")
            today = ledger.data["today"]
            self.assertAlmostEqual(today["cost_usd"], 0.0016)
            self.assertAlmostEqual(today["roles"]["vision"]["cost_usd"], 0.0016)

    def test_reserve_blocks_request_before_limit(self) -> None:
        with TemporaryDirectory() as directory:
            ledger = UsageLedger(Path(directory) / "usage.json")
            ledger.data["today"]["cost_usd"] = 0.245
            with self.assertRaises(BudgetExceeded):
                ledger.ensure_allowed(CONFIG)

    def test_zero_limit_disables_that_limit(self) -> None:
        with TemporaryDirectory() as directory:
            ledger = UsageLedger(Path(directory) / "usage.json")
            ledger.data["today"]["cost_usd"] = 100
            ledger.data["this_month"]["cost_usd"] = 100
            ledger.ensure_allowed(replace(CONFIG, daily_limit_usd=0, monthly_limit_usd=0))


class ResponseParsingTest(unittest.TestCase):
    def test_extracts_all_output_text_chunks(self) -> None:
        response = {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "Привет"},
            {"type": "output_text", "text": ", Виктор"},
        ]}]}
        self.assertEqual(OpenAIAgent._output_text(response), "Привет, Виктор")


class LocalRobotControlTest(unittest.TestCase):
    def test_sol_and_luna_prompts_define_single_voice_and_hard_limiter(self) -> None:
        sol = SOL_ADDENDUM_PATH.read_text(encoding="utf-8")
        luna = SOL_ADDENDUM_PATH.with_name("LUNA_SYSTEM_PROMPT.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("only model allowed to speak as Sokol-9", sol)
        self.assertIn("deterministic limiter", luna)
        self.assertIn("компактный JSON", LUNA_PROMPT_PATH.read_text(encoding="utf-8"))

    def test_builtin_persona_covers_household_roles(self) -> None:
        prompt = BUILTIN_PROMPT_PATH.read_text(encoding="utf-8")
        for role in ("Виктор", "Жена", "Дочка", "Алиса", "Мика", "гост"):
            self.assertIn(role.casefold(), prompt.casefold())
        self.assertIn("сырые скорости", prompt)

    def test_local_commands_do_not_need_agent(self) -> None:
        self.assertEqual(local_robot_command("езжай домой"), "home")
        self.assertEqual(local_robot_command("следуй за мной"), "follow")
        self.assertEqual(local_robot_command("остановись"), "stop")
        self.assertIsNone(local_robot_command("какая погода дома"))

    def test_local_identity_does_not_need_agent(self) -> None:
        response = local_personality_response("Как тебя зовут?")
        self.assertIsNotNone(response)
        self.assertIn("Сокол-девять", response)
        self.assertIn("Виктор", response)
        self.assertIsNone(local_personality_response("какая погода дома"))

    def test_realtime_pcm_resampler_changes_16k_to_24k(self) -> None:
        samples = array("h", [0, 300, 600, 900]).tobytes()
        converted = array("h")
        converted.frombytes(pcm16_16k_to_24k(samples))
        self.assertEqual(len(converted), 6)
        self.assertEqual(converted[0], 0)
        self.assertEqual(converted[3], 600)

    def test_finish_phrase_is_explicit(self) -> None:
        self.assertTrue(is_finish_phrase("Всё, закончили!"))
        self.assertTrue(is_finish_phrase("Стоп разговор"))
        self.assertFalse(is_finish_phrase("хватит пяти минут"))

    def test_spoken_response_is_short_and_plain(self) -> None:
        long_text = " ".join(f"слово{index}" for index in range(30))
        result = brief_voice_response(f"**{long_text}**")
        self.assertEqual(len(result.removesuffix("…").split()), 18)
        self.assertTrue(result.endswith("…"))
        self.assertNotIn("*", result)

    def test_time_is_answered_locally_and_camera_uses_tools(self) -> None:
        self.assertRegex(local_context_response("который час"), r"^Сейчас \d\d:\d\d\.$")
        self.assertTrue(needs_agent_tools("что ты сейчас видишь через камеру"))
        self.assertFalse(needs_agent_tools("давай просто поговорим"))

    def test_follow_targets_are_slow_and_directional(self) -> None:
        self.assertEqual(follow_wheel_targets(None, None, None), (0, 0))
        self.assertEqual(follow_wheel_targets(40, None, None), (0, 0))
        left_turn = follow_wheel_targets(90, None, 130)
        self.assertLess(left_turn[0], left_turn[1])
        straight = follow_wheel_targets(100, 80, 100)
        self.assertEqual(straight[0], straight[1])
        self.assertLessEqual(straight[0], 22)

    def test_speaker_enrollment_phrase(self) -> None:
        self.assertEqual(
            speaker_enrollment_name("запомни мой голос я виктор"), "Виктор"
        )
        self.assertIsNone(speaker_enrollment_name("виктор говорит"))

    def test_visual_place_phrases(self) -> None:
        self.assertEqual(place_enrollment_label("запомни место кухня"), "Кухня")
        self.assertTrue(is_location_query("в какой мы комнате"))
        self.assertFalse(is_location_query("какая комната больше"))


class SpeakerProfilesTest(unittest.TestCase):
    @staticmethod
    def _tone(frequency: float) -> bytes:
        samples = array("h", (
            round(5000 * math.sin(2 * math.pi * frequency * index / 16000))
            for index in range(24000)
        ))
        return samples.tobytes()

    def test_enrolls_and_separates_two_pitch_profiles(self) -> None:
        with TemporaryDirectory() as directory:
            profiles = SpeakerProfiles(Path(directory) / "speakers.json")
            profiles.enroll("Виктор", self._tone(120) * 2)
            profiles.enroll("Дочка", self._tone(240) * 2)
            self.assertEqual(profiles.identify(self._tone(120))[0], "Виктор")
            self.assertEqual(profiles.identify(self._tone(240))[0], "Дочка")
            references = profiles.reference_clips()
            self.assertEqual([name for name, _ in references], ["Виктор", "Дочка"])
            self.assertTrue(all(wav.startswith(b"RIFF") for _, wav in references))


class VisualPlaceSignatureTest(unittest.TestCase):
    def test_same_room_signature_scores_higher_than_different_layout(self) -> None:
        left_dark = bytes(30 if index % 32 < 16 else 220 for index in range(32 * 18))
        noisy = bytearray(left_dark)
        for index in range(0, len(noisy), 53):
            noisy[index] = 120
        horizontal = bytes(30 if index // 32 < 9 else 220 for index in range(32 * 18))
        reference = signature_from_gray(left_dark)
        self.assertGreater(
            similarity(reference, signature_from_gray(bytes(noisy))),
            similarity(reference, signature_from_gray(horizontal)),
        )


class SemanticEventJournalTest(unittest.TestCase):
    def test_bumper_release_is_ignored_and_press_has_meaning(self) -> None:
        journal = SemanticEventJournal()
        journal.record("sensor.v7s_plus_robot_state", "Paused", "Running", "t0")
        journal.record("binary_sensor.v7s_plus_left_bumper_pressed", "off", "on", "t1")
        journal.record("binary_sensor.v7s_plus_left_bumper_pressed", "on", "off", "t2")
        events = journal.since_last_frame()
        self.assertIn('"event":"motion_started"', events)
        self.assertIn('"event":"collision"', events)
        self.assertIn('"side":"left"', events)
        self.assertIn('"while":"Running"', events)
        self.assertNotIn("t2", events)

    def test_frame_cursor_returns_only_new_events(self) -> None:
        journal = SemanticEventJournal()
        journal.record("binary_sensor.v7s_plus_robot_docked", "off", "on", "t1")
        self.assertIn('"event":"docked"', journal.since_last_frame())
        self.assertEqual(journal.since_last_frame(), "[]")

    def test_proximity_event_is_semantic(self) -> None:
        journal = SemanticEventJournal()
        journal.record(
            "sensor.v7s_plus_proximity_event",
            "stopped:center;strength=0;seq=4",
            "approaching:left;strength=91;seq=5",
            "t1",
        )
        event = json.loads(journal.recent())[0]
        self.assertEqual(event["event"], "object_approaching")
        self.assertEqual(event["zones"], ["left"])
        self.assertEqual(event["strength"], 91)
        self.assertEqual(event["seq"], 5)

    def test_events_and_state_survive_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            first = SemanticEventJournal(path=path)
            first.record("sensor.v7s_plus_robot_state", "Paused", "Running", "t0")
            first.record("binary_sensor.v7s_plus_right_bumper_pressed", "off", "on", "t1")
            second = SemanticEventJournal(path=path)
            self.assertEqual(second.robot_state, "Running")
            recent = second.recent()
            self.assertIn('"event":"motion_started"', recent)
            self.assertIn('"side":"right"', recent)


class WorldMemoryTest(unittest.TestCase):
    def test_layout_and_seed_are_created(self) -> None:
        with TemporaryDirectory() as directory:
            memory = WorldMemory(Path(directory))
            self.assertTrue((memory.root / "identity" / "personality.md").is_file())
            self.assertTrue((memory.root / "vision" / "people" / "viktor").is_dir())
            self.assertIn("Sokol-9", memory.prompt_context())

    def test_artifact_move_updates_current_location_and_keeps_history(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            memory = WorldMemory(root)
            first = json.loads(memory.remember_artifact(
                "Красная кружка", "Кухня", "на столе", source="camera", confidence=0.9
            ))
            second = json.loads(memory.remember_artifact(
                "красная кружка", "Офис", "у монитора", source="user", confidence=1.0
            ))
            self.assertEqual(first["event"], "discovered")
            self.assertEqual(second["event"], "moved")
            found = json.loads(WorldMemory(root).find_artifacts("КРУЖКА"))
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["place"], "Офис")
            changes = json.loads(memory.recent_artifact_changes())
            self.assertEqual(changes[0]["event"], "moved")
            self.assertEqual(changes[0]["place"], "Офис")


if __name__ == "__main__":
    unittest.main()
