#!/usr/bin/env python3
"""
plot.py — Generates memory.high throttle timeline graph for Scenario 2.

Usage: python3 plot.py <csv_file> <output_dir> [throttle_factor]

Produces:
  1. memory-throttle-timeline.png — Main graph showing memory usage vs time
     with memory.high and memory.max thresholds, plus memory.events overlays
  2. allocation-rate.png — Allocation rate (MiB/s) showing throttling effect
  3. events-timeline.png — Stacked memory.events counters over time
"""

import sys
import csv
import os
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib.patches import FancyBboxPatch
import numpy as np


def load_csv(path):
    data = {
        'elapsed': [], 'mem_current': [], 'mem_high': [], 'mem_max': [],
        'evt_low': [], 'evt_high': [], 'evt_max': [], 'evt_oom': [],
        'evt_oom_kill': [], 'anon': [], 'file': [], 'pgfault': [],
        'pgmajfault': [], 'pod_mem_min': []
    }
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            data['elapsed'].append(float(row['elapsed_s']))
            data['mem_current'].append(int(row['memory_current_bytes']))
            data['mem_high'].append(int(row['memory_high_bytes']))
            data['mem_max'].append(int(row['memory_max_bytes']))
            data['evt_low'].append(int(row['evt_low']))
            data['evt_high'].append(int(row['evt_high']))
            data['evt_max'].append(int(row['evt_max']))
            data['evt_oom'].append(int(row['evt_oom']))
            data['evt_oom_kill'].append(int(row['evt_oom_kill']))
            data['anon'].append(int(row['anon_bytes']))
            data['file'].append(int(row['file_bytes']))
            data['pgfault'].append(int(row['pgfault']))
            data['pgmajfault'].append(int(row['pgmajfault']))
            data['pod_mem_min'].append(int(row['pod_memory_min_bytes']))
    return data


def to_mib(values):
    return [v / (1024 * 1024) for v in values]


def compute_rate(elapsed, values, window=3):
    """Compute rate of change (MiB/s) with a rolling window."""
    rates = [0.0] * len(elapsed)
    mib = to_mib(values)
    for i in range(1, len(elapsed)):
        # Find the sample ~window seconds ago
        j = i
        while j > 0 and (elapsed[i] - elapsed[j]) < window:
            j -= 1
        if j < i and (elapsed[i] - elapsed[j]) > 0:
            rates[i] = (mib[i] - mib[j]) / (elapsed[i] - elapsed[j])
    return rates


