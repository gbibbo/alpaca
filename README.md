# Qwen VAD LoRA

Robust voice activity detection with audio-language models under short, noisy, reverberant, and spectrally filtered audio.

This repository documents an applied audio ML experiment on binary Voice Activity Detection (VAD). It compares a frozen large audio-language model against a smaller model adapted with LoRA, then evaluates both under controlled acoustic degradations.

**Main result:** Qwen2-Audio-7B adapted with LoRA and OPRO-Template reached **93.3% balanced accuracy** on **21,340 degraded test clips**, outperforming a frozen Qwen3-Omni-30B baseline at **91.1% balanced accuracy**.

---

## Robust VAD under degraded audio

VAD is often reliable on clean, well-segmented audio. The harder case is deciding whether speech is present when the available acoustic evidence is short, noisy, reverberant, or spectrally distorted.

This experiment evaluates that case across four degradation axes:

| Axis | Conditions |
|---|---|
| Segment duration | 20, 40, 60, 80, 100, 200, 500, 1000 ms |
| Additive noise | -10, -5, 0, +5, +10, +20 dB |
| Reverberation | RT60 = 0.0, 0.3, 1.0, 2.5 s |
| Spectral filtering | none, bandpass, lowpass, highpass |

The final test bank contains **21,340 clips** across speech and non-speech classes.

---

## Experiment design

The repository implements a comparison matrix for robust binary VAD using audio-language models.

| Component | Description |
|---|---|
| Degradation bank | Controlled evaluation across duration, SNR, reverberation, and filtering |
| Model comparison | Qwen2-Audio-7B base, Qwen2-Audio-7B + LoRA, Qwen3-Omni-30B frozen |
| Prompt comparison | Hand prompt, OPRO-LLM, OPRO-Template |
| Adaptation | LoRA fine-tuning for Qwen2-Audio-7B |
| Evaluation | Balanced accuracy, speech recall, non-speech recall, per-condition breakdowns |
| Reporting | JSON metrics, CSV predictions, LaTeX tables, audit reports, and figures |

```text
Speech and non-speech audio
        |
        v
Controlled degradation bank
        |
        v
Model configuration
  |- Qwen2-Audio-7B base
  |- Qwen2-Audio-7B + LoRA
  `- Qwen3-Omni-30B frozen
        |
        v
Prompt strategy
  |- Hand prompt
  |- OPRO-LLM
  `- OPRO-Template
        |
        v
Evaluation and audit artifacts
  |- metrics.json
  |- predictions.csv
  |- statistical tests
  |- LaTeX tables
  `- figures
