from email.message import EmailMessage
import smtplib, os
from configparser import ConfigParser


def send_email(subject, message):
    try:
        parser = ConfigParser()
        parser.read(os.path.join(os.path.abspath(os.path.dirname(__file__)),'configurations','emailnotification.ini'))
        FromID = parser['NOTIFICATION']['From']
        ToID = parser['NOTIFICATION']['To']
        cc = parser['NOTIFICATION']['CC']
        smtpServer = parser['NOTIFICATION']['SMTPServer']
        footer = parser['NOTIFICATION']['email_footer']
        msg = EmailMessage()
        msg["From"] = FromID
        msg["Subject"] = subject
        msg["To"] = ToID
        msg['Cc'] = cc
        msg.set_content(f"""{message}\n \n Thanks, \n {footer}""")
        s = smtplib.SMTP(smtpServer)
        s.send_message(msg)
        statusmessage = "An email has been sent successfully."
    except Exception as e:
        template = "An exception of type {0} occurred. Arguments:\n{1!r}"
        message = template.format(type(e).__name__, e.args)
        return 'ERROR:' + message
    return 'Email sent.'

# enter subject details and message to be sent
send_email(subject, message)