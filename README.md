# KEP-2570: MemoryQoS Benchmark Results

## Test Environment

| Component | Version |
|-----------|---------|
| Kernel | 6.18.5-100.fc42.x86_64 |
| Kubelet | v1.36.0-alpha.2.710+b04eff2bcba (commit `2fba371e944`) |
| Container Runtime | containerd 2.2.0 |
| Cluster | kind, single control-plane node (fresh cluster) |
| cgroup | v2 |

The kind node image was built from the [memqos-kernel-metrics-e2e](https://github.com/sohankunkerkar/kubernetes/tree/memqos-kernel-metrics-e2e) branch using `kind build node-image`. Kubelet configuration: [`manifests/kind-cluster.yaml`](manifests/kind-cluster.yaml). System pods (coredns, kube-proxy, etc.) were running during all tests and contribute to cgroup hierarchy totals.

Data was collected by polling cgroup files (`memory.current`, `memory.high`, `memory.max`, `memory.min`, `memory.events`, `memory.stat`) every 1s from the container's cgroup path on the node. Collection script: [`scripts/collect-cgroup-data.sh`](scripts/collect-cgroup-data.sh).

---

## 1. memory.high Throttle Behavior (Livelock Fix Verification)

Beta was blocked in v1.28 because a [kernel bug](https://github.com/torvalds/linux/commit/b3ff92916af) (fixed in 5.9) caused processes to get stuck in an infinite reclaim loop at `memory.high`. This test verifies the fix on a modern kernel.

**Pod spec**: [`manifests/throttle-test-pod.yaml`](manifests/throttle-test-pod.yaml) - Burstable pod, requests=256Mi, limits=512Mi. Container allocates 10 MiB/s, touching every page.

**Kubelet config**: `memoryThrottlingFactor=0.9` (kubelet default)

Expected cgroup values:
- `memory.high` = 256 + 0.9 * (512 - 256) = 486.4 MiB
- `memory.max` = 512 MiB
- `memory.min` = 256 MiB (HardReservation)

### Results

| Metric | Value |
|--------|-------|
| Time to reach memory.high | ~48s |
| Peak memory | 511.5 MiB |
| Total `memory.events` high counter | 3,019 |
| Duration to OOM-kill | 67s |
| Pod exit reason | OOMKilled |
| Data points collected | 66 |

Raw data: [`data/01-throttle-test-factor-0.9.csv`](data/01-throttle-test-factor-0.9.csv)

Top panel: memory usage over time. Bottom panel: cumulative `memory.events` high counter.

![memory-throttle-timeline](images/01-memory-throttle-timeline.png)

The container went from 486 MiB to 512 MiB under throttling and was OOM-killed as expected. On kernels < 5.9, this same workload got stuck indefinitely at `memory.high`. The kernel 5.9 fix resolves that.

---

## 2. memory.min Protection

Two Burstable pods on the same node - one holding memory below its requests, one allocating aggressively.

**Pod specs**: [`manifests/protection-test-pods.yaml`](manifests/protection-test-pods.yaml)
- Protected: requests=256Mi, limits=512Mi, holds 200 MiB
- Aggressor: requests=64Mi, limits=512Mi, allocates 10 MiB/0.5s

### Results

| Pod | memory.min | memory.current | memory.high | Status |
|-----|-----------|----------------|-------------|--------|
| Protected | 256 MiB | 204 MiB | 486 MiB | Running, unaffected |
| Aggressor | 64 MiB | 501 MiB | 467 MiB | Running, throttled (5,551 high events) |

Cgroup hierarchy `memory.min` values:

| Level | memory.min |
|-------|-----------|
| kubepods.slice | 354 MiB |
| kubepods-burstable.slice | 304 MiB |
| Protected pod | 256 MiB |
| Aggressor pod | 64 MiB |

![cgroup-hierarchy](images/06-cgroup-hierarchy.png)

Protected pod's 204 MiB stayed intact. `memory.min` propagates correctly through the full cgroup hierarchy.

---

## 3. Guaranteed Pod Behavior

Guaranteed pod (requests = limits = 256Mi) with MemoryQoS enabled.

**Pod spec**: [`manifests/guaranteed-test-pod.yaml`](manifests/guaranteed-test-pod.yaml)

| cgroup knob | Value |
|-------------|-------|
| memory.high | `max` (no throttling) |
| memory.max | 256 MiB |
| memory.min | 256 MiB |

![guaranteed-vs-burstable](images/07-guaranteed-vs-burstable.png)

`memory.high=max` means Guaranteed pods are not throttled. `memory.min=requests` gives them full reclaim protection. No overcommit means no need for throttling.

---

## 4. BestEffort Pod Behavior

Pod with no resource requests or limits.

**Pod spec**: [`manifests/besteffort-test-pod.yaml`](manifests/besteffort-test-pod.yaml)

| cgroup knob | Value |
|-------------|-------|
| memory.high | `max` |
| memory.max | `max` |
| memory.min | 0 |

No throttling, no protection, no limit. `memory.min=0` means the kernel can reclaim all their memory under pressure.

---

## 5. Multi-Container Pod

Two containers in one pod: container-a (requests=128Mi, limits=256Mi) and container-b (requests=64Mi, limits=128Mi).

**Pod spec**: [`manifests/multi-container-test-pod.yaml`](manifests/multi-container-test-pod.yaml)

| Resource | memory.min | memory.high |
|----------|-----------|------------|
| Container A (req=128Mi, lim=256Mi) | 128 MiB | 243 MiB |
| Container B (req=64Mi, lim=128Mi) | 64 MiB | 121 MiB |
| **Pod total** | **192 MiB** | -- |

Pod-level `memory.min` = 128 + 64 = 192 MiB. Each container's `memory.high` is computed independently using `floor[(requests + factor * (limits - requests)) / pageSize] * pageSize`.

---

## 6. Pod Deletion Cleanup

Created a pod with requests=200Mi, verified the QoS-class `memory.min` increased by 200 MiB, deleted the pod, verified `memory.min` returned to its original value after the next reconciliation loop (60s).

**Pod spec**: [`manifests/deletion-test-pod.yaml`](manifests/deletion-test-pod.yaml)

| State | Burstable QoS memory.min |
|-------|-------------------------|
| Before pod creation | 496 MiB |
| After pod created (+ 60s loop) | 696 MiB (+200 MiB) |
| After pod deleted (+ 60s loop) | 496 MiB (back to original) |

The periodic `setMemoryQoS` loop correctly adds and removes `memory.min` contributions as pods come and go.

---

## 7. Application Latency Impact

Measured operation latency (1000 dict insertions) while gradually allocating memory from 0 to 512 MiB with memory.high at 486 MiB.

| Memory | Latency | Ratio vs baseline |
|--------|---------|-------------------|
| 0-480 MiB | ~0.7-1.6ms | 1x |
| 490 MiB | 110ms | ~150x |
| 495 MiB | 239ms | ~340x |
| 500 MiB | 336ms | ~480x |
| 505 MiB | 1,023ms | ~1,460x |

Raw data: [`data/latency-impact-factor-0.9.csv`](data/latency-impact-factor-0.9.csv)

![latency-impact](images/10-latency-impact.png)

Latency is flat until memory crosses memory.high (486 MiB), then degrades progressively. At 505 MiB (19 MiB past memory.high), a 1000-op batch takes over 1 second. This quantifies the silent degradation concern raised in [#2570 comment](https://github.com/kubernetes/enhancements/issues/2570#issuecomment-3960592763). The `memory.events` high counter is the most direct kernel-native signal for this throttling.

---

## 8. Node-Level Metric

Queried `/metrics` on the kubelet.

```
# HELP kubelet_memory_qos_node_memory_min_total_bytes [ALPHA] Total cgroup v2 memory.min
# in bytes across all pods on the node.
# TYPE kubelet_memory_qos_node_memory_min_total_bytes gauge
kubelet_memory_qos_node_memory_min_total_bytes 5.05413632e+08
```

482 MiB = sum of `memory.min` across all pods. Updated every 60 seconds in the `setMemoryQoS` loop. Feature-gated behind `MemoryQoS`.

---

## 9. Pod Without Memory Limit

Burstable pod with requests=128Mi but no memory limit. Node allocatable: 63,996 MiB.

**Pod spec**: [`manifests/no-limit-test-pod.yaml`](manifests/no-limit-test-pod.yaml)

| cgroup knob | Value |
|-------------|-------|
| memory.min | 128 MiB |
| memory.high | 57,609 MiB |
| memory.max | `max` |

When no limit is set, `memory.high = 128 + 0.9 * (63,996 - 128) = 57,510 MiB` (actual 57,609 after page alignment).

---

## 10. High Aggregate memory.min

Three pods each requesting 8Gi on a node with 64Gi allocatable. Sum is below allocatable in this test.

| Level | memory.min |
|-------|-----------|
| Pod 1 | 8,192 MiB |
| Pod 2 | 8,192 MiB |
| Pod 3 | 8,192 MiB |
| burstable QoS total | 24,816 MiB |
| kubepods total | 24,866 MiB |
| node allocatable | 63,996 MiB |

If sum(memory.min) exceeds available memory, the kernel proportionally reduces each cgroup's effective protection based on sibling ratios ([cgroup v2 docs](https://docs.kernel.org/admin-guide/cgroup-v2.html)).

---

## 11. Kubelet Overhead

50 Burstable pods (requests=32Mi, limits=64Mi) at steady state.

| Metric | Value |
|--------|-------|
| Kubelet CPU (30s average) | 2.00% of one core |
| Kubelet memory | 79 MiB |
| Pods managed | 50 |

2.00% CPU is the total kubelet CPU, not MemoryQoS alone. The `setMemoryQoS` loop runs every 60s. Writing `memory.min=0` when already set is a kernel no-op (~20us per pod).

---

## 12. Rollback Safety

Burstable pod (requests=128Mi, limits=256Mi). Disabled MemoryQoS by removing `memoryReservationPolicy` and setting `MemoryQoS: false`, then restarted kubelet. Waited 90s.

| cgroup knob | Before | After (90s) |
|-------------|--------|-------------|
| pod memory.min | 128 MiB | 0 (cleared) |
| burstable QoS memory.min | -- | 0 (cleared) |
| container memory.high | 243 MiB | 243 MiB (stale) |
| container memory.min | 128 MiB | 128 MiB (stale) |

QoS-class and pod-level values are cleared by the reconcile loop. Container-level values persist because they are set via `Unified` at creation time and require CRI runtime support to update. Both `memoryReservationPolicy` and the feature gate must be removed before restart, otherwise kubelet validation rejects the config and won't start.

---

## 13. Repeated Trials (factor 0.9)

Three runs of the same throttle test.

| Trial | Duration (s) | Throttle events | Outcome |
|-------|-------------|-----------------|---------|
| 1 | 63 | 2,677 | OOMKilled |
| 2 | 75 | 4,140 | OOMKilled |
| 3 | 61 | 2,713 | OOMKilled |
| **Median** | **63** | **2,713** | |

Variance is 61-75s (19% spread). All three follow the same pattern.

---

## Notes

- `memoryThrottlingFactor=0.9` is the kubelet default. All tests use this value.
- memory.high formula: `floor[(requests + factor * (limits - requests)) / pageSize] * pageSize`. MiB values in tables are rounded from exact byte values.
- When no memory limit is set, `limits` in the formula is replaced with node allocatable memory.
- Data was collected by polling cgroup files directly on the node (not via kubelet API). Surfacing `memory.events` through the kubelet stats API is a separate effort (PR [#137760](https://github.com/kubernetes/kubernetes/pull/137760)).

---

## References

- [KEP-2570: Memory QoS](https://github.com/kubernetes/enhancements/issues/2570)
- [Kernel livelock fix (5.9)](https://github.com/torvalds/linux/commit/b3ff92916af)
- [v1.28 beta stall report](https://docs.google.com/document/d/1mY0MTT34P-Eyv5G1t_Pqs4OWyIH-cg9caRKWmqYlSbI/edit)
- [cgroup v2 memory controller docs](https://docs.kernel.org/admin-guide/cgroup-v2.html)
- [Kubelet PR #137719 (kernel check, metrics, E2E tests)](https://github.com/kubernetes/kubernetes/pull/137719)
- [Kubelet PR #137760 (CRI MemoryEvents)](https://github.com/kubernetes/kubernetes/pull/137760)
