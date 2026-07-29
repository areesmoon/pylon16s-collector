# SG Power 16S BMS Reader & Firebase Sync

A robust Python utility designed to interface with **SG Power 16S BMS** via RS485 serial communication, parse telemetry data (cell voltages, temperatures, current, SOC, and power), and update real-time logs directly to **Google Cloud Firestore**.

---

## Features

* **RS485 Serial Communication:** Communicates directly with the BMS protocol frame using custom hex command strings.
* **Smart Current & Power Tracking:** Automatically resolves charging/discharging states and matches sign continuity based on historical log patterns.
* **Aggregated Telemetry:** Tracks state-of-charge (SOC), remaining capacity, cycle count, temperature averages, and average cell voltages (`cellVoltageAvg`).
* **External Configuration Support:** Keeps system parameters isolated in an external JSON configuration file to protect deployment settings across code updates.
* **Cloud Integration:** Appends parsed metrics into Firestore documents seamlessly.

---

## Project Structure

```text
├── main.py              # Main execution script
├── config.json          # Local configuration file (Port, Baudrate, Collection)
├── serviceAccountKey.json # Firebase Admin SDK credentials
└── README.md            # Project documentation

```

---

## Configuration (`config.json`)

Create a `config.json` file in the root directory of your project. You can use the template below:

```json
{
  "serial_port": "COM9",
  "baud_rate": 9600,
  "firestore_collection": "bms_logs",
  "credentials_file": "serviceAccountKey.json"
}

```

> **Note for Linux / Raspberry Pi Users:**
> Change `"serial_port"` from `"COM9"` to `"/dev/ttyUSB0"` (or your active USB port path).

---

## Installation & Setup

1. **Clone or Download** this project into your workspace (or directly onto your Raspberry Pi via SSH).
2. **Install Dependencies:**
Make sure you have Python installed along with the required libraries:
```bash
pip install pyserial firebase-admin

```


3. **Add Firebase Credentials:**
Place your Firebase service account key file (`serviceAccountKey.json`) into the project root directory.

---

## Usage

Run the script directly from your terminal:

```bash
python main.py

```

### Expected Output (Successful Run):

```text
✅ Berhasil update field 'slave' [ID: aBcDeFg12345] | Status: DISCHARGING | Current: -2.0A | Power: -105.84W | Avg Cell: 3.308V

```