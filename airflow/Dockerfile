# ValueLine 一页研报 · Airflow 运行镜像
# 基于官方 Airflow 镜像（Python 3.11）+ 项目依赖（AKShare/pandas/pyarrow/pymupdf/requests）
FROM apache/airflow:2.9.3-python3.11

USER root
COPY requirements.txt /requirements.txt
USER airflow

RUN pip install --no-cache-dir -r /requirements.txt

# 可选：导出 PNG 长图 / PDF 需要 playwright（镜像会明显变大，按需开启）
# USER root
# RUN pip install --no-cache-dir playwright && playwright install --with-deps chromium
# USER airflow
