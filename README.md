# Autonomous Onboard Decision-Making for CubeSats

*Energy-aware task scheduling, downlink prediction and adaptive compression*

## The problem

A CubeSat generates data all the time but is limited by three things: battery energy (recharged only in sunlight), onboard compute and memory, and short ground-station passes. For every task or data product, the satellite has to decide on its own whether to:

- *process* it now onboard,
- *store* it for later, or
- *transmit* it to the ground, as-is or compressed.

The goal is a closed loop that runs every time a task arrives:

Receive task/data → Analyze situation → Evaluate actions → Choose best action → Execute / schedule
                         ↑                     ↑
                 Task-energy predictor   Downlink/compression predictor

---

## Done so far (Phase 2)

Two predictors are built and evaluated independently. Both are trained on *simulated data* based on published hardware figures, because no flight telemetry was available.

### 1. Task-energy predictor

Predicts how much energy a task will use (*receive + compute + transmit*) before it runs.

- *Physics basis:* E = P × T for each phase, with compute time ≈ CPU cycles ÷ CPU frequency.
- *Data:* 3,000 synthetic tasks across 6 types (image capture, image compression, image classification, object detection, hyperspectral processing, data filtering).
  - Power, frequency and radio ranges come from CubeSat hardware literature and radio datasheets.
  - Realistic overheads (link handshake, CPU contention) and noise were added.
- *Inputs:* 13 features known at the moment the task is assigned: task type, data volume, CPU cycles, memory, CPU frequency, CPU/memory load and radio settings.
  - Anything measured after the task runs is excluded, so the model can't cheat by seeing the answer.
- *Training:* 5 models compared on an 80/20 split (2,400 / 600 tasks) with 5-fold cross-validation. The model is trained on log(1 + E), because a few hyperspectral tasks have very large energies that skew the data.

| Model | MAE (J) | RMSE (J) | R² | MAPE |
|---|---|---|---|---|
| Mean baseline | 585.2 | 1477.7 | −0.002 | 5.485 |
| Linear regression | 253.6 | 1313.3 | 0.209 | 0.502 |
| *Random Forest (retained)* | 115.7 | *578.6* | *0.846* | 0.226 |
| XGBoost | *100.6* | 637.2 | 0.814 | *0.136* |
| MLP | 122.1 | 853.0 | 0.666 | 0.185 |

- *Cross-validation:* R² = 0.951 ± 0.006 (Random Forest) and 0.974 ± 0.004 (XGBoost).
- *By task type:* weakest on object detection (R² 0.566), strongest on hyperspectral processing (R² 0.936). MAPE stays between 0.20 and 0.26 for every type.
- *Bias:* errors are balanced (46.2% over-predictions vs. 53.8% under-predictions).
- *Known limitation:* the ranking of important inputs makes physical sense (CPU cycles, CPU frequency, compute power). But when one input is changed at a time, the predicted energy doesn't always move steadily in the expected direction. This must be fixed before the model runs without supervision.

### 2. Downlink-feasibility and adaptive-compression predictor

For each ground-station pass, predicts whether the full payload can be sent and, if not, how much to compress it.

- *Data:* 50,000 simulated passes covering geometry, path loss, rain attenuation, antenna mispointing, SNR fluctuation and payload size. Statistics from SNR, range and elevation time series during each pass were added as features.
  - Files: aggregated_passes.csv, timeseries_passes_profiles.csv, timeseries_passes_meta.csv, ts_features.csv → aggregated_passes_enriched.csv
- *Model:* XGBoost with two outputs:
  - can_send_all: R² = 0.97
  - recommended_compression_ratio: R² = 0.98
- *Compression pipeline:* if the payload doesn't fit, a compressor is picked by data type before the data goes to the radio:
  - *JPEG* for images (.jpg, .png, .tif)
  - *LZ4* for telemetry (.csv, .txt)
  - *Zstandard* for science/binary data (.bin, .dat)
- *Scripts:*
  - train_XGBoost.py: trains the model.
  - send_with_compressing.py: makes the send/compress decision and runs the compression at transmission time.

---

## Next steps (Phase 3)

1. *Fix the energy predictor's behaviour.* Make predicted energy move steadily in the expected direction when an input changes. Options:
   - gradient boosting with monotonic constraints,
   - a physics estimate (E = P × T) plus a learned correction,
   - extra training data in the problem regions.
2. *Integrate the predictors.* Combine predicted energy, downlink feasibility and compression ratio with live battery charge and daylight/eclipse timing into one decision policy. Start rule-based, then move to reinforcement learning.
3. *Priority-aware scheduling.* Let urgent detections (fire, flood, disaster imagery) jump ahead of routine work, even when they aren't the cheapest option.
4. *Hardware-in-the-loop testing.* Run on an emulated onboard computer with realistic CPU and radio power draws, to check whether the simulated ranges hold up.
5. *Closed-loop evaluation of store vs. send.* Test the full store/process/transmit loop over simulated timelines of many orbits, tracking battery charge, stored backlog and total data sent.
6. *Real data, where possible.* Check both predictors against real subsystem telemetry or a ground-based RF testbed before claiming accuracy beyond simulation.

*Reporting to add:* because can_send_all is a yes/no output, report accuracy, F1 and the false "can send" rate alongside R².

---

## Key references

- Rigo et al., 2021; Slongo et al., 2018; Seman et al., 2023: energy-aware CubeSat scheduling
- Ramezani et al., 2023: safe hierarchical reinforcement learning for CubeSat scheduling
- Sabri et al., 2021 (DVFS); Arnold et al., 2012 (FPGA energy budgets): hardware power ranges
- ITU-R P.618: rain attenuation / link budget
- Chen & Guestrin, 2016: XGBoost
