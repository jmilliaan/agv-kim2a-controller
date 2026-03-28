# from flask import Flask, render_template, request, jsonify
# from flask_socketio import SocketIO
# import threading
# import time
# import json
# import config

# app = Flask(__name__)
# socketio = SocketIO(app, cors_allowed_origins="*")

# # Variabel global untuk menyimpan state AGV
# agv_state = None

# @app.route('/')
# def index():
#     """Menyajikan halaman utama dashboard"""
#     return render_template('index.html')

# @app.route('/api/config', methods=['GET', 'POST'])
# def handle_config():
#     """Mengatur konfigurasi PID dan Parameter lewat API"""
#     if request.method == 'POST':
#         new_data = request.json
#         # Simpan ke parameters.json (logika penghematan bisa ditambahkan di sini)
#         print("Menerima konfigurasi baru:", new_data)
#         return jsonify({"status": "success"})
    
#     # Kirim config saat ini
#     return jsonify({
#         "kp": config.KP,
#         "ti": config.TI,
#         "td": config.TD,
#         "speed": config.AUTO_TARGET_HIGH_SPEED
#     })

# def background_emitter():
#     """Thread untuk mengirim data sensor ke browser secara real-time"""
#     while True:
#         if agv_state:
#             # Ambil data dari sensor_queue tanpa memblokir
#             data_to_send = {}
            
#             # Monitoring Sensor Magnet
#             if not agv_state.sensor_queue.empty():
#                 try:
#                     sensor_val = agv_state.sensor_queue.get_nowait()
#                     data_to_send['sensor'] = sensor_val
#                 except: pass
            
#             # Monitoring RFID
#             if not agv_state.rfid_queue.empty():
#                 try:
#                     tag = agv_state.rfid_queue.get_nowait()
#                     data_to_send['rfid'] = tag
#                 except: pass

#             if data_to_send:
#                 socketio.emit('agv_update', data_to_send)
        
#         time.sleep(0.1)  # Kirim setiap 100ms

# def run_server(state):
#     global agv_state
#     agv_state = state
    
#     # Jalankan background thread untuk update data
#     threading.Thread(target=background_emitter, daemon=True).start()
    
#     # Jalankan server (host 0.0.0.0 agar bisa diakses dari perangkat lain di jaringan)
#     socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False)