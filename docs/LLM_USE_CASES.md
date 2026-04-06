# BioT Sensor Assistant — LLM Use Case Specification

> **Issue #7 — Define LLM-Specific Use Cases**
>
> This document defines every query category the BioT Sensor Assistant must handle.
> It is the source of truth for what tools need to exist in the backend, what SQL the
> agent must generate, and what structured responses the Android app must be able to parse.
>
> The use cases are modelled on the Android `VoiceCommandDictionary` — the same intents
> that the keyword-based voice system handles locally are also handled by the LLM, but
> the LLM can handle them in natural language, across both German and English, with
> context and follow-up questions.

---

## How the LLM differs from the keyword dictionary

The Android `VoiceCommandDictionary` matches fixed keyword groups — it is fast, offline,
and deterministic. It handles simple one-shot commands ("zeige gyro", "stream modus").

The LLM handles everything the dictionary cannot:

- Ambiguous or conversational phrasing ("what's been happening with the accelerometer today?")
- Multi-part questions ("show me the last 10 minutes of gyro data and tell me if anything looks unusual")
- Follow-up context ("what about the X axis specifically?")
- Explanation requests ("why is the mic reading so high?")
- Queries that require actual data analysis, not just navigation

The keyword dictionary is the fast path. The LLM is the intelligent path.

---

## Use Case Categories

### Category 1 — Sensor Data Queries

These require the agent to call `query_sensor_db` and return interpreted results.

---

#### UC-1.1 Latest reading for a sensor

**Trigger phrases (DE/EN):**
- "Was ist der aktuelle Beschleunigungswert?"
- "Zeige den letzten Gyro-Wert"
- "What is the current accelerometer reading?"
- "Show me the latest mic level"
- "What is the magnet sensor showing right now?"

**Required SQL pattern:**
```sql
SELECT * FROM accel_data ORDER BY timestamp DESC LIMIT 1
SELECT * FROM gyro_data ORDER BY timestamp DESC LIMIT 1
SELECT * FROM magnet_data ORDER BY timestamp DESC LIMIT 1
```

**Expected agent behaviour:**
- Query the relevant table for the single most recent row
- Return the X, Y, Z values in plain language with units
- Convert the timestamp to a human-readable relative time ("3 seconds ago", "vor 5 Sekunden")

**Example response:**
> "The latest accelerometer reading is X: 0.12g, Y: -0.04g, Z: 9.81g, recorded 2 seconds ago."

---

#### UC-1.2 Average over a time window

**Trigger phrases:**
- "Was war der Durchschnitt der letzten 5 Minuten?"
- "Show me the average gyro X over the last hour"
- "Durchschnittlicher Mikrofonwert heute"
- "What was the average acceleration this morning?"

**Required SQL pattern:**
```sql
SELECT AVG(accelX), AVG(accelY), AVG(accelZ)
FROM accel_data
WHERE timestamp >= (strftime('%s','now') - 300) * 1000
```

**Expected agent behaviour:**
- Parse the time window from the query (5 minutes, 1 hour, today, etc.)
- Convert to a UNIX millisecond timestamp range
- Query the appropriate table with AVG()
- Return the averaged values in plain language

---

#### UC-1.3 Data over a time range

**Trigger phrases:**
- "Zeige mir die Gyro-Daten der letzten 10 Minuten"
- "Show me accelerometer data from the last hour"
- "Wie war der Magnetfeldsensor heute Morgen?"
- "Give me all mic readings from the last 30 minutes"

**Required SQL pattern:**
```sql
SELECT * FROM gyro_data
WHERE timestamp >= (strftime('%s','now') - 600) * 1000
ORDER BY timestamp DESC
LIMIT 50
```

**Expected agent behaviour:**
- Fetch the rows for the time window (capped at 50 rows)
- Summarise the range: min, max, average, number of readings
- Note any obvious spikes or drops

---

#### UC-1.4 Row count / data availability check

**Trigger phrases:**
- "Wie viele Einträge gibt es in der Datenbank?"
- "How many accelerometer readings have been recorded?"
- "Is there any data in the database?"
- "Gibt es Gyro-Daten?"

**Required SQL pattern:**
```sql
SELECT COUNT(*) FROM accel_data
SELECT COUNT(*) FROM gyro_data
SELECT COUNT(*) FROM magnet_data
SELECT COUNT(*) FROM ereignis_data
```

**Expected agent behaviour:**
- Run COUNT on the relevant table(s)
- If zero → tell the user the table is empty and suggest starting the Android app
- If non-zero → report the count and the timestamp of the most recent entry

---

#### UC-1.5 Database schema inspection

**Trigger phrases:**
- "Was sind die Tabellen in der Datenbank?"
- "What columns does the gyro table have?"
- "Show me the database structure"
- "Welche Daten werden gespeichert?"

