from google.cloud import firestore
from datetime import datetime
import functions_framework
import base64
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Firestore client
db = firestore.Client()

def store_alert(alert_data):
    """Store alert in Firestore"""
    try:
        # First, ensure we have the email from the alert data
        if 'email' in alert_data:
            user_ref = db.collection('users').document(alert_data['user_id'])
            user_ref.set({
                'email': alert_data['email'],
                'updated_at': firestore.SERVER_TIMESTAMP,
                'created_at': firestore.SERVER_TIMESTAMP
            }, merge=True)
            logger.info(f"Updated user {alert_data['user_id']} with email {alert_data['email']}")

        # Create alert document
        alert_ref = db.collection('alerts').document(alert_data['alert_id'])
        alert_ref.set({
            'user_id': alert_data['user_id'],
            'cryptocurrency_id': alert_data['cryptocurrency_id'],
            'target_price': float(alert_data['target_price']),
            'condition_type': alert_data['condition_type'],
            'notification_type': alert_data['notification_type'],
            'current_price_at_creation': alert_data.get('current_price', 0.0),
            'is_active': True,
            'created_at': firestore.SERVER_TIMESTAMP
        })
        
        logger.info(f"Alert stored successfully: {alert_data['alert_id']}")
    except Exception as e:
        logger.error(f"Error storing alert: {str(e)}")
        raise

@functions_framework.cloud_event
def process_alert(cloud_event):
    """Cloud Function triggered by Pub/Sub event"""
    try:
        # Extract message data
        pubsub_message = base64.b64decode(cloud_event.data["message"]["data"]).decode()
        logger.info(f"Raw pubsub message: {pubsub_message}")
        alert_data = json.loads(pubsub_message)
        logger.info(f"Processed alert data: {alert_data}")
        
        logger.info(f"Received alert: {alert_data}")
        
        # Store in Firestore
        store_alert(alert_data)
        
        return 'Alert processed successfully', 200
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        return str(e), 500