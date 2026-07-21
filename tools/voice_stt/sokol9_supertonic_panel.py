# -*- coding: utf-8 -*-
"""
Sokol-9 local TTS panel for Supertonic.

Работает полностью локально после первого скачивания модели Supertonic (~400 MB).
Открывает браузерный пульт, принимает сцену вида:
    Ника: ...
    Волкова: ...
    Капитан видит: ...
и возвращает один WAV, который браузер сразу проигрывает.

Запуск:
    python sokol9_supertonic_panel.py
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import wave
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HOST = "127.0.0.1"
PORT = 8790

ROOT = Path(__file__).resolve().parent
VOICE_DIR = ROOT / "voice"
CURRENT_SCENE = VOICE_DIR / "current_scene_tts.txt"
VOICE_MAP_FILE = VOICE_DIR / "supertonic_voice_map.json"
WINDOWS_TTS_DIR = Path(r"D:\cltkfq_tts")
LOCAL_TTS_DIR = Path(
    os.environ.get(
        "SOKOL9_TTS_HOME",
        str(WINDOWS_TTS_DIR if WINDOWS_TTS_DIR.is_dir() else ROOT / ".tts_cache"),
    )
)
LOCAL_CACHE_DIR = LOCAL_TTS_DIR / "cache"

SAMPLE_RATE = 44100
KEEP_WAVS = 5

LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
for key, value in {
    "HF_HOME": LOCAL_CACHE_DIR / "huggingface",
    "HUGGINGFACE_HUB_CACHE": LOCAL_CACHE_DIR / "huggingface" / "hub",
    "TORCH_HOME": LOCAL_CACHE_DIR / "torch",
    "XDG_CACHE_HOME": LOCAL_CACHE_DIR,
}.items():
    os.environ.setdefault(key, str(value))


DEFAULT_VOICE_MAP: dict[str, dict[str, Any]] = {
    # Встроенные Supertonic голоса: M1-M5, F1-F5.
    # speed: 0.7 медленно, 1.0 нормально, 2.0 быстро.
    # steps: 5 быстрее/хуже, 8 норм, 10-12 лучше/медленнее.
    "капитан": {"voice": "M1", "speed": 1.08, "steps": 8, "prefix": "Капитан."},
    "виктор": {"voice": "M1", "speed": 1.08, "steps": 8, "prefix": "Капитан."},

    "капитан видит": {"voice": "M2", "speed": 1.12, "steps": 8, "prefix": ""},
    "сцена": {"voice": "M2", "speed": 1.12, "steps": 8, "prefix": ""},
    "ведущий": {"voice": "M2", "speed": 1.12, "steps": 8, "prefix": ""},

    "ника": {"voice": "F5", "speed": 0.96, "steps": 10, "prefix": "Ника."},
    "корабельный ии ника": {"voice": "F5", "speed": 0.96, "steps": 10, "prefix": "Ника."},

    "волкова": {"voice": "F1", "speed": 0.98, "steps": 10, "prefix": "Волкова."},
    "рейна волкова": {"voice": "F1", "speed": 0.98, "steps": 10, "prefix": "Волкова."},
    "старпом рейна волкова": {"voice": "F1", "speed": 0.98, "steps": 10, "prefix": "Волкова."},

    "мор": {"voice": "F3", "speed": 1.12, "steps": 8, "prefix": "Мор."},
    "лиан мор": {"voice": "F3", "speed": 1.12, "steps": 8, "prefix": "Мор."},
    "главный инженер лиан мор": {"voice": "F3", "speed": 1.12, "steps": 8, "prefix": "Мор."},

    "кайл": {"voice": "M3", "speed": 1.18, "steps": 8, "prefix": "Кайл."},
    "техник кайл": {"voice": "M3", "speed": 1.18, "steps": 8, "prefix": "Кайл."},

    "арден": {"voice": "M2", "speed": 1.10, "steps": 8, "prefix": "Арден."},
    "том арден": {"voice": "M2", "speed": 1.10, "steps": 8, "prefix": "Арден."},
    "пилот том арден": {"voice": "M2", "speed": 1.10, "steps": 8, "prefix": "Арден."},

    "науменко": {"voice": "M4", "speed": 1.04, "steps": 8, "prefix": "Науменко."},
    "эдвард науменко": {"voice": "M4", "speed": 1.04, "steps": 8, "prefix": "Науменко."},
    "майор эдвард науменко": {"voice": "M4", "speed": 1.04, "steps": 8, "prefix": "Науменко."},

    "эйлер": {"voice": "M5", "speed": 1.02, "steps": 8, "prefix": "Эйлер."},
    "марк эйлер": {"voice": "M5", "speed": 1.02, "steps": 8, "prefix": "Эйлер."},

    "вейс": {"voice": "F2", "speed": 1.04, "steps": 8, "prefix": "Доктор Вейс."},
    "доктор вейс": {"voice": "F2", "speed": 1.04, "steps": 8, "prefix": "Доктор Вейс."},
    "алёна вейс": {"voice": "F2", "speed": 1.04, "steps": 8, "prefix": "Доктор Вейс."},
    "алена вейс": {"voice": "F2", "speed": 1.04, "steps": 8, "prefix": "Доктор Вейс."},

    "михаил": {"voice": "M1", "speed": 1.07, "steps": 8, "prefix": "Михаил."},
    "михаил середа": {"voice": "M1", "speed": 1.07, "steps": 8, "prefix": "Михаил."},
}


HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8" />
<title>Сокол-9 — Supertonic Local TTS</title>
<style>
:root { color-scheme: dark; }
body {
    margin: 0; font-family: system-ui, Segoe UI, sans-serif;
    background: #101218; color: #eef1f6;
}
.wrap { max-width: 1120px; margin: 0 auto; padding: 20px; }
h1 { margin: 0 0 8px; font-size: 24px; }
.sub { color:#aeb7c7; margin-bottom: 16px; line-height: 1.4; }
textarea {
    width: 100%; min-height: 360px; box-sizing: border-box;
    background: #161a23; color: #eef1f6; border: 1px solid #303848;
    border-radius: 12px; padding: 14px; font-size: 16px; line-height: 1.45;
    resize: vertical;
}
.row { display:flex; gap: 10px; flex-wrap: wrap; align-items:center; margin: 12px 0; }
button {
    background: #405cf5; color: white; border: 0; border-radius: 10px;
    padding: 10px 14px; font-weight: 650; cursor: pointer;
}
button.secondary { background:#2a3140; }
button:disabled { opacity: .45; cursor: wait; }
select, input[type="number"] {
    background:#161a23; color:#eef1f6; border:1px solid #303848;
    border-radius:8px; padding:8px;
}
.card {
    background:#161a23; border:1px solid #303848; border-radius:12px;
    padding:12px; margin-top:12px;
}
.small { color:#aeb7c7; font-size: 13px; }
pre {
    white-space: pre-wrap; background:#0c0e13; border-radius:10px;
    padding:10px; overflow:auto;
}
audio { width:100%; margin-top:10px; }
.bad { color:#ff9b9b; }
.good { color:#93ffb0; }
</style>
</head>
<body>
<div class="wrap">
<h1>Сокол-9 — локальная озвучка Supertonic</h1>
<div class="sub">
Вставляй сцену в формате <b>Персонаж: реплика</b>.
Озвучка идёт локально через <code>supertonic</code>, без Edge/Microsoft TTS и без браузерного <code>speechSynthesis</code>.
Первый запуск может скачать модель Supertonic один раз.
</div>

<textarea id="scene">Капитан видит: Мостик просыпается после ночной вахты.

Волкова: Доброе утро, капитан. Кофе-машина установлена.

Ника: Подтверждаю. Я не анализирую кофейный эксперимент без приказа.

Кайл: Зато медотсек теперь анализирует отсутствие кофейника как личную трагедию.</textarea>

<div class="row">
    <button id="synth">Озвучить сцену</button>
    <button id="save" class="secondary">Сохранить current_scene_tts.txt</button>
    <button id="stop" class="secondary">Стоп</button>
    <label class="small">Общее качество/steps:
        <select id="steps">
            <option value="">по персонажам</option>
            <option value="5">5 быстро</option>
            <option value="8">8 норм</option>
            <option value="10">10 качественнее</option>
            <option value="12">12 максимум</option>
        </select>
    </label>
</div>

<div class="card">
    <div id="status" class="small">Статус: готов.</div>
    <audio id="audio" controls></audio>
</div>

<div class="card">
    <b>Карта голосов</b>
    <div class="small">Правится в файле <code>supertonic_voice_map.json</code>. После правки перезапусти сервер.</div>
    <pre id="voices"></pre>
</div>
</div>

<script>
const scene = document.getElementById('scene');
const synth = document.getElementById('synth');
const save = document.getElementById('save');
const stopBtn = document.getElementById('stop');
const audio = document.getElementById('audio');
const statusEl = document.getElementById('status');
const voices = document.getElementById('voices');
const steps = document.getElementById('steps');

function setStatus(txt, cls='') {
    statusEl.className = 'small ' + cls;
    statusEl.textContent = 'Статус: ' + txt;
}

async function loadVoices() {
    const r = await fetch('/api/voices');
    const j = await r.json();
    voices.textContent = JSON.stringify(j, null, 2);
}

async function saveScene() {
    save.disabled = true;
    try {
        const r = await fetch('/api/save_scene', {
            method:'POST', headers:{'content-type':'application/json'},
            body: JSON.stringify({text: scene.value})
        });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || r.statusText);
        setStatus('сцена сохранена в current_scene_tts.txt', 'good');
    } catch (e) {
        setStatus('ошибка сохранения: ' + e.message, 'bad');
    } finally {
        save.disabled = false;
    }
}

async function synthesize() {
    synth.disabled = true;
    setStatus('генерация WAV через локальный Supertonic...');
    try {
        const payload = { text: scene.value, override_steps: steps.value ? Number(steps.value) : null };
        const r = await fetch('/api/synthesize', {
            method:'POST',
            headers:{'content-type':'application/json'},
            body: JSON.stringify(payload)
        });
        if (!r.ok) {
            let msg = await r.text();
            try { msg = JSON.parse(msg).error || msg; } catch {}
            throw new Error(msg);
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        audio.src = url;
        await audio.play();
        setStatus('готово, проигрываю', 'good');
    } catch(e) {
        setStatus('ошибка: ' + e.message, 'bad');
    } finally {
        synth.disabled = false;
    }
}

synth.onclick = synthesize;
save.onclick = saveScene;
stopBtn.onclick = () => { audio.pause(); audio.currentTime = 0; };
loadVoices().catch(e => setStatus('не могу загрузить карту голосов: ' + e.message, 'bad'));
</script>
</body>
</html>
"""


