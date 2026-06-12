# AGV I-PRIME 2.0 — Operation & Maintenance Training

<!-- ============================================================= -->
<!-- INSTRUCTIONS FOR THE POWERPOINT AI AGENT — READ FIRST          -->
<!-- ============================================================= -->

## ⚙️ BRIEF FOR THE SLIDE-BUILDING AGENT

**Your job:** turn this markdown into a finished PowerPoint deck. One `## Slide N` block = one
slide. Build all 25 slides in order.

**Deck facts**
- Topic: operation & basic maintenance of the **AGV I-PRIME 2.0** demo unit ("TN").
- Audience: *sales* & *engineering* staff who will both operate and promote the unit. They know
  what AGVs are. Keep it professional and technical, not childish.
- Duration: 45-minute in-class session; a separate hands-on session follows.
- Aspect ratio: **16:9**. ~25 content slides.

**Language & typography rules (IMPORTANT — follow exactly)**
- Body text is **Bahasa Indonesia**.
- **Slide titles stay in English** exactly as written in each `Title:` field — do not translate them.
- English technical / AGV / UI terms are kept in English and written in *italics* in this file
  (e.g. *magnetic tape*, *Start*, *Armed*, *slow zone*, *tow-pin*). **Preserve them as italic
  English text on the slides — do not translate and do not un-italicize.**
- Common acronyms are kept upright (not italic) and unchanged: AGV, RFID, HMI, LiDAR, BLDC, LFP,
  RPM, Wi-Fi, SSID. Keep them as-is.
- The Title block (Slide 1) stays fully English.

**How to read each slide block**
- `Title:` → the slide title (English, keep verbatim).
- `Layout:` → use this PowerPoint layout/template (Title, Section, Bullets, Two-column, Table,
  Comparison, Diagram, Callout, Internal-warning).
- `Visual:` → a suggestion for an icon / image placeholder / diagram. If you cannot generate an
  image, insert a labelled placeholder box. Do not invent product photos.
- `Body:` → the on-slide content (bullets / table / diagram). Render tables as styled tables and
  the ASCII diagrams as simple graphics or shapes where you can.
- `Notes:` → put this in the slide's **speaker-notes** pane (not on the slide). It holds timing
  and "💬 *talking point*" lines for the trainer.

**Design system**
- Industrial / clean. Suggested palette: deep blue + dark slate, one accent (orange or teal),
  light background. Manufacturer: **PT Inti Ganda Perdana (IGP)** — leave room for a logo top-left.
- Consistent footer: "AGV I-PRIME 2.0 — Demo Unit TN · IGP" + slide number.
- Use a clear sans-serif (e.g. Calibri / Inter / Segoe UI). Generous spacing; max ~6 bullets/slide.
- ⚠️ marks a safety callout → render as a colored callout box.
- **Slide 12 is INTERNAL** — give it a distinct red/warning style and a visible "INTERNAL" ribbon;
  its content must look clearly different from customer-safe slides.

<!-- ============================================================= -->
<!-- SLIDES                                                        -->
<!-- ============================================================= -->

---

## Slide 1

**Title:** AGV I-PRIME 2.0
**Layout:** Title slide
**Visual:** Large product silhouette or IGP logo; clean industrial hero background.
**Body (English — title block, do not translate):**
- # AGV I-PRIME 2.0
- ### Operation & Maintenance — Demo Unit "TN"
- PT Inti Ganda Perdana (IGP)
- *Magnetic-tape guided · RFID route logic · 1-ton towing*

**Notes:** Pembuka, ~1 menit. Sesi 45 menit di kelas; sesi *hands-on* terpisah menyusul.

---

## Slide 2

**Title:** Today's Session
**Layout:** Bullets (agenda)
**Visual:** Simple numbered agenda / timeline strip.
**Body:**
- **Tujuan:** setelah sesi ini Anda dapat menyalakan, mengoperasikan, memantau, dan merawat dasar
  unit ini — serta menjelaskannya dengan percaya diri di depan calon pelanggan.
- Agenda:
  1. Apa itu unit ini & cara kerjanya (8 mnt)
  2. Mekanisme, *mode*, & kontrol fisik (10 mnt)
  3. Perilaku keselamatan (5 mnt)
  4. HMI — halaman demi halaman (13 mnt)
  5. Rute & peta *tag* (3 mnt)
  6. Prosedur & perawatan sederhana (5 mnt)
  7. Penutup + cakupan sesi *hands-on* (1 mnt)

