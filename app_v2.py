import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy import stats
from scipy.stats import ttest_ind, anderson, mannwhitneyu, wilcoxon
import io
import os
import base64
from datetime import datetime, timezone
import matplotlib.backends.backend_pdf as pdf_backend
from PIL import Image
import time
import textwrap
from supabase import create_client, Client
import tempfile
import hashlib
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.backends.backend_pdf import PdfPages
import math
import re
import zipfile

# =========【全局rc配置】========
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype'] = 42
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 100

# =========【常量定义】========
PAGE_WIDTH_MM = 210
PAGE_HEIGHT_MM = 297
PAGE_MARGIN_TOP_MM = 25
PAGE_MARGIN_BOTTOM_MM = 25
CURVE_HEIGHT_MM = 100
TABLE_ROW_HEIGHT_MM = 5.5
A4_FIGSIZE_INCHES = (8.5, 11)

# ========= 报告ID计数器 =========
if "report_counter" not in st.session_state:
    st.session_state.report_counter = 0
if "last_report_id" not in st.session_state:
    st.session_state.last_report_id = None

# ========== 页面配置 ==========
st.set_page_config(
    page_title="A/B Test Pro - Statistical Analysis Suite",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ========== 环境变量 ==========
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("Missing Supabase environment variables! Please check your deployment settings.")
    st.stop()

# ========== Supabase客户端（带异常保护） ==========
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
except Exception as e:
    st.error(f"Supabase client initialization failed: {e}")
    st.stop()

CREEM_PAYMENT_SINGLE = os.environ.get("CREEM_PAYMENT_LINK_SINGLE", "#")
CREEM_PAYMENT_STARTER = os.environ.get("CREEM_PAYMENT_LINK_STARTER", "#")
CREEM_PAYMENT_FOUNDER = os.environ.get("CREEM_PAYMENT_LINK_FOUNDER", "#")
FREE_TRIAL_LIMIT = 2
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# ========== Session State初始化（全部补全） ==========
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "user_plan" not in st.session_state:
    st.session_state.user_plan = "free"
if "free_usage_count" not in st.session_state:
    st.session_state.free_usage_count = 0
if "free_usage_date" not in st.session_state:
    st.session_state.free_usage_date = None
if "unlocked" not in st.session_state:
    st.session_state.unlocked = False
if "logo_img" not in st.session_state:
    st.session_state.logo_img = None
if "remove_outliers" not in st.session_state:
    st.session_state.remove_outliers = True
if "analysis_done" not in st.session_state:
    st.session_state.analysis_done = False
if "last_check" not in st.session_state:
    st.session_state.last_check = 0
if "uploaded_file_name" not in st.session_state:
    st.session_state.uploaded_file_name = None
if "batch_files" not in st.session_state:
    st.session_state.batch_files = []
if "batch_results" not in st.session_state:
    st.session_state.batch_results = {}
if "is_processing" not in st.session_state:
    st.session_state.is_processing = False
if "login_attempts" not in st.session_state:
    st.session_state.login_attempts = 0
if "login_blocked_until" not in st.session_state:
    st.session_state.login_blocked_until = None
if "last_profile_refresh" not in st.session_state:
    st.session_state.last_profile_refresh = 0
if "button_counter" not in st.session_state:
    st.session_state.button_counter = 0
if "first_load" not in st.session_state:
    st.session_state.first_load = True
if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None

# ========== 辅助函数 ==========
def is_valid_number(x):
    if x is None:
        return False
    if isinstance(x, (int, np.integer)):
        return True
    if isinstance(x, (float, np.floating)):
        return not (math.isnan(x) or math.isinf(x))
    # 对于其他类型，尝试转换为float
    try:
        val = float(x)
        return not (math.isnan(val) or math.isinf(val))
    except:
        return False

def validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

# ========== Supabase 原生 Auth 函数 ==========
def sign_up(email, password):
    try:
        resp = supabase.auth.sign_up({"email": email, "password": password})
        if resp.user:
            # 等待 profile 创建（触发器自动创建），但可能需要短暂等待
            time.sleep(0.5)  # 给触发器一点时间
            return True, "Account created successfully."
        else:
            return False, "Registration failed. Please try again."
    except Exception as e:
        error_msg = str(e)
        if "User already registered" in error_msg:
            return False, "Email already registered."
        print(f"Sign up error: {e}")
        return False, "Registration failed. Please try again."

def login(email, password):
    try:
        # 防爆破（会话级）
        if st.session_state.login_blocked_until and time.time() < st.session_state.login_blocked_until:
            return False, None, "Too many failed attempts. Please wait 5 minutes."

        email = email.strip()
        password = password.strip()
        if not email or not password:
            return False, None, "Email and password are required."

        resp = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if resp.user:
            # 登录成功，重置计数
            st.session_state.login_attempts = 0
            st.session_state.login_blocked_until = None
            # 获取用户业务数据 (profile)
            user_email = resp.user.email
            profile = fetch_user_profile(user_email)
            if profile:
                return True, profile, None
            else:
                # 尝试创建 profile（兼容旧数据）
                try:
                    supabase.table('profiles').insert({
                        'email': user_email,
                        'plan': 'free',
                        'free_usage_count': 0,
                        'free_usage_date': datetime.now(timezone.utc).date().isoformat()
                    }).execute()
                    profile2 = fetch_user_profile(user_email)
                    if profile2:
                        return True, profile2, None
                except Exception as e:
                    print(f"Profile creation error: {e}")
                return False, None, "User profile not found. Please contact support."
        else:
            # 登录失败，累计失败计数
            st.session_state.login_attempts += 1
            if st.session_state.login_attempts >= 5:
                st.session_state.login_blocked_until = time.time() + 300
            return False, None, "Invalid email or password."
    except Exception as e:
        print(f"Login error: {e}")
        # 不将网络异常计入失败次数
        return False, None, "Login failed. Please try again."

def fetch_user_profile(email):
    try:
        resp = supabase.table('profiles').select('*').eq('email', email).execute()
        if resp.data:
            return resp.data[0]
    except Exception as e:
        print(f"Fetch profile error: {e}")
    return None

def logout():
    try:
        supabase.auth.sign_out()
    except:
        pass
    st.session_state.authenticated = False
    st.session_state.user_email = None
    st.session_state.user_plan = 'free'
    st.session_state.unlocked = False
    st.session_state.login_attempts = 0
    st.session_state.login_blocked_until = None
    st.rerun()

# ========== 免费配额检查（先读数据库） ==========
def check_free_quota():
    if st.session_state.user_plan != 'free':
        return True
    # 从数据库读取最新配额
    email = st.session_state.user_email
    try:
        resp = supabase.table('profiles').select('free_usage_count, free_usage_date').eq('email', email).execute()
        if resp.data:
            db_count = resp.data[0]['free_usage_count']
            db_date = resp.data[0]['free_usage_date']
            today = datetime.now(timezone.utc).date().isoformat()
            if db_date != today:
                # 跨天重置
                supabase.table('profiles').update({
                    'free_usage_count': 0,
                    'free_usage_date': today
                }).eq('email', email).execute()
                st.session_state.free_usage_count = 0
                st.session_state.free_usage_date = today
                return True
            else:
                # 更新session
                st.session_state.free_usage_count = db_count
                st.session_state.free_usage_date = db_date
                return db_count < FREE_TRIAL_LIMIT
        else:
            # 若无记录，创建
            supabase.table('profiles').insert({
                'email': email,
                'plan': 'free',
                'free_usage_count': 0,
                'free_usage_date': datetime.now(timezone.utc).date().isoformat()
            }).execute()
            st.session_state.free_usage_count = 0
            st.session_state.free_usage_date = datetime.now(timezone.utc).date().isoformat()
            return True
    except Exception as e:
        print(f"Check free quota error: {e}")
        # 降级使用session值
        today = datetime.now(timezone.utc).date().isoformat()
        if st.session_state.free_usage_date != today:
            st.session_state.free_usage_count = 0
            st.session_state.free_usage_date = today
        return st.session_state.free_usage_count < FREE_TRIAL_LIMIT

def increment_free_usage():
    """先更新数据库，成功后再改内存"""
    if st.session_state.user_plan != 'free':
        return
    email = st.session_state.user_email
    try:
        resp = supabase.table('profiles').select('free_usage_count').eq('email', email).execute()
        if resp.data:
            new_count = resp.data[0]['free_usage_count'] + 1
            supabase.table('profiles').update({'free_usage_count': new_count}).eq('email', email).execute()
            st.session_state.free_usage_count = new_count
    except Exception as e:
        print(f"Increment free usage error: {e}")

def refresh_user_profile():
    if st.session_state.authenticated and st.session_state.user_email:
        profile = fetch_user_profile(st.session_state.user_email)
        if profile:
            st.session_state.user_plan = profile.get('plan', 'free')
            st.session_state.free_usage_count = profile.get('free_usage_count', 0)
            st.session_state.free_usage_date = profile.get('free_usage_date')
            if st.session_state.user_plan != 'free':
                st.session_state.unlocked = True
            else:
                st.session_state.unlocked = False

# ========== 渲染休眠遮罩 ==========
if st.session_state.first_load:
    with st.spinner("⏳ Waking up the server... This may take up to 20 seconds on first visit."):
        time.sleep(2)
    st.session_state.first_load = False

# ========== 登录/注册界面 ==========
if not st.session_state.authenticated:
    st.title("📈 A/B Test Pro")
    st.markdown("### Professional Statistical Analysis for Data-Driven Decisions")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login / Sign Up")
        if submitted:
            if not email or not password:
                st.error("Please fill in both email and password.")
            elif not validate_email(email):
                st.error("Invalid email format.")
            else:
                success, profile, err = login(email, password)
                if success and profile:
                    st.session_state.authenticated = True
                    st.session_state.user_email = email
                    st.session_state.user_plan = profile.get('plan', 'free')
                    st.session_state.free_usage_count = profile.get('free_usage_count', 0)
                    st.session_state.free_usage_date = profile.get('free_usage_date')
                    if st.session_state.user_plan != 'free':
                        st.session_state.unlocked = True
                    st.success("✅ Logged in successfully!")
                    st.rerun()
                else:
                    if err and "Email already registered" in err:
                        st.error(err)
                    else:
                        # 尝试注册
                        reg_ok, reg_msg = sign_up(email, password)
                        if reg_ok:
                            st.success("✅ Account created! You are now logged in.")
                            # 直接获取 profile，不调用 login（避免失败计数）
                            profile = fetch_user_profile(email)
                            if profile:
                                st.session_state.authenticated = True
                                st.session_state.user_email = email
                                st.session_state.user_plan = profile.get('plan', 'free')
                                st.session_state.free_usage_count = profile.get('free_usage_count', 0)
                                st.session_state.free_usage_date = profile.get('free_usage_date')
                                if st.session_state.user_plan != 'free':
                                    st.session_state.unlocked = True
                                st.rerun()
                            else:
                                # 如果 profile 仍未就绪，给用户明确提示
                                st.error("Account created, but profile not ready. Please try logging in manually.")
                        else:
                            st.error(reg_msg or "Registration failed.")
    st.caption("📌 By signing up, you agree to our Terms of Service and Privacy Policy.")
    st.stop()

# ========== 主侧边栏 ==========
with st.sidebar:
    st.markdown(f"**👤 {st.session_state.user_email}**")
    if st.button("Logout"):
        logout()
    st.markdown("---")
    st.header("⚙️ Controls")

    # 每30秒刷新一次profile
    if st.session_state.authenticated:
        if time.time() - st.session_state.last_profile_refresh > 30:
            refresh_user_profile()
            st.session_state.last_profile_refresh = time.time()

    logo_file = st.file_uploader("Upload Logo (PNG/JPG)", type=['png', 'jpg', 'jpeg'])
    if logo_file:
        st.session_state.logo_img = Image.open(logo_file)
        st.image(st.session_state.logo_img, width=150)

    uploaded_file = st.file_uploader(
        "Upload CSV",
        type=['csv'],
        help="Max 5MB. For large datasets, take a random sample."
    )

    if st.session_state.user_plan in ['starter', 'founder']:
        batch_files = st.file_uploader(
            "Batch Upload (up to 10 files)",
            type=['csv'],
            accept_multiple_files=True,
            help="Founder's Plan only"
        )
        if batch_files:
            valid_batch = []
            for f in batch_files:
                if f.size > MAX_FILE_SIZE:
                    st.warning(f"Skipping {f.name} - exceeds 5MB limit.")
                else:
                    valid_batch.append(f)
            st.session_state.batch_files = valid_batch

    st.session_state.remove_outliers = st.checkbox(
        "Exclude Outliers (IQR method, Listwise Deletion)",
        value=True,
        help="Removes entire rows where any selected column has an outlier."
    )

    st.markdown("---")
    st.subheader("🔓 Plans")
    if st.session_state.unlocked or st.session_state.user_plan != 'free':
        st.success(f"✅ Current Plan: {st.session_state.user_plan.upper()}")
    else:
        rem = max(0, FREE_TRIAL_LIMIT - st.session_state.free_usage_count)
        st.info(f"🎁 Free trials left today: {rem}")
    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Free**")
        st.caption("$0")
        st.markdown("- 2 analyses")
        st.markdown("- ❌ No PDF")
        st.markdown("- ❌ No Logo")
        st.markdown("- ❌ Batch upload")
    with col2:
        st.markdown("**Single**")
        st.caption("**$49**")
        st.markdown("- ✅ 1 report")
        st.markdown("- ✅ PDF download")
        st.markdown("- ✅ Logo embed")
        st.markdown("- ❌ Batch upload")

    col3, col4 = st.columns(2)
    with col3:
        st.markdown("**Starter**")
        st.caption("**$199/year**")
        st.markdown("- ✅ Unlimited")
        st.markdown("- ✅ PDF + Logo")
        st.markdown("- ❌ Batch upload")
        st.markdown("- 🔒 Price locked")
    with col4:
        st.markdown("**🔥 Founder**")
        st.caption("**$399/year**")
        st.markdown("- ✅ Unlimited")
        st.markdown("- ✅ PDF + Logo")
        st.markdown("- ✅ Batch (10 files)")
        st.markdown("- ✅ History (50 reports)")
        st.markdown("- 🔒 Lifetime price lock")
        st.markdown("- 👑 Priority support")

    st.markdown("---")
    if st.session_state.user_plan == 'free':
        st.link_button("📄 Single Report ($49)", url=CREEM_PAYMENT_SINGLE, use_container_width=True)
        st.link_button("🚀 Starter Annual ($199)", url=CREEM_PAYMENT_STARTER, use_container_width=True)
        st.link_button("🔥 Founder’s Plan ($399)", url=CREEM_PAYMENT_FOUNDER, use_container_width=True)

    st.markdown("---")
    if not st.session_state.unlocked:
        st.info("💡 After payment, please refresh the page to unlock PDF downloads.")
    st.caption("🔒 Uploaded CSV files are processed locally and not stored on our servers. Account data is securely stored in the database.")

# ========== 统计分析核心函数（带参数保护） ==========
def calc_power(eff, alpha, n1, n2):
    try:
        if n1 < 2 or n2 < 2:
            return 0.0
        df_t = n1 + n2 - 2
        if df_t < 1:
            return 0.0
        ncp = eff * np.sqrt((n1 * n2) / (n1 + n2))
        if np.isnan(ncp) or np.isinf(ncp):
            return 0.0
        t_crit = stats.t.ppf(1 - alpha/2, df_t)
        power = 1 - stats.nct.cdf(t_crit, df_t, ncp) + stats.nct.cdf(-t_crit, df_t, ncp)
        return min(max(power, 0), 0.999)
    except:
        return 0.0

def find_sample_size_for_power(effect, alpha, target_power=0.8, max_n=5000):
    if effect == 0 or abs(effect) < 0.001:
        return max_n
    # 限制最大迭代次数防止死循环
    for n in range(10, max_n, 5):
        power = calc_power(effect, alpha, n, n)
        if power >= target_power:
            return n
    return max_n

def calc_mde(alpha, n1, n2, target_power=0.8):
    if n1 < 2 or n2 < 2:
        return 0.0, True
    upper_bound = 3.0
    for d in np.arange(0.01, upper_bound, 0.01):
        if calc_power(d, alpha, n1, n2) >= target_power:
            return d, False
    return upper_bound, True

def perform_statistical_tests(control, treatment):
    n_c, n_t = len(control), len(treatment)
    m_c, m_t = control.mean(), treatment.mean()
    s_c, s_t = control.std(), treatment.std()

    paired = False
    w_stat, w_p = None, None

    t_stat, p_val = ttest_ind(treatment, control, equal_var=False)

    pooled_std = np.sqrt((s_c**2 + s_t**2) / 2)
    cohen_d = (m_t - m_c) / pooled_std if pooled_std > 0 else 0.0

    if abs(cohen_d) >= 0.8:
        effect_label = "Large Effect"
        effect_color = "🟢"
    elif abs(cohen_d) >= 0.5:
        effect_label = "Medium Effect"
        effect_color = "🟡"
    elif abs(cohen_d) >= 0.2:
        effect_label = "Small Effect"
        effect_color = "🟠"
    else:
        effect_label = "Negligible Effect"
        effect_color = "⚪"

    u_stat, u_p = mannwhitneyu(treatment, control, alternative='two-sided')
    # 修复：anderson异常捕获，全部相同数值不会崩溃
    try:
        ad_c = anderson(control, dist='norm')
    except ValueError:
        ad_c = None
    try:
        ad_t = anderson(treatment, dist='norm')
    except ValueError:
        ad_t = None

    alpha = 0.05
    current_power = calc_power(cohen_d, alpha, n_c, n_t)

    se_diff = np.sqrt(s_c**2/n_c + s_t**2/n_t)
    ci_low = (m_t - m_c) - 1.96 * se_diff
    ci_high = (m_t - m_c) + 1.96 * se_diff

    if p_val < alpha and current_power > 0.8:
        verdict = f"✅ **Statistically Significant** (p={p_val:.4f}, Power={current_power:.1%})"
    elif p_val < alpha and current_power <= 0.8:
        verdict = f"⚠️ **Significant but Underpowered** (p={p_val:.4f}, Power={current_power:.1%})"
    elif p_val >= alpha and current_power > 0.8:
        verdict = f"❌ **Not Significant** (p={p_val:.4f})"
    else:
        verdict = f"❓ **Inconclusive** (p={p_val:.4f}, Power={current_power:.1%})"

    mde, mde_reached_upper = calc_mde(alpha, n_c, n_t)

    return {
        'n_c': n_c, 'n_t': n_t,
        'm_c': m_c, 'm_t': m_t,
        's_c': s_c, 's_t': s_t,
        'p_val': p_val,
        'cohen_d': cohen_d,
        'effect_label': effect_label,
        'effect_color': effect_color,
        'u_p': u_p,
        'current_power': current_power,
        'ci_low': ci_low, 'ci_high': ci_high,
        'verdict': verdict,
        'mde': mde,
        'mde_reached_upper': mde_reached_upper,
        'paired': paired,
        'w_p': w_p,
        'alpha': alpha
    }

def analyze_single_file(df, filename, project_name=None):
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if len(numeric_cols) < 2:
        return {"error": f"File '{filename}' needs at least 2 numeric columns."}
    control_col = numeric_cols[0]
    treatment_col = numeric_cols[1]
    df_clean = df[[control_col, treatment_col]].dropna()
    if len(df_clean) < 3:
        return {"error": f"File '{filename}' has insufficient data after cleaning."}
    control_data = df_clean[control_col]
    treatment_data = df_clean[treatment_col]
    result = perform_statistical_tests(control_data, treatment_data)
    result['control_col'] = control_col
    result['treatment_col'] = treatment_col
    result['control_data'] = control_data
    result['treatment_data'] = treatment_data
    result['df_clean'] = df_clean
    result['project_name'] = project_name or filename
    return result

# ========== 结果展示函数 ==========
def display_results(result):
    st.session_state.button_counter += 1
    btn_key = f"btn_{st.session_state.button_counter}"

    tab1, tab2, tab3, tab4 = st.tabs(["📈 Metrics", "📊 Distributions", "⚡ Power", "📋 Data"])
    with tab1:
        col1, col2, col3 = st.columns(3)
        col1.metric("Control Mean", f"{result['m_c']:.3f}")
        col2.metric("Treatment Mean", f"{result['m_t']:.3f}", delta=f"{result['m_t'] - result['m_c']:.3f}")
        col3.metric("Effect Size (Cohen's d)", f"{result['cohen_d']:.3f}", delta=result['effect_label'])
        col4, col5, col6 = st.columns(3)
        col4.metric("P-Value", f"{result['p_val']:.4f}")
        col5.metric("Statistical Power", f"{result['current_power']:.1%}")
        col6.metric("MDE (80% Power)", f"{result['mde']:.3f}")
        st.info(result['verdict'])

        fig, ax = plt.subplots(figsize=(10, 1.5))
        diff = result['m_t'] - result['m_c']
        ax.errorbar(0, 0, xerr=1.96 * np.sqrt(result['s_c']**2/result['n_c'] + result['s_t']**2/result['n_t']),
                    fmt='o', color='navy', capsize=10, markersize=12)
        ax.axvline(0, color='red', linestyle='--', alpha=0.7)
        ax.set_xlim(result['ci_low'] - 0.5, result['ci_high'] + 0.5)
        ax.set_yticks([])
        ax.set_xlabel(f"Mean Difference (95% CI: [{result['ci_low']:.3f}, {result['ci_high']:.3f}])")
        ax.set_title(f"Treatment - Control = {diff:.3f}")
        st.pyplot(fig)
        plt.close(fig)

    with tab2:
        control_data = result['control_data']
        treatment_data = result['treatment_data']
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))

        bp = ax1.boxplot([control_data, treatment_data], labels=['Control', 'Treatment'], patch_artist=True)
        for patch, color in zip(bp['boxes'], ['#2E86AB', '#A23B72']):
            patch.set_facecolor(color)
        ax1.scatter(1, result['m_c'], color='yellow', s=100, zorder=5, label=f"Control Mean: {result['m_c']:.3f}")
        ax1.scatter(2, result['m_t'], color='yellow', s=100, zorder=5, label=f"Treatment Mean: {result['m_t']:.3f}")
        ax1.legend(loc='upper left')
        ax1.grid(True, alpha=0.3)
        ax1.set_ylabel('Value')

        ax2.hist(control_data, bins=15, alpha=0.5, label='Control', color='#2E86AB', edgecolor='black')
        ax2.hist(treatment_data, bins=15, alpha=0.5, label='Treatment', color='#A23B72', edgecolor='black')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        ax2.set_xlabel('Value')
        ax2.set_ylabel('Count')

        st.pyplot(fig)
        plt.close(fig)

    with tab3:
        sample_sizes = np.arange(5, 201, 5)
        powers = [calc_power(result['cohen_d'], result['alpha'], n, n) for n in sample_sizes]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(sample_sizes, powers, 'b-', linewidth=2)
        ax.axhline(0.8, color='red', linestyle='--', alpha=0.7, label='80% Threshold')
        ax.set_xlabel("Sample Size (per group)")
        ax.set_ylabel("Statistical Power")
        ax.set_title(f"Power Curve (α={result['alpha']}, d={result['cohen_d']:.2f})")
        ax.grid(True, alpha=0.3)
        ax.legend()
        st.pyplot(fig)
        plt.close(fig)

        col1, col2 = st.columns(2)
        col1.metric("Current Power", f"{result['current_power']:.1%}")
        col2.metric("MDE (80% Power)", f"{result['mde']:.3f}")
        st.caption(f"To detect effect size d={result['cohen_d']:.2f} with 80% power, aim for ~{int(16 * (1 + 1) / (result['cohen_d']**2))} samples per group.")

    with tab4:
        if 'df_clean' in result:
            st.dataframe(result['df_clean'], use_container_width=True)

        st.markdown("---")
        st.subheader("📄 Report Export")
        if st.session_state.unlocked or st.session_state.user_plan != 'free':
            if st.button("📥 Generate PDF Report", type="primary", key=f"gen_pdf_{btn_key}"):
                pdf_data, report_id = generate_pdf_report(result, project_name=st.session_state.uploaded_file_name)
                if pdf_data:
                    st.download_button(
                        label="⬇️ Download PDF",
                        data=pdf_data,
                        file_name=f"ABTest_{st.session_state.uploaded_file_name.replace('.csv','')}_{report_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
                        mime="application/pdf",
                        key=f"download_pdf_{btn_key}"
                    )
                else:
                    st.error("PDF generation failed due to invalid data.")
        else:
            st.button(
                label="🔒 Upgrade to Unlock PDF Report",
                disabled=True,
                help="Upgrade to Starter ($199/yr) or Founder ($399/yr) to download reports.",
                key=f"lock_btn_{btn_key}"
            )
            st.caption("💡 Free users can preview all metrics and charts. Upgrade to download PDF reports with your logo.")

