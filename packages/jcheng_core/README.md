# jcheng_core

Shared pure-Python primitives used by the Studio backend:

- `jcheng_stats` — paired-trial and experiment statistics.
- `jcheng_vision` — point-cloud segmentation, fitting and locate primitives.

Ported from the frozen `jcheng-manipulation` baseline (`packages/jcheng_core`
at `a08b8b6`). This is project code under the baseline MIT license; no vendor
source, SDK or model weight is included. It is installed editable by the quick
start and CI:

```bash
pip install -e packages/jcheng_core --no-deps
```

The package has no ROS dependency and no network or device access.
