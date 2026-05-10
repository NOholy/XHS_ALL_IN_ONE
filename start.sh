#!/bin/bash

# 获取脚本所在目录的绝对路径
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "======================================"
echo "    XHS_ALL_IN_ONE 本地启动脚本"
echo "======================================"

# 检查虚拟环境是否存在
if [ ! -d "venv" ]; then
    echo "❌ 错误: 未找到虚拟环境 'venv' 目录。"
    echo "请先根据文档执行环境安装步骤："
    echo "1. python3 -m venv venv"
    echo "2. source venv/bin/activate"
    echo "3. pip install -r requirements.txt"
    echo "4. npm install && cd frontend && npm install"
    echo "======================================"
    exit 1
fi

# 激活虚拟环境
echo "⏳ 正在激活虚拟环境..."
source venv/bin/activate

# 检查激活是否成功
if [ $? -ne 0 ]; then
    echo "❌ 错误: 虚拟环境激活失败！"
    exit 1
fi

echo "✅ 虚拟环境已激活"
echo "🚀 正在启动前后端服务..."
echo "访问地址: http://127.0.0.1:5173"
echo "停止服务请按 Ctrl+C"
echo "--------------------------------------"

# 启动服务
python main.py --with-frontend
