from jira import JIRA
import re, os, sys
from configparser import ConfigParser
from dotenv import load_dotenv
load_dotenv()

def extract_eagle_build(text):
    match = re.search(r"(Eagle_V[^\s\\/]+)", text)
    return match.group(1) if match else None

def get_jira_details(build_id):
    parser = ConfigParser()
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    config_path = os.path.join(base_dir, "configurations", "jiradetails.ini")
    parser.read(config_path)

    jira = JIRA(
        options={'server': parser['GET_JIRA']['jira_server']},
        basic_auth=(parser['GET_JIRA']['jira_uid'], parser['GET_JIRA']['jira_token'])
    )

    issue = jira.issue(build_id)
    build_status = str(issue.fields.status)
    summary_build_version = "Eagle_V" + issue.fields.summary.replace(" ", "")
    comments = jira.comments(build_id)
    for comment in reversed(comments):
        text = comment.body
        if "Build:" in text:
            build_version = extract_eagle_build(text) or summary_build_version
            build_number = next(
                (line for line in text.splitlines() if "Build" in line),
                "Build Number Not Found"
            )
            return text.replace("*", ""), build_version, build_number, build_status
    return None, summary_build_version, None, build_status

#build_details, build_version, build_number, build_status = get_jira_details()