import sqlite3
import os
import json
import logging
from flask import Flask, render_template, jsonify, request
from flask_apscheduler import APScheduler
from datetime import datetime, timedelta
from pywebpush import webpush, WebPushException
from dotenv import load_dotenv

# Ortam değişkenlerini yükle
load_dotenv()

# =========================================================================
# TELEMETRY VE MERKEZİ LOGLAMA ALTYAPISI
# =========================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("NodeTerminal")

app = Flask(__name__)

# =========================================================================
# ORTAM DEĞİŞKENLERİ VE YAPILANDIRMA
# =========================================================================
app.secret_key = os.getenv("SECRET_KEY", "default-fallback-node-secure-key")
DB_NAME = os.getenv("DB_NAME", "node.db")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY")
VAPID_CLAIMS = {"sub": os.getenv("VAPID_CLAIM_SUB", "mailto:admin@node.sys")}
DEBUG_MODE = os.getenv("FLASK_DEBUG", "False").lower() == "true"
PORT = int(os.getenv("PORT", 5000))

if not VAPID_PRIVATE_KEY:
    logger.critical("[CRITICAL ERROR]: VAPID_PRIVATE_KEY bulunamadı! Anlık bildirim motoru çalışmayacak.")

# =========================================================================
# ARKA PLAN ZAMANLAYICI (FLASK-APSCHEDULER)
# =========================================================================
scheduler = APScheduler()
app.config['SCHEDULER_API_ENABLED'] = True
scheduler.init_app(app)
scheduler.start()

