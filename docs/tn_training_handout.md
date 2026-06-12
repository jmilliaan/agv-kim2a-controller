# AGV I-PRIME 2.0 (TN) — Quick Reference Handout

*Lembar pegangan satu halaman untuk pelatihan operasi & promosi. Simpan bersama unit.*
*(Bahasa: Indonesia. Istilah teknis/UI dalam bahasa Inggris ditulis* miring*; akronim AGV, RFID,
HMI, LiDAR, BLDC, LFP, RPM, Wi-Fi, SSID tetap apa adanya.)*

---

### What it is
AGV penggerak *differential drive* yang **mengikuti *magnetic tape*** dan membaca *tag* **UHF RFID**
untuk aksi berdasarkan lokasi. Menarik *trolley* 1 ton lewat *tow-pin* vertikal. Dioperasikan lewat
panel fisik + **HMI web lokal**. *(Unit demo — loop kecil berbentuk stadion.)*

### Key specs
*Towing* 1 ton · 1136 × 503 mm · BLDC 400 W, *gearbox* 1:30 · ~0,4 m/s *auto* ·
**48 V / 105 Ah LFP**, isi bila di bawah 48 V, ~4–5 jam · *tape* 30 mm (kutub utara di atas) · UHF RFID.

---

### Modes
***Manual*** (Anda mengemudi: *Fwd/Rev/Left/Right*) · ***Armed*** (siap *auto*) · ***Running***
(*auto*, hanya maju) · ***Emergency***. *Mundur hanya di *Manual* — tidak ada *auto* mundur.*

### Physical controls
***E-stop*** (hanya bahaya) · selektor *Manual/Auto* · ***Start*** (perlu *Auto* + *Armed* + *tape*
terdeteksi) · ***Reset/Stop*** (berhenti normal & pemulihan *E-stop*) · tombol arah (*hold-to-move*).

---

### HMI — connect
Wi-Fi **`agv_field`** / `igp@2026` → *browser* **`http://192.168.2.100:5000`**.
*Jaringan lokal saja. HMI bukan tombol berhenti darurat — pakai *E-stop* fisik.*

### HMI — six pages
| Halaman | Fungsi |
|---|---|
| ***Home*** | *Mode* langsung, keselamatan/alarm, simpangan & *marker* sensor, *tag* RFID, kecepatan, Wi-Fi |
| ***Manual*** | Jog di layar (*hold-to-move*, *watchdog* 400 ms); *tow-pin* naik/turun manual |
| ***IO Monitor*** | *Bit* input/output langsung berlabel — diagnostik |
| ***Parameters*** | *Toggle* runtime (sensor, RFID, *LiDAR-Slow/Stop*) + kecepatan yang dapat diubah |
| ***Mappings*** | Program rute RFID — *tag* → *rule*, *ADD/EDIT/SAVE/APPLY*, *export/import* |
| ***Errors*** | Log kejadian & gangguan — cek di sini dulu jika tak mau jalan |

### Mapping rule types
*End cycle* · *Start cycle* · *Slow zone* (*tag* berpasangan) · *Timed pause* · *Pause-until-tag* ·
*Pulse pusher*.

---

### TN route tags
| *Tag* | Aksi |
|---|---|
| **10** | *Home*: tiba → berhenti, *pin* **turun**, akhiri siklus · berangkat → berhenti, *pin* **naik**, jalan |
| **20** | Masuk *slow zone* (tikungan) |
| **30** | Kembali cepat |
| **40** | Berhenti, *pin* **turun** (lepas), lanjut |
| **50** | Berhenti, *pin* **naik** (kopel), lanjut |

### Safety responses
*E-stop* → berhenti + rem dilepas (bisa didorong), *reset* untuk pulih · LiDAR zona dalam →
berhenti, 2 dtk, lanjut · *Bumper* → berhenti, 2 dtk, lanjut · *Tape* hilang → berhenti, posisikan
ulang · *running horn* vs *alarm horn*.

---

### Run a cycle
Jalur bersih → selektor *Auto* → pastikan *Armed* + *tape* terdeteksi → *Start* → amati awal gerak.
Di luar *tape* = *Start* ditolak (perbaiki posisi, jangan tekan ulang).

### Charging
Parkir → cek *display* baterai → isi bila **di bawah 48 V** → cabut *Anderson* AGV → sambung
*charger* → **kedip merah = mengisi, kedip hijau = selesai** → sambung ulang AGV → periksa kabel.

### Daily check
Bersihkan area sensor/RFID · *tape* & *tag* rata/bersih · loop bersih · *E-stop* & tombol merespons
(*IO Monitor*) · tegangan baterai.

### If it won't run
Selektor *Auto*? *Armed*? **_Tape_ terdeteksi?** → **baca halaman *Errors*** sebelum menekan *Start*
lagi. Hal terkait keselamatan atau tak jelas → **eskalasi ke *engineering* / IGP.**