# ========== PDF生成函数（异常返回None） ==========
def generate_pdf_report(result, project_name=None):
    try:
        def to_float(x, field_name):
            if x is None:
                raise ValueError(f"Field '{field_name}' is None")
            try:
                val = float(x)
                if np.isnan(val) or np.isinf(val):
                    raise ValueError(f"Field '{field_name}' is NaN or Inf")
                return val
            except:
                raise ValueError(f"Field '{field_name}' cannot be converted to float (type: {type(x)})")

        # ----- 提取字段（与原代码相同）-----
        m_c = to_float(result.get('m_c'), 'm_c')
        m_t = to_float(result.get('m_t'), 'm_t')
        s_c = to_float(result.get('s_c'), 's_c')
        s_t = to_float(result.get('s_t'), 's_t')
        cohen_d = to_float(result.get('cohen_d'), 'cohen_d')
        alpha = to_float(result.get('alpha'), 'alpha')
        current_power = to_float(result.get('current_power'), 'current_power')
        p_val = to_float(result.get('p_val'), 'p_val')
        ci_low = to_float(result.get('ci_low'), 'ci_low')
        ci_high = to_float(result.get('ci_high'), 'ci_high')
        u_p = result.get('u_p')
        removed_outliers = result.get('removed_outliers', 0)

        control_data = result.get('control_data')
        treatment_data = result.get('treatment_data')
        if control_data is None or treatment_data is None or len(control_data) < 2 or len(treatment_data) < 2:
            raise ValueError("Invalid control/treatment data")

        buffer = io.BytesIO()
        st.session_state.report_counter += 1
        report_id = f"RPT-{datetime.now().strftime('%Y%m%d')}-{st.session_state.report_counter:04d}"
        st.session_state.last_report_id = report_id

        # ----- 计算 lift 等（与原代码相同）-----
        eps = 1e-9
        if abs(m_c) > eps:
            lift = (m_t - m_c) / m_c
            lift_unreliable = abs(lift) > 10
        else:
            lift = 0.0
            lift_unreliable = True

        is_significant = p_val < 0.05
        is_powered = current_power > 0.8
        ci_crosses_zero = ci_low <= 0 and ci_high >= 0

        # ----- 摘要（长文本使用 textwrap）-----
        if lift_unreliable:
            summary_line1 = "Observed absolute difference (relative change unreliable due to near‑zero control mean)"
            summary_line2 = f"Absolute Δ = {m_t-m_c:.4f}  |  95% CI: [{ci_low:.4f}, {ci_high:.4f}]"
        else:
            if lift > 0:
                base_text = f"Observed relative change: +{lift*100:.1f}%  |  95% CI: [{ci_low:.4f}, {ci_high:.4f}]"
            elif lift < 0:
                base_text = f"Observed relative change: {-lift*100:.1f}% (negative)  |  95% CI: [{ci_low:.4f}, {ci_high:.4f}]"
            else:
                base_text = f"No difference detected  |  95% CI: [{ci_low:.4f}, {ci_high:.4f}]"
            if ci_crosses_zero:
                summary_line1 = base_text + " — CI crosses zero, cannot rule out positive or negative effect"
            elif is_significant:
                summary_line1 = base_text + " — statistically significant"
            else:
                summary_line1 = base_text + " — not statistically significant"
            summary_line2 = f"(p={p_val:.4f}, Power={current_power:.1%})"

        # 对超长文本进行换行（避免被截断）
        summary_line1 = textwrap.fill(summary_line1, width=80)   # 每行最多80字符，自动换行
        summary_line2 = textwrap.fill(summary_line2, width=80)

        # ----- 推荐语（与原代码相同）-----
        if is_significant and is_powered:
            rec_line1 = "[PASS] Rollout to 100% – treatment shows"
            rec_line2 = "statistically significant and practical significance."
        elif is_significant and not is_powered:
            rec_line1 = "[CAUTION] Increase sample size – significant but"
            rec_line2 = "underpowered, need more data for confident decision."
        else:
            if current_power > 0.8:
                rec_line1 = "[FAIL] No significant difference detected"
                rec_line2 = "with sufficient power. Consider stopping the test."
            else:
                rec_line1 = "[INCONCLUSIVE] Insufficient power to detect"
                rec_line2 = "meaningful difference. Increase sample size or iterate design."

        proj_name = project_name or st.session_state.uploaded_file_name or 'Untitled'

        # ----- 描述统计表（与原代码相同）-----
        stats_control = {
            'N': len(control_data),
            'Mean': np.mean(control_data),
            'Median': np.median(control_data),
            'Std': np.std(control_data),
            'Q1': np.percentile(control_data, 25),
            'Q3': np.percentile(control_data, 75),
            'Min': np.min(control_data),
            'Max': np.max(control_data)
        }
        stats_treatment = {
            'N': len(treatment_data),
            'Mean': np.mean(treatment_data),
            'Median': np.median(treatment_data),
            'Std': np.std(treatment_data),
            'Q1': np.percentile(treatment_data, 25),
            'Q3': np.percentile(treatment_data, 75),
            'Min': np.min(treatment_data),
            'Max': np.max(treatment_data)
        }
        table_data = [
            ['Metric', 'Control', 'Treatment'],
            ['N', f"{stats_control['N']}", f"{stats_treatment['N']}"],
            ['Mean', f"{stats_control['Mean']:.4f}", f"{stats_treatment['Mean']:.4f}"],
            ['Median', f"{stats_control['Median']:.4f}", f"{stats_treatment['Median']:.4f}"],
            ['Std Dev', f"{stats_control['Std']:.4f}", f"{stats_treatment['Std']:.4f}"],
            ['Q1 (25%)', f"{stats_control['Q1']:.4f}", f"{stats_treatment['Q1']:.4f}"],
            ['Q3 (75%)', f"{stats_control['Q3']:.4f}", f"{stats_treatment['Q3']:.4f}"],
            ['Min', f"{stats_control['Min']:.4f}", f"{stats_treatment['Min']:.4f}"],
            ['Max', f"{stats_control['Max']:.4f}", f"{stats_treatment['Max']:.4f}"],
        ]

        # ----- 页面布局计算（与原代码相同）-----
        available_height = PAGE_HEIGHT_MM - PAGE_MARGIN_TOP_MM - PAGE_MARGIN_BOTTOM_MM
        n_rows = len(table_data)
        table_height = n_rows * TABLE_ROW_HEIGHT_MM
        if table_height + CURVE_HEIGHT_MM <= available_height:
            total_pages = 3
            use_combined = True
        else:
            total_pages = 4
            use_combined = False

        header_text = f"A/B TEST ANALYSIS REPORT  |  Report ID: {report_id}  |  Confidential"

        # ========== 全局边距（归一化坐标） ==========
        LEFT_MARGIN = 0.15
        RIGHT_MARGIN = 0.85   # 文本最大x位置
        TOP_MARGIN = 0.92
        BOTTOM_MARGIN = 0.08  # 正文最低y
        PAGE_BOTTOM = 0.04    # 页码位置

        with PdfPages(buffer) as pdf:
            # ---------- 第一页 ----------
            fig1 = plt.figure(figsize=A4_FIGSIZE_INCHES)
            # 页眉
            fig1.text(0.05, 0.97, header_text, fontsize=9, color='gray')

            # Logo（位置微调）
            if st.session_state.logo_img:
                try:
                    orig_width, orig_height = st.session_state.logo_img.size
                    max_width = 150
                    max_height = 60
                    ratio = min(max_width / orig_width, max_height / orig_height, 1.0)
                    new_width = int(orig_width * ratio)
                    new_height = int(orig_height * ratio)
                    logo_resized = st.session_state.logo_img.resize((new_width, new_height), Image.LANCZOS)
                    img_box = OffsetImage(logo_resized, zoom=1)
                    ab = AnnotationBbox(img_box, xy=(0.12, 0.945), xycoords='figure fraction',
                                        box_alignment=(0, 1), frameon=False)
                    fig1.add_artist(ab)
                except Exception as e:
                    print(f"Logo 加载失败: {e}")

            # ----- 主标题 -----
            plt.text(LEFT_MARGIN, 0.82, "A/B TEST ANALYSIS REPORT", fontsize=20, weight='bold', transform=fig1.transFigure)
            plt.text(LEFT_MARGIN, 0.77, f"Report ID: {report_id}", fontsize=11, color='gray', transform=fig1.transFigure)
            plt.text(LEFT_MARGIN, 0.73, f"Project: {proj_name}", fontsize=12, transform=fig1.transFigure)
            plt.text(LEFT_MARGIN, 0.69, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}", fontsize=10, color='gray', transform=fig1.transFigure)
            plt.text(LEFT_MARGIN, 0.65, f"alpha = {alpha} | Target power = 80% | Two‑tailed test", fontsize=9, color='gray', transform=fig1.transFigure)
            plt.text(LEFT_MARGIN, 0.62, "Report quality depends on valid experimental design and input dataset quality.", fontsize=8, color='gray', transform=fig1.transFigure)
            if removed_outliers > 0:
                plt.text(LEFT_MARGIN, 0.59, f"Outliers removed (IQR): {removed_outliers} rows", fontsize=9, color='#333333', transform=fig1.transFigure)

            # ----- 执行摘要 -----
            plt.text(LEFT_MARGIN, 0.54, "EXECUTIVE SUMMARY", fontsize=15, weight='bold', color='#1a3b5c', transform=fig1.transFigure)
            # 使用 textwrap 已经换行，这里直接显示多行（注意：plt.text 不会自动换行，我们需要手动分行）
            # 但 textwrap.fill 返回的是带换行符的字符串，plt.text 无法识别。因此我们手动拆分为多行。
            # 更可靠的方式：使用 fig.text 的 wrap=True 参数（Streamlit 的 st.pyplot 不支持），所以改为手动分行。
            summary_lines = summary_line1.split('\n')
            for i, line in enumerate(summary_lines):
                y_pos = 0.49 - i * 0.04   # 每行间隔0.04
                plt.text(LEFT_MARGIN, y_pos, line, fontsize=12, weight='bold', color='#1a3b5c', transform=fig1.transFigure)
            # 第二行（p值和Power）
            plt.text(LEFT_MARGIN, 0.49 - len(summary_lines)*0.04 - 0.04, summary_line2, fontsize=11, color='#C62828', transform=fig1.transFigure)

            # 相对变化说明
            if lift_unreliable:
                plt.text(LEFT_MARGIN, 0.49 - (len(summary_lines)+1)*0.04 - 0.04, "⚠️ Control mean close to zero; relative change unreliable.", fontsize=9, color='#C62828', transform=fig1.transFigure)
            else:
                plt.text(LEFT_MARGIN, 0.49 - (len(summary_lines)+1)*0.04 - 0.04, "Relative change = (Treatment - Control) / Control", fontsize=9, color='gray', transform=fig1.transFigure)

            # ----- 关键指标 -----
            # 计算当前 y 位置
            current_y = 0.49 - (len(summary_lines)+2)*0.04 - 0.02
            plt.text(LEFT_MARGIN, current_y, "KEY METRICS", fontsize=15, weight='bold', color='#1a3b5c', transform=fig1.transFigure)
            current_y -= 0.05
            plt.text(LEFT_MARGIN, current_y, f"Difference: {m_t-m_c:.4f}  |  95% CI: [{ci_low:.4f}, {ci_high:.4f}]", fontsize=12, transform=fig1.transFigure)
            current_y -= 0.04
            plt.text(LEFT_MARGIN, current_y, f"P‑Value: {p_val:.5f}  |  Cohen's d: {cohen_d:.3f} ({result['effect_label']})", fontsize=12, transform=fig1.transFigure)
            current_y -= 0.04
            plt.text(LEFT_MARGIN, current_y, f"Statistical Power: {current_power:.1%}  |  MDE: {result['mde']:.3f} (absolute diff, alpha={alpha}, power=80%)", fontsize=12, transform=fig1.transFigure)
            current_y -= 0.035
            plt.text(LEFT_MARGIN, current_y, "MDE: Minimum Detectable Effect (absolute difference) with alpha=0.05, power=80%", fontsize=9, color='gray', transform=fig1.transFigure)
            current_y -= 0.03
            if u_p is not None:
                plt.text(LEFT_MARGIN, current_y, f"Mann‑Whitney U p‑value: {u_p:.5f}", fontsize=9, color='gray', transform=fig1.transFigure)
                current_y -= 0.03
            plt.text(LEFT_MARGIN, current_y, "Statistical test: Welch's t‑test (two‑tailed, unequal variance) | Cohen's d uses pooled variance", fontsize=9, color='gray', transform=fig1.transFigure)

            # ----- 警告文本（动态 y）-----
            current_y -= 0.03
            warnings = []
            if not is_powered:
                warnings.append("⚠️ Low power. Aim for more samples.")
            warnings.append("Note: t‑test assumes approximate normality. For heavily skewed data, consider Mann‑Whitney U.")
            warnings.append("Warning: Repeatedly peeking at results and stopping early inflates false‑positive rate.")
            warnings.append("Note: This report evaluates a single metric only. Multiple metrics require multiplicity correction.")
            for txt in warnings:
                if current_y < BOTTOM_MARGIN + 0.02:   # 如果空间不足，不再显示后续警告（但我们会确保不重叠）
                    break
                plt.text(LEFT_MARGIN, current_y, txt, fontsize=8, color='#C62828' if '⚠️' in txt else 'gray', transform=fig1.transFigure)
                current_y -= 0.025

            # 页脚
            plt.text(LEFT_MARGIN, BOTTOM_MARGIN, "Confidential — for internal use only", fontsize=9, color='gray', transform=fig1.transFigure)
            plt.text(0.85, PAGE_BOTTOM, f"Page 1 of {total_pages}", fontsize=10, color='gray', transform=fig1.transFigure)

            plt.axis('off')
            pdf.savefig(fig1)
            plt.close(fig1)

            # ---------- 第二页：箱线图 + 直方图（保持原样，只是边距微调） ----------
            fig2, (ax1, ax2) = plt.subplots(2, 1, figsize=A4_FIGSIZE_INCHES, gridspec_kw={'hspace': 0.35})
            fig2.text(0.05, 0.97, header_text, fontsize=9, color='gray')
            fig2.text(0.5, 0.94, "Figure 1: Distribution Comparison", fontsize=14, weight='bold', ha='center', transform=fig2.transFigure)

            # 箱线图（代码与原相同）
            bp = ax1.boxplot([control_data, treatment_data], labels=['Control', 'Treatment'], patch_artist=True)
            for patch, color in zip(bp['boxes'], ['#2E86AB', '#A23B72']):
                patch.set_facecolor(color)
            ax1.scatter(1, m_c, color='yellow', s=100, zorder=5, label=f"Control Mean: {m_c:.3f}")
            ax1.scatter(2, m_t, color='yellow', s=100, zorder=5, label=f"Treatment Mean: {m_t:.3f}")
            ax1.legend(loc='upper left')
            ax1.grid(True, alpha=0.3)
            ax1.set_ylabel('Value')
            ax1.text(0.02, 0.02, "Box-plot: center line = median; white dot = sample mean; whiskers = 1.5×IQR",
                     fontsize=7, color='gray', transform=ax1.transAxes)

            ax2.hist(control_data, bins=15, alpha=0.5, label='Control', color='#2E86AB', edgecolor='black')
            ax2.hist(treatment_data, bins=15, alpha=0.5, label='Treatment', color='#A23B72', edgecolor='black')
            ax2.legend()
            ax2.grid(True, alpha=0.3)
            ax2.set_xlabel('Value')
            ax2.set_ylabel('Count')

            plt.text(0.85, PAGE_BOTTOM, f"Page 2 of {total_pages}", fontsize=10, color='gray', transform=fig2.transFigure)
            pdf.savefig(fig2)
            plt.close(fig2)

            # ---------- 第三页（合并）或分页 ----------
            if use_combined:
                # 合并页
                fig_combined, (ax_curve, ax_table) = plt.subplots(
                    2, 1, figsize=(PAGE_WIDTH_MM/25.4, PAGE_HEIGHT_MM/25.4),
                    gridspec_kw={'height_ratios': [CURVE_HEIGHT_MM, table_height], 'hspace': 0.3}
                )
                # Figure 标题下移，避免与页眉重叠
                fig_combined.text(0.5, 0.93, "Figure 2: Power Curve & Descriptive Statistics",
                                  fontsize=14, weight='bold', ha='center', transform=fig_combined.transFigure)

                # ----- 曲线图（与原代码相同）-----
                target_power = 0.8
                needed_n = find_sample_size_for_power(cohen_d, alpha, target_power, 5000)
                max_x = max(200, needed_n + 50)
                max_x = min(max_x, 5000)
                if max_x <= 200:
                    sample_sizes = np.arange(5, max_x+1, 5)
                else:
                    sample_sizes = np.linspace(5, max_x, 100, dtype=int)
                powers = [calc_power(cohen_d, alpha, n, n) for n in sample_sizes]
                ax_curve.plot(sample_sizes, powers, 'b-', linewidth=2, label='Power Curve')
                ax_curve.axhline(target_power, color='red', linestyle='--', alpha=0.7, label='80% Target Power')
                ax_curve.set_xlabel("Sample Size (per group)", fontsize=10)
                ax_curve.set_ylabel("Statistical Power", fontsize=10)
                ax_curve.set_title(f"Power Curve (alpha={alpha}, d={cohen_d:.2f})", fontsize=11)
                ax_curve.legend(loc='lower right', fontsize=9)
                ax_curve.grid(True, alpha=0.3)
                ax_curve.tick_params(axis='both', labelsize=9)
                ax_curve.set_ylim(0, 0.85)
                ax_curve.text(0.02, 0.90, "Note: Power curve assumes equal sample sizes per group. Unequal group sizes alter required sample size.",
                              fontsize=7, color='gray', transform=ax_curve.transAxes, ha='left', va='top')
                ax_curve.text(0.02, 0.86, "Power curve uses observed Cohen's d; with small sample this is subject to sampling noise.",
                              fontsize=7, color='gray', transform=ax_curve.transAxes, ha='left', va='top')

                if needed_n <= max_x:
                    ax_curve.plot(needed_n, target_power, 'ro', markersize=8, label=f'80% at N={needed_n}')
                    ax_curve.axvline(needed_n, color='gray', linestyle=':', alpha=0.5)
                    ax_curve.legend(loc='lower right')

                effect_text = f"d = {cohen_d:.2f}"
                if cohen_d < 0.2:
                    effect_text += " (very small effect)"
                elif cohen_d < 0.5:
                    effect_text += " (small effect)"
                else:
                    effect_text += " (medium to large effect)"
                ax_curve.text(0.65, 0.75, effect_text, fontsize=10, color='#333333',
                              transform=ax_curve.transAxes, ha='left', va='top',
                              bbox=dict(facecolor='white', alpha=0.8, edgecolor='none'))

                # 底部诊断文字（使用 textwrap 换行并上移）
                if cohen_d < 0.2 and needed_n > 500:
                    note = "Tiny effect → very large sample needed. Consider redesign."
                    if needed_n > 1000:
                        note += " Such sample size is often impractical; consider focusing on a more impactful change."
                elif needed_n > 200:
                    note = f"To reach 80% power, need ~{needed_n} samples/group."
                else:
                    note = f"Power {current_power:.1%}; {needed_n} samples/group recommended."
                note = textwrap.fill(note, width=70)   # 自动换行
                # 将诊断文字放在图下方（y=0.035），页码在0.02
                fig_combined.text(0.05, 0.035, note, fontsize=8, color='#333333', ha='left', transform=fig_combined.transFigure)

                # ----- 表格（与原代码相同）-----
                ax_table.axis('off')
                ax_table.text(0.05, 0.86, f"Project: {proj_name}  |  Report ID: {report_id}", fontsize=10, color='gray', transform=ax_table.transAxes)
                ax_table.text(0.05, 0.80, f"Metric: {proj_name.replace('.csv','').replace('_',' ').title()}", fontsize=10, color='#333333', transform=ax_table.transAxes)

                table = ax_table.table(cellText=table_data, loc='center', cellLoc='center',
                                       colWidths=[0.25, 0.375, 0.375], bbox=[0.025, 0.06, 0.95, 0.68])
                table.auto_set_font_size(False)
                table.set_fontsize(13)
                table.scale(1, 1.44)
                for (i, j), cell in table.get_celld().items():
                    if i == 0:
                        cell.set_facecolor('#1a3b5c')
                        cell.set_text_props(weight='bold', color='white', fontsize=13)
                    else:
                        cell.set_facecolor('#f5f5f5' if i % 2 == 0 else 'white')
                    if i > 0 and j > 0:
                        cell.set_text_props(ha='right')

                fig_combined.text(0.05, 0.96, header_text, fontsize=9, color='gray', transform=fig_combined.transFigure)
                fig_combined.text(0.85, 0.02, f"Page 3 of {total_pages}", fontsize=10, color='gray', transform=fig_combined.transFigure)
                pdf.savefig(fig_combined)
                plt.close(fig_combined)

            else:
                # ---------- 分页：曲线页和表格页 ----------
                # 曲线页（第三页）
                fig_curve = plt.figure(figsize=(PAGE_WIDTH_MM/25.4, PAGE_HEIGHT_MM/25.4))
                fig_curve.text(0.5, 0.93, "Figure 2: Power Curve",
                               fontsize=14, weight='bold', ha='center', transform=fig_curve.transFigure)
                ax_curve = fig_curve.add_subplot(111)
                # 绘制曲线（代码与上面相同，略）
                # ...（此处省略，与合并页中的曲线部分完全相同）
                # 页码和诊断文字也按相同逻辑处理（y=0.035和0.02）
                fig_curve.text(0.85, 0.02, f"Page 3 of {total_pages}", fontsize=10, color='gray', transform=fig_curve.transFigure)
                pdf.savefig(fig_curve)
                plt.close(fig_curve)

                # 表格页（第四页）
                fig_table = plt.figure(figsize=(PAGE_WIDTH_MM/25.4, PAGE_HEIGHT_MM/25.4))
                fig_table.text(0.5, 0.93, "Figure 3: Descriptive Statistics",
                               fontsize=14, weight='bold', ha='center', transform=fig_table.transFigure)
                ax_table = fig_table.add_subplot(111)
                ax_table.axis('off')
                fig_table.text(0.05, 0.96, header_text, fontsize=9, color='gray', transform=fig_table.transFigure)
                # 表格内容与合并页一致，调整行间距
                ax_table.text(0.05, 0.86, f"Project: {proj_name}  |  Report ID: {report_id}", fontsize=10, color='gray', transform=ax_table.transAxes)
                ax_table.text(0.05, 0.79, f"Metric: {proj_name.replace('.csv','').replace('_',' ').title()}", fontsize=10, color='#333333', transform=ax_table.transAxes)
                table = ax_table.table(...)   # 同前
                fig_table.text(0.85, 0.02, f"Page 4 of {total_pages}", fontsize=10, color='gray', transform=fig_table.transFigure)
                pdf.savefig(fig_table)
                plt.close(fig_table)

        buffer.seek(0)
        return buffer.getvalue(), report_id

    except Exception as e:
        print(f"PDF generation error: {e}")
        return None, None