```

---

## Results

### Headline comparison

| System | Balanced accuracy | Speech recall | Non-speech recall |
|---|---:|---:|---:|
| Qwen2-Audio-7B + OPRO-LLM | 82.6% | 74.7% | 90.6% |
| **Qwen2-Audio-7B + LoRA + OPRO-Template** | **93.3%** | **92.8%** | **93.8%** |
| Qwen3-Omni-30B frozen + hand prompt | 91.1% | 87.4% | 94.7% |
| Silero VAD | 88.9% | 78.8% | 99.1% |

The strongest result is not the largest model. The adapted 7B model gives the best balanced result, while the specialist Silero baseline remains highly conservative: very strong non-speech recall, weaker speech recall.

Evidence files:

```text
audits/round2/b2_normalization/02_base_opro_llm/metrics.json
audits/round2/b2_normalization/06_lora_opro_template/metrics.json
audits/round2/b2_normalization/07_qwen3_baseline/metrics.json
audits/round2/B6_silero_results.md
results/CONSOLIDATED_MATRIX_RESULTS.md
```

### Full matrix summary

| Model | Configuration | BA_clip [95% CI] | Recall_SPEECH | Recall_NONSPEECH |
|---|---|---:|---:|---:|
| Qwen2-Audio-7B | Base + Hand | 0.640 [0.626, 0.654] | 0.321 | 0.959 |
| Qwen2-Audio-7B | Base + OPRO-LLM | 0.826 [0.814, 0.838] | 0.747 | 0.906 |
| Qwen2-Audio-7B | LoRA + Hand | 0.864 [0.852, 0.875] | 0.824 | 0.903 |
| Qwen2-Audio-7B | **LoRA + OPRO-Template** | **0.933 [0.925, 0.940]** | **0.928** | **0.938** |
| Qwen3-Omni-30B | Frozen + Hand | 0.911 [0.904, 0.918] | 0.874 | 0.947 |
| Qwen3-Omni-30B | Frozen + OPRO-LLM | 0.914 [0.906, 0.921] | 0.892 | 0.935 |

The 7B LoRA + OPRO-Template system surpasses the frozen 30B baseline by 2.2 percentage points in balanced accuracy. The difference is statistically significant under McNemar testing in the audit results.

---

## Robustness profiles

### Duration

<img src="figures/Fig_Duration.png" alt="Balanced accuracy across segment duration" width="720">

LoRA + OPRO-Template reaches **DT90 = 96 ms**, meaning it reaches 90% balanced accuracy with approximately 100 ms of audio. The unoptimized baseline does not reach 90% balanced accuracy at any tested duration.

| Configuration | DT90 (ms) | SNR75 (dB) |
|---|---:|---:|
| Baseline | >1000 | >+20 |
| Base + OPRO | >1000 | <-10 |
| LoRA + Hand | 329 [87, 1000] | <-10 |
| **LoRA + OPRO** | **96 [88, 133]** | **<-10** |
| Qwen3-Omni frozen | 175 [146, 222] | <-10 |

### Noise

<img src="figures/Fig_SNR.png" alt="Balanced accuracy across SNR levels" width="720">

LoRA adaptation makes the model much more stable under additive noise. Prompt optimization improves the base model, but does not reproduce the same robustness profile.

### Reverberation

The original reverberation figure is included as a PDF in the repository:

```text
figures/Fig_Reverb.pdf
```

Adapted models maintain stable performance across reverberation conditions from RT60 = 0.0 s to RT60 = 2.5 s. The baseline is more sensitive to reverberation.

### Sensitivity and specificity

<img src="figures/Fig_Tradeoff.png" alt="Speech recall versus non-speech recall trade-off" width="620">

The systems occupy different operating regimes:

| Regime | Behavior |
|---|---|
| Qwen2-Audio base | Conservative, biased toward non-speech under uncertainty |
| Base + OPRO | Recovers speech sensitivity, with more false alarms |
| LoRA + OPRO-Template | Best balance between speech and non-speech recall |
| Silero VAD | Very high non-speech recall, weaker speech recall |

### Per-axis balanced accuracy

| Configuration | Duration | SNR | Reverb | Filter |
|---|---:|---:|---:|---:|
| Base + Hand | 65.9 | 62.8 | 64.0 | 62.1 |
| Base + OPRO-LLM | 82.5 | 86.0 | 81.8 | 78.7 |
| **LoRA + OPRO-Template** | **87.4** | **97.4** | **96.1** | **96.2** |
| Qwen3-Omni + Hand | 79.8 | 98.5 | 97.0 | 96.7 |

---

## Prompt optimization

Prompt optimization is evaluated as an experimental factor, not used as a cosmetic rewrite of the instruction.

| Search component | Count |
|---|---:|
| Total prompt evaluations | 435 |
| Unique prompts | 71 |
| OPRO-LLM evaluations | 75 |
| OPRO-Template evaluations | 360 |

Evidence file:

```text
audits/round1/B8_opro_prompt_analysis.md
```

Multi-seed OPRO-Template results:

| Model | Seeds | Mean BA | Std | Range |
|---|---:|---:|---:|---:|
| Base + OPRO-Template | 5 | 72.34% | 6.14 pp | 61.36 to 75.08% |
| **LoRA + OPRO-Template** | 5 | **91.80%** | **2.44 pp** | 87.66 to 93.29% |
| Qwen3 + OPRO-Template | 5 | 87.86% | 1.13 pp | 86.34 to 89.54% |

Evidence file:

```text
audits/round3/B1_multiseed_opro.md
```

The prompt search results show an interaction between adaptation and prompt style. Frozen models benefit more from natural-language OPRO prompts. The LoRA-adapted model performs best with structured templates.

---

## Failure analysis

The repository includes class-level analysis for ESC-50 non-speech categories. The hardest cases are mostly human or animal vocalizations, which are plausible VAD confounders.

| Category | Group | Mean accuracy across configs | LoRA + OPRO-Template accuracy |
|---|---|---:|---:|
| laughing | Human vocalizations | 43.9% | 31.8% |
| coughing | Human vocalizations | 56.4% | 56.6% |
| crying_baby | Human vocalizations | 60.4% | 77.3% |

Evidence file:

```text
audits/round1/B7_esc50_accuracy_report.md
```

This analysis is included because aggregate VAD accuracy is not enough for deployment. A useful system also needs to expose the sounds that produce false alarms or missed speech.

---

## Implementation

| Area | Tools |
|---|---|
| Models | Qwen2-Audio-7B, Qwen3-Omni-30B, Silero VAD |
| Training and adaptation | PyTorch, LoRA, PEFT, 4-bit quantization |
| Model ecosystem | Hugging Face Transformers, bitsandbytes |
| Audio processing | 16 kHz mono audio, short-window evaluation, degradation banks |
| Evaluation | Balanced accuracy, recall, bootstrap confidence intervals, McNemar tests |
| Experiment management | Python scripts, Slurm job support, JSON and CSV artifacts |
| Reporting | Matplotlib figures, LaTeX tables, markdown audit reports |

Hardware used during the experiment:

| Model | GPU | Quantization | VRAM |
|---|---|---|---:|
| Qwen2-Audio-7B base and LoRA | NVIDIA RTX 3090 or A100 | 4-bit NF4 | about 24 GB |
| Qwen3-Omni-30B frozen | NVIDIA A100 | fp16 | about 80 GB |

The full Qwen2-Audio matrix, including LoRA training, OPRO, and six-cell evaluation, took about 13.4 hours on a single A100-SXM4-80GB in the original run.

---

## Repository structure

```text
.
├── README.md
├── config.yaml
├── main.tex
├── figures/
│   ├── Fig_Duration.png
│   ├── Fig_SNR.png
│   ├── Fig_Tradeoff.png
│   ├── Fig_Reverb.pdf
│   └── esc50_heatmap.pdf
├── scripts/
│   ├── run_matrix.py
│   ├── finetune.py
│   ├── eval.py
│   ├── eval_silero.py
│   ├── opro_llm.py
│   ├── opro_template.py
│   ├── stats.py
│   ├── make_tables.py
│   └── plot_final_figures.py
├── results/
│   ├── CONSOLIDATED_MATRIX_RESULTS.json
│   └── CONSOLIDATED_MATRIX_RESULTS.md
├── tables/
│   ├── Tab_R02_OverallPerformance.tex
│   ├── Tab_R04_dimension_means.tex
│   ├── Tab_R05_ErrorCounts.tex
│   └── tab_primary_comparisons.tex
├── audits/
│   └── paper_audit_20260213.md
└── slurm/
    └── stats_rerun.job