**Expected agent behaviour:**
- Call `get_db_schema` tool
- Explain the tables in plain language
- Map column names to what the physical sensor measures

---

### Category 2 — Anomaly and Pattern Detection

These require the agent to fetch data and reason about it.

---

#### UC-2.1 Spike detection

**Trigger phrases:**
- "Gab es ungewöhnliche Werte beim Gyroskop?"
- "Were there any spikes in the accelerometer data?"
- "Hat der Mikrofon-Sensor heute Ausreißer gezeigt?"
- "Show me any anomalies in the last hour"

**Required SQL pattern:**
```sql
SELECT * FROM gyro_data
WHERE timestamp >= (strftime('%s','now') - 3600) * 1000
ORDER BY timestamp DESC LIMIT 50
```

**Expected agent behaviour:**
- Fetch the recent data window
- Calculate the mean and standard deviation mentally (or via SQL)
- Flag any readings more than 2x the average as potential spikes
- Report the timestamp and value of flagged readings
- If nothing unusual → confirm the data looks stable

---

#### UC-2.2 Trend analysis

**Trigger phrases:**
- "Steigt die Beschleunigung über Zeit?"
- "Is the gyro value increasing or decreasing?"
- "Wie hat sich der Magnetfeldsensor in der letzten Stunde verändert?"

**Expected agent behaviour:**
- Fetch a time-ordered set of readings
- Compare the first half average to the second half average
- Report whether the trend is rising, falling, or stable with the magnitude of change

---

#### UC-2.3 Event log queries

**Trigger phrases:**
- "Wie viele Ereignisse wurden heute ausgelöst?"
- "How many events were triggered in the last hour?"
- "Zeige die letzten 5 Ereignisse"
- "Were there any magnet events recently?"
- "Show me all ACCEL events"

**Required SQL pattern:**
```sql
SELECT * FROM ereignis_data ORDER BY timestamp DESC LIMIT 10
SELECT * FROM ereignis_data WHERE sensorType = 'ACCEL' ORDER BY timestamp DESC LIMIT 10
SELECT COUNT(*) FROM ereignis_data WHERE timestamp >= (strftime('%s','now') - 86400) * 1000
```

**Expected agent behaviour:**
- Query `ereignis_data` with optional filter on `sensorType`
- Return event count, sensor type, value, and relative timestamp
- For listing: format as a readable list with timestamps

---

### Category 3 — MQTT Mode Control

These mirror the `SET_MODE_STREAM / BURST / AVERAGE` intents from the Android dictionary.
The LLM handles these when the keyword matcher is bypassed (e.g. natural phrasing).

---

#### UC-3.1 Switch transmission mode

**Trigger phrases:**
- "Wechsle in den Stream-Modus"
- "Switch to burst mode"
- "Aktiviere den Durchschnittsmodus"
- "Change the sensor to average mode"
- "Set it to stream"

**Expected agent behaviour:**
- Identify the target mode: STREAM, BURST, or AVERAGE
- Return a structured action response the Android app can execute
- Speak a confirmation via TTS

**Structured response format:**
```json
{
  "action": "mqtt_publish",
  "topic": "Control/Mode",
  "payload": "BURST",
  "tts": "Burst Modus aktiviert"
}
```

---

#### UC-3.2 What mode is active?

**Trigger phrases:**
- "Welcher Modus ist aktiv?"
- "What mode are the sensors in?"
- "Are we in stream or burst mode?"

**Expected agent behaviour:**
- Query `Control/Mode` retained message knowledge (or tell the user to check the app)
- Note: this cannot be answered from the SQLite DB — the agent should explain this clearly
- Suggest the user look at the mode buttons in the app or listen to the TTS confirmation from the last mode change

---

### Category 4 — Navigation Commands

These mirror the `NAVIGATE_*` intents from the Android dictionary.
The LLM handles these when combined with a question ("show me the gyro screen and tell me the latest value").

---

#### UC-4.1 Navigate to a screen

**Trigger phrases:**
- "Zeige mir das Gyroskop"
- "Open the accelerometer view"
- "Gehe zur Magnetfeld-Ansicht"
- "Navigate to events"
- "Zeige die Graphenansicht"
- "Öffne die Einstellungen"

**Screen mapping:**

| Phrase keywords | Target screen | Android Activity |
|---|---|---|
| gyro, gyroskop, gyroscope | Gyroscope chart | `GyroActivity` |
| accel, beschleunigung, accelerometer | Accelerometer chart | `AccelActivity` |
| magnet, magnetfeld, hall | Magnetometer chart | `MagnetActivity` |
| graph, graphen, alle sensoren, all sensors | Combined chart | `MainGraphActivity` |
| ereignis, events, notifications | Event log | `EreignisActivity` |
| einstellungen, settings | Settings | `SettingsActivity` |
| home, hauptseite, main, start | Main dashboard | `MainActivity` |

