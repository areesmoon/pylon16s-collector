import time
import json
import os
import firebase_admin
from firebase_admin import credentials, firestore
from bms_pylon import SGPower16S

# ==========================================
# LOAD KONFIGURASI DARI FILE EXTERNAL
# ==========================================
CONFIG_FILE = 'config.json'

if not os.path.exists(CONFIG_FILE):
    print(f"❌ File konfigurasi '{CONFIG_FILE}' tidak ditemukan! Buat dulu file-nya.")
    exit(1)

with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)

SERIAL_PORT = config.get("serial_port", "COM9")
BAUD_RATE = config.get("baud_rate", 9600)
FIRESTORE_COLLECTION = config.get("firestore_collection", "bms_logs")
CREDENTIALS_FILE = config.get("credentials_file", "serviceAccountKey.json")

# ==========================================
# KONFIGURASI FIREBASE
# ==========================================
cred = credentials.Certificate(CREDENTIALS_FILE)
firebase_admin.initialize_app(cred)

db = firestore.client()


if __name__ == "__main__":
    # Inisialisasi BMS menggunakan library terpisah
    bms = SGPower16S(port=SERIAL_PORT, baudrate=BAUD_RATE)
    raw_data = bms.get_data()

    if raw_data and raw_data.get("success"):
        try:
            collection_ref = db.collection(FIRESTORE_COLLECTION)

            # Ambil data TERAKHIR di Firestore untuk ngecek tanda arus sebelumnya (+ / -)
            last_snapshot = collection_ref.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(1).get()

            # Tentukan tanda default (misal ikuti record terakhir, kalau kosong default negatif)
            sign_multiplier = -1  # Default ikut negatif (discharging)

            if last_snapshot:
                last_doc_data = last_snapshot[0].to_dict()
                last_current = float(last_doc_data.get('slave', {}).get('current', -1.0))
                if last_current > 0:
                    sign_multiplier = 1
                elif last_current < 0:
                    sign_multiplier = -1

            # Terapkan tanda arus berdasarkan record terakhir
            current_abs = raw_data['current_abs']
            final_current = round(current_abs * sign_multiplier, 2)
            total_voltage = raw_data['voltage']
            final_power = round(total_voltage * final_current, 2)
            status_bms = "CHARGING" if final_current > 0 else ("DISCHARGING" if final_current < 0 else "STANDBY")

            # Rakit payload final untuk field 'slave'
            slave_payload = {
                "ah": raw_data['ah'],
                "soc": raw_data['soc'],
                "voltage": total_voltage,
                "current": final_current,
                "power": final_power,
                "soh": raw_data['soh'],
                "cycleCount": raw_data['cycles'],
                "temperature": raw_data['temperature'],
                "statusBms": status_bms,
                "cellVoltageAvg": raw_data['avg_cell']
            }

            if not last_snapshot:
                print(f"⚠️ Tidak ada dokumen ditemukan di collection: {FIRESTORE_COLLECTION}")
            else:
                last_doc = last_snapshot[0]
                last_id = last_doc.id

                # Update field 'slave' dengan data terbaru
                collection_ref.document(last_id).update({
                    "slave": slave_payload
                })

                print(f"✅ Berhasil update field 'slave' [ID: {last_id}] | Status: {slave_payload['statusBms']} | Current: {slave_payload['current']}A | Power: {slave_payload['power']}W | Avg Cell: {slave_payload['cellVoltageAvg']}V")

        except Exception as fb_err:
            print(f"❌ Gagal update Firestore: {fb_err}")
    else:
        error_msg = raw_data.get("error") if raw_data else "Baterai tidak memberikan respons."
        print(f"[{time.strftime('%H:%M:%S' )}] ⚠️ Gagal membaca data dari port RS485: {error_msg}")