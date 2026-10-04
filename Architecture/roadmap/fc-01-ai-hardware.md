# FC-01: hardware options for local inference in the field

**Version:** 0.1
**Status:** Assessed (no purchase or ADR yet)
**Last updated:** 2026-10-04
**Source:** hardware research by Kiril (2026-10-04), checked here against ADR 2, ADR 11, ADR 12 and the
[FC-01 assessment](fc-01-ai-assistant.md).

---

## 1. Summary of the research

For local LLM inference with Ollama the bottleneck is **memory bandwidth** and **memory size** (VRAM or
unified memory), then **power** (watts per token) and **ruggedness**. The research compares four setups:

| Setup | Memory for the model | Bandwidth | Model size that fits (Q4) | Power | Ruggedness |
|-------|---------------------|-----------|---------------------------|-------|------------|
| A. Apple Silicon (MacBook Pro / Mac Studio, M3/M4 Max/Ultra) | 36 to 192 GB unified | 300 to 800 GB/s | up to 70B | 40 to 120 W | Low; needs a case |
| B. NVIDIA Jetson AGX Orin 64 GB, industrial enclosure | 64 GB unified | 205 GB/s | up to 32B | 15 to 60 W, 7 to 28 V DC direct | High |
| C. Mini-ITX with RTX 4000 SFF / 4080 / 4090 in a rugged case | 16 to 24 GB VRAM | 460 to 1000 GB/s | 14B to 32B | 200 to 500 W | Very high, needs filtered airflow |
| D. Rugged laptop (Getac X600, Panasonic Toughbook) with RTX mobile GPU | 8 to 16 GB VRAM | 190 to 500 GB/s | 8B to 14B | 65 to 230 W | MIL-STD-810H, IP65, hot-swap batteries |

It also recommends DC-DC power instead of an inverter (15 to 25 % less loss) and a 500 to 1000 Wh LiFePO4 power
station; at 60 W a 512 Wh station runs about 8 hours.

---

## 2. Fit with this architecture

### 2.1 Constraints from accepted or proposed decisions

| Decision | Effect on the hardware choice |
|----------|-------------------------------|
| [ADR 2](../decisions/0002-single-field-machine-intranet-deployment.md): one field machine, Windows or Linux, **no macOS** (USB HID gateway) | Setup A cannot be the field machine. It could only be a **second machine** used for inference. |
| [ADR 11](../decisions/0011-containers-for-infrastructure-host-for-app.md): database in containers; the compose file pins `platform: linux/amd64` | Setup B is ARM (aarch64). The PostGIS image and `hidapi`, WeasyPrint, Node would need ARM builds and testing; today it does not run the stack as is. |
| [ADR 12](../decisions/0012-loopback-only-network-exposure.md): every listener on loopback | Any **separate** inference box (A, B, C as a server) means the backend calls another machine over a network. That box must sit on a dedicated cable or private link, never on the Starlink network, and it receives personal data (names, positions) in prompts. Needs a new ADR. |
| [TP-03](../principles/architecture-principles.md): one machine, reproducible start | A second box doubles what the operator installs, powers and updates. |
| Owner, 2026-10-03: field machine is a **typical laptop**; power supply in the field not stated | No power budget is defined; setups C (200 to 500 W) and D under load (150 to 230 W) need a power plan. |

### 2.2 What the first use actually needs

The first candidate use is the **situation report** ([FC-01, Section 3](fc-01-ai-assistant.md#3-ranking-of-the-uses)).
It is not latency-critical: a report of 300 to 500 tokens that takes one to two minutes is acceptable.

Rule of thumb: generation speed is at most **memory bandwidth ÷ model size in memory**.

| Machine | Bandwidth | 3B Q4 (about 2 GB) | 8B Q4 (about 5 GB) | 14B Q4 (about 9 GB) |
|---------|-----------|--------------------|--------------------|---------------------|
| Typical laptop, DDR5, CPU only | 60 to 90 GB/s | up to about 30 to 45 tok/s | up to about 12 to 18 tok/s | up to about 7 to 10 tok/s |
| Rugged laptop, RTX mobile 12 to 16 GB | 300 to 500 GB/s | fast | fast | up to about 30 to 50 tok/s |

Real speed is lower than the ceiling (often half). Even so, **a typical laptop may be enough for a 3B to 8B
model writing a report**, if the quality in Bulgarian is acceptable. That is what the prototype must measure.

### 2.3 Ranking for this project

| Setup | Fit | Why |
|-------|-----|-----|
| **Existing typical laptop, CPU only** | **Try first** | No purchase; fits ADR 2, 11, 12 unchanged; enough for a non-urgent report if quality holds |
| **D. Rugged x86 laptop with RTX GPU, Windows or Linux** | **Best fit if a purchase is needed** | It *is* the field machine: one box (TP-03), x86 (ADR 11), loopback (ADR 12), Windows/Linux with USB HID (ADR 2); ruggedness helps the whole system, not only AI |
| B. Jetson AGX Orin | Possible as a separate node only | ARM porting of the stack, or a second box with a network link and a new ADR |
| C. Rugged Mini-ITX with desktop GPU | Poor | Power 200 to 500 W, weight, airflow; a second box |
| A. Apple Silicon | Poor as field machine | macOS excluded by ADR 2; usable only as a second box with a network link |

---

## 3. Corrections to the research

| Point | Correction |
|-------|-----------|
| `llama3.2:34b`, `llama3.2:8b` | Not existing tags: Llama 3.2 text models are 1B and 3B (vision 11B and 90B). Use real tags such as `llama3.1:8b`, `qwen2.5:7b`, `qwen2.5:14b`, `qwen2.5:32b`. |
| Jetson AGX Orin: 32B at 15 to 20 tok/s | Above the bandwidth ceiling: 205 GB/s ÷ about 18 to 20 GB for 32B Q4 gives at most about 10 tok/s. |
| "100 % offline, no network latency" for a central AI node serving the team over Wi-Fi | A team Wi-Fi to an inference node is a network exposure of personal data in prompts; conflicts with ADR 12 unless a new ADR decides it. |
| Vision models for casualty photos | Out of scope: the system does not describe medical conditions (owner, 2026-10-04). Drone or terrain imagery would be a separate future capability. |

---

## 4. Open questions

> ⚠️ **Requires stakeholder input (owner):** what power is available at the command post (vehicle 12/24 V, generator,
> power station, mains)? It decides whether any GPU option is realistic and is relevant to the whole system (R-01).

> ⚠️ **Requires stakeholder input (owner):** is replacing the typical laptop with a rugged laptop on the table
> anyway (dust, rain, drops at the command post)? If yes, choose one with an NVIDIA GPU and 12 to 16 GB VRAM and
> FC-01 needs no second box.

> ⚠️ **Requires technical clarification:** measured tokens per second, RAM, and battery drain of `qwen2.5:3b`,
> `qwen2.5:7b` and `llama3.1:8b` on the current field laptop, and the quality of a Bulgarian situation report
> (FC-01, Section 6).
