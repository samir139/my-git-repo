import os
import re
import sys
import json
import requests
import pandas as pd
from datetime import datetime
from datetime import timedelta
from lxml import html
from jira import config
from tabulate import tabulate
from bs4 import BeautifulSoup
import zipfile
from pathlib import Path
from collections import defaultdict, Counter
from configparser import ConfigParser
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

destination_folder = r"./Artifacts"
jenkins_file = './../configurations/jenkins_params.json'

def read_json(job_name):
    with open(jenkins_file, "r") as f:
        config = json.load(f)

    common = config.get("COMMON-INFO", {})
    username = common.get("username")
    api_token = common.get("api_token")
    job_config = config.get(job_name)
    if not job_config:
        print("No configuration found for job: ", job_name)
        sys.exit(1)

    jenkins_url = job_config.get("jenkins_url")
    params = {k: v for k, v in job_config.items() if k != "jenkins_url"}
    return jenkins_url, username, api_token

def get_build_details(jenkins_url, job_name):
    built_url = jenkins_url + '/job/' + job_name + '/lastBuild/api/json'
    response = requests.get(built_url, auth=(username, api_token))
    response.raise_for_status()
    data = response.json()

    # Extract fields
    build_url = data.get("url")
    build_number = data.get("number")
    project = data.get("fullDisplayName", "")
    timestamp = data.get("timestamp")
    date_of_build = datetime.fromtimestamp(timestamp / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
    duration_ms = data.get("duration")
    if duration_ms:
        total_seconds = duration_ms // 1000
        duration = str(timedelta(seconds=total_seconds))
    else:
        duration = "N/A"

    # Extract cause
    build_cause = "N/A"
    for action in data.get("actions", []):
        if "causes" in action:
            cause = action["causes"][0]
            build_cause = cause.get("shortDescription", "N/A")
            break

    desc_html = data.get("description", "")
    if desc_html:
        soup = BeautifulSoup(desc_html, "html.parser")
        build_description = soup.get_text(strip=True)
    else:
        build_description = "N/A"
    built_on = data.get("builtOn", "N/A")

    # Prepare table data
    table_data = [
        ["Build URL", build_url],
        ["Build Number", build_number],
        ["Project", project.split("»")[-1].split("#")[0].strip()],
        ["Date of build", date_of_build],
        ["Build duration", duration],
        ["Build cause", build_cause],
        ["Build description", build_description],
        ["Built on", built_on]
    ]

    # Print as table
    print(tabulate(table_data, headers=["Build", "Description"], tablefmt="grid"))
    return tabulate(table_data, headers=["Build", "Description"], tablefmt="grid"), build_number

def get_build_archive_path(jenkins_url, job_name, build_number):
    try:
        lastbuildurl = jenkins_url + '/job/' + job_name + '/' + str(build_number)
        response = requests.get(lastbuildurl, auth=(username, api_token))
        response.raise_for_status()

        # Parse HTML
        tree = html.fromstring(response.content)
        zip_links = tree.xpath("//a[contains(@href, 'build-archive.zip')]/@href")

        if not zip_links:
            print("No build-archive.zip link found via XPath.")
        else:
            zip_url = zip_links[0]
            if zip_url.startswith("/"):
                json_root = lastbuildurl.rstrip("/")
                zip_url = json_root + zip_url
            print("\n" + "Artifact ZIP URL:", zip_url)
            return zip_url
    except Exception as e:
        print(f"Failed to get Artifact ZIP URL : {e}")

def download_and_extract_build_archive_zip(artifact_url, job_name, build_number):
    try:
        os.makedirs(destination_folder, exist_ok=True)
        zip_file_name = job_name + '_' + str(build_number) + '_' + 'build-archive.zip'
        destination_file = os.path.join(destination_folder, zip_file_name)

        with requests.Session() as session:
            with session.get(artifact_url, stream=True) as response:
                response.raise_for_status()
                with open(destination_file, "wb") as f:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
        print("\n" + f"Downloaded build-archive.zip to : {destination_file}")

        return zip_file_name
    except Exception as e:
        print(f"Failed to download or extract build-archive.zip file : {e}")

def summarize_cucumber_report(zip_path):
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

    per_stage_sid_statuses = defaultdict(lambda: defaultdict(list))  # stage -> sid -> [occ_status]

    with zipfile.ZipFile(zip_path, "r") as z:
        json_files = [n for n in z.namelist()
                      if Path(n).name.startswith("cucumber_") and n.endswith(".json")]

        for jf in json_files:
            stage = derive_stage_from_path(jf)
            try:
                data = json.load(z.open(jf))
            except Exception:
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
    for stage, sid2list in per_stage_sid_statuses.items():
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
            "Stage": stage,
            "Scenarios": total,
            "Passed": final_counts.get("passed", 0),
            "Failed": final_counts.get("failed", 0),
            "Skipped": final_counts.get("skipped", 0)
        })

    print("\nCucumber Report:")
    print(tabulate(
        [[r["Stage"], r["Scenarios"], r["Passed"], r["Failed"], r["Skipped"]] for r in cucumber_report_summaries],
        headers=["Stage", "Scenarios", "Passed", "Failed", "Skipped"],
        tablefmt="grid"
    ))

    return cucumber_report_summaries

