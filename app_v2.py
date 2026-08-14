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
import math

# ========== 辅助函数 ==========
def is_valid_number(x):
    """检查数值是否为有效数字（非None，非NaN，非inf）"""
    if x is None:
        return False
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return False
    return True

# ========== 报告ID计数器 ==========
if "report_counter" not in st.session_state:
    st.session_state.report_counter = 0

# ========== 页面配置 ==========
st.set_page_config(
    page_title="A/B Test Pro - Statistical Analysis Suite",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ========== 环境变量 ==========
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
CREEM_PAYMENT_SINGLE = os.environ.get("CREEM_PAYMENT_LINK_SINGLE", "#")
CREEM_PAYMENT_STARTER = os.environ.get("CREEM_PAYMENT_LINK_STARTER", "#")
CREEM_PAYMENT_FOUNDER = os.environ.get("CREEM_PAYMENT_LINK_FOUNDER", "#")
FREE_TRIAL_LIMIT = 2
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# ========== Supabase客户端 ==========
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ========== Session State初始化 ==========
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

# ========== 认证函数 ==========
def login(email, password):
    try:
        response = supabase.table('profiles').select('*').eq('email', email).execute()
        if response.data:
            user = response.data[0]
            st.session_state.authenticated = True
            st.session_state.user_email = email
            st.session_state.user_plan = user.get('plan', 'free')
            st.session_state.free_usage_count = user.get('free_usage_count', 0)
            st.session_state.free_usage_date = user.get('free_usage_date')
            return True
        else:
            supabase.table('profiles').insert({
                'email': email,
                'plan': 'free',
                'free_usage_count': 0,
                'free_usage_date': datetime.now(timezone.utc).date().isoformat()
            }).execute()
            st.session_state.authenticated = True
            st.session_state.user_email = email
            st.session_state.user_plan = 'free'
            st.session_state.free_usage_count = 0
            return True
    except Exception as e:
        st.error(f"Login error: {e}")
        return False

def logout():
    st.session_state.authenticated = False
    st.session_state.user_email = None
    st.session_state.user_plan = 'free'
    st.session_state.unlocked = False
    st.rerun()

# ========== 免费配额检查 ==========
def check_free_quota():
    if st.session_state.user_plan != 'free':
        return True
    today = datetime.now(timezone.utc).date().isoformat()
    if st.session_state.free_usage_date != today:
        st.session_state.free_usage_count = 0
        st.session_state.free_usage_date = today
        try:
            supabase.table('profiles').update({
                'free_usage_count': 0,
                'free_usage_date': today
            }).eq('email', st.session_state.user_email).execute()
        except:
            pass
        return True
    return st.session_state.free_usage_count < FREE_TRIAL_LIMIT

def increment_free_usage():
    if st.session_state.user_plan != 'free':
        return
    st.session_state.free_usage_count += 1
    try:
        supabase.table('profiles').update({
            'free_usage_count': st.session_state.free_usage_count
        }).eq('email', st.session_state.user_email).execute()
    except:
        pass

# ========== 轮询Webhook解锁 ==========
def check_webhook_unlock():
    if st.session_state.unlocked or st.session_state.user_plan != 'free':
        return
    if time.time() - st.session_state.last_check < 3:
        return
    st.session_state.last_check = time.time()
    try:
        response = supabase.table('profiles').select('plan').eq('email', st.session_state.user_email).execute()
        if response.data:
            plan = response.data[0].get('plan', 'free')
            if plan != 'free':
                st.session_state.user_plan = plan
                st.session_state.unlocked = True
                st.rerun()
    except:
        pass

# ========== 渲染休眠遮罩 ==========
if 'first_load' not in st.session_state:
    st.session_state.first_load = True
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
        if submitted and email:
            if login(email, password):
                st.success("✅ Logged in successfully!")
                st.rerun()
            else:
                st.error("Login failed. Please try again.")
    st.caption("📌 By signing up, you agree to our Terms of Service and Privacy Policy.")
    st.stop()

# ========== 主侧边栏 ==========
with st.sidebar:
    st.markdown(f"**👤 {st.session_state.user_email}**")
    if st.button("Logout"):
        logout()
    st.markdown("---")
    st.header("⚙️ Controls")
    
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
            st.session_state.batch_files = batch_files
    
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
        if st.button("📄 Single Report ($49)", use_container_width=True):
            st.markdown(f'<meta http-equiv="refresh" content="0;url={CREEM_PAYMENT_SINGLE}">', unsafe_allow_html=True)
        if st.button("🚀 Starter Annual ($199)", use_container_width=True):
            st.markdown(f'<meta http-equiv="refresh" content="0;url={CREEM_PAYMENT_STARTER}">', unsafe_allow_html=True)
        if st.button("🔥 Founder’s Plan ($399)", use_container_width=True):
            st.markdown(f'<meta http-equiv="refresh" content="0;url={CREEM_PAYMENT_FOUNDER}">', unsafe_allow_html=True)
    st.markdown("---")
    st.caption("🔒 All data processed locally. No data stored.")

# ========== 统计分析核心函数 ==========
def perform_statistical_tests(control, treatment):
    n_c, n_t = len(control), len(treatment)
    m_c, m_t = control.mean(), treatment.mean()
    s_c, s_t = control.std(), treatment.std()
    
    paired = False
    w_stat, w_p = None, None
    if n_c == n_t:
        try:
            w_stat, w_p = wilcoxon(treatment, control)
            paired = True
        except:
            pass
    
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
    ad_c = anderson(control, dist='norm')
    ad_t = anderson(treatment, dist='norm')
    
    def calc_power(eff, alpha, n1, n2):
        df_t = n1 + n2 - 2
        ncp = eff * np.sqrt((n1 * n2) / (n1 + n2))
        t_crit = stats.t.ppf(1 - alpha/2, df_t)
        power = 1 - stats.nct.cdf(t_crit, df_t, ncp) + stats.nct.cdf(-t_crit, df_t, ncp)
        return min(max(power, 0), 0.999)
    
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
    
    def calc_mde(alpha, n1, n2, target_power=0.8):
        for d in np.arange(0.01, 3.0, 0.01):
            if calc_power(d, alpha, n1, n2) >= target_power:
                return d
        return 3.0
    mde = calc_mde(alpha, n_c, n_t)
    
    return {
        'n_c': n_c, 'n_t': n_t,
        'm_c': m_c, 'm_t': m_t,
        's_c': s_c, 's_t': s_t,
        'p_val': p_val,
        'cohen_d': cohen_d,
        'effect_label': effect_label,
        'effect_color': effect_color,
        'u_p': u_p,
        'ad_c': ad_c,
        'ad_t': ad_t,
        'current_power': current_power,
        'ci_low': ci_low, 'ci_high': ci_high,
        'verdict': verdict,
        'mde': mde,
        'paired': paired,
        'w_p': w_p,
        'alpha': alpha
    }

def analyze_single_file(df, filename):
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
    return result

# ========== 结果展示函数 ==========
def display_results(result):
    if 'button_counter' not in st.session_state:
        st.session_state.button_counter = 0
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
        plt.close()
    
    with tab2:
        control_data = result['control_data']
        treatment_data = result['treatment_data']
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
        bp = ax1.boxplot([control_data, treatment_data], labels=['Control', 'Treatment'], patch_artist=True)
        for patch, color in zip(bp['boxes'], ['#2E86AB', '#A23B72']):
            patch.set_facecolor(color)
        ax1.grid(True, alpha=0.3)
        ax1.set_ylabel('Value')
        ax2.hist(control_data, bins=15, alpha=0.6, label='Control', color='#2E86AB', edgecolor='black')
        ax2.hist(treatment_data, bins=15, alpha=0.6, label='Treatment', color='#A23B72', edgecolor='black')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        ax2.set_xlabel('Value')
        ax2.set_ylabel('Frequency')
        st.pyplot(fig)
        plt.close()
    
    with tab3:
        sample_sizes = np.arange(5, 201, 5)
        powers = [calc_power_curve(result['cohen_d'], result['alpha'], n, n) for n in sample_sizes]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(sample_sizes, powers, 'b-', linewidth=2)
        ax.axhline(0.8, color='red', linestyle='--', alpha=0.7, label='80% Threshold')
        ax.set_xlabel("Sample Size (per group)")
        ax.set_ylabel("Statistical Power")
        ax.set_title(f"Power Curve (α={result['alpha']}, d={result['cohen_d']:.2f})")
        ax.grid(True, alpha=0.3)
        ax.legend()
        st.pyplot(fig)
        plt.close()
        
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
                pdf_data, report_id = generate_pdf_report(result)
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

def calc_power_curve(effect, alpha, n1, n2):
    df_t = n1 + n2 - 2
    ncp = effect * np.sqrt((n1 * n2) / (n1 + n2))
    t_crit = stats.t.ppf(1 - alpha/2, df_t)
    power = 1 - stats.nct.cdf(t_crit, df_t, ncp) + stats.nct.cdf(-t_crit, df_t, ncp)
    return min(max(power, 0), 0.999)

# ========== PDF生成函数（带全局异常捕获） ==========
def generate_pdf_report(result):
    try:
        # ===== 强制类型检查 =====
        def to_float(x, field_name):
            if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
                raise ValueError(f"Field '{field_name}' is None or NaN")
            try:
                return float(x)
            except:
                raise ValueError(f"Field '{field_name}' cannot be converted to float (type: {type(x)})")
        
        # 提取并转换所有关键数值字段（含标准差）
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
        
        # 检查数据
        control_data = result.get('control_data')
        treatment_data = result.get('treatment_data')
        if control_data is None or treatment_data is None or len(control_data) < 2 or len(treatment_data) < 2:
            raise ValueError("control_data or treatment_data is missing or too short")
        
        buffer = io.BytesIO()
        st.session_state.report_counter += 1
        report_id = f"RPT-{datetime.now().strftime('%Y%m%d')}-{st.session_state.report_counter:04d}"
        
        # 安全计算 lift
        lift = (m_t - m_c) / abs(m_c) if abs(m_c) > 1e-9 else 0.0
        is_significant = p_val < 0.05
        is_powered = current_power > 0.8
        
        if is_significant and is_powered:
            rec_line1 = "[PASS] Rollout to 100% – treatment shows"
            rec_line2 = "statistically significant and practical significance."
        elif is_significant and not is_powered:
            rec_line1 = "[CAUTION] Increase sample size – significant but"
            rec_line2 = "underpowered, need more data for confident decision."
        else:
            rec_line1 = "[FAIL] Stop or iterate – no significant"
            rec_line2 = "difference detected with sufficient power."
        
        project_name = st.session_state.uploaded_file_name or 'Untitled'
        
        # 描述统计表
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
        
        # 动态分页判断
        PAGE_WIDTH = 210
        PAGE_HEIGHT = 297
        PAGE_MARGIN_TOP = 25
        PAGE_MARGIN_BOTTOM = 25
        available_height = PAGE_HEIGHT - PAGE_MARGIN_TOP - PAGE_MARGIN_BOTTOM
        CURVE_HEIGHT = 100
        TABLE_ROW_HEIGHT = 5.5
        n_rows = len(table_data)
        table_height = n_rows * TABLE_ROW_HEIGHT
        
        if table_height + CURVE_HEIGHT <= available_height:
            total_pages = 3
            use_combined = True
        else:
            total_pages = 4
            use_combined = False
        
        header_text = f"A/B TEST ANALYSIS REPORT  |  Report ID: {report_id}  |  Confidential"
        
        with pdf_backend.PdfPages(buffer, 'wb') as pdf:
            # ---------- 第一页：封面 ----------
            fig1 = plt.figure(figsize=(8.5, 11))
            fig1.text(0.05, 0.97, header_text, fontsize=9, color='gray')
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
                    ab = AnnotationBbox(img_box, xy=(0.12, 0.962), xycoords='figure fraction',
                                        box_alignment=(0, 1), frameon=False)
                    fig1.add_artist(ab)
                except Exception as e:
                    print(f"Logo 加载失败: {e}")
            plt.text(0.12, 0.82, "A/B TEST ANALYSIS REPORT", fontsize=22, weight='bold', transform=fig1.transFigure)
            plt.text(0.12, 0.77, f"Report ID: {report_id}", fontsize=12, color='gray', transform=fig1.transFigure)
            plt.text(0.12, 0.73, f"Project: {project_name}", fontsize=13, transform=fig1.transFigure)
            plt.text(0.12, 0.69, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}", fontsize=11, color='gray', transform=fig1.transFigure)
            plt.text(0.12, 0.62, "EXECUTIVE SUMMARY", fontsize=15, weight='bold', color='#1a3b5c', transform=fig1.transFigure)
            summary_line1 = f"Treatment outperforms Control by {lift*100:.1f}%"
            summary_line2 = f"(p={p_val:.4f}, Power={current_power:.1%})"
            summary_color = '#2E7D32' if is_significant and is_powered else '#C62828'
            plt.text(0.12, 0.57, summary_line1, fontsize=16, weight='bold', color=summary_color, transform=fig1.transFigure)
            plt.text(0.12, 0.53, summary_line2, fontsize=14, color=summary_color, transform=fig1.transFigure)
            plt.text(0.12, 0.45, "KEY METRICS", fontsize=15, weight='bold', color='#1a3b5c', transform=fig1.transFigure)
            plt.text(0.12, 0.40, f"Difference: {m_t-m_c:.4f}  |  95% CI: [{ci_low:.4f}, {ci_high:.4f}]", fontsize=13, transform=fig1.transFigure)
            plt.text(0.12, 0.36, f"P-Value: {p_val:.5f}  |  Cohen's d: {cohen_d:.3f} ({result['effect_label']})", fontsize=13, transform=fig1.transFigure)
            plt.text(0.12, 0.32, f"Statistical Power: {current_power:.1%}  |  MDE: {result['mde']:.3f}", fontsize=13, transform=fig1.transFigure)
            if current_power < 0.8:
                plt.text(0.12, 0.26, f"⚠️ Low power. Aim for ~{int(16 * (1 + 1) / (cohen_d**2))} samples per group for 80% power.", fontsize=11, color='#C62828', transform=fig1.transFigure)
            plt.text(0.12, 0.24, "RECOMMENDED ACTION", fontsize=15, weight='bold', color='#1a3b5c', transform=fig1.transFigure)
            plt.text(0.12, 0.20, rec_line1, fontsize=13, weight='bold', color='#1a3b5c', transform=fig1.transFigure)
            plt.text(0.12, 0.16, rec_line2, fontsize=13, weight='bold', color='#1a3b5c', transform=fig1.transFigure)
            plt.text(0.12, 0.06, "Confidential — for internal use only", fontsize=10, color='gray', transform=fig1.transFigure)
            plt.text(0.85, 0.06, f"Page 1 of {total_pages}", fontsize=10, color='gray', transform=fig1.transFigure)
            plt.axis('off')
            pdf.savefig(fig1)
            plt.close(fig1)
            
            # ---------- 第二页：箱线图 + 直方图 ----------
            fig2, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.5, 11), gridspec_kw={'hspace': 0.3})
            fig2.text(0.05, 0.97, header_text, fontsize=9, color='gray')
            fig2.text(0.5, 0.94, "Distribution Comparison: Box Plot & Histogram", fontsize=14, weight='bold', ha='center', transform=fig2.transFigure)
            bp = ax1.boxplot([control_data, treatment_data], labels=['Control', 'Treatment'], patch_artist=True)
            for patch, color in zip(bp['boxes'], ['#2E86AB', '#A23B72']):
                patch.set_facecolor(color)
            ax1.scatter(1, m_c, color='white', s=80, zorder=5, label=f"Mean: {m_c:.3f}")
            ax1.scatter(2, m_t, color='white', s=80, zorder=5, label=f"Mean: {m_t:.3f}")
            ax1.legend(loc='upper left')
            ax1.grid(True, alpha=0.3)
            ax2.hist(control_data, bins=15, alpha=0.6, label='Control', color='#2E86AB')
            ax2.hist(treatment_data, bins=15, alpha=0.6, label='Treatment', color='#A23B72')
            ax2.legend()
            ax2.grid(True, alpha=0.3)
            plt.text(0.85, 0.02, f"Page 2 of {total_pages}", fontsize=10, color='gray', transform=fig2.transFigure)
            pdf.savefig(fig2)
            plt.close(fig2)
            
            # ---------- 第三页（合并）或分页 ----------
            if use_combined:
                fig_combined, (ax_curve, ax_table) = plt.subplots(
                    2, 1, figsize=(PAGE_WIDTH/25.4, PAGE_HEIGHT/25.4),
                    gridspec_kw={'height_ratios': [CURVE_HEIGHT, table_height], 'hspace': 0.2}
                )
                # 曲线
                target_power = 0.8
                def find_sample_size_for_power(eff, alpha, target_power=0.8, max_n=5000):
                    for n in range(10, max_n, 5):
                        if calc_power_curve(eff, alpha, n, n) >= target_power:
                            return n
                    return max_n
                needed_n = find_sample_size_for_power(cohen_d, alpha)
                max_x = max(200, needed_n + 50)
                max_x = min(max_x, 5000)
                if max_x <= 200:
                    sample_sizes = np.arange(5, max_x+1, 5)
                else:
                    sample_sizes = np.linspace(5, max_x, 100, dtype=int)
                powers = [calc_power_curve(cohen_d, alpha, n, n) for n in sample_sizes]
                ax_curve.plot(sample_sizes, powers, 'b-', linewidth=2, label='Power Curve')
                ax_curve.axhline(target_power, color='red', linestyle='--', alpha=0.7, label='80% Threshold')
                ax_curve.set_xlabel("Sample Size (per group)", fontsize=10)
                ax_curve.set_ylabel("Statistical Power", fontsize=10)
                ax_curve.set_title(f"Power Curve (α={alpha}, d={cohen_d:.2f})", fontsize=11)
                ax_curve.legend(loc='lower right', fontsize=9)
                ax_curve.grid(True, alpha=0.3)
                ax_curve.tick_params(axis='both', labelsize=9)
                ax_curve.set_ylim(0, 0.85)
                if needed_n <= max_x:
                    ax_curve.plot(needed_n, target_power, 'ro', markersize=8, label=f'80% at N={needed_n}')
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
                if cohen_d < 0.2 and needed_n > 500:
                    note = "Tiny effect → very large sample needed. Consider redesign."
                elif needed_n > 200:
                    note = f"Need ~{needed_n} samples/group for 80% power."
                else:
                    note = f"Power {current_power:.1%}; {needed_n} samples/group recommended."
                ax_curve.text(0.02, 0.02, note, fontsize=8, color='#333333',
                              transform=ax_curve.transAxes, ha='left', va='bottom', wrap=True)
                # 表格
                ax_table.axis('off')
                ax_table.text(0.05, 0.85, "DESCRIPTIVE STATISTICS", fontsize=14, weight='bold', color='#1a3b5c', transform=ax_table.transAxes)
                ax_table.text(0.05, 0.78, f"Project: {project_name}  |  Report ID: {report_id}", fontsize=10, color='gray', transform=ax_table.transAxes)
                table = ax_table.table(cellText=table_data, loc='center', cellLoc='center',
                                       colWidths=[0.25, 0.375, 0.375], bbox=[0.025, 0.1, 0.95, None])
                table.auto_set_font_size(False)
                table.set_fontsize(10.5)
                for (i, j), cell in table.get_celld().items():
                    if i == 0:
                        cell.set_facecolor('#1a3b5c')
                        cell.set_text_props(weight='bold', color='white')
                    else:
                        cell.set_facecolor('#f5f5f5' if i % 2 == 0 else 'white')
                fig_combined.text(0.05, 0.96, header_text, fontsize=9, color='gray')
                fig_combined.text(0.85, 0.02, f"Page 3 of {total_pages}", fontsize=10, color='gray')
                pdf.savefig(fig_combined)
                plt.close(fig_combined)
            else:
                # 分页：曲线页
                fig_curve = plt.figure(figsize=(PAGE_WIDTH/25.4, PAGE_HEIGHT/25.4))
                ax_curve = fig_curve.add_subplot(111)
                target_power = 0.8
                def find_sample_size_for_power(eff, alpha, target_power=0.8, max_n=5000):
                    for n in range(10, max_n, 5):
                        if calc_power_curve(eff, alpha, n, n) >= target_power:
                            return n
                    return max_n
                needed_n = find_sample_size_for_power(cohen_d, alpha)
                max_x = max(200, needed_n + 50)
                max_x = min(max_x, 5000)
                if max_x <= 200:
                    sample_sizes = np.arange(5, max_x+1, 5)
                else:
                    sample_sizes = np.linspace(5, max_x, 100, dtype=int)
                powers = [calc_power_curve(cohen_d, alpha, n, n) for n in sample_sizes]
                ax_curve.plot(sample_sizes, powers, 'b-', linewidth=2, label='Power Curve')
                ax_curve.axhline(target_power, color='red', linestyle='--', alpha=0.7, label='80% Threshold')
                ax_curve.set_xlabel("Sample Size (per group)", fontsize=10)
                ax_curve.set_ylabel("Statistical Power", fontsize=10)
                ax_curve.set_title(f"Power Curve (α={alpha}, d={cohen_d:.2f})", fontsize=11)
                ax_curve.legend(loc='lower right', fontsize=9)
                ax_curve.grid(True, alpha=0.3)
                ax_curve.tick_params(axis='both', labelsize=9)
                ax_curve.set_ylim(0, 0.85)
                if needed_n <= max_x:
                    ax_curve.plot(needed_n, target_power, 'ro', markersize=8, label=f'80% at N={needed_n}')
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
                if cohen_d < 0.2 and needed_n > 500:
                    note = "Given the tiny effect size, extremely large sample size is required to reach 80% power. Consider increasing the treatment intensity or re-evaluating the experiment design."
                elif needed_n > 200:
                    note = f"To reach 80% power, you need about {needed_n} samples per group. This may require extending the collection period."
                else:
                    note = f"Current power is {current_power:.1%}. About {needed_n} samples per group is recommended for 80% power."
                ax_curve.text(0.02, -0.30, note, fontsize=8, color='#333333',
                              transform=ax_curve.transAxes, ha='left', va='bottom', wrap=True)
                fig_curve.text(0.05, 0.96, header_text, fontsize=9, color='gray')
                fig_curve.text(0.85, 0.02, f"Page 3 of {total_pages}", fontsize=10, color='gray')
                pdf.savefig(fig_curve)
                plt.close(fig_curve)
                
                # 表格页
                fig_table = plt.figure(figsize=(PAGE_WIDTH/25.4, PAGE_HEIGHT/25.4))
                ax_table = fig_table.add_subplot(111)
                ax_table.axis('off')
                fig_table.text(0.05, 0.96, header_text, fontsize=9, color='gray')
                ax_table.text(0.05, 0.85, "DESCRIPTIVE STATISTICS", fontsize=14, weight='bold', color='#1a3b5c', transform=ax_table.transAxes)
                ax_table.text(0.05, 0.78, f"Project: {project_name}  |  Report ID: {report_id}", fontsize=10, color='gray', transform=ax_table.transAxes)
                table = ax_table.table(cellText=table_data, loc='center', cellLoc='center',
                                       colWidths=[0.25, 0.375, 0.375], bbox=[0.025, 0.1, 0.95, None])
                table.auto_set_font_size(False)
                table.set_fontsize(10.5)
                for (i, j), cell in table.get_celld().items():
                    if i == 0:
                        cell.set_facecolor('#1a3b5c')
                        cell.set_text_props(weight='bold', color='white')
                    else:
                        cell.set_facecolor('#f5f5f5' if i % 2 == 0 else 'white')
                fig_table.text(0.85, 0.02, f"Page 4 of {total_pages}", fontsize=10, color='gray')
                pdf.savefig(fig_table)
                plt.close(fig_table)
        
        buffer.seek(0)
        st.session_state.last_report_id = report_id
        return buffer.getvalue(), report_id
    
    except Exception as e:
        import traceback
        # 使用 st.exception 显示完整堆栈，不会折叠
        st.exception(e)
        st.error(f"Result keys: {list(result.keys())}")
        st.error(f"m_c={result.get('m_c')}, m_t={result.get('m_t')}, s_c={result.get('s_c')}, s_t={result.get('s_t')}, cohen_d={result.get('cohen_d')}, alpha={result.get('alpha')}, power={result.get('current_power')}")
        st.error(f"ci_low={result.get('ci_low')}, ci_high={result.get('ci_high')}")
        # 打印到日志
        print(traceback.format_exc())
        return b"", "ERROR"