@dataclass
class Utterance:
    speaker: str
    text: str


class Engine:
    def __init__(self) -> None:
        self.tts = None
        self.styles: dict[str, Any] = {}

    def load(self) -> None:
        if self.tts is not None:
            return
        try:
            from supertonic import TTS
        except Exception as exc:
            raise RuntimeError(
                "Модуль supertonic не установлен. Запусти install_supertonic_local.bat "
                "или выполни: python -m pip install supertonic"
            ) from exc
        self.tts = TTS(auto_download=True)

    def get_style(self, voice: str) -> Any:
        self.load()
        assert self.tts is not None
        if voice not in self.styles:
            self.styles[voice] = self.tts.get_voice_style(voice_name=voice)
        return self.styles[voice]

    def synthesize_one(self, text: str, voice: str, speed: float, steps: int) -> bytes:
        self.load()
        assert self.tts is not None
        style = self.get_style(voice)
        wav, _duration = self.tts.synthesize(
            text=text,
            voice_style=style,
            total_steps=steps,
            speed=speed,
            max_chunk_length=260,
            silence_duration=0.08,
            lang="ru",
            verbose=False,
        )
        tmp = io.BytesIO()
        # Supertonic умеет save_audio только в файл, поэтому пишем WAV сами.
        # На текущих версиях wav обычно numpy float32/float64 [-1..1].
        import numpy as np

        arr = np.asarray(wav)
        if arr.ndim > 1:
            arr = arr.reshape(-1)
        arr = np.clip(arr, -1.0, 1.0)
        pcm = (arr * 32767.0).astype("<i2").tobytes()

        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm)
        return tmp.getvalue()