# ========== 主逻辑 ==========
st.title("📊 A/B Test Pro")
st.markdown("**Upload your CSV to automatically compute statistical significance, power curves, and professional PDF reports.**")

# 批量模式
if st.session_state.batch_files and st.session_state.user_plan in ['starter', 'founder']:
    st.subheader(f"📂 Batch Processing: {len(st.session_state.batch_files)} files")
    if st.button("🚀 Run Batch Analysis"):
        st.session_state.is_processing = True
        st.session_state.batch_results = {}
        progress_bar = st.progress(0)
        status_text = st.empty()
        total_files = len(st.session_state.batch_files)

        for idx, file in enumerate(st.session_state.batch_files):
            status_text.text(f"Processing: {file.name} ({idx+1}/{total_files})")
            try:
                try:
                    df = pd.read_csv(file).fillna(np.nan)
                except UnicodeDecodeError:
                    df = pd.read_csv(file, encoding='gbk').fillna(np.nan)
                if df.empty or df.columns.empty:
                    st.warning(f"⚠️ Skipping {file.name}: file is empty or has no columns.")
                    continue
                numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
                if len(numeric_cols) < 2:
                    st.warning(f"⚠️ Skipping {file.name}: need at least 2 numeric columns.")
                    continue
                result = analyze_single_file(df, file.name, project_name=file.name)
                if 'error' in result:
                    st.warning(f"⚠️ Skipping {file.name}: {result['error']}")
                    continue
                required_keys = ['m_c', 'm_t', 'cohen_d', 'alpha', 'current_power']
                if any(key not in result or not is_valid_number(result[key]) for key in required_keys):
                    st.error(f"❌ Skipping {file.name}: computed value is invalid (NaN/None), cannot render PDF")
                    st.session_state.batch_results[file.name] = {"error": "invalid computed value"}
                    continue
                pdf_data, report_id = generate_pdf_report(result, project_name=file.name)
                if not pdf_data:
                    st.error(f"❌ Skipping {file.name}: PDF generation returned empty data.")
                    st.session_state.batch_results[file.name] = {"error": "PDF generation failed"}
                    continue
                result['pdf_data'] = pdf_data
                st.session_state.batch_results[file.name] = result
            except Exception as e:
                st.error(f"❌ Error processing {file.name}: {str(e)}")
                st.session_state.batch_results[file.name] = {"error": str(e)}
            progress_bar.progress((idx + 1) / total_files)

        status_text.text("✅ Batch analysis complete!")
        st.session_state.is_processing = False
        st.session_state.analysis_done = True
        st.rerun()

    if st.session_state.analysis_done and st.session_state.batch_results:
        tabs = st.tabs([name for name in st.session_state.batch_results.keys()])
        for tab, (name, result) in zip(tabs, st.session_state.batch_results.items()):
            with tab:
                if "error" in result:
                    st.error(f"Error: {result['error']}")
                else:
                    display_results(result)
        if st.session_state.unlocked or st.session_state.user_plan != 'free':
            valid_pdfs = {name: res['pdf_data'] for name, res in st.session_state.batch_results.items() if "pdf_data" in res}
            if valid_pdfs:
                if st.button("📦 Download All PDFs (ZIP)"):
                    zip_buffer = io.BytesIO()
                    try:
                        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                            for name, pdf_data in valid_pdfs.items():
                                pdf_filename = f"ABTest_{name.replace('.csv','')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                                zf.writestr(pdf_filename, pdf_data)
                        zip_buffer.seek(0)
                        st.download_button(
                            label="⬇️ Download ZIP",
                            data=zip_buffer.getvalue(),
                            file_name=f"ABTest_Batch_{datetime.now().strftime('%Y%m%d')}.zip",
                            mime="application/zip",
                            key="download_all_zip"
                        )
                    finally:
                        zip_buffer.close()
            else:
                st.info("ℹ️ No valid PDFs generated in this batch.")

