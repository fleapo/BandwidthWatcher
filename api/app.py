from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, timedelta
import os
import threading
import queue

app = Flask(__name__)
CORS(app)

MAX_POINTS = 100  # 最大数据点数
WRITE_INTERVAL = 10  # 每10秒写入一次数据库
data_queue = queue.Queue()  # 用于存储待写入的数据

# 数据库连接配置
DB_CONFIG = {
    'dbname': os.environ.get('POSTGRES_DATABASE'),
    'user': os.environ.get('POSTGRES_USER'),
    'password': os.environ.get('POSTGRES_PASSWORD'),
    'host': os.environ.get('POSTGRES_HOST'),
    'port': '5432',
    'sslmode': 'require'
}

def get_db_connection():
    """创建数据库连接"""
    return psycopg2.connect(**DB_CONFIG)

# 数据库初始化
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS speed_data
                   (timestamp BIGINT, download INTEGER, upload INTEGER)''')
    conn.commit()
    cur.close()
    conn.close()

init_db()

def batch_write_worker():
    """后台线程：定期将数据批量写入数据库"""
    batch_data = []
    while True:
        try:
            # 收集数据直到队列为空或达到写入间隔
            try:
                while True:
                    data = data_queue.get_nowait()
                    batch_data.append((data['timestamp'], data['download'], data['upload']))
            except queue.Empty:
                pass

            # 如果有数据要写入
            if batch_data:
                conn = get_db_connection()
                cur = conn.cursor()
                execute_values(cur,
                    'INSERT INTO speed_data (timestamp, download, upload) VALUES %s',
                    batch_data)
                conn.commit()
                cur.close()
                conn.close()
                print(f"Batch wrote {len(batch_data)} records to database")
                batch_data = []  # 清空批次数据

            # 等待下一个写入间隔
            threading.Event().wait(WRITE_INTERVAL)
        except Exception as e:
            print(f"Error in batch write worker: {e}")
            threading.Event().wait(5)

# 启动后台写入线程
write_thread = threading.Thread(target=batch_write_worker, daemon=True)
write_thread.start()

@app.route('/speed', methods=['POST'])
def record_speed():
    data = request.json
    data_queue.put(data)
    return jsonify({"status": "success"})

@app.route('/data/<timerange>')
def get_data(timerange):
    conn = get_db_connection()
    cur = conn.cursor()

    now = datetime.now()
    if timerange == 'minute':
        start_time = int((now - timedelta(minutes=1)).timestamp())
    elif timerange == 'tenminutes':
        start_time = int((now - timedelta(minutes=10)).timestamp())
    elif timerange == 'hour':
        start_time = int((now - timedelta(hours=1)).timestamp())
    elif timerange == 'day':
        start_time = int((now - timedelta(days=1)).timestamp())
    elif timerange == 'week':
        start_time = int((now - timedelta(weeks=1)).timestamp())
    else:
        return jsonify({"error": "Invalid timerange"})

    cur.execute('SELECT * FROM speed_data WHERE timestamp >= %s ORDER BY timestamp ASC', (start_time,))
    data = cur.fetchall()
    cur.close()
    conn.close()

    if not data:
        return jsonify({
            "timestamps": [],
            "download": [],
            "upload": []
        })

    # 分离数据
    timestamps = [row[0] for row in data]
    downloads = [row[1] for row in data]
    uploads = [row[2] for row in data]

    # 对数据进行降采样
    if len(timestamps) > MAX_POINTS:
        interval = len(timestamps) // MAX_POINTS
        timestamps = timestamps[::interval]
        downloads = downloads[::interval]
        uploads = uploads[::interval]

    result = {
        "timestamps": timestamps,
        "download": downloads,
        "upload": uploads
    }

    return jsonify(result)

@app.route('/')
def serve_frontend():
    # 检查数据库连接
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT 1')
        cur.close()
        conn.close()
        db_status = "数据库连接正常"
    except Exception as e:
        db_status = f"数据库连接错误: {str(e)}"

    # 返回包含数据库状态的HTML页面
    return f'''
    <html>
        <head>
            <title>Speed Test Monitor</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 40px;
                }}
                .status {{
                    padding: 10px;
                    border-radius: 4px;
                    margin: 20px 0;
                    background-color: #f0f0f0;
                }}
            </style>
        </head>
        <body>
            <h1>Speed Test Monitor</h1>
            <div class="status">
                <h2>系统状态</h2>
                <p>数据库状态: {db_status}</p>
            </div>
        </body>
    </html>
    '''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)