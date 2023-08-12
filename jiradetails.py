from jira import JIRA
import re, os
from configparser import ConfigParser
from dotenv import load_dotenv
load_dotenv()


def get_jira_details():
    build_id = os.environ.get('build_id')
    parser = ConfigParser()
    parser.read(os.path.join(os.path.abspath(os.path.dirname(__file__)),'configurations','jiradetails.ini'))
    jira_server = parser['GET_JIRA']['jira_server']
    jira_uid = parser['GET_JIRA']['jira_uid']
    jira_token = parser['GET_JIRA']['jira_token']
    jiraOptions = {'server': jira_server}
    jira = JIRA(options=jiraOptions, basic_auth=(jira_uid, jira_token))
    build_version = str('Eagle_V' + jira.issue(build_id).fields.summary.replace(" ", ""))
    build_status = str(jira.issue(build_id).fields.status)
    a=int(0)
    for i in jira.comments(build_id):
         if int(i.id)>a:
             a = int(i.id)

    build_details = jira.comment(build_id,a).body
    count = len(jira.comments(build_id))
    for i in range(count,0,-1):
        build_details = jira.comment(build_id,jira.comments(build_id)[i-1]).body
        if re.findall("Build:",build_details,re.MULTILINE):
            for line in build_details.splitlines():
               if "Build" in line:
                   build_number = line
                   break
            return build_details.replace('*',''), build_version, build_number
            break

get_jira_details()
