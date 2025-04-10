import time
import logging
from google.cloud import firestore
import requests
import json
from datetime import datetime
import functions_framework
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = firestore.Client()
CMC_API_KEY = "0678708a-7b8c-42c7-873b-432cc2360d03"
MAX_RETRIES = 3
RETRY_DELAY = 60

class EmailNotifier:
    def __init__(self):
        self.sendgrid_key = os.environ.get('SENDGRID_API_KEY')
        logger.info(f"Initializing SendGrid with API key present: {bool(self.sendgrid_key)}")
        self.sendgrid_client = SendGridAPIClient(self.sendgrid_key)
        self.from_email = 'pritamchavan1212@gmail.com'
        
    def send_email(self, to_email, subject, content):
        try:
            logger.info(f"Starting email send process to: {to_email}")
            message = Mail(
                from_email=self.from_email,
                to_emails=to_email,
                subject=subject,
                html_content=content
            )
            
            logger.info("Email object created, attempting to send...")
            response = self.sendgrid_client.send(message)
            logger.info(f"SendGrid Response Status Code: {response.status_code}")
            logger.info(f"SendGrid Response Headers: {dict(response.headers)}")
            
            if response.status_code == 202:
                logger.info("Email sent successfully")
                return True
            else:
                logger.error(f"SendGrid returned non-success status code: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to send email: {str(e)}")
            logger.error(f"Error type: {type(e).__name__}")
            return False

def get_current_prices(crypto_ids, retry_count=0):
    if retry_count >= MAX_RETRIES:
        raise Exception("Max retries reached for API calls")
        
    url = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"
    headers = {
        'X-CMC_PRO_API_KEY': CMC_API_KEY,
        'Accept': 'application/json'
    }
    params = {'id': ','.join(map(str, crypto_ids)), 'convert': 'USD'}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 429:
            logger.warning(f"Rate limit hit, waiting {RETRY_DELAY} seconds")
            time.sleep(RETRY_DELAY)
            return get_current_prices(crypto_ids, retry_count + 1)
            
        response.raise_for_status()
        return response.json()['data']
    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed: {str(e)}")
        if retry_count < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
            return get_current_prices(crypto_ids, retry_count + 1)
        raise

def update_alert_status(alert_id):
    try:
        alert_ref = db.collection('alerts').document(alert_id)
        alert_ref.update({
            'is_active': False,
            'triggered_at': firestore.SERVER_TIMESTAMP
        })
        logger.info(f"Successfully updated alert status: {alert_id}")
    except Exception as e:
        logger.error(f"Error updating alert status: {str(e)}")
        raise

def send_notification(alert, current_price):
    logger.info(f"Processing notification for alert: {alert['alert_id']}")
    try:
        # Get user email from Firestore with enhanced logging
        user_ref = db.collection('users').document(alert['user_id'])
        user_doc = user_ref.get()
        
        if not user_doc.exists:
            logger.error(f"User document not found for user_id: {alert['user_id']}")
            return False
            
        user_data = user_doc.to_dict()
        user_email = user_data.get('email')
        logger.info(f"Retrieved email for user {alert['user_id']}: {user_email}")
        
        if not user_email or user_email == 'no-email@example.com':
            logger.error(f"Invalid email found for user_id: {alert['user_id']}")
            return False
            
        email_content = f"""
        <h2>Cryptocurrency Price Alert</h2>
        <p>Your alert condition has been triggered:</p>
        <ul>
            <li>Cryptocurrency ID: {alert['cryptocurrency_id']}</li>
            <li>Target Price: ${alert['target_price']:,.2f}</li>
            <li>Current Price: ${current_price:,.2f}</li>
            <li>Condition: Price went {alert['condition_type']} target</li>
        </ul>
        <p>This alert is now deactivated. You can create a new alert through the API.</p>
        <br>
        <p>Best regards,<br>Your Crypto Alert System</p>
        """
        
        subject = f"Crypto Alert: Price {alert['condition_type'].title()} {alert['target_price']}"
        
        notifier = EmailNotifier()
        success = notifier.send_email(user_email, subject, email_content)
        logger.info(f"Email sending {'succeeded' if success else 'failed'} for alert {alert['alert_id']}")
        return success
        
    except Exception as e:
        logger.error(f"Error in send_notification: {str(e)}", exc_info=True)
        return False

def check_prices():
    try:
        alerts_ref = db.collection('alerts').where('is_active', '==', True)
        alerts = list(alerts_ref.stream())
        
        if not alerts:
            logger.info("No active alerts found")
            return 'No active alerts', 200
            
        crypto_ids = set()
        alert_data = []
        
        for alert in alerts:
            data = alert.to_dict()
            data['alert_id'] = alert.id
            alert_data.append(data)
            crypto_ids.add(str(data['cryptocurrency_id']))

        current_prices = get_current_prices(list(crypto_ids))
        
        for alert in alert_data:
            try:
                crypto_id = str(alert['cryptocurrency_id'])
                crypto_price = current_prices[crypto_id]['quote']['USD']['price']
                
                logger.info(f"Checking alert {alert['alert_id']}: Target {alert['target_price']} vs Current {crypto_price}")
                
                condition_met = (
                    (alert['condition_type'] == 'above' and float(crypto_price) >= float(alert['target_price'])) or
                    (alert['condition_type'] == 'below' and float(crypto_price) <= float(alert['target_price']))
                )
                
                if condition_met:
                    logger.info(f"Alert condition met for {alert['alert_id']}")
                    if send_notification(alert, crypto_price):
                        update_alert_status(alert['alert_id'])
                        logger.info(f"Alert {alert['alert_id']} processed successfully")
                    else:
                        logger.error(f"Failed to send notification for alert {alert['alert_id']}")
                
            except KeyError as e:
                logger.error(f"Error processing alert {alert['alert_id']}: Missing key {str(e)}")
                continue
            except Exception as e:
                logger.error(f"Unexpected error processing alert {alert['alert_id']}: {str(e)}")
                continue

    except Exception as e:
        logger.error(f"Error in check_prices: {str(e)}")
        raise

@functions_framework.http
def check_crypto_prices(request):
    try:
        result = check_prices()
        return result if result else ('Price check completed', 200)
    except Exception as e:
        logger.error(f"Price check failed: {str(e)}")
        return str(e), 500