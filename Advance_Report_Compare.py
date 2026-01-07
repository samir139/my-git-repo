import os
import sys
import re
import pandas as pd
from tabulate import tabulate
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from configparser import ConfigParser


class CompareCycleReport:
    COLS = ['Scenarios', 'Passed', 'Failed', 'Skipped']

    def __init__(self, folder_path, prefix):
        self.folder_path = folder_path
        self.prefix = prefix

    # ---------- FILE OPERATIONS ----------
    def list_all_files(self):
        try:
            return os.listdir(self.folder_path)
        except FileNotFoundError:
            print(f"Folder not found: {self.folder_path}")
            return []

    def list_matching_csv_files(self):
        return [
            f for f in self.list_all_files()
            if f.startswith(self.prefix) and f.lower().endswith(".csv")
        ]

    def list_sorted_csv_files(self, descending=False):
        extractor = lambda f: int(re.search(r"_(\d+)\.csv$", f).group(1)) if re.search(r"_(\d+)\.csv$", f) else -1
        return sorted(self.list_matching_csv_files(), key=extractor, reverse=descending)

    def list_top_n_csv_files(self, n=2):
        return self.list_sorted_csv_files(descending=True)[:n]

    def extract_number_from_filename(self, filename):
        match = re.search(r"_(\d+)\.csv$", filename)
        return match.group(1) if match else "Unknown"

    # ---------- COMPARISON ----------
    # ---------- COMPARISON ----------
    def compare_csv_side_by_side(self, files):
        if len(files) < 2:
            print("Need at least 2 files to compare.")
            return ""

        file_old = os.path.join(self.folder_path, files[1])
        file_new = os.path.join(self.folder_path, files[0])

        num_old = self.extract_number_from_filename(files[1])
        num_new = self.extract_number_from_filename(files[0])

        merged = pd.merge(pd.read_csv(file_old), pd.read_csv(file_new),
                          on='Stages', how='outer',
                          suffixes=(f'_{num_old}', f'_{num_new}'))

        # Correctly reorder: 'Stages' once, then base columns
        reordered = ['Stages']
        for base in self.COLS:
            col_old, col_new = f"{base}_{num_old}", f"{base}_{num_new}"
            if col_old in merged.columns and col_new in merged.columns:
                reordered.extend([col_old, col_new])

        merged = merged[[c for c in reordered if c in merged.columns]].copy()

        print(f"\nComparison Cycle Report ({num_old} | {num_new})")
        print(tabulate(merged.values.tolist(), headers=merged.columns.tolist(), tablefmt="grid"))

        return merged.to_html(index=False, escape=False, border=1)

    # ---------- HIGHLIGHT DIFFERENCES ----------
    def highlight_difference(self, files):
        if len(files) < 2:
            print("Need at least 2 files to compare.")
            return ""

        file_new = os.path.join(self.folder_path, files[0])
        file_old = os.path.join(self.folder_path, files[1])

        num_new = self.extract_number_from_filename(files[0])
        num_old = self.extract_number_from_filename(files[1])

        merged = pd.merge(pd.read_csv(file_old), pd.read_csv(file_new),
                          on='Stages', how='outer',
                          suffixes=(f'_{num_old}', f'_{num_new}'))

        # Convert numeric columns
        for base in self.COLS:
            for col in [f"{base}_{num_old}", f"{base}_{num_new}"]:
                if col in merged.columns:
                    merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0).astype(int)

        # Highlight differences
        for base in self.COLS:
            col_old, col_new = f"{base}_{num_old}", f"{base}_{num_new}"
            if col_old in merged.columns and col_new in merged.columns:
                diff = merged[col_new] - merged[col_old]
                merged[col_old] = merged[col_old].astype(str)
                merged[col_new] = merged[col_new].astype(str)
                for i in merged.index:
                    if base == "Failed" and diff[i] > 0:
                        merged.at[
                            i, col_new] = f'<span style="color:red;font-weight:bold">{merged.at[i, col_new]} (+{diff[i]})</span>'
                    elif base != "Failed" and diff[i] < 0:
                        merged.at[
                            i, col_new] = f'<span style="color:red;font-weight:bold">{merged.at[i, col_new]} ({diff[i]})</span>'

        # Rename columns
        rename_map = {}
        for base in self.COLS:
            col_old, col_new = f"{base}_{num_old}", f"{base}_{num_new}"
            if col_old in merged.columns and col_new in merged.columns:
                rename_map[col_old] = f"{base} (Build_{num_old})"
                rename_map[col_new] = f"{base} (Build_{num_new})"

        merged.rename(columns=rename_map, inplace=True)

        # Reorder: 'Stages' first, then all renamed old/new columns
        reordered = ['Stages'] + list(rename_map.values())
        merged = merged[[c for c in reordered if c in merged.columns]].copy()

        return merged.to_html(index=False, escape=False, border=1)

    # ---------- EMAIL ----------
    def send_comparision_report(self, html_table, files):
        parser = ConfigParser()
        parser.read(os.path.join(os.path.dirname(__file__), './../configurations/emailnotification.ini'))

        smtp_server = parser['Email_Alert']['smtpServer']
        sender = parser['Email_Alert']['Sender']
        recipients = ["marimuthu.mannathan@bny.com","samir.ransingh@bny.com"]

        new_num, old_num = self.extract_number_from_filename(files[0]), self.extract_number_from_filename(files[1])
        base_title = "_".join(os.path.splitext(files[0])[0].split("_")[:-1])
        subject = f"Consolidated Comparison Report - {base_title}"

        msg = MIMEMultipart("alternative")
        msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(recipients), subject

        html_content = f"""
                        <html>
                        <head>
                        <style>
                            table {{
                                border-collapse: collapse;
                                width: 100%;
                                font-family: Arial, sans-serif;
                                font-size: 13px;
                            }}
                            th, td {{
                                border: 1px solid #ccc;
                                padding: 6px 10px;
                                text-align: left;
                                vertical-align: top;
                            }}
                            th {{
                                background-color: #f2f2f2;
                                color: #333;
                            }}
                            tr:nth-child(even) {{ background-color: #fafafa; }}
                            a {{ color: #155885; text-decoration: none; }}
                            a:hover {{ text-decoration: underline; }}
                        </style>
                        </head>
                        <body>
                            <p>Hello Team,</p>
                            <h3 style="color:#155885;">Here is the comparision report between latest API cycle {new_num} and last API cycle {old_num}. </h3>
                            {html_table}
                            <div class="footer" style="margin-top:20px; padding:0;">
                                <p style="margin:0;">Thanks,</p>
                                <p style="margin:0; font-size:14px; font-weight:bold;">Regression Engineering Team</p>
                                <p style="margin:0; font-size:12px; color:#333;">da_regoperations@bny.com</p>
                                <p style="margin:0; font-size:10px; color:#333;">bny.com</p>
                            </div>
                          </body>
                        </html>
                        """

        msg.attach(MIMEText(html_content, "html"))
        with smtplib.SMTP(smtp_server) as server:
            server.starttls()
            server.sendmail(sender, recipients, msg.as_string())
        print("Email sent successfully!")


