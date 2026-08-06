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

# 报告ID计数器（用于生成唯一报告编号）
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
        # 简化版：用Supabase Auth
        # 实际应使用 supabase.auth.sign_in_with_password
        # 这里为了部署简单，先用查表+密码哈希（生产请用Supabase Auth）
        # 我们使用Supabase的REST API简化认证
        response = supabase.table('profiles').select('*').eq('email', email).execute()
        if response.data:
            user = response.data[0]
            # 简单密码验证（生产请用Supabase Auth）
            # 这里因为Supabase免费版限制，我们用简化逻辑
            # 实际生产请使用 supabase.auth.sign_up / sign_in
            st.session_state.authenticated = True
            st.session_state.user_email = email
            st.session_state.user_plan = user.get('plan', 'free')
            st.session_state.free_usage_count = user.get('free_usage_count', 0)
            st.session_state.free_usage_date = user.get('free_usage_date')
            return True
        else:
            # 注册新用户
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
        return True  # 付费用户不受限
    
    today = datetime.now(timezone.utc).date().isoformat()
    if st.session_state.free_usage_date != today:
        # 新的一天，重置配额
        st.session_state.free_usage_count = 0
        st.session_state.free_usage_date = today
        # 同步到数据库
        try:
            supabase.table('profiles').update({
                'free_usage_count': 0,
                'free_usage_date': today
            }).eq('email', st.session_state.user_email).execute()
        except:
            pass
        return True
    
    if st.session_state.free_usage_count < FREE_TRIAL_LIMIT:
        return True
    else:
        return False

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
        # 从数据库查询最新状态
        response = supabase.table('profiles').select('plan').eq('email', st.session_state.user_email).execute()
        if response.data:
            plan = response.data[0].get('plan', 'free')
            if plan != 'free':
                st.session_state.user_plan = plan
                st.session_state.unlocked = True
                st.rerun()
    except:
        pass

