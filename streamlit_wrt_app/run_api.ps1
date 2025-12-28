
# 第一步：在Powershell输入路径
# 第二步，设置 API Key
# 第三步：后台启动 uvicorn
cd "C:\Users\24340\Desktop\EduTech\streamlit_wrt_app"
$env:DEEPSEEK_API_KEY="sk-ad25aecd5ba24052a71f852a3812d33a"
python -m uvicorn app:app --host 127.0.0.1 --port 8003 --reload

# 等 2 秒，确保服务启动
Start-Sleep -Seconds 2

# 第四步：打开页面
Start-Process "http://127.0.0.1:8003/"
Start-Process "http://127.0.0.1:8003/single"