**Notes:** ~2 menit. Tekankan bahwa mengemudikan unit secara langsung ada di sesi *hands-on* terpisah.

---

## Slide 3

**Title:** What the AGV I-PRIME 2.0 Is
**Layout:** Bullets
**Visual:** Icon row: tape line, RFID tag, two-wheel drive, trolley.
**Body:**
- AGV dengan penggerak *differential drive* (dua roda yang digerakkan secara terpisah).
- Mengikuti loop *magnetic tape* di lantai; membaca *tag* RFID untuk aksi berdasarkan lokasi.
- **Menarik *trolley*** dengan mengunci batang *trolley* memakai *tow-pin* vertikal.
- Dioperasikan lewat **panel fisik** + **HMI web lokal** (ponsel/tablet/laptop via Wi-Fi).
- Unit ini adalah **konfigurasi demo ("TN")**: loop kecil berbentuk stadion, fungsi *towing* saja.

**Notes:** ~2 menit. 💬 *Talking point:* "Ini kendaraan rute-tetap yang andal — Anda definisikan
jalurnya dengan *tape* dan *tag*, lalu ia mengulanginya tanpa pengemudi."

---

## Slide 4

**Title:** How It Works, in One Picture
**Layout:** Diagram
**Visual:** Render this stack as 4 connected blocks with a "+" between them.
**Body (diagram):**
```
   MAGNETIC TAPE  →  menjaga AGV di garis (referensi kemudi)
        +
   RFID TAGS      →  memberi tahu APA yang dilakukan & DI MANA
                     (pelan, berhenti, kopel, akhiri siklus)
        +
   DIFFERENTIAL   →  dua roda dengan kecepatan berbeda = jalan + belok
   DRIVE
        +
   TOW-PIN        →  naik = jepit batang trolley, turun = lepas
```
- Inti: AGV **tidak** memilih jalurnya sendiri — **rute adalah programnya.** *Tape* = garis,
  *tag* = instruksi di sepanjang garis.

**Notes:** ~2 menit. Konsep paling penting di seluruh sesi.

---

## Slide 5

**Title:** The Unit at a Glance
**Layout:** Table (two columns: parameter / value)
**Visual:** Spec sheet style; small dimension diagram if possible.
**Body:**
| Parameter | Nilai |
|---|---|
| Kapasitas *towing* | **1 ton** |
| Ukuran (P × L) | 1136 × 503 mm |
| Tinggi | 245 mm (*pin* turun) / 295 mm (*pin* naik) |
| Motor penggerak | BLDC **400 W**, 0–3000 RPM, *gearbox* 1:30 |
| Roda | Ø180 mm |
| Kecepatan *auto* | hingga ~0,4 m/s |
| Baterai | **48 V · 105 Ah · LFP (LiFePO₄)** |
| Pengisian | hingga 30 A, ~4–5 jam, ≈1 *shift* per pengisian |
| Pemandu | *magnetic tape*, 30 mm, kutub utara di atas |
| *Tag* | **UHF RFID** |

**Notes:** ~2 menit.

---

## Slide 6

**Title:** Major Components — Know Them by Name
**Layout:** Two-column
**Visual:** Labelled callout photo/diagram placeholder of the unit with leader lines.
**Body:**
- **Panel & kontrol:** *Emergency stop* · selektor *Manual/Auto* · *Start* · *Reset/Stop* ·
  tombol arah (*Fwd/Rev/Left/Right*).
- **Di kendaraan:** *tow-pin* (*pusher*) · sensor pemandu magnetik · *reader* RFID · *bumper*
  benturan depan · LiDAR · *horn*/buzzer · sakelar daya utama · *port* pengisian · *Anderson plug*
  baterai · *display* tegangan baterai.
- ⚠️ Tegangan baterai dibaca di **display baterai**, bukan di HMI.

**Notes:** ~2 menit.

---

## Slide 7

**Title:** Navigation: Tape + RFID
**Layout:** Two-column
**Visual:** Left = tape/steering icon; right = RFID tag icon.
**Body:**
- **_Magnetic tape_ (kemudi):**
  - Lebar 30 mm, kutub utara menghadap atas, terpasang menerus mengelilingi loop.
  - Sensor membaca simpangan lateral; *controller* mengemudi agar tetap di tengah.
  - Harus rata, bersih, menerus — celah > ~10 mm berisiko *stop* karena *tape* hilang.
