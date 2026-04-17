# BioT Sensor Assistant — LLM Use Case Specification

> **Issues #7 + #11 — Define LLM-Specific Use Cases & Parse and Execute LLM Intents**
>
> This document defines every query category the BioT Sensor Assistant must handle.
> It is the source of truth for what tools need to exist in the backend, what SQL the
> agent must generate, and what structured responses the Android app must be able to parse.
>
> Source of truth for the command set: `BioT_Speech_IoT_Doc/doc/content/command-dictionary.adoc`.
> The Android `VoiceCommandDictionary` is the keyword-based fast path; the LLM is the
> intelligent fallback for everything the dictionary cannot match.

---

## How the LLM differs from the keyword dictionary

The Android `VoiceCommandDictionary` matches fixed keyword groups — fast, offline, deterministic.
It handles simple one-shot commands ("zeige gyro", "stream modus", "supervision mode").

The LLM handles everything the dictionary cannot:

- Ambiguous or conversational phrasing ("what's been happening with the accelerometer today?")
- Multi-part questions ("show me the last 10 minutes of gyro data and tell me if anything looks unusual")
- **Entity extraction** ("set epsilon to 0.5 for gyro Y axis" — the dictionary can recognise the SET_EPSILON intent but cannot extract sensor + axis + value)
- Follow-up context ("what about the X axis specifically?")
- Explanation requests ("why is the mic reading so high?")
- Queries that require actual data analysis, not just navigation

The keyword dictionary is the fast path. The LLM is the intelligent path.

---

## Command source: `command-dictionary.adoc`

The .adoc lists these commands. Each maps to one or more LLM use cases below.

| .adoc command | Use case section |
|---|---|
| Start calibration | UC-7.1 |
| Show (sensor) | UC-1.1, UC-4.1 |
| Set epsilon (sensor) (axis?) | UC-7.2 |
| Show events | UC-2.3, UC-4.1 (EreignisActivity) |
| Show events (sensor) | UC-2.3 (filtered) |
| Create event (sensor) (threshold) | UC-7.3 |
| Show notifications (timeframe?) | UC-2.3 |
| Show notifications (sensor) (timeframe?) | UC-2.3 (filtered) |
| Set mode to (Autark/Supervision/Event/Identification) | UC-3.1 |
| Get mode | UC-3.2 |
| Tell value (sensor) (axis?) | UC-1.1, UC-1.1b |
| Show all sensors / Show homescreen | UC-4.1 (MainActivity) |

---

## Use Case Categories

### Category 1 — Sensor Data Queries

These require the agent to call a sensor tool and return interpreted results.

---

#### UC-1.1 Latest reading for a sensor

**Trigger phrases (DE/EN):**
- "Was ist der aktuelle Beschleunigungswert?"
- "Zeige den letzten Gyro-Wert"
- "What is the current accelerometer reading?"
- "Show me the latest mic level"

**Tool to call:** `get_latest_sensor_data(sensor)`

**Structured response format:**
```json
{
  "action": "answer",
  "tts": "The latest accelerometer reading is X 0.12g, Y -0.04g, Z 9.81g, recorded 2 seconds ago."
}
```

---

#### UC-1.1b Tell value for a single axis  *(new — from .adoc "Tell value (sensor) (axis?)")*

**Trigger phrases:**
- "Tell me the gyro X value"
- "Sage mir die Beschleunigung Z"
- "What is the magnet Y reading right now?"

**Tool to call:** `get_value_for_axis(sensor, axis)`

**Structured response format:**
```json
{
  "action": "answer",
  "tts": "Gyro axis X is 0.12 degrees per second, recorded 3 seconds ago."
}
```

If no axis is given (e.g. "Tell me the gyro value"), fall back to UC-1.1.

---

#### UC-1.2 Average over a time window
#### UC-1.3 Data over a time range
#### UC-1.4 Row count / data availability check
#### UC-1.5 Database schema inspection

*(unchanged from previous version — see `get_sensor_history`, `get_row_count`, `get_db_schema` tools)*

---

### Category 2 — Anomaly and Pattern Detection

#### UC-2.1 Spike detection
#### UC-2.2 Trend analysis

*(unchanged — see `get_sensor_history` tool)*

---

#### UC-2.3 Event log queries  *(extended for .adoc "Show events" and "Show notifications")*