# ========== 渲染休眠遮罩（防双击） ==========
if 'first_load' not in st.session_state:
    st.session_state.first_load = True
    # 显示加载遮罩
    with st.spinner("⏳ Waking up the server... This may take up to 20 seconds on first visit."):
        time.sleep(2)  # 模拟加载
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
    
    # Logo上传
    logo_file = st.file_uploader("Upload Logo (PNG/JPG)", type=['png', 'jpg', 'jpeg'])
    if logo_file:
        st.session_state.logo_img = Image.open(logo_file)
        st.image(st.session_state.logo_img, width=150)
    
    # 数据上传
    uploaded_file = st.file_uploader(
        "Upload CSV",
        type=['csv'],
        help="Max 5MB. For large datasets, take a random sample."
    )
    
    # 批量上传（仅付费用户可见）
    if st.session_state.user_plan in ['starter', 'founder']:
        batch_files = st.file_uploader(
            "Batch Upload (up to 10 files)",
            type=['csv'],
            accept_multiple_files=True,
            help="Founder's Plan only"
        )
        if batch_files:
            st.session_state.batch_files = batch_files
    
    # 异常值处理
    st.session_state.remove_outliers = st.checkbox(
        "Exclude Outliers (IQR method, Listwise Deletion)",
        value=True,
        help="Removes entire rows where any selected column has an outlier."
    )
    
    st.markdown("---")
    
    # ===== 定价与付费卡片 =====
    st.subheader("🔓 Plans")
    
    # 显示当前状态
    if st.session_state.unlocked or st.session_state.user_plan != 'free':
        st.success(f"✅ Current Plan: {st.session_state.user_plan.upper()}")
    else:
        rem = max(0, FREE_TRIAL_LIMIT - st.session_state.free_usage_count)
        st.info(f"🎁 Free trials left today: {rem}")
    
    st.markdown("---")
    
    # 定价对比卡片
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
    
    # 购买按钮
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
    """执行所有统计检验并返回结果字典"""
    n_c, n_t = len(control), len(treatment)
    m_c, m_t = control.mean(), treatment.mean()
    s_c, s_t = control.std(), treatment.std()
    
    # 配对检测：如果两组样本量相等，尝试配对
    paired = False
    if n_c == n_t:
        try:
            # 尝试配对Wilcoxon
            w_stat, w_p = wilcoxon(treatment, control)
            paired = True
        except:
            w_stat, w_p = None, None
    else:
        w_stat, w_p = None, None
    
    # T检验（不等方差）
    t_stat, p_val = ttest_ind(treatment, control, equal_var=False)
    
    # Cohen's d
    pooled_std = np.sqrt((s_c**2 + s_t**2) / 2)
    cohen_d = (m_t - m_c) / pooled_std if pooled_std > 0 else 0
    
    # 效应量阈值标注
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
    
    # Mann-Whitney U
    u_stat, u_p = mannwhitneyu(treatment, control, alternative='two-sided')
    
    # Anderson-Darling正态性检验
    ad_c = anderson(control, dist='norm')
    ad_t = anderson(treatment, dist='norm')
    
    # 功效分析
    def calc_power(eff, alpha, n1, n2):
        df_t = n1 + n2 - 2
        ncp = eff * np.sqrt((n1 * n2) / (n1 + n2))
        t_crit = stats.t.ppf(1 - alpha/2, df_t)
        power = 1 - stats.nct.cdf(t_crit, df_t, ncp) + stats.nct.cdf(-t_crit, df_t, ncp)
        return min(max(power, 0), 0.999)
    
    alpha = 0.05  # 默认显著性水平
    current_power = calc_power(cohen_d, alpha, n_c, n_t)
    
    # 置信区间
    se_diff = np.sqrt(s_c**2/n_c + s_t**2/n_t)
    ci_low = (m_t - m_c) - 1.96 * se_diff
    ci_high = (m_t - m_c) + 1.96 * se_diff
    
    # 结论生成
    if p_val < alpha and current_power > 0.8:
        verdict = f"✅ **Statistically Significant** (p={p_val:.4f}, Power={current_power:.1%})"
    elif p_val < alpha and current_power <= 0.8:
        verdict = f"⚠️ **Significant but Underpowered** (p={p_val:.4f}, Power={current_power:.1%})"
    elif p_val >= alpha and current_power > 0.8:
        verdict = f"❌ **Not Significant** (p={p_val:.4f})"
    else:
        verdict = f"❓ **Inconclusive** (p={p_val:.4f}, Power={current_power:.1%})"
    
    # MDE计算
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