- **_RFID tags_ (instruksi):**
  - *Tag* UHF dipasang di titik tempat AGV harus beraksi.
  - Tiap *tag* punya **nomor desimal** yang dipetakan ke sebuah *rule* (pelan, berhenti, kopel,
    akhiri siklus…).
  - *Tag* adalah "program rute" yang dapat diubah pelanggan — lewat HMI, tanpa *code*.

**Notes:** ~2 menit.

---

## Slide 8

**Title:** The Tow-Pin (Pusher) Mechanism
**Layout:** Bullets + safety callout
**Visual:** Simple up/down arrow animation of a pin engaging a trolley bar.
**Body:**
- *Shaft* vertikal yang dinaik-turunkan oleh *linear actuator* kerja-ganda.
- ***Pin* NAIK (*extend*)** → menjepit batang *trolley* = **kopel / ambil**.
- ***Pin* TURUN (*retract*)** → melepas batang = **lepas / turunkan**.
- Gerakan **berbasis waktu** (mis. 5 detik) — tanpa sensor posisi; andalkan timing + cek visual.
- Digerakkan oleh *rule* RFID saat *auto*, atau tombol *hold-to-move* di halaman *Manual* HMI.
- ⚠️ Bahaya jepit — jauhkan tangan; *pin* bisa bergerak saat *auto* meski AGV berhenti.

**Notes:** ~2 menit. "*Pusher*" dan "*tow-pin*" merujuk mekanisme yang sama di unit TN.

---

## Slide 9

**Title:** Operating Modes
**Layout:** Table
**Visual:** State diagram: Manual ↔ Armed → Running, dengan Emergency di samping.
**Body:**
| *Mode* | Arti |
|---|---|
| ***Manual*** | Operator mengemudi dengan tombol / HMI: *Fwd*, **_Rev_**, *Left*, *Right*. |
| ***Armed*** | Siap *auto*, menunggu. Tidak bergerak. |
| ***Running*** | Mengikuti *tape* maju, menjalankan *rule* *tag*. |
| ***Emergency*** | *E-stop* ditekan; harus di-*reset* sebelum dipakai lagi. |

- Selektor *Manual/Auto* menentukan wewenang dasar.
- **Mundur hanya aksi *mode Manual*** di unit ini — tidak ada *auto* mundur. *Auto* hanya maju.

**Notes:** ~2 menit. Penting: *auto* di TN forward-only.

---

## Slide 10

**Title:** Physical Controls
**Layout:** Bullets
**Visual:** Photo/diagram of the control panel with each control labelled.
**Body:**
- ***Emergency stop*** — hanya untuk kondisi tidak aman (bukan berhenti rutin).
- **Selektor *Manual/Auto*** — *Manual* = Anda mengemudi; *Auto* = masuk *Armed* (siap *auto*).
- ***Start*** — memulai *auto* dari *Armed*. Syarat: selektor *Auto*, *Armed*, **_tape_ terdeteksi**,
  jalur bersih.
  - Di luar *tape*, *Start* **ditolak** — itu disengaja; perbaiki posisi, jangan tekan berulang.
- ***Reset/Stop*** — berhenti normal kembali ke *Armed*; juga konfirmasi pemulihan setelah *E-stop*.
- **Tombol arah** — *hold-to-move* saat *Manual*; lepas = berhenti.

**Notes:** ~2 menit.

---

## Slide 11

**Title:** Safety Behavior
**Layout:** Table + callout
**Visual:** Row of safety icons (E-stop, LiDAR, bumper, tape).
**Body:**
| Kejadian | Yang dilakukan AGV | Pemulihan |
|---|---|---|
| ***E-stop*** | Berhenti, rem dilepas (bisa didorong), masuk *Emergency* | Amankan → lepas *E-stop* → **_Reset_** |
| **LiDAR — zona tengah** | Memperlambat (jika *LiDAR-Slow* aktif) | Otomatis saat bersih |
| **LiDAR — zona dalam** | *Protective stop* (jika *LiDAR-Stop* aktif) | Bersih, tunggu **2 dtk**, lanjut |
| ***Bumper* depan** | *Protective stop* | Lepas kontak, 2 dtk, lanjut; periksa penyebab |
| ***Tape* hilang** | Berhenti, alarm | Posisikan ulang di *tape*, konfirmasi deteksi, mulai lagi |

