# ValueLine 一页研报 · Airflow 运行镜像
# 基于官方 Airflow 镜像（Python 3.11）+ 项目依赖（AKShare/pandas/pyarrow/pymupdf/requests）
FROM apache/airflow:2.9.3-python3.11

USER root
COPY requirements.txt /requirements.txt
USER airflow

# 用国内 pip 镜像源（清华）+ 加长超时/重试，避免国内直连 pypi.org 卡死
RUN pip install --no-cache-dir --default-timeout=100 --retries 5 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple -r /requirements.txt

# 可选：导出 PNG 长图 / PDF 需要 playwright（镜像会明显变大，按需开启）
# USER root
# RUN pip install --no-cache-dir playwright && playwright install --with-deps chromium
# USER airflow
