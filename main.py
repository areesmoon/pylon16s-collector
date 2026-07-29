import serial
import time
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================
# KONFIGURASI FIREBASE & SERIAL
# ==========================================
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)

db = firestore.client()
FIRESTORE_COLLECTION = 'bms_logs'

# SERIAL_PORT = '/dev/ttyUSB0'
SERIAL_PORT = 'COM9'
BAUD_RATE = 9600


class SGPower16S:
    def __init__(self, port, baudrate, timeout=3):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

    def get_data(self):
        try:
            # 1. Buka koneksi serial ke port RS485
            ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )

            # 2. Kirim command request analog values
            request_cmd = b"~20024642E00202FD33\r"
            ser.write(request_cmd)
            time.sleep(0.5)

            response = ser.readline()
            ser.close()

            if not response:
                print("❌ Baterai tidak memberikan respons.")
                return None

            # 3. Bersihkan data frame
            clean_data = response.strip().decode('ascii', errors='ignore')
            if not clean_data.startswith('~'):
                print("❌ Format respons invalid.")
                return None

            info_hex = clean_data[1:]
            start_idx = 18

            # 4. Parsing 16 Sel Voltase
            cell_voltages = []
            for i in range(16):
                pos = start_idx + (i * 4)
                mv_val = int(info_hex[pos:pos + 4], 16)
                cell_voltages.append(mv_val / 1000.0)

            avg_cell_voltage = round(sum(cell_voltages) / 16.0, 3) if cell_voltages else 0.0

            # 5. Parsing Tail Data
            tail_idx = start_idx + (16 * 4)
            tail = info_hex[tail_idx:]

            num_temp = int(tail[0:2], 16)
            temperatures = []
            for i in range(num_temp):
                pos = 2 + (i * 4)
                raw_temp = int(tail[pos:pos + 4], 16)
                celsius = (raw_temp / 10.0) - 273.15
                temperatures.append(celsius)

            offset = 2 + (num_temp * 4)
            current_hex = tail[offset:offset + 4]
            voltage_hex = tail[offset + 4:offset + 8]

            # Ambil nilai mutlak besaran arusnya dari serial
            current_val = int(current_hex, 16)
            current_abs = abs(current_val / 10.0)

            total_voltage = int(voltage_hex, 16) / 1000.0

            # --- BAGIAN KAPASITAS, SOC & CYCLE ---
            user_defined = int(tail[offset + 12:offset + 14], 16)
            cycle_count = int(tail[offset + 18:offset + 22], 16)

            if user_defined > 2:
                remain_hex = tail[offset + 22:offset + 28]
                total_hex = tail[offset + 28:offset + 34]
            else:
                remain_hex = tail[offset + 8:offset + 12]
                total_hex = tail[offset + 14:offset + 18]

            remain_ah = int(remain_hex, 16) / 1000.0
            total_ah = int(total_hex, 16) / 1000.0
            soc = (remain_ah / total_ah) * 100 if total_ah > 0 else 0

            # Kembalikan data mentah beserta besaran absolutnya dulu
            raw_data = {
                "ah": round(remain_ah, 2),
                "soc": round(soc, 1),
                "voltage": round(total_voltage, 2),
                "current_abs": current_abs,
                "soh": 0.0,
                "cycleCount": cycle_count,
                "temperature": round(sum(temperatures) / len(temperatures), 1) if temperatures else 0.0,
                "cellVoltageAvg": avg_cell_voltage
            }
            return raw_data

        except Exception as e:
            print(f"❌ Error baca serial / parsing: {e}")
            return None


if __name__ == "__main__":
    bms = SGPower16S(port=SERIAL_PORT, baudrate=BAUD_RATE)
    raw_slave_data = bms.get_data()

    if raw_slave_data:
        try:
            collection_ref = db.collection(FIRESTORE_COLLECTION)

            # Ambil data TERAKHIR di Firestore untuk ngecek tanda arus sebelumnya (+ / -)
            last_snapshot = collection_ref.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(1).get()

            # Tentukan tanda default (misal ikuti record terakhir, kalau kosong default negatif sesuai kondisi real lu)
            sign_multiplier = -1  # Default ikut negatif (discharging)

            if last_snapshot:
                last_doc_data = last_snapshot[0].to_dict()
                last_current = float(last_doc_data.get('slave', {}).get('current', -1.0))
                if last_current > 0:
                    sign_multiplier = 1
                elif last_current < 0:
                    sign_multiplier = -1

            # Terapkan tanda arus berdasarkan record terakhir
            final_current = round(raw_slave_data['current_abs'] * sign_multiplier, 2)
            final_power = round(raw_slave_data['voltage'] * final_current, 2)
            status_bms = "CHARGING" if final_current > 0 else ("DISCHARGING" if final_current < 0 else "STANDBY")

            # Rakit payload final untuk field 'slave'
            slave_payload = {
                "ah": raw_slave_data['ah'],
                "soc": raw_slave_data['soc'],
                "voltage": raw_slave_data['voltage'],
                "current": final_current,
                "power": final_power,
                "soh": raw_slave_data['soh'],
                "cycleCount": raw_slave_data['cycleCount'],
                "temperature": raw_slave_data['temperature'],
                "statusBms": status_bms,
                "cellVoltageAvg": raw_slave_data['cellVoltageAvg']
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
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Gagal membaca data dari port RS485 baterai.")