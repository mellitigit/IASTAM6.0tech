# Satellite Data Decision Engine (Team Aachiri)

*AI that decides when to process, store or send.* IEEE IASTAM, ESPRIT Student Branch Joint Chapter.

## The problem

A CubeSat generates data all the time but is limited by three things: battery energy (recharged only in sunlight), onboard compute and memory, and short ground-station passes (8-12 min of contact every ~1.5 h). About 70% of images are cloudy or useless, and disaster alerts can reach rescuers hours late.

For every task or data product, the satellite has to decide on its own whether to:

1. process it now onboard,
2. store it for later, or
3. transmit it to the ground, as-is or compressed.

The goal is a closed loop that runs every time a task arrives:

```
Receive task/data -> Analyze situation -> Evaluate actions -> Choose best action -> Execute / schedule
                                              ^                      ^
                              Task-energy predictor      Downlink / compression / radio-link predictors
                              Disaster triage (priority)
```

### Existing work and what is missing

NASA/JPL Dynamic Targeting (CogniSAT-6, tested 2025), NASA/JPL SYNOPSIS and ESA Phi-sat-1/2 each solve one part (cloud avoidance, data prioritisation, onboard filtering). What is missing is a single safe manager that weighs battery, temperature, storage, data urgency, AI confidence, time to next contact and contact capacity together. Our approach is hybrid: simple safety rules first, then AI inside those limits, with a safe fallback to priority rules if the AI fails.

##  four predictors

All predictors are evaluated independently. Three are trained on simulated data based on published hardware and radio figures (no flight telemetry was available); the disaster model uses real Sentinel-2 imagery.

| # | Predictor | Chosen model | Headline results |
|---|-----------|--------------|------------------|
| 1 | Task energy | Random Forest | R2 0.846, MAE 115.7 J, RMSE 578.6 J, MAPE 0.226, CV R2 0.951 |
| 2 | Downlink feasibility + adaptive compression | XGBoost (2 outputs) | R2 0.97 (`can_send_all`), 0.98 (`recommended_compression_ratio`) |
| 3 | Disaster triage (DVM) | MobileNetV3, 6 bands, Focal Loss | 100% alert-level recall, 76.5% precision, 6.45 ms, 12.05 MB |
| 4 | Radio-link prediction | HistGradientBoosting | ROC-AUC 0.98, F1 0.93, ECE 0.002 |

### 1. Task-energy predictor

Predicts how much energy a task will use (receive + compute + transmit) before it runs.

- **Physics basis:** E = P x T for each phase, with compute time = CPU cycles / CPU frequency.
- **Data:** 3,000 synthetic tasks across 6 types (image capture, image compression, image classification, object detection, hyperspectral processing, data filtering). Power, frequency and radio ranges come from CubeSat hardware literature and radio datasheets. Link-handshake and CPU-contention overheads and noise were added.
- **Inputs:** 13 features known when the task is assigned (task type, data volume, CPU cycles, memory, CPU frequency, CPU/memory load, radio settings). Anything measured after the task runs is excluded, so the model cannot see the answer.
- **Training:** 5 models on an 80/20 split (2,400 / 600) with 5-fold CV. Target is log(1 + E) because a few hyperspectral tasks have very large energies.

| Model | MAE (J) | RMSE (J) | R2 | MAPE |
|-------|--------:|---------:|---:|-----:|
| Mean baseline | 585.2 | 1477.7 | -0.002 | 5.485 |
| Linear regression | 253.6 | 1313.3 | 0.209 | 0.502 |
| **Random Forest (retained)** | 115.7 | 578.6 | 0.846 | 0.226 |
| XGBoost | 100.6 | 637.2 | 0.814 | 0.136 |
| MLP | 122.1 | 853.0 | 0.666 | 0.185 |

- Cross-validation: R2 = 0.951 +/- 0.006 (Random Forest), 0.974 +/- 0.004 (XGBoost).
- By task type: weakest on object detection (R2 0.566), strongest on hyperspectral processing (R2 0.936). MAPE stays between 0.20 and 0.26 for every type.
- Bias: 46.2% over-predictions vs 53.8% under-predictions.
- **Known limitation:** the feature-importance ranking makes physical sense (CPU cycles, CPU frequency, compute power), but changing one input at a time does not always move the predicted energy steadily in the expected direction. This must be fixed before unsupervised operation.

Dataset link: https://drive.google.com/drive/folders/1kQYSA1ZckSWrlfvyvgcXVGEuLLEpXbOO?usp=drive_link

### 2. Downlink-feasibility and adaptive-compression predictor

For each ground-station pass, predicts whether the full payload can be sent and, if not, how much to compress it.

