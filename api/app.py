from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
import os
import threading
import queue
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)

MAX_POINTS = 100  # 最大数据点数
WRITE_INTERVAL = 10  # 每10秒写入一次数据库
data_queue = queue.Queue()  # 用于存储待写入的数据

# Supabase客户端初始化
def get_supabase_client():
    """创建Supabase客户端连接"""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise Exception("Supabase配置未设置")
    return create_client(url, key)

# 数据库初始化
def init_db():
    supabase = get_supabase_client()
    # Supabase会自动创建表，所以这里不需要CREATE TABLE

def batch_write_worker():
    """后台线程：定期将数据批量写入数据库"""
    batch_data = []
    while True:
        try:
            # 收集数据直到队列为空或达到写入间隔
            try:
                while True:
                    data = data_queue.get_nowait()
                    batch_data.append({
                        'timestamp': data['timestamp'],
                        'download': data['download'],
                        'upload': data['upload']
                    })
            except queue.Empty:
                pass

            # 如果有数据要写入
            if batch_data:
                supabase = get_supabase_client()
                response = supabase.table('speed_data').insert(batch_data).execute()
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

    try:
        supabase = get_supabase_client()
        response = (
            supabase.table('speed_data')
            .select('*')
            .gte('timestamp', start_time)
            .order('timestamp', desc=False)
            .execute()
        )

        data = response.data
        if not data:
            return jsonify({
                "timestamps": [],
                "download": [],
                "upload": []
            })

        # 分离数据
        timestamps = [row['timestamp'] for row in data]
        downloads = [row['download'] for row in data]
        uploads = [row['upload'] for row in data]

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
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/')
def serve_frontend():
    # 检查数据库连接
    try:
        supabase = get_supabase_client()
        response = supabase.table('speed_data').select('count', count='exact').execute()
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