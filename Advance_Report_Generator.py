import os
import re
import sys
import json
import zipfile
import smtplib
import shutil
import requests
import socket
import pandas as pd
from lxml import html
from pathlib import Path
from datetime import datetime, timedelta
from tabulate import tabulate
from bs4 import BeautifulSoup
from collections import defaultdict, Counter
from configparser import ConfigParser
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()


class GetJenkinsReport:
    def __init__(self, job_name, jenkins_file="./../configurations/jenkins_params.json", destination_folder="./Artifacts", build_number=None):
        self.job_name = job_name
        self.jenkins_file = jenkins_file
        self.destination_folder = destination_folder
        self.jenkins_url, self.username, self.api_token = self.read_json(job_name)
        self.build_number = build_number
        self.artifact_table_data = None

    # ---------------- CONFIG ---------------- #
    def read_json(self, job_name):
        with open(self.jenkins_file, "r") as f:
            config = json.load(f)

        common = config.get("COMMON-INFO", {})
        username = common.get("username")
        api_token = common.get("api_token")
        job_config = config.get(job_name)
        if not job_config:
            print("No configuration found for job: ", job_name)
            sys.exit(1)

        jenkins_url = job_config.get("jenkins_url")
        return jenkins_url, username, api_token

    # ---------------- HELPERS ---------------- #
    def fetch_jenkins_params(self, resp):
        data = resp.json()
        for action in data.get("actions", []):
            if action.get("_class", "").endswith("ParametersAction"):
                params = action.get("parameters", [])
                return {p.get("name"): p.get("value") for p in params}
        return {}

    # ---------------- BUILD DETAILS ---------------- #
    def get_build_details(self):
        build_id_segment = f"{self.build_number}/api/json" if self.build_number else "lastBuild/api/json"
        built_url = f"{self.jenkins_url}/job/{self.job_name}/{build_id_segment}"
        #built_url = f"{self.jenkins_url}/job/{self.job_name}/lastBuild/api/json"
        print("Build URL: ", built_url)
        response = requests.get(built_url, auth=(self.username, self.api_token))
        response.raise_for_status()
        data = response.json()

        build_url = data.get("url")
        #self.build_number = data.get("number")
        self.build_number = data.get("number") if not self.build_number else self.build_number
        project = data.get("fullDisplayName", "")
        timestamp = data.get("timestamp")
        date_of_build = datetime.fromtimestamp(timestamp / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
        duration_ms = data.get("duration")
        duration = str(timedelta(seconds=duration_ms // 1000)) if duration_ms else "N/A"

        params = self.fetch_jenkins_params(response)
        branch = params.get("BRANCH")
        environment = params.get("ENV")
        e2e_opts = params.get("E2E_RUN_OPTIONS")
        git_repo_url = params.get("GIT_REPO_URL")
        concurrent_test = params.get("CONCURRENT_TESTS")
        sanity_check = params.get("SKIP_SANITY_CHECK_STEP")

        build_cause = "N/A"
        for action in data.get("actions", []):
            if "causes" in action:
                cause = action["causes"][0]
                build_cause = cause.get("userName", "N/A")
                break

        desc_html = data.get("description", "")
        build_description = BeautifulSoup(desc_html, "html.parser").get_text(strip=True) if desc_html else "N/A"

        table_data = [
            ["Build URL", build_url],
            ["Build Number", self.build_number],
            ["Project", project.split("»")[-1].split("#")[0].strip()],
            ["Build description", build_description],
            ["Branch", branch],
            ["Environment", environment],
            ["E2E Run Option", e2e_opts],
            ["GIT Repo URL", git_repo_url],
            ["Concurrent Test", concurrent_test],
            ["Skip Sanity Check", sanity_check],
            ["Date of build", date_of_build],
            ["Build duration", duration],
            ["Build Started by", build_cause],
            ["Built Status", data.get("result", "N/A")]
        ]

        print("\nBuild Description:")
        print(tabulate(table_data, headers=["Build", "Description"], tablefmt="grid"))
        return tabulate(table_data, headers=["Build", "Description"], tablefmt="html")

    # ---------------- ARTIFACT ---------------- #
    def get_build_archive_path(self):
        try:
            lastbuildurl = f"{self.jenkins_url}/job/{self.job_name}/{self.build_number}"
            response = requests.get(lastbuildurl, auth=(self.username, self.api_token))
            response.raise_for_status()

            tree = html.fromstring(response.content)
            zip_links = tree.xpath("//a[contains(@href, 'build-archive.zip')]/@href")

            if not zip_links:
                print("No build-archive.zip link found via XPath.")
                table_data = [["-", "No build-archive.zip link found"]]
                return tabulate(table_data, headers=["Build", "Artifact URL"], tablefmt="grid"), None

            zip_url = zip_links[0]
            if zip_url.startswith("/"):
                json_root = lastbuildurl.rstrip("/")
                zip_url = json_root + zip_url

            table_data = [[f"{self.job_name} #{self.build_number}", zip_url]]
            print("\nArtifact ZIP URL:")
            print(tabulate(table_data, headers=["Build", "Artifact URL"], tablefmt="grid"))
            return tabulate(table_data, headers=["Build", "Artifact URL"], tablefmt="html"), zip_url

        except Exception as e:
            print(f"Failed to get Artifact ZIP URL : {e}")
            return None, None

    def download_and_extract_build_archive_zip(self, artifact_url):
        try:
            os.makedirs(self.destination_folder, exist_ok=True)
            zip_file_name = f"{self.job_name}_{self.build_number}_build-archive.zip"
            destination_file = os.path.join(self.destination_folder, zip_file_name)

            with requests.Session() as session:
                with session.get(artifact_url, stream=True) as response:
                    response.raise_for_status()
                    with open(destination_file, "wb") as f:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
            return zip_file_name
        except Exception as e:
            print(f"Failed to download build-archive.zip file : {e}")
            return None

    # ---------------- REPORTS ---------------- #
    def summarize_cucumber_report(self, zip_path):
        def derive_stage_from_path(path_str: str) -> str:
            parts = Path(path_str).parts
            if "cucumber-reports" in parts:
                idx = parts.index("cucumber-reports")
                if idx + 1 < len(parts):
                    stage_folder = parts[idx + 1]
                    stage_clean = stage_folder.replace("_-_", " - ").replace("_", " ")
                    return f"E2E Cucumber Report - {stage_clean}"
            return "UNKNOWN"

        def occ_status(statuses):
            if any(s in ("failed", "undefined", "pending", "ambiguous") for s in statuses):
                return "failed"
            if any(s == "skipped" for s in statuses):
                return "skipped"
            if any(s == "passed" for s in statuses):
                return "passed"
            return "skipped"

        per_stage_sid_statuses = defaultdict(lambda: defaultdict(list))
        seen_stages = set()

        with zipfile.ZipFile(zip_path, "r") as z:
            json_files = [n for n in z.namelist()
                          if Path(n).name.startswith("cucumber_") and n.endswith(".json")]

            for jf in json_files:
                stage = derive_stage_from_path(jf)
                seen_stages.add(stage)
                try:
                    data = json.load(z.open(jf))
                except Exception:
                    continue
                if not data:
                    continue

                for feature in data:
                    feature_uri = feature.get("uri") or feature.get("id") or feature.get("name")
                    for el in feature.get("elements", []) or []:
                        if (el.get("type") or "").lower() == "background":
                            continue
                        sid = el.get("id") or f"{feature_uri}::{el.get('name')}::{el.get('line')}"
                        statuses = []

                        for s in el.get("steps", []) or []:
                            st = (s.get("result") or {}).get("status")
                            if st: statuses.append(st)

                        for hook_key in ("before", "after"):
                            for h in el.get(hook_key, []) or []:
                                st = (h.get("result") or {}).get("status")
                                if st: statuses.append(st)

                        if el.get("status"):
                            statuses.append(el["status"])

                        per_stage_sid_statuses[stage][sid].append(occ_status(statuses))

        cucumber_report_summaries = []
        #for stage, sid2list in per_stage_sid_statuses.items():
        for stage in seen_stages:  # 👈 include even empty stages
            sid2list = per_stage_sid_statuses.get(stage, {})
            final_counts = Counter()
            for sid, occs in sid2list.items():
                if "failed" in occs:
                    final = "failed"
                elif "skipped" in occs:
                    final = "skipped"
                elif "passed" in occs:
                    final = "passed"
                else:
                    final = "skipped"
                final_counts[final] += 1
            total = sum(final_counts.values())
            cucumber_report_summaries.append({
                "Stages": stage,
                "Scenarios": total,
                "Passed": final_counts.get("passed", 0),
                "Failed": final_counts.get("failed", 0),
                "Skipped": final_counts.get("skipped", 0)
            })

        return cucumber_report_summaries

    def summarize_junit_report(self, zip_path):
        results = []

        def extract_stage_name(path_parts):
            if 'build' in path_parts:
                build_index = path_parts.index('build')
                if build_index + 1 < len(path_parts):
                    return "E2E-JUnit-Report " + path_parts[build_index + 1].replace("_-_", " ")
            return "UNKNOWN"

        def extract_test_data(file_content):
            try:
                tree = html.fromstring(file_content)
                tds = tree.xpath('//div[@id="summary"]//div[@class="summaryGroup"]//table//tr[1]/td')

                if len(tds) >= 4:
                    def extract_int(text):
                        match = re.search(r"\d+", text)
                        return int(match.group()) if match else 0

                    tests = extract_int(tds[0].text_content())
                    failures = extract_int(tds[1].text_content())
                    skipped = extract_int(tds[2].text_content())
                    passed = tests - failures - skipped
                    return tests, passed, failures, skipped
            except Exception as e:
                print(f"Error parsing JUnit HTML: {e}")
            return 0, 0, 0, 0

        with zipfile.ZipFile(zip_path, 'r') as archive:
            for name in archive.namelist():
                if name.endswith("index.html") and "reports/tests/test" in name:
                    path_parts = name.split("/")
                    stage_name = extract_stage_name(path_parts)

                    with archive.open(name) as f:
                        content = f.read().decode("utf-8", errors="ignore")
                        tests, passed, failed, skipped = extract_test_data(content)

                    results.append({
                        'Stages': stage_name,
                        'Scenarios': tests,
                        'Passed': passed,
                        'Failed': failed,
                        'Skipped': skipped
                    })

        return results

    def consolidated_cycle_report(self, cucumber_report, junit_report):
        columns = ["Stages", "Scenarios", "Passed", "Failed", "Skipped"]
        df1 = pd.DataFrame(cucumber_report, columns=columns)
        df2 = pd.DataFrame(junit_report, columns=columns)
        stacked = pd.concat([df1, df2], ignore_index=True)
        print("\nConsolidated Cycle Report:")
        print(tabulate(stacked.values.tolist(), headers=stacked.columns.tolist(), tablefmt="grid"))
        #stacked.to_csv("Artifacts/" + build_archive_file.replace("_build-archive.zip", ".csv"), index=False)
        csv_filename = build_archive_file.replace("_build-archive.zip", ".csv")
        csv_path = os.path.join("Artifacts", csv_filename)
        stacked.to_csv(csv_path, index=False)
        return tabulate(stacked.values.tolist(), headers=stacked.columns.tolist(), tablefmt="html"), csv_path
        #return tabulate(stacked.values.tolist(), headers=stacked.columns.tolist(), tablefmt="html")

    def delete_build_archive_zip(self, build_archive_file):
        if os.path.exists(build_archive_file):
            os.remove(build_archive_file)
            print("\n" + "Build Archive File " + build_archive_file + " deleted successfully.")
        else:
            print("Build Archive File not found.")

    def create_baseline_folder_and_copy(self, job_type, csv_file_path):
        baseline_root = os.path.join("Artifacts", "Baseline")
        os.makedirs(baseline_root, exist_ok=True)

        baseline_dirs = [
            "SMART-2017", "WASHSALES-2017", "SMART HUB", "DMFARM-2017",
            "PERF-FARM-2017", "EARTH-2017",
            "R2.51-CST", "R2.53-CST", "R2.54-CST", "R2.55-CST"
        ]

        for folder in baseline_dirs:
            os.makedirs(os.path.join(baseline_root, folder), exist_ok=True)

        target_path = os.path.join(baseline_root, job_type, os.path.basename(csv_file_path))
        shutil.copy(csv_file_path, target_path)
        print(f"Baseline file copied to: {target_path}")

    def send_consolidated_status(
            self,
            build_description_report,
            latest_build_cycle_report,
            subject,
            sender_email,
            recipient_emails,
            smtp_server
    ):
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = sender_email
            msg["To"] = ", ".join(recipient_emails)
            msg["Subject"] = subject
            build_desc_html = build_description_report or "<p>No build description data</p>"
            artifact_html = self.artifact_table_data or "<p>No artifact data</p>"
            cycle_report_html = latest_build_cycle_report or "<p>No consolidated cycle data</p>"

            html_body = f"""
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
                     <p>Here below is the Jenkins API cycle report details for today's run.</p>
                        <h3 style="color:#155885;">Build Details</h3>
                        {build_desc_html}                        

                        <h3 style="color:#155885;">Consolidated Cycle Report</h3>
                        {cycle_report_html}
                        
                        <h3 style="color:#155885;">Artifact Details</h3>
                        {artifact_html}
                        
                        <div class="footer" style="margin-top:20px; padding:0;">
                            <p style="margin:0;">Thanks,</p>
                            <p style="margin:0; font-size:14px; font-weight:bold;">Regression Engineering Team</p>
                            <p style="margin:0; font-size:12px; color:#333;">da_regoperations@bny.com</p>
                            <p style="margin:0; font-size:10px; color:#333;">bny.com</p>
                        </div>
                    </body>
                    </html>
                    """

            msg.attach(MIMEText(html_body, "html"))
            server = smtplib.SMTP(smtp_server)
            server.sendmail(sender_email, recipient_emails, msg.as_string())
            server.quit()

            print(f"\nConsolidated report sent to {msg['To']} successfully.")
        except Exception as e:
            print(f"Failed to send email: {e}")

if __name__ == "__main__":
    job_name = sys.argv[1]
    build_number = sys.argv[3] if len(sys.argv) > 3 else None
    make_baseline = sys.argv[2] if len(sys.argv) > 2 else "N"

    jr = GetJenkinsReport(job_name, build_number=build_number)
    build_description_report = jr.get_build_details()
    jr.artifact_table_data, build_artifact_url = jr.get_build_archive_path()
    build_archive_file = jr.download_and_extract_build_archive_zip(build_artifact_url)
    archive_file = os.path.join(jr.destination_folder, build_archive_file)
    cucumber_report = jr.summarize_cucumber_report(archive_file)
    junit_report = jr.summarize_junit_report(archive_file)
    latest_build_cycle_report, csv_file_path = jr.consolidated_cycle_report(cucumber_report, junit_report)
    if make_baseline.upper() == "Y":
        jr.create_baseline_folder_and_copy(job_name, csv_file_path)
    jr.delete_build_archive_zip(archive_file)
    parser = ConfigParser()
    parser.read(os.path.join(os.path.abspath(os.path.dirname(__file__)), './../configurations', 'emailnotification.ini'))
    smtp_server = parser['Email_Alert']['smtpServer']
    email_sender = parser['Email_Alert']['Sender']
    email_subject = f"E2E Detail Report from [develop] branch - [{job_name}]"
    jr.send_consolidated_status(
        build_description_report,
        latest_build_cycle_report,
        subject=email_subject,
        sender_email=email_sender,
        recipient_emails=["marimuthu.mannathan@bny.com","samir.ransingh@bny.com"],
        smtp_server=smtp_server
    )