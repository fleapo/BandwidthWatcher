from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
import os
import threading
import queue
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)

# 配置常量
MAX_POINTS = 120  # 最大数据点数
WRITE_INTERVAL = 10  # 数据库写入间隔（秒）
data_queue = queue.Queue()  # 数据写入队列

def get_supabase_client():
    """创建并返回Supabase客户端连接"""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise Exception("Supabase配置未设置")
    return create_client(url, key)

def init_db():
    """初始化数据库连接"""
    try:
        get_supabase_client()
        print("数据库连接初始化成功")
    except Exception as e:
        print(f"数据库连接初始化失败: {str(e)}")

init_db()

def batch_write_worker():
    """
    后台线程：批量写入数据到数据库
    - 每WRITE_INTERVAL秒执行一次批量写入
    - 将队列中的所有数据一次性写入数据库
    - 出错时等待5秒后继续
    """
    batch_data = []
    while True:
        try:
            # 收集队列中的所有数据
            try:
                while True:
                    data = data_queue.get_nowait()
                    batch_data.append({
                        'timestamp': data['timestamp'],
                        'download': data['download'],
                        'upload': data['upload'],
                        'hostname': data.get('hostname')
                    })
            except queue.Empty:
                pass

            # 如果有数据则写入数据库
            if batch_data:
                supabase = get_supabase_client()
                response = supabase.table('speed_data').insert(batch_data).execute()
                print(f"批量写入 {len(batch_data)} 条记录到数据库")
                batch_data = []

            threading.Event().wait(WRITE_INTERVAL)
        except Exception as e:
            print(f"批量写入出错: {e}")
            threading.Event().wait(5)

# 启动后台写入线程
write_thread = threading.Thread(target=batch_write_worker, daemon=True)
write_thread.start()

@app.route('/speed', methods=['POST'])
def record_speed():
    """接收速度数据并加入写入队列"""
    data = request.json
    data_queue.put(data)
    return jsonify({"status": "success"})

def get_interval(timerange):
    """
    获取不同时间范围的采样间隔（秒）
    - minute: 1秒
    - tenminutes: 5秒
    - hour: 30秒
    - day: 12分钟
    - week: 1小时
    """
    intervals = {
        'minute': 1,
        'tenminutes': 5,
        'hour': 30,
        'day': 720,
        'week': 3600
    }
    return intervals[timerange]

def downsample_data(timestamps, downloads, uploads, timerange, max_points, align_timestamp=None):
    """
    基于固定时间点的数据采样
    参数:
        timestamps: 时间戳列表
        downloads: 下载速度列表
        uploads: 上传速度列表
        timerange: 时间范围
        max_points: 最大点数
        align_timestamp: 对齐时间戳
    返回:
        采样后的时间戳、下载速度和上传速度列表
    """
    if not timestamps:
        return [], [], []

    # 获取采样间隔
    interval = get_interval(timerange)

    # 确保align_timestamp是interval的整数倍
    if align_timestamp:
        now = (align_timestamp // interval) * interval
    else:
        now = (max(timestamps) // interval) * interval

    # 计算时间范围
    durations = {
        'minute': 60,
        'tenminutes': 600,
        'hour': 3600,
        'day': 86400,
        'week': 604800
    }
    duration = durations[timerange]
    start_time = ((now - duration) // interval) * interval

    # 创建时间点到数据的映射
    data_map = {ts: (dl, ul) for ts, dl, ul in zip(timestamps, downloads, uploads)}

    # 对每个单位时间进行采样
    fixed_timestamps = []
    fixed_downloads = []
    fixed_uploads = []

    for point_time in range(int(start_time), int(now + interval), int(interval)):
        # 收集时间窗口内的数据点
        window_data = []
        for ts in timestamps:
            if point_time <= ts < point_time + interval:
                window_data.append((ts, data_map[ts][0], data_map[ts][1]))

        # 如果窗口内有数据，计算平均值；否则使用null
        if window_data:
            window_timestamps, window_downloads, window_uploads = zip(*window_data)
            fixed_timestamps.append(point_time)
            fixed_downloads.append(int(sum(window_downloads) / len(window_downloads)))
            fixed_uploads.append(int(sum(window_uploads) / len(window_uploads)))
        else:
            fixed_timestamps.append(point_time)
            fixed_downloads.append(None)
            fixed_uploads.append(None)

    return fixed_timestamps, fixed_downloads, fixed_uploads

@app.route('/data/<timerange>')
def get_data(timerange):
    """
    获取指定时间范围的完整数据
    参数:
        timerange: 时间范围（minute/tenminutes/hour/day/week）
    查询参数:
        align: 对齐时间戳
        hostname: 主机名（可选）
    """
    try:
        # 获取参数
        align_timestamp = request.args.get('align')
        if align_timestamp:
            align_timestamp = int(align_timestamp)

        # 计算起始时间
        now = datetime.now()
        time_ranges = {
            'minute': timedelta(minutes=1),
            'tenminutes': timedelta(minutes=10),
            'hour': timedelta(hours=1),
            'day': timedelta(days=1),
            'week': timedelta(weeks=1)
        }
        if timerange not in time_ranges:
            return jsonify({"error": "Invalid timerange"})

        start_time = int((now - time_ranges[timerange]).timestamp())

        # 查询数据
        print(f"开始获取{timerange}数据")
        fetch_start_time = datetime.now()

        supabase = get_supabase_client()
        query = (
            supabase.table('speed_data')
            .select('*')
            .gte('timestamp', start_time)
        )

        hostname = request.args.get('hostname')
        if hostname:
            query = query.eq('hostname', hostname)

        response = query.order('timestamp', desc=False).execute()
        data = response.data

        print(f'获取了{len(data)}条数据, 耗时{datetime.now() - fetch_start_time}')

        if not data:
            return jsonify({
                "timestamps": [],
                "download": [],
                "upload": []
            })

        # 处理数据
        timestamps = [row['timestamp'] for row in data]
        downloads = [row['download'] for row in data]
        uploads = [row['upload'] for row in data]

        # 采样数据
        timestamps, downloads, uploads = downsample_data(
            timestamps, downloads, uploads, timerange, MAX_POINTS, align_timestamp
        )

        return jsonify({
            "timestamps": timestamps,
            "download": downloads,
            "upload": uploads
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/latest/<timerange>')
def get_latest_data(timerange):
    """
    获取最新的增量数据
    参数:
        timerange: 时间范围
    查询参数:
        last_timestamp: 上次获取的最后时间戳
        align: 对齐时间戳
        hostname: 主机名（可选）
    """
    try:
        # 获取参数
        last_timestamp = request.args.get('last_timestamp')
        if last_timestamp:
            last_timestamp = int(last_timestamp)

        align_timestamp = request.args.get('align')
        if align_timestamp:
            align_timestamp = int(align_timestamp)

        # 查询数据
        supabase = get_supabase_client()
        query = supabase.table('speed_data').select('*')

        if last_timestamp:
            query = query.gt('timestamp', last_timestamp)

        hostname = request.args.get('hostname')
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

        # 处理数据
        timestamps = [row['timestamp'] for row in data]
        downloads = [row['download'] for row in data]
        uploads = [row['upload'] for row in data]

        # 采样数据
        timestamps, downloads, uploads = downsample_data(
            timestamps, downloads, uploads, timerange, MAX_POINTS, align_timestamp
        )

        return jsonify({
            "timestamps": timestamps,
            "download": downloads,
            "upload": uploads
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/')
def serve_frontend():
    """提供前端页面"""
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    import dotenv
    dotenv.load_dotenv()
    app.run(host='0.0.0.0', port=5000, debug=True)