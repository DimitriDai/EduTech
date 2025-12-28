# speaking_service/run_api.ps1

# 设置 DeepSeek Key（当前窗口有效）
# ⚠️ 你可以每次启动前手动设，也可以复制你之前用的那条
# $env:DEEPSEEK_API_KEY="sk-xxxxxxxxxxxx"

# S1：先在Powershell进入：cd "C:\Users\24340\Desktop\EduTech\speaking_service"
# S2: 启动：$env:DEEPSEEK_API_KEY="sk-ad25aecd5ba24052a71f852a3812d33a"
# S3: 启动：python -m uvicorn app:app --host 127.0.0.1 --port 8001 --reload
# S4: 后端地址：http://127.0.0.1:8001/docs
# S5: 前端地址：http://127.0.0.1:8001/static/batch.html

Write-Host "Starting IELTS Speaking Service on port 8001..."

cd $PSScriptRoot
python -m uvicorn app:app --host 127.0.0.1 --port 8001 --reload