# Define the AlertSystem class

import os
import smtplib

""" This class handles the sending of alert messages, such as SMS or emails."""
#


class AlertSystem:
    _instance = None
    _is_loaded = False

    def __new__(cls, logmanager):
        if cls._instance is None:
            cls._instance = super(AlertSystem, cls).__new__(cls)
        return cls._instance

    def __init__(self, logmanager):
        self._smtp_server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        self._phone = os.getenv('PHONE')
        self._email = os.getenv('EMAIL')
        self._e_mailpass = os.getenv('E_MAILPASS')
        self._my_email = os.getenv('MY_EMAIL')
        self.log_manager = logmanager

    @property
    def smtp_server(self):
        return self._smtp_server

    @property
    def phone(self):
        return self._phone

    @property
    def email(self):
        return self._email

    @property
    def e_mailpass(self):
        return self._e_mailpass

    @property
    def my_email(self):
        return self._my_email

    def callhome(self, subject, message):
        try:
            #  logger.info('Sending SMS alert')
            to = f'{self.phone}@txt.att.net'  # Format the phone number as an Email-to-SMS gateway address
            email_text = f'Subject: {subject}\n\n{message}'
            self.smtp_server.login(self.email, self.e_mailpass)
            self.smtp_server.sendmail(self.my_email, to, email_text)
            self.smtp_server.quit()
        except Exception as e:
            print(f'Error sending SMS alert: {e}')
            self.log_manager.sighook_logger.error(f'Error sending SMS alert: {e}')