def summarize_junit_report(zip_path):
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
                    'Stage': stage_name,
                    'Scenarios': tests,
                    'Passed': passed,
                    'Failed': failed,
                    'Skipped': skipped
                })

    # Pretty print
    table = [[r["Stage"], r["Scenarios"], r["Passed"], r["Failed"], r["Skipped"]] for r in results]
    print("\nJunit Report:")
    print(tabulate(table, headers=["Stage", "Scenarios", "Passed", "Failed", "Skipped"], tablefmt="grid"))

    return results

def consolidated_cycle_report(cucumber_report, junit_report):
    # First table data
    table1 = cucumber_report

    # Second table data
    table2 = junit_report

    # Convert both to pandas DataFrame
    columns = ["Stage", "Scenarios", "Passed", "Failed", "Skipped"]

    df1 = pd.DataFrame(table1, columns=columns)
    df2 = pd.DataFrame(table2, columns=columns)

    # ---- Option 1: Just stack (append) ----
    stacked = pd.concat([df1, df2], ignore_index=True)
    print("\nConsolidate Cycle Report:")
    # print(tabulate(stacked, headers="keys", tablefmt="grid"))
    # return tabulate(stacked, headers="keys", tablefmt="grid")
    print(tabulate(stacked.values.tolist(), headers=stacked.columns.tolist(), tablefmt="grid"))
    return tabulate(stacked.values.tolist(), headers=stacked.columns.tolist(), tablefmt="grid")

def send_consolidated_status(table_data, build_artifact_url, report_text, subject, sender_email, recipient_emails, smtp_server):
    try:
        msg = MIMEMultipart()
        msg["From"] = sender_email
        msg["To"] = ", ".join(recipient_emails)
        msg["Subject"] = subject
        html_body = (f"<pre style='font-family: monospace;'><h3>Build Description</h3></pre>"
                     f"<pre style='font-family: monospace;'>{table_data}</pre>"
                     f"<pre style='font-family: monospace;'><h3>Build Artifact</h3></pre>"
                     f"<pre style='font-family: monospace;'>{build_artifact_url}</pre>"
                     f"<pre style='font-family: monospace;'><h3>Consolidated Cycle Report</h3></pre>"
                     f"<pre style='font-family: monospace;'>{report_text}</pre>")
        msg.attach(MIMEText(html_body, "html"))
        server = smtplib.SMTP(smtp_server)
        server.sendmail(sender_email, recipient_emails, msg.as_string())
        server.quit()

        print("Consolidated job status sent to " + msg["To"] + " successfully.")

    except Exception as e:
        print(f"Failed to send email: {e}")

# Step 1 - Read Json file
job_name = sys.argv[1]
jenkins_url, username, api_token = read_json(job_name)

# Step 2 - Get the consolidated Details of the build
table_data, build_number = get_build_details(jenkins_url, job_name)

# Step 2 - get build-archive.zip artifact path
build_artifact_url = get_build_archive_path(jenkins_url, job_name, build_number)

# Step 3 - Get Build-archive.zip details
build_archive_file = download_and_extract_build_archive_zip(build_artifact_url, job_name, build_number)

archive_file = destination_folder + '/' + build_archive_file
# Step 4 - Generate Cucumber Report
#summarize_cucumber_report(archive_file)

# Step 5 - Generate JUnit Report
#summarize_junit_report(archive_file)

# Step 6 - Generate Consolidated Cycle Report
#consolidated_cycle_report(summarize_cucumber_report(archive_file),
#                          summarize_junit_report(archive_file))

# Last Step send the report via email
parser = ConfigParser()
parser.read(os.path.join(os.path.abspath(os.path.dirname(__file__)),'./../configurations','emailnotification.ini'))
smtp_server = parser['Email_Alert']['smtpServer']
email_sender = parser['Email_Alert']['Sender']
email_receivers = parser['Email_Alert']['recipients']
email_subject = 'E2E Detail Report from [develop] branch - [' + job_name + ']'
send_consolidated_status(table_data, build_artifact_url,
                         report_text=consolidated_cycle_report(summarize_cucumber_report(archive_file),
                                                               summarize_junit_report(archive_file)),
                        subject=email_subject,
                        sender_email=email_sender,
                        recipient_emails=["samir.ransingh@bny.com"],
                        smtp_server=smtp_server)

