#!/usr/bin/env python3
"""
plot-comparison.py — Compare memory.high throttle behavior across throttling factors.

Usage: python3 plot-comparison.py <data_dir>

Expects: data_dir/factor-{0.6,0.8,0.9,1.0}/cgroup-data.csv
"""

import sys
import csv
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def load_csv(path):
    data = {'elapsed': [], 'mem_current': [], 'mem_high': [], 'evt_high': []}
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            data['elapsed'].append(float(row['elapsed_s']))
            data['mem_current'].append(int(row['memory_current_bytes']))
            data['mem_high'].append(int(row['memory_high_bytes']))
            data['evt_high'].append(int(row['evt_high']))
    return data


def to_mib(values):
    return [v / (1024 * 1024) for v in values]


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else 'data'

    factors = []
    datasets = {}
    colors = {'0.6': '#E91E63', '0.8': '#FF9800', '0.9': '#2196F3', '1.0': '#4CAF50'}

    for name in sorted(os.listdir(data_dir)):
        if not name.startswith('factor-'):
            continue
        factor = name.replace('factor-', '')
        csv_path = os.path.join(data_dir, name, 'cgroup-data.csv')
        if not os.path.exists(csv_path):
            continue
        factors.append(factor)
        datasets[factor] = load_csv(csv_path)
        print(f"Loaded factor={factor}: {len(datasets[factor]['elapsed'])} samples, "
              f"{datasets[factor]['elapsed'][-1]:.0f}s")

    if not factors:
        print("No data found!")
        sys.exit(1)

    # --- Graph 1: Memory usage overlay ---
    fig, ax = plt.subplots(figsize=(14, 7))

    for factor in factors:
        d = datasets[factor]
        mem_mib = to_mib(d['mem_current'])
        color = colors.get(factor, '#666666')
        high_mib = d['mem_high'][0] / (1024 * 1024) if d['mem_high'][0] > 0 else None

        ax.plot(d['elapsed'], mem_mib, color=color, linewidth=2,
                label=f'factor={factor}' + (f' (high={high_mib:.0f}Mi)' if high_mib else ' (no throttle)'))

        if high_mib and high_mib < 512:
            ax.axhline(y=high_mib, color=color, linewidth=1, linestyle=':', alpha=0.5)

    ax.axhline(y=512, color='#F44336', linewidth=2, linestyle='-.', label='memory.max (512 MiB)')
    ax.axhline(y=256, color='#4CAF50', linewidth=1.5, linestyle=':', alpha=0.5, label='memory.min (256 MiB)')

    ax.set_xlabel('Time (seconds)', fontsize=12)
    ax.set_ylabel('Memory (MiB)', fontsize=12)
    ax.set_title('KEP-2570: Memory Usage by Throttling Factor\n'
                 'Lower factor = more aggressive throttling = slower progression',
                 fontsize=14, fontweight='bold')
    ax.legend(fontsize=10, loc='lower right')
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=0, top=560)

    plt.tight_layout()
    path = os.path.join(data_dir, 'factor-comparison-memory.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {path}")

    # --- Graph 2: Time-to-OOM bar chart ---
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    durations = []
    high_events = []
    factor_labels = []

    for factor in factors:
        d = datasets[factor]
        durations.append(d['elapsed'][-1])
        high_events.append(max(d['evt_high']))
        factor_labels.append(factor)

    bar_colors = [colors.get(f, '#666666') for f in factor_labels]

    ax1.bar(factor_labels, durations, color=bar_colors, alpha=0.8, edgecolor='black')
    ax1.set_xlabel('memoryThrottlingFactor', fontsize=12)
    ax1.set_ylabel('Time to OOM-kill (seconds)', fontsize=12)
    ax1.set_title('Time to OOM-kill by Factor', fontsize=13, fontweight='bold')
    ax1.grid(True, alpha=0.3, axis='y')
    for i, d in enumerate(durations):
        ax1.text(i, d + 5, f'{d:.0f}s', ha='center', fontsize=11, fontweight='bold')

    ax2.bar(factor_labels, high_events, color=bar_colors, alpha=0.8, edgecolor='black')
    ax2.set_xlabel('memoryThrottlingFactor', fontsize=12)
    ax2.set_ylabel('Total memory.events high', fontsize=12)
    ax2.set_title('Throttle Events by Factor', fontsize=13, fontweight='bold')
    ax2.grid(True, alpha=0.3, axis='y')
    for i, e in enumerate(high_events):
        ax2.text(i, e + max(high_events) * 0.02, f'{e:,}', ha='center', fontsize=10)

    plt.tight_layout()
    path = os.path.join(data_dir, 'factor-comparison-bars.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {path}")

    # --- Summary table ---
    print("\n=== Summary ===")
    print(f"{'Factor':<10} {'Duration(s)':<15} {'High Events':<15} {'Outcome'}")
    print("-" * 55)
    for factor in factors:
        d = datasets[factor]
        dur = d['elapsed'][-1]
        evt = max(d['evt_high'])
        print(f"{factor:<10} {dur:<15.0f} {evt:<15,} OOMKilled")


if __name__ == '__main__':
    main()