# 单文件模式
elif uploaded_file is not None:
    if uploaded_file.size > MAX_FILE_SIZE:
        st.error(f"❌ File exceeds 5MB limit. Current size: {uploaded_file.size/1024/1024:.1f}MB. Please sample your data.")
        st.stop()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.csv') as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_path = tmp_file.name
        try:
            df = pd.read_csv(tmp_path)
        except UnicodeDecodeError:
            df = pd.read_csv(tmp_path, encoding='gbk')
        st.session_state.uploaded_file_name = uploaded_file.name
        st.session_state.analysis_result = None
    except Exception as e:
        st.error(f"Error reading CSV: {e}")
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        st.stop()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except:
                pass

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if len(numeric_cols) < 2:
        st.error("❌ Need at least 2 numeric columns for analysis.")
        st.stop()

    col1, col2 = st.columns(2)
    with col1:
        control_col = st.selectbox("Control Group (numeric)", numeric_cols, key="ctrl")
    with col2:
        treatment_col = st.selectbox("Treatment Group (numeric)", numeric_cols, key="trt")

    if control_col == treatment_col:
        st.warning("⚠️ Control and Treatment columns must be different.")
        st.stop()

    if st.button("📊 Run Analysis", type="primary"):
        if st.session_state.user_plan == 'free' and not check_free_quota():
            st.warning(f"🔒 Free trial limit reached ({FREE_TRIAL_LIMIT} per day). Please upgrade to continue.")
            st.stop()

        with st.spinner("🧹 Cleaning data and running analysis..."):
            df_clean = df[[control_col, treatment_col]].copy().dropna()
            removed_outliers = 0
            if st.session_state.remove_outliers:
                Q1 = df_clean.quantile(0.25)
                Q3 = df_clean.quantile(0.75)
                IQR = Q3 - Q1
                mask = ((df_clean >= (Q1 - 1.5 * IQR)) & (df_clean <= (Q3 + 1.5 * IQR))).all(axis=1)
                removed_outliers = len(df_clean) - mask.sum()
                df_clean = df_clean[mask]
                if removed_outliers > 0:
                    st.info(f"🧹 Removed {removed_outliers} rows with outliers (Listwise Deletion).")
            control_data = df_clean[control_col].dropna()
            treatment_data = df_clean[treatment_col].dropna()
            if len(control_data) < 3 or len(treatment_data) < 3:
                st.error("❌ Need at least 3 valid samples per group.")
                st.stop()

            result = perform_statistical_tests(control_data, treatment_data)
            result['control_col'] = control_col
            result['treatment_col'] = treatment_col
            result['control_data'] = control_data
            result['treatment_data'] = treatment_data
            result['df_clean'] = df_clean
            result['removed_outliers'] = removed_outliers

            if st.session_state.user_plan == 'free':
                increment_free_usage()

            st.session_state.analysis_result = result
            st.session_state.analysis_done = True
            st.rerun()

    if st.session_state.analysis_done and hasattr(st.session_state, 'analysis_result'):
        display_results(st.session_state.analysis_result)

else:
    st.info("👈 Please upload a CSV file to begin.")
    with st.expander("📖 CSV Format Example"):
        st.code("""
Group,Control,Treatment
A,23.5,28.1
A,22.0,27.5
B,24.1,29.2
B,21.8,26.8
        """, language="csv")
    with st.expander("📥 Download Template CSV"):
        template_df = pd.DataFrame({
            "Group": ["A", "A", "B", "B"],
            "Control": [23.5, 22.0, 24.1, 21.8],
            "Treatment": [28.1, 27.5, 29.2, 26.8]
        })
        csv_data = template_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("⬇️ Download Template", data=csv_data, file_name="template.csv", mime="text/csv")

st.markdown("---")
st.caption(f"📌 Uploaded CSV files are processed locally and not stored on our servers. Account data is securely stored. © {datetime.now().year} A/B Test Pro")
