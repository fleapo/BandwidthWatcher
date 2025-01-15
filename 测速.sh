#!/bin/bash
 设置颜色
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 服务器配置
SERVER_URL="https://xxx/speed"

# 清屏
clear

# 打印表头
echo -e "${GREEN}实时网速监测${NC}"
echo "-------------------"

# 初始化变量
last_rx=0
last_tx=0

while true; do
    # 获取当前字节数
    current=$(ifconfig pppoe-wan | grep "RX bytes")
    # current=$(ifconfig eth0)

    # 提取RX和TX的值
    rx=$(echo $current | grep -o "RX bytes:[0-9]*" | grep -o "[0-9]*")
    tx=$(echo $current | grep -o "TX bytes:[0-9]*" | grep -o "[0-9]*")

    # # 生成递增的RX和TX值，确保差值为正数
    # rx=$(( last_rx + (RANDOM % 50000) + 10000 ))
    # tx=$(( last_tx + (RANDOM % 50000) + 10000 ))
    if [ $last_rx -ne 0 ]; then
        # 计算速度 (bytes/s 转换为 KB/s)
        rx_diff=$(( (rx - last_rx) / 1024 ))
        tx_diff=$(( (tx - last_tx) / 1024 ))

        # 显示结果
        echo -e "${BLUE}↓ 下载速度: ${rx_diff} KB/s    ↑ 上传速度: ${tx_diff} KB/s${NC}"

        # 发送数据到服务器
        timestamp=$(date +%s)
        curl -s -X POST -k $SERVER_URL \
             -H "Content-Type: application/json" \             -d "{\"timestamp\": $timestamp, \"download\": $rx_diff, \"upload\": $tx_diff}" > /dev/null
    fi

    # 保存当前值作为下次计算用
    last_rx=$rx
    last_tx=$tx

    # 等待1秒
    sleep 1
done
