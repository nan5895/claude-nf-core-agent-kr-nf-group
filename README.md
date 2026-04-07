# 🧬 nf-core plugin for Claude Code

![nf-core](https://img.shields.io/badge/nf--core-pipelines-23aa62.svg)

![Claude Code](https://img.shields.io/badge/Claude_Code-Agent-D97757.svg)

**"Automate complex bioinformatics pipelines with a single natural language command."**

This project introduces a custom slash command (`/nf`) to Anthropic's [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code), transforming it into a fully-fledged bioinformatics AI assistant. With a single prompt, the agent handles everything from environment validation and data acquisition to dynamic samplesheet generation, parameter tuning, and real-time error recovery for any [nf-core](https://nf-co.re/) pipeline (e.g., RNA-seq, WGS, Protein Structure Prediction).

## ✨ Key Features

1. 🗣️ **Natural Language Pipeline Recommendation:** Just describe your goal (e.g., "I want to find cancer mutations in my patient data"). The agent queries the live nf-core registry and recommends the most suitable pipeline (like `nf-core/sarek`).
2. 🛡️ **Pre-flight Environment Validation:** Before executing anything, it checks your Docker daemon, Nextflow version, Java runtime, and disk space to prevent runtime crashes.
3. 📥 **Public Data (GEO/SRA) Auto-Acquisition:** Provide a public accession ID (like `GSE110004`), and the agent will automatically download the required raw data.
4. 🧠 **Universal Dynamic Samplesheet Engine:** The agent fetches the specific `schema_input.json` for your chosen pipeline on the fly. Whether it requires FASTQ files with strandedness (genomics) or FASTA files (proteomics), it interactively builds a flawless `samplesheet.csv`.
5. 💡 **Domain Expert Guide:** During parameter setup, the agent scrapes and presents crucial biological tips and resource warnings specific to that pipeline (e.g., "Warning: AlphaFold DB requires 2.5TB of storage. Consider using ColabFold mode instead.").
6. 🚑 **Real-time Monitoring & Auto-Recovery:** If the pipeline fails (e.g., an OOM Exit 137 error), the agent steps in, explains the failure in plain English, and suggests a `resume` command with optimized parameters (like `-max_memory`).

## 🚀 Installation

You can install and inject the agent into any working directory with a single command via NPM.

### Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code) installed and authenticated
- Nextflow (v23.04+)
- Docker or Singularity
- Node.js & NPM (for installation)
- Python 3.8+

### Quick Setup

Run the following command in the directory where your data lives (or where you want to run your analysis):

```bash
npx claude-nf-core-agent-kr-nf-group
```

or 

```bash
git clone https://github.com/nan5895/claude-nf-core-agent-kr-nf-group.git
```

*This command will automatically create the `.claude/commands/nf.md` instruction file and copy the necessary backend Python scripts into your current directory without overwriting your existing data.*

## 🎯 Usage

Once installed, launch Claude Code in that directory:

Bash

`claude`

Then, use the `/nf` command followed by your natural language request.

**Example 1: Local RNA-seq Analysis**

Bash

`> /nf I want to analyze the gene expression levels of the raw data in my ./patients_fastq folder`

**Example 2: Public Data Re-analysis (WGS)**

Bash

`> /nf Download GSE110004 and find somatic variants`

**Example 3: Protein Structure Prediction**

Bash

`> /nf Predict the 3D structures for the protein sequences in my ./fasta_files directory`

## 📂 Architecture

When installed, the package provisions the following structure:

- `.claude/commands/nf.md`: The master AI prompt defining the agent's workflow and behavior.
- `scripts/`: Executable automation scripts driven by the agent.
    - `search_pipeline.py`: Live nf-core API querying.
    - `check_environment.py`: System readiness validation.
    - `generate_samplesheet.py`: The dynamic, universal schema parser and generator.
    - `run_nextflow.py`: Interactive parameter tuning and deployment.
    - `monitor_nextflow.py`: Real-time log tailing and error diagnostics.


## ⚠️ Disclaimer & Liability Warning

Please read carefully before using this tool:

1. **AI Hallucinations & Verification:** This agent relies on Anthropic's Claude. While highly capable, AI models can occasionally hallucinate incorrect parameters, inappropriate pipelines, or misinterpret data structures. **You MUST always review and verify the generated `nextflow run` command and `samplesheet.csv` before giving the final execution approval.**
2. **Research Use Only (RUO):** This tool is intended strictly for educational and research purposes. It is **NOT** validated for clinical diagnostics, patient treatment decisions, or medical use.
3. **Infrastructure Costs & Resource Management:** Bioinformatics pipelines can consume massive amounts of CPU, memory, and storage. The creators of this plugin are not responsible for any unexpected cloud billing (AWS/GCP/Azure) or local system crashes (e.g., OOM) resulting from the agent's execution.
4. **Provided "AS-IS":** This software is provided "as is", without warranty of any kind. The maintainers assume no liability for data loss, environment corruption, or incorrect scientific results. You are solely responsible for your own data and infrastructure.