**Trigger phrases:**
- "Wie viele Ereignisse wurden heute ausgelöst?"
- "Show events" / "Show events for accel"
- "Show notifications" / "Show notifications today"
- "Were there any magnet events recently?"

**Tool to call:** `get_event_log(limit)` and optionally `execute_query` for filters.

**Structured response format:**
```json
{
  "action": "navigate",
  "screen": "EreignisActivity",
  "tts": "There are 3 events today. Latest was an ACCEL threshold crossing 12 minutes ago."
}
```

---

### Category 3 — Mode Control

> **Important:** The system has TWO orthogonal mode concepts that publish on
> separate MQTT topics. Don't conflate them.

#### UC-3.1 Switch transmission mode  *(Control/Mode topic — Stream/Burst/Average)*

This controls how often the ESP8266 publishes readings.

**Trigger phrases:**
- "Wechsle in den Stream-Modus" / "Switch to burst mode" / "Aktiviere den Durchschnittsmodus"

**Structured response format:**
```json
{
  "action": "mqtt_publish",
  "topic": "Control/Mode",
  "payload": "BURST",
  "tts": "Burst mode activated"
}
```

#### UC-3.1b Switch operating mode  *(Control/OperatingMode topic — Autark/Supervision/Event/Identification)*

This controls what the ESP8266 is doing — per `command-dictionary.adoc`:
- **Autark:** sensors stop sending data (power saving)
- **Supervision:** sensors send all data; app shows homescreen
- **Event:** sensors only send data when thresholds are crossed; app notifies
- **Identification:** sensors send all data; app forwards to database

**Trigger phrases:**
- "Set mode to Supervision" / "Wechsle in den Autark Modus" / "Identifikation aktivieren"

**Structured response format:**
```json
{
  "action": "mqtt_publish",
  "topic": "Control/OperatingMode",
  "payload": "SUPERVISION",
  "tts": "Supervision mode activated"
}
```

#### UC-3.2 Get mode  *(.adoc "Get mode")*

**Trigger phrases:**
- "Welcher Modus ist aktiv?" / "What mode is active?" / "Get mode"

**Expected agent behaviour:**
The current mode is not stored in the SQLite DB — it lives only as a retained MQTT
message that the Android app already displays in its `ModeLabel` and `OperatingModeLabel`
text views. The agent should respond with `action=answer` and tell the user to look at
those labels (or wait for the ESP8266 to re-publish on `Control/OperatingMode`).

```json
{
  "action": "answer",
  "tts": "Check the mode labels on the home screen — they always show the current transmission and operating mode."
}
```

---

### Category 4 — Navigation Commands

#### UC-4.1 Navigate to a screen

Same as before. Screen mapping table:

| Phrase keywords | Target screen | Android Activity |
|---|---|---|
| gyro, gyroskop, gyroscope | Gyroscope chart | `GyroActivity` |
| accel, beschleunigung, accelerometer | Accelerometer chart | `AccelActivity` |
| magnet, magnetfeld, hall | Magnetometer chart | `MagnetActivity` |
| graph, graphen, alle sensoren, all sensors | Combined chart | `MainGraphActivity` |
| ereignis, events, notifications | Event log | `EreignisActivity` |
| einstellungen, settings | Settings | `SettingsActivity` |
| home, hauptseite, main, start, **show all sensors**, **show homescreen** | Main dashboard | `MainActivity` |

```json
{ "action": "navigate", "screen": "GyroActivity", "tts": "Opening Gyroscope" }
```

#### UC-4.2 Navigate + query combined

*(unchanged — combine the navigate action with the data answer in `tts`)*

---

### Category 5 — Time Filter Commands

#### UC-5.1 Apply a time filter
#### UC-5.2 Clear filters

*(unchanged — `apply_filter` / `clear_filter` actions)*

---

### Category 6 — General / System

#### UC-6.1 Help / what can you do?
#### UC-6.2 Project information
#### UC-6.3 Unknown / fallback  *(Issue #11 acceptance criterion)*

When the user's transcript is unparseable, the LLM returns invalid JSON, or the resolver
matched UNKNOWN_INTENT, both the Android `LlmQueryHandler` and the agent system prompt
fall back to:

```json
{
  "action": "answer",
  "tts": "Sorry, I didn't catch that. Could you repeat your question?"
}
```

