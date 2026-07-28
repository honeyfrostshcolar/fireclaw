# BGE Model Download Readiness

## 2026-07-28T00:00+08:00

### Task Goal

Determine whether the real BGE-M3 embedding model and
BGE-reranker-v2-m3 reranker can now be downloaded, how they should be
downloaded, and where FireClaw expects them.

### Repository Findings

- Existing code and design documents consistently use:
  - `.cache/models/bge-m3`
  - `.cache/models/bge-reranker-v2-m3`
- `BGEM3EmbeddingProvider` and `BGEFlagRerankerProvider` accept these as local
  model directories.
- `.cache/models/` is not currently covered by the root `.gitignore`; it should
  be ignored before downloading to prevent accidental model commits.
- No local model cache currently exists.

### Machine Findings

- Filesystem free space: approximately 20 GB.
- NVIDIA driver is not currently usable:
  `nvidia-smi` cannot communicate with the driver.
- `/home/lpp/miniconda3/envs/py310` has PyTorch 2.8.0+cu126, but
  `torch.cuda.is_available()` is false.
- `huggingface_hub`, `FlagEmbedding`, `transformers`, and `safetensors` are not
  installed in the available environment.
- The repository declares Python >=3.11, but only the Python 3.10 conda
  environment is currently available; a proper Python 3.11 runtime remains a
  separate environment task.

### Official Model Information Checked

- `BAAI/bge-m3`
  - official repository total: approximately 4.59 GB;
  - dense dimension: 1024;
  - sequence length: 8192;
  - repository includes an ONNX directory and images that FireClaw's
    FlagEmbedding path does not need.
- `BAAI/bge-reranker-v2-m3`
  - official repository total: approximately 2.29 GB;
  - main `model.safetensors`: approximately 2.27 GB;
  - multilingual reranker intended to work with BGE-M3 retrieval.
- Hugging Face officially recommends `snapshot_download()` or `hf download`;
  `local_dir` preserves repository structure and download metadata.

### Recommendation

1. Add `.cache/models/` to `.gitignore`.
2. Install only `huggingface_hub` first.
3. Resolve each repository's current full commit SHA using `HfApi.model_info`.
4. Download snapshots into the two existing default model directories.
5. For BGE-M3, exclude `onnx/*`, `imgs/*`, and image files.
6. Record the resolved SHAs for reproducibility.
7. Verify required files and disk usage before installing the heavier
   FlagEmbedding runtime.
8. Perform CPU load smoke tests first; CUDA testing is blocked until the
   NVIDIA driver works.

### Capacity Conclusion

The optimized model download is expected to consume approximately 4.6 GB,
which fits in the current 20 GB free space. A separate Python 3.11 environment
plus CUDA PyTorch and FlagEmbedding may consume several additional gigabytes,
so disk usage should be checked again before runtime dependency installation.

### Worktree Safety

No model was downloaded and no dependency was installed in this turn. Existing
uncommitted work was not modified.

## 2026-07-28T00:07+08:00 - GPU Recheck

The user correctly noted that the host should have a working NVIDIA driver and
asked for a recheck inside the `py310` conda environment.

The first check was misleading because the managed Codex sandbox hides NVIDIA
device nodes. A read-only check outside that sandbox confirmed:

```text
NVIDIA driver: 535.230.02
driver CUDA capability: 12.2
GPU: NVIDIA GeForce GTX 1650
VRAM: 4096 MiB
py310 torch: 2.8.0+cu126
torch.cuda.is_available(): True
torch.cuda.device_count(): 1
```

Activating `py310` also runs the user's environment hook that clears the
ROS-contaminated `PYTHONPATH`.

Conclusion:

- The host driver and `py310` CUDA runtime are working.
- The earlier negative result was a sandbox visibility artifact, not a broken
  host driver.
- Both providers default to FP16 on CUDA and lazily retain their loaded models.
- A 4 GB GTX 1650 may run each model separately with conservative batch/length
  settings, but keeping BGE-M3 and the reranker resident together for
  `hybrid_rerank` is likely to exceed VRAM. This requires a real load test and
  probably smaller batches, shorter lengths, CPU placement for one model, or
  explicit model unloading.

## 2026-07-28T00:12+08:00 - System-Level Model Location

The user clarified that "root directory" means the system root filesystem
(`/`), not the repository root.

Filesystem capacity:

```text
/      /dev/nvme0n1p10  86 GB total, 45 GB free
/home  /dev/nvme0n1p9   38 GB total, 20 GB free
```

Revised recommendation:

- Do not place multi-gigabyte models inside the Git repository for deployment.
- Do not create loose nonstandard directories such as `/bge-m3`.
- Use system application state:
  - `/var/lib/fireclaw/models/bge-m3`
  - `/var/lib/fireclaw/models/bge-reranker-v2-m3`
- The existing provider cache calculation naturally places Hugging Face cache
  data at `/var/lib/fireclaw/huggingface` for these paths.
- `/var/lib/fireclaw` is on the root filesystem with materially more free
  capacity than `/home`.
- `/opt/fireclaw/models` is also defensible for immutable administrator-managed
  assets, but `/var/lib/fireclaw/models` better describes downloaded,
  versioned runtime state.

## 2026-07-28T00:18+08:00 - User-Preferred Development Location

The user presented a more development-specific placement recommendation:

```text
/srv/lpp-extra/fireclaw/models
/srv/lpp-extra/fireclaw/huggingface
/srv/lpp-extra/fireclaw/indexes
```

Verified:

```text
/srv/lpp-extra exists
owner: lpp:lpp
filesystem: /dev/nvme0n1p10
root-filesystem free space: approximately 45 GB
/srv/lpp-extra/fireclaw does not exist yet
```

This is now the preferred current-machine development layout. It keeps
multi-gigabyte runtime assets out of the Git worktree, uses the larger root
filesystem, and remains writable by the `lpp` development user. It also aligns
with the providers' cache calculation: a model at
`/srv/lpp-extra/fireclaw/models/<name>` resolves its shared Hugging Face cache
to `/srv/lpp-extra/fireclaw/huggingface`.

For a later package-managed systemd deployment with a dedicated service user,
`/var/lib/fireclaw` remains the conventional state directory. Moving is not
strictly required if the service is deliberately configured to use `/srv`;
ownership and systemd write restrictions must simply permit the selected path.
