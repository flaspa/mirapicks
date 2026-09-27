# Mira Picks

Mira Picks is an autonomous AI fashion publication, built for the Vultr Agent Arena Hackathon (Challenge 1: safe agent execution).

A small desk of AI agents decides what deserves to be published, then builds, checks and publishes the story.

| | URL |
|---|---|
| Public publication | https://www.mirapicks.com |
| Agent console (backstage) | https://agents.mirapicks.com |

## Editorial flow

```text
Human: editorial topic (or blank)
  → Miranda (Editor-in-Chief)   "What are we covering?"      assignment: topic, question, angle, keywords
  → Andy (Fashion Press Scout)  "What is the fashion press saying?"   picks 4–6 publications, reads them in a sandbox
  + Emily (Social Scout)        "What is social culture doing?"       searches her TikTok clipping book
  → Nigel (Fashion Director)    "Do these worlds agree?"              for / against / agreements / contradictions
  → Miranda                     "Is this worth publishing?"           FEATURE / WATCH / PASS
  → FEATURE only: Developer writes the page → Technical QA renders it in a sandbox (one repair) → publish
```

The agent console runs the research half live. The publisher (`python -m backend.publish`) runs the same research and continues FEATURE stories into Developer, QA and publication.

## Vultr

- **Control plane:** one Vultr VM runs the FastAPI console, orchestration, run state, the Bright Data collector, the sandbox dispatcher and the publication server.
- **Reasoning:** every agent call goes through Vultr Serverless Inference.

| Agent | Model |
|---|---|
| Miranda | glm-5.3 |
| Andy | nemotron-3-nano-omni-30b-a3b-reasoning |
| Emily | qwen3.8-flash-next |
| Nigel | deepseek-v4-flash-0731 |
| Developer | deepseek-v4-flash-0731 |
| Technical QA | deterministic browser checks, no model |

## Sandboxing

All browsing and all execution of generated code happens outside the FastAPI process. The current implementation uses one disposable Docker container per task, built from `sandbox/Dockerfile`.

Each container runs with:
- a read-only root filesystem
- a non-root user
- all capabilities dropped and no-new-privileges
- limits of 1 CPU, 1 GB of memory and 512 processes
- a hard timeout
- no secrets or environment variables
- a single per-run output folder as its only mount

Each container is removed after its run. To see containment in action:

```sh
python -m backend.containment_demo
```

## Hackathon requirements coverage

Mira Picks implements all required Challenge 1 technology components.

| Challenge 1 requirement | Mira Picks implementation |
| :--- | :--- |
| **Vultr VM backend** | A Vultr VM is the central control plane for FastAPI, agent orchestration, run state, Bright Data integration, sandbox dispatch and publication. |
| **Web application deployed on Vultr** | The backstage console and public publication are deployed on the Vultr-hosted system at `agents.mirapicks.com` and `www.mirapicks.com`. |
| **Vultr Serverless Inference for agent reasoning** | Miranda, Andy, Emily, Nigel and Developer all call models through the shared Vultr Serverless Inference client. |
| **Sandbox isolated from the application process** | Browser execution and Technical QA run in disposable Docker containers outside the FastAPI process. Containers receive no application secrets and are destroyed after each task. |

Mira Picks also implements several strongly recommended elements:

- **Docker as the sandbox boundary**
- **REST APIs** through FastAPI
- **Web dashboard** showing the multi-agent execution and evidence flow
- **Human approval before publication** through the authenticated staging → review → publish workflow
- **Playwright in Docker** for browser execution and deterministic QA

### Containment demonstration

The repository includes:

```sh
python -m backend.containment_demo

## NetBird

Public HTTPS reaches the VM through the NetBird reverse proxy and overlay network. The application services bind only to `127.0.0.1` and the NetBird overlay address. The VM's firewall allows only SSH inbound. No application port is open to the internet.

## More

- [ARCHITECTURE.md](ARCHITECTURE.md): full architecture, marking current and planned components.
- [DEMO.md](DEMO.md): demo steps and the one-minute script.