- *Horn*: ada *running horn* tetap saat *auto*; *alarm horn* terpisah menandai gangguan/*stop*.
- ⚠️ Untuk bahaya nyata, selalu pakai **_E-stop_ fisik** — jangan andalkan HMI untuk keselamatan.

**Notes:** ~3 menit.

---

## Slide 12

**Title:** INTERNAL — Demo Scope & Limits
**Layout:** Internal-warning (distinct red style + "INTERNAL" ribbon)
**Visual:** Warning banner. Make it obviously different from customer-safe slides.
**Body:**
- 🔒 **JANGAN tampilkan slide ini atau sampaikan poin-poin ini ke pelanggan.** Ini agar Anda tahu
  persis apa unit demo ini — dan apa yang bukan — sebelum mempromosikannya.
- Ini **unit demonstrasi**, bukan AGV produksi tersertifikasi keselamatan.
- ***E-stop* dan logika berhenti bersifat *software-mediated*** — tanpa *safety relay* perangkat
  keras independen.
- **LiDAR bukan *scanner* tersertifikasi keselamatan**; *bumper* hanya kontak mekanis sederhana
  (dua pelat, depan saja). Tidak ada proteksi belakang — tapi memang tidak ada *auto* mundur.
- **Tanpa *external watchdog*, tanpa redundansi, roda belum dikalibrasi kecepatan** — wajar untuk
  loop demo.
- Jika pelanggan bertanya soal sertifikasi/kepatuhan standar keselamatan → **jangan berimprovisasi**;
  arahkan ke *engineering*. Promosikan apa yang **bisa dilakukan** unit ini, bukan rating
  keselamatan yang belum dimilikinya.

**Notes:** ~2 menit. Slide khusus internal — lewati saat ada pelanggan di ruangan.

---

## Slide 13

**Title:** HMI: Getting Connected
**Layout:** Numbered steps
**Visual:** Phone/tablet showing a browser; Wi-Fi + URL callouts.
**Body:**
1. Nyalakan AGV; ia otomatis bergabung ke *access point*.
2. Hubungkan perangkat Anda ke Wi-Fi **SSID `agv_field`** (*password* `igp@2026`).
3. *Browser* → **`http://192.168.2.100:5000`**
4. Pastikan halaman **_Home_** termuat dan ter-*update* langsung.

- HMI bersifat **lokal pada AGV** — hanya jalan di jaringannya, di dekat unit.
- Halaman yang macet/basi bukan tombol berhenti. Wi-Fi lemah memengaruhi HMI, **bukan** *E-stop*.

**Notes:** ~2 menit.

---

## Slide 14

**Title:** The HMI: Six Pages
**Layout:** Bullets (nav map)
**Visual:** Mock top-nav bar with the six page names highlighted.
**Body:**
- *Navigation bar* atas: **HOME · MANUAL · IO MONITOR · PARAMETERS · MAPPINGS · ERRORS**
- ***Home*** — status langsung sekilas
- ***Manual*** — *remote* jog di layar
- ***IO Monitor*** — *bit* input/output mentah
- ***Parameters*** — *toggle* runtime & kecepatan
- ***Mappings*** — program rute RFID
- ***Errors*** — log kejadian & gangguan
- Semua orang wajib paham *Home*, *Manual*, *Errors*. *Supervisor* juga: *Parameters*, *Mappings*.

**Notes:** ~1 menit. Slide 15–20 membahas tiap halaman.

---

## Slide 15

**Title:** HMI · Home Page
**Layout:** Bullets
**Visual:** Screenshot placeholder of the Home page with regions circled.
**Body:**
- Gambaran langsung kondisi kendaraan:
  - ***Mode*** (*Manual* / *Armed* / *Running* / *Emergency*)
  - Status **keselamatan/alarm** (*emergency*, *system error*)
  - **Sensor magnetik**: simpangan lateral, *marker* kiri/kanan, RPM/kecepatan per roda, output kemudi
  - **RFID & sekuens**: *tag* terakhir dibaca, sekuens aktif, *speed mode* saat ini (*HIGH*/*SLOW*)
  - **Wi-Fi**: kekuatan sinyal & SSID

**Notes:** ~2 menit. 💬 *Talking point:* "Semua yang dirasakan & diputuskan kendaraan tampil
langsung — bagus untuk demo dan untuk diagnosa rute saat *commissioning*."

---

## Slide 16