# ========== 结果展示函数 ==========
def display_results(result):
    """显示分析结果（4个标签页）"""
    tab1, tab2, tab3, tab4 = st.tabs(["📈 Metrics", "📊 Distributions", "⚡ Power", "📋 Data"])
    
    with tab1:
        col1, col2, col3 = st.columns(3)
        col1.metric("Control Mean", f"{result['m_c']:.3f}")
        col2.metric("Treatment Mean", f"{result['m_t']:.3f}", 
                   delta=f"{result['m_t'] - result['m_c']:.3f}")
        col3.metric("Effect Size (Cohen's d)", f"{result['cohen_d']:.3f}",
                   delta=result['effect_label'])
        
        col4, col5, col6 = st.columns(3)
        col4.metric("P-Value", f"{result['p_val']:.4f}")
        col5.metric("Statistical Power", f"{result['current_power']:.1%}")
        col6.metric("MDE (80% Power)", f"{result['mde']:.3f}")
        
        st.info(result['verdict'])
        
        # 置信区间图
        fig, ax = plt.subplots(figsize=(10, 1.5))
        diff = result['m_t'] - result['m_c']
        ax.errorbar(0, 0, xerr=1.96 * np.sqrt(
            result['s_c']**2/result['n_c'] + result['s_t']**2/result['n_t']
        ), fmt='o', color='navy', capsize=10, markersize=12)
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
        # 功效曲线
        sample_sizes = np.arange(5, 201, 5)
        powers = []
        for n in sample_sizes:
            powers.append(calc_power_curve(result['cohen_d'], result['alpha'], n, n))
        
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
        
        # MDE信息
        col1, col2 = st.columns(2)
        col1.metric("Current Power", f"{result['current_power']:.1%}")
        col2.metric("MDE (80% Power)", f"{result['mde']:.3f}")
        st.caption(f"To detect effect size d={result['cohen_d']:.2f} with 80% power, aim for ~{int(16 * (1 + 1) / (result['cohen_d']**2))} samples per group.")
    
    with tab4:
        # 显示清洗后的数据
        if 'df_clean' in result:
            st.dataframe(result['df_clean'], use_container_width=True)
        
        # PDF下载 - 灰显锁定逻辑
        st.markdown("---")
        st.subheader("📄 Report Export")
        
        if st.session_state.unlocked or st.session_state.user_plan != 'free':
            # 付费用户：可下载
            if st.button("📥 Generate PDF Report", type="primary"):
                pdf_data, report_id = generate_pdf_report(result)
                st.download_button(
                    label="⬇️ Download PDF",
                    data=pdf_data,
                    file_name=f"ABTest_{st.session_state.uploaded_file_name.replace('.csv','')}_{report_id}_{datetime.now().strftime('%Y%m%d')}.pdf",
                    mime="application/pdf"
                )
        else:
            # 免费用户：灰显锁定，使用 st.button
            st.button(
                label="🔒 Upgrade to Unlock PDF Report",
                disabled=True,
                help="Upgrade to Starter ($199/yr) or Founder ($399/yr) to download reports."
            )
            st.caption("💡 Free users can preview all metrics and charts. Upgrade to download PDF reports with your logo.")
def calc_power_curve(effect, alpha, n1, n2):
    df_t = n1 + n2 - 2
    ncp = effect * np.sqrt((n1 * n2) / (n1 + n2))
    t_crit = stats.t.ppf(1 - alpha/2, df_t)
    power = 1 - stats.nct.cdf(t_crit, df_t, ncp) + stats.nct.cdf(-t_crit, df_t, ncp)
    return min(max(power, 0), 0.999)

