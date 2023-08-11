import requests, sys
import jiradetails

def post_to_slack(webhook_url, channel, message):
    payload = {
        "channel": channel,
        "text": message
    }

    try:
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
        print("Message posted successfully!")
    except requests.exceptions.RequestException as e:
        print(f"Error posting message: {e}")

build_details, build_version, build_number, build_status = jiradetails.get_jira_details()

l_msg = sys.argv[1]
message_to_post = 'Provide the message you want to post in the slack channel'

slack_webhook_url = "Webhook URL"
slack_channel = "#slack Channel"
print(message_to_post)
post_to_slack(slack_webhook_url, slack_channel, message_to_post)