```

---

## Inspect the results

Clone the repository:

```bash
git clone <repo-url>
cd qwen-vad-lora
```

Read the consolidated result report:

```bash
cat results/CONSOLIDATED_MATRIX_RESULTS.md
```

Recompute the headline comparison from JSON metrics:

```bash
python - <<'PY'
import json

systems = {
    "Base + OPRO-LLM": "audits/round2/b2_normalization/02_base_opro_llm/metrics.json",
    "LoRA + OPRO-Template": "audits/round2/b2_normalization/06_lora_opro_template/metrics.json",
    "Qwen3 + Hand": "audits/round2/b2_normalization/07_qwen3_baseline/metrics.json",
}

print(f"{'system':<24} {'BA':>8} {'speech':>8} {'nonspeech':>10} {'n':>8}")
for name, path in systems.items():
    with open(path) as f:
        m = json.load(f)
    print(
        f"{name:<24} "
        f"{100*m['ba_clip']:>7.1f}% "
        f"{100*m['speech_acc']:>7.1f}% "
        f"{100*m['nonspeech_acc']:>9.1f}% "
        f"{m['n_samples']:>8}"
    )
PY
```

Expected output:

```text
system                         BA   speech  nonspeech        n
Base + OPRO-LLM             82.6%    74.7%      90.6%    21340
LoRA + OPRO-Template        93.3%    92.8%      93.8%    21340
Qwen3 + Hand                91.1%    87.4%      94.7%    21340
```

Generate analysis artifacts:

```bash
python scripts/analyze_multiseed_opro.py
python scripts/analyze_normalization_levels.py
python scripts/analyze_silero.py
python scripts/plot_final_figures.py
python scripts/make_tables.py
```

Check the experiment orchestrator:

```bash
python scripts/run_matrix.py --dry_run
```

Some scripts expect the original data and model-cache layout used during the experiment.

---

## Reproducing the experiment

### Data

The data directory should point to the preprocessed audio data from the earlier pipeline used for the experiment.

Expected contents include:

```text
data/processed/experimental_variants/
data/processed/variants_validated_1000/
```

The original run used speech and non-speech audio resampled to 16 kHz mono, with train, development, and test manifests. Raw datasets and trained model checkpoints are not bundled in this repository.

### Environment

For Qwen2-Audio base and LoRA runs:

```bash
pip install torch transformers peft bitsandbytes accelerate
pip install pandas numpy scipy tqdm scikit-learn soundfile librosa
pip install matplotlib seaborn
```

For Qwen3-Omni runs, the experiment used a development version of Transformers:

```bash
pip install git+https://github.com/huggingface/transformers.git
```

### Full matrix

```bash
python scripts/run_matrix.py --dry_run
python scripts/run_matrix.py --cells all
```

Specific cells can be selected when only part of the matrix is needed:

```bash
python scripts/run_matrix.py --cells 1A,2A,2B,2C
```

### Individual stages

LoRA fine-tuning:

```bash
python scripts/finetune.py \
    --train_csv data/processed/experimental_variants/train_metadata.csv \
    --val_csv data/processed/experimental_variants/dev_metadata.csv \
    --output_dir results/lora_training/checkpoints \
    --seed 42
