import serial
import time
import json
import os

# ==========================================
# LOAD KONFIGURASI DARI FILE EXTERNAL
# ==========================================
CONFIG_FILE = 'config.json'

if not os.path.exists(CONFIG_FILE):
    print(f"❌ File konfigurasi '{CONFIG_FILE}' tidak ditemukan! Buat dulu file-nya.")
    exit(1)

with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)

SERIAL_PORT = config.get("serial_port", "/dev/ttyUSB0")
BAUD_RATE = config.get("baud_rate", 9600)

def parse_signed_16bit(hex_str):
    """Helper untuk mengubah hex 16-bit menjadi angka bertanda (Signed Two's Complement)"""
    val = int(hex_str, 16)
    if val >= 0x8000:
        val -= 0x10000
    return val

class SGPower16S:
    def __init__(self, port=SERIAL_PORT, baudrate=BAUD_RATE, timeout=3):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

    def get_data(self):
        try:
            # 1. Buka koneksi serial
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

            # 5. Parsing Tail Data
            tail_idx = start_idx + (16 * 4)
            tail = info_hex[tail_idx:]

            # --- DEBUG MENTAH BUAT NGINTIP POSISI BYTE ---
            print(f"🔍 DEBUG TAIL HEX: {tail}")

            # Ambil jumlah sensor suhu di 2 karakter awal
            num_temp = int(tail[0:2], 16)
            temperatures = []
            for i in range(num_temp):
                pos = 2 + (i * 4)
                raw_temp = int(tail[pos:pos + 4], 16)
                celsius = (raw_temp / 10.0) - 273.15
                temperatures.append(celsius)

            # --- OFFSET DINAMIS BERDASARKAN JUMLAH SUHU ---
            offset = 2 + (num_temp * 4)

            # Tepat setelah suhu, 4 karakter berikutnya adalah ARUS (Current)
            current_hex = tail[offset:offset + 4]
            # 4 karakter setelah arus adalah TEGANGAN TOTAL (Voltage)
            voltage_hex = tail[offset + 4:offset + 8]

            # Konversi Arus dengan Signed 16-bit (Two's Complement)
            current_val = parse_signed_16bit(current_hex)
            print(f"🔍 DEBUG MENTAH ARUS: hex={current_hex} | decimal_signed={current_val}")

            current = current_val / 10.0
            total_voltage = int(voltage_hex, 16) / 1000.0

            # --- BAGIAN KAPASITAS & SOC ---
            user_defined = int(tail[offset + 12:offset + 14], 16)
            cycle_count = int(tail[offset + 18:offset + 22], 16)

            # Cek apakah BMS pakai format kapasitas 24-bit (User Defined > 2)
            if user_defined > 2:
                remain_hex = tail[offset + 22:offset + 28]  # 6 karakter (24-bit)
                total_hex = tail[offset + 28:offset + 34]   # 6 karakter (24-bit)
            else:
                remain_hex = tail[offset + 8:offset + 12]   # 4 karakter (16-bit)
                total_hex = tail[offset + 14:offset + 18]   # 4 karakter (16-bit)

            remain_ah = int(remain_hex, 16) / 1000.0
            total_ah = int(total_hex, 16) / 1000.0
            soc = (remain_ah / total_ah) * 100 if total_ah > 0 else 0

            # Status arah arus yang akurat (Positif = Charging, Negatif = Discharging, 0 = Standby)
            status_arus = "CHARGING ⚡" if current > 0 else ("DISCHARGING 🔋" if current < 0 else "STANDBY 💤")

            # Kembalikan sebagai dictionary yang rapi
            result = {
                "cell_voltages": cell_voltages,
                "avg_cell": sum(cell_voltages) / 16.0,
                "min_cell": min(cell_voltages),
                "max_cell": max(cell_voltages),
                "total_voltage": total_voltage,
                "current": current,
                "status_arus": status_arus,
                "temperatures": temperatures,
                "avg_temperature": sum(temperatures) / len(temperatures) if temperatures else 0,
                "remain_ah": remain_ah,
                "total_ah": total_ah,
                "soc": soc,
                "cycles": cycle_count
            }

            return result

        except Exception as e:
            print(f"❌ Error komunikasi serial: {e}")
            return None


if __name__ == "__main__":
    bms = SGPower16S(port=SERIAL_PORT)
    data = bms.get_data()

    if data:
        print("\n==========================================")
        print("🔋 MONITORING BATERAI SG POWER 16S")
        print("==========================================")
        print(f"⚡ Tegangan Total  : {data['total_voltage']:.2f} V")
        print(f"⚡ Arus (Current)  : {data['current']:.1f} A ({data['status_arus']})")
        print(f"📊 SOC (Persen)    : {data['soc']:.1f} %")
        print(f"🔋 Kapasitas Sisa  : {data['remain_ah']:.2f} Ah / {data['total_ah']:.2f} Ah")
        print(f"🔄 Cycle Count     : {data['cycles']} kali")
        print(f"⚡ Rata-rata Sel   : {data['avg_cell']:.3f} V")
        print(f"🌡️ Suhu Rata-rata  : {data['avg_temperature']:.1f} °C")
        print("------------------------------------------")
        print("Detail Sel 1-16:")
        for idx, v in enumerate(data['cell_voltages']):
            print(f"  Sel #{idx + 1:02d}: {v:.3f} V")
        print("==========================================\n")