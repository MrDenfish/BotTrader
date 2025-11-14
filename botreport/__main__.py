# botreport/__main__.py
"""
Thin CLI wrapper around aws_daily_report.py so Docker/cron can keep calling:
  python -m botreport [flags]

All heavy lifting (DB queries, metrics, HTML, SES) happens in aws_daily_report.py.
"""

import os
import argparse
from datetime import datetime, timezone

# Import the real report entrypoints/utilities
from . import aws_daily_report as report


def _setenv_if(value: str | None, key: str):
    if value is not None:
        os.environ[key] = str(value)


def parse_args():
    p = argparse.ArgumentParser(
        description="Daily Trading Bot report runner (wrapper around aws_daily_report.py)"
    )
    p.add_argument("--hours", type=int, default=None,
                   help="Lookback window in hours (maps to REPORT_LOOKBACK_HOURS).")
    p.add_argument("--show-details", action="store_true",
                   help="Include detailed positions/trades tables (maps to REPORT_SHOW_DETAILS=1).")
    p.add_argument("--no-send", action="store_true",
                   help="Do not email; print/preview locally (forces IN_DOCKER=false).")
    p.add_argument("--send", action="store_true",
                   help="Force email send (sets IN_DOCKER=true).")
    p.add_argument("--score-path", type=str, default=None,
                   help="Override SCORE_JSONL_PATH (default /app/logs/score_log.jsonl).")
    p.add_argument("--email-from", type=str, default=None,
                   help="Override REPORT_SENDER.")
    p.add_argument("--email-to", type=str, default=None,
                   help="Override REPORT_RECIPIENTS (comma or RFC822 list).")
    p.add_argument("--aws-region", type=str, default=None,
                   help="Override AWS_REGION for SES/SSM.")
    p.add_argument("--subject", type=str, default=None,
                   help="(Optional) Subject override via REPORT_SUBJECT.")
    p.add_argument("--preview-html", type=str, default=None,
                   help="If set, also write the final HTML body to this path (in addition to normal behavior).")
    return p.parse_args()


def main():
    args = parse_args()

    # ----------------------------
    # Map CLI args to environment
    # ----------------------------
    _setenv_if(args.hours, "REPORT_LOOKBACK_HOURS")
    if args.show_details:
        os.environ["REPORT_SHOW_DETAILS"] = "1"

    # Allow score-log override
    _setenv_if(args.score_path, "SCORE_JSONL_PATH")
    # Email/region overrides
    _setenv_if(args.email_from, "REPORT_SENDER")
    _setenv_if(args.email_to, "REPORT_RECIPIENTS")
    _setenv_if(args.aws_region, "AWS_REGION")
    _setenv_if(args.subject, "REPORT_SUBJECT")

    # Decide whether to send email: aws_daily_report.main() checks IN_DOCKER
    # We control that explicitly so behavior is predictable from CLI.
    if args.send:
        os.environ["IN_DOCKER"] = "true"
    elif args.no_send:
        os.environ["IN_DOCKER"] = "false"
    # else: leave IN_DOCKER as-is (from env/Compose)

    # Load .envs the same way the module does
    report.load_report_dotenv()

    # Run the real report
    # aws_daily_report.main() will:
    #   - compute metrics
    #   - build HTML/CSV
    #   - if IN_DOCKER=true -> email via SES and save CSV copy
    #   - else               -> print console summary + save local CSV only
    # Returns HTML content if preview_html is requested
    html_content = None
    if args.preview_html:
        # Capture HTML output by modifying the report module temporarily
        os.environ["REPORT_CAPTURE_HTML"] = "true"

    try:
        html_content = report.main()
    except TypeError:
        # main() doesn't return HTML yet, need to modify it
        report.main()
        html_content = None

    # Optional: write a preview HTML copy if requested
    if args.preview_html and html_content:
        try:
            with open(args.preview_html, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f"\n[Saved HTML Preview] {args.preview_html}")
        except Exception as e:
            print(f"\n[Warning] Could not save HTML preview: {e}")


if __name__ == "__main__":
    main()