- **Data:** 50,000 simulated passes covering geometry, path loss, rain attenuation (ITU-R P.618), antenna mispointing, SNR fluctuation and payload size, plus statistics from SNR, range and elevation time series.
- **Files:** `aggregated_passes.csv`, `timeseries_passes_profiles.csv`, `timeseries_passes_meta.csv`, `ts_features.csv` -> `aggregated_passes_enriched.csv`
- **Model:** XGBoost with two outputs: `can_send_all` (R2 0.97) and `recommended_compression_ratio` (R2 0.98).
- **Compression pipeline:** if the payload does not fit, a compressor is picked by data type: JPEG for images (.jpg, .png, .tif), LZ4 for telemetry (.csv, .txt), Zstandard for science/binary data (.bin, .dat).
- **Scripts:** `train_XGBoost.py` (trains the model), `send_with_compressing.py` (send/compress decision and compression at transmission time).

### 3. Disaster triage (DVM)

Screens images onboard and sends disasters first.

- **Data:** real Sentinel-2 (ESA Copernicus) patches, 6 bands (RGB, NIR, 2 SWIR). Classes: Normal, Fire, Flood, Alarm (anomaly). Stratified, scene-grouped split to avoid spatial leakage. Test set: 98 patches (32 wildfire, 22 flood, 21 alarm, 23 normal).
- **Pipeline:** Step 0 cloud screener (CPU, <1 ms; cloudy images are kept as 2 KB thumbnails, never deleted) -> Step 1 6-band MobileNetV3-Large (usability, disaster class, uncertainty as normalised Shannon entropy) -> Step 2 dispatcher (store if battery <30%; transmit immediately if a disaster is detected over a ground station).
- **Outputs:** usability, disaster class, uncertainty.

| Approach | Bands | Loss / alert rule | Disaster recall | Wildfire recall | Latency |
|----------|-------|-------------------|----------------:|----------------:|--------:|
| Custom small CNN (scratch) | 4 | Cross-entropy, 50% cutoff | 57.4% | 68.2% | 12.4 ms |
| MobileNetV3 pretrained | 4 | Weighted CE, 50% cutoff | 78.2% | 81.0% | 6.54 ms |
| **MobileNetV3 pretrained (chosen)** | 6 | Focal Loss (gamma 2.0), tuned threshold tau* = 0.10 | 100.0% | 100.0% | 6.45 ms |

- Model size 12.05 MB (FP32, 3,158,149 parameters), exported as TorchScript; 7/7 safety invariants passed.
- Energy: 32 mJ inference vs 1,887 mJ downlink, i.e. 98.3% saved each time a useless image is filtered onboard.
- **Known limitation:** the 100% recall is for the "any disaster" alert. On the 98-patch test set the 6-band model predicted no patch as Normal, precision (76.5%) equals the disaster share of the test set (75/98), and only 4 of 22 flood patches received the exact Flood label. At tau* = 0.10 it behaves close to "flag everything". A larger, less disaster-heavy test set and per-class metrics are needed.

### 4. Radio-link prediction

Estimates the probability that a file is fully delivered before the pass ends, so the engine can attempt or defer.

- **Data:** simulated X-band dataset, 100,000 transmission attempts over 35,073 passes, 59 model inputs, ITU-R-style link budget and DVB-S2-like MODCOD table.
- **Column groups:** pass geometry, radio hardware, environment (weather, rain, cloud, humidity), link budget (path loss, attenuations, SNR/SINR), mission data and protocol (data class, file size, packet size, retransmissions, MODCOD), window prediction (capacity, time and capacity ratios), and outcome labels (`delivery_success`, `delivery_failure_reason`, `success_probability_simulated`, ...).
- **Models compared:** Logistic Regression, Random Forest, Gradient Boosting, HistGradientBoosting (chosen).
- **Results:** ROC-AUC 0.98, F1 0.93, ECE 0.002 (well calibrated).


## Key references

- Rigo et al., 2021; Slongo et al., 2018; Seman et al., 2023: energy-aware CubeSat scheduling
- Ramezani et al., 2023: safe hierarchical reinforcement learning for CubeSat scheduling
- Sabri et al., 2021 (DVFS); Arnold et al., 2012 (FPGA energy budgets): hardware power ranges
- ITU-R P.618: rain attenuation / link budget; DVB-S2-like MODCOD table
- Chen and Guestrin, 2016: XGBoost
- Sentinel-2, ESA Copernicus: imagery for disaster triage
- NASA/JPL Dynamic Targeting (CogniSAT-6) and SYNOPSIS; ESA Phi-sat-1 and Phi-sat-2: existing systems studied