# ========== 主逻辑 ==========
st.title("📊 A/B Test Pro")
st.markdown("**Upload your CSV to automatically compute statistical significance, power curves, and professional PDF reports.**")

check_webhook_unlock()

# ========== 数据上传处理 ==========
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
                df = pd.read_csv(file).fillna(np.nan)
                if df.empty or df.columns.empty:
                    st.warning(f"⚠️ Skipping {file.name}: file is empty or has no columns.")
                    continue
                numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
                if len(numeric_cols) < 2:
                    st.warning(f"⚠️ Skipping {file.name}: need at least 2 numeric columns.")
                    continue
                result = analyze_single_file(df, file.name)
                if 'error' in result:
                    st.warning(f"⚠️ Skipping {file.name}: {result['error']}")
                    continue
                # 校验关键字段
                required_keys = ['m_c', 'm_t', 'cohen_d', 'alpha', 'current_power']
                if any(key not in result or not is_valid_number(result[key]) for key in required_keys):
                    st.error(f"❌ Skipping {file.name}: computed value is invalid (NaN/None), cannot render PDF")
                    st.session_state.batch_results[file.name] = {"error": "invalid computed value"}
                    continue
                st.session_state.uploaded_file_name = file.name
                pdf_data, report_id = generate_pdf_report(result)
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
                    import zipfile
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, 'w') as zf:
                        for name, pdf_data in valid_pdfs.items():
                            pdf_filename = f"ABTest_{name.replace('.csv','')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                            zf.writestr(pdf_filename, pdf_data)
                    zip_buffer.seek(0)
                    st.download_button(
                        label="⬇️ Download ZIP",
                        data=zip_buffer,
                        file_name=f"ABTest_Batch_{datetime.now().strftime('%Y%m%d')}.zip",
                        mime="application/zip",
                        key="download_all_zip"
                    )
            else:
                st.info("ℹ️ No valid PDFs generated in this batch.")
    st.stop()

