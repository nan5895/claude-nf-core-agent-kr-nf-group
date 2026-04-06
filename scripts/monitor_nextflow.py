#!/usr/bin/env python3
"""
Nextflow pipeline monitor with real-time error detection and actionable advice.

Continuously tails a Nextflow log file, detects failures, classifies error types,
and provides concrete suggestions for recovery.

Usage:
    python monitor_nextflow.py nextflow_run.log
    python monitor_nextflow.py nextflow_run.log --json
    python monitor_nextflow.py nextflow_run.log --poll 5
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Error classification patterns ────────────────────────────────

ERROR_PATTERNS = [
    # Process-level failures
    {
        "pattern": re.compile(
            r"Error executing process\s+[>']?\s*'?([^'>\n]+)'?", re.IGNORECASE
        ),
        "type": "process_error",
        "extract": "process_name",
    },
    {
        "pattern": re.compile(r"Command error:", re.IGNORECASE),
        "type": "command_error",
    },
    {
        "pattern": re.compile(r"Process\s+`([^`]+)`\s+terminated"),
        "type": "process_terminated",
        "extract": "process_name",
    },
]

EXIT_STATUS_PATTERN = re.compile(r"exit\s*status\s*[:=]?\s*(\d+)", re.IGNORECASE)
COMMAND_EXIT_PATTERN = re.compile(
    r"Command\s+exit\s+status:\s*(\d+)", re.IGNORECASE
)

SUCCESS_PATTERNS = [
    re.compile(r"Pipeline completed successfully", re.IGNORECASE),
    re.compile(r"Succeeded\s*$", re.IGNORECASE),
]

WORK_DIR_PATTERN = re.compile(r"Work dir:\s+(\S+)")
RESULTS_DIR_PATTERN = re.compile(r"--outdir\s+(\S+)")

PROGRESS_PATTERN = re.compile(
    r"\[([0-9a-f]{2}/[0-9a-f]{6})\]\s+process\s+>\s+(\S+)"
)

# ── Exit code → diagnosis mapping ────────────────────────────────

EXIT_CODE_DB: Dict[int, Dict[str, str]] = {
    1: {
        "label": "General error",
        "explanation": "The process exited with a generic error. Check the command stderr for details.",
        "tip": "Review the .command.err file in the work directory for the exact error message.",
    },
    126: {
        "label": "Permission denied / not executable",
        "explanation": "The command or script could not be executed due to permissions.",
        "tip": "Check that container images are pulled correctly. Try: nextflow pull nf-core/<pipeline>",
    },
    127: {
        "label": "Command not found",
        "explanation": "A tool required by this process is not installed in the container.",
        "tip": "Ensure you're using the correct -profile (docker/singularity). The container may need updating.",
    },
    134: {
        "label": "SIGABRT — Abort signal",
        "explanation": "The process was aborted, often due to an assertion failure or corrupted data.",
        "tip": "Check input file integrity. For BAM files, try: samtools quickcheck <file>",
    },
    137: {
        "label": "SIGKILL — Out of Memory (OOM)",
        "explanation": "The process was killed by the OS because it exceeded the available memory.",
        "tip": "Increase memory allocation with --max_memory '64.GB' or add a custom config:\n"
               "         process { withName:'PROCESS_NAME' { memory = '64.GB' } }",
    },
    139: {
        "label": "SIGSEGV — Segmentation fault",
        "explanation": "The process crashed due to a memory access violation.",
        "tip": "This is usually a bug in the tool. Check if a newer container version is available, "
               "or try with fewer threads (--max_cpus 4).",
    },
    140: {
        "label": "SIGPIPE — Broken pipe",
        "explanation": "A downstream process closed its input before upstream finished writing.",
        "tip": "Often harmless. If persistent, check that input files are not truncated or corrupted.",
    },
    143: {
        "label": "SIGTERM — Terminated by scheduler",
        "explanation": "The job was killed by the cluster scheduler (SLURM/PBS) — likely a walltime or memory limit.",
        "tip": "Increase job limits: --max_time '72.h' or --max_memory '128.GB'. "
               "For SLURM: check sacct -j <jobid> for the actual limit hit.",
    },
}


@dataclass
class PipelineEvent:
    """A detected event during pipeline execution."""
    timestamp: str
    event_type: str          # process_error, command_error, oom, success, progress
    process_name: str = ""
    exit_code: Optional[int] = None
    diagnosis: str = ""
    tip: str = ""
    raw_lines: List[str] = field(default_factory=list)

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v}


class NextflowMonitor:
    """Monitors a Nextflow log file for errors and completion."""

    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self.events: List[PipelineEvent] = []
        self.work_dir: Optional[str] = None
        self.results_dir: Optional[str] = None
        self.completed = False
        self.failed = False
        self._line_buffer: List[str] = []
        self._process_count = 0

    def _classify_exit_code(self, code: int) -> Tuple[str, str]:
        """Look up exit code in the diagnosis database."""
        info = EXIT_CODE_DB.get(code, {})
        diagnosis = info.get("explanation", f"Process exited with code {code}.")
        tip = info.get("tip", "Check the .command.err and .command.log files in the work directory.")
        return diagnosis, tip

    def _extract_exit_code(self, lines: List[str]) -> Optional[int]:
        """Extract exit code from a block of log lines."""
        for line in lines:
            m = COMMAND_EXIT_PATTERN.search(line)
            if m:
                return int(m.group(1))
            m = EXIT_STATUS_PATTERN.search(line)
            if m:
                return int(m.group(1))
        return None

    def _detect_process_name(self, lines: List[str]) -> str:
        """Extract the failed process name from log lines."""
        for line in lines:
            for ep in ERROR_PATTERNS:
                m = ep["pattern"].search(line)
                if m and ep.get("extract") == "process_name":
                    return m.group(1).strip()
        return "unknown"

    def process_line(self, line: str):
        """Process a single log line."""
        stripped = line.strip()
        if not stripped:
            return

        # Track work directory
        m = WORK_DIR_PATTERN.search(stripped)
        if m:
            self.work_dir = m.group(1)

        # Track progress
        m = PROGRESS_PATTERN.search(stripped)
        if m:
            self._process_count += 1

        # Check for success
        for sp in SUCCESS_PATTERNS:
            if sp.search(stripped):
                self.completed = True
                event = PipelineEvent(
                    timestamp=time.strftime("%H:%M:%S"),
                    event_type="success",
                    diagnosis="Pipeline completed successfully.",
                )
                self.events.append(event)
                return

        # Check for errors
        is_error = False
        for ep in ERROR_PATTERNS:
            if ep["pattern"].search(stripped):
                is_error = True
                break

        if is_error:
            self._line_buffer.append(stripped)
            # Buffer a few more lines for context
            return

        # If we have a buffer and this line looks like continuation, keep buffering
        if self._line_buffer:
            if stripped.startswith(("Command", "Caused by", "  ", "exit", "Work dir")):
                self._line_buffer.append(stripped)
                if len(self._line_buffer) < 15:
                    return
            # Flush the buffer — we have enough context
            self._flush_error_buffer()

    def _flush_error_buffer(self):
        """Process accumulated error lines."""
        if not self._line_buffer:
            return

        lines = self._line_buffer
        self._line_buffer = []

        process_name = self._detect_process_name(lines)
        exit_code = self._extract_exit_code(lines)
        diagnosis, tip = "", ""

        if exit_code is not None:
            diagnosis, tip = self._classify_exit_code(exit_code)
            label = EXIT_CODE_DB.get(exit_code, {}).get("label", f"Exit {exit_code}")
            event_type = "oom" if exit_code == 137 else "process_error"
        else:
            event_type = "process_error"
            label = "Unknown error"
            diagnosis = "Process failed. Check the work directory logs for details."
            tip = "Review .command.err in the work directory."

        event = PipelineEvent(
            timestamp=time.strftime("%H:%M:%S"),
            event_type=event_type,
            process_name=process_name,
            exit_code=exit_code,
            diagnosis=f"[{label}] {diagnosis}",
            tip=tip,
            raw_lines=lines[:5],
        )
        self.events.append(event)
        self.failed = True

    def tail_file(self, poll_interval: float = 3.0, output_json: bool = False):
        """Tail the log file and monitor in real time."""
        # Wait for the file to appear
        wait_count = 0
        while not self.log_path.exists():
            if wait_count == 0:
                print(f"Waiting for log file: {self.log_path}")
            time.sleep(1)
            wait_count += 1
            if wait_count > 60:
                print(f"ERROR: Log file not created after 60 seconds: {self.log_path}")
                sys.exit(1)

        if not output_json:
            print(f"\n{'=' * 60}")
            print(f"  Monitoring: {self.log_path}")
            print(f"{'=' * 60}\n")

        with open(self.log_path, "r") as f:
            # Read existing content first
            for line in f:
                self.process_line(line)

            # Then tail for new content
            while not self.completed and not self.failed:
                line = f.readline()
                if line:
                    self.process_line(line)
                    # Flush buffer if we have one
                    if not line.strip() and self._line_buffer:
                        self._flush_error_buffer()

                    # Print events as they happen
                    while self.events:
                        event = self.events[-1]
                        if not output_json:
                            self._print_event(event)
                        if event.event_type in ("success", "oom", "process_error"):
                            break
                else:
                    # Flush any pending buffer on quiet periods
                    if self._line_buffer:
                        self._flush_error_buffer()
                    time.sleep(poll_interval)

        # Final flush
        if self._line_buffer:
            self._flush_error_buffer()

        # Output
        if output_json:
            result = {
                "status": "success" if self.completed else "failed",
                "events": [e.to_dict() for e in self.events],
                "work_dir": self.work_dir,
                "processes_run": self._process_count,
            }
            print(json.dumps(result, indent=2))
        else:
            self._print_summary()

    def _print_event(self, event: PipelineEvent):
        """Print a single event to the terminal."""
        if event.event_type == "success":
            print(f"\n{'=' * 60}")
            print(f"  [OK] Pipeline completed successfully!")
            print(f"{'=' * 60}")
            if self.work_dir:
                print(f"  Work dir: {self.work_dir}")
            print(f"  Check your results directory for output files.")
            print(f"  Look for the MultiQC report: results/multiqc/multiqc_report.html")

        elif event.event_type in ("process_error", "oom"):
            print(f"\n{'=' * 60}")
            print(f"  [FAIL] Error in process: {event.process_name}")
            print(f"{'=' * 60}")
            if event.exit_code is not None:
                print(f"  Exit code: {event.exit_code}")
            print(f"  {event.diagnosis}")
            if event.tip:
                tip_lines = event.tip.split("\n")
                print(f"\n  Tip: {tip_lines[0]}")
                for tl in tip_lines[1:]:
                    print(f"  {tl}")
            if self.work_dir:
                print(f"\n  Work dir: {self.work_dir}")
                print(f"  Debug:    cat {self.work_dir}/*/.command.err")
            print(f"{'=' * 60}")

    def _print_summary(self):
        """Print final summary."""
        print(f"\n--- Monitor Summary ---")
        print(f"  Status:    {'SUCCESS' if self.completed else 'FAILED'}")
        print(f"  Processes: {self._process_count} observed")
        print(f"  Events:    {len(self.events)}")

        if self.completed:
            print(f"\n  Next steps:")
            print(f"    1. Check MultiQC report: results/multiqc/multiqc_report.html")
            print(f"    2. Review output files in the results directory")

        elif self.failed:
            error_events = [e for e in self.events if e.event_type in ("process_error", "oom")]
            if error_events:
                last = error_events[-1]
                print(f"\n  Last error: {last.process_name} (exit {last.exit_code})")
                print(f"  {last.diagnosis}")
                print(f"\n  To resume after fixing:")
                print(f"    nextflow run <pipeline> -resume [adjusted params]")


# ── One-shot log analysis (non-tailing) ──────────────────────────

def analyze_log(log_path: str, output_json: bool = False) -> dict:
    """Parse a completed log file and return structured analysis."""
    monitor = NextflowMonitor(log_path)

    with open(log_path, "r") as f:
        for line in f:
            monitor.process_line(line)

    # Final flush
    if monitor._line_buffer:
        monitor._flush_error_buffer()

    result = {
        "status": "success" if monitor.completed else ("failed" if monitor.failed else "running"),
        "events": [e.to_dict() for e in monitor.events],
        "work_dir": monitor.work_dir,
        "processes_run": monitor._process_count,
    }

    if output_json:
        print(json.dumps(result, indent=2))

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Monitor a Nextflow pipeline log for errors and completion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s nextflow_run.log              # Real-time monitoring (tail mode)
    %(prog)s nextflow_run.log --no-tail    # One-shot analysis of completed log
    %(prog)s nextflow_run.log --json       # JSON output for programmatic use
    %(prog)s nextflow_run.log --poll 10    # Check every 10 seconds
        """,
    )

    parser.add_argument("log_file", help="Path to Nextflow log file")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--no-tail", action="store_true", help="One-shot analysis (don't tail)")
    parser.add_argument("--poll", type=float, default=3.0, help="Poll interval in seconds (default: 3)")

    args = parser.parse_args()

    if not os.path.exists(args.log_file) and args.no_tail:
        print(f"ERROR: Log file not found: {args.log_file}")
        sys.exit(1)

    if args.no_tail:
        result = analyze_log(args.log_file, output_json=args.json)
        status = result.get("status", "unknown")
        sys.exit(0 if status == "success" else 1)
    else:
        monitor = NextflowMonitor(args.log_file)
        monitor.tail_file(poll_interval=args.poll, output_json=args.json)
        sys.exit(0 if monitor.completed else 1)


if __name__ == "__main__":
    main()
