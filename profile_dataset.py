import polars as pl
from dataxid_profiling import ProfileReport

df = pl.read_csv(r"C:\Users\EthanDouglas\Desktop\DQaaS\sample_orders.csv")
report = ProfileReport(df)
report.to_html("modern_profile.html")