# ========== 单文件模式 ==========
elif uploaded_file is not None:
    if uploaded_file.size > MAX_FILE_SIZE:
        st.error(f"❌ File exceeds 5MB limit. Current size: {uploaded_file.size/1024/1024:.1f}MB. Please sample your data.")
        st.stop()
    
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv') as tmp_file:
        tmp_file.write(uploaded_file.getvalue())
        tmp_path = tmp_file.name
    
    try:
        df = pd.read_csv(tmp_path)
        st.session_state.uploaded_file_name = uploaded_file.name
    except Exception as e:
        st.error(f"Error reading CSV: {e}")
        os.unlink(tmp_path)
        st.stop()
    
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if len(numeric_cols) < 2:
        st.error("❌ Need at least 2 numeric columns for analysis.")
        os.unlink(tmp_path)
        st.stop()
    
    col1, col2 = st.columns(2)
    with col1:
        control_col = st.selectbox("Control Group (numeric)", numeric_cols, key="ctrl")
    with col2:
        treatment_col = st.selectbox("Treatment Group (numeric)", numeric_cols, key="trt")
    
    if control_col == treatment_col:
        st.warning("⚠️ Control and Treatment columns must be different.")
        os.unlink(tmp_path)
        st.stop()
    
    if st.button("📊 Run Analysis", type="primary"):
        if st.session_state.user_plan == 'free' and not check_free_quota():
            st.warning(f"🔒 Free trial limit reached ({FREE_TRIAL_LIMIT} per day). Please upgrade to continue.")
            os.unlink(tmp_path)
            st.stop()
        
        with st.spinner("🧹 Cleaning data and running analysis..."):
            df_clean = df[[control_col, treatment_col]].copy().dropna()
            if st.session_state.remove_outliers:
                Q1 = df_clean.quantile(0.25)
                Q3 = df_clean.quantile(0.75)
                IQR = Q3 - Q1
                mask = ~((df_clean < (Q1 - 1.5 * IQR)) | (df_clean > (Q3 + 1.5 * IQR))).any(axis=1)
                removed = len(df_clean) - mask.sum()
                df_clean = df_clean[mask]
                if removed > 0:
                    st.info(f"🧹 Removed {removed} rows with outliers (Listwise Deletion).")
            control_data = df_clean[control_col].dropna()
            treatment_data = df_clean[treatment_col].dropna()
            if len(control_data) < 3 or len(treatment_data) < 3:
                st.error("❌ Need at least 3 valid samples per group.")
                os.unlink(tmp_path)
                st.stop()
            
            result = perform_statistical_tests(control_data, treatment_data)
            result['control_col'] = control_col
            result['treatment_col'] = treatment_col
            result['control_data'] = control_data
            result['treatment_data'] = treatment_data
            result['df_clean'] = df_clean
            result['removed_outliers'] = removed if st.session_state.remove_outliers else 0
            
            if st.session_state.user_plan == 'free':
                increment_free_usage()
            
            st.session_state.analysis_result = result
            st.session_state.analysis_done = True
            st.rerun()
        
        os.unlink(tmp_path)
    
    if st.session_state.analysis_done and hasattr(st.session_state, 'analysis_result'):
        display_results(st.session_state.analysis_result)
    
    try:
        os.unlink(tmp_path)
    except:
        pass

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
st.caption(f"📌 All data processed locally. No data uploaded. © {datetime.now().year} A/B Test Pro")
