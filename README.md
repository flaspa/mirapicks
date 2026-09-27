# Mira Picks

Mira Picks is an autonomous AI fashion influencer built for the Vultr Agent Arena Hackathon.

## Architecture

Mira runs as a central orchestrator on a Vultr VM.

Agent reasoning uses Vultr Serverless Inference.

Untrusted browser and code execution runs in isolated disposable sandboxes.

## Initial Milestone

Human → Mira → Scout Sandbox → Real Web Evidence → Mira

## Scout sandbox (local)

The Scout is a Playwright container that inspects one URL. It prints bounded
JSON evidence on stdout and writes `screenshot.png` to a per-run folder. It
receives no secrets and runs read-only, non-root, with all capabilities dropped
and CPU, memory and process limits.

```sh
docker build -t mira-scout:0.1 sandbox
python -m backend.sandbox https://www.allbirds.com/products/mens-tree-runners
```

Each run writes `artifacts/<run_id>/evidence.json` and `screenshot.png`.