**Title:** HMI · Manual Page
**Layout:** Bullets
**Visual:** On-screen jog pad mockup (arrows + pusher buttons).
**Body:**
- *Remote* jog di layar: maju, mundur, belok, diagonal — **_hold-to-move_**.
- Hanya berfungsi di ***mode Manual*.**
- ***Watchdog* keselamatan 400 ms**: jika koneksi putus atau tombol dilepas, gerak berhenti cepat.
- ***Pusher* manual**: tahan tombol untuk menaik-turunkan *tow-pin*.
- Untuk pemosisian & pemulihan — bukan untuk jalan produksi jarak jauh.

**Notes:** ~2 menit.

---

## Slide 17

**Title:** HMI · IO Monitor
**Layout:** Bullets
**Visual:** Grid of green/grey indicator lamps with labels.
**Body:**
- Tampilan langsung tiap **digital input** (tombol, selektor, *E-stop*, zona LiDAR, *bumper*) dan
  **digital output** (arah motor, rem, relay *pusher*, *horn*) dengan **label bahasa manusia.**
- Alat diagnosa — pastikan sebuah tombol, sensor, atau output benar-benar berubah status.
- Hanya-baca: menampilkan kenyataan, tidak meng-*override* keselamatan.

**Notes:** ~1 menit. 💬 *Talking point:* di lapangan berguna membuktikan "sensor melihat objek" vs
"masalah ada di kabel".

---

## Slide 18