# =========================================================================
# AKILLI VERİTABANI İLİŞKİLERİ VE MİGRASYON MOTORU
# =========================================================================
def init_and_migrate_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, timestamp TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, timestamp TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, sub_json TEXT UNIQUE)''')
        c.execute('''CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, date TEXT, time TEXT, category TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS balances (user_id TEXT PRIMARY KEY, score INTEGER DEFAULT 50)''')
        
        c.execute("PRAGMA table_info(events)")
        columns = [column[1] for column in c.fetchall()]
        if "notified" not in columns:
            c.execute("ALTER TABLE events ADD COLUMN notified INTEGER DEFAULT 0")
            logger.info("[MIGRATION]: 'events' tablosuna 'notified' eklendi.")
            
        c.execute("INSERT OR IGNORE INTO events (title, date, time, category, notified) VALUES ('Sistem Testi', '2026-10-10', '12:00', 'Buluşma', 0)")
        
    logger.info("[SİSTEM]: Güvenli SQL veritabanı mimarisi doğrulandı.")

init_and_migrate_db()

# =========================================================================
# ASENKRON SEÇİCİ WEB PUSH BİLDİRİM MOTORU
# =========================================================================
def send_covert_push(title, body, exclude_endpoint=None):
    if not VAPID_PRIVATE_KEY:
        logger.error("[PUSH ABORTED]: Özel anahtar eksik.")
        return

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id, sub_json FROM subscriptions")
        rows = c.fetchall()

    for row_id, sub_json in rows:
        try:
            subscription_info = json.loads(sub_json)
            
            if exclude_endpoint and exclude_endpoint in subscription_info.get('endpoint', ''):
                continue
                
            webpush(
                subscription_info=subscription_info,
                data=json.dumps({"title": title, "body": body}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
            logger.info(f"[PUSH SUCCESS]: Paket ID {row_id} iletildi.")
        except WebPushException as ex:
            if ex.response is not None and ex.response.status_code in [404, 410]:
                with sqlite3.connect(DB_NAME) as conn_clean:
                    cursor_clean = conn_clean.cursor()
                    cursor_clean.execute("DELETE FROM subscriptions WHERE id = ?", (row_id,))
                logger.warning(f"[CLEANUP]: Ölü jeton (ID: {row_id}) temizlendi.")
        except Exception as e:
            logger.error(f"[PUSH FATAL]: Hata (ID: {row_id}): {e}")

# =========================================================================
# CRON GÖREVİ: DAKİKALIK AJANDA TARAYICI
# =========================================================================
@scheduler.task('interval', id='check_calendar_job', minutes=1, misfire_grace_time=900)
def check_calendar():
    with app.app_context():
        now = datetime.now()
        one_hour_later = now + timedelta(hours=1)
        target_date = one_hour_later.strftime("%Y-%m-%d")
        target_time = one_hour_later.strftime("%H:%M")

        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("SELECT id, title, time FROM events WHERE date = ? AND time = ? AND notified = 0", (target_date, target_time))
            upcoming_events = c.fetchall()

            for event in upcoming_events:
                event_id, title, event_time = event
                logger.info(f"[SCHEDULER]: Sinyal tetiklendi: {title}")
                send_covert_push("Zaman Çizelgesi Uyarısı", f"'{title}' planına son 1 saat kaldı! Saat: {event_time}")
                c.execute("UPDATE events SET notified = 1 WHERE id = ?", (event_id,))

# =========================================================================
# SPA SAYFA ROTALARI
# =========================================================================
@app.route('/')
def home(): return render_template('index.html')

@app.route('/yemek')
def yemek(): return render_template('yemek.html')

@app.route('/notlar')
def notlar(): return render_template('notlar.html')

@app.route('/istatistik')
def istatistik(): return render_template('istatistik.html')

@app.route('/ayarlar')
def ayarlar(): return render_template('ayarlar.html')

@app.route('/takvim')
def takvim(): return render_template('takvim.html')

# =========================================================================
# API ROTALARI
# =========================================================================
@app.route('/api/subscribe', methods=['POST'])
def save_subscription():
    sub_data = request.get_json()
    if not sub_data: return jsonify({"status": "error", "message": "Geçersiz veri"}), 400
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO subscriptions (sub_json) VALUES (?)", (json.dumps(sub_data),))
    return jsonify({"status": "success"})

@app.route('/api/balance', methods=['GET', 'POST'])
def handle_balance():
    user_id = request.args.get('user_id', 'User_A')
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO balances (user_id, score) VALUES (?, 50)", (user_id,))
        
        if request.method == 'POST':
            data = request.get_json() or {}
            amount = data.get('amount', 0)
            c.execute("UPDATE balances SET score = score + ? WHERE user_id = ?", (amount, user_id))
            return jsonify({"status": "success"})
            
        c.execute("SELECT score FROM balances WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        score = row[0] if row else 50
    return jsonify({"score": score})

@app.route('/api/trigger', methods=['POST'])
def trigger_action():
    data = request.get_json() or {}
    action_name = data.get('action', 'Bilinmeyen Sinyal')
    user_id = data.get('user_id', 'User_A')
    my_endpoint = data.get('endpoint', None)
    time_now = datetime.now().strftime("%H:%M - %d.%m.%Y")
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO signals (action, timestamp) VALUES (?, ?)", (action_name, time_now))
        c.execute("INSERT OR IGNORE INTO balances (user_id, score) VALUES (?, 50)", (user_id,))
        c.execute("UPDATE balances SET score = score + 10 WHERE user_id = ?", (user_id,))
        
    logger.info(f"[TELEMETRY]: [{user_id}] eylemi ateşledi: '{action_name}'")
    send_covert_push("Terminal Sinyali Alındı", f"[{action_name}] paketi gönderildi.", exclude_endpoint=my_endpoint)
    return jsonify({"status": "success"})

@app.route('/api/buy', methods=['POST'])
def buy_item():
    data = request.get_json() or {}
    user_id = data.get('user_id', 'User_A')
    item_name = data.get('item_name')
    price = data.get('price', 0)
    my_endpoint = data.get('endpoint', None)

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO balances (user_id, score) VALUES (?, 50)", (user_id,))
        c.execute("SELECT score FROM balances WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        current_score = row[0] if row else 0
        
        if current_score >= price:
            c.execute("UPDATE balances SET score = score - ? WHERE user_id = ?", (price, user_id))
            logger.info(f"[STORE PURCHASE]: [{user_id}] '{item_name}' satın aldı.")
            send_covert_push("Mağaza İşlemi", f"Partneriniz '{item_name}' kullandı!", exclude_endpoint=my_endpoint)
            return jsonify({"status": "success", "new_score": current_score - price})
        else:
            return jsonify({"status": "error", "message": "Yetersiz bakiye"}), 400

@app.route('/api/notes', methods=['GET', 'POST'])
def handle_notes():
    if request.method == 'POST':
        data = request.get_json() or {}
        content = data.get("content")
        my_endpoint = data.get('endpoint', None)
        time_now = datetime.now().strftime("%H:%M - %d.%m.%Y")
        
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO notes (content, timestamp) VALUES (?, ?)", (content, time_now))
            
        logger.info("[DATA ENGINE]: Not paneli güncellendi.")
        send_covert_push("Buzdolabı Güncellendi", "Sanal buzdolabına not bırakıldı.", exclude_endpoint=my_endpoint)
        return jsonify({"status": "success"})
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT content, timestamp FROM notes ORDER BY id DESC")
        notes = [{"content": row[0], "time": row[1]} for row in c.fetchall()]
    return jsonify(notes)

@app.route('/api/events', methods=['GET', 'POST'])
def handle_events():
    if request.method == 'POST':
        data = request.get_json() or {}
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO events (title, date, time, category, notified) VALUES (?, ?, ?, ?, 0)", 
                      (data.get("title"), data.get("date"), data.get("time"), data.get("category")))
        logger.info(f"[AJANDA]: Yeni veri yazıldı: {data.get('title')}")
        return jsonify({"status": "success"})
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT title, date, time, category FROM events ORDER BY date ASC, time ASC")
        events = [{"title": row[0], "date": row[1], "time": row[2], "category": row[3]} for row in c.fetchall()]
    return jsonify(events)

@app.route('/api/stats', methods=['GET'])
def get_stats():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT action, COUNT(*) FROM signals GROUP BY action")
        data = c.fetchall()
    stats = {item[0]: item[1] for item in data}
    return jsonify(stats)

# =========================================================================
# SUNUCU BAŞLATMA SOKETİ
# =========================================================================
if __name__ == '__main__':
    # Gunicorn kullanıldığında bu blok çalışmaz (Güvenli Üretim Modu)
    app.run(debug=DEBUG_MODE, port=PORT, host='0.0.0.0', use_reloader=False)