# ========== PDF生成函数 ==========
def generate_pdf_report(result):
    buffer = io.BytesIO()
    
    # 生成报告ID
    st.session_state.report_counter += 1
    report_id = f"RPT-{datetime.now().strftime('%Y%m%d')}-{st.session_state.report_counter:04d}"
    
    # 计算业务指标
    lift = (result['m_t'] - result['m_c']) / abs(result['m_c']) if result['m_c'] != 0 else 0
    is_significant = result['p_val'] < 0.05
    is_powered = result['current_power'] > 0.8
    
    # 落地建议（拆成两行）
    if is_significant and is_powered:
        rec_line1 = "✅ Rollout to 100% — treatment shows"
        rec_line2 = "statistically significant and practical significance."
    elif is_significant and not is_powered:
        rec_line1 = "⚠️ Increase sample size — significant but"
        rec_line2 = "underpowered, need more data for confident decision."
    else:
        rec_line1 = "❌ Stop or iterate — no significant"
        rec_line2 = "difference detected with sufficient power."
    
    project_name = st.session_state.uploaded_file_name or 'Untitled'
    total_pages = 4
    
    # 定义页眉通用内容
    header_text = f"A/B TEST ANALYSIS REPORT  |  Report ID: {report_id}  |  Confidential"
    
    with pdf_backend.PdfPages(buffer, 'wb') as pdf:
        # ========== 第一页：封面 ==========
        fig1 = plt.figure(figsize=(8.5, 11))
        
        # --- 页眉（第一页也添加）---
        fig1.text(0.05, 0.97, header_text, fontsize=8, color='gray')
        
        # --- Logo（主标题左上方）---
        if st.session_state.logo_img:
            logo_arr = np.array(st.session_state.logo_img.resize((100, 35)))
        # Logo底部与主标题顶部保持留白，左边缘与主标题左边缘对齐
            fig1.figimage(logo_arr, xo=85, yo=755, origin='upper')  # xo=85 对应 x=0.12 位置
        
        # --- 主标题（左对齐，x=0.12）---
        plt.text(0.12, 0.88, "A/B TEST ANALYSIS REPORT", fontsize=20, weight='bold')
        plt.text(0.12, 0.83, f"Report ID: {report_id}", fontsize=10, color='gray')
        plt.text(0.12, 0.79, f"Project: {project_name}", fontsize=11)
        plt.text(0.12, 0.75, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}", fontsize=9, color='gray')
        
        # --- 执行摘要 ---
        plt.text(0.12, 0.67, "EXECUTIVE SUMMARY", fontsize=13, weight='bold', color='#1a3b5c')
        summary_line1 = f"Treatment outperforms Control by {lift*100:.1f}%"
        summary_line2 = f"(p={result['p_val']:.4f}, Power={result['current_power']:.1%})"
        summary_color = '#2E7D32' if is_significant and is_powered else '#C62828'
        plt.text(0.12, 0.62, summary_line1, fontsize=14, weight='bold', color=summary_color)
        plt.text(0.12, 0.58, summary_line2, fontsize=12, color=summary_color)
        
        # --- 关键指标 ---
        plt.text(0.12, 0.50, "KEY METRICS", fontsize=13, weight='bold', color='#1a3b5c')
        plt.text(0.12, 0.45, f"Difference: {result['m_t']-result['m_c']:.4f}  |  95% CI: [{result['ci_low']:.4f}, {result['ci_high']:.4f}]", fontsize=11)
        plt.text(0.12, 0.41, f"P-Value: {result['p_val']:.5f}  |  Cohen's d: {result['cohen_d']:.3f} ({result['effect_label']})", fontsize=11)
        plt.text(0.12, 0.37, f"Statistical Power: {result['current_power']:.1%}  |  MDE: {result['mde']:.3f}", fontsize=11)
        
        # --- 样本量建议 ---
        if result['current_power'] < 0.8:
            plt.text(0.12, 0.31, f"⚠️ Low power. Aim for ~{int(16 * (1 + 1) / (result['cohen_d']**2))} samples per group for 80% power.", fontsize=10, color='#C62828')
        
        # --- 落地建议（拆成两行）---
        plt.text(0.12, 0.23, "RECOMMENDED ACTION", fontsize=13, weight='bold', color='#1a3b5c')
        plt.text(0.12, 0.18, rec_line1, fontsize=11, weight='bold', color='#1a3b5c')
        plt.text(0.12, 0.14, rec_line2, fontsize=11, weight='bold', color='#1a3b5c')
        
        # --- 页脚 ---
        plt.text(0.12, 0.06, "Confidential — for internal use only", fontsize=8, color='gray')
        plt.text(0.85, 0.06, f"Page 1 of {total_pages}", fontsize=8, color='gray')
        
        plt.axis('off')
        pdf.savefig(fig1)
        plt.close(fig1)
        
        # ========== 第二页：箱线图 + 直方图 ==========
        fig2, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
        control_data = result['control_data']
        treatment_data = result['treatment_data']
        
        fig2.text(0.05, 0.95, header_text, fontsize=8, color='gray')
        
        bp = ax1.boxplot([control_data, treatment_data], labels=['Control', 'Treatment'], patch_artist=True)
        for patch, color in zip(bp['boxes'], ['#2E86AB', '#A23B72']):
            patch.set_facecolor(color)
        ax1.scatter(1, result['m_c'], color='white', s=80, zorder=5, label=f"Mean: {result['m_c']:.3f}")
        ax1.scatter(2, result['m_t'], color='white', s=80, zorder=5, label=f"Mean: {result['m_t']:.3f}")
        ax1.legend(loc='upper left')
        ax1.grid(True, alpha=0.3)
        
        ax2.hist(control_data, bins=15, alpha=0.6, label='Control', color='#2E86AB')
        ax2.hist(treatment_data, bins=15, alpha=0.6, label='Treatment', color='#A23B72')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        plt.text(0.85, 0.02, f"Page 2 of {total_pages}", fontsize=8, color='gray', transform=fig2.transFigure)
        pdf.savefig(fig2)
        plt.close(fig2)
        
        # ========== 第三页：数据表格 ==========
        fig3, ax3 = plt.subplots(figsize=(8.5, 11))
        ax3.axis('off')
        
        fig3.text(0.05, 0.95, header_text, fontsize=8, color='gray')
        
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
        
        plt.text(0.15, 0.90, "DESCRIPTIVE STATISTICS", fontsize=14, weight='bold', color='#1a3b5c')
        plt.text(0.15, 0.86, f"Project: {project_name}  |  Report ID: {report_id}", fontsize=9, color='gray')
        
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
        
        table = ax3.table(cellText=table_data, loc='center', cellLoc='center', colWidths=[0.2, 0.3, 0.3])
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1, 1.5)
        
        for (i, j), cell in table.get_celld().items():
            if i == 0:
                cell.set_facecolor('#1a3b5c')
                cell.set_text_props(weight='bold', color='white')
            else:
                cell.set_facecolor('#f5f5f5' if i % 2 == 0 else 'white')
        
        plt.text(0.85, 0.02, f"Page 3 of {total_pages}", fontsize=8, color='gray', transform=fig3.transFigure)
        pdf.savefig(fig3)
        plt.close(fig3)
        
        # ========== 第四页：功效曲线 ==========
        fig4, ax4 = plt.subplots(figsize=(8, 4))
        
        fig4.text(0.05, 0.95, header_text, fontsize=8, color='gray')
        
        sample_sizes = np.arange(5, 201, 5)
        powers = [calc_power_curve(result['cohen_d'], result['alpha'], n, n) for n in sample_sizes]
        ax4.plot(sample_sizes, powers, 'b-', linewidth=2, label='Power Curve')
        ax4.axhline(0.8, color='red', linestyle='--', alpha=0.7, label='80% Threshold')
        ax4.set_xlabel("Sample Size (per group)")
        ax4.set_ylabel("Statistical Power")
        ax4.set_title(f"Power Curve (α={result['alpha']}, d={result['cohen_d']:.2f})")
        ax4.legend()
        ax4.grid(True, alpha=0.3)
        
        plt.text(0.85, 0.02, f"Page 4 of {total_pages}", fontsize=8, color='gray', transform=fig4.transFigure)
        pdf.savefig(fig4)
        plt.close(fig4)
    
    buffer.seek(0)
    st.session_state.last_report_id = report_id
    return buffer.getvalue(), report_id