```

OPRO-LLM optimization:

```bash
python scripts/opro_llm.py \
    --manifest data/processed/variants_validated_1000/dev_metadata.csv \
    --output_dir results/opro_llm_base/ \
    --model_type qwen2
```

OPRO-Template optimization:

```bash
python scripts/opro_template.py \
    --manifest data/processed/variants_validated_1000/dev_metadata.csv \
    --output_dir results/opro_template_base/ \
    --model_type qwen2
```

Evaluation:

```bash
python scripts/eval.py \
    --manifest data/processed/variants_validated_1000/test_metadata.csv \
    --prompt "Is this audio human speech? Answer: SPEECH or NON-SPEECH." \
    --output_dir results/eval_base_opro/ \
    --model_type qwen2
```

Evaluation with a LoRA checkpoint:

```bash
python scripts/eval.py \
    --manifest data/processed/variants_validated_1000/test_metadata.csv \
    --prompt "Detect human speech. Answer: SPEECH or NONSPEECH." \
    --output_dir results/eval_lora/ \
    --checkpoint results/lora_training/checkpoints/final \
    --model_type qwen2
```

Qwen3-Omni frozen evaluation:

```bash
python scripts/eval.py \
    --manifest data/processed/variants_validated_1000/test_metadata.csv \
    --prompt "What type of sound is this? Respond: SPEECH or NON-SPEECH." \
    --output_dir results/eval_qwen3/ \
    --model_type qwen3_omni
```

Statistical analysis and figure generation:

```bash
python scripts/stats.py --results_dir results/<TIMESTAMP>_COMPARATIVE_RUN/
python scripts/make_tables.py
python scripts/plot_final_figures.py
```

### Slurm

On the Surrey HPC cluster, Slurm commands are submitted through the provided wrapper:

```bash
./slurm/tools/on_submit.sh sbatch slurm/jobs/<job_file>.job
./slurm/tools/on_submit.sh squeue --me
./slurm/tools/on_submit.sh scancel <job_id>
```

---

## Current scope

This repository is strongest as an experiment, audit, and reporting package for robust VAD with audio-language models. It is not packaged as a one-command training library.

Current constraints:

- Raw audio datasets are not included.
- Trained checkpoints are not bundled.
- Some scripts depend on the original HPC data layout.
- Full reproduction requires access to the same data manifests and model-cache setup.

The included metrics, figures, tables, audit reports, and result files are intended to make the experiment inspectable even when the full training environment is not available.

---

## Author

Gabriel Bibbó  
Audio ML Research Engineer  
Sound event detection · Voice activity detection · Audio-language models

