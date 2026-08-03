import math
import datetime
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mtick
from sqlalchemy import create_engine
from matplotlib.backends.backend_pdf import PdfPages

# =====================================================
# 全局配置
# =====================================================
date_range = 90
today_date = datetime.date.today().strftime("%m%d")


# =====================================================
# 模块 1: 数据库交互与数据预处理
# =====================================================
def fetch_test_data(localhost, username, password, database, app_name, chipname):
    """从数据库拉取指定 app 和芯片的通过测试数据"""
    db_uri = f"mysql+pymysql://{username}:{password}@{localhost}/{database}?charset=utf8"
    engine = create_engine(db_uri)
    try:
        if chipname.lower() == 'c500':
            hw_cond = "hardware IN ('c500', 'x201')"
        else:
            hw_cond = f"hardware = '{chipname}'"

        sql = f"""
            SELECT * FROM hpc_autotest
            WHERE testgroup = '{app_name}'
            AND status = 'Passed'
            AND arch = 'amd64'
            AND {hw_cond}
            AND date >= DATE_SUB(CURDATE(), INTERVAL {date_range} DAY)
            ORDER BY id DESC;
        """
        # print(sql)
        return pd.read_sql(sql, con=engine)
    finally:
        engine.dispose()


def preprocess_data(df):
    """清洗数据、标准化列名、计算统一的 X 轴时间边界"""
    df = df.rename(columns={'ecc state': 'ecc_state', 'metric golden': 'metric_golden'})

    # 删除release数据, maca_version不是"20"开头的, 肯定是release数据
    if 'maca_version' in df.columns:
        # astype(str) 确保兼容性, na=False 确保剔除空值
        df = df[df['maca_version'].astype(str).str.startswith('20', na=False)]

    if not df.empty:
        mask = (df['testgroup'] == 'lammps') & (df['branch'].isin(['master', 'maca']))
        df = df[~mask]

    if 'branch' in df.columns:
        df['branch'] = df['branch'].str.replace('hpcc_stable_22Jul2025', 'maca_stable_22Jul2025', regex=False)
        df['branch'] = df['branch'].str.replace('hpcc', 'maca', regex=False)
        df['branch'] = df['branch'].str.replace('hpc_patch_4May2022', 'maca', regex=False)
        df['branch'] = df['branch'].str.replace('hpc_22Jul2025', 'maca_stable_22Jul2025', regex=False)

    # 将x201和c500的数据续接上
    if 'hardware' in df.columns:
        df['hardware'] = df['hardware'].replace(['x201', 'c500'], 'c500')

    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values(by=['date'])
    df['metric'] = pd.to_numeric(df['metric'], errors='coerce')
    df = df.dropna(subset=['metric'])

    today_midnight = pd.Timestamp.now().normalize()
    x_min = today_midnight - pd.Timedelta(days=date_range + 1)
    x_max = today_midnight + pd.Timedelta(days=1)

    return df, x_min, x_max


# =====================================================
# 模块 2: 数据计算与过滤
# =====================================================
def filter_valid_testcases(outer_data):
    """剔除纯功能性测试 (metric 恒为 0)"""
    raw_inner_groups = list(outer_data.groupby(['testcase', 'ecc_state']))
    valid_groups = []

    for (tc, ecc), group_data in raw_inner_groups:
        if group_data['metric'].max() == 0 and group_data['metric'].min() == 0:
            continue
        valid_groups.append(((tc, ecc), group_data))

    return valid_groups


def calculate_normalized_ema(inner_data):
    """独立归一化并计算 10 点 EMA"""
    df_calc = inner_data.copy()

    valid_goldens = df_calc['metric_golden'].dropna()
    rule = str(valid_goldens.iloc[0]).strip().lower() if not valid_goldens.empty else 'max'

    if rule == 'max':
        max_val = df_calc['metric'].max()
        df_calc['metric_percent'] = np.where(max_val != 0, df_calc['metric'] / max_val, 0)
    elif rule == 'min':
        min_val = df_calc['metric'].min()
        df_calc['metric_percent'] = np.where(df_calc['metric'] != 0, min_val / df_calc['metric'], 0)
    else:
        max_val = df_calc['metric'].max()
        df_calc['metric_percent'] = np.where(max_val != 0, df_calc['metric'] / max_val, 0)

    df_calc['ema'] = df_calc['metric_percent'].ewm(span=10, adjust=False).mean()
    df_calc['centered_ma'] = df_calc['metric_percent'].rolling(window=7, min_periods=1, center=True).mean()

    return df_calc, rule