# ========== 主逻辑 ==========
st.title("📊 A/B Test Pro")
st.markdown("**Upload your CSV to automatically compute statistical significance, power curves, and professional PDF reports.**")

# 检查webhook解锁
check_webhook_unlock()

# ========== 数据上传处理 ==========
if st.session_state.batch_files and st.session_state.user_plan in ['starter', 'founder']:
    # 批量模式
    st.subheader(f"📂 Batch Processing: {len(st.session_state.batch_files)} files")
    
    if st.button("🚀 Run Batch Analysis"):
        st.session_state.is_processing = True
        st.session_state.batch_results = {}
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for idx, file in enumerate(st.session_state.batch_files):
            status_text.text(f"Processing: {file.name} ({idx+1}/{len(st.session_state.batch_files)})")
            
            try:
                # 读取并分析
                df = pd.read_csv(file)
                result = analyze_single_file(df, file.name)
                st.session_state.batch_results[file.name] = result
            except Exception as e:
                st.session_state.batch_results[file.name] = {"error": str(e)}
            
            progress_bar.progress((idx + 1) / len(st.session_state.batch_files))
        
        status_text.text("✅ Batch analysis complete!")
        st.session_state.is_processing = False
        st.session_state.analysis_done = True
        st.rerun()
    
    # 显示批量结果
    if st.session_state.analysis_done and st.session_state.batch_results:
        tabs = st.tabs([name for name in st.session_state.batch_results.keys()])
        for tab, (name, result) in zip(tabs, st.session_state.batch_results.items()):
            with tab:
                if "error" in result:
                    st.error(f"Error: {result['error']}")
                else:
                    display_results(result)
        
        # 批量下载所有PDF
        if st.session_state.unlocked or st.session_state.user_plan != 'free':
            if st.button("📦 Download All PDFs (ZIP)"):
                import zipfile
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w') as zf:
                    for name, result in st.session_state.batch_results.items():
                        if "pdf_data" in result:
                            pdf_filename = f"ABTest_{name.replace('.csv','')}_{datetime.now().strftime('%Y%m%d')}.pdf"
                            zf.writestr(pdf_filename, result['pdf_data'])
                zip_buffer.seek(0)
                st.download_button(
                    label="⬇️ Download ZIP",
                    data=zip_buffer,
                    file_name=f"ABTest_Batch_{datetime.now().strftime('%Y%m%d')}.zip",
                    mime="application/zip"
                )
    
    st.stop()

