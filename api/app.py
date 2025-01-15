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
    try:
        supabase = get_supabase_client()
        # 使用SQL创建表 暂时不需要了，因为已经手动创建了
        # response = supabase.rpc(
        #     'create_speed_data_table',
        #     {
        #         'query': '''
        #         CREATE TABLE IF NOT EXISTS public.speed_data (
        #             id SERIAL PRIMARY KEY,
        #             timestamp BIGINT NOT NULL,
        #             download INTEGER NOT NULL,
        #             upload INTEGER NOT NULL,
        #             hostname VARCHAR(255),
        #             created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
        #         );
        #         CREATE INDEX IF NOT EXISTS speed_data_timestamp_idx ON public.speed_data (timestamp);
        #         CREATE INDEX IF NOT EXISTS speed_data_hostname_idx ON public.speed_data (hostname);
        #         '''
        #     }
        # ).execute()
        print("数据库表初始化成功")
    except Exception as e:
        print(f"数据库表初始化失败: {str(e)}")

# 确保调用初始化函数
init_db()

def batch_write_worker():
    """后台线程：定期将数据批量写入数据库"""
    batch_data = []
    while True:
        try:
            try:
                while True:
                    data = data_queue.get_nowait()
                    batch_data.append({
                        'timestamp': data['timestamp'],
                        'download': data['download'],
                        'upload': data['upload'],
                        'hostname': data.get('hostname')  # 使用get方法使hostname为可选
                    })
            except queue.Empty:
                pass

            if batch_data:
                supabase = get_supabase_client()
                response = supabase.table('speed_data').insert(batch_data).execute()
                print(f"Batch wrote {len(batch_data)} records to database")
                batch_data = []

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
        # 添加hostname参数支持
        hostname = request.args.get('hostname')
        query = (
            supabase.table('speed_data')
            .select('*')
            .gte('timestamp', start_time)
        )

        # 如果指定了hostname，添加过滤条件
        if hostname:
            query = query.eq('hostname', hostname)

        response = query.order('timestamp', desc=False).execute()

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
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    import dotenv
    dotenv.load_dotenv()
    app.run(host='0.0.0.0', port=5000, debug=True)