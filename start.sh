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
    echo "3. pip install --upgrade pip"
    echo "4. pip install -r requirements.txt"
    echo "5. pip install -e ./xhs-cli"
    echo "6. cloakbrowser install && playwright install"
    echo "7. npm install && cd frontend && npm install"
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

# 检查并下载 obscura
if [ ! -f "obscura_bin/obscura" ]; then
    echo "⏳ 未检测到 obscura，正在自动下载..."
    mkdir -p obscura_bin
    cd obscura_bin
    # 检测系统类型
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if [[ $(uname -m) == "arm64" ]]; then
            curl -LO https://github.com/h4ckf0r0day/obscura/releases/latest/download/obscura-aarch64-macos.tar.gz
            tar xzf obscura-aarch64-macos.tar.gz
            rm obscura-aarch64-macos.tar.gz
        else
            curl -LO https://github.com/h4ckf0r0day/obscura/releases/latest/download/obscura-x86_64-macos.tar.gz
            tar xzf obscura-x86_64-macos.tar.gz
            rm obscura-x86_64-macos.tar.gz
        fi
    else
        # 默认使用 linux x86_64
        curl -LO https://github.com/h4ckf0r0day/obscura/releases/latest/download/obscura-x86_64-linux.tar.gz
        tar xzf obscura-x86_64-linux.tar.gz
        rm obscura-x86_64-linux.tar.gz
    fi
    cd ..
    echo "✅ obscura 下载完成"
fi

# 启动 obscura 进程
echo "⏳ 正在后台启动 obscura..."
# 检查端口 9222 是否已被占用
if ! lsof -i :9222 > /dev/null; then
    ./obscura_bin/obscura serve --port 9222 --stealth &
    OBSCURA_PID=$!
    echo "✅ obscura 已启动 (PID: $OBSCURA_PID)"
    # 等待几秒钟让 obscura 启动完成
    sleep 2
    # 确保在脚本退出时杀掉 obscura
    trap "kill $OBSCURA_PID 2>/dev/null" EXIT
else
    echo "✅ obscura (或9222端口) 已经在运行"
fi

echo "🚀 正在启动前后端服务..."
echo "访问地址: http://127.0.0.1:5173"
echo "停止服务请按 Ctrl+C"
echo "--------------------------------------"

# 启动服务
export XHS_BROWSER_ENGINE=obscura
python main.py --with-frontend
