import serial
import time
import json
import os

CONFIG_FILE = 'config.json'

if not os.path.exists(CONFIG_FILE):
    print(f"❌ File konfigurasi '{CONFIG_FILE}' tidak ditemukan!")
    exit(1)

with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)

SERIAL_PORT = config.get("serial_port", "/dev/ttyUSB0")
BAUD_RATE = config.get("baud_rate", 9600)


class SGPower16S:
    def __init__(self, port=SERIAL_PORT, baudrate=BAUD_RATE, timeout=3):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

    def _send_command(self, cmd_bytes):
        """Helper universal untuk kirim command ke serial dan ambil responsnya"""
        try:
            ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            ser.write(cmd_bytes)
            time.sleep(0.5)
            response = ser.readline()
            ser.close()

            if not response:
                return None

            clean_data = response.strip().decode('ascii', errors='ignore')
            if not clean_data.startswith('~'):
                return None

            return clean_data[1:]  # Buang karakter '~' di depan
        except Exception as e:
            print(f"❌ Error serial: {e}")
            return None

    def get_bms_data(self):
        # 1. Ambil Data Analog (Suhu, Tegangan, Arus, Ah, Cycle) -> Command 0x42
        # Command hex: ~20 02 46 42 E0 02 02 FD 33 \r
        analog_cmd = b"~20024642E00202FD33\r"
        info_hex = self._send_command(analog_cmd)

        if not info_hex:
            print("❌ BMS tidak merespons command analog.")
            return None

        # Parsing 16 Sel Voltase
        cell_voltages = [int(info_hex[18 + (i * 4): 18 + (i * 4) + 4], 16) / 1000.0 for i in range(16)]

        tail_idx = 18 + (16 * 4)
        tail = info_hex[tail_idx:]

        num_temp = int(tail[0:2], 16)
        temperatures = [((int(tail[2 + (i * 4): 6 + (i * 4)], 16) / 10.0) - 273.15) for i in range(num_temp)]

        offset = 2 + (num_temp * 4)
        current_abs = int(tail[offset:offset + 4], 16) / 10.0
        total_voltage = int(tail[offset + 4:offset + 8], 16) / 1000.0

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

        # 2. Ambil Data Status / MOSFET Switch -> Command 0x44 (atau info proteksi)
        # Format umum command status biasanya pakai kode 0x44 di byte keempat
        status_cmd = b"~20024644E00202FD31\r"  # Coba command 0x44
        status_hex = self._send_command(status_cmd)

        charge_fet = True
        discharge_fet = True
        status_bms = "STANDBY"

        if status_hex:
            print(f"🔍 DEBUG STATUS HEX (0x44): {status_hex}")
            # Biasanya byte status FET ada di bagian awal payload status (misal byte ke-0 atau ke-1 setelah info index)
            # Nilai umum: 0x01 = Chg On, 0x02 = Dsg On (atau sebaliknya tergantung manufacturer)
            # Kita bisa print dulu hex-nya buat dianalisis bareng kalau status_hex masuk.

        # Sementara kita tentukan status bms berdasarkan besaran arus absolut & logika aman
        # (Atau jika arus > 0.1A kita asumsikan aktif, atau pakai state sebelumnya)
        if current_abs > 0.1:
            # Karena arusnya absolut, kita bisa cek dari pembacaan sensor atau biarkan dinamis
            status_bms = "ACTIVE"
        else:
            status_bms = "STANDBY"

        return {
            "cell_voltages": cell_voltages,
            "avg_cell": sum(cell_voltages) / 16.0,
            "total_voltage": total_voltage,
            "current_abs": current_abs,
            "temperatures": temperatures,
            "avg_temperature": sum(temperatures) / len(temperatures) if temperatures else 0,
            "remain_ah": remain_ah,
            "total_ah": total_ah,
            "soc": soc,
            "cycles": cycle_count,
            "statusBms": status_bms
        }


if __name__ == "__main__":
    bms = SGPower16S(port=SERIAL_PORT)
    data = bms.get_bms_data()

    if data:
        print("\n==========================================")
        print("🔋 ANALISIS STATUS & DATA HARDWARE BMS")
        print("==========================================")
        print(f"⚡ Tegangan Total  : {data['total_voltage']:.2f} V")
        print(f"⚡ Arus Absolut    : {data['current_abs']:.1f} A")
        print(f"📊 SOC (Persen)    : {data['soc']:.1f} %")
        print(f"🔋 Kapasitas Sisa  : {data['remain_ah']:.2f} Ah / {data['total_ah']:.2f} Ah")
        print(f"🔄 Cycle Count     : {data['cycles']} kali")
        print("==========================================\n")