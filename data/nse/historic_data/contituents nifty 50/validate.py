#!/usr/bin/env python3
import pandas as pd
from datetime import datetime

def main():
    df = pd.read_csv("weights.csv", header=0, dtype=str)
    
    first_col = df.columns[0]
    if first_col.lower() != "date":
        df = df.rename(columns={first_col: "DATE"})
    
    num_cols = [c for c in df.columns if c not in ('DATE')]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c].str.replace(',', ''), errors='coerce').fillna(0.0)
    
    row_sums = df[num_cols].sum(axis=1)
    min_sum, max_sum, mean_sum = row_sums.min(), row_sums.max(), row_sums.mean()
    
    dates = df['DATE'].tolist()
    out_of_order_indices = [i for i in range(1, len(dates)) if dates[i] < dates[i-1]]
    out_of_order_samples = []
    for i in out_of_order_indices[:5]:
        out_of_order_samples.append(f"index {i-1} {dates[i-1]} -> index {i} {dates[i]}")
    
    with open("validation_report.txt", "w") as f:
        f.write("VALIDATION REPORT\n")
        f.write(f"Rows (excluding Headers): {df.shape[0]}, Columns: {df.shape[1]}\n")
        f.write(f"Numeric tickers: {len(num_cols)}\n")
        f.write(f"Date range: {dates[0]} -> {dates[-1]}\n")
        f.write(f"Row sums (weights): min={min_sum}, max={max_sum}, mean={mean_sum}\n")
        f.write(f"Rows where sum != 100 within tolerance 1e-6: {((row_sums - 100).abs() > 1e-6).sum()}\n")
        f.write(f"Out-of-order date indices count: {len(out_of_order_indices)}\n")
        if out_of_order_samples:
            f.write("Example out-of-order samples:\n" + "\n".join(out_of_order_samples) + "\n")

if __name__ == '__main__':
    main()