The Android app speaks this fallback via `TtsManager` whether the bad reply came from the
network, a JSON parse error, or an explicit UNKNOWN_INTENT signal from the LLM.

---

### Category 7 — Calibration & Event Management  *(new — from .adoc, rails laid in Phase 2)*

These commands have voice-pipeline support (the dictionary recognises them and the executor
forwards to the LLM) but the underlying app features are not yet implemented. The agent's job
for now is to acknowledge politely and direct the user to the manual UI path.

---

#### UC-7.1 Start calibration  *(.adoc "Start calibration")*

**Trigger phrases:** "Start calibration", "Kalibrierung starten"

**Status:** Voice command recognised. Calibration UI not yet implemented.

**Structured response format:**
```json
{
  "action": "answer",
  "tts": "Calibration is recognised but not yet implemented. We'll add it in a later milestone."
}
```

---

#### UC-7.2 Set epsilon  *(.adoc "Set epsilon (sensor) (axis?)")*

**Trigger phrases:**
- "Set epsilon for gyro to 0.5"
- "Setze Epsilon für Beschleunigung Z auf eins"
- "Adjust epsilon"

**Expected agent behaviour:**
- Try to extract sensor + axis + value from the transcript
- If extraction succeeds, respond with action=answer explaining how to set it manually
  in `SettingsActivity` (the Douglas-Peucker epsilon field is already there)
- If extraction fails, ask the user to repeat with explicit values

```json
{
  "action": "navigate",
  "screen": "SettingsActivity",
  "tts": "Open Settings to adjust epsilon. Per-sensor and per-axis epsilon is not yet implemented."
}
```

---

#### UC-7.3 Create event  *(.adoc "Create event (sensor) (threshold)")*

**Trigger phrases:**
- "Create event for gyro at threshold 50"
- "Erstelle ein Ereignis für Beschleunigung wenn größer als 5"

**Expected agent behaviour:**
- Try to extract sensor + threshold from the transcript
- Navigate to `EreignisActivity` so the user can fill in the form there
- Programmatic event creation from the LLM is not yet supported

```json
{
  "action": "navigate",
  "screen": "EreignisActivity",
  "tts": "Open the events screen to create a new threshold rule. I detected sensor=gyro, threshold=50 — please confirm in the form."
}
```

---

## Structured Response Schema (single source of truth)

Every response from the `/chat` endpoint MUST be one valid JSON object matching:

```json
{
  "action":  "answer | navigate | mqtt_publish | apply_filter | clear_filter",
  "tts":     "Text to speak aloud via Android TtsManager (always present)",

  // action="navigate":
  "screen":  "MainActivity | AccelActivity | GyroActivity | MagnetActivity | MainGraphActivity | EreignisActivity | SettingsActivity",

  // action="mqtt_publish":
  "topic":   "Control/Mode | Control/OperatingMode",
  "payload": "STREAM | BURST | AVERAGE | AUTARK | SUPERVISION | EVENT | IDENTIFICATION",

  // action="apply_filter":
  "minutes": 10
}
```

**Parser tolerance** (Android side, see `LlmAction.parse()`):
- Markdown fences (` ```json ... ``` `) are stripped
- Plain prose with no JSON → treated as ANSWER with the prose as `tts`
- Malformed JSON → falls back to the UC-6.3 prompt
- Unknown action types → treated as ANSWER (forward-compatible)

---

## Tools Required in the Backend

| Tool | UC categories | What it does |
|---|---|---|
| `get_latest_sensor_data(sensor)` | 1.1, 4.2 | Most recent row from one sensor table |
| `get_value_for_axis(sensor, axis)` | 1.1b | Most recent value for one axis (NEW) |
| `get_sensor_history(sensor, minutes)` | 1.2, 1.3, 2.1, 2.2 | Time-windowed rows + summary stats |
| `get_db_schema()` | 1.5 | CREATE TABLE statements |
| `get_event_log(limit)` | 2.3 | Recent ereignis_data entries |
| `get_row_count(table)` | 1.4 | Per-table counts and freshness |
| `execute_query(sql)` | 1.x complex | Raw SELECT for ad-hoc queries |

All tools live in `mcp_server/sensor_mcp_server.py` and are exposed via the
Streamable HTTP MCP transport on `MCP_SERVER_PORT` (default 8002).

---

*BioT Speech IoT — LLM Use Case Specification | Issues #7 + #11 | FHDW Hannover IoT 2025-26*