# ---------- MAIN ----------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py <prefix> [compare_with_baseline]")
        sys.exit(1)

    prefix = sys.argv[1]
    compare_with_baseline = sys.argv[2].upper() if len(sys.argv) > 2 else "N"

    # Path setup
    artifacts_path = "Artifacts"
    baseline_path = os.path.join("Artifacts", "Baseline", prefix)

    tool = CompareCycleReport(artifacts_path, prefix)

    # Check CSV in Artifacts
    latest_files = tool.list_top_n_csv_files(1)
    if not latest_files:
        print(f"No CSV files found in Artifacts with prefix '{prefix}'.")
        sys.exit(1)

    latest_file = latest_files[0]
    print(f"Latest artifact found: {latest_file}")

    if compare_with_baseline == "Y":
        print("Comparing Artifact with Baseline...")

        if not os.path.exists(baseline_path):
            print(f"Baseline folder does not exist: {baseline_path}")
            sys.exit(1)

        baseline_files = [f for f in os.listdir(baseline_path) if f.lower().endswith(".csv")]
        if not baseline_files:
            print(f"No CSV files found inside baseline folder: {baseline_path}")
            sys.exit(1)

        baseline_file = baseline_files[0]
        print(f"Baseline file found: {baseline_file}")

        files = [latest_file, baseline_file]

    else:
        print("Comparing latest two Artifact results...")
        latest_two_files = tool.list_top_n_csv_files(2)

        if len(latest_two_files) < 2:
            print("Not enough CSV files in Artifacts to perform comparison. Need at least 2.")
            sys.exit(1)

        files = latest_two_files
        print(f"Comparing {files[0]} and {files[1]}")

    # Run report
    html_table = tool.compare_csv_side_by_side(files)
    if not html_table:
        print("Comparison could not be generated due to missing or invalid data.")
        sys.exit(1)

    highlighted = tool.highlight_difference(files)
    tool.send_comparision_report(highlighted, files)
