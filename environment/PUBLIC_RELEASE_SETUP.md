# Validated reproduction environment

Canonical platform: Linux x86_64. Python 3.10.19, NumPy 2.2.6, SciPy 1.15.2,
threadpoolctl 3.6.0, OpenBLAS 0.3.30 pthreads. Use `public-release-explicit.txt`
for exact conda-forge package builds and `public-release-lock.json` for SHA records.

```bash
conda create -n td-repro --file environment/public-release-explicit.txt
conda activate td-repro
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
```

Allocate 17 CPUs for applicable 16-worker stages plus the coordinator. Worker/fidelity
policies are frozen. No BLAS/OpenMP oversubscription. The broader environment YAML
is a convenience description, not an independently validated substitute for the
explicit lock. Accepted scientific validation was staged with documented live
repairs. Publication tests do not constitute a new heavy integrated run.
