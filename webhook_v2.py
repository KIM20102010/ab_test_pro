from flask import Flask, request, abort
import hmac
import hashlib
import os
import json
from datetime import datetime
from supabase import create_client, Client
import threading
import time
import tempfile
import shutil

app = Flask(__name__)

# ========== 环境变量 ==========
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
WEBHOOK_SECRET = os.environ.get("CREEM_WEBHOOK_SECRET", "your_super_secret_here")
TOKEN_FILE = "unlock_token.txt"
PROCESSED_EVENTS_FILE = "processed_events.json"

# ========== Supabase客户端 ==========
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ========== 防重放缓存 ==========
if os.path.exists(PROCESSED_EVENTS_FILE):
    with open(PROCESSED_EVENTS_FILE, 'r') as f:
        processed_events = set(json.load(f))
else:
    processed_events = set()

def save_processed_events():
    with open(PROCESSED_EVENTS_FILE, 'w') as f:
        json.dump(list(processed_events), f)

# ========== 定时清理任务（每30分钟清理/tmp下的CSV） ==========
def cleanup_temp_files():
    while True:
        time.sleep(1800)  # 30分钟
        try:
            temp_dir = tempfile.gettempdir()
            now = time.time()
            for filename in os.listdir(temp_dir):
                if filename.endswith('.csv'):
                    filepath = os.path.join(temp_dir, filename)
                    if os.path.isfile(filepath):
                        file_age = now - os.path.getmtime(filepath)
                        if file_age > 3600:  # 超过1小时
                            os.remove(filepath)
                            print(f"🧹 Cleaned up old CSV: {filename}")
        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")

# 启动后台清理线程
cleanup_thread = threading.Thread(target=cleanup_temp_files, daemon=True)
cleanup_thread.start()

# ========== Webhook端点 ==========
@app.route('/webhook', methods=['POST'])
def webhook():
    signature = request.headers.get('X-Creem-Signature')
    payload = request.get_data()
    
    # 1. 验证HMAC签名
    if not signature or not hmac.compare_digest(
        hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest(),
        signature
    ):
        abort(403)
    
    data = request.json
    event_id = data.get('id')
    event_type = data.get('type')
    user_email = data.get('data', {}).get('customer', {}).get('email')
    amount = data.get('data', {}).get('amount', 0)
    
    # 2. 防重放攻击
    if event_id in processed_events:
        print(f"⛔ Replayed event {event_id} blocked.")
        return "Already Processed", 200
    
    # 3. 处理支付成功事件
    if event_type == 'payment.succeeded':
        if not user_email:
            print("❌ No user email in webhook")
            return "Missing email", 400
        
        # 根据金额判断订阅类型
        if amount >= 39900:  # $399 Founders Plan
            plan = 'founder'
        elif amount >= 19900:  # $199 Starter Annual
            plan = 'starter'
        else:  # $49 Single
            plan = 'pro_single'
        
        # 4. 更新数据库（事务性）
        try:
            # 检查用户是否存在
            user_resp = supabase.table('profiles').select('id').eq('email', user_email).execute()
            if not user_resp.data:
                # 如果用户不存在，创建新用户
                # 注意：这里需要先创建auth用户，但简化版用upsert
                supabase.table('profiles').upsert({
                    'email': user_email,
                    'plan': plan
                }).execute()
            else:
                # 更新现有用户
                supabase.table('profiles').update({
                    'plan': plan
                }).eq('email', user_email).execute()
            
            # 记录支付事件（防重放）
            processed_events.add(event_id)
            save_processed_events()
            
            # 写入令牌供Streamlit轮询（兼容旧逻辑）
            with open(TOKEN_FILE, 'w') as f:
                f.write(f"paid|{datetime.now().timestamp()}|{event_id}")
            
            print(f"✅ Payment confirmed: {user_email} -> {plan}")
            return "OK", 200
            
        except Exception as e:
            print(f"❌ Database error: {e}")
            return "Database error", 500
    
    return "OK", 200

# ========== 健康检查 ==========
@app.route('/health', methods=['GET'])
def health():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)