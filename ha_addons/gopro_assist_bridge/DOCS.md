# Настройка

По умолчанию используется поток
`rtsp://192.168.0.4:8554/gopro_robot`, Assist pipeline `GPT`, локальный STT
`stt.faster_whisper`, Piper `tts.piper` и
`media_player.v7s_plus_wifi_audio`.

Основные параметры VAD:

- `vad_start_rms` — абсолютный минимальный порог начала речи;
- `vad_end_rms` — абсолютный минимальный порог окончания речи;
- `vad_noise_multiplier` — адаптивный множитель измеренного фонового шума;
- `vad_silence_ms` — пауза, завершающая фразу.

Диагностика доступна в журнале add-on и сущностях
`sensor.gopro_assist_status`, `sensor.gopro_assist_transcript`,
`sensor.gopro_assist_response`.
