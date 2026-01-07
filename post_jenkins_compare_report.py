import os
import sys
import re
import pandas as pd
from tabulate import tabulate
import jinja2
import smtplib
import socket
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from configparser import ConfigParser

class CompareCycleReport:
    def __init__(self, folder_path, prefix):
        self.folder_path = folder_path
        self.prefix = prefix

    def extract_build_number_from_filename(self):
        return os.path.splitext(os.path.basename(self.fname))[0].split("_")[-1]

    def list_all_files(self):
        try:
            return os.listdir(self.folder_path)
        except FileNotFoundError:
            print(f"Folder not found: {self.folder_path}")
            return []

    def list_matching_csv_files(self):
        all_files = self.list_all_files()
        return [
            f for f in all_files
            if f.startswith(self.prefix) and f.lower().endswith(".csv")
        ]

    def list_sorted_csv_files(self, descending=False):
        files = self.list_matching_csv_files()

        def extract_number(filename):
            match = re.search(r"_(\d+)\.csv$", filename)
            return int(match.group(1)) if match else -1

        return sorted(files, key=extract_number, reverse=descending)

    def list_top_n_csv_files(self, n=2):
        files = self.list_sorted_csv_files(descending=True)
        return files[:n]

    def extract_number_from_filename(self, filename):
        match = re.search(r"_(\d+)\.csv$", filename)
        return match.group(1) if match else "Unknown"

    def compare_csv_side_by_side(self, files):
        if len(files) < 2:
            print("Need at least 2 files to compare.")
            return

        file_old = os.path.join(self.folder_path, files[1])
        file_new = os.path.join(self.folder_path, files[0])

        num_old = self.extract_number_from_filename(files[1])
        num_new = self.extract_number_from_filename(files[0])

        df_old = pd.read_csv(file_old)
        df_new = pd.read_csv(file_new)

        merged = pd.merge(df_old, df_new, on='Stages', how='outer', suffixes=(f'_{num_old}', f'_{num_new}'))

        # Reorder columns to show new | old
        reordered_cols = ['Stages']
        for col_base in ['Scenarios', 'Passed', 'Failed', 'Skipped']:
            col_new = f"{col_base}_{num_new}"
            col_old = f"{col_base}_{num_old}"
            if col_new in merged.columns and col_old in merged.columns:
                reordered_cols.extend([col_new, col_old])
        merged = merged[reordered_cols]

        print(f"\nComparison Cycle Report ({num_old} | {num_new})")
        print(tabulate(merged.values.tolist(), headers=merged.columns.tolist(), tablefmt="grid"))

        html_table = merged.to_html(index=False, escape=False, border=1)
        return html_table

    def highlight_difference(self, files):
        if len(files) < 2:
            print("Need at least 2 files to compare.")
            return

        file_new = os.path.join(self.folder_path, files[0])
        file_old = os.path.join(self.folder_path, files[1])

        num_new = self.extract_number_from_filename(files[0])
        num_old = self.extract_number_from_filename(files[1])

        df_old = pd.read_csv(file_old)
        df_new = pd.read_csv(file_new)

        merged = pd.merge(df_old, df_new, on='Stages', how='outer', suffixes=(f'_{num_old}', f'_{num_new}'))

        # Format numbers for display
        def format_value(val):
            if pd.isna(val):
                return "0"
            if isinstance(val, float) and val.is_integer():
                return str(int(val))
            return str(val)

        for col in merged.columns:
            merged[col] = merged[col].apply(format_value)

        # Highlight only negative differences (regression)
        for col_base in ['Passed', 'Failed', 'Skipped', 'Scenarios']:
            col_old = f"{col_base}_{num_old}"
            col_new = f"{col_base}_{num_new}"

            if col_new in merged.columns and col_old in merged.columns:
                for i in merged.index:
                    val_new, val_old = merged.at[i, col_new], merged.at[i, col_old]
                    try:
                        n_new, n_old = float(val_new), float(val_old)
                        diff = n_new - n_old

                        if col_base == "Failed":
                            # failed increase → highlight red
                            if diff > 0:
                                merged.at[i, col_new] = (
                                    f'<span style="color:red; font-weight:bold">{val_new} (+{int(diff)})</span>'
                                )
                            # diff <= 0 → do nothing
                        else:
                            # normal logic for others (negative regression)
                            if diff < 0:
                                merged.at[i, col_new] = (
                                    f'<span style="color:red; font-weight:bold">{val_new} ({int(diff)})</span>'
                                )
                            # diff >= 0 → do nothing

                    except ValueError:
                        pass

        # Reorder columns to old | new
        reordered_cols = ['Stages']
        for col_base in ['Scenarios', 'Passed', 'Failed', 'Skipped']:
            col_old = f"{col_base}_{num_old}"
            col_new = f"{col_base}_{num_new}"
            if col_new in merged.columns and col_old in merged.columns:
                reordered_cols.extend([col_old, col_new])
        merged = merged[reordered_cols]

        # Rename headers to Base(Build_xxx)
        rename_map = {}
        for col in merged.columns:
            for base in ['Scenarios', 'Passed', 'Failed', 'Skipped']:
                if col.startswith(base + "_"):
                    cycle = col.split("_")[-1]
                    rename_map[col] = f"{base} (Build_{cycle})"
        merged.rename(columns=rename_map, inplace=True)

        html_table = merged.to_html(index=False, escape=False, border=1)
        return html_table

    def highlight_difference_1(self, files):
        if len(files) < 2:
            print("Need at least 2 files to compare.")
            return

        file1 = os.path.join(self.folder_path, files[0])
        file2 = os.path.join(self.folder_path, files[1])
        num1 = self.extract_number_from_filename(files[0])
        num2 = self.extract_number_from_filename(files[1])
        df1 = pd.read_csv(file1)
        df2 = pd.read_csv(file2)
        merged = pd.merge(df1, df2, on='Stages', how='outer', suffixes=(f'_{num1}', f'_{num2}'))

        # Highlight differences manually
        def format_value(val):
            if pd.isna(val):
                return "0"
            if isinstance(val, float) and val.is_integer():
                return str(int(val))  # drop .0
            return str(val)

        for col in merged.columns:
            merged[col] = merged[col].apply(format_value)

        for col in merged.columns:
            if col.endswith(num1):
                base = col.rsplit("_", 1)[0]
                other_col = f"{base}_{num2}"
                if other_col in merged.columns:
                    for i in merged.index:
                        val1, val2 = merged.at[i, col], merged.at[i, other_col]
                        if val1 != "nan" and val2 != "nan":
                            try:
                                n1, n2 = float(val1), float(val2)
                                if base.lower() == "failed":
                                    if n1 > n2:
                                        merged.at[i, col] = f'<span style="color:red; font-weight:bold">{val1}</span>'
                                        merged.at[i, other_col] = f'<span font-weight:bold">{val2}</span>'
                            except ValueError:
                                pass

        html_table = merged.to_html(index=False, escape=False, border=1)
        return html_table

    def send_comparision_report(self, html_table, files):
        parser = ConfigParser()
        parser.read(
            os.path.join(os.path.abspath(os.path.dirname(__file__)), './../configurations', 'emailnotification.ini'))
        smtp_server = parser['Email_Alert']['smtpServer']
        sender_email = parser['Email_Alert']['Sender']
        recipient_emails = ["marimuthu.mannathan@bny.com","samir.ransingh@bny.com"]

        new_num = self.extract_number_from_filename(files[0])
        old_num = self.extract_number_from_filename(files[1])
        base_name_parts = os.path.splitext(os.path.basename(files[0]))[0].split("_")[:-1]
        base_name = "_".join(base_name_parts)
        subject = f"Consolidated Comparison Report - {base_name}"

        msg = MIMEMultipart("alternative")
        msg["From"] = sender_email
        msg["To"] = ", ".join(recipient_emails)
        msg["Subject"] = subject

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
            server.sendmail(sender_email, recipient_emails, msg.as_string())
        print("Email sent successfully!")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py <prefix>")
        sys.exit(1)

    prefix = sys.argv[1]
    compare_report = CompareCycleReport("Artifacts", prefix)
    compare_report.list_all_files()
    compare_report.list_matching_csv_files()
    compare_report.list_sorted_csv_files(descending=True)
    compare_report.list_top_n_csv_files(2)
    files = compare_report.list_top_n_csv_files(2)
    html_table = compare_report.compare_csv_side_by_side(files)
    highlighted_html_table = compare_report.highlight_difference(files)
    compare_report.send_comparision_report(highlighted_html_table, files)