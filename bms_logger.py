import serial
import time
import json
import os
import urllib.request
import urllib.error

# ==========================================
# LOAD KONFIGURASI DARI FILE EXTERNAL
# ==========================================
CONFIG_FILE = 'config.json'
STATE_FILE = 'bms_state.json'

if not os.path.exists(CONFIG_FILE):
    print(f"❌ File konfigurasi '{CONFIG_FILE}' tidak ditemukan! Buat dulu file-nya.")
    exit(1)

with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)

SERIAL_PORT = config.get("serial_port", "/dev/ttyUSB0")
BAUD_RATE = int(config.get("baud_rate", 9600))
API_ENDPOINT = config.get("api_endpoint", "http://IP_SERVER_NEXTJS:3000/api/bms/update-slave")
API_ENDPOINT_SLAVE = config.get("api_endpoint_slave", "http://IP_SERVER_NEXTJS:3000/api/bms/insert-slave")
API_SECRET = config.get("api_secret", "")


def fetch_initial_state_from_api():
    """Ambil master.current terakhir dari API Next.js jika file state lokal belum ada"""
    try:
        req = urllib.request.Request(API_ENDPOINT, method="GET")
        if API_SECRET:
            req.add_header('Authorization', f'Bearer {API_SECRET}')

        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get("success"):
                master_current = res_data.get("masterCurrent", 0)
                print(f"🌐 Berhasil sinkronisasi state awal dari master.current: {master_current} A")

                sign = 1 if master_current >= 0 else -1
                return sign
    except Exception as e:
        print(f"⚠️ Gagal ambil state awal dari API (pakai default): {e}")

    return -1  # Default aman jika server gagal di-fetch saat booting


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

            current_val = int(current_hex, 16)
            current_abs = current_val / 10.0
            total_voltage = int(voltage_hex, 16) / 1000.0

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

            # ====================================================
            # LOGIKA PENENTUAN TANDA ARUS & STATUS VIA STATE LOKAL
            # ====================================================
            sign_multiplier = -1
            status_bms = "DISCHARGING"

            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    try:
                        last_state = json.load(f)
                        last_ah = last_state.get("last_ah", remain_ah)
                        last_sign = last_state.get("last_sign", -1)

                        if remain_ah > last_ah:
                            sign_multiplier = 1
                            status_bms = "CHARGING"
                        elif remain_ah < last_ah:
                            sign_multiplier = -1
                            status_bms = "DISCHARGING"
                        else:
                            sign_multiplier = last_sign
                            status_bms = "CHARGING" if sign_multiplier == 1 else "DISCHARGING"
                    except:
                        pass
            else:
                # First run: Tarik data master.current dari API Next.js biar langsung akurat
                sign_multiplier = fetch_initial_state_from_api()
                status_bms = "CHARGING" if sign_multiplier == 1 else "DISCHARGING"

            # Jika arus absolut kecil sekali / 0, set STANDBY
            if current_abs < 0.1:
                status_bms = "STANDBY"
                sign_multiplier = 1

            final_current = round(current_abs * sign_multiplier, 2)

            # Simpan state terbaru ke file lokal bms_state.json
            with open(STATE_FILE, 'w') as f:
                json.dump({
                    "last_ah": remain_ah,
                    "last_sign": sign_multiplier
                }, f)
            # ====================================================

            # Generate epoch timestamp dalam milidetik
            current_timestamp_ms = int(time.time() * 1000)

            raw_data = {
                "ah": round(remain_ah, 2),
                "soc": round(soc, 1),
                "voltage": round(total_voltage, 2),
                "current": final_current,
                "power": round(total_voltage * final_current, 2),
                "soh": 100.0,
                "cycleCount": cycle_count,
                "temperature": round(sum(temperatures) / len(temperatures), 1) if temperatures else 0.0,
                "statusBms": status_bms,
                "cellVoltageAvg": avg_cell_voltage,
                "timestamp": current_timestamp_ms
            }
            return raw_data

        except Exception as e:
            print(f"❌ Error baca serial / parsing: {e}")
            return None


if __name__ == "__main__":
    bms = SGPower16S(port=SERIAL_PORT, baudrate=BAUD_RATE)
    slave_payload = bms.get_data()

    if slave_payload:
        try:
            # Kirim data hasil pembacaan RS485 via HTTP POST ke endpoint insert-slave
            data_json = json.dumps(slave_payload).encode('utf-8')

            req = urllib.request.Request(API_ENDPOINT_SLAVE, data=data_json, method="POST")
            req.add_header('Content-Type', 'application/json')

            if API_SECRET:
                req.add_header('Authorization', f'Bearer {API_SECRET}')

            with urllib.request.urlopen(req, timeout=10) as response:
                result = response.read().decode('utf-8')
                print(f"✅ Berhasil kirim data slave ke API Next.js: {result}")

        except urllib.error.HTTPError as e:
            print(f"❌ HTTP Error saat kirim ke API: {e.code} - {e.reason}")
        except urllib.error.URLError as e:
            print(f"❌ Gagal koneksi ke server Next.js: {e.reason}")
        except Exception as err:
            print(f"❌ Error tak terduga saat POST ke API: {err}")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Gagal membaca data dari port RS485 baterai.")