ENGINE = Engine()


def normalize_speaker(name: str) -> str:
    name = name.strip().lower()
    name = name.replace("ё", "е")
    name = re.sub(r"\s+", " ", name)
    return name


def load_voice_map() -> dict[str, dict[str, Any]]:
    if not VOICE_MAP_FILE.exists():
        VOICE_MAP_FILE.write_text(
            json.dumps(DEFAULT_VOICE_MAP, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return DEFAULT_VOICE_MAP
    data = json.loads(VOICE_MAP_FILE.read_text(encoding="utf-8"))
    # ключи нормализуем, чтобы "Ника" и "ника" не отличались.
    out: dict[str, dict[str, Any]] = {}
    for k, v in data.items():
        out[normalize_speaker(k)] = v
    return out


def parse_scene(text: str) -> list[Utterance]:
    items: list[Utterance] = []
    current: Utterance | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        # Поддержка markdown-цитат из лога.
        line = re.sub(r"^>\s*", "", line)

        m = re.match(r"^([^:]{1,48}):\s*(.+)$", line)
        if m:
            speaker = m.group(1).strip()
            speech = m.group(2).strip()
            current = Utterance(speaker=speaker, text=speech)
            items.append(current)
        else:
            # Если строка без "Персонаж:", присоединяем к предыдущей.
            if current is not None:
                current.text += " " + line
            else:
                current = Utterance(speaker="Капитан видит", text=line)
                items.append(current)

    return items


def wav_bytes_to_pcm(wav_bytes: bytes) -> tuple[bytes, int, int, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    return pcm, nch, sw, sr


def join_wavs(chunks: list[bytes], silence_ms: int = 120) -> bytes:
    if not chunks:
        raise ValueError("Нет реплик для озвучки.")

    frames: list[bytes] = []
    nch = sw = sr = None

    for ch in chunks:
        pcm, c_nch, c_sw, c_sr = wav_bytes_to_pcm(ch)
        if nch is None:
            nch, sw, sr = c_nch, c_sw, c_sr
        if (c_nch, c_sw, c_sr) != (nch, sw, sr):
            raise ValueError("Разные WAV параметры, не могу склеить.")
        frames.append(pcm)
        silence_samples = int((sr or SAMPLE_RATE) * silence_ms / 1000)
        frames.append(b"\x00" * silence_samples * (sw or 2) * (nch or 1))

    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(nch or 1)
        wf.setsampwidth(sw or 2)
        wf.setframerate(sr or SAMPLE_RATE)
        wf.writeframes(b"".join(frames))
    return out.getvalue()


def save_scene_wav(wav_bytes: bytes) -> Path:
    VOICE_DIR.mkdir(exist_ok=True)
    out = VOICE_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_scene.wav"
    out.write_bytes(wav_bytes)

    wavs = sorted(
        VOICE_DIR.glob("*.wav"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in wavs[KEEP_WAVS:]:
        try:
            old.unlink()
        except OSError:
            pass
    return out


def clean_for_tts(s: str) -> str:
    s = s.replace("Сокол-9", "Сокол девять")
    s = s.replace('"', "")
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def synthesize_scene_text(text: str, override_steps: int | None = None) -> bytes:
    utterances = parse_scene(text)
    if not utterances:
        raise ValueError("Нет реплик для озвучки.")

    vmap = load_voice_map()
    wavs: list[bytes] = []
    for u in utterances:
        key = normalize_speaker(u.speaker)
        cfg = vmap.get(key) or vmap.get(key.replace("старпом ", "")) or {
            "voice": "M2",
            "speed": 1.08,
            "steps": 8,
            "prefix": u.speaker + ".",
        }
        voice = str(cfg.get("voice", "M2"))
        speed = float(cfg.get("speed", 1.05))
        steps = int(override_steps or cfg.get("steps", 8))
        prefix = str(cfg.get("prefix", "")).strip()

        speech = clean_for_tts(u.text)
        if prefix:
            speech = f"{prefix} {speech}"
        wavs.append(ENGINE.synthesize_one(speech, voice=voice, speed=speed, steps=steps))
    return join_wavs(wavs)


def speak_current_scene() -> Path:
    text = CURRENT_SCENE.read_text(encoding="utf-8-sig")
    wav = synthesize_scene_text(text)
    out = save_scene_wav(wav)
    try:
        os.startfile(out)
    except Exception as exc:
        print(f"Playback skipped: {exc}")
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "Sokol9Supertonic/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def send_json(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("content-length") or "0")
        raw = self.rfile.read(n)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/voices":
            self.send_json(load_voice_map())
            return

        self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/save_scene":
                data = self.read_json()
                text = str(data.get("text", ""))
                VOICE_DIR.mkdir(exist_ok=True)
                CURRENT_SCENE.write_text(text, encoding="utf-8")
                self.send_json({"ok": True, "path": str(CURRENT_SCENE)})
                return

            if self.path == "/api/synthesize":
                data = self.read_json()
                text = str(data.get("text", ""))
                override_steps = data.get("override_steps")
                utterances = parse_scene(text)
                if not utterances:
                    self.send_json({"error": "Нет реплик для озвучки."}, 400)
                    return

                VOICE_DIR.mkdir(exist_ok=True)
                CURRENT_SCENE.write_text(text, encoding="utf-8")

                vmap = load_voice_map()
                wavs: list[bytes] = []
                t0 = time.time()

                for u in utterances:
                    key = normalize_speaker(u.speaker)
                    cfg = vmap.get(key) or vmap.get(key.replace("старпом ", "")) or {
                        "voice": "M2",
                        "speed": 1.08,
                        "steps": 8,
                        "prefix": u.speaker + ".",
                    }
                    voice = str(cfg.get("voice", "M2"))
                    speed = float(cfg.get("speed", 1.05))
                    steps = int(override_steps or cfg.get("steps", 8))
                    prefix = str(cfg.get("prefix", "")).strip()

                    speech = clean_for_tts(u.text)
                    if prefix:
                        speech = f"{prefix} {speech}"

                    wavs.append(ENGINE.synthesize_one(speech, voice=voice, speed=speed, steps=steps))

                combined = join_wavs(wavs)
                saved_wav = save_scene_wav(combined)
                dt = time.time() - t0

                self.send_response(200)
                self.send_header("content-type", "audio/wav")
                self.send_header("content-length", str(len(combined)))
                self.send_header("x-generation-seconds", f"{dt:.2f}")
                self.send_header("x-saved-wav", saved_wav.name)
                self.end_headers()
                self.wfile.write(combined)
                return

            self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def main() -> None:
    if "--speak-current" in sys.argv:
        out = speak_current_scene()
        print(f"Saved and played: {out}")
        return

    print(f"Starting Sokol-9 Supertonic panel: http://{HOST}:{PORT}")
    print("First Supertonic run may download the model once (~400 MB).")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    webbrowser.open(f"http://{HOST}:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
