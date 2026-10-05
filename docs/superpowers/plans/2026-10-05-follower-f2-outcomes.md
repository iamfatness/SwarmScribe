# Follower F2: the `cuda` image on a GPU (ruling R5)

The follower spec has the `cuda` image proven on the development machine, because GitHub's
runners have no GPU. This is the record. CI builds the image and checks what it holds (job
`follower-cuda-image`); it never runs it on a GPU. Repeat this run, and add a section here,
whenever CTranslate2 or `nvidia-cublas-cu12` changes in `uv.lock`.

## The planner's run, 2026-10-05

Machine: Windows 11, Docker Desktop (Engine 29.8.1, WSL 2), NVIDIA GeForce RTX 4090 (24 GB),
driver 617.14. Built in a scratch folder from the `follower-f1` tree with this plan's files.

| What | Result |
|---|---|
| `docker run --rm --gpus all nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi` | worked: the RTX 4090, NVIDIA-SMI 615.78.02, CUDA 13.4 |
| Libraries in the image | ctranslate2 4.8.2, faster-whisper 1.2.1, nvidia-cublas-cu12 12.9.2.10, nvidia-cuda-nvrtc-cu12 12.9.86; no cuDNN |
| `doctor`, `--gpus all --read-only --cap-drop ALL --network none` | `device: cuda (NVIDIA GeForce RTX 4090, 24564 MiB)`; `model: large-v3 (float16) loaded and ran`; 4.7 s |
| Without cuBLAS on the library path | `RuntimeError: Library libcublas.so.12 is not found or cannot be loaded` |
| Without cuDNN on the library path | loaded, warmed up and transcribed five minutes of audio |
| One hour mono, large-v3 float16 | 377 s |
| One hour split | 282 s |
| `CHECK_GPU=1 check-follower-image.sh ... cuda large-v3` | ok (8484 MB as `docker image inspect` counts it; 4.8 GB of layers) |
| `run_e2e.py run --gpu` | passed twice: registered in 6 and 9 s; the killed follower's job redone in 34 and 39 s under the 8 s lease; a stop mid-job in 1.8 and 2.3 s; the hour-long recording refused three times by the memory guard; drain 0, revoke 4 |
| Host memory, large-v3 on the GPU | 741 MiB once loaded, 3155 MiB at the peak of loading; one hour mono adds 3511 MiB |

## The run of Task 6

Run on 2026-10-05, on the same machine (Windows 11, Docker Desktop, RTX 4090), from the
`follower-f2b` tree. Docker's data disk is on D: (1.4 TB free; C: had 93 GB free).

- Step 1 (`nvidia-smi` in a container), this run: worked; the table names
  `NVIDIA GeForce RTX 4090`, NVIDIA-SMI 615.78.02, driver (KMD) 617.14, CUDA 13.4.
- Step 3 (the check's last line), this run: the build took 1 min 56 s and printed
  `baked large-v3: Systran/faster-whisper-large-v3@edaa852ec7e1, 5 files, 3090 MB` (the
  download took 40 s); `CHECK_GPU=1 bash docker/check-follower-image.sh
  swarmscribe-follower:cuda-large-v3 cuda large-v3` took 54 s and ended
  `ok: swarmscribe-follower:cuda-large-v3 is a cuda follower with large-v3 baked in (8486 MB)`.
- Step 4 (the scenario's last line), this run: passed in 2 min 24 s. It printed
  `killed follower-1 mid-job` and `stopped follower-2 mid-job in 1.8 s`, then
  `passed (large-v3 on cuda): two followers registered in 9 s and shared six recordings; a killed follower's job was redone in 40 s under an 8 s lease; a stop mid-job took 1.8 s and counted no attempt; drain exited 0 and revoke exited 4, twice each`.

## What this does not prove

- A Linux host with the NVIDIA Container Toolkit, and Kubernetes with the device plugin:
  only Docker Desktop on WSL 2 was run. The image asks the runtime for
  `NVIDIA_DRIVER_CAPABILITIES=compute,utility` and needs nothing else from it.
- More than one GPU, and any GPU but the RTX 4090.
- A recording of people talking: the long recordings here are one synthetic phrase repeated,
  which large-v3 partly collapses. The benchmark of 2026-10 is the measure of quality and
  speed on real speech.
