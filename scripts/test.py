import pandas as pd

print("1. 开始读取...", flush=True)
df = pd.read_parquet("../data/raw/601088/financial_indicator.parquet")
print("2. 读取完成，shape =", df.shape, flush=True)
print("3. 前 5 行如下：", flush=True)
print(df.head(), flush=True)