**Title:** HMI · Parameters Page
**Layout:** Two-column
**Visual:** Toggle switches column + editable speed fields column.
**Body:**
- **Sakelar ON/OFF runtime** (langsung, terkunci saat *running*/*emergency*):
  - Sensor magnetik (CAN), sekuens RFID, **_LiDAR-Stop_**, **_LiDAR-Slow_**.
- **Nilai kecepatan runtime** (dapat diubah langsung):
  - *Manual* high/slow, *Auto* high/slow/extra-slow, laju akselerasi.
- **Info profil hanya-baca:** konfigurasi tetap unit ini.
- Untuk menyetel perilaku demo tanpa menyentuh *code*.
- Perlakukan sebagai fungsi **_supervisor_** — perubahan memengaruhi cara gerak & titik berhenti.

**Notes:** ~2 menit.

---

## Slide 19

**Title:** HMI · Mappings Page (the Route Program)
**Layout:** Table + actions row
**Visual:** Rule-editor table mockup + button row.
**Body:**
- Tempat perilaku RFID dibuat — **tanpa *code*, semua di layar.** Tiap *rule* menautkan **nomor
  *tag* (desimal)** ke sebuah aksi:

| Tipe *rule* | Fungsi |
|---|---|
| ***End cycle*** | Tiba: berhenti, *pin* turun/lepas, akhiri siklus |
| ***Start cycle*** | Berangkat: berhenti, *pin* naik/kopel, lanjut |
| ***Slow zone*** | *Tag* berpasangan: awal = pelan, akhir = kembali cepat |
| ***Timed pause*** | Berhenti N detik, lanjut otomatis |
| ***Pause until tag*** | Berhenti, tunggu *tag* kedua untuk lanjut |
| ***Pulse pusher*** | Berhenti, gerakkan *pin* (naik/turun) N detik, lanjut |

- *Tools*: *ADD / EDIT / DEL* · *CONFIRM & SAVE* · *APPLY NOW* · *EXPORT / IMPORT / RESTORE* ·
  *history* · inventaris *tag* · daftar *tag* tak terpetakan. *Rule* tersimpan aktif saat masuk
  *Armed* berikutnya (atau *APPLY NOW*).

**Notes:** ~3 menit. Ini halaman "program rute" yang paling sering disesuaikan pelanggan.

---

## Slide 20

**Title:** HMI · Errors Page
**Layout:** Bullets
**Visual:** Log list mockup with severity color tags.
**Body:**
- **Log kejadian & gangguan** bergulir dengan level keparahan (*info* → *warning* → *alarm/critical*).
- Pemeriksaan pertama saat AGV tak mau jalan atau berhenti terus — baca kondisi aktif.
- **_Clear Log_** untuk mengosongkan tampilan.
- Patokan: jika tak mau jalan, cek **_Errors_** sebelum menekan *Start* lagi.

**Notes:** ~1 menit.

---

## Slide 21

**Title:** The TN Route & Tag Map
**Layout:** Diagram + table
**Visual:** Stadium-shaped loop with the five tags placed around it.
**Body:**
- Rute demo berbentuk **loop stadion** (area kecil). *Tag* di unit ini:

| *Tag* (des) | *Rule* di rute |
|---|---|
| **10** | **Home** — tiba: berhenti, *pin* **turun**, akhiri siklus / berangkat: berhenti, *pin* **naik**, jalan |
| **20** | Masuk **_slow zone_** (tikungan / pendekatan) |
| **30** | **Kembali cepat** (keluar *slow zone*) |
| **40** | Berhenti, *pin* **turun** (lepas), lanjut |
| **50** | Berhenti, *pin* **naik** (kopel), lanjut |

**Notes:** ~3 menit. 💬 Cara demo yang rapi: tunjuk tiap *tag* di loop, lalu perhatikan halaman
*Home* menampilkan nomor *tag* dan aksinya saat dilewati.

---

## Slide 22

**Title:** Standard Procedures
**Layout:** Four short blocks (one per procedure)
**Visual:** 4-quadrant card layout.
**Body:**
- **Mulai siklus:** jalur bersih → selektor *Auto* → pastikan *Armed* + *tape* terdeteksi →
  *Start* → amati beberapa detik pertama.
- **Berhenti normal:** *Reset/Stop* → kembali ke *Armed*.
- **Pemulihan *emergency*:** amankan bahaya → lepas *E-stop* → *Reset* → kembali ke *Manual* atau
  *Armed* sesuai selektor.
- **Pengisian:** parkir → cek *display* baterai, isi bila **di bawah 48 V** → cabut *Anderson* dari
  AGV → sambung *charger* → kedip merah = mengisi, kedip hijau = selesai → sambung ulang AGV →
  periksa kabel. (~4–5 jam, LFP.)

**Notes:** ~3 menit.

---

## Slide 23

**Title:** Simple Maintenance
**Layout:** Two-column (Harian / Mingguan)
**Visual:** Checklist icons.
**Body:**
- **Harian / sebelum demo:**
  - Lap sensor magnetik & area *reader* RFID; pastikan *tag*/*tape* rata dan bersih.
  - Periksa loop dari *tape* rusak/terangkat dan halangan.
  - Pastikan *E-stop*, *bumper*, dan tombol arah merespons (lihat *IO Monitor*).
  - Cek tegangan baterai; isi bila di bawah 48 V.
- **Mingguan:**
  - Periksa roda, gerak *tow-pin*, kabel, *Anderson connector* dari kerusakan/bekas panas.
  - Uji jalan singkat + cek sensor di loop.
- ⚠️ Laporkan hal abnormal ke *engineering* — **jangan mem-*bypass* perangkat keselamatan** demi
  melanjutkan demo.

**Notes:** ~2 menit.

---

## Slide 24

**Title:** Troubleshooting Quick Reference
**Layout:** Table
**Visual:** Symptom/diagnosis two-column table.
**Body:**
| Gejala | Pemeriksaan awal |
|---|---|
| Tak mau *Start* | Selektor *Auto*? *Armed*? **_Tape_ terdeteksi?** Cek halaman *Errors*. |
| Berhenti di garis | *Tape* kotor/terangkat/bercelah? Posisikan ulang, konfirmasi deteksi. |
| Berhenti berulang di tengah rute | Halangan LiDAR/*bumper*? Bersihkan jalur; periksa penyebab. |
| Aksi salah di sebuah *tag* | Cek *Mappings*: nomor *tag* benar, *rule* aktif, *master* RFID ON. |
| *Pin* tak mengopel | Batang *trolley* lurus? Durasi *pulse* cukup? Cek visual (tanpa *feedback*). |
| HMI tak termuat | Di *agv_field*? URL benar? Dekatkan ke *access point*. |
| | Hal tak jelas / terkait keselamatan → **eskalasi ke *engineering* / IGP.** |

**Notes:** ~2 menit.

---

## Slide 25

**Title:** Wrap-up & What's Next
**Layout:** Bullets + closing
**Visual:** Recap checklist + "Hands-on next" badge.
**Body:**
- Kini Anda dapat:
  - Menjelaskan unit ini dan bagaimana *tape* + RFID menggerakkan perilakunya.
  - Mengidentifikasi *mode*, kontrol, dan respons keselamatan.
  - Menavigasi keenam halaman HMI dan membaca status langsung.
  - Menjalankan satu siklus, pulih dari *E-stop*, dan mengisi baterai.
- **Sesi *hands-on* mencakup:** menyalakan, jog *manual*, menjalankan loop, mengamati *tag* aktif di
  halaman *Home*, mengubah satu *rule Mappings*, dan prosedur pengisian.
- **Pertanyaan?**

**Notes:** ~1 menit. Tutup, lalu arahkan ke sesi *hands-on*.