def plot_main_timeline(data, output_dir, throttle_factor):
    """Main graph: memory usage, thresholds, and events."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10),
                                     gridspec_kw={'height_ratios': [3, 1]},
                                     sharex=True)

    elapsed = data['elapsed']
    mem_mib = to_mib(data['mem_current'])
    anon_mib = to_mib(data['anon'])
    file_mib = to_mib(data['file'])

    # --- Top panel: Memory usage ---
    ax1.fill_between(elapsed, anon_mib, alpha=0.3, color='#2196F3', label='Anonymous (heap)')
    ax1.fill_between(elapsed, file_mib, alpha=0.3, color='#4CAF50', label='File cache')
    ax1.plot(elapsed, mem_mib, color='#1565C0', linewidth=2, label='memory.current')

    # Threshold lines
    if data['mem_high'][0] > 0:
        high_mib = data['mem_high'][0] / (1024 * 1024)
        ax1.axhline(y=high_mib, color='#FF9800', linewidth=2, linestyle='--',
                    label=f'memory.high ({high_mib:.0f} MiB)')
    if data['mem_max'][0] > 0:
        max_mib = data['mem_max'][0] / (1024 * 1024)
        ax1.axhline(y=max_mib, color='#F44336', linewidth=2, linestyle='-.',
                    label=f'memory.max ({max_mib:.0f} MiB)')

    # memory.min (from pod level)
    if data['pod_mem_min'][0] > 0:
        min_mib = data['pod_mem_min'][0] / (1024 * 1024)
        ax1.axhline(y=min_mib, color='#4CAF50', linewidth=1.5, linestyle=':',
                    label=f'memory.min ({min_mib:.0f} MiB)')

    # Mark where throttling starts (first evt_high > 0)
    throttle_start = None
    for i, e in enumerate(data['evt_high']):
        if e > 0:
            throttle_start = elapsed[i]
            break

    if throttle_start is not None:
        ax1.axvline(x=throttle_start, color='#FF9800', linewidth=1, alpha=0.5)
        ax1.annotate('throttling starts',
                    xy=(throttle_start, max(mem_mib) * 0.5),
                    xytext=(throttle_start + 2, max(mem_mib) * 0.6),
                    arrowprops=dict(arrowstyle='->', color='#FF9800'),
                    fontsize=9, color='#FF9800')

    # Mark OOM kill
    oom_time = None
    for i in range(1, len(data['evt_oom_kill'])):
        if data['evt_oom_kill'][i] > data['evt_oom_kill'][i-1]:
            oom_time = elapsed[i]
            break

    if oom_time is not None:
        ax1.axvline(x=oom_time, color='#F44336', linewidth=1.5, alpha=0.7)
        ax1.annotate('OOM Kill',
                    xy=(oom_time, max(mem_mib) * 0.9),
                    xytext=(oom_time - 5, max(mem_mib) * 0.95),
                    arrowprops=dict(arrowstyle='->', color='#F44336'),
                    fontsize=10, fontweight='bold', color='#F44336')

    ax1.set_ylabel('Memory (MiB)', fontsize=12)
    ax1.set_title(f'KEP-2570 MemoryQoS: memory.high Throttle Behavior\n'
                  f'(requests=256Mi, limits=512Mi, throttlingFactor={throttle_factor})',
                  fontsize=14, fontweight='bold')
    ax1.legend(loc='upper left', fontsize=9, framealpha=0.9)
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(bottom=0)

    # --- Bottom panel: memory.events counters ---
    ax2.plot(elapsed, data['evt_high'], color='#FF9800', linewidth=1.5,
             label='high (throttle)', marker='', markersize=2)
    ax2.plot(elapsed, data['evt_max'], color='#9C27B0', linewidth=1.5,
             label='max', marker='', markersize=2)
    ax2.plot(elapsed, data['evt_oom'], color='#F44336', linewidth=1.5,
             label='oom', marker='', markersize=2)
    ax2.plot(elapsed, data['evt_oom_kill'], color='#B71C1C', linewidth=2,
             label='oom_kill', marker='', markersize=2)

    ax2.set_xlabel('Time (seconds)', fontsize=12)
    ax2.set_ylabel('Event Count', fontsize=12)
    ax2.legend(loc='upper left', fontsize=9, ncol=4)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(bottom=0)

    # Add text box with key findings
    if throttle_start is not None and oom_time is not None:
        duration = oom_time - throttle_start
        text = (f'Throttle duration: {duration:.1f}s\n'
                f'Throttle events: {max(data["evt_high"])}\n'
                f'NOT stuck (livelock fixed)')
        props = dict(boxstyle='round,pad=0.5', facecolor='#E8F5E9', alpha=0.8,
                    edgecolor='#4CAF50')
        ax1.text(0.98, 0.02, text, transform=ax1.transAxes, fontsize=10,
                verticalalignment='bottom', horizontalalignment='right',
                bbox=props)

    plt.tight_layout()
    path = os.path.join(output_dir, 'memory-throttle-timeline.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {path}")
    return path


def plot_allocation_rate(data, output_dir, throttle_factor):
    """Allocation rate graph showing throttling effect on speed."""
    fig, ax = plt.subplots(figsize=(14, 5))

    elapsed = data['elapsed']
    rates = compute_rate(elapsed, data['anon'], window=3)

    ax.plot(elapsed, rates, color='#1565C0', linewidth=1.5)
    ax.fill_between(elapsed, rates, alpha=0.2, color='#2196F3')

    # Mark memory.high threshold time
    if data['mem_high'][0] > 0:
        high_mib = data['mem_high'][0] / (1024 * 1024)
        # Find when memory first reached memory.high
        for i, m in enumerate(data['mem_current']):
            if m >= data['mem_high'][0]:
                ax.axvline(x=elapsed[i], color='#FF9800', linewidth=1.5,
                          linestyle='--', label=f'Memory reached memory.high ({high_mib:.0f} MiB)')
                break

    ax.set_xlabel('Time (seconds)', fontsize=12)
    ax.set_ylabel('Allocation Rate (MiB/s)', fontsize=12)
    ax.set_title(f'Memory Allocation Rate Over Time (throttlingFactor={throttle_factor})\n'
                 f'Rate drop at memory.high proves kernel throttling works',
                 fontsize=13, fontweight='bold')
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=0)

    plt.tight_layout()
    path = os.path.join(output_dir, 'allocation-rate.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {path}")


def plot_events_detail(data, output_dir, throttle_factor):
    """Detailed memory.events stacked area chart."""
    fig, ax = plt.subplots(figsize=(14, 5))

    elapsed = data['elapsed']

    # Compute per-interval deltas for stacked view
    delta_high = [0] + [max(0, data['evt_high'][i] - data['evt_high'][i-1])
                        for i in range(1, len(elapsed))]
    delta_max = [0] + [max(0, data['evt_max'][i] - data['evt_max'][i-1])
                       for i in range(1, len(elapsed))]
    delta_oom = [0] + [max(0, data['evt_oom'][i] - data['evt_oom'][i-1])
                       for i in range(1, len(elapsed))]

    ax.bar(elapsed, delta_high, width=0.4, color='#FF9800', alpha=0.7, label='high events/interval')
    ax.bar(elapsed, delta_max, width=0.4, bottom=delta_high, color='#9C27B0',
           alpha=0.7, label='max events/interval')

    bottom_oom = [h + m for h, m in zip(delta_high, delta_max)]
    ax.bar(elapsed, delta_oom, width=0.4, bottom=bottom_oom, color='#F44336',
           alpha=0.7, label='oom events/interval')

    ax.set_xlabel('Time (seconds)', fontsize=12)
    ax.set_ylabel('Events per Interval', fontsize=12)
    ax.set_title(f'memory.events Activity (throttlingFactor={throttle_factor})\n'
                 f'Shows kernel intervention frequency as memory grows',
                 fontsize=13, fontweight='bold')
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()
    path = os.path.join(output_dir, 'events-detail.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {path}")


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <csv_file> <output_dir> [throttle_factor]")
        sys.exit(1)

    csv_file = sys.argv[1]
    output_dir = sys.argv[2]
    throttle_factor = float(sys.argv[3]) if len(sys.argv) > 3 else 0.9

    os.makedirs(output_dir, exist_ok=True)

    print(f"Loading data from {csv_file}...")
    data = load_csv(csv_file)
    print(f"Loaded {len(data['elapsed'])} samples over {data['elapsed'][-1]:.1f}s")

    plot_main_timeline(data, output_dir, throttle_factor)
    plot_allocation_rate(data, output_dir, throttle_factor)
    plot_events_detail(data, output_dir, throttle_factor)

    # Print summary stats
    print("\n=== Summary ===")
    peak_mem = max(data['mem_current']) / (1024 * 1024)
    print(f"Peak memory:     {peak_mem:.1f} MiB")
    if data['mem_high'][0] > 0:
        print(f"memory.high:     {data['mem_high'][0] / (1024*1024):.1f} MiB")
    if data['mem_max'][0] > 0:
        print(f"memory.max:      {data['mem_max'][0] / (1024*1024):.1f} MiB")
    print(f"Total high events: {max(data['evt_high'])}")
    print(f"Total oom events:  {max(data['evt_oom'])}")
    print(f"Total oom_kill:    {max(data['evt_oom_kill'])}")

    # Check for livelock
    final_evt_high = max(data['evt_high'])
    final_oom_kill = max(data['evt_oom_kill'])
    duration = data['elapsed'][-1]

    if final_oom_kill > 0:
        print(f"\nVERDICT: Container was OOM-killed after {duration:.1f}s")
        print("         memory.high throttling DID NOT cause livelock.")
        print("         Kernel >= 5.9 fix confirmed working.")
    elif final_evt_high > 0 and duration > 120:
        print(f"\nWARNING: Container was throttled for {duration:.1f}s without OOM-kill.")
        print("         This may indicate livelock behavior (kernel < 5.9?).")
    else:
        print(f"\nContainer ran for {duration:.1f}s. Check pod logs for details.")


if __name__ == '__main__':
    main()
