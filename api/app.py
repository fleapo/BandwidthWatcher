from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
import os
import threading
import queue
from supabase import create_client, Client
import time

app = Flask(__name__)
CORS(app)

# 配置常量
MAX_POINTS = 120  # 最大数据点数
WRITE_INTERVAL = 5  # 延迟写入模式下的等待时间（秒）
IMMEDIATE_WRITE = True  # 是否启用即时写入模式
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
    批量写入工作线程。
    根据配置的写入模式，执行即时写入或延迟写入。
    """
    global data_queue
    while True:
        if IMMEDIATE_WRITE:
            # 即时写入模式：只要有数据就立即写入
            if not data_queue.empty():
                records = []
                try:
                    while not data_queue.empty():
                        record = data_queue.get_nowait()
                        records.append({
                            'timestamp': record['timestamp'],
                            'download': record['download'],
                            'upload': record['upload'],
                            'hostname': record.get('hostname')
                        })
                except queue.Empty:
                    pass

                if records:
                    try:
                        supabase = get_supabase_client()
                        supabase.table('speed_data').insert(records).execute()
                    except Exception as e:
                        print(f"Error writing to database: {e}")
                        # 写入失败时，将数据放回队列
                        for record in records:
                            data_queue.put(record)
            time.sleep(0.1)  # 短暂休眠以避免CPU占用过高
        else:
            # 延迟写入模式：队列空时等待一段时间再写入
            records = []
            last_write_time = time.time()

            try:
                while True:
                    try:
                        # 非阻塞方式获取数据
                        record = data_queue.get_nowait()
                        records.append({
                            'timestamp': record['timestamp'],
                            'download': record['download'],
                            'upload': record['upload'],
                            'hostname': record.get('hostname')
                        })
                    except queue.Empty:
                        current_time = time.time()
                        # 如果有数据且(队列为空或已经等待足够长时间)，则执行写入
                        if records and (data_queue.empty() and current_time - last_write_time >= WRITE_INTERVAL):
                            try:
                                supabase = get_supabase_client()
                                supabase.table('speed_data').insert(records).execute()
                                records = []
                                last_write_time = current_time
                            except Exception as e:
                                print(f"Error writing to database: {e}")
                                # 写入失败时，将数据放回队列
                                for record in records:
                                    data_queue.put(record)
                            break
                        elif not records:
                            # 如果没有数据，短暂休眠
                            time.sleep(0.1)
                        elif current_time - last_write_time >= WRITE_INTERVAL:
                            # 如果等待时间已到，执行写入
                            try:
                                supabase = get_supabase_client()
                                supabase.table('speed_data').insert(records).execute()
                                records = []
                                last_write_time = current_time
                            except Exception as e:
                                print(f"Error writing to database: {e}")
                                # 写入失败时，将数据放回队列
                                for record in records:
                                    data_queue.put(record)
                            break
            except Exception as e:
                print(f"Error in batch write worker: {e}")
                if records:  # 确保任何未写入的记录都放回队列
                    for record in records:
                        data_queue.put(record)

            time.sleep(0.1)  # 短暂休眠以避免CPU占用过高

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

@app.route('/data')
def get_data():
    """
    获取指定时间范围的数据
    查询参数:
        start_time: 开始时间戳（必需）
        hostname: 主机名（可选）
    """
    try:
        # 获取参数
        start_time = request.args.get('start_time')
        if not start_time:
            return jsonify({"error": "Missing start_time parameter"})
        start_time = int(start_time)

        # 获取时间范围参数
        timerange = request.args.get('timerange')
        if not timerange:
            return jsonify({"error": "Missing timerange parameter"})

        # 计算需要获取的数据点数
        now = int(datetime.now().timestamp())
        time_range = now - start_time
        duration = {
            'minute': 60,
            'tenminutes': 600,
            'hour': 3600,
            'day': 86400,
            'week': 604800
        }[timerange]
        show_num = int(time_range / duration * MAX_POINTS)  # 根据时间范围计算显示点数
        print(f'请求的时间范围是{time_range}秒, 总时间范围是{duration}秒, 计算的显示点数是{show_num}')
        fetch_num = min(show_num * 2, MAX_POINTS * 2)  # 限制最大获取数量
        print(f'计算的获取点数是{fetch_num}')

        # 查询数据
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, 开始获取数据，起始时间HH:MM:SS, {datetime.fromtimestamp(start_time).strftime('%H:%M:%S')}")
        fetch_start_time = datetime.now()

        try:
            supabase = get_supabase_client()

            # 使用数据库函数进行数据采样
            response = (
                supabase.schema('public')
                .rpc('sample_speed_data', {
                    'start_ts': start_time,
                    'bucket_count': fetch_num
                })
                .execute()
            )
            print(f'sample_speed_data({start_time}, {fetch_num})')
            data = response.data

            print(f'获取了{len(data)}条数据, 耗时{datetime.now() - fetch_start_time}')

            # 返回数据
            return jsonify({
                "timestamps": [row['ts'] for row in data] if data else [],
                "download": [row['download'] for row in data] if data else [],
                "upload": [row['upload'] for row in data] if data else []
            })

        except Exception as e:
            print(f"数据查询出错: {str(e)}")
            return jsonify({
                "error": str(e)
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