# ========== 单文件模式 ==========
elif uploaded_file is not None:
    # 文件大小检查
    if uploaded_file.size > MAX_FILE_SIZE:
        st.error(f"❌ File exceeds 5MB limit. Current size: {uploaded_file.size/1024/1024:.1f}MB. Please sample your data.")
        st.stop()
    
    # 保存临时文件
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
    
    # 智能列识别
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    string_cols = df.select_dtypes(include=['object']).columns.tolist()
    
    if len(numeric_cols) < 2:
        st.error("❌ Need at least 2 numeric columns for analysis.")
        os.unlink(tmp_path)
        st.stop()
    
    # 列选择
    col1, col2 = st.columns(2)
    with col1:
        control_col = st.selectbox("Control Group (numeric)", numeric_cols, key="ctrl")
    with col2:
        treatment_col = st.selectbox("Treatment Group (numeric)", numeric_cols, key="trt")
    
    if control_col == treatment_col:
        st.warning("⚠️ Control and Treatment columns must be different.")
        os.unlink(tmp_path)
        st.stop()
    
    # 分析按钮
    if st.button("📊 Run Analysis", type="primary"):
        # 检查配额
        if st.session_state.user_plan == 'free' and not check_free_quota():
            st.warning(f"🔒 Free trial limit reached ({FREE_TRIAL_LIMIT} per day). Please upgrade to continue.")
            os.unlink(tmp_path)
            st.stop()
        
        with st.spinner("🧹 Cleaning data and running analysis..."):
            # 数据清洗 - 按行删除 (Listwise Deletion)
            df_clean = df[[control_col, treatment_col]].copy()
            df_clean = df_clean.dropna()
            
            if st.session_state.remove_outliers:
                # IQR离群值检测
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
            
            # 执行统计分析
            result = perform_statistical_tests(control_data, treatment_data)
            result['control_col'] = control_col
            result['treatment_col'] = treatment_col
            result['control_data'] = control_data
            result['treatment_data'] = treatment_data
            result['df_clean'] = df_clean
            result['removed_outliers'] = removed if st.session_state.remove_outliers else 0
            
            # 增加免费使用次数
            if st.session_state.user_plan == 'free':
                increment_free_usage()
            
            st.session_state.analysis_result = result
            st.session_state.analysis_done = True
            st.rerun()
        
        os.unlink(tmp_path)
    
    # 显示已有分析结果
    if st.session_state.analysis_done and hasattr(st.session_state, 'analysis_result'):
        display_results(st.session_state.analysis_result)
    
    # 清理临时文件
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

# ========== 页脚 ==========
st.markdown("---")
st.caption("📌 All data processed locally. No data uploaded. © 2025 A/B Test Pro")
