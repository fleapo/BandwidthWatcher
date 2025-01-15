from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
import os
import threading
import queue
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)

MAX_POINTS = 120  # 最大数据点数
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

def downsample_data(timestamps, downloads, uploads, timerange, max_points):
    """基于固定时间点的数据采样"""
    if not timestamps:
        return [], [], []

    now = int(datetime.now().timestamp())

    # 根据时间范围确定总时长（秒）和采样间隔
    if timerange == 'minute':
        duration = 60
    elif timerange == 'tenminutes':
        duration = 600
    elif timerange == 'hour':
        duration = 3600
    elif timerange == 'day':
        duration = 86400
    else:  # week
        duration = 604800

    interval = duration / max_points  # 采样间隔
    start_time = now - duration

    # 创建固定的时间点
    fixed_timestamps = []
    fixed_downloads = []
    fixed_uploads = []

    # 对每个时间点进行采样
    for i in range(max_points):
        point_time = start_time + (i * interval)
        # 扩大时间窗口的范围，前后各延伸interval的一小部分
        window_start = point_time - (interval * 0.1)  # 向前延伸10%
        window_end = point_time + (interval * 1.1)    # 向后延伸10%

        # 查找这个时间窗口内的所有数据点
        window_data = [(ts, dl, ul) for ts, dl, ul in zip(timestamps, downloads, uploads)
                      if window_start <= ts < window_end]

        if window_data:
            # 如果有数据，计算平均值
            window_timestamps, window_downloads, window_uploads = zip(*window_data)
            # 使用最接近point_time的时间戳
            closest_ts = min(window_timestamps, key=lambda x: abs(x - point_time))
            fixed_timestamps.append(int(closest_ts))
            fixed_downloads.append(int(sum(window_downloads) / len(window_downloads)))
            fixed_uploads.append(int(sum(window_uploads) / len(window_uploads)))
        else:
            # 如果没有数据，添加时间点但值为null
            fixed_timestamps.append(int(point_time))
            fixed_downloads.append(None)
            fixed_uploads.append(None)

    return fixed_timestamps, fixed_downloads, fixed_uploads

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
        hostname = request.args.get('hostname')
        query = (
            supabase.table('speed_data')
            .select('*')
            .gte('timestamp', start_time)
        )

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

        # 使用基于固定时间点的采样
        timestamps, downloads, uploads = downsample_data(timestamps, downloads, uploads, timerange, MAX_POINTS)

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