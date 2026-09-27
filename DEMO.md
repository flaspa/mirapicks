# Mira Picks — Demo Guide

This guide covers the three recorded moments for the one-minute video. Every command below is read-only or runs only inside a disposable sandbox. None changes the server.

All server commands assume SSH access to the Vultr VM. On the development laptop, use Windows OpenSSH, because the key is held by the Windows ssh-agent.

```sh
ssh root@45.63.94.3
cd /opt/mirapicks
```

---

## 1. Editorial run (agent console)

1. Open https://agents.mirapicks.com.
2. Type a topic, for example `streetwear / Gen Z style`, or leave it blank so Miranda chooses. Click **Scout it**.
3. Narrate as the stages light up. A full run takes about 1.5–2 minutes, so plan to cut or speed up the recording.
   - **Miranda:** the editorial assignment appears, with the question, angle and keywords.
   - **Andy:** the chosen publications appear, each with a reason, a status and the article read, followed by the press signal.
   - **Emily:** shows "TikTok clipping book · N of 762 clips matched", with her social signals and representative posts.
   - **Nigel:** agreements, contradictions, for and against.
   - **Miranda:** the verdict, FEATURE, WATCH or PASS, with a headline.
4. Then open https://www.mirapicks.com to show stories that FEATURE verdicts produced through Developer → QA → publish.

### 1b. Demo Publish (authenticated, all the way to publication)

Public visitors can only run research. To show the full pipeline, use the protected Demo Publish mode.

1. **Once, before recording**, set the PIN on the VM. It prompts without echoing, then restarts only the console:
   ```sh
   ssh -t root@45.63.94.3 /opt/mirapicks/artifacts/set_demo_pin.sh
   ```
2. On https://agents.mirapicks.com, click **Demo publish** under the topic box and enter the PIN. The panel "Demo mode · Demo publishing enabled" appears. The session lasts 4 hours, and **Lock** ends it.
3. Choose **Runway / Couture**, **Streetwear / Gen Z** or **Budget Shopping**, then click **Run Demo Publish**.
4. The stages run live, and each is a real execution:
   - Miranda writes the assignment.
   - Andy selects and reads the publications.
   - Emily reads the cached demo clip set. The console says so, and no live Bright Data is used.
   - Nigel compares the evidence, and Miranda gives her verdict.
   - Only if the verdict is FEATURE does the "Developer → Technical QA → Publish" card continue: the brief, then the page, then QA in the sandbox, then publication.
   - WATCH or PASS shows "Not published — Miranda returned …".
5. On success the card shows the live story link. Each preset uses a fixed product, so a rehearsal **updates that story in place**. It never adds a duplicate.

A full demo publish takes about 2.5–3 minutes, so plan to cut or speed up the recording.

---

## 2. Containment moment (Challenge 1)

This runs deliberately hostile code in the same disposable sandbox every agent uses. It acts only inside the container.

```sh
cd /opt/mirapicks && /opt/mirapicks-venv/bin/python -m backend.containment_demo
```

Expected output, verified on the VM:

```text
sandbox: disposable container mira-scout-containment-xxxxxx (read-only, non-root, no secrets, 1 CPU, 1 GB, 512 pids, 10s timeout)

user inside sandbox: uid=1001 (not root)
BLOCKED  overwrite sandbox code /app/dist   EROFS
BLOCKED  write system file /etc/passwd      EACCES
BLOCKED  create file in /usr/bin            EROFS
BLOCKED  read host secrets /opt/mirapicks/.env ENOENT
BLOCKED  find API keys in environment       none present
ALLOWED  write own scratch space /out       ok (per-run folder only)
starting infinite loop... (host timeout will kill this container)

HOST: timeout after 10.1s -> container killed
HOST: container still present afterwards? no (removed by --rm)
HOST: files the sandbox could write: ['scratch.txt'] in /opt/mirapicks/artifacts/containment-xxxxxx
```

To show the app was unaffected, run:

```sh
systemctl is-active mirapicks.service && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/
```

It should print `active` and then `200`.

---

## 3. NetBird zero-port moment (bonus)

**On the VM**, show what listens where. This prints no secrets.

```sh
ss -tlnH | awk '{print $4}' | sort -u
ufw status
```

Expected:
- Application ports `:8000` and `:8100` listen only on `127.0.0.1` and on the NetBird overlay address `100.101.92.248`.
- Only `:22` (SSH) listens publicly.
- The firewall shows `Status: active`, allowing only `22/tcp`.

**From any laptop**, try the app port on the VM's public IP. The connection fails.

```sh
curl -m 6 http://45.63.94.3:8000/
```

Expected: a connection timeout or refusal, with no HTTP response.

**Then in a browser**, open https://agents.mirapicks.com or https://www.mirapicks.com. It loads over HTTPS through the NetBird reverse proxy.

---

## 4. One-minute video script (about 60 seconds)

| Time | Screen | Voice-over |
|---|---|---|
| 0:00–0:08 | www.mirapicks.com front page | "This is Mira Picks, a fashion magazine with no human editors. Every story here was assigned, researched, judged, built and checked by AI agents." |
| 0:08–0:14 | agents.mirapicks.com; type "streetwear / Gen Z style", click Scout it | "Behind it is Miranda's editorial desk. I give her a topic, or none, and she writes the assignment." |
| 0:14–0:24 | Andy's panel: publications, reasons, article links | "Andy decides which fashion publications matter for this story and reads them in a disposable browser sandbox." |
| 0:24–0:31 | Emily's panel: clipping-book matches, social signals | "Emily checks what social culture is actually doing, from a TikTok clipping book collected in the background with Bright Data." |
| 0:31–0:38 | Nigel's agreements and contradictions, then Miranda's verdict | "Nigel compares the press with the street. Miranda makes the call: feature, watch or pass." |
| 0:38–0:45 | A published story page, then its QA screenshots | "Only features go to the Developer agent. It writes the page, and a sandboxed QA browser must pass it before it goes live." |
| 0:45–0:53 | Terminal: containment demo, BLOCKED lines, container killed | "Anything untrusted runs in throwaway containers. Here hostile code tries to escape. It's read-only, it has no secrets, and it's killed at the timeout, while the app keeps running." |
| 0:53–1:00 | Terminal: `curl` to :8000 fails; browser loads agents.mirapicks.com | "The whole system runs on a Vultr VM with Vultr Serverless Inference, and it's reachable only through NetBird. No application ports are open." |