**Structured response format:**
```json
{
  "action": "navigate",
  "screen": "GyroActivity",
  "tts": "Öffne Gyroskop"
}
```

---

#### UC-4.2 Navigate + query combined

**Trigger phrases:**
- "Zeige mir den Gyro-Bildschirm und sage mir den letzten Wert"
- "Open the accelerometer and show me if anything is unusual"
- "Go to events and tell me how many there are today"

**Expected agent behaviour:**
- Return a navigate action for the Android app to execute
- Also include the data answer in the `tts` field so it is spoken immediately after navigation
- Query the database as needed for the data part

**Structured response format:**
```json
{
  "action": "navigate",
  "screen": "GyroActivity",
  "tts": "Öffne Gyroskop. Der letzte Wert war X: -0.94, Y: 4.50, Z: -4.06."
}
```

---

### Category 5 — Time Filter Commands

These mirror the `FILTER_LAST_10_MIN / FILTER_LAST_HOUR / FILTER_LAST_DAY / FILTER_CLEAR` intents.

---

#### UC-5.1 Apply a time filter

**Trigger phrases:**
- "Zeige die letzten 10 Minuten"
- "Filter the last hour"
- "Zeige nur Daten von heute"
- "Show me the last 30 minutes"

**Structured response format:**
```json
{
  "action": "apply_filter",
  "minutes": 10,
  "tts": "Zeige letzte 10 Minuten"
}
```

---

#### UC-5.2 Clear filters

**Trigger phrases:**
- "Filter entfernen"
- "Clear the filter"
- "Zeige alle Daten"
- "Remove time filter"

**Structured response format:**
```json
{
  "action": "clear_filter",
  "tts": "Filter entfernt"
}
```

---

### Category 6 — General / System

---

#### UC-6.1 Help / what can you do?

**Trigger phrases:**
- "Hilfe"
- "Was kannst du?"
- "Help"
- "What can you help me with?"
- "Liste alle Befehle auf"

**Expected agent behaviour:**
- Return a concise summary of the six use case categories
- Do not list every single phrase — give one example per category
- Keep it short enough to be spoken via TTS in under 20 seconds

---

#### UC-6.2 Project information

**Trigger phrases:**
- "Was ist BioT?"
- "Erkläre das Projekt"
- "What sensors are connected?"
- "Welche Sensoren hat das System?"

**Expected agent behaviour:**
- Answer from the system prompt knowledge (no DB query needed)
- Describe the ESP8266, KY-037, MPU-6050, A3144, MQTT, Android app connection
- Keep the answer conversational and short

---

#### UC-6.3 Unknown / fallback

When no use case matches:

**Expected agent behaviour:**
- Acknowledge the query was not understood
- Suggest the closest matching use case
- Do not hallucinate data or make up sensor values

**Structured response format:**
```json
{
  "action": "answer",
  "tts": "Das habe ich nicht verstanden. Du kannst mich zum Beispiel fragen: Was ist der aktuelle Gyro-Wert?"
}
```

---

## Structured Response Schema

Every response from the `/chat` endpoint must follow one of these action types so the Android app can parse and act on it.

```json
{
  "action": "answer | navigate | mqtt_publish | apply_filter | clear_filter",
  "tts": "Text to speak aloud via Android TTS",

  // Only for action = "navigate":
  "screen": "MainActivity | AccelActivity | GyroActivity | MagnetActivity | MainGraphActivity | EreignisActivity | SettingsActivity",

  // Only for action = "mqtt_publish":
  "topic": "Control/Mode",
  "payload": "STREAM | BURST | AVERAGE",

  // Only for action = "apply_filter":
  "minutes": 10
}
```

The `tts` field is always present. The Android app speaks it via `TtsManager` regardless of what other action is taken.

For pure data answers with no app action needed, use `action: "answer"` and put the full answer in `tts`.

---

## Tools Required in the Backend

Based on the use cases above, the agent needs these tools:

| Tool | UC categories | SQL / action |
|---|---|---|
| `query_sensor_db(sql)` | 1, 2, 3.2 | Any SELECT against the 4 tables |
| `get_db_schema()` | 1.5 | sqlite_master query |

These two tools already exist in `app/agent.py`. No additional tools are needed — the agent uses `query_sensor_db` with the right SQL for every data use case.

The agent's system prompt instructs it to always return structured JSON matching the schema above. The Android `LlmQueryHandler` (to be implemented in Phase 2 #11) parses this JSON and dispatches the action.

---

*BioT Speech IoT — LLM Use Case Specification | Issue #7 | FHDW Hannover IoT 2025-26*