# =====================================================
# 模块 3: 可视化渲染
# =====================================================
def draw_single_subplot(ax, inner_data, testcase, ecc_state, rule, x_min, x_max):
    """在指定的坐标轴 (ax) 上绘制单个折线图"""
    ecc_str = str(ecc_state).strip().upper()
    if 'ON' in ecc_str:
        raw_color, line_color = 'lightsteelblue', 'blue'
    elif 'OFF' in ecc_str:
        raw_color, line_color = 'lightcoral', 'red'
    else:
        raw_color, line_color = 'silver', 'black'

    ax.plot(inner_data['date'], inner_data['metric_percent'],
            marker='o', linestyle='-', color=raw_color, alpha=0.4, label='Raw %')
    ax.plot(inner_data['date'], inner_data['centered_ma'],
            marker='', linestyle='-', color=line_color, linewidth=2.5, label='7-Pt Centered MA')

    ax.set_title(f'{testcase} | ECC: {ecc_state} (Rule: {rule})', fontsize=12, fontweight='bold')
    ax.set_xlabel('Date', fontsize=10)
    ax.set_ylabel('Normalized Metric (%)', fontsize=10)

    ax.yaxis.set_major_formatter(mtick.PercentFormatter(xmax=1.0))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.tick_params(axis='x', rotation=45)

    ax.set_xlim([x_min, x_max])
    ax.grid(True, linestyle='--', alpha=0.7)
    ax.legend(loc='best', fontsize=8, ncol=2)


# =====================================================
# 模块 4: 主控流水线
# =====================================================
def generate_performance_dashboards(localhost, username, password, database, app_name, chipname, pdf_obj):
    """单 App 处理主程序"""
    df = fetch_test_data(localhost, username, password, database, app_name, chipname)
    if df.empty:
        print(f"   X No valid data found for {app_name} on {chipname} in the last {date_range} days. Skipping.")
        return

    if not df.empty:
        # 解除 Pandas 打印时的最大列数限制
        pd.set_option('display.max_columns', None)
        # 可选: 解除最大宽度的限制, 防止换行太乱
        pd.set_option('display.width', 1000)

        # 打印前 3 行数据看看
        print(f"\n--- Data Preview for {app_name} ---")
        # print(df.head(3))

        # 打印完可以重置回去 (避免影响后续其他地方的打印)
        pd.reset_option('display.max_columns')
        pd.reset_option('display.width')

    df, x_min, x_max = preprocess_data(df)

    outer_groups = df.groupby(['testgroup', 'branch'])

    for (testgroup, branch), outer_data in outer_groups:
        if (branch == 'gmx2023-fep-gpu-maca' or branch == 'gmx2025-maca'):
            continue

        valid_inner_groups = filter_valid_testcases(outer_data)
        num_plots = len(valid_inner_groups)

        if num_plots == 0:
            continue

        cols = 4
        rows = math.ceil(num_plots / cols)
        fig, axes = plt.subplots(rows, cols, figsize=(24, 5 * rows), squeeze=False)
        axes = axes.flatten()

        print(f" 🎯 Plotting: {testgroup:<8} | Branch: {branch:<25} ({num_plots:<3} subplots) ")

        for i, ((testcase, ecc_state), group_data) in enumerate(valid_inner_groups):
            ax = axes[i]
            processed_data, rule = calculate_normalized_ema(group_data)
            draw_single_subplot(ax, processed_data, testcase, ecc_state, rule, x_min, x_max)

        # 即使删除了多余的网格, 所在的占位依然存在, 从而保证页面严格等于设置的宽幅
        for j in range(num_plots, len(axes)):
            fig.delaxes(axes[j])

        # 修改点 1: 去掉了导致高度溢出的 y=1.02
        fig.suptitle(f'Chip: {chipname.upper()} | App: {app_name.upper()} | Branch: {branch} (Last {date_range} Days)',
                     fontsize=18, fontweight='bold')

        # 修改点 2: 改用 rect 强制给大标题留出顶端 4% 的安全区
        plt.tight_layout(rect=[0, 0, 1, 0.96])

        # 修改点 3: 去掉 bbox_inches='tight', 让 PDF 直接输出硬编码的 24 英寸超宽幅图纸
        pdf_obj.savefig(fig)
        plt.close(fig)


# =====================================================
# 运行入口
# =====================================================
if __name__ == "__main__":
    username = "pdehpc"
    password = "pde123456"
    localhost = "hpcdb.swlab.metax-tech.com"
    database = "hpcdb"

    app_names = ['gromacs', 'lammps', 'openmm', 'namd', 'amber', 'quda', 'vkfft']
    # app_names=['lammps']
    chip_serials = ['c500','x301']

    for chipname in chip_serials:
        print(f"\n========== {chipname.upper()} plotting... ==========")
        output_pdf = f"{chipname}_{today_date}_perf_report.pdf"

        with PdfPages(output_pdf) as final_pdf:
            for app in app_names:
                generate_performance_dashboards(
                    localhost=localhost,
                    username=username,
                    password=password,
                    database=database,
                    app_name=app,
                    chipname=chipname,
                    pdf_obj=final_pdf,
                )

        print(f" 🎉 {chipname.upper()} report saved to -> {output_pdf}")

    print("\nDone.")