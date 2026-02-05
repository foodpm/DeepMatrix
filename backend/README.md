运行

1. 安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

2. 启动服务
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

3. 测试
GET http://localhost:8000